"""Pure simulator execution-event construction and validation."""

import re
from datetime import datetime
from typing import Literal, TypedDict, cast
from uuid import UUID


class ExecutionAuditValidationError(ValueError):
    """An input does not satisfy the execution-event contract."""


class ExecutionEvent(TypedDict):
    schema_version: Literal[1]
    event_type: Literal["simulator_execution"]
    event_id: str
    execution_id: str
    policy_event_id: str
    occurred_at: str
    principal_id: Literal["reliability-reader"]
    tool: Literal["fixture_store"]
    operation: Literal["READ_ITEM"]
    status: Literal["started", "completed", "failed"]
    reason_code: Literal[
        "SIMULATION_STARTED",
        "SIMULATION_COMPLETED",
        "SIMULATOR_ERROR",
        "INVALID_RESULT",
    ]


_EVENT_KEYS = frozenset(ExecutionEvent.__annotations__)
_STATUS_REASON_PAIRS = frozenset(
    {
        ("started", "SIMULATION_STARTED"),
        ("completed", "SIMULATION_COMPLETED"),
        ("failed", "SIMULATOR_ERROR"),
        ("failed", "INVALID_RESULT"),
    }
)
_UTC_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def _valid_uuid(value: str) -> bool:
    try:
        parsed = UUID(value)
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == value


def validate_execution_event(event: object) -> ExecutionEvent:
    """Return a fresh validated record without proving the activity occurred."""
    if type(event) is not dict or event.keys() != _EVENT_KEYS:
        raise ExecutionAuditValidationError("Invalid execution event")

    if type(event["schema_version"]) is not int or event["schema_version"] != 1:
        raise ExecutionAuditValidationError("Invalid execution event")
    if any(
        type(event[key]) is not str for key in _EVENT_KEYS if key != "schema_version"
    ):
        raise ExecutionAuditValidationError("Invalid execution event")

    if (
        event["event_type"] != "simulator_execution"
        or event["principal_id"] != "reliability-reader"
        or event["tool"] != "fixture_store"
        or event["operation"] != "READ_ITEM"
    ):
        raise ExecutionAuditValidationError("Invalid execution event")
    if (event["status"], event["reason_code"]) not in _STATUS_REASON_PAIRS:
        raise ExecutionAuditValidationError("Invalid execution event")

    for key in ("event_id", "execution_id", "policy_event_id"):
        if not _valid_uuid(event[key]):
            raise ExecutionAuditValidationError("Invalid execution event")

    timestamp = event["occurred_at"]
    if _UTC_TIMESTAMP.fullmatch(timestamp) is None:
        raise ExecutionAuditValidationError("Invalid execution event")
    try:
        datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ExecutionAuditValidationError("Invalid execution event") from None

    return cast(ExecutionEvent, dict(event))


def build_execution_event(
    *,
    event_id: object,
    execution_id: object,
    policy_event_id: object,
    occurred_at: object,
    principal_id: object,
    tool: object,
    operation: object,
    status: object,
    reason_code: object,
) -> ExecutionEvent:
    """Build a record from explicit context without I/O or generated values."""
    return validate_execution_event(
        {
            "schema_version": 1,
            "event_type": "simulator_execution",
            "event_id": event_id,
            "execution_id": execution_id,
            "policy_event_id": policy_event_id,
            "occurred_at": occurred_at,
            "principal_id": principal_id,
            "tool": tool,
            "operation": operation,
            "status": status,
            "reason_code": reason_code,
        }
    )
