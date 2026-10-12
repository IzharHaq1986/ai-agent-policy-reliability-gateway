"""Pure synthetic fixture data; no authorization or tool dispatch."""

from typing import TypedDict


class FixtureValidationError(ValueError):
    """The item identifier does not satisfy the fixture contract."""


class FixtureResult(TypedDict):
    item_id: int
    label: str


def simulate_read_item(item_id: object) -> FixtureResult:
    """Return fresh deterministic synthetic data without external access."""
    if type(item_id) is not int or not 1 <= item_id <= 1000:
        raise FixtureValidationError("Invalid item identifier")

    return {
        "item_id": item_id,
        "label": f"Synthetic item {item_id}",
    }
