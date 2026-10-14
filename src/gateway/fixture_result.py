"""Pure validation of synthetic fixture results against an expected item."""

from gateway.fixture_simulator import FixtureResult


class FixtureResultValidationError(ValueError):
    """A returned result or expected identifier violates the contract."""


def validate_fixture_result(
    result: object,
    *,
    expected_item_id: object,
) -> FixtureResult:
    """Return a fresh checked answer without proving execution or permission."""
    if type(expected_item_id) is not int or not 1 <= expected_item_id <= 1000:
        raise FixtureResultValidationError("Invalid fixture result")

    if type(result) is not dict or result.keys() != {"item_id", "label"}:
        raise FixtureResultValidationError("Invalid fixture result")

    item_id = result["item_id"]
    label = result["label"]
    if (
        type(item_id) is not int
        or item_id != expected_item_id
        or type(label) is not str
        or label != f"Synthetic item {expected_item_id}"
    ):
        raise FixtureResultValidationError("Invalid fixture result")

    return {
        "item_id": item_id,
        "label": label,
    }
