"""Contract tests for synthetic request-binding records."""

from copy import deepcopy  # Preserve inputs so tests can detect unwanted changes.

import pytest  # Run repeated test cases and check expected exceptions.

from gateway.request_binding import (  # Import the real component under test.
    RequestBindingValidationError,  # Identify the component's expected rejection.
    build_request_binding,  # Test record construction.
    validate_request_binding,  # Test checks applied to existing records.
)  # Finish the component imports.


def binding():  # Provide one known-good record for each test.
    return {  # Return a new dictionary so tests remain independent.
        "schema_version": 1,  # Select the supported format.
        "policy_event_id": "11111111-1111-4111-8111-111111111111",  # Valid decision ID.
        "principal_id": "reliability-reader",  # Use the supported caller name.
        "tool": "fixture_store",  # Use the supported practice tool.
        "operation": "READ_ITEM",  # Use the supported operation.
        "item_id": 42,  # Use an allowed item number.
    }  # Finish the sample record.


def build(record):  # Pass a candidate record through the public builder.
    arguments = dict(record)  # Keep the original record untouched.
    del arguments["schema_version"]  # The builder supplies this field itself.
    return build_request_binding(**arguments)  # Validate all supplied values.


def assert_rejected(record):  # Check rejection and preservation together.
    original = deepcopy(record)  # Remember the input before validation.
    with pytest.raises(  # Require the dedicated error and generic message.
        RequestBindingValidationError, match="^Invalid request binding$"
    ):  # Run the validation inside the expected-error check.
        validate_request_binding(record)  # Exercise the real validator.
    assert record == original  # Rejected input must remain unchanged.


@pytest.mark.parametrize("item_id", [1, 2, 42, 999, 1000])  # Include range boundaries.
def test_valid_records_build_and_validate_independently(
    item_id,
):  # Check both interfaces.
    record = binding()  # Start from a supported record.
    record["item_id"] = item_id  # Select this case's item.
    original = deepcopy(record)  # Preserve the expected contents.
    checked = validate_request_binding(record)  # Validate an existing record.
    built = build(record)  # Build the same record through explicit inputs.
    assert checked == built == original  # Require the exact expected contents.
    assert record == original  # Neither interface may alter the original.
    assert checked is not record  # Validation must return a fresh dictionary.
    assert built is not record  # Construction must return a separate dictionary.
    assert built is not checked  # Separate calls must not share their result.
    checked["item_id"] = 0  # Simulate a caller changing one returned dictionary.
    assert record == built == original  # Other dictionaries must remain unchanged.


@pytest.mark.parametrize("key", tuple(binding()))  # Remove each required field in turn.
def test_missing_fields_are_rejected(key):  # Detect incomplete records.
    record = binding()  # Start with all required fields.
    del record[key]  # Remove the selected field.
    assert_rejected(record)  # Require rejection without mutation.


@pytest.mark.parametrize(
    "key", ["credentials", "output", "metadata"]
)  # Try extra fields.
def test_extra_fields_are_rejected(key):  # Prevent unsupported payload storage.
    record = binding()  # Start with a supported record.
    record[key] = "private"  # Add information outside the contract.
    assert_rejected(record)  # Reject the entire record.


@pytest.mark.parametrize(
    "value", [None, True, 1, "record", []]
)  # Try wrong containers.
def test_non_dictionary_inputs_are_rejected(value):  # Require an actual dictionary.
    assert_rejected(value)  # Check the generic rejection.


class DictionarySubclass(
    dict
):  # Represent a dictionary with potentially custom behavior.
    pass  # No custom behavior is needed to test the exact-type rule.


class StringSubclass(str):  # Represent text that is not a built-in string.
    pass  # The subclass type alone should cause rejection.


class IntegerSubclass(int):  # Represent a number that is not a built-in integer.
    pass  # The subclass type alone should cause rejection.


def test_dictionary_subclass_is_rejected():  # Enforce the exact container type.
    assert_rejected(DictionarySubclass(binding()))  # Reject even matching contents.


@pytest.mark.parametrize(  # Apply wrong types to every text field.
    "key", ["policy_event_id", "principal_id", "tool", "operation"]
)  # Finish the text-field list.
@pytest.mark.parametrize("value", [None, True, 1, [], {}])  # Include unhashable values.
def test_invalid_text_types_are_rejected(
    key, value
):  # Check types before using values.
    record = binding()  # Start with a supported record.
    record[key] = value  # Replace one text value with a wrong type.
    assert_rejected(record)  # Require the component's generic validation error.


@pytest.mark.parametrize(  # Check the exact string type for every text field.
    "key", ["policy_event_id", "principal_id", "tool", "operation"]
)  # Finish the field list.
def test_string_subclasses_are_rejected(key):  # Reject text subclasses.
    record = binding()  # Start with valid values.
    record[key] = StringSubclass(record[key])  # Preserve text while changing its type.
    assert_rejected(record)  # Require rejection of the subclass.


@pytest.mark.parametrize(  # Reject unsupported version values and types.
    "value", [True, False, 0, 2, 1.0, "1", IntegerSubclass(1)]
)  # Finish the invalid version cases.
def test_invalid_schema_versions_are_rejected(value):  # Enforce format version exactly.
    record = binding()  # Start with a supported record.
    record["schema_version"] = value  # Supply an invalid version.
    assert_rejected(record)  # Require rejection.


@pytest.mark.parametrize(  # Try unsupported names while keeping their types valid.
    "key,value",
    [
        ("principal_id", "other-reader"),  # A different caller name is unsupported.
        ("tool", "other-store"),  # A different tool is unsupported.
        ("operation", "WRITE_ITEM"),  # Writing is unsupported.
    ],
)  # Finish the unsupported-value cases.
def test_unsupported_context_is_rejected(key, value):  # Enforce the bounded context.
    record = binding()  # Start with supported values.
    record[key] = value  # Introduce one unsupported value.
    assert_rejected(record)  # Reject the record.


@pytest.mark.parametrize(  # Exercise malformed and noncanonical decision identifiers.
    "value",
    [
        "",  # Empty text cannot identify a decision.
        "not-a-uuid",  # Arbitrary text is not a UUID.
        "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",  # Uppercase is not canonical here.
        "11111111111141118111111111111111",  # Missing separators is noncanonical.
        "{11111111-1111-4111-8111-111111111111}",  # Braces are noncanonical.
        "11111111-1111-1111-8111-111111111111",  # Version 1 is unsupported.
        "11111111-1111-4111-7111-111111111111",  # The UUID variant is invalid here.
    ],
)  # Finish the identifier cases.
def test_invalid_policy_identifiers_are_rejected(value):  # Check identifier format.
    record = binding()  # Start with a supported record.
    record["policy_event_id"] = value  # Replace its decision identifier.
    assert_rejected(record)  # Require generic rejection.


@pytest.mark.parametrize(  # Exercise item types and values outside the contract.
    "value",
    [
        True,
        False,
        None,
        "42",
        42.0,
        [],
        {},  # These are not built-in integers.
        IntegerSubclass(42),  # Integer subclasses are also excluded.
        -1,
        0,
        1001,
        10**100,  # These integers are outside the allowed range.
    ],
)  # Finish the invalid item cases.
def test_invalid_items_are_rejected(value):  # Enforce item type and range.
    record = binding()  # Start with a supported record.
    record["item_id"] = value  # Supply the invalid item.
    assert_rejected(record)  # Require rejection without input changes.


@pytest.mark.parametrize(  # Check every value accepted by the builder.
    "key", [key for key in binding() if key != "schema_version"]
)  # Exclude the version because the builder supplies it.
def test_builder_checks_each_supplied_field(
    key,
):  # Ensure construction cannot bypass checks.
    record = binding()  # Start with a supported record.
    record[key] = None  # Make one supplied value invalid.
    original = deepcopy(record)  # Preserve the candidate before construction.
    with pytest.raises(  # Require the dedicated generic error.
        RequestBindingValidationError, match="^Invalid request binding$"
    ):  # Exercise the builder's validation path.
        build(record)  # Construct using the invalid input.
    assert record == original  # Failure must leave the candidate unchanged.
