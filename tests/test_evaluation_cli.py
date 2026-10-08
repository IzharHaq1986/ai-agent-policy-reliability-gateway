"""Acceptance tests for the bounded policy evaluation command."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gateway import evaluation, evaluation_cli
from gateway.evaluation import evaluate_dataset

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluations/policy_core_v1.json"


def dataset():
    return json.loads(DATASET.read_text())


def test_success_report_is_deterministic_and_preserves_file(capsys):
    original = DATASET.read_bytes()
    expected = evaluate_dataset(dataset())
    assert evaluation_cli.main([str(DATASET)]) == 0
    first = capsys.readouterr()
    assert first.err == ""
    assert json.loads(first.out) == expected
    assert (
        first.out
        == json.dumps(expected, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    )
    assert evaluation_cli.main([str(DATASET)]) == 0
    assert capsys.readouterr() == first
    assert DATASET.read_bytes() == original


def test_mismatch_returns_one_with_report(tmp_path, capsys):
    supplied = dataset()
    supplied["scenarios"][0]["expected_result"].update(
        decision="DENY", reason_code="NOT_AUTHORIZED"
    )
    path = tmp_path / "mismatch.json"
    path.write_text(json.dumps(supplied))
    original = path.read_bytes()
    assert evaluation_cli.main([str(path)]) == 1
    output = capsys.readouterr()
    assert output.err == ""
    report = json.loads(output.out)
    assert report["passed"] == 11
    assert report["failed"] == 1
    assert report["pass_rate"] == 11 / 12
    assert path.read_bytes() == original


@pytest.mark.parametrize("arguments", [[], ["one", "two"]])
def test_usage_failure(arguments, capsys):
    assert evaluation_cli.main(arguments) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "USAGE_ERROR\n"


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"{",
        b"\xff",
        b'{"schema_version":1,"schema_version":1,"scenarios":[]}',
        b'{"schema_version":1,"scenarios":[],"extra":{"x":1,"x":2}}',
        b'{"schema_version":NaN,"scenarios":[]}',
        b'{"schema_version":Infinity,"scenarios":[]}',
        b'{"schema_version":-Infinity,"scenarios":[]}',
        b"{}",
        b'{"schema_version":1,"scenarios":[]}',
        b"[" * 2000 + b"]" * 2000,
    ],
)
def test_invalid_input_never_reaches_policy(tmp_path, capsys, monkeypatch, body):
    path = tmp_path / "private-input.json"
    path.write_bytes(body)

    def prohibited(*args, **kwargs):
        raise AssertionError("Invalid JSON reached evaluation")

    monkeypatch.setattr(evaluation, "evaluate_request", prohibited)
    assert evaluation_cli.main([str(path)]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "INPUT_ERROR\n"
    assert path.read_bytes() == body


@pytest.mark.parametrize("kind", ["missing", "directory", "permission"])
def test_unreadable_input(tmp_path, capsys, monkeypatch, kind):
    path = tmp_path / "private-path.json"
    if kind == "directory":
        path.mkdir()
    elif kind == "permission":

        def denied(*args, **kwargs):
            raise PermissionError("synthetic-private-error")

        monkeypatch.setattr(Path, "open", denied)
    assert evaluation_cli.main([str(path)]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "INPUT_ERROR\n"


@pytest.mark.parametrize("extra_bytes", [0, 1])
def test_size_boundary(tmp_path, capsys, extra_bytes):
    encoded = DATASET.read_bytes()
    limit = evaluation_cli._MAX_DATASET_BYTES
    body = encoded + b" " * (limit - len(encoded) + extra_bytes)
    path = tmp_path / "bounded.json"
    path.write_bytes(body)
    assert evaluation_cli.main([str(path)]) == (2 if extra_bytes else 0)
    output = capsys.readouterr()
    if extra_bytes:
        assert output.out == ""
        assert output.err == "INPUT_ERROR\n"
    else:
        assert json.loads(output.out)["failed"] == 0
        assert output.err == ""


def test_unexpected_failure_does_not_expose_details(capsys, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic-private-credential")

    monkeypatch.setattr(evaluation_cli, "evaluate_dataset", fail)
    assert evaluation_cli.main([str(DATASET)]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "EVALUATION_ERROR\n"


@pytest.mark.parametrize("mismatch", [False, True])
def test_module_execution(tmp_path, mismatch):
    supplied = dataset()
    if mismatch:
        supplied["scenarios"][0]["expected_result"].update(
            decision="DENY", reason_code="NOT_AUTHORIZED"
        )
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(supplied))
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-m", "gateway.evaluation_cli", str(path)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == int(mismatch)
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == evaluate_dataset(supplied)
