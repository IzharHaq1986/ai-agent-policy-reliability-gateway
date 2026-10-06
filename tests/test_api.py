"""Acceptance tests for the authenticated, non-executing API."""

from collections.abc import Iterator

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from gateway import api
from gateway.policy import PolicyResult

TOKEN = "SyntheticTestCredential_" + "x" * 32
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
BODY = {
    "tool": "fixture_store",
    "operation": "READ_ITEM",
    "arguments": {"item_id": 1},
}
URL = "/v1/policy/evaluate"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("GATEWAY_API_TOKEN", TOKEN)
    with TestClient(api.create_app()) as test_client:
        yield test_client


@pytest.mark.parametrize("token", [None, "", "short", "x" * 129, "é" * 32])
def test_invalid_configuration_prevents_creation(
    monkeypatch: pytest.MonkeyPatch, token: str | None
) -> None:
    if token is None:
        monkeypatch.delenv("GATEWAY_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("GATEWAY_API_TOKEN", token)
    with pytest.raises(RuntimeError, match="Invalid gateway credential configuration"):
        api.create_app()


@pytest.mark.parametrize(
    "authorization",
    [None, "", "Basic abc", "Bearer short", f"Bearer {'z' * 32}", f"Bearer {TOKEN} "],
)
def test_authentication_precedes_body_and_policy(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    authorization: str | None,
) -> None:
    def prohibited(*args: object, **kwargs: object) -> None:
        raise AssertionError("Unauthenticated request crossed the boundary")

    monkeypatch.setattr(Request, "stream", prohibited)
    monkeypatch.setattr(api, "evaluate_request", prohibited)
    headers = {} if authorization is None else {"Authorization": authorization}
    response = client.post(URL, headers=headers, content=b"{" * 5000)
    assert response.status_code == 401
    assert response.json() == {"error": {"code": "UNAUTHENTICATED"}}
    assert response.headers["www-authenticate"] == "Bearer"
    assert TOKEN not in response.text


def test_duplicate_authorization_is_rejected(client: TestClient) -> None:
    response = client.post(
        URL,
        headers=[
            ("Authorization", f"Bearer {TOKEN}"),
            ("Authorization", f"Bearer {TOKEN}"),
        ],
        json=BODY,
    )
    assert response.status_code == 401


def test_allowed_request_uses_server_identity(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    identities: list[object] = []
    original = api.evaluate_request

    def observed(request: object, *, principal_id: object) -> PolicyResult:
        identities.append(principal_id)
        return original(request, principal_id=principal_id)

    monkeypatch.setattr(api, "evaluate_request", observed)
    response = client.post(
        URL, headers={**HEADERS, "X-Principal-ID": "attacker"}, json=BODY
    )
    assert identities == ["reliability-reader"]
    assert response.status_code == 200
    assert response.json() == {
        "decision": "ALLOW",
        "reason_code": "POLICY_ALLOW",
        "policy_version": "policy-core-v1",
        "execution_status": "not_executed",
    }


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("tool", "unknown", "UNKNOWN_TOOL"),
        ("operation", "WRITE_ITEM", "UNKNOWN_OPERATION"),
    ],
)
def test_valid_policy_denials(
    client: TestClient, field: str, value: str, reason: str
) -> None:
    response = client.post(URL, headers=HEADERS, json={**BODY, field: value})
    assert response.status_code == 200
    assert response.json() == {
        "decision": "DENY",
        "reason_code": reason,
        "policy_version": "policy-core-v1",
        "execution_status": "not_executed",
    }


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"{",
        b"\xff",
        b'{"tool":"fixture_store","tool":"unknown"}',
        b'{"arguments":{"item_id":1,"item_id":2}}',
        b'{"item_id":NaN}',
        b'{"item_id":Infinity}',
        b'{"item_id":-Infinity}',
    ],
)
def test_invalid_json_never_reaches_policy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    def prohibited(*args: object, **kwargs: object) -> None:
        raise AssertionError("Invalid JSON reached policy")

    monkeypatch.setattr(api, "evaluate_request", prohibited)
    response = client.post(
        URL, headers={**HEADERS, "Content-Type": "application/json"}, content=body
    )
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "INVALID_REQUEST"}}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {**BODY, "principal_id": "reliability-reader"},
        {**BODY, "arguments": {"item_id": True}},
        {**BODY, "arguments": {"item_id": 1001}},
    ],
)
def test_invalid_policy_input(client: TestClient, body: object) -> None:
    response = client.post(URL, headers=HEADERS, json=body)
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "INVALID_REQUEST"}}


@pytest.mark.parametrize(
    "media_type", ["text/plain", "application/json; charset=latin-1"]
)
def test_unsupported_media_type(client: TestClient, media_type: str) -> None:
    response = client.post(
        URL, headers={**HEADERS, "Content-Type": media_type}, content=b"{}"
    )
    assert response.status_code == 415
    assert response.json() == {"error": {"code": "UNSUPPORTED_MEDIA_TYPE"}}


def test_oversized_body(client: TestClient) -> None:
    response = client.post(
        URL,
        headers={**HEADERS, "Content-Type": "application/json"},
        content=b" " * 4097,
    )
    assert response.status_code == 413
    assert response.json() == {"error": {"code": "BODY_TOO_LARGE"}}


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_documentation_endpoints_disabled(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404


def test_unexpected_failure_returns_generic_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATEWAY_API_TOKEN", TOKEN)

    def failure(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Synthetic internal detail")

    monkeypatch.setattr(api, "evaluate_request", failure)
    with TestClient(api.create_app(), raise_server_exceptions=False) as client:
        response = client.post(URL, headers=HEADERS, json=BODY)
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "INTERNAL_ERROR"}}
    assert "Synthetic internal detail" not in response.text
