"""Acceptance tests for evaluation artifact generation."""

import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import pytest

from gateway import evaluation_artifact as artifact
from gateway.evaluation import evaluate_dataset

DATASET = Path(__file__).resolve().parents[1] / "evaluations/policy_core_v1.json"
REVISION = "a" * 40


@pytest.fixture(autouse=True)
def trusted_context(monkeypatch):
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 9, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(artifact, "datetime", FixedClock)
    monkeypatch.setattr(artifact, "_verified_revision", lambda: REVISION)


def test_exact_artifact_and_input_preservation():
    original = DATASET.read_bytes()
    expected_report = evaluate_dataset(json.loads(original))

    result = artifact.generate_evaluation_artifact(DATASET)

    assert set(result) == {
        "schema_version",
        "evaluation_type",
        "dataset_schema_version",
        "dataset_sha256",
        "code_revision",
        "code_worktree_clean",
        "occurred_at",
        "executed",
        "skipped",
        "failed",
        "report",
        "limitations",
    }
    assert result["schema_version"] == 1
    assert result["evaluation_type"] == "deterministic_policy_model_free"
    assert result["dataset_schema_version"] == 1
    assert result["dataset_sha256"] == sha256(original).hexdigest()
    assert result["code_revision"] == REVISION
    assert result["code_worktree_clean"] is True
    assert result["occurred_at"] == "2026-10-09T12:00:00Z"
    assert result["executed"] == expected_report["total"] == 12
    assert result["skipped"] == 0
    assert result["failed"] == 0
    assert result["report"] == expected_report
    assert "no live-model evaluation" in result["limitations"]
    assert "atomic code snapshot" in result["limitations"]
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    assert artifact.generate_evaluation_artifact(DATASET) == result
    assert DATASET.read_bytes() == original


def test_hash_identifies_bytes_not_only_parsed_content(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    original = DATASET.read_bytes()
    first.write_bytes(original)
    second.write_bytes(original + b"\n")

    left = artifact.generate_evaluation_artifact(first)
    right = artifact.generate_evaluation_artifact(second)

    assert left["report"] == right["report"]
    assert left["dataset_sha256"] != right["dataset_sha256"]


def test_captured_bytes_are_loaded_once(tmp_path, monkeypatch):
    path = tmp_path / "dataset.json"
    original = DATASET.read_bytes()
    path.write_bytes(original)
    loader = artifact.load_evaluation_dataset
    calls = []

    def load_once(supplied_path):
        calls.append(supplied_path)
        captured = loader(supplied_path)
        path.write_bytes(b"invalid replacement")
        return captured

    monkeypatch.setattr(artifact, "load_evaluation_dataset", load_once)
    result = artifact.generate_evaluation_artifact(path)

    assert calls == [path]
    assert result["dataset_sha256"] == sha256(original).hexdigest()
    assert result["report"] == evaluate_dataset(json.loads(original))


def test_mismatch_is_recorded_as_failure(tmp_path):
    dataset = json.loads(DATASET.read_bytes())
    dataset["scenarios"][0]["expected_result"].update(
        decision="DENY", reason_code="NOT_AUTHORIZED"
    )
    path = tmp_path / "mismatch.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")

    result = artifact.generate_evaluation_artifact(path)

    assert result["executed"] == 12
    assert result["skipped"] == 0
    assert result["failed"] == result["report"]["failed"] == 1


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b"\xff",
        b'{"schema_version":1,"schema_version":1,"scenarios":[]}',
        b'{"schema_version":NaN,"scenarios":[]}',
        b"{}",
    ],
)
def test_invalid_input_returns_only_generic_failure(tmp_path, body):
    path = tmp_path / "synthetic-private-path.json"
    path.write_bytes(body)

    with pytest.raises(artifact.ArtifactGenerationError) as caught:
        artifact.generate_evaluation_artifact(path)

    assert str(caught.value) == "INPUT_ERROR"


def test_evaluator_failure_returns_generic_error(monkeypatch):
    def fail(dataset):
        raise RuntimeError("synthetic-private-evaluator-error")

    monkeypatch.setattr(artifact, "evaluate_dataset", fail)

    with pytest.raises(artifact.ArtifactGenerationError) as caught:
        artifact.generate_evaluation_artifact(DATASET)

    assert str(caught.value) == "EVALUATION_ERROR"


def test_changed_revision_prevents_artifact(monkeypatch):
    revisions = iter([REVISION, "b" * 40])
    monkeypatch.setattr(artifact, "_verified_revision", lambda: next(revisions))

    with pytest.raises(artifact.ArtifactGenerationError) as caught:
        artifact.generate_evaluation_artifact(DATASET)

    assert str(caught.value) == "CODE_IDENTITY_ERROR"


def test_initial_identity_failure_prevents_input_loading(monkeypatch):
    def fail_identity():
        raise artifact.ArtifactGenerationError("CODE_IDENTITY_ERROR")

    def forbidden_loader(path):
        pytest.fail("Input loaded before code identity was established")

    monkeypatch.setattr(artifact, "_verified_revision", fail_identity)
    monkeypatch.setattr(artifact, "load_evaluation_dataset", forbidden_loader)

    with pytest.raises(artifact.ArtifactGenerationError) as caught:
        artifact.generate_evaluation_artifact(DATASET)

    assert str(caught.value) == "CODE_IDENTITY_ERROR"
