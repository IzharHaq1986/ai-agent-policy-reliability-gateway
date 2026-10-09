"""Acceptance tests for artifact command output and failure boundaries."""

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

from gateway import evaluation_artifact as builder
from gateway import evaluation_artifact_cli as cli
from gateway import evaluation_cli

DATASET = Path(__file__).resolve().parents[1] / "evaluations/policy_core_v1.json"


@pytest.fixture
def artifact(monkeypatch):
    monkeypatch.setattr(builder, "_verified_revision", lambda: "a" * 40)
    return builder.generate_evaluation_artifact(DATASET)


@pytest.mark.parametrize("failed", [0, 1])
def test_exact_output_and_single_generation(monkeypatch, capsys, artifact, failed):
    artifact["failed"] = failed
    calls = []

    def generate(path):
        calls.append(path)
        return artifact

    monkeypatch.setattr(cli, "generate_evaluation_artifact", generate)

    assert cli.main(["synthetic-dataset.json"]) == failed
    captured = capsys.readouterr()
    assert captured.err == ""
    assert (
        captured.out
        == json.dumps(artifact, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    )
    assert calls == ["synthetic-dataset.json"]


@pytest.mark.parametrize("arguments", [[], ["one", "two"]])
def test_usage_prevents_generation(monkeypatch, capsys, arguments):
    def forbidden(path):
        pytest.fail("Invalid usage reached artifact generation")

    monkeypatch.setattr(cli, "generate_evaluation_artifact", forbidden)
    assert cli.main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "USAGE_ERROR\n"


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (builder.ArtifactGenerationError("INPUT_ERROR"), "INPUT_ERROR"),
        (builder.ArtifactGenerationError("CODE_IDENTITY_ERROR"), "CODE_IDENTITY_ERROR"),
        (builder.ArtifactGenerationError("EVALUATION_ERROR"), "EVALUATION_ERROR"),
        (
            builder.ArtifactGenerationError("synthetic-private-error"),
            "EVALUATION_ERROR",
        ),
        (RuntimeError("synthetic-private-error"), "EVALUATION_ERROR"),
    ],
)
def test_generation_failure_is_generic(monkeypatch, capsys, failure, code):
    def fail(path):
        raise failure

    monkeypatch.setattr(cli, "generate_evaluation_artifact", fail)
    assert cli.main(["synthetic-private-path"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == code + "\n"


@pytest.mark.parametrize("value", [object(), float("nan")])
def test_serialization_failure_emits_no_artifact(monkeypatch, capsys, artifact, value):
    artifact["limitations"] = value
    monkeypatch.setattr(cli, "generate_evaluation_artifact", lambda path: artifact)

    assert cli.main(["dataset.json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "SERIALIZATION_ERROR\n"


@pytest.mark.parametrize("failure", ["write", "flush", "short"])
def test_delivery_failure_is_generic(monkeypatch, capsys, artifact, failure):
    class FailingOutput(StringIO):
        def write(self, text):
            if failure == "write":
                raise OSError("synthetic-private-output-error")
            if failure == "short":
                return super().write(text[:-1])
            return super().write(text)

        def flush(self):
            if failure == "flush":
                raise OSError("synthetic-private-output-error")
            return super().flush()

    output = FailingOutput()
    monkeypatch.setattr(cli, "generate_evaluation_artifact", lambda path: artifact)
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", output)
        status = cli.main(["dataset.json"])

    captured = capsys.readouterr()
    assert status == 2
    assert captured.out == ""
    assert captured.err == "OUTPUT_ERROR\n"
    assert "synthetic-private-output-error" not in output.getvalue()


def test_existing_evaluation_cli_rejects_short_write(monkeypatch, capsys):
    class ShortOutput(StringIO):
        def write(self, text):
            return super().write(text[:-1])

    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", ShortOutput())
        status = evaluation_cli.main([str(DATASET)])

    captured = capsys.readouterr()
    assert status == 2
    assert captured.out == ""
    assert captured.err == "OUTPUT_ERROR\n"


def test_unavailable_stderr_retains_failure_status(monkeypatch):
    class UnavailableError(StringIO):
        def write(self, text):
            raise OSError("synthetic-private-stderr-error")

    with monkeypatch.context() as patch:
        patch.setattr(sys, "stderr", UnavailableError())
        assert cli.main([]) == 2
