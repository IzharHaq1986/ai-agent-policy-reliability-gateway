"""Real-process artifact CLI tests using committed disposable source copies."""

import json
import os
import shutil
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluations/policy_core_v1.json"


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    shutil.copytree(
        ROOT / "src" / "gateway",
        root / "src" / "gateway",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["PYTHONPATH"] = str(root / "src")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    def git(*arguments):
        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()

    git("init")
    git("add", "--", "src")
    git(
        "-c",
        "user.name=Synthetic Test",
        "-c",
        "user.email=synthetic@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "Synthetic artifact CLI checkout",
    )
    revision = git("rev-parse", "HEAD")
    return root, environment, revision


def command(checkout, arguments, **overrides):
    root, environment, _ = checkout
    options = {
        "cwd": root,
        "env": environment,
        "capture_output": True,
        "text": True,
        "timeout": 30,
        "check": False,
    }
    options.update(overrides)
    return subprocess.run(
        [sys.executable, "-m", "gateway.evaluation_artifact_cli", *arguments],
        **options,
    )


@pytest.mark.parametrize("mismatch", [False, True])
def test_real_artifact_output(checkout, tmp_path, mismatch):
    dataset = json.loads(DATASET.read_bytes())
    if mismatch:
        dataset["scenarios"][0]["expected_result"].update(
            decision="DENY", reason_code="NOT_AUTHORIZED"
        )
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")
    original = path.read_bytes()

    completed = command(checkout, [str(path)])

    assert completed.returncode == int(mismatch)
    assert completed.stderr == ""
    artifact = json.loads(completed.stdout)
    assert artifact["code_revision"] == checkout[2]
    assert artifact["code_worktree_clean"] is True
    assert artifact["dataset_sha256"] == sha256(original).hexdigest()
    assert artifact["executed"] == 12
    assert artifact["skipped"] == 0
    assert artifact["failed"] == artifact["report"]["failed"] == int(mismatch)
    assert artifact["report"]["passed"] == 12 - int(mismatch)
    assert (
        completed.stdout
        == json.dumps(artifact, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    )
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("missing_arguments", "USAGE_ERROR"),
        ("excess_arguments", "USAGE_ERROR"),
        ("missing_file", "INPUT_ERROR"),
        ("invalid_json", "INPUT_ERROR"),
    ],
)
def test_real_usage_and_input_failure(checkout, tmp_path, case, code):
    path = tmp_path / "synthetic-private-path.json"
    arguments = []
    if case == "excess_arguments":
        arguments = ["one", "two"]
    elif case == "missing_file":
        arguments = [str(path)]
    elif case == "invalid_json":
        path.write_text("{synthetic-private-content", encoding="utf-8")
        arguments = [str(path)]

    completed = command(checkout, arguments)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == code + "\n"


def test_real_dirty_checkout_rejects_generation(checkout):
    root, _, _ = checkout
    (root / "unexpected.txt").write_text("untracked\n", encoding="utf-8")

    completed = command(checkout, [str(DATASET)])

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "CODE_IDENTITY_ERROR\n"


def test_real_closed_pipe_preserves_failure_exit(checkout):
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        completed = command(
            checkout,
            [str(DATASET)],
            capture_output=False,
            stdout=write_fd,
            stderr=subprocess.PIPE,
        )
    finally:
        os.close(write_fd)

    assert completed.returncode == 2
    assert completed.stderr == "OUTPUT_ERROR\n"
