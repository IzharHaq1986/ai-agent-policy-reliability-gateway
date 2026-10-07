"""Acceptance tests for optional API policy audit delivery."""

import threading
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

import gateway.api as api
from gateway.audit import build_audit_event

TOKEN = "synthetic_test_credential_1234567890"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}
EVENT_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
TIMESTAMP = "2026-10-07T10:00:00Z"


@pytest.fixture(autouse=True)
def configuration(monkeypatch):
    monkeypatch.setenv("GATEWAY_API_TOKEN", TOKEN)


def payload():
    return {
        "tool": "fixture_store",
        "operation": "READ_ITEM",
        "arguments": {"item_id": 1},
    }


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        assert tz is UTC
        return cls(2026, 10, 7, 10, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("change", "status", "decision", "reason"),
    [
        ({}, 200, "ALLOW", "POLICY_ALLOW"),
        ({"tool": "other_tool"}, 200, "DENY", "UNKNOWN_TOOL"),
        ({"operation": "WRITE_ITEM"}, 200, "DENY", "UNKNOWN_OPERATION"),
        ({"arguments": {"item_id": 0}}, 422, "DENY", "INVALID_REQUEST"),
        (
            {"principal_id": "request-owned", "event_id": "request-owned"},
            422,
            "DENY",
            "INVALID_REQUEST",
        ),
    ],
)
def test_deliver_exact_trusted_event(monkeypatch, change, status, decision, reason):
    monkeypatch.setattr(api, "uuid4", lambda: EVENT_ID)
    monkeypatch.setattr(api, "datetime", FixedDatetime)
    events = []
    app = api.create_app(audit_sink=events.append)
    request = payload()
    request.update(change)

    with TestClient(app) as client:
        response = client.post("/v1/policy/evaluate", headers=HEADERS, json=request)

    assert response.status_code == status
    assert events == [
        {
            "schema_version": 1,
            "event_type": "policy_decision",
            "event_id": str(EVENT_ID),
            "occurred_at": TIMESTAMP,
            "principal_id": "reliability-reader",
            "decision": decision,
            "reason_code": reason,
            "policy_version": "policy-core-v1",
            "execution_status": "not_executed",
        }
    ]
    event = events[0]
    result = {
        field: event[field]
        for field in ("decision", "reason_code", "policy_version", "execution_status")
    }
    assert (
        build_audit_event(
            result,
            principal_id=event["principal_id"],
            event_id=event["event_id"],
            occurred_at=event["occurred_at"],
        )
        == event
    )
    if status == 200:
        assert response.json() == result
    else:
        assert response.json() == {"error": {"code": "INVALID_REQUEST"}}


def forbidden_builder(*args, **kwargs):
    pytest.fail("Audit builder must not run")


@pytest.mark.parametrize(
    ("headers", "body", "status"),
    [
        ({}, b"{}", 401),
        ({**HEADERS, "Authorization": "Bearer invalid"}, b"{}", 401),
        ({**HEADERS, "Content-Type": "text/plain"}, b"{}", 415),
        (HEADERS, b"x" * 4097, 413),
        (HEADERS, b"{", 422),
        (HEADERS, b'{"key": 1, "key": 2}', 422),
        (HEADERS, b'{"value": NaN}', 422),
    ],
)
def test_pre_policy_failures_emit_nothing(monkeypatch, headers, body, status):
    monkeypatch.setattr(api, "build_audit_event", forbidden_builder)
    events = []
    with TestClient(api.create_app(audit_sink=events.append)) as client:
        response = client.post("/v1/policy/evaluate", headers=headers, content=body)
    assert response.status_code == status
    assert events == []


@pytest.mark.parametrize("failure_source", ["builder", "sink", "policy"])
def test_failures_return_generic_error(monkeypatch, failure_source):
    events = []

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic-sensitive-detail")

    sink = events.append
    if failure_source == "sink":
        sink = fail
    elif failure_source == "builder":
        monkeypatch.setattr(api, "build_audit_event", fail)
    else:
        monkeypatch.setattr(api, "evaluate_request", fail)

    with TestClient(
        api.create_app(audit_sink=sink), raise_server_exceptions=False
    ) as client:
        response = client.post("/v1/policy/evaluate", headers=HEADERS, json=payload())

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "INTERNAL_ERROR"}}
    assert events == []


def test_no_sink_preserves_existing_behavior(monkeypatch):
    monkeypatch.setattr(api, "build_audit_event", forbidden_builder)
    with TestClient(api.create_app()) as client:
        response = client.post("/v1/policy/evaluate", headers=HEADERS, json=payload())
    assert response.status_code == 200
    assert response.json()["execution_status"] == "not_executed"


@pytest.mark.parametrize("sink", [False, 1, "sink", [], {}])
def test_invalid_sink_prevents_creation(sink):
    with pytest.raises(RuntimeError, match="Invalid audit sink configuration"):
        api.create_app(audit_sink=sink)


def test_async_sink_prevents_creation():
    async def sink(event):
        return None

    with pytest.raises(RuntimeError, match="Invalid audit sink configuration"):
        api.create_app(audit_sink=sink)


def test_invalid_sink_return_fails_delivery():
    def sink(event):
        return "not-None"

    with TestClient(
        api.create_app(audit_sink=sink), raise_server_exceptions=False
    ) as client:
        response = client.post("/v1/policy/evaluate", headers=HEADERS, json=payload())
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "INTERNAL_ERROR"}}


def test_sink_runs_outside_event_loop_thread(monkeypatch):
    policy_threads = []
    sink_threads = []
    original = api.evaluate_request

    def evaluate(request, *, principal_id):
        policy_threads.append(threading.get_ident())
        return original(request, principal_id=principal_id)

    def sink(event):
        sink_threads.append(threading.get_ident())

    monkeypatch.setattr(api, "evaluate_request", evaluate)
    with TestClient(api.create_app(audit_sink=sink)) as client:
        response = client.post("/v1/policy/evaluate", headers=HEADERS, json=payload())

    assert response.status_code == 200
    assert len(policy_threads) == len(sink_threads) == 1
    assert policy_threads[0] != sink_threads[0]


@pytest.mark.parametrize("invalid_request", [False, True])
@pytest.mark.parametrize("failure_source", ["builder", "sink"])
def test_delivery_failure_replaces_normal_response(
    monkeypatch, invalid_request, failure_source
):
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic-sensitive-detail")

    if failure_source == "builder":
        monkeypatch.setattr(api, "build_audit_event", fail)

        def sink(event):
            return None
    else:
        sink = fail

    request = payload()
    if invalid_request:
        request["arguments"] = {"item_id": 0}

    with TestClient(
        api.create_app(audit_sink=sink), raise_server_exceptions=False
    ) as client:
        response = client.post("/v1/policy/evaluate", headers=HEADERS, json=request)

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "INTERNAL_ERROR"}}


def test_async_callable_object_prevents_creation():
    class AsyncSink:
        async def __call__(self, event):
            return None

    with pytest.raises(RuntimeError, match="Invalid audit sink configuration"):
        api.create_app(audit_sink=AsyncSink())


def test_sink_failure_after_acceptance():
    events = []

    def sink(event):
        events.append(event)
        raise RuntimeError("synthetic-sensitive-detail")

    with TestClient(
        api.create_app(audit_sink=sink), raise_server_exceptions=False
    ) as client:
        response = client.post("/v1/policy/evaluate", headers=HEADERS, json=payload())

    assert len(events) == 1
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "INTERNAL_ERROR"}}
