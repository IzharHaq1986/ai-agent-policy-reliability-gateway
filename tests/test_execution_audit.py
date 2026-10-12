"""Contract tests for pure simulator execution events."""

from copy import deepcopy
from itertools import product

import pytest

from gateway.execution_audit import (
    ExecutionAuditValidationError,
    build_execution_event,
    validate_execution_event,
)


def event():
    return {
        "schema_version": 1,
        "event_type": "simulator_execution",
        "event_id": "11111111-1111-4111-8111-111111111111",
        "execution_id": "22222222-2222-4222-8222-222222222222",
        "policy_event_id": "33333333-3333-4333-8333-333333333333",
        "occurred_at": "2026-10-12T14:00:00Z",
        "principal_id": "reliability-reader",
        "tool": "fixture_store",
        "operation": "READ_ITEM",
        "status": "started",
        "reason_code": "SIMULATION_STARTED",
    }


VALID_PAIRS = {
    ("started", "SIMULATION_STARTED"),
    ("completed", "SIMULATION_COMPLETED"),
    ("failed", "SIMULATOR_ERROR"),
    ("failed", "INVALID_RESULT"),
}
STRING_FIELDS = tuple(key for key in event() if key != "schema_version")


def build(record):
    return build_execution_event(
        **{
            key: value
            for key, value in record.items()
            if key not in {"schema_version", "event_type"}
        }
    )


def assert_rejected(record):
    original = deepcopy(record)
    with pytest.raises(
        ExecutionAuditValidationError, match="^Invalid execution event$"
    ):
        validate_execution_event(record)
    assert record == original


@pytest.mark.parametrize("status,reason", sorted(VALID_PAIRS))
def test_valid_events_build_and_validate_without_mutation(status, reason):
    record = event()
    record.update(status=status, reason_code=reason)
    original = deepcopy(record)

    validated = validate_execution_event(record)
    built = build(record)

    assert record == original
    assert validated == built == original
    assert validated is not record
    assert built is not record
    assert built is not validated

    validated["tool"] = "changed"
    assert record == original
    assert built == original


@pytest.mark.parametrize("key", tuple(event()))
def test_missing_fields_are_rejected(key):
    record = event()
    del record[key]
    assert_rejected(record)


@pytest.mark.parametrize("key", ["credentials", "item_id", "output", "exception"])
def test_extra_fields_are_rejected(key):
    record = event()
    record[key] = "private"
    assert_rejected(record)


@pytest.mark.parametrize("value", [None, [], "event", 1, True])
def test_non_dictionary_events_are_rejected(value):
    assert_rejected(value)


class DictionarySubclass(dict):
    pass


class StringSubclass(str):
    pass


class IntegerSubclass(int):
    pass


def test_dictionary_subclass_is_rejected():
    assert_rejected(DictionarySubclass(event()))


@pytest.mark.parametrize("key", STRING_FIELDS)
@pytest.mark.parametrize("value", [None, 1, True, [], {}])
def test_non_string_fields_are_rejected(key, value):
    record = event()
    record[key] = value
    assert_rejected(record)


@pytest.mark.parametrize("key", STRING_FIELDS)
def test_string_subclasses_are_rejected(key):
    record = event()
    record[key] = StringSubclass(record[key])
    assert_rejected(record)


@pytest.mark.parametrize("value", [True, False, 0, 2, 1.0, "1", IntegerSubclass(1)])
def test_invalid_schema_versions_are_rejected(value):
    record = event()
    record["schema_version"] = value
    assert_rejected(record)


@pytest.mark.parametrize(
    "key,value",
    [
        ("event_type", "policy_decision"),
        ("principal_id", "other-reader"),
        ("tool", "other_store"),
        ("operation", "WRITE_ITEM"),
    ],
)
def test_unsupported_fixed_values_are_rejected(key, value):
    record = event()
    record[key] = value
    assert_rejected(record)


@pytest.mark.parametrize("key", ["event_id", "execution_id", "policy_event_id"])
@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-a-uuid",
        "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
        "11111111111141118111111111111111",
        "{11111111-1111-4111-8111-111111111111}",
        "11111111-1111-1111-8111-111111111111",
        "11111111-1111-4111-7111-111111111111",
    ],
)
def test_invalid_identifiers_are_rejected(key, value):
    record = event()
    record[key] = value
    assert_rejected(record)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "2026-02-29T14:00:00Z",
        "2026-04-31T14:00:00Z",
        "0000-01-01T00:00:00Z",
        "2026-13-01T00:00:00Z",
        "2026-10-12T24:00:00Z",
        "2026-10-12T14:60:00Z",
        "2026-10-12T14:00:60Z",
        "2026-10-12T14:00:00+00:00",
        "2026-10-12T14:00:00.000Z",
        "2026-10-12T14:00:00Z\n",
    ],
)
def test_invalid_timestamps_are_rejected(value):
    record = event()
    record["occurred_at"] = value
    assert_rejected(record)


@pytest.mark.parametrize(
    "value",
    ["2024-02-29T00:00:00Z", "0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z"],
)
def test_valid_calendar_boundaries_are_accepted(value):
    record = event()
    record["occurred_at"] = value
    assert validate_execution_event(record) == record


INVALID_PAIRS = sorted(
    set(
        product(
            ["started", "completed", "failed", "unknown"],
            [
                "SIMULATION_STARTED",
                "SIMULATION_COMPLETED",
                "SIMULATOR_ERROR",
                "INVALID_RESULT",
                "UNKNOWN",
            ],
        )
    )
    - VALID_PAIRS
)


@pytest.mark.parametrize("status,reason", INVALID_PAIRS)
def test_invalid_status_reason_pairs_are_rejected(status, reason):
    record = event()
    record.update(status=status, reason_code=reason)
    assert_rejected(record)


@pytest.mark.parametrize(
    "key", [key for key in event() if key not in {"schema_version", "event_type"}]
)
def test_builder_validates_every_supplied_field(key):
    record = event()
    record[key] = None
    with pytest.raises(
        ExecutionAuditValidationError, match="^Invalid execution event$"
    ):
        build(record)
