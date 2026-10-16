"""Unit checks for execution-event relationships and delivery failures."""

from copy import deepcopy  # Verify that caller data remains unchanged.

import psycopg  # Check driver settings and database error propagation.
import pytest  # Supply cases and expected-failure assertions.

from gateway import postgresql_execution_receiver as module  # Exercise real code.
from gateway.execution_audit import (  # Reuse the existing event contract.
    ExecutionAuditValidationError,
    build_execution_event,
)

CONNINFO = "dbname=synthetic_audit user=synthetic_writer"  # Use non-secret setup.
EVENT_KEYS = (  # Define database parameter order independently of the receiver.
    "schema_version",
    "event_type",
    "event_id",
    "execution_id",
    "policy_event_id",
    "occurred_at",
    "principal_id",
    "tool",
    "operation",
    "status",
    "reason_code",
)


def event(status="started"):  # Build independent valid inputs for each case.
    return build_execution_event(  # Apply the existing event validation rules.
        event_id="110e8400-e29b-41d4-a716-446655440000",
        execution_id="220e8400-e29b-41d4-a716-446655440000",
        policy_event_id="330e8400-e29b-41d4-a716-446655440000",
        occurred_at="2026-10-16T10:00:00Z",
        principal_id="reliability-reader",
        tool="fixture_store",
        operation="READ_ITEM",
        status=status,
        reason_code={  # Match each supported status to a valid reason.
            "started": "SIMULATION_STARTED",
            "completed": "SIMULATION_COMPLETED",
            "failed": "SIMULATOR_ERROR",
        }[status],
    )


def start_record(candidate):  # Model the independently stored start row.
    return (  # Match the documented SELECT output order.
        candidate["policy_event_id"],
        candidate["principal_id"],
        candidate["tool"],
        candidate["operation"],
        candidate["occurred_at"],
        "started",
    )


class Connection:  # Record delivery operations without opening PostgreSQL.
    def __init__(self, *, records=(), failure_at=None, cleanup_fails=False):
        self.context = (  # Model a matching ALLOW decision and request binding.
            "ALLOW",
            "POLICY_ALLOW",
            "reliability-reader",
            "reliability-reader",
            "fixture_store",
            "READ_ITEM",
        )
        self.records = list(records)  # Supply existing attempt rows.
        self.failure_at = failure_at  # Select a database failure boundary.
        self.cleanup_fails = cleanup_fails  # Optionally break both cleanup calls.
        self.failure = OSError("synthetic delivery failure")  # Preserve identity.
        self.operations = []  # Capture statements, parameters, and cleanup.
        self.executions = 0  # Count statements for fault injection.
        self.isolation_level = None  # Capture the requested transaction setting.

    def execute(self, statement, params=None):  # Record one actual SQL call.
        self.executions += 1  # Identify the selected statement boundary.
        self.operations.append(("execute", statement, params))
        if self.failure_at == self.executions:  # Inject the requested failure.
            raise self.failure
        return self  # Support execute(...).fetchone() and fetchall().

    def fetchone(self):  # Supply the saved policy and binding context.
        return self.context

    def fetchall(self):  # Supply the saved execution records.
        return list(self.records)

    def commit(self):  # Model commit acknowledgement.
        self.operations.append(("commit",))
        if self.failure_at == "commit":  # Leave commit outcome unspecified.
            raise self.failure

    def rollback(self):  # Observe attempted failure cleanup.
        self.operations.append(("rollback",))
        if self.cleanup_fails:  # Ensure cleanup cannot hide the original error.
            raise RuntimeError("rollback failed")

    def close(self):  # Observe connection release.
        self.operations.append(("close",))
        if self.cleanup_fails or self.failure_at == "close":
            raise RuntimeError("close failed")


def install(monkeypatch, connection):  # Replace only database connection creation.
    calls = []  # Track connection attempts and supplied configuration.

    def connect(conninfo, **kwargs):  # Supply the controlled connection.
        calls.append((conninfo, kwargs))
        return connection

    monkeypatch.setattr(module.psycopg, "connect", connect)
    return calls  # Let each test check that no retry occurred.


@pytest.mark.parametrize("configuration", [None, True, 1, "", "  "])
def test_invalid_configuration_never_connects(monkeypatch, configuration):
    calls = install(monkeypatch, Connection())  # Observe unexpected access.
    with pytest.raises(ValueError, match="^Invalid execution database configuration$"):
        module.create_postgresql_execution_receiver(configuration)
    assert calls == []  # Configuration must fail before database access.


@pytest.mark.parametrize("key", EVENT_KEYS)  # Remove every required event field.
def test_invalid_event_never_connects(monkeypatch, key):
    connection = Connection()  # Prepare an unused connection substitute.
    calls = install(monkeypatch, connection)
    candidate = event()  # Begin with a supported event.
    del candidate[key]  # Make the event invalid.
    original = deepcopy(candidate)  # Preserve the malformed input.
    with pytest.raises(ExecutionAuditValidationError):
        module.create_postgresql_execution_receiver(CONNINFO)(candidate)
    assert calls == []  # Validation must precede connection creation.
    assert connection.operations == []  # No SQL or cleanup should be attempted.
    assert candidate == original  # Reject without changing caller input.


@pytest.mark.parametrize("status", ["started", "completed", "failed"])
def test_success_checks_relationships_before_strict_insert(monkeypatch, status):
    candidate = event(status)  # Exercise each supported status.
    original = deepcopy(candidate)  # Preserve independent expected values.
    records = [] if status == "started" else [start_record(candidate)]
    connection = Connection(records=records)  # Supply valid stored relationships.
    calls = install(monkeypatch, connection)
    receive = module.create_postgresql_execution_receiver(CONNINFO)
    assert calls == []  # Configuration alone must not connect.
    assert receive(candidate) is None  # Success follows commit and close.
    assert calls == [(CONNINFO, {"connect_timeout": 5})]
    assert connection.isolation_level == psycopg.IsolationLevel.READ_COMMITTED
    assert connection.operations == [  # Check lock-before-read and insert order.
        ("execute", "SET LOCAL synchronous_commit = on", None),
        ("execute", "SET LOCAL lock_timeout = '2s'", None),
        ("execute", "SET LOCAL statement_timeout = '5s'", None),
        (
            "execute",
            module._LOCK,
            ("gateway.execution_events:" + original["execution_id"],),
        ),
        ("execute", module._CONTEXT, (original["policy_event_id"],)),
        ("execute", module._ATTEMPT, (original["execution_id"],)),
        ("execute", module._INSERT, tuple(original[key] for key in EVENT_KEYS)),
        ("commit",),
        ("close",),
    ]
    assert "ON CONFLICT" not in module._INSERT.upper()  # Forbid duplicate repair.
    assert candidate == original  # Successful delivery preserves caller data.


@pytest.mark.parametrize("field", [None, 0, 1, 2, 3, 4, 5])
def test_missing_or_mismatched_context_rejects_without_insert(monkeypatch, field):
    connection = Connection()  # Start with a valid stored context.
    if field is None:  # Model a missing decision or binding.
        connection.context = None
    else:  # Change one selected stored relationship value.
        values = list(connection.context)
        values[field] = "unsupported"
        connection.context = tuple(values)
    install(monkeypatch, connection)
    with pytest.raises(
        module.ExecutionRelationshipError, match="^Invalid execution relationship$"
    ):
        module.create_postgresql_execution_receiver(CONNINFO)(event())
    assert not any(  # Rejection must occur before insertion or commit.
        op[0] == "commit" or (op[0] == "execute" and op[1] == module._INSERT)
        for op in connection.operations
    )
    assert connection.operations[-2:] == [("rollback",), ("close",)]


@pytest.mark.parametrize("status", ["started", "completed", "failed"])
def test_existing_attempt_blocks_another_start(monkeypatch, status):
    record = (*start_record(event())[:5], status)  # Model any existing record.
    connection = Connection(records=[record])
    install(monkeypatch, connection)
    with pytest.raises(module.ExecutionRelationshipError):
        module.create_postgresql_execution_receiver(CONNINFO)(event())
    assert connection.executions == 6  # Stop after inspecting the attempt.
    assert connection.operations[-2:] == [("rollback",), ("close",)]


@pytest.mark.parametrize(  # Reject every unsupported terminal relationship.
    "case",
    [
        "missing",
        "two_rows",
        "policy",
        "principal",
        "tool",
        "operation",
        "completed",
        "failed",
        "earlier",
    ],
)
def test_terminal_requires_matching_start_and_time(monkeypatch, case):
    candidate = event("completed")  # Supply a structurally valid terminal event.
    row = list(start_record(candidate))  # Begin with a matching start.
    records = [tuple(row)]  # Default to one stored row.
    if case == "missing":  # A terminal event requires an earlier saved start.
        records = []
    elif case == "two_rows":  # A completed attempt cannot accept another outcome.
        records = [tuple(row), (*row[:5], "completed")]
    elif case in {"policy", "principal", "tool", "operation"}:
        position = ["policy", "principal", "tool", "operation"].index(case)
        row[position] = "different"  # Break one stored context relationship.
        records = [tuple(row)]
    elif case in {"completed", "failed"}:  # Reject terminal-only attempts too.
        row[5] = case
        records = [tuple(row)]
    else:  # The proposed terminal time precedes the start time.
        candidate["occurred_at"] = "2026-10-16T09:59:59Z"
    connection = Connection(records=records)
    install(monkeypatch, connection)
    with pytest.raises(module.ExecutionRelationshipError):
        module.create_postgresql_execution_receiver(CONNINFO)(candidate)
    assert connection.executions == 6  # Do not attempt insertion.
    assert connection.operations[-2:] == [("rollback",), ("close",)]


@pytest.mark.parametrize("stamp", ["2026-10-16T10:00:00Z", "2026-10-17T00:00:00Z"])
def test_terminal_accepts_equal_or_later_time(monkeypatch, stamp):
    candidate = event("completed")  # Begin at the start's exact timestamp.
    connection = Connection(records=[start_record(candidate)])
    candidate["occurred_at"] = stamp  # Supply equal or later terminal time.
    install(monkeypatch, connection)
    assert module.create_postgresql_execution_receiver(CONNINFO)(candidate) is None


@pytest.mark.parametrize("failure_at", [1, 2, 3, 4, 5, 6, 7, "commit"])
def test_database_failures_preserve_error_and_do_not_retry(monkeypatch, failure_at):
    connection = Connection(failure_at=failure_at)  # Fail one delivery boundary.
    calls = install(monkeypatch, connection)
    with pytest.raises(OSError) as caught:
        module.create_postgresql_execution_receiver(CONNINFO)(event())
    assert caught.value is connection.failure  # Preserve the original failure.
    assert len(calls) == 1  # Never reconnect automatically.
    assert connection.operations[-2:] == [("rollback",), ("close",)]
    if failure_at != "commit":  # No pre-commit failure may attempt commit.
        assert ("commit",) not in connection.operations


def test_cleanup_failures_do_not_replace_delivery_failure(monkeypatch):
    connection = Connection(failure_at=7, cleanup_fails=True)
    install(monkeypatch, connection)
    with pytest.raises(OSError) as caught:
        module.create_postgresql_execution_receiver(CONNINFO)(event())
    assert caught.value is connection.failure  # Keep the insertion failure.
    assert connection.operations[-2:] == [("rollback",), ("close",)]


def test_close_failure_after_commit_propagates(monkeypatch):
    connection = Connection(failure_at="close")  # Model saved-but-failed delivery.
    calls = install(monkeypatch, connection)
    with pytest.raises(RuntimeError, match="^close failed$"):
        module.create_postgresql_execution_receiver(CONNINFO)(event())
    assert len(calls) == 1  # Do not retry a committed event.
    assert connection.operations[-2:] == [("commit",), ("close",)]
    assert ("rollback",) not in connection.operations  # Do not imply reversal.


def test_unique_violation_propagates_without_repair(monkeypatch):
    connection = Connection(failure_at=7)  # Fail the strict INSERT.
    connection.failure = psycopg.errors.UniqueViolation("synthetic duplicate")
    calls = install(monkeypatch, connection)
    with pytest.raises(psycopg.errors.UniqueViolation) as caught:
        module.create_postgresql_execution_receiver(CONNINFO)(event())
    assert caught.value is connection.failure  # Preserve the database error.
    assert len(calls) == 1  # No retry or silent reuse.
    assert connection.operations[-2:] == [("rollback",), ("close",)]


def test_connect_failure_propagates_without_retry(monkeypatch):
    calls = []  # Observe connection attempts before any connection exists.
    failure = psycopg.OperationalError("synthetic connection failure")

    def connect(*args, **kwargs):  # Fail at connection creation.
        calls.append((args, kwargs))
        raise failure

    monkeypatch.setattr(module.psycopg, "connect", connect)
    with pytest.raises(psycopg.OperationalError) as caught:
        module.create_postgresql_execution_receiver(CONNINFO)(event())
    assert caught.value is failure  # Preserve the original connection error.
    assert len(calls) == 1  # Never retry automatically.
