"""DTG-CLEAN-M4: worker workspace filesystem owner.

Worktree preparation, safe reuse/refresh, dirt classification, dirty
archive, registered-lease cleanup, and orphan pruning for worker
worktrees, moved out of .orchestrator/supervisor.py. Operates on
explicit config/state/path inputs and git worktree state only; it does
not read or write canonical task records and does not decide dispatch.
supervisor.py retains cycle timing (interval gating in its callers),
worker task-brief/context materialization, and tree-guard policy.

A handful of symbols (write_activity_log, pid_is_alive,
parse_runtime_timestamp, materialize_worker_context_files,
bind_external_worker_context) remain owned by supervisor.py because
they are shared with unrelated supervisor concerns; they are resolved
lazily via _supervisor_module() so this module can be imported at
supervisor.py's top level without a circular import.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from adapters.base import DeliveryRequest
from common import (
    config_path,
    first_symlink_component,
    load_status,
    normalize_github_repo_slug,
    utc_now,
)
from dispatch_policy import (
    REASON_OWNED_FINALIZE,
    REASON_OWNED_IN_PROGRESS,
    REASON_OWNED_READY,
    REASON_REVIEW_READY,
    ready_dispatch_settings,
)
from multi_repo_registry import (
    repositories,
    repository_configured_local_path,
    repository_local_path,
    repository_slug,
    resolve_repository,
    validate_task_repository_scope,
)
from rewrite.task_identity import task_generation
from rewrite.worker_recovery import _canonical_worker_recovery_receipt

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _supervisor_module():
    orchestrator_dir = Path(__file__).resolve().parents[1]
    if str(orchestrator_dir) not in sys.path:
        sys.path.insert(0, str(orchestrator_dir))
    import supervisor

    return supervisor


def write_activity_log(config: dict[str, Any], entry: dict[str, Any]) -> None:
    _supervisor_module().write_activity_log(config, entry)


def pid_is_alive(pid: int | None) -> bool:
    return _supervisor_module().pid_is_alive(pid)


def _parse_iso_utc(value: str | None) -> datetime | None:
    return _supervisor_module().parse_runtime_timestamp(value)


def materialize_worker_context_files(*args: Any, **kwargs: Any) -> Any:
    return _supervisor_module().materialize_worker_context_files(*args, **kwargs)


def bind_external_worker_context(*args: Any, **kwargs: Any) -> Any:
    return _supervisor_module().bind_external_worker_context(*args, **kwargs)

def worker_worktree_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("worker_worktrees")
    settings = raw if isinstance(raw, dict) else {}
    return {
        "root": str(settings.get("root") or "/tmp/pantheon-worker-worktrees"),
    }


def worktree_cleanup_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("worker_worktree_cleanup")
    settings = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(settings.get("enabled", True)),
        "cleanup_inactive_leases": bool(settings.get("cleanup_inactive_leases", True)),
        "archive_dirty_worktrees": bool(settings.get("archive_dirty_worktrees", True)),
        "force_remove_archived_dirty": bool(settings.get("force_remove_archived_dirty", True)),
        "archive_root": str(settings.get("archive_root") or "/tmp/pantheon-worker-worktree-archive"),
        "archive_max_file_bytes": int(settings.get("archive_max_file_bytes", 20 * 1024 * 1024) or 0),
        "max_removals_per_tick": int(settings.get("max_removals_per_tick", 25) or 0),
        "orphan_prune_interval_seconds": int(
            settings.get("orphan_prune_interval_seconds", 600) or 0
        ),
        # An orphan whose branch never merges (superseded, abandoned, rejected)
        # would otherwise be skipped by require_merged forever. Removing its
        # worktree loses nothing: the branch and its commits stay in the repo's
        # object database, recoverable with `git worktree add` again. 0 disables
        # this fallback and restores the old permanent-skip behavior.
        "orphan_unmerged_max_age_days": int(
            settings.get("orphan_unmerged_max_age_days", 14) or 0
        ),
    }


def _worktree_last_activity_epoch(
    repository_root: Path, branch: str, worktree_path: Path
) -> float | None:
    """Best-effort last-touched time for staleness comparisons."""

    if branch:
        proc = subprocess.run(
            ["git", "-C", str(repository_root), "log", "-1", "--format=%ct", branch],
            capture_output=True,
            text=True,
            check=False,
        )
        text = proc.stdout.strip()
        if proc.returncode == 0 and text.isdigit():
            return float(text)
    try:
        return worktree_path.stat().st_mtime
    except OSError:
        return None


def _task_id_slug(task_id: str | None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(task_id or "").lower()).strip("-")
    return slug or "unknown-task"


def worker_task_branch(config: dict[str, Any], task_id: str | None) -> str:
    branch_workflow = config.get("branch_workflow") if isinstance(config.get("branch_workflow"), dict) else {}
    prefix = str(branch_workflow.get("task_branch_prefix") or "task/")
    normalized_task_id = str(task_id or "").strip()
    return f"{prefix}{normalized_task_id}" if normalized_task_id else f"{prefix}unknown-task"


def _worker_worktree_base_root(config: dict[str, Any], settings: dict[str, Any]) -> Path:
    repo_root = config_path(config, "status_file").parents[0]
    configured = Path(os.path.expanduser(str(settings.get("root") or "")))
    if not configured.is_absolute():
        configured = repo_root / configured
    return configured.resolve()


def worker_worktree_source_root(
    config: dict[str, Any],
    *,
    repository_id: str = "pantheon",
) -> Path:
    """Return the registry-owned checkout used for this repository's worktrees.

    The repository registry is the sole source authority for both Pantheon and
    cross-repository delivery.  A live split-root projection supplies absolute
    paths (Pantheon staging for Pantheon; the canonical checkout for each
    external repository); status paths never determine Git source ownership.
    """

    repository_root = repository_local_path(config, repository_id)
    if repository_root is None:
        raise RuntimeError(
            f"delivery repository {repository_id!r} has no registered local_path"
        )
    return repository_root.resolve()


def worker_task_worktree_path(
    config: dict[str, Any],
    task_id: str | None,
    settings: dict[str, Any] | None = None,
    *,
    repository_id: str = "pantheon",
) -> Path:
    active_settings = settings or worker_worktree_settings(config)
    repository_name = str(
        resolve_repository(config, repository_id).get("display_name") or repository_id
    )
    repo_slug = re.sub(r"[^a-z0-9]+", "-", repository_name.lower()).strip("-") or "repo"
    return _worker_worktree_base_root(config, active_settings) / repo_slug / _task_id_slug(task_id)


def worker_request_repository_id(config: dict[str, Any], request: DeliveryRequest) -> str:
    task = request.metadata.get("task")
    task_payload = task if isinstance(task, dict) else {}
    try:
        repository_id = validate_task_repository_scope(config, task_payload)
    except (ValueError, RuntimeError) as exc:
        raise RuntimeError(f"invalid delivery repository scope: {exc}") from exc
    declared = str(request.metadata.get("workspace_repository_id") or "").strip()
    if declared and declared != repository_id:
        raise RuntimeError(
            f"workspace repository mismatch: {declared} != {repository_id}"
        )
    return repository_id


def worker_repository_base_ref(
    config: dict[str, Any],
    repository_id: str,
) -> str:
    default_branch = str(
        resolve_repository(config, repository_id).get("default_branch") or ""
    ).strip()
    if not default_branch:
        raise RuntimeError(
            f"delivery repository {repository_id!r} has no default_branch"
        )
    return f"origin/{default_branch}"


def validate_worker_repository_source(
    config: dict[str, Any],
    repository_id: str,
    source_root: Path,
) -> None:
    configured_root = repository_configured_local_path(config, repository_id)
    if configured_root is None:
        raise RuntimeError(
            f"delivery repository {repository_id!r} has no configured local_path"
        )
    configured_symlink = first_symlink_component(configured_root)
    if configured_symlink is not None:
        raise RuntimeError(
            "repository source root cannot include a symlink component: "
            f"{configured_symlink}"
        )
    if configured_root.resolve() != source_root:
        raise RuntimeError(
            f"repository source root does not match configured local_path: {source_root}"
        )
    if not source_root.is_absolute():
        raise RuntimeError(f"repository source root must be absolute: {source_root}")
    symlink_component = first_symlink_component(source_root)
    if symlink_component is not None:
        raise RuntimeError(
            f"repository source root cannot include a symlink component: {symlink_component}"
        )
    if not source_root.is_dir():
        raise RuntimeError(f"repository source root does not exist: {source_root}")
    top_proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=source_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if top_proc.returncode != 0 or Path(top_proc.stdout.strip()).resolve() != source_root:
        raise RuntimeError(f"repository source root is not a git root: {source_root}")
    expected_slug = normalize_github_repo_slug(repository_slug(config, repository_id))
    if not expected_slug:
        raise RuntimeError(
            f"delivery repository {repository_id!r} has no configured GitHub slug"
        )
    remote_proc = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=source_root,
        capture_output=True,
        text=True,
        check=False,
    )
    actual_slug = normalize_github_repo_slug(remote_proc.stdout.strip())
    if remote_proc.returncode != 0 or actual_slug != expected_slug:
        raise RuntimeError(
            f"repository source origin mismatch: {actual_slug or 'missing'} != {expected_slug}"
        )


def validate_worker_workspace_binding(
    source_root: Path,
    workspace_path: Path,
    *,
    expected_branch: str | None = None,
) -> None:
    top_proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=workspace_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if top_proc.returncode != 0 or Path(top_proc.stdout.strip()).resolve() != workspace_path:
        raise RuntimeError(
            f"workspace_path is not a git repository root: {workspace_path}"
        )

    def common_dir(root: Path) -> Path:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git common directory is unavailable for {root}")
        path = Path(proc.stdout.strip())
        if not path.is_absolute():
            path = root / path
        return path.resolve()

    if common_dir(workspace_path) != common_dir(source_root):
        raise RuntimeError(
            "workspace_path is not registered to the selected delivery repository"
        )
    records = {
        Path(record["worktree"]).resolve(): record
        for record in _git_worktree_records(source_root)
        if record.get("worktree")
    }
    record = records.get(workspace_path)
    if record is None:
        raise RuntimeError(
            "workspace_path is absent from the selected repository worktree registry"
        )
    branch = _worktree_record_branch(record)
    if expected_branch and branch != expected_branch:
        raise RuntimeError(
            f"workspace branch mismatch: {branch or 'detached'} != {expected_branch}"
        )


def worker_workspace_task_id(request: DeliveryRequest) -> str | None:
    metadata_task_id = str(request.metadata.get("workspace_task_id") or "").strip()
    task_id = metadata_task_id or str(request.task_id or "").strip()
    return task_id or None


def _git_worktree_records(repo_root: Path) -> list[dict[str, str]]:
    proc = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value.strip()
    if current:
        records.append(current)
    return records


def _worktree_record_branch(record: dict[str, str]) -> str:
    branch = str(record.get("branch") or "").strip()
    if branch.startswith("refs/heads/"):
        return branch[len("refs/heads/") :]
    return branch


def _existing_worktree_for_branch(repo_root: Path, branch: str, *, exclude_root: bool) -> Path | None:
    resolved_repo_root = repo_root.resolve()
    for record in _git_worktree_records(repo_root):
        if _worktree_record_branch(record) != branch:
            continue
        path_value = record.get("worktree")
        if not path_value:
            continue
        path = Path(path_value).resolve()
        if exclude_root and path == resolved_repo_root:
            continue
        return path
    return None


def _branch_checked_out_in_root(repo_root: Path, branch: str) -> bool:
    for record in _git_worktree_records(repo_root):
        path_value = record.get("worktree")
        if not path_value:
            continue
        if Path(path_value).resolve() == repo_root.resolve():
            return _worktree_record_branch(record) == branch
    return False


def _git_ref_exists(repo_root: Path, ref: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode == 0


def _fetch_worker_base_ref(
    repo_root: Path,
    base_ref: str,
    *,
    timeout_seconds: float | None = None,
) -> tuple[bool, str | None]:
    """Refresh the exact remote-tracking ref used to lease worker worktrees.

    ``git fetch origin dev`` updates ``FETCH_HEAD`` but does not necessarily
    update ``refs/remotes/origin/dev`` when the checkout's configured fetch
    refspec tracks only another branch (the live command checkout tracked only
    ``master``).  Worktree creation and freshness checks consume the remote-
    tracking ref, so fetch it with an explicit source and destination.

    ``timeout_seconds`` is available to standalone callers.  The supervisor
    cycle invokes this function only during its pre-admission phase; dispatch
    itself never performs a recovery fetch while holding runtime admission.
    """

    normalized = str(base_ref or "").strip()
    if normalized.startswith("origin/"):
        branch = normalized[len("origin/") :]
        refspec = f"+refs/heads/{branch}:refs/remotes/origin/{branch}"
    else:
        refspec = normalized
    if not refspec:
        return False, "missing_base_ref"

    try:
        proc = subprocess.run(
            ["git", "fetch", "origin", refspec, "--quiet"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return False, f"git fetch timed out after {timeout_seconds}s"
    if proc.returncode == 0:
        return True, None
    details = (proc.stderr or proc.stdout or "").strip()
    return False, details or "git fetch failed"


def _git_resolve_commit(repo_root: Path, ref: str) -> tuple[str | None, str | None]:
    """Resolve one ref to the immutable commit a worker can safely use."""

    proc = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    resolved = (proc.stdout or "").strip().lower()
    if proc.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", resolved):
        return resolved, None
    detail = (proc.stderr or proc.stdout or "").strip()
    return None, detail or f"base_ref_unresolved:{ref}"


def resolve_worker_base_snapshot(
    config: dict[str, Any],
    repository_id: str,
    snapshot_cache: dict[str, dict[str, str]],
) -> tuple[dict[str, str] | None, str | None]:
    """Fetch a delivery repository once per cycle and pin its base commit.

    ``origin/<default_branch>`` is intentionally mutable.  A cycle shares one
    resolved commit across every launch for the same repository, while the
    durable worker lease records that exact SHA after a successful launch.
    Git I/O occurs from the existing reserved delivery phase, outside runtime
    admission locks; this cache is deliberately in-memory rather than a second
    runtime-state authority.
    """

    cached = snapshot_cache.get(repository_id)
    if cached is not None:
        error = cached.get("error")
        return (None, error) if error else (cached, None)

    try:
        source_root = worker_worktree_source_root(config, repository_id=repository_id)
        base_ref = worker_repository_base_ref(config, repository_id)
        validate_worker_repository_source(config, repository_id, source_root)
    except RuntimeError as exc:
        error = f"delivery_repository_invalid:{exc}"
        snapshot_cache[repository_id] = {"error": error}
        return None, error

    fetched, fetch_error = _fetch_worker_base_ref(
        source_root,
        base_ref,
        timeout_seconds=30,
    )
    if not fetched:
        error = f"base_fetch_failed:{fetch_error or 'git fetch failed'}"
        snapshot_cache[repository_id] = {"error": error}
        return None, error
    base_sha, resolve_error = _git_resolve_commit(source_root, base_ref)
    if base_sha is None:
        error = f"base_ref_unresolved:{resolve_error or base_ref}"
        snapshot_cache[repository_id] = {"error": error}
        return None, error

    snapshot = {
        "repository_id": repository_id,
        "source_root": str(source_root),
        "base_ref": base_ref,
        "base_sha": base_sha,
        "fetched_at": utc_now(),
    }
    snapshot_cache[repository_id] = snapshot
    return snapshot, None


def _quarantine_incomplete_worker_path(path: Path) -> Path | None:
    """Move an unregistered partial checkout aside so dispatch can recover.

    ``git worktree add`` can leave a populated directory without a ``.git``
    marker when checkout is interrupted (for example by ENOSPC).  These paths
    are not reusable worktrees, but refusing them forever wedges every later
    dispatch for the task.  Preserve the entire directory under the managed
    root and let the caller create a clean worktree at the canonical path.
    """
    if (
        not path.exists()
        or path.is_symlink()
        or not path.is_dir()
        or not any(path.iterdir())
        or (path / ".git").exists()
    ):
        return None

    quarantine_root = path.parent / ".incomplete-worktree-quarantine"
    quarantine_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    quarantine_path = quarantine_root / f"{path.name}-{stamp}-{os.getpid()}"
    try:
        path.replace(quarantine_path)
    except OSError:
        return None
    try:
        (quarantine_path / "ORCHESTRATOR_QUARANTINE.txt").write_text(
            "Incomplete worker checkout preserved before automatic redispatch.\n"
            f"original_path={path}\n"
            f"quarantined_at={utc_now()}\n",
            encoding="utf-8",
        )
    except OSError:
        # The recovery must still unblock a fresh checkout when the original
        # interruption was ENOSPC and even the small marker cannot be written.
        pass
    return quarantine_path


def _create_worker_worktree(
    repo_root: Path,
    path: Path,
    branch: str,
    base_sha: str,
) -> tuple[bool, str | None, str | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        if _quarantine_incomplete_worker_path(path) is None:
            return False, f"Worker worktree path already exists and is not empty: {path}", None

    remote_ref = f"refs/remotes/origin/{branch}"
    if _git_ref_exists(repo_root, f"refs/heads/{branch}"):
        command = ["git", "worktree", "add", str(path), branch]
        origin = "existing_local_branch"
    elif _git_ref_exists(repo_root, remote_ref):
        command = ["git", "worktree", "add", "-b", branch, str(path), f"origin/{branch}"]
        origin = "existing_remote_branch"
    else:
        command = ["git", "worktree", "add", "-b", branch, str(path), base_sha]
        origin = "base_snapshot"

    proc = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        details = (proc.stderr or proc.stdout or "").strip()
        return False, f"Failed to create worker worktree {path} for {branch}: {details}", None
    return True, None, origin


_REUSABLE_DIRTY_PREFIXES = (
    ".orchestrator/reviews/",
)


def _classify_worktree_dirt(porcelain_status: str) -> tuple[str, list[str]]:
    """Classify reused-worktree dirtiness from `git status --porcelain` output.

    Returns (classification, paths):
      'clean'        - no tracked/staged changes; paths is []
      'scratch_only' - every change is orchestrator-managed scratch
                       (see _REUSABLE_DIRTY_PREFIXES); paths lists them
      'real'         - at least one change outside scratch -> must block dispatch
    """
    lines = [ln for ln in porcelain_status.splitlines() if ln.strip()]
    if not lines:
        return "clean", []
    paths: list[str] = []
    for ln in lines:
        body = ln[3:] if len(ln) > 3 else ln.strip()
        # rename/copy lines render as "old -> new"; the new path is what exists.
        path = body.split(" -> ")[-1].strip().strip('"')
        if path:
            paths.append(path)
    if any(not p.startswith(_REUSABLE_DIRTY_PREFIXES) for p in paths):
        return "real", []
    return "scratch_only", paths


def _restore_reusable_scratch(worktree_path: Path, paths: list[str]) -> None:
    """Restore orchestrator scratch paths to HEAD and drop untracked scratch."""
    if paths:
        subprocess.run(
            ["git", "checkout", "-q", "HEAD", "--", *sorted(set(paths))],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
        )
    subprocess.run(
        ["git", "clean", "-fq", "--", *_REUSABLE_DIRTY_PREFIXES],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )


def _staged_index_split_paths_matching_head(worktree_path: Path) -> list[str]:
    """Return staged paths whose worktree bytes already match HEAD.

    Worker worktrees can be left with a split index after a merge/review loop:
    the index stages a reverse patch while the working tree contains the branch
    HEAD content. In that case `git restore --staged` is safe because it only
    repairs the index. Real staged additions/renames or content changes must
    continue to block dispatch.
    """
    proc = subprocess.run(
        ["git", "diff", "--cached", "--name-status"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    paths: list[str] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            return []
        status, path = parts[0], parts[-1]
        if status not in {"M", "D"}:
            return []
        candidate = worktree_path / path
        if not candidate.is_file():
            return []
        head_proc = subprocess.run(
            ["git", "rev-parse", f"HEAD:{path}"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
        )
        worktree_proc = subprocess.run(
            ["git", "hash-object", path],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if head_proc.returncode != 0 or worktree_proc.returncode != 0:
            return []
        if head_proc.stdout.strip() != worktree_proc.stdout.strip():
            return []
        paths.append(path)
    return paths


def _restore_reused_index_split(worktree_path: Path, paths: list[str]) -> bool:
    if not paths:
        return False
    proc = subprocess.run(
        ["git", "restore", "--staged", "--", *sorted(set(paths))],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _lost_lease_replacement_may_adopt_worktree(
    config: dict[str, Any],
    state: dict[str, Any],
    request: DeliveryRequest,
    *,
    task_id: str | None,
    repository_id: str,
    source_root: Path,
    branch: str,
    worktree_path: Path,
    base_ref: str,
    queue_event_id: str | None,
    target_agent: str | None,
) -> bool:
    """True only for the exact fenced replacement and its registered worktree.

    A receipt only reaches ``reassigned`` after `_persist_task_reassignment_locked`
    CAS'd it out of ``pending``, and it only reaches ``pending`` after
    `recover_lost_worker_lease` fenced the predecessor using the existing
    poll-stage liveness check (missing process / expired lease). The receipt
    is therefore already durable proof the predecessor process and lease are
    no longer live; no separate liveness probe is needed here.
    """
    if not task_id or str(request.task_id or "") != task_id:
        return False
    # Some unit callers exercise workspace preparation with a legacy
    # non-authoritative fixture that intentionally has no task-state store.
    # Eligibility is an opt-in safety gate: if the canonical binding cannot be
    # read, fail closed and let the existing dirty-worktree guard decide.
    try:
        status = load_status(config)
    except (RuntimeError, OSError, ValueError):
        return False
    supervisor = _supervisor_module()
    task = supervisor.task_index_from_status(config, status).get(task_id)
    if task is None:
        return False
    try:
        canonical_repository_id = validate_task_repository_scope(config, task)
    except (RuntimeError, ValueError):
        return False
    if (
        canonical_repository_id != repository_id
        or branch != worker_task_branch(config, task_id)
    ):
        return False
    receipt = _canonical_worker_recovery_receipt(status, task)
    if receipt is None or str(receipt.get("task_id") or "") != task_id:
        return False
    if str(receipt.get("status") or "") != "reassigned":
        return False
    receipt_id = str(receipt.get("receipt_id") or "").strip()
    if (
        not receipt_id
        or str(receipt.get("reason_kind") or "")
        not in {"worker_process_missing", "worker_lease_expired"}
        or str(request.metadata.get("recovery_receipt_id") or "") != receipt_id
    ):
        return False
    replacement = receipt.get("replacement")
    if not isinstance(replacement, Mapping):
        return False
    generation = task_generation(task)
    try:
        replacement_generation = int(replacement.get("task_generation") or -1)
        request_generation = int(request.metadata.get("task_generation") or -1)
    except (TypeError, ValueError):
        return False
    if replacement_generation != generation or request_generation != generation:
        return False
    role = str(receipt.get("recovery_role") or "owner")
    expected_actor = str(
        replacement.get("agent")
        or (
            replacement.get("reviewer")
            if role == "reviewer"
            else replacement.get("owner")
        )
        or ""
    )
    actual_actor = supervisor.canonical_agent_name(config, str(target_agent or ""))
    if (
        not expected_actor
        or supervisor.canonical_agent_name(config, expected_actor) != actual_actor
        or str(replacement.get("owner") or "") != str(task.get("owner") or "")
        or str(replacement.get("reviewer") or "")
        != str(task.get("reviewer") or "")
    ):
        return False
    expected_reasons = (
        {REASON_REVIEW_READY}
        if role == "reviewer"
        else {REASON_OWNED_READY, REASON_OWNED_IN_PROGRESS, REASON_OWNED_FINALIZE}
    )
    if str(request.reason or "") not in expected_reasons:
        return False

    queue_events_by_id = (state.get("queue") or {}).get("events") or {}
    queue_record = queue_events_by_id.get(str(queue_event_id or ""))
    queue_intent = (
        queue_record.get("intent") if isinstance(queue_record, Mapping) else None
    )
    try:
        queue_generation = int((queue_intent or {}).get("task_generation") or -1)
    except (AttributeError, TypeError, ValueError):
        return False
    if (
        not isinstance(queue_record, Mapping)
        or not isinstance(queue_intent, Mapping)
        or str(queue_record.get("recovery_receipt_id") or "") != receipt_id
        or str(queue_intent.get("recovery_receipt_id") or "") != receipt_id
        or str(queue_intent.get("task_id") or "") != task_id
        or queue_generation != generation
        or supervisor.canonical_agent_name(
            config, str(queue_intent.get("target_agent") or "")
        )
        != actual_actor
    ):
        return False

    leases = (state.get("worker_worktrees") or {}).get("leases") or {}
    lease = leases.get(task_id)
    if not isinstance(lease, Mapping):
        return False
    try:
        lease_path = Path(str(lease.get("path") or "")).resolve()
        lease_source_root = Path(str(lease.get("source_root") or "")).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    if (
        str(lease.get("task_id") or "") != task_id
        or str(lease.get("workspace_task_id") or "") != task_id
        or str(lease.get("repository_id") or "") != repository_id
        or str(lease.get("branch") or "") != branch
        or str(lease.get("base_ref") or "") != base_ref
        or lease_path != worktree_path.resolve()
        or lease_source_root != source_root.resolve()
    ):
        return False
    active_statuses = {
        str(value)
        for value in ready_dispatch_settings(config).get("active_worker_statuses", [])
    }
    for worker in (state.get("workers") or {}).values():
        if (
            str(worker.get("task_id") or "") == task_id
            and str(worker.get("status") or "") in active_statuses
        ):
            return False
    return True


def _refresh_reused_worker_worktree(
    worktree_path: Path,
    base_sha: str,
    *,
    task_id: str | None = None,
    branch: str | None = None,
    allow_dirty_wip_adoption: bool = False,
) -> tuple[bool, str]:
    """Fast-forward a reused worker worktree to the cycle's pinned base SHA.

    Reused worktrees may carry the worker's per-task branch from days ago,
    which means their copy of `scripts/ai_status.py` / supervisor / skills can
    be older than the supervisor root. That stale snapshot has bypassed gates
    such as ORCH-CLOSEOUT-MERGE-GATE (require_merged_pr). Refresh on lease so
    the worker always sees current control-plane code.

    Strategy: merge the already fetched, immutable cycle snapshot with
    `git merge --ff-only <base-sha>`. Never auto-resolve a real merge — if the branch genuinely
    diverged, leave it for the worker to handle. Dirty reused worktrees are
    blocked before dispatch so workers cannot inherit unrelated staged or
    tracked changes, unless `allow_dirty_wip_adoption` proves this is the
    exact fenced lost-lease replacement taking over its own task's worktree;
    that WIP is left untouched (no reset/clean/stash/commit) and dispatch
    proceeds without the base-SHA refresh below.
    """
    status_proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    scratch_restored = False
    index_restored = False
    if status_proc.returncode == 0 and status_proc.stdout.strip():
        classification, scratch_paths = _classify_worktree_dirt(status_proc.stdout)
        if classification == "real":
            if allow_dirty_wip_adoption:
                return True, "adopted_lost_lease_dirty_wip"
            index_split_paths = _staged_index_split_paths_matching_head(worktree_path)
            if index_split_paths and _restore_reused_index_split(worktree_path, index_split_paths):
                index_restored = True
            # No restorable staged index-split (or a failed restore) is NOT fatal:
            # fall through to re-classify and anchor genuine task WIP below instead
            # of hard-blocking dispatch forever. The previous early return here made
            # the auto-anchor unreachable for plain unstaged real dirt -- the common
            # case (a superseded run leaves modified-but-unstaged task files).
            status_proc = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                check=False,
            )
            if status_proc.returncode != 0:
                return False, "skipped_dirty_worktree"
            classification, scratch_paths = _classify_worktree_dirt(status_proc.stdout)
            if classification == "real":
                # The supervisor owns leases, not source authorship.  Preserve
                # worker WIP and wait for the task's normal delivery path to
                # reconcile it; never synthesize a commit or reviewer identity.
                return False, "skipped_dirty_worktree"
            if classification == "clean":
                scratch_paths = []
        # Only orchestrator-managed scratch is dirty: restore it and reuse the
        # worktree instead of jamming dispatch on regenerable bookkeeping churn.
        if scratch_paths:
            _restore_reusable_scratch(worktree_path, scratch_paths)
            verify_untracked = "all" if index_restored else "no"
            verify_proc = subprocess.run(
                ["git", "status", "--porcelain", f"--untracked-files={verify_untracked}"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                check=False,
            )
            if verify_proc.returncode == 0 and verify_proc.stdout.strip():
                return False, "skipped_dirty_worktree"
            scratch_restored = True

    merge_proc = subprocess.run(
        ["git", "merge", "--ff-only", base_sha],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if merge_proc.returncode == 0:
        head_proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
        )
        head = (head_proc.stdout or "").strip()
        status_suffixes = []
        if scratch_restored:
            status_suffixes.append("scratch_restored")
        if index_restored:
            status_suffixes.append("index_restored")
        suffix = f"+{'+'.join(status_suffixes)}" if status_suffixes else ""
        return True, (f"ff_to_{head}{suffix}" if head else f"ff_ok{suffix}")
    details = (merge_proc.stderr or merge_proc.stdout or "").strip().splitlines()[0] if (merge_proc.stderr or merge_proc.stdout) else "unknown"
    return False, f"non_fast_forward: {details}"


def _recovery_worktree_archive_root(config: dict[str, Any]) -> Path:
    """Resolve the managed archive root used for recovery WIP snapshots."""

    settings = worktree_cleanup_settings(config)
    archive_root = Path(os.path.expanduser(str(settings["archive_root"])))
    if not archive_root.is_absolute():
        archive_root = config_path(config, "status_file").parents[0] / archive_root
    return archive_root.resolve()


def _recovery_worktree_has_stale_adopted_wip(
    state: dict[str, Any],
    *,
    task_id: str,
    worktree_path: Path,
    recovery_receipt_id: str | None = None,
) -> bool:
    """Return whether a prior lost-lease replacement already adopted WIP.

    Dirty-WIP adoption is intentionally a one-shot handoff.  A second lost
    lease must not keep inheriting the same rejected tree indefinitely; it must
    archive the snapshot and start from the pinned base instead.
    """

    leases = (state.get("worker_worktrees") or {}).get("leases") or {}
    lease = leases.get(task_id)
    if not isinstance(lease, Mapping):
        return False
    try:
        leased_path = Path(str(lease.get("path") or "")).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    if leased_path != worktree_path.resolve():
        return False
    current_receipt = str(recovery_receipt_id or "").strip()
    prior_receipt = str(
        lease.get("recovery_receipt_id")
        or lease.get("dirty_wip_adoption_receipt_id")
        or ""
    ).strip()
    # A duplicate dispatch for the same recovery receipt is the same handoff
    # and may continue using its adopted WIP. A later receipt means the prior
    # handoff has ended; archive/reset before another worker touches the tree.
    # An empty current receipt is ordinary reuse and cannot inherit recovery
    # WIP either.
    return bool(prior_receipt and prior_receipt != current_receipt)


def _replace_recovery_worktree_from_base(
    repo_root: Path,
    worktree_path: Path,
    *,
    branch: str,
    base_sha: str,
    archive_root: Path,
    task_id: str,
    max_file_bytes: int,
) -> tuple[bool, str, Path | None, str | None]:
    """Archive a stale recovery tree and recreate its task branch at base.

    The old worktree is preserved as a patch/file archive and its previous
    branch tip is retained under a private recovery ref before the task branch
    is reset to the immutable cycle base.  This keeps rejected WIP recoverable
    without allowing it to become the next worker's source tree.
    """

    branch_ref = f"refs/heads/{branch}"
    old_head = subprocess.run(
        ["git", "rev-parse", "--verify", f"{branch_ref}^{{commit}}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    old_sha = str(old_head.stdout or "").strip()
    if old_head.returncode != 0 or not old_sha:
        return False, "recovery_branch_tip_unresolved", None, None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ref_slug = _task_id_slug(task_id)
    recovery_ref = f"refs/pantheon/recovery/{ref_slug}/{stamp}-{os.getpid()}"
    backup = subprocess.run(
        ["git", "update-ref", recovery_ref, branch_ref],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if backup.returncode != 0:
        return False, "recovery_branch_backup_failed", None, None

    archive_dir = _archive_dirty_worktree(
        worktree_path,
        archive_root,
        reason="recovery_replacement_stale_dirty_wip",
        max_file_bytes=max_file_bytes,
    )
    if archive_dir is None:
        subprocess.run(
            ["git", "update-ref", recovery_ref],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        return False, "recovery_wip_archive_failed", None, None

    try:
        manifest_path = archive_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["preserved_branch_ref"] = recovery_ref
        manifest["preserved_branch_head"] = old_sha
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError):
        # The patch/file archive remains useful even if the optional metadata
        # enrichment fails; do not silently discard the recovery ref.
        pass

    removed = _remove_worker_worktree(repo_root, worktree_path, force=True)
    if removed.returncode != 0:
        return False, "recovery_worktree_remove_failed", archive_dir, recovery_ref

    reset = subprocess.run(
        ["git", "update-ref", branch_ref, base_sha],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if reset.returncode != 0:
        # Keep the branch recoverable at its old tip if resetting the task
        # branch fails after the worktree was removed.
        subprocess.run(
            ["git", "update-ref", branch_ref, old_sha],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        return False, "recovery_branch_reset_failed", archive_dir, recovery_ref

    return True, "replaced_from_base", archive_dir, recovery_ref


def worker_worktree_base_relation(worktree_path: Path, base_sha: str) -> str:
    """Describe a task branch's relationship to one immutable base snapshot."""

    head_contains_base = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_sha, "HEAD"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if head_contains_base.returncode == 0:
        return "contains_base"
    head_is_base_ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "HEAD", base_sha],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if head_is_base_ancestor.returncode == 0:
        return "behind_base"
    return "diverged"


def prepare_worker_workspace(
    config: dict[str, Any],
    state: dict[str, Any],
    request: DeliveryRequest,
    *,
    queue_event_id: str | None,
    target_agent: str | None,
    worker_base_snapshots: dict[str, dict[str, str]] | None = None,
) -> tuple[bool, str | None]:
    """Lease one isolated task worktree from a cycle-pinned repository base."""

    # This marker is supervisor-derived authority, never queue input. Strip any
    # inherited/request-supplied value before independently proving adoption.
    request.metadata.pop("fenced_dirty_wip_adoption", None)
    settings = worker_worktree_settings(config)
    workspace_task_id = worker_workspace_task_id(request)
    if not workspace_task_id:
        message = "Cannot dispatch without a task-scoped isolated worktree identity."
        write_activity_log(
            config,
            {
                "type": "dispatch_blocked_worktree_lease",
                "task_id": request.task_id,
                "target_agent": target_agent,
                "queue_event_id": queue_event_id,
                "message": message,
                "refresh_status": "missing_workspace_task_id",
            },
        )
        return False, message
    try:
        repository_id = worker_request_repository_id(config, request)
    except RuntimeError as exc:
        message = (
            f"Cannot lease isolated worker worktree for {workspace_task_id}: {exc}."
        )
        write_activity_log(
            config,
            {
                "type": "dispatch_blocked_worktree_lease",
                "task_id": request.task_id,
                "workspace_task_id": workspace_task_id,
                "target_agent": target_agent,
                "queue_event_id": queue_event_id,
                "message": message,
                "refresh_status": "delivery_repository_invalid",
            },
        )
        return False, message

    snapshot_cache = worker_base_snapshots if worker_base_snapshots is not None else {}
    base_snapshot, snapshot_error = resolve_worker_base_snapshot(
        config,
        repository_id,
        snapshot_cache,
    )
    if base_snapshot is None:
        message = (
            f"Cannot lease isolated worker worktree for {workspace_task_id}: "
            f"{snapshot_error or 'base snapshot unavailable'}."
        )
        write_activity_log(
            config,
            {
                "type": "dispatch_blocked_worktree_lease",
                "task_id": request.task_id,
                "workspace_task_id": workspace_task_id,
                "target_agent": target_agent,
                "queue_event_id": queue_event_id,
                "message": message,
                "workspace_repository_id": repository_id,
                "refresh_status": "base_snapshot_unavailable",
            },
        )
        return False, message

    source_root = Path(base_snapshot["source_root"])
    base_ref = base_snapshot["base_ref"]
    base_sha = base_snapshot["base_sha"]
    base_fetched_at = base_snapshot["fetched_at"]
    if request.metadata.get("workspace_path"):
        status_root = config_path(config, "status_file").parents[0].resolve()
        raw_workspace_path = Path(
            os.path.expanduser(str(request.metadata["workspace_path"]))
        )
        try:
            if not raw_workspace_path.is_absolute():
                raise RuntimeError("workspace_path must be absolute")
            workspace_symlink = first_symlink_component(raw_workspace_path)
            if workspace_symlink is not None:
                raise RuntimeError(
                    f"workspace_path contains a symlink component: {workspace_symlink}"
                )
            workspace_path = raw_workspace_path.resolve()
            if workspace_path in {status_root, source_root}:
                raise RuntimeError(
                    "workspace_path resolves to the shared supervisor or repository source checkout"
                )
            validate_worker_workspace_binding(
                source_root,
                workspace_path,
                expected_branch=str(
                    request.metadata.get("workspace_branch")
                    or worker_task_branch(config, workspace_task_id)
                ),
            )
        except RuntimeError as exc:
            message = (
                f"Cannot dispatch existing workspace for {workspace_task_id}: {exc}. "
                "Refusing unregistered checkout fallback."
            )
            write_activity_log(
                config,
                {
                    "type": "dispatch_blocked_worktree_lease",
                    "task_id": request.task_id,
                    "workspace_task_id": workspace_task_id,
                    "target_agent": target_agent,
                    "queue_event_id": queue_event_id,
                    "message": message,
                    "workspace_path": str(raw_workspace_path),
                    "refresh_status": "workspace_binding_rejected",
                },
            )
            return False, message
        request.metadata.update(
            {
                "workspace_mode": "isolated_worktree",
                "workspace_path": str(workspace_path),
                "workspace_repository_id": repository_id,
                "workspace_source_root": str(source_root),
                "workspace_base_ref": base_ref,
                "workspace_base_sha": base_sha,
                "workspace_base_fetched_at": base_fetched_at,
                "workspace_base_relation": worker_worktree_base_relation(workspace_path, base_sha),
            }
        )
        return True, None

    status_root = config_path(config, "status_file").parents[0].resolve()
    repo_root = source_root
    branch = worker_task_branch(config, workspace_task_id)
    worktree_path = worker_task_worktree_path(
        config,
        workspace_task_id,
        settings,
        repository_id=repository_id,
    )
    reused = False
    creation_origin: str | None = None
    recovery_wip_archive: Path | None = None
    recovery_wip_ref: str | None = None
    leases = state.setdefault("worker_worktrees", {}).setdefault("leases", {})
    if not isinstance(leases, dict):
        leases = {}
        state["worker_worktrees"]["leases"] = leases

    existing = _existing_worktree_for_branch(repo_root, branch, exclude_root=True)
    if existing:
        worktree_path = existing
        stale_adopted_wip = _recovery_worktree_has_stale_adopted_wip(
            state,
            task_id=workspace_task_id,
            worktree_path=worktree_path,
            recovery_receipt_id=str(
                request.metadata.get("recovery_receipt_id") or ""
            ),
        )
        if stale_adopted_wip and _git_dirty_entries(worktree_path):
            active_roots = active_worker_workspace_roots(config, state)
            if any(_paths_overlap(worktree_path, active) for active in active_roots):
                message = (
                    f"Cannot replace recovery worktree for {workspace_task_id}: "
                    f"{worktree_path} is still owned by an active worker."
                )
                write_activity_log(
                    config,
                    {
                        "type": "dispatch_blocked_worktree_lease",
                        "task_id": request.task_id,
                        "workspace_task_id": workspace_task_id,
                        "target_agent": target_agent,
                        "queue_event_id": queue_event_id,
                        "message": message,
                        "workspace_branch": branch,
                        "workspace_path": str(worktree_path),
                        "refresh_status": "active_stale_adopted_wip",
                    },
                )
                return False, message
            cleanup_settings = worktree_cleanup_settings(config)
            replaced, replace_status, archive_dir, recovery_ref = (
                _replace_recovery_worktree_from_base(
                    repo_root,
                    worktree_path,
                    branch=branch,
                    base_sha=base_sha,
                    archive_root=_recovery_worktree_archive_root(config),
                    task_id=workspace_task_id,
                    max_file_bytes=int(cleanup_settings["archive_max_file_bytes"]),
                )
            )
            write_activity_log(
                config,
                {
                    "type": "worker_worktree_replaced_after_recovery_wip",
                    "task_id": request.task_id,
                    "workspace_task_id": workspace_task_id,
                    "target_agent": target_agent,
                    "queue_event_id": queue_event_id,
                    "workspace_branch": branch,
                    "workspace_path": str(worktree_path),
                    "workspace_base_sha": base_sha,
                    "replace_ok": replaced,
                    "replace_status": replace_status,
                    "archive_path": str(archive_dir) if archive_dir else None,
                    "preserved_branch_ref": recovery_ref,
                },
            )
            if not replaced:
                return False, (
                    f"Cannot replace stale recovery worktree for {workspace_task_id}: "
                    f"{replace_status}."
                )
            recovery_wip_archive = archive_dir
            recovery_wip_ref = recovery_ref
            leases.pop(workspace_task_id, None)
        else:
            reused = True
            lost_lease_wip_adoption = _lost_lease_replacement_may_adopt_worktree(
                config,
                state,
                request,
                task_id=workspace_task_id,
                repository_id=repository_id,
                source_root=repo_root,
                branch=branch,
                worktree_path=worktree_path,
                base_ref=base_ref,
                queue_event_id=queue_event_id,
                target_agent=target_agent,
            )
            refresh_ok, refresh_status = _refresh_reused_worker_worktree(
                worktree_path,
                base_sha,
                task_id=workspace_task_id,
                branch=branch,
                allow_dirty_wip_adoption=lost_lease_wip_adoption,
            )
            if refresh_status == "adopted_lost_lease_dirty_wip":
                request.metadata["fenced_dirty_wip_adoption"] = {
                    "receipt_id": str(
                        request.metadata.get("recovery_receipt_id") or ""
                    ),
                    "task_id": workspace_task_id,
                    "task_generation": request.metadata.get("task_generation"),
                    "queue_event_id": queue_event_id,
                    "repository_id": repository_id,
                    "branch": branch,
                    "workspace_path": str(worktree_path),
                }
            write_activity_log(
                config,
                {
                    "type": "worker_worktree_refreshed",
                    "task_id": request.task_id,
                    "target_agent": target_agent,
                    "queue_event_id": queue_event_id,
                    "workspace_branch": branch,
                    "workspace_path": str(worktree_path),
                    "status_root": str(status_root),
                    "workspace_source_root": str(repo_root),
                    "workspace_repository_id": repository_id,
                    "workspace_base_ref": base_ref,
                    "workspace_base_sha": base_sha,
                    "refresh_ok": refresh_ok,
                    "refresh_status": refresh_status,
                    "recovery_receipt_id": (
                        request.metadata.get("recovery_receipt_id")
                        if refresh_status == "adopted_lost_lease_dirty_wip"
                        else None
                    ),
                },
            )
            if not refresh_ok and refresh_status == "skipped_dirty_worktree":
                message = (
                    f"Cannot lease isolated worker worktree for {workspace_task_id}: "
                    f"reused worktree {worktree_path} has dirty tracked or staged changes. "
                    "Clean or remove that worktree before dispatch."
                )
                write_activity_log(
                    config,
                    {
                        "type": "dispatch_blocked_worktree_lease",
                        "task_id": request.task_id,
                        "workspace_task_id": workspace_task_id,
                        "target_agent": target_agent,
                        "queue_event_id": queue_event_id,
                        "message": message,
                        "workspace_branch": branch,
                        "workspace_path": str(worktree_path),
                        "status_root": str(status_root),
                        "workspace_source_root": str(repo_root),
                        "base_sha": base_sha,
                        "refresh_status": refresh_status,
                    },
                )
                return False, message

    if not reused:
        if _branch_checked_out_in_root(repo_root, branch):
            message = (
                f"Cannot lease isolated worker worktree for {workspace_task_id}: "
                f"branch {branch} is currently checked out in supervisor root {repo_root}. "
                "Move the supervisor root back to dev or finish that root task branch first."
            )
            write_activity_log(
                config,
                {
                    "type": "dispatch_blocked_worktree_lease",
                    "task_id": request.task_id,
                    "workspace_task_id": workspace_task_id,
                    "target_agent": target_agent,
                    "queue_event_id": queue_event_id,
                    "message": message,
                    "workspace_branch": branch,
                    "workspace_path": str(worktree_path),
                    "status_root": str(status_root),
                    "workspace_source_root": str(repo_root),
                },
            )
            return False, message
        created, error, creation_origin = _create_worker_worktree(
            repo_root,
            worktree_path,
            branch,
            base_sha,
        )
        if not created:
            message = error or f"Failed to create worker worktree for {workspace_task_id}."
            write_activity_log(
                config,
                {
                    "type": "dispatch_blocked_worktree_lease",
                    "task_id": request.task_id,
                    "workspace_task_id": workspace_task_id,
                    "target_agent": target_agent,
                    "queue_event_id": queue_event_id,
                    "message": message,
                    "workspace_branch": branch,
                    "workspace_path": str(worktree_path),
                    "status_root": str(status_root),
                    "workspace_source_root": str(repo_root),
                },
            )
            return False, message
        if creation_origin != "base_snapshot":
            # A local or remote task branch can outlive its old worktree.  Do
            # the same safe ff-only refresh used for an existing worktree,
            # but never fetch a moving ref again in this cycle.
            refresh_ok, refresh_status = _refresh_reused_worker_worktree(
                worktree_path,
                base_sha,
                task_id=workspace_task_id,
                branch=branch,
            )
            write_activity_log(
                config,
                {
                    "type": "worker_worktree_refreshed",
                    "task_id": request.task_id,
                    "target_agent": target_agent,
                    "queue_event_id": queue_event_id,
                    "workspace_branch": branch,
                    "workspace_path": str(worktree_path),
                    "status_root": str(status_root),
                    "workspace_source_root": str(repo_root),
                    "workspace_repository_id": repository_id,
                    "workspace_base_ref": base_ref,
                    "workspace_base_sha": base_sha,
                    "refresh_ok": refresh_ok,
                    "refresh_status": refresh_status,
                },
            )
            if not refresh_ok and refresh_status == "skipped_dirty_worktree":
                message = (
                    f"Cannot lease isolated worker worktree for {workspace_task_id}: "
                    f"new worktree {worktree_path} could not be refreshed safely."
                )
                return False, message

    base_relation = (
        "exact_base" if not reused and creation_origin == "base_snapshot"
        else worker_worktree_base_relation(worktree_path, base_sha)
    )

    request.metadata.update(
        {
            "workspace_mode": "isolated_worktree",
            "workspace_path": str(worktree_path),
            "workspace_branch": branch,
            "status_root": str(status_root),
            "workspace_source_root": str(repo_root),
            "workspace_repository_id": repository_id,
            "workspace_base_ref": base_ref,
            "workspace_base_sha": base_sha,
            "workspace_base_fetched_at": base_fetched_at,
            "workspace_base_relation": base_relation,
        }
    )
    if repository_id == "pantheon":
        materialized_context_files = materialize_worker_context_files(
            config, request, worktree_path
        )
    else:
        materialized_context_files = bind_external_worker_context(
            config, request, repository_id
        )
    leases[workspace_task_id] = {
        "task_id": request.task_id,
        "workspace_task_id": workspace_task_id,
        "branch": branch,
        "path": str(worktree_path),
        "status_root": str(status_root),
        "source_root": str(repo_root),
        "repository_id": repository_id,
        "base_ref": base_ref,
        "base_sha": base_sha,
        "base_fetched_at": base_fetched_at,
        "base_relation": base_relation,
        "last_queue_event_id": queue_event_id,
        "last_target_agent": target_agent,
        "last_used_at": utc_now(),
        "materialized_context_files": materialized_context_files,
    }
    if adopted_dirty_wip_receipt_id := str(
        request.metadata.get("recovery_receipt_id") or ""
    ).strip():
        # Persist every recovery handoff, including a clean one. This closes
        # the legacy gap where an adopted tree had no durable receipt and a
        # later lost lease could inherit it again.
        leases[workspace_task_id]["recovery_receipt_id"] = (
            adopted_dirty_wip_receipt_id
        )
        leases[workspace_task_id]["recovery_started_at"] = utc_now()
        if request.metadata.get("fenced_dirty_wip_adoption"):
            leases[workspace_task_id]["dirty_wip_adoption_receipt_id"] = (
                adopted_dirty_wip_receipt_id
            )
            leases[workspace_task_id]["dirty_wip_adopted_at"] = utc_now()
    if recovery_wip_archive is not None:
        leases[workspace_task_id]["replaced_recovery_wip_archive"] = str(
            recovery_wip_archive
        )
    if recovery_wip_ref is not None:
        leases[workspace_task_id]["replaced_recovery_wip_ref"] = recovery_wip_ref
    write_activity_log(
        config,
        {
            "type": "worker_worktree_reused" if reused else "worker_worktree_allocated",
            "task_id": request.task_id,
            "workspace_task_id": workspace_task_id,
            "target_agent": target_agent,
            "queue_event_id": queue_event_id,
            "workspace_branch": branch,
            "workspace_path": str(worktree_path),
            "status_root": str(status_root),
            "workspace_source_root": str(repo_root),
            "workspace_repository_id": repository_id,
            "workspace_base_ref": base_ref,
            "workspace_base_sha": base_sha,
            "workspace_base_relation": base_relation,
        },
    )
    return True, None


def _git_dirty_entries(cwd: Path | None = None) -> list[dict[str, str]]:
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=cwd or _REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    entries: list[dict[str, str]] = []
    parts = proc.stdout.split("\0")
    index = 0
    while index < len(parts):
        raw = parts[index]
        index += 1
        if not raw:
            continue
        status = raw[:2]
        path = raw[3:] if len(raw) > 3 else ""
        if not path:
            continue
        entries.append({"status": status, "path": path.replace("\\", "/")})
        if status[:1] in {"R", "C"} and index < len(parts):
            index += 1
    return entries


def isolated_workspace_commit_sha(
    workspace_mode: str | None,
    workspace_path: str | Path | None,
) -> str | None:
    """Read HEAD for a worker-owned worktree, never a shared checkout.

    A commit in a shared root cannot be attributed to one worker, so it must not
    renew that worker's lease. Isolated task worktrees provide the ownership
    boundary required for a real per-worker progress signal.
    """
    if str(workspace_mode or "").strip() != "isolated_worktree" or not workspace_path:
        return None
    try:
        path = Path(workspace_path).expanduser().resolve()
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    sha = str(result.stdout or "").strip().lower()
    return sha if re.fullmatch(r"[0-9a-f]{40,64}", sha) else None


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    except OSError:
        return False
    return True


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        left_resolved = left.resolve()
        right_resolved = right.resolve()
    except OSError:
        return False
    return _path_is_within(left_resolved, right_resolved) or _path_is_within(right_resolved, left_resolved)


def active_worker_workspace_roots(config: dict[str, Any], state: dict[str, Any]) -> set[Path]:
    active_statuses = {str(value) for value in ready_dispatch_settings(config).get("active_worker_statuses", [])}
    active_statuses.update(
        {"running", "started", "waiting_approval", "suspended_approval", "retry_backoff", "stalled"}
    )
    roots: set[Path] = set()
    for worker in state.get("workers", {}).values():
        if not isinstance(worker, dict):
            continue
        workspace_path = worker.get("workspace_path")
        if not workspace_path:
            continue
        status = str(worker.get("status") or "")
        if status not in active_statuses and not pid_is_alive(worker.get("pid")):
            continue
        try:
            roots.add(Path(str(workspace_path)).expanduser().resolve())
        except OSError:
            continue
    return roots


def _status_changed_paths(porcelain_status: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for line in porcelain_status.splitlines():
        if not line.strip():
            continue
        body = line[3:] if len(line) > 3 else line.strip()
        path = body.split(" -> ")[-1].strip().strip('"')
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def _archive_dirty_worktree(
    worktree_path: Path,
    archive_root: Path,
    *,
    reason: str,
    max_file_bytes: int,
) -> Path | None:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", worktree_path.name).strip("-") or "worktree"
    archive_dir = archive_root / f"{slug}-{timestamp}-{os.getpid()}"
    suffix = 1
    while archive_dir.exists():
        suffix += 1
        archive_dir = archive_root / f"{slug}-{timestamp}-{os.getpid()}-{suffix}"
    try:
        archive_dir.mkdir(parents=True)
    except OSError:
        return None

    def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(worktree_path), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    status_proc = run_git(["status", "--porcelain", "--untracked-files=all"])
    diff_proc = run_git(["diff", "--binary"])
    staged_diff_proc = run_git(["diff", "--cached", "--binary"])
    untracked_proc = run_git(["ls-files", "--others", "--exclude-standard"])

    (archive_dir / "status.txt").write_text(status_proc.stdout or status_proc.stderr or "", encoding="utf-8")
    (archive_dir / "diff.patch").write_text(diff_proc.stdout or diff_proc.stderr or "", encoding="utf-8")
    (archive_dir / "diff-staged.patch").write_text(
        staged_diff_proc.stdout or staged_diff_proc.stderr or "",
        encoding="utf-8",
    )
    (archive_dir / "untracked-files.txt").write_text(
        untracked_proc.stdout or untracked_proc.stderr or "",
        encoding="utf-8",
    )

    copied: list[str] = []
    skipped: list[str] = []
    files_root = archive_dir / "files"
    for rel_path in _status_changed_paths(status_proc.stdout):
        source = worktree_path / rel_path
        if not source.exists() or not source.is_file():
            skipped.append(rel_path)
            continue
        try:
            size = source.stat().st_size
        except OSError:
            skipped.append(rel_path)
            continue
        if max_file_bytes > 0 and size > max_file_bytes:
            skipped.append(f"{rel_path}\ttoo_large:{size}")
            continue
        destination = files_root / rel_path
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(rel_path)
        except OSError:
            skipped.append(rel_path)

    (archive_dir / "copied-files.txt").write_text("\n".join(copied) + ("\n" if copied else ""), encoding="utf-8")
    (archive_dir / "skipped-files.txt").write_text("\n".join(skipped) + ("\n" if skipped else ""), encoding="utf-8")
    manifest = {
        "archived_at": utc_now(),
        "worktree_path": str(worktree_path),
        "reason": reason,
        "status_returncode": status_proc.returncode,
        "copied_files": copied,
        "skipped_files": skipped,
    }
    (archive_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return archive_dir


def _merged_task_branches(repo_root: Path, base_ref: str) -> set[str]:
    merged_branches: set[str] = set()
    if not _git_ref_exists(repo_root, base_ref):
        return merged_branches
    proc = subprocess.run(
        ["git", "branch", "--merged", base_ref, "--list", "task/*"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return merged_branches
    for line in proc.stdout.splitlines():
        name = line.strip().lstrip("*").strip()
        if name:
            merged_branches.add(name)
    return merged_branches


def _remove_worker_worktree(
    repo_root: Path,
    worktree_path: Path,
    *,
    force: bool,
) -> subprocess.CompletedProcess[str]:
    command = ["git", "-C", str(repo_root), "worktree", "remove"]
    if force:
        command.append("--force")
    command.append(str(worktree_path))
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _cleanup_registered_worker_worktrees(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    source: str,
    require_merged: bool,
    include_unregistered: bool = False,
    only_workspace_paths: set[Path] | None = None,
) -> bool:
    settings = worktree_cleanup_settings(config)
    if not settings["enabled"]:
        return False
    worktree_settings = worker_worktree_settings(config)
    base_root = _worker_worktree_base_root(config, worktree_settings)
    status_root = config_path(config, "status_file").parents[0]
    leases = state.setdefault("worker_worktrees", {}).setdefault("leases", {})
    if not isinstance(leases, dict):
        return False
    normalized_only = {path.resolve() for path in only_workspace_paths} if only_workspace_paths else None
    missing_lease_paths: list[str] = []
    for workspace_id, lease in list(leases.items()):
        if not isinstance(lease, Mapping) or not lease.get("path"):
            continue
        try:
            lease_path = Path(str(lease["path"])).expanduser().resolve()
        except OSError:
            continue
        if normalized_only is not None and lease_path not in normalized_only:
            continue
        if not lease_path.exists():
            leases.pop(workspace_id, None)
            missing_lease_paths.append(str(lease_path))
    if not base_root.exists():
        if not missing_lease_paths:
            return False
        state.setdefault("worker_worktree_cleanup", {})["last_run"] = {
            "at": utc_now(),
            "source": source,
            "status_root": str(status_root.resolve()),
            "checked": len(missing_lease_paths),
            "removed": 0,
            "skipped": 0,
            "active": 0,
            "archived": 0,
            "failed": 0,
            "missing_leases": len(missing_lease_paths),
            "stale_unmerged": 0,
            "details": [
                {"path": path, "disposition": "missing_lease_removed"}
                for path in missing_lease_paths
            ],
        }
        return True
    active_roots = active_worker_workspace_roots(config, state)
    live_paths = _scan_process_paths_in_root(base_root)
    max_removals = max(0, int(settings["max_removals_per_tick"]))
    archive_root = Path(os.path.expanduser(str(settings["archive_root"])))
    if not archive_root.is_absolute():
        archive_root = status_root / archive_root

    repository_sources: dict[Path, tuple[str, str]] = {}

    def add_repository_source(
        repository_id: str,
        source_root: Path,
        base_ref: str,
    ) -> None:
        repository_sources.setdefault(source_root.resolve(), (repository_id, base_ref))

    registered_repository_ids = (
        list(repositories(config)) if include_unregistered else ["pantheon"]
    )
    for repository_id in registered_repository_ids:
        try:
            source_root = worker_worktree_source_root(
                config,
                repository_id=repository_id,
            )
            base_ref = worker_repository_base_ref(config, repository_id)
        except RuntimeError:
            continue
        if source_root.is_dir():
            add_repository_source(repository_id, source_root, base_ref)

    for lease in leases.values():
        if not isinstance(lease, dict):
            continue
        repository_id = str(lease.get("repository_id") or "pantheon")
        try:
            source_root = Path(
                str(
                    lease.get("source_root")
                    or worker_worktree_source_root(config, repository_id=repository_id)
                )
            ).expanduser().resolve()
            base_ref = str(
                lease.get("base_ref")
                or worker_repository_base_ref(config, repository_id)
            )
        except RuntimeError:
            continue
        if source_root.is_dir():
            add_repository_source(repository_id, source_root, base_ref)

    records_by_path: dict[Path, tuple[dict[str, str], Path]] = {}
    merged_by_root: dict[Path, set[str]] = {}
    for repository_root, (_repository_id, base_ref) in repository_sources.items():
        if not repository_root.is_dir():
            continue
        if require_merged:
            merged_by_root[repository_root] = _merged_task_branches(
                repository_root, base_ref
            )
        for record in _git_worktree_records(repository_root):
            wt_value = record.get("worktree")
            if not wt_value:
                continue
            try:
                wt_path = Path(wt_value).expanduser().resolve()
            except OSError:
                continue
            records_by_path[wt_path] = (record, repository_root)

    candidates: list[
        tuple[str | None, dict[str, Any], Path, str | None, Path]
    ] = []
    candidate_paths: set[Path] = set()
    for workspace_id, lease in list(leases.items()):
        if not isinstance(lease, dict):
            continue
        path_value = lease.get("path")
        if not path_value:
            continue
        try:
            wt_path = Path(str(path_value)).expanduser().resolve()
        except OSError:
            continue
        if not _path_is_within(wt_path, base_root):
            continue
        if normalized_only is not None and wt_path not in normalized_only:
            continue
        record_binding = records_by_path.get(wt_path)
        record = record_binding[0] if record_binding is not None else {}
        repository_id = str(lease.get("repository_id") or "pantheon")
        try:
            lease_source = Path(
                str(
                    lease.get("source_root")
                    or worker_worktree_source_root(config, repository_id=repository_id)
                )
            ).expanduser().resolve()
        except RuntimeError:
            continue
        repository_root = record_binding[1] if record_binding is not None else lease_source
        branch = str(lease.get("branch") or _worktree_record_branch(record) or "")
        candidates.append(
            (str(workspace_id), lease, wt_path, branch, repository_root)
        )
        candidate_paths.add(wt_path)

    if include_unregistered:
        for wt_path, (record, repository_root) in records_by_path.items():
            if wt_path in candidate_paths or not _path_is_within(wt_path, base_root):
                continue
            if normalized_only is not None and wt_path not in normalized_only:
                continue
            candidates.append(
                (None, {}, wt_path, _worktree_record_branch(record), repository_root)
            )

    summary: dict[str, Any] = {
        "at": utc_now(),
        "source": source,
        "status_root": str(status_root.resolve()),
        "workspace_source_roots": sorted(str(root) for root in repository_sources),
        "checked": 0,
        "removed": 0,
        "skipped": 0,
        "active": 0,
        "archived": 0,
        "failed": 0,
        "missing_leases": len(missing_lease_paths),
        "stale_unmerged": 0,
        "details": [
            {"path": path, "disposition": "missing_lease_removed"}
            for path in missing_lease_paths
        ],
    }
    changed = bool(missing_lease_paths)
    removed_paths: list[str] = []
    for workspace_id, _lease, wt_path, branch, repository_root in candidates:
        if summary["removed"] >= max_removals and wt_path.exists():
            break
        summary["checked"] += 1
        if any(_paths_overlap(wt_path, active) for active in active_roots) or any(
            _paths_overlap(wt_path, live) for live in live_paths
        ):
            summary["active"] += 1
            continue
        if require_merged and (
            not branch or branch not in merged_by_root.get(repository_root, set())
        ):
            max_age_days = settings["orphan_unmerged_max_age_days"]
            stale_enough = False
            if max_age_days > 0:
                last_active = _worktree_last_activity_epoch(repository_root, branch, wt_path)
                if last_active is not None:
                    age_days = (time.time() - last_active) / 86400.0
                    stale_enough = age_days >= max_age_days
            if not stale_enough:
                summary["skipped"] += 1
                continue
            summary["stale_unmerged"] += 1
        if not wt_path.exists():
            if workspace_id is not None:
                leases.pop(workspace_id, None)
                summary["missing_leases"] += 1
                changed = True
            continue

        status_proc = subprocess.run(
            ["git", "-C", str(wt_path), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status_proc.returncode != 0:
            summary["failed"] += 1
            summary["details"].append({"path": str(wt_path), "error": (status_proc.stderr or status_proc.stdout or "").strip()})
            continue

        force_remove = False
        if status_proc.stdout.strip():
            if not settings["archive_dirty_worktrees"]:
                summary["skipped"] += 1
                continue
            archive_dir = _archive_dirty_worktree(
                wt_path,
                archive_root,
                reason=source,
                max_file_bytes=int(settings["archive_max_file_bytes"]),
            )
            if archive_dir is None:
                summary["failed"] += 1
                summary["details"].append({"path": str(wt_path), "error": "archive_failed"})
                continue
            force_remove = bool(settings["force_remove_archived_dirty"])
            summary["archived"] += 1
            summary["details"].append({"path": str(wt_path), "archive": str(archive_dir)})
            if not force_remove:
                summary["skipped"] += 1
                continue

        remove_proc = _remove_worker_worktree(
            repository_root, wt_path, force=force_remove
        )
        if remove_proc.returncode != 0:
            summary["failed"] += 1
            summary["details"].append(
                {"path": str(wt_path), "error": (remove_proc.stderr or remove_proc.stdout or "").strip()}
            )
            continue
        if workspace_id is not None:
            leases.pop(workspace_id, None)
        summary["removed"] += 1
        removed_paths.append(str(wt_path))
        changed = True

    if changed or summary["checked"]:
        bucket = state.setdefault("worker_worktree_cleanup", {})
        bucket["last_run"] = summary
    if removed_paths:
        write_activity_log(
            config,
            {
                "type": "worktree_pruned",
                "message": f"Pruned {len(removed_paths)} worker worktree(s): {', '.join(removed_paths)}",
                "source": source,
                "archived": summary["archived"],
                "failed": summary["failed"],
            },
        )
    return changed


def cleanup_inactive_worker_worktrees(config: dict[str, Any], state: dict[str, Any]) -> bool:
    settings = worktree_cleanup_settings(config)
    if not settings["cleanup_inactive_leases"]:
        return False
    return _cleanup_registered_worker_worktrees(
        config,
        state,
        source="worker_lifecycle",
        require_merged=False,
        include_unregistered=False,
    )


def _scan_process_paths_in_root(base_root: Path) -> set[Path]:
    """Return resolved paths under base_root mentioned in any live process cmdline."""
    base_str = str(base_root)
    referenced: set[Path] = set()
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return referenced
    self_pid = os.getpid()
    for entry in entries:
        name = entry.name
        if not name.isdigit():
            continue
        if int(name) == self_pid:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore")
        if base_str not in cmdline:
            continue
        for tok in cmdline.split(" "):
            if tok.startswith(base_str):
                try:
                    referenced.add(Path(tok).resolve())
                except OSError:
                    pass
    return referenced


def prune_orphan_worktrees(config: dict[str, Any], state: dict[str, Any]) -> bool:
    """Remove finished worker worktrees whose branches are merged."""
    settings = worktree_cleanup_settings(config)
    interval = settings["orphan_prune_interval_seconds"]
    bucket = state.setdefault("worker_worktree_cleanup", {})
    if interval > 0:
        last_at = bucket.get("last_orphan_prune_at")
        last_dt = _parse_iso_utc(str(last_at or ""))
        now = datetime.now(timezone.utc)
        if last_dt is not None and (now - last_dt).total_seconds() < interval:
            return False
    bucket["last_orphan_prune_at"] = utc_now()
    return _cleanup_registered_worker_worktrees(
        config,
        state,
        source="worker_worktree_cleanup",
        require_merged=True,
        include_unregistered=True,
    )
