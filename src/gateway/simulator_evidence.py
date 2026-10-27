"""Explicit audited simulator composition returning checked evidence envelopes."""

from datetime import UTC, datetime  # Observe returned answers using local UTC time.
from typing import Literal, Protocol  # Describe callable and reconciliation context.

from gateway.execution_evidence import (  # Reuse the complete envelope contract.
    ExecutionEvidence,
    build_execution_evidence,
)
from gateway.execution_result import (  # Check returned claims before trusting context.
    ExecutionResult,
    validate_execution_result,
)
from gateway.request_snapshot import (  # Retain independent request values.
    RequestSnapshotValidationError,
    capture_request_snapshot,
)
from gateway.simulator_execution import (  # Preserve the existing execution boundary.
    SimulatorPreparationError,
    create_simulator_executor,
)


class SimulatorEvidenceError(RuntimeError):  # Distinguish packaging from execution.
    """Evidence preparation failed; execution must not be repeated blindly."""

    def __init__(
        self,
        *,
        policy_event_id: str | None = None,  # Supply only a checked policy reference.
        execution_id: str | None = None,  # Supply only a checked attempt reference.
        invocation_status: Literal["not_invoked", "invoked", "unknown"] = "unknown",
    ) -> None:
        super().__init__("Simulator evidence preparation failed")  # Hide details.
        self.policy_event_id = policy_event_id  # Retain safe reconciliation context.
        self.execution_id = execution_id  # Retain no answer or request payload.
        self.invocation_status = invocation_status  # Preserve established certainty.


class SimulatorEvidenceExecutor(Protocol):  # Preserve explicit trusted identity input.
    def __call__(
        self,
        request: object,  # Receive untrusted structured request data.
        *,
        principal_id: object,  # Require identity from trusted application context.
    ) -> ExecutionEvidence:
        """Execute the supported synthetic request and package its returned result."""


def _now() -> str:  # Observe when this composition receives the completed answer.
    return datetime.now(UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )  # Do not adjust clock drift.


def create_simulator_evidence_executor(
    conninfo: str,  # Accept database setup only from trusted application configuration.
) -> SimulatorEvidenceExecutor:
    """Configure composition without connecting or changing the original executor."""
    execute = create_simulator_executor(
        conninfo
    )  # Reuse configuration and audit gates.

    def execute_with_evidence(
        request: object,  # Capture input without retaining caller dictionaries.
        *,
        principal_id: object,  # Keep identity separate from request-body claims.
    ) -> ExecutionEvidence:
        try:  # Capture fixed supported values before invoking the underlying executor.
            snapshot = capture_request_snapshot(request, principal_id=principal_id)
        except RequestSnapshotValidationError:  # Preserve the existing input error.
            raise
        except Exception:  # Stop before execution on unexpected capture failures.
            raise SimulatorPreparationError("Simulator preparation failed") from None

        returned = execute(
            snapshot.to_policy_request(),  # Supply fresh captured request dictionaries.
            principal_id=snapshot.principal_id,  # Supply the same captured principal.
        )  # Call exactly once; underlying exceptions and interruptions propagate.

        checked: ExecutionResult | None = None  # No correlation context trusted yet.
        try:  # Keep post-return packaging failures separate from execution failures.
            checked = validate_execution_result(
                returned, expected_item_id=snapshot.item_id
            )  # Trust identifiers and invocation status only after full validation.
            observed_at = _now() if checked["status"] == "completed" else None
            # Observe only completed answers; unsuccessful outcomes require no clock.
            return build_execution_evidence(
                checked,  # Package the independently checked result.
                expected_item_id=snapshot.item_id,  # Never reread caller input.
                observed_at=observed_at,  # Record this component's observation time.
            )
        except Exception:  # Withhold answers on ordinary packaging failures.
            if (
                checked is None
            ):  # Invalid returned claims cannot supply trusted context.
                raise SimulatorEvidenceError() from None
            raise SimulatorEvidenceError(
                policy_event_id=checked["policy_event_id"],  # Preserve checked links.
                execution_id=checked["execution_id"],
                invocation_status=checked["invocation_status"],  # Do not claim no run.
            ) from None  # No payload, retry, fallback, or displayed exception chain.

    return execute_with_evidence  # Processing requires an explicit Python call.
