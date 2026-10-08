"""Unit checks for PostgreSQL audit delivery and failure boundaries."""

from copy import deepcopy

import psycopg
import pytest

from gateway import postgresql_audit_receiver as receiver_module
from gateway.audit import AuditValidationError, build_audit_event

CONNINFO = "dbname=synthetic_audit user=synthetic_writer"


def event():
    return build_audit_event(
        {
            "decision": "ALLOW",
            "reason_code": "POLICY_ALLOW",
            "policy_version": "policy-core-v1",
            "execution_status": "not_executed",
        },
        principal_id="reliability-reader",
        event_id="550e8400-e29b-41d4-a716-446655440000",
        occurred_at="2026-10-08T09:00:00Z",
    )


def row(supplied):
    return tuple(
        supplied[key]
        for key in (
            "schema_version",
            "event_type",
            "event_id",
            "occurred_at",
            "principal_id",
            "decision",
            "reason_code",
            "policy_version",
            "execution_status",
        )
    )


class Connection:
    def __init__(self, *, duplicate=False, stored=None, failure=None):
        self.duplicate = duplicate
        self.stored = stored
        self.failure = failure
        self.operations = []
        self.isolation_level = None
        self.result = None

    def execute(self, sql, params=None):
        self.operations.append(("execute", sql, params))
        if self.failure == "execute":
            raise OSError("synthetic delivery failure")
        if sql == receiver_module._INSERT:
            self.result = None if self.duplicate else (event()["event_id"],)
        elif sql == receiver_module._SELECT:
            self.result = self.stored
        return self

    def fetchone(self):
        return self.result

    def commit(self):
        self.operations.append(("commit",))
        if self.failure in {"commit", "cleanup"}:
            raise OSError("synthetic delivery failure")

    def rollback(self):
        self.operations.append(("rollback",))
        if self.failure == "cleanup":
            raise RuntimeError("synthetic rollback failure")

    def close(self):
        self.operations.append(("close",))
        if self.failure in {"close", "cleanup"}:
            raise RuntimeError("synthetic close failure")


def install_connection(monkeypatch, connection):
    calls = []

    def connect(conninfo, **kwargs):
        calls.append((conninfo, kwargs))
        return connection

    monkeypatch.setattr(receiver_module.psycopg, "connect", connect)
    return calls


@pytest.mark.parametrize("configuration", [None, 1, True, "", "  "])
def test_invalid_configuration(configuration):
    with pytest.raises(ValueError, match="Invalid audit database configuration"):
        receiver_module.create_postgresql_audit_receiver(configuration)


@pytest.mark.parametrize("field", list(event()))
def test_missing_field_opens_no_connection(monkeypatch, field):
    connection = Connection()
    calls = install_connection(monkeypatch, connection)
    supplied = event()
    del supplied[field]
    original = deepcopy(supplied)
    with pytest.raises(AuditValidationError):
        receiver_module.create_postgresql_audit_receiver(CONNINFO)(supplied)
    assert calls == []
    assert connection.operations == []
    assert supplied == original


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("token", "synthetic-private"),
        ("schema_version", True),
        ("execution_status", "executed"),
    ],
)
def test_invalid_event_opens_no_connection(monkeypatch, field, value):
    calls = install_connection(monkeypatch, Connection())
    supplied = event()
    supplied[field] = value
    with pytest.raises(AuditValidationError):
        receiver_module.create_postgresql_audit_receiver(CONNINFO)(supplied)
    assert calls == []


@pytest.mark.parametrize("duplicate", [False, True])
def test_success_commits_then_closes(monkeypatch, duplicate):
    supplied = event()
    original = deepcopy(supplied)
    connection = Connection(duplicate=duplicate, stored=row(supplied))
    calls = install_connection(monkeypatch, connection)

    assert receiver_module.create_postgresql_audit_receiver(CONNINFO)(supplied) is None
    assert calls == [(CONNINFO, {"connect_timeout": 5})]
    assert connection.isolation_level == psycopg.IsolationLevel.READ_COMMITTED
    assert connection.operations[:3] == [
        ("execute", "SET LOCAL synchronous_commit = on", None),
        ("execute", "SET LOCAL lock_timeout = '2s'", None),
        ("execute", "SET LOCAL statement_timeout = '5s'", None),
    ]
    assert connection.operations[3] == (
        "execute",
        receiver_module._INSERT,
        row(supplied),
    )
    if duplicate:
        assert connection.operations[4] == (
            "execute",
            receiver_module._SELECT,
            (supplied["event_id"],),
        )
    assert connection.operations[-2:] == [("commit",), ("close",)]
    assert supplied == original


@pytest.mark.parametrize("stored", [None, ("different",)])
def test_conflict_rolls_back_without_commit(monkeypatch, stored):
    connection = Connection(duplicate=True, stored=stored)
    install_connection(monkeypatch, connection)
    with pytest.raises(receiver_module.AuditConflictError):
        receiver_module.create_postgresql_audit_receiver(CONNINFO)(event())
    assert ("commit",) not in connection.operations
    assert connection.operations[-2:] == [("rollback",), ("close",)]


@pytest.mark.parametrize("failure", ["execute", "commit", "cleanup"])
def test_failure_preserves_original_exception_and_does_not_retry(monkeypatch, failure):
    connection = Connection(failure=failure)
    calls = install_connection(monkeypatch, connection)
    with pytest.raises(OSError, match="synthetic delivery failure"):
        receiver_module.create_postgresql_audit_receiver(CONNINFO)(event())
    assert len(calls) == 1
    assert connection.operations[-2:] == [("rollback",), ("close",)]


def test_close_failure_after_commit_propagates(monkeypatch):
    connection = Connection(failure="close")
    install_connection(monkeypatch, connection)
    with pytest.raises(RuntimeError, match="synthetic close failure"):
        receiver_module.create_postgresql_audit_receiver(CONNINFO)(event())
    assert connection.operations[-2:] == [("commit",), ("close",)]


def test_connection_failure_propagates_without_retry(monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        raise psycopg.OperationalError("synthetic connection failure")

    monkeypatch.setattr(receiver_module.psycopg, "connect", fail)
    with pytest.raises(psycopg.OperationalError):
        receiver_module.create_postgresql_audit_receiver(CONNINFO)(event())
    assert len(calls) == 1
