"""Validated JSON-lines audit delivery to a caller-owned text stream."""

import json
from collections.abc import Callable
from threading import Lock
from typing import TextIO

from gateway.audit import AuditEvent, AuditValidationError, build_audit_event

_EVENT_KEYS = frozenset(AuditEvent.__annotations__)


def create_audit_receiver(stream: TextIO) -> Callable[[AuditEvent], None]:
    """Create a synchronous receiver without owning or closing the stream.

    Successful write and flush do not establish durable persistence.
    Coordination applies only to calls through this receiver instance.
    """
    lock = Lock()

    def receive(event: AuditEvent) -> None:
        if type(event) is not dict or event.keys() != _EVENT_KEYS:
            raise AuditValidationError("Invalid audit event fields")
        if type(event["schema_version"]) is not int or event["schema_version"] != 1:
            raise AuditValidationError("Unsupported audit schema version")
        if (
            type(event["event_type"]) is not str
            or event["event_type"] != "policy_decision"
        ):
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
        line = (
            json.dumps(
                validated,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )

        with lock:
            written = stream.write(line)
            if type(written) is not int or written != len(line):
                raise OSError("Incomplete audit event write")
            stream.flush()

    return receive
