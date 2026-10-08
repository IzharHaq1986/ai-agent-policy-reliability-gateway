"""Opt-in acceptance checks against isolated PostgreSQL storage."""

import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from gateway.audit import build_audit_event
from gateway.postgresql_audit_receiver import (
    AuditConflictError,
    create_postgresql_audit_receiver,
)


@pytest.fixture
def database():
    writer = os.environ.get("GATEWAY_TEST_WRITER_DSN")
    reader = os.environ.get("GATEWAY_TEST_READER_DSN")
    if not writer or not reader:
        pytest.skip("Explicit isolated PostgreSQL test connections required")

    for conninfo, expected_role in (
        (writer, "gateway_audit_writer"),
        (reader, "gateway_audit_reader"),
    ):
        with psycopg.connect(conninfo, connect_timeout=5) as connection:
            identity = connection.execute(
                "SELECT current_database(), current_user"
            ).fetchone()
            assert identity is not None
            assert identity[0] == "gateway_audit_test"
            assert identity[1] == expected_role
    return writer, reader


def event():
    return build_audit_event(
        {
            "decision": "ALLOW",
            "reason_code": "POLICY_ALLOW",
            "policy_version": "policy-core-v1",
            "execution_status": "not_executed",
        },
        principal_id="reliability-reader",
        event_id=str(uuid4()),
        occurred_at="2026-10-08T09:00:00Z",
    )


def stored_events(reader, event_id):
    with psycopg.connect(reader, connect_timeout=5, row_factory=dict_row) as connection:
        return connection.execute(
            """
            SELECT schema_version, event_type, event_id, occurred_at,
                   principal_id, decision, reason_code, policy_version,
                   execution_status
            FROM gateway_audit.policy_decisions WHERE event_id = %s
            """,
            (event_id,),
        ).fetchall()


def test_acknowledged_event_is_visible_to_separate_reader(database):
    writer, reader = database
    supplied = event()
    original = deepcopy(supplied)

    assert create_postgresql_audit_receiver(writer)(supplied) is None
    assert stored_events(reader, supplied["event_id"]) == [original]
    assert supplied == original


def test_identical_duplicate_preserves_one_row(database):
    writer, reader = database
    supplied = event()
    receive = create_postgresql_audit_receiver(writer)

    receive(supplied)
    assert receive(deepcopy(supplied)) is None
    assert stored_events(reader, supplied["event_id"]) == [supplied]


def test_conflicting_duplicate_preserves_original(database):
    writer, reader = database
    supplied = event()
    receive = create_postgresql_audit_receiver(writer)
    receive(supplied)
    conflicting = {**supplied, "occurred_at": "2026-10-08T09:00:01Z"}

    with pytest.raises(AuditConflictError):
        receive(conflicting)
    assert stored_events(reader, supplied["event_id"]) == [supplied]


@pytest.mark.parametrize("conflicting", [False, True])
def test_concurrent_delivery_is_consistent(database, conflicting):
    writer, reader = database
    first = event()
    second = deepcopy(first)
    if conflicting:
        second["occurred_at"] = "2026-10-08T09:00:01Z"
    barrier = Barrier(2)
    receive = create_postgresql_audit_receiver(writer)

    def deliver(supplied):
        barrier.wait(timeout=5)
        try:
            receive(supplied)
        except AuditConflictError:
            return "conflict"
        return "success"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(deliver, supplied) for supplied in (first, second)]
        outcomes = [future.result(timeout=20) for future in futures]

    rows = stored_events(reader, first["event_id"])
    assert len(rows) == 1
    if conflicting:
        assert sorted(outcomes) == ["conflict", "success"]
        assert rows[0] in (first, second)
    else:
        assert outcomes == ["success", "success"]
        assert rows == [first]


def test_contended_insert_hits_lock_timeout(database):
    writer, reader = database
    supplied = event()
    values = tuple(
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
    with psycopg.connect(writer, connect_timeout=5) as blocker:
        blocker.execute(
            """
            INSERT INTO gateway_audit.policy_decisions (
                schema_version, event_type, event_id, occurred_at,
                principal_id, decision, reason_code, policy_version,
                execution_status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            values,
        )
        try:
            with pytest.raises(psycopg.errors.LockNotAvailable) as failure:
                create_postgresql_audit_receiver(writer)(supplied)
            assert failure.value.sqlstate == "55P03"
        finally:
            blocker.rollback()

    assert stored_events(reader, supplied["event_id"]) == []


def test_slow_statement_hits_statement_timeout_and_closes(database, monkeypatch):
    from gateway import postgresql_audit_receiver as receiver_module

    writer, reader = database
    supplied = event()
    real_connect = psycopg.connect
    connections = []

    class SlowStatementConnection:
        def __init__(self, connection):
            self.connection = connection

        @property
        def isolation_level(self):
            return self.connection.isolation_level

        @isolation_level.setter
        def isolation_level(self, value):
            self.connection.isolation_level = value

        def execute(self, statement, params=None):
            result = self.connection.execute(statement, params)
            if statement == "SET LOCAL statement_timeout = '5s'":
                # Inject a real slow database statement after timeout setup.
                self.connection.execute("SELECT pg_sleep(6)")
            return result

        def commit(self):
            self.connection.commit()

        def rollback(self):
            self.connection.rollback()

        def close(self):
            self.connection.close()

    def connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return SlowStatementConnection(connection)

    with monkeypatch.context() as patch:
        patch.setattr(receiver_module.psycopg, "connect", connect)
        with pytest.raises(psycopg.errors.QueryCanceled) as failure:
            create_postgresql_audit_receiver(writer)(supplied)
        assert failure.value.sqlstate == "57014"

    assert len(connections) == 1
    assert connections[0].closed
    assert stored_events(reader, supplied["event_id"]) == []


@pytest.fixture
def api_context(monkeypatch):
    import secrets

    from gateway import api

    token = secrets.token_urlsafe(32)
    identifier = uuid4()
    monkeypatch.setenv("GATEWAY_API_TOKEN", token)
    # Supply a known valid server-generated ID to locate only this test's row.
    monkeypatch.setattr(api, "uuid4", lambda: identifier)
    return token, str(identifier)


def api_payload():
    return {
        "tool": "fixture_store",
        "operation": "READ_ITEM",
        "arguments": {"item_id": 1},
    }


@pytest.mark.parametrize(
    ("change", "status", "decision", "reason"),
    [
        ({}, 200, "ALLOW", "POLICY_ALLOW"),
        ({"tool": "other_tool"}, 200, "DENY", "UNKNOWN_TOOL"),
        ({"operation": "WRITE_ITEM"}, 200, "DENY", "UNKNOWN_OPERATION"),
        ({"arguments": {"item_id": 0}}, 422, "DENY", "INVALID_REQUEST"),
    ],
)
def test_api_commits_matching_audit_event(
    database, api_context, change, status, decision, reason
):
    import json

    from fastapi.testclient import TestClient

    from gateway.api import create_app
    from gateway.audit import validate_audit_event

    writer, reader = database
    token, identifier = api_context
    supplied = api_payload()
    supplied.update(change)
    app = create_app(audit_sink=create_postgresql_audit_receiver(writer))

    with TestClient(app) as client:
        response = client.post(
            "/v1/policy/evaluate",
            headers={"Authorization": f"Bearer {token}"},
            json=supplied,
        )
        rows = stored_events(reader, identifier)

    assert len(rows) == 1
    audit = rows[0]
    assert validate_audit_event(audit) == audit
    assert audit["event_id"] == identifier
    assert audit["principal_id"] == "reliability-reader"
    assert audit["decision"] == decision
    assert audit["reason_code"] == reason
    assert audit["execution_status"] == "not_executed"
    result = {
        key: audit[key]
        for key in ("decision", "reason_code", "policy_version", "execution_status")
    }
    assert response.status_code == status
    assert response.json() == (
        result if status == 200 else {"error": {"code": "INVALID_REQUEST"}}
    )
    serialized = json.dumps(audit)
    assert token not in serialized
    assert set(audit) == {
        "schema_version",
        "event_type",
        "event_id",
        "occurred_at",
        "principal_id",
        "decision",
        "reason_code",
        "policy_version",
        "execution_status",
    }


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        ("missing_auth", 401, "UNAUTHENTICATED"),
        ("invalid_auth", 401, "UNAUTHENTICATED"),
        ("media", 415, "UNSUPPORTED_MEDIA_TYPE"),
        ("size", 413, "BODY_TOO_LARGE"),
        ("json", 422, "INVALID_REQUEST"),
    ],
)
def test_api_pre_policy_failure_persists_no_event(
    database, api_context, failure, status, code
):
    from fastapi.testclient import TestClient

    from gateway.api import create_app

    writer, reader = database
    token, identifier = api_context
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = b"{}"
    if failure == "missing_auth":
        del headers["Authorization"]
    elif failure == "invalid_auth":
        headers["Authorization"] = "Bearer invalid"
    elif failure == "media":
        headers["Content-Type"] = "text/plain"
    elif failure == "size":
        body = b" " * 4097
    elif failure == "json":
        body = b"{"

    app = create_app(audit_sink=create_postgresql_audit_receiver(writer))
    with TestClient(app) as client:
        response = client.post("/v1/policy/evaluate", headers=headers, content=body)

    assert response.status_code == status
    assert response.json() == {"error": {"code": code}}
    assert stored_events(reader, identifier) == []


@pytest.mark.parametrize(
    "change",
    [{}, {"tool": "other_tool"}, {"arguments": {"item_id": 0}}],
)
def test_api_real_database_permission_failure_returns_generic_500(
    database, api_context, change
):
    from fastapi.testclient import TestClient

    from gateway.api import create_app

    _, reader = database
    token, identifier = api_context
    supplied = api_payload()
    supplied.update(change)
    # The real reader role can connect and select but cannot insert.
    app = create_app(audit_sink=create_postgresql_audit_receiver(reader))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/policy/evaluate",
            headers={"Authorization": f"Bearer {token}"},
            json=supplied,
        )

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "INTERNAL_ERROR"}}
    assert token not in response.text
    assert reader not in response.text
    assert stored_events(reader, identifier) == []
