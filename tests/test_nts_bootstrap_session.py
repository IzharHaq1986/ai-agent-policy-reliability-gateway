"""Deterministic bootstrap transitions; no clocks, transport, or child I/O."""

import importlib  # Load only the reviewed internal test-support modules.

# Exercise immutable expectations/history.
from dataclasses import FrozenInstanceError, replace
from pathlib import Path  # Select the fixed helper directory deterministically.

import pytest  # Restore injected failures and require precise rejection reasons.


@pytest.fixture
def module(monkeypatch):
    """Importing this helper must not start a process or a message exchange."""
    # Fixed code path.
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "support"))
    # No caller-selected module.
    return importlib.import_module("nts_bootstrap_session")


@pytest.fixture
def codec(module):
    """Reuse the actual packet checker without replacing validation with a stub."""
    # This import performs no I/O.
    return importlib.import_module("nts_bootstrap_codec")


@pytest.fixture
def context(module):
    """Use synthetic references and explicit tiny monotonic values, not actual time."""
    # Reuse established field types.
    health = importlib.import_module("nts_health_protocol")
    return module.BootstrapContext(
        health.Lifetime(  # No real process or container is represented by these fields.
            boot_id="11111111-1111-4111-8111-111111111111",
            collection_id="22222222-2222-4222-8222-222222222222",
            container_id="d" * 64,  # Full-format synthetic container reference.
            owner="c" * 32,  # Ownership label syntax, not a credential.
            watchdog_pid=4242,  # No process lookup is performed.
            watchdog_start_ticks=9000,  # No kernel measurement is performed.
            # All test times are explicit, not wall-clock observations.
            deadline_ns=100,
        ),
        "e" * 64,  # Predictable sample token; generation and entropy are outside scope.
    )


def new_session(module, context):
    """Every test starts a fresh isolated instance with the same explicit limits."""
    return module.BootstrapSession(context, started_ns=10, cutoff_ns=50)


def waiting(module, context):
    """Reach waiting-ack through the real preparation and send-confirmation gates."""
    session = new_session(module, context)  # This helper supplies no I/O evidence.
    session.prepare_context(11)  # Release bytes exactly once through the state API.
    # Explicitly report a send; no actual packet is transmitted.
    session.confirm_sent(12)
    return session  # Tests still use actual codec matching for the acknowledgement.


def assert_failed(module, session, reason):
    """Failure cannot be rescued by later well-formed operations or fresh time."""
    snapshot = session.snapshot()  # Reading history cannot renew or reset the attempt.
    # Reuse a valid matching packet.
    codec = importlib.import_module("nts_bootstrap_codec")
    # Good input cannot rescue failure.
    valid = codec.encode_acknowledgement(snapshot.expected)
    assert snapshot.phase == "failed"  # The phase must already have latched.
    assert snapshot.failure_reason == reason  # Preserve the first fixed reason.
    for operation in (  # Every public progression path remains closed after failure.
        lambda: session.prepare_context(49),
        lambda: session.confirm_sent(49),
        lambda: session.accept_acknowledgement(valid, 49),
        lambda: session.check(49),
        lambda: session.abort(49),
    ):
        with pytest.raises(module.BootstrapSessionError, match="^SESSION_FAILED$"):
            operation()  # Even fresh time cannot reopen a terminal session.
    # Subsequent calls must not overwrite history.
    assert session.snapshot() == snapshot


def test_legal_progression_and_immutable_history(module, codec, context):
    # Validate configuration and retain expectations.
    session = new_session(module, context)
    # Initial history claims no released bytes or confirmation.
    ready = session.snapshot()
    assert ready.phase == "ready" and ready.cutoff_ns == 50
    assert ready.expected == context and ready.failure_reason is None
    assert not ready.packet_released and not ready.send_confirmed
    raw = session.prepare_context(11)  # One exact codec packet is returned.
    assert raw == codec.encode_context(context)  # No context replacement or new token.
    # Preparation cannot be confused with send confirmation.
    prepared = session.snapshot()
    assert prepared.phase == "send_unconfirmed" and prepared.packet_released
    assert not prepared.send_confirmed and not prepared.acknowledgement_confirmed
    session.confirm_sent(12)  # Record only the trusted caller's complete-send report.
    sent = session.snapshot()  # A send report does not establish child adoption.
    assert sent.phase == "waiting_ack" and sent.send_confirmed
    assert not sent.acknowledgement_confirmed
    assert (
        session.accept_acknowledgement(codec.encode_acknowledgement(context), 49)
        is None
    )
    # Equality before cutoff is only a historical result.
    confirmed = session.snapshot()
    assert confirmed.phase == "confirmed" and confirmed.acknowledgement_confirmed
    assert confirmed.expected == context and confirmed.cutoff_ns == 50
    assert ready.phase == "ready"  # Older immutable snapshots do not change in place.
    with pytest.raises(FrozenInstanceError):
        # Ordinary snapshot mutation cannot reset the session.
        confirmed.phase = "ready"


@pytest.mark.parametrize(
    "started,cutoff",
    [
        (True, 50),
        (10.0, 50),
        (-1, 50),
        (None, 50),
        (2**63, 50),
        (10, True),
        (10, 50.0),
        (10, None),
        (10, 0),
        (10, -1),
        (10, 10),
        (11, 10),
        (10, 101),
        (10, 2**63),
    ],
)
def test_invalid_configuration_rejected(module, context, started, cutoff):
    with pytest.raises(module.BootstrapSessionError, match="^INVALID_CONFIGURATION$"):
        module.BootstrapSession(context, started_ns=started, cutoff_ns=cutoff)


@pytest.mark.parametrize("invalid", [None, {}, True, "context"])
def test_foreign_context_rejected(module, invalid):
    with pytest.raises(module.BootstrapSessionError, match="^INVALID_CONFIGURATION$"):
        module.BootstrapSession(invalid, started_ns=10, cutoff_ns=50)


def test_invalid_typed_context_and_nonextended_deadline(module, context):
    # A typed wrapper does not bypass validation.
    invalid = replace(context, token="invalid")
    with pytest.raises(module.BootstrapSessionError, match="^INVALID_CONFIGURATION$"):
        new_session(module, invalid)
    equal = module.BootstrapSession(context, started_ns=10, cutoff_ns=100)
    # Equality is allowed.
    assert equal.snapshot().cutoff_ns == context.lifetime.deadline_ns
    with pytest.raises(module.BootstrapSessionError, match="^BOOTSTRAP_EXPIRED$"):
        equal.prepare_context(100)  # The original absolute deadline remains exclusive.


@pytest.mark.parametrize("stage", ["ready", "send_unconfirmed", "waiting_ack"])
@pytest.mark.parametrize("now", [50, 51])
def test_pending_expiry_without_receipt(module, context, stage, now):
    session = new_session(module, context)  # Begin with the unchanged original cutoff.
    if stage != "ready":
        session.prepare_context(11)  # Preserve preparation history on later expiry.
    if stage == "waiting_ack":
        session.confirm_sent(12)  # Missing acknowledgement can expire without a packet.
    with pytest.raises(module.BootstrapSessionError, match="^BOOTSTRAP_EXPIRED$"):
        session.check(now)  # Equality and later observations both fail pending startup.
    # Keep investigation references intact.
    assert session.snapshot().expected == context
    assert session.snapshot().packet_released is (stage != "ready")
    assert session.snapshot().send_confirmed is (stage == "waiting_ack")
    # No claim about actual adoption.
    assert not session.snapshot().acknowledgement_confirmed
    assert_failed(module, session, "BOOTSTRAP_EXPIRED")


@pytest.mark.parametrize("now", [True, 12.0, None, -1, 2**63, 11])
def test_invalid_or_backwards_time_latches_failure(module, context, now):
    session = waiting(module, context)  # The latest valid observation was 12.
    with pytest.raises(module.BootstrapSessionError, match="^INVALID_TIME$"):
        session.check(now)  # No coercion, clock-domain guess, or backwards reset.
    assert_failed(module, session, "INVALID_TIME")


def test_equal_time_is_allowed_but_not_a_fresh_budget(module, codec, context):
    # Nondecreasing time need not strictly increase.
    session = new_session(module, context)
    # Multiple legal operations may share one observation value.
    session.prepare_context(10)
    session.confirm_sent(10)  # State still enforces their order.
    session.accept_acknowledgement(codec.encode_acknowledgement(context), 10)
    assert session.snapshot().phase == "confirmed"  # No extra time was granted.
    assert session.snapshot().cutoff_ns == 50


@pytest.mark.parametrize("operation", ["prepare", "send", "receipt"])
def test_cutoff_checked_at_each_transition(module, codec, context, operation):
    # Do not rely only on a separate periodic check.
    session = new_session(module, context)
    if operation != "prepare":
        session.prepare_context(11)
    if operation == "receipt":
        session.confirm_sent(12)
    with pytest.raises(module.BootstrapSessionError, match="^BOOTSTRAP_EXPIRED$"):
        if operation == "prepare":
            session.prepare_context(50)  # No late release.
        elif operation == "send":
            session.confirm_sent(50)  # No late send confirmation.
        else:
            session.accept_acknowledgement(codec.encode_acknowledgement(context), 50)
    assert_failed(module, session, "BOOTSTRAP_EXPIRED")


@pytest.mark.parametrize(
    "phase,operation",
    [
        ("ready", "send"),
        ("ready", "receipt"),
        ("send_unconfirmed", "prepare"),
        ("send_unconfirmed", "receipt"),
        ("waiting_ack", "prepare"),
        ("waiting_ack", "send"),
        ("confirmed", "prepare"),
        ("confirmed", "send"),
        ("confirmed", "receipt"),
    ],
)
def test_wrong_phase_and_replayed_receipt_fail(
    module, codec, context, phase, operation
):
    session = new_session(module, context)  # Retain one expected context throughout.
    # The replay packet remains valid syntax.
    raw = codec.encode_acknowledgement(context)
    if phase != "ready":
        session.prepare_context(11)
    if phase in {"waiting_ack", "confirmed"}:
        session.confirm_sent(12)
    if phase == "confirmed":
        session.accept_acknowledgement(raw, 13)  # Record a legitimate first acceptance.
    history = session.snapshot()  # Capture facts before the violating operation.
    with pytest.raises(module.BootstrapSessionError, match="^WRONG_PHASE$"):
        if operation == "prepare":
            session.prepare_context(14)
        elif operation == "send":
            session.confirm_sent(14)
        else:
            # Never queue a receipt for a later phase.
            session.accept_acknowledgement(raw, 14)
    assert (
        session.snapshot().acknowledgement_confirmed
        == history.acknowledgement_confirmed
    )
    assert session.snapshot().send_confirmed == history.send_confirmed
    assert_failed(module, session, "WRONG_PHASE")


@pytest.mark.parametrize(
    "fault", ["malformed", "different_token", "different_lifetime", "context_packet"]
)
def test_rejected_receipt_permanently_fails(module, codec, context, fault):
    session = waiting(module, context)  # Only waiting-ack may invoke exact matching.
    packets = {  # Each fault exercises the real existing codec boundary.
        "malformed": b"{}",
        "different_token": codec.encode_acknowledgement(
            replace(context, token="f" * 64)
        ),
        "different_lifetime": codec.encode_acknowledgement(
            replace(context, lifetime=replace(context.lifetime, watchdog_pid=4243))
        ),
        "context_packet": codec.encode_context(context),
    }
    with pytest.raises(
        module.BootstrapSessionError, match="^ACKNOWLEDGEMENT_REJECTED$"
    ):
        session.accept_acknowledgement(packets[fault], 13)
    assert not session.snapshot().acknowledgement_confirmed
    assert_failed(module, session, "ACKNOWLEDGEMENT_REJECTED")


@pytest.mark.parametrize(
    "phase", ["ready", "send_unconfirmed", "waiting_ack", "confirmed"]
)
def test_explicit_abort_preserves_uncertainty_and_history(
    module, codec, context, phase
):
    # No external delivery is inferred from history.
    session = new_session(module, context)
    if phase != "ready":
        session.prepare_context(11)
    if phase in {"waiting_ack", "confirmed"}:
        session.confirm_sent(12)
    if phase == "confirmed":
        session.accept_acknowledgement(codec.encode_acknowledgement(context), 13)
    history = session.snapshot()  # Keep the actual sequence of controller observations.
    with pytest.raises(module.BootstrapSessionError, match="^BOOTSTRAP_ABORTED$"):
        # Caller-observed I/O failure never means adoption did not happen.
        session.abort(14)
    snapshot = session.snapshot()
    assert snapshot.packet_released == history.packet_released
    assert snapshot.send_confirmed == history.send_confirmed
    assert snapshot.acknowledgement_confirmed == history.acknowledgement_confirmed
    assert snapshot.expected == context and snapshot.cutoff_ns == 50
    assert_failed(module, session, "BOOTSTRAP_ABORTED")


def test_confirmed_history_is_not_a_time_lease(module, codec, context):
    session = waiting(module, context)
    session.accept_acknowledgement(codec.encode_acknowledgement(context), 49)
    # Completed bootstrap history does not expire into an authority claim.
    session.check(100)
    # This remains a past observation only.
    assert session.snapshot().phase == "confirmed"
    # A late status check cannot renew the cutoff.
    assert session.snapshot().cutoff_ns == 50
    with pytest.raises(module.BootstrapSessionError, match="^WRONG_PHASE$"):
        session.accept_acknowledgement(codec.encode_acknowledgement(context), 100)
    # Preserve historical acceptance.
    assert session.snapshot().acknowledgement_confirmed is True
    assert_failed(module, session, "WRONG_PHASE")


@pytest.mark.parametrize("interrupt", [False, True])
def test_unavailable_or_interrupted_validation_fails_closed(
    module, codec, context, monkeypatch, interrupt
):
    session = waiting(module, context)  # Preserve the actual preparation/send gates.

    def unavailable(*args):
        if interrupt:
            # Preserve operator interruption without allowing reuse.
            raise KeyboardInterrupt
        # Normalize unexpected errors.
        raise RuntimeError("PRIVATE_DETAIL_MUST_NOT_ESCAPE")

    # Fault only validation.
    monkeypatch.setattr(module, "verify_acknowledgement", unavailable)
    expected = KeyboardInterrupt if interrupt else module.BootstrapSessionError
    with pytest.raises(expected) as error:
        session.accept_acknowledgement(codec.encode_acknowledgement(context), 13)
    if not interrupt:
        # No private payload echoed.
        assert str(error.value) == "ACKNOWLEDGEMENT_UNAVAILABLE"
    assert_failed(
        module, session, "INTERRUPTED" if interrupt else "ACKNOWLEDGEMENT_UNAVAILABLE"
    )


def test_instance_is_not_persistent_replay_history(module, codec, context):
    # Both isolated instances see matching syntax.
    raw = codec.encode_acknowledgement(context)
    for _ in range(2):
        # Recreation is not prevented by a pure helper.
        session = waiting(module, context)
        session.accept_acknowledgement(raw, 13)
        # Launcher must forbid attempt reuse.
        assert session.snapshot().phase == "confirmed"
