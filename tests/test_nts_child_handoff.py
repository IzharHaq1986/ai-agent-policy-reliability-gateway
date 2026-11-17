"""Synthetic direct-child proof tests; no real watchdog, Docker, or time exchange."""

import importlib  # Import only the test-support harness under explicit test control.
import os  # Check owned child reaping and descriptor release independently.
import signal  # Exercise unsafe-reaper rejection without launching a child.
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
    assert result["checked_adoption_used"] is True  # Exercise the real adoption helper.
    # The parent must independently observe original release, not trust its report.
    assert result["child_original_absence_observed"] is True
    assert (
        type(result["child_owned_fd"]) is int
    )  # Keep the checked locator's exact type.
    # Compare the two actual socket cases.
    assert result["closure_hidden_by_duplicate"] is duplicate
    assert result["synthetic_health_exchange_accepted"] is True  # Real bounded reply.
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
def test_child_exit_or_stall_cannot_pass(module, launches, monkeypatch, mode):
    exchanges = []  # Missing progress evidence must prevent a health challenge.
    # Record any forbidden advancement without supplying an alternate child response.
    monkeypatch.setattr(
        module, "_health_exchange", lambda *args: exchanges.append(True)
    )
    # Neither missing nor late evidence is accepted.
    with pytest.raises(module.HandoffError):
        module.run_case(
            duplicate=False, startup_seconds=0.2, cleanup_seconds=2, _mode=mode
        )
    assert not exchanges  # No report means no health exchange or execution authority.
    # A failed observation must still stop and reap the exact child.
    assert_reaped(launches)


@pytest.mark.parametrize("observer", ["_start_reference", "_inspect"])
def test_unavailable_identity_cleans_child(module, launches, monkeypatch, observer):
    def denied(*args):  # Simulate inaccessible trusted local evidence.
        # No fallback to child assertions is allowed.
        raise PermissionError("private proc detail")

    # Fail the independent identity observation.
    monkeypatch.setattr(module, observer, denied)
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
            "version",
            1,
        ),  # The prior raw-socket schema cannot satisfy the checked-copy proof.
        (
            "original_closed",
            False,
        ),  # Never accept a missing source-release acknowledgement.
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

    # An invalid report must not progress to a health challenge.
    exchanges = []  # Record forbidden advancement without authorizing any response.
    monkeypatch.setattr(
        module, "_health_exchange", lambda *args: exchanges.append(True)
    )
    # Leave all other launch behavior unchanged.
    monkeypatch.setattr(module, "_report", changed)
    with pytest.raises(module.HandoffError, match="IDENTITY_MISMATCH") as failure:
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    # Fixed field gate label.
    assert failure.value.identity_checkpoint == "REPORT_FIELDS"
    assert failure.value.__notes__ == [  # No rejected field values enter diagnostics.
        "IDENTITY_CHECKPOINT=REPORT_FIELDS"
    ]
    assert not exchanges  # A valid command cannot make an invalid report authoritative.
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
    # Match the parser's actual bounded transport input, preserving every bad packet.
    left, right = module.create_pair(buffer_bytes=8192, effective_buffer_limit=16384)
    # Data is already queued in this unit test.
    monkeypatch.setattr(module, "_wait", lambda *args: None)
    # Isolate packet parsing only.
    monkeypatch.setattr(module, "_child_exited", lambda _: False)
    try:  # Close both test endpoints even when the parser rejects input.
        # Deliberately bypass the sender cap only to fault-inject invalid receiver
        # input.
        left._owned().send(packet)
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
    with pytest.raises(module.HandoffError, match="IDENTITY_MISMATCH") as failure:
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    assert failure.value.identity_checkpoint == "REPORT_BOOT"  # Fixed boot-stage label.
    # Never expose either boot identity in an error.
    assert failure.value.__notes__ == ["IDENTITY_CHECKPOINT=REPORT_BOOT"]
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


@pytest.mark.parametrize("field", [0, 1, 2, 3])
def test_child_rejects_wrong_trusted_adoption_context(
    module, launches, monkeypatch, field
):
    """Require the child to validate the supplied adoption context."""
    original = module._adoption_context  # Preserve the actual fixed context formatter.

    def wrong(
        identity,
    ):  # Inject one explicit bad expectation into the real child argv.
        context = list(
            original(identity)
        )  # Keep the reviewed argument shape unchanged.
        if field in (0, 1):  # Device or inode must match the actual inherited object.
            context[field] = str(
                (int(context[field]) ^ 1) or 2
            )  # Keep a valid wrong integer.
        else:  # Invalid resource settings cannot be repaired inside the child.
            context[field] = (
                "0"  # The shared transport configuration gate must reject it.
            )
        return tuple(
            context
        )  # The parent's command witness still checks the actual argv.

    monkeypatch.setattr(
        module, "_adoption_context", wrong
    )  # Fault the trusted test context only.
    with pytest.raises(
        module.HandoffError
    ):  # No fixture report can restore a rejected setup.
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    assert_reaped(launches)  # The exact failed child must still be cleaned and reaped.
    assert len(launches) == 1  # No replacement child is allowed after rejection.
    assert (
        launches[0][0].returncode == 4
    )  # The real child rejected its adoption inputs.


def test_false_original_release_claim_is_rejected(module, launches):
    """A correct-looking report must not replace the independent absence observation."""
    with pytest.raises(module.HandoffError, match="ORIGINAL_STILL_OPEN"):
        module.run_case(
            duplicate=False,
            startup_seconds=5,
            cleanup_seconds=2,
            _mode="retain_original",
        )  # The child deliberately keeps its source open.
    assert_reaped(
        launches
    )  # Parent cleanup handles the failed fixture without a retry launch.
    assert len(launches) == 1  # Do not reopen the failed attempt under a new child.


def test_original_close_confirmation_loss_fails_attempt(module, launches):
    """Keep original-release uncertainty failed despite later child cleanup."""
    with pytest.raises(module.HandoffError, match="FIXTURE_RELEASE_UNCONFIRMED"):
        module.run_case(
            duplicate=False,
            startup_seconds=5,
            cleanup_seconds=2,
            _mode="source_close_failure",
        )  # Real close occurs before acknowledgement loss.
    assert_reaped(
        launches
    )  # The parent still independently verifies final exit/reaping.
    assert (
        launches[0][0].returncode == 6
    )  # Keep release uncertainty distinct from bad input.


@pytest.mark.parametrize("locator", [True, None, "3", 2, -1, 1 << 31])
def test_owned_descriptor_locator_is_validated(module, launches, monkeypatch, locator):
    """A report may locate a descriptor only inside the already retained child."""
    original = module._report  # Preserve actual bounded report reception first.

    def changed(
        *args,
    ):  # Alter one untrusted report field without changing launch ownership.
        report = original(*args)  # The real child still performs checked adoption.
        report["owned_fd"] = (
            locator  # Invalid types or ranges cannot select a kernel path.
        )
        return report  # The parent must reject before using this locator.

    monkeypatch.setattr(
        module, "_report", changed
    )  # Keep all other evidence checks intact.
    with pytest.raises(module.HandoffError, match="INVALID_OWNED_REFERENCE"):
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    assert_reaped(launches)  # A rejected locator cannot skip exact-child cleanup.


def test_original_absence_evidence_is_required(module, launches, monkeypatch):
    """Unreadable kernel evidence is not proof that the original descriptor is gone."""

    def denied(*args):  # Fail only the independent original-reference observation.
        raise PermissionError(
            "private absence detail"
        )  # Never print raw filesystem details.

    monkeypatch.setattr(
        module, "_original_absent", denied
    )  # Do not alter the child's valid report.
    with pytest.raises(module.HandoffError, match="HANDOFF_UNAVAILABLE"):
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    assert_reaped(
        launches
    )  # Retained identity still bounds cleanup after unavailable evidence.


def test_closed_channel_reconciliation_keeps_original_deadline(module, monkeypatch):
    """A closed channel cannot renew startup while the final child status is pending."""
    parent, child = module.create_pair(buffer_bytes=8192, effective_buffer_limit=16384)
    # Create actual closure before a simulated final-exit publication delay.
    child.close()
    calls = []  # Record the initial receive and failed-start reconciliation waits.

    def wait(pidfd, endpoint, deadline):  # This unit fixture has no real child.
        # Retain the exact supplied startup boundary.
        calls.append((endpoint, deadline))
        # The next wait must observe only the retained lifetime reference.
        if len(calls) == 2:
            # Failed-start reconciliation cannot reopen the channel.
            assert endpoint is None
            # Model expiry of the original bound.
            raise module.HandoffError("STARTUP_EXPIRED")

    monkeypatch.setattr(module, "_wait", wait)  # Isolate the timing-state transition.
    # Delay exit publication.
    monkeypatch.setattr(module, "_child_exited", lambda _: False)
    try:  # Keep this parser fixture independently clean.
        with pytest.raises(module.HandoffError, match="STARTUP_EXPIRED"):
            module._report(parent, -1, 123.0)  # Closure cannot become readiness.
        # Never reset the timeline.
        assert [limit for _, limit in calls] == [123.0, 123.0]
    finally:  # Releasing an already rejected owned endpoint is idempotent.
        parent.close()  # Close only the test's owned transport reference.


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("health_malformed", "HEALTH_EXCHANGE_REJECTED"),
        ("health_mismatch", "HEALTH_EXCHANGE_REJECTED"),
        ("health_lifetime", "HEALTH_EXCHANGE_REJECTED"),
        ("health_generation", "HEALTH_EXCHANGE_REJECTED"),
        ("health_silent", "HEALTH_RESPONSE_EXPIRED"),
        ("health_duplicate", "UNEXPECTED_PACKET_AFTER_EXIT"),
    ],
)
def test_real_health_failure_stops_and_reaps(module, launches, mode, reason):
    """Reject bad or missing replies across the actual child boundary."""
    before = len(os.listdir("/proc/self/fd"))  # Independently count owned descriptors.
    with pytest.raises(module.HandoffError, match=reason) as failure:
        module.run_case(  # Launch only the fixed reviewed negative fixture.
            duplicate=False, startup_seconds=5, cleanup_seconds=2, _mode=mode
        )
    assert failure.value.cleanup_complete is True  # Rejection still requires cleanup.
    assert len(launches) == 1  # Never replace the failed child automatically.
    assert_reaped(
        launches
    )  # Verify actual reaping rather than accepting a signal result.
    assert (
        len(os.listdir("/proc/self/fd")) == before
    )  # Failure must not leak endpoints.


def test_health_send_backpressure_is_not_retried(module, launches, monkeypatch):
    """A full challenge queue cannot create another token, send, or child."""
    original = (
        module.Endpoint.send
    )  # Keep real fixture-report and exit behavior available.
    challenges = []  # Count actual parent challenge send attempts only.

    def send(endpoint, packet):
        if b'"kind":"challenge"' in packet:  # Match the protocol's canonical encoding.
            challenges.append(packet)  # Record the attempted complete packet.
            raise module.WouldBlock()  # Model immediate nonblocking backpressure.
        return original(
            endpoint, packet
        )  # Do not alter unrelated transport operations.

    monkeypatch.setattr(module.Endpoint, "send", send)  # Inject only the parent fault.
    with pytest.raises(module.HandoffError, match="TRANSPORT_WOULD_BLOCK"):
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    assert len(challenges) == 1  # No resend or replacement challenge is permitted.
    assert len(launches) == 1  # Queue failure cannot launch a second child.
    assert_reaped(launches)  # Independently check cleanup of the original child.


def test_health_receive_race_preserves_one_challenge(module, launches, monkeypatch):
    """Temporary receive unavailability returns to waiting without a new exchange."""
    original = module.Endpoint.receive  # Preserve real packet delivery after one race.
    calls = []  # Count parent receives: report, injected race, then actual reply.

    def receive(endpoint):
        calls.append(endpoint)  # Track only this parent's receive operations.
        if len(calls) == 2:  # Leave the queued health response untouched on this call.
            # Model readiness followed by temporary no progress.
            raise module.WouldBlock()
        return original(endpoint)  # The next bounded wait can receive the same reply.

    monkeypatch.setattr(module.Endpoint, "receive", receive)  # Child remains unchanged.
    result = module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    assert (
        result["synthetic_health_exchange_accepted"] is True
    )  # Actual reply validated.
    assert len(launches) == 1  # A read race must not cause relaunch.
    assert_reaped(launches)  # Preserve independent exit and cleanup evidence.


def test_late_health_reply_cannot_pass(module, launches, monkeypatch):
    """A real valid reply fails when its acceptance timestamp exceeds the budget."""
    original = module.Session.accept  # Keep the actual protocol validation in use.

    def accept(session, packet, received_ns):
        return original(  # Model scheduling delay after delivery without sleeping.
            session, packet, received_ns + 2 * module._HEALTH_TIMEOUT_NS
        )

    monkeypatch.setattr(
        module.Session, "accept", accept
    )  # Affect only parent acceptance.
    with pytest.raises(module.HandoffError, match="HEALTH_EXCHANGE_REJECTED"):
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    assert len(launches) == 1  # A late response cannot reopen the attempt.
    assert_reaped(launches)  # The failure still releases the retained child.


def test_replayed_health_reply_cannot_pass(module, launches, monkeypatch):
    """The same real response cannot satisfy a second acceptance on its session."""
    original = module.Session.accept  # Reuse the real outstanding-challenge state.

    def accept(session, packet, received_ns):
        original(session, packet, received_ns)  # Consume the one legitimate reply.
        original(
            session, packet, received_ns
        )  # Replay must latch failure, not succeed.

    monkeypatch.setattr(
        module.Session, "accept", accept
    )  # Inject replay at validation.
    with pytest.raises(module.HandoffError, match="HEALTH_EXCHANGE_REJECTED"):
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    assert len(launches) == 1  # No fresh child or exchange repairs a failed session.
    assert_reaped(
        launches
    )  # Independently confirm the unsuccessful attempt was cleaned.


def test_peer_loss_during_health_exchange_stops(module, launches):
    """Peer closure never substitutes for a matching reply or verified cleanup."""
    with pytest.raises(module.HandoffError) as failure:
        module.run_case(  # Fixed fixture exits after receiving its challenge.
            duplicate=False, startup_seconds=5, cleanup_seconds=2, _mode="health_close"
        )
    assert str(failure.value) in {  # Kernel socket-close and exit status may race.
        "HEALTH_CHILD_EXITED",  # The independent pidfd observed exit first.
        "TRANSPORT_FAILED",  # Socket closure became visible before pidfd exit.
    }
    assert (
        failure.value.cleanup_complete is True
    )  # Both paths require confirmed cleanup.
    assert len(launches) == 1  # Peer loss cannot silently restart the attempt.
    assert_reaped(launches)  # Independently confirm the exact child was reaped.


@pytest.mark.parametrize(
    "checkpoint",
    [
        "COMMAND_SNAPSHOT",  # The bounded kernel command observation disagrees.
        "REPORT_START_REFERENCE",  # The bracketing process-start references disagree.
        "REPORT_CHILD_EXIT",  # Independent exit observation rejects the report stage.
        "HEALTH_START_REFERENCE",  # Identity changes after the valid synthetic reply.
        "HEALTH_BOOT",  # The post-reply boot observation changes.
        "HEALTH_CHILD_EXIT",  # Exit is observed after the accepted reply.
    ],
)
def test_identity_checkpoint_rejects_and_reaps(
    module, launches, monkeypatch, checkpoint
):
    """Identify an injected checkpoint without accepting its failure or retrying it."""
    inspect = module._inspect  # Preserve actual retained-child kernel inspection.
    report = module._report  # Keep actual packet receipt before report-stage faults.
    exchange = module._health_exchange  # Complete the real reply before later faults.
    boot = module._boot  # Preserve actual boot observations except the selected fault.
    exited = module._child_exited  # Keep real exit evidence outside the injected gate.
    # Track boundaries, not scheduling delays.
    phase = {"report": False, "health": False}
    before = len(os.listdir("/proc/self/fd"))  # Independent resource baseline.

    def receive_report(*args):
        value = report(*args)  # Do not substitute synthetic report success.
        phase["report"] = True  # The next identity bracket belongs to the report gate.
        return value  # Let all normal kernel and report checks still run.

    def health(*args):
        # First accept a real timely response through the real transport.
        exchange(*args)
        phase["health"] = True  # Only later observations receive post-reply faults.

    def observe(pid, command):
        if checkpoint == "COMMAND_SNAPSHOT":  # Fault expected comparison, not launch.
            # Real kernel bytes must reject.
            return inspect(pid, (*command, "unexpected"))
        ticks = inspect(pid, command)  # Read the actual owned child before injection.
        if checkpoint == "REPORT_START_REFERENCE" and phase["report"]:
            return ticks + 1  # Model a changed independently observed start reference.
        if checkpoint == "HEALTH_START_REFERENCE" and phase["health"]:
            return ticks + 1  # Do not change the fixture's actual process or reply.
        return ticks  # Leave unselected checkpoints unchanged.

    def observe_boot():
        value = boot()  # Obtain the normal host reference first.
        if checkpoint == "HEALTH_BOOT" and phase["health"]:
            return "0" * 36  # A deliberately different observation, never new trust.
        return value  # Preserve startup and report-stage source context.

    def observe_exit(pidfd):
        if checkpoint == "REPORT_CHILD_EXIT" and phase["report"]:
            return True  # Model exit at the report gate while retaining real cleanup.
        if checkpoint == "HEALTH_CHILD_EXIT" and phase["health"]:
            return True  # A previously valid reply must not bypass this rejection.
        # Preserve earlier actual retained-generation observations.
        return exited(pidfd)

    # Mark the real report boundary.
    monkeypatch.setattr(module, "_report", receive_report)
    # Mark the real reply boundary.
    monkeypatch.setattr(module, "_health_exchange", health)
    # Inject only selected identity data.
    monkeypatch.setattr(module, "_inspect", observe)
    # Change only the selected boot gate.
    monkeypatch.setattr(module, "_boot", observe_boot)
    # Preserve real cleanup APIs.
    monkeypatch.setattr(module, "_child_exited", observe_exit)
    with pytest.raises(module.HandoffError, match="IDENTITY_MISMATCH") as failure:
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    assert str(failure.value) == "IDENTITY_MISMATCH"  # Preserve the high-level reason.
    assert failure.value.identity_checkpoint == checkpoint  # Identify the actual gate.
    # No earlier fault advances.
    assert phase["health"] is checkpoint.startswith("HEALTH_")
    assert failure.value.__notes__ == [  # Notes contain only a fixed public-safe label.
        "IDENTITY_CHECKPOINT=" + checkpoint
    ]
    # No diagnosis substitutes for cleanup.
    assert failure.value.cleanup_complete is True
    # Diagnostic labeling cannot create retries or new children.
    assert len(launches) == 1
    assert_reaped(launches)  # Independently establish the owned child's final state.
    # Reject without descriptor leaks.
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("duplicate", [False, True])
def test_command_checks_follow_report_without_early_read(
    module, launches, monkeypatch, duplicate
):
    """An unpublished early command is never queried or accepted as identity."""
    import io  # Model an empty early kernel read without sleeps or a real race.

    report = module._report  # Keep the actual bounded report exchange.
    start = module._start_reference  # Preserve independent process-start evidence.
    inspect = module._inspect  # Keep every strict command comparison in use.
    witness = module._endpoint_witness  # Preserve the independent owned-socket gate.
    # Preserve independent original-descriptor absence.
    absence = module._original_absent
    health = module._health_exchange  # Keep the real challenge and response path.
    open_path = Path.open  # Delegate unmodified kernel reads to the real filesystem.
    # A report indicates progress only; its identity remains untrusted.
    received = False
    # Record the security-relevant order without imposing scheduling delays.
    events = []
    # Count command reads so an automatic retry cannot hide the first result.
    reads = []

    def read(path, *args, **kwargs):
        if path.name == "cmdline":  # Fault only the existing fixed-path kernel query.
            reads.append(received)  # Record whether the report preceded this read.
            # The earlier implementation would observe this empty value.
            if not received:
                # No permissive replacement or retry is supplied.
                return io.BytesIO(b"")
        return open_path(path, *args, **kwargs)  # Keep normal post-report reads real.

    def observe_start(pid):
        if not received:  # A trusted lifetime witness must still precede the report.
            events.append("initial_start")  # This is not a command-profile observation.
        return start(pid)  # Use the actual retained child, never a reported PID.

    def receive(*args):
        nonlocal received  # Update only this test's progress boundary.
        value = report(*args)  # Receive and parse the real bounded fixture report.
        received = True  # Signal progress, not trusted identity or permission.
        events.append("report")  # Later strict checks must follow this point.
        return value  # Do not alter the child's report values.

    def observe(*args):
        events.append("command")  # Mark each full comparison before its real execution.
        return inspect(*args)  # Empty or wrong post-report snapshots still reject.

    def socket_gate(*args):
        events.append("endpoint")  # Require two full checks around this witness.
        return witness(*args)  # Validate the actual kernel socket identity and flags.

    def original_gate(*args):
        events.append("original_absence")  # Keep this separate from the child's claim.
        return absence(*args)  # Independently inspect the retained child's descriptor.

    def exchange(*args):
        events.append("health")  # No challenge may precede both profile checks.
        # Perform the real synthetic exchange under the old deadline.
        return health(*args)

    # Supply a deterministic early-empty scenario.
    monkeypatch.setattr(Path, "open", read)
    # Keep lifetime coverage.
    monkeypatch.setattr(module, "_start_reference", observe_start)
    monkeypatch.setattr(module, "_report", receive)  # Mark actual report completion.
    monkeypatch.setattr(module, "_inspect", observe)  # Count every full identity check.
    # Keep the kernel gate.
    monkeypatch.setattr(module, "_endpoint_witness", socket_gate)
    # Keep the absence gate.
    monkeypatch.setattr(module, "_original_absent", original_gate)
    # Observe real challenge order.
    monkeypatch.setattr(module, "_health_exchange", exchange)
    result = module.run_case(duplicate=duplicate, startup_seconds=5, cleanup_seconds=2)
    # Two pre-health checks plus the post-reply check.
    assert reads == [True, True, True]
    assert events == [  # No sleeps, identity retries, or reordered permission gates.
        "initial_start",
        "report",
        "command",
        "endpoint",
        "original_absence",
        "command",
        "health",
        "command",
    ]
    # Real reply was checked.
    assert result["synthetic_health_exchange_accepted"] is True
    # This proves no execution authority.
    assert result["approval_eligibility"] == "blocked"
    assert_reaped(launches)  # Independently verify final ownership cleanup.


@pytest.mark.parametrize("command_number", [1, 2, 3])
@pytest.mark.parametrize("fault", ["empty", "oversized", "different"])
def test_post_report_command_failure_is_never_retried(
    module, launches, monkeypatch, command_number, fault
):
    """All three post-report profile checks reject bad evidence on their first read."""
    # Supply bounded deterministic kernel observations without altering the child.
    import io

    open_path = Path.open  # Preserve all unselected actual kernel observations.
    exchange = module._health_exchange  # Observe whether a real reply already occurred.
    reads = []  # Count strict command queries independently of the result message.
    exchanges = []  # Earlier profile rejection must prevent the health challenge.
    before = len(os.listdir("/proc/self/fd"))  # Independently count owned descriptors.

    def read(path, *args, **kwargs):
        if path.name == "cmdline":  # Change only the selected command snapshot.
            # Each query consumes one observation; no retries allowed.
            reads.append(True)
            if len(reads) == command_number:  # Fault one existing full-profile check.
                value = {  # Keep each fault bounded and distinct from valid argv.
                    "empty": b"",  # Startup or unavailable publication is not accepted.
                    "oversized": b"x" * (module.QUERY_BYTES + 1),  # Detect overflow.
                    "different": b"unexpected-command\0",  # Reject a different profile.
                }[fault]
                return io.BytesIO(value)  # The actual comparison must reject this read.
        return open_path(path, *args, **kwargs)  # Do not fake other identity evidence.

    def health(*args):
        exchanges.append(True)  # Only the third profile check follows an exchange.
        return exchange(*args)  # Keep the real protocol validator and transport.

    monkeypatch.setattr(Path, "open", read)  # Inject only parent-side observation data.
    # Record protocol progression.
    monkeypatch.setattr(module, "_health_exchange", health)
    with pytest.raises(module.HandoffError, match="IDENTITY_MISMATCH") as failure:
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    # Keep precise diagnosis.
    assert failure.value.identity_checkpoint == "COMMAND_SNAPSHOT"
    # Never read again until an identity happens to match.
    assert len(reads) == command_number
    assert len(exchanges) == (1 if command_number == 3 else 0)  # Keep challenge gates.
    # Failed identity still requires cleanup.
    assert failure.value.cleanup_complete is True
    assert len(launches) == 1  # Do not replace the rejected child.
    assert_reaped(launches)  # Independently confirm the exact owned child's exit.
    # No descriptor leak after rejection.
    assert len(os.listdir("/proc/self/fd")) == before


def test_pre_report_start_reference_cannot_be_replaced(module, launches, monkeypatch):
    """A valid report and matching command cannot replace a changed start witness."""
    start = module._start_reference  # Preserve real process-state parsing.
    received = False  # Identify the one pre-report observation deterministically.
    report = module._report  # Keep the real progress packet and its bounded parser.
    exchanges = []  # A changed start reference must block the health exchange.

    def observe(pid):
        value = start(pid)  # Obtain the actual retained-child reference first.
        return value if received else value + 1  # Model a changed pre-report witness.

    def receive(*args):
        nonlocal received  # Do not replace the report or its expected identity fields.
        # Wait for the real child report under the original deadline.
        value = report(*args)
        # Subsequent independent observations report the actual reference.
        received = True
        return value  # The parent must compare against its earlier witness.

    # Change only initial evidence.
    monkeypatch.setattr(module, "_start_reference", observe)
    monkeypatch.setattr(module, "_report", receive)  # Preserve the actual report path.
    monkeypatch.setattr(
        module, "_health_exchange", lambda *args: exchanges.append(True)
    )
    with pytest.raises(module.HandoffError, match="IDENTITY_MISMATCH") as failure:
        module.run_case(duplicate=False, startup_seconds=5, cleanup_seconds=2)
    # Reject the change.
    assert failure.value.identity_checkpoint == "REPORT_START_REFERENCE"
    assert not exchanges  # A report cannot grant permission to advance.
    assert_reaped(launches)  # Reconcile and clean up only the retained owned child.
