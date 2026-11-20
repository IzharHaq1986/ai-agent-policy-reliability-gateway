"""One-shot polling with deterministic time faults and real packet-socket evidence."""

import errno  # Independently distinguish closed owned descriptors from other errors.
import importlib  # Load fixed reviewed internal helpers without launching a process.
import os  # Verify caller ownership and actual test resource release.
import socket  # Exercise native private packet readiness without consuming input.
from pathlib import Path  # Locate helpers through a fixed test-owned path.
from types import SimpleNamespace  # Keep fault controls in test code, not runtime APIs.

import pytest  # Require exact errors and restore all injected faults after each case.


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(
        str(Path(__file__).parent / "support")
    )  # Fixed imports.
    return importlib.import_module("nts_readiness_wait")  # Import starts no wait.


@pytest.fixture
def fake(module, monkeypatch):
    state = SimpleNamespace(  # Explicit synthetic times and native-shaped poll outputs.
        times=[0, 0, 0, 0, 0],  # Initial, sampled, pre-wait, return, final readings.
        result=[],  # Empty native-shaped return means no input, never acceptance.
        error=None,  # Inject a specific failure only when a test requests it.
        setups=0,  # Count creation of local poll registrations.
        registrations=[],  # Retain exact fixed interests for independent assertions.
        calls=[],  # Count Python-level wait invocations and their timeouts.
        readings=0,  # Verify where time checks happened before any result.
    )

    def clock():
        state.readings += 1  # Count observations independently of the adapter's result.
        sample = state.times.pop(
            0
        )  # Unexpected extra reads fail rather than repeat time.
        if isinstance(sample, BaseException):
            raise sample  # Model unavailable or interrupted clock access.
        return sample  # Deliberately allow malformed values to test runtime validation.

    class Poller:
        def register(self, fd, flags):
            state.registrations.append(
                (fd, flags)
            )  # Inspect exact interests and ownership.

        def poll(self, timeout):
            state.calls.append(timeout)  # Record the invocation even when it raises.
            if state.error is not None:
                raise state.error  # No second attempt may follow this failure.
            return state.result  # Real shape checks remain in the production helper.

    def factory():
        state.setups += (
            1  # Prove expired or invalid initial input cannot set up a wait.
        )
        return Poller()  # A new local registration is not a caller-owned poll object.

    monkeypatch.setattr(
        module.time, "monotonic_ns", clock
    )  # Restore host function afterward.
    monkeypatch.setattr(
        module.select, "poll", factory
    )  # No actual wait in fault cases.
    return (
        state  # Tests choose finite observations; there is no sleeping or retry loop.
    )


@pytest.mark.parametrize("ready", [False, True])
def test_single_poll_returns_explicit_outcome(module, fake, ready):
    fake.result = (
        [(7, module.select.POLLIN)] if ready else []
    )  # Exact native list shape.
    assert module.wait_readable_once(7, 10_000_000) == (
        "input_ready" if ready else "no_input"
    )  # Empty return is not an accepted packet or inferred deadline renewal.
    assert fake.calls == [10]  # Exactly one positive, bounded millisecond wait.
    assert fake.readings == 5  # Bracket setup, conversion, poll, and result validation.
    assert fake.registrations == [
        (
            7,
            module.select.POLLIN
            | module.select.POLLHUP
            | module.select.POLLERR
            | module.select.POLLNVAL,
        )
    ]
    # The interests exclude writable/priority readiness and preserve error precedence.


@pytest.mark.parametrize("fd", [True, None, "7", 7.0, -1, 0, 1, 2, 2**31])
def test_invalid_descriptor_never_sets_up_poll(module, fake, fd):
    with pytest.raises(module.ReadinessWaitError, match="^INVALID_DESCRIPTOR$"):
        module.wait_readable_once(
            fd, 10_000_000
        )  # Do not coerce an external reference.
    assert fake.setups == 0 and fake.calls == []  # No poll or registration can occur.


@pytest.mark.parametrize("deadline", [True, None, "10000000", 1.0, -1, 0, 2**63])
def test_invalid_deadline_never_sets_up_poll(module, fake, deadline):
    with pytest.raises(module.ReadinessWaitError, match="^INVALID_DEADLINE$"):
        module.wait_readable_once(
            7, deadline
        )  # Reject configuration before clock access.
    assert fake.readings == 0 and fake.setups == 0  # No partial wait was attempted.


@pytest.mark.parametrize("stage", range(5))
@pytest.mark.parametrize("bad", [True, None, 1.0, -1, 2**63])
def test_invalid_clock_at_every_boundary(module, fake, stage, bad):
    fake.times[stage] = bad  # Inject after setup, conversion, poll, or validation too.
    with pytest.raises(module.ReadinessWaitError, match="^INVALID_TIME$"):
        module.wait_readable_once(7, 10_000_000)
    assert len(fake.calls) == (
        1 if stage >= 3 else 0
    )  # No retry after clock rejection.


@pytest.mark.parametrize("stage", [1, 2, 3, 4])
def test_backward_clock_at_every_later_boundary(module, fake, stage):
    fake.times = [100] * 5  # Keep the earlier observations internally consistent.
    fake.times[stage] = 99  # Only the selected boundary moves backward.
    with pytest.raises(module.ReadinessWaitError, match="^INVALID_TIME$"):
        module.wait_readable_once(7, 10_000_100)
    assert len(fake.calls) == (
        1 if stage >= 3 else 0
    )  # Backward time is never readiness.


@pytest.mark.parametrize("stage", range(5))
@pytest.mark.parametrize("late", [10_000_000, 10_000_001])
def test_expiry_before_or_after_wait_overrides_readiness(module, fake, stage, late):
    fake.times[stage] = (
        late  # Equality and later values both exhaust the same deadline.
    )
    fake.result = [
        (7, module.select.POLLIN)
    ]  # Even readable input cannot rescue expiry.
    with pytest.raises(module.ReadinessWaitError, match="^DEADLINE_EXPIRED$"):
        module.wait_readable_once(7, 10_000_000)
    assert len(fake.calls) == (1 if stage >= 3 else 0)  # No late result is retried.


def test_setup_delay_is_included_in_fresh_conversion(module, fake):
    fake.times = [0, 2_500_000, 2_500_000, 2_500_000, 2_500_000]
    assert (
        module.wait_readable_once(7, 10_000_000) == "no_input"
    )  # No message fabricated.
    assert fake.calls == [
        7
    ]  # Setup consumed time; the initial ten milliseconds is stale.


def test_conversion_delay_rejects_stale_timeout_without_recalculation(module, fake):
    fake.times = [
        0,
        0,
        1,
        1,
        1,
    ]  # Ten exact milliseconds fit only at the earlier reading.
    with pytest.raises(module.ReadinessWaitError, match="^STALE_TIMEOUT$"):
        module.wait_readable_once(7, 10_000_000)
    assert (
        fake.calls == [] and fake.readings == 3
    )  # No wait or recalculation loop occurs.


def test_small_delay_within_round_down_margin_is_allowed(module, fake):
    fake.times = [
        0,
        0,
        499_999,
        499_999,
        499_999,
    ]  # Existing fractional margin covers delay.
    assert module.wait_readable_once(7, 10_500_000) == "no_input"
    assert fake.calls == [
        10
    ]  # The unchanged requested wait still fits the last observation.


@pytest.mark.parametrize(
    "deadline,reason",
    [
        (999_999, "WAIT_UNREPRESENTABLE"),  # No positive native poll interval fits.
        (
            2_147_483_648_000_000,
            "POLL_TIMEOUT_OVERFLOW",
        ),  # Do not wrap or clamp a wait.
    ],
)
def test_conversion_errors_propagate_without_poll(module, fake, deadline, reason):
    timeout = importlib.import_module("nts_poll_timeout")  # Use the existing converter.
    with pytest.raises(timeout.PollTimeoutError, match=f"^{reason}$"):
        module.wait_readable_once(7, deadline)
    assert fake.calls == []  # Invalid native waits must never reach the poller.


@pytest.mark.parametrize(
    "result",
    [
        None,
        (),
        [(7, 1), (7, 1)],
        [7],
        [[7, 1]],
        [(7,)],
        [(7, 1, 2)],
        [(8, 1)],
        [(True, 1)],
        [(7, True)],
        [(7, "1")],
        [(7, 0)],
        [(7, 2**20)],
    ],
)
def test_malformed_poll_results_never_become_input(module, fake, result):
    fake.result = result  # No mock bypasses the actual result validator.
    with pytest.raises(module.ReadinessWaitError, match="^INVALID_POLL_RESULT$"):
        module.wait_readable_once(7, 10_000_000)
    assert fake.calls == [10]  # Rejected evidence does not cause a second wait.


@pytest.mark.parametrize("flag", ["POLLHUP", "POLLERR", "POLLNVAL"])
@pytest.mark.parametrize("with_input", [False, True])
def test_error_flags_override_simultaneous_input(module, fake, flag, with_input):
    flags = getattr(
        module.select, flag
    )  # Each documented native error must fail closed.
    fake.result = [(7, flags | (module.select.POLLIN if with_input else 0))]
    with pytest.raises(module.ReadinessWaitError, match="^POLL_EVENT_ERROR$"):
        module.wait_readable_once(7, 10_000_000)
    assert fake.calls == [10]  # Closure does not authorize another attempt.


@pytest.mark.parametrize("error", [OSError("private"), InterruptedError("private")])
def test_poll_failure_is_sanitized_and_not_retried(module, fake, error):
    fake.error = error  # Include interruption returned as an ordinary OS exception.
    with pytest.raises(
        module.ReadinessWaitError, match="^POLL_UNAVAILABLE$"
    ) as failure:
        module.wait_readable_once(7, 10_000_000)
    assert "private" not in str(failure.value)  # No raw host diagnostics reach the API.
    assert fake.calls == [10]  # Never repeat an interrupted native operation here.


@pytest.mark.parametrize("stage", range(5))
def test_clock_failure_is_sanitized(module, fake, stage):
    fake.times[stage] = OSError("private")  # Test each available clock observation.
    with pytest.raises(
        module.ReadinessWaitError, match="^CLOCK_UNAVAILABLE$"
    ) as failure:
        module.wait_readable_once(7, 10_000_000)
    assert "private" not in str(failure.value)  # Preserve fixed, payload-free failures.


@pytest.mark.parametrize("where", ["clock", "poll"])
def test_operator_interruption_propagates_without_retry(module, fake, where):
    if where == "clock":  # The stateless helper has no lifecycle to reopen or reset.
        fake.times[0] = KeyboardInterrupt()
    else:  # Preserve operator interruption during the sole actual poll invocation.
        fake.error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        module.wait_readable_once(7, 10_000_000)
    assert len(fake.calls) == (1 if where == "poll" else 0)  # No fallback or retry.


def test_registration_failure_is_sanitized(module, fake, monkeypatch):
    def denied():
        raise OSError("private")  # Simulate unavailable poll setup, not clock failure.

    monkeypatch.setattr(module.select, "poll", denied)  # Restore after the single case.
    with pytest.raises(
        module.ReadinessWaitError, match="^POLL_UNAVAILABLE$"
    ) as failure:
        module.wait_readable_once(7, 10_000_000)
    assert "private" not in str(failure.value) and fake.calls == []  # No wait occurred.


@pytest.fixture
def sockets():
    left, right = socket.socketpair(  # Native private transport, not a fake FD object.
        socket.AF_UNIX,
        socket.SOCK_SEQPACKET | socket.SOCK_NONBLOCK | socket.SOCK_CLOEXEC,
    )
    descriptors = (
        left.fileno(),
        right.fileno(),
    )  # Retain ownership witnesses for teardown.
    try:
        yield left, right  # Only this fixture owns and closes these descriptors.
    finally:
        try:
            left.close()  # No helper error may transfer this caller-owned close duty.
        finally:
            right.close()  # Always attempt both endpoint releases.
        for descriptor in descriptors:  # No FD-acquiring work intervenes after closure.
            with pytest.raises(OSError) as failure:
                os.fstat(
                    descriptor
                )  # Check kernel absence independently of close return.
            assert (
                failure.value.errno == errno.EBADF
            )  # Other errors are not closure proof.


def test_real_packet_readiness_does_not_consume_or_close(module, sockets, monkeypatch):
    left, right = sockets  # Real poll and socket operations remain unmodified.
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: 0)  # Synthetic time only.
    assert right.send(b"synthetic card") == len(
        b"synthetic card"
    )  # Queue one whole packet.
    before = os.fstat(
        left.fileno()
    )  # Preserve actual caller-owned descriptor identity.
    assert module.wait_readable_once(left.fileno(), 10_000_000) == "input_ready"
    assert (
        os.fstat(left.fileno()) == before
    )  # Readiness did not close or replace the FD.
    assert (
        left.recv(64) == b"synthetic card"
    )  # Input remains for independent protocol checks.
    assert (
        not left.getblocking() and not left.get_inheritable()
    )  # Mode stays unchanged.


def test_real_peer_closure_is_rejected_without_closing_borrowed_fd(
    module, sockets, monkeypatch
):
    left, right = sockets  # Exercise actual kernel closure flags, not injected output.
    monkeypatch.setattr(
        module.time, "monotonic_ns", lambda: 0
    )  # No elapsed-time claim.
    right.close()  # Close only the peer owned by this test fixture.
    with pytest.raises(module.ReadinessWaitError, match="^POLL_EVENT_ERROR$"):
        module.wait_readable_once(left.fileno(), 10_000_000)
    os.fstat(
        left.fileno()
    )  # Even on error, the borrowed endpoint belongs to the caller.


def test_registration_method_failure_never_polls(module, fake, monkeypatch):
    class Denied:
        def register(self, fd, flags):
            raise OSError(
                "private"
            )  # Fail the registration method after object creation.

    monkeypatch.setattr(
        module.select, "poll", Denied
    )  # A missing poll method must be unused.
    with pytest.raises(module.ReadinessWaitError, match="^POLL_UNAVAILABLE$"):
        module.wait_readable_once(7, 10_000_000)
    assert fake.calls == []  # Failed setup cannot advance to the sole poll operation.


def test_missing_native_semantics_have_no_fallback(module, fake, monkeypatch):
    monkeypatch.delattr(
        module.select, "POLLIN"
    )  # Restore the actual constant after the case.
    with pytest.raises(module.ReadinessWaitError, match="^UNSUPPORTED_PLATFORM$"):
        module.wait_readable_once(7, 10_000_000)
    assert (
        fake.setups == 0 and fake.readings == 0
    )  # No weaker waiting API is substituted.
