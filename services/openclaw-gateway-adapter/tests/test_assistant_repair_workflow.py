from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


ADAPTER_DIR = Path(__file__).resolve().parents[1]
if str(ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTER_DIR))

from assistant_command_policy import AssistantCommandPolicy  # noqa: E402
from assistant_repair_workflow import (  # noqa: E402
    AssistantRepairWorkflow,
    AssistantRepairWorkflowError,
)


TASK_ID = "ASST-KERNEL-007"


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _init_task_worktree(tmp_path: Path, *, task_id: str = TASK_ID) -> tuple[Path, Path]:
    remote = tmp_path / "origin.git"
    worktree_root = tmp_path / "worktrees"
    worktree = worktree_root / f"task-{task_id}"
    _git(tmp_path, "init", "--bare", remote.as_posix())
    worktree.mkdir(parents=True)
    _git(worktree, "init")
    _git(worktree, "config", "user.email", "assistant@example.invalid")
    _git(worktree, "config", "user.name", "Assistant Test")
    (worktree / "README.md").write_text("# test\n", encoding="utf-8")
    _git(worktree, "add", "README.md")
    _git(worktree, "commit", "-m", "initial")
    _git(worktree, "branch", "-M", f"task/{task_id}")
    _git(worktree, "remote", "add", "origin", remote.as_posix())
    return worktree_root, worktree


def _workflow(worktree_root: Path) -> AssistantRepairWorkflow:
    return AssistantRepairWorkflow(
        {
            "PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT": worktree_root.as_posix(),
        },
        pr_lookup=lambda _worktree, _branch: None,
    )


def test_clean_task_branch_records_workflow_metadata(tmp_path: Path) -> None:
    worktree_root, worktree = _init_task_worktree(tmp_path)
    snapshot = _workflow(worktree_root).validate(
        {
            "task_id": TASK_ID,
            "task_worktree": worktree.as_posix(),
            "declared_scope": ["services/openclaw-gateway-adapter"],
        }
    )

    payload = snapshot.to_dict()
    assert payload["branch"] == f"task/{TASK_ID}"
    assert payload["expected_branch"] == f"task/{TASK_ID}"
    assert payload["remote"] == "origin"
    assert payload["remote_url"].endswith("origin.git")
    assert payload["merge_target"] == "dev"
    assert len(payload["validation_commit"]) == 40
    assert payload["clean"] is True
    assert payload["declared_scope"] == ["services/openclaw-gateway-adapter"]
    assert payload["pull_request"] is None


def test_dirty_unrelated_worktree_is_rejected(tmp_path: Path) -> None:
    worktree_root, worktree = _init_task_worktree(tmp_path)
    (worktree / "notes.txt").write_text("outside task scope\n", encoding="utf-8")

    with pytest.raises(AssistantRepairWorkflowError) as exc_info:
        _workflow(worktree_root).validate(
            {
                "task_id": TASK_ID,
                "task_worktree": worktree.as_posix(),
                "declared_scope": ["services/openclaw-gateway-adapter"],
            }
        )

    assert exc_info.value.code == "REPAIR_DIRTY_UNRELATED"
    assert exc_info.value.details["dirty_outside_scope"] == ["notes.txt"]


def test_staged_files_cannot_escape_declared_scope(tmp_path: Path) -> None:
    worktree_root, worktree = _init_task_worktree(tmp_path)
    (worktree / "outside.txt").write_text("outside scope\n", encoding="utf-8")
    _git(worktree, "add", "outside.txt")

    with pytest.raises(AssistantRepairWorkflowError) as exc_info:
        _workflow(worktree_root).validate(
            {
                "task_id": TASK_ID,
                "task_worktree": worktree.as_posix(),
                "declared_scope": ["services/openclaw-gateway-adapter"],
                "require_clean": False,
            }
        )

    assert exc_info.value.code == "REPAIR_STAGED_SCOPE_VIOLATION"
    assert exc_info.value.details["staged_outside_scope"] == ["outside.txt"]


def test_missing_pr_is_rejected_when_closeout_requires_pr(tmp_path: Path) -> None:
    worktree_root, worktree = _init_task_worktree(tmp_path)

    with pytest.raises(AssistantRepairWorkflowError) as exc_info:
        _workflow(worktree_root).validate(
            {
                "task_id": TASK_ID,
                "task_worktree": worktree.as_posix(),
                "declared_scope": ["services/openclaw-gateway-adapter"],
                "require_pr": True,
            }
        )

    assert exc_info.value.code == "REPAIR_PR_REQUIRED"
    assert exc_info.value.details["branch"] == f"task/{TASK_ID}"
    assert exc_info.value.details["merge_target"] == "dev"


def test_pr_metadata_records_merge_target(tmp_path: Path) -> None:
    worktree_root, worktree = _init_task_worktree(tmp_path)
    snapshot = _workflow(worktree_root).validate(
        {
            "task_id": TASK_ID,
            "task_worktree": worktree.as_posix(),
            "declared_scope": ["services/openclaw-gateway-adapter"],
            "require_pr": True,
            "pull_request": {
                "number": 42,
                "url": "https://github.com/ajoe734/pantheon/pull/42",
                "state": "OPEN",
                "baseRefName": "dev",
                "headRefName": f"task/{TASK_ID}",
            },
        }
    )

    assert snapshot.pull_request is not None
    assert snapshot.to_dict()["pull_request"] == {
        "number": 42,
        "url": "https://github.com/ajoe734/pantheon/pull/42",
        "state": "OPEN",
        "base": "dev",
        "head": f"task/{TASK_ID}",
    }


def test_staged_rename_source_outside_scope_is_rejected(tmp_path: Path) -> None:
    worktree_root, worktree = _init_task_worktree(tmp_path)
    (worktree / "outside.txt").write_text("original content\n", encoding="utf-8")
    _git(worktree, "add", "outside.txt")
    _git(worktree, "commit", "-m", "add outside.txt")
    inner = worktree / "services" / "openclaw-gateway-adapter"
    inner.mkdir(parents=True, exist_ok=True)
    _git(worktree, "mv", "outside.txt", "services/openclaw-gateway-adapter/inside.txt")

    with pytest.raises(AssistantRepairWorkflowError) as exc_info:
        _workflow(worktree_root).validate(
            {
                "task_id": TASK_ID,
                "task_worktree": worktree.as_posix(),
                "declared_scope": ["services/openclaw-gateway-adapter"],
                "require_clean": False,
            }
        )

    assert exc_info.value.code == "REPAIR_STAGED_SCOPE_VIOLATION"
    assert "outside.txt" in exc_info.value.details["staged_outside_scope"]


def test_metadata_cannot_lower_require_clean_when_explicit_true(tmp_path: Path) -> None:
    worktree_root, worktree = _init_task_worktree(tmp_path)
    inner = worktree / "services" / "openclaw-gateway-adapter"
    inner.mkdir(parents=True, exist_ok=True)
    (inner / "dirty.py").write_text("# unstaged change\n", encoding="utf-8")

    with pytest.raises(AssistantRepairWorkflowError) as exc_info:
        _workflow(worktree_root).validate(
            {
                "task_id": TASK_ID,
                "task_worktree": worktree.as_posix(),
                "declared_scope": ["services/openclaw-gateway-adapter"],
                "require_clean": False,
            },
            require_clean=True,
        )

    assert exc_info.value.code == "REPAIR_WORKTREE_DIRTY"


def test_metadata_cannot_lower_require_pr_when_explicit_true(tmp_path: Path) -> None:
    worktree_root, worktree = _init_task_worktree(tmp_path)

    with pytest.raises(AssistantRepairWorkflowError) as exc_info:
        _workflow(worktree_root).validate(
            {
                "task_id": TASK_ID,
                "task_worktree": worktree.as_posix(),
                "declared_scope": ["services/openclaw-gateway-adapter"],
                "require_pr": False,
            },
            require_pr=True,
        )

    assert exc_info.value.code == "REPAIR_PR_REQUIRED"


def test_repair_mode_still_denies_destructive_git() -> None:
    decision = AssistantCommandPolicy().evaluate(
        mode="kernel_repair",
        command_class="repo_status",
        argv=["git", "reset", "--hard"],
    )

    assert decision.allowed is False
    assert decision.policy_class == "denylist"
