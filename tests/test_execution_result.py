"""Contract checks for pure execution-result construction and validation."""

from copy import deepcopy  # Check that validation preserves caller data.
from itertools import product  # Exercise every supported-value combination.

import pytest  # Supply cases and assert generic validation failures.

from gateway.execution_result import (  # Test the public component interface.
    ExecutionResultValidationError,
    build_execution_result,
    validate_execution_result,
)

ALLOWED = (  # Define expected rules independently of implementation constants.
    ("denied", "POLICY_DENIED", "not_invoked"),
    ("completed", "SIMULATION_COMPLETED", "invoked"),
    ("failed", "PRE_EXECUTION_AUDIT_ERROR", "not_invoked"),
    ("failed", "SIMULATOR_ERROR", "invoked"),
    ("failed", "INVALID_RESULT", "invoked"),
    ("failed", "OUTCOME_AUDIT_ERROR", "invoked"),
    ("failed", "EXECUTION_UNCERTAIN", "unknown"),
)
KEYS = (  # Define the exact public record fields independently.
    "schema_version",
    "status",
    "reason_code",
    "invocation_status",
    "policy_event_id",
    "execution_id",
    "result",
)


class IntegerSubclass(int):  # Exercise exact built-in integer restrictions.
    pass


class StringSubclass(str):  # Exercise exact built-in string restrictions.
    pass


class DictionarySubclass(dict):  # Exercise exact dictionary restrictions.
    pass


def record(combination=ALLOWED[1], item_id=42):
    status, reason, invocation = combination  # Unpack one expected valid case.
    return {  # Return fresh data for each test.
        "schema_version": 1,
        "status": status,
        "reason_code": reason,
        "invocation_status": invocation,
        "policy_event_id": "110e8400-e29b-41d4-a716-446655440000",
        "execution_id": "220e8400-e29b-41d4-a716-446655440000",
        "result": (
            {"item_id": item_id, "label": f"Synthetic item {item_id}"}
            if status == "completed"
            else None
        ),
    }


def reject(candidate, expected_item_id=42):
    original = deepcopy(candidate)  # Preserve the exact supplied candidate.
    with pytest.raises(
        ExecutionResultValidationError, match="^Invalid execution result$"
    ):
        validate_execution_result(candidate, expected_item_id=expected_item_id)
    assert candidate == original  # Invalid data must remain unchanged too.


@pytest.mark.parametrize("combination", ALLOWED)
def test_allowed_combinations_pass_both_interfaces(combination):
    candidate = record(combination)  # Build independent expected data.
    original = deepcopy(candidate)
    checked = validate_execution_result(candidate, expected_item_id=42)
    arguments = dict(candidate)  # Supply only the builder's documented fields.
    del arguments["schema_version"]  # The builder supplies its own version.
    built = build_execution_result(**arguments, expected_item_id=42)
    assert checked == built == original  # Require identical public behavior.
    assert set(checked) == set(KEYS)  # Require exactly the seven fields.
    assert type(checked) is dict
    assert type(checked["schema_version"]) is int
    for key in KEYS[1:-1]:  # Require ordinary text for every text field.
        assert type(checked[key]) is str
    assert checked is not candidate and built is not candidate
    checked["status"] = "changed"  # Caller edits must not affect other copies.
    assert candidate == original and built == original


@pytest.mark.parametrize("item_id", [1, 2, 42, 500, 999, 1000])
def test_completed_payload_is_exact_and_independent(item_id):
    candidate = record(item_id=item_id)
    original = deepcopy(candidate)
    checked = validate_execution_result(candidate, expected_item_id=item_id)
    assert checked == original
    assert checked["result"] is not candidate["result"]  # Copy the nested answer.
    assert type(checked["result"]) is dict
    checked["result"]["label"] = "changed"  # Do not retain caller dictionaries.
    assert candidate == original
    candidate["result"]["item_id"] = 99  # Input edits must not change checked data.
    assert checked["result"]["item_id"] == item_id


@pytest.mark.parametrize(  # Reject all unlisted combinations of known values.
    "combination",
    [
        values
        for values in product(
            ("denied", "completed", "failed"),
            tuple(entry[1] for entry in ALLOWED),
            ("not_invoked", "invoked", "unknown"),
        )
        if values not in ALLOWED
    ],
)
def test_unlisted_combinations_are_rejected(combination):
    reject(record(combination))


@pytest.mark.parametrize("key", KEYS)
def test_missing_fields_are_rejected(key):
    candidate = record()
    del candidate[key]  # Remove one required field.
    reject(candidate)


@pytest.mark.parametrize("key", ["credentials", "request", "exception", "timestamp"])
def test_extra_fields_are_rejected(key):
    candidate = record()
    candidate[key] = "private"  # Extra details must not enter the public record.
    reject(candidate)


@pytest.mark.parametrize(
    "candidate", [None, True, 42, "result", [], DictionarySubclass()]
)
def test_invalid_containers_are_rejected(candidate):
    reject(candidate)


def test_populated_dictionary_subclass_is_rejected():
    reject(DictionarySubclass(record()))  # Matching fields do not relax exact types.


@pytest.mark.parametrize("value", [True, False, 0, 2, 1.0, "1", IntegerSubclass(1)])
def test_invalid_versions_are_rejected(value):
    candidate = record()
    candidate["schema_version"] = value
    reject(candidate)


@pytest.mark.parametrize("key", KEYS[1:-1])
@pytest.mark.parametrize("value", [None, True, 42, [], {}])
def test_non_string_text_fields_are_rejected(key, value):
    candidate = record()
    candidate[key] = value
    reject(candidate)


@pytest.mark.parametrize("key", KEYS[1:-1])
def test_string_subclasses_are_rejected(key):
    candidate = record()
    candidate[key] = StringSubclass(candidate[key])
    reject(candidate)


@pytest.mark.parametrize("key", ["status", "reason_code", "invocation_status"])
def test_unknown_text_values_are_rejected(key):
    candidate = record()
    candidate[key] = "UNSUPPORTED"
    reject(candidate)


@pytest.mark.parametrize("key", ["policy_event_id", "execution_id"])
@pytest.mark.parametrize(
    "identifier",
    [
        "",
        "not-a-uuid",
        "110E8400-e29b-41d4-a716-446655440000",
        "110e8400e29b41d4a716446655440000",
        "{110e8400-e29b-41d4-a716-446655440000}",
        "110e8400-e29b-11d4-a716-446655440000",
        "110e8400-e29b-41d4-0716-446655440000",
        "110e8400-e29b-41d4-a716-446655440000\n",
    ],
)
def test_invalid_identifiers_are_rejected(key, identifier):
    candidate = record()
    candidate[key] = identifier
    reject(candidate)


@pytest.mark.parametrize("combination", ALLOWED)
@pytest.mark.parametrize(
    "expected", [True, False, None, "42", 42.0, IntegerSubclass(42), 0, -1, 1001]
)
def test_invalid_expected_items_fail_for_every_outcome(combination, expected):
    reject(record(combination), expected_item_id=expected)


@pytest.mark.parametrize(
    "combination", [entry for entry in ALLOWED if entry[0] != "completed"]
)
@pytest.mark.parametrize(
    "payload", [{}, {"item_id": 42, "label": "Synthetic item 42"}, False]
)
def test_non_completed_outcomes_cannot_contain_payloads(combination, payload):
    candidate = record(combination)
    candidate["result"] = payload
    reject(candidate)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"item_id": 43, "label": "Synthetic item 43"},
        {"item_id": True, "label": "Synthetic item 42"},
        {"item_id": IntegerSubclass(42), "label": "Synthetic item 42"},
        {"item_id": 42, "label": StringSubclass("Synthetic item 42")},
        {"item_id": 42, "label": "Synthetic item 042"},
        {"item_id": 42, "label": "Synthetic item 42", "extra": "private"},
        DictionarySubclass(item_id=42, label="Synthetic item 42"),
    ],
)
def test_invalid_completed_payloads_raise_normalized_error(payload):
    candidate = record()
    candidate["result"] = payload
    reject(candidate)


def test_builder_reuses_validation_and_generic_error():
    candidate = record()
    del candidate["schema_version"]  # Supply the builder's normal argument shape.
    candidate["result"] = {"item_id": 43, "label": "Synthetic item 43"}
    original = deepcopy(candidate)
    with pytest.raises(
        ExecutionResultValidationError, match="^Invalid execution result$"
    ):
        build_execution_result(**candidate, expected_item_id=42)
    assert candidate == original


def test_builder_nested_result_is_independent():
    arguments = record()
    del arguments["schema_version"]
    built = build_execution_result(**arguments, expected_item_id=42)
    built["result"]["label"] = "changed"  # Verify the builder also copies payloads.
    assert arguments["result"]["label"] == "Synthetic item 42"


def test_function_argument_mistakes_retain_type_error():
    with pytest.raises(TypeError):  # Missing required context is a Python call error.
        validate_execution_result(record())
    with pytest.raises(TypeError):  # Missing builder arguments remain call errors.
        build_execution_result()
    with pytest.raises(TypeError):  # Unexpected keywords remain call errors.
        validate_execution_result(record(), expected_item_id=42, unexpected=True)
