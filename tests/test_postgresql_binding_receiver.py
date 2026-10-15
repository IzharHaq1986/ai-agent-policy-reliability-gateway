"""Unit tests for paired PostgreSQL decision and binding delivery."""

from copy import deepcopy  # Check that caller inputs stay unchanged.

import psycopg  # Check isolation settings and database exception propagation.
import pytest  # Run cases and assert expected failures.

from gateway import postgresql_binding_receiver as module  # Test the real receiver.
from gateway.audit import build_audit_event  # Build valid policy fixtures.
from gateway.request_binding import build_request_binding  # Build matching bindings.

CONNINFO = "dbname=synthetic_audit user=synthetic_writer"  # Use non-secret test setup.
POLICY_KEYS = (  # Define the required database parameter order independently.
    "schema_version",
    "event_type",
    "event_id",
    "occurred_at",
    "principal_id",
    "decision",
    "reason_code",
    "policy_version",
    "execution_status",
)  # Finish the policy column order.
BINDING_KEYS = (  # Define the binding INSERT parameter order.
    "schema_version",
    "policy_event_id",
    "principal_id",
    "tool",
    "operation",
    "item_id",
)  # Finish the binding column order.


def pair():  # Return new valid inputs for every test.
    policy = build_audit_event(  # Use the existing policy-event builder.
        {  # Supply the supported non-executing ALLOW result.
            "decision": "ALLOW",  # Permit the supported synthetic request.
            "reason_code": "POLICY_ALLOW",  # Match the allow decision.
            "policy_version": "policy-core-v1",  # Use the existing policy version.
            "execution_status": "not_executed",  # No tool has run.
        },  # Finish the policy result.
        principal_id="reliability-reader",  # Supply the supported context.
        event_id="550e8400-e29b-41d4-a716-446655440000",  # Use a canonical UUID.
        occurred_at="2026-10-15T12:00:00Z",  # Use a calendar-valid UTC timestamp.
    )  # Finish the parent event.
    binding = build_request_binding(  # Build the matching request record.
        policy_event_id=policy["event_id"],  # Reference this exact event.
        principal_id="reliability-reader",  # Match its principal.
        tool="fixture_store",  # Use the supported tool.
        operation="READ_ITEM",  # Use the supported operation.
        item_id=42,  # Record the requested item.
    )  # Finish the binding.
    return policy, binding  # Keep the two records independently mutable for testing.


class Connection:  # Simulate transaction operations without opening a database.
    def __init__(self, *, failure_at=None, failure=None, cleanup_fails=False):
        self.operations = []  # Record the actual call order.
        self.isolation_level = None  # Capture the requested isolation setting.
        self.failure_at = failure_at  # Select the operation that should fail.
        self.failure = failure if failure is not None else OSError("delivery failed")
        self.cleanup_fails = cleanup_fails  # Optionally break rollback and close.
        self.executions = 0  # Count setup statements and inserts.

    def execute(self, statement, params=None):  # Record one database statement.
        self.executions += 1  # Identify its position in the transaction.
        self.operations.append(("execute", statement, params))  # Preserve parameters.
        if self.failure_at == self.executions:  # Fail the selected statement.
            raise self.failure  # Preserve the exact simulated database exception.
        return self  # Match the connection's chaining convention.

    def commit(self):  # Simulate transaction acknowledgement.
        self.operations.append(("commit",))  # Record every commit attempt.
        if self.failure_at == "commit":  # Simulate uncertain commit delivery.
            raise self.failure  # Do not imply that the database rolled back.

    def rollback(self):  # Record attempted failure recovery.
        self.operations.append(("rollback",))  # Track rollback order.
        if self.cleanup_fails:  # Simulate a broken cleanup connection.
            raise RuntimeError(
                "rollback failed"
            )  # Must not replace the original error.

    def close(self):  # Record connection release.
        self.operations.append(("close",))  # Track close even when it fails.
        if self.cleanup_fails or self.failure_at == "close":  # Simulate close failure.
            raise RuntimeError("close failed")  # Propagate only on the success path.


def install(monkeypatch, connection):  # Replace only connection creation.
    calls = []  # Track the number of connection attempts.

    def connect(conninfo, **kwargs):  # Capture actual connection arguments.
        calls.append((conninfo, kwargs))  # Preserve configuration and timeout.
        return connection  # Supply the controlled test connection.

    monkeypatch.setattr(module.psycopg, "connect", connect)  # Inject the connection.
    return calls  # Allow tests to verify no connection or retry occurred.


@pytest.mark.parametrize(
    "configuration", [None, 1, True, "", "  "]
)  # Reject bad setup.
def test_invalid_configuration_opens_no_connection(monkeypatch, configuration):
    calls = install(monkeypatch, Connection())  # Observe attempted database access.
    with pytest.raises(ValueError, match="^Invalid binding database configuration$"):
        module.create_postgresql_binding_receiver(configuration)  # Validate setup.
    assert calls == []  # Configuration rejection must occur without connection.


@pytest.mark.parametrize(  # Remove every required field from either record.
    "which,key",
    [(0, key) for key in POLICY_KEYS] + [(1, key) for key in BINDING_KEYS],
)  # Cover both existing validation contracts.
def test_missing_fields_open_no_connection(monkeypatch, which, key):
    connection = Connection()  # Prepare an unused database substitute.
    calls = install(monkeypatch, connection)  # Observe any unexpected connection.
    supplied = pair()  # Build independent valid inputs.
    del supplied[which][key]  # Remove the selected required field.
    original = deepcopy(supplied)  # Preserve the malformed input.
    with pytest.raises(  # Require the receiver's normalized validation error.
        module.BindingPairValidationError, match="^Invalid policy binding pair$"
    ):  # Exercise the actual receiver.
        module.create_postgresql_binding_receiver(CONNINFO)(*supplied)
    assert calls == []  # Validation must precede database access.
    assert connection.operations == []  # No database operations may occur.
    assert supplied == original  # Rejection must preserve caller input.


@pytest.mark.parametrize(  # Exercise malformed values and mismatched record links.
    "which,patch",
    [
        (0, {"schema_version": True}),  # Reject an invalid policy type.
        (0, {"execution_status": "executed"}),  # Preserve the policy contract.
        (0, {"credentials": "private"}),  # Reject extra policy content.
        (1, {"item_id": True}),  # Reject boolean item identifiers.
        (1, {"item_id": 1001}),  # Reject out-of-range items.
        (1, {"principal_id": "other-reader"}),  # Reject unsupported caller context.
        (1, {"output": "private"}),  # Reject extra binding content.
        (1, {"policy_event_id": "660e8400-e29b-41d4-a716-446655440000"}),
    ],
)  # The final case is individually valid but references another decision.
def test_invalid_pairs_open_no_connection(monkeypatch, which, patch):
    calls = install(monkeypatch, Connection())  # Track database access.
    supplied = pair()  # Start with a valid matching pair.
    supplied[which].update(patch)  # Introduce one invalid relationship or value.
    original = deepcopy(supplied)  # Preserve the modified candidate.
    with pytest.raises(  # Require the same generic validation message.
        module.BindingPairValidationError, match="^Invalid policy binding pair$"
    ):
        module.create_postgresql_binding_receiver(CONNINFO)(*supplied)
    assert calls == []  # Reject before opening a connection.
    assert supplied == original  # Preserve both input dictionaries.


@pytest.mark.parametrize(  # Valid DENY records must still fail this receiver's gate.
    "reason",
    [
        "UNAUTHENTICATED",
        "INVALID_REQUEST",
        "UNKNOWN_TOOL",
        "UNKNOWN_OPERATION",
        "NOT_AUTHORIZED",
    ],
)  # Cover every existing deny reason.
def test_deny_decisions_open_no_connection(monkeypatch, reason):
    calls = install(monkeypatch, Connection())  # Observe connection attempts.
    policy, binding = pair()  # Start with matching identifiers.
    policy.update(decision="DENY", reason_code=reason)  # Form a valid denial event.
    with pytest.raises(module.BindingPairValidationError):  # Require rejection.
        module.create_postgresql_binding_receiver(CONNINFO)(policy, binding)
    assert calls == []  # A denial must not reach database delivery.


def test_success_uses_one_transaction_and_preserves_inputs(monkeypatch):
    connection = Connection()  # Simulate successful database operations.
    calls = install(monkeypatch, connection)  # Observe connection creation.
    supplied = pair()  # Build a supported pair.
    original = deepcopy(supplied)  # Preserve the expected stored values.
    receive = module.create_postgresql_binding_receiver(CONNINFO)  # Configure delivery.
    assert calls == []  # Creating a receiver must not connect.
    assert receive(*supplied) is None  # Success must return only after cleanup.
    assert calls == [(CONNINFO, {"connect_timeout": 5})]  # Require one bounded connect.
    assert connection.isolation_level == psycopg.IsolationLevel.READ_COMMITTED
    assert (
        connection.operations
        == [  # Require exact setup, insertion, and cleanup order.
            ("execute", "SET LOCAL synchronous_commit = on", None),
            ("execute", "SET LOCAL lock_timeout = '2s'", None),
            ("execute", "SET LOCAL statement_timeout = '5s'", None),
            (
                "execute",
                module._POLICY_INSERT,
                tuple(original[0][key] for key in POLICY_KEYS),
            ),
            (
                "execute",
                module._BINDING_INSERT,
                tuple(original[1][key] for key in BINDING_KEYS),
            ),
            ("commit",),  # Commit once after both inserts.
            ("close",),  # Close before reporting success.
        ]
    )  # No extra lookup, repair, or retry may occur.
    assert (
        "ON CONFLICT" not in module._POLICY_INSERT.upper()
    )  # Require strict insertion.
    assert "ON CONFLICT" not in module._BINDING_INSERT.upper()  # No duplicate repair.
    assert supplied == original  # Successful delivery must preserve input.


@pytest.mark.parametrize(
    "failure_at", [1, 2, 3, 4, 5, "commit"]
)  # Fail each database stage.
def test_failure_rolls_back_closes_and_does_not_retry(monkeypatch, failure_at):
    connection = Connection(failure_at=failure_at)  # Inject one delivery failure.
    calls = install(monkeypatch, connection)  # Observe any attempted retry.
    supplied = pair()  # Build valid inputs so database handling is exercised.
    original = deepcopy(supplied)  # Preserve caller data.
    with pytest.raises(OSError) as caught:  # Require the original error to propagate.
        module.create_postgresql_binding_receiver(CONNINFO)(*supplied)
    assert caught.value is connection.failure  # Do not replace the delivery exception.
    assert len(calls) == 1  # Never reconnect automatically.
    assert connection.operations[-2:] == [("rollback",), ("close",)]  # Attempt cleanup.
    if failure_at != "commit":  # Pre-commit failures must never attempt commit.
        assert ("commit",) not in connection.operations
    assert supplied == original  # Failure must preserve caller input.


@pytest.mark.parametrize(  # Simulate uniqueness failures at either insert.
    "failure_at", [4, 5]
)  # Setup consumes the first three execute calls.
def test_duplicate_errors_propagate_without_repair(monkeypatch, failure_at):
    failure = psycopg.errors.UniqueViolation(
        "synthetic duplicate"
    )  # Model a duplicate.
    connection = Connection(
        failure_at=failure_at, failure=failure
    )  # Fail strict INSERT.
    calls = install(monkeypatch, connection)  # Track connections.
    with pytest.raises(psycopg.errors.UniqueViolation) as caught:  # Preserve DB error.
        module.create_postgresql_binding_receiver(CONNINFO)(*pair())
    assert caught.value is failure  # No conflict translation or silent success.
    assert len(calls) == 1  # No retry may follow a duplicate.
    assert ("commit",) not in connection.operations  # Do not save partial work.
    assert connection.operations[-2:] == [("rollback",), ("close",)]  # Clean up.


def test_cleanup_errors_do_not_hide_original_failure(monkeypatch):
    connection = Connection(
        failure_at=5, cleanup_fails=True
    )  # Break insert and cleanup.
    install(monkeypatch, connection)  # Supply the failing connection.
    with pytest.raises(OSError) as caught:  # Retain the original insertion failure.
        module.create_postgresql_binding_receiver(CONNINFO)(*pair())
    assert caught.value is connection.failure  # Cleanup errors must not replace it.
    assert connection.operations[-2:] == [
        ("rollback",),
        ("close",),
    ]  # Try both actions.


def test_close_failure_after_commit_propagates(monkeypatch):
    connection = Connection(failure_at="close")  # Commit succeeds but closing fails.
    calls = install(monkeypatch, connection)  # Observe attempts.
    with pytest.raises(RuntimeError, match="^close failed$"):  # Do not report success.
        module.create_postgresql_binding_receiver(CONNINFO)(*pair())
    assert len(calls) == 1  # Do not retry an already committed pair.
    assert connection.operations[-2:] == [("commit",), ("close",)]  # Preserve order.
    assert ("rollback",) not in connection.operations  # Do not claim commit reversal.


def test_connect_failure_propagates_without_retry(monkeypatch):
    calls = []  # Track connection attempts.
    failure = psycopg.OperationalError("synthetic connection failure")  # Model failure.

    def connect(*args, **kwargs):  # Fail before a connection is returned.
        calls.append((args, kwargs))  # Record the single attempt.
        raise failure  # Preserve the original connection error.

    monkeypatch.setattr(
        module.psycopg, "connect", connect
    )  # Inject connection failure.
    with pytest.raises(psycopg.OperationalError) as caught:  # Require propagation.
        module.create_postgresql_binding_receiver(CONNINFO)(*pair())
    assert caught.value is failure  # Preserve the connection error.
    assert len(calls) == 1  # Do not retry automatically.
