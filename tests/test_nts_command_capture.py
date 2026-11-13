"""Exercise real command deadlines, output bounds, and owned-process cleanup."""

import importlib
import os
import signal
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture
def helper(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "support"))
    return importlib.import_module("nts_command_capture")


def run(helper, code, *, budget=None, deadline=None):
    return helper.capture(
        (sys.executable, "-c", code),
        deadline=time.monotonic() + 5 if deadline is None else deadline,
        budget=helper.OutputBudget() if budget is None else budget,
    )


def test_capture_preserves_both_binary_streams_and_times(helper):
    budget = helper.OutputBudget()
    result = run(
        helper, "import os; os.write(1,b'a\\x00'); os.write(2,b'\\xffb')", budget=budget
    )
    assert result.stdout == b"a\0" and result.stderr == b"\xffb"
    assert result.started_monotonic <= result.ended_monotonic
    assert budget.remaining == helper.BUNDLE_BYTES - 4


def test_empty_output_is_success(helper):
    result = run(helper, "pass")
    assert result.stdout == result.stderr == b""


@pytest.mark.parametrize("stream", [1, 2])
def test_exact_query_limit_is_accepted(helper, stream):
    result = run(helper, f"import os; os.write({stream}, b'x'*32768)")
    assert len(result.stdout) + len(result.stderr) == helper.QUERY_BYTES


@pytest.mark.parametrize("stream", [1, 2])
def test_real_overflow_is_rejected_and_charged(helper, stream):
    budget = helper.OutputBudget()
    with pytest.raises(helper.CaptureError, match="^OUTPUT_LIMIT$"):
        run(helper, f"import os; os.write({stream}, b'x'*32769)", budget=budget)
    assert budget.remaining == helper.BUNDLE_BYTES - 32769


def test_stdout_and_stderr_share_the_query_limit(helper):
    with pytest.raises(helper.CaptureError, match="^OUTPUT_LIMIT$"):
        run(helper, "import os; os.write(1,b'x'*20000); os.write(2,b'y'*20000)")


def test_shared_budget_is_not_reset_between_queries(helper):
    budget = helper.OutputBudget(5)
    run(helper, "print('abc', end='')", budget=budget)
    assert budget.remaining == 2
    run(helper, "print('de', end='')", budget=budget)
    assert budget.remaining == 0
    with pytest.raises(helper.CaptureError, match="^OUTPUT_BUDGET_EXHAUSTED$"):
        run(helper, "raise AssertionError('must never run')", budget=budget)


def test_budget_overflow_fails_without_returning_partial_output(helper):
    budget = helper.OutputBudget(3)
    with pytest.raises(helper.CaptureError, match="^OUTPUT_LIMIT$"):
        run(helper, "print('secret', end='')", budget=budget)
    assert budget.remaining == 0


def test_nonzero_exit_never_exposes_output_or_refunds_budget(helper, capsys):
    budget = helper.OutputBudget(100)
    with pytest.raises(helper.CaptureError, match="^COMMAND_FAILED$") as error:
        run(
            helper, "import sys; print('sensitive-payload'); sys.exit(7)", budget=budget
        )
    assert "sensitive" not in str(error.value)
    assert budget.remaining < 100
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "deadline", [float("nan"), float("inf"), -float("inf"), True, 10**1000]
)
def test_invalid_deadline_never_launches(helper, monkeypatch, deadline):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid input launched a process")

    monkeypatch.setattr(helper.subprocess, "Popen", forbidden)
    with pytest.raises(helper.CaptureError):
        run(helper, "pass", deadline=deadline)


def test_expired_deadline_never_launches(helper, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("expired deadline launched a process")

    monkeypatch.setattr(helper.subprocess, "Popen", forbidden)
    with pytest.raises(helper.CaptureError, match="^QUERY_TIMEOUT$"):
        run(helper, "pass", deadline=time.monotonic() - 1)


@pytest.mark.parametrize("limit", [0, -1, True, 1048577, 1.5])
def test_invalid_budgets_are_rejected(helper, limit):
    with pytest.raises(helper.CaptureError, match="^INVALID_OUTPUT_BUDGET$"):
        helper.OutputBudget(limit)


def test_missing_executable_has_fixed_error(helper):
    with pytest.raises(helper.CaptureError, match="^COMMAND_MISSING$"):
        helper.capture(
            ("/nonexistent-nts-command",),
            deadline=time.monotonic() + 1,
            budget=helper.OutputBudget(),
        )


def test_arguments_are_not_interpreted_by_a_shell(helper, tmp_path):
    target = tmp_path / "must-not-exist"
    argument = f"$(touch {target}); exit 9"
    result = helper.capture(
        (sys.executable, "-c", "import sys; print(sys.argv[1], end='')", argument),
        deadline=time.monotonic() + 2,
        budget=helper.OutputBudget(),
    )
    assert result.stdout.decode() == argument
    assert not target.exists()


def test_each_query_is_capped_at_two_seconds(helper):
    started = time.monotonic()
    with pytest.raises(helper.CaptureError, match="^QUERY_TIMEOUT$"):
        run(helper, "import time; time.sleep(30)")
    assert 1.8 <= time.monotonic() - started < 5


def test_prior_query_does_not_refresh_shared_deadline(helper):
    deadline = time.monotonic() + 0.5
    budget = helper.OutputBudget()
    run(helper, "import time; time.sleep(0.15)", budget=budget, deadline=deadline)
    with pytest.raises(helper.CaptureError, match="^QUERY_TIMEOUT$"):
        run(helper, "import time; time.sleep(30)", budget=budget, deadline=deadline)
    assert time.monotonic() - deadline < 1


def stopped(pid):
    path = Path(f"/proc/{pid}/stat")
    try:
        return path.read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except (FileNotFoundError, ProcessLookupError):
        # Linux can lose the process after opening /proc but before reading it.
        return True


def test_timeout_kills_group_even_when_parent_exits_first(helper, tmp_path):
    record = tmp_path / "pids"
    child = (
        "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "time.sleep(30)"
    )
    code = (
        "import os,subprocess,sys; from pathlib import Path; "
        f"p=subprocess.Popen([sys.executable,'-c',{child!r}]); "
        f"Path({str(record)!r}).write_text(str(os.getpid())+' '+str(p.pid))"
    )
    with pytest.raises(helper.CaptureError, match="^QUERY_TIMEOUT$"):
        run(helper, code, deadline=time.monotonic() + 0.7)
    parent, descendant = map(int, record.read_text().split())
    try:
        assert not Path(f"/proc/{parent}").exists(), "owned leader was not reaped"
        deadline = time.monotonic() + 1
        while not stopped(descendant) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert stopped(descendant), "descendant in the owned group is still running"
    finally:
        if not stopped(descendant):
            os.kill(descendant, signal.SIGKILL)


def test_closed_pipes_do_not_hide_a_running_process(helper):
    with pytest.raises(helper.CaptureError, match="^QUERY_TIMEOUT$"):
        run(
            helper,
            "import os,time; os.close(1); os.close(2); time.sleep(30)",
            deadline=time.monotonic() + 0.3,
        )


def test_cleanup_failure_overrides_success(helper, monkeypatch):
    cleanup = helper._cleanup

    def failed_cleanup(process):
        cleanup(process)
        raise helper.CaptureError("PROCESS_CLEANUP_FAILED")

    monkeypatch.setattr(helper, "_cleanup", failed_cleanup)
    with pytest.raises(helper.CaptureError, match="^PROCESS_CLEANUP_FAILED$"):
        run(helper, "pass")


@pytest.mark.parametrize(
    "failure", [OSError("sensitive read error"), KeyboardInterrupt()]
)
def test_read_failure_or_interrupt_still_reaps_leader(helper, monkeypatch, failure):
    pids = []

    def failed_read(process, *args):
        pids.append(process.pid)
        raise failure

    monkeypatch.setattr(helper, "_read", failed_read)
    expected = (
        KeyboardInterrupt
        if isinstance(failure, KeyboardInterrupt)
        else helper.CaptureError
    )
    with pytest.raises(expected):
        run(helper, "import time; time.sleep(30)")
    assert len(pids) == 1 and not Path(f"/proc/{pids[0]}").exists()


@pytest.mark.parametrize("command", [(), "echo unsafe", ("",), ("x\0y",), ("x", 7)])
def test_invalid_argv_never_launches(helper, monkeypatch, command):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid argv launched a process")

    monkeypatch.setattr(helper.subprocess, "Popen", forbidden)
    with pytest.raises(helper.CaptureError, match="^INVALID_CAPTURE_INPUT$"):
        helper.capture(
            command, deadline=time.monotonic() + 1, budget=helper.OutputBudget()
        )


def test_permission_failure_has_sanitized_error(helper, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("sensitive-path")

    monkeypatch.setattr(helper.subprocess, "Popen", denied)
    with pytest.raises(helper.CaptureError, match="^COMMAND_UNAVAILABLE$") as error:
        run(helper, "pass")
    assert error.value.__suppress_context__


def test_failed_group_signal_still_attempts_reap_and_pipe_closure(helper, monkeypatch):
    from types import SimpleNamespace

    calls = []

    def denied(*args):
        calls.append("signal")
        raise PermissionError("sensitive-path")

    def wait(*, timeout):
        calls.append(("wait", timeout))
        return 0

    def close():
        calls.append("close")

    monkeypatch.setattr(helper.os, "killpg", denied)
    process = SimpleNamespace(
        pid=123,
        wait=wait,
        stdout=SimpleNamespace(close=close),
        stderr=SimpleNamespace(close=close),
    )
    with pytest.raises(helper.CaptureError, match="^PROCESS_CLEANUP_FAILED$"):
        helper._cleanup(process)
    assert calls == ["signal", ("wait", helper.CLEANUP_SECONDS), "close", "close"]


def test_external_child_reaper_prevents_launch(helper, monkeypatch):
    monkeypatch.setattr(helper.signal, "getsignal", lambda number: signal.SIG_IGN)

    def forbidden(*args, **kwargs):
        pytest.fail("unsafe child reaping allowed process launch")

    monkeypatch.setattr(helper.subprocess, "Popen", forbidden)
    with pytest.raises(helper.CaptureError, match="^UNSAFE_CHILD_REAPER$"):
        run(helper, "pass")


def test_overflow_stops_and_reaps_a_still_running_producer(helper, tmp_path):
    record = tmp_path / "producer"
    code = (
        "import os,time; from pathlib import Path; "
        f"Path({str(record)!r}).write_text(str(os.getpid())); "
        "os.write(2,b'x'*32769); time.sleep(30)"
    )
    with pytest.raises(helper.CaptureError, match="^OUTPUT_LIMIT$"):
        run(helper, code)
    pid = int(record.read_text())
    assert not Path(f"/proc/{pid}").exists(), "overflow producer was not reaped"


@pytest.mark.parametrize("failure", [FileNotFoundError(), ProcessLookupError()])
def test_disappeared_process_during_proc_read_counts_as_gone(monkeypatch, failure):
    def disappeared(*args, **kwargs):
        raise failure

    monkeypatch.setattr(Path, "read_text", disappeared)
    assert stopped(123)


def test_inaccessible_proc_state_is_not_proof_of_stopping(monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("synthetic denied access")

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(PermissionError):
        stopped(123)
