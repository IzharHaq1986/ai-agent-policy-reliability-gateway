"""Check watchdog ownership, explicit readiness, and independent deadline action."""

import importlib
import json
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path

import pytest

CONTAINER = "a" * 64
OWNER = "b" * 32


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "support"))
    return importlib.import_module("nts_container_watchdog")


def metadata(module, *, owner=OWNER, state="created"):
    return json.dumps(
        [
            {
                "Id": CONTAINER,
                "Config": {"Labels": {module.LABEL: owner}},
                "State": {"Status": state},
            }
        ]
    )


def install(monkeypatch, module, outputs):
    calls = []
    responses = iter(outputs)

    def query(arguments, deadline, budget):
        calls.append(arguments)
        value = next(responses)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(module, "query", query)
    return calls


def test_owned_removal_requires_fresh_ownership_and_independent_absence(
    module, monkeypatch
):
    calls = install(
        monkeypatch, module, [CONTAINER + "\n", metadata(module), CONTAINER, ""]
    )
    result = module.remove_owned(
        CONTAINER, OWNER, time.monotonic() + 10, module.OutputBudget()
    )
    assert result == "removed_and_absence_verified"
    assert calls[1] == ("container", "inspect", CONTAINER)
    assert calls[2] == ("container", "rm", "--force", CONTAINER)
    assert calls[0] == calls[3]
    assert "--no-trunc" in calls[3]


def test_already_absent_never_attempts_removal(module, monkeypatch):
    calls = install(monkeypatch, module, [""])
    assert (
        module.remove_owned(
            CONTAINER, OWNER, time.monotonic() + 10, module.OutputBudget()
        )
        == "already_absent"
    )
    assert len(calls) == 1


@pytest.mark.parametrize("output", ["wrong-id\n", CONTAINER + "\n" + CONTAINER + "\n"])
def test_invalid_inventory_does_not_mean_absence(module, monkeypatch, output):
    install(monkeypatch, module, [output])
    with pytest.raises(module.WatchdogError, match="INVALID_CONTAINER_INVENTORY"):
        module.absent(CONTAINER, time.monotonic() + 2, module.OutputBudget())


@pytest.mark.parametrize("output", ["{}", "[]", "invalid-json", "[{}]"])
def test_malformed_ownership_is_rejected(module, monkeypatch, output):
    install(monkeypatch, module, [output])
    with pytest.raises(module.WatchdogError):
        module.verify_owner(
            CONTAINER, OWNER, time.monotonic() + 2, module.OutputBudget()
        )


def test_ownership_mismatch_prevents_destructive_action(module, monkeypatch):
    calls = install(monkeypatch, module, [CONTAINER, metadata(module, owner="c" * 32)])
    with pytest.raises(module.WatchdogError):
        module.remove_owned(
            CONTAINER, OWNER, time.monotonic() + 10, module.OutputBudget()
        )
    assert all("rm" not in args for args in calls)


def test_removal_exit_alone_cannot_prove_absence(module, monkeypatch):
    install(monkeypatch, module, [CONTAINER, metadata(module), CONTAINER, CONTAINER])
    with pytest.raises(module.WatchdogError, match="REMOVAL_NOT_VERIFIED"):
        module.remove_owned(
            CONTAINER, OWNER, time.monotonic() + 10, module.OutputBudget()
        )


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_any_docker_failure_prevents_success(module, monkeypatch, index):
    values = [CONTAINER, metadata(module), CONTAINER, ""]
    values[index] = module.CaptureError("QUERY_TIMEOUT")
    install(monkeypatch, module, values)
    with pytest.raises(module.CaptureError, match="QUERY_TIMEOUT"):
        module.remove_owned(
            CONTAINER, OWNER, time.monotonic() + 10, module.OutputBudget()
        )


@pytest.mark.parametrize(
    "kind", ["expired", "too_long", "infinite", "wrong_id", "wrong_owner", "wrong_boot"]
)
def test_invalid_inputs_cannot_arm(module, monkeypatch, tmp_path, kind):
    calls = install(monkeypatch, module, [])
    deadline = time.monotonic() + 1
    container, owner, boot = CONTAINER, OWNER, module.boot_id()
    if kind == "expired":
        deadline = time.monotonic() - 1
    elif kind == "too_long":
        deadline = time.monotonic() + 121
    elif kind == "infinite":
        deadline = float("inf")
    elif kind == "wrong_id":
        container = "short-prefix"
    elif kind == "wrong_owner":
        owner = "invalid"
    else:
        boot = "different-boot"
    with pytest.raises(module.WatchdogError, match="INVALID_WATCHDOG_INPUT"):
        module.watch(container, owner, deadline, boot, tmp_path / "record")
    assert calls == [] and not (tmp_path / "record").exists()


def test_arming_failure_is_persisted_without_ready(
    module, monkeypatch, tmp_path, capfd
):
    calls = install(monkeypatch, module, [metadata(module, state="running")])
    record = tmp_path / "record"
    assert (
        module.watch(CONTAINER, OWNER, time.monotonic() + 1, module.boot_id(), record)
        == 2
    )
    events = [json.loads(line) for line in record.read_text().splitlines()]
    assert [event["event"] for event in events] == ["failed"]
    assert "armed" not in capfd.readouterr().out
    assert len(calls) == 1


def test_record_is_exclusive_and_never_overwritten(module, monkeypatch, tmp_path):
    record = tmp_path / "record"
    record.write_text("earlier-evidence")
    calls = install(monkeypatch, module, [])
    with pytest.raises(FileExistsError):
        module.watch(CONTAINER, OWNER, time.monotonic() + 1, module.boot_id(), record)
    assert record.read_text() == "earlier-evidence" and calls == []


def test_unsafe_record_directory_cannot_arm(module, monkeypatch, tmp_path):
    calls = install(monkeypatch, module, [])
    tmp_path.chmod(0o755)
    with pytest.raises(module.WatchdogError, match="UNSAFE_RECORD_DIRECTORY"):
        module.watch(
            CONTAINER,
            OWNER,
            time.monotonic() + 1,
            module.boot_id(),
            tmp_path / "record",
        )
    assert calls == []


def test_no_opt_in_never_queries_docker(module, monkeypatch):
    calls = install(monkeypatch, module, [])
    assert module.main([]) == 2
    assert calls == []


@pytest.fixture
def fake_docker(module, tmp_path, monkeypatch):
    # Exercise the real subprocess boundary with a local filesystem simulator,
    # never a production Docker daemon. This is not Docker acceptance evidence.
    state = tmp_path / "state.json"
    state.write_text(metadata(module))
    executable = tmp_path / "docker"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys\nfrom pathlib import Path\n"
        "p=Path(os.environ['WATCHDOG_TEST_STATE']); args=sys.argv[1:]\n"
        "if args[:2]==['container','inspect']:\n"
        " print(p.read_text())\n"
        "elif args[:2]==['container','ls']:\n"
        " print(json.loads(p.read_text())[0]['Id'] if p.exists() else '',end='')\n"
        "elif args[:3]==['container','rm','--force']:\n"
        " p.unlink(); print(args[3])\n"
        "else:\n sys.exit(9)\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("WATCHDOG_TEST_STATE", str(state))
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    return state


def launch(module, tmp_path):
    record = tmp_path / "record.jsonl"
    deadline = time.monotonic() + 1.2
    process = subprocess.Popen(
        [
            sys.executable,
            module.__file__,
            "--watch",
            CONTAINER,
            OWNER,
            repr(deadline),
            module.boot_id(),
            str(record),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(2), "no readiness or failure acknowledgement"
        event = json.loads(process.stdout.readline(2049))
        assert event["event"] == "armed" and event["watchdog_pid"] == process.pid
    except BaseException:
        process.kill()
        process.wait(timeout=2)
        raise
    return process, record, deadline


@pytest.mark.parametrize("disconnect", [False, True])
def test_real_watchdog_process_survives_blocked_or_disconnected_collector(
    module, fake_docker, tmp_path, disconnect
):
    process, record, deadline = launch(module, tmp_path)
    try:
        if disconnect:
            process.stdout.close()
        # No service calls to the watchdog while the collector is blocked.
        assert process.wait(timeout=13) == 0
        resumed = time.monotonic()
        events = [json.loads(line) for line in record.read_text().splitlines()]
        assert [event["event"] for event in events] == ["armed", "finished"]
        assert deadline <= events[1]["ended_monotonic"] < resumed
        assert events[1]["absence_verified"] is True
        assert events[1]["approval_eligibility"] == "blocked"
        assert not fake_docker.exists()
    finally:
        if process.returncode is None:
            process.kill()
            process.wait(timeout=2)
        process.stdout.close()
        process.stderr.close()


def test_label_changed_after_arming_blocks_removal(module, fake_docker, tmp_path):
    process, record, _ = launch(module, tmp_path)
    try:
        fake_docker.write_text(metadata(module, owner="c" * 32))
        process.communicate(timeout=4)
        assert process.returncode == 2
        assert fake_docker.exists()
        events = [json.loads(line) for line in record.read_text().splitlines()]
        assert events[-1]["event"] == "failed"
        assert events[-1]["absence_verified"] is False
    finally:
        if process.returncode is None:
            process.kill()
            process.wait(timeout=2)


@pytest.mark.parametrize(
    "reason", ["QUERY_TIMEOUT", "COMMAND_FAILED", "PROCESS_CLEANUP_FAILED"]
)
def test_failed_deadline_action_is_recorded_without_success(
    module, monkeypatch, tmp_path, reason
):
    from types import SimpleNamespace

    now = [100.0]

    def sleep(seconds):
        now[0] += seconds

    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=sleep)
    )
    install(monkeypatch, module, [metadata(module)])

    def failed_removal(*args):
        raise module.CaptureError(reason)

    monkeypatch.setattr(module, "remove_owned", failed_removal)
    record = tmp_path / "record"
    assert module.watch(CONTAINER, OWNER, 100.5, module.boot_id(), record) == 2
    events = [json.loads(line) for line in record.read_text().splitlines()]
    assert [event["event"] for event in events] == ["armed", "failed"]
    assert events[-1]["reason_code"] == reason
    assert events[-1]["absence_verified"] is False


@pytest.mark.parametrize(
    "changed",
    ["event", "owner", "container_id", "watchdog_pid", "deadline_monotonic", "boot_id"],
)
def test_acceptance_readiness_rejects_wrong_acknowledgement(
    module, monkeypatch, changed
):
    from types import SimpleNamespace

    acceptance = importlib.import_module("run_nts_watchdog_acceptance")
    deadline = time.monotonic() + 2
    event = {
        "event": "armed",
        "owner": OWNER,
        "container_id": CONTAINER,
        "watchdog_pid": 123,
        "deadline_monotonic": deadline,
        "boot_id": module.boot_id(),
    }
    event[changed] = "invalid"
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb") as stream:
        with os.fdopen(write_fd, "wb") as writer:
            writer.write((json.dumps(event) + "\n").encode())
        with pytest.raises(RuntimeError, match="Invalid watchdog readiness"):
            acceptance.ready(
                SimpleNamespace(stdout=stream, pid=123), CONTAINER, OWNER, deadline
            )


def test_acceptance_readiness_eof_is_not_an_acknowledgement(module):
    from types import SimpleNamespace

    acceptance = importlib.import_module("run_nts_watchdog_acceptance")
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    with os.fdopen(read_fd, "rb") as stream:
        with pytest.raises(RuntimeError, match="before readiness"):
            acceptance.ready(
                SimpleNamespace(stdout=stream), CONTAINER, OWNER, time.monotonic() + 1
            )


def test_late_removal_cannot_be_reported_as_deadline_success(
    module, monkeypatch, tmp_path
):
    from types import SimpleNamespace

    now = [100.0]

    def sleep(seconds):
        now[0] += seconds

    def late_removal(*args):
        now[0] += 11
        return "removed_and_absence_verified"

    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=sleep)
    )
    install(monkeypatch, module, [metadata(module)])
    monkeypatch.setattr(module, "remove_owned", late_removal)
    record = tmp_path / "record"
    assert module.watch(CONTAINER, OWNER, 100.5, module.boot_id(), record) == 2
    events = [json.loads(line) for line in record.read_text().splitlines()]
    assert events[-1]["event"] == "failed"
    assert events[-1]["reason_code"] == "DEADLINE_ACTION_LATE"
    assert events[-1]["absence_verified"] is True


def test_acceptance_requires_explicit_opt_in(module, monkeypatch):
    acceptance = importlib.import_module("run_nts_watchdog_acceptance")

    def forbidden(*args, **kwargs):
        pytest.fail("Docker invoked without acceptance opt-in")

    monkeypatch.setattr(acceptance, "docker", forbidden)
    assert acceptance.main([]) == 2


def test_acceptance_cleanup_failure_never_reports_pass(module, monkeypatch, capsys):
    from types import SimpleNamespace

    acceptance = importlib.import_module("run_nts_watchdog_acceptance")

    def docker(*args, **kwargs):
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Id": "sha256:" + "a" * 64}])
        if args[:2] == ("image", "rm"):
            raise RuntimeError("cleanup unavailable")
        return ""

    monkeypatch.setattr(acceptance, "docker", docker)
    monkeypatch.setattr(
        acceptance.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(acceptance, "run_case", lambda *args: None)
    with pytest.raises(RuntimeError, match="cleanup unavailable"):
        acceptance.main(["--accept"])
    assert "PASS" not in capsys.readouterr().out


def test_acceptance_rejects_oversize_readiness(module):
    from types import SimpleNamespace

    acceptance = importlib.import_module("run_nts_watchdog_acceptance")
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb") as stream:
        with os.fdopen(write_fd, "wb") as writer:
            writer.write(b"x" * 2049)
        with pytest.raises(RuntimeError, match="exceeded limit"):
            acceptance.ready(
                SimpleNamespace(stdout=stream), CONTAINER, OWNER, time.monotonic() + 1
            )


def test_non_ascii_docker_output_fails_closed(module, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        module, "capture", lambda *args, **kwargs: SimpleNamespace(stdout=b"\xff")
    )
    with pytest.raises(module.WatchdogError, match="INVALID_DOCKER_OUTPUT"):
        module.query(("container", "ls"), time.monotonic() + 1, module.OutputBudget())
