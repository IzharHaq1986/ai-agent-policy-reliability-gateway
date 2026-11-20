"""One descriptor, one poll, original deadline; readiness is never authorization."""

import select  # Use fixed poll interests without a receive or retry loop.
import time  # Read local monotonic observations; their provenance is a host assumption.
from typing import Literal  # Distinguish input readiness from an early empty return.

from nts_health_protocol import _integer  # Reuse exact bounded nanosecond validation.
from nts_poll_timeout import poll_timeout_ms  # Reuse non-extending integer conversion.


class ReadinessWaitError(RuntimeError):
    """Expose fixed errors without private descriptors, times, or raw exceptions."""


def _observe(deadline_ns: int, previous_ns: int) -> int:
    """Reject invalid, backward, or expired observations within this call only."""
    try:  # Failed clock access cannot establish current eligibility.
        now_ns = time.monotonic_ns()  # No caller-supplied packet selects the clock.
    except Exception:
        raise ReadinessWaitError("CLOCK_UNAVAILABLE") from None  # Hide raw host errors.
    if not _integer(now_ns) or now_ns < previous_ns:
        raise ReadinessWaitError("INVALID_TIME")  # No coercion or backwards progress.
    if now_ns >= deadline_ns:  # Equality is expiry before or after any operation.
        raise ReadinessWaitError("DEADLINE_EXPIRED")  # Never construct a fresh budget.
    return now_ns  # A valid local reading alone establishes no trusted-time contract.


def wait_readable_once(fd: int, deadline_ns: int) -> Literal["input_ready", "no_input"]:
    """Borrow one stable descriptor; caller retains ownership and protocol gates."""
    if type(fd) is not int or not 2 < fd <= (1 << 31) - 1:
        raise ReadinessWaitError("INVALID_DESCRIPTOR")  # Never poll standard streams.
    if not _integer(deadline_ns, 1):  # Reject invalid configuration before setup.
        raise ReadinessWaitError(
            "INVALID_DEADLINE"
        )  # Do not coerce or renew a boundary.
    if not all(  # Refuse unsupported semantics without another wait API.
        hasattr(select, name)
        for name in ("poll", "POLLIN", "POLLHUP", "POLLERR", "POLLNVAL")
    ):
        raise ReadinessWaitError(
            "UNSUPPORTED_PLATFORM"
        )  # No fallback or partial setup.
    observed = _observe(
        deadline_ns, 0
    )  # Establish an initial within-call time witness.
    errors = (
        select.POLLHUP | select.POLLERR | select.POLLNVAL
    )  # Errors override readiness.
    try:  # Registration borrows the FD; it does not open, duplicate, or close it.
        poller = (
            select.poll()
        )  # A local registration cannot change another poller's state.
        poller.register(
            fd, select.POLLIN | errors
        )  # No writable or priority interests.
    except Exception:
        raise ReadinessWaitError("POLL_UNAVAILABLE") from None  # No setup retry.
    sampled = _observe(
        deadline_ns, observed
    )  # Setup time consumes the original budget.
    timeout_ms = poll_timeout_ms(
        deadline_ns, sampled
    )  # Only positive bounded integers.
    before_wait = _observe(
        deadline_ns, sampled
    )  # Catch delay during or after conversion.
    if timeout_ms * 1_000_000 > deadline_ns - before_wait:
        raise ReadinessWaitError(
            "STALE_TIMEOUT"
        )  # Do not recompute until one happens to fit.
    try:  # Invoke poll at most once; no fallback or interruption retry exists here.
        events = poller.poll(
            timeout_ms
        )  # Scheduler delay is not controlled by this timeout.
    except Exception:
        raise ReadinessWaitError(
            "POLL_UNAVAILABLE"
        ) from None  # Never fabricate readiness.
    # BaseException interruptions propagate; this stateless helper owns no descriptor.
    returned = _observe(
        deadline_ns, before_wait
    )  # Reject late returns even with POLLIN.
    if type(events) is not list or len(events) > 1:
        raise ReadinessWaitError(
            "INVALID_POLL_RESULT"
        )  # One registered FD has at most one entry.
    ready = False  # Only an empty, valid result can become the no-input outcome.
    if (
        events
    ):  # Validate one exact native event shape without accepting foreign objects.
        event = events[0]  # Index only after bounding the list length.
        if type(event) is not tuple or len(event) != 2:
            raise ReadinessWaitError(
                "INVALID_POLL_RESULT"
            )  # No alternate result schema.
        event_fd, flags = event  # Values remain untrusted until exact type checks pass.
        if type(event_fd) is not int or event_fd != fd or type(flags) is not int:
            raise ReadinessWaitError("INVALID_POLL_RESULT")  # Never follow another FD.
        if flags & errors:  # Simultaneous readable and closed/error evidence must fail.
            raise ReadinessWaitError(
                "POLL_EVENT_ERROR"
            )  # Do not infer precise peer state.
        if (
            flags != select.POLLIN
        ):  # Reject zero, unknown bits, and unsupported interests.
            raise ReadinessWaitError(
                "INVALID_POLL_RESULT"
            )  # No permissive flag masking.
        ready = (
            True  # Readiness still does not prove a complete or valid packet exists.
        )
    _observe(
        deadline_ns, returned
    )  # Include event-validation time before choosing a result.
    return "input_ready" if ready else "no_input"  # Neither outcome accepts a message.
