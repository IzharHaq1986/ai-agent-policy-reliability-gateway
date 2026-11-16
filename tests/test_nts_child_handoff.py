"""Synthetic direct-child proof tests; no real watchdog, Docker, or time exchange."""

import importlib  # Import only the test-support harness under explicit test control.
import os  # Check owned child reaping and descriptor release independently.
import signal  # Exercise unsafe-reaper rejection without launching a child.
import socket  # Feed real local packets into bounded fixture-report validation.
from pathlib import Path  # Locate the reviewed support files deterministically.

import pytest  # Express expected failures and restore injected faults after each test.


@pytest.fixture
def module(monkeypatch):
    """Load the harness without invoking its opt-in CLI."""
    # Find reviewed helpers.
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "support"))
    # Imports do not launch.
    return importlib.import_module("run_nts_child_handoff_acceptance")


@pytest.fixture
def launches(module, monkeypatch):
    """Retain every real child handle so tests can independently check cleanup."""
    # Keep the real launcher behind a recording wrapper.
    original = module.subprocess.Popen
    recorded = []  # This test creates only a small fixed number of children.

    # Record actual launch settings without changing behavior.
    def launch(*args, **kwargs):
        # Still launch only the fixed harmless fixture.
        child = original(*args, **kwargs)
        # Keep the retained owner and exact options.
        recorded.append((child, args, kwargs))
        return child  # Give the harness the original direct-child handle.

    # Observe the actual execution path.
    monkeypatch.setattr(module.subprocess, "Popen", launch)
    return recorded  # Failure tests inspect these handles after the harness returns.


def assert_reaped(launches):
    """Do not infer exit from socket closure or a successful signal call."""
    assert launches  # The tested failure must have actually reached child launch.
    for child, _, _ in launches:  # Inspect only children created by this test.
        # The retained owner completed its bounded wait.
        assert child.returncode is not None
        # Reaping removes this parent's waitable child.
        with pytest.raises(ChildProcessError):
            # No broad process query.
            os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG)


@pytest.mark.parametrize("duplicate", [False, True])
def test_real_handoff_and_independent_exit(module, launches, duplicate):
    # Take an independent descriptor baseline.
    before = len(os.listdir("/proc/self/fd"))
    result = module.run_case(duplicate=duplicate, startup_seconds=5, cleanup_seconds=2)
    # Require the pidfd witness.
    assert result["child_exit_independently_observed"] is True
    # Compare the two actual socket cases.
    assert result["closure_hidden_by_duplicate"] is duplicate
    # This proof is not time freshness evidence.
    assert result["measurement_age"] == "unknown"
    # No synthetic observation grants authority.
    assert result["approval_eligibility"] == "blocked"
    # Confirm exit and reaping independently of the result dictionary.
    assert_reaped(launches)
    # No endpoint, marker, or pidfd leaked.
    assert len(os.listdir("/proc/self/fd")) == before
    # Inspect the actual Popen call, not a proposed configuration.
    _, args, options = launches[0]
    command = args[0]  # The interpreter and entrypoint must be fixed absolute paths.
    assert Path(command[0]).is_absolute() and Path(command[3]).is_absolute()
    # Exclude Python environment/path injection and bytecode writes.
    assert command[1:3] == (
        "-I",
        "-B",
    )
    assert options["shell"] is False and options["close_fds"] is True
    # Only the designated child endpoint crosses exec.
    assert options["pass_fds"] == (int(command[4]),)
    # Do not inherit credentials or Python overrides.
    assert options["env"] == {"LC_ALL": "C"}
    assert "preexec_fn" not in options  # Avoid unsafe threaded pre-exec callbacks.


# Zero startup allowance cannot authorize a launch.
# Cleanup also needs a positive bound.
# NaN cannot establish a deadline.
# Infinite cleanup would be an unbounded wait.
# Booleans are not experiment duration settings.
# Exceeding the synthetic cap is outside this proof.
# Huge integer input must fail before float conversion.
@pytest.mark.parametrize(
    "startup,cleanup",
    [
        (0, 1),
        (1, -1),
        (float("nan"), 1),
        (1, float("inf")),
        (True, 1),
        (11, 1),
        (10**400, 1),
    ],
)
def test_bad_budgets_never_launch(module, launches, startup, cleanup):
    with pytest.raises(module.HandoffError, match="INVALID_BUDGET"):
        module.run_case(
            duplicate=False, startup_seconds=startup, cleanup_seconds=cleanup
        )
    # Validation must fail before any resource-owning child is created.
    assert not launches


def test_unsafe_reaper_never_launches(module, launches, monkeypatch):
    # Simulate auto-reaping.
    monkeypatch.setattr(module.signal, "getsignal", lambda _: signal.SIG_IGN)
    with pytest.raises(module.HandoffError, match="UNSAFE_CHILD_OWNERSHIP"):
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    assert not launches  # Reused numeric PIDs must never become a cleanup target.


def test_competing_thread_never_launches(module, launches, monkeypatch):
    # Simulate another owner.
    monkeypatch.setattr(module.threading, "active_count", lambda: 2)
    with pytest.raises(module.HandoffError, match="UNSAFE_CHILD_OWNERSHIP"):
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    # This isolated harness does not support competing launch/reap threads.
    assert not launches


def test_unsupported_pidfd_never_launches(module, launches, monkeypatch):
    # No fallback may pretend to provide pidfd proof.
    monkeypatch.delattr(module.os, "pidfd_open")
    with pytest.raises(module.HandoffError, match="PIDFD_UNAVAILABLE"):
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    assert not launches  # Reject unavailable prerequisites before creating the child.


def test_pidfd_setup_failure_cleans_owned_child(module, launches, monkeypatch):
    def denied(*args):  # Simulate a permission failure after real launch.
        # Raw details must not enter public errors.
        raise PermissionError("private kernel detail")

    # Exercise the retained-handle cleanup path.
    monkeypatch.setattr(module.os, "pidfd_open", denied)
    with pytest.raises(module.HandoffError, match="HANDOFF_UNAVAILABLE") as error:
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    # Success means cleanup was separately confirmed.
    assert error.value.cleanup_complete
    # No pidfd acknowledgement does not mean no child was created.
    assert_reaped(launches)


def test_launch_failure_releases_descriptors(module, monkeypatch):
    # Inspect resource state before the injected failure.
    before = len(os.listdir("/proc/self/fd"))

    # Simulate failed creation without secretly launching a child.
    def denied(*args, **kwargs):
        raise OSError("private launch details")  # Only fixed failure codes may escape.

    # Fail exactly at the launch boundary.
    monkeypatch.setattr(module.subprocess, "Popen", denied)
    with pytest.raises(module.HandoffError, match="CLEANUP_UNVERIFIED") as error:
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    # Do not echo private paths or arguments.
    assert not error.value.cleanup_complete
    # Both endpoints and the marker were closed.
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("mode", ["early_exit", "stall"])
def test_child_exit_or_stall_cannot_pass(module, launches, mode):
    # Neither missing nor late evidence is accepted.
    with pytest.raises(module.HandoffError):
        module.run_case(
            duplicate=False, startup_seconds=0.2, cleanup_seconds=2, _mode=mode
        )
    # A failed observation must still stop and reap the exact child.
    assert_reaped(launches)


def test_unavailable_identity_cleans_child(module, launches, monkeypatch):
    def denied(*args):  # Simulate inaccessible trusted local evidence.
        # No fallback to child assertions is allowed.
        raise PermissionError("private proc detail")

    # Fail the independent identity observation.
    monkeypatch.setattr(module, "_inspect", denied)
    with pytest.raises(module.HandoffError, match="HANDOFF_UNAVAILABLE"):
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    # Retained ownership still permits bounded exact-child cleanup.
    assert_reaped(launches)


def test_late_startup_never_recovers_success(module, launches, monkeypatch):
    original = module._before  # Retain the real check for the pre-launch observation.
    calls = 0  # Count the two fixed startup checkpoints deterministically.

    # Inject expiry after a real child and pidfd have been acquired.
    def expire(
        deadline,
    ):
        nonlocal calls  # Keep the timing fault local to this test.
        calls += 1  # The first check permits launch; the next must fail.
        if calls >= 2:  # A late acknowledgement cannot create a new deadline.
            raise module.HandoffError("STARTUP_EXPIRED")
        original(deadline)  # Still enforce the real original bound before launching.

    # Avoid a scheduling-dependent sleep test.
    monkeypatch.setattr(module, "_before", expire)
    with pytest.raises(module.HandoffError, match="STARTUP_EXPIRED"):
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    # Expiry must trigger cleanup rather than a replacement launch.
    assert_reaped(launches)
    assert len(launches) == 1  # No automatic second child is allowed.


def test_cleanup_confirmation_failure_overrides_success(module, launches, monkeypatch):
    # Keep actual cleanup so this fault injection cannot leak a child.
    original = module._cleanup

    # Model missing confidence after resources really were cleaned.
    def lost_confirmation(
        *args,
    ):
        # Independently require the actual child cleanup to complete.
        assert original(*args)
        return False  # The caller still cannot claim an acknowledged clean run.

    # Exercise failure precedence.
    monkeypatch.setattr(module, "_cleanup", lost_confirmation)
    with pytest.raises(module.HandoffError, match="CLEANUP_UNVERIFIED") as error:
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    # The error must preserve uncertainty explicitly.
    assert not error.value.cleanup_complete
    # This fixture's real resources were cleaned despite simulated ack loss.
    assert_reaped(launches)


def test_injected_extra_inheritance_is_rejected(module, launches, monkeypatch):
    # Retain the test's wrapper and actual child handles.
    recording = module.subprocess.Popen

    # Deliberately break the allowed inheritance for this negative case.
    def leak(command, **kwargs):
        # Also pass the unrelated marker.
        kwargs["pass_fds"] = (
            *kwargs["pass_fds"],
            int(command[6]),
        )
        # The child must detect the real surviving descriptor.
        return recording(command, **kwargs)

    # Inject the fault at the actual boundary.
    monkeypatch.setattr(module.subprocess, "Popen", leak)
    # A compromised allowlist cannot produce a passing proof.
    with pytest.raises(module.HandoffError):
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    # A real failing child must still be independently cleaned and reaped.
    assert_reaped(launches)


# A message assertion cannot replace the retained child handle.
# A mismatched process generation invalidates the report.
# Restoring endpoint ownership is part of the fixture contract.
# A surviving unrelated descriptor is unacceptable.
# Boolean/integer equivalence must not bypass exact schema types.
@pytest.mark.parametrize(
    "field,value",
    [
        (
            "pid",
            999999,
        ),
        ("start_ticks", 1),
        (
            "non_inheritable",
            False,
        ),
        ("excluded_closed", False),
        (
            "version",
            True,
        ),
    ],
)
def test_mismatched_report_never_passes(module, launches, monkeypatch, field, value):
    # Receive actual inheritance evidence before corrupting one field.
    original = module._report

    def changed(*args):  # Model a malformed or mismatched child report.
        report = original(*args)  # Preserve the real bounded receive path.
        report[field] = value  # Change exactly the condition under test.
        return report  # Trusted local observations must reject the altered assertion.

    # Leave all other launch behavior unchanged.
    monkeypatch.setattr(module, "_report", changed)
    with pytest.raises(module.HandoffError, match="IDENTITY_MISMATCH"):
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    assert_reaped(launches)  # Invalid evidence cannot skip cleanup.


# Missing fields cannot establish inheritance.
# A scalar cannot be treated as a report.
# Duplicate keys cannot overwrite evidence silently.
# A truncated packet must be rejected before JSON parsing.
@pytest.mark.parametrize(
    "packet",
    [
        b"{}",
        b"null",
        b'{"version":1,"version":1}',
        b"x" * 2049,
    ],
)
def test_malformed_report_is_bounded(module, monkeypatch, packet):
    # Use real records.
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    # Data is already queued in this unit test.
    monkeypatch.setattr(module, "_wait", lambda *args: None)
    # Isolate packet parsing only.
    monkeypatch.setattr(module, "_child_exited", lambda _: False)
    try:  # Close both test endpoints even when the parser rejects input.
        left.send(packet)  # Feed the exact malformed or oversized record.
        with pytest.raises((module.HandoffError, ValueError)):
            # No real process identity is claimed by this unit fixture.
            module._report(right, -1, 0)
    finally:  # These local parser tests do not own a child process.
        left.close()  # Release the sender created by this test.
        right.close()  # Release the receiver created by this test.


def test_missing_boot_evidence_never_launches(module, launches, monkeypatch):
    """An unreadable host reference cannot be replaced by a child-supplied claim."""

    def denied():  # Fail the trusted fixed-path query before any resource creation.
        raise PermissionError("private boot detail")  # This must never be echoed.

    monkeypatch.setattr(
        module, "boot_id", denied
    )  # Preserve all other preflight behavior.
    with pytest.raises(module.HandoffError, match="BOOT_ID_UNAVAILABLE"):
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    assert not launches  # No child can start without the recorded host boundary.


def test_changed_boot_invalidates_existing_attempt(module, launches, monkeypatch):
    """A new host boundary must not be silently accepted as the original attempt."""
    observations = iter(
        ["1" * 36, "2" * 36]
    )  # Use clearly synthetic mismatched witnesses.
    monkeypatch.setattr(
        module, "_boot", lambda: next(observations)
    )  # Change one trusted reference.
    with pytest.raises(module.HandoffError, match="IDENTITY_MISMATCH"):
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    assert_reaped(
        launches
    )  # Even failed identity evidence must retain exact child ownership.


def test_unavailable_wait_ownership_cannot_pass(module, launches, monkeypatch):
    """A retained descriptor alone cannot replace the direct-child wait association."""

    def lost(_):  # Simulate a competing reaper invalidating lifetime observations.
        raise ChildProcessError(
            "private wait detail"
        )  # Do not treat absence as healthy progress.

    monkeypatch.setattr(
        module, "_child_exited", lost
    )  # Fail the independent exit observer.
    with pytest.raises(module.HandoffError, match="HANDOFF_UNAVAILABLE"):
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    assert_reaped(
        launches
    )  # Actual cleanup still uses the real owned child in this injected case.


def test_kernel_endpoint_witness_is_required(module, launches, monkeypatch):
    """Do not rely exclusively on the fixture's assertion about close-on-exec."""

    def mismatch(
        *args,
    ):  # Simulate an independently observed wrong endpoint flag or inode.
        raise module.HandoffError(
            "ENDPOINT_MISMATCH"
        )  # Preserve the real witness failure reason.

    monkeypatch.setattr(
        module, "_endpoint_witness", mismatch
    )  # Fail after a real report arrives.
    with pytest.raises(module.HandoffError, match="ENDPOINT_MISMATCH"):
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    assert_reaped(
        launches
    )  # A valid-looking report cannot bypass failed independent evidence.


def test_cli_requires_opt_in_and_valid_budgets(module, launches, monkeypatch):
    """Import and malformed CLI use must never implicitly create a process."""
    monkeypatch.setattr(module.sys, "argv", ["proof"])  # Omit explicit opt-in.
    with pytest.raises(SystemExit, match="Use --accept"):
        module.main()  # This command has no automatic default experiment.
    monkeypatch.setattr(module.sys, "argv", ["proof", "--accept", "private", "2"])
    with pytest.raises(module.HandoffError, match="INVALID_BUDGET"):
        # Invalid values must not echo private arguments.
        module.main()
    assert not launches  # Both rejected CLI paths must fail before launch.


def test_lost_launch_ack_does_not_claim_no_child(module, launches, monkeypatch):
    """A real created child can exist even when the launcher reports an error."""
    recording = module.subprocess.Popen  # Retain a separate trusted test witness.

    def lost_ack(*args, **kwargs):  # Model failure after actual process creation.
        # The recording fixture retains the real handle privately.
        recording(*args, **kwargs)
        # The harness receives no handle.
        raise OSError("synthetic lost launch acknowledgement")

    # Fail after the side effect.
    monkeypatch.setattr(module.subprocess, "Popen", lost_ack)
    # The test owns an independent handle and must clean its injected hidden child.
    try:
        with pytest.raises(module.HandoffError, match="CLEANUP_UNVERIFIED") as error:
            module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
        # Missing acknowledgement cannot establish absence.
        assert not error.value.cleanup_complete
        assert len(launches) == 1  # Never retry launch or create a replacement child.
    finally:  # Keep the negative test itself independently clean.
        # Only the recording test owns these hidden handles.
        for child, _, _ in launches:
            # Verify exact-child stop and reaping.
            assert module._cleanup(child, None, 2)
    assert_reaped(launches)  # Confirm the injected child really existed and was reaped.
