"""Prove approval consumption behavior in disposable PostgreSQL."""

import os
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from uuid import UUID

import psycopg
import pytest
from psycopg.errors import CheckViolation

from gateway import fixture_simulator  # Exercise only the fixed synthetic simulator.

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
ACTIVE_SCOPE_APPROVAL = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
REVOKED_SCOPE_APPROVAL = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
ACTIVE_SCOPE_EXECUTION = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
REVOKED_SCOPE_EXECUTION = UUID("01234567-89ab-4cde-8f01-23456789abcd")


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


@pytest.fixture  # Keep each scenario's invocation evidence independent.
def simulator_calls(monkeypatch):  # Observe calls without replacing fixture behavior.
    """Count actual fixed-simulator entries, not claims of successful dispatch."""
    calls = []  # A fresh list prevents other proof cases from supplying evidence.
    original = fixture_simulator.simulate_read_item  # Preserve the real fixture code.

    def recorded(item_id):  # Intercept only this test's fixed simulator boundary.
        calls.append(item_id)  # Record entry even if the simulator later fails.
        return original(item_id)  # Still execute the existing deterministic fixture.

    monkeypatch.setattr(  # Restore the simulator automatically after this scenario.
        fixture_simulator,  # Observe the existing module, not a dispatch registry.
        "simulate_read_item",  # Replace only the fixed fixture entry for this test.
        recorded,  # Keep the real implementation behind the observation wrapper.
    )
    return calls  # Assertions inspect entries independently of returned payloads.


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


def simulate_confirmed_fixture(reader, approval_id, execution_id):
    """Check committed proof rows before one test-only fixed fixture invocation."""
    assert observed(reader, approval_id) == [  # Read through the separate proof role.
        ("consumed", execution_id, execution_id, "consumed")  # Require both references.
    ]
    result = fixture_simulator.simulate_read_item(1)  # Use only the fixed sample item.
    assert result == {
        "item_id": 1,  # Match the fixed input rather than another synthetic item.
        "label": "Synthetic item 1",  # Require the existing deterministic payload.
    }  # Check the real answer.


@pytest.mark.parametrize(  # Isolate the three faults with separate seeded approvals.
    "mode,approval_id,execution_id",  # Keep each fault and its references together.
    [  # These approval IDs match only the new disposable fixture rows.
        (  # Hide the reported pair without changing the actual committed records.
            "missing",  # Exercise the existing gate with no reported rows.
            UUID("10000000-0000-4000-8000-000000000001"),  # Missing-result approval.
            UUID("20000000-0000-4000-8000-000000000001"),  # Its exact execution.
        ),
        (  # Report an audit reference that differs from the committed execution.
            "mismatch",  # Exercise the existing matching-pair assertion.
            UUID("10000000-0000-4000-8000-000000000002"),  # Mismatched-result approval.
            UUID("20000000-0000-4000-8000-000000000002"),  # Its true execution.
        ),
        (  # Fail observation delivery after a successful real reader query.
            "unavailable",  # Model an error at the verification boundary.
            UUID("10000000-0000-4000-8000-000000000003"),  # Reader-error approval.
            UUID("20000000-0000-4000-8000-000000000003"),  # Its true execution.
        ),
    ],
    ids=["missing", "mismatch", "unavailable"],  # Name the failed boundary in pytest.
)
def test_verification_failure_preserves_consumption_without_invocation(
    proof_database,  # Retain the separately authenticated writer and reader roles.
    simulator_calls,  # Count actual fixed simulator entries for this scenario.
    monkeypatch,  # Restore the real reader before investigating saved state.
    mode,  # Choose one fixed observation fault, never a caller-selected action.
    approval_id,  # Keep each case separate from earlier committed proofs.
    execution_id,  # Require this reference in both saved records.
):
    """Reject bad reader evidence without undoing the acknowledged consumption."""
    writer, reader = proof_database  # Preserve the existing role and database checks.
    with proof_connection(writer) as connection:  # Reuse the existing commit operation.
        assert competing_claim(connection, approval_id, execution_id) == 1  # Ack.
    committed = [  # Keep this exact pair through the fault and later investigation.
        ("consumed", execution_id, execution_id, "consumed")  # Same attempt in both.
    ]
    original_reader = observed  # Retain the real independent query outside the fault.
    observations = []  # Detect a skipped fault boundary or an automatic read retry.

    def faulty_reader(actual_reader, actual_approval):
        """Alter only the observation delivered to the unchanged verification gate."""
        assert actual_reader == reader  # Require the intended reader role context.
        assert actual_approval == approval_id  # Inspect exactly this scenario's row.
        assert (
            original_reader(actual_reader, actual_approval) == committed
        )  # Saved pair.
        observations.append(mode)  # Count entry only after the real query succeeds.
        if mode == "missing":  # Hide evidence at the observation boundary only.
            return []  # Leave all actual database records untouched.
        if mode == "mismatch":  # Supply inconsistent reported execution references.
            return [  # Only the fabricated audit execution differs from the real pair.
                ("consumed", execution_id, SECOND_EXECUTION, "consumed")  # Mismatch.
            ]
        raise psycopg.OperationalError(  # The third case fails observation delivery.
            "synthetic verification unavailable"  # Exclude private connection details.
        )

    expected_error = (  # Mismatches assert; the helper must propagate reader errors.
        psycopg.OperationalError if mode == "unavailable" else AssertionError
    )
    with monkeypatch.context() as injected:  # Limit the fault to this stopped attempt.
        injected.setitem(  # Patch the reader used by the existing verification helper.
            globals(),  # Leave the database and reader privilege grants unchanged.
            "observed",  # Intercept only the independent observation boundary.
            faulty_reader,  # Never replace the matching-pair verification gate.
        )
        with pytest.raises(expected_error) as failure:  # Failure must prevent dispatch.
            simulate_confirmed_fixture(  # Exercise the existing fixed invocation path.
                reader,  # Read through the scenario's independent role context.
                approval_id,  # Verify the original consumed approval only.
                execution_id,  # Preserve the execution reference from the normal ack.
            )
    assert observations == [mode]  # One failed observation, without automatic retries.
    if mode == "unavailable":  # Ensure the expected injected reader error propagated.
        assert str(failure.value) == "synthetic verification unavailable"  # Reason.
    assert simulator_calls == []  # Verification failure cannot invoke the simulator.
    assert observed(reader, approval_id) == committed  # Investigation only.
    with proof_connection(writer) as later_writer:  # New reuse probe.
        changed = competing_fixture_attempt(  # Retain the probe invocation branch.
            later_writer,  # Do not resume the earlier attempt or its transaction.
            reader,  # A mistaken winner would independently inspect its new pair.
            approval_id,  # The original approval must remain consumed.
            SECOND_EXECUTION,  # A different execution cannot reopen spent approval.
        )
        assert changed == 0  # Require no row changes and no new consumption.
    assert observed(reader, approval_id) == committed  # Preserve both original records.
    assert simulator_calls == []  # No resumed dispatch.


def competing_fixture_attempt(connection, reader, approval_id, execution_id):
    """Exercise the existing claim operation before the test's invocation branch."""
    changed = competing_claim(
        connection,  # Reuse the existing transaction and its commit handling.
        approval_id,  # Claim exactly the fixture row selected by this scenario.
        execution_id,  # Bind the attempt to its existing execution reference.
    )
    if changed == 1:  # A losing claim must not reach the synthetic invocation branch.
        simulate_confirmed_fixture(reader, approval_id, execution_id)  # No retry path.
    return changed  # Preserve the contender's independently checked claim outcome.


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


def test_competing_claims_commit_only_one_consumption(proof_database, simulator_calls):
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
                competing_fixture_attempt,  # Exercise the losing invocation branch.
                second,  # Retain the separate transaction and real row contention.
                reader,  # Independently inspect committed evidence before any call.
                CONTENTION_APPROVAL,  # Both attempts target the same approval.
                SECOND_EXECUTION,  # Keep the contender's execution reference distinct.
            )
            wait_for_blocking(observer, first, second)
            assert simulator_calls == []  # Neither uncommitted attempt may invoke.
            first.commit()
            simulate_confirmed_fixture(  # Invoke only after commit returned normally.
                reader,  # Check committed rows through the independent reader.
                CONTENTION_APPROVAL,  # Inspect the approval contested by both writers.
                FIRST_EXECUTION,  # Require the winning execution in both records.
            )
        finally:
            # Release the first transaction before waiting for the worker to exit.
            first.rollback()

        assert contender.result(timeout=12) == 0

    assert observed(reader, CONTENTION_APPROVAL) == [
        ("consumed", FIRST_EXECUTION, FIRST_EXECUTION, "consumed")
    ]
    assert simulator_calls == [1]  # The losing contender must not add another call.


def test_failed_audit_insert_rolls_back_consumption(proof_database, simulator_calls):
    writer, reader = proof_database
    with proof_connection(writer) as connection:
        # Confirm the update ran before triggering the named audit constraint.
        assert claim(connection, ROLLBACK_APPROVAL, ROLLBACK_EXECUTION) == 1
        with pytest.raises(CheckViolation) as failure:
            write_event(connection, ROLLBACK_APPROVAL, ROLLBACK_EXECUTION, "invalid")
            connection.commit()  # Audit rejection must prevent reaching this commit.
            simulate_confirmed_fixture(  # An audit failure must bypass this branch.
                reader,  # A successful branch would require committed evidence.
                ROLLBACK_APPROVAL,  # Isolate the failed-audit scenario.
                ROLLBACK_EXECUTION,  # Compare the exact attempted execution.
            )
        assert failure.value.diag.constraint_name == "proof_consumption_event_type"
        connection.rollback()

    assert observed(reader, ROLLBACK_APPROVAL) == [("approved", None, None, None)]
    assert simulator_calls == []  # Failed persistence cannot release a fixture call.


def test_revocation_committing_first_blocks_consumption(
    proof_database,  # Retain the isolated writer and reader role checks.
    simulator_calls,  # Observe whether the revoked attempt enters the simulator.
):
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
                competing_fixture_attempt,  # Observe the rejected invocation branch.
                second,  # Preserve the blocked transaction used by the existing proof.
                reader,  # A successful branch would inspect the committed pair.
                REVOCATION_FIRST_APPROVAL,  # Revoke exactly the approval being claimed.
                BLOCKED_EXECUTION,  # Identify the rejected execution attempt.
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
    assert simulator_calls == []  # Earlier revocation blocks the fixture invocation.


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


def test_lost_commit_acknowledgement_preserves_consumption(
    proof_database,  # Reconcile with the separately authenticated proof reader.
    simulator_calls,  # Retain a call observation through reconciliation.
):
    writer, reader = proof_database
    with proof_connection(writer) as connection:
        # Commit the claim and its audit record in the same transaction.
        assert claim(connection, LOST_ACK_APPROVAL, LOST_ACK_EXECUTION) == 1
        write_event(connection, LOST_ACK_APPROVAL, LOST_ACK_EXECUTION)
        with pytest.raises(
            psycopg.OperationalError, match="^synthetic lost commit acknowledgement$"
        ):
            commit_with_lost_acknowledgement(connection)
            simulate_confirmed_fixture(  # Lost confirmation must prevent this call.
                reader,  # A successful branch would independently read the pair.
                LOST_ACK_APPROVAL,  # Preserve the original approval reference.
                LOST_ACK_EXECUTION,  # Never invent a replacement execution identity.
            )
        # Cleanup after the error cannot undo an already committed transaction.
        connection.rollback()

    # Reconcile with the read-only role, independently of the failed call.
    committed = [("consumed", LOST_ACK_EXECUTION, LOST_ACK_EXECUTION, "consumed")]
    assert observed(reader, LOST_ACK_APPROVAL) == committed
    assert simulator_calls == []  # Saved evidence alone does not resume dispatch.

    # A separate test writer probes whether the spent approval can be reused.
    with proof_connection(writer) as later_writer:
        assert claim(later_writer, LOST_ACK_APPROVAL, LATER_CLAIM_EXECUTION) == 0
    assert observed(reader, LOST_ACK_APPROVAL) == committed
    assert simulator_calls == []  # The rejected reuse probe must not resume dispatch.


def consume_with_scope_authority(connection, approval_id, execution_id):
    """Hold the exact grant lock through consumption and its audit commit."""
    grant = connection.execute(
        "SELECT g.active FROM approval_consumption_proof.approvals AS a "
        "JOIN approval_consumption_proof.scope_grants AS g "
        "ON g.scope = a.authority_scope WHERE a.approval_id = %s FOR UPDATE OF g",
        (approval_id,),
    ).fetchone()
    # Missing or inactive authority leaves the approval and audit record untouched.
    if grant != (True,):
        connection.rollback()
        return 0
    return competing_claim(connection, approval_id, execution_id)


def test_active_scope_authority_allows_consumption(proof_database):
    writer, reader = proof_database
    with proof_connection(writer) as connection:
        assert (
            consume_with_scope_authority(
                connection, ACTIVE_SCOPE_APPROVAL, ACTIVE_SCOPE_EXECUTION
            )
            == 1
        )

    assert observed(reader, ACTIVE_SCOPE_APPROVAL) == [
        ("consumed", ACTIVE_SCOPE_EXECUTION, ACTIVE_SCOPE_EXECUTION, "consumed")
    ]


def test_scope_authority_revocation_committing_first_blocks_consumption(proof_database):
    writer, reader = proof_database
    with (
        proof_connection(writer) as first,
        proof_connection(writer) as second,
        proof_connection(reader, autocommit=True) as observer,
        ThreadPoolExecutor(max_workers=1) as worker,
    ):
        try:
            # Hold the exact grant row uncommitted until PostgreSQL sees the wait.
            revoked = first.execute(
                "UPDATE approval_consumption_proof.scope_grants "
                "SET active = false WHERE scope = %s AND active",
                ("revocation-scope",),
            )
            assert revoked.rowcount == 1
            contender = worker.submit(
                consume_with_scope_authority,
                second,
                REVOKED_SCOPE_APPROVAL,
                REVOKED_SCOPE_EXECUTION,
            )
            wait_for_blocking(observer, first, second)
            first.commit()
        finally:
            # Release the grant row before joining the worker on failure.
            first.rollback()

        assert contender.result(timeout=12) == 0

    assert observed(reader, REVOKED_SCOPE_APPROVAL) == [("approved", None, None, None)]
