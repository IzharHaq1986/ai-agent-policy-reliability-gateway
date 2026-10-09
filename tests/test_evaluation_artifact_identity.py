"""Acceptance tests for Git observations used by evaluation artifacts."""

import os
import subprocess

import pytest

from gateway import evaluation_artifact as artifact


@pytest.fixture
def repository(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    root.mkdir()
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }

    def git(*arguments):
        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()

    git("init")
    (root / "source.txt").write_text("baseline\n", encoding="utf-8")
    (root / ".gitignore").write_text("/project_state.md\n/venv/\n", encoding="utf-8")
    git("add", "--", "source.txt", ".gitignore")
    git(
        "-c",
        "user.name=Synthetic Test",
        "-c",
        "user.email=synthetic@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "Synthetic identity baseline",
    )
    monkeypatch.setattr(artifact, "_REPOSITORY_ROOT", root.resolve())
    return root, git


def test_clean_repository_with_ignored_local_files(repository, monkeypatch):
    root, git = repository
    expected = git("rev-parse", "HEAD")
    (root / "project_state.md").write_text("local planning\n", encoding="utf-8")
    (root / "venv").mkdir()
    (root / "venv" / "local.txt").write_text("local environment\n", encoding="utf-8")
    # Ambient Git variables must not redirect the identity observation.
    monkeypatch.setenv("GIT_DIR", str(root / "nonexistent-git-directory"))
    monkeypatch.setenv("GIT_WORK_TREE", str(root / "nonexistent-worktree"))

    assert artifact._verified_revision() == expected
    assert git("status", "--porcelain") == ""


@pytest.mark.parametrize("change", ["modified", "staged", "untracked", "deleted"])
def test_changed_repository_is_rejected(repository, change):
    root, git = repository
    if change in {"modified", "staged"}:
        (root / "source.txt").write_text("changed\n", encoding="utf-8")
        if change == "staged":
            git("add", "--", "source.txt")
    elif change == "untracked":
        (root / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
    else:
        (root / "source.txt").unlink()

    with pytest.raises(artifact.ArtifactGenerationError) as caught:
        artifact._verified_revision()

    assert str(caught.value) == "CODE_IDENTITY_ERROR"


@pytest.mark.parametrize("failure", ["missing_git", "timeout", "command_failure"])
def test_git_failure_returns_generic_error(repository, monkeypatch, failure):
    def fail(*args, **kwargs):
        if failure == "missing_git":
            raise FileNotFoundError("synthetic-private-git-path")
        if failure == "timeout":
            raise subprocess.TimeoutExpired("synthetic-private-command", 5)
        raise subprocess.CalledProcessError(
            128, "synthetic-private-command", stderr="synthetic-private-error"
        )

    monkeypatch.setattr(artifact.subprocess, "run", fail)

    with pytest.raises(artifact.ArtifactGenerationError) as caught:
        artifact._verified_revision()

    assert str(caught.value) == "CODE_IDENTITY_ERROR"


def test_repository_root_mismatch_is_rejected(repository, monkeypatch):
    root, _ = repository
    nested = root / "nested"
    nested.mkdir()
    monkeypatch.setattr(artifact, "_REPOSITORY_ROOT", nested.resolve())

    with pytest.raises(artifact.ArtifactGenerationError) as caught:
        artifact._verified_revision()

    assert str(caught.value) == "CODE_IDENTITY_ERROR"
