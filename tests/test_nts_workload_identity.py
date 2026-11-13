"""Synthetic identity fixtures are not real Docker or trusted-time proof."""

import importlib
import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

CONTAINER = "a" * 64
IMAGE = "sha256:" + "b" * 64
OWNER = "c" * 32
PID = 123
COMMAND = ("python", "-c", "import time; time.sleep(600)")
START = "2026-11-13T12:00:00.123456789Z"


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "support"))
    return importlib.import_module("nts_workload_identity")


def process_stat(ticks=12345, pid=PID, state="S"):
    return f"{pid} (python test (name)) " + " ".join(
        [state] + ["0"] * 18 + [str(ticks)]
    )


def process_status():
    return (
        "\n".join(
            [
                f"Pid:\t{PID}",
                "Uid:\t10001 10001 10001 10001",
                "Gid:\t10001 10001 10001 10001",
                "NoNewPrivs:\t1",
                *(
                    key + ":\t" + "0" * 16
                    for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
                ),
            ]
        )
        + "\n"
    )


def inspect(module):
    return {
        "Id": CONTAINER,
        "Image": IMAGE,
        "User": "10001:10001",
        "Labels": {module.LABEL: OWNER},
        "RestartCount": 0,
        "State": {
            "Pid": PID,
            "StartedAt": START,
            "Status": "running",
            "Running": True,
            "Paused": False,
            "Restarting": False,
            "Dead": False,
            "OOMKilled": False,
            "Error": "",
        },
    }


@pytest.fixture
def installed(module, monkeypatch, tmp_path):
    proc = tmp_path / "proc"
    directory = proc / str(PID)
    directory.mkdir(parents=True)
    (directory / "stat").write_text(process_stat())
    (directory / "status").write_text(process_status())
    (directory / "cmdline").write_bytes(
        b"\0".join(arg.encode() for arg in COMMAND) + b"\0"
    )
    (directory / "cgroup").write_text(f"0::/system.slice/docker-{CONTAINER}.scope\n")
    monkeypatch.setattr(module, "_PROC", proc)
    expected = module.ExpectedWorkload(
        CONTAINER, IMAGE, OWNER, COMMAND, module.boot_id()
    )
    calls = []
    data = inspect(module)

    def capture(arguments, *, deadline, budget):
        assert arguments[: len(module._DOCKER)] == module._DOCKER
        assert arguments[-1] == CONTAINER
        assert "container" in arguments and "inspect" in arguments
        assert deadline > time.monotonic()
        encoded = json.dumps(data).encode()
        budget._charge(len(encoded))
        calls.append(arguments)
        return SimpleNamespace(stdout=encoded)

    monkeypatch.setattr(module, "capture", capture)
    return expected, directory, data, calls


def snap(module, installed, *, deadline=None, budget=None):
    return module.snapshot(
        installed[0],
        deadline=time.monotonic() + 5 if deadline is None else deadline,
        budget=module.OutputBudget() if budget is None else budget,
    )


def test_unchanged_running_snapshots_match_and_remain_blocked(module, installed):
    before, after = snap(module, installed), snap(module, installed)
    module.require_same(installed[0], before, after)
    assert before.host_pid == PID and before.process_start_ticks == 12345
    assert before.container_started_at == START
    assert before.command == COMMAND and before.uid == (10001,) * 4
    assert before.capabilities == (0,) * 5 and before.no_new_privs == 1
    assert (
        before.approval_eligibility == "blocked" and before.measurement_age == "unknown"
    )
    assert len(installed[3]) == 4


@pytest.mark.parametrize(
    "field,value",
    [
        ("Id", "d" * 64),
        ("Image", "sha256:" + "d" * 64),
        ("User", "0"),
        ("RestartCount", True),
        ("RestartCount", -1),
    ],
)
def test_bad_container_identity_fails(module, installed, field, value):
    installed[2][field] = value
    with pytest.raises(module.IdentityError):
        snap(module, installed)


@pytest.mark.parametrize(
    "field,value",
    [
        ("Pid", 0),
        ("Pid", True),
        ("StartedAt", "invalid"),
        ("StartedAt", "2026-02-30T00:00:00Z"),
        ("Status", "exited"),
        ("Running", False),
        ("Paused", True),
        ("Restarting", True),
        ("Dead", True),
        ("OOMKilled", True),
        ("Error", "failure"),
    ],
)
def test_bad_state_never_becomes_valid_snapshot(module, installed, field, value):
    installed[2]["State"][field] = value
    with pytest.raises(module.IdentityError):
        snap(module, installed)


def test_ownership_mismatch_is_rejected(module, installed):
    installed[2]["Labels"][module.LABEL] = "d" * 32
    with pytest.raises(module.IdentityError):
        snap(module, installed)


@pytest.mark.parametrize(
    "name,content",
    [
        ("stat", "malformed"),
        ("stat", process_stat(state="Z")),
        ("stat", process_stat(pid=124)),
        ("status", process_status().replace("NoNewPrivs:\t1", "NoNewPrivs:\t0")),
        (
            "status",
            process_status().replace(
                "CapEff:\t" + "0" * 16, "CapEff:\t" + "0" * 15 + "1"
            ),
        ),
        ("status", process_status().replace("10001", "0")),
        ("status", process_status() + "Pid:\t123\n"),
        ("cgroup", "0::/system.slice/docker-" + "d" * 64 + ".scope\n"),
        ("cgroup", "malformed\n"),
        ("cmdline", "different-command"),
    ],
)
def test_malformed_or_unrestricted_process_is_rejected(
    module, installed, name, content
):
    (installed[1] / name).write_text(content)
    with pytest.raises(module.IdentityError):
        snap(module, installed)


@pytest.mark.parametrize(
    "failure",
    [PermissionError("private-details"), ProcessLookupError(), FileNotFoundError()],
)
def test_unavailable_process_state_cannot_mean_valid_identity(
    module, installed, monkeypatch, failure
):
    def failed(*args):
        raise failure

    monkeypatch.setattr(module, "_read", failed)
    with pytest.raises(module.IdentityError, match="^IDENTITY_UNAVAILABLE$") as error:
        snap(module, installed)
    assert "private-details" not in str(error.value)


@pytest.mark.parametrize("name", ["stat", "status", "cmdline", "cgroup"])
def test_oversize_process_files_fail_during_bounded_read(module, installed, name):
    (installed[1] / name).write_bytes(b"x" * (module._LIMITS[name] + 1))
    with pytest.raises(module.IdentityError):
        snap(module, installed)


def test_shared_budget_is_charged_and_not_refreshed(module, installed):
    budget = module.OutputBudget(4000)
    snap(module, installed, budget=budget)
    first_remaining = budget.remaining
    assert first_remaining < 4000
    while budget.remaining:
        try:
            snap(module, installed, budget=budget)
        except module.IdentityError:
            break
    assert budget.remaining < first_remaining
    with pytest.raises(module.IdentityError):
        snap(module, installed, budget=budget)


@pytest.mark.parametrize(
    "deadline", [0, -1, float("nan"), float("inf"), True, 10**1000]
)
def test_invalid_or_expired_deadline_prevents_docker_queries(
    module, installed, deadline
):
    with pytest.raises(module.IdentityError):
        snap(module, installed, deadline=deadline)
    assert installed[3] == []


def test_deadline_expiring_between_reads_invalidates_snapshot(
    module, installed, monkeypatch
):
    read = module._read
    deadline = time.monotonic() + 1

    def late(*args):
        value = read(*args)
        monkeypatch.setattr(module.time, "monotonic", lambda: deadline + 1)
        return value

    monkeypatch.setattr(module, "_read", late)
    with pytest.raises(module.IdentityError):
        snap(module, installed, deadline=deadline)


def test_process_start_change_during_snapshot_is_rejected(
    module, installed, monkeypatch
):
    read = module._read
    count = []

    def replaced(directory, name, *args):
        if name == "stat":
            count.append(name)
            if len(count) == 2:
                (installed[1] / "stat").write_text(process_stat(ticks=99999))
        return read(directory, name, *args)

    monkeypatch.setattr(module, "_read", replaced)
    with pytest.raises(module.IdentityError, match="CHANGED"):
        snap(module, installed)


def test_docker_generation_change_during_snapshot_is_rejected(
    module, installed, monkeypatch
):
    capture = module.capture
    count = []

    def changed(*args, **kwargs):
        count.append(1)
        if len(count) == 2:
            installed[2]["State"]["StartedAt"] = "2026-11-13T13:00:00Z"
        return capture(*args, **kwargs)

    monkeypatch.setattr(module, "capture", changed)
    with pytest.raises(module.IdentityError, match="WORKLOAD_CHANGED"):
        snap(module, installed)


def test_host_boot_change_invalidates_snapshot(module, installed, monkeypatch):
    boots = iter([installed[0].host_boot_id, "d" * 36])
    monkeypatch.setattr(module, "boot_id", lambda: next(boots))
    with pytest.raises(module.IdentityError, match="HOST_BOOT_CHANGED"):
        snap(module, installed)


@pytest.mark.parametrize(
    "change",
    [
        {"host_pid": 124},
        {"process_start_ticks": 99999},
        {"container_started_at": "2026-11-13T13:00:00Z"},
        {"restart_count": 1},
        {"cgroup_sha256": "d" * 64},
    ],
)
def test_changed_lifetime_cannot_match_prior_snapshot(module, installed, change):
    before, after = snap(module, installed), snap(module, installed)
    changed = replace(after, **change)
    with pytest.raises(module.IdentityError, match="WORKLOAD_CHANGED"):
        module.require_same(installed[0], before, changed)


@pytest.mark.parametrize(
    "change",
    [
        {"uid": (0,) * 4},
        {"capabilities": (False,) * 5},
        {"no_new_privs": False},
        {"command": ("different",)},
        {"approval_eligibility": "allowed"},
        {"started_monotonic": float("nan")},
        {"host_pid": True},
    ],
)
def test_comparison_independently_rejects_invalid_snapshot(module, installed, change):
    before, after = snap(module, installed), snap(module, installed)
    with pytest.raises(module.IdentityError):
        module.require_same(installed[0], before, replace(after, **change))


def test_snapshot_order_is_checked(module, installed):
    before, after = snap(module, installed), snap(module, installed)
    with pytest.raises(module.IdentityError, match="SNAPSHOT_ORDER_INVALID"):
        module.require_same(installed[0], after, before)


def test_docker_command_failure_is_explicit_and_sanitized(
    module, installed, monkeypatch
):
    def failed(*args, **kwargs):
        raise module.CaptureError("QUERY_TIMEOUT")

    monkeypatch.setattr(module, "capture", failed)
    with pytest.raises(module.IdentityError, match="^IDENTITY_UNAVAILABLE$"):
        snap(module, installed)


@pytest.mark.parametrize(
    "change",
    [
        {"container_id": "short"},
        {"image_id": "tag:latest"},
        {"owner": "invalid"},
        {"command": "shell-command"},
        {"command": ("x\0y",)},
        {"host_boot_id": "unverified"},
    ],
)
def test_invalid_expected_identity_never_queries_docker(module, installed, change):
    with pytest.raises(module.IdentityError):
        module.snapshot(
            replace(installed[0], **change),
            deadline=time.monotonic() + 1,
            budget=module.OutputBudget(),
        )
    assert installed[3] == []


def test_symlink_process_directory_is_rejected(module, installed):
    directory = installed[1]
    moved = directory.with_name("other")
    directory.rename(moved)
    directory.symlink_to(moved, target_is_directory=True)
    with pytest.raises(module.IdentityError, match="IDENTITY_UNAVAILABLE"):
        snap(module, installed)


def test_process_command_change_between_bracketed_reads_is_rejected(
    module, installed, monkeypatch
):
    read = module._read
    count = []

    def changed(directory, name, *args):
        if name == "cmdline":
            count.append(name)
            if len(count) == 2:
                (installed[1] / "cmdline").write_bytes(b"changed\0")
        return read(directory, name, *args)

    monkeypatch.setattr(module, "_read", changed)
    with pytest.raises(module.IdentityError, match="PROCESS_IDENTITY_CHANGED"):
        snap(module, installed)


def test_kernel_file_symlink_cannot_redirect_a_read(module, installed):
    path = installed[1] / "cmdline"
    target = installed[1] / "unrelated"
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(module.IdentityError, match="IDENTITY_UNAVAILABLE"):
        snap(module, installed)


def test_opt_in_acceptance_does_not_run_docker_on_import_or_usage(module, monkeypatch):
    acceptance = importlib.import_module("run_nts_identity_acceptance")

    def forbidden(*args, **kwargs):
        pytest.fail("Docker ran without explicit acceptance opt-in")

    monkeypatch.setattr(acceptance, "docker", forbidden)
    assert acceptance.main([]) == 2


def test_acceptance_cleanup_failure_cannot_report_overall_success(
    module, monkeypatch, capsys
):
    acceptance = importlib.import_module("run_nts_identity_acceptance")
    for name in (
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    def docker(*args, **kwargs):
        if args[:2] == ("context", "show"):
            return "default"
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Id": IMAGE}])
        if args[:2] == ("image", "rm"):
            raise RuntimeError("cleanup failed")
        return ""

    monkeypatch.setattr(acceptance, "docker", docker)
    monkeypatch.setattr(
        acceptance.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(acceptance, "run_case", lambda *args: None)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        acceptance.main(["--accept"])
    assert "ACCEPTANCE=PASS" not in capsys.readouterr().out


def test_no_snapshot_data_is_printed(module, installed, capsys):
    before, after = snap(module, installed), snap(module, installed)
    module.require_same(installed[0], before, after)
    assert capsys.readouterr() == ("", "")
