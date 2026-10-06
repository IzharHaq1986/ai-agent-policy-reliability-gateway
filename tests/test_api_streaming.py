"""Direct ASGI tests for request-stream boundaries."""

import asyncio
import json

import pytest
from fastapi import FastAPI
from starlette.types import Message, Scope

from gateway import api

TOKEN = "SyntheticStreamingCredential_" + "x" * 32
VALID = json.dumps(
    {
        "tool": "fixture_store",
        "operation": "READ_ITEM",
        "arguments": {"item_id": 1},
    }
).encode()
EXACT_LIMIT = VALID + b" " * (4096 - len(VALID))


def invoke(
    app: FastAPI,
    headers: list[tuple[bytes, bytes]],
    events: list[Message],
) -> tuple[int, object, int]:
    sent: list[Message] = []
    reads = 0

    async def run() -> None:
        async def receive() -> Message:
            nonlocal reads
            if reads >= len(events):
                raise AssertionError("Unexpected additional body read")
            event = events[reads]
            reads += 1
            return event

        async def send(message: Message) -> None:
            sent.append(message)

        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/policy/evaluate",
            "raw_path": b"/v1/policy/evaluate",
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
        }
        await app(scope, receive, send)

    asyncio.run(run())
    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert len(starts) == 1
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    return starts[0]["status"], json.loads(body), reads


def authenticated_headers() -> list[tuple[bytes, bytes]]:
    return [
        (b"authorization", f"Bearer {TOKEN}".encode()),
        (b"content-type", b"application/json"),
    ]


@pytest.mark.parametrize(
    ("chunks", "length", "expected_status", "expected_reads"),
    [
        ([EXACT_LIMIT], None, 200, 1),
        ([EXACT_LIMIT[:2048], EXACT_LIMIT[2048:]], None, 200, 2),
        ([EXACT_LIMIT, b" "], None, 413, 2),
        ([b" " * 2048, b" " * 2049, b"x"], None, 413, 2),
        ([b" " * 4097], b"1", 413, 1),
        ([EXACT_LIMIT], b"4096", 200, 1),
    ],
)
def test_streamed_size_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    chunks: list[bytes],
    length: bytes | None,
    expected_status: int,
    expected_reads: int,
) -> None:
    monkeypatch.setenv("GATEWAY_API_TOKEN", TOKEN)
    headers = authenticated_headers()
    if length is not None:
        headers.append((b"content-length", length))
    events: list[Message] = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]
    status, body, reads = invoke(api.create_app(), headers, events)
    assert status == expected_status
    assert reads == expected_reads
    if status == 200:
        assert isinstance(body, dict)
        assert body["decision"] == "ALLOW"
        assert body["execution_status"] == "not_executed"
    else:
        assert body == {"error": {"code": "BODY_TOO_LARGE"}}


def test_unauthenticated_request_never_receives_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATEWAY_API_TOKEN", TOKEN)

    def prohibited(*args: object, **kwargs: object) -> None:
        raise AssertionError("Unauthenticated policy invocation")

    monkeypatch.setattr(api, "evaluate_request", prohibited)
    status, body, reads = invoke(api.create_app(), [], [])
    assert (status, body, reads) == (401, {"error": {"code": "UNAUTHENTICATED"}}, 0)


def test_unsupported_media_never_receives_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATEWAY_API_TOKEN", TOKEN)
    headers = [(b"authorization", f"Bearer {TOKEN}".encode())]
    status, body, reads = invoke(api.create_app(), headers, [])
    assert (status, body, reads) == (
        415,
        {"error": {"code": "UNSUPPORTED_MEDIA_TYPE"}},
        0,
    )


def test_disconnect_does_not_evaluate_partial_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATEWAY_API_TOKEN", TOKEN)

    def prohibited(*args: object, **kwargs: object) -> None:
        raise AssertionError("Partial body reached policy")

    monkeypatch.setattr(api, "evaluate_request", prohibited)
    events: list[Message] = [
        {"type": "http.request", "body": b"{", "more_body": True},
        {"type": "http.disconnect"},
    ]
    status, body, reads = invoke(api.create_app(), authenticated_headers(), events)
    assert (status, body, reads) == (422, {"error": {"code": "INVALID_REQUEST"}}, 2)
