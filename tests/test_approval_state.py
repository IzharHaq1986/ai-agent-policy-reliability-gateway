"""Check every known approval state pair and reject malformed states."""

import pytest

from gateway.approval_state import (
    ApprovalStateValidationError,
    is_approval_transition_allowed,
)

# Read each row in this destination order.
STATES = ("pending", "approved", "rejected", "expired", "revoked", "consumed")
ROWS = (
    "NYYYYN",  # pending
    "NNNYYY",  # approved
    "NNNNNN",  # rejected
    "NNNNNN",  # expired
    "NNNNNN",  # revoked
    "NNNNNN",  # consumed
)


class StateString(str):
    """A string subclass must not count as a plain state label."""


@pytest.mark.parametrize(
    ("current", "proposed", "expected"),
    [
        (current, proposed, answer == "Y")
        for current, row in zip(STATES, ROWS, strict=True)
        for proposed, answer in zip(STATES, row, strict=True)
    ],
)
def test_known_state_pairs(current, proposed, expected):
    assert is_approval_transition_allowed(current, proposed) is expected


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        True,
        1,
        [],
        {},
        b"pending",
        "",
        "unknown",
        "Pending",
        "pending ",
        StateString("pending"),
    ],
)
@pytest.mark.parametrize("position", ["current", "next"])
def test_invalid_state_is_rejected(invalid, position):
    # A forbidden move must still report a malformed input in either position.
    current, proposed = (
        (invalid, "pending") if position == "current" else ("consumed", invalid)
    )
    with pytest.raises(ApprovalStateValidationError, match="^Invalid approval state$"):
        is_approval_transition_allowed(current, proposed)
