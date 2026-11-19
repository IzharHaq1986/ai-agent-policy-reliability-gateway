"""Single-owner in-memory bootstrap phases; no delivery, clock, or authority proof."""

# Return immutable historical facts for investigation.
from dataclasses import dataclass

# Keep legal phases explicit and failures terminal.
from typing import Literal, NoReturn

from nts_bootstrap_codec import (  # Reuse existing syntax and exact-receipt checks.
    BootstrapContext,  # Retain the separately supplied immutable expected context.
    BootstrapError,  # Normalize rejected codec inputs without echoing their contents.
    encode_context,  # Validate and encode one complete context before releasing bytes.
    verify_acknowledgement,  # Compare every receipt field with retained expectations.
)
from nts_health_protocol import _integer  # Reuse exact monotonic-integer bounds.

Phase = Literal[  # These are protocol phases, never operational permission levels.
    "ready",  # No context bytes have been released by this controller.
    "send_unconfirmed",  # Bytes were released; delivery outcome is not established.
    "waiting_ack",  # Trusted caller reported one complete send, not child adoption.
    "confirmed",  # One matching receipt was accepted under the fixed cutoff.
    "failed",  # This instance cannot be reopened or rebound.
]


class BootstrapSessionError(RuntimeError):
    """Expose fixed failure reasons without packets or operational identifiers."""


@dataclass(frozen=True)
class BootstrapSnapshot:
    """Historical controller observations, not readiness or proof of external state."""

    phase: Phase  # Current phase is not permission to start a workload.
    expected: BootstrapContext  # Keep original references even after failure.
    cutoff_ns: int  # This fixed cutoff is never replaced by a later observation.
    # Bytes returned to a caller do not establish actual delivery.
    packet_released: bool
    # This records a trusted caller's report, not an I/O observation.
    send_confirmed: bool
    # Preserve an earlier acceptance even after failure.
    acknowledgement_confirmed: bool
    # Retain the first fixed failure label, never raw errors.
    failure_reason: str | None


class BootstrapSession:
    """Require trusted single-owner code and supplied host-monotonic observations."""

    def __init__(
        self, expected: BootstrapContext, *, started_ns: int, cutoff_ns: int
    ) -> None:
        try:  # Construction validates context without generating or sending a token.
            # Reuse the codec's complete schema checks.
            packet = encode_context(expected)
        except BootstrapError:
            raise BootstrapSessionError("INVALID_CONFIGURATION") from None
        # Missing, coerced, backwards, or extended timing configuration is invalid.
        if (
            not _integer(started_ns)
            or not _integer(cutoff_ns, 1)
            or not started_ns < cutoff_ns <= expected.lifetime.deadline_ns
        ):
            # No usable session exists.
            raise BootstrapSessionError("INVALID_CONFIGURATION")
        # Never replace expectations with received packet values.
        self._expected = expected
        # The caller establishes provenance before construction.
        self._cutoff_ns = cutoff_ns
        # Nondecreasing observations stay in the supplied domain.
        self._last_ns = started_ns
        # Retain at most one bounded prepared packet.
        self._packet: bytes | None = packet
        self._phase: Phase = "ready"  # No bytes have yet been released to the caller.
        # This is controller history, not external delivery evidence.
        self._released = False
        # A missing report must not imply that a send did not happen.
        self._sent = False
        # A missing receipt must not imply absence of adoption.
        self._acknowledged = False
        self._failure: str | None = None  # The first failure label remains available.

    def snapshot(self) -> BootstrapSnapshot:
        """Read immutable history without time reads or revival."""
        return BootstrapSnapshot(
            phase=self._phase,  # No mutable state container is exposed to the caller.
            # Preserve original context and investigation references.
            expected=self._expected,
            cutoff_ns=self._cutoff_ns,  # A snapshot never renews the cutoff.
            # Do not reinterpret this as receipt by a child.
            packet_released=self._released,
            # This remains a report made by trusted caller code.
            send_confirmed=self._sent,
            # Preserve previously observed facts.
            acknowledgement_confirmed=self._acknowledged,
            # Do not echo any original exception or packet.
            failure_reason=self._failure,
        )

    def _latch(self, reason: str) -> None:
        """Fail this instance while retaining original references and facts."""
        if self._phase != "failed":  # Never overwrite the first reason on a later call.
            self._failure = reason  # Only fixed internal labels reach this operation.
        self._phase = "failed"  # No transition may restore this instance to ready.
        # Never release context bytes again after an uncertain failure.
        self._packet = None

    def _fail(self, reason: str) -> NoReturn:
        """Latch before raising so another call cannot recover the failed attempt."""
        # History is retained even if an exception is caught externally.
        self._latch(reason)
        raise BootstrapSessionError(reason) from None  # Keep the failure payload-free.

    def check(self, now_ns: int) -> None:
        """Check supplied time and pending expiry; scheduling is external."""
        # Preserve the first failure and all historical flags.
        if self._phase == "failed":
            # Never reopen on later good input.
            raise BootstrapSessionError("SESSION_FAILED")
        if not _integer(now_ns) or now_ns < self._last_ns:
            # Booleans and backwards observations cannot advance.
            self._fail("INVALID_TIME")
        # Record only a correctly typed nondecreasing observation.
        self._last_ns = now_ns
        if self._phase != "confirmed" and now_ns >= self._cutoff_ns:
            # Equality is expiry for every pending phase.
            self._fail("BOOTSTRAP_EXPIRED")
        # Confirmed history is not a health lease or continuing authority.

    def _require(self, phase: Phase) -> None:
        """Reject wrong phases without buffering for later acceptance."""
        # Identical bytes do not make a repeated operation valid.
        if self._phase != phase:
            # A duplicate also permanently fails this instance.
            self._fail("WRONG_PHASE")

    def prepare_context(self, now_ns: int) -> bytes:
        """Release one packet; do not infer that it was sent or adopted."""
        self.check(now_ns)  # All startup work consumes the original fixed allowance.
        # No second preparation or replacement token is possible.
        self._require("ready")
        packet = self._packet  # Take only the packet validated during construction.
        if packet is None:  # Broken internal state must never become permissive output.
            # Do not rebuild or silently replace context.
            self._fail("PACKET_UNAVAILABLE")
        # Transition before handing bytes to caller code.
        self._phase = "send_unconfirmed"
        self._released = True  # Preserve this fact if send confirmation is later lost.
        # The controller cannot release a second copy through this API.
        self._packet = None
        # Trusted caller still owns delivery, error handling, and evidence.
        return packet

    def confirm_sent(self, now_ns: int) -> None:
        """Record a caller-confirmed whole-packet send, not a child's adoption."""
        self.check(now_ns)  # A late confirmation cannot revive the pending attempt.
        # Reject confirmation before preparation or twice.
        self._require("send_unconfirmed")
        # This records an external trusted assertion, not observed I/O.
        self._sent = True
        self._phase = "waiting_ack"  # Only this phase may accept the matching receipt.

    def accept_acknowledgement(self, packet: bytes, now_ns: int) -> None:
        """Accept one receipt before cutoff without granting authority."""
        # Missing receipt handling does not require a packet to expire.
        self.check(now_ns)
        self._require("waiting_ack")  # Early, duplicate, and unexpected receipts fail.
        # Verify against original context, never expectations derived from the receipt.
        try:
            # Reuse exact schema and equality.
            verify_acknowledgement(packet, self._expected)
            self._acknowledged = True  # Record one successful bounded comparison.
            # This supplies no arming, health, or authorization.
            self._phase = "confirmed"
        # Malformed or mismatched receipts cannot rescue startup.
        except BootstrapError:
            self._fail("ACKNOWLEDGEMENT_REJECTED")
        # Unavailable validation also fails without leaking raw errors.
        except Exception:
            self._fail("ACKNOWLEDGEMENT_UNAVAILABLE")
        # Preserve interruption control while preventing later reuse.
        except BaseException:
            # No interrupted validation may reopen the session.
            self._latch("INTERRUPTED")
            # Let the trusted caller handle interruption and independently clean up.
            raise

    def abort(self, now_ns: int) -> NoReturn:
        """Stop without claiming that delivery or adoption did not happen."""
        # Invalid time or pending expiry retain their own failure labels.
        self.check(now_ns)
        # There is no resend, rebind, or reset operation.
        self._fail("BOOTSTRAP_ABORTED")
