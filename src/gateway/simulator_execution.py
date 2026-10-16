"""Explicit synthetic execution with required PostgreSQL audit delivery."""

from datetime import UTC, datetime  # Generate UTC occurrence times internally.
from typing import Protocol  # Describe the callable's keyword-only identity input.
from uuid import uuid4  # Generate internal correlation and event identities.

from gateway.audit import build_audit_event  # Validate internal policy decisions.
from gateway.execution_audit import (  # Reuse the execution-event contract.
    ExecutionEvent,
    build_execution_event,
)
from gateway.execution_result import (  # Reuse the caller-facing result contract.
    ExecutionResult,
    build_execution_result,
)
from gateway.fixture_result import (  # Check the exact returned synthetic answer.
    FixtureResultValidationError,
    validate_fixture_result,
)
from gateway.fixture_simulator import simulate_read_item  # Use only this fixed tool.
from gateway.policy import evaluate_request  # Evaluate policy inside this boundary.
from gateway.postgresql_audit_receiver import (  # Save denied policy decisions.
    create_postgresql_audit_receiver,
)
from gateway.postgresql_binding_receiver import (  # Save allowed request pairs.
    create_postgresql_binding_receiver,
)
from gateway.postgresql_execution_receiver import (  # Save start and final records.
    create_postgresql_execution_receiver,
)
from gateway.request_binding import build_request_binding  # Bind the captured item.
from gateway.request_snapshot import (  # Retain one fixed request throughout execution.
    RequestSnapshot,
    RequestSnapshotValidationError,
    capture_request_snapshot,
)


class SimulatorPreparationError(ValueError):  # Identify failed internal preparation.
    """Execution could not be safely prepared."""


class SimulatorExecutor(Protocol):  # Preserve the callable's named identity argument.
    def __call__(
        self,
        request: object,  # Receive untrusted structured request data.
        *,
        principal_id: object,  # Require identity from trusted application context.
    ) -> ExecutionResult:
        """Execute only the supported synthetic request."""


def _now() -> str:  # Generate the existing second-resolution UTC text format.
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event(  # Build an execution record from fixed captured context.
    snapshot: RequestSnapshot,
    policy_event_id: str,
    execution_id: str,
    status: object,
    reason_code: object,
) -> ExecutionEvent:
    return build_execution_event(  # Reuse validation and generate no caller identity.
        event_id=str(uuid4()),  # Give each audit record its own identity.
        execution_id=execution_id,  # Correlate one attempt's records.
        policy_event_id=policy_event_id,  # Reference its exact decision and binding.
        occurred_at=_now(),  # Record the current time without adjusting clock drift.
        principal_id=snapshot.principal_id,
        tool=snapshot.tool,
        operation=snapshot.operation,
        status=status,
        reason_code=reason_code,
    )


def create_simulator_executor(  # Configure explicit opt-in Python composition.
    conninfo: str,  # Accept database configuration only from trusted setup.
) -> SimulatorExecutor:
    """Create an executor without connecting or changing API startup."""
    if type(conninfo) is not str or not conninfo.strip():  # Reject invalid setup.
        raise ValueError("Invalid simulator database configuration")

    policy_receiver = create_postgresql_audit_receiver(conninfo)  # No connection yet.
    binding_receiver = create_postgresql_binding_receiver(conninfo)  # Fixed delivery.
    execution_receiver = create_postgresql_execution_receiver(
        conninfo
    )  # Fixed delivery.

    def execute(
        request: object,
        *,
        principal_id: object,
    ) -> ExecutionResult:
        try:  # Capture once before generating records or contacting PostgreSQL.
            snapshot = capture_request_snapshot(request, principal_id=principal_id)
        except RequestSnapshotValidationError:  # Preserve documented input rejection.
            raise
        except Exception:  # Hide unexpected ordinary preparation details.
            raise SimulatorPreparationError("Simulator preparation failed") from None

        try:  # Generate identities and evaluate policy using captured values only.
            policy_event_id = str(uuid4())
            execution_id = str(uuid4())
            decision = evaluate_request(
                snapshot.to_policy_request(), principal_id=snapshot.principal_id
            )
            policy_event = (
                build_audit_event(  # Validate the internally obtained decision.
                    decision,
                    principal_id=snapshot.principal_id,
                    event_id=policy_event_id,
                    occurred_at=_now(),
                )
            )
        except Exception:  # Missing or invalid preparation must prevent invocation.
            raise SimulatorPreparationError("Simulator preparation failed") from None

        def outcome(  # Build every returned result using the shared checked contract.
            status: object,
            reason_code: object,
            invocation_status: object,
            payload: object = None,
        ) -> ExecutionResult:
            return build_execution_result(
                status=status,
                reason_code=reason_code,
                invocation_status=invocation_status,
                policy_event_id=policy_event_id,
                execution_id=execution_id,
                result=payload,
                expected_item_id=snapshot.item_id,  # Never reread caller dictionaries.
            )

        if (
            policy_event["decision"] == "DENY"
        ):  # Denial never creates an execution start.
            try:
                acknowledgement = policy_receiver(policy_event)
                if acknowledgement is not None:  # Require the documented success value.
                    raise RuntimeError("Unexpected audit acknowledgement")
            except (
                Exception
            ):  # Saved state may be uncertain; invocation is still absent.
                return outcome("failed", "PRE_EXECUTION_AUDIT_ERROR", "not_invoked")
            return outcome("denied", "POLICY_DENIED", "not_invoked")

        try:  # Construct prerequisites before attempting delivery.
            binding = build_request_binding(
                policy_event_id=policy_event_id,
                principal_id=snapshot.principal_id,
                tool=snapshot.tool,
                operation=snapshot.operation,
                item_id=snapshot.item_id,
            )
            started = _event(
                snapshot, policy_event_id, execution_id, "started", "SIMULATION_STARTED"
            )
        except (
            Exception
        ):  # Construction errors are preparation failures, not DB failures.
            raise SimulatorPreparationError("Simulator preparation failed") from None

        try:  # Require both acknowledged deliveries before calling the simulator.
            pair_acknowledgement = binding_receiver(policy_event, binding)
            if pair_acknowledgement is not None:
                raise RuntimeError("Unexpected audit acknowledgement")
            start_acknowledgement = execution_receiver(started)
            if start_acknowledgement is not None:
                raise RuntimeError("Unexpected audit acknowledgement")
        except Exception:  # Do not retry or invoke after uncertain pre-run delivery.
            return outcome("failed", "PRE_EXECUTION_AUDIT_ERROR", "not_invoked")

        payload = None  # Failures must not expose a successful answer.
        try:  # Invoke exactly the fixed simulator, once, with the captured item.
            raw_result = simulate_read_item(snapshot.item_id)
        except Exception:  # Record ordinary invocation errors without their details.
            terminal_status = "failed"
            terminal_reason = "SIMULATOR_ERROR"
        else:
            try:  # Treat the simulator's returned data as untrusted.
                payload = validate_fixture_result(
                    raw_result, expected_item_id=snapshot.item_id
                )
            except FixtureResultValidationError:  # Reject incorrect synthetic answers.
                terminal_status = "failed"
                terminal_reason = "INVALID_RESULT"
            except (
                Exception
            ):  # Unexpected result-checking failures cannot report success.
                terminal_status = "failed"
                terminal_reason = "INVALID_RESULT"
            else:
                terminal_status = "completed"
                terminal_reason = "SIMULATION_COMPLETED"

        try:  # A terminal record is required even when the simulator failed.
            terminal = _event(
                snapshot,
                policy_event_id,
                execution_id,
                terminal_status,
                terminal_reason,
            )
            terminal_acknowledgement = execution_receiver(terminal)
            if terminal_acknowledgement is not None:
                raise RuntimeError("Unexpected audit acknowledgement")
        except Exception:  # Invocation occurred even when final recording failed.
            return outcome("failed", "OUTCOME_AUDIT_ERROR", "invoked")

        return outcome(  # Release an answer only after required final acknowledgement.
            terminal_status, terminal_reason, "invoked", payload
        )

    return execute  # Only an explicit call can begin processing.
