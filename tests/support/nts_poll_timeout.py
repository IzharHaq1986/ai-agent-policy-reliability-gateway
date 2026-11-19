"""Pure non-extending poll timeout conversion; no clock or scheduling proof."""

from nts_health_protocol import _integer  # Reuse exact bounded nanosecond validation.

_NANOSECONDS_PER_MILLISECOND = 1_000_000  # Poll accepts whole milliseconds only.
_MAX_POLL_MILLISECONDS = (
    1 << 31
) - 1  # Exclude signed overflow and infinite sentinels.


class PollTimeoutError(RuntimeError):
    """Expose fixed failure reasons without operational timestamps."""


def poll_timeout_ms(deadline_ns: int, now_ns: int) -> int:
    """Return a positive safe relative wait; callers retain the original deadline."""
    if not _integer(deadline_ns, 1) or not _integer(now_ns):
        raise PollTimeoutError("INVALID_TIME_INPUT")  # Never coerce untrusted values.
    remaining_ns = deadline_ns - now_ns  # Integer subtraction cannot round time upward.
    if remaining_ns <= 0:  # Equality is expiry, not a request to poll immediately.
        raise PollTimeoutError("DEADLINE_EXPIRED")  # A fresh budget is not substituted.
    timeout_ms = (
        remaining_ns // _NANOSECONDS_PER_MILLISECOND
    )  # Discard fractional time.
    if timeout_ms == 0:  # No positive whole-millisecond wait fits before this deadline.
        raise PollTimeoutError(
            "WAIT_UNREPRESENTABLE"
        )  # Do not spin or round up to one.
    if (
        timeout_ms > _MAX_POLL_MILLISECONDS
    ):  # Reject rather than clamp or wrap the wait.
        raise PollTimeoutError(
            "POLL_TIMEOUT_OVERFLOW"
        )  # Never return an infinite wait.
    return timeout_ms  # Actual wait start/return and clock provenance remain unchecked.
