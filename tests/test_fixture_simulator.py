"""Contract tests for the pure synthetic fixture simulator."""

import pytest

from gateway.fixture_simulator import (
    FixtureValidationError,
    simulate_read_item,
)


@pytest.mark.parametrize("item_id", [1, 2, 500, 999, 1000])
def test_valid_identifiers_return_exact_synthetic_data(item_id):
    result = simulate_read_item(item_id)

    assert result == {
        "item_id": item_id,
        "label": f"Synthetic item {item_id}",
    }
    assert type(result) is dict
    assert type(result["item_id"]) is int
    assert type(result["label"]) is str


class IntegerSubclass(int):
    pass


@pytest.mark.parametrize(
    "item_id",
    [
        True,
        False,
        None,
        "1",
        1.0,
        b"1",
        [],
        {},
        (1,),
        IntegerSubclass(1),
        -1,
        0,
        1001,
        10**100,
    ],
)
def test_invalid_identifiers_raise_generic_validation_error(item_id):
    with pytest.raises(
        FixtureValidationError,
        match="^Invalid item identifier$",
    ):
        simulate_read_item(item_id)


def test_repeated_results_are_equal_and_independent():
    first = simulate_read_item(42)
    second = simulate_read_item(42)

    assert first == second
    assert first is not second

    first["item_id"] = 99
    first["label"] = "Changed by caller"

    expected = {"item_id": 42, "label": "Synthetic item 42"}
    assert second == expected
    assert simulate_read_item(42) == expected
