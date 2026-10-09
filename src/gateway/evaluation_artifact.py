"""Traceable artifacts for deterministic, model-free policy evaluations."""

import os
import re
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, TypedDict

from gateway.evaluation import EvaluationDataError, EvaluationReport, evaluate_dataset
from gateway.evaluation_input import load_evaluation_dataset

_REVISION = re.compile(r"[0-9a-f]{40}")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class ArtifactGenerationError(RuntimeError):
    """Artifact generation could not establish its required evidence."""


def _verified_revision() -> str:
    """Observe a clean revision in the repository containing this module."""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }

    def git(*arguments: str) -> str:
        try:
            completed = subprocess.run(
                ["git", *arguments],
                cwd=_REPOSITORY_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError):
            raise ArtifactGenerationError("CODE_IDENTITY_ERROR") from None
        return completed.stdout

    root = git("rev-parse", "--show-toplevel").strip()
    if Path(root).resolve() != _REPOSITORY_ROOT:
        raise ArtifactGenerationError("CODE_IDENTITY_ERROR")

    revision = git("rev-parse", "--verify", "HEAD^{commit}").strip()
    if _REVISION.fullmatch(revision) is None:
        raise ArtifactGenerationError("CODE_IDENTITY_ERROR")

    status = git(
        "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none"
    )
    if status:
        raise ArtifactGenerationError("CODE_IDENTITY_ERROR")
    return revision


_LIMITATIONS = (
    "Synthetic deterministic policy evidence only; no live-model evaluation "
    "or production safety guarantee. Hashes and Git observations do not "
    "establish authenticity or an atomic code snapshot."
)


class EvaluationArtifact(TypedDict):
    schema_version: Literal[1]
    evaluation_type: Literal["deterministic_policy_model_free"]
    dataset_schema_version: Literal[1]
    dataset_sha256: str
    code_revision: str
    code_worktree_clean: Literal[True]
    occurred_at: str
    executed: int
    skipped: Literal[0]
    failed: int
    report: EvaluationReport
    limitations: str


def generate_evaluation_artifact(dataset_path: str | Path) -> EvaluationArtifact:
    """Evaluate captured bytes and return evidence without writing a file.

    Git identity is observed before and after evaluation. These checks
    cannot detect a hostile process changing and restoring code between
    observations, and do not identify installed dependency versions.
    """
    revision = _verified_revision()
    occurred_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        body, dataset = load_evaluation_dataset(dataset_path)
    except (OSError, ValueError, RecursionError):
        raise ArtifactGenerationError("INPUT_ERROR") from None

    try:
        report = evaluate_dataset(dataset)
    except EvaluationDataError:
        raise ArtifactGenerationError("INPUT_ERROR") from None
    except Exception:
        raise ArtifactGenerationError("EVALUATION_ERROR") from None

    if _verified_revision() != revision:
        raise ArtifactGenerationError("CODE_IDENTITY_ERROR")

    return {
        "schema_version": 1,
        "evaluation_type": "deterministic_policy_model_free",
        "dataset_schema_version": 1,
        "dataset_sha256": sha256(body).hexdigest(),
        "code_revision": revision,
        "code_worktree_clean": True,
        "occurred_at": occurred_at,
        "executed": report["total"],
        "skipped": 0,
        "failed": report["failed"],
        "report": report,
        "limitations": _LIMITATIONS,
    }
