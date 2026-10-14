"""Contract tests for execution-event text delivery."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import StringIO
from threading import Barrier
from uuid import UUID

import pytest

from gateway.execution_audit import ExecutionAuditValidationError
from gateway.execution_audit_receiver import create_execution_audit_receiver


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


class TrackingStream(StringIO):
    def __init__(self):
        super().__init__()
        self.calls = []

    def write(self, text):
        self.calls.append("write")
        return super().write(text)

    def flush(self):
        self.calls.append("flush")
        super().flush()


@pytest.mark.parametrize(
    "status,reason",
    [
        ("started", "SIMULATION_STARTED"),
        ("completed", "SIMULATION_COMPLETED"),
        ("failed", "SIMULATOR_ERROR"),
        ("failed", "INVALID_RESULT"),
    ],
)
def test_exact_delivery_returns_none_without_mutating_input(status, reason):
    record = event()
    record.update(status=status, reason_code=reason)
    original = deepcopy(record)
    stream = TrackingStream()

    result = create_execution_audit_receiver(stream)(record)

    assert result is None
    assert (
        stream.getvalue()
        == json.dumps(original, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    )
    assert stream.calls == ["write", "flush"]
    assert record == original
    assert not stream.closed


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"event_id": "invalid"},
        {"occurred_at": "2026-02-29T00:00:00Z"},
        {"reason_code": "SIMULATION_COMPLETED"},
        {"credentials": "private"},
        {"operation": "WRITE_ITEM"},
    ],
)
def test_invalid_records_do_not_touch_output(change):
    record = event()
    record.update(change)
    original = deepcopy(record)
    stream = TrackingStream()

    with pytest.raises(ExecutionAuditValidationError):
        create_execution_audit_receiver(stream)(record)

    assert stream.calls == []
    assert stream.getvalue() == ""
    assert record == original
    assert not stream.closed


def test_serialization_failure_does_not_touch_output(monkeypatch):
    failure = ValueError("synthetic serialization failure")

    def fail_serialization(*args, **kwargs):
        raise failure

    monkeypatch.setattr(
        "gateway.execution_audit_receiver.json.dumps", fail_serialization
    )
    record = event()
    original = deepcopy(record)
    stream = TrackingStream()

    with pytest.raises(ValueError) as caught:
        create_execution_audit_receiver(stream)(record)

    assert caught.value is failure
    assert stream.calls == []
    assert stream.getvalue() == ""
    assert record == original
    assert not stream.closed


@pytest.mark.parametrize(
    "count_kind", ["none", "boolean", "float", "negative", "zero", "short", "long"]
)
def test_invalid_write_counts_fail_without_flush_or_retry(count_kind):
    class CountStream(TrackingStream):
        def write(self, text):
            self.calls.append("write")
            counts = {
                "none": None,
                "boolean": True,
                "float": float(len(text)),
                "negative": -1,
                "zero": 0,
                "short": len(text) - 1,
                "long": len(text) + 1,
            }
            if count_kind == "short":
                StringIO.write(self, text[:-1])
            return counts[count_kind]

    stream = CountStream()
    with pytest.raises(OSError, match="^Incomplete execution event write$"):
        create_execution_audit_receiver(stream)(event())

    assert stream.calls == ["write"]
    assert not stream.closed
    if count_kind == "short":
        assert stream.getvalue()
        assert not stream.getvalue().endswith("\n")
    else:
        assert stream.getvalue() == ""


@pytest.mark.parametrize("stage", ["write", "flush"])
def test_output_exceptions_propagate_without_retry_or_close(stage):
    failure = OSError("synthetic private output failure")

    class FailingStream(TrackingStream):
        def write(self, text):
            if stage == "write":
                self.calls.append("write")
                raise failure
            return super().write(text)

        def flush(self):
            self.calls.append("flush")
            raise failure

    stream = FailingStream()
    record = event()
    original = deepcopy(record)

    with pytest.raises(OSError) as caught:
        create_execution_audit_receiver(stream)(record)

    assert caught.value is failure
    assert record == original
    assert not stream.closed
    if stage == "write":
        assert stream.calls == ["write"]
        assert stream.getvalue() == ""
    else:
        assert stream.calls == ["write", "flush"]
        assert json.loads(stream.getvalue()) == original
        assert stream.getvalue().endswith("\n")


def test_concurrent_calls_keep_write_and_flush_together():
    class YieldingStream(TrackingStream):
        def write(self, text):
            count = super().write(text)
            time.sleep(0.005)
            return count

    workers = 8
    barrier = Barrier(workers)
    stream = YieldingStream()
    receive = create_execution_audit_receiver(stream)
    records = []
    for index in range(workers):
        record = event()
        record["event_id"] = str(UUID(int=index + 1, version=4))
        records.append(record)

    def deliver(record):
        barrier.wait(timeout=5)
        return receive(record)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(deliver, record) for record in records]
        assert [future.result(timeout=10) for future in futures] == [None] * workers

    lines = stream.getvalue().splitlines()
    assert len(lines) == workers
    delivered = [json.loads(line) for line in lines]
    assert {record["event_id"] for record in delivered} == {
        record["event_id"] for record in records
    }
    expected = {record["event_id"]: record for record in records}
    for record in delivered:
        assert record == expected[record["event_id"]]
    assert stream.calls == ["write", "flush"] * workers
    assert not stream.closed
