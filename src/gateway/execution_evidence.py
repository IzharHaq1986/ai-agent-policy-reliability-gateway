"""Pure composition of checked execution results and synthetic provenance."""

from typing import Literal, TypedDict  # Describe the three-field envelope.

from gateway.execution_result import (  # Reuse existing execution-result rules.
    ExecutionResult,
    ExecutionResultValidationError,
    validate_execution_result,
)
from gateway.fixture_provenance import (  # Reuse source and fingerprint checks.
    FixtureProvenance,
    FixtureProvenanceValidationError,
    build_fixture_provenance,
    validate_fixture_provenance,
)


class ExecutionEvidenceValidationError(ValueError):  # Identify envelope failures.
    """An envelope or its supplied context violates the evidence contract."""


class ExecutionEvidence(TypedDict):  # Describe the separate evidence representation.
    schema_version: Literal[1]  # Identify the supported envelope format.
    execution_result: ExecutionResult  # Preserve the existing result structure.
    provenance: FixtureProvenance | None  # Include metadata only for completion.


_KEYS = frozenset(ExecutionEvidence.__annotations__)  # Require exactly these fields.


def validate_execution_evidence(
    evidence: object,  # Receive an untrusted envelope.
    *,
    expected_item_id: object,  # Require separately supplied item context.
) -> ExecutionEvidence:
    """Return independent checked dictionaries without establishing activity."""
    if type(evidence) is not dict or evidence.keys() != _KEYS:
        raise ExecutionEvidenceValidationError("Invalid execution evidence")
    if (
        type(evidence["schema_version"]) is not int or evidence["schema_version"] != 1
    ):  # Reject booleans, subclasses, and unsupported versions.
        raise ExecutionEvidenceValidationError("Invalid execution evidence")

    try:  # Validate the nested result before using its status or answer.
        checked_result = validate_execution_result(
            evidence["execution_result"], expected_item_id=expected_item_id
        )  # Obtain a fresh result and completed-answer dictionary.
    except ExecutionResultValidationError:  # Hide nested validation details.
        raise ExecutionEvidenceValidationError("Invalid execution evidence") from None

    checked_provenance: FixtureProvenance | None = None  # Failures carry no metadata.
    if checked_result["status"] == "completed":  # Completion requires bound provenance.
        try:  # Check metadata against the independently validated answer.
            checked_provenance = validate_fixture_provenance(
                evidence["provenance"],
                result=checked_result["result"],  # Bind the exact completed answer.
                expected_item_id=expected_item_id,  # Reuse the same expected item.
            )
        except FixtureProvenanceValidationError:  # Normalize metadata failures.
            raise ExecutionEvidenceValidationError(
                "Invalid execution evidence"
            ) from None
    elif evidence["provenance"] is not None:  # Reject metadata on denial or failure.
        raise ExecutionEvidenceValidationError("Invalid execution evidence")

    return {
        "schema_version": 1,  # Return the supported envelope version.
        "execution_result": checked_result,  # Return the independent checked result.
        "provenance": checked_provenance,  # Return fresh metadata or explicit None.
    }


def build_execution_evidence(
    execution_result: object,  # Receive the existing execution-result record.
    *,
    expected_item_id: object,  # Require independently established item context.
    observed_at: object,  # Require a supplied time for completion and None otherwise.
) -> ExecutionEvidence:
    """Build an envelope without invocation, clock access, or persistence."""
    try:  # Check the result before selecting the metadata rule.
        checked_result = validate_execution_result(
            execution_result, expected_item_id=expected_item_id
        )  # Preserve existing outcome and answer validation.
    except ExecutionResultValidationError:  # Normalize nested result failures.
        raise ExecutionEvidenceValidationError("Invalid execution evidence") from None

    provenance: FixtureProvenance | None = None  # Non-completion has no provenance.
    if checked_result["status"] == "completed":  # Build metadata only for an answer.
        try:  # Reuse the fixed source labels, timestamp rules, and digest encoding.
            provenance = build_fixture_provenance(
                checked_result["result"],
                expected_item_id=expected_item_id,
                observed_at=observed_at,  # Validate the supplied observation claim.
            )
        except FixtureProvenanceValidationError:  # Hide supplied metadata details.
            raise ExecutionEvidenceValidationError(
                "Invalid execution evidence"
            ) from None
    elif observed_at is not None:  # Do not silently discard unsupported time claims.
        raise ExecutionEvidenceValidationError("Invalid execution evidence")

    return validate_execution_evidence(
        {
            "schema_version": 1,  # Supply the supported envelope format.
            "execution_result": checked_result,  # Preserve the validated outcome.
            "provenance": provenance,  # Attach metadata only to completion.
        },
        expected_item_id=expected_item_id,  # Apply the full shared envelope contract.
    )
