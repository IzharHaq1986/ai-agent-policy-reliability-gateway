"""Opt-in real PostgreSQL checks for execution-event delivery."""

import os  # Read only explicitly supplied isolated-test settings.
from concurrent.futures import ThreadPoolExecutor  # Run two real calls.
from copy import deepcopy  # Check that delivery preserves caller input.
from threading import Event  # Coordinate calls without guessing timing.
from time import monotonic  # Bound observation of database lock waits.
from uuid import uuid4  # Give each test independent record identities.

import psycopg  # Exercise real PostgreSQL transactions.
import pytest  # Supply fixtures and expected rejection checks.
from psycopg.rows import dict_row  # Inspect saved records by field name.

from gateway import postgresql_execution_receiver as module  # Test real delivery.
from gateway.audit import build_audit_event  # Build valid policy fixtures.
from gateway.execution_audit import build_execution_event  # Build valid events.
from gateway.postgresql_binding_receiver import (  # Save prerequisite pairs.
    create_postgresql_binding_receiver,
)
from gateway.request_binding import build_request_binding  # Build request links.


@pytest.fixture
def database():  # Require and verify the disposable database boundary.
    writer = os.environ.get("GATEWAY_TEST_WRITER_DSN")  # Read test writer setup.
    reader = os.environ.get("GATEWAY_TEST_READER_DSN")  # Read test reader setup.
    if not writer or not reader:  # Never silently select another database.
        pytest.skip("Explicit isolated PostgreSQL test connections required")
    for conninfo, role in (  # Verify both configured identities before writes.
        (writer, "gateway_audit_writer"),
        (reader, "gateway_audit_reader"),
    ):
        with psycopg.connect(conninfo, connect_timeout=5) as connection:
            identity = connection.execute(  # Inspect actual database and role.
                "SELECT current_database(), current_user"
            ).fetchone()
            assert identity == ("gateway_audit_test", role)
    return writer, reader  # Return only checked test connections.


def prepare(writer):  # Save one valid policy and request-binding pair.
    identifier = str(uuid4())  # Isolate this fixture from every other test.
    policy = build_audit_event(  # Reuse existing policy validation.
        {
            "decision": "ALLOW",  # Supply the supported decision.
            "reason_code": "POLICY_ALLOW",  # Supply its matching reason.
            "policy_version": "policy-core-v1",  # Preserve the existing policy.
            "execution_status": "not_executed",  # No simulator has run.
        },
        principal_id="reliability-reader",
        event_id=identifier,
        occurred_at="2026-10-16T10:00:00Z",
    )
    binding = build_request_binding(  # Link the synthetic request explicitly.
        policy_event_id=identifier,
        principal_id="reliability-reader",
        tool="fixture_store",
        operation="READ_ITEM",
        item_id=42,
    )
    create_postgresql_binding_receiver(writer)(policy, binding)  # Save atomically.
    return identifier  # Return the saved policy and binding reference.


def event(policy_id, *, execution_id=None, status="started"):
    return build_execution_event(  # Build a fresh supported execution record.
        event_id=str(uuid4()),  # Give this record its own identity.
        execution_id=execution_id or str(uuid4()),  # Share only when requested.
        policy_event_id=policy_id,
        occurred_at="2026-10-16T10:01:00Z",
        principal_id="reliability-reader",
        tool="fixture_store",
        operation="READ_ITEM",
        status=status,
        reason_code={  # Select a valid reason for the requested status.
            "started": "SIMULATION_STARTED",
            "completed": "SIMULATION_COMPLETED",
            "failed": "SIMULATOR_ERROR",
        }[status],
    )


def stored(reader, execution_id):  # Inspect through a separate reader connection.
    with psycopg.connect(reader, connect_timeout=5, row_factory=dict_row) as connection:
        return connection.execute(  # Read only this test's execution attempt.
            "SELECT schema_version, event_type, event_id, execution_id, "
            "policy_event_id, occurred_at, principal_id, tool, operation, "
            "status, reason_code FROM gateway_audit.execution_events "
            "WHERE execution_id = %s ORDER BY status",
            (execution_id,),  # Keep the identifier separate from SQL syntax.
        ).fetchall()


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_committed_start_and_terminal_are_visible(database, status):
    writer, reader = database  # Use verified isolated roles.
    policy_id = prepare(writer)  # Save the required authorization link.
    start = event(policy_id)  # Begin one independent attempt.
    terminal = event(  # Link the final record to that exact attempt.
        policy_id, execution_id=start["execution_id"], status=status
    )
    original = deepcopy((start, terminal))  # Preserve expected caller data.
    receive = module.create_postgresql_execution_receiver(writer)
    assert receive(start) is None  # Require acknowledged start delivery.
    assert stored(reader, start["execution_id"]) == [start]
    assert receive(terminal) is None  # Equal start and terminal times are valid.
    expected = sorted([start, terminal], key=lambda row: row["status"])
    assert stored(reader, start["execution_id"]) == expected
    assert (start, terminal) == original  # Delivery must preserve both inputs.


def test_missing_binding_leaves_no_execution_event(database):
    writer, reader = database  # Use real checked connections.
    candidate = event(str(uuid4()))  # Reference a binding that does not exist.
    with pytest.raises(module.ExecutionRelationshipError):
        module.create_postgresql_execution_receiver(writer)(candidate)
    assert stored(reader, candidate["execution_id"]) == []  # Reject without writes.


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_terminal_without_start_is_rejected(database, status):
    writer, reader = database  # Keep the prerequisite binding valid.
    candidate = event(prepare(writer), status=status)  # Omit only the start.
    with pytest.raises(module.ExecutionRelationshipError):
        module.create_postgresql_execution_receiver(writer)(candidate)
    assert stored(reader, candidate["execution_id"]) == []


def test_terminal_cannot_switch_policy_reference(database):
    writer, reader = database  # Prepare two separately valid authorization links.
    first_policy = prepare(writer)
    second_policy = prepare(writer)
    start = event(first_policy)  # Save the attempt under the first link.
    receive = module.create_postgresql_execution_receiver(writer)
    receive(start)
    terminal = event(  # Try to move the same attempt to another valid link.
        second_policy, execution_id=start["execution_id"], status="completed"
    )
    with pytest.raises(module.ExecutionRelationshipError):
        receive(terminal)
    assert stored(reader, start["execution_id"]) == [start]  # Preserve the start.


def test_terminal_before_start_time_is_rejected(database):
    writer, reader = database  # Use a valid saved start.
    start = event(prepare(writer))
    receive = module.create_postgresql_execution_receiver(writer)
    receive(start)
    terminal = event(
        start["policy_event_id"],
        execution_id=start["execution_id"],
        status="completed",
    )
    terminal["occurred_at"] = "2026-10-16T10:00:59Z"  # Precede the start by a second.
    with pytest.raises(module.ExecutionRelationshipError):
        receive(terminal)
    assert stored(reader, start["execution_id"]) == [start]


def test_duplicate_start_preserves_original_event(database):
    writer, reader = database  # Use an existing start under a valid binding.
    start = event(prepare(writer))
    receive = module.create_postgresql_execution_receiver(writer)
    receive(start)
    duplicate = deepcopy(start)  # Also test a different event identity.
    duplicate["event_id"] = str(uuid4())
    with pytest.raises(module.ExecutionRelationshipError):
        receive(duplicate)
    assert stored(reader, start["execution_id"]) == [start]


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_second_terminal_preserves_first_outcome(database, status):
    writer, reader = database  # Save one complete attempt.
    start = event(prepare(writer))
    receive = module.create_postgresql_execution_receiver(writer)
    receive(start)
    first = event(
        start["policy_event_id"],
        execution_id=start["execution_id"],
        status="completed",
    )
    receive(first)
    second = event(  # Attempt another terminal record with a fresh event ID.
        start["policy_event_id"], execution_id=start["execution_id"], status=status
    )
    with pytest.raises(module.ExecutionRelationshipError):
        receive(second)
    assert stored(reader, start["execution_id"]) == [first, start]


def test_reader_credentials_cannot_insert_execution_event(database):
    writer, reader = database  # Save prerequisites with the authorized writer.
    candidate = event(prepare(writer))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        module.create_postgresql_execution_receiver(reader)(candidate)
    assert stored(reader, candidate["execution_id"]) == []  # No event was saved.


def test_saved_deny_and_binding_cannot_support_execution_event(database):
    writer, reader = database  # Use verified isolated database roles.
    policy_id = str(uuid4())  # Keep this denied request independent.
    with psycopg.connect(writer, connect_timeout=5) as connection:
        connection.execute(  # Save a valid DENY policy record directly.
            "INSERT INTO gateway_audit.policy_decisions "
            "(schema_version, event_type, event_id, occurred_at, principal_id, "
            "decision, reason_code, policy_version, execution_status) "
            "VALUES (1, 'policy_decision', %s, '2026-10-16T10:00:00Z', "
            "'reliability-reader', 'DENY', 'NOT_AUTHORIZED', "
            "'policy-core-v1', 'not_executed')",
            (policy_id,),
        )
        connection.execute(  # The foreign key permits a binding to a DENY row.
            "INSERT INTO gateway_audit.request_bindings "
            "(schema_version, policy_event_id, principal_id, tool, operation, "
            "item_id) VALUES (1, %s, 'reliability-reader', "
            "'fixture_store', 'READ_ITEM', 42)",
            (policy_id,),
        )
    candidate = event(policy_id)  # Supply a structurally valid start event.
    with pytest.raises(module.ExecutionRelationshipError):
        module.create_postgresql_execution_receiver(writer)(candidate)
    assert stored(reader, candidate["execution_id"]) == []  # No start was saved.


class ExecutionFaultConnection:  # Keep real SQL while injecting one failure.
    def __init__(self, connection, failure):
        self.connection = connection  # Retain the actual database connection.
        self.failure = failure  # Select insert, acknowledgement, or close failure.

    @property
    def isolation_level(self):  # Expose the real transaction isolation setting.
        return self.connection.isolation_level

    @isolation_level.setter
    def isolation_level(self, value):  # Apply receiver settings to PostgreSQL.
        self.connection.isolation_level = value

    def execute(self, statement, params=None):  # Forward actual SQL operations.
        if statement == module._INSERT and self.failure == "insert":
            self.connection.execute(  # Remove write permission before insertion.
                "SET LOCAL ROLE gateway_audit_reader"
            )
        return self.connection.execute(statement, params)  # Preserve real behavior.

    def commit(self):  # Perform a real commit before acknowledgement failure.
        self.connection.commit()
        if self.failure == "commit_ack":  # Model saved data with lost confirmation.
            raise psycopg.OperationalError("synthetic lost commit acknowledgement")

    def rollback(self):  # Forward cleanup without claiming commit reversal.
        self.connection.rollback()

    def close(self):  # Release the real connection even in the close-failure case.
        self.connection.close()
        if self.failure == "close":  # Report failure after resources were released.
            raise RuntimeError("synthetic close failure")


@pytest.mark.parametrize("failure", ["insert", "commit_ack", "close"])
def test_real_delivery_failure_has_documented_saved_state(
    database, monkeypatch, failure
):
    writer, reader = database  # Verify test boundaries before fault injection.
    candidate = event(prepare(writer))  # Save valid prerequisite records first.
    original = deepcopy(candidate)  # Preserve the caller's input.
    real_connect = psycopg.connect  # Retain the real driver for wrapped delivery.
    connections = []  # Track connections to detect retries and resource leaks.

    def connect(*args, **kwargs):  # Wrap each actual receiver connection.
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return ExecutionFaultConnection(connection, failure)

    expected_error = {  # Distinguish database denial from post-commit failures.
        "insert": psycopg.errors.InsufficientPrivilege,
        "commit_ack": psycopg.OperationalError,
        "close": RuntimeError,
    }[failure]
    with monkeypatch.context() as patch:  # Restore the driver before inspection.
        patch.setattr(module.psycopg, "connect", connect)
        with pytest.raises(expected_error):
            module.create_postgresql_execution_receiver(writer)(candidate)
    assert len(connections) == 1  # Never retry automatically after failure.
    assert connections[0].closed  # Release the real database connection.
    expected = [] if failure == "insert" else [original]  # Commit changes the result.
    assert stored(reader, candidate["execution_id"]) == expected
    assert candidate == original  # Failure must preserve caller input.


def concurrent_delivery(database, monkeypatch, first, second):
    writer, _ = database  # Use the verified isolated writer.
    real_connect = psycopg.connect  # Keep observer connections outside the patch.
    acquired = Event()  # Signal when the first call holds its attempt lock.
    release = Event()  # Keep that lock until PostgreSQL confirms the second waits.
    second_connected = Event()  # Expose the second connection's backend identity.
    connections = []  # Preserve connection identities and cleanup evidence.

    class PausedConnection(ExecutionFaultConnection):
        def execute(self, statement, params=None):  # Execute real database work.
            result = self.connection.execute(statement, params)
            if statement == module._LOCK and self.connection is connections[0]:
                acquired.set()  # The first receiver now owns the actual lock.
                if not release.wait(10):  # Prevent an indefinite test-only pause.
                    raise TimeoutError("Test coordination timed out")
            return result  # Preserve actual cursor results.

    def connect(*args, **kwargs):  # Open separate real receiver connections.
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        if len(connections) == 2:  # Let the observer inspect the second backend.
            second_connected.set()
        return PausedConnection(connection, None)  # Inject coordination only.

    def deliver(candidate):  # Return failures for explicit assertions below.
        try:
            return module.create_postgresql_execution_receiver(writer)(candidate)
        except Exception as error:  # Do not hide errors from the test assertions.
            return error

    with monkeypatch.context() as patch:  # Restore the driver after workers finish.
        patch.setattr(module.psycopg, "connect", connect)
        with ThreadPoolExecutor(max_workers=2) as workers:
            first_future = workers.submit(deliver, first)  # Acquire the lock first.
            try:
                assert acquired.wait(5), "First receiver did not acquire its lock"
                second_future = workers.submit(deliver, second)  # Compete for it.
                assert second_connected.wait(5), "Second receiver did not connect"
                first_pid = connections[0].info.backend_pid  # Identify lock owner.
                second_pid = connections[1].info.backend_pid  # Identify waiting call.
                deadline = monotonic() + 1.5  # Stay below the receiver lock timeout.
                blocked = False  # Require observed blocking, not elapsed time alone.
                with real_connect(
                    writer, connect_timeout=5, autocommit=True
                ) as observer:
                    while monotonic() < deadline:  # Poll for an actual lock wait.
                        row = observer.execute(
                            "SELECT %s = ANY(pg_blocking_pids(%s))",
                            (first_pid, second_pid),
                        ).fetchone()
                        if row == (True,):  # PostgreSQL confirms the dependency.
                            blocked = True
                            break
                        release.wait(0.01)  # Bound polling without busy spinning.
                assert blocked, "Second receiver was not observed waiting"
            finally:
                release.set()  # Release the first call even if an assertion fails.
            results = (  # Collect both outcomes after the first transaction finishes.
                first_future.result(timeout=10),
                second_future.result(timeout=10),
            )
    assert len(connections) == 2  # Neither call may reconnect or retry.
    assert all(connection.closed for connection in connections)  # Release resources.
    return results  # Let each scenario check its own required outcomes.


@pytest.mark.parametrize("terminal", [False, True])
def test_competing_records_save_only_one_new_event(database, monkeypatch, terminal):
    writer, reader = database  # Prepare valid committed prerequisites.
    policy_id = prepare(writer)
    start = event(policy_id)
    if terminal:  # Compete over final outcomes for an already-started attempt.
        module.create_postgresql_execution_receiver(writer)(start)
        first = event(policy_id, execution_id=start["execution_id"], status="completed")
        second = event(policy_id, execution_id=start["execution_id"], status="failed")
    else:  # Compete over two starts with different event identities.
        first = start
        second = event(policy_id, execution_id=start["execution_id"])
    first_result, second_result = concurrent_delivery(
        database, monkeypatch, first, second
    )
    assert first_result is None  # The lock owner saves its valid event.
    assert isinstance(second_result, module.ExecutionRelationshipError)
    expected = [first, start] if terminal else [first]  # Preserve only one winner.
    assert stored(reader, start["execution_id"]) == expected


def test_waiting_terminal_observes_newly_committed_start(database, monkeypatch):
    writer, reader = database  # Begin with no execution rows for this attempt.
    policy_id = prepare(writer)
    start = event(policy_id)
    terminal = event(policy_id, execution_id=start["execution_id"], status="completed")
    results = concurrent_delivery(database, monkeypatch, start, terminal)
    assert results == (None, None)  # The waiting call must see the committed start.
    assert stored(reader, start["execution_id"]) == [terminal, start]


def test_lock_timeout_saves_nothing_and_closes_connection(database, monkeypatch):
    writer, reader = database  # Use the same lock convention as the receiver.
    candidate = event(prepare(writer))
    real_connect = psycopg.connect  # Keep the lock holder independent of patching.
    connections = []  # Observe receiver cleanup after the real timeout.

    def connect(*args, **kwargs):  # Track actual receiver connections.
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    with real_connect(writer, connect_timeout=5) as holder:
        holder.execute(  # Hold the exact attempt lock in another transaction.
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ("gateway.execution_events:" + candidate["execution_id"],),
        )
        with monkeypatch.context() as patch:
            patch.setattr(module.psycopg, "connect", connect)
            with pytest.raises(psycopg.errors.LockNotAvailable):
                module.create_postgresql_execution_receiver(writer)(candidate)
        assert len(connections) == 1  # A timeout must not trigger automatic retry.
        assert connections[0].closed  # Release the failed receiver connection.
        assert stored(reader, candidate["execution_id"]) == []  # No event was saved.
    assert module.create_postgresql_execution_receiver(writer)(candidate) is None
    assert stored(reader, candidate["execution_id"]) == [candidate]
