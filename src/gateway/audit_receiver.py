"""Validated JSON-lines audit delivery to a caller-owned text stream."""

import json
from collections.abc import Callable
from threading import Lock
from typing import TextIO

from gateway.audit import AuditEvent, validate_audit_event


def create_audit_receiver(stream: TextIO) -> Callable[[AuditEvent], None]:
    """Create a synchronous receiver without owning or closing the stream.

    Successful write and flush do not establish durable persistence.
    Coordination applies only to calls through this receiver instance.
    """
    lock = Lock()

    def receive(event: AuditEvent) -> None:
        validated = validate_audit_event(event)
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
