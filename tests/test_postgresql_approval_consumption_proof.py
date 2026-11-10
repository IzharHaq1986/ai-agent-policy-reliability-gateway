"""Prove approval consumption behavior in disposable PostgreSQL."""

import os
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from uuid import UUID

import psycopg
import pytest
from psycopg.errors import CheckViolation

CONTENTION_APPROVAL = UUID("11111111-1111-4111-8111-111111111111")
ROLLBACK_APPROVAL = UUID("22222222-2222-4222-8222-222222222222")
FIRST_EXECUTION = UUID("33333333-3333-4333-8333-333333333333")
SECOND_EXECUTION = UUID("44444444-4444-4444-8444-444444444444")
ROLLBACK_EXECUTION = UUID("55555555-5555-4555-8555-555555555555")
REVOCATION_FIRST_APPROVAL = UUID("66666666-6666-4666-8666-666666666666")
CONSUMPTION_FIRST_APPROVAL = UUID("77777777-7777-4777-8777-777777777777")
BLOCKED_EXECUTION = UUID("88888888-8888-4888-8888-888888888888")
CONSUMPTION_FIRST_EXECUTION = UUID("99999999-9999-4999-8999-999999999999")
LOST_ACK_APPROVAL = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
LOST_ACK_EXECUTION = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
LATER_CLAIM_EXECUTION = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


@pytest.fixture
def proof_database():
    """Require both dedicated roles on the isolated test database."""
    writer = os.environ.get("GATEWAY_TEST_APPROVAL_PROOF_WRITER_DSN")
    reader = os.environ.get("GATEWAY_TEST_APPROVAL_PROOF_READER_DSN")
    if writer is None and reader is None:
        pytest.skip("Isolated approval proof connections required")
    assert writer and reader, "Both approval proof connections are required"

    for dsn, role in (
        (writer, "gateway_approval_proof_writer"),
        (reader, "gateway_approval_proof_reader"),
    ):
        with proof_connection(dsn) as connection:
            assert connection.execute(
                "SELECT current_database(), current_user"
            ).fetchone() == ("gateway_audit_test", role)
    return writer, reader


def proof_connection(dsn, *, autocommit=False):
    """Bound database work and make the isolation level explicit."""
    return psycopg.connect(
        dsn,
        connect_timeout=5,
        autocommit=autocommit,
        options="-c default_transaction_isolation=read\\ committed "
        "-c lock_timeout=8s -c statement_timeout=10s "
        "-c idle_in_transaction_session_timeout=15s",
    )


def claim(connection, approval_id, execution_id):
    """Update only an approval that is still available for use."""
    cursor = connection.execute(
        "UPDATE approval_consumption_proof.approvals "
        "SET state = 'consumed', execution_id = %s "
        "WHERE approval_id = %s AND state = 'approved'",
        (execution_id, approval_id),
    )
    return cursor.rowcount


def write_event(connection, approval_id, execution_id, event_type="consumed"):
    """Write the audit event on the same connection as the claim."""
    connection.execute(
        "INSERT INTO approval_consumption_proof.consumption_events "
        "(approval_id, execution_id, event_type) VALUES (%s, %s, %s)",
        (approval_id, execution_id, event_type),
    )


def observed(reader, approval_id):
    """Read committed state and its audit event together as the proof reader."""
    with proof_connection(reader) as connection:
        return connection.execute(
            "SELECT a.state, a.execution_id, e.execution_id, e.event_type "
            "FROM approval_consumption_proof.approvals AS a "
            "LEFT JOIN approval_consumption_proof.consumption_events AS e "
            "ON e.approval_id = a.approval_id WHERE a.approval_id = %s",
            (approval_id,),
        ).fetchall()


def competing_claim(connection, approval_id, execution_id):
    """Commit only if this contender actually claimed the record."""
    changed = claim(connection, approval_id, execution_id)
    if changed == 1:
        write_event(connection, approval_id, execution_id)
        connection.commit()
    else:
        connection.rollback()
    return changed


def revoke(connection, approval_id):
    """Withdraw an approved row or report an already committed consumption."""
    # Target the same row and check the same available state as a claim.
    updated = connection.execute(
        "UPDATE approval_consumption_proof.approvals "
        "SET state = 'revoked' WHERE approval_id = %s AND state = 'approved'",
        (approval_id,),
    )
    if updated.rowcount == 1:
        return "revoked"

    # Read committed isolation gives this statement a fresh view after the wait.
    state = connection.execute(
        "SELECT state FROM approval_consumption_proof.approvals WHERE approval_id = %s",
        (approval_id,),
    ).fetchone()
    if state == ("consumed",):
        return "too_late"
    pytest.fail(f"Unexpected approval state after a rejected revocation: {state!r}")


def wait_for_blocking(observer, first, second):
    """Confirm PostgreSQL sees the second writer waiting on the first."""
    deadline = monotonic() + 5
    while monotonic() < deadline:
        blocked = observer.execute(
            "SELECT %s = ANY(pg_blocking_pids(%s))",
            (first.info.backend_pid, second.info.backend_pid),
        ).fetchone()
        if blocked == (True,):
            return
        sleep(0.01)
    pytest.fail("PostgreSQL did not report the competing lock wait")


def test_competing_claims_commit_only_one_consumption(proof_database):
    writer, reader = proof_database
    with (
        proof_connection(writer) as first,
        proof_connection(writer) as second,
        proof_connection(reader, autocommit=True) as observer,
        ThreadPoolExecutor(max_workers=1) as worker,
    ):
        # Keep the first claim uncommitted until PostgreSQL confirms contention.
        try:
            assert claim(first, CONTENTION_APPROVAL, FIRST_EXECUTION) == 1
            write_event(first, CONTENTION_APPROVAL, FIRST_EXECUTION)
            contender = worker.submit(
                competing_claim, second, CONTENTION_APPROVAL, SECOND_EXECUTION
            )
            wait_for_blocking(observer, first, second)
            first.commit()
        finally:
            # Release the first transaction before waiting for the worker to exit.
            first.rollback()

        assert contender.result(timeout=12) == 0

    assert observed(reader, CONTENTION_APPROVAL) == [
        ("consumed", FIRST_EXECUTION, FIRST_EXECUTION, "consumed")
    ]


def test_failed_audit_insert_rolls_back_consumption(proof_database):
    writer, reader = proof_database
    with proof_connection(writer) as connection:
        # Confirm the update ran before triggering the named audit constraint.
        assert claim(connection, ROLLBACK_APPROVAL, ROLLBACK_EXECUTION) == 1
        with pytest.raises(CheckViolation) as failure:
            write_event(connection, ROLLBACK_APPROVAL, ROLLBACK_EXECUTION, "invalid")
        assert failure.value.diag.constraint_name == "proof_consumption_event_type"
        connection.rollback()

    assert observed(reader, ROLLBACK_APPROVAL) == [("approved", None, None, None)]


def test_revocation_committing_first_blocks_consumption(proof_database):
    writer, reader = proof_database
    with (
        proof_connection(writer) as first,
        proof_connection(writer) as second,
        proof_connection(reader, autocommit=True) as observer,
        ThreadPoolExecutor(max_workers=1) as worker,
    ):
        try:
            assert revoke(first, REVOCATION_FIRST_APPROVAL) == "revoked"
            contender = worker.submit(
                competing_claim, second, REVOCATION_FIRST_APPROVAL, BLOCKED_EXECUTION
            )
            wait_for_blocking(observer, first, second)
            first.commit()
        finally:
            # Release the row before joining the worker if the check fails.
            first.rollback()

        assert contender.result(timeout=12) == 0

    assert observed(reader, REVOCATION_FIRST_APPROVAL) == [
        ("revoked", None, None, None)
    ]


def test_consumption_committing_first_makes_revocation_too_late(proof_database):
    writer, reader = proof_database
    with (
        proof_connection(writer) as first,
        proof_connection(writer) as second,
        proof_connection(reader, autocommit=True) as observer,
        ThreadPoolExecutor(max_workers=1) as worker,
    ):
        try:
            assert (
                claim(first, CONSUMPTION_FIRST_APPROVAL, CONSUMPTION_FIRST_EXECUTION)
                == 1
            )
            write_event(first, CONSUMPTION_FIRST_APPROVAL, CONSUMPTION_FIRST_EXECUTION)
            contender = worker.submit(revoke, second, CONSUMPTION_FIRST_APPROVAL)
            wait_for_blocking(observer, first, second)
            first.commit()
        finally:
            # Release the row before joining the worker if the check fails.
            first.rollback()

        assert contender.result(timeout=12) == "too_late"

    assert observed(reader, CONSUMPTION_FIRST_APPROVAL) == [
        (
            "consumed",
            CONSUMPTION_FIRST_EXECUTION,
            CONSUMPTION_FIRST_EXECUTION,
            "consumed",
        )
    ]


def commit_with_lost_acknowledgement(connection):
    """Model confirmation loss after PostgreSQL has committed."""
    connection.commit()
    raise psycopg.OperationalError("synthetic lost commit acknowledgement")


def test_lost_commit_acknowledgement_preserves_consumption(proof_database):
    writer, reader = proof_database
    with proof_connection(writer) as connection:
        # Commit the claim and its audit record in the same transaction.
        assert claim(connection, LOST_ACK_APPROVAL, LOST_ACK_EXECUTION) == 1
        write_event(connection, LOST_ACK_APPROVAL, LOST_ACK_EXECUTION)
        with pytest.raises(
            psycopg.OperationalError, match="^synthetic lost commit acknowledgement$"
        ):
            commit_with_lost_acknowledgement(connection)
        # Cleanup after the error cannot undo an already committed transaction.
        connection.rollback()

    # Reconcile with the read-only role, independently of the failed call.
    committed = [("consumed", LOST_ACK_EXECUTION, LOST_ACK_EXECUTION, "consumed")]
    assert observed(reader, LOST_ACK_APPROVAL) == committed

    # A separate test writer probes whether the spent approval can be reused.
    with proof_connection(writer) as later_writer:
        assert claim(later_writer, LOST_ACK_APPROVAL, LATER_CLAIM_EXECUTION) == 0
    assert observed(reader, LOST_ACK_APPROVAL) == committed
