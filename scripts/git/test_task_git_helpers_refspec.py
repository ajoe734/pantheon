from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_task_helpers_fetch_dev_into_remote_tracking_ref() -> None:
    expected_dynamic = "+refs/heads/${DEV_BRANCH}:refs/remotes/origin/${DEV_BRANCH}"
    assert expected_dynamic in _read("scripts/git/task_start.sh")
    assert expected_dynamic in _read("scripts/git/task_finalize.sh")


def test_safe_pr_fetches_dev_into_remote_tracking_ref() -> None:
    content = _read("scripts/git/safe_pr.sh")
    assert "+refs/heads/dev:refs/remotes/origin/dev" in content
    assert "git fetch origin dev --quiet" not in content
