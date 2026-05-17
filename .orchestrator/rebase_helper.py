"""Auto-handle empty commits in worker git pull --rebase flows.

When a worker runs `git pull --rebase` and one or more commits are already
applied on the target branch, git stops the rebase with an "empty" commit
state and waits for user input.  This module resolves that state without
human intervention by detecting and skipping empty commits, or aborting when
a real merge conflict is present.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

RebaseResult = dict[str, Any]


def _git_dir(repo_path: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        git_dir = Path(result.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = repo_path / git_dir
        return git_dir

    dotgit = repo_path / ".git"
    if dotgit.is_file():
        try:
            content = dotgit.read_text(encoding="utf-8").strip()
        except OSError:
            return dotgit
        prefix = "gitdir:"
        if content.startswith(prefix):
            git_dir = Path(content[len(prefix) :].strip())
            if not git_dir.is_absolute():
                git_dir = repo_path / git_dir
            return git_dir
    return dotgit


def _rebase_in_progress(repo_path: Path) -> bool:
    git_dir = _git_dir(repo_path)
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def _has_conflicts(repo_path: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        xy = line[:2]
        if "U" in xy or xy in ("AA", "DD"):
            return True
    return False


def _nothing_staged(repo_path: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "diff", "--cached", "--quiet"],
        capture_output=True,
    )
    return result.returncode == 0


def _abort(repo_path: Path) -> None:
    subprocess.run(
        ["git", "-C", str(repo_path), "rebase", "--abort"],
        capture_output=True,
        text=True,
    )


def continue_or_skip_empty(repo_path: str | Path) -> RebaseResult:
    """Continue an in-progress rebase, auto-skipping empty commits.

    Iterates until the rebase finishes or a real (non-empty) conflict is
    detected.  Safe to call when no rebase is in progress; returns
    ``action="no_rebase"`` immediately in that case.

    Returns a RebaseResult dict:
      action  : "continued" | "skipped" | "aborted_with_conflict" | "no_rebase"
      skipped : int   — number of empty commits auto-skipped
      message : str   — human-readable summary
    """
    repo_path = Path(repo_path)

    if not _rebase_in_progress(repo_path):
        return {"action": "no_rebase", "skipped": 0, "message": "No rebase in progress."}

    skipped = 0
    max_steps = 200  # safety ceiling against infinite loops

    for _ in range(max_steps):
        if not _rebase_in_progress(repo_path):
            action = "skipped" if skipped > 0 else "continued"
            return {
                "action": action,
                "skipped": skipped,
                "message": f"Rebase complete; {skipped} empty commit(s) skipped.",
            }

        if _has_conflicts(repo_path):
            _abort(repo_path)
            return {
                "action": "aborted_with_conflict",
                "skipped": skipped,
                "message": "Conflict detected; rebase aborted.",
            }

        if _nothing_staged(repo_path):
            result = subprocess.run(
                ["git", "-C", str(repo_path), "rebase", "--skip"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                _abort(repo_path)
                return {
                    "action": "aborted_with_conflict",
                    "skipped": skipped,
                    "message": (result.stderr or result.stdout or "rebase --skip failed").strip(),
                }
            skipped += 1
            continue

        env = {**os.environ, "GIT_EDITOR": "true"}
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rebase", "--continue"],
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 0:
            _abort(repo_path)
            return {
                "action": "aborted_with_conflict",
                "skipped": skipped,
                "message": (result.stderr or result.stdout or "rebase --continue failed").strip(),
            }

    _abort(repo_path)
    return {
        "action": "aborted_with_conflict",
        "skipped": skipped,
        "message": f"Safety limit ({max_steps} steps) reached; rebase aborted.",
    }
