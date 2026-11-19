"""Child-side historical phases only; no I/O, identity observation, or authority."""

from dataclasses import dataclass  # Freeze diagnostic history and adopted context.
from typing import Literal, NoReturn  # Enumerate phases and terminal failure paths.

from nts_bootstrap_codec import (  # Preserve the existing complete packet contract.
    BootstrapContext,  # Retain the immutable decoded context after exact matching.
    BootstrapError,  # Hide malformed packet contents behind fixed error labels.
    decode_context,  # Never treat acknowledgement packets as incoming context.
    encode_acknowledgement,  # Prepare a receipt without claiming delivery.
)
from nts_health_protocol import (  # Share validation rather than copy field rules.
    HealthError,  # Normalize invalid trusted initialization values.
    Lifetime,  # Expected values come from trusted launch and local observations.
    _integer,  # Reject coerced, negative, or oversized monotonic observations.
    _lifetime,  # Check all seven retained lifetime fields before reading a packet.
)

Phase = Literal[  # None of these phases grants operational authority.
    "waiting_context", "adopted", "send_unconfirmed", "confirmed", "failed"
]


class ChildBootstrapError(RuntimeError):
    """Expose fixed reasons without private packet or exception details."""


@dataclass(frozen=True)
class ChildBootstrapSnapshot:
    """Immutable historical facts; a false flag does not prove external absence."""

    phase: Phase  # This describes one controller instance only.
    expected: Lifetime  # Never substitute received fields for original expectations.
    cutoff_ns: int  # Reading history never renews the original startup cutoff.
    adopted: BootstrapContext | None  # Preserve adoption even after later failure.
    packet_released: bool  # Release is not evidence that the receipt was sent.
    send_confirmed: bool  # This records a trusted caller report, not parent receipt.
    failure_reason: str | None  # Keep the first failure rather than overwrite it.


class ChildBootstrapSession:
    """Require one trusted owner and independently established initialization."""

    def __init__(self, expected: Lifetime, *, started_ns: int, cutoff_ns: int) -> None:
        try:  # Validate launch expectations before examining any received context.
            _lifetime(expected)  # Syntax checking cannot establish input provenance.
        except HealthError:
            raise ChildBootstrapError("INVALID_CONFIGURATION") from None
        if (  # Keep the fixed allowance inside the retained original deadline.
            not _integer(started_ns)
            or not _integer(cutoff_ns, 1)
            or not started_ns < cutoff_ns <= expected.deadline_ns
        ):
            raise ChildBootstrapError("INVALID_CONFIGURATION")  # No usable instance.
        self._expected = expected  # Trusted caller supplies actual local identity.
        self._cutoff_ns = cutoff_ns  # A packet cannot choose its own waiting budget.
        self._last_ns = (
            started_ns  # Require one consistent monotonic observation domain.
        )
        self._phase: Phase = "waiting_context"  # No context has been adopted yet.
        self._adopted: BootstrapContext | None = None  # Missing context stays unknown.
        self._packet: bytes | None = None  # Hold at most one bounded receipt.
        self._released = False  # Record packet release separately from send outcome.
        self._sent = False  # Absence of a confirmation cannot establish send failure.
        self._failure: str | None = None  # The first failure remains inspectable.

    def snapshot(self) -> ChildBootstrapSnapshot:
        """Return history without reading a clock or restoring eligibility."""
        return ChildBootstrapSnapshot(  # Expose values without a mutable container.
            self._phase,  # Preserve the current local phase.
            self._expected,  # Retain independently established expectations.
            self._cutoff_ns,  # Never refresh the waiting allowance.
            self._adopted,  # Keep adoption history after a failure.
            self._released,  # Release is separate from delivery.
            self._sent,  # Reported send does not prove parent receipt.
            self._failure,  # First failure remains the investigation starting point.
        )

    def _latch(self, reason: str) -> None:
        """Retain references while permanently preventing further receipt release."""
        if self._phase != "failed":  # Preserve the first reason across later calls.
            self._failure = reason  # Only fixed internal labels enter this path.
        self._phase = "failed"  # No reset or replacement-context API exists.
        self._packet = None  # Never retry an uncertain send with retained bytes.

    def _fail(self, reason: str) -> NoReturn:
        self._latch(reason)  # Latch before exposing the error to external code.
        raise ChildBootstrapError(reason) from None  # Keep errors payload-free.

    def check(self, now_ns: int) -> None:
        """Expire pending work when called; external code owns scheduling."""
        if self._phase == "failed":  # Good input cannot revive a failed instance.
            raise ChildBootstrapError(
                "SESSION_FAILED"
            )  # Leave original history intact.
        if not _integer(now_ns) or now_ns < self._last_ns:
            self._fail("INVALID_TIME")  # Reject backwards or invalid observations.
        self._last_ns = (
            now_ns  # Equal times are allowed, but phase order still applies.
        )
        if self._phase != "confirmed" and now_ns >= self._cutoff_ns:
            self._fail(
                "BOOTSTRAP_EXPIRED"
            )  # Equality is expiry in every pending phase.
        # Confirmed state remains historical and never becomes a continuing lease.

    def _require(self, phase: Phase) -> None:
        if self._phase != phase:  # A duplicate valid packet is still a protocol error.
            self._fail("WRONG_PHASE")  # Do not buffer unexpected operations for later.

    def accept_context(self, packet: bytes, now_ns: int) -> None:
        """Adopt one exact lifetime; packet token origin remains externally verified."""
        self.check(now_ns)  # Packet arrival never extends the original allowance.
        self._require(
            "waiting_context"
        )  # No replacement or duplicate context is valid.
        try:  # Failed or interrupted validation must not leave a reusable instance.
            context = decode_context(packet)  # Apply actual bounded codec validation.
            if (
                context.lifetime != self._expected
            ):  # Compare every pre-established field.
                raise BootstrapError(
                    "MISMATCH"
                )  # Never adopt packet-selected identity.
            receipt = encode_acknowledgement(
                context
            )  # Prepare the full matching receipt.
            self._adopted = (
                context  # Retain immutable values only after all checks pass.
            )
            self._packet = receipt  # Release is a separate one-time transition.
            self._phase = "adopted"  # Adoption is not arming or execution permission.
        except BootstrapError:
            self._fail("CONTEXT_REJECTED")  # No packet contents escape in errors.
        except Exception:
            self._fail(
                "CONTEXT_UNAVAILABLE"
            )  # Unknown validation failure stays closed.
        except BaseException:
            self._latch(
                "INTERRUPTED"
            )  # Preserve operator interruption without revival.
            raise  # Trusted caller must reconcile external state independently.

    def prepare_acknowledgement(self, now_ns: int) -> bytes:
        self.check(now_ns)  # Receipt release must remain before the original cutoff.
        self._require("adopted")  # Do not release before adoption or a second time.
        packet = self._packet  # Use only the receipt prepared from validated context.
        if (
            packet is None
        ):  # Broken internal state must not silently reconstruct output.
            self._fail("PACKET_UNAVAILABLE")  # No replacement transmission is allowed.
        self._packet = (
            None  # Clear the releasable copy before returning to caller code.
        )
        self._released = True  # Record history even if external delivery later fails.
        self._phase = "send_unconfirmed"  # Missing confirmation remains ambiguous.
        return packet  # No send or parent acceptance is established by these bytes.

    def confirm_sent(self, now_ns: int) -> None:
        self.check(now_ns)  # A late caller report cannot revive pending startup.
        self._require("send_unconfirmed")  # Reject early or repeated confirmations.
        self._sent = True  # Trust the caller report without claiming observed I/O.
        self._phase = "confirmed"  # Parent receipt and arming remain separate gates.

    def abort(self, now_ns: int) -> NoReturn:
        self.check(now_ns)  # Preserve invalid-time and expiry reasons when applicable.
        self._fail(
            "BOOTSTRAP_ABORTED"
        )  # Retain adoption and release facts for diagnosis.
