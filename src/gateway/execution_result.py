"""Pure execution-result validation; no execution or authority established."""

from typing import Literal, TypedDict  # Describe the checked result fields.
from uuid import UUID  # Parse supplied identifiers without generating them.

from gateway.fixture_result import (  # Reuse exact synthetic-answer checking.
    FixtureResultValidationError,
    validate_fixture_result,
)
from gateway.fixture_simulator import FixtureResult  # Reuse the result type.


class ExecutionResultValidationError(ValueError):  # Identify contract failures.
    """An input does not satisfy the execution-result contract."""


class ExecutionResult(TypedDict):  # Describe the seven supported fields.
    schema_version: Literal[1]  # Identify the supported format.
    status: Literal["denied", "completed", "failed"]  # Describe the claimed outcome.
    reason_code: Literal[  # Limit explanations to the recorded contract.
        "POLICY_DENIED",
        "SIMULATION_COMPLETED",
        "PRE_EXECUTION_AUDIT_ERROR",
        "SIMULATOR_ERROR",
        "INVALID_RESULT",
        "OUTCOME_AUDIT_ERROR",
        "EXECUTION_UNCERTAIN",
    ]
    invocation_status: Literal["not_invoked", "invoked", "unknown"]  # Track certainty.
    policy_event_id: str  # Identify the claimed policy event.
    execution_id: str  # Identify the claimed attempt.
    result: FixtureResult | None  # Include an answer only for completion.


_RESULT_KEYS = frozenset(ExecutionResult.__annotations__)  # Require exact fields.
_STRING_KEYS = (  # Require ordinary strings before comparing or parsing values.
    "status",
    "reason_code",
    "invocation_status",
    "policy_event_id",
    "execution_id",
)
_ALLOWED_COMBINATIONS = frozenset(  # Define every permitted outcome explicitly.
    {
        ("denied", "POLICY_DENIED", "not_invoked"),
        ("completed", "SIMULATION_COMPLETED", "invoked"),
        ("failed", "PRE_EXECUTION_AUDIT_ERROR", "not_invoked"),
        ("failed", "SIMULATOR_ERROR", "invoked"),
        ("failed", "INVALID_RESULT", "invoked"),
        ("failed", "OUTCOME_AUDIT_ERROR", "invoked"),
        ("failed", "EXECUTION_UNCERTAIN", "unknown"),
    }
)


def validate_execution_result(  # Check untrusted claims without proving them.
    result: object,  # Receive the complete candidate record.
    *,
    expected_item_id: object,  # Receive the separately established item identity.
) -> ExecutionResult:
    """Return independent checked data without establishing execution facts."""
    if type(expected_item_id) is not int or not 1 <= expected_item_id <= 1000:
        raise ExecutionResultValidationError("Invalid execution result")

    if type(result) is not dict or result.keys() != _RESULT_KEYS:
        raise ExecutionResultValidationError("Invalid execution result")
    if type(result["schema_version"]) is not int or result["schema_version"] != 1:
        raise ExecutionResultValidationError("Invalid execution result")
    if any(type(result[key]) is not str for key in _STRING_KEYS):
        raise ExecutionResultValidationError("Invalid execution result")

    combination = (  # Check the relationship between the three outcome claims.
        result["status"],
        result["reason_code"],
        result["invocation_status"],
    )
    if combination not in _ALLOWED_COMBINATIONS:  # Reject every unlisted case.
        raise ExecutionResultValidationError("Invalid execution result")

    for key in ("policy_event_id", "execution_id"):  # Validate both supplied links.
        identifier = result[key]  # String types were checked before parsing.
        try:
            parsed = UUID(identifier)  # Parse without generating new identity.
        except ValueError:  # Hide malformed input details behind a generic error.
            raise ExecutionResultValidationError("Invalid execution result") from None
        if parsed.version != 4 or str(parsed) != identifier:
            raise ExecutionResultValidationError("Invalid execution result")

    checked_payload: FixtureResult | None = None  # Failures carry no answer.
    if result["status"] == "completed":  # Completion requires the exact answer.
        try:
            checked_payload = validate_fixture_result(  # Return a fresh inner copy.
                result["result"], expected_item_id=expected_item_id
            )
        except FixtureResultValidationError:  # Normalize the component boundary.
            raise ExecutionResultValidationError("Invalid execution result") from None
    elif result["result"] is not None:  # Never expose a payload on a failure or denial.
        raise ExecutionResultValidationError("Invalid execution result")

    return {  # Return a fresh outer record with independently checked payload.
        "schema_version": 1,
        "status": result["status"],
        "reason_code": result["reason_code"],
        "invocation_status": result["invocation_status"],
        "policy_event_id": result["policy_event_id"],
        "execution_id": result["execution_id"],
        "result": checked_payload,
    }


def build_execution_result(  # Build from explicit context using the same checks.
    *,
    status: object,  # Receive the claimed outcome.
    reason_code: object,  # Receive its explanation.
    invocation_status: object,  # Receive the claimed invocation certainty.
    policy_event_id: object,  # Receive the claimed policy reference.
    execution_id: object,  # Receive the claimed attempt identity.
    result: object,  # Receive an answer or explicit None.
    expected_item_id: object,  # Receive the expected synthetic item.
) -> ExecutionResult:
    """Build checked data without I/O, generated identity, or simulator calls."""
    return validate_execution_result(  # Keep runtime rules in one validator.
        {
            "schema_version": 1,  # Supply the only supported format.
            "status": status,
            "reason_code": reason_code,
            "invocation_status": invocation_status,
            "policy_event_id": policy_event_id,
            "execution_id": execution_id,
            "result": result,
        },
        expected_item_id=expected_item_id,  # Check against separately supplied context.
    )
