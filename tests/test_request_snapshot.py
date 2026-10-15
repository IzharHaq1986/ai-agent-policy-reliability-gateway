"""Contract tests for fixed synthetic request snapshots."""

from copy import deepcopy  # Detect unwanted changes to caller input.
from dataclasses import FrozenInstanceError  # Identify blocked ordinary edits.

import pytest  # Run cases and check expected failures.

from gateway.policy import evaluate_request  # Verify compatibility with real policy.
from gateway.request_snapshot import (  # Import the actual component.
    RequestSnapshot,  # Test direct construction as well as capture.
    RequestSnapshotValidationError,  # Check the documented rejection.
    capture_request_snapshot,  # Capture the supported request.
)  # Finish the imports.


def request():  # Provide an independent supported request for each test.
    return {  # Keep caller identity outside the request body.
        "tool": "fixture_store",  # Select the supported tool.
        "operation": "READ_ITEM",  # Select the supported operation.
        "arguments": {"item_id": 42},  # Select an allowed item.
    }  # Finish the request.


def fields():  # Provide valid direct-construction arguments.
    return {  # Return a fresh set of immutable values.
        "principal_id": "reliability-reader",  # Supply the supported identity.
        "tool": "fixture_store",  # Supply the supported tool.
        "operation": "READ_ITEM",  # Supply the supported operation.
        "item_id": 42,  # Supply an allowed item.
    }  # Finish the constructor arguments.


@pytest.mark.parametrize("item_id", [1, 2, 42, 999, 1000])  # Cover range boundaries.
def test_valid_capture_and_policy_compatibility(item_id):  # Check the full data shape.
    original_request = request()  # Create caller-owned input.
    original_request["arguments"]["item_id"] = item_id  # Select the test item.
    before = deepcopy(original_request)  # Preserve the input for comparison.
    snapshot = capture_request_snapshot(  # Use the public capture interface.
        original_request, principal_id="reliability-reader"
    )  # Keep identity separate from request contents.
    expected_fields = fields()  # Prepare the expected constructor values.
    expected_fields["item_id"] = item_id  # Match the selected item.
    assert snapshot == RequestSnapshot(**expected_fields)  # Check all captured fields.
    assert original_request == before  # Capture must not change caller input.
    assert snapshot.to_policy_request() == before  # Preserve the policy request shape.
    result = evaluate_request(  # Exercise the unchanged policy with captured data.
        snapshot.to_policy_request(), principal_id=snapshot.principal_id
    )  # Policy still makes its own decision.
    assert result == {  # Require the existing non-executing result contract.
        "decision": "ALLOW",  # This supported request is permitted by current policy.
        "reason_code": "POLICY_ALLOW",  # Preserve the current allow reason.
        "policy_version": "policy-core-v1",  # Preserve the policy version.
        "execution_status": "not_executed",  # Capture and evaluation run no tool.
    }  # Finish the expected policy result.


class DictionarySubclass(dict):  # Represent a custom dictionary type.
    pass  # The type alone tests the exact-container requirement.


class StringSubclass(str):  # Represent text with a custom type.
    pass  # Matching text must still be rejected.


class IntegerSubclass(int):  # Represent an integer with a custom type.
    pass  # Matching numeric value must still be rejected.


@pytest.mark.parametrize(  # Exercise invalid outer and nested request shapes.
    "candidate",
    [
        None,  # No request is invalid.
        [],  # A list is not a request dictionary.
        "request",  # Text is not a request dictionary.
        DictionarySubclass(request()),  # Custom outer dictionaries are unsupported.
        {},  # Required fields are missing.
        {"operation": "READ_ITEM", "arguments": {"item_id": 42}},  # Tool is missing.
        {
            "tool": "fixture_store",
            "arguments": {"item_id": 42},
        },  # Operation is missing.
        {"tool": "fixture_store", "operation": "READ_ITEM"},  # Arguments are missing.
        {**request(), "principal_id": "reliability-reader"},  # Body identity is extra.
        {**request(), "decision": "ALLOW"},  # A supplied decision is extra.
        {**request(), "arguments": None},  # Arguments must be a dictionary.
        {**request(), "arguments": []},  # Lists cannot contain supported arguments.
        {
            **request(),
            "arguments": DictionarySubclass(item_id=42),
        },  # Reject subclasses.
        {**request(), "arguments": {}},  # The item is missing.
        {
            **request(),
            "arguments": {"item_id": 42, "extra": 1},
        },  # Extra arguments fail.
    ],
)  # Finish the shape cases.
def test_invalid_shapes_are_rejected_without_mutation(
    candidate,
):  # Enforce exact fields.
    before = deepcopy(candidate)  # Preserve even malformed input.
    with pytest.raises(  # Require the dedicated generic error.
        RequestSnapshotValidationError, match="^Invalid request snapshot$"
    ):  # Exercise capture only; no tool is called.
        capture_request_snapshot(candidate, principal_id="reliability-reader")
    assert candidate == before  # Rejection must not alter the input.


INVALID_FIELDS = [  # Test the same rules through capture and direct construction.
    ("principal_id", None),  # Identity cannot be absent.
    ("principal_id", True),  # Identity cannot be boolean.
    ("principal_id", "other-reader"),  # Only the supported context is accepted.
    ("principal_id", StringSubclass("reliability-reader")),  # Reject text subclasses.
    ("tool", None),  # Tool must be text.
    ("tool", []),  # Reject custom-shaped tool values before comparisons.
    ("tool", "other-store"),  # Unknown tools are unsupported.
    ("tool", StringSubclass("fixture_store")),  # Reject tool-name subclasses.
    ("operation", True),  # Operation must be text.
    ("operation", {}),  # A dictionary is not an operation name.
    ("operation", "WRITE_ITEM"),  # Writing is unsupported.
    ("operation", StringSubclass("READ_ITEM")),  # Reject operation-name subclasses.
    ("item_id", True),  # Boolean True is not an item number.
    ("item_id", False),  # Boolean False is not an item number.
    ("item_id", None),  # An item number is required.
    ("item_id", "42"),  # Numeric text is not a built-in integer.
    ("item_id", 42.0),  # A float is not a built-in integer.
    ("item_id", []),  # Lists are invalid item values.
    ("item_id", {}),  # Dictionaries are invalid item values.
    ("item_id", IntegerSubclass(42)),  # Reject integer subclasses.
    ("item_id", -1),  # Reject negative identifiers.
    ("item_id", 0),  # Reject values below the lower boundary.
    ("item_id", 1001),  # Reject values above the upper boundary.
    ("item_id", 10**100),  # Reject very large integers.
]  # Finish the invalid field cases.


@pytest.mark.parametrize("key,value", INVALID_FIELDS)  # Check every invalid field.
def test_direct_construction_rejects_invalid_fields(key, value):  # Prevent a bypass.
    arguments = fields()  # Start with valid constructor values.
    arguments[key] = value  # Introduce one invalid field.
    with pytest.raises(  # Require the public validation exception.
        RequestSnapshotValidationError, match="^Invalid request snapshot$"
    ):  # Construction must validate itself.
        RequestSnapshot(**arguments)  # Exercise the dataclass constructor.


@pytest.mark.parametrize(
    "key,value", INVALID_FIELDS
)  # Reuse the field cases for capture.
def test_capture_rejects_invalid_fields_without_mutation(
    key, value
):  # Check both paths.
    candidate = request()  # Start with a supported request.
    principal = "reliability-reader"  # Keep identity separate from the request.
    if key == "principal_id":  # Invalid identity belongs in trusted-context input.
        principal = value  # Supply the invalid context value.
    elif key == "item_id":  # Item values belong in the nested arguments.
        candidate["arguments"]["item_id"] = value  # Replace the requested item.
    else:  # Tool and operation are outer request fields.
        candidate[key] = value  # Replace the selected outer field.
    before = deepcopy(candidate)  # Preserve the actual candidate.
    with pytest.raises(  # Require the same generic failure for every invalid value.
        RequestSnapshotValidationError, match="^Invalid request snapshot$"
    ):  # Run capture with its separate context.
        capture_request_snapshot(candidate, principal_id=principal)
    assert candidate == before  # Rejection must preserve the request.


@pytest.mark.parametrize("name", tuple(fields()))  # Try changing every stored field.
def test_ordinary_field_assignment_is_blocked(name):  # Check the frozen record.
    snapshot = RequestSnapshot(**fields())  # Construct a valid snapshot.
    with pytest.raises(FrozenInstanceError):  # Ordinary replacement must fail.
        setattr(snapshot, name, "changed")  # Attempt to replace a field.
    assert snapshot == RequestSnapshot(**fields())  # Ensure values remain intact.


@pytest.mark.parametrize("name", tuple(fields()))  # Try removing every stored field.
def test_ordinary_field_deletion_is_blocked(name):  # Preserve the captured record.
    snapshot = RequestSnapshot(**fields())  # Construct a valid snapshot.
    with pytest.raises(FrozenInstanceError):  # Ordinary deletion must fail.
        delattr(snapshot, name)  # Attempt to remove a field.
    assert snapshot == RequestSnapshot(**fields())  # Ensure values remain intact.


def test_caller_mutation_after_capture_cannot_change_snapshot():  # Check ownership.
    candidate = request()  # Create caller-owned nested dictionaries.
    snapshot = capture_request_snapshot(  # Capture the original item 42.
        candidate, principal_id="reliability-reader"
    )  # Retain no caller dictionaries.
    candidate["arguments"]["item_id"] = 43  # Change the caller's nested item.
    candidate["tool"] = "other-store"  # Change the caller's outer tool.
    candidate.clear()  # Remove the rest of the caller's request.
    assert snapshot == RequestSnapshot(**fields())  # Captured values stay unchanged.


def test_policy_conversions_have_independent_nested_dictionaries():  # Fresh output.
    snapshot = RequestSnapshot(**fields())  # Construct a supported snapshot.
    first = snapshot.to_policy_request()  # Create the first policy request.
    second = snapshot.to_policy_request()  # Create another policy request.
    assert (
        first == second == request()
    )  # Both must initially match the captured request.
    assert first is not second  # Outer dictionaries must be independent.
    assert first["arguments"] is not second["arguments"]  # Nested dictionaries too.
    first["arguments"]["item_id"] = 43  # Change one returned nested dictionary.
    first["tool"] = "other-store"  # Change its outer dictionary.
    assert second == request()  # The other conversion must remain unchanged.
    assert (
        snapshot.to_policy_request() == request()
    )  # Future conversions remain correct.
    assert snapshot == RequestSnapshot(
        **fields()
    )  # The snapshot itself remains intact.


def test_snapshot_has_no_instance_dictionary():  # Verify the slots representation.
    snapshot = RequestSnapshot(**fields())  # Construct the actual dataclass.
    assert not hasattr(snapshot, "__dict__")  # Ordinary per-instance extras are absent.
