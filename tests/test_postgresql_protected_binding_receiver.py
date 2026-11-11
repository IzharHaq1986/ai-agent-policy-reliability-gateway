"""Verify the protected three-record transaction against isolated PostgreSQL."""

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from gateway import postgresql_protected_binding_receiver as module
from gateway.audit import build_audit_event
from gateway.postgresql_protected_request_writer import (
    write_postgresql_protected_request,
)
from gateway.request_binding import build_request_binding
from gateway.request_snapshot import RequestSnapshot


def attempt():
    """Build a unique attempt and the trusted context captured for its request."""
    context = {
        "request_id": str(uuid4()),
        "snapshot": RequestSnapshot(
            "reliability-reader", "fixture_store", "READ_ITEM", 42
        ),
        "requester_actor_id": "test-actor",
        "adapter_version": "test-adapter-v1",
    }
    policy = build_audit_event(
        {
            "decision": "ALLOW",
            "reason_code": "POLICY_ALLOW",
            "policy_version": "policy-core-v1",
            "execution_status": "not_executed",
        },
        principal_id="reliability-reader",
        event_id=str(uuid4()),
        occurred_at="2026-11-11T00:00:00Z",
    )
    binding = build_request_binding(
        policy_event_id=policy["event_id"],
        principal_id="reliability-reader",
        tool="fixture_store",
        operation="READ_ITEM",
        item_id=42,
    )
    return context, policy, binding


@pytest.mark.parametrize("invalid", ["deny", "event_id", "binding_values"])
def test_invalid_pair_fails_before_connecting(monkeypatch, invalid):
    context, policy, binding = attempt()
    if invalid == "deny":
        policy.update(decision="DENY", reason_code="NOT_AUTHORIZED")
    elif invalid == "event_id":
        binding["policy_event_id"] = str(uuid4())
    else:
        binding["item_id"] = 43

    def unexpected_connection(*args, **kwargs):
        pytest.fail("Invalid binding reached PostgreSQL")

    monkeypatch.setattr(psycopg, "connect", unexpected_connection)
    with pytest.raises(module.ProtectedBindingValidationError):
        module.write_postgresql_protected_binding("unused", policy, binding, **context)


@pytest.fixture
def database():
    """Verify the writer and the two independent readers before using them."""
    settings = (
        (
            "GATEWAY_TEST_PROTECTED_BINDING_WRITER_DSN",
            "gateway_protected_binding_writer",
        ),
        ("GATEWAY_TEST_APPROVAL_REQUEST_WRITER_DSN", "gateway_approval_request_writer"),
        ("GATEWAY_TEST_READER_DSN", "gateway_audit_reader"),
        ("GATEWAY_TEST_APPROVAL_REQUEST_READER_DSN", "gateway_approval_request_reader"),
    )
    connections = [os.environ.get(variable) for variable, _ in settings]
    if connections[0] is None:
        pytest.skip("Isolated protected binding writer connection required")
    assert all(connections), "All protected binding test connections are required"
    for dsn, (_, role) in zip(connections, settings, strict=True):
        with psycopg.connect(dsn, connect_timeout=5) as connection:
            assert connection.execute(
                "SELECT current_database(), current_user"
            ).fetchone() == ("gateway_audit_test", role)
    return connections


def save_request(writer, context):
    write_postgresql_protected_request(
        writer, policy_version="policy-core-v1", **context
    )


def observed(database, event_id):
    """Read every saved field through the separately authorized readers."""
    _, _, audit_reader, protected_reader = database
    with psycopg.connect(
        audit_reader, connect_timeout=5, row_factory=dict_row
    ) as connection:
        policies = connection.execute(
            "SELECT schema_version, event_type, event_id, occurred_at, principal_id, "
            "decision, reason_code, policy_version, execution_status "
            "FROM gateway_audit.policy_decisions WHERE event_id = %s",
            (event_id,),
        ).fetchall()
        bindings = connection.execute(
            "SELECT schema_version, policy_event_id, principal_id, "
            "tool, operation, item_id "
            "FROM gateway_audit.request_bindings WHERE policy_event_id = %s",
            (event_id,),
        ).fetchall()
    with psycopg.connect(protected_reader, connect_timeout=5) as connection:
        links = connection.execute(
            "SELECT policy_event_id, request_id "
            "FROM gateway_approval.policy_event_request_links "
            "WHERE policy_event_id = %s",
            (event_id,),
        ).fetchall()
    return policies, bindings, links


def test_confirmed_transaction_saves_all_three_records(database):
    writer, request_writer, _, _ = database
    context, policy, binding = attempt()
    save_request(request_writer, context)
    assert (
        module.write_postgresql_protected_binding(writer, policy, binding, **context)
        is None
    )
    expected = ([policy], [binding], [(policy["event_id"], context["request_id"])])
    assert observed(database, policy["event_id"]) == expected
    # A duplicate attempt must preserve the original records without silent reuse.
    with pytest.raises(psycopg.errors.UniqueViolation):
        module.write_postgresql_protected_binding(writer, policy, binding, **context)
    assert observed(database, policy["event_id"]) == expected


@pytest.mark.parametrize("mismatch", ["missing", "actor", "adapter", "item"])
def test_saved_request_must_match_trusted_context(database, mismatch):
    writer, request_writer, _, _ = database
    context, policy, binding = attempt()
    if mismatch != "missing":
        save_request(request_writer, context)
    if mismatch == "actor":
        context["requester_actor_id"] = "another-actor"
    elif mismatch == "adapter":
        context["adapter_version"] = "another-version"
    elif mismatch == "item":
        context["snapshot"] = RequestSnapshot(
            "reliability-reader", "fixture_store", "READ_ITEM", 43
        )
        binding["item_id"] = 43
    with pytest.raises(module.ProtectedBindingValidationError):
        module.write_postgresql_protected_binding(writer, policy, binding, **context)
    assert observed(database, policy["event_id"]) == ([], [], [])


def test_link_insert_failure_rolls_back_decision_and_binding(database, monkeypatch):
    writer, request_writer, _, _ = database
    context, policy, binding = attempt()
    save_request(request_writer, context)
    real_execute = psycopg.Connection.execute
    nonexistent_request = str(uuid4())

    def fail_link(connection, statement, params=None, **kwargs):
        if statement == module._LINK_INSERT:
            params = (params[0], nonexistent_request)
        return real_execute(connection, statement, params, **kwargs)

    # A real foreign-key rejection occurs after the first two inserts.
    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "execute", fail_link)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            module.write_postgresql_protected_binding(
                writer, policy, binding, **context
            )
    assert observed(database, policy["event_id"]) == ([], [], [])


def test_lost_commit_confirmation_preserves_all_three_without_retry(
    database, monkeypatch
):
    writer, request_writer, _, _ = database
    context, policy, binding = attempt()
    save_request(request_writer, context)
    real_connect, real_commit = psycopg.connect, psycopg.Connection.commit
    connections, commits = [], []

    def connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    def commit_then_lose_confirmation(connection):
        commits.append(connection)
        real_commit(connection)
        raise psycopg.OperationalError("synthetic lost commit acknowledgement")

    with monkeypatch.context() as patch:
        patch.setattr(psycopg, "connect", connect)
        patch.setattr(psycopg.Connection, "commit", commit_then_lose_confirmation)
        with pytest.raises(
            psycopg.OperationalError, match="lost commit acknowledgement"
        ):
            module.write_postgresql_protected_binding(
                writer, policy, binding, **context
            )
    assert len(connections) == 1
    assert commits == connections
    assert connections[0].closed
    assert observed(database, policy["event_id"]) == (
        [policy],
        [binding],
        [(policy["event_id"], context["request_id"])],
    )
