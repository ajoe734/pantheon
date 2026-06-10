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


def _init_source_repo(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "assistant@example.invalid")
    _git(source, "config", "user.name", "Assistant Test")
    (source / "README.md").write_text("# source\n", encoding="utf-8")
    (source / "services" / "control-plane" / "bff").mkdir(parents=True)
    (source / "services" / "control-plane" / "bff" / "main.py").write_text("# bff\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "initial")
    _git(source, "branch", "-M", "dev")
    _git(source, "remote", "add", "origin", "https://github.com/ajoe734/pantheon.git")
    return source


def _init_canonical_remote_with_status_source(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "canonical.git"
    canonical = tmp_path / "canonical-work"
    source = tmp_path / "status-root"
    _git(tmp_path, "init", "--bare", remote.as_posix())
    canonical.mkdir()
    _git(canonical, "init")
    _git(canonical, "config", "user.email", "assistant@example.invalid")
    _git(canonical, "config", "user.name", "Assistant Test")
    (canonical / "README.md").write_text("# canonical-dev\n", encoding="utf-8")
    _git(canonical, "add", "README.md")
    _git(canonical, "commit", "-m", "canonical dev")
    _git(canonical, "branch", "-M", "dev")
    _git(canonical, "remote", "add", "origin", remote.as_posix())
    _git(canonical, "push", "-u", "origin", "dev")

    _git(tmp_path, "clone", remote.as_posix(), source.as_posix())
    _git(source, "config", "user.email", "assistant@example.invalid")
    _git(source, "config", "user.name", "Assistant Test")
    _git(source, "checkout", "-b", "task/live-status")
    (source / "README.md").write_text("# live-status-branch\n", encoding="utf-8")
    _git(source, "add", "README.md")
    _git(source, "commit", "-m", "live status branch")
    return source, remote


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


def test_prepare_creates_clean_task_worktree_from_configured_source(tmp_path: Path) -> None:
    source = _init_source_repo(tmp_path)
    worktree_root = tmp_path / "prepared-worktrees"
    workflow = AssistantRepairWorkflow(
        {
            "PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT": worktree_root.as_posix(),
            "PANTHEON_ASSISTANT_REPAIR_REPO_URL": source.as_posix(),
        },
        pr_lookup=lambda _worktree, _branch: None,
    )

    prepared = workflow.prepare(
        {
            "task_id": "MGMT-AI-REPAIR-123",
            "declared_scope": ["services/control-plane/bff"],
        }
    )

    payload = prepared.to_dict()
    worktree = Path(payload["repair"]["task_worktree"])
    assert payload["created"] is True
    assert worktree.is_dir()
    assert _git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == "task/MGMT-AI-REPAIR-123"
    assert _git(worktree, "remote", "get-url", "origin") == "https://github.com/ajoe734/pantheon.git"
    assert payload["repair"]["declared_scope"] == ["services/control-plane/bff"]
    assert payload["workflow"]["clean"] is True
    assert payload["workflow"]["merge_target"] == "dev"


def test_prepare_existing_worktree_is_idempotent(tmp_path: Path) -> None:
    source = _init_source_repo(tmp_path)
    worktree_root = tmp_path / "prepared-worktrees"
    workflow = AssistantRepairWorkflow(
        {
            "PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT": worktree_root.as_posix(),
            "PANTHEON_ASSISTANT_REPAIR_REPO_URL": source.as_posix(),
        },
        pr_lookup=lambda _worktree, _branch: None,
    )

    first = workflow.prepare(
        {
            "task_id": "MGMT-AI-REPAIR-456",
            "declared_scope": ["services/control-plane/bff"],
        }
    )
    second = workflow.prepare(
        {
            "task_id": "MGMT-AI-REPAIR-456",
            "declared_scope": ["services/control-plane/bff"],
        }
    )

    assert first.created is True
    assert second.created is False
    assert second.workflow.clean is True
    assert second.repair_metadata["task_worktree"] == first.repair_metadata["task_worktree"]


def test_prepare_uses_repo_key_specific_source_env(tmp_path: Path) -> None:
    source = _init_source_repo(tmp_path)
    worktree_root = tmp_path / "prepared-worktrees"
    workflow = AssistantRepairWorkflow(
        {
            "PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT": worktree_root.as_posix(),
            "PANTHEON_ASSISTANT_REPAIR_REPO_URL_EXECUTE_PLANS": source.as_posix(),
            "PANTHEON_ASSISTANT_REPAIR_REMOTE_URL_EXECUTE_PLANS": "https://github.com/ajoe734/execute-plans.git",
        },
        pr_lookup=lambda _worktree, _branch: None,
    )

    prepared = workflow.prepare(
        {
            "task_id": "MGMT-AI-REPAIR-FE",
            "repoKey": "execute-plans",
            "declared_scope": ["src/lib/bff-v1"],
        }
    )

    payload = prepared.to_dict()
    worktree = Path(payload["repair"]["task_worktree"])
    assert payload["repair"]["repo_key"] == "execute-plans"
    assert worktree.parent.name == "execute-plans"
    assert _git(worktree, "remote", "get-url", "origin") == "https://github.com/ajoe734/execute-plans.git"


def test_prepare_fetches_merge_target_from_canonical_remote_not_status_head(tmp_path: Path) -> None:
    source, canonical_remote = _init_canonical_remote_with_status_source(tmp_path)
    worktree_root = tmp_path / "prepared-worktrees"
    workflow = AssistantRepairWorkflow(
        {
            "PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT": worktree_root.as_posix(),
            "PANTHEON_ASSISTANT_REPAIR_REPO_URL": source.as_posix(),
            "PANTHEON_ASSISTANT_REPAIR_REMOTE_URL": canonical_remote.as_posix(),
        },
        pr_lookup=lambda _worktree, _branch: None,
    )

    prepared = workflow.prepare(
        {
            "task_id": "MGMT-AI-REPAIR-CANONICAL",
            "declared_scope": ["README.md"],
        }
    )

    worktree = Path(prepared.repair_metadata["task_worktree"])
    assert _git(worktree, "remote", "get-url", "origin") == canonical_remote.as_posix()
    assert (worktree / "README.md").read_text(encoding="utf-8") == "# canonical-dev\n"
    assert prepared.workflow.branch == "task/MGMT-AI-REPAIR-CANONICAL"


def test_prepare_requires_configured_repo_source(tmp_path: Path) -> None:
    workflow = AssistantRepairWorkflow(
        {
            "PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT": (tmp_path / "prepared-worktrees").as_posix(),
        },
        pr_lookup=lambda _worktree, _branch: None,
    )

    with pytest.raises(AssistantRepairWorkflowError) as exc_info:
        workflow.prepare(
            {
                "task_id": "MGMT-AI-REPAIR-789",
                "declared_scope": ["services/control-plane/bff"],
            }
        )

    assert exc_info.value.code == "REPAIR_REPO_URL_NOT_CONFIGURED"
    assert exc_info.value.status_code == 503
