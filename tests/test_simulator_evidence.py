"""Unit checks for audited simulator evidence composition."""

from copy import deepcopy  # Preserve independent expectations.

import psycopg  # Block accidental database access in unit tests.
import pytest  # Supply controlled failures and boundary cases.

from gateway import simulator_evidence as module  # Exercise the real composition.
from gateway.execution_evidence import validate_execution_evidence  # Check outputs.
from gateway.request_snapshot import RequestSnapshotValidationError  # Preserve errors.
from gateway.simulator_execution import create_simulator_executor  # Check real setup.

CONNINFO = "dbname=synthetic_audit user=synthetic_writer"  # Use test-only settings.
STAMP = "2026-10-27T14:00:00Z"  # Supply a controlled observation time.
NON_COMPLETED = [
    ("denied", "POLICY_DENIED", "not_invoked"),
    ("failed", "PRE_EXECUTION_AUDIT_ERROR", "not_invoked"),
    ("failed", "SIMULATOR_ERROR", "invoked"),
    ("failed", "INVALID_RESULT", "invoked"),
    ("failed", "OUTCOME_AUDIT_ERROR", "invoked"),
    ("failed", "EXECUTION_UNCERTAIN", "unknown"),
]  # Exercise every existing unsuccessful outcome.


def request():  # Return an independent supported request.
    return {
        "tool": "fixture_store",
        "operation": "READ_ITEM",
        "arguments": {"item_id": 42},
    }


def result(combination=None):  # Construct returned execution claims independently.
    status, reason, invocation = combination or (
        "completed",
        "SIMULATION_COMPLETED",
        "invoked",
    )
    return {
        "schema_version": 1,
        "status": status,
        "reason_code": reason,
        "invocation_status": invocation,
        "policy_event_id": "11111111-1111-4111-8111-111111111111",
        "execution_id": "22222222-2222-4222-8222-222222222222",
        "result": (
            {"item_id": 42, "label": "Synthetic item 42"}
            if status == "completed"
            else None
        ),  # Failures and denials must contain no answer.
    }


@pytest.fixture
def harness(monkeypatch):  # Replace only the underlying executor inside these tests.
    state = {
        "configured": [],
        "calls": [],
        "returned": result(),
        "failure": None,
        "mutate": None,
        "clocks": 0,
    }  # Observe configuration, one invocation, and packaging boundaries.

    def configure(conninfo):  # Observe factory setup without processing.
        state["configured"].append(conninfo)

        def execute(candidate, *, principal_id):  # Stand in for the existing executor.
            state["calls"].append((deepcopy(candidate), principal_id))
            if state["mutate"] is not None:  # Exercise mutation after wrapper capture.
                state["mutate"](candidate)
            if state["failure"] is not None:  # Inject an underlying execution error.
                raise state["failure"]
            return state["returned"]  # Let the real wrapper validate returned claims.

        return execute

    def now():  # Observe whether packaging reads the clock.
        state["clocks"] += 1
        return STAMP

    def forbidden(*args, **kwargs):  # Prevent accidental database access.
        pytest.fail("Unit tests must not connect to PostgreSQL")

    monkeypatch.setattr(module, "create_simulator_executor", configure)
    monkeypatch.setattr(module, "_now", now)
    monkeypatch.setattr(psycopg, "connect", forbidden)
    return state


def run():  # Exercise the public wrapper interface.
    return module.create_simulator_evidence_executor(CONNINFO)(
        request(), principal_id="reliability-reader"
    )


def test_factory_only_configures(harness):  # Do not execute during factory creation.
    module.create_simulator_evidence_executor(CONNINFO)
    assert harness["configured"] == [CONNINFO]
    assert harness["calls"] == [] and harness["clocks"] == 0


def test_real_factory_creation_does_not_connect(harness, monkeypatch):
    monkeypatch.setattr(module, "create_simulator_executor", create_simulator_executor)
    module.create_simulator_evidence_executor(CONNINFO)  # Connections remain blocked.
    assert harness["calls"] == [] and harness["clocks"] == 0


@pytest.mark.parametrize("configuration", [None, True, 1, "", " "])
def test_real_configuration_errors_are_preserved(harness, monkeypatch, configuration):
    monkeypatch.setattr(module, "create_simulator_executor", create_simulator_executor)
    with pytest.raises(ValueError, match="^Invalid simulator database configuration$"):
        module.create_simulator_evidence_executor(configuration)


def test_completed_output_is_checked_and_independent(harness):
    original = deepcopy(harness["returned"])  # Preserve the underlying result.
    evidence = run()
    assert evidence == validate_execution_evidence(evidence, expected_item_id=42)
    assert evidence["execution_result"] == original
    assert evidence["provenance"]["observed_at"] == STAMP
    assert harness["calls"] == [(request(), "reliability-reader")]
    assert harness["clocks"] == 1
    evidence["execution_result"]["result"]["label"] = "changed"
    assert harness["returned"] == original  # Returned payloads must not share inputs.


@pytest.mark.parametrize("combination", NON_COMPLETED)
def test_unsuccessful_results_have_no_provenance_or_clock_read(harness, combination):
    harness["returned"] = result(combination)
    evidence = run()
    assert evidence["execution_result"] == result(combination)
    assert evidence["provenance"] is None
    assert harness["clocks"] == 0 and len(harness["calls"]) == 1


@pytest.mark.parametrize("candidate", [None, {}, True, {"principal_id": "reader"}])
def test_invalid_request_stops_before_execution(harness, candidate):
    execute = module.create_simulator_evidence_executor(CONNINFO)
    with pytest.raises(RequestSnapshotValidationError):
        execute(candidate, principal_id="reliability-reader")
    assert harness["calls"] == [] and harness["clocks"] == 0


def test_snapshot_failure_is_generic_and_precedes_execution(harness, monkeypatch):
    def fail(*args, **kwargs):  # Simulate unexpected capture failure.
        raise RuntimeError("private capture details")

    monkeypatch.setattr(module, "capture_request_snapshot", fail)
    with pytest.raises(
        module.SimulatorPreparationError, match="^Simulator preparation failed$"
    ) as caught:
        run()
    assert caught.value.__suppress_context__
    assert harness["calls"] == []


def test_caller_and_forwarded_mutation_cannot_change_expected_item(harness):
    candidate = request()  # Capture item 42 before either dictionary is changed.

    def mutate(forwarded):  # Change both caller and forwarded dictionaries afterward.
        assert forwarded is not candidate
        assert forwarded["arguments"] is not candidate["arguments"]
        candidate["arguments"]["item_id"] = 43
        forwarded["arguments"]["item_id"] = 44

    harness["mutate"] = mutate
    evidence = module.create_simulator_evidence_executor(CONNINFO)(
        candidate, principal_id="reliability-reader"
    )
    assert evidence["execution_result"]["result"]["item_id"] == 42
    assert evidence["provenance"]["source_id"] == "fixture_store:READ_ITEM:42"
    assert len(harness["calls"]) == 1


@pytest.mark.parametrize("failure", [RuntimeError("private"), KeyboardInterrupt()])
def test_underlying_exceptions_propagate_unchanged(harness, failure):
    harness["failure"] = failure
    with pytest.raises(type(failure)) as caught:
        run()
    assert caught.value is failure
    assert len(harness["calls"]) == 1 and harness["clocks"] == 0


@pytest.mark.parametrize("boundary", ["clock", "builder"])
def test_packaging_failure_withholds_answer_and_preserves_context(
    harness, monkeypatch, boundary
):
    def fail(*args, **kwargs):  # Fail only after execution returned successfully.
        raise RuntimeError("private packaging details")

    target = "_now" if boundary == "clock" else "build_execution_evidence"
    monkeypatch.setattr(module, target, fail)
    with pytest.raises(module.SimulatorEvidenceError) as caught:
        run()
    error = caught.value
    assert str(error) == "Simulator evidence preparation failed"
    assert error.__suppress_context__ and error.__cause__ is None
    assert vars(error) == {
        "policy_event_id": harness["returned"]["policy_event_id"],
        "execution_id": harness["returned"]["execution_id"],
        "invocation_status": "invoked",
    }  # Expose safe context only, never the successful answer.
    assert len(harness["calls"]) == 1  # Never execute again after packaging failure.


@pytest.mark.parametrize("returned", [None, {}, {"execution_id": "private"}])
def test_invalid_returned_claims_provide_no_trusted_context(harness, returned):
    harness["returned"] = returned
    with pytest.raises(module.SimulatorEvidenceError) as caught:
        run()
    assert vars(caught.value) == {
        "policy_event_id": None,
        "execution_id": None,
        "invocation_status": "unknown",
    }
    assert caught.value.__suppress_context__
    assert len(harness["calls"]) == 1 and harness["clocks"] == 0


@pytest.mark.parametrize("combination", NON_COMPLETED)
def test_unsuccessful_packaging_error_preserves_invocation_status(
    harness, monkeypatch, combination
):
    harness["returned"] = result(combination)

    def fail(*args, **kwargs):  # Exercise packaging failure for every valid outcome.
        raise ValueError("private envelope details")

    monkeypatch.setattr(module, "build_execution_evidence", fail)
    with pytest.raises(module.SimulatorEvidenceError) as caught:
        run()
    assert caught.value.invocation_status == combination[2]
    assert caught.value.execution_id == harness["returned"]["execution_id"]
    assert len(harness["calls"]) == 1 and harness["clocks"] == 0


def test_packaging_interruption_propagates(harness, monkeypatch):
    interruption = KeyboardInterrupt()  # Ordinary-error handling must not catch this.

    def stop():  # Interrupt observation after underlying execution.
        raise interruption

    monkeypatch.setattr(module, "_now", stop)
    with pytest.raises(KeyboardInterrupt) as caught:
        run()
    assert caught.value is interruption
    assert len(harness["calls"]) == 1
