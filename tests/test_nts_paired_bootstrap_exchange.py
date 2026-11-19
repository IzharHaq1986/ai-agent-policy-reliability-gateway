"""Compose real bootstrap controllers and transport; synthetic time is not trust."""

import errno  # Distinguish an independently observed closed FD from other errors.
import importlib  # Load the fixed existing helpers without launching any workload.
import os  # Inspect actual kernel descriptors during test-owned cleanup.
from dataclasses import replace  # Construct valid conflicting synthetic packets.
from pathlib import Path  # Locate the reviewed internal helpers deterministically.
from types import SimpleNamespace  # Name test components without a runtime API.

import pytest  # Require precise errors and execute cleanup after rejection paths.


@pytest.fixture
def rig(monkeypatch):
    """Own a private pair; independently check both descriptors after each case."""
    monkeypatch.syspath_prepend(
        str(Path(__file__).parent / "support")
    )  # Fixed imports.
    parent = importlib.import_module("nts_bootstrap_session")  # Real supervisor phases.
    child = importlib.import_module("nts_child_bootstrap_session")  # Real child phases.
    codec = importlib.import_module(
        "nts_bootstrap_codec"
    )  # Actual bounded packet rules.
    health = importlib.import_module(
        "nts_health_protocol"
    )  # Shared immutable lifetime.
    transport = importlib.import_module("nts_health_transport")  # Actual local IPC.
    expected = codec.BootstrapContext(  # All identifiers and times are synthetic.
        health.Lifetime(
            boot_id="11111111-1111-4111-8111-111111111111",  # No host identity claim.
            collection_id="22222222-2222-4222-8222-222222222222",  # Test reference.
            container_id="d" * 64,  # No actual Docker container exists in this test.
            owner="c" * 32,  # A label is not a credential.
            watchdog_pid=4242,  # No actual child process is launched or observed.
            watchdog_start_ticks=9000,  # No kernel lifetime provenance is established.
            deadline_ns=100,  # Explicit values do not measure actual elapsed time.
        ),
        token="e" * 64,  # Predictable fixture token is not evidence of randomness.
    )
    supervisor = parent.BootstrapSession(expected, started_ns=10, cutoff_ns=50)
    worker = child.ChildBootstrapSession(expected.lifetime, started_ns=10, cutoff_ns=50)
    left, right = transport.create_pair(  # Preserve established explicit buffer bounds.
        buffer_bytes=8192, effective_buffer_limit=16384
    )
    descriptors = ()  # Track both FDs before exposing any endpoint to test code.
    try:  # Any test or setup assertion failure must still close both owned endpoints.
        descriptors = (
            left._owned().fileno(),  # Retain the supervisor endpoint number.
            right._owned().fileno(),  # Retain the child endpoint number.
        )  # Kernel handles.
        yield SimpleNamespace(  # This bundle is test-only, not an orchestration helper.
            supervisor=supervisor,  # Keep the actual parent controller.
            worker=worker,  # Keep the actual child controller.
            expected=expected,  # Retain independent test expectations.
            left=left,  # Parent direction of the actual channel.
            right=right,  # Child direction of the actual channel.
            parent=parent,  # Use the public supervisor error type.
            child=child,  # Use the public child error type.
            codec=codec,  # Validate actual packet bytes.
            transport=transport,  # Recognize real transport outcomes.
        )
    finally:
        try:  # Always attempt the second close even if the first reports an error.
            left.close()  # Release only the endpoint created by this fixture.
        finally:
            right.close()  # Never leave a spare endpoint masking peer closure.
        for descriptor in descriptors:  # Verify the kernel state, not controller flags.
            with pytest.raises(OSError) as failure:
                os.fstat(descriptor)  # No FD-acquiring work occurs before this check.
            assert (
                failure.value.errno == errno.EBADF
            )  # Other errors do not prove closure.


def sent_context(rig):
    """Send once through real IPC, then report successful send to the supervisor."""
    packet = rig.supervisor.prepare_context(11)  # Release does not imply delivery.
    rig.left.send(packet)  # Whole-packet nonblocking send; no retry loop exists.
    rig.supervisor.confirm_sent(12)  # Report only after the actual send returns.
    return packet  # Retain bytes only for explicit adversarial duplicate tests.


def adopted_context(rig):
    """Reach child adoption through actual channel receipt and real codec checks."""
    packet = sent_context(rig)  # Establish supervisor send history in legal order.
    observed = (
        rig.right.receive()
    )  # Exactly one nonblocking receive, never a poll loop.
    assert observed == packet  # Independently compare the transported bytes.
    rig.worker.accept_context(
        observed, 13
    )  # Child checks against retained expectations.
    return packet  # Fault cases may deliberately inject a duplicate into the channel.


def sent_receipt(rig, *, confirm=True):
    """Send a real acknowledgement once; optionally omit the caller's confirmation."""
    adopted_context(rig)  # Keep the ordinary setup path identical to successful flow.
    packet = rig.worker.prepare_acknowledgement(14)  # One-time release only.
    rig.right.send(packet)  # Sending does not imply supervisor receipt or acceptance.
    if confirm:  # Deliberately omit this report in the ambiguous-send test.
        rig.worker.confirm_sent(15)  # Only a caller-reported historical send fact.
    return packet  # No readiness or execution authorization is returned.


def test_complete_exchange_preserves_exact_context_and_history(rig):
    original = rig.supervisor.snapshot()  # Previously returned history must stay fixed.
    context_packet = adopted_context(rig)  # Exercise actual sender and receiver halves.
    assert original.phase == "ready"  # No live reference into mutable controller state.
    assert (
        rig.supervisor.snapshot().phase == "waiting_ack"
    )  # Send is not child receipt.
    assert (
        rig.worker.snapshot().adopted == rig.expected
    )  # Every context field survived.
    assert (
        not rig.worker.snapshot().packet_released
    )  # Adoption does not imply a receipt.
    assert (
        not rig.supervisor.snapshot().acknowledgement_confirmed
    )  # No premature success.
    receipt = rig.worker.prepare_acknowledgement(14)  # Produce one matching receipt.
    rig.right.send(
        receipt
    )  # Complete the bounded whole-packet send before reporting it.
    rig.worker.confirm_sent(
        15
    )  # Child history still says nothing about parent receipt.
    assert (
        not rig.supervisor.snapshot().acknowledgement_confirmed
    )  # Send is not acceptance.
    observed = rig.left.receive()  # Receive exactly once from the real private channel.
    assert observed == receipt  # Do not manufacture expected bytes instead of reading.
    rig.codec.verify_acknowledgement(
        observed, rig.expected
    )  # Independent codec comparison.
    rig.supervisor.accept_acknowledgement(
        observed, 16
    )  # Consume one receipt before cutoff.
    assert (
        rig.supervisor.snapshot().phase == "confirmed"
    )  # Historical local completion.
    assert (
        rig.worker.snapshot().phase == "confirmed"
    )  # No arming or authority is implied.
    assert (
        rig.codec.decode_context(context_packet) == rig.expected
    )  # Exact original card.
    for endpoint in (
        rig.left,
        rig.right,
    ):  # No extra packet should appear in either queue.
        with pytest.raises(rig.transport.WouldBlock, match="^RECEIVE_WOULD_BLOCK$"):
            endpoint.receive()  # Unavailable input is explicit, never invented success.


@pytest.mark.parametrize("kind", ["malformed", "mismatch"])
def test_bad_context_from_channel_permanently_rejects_child(rig, kind):
    rig.supervisor.prepare_context(
        11
    )  # Retain the original attempt despite bad wire data.
    packet = (
        b"{"
        if kind == "malformed"
        else rig.codec.encode_context(
            replace(
                rig.expected, lifetime=replace(rig.expected.lifetime, watchdog_pid=4243)
            )
        )
    )  # A valid but wrong identity must fail independently of JSON syntax.
    rig.left.send(packet)  # Real transport accepts bounded bytes, not their meaning.
    rig.supervisor.confirm_sent(12)  # Successful send does not attest the contents.
    with pytest.raises(rig.child.ChildBootstrapError, match="^CONTEXT_REJECTED$"):
        rig.worker.accept_context(rig.right.receive(), 13)  # Reject actual wire input.
    assert rig.worker.snapshot().adopted is None  # No conflicting card was adopted.
    with pytest.raises(rig.child.ChildBootstrapError, match="^SESSION_FAILED$"):
        rig.worker.prepare_acknowledgement(14)  # No repaired or replacement receipt.
    assert (
        not rig.supervisor.snapshot().acknowledgement_confirmed
    )  # Parent stays unconfirmed.


@pytest.mark.parametrize("kind", ["malformed", "mismatch"])
def test_bad_receipt_from_channel_permanently_rejects_supervisor(rig, kind):
    adopted_context(rig)  # First establish the valid immutable child context.
    rig.worker.prepare_acknowledgement(
        14
    )  # Account for release before injected damage.
    packet = (
        b"{"
        if kind == "malformed"
        else rig.codec.encode_acknowledgement(replace(rig.expected, token="f" * 64))
    )  # A syntactically valid receipt for another context must not match.
    rig.right.send(packet)  # Keep fault injection at the actual wire boundary.
    rig.worker.confirm_sent(15)  # Child send history cannot validate parent input.
    with pytest.raises(
        rig.parent.BootstrapSessionError, match="^ACKNOWLEDGEMENT_REJECTED$"
    ):
        rig.supervisor.accept_acknowledgement(rig.left.receive(), 16)
    assert (
        not rig.supervisor.snapshot().acknowledgement_confirmed
    )  # No accepted receipt.
    with pytest.raises(rig.parent.BootstrapSessionError, match="^SESSION_FAILED$"):
        rig.supervisor.prepare_context(17)  # Failure must not issue a replacement card.


def test_duplicate_context_over_channel_is_terminal(rig):
    packet = adopted_context(rig)  # One legitimate context was already consumed.
    rig.left.send(
        packet
    )  # Adversarial duplicate; ordinary orchestration must not resend.
    with pytest.raises(rig.child.ChildBootstrapError, match="^WRONG_PHASE$"):
        rig.worker.accept_context(rig.right.receive(), 14)  # Even identical bytes fail.
    assert rig.worker.snapshot().adopted == rig.expected  # Preserve original adoption.
    with pytest.raises(rig.child.ChildBootstrapError, match="^SESSION_FAILED$"):
        rig.worker.prepare_acknowledgement(
            15
        )  # A duplicate cannot reopen receipt release.


def test_duplicate_receipt_over_channel_retains_first_acceptance_but_fails(rig):
    packet = sent_receipt(rig)  # Complete the ordinary child-side send path once.
    rig.supervisor.accept_acknowledgement(rig.left.receive(), 16)  # First acceptance.
    rig.right.send(
        packet
    )  # Inject an unauthorized second copy to exercise replay rejection.
    with pytest.raises(rig.parent.BootstrapSessionError, match="^WRONG_PHASE$"):
        rig.supervisor.accept_acknowledgement(rig.left.receive(), 17)
    assert rig.supervisor.snapshot().phase == "failed"  # No duplicate state transition.
    assert (
        rig.supervisor.snapshot().acknowledgement_confirmed
    )  # Historical fact survives.


def test_receipt_before_parent_send_confirmation_is_not_buffered(rig):
    packet = rig.supervisor.prepare_context(
        11
    )  # Parent has not recorded a send outcome.
    rig.left.send(
        packet
    )  # Real delivery may succeed despite missing caller confirmation.
    rig.worker.accept_context(rig.right.receive(), 12)  # Child can independently adopt.
    receipt = rig.worker.prepare_acknowledgement(
        13
    )  # Matching context still cannot skip order.
    rig.right.send(receipt)  # Actual acknowledgement is available at the parent.
    rig.worker.confirm_sent(
        14
    )  # This is not a substitute for the parent's send report.
    with pytest.raises(rig.parent.BootstrapSessionError, match="^WRONG_PHASE$"):
        rig.supervisor.accept_acknowledgement(rig.left.receive(), 15)
    assert not rig.supervisor.snapshot().send_confirmed  # Missing report stays missing.
    assert (
        not rig.supervisor.snapshot().acknowledgement_confirmed
    )  # No reordered acceptance.


@pytest.mark.parametrize("confirmed", [False, True])
def test_child_send_does_not_resolve_missing_parent_receipt(rig, confirmed):
    sent_receipt(
        rig, confirm=confirmed
    )  # Bytes are queued even if confirmation is absent.
    assert (
        rig.worker.snapshot().send_confirmed is confirmed
    )  # Keep the distinction explicit.
    assert (
        rig.supervisor.snapshot().phase == "waiting_ack"
    )  # No receive/accept took place.
    with pytest.raises(rig.parent.BootstrapSessionError, match="^BOOTSTRAP_EXPIRED$"):
        rig.supervisor.check(50)  # Expire without consuming a late queued receipt.
    with pytest.raises(rig.parent.BootstrapSessionError, match="^SESSION_FAILED$"):
        rig.supervisor.accept_acknowledgement(
            rig.left.receive(), 51
        )  # Reconcile only, no revival.
    assert (
        not rig.supervisor.snapshot().acknowledgement_confirmed
    )  # Delivery is not acceptance.
    assert (
        rig.worker.snapshot().adopted == rig.expected
    )  # Preserve ambiguous external history.
    if confirmed:  # A completed send stays historical, never an execution lease.
        rig.worker.check(50)  # Parent expiry does not erase the child's earlier send.
        assert rig.worker.snapshot().phase == "confirmed"  # Not parent acceptance.
    else:  # Missing confirmation leaves the child pending under its own fixed cutoff.
        with pytest.raises(rig.child.ChildBootstrapError, match="^BOOTSTRAP_EXPIRED$"):
            rig.worker.check(50)  # No resend or replacement receipt is permitted.
        assert rig.worker.snapshot().packet_released  # Preserve the ambiguous release.


@pytest.mark.parametrize("side", ["context", "receipt"])
def test_actual_packet_received_at_cutoff_is_rejected(rig, side):
    if side == "context":  # A queued context cannot renew the child's fixed cutoff.
        sent_context(rig)  # The wire contains one valid context.
        with pytest.raises(rig.child.ChildBootstrapError, match="^BOOTSTRAP_EXPIRED$"):
            rig.worker.accept_context(rig.right.receive(), 50)
        assert rig.worker.snapshot().adopted is None  # Late card was never adopted.
    else:  # The same deadline rule applies to the parent accepting a receipt.
        sent_receipt(rig)  # Both actual sends completed within the synthetic sequence.
        with pytest.raises(
            rig.parent.BootstrapSessionError, match="^BOOTSTRAP_EXPIRED$"
        ):
            rig.supervisor.accept_acknowledgement(rig.left.receive(), 50)
        assert (
            not rig.supervisor.snapshot().acknowledgement_confirmed
        )  # No late success.


@pytest.mark.parametrize("side", ["context", "receipt"])
def test_missing_input_is_explicit_and_pending_attempt_expires(rig, side):
    if side == "context":  # No card has been sent to the child.
        endpoint, controller = (
            rig.right,
            rig.worker,
        )  # Wait under original child budget.
        error = (
            rig.child.ChildBootstrapError
        )  # Preserve the existing public error type.
    else:  # Child adopted a card but has not released or sent any receipt.
        adopted_context(rig)  # Parent remains in its pending receipt phase.
        endpoint, controller = (
            rig.left,
            rig.supervisor,
        )  # No replacement send is attempted.
        error = (
            rig.parent.BootstrapSessionError
        )  # Require the supervisor's fixed error.
    with pytest.raises(rig.transport.WouldBlock, match="^RECEIVE_WOULD_BLOCK$"):
        endpoint.receive()  # Exactly one attempt; no sleep or retry loop.
    with pytest.raises(error, match="^BOOTSTRAP_EXPIRED$"):
        controller.check(50)  # Explicit observation expires even without any packet.
    assert controller.snapshot().phase == "failed"  # Empty queue never implies success.


def test_peer_closure_aborts_without_fabricating_a_receipt(rig):
    adopted_context(rig)  # Establish child adoption before a channel failure.
    rig.right.close()  # Simulate peer loss, not a malformed protocol message.
    with pytest.raises(
        rig.transport.TransportError, match="^EMPTY_OR_CLOSED$"
    ) as failure:
        rig.left.receive()  # Existing transport closes the failed endpoint itself.
    assert failure.value.cleanup_complete  # Verify the transport's cleanup report too.
    with pytest.raises(rig.parent.BootstrapSessionError, match="^BOOTSTRAP_ABORTED$"):
        rig.supervisor.abort(
            14
        )  # Trusted caller stops the unresolved bootstrap attempt.
    assert (
        not rig.supervisor.snapshot().acknowledgement_confirmed
    )  # No invented receipt.
