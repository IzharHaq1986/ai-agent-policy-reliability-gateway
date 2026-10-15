"""Pure capture of fixed synthetic request values."""

from dataclasses import dataclass  # Generate a record that blocks ordinary changes.


class RequestSnapshotValidationError(ValueError):  # Identify invalid snapshot inputs.
    """An input does not satisfy the supported snapshot contract."""


@dataclass(frozen=True, slots=True)  # Block ordinary edits and extra instance fields.
class RequestSnapshot:  # Hold only immutable values needed for the supported request.
    principal_id: str  # Record the separately supplied caller identity.
    tool: str  # Record the supported tool name.
    operation: str  # Record the supported operation.
    item_id: int  # Record the item used by all later processing.

    def __post_init__(self) -> None:  # Check capture and direct construction equally.
        if (  # Require ordinary text before comparing context values.
            type(self.principal_id) is not str  # Reject non-string caller identities.
            or type(self.tool) is not str  # Reject non-string tool names.
            or type(self.operation) is not str  # Reject non-string operations.
        ):  # Stop before accepting unsupported input types.
            raise RequestSnapshotValidationError("Invalid request snapshot")
        if (  # Restrict this component to its documented synthetic context.
            self.principal_id != "reliability-reader"  # Require the supported caller.
            or self.tool != "fixture_store"  # Require the supported practice tool.
            or self.operation != "READ_ITEM"  # Require the supported read operation.
        ):  # Matching context values do not establish permission.
            raise RequestSnapshotValidationError("Invalid request snapshot")
        if type(self.item_id) is not int or not 1 <= self.item_id <= 1000:
            # Reject booleans, subclasses, and item numbers outside the allowed range.
            raise RequestSnapshotValidationError("Invalid request snapshot")

    def to_policy_request(
        self,
    ) -> dict[str, object]:  # Prepare data for policy evaluation.
        return {  # Create a new outer dictionary on every call.
            "tool": self.tool,  # Use the captured tool name.
            "operation": self.operation,  # Use the captured operation.
            "arguments": {"item_id": self.item_id},  # Create fresh nested arguments.
        }  # Changing this returned dictionary cannot change the snapshot.


def capture_request_snapshot(  # Capture supported data without granting authority.
    request: object,  # Receive the caller's structured request.
    *,  # Require the identity to be supplied by name.
    principal_id: object,  # Receive identity separately from request-body claims.
) -> RequestSnapshot:  # Return a checked snapshot or raise a validation error.
    """Capture values without I/O; input must not change during capture."""
    if type(request) is not dict:  # Require an ordinary request dictionary.
        raise RequestSnapshotValidationError("Invalid request snapshot")
    if request.keys() != {"tool", "operation", "arguments"}:  # Require exact fields.
        raise RequestSnapshotValidationError("Invalid request snapshot")

    arguments = request["arguments"]  # Read arguments after checking the outer fields.
    if type(arguments) is not dict:  # Reject custom or unsupported containers.
        raise RequestSnapshotValidationError("Invalid request snapshot")
    if arguments.keys() != {"item_id"}:  # Accept only the supported argument.
        raise RequestSnapshotValidationError("Invalid request snapshot")

    tool = request["tool"]  # Capture the tool before constructing the snapshot.
    operation = request["operation"]  # Capture the operation once.
    item_id = arguments["item_id"]  # Capture the item without retaining its dictionary.
    if (  # Check runtime types before calling the typed constructor.
        type(principal_id) is not str  # Identity must be ordinary text.
        or type(tool) is not str  # Tool must be ordinary text.
        or type(operation) is not str  # Operation must be ordinary text.
        or type(item_id) is not int  # Item must be an actual built-in integer.
    ):  # Reject invalid types with the same generic error.
        raise RequestSnapshotValidationError("Invalid request snapshot")

    return RequestSnapshot(  # Construction checks supported values and item range.
        principal_id=principal_id,  # Preserve the separately supplied identity.
        tool=tool,  # Preserve the captured tool.
        operation=operation,  # Preserve the captured operation.
        item_id=item_id,  # Preserve the captured item number.
    )  # No authentication, policy evaluation, storage, or execution occurs.
