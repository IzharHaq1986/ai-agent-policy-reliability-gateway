"""Opt-in PostgreSQL acceptance for paired decision and binding delivery."""

import os  # Read only the explicit isolated-test connection settings.
from copy import deepcopy  # Verify caller input preservation.
from uuid import uuid4  # Give each test independent fixture identifiers.

import psycopg  # Use real PostgreSQL connections.
import pytest  # Supply fixtures and expected failure checks.
from psycopg.rows import dict_row  # Read stored records as dictionaries.

from gateway import postgresql_binding_receiver as module  # Exercise the new receiver.
from gateway.audit import build_audit_event  # Build valid policy events.
from gateway.postgresql_audit_receiver import (  # Prepare existing policy-only records.
    create_postgresql_audit_receiver,
)  # Preserve coverage of compatibility with policy-only storage.
from gateway.request_binding import (
    build_request_binding,
)  # Build matching request data.


@pytest.fixture  # Supply explicitly configured isolated database access.
def database():  # Refuse to silently use a developer or production database.
    writer = os.environ.get(
        "GATEWAY_TEST_WRITER_DSN"
    )  # Read test writer configuration.
    reader = os.environ.get(
        "GATEWAY_TEST_READER_DSN"
    )  # Read test reader configuration.
    if not writer or not reader:  # Require both explicit test connections.
        pytest.skip("Explicit isolated PostgreSQL test connections required")
    for conninfo, role in (  # Verify both database identities.
        (writer, "gateway_audit_writer"),  # Writer may append and inspect.
        (reader, "gateway_audit_reader"),  # Reader may inspect only.
    ):  # Check actual identity before any fixture writes.
        with psycopg.connect(conninfo, connect_timeout=5) as connection:
            identity = connection.execute(  # Query the real database and role.
                "SELECT current_database(), current_user"
            ).fetchone()  # Read the identity row.
            assert identity == ("gateway_audit_test", role)  # Require test boundaries.
    return writer, reader  # Give tests only the checked connection settings.


def pair():  # Generate a fresh matching pair for each test.
    policy = build_audit_event(  # Use the actual event validator and builder.
        {  # Supply the supported non-executing allow result.
            "decision": "ALLOW",  # Select the receiver's supported decision.
            "reason_code": "POLICY_ALLOW",  # Match the allow reason.
            "policy_version": "policy-core-v1",  # Preserve the existing version.
            "execution_status": "not_executed",  # No simulator is invoked.
        },  # Finish the policy result.
        principal_id="reliability-reader",  # Use the supported context.
        event_id=str(uuid4()),  # Avoid collisions between tests.
        occurred_at="2026-10-15T12:00:00Z",  # Use a fixed valid timestamp.
    )  # Finish the event.
    binding = build_request_binding(  # Associate the synthetic request with this event.
        policy_event_id=policy["event_id"],  # Match the exact event identifier.
        principal_id="reliability-reader",  # Match the supported principal.
        tool="fixture_store",  # Select the supported tool.
        operation="READ_ITEM",  # Select the supported operation.
        item_id=42,  # Use an allowed item.
    )  # Finish the binding.
    return policy, binding  # Return the independent input dictionaries.


def stored(reader, identifier):  # Inspect both tables through a separate connection.
    with psycopg.connect(  # Read committed data using the actual reader role.
        reader, connect_timeout=5, row_factory=dict_row
    ) as connection:  # Keep the inspection separate from the write transaction.
        policies = connection.execute(  # Read only this test's policy event.
            "SELECT schema_version, event_type, event_id, occurred_at, "
            "principal_id, decision, reason_code, policy_version, execution_status "
            "FROM gateway_audit.policy_decisions WHERE event_id = %s",
            (identifier,),  # Bind the lookup value separately from SQL.
        ).fetchall()  # Collect matching policy rows.
        bindings = connection.execute(  # Read only this test's request binding.
            "SELECT schema_version, policy_event_id, principal_id, tool, "
            "operation, item_id FROM gateway_audit.request_bindings "
            "WHERE policy_event_id = %s",
            (identifier,),  # Reuse the same decision identifier.
        ).fetchall()  # Collect matching binding rows.
    return policies, bindings  # Return both independently inspected results.


def test_acknowledged_pair_is_visible_to_separate_reader(database):
    writer, reader = database  # Use verified test roles.
    supplied = pair()  # Create a unique supported pair.
    original = deepcopy(supplied)  # Preserve expected values.
    assert module.create_postgresql_binding_receiver(writer)(*supplied) is None
    assert stored(reader, supplied[0]["event_id"]) == ([original[0]], [original[1]])
    assert supplied == original  # Delivery must not change caller input.


@pytest.mark.parametrize("conflicting", [False, True])  # Reject both duplicate forms.
def test_duplicate_pair_preserves_original_records(database, conflicting):
    writer, reader = database  # Use verified test connections.
    supplied = pair()  # Create a unique pair.
    receive = module.create_postgresql_binding_receiver(writer)  # Configure delivery.
    receive(*supplied)  # Save the original pair.
    duplicate = deepcopy(supplied)  # Prepare another attempt with the same identifiers.
    if conflicting:  # Also test different content under the same identity.
        duplicate[1]["item_id"] = 43  # Keep the replacement structurally valid.
    with pytest.raises(psycopg.errors.UniqueViolation):  # Strict INSERT must fail.
        receive(*duplicate)  # No duplicate repair or silent reuse is allowed.
    assert stored(reader, supplied[0]["event_id"]) == ([supplied[0]], [supplied[1]])


def test_existing_policy_only_record_is_not_completed_or_modified(database):
    writer, reader = database  # Use verified test roles.
    policy, binding = pair()  # Prepare a matching pair.
    create_postgresql_audit_receiver(writer)(policy)  # Save only the policy beforehand.
    with pytest.raises(psycopg.errors.UniqueViolation):  # Existing event must conflict.
        module.create_postgresql_binding_receiver(writer)(policy, binding)
    assert stored(reader, policy["event_id"]) == ([policy], [])  # No binding was added.


class WrappedConnection:  # Wrap a real connection to inject test failures.
    def __init__(self, connection, failure):  # Select one failure boundary.
        self.connection = connection  # Retain the actual PostgreSQL connection.
        self.failure = failure  # Select binding permission, commit, or close failure.

    @property  # Expose the real isolation setting.
    def isolation_level(self):  # Match the receiver's connection interface.
        return self.connection.isolation_level  # Read the actual setting.

    @isolation_level.setter  # Apply isolation changes to the real connection.
    def isolation_level(self, value):  # Receive the requested isolation level.
        self.connection.isolation_level = value  # Preserve real transaction behavior.

    def execute(self, statement, params=None):  # Forward real database statements.
        if statement == module._BINDING_INSERT and self.failure == "binding":
            self.connection.execute(  # Remove INSERT authority after the parent insert.
                "SET LOCAL ROLE gateway_audit_reader"
            )  # The binding insert now fails under actual database permissions.
        return self.connection.execute(
            statement, params
        )  # Execute real parameterized SQL.

    def commit(self):  # Exercise a real commit before simulating lost acknowledgement.
        self.connection.commit()  # Save the transaction in PostgreSQL.
        if (
            self.failure == "commit_ack"
        ):  # Simulate failure after the database committed.
            raise psycopg.OperationalError("synthetic lost commit acknowledgement")

    def rollback(self):  # Forward the receiver's cleanup attempt.
        self.connection.rollback()  # Cannot undo an already completed commit.

    def close(self):  # Close the real connection before optional error injection.
        self.connection.close()  # Release resources even in the fault test.
        if self.failure == "close":  # Simulate a close error after successful commit.
            raise RuntimeError("synthetic close failure")


def install_wrapper(
    monkeypatch, failure
):  # Inject faults without replacing database work.
    real_connect = psycopg.connect  # Preserve access to the real driver.
    connections = []  # Track underlying connections and retry attempts.

    def connect(*args, **kwargs):  # Open one real connection per receiver attempt.
        connection = real_connect(*args, **kwargs)  # Connect to the isolated database.
        connections.append(connection)  # Retain it for cleanup assertions.
        return WrappedConnection(connection, failure)  # Inject the selected boundary.

    monkeypatch.setattr(
        module.psycopg, "connect", connect
    )  # Patch only within a context.
    return connections  # Let tests verify no retry and successful connection closure.


def test_real_second_insert_failure_rolls_back_parent(database, monkeypatch):
    writer, reader = database  # Use the real isolated database.
    supplied = pair()  # Create unique valid inputs.
    with monkeypatch.context() as patch:  # Restore driver behavior before inspection.
        connections = install_wrapper(patch, "binding")  # Fail the binding INSERT.
        with pytest.raises(
            psycopg.errors.InsufficientPrivilege
        ):  # Require real denial.
            module.create_postgresql_binding_receiver(writer)(*supplied)
    assert len(connections) == 1  # Do not retry automatically.
    assert connections[0].closed  # Release the failed transaction's connection.
    assert stored(reader, supplied[0]["event_id"]) == (
        [],
        [],
    )  # Parent rolled back too.


@pytest.mark.parametrize(
    "failure", ["commit_ack", "close"]
)  # Cover saved-but-failed calls.
def test_post_commit_failure_can_leave_both_records_saved(
    database, monkeypatch, failure
):
    writer, reader = database  # Use verified test roles.
    supplied = pair()  # Create a unique pair.
    expected_error = (
        psycopg.OperationalError if failure == "commit_ack" else RuntimeError
    )
    with monkeypatch.context() as patch:  # Restore the driver before independent reads.
        connections = install_wrapper(patch, failure)  # Inject a post-commit failure.
        with pytest.raises(expected_error):  # The receiver must not report success.
            module.create_postgresql_binding_receiver(writer)(*supplied)
    assert len(connections) == 1  # Never retry the uncertain outcome.
    assert connections[0].closed  # Ensure the actual connection was closed.
    assert stored(reader, supplied[0]["event_id"]) == ([supplied[0]], [supplied[1]])


def test_reader_credentials_cannot_persist_either_record(database):
    _, reader = database  # Intentionally use the read-only role for delivery.
    supplied = pair()  # Keep input valid so permissions are actually tested.
    with pytest.raises(
        psycopg.errors.InsufficientPrivilege
    ):  # Require real INSERT denial.
        module.create_postgresql_binding_receiver(reader)(*supplied)
    assert stored(reader, supplied[0]["event_id"]) == (
        [],
        [],
    )  # Neither record was saved.
