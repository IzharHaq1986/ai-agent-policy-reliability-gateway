"""Readiness, packet receipt, and acceptance are separate gates; no process launch."""

import array  # Inject a deliberately forbidden ancillary FD into a real local packet.
import errno  # Require kernel-confirmed absence during test-owned descriptor cleanup.
import importlib  # Load fixed reviewed helpers without starting a process or watchdog.
import os  # Inspect and close only resources owned by this test fixture.
import socket  # Access native sendmsg only for adversarial transport input.
from dataclasses import replace  # Build valid but conflicting synthetic context values.
from pathlib import Path  # Select the fixed internal helper directory.
from types import SimpleNamespace  # Keep orchestration and fault controls test-only.

import pytest  # Check exact errors and restore injected boundary faults automatically.


class ProofStopped(RuntimeError):
    """Reject this test's overall step even if a controller retained earlier history."""


@pytest.fixture
def rig(monkeypatch):
    monkeypatch.syspath_prepend(
        str(Path(__file__).parent / "support")
    )  # Fixed imports.
    parent = importlib.import_module("nts_bootstrap_session")  # Actual parent phases.
    child = importlib.import_module(
        "nts_child_bootstrap_session"
    )  # Actual child phases.
    codec = importlib.import_module(
        "nts_bootstrap_codec"
    )  # Actual bounded JSON contract.
    health = importlib.import_module(
        "nts_health_protocol"
    )  # Shared immutable lifetime.
    transport = importlib.import_module(
        "nts_health_transport"
    )  # Actual packet operations.
    readiness = importlib.import_module(
        "nts_readiness_wait"
    )  # Actual one-shot polling.
    expected = codec.BootstrapContext(  # Synthetic values carry no runtime identity.
        health.Lifetime(
            boot_id="11111111-1111-4111-8111-111111111111",  # Synthetic host reference.
            collection_id="22222222-2222-4222-8222-222222222222",  # Test attempt.
            container_id="d"
            * 64,  # Container-shaped placeholder, not ownership evidence.
            owner="c" * 32,  # Fixed label does not authenticate the sender.
            watchdog_pid=4242,  # No child is launched or inspected by this proof.
            watchdog_start_ticks=9000,  # No actual kernel start observation is implied.
            deadline_ns=100_000_000,  # Explicit synthetic original lifetime boundary.
        ),
        token="e" * 64,  # Predictable fixture token is not an unpredictability proof.
    )
    state = SimpleNamespace(  # Expose only test-owned state, not a new runtime API.
        now=10_000_000,
        cutoff=50_000_000,
        completed=set(),
        expected=expected,
        parent=parent,
        child=child,
        codec=codec,
        transport=transport,
        readiness=readiness,
    )
    state.supervisor = parent.BootstrapSession(
        expected, started_ns=state.now, cutoff_ns=state.cutoff
    )  # Both controllers retain the original cutoff before any incoming packet.
    state.worker = child.ChildBootstrapSession(
        expected.lifetime, started_ns=state.now, cutoff_ns=state.cutoff
    )  # Child expectations never come from the received card.
    monkeypatch.setattr(
        readiness.time, "monotonic_ns", lambda: state.now
    )  # Synthetic clock.
    left, right = transport.create_pair(buffer_bytes=8192, effective_buffer_limit=16384)
    descriptors = ()  # Record real handles before exposing them to the test.
    extra = None  # Ancillary injection owns a separate descriptor only when requested.
    try:
        descriptors = (
            left._owned().fileno(),
            right._owned().fileno(),
        )  # Kernel handles.
        state.left, state.right = left, right  # Parent and child directions are fixed.
        extra = os.open(
            "/dev/null", os.O_RDONLY | os.O_CLOEXEC
        )  # Test-owned ancillary source.
        state.extra = extra  # Never transfer ownership of this source to the helper.
        yield state  # Native operations stay real except targeted faults.
    finally:
        try:
            left.close()  # Close even if readiness or protocol checks rejected input.
        finally:
            try:
                right.close()  # Attempt both endpoint releases independently.
            finally:
                if extra is not None:
                    os.close(extra)  # Close only the test-owned ancillary source once.
        for descriptor in (*descriptors, *((extra,) if extra is not None else ())):
            with pytest.raises(OSError) as failure:
                os.fstat(
                    descriptor
                )  # No descriptor acquisition intervenes after closure.
            assert (
                failure.value.errno == errno.EBADF
            )  # Other errors do not prove absence.


def before_cutoff(rig):
    """This test gate observes completion independently of controller history."""
    if rig.readiness.time.monotonic_ns() >= rig.cutoff:
        raise ProofStopped("COMPLETION_EXPIRED")  # Original deadline never renews.


def receive_and_accept(rig, side):
    """Test-only composition of existing APIs; no retry, receive loop, or new helper."""
    if side == "context":  # Only the two fixed protocol directions exist in this proof.
        endpoint, accept = rig.right, rig.worker.accept_context
    else:  # Test parameters are fixed, never untrusted external dispatch instructions.
        assert (
            side == "receipt"
        )  # Reject accidental expansion of this test-only helper.
        endpoint, accept = rig.left, rig.supervisor.accept_acknowledgement
    status = rig.readiness.wait_readable_once(endpoint._owned().fileno(), rig.cutoff)
    if status != "input_ready":  # Empty input is unresolved, not an invented message.
        raise ProofStopped("NO_INPUT")  # This test path stops rather than retrying.
    before_cutoff(rig)  # A pause after readiness can consume the remaining allowance.
    packet = (
        endpoint.receive()
    )  # Read at most one complete packet through real transport.
    before_cutoff(
        rig
    )  # Receiving bytes cannot establish timely validation eligibility.
    accept(packet, rig.readiness.time.monotonic_ns())  # Real codec and state checks.
    before_cutoff(rig)  # Reject overall completion if validation consumed the cutoff.
    rig.completed.add(
        side
    )  # Mark only test steps that passed the final independent gate.
    return packet  # Returned bytes still establish no operational authority.


def queued_context(rig):
    packet = rig.supervisor.prepare_context(
        rig.now
    )  # Release once, no replacement card.
    rig.left.send(packet)  # Native complete-packet send before caller confirmation.
    rig.supervisor.confirm_sent(rig.now)  # Report local send only after it returns.
    return (
        packet  # Adversarial cases may replace these bytes before sending separately.
    )


def queue_for_side(rig, side):
    """Reach each direction through real sends and the real readiness gate."""
    context = queued_context(rig)  # Context is queued on the actual socket pair.
    if side == "context":
        return context  # Child has not yet received or adopted anything.
    assert side == "receipt"  # Keep fault setup restricted to the two fixed directions.
    receive_and_accept(rig, "context")  # Adoption passes the complete gate sequence.
    receipt = rig.worker.prepare_acknowledgement(
        rig.now
    )  # Release one matching receipt.
    rig.right.send(receipt)  # Native send does not establish parent acceptance.
    rig.worker.confirm_sent(
        rig.now
    )  # Child send history stays separate from completion.
    return receipt  # Parent still has to receive and validate the actual packet.


def test_complete_readiness_receive_and_acceptance_sequence(rig):
    context = queued_context(rig)  # Parent is awaiting a receipt, not confirmed yet.
    assert (
        rig.worker.snapshot().adopted is None
    )  # Queued context is not child adoption.
    assert (
        receive_and_accept(rig, "context") == context
    )  # Real poll then receive/validate.
    assert (
        rig.worker.snapshot().adopted == rig.expected
    )  # Every original field matches.
    receipt = rig.worker.prepare_acknowledgement(
        rig.now
    )  # Prepare once after adoption.
    rig.right.send(receipt)  # Keep readiness distinct from this successful send.
    rig.worker.confirm_sent(rig.now)  # Child history does not establish parent receipt.
    assert (
        not rig.supervisor.snapshot().acknowledgement_confirmed
    )  # No premature acceptance.
    assert (
        receive_and_accept(rig, "receipt") == receipt
    )  # Separate parent acceptance gate.
    assert rig.completed == {
        "context",
        "receipt",
    }  # Both final completion checks passed.
    assert (
        rig.supervisor.snapshot().acknowledgement_confirmed
    )  # Historical acceptance only.


@pytest.mark.parametrize("side", ["context", "receipt"])
@pytest.mark.parametrize("kind", ["malformed", "mismatch"])
def test_readable_packet_can_fail_real_protocol_validation(rig, side, kind):
    if side == "context":  # Prepare a send without first queuing a valid card.
        rig.supervisor.prepare_context(rig.now)  # Preserve legal release history.
        sender = rig.left  # Child direction receives the actual injected packet.
        error, reason = rig.child.ChildBootstrapError, "CONTEXT_REJECTED"
        other = replace(
            rig.expected, lifetime=replace(rig.expected.lifetime, watchdog_pid=4243)
        )
        packet = rig.codec.encode_context(
            other
        )  # Valid syntax but wrong expected identity.
    else:
        queued_context(
            rig
        )  # First establish valid child adoption through the real gates.
        receive_and_accept(rig, "context")
        rig.worker.prepare_acknowledgement(rig.now)  # Keep one-time release history.
        sender = rig.right  # Parent receives a wrong or damaged receipt.
        error, reason = rig.parent.BootstrapSessionError, "ACKNOWLEDGEMENT_REJECTED"
        packet = rig.codec.encode_acknowledgement(replace(rig.expected, token="f" * 64))
    sender.send(
        b"{" if kind == "malformed" else packet
    )  # Native transport accepts bounded bytes.
    if side == "context":
        rig.supervisor.confirm_sent(
            rig.now
        )  # Send completion does not attest packet meaning.
    else:
        rig.worker.confirm_sent(
            rig.now
        )  # Child confirmation cannot validate parent input.
    with pytest.raises(error, match=f"^{reason}$"):
        receive_and_accept(
            rig, side
        )  # Readiness succeeds, but actual validation rejects.
    assert side not in rig.completed  # Overall step never becomes accepted completion.
    controller = (
        rig.worker if side == "context" else rig.supervisor
    )  # Inspect rejected owner.
    assert (
        controller.snapshot().phase == "failed"
    )  # Controller rejection remains terminal.


@pytest.mark.parametrize("side", ["context", "receipt"])
def test_pause_after_readiness_prevents_packet_receipt(rig, monkeypatch, side):
    queued = queue_for_side(
        rig, side
    )  # Actual complete packet is available before polling.
    original = (
        rig.readiness.wait_readable_once
    )  # Preserve the real native poll operation.

    def delayed(fd, deadline):
        result = original(
            fd, deadline
        )  # Readiness genuinely succeeded within fake time.
        rig.now = rig.cutoff  # Inject a pause immediately after readiness returns.
        return result  # Readiness cannot renew the cutoff it was observed under.

    monkeypatch.setattr(rig.readiness, "wait_readable_once", delayed)
    with pytest.raises(ProofStopped, match="^COMPLETION_EXPIRED$"):
        receive_and_accept(rig, side)
    receiver = (
        rig.right if side == "context" else rig.left
    )  # Inspect actual queued bytes.
    assert (
        receiver.receive() == queued
    )  # Reconciliation only: failed step did not read them.
    assert side not in rig.completed  # A readable late card never became completion.


@pytest.mark.parametrize("side", ["context", "receipt"])
def test_expiry_after_receipt_prevents_validation(rig, monkeypatch, side):
    queue_for_side(rig, side)  # Reach the real readable channel boundary.
    endpoint = (
        rig.right if side == "context" else rig.left
    )  # Choose the fixed recipient.
    original = endpoint.receive  # Keep native packet retrieval intact.

    def delayed():
        packet = original()  # Obtain actual complete bytes before the injected pause.
        rig.now = rig.cutoff  # Time expires before protocol acceptance can start.
        return packet  # Packet possession does not establish temporal eligibility.

    monkeypatch.setattr(endpoint, "receive", delayed)
    with pytest.raises(ProofStopped, match="^COMPLETION_EXPIRED$"):
        receive_and_accept(rig, side)
    assert (
        side not in rig.completed
    )  # Do not validate a packet whose receipt completed late.
    if side == "context":
        assert rig.worker.snapshot().adopted is None  # No late adoption occurred.
    else:
        assert (
            not rig.supervisor.snapshot().acknowledgement_confirmed
        )  # No late receipt acceptance.


@pytest.mark.parametrize("side", ["context", "receipt"])
def test_validation_completion_can_reject_earlier_controller_history(
    rig, monkeypatch, side
):
    queue_for_side(rig, side)  # Queue actual bytes using ordinary controller order.
    controller = (
        rig.worker if side == "context" else rig.supervisor
    )  # Local state owner.
    method = "accept_context" if side == "context" else "accept_acknowledgement"
    original = getattr(controller, method)  # Do not stub codec or phase validation.

    def delayed(packet, now):
        original(packet, now)  # Real controller records valid supplied-time history.
        rig.now = (
            rig.cutoff
        )  # Inject delay before orchestration sees validation completion.

    monkeypatch.setattr(controller, method, delayed)
    with pytest.raises(ProofStopped, match="^COMPLETION_EXPIRED$"):
        receive_and_accept(rig, side)
    assert (
        side not in rig.completed
    )  # Historical controller transition is not overall acceptance.
    if side == "context":
        assert (
            controller.snapshot().adopted == rig.expected
        )  # Do not erase adoption history.
        assert (
            not controller.snapshot().packet_released
        )  # Failed completion issues no receipt.
    else:
        assert (
            controller.snapshot().acknowledgement_confirmed
        )  # Keep the earlier local fact.
        assert (
            controller.snapshot().phase == "confirmed"
        )  # This is not delayed admission authority.


@pytest.mark.parametrize("side", ["context", "receipt"])
def test_input_disappearing_after_readiness_never_fabricates_receipt(
    rig, monkeypatch, side
):
    queue_for_side(rig, side)  # A real poll will see actual readable input.
    endpoint = rig.right if side == "context" else rig.left  # Fixed intended recipient.
    original = (
        endpoint.receive
    )  # Native receive still enforces whole packet boundaries.

    def raced():
        original()  # Model interference, not normal single ownership.
        return (
            original()
        )  # Actual empty channel raises WouldBlock instead of returning bytes.

    monkeypatch.setattr(endpoint, "receive", raced)
    with pytest.raises(rig.transport.WouldBlock, match="^RECEIVE_WOULD_BLOCK$"):
        receive_and_accept(rig, side)
    assert (
        side not in rig.completed
    )  # No retry, replacement message, or invented acceptance.
    if side == "context":
        assert (
            rig.worker.snapshot().adopted is None
        )  # Readiness did not become adoption.
    else:
        assert (
            not rig.supervisor.snapshot().acknowledgement_confirmed
        )  # No fabricated receipt.


@pytest.mark.parametrize("side", ["context", "receipt"])
def test_empty_poll_result_stops_before_receipt(rig, monkeypatch, side):
    queued = queue_for_side(
        rig, side
    )  # Keep actual input for an independent no-read check.

    class EmptyPoll:
        def register(self, fd, flags):
            pass  # Targeted early-empty fault; ordinary cases use actual registration.

        def poll(self, timeout):
            return []  # Inject an empty poll result; keep real result validation.

    monkeypatch.setattr(
        rig.readiness.select, "poll", EmptyPoll
    )  # Restore after this case.
    with pytest.raises(ProofStopped, match="^NO_INPUT$"):
        receive_and_accept(rig, side)
    receiver = (
        rig.right if side == "context" else rig.left
    )  # Confirm no packet was consumed.
    assert (
        receiver.receive() == queued
    )  # No readiness result means no test-path receive.
    assert side not in rig.completed  # No empty-result success or new waiting budget.


@pytest.mark.parametrize("side", ["context", "receipt"])
@pytest.mark.parametrize("bad", ["truncated", "ancillary"])
def test_readable_forbidden_transport_input_is_not_reassembled(rig, side, bad):
    queue_for_side(rig, side)  # Reach the intended direction with a valid packet first.
    receiver = (
        rig.right if side == "context" else rig.left
    )  # Retain actual endpoint owner.
    sender = rig.left if side == "context" else rig.right  # Fixed opposite direction.
    receiver.receive()  # Drain setup bytes without acceptance before injecting a fault.
    if bad == "truncated":
        oversized = b"x" * (
            rig.transport.PACKET_BYTES + 1
        )  # Exceed one bounded receive.
        assert sender._owned().send(oversized) == len(
            oversized
        )  # Adversarial native bypass.
        reason = (
            "PACKET_TRUNCATED"  # Transport must reject, never assemble a remainder.
        )
    else:
        assert (
            sender._owned().sendmsg(  # Kernel transfers a duplicate, not our source FD.
                [b"forbidden"],
                [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [rig.extra]))],
            )
            == len(b"forbidden")
        )  # Unexpected ancillary evidence must not reach the codec.
        reason = (
            "ANCILLARY_REJECTED"  # Existing transport discards unexpected received FDs.
        )
    before = len(
        os.listdir("/proc/self/fd")
    )  # Independently watch receive-time FD residue.
    with pytest.raises(rig.transport.TransportError, match=f"^{reason}$") as failure:
        receive_and_accept(
            rig, side
        )  # Native readiness precedes the real transport rejection.
    assert (
        failure.value.cleanup_complete
    )  # The failed receiver reports releasing its own FD.
    assert (
        len(os.listdir("/proc/self/fd")) == before - 1
    )  # No hidden ancillary duplicate remains.
    assert (
        side not in rig.completed
    )  # No protocol transition or partial-read retry occurred.
    os.fstat(
        rig.extra
    )  # The caller-owned ancillary source was not closed by receiving.


@pytest.mark.parametrize("side", ["context", "receipt"])
def test_peer_closure_rejects_readiness_before_protocol_acceptance(rig, side):
    queue_for_side(
        rig, side
    )  # Queue actual data alongside the impending closure signal.
    peer = (
        rig.left if side == "context" else rig.right
    )  # Close only the fixture-owned peer.
    peer.close()  # Both readability and hangup may now be present.
    with pytest.raises(rig.readiness.ReadinessWaitError, match="^POLL_EVENT_ERROR$"):
        receive_and_accept(rig, side)
    assert (
        side not in rig.completed
    )  # Error flags cannot become a trusted card or receipt.


def test_actual_empty_channel_does_not_accept_a_context(rig):
    with pytest.raises(ProofStopped, match="^NO_INPUT$"):
        receive_and_accept(
            rig, "context"
        )  # One native poll returns with no queued data.
    assert rig.worker.snapshot().adopted is None  # Empty input is not a context.
    assert (
        rig.completed == set()
    )  # No retry or new cutoff turns absence into acceptance.
    # Synthetic clock observations establish no elapsed-time guarantee.
