"""Acceptance tests composing the real API and JSON-lines receiver."""

import json
from io import StringIO
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from gateway.api import create_app
from gateway.audit import build_audit_event
from gateway.audit_receiver import create_audit_receiver

TOKEN = "synthetic_composition_credential_1234567890"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}
EVENT_KEYS = {
    "schema_version",
    "event_type",
    "event_id",
    "occurred_at",
    "principal_id",
    "decision",
    "reason_code",
    "policy_version",
    "execution_status",
}


@pytest.fixture(autouse=True)
def configuration(monkeypatch):
    monkeypatch.setenv("GATEWAY_API_TOKEN", TOKEN)


def payload():
    return {
        "tool": "fixture_store",
        "operation": "READ_ITEM",
        "arguments": {"item_id": 1},
    }


@pytest.mark.parametrize(
    ("change", "status", "decision", "reason"),
    [
        ({}, 200, "ALLOW", "POLICY_ALLOW"),
        ({"tool": "other_tool"}, 200, "DENY", "UNKNOWN_TOOL"),
        ({"operation": "WRITE_ITEM"}, 200, "DENY", "UNKNOWN_OPERATION"),
        ({"arguments": {"item_id": 0}}, 422, "DENY", "INVALID_REQUEST"),
    ],
)
def test_real_components_emit_one_valid_event(change, status, decision, reason):
    stream = StringIO()
    request = payload()
    request.update(change)
    app = create_app(audit_sink=create_audit_receiver(stream))

    with TestClient(app) as client:
        response = client.post("/v1/policy/evaluate", headers=HEADERS, json=request)

    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    audit = json.loads(lines[0])
    assert set(audit) == EVENT_KEYS
    assert audit["principal_id"] == "reliability-reader"
    assert audit["decision"] == decision
    assert audit["reason_code"] == reason
    assert audit["execution_status"] == "not_executed"
    identifier = UUID(audit["event_id"])
    assert identifier.version == 4
    assert str(identifier) == audit["event_id"]

    result = {
        key: audit[key]
        for key in ("decision", "reason_code", "policy_version", "execution_status")
    }
    assert (
        build_audit_event(
            result,
            principal_id=audit["principal_id"],
            event_id=audit["event_id"],
            occurred_at=audit["occurred_at"],
        )
        == audit
    )
    assert response.status_code == status
    assert response.json() == (
        result if status == 200 else {"error": {"code": "INVALID_REQUEST"}}
    )
    assert TOKEN not in stream.getvalue()
    assert "arguments" not in stream.getvalue()
    assert "item_id" not in stream.getvalue()
    assert not stream.closed


@pytest.mark.parametrize(
    ("headers", "body", "status"),
    [
        ({}, b"{}", 401),
        ({**HEADERS, "Authorization": "Bearer invalid"}, b"{}", 401),
        (HEADERS, b"{", 422),
    ],
)
def test_pre_policy_failure_emits_nothing(headers, body, status):
    stream = StringIO()
    app = create_app(audit_sink=create_audit_receiver(stream))
    with TestClient(app) as client:
        response = client.post("/v1/policy/evaluate", headers=headers, content=body)
    assert response.status_code == status
    assert stream.getvalue() == ""
    assert not stream.closed


@pytest.mark.parametrize("failure", ["write", "short", "flush"])
@pytest.mark.parametrize("invalid_request", [False, True])
def test_real_stream_failure_replaces_policy_response(failure, invalid_request):
    class FailingStream(StringIO):
        def write(self, text):
            if failure == "write":
                raise OSError("synthetic-private-stream-error")
            if failure == "short":
                return super().write(text[:-1])
            return super().write(text)

        def flush(self):
            if failure == "flush":
                raise OSError("synthetic-private-stream-error")
            return super().flush()

    stream = FailingStream()
    request = payload()
    if invalid_request:
        request["arguments"] = {"item_id": 0}
    app = create_app(audit_sink=create_audit_receiver(stream))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/v1/policy/evaluate", headers=HEADERS, json=request)

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "INTERNAL_ERROR"}}
    assert not stream.closed
    output = stream.getvalue()
    if failure == "write":
        assert output == ""
    else:
        assert json.loads(output)["execution_status"] == "not_executed"
        assert output.endswith("\n") is (failure == "flush")
    assert TOKEN not in output
    assert "synthetic-private-stream-error" not in output
