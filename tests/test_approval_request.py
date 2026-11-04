"""Contract tests for pure synthetic approval-request descriptors."""

from copy import deepcopy  # Detect input mutation.

import pytest

from gateway.approval_request import (
    ApprovalRequestValidationError,
    build_approval_request,
    validate_approval_request,
)

REQUEST_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"


class DictionarySubclass(dict):
    pass


class StringSubclass(str):
    pass


class IntegerSubclass(int):
    pass


def request():
    """Return independent supported request dictionaries."""
    return {
        "tool": "fixture_store",
        "operation": "READ_ITEM",
        "arguments": {"item_id": 42},
    }


def record():
    """Return an independent supported descriptor."""
    return {
        "schema_version": 1,
        "request_id": REQUEST_ID,
        "principal_id": "reliability-reader",
        "tool": "fixture_store",
        "operation": "READ_ITEM",
        "item_id": 42,
    }


def rejected(candidate, *, expected=REQUEST_ID):
    """Require generic rejection without modifying the candidate."""
    original = deepcopy(candidate)
    with pytest.raises(
        ApprovalRequestValidationError, match="^Invalid approval request$"
    ):
        validate_approval_request(candidate, expected_request_id=expected)
    assert candidate == original


@pytest.mark.parametrize("item_id", [1, 2, 42, 999, 1000])
def test_build_and_validate_return_independent_checked_records(item_id):
    supplied = request()
    supplied["arguments"]["item_id"] = item_id
    original = deepcopy(supplied)
    expected = record()
    expected["item_id"] = item_id

    built = build_approval_request(
        supplied, request_id=REQUEST_ID, principal_id="reliability-reader"
    )
    checked = validate_approval_request(built, expected_request_id=REQUEST_ID)
    assert built == checked == expected
    assert built is not checked
    assert supplied == original

    supplied["arguments"]["item_id"] = 0
    assert built == checked == expected
    checked["item_id"] = 1001
    assert built == expected


@pytest.mark.parametrize("key", tuple(record()))
def test_missing_fields_are_rejected(key):
    candidate = record()
    del candidate[key]
    rejected(candidate)


@pytest.mark.parametrize("key", ["approved", "credentials", "metadata"])
def test_extra_fields_are_rejected(key):
    candidate = record()
    candidate[key] = "unsupported"
    rejected(candidate)


@pytest.mark.parametrize(
    "candidate", [None, True, 1, "record", [], DictionarySubclass(record())]
)
def test_invalid_record_containers_are_rejected(candidate):
    rejected(candidate)


@pytest.mark.parametrize("value", [True, False, 0, 2, 1.0, "1", IntegerSubclass(1)])
def test_invalid_versions_are_rejected(value):
    candidate = record()
    candidate["schema_version"] = value
    rejected(candidate)


@pytest.mark.parametrize("key", ["principal_id", "tool", "operation"])
@pytest.mark.parametrize("value", [None, True, 1, [], {}, "unsupported"])
def test_invalid_context_fields_are_rejected(key, value):
    candidate = record()
    candidate[key] = value
    rejected(candidate)


@pytest.mark.parametrize("key", ["principal_id", "tool", "operation"])
def test_context_string_subclasses_are_rejected(key):
    candidate = record()
    candidate[key] = StringSubclass(candidate[key])
    rejected(candidate)


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        None,
        "42",
        42.0,
        [],
        {},
        IntegerSubclass(42),
        -1,
        0,
        1001,
        10**100,
    ],
)
def test_invalid_items_are_rejected(value):
    candidate = record()
    candidate["item_id"] = value
    rejected(candidate)


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        1,
        [],
        {},
        "",
        "not-a-uuid",
        "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
        "11111111111141118111111111111111",
        "{11111111-1111-4111-8111-111111111111}",
        "11111111-1111-1111-8111-111111111111",
        "11111111-1111-4111-7111-111111111111",
        StringSubclass(REQUEST_ID),
    ],
)
@pytest.mark.parametrize("location", ["record", "expected", "builder"])
def test_invalid_identifiers_are_rejected(value, location):
    if location == "record":
        candidate = record()
        candidate["request_id"] = value
        rejected(candidate)
    elif location == "expected":
        rejected(record(), expected=value)
    else:
        with pytest.raises(
            ApprovalRequestValidationError, match="^Invalid approval request$"
        ):
            build_approval_request(
                request(), request_id=value, principal_id="reliability-reader"
            )


def test_equal_contents_with_distinct_request_identities_remain_distinct():
    first = build_approval_request(
        request(), request_id=REQUEST_ID, principal_id="reliability-reader"
    )
    second = build_approval_request(
        request(), request_id=OTHER_ID, principal_id="reliability-reader"
    )
    assert first["request_id"] != second["request_id"]
    assert {key: value for key, value in first.items() if key != "request_id"} == {
        key: value for key, value in second.items() if key != "request_id"
    }
    assert validate_approval_request(second, expected_request_id=OTHER_ID) == second
    rejected(first, expected=OTHER_ID)
    rejected(second, expected=REQUEST_ID)


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        DictionarySubclass(request()),
        {},
        {**request(), "approved": True},
        {"tool": "fixture_store", "operation": "READ_ITEM", "arguments": []},
        {"tool": "fixture_store", "operation": "READ_ITEM", "arguments": {}},
        {
            "tool": "fixture_store",
            "operation": "READ_ITEM",
            "arguments": {"item_id": 42, "extra": 1},
        },
        {
            "tool": "fixture_store",
            "operation": "READ_ITEM",
            "arguments": DictionarySubclass({"item_id": 42}),
        },
        {**request(), "tool": "other_tool"},
        {**request(), "operation": "WRITE_ITEM"},
        {**request(), "arguments": {"item_id": 0}},
        {**request(), "arguments": {"item_id": True}},
    ],
)
def test_builder_rejects_invalid_requests_without_mutation(candidate):
    original = deepcopy(candidate)
    with pytest.raises(
        ApprovalRequestValidationError, match="^Invalid approval request$"
    ) as caught:
        build_approval_request(
            candidate, request_id=REQUEST_ID, principal_id="reliability-reader"
        )
    assert str(caught.value) == "Invalid approval request"
    assert candidate == original


@pytest.mark.parametrize(
    "principal",
    [None, True, 1, [], {}, "other-reader", StringSubclass("reliability-reader")],
)
def test_builder_rejects_invalid_principals(principal):
    with pytest.raises(
        ApprovalRequestValidationError, match="^Invalid approval request$"
    ):
        build_approval_request(request(), request_id=REQUEST_ID, principal_id=principal)


def test_nested_validation_details_are_not_displayed():
    candidate = record()
    candidate["item_id"] = 0
    for operation in (
        lambda: validate_approval_request(candidate, expected_request_id=REQUEST_ID),
        lambda: build_approval_request(
            {**request(), "arguments": {"item_id": 0}},
            request_id=REQUEST_ID,
            principal_id="reliability-reader",
        ),
    ):
        with pytest.raises(ApprovalRequestValidationError) as caught:
            operation()
        assert caught.value.__suppress_context__ is True
        assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "operation",
    [
        lambda: build_approval_request(request(), principal_id="reliability-reader"),
        lambda: build_approval_request(request(), request_id=REQUEST_ID),
        lambda: validate_approval_request(record()),
        lambda: validate_approval_request(
            record(), expected_request_id=REQUEST_ID, extra=True
        ),
        lambda: build_approval_request(
            request(),
            request_id=REQUEST_ID,
            principal_id="reliability-reader",
            extra=True,
        ),
    ],
)
def test_missing_or_unexpected_arguments_retain_type_error(operation):
    with pytest.raises(TypeError):
        operation()
