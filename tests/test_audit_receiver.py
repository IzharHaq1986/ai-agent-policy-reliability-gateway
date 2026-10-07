"""Acceptance tests for validated JSON-lines audit delivery."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import StringIO
from uuid import UUID

import pytest

from gateway.audit import AuditValidationError, build_audit_event
from gateway.audit_receiver import create_audit_receiver


def event(decision="ALLOW", reason="POLICY_ALLOW"):
    return build_audit_event(
        {
            "decision": decision,
            "reason_code": reason,
            "policy_version": "policy-core-v1",
            "execution_status": "not_executed",
        },
        principal_id="reliability-reader",
        event_id="550e8400-e29b-41d4-a716-446655440000",
        occurred_at="2026-10-07T10:00:00Z",
    )


class RecordingStream(StringIO):
    def __init__(self):
        super().__init__()
        self.operations = []

    def write(self, text):
        self.operations.append("write")
        return super().write(text)

    def flush(self):
        self.operations.append("flush")
        return super().flush()


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
def test_stable_serialization_and_input_preservation(decision, reason):
    stream = RecordingStream()
    receiver = create_audit_receiver(stream)
    supplied = event(decision, reason)
    original = deepcopy(supplied)

    assert receiver(supplied) is None
    first = stream.getvalue()
    assert (
        first
        == json.dumps(original, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    )
    assert json.loads(first) == original
    assert receiver(supplied) is None
    assert stream.getvalue() == first + first
    assert stream.operations == ["write", "flush", "write", "flush"]
    assert supplied == original
    assert not stream.closed


def assert_rejected_without_output(supplied):
    stream = RecordingStream()
    original = deepcopy(supplied)
    with pytest.raises(AuditValidationError):
        create_audit_receiver(stream)(supplied)
    assert stream.getvalue() == ""
    assert stream.operations == []
    assert supplied == original
    assert not stream.closed


@pytest.mark.parametrize("value", [None, [], (), "event", 1, True])
def test_reject_non_dictionary(value):
    assert_rejected_without_output(value)


@pytest.mark.parametrize("field", list(event()))
def test_reject_missing_field(field):
    supplied = event()
    del supplied[field]
    assert_rejected_without_output(supplied)


@pytest.mark.parametrize("field", ["token", "arguments", "headers", "error"])
def test_reject_extra_field(field):
    supplied = event()
    supplied[field] = "synthetic-private-content"
    assert_rejected_without_output(supplied)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("schema_version", "1"),
        ("schema_version", 2),
        ("event_type", None),
        ("event_type", "other_event"),
        ("principal_id", "other-reader"),
        ("event_id", "not-a-uuid"),
        ("occurred_at", "2026-02-29T00:00:00Z"),
        ("decision", "DENY"),
        ("reason_code", "UNKNOWN_REASON"),
        ("policy_version", "policy-core-v2"),
        ("execution_status", "executed"),
    ],
)
def test_reject_invalid_field(field, value):
    supplied = event()
    supplied[field] = value
    assert_rejected_without_output(supplied)


@pytest.mark.parametrize("failure", ["write", "short", "flush", "wrong_type"])
def test_stream_failures_propagate(failure):
    class FailingStream(RecordingStream):
        def write(self, text):
            if failure == "write":
                raise OSError("synthetic write failure")
            if failure == "short":
                return super().write(text[:-1])
            if failure == "wrong_type":
                return None
            return super().write(text)

        def flush(self):
            if failure == "flush":
                raise OSError("synthetic flush failure")
            return super().flush()

    stream = FailingStream()
    supplied = event()
    original = deepcopy(supplied)

    with pytest.raises(OSError):
        create_audit_receiver(stream)(supplied)

    assert supplied == original
    assert not stream.closed
    if failure in {"write", "wrong_type"}:
        assert stream.getvalue() == ""
    if failure == "short":
        assert not stream.getvalue().endswith("\n")
        assert stream.operations == ["write"]
    if failure == "flush":
        assert json.loads(stream.getvalue()) == original


def test_concurrent_calls_produce_complete_distinct_lines():
    stream = RecordingStream()
    receiver = create_audit_receiver(stream)
    events = []
    for index in range(40):
        supplied = event()
        supplied["event_id"] = str(UUID(int=index + 1, version=4))
        events.append(supplied)
    original = deepcopy(events)

    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(receiver, events))

    lines = stream.getvalue().splitlines()
    decoded = [json.loads(line) for line in lines]
    assert len(lines) == 40
    assert {item["event_id"] for item in decoded} == {
        item["event_id"] for item in events
    }
    assert sorted(decoded, key=lambda item: item["event_id"]) == sorted(
        events, key=lambda item: item["event_id"]
    )
    assert stream.operations == ["write", "flush"] * 40
    assert results == [None] * 40
    assert events == original
    assert not stream.closed
