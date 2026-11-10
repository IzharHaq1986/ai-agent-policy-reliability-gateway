"""Verify validation, permissions, and commit handling for protected requests."""

import os
from uuid import uuid4

import psycopg
import pytest

from gateway.approval_request import ApprovalRequestValidationError
from gateway.postgresql_protected_request_writer import (
    write_postgresql_protected_request,
)
from gateway.request_snapshot import RequestSnapshot


def captured_request(item_id=42):
    """Provide the trusted context for one unique synthetic request."""
    return {
        "request_id": str(uuid4()),
        "snapshot": RequestSnapshot(
            "reliability-reader", "fixture_store", "READ_ITEM", item_id
        ),
        "requester_actor_id": "test-actor",
        "policy_version": "policy-core-v1",
        "adapter_version": "test-adapter-v1",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_id", "invalid-id"),
        ("snapshot", {"item_id": 42}),
        ("requester_actor_id", " "),
        ("requester_actor_id", "actor\n"),
        ("requester_actor_id", "a" * 257),
        ("requester_actor_id", "\ud800"),
        ("policy_version", "unknown-policy"),
        ("adapter_version", "invalid version"),
    ],
)
def test_invalid_request_fails_before_connecting(monkeypatch, field, value):
    def unexpected_connection(*args, **kwargs):
        pytest.fail("Invalid input reached PostgreSQL")

    monkeypatch.setattr(psycopg, "connect", unexpected_connection)
    request = captured_request()
    request[field] = value
    with pytest.raises(ApprovalRequestValidationError):
        write_postgresql_protected_request("unused", **request)


@pytest.fixture
def database():
    """Require separate authenticated roles on the isolated test database."""
    writer = os.environ.get("GATEWAY_TEST_APPROVAL_REQUEST_WRITER_DSN")
    reader = os.environ.get("GATEWAY_TEST_APPROVAL_REQUEST_READER_DSN")
    if writer is None and reader is None:
        pytest.skip("Isolated protected request connections required")
    assert writer and reader, "Both protected request connections are required"

    for dsn, role in (
        (writer, "gateway_approval_request_writer"),
        (reader, "gateway_approval_request_reader"),
    ):
        with psycopg.connect(dsn, connect_timeout=5) as connection:
            assert connection.execute(
                "SELECT current_database(), current_user"
            ).fetchone() == ("gateway_audit_test", role)
    return writer, reader


def assert_saved(reader, request_id):
    """Read the committed request and compare all fields using the reader role."""
    with psycopg.connect(reader, connect_timeout=5) as connection:
        rows = connection.execute(
            "SELECT request_id, request_schema_version, requester_actor_id, "
            "principal_id, tool, operation, item_id, policy_version, adapter_version "
            "FROM gateway_approval.requests WHERE request_id = %s",
            (request_id,),
        ).fetchall()
    assert rows == [
        (
            request_id,
            1,
            "test-actor",
            "reliability-reader",
            "fixture_store",
            "READ_ITEM",
            42,
            "policy-core-v1",
            "test-adapter-v1",
        )
    ]


def test_confirmed_request_is_visible_to_separate_reader(database):
    writer, reader = database
    request = captured_request()
    assert write_postgresql_protected_request(writer, **request) is None
    assert_saved(reader, request["request_id"])


def test_duplicate_request_id_preserves_original(database):
    writer, reader = database
    request = captured_request()
    write_postgresql_protected_request(writer, **request)

    # Try different captured values under the same trusted identifier.
    duplicate = captured_request(item_id=43)
    duplicate["request_id"] = request["request_id"]
    with pytest.raises(psycopg.errors.UniqueViolation):
        write_postgresql_protected_request(writer, **duplicate)
    assert_saved(reader, request["request_id"])


def test_lost_commit_acknowledgement_does_not_retry(database, monkeypatch):
    writer, reader = database
    request = captured_request()
    real_connect = psycopg.connect
    real_commit = psycopg.Connection.commit
    connections = []
    commits = []

    def connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    def commit_then_lose_confirmation(connection):
        commits.append(connection)
        real_commit(connection)
        raise psycopg.OperationalError("synthetic lost commit acknowledgement")

    # Commit in PostgreSQL, then simulate failure before the caller gets success.
    with monkeypatch.context() as patch:
        patch.setattr(psycopg, "connect", connect)
        patch.setattr(psycopg.Connection, "commit", commit_then_lose_confirmation)
        with pytest.raises(
            psycopg.OperationalError, match="lost commit acknowledgement"
        ):
            write_postgresql_protected_request(writer, **request)

    assert len(connections) == 1
    assert commits == connections
    assert connections[0].closed
    assert_saved(reader, request["request_id"])
