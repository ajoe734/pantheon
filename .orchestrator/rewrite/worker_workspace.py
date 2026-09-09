"""DTG-CLEAN-M4: worker workspace filesystem owner.

Worktree preparation, safe reuse/refresh, dirt classification, dirty
archive, registered-lease cleanup, and orphan pruning for worker
worktrees, moved out of .orchestrator/supervisor.py. Operates on
explicit config/state/path inputs and git worktree state. Recovery reads
canonical eligibility and delegates receipt publication to the supervisor's
existing TaskStore transaction; it never owns task transitions or dispatch.
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
import hashlib
import os
import re
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from adapters.base import DeliveryRequest
from common import (
    _fsync_directory,
    config_path,
    durable_write_bytes,
    first_symlink_component,
    load_status,
    normalize_github_repo_slug,
    read_regular_file_bytes,
    read_regular_file_snapshot,
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
from rewrite.worker_recovery import (
    _canonical_worker_recovery_receipt,
    worker_recovery_workspace_facts,
)

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


def _lost_lease_replacement_may_recover_worktree(
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
    """Authorize WIP quarantine only for the exact fenced replacement.

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
    role = str(receipt.get("recovery_role") or "")
    if (
        role not in {"owner", "reviewer"}
        or supervisor.task_current_dispatch_responsibility(config, task) != role
        or replacement.get("role") != role
    ):
        return False
    expected_actor = str(replacement.get("agent") or "")
    actual_actor = supervisor.canonical_agent_name(config, str(target_agent or ""))
    if (
        not expected_actor
        or supervisor.canonical_agent_name(config, expected_actor) != actual_actor
        or supervisor.canonical_agent_name(config, str(task.get(role) or "")) != actual_actor
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
    tracked changes. A qualified lost-lease replacement quarantines its WIP
    through the single recovery path before reaching this ordinary refresh.
    """
    status_proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    scratch_restored = False
    index_restored = False
    if status_proc.returncode != 0:
        return False, "worktree_status_failed"
    if status_proc.returncode == 0 and status_proc.stdout.strip():
        classification, scratch_paths = _classify_worktree_dirt(status_proc.stdout)
        if classification == "real":
            index_split_paths = _staged_index_split_paths_matching_head(worktree_path)
            if index_split_paths and _restore_reused_index_split(worktree_path, index_split_paths):
                index_restored = True
            # Repair only the index split; genuine source WIP still blocks an
            # ordinary dispatch. The supervisor never invents an anchor commit.
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
            verify_proc = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                check=False,
            )
            if verify_proc.returncode != 0 or verify_proc.stdout.strip():
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
    if worker_worktree_base_relation(worktree_path, base_sha) == "diverged":
        return False, "skipped_non_fast_forward"
    return False, f"merge_failed: {details}"


def _recovery_worktree_archive_root(config: dict[str, Any]) -> Path:
    """Resolve the managed archive root used for recovery WIP snapshots."""

    settings = worktree_cleanup_settings(config)
    archive_root = Path(os.path.expanduser(str(settings["archive_root"])))
    if not archive_root.is_absolute():
        archive_root = config_path(config, "status_file").parents[0] / archive_root
    return archive_root.resolve()


def _quarantine_recovery_worktree(
    repo_root: Path,
    worktree_path: Path,
    *,
    branch: str,
    archive_root: Path,
    task_id: str,
    repository_id: str,
    max_file_bytes: int,
    publish_archive: Callable[[dict[str, Any]], bool],
    existing_archive: Mapping[str, Any] | None = None,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Quarantine WIP without changing the committed task branch.

    The canonical recovery receipt records the complete archive BEFORE any
    source restoration. Retrying that exact binding resumes the same operation;
    ignored files, committed source, and other worktrees are never removed.
    """
    def git(*args: str, raw: bool = False) -> subprocess.CompletedProcess[Any]:
        return subprocess.run(
            ["git", "-C", str(worktree_path), *args],
            capture_output=True, text=not raw, check=False,
        )

    head = git("rev-parse", "--verify", "HEAD^{commit}")
    checked_branch = git("symbolic-ref", "--quiet", "--short", "HEAD")
    if head.returncode or checked_branch.returncode or checked_branch.stdout.strip() != branch:
        return False, "recovery_branch_identity_mismatch", None
    current_head = head.stdout.strip()
    source_head = current_head
    binding = dict(existing_archive) if existing_archive is not None else None
    try:
        if binding is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            ref_slug = _task_id_slug(task_id)
            recovery_ref = f"refs/pantheon/recovery/{ref_slug}/{stamp}-{os.getpid()}-{time.time_ns()}"
            backup = subprocess.run(
                ["git", "-C", str(repo_root), "update-ref", recovery_ref, source_head, ""],
                capture_output=True, text=True, check=False,
            )
            if backup.returncode:
                return False, "recovery_branch_backup_failed", None
            archive_dir = _archive_dirty_worktree(
                worktree_path, archive_root,
                reason="recovery_uncommitted_wip_quarantine",
                max_file_bytes=max_file_bytes,
            )
            if archive_dir is None:
                return False, "recovery_wip_archive_failed", None
            binding = {
                "repository_id": repository_id,
                "workspace_path": str(worktree_path),
                "branch": branch,
                "source_head": source_head,
                "archive_path": str(archive_dir),
                "preserved_branch_ref": recovery_ref,
            }
            manifest_path = archive_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.update(preserved_branch_ref=recovery_ref, preserved_branch_head=source_head)
            durable_write_bytes(
                manifest_path,
                (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            )
        else:
            source_head = str(binding.get("source_head") or "")
            archive_dir = Path(str(binding.get("archive_path") or ""))
            if (
                binding.get("repository_id") != repository_id
                or binding.get("branch") != branch
                or re.fullmatch(r"[0-9a-f]{40,64}", source_head) is None
                or Path(str(binding.get("workspace_path") or "")).resolve() != worktree_path.resolve()
                or not archive_dir.resolve().is_relative_to(archive_root.resolve())
                or archive_dir.resolve() == archive_root.resolve()
                or first_symlink_component(archive_dir) is not None
                or not str(binding.get("preserved_branch_ref") or "").startswith(
                    f"refs/pantheon/recovery/{_task_id_slug(task_id)}/"
                )
            ):
                return False, "recovery_archive_binding_mismatch", binding
            manifest = json.loads((archive_dir / "manifest.json").read_text(encoding="utf-8"))

        if not isinstance(manifest, dict) or manifest.get("complete") is not True:
            return False, "recovery_wip_archive_incomplete", binding
        if (
            manifest.get("preserved_branch_head") != source_head
            or manifest.get("preserved_branch_ref") != binding["preserved_branch_ref"]
            or Path(str(manifest.get("worktree_path") or "")).resolve() != worktree_path.resolve()
        ):
            return False, "recovery_archive_identity_mismatch", binding
        preserved = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--verify",
             f"{binding['preserved_branch_ref']}^{{commit}}"],
            capture_output=True, text=True, check=False,
        )
        if preserved.returncode or preserved.stdout.strip() != source_head:
            return False, "recovery_source_ref_mismatch", binding
        current_status = git("status", "--porcelain", "--untracked-files=all")
        current_diff = git("diff", "--binary", raw=True)
        current_staged = git("diff", "--cached", "--binary", raw=True)
        current_untracked = git("ls-files", "--others", "--exclude-standard", "-z")
        if any(item.returncode for item in (current_status, current_diff, current_staged, current_untracked)):
            return False, "recovery_source_read_failed", binding
        if current_head != source_head:
            # A completed quarantine may have reached the ordinary fast-forward
            # before preparation was interrupted. Validate the historical
            # archive without rewriting that legitimate committed advancement.
            # Dirty continuation still requires the exact archived source HEAD.
            ancestry = git("merge-base", "--is-ancestor", source_head, current_head)
            if current_status.stdout.strip() or ancestry.returncode != 0:
                return False, "recovery_branch_changed_after_archive", binding
        untracked_paths = manifest["untracked_paths"]
        checksums = manifest["file_checksums"]
        file_modes = manifest["file_modes"]
        archive_checksums = manifest["archive_checksums"]
        if (
            not isinstance(untracked_paths, list)
            or not isinstance(checksums, dict)
            or not isinstance(file_modes, dict)
            or not isinstance(archive_checksums, dict)
            or not all(isinstance(path, str) for path in untracked_paths)
            or len(set(untracked_paths)) != len(untracked_paths)
            or not set(untracked_paths).issubset(checksums)
            or set(file_modes) != set(checksums)
        ):
            return False, "recovery_archive_invalid", binding
        archived_bytes: dict[str, bytes] = {}
        for name in ("status.txt", "diff.patch", "diff-staged.patch", "untracked-files.txt"):
            payload = read_regular_file_bytes(archive_dir / name, source="recovery archive")
            if hashlib.sha256(payload).hexdigest() != archive_checksums.get(name):
                return False, "recovery_archive_checksum_mismatch", binding
            archived_bytes[name] = payload
        for rel_path, checksum in checksums.items():
            if (
                not isinstance(rel_path, str) or not rel_path
                or Path(rel_path).is_absolute() or ".." in Path(rel_path).parts
                or not isinstance(checksum, str) or re.fullmatch(r"[0-9a-f]{64}", checksum) is None
                or not isinstance(file_modes[rel_path], int)
            ):
                return False, "recovery_archive_invalid", binding
            archived = archive_dir / "files" / rel_path
            if not archived.resolve().is_relative_to((archive_dir / "files").resolve()):
                return False, "recovery_archive_invalid", binding
            payload, archived_stat = read_regular_file_snapshot(archived, source="recovery archive")
            if (
                hashlib.sha256(payload).hexdigest() != checksum
                or stat.S_IMODE(archived_stat.st_mode) != file_modes[rel_path]
            ):
                return False, "recovery_archive_checksum_mismatch", binding
        same_snapshot = (
            current_status.stdout.encode("utf-8") == archived_bytes["status.txt"]
            and current_diff.stdout == archived_bytes["diff.patch"]
            and current_staged.stdout == archived_bytes["diff-staged.patch"]
        )
        remaining_untracked = [item for item in current_untracked.stdout.split("\0") if item]
        resumed_after_restore = (
            existing_archive is not None
            and not current_diff.stdout and not current_staged.stdout
            and set(remaining_untracked).issubset(untracked_paths)
        )
        if not same_snapshot and not resumed_after_restore:
            return False, "recovery_wip_changed_after_archive", binding
        checked_paths = list(checksums) if same_snapshot else remaining_untracked
        for rel_path in checked_paths:
            source = worktree_path / rel_path
            if not source.resolve().is_relative_to(worktree_path.resolve()):
                return False, "recovery_wip_changed_after_archive", binding
            payload, source_stat = read_regular_file_snapshot(source, source="recovery WIP")
            if (hashlib.sha256(payload).hexdigest() != checksums[rel_path]
                    or stat.S_IMODE(source_stat.st_mode) != file_modes[rel_path]):
                return False, "recovery_wip_changed_after_archive", binding
        verified_head = git("rev-parse", "HEAD")
        if verified_head.returncode or verified_head.stdout.strip() != current_head:
            return False, "recovery_branch_changed_after_archive", binding
        if not publish_archive(binding):
            return False, "recovery_archive_publication_failed", binding

        # Publication can block on canonical persistence and projection. A
        # source or index edit during that interval must not be overwritten by
        # restoration from the earlier archive.
        published_status = git("status", "--porcelain", "--untracked-files=all")
        published_diff = git("diff", "--binary", raw=True)
        published_staged = git("diff", "--cached", "--binary", raw=True)
        published_untracked = git("ls-files", "--others", "--exclude-standard", "-z")
        published_branch = git("symbolic-ref", "--quiet", "--short", "HEAD")
        published_head = git("rev-parse", "HEAD")
        if any(item.returncode for item in (
            published_status, published_diff, published_staged,
            published_untracked, published_branch, published_head,
        )):
            return False, "recovery_source_read_failed", binding
        if (published_head.stdout.strip() != current_head
                or published_branch.stdout.strip() != branch):
            return False, "recovery_branch_changed_after_publication", binding
        if any(before.stdout != after.stdout for before, after in (
            (current_status, published_status), (current_diff, published_diff),
            (current_staged, published_staged), (current_untracked, published_untracked),
        )):
            return False, "recovery_wip_changed_after_publication", binding
        for rel_path in checked_paths:
            source = worktree_path / rel_path
            if not source.resolve().is_relative_to(worktree_path.resolve()):
                return False, "recovery_wip_changed_after_publication", binding
            payload, source_stat = read_regular_file_snapshot(source, source="recovery WIP")
            if (hashlib.sha256(payload).hexdigest() != checksums[rel_path]
                    or stat.S_IMODE(source_stat.st_mode) != file_modes[rel_path]):
                return False, "recovery_wip_changed_after_publication", binding

        # This restores only tracked/indexed source. Unlike removing a whole
        # worktree, it leaves ignored local files and all commits untouched.
        if same_snapshot and (current_diff.stdout or current_staged.stdout):
            restored = git("restore", f"--source={source_head}", "--staged", "--worktree", "--", ".")
            if restored.returncode:
                return False, "recovery_wip_restore_failed", binding
        for rel_path in remaining_untracked:
            source = worktree_path / rel_path
            payload, source_stat = read_regular_file_snapshot(source, source="recovery WIP")
            if (hashlib.sha256(payload).hexdigest() != checksums[rel_path]
                    or stat.S_IMODE(source_stat.st_mode) != file_modes[rel_path]):
                return False, "recovery_untracked_changed_after_archive", binding
            source.unlink()  # Exact file already durably archived and published.
        final_status = git("status", "--porcelain", "--untracked-files=all")
        final_head = git("rev-parse", "HEAD")
        if (final_status.returncode or final_head.returncode
                or final_status.stdout.strip() or final_head.stdout.strip() != current_head):
            return False, "recovery_wip_restore_incomplete", binding
        return True, "quarantined_wip", binding
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return False, "recovery_wip_archive_unavailable", binding


def worker_worktree_base_relation(worktree_path: Path, base_sha: str) -> str:
    """Describe ancestry, distinguishing a negative answer from Git failure."""

    head_contains_base = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_sha, "HEAD"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if head_contains_base.returncode == 0:
        return "contains_base"
    if head_contains_base.returncode != 1:
        return "base_relation_failed"
    head_is_base_ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "HEAD", base_sha],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if head_is_base_ancestor.returncode == 0:
        return "behind_base"
    if head_is_base_ancestor.returncode != 1:
        return "base_relation_failed"
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

    # Workspace provenance is projected from the canonical receipt, never
    # accepted as queue-supplied filesystem or cleanup authority.
    request.metadata.pop("recovery_workspace", None)
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
                expected_branch=worker_task_branch(config, workspace_task_id),
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
        # A bound request still goes through the same reuse/recovery checks.
        # Binding a path cannot bypass dirty-worktree admission.

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
    recovery_workspace: dict[str, str] = {}
    leases = state.setdefault("worker_worktrees", {}).setdefault("leases", {})
    if not isinstance(leases, dict):
        leases = {}
        state["worker_worktrees"]["leases"] = leases

    existing = _existing_worktree_for_branch(repo_root, branch, exclude_root=True)
    if existing:
        worktree_path = existing
        reused = True
        recovery_eligible = _lost_lease_replacement_may_recover_worktree(
            config, state, request,
            task_id=workspace_task_id,
            repository_id=repository_id,
            source_root=repo_root,
            branch=branch,
            worktree_path=worktree_path,
            base_ref=base_ref,
            queue_event_id=queue_event_id,
            target_agent=target_agent,
        )
        if recovery_eligible:
            supervisor = _supervisor_module()
            status = load_status(config)
            task = supervisor.task_index_from_status(config, status).get(workspace_task_id)
            receipt = _canonical_worker_recovery_receipt(status, task) if task else None
            if receipt is None or receipt.get("receipt_id") != request.metadata.get("recovery_receipt_id"):
                return False, "Recovery receipt changed before workspace preparation."
            recovery_workspace = worker_recovery_workspace_facts(receipt.get("workspace"))
            if recovery_workspace or _git_dirty_entries(worktree_path):
                active_roots = active_worker_workspace_roots(config, state)
                if any(_paths_overlap(worktree_path, active) for active in active_roots):
                    return False, f"Cannot quarantine active worker workspace {worktree_path}."
                cleanup_settings = worktree_cleanup_settings(config)
                recovered, recovery_status, binding = _quarantine_recovery_worktree(
                    repo_root, worktree_path,
                    branch=branch,
                    archive_root=_recovery_worktree_archive_root(config),
                    task_id=workspace_task_id,
                    repository_id=repository_id,
                    max_file_bytes=int(cleanup_settings["archive_max_file_bytes"]),
                    existing_archive=recovery_workspace or None,
                    publish_archive=lambda facts: supervisor.persist_worker_recovery_workspace(
                        config,
                        task_id=workspace_task_id,
                        receipt_id=str(request.metadata["recovery_receipt_id"]),
                        expected_generation=request.metadata["task_generation"],
                        workspace=facts,
                    ),
                )
                write_activity_log(
                    config,
                    {
                        "type": "worker_recovery_workspace_quarantine",
                        "task_id": request.task_id,
                        "target_agent": target_agent,
                        "queue_event_id": queue_event_id,
                        "recovery_receipt_id": request.metadata["recovery_receipt_id"],
                        "recovery_ok": recovered,
                        "recovery_status": recovery_status,
                        "workspace": binding,
                    },
                )
                if not recovered:
                    return False, f"Cannot quarantine recovery WIP for {workspace_task_id}: {recovery_status}."
                recovery_workspace = worker_recovery_workspace_facts(binding)
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

    # Reused and recreated task branches share exactly one refresh/admission
    # policy. Only a brand-new branch created at the pinned base needs no merge.
    if reused or creation_origin != "base_snapshot":
        refresh_ok, refresh_status = _refresh_reused_worker_worktree(
            worktree_path, base_sha, task_id=workspace_task_id, branch=branch,
        )
        write_activity_log(config, {
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
        })
        if not refresh_ok and refresh_status != "skipped_non_fast_forward":
            return False, (
                f"Cannot lease isolated worker worktree for {workspace_task_id}: "
                f"worktree {worktree_path} refresh failed ({refresh_status})."
            )

    base_relation = (
        "exact_base" if not reused and creation_origin == "base_snapshot"
        else worker_worktree_base_relation(worktree_path, base_sha)
    )
    if base_relation == "base_relation_failed":
        return False, (
            f"Cannot lease isolated worker worktree for {workspace_task_id}: "
            f"worktree {worktree_path} base_relation_failed."
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
    if recovery_receipt_id := str(
        request.metadata.get("recovery_receipt_id") or ""
    ).strip():
        leases[workspace_task_id]["recovery_receipt_id"] = recovery_receipt_id
        leases[workspace_task_id]["recovery_started_at"] = utc_now()
    if recovery_workspace:
        request.metadata["recovery_workspace"] = recovery_workspace
        provenance_text = (
            "\n\nRecovery workspace provenance (advisory, not delivery approval):\n"
            + json.dumps(recovery_workspace, sort_keys=True)
            + "\nThe committed task branch is preserved. Uncommitted WIP was quarantined; "
            "inspect its manifest and binary patches before selectively restoring changes. "
            "Do not treat the archived WIP or preserved source head as accepted delivery.\n"
        )
        if provenance_text not in request.message:
            request.message += provenance_text
        leases[workspace_task_id]["recovery_workspace"] = recovery_workspace
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


def _archive_dirty_worktree(
    worktree_path: Path,
    archive_root: Path,
    *,
    reason: str,
    max_file_bytes: int,
) -> Path | None:
    """Snapshot source WIP; an incomplete archive never authorizes disposal."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", worktree_path.name).strip("-") or "worktree"
    archive_dir = archive_root / f"{slug}-{timestamp}-{os.getpid()}"
    suffix = 1
    while archive_dir.exists():
        suffix += 1
        archive_dir = archive_root / f"{slug}-{timestamp}-{os.getpid()}-{suffix}"

    def run_git(args: list[str], *, raw: bool = False) -> subprocess.CompletedProcess[Any]:
        return subprocess.run(
            ["git", "-C", str(worktree_path), *args],
            capture_output=True, text=not raw, check=False,
        )

    try:
        if first_symlink_component(archive_dir) is not None:
            return None
        # Durable file replacement fsyncs its immediate parent; newly created
        # ancestor directories must also be published before disposal is safe.
        directories_to_sync: set[Path] = set()
        directory = archive_dir
        while not directory.exists():
            directories_to_sync.add(directory.parent)
            directory = directory.parent
        archive_dir.mkdir(parents=True)
        status_proc = run_git(["status", "--porcelain", "--untracked-files=all"])
        # Text-mode pipes normalize CRLF, losing distinct staged bytes even
        # when the working-tree copy is preserved separately.
        diff_proc = run_git(["diff", "--binary"], raw=True)
        staged_diff_proc = run_git(["diff", "--cached", "--binary"], raw=True)
        untracked_proc = run_git(["ls-files", "--others", "--exclude-standard", "-z"])
        commands = {
            "status": status_proc, "diff": diff_proc,
            "diff_staged": staged_diff_proc, "untracked": untracked_proc,
        }
        archive_checksums: dict[str, str] = {}
        for name, result in (
            ("status.txt", status_proc), ("diff.patch", diff_proc),
            ("diff-staged.patch", staged_diff_proc),
        ):
            payload = result.stdout if isinstance(result.stdout, bytes) else (result.stdout or "").encode("utf-8")
            durable_write_bytes(archive_dir / name, payload)
            archive_checksums[name] = hashlib.sha256(payload).hexdigest()
        untracked_paths = [path for path in untracked_proc.stdout.split("\0") if path]
        untracked_payload = "\n".join(untracked_paths).encode("utf-8")
        durable_write_bytes(archive_dir / "untracked-files.txt", untracked_payload)
        archive_checksums["untracked-files.txt"] = hashlib.sha256(untracked_payload).hexdigest()

        copied: list[str] = []
        skipped: list[str] = []
        deleted: list[str] = []
        checksums: dict[str, str] = {}
        file_modes: dict[str, int] = {}
        entries = _git_dirty_entries(worktree_path)
        complete = all(result.returncode == 0 for result in commands.values())
        if status_proc.stdout.strip() and not entries:
            complete = False
        files_root = archive_dir / "files"
        for entry in entries:
            rel_path = entry["path"]
            source = worktree_path / rel_path
            if (
                Path(rel_path).is_absolute()
                or ".." in Path(rel_path).parts
                or first_symlink_component(source) is not None
                or not source.resolve().is_relative_to(worktree_path.resolve())
            ):
                skipped.append(f"{rel_path}\tunsupported_path")
                continue
            if not source.exists() and "D" in entry["status"]:
                deleted.append(rel_path)
                continue
            try:
                if not source.is_file():
                    skipped.append(f"{rel_path}\tunsupported_file")
                    continue
                size = source.stat().st_size
                if max_file_bytes > 0 and size > max_file_bytes:
                    skipped.append(f"{rel_path}\ttoo_large:{size}")
                    continue
                archived_bytes, source_stat = read_regular_file_snapshot(source, source="worktree archive")
                if max_file_bytes > 0 and len(archived_bytes) > max_file_bytes:
                    skipped.append(f"{rel_path}\ttoo_large:{len(archived_bytes)}")
                    continue
                destination = files_root / rel_path
                directory = destination.parent
                while directory != archive_dir:
                    directories_to_sync.add(directory.parent)
                    directory = directory.parent
                mode = stat.S_IMODE(source_stat.st_mode)
                durable_write_bytes(destination, archived_bytes, mode=mode)
                verified_bytes, verified_stat = read_regular_file_snapshot(source, source="worktree archive")
                if verified_bytes != archived_bytes or stat.S_IMODE(verified_stat.st_mode) != mode:
                    skipped.append(f"{rel_path}\tchanged_during_archive")
                    continue
                checksums[rel_path] = hashlib.sha256(archived_bytes).hexdigest()
                file_modes[rel_path] = mode
                copied.append(rel_path)
            except (OSError, RuntimeError):
                skipped.append(rel_path)

        complete = complete and not skipped and set(untracked_paths).issubset(checksums)
        durable_write_bytes(archive_dir / "copied-files.txt", "\n".join(copied).encode("utf-8"))
        durable_write_bytes(archive_dir / "skipped-files.txt", "\n".join(skipped).encode("utf-8"))
        manifest = {
            "archived_at": utc_now(),
            "worktree_path": str(worktree_path),
            "reason": reason,
            "status_returncode": status_proc.returncode,
            "command_returncodes": {name: result.returncode for name, result in commands.items()},
            "complete": complete,
            "copied_files": copied,
            "skipped_files": skipped,
            "deleted_files": deleted,
            "file_checksums": checksums,
            "file_modes": file_modes,
            "archive_checksums": archive_checksums,
            "untracked_paths": untracked_paths,
        }
        durable_write_bytes(
            archive_dir / "manifest.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        for directory in sorted(directories_to_sync, key=lambda path: len(path.parts), reverse=True):
            _fsync_directory(directory)
        return archive_dir if complete else None
    except (OSError, ValueError, RuntimeError):
        # Preserve any partial archive for inspection; the caller must leave
        # the source untouched when no completed archive is returned.
        return None


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
