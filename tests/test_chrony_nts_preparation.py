"""Preparation must prove isolation without running a live time experiment."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "sha256:" + "a" * 64


@pytest.fixture
def runner(monkeypatch):
    # Support scripts share the existing Docker helper without starting it on import.
    monkeypatch.syspath_prepend(str(ROOT / "tests/support"))
    spec = importlib.util.spec_from_file_location(
        "nts_preparation", ROOT / "tests/support/prepare_chrony_nts_experiment.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def metadata(runner):
    checksum = hashlib.sha256(runner.CONFIG.encode()).hexdigest()
    return (
        "[VERSION]\nchronyd (chrony) version 4.5 (+NTP +NTS)\n[/VERSION]\n"
        "[CONFIG]\nserver time.cloudflare.com iburst nts\n[/CONFIG]\n"
        "[PACKAGES]\nchrony\t4.5-1ubuntu4.2\nca-certificates\t1\n"
        "libgnutls30t64:amd64\t1\n[/PACKAGES]\n"
        f"[HASH]\n{checksum} /experiment/chrony.conf\n[/HASH]\n"
        "[PROCESS]\n10001\n10001\nCapEff:\t0000000000000000\n"
        "NoNewPrivs:\t1\n[/PROCESS]\n"
    )


def fake_docker(monkeypatch, runner, *, logs=None, unsafe=False, cleanup_error=False):
    calls = []

    def docker(*arguments, **options):
        calls.append((arguments, options))
        if arguments[0] == "build":
            Path(arguments[arguments.index("--iidfile") + 1]).write_text(IMAGE)
        elif arguments[:2] == ("image", "inspect"):
            return json.dumps(
                [
                    {
                        "Id": IMAGE,
                        "Architecture": "amd64",
                        "Os": "linux",
                        "Config": {
                            "User": "10001:10001",
                            "Entrypoint": runner.ENTRYPOINT,
                        },
                    }
                ]
            )
        elif arguments[0] == "inspect":
            return json.dumps(
                [
                    {
                        "HostConfig": {
                            "NetworkMode": "bridge" if unsafe else "none",
                            "ReadonlyRootfs": True,
                            "CapDrop": ["ALL"],
                            "Privileged": False,
                            "NanoCpus": 1000000000,
                            "Memory": 268435456,
                            "PidsLimit": 64,
                        }
                    }
                ]
            )
        elif arguments[0] == "wait":
            return "0\n"
        elif arguments[0] == "logs":
            return metadata(runner) if logs is None else logs
        elif arguments[0] == "rm" and cleanup_error:
            raise RuntimeError("cleanup failed")
        return ""

    monkeypatch.setattr(runner, "docker", docker)
    return calls


def test_preparation_records_image_and_remains_blocked(monkeypatch, runner):
    calls = fake_docker(monkeypatch, runner)
    report = runner.prepare()
    assert report["image_id"] == IMAGE
    assert report["approval_eligibility"] == "blocked"
    assert report["measurement_age"] == "unknown"
    assert report["live_nts_measurement"] == "not_run"
    assert report["owned_container_cleanup"] == "complete"
    create = next(args for args, _ in calls if args[0] == "create")
    assert create[create.index("--network") + 1] == "none"
    assert create[create.index("--entrypoint") + 1] == "/bin/sh"
    assert "--privileged" not in create
    assert calls[-1][0][:2] == ("rm", "--force")
    assert (
        next(options for args, options in calls if args[0] == "wait")["timeout"] == 20
    )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("version 4.5", "version 4.6"),
        ("+NTS", "-NTS"),
        ("4.5-1ubuntu4.2", "4.5-other"),
        ("ca-certificates\t1", "unrelated\t1"),
        ("libgnutls30t64:amd64\t1", "other-tls\t1"),
        ("10001\n10001", "0\n0"),
        ("CapEff:\t0000000000000000", "CapEff:\t0000000000000001"),
        ("NoNewPrivs:\t1", "NoNewPrivs:\t0"),
        ("[CONFIG]", "[MISSING]"),
        ("/experiment/chrony.conf\n[/HASH]", "/other.conf\n[/HASH]"),
    ],
)
def test_bad_evidence_fails_and_cleans_container(monkeypatch, runner, old, new):
    calls = fake_docker(monkeypatch, runner, logs=metadata(runner).replace(old, new))
    with pytest.raises((RuntimeError, ValueError, KeyError)):
        runner.prepare()
    assert calls[-1][0][:2] == ("rm", "--force")


def test_unsafe_container_never_starts(monkeypatch, runner):
    calls = fake_docker(monkeypatch, runner, unsafe=True)
    with pytest.raises(RuntimeError, match="isolation"):
        runner.prepare()
    assert not any(args[0] == "start" for args, _ in calls)
    assert calls[-1][0][0] == "rm"


def test_cleanup_failure_cannot_report_success(monkeypatch, runner, capsys):
    fake_docker(monkeypatch, runner, cleanup_error=True)
    assert runner.main(["--prepare"]) == 2
    output = capsys.readouterr()
    assert "PASS" not in output.out
    assert "CHRONY_NTS_PREPARATION=FAILED" in output.err


def test_no_opt_in_does_not_call_docker(monkeypatch, runner):
    calls = fake_docker(monkeypatch, runner)
    assert runner.main([]) == 2
    assert calls == []


def test_build_failure_never_creates_container(monkeypatch, runner):
    calls = []

    def unavailable(*arguments, **options):
        calls.append(arguments)
        raise RuntimeError("build unavailable")

    monkeypatch.setattr(runner, "docker", unavailable)
    assert runner.main(["--prepare"]) == 2
    assert len(calls) == 1 and calls[0][0] == "build"
