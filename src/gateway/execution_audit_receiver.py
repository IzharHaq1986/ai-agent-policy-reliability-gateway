"""Validated execution-event delivery to a caller-owned text stream."""

import json
from collections.abc import Callable
from threading import Lock
from typing import TextIO

from gateway.execution_audit import ExecutionEvent, validate_execution_event


def create_execution_audit_receiver(
    stream: TextIO,
) -> Callable[[ExecutionEvent], None]:
    """Create a synchronous receiver without owning or closing the stream.

    Successful write and flush do not establish durable persistence.
    Coordination applies only to calls through this receiver instance.
    Failed delivery may leave partial or complete output.
    """
    lock = Lock()

    def receive(event: ExecutionEvent) -> None:
        validated = validate_execution_event(event)
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
                raise OSError("Incomplete execution event write")
            stream.flush()

    return receive
