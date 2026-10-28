"""Opt-in PostgreSQL integration checks for fixed simulator execution."""

import os  # Read only explicitly supplied isolated-test settings.
from copy import deepcopy  # Verify that caller input is preserved.
from uuid import UUID  # Inject a repeated identifier without generating authority.

import psycopg  # Exercise actual PostgreSQL transactions.
import pytest  # Supply fixtures and expected assertions.
from psycopg.rows import dict_row  # Inspect persisted records by field name.

from gateway import simulator_execution as module  # Exercise real orchestration.

REAL_CONNECT = psycopg.connect  # Keep inspection independent of later fault injection.


@pytest.fixture
def database():  # Verify the disposable test database before creating records.
    writer = os.environ.get("GATEWAY_TEST_WRITER_DSN")
    reader = os.environ.get("GATEWAY_TEST_READER_DSN")
    if not writer or not reader:  # Do not silently select another database.
        pytest.skip("Explicit isolated PostgreSQL test connections required")
    for conninfo, role in (  # Check actual database and role identities.
        (writer, "gateway_audit_writer"),
        (reader, "gateway_audit_reader"),
    ):
        with REAL_CONNECT(conninfo, connect_timeout=5) as connection:
            identity = connection.execute(
                "SELECT current_database(), current_user"
            ).fetchone()
            assert identity == ("gateway_audit_test", role)
    return writer, reader  # Return only verified test connection settings.


def request():  # Return independent supported request data.
    return {
        "tool": "fixture_store",
        "operation": "READ_ITEM",
        "arguments": {"item_id": 42},
    }


def stored(reader, policy_id):  # Inspect committed data through another connection.
    with REAL_CONNECT(reader, connect_timeout=5, row_factory=dict_row) as connection:
        policies = connection.execute(
            "SELECT * FROM gateway_audit.policy_decisions WHERE event_id = %s",
            (policy_id,),
        ).fetchall()
        bindings = connection.execute(
            "SELECT * FROM gateway_audit.request_bindings WHERE policy_event_id = %s",
            (policy_id,),
        ).fetchall()
        events = connection.execute(
            "SELECT * FROM gateway_audit.execution_events "
            "WHERE policy_event_id = %s ORDER BY status",
            (policy_id,),
        ).fetchall()
    return policies, bindings, events  # Return independently observed saved state.


def test_real_prerequisites_are_visible_before_simulator_invocation(
    database, monkeypatch
):
    writer, reader = database
    actual_simulator = (
        module.simulate_read_item
    )  # Preserve the genuine fixed simulator.
    actual_event = module._event  # Observe generated correlation without replacing SQL.
    policy_ids = []  # Track the internally generated policy identifier.
    invocations = []  # Verify exactly one call with the captured item.

    def build_event(snapshot, policy_id, execution_id, status, reason):
        policy_ids.append(policy_id)  # Observe identity before the start is saved.
        return actual_event(snapshot, policy_id, execution_id, status, reason)

    def simulate(item_id):  # Inspect real committed prerequisites before running.
        policies, bindings, events = stored(reader, policy_ids[0])
        assert len(policies) == len(bindings) == len(events) == 1
        assert policies[0]["decision"] == "ALLOW"
        assert policies[0]["execution_status"] == "not_executed"
        assert bindings[0]["item_id"] == item_id == 42
        assert events[0]["status"] == "started"  # No terminal event exists yet.
        invocations.append(item_id)
        return actual_simulator(item_id)  # Run the actual pure simulator once.

    monkeypatch.setattr(module, "_event", build_event)
    monkeypatch.setattr(module, "simulate_read_item", simulate)
    candidate = request()
    original = deepcopy(candidate)
    result = module.create_simulator_executor(writer)(
        candidate, principal_id="reliability-reader"
    )
    assert result["status"] == "completed"
    assert result["result"] == {"item_id": 42, "label": "Synthetic item 42"}
    assert invocations == [42] and candidate == original
    policies, bindings, events = stored(reader, result["policy_event_id"])
    assert len(policies) == len(bindings) == 1
    assert [row["status"] for row in events] == ["completed", "started"]
    assert all(row["execution_id"] == result["execution_id"] for row in events)


def test_real_denial_saves_policy_only(database, monkeypatch):
    writer, reader = database

    def deny(*args, **kwargs):  # Exercise denial without changing production policy.
        return {
            "decision": "DENY",
            "reason_code": "NOT_AUTHORIZED",
            "policy_version": "policy-core-v1",
            "execution_status": "not_executed",
        }

    def forbidden(item_id):  # Fail the test if denial reaches the simulator.
        pytest.fail("Denied request reached simulator")

    monkeypatch.setattr(module, "evaluate_request", deny)
    monkeypatch.setattr(module, "simulate_read_item", forbidden)
    result = module.create_simulator_executor(writer)(
        request(), principal_id="reliability-reader"
    )
    assert result["status"] == "denied" and result["result"] is None
    policies, bindings, events = stored(reader, result["policy_event_id"])
    assert len(policies) == 1 and policies[0]["decision"] == "DENY"
    assert bindings == events == []  # No binding, start, or final event was created.


@pytest.mark.parametrize("failure", ["exception", "invalid_result"])
def test_real_failed_outcome_is_saved_without_answer(database, monkeypatch, failure):
    writer, reader = database
    invocations = []

    def simulate(item_id):  # Inject a tool-boundary failure, keeping real SQL.
        invocations.append(item_id)
        if failure == "exception":
            raise RuntimeError("private simulator details")
        return {"item_id": 43, "label": "Synthetic item 43"}  # Wrong requested item.

    monkeypatch.setattr(module, "simulate_read_item", simulate)
    result = module.create_simulator_executor(writer)(
        request(), principal_id="reliability-reader"
    )
    reason = "SIMULATOR_ERROR" if failure == "exception" else "INVALID_RESULT"
    assert result["status"] == "failed" and result["reason_code"] == reason
    assert result["invocation_status"] == "invoked" and result["result"] is None
    assert invocations == [42]
    _, _, events = stored(reader, result["policy_event_id"])
    assert [row["status"] for row in events] == ["failed", "started"]
    assert events[0]["reason_code"] == reason


def test_read_only_credentials_prevent_invocation(database, monkeypatch):
    _, reader = database

    def forbidden(item_id):  # Database denial must stop execution first.
        pytest.fail("Persistence failure reached simulator")

    monkeypatch.setattr(module, "simulate_read_item", forbidden)
    result = module.create_simulator_executor(reader)(
        request(), principal_id="reliability-reader"
    )
    assert result["reason_code"] == "PRE_EXECUTION_AUDIT_ERROR"
    assert result["invocation_status"] == "not_invoked"
    assert stored(reader, result["policy_event_id"]) == ([], [], [])


def test_new_requests_repeat_real_persistence_with_fresh_identities(database):
    writer, reader = database
    execute = module.create_simulator_executor(writer)
    first = execute(request(), principal_id="reliability-reader")
    second = execute(request(), principal_id="reliability-reader")
    assert first["status"] == second["status"] == "completed"
    assert first["execution_id"] != second["execution_id"]
    assert first["policy_event_id"] != second["policy_event_id"]
    for result in (first, second):  # Inspect both separately saved attempts.
        policies, bindings, events = stored(reader, result["policy_event_id"])
        assert len(policies) == len(bindings) == 1 and len(events) == 2


def test_existing_policy_identity_cannot_trigger_another_invocation(
    database, monkeypatch
):
    writer, reader = database
    execute = module.create_simulator_executor(writer)
    first = execute(request(), principal_id="reliability-reader")
    assert first["status"] == "completed"
    original = stored(reader, first["policy_event_id"])

    def forbidden(item_id):  # Strict duplicate rejection must precede invocation.
        pytest.fail("Duplicate policy identity reached simulator")

    monkeypatch.setattr(module, "simulate_read_item", forbidden)
    monkeypatch.setattr(module, "uuid4", lambda: UUID(first["policy_event_id"]))
    second = execute(request(), principal_id="reliability-reader")
    assert second["reason_code"] == "PRE_EXECUTION_AUDIT_ERROR"
    assert second["invocation_status"] == "not_invoked"
    assert stored(reader, first["policy_event_id"]) == original


class OrchestrationFaultConnection:  # Keep real SQL while injecting delivery faults.
    def __init__(self, connection, target, failure):
        self.connection = connection  # Retain the actual PostgreSQL connection.
        self.target = target  # Select pair, start, or terminal delivery.
        self.failure = failure  # Select insertion, acknowledgement, or close failure.
        self.stage = None  # Identify the transaction from its INSERT statements.
        self.committed = False  # Distinguish pre-commit cleanup from saved data.

    @property
    def isolation_level(self):  # Expose the real isolation setting.
        return self.connection.isolation_level

    @isolation_level.setter
    def isolation_level(self, value):  # Apply receiver settings to the real driver.
        self.connection.isolation_level = value

    def execute(self, statement, params=None):  # Forward real parameterized SQL.
        binding_insert = "INSERT INTO gateway_audit.request_bindings" in statement
        execution_insert = "INSERT INTO gateway_audit.execution_events" in statement
        if "INSERT INTO gateway_audit.policy_decisions" in statement:
            self.stage = "pair"  # The allowed policy is the pair's first insert.
        elif execution_insert:
            self.stage = "start" if params[9] == "started" else "terminal"
        if (  # Deny insertion at the selected transaction's final INSERT.
            self.failure == "insert"
            and self.stage == self.target
            and (binding_insert or execution_insert)
        ):
            self.connection.execute("SET LOCAL ROLE gateway_audit_reader")
        return self.connection.execute(
            statement, params
        )  # Exercise actual permissions.

    def commit(self):  # Commit real data before optionally losing acknowledgement.
        self.connection.commit()
        self.committed = True
        if self.stage == self.target and self.failure == "commit_ack":
            raise psycopg.OperationalError("synthetic lost acknowledgement")

    def rollback(self):  # Preserve the receiver's actual cleanup attempt.
        self.connection.rollback()  # Cannot undo an already completed commit.

    def close(self):  # Release resources even when reporting a close failure.
        self.connection.close()
        if self.committed and self.stage == self.target and self.failure == "close":
            raise RuntimeError("synthetic close failure")


@pytest.mark.parametrize("target", ["pair", "start", "terminal"])
@pytest.mark.parametrize("failure", ["insert", "commit_ack", "close"])
def test_real_audit_failure_stops_at_correct_invocation_boundary(
    database, monkeypatch, target, failure
):
    writer, reader = database
    connections = []  # Detect unexpected retries and leaked connections.
    invocations = []  # Establish whether this call entered the simulator.
    actual_simulator = module.simulate_read_item

    def connect(*args, **kwargs):  # Wrap each genuine receiver connection.
        connection = REAL_CONNECT(*args, **kwargs)
        connections.append(connection)
        return OrchestrationFaultConnection(connection, target, failure)

    def simulate(item_id):  # Count calls while retaining real simulator behavior.
        invocations.append(item_id)
        return actual_simulator(item_id)

    with monkeypatch.context() as patch:  # Restore database access before inspection.
        patch.setattr(psycopg, "connect", connect)
        patch.setattr(module, "simulate_read_item", simulate)
        result = module.create_simulator_executor(writer)(
            request(), principal_id="reliability-reader"
        )

    expected_calls = {"pair": 1, "start": 2, "terminal": 3}[target]
    assert len(connections) == expected_calls  # Never reconnect or retry a delivery.
    assert all(connection.closed for connection in connections)
    assert result["status"] == "failed" and result["result"] is None
    if target == "terminal":  # Only this boundary follows simulator invocation.
        assert invocations == [42]
        assert result["reason_code"] == "OUTCOME_AUDIT_ERROR"
        assert result["invocation_status"] == "invoked"
    else:
        assert invocations == []  # Even lost pre-run acknowledgement blocks execution.
        assert result["reason_code"] == "PRE_EXECUTION_AUDIT_ERROR"
        assert result["invocation_status"] == "not_invoked"

    policies, bindings, events = stored(reader, result["policy_event_id"])
    if target == "pair" and failure == "insert":
        assert policies == bindings == events == []  # Roll back the first insert too.
    else:
        assert len(policies) == len(bindings) == 1  # The pair was committed.
        if target == "pair" or (target == "start" and failure == "insert"):
            assert events == []  # No start was committed.
        elif target == "start" or failure == "insert":
            assert [row["status"] for row in events] == ["started"]
        else:
            assert [row["status"] for row in events] == ["completed", "started"]


def test_backward_clock_does_not_force_terminal_acceptance(database, monkeypatch):
    writer, reader = database
    times = iter(  # Supply a final occurrence time earlier than the saved start.
        [
            "2026-10-16T10:00:00Z",
            "2026-10-16T10:01:00Z",
            "2026-10-16T10:00:59Z",
        ]
    )
    monkeypatch.setattr(module, "_now", lambda: next(times))
    result = module.create_simulator_executor(writer)(
        request(), principal_id="reliability-reader"
    )
    assert result["reason_code"] == "OUTCOME_AUDIT_ERROR"
    assert result["invocation_status"] == "invoked"
    assert result["result"] is None  # Do not return success after rejected recording.
    _, _, events = stored(reader, result["policy_event_id"])
    assert [row["status"] for row in events] == ["started"]
    assert events[0]["occurred_at"] == "2026-10-16T10:01:00Z"


@pytest.mark.parametrize(
    "item_id",
    [1, 42, 1000],  # Exercise the lower bound, middle, and upper bound.
    ids=["item_lower_bound", "item_middle", "item_upper_bound"],
)
def test_evidence_executor_returns_correlated_completion(
    database, monkeypatch, item_id
):
    from gateway import simulator_evidence as evidence_module  # Use real composition.
    from gateway.execution_evidence import validate_execution_evidence  # Check output.

    writer, reader = database  # Require verified isolated database roles.
    actual_simulator = module.simulate_read_item  # Retain the genuine fixed simulator.
    invocations = []  # Detect repeated execution.
    observations = []  # Establish observation after the simulator returned.

    def simulate(item_id):  # Count calls without replacing genuine simulator behavior.
        invocations.append(item_id)
        return actual_simulator(item_id)

    def observe():  # Supply a fixed observation claim after underlying execution.
        observations.append(list(invocations))
        return "2026-10-27T14:00:00Z"

    monkeypatch.setattr(module, "simulate_read_item", simulate)
    monkeypatch.setattr(evidence_module, "_now", observe)
    candidate = request()  # Build an independent supported request.
    candidate["arguments"]["item_id"] = item_id  # Select this scenario.
    original = deepcopy(candidate)
    evidence = evidence_module.create_simulator_evidence_executor(writer)(
        candidate, principal_id="reliability-reader"
    )
    assert evidence == validate_execution_evidence(evidence, expected_item_id=item_id)
    result = evidence["execution_result"]  # Inspect the underlying checked result.
    assert result["status"] == "completed"
    assert result["result"] == {
        "item_id": item_id,
        "label": f"Synthetic item {item_id}",
    }
    assert evidence["provenance"]["source_id"] == f"fixture_store:READ_ITEM:{item_id}"
    assert evidence["provenance"]["observed_at"] == "2026-10-27T14:00:00Z"
    assert invocations == [item_id] and observations == [
        [item_id]
    ]  # Execute once before packaging.
    assert candidate == original  # Preserve the supplied request.
    policies, bindings, events = stored(reader, result["policy_event_id"])
    assert len(policies) == len(bindings) == 1  # Require independently visible records.
    assert policies[0]["decision"] == "ALLOW"
    assert policies[0]["execution_status"] == "not_executed"
    assert bindings[0]["item_id"] == item_id
    assert [row["status"] for row in events] == ["completed", "started"]
    assert all(row["execution_id"] == result["execution_id"] for row in events)


def test_evidence_executor_preserves_real_denial(database, monkeypatch):
    from gateway import simulator_evidence as evidence_module  # Exercise the wrapper.

    writer, reader = database

    def deny(*args, **kwargs):  # Exercise denial through controlled internal policy.
        return {
            "decision": "DENY",
            "reason_code": "NOT_AUTHORIZED",
            "policy_version": "policy-core-v1",
            "execution_status": "not_executed",
        }

    def forbidden(*args, **kwargs):  # Denial must reach neither simulator nor clock.
        pytest.fail("Denied evidence request reached an unsupported stage")

    monkeypatch.setattr(module, "evaluate_request", deny)
    monkeypatch.setattr(module, "simulate_read_item", forbidden)
    monkeypatch.setattr(evidence_module, "_now", forbidden)
    evidence = evidence_module.create_simulator_evidence_executor(writer)(
        request(), principal_id="reliability-reader"
    )
    result = evidence["execution_result"]
    assert result["status"] == "denied" and result["invocation_status"] == "not_invoked"
    assert result["result"] is None and evidence["provenance"] is None
    policies, bindings, events = stored(reader, result["policy_event_id"])
    assert len(policies) == 1 and policies[0]["decision"] == "DENY"
    assert bindings == events == []  # Denial must create no execution records.


@pytest.mark.parametrize("failure", ["exception", "invalid_result"])
def test_evidence_executor_preserves_real_simulator_failure(
    database, monkeypatch, failure
):
    from gateway import simulator_evidence as evidence_module  # Keep real audit SQL.

    writer, reader = database
    invocations = []  # Detect retries at the simulator boundary.

    def simulate(item_id):  # Inject only the selected tool-boundary failure.
        invocations.append(item_id)
        if failure == "exception":
            raise RuntimeError("private simulator details")
        return {"item_id": 43, "label": "Synthetic item 43"}

    def forbidden():  # Unsuccessful outcomes must not read an observation time.
        pytest.fail("Failed outcome attempted a provenance clock read")

    monkeypatch.setattr(module, "simulate_read_item", simulate)
    monkeypatch.setattr(evidence_module, "_now", forbidden)
    evidence = evidence_module.create_simulator_evidence_executor(writer)(
        request(), principal_id="reliability-reader"
    )
    result = evidence["execution_result"]
    reason = "SIMULATOR_ERROR" if failure == "exception" else "INVALID_RESULT"
    assert result["status"] == "failed" and result["reason_code"] == reason
    assert result["invocation_status"] == "invoked"
    assert result["result"] is None and evidence["provenance"] is None
    assert invocations == [42]  # The wrapper must not repeat failed execution.
    _, _, events = stored(reader, result["policy_event_id"])
    assert [row["status"] for row in events] == ["failed", "started"]
    assert events[0]["reason_code"] == reason


def test_evidence_executor_read_only_database_prevents_invocation(
    database, monkeypatch
):
    from gateway import (
        simulator_evidence as evidence_module,
    )  # Use real permission checks.

    _, reader = database

    def forbidden(*args, **kwargs):  # Audit failure must stop before invocation.
        pytest.fail("Pre-execution failure reached simulator or provenance clock")

    monkeypatch.setattr(module, "simulate_read_item", forbidden)
    monkeypatch.setattr(evidence_module, "_now", forbidden)
    evidence = evidence_module.create_simulator_evidence_executor(reader)(
        request(), principal_id="reliability-reader"
    )
    result = evidence["execution_result"]
    assert result["reason_code"] == "PRE_EXECUTION_AUDIT_ERROR"
    assert result["invocation_status"] == "not_invoked"
    assert result["result"] is None and evidence["provenance"] is None
    assert stored(reader, result["policy_event_id"]) == ([], [], [])


@pytest.mark.parametrize("target", ["pair", "start", "terminal"])
def test_evidence_executor_preserves_lost_audit_acknowledgement(
    database, monkeypatch, target
):
    from gateway import simulator_evidence as evidence_module  # Exercise composition.

    writer, reader = database
    connections = []  # Verify delivery stops without reconnecting.
    invocations = []  # Establish actual invocation in this test.
    actual_simulator = module.simulate_read_item

    def connect(*args, **kwargs):  # Reuse real committed-data fault injection.
        connection = REAL_CONNECT(*args, **kwargs)
        connections.append(connection)
        return OrchestrationFaultConnection(connection, target, "commit_ack")

    def simulate(item_id):  # Count genuine simulator calls.
        invocations.append(item_id)
        return actual_simulator(item_id)

    def forbidden():  # Failed execution outcomes must have no provenance observation.
        pytest.fail("Audit failure reached provenance clock")

    with monkeypatch.context() as patch:  # Restore driver access before inspection.
        patch.setattr(psycopg, "connect", connect)
        patch.setattr(module, "simulate_read_item", simulate)
        patch.setattr(evidence_module, "_now", forbidden)
        evidence = evidence_module.create_simulator_evidence_executor(writer)(
            request(), principal_id="reliability-reader"
        )

    result = evidence["execution_result"]
    assert result["status"] == "failed" and result["result"] is None
    assert evidence["provenance"] is None
    assert len(connections) == {"pair": 1, "start": 2, "terminal": 3}[target]
    assert all(connection.closed for connection in connections)
    invoked = target == "terminal"  # Only terminal recording follows invocation.
    assert invocations == ([42] if invoked else [])
    assert result["invocation_status"] == ("invoked" if invoked else "not_invoked")
    assert result["reason_code"] == (
        "OUTCOME_AUDIT_ERROR" if invoked else "PRE_EXECUTION_AUDIT_ERROR"
    )
    policies, bindings, events = stored(reader, result["policy_event_id"])
    assert (
        len(policies) == len(bindings) == 1
    )  # Lost acknowledgement can follow commit.
    expected = {
        "pair": [],
        "start": ["started"],
        "terminal": ["completed", "started"],
    }
    assert [row["status"] for row in events] == expected[target]


@pytest.mark.parametrize("boundary", ["clock", "builder"])
def test_evidence_packaging_failure_leaves_completed_records(
    database, monkeypatch, boundary
):
    from gateway import simulator_evidence as evidence_module  # Test post-run failures.

    writer, reader = database
    invocations = []  # Establish no second execution after packaging fails.
    actual_simulator = module.simulate_read_item

    def simulate(item_id):  # Preserve genuine simulator behavior.
        invocations.append(item_id)
        return actual_simulator(item_id)

    def fail(*args, **kwargs):  # Fail only observation or envelope preparation.
        raise RuntimeError("private packaging details")

    monkeypatch.setattr(module, "simulate_read_item", simulate)
    target = "_now" if boundary == "clock" else "build_execution_evidence"
    monkeypatch.setattr(evidence_module, target, fail)
    with pytest.raises(evidence_module.SimulatorEvidenceError) as caught:
        evidence_module.create_simulator_evidence_executor(writer)(
            request(), principal_id="reliability-reader"
        )

    error = caught.value
    assert str(error) == "Simulator evidence preparation failed"
    assert error.__suppress_context__ and error.invocation_status == "invoked"
    assert set(vars(error)) == {"policy_event_id", "execution_id", "invocation_status"}
    assert invocations == [42]  # Packaging failure is not permission to rerun.
    policies, bindings, events = stored(reader, error.policy_event_id)
    assert (
        len(policies) == len(bindings) == 1
    )  # Already committed records remain saved.
    assert [row["status"] for row in events] == ["completed", "started"]
    assert all(row["execution_id"] == error.execution_id for row in events)
