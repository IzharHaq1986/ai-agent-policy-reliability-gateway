"""Explicit synthetic execution with required PostgreSQL audit delivery."""

import logging  # Emit supplementary diagnostics through application setup.
from datetime import UTC, datetime  # Generate UTC occurrence times internally.
from typing import Protocol  # Describe the callable's keyword-only identity input.
from uuid import UUID, uuid4  # Generate internal correlation and event identities.

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

_LOGGER = logging.getLogger(__name__)  # Leave configuration to the application.
_DIAGNOSTIC_LEVELS = {  # Permit only the recorded stage/outcome combinations.
    ("snapshot", "rejected"): logging.INFO,
    ("snapshot", "preparation_failed"): logging.WARNING,
    ("policy_preparation", "preparation_failed"): logging.WARNING,
    ("allow_preparation", "preparation_failed"): logging.WARNING,
    ("denial_audit", "denied"): logging.INFO,
    ("denial_audit", "audit_failed"): logging.WARNING,
    ("pair_audit", "audit_failed"): logging.WARNING,
    ("start_audit", "audit_failed"): logging.WARNING,
    ("invocation", "simulator_failed"): logging.WARNING,
    ("result_validation", "invalid_result"): logging.WARNING,
    ("terminal_audit", "audit_failed"): logging.WARNING,
    ("result", "completed"): logging.INFO,
}


def _diagnostic(  # Keep supplementary logging separate from required audit gates.
    stage: str,
    outcome: str,
    policy_event_id: object = None,
    execution_id: object = None,
) -> None:
    try:  # Ordinary diagnostic failures must not change execution behavior.
        level = _DIAGNOSTIC_LEVELS[(stage, outcome)]  # Reject unlisted messages.
        fields: dict[str, object] = {
            "gateway_stage": stage,  # Include only the fixed processing stage.
            "gateway_outcome": outcome,  # Include only the fixed outcome.
        }
        for key, value in (  # Never include requests, identities, or payloads.
            ("policy_event_id", policy_event_id),
            ("execution_id", execution_id),
        ):
            if type(value) is not str:  # Omit unavailable or unsupported values.
                continue
            try:
                parsed = UUID(value)  # Check supplied internal identifier format.
            except ValueError:  # Invalid identifiers are omitted, not logged.
                continue
            if parsed.version == 4 and str(parsed) == value:
                fields[key] = value  # Include canonical version-4 identifiers only.
        _LOGGER.log(level, "simulator_execution", extra=fields)  # No exception details.
    except Exception:  # No retry, fallback output, or replacement exception.
        pass


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
            _diagnostic("snapshot", "rejected")  # No caller data is logged.
            raise
        except Exception:  # Hide unexpected ordinary preparation details.
            _diagnostic("snapshot", "preparation_failed")
            raise SimulatorPreparationError("Simulator preparation failed") from None

        policy_event_id: str | None = None  # Unavailable until generated.
        execution_id: str | None = None  # Never use caller-selected identifiers.
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
            _diagnostic(
                "policy_preparation",
                "preparation_failed",
                policy_event_id,
                execution_id,
            )
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
                _diagnostic(
                    "denial_audit", "audit_failed", policy_event_id, execution_id
                )
                return outcome("failed", "PRE_EXECUTION_AUDIT_ERROR", "not_invoked")
            _diagnostic("denial_audit", "denied", policy_event_id, execution_id)
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
            _diagnostic(
                "allow_preparation", "preparation_failed", policy_event_id, execution_id
            )
            raise SimulatorPreparationError("Simulator preparation failed") from None

        audit_stage = "pair_audit"  # Distinguish the two existing audit gates.
        try:  # Require both acknowledged deliveries before calling the simulator.
            pair_acknowledgement = binding_receiver(policy_event, binding)
            if pair_acknowledgement is not None:
                raise RuntimeError("Unexpected audit acknowledgement")
            audit_stage = "start_audit"  # Pair delivery already acknowledged.
            start_acknowledgement = execution_receiver(started)
            if start_acknowledgement is not None:
                raise RuntimeError("Unexpected audit acknowledgement")
        except Exception:  # Do not retry or invoke after uncertain pre-run delivery.
            _diagnostic(audit_stage, "audit_failed", policy_event_id, execution_id)
            return outcome("failed", "PRE_EXECUTION_AUDIT_ERROR", "not_invoked")

        payload = None  # Failures must not expose a successful answer.
        try:  # Invoke exactly the fixed simulator, once, with the captured item.
            raw_result = simulate_read_item(snapshot.item_id)
        except Exception:  # Record ordinary invocation errors without their details.
            _diagnostic("invocation", "simulator_failed", policy_event_id, execution_id)
            terminal_status = "failed"
            terminal_reason = "SIMULATOR_ERROR"
        else:
            try:  # Treat the simulator's returned data as untrusted.
                payload = validate_fixture_result(
                    raw_result, expected_item_id=snapshot.item_id
                )
            except FixtureResultValidationError:  # Reject incorrect synthetic answers.
                _diagnostic(
                    "result_validation", "invalid_result", policy_event_id, execution_id
                )
                terminal_status = "failed"
                terminal_reason = "INVALID_RESULT"
            except (
                Exception
            ):  # Unexpected result-checking failures cannot report success.
                _diagnostic(
                    "result_validation", "invalid_result", policy_event_id, execution_id
                )
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
            _diagnostic("terminal_audit", "audit_failed", policy_event_id, execution_id)
            return outcome("failed", "OUTCOME_AUDIT_ERROR", "invoked")

        result = outcome(  # Construct checked data after final acknowledgement.
            terminal_status, terminal_reason, "invoked", payload
        )
        if result["status"] == "completed":  # Never log premature completion.
            _diagnostic("result", "completed", policy_event_id, execution_id)
        return result  # Preserve the existing caller-facing result.

    return execute  # Only an explicit call can begin processing.
