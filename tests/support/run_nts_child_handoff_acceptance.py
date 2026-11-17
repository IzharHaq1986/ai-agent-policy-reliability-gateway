"""Opt-in fixed-child inheritance proof; no general launcher or watchdog wiring."""

import json  # Parse bounded fixture reports and emit small private proof records.
import math  # Reject non-finite timing budgets before creating resources.
import os  # Retain pidfds and inspect only the owned direct child.
import secrets  # Generate an unpredictable token for this synthetic exchange.
import select  # Observe socket readiness and child exit without busy waiting.
import signal  # Signal only the retained owned child during bounded cleanup.
import stat  # Compare kernel socket type and device identity in the retained child.
import subprocess  # Launch only the adjacent reviewed harmless fixture.
import sys  # Select this validation interpreter and the explicit opt-in CLI.
import threading  # Reject competing Python-thread ownership in this isolated proof.
import time  # Keep one original monotonic startup budget and separate cleanup budget.
import uuid  # Give each synthetic launch a distinct collection identity.
from pathlib import Path  # Resolve fixed reviewed files without PATH search.

from nts_command_capture import QUERY_BYTES  # Reuse the existing bounded-read ceiling.

# Reuse the bounded host boot observation.
from nts_container_watchdog import (
    WatchdogError,
    boot_id,
)

# Keep synthetic packets within existing limits.
from nts_health_protocol import (
    PACKET_BYTES,
    HealthError,  # Reject protocol failures without granting execution authority.
    Lifetime,  # Bind expected values to trusted launch observations.
    Session,  # Reuse the existing single-outstanding-challenge validator.
)

# Use existing bounded transport without changing its API.
from nts_health_transport import (
    Endpoint,  # Track parent-side ownership through the existing transport objects.
    # Preserve validation and close uncertainty as failed proof evidence.
    TransportError,
    WouldBlock,  # Wait only under the original startup budget when receive has no data.
    create_pair,  # Configure both ends under explicit accepted buffer bounds.
)

_HEALTH_TIMEOUT_NS = 1_000_000_000  # One-second synthetic limit, not a clock threshold.
_BUFFER_BYTES = 8192  # Explicit synthetic transport setting, not a production default.
_BUFFER_CEILING = 16384  # Accept the expected Linux-adjusted test buffer readback.


# Fix the fixture.
_CHILD = Path(__file__).with_name("nts_child_handoff_fixture.py").resolve()
_INTERPRETER = Path(sys.executable).resolve()  # Never accept a caller-selected command.


class HandoffError(RuntimeError):
    """Expose a fixed failure reason and explicit cleanup confidence."""

    def __init__(self, reason: str, *, cleanup_complete: bool = True) -> None:
        # Never include arguments, report contents, or private paths.
        super().__init__(reason)
        # An action error is not proof of cleanup.
        self.cleanup_complete = cleanup_complete


def _boot() -> str:
    """Normalize unavailable boot evidence without printing local filesystem details."""
    try:  # Reuse the existing fixed-path, bounded boot query.
        return boot_id()  # Local evidence remains separate from child assertions.
    # Missing or inaccessible evidence blocks the proof.
    except (
        OSError,
        WatchdogError,
    ):
        # No raw path or exception output.
        raise HandoffError("BOOT_ID_UNAVAILABLE") from None


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate fields instead of silently overwriting evidence."""
    result: dict[str, object] = {}  # At most one bounded report is retained.
    for key, value in pairs:  # Parsing is already capped by the packet byte limit.
        if key in result:  # Duplicate fields make the evidence ambiguous.
            # The caller normalizes this fixed failure.
            raise ValueError("duplicate field")
        result[key] = value  # Keep the original value only after the uniqueness check.
    return result  # Exact schema and field types are validated separately.


def _before(deadline: float) -> None:
    """Reject late observations without replacing the original deadline."""
    if time.monotonic() >= deadline:  # Equality already exhausts the startup budget.
        raise HandoffError("STARTUP_EXPIRED")  # Late evidence cannot restore success.


def _preflight(startup_seconds: float, cleanup_seconds: float) -> None:
    """Validate explicit experiment limits and single-owner process prerequisites."""
    # Both waits need explicit finite bounds.
    for value in (
        startup_seconds,
        cleanup_seconds,
    ):
        if (
            type(value) not in (int, float)
            or not 0 < value <= 10
            or not math.isfinite(value)
        ):
            # Reject invalid inputs before any launch.
            raise HandoffError("INVALID_BUDGET")
    # Unsupported systems cannot skip lifetime proof.
    # Require a kernel-backed process reference.
    # Confirm the pidfd still refers to our waitable child.
    # Keep cleanup targeted to that generation.
    if sys.platform != "linux" or not all(
        (
            hasattr(os, "pidfd_open"),
            hasattr(os, "P_PIDFD"),
            hasattr(signal, "pidfd_send_signal"),
        )
    ):
        raise HandoffError("PIDFD_UNAVAILABLE")  # No numeric-PID replacement fallback.
    if (
        signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL
        or threading.active_count() != 1
    ):
        # No competing reaper or launch thread.
        raise HandoffError("UNSAFE_CHILD_OWNERSHIP")


def _inspect(pid: int, expected_command: tuple[str, ...]) -> int:
    """Read a bounded local lifetime witness, never a child-asserted identity."""
    root = Path("/proc") / str(pid)  # PID comes only from our retained Popen handle.
    # Read kernel lifetime data independently.
    with (root / "stat").open("rb") as source:
        raw = source.read(4097)  # Detect overflow before parsing the process record.
    if len(raw) > 4096 or int(raw.split(b" ", 1)[0]) != pid:
        # Oversize or mismatched data is unusable.
        raise HandoffError("IDENTITY_UNAVAILABLE")
    # Parenthesized process names can contain spaces.
    fields = raw.rsplit(b") ", 1)[1].split()
    # Kernel field 22 binds the observed process start reference.
    ticks = int(fields[19])
    # Dead children cannot report readiness.
    if ticks <= 0 or fields[0] in {
        b"Z",
        b"X",
        b"x",
    }:
        raise HandoffError("CHILD_EXITED")  # An existing PID alone is not sufficient.
    # Verify the fixed profile at this observation.
    with (root / "cmdline").open("rb") as source:
        # Bound inspection even for unexpected processes.
        command = source.read(QUERY_BYTES + 1)
    # Reconstruct the exact reviewed argv for comparison with kernel bytes.
    expected = b"\0".join(os.fsencode(arg) for arg in expected_command) + b"\0"
    if len(command) > QUERY_BYTES or command != expected:
        # Matching start ticks do not attest code.
        raise HandoffError("IDENTITY_MISMATCH")
    return ticks  # The caller brackets the fixture report with this local witness.


def _endpoint_witness(
    pid: int, endpoint_fd: int, expected_inode: int, expected_device: int
) -> None:
    """Independently verify the child's endpoint and restored close-on-exec flag."""
    # A reported descriptor is only a locator inside our retained child.
    root = Path("/proc") / str(pid)
    observed = (root / "fd" / str(endpoint_fd)).stat()  # Read kernel object identity.
    if (
        not stat.S_ISSOCK(observed.st_mode)
        or observed.st_ino != expected_inode
        or observed.st_dev != expected_device
    ):
        raise HandoffError(
            "ENDPOINT_MISMATCH"
        )  # Message fields cannot establish identity.
    path = (
        root / "fdinfo" / str(endpoint_fd)
    )  # Read flags for that same bounded locator.
    # Use bounded kernel data, not the child's assertions.
    with path.open("rb") as source:
        # One extra byte distinguishes overflow from valid evidence.
        raw = source.read(4097)
    if len(raw) > 4096:  # Do not parse truncated or unbounded descriptor evidence.
        raise HandoffError("IDENTITY_UNAVAILABLE")  # Missing evidence cannot pass.
    # Parse fixed fdinfo pairs.
    fields = dict(line.split(b":", 1) for line in raw.splitlines())
    if (
        int(fields[b"ino"]) != expected_inode
        or not (int(fields[b"flags"], 8) & os.O_CLOEXEC)
        or not (int(fields[b"flags"], 8) & os.O_NONBLOCK)
    ):
        # Match both the endpoint and ownership flag.
        raise HandoffError("ENDPOINT_MISMATCH")


def _original_absent(pid: int, original_fd: int) -> None:
    """Check the original slot separately; later lifetime checks still have to pass."""
    path = (
        Path("/proc") / str(pid) / "fdinfo" / str(original_fd)
    )  # Inspect only our child.
    try:  # Permission errors cannot be mistaken for absence.
        with path.open("rb") as source:  # An existing entry disproves original release.
            source.read(1)  # No unbounded contents need to be retained to reject it.
    except (
        FileNotFoundError
    ):  # This point observation is bracketed by child lifetime checks.
        # Do not infer uninterrupted absence or universal descriptor confinement.
        return
    raise HandoffError(
        "ORIGINAL_STILL_OPEN"
    )  # Adoption alone cannot establish source release.


def _adoption_context(identity: os.stat_result) -> tuple[str, ...]:
    """Format only the parent's captured identity and explicit synthetic bounds."""
    return (
        str(identity.st_dev),  # Expected identity never originates in a child reply.
        str(identity.st_ino),  # Retain the exact pre-launch socket object reference.
        str(_BUFFER_BYTES),  # No child-selected resource defaults are accepted.
        str(_BUFFER_CEILING),  # Preserve the accepted kernel readback limit.
    )


def _descriptor(endpoint: Endpoint) -> int:
    """Read a proof-owned descriptor for launch or readiness inspection."""
    return (
        endpoint._owned().fileno()
    )  # Synthetic internal access, not a general handoff API.


def _closure_observed(endpoint: Endpoint) -> bool:
    """Check kernel closure and transport rejection after verified child exit."""
    poller = select.poll()  # Do not wait or consume data outside the bounded transport.
    mask = (
        select.POLLHUP | select.POLLRDHUP
    )  # Require an independent current closure flag.
    poller.register(
        _descriptor(endpoint), mask
    )  # Keep the exact owned endpoint association.
    closed = any(
        flags & mask for _, flags in poller.poll(0)
    )  # Zero-time state observation.
    try:  # Exercise the actual bounded receive rather than a permissive raw fallback.
        # Unexpected data after verified fixture exit invalidates the proof.
        endpoint.receive()
    except (
        WouldBlock
    ):  # A retained parent-side copy should keep the empty connection open.
        if (
            closed
        ):  # Contradictory point observations cannot establish the expected case.
            # Fail instead of guessing.
            raise HandoffError("CLOSURE_NOT_OBSERVED") from None
        return (
            False  # This says nothing about child lifetime without the retained pidfd.
        )
    except (
        TransportError
    ) as error:  # Empty/closed input is not a valid transport packet.
        if (
            not error.cleanup_complete
        ):  # Do not hide a close failure behind expected EOF.
            raise HandoffError("CLEANUP_UNVERIFIED", cleanup_complete=False) from None
        if (
            str(error) != "EMPTY_OR_CLOSED" or not closed
        ):  # Empty packet alone is ambiguous.
            raise HandoffError(
                "CLOSURE_NOT_OBSERVED"
            ) from None  # Keep evidence meanings distinct.
        # Verified child exit and the kernel flag support this limited observation.
        return True
    raise HandoffError(
        "UNEXPECTED_PACKET_AFTER_EXIT"
    )  # Never discard unexpected trailing data.


def _child_exited(pidfd: int) -> bool:
    """Check the retained generation without reaping or trusting socket closure."""
    # Observe our waitable generation without reaping or blocking on it.
    state = os.waitid(os.P_PIDFD, pidfd, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if state is not None and state.si_code == os.CLD_EXITED and state.si_status == 6:
        raise HandoffError(
            "FIXTURE_RELEASE_UNCONFIRMED"
        )  # Cleanup success cannot revive the attempt.
    return state is not None  # ECHILD propagates: lost wait ownership is not healthy.


def _wait(pidfd: int, endpoint: Endpoint | None, deadline: float) -> None:
    """Wait for one owned event within the original budget, never indefinitely."""
    poller = select.poll()  # Readiness waiting avoids an output-polling busy loop.
    # Observe independent child exit.
    poller.register(pidfd, select.POLLIN | select.POLLHUP)
    if endpoint is not None:  # Startup also needs a bounded fixture packet.
        poller.register(_descriptor(endpoint), select.POLLIN | select.POLLHUP)
    _before(deadline)  # Do not issue a new wait after expiry.
    remaining = deadline - time.monotonic()  # Use only the original remaining time.
    # Round once; late return still fails.
    poller.poll(max(1, math.ceil(remaining * 1000)))
    _before(deadline)  # Scheduling delay cannot turn expired evidence into success.


def _report(endpoint: Endpoint, pidfd: int, deadline: float) -> dict[str, object]:
    """Receive one small fixture packet; exit and invalid input stop startup."""
    while True:  # Every iteration waits for readiness under the unchanged deadline.
        # Observe child exit even if the channel stays open.
        _wait(pidfd, endpoint, deadline)
        # Exit wins over a queued report from the dead fixture.
        if _child_exited(pidfd):
            # A late queued packet cannot establish readiness.
            raise HandoffError("CHILD_EXITED")
        try:  # Read only after readiness, but retain nonblocking race handling.
            raw = endpoint.receive()  # Reuse all existing packet and ancillary checks.
        except WouldBlock:  # Readiness may race without granting a new budget.
            continue  # The next iteration waits instead of spinning on recv.
        except (
            TransportError
        ) as error:  # Invalid packets cannot become fixture evidence.
            # Socket close can precede publication of the child's final exit status.
            if str(error) == "EMPTY_OR_CLOSED" and error.cleanup_complete:
                # This is failed-start reconciliation, never a readiness or retry path.
                while not _child_exited(pidfd):
                    _wait(pidfd, None, deadline)  # Preserve the original startup bound.
                raise HandoffError("CHILD_EXITED") from None  # No report was accepted.
            raise HandoffError(
                "INVALID_FIXTURE_REPORT", cleanup_complete=error.cleanup_complete
            ) from None
        # Parsing is bounded by the existing packet limit.
        report = json.loads(raw, object_pairs_hook=_unique)
        # Enforce the exact fixture schema.
        if type(report) is not dict or set(report) != {
            "version",
            "pid",
            "start_ticks",
            "excluded_closed",
            "non_inheritable",
            # A bounded untrusted locator, never a child-selected process ID.
            "owned_fd",
            # The kernel absence check remains independently required.
            "original_closed",
        }:
            # Extra fields cannot expand authority.
            raise HandoffError("INVALID_FIXTURE_REPORT")
        return report  # The parent still independently compares identity assertions.


def _health_exchange(
    endpoint: Endpoint, pidfd: int, lifetime: Lifetime, deadline: float
) -> None:
    """Accept exactly one timely synthetic reply; this is not watchdog readiness."""
    session = Session(  # Keep expected identity independent of the received packet.
        lifetime,  # Parent-owned launch evidence defines the only accepted lifetime.
        response_timeout_ns=_HEALTH_TIMEOUT_NS,  # Explicit synthetic response budget.
        max_generation_age_ns=_HEALTH_TIMEOUT_NS,  # Not an NTP measurement-age bound.
        token_factory=lambda: secrets.token_hex(32),  # Fresh 256-bit parent challenge.
    )
    sent = time.monotonic_ns()  # Start response timing before the single send attempt.
    packet = session.challenge(sent)  # Only this outstanding challenge may be answered.
    endpoint.send(packet)  # Backpressure fails this attempt; never resend or rearm.
    response_deadline = min(  # Health waiting cannot extend the original lifetime.
        deadline, (sent + _HEALTH_TIMEOUT_NS) / 1_000_000_000
    )
    while True:  # Every empty receive returns to a bounded readiness wait.
        session.check(time.monotonic_ns())  # Reject expired or backwards timing first.
        try:  # Distinguish the health budget from the initial report wait.
            _wait(pidfd, endpoint, response_deadline)  # Observe exit and socket input.
        except HandoffError as error:  # Preserve non-time failures unchanged.
            if str(error) == "STARTUP_EXPIRED":  # This narrower wait expired.
                raise HandoffError("HEALTH_RESPONSE_EXPIRED") from None
            raise  # Do not reinterpret other evidence failures.
        if _child_exited(pidfd):  # A queued reply cannot revive an exited worker.
            raise HandoffError("HEALTH_CHILD_EXITED")
        try:  # Readiness does not guarantee a subsequent nonblocking read succeeds.
            response = endpoint.receive()  # Enforce whole-packet and ancillary limits.
        except WouldBlock:  # Preserve the same challenge and deadline on a read race.
            continue  # Wait again; never busy-poll or regenerate a challenge token.
        session.accept(
            response, time.monotonic_ns()
        )  # Validate before reporting success.
        return  # A second response is never accepted as a new exchange.


def _cleanup(
    process: subprocess.Popen[bytes], pidfd: int | None, seconds: float
) -> bool:
    """Stop only the retained direct child, then independently wait for its exit."""
    # Cleanup has its own explicit finite allowance.
    deadline = time.monotonic() + seconds
    # Retain signal and wait failures instead of hiding one with the other.
    complete = True
    try:  # Even an already-exited child must be reaped through the retained owner.
        if pidfd is not None:  # Prefer the stable generation-specific kernel reference.
            # Never kill by process name.
            signal.pidfd_send_signal(pidfd, signal.SIGKILL)
        else:  # A failed pidfd setup still leaves our unreaped direct-child handle.
            state = os.waitid(
                os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT
            )
            # Signal only while direct-child wait ownership still exists.
            if state is None:
                process.kill()  # No competing waiter is allowed in this fixed proof.
    # Exit can race with cleanup without implying successful reaping.
    except ProcessLookupError:
        pass  # The independent bounded wait below must still establish the result.
    # Denied signaling or lost ownership is explicit cleanup uncertainty.
    except OSError:
        # Do not claim that a signal failure proves presence or absence.
        complete = False
    try:  # Exit verification is separate from the signal acknowledgement.
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
    # Unknown exit/reaping prevents a PASS.
    except (
        OSError,
        subprocess.TimeoutExpired,
    ):
        complete = False  # The fixture's own deadline is not independent verification.
    # Late cleanup is not timely success.
    return complete and time.monotonic() < deadline


def _emit(record: dict[str, object]) -> None:
    """Print bounded private proof receipts; never commit operational output."""
    # Deterministic field order aids review.
    encoded = json.dumps(record, sort_keys=True)
    # Do not truncate required evidence.
    if len(encoded.encode("ascii")) > PACKET_BYTES:
        raise HandoffError("RECEIPT_LIMIT")  # Oversized evidence invalidates the proof.
    # Persist this output privately with the acceptance receipt.
    print(encoded, flush=True)


def run_case(
    *,
    duplicate: bool,
    startup_seconds: float,
    cleanup_seconds: float,
    _mode: str = "normal",
) -> dict[str, object]:
    """Run one fixed synthetic fixture; private modes support focused failure tests."""
    # Reject unsafe setup before allocating FDs.
    _preflight(startup_seconds, cleanup_seconds)
    if (
        type(duplicate) is not bool
        or type(_mode) is not str
        or _mode
        not in {
            "normal",
            "early_exit",
            "stall",
            "source_close_failure",
            "retain_original",
            "health_close",  # A closed peer cannot establish a successful exchange.
            "health_lifetime",  # A reply with another lifetime cannot pass.
            "health_generation",  # A future generation timestamp must fail.
            "health_malformed",  # Private end-to-end protocol rejection fixture.
            "health_mismatch",  # A different challenge token must fail.
            "health_silent",  # Missing replies must exhaust only the existing bound.
            "health_duplicate",  # Extra replies must not survive as accepted evidence.
        }
    ):
        # No arbitrary child entrypoint or command is accepted.
        raise HandoffError("INVALID_CASE")
    deadline = time.monotonic() + startup_seconds  # Keep one original startup deadline.
    collection_id = str(
        uuid.uuid4()
    )  # Separate this attempt from all earlier launches.
    initial_boot = _boot()  # Bind local observations to the same host boot.
    # Track each resource as soon as ownership is acquired.
    parent: Endpoint | None = None
    # A deliberate retained copy exists only in one case.
    child: Endpoint | None = None
    sentinel: int | None = None  # An unrelated inheritable FD challenges close_fds.
    # Retain the exact direct-child owner.
    process: subprocess.Popen[bytes] | None = None
    pidfd: int | None = None  # Never obtain this reference from child-supplied data.
    cleanup_complete = True  # Any failed resource release prevents overall success.
    launch_attempted = (
        False  # A missing launch handle cannot prove that no child exists.
    )
    try:  # Every exit path attempts cleanup of all resources already owned.
        # Reuse bounded pair creation instead of permissive raw socket setup.
        parent, child = create_pair(
            buffer_bytes=_BUFFER_BYTES, effective_buffer_limit=_BUFFER_CEILING
        )
        # Create an unrelated marker.
        sentinel = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
        # Deliberately challenge the child's FD allowlist.
        os.set_inheritable(sentinel, True)
        child_fd = _descriptor(
            child
        )  # Retain the exact handed-off descriptor reference.
        # Observe its identity before closing our copy.
        child_identity = os.fstat(
            child_fd
        )  # Capture before launch, independently of the reply.
        child_inode = child_identity.st_ino
        # The exact argv is also independently checked through /proc.
        # Ignore Python environment/path injection.
        # Trusted fixture references.
        # Fixed instructions and unchanged budget.
        command = (
            str(_INTERPRETER),  # Use the fixed validation interpreter.
            "-I",  # Ignore Python environment and user import-path injection.
            "-B",  # Keep bytecode caches out of the checked archive.
            str(_CHILD),  # Run only the adjacent reviewed harmless fixture.
            str(child_fd),  # Hand over this endpoint, not an external FD.
            str(
                _descriptor(parent)
            ),  # Tell the child which opposite-end FD must be absent.
            str(sentinel),  # Check that an unrelated inheritable FD was excluded.
            repr(deadline),  # Preserve the exact original startup boundary.
            _mode,  # Select only the reviewed harmless failure-test modes.
            str(PACKET_BYTES),  # Check the fixture's bound against the shared ceiling.
            # The fixed fixture context is not a general bootstrap-message API.
            *_adoption_context(child_identity),
            initial_boot,  # Fixed trusted context, not a child-selected lifetime.
            collection_id,  # Bind this launch to a fresh synthetic collection.
        )
        _before(deadline)  # Time spent preparing resources counts against startup.
        # Launch only this reviewed harmless child.
        _emit(
            {
                "event": "launch_attempt",
                "boot_id": initial_boot,
                "duplicate_case": duplicate,
                "deadline_monotonic": deadline,
            }
        )
        launch_attempted = True  # Treat a missing launch acknowledgement as unresolved.
        process = subprocess.Popen(
            command,  # No caller-supplied command can enter this fixture launch.
            shell=False,  # Do not reinterpret arguments as shell code.
            close_fds=True,  # Close unrelated inherited resources before exec.
            pass_fds=(child_fd,),  # Allow only the designated endpoint.
            stdin=subprocess.DEVNULL,  # Use only the socket for instructions.
            stdout=subprocess.DEVNULL,  # No uncontrolled diagnostic output is retained.
            stderr=subprocess.DEVNULL,  # Use exit status for child failures.
            cwd=str(
                _CHILD.parent
            ),  # Avoid caller-selected module/working-directory context.
            env={"LC_ALL": "C"},  # Do not inherit credentials or executable overrides.
            start_new_session=True,  # Isolate the fixture from terminal signals.
        )
        _emit(
            {
                "event": "owned_child",
                "pid": process.pid,
                "boot_id": initial_boot,
                "duplicate_case": duplicate,
                "deadline_monotonic": deadline,
            }
        )
        # Bind the direct child before any poll/wait reaps it.
        pidfd = os.pidfd_open(process.pid, 0)
        # Confirm wait ownership and detect an already-dead fixture.
        if _child_exited(pidfd):
            raise HandoffError("CHILD_EXITED")  # No report can revive an exited child.
        # The normal path immediately releases the parent's spare child end.
        if not duplicate:
            # Without this close, channel EOF can remain hidden after child exit.
            child.close()
            child = None  # Do not close or reuse the released descriptor number again.
        # A blocking launch or late pidfd acquisition cannot pass startup.
        _before(deadline)
        # Observe identity independently before the report.
        before = _inspect(process.pid, command)
        # Wait for bounded inheritance evidence.
        report = _report(parent, pidfd, deadline)
        owned_fd = report[
            "owned_fd"
        ]  # Treat this as untrusted input, not identity proof.
        if (
            type(owned_fd) is not int
            or not 2 < owned_fd <= (1 << 31) - 1
            or owned_fd == child_fd
        ):
            raise HandoffError(
                "INVALID_OWNED_REFERENCE"
            )  # Never build paths from arbitrary data.
        # Verify the duplicate's kernel identity and flags against trusted launch
        # evidence.
        _endpoint_witness(process.pid, owned_fd, child_inode, child_identity.st_dev)
        _original_absent(
            process.pid, child_fd
        )  # Adoption itself left this source copy open.
        # Bracket the report with a second local witness.
        after = _inspect(process.pid, command)
        expected = {
            # Require the checked-copy fixture report, not the old raw format.
            "version": 2,
            "pid": process.pid,
            "start_ticks": before,
            "excluded_closed": True,
            "non_inheritable": True,
            # This assertion alone did not satisfy the kernel gate.
            "original_closed": True,
        }
        # Matching values must also have exact expected types.
        if (
            before != after
            or any(
                type(report[key]) is not type(value) or report[key] != value
                for key, value in expected.items()
            )
            or _boot() != initial_boot
            or _child_exited(pidfd)
        ):
            # Neither report fields nor PID alone suffice.
            raise HandoffError("IDENTITY_MISMATCH")
        # All observations must remain inside the original startup allowance.
        _before(deadline)
        lifetime = Lifetime(  # These container-shaped IDs are synthetic placeholders.
            boot_id=initial_boot,  # Independently bracketed local boot identity.
            collection_id=collection_id,  # Created by the parent before launch.
            container_id="0" * 64,  # No real container is represented by this fixture.
            owner="0" * 32,  # No Docker ownership claim is made by this placeholder.
            watchdog_pid=process.pid,  # Retained direct-child handle, not report input.
            watchdog_start_ticks=before,  # Independently inspected kernel lifetime.
            deadline_ns=int(deadline * 1_000_000_000),  # Original monotonic boundary.
        )
        _health_exchange(
            parent, pidfd, lifetime, deadline
        )  # One bounded exchange only.
        if (  # Recheck point identity after the reply without claiming continuity.
            _inspect(process.pid, command) != before
            or _boot() != initial_boot
            or _child_exited(pidfd)
        ):
            raise HandoffError(
                "IDENTITY_MISMATCH"
            )  # A reply cannot bypass identity gates.
        parent.send(
            b"exit"
        )  # Reuse all-or-error bounded send; no partial-send fallback.
        # Observe exit independently before reaping the child.
        while not _child_exited(pidfd):
            # Block for the retained generation, never channel EOF.
            _wait(pidfd, None, deadline)
        # Observe our waitable generation without reaping or blocking on it.
        state = os.waitid(os.P_PIDFD, pidfd, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        if state is None or state.si_code != os.CLD_EXITED or state.si_status != 0:
            # Exit alone is insufficient for normal proof success.
            raise HandoffError("FIXTURE_FAILED")
        # Only inspect closure after the retained pidfd independently established child
        # exit.
        if _closure_observed(parent) is duplicate:
            raise HandoffError(
                "DUPLICATE_PROOF_FAILED"
            )  # A retained copy must actually hide closure.
        if (
            child is not None
        ):  # Release the deliberately retained parent copy after its proof.
            # Do not confuse this copy with the child's already-released original.
            child.close()
            child = None  # Do not retry a released descriptor number.
            if not _closure_observed(
                parent
            ):  # The final spare-copy release must expose closure.
                raise HandoffError("CLOSURE_NOT_OBSERVED")
        _before(deadline)  # Receipt success also requires timely completion.
        # Report only the limited observation; never actual watchdog readiness.
        return {
            "case": "duplicate_retained" if duplicate else "normal_handoff",
            "pid": process.pid,
            "start_ticks": before,
            "boot_id": initial_boot,
            "excluded_test_fds_reported_closed": True,  # Validated fixed-child report.
            "endpoint_cloexec_independently_observed": True,
            # Both communication paths used bounded transport.
            "checked_adoption_used": True,
            "synthetic_health_exchange_accepted": True,  # No actual watchdog readiness.
            # A separate kernel point observation.
            "child_original_absence_observed": True,
            # This locator was validated against expected identity.  # Kernel fdinfo
            # witness.
            "child_owned_fd": owned_fd,
            "child_exit_independently_observed": True,
            # Returned only if the finally block confirms cleanup.
            "cleanup_complete": True,
            "closure_hidden_by_duplicate": duplicate,
            "measurement_age": "unknown",
            "approval_eligibility": "blocked",
        }
    except HealthError:  # Protocol errors stop the attempt and retain normal cleanup.
        raise HandoffError("HEALTH_EXCHANGE_REJECTED") from None
    except TransportError as error:  # Keep owned-copy cleanup uncertainty explicit.
        raise HandoffError(
            "TRANSPORT_FAILED", cleanup_complete=error.cleanup_complete
        ) from None
    except WouldBlock:  # A full fixed exit-request queue fails this synthetic attempt.
        raise HandoffError(
            "TRANSPORT_WOULD_BLOCK"
        ) from None  # No automatic resend or new budget.
    # Normalize unavailable or malformed evidence.
    except (
        OSError,
        ValueError,
        IndexError,
        KeyError,
        RecursionError,
    ):
        # Do not emit raw private details.
        raise HandoffError("HANDOFF_UNAVAILABLE") from None
    finally:  # Cleanup failure overrides success and preserves explicit uncertainty.
        # Never signal anything without the retained direct-child owner.
        try:  # Descriptor cleanup must still run if child cleanup is interrupted.
            cleanup_complete = (
                False  # Require a fresh acknowledgement from child cleanup.
            )
            if process is not None:
                cleanup_complete = _cleanup(process, pidfd, cleanup_seconds)
            # Attempt both owned endpoint closes despite prior failure.
            if process is None:  # A failed launch acquired no child handle.
                cleanup_complete = (
                    # No launch acknowledgement leaves child state unknown.
                    not launch_attempted
                )
        finally:  # Always release all separately owned descriptor resources.
            for endpoint in (
                parent,
                child,
            ):
                # A released endpoint must not be closed by number again.
                if endpoint is not None:
                    try:  # Close the actual owned socket object once.
                        endpoint.close()  # Only this proof's endpoint is affected.
                    except (
                        OSError,
                        TransportError,
                    ):  # A close error cannot be treated as confirmed release.
                        # Do not retry a descriptor number that may be reused.
                        cleanup_complete = False
            # Close the unrelated marker and retained lifetime handle.
            for owned_fd in (
                sentinel,
                pidfd,
            ):
                # Only acquired descriptors belong to this cleanup path.
                if owned_fd is not None:
                    try:  # Continue cleanup even if another resource failed.
                        os.close(
                            owned_fd
                        )  # Release only this proof's retained descriptor.
                    # Preserve uncertain release instead of a pristine PASS.
                    except OSError:
                        # Never retry an uncertain close by numeric FD.
                        cleanup_complete = False
            # Even useful observations cannot hide unverified cleanup.
            if not cleanup_complete:
                raise HandoffError("CLEANUP_UNVERIFIED", cleanup_complete=False)


def main() -> None:
    """Run exactly two synthetic cases only after explicit CLI opt-in."""
    # Require explicit finite experiment budgets.
    if len(sys.argv) != 4 or sys.argv[1] != "--accept":
        # No implicit launch.
        raise SystemExit("Use --accept STARTUP_SECONDS CLEANUP_SECONDS")
    # Validate these limits again before each launch.
    try:  # Malformed CLI budgets must not echo private argument values.
        startup, cleanup = map(
            float, sys.argv[2:]
        )  # Parse only the fixed two-budget schema.
    except (
        ValueError,
        OverflowError,
    ):  # No invalid duration can authorize a child launch.
        raise HandoffError("INVALID_BUDGET") from None  # Keep the public reason fixed.
    # The proof is normal handoff plus a deliberate spare-copy case.
    for duplicate in (
        False,
        True,
    ):
        result = run_case(
            duplicate=duplicate, startup_seconds=startup, cleanup_seconds=cleanup
        )
        # Keep actual process references in the private acceptance output.
        _emit(result)
    # Emit only after both cases and cleanup pass.
    print("NTS_CHILD_HANDOFF_ACCEPTANCE=PASS")
    # Never confuse this fixture with a live time exchange.
    print("LIVE_NTS_COLLECTION=NOT_RUN")
    # Synthetic inheritance never authorizes approval.
    print("APPROVAL_ELIGIBILITY=BLOCKED")


if __name__ == "__main__":  # Importing the harness never launches a child.
    try:  # Keep CLI failure output fixed and bounded.
        main()  # The explicit opt-in path is the only command entrypoint.
    # Preserve cleanup uncertainty without printing a traceback.
    except HandoffError as error:
        _emit({"failure": str(error), "cleanup_complete": error.cleanup_complete})
        # A failed proof must fail the calling acceptance gate.
        raise SystemExit(1) from None
