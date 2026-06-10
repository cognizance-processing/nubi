"""Security regression tests for B6 — GitSync.read() path-traversal guard.

GitSync.read() must only return files that live *inside* ``self.repo_dir``.
Any attempt to escape the repo (absolute paths, ``..`` segments, or paths
that resolve outside the repo dir) must raise ``AppError`` with code
``invalid_path`` and HTTP status 400 — and must never read the target file.

No network calls are made; a GitSync is pointed at a ``tmp_path`` repo dir
(mirroring the ``git_sync`` fixture in tests/test_git_sync.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.errors import AppError
from app.git.sync import GitSync


@pytest.fixture()
def git_sync(tmp_path: Path) -> GitSync:
    """Return a GitSync pointing at an isolated tmp repo dir."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    return GitSync(repo_dir=repo_dir)


def test_read_returns_in_repo_file(git_sync: GitSync) -> None:
    """A normal relative path inside the repo is read back verbatim."""
    (git_sync.repo_dir / "queries").mkdir(parents=True, exist_ok=True)
    target = git_sync.repo_dir / "queries" / "q1.sql"
    target.write_text("SELECT 1 AS n", encoding="utf-8")

    assert git_sync.read("queries/q1.sql") == "SELECT 1 AS n"


def test_read_rejects_parent_traversal(git_sync: GitSync) -> None:
    """A leading ``..`` segment is rejected before any read happens."""
    # Plant a secret one level above the repo dir to prove it stays unread.
    secret = git_sync.repo_dir.parent / "outside.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")

    with pytest.raises(AppError) as excinfo:
        git_sync.read("../outside.txt")

    assert excinfo.value.code == "invalid_path"
    assert excinfo.value.status == 400


def test_read_rejects_absolute_path(git_sync: GitSync) -> None:
    """An absolute path (e.g. /etc/passwd) is rejected."""
    with pytest.raises(AppError) as excinfo:
        git_sync.read("/etc/passwd")

    assert excinfo.value.code == "invalid_path"
    assert excinfo.value.status == 400


def test_read_rejects_embedded_traversal(git_sync: GitSync) -> None:
    """A ``..`` segment embedded mid-path that escapes the repo is rejected."""
    secret = git_sync.repo_dir.parent / "escape"
    secret.write_text("ESCAPED", encoding="utf-8")

    with pytest.raises(AppError) as excinfo:
        git_sync.read("a/../../escape")

    assert excinfo.value.code == "invalid_path"
    assert excinfo.value.status == 400


def test_read_never_reads_outside_repo(git_sync: GitSync, tmp_path: Path) -> None:
    """Guard rejects traversal even when the out-of-repo target exists."""
    outside = tmp_path / "outside.txt"
    outside.write_text("MUST NOT BE RETURNED", encoding="utf-8")

    for bad in ("../outside.txt", "/etc/passwd", "a/../../outside.txt"):
        with pytest.raises(AppError) as excinfo:
            git_sync.read(bad)
        assert excinfo.value.code == "invalid_path"
        assert excinfo.value.status == 400
