"""Pure validation and generation of non-executing policy audit events."""

import re
from datetime import datetime
from typing import Literal, cast, get_args
from uuid import UUID

from gateway.policy import PolicyResult, ReasonCode


class AuditValidationError(ValueError):
    """An input does not satisfy the audit event contract."""


class AuditEvent(PolicyResult):
    schema_version: Literal[1]
    event_type: Literal["policy_decision"]
    event_id: str
    occurred_at: str
    principal_id: Literal["reliability-reader"]


_EVENT_KEYS = frozenset(AuditEvent.__annotations__)
_RESULT_KEYS = frozenset(PolicyResult.__annotations__)
_REASON_CODES = frozenset(get_args(ReasonCode))
_UTC_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def validate_policy_result(policy_result: object) -> PolicyResult:
    """Validate the existing result contract without generating an event."""
    if type(policy_result) is not dict:
        raise AuditValidationError("Invalid policy result")
    if policy_result.keys() != _RESULT_KEYS:
        raise AuditValidationError("Invalid policy result fields")
    if any(type(value) is not str for value in policy_result.values()):
        raise AuditValidationError("Invalid policy result field type")

    decision = policy_result["decision"]
    reason_code = policy_result["reason_code"]
    if decision not in {"ALLOW", "DENY"} or reason_code not in _REASON_CODES:
        raise AuditValidationError("Unsupported policy decision")
    if (decision == "ALLOW") != (reason_code == "POLICY_ALLOW"):
        raise AuditValidationError("Inconsistent policy decision")
    if policy_result["policy_version"] != "policy-core-v1":
        raise AuditValidationError("Unsupported policy version")
    if policy_result["execution_status"] != "not_executed":
        raise AuditValidationError("Unsupported execution status")
    return cast(PolicyResult, policy_result)


def build_audit_event(
    policy_result: object,
    *,
    principal_id: object,
    event_id: object,
    occurred_at: object,
) -> AuditEvent:
    """Build an event from explicit trusted context and a validated result.

    This function generates no identity or timestamp, performs no I/O,
    and does not establish authenticity, persistence, or authorization.
    """
    result = validate_policy_result(policy_result)

    if type(principal_id) is not str or principal_id != "reliability-reader":
        raise AuditValidationError("Unsupported principal identity")

    if type(event_id) is not str:
        raise AuditValidationError("Invalid event ID")
    try:
        parsed_id = UUID(event_id)
    except ValueError:
        raise AuditValidationError("Invalid event ID") from None
    if parsed_id.version != 4 or str(parsed_id) != event_id:
        raise AuditValidationError("Invalid event ID")

    if type(occurred_at) is not str:
        raise AuditValidationError("Invalid occurrence timestamp")
    if _UTC_TIMESTAMP.fullmatch(occurred_at) is None:
        raise AuditValidationError("Invalid occurrence timestamp")
    try:
        datetime.strptime(occurred_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise AuditValidationError("Invalid occurrence timestamp") from None

    return {
        "schema_version": 1,
        "event_type": "policy_decision",
        "event_id": event_id,
        "occurred_at": occurred_at,
        "principal_id": "reliability-reader",
        "decision": result["decision"],
        "reason_code": result["reason_code"],
        "policy_version": result["policy_version"],
        "execution_status": result["execution_status"],
    }


def validate_audit_event(event: object) -> AuditEvent:
    """Validate an exact audit event and return a fresh copy without I/O."""
    if type(event) is not dict or event.keys() != _EVENT_KEYS:
        raise AuditValidationError("Invalid audit event fields")
    if type(event["schema_version"]) is not int or event["schema_version"] != 1:
        raise AuditValidationError("Unsupported audit schema version")
    if type(event["event_type"]) is not str or event["event_type"] != "policy_decision":
        raise AuditValidationError("Unsupported audit event type")

    validated = build_audit_event(
        {
            "decision": event["decision"],
            "reason_code": event["reason_code"],
            "policy_version": event["policy_version"],
            "execution_status": event["execution_status"],
        },
        principal_id=event["principal_id"],
        event_id=event["event_id"],
        occurred_at=event["occurred_at"],
    )
    return validated
