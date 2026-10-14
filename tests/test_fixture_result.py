"""Contract tests for synthetic fixture-result validation."""

from copy import deepcopy

import pytest

from gateway.fixture_result import (
    FixtureResultValidationError,
    validate_fixture_result,
)
from gateway.fixture_simulator import simulate_read_item


class IntegerSubclass(int):
    pass


class StringSubclass(str):
    pass


class DictionarySubclass(dict):
    pass


def answer(item_id=42):
    return {"item_id": item_id, "label": f"Synthetic item {item_id}"}


def assert_rejected(result, expected_item_id=42):
    original = deepcopy(result)
    with pytest.raises(FixtureResultValidationError, match="^Invalid fixture result$"):
        validate_fixture_result(result, expected_item_id=expected_item_id)
    assert result == original


@pytest.mark.parametrize("item_id", [1, 2, 42, 500, 999, 1000])
def test_exact_answers_are_accepted_without_mutation(item_id):
    result = answer(item_id)
    original = deepcopy(result)

    checked = validate_fixture_result(result, expected_item_id=item_id)

    assert checked == original
    assert result == original
    assert checked is not result
    assert type(checked) is dict
    assert type(checked["item_id"]) is int
    assert type(checked["label"]) is str

    checked["item_id"] = 0
    checked["label"] = "changed"
    assert result == original
    assert validate_fixture_result(result, expected_item_id=item_id) == original


@pytest.mark.parametrize("item_id", [1, 42, 1000])
def test_real_simulator_answers_pass_validation(item_id):
    result = simulate_read_item(item_id)
    checked = validate_fixture_result(result, expected_item_id=item_id)
    assert checked == answer(item_id)
    assert checked is not result


@pytest.mark.parametrize(
    "expected",
    [
        True,
        False,
        None,
        "42",
        42.0,
        b"42",
        [],
        {},
        IntegerSubclass(42),
        -1,
        0,
        1001,
        10**100,
    ],
)
def test_invalid_expected_identifiers_are_rejected(expected):
    assert_rejected(answer(), expected_item_id=expected)


@pytest.mark.parametrize(
    "result", [None, True, 42, "answer", [], DictionarySubclass(answer())]
)
def test_invalid_result_containers_are_rejected(result):
    assert_rejected(result)


@pytest.mark.parametrize("missing", ["item_id", "label"])
def test_missing_fields_are_rejected(missing):
    result = answer()
    del result[missing]
    assert_rejected(result)


@pytest.mark.parametrize("extra", ["credentials", "output", "metadata"])
def test_extra_fields_are_rejected(extra):
    result = answer()
    result[extra] = "private"
    assert_rejected(result)


@pytest.mark.parametrize(
    "item_id",
    [
        True,
        False,
        None,
        "42",
        42.0,
        [],
        {},
        IntegerSubclass(42),
        0,
        41,
        43,
        1001,
    ],
)
def test_wrong_or_invalid_returned_item_is_rejected(item_id):
    result = answer()
    result["item_id"] = item_id
    assert_rejected(result)


@pytest.mark.parametrize(
    "label",
    [
        None,
        True,
        42,
        [],
        {},
        StringSubclass("Synthetic item 42"),
        "",
        "Synthetic item 41",
        "synthetic item 42",
        "Synthetic item 042",
        "Synthetic item 42 ",
        "Synthetic item 42\n",
        "private exception details",
    ],
)
def test_wrong_or_invalid_label_is_rejected(label):
    result = answer()
    result["label"] = label
    assert_rejected(result)


def test_consistent_answer_for_a_different_item_is_rejected():
    assert_rejected(answer(43), expected_item_id=42)
