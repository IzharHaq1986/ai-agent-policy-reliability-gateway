"""Local authenticated HTTP boundary for the non-executing policy core."""

import inspect
import json
import os
import re
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import NoReturn
from uuid import uuid4

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from gateway.audit import AuditEvent, build_audit_event
from gateway.policy import evaluate_request

_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}")
_BEARER = re.compile(r"Bearer ([A-Za-z0-9_-]{32,128})", re.IGNORECASE)
_JSON_TYPE = re.compile(
    r'application/json(?:\s*;\s*charset\s*=\s*(?:utf-8|"utf-8"))?\s*',
    re.IGNORECASE,
)
_MAX_BODY_BYTES = 4096


def _error(status: int, code: str) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(
        {"error": {"code": code}},
        status_code=status,
        headers=headers,
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    raise ValueError("Non-finite JSON number")


def create_app(*, audit_sink: Callable[[AuditEvent], None] | None = None) -> FastAPI:
    """Create the local API with optional trusted audit delivery."""
    if audit_sink is not None:
        if (
            not callable(audit_sink)
            or inspect.iscoroutinefunction(audit_sink)
            or inspect.iscoroutinefunction(type(audit_sink).__call__)
        ):
            raise RuntimeError("Invalid audit sink configuration")

    token = os.environ.get("GATEWAY_API_TOKEN")
    if token is None or _TOKEN.fullmatch(token) is None:
        raise RuntimeError("Invalid gateway credential configuration")

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return _error(500, "INTERNAL_ERROR")

    @app.post("/v1/policy/evaluate", response_model=None)
    async def evaluate(request: Request) -> JSONResponse:
        authorization = request.headers.getlist("authorization")
        if len(authorization) != 1:
            return _error(401, "UNAUTHENTICATED")

        match = _BEARER.fullmatch(authorization[0])
        if match is None or not secrets.compare_digest(match.group(1), token):
            return _error(401, "UNAUTHENTICATED")

        content_types = request.headers.getlist("content-type")
        if len(content_types) != 1 or _JSON_TYPE.fullmatch(content_types[0]) is None:
            return _error(415, "UNSUPPORTED_MEDIA_TYPE")

        body = bytearray()
        try:
            async for chunk in request.stream():
                if len(chunk) > _MAX_BODY_BYTES - len(body):
                    return _error(413, "BODY_TOO_LARGE")
                body.extend(chunk)
        except ClientDisconnect:
            return _error(422, "INVALID_REQUEST")

        try:
            payload: object = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except (ValueError, RecursionError):
            return _error(422, "INVALID_REQUEST")

        result = evaluate_request(payload, principal_id="reliability-reader")
        if audit_sink is not None:
            event = build_audit_event(
                result,
                principal_id="reliability-reader",
                event_id=str(uuid4()),
                occurred_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            checked_sink: Callable[[AuditEvent], object] = audit_sink
            delivery_result = await run_in_threadpool(checked_sink, event)
            if delivery_result is not None:
                raise RuntimeError("Invalid audit sink result")
        if result["reason_code"] == "INVALID_REQUEST":
            return _error(422, "INVALID_REQUEST")

        return JSONResponse(result)

    return app
