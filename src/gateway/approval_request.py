"""Pure descriptors for synthetic requests awaiting future approval."""

from typing import Literal, TypedDict  # Describe the checked descriptor.
from uuid import UUID  # Validate supplied identifiers without generating them.

from gateway.request_snapshot import (
    RequestSnapshot,
    RequestSnapshotValidationError,
    capture_request_snapshot,
)  # Reuse existing synthetic request checks.


class ApprovalRequestValidationError(ValueError):
    """A descriptor or its supplied context violates the contract."""


class ApprovalRequest(TypedDict):
    schema_version: Literal[1]  # Identify the supported descriptor format.
    request_id: str  # Refer to one claimed request instance.
    principal_id: str  # Preserve the separately supplied requester name.
    tool: str  # Preserve the supported synthetic tool.
    operation: str  # Preserve the supported read operation.
    item_id: int  # Preserve the exact requested item.


_KEYS = frozenset(ApprovalRequest.__annotations__)  # Require exactly these fields.


def _validate_identifier(value: object) -> str:
    """Check canonical UUIDv4 text without establishing its origin."""
    if type(value) is not str:  # Reject subclasses and non-text values.
        raise ApprovalRequestValidationError("Invalid approval request")
    try:
        parsed = UUID(value)  # Parse only the supplied text.
    except ValueError:
        raise ApprovalRequestValidationError("Invalid approval request") from None
    if parsed.version != 4 or str(parsed) != value:  # Require canonical UUIDv4.
        raise ApprovalRequestValidationError("Invalid approval request")
    return value  # Formatting does not establish uniqueness or authority.


def validate_approval_request(
    record: object,
    *,
    expected_request_id: object,
) -> ApprovalRequest:
    """Return a fresh checked descriptor without granting permission."""
    expected = _validate_identifier(expected_request_id)  # Check separate context.
    if type(record) is not dict or record.keys() != _KEYS:
        raise ApprovalRequestValidationError("Invalid approval request")
    if type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise ApprovalRequestValidationError("Invalid approval request")

    identifier = _validate_identifier(record["request_id"])
    if identifier != expected:  # Equal contents cannot replace request identity.
        raise ApprovalRequestValidationError("Invalid approval request")

    try:
        snapshot = capture_request_snapshot(
            {
                "tool": record["tool"],
                "operation": record["operation"],
                "arguments": {"item_id": record["item_id"]},
            },
            principal_id=record["principal_id"],
        )  # Reuse exact type, supported context, and item-range checks.
    except RequestSnapshotValidationError:
        raise ApprovalRequestValidationError("Invalid approval request") from None

    return {
        "schema_version": 1,
        "request_id": identifier,
        "principal_id": snapshot.principal_id,
        "tool": snapshot.tool,
        "operation": snapshot.operation,
        "item_id": snapshot.item_id,
    }  # Return only checked immutable values in a fresh dictionary.


def build_approval_request(
    request: object,
    *,
    request_id: object,
    principal_id: object,
) -> ApprovalRequest:
    """Capture supplied values without authentication, storage, or execution."""
    identifier = _validate_identifier(request_id)  # Generate no request identity.
    try:
        snapshot = capture_request_snapshot(
            request, principal_id=principal_id
        )  # Retain checked scalar values rather than caller-owned dictionaries.
    except RequestSnapshotValidationError:
        raise ApprovalRequestValidationError("Invalid approval request") from None

    return validate_approval_request(
        {
            "schema_version": 1,
            "request_id": identifier,
            "principal_id": snapshot.principal_id,
            "tool": snapshot.tool,
            "operation": snapshot.operation,
            "item_id": snapshot.item_id,
        },
        expected_request_id=identifier,
    )  # Apply the shared descriptor contract without granting approval.


def validate_approval_request_match(
    record: object,
    *,
    expected_request_id: object,
    expected_snapshot: object,
) -> ApprovalRequest:
    """Check consistency with expected values supplied by trusted code."""
    # Require the existing snapshot type rather than accepting a second form.
    if type(expected_snapshot) is not RequestSnapshot:
        raise ApprovalRequestValidationError("Invalid approval request")

    # Check the candidate's schema, supported values, and request ID first.
    checked = validate_approval_request(record, expected_request_id=expected_request_id)

    # Compare every captured field so a matching ID cannot hide changed details.
    if (
        checked["principal_id"] != expected_snapshot.principal_id
        or checked["tool"] != expected_snapshot.tool
        or checked["operation"] != expected_snapshot.operation
        or checked["item_id"] != expected_snapshot.item_id
    ):
        raise ApprovalRequestValidationError("Invalid approval request")

    # Return the validator's fresh dictionary, without modifying the input.
    return checked
