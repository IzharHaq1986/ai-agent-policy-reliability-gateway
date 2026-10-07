"""Acceptance tests for pure policy audit generation."""

import json
from copy import deepcopy

import pytest

from gateway.audit import AuditValidationError, build_audit_event

EVENT_ID = "550e8400-e29b-41d4-a716-446655440000"
OCCURRED_AT = "2026-10-07T10:00:00Z"


def valid_inputs() -> dict[str, object]:
    return {
        "policy_result": {
            "decision": "ALLOW",
            "reason_code": "POLICY_ALLOW",
            "policy_version": "policy-core-v1",
            "execution_status": "not_executed",
        },
        "principal_id": "reliability-reader",
        "event_id": EVENT_ID,
        "occurred_at": OCCURRED_AT,
    }


@pytest.mark.parametrize(
    ("decision", "reason"),
    [
        ("ALLOW", "POLICY_ALLOW"),
        ("DENY", "UNAUTHENTICATED"),
        ("DENY", "INVALID_REQUEST"),
        ("DENY", "UNKNOWN_TOOL"),
        ("DENY", "UNKNOWN_OPERATION"),
        ("DENY", "NOT_AUTHORIZED"),
    ],
)
def test_exact_event_and_serialization(decision, reason):
    inputs = valid_inputs()
    inputs["policy_result"]["decision"] = decision
    inputs["policy_result"]["reason_code"] = reason
    original = deepcopy(inputs)

    event = build_audit_event(**inputs)

    assert event == {
        "schema_version": 1,
        "event_type": "policy_decision",
        "event_id": EVENT_ID,
        "occurred_at": OCCURRED_AT,
        "principal_id": "reliability-reader",
        "decision": decision,
        "reason_code": reason,
        "policy_version": "policy-core-v1",
        "execution_status": "not_executed",
    }
    assert type(event["schema_version"]) is int
    assert json.loads(json.dumps(event, allow_nan=False)) == event
    assert build_audit_event(**inputs) == event
    assert inputs == original

    event["decision"] = "DENY"
    assert inputs == original


@pytest.mark.parametrize("value", [None, [], (), "result", 1, True])
def test_reject_non_dictionary_result(value):
    inputs = valid_inputs()
    inputs["policy_result"] = value
    with pytest.raises(AuditValidationError):
        build_audit_event(**inputs)


@pytest.mark.parametrize(
    "field", ["decision", "reason_code", "policy_version", "execution_status"]
)
def test_reject_missing_result_field(field):
    inputs = valid_inputs()
    del inputs["policy_result"][field]
    with pytest.raises(AuditValidationError):
        build_audit_event(**inputs)


@pytest.mark.parametrize(
    "field", ["token", "arguments", "principal_id", "schema_version"]
)
def test_reject_extra_result_field(field):
    inputs = valid_inputs()
    inputs["policy_result"][field] = "synthetic-untrusted-value"
    original = deepcopy(inputs)
    with pytest.raises(AuditValidationError) as error:
        build_audit_event(**inputs)
    assert "synthetic-untrusted-value" not in str(error.value)
    assert inputs == original


@pytest.mark.parametrize(
    "field", ["decision", "reason_code", "policy_version", "execution_status"]
)
@pytest.mark.parametrize("value", [None, True, 1, [], {}])
def test_reject_wrong_result_field_type(field, value):
    inputs = valid_inputs()
    inputs["policy_result"][field] = value
    original = deepcopy(inputs)
    with pytest.raises(AuditValidationError):
        build_audit_event(**inputs)
    assert inputs == original


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision", "allow"),
        ("decision", "UNKNOWN"),
        ("reason_code", "UNKNOWN_REASON"),
        ("reason_code", "UNKNOWN_TOOL"),
        ("policy_version", "policy-core-v2"),
        ("execution_status", "executed"),
    ],
)
def test_reject_unsupported_or_inconsistent_result(field, value):
    inputs = valid_inputs()
    inputs["policy_result"][field] = value
    with pytest.raises(AuditValidationError):
        build_audit_event(**inputs)


def test_reject_allow_reason_with_denial():
    inputs = valid_inputs()
    inputs["policy_result"]["decision"] = "DENY"
    with pytest.raises(AuditValidationError):
        build_audit_event(**inputs)


@pytest.mark.parametrize(
    "value", [None, True, 1, [], {}, "", "other-reader", " reliability-reader"]
)
def test_reject_invalid_principal(value):
    inputs = valid_inputs()
    inputs["principal_id"] = value
    with pytest.raises(AuditValidationError):
        build_audit_event(**inputs)


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
        EVENT_ID.upper(),
        EVENT_ID.replace("-", ""),
        "{" + EVENT_ID + "}",
        "urn:uuid:" + EVENT_ID,
        " " + EVENT_ID,
        "550e8400-e29b-11d4-a716-446655440000",
        "550e8400-e29b-41d4-0716-446655440000",
    ],
)
def test_reject_invalid_event_id(value):
    inputs = valid_inputs()
    inputs["event_id"] = value
    with pytest.raises(AuditValidationError):
        build_audit_event(**inputs)


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        1,
        [],
        {},
        "",
        "2026-02-29T10:00:00Z",
        "2026-04-31T10:00:00Z",
        "0000-01-01T00:00:00Z",
        "2026-13-01T00:00:00Z",
        "2026-10-07T24:00:00Z",
        "2026-10-07T10:60:00Z",
        "2026-10-07T10:00:60Z",
        "2026-10-07T10:00:00+00:00",
        "2026-10-07T10:00:00.000Z",
        "2026-10-07t10:00:00z",
        "2026-1-07T10:00:00Z",
        OCCURRED_AT + "\n",
        "２０２６-10-07T10:00:00Z",
    ],
)
def test_reject_invalid_timestamp(value):
    inputs = valid_inputs()
    inputs["occurred_at"] = value
    with pytest.raises(AuditValidationError):
        build_audit_event(**inputs)


@pytest.mark.parametrize(
    "value",
    [
        "2024-02-29T00:00:00Z",
        "0001-01-01T00:00:00Z",
        "9999-12-31T23:59:59Z",
    ],
)
def test_accept_valid_calendar_boundaries(value):
    inputs = valid_inputs()
    inputs["occurred_at"] = value
    assert build_audit_event(**inputs)["occurred_at"] == value
