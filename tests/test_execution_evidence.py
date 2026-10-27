"""Contract checks for execution-result and provenance composition."""

from copy import deepcopy  # Preserve independent input expectations.
from hashlib import sha256  # Fingerprint explicit expected answer bytes.

import pytest  # Supply outcome combinations and rejection checks.

from gateway.execution_evidence import (  # Exercise only the public envelope API.
    ExecutionEvidenceValidationError,
    build_execution_evidence,
    validate_execution_evidence,
)

STAMP = "2026-10-27T12:00:00Z"  # Use a supplied fixed observation claim.
NON_COMPLETED = [
    ("denied", "POLICY_DENIED", "not_invoked"),
    ("failed", "PRE_EXECUTION_AUDIT_ERROR", "not_invoked"),
    ("failed", "SIMULATOR_ERROR", "invoked"),
    ("failed", "INVALID_RESULT", "invoked"),
    ("failed", "OUTCOME_AUDIT_ERROR", "invoked"),
    ("failed", "EXECUTION_UNCERTAIN", "unknown"),
]  # Cover every supported outcome that must carry no answer or provenance.


class IntegerSubclass(int):  # Check strict envelope version types.
    pass


class DictionarySubclass(dict):  # Check strict envelope container types.
    pass


def execution(combination=None, item_id=42):  # Construct independent result fixtures.
    status, reason, invocation = combination or (
        "completed",
        "SIMULATION_COMPLETED",
        "invoked",
    )  # Select completion unless a failure or denial is requested.
    return {
        "schema_version": 1,  # Use the existing result format.
        "status": status,  # Supply the claimed outcome.
        "reason_code": reason,  # Supply its matching reason.
        "invocation_status": invocation,  # Preserve invocation certainty.
        "policy_event_id": "11111111-1111-4111-8111-111111111111",
        "execution_id": "22222222-2222-4222-8222-222222222222",
        "result": (
            {"item_id": item_id, "label": f"Synthetic item {item_id}"}
            if status == "completed"
            else None
        ),  # Include an answer only for completion.
    }


def provenance(item_id=42):  # Construct metadata without production builders.
    encoded = (
        f'{{"item_id":{item_id},"label":"Synthetic item {item_id}"}}'
    ).encode()  # Specify the expected canonical UTF-8 bytes independently.
    return {
        "schema_version": 1,  # Select the provenance format.
        "source_type": "synthetic_fixture",  # Identify synthetic data.
        "source_id": f"fixture_store:READ_ITEM:{item_id}",  # Match the expected item.
        "source_version": "synthetic-fixture-v1",  # Supply the supported label.
        "observed_at": STAMP,  # Supply the fixed time claim.
        "result_sha256": sha256(encoded).hexdigest(),  # Bind the expected answer.
        "permitted_use": "synthetic_demonstration_only",  # Describe intended use.
    }


def envelope(combination=None, item_id=42):  # Build independent expected packages.
    return {
        "schema_version": 1,  # Select the envelope format.
        "execution_result": execution(combination, item_id),  # Preserve result shape.
        "provenance": provenance(item_id) if combination is None else None,
    }  # Keep unsuccessful outcomes free of provenance.


def assert_rejected(candidate, expected_item_id=42):  # Check safe direct rejection.
    original = deepcopy(candidate)  # Preserve every nested dictionary.
    with pytest.raises(
        ExecutionEvidenceValidationError, match="^Invalid execution evidence$"
    ) as caught:
        validate_execution_evidence(candidate, expected_item_id=expected_item_id)
    assert caught.value.__suppress_context__ or caught.value.__context__ is None
    assert candidate == original  # Rejected input must remain unchanged.


@pytest.mark.parametrize("item_id", [1, 42, 1000])
def test_completed_envelope_matches_independent_expectation(item_id):
    supplied = execution(item_id=item_id)  # Construct a valid completed result.
    original = deepcopy(supplied)  # Preserve the caller-owned result.
    expected = envelope(item_id=item_id)  # Construct the independent expectation.
    built = build_execution_evidence(
        supplied, expected_item_id=item_id, observed_at=STAMP
    )
    assert built == expected  # Check every envelope and nested field.
    checked = validate_execution_evidence(expected, expected_item_id=item_id)
    assert (
        checked == expected and checked is not expected
    )  # Require a fresh outer copy.
    assert checked["execution_result"] is not expected["execution_result"]
    assert (
        checked["execution_result"]["result"]
        is not expected["execution_result"]["result"]
    )
    assert checked["provenance"] is not expected["provenance"]  # Copy metadata too.
    checked["execution_result"]["result"]["label"] = (
        "changed"  # Mutate returned answer.
    )
    checked["provenance"]["source_id"] = "changed"  # Mutate returned metadata.
    checked["execution_result"]["execution_id"] = "changed"  # Mutate returned result.
    built["execution_result"]["result"]["item_id"] = 0  # Check builder independence.
    assert expected == envelope(item_id=item_id)  # Preserve original envelope.
    assert supplied == original  # Preserve the supplied execution result.


@pytest.mark.parametrize("combination", NON_COMPLETED)
def test_all_unsuccessful_outcomes_have_no_provenance(combination):
    supplied = execution(combination)  # Exercise one existing supported outcome.
    original = deepcopy(supplied)  # Preserve the caller-owned dictionary.
    built = build_execution_evidence(supplied, expected_item_id=42, observed_at=None)
    assert built == envelope(combination)  # Preserve status, reason, and invocation.
    assert built["execution_result"] is not supplied  # Return an independent result.
    assert built["provenance"] is None  # Never attach successful-answer metadata.
    assert validate_execution_evidence(built, expected_item_id=42) == built
    assert supplied == original  # Preserve input contents.


@pytest.mark.parametrize("combination", NON_COMPLETED)
def test_unsuccessful_outcomes_reject_provenance(combination):
    candidate = envelope(combination)  # Begin with a valid unsuccessful outcome.
    candidate["provenance"] = provenance()  # Add unsupported answer metadata.
    assert_rejected(candidate)


@pytest.mark.parametrize("combination", NON_COMPLETED)
@pytest.mark.parametrize("stamp", [STAMP, False, ""])
def test_builder_rejects_observation_time_without_completion(combination, stamp):
    with pytest.raises(
        ExecutionEvidenceValidationError, match="^Invalid execution evidence$"
    ):
        build_execution_evidence(
            execution(combination), expected_item_id=42, observed_at=stamp
        )  # Require explicit None instead of silently discarding the supplied value.


@pytest.mark.parametrize("value", [None, {}, True, "metadata"])
def test_completion_requires_valid_provenance(value):
    candidate = envelope()  # Begin with valid completion.
    candidate["provenance"] = value  # Remove or invalidate its metadata.
    assert_rejected(candidate)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_id", "fixture_store:READ_ITEM:43"),
        ("result_sha256", "0" * 64),
        ("observed_at", "2026-02-29T00:00:00Z"),
        ("permitted_use", "operational_control"),
    ],
)
def test_nested_metadata_must_match_answer(field, value):
    candidate = envelope()  # Begin with consistent nested records.
    candidate["provenance"][field] = value  # Break one existing provenance rule.
    assert_rejected(candidate)


def test_another_items_metadata_is_rejected():  # Bind both records to one item.
    candidate = envelope()  # Complete item 42.
    candidate["provenance"] = provenance(
        43
    )  # Supply internally valid item-43 metadata.
    assert_rejected(candidate)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution_id", "private-invalid-id"),
        ("status", "unknown"),
        ("result", {"item_id": 43, "label": "Synthetic item 43"}),
    ],
)
def test_invalid_nested_execution_is_rejected(field, value):
    candidate = envelope()  # Begin with valid completion.
    candidate["execution_result"][field] = value  # Break a nested result rule.
    assert_rejected(candidate)
    with pytest.raises(
        ExecutionEvidenceValidationError, match="^Invalid execution evidence$"
    ):
        build_execution_evidence(
            candidate["execution_result"], expected_item_id=42, observed_at=STAMP
        )  # Normalize builder validation failures too.


@pytest.mark.parametrize("field", ["schema_version", "execution_result", "provenance"])
def test_missing_fields_are_rejected(field):
    candidate = envelope()  # Begin with the complete record.
    del candidate[field]  # Remove exactly one required field.
    assert_rejected(candidate)


def test_extra_fields_are_rejected():
    candidate = envelope()  # Begin with the supported field set.
    candidate["credentials"] = "private"  # Add undeclared content.
    assert_rejected(candidate)


@pytest.mark.parametrize("value", [True, False, 0, 2, 1.0, "1", IntegerSubclass(1)])
def test_invalid_envelope_version_is_rejected(value):
    candidate = envelope()  # Begin with valid nested data.
    candidate["schema_version"] = value  # Change the envelope's version only.
    assert_rejected(candidate)


@pytest.mark.parametrize(
    "candidate", [None, [], True, "record", DictionarySubclass(envelope())]
)
def test_invalid_envelope_container_is_rejected(candidate):
    assert_rejected(candidate)  # Require an ordinary built-in dictionary.


@pytest.mark.parametrize("expected", [True, None, "42", 42.0, 0, 1001])
def test_invalid_expected_item_is_rejected(expected):
    assert_rejected(envelope(), expected)  # Reuse existing expected-item validation.
    with pytest.raises(
        ExecutionEvidenceValidationError, match="^Invalid execution evidence$"
    ):
        build_execution_evidence(
            execution(), expected_item_id=expected, observed_at=STAMP
        )


@pytest.mark.parametrize("stamp", [None, True, "", "2026-04-31T00:00:00Z"])
def test_completed_builder_rejects_invalid_time(stamp):
    with pytest.raises(
        ExecutionEvidenceValidationError, match="^Invalid execution evidence$"
    ) as caught:
        build_execution_evidence(execution(), expected_item_id=42, observed_at=stamp)
    assert caught.value.__suppress_context__  # Suppress underlying metadata errors.


def test_failure_cannot_carry_an_answer():
    candidate = envelope(
        NON_COMPLETED[4]
    )  # Represent an invoked outcome-audit failure.
    candidate["execution_result"]["result"] = execution()["result"]  # Add an answer.
    assert_rejected(candidate)  # Do not release a withheld answer through the envelope.


def test_identical_content_does_not_bind_provenance_to_attempt():
    first = envelope()  # Construct one claimed completed attempt.
    second = deepcopy(first)  # Reuse the same synthetic answer and metadata.
    second["execution_result"]["execution_id"] = "33333333-3333-4333-8333-333333333333"
    assert validate_execution_evidence(first, expected_item_id=42) == first
    assert validate_execution_evidence(second, expected_item_id=42) == second
    assert (
        first["provenance"] == second["provenance"]
    )  # No cryptographic attempt binding.


def test_missing_builder_argument_retains_type_error():
    with pytest.raises(TypeError):
        build_execution_evidence(execution(), expected_item_id=42)  # Time is explicit.
