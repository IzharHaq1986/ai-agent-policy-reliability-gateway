"""Explicit transactional PostgreSQL delivery of validated audit events."""

from collections.abc import Callable
from contextlib import suppress

import psycopg

from gateway.audit import AuditEvent, validate_audit_event

_INSERT = """
INSERT INTO gateway_audit.policy_decisions (
    schema_version, event_type, event_id, occurred_at, principal_id,
    decision, reason_code, policy_version, execution_status
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (event_id) DO NOTHING
RETURNING event_id
"""
_SELECT = """
SELECT schema_version, event_type, event_id, occurred_at, principal_id,
       decision, reason_code, policy_version, execution_status
FROM gateway_audit.policy_decisions
WHERE event_id = %s
"""


class AuditConflictError(ValueError):
    """An existing event ID identifies different audit content."""


def create_postgresql_audit_receiver(
    conninfo: str,
) -> Callable[[AuditEvent], None]:
    """Create an opt-in receiver using trusted connection configuration.

    Each invocation owns one connection. Timeouts do not establish a total
    deadline. Commit acknowledgement failures may leave a committed event.
    """
    if type(conninfo) is not str or not conninfo.strip():
        raise ValueError("Invalid audit database configuration")

    def receive(event: AuditEvent) -> None:
        validated = validate_audit_event(event)
        values = (
            validated["schema_version"],
            validated["event_type"],
            validated["event_id"],
            validated["occurred_at"],
            validated["principal_id"],
            validated["decision"],
            validated["reason_code"],
            validated["policy_version"],
            validated["execution_status"],
        )

        connection = psycopg.connect(conninfo, connect_timeout=5)
        try:
            connection.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
            connection.execute("SET LOCAL synchronous_commit = on")
            connection.execute("SET LOCAL lock_timeout = '2s'")
            connection.execute("SET LOCAL statement_timeout = '5s'")

            inserted = connection.execute(_INSERT, values).fetchone()
            if inserted is None:
                stored = connection.execute(
                    _SELECT, (validated["event_id"],)
                ).fetchone()
                if stored != values:
                    raise AuditConflictError("Conflicting audit event ID")

            connection.commit()
        except BaseException:
            # Cleanup must not replace the original delivery failure.
            with suppress(Exception):
                connection.rollback()
            with suppress(Exception):
                connection.close()
            raise
        else:
            connection.close()

    return receive
