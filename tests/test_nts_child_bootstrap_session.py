"""Pure child progression with real codec checks and synthetic launch expectations."""

import importlib  # Import reviewed helpers without invoking a process or transport.
from dataclasses import (
    FrozenInstanceError,
    replace,
)  # Exercise immutable field changes.
from pathlib import Path  # Locate fixed internal helpers rather than caller paths.

import pytest  # Check exact failures and restore injected validator faults.


@pytest.fixture
def parts(monkeypatch):
    monkeypatch.syspath_prepend(
        str(Path(__file__).parent / "support")
    )  # Fixed helper path.
    child = importlib.import_module("nts_child_bootstrap_session")  # No I/O on import.
    codec = importlib.import_module(
        "nts_bootstrap_codec"
    )  # Use real packet validation.
    health = importlib.import_module(
        "nts_health_protocol"
    )  # Shared immutable lifetime.
    context = codec.BootstrapContext(  # Predictable synthetic token conveys no trust.
        health.Lifetime(
            "1" * 8 + "-1111-4111-8111-" + "1" * 12,
            "2" * 8 + "-2222-4222-8222-" + "2" * 12,
            "d" * 64,
            "c" * 32,
            4242,
            9000,
            100,
        ),
        "e" * 64,
    )
    return child, codec, context  # No actual local identity or clock is represented.


def session(parts, phase="waiting_context"):
    child, codec, context = parts  # Retain independently supplied expected fields.
    value = child.ChildBootstrapSession(context.lifetime, started_ns=10, cutoff_ns=50)
    if phase != "waiting_context":  # Advance only through real public operations.
        value.accept_context(codec.encode_context(context), 11)
    if phase in ("send_unconfirmed", "confirmed"):  # Release without claiming delivery.
        value.prepare_acknowledgement(12)
    if phase == "confirmed":  # Record a caller assertion only after packet release.
        value.confirm_sent(13)
    return value  # Each test owns a fresh instance and explicit time observations.


def failed(parts, value, reason):
    child, codec, context = parts  # Good inputs must never revive failed state.
    saved = value.snapshot()  # Retain history before attempting all public operations.
    assert saved.phase == "failed"  # Check state as well as exception text.
    assert saved.failure_reason == reason  # First failure must remain visible.
    for operation in (  # Exercise every route that could otherwise advance startup.
        lambda: value.check(49),
        lambda: value.accept_context(codec.encode_context(context), 49),
        lambda: value.prepare_acknowledgement(49),
        lambda: value.confirm_sent(49),
        lambda: value.abort(49),
    ):
        with pytest.raises(child.ChildBootstrapError, match="^SESSION_FAILED$"):
            operation()  # A fresh matching packet is insufficient after failure.
        assert value.snapshot() == saved  # No history or expectation may be rewritten.


def test_progression_and_frozen_history(parts):
    child, codec, context = parts  # Actual codec verifies the generated receipt.
    value = session(parts)  # Begin without an adopted context.
    before = value.snapshot()  # Old snapshots must stay unchanged after progression.
    value.accept_context(codec.encode_context(context), 10)  # Equal time is allowed.
    adopted = value.snapshot()  # Adoption precedes receipt release.
    assert adopted.adopted == context  # All original packet fields survive unchanged.
    assert not adopted.packet_released  # Adoption does not establish a send.
    packet = value.prepare_acknowledgement(10)  # Release exactly one bounded receipt.
    codec.verify_acknowledgement(packet, context)  # Compare every receipt field.
    assert (
        not value.snapshot().send_confirmed
    )  # Missing confirmation remains ambiguous.
    value.confirm_sent(10)  # Record the trusted caller assertion.
    value.check(1000)  # Historical completion is not a continuing health lease.
    assert value.snapshot().phase == "confirmed"  # No expiry claim applies to history.
    assert (
        before.phase == "waiting_context"
    )  # Previously returned history is immutable.
    with pytest.raises(FrozenInstanceError):
        adopted.phase = "confirmed"  # Callers cannot edit a frozen snapshot normally.
    with pytest.raises(FrozenInstanceError):
        adopted.adopted.token = "f" * 64  # Nested retained values are frozen too.


@pytest.mark.parametrize("phase", ["waiting_context", "adopted", "send_unconfirmed"])
@pytest.mark.parametrize("now", [50, 51])
def test_pending_expiry_without_packet(parts, phase, now):
    child, _, _ = parts  # Expiry must work even if no new message ever arrives.
    value = session(parts, phase)  # Exercise every incomplete phase.
    before = value.snapshot()  # Failure retains adoption and release facts.
    with pytest.raises(child.ChildBootstrapError, match="^BOOTSTRAP_EXPIRED$"):
        value.check(now)  # Equality and later observations expire the pending attempt.
    assert value.snapshot().adopted == before.adopted  # Preserve original job card.
    assert (
        value.snapshot().packet_released == before.packet_released
    )  # Preserve history.
    failed(
        parts, value, "BOOTSTRAP_EXPIRED"
    )  # Valid later input cannot rescue startup.


@pytest.mark.parametrize("now", [True, 1.0, None, -1, 2**63, 9])
def test_invalid_time_is_terminal(parts, now):
    child, _, _ = parts  # No type coercion or backward monotonic observation is valid.
    value = session(parts)  # Initial observation is ten.
    with pytest.raises(child.ChildBootstrapError, match="^INVALID_TIME$"):
        value.check(now)  # Fail before any state progression.
    failed(parts, value, "INVALID_TIME")  # First reason survives future calls.


@pytest.mark.parametrize(
    "started,cutoff",
    [(True, 50), (10, False), (10, 10), (10, 101), (-1, 50), (10, 2**63)],
)
def test_invalid_configuration(parts, started, cutoff):
    child, _, context = parts  # Invalid limits must not create a usable controller.
    with pytest.raises(child.ChildBootstrapError, match="^INVALID_CONFIGURATION$"):
        child.ChildBootstrapSession(
            context.lifetime, started_ns=started, cutoff_ns=cutoff
        )


@pytest.mark.parametrize(
    "field,replacement",
    [  # Every lifetime field is binding.
        ("boot_id", "33333333-3333-4333-8333-333333333333"),
        ("collection_id", "33333333-3333-4333-8333-333333333333"),
        ("container_id", "f" * 64),
        ("owner", "f" * 32),
        ("watchdog_pid", 4243),
        ("watchdog_start_ticks", 9001),
        ("deadline_ns", 99),
    ],
)
def test_each_expected_field_must_match(parts, field, replacement):
    child, codec, context = parts  # Generate a syntactically valid conflicting card.
    other = replace(context, lifetime=replace(context.lifetime, **{field: replacement}))
    value = session(parts)  # Retain the original independent expectations.
    with pytest.raises(child.ChildBootstrapError, match="^CONTEXT_REJECTED$"):
        value.accept_context(codec.encode_context(other), 11)
    assert value.snapshot().adopted is None  # Do not adopt conflicting packet values.
    failed(
        parts, value, "CONTEXT_REJECTED"
    )  # A subsequent correct card stays rejected.


@pytest.mark.parametrize("packet", [b"", b"{}", b"x" * 2049, b"\xff", None])
def test_malformed_context(parts, packet):
    child, _, _ = parts  # Exercise the real bounded codec boundary.
    value = session(parts)  # No parsing failure may leave the instance reusable.
    with pytest.raises(child.ChildBootstrapError, match="^CONTEXT_REJECTED$"):
        value.accept_context(packet, 11)
    failed(parts, value, "CONTEXT_REJECTED")  # No repaired packet is retried.


@pytest.mark.parametrize(
    "phase,operation",
    [  # Wrong order and duplicates are terminal.
        ("waiting_context", "prepare"),
        ("waiting_context", "confirm"),
        ("adopted", "accept"),
        ("adopted", "confirm"),
        ("send_unconfirmed", "accept"),
        ("send_unconfirmed", "prepare"),
        ("confirmed", "accept"),
        ("confirmed", "prepare"),
        ("confirmed", "confirm"),
    ],
)
def test_wrong_phase(parts, phase, operation):
    child, codec, context = parts  # Matching bytes still cannot justify repetition.
    value = session(parts, phase)  # Establish only the specified legal history.
    actions = {
        "accept": lambda: value.accept_context(codec.encode_context(context), 14),
        "prepare": lambda: value.prepare_acknowledgement(14),
        "confirm": lambda: value.confirm_sent(14),
    }  # No I/O is performed.
    with pytest.raises(child.ChildBootstrapError, match="^WRONG_PHASE$"):
        actions[operation]()  # Reject before a second release or confirmation.
    failed(parts, value, "WRONG_PHASE")  # Preserve historical confirmation if present.


@pytest.mark.parametrize("phase", ["waiting_context", "adopted", "send_unconfirmed"])
def test_each_transition_cutoff(parts, phase):
    child, codec, context = parts  # Deadline checks must guard actual operations too.
    value = session(parts, phase)  # Reach the selected pending boundary normally.
    with pytest.raises(child.ChildBootstrapError, match="^BOOTSTRAP_EXPIRED$"):
        if phase == "waiting_context":  # Incoming context cannot extend its budget.
            value.accept_context(codec.encode_context(context), 50)
        elif phase == "adopted":  # Do not release a late receipt.
            value.prepare_acknowledgement(50)
        else:  # An ambiguous late send report cannot restore the conversation.
            value.confirm_sent(50)
    failed(parts, value, "BOOTSTRAP_EXPIRED")  # Every expiry permanently latches.


@pytest.mark.parametrize(
    "phase", ["waiting_context", "adopted", "send_unconfirmed", "confirmed"]
)
def test_abort_preserves_history(parts, phase):
    child, _, _ = parts  # Stopping never claims external delivery did not happen.
    value = session(parts, phase)  # Include the completed historical phase.
    before = value.snapshot()  # Compare retained facts after abort.
    with pytest.raises(child.ChildBootstrapError, match="^BOOTSTRAP_ABORTED$"):
        value.abort(14)
    assert (
        value.snapshot().adopted == before.adopted
    )  # Keep original investigation data.
    assert (
        value.snapshot().send_confirmed == before.send_confirmed
    )  # Do not erase facts.
    failed(parts, value, "BOOTSTRAP_ABORTED")  # Abort cannot become a reset operation.


@pytest.mark.parametrize(
    "error,reason",
    [
        (RuntimeError("private"), "CONTEXT_UNAVAILABLE"),
        (KeyboardInterrupt(), "INTERRUPTED"),
    ],
)
def test_unavailable_validation_latches(parts, monkeypatch, error, reason):
    child, codec, context = (
        parts  # Fault injection must not bypass normal phase checks.
    )
    value = session(parts)  # No adoption exists when validation begins.

    def broken(packet):
        raise error  # Simulate unavailable or interrupted internal validation.

    monkeypatch.setattr(
        child, "decode_context", broken
    )  # Restore automatically after test.
    expected = (
        KeyboardInterrupt if reason == "INTERRUPTED" else child.ChildBootstrapError
    )
    with pytest.raises(expected) as observed:
        value.accept_context(codec.encode_context(context), 11)
    assert "private" not in str(observed.value)  # Do not leak raw unexpected errors.
    failed(parts, value, reason)  # Interruptions must not permit later adoption.


def test_new_instance_is_not_persistent_replay_protection(parts):
    _, codec, context = parts  # Document the deliberate cross-instance trust boundary.
    for _ in range(2):  # A recreated controller has no knowledge of earlier history.
        value = session(parts)  # Trusted orchestration must forbid blind recreation.
        value.accept_context(codec.encode_context(context), 11)
        assert (
            value.snapshot().adopted == context
        )  # Syntax and local state cannot attest freshness.


@pytest.mark.parametrize("expected", [None, {}, True])
def test_foreign_expected_lifetime(parts, expected):
    child, _, _ = parts  # Foreign objects cannot define trusted initialization.
    with pytest.raises(child.ChildBootstrapError, match="^INVALID_CONFIGURATION$"):
        child.ChildBootstrapSession(expected, started_ns=10, cutoff_ns=50)


def test_receipt_cannot_be_adopted_as_context(parts):
    child, codec, context = parts  # Bootstrap namespaces must remain separate.
    value = session(parts)  # Start with independent expected lifetime values.
    with pytest.raises(child.ChildBootstrapError, match="^CONTEXT_REJECTED$"):
        value.accept_context(codec.encode_acknowledgement(context), 11)
    failed(
        parts, value, "CONTEXT_REJECTED"
    )  # Correct field values cannot rescue wrong kind.


def test_missing_prepared_packet_fails_without_rebuilding(parts):
    child, _, _ = parts  # Inject broken internal storage to test closed failure.
    value = session(parts, "adopted")  # Adoption was already recorded normally.
    value._packet = (
        None  # Simulate unavailable cached receipt without modifying expectations.
    )
    with pytest.raises(child.ChildBootstrapError, match="^PACKET_UNAVAILABLE$"):
        value.prepare_acknowledgement(12)
    failed(parts, value, "PACKET_UNAVAILABLE")  # Do not reconstruct and retry bytes.
