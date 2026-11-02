"""Deterministic checks for PostgreSQL acceptance startup diagnostics."""

import importlib.util  # Load the runner without invoking its main function.
import json  # Supply controlled inspection output.
import subprocess  # Construct realistic subprocess results and failures.
from pathlib import Path  # Locate the checked-out runner.
from types import SimpleNamespace  # Provide a controlled clock and process interface.

import pytest  # Supply isolated patches and expected-failure checks.

ROOT = Path(__file__).resolve().parents[1]
CONTAINER = "owned-test-container"
SECRET = "sensitive-value-must-never-appear"


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location(
        "startup_runner_under_test",
        ROOT / "tests/support/run_postgresql_acceptance.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # Importing must not start Docker.
    return module


def configure(monkeypatch, runner, outcomes, times):
    calls = []
    sleeps = []
    results = iter(outcomes)
    clock = iter(times)

    def run(arguments, **options):
        calls.append((arguments, options))
        outcome = next(results)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(
        runner,
        "subprocess",
        SimpleNamespace(
            run=run,
            TimeoutExpired=subprocess.TimeoutExpired,
        ),
    )
    monkeypatch.setattr(
        runner,
        "time",
        SimpleNamespace(
            monotonic=lambda: next(clock),
            sleep=sleeps.append,
        ),
    )
    return calls, sleeps


def result(code=0, output=""):
    return subprocess.CompletedProcess(
        [],
        code,
        stdout=output,
        stderr=SECRET,
    )  # Even failing stderr must remain absent from diagnostic output.


def state(**changes):
    supplied = {
        "Status": "running",
        "ExitCode": 0,
        "OOMKilled": False,
        "Error": SECRET,
        "Unexpected": SECRET,
    }
    supplied.update(changes)
    return result(output=json.dumps(supplied))


def test_success_preserves_probe_command_and_silence(runner, monkeypatch, capsys):
    calls, sleeps = configure(monkeypatch, runner, [result()], [100])
    assert runner.wait_for_postgresql(CONTAINER) is None
    assert calls == [
        (
            [
                "docker",
                "exec",
                CONTAINER,
                "pg_isready",
                "-h",
                "127.0.0.1",
                "-U",
                "gateway_test_admin",
                "-d",
                "gateway_audit_test",
            ],
            {"capture_output": True, "timeout": 5},
        )
    ]
    assert sleeps == []
    assert capsys.readouterr().out == ""


def test_nonzero_probe_retains_polling_and_success(runner, monkeypatch, capsys):
    calls, sleeps = configure(monkeypatch, runner, [result(2), result()], [100, 101])
    runner.wait_for_postgresql(CONTAINER)
    assert len(calls) == 2
    assert sleeps == [0.5]
    assert capsys.readouterr().out == ""


def test_timeout_preserves_original_exception_without_retry(
    runner, monkeypatch, capsys
):
    failure = subprocess.TimeoutExpired(["private-command"], 5, output=SECRET)
    calls, sleeps = configure(monkeypatch, runner, [failure, state()], [100, 105])
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        runner.wait_for_postgresql(CONTAINER)
    assert caught.value is failure
    assert len(calls) == 2
    assert calls[1] == (
        ["docker", "inspect", "--format", "{{json .State}}", CONTAINER],
        {"capture_output": True, "text": True, "timeout": 5},
    )
    assert sleeps == []
    output = capsys.readouterr().out
    assert output.splitlines() == [
        "POSTGRESQL_STARTUP_FAILURE=probe_timeout PROBES=1 ELAPSED_SECONDS=5.00",
        "POSTGRESQL_STARTUP_INSPECTION=available STATUS=running EXIT_CODE=0 OOM=false",
    ]
    assert SECRET not in output


def test_observation_expiry_preserves_error_and_threshold(runner, monkeypatch, capsys):
    calls, sleeps = configure(
        monkeypatch, runner, [result(2), state()], [100, 140, 140]
    )
    with pytest.raises(RuntimeError, match="^Isolated PostgreSQL readiness timed out$"):
        runner.wait_for_postgresql(CONTAINER)
    assert len(calls) == 2
    assert sleeps == []
    assert "FAILURE=observation_expired" in capsys.readouterr().out


def test_probe_error_preserves_original_exception(runner, monkeypatch, capsys):
    failure = OSError(SECRET)
    calls, _ = configure(monkeypatch, runner, [failure, state()], [100, 101])
    with pytest.raises(OSError) as caught:
        runner.wait_for_postgresql(CONTAINER)
    assert caught.value is failure
    assert len(calls) == 2
    output = capsys.readouterr().out
    assert "FAILURE=probe_error" in output
    assert SECRET not in output


@pytest.mark.parametrize(
    "inspection",
    [
        result(1),
        result(output="invalid-json"),
        result(output="[]"),
        state(Status=SECRET),
        state(ExitCode=True),
        state(ExitCode=2**31),
        state(OOMKilled="false"),
        OSError(SECRET),
        subprocess.TimeoutExpired(["inspect"], 5, output=SECRET),
    ],
)
def test_unavailable_inspection_never_replaces_timeout(
    runner, monkeypatch, capsys, inspection
):
    failure = subprocess.TimeoutExpired(["probe"], 5)
    calls, _ = configure(monkeypatch, runner, [failure, inspection], [100, 105])
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        runner.wait_for_postgresql(CONTAINER)
    assert caught.value is failure
    assert len(calls) == 2
    output = capsys.readouterr().out
    assert "POSTGRESQL_STARTUP_INSPECTION=unavailable" in output
    assert SECRET not in output
    assert "=PASS" not in output


def test_output_failure_does_not_replace_original_timeout(runner, monkeypatch):
    failure = subprocess.TimeoutExpired(["probe"], 5)
    calls, _ = configure(monkeypatch, runner, [failure], [100, 105])

    def broken_print(*arguments, **options):
        raise OSError(SECRET)

    monkeypatch.setattr(runner, "print", broken_print, raising=False)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        runner.wait_for_postgresql(CONTAINER)
    assert caught.value is failure
    assert len(calls) == 1  # No retry or fallback output after emission failure.


def test_clock_failure_in_diagnostics_preserves_timeout(runner, monkeypatch):
    failure = subprocess.TimeoutExpired(["probe"], 5)
    configure(monkeypatch, runner, [failure], [100])
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        runner.wait_for_postgresql(CONTAINER)
    assert caught.value is failure  # Exhausted diagnostic clock is suppressed.


@pytest.mark.parametrize("location", ["probe", "inspection"])
def test_process_interruptions_propagate(runner, monkeypatch, location):
    interruption = KeyboardInterrupt()
    outcomes = (
        [interruption]
        if location == "probe"
        else [subprocess.TimeoutExpired(["probe"], 5), interruption]
    )
    configure(monkeypatch, runner, outcomes, [100, 105])
    with pytest.raises(KeyboardInterrupt) as caught:
        runner.wait_for_postgresql(CONTAINER)
    assert caught.value is interruption


def test_main_cleans_resources_after_real_readiness_failure(
    runner, monkeypatch, capsys
):
    failure = subprocess.TimeoutExpired(["probe"], 5)
    calls, _ = configure(
        monkeypatch,
        runner,
        [failure, state(), result()],
        [100, 105],
    )
    operations = []

    def docker(*arguments, **options):
        operations.append(arguments)
        return ""

    monkeypatch.setattr(runner, "docker", docker)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        runner.main()
    assert caught.value is failure
    assert len(calls) == 3  # Probe, diagnostic inspection, then cleanup inspection.
    created = next(args[2] for args in operations if args[:2] == ("volume", "create"))
    assert ("rm", "--force", created) in operations
    assert ("volume", "rm", created) in operations
    output = capsys.readouterr().out
    assert "CONTAINER_AND_VOLUME_CLEANUP=COMPLETE" in output
    assert "AUTHENTICATED_POSTGRESQL_ACCEPTANCE=PASS" not in output
    assert SECRET not in output
