"""Pure construction and validation of synthetic request bindings."""

from typing import (
    Literal,
    TypedDict,
    cast,
)  # Describe record types for static checking.
from uuid import UUID  # Check the decision identifier without generating a new one.


class RequestBindingValidationError(ValueError):  # Identify failures in this component.
    """An input does not satisfy the request-binding contract."""


class RequestBinding(TypedDict):  # Describe the six fields a checked record contains.
    schema_version: Literal[1]  # Identify the supported record format.
    policy_event_id: str  # Link this request to a claimed policy-decision identifier.
    principal_id: Literal["reliability-reader"]  # Restrict the supported caller name.
    tool: Literal["fixture_store"]  # Restrict the supported practice tool.
    operation: Literal["READ_ITEM"]  # Restrict the supported operation.
    item_id: int  # Identify the requested synthetic item.


_BINDING_KEYS = frozenset(
    RequestBinding.__annotations__
)  # Require exactly these fields.
_STRING_KEYS = (
    "policy_event_id",
    "principal_id",
    "tool",
    "operation",
)  # Check text types.


def validate_request_binding(
    binding: object,
) -> RequestBinding:  # Check untrusted data.
    """Return a fresh checked record without establishing authority."""
    # Reject the wrong container or field set before accessing individual values.
    if type(binding) is not dict or binding.keys() != _BINDING_KEYS:
        raise RequestBindingValidationError(
            "Invalid request binding"
        )  # Hide input details.

    # Require the actual integer 1; True and integer subclasses are not accepted.
    if type(binding["schema_version"]) is not int or binding["schema_version"] != 1:
        raise RequestBindingValidationError(
            "Invalid request binding"
        )  # Reject bad versions.
    if any(
        type(binding[key]) is not str for key in _STRING_KEYS
    ):  # Require ordinary text.
        raise RequestBindingValidationError(
            "Invalid request binding"
        )  # Reject bad types.

    item_id = binding["item_id"]  # Read the requested item after checking the fields.
    if type(item_id) is not int or not 1 <= item_id <= 1000:  # Enforce type and range.
        raise RequestBindingValidationError(
            "Invalid request binding"
        )  # Reject invalid items.

    if (  # Accept only the caller, tool, and operation supported by this contract.
        binding["principal_id"]
        != "reliability-reader"  # Check the supported caller name.
        or binding["tool"] != "fixture_store"  # Check the supported tool name.
        or binding["operation"] != "READ_ITEM"  # Check the supported operation.
    ):  # Any mismatch invalidates the record; matching names do not prove permission.
        raise RequestBindingValidationError(
            "Invalid request binding"
        )  # Reject mismatches.

    identifier = binding[
        "policy_event_id"
    ]  # Read the claimed policy-decision identifier.
    try:  # Report malformed identifiers with a generic error.
        parsed = UUID(
            identifier
        )  # Parse the supplied identifier without external access.
    except ValueError:  # Handle text that cannot be parsed as a UUID.
        raise RequestBindingValidationError(
            "Invalid request binding"
        ) from None  # Hide details.
    if (
        parsed.version != 4 or str(parsed) != identifier
    ):  # Require canonical version-4 text.
        raise RequestBindingValidationError(
            "Invalid request binding"
        )  # Reject other forms.

    # Copy the record so changing the returned dictionary does not change the input.
    return cast(
        RequestBinding, dict(binding)
    )  # The cast aids typing; checks above validate.


def build_request_binding(  # Construct a record and apply the same runtime checks.
    *,  # Require named arguments to make each supplied value's purpose explicit.
    policy_event_id: object,  # Receive the decision identifier for validation.
    principal_id: object,  # Receive the caller name without authenticating it.
    tool: object,  # Receive the requested tool name.
    operation: object,  # Receive the requested operation.
    item_id: object,  # Receive the requested item number.
) -> RequestBinding:  # Return a checked record or raise a validation error.
    """Build a record from explicit values without I/O or generated identity."""
    return validate_request_binding(  # Apply the shared validation rules.
        {  # Assemble only the six supported fields.
            "schema_version": 1,  # Supply the record format supported by this builder.
            "policy_event_id": policy_event_id,  # Keep the supplied decision ID.
            "principal_id": principal_id,  # Preserve the supplied caller name.
            "tool": tool,  # Preserve the supplied tool name.
            "operation": operation,  # Preserve the supplied operation.
            "item_id": item_id,  # Preserve the supplied item number.
        }  # Finish the candidate record.
    )  # Return a fresh validated record; no storage or execution occurs.
