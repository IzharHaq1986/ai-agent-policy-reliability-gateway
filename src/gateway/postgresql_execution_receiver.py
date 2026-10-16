"""Transactional execution-event storage; no simulator invocation."""

from collections.abc import Callable  # Describe the configured receiver.
from contextlib import suppress  # Preserve failures when cleanup also fails.

import psycopg  # Use the existing PostgreSQL driver.

from gateway.execution_audit import (  # Reuse the execution-event contract.
    validate_execution_event,  # Validate and copy before database access.
)

_LOCK = """
SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))
"""  # Serialize cooperating receivers for the supplied attempt.

_CONTEXT = """
SELECT p.decision, p.reason_code, p.principal_id,
       b.principal_id, b.tool, b.operation
FROM gateway_audit.request_bindings AS b
JOIN gateway_audit.policy_decisions AS p
    ON p.event_id = b.policy_event_id
WHERE b.policy_event_id = %s
"""  # Inspect the saved decision and its linked request.

_ATTEMPT = """
SELECT policy_event_id, principal_id, tool, operation, occurred_at, status
FROM gateway_audit.execution_events
WHERE execution_id = %s
"""  # Read full attempt identities after acquiring the lock.

_INSERT = """
INSERT INTO gateway_audit.execution_events (
    schema_version, event_type, event_id, execution_id, policy_event_id,
    occurred_at, principal_id, tool, operation, status, reason_code
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""  # Strict insertion rejects duplicate identities without repair.


class ExecutionRelationshipError(ValueError):  # Identify invalid saved links.
    """Stored records do not support the supplied execution event."""


def create_postgresql_execution_receiver(  # Configure explicit delivery.
    conninfo: str,  # Receive configuration from trusted application setup.
) -> Callable[[object], None]:  # Accept an untrusted event at runtime.
    """Create a receiver; delivery failures may leave a committed event."""
    if type(conninfo) is not str or not conninfo.strip():  # Check configuration.
        raise ValueError("Invalid execution database configuration")

    def receive(event: object) -> None:  # Save one checked event per call.
        validated = validate_execution_event(event)  # Reject before connecting.
        context = (  # Define the exact required saved authorization context.
            "ALLOW",  # Require an allowed decision.
            "POLICY_ALLOW",  # Require its matching reason.
            validated["principal_id"],  # Match the decision's principal.
            validated["principal_id"],  # Match the binding's principal.
            validated["tool"],  # Match the bound tool.
            validated["operation"],  # Match the bound operation.
        )
        attempt_context = (  # Require terminal records to match their start.
            validated["policy_event_id"],  # Match the exact policy reference.
            validated["principal_id"],  # Match the caller context.
            validated["tool"],  # Match the tool.
            validated["operation"],  # Match the operation.
        )
        values = (  # Preserve the INSERT column order explicitly.
            validated["schema_version"],  # Store the supported format.
            validated["event_type"],  # Store the event category.
            validated["event_id"],  # Store this record's identity.
            validated["execution_id"],  # Store the attempt identity.
            validated["policy_event_id"],  # Store the binding reference.
            validated["occurred_at"],  # Store the supplied UTC time.
            validated["principal_id"],  # Store caller context.
            validated["tool"],  # Store the supported tool.
            validated["operation"],  # Store the supported operation.
            validated["status"],  # Store intent or the claimed outcome.
            validated["reason_code"],  # Store the matching explanation.
        )
        connection = psycopg.connect(  # Own one bounded connection attempt.
            conninfo, connect_timeout=5
        )
        try:  # Preserve existing transaction and cleanup conventions.
            connection.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
            connection.execute("SET LOCAL synchronous_commit = on")  # Request sync.
            connection.execute("SET LOCAL lock_timeout = '2s'")  # Bound lock waits.
            connection.execute("SET LOCAL statement_timeout = '5s'")  # Bound SQL.
            connection.execute(  # Hold the attempt lock until transaction end.
                _LOCK,
                ("gateway.execution_events:" + validated["execution_id"],),
            )
            stored_context = connection.execute(  # Read after lock acquisition.
                _CONTEXT, (validated["policy_event_id"],)
            ).fetchone()
            if stored_context != context:  # Reject missing or inconsistent links.
                raise ExecutionRelationshipError("Invalid execution relationship")

            records = connection.execute(  # Observe previously committed events.
                _ATTEMPT, (validated["execution_id"],)
            ).fetchall()
            if validated["status"] == "started":  # A new start needs an empty attempt.
                if records:  # Reject duplicate starts and existing terminal records.
                    raise ExecutionRelationshipError("Invalid execution relationship")
            else:  # A terminal event needs exactly one matching start.
                if len(records) != 1:  # Reject missing starts or completed attempts.
                    raise ExecutionRelationshipError("Invalid execution relationship")
                start = records[0]  # Inspect the sole existing attempt record.
                if (  # Require matching context, start status, and valid time order.
                    start[:4] != attempt_context  # Compare full stored references.
                    or start[5] != "started"  # Reject a terminal-only attempt.
                    or validated["occurred_at"] < start[4]  # UTC text sorts by time.
                ):
                    raise ExecutionRelationshipError("Invalid execution relationship")

            connection.execute(_INSERT, values)  # Insert only after every gate passes.
            connection.commit()  # Commit the event and release the attempt lock.
        except BaseException:  # Attempt cleanup even when interrupted.
            with suppress(Exception):  # Retain the original delivery failure.
                connection.rollback()  # Cannot reverse an already completed commit.
            with suppress(Exception):  # Attempt close even if rollback failed.
                connection.close()  # Release the connection when possible.
            raise  # Propagate without retries or fallback.
        else:  # Commit acknowledgement succeeded.
            connection.close()  # Propagate close failures despite a saved event.

    return receive  # Configuration alone opens no connection.
