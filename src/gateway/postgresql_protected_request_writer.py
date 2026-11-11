"""Persist one protected synthetic request before it can be shown for approval."""

from contextlib import suppress
from re import fullmatch

import psycopg

from gateway.approval_request import (
    ApprovalRequestValidationError,
    validate_approval_request,
)
from gateway.request_snapshot import RequestSnapshot

_INSERT = """
INSERT INTO gateway_approval.requests (
    request_id, request_schema_version, requester_actor_id, principal_id,
    tool, operation, item_id, policy_version, adapter_version
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""  # A duplicate ID must fail rather than overwrite an existing request.


def protected_request_values(
    *,
    request_id: object,
    snapshot: RequestSnapshot,
    requester_actor_id: object,
    policy_version: object,
    adapter_version: object,
) -> tuple[str, int, str, str, str, str, int, str, str]:
    """Validate captured context for both request storage and later comparison."""
    # The caller supplies trusted identity and version context. Check its shape
    # before connecting, without attempting to authenticate or replace it.
    if (
        type(snapshot) is not RequestSnapshot
        or type(requester_actor_id) is not str
        or requester_actor_id.strip(" ") != requester_actor_id
        or fullmatch(r"[^\x00-\x1f\x7f\ud800-\udfff]{1,256}", requester_actor_id)
        is None
        or type(policy_version) is not str
        or policy_version != "policy-core-v1"
        or type(adapter_version) is not str
        or fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}", adapter_version) is None
    ):
        raise ApprovalRequestValidationError("Invalid protected request")

    # Reuse the existing exact synthetic request and canonical ID validation.
    request = validate_approval_request(
        {
            "schema_version": 1,
            "request_id": request_id,
            "principal_id": snapshot.principal_id,
            "tool": snapshot.tool,
            "operation": snapshot.operation,
            "item_id": snapshot.item_id,
        },
        expected_request_id=request_id,
    )
    return (
        request["request_id"],
        request["schema_version"],
        requester_actor_id,
        request["principal_id"],
        request["tool"],
        request["operation"],
        request["item_id"],
        policy_version,
        adapter_version,
    )  # Keep checked values separate from SQL syntax.


def write_postgresql_protected_request(
    conninfo: str,
    *,
    request_id: object,
    snapshot: RequestSnapshot,
    requester_actor_id: object,
    policy_version: object,
    adapter_version: object,
) -> None:
    """Return after commit and close. A failed call can still leave a saved row."""
    if type(conninfo) is not str or not conninfo.strip():
        raise ValueError("Invalid protected request database configuration")
    values = protected_request_values(
        request_id=request_id,
        snapshot=snapshot,
        requester_actor_id=requester_actor_id,
        policy_version=policy_version,
        adapter_version=adapter_version,
    )
    connection = psycopg.connect(conninfo, connect_timeout=5)
    try:
        connection.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
        # Require a synchronous commit and bound database lock and statement waits.
        connection.execute("SET LOCAL synchronous_commit = on")
        connection.execute("SET LOCAL lock_timeout = '2s'")
        connection.execute("SET LOCAL statement_timeout = '5s'")
        connection.execute(_INSERT, values)
        connection.commit()
    except BaseException:
        # Preserve the original failure. Cleanup cannot undo a committed insert.
        with suppress(Exception):
            connection.rollback()
        with suppress(Exception):
            connection.close()
        raise
    else:
        # Return success only after the driver confirms commit and closes.
        connection.close()
