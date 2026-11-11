"""Save a current policy decision, its binding, and its exact request link."""

from contextlib import suppress

import psycopg

from gateway.audit import validate_audit_event
from gateway.postgresql_binding_receiver import _BINDING_INSERT, _POLICY_INSERT
from gateway.postgresql_protected_request_writer import protected_request_values
from gateway.request_binding import validate_request_binding
from gateway.request_snapshot import RequestSnapshot

_REQUEST_SELECT = """
SELECT request_id, request_schema_version, requester_actor_id, principal_id,
    tool, operation, item_id, policy_version, adapter_version
FROM gateway_approval.requests WHERE request_id = %s
"""  # Read exactly one immutable protected record.

_LINK_INSERT = """
INSERT INTO gateway_approval.policy_event_request_links (policy_event_id, request_id)
VALUES (%s, %s)
"""  # Strict inserts prevent reassignment of an existing event.


class ProtectedBindingValidationError(ValueError):
    """The current attempt does not match the saved protected request."""


def write_postgresql_protected_binding(
    conninfo: str,
    policy_event: object,
    request_binding: object,
    *,
    request_id: object,
    snapshot: RequestSnapshot,
    requester_actor_id: object,
    adapter_version: object,
) -> None:
    """Accept trusted attempt context and return after all three writes commit."""
    if type(conninfo) is not str or not conninfo.strip():
        raise ValueError("Invalid protected binding database configuration")

    # Reuse the existing validators before opening a database connection.
    policy = validate_audit_event(policy_event)
    binding = validate_request_binding(request_binding)
    expected = protected_request_values(
        request_id=request_id,
        snapshot=snapshot,
        requester_actor_id=requester_actor_id,
        policy_version=policy["policy_version"],
        adapter_version=adapter_version,
    )
    if (
        policy["decision"] != "ALLOW"
        or policy["event_id"] != binding["policy_event_id"]
        or policy["principal_id"] != binding["principal_id"]
        or (
            binding["principal_id"],
            binding["tool"],
            binding["operation"],
            binding["item_id"],
        )
        != expected[3:7]
    ):
        raise ProtectedBindingValidationError("Invalid protected policy binding")

    connection = psycopg.connect(conninfo, connect_timeout=5)
    try:
        connection.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
        connection.execute("SET LOCAL synchronous_commit = on")
        connection.execute("SET LOCAL lock_timeout = '2s'")
        connection.execute("SET LOCAL statement_timeout = '5s'")
        # The immutable record must match both its ID and all captured context.
        saved = connection.execute(_REQUEST_SELECT, (expected[0],)).fetchone()
        if saved != expected:
            raise ProtectedBindingValidationError("Protected request does not match")

        connection.execute(
            _POLICY_INSERT,
            (
                policy["schema_version"],
                policy["event_type"],
                policy["event_id"],
                policy["occurred_at"],
                policy["principal_id"],
                policy["decision"],
                policy["reason_code"],
                policy["policy_version"],
                policy["execution_status"],
            ),
        )
        connection.execute(
            _BINDING_INSERT,
            (
                binding["schema_version"],
                binding["policy_event_id"],
                binding["principal_id"],
                binding["tool"],
                binding["operation"],
                binding["item_id"],
            ),
        )
        connection.execute(_LINK_INSERT, (policy["event_id"], expected[0]))
        connection.commit()
    except BaseException:
        # Rollback removes pre-commit writes and never proves a lost commit failed.
        with suppress(Exception):
            connection.rollback()
        with suppress(Exception):
            connection.close()
        raise
    else:
        connection.close()
