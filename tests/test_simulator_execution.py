"""Unit checks for fixed simulator orchestration and stopping rules."""

from copy import deepcopy  # Preserve independent expectations.

import pytest  # Supply controlled cases and expected failures.

from gateway import simulator_execution as module  # Exercise the real coordinator.
from gateway.execution_result import validate_execution_result  # Check public outputs.
from gateway.request_snapshot import (  # Preserve existing capture rejection.
    RequestSnapshotValidationError,
)

CONNINFO = "dbname=synthetic_audit user=synthetic_writer"  # Use non-secret setup.


def request():  # Return an independent supported request for each test.
    return {
        "tool": "fixture_store",
        "operation": "READ_ITEM",
        "arguments": {"item_id": 42},
    }


@pytest.fixture
def harness(monkeypatch):  # Replace fixed dependencies only inside these tests.
    state = {  # Expose observed behavior and selected fault boundaries.
        "calls": [],
        "configured": [],
        "records": [],
        "failure": None,
        "bad_ack": None,
        "denied": False,
        "raw_result": {"item_id": 42, "label": "Synthetic item 42"},
    }
    real_policy = module.evaluate_request  # Keep genuine policy behavior by default.

    def delivery(name):  # Configure a controlled stand-in for one fixed receiver.
        def configure(conninfo):  # Observe configuration without opening a database.
            state["configured"].append((name, conninfo))

            def receive(*records):  # Record exact data passed to the receiver.
                step = name
                if name == "execution":  # Distinguish start from final delivery.
                    step = "start" if records[0]["status"] == "started" else "terminal"
                state["calls"].append(step)
                state["records"].append((step, deepcopy(records)))
                if state["failure"] == step:  # Simulate ordinary delivery failure.
                    raise OSError("private delivery details")
                if state["bad_ack"] == step:  # A non-None return is not success.
                    return False
                return None  # Match the actual receivers' acknowledgement contract.

            return receive

        return configure

    def policy(candidate, *, principal_id):  # Observe the internally evaluated copy.
        state["calls"].append("policy")
        state["policy_input"] = deepcopy(candidate)
        state["principal"] = principal_id
        if state["failure"] == "policy":  # Stop before audit delivery or invocation.
            raise RuntimeError("private policy details")
        result = real_policy(candidate, principal_id=principal_id)
        if state[
            "denied"
        ]:  # Exercise a future denial without changing production policy.
            result["decision"] = "DENY"
            result["reason_code"] = "NOT_AUTHORIZED"
        return result

    def simulate(item_id):  # Observe invocation without using real tools.
        state["calls"].append("simulate")
        state["simulated_item"] = item_id
        if state["failure"] == "simulate":  # Exercise an ordinary invocation exception.
            raise RuntimeError("private simulator details")
        return deepcopy(
            state["raw_result"]
        )  # Return untrusted output for real validation.

    monkeypatch.setattr(
        module, "create_postgresql_audit_receiver", delivery("policy_audit")
    )
    monkeypatch.setattr(module, "create_postgresql_binding_receiver", delivery("pair"))
    monkeypatch.setattr(
        module, "create_postgresql_execution_receiver", delivery("execution")
    )
    monkeypatch.setattr(module, "evaluate_request", policy)
    monkeypatch.setattr(module, "simulate_read_item", simulate)
    return state  # Let each test inspect evidence and select one failure.


def run():  # Invoke the actual public composition interface.
    return module.create_simulator_executor(CONNINFO)(
        request(), principal_id="reliability-reader"
    )


@pytest.mark.parametrize("configuration", [None, True, 1, "", "  "])
def test_invalid_configuration_creates_no_receivers(harness, configuration):
    with pytest.raises(ValueError, match="^Invalid simulator database configuration$"):
        module.create_simulator_executor(configuration)
    assert harness["configured"] == []  # Fail before constructing dependencies.
    assert harness["calls"] == []


def test_factory_configures_receivers_without_processing(harness):
    module.create_simulator_executor(CONNINFO)  # Configure without invoking.
    assert harness["configured"] == [
        ("policy_audit", CONNINFO),
        ("pair", CONNINFO),
        ("execution", CONNINFO),
    ]
    assert harness["calls"] == []  # No policy, delivery, or simulator calls.


def test_success_uses_required_order_and_matching_records(harness):
    candidate = request()
    original = deepcopy(candidate)
    result = module.create_simulator_executor(CONNINFO)(
        candidate, principal_id="reliability-reader"
    )
    assert harness["calls"] == ["policy", "pair", "start", "simulate", "terminal"]
    assert harness["simulated_item"] == 42  # Invoke only the captured item.
    assert harness["policy_input"] == original
    assert harness["principal"] == "reliability-reader"
    assert candidate == original  # Preserve caller input.
    assert result == validate_execution_result(result, expected_item_id=42)
    assert (result["status"], result["reason_code"], result["invocation_status"]) == (
        "completed",
        "SIMULATION_COMPLETED",
        "invoked",
    )
    assert result["result"] == {"item_id": 42, "label": "Synthetic item 42"}
    policy, binding = harness["records"][0][1]  # Inspect the paired delivery.
    start = harness["records"][1][1][0]
    terminal = harness["records"][2][1][0]
    assert policy["execution_status"] == "not_executed"  # Preserve policy semantics.
    assert binding["item_id"] == 42
    assert policy["event_id"] == binding["policy_event_id"] == result["policy_event_id"]
    assert start["policy_event_id"] == terminal["policy_event_id"] == policy["event_id"]
    assert start["execution_id"] == terminal["execution_id"] == result["execution_id"]
    assert len({policy["event_id"], start["event_id"], terminal["event_id"]}) == 3
    assert start["status"] == "started" and terminal["status"] == "completed"


@pytest.mark.parametrize("candidate", [None, {}, True, {"principal_id": "reader"}])
def test_invalid_request_never_reaches_policy_or_delivery(harness, candidate):
    with pytest.raises(RequestSnapshotValidationError):
        module.create_simulator_executor(CONNINFO)(
            candidate, principal_id="reliability-reader"
        )
    assert harness["calls"] == []


@pytest.mark.parametrize("principal", [None, True, "other-reader", ""])
def test_unsupported_principal_never_reaches_policy(harness, principal):
    with pytest.raises(RequestSnapshotValidationError):
        module.create_simulator_executor(CONNINFO)(request(), principal_id=principal)
    assert harness["calls"] == []


def test_denial_saves_only_policy_and_never_invokes(harness):
    harness["denied"] = True  # Exercise the explicit denial path.
    result = run()
    assert harness["calls"] == ["policy", "policy_audit"]
    assert (result["status"], result["reason_code"], result["invocation_status"]) == (
        "denied",
        "POLICY_DENIED",
        "not_invoked",
    )
    assert result["result"] is None


@pytest.mark.parametrize("step", ["policy_audit", "pair", "start"])
@pytest.mark.parametrize("fault", ["failure", "bad_ack"])
def test_pre_execution_delivery_failure_prevents_invocation(harness, step, fault):
    harness["denied"] = step == "policy_audit"  # Reach the selected receiver.
    harness[fault] = step
    result = run()
    assert (
        "simulate" not in harness["calls"]
    )  # Never invoke after failed prerequisites.
    assert "terminal" not in harness["calls"]
    assert harness["calls"][-1] == step  # Do not continue to later delivery stages.
    assert (result["status"], result["reason_code"], result["invocation_status"]) == (
        "failed",
        "PRE_EXECUTION_AUDIT_ERROR",
        "not_invoked",
    )
    assert result["result"] is None


def test_policy_failure_is_generic_preparation_error(harness):
    harness["failure"] = "policy"
    with pytest.raises(
        module.SimulatorPreparationError, match="^Simulator preparation failed$"
    ):
        run()
    assert harness["calls"] == [
        "policy"
    ]  # Never audit or invoke after preparation fails.


@pytest.mark.parametrize("failure", ["simulate", "invalid_result"])
def test_invocation_failure_records_failed_outcome_without_payload(harness, failure):
    if failure == "simulate":  # Raise an ordinary error from the invocation.
        harness["failure"] = "simulate"
        expected_reason = "SIMULATOR_ERROR"
    else:  # Return a valid-looking answer for the wrong item.
        harness["raw_result"] = {"item_id": 43, "label": "Synthetic item 43"}
        expected_reason = "INVALID_RESULT"
    result = run()
    assert harness["calls"] == ["policy", "pair", "start", "simulate", "terminal"]
    assert result["reason_code"] == expected_reason
    assert result["status"] == "failed" and result["invocation_status"] == "invoked"
    assert result["result"] is None
    terminal = harness["records"][-1][1][0]
    assert terminal["status"] == "failed" and terminal["reason_code"] == expected_reason


@pytest.mark.parametrize("fault", ["failure", "bad_ack"])
def test_terminal_delivery_failure_withholds_successful_answer(harness, fault):
    harness[fault] = "terminal"
    result = run()
    assert harness["calls"].count("simulate") == 1  # Never repeat the invocation.
    assert harness["calls"].count("terminal") == 1  # Never retry final delivery.
    assert (result["status"], result["reason_code"], result["invocation_status"]) == (
        "failed",
        "OUTCOME_AUDIT_ERROR",
        "invoked",
    )
    assert result["result"] is None  # A correct answer is withheld after audit failure.


def test_repeated_requests_are_independent_new_attempts(harness):
    execute = module.create_simulator_executor(CONNINFO)
    first = execute(request(), principal_id="reliability-reader")
    second = execute(request(), principal_id="reliability-reader")
    assert first["policy_event_id"] != second["policy_event_id"]
    assert first["execution_id"] != second["execution_id"]
    assert first["result"] == second["result"]
    assert harness["calls"] == [
        "policy",
        "pair",
        "start",
        "simulate",
        "terminal",
        "policy",
        "pair",
        "start",
        "simulate",
        "terminal",
    ]  # Repeat every gate for each explicit new request.


def test_captured_item_survives_later_request_changes(harness, monkeypatch):
    candidate = request()  # Supply mutable caller-owned dictionaries.
    observed_policy = module.evaluate_request  # Retain the harness policy observer.

    def policy(policy_request, *, principal_id):
        decision = observed_policy(policy_request, principal_id=principal_id)
        candidate["arguments"]["item_id"] = 43  # Change the original after capture.
        policy_request["arguments"]["item_id"] = 99  # Change the policy copy too.
        return decision  # Neither edit may change the retained snapshot.

    monkeypatch.setattr(module, "evaluate_request", policy)
    result = module.create_simulator_executor(CONNINFO)(
        candidate, principal_id="reliability-reader"
    )
    assert candidate["arguments"]["item_id"] == 43  # Confirm the mutation occurred.
    assert harness["simulated_item"] == 42  # Execute the original captured item.
    assert harness["records"][0][1][1]["item_id"] == 42  # Persist that same item.
    assert result["result"] == {"item_id": 42, "label": "Synthetic item 42"}


@pytest.mark.parametrize(
    "dependency",
    [
        "capture_request_snapshot",
        "uuid4",
        "_now",
        "build_audit_event",
        "build_request_binding",
        "_event",
    ],
)
def test_preparation_dependency_failure_never_invokes(harness, monkeypatch, dependency):
    def fail(*args, **kwargs):  # Inject an ordinary internal preparation failure.
        raise RuntimeError("private preparation details")

    monkeypatch.setattr(module, dependency, fail)
    with pytest.raises(
        module.SimulatorPreparationError, match="^Simulator preparation failed$"
    ):
        run()
    assert "simulate" not in harness["calls"]
    assert not harness["records"]  # These failures precede delivery.


def test_invalid_internal_policy_result_cannot_authorize(harness, monkeypatch):
    def invalid_policy(*args, **kwargs):  # Model a broken policy dependency.
        return {"decision": "ALLOW"}  # Missing fields must prevent invocation.

    monkeypatch.setattr(module, "evaluate_request", invalid_policy)
    with pytest.raises(module.SimulatorPreparationError):
        run()
    assert harness["calls"] == []  # No receiver or simulator was reached.


def test_terminal_construction_failure_reports_invocation(harness, monkeypatch):
    real_event = module._event  # Preserve genuine start-event construction.

    def build(snapshot, policy_id, execution_id, status, reason):
        if status != "started":  # Fail only after the simulator returned.
            raise ValueError("private terminal construction details")
        return real_event(snapshot, policy_id, execution_id, status, reason)

    monkeypatch.setattr(module, "_event", build)
    result = run()
    assert harness["calls"] == ["policy", "pair", "start", "simulate"]
    assert result["reason_code"] == "OUTCOME_AUDIT_ERROR"
    assert result["invocation_status"] == "invoked"
    assert result["result"] is None  # Never release an unaudited answer.


@pytest.mark.parametrize("simulator_failed", [False, True])
def test_audit_failure_takes_precedence_over_invalid_or_failed_simulation(
    harness, monkeypatch, simulator_failed
):
    harness["failure"] = "terminal"  # Also fail the attempt's outcome delivery.
    if simulator_failed:

        def fail(item_id):  # Raise an ordinary simulator error.
            harness["calls"].append("simulate")
            raise RuntimeError("private simulator details")

        monkeypatch.setattr(module, "simulate_read_item", fail)
    else:
        harness["raw_result"] = None  # Return an invalid answer.
    result = run()
    assert result["reason_code"] == "OUTCOME_AUDIT_ERROR"
    assert result["invocation_status"] == "invoked"
    assert result["result"] is None
    assert harness["calls"].count("simulate") == 1
    assert harness["calls"].count("terminal") == 1


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("dependency", ["evaluate_request", "simulate_read_item"])
def test_interruption_propagates_without_automatic_retry(
    harness, monkeypatch, interruption, dependency
):
    attempts = []  # Count the exact interrupted dependency calls.

    def interrupt(*args, **kwargs):
        attempts.append(True)  # Record entry before raising the interruption.
        raise interruption()

    monkeypatch.setattr(module, dependency, interrupt)
    with pytest.raises(interruption):  # Do not convert interruption into a result.
        run()
    assert attempts == [True]  # Never retry the interrupted operation.
    assert "terminal" not in harness["calls"]
    if dependency == "evaluate_request":
        assert not harness["records"]  # No pre-run records were delivered.
    else:
        assert harness["calls"] == ["policy", "pair", "start"]
        assert harness["records"][-1][0] == "start"  # Outcome remains unresolved.


def test_unexpected_result_validation_failure_withholds_answer(harness, monkeypatch):
    def fail(*args, **kwargs):  # Model an unexpected ordinary checker failure.
        raise RuntimeError("private validation details")

    monkeypatch.setattr(module, "validate_fixture_result", fail)
    result = run()
    assert result["reason_code"] == "INVALID_RESULT"
    assert result["invocation_status"] == "invoked"
    assert result["result"] is None
    assert harness["records"][-1][1][0]["reason_code"] == "INVALID_RESULT"
