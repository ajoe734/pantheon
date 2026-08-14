#!/usr/bin/env python3
"""Fail-closed guard for retired one-shot canonical writers.

Legacy dispatchers may still be exercised against isolated fixture directories,
but they are not protocol-v1 writers and must never target a Pantheon git
checkout or the status root selected for a live process.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


CANONICAL_RELATIVE_PATHS = (
    Path("ai-status.json"),
    Path("ai-activity-log.jsonl"),
    Path(".orchestrator/state.json"),
    Path(".orchestrator/approval-queue.json"),
)


def _normalized(path: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _matches_canonical_root(target: Path, root: Path) -> bool:
    normalized_root = _normalized(root)
    resolved_root = normalized_root.resolve()
    candidates = {
        _normalized(normalized_root / relative)
        for relative in CANONICAL_RELATIVE_PATHS
    }
    candidates.update(
        (resolved_root / relative).resolve()
        for relative in CANONICAL_RELATIVE_PATHS
    )
    return target in candidates or target.resolve() in candidates


def _containing_git_root(target: Path) -> Path | None:
    cursor = target.parent
    while not cursor.exists() and cursor != cursor.parent:
        cursor = cursor.parent
    result = subprocess.run(
        ["git", "-C", str(cursor), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return _normalized(result.stdout.strip())


def assert_isolated_legacy_write_target(
    path: str | Path,
    *,
    tool: str,
) -> None:
    """Reject a legacy write whenever its target is canonical runtime state."""

    target = _normalized(path)
    configured_root = str(os.environ.get("PANTHEON_STATUS_ROOT") or "").strip()
    if configured_root and _matches_canonical_root(target, _normalized(configured_root)):
        # PANTHEON_ALLOW_ISOLATED_LEGACY_WRITES cannot authorize writes to the configured
        # PANTHEON_STATUS_ROOT. For isolated test fixtures, use PANTHEON_ALLOW_ISOLATED_TEST_WRITES instead.
        isolated_override = (
            os.environ.get("PANTHEON_ALLOW_ISOLATED_TEST_WRITES") == "1"
            and _containing_git_root(target) is None
        )
        if not isolated_override:
            raise RuntimeError(
                f"{tool} is a retired protocol-external writer and cannot mutate "
                f"PANTHEON_STATUS_ROOT canonical state: {target}"
            )
    git_root = _containing_git_root(target)
    if git_root is not None and _matches_canonical_root(target, git_root):
        raise RuntimeError(
            f"{tool} is a retired protocol-external writer and cannot mutate "
            f"canonical state in a git checkout: {target}"
        )


__all__ = ["assert_isolated_legacy_write_target"]
