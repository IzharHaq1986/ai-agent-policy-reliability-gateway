"""Regression tests for CLI process and output failure boundaries."""

import os
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

from gateway import evaluation_cli

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluations/policy_core_v1.json"


def environment():
    configured = os.environ.copy()
    configured["PYTHONPATH"] = str(ROOT / "src")
    configured["PYTHONDONTWRITEBYTECODE"] = "1"
    return configured


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("missing_arguments", "USAGE_ERROR"),
        ("excess_arguments", "USAGE_ERROR"),
        ("missing_file", "INPUT_ERROR"),
        ("invalid_json", "INPUT_ERROR"),
    ],
)
def test_real_process_failure(tmp_path, case, code):
    private_path = tmp_path / "synthetic-private-path.json"
    arguments = []
    if case == "excess_arguments":
        arguments = ["one", "two"]
    elif case == "missing_file":
        arguments = [str(private_path)]
    elif case == "invalid_json":
        private_path.write_text("{synthetic-private-content")
        arguments = [str(private_path)]

    completed = subprocess.run(
        [sys.executable, "-m", "gateway.evaluation_cli", *arguments],
        cwd=ROOT,
        env=environment(),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == code + "\n"


def test_closed_pipe_preserves_exit_code_and_generic_error():
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "gateway.evaluation_cli", str(DATASET)],
            cwd=ROOT,
            env=environment(),
            stdout=write_fd,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )
    finally:
        os.close(write_fd)

    assert completed.returncode == 2
    assert completed.stderr == "OUTPUT_ERROR\n"


@pytest.mark.parametrize("failure", ["write", "flush"])
def test_output_failure_returns_generic_error(monkeypatch, capsys, failure):
    class FailingOutput(StringIO):
        def write(self, text):
            if failure == "write":
                raise OSError("synthetic-private-output-error")
            return super().write(text)

        def flush(self):
            if failure == "flush":
                raise OSError("synthetic-private-output-error")
            return super().flush()

    output = FailingOutput()
    # StringIO has no OS descriptor; recovery must also tolerate that case.
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", output)
        status = evaluation_cli.main([str(DATASET)])

    captured = capsys.readouterr()
    assert status == 2
    assert captured.out == ""
    assert captured.err == "OUTPUT_ERROR\n"
    assert "synthetic-private-output-error" not in output.getvalue()
