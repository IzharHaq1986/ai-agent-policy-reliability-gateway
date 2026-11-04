"""Check exact matching against separately supplied request context."""

import pytest

from gateway.approval_request import (
    ApprovalRequestValidationError,
    validate_approval_request,
    validate_approval_request_match,
)
from gateway.request_snapshot import RequestSnapshot

REQUEST_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"


def candidate():
    """Build a fresh candidate independently of the expected snapshot."""
    return {
        "schema_version": 1,
        "request_id": REQUEST_ID,
        "principal_id": "reliability-reader",
        "tool": "fixture_store",
        "operation": "READ_ITEM",
        "item_id": 42,
    }


def expected_snapshot():
    """Represent the original request captured by the caller."""
    return RequestSnapshot("reliability-reader", "fixture_store", "READ_ITEM", 42)


def test_exact_match_returns_an_independent_record():
    record = candidate()
    checked = validate_approval_request_match(
        record, expected_request_id=REQUEST_ID, expected_snapshot=expected_snapshot()
    )

    assert checked == record == candidate()
    assert checked is not record
    checked["item_id"] = 43
    assert record == candidate()


def test_matching_id_cannot_hide_a_different_valid_item():
    record = {**candidate(), "item_id": 43}
    original = record.copy()

    # The existing validator accepts item 43; exact matching must reject it.
    assert validate_approval_request(record, expected_request_id=REQUEST_ID) == original
    with pytest.raises(
        ApprovalRequestValidationError, match="^Invalid approval request$"
    ):
        validate_approval_request_match(
            record,
            expected_request_id=REQUEST_ID,
            expected_snapshot=expected_snapshot(),
        )
    assert record == original


def test_identical_details_cannot_hide_a_different_request_id():
    with pytest.raises(
        ApprovalRequestValidationError, match="^Invalid approval request$"
    ):
        validate_approval_request_match(
            candidate(),
            expected_request_id=OTHER_ID,
            expected_snapshot=expected_snapshot(),
        )


@pytest.mark.parametrize(
    "changes", [{"item_id": True}, {"schema_version": 2}, {"approved": True}]
)
def test_malformed_candidate_is_rejected(changes):
    with pytest.raises(
        ApprovalRequestValidationError, match="^Invalid approval request$"
    ):
        validate_approval_request_match(
            {**candidate(), **changes},
            expected_request_id=REQUEST_ID,
            expected_snapshot=expected_snapshot(),
        )


@pytest.mark.parametrize("invalid_id", [None, "not-a-uuid"])
def test_invalid_expected_id_is_rejected(invalid_id):
    with pytest.raises(
        ApprovalRequestValidationError, match="^Invalid approval request$"
    ):
        validate_approval_request_match(
            candidate(),
            expected_request_id=invalid_id,
            expected_snapshot=expected_snapshot(),
        )


class SnapshotSubclass(RequestSnapshot):
    """A subclass is not the exact snapshot type required by the helper."""


@pytest.mark.parametrize(
    "invalid_snapshot",
    [
        None,
        candidate(),
        SnapshotSubclass("reliability-reader", "fixture_store", "READ_ITEM", 42),
    ],
)
def test_invalid_expected_snapshot_is_rejected(invalid_snapshot):
    with pytest.raises(
        ApprovalRequestValidationError, match="^Invalid approval request$"
    ):
        validate_approval_request_match(
            candidate(),
            expected_request_id=REQUEST_ID,
            expected_snapshot=invalid_snapshot,
        )
