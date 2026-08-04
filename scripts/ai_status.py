#!/usr/bin/env python3
from __future__ import annotations

import gzip
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from threading import local
from typing import Any, Mapping
from zoneinfo import ZoneInfo

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - exercised only in lean supervisor envs
    yaml = None

YAML_ERROR_TYPES = (yaml.YAMLError,) if yaml is not None else ()

ROOT = Path(__file__).resolve().parents[1]
STATUS_ROOT_ENV = "PANTHEON_STATUS_ROOT"
STATUS_COMMAND_ROOT_ENV = "PANTHEON_COMMAND_ROOT"
STATUS_COMMAND_SHA_ENV = "PANTHEON_COMMAND_RUNTIME_SHA"
STATUS_COMMAND_REMOTE_ENV = "PANTHEON_COMMAND_REMOTE"
STATUS_COMMAND_BASE_REF_ENV = "PANTHEON_COMMAND_BASE_REF"
TASK_STATE_STORE_MODE_ENV = "PANTHEON_TASK_STATE_STORE_MODE"
TASK_STATE_EVENT_LOG_ENV = "PANTHEON_TASK_STATE_EVENT_LOG"
LEGACY_STATUS_COMMAND_ROOT_ENV = "PANTHEON_STATUS_COMMAND_ROOT"
LEGACY_STATUS_COMMAND_SHA_ENV = "PANTHEON_STATUS_COMMAND_SHA"
LEGACY_STATUS_COMMAND_REMOTE_ENV = "PANTHEON_STATUS_COMMAND_REMOTE"
LEGACY_STATUS_COMMAND_BASE_REF_ENV = "PANTHEON_STATUS_COMMAND_BASE_REF"
AUTO_WORKER_ENV_MARKERS = (
    "ORCH_RUN_ID",
    "PANTHEON_WORKTREE_ROOT",
    "ORCH_WORKSPACE_PATH",
)


def _status_root_env_value() -> str:
    return str(os.environ.get(STATUS_ROOT_ENV) or "").strip()


def _resolve_status_root_from_env() -> Path:
    raw = _status_root_env_value()
    if raw:
        return Path(os.path.expanduser(raw)).resolve()
    return ROOT


STATUS_ROOT = _resolve_status_root_from_env()
ORCHESTRATOR_DIR = ROOT / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

import task_archive as task_archive_module
from task_archive import (
    ARCHIVE_TASKS_DIR,
    DEFAULT_RECENT_LIMIT as DEFAULT_ARCHIVE_RECENT_LIMIT,
    TaskResolver,
    archive_display_path,
    archive_task_path,
    archive_task_snapshot,
    is_terminal_task,
    load_archive_index,
    load_archived_snapshot,
    rebuild_archive_index,
    recent_terminal_summaries,
    task_satisfies_dependency,
)
from multi_repo_registry import (
    repository_local_path,
    repository_slug,
    resolve_repository,
    task_artifact_repository_ids,
    task_primary_repository_id,
)
from runtime_state import (
    activity_audit_lock_file,
    canonical_task_state_lock_file,
    load_runtime_state_snapshot,
    runtime_state_lock,
)
from rewrite.task_state_store import (
    append_state_commit,
    load_events,
    load_snapshot,
    project_latest_state,
    snapshot_transaction,
)
from common import (
    ActivityAuditInvariantError,
    DuplicateActivityJSONKeyError,
    activity_audit_invariant_error,
    activity_audit_lock_path,
    activity_audit_source_paths_unlocked,
    append_activity_log_entries_unlocked,
    canonical_task_state_lock_path,
    durable_write_bytes,
    prepare_activity_audit_unlocked,
    read_activity_log_tail_bytes,
    read_regular_file_bytes,
    rotate_activity_log_unlocked,
    strict_activity_json_loads,
    validated_activity_event_digests_unlocked,
)

# Derived dashboard rendering intentionally uses an atomic projection-only
# reader. Canonical mutation/admission callers must use runtime_state's locked
# APIs instead; taking a runtime lock here while task-state is held would
# reverse the global runtime -> task -> audit order.
load_runtime_state = load_runtime_state_snapshot

STATUS_FILE = STATUS_ROOT / "ai-status.json"
LOG_FILE = STATUS_ROOT / "ai-activity-log.jsonl"
LOG_ROTATE_MAX_BYTES = int(os.environ.get("AI_STATUS_LOG_ROTATE_MAX_BYTES", str(5 * 1024 * 1024)))
LOG_ROTATE_KEEP_LINES = int(os.environ.get("AI_STATUS_LOG_ROTATE_KEEP_LINES", "1000"))
STATUS_ACTIVITY_OUTBOX_KEY = "status_activity_outbox"
STATUS_ACTIVITY_OUTBOX_SCHEMA_VERSION = 1
STATUS_ARCHIVE_OUTBOX_KEY = "status_archive_outbox"
STATUS_ARCHIVE_OUTBOX_SCHEMA_VERSION = 1
SUPERVISOR_DISPATCH_BATCH_SCHEMA_VERSION = 1
SUPERVISOR_DISPATCH_BATCH_MAX_MUTATIONS = 64
SUPERVISOR_DISPATCH_BATCH_COMMAND = "supervisor-dispatch-batch"
GLOBAL_STATUS_LOCK_ORDER = (
    "runtime_admission",
    "task_state",
    "activity_audit",
)
_ACTIVITY_TRANSACTION_LOCAL = local()
_TASK_STATE_TRANSACTION_LOCAL = local()
_STATUS_COMMAND_LEASE_LOCAL = local()
CURRENT_WORK_FILE = STATUS_ROOT / "current-work.md"
DOCS_SITE_DIR = STATUS_ROOT / "docs-site"
CONFIG_FILE = ROOT / ".orchestrator" / "config.json"
PLANNING_STATE_FILE = STATUS_ROOT / ".orchestrator" / "planning-state.json"
ORCHESTRATOR_STATE_FILE = STATUS_ROOT / ".orchestrator" / "state.json"
APPROVAL_QUEUE_FILE = STATUS_ROOT / ".orchestrator" / "approval-queue.json"
DASHBOARD_BUNDLE_FILE = STATUS_ROOT / "dashboard-bundle.json"
DEFAULT_PLANNING_README = "docs/02-architecture/consensus/phase1/README.md"
DEFAULT_PLANNING_SESSION_FILE = "docs/02-architecture/consensus/phase1/planning-session.json"
DEFAULT_PLANNING_CHECKLIST_FILE = "docs/02-architecture/consensus/phase1/pantheon-backend-completion-checklist.md"


def configure_status_root_paths(status_root: str | Path) -> Path:
    """Bind every governed status/archive/audit path to one root."""

    global STATUS_ROOT
    global STATUS_FILE, LOG_FILE, CURRENT_WORK_FILE, DOCS_SITE_DIR
    global PLANNING_STATE_FILE, ORCHESTRATOR_STATE_FILE, APPROVAL_QUEUE_FILE
    global DASHBOARD_BUNDLE_FILE, ARCHIVE_TASKS_DIR

    root = Path(status_root).expanduser().resolve()
    STATUS_ROOT = root
    STATUS_FILE = root / "ai-status.json"
    LOG_FILE = root / "ai-activity-log.jsonl"
    CURRENT_WORK_FILE = root / "current-work.md"
    DOCS_SITE_DIR = root / "docs-site"
    PLANNING_STATE_FILE = root / ".orchestrator" / "planning-state.json"
    ORCHESTRATOR_STATE_FILE = root / ".orchestrator" / "state.json"
    APPROVAL_QUEUE_FILE = root / ".orchestrator" / "approval-queue.json"
    DASHBOARD_BUNDLE_FILE = root / "dashboard-bundle.json"

    task_archive_module.STATUS_ROOT = root
    task_archive_module.STATUS_FILE = STATUS_FILE
    task_archive_module.ARCHIVE_DIR = root / "ai-task-archive"
    task_archive_module.ARCHIVE_TASKS_DIR = task_archive_module.ARCHIVE_DIR / "tasks"
    task_archive_module.ARCHIVE_INDEX_FILE = task_archive_module.ARCHIVE_DIR / "index.json"
    ARCHIVE_TASKS_DIR = task_archive_module.ARCHIVE_TASKS_DIR
    return root


configure_status_root_paths(STATUS_ROOT)


def _auto_worker_requires_explicit_status_root() -> bool:
    return any(str(os.environ.get(marker) or "").strip() for marker in AUTO_WORKER_ENV_MARKERS)


def _worker_workspace_root() -> Path | None:
    roots: list[tuple[str, Path]] = []
    for env_name in ("PANTHEON_WORKTREE_ROOT", "ORCH_WORKSPACE_PATH"):
        raw = str(os.environ.get(env_name) or "").strip()
        if not raw:
            continue
        expanded = Path(os.path.expanduser(raw))
        if not expanded.is_absolute():
            raise RuntimeError(f"{env_name} must be an absolute path when set")
        symlink_component = _first_symlink_component(expanded)
        if symlink_component is not None:
            raise RuntimeError(
                f"{env_name} cannot include a symlink component: {symlink_component}"
            )
        roots.append((env_name, expanded.resolve()))
    if not roots:
        return None
    first_name, first_root = roots[0]
    for env_name, root in roots[1:]:
        if root != first_root:
            raise RuntimeError(
                f"{first_name} and {env_name} disagree on delivery worktree root: "
                f"{first_root} != {root}"
            )
    return first_root


def _first_symlink_component(path: Path) -> Path | None:
    if ".." in path.parts:
        raise RuntimeError(f"Path contains parent directory references (..): {path}")
    current = Path(path.anchor)
    parts = path.parts[1:] if path.is_absolute() else path.parts
    for part in parts:
        current = current / part
        try:
            if current.is_symlink():
                return current
            if not current.exists() and not current.is_symlink():
                return None
        except OSError:
            return current
    return None



def _status_root_from_runtime_path(raw: str, *, label: str) -> Path:
    path = Path(os.path.expanduser(raw))
    if not path.is_absolute():
        raise RuntimeError(f"{label} must be absolute when set")
    symlink_comp = _first_symlink_component(path)
    if symlink_comp is not None:
        raise RuntimeError(f"{label} path contains a symlink component: {symlink_comp}")
    if path.is_symlink():
        raise RuntimeError(f"{label} cannot be a symlink: {path}")
    resolved = path.resolve()
    parent = resolved.parent
    if (
        parent.name not in {"status", "heartbeats"}
        or parent.parent.name != "worker-runtime"
        or parent.parent.parent.name != ".orchestrator"
    ):
        raise RuntimeError(
            f"{label} is not under .orchestrator/worker-runtime: {resolved}"
        )
    return parent.parent.parent.parent.resolve()


def _supervisor_expected_status_root() -> Path | None:
    roots: list[Path] = []
    for env_name in ("ORCH_RUNNER_STATUS_PATH", "ORCH_HEARTBEAT_PATH"):
        raw = str(os.environ.get(env_name) or "").strip()
        if not raw:
            continue
        root = _status_root_from_runtime_path(raw, label=env_name)
        if root not in roots:
            roots.append(root)
    if len(roots) > 1:
        raise RuntimeError(
            "ORCH_RUNNER_STATUS_PATH and ORCH_HEARTBEAT_PATH disagree on "
            f"the supervisor coordination root: {roots[0]} != {roots[1]}"
        )
    return roots[0] if roots else None


def _git_toplevel(path: Path) -> Path | None:
    proc = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    top = proc.stdout.strip()
    if not top:
        return None
    return Path(top).resolve()


def _git_stdout(path: Path, args: list[str]) -> str:
    proc = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "git command failed").strip()
        raise RuntimeError(detail)
    return proc.stdout.strip()


def _normalize_github_repo_slug(value: str | None) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    if candidate.endswith(".git"):
        candidate = candidate[:-4]
    for prefix in (
        "git@github.com:",
        "ssh://git@github.com/",
        "https://github.com/",
        "http://github.com/",
    ):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    return candidate.strip("/")


def _command_env(primary: str, legacy: str, default: str = "") -> str:
    return str(os.environ.get(primary) or os.environ.get(legacy) or default).strip()


def validate_status_command_runtime_binding() -> None:
    """Ensure auto-worker status commands run from the installed command root."""

    raw_root = _command_env(STATUS_COMMAND_ROOT_ENV, LEGACY_STATUS_COMMAND_ROOT_ENV)
    if not raw_root:
        if _auto_worker_requires_explicit_status_root():
            raise RuntimeError(
                "PANTHEON_COMMAND_ROOT is required for auto-worker status commands"
            )
        return

    expanded_root = Path(os.path.expanduser(raw_root))
    if not expanded_root.is_absolute():
        raise RuntimeError(f"{STATUS_COMMAND_ROOT_ENV} must be an absolute path")
    symlink_component = _first_symlink_component(expanded_root)
    if symlink_component is not None:
        raise RuntimeError(
            f"{STATUS_COMMAND_ROOT_ENV} cannot include a symlink component: {symlink_component}"
        )
    command_root = expanded_root.resolve()
    if not command_root.exists() or not command_root.is_dir():
        raise RuntimeError(
            f"{STATUS_COMMAND_ROOT_ENV} does not exist or is not a directory: {command_root}"
        )
    if _git_toplevel(command_root) != command_root:
        raise RuntimeError(f"{STATUS_COMMAND_ROOT_ENV} must be a git repository root: {command_root}")

    source_sha = _git_stdout(command_root, ["rev-parse", "HEAD"])
    expected_sha = _command_env(STATUS_COMMAND_SHA_ENV, LEGACY_STATUS_COMMAND_SHA_ENV)
    if not expected_sha:
        raise RuntimeError("PANTHEON_COMMAND_RUNTIME_SHA is required for auto-worker status commands")
    if source_sha != expected_sha:
        raise RuntimeError(
            f"PANTHEON_COMMAND_RUNTIME_SHA mismatch: command root is {source_sha}, expected {expected_sha}"
        )

    current_root = ROOT.resolve()
    if current_root != command_root:
        raise RuntimeError(
            "auto-worker status command must execute the installed command runtime: "
            f"running {current_root}, expected {command_root}"
        )

    expected_remote = _normalize_github_repo_slug(
        _command_env(STATUS_COMMAND_REMOTE_ENV, LEGACY_STATUS_COMMAND_REMOTE_ENV, "ajoe734/pantheon")
    )
    remote_url = _git_stdout(command_root, ["remote", "get-url", "origin"])
    actual_remote = _normalize_github_repo_slug(remote_url)
    if expected_remote and actual_remote != expected_remote:
        raise RuntimeError(
            f"{STATUS_COMMAND_ROOT_ENV} remote mismatch: {actual_remote or remote_url} != {expected_remote}"
        )

    base_ref = _command_env(STATUS_COMMAND_BASE_REF_ENV, LEGACY_STATUS_COMMAND_BASE_REF_ENV, "origin/dev") or "origin/dev"
    _git_stdout(command_root, ["rev-parse", "--verify", base_ref])
    proc = subprocess.run(
        ["git", "-C", str(command_root), "merge-base", "--is-ancestor", source_sha, base_ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(
            f"{STATUS_COMMAND_ROOT_ENV} source SHA {source_sha} is not merged into {base_ref}{suffix}"
        )


STATUS_WORKER_PROCESS_GENERATION_SCHEMA_VERSION = 1
STATUS_WORKER_PROCESS_GENERATION_PREFIX = "worker-process-generation-sha256:"


def status_worker_process_generation_id(
    *,
    task_id: str,
    worker_run_id: str,
    queue_event_id: str,
    pid: int,
    pid_start_ticks: int,
) -> str:
    payload = {
        "schema_version": STATUS_WORKER_PROCESS_GENERATION_SCHEMA_VERSION,
        "task_id": str(task_id),
        "worker_run_id": str(worker_run_id),
        "queue_event_id": str(queue_event_id),
        "pid": int(pid),
        "pid_start_ticks": int(pid_start_ticks),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return STATUS_WORKER_PROCESS_GENERATION_PREFIX + hashlib.sha256(encoded).hexdigest()


def _clear_status_command_lease_binding() -> None:
    try:
        delattr(_STATUS_COMMAND_LEASE_LOCAL, "binding")
    except AttributeError:
        pass


def _validated_status_command_worker_lease(
    worker: Mapping[str, Any],
    *,
    run_id: str,
    task_id: str,
) -> dict[str, Any]:
    worker_run_id = str(worker.get("run_id") or "").strip()
    worker_task_id = str(worker.get("task_id") or "").strip()
    queue_event_id = str(worker.get("queue_event_id") or "").strip()
    process_generation = str(worker.get("process_generation") or "").strip()
    pid = worker.get("pid")
    pid_start_ticks = worker.get("pid_start_ticks")
    if worker_run_id != run_id or worker_task_id != task_id:
        raise RuntimeError("active status command process generation has mismatched run/task identity")
    if (
        not queue_event_id
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or not isinstance(pid_start_ticks, int)
        or isinstance(pid_start_ticks, bool)
        or pid_start_ticks <= 0
    ):
        raise RuntimeError(
            f"active status command lease for ORCH_RUN_ID={run_id} has no exact process generation"
        )
    expected = status_worker_process_generation_id(
        task_id=task_id,
        worker_run_id=run_id,
        queue_event_id=queue_event_id,
        pid=pid,
        pid_start_ticks=pid_start_ticks,
    )
    if process_generation != expected:
        raise RuntimeError(
            f"active status command lease for ORCH_RUN_ID={run_id} has invalid process generation"
        )
    return {
        "schema_version": STATUS_WORKER_PROCESS_GENERATION_SCHEMA_VERSION,
        "task_id": task_id,
        "worker_run_id": run_id,
        "queue_event_id": queue_event_id,
        "pid": pid,
        "pid_start_ticks": pid_start_ticks,
        "process_generation": process_generation,
    }


def status_command_metadata() -> dict[str, Any] | None:
    raw_root = _command_env(STATUS_COMMAND_ROOT_ENV, LEGACY_STATUS_COMMAND_ROOT_ENV)
    raw_sha = _command_env(STATUS_COMMAND_SHA_ENV, LEGACY_STATUS_COMMAND_SHA_ENV)
    if not raw_root and not raw_sha:
        return None
    delivery_root = _worker_workspace_root()
    payload: dict[str, Any] = {
        "command_root": str(Path(os.path.expanduser(raw_root)).resolve()) if raw_root else None,
        "source_sha": raw_sha or None,
        "base_ref": _command_env(STATUS_COMMAND_BASE_REF_ENV, LEGACY_STATUS_COMMAND_BASE_REF_ENV) or None,
        "remote": _normalize_github_repo_slug(
            _command_env(STATUS_COMMAND_REMOTE_ENV, LEGACY_STATUS_COMMAND_REMOTE_ENV)
        ),
        "status_root": str(STATUS_ROOT),
        "delivery_root": str(delivery_root) if delivery_root is not None else None,
        "wrapper_root": str(os.environ.get("PANTHEON_STATUS_COMMAND_WRAPPER_ROOT") or "").strip() or None,
    }
    worker_lease = getattr(_STATUS_COMMAND_LEASE_LOCAL, "binding", None)
    if isinstance(worker_lease, Mapping):
        payload["worker_lease"] = dict(worker_lease)
    return {key: value for key, value in payload.items() if value not in (None, "")}


TASK_ID_COMMAND_ARG_INDEX: dict[str, int] = {
    "assign": 0,
    "start": 0,
    "progress": 0,
    "note": 0,
    "reopen": 0,
    "handoff": 0,
    "blocker": 0,
    "done": 0,
    "restore_approved": 0,
    "reconcile_merged_done": 0,
    "supersede": 0,
    "approve": 0,
    "archive_correct_review_file": 0,
}
ACTIVE_WORKER_LEASE_STATUSES = {
    "running",
    "started",
    "waiting_approval",
    "suspended_approval",
    "manual_pending",
    "retry_backoff",
    "stalled",
    "fallback",
}


def _parse_utc_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _metadata_path(value: Any, *, label: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(f"{label} is required for active status command lease validation")
    path = Path(os.path.expanduser(text))
    if not path.is_absolute():
        raise RuntimeError(f"{label} must be an absolute path")
    symlink_component = _first_symlink_component(path)
    if symlink_component is not None:
        raise RuntimeError(f"{label} cannot include a symlink component: {symlink_component}")
    return path.resolve()


def _worker_status_command_runtime(worker: Mapping[str, Any]) -> Mapping[str, Any] | None:
    direct = worker.get("status_command_runtime")
    if isinstance(direct, Mapping):
        return direct
    metadata = worker.get("metadata")
    if isinstance(metadata, Mapping) and isinstance(metadata.get("status_command_runtime"), Mapping):
        return metadata["status_command_runtime"]
    snapshot = worker.get("request_snapshot")
    if isinstance(snapshot, Mapping):
        snapshot_metadata = snapshot.get("metadata")
        if isinstance(snapshot_metadata, Mapping) and isinstance(snapshot_metadata.get("status_command_runtime"), Mapping):
            return snapshot_metadata["status_command_runtime"]
    return None


def _worker_metadata_value(worker: Mapping[str, Any], key: str) -> Any:
    value = worker.get(key)
    if value not in (None, ""):
        return value
    snapshot = worker.get("request_snapshot")
    if isinstance(snapshot, Mapping):
        metadata = snapshot.get("metadata")
        if isinstance(metadata, Mapping):
            value = metadata.get(key)
            if value not in (None, ""):
                return value
    return None


def _command_task_id(command: str, args: list[str]) -> str | None:
    index = TASK_ID_COMMAND_ARG_INDEX.get(command)
    if index is None or len(args) <= index:
        return None
    return str(args[index] or "").strip() or None


def _find_worker_worktree_lease(
    runtime_state: Mapping[str, Any],
    *,
    worker: Mapping[str, Any],
    task_id: str | None,
    workspace_root: Path | None,
    status_root: Path,
) -> tuple[str, Mapping[str, Any]] | None:
    raw_leases = (
        runtime_state.get("worker_worktrees", {})
        if isinstance(runtime_state.get("worker_worktrees"), Mapping)
        else {}
    ).get("leases", {})
    if not isinstance(raw_leases, Mapping):
        return None
    snapshot = worker.get("request_snapshot")
    metadata = snapshot.get("metadata") if isinstance(snapshot, Mapping) else None
    workspace_task_id = ""
    if isinstance(metadata, Mapping):
        workspace_task_id = str(metadata.get("workspace_task_id") or "").strip()
    workspace_task_id = workspace_task_id or str(task_id or "").strip()
    if workspace_task_id and isinstance(raw_leases.get(workspace_task_id), Mapping):
        return workspace_task_id, raw_leases[workspace_task_id]
    for key, candidate in raw_leases.items():
        if not isinstance(candidate, Mapping):
            continue
        if task_id and str(candidate.get("task_id") or "").strip() not in {"", task_id}:
            continue
        try:
            candidate_status_root = _metadata_path(candidate.get("status_root"), label="worker_worktrees lease status_root")
        except RuntimeError:
            continue
        if candidate_status_root != status_root:
            continue
        if workspace_root is not None:
            try:
                candidate_path = _metadata_path(candidate.get("path"), label="worker_worktrees lease path")
            except RuntimeError:
                continue
            if candidate_path != workspace_root:
                continue
        return str(key), candidate
    return None


def _declared_lease_workspace_roots(
    runtime_state: Mapping[str, Any],
    *,
    worker: Mapping[str, Any],
    task_id: str | None,
    status_root: Path,
) -> tuple[Path, ...]:
    """Return the worktree roots the supervisor recorded for this worker run.

    Both sources live in central runtime state, which is written by the
    supervisor outside every task worktree.  A candidate can rewrite its own
    environment but not this file, so these paths stay true even when the
    worker's workspace variables are missing.
    """

    roots: list[Path] = []
    raw_workspace = _worker_metadata_value(worker, "workspace_path")
    if raw_workspace not in (None, ""):
        roots.append(_metadata_path(raw_workspace, label="worker workspace_path"))
    lease_match = _find_worker_worktree_lease(
        runtime_state,
        worker=worker,
        task_id=task_id,
        workspace_root=None,
        status_root=status_root,
    )
    if lease_match is not None:
        raw_lease_path = lease_match[1].get("path")
        if raw_lease_path not in (None, ""):
            roots.append(_metadata_path(raw_lease_path, label="worktree lease path"))
    ordered: list[Path] = []
    for root in roots:
        if root not in ordered:
            ordered.append(root)
    return tuple(ordered)


def active_lease_workspace_roots() -> tuple[Path, ...]:
    """Return the canonical candidate-controlled worktrees for this run.

    ``PANTHEON_WORKTREE_ROOT`` / ``ORCH_WORKSPACE_PATH`` cannot be the
    authority for "which directory is candidate-controlled": the candidate owns
    its own environment and can simply unset both, which silently removes its
    worktree from every workspace-scoped boundary while its run lease stays
    valid.  Consumers that need that boundary — notably the protected closeout
    verifier's forbidden roots — must ask the supervisor's runtime state
    instead.  Returns an empty tuple when there is no active run lease.
    """

    run_id = str(os.environ.get("ORCH_RUN_ID") or "").strip()
    if not run_id:
        return ()
    config = load_config()
    runtime_state = load_runtime_state_snapshot(config)
    workers = runtime_state.get("workers", {})
    worker = workers.get(run_id) if isinstance(workers, Mapping) else None
    if not isinstance(worker, Mapping):
        raise RuntimeError(
            f"active status command lease not found for ORCH_RUN_ID={run_id}"
        )
    task_id = str(worker.get("task_id") or "").strip() or str(
        os.environ.get("ORCH_TASK_ID") or ""
    ).strip()
    return _declared_lease_workspace_roots(
        runtime_state,
        worker=worker,
        task_id=task_id or None,
        status_root=STATUS_ROOT.resolve(),
    )


def normalize_logical_actor(name: str | None) -> str:
    if not name:
        return ""
    trimmed = str(name).strip()
    slot_id = trimmed.lower().replace("-", "_")
    slot_match = re.fullmatch(r"codex([12])_[1-4]", slot_id)
    if slot_match:
        return "codex" if slot_match.group(1) == "1" else "codex2"
    return canonical_agent_name(trimmed).casefold()


def validate_active_status_command_lease(
    command: str,
    args: list[str],
    *,
    runtime_state_snapshot: Mapping[str, Any] | None = None,
    config_snapshot: dict[str, Any] | None = None,
) -> None:
    """Validate the supervisor-issued worker lease before canonical mutation."""

    _clear_status_command_lease_binding()
    run_id = str(os.environ.get("ORCH_RUN_ID") or "").strip()
    actor = current_actor()

    is_auto_worker = False
    canonical_actor = canonical_agent_name(actor)
    if canonical_actor and canonical_actor.lower() not in {"human", "human/ops", "ops"}:
        is_auto_worker = True

    if not run_id:
        if is_auto_worker:
            task_id = args[0] if args else ""
            is_reviewer = False
            if task_id:
                try:
                    if STATUS_FILE.exists():
                        status_data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
                        tasks = status_data.get("tasks", [])
                        for t in tasks:
                            if t.get("id") == task_id:
                                rev = str(t.get("reviewer") or "").strip()
                                if rev and normalize_logical_actor(actor) == normalize_logical_actor(rev):
                                    is_reviewer = True
                                    break
                except Exception:
                    pass
            if is_reviewer and command in {"reopen", "approve", "note"}:
                pass
            else:
                raise RuntimeError(f"status command lease required for auto worker: {actor}")
        return

    config = config_snapshot if isinstance(config_snapshot, dict) else load_config()
    runtime_state = (
        runtime_state_snapshot
        if isinstance(runtime_state_snapshot, Mapping)
        else load_runtime_state_snapshot(config)
    )
    workers = runtime_state.get("workers", {})
    if not isinstance(workers, Mapping):
        raise RuntimeError("central runtime state has no worker records")
    worker = workers.get(run_id)
    if not isinstance(worker, Mapping):
        raise RuntimeError(f"active status command lease not found for ORCH_RUN_ID={run_id}")

    # Bind lease to normalized logical actor
    worker_actor = normalize_worker_actor(dict(worker))
    if worker_actor:
        normalized_actor = normalize_logical_actor(actor)
        normalized_worker_agent = normalize_logical_actor(worker_actor)
        if normalized_actor != normalized_worker_agent:
            raise RuntimeError(
                f"status command lease AI identity mismatch: actor {actor} (normalized: {normalized_actor}) != worker actor {worker_actor} (normalized: {normalized_worker_agent})"
            )

    status = str(worker.get("status") or "").strip()
    if status not in ACTIVE_WORKER_LEASE_STATUSES:
        raise RuntimeError(
            f"active status command lease for ORCH_RUN_ID={run_id} is not running: {status or 'missing'}"
        )
    expires_at = _parse_utc_timestamp(worker.get("lease_expires_at"))
    if expires_at is None:
        raise RuntimeError(f"active status command lease for ORCH_RUN_ID={run_id} has no lease_expires_at")
    if datetime.now(timezone.utc) > expires_at:
        raise RuntimeError(f"active status command lease for ORCH_RUN_ID={run_id} is expired")

    command_task_id = _command_task_id(command, args)
    env_task_id = str(os.environ.get("ORCH_TASK_ID") or "").strip()
    worker_task_id = str(worker.get("task_id") or "").strip()
    expected_task_id = command_task_id or env_task_id
    if command_task_id and env_task_id and command_task_id != env_task_id:
        raise RuntimeError(
            f"status command task mismatch: argv task {command_task_id} != ORCH_TASK_ID {env_task_id}"
        )
    if worker_task_id and expected_task_id and worker_task_id != expected_task_id:
        raise RuntimeError(
            f"status command task mismatch: worker task {worker_task_id} != command task {expected_task_id}"
        )
    if worker_task_id and not expected_task_id:
        raise RuntimeError(
            f"status command task identity is required for worker task {worker_task_id}"
        )

    status_root = STATUS_ROOT.resolve()
    worker_status_root = _metadata_path(
        _worker_metadata_value(worker, "status_root"),
        label="worker status_root",
    )
    if worker_status_root != status_root:
        raise RuntimeError(
            f"status command root mismatch: worker status_root {worker_status_root} != {status_root}"
        )

    workspace_root = _worker_workspace_root()
    worker_workspace_raw = _worker_metadata_value(worker, "workspace_path")
    if worker_workspace_raw not in (None, ""):
        worker_workspace = _metadata_path(worker_workspace_raw, label="worker workspace_path")
        # The supervisor recorded which worktree this run owns.  Treating the
        # environment binding as optional let a candidate unset both workspace
        # variables and drop out of every workspace-scoped check while its
        # lease stayed valid, so the canonical value is the authority here and
        # an absent binding fails closed instead of widening the lease.
        if workspace_root is None:
            raise RuntimeError(
                f"status command workspace binding is required: lease for ORCH_RUN_ID={run_id} "
                f"owns worktree {worker_workspace} but PANTHEON_WORKTREE_ROOT and "
                "ORCH_WORKSPACE_PATH are unset"
            )
        if worker_workspace != workspace_root:
            raise RuntimeError(
                f"status command workspace mismatch: worker workspace {worker_workspace} != {workspace_root}"
            )
        workspace_root = worker_workspace

    runtime_metadata = status_command_metadata() or {}
    issued_runtime = _worker_status_command_runtime(worker)
    if not isinstance(issued_runtime, Mapping):
        raise RuntimeError(f"active status command lease for ORCH_RUN_ID={run_id} has no issued command runtime")
    issued_root = _metadata_path(issued_runtime.get("command_root"), label="issued command_root")
    running_root = _metadata_path(runtime_metadata.get("command_root"), label="running command_root")
    if issued_root != running_root:
        raise RuntimeError(
            f"status command runtime root mismatch: issued {issued_root} != running {running_root}"
        )
    issued_sha = str(issued_runtime.get("source_sha") or "").strip()
    running_sha = str(runtime_metadata.get("source_sha") or "").strip()
    if not issued_sha or issued_sha != running_sha:
        raise RuntimeError(
            f"status command runtime SHA mismatch: issued {issued_sha or 'missing'} != running {running_sha or 'missing'}"
        )

    lease_match = _find_worker_worktree_lease(
        runtime_state,
        worker=worker,
        task_id=worker_task_id or expected_task_id,
        workspace_root=workspace_root,
        status_root=status_root,
    )
    if lease_match is None:
        raise RuntimeError(f"active worktree lease not found for ORCH_RUN_ID={run_id}")
    lease_key, lease = lease_match
    if worker_task_id and str(lease.get("task_id") or "").strip() not in {"", worker_task_id}:
        raise RuntimeError(
            f"worktree lease task mismatch for {lease_key}: {lease.get('task_id')} != {worker_task_id}"
        )
    lease_status_root = _metadata_path(lease.get("status_root"), label="worktree lease status_root")
    if lease_status_root != status_root:
        raise RuntimeError(
            f"worktree lease status root mismatch for {lease_key}: {lease_status_root} != {status_root}"
        )
    raw_lease_path = lease.get("path")
    if raw_lease_path not in (None, ""):
        lease_path = _metadata_path(raw_lease_path, label="worktree lease path")
        if workspace_root is None:
            raise RuntimeError(
                f"status command workspace binding is required: worktree lease {lease_key} "
                f"owns {lease_path} but PANTHEON_WORKTREE_ROOT and ORCH_WORKSPACE_PATH "
                "are unset"
            )
        if lease_path != workspace_root:
            raise RuntimeError(
                f"worktree lease path mismatch for {lease_key}: {lease_path} != {workspace_root}"
            )

    _STATUS_COMMAND_LEASE_LOCAL.binding = _validated_status_command_worker_lease(
        worker,
        run_id=run_id,
        task_id=worker_task_id or str(expected_task_id or ""),
    )


def _path_parent_under_root(path: Path, root: Path) -> bool:
    try:
        path.parent.resolve().relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def _existing_path_is_symlink(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError:
        return True


def _validate_directory_no_symlinks_recursive(directory: Path, label: str) -> None:
    if directory.is_symlink():
        raise RuntimeError(f"PANTHEON_STATUS_ROOT {label} directory cannot be a symlink: {directory}")
    if not directory.exists() or not directory.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(directory):
        for dirname in dirnames:
            p = Path(dirpath) / dirname
            if p.is_symlink():
                raise RuntimeError(f"PANTHEON_STATUS_ROOT {label} component cannot be a symlink: {p}")
        for filename in filenames:
            p = Path(dirpath) / filename
            if p.is_symlink():
                raise RuntimeError(f"PANTHEON_STATUS_ROOT {label} leaf cannot be a symlink: {p}")


def validate_status_root_binding() -> None:
    """Fail closed before any governed status command can hit a stale worktree."""

    raw_root = _status_root_env_value()
    if not raw_root:
        if _auto_worker_requires_explicit_status_root():
            raise RuntimeError(
                "PANTHEON_STATUS_ROOT is required for auto workers running outside "
                "the supervisor coordination root"
            )
        return

    expanded_root = Path(os.path.expanduser(raw_root))
    if not expanded_root.is_absolute():
        raise RuntimeError("PANTHEON_STATUS_ROOT must be an absolute path")
    symlink_component = _first_symlink_component(expanded_root)
    if symlink_component is not None:
        raise RuntimeError(
            f"PANTHEON_STATUS_ROOT cannot include a symlink component: {symlink_component}"
        )

    root = expanded_root.resolve()
    if root != STATUS_ROOT:
        raise RuntimeError(
            f"PANTHEON_STATUS_ROOT binding mismatch: env resolves to {root}, "
            f"module is bound to {STATUS_ROOT}"
        )
    if not root.exists() or not root.is_dir():
        raise RuntimeError(f"PANTHEON_STATUS_ROOT does not exist or is not a directory: {root}")

    workspace_root = _worker_workspace_root()
    if workspace_root is not None and root == workspace_root:
        raise RuntimeError(
            "PANTHEON_STATUS_ROOT must point at the supervisor coordination "
            "root, not the isolated task worktree"
        )
    expected_root = _supervisor_expected_status_root()
    if expected_root is not None and root != expected_root:
        raise RuntimeError(
            "PANTHEON_STATUS_ROOT does not match the supervisor runtime "
            f"coordination root: {root} != {expected_root}"
        )

    git_root = _git_toplevel(root)
    if git_root != root:
        raise RuntimeError(
            f"PANTHEON_STATUS_ROOT must be a git repository root: {root}"
        )
    if not STATUS_FILE.exists() or not STATUS_FILE.is_file():
        raise RuntimeError(
            f"PANTHEON_STATUS_ROOT is missing required ai-status.json: {STATUS_FILE}"
        )
    symlink_comp_status = _first_symlink_component(STATUS_FILE)
    if symlink_comp_status is not None:
        raise RuntimeError(f"ai-status.json cannot be a symlink: {STATUS_FILE} (contains symlink component: {symlink_comp_status})")
    if _existing_path_is_symlink(STATUS_FILE):
        raise RuntimeError(f"ai-status.json cannot be a symlink: {STATUS_FILE}")

    for label, path in {
        "activity_log": LOG_FILE,
        "current_work": CURRENT_WORK_FILE,
        "docs_site": DOCS_SITE_DIR,
        "dashboard_bundle": DASHBOARD_BUNDLE_FILE,
        "planning_state": PLANNING_STATE_FILE,
        "orchestrator_state": ORCHESTRATOR_STATE_FILE,
        "approval_queue": APPROVAL_QUEUE_FILE,
        "archive_dir": task_archive_module.ARCHIVE_DIR,
        "archive_tasks_dir": task_archive_module.ARCHIVE_TASKS_DIR,
        "archive_index": task_archive_module.ARCHIVE_INDEX_FILE,
        "task_state_lock": canonical_task_state_lock_path(STATUS_FILE),
        "activity_audit_lock": activity_audit_lock_path(LOG_FILE),
        "docs_site_ai_status": DOCS_SITE_DIR / "ai-status.json",
        "docs_site_current_work": DOCS_SITE_DIR / "current-work.md",
        "docs_site_dashboard_bundle": DOCS_SITE_DIR / "dashboard-bundle.json",
        "docs_site_orchestrator_state": DOCS_SITE_DIR / "orchestrator-state.json",
        "docs_site_approval_queue": DOCS_SITE_DIR / "approval-queue.json",
        "docs_site_planning_state": DOCS_SITE_DIR / "planning-state.json",
        "docs_site_ai_activity_log": DOCS_SITE_DIR / "ai-activity-log.jsonl",
    }.items():
        if not _path_parent_under_root(Path(path), root):
            raise RuntimeError(
                f"PANTHEON_STATUS_ROOT path binding for {label} escapes root: {path}"
            )
        symlink_comp = _first_symlink_component(Path(path))
        if symlink_comp is not None:
            raise RuntimeError(
                f"PANTHEON_STATUS_ROOT path binding for {label} cannot be a symlink: {path} (contains symlink component: {symlink_comp})"
            )
        if _existing_path_is_symlink(Path(path)):
            raise RuntimeError(
                f"PANTHEON_STATUS_ROOT path binding for {label} cannot be a symlink: {path}"
            )

    for path, label in (
        (root / "ai-task-archive", "task archive"),
        (root / "archive" / "logs", "activity rotation archive"),
        (root / ".orchestrator" / "logs" / "activity-log-archive", "legacy activity archive"),
        (root / ".orchestrator" / "logs" / "activity-rotation", "activity rotation"),
        (root / ".orchestrator" / "worker-runtime", "worker runtime"),
    ):
        symlink_comp = _first_symlink_component(path)
        if symlink_comp is not None:
            raise RuntimeError(f"PANTHEON_STATUS_ROOT {label} component cannot be a symlink: {symlink_comp}")
        _validate_directory_no_symlinks_recursive(path, label)

    assert_task_archive_root_binding()

KNOWN_AGENTS = {
    "Claude": {
        "capability_lane": ["execution", "control-plane", "governance-review"],
        "default_branch": "feat/claude-execution-control",
        "target_workload": 5,
    },
    "Claude2": {
        "capability_lane": ["execution", "control-plane", "governance-review"],
        "default_branch": "feat/claude2-execution-control",
        "target_workload": 5,
    },
    "Antigravity": {
        "capability_lane": ["gcp", "ci-cd", "runtime-packaging", "worker-ops"],
        "default_branch": "feat/antigravity-research-runtime",
        "target_workload": 5,
    },
    "Antigravity2": {
        "capability_lane": ["gcp", "ci-cd", "runtime-packaging", "worker-ops"],
        "default_branch": "feat/antigravity2-research-runtime",
        "target_workload": 5,
    },
    "Gemini": {
        "capability_lane": ["gcp", "ci-cd", "runtime-packaging", "worker-ops"],
        "default_branch": "feat/gemini-research-runtime",
        "target_workload": 5,
    },
    "Gemini2": {
        "capability_lane": ["gcp", "ci-cd", "runtime-packaging", "worker-ops"],
        "default_branch": "feat/gemini2-research-runtime",
        "target_workload": 5,
    },
    "Codex": {
        "capability_lane": ["integration", "status-system", "schema", "acceptance"],
        "default_branch": "feat/codex-collab-system",
        "target_workload": 35,
    },
    "Codex2": {
        "capability_lane": ["integration", "status-system", "schema", "acceptance"],
        "default_branch": "feat/codex-collab-system",
        "target_workload": 35,
    },
    "Copilot": {
        "capability_lane": ["research-ingest", "external-search", "spec-review", "critique"],
        "default_branch": "feat/copilot-research-critique",
        "target_workload": 5,
    },
    "Human/Ops": {
        "capability_lane": ["human-gate", "operations", "signoff"],
        "default_branch": "human/ops",
        "target_workload": 0,
    },
}

AGENT_ALIASES = {
    "claude2": "Claude2",
    "claude 2": "Claude2",
    "gemini2": "Gemini2",
    "gemini 2": "Gemini2",
    "antigravity": "Antigravity",
    "antigravity2": "Antigravity2",
    "agy": "Antigravity",
    "agy2": "Antigravity2",
    "codex2": "Codex2",
    "codex (2)": "Codex2",
    "codex3": "Codex",
    "codex (3)": "Codex",
    "grok": "Copilot",
    "copilot": "Copilot",
    "copilot host": "Copilot",
    "copilot_host": "Copilot",
    "human": "Human/Ops",
    "human ops": "Human/Ops",
    "human/ops": "Human/Ops",
    "human-ops": "Human/Ops",
    "ops": "Human/Ops",
}

RETIRED_AGENT_REPLACEMENTS = {}

STATUS_LABELS = {
    "todo": "todo",
    "in_progress": "in_progress",
    "review": "review",
    "review_approved": "review_approved",
    "blocked": "blocked",
    "done": "done",
}

DEPENDENCY_DONE_STATUSES = {"done"}
ACTIVE_TASK_STATUSES = {"todo", "in_progress", "review", "review_approved", "blocked"}
EXTERNAL_TASK_PREFIXES = {"OC", "RS", "LP", "OSS", "SPIKE"}
EXTERNAL_TASK_ID_TOKENS = {
    "DATASOURCE",
    "OPENCLAW",
    "OSS",
    "SEARCH",
    "SOURCE",
}
EXTERNAL_TASK_TEXT_KEYWORDS = {
    "external",
    "external source",
    "external search",
    "openclaw",
    "oss",
    "searchgateway",
    "source/search",
    "source-ingest",
    "source_ingestion",
}
EXTERNAL_TASK_ARTIFACT_PREFIXES = (
    "integrations/",
    "services/openclaw",
    "services/search",
    "services/source_ingestion",
)
TASK_TERMINAL_SUPERSEDED = "superseded"
DEFAULT_DELIVERY_GATES = {
    "require_commit_hash": True,
    "require_git_clean": False,
    "record_remote_status": True,
    "require_merged_pr": True,
}
DEFAULT_COMMIT_CONVENTIONS = {
    "subject_must_include_task_id": True,
    "required_body_fields": ["LLM-Agent", "Task-ID", "Reviewer"],
}
COMMIT_TRAILER_SKIP_PREFIXES = (
    "Merge ",
    "Revert ",
    "promote:",
    "hotfix:",
    "publish:",
)
COMMIT_TRAILER_SKIP_RE = re.compile(r"^OPS-(?:GIT-(?:WORKFLOW|REDESIGN)|DOC|REBASE)-")
FIRST_PROMPT_PRIORITY = [
    "AI_COLLABORATION_GUIDE.md",
    "ai-status.json",
    "TARGET_ARCHITECTURE.md",
    "CANONICAL_DOCUMENT_MAP.md",
    "ROADMAP.md",
    "DEVELOPMENT_WORKBREAKDOWN.md",
    "WORKBENCH_DELIVERY_BACKLOG.md",
    "DELIVERY_CLOSURE_AND_LOOP_STATES.md",
    "EXECUTION_PROOF_AND_MATURITY_LEVELS.md",
]
OPTIONAL_CURRENT_WORK_REFERENCES = (
    ("CANONICAL_DOCUMENT_MAP.md", "Canonical map"),
    ("DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md", "Document boundary"),
    ("DEVELOPMENT_WORKBREAKDOWN.md", "Full backlog"),
    ("WORKBENCH_DELIVERY_BACKLOG.md", "Workbench backlog"),
    ("DELIVERY_CLOSURE_AND_LOOP_STATES.md", "Loop closure"),
    ("EXECUTION_PROOF_AND_MATURITY_LEVELS.md", "Execution proof"),
)
DISPLAY_TIMEZONE = ZoneInfo("Asia/Taipei")
DISPLAY_TIMEZONE_LABEL = "台灣時間 (UTC+8)"
ISO_TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\b")
FEATURE_MODULE_RE = re.compile(r"^([A-Z]+-\d{2,3})(?:-|$)")


def default_canonical_document_layers() -> dict[str, list[str]]:
    return {
        "L0 Collaboration & State": [
            "AI_COLLABORATION_GUIDE.md",
            "ai-status.json",
            "ai-activity-log.jsonl",
        ],
        "L0.5 Derived Narrative": [
            "current-work.md",
        ],
        "L1 Platform Architecture & Policy": [
            "TARGET_ARCHITECTURE.md",
            "OPENCLAW_RUNTIME_CONTRACT.md",
            "PERSONA_RUNTIME_MODEL.md",
            "BINDING_AND_DEPLOYMENT_SEMANTICS.md",
            "PAPER_CANARY_LIVE_POLICY.md",
            "ROLLBACK_AND_POSITION_SEMANTICS.md",
            "LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md",
            "EVOLUTION_REVIEW_AND_THRESHOLDS.md",
            "CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md",
            "KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md",
            "MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md",
            "TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md",
            "DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md",
            "EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md",
            "EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md",
            "BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md",
            "LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md",
        ],
        "L2 Planning & Execution": [
            "CANONICAL_DOCUMENT_MAP.md",
            "DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md",
            "ROADMAP.md",
            "DEVELOPMENT_WORKBREAKDOWN.md",
            "WORKBENCH_DELIVERY_BACKLOG.md",
            "DELIVERY_CLOSURE_AND_LOOP_STATES.md",
            "EXECUTION_PROOF_AND_MATURITY_LEVELS.md",
            "OSS_INTEGRATION_CHECKLIST.md",
        ],
        "L3 Supporting Design & Migration": [
            "CANONICAL_CONTRACT_MIGRATION_DECISION.md",
            "WORK_REBASELINE.md",
            "Pantheon_總索引版系統分析文件.md",
            "Pantheon_資料表_Schema_設計版.md",
            "Pantheon_API_Service_Contract_設計版.md",
        ],
    }


def flatten_canonical_document_layers(layers: dict[str, list[str]]) -> list[str]:
    flattened: list[str] = []
    for documents in layers.values():
        for document in documents:
            if document not in flattened:
                flattened.append(document)
    return flattened


def sync_canonical_document_metadata(state: dict[str, Any]) -> None:
    default_layers = default_canonical_document_layers()
    layers = state.get("canonical_document_layers")
    merge_default_layers = str(state.get("project") or "").strip() in {"", "pantheon"}
    if not isinstance(layers, dict) or not layers:
        layers = default_layers
    else:
        normalized_layers: dict[str, list[str]] = {}
        for key, value in layers.items():
            if isinstance(value, list):
                normalized_layers[str(key)] = [str(item) for item in value]
        if not normalized_layers:
            normalized_layers = default_layers
        elif merge_default_layers:
            for key, default_documents in default_layers.items():
                existing_documents = normalized_layers.get(key, [])
                merged_documents = list(existing_documents)
                for document in default_documents:
                    if document not in merged_documents:
                        merged_documents.append(document)
                normalized_layers[key] = merged_documents
        layers = normalized_layers
    current_work = "current-work.md"
    derived_layer = "L0.5 Derived Narrative"
    removed_current_work = False
    for key, documents in layers.items():
        if key == derived_layer:
            continue
        filtered = [document for document in documents if document != current_work]
        if len(filtered) != len(documents):
            removed_current_work = True
            layers[key] = filtered
    derived_documents = [str(item) for item in layers.get(derived_layer, []) if str(item).strip()]
    if current_work in derived_documents:
        derived_documents = [document for document in derived_documents if document != current_work]
    derived_payload = [current_work, *derived_documents] if (removed_current_work or derived_documents) else None
    if derived_payload is None and derived_layer in layers:
        derived_payload = [current_work]

    reordered_layers: dict[str, list[str]] = {}
    inserted = False
    for key, documents in layers.items():
        if key == derived_layer:
            continue
        reordered_layers[key] = documents
        if key == "L0 Collaboration & State" and derived_payload is not None:
            reordered_layers[derived_layer] = derived_payload
            inserted = True

    if derived_payload is not None and not inserted:
        reordered_layers[derived_layer] = derived_payload

    if not reordered_layers and derived_payload is not None:
        reordered_layers[derived_layer] = derived_payload

    layers = reordered_layers
    state["canonical_document_layers"] = layers
    state["canonical_files"] = flatten_canonical_document_layers(layers)


def canonical_file_set(state: dict[str, Any]) -> set[str]:
    sync_canonical_document_metadata(state)
    return {
        str(item)
        for item in state.get("canonical_files", [])
        if str(item).strip()
    }


def canonical_tier_labels(state: dict[str, Any]) -> list[str]:
    sync_canonical_document_metadata(state)
    layers = state.get("canonical_document_layers", {})
    return [f"`{name}`" for name in layers]


def human_join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def unique_strings(items: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def planning_reference_files(planning_state: dict[str, Any] | None) -> list[str]:
    if not planning_state:
        return []
    artifacts = planning_state.get("artifacts", {}) if isinstance(planning_state.get("artifacts"), dict) else {}
    files = [
        str(((artifacts.get("planning_readme") or {}).get("path")) or DEFAULT_PLANNING_README).strip(),
        str(planning_state.get("session_file") or ((artifacts.get("planning_session") or {}).get("path")) or DEFAULT_PLANNING_SESSION_FILE).strip(),
    ]
    files.extend(str(item).strip() for item in planning_state.get("brief_files", []) if str(item).strip())
    checklist_path = str(((artifacts.get("backend_completion_checklist") or {}).get("path")) or "").strip()
    if checklist_path:
        files.append(checklist_path)
    return unique_strings(files)


def build_onboarding_prompt(state: dict[str, Any]) -> str:
    canonical_files = canonical_file_set(state)
    prompt_files = [item for item in FIRST_PROMPT_PRIORITY if item in canonical_files]
    if not prompt_files:
        prompt_files = FIRST_PROMPT_PRIORITY[:2]

    parts = [f"Read {human_join(prompt_files)} first."]
    if "current-work.md" in canonical_files:
        parts.append("Use current-work.md as a human summary only; do not treat it as the primary machine context.")
    if "ai-activity-log.jsonl" in canonical_files:
        parts.append("Use ai-activity-log.jsonl only when you need targeted recent history.")
    planning_state = load_planning_state()
    if planning_state and planning_state.get("status") in {"active", "human_required"}:
        planning_files = planning_reference_files(planning_state)[:4]
        if planning_files:
            parts.append(
                f"Discussion planning is {planning_state['status']}; read {human_join(planning_files)} before implementation work."
            )
    parts.append("Treat generated views as derived from machine-readable state.")
    parts.append("Follow the canonical lifecycle todo -> in_progress -> review -> review_approved -> done.")
    parts.append("Use scripts/ai-status.sh for every state change.")
    return " ".join(parts)


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text or text == "-":
        return None
    normalized = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_display_timestamp(value: Any) -> str:
    parsed = parse_timestamp(value)
    if parsed is None:
        return "-" if value is None or value == "" else str(value)
    return parsed.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def localize_embedded_timestamps(text: Any) -> str:
    if text is None:
        return "-"
    rendered = str(text)
    if not rendered:
        return "-"
    return ISO_TIMESTAMP_RE.sub(lambda match: format_display_timestamp(match.group(0)), rendered)


def canonical_agent_name(name: str | None) -> str:
    if name is None:
        return ""
    trimmed = str(name).strip()
    if not trimmed:
        return ""
    canonical_by_lower = {agent.lower(): agent for agent in KNOWN_AGENTS}
    lowered = trimmed.lower()
    if lowered in canonical_by_lower:
        return canonical_by_lower[lowered]
    alias_target = AGENT_ALIASES.get(lowered)
    if alias_target:
        return alias_target
    return trimmed


def active_agent_name(name: str | None) -> str:
    canonical = canonical_agent_name(name)
    replacement = RETIRED_AGENT_REPLACEMENTS.get(canonical.lower())
    return replacement or canonical


def current_actor(default: str = "Codex") -> str:
    return canonical_agent_name(os.environ.get("AI_NAME", default))


def default_state() -> dict[str, Any]:
    timestamp = iso_now()
    canonical_layers = default_canonical_document_layers()
    return {
        "project": "pantheon",
        "sprint": "2026-04-09-canonical-adoption-platform-plan",
        "objective": (
            "Adopt the layered canonical document system, align architecture and planning truth, and publish the "
            "full Pantheon platform backlog without overwriting historical collaboration records."
        ),
        "updated_at": timestamp,
        "canonical_document_layers": canonical_layers,
        "canonical_files": flatten_canonical_document_layers(canonical_layers),
        "agents": [
            {
                "name": name,
                "capability_lane": meta["capability_lane"],
                "status": "idle",
                "current_task_ids": [],
                "branch": meta["default_branch"],
                "next": "",
                "last_update": None,
            }
            for name, meta in KNOWN_AGENTS.items()
        ],
        "tasks": [
            {
                "id": "P1-001",
                "title": "Define SignalStoreClient contract",
                "phase": "Phase 1",
                "owner": "Codex",
                "reviewer": "Gemini",
                "status": "todo",
                "depends_on": [],
                "artifacts": ["services/signal-store/client.py"],
                "acceptance": [
                    "interface documented",
                    "example payload added",
                    "consumer assumptions listed",
                ],
                "next": "Lock interface for downstream work",
                "last_update": None,
            },
            {
                "id": "P2-001",
                "title": "Define signal JSON schema",
                "phase": "Phase 2",
                "owner": "Gemini",
                "reviewer": "Claude",
                "status": "todo",
                "depends_on": ["P1-001"],
                "artifacts": ["services/research/schema.json"],
                "acceptance": [
                    "payload keys documented",
                    "worker contract references same schema",
                ],
                "next": "Publish worker payload contract",
                "last_update": None,
            },
            {
                "id": "P3-001",
                "title": "Wire LEAN runtime signal consumer",
                "phase": "Phase 3",
                "owner": "Claude",
                "reviewer": "Gemini",
                "status": "todo",
                "depends_on": ["P1-001", "P2-001"],
                "artifacts": ["services/execution/lean-runtime/"],
                "acceptance": [
                    "runtime can consume signal payload",
                    "broker config edge documented",
                ],
                "next": "Connect signal intake to execution plane",
                "last_update": None,
            },
            {
                "id": "P4-001",
                "title": "Draft control-plane routing contract",
                "phase": "Phase 4",
                "owner": "Claude",
                "reviewer": "Codex",
                "status": "todo",
                "depends_on": ["P2-001"],
                "artifacts": ["services/control-plane/router/"],
                "acceptance": [
                    "router contract documented",
                    "monitoring handoff documented",
                ],
                "next": "Define router and monitoring handoff",
                "last_update": None,
            },
        ],
        "handoffs": [],
        "blockers": [],
        "workload": {name: meta["target_workload"] for name, meta in KNOWN_AGENTS.items()},
    }


def _validate_task_state_projection_binding(store_mode: str) -> None:
    """Reject a journal transaction whose projection was rebound in-process.

    Background workers inherit the live task-state journal environment so that
    governed status commands can reach the canonical coordination root. Unit
    tests and helper processes sometimes override ``STATUS_FILE`` directly.
    Without this binding check, such a helper can append fixture state to the
    inherited live journal even though its intended projection is temporary.
    """

    expected = (STATUS_ROOT / "ai-status.json").expanduser().absolute()
    actual = STATUS_FILE.expanduser().absolute()
    if actual != expected:
        raise RuntimeError(
            f"{store_mode} task-state projection binding mismatch: "
            f"STATUS_FILE {actual} != STATUS_ROOT projection {expected}"
        )


def load_state() -> dict[str, Any]:
    store_mode = str(os.environ.get(TASK_STATE_STORE_MODE_ENV) or "").strip().lower()
    if store_mode == "authoritative":
        _validate_task_state_projection_binding(store_mode)
        event_path = _task_state_event_path(store_mode)
        # One validated pass over the journal: load_events followed by
        # project_latest_state replayed and revalidated every event twice, which
        # is the bulk of what a plain note command used to spend.
        transaction = getattr(_TASK_STATE_TRANSACTION_LOCAL, "transaction", None)
        snapshot = (
            transaction.load_snapshot()
            if transaction is not None
            else load_snapshot(event_path)
        )
        if not snapshot["event_count"]:
            raise SystemExit(
                "Authoritative task-state journal is empty; refusing ai-status.json fallback."
            )
        state = snapshot["state"]
        if not isinstance(state, dict) or not state:
            raise SystemExit("Authoritative task-state projection is not a non-empty object.")
        sync_canonical_document_metadata(state)
        normalize_state_agents(state)
        return state
    try:
        payload = read_regular_file_bytes(
            STATUS_FILE,
            source="canonical status state",
        )
    except FileNotFoundError:
        return default_state()
    if not payload.strip():
        raise SystemExit(f"Refusing to initialize from empty status file: {STATUS_FILE}")
    state = json.loads(payload.decode("utf-8", errors="strict"))
    sync_canonical_document_metadata(state)
    normalize_state_agents(state)
    return state


@contextmanager
def canonical_task_state_lock(*, shared: bool = False, nonblocking: bool = False):
    with canonical_task_state_lock_file(
        STATUS_FILE,
        shared=shared,
        nonblocking=nonblocking,
    ):
        yield


@contextmanager
def authoritative_task_state_transaction():
    """Reuse one validated journal snapshot across a governed mutation."""

    store_mode = str(os.environ.get(TASK_STATE_STORE_MODE_ENV) or "").strip().lower()
    if store_mode != "authoritative":
        yield
        return
    _validate_task_state_projection_binding(store_mode)
    event_path = _task_state_event_path(store_mode)
    if getattr(_TASK_STATE_TRANSACTION_LOCAL, "transaction", None) is not None:
        raise RuntimeError("nested authoritative task-state transaction")
    with snapshot_transaction(event_path) as transaction:
        _TASK_STATE_TRANSACTION_LOCAL.transaction = transaction
        try:
            yield
        finally:
            del _TASK_STATE_TRANSACTION_LOCAL.transaction


def load_logs() -> list[dict[str, Any]]:
    payload = read_activity_log_tail_bytes(LOG_FILE, max_lines=None)
    if payload is None:
        return []
    logs: list[dict[str, Any]] = []
    for line_no, line in enumerate(
        payload.decode("utf-8", errors="strict").splitlines(),
        start=1,
    ):
        line = line.strip()
        if not line:
            continue
        try:
            entry = strict_activity_json_loads(line)
        except DuplicateActivityJSONKeyError as exc:
            raise RuntimeError(
                f"ai-activity-log.jsonl line {line_no} contains {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            print(
                f"Warning: skipping malformed ai-activity-log.jsonl line {line_no}: {exc}",
                file=sys.stderr,
            )
            continue
        if not isinstance(entry, dict):
            raise RuntimeError(
                f"ai-activity-log.jsonl line {line_no} is not an object row"
            )
        logs.append(entry)
    return logs


def load_log_tail_lines(max_lines: int = 5000) -> list[str]:
    try:
        payload = read_activity_log_tail_bytes(
            LOG_FILE,
            max_lines=max_lines,
        )
    except OSError:
        return []
    if payload is None:
        return []
    return payload.decode("utf-8", errors="replace").splitlines()


def load_planning_state() -> dict[str, Any] | None:
    if not PLANNING_STATE_FILE.exists():
        return None
    try:
        payload = json.loads(PLANNING_STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return deepcopy(default)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return deepcopy(default)


def load_config() -> dict[str, Any]:
    payload = load_json_file(CONFIG_FILE, {})
    if not isinstance(payload, dict):
        return {}
    paths = payload.setdefault("paths", {})
    if isinstance(paths, dict):
        paths.update(
            {
                "status_file": str(STATUS_FILE),
                "activity_log": str(LOG_FILE),
                "current_work": str(CURRENT_WORK_FILE),
                "dashboard": str(DOCS_SITE_DIR / "index.html"),
                "state_file": str(ORCHESTRATOR_STATE_FILE),
                "event_queue": str(STATUS_ROOT / ".orchestrator" / "event-queue.jsonl"),
                "approval_queue": str(APPROVAL_QUEUE_FILE),
                "github_bus_state": str(STATUS_ROOT / ".orchestrator" / "github-bus-state.json"),
                "github_webhook_events": str(STATUS_ROOT / ".orchestrator" / "github-webhook-events.jsonl"),
                "github_relay_state": str(STATUS_ROOT / ".orchestrator" / "github-relay-state.json"),
                "provider_capabilities": str(STATUS_ROOT / ".orchestrator" / "provider_capabilities.json"),
            }
        )
    delivery_root = _worker_workspace_root()
    if delivery_root is not None:
        coordination = payload.setdefault("coordination", {})
        if isinstance(coordination, dict):
            repositories = coordination.setdefault("repositories", {})
            if isinstance(repositories, dict):
                pantheon_repo = repositories.setdefault("pantheon", {})
                if isinstance(pantheon_repo, dict):
                    pantheon_repo["local_path"] = str(delivery_root)
                    pantheon_repo.setdefault(
                        "repo",
                        str(((payload.get("github_bus") or {}).get("repo")) or "ajoe734/pantheon"),
                    )
    return payload


def bool_config_setting(settings: dict[str, Any], key: str, default: bool = False) -> bool:
    value = settings.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def int_config_setting(settings: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(settings.get(key, default))
    except (TypeError, ValueError):
        return default


def optional_int_config_setting(settings: dict[str, Any], key: str) -> int | None:
    value = settings.get(key)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def int_mapping_config_setting(settings: dict[str, Any], key: str) -> dict[str, int]:
    raw = settings.get(key)
    if not isinstance(raw, dict):
        return {}
    values: dict[str, int] = {}
    for name, value in raw.items():
        try:
            values[str(name)] = int(value)
        except (TypeError, ValueError):
            continue
    return values


def build_dispatch_policy_summary(config: dict[str, Any]) -> dict[str, Any]:
    ready_dispatcher = config.get("ready_dispatcher") if isinstance(config.get("ready_dispatcher"), dict) else {}
    helper_claim = ready_dispatcher.get("helper_claim") if isinstance(ready_dispatcher.get("helper_claim"), dict) else {}
    worker_self_claim = ready_dispatcher.get("worker_self_claim") if isinstance(ready_dispatcher.get("worker_self_claim"), dict) else {}
    claim_idle_work = bool_config_setting(helper_claim, "claim_idle_work", False)
    helper_claim_enabled = bool_config_setting(helper_claim, "enabled", True)
    worker_self_claim_enabled = bool_config_setting(worker_self_claim, "enabled", False)
    account_cap_key = (
        "max_concurrent_per_account"
        if "max_concurrent_per_account" in ready_dispatcher
        else "max_concurrent_per_quota_group"
    )
    account_caps = int_mapping_config_setting(ready_dispatcher, account_cap_key)
    return {
        "mode": "worker_self_claim" if worker_self_claim_enabled else ("idle_worker_claim" if helper_claim_enabled and claim_idle_work else "supervisor_owned_dispatch"),
        "worker_self_claim_enabled": worker_self_claim_enabled,
        "worker_self_claim_command": worker_self_claim.get("claim_command") or "",
        "helper_claim_enabled": helper_claim_enabled,
        "claim_idle_work": claim_idle_work,
        "claim_sidecars_when_idle": bool_config_setting(helper_claim, "claim_sidecars_when_idle", False),
        "require_owner_higher_priority_load": bool_config_setting(helper_claim, "require_owner_higher_priority_load", True),
        "owned_work_first": True,
        "max_dispatches_per_tick": int_config_setting(ready_dispatcher, "max_dispatches_per_tick", 4),
        "max_tasks_per_agent": optional_int_config_setting(ready_dispatcher, "max_tasks_per_agent"),
        "max_tasks_per_agent_by_agent": int_mapping_config_setting(ready_dispatcher, "max_tasks_per_agent_by_agent"),
        "max_concurrent_per_account": account_caps,
        # Deprecated dashboard compatibility alias; source configs use account.
        "max_concurrent_per_quota_group": account_caps,
        "sidecar_only_agents": ready_dispatcher.get("sidecar_only_agents") or [],
        "disabled_agents": ready_dispatcher.get("disabled_agents") or [],
    }


def _dashboard_agent_id(config: dict[str, Any], agent_name: str | None) -> str:
    raw = str(agent_name or "").strip()
    canonical = canonical_agent_name(raw)
    candidates = {
        raw.lower(),
        canonical.lower(),
        raw.lower().replace("-", "_"),
        canonical.lower().replace("-", "_"),
    }
    for agent_id, agent in (config.get("agents", {}) or {}).items():
        display_name = str((agent or {}).get("display_name") or "").strip()
        values = {
            str(agent_id).lower(),
            str(agent_id).lower().replace("-", "_"),
            display_name.lower(),
            display_name.lower().replace("-", "_"),
        }
        if candidates & values:
            return str(agent_id)
    return canonical or raw


def _dashboard_slot_count(config: dict[str, Any], agent_id: str) -> int:
    agents = config.get("agents", {}) or {}
    agent = agents.get(agent_id) or {}
    slots = [str(slot or "").strip() for slot in (agent.get("worker_slots", []) or [])]
    slot_ids = {slot for slot in slots if slot and slot in agents}
    for slot_id, slot_agent in agents.items():
        if str((slot_agent or {}).get("dispatch_slot_for") or "").strip() == agent_id:
            slot_ids.add(str(slot_id))
    return len(slot_ids)


def dashboard_agent_capacity(config: dict[str, Any], agent_name: str | None) -> int:
    ready_dispatcher = config.get("ready_dispatcher") if isinstance(config.get("ready_dispatcher"), dict) else {}
    caps = ready_dispatcher.get("max_tasks_per_agent_by_agent")
    agent_id = _dashboard_agent_id(config, agent_name)
    canonical = canonical_agent_name(agent_name)
    lookup_keys = {
        str(agent_name or "").strip().lower(),
        canonical.lower(),
        agent_id.lower(),
        agent_id.lower().replace("_", "-"),
        agent_id.lower().replace("-", "_"),
    }
    if isinstance(caps, dict):
        for key, value in caps.items():
            if str(key).strip().lower() not in lookup_keys:
                continue
            try:
                return max(1, int(value))
            except (TypeError, ValueError):
                continue

    default_capacity = optional_int_config_setting(ready_dispatcher, "max_tasks_per_agent")
    slot_count = _dashboard_slot_count(config, agent_id)
    if slot_count:
        return max(default_capacity or 0, slot_count)
    return default_capacity or 1


def recent_helper_claims(limit: int = 8, max_scan_lines: int = 5000) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for line_no, line in enumerate(reversed(load_log_tail_lines(max_lines=max_scan_lines)), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = strict_activity_json_loads(stripped)
        except DuplicateActivityJSONKeyError as exc:
            raise RuntimeError(
                "ai-activity-log.jsonl tail line "
                f"-{line_no} contains {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            print(
                f"Warning: skipping malformed ai-activity-log.jsonl tail line -{line_no}: {exc}",
                file=sys.stderr,
            )
            continue
        if not isinstance(entry, dict):
            raise RuntimeError(
                f"ai-activity-log.jsonl tail line -{line_no} is not an object row"
            )
        if str(entry.get("type") or "") != "task_helper_claimed":
            continue
        claims.append(
            {
                "task_id": entry.get("task_id"),
                "from_owner": entry.get("from_owner") or entry.get("from"),
                "to_owner": entry.get("to_owner") or entry.get("to"),
                "new_reviewer": entry.get("new_reviewer") or entry.get("reviewer"),
                "message": entry.get("message"),
                "ts": entry.get("ts") or entry.get("updated_at"),
            }
        )
        if len(claims) >= limit:
            break
    return claims


def save_state(state: dict[str, Any]) -> None:
    store_mode = str(os.environ.get(TASK_STATE_STORE_MODE_ENV) or "").strip().lower()
    if store_mode == "authoritative":
        _validate_task_state_projection_binding(store_mode)
        event_path = _task_state_event_path(store_mode)
        source = (
            str(os.environ.get("ORCH_RUN_ID") or "").strip()
            or str(os.environ.get("AI_NAME") or "").strip()
            or "ai-status"
        )
        transaction = getattr(_TASK_STATE_TRANSACTION_LOCAL, "transaction", None)
        if transaction is not None:
            transaction.append_state_commit(state, source=source)
        else:
            append_state_commit(event_path, state, source=source)
    serialized = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=STATUS_FILE.parent, delete=False) as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    os.replace(temp_path, STATUS_FILE)
    _fsync_directory(STATUS_FILE.parent)
    if STATUS_FILE.read_text(encoding="utf-8") != serialized:
        raise RuntimeError("canonical task-state readback mismatch")
    if store_mode == "shadow":
        _append_task_state_shadow(state)


def _task_state_event_path(mode: str) -> Path:
    raw_path = str(os.environ.get(TASK_STATE_EVENT_LOG_ENV) or "").strip()
    if not raw_path:
        raise SystemExit(f"{TASK_STATE_EVENT_LOG_ENV} is required in {mode} mode")
    event_path = Path(os.path.expanduser(raw_path))
    if not event_path.is_absolute():
        raise SystemExit(f"{TASK_STATE_EVENT_LOG_ENV} must be an absolute path")
    return event_path


def _append_task_state_shadow(state: dict[str, Any]) -> None:
    if str(os.environ.get(TASK_STATE_STORE_MODE_ENV) or "").strip().lower() != "shadow":
        return
    try:
        _validate_task_state_projection_binding("shadow")
        event_path = _task_state_event_path("shadow")
        source = (
            str(os.environ.get("ORCH_RUN_ID") or "").strip()
            or str(os.environ.get("AI_NAME") or "").strip()
            or "ai-status"
        )
        append_state_commit(event_path, state, source=source)
    except Exception as exc:
        # Shadow mode cannot make the incumbent canonical write unavailable.
        print(f"Warning: task-state shadow append failed: {exc}", file=sys.stderr)


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure_sprint_started_at(state: dict[str, Any]) -> None:
    current_sprint = str(state.get("sprint") or "").strip()
    if not current_sprint:
        return
    on_disk: dict[str, Any] = {}
    if STATUS_FILE.exists():
        try:
            on_disk = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            on_disk = {}
    on_disk_sprint = str(on_disk.get("sprint") or "").strip()
    on_disk_started_at = on_disk.get("sprint_started_at")
    if on_disk_sprint == current_sprint and on_disk_started_at:
        state["sprint_started_at"] = on_disk_started_at
        return
    state["sprint_started_at"] = iso_now()


def count_terminal_since(threshold_iso: str | None) -> tuple[int, int]:
    if not threshold_iso:
        return (0, 0)
    try:
        threshold = datetime.fromisoformat(str(threshold_iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return (0, 0)
    completed_count = 0
    superseded_count = 0
    if not ARCHIVE_TASKS_DIR.exists():
        return (0, 0)
    for path in ARCHIVE_TASKS_DIR.glob("*.json"):
        try:
            text = task_archive_module.read_task_archive_file_safe(path)
            snapshot = json.loads(text)
        except (OSError, json.JSONDecodeError):
            continue
        archived_at_raw = str(snapshot.get("archived_at") or "").strip()
        if not archived_at_raw:
            continue
        try:
            archived_at = datetime.fromisoformat(archived_at_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if archived_at < threshold:
            continue
        outcome = str(snapshot.get("terminal_outcome") or "").strip().lower()
        if outcome == "superseded":
            superseded_count += 1
        else:
            completed_count += 1
    return (completed_count, superseded_count)


def task_resolver(state: dict[str, Any]) -> TaskResolver:
    return TaskResolver(state.get("tasks", []))


def archived_task_snapshot(task_id: str) -> dict[str, Any] | None:
    return load_archived_snapshot(task_id)


def task_archive_recent_limit() -> int:
    return DEFAULT_ARCHIVE_RECENT_LIMIT


def assert_task_archive_root_binding() -> None:
    expected_status = STATUS_FILE.expanduser().resolve()
    expected_archive = expected_status.parent / "ai-task-archive"
    bindings = {
        "STATUS_FILE": task_archive_module.STATUS_FILE.expanduser().resolve(),
        "ARCHIVE_DIR": task_archive_module.ARCHIVE_DIR.expanduser().resolve(),
        "ARCHIVE_TASKS_DIR": task_archive_module.ARCHIVE_TASKS_DIR.expanduser().resolve(),
        "ARCHIVE_INDEX_FILE": task_archive_module.ARCHIVE_INDEX_FILE.expanduser().resolve(),
    }
    expected = {
        "STATUS_FILE": expected_status,
        "ARCHIVE_DIR": expected_archive,
        "ARCHIVE_TASKS_DIR": expected_archive / "tasks",
        "ARCHIVE_INDEX_FILE": expected_archive / "index.json",
    }
    if bindings != expected:
        raise RuntimeError(
            "task archive/status root binding mismatch; refusing split task-state locks"
        )


def _status_archive_terminal_outcome(task: Any) -> str:
    """Return the exact archive outcome, with one legacy compatibility case."""

    if not isinstance(task, dict) or task.get("status") != "done":
        return ""
    if "terminal_outcome" not in task:
        return "completed"
    outcome = task.get("terminal_outcome")
    if outcome in {"completed", "superseded"}:
        return str(outcome)
    return ""


def archive_terminal_task_from_state(state: dict[str, Any], task: dict[str, Any], *, archived_at: str | None = None) -> dict[str, Any]:
    assert_task_archive_root_binding()
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        raise SystemExit("Cannot archive a task without an id")
    terminal_outcome = _status_archive_terminal_outcome(task)
    if not terminal_outcome:
        raise RuntimeError(
            f"terminal task has invalid archive outcome: {task_id}"
        )
    related_handoffs = [deepcopy(handoff) for handoff in state.get("handoffs", []) if handoff.get("task_id") == task_id]
    related_blockers = [deepcopy(blocker) for blocker in state.get("blockers", []) if blocker.get("task_id") == task_id]
    existing = archived_task_snapshot(task_id)
    snapshot = {
        "version": 1,
        "task_id": task_id,
        "archived_at": archived_at
        or (
            str(existing.get("archived_at") or "").strip()
            if isinstance(existing, dict)
            else ""
        )
        or iso_now(),
        "terminal_status": "done",
        "terminal_outcome": terminal_outcome,
        "task": deepcopy(task),
        "handoffs": related_handoffs,
        "blockers": related_blockers,
    }
    _validate_status_archive_snapshot(snapshot)
    if existing is not None:
        _validate_status_archive_snapshot(existing)
        if is_terminal_task(task):
            if _canonical_json_sha256(existing) != _canonical_json_sha256(snapshot):
                raise RuntimeError(
                    f"existing archive snapshot conflicts with terminal task: {task_id}"
                )
        snapshot = deepcopy(existing)

    pending = state.get(STATUS_ARCHIVE_OUTBOX_KEY)
    snapshots: list[dict[str, Any]] = []
    if pending not in (None, {}, []):
        snapshots = list(_validate_status_archive_outbox(pending)["snapshots"])
    same_task = [
        item
        for item in snapshots
        if str(item.get("task_id") or "") == task_id
    ]
    if same_task and any(
        _canonical_json_sha256(item) != _canonical_json_sha256(snapshot)
        for item in same_task
    ):
        raise RuntimeError(f"archive outbox payload conflict: {task_id}")
    if not same_task:
        snapshots.append(snapshot)
    state[STATUS_ARCHIVE_OUTBOX_KEY] = {
        "schema_version": STATUS_ARCHIVE_OUTBOX_SCHEMA_VERSION,
        "transaction_id": "ai-status-archive-tx-"
        + _canonical_json_sha256(snapshots),
        "snapshots": snapshots,
    }
    # Retain the terminal row and its references until the archive and rebuilt
    # index are durable. Recovery removes them in the same final status write
    # that clears this outbox, so readers never observe a vanished task.
    return snapshot


def archive_terminal_tasks_in_state(state: dict[str, Any], *, archived_at: str | None = None) -> list[str]:
    archived_ids: list[str] = []
    for task in list(state.get("tasks", [])):
        if not is_terminal_task(task):
            continue
        snapshot = archive_terminal_task_from_state(state, task, archived_at=archived_at)
        archived_ids.append(str(snapshot.get("task_id") or task.get("id") or ""))
    return [task_id for task_id in archived_ids if task_id]


def prune_archived_active_tasks(state: dict[str, Any]) -> list[str]:
    """Remove invalid active rows whose task id already has an archive snapshot."""

    pruned_ids: list[str] = []
    remaining_tasks: list[dict[str, Any]] = []
    for task in list(state.get("tasks", [])):
        task_id = str(task.get("id") or "").strip()
        existing = archived_task_snapshot(task_id) if task_id else None
        if existing:
            archive_terminal_task_from_state(
                state,
                task,
                archived_at=str(existing.get("archived_at") or "").strip() or None,
            )
            pruned_ids.append(task_id)
            continue
        remaining_tasks.append(task)
    if not pruned_ids:
        return []

    pruned = set(pruned_ids)
    state["tasks"] = remaining_tasks
    state["handoffs"] = [handoff for handoff in state.get("handoffs", []) if handoff.get("task_id") not in pruned]
    state["blockers"] = [blocker for blocker in state.get("blockers", []) if blocker.get("task_id") not in pruned]
    return pruned_ids


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _activity_event(entry: dict[str, Any]) -> dict[str, Any]:
    event = deepcopy(entry)
    command_metadata = status_command_metadata()
    if command_metadata and "status_command" not in event:
        event["status_command"] = command_metadata
    if not str(event.get("event_id") or "").strip():
        event["event_id"] = "ai-status-event-" + _canonical_json_sha256(event)
    return event


@contextmanager
def buffer_activity_events():
    previous = getattr(_ACTIVITY_TRANSACTION_LOCAL, "events", None)
    events: list[dict[str, Any]] = []
    _ACTIVITY_TRANSACTION_LOCAL.events = events
    try:
        yield events
    finally:
        if previous is None:
            try:
                delattr(_ACTIVITY_TRANSACTION_LOCAL, "events")
            except AttributeError:
                pass
        else:
            _ACTIVITY_TRANSACTION_LOCAL.events = previous


def _maybe_rotate_activity_log_unlocked(log_path: Path) -> Path | None:
    """Durably rotate through the shared restart-recoverable intent protocol."""

    return rotate_activity_log_unlocked(
        log_path,
        max_bytes=LOG_ROTATE_MAX_BYTES,
        keep_lines=LOG_ROTATE_KEEP_LINES,
        archive_dir=log_path.parent / "archive" / "logs",
    )


def maybe_rotate_activity_log(path: Path | None = None) -> Path | None:
    log_path = path if path is not None else LOG_FILE
    with activity_audit_lock_file(log_path, shared=False, nonblocking=False):
        return _maybe_rotate_activity_log_unlocked(log_path)


def _append_log_unlocked(entry: dict[str, Any]) -> None:
    _append_logs_unlocked([entry])


def _append_logs_unlocked(entries: list[dict[str, Any]]) -> None:
    append_activity_log_entries_unlocked(
        LOG_FILE,
        entries,
        rotate_bytes=LOG_ROTATE_MAX_BYTES,
        keep_lines=LOG_ROTATE_KEEP_LINES,
    )


def append_log(entry: dict[str, Any]) -> None:
    event = _activity_event(entry)
    buffer = getattr(_ACTIVITY_TRANSACTION_LOCAL, "events", None)
    if isinstance(buffer, list):
        buffer.append(event)
        return
    with activity_audit_lock_file(LOG_FILE, shared=False, nonblocking=False):
        _append_log_unlocked(event)


def _activity_audit_sources() -> list[Path]:
    return activity_audit_source_paths_unlocked(LOG_FILE)


def _activity_event_index_unlocked(event_ids: set[str]) -> dict[str, str]:
    try:
        prepare_activity_audit_unlocked(LOG_FILE)
        return validated_activity_event_digests_unlocked(LOG_FILE, event_ids)
    except ActivityAuditInvariantError:
        raise
    except RuntimeError as exc:
        raise activity_audit_invariant_error(
            exc,
            log_path=LOG_FILE,
            operation="status_outbox_recovery",
        ) from exc


def _active_activity_event_digests_unlocked(
    event_ids: set[str],
) -> dict[str, str]:
    """Look for a just-appended outbox transaction in a bounded active tail."""

    payload = read_activity_log_tail_bytes(
        LOG_FILE,
        max_lines=max(64, len(event_ids) * 8),
    )
    result: dict[str, str] = {}
    for line_number, raw_line in enumerate((payload or b"").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            entry = strict_activity_json_loads(
                raw_line.decode("utf-8", errors="strict")
            )
        except (
            UnicodeError,
            json.JSONDecodeError,
            DuplicateActivityJSONKeyError,
        ) as exc:
            raise ActivityAuditInvariantError(
                "active activity tail is unreadable",
                invariant="activity_tail_json",
                evidence={
                    "log_path": str(LOG_FILE),
                    "tail_line_number": line_number,
                    "error_type": type(exc).__name__,
                },
            ) from exc
        if not isinstance(entry, dict):
            raise ActivityAuditInvariantError(
                "active activity tail row is not an object",
                invariant="activity_tail_json",
                evidence={
                    "log_path": str(LOG_FILE),
                    "tail_line_number": line_number,
                },
            )
        event_id = str(entry.get("event_id") or "")
        if event_id not in event_ids:
            continue
        digest = _canonical_json_sha256(entry)
        existing = result.get(event_id)
        if existing is not None and existing != digest:
            raise RuntimeError(f"activity outbox payload conflict: {event_id}")
        result[event_id] = digest
    return result


def _validate_status_archive_outbox(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "transaction_id",
        "snapshots",
    }:
        raise RuntimeError("status archive outbox schema is not exact")
    snapshots = value.get("snapshots")
    if (
        value.get("schema_version") != STATUS_ARCHIVE_OUTBOX_SCHEMA_VERSION
        or not isinstance(snapshots, list)
        or not snapshots
        or any(not _status_archive_snapshot_is_valid(snapshot) for snapshot in snapshots)
        or len({str(snapshot["task_id"]) for snapshot in snapshots})
        != len(snapshots)
    ):
        raise RuntimeError("status archive outbox contract is invalid")
    expected_id = "ai-status-archive-tx-" + _canonical_json_sha256(snapshots)
    if value.get("transaction_id") != expected_id:
        raise RuntimeError("status archive outbox digest mismatch")
    return value


def _status_archive_snapshot_is_valid(snapshot: Any) -> bool:
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "version",
        "task_id",
        "archived_at",
        "terminal_status",
        "terminal_outcome",
        "task",
        "handoffs",
        "blockers",
    }:
        return False
    task = snapshot.get("task")
    terminal_outcome = _status_archive_terminal_outcome(task)
    return bool(
        snapshot.get("version") == 1
        and snapshot.get("terminal_status") == "done"
        and str(snapshot.get("task_id") or "").strip()
        and str(snapshot.get("archived_at") or "").strip()
        and isinstance(task, dict)
        and task.get("id") == snapshot.get("task_id")
        and task.get("status") == "done"
        and terminal_outcome
        and snapshot.get("terminal_outcome") == terminal_outcome
        and isinstance(snapshot.get("handoffs"), list)
        and isinstance(snapshot.get("blockers"), list)
    )


def _validate_status_archive_snapshot(snapshot: Any) -> dict[str, Any]:
    if not _status_archive_snapshot_is_valid(snapshot):
        raise RuntimeError("status archive snapshot contract is invalid")
    return snapshot


def _status_archive_fault(point: str) -> None:
    if str(os.environ.get("LOOP_TEST_ARCHIVE_SIGKILL_AFTER") or "").strip() == point:
        os.kill(os.getpid(), 9)


def recover_status_archive_outbox(state: dict[str, Any]) -> bool:
    pending = state.get(STATUS_ARCHIVE_OUTBOX_KEY)
    if pending in (None, {}, []):
        return False
    assert_task_archive_root_binding()
    pending = _validate_status_archive_outbox(pending)
    for expected in pending["snapshots"]:
        actual = archive_task_snapshot(
            deepcopy(expected["task"]),
            handoffs=deepcopy(expected["handoffs"]),
            blockers=deepcopy(expected["blockers"]),
            archived_at=str(expected["archived_at"]),
            recent_limit=task_archive_recent_limit(),
        )
        if _canonical_json_sha256(actual) != _canonical_json_sha256(expected):
            raise RuntimeError(
                f"status archive outbox readback mismatch: {expected['task_id']}"
            )
    rebuild_archive_index(recent_limit=task_archive_recent_limit())
    _status_archive_fault("rebuild")
    archived_ids = {str(item["task_id"]) for item in pending["snapshots"]}
    active_by_id = {
        str(task.get("id") or ""): task
        for task in state.get("tasks", [])
        if isinstance(task, dict)
    }
    for expected in pending["snapshots"]:
        task_id = str(expected["task_id"])
        active = active_by_id.get(task_id)
        if active is not None and _canonical_json_sha256(active) != _canonical_json_sha256(
            expected["task"]
        ):
            raise RuntimeError(
                f"active terminal task changed during archive recovery: {task_id}"
            )
    state["tasks"] = [
        task
        for task in state.get("tasks", [])
        if str(task.get("id") or "") not in archived_ids
    ]
    state["handoffs"] = [
        handoff
        for handoff in state.get("handoffs", [])
        if str(handoff.get("task_id") or "") not in archived_ids
    ]
    state["blockers"] = [
        blocker
        for blocker in state.get("blockers", [])
        if str(blocker.get("task_id") or "") not in archived_ids
    ]
    state[STATUS_ARCHIVE_OUTBOX_KEY] = None
    save_state(state)
    return True


def _validate_status_activity_outbox(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "transaction_id",
        "events",
    }:
        raise RuntimeError("status activity outbox schema is not exact")
    events = value.get("events")
    if (
        value.get("schema_version") != STATUS_ACTIVITY_OUTBOX_SCHEMA_VERSION
        or not isinstance(events, list)
        or not events
        or any(
            not isinstance(event, dict)
            or not isinstance(event.get("event_id"), str)
            or not event["event_id"]
            or event["event_id"] != event["event_id"].strip()
            for event in events
        )
        or len({str(event["event_id"]) for event in events}) != len(events)
    ):
        raise RuntimeError("status activity outbox contract is invalid")
    expected_id = "ai-status-tx-" + _canonical_json_sha256(events)
    if value.get("transaction_id") != expected_id:
        raise RuntimeError("status activity outbox digest mismatch")
    return value


def recover_status_activity_outbox(
    state: dict[str, Any],
    *,
    known_unappended: bool = False,
) -> bool:
    pending = state.get(STATUS_ACTIVITY_OUTBOX_KEY)
    if pending in (None, {}, []):
        return False
    pending = _validate_status_activity_outbox(pending)
    pending_event_ids = {str(event["event_id"]) for event in pending["events"]}
    with activity_audit_lock_file(LOG_FILE, shared=False, nonblocking=False):
        existing = (
            {}
            if known_unappended
            else _activity_event_index_unlocked(pending_event_ids)
        )
        missing: list[dict[str, Any]] = []
        for event in pending["events"]:
            event_id = str(event["event_id"])
            digest = _canonical_json_sha256(event)
            if event_id in existing:
                if existing[event_id] != digest:
                    raise RuntimeError(
                        f"activity outbox payload conflict: {event_id}"
                    )
                continue
            missing.append(event)
            existing[event_id] = digest
        _append_logs_unlocked(missing)
        final = _active_activity_event_digests_unlocked(pending_event_ids)
        if set(final) != pending_event_ids:
            final = _activity_event_index_unlocked(pending_event_ids)
        if any(
            final.get(str(event["event_id"])) != _canonical_json_sha256(event)
            for event in pending["events"]
        ):
            raise RuntimeError("status activity outbox append/readback mismatch")
        state[STATUS_ACTIVITY_OUTBOX_KEY] = None
        save_state(state)
    return True


def commit_state_with_activity_outbox(
    state: dict[str, Any],
    events: list[dict[str, Any]],
) -> None:
    if events:
        state[STATUS_ACTIVITY_OUTBOX_KEY] = {
            "schema_version": STATUS_ACTIVITY_OUTBOX_SCHEMA_VERSION,
            "transaction_id": "ai-status-tx-" + _canonical_json_sha256(events),
            "events": deepcopy(events),
        }
    save_state(state)
    if state.get(STATUS_ARCHIVE_OUTBOX_KEY) not in (None, {}, []):
        _status_archive_fault("pending_status")
    recover_status_archive_outbox(state)
    if events:
        recover_status_activity_outbox(state, known_unappended=True)


def ensure_agent(name: str) -> dict[str, Any]:
    canonical = canonical_agent_name(name)
    if canonical not in KNOWN_AGENTS:
        raise SystemExit(f"Unknown agent: {name}")
    return KNOWN_AGENTS[canonical]


def get_agent(state: dict[str, Any], name: str) -> dict[str, Any]:
    name = canonical_agent_name(name)
    ensure_agent(name)
    for agent in state["agents"]:
        if agent["name"] == name:
            return agent
    meta = KNOWN_AGENTS[name]
    agent = {
        "name": name,
        "capability_lane": meta["capability_lane"],
        "status": "idle",
        "current_task_ids": [],
        "branch": meta["default_branch"],
        "next": "",
        "last_update": None,
    }
    state["agents"].append(agent)
    return agent


def get_task(state: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    for task in state["tasks"]:
        if task["id"] == task_id:
            return task
    return None


def parse_csv_env(name: str) -> list[str]:
    value = os.environ.get(name, "").strip()
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_delimited_env(name: str, delimiter: str = "||") -> list[str]:
    value = os.environ.get(name, "").strip()
    if not value:
        return []
    return [item.strip() for item in value.split(delimiter) if item.strip()]


def parse_json_env(name: str) -> dict[str, Any]:
    value = os.environ.get(name, "").strip()
    if not value:
        return {}
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise SystemExit(f"{name} must decode to a JSON object")
    return payload


def parse_bool_env(name: str) -> bool | None:
    value = os.environ.get(name)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"{name} must be a boolean-like string")


def delivery_gate_settings() -> dict[str, bool]:
    settings = dict(DEFAULT_DELIVERY_GATES)
    config = load_config()
    payload = config.get("delivery_gates", {})
    if isinstance(payload, dict):
        for key in DEFAULT_DELIVERY_GATES:
            value = payload.get(key)
            if isinstance(value, bool):
                settings[key] = value

    env_overrides = {
        "TASK_REQUIRE_COMMIT_HASH": "require_commit_hash",
        "TASK_REQUIRE_GIT_CLEAN": "require_git_clean",
        "TASK_RECORD_REMOTE_STATUS": "record_remote_status",
        "TASK_REQUIRE_MERGED_PR": "require_merged_pr",
    }
    for env_name, field_name in env_overrides.items():
        parsed = parse_bool_env(env_name)
        if parsed is not None:
            settings[field_name] = parsed
    return settings


def commit_convention_settings() -> dict[str, Any]:
    settings = deepcopy(DEFAULT_COMMIT_CONVENTIONS)
    config = load_config()
    payload = config.get("commit_conventions", {})
    if isinstance(payload, dict):
        subject_required = payload.get("subject_must_include_task_id")
        if isinstance(subject_required, bool):
            settings["subject_must_include_task_id"] = subject_required
        required_fields = payload.get("required_body_fields")
        if isinstance(required_fields, list):
            normalized = [str(item).strip() for item in required_fields if str(item).strip()]
            if normalized:
                settings["required_body_fields"] = normalized

    subject_override = parse_bool_env("TASK_REQUIRE_SUBJECT_TASK_ID")
    if subject_override is not None:
        settings["subject_must_include_task_id"] = subject_override

    body_fields = os.environ.get("TASK_COMMIT_REQUIRED_FIELDS", "").strip()
    if body_fields:
        settings["required_body_fields"] = [item.strip() for item in body_fields.split(",") if item.strip()]
    return settings


def run_git_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    required: bool = True,
    failure_message: str | None = None,
) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        if required:
            detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
            raise SystemExit(failure_message or detail)
        return ""
    return result.stdout.strip()


def git_command_succeeds(args: list[str], *, cwd: Path | None = None) -> bool:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def run_gh_json_command(args: list[str], *, cwd: Path | None = None) -> dict[str, Any] | None:
    result = subprocess.run(
        ["gh", *args],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def classify_push_status(ahead: int, behind: int) -> str:
    if ahead == 0 and behind == 0:
        return "in_sync"
    if ahead > 0 and behind == 0:
        return "ahead"
    if ahead == 0 and behind > 0:
        return "behind"
    return "diverged"


def delivery_merge_target_branch(config: dict[str, Any], repository_id: str) -> str:
    if repository_id == "pantheon":
        branch = str((config.get("branch_workflow") or {}).get("dev_branch") or "").strip()
        if branch:
            return branch
    repo = resolve_repository(config, repository_id)
    branch = str(repo.get("default_branch") or "").strip()
    return branch or "main"


def pull_request_status_for_branch(repository_root: Path, branch: str) -> dict[str, Any] | None:
    if not branch or branch == "HEAD":
        return None
    return run_gh_json_command(
        [
            "pr",
            "view",
            branch,
            "--json",
            "number,state,mergeStateStatus,mergedAt,mergeCommit,autoMergeRequest,url",
        ],
        cwd=repository_root,
    )


def format_pull_request_status(pr: dict[str, Any] | None) -> str:
    if not pr:
        return ""
    number = pr.get("number")
    state = str(pr.get("state") or "unknown")
    merge_state = str(pr.get("mergeStateStatus") or "unknown")
    url = str(pr.get("url") or "").strip()
    auto_merge = "enabled" if pr.get("autoMergeRequest") else "disabled"
    prefix = f" PR #{number}" if number else " PR"
    parts = [f"{prefix} is {state}", f"mergeState={merge_state}", f"autoMerge={auto_merge}"]
    if url:
        parts.append(url)
    return "; ".join(parts)


def enforce_delivery_merged_gate(
    config: dict[str, Any],
    delivery: dict[str, Any],
    *,
    repository_root: Path,
    repository_id: str,
    branch: str,
    remote_names: list[str],
) -> None:
    target_branch = delivery_merge_target_branch(config, repository_id)
    delivery["merge_target_branch"] = target_branch
    if not remote_names:
        raise SystemExit(
            "Cannot finalize task: delivery_gates.require_merged_pr is enabled, "
            "but the repository has no git remote to verify the task PR merge."
        )
    remote = "origin" if "origin" in remote_names else remote_names[0]
    target_ref = f"{remote}/{target_branch}"
    delivery["merge_target_ref"] = target_ref
    run_git_command(["fetch", remote, target_branch], cwd=repository_root, required=False)
    target_sha = run_git_command(
        ["rev-parse", "--verify", target_ref],
        cwd=repository_root,
        required=False,
    )
    if not target_sha:
        raise SystemExit(
            "Cannot finalize task: unable to verify task PR merge because "
            f"`{target_ref}` is unavailable."
        )
    delivery["merge_target_sha"] = target_sha
    merged = git_command_succeeds(
        ["merge-base", "--is-ancestor", "HEAD", target_ref],
        cwd=repository_root,
    )
    delivery["head_merged_to_target"] = merged
    if merged:
        return
    pr_status = pull_request_status_for_branch(repository_root, branch)
    status_text = format_pull_request_status(pr_status)
    detail = f";{status_text}" if status_text else ""
    raise SystemExit(
        "Cannot finalize task: the task branch HEAD is not merged into "
        f"`{target_ref}` yet{detail}. Keep the task in `review_approved`, "
        "refresh the PR branch if it is behind, and run `done` only after "
        "GitHub reports the PR merged."
    )


def parse_commit_metadata_lines(body: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            metadata[key] = value
    return metadata


def commit_subject_skips_trailer_check(subject: str) -> str | None:
    for prefix in COMMIT_TRAILER_SKIP_PREFIXES:
        if subject.startswith(prefix):
            return prefix.rstrip(": ")
    if COMMIT_TRAILER_SKIP_RE.match(subject):
        return "OPS"
    return None


LOOP_AUTOPILOT_NON_GOALS = {
    "No panel-only closure",
    "No seed fixture as live proof",
    "No approval gate bypass",
}

# Phrases in review notes or completion messages that signal a fixture/seed-only or
# panel-only closure.  Matches are case-insensitive substrings.
_FIXTURE_ONLY_SIGNALS = (
    "fixture only",
    "fixture-only",
    "fixture_only",
    "seed only",
    "seed-only",
    "seed_only",
    "seed fixture as proof",
    "seed fixture as live",
    "fixture as live proof",
    "panel only",
    "panel-only",
    "panel_only",
    "panel copy",
    "route only",
    "route-only",
    "route_only",
)


def is_loop_autopilot_task(task: dict[str, Any]) -> bool:
    """Return True when the task carries loop-autopilot guardrail requirements.

    A task qualifies when it has a non-empty ``loop_ids`` list, or when its
    ``non_goals`` list overlaps with the canonical LOOP_AUTOPILOT_NON_GOALS set.
    """
    if task.get("loop_ids"):
        return True
    non_goals: list[str] = task.get("non_goals") or []
    return bool(set(non_goals) & LOOP_AUTOPILOT_NON_GOALS)


def validate_loop_completion_claim(task: dict[str, Any]) -> None:
    """Gate the done transition for loop-autopilot tasks.

    Raises SystemExit with a descriptive message when the task's evidence
    fields are insufficient to support the done claim.
    """
    import loop_done_guardrail
    gaps = loop_done_guardrail.check_task(task)
    if gaps:
        task_id = task.get("id", "?")
        raise SystemExit(
            f"Loop task {task_id} done claim rejected:\n" +
            "\n".join(f"  ✗ {gap}" for gap in gaps)
        )


def validate_protected_closeout_transition(
    task: dict[str, Any],
    *,
    transition: str,
    consume: bool = False,
    transition_actor: str = "",
) -> dict[str, Any] | None:
    """Delegate protected Human/Ops verdict checks to the loop guardrail."""

    import loop_done_guardrail

    try:
        return loop_done_guardrail.validate_protected_closeout_transition(
            task,
            transition=transition,
            consume=consume,
            transition_actor=transition_actor,
        )
    except Exception as exc:
        task_id = task.get("id", "?")
        raise SystemExit(
            f"Protected Human/Ops verdict rejected for {task_id} "
            f"{transition} transition: {type(exc).__name__}: {exc}"
        ) from exc


def collect_done_delivery_metadata(task: dict[str, Any], actor: str) -> dict[str, Any]:
    settings = delivery_gate_settings()
    commit_rules = commit_convention_settings()
    config = load_config()
    repository_id = task_primary_repository_id(config, task)
    if repository_id is None:
        repo_ids = [repo_id for repo_id in task_artifact_repository_ids(config, task) if repo_id != "pantheon"]
        raise SystemExit(
            "Cannot finalize task: task artifacts span multiple non-Pantheon repositories; "
            f"split closeout or set a single artifact prefix. Repositories: {', '.join(repo_ids)}."
        )
    repository_root = repository_local_path(config, repository_id)
    if repository_root is None:
        raise SystemExit(f"Cannot finalize task: repository `{repository_id}` has no local_path configured.")
    repository_fallback: dict[str, Any] | None = None
    if repository_id != "pantheon" and not repository_root.exists():
        repo_ids = task_artifact_repository_ids(config, task)
        pantheon_root = repository_local_path(config, "pantheon")
        if "pantheon" in repo_ids and pantheon_root is not None:
            repository_fallback = {
                "from_repository_id": repository_id,
                "missing_repository_path": str(repository_root.resolve(strict=False)),
                "reason": (
                    "non-Pantheon artifact repository local_path is unavailable; "
                    "using Pantheon because the task also has Pantheon artifacts"
                ),
            }
            repository_id = "pantheon"
            repository_root = pantheon_root
    repository_root = repository_root.resolve(strict=False)
    repository_slug_value = repository_slug(config, repository_id)
    branch = run_git_command(
        ["rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repository_root,
        failure_message="Cannot finalize task: git branch information is unavailable.",
    )
    delivery: dict[str, Any] = {
        "recorded_at": iso_now(),
        "repository_id": repository_id,
        "repository_path": str(repository_root),
        "repository_slug": repository_slug_value,
        "branch": branch,
        "git_clean_required": settings["require_git_clean"],
    }
    command_metadata = status_command_metadata()
    if command_metadata:
        delivery["status_command_runtime"] = command_metadata
    if repository_fallback is not None:
        delivery["repository_fallback"] = repository_fallback

    if settings["require_commit_hash"]:
        commit_hash = run_git_command(
            ["rev-parse", "HEAD"],
            cwd=repository_root,
            failure_message="Cannot finalize task: a HEAD commit hash is required before moving to done.",
        )
        if not commit_hash:
            raise SystemExit("Cannot finalize task: a HEAD commit hash is required before moving to done.")
        delivery["commit"] = commit_hash
        subject = run_git_command(
            ["show", "-s", "--format=%s", "HEAD"],
            cwd=repository_root,
            failure_message="Cannot finalize task: latest commit subject is unavailable.",
        )
        body = run_git_command(
            ["show", "-s", "--format=%b", "HEAD"],
            cwd=repository_root,
            failure_message="Cannot finalize task: latest commit body is unavailable.",
        )
        author_name = run_git_command(
            ["show", "-s", "--format=%an", "HEAD"],
            cwd=repository_root,
            failure_message="Cannot finalize task: latest commit author name is unavailable.",
        )
        author_email = run_git_command(
            ["show", "-s", "--format=%ae", "HEAD"],
            cwd=repository_root,
            failure_message="Cannot finalize task: latest commit author email is unavailable.",
        )
        delivery["commit_subject"] = subject
        delivery["commit_author"] = {
            "name": author_name,
            "email": author_email,
        }

        task_id = str(task.get("id") or "").strip()
        if commit_rules["subject_must_include_task_id"] and task_id and task_id not in subject:
            raise SystemExit(
                f"Cannot finalize task: latest commit subject must include task id {task_id}."
            )

        metadata_fields = parse_commit_metadata_lines(body)
        expected_fields = {
            "LLM-Agent": actor,
            "Task-ID": task_id,
            "Reviewer": canonical_agent_name(task.get("reviewer")),
        }
        required_fields = commit_rules.get("required_body_fields", [])
        trailer_skip_reason = commit_subject_skips_trailer_check(subject)
        missing_fields: list[str] = []
        mismatched_fields: list[tuple[str, str]] = []
        if trailer_skip_reason is None:
            for field_name in required_fields:
                actual_value = metadata_fields.get(field_name)
                if not actual_value:
                    missing_fields.append(field_name)
                    continue
                expected_value = expected_fields.get(field_name)
                if expected_value and actual_value != expected_value:
                    if field_name == "LLM-Agent":
                        commit_timestamp = run_git_command(
                            ["show", "-s", "--format=%cI", "HEAD"],
                            cwd=repository_root,
                            failure_message=(
                                "Cannot finalize task: delivered commit timestamp is "
                                "unavailable for owner reassignment verification."
                            ),
                        )
                        delivery["commit_owner_reassignment"] = (
                            _verified_done_owner_reassignment(
                                task,
                                commit_owner=actual_value,
                                current_owner=actor,
                                commit_timestamp=commit_timestamp,
                            )
                        )
                        continue
                    mismatched_fields.append((field_name, expected_value))
        else:
            delivery["commit_trailer_check_skipped"] = True
            delivery["commit_trailer_skip_reason"] = trailer_skip_reason
        if missing_fields or mismatched_fields:
            issues: list[str] = []
            if missing_fields:
                missing_list = ", ".join(f"`{field_name}: ...`" for field_name in missing_fields)
                issues.append(f"latest commit body must include {missing_list}")
            if mismatched_fields:
                mismatch_list = ", ".join(
                    f"`{field_name}` must be `{expected_value}`"
                    for field_name, expected_value in mismatched_fields
                )
                issues.append(f"latest commit body fields must match task metadata: {mismatch_list}")
            raise SystemExit(f"Cannot finalize task: {'; '.join(issues)}.")
        delivery["commit_metadata"] = metadata_fields

    porcelain = run_git_command(
        ["status", "--porcelain"],
        cwd=repository_root,
        failure_message="Cannot finalize task: git status is unavailable.",
    )
    dirty_entries = [line for line in porcelain.splitlines() if line.strip()]
    delivery["git_clean"] = not dirty_entries
    delivery["dirty_entry_count"] = len(dirty_entries)

    if settings["require_git_clean"] and dirty_entries:
        raise SystemExit(
            "Cannot finalize task: git working tree is dirty while delivery_gates.require_git_clean is enabled."
        )

    remotes_output = run_git_command(
        ["remote"],
        cwd=repository_root,
        required=False,
    )
    remote_names = [line.strip() for line in remotes_output.splitlines() if line.strip()]
    delivery["remote_present"] = bool(remote_names)
    if remote_names:
        delivery["remote_names"] = remote_names

    if settings["record_remote_status"] and remote_names:
        upstream = run_git_command(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
            cwd=repository_root,
            required=False,
        )
        delivery["upstream"] = upstream or None
        if upstream:
            counts = run_git_command(
                ["rev-list", "--left-right", "--count", f"{upstream}...HEAD"],
                cwd=repository_root,
                failure_message="Cannot finalize task: unable to compute branch push status against upstream.",
            )
            try:
                behind_text, ahead_text = counts.split()
                behind = int(behind_text)
                ahead = int(ahead_text)
            except ValueError as exc:
                raise SystemExit("Cannot finalize task: malformed git push status output.") from exc
            delivery["ahead"] = ahead
            delivery["behind"] = behind
            delivery["push_status"] = classify_push_status(ahead, behind)
        else:
            delivery["push_status"] = "no_upstream"

    if settings["require_merged_pr"]:
        enforce_delivery_merged_gate(
            config,
            delivery,
            repository_root=repository_root,
            repository_id=repository_id,
            branch=branch,
            remote_names=remote_names,
        )

    return delivery


def task_metadata_from_env() -> dict[str, Any]:
    metadata = parse_json_env("TASK_METADATA_JSON")
    explicit_fields = {
        "task_class": os.environ.get("TASK_CLASS", "").strip() or None,
        "helper_parent": os.environ.get("TASK_HELPER_PARENT", "").strip() or None,
        "helper_kind": os.environ.get("TASK_HELPER_KIND", "").strip() or None,
        "auto_created_by": os.environ.get("TASK_AUTO_CREATED_BY", "").strip() or None,
    }
    for key, value in explicit_fields.items():
        if value is not None:
            metadata[key] = value

    for env_name, field_name in (
        ("TASK_AUTO_GENERATED", "auto_generated"),
        ("TASK_MUTATES_CANONICAL", "mutates_canonical"),
    ):
        parsed = parse_bool_env(env_name)
        if parsed is not None:
            metadata[field_name] = parsed

    return metadata


def dependency_is_satisfied(resolver: TaskResolver, dep_id: str) -> bool:
    return resolver.dependency_satisfied(dep_id)


def dependency_status_label(resolver: TaskResolver, dep_id: str) -> str:
    return resolver.dependency_status(dep_id)


def ensure_review_finalize_handoff(
    state: dict[str, Any],
    task: dict[str, Any],
    *,
    from_agent: str,
    timestamp: str,
    message: str | None = None,
) -> None:
    owner = canonical_agent_name(task.get("owner"))
    if not owner:
        return
    pending_owner_handoff = next(
        (
            handoff
            for handoff in state.get("handoffs", [])
            if handoff.get("task_id") == task.get("id")
            and handoff.get("to") == owner
            and handoff.get("status") != "done"
        ),
        None,
    )
    if pending_owner_handoff:
        if message:
            pending_owner_handoff["message"] = message
        return

    state.setdefault("handoffs", []).append(
        {
            "task_id": task.get("id"),
            "from": canonical_agent_name(from_agent),
            "to": owner,
            "message": message or "Review approved. Owner must finalize this task to move it from review_approved to done.",
            "status": "pending",
            "created_at": timestamp,
        }
    )


def validate_state(state: dict[str, Any]) -> None:
    sync_canonical_document_metadata(state)
    normalize_state_agents(state)
    for task in state["tasks"]:
        ensure_agent(task["owner"])
        ensure_agent(task["reviewer"])
        if task["owner"] == task["reviewer"]:
            raise SystemExit(f"Task {task['id']} has identical owner and reviewer")
        if task["status"] == "blocked" and not task.get("waiting_for"):
            raise SystemExit(f"Blocked task {task['id']} is missing waiting_for")

    for blocker in state.get("blockers", []):
        ensure_agent(blocker["owner"])
        ensure_agent(blocker["waiting_for"])

    for handoff in state.get("handoffs", []):
        ensure_agent(handoff["from"])
        ensure_agent(handoff["to"])


def normalize_state_agents(state: dict[str, Any]) -> None:
    for task in state.get("tasks", []):
        task["owner"] = active_agent_name(task.get("owner"))
        task["reviewer"] = active_agent_name(task.get("reviewer"))
        if task.get("waiting_for"):
            task["waiting_for"] = active_agent_name(task.get("waiting_for"))

    for blocker in state.get("blockers", []):
        blocker["owner"] = active_agent_name(blocker.get("owner"))
        blocker["waiting_for"] = active_agent_name(blocker.get("waiting_for"))

    for handoff in state.get("handoffs", []):
        handoff["from"] = active_agent_name(handoff.get("from"))
        handoff["to"] = active_agent_name(handoff.get("to"))

    for agent in state.get("agents", []):
        agent["name"] = active_agent_name(agent.get("name"))


def recompute_agents(state: dict[str, Any]) -> None:
    deduped_agents: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for agent in state.get("agents", []):
        name = agent.get("name")
        if not name or name in seen_names:
            continue
        deduped_agents.append(agent)
        seen_names.add(name)
    state["agents"] = deduped_agents

    by_owner: dict[str, list[dict[str, Any]]] = {name: [] for name in KNOWN_AGENTS}
    resolver = task_resolver(state)
    for task in state["tasks"]:
        by_owner.setdefault(task["owner"], []).append(task)

    for name in KNOWN_AGENTS:
        agent = get_agent(state, name)
        owned = by_owner.get(name, [])
        active = [task for task in owned if task["status"] in {"in_progress", "review", "blocked"}]
        approved = [task for task in owned if task["status"] == "review_approved"]
        queued = [task for task in owned if task["status"] == "todo"]
        ready = [
            task
            for task in queued
            if all(dependency_is_satisfied(resolver, dep_id) for dep_id in task.get("depends_on", []))
        ]
        waiting = [task for task in queued if task not in ready]

        if any(task["status"] == "blocked" for task in active):
            agent["status"] = "blocked"
            agent["current_task_ids"] = [task["id"] for task in active]
        elif any(task["status"] == "in_progress" for task in active):
            agent["status"] = "working"
            agent["current_task_ids"] = [task["id"] for task in active]
        elif any(task["status"] == "review" for task in active):
            agent["status"] = "reviewing"
            agent["current_task_ids"] = [task["id"] for task in active]
        elif approved:
            agent["status"] = "finalize"
            agent["current_task_ids"] = [task["id"] for task in approved]
        elif ready:
            agent["status"] = "ready"
            agent["current_task_ids"] = [task["id"] for task in ready]
        elif waiting:
            agent["status"] = "waiting"
            agent["current_task_ids"] = [task["id"] for task in waiting[:3]]
        else:
            agent["status"] = "idle"
            agent["current_task_ids"] = []

        if active:
            latest = sorted(
                active,
                key=lambda task: task.get("last_update") or "",
                reverse=True,
            )[0]
            agent["next"] = latest.get("next", "")
            agent["last_update"] = latest.get("last_update")
        elif approved:
            agent["next"] = approved[0].get("next", "")
            agent["last_update"] = approved[0].get("last_update")
        elif ready:
            agent["next"] = ready[0].get("next", "")
            agent["last_update"] = ready[0].get("last_update")
        elif waiting:
            agent["next"] = waiting[0].get("next", "")
            if not agent.get("last_update"):
                agent["last_update"] = waiting[0].get("last_update")
        elif queued:
            agent["next"] = queued[0].get("next", "")
        else:
            # Idle agents should not keep stale dispatch text from long-closed tasks.
            agent["next"] = ""
            if not agent.get("last_update"):
                agent["last_update"] = None


def recompute_workload(state: dict[str, Any]) -> None:
    summary: dict[str, dict[str, int]] = {}
    for name in KNOWN_AGENTS:
        summary[name] = {
            "total": 0,
            "active": 0,
            "blocked": 0,
            "done": 0,
            "review": 0,
            "review_approved": 0,
            "todo": 0,
        }

    for task in state["tasks"]:
        owner = task["owner"]
        bucket = summary[owner]
        bucket["total"] += 1
        bucket[task["status"] if task["status"] in bucket else "todo"] += 1
        if task["status"] in {"in_progress", "review", "blocked"}:
            bucket["active"] += 1

    state["workload"] = {name: KNOWN_AGENTS[name]["target_workload"] for name in KNOWN_AGENTS}
    state["workload_summary"] = summary


def task_delivery_layer(task: dict[str, Any]) -> str:
    explicit = str(task.get("delivery_layer") or "").strip().lower()
    if explicit in {"primary", "project"}:
        return "primary"
    if explicit in {"external", "upstream"}:
        return "external"
    task_id = str(task.get("id") or "")
    prefix = task_id.split("-", 1)[0]
    if prefix in EXTERNAL_TASK_PREFIXES:
        return "external"
    id_tokens = {token.strip().upper() for token in re.split(r"[-_/]+", task_id) if token.strip()}
    if id_tokens & EXTERNAL_TASK_ID_TOKENS:
        return "external"
    artifacts = [str(item) for item in task.get("artifacts", []) if str(item).strip()]
    if any(artifact.startswith(EXTERNAL_TASK_ARTIFACT_PREFIXES) for artifact in artifacts):
        return "external"
    text = " ".join(
        str(task.get(field) or "")
        for field in ("id", "title", "summary_zh", "phase")
    ).lower()
    if any(keyword in text for keyword in EXTERNAL_TASK_TEXT_KEYWORDS):
        return "external"
    return "primary"


def display_task_title(task: dict[str, Any]) -> str:
    title = str(task.get("title") or "")
    if task.get("task_class") != "sidecar":
        return title

    markers = ["[Sidecar]"]
    if task.get("auto_generated"):
        markers.append("[Auto]")
    if task.get("helper_parent"):
        markers.append(f"[Parent {task['helper_parent']}]")
    marker_text = " ".join(markers)
    if title:
        return f"{marker_text} {title}"
    return marker_text


def activity_log_message(entry: dict[str, Any]) -> str:
    message = entry.get("message")
    if message is not None and str(message).strip():
        return str(message)

    event_type = str(entry.get("type") or "event").strip() or "event"
    details: list[str] = []
    commit = str(entry.get("commit") or "").strip()
    if commit:
        details.append(f"commit {commit[:12]}")

    scope = entry.get("scope")
    if isinstance(scope, list) and scope:
        rendered_scope = ", ".join(f"`{str(item)}`" for item in scope[:3])
        if len(scope) > 3:
            rendered_scope += ", ..."
        details.append(f"scope {rendered_scope}")

    if details:
        return f"{event_type}: {'; '.join(details)}"
    return event_type


def write_current_work(state: dict[str, Any], logs: list[dict[str, Any]]) -> None:
    def cell(value: Any) -> str:
        text = "-" if value is None or value == "" else str(value)
        return text.replace("|", "\\|").replace("\n", "<br>")

    def append_layer_table(lines: list[str], tasks: list[dict[str, Any]]) -> None:
        lines.extend(
            [
                "| ID | Phase | Task | Owner | Status | Depends On | 中文說明 |",
                "|---|---|---|---|---|---|---|",
            ]
        )
        if not tasks:
            lines.append("| _(none)_ | - | - | - | - | - | - |")
            return
        for task in tasks:
            depends = ", ".join(f"`{item}`" for item in task.get("depends_on", [])) or "-"
            lines.append(
                "| `{id}` | {phase} | {title} | {owner} | {status} | {depends} | {summary} |".format(
                    id=cell(task.get("id")),
                    phase=cell(task.get("phase") or "Unassigned"),
                    title=cell(display_task_title(task)),
                    owner=cell(task.get("owner")),
                    status=cell(task.get("status")),
                    depends=cell(depends),
                    summary=cell(task.get("summary_zh") or "-"),
                )
            )

    current_logs = logs[-20:]
    canonical_files = canonical_file_set(state)
    tier_labels = canonical_tier_labels(state)
    planning_state = load_planning_state()
    orchestrator_state = load_json_file(ORCHESTRATOR_STATE_FILE, {})
    coordination_summary = build_coordination_summary(orchestrator_state)
    archive_index = load_archive_index()
    archive_counts = archive_index.get("counts", {}) if isinstance(archive_index.get("counts"), dict) else {}
    recent_terminal_tasks = recent_terminal_summaries(limit=task_archive_recent_limit())
    active_tasks = [task for task in state["tasks"] if task.get("status") != "done"]
    primary_tasks = [task for task in active_tasks if task_delivery_layer(task) == "primary"]
    external_tasks = [task for task in active_tasks if task_delivery_layer(task) == "external"]
    current_sprint_lines = [
        f"- Sprint: `{state['sprint']}`",
        "- Canonical files: " + ", ".join(f"`{item}`" for item in state["canonical_files"]),
        "- Canonical tiers: " + (", ".join(tier_labels) if tier_labels else "-"),
    ]
    planning_reference = planning_reference_files(planning_state)
    if planning_reference:
        current_sprint_lines.append(f"- Planning mode: `{planning_reference[0]}`")
    for path, label in OPTIONAL_CURRENT_WORK_REFERENCES:
        if path in canonical_files:
            current_sprint_lines.append(f"- {label}: `{path}`")
    current_sprint_lines.append("- Dashboard: `docs-site/index.html`")

    lines: list[str] = [
        "# Current Work",
        "",
        "This file is generated from `ai-status.json` and `ai-activity-log.jsonl`.",
        "Do not treat this file as the machine-readable source of truth.",
        f"Absolute times below use {DISPLAY_TIMEZONE_LABEL}.",
        "",
        f"Last updated: {format_display_timestamp(state['updated_at'])}",
        "",
        "## Objective",
        "",
        localize_embedded_timestamps(state["objective"]),
        "",
        "## Current Sprint",
        "",
        *current_sprint_lines,
        "",
    ]

    if planning_state:
        gate = planning_state.get("switch_gate", {})
        lines.extend(
            [
                "## Discussion Planning",
                "",
                f"- Session: `{planning_state.get('session_id', '-')}`",
                f"- Status: `{planning_state.get('status', '-')}`",
                f"- Baton owner: `{planning_state.get('baton_owner', '-')}`",
                f"- Current round: `{planning_state.get('current_round', 0)}`",
                f"- Consensus: `{planning_state.get('consensus_status', '-')}`",
                f"- Human gate: `{planning_state.get('human_gate_status', '-')}`",
                f"- Ready for human: `{gate.get('ready_for_human', False)}`",
                f"- Ready to materialize execution: `{gate.get('ready_to_materialize', False)}`",
                "",
            ]
        )
    lines.extend(
        [
        "## Active Slices",
        "",
        ]
    )

    for agent in state["agents"]:
        next_text = localize_embedded_timestamps(agent.get("next") or "No active assignment")
        lines.append(f"- `{agent['name']}`: {', '.join(agent['capability_lane'])}; next: {next_text}")

    lines.extend(
        [
            "",
            "## Delivery Layers",
            "",
            "### Primary Project Work",
            "",
        ]
    )
    append_layer_table(lines, primary_tasks)
    lines.extend(
        [
            "",
            "### External / Upstream Integration Work",
            "",
        ]
    )
    append_layer_table(lines, external_tasks)

    lines.extend(
        [
            "",
            "## Recently Executed Tasks",
            "",
            f"- Archive updated: {format_display_timestamp(archive_index.get('updated_at'))}",
            f"- Terminal tasks archived: `{int(archive_counts.get('total') or 0)}` total, `{int(archive_counts.get('completed') or 0)}` completed, `{int(archive_counts.get('superseded') or 0)}` superseded",
            "",
            "| ID | Phase | Task | Owner | Outcome | Archived At | Snapshot |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    if recent_terminal_tasks:
        for task in recent_terminal_tasks:
            lines.append(
                "| `{id}` | {phase} | {title} | {owner} | {outcome} | {archived_at} | `{snapshot}` |".format(
                    id=cell(task.get("task_id")),
                    phase=cell(task.get("phase")),
                    title=cell(task.get("title") or "-"),
                    owner=cell(task.get("owner")),
                    outcome=cell(task.get("terminal_outcome")),
                    archived_at=cell(format_display_timestamp(task.get("archived_at"))),
                    snapshot=cell(task.get("snapshot_path") or "-"),
                )
            )
    else:
        lines.append("| _(none)_ | - | - | - | - | - | - |")

    lines.extend(["", "## Task Board", "", "| ID | Phase | Task | 中文說明 | Owner | Reviewer | Status | Depends On | Last Update | Next |", "|---|---|---|---|---|---|---|---|---|---|"])

    for task in state["tasks"]:
        depends = ", ".join(f"`{item}`" for item in task.get("depends_on", [])) or "-"
        lines.append(
            "| `{id}` | {phase} | {title} | {summary} | {owner} | {reviewer} | {status} | {depends} | {last_update} | {next} |".format(
                id=cell(task.get("id")),
                phase=cell(task.get("phase") or "Unassigned"),
                title=cell(display_task_title(task)),
                summary=cell(task.get("summary_zh") or "-"),
                owner=cell(task.get("owner")),
                reviewer=cell(task.get("reviewer")),
                status=cell(task.get("status")),
                depends=cell(depends),
                last_update=cell(format_display_timestamp(task.get("last_update"))),
                next=cell(localize_embedded_timestamps(task.get("next") or "-")),
            )
        )

    lines.extend(["", "## Handoff Queue", "", "| Task | From | To | Message | Status | Created At |", "|---|---|---|---|---|---|"])
    pending_handoffs = [handoff for handoff in state.get("handoffs", []) if handoff.get("status") != "done"]
    if pending_handoffs:
        for handoff in pending_handoffs:
            lines.append(
                f"| `{handoff['task_id']}` | {handoff['from']} | {handoff['to']} | {cell(localize_embedded_timestamps(handoff['message']))} | {handoff['status']} | {cell(format_display_timestamp(handoff['created_at']))} |"
            )
    else:
        lines.append("| _(none)_ | - | - | - | - | - |")

    lines.extend(["", "## Blockers", "", "| Task | Owner | Waiting For | Message | Status |", "|---|---|---|---|---|"])
    open_blockers = [blocker for blocker in state.get("blockers", []) if blocker.get("status") == "open"]
    if open_blockers:
        for blocker in open_blockers:
            lines.append(
                f"| `{blocker['task_id']}` | {blocker['owner']} | {blocker['waiting_for']} | {blocker['message']} | {blocker['status']} |"
            )
    else:
        lines.append("| _(none)_ | - | - | - | - |")

    lines.extend(["", "## Review Notes", "", "| Task | Reviewer | 修正重點 | Review File |", "|---|---|---|---|"])
    review_tasks = [task for task in state["tasks"] if task.get("review_notes_zh")]
    if review_tasks:
        for task in review_tasks:
            note_html = "<br>".join(localize_embedded_timestamps(note) for note in task.get("review_notes_zh", []))
            lines.append(
                f"| `{task['id']}` | {cell(task['reviewer'])} | {cell(note_html)} | {cell(task.get('review_file') or '-')} |"
            )
    else:
        lines.append("| _(none)_ | - | - | - |")

    coordination_counts = coordination_summary.get("counts", {}) if isinstance(coordination_summary.get("counts"), dict) else {}
    lines.extend(
        [
            "",
            "## Lovable Coordination",
            "",
            f"- Last coordination scan: {format_display_timestamp(coordination_summary.get('last_scan_at'))}",
            f"- Tracked features: `{coordination_counts.get('tracked_features', 0)}`",
            f"- Lovable-ready packets: `{coordination_counts.get('lovable_ready', 0)}`",
            f"- Waiting for Lovable/front-end: `{coordination_counts.get('waiting_for_lovable', 0)}`",
            f"- UI-done returned: `{coordination_counts.get('ui_done_received', 0)}`",
            f"- Frontend feedback returned: `{coordination_counts.get('frontend_feedback_received', 0)}`",
            f"- Open BFF gaps: `{coordination_counts.get('open_bff_gaps', 0)}`",
            f"- Backend route live: `{coordination_counts.get('backend_route_live', 0)}`",
            f"- Pantheon handoff published: `{coordination_counts.get('pantheon_handoff_published', 0)}`",
            f"- Mirrored to front default branch: `{coordination_counts.get('mirrored_to_front_default_branch', 0)}`",
            f"- Dispatch recorded in coordinator state: `{coordination_counts.get('dispatch_emitted', 0)}`",
            f"- Receiver-visible payload on front default branch: `{coordination_counts.get('front_receiver_applied', 0)}`",
            f"- Lovable consumed packet: `{coordination_counts.get('lovable_consumed', 0)}`",
            f"- UI activated: `{coordination_counts.get('ui_activated', 0)}`",
            f"- Runtime verified: `{coordination_counts.get('runtime_verified', 0)}`",
            "",
            "| Feature | Screen | Stage | Lovable Ready | Mirrored | UI Done | Feedback | Next Action |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    coordination_features = coordination_summary.get("features") if isinstance(coordination_summary.get("features"), list) else []
    if coordination_features:
        for feature in coordination_features:
            lines.append(
                "| `{feature_id}` | {screen} | `{stage}` | {lovable_ready} | {mirrored} | {ui_done} | {feedback} | {next_action} |".format(
                    feature_id=cell(feature.get("feature_id") or "-"),
                    screen=cell(feature.get("screen") or "-"),
                    stage=cell(feature.get("stage") or "-"),
                    lovable_ready="yes" if feature.get("lovable_ready") else "no",
                    mirrored="yes" if feature.get("mirrored_to_target_repo") else "no",
                    ui_done="yes" if feature.get("has_ui_done") else "no",
                    feedback="yes" if feature.get("has_frontend_feedback") else "no",
                    next_action=cell(localize_embedded_timestamps(feature.get("next_action") or "-")),
                )
            )
    else:
        lines.append("| _(none)_ | - | - | - | - | - | - | - |")

    route_live_activation_archive = archived_route_live_activation_modules()
    route_live_activation_outside_feature_rows = modules_outside_coordination_feature_rows(
        route_live_activation_archive,
        coordination_features,
    )
    if route_live_activation_outside_feature_rows:
        lines.extend(
            [
                "",
                "Tracked-feature note: the table above only lists modules that currently have coordination feature records.",
                "Archive-done route-live activation publication lanes that remain outside explicit feature rows: "
                + ", ".join(f"`{module}`" for module in route_live_activation_outside_feature_rows) + ".",
                "Do not read those omitted modules as open Pantheon backlog purely because they are absent from the coordination feature table.",
            ]
        )

    lines.extend(["", "## Latest Checkpoints", ""])
    if current_logs:
        for entry in current_logs:
            task_id = f" `{entry['task_id']}`" if entry.get("task_id") else ""
            timestamp = entry.get("ts") or entry.get("timestamp")
            lines.append(
                f"- {format_display_timestamp(timestamp)} {entry.get('agent') or 'Unknown'}:{task_id} "
                f"{localize_embedded_timestamps(activity_log_message(entry))}"
            )
    else:
        lines.append("- No checkpoints yet.")

    CURRENT_WORK_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def archived_route_live_activation_modules() -> list[str]:
    task_modules = {
        "APP-003-ROUTE-LIVE-FRONTEND-001": ["CW-02", "KW-04", "KW-05"],
        "APP-003-ROUTE-LIVE-FRONTEND-002": ["RW-02", "RW-04", "RW-05", "KW-02", "KW-03", "TW-01", "TW-02", "TW-04"],
    }
    modules: list[str] = []
    for task_id, task_module_list in task_modules.items():
        snapshot = archived_task_snapshot(task_id)
        if snapshot is None:
            continue
        task = snapshot.get("task") if isinstance(snapshot, dict) else None
        if not isinstance(task, dict):
            continue
        if str(task.get("status") or "").lower() != "done":
            continue
        for module in task_module_list:
            if module not in modules:
                modules.append(module)
    return modules


def feature_module_identifier(feature_id: Any) -> str | None:
    candidate = str(feature_id or "").strip()
    if not candidate:
        return None
    match = FEATURE_MODULE_RE.match(candidate)
    if match:
        return match.group(1)
    return None


def modules_outside_coordination_feature_rows(
    modules: list[str],
    coordination_features: list[dict[str, Any]],
) -> list[str]:
    tracked_modules: set[str] = set()
    for feature in coordination_features:
        if not isinstance(feature, dict):
            continue
        module = feature_module_identifier(feature.get("feature_id"))
        if module:
            tracked_modules.add(module)

    return [module for module in modules if module not in tracked_modules]


def normalize_worker_actor(worker: dict[str, Any]) -> str:
    for candidate in (worker.get("logical_agent_id"), worker.get("agent_id"), worker.get("target_agent"), worker.get("provider")):
        normalized = str(candidate or "").strip().lower().replace("-", "_")
        if re.match(r"^codex1_[1-4]$", normalized):
            return "Codex"
        if re.match(r"^codex2_[1-4]$", normalized):
            return "Codex2"
        canonical = canonical_agent_name(candidate)
        if canonical:
            return canonical
        lowered = str(candidate or "").strip().lower()
        if lowered in {"grok", "copilot"}:
            return "Copilot"
    return str(worker.get("agent_id") or worker.get("provider") or "").strip()


def runtime_dispatch_mode(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return "execution"
    explicit = str(payload.get("dispatch_mode") or "").strip()
    if explicit:
        return explicit
    request_snapshot = payload.get("request_snapshot") if isinstance(payload.get("request_snapshot"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else request_snapshot.get("metadata", {})
    if isinstance(metadata.get("planning"), dict) and metadata.get("planning"):
        return str(metadata["planning"].get("mode") or "discussion_planning")
    if isinstance(metadata.get("chair"), dict) and metadata.get("chair"):
        return "chair_review"
    if isinstance(metadata.get("coordination"), dict) and metadata.get("coordination"):
        return "coordination"
    reason = str(payload.get("reason") or request_snapshot.get("reason") or "").strip()
    if reason.startswith("discussion_planning_"):
        return "discussion_planning"
    if reason.startswith("chair_review:"):
        return "chair_review"
    if reason.startswith("coordination:"):
        return "coordination"
    return "execution"


def expected_task_actor(task: dict[str, Any]) -> str:
    if str(task.get("status") or "").lower() == "review":
        return canonical_agent_name(task.get("reviewer"))
    return canonical_agent_name(task.get("owner"))


def pid_is_alive(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    state = proc_pid_state(value)
    if not state:
        return False
    return state.upper() not in {"Z", "X"}


def proc_pid_state(pid: Any) -> str | None:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    try:
        stat = Path(f"/proc/{value}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return stat.rsplit(")", 1)[1].strip().split()[0]
    except IndexError:
        return None


def worker_has_live_runtime(worker: dict[str, Any], *, pid_alive: bool | None = None) -> bool:
    status = str(worker.get("status") or "").strip().lower()
    pid = worker.get("pid")
    has_pid = pid not in {None, "", 0, "0"}
    if pid_alive is None and has_pid:
        pid_alive = pid_is_alive(pid)

    if status in {"running", "started", "waiting_approval"}:
        if has_pid:
            return bool(pid_alive)
        return True
    if status in {"suspended_approval", "manual_pending", "retry_backoff", "fallback", "stalled"}:
        if has_pid:
            return bool(pid_alive)
        return False
    return False


def normalize_runtime_workers(state: dict[str, Any], orchestrator_state: dict[str, Any]) -> list[dict[str, Any]]:
    resolver = task_resolver(state)
    rows: list[dict[str, Any]] = []
    for run_id, worker in (orchestrator_state.get("workers", {}) or {}).items():
        task_id = str(worker.get("task_id") or "").strip()
        task = resolver.get(task_id) if task_id else None
        request_snapshot = worker.get("request_snapshot") if isinstance(worker.get("request_snapshot"), dict) else {}
        request_metadata = request_snapshot.get("metadata") if isinstance(request_snapshot.get("metadata"), dict) else {}
        handoff = request_metadata.get("handoff") if isinstance(request_metadata.get("handoff"), dict) else None
        task_status = str(task.get("status") or "") if task else None
        task_source = resolver.source(task_id) if task_id else None
        worker_status = str(worker.get("status") or "")
        reason = worker.get("reason") or request_snapshot.get("reason")
        if task is None and str(reason or "") == "handoff_pending" and handoff:
            task_status = str(handoff.get("status") or "pending")
            task_source = "handoff"
        pid = worker.get("pid")
        pid_state = proc_pid_state(pid) if pid not in {None, "", 0, "0"} else None
        pid_alive = bool(pid_state and pid_state.upper() not in {"Z", "X"}) if pid_state is not None else None
        live_runtime = worker_has_live_runtime(worker, pid_alive=pid_alive)
        if worker_status in {"superseded", "reassigned"}:
            bucket = "transition"
        elif task_status == "done" or worker_status in {"completed", "failed"}:
            bucket = "completed"
        elif not live_runtime and worker_status in {"running", "started"} and pid not in {None, "", 0, "0"}:
            bucket = "stale"
        elif live_runtime and worker_status in {"running", "started"}:
            bucket = "running"
        else:
            bucket = "pending"
        rows.append(
            {
                "run_id": run_id,
                "task_id": worker.get("task_id"),
                "queue_event_id": worker.get("queue_event_id"),
                "actor": normalize_worker_actor(worker),
                "provider": worker.get("provider"),
                "logical_agent_id": worker.get("logical_agent_id"),
                "dispatch_slot": worker.get("dispatch_slot"),
                "dispatch_slot_id": worker.get("dispatch_slot_id"),
                "quota_group": worker.get("quota_group"),
                "status": worker_status,
                "bucket": bucket,
                "task_status": task_status,
                "task_source": task_source,
                "reason": reason,
                "handoff": handoff,
                "delivery_mode": worker.get("mode"),
                "dispatch_mode": runtime_dispatch_mode(worker),
                "last_event_at": worker.get("last_event_at"),
                "started_at": worker.get("started_at"),
                "last_error": worker.get("last_error"),
                "pid": pid,
                "pid_alive": pid_alive,
                "pid_state": pid_state,
                "is_live_runtime": live_runtime,
            }
        )
    rows.sort(key=lambda item: str(item.get("last_event_at") or ""), reverse=True)
    return rows


def normalize_runtime_queue(orchestrator_state: dict[str, Any]) -> list[dict[str, Any]]:
    queue_records = ((orchestrator_state.get("queue") or {}).get("events") or {})
    workers_by_event: dict[str, dict[str, Any]] = {}
    for run_id, worker in (orchestrator_state.get("workers", {}) or {}).items():
        queue_event_id = worker.get("queue_event_id")
        if queue_event_id:
            workers_by_event[str(queue_event_id)] = {"run_id": run_id, **worker}
    rows: list[dict[str, Any]] = []
    for event_id, event in queue_records.items():
        linked_worker = workers_by_event.get(str(event_id), {})
        rows.append(
            {
                "id": event_id,
                "task_id": event.get("task_id") or linked_worker.get("task_id"),
                "status": event.get("status"),
                "agent": canonical_agent_name(event.get("target_display_name") or event.get("target_agent") or linked_worker.get("agent_id")),
                "provider": event.get("provider") or linked_worker.get("provider"),
                "reason": event.get("reason") or linked_worker.get("reason") or (linked_worker.get("request_snapshot") or {}).get("reason"),
                "dispatch_mode": runtime_dispatch_mode(event or linked_worker),
                "run_id": event.get("run_id") or linked_worker.get("run_id"),
                "last_event_at": event.get("last_event_at") or event.get("processed_at") or event.get("last_attempt_at") or linked_worker.get("last_event_at"),
            }
        )
    rows.sort(key=lambda item: str(item.get("last_event_at") or ""), reverse=True)
    return rows


def mismatch_resolution_hint(item: dict[str, Any]) -> str:
    mismatch_type = str(item.get("type") or "")
    if mismatch_type == "delivery_merged_needs_closeout":
        return (
            "先用 merged-dev evidence 補正式 closeout/review 檔，"
            "再走 governed done 或 reconcile_merged_done；不要重新開工或重派已 merged 的 PR。"
        )
    if mismatch_type == "delivery_binding_stale":
        return (
            "先把 task 的 source_ref/review binding 對齊實際 reviewed/merged exact head；"
            "舊 head_sha 留在 active board 會讓 dashboard 和 supervisor 誤判。"
        )
    if mismatch_type == "github_review_gate_missing":
        return (
            "以 assigned reviewer 對 exact PR head 重新執行 governed approve；"
            "GitHub review 或 branch-policy-required canonical status 成功寫入前，"
            "不得把 internal review_approved 當成 PR completion。"
        )
    if mismatch_type == "worker_without_task":
        return "先檢查 dispatch/request snapshot 是否漏掉 task_id；如果是舊 worker，應重派成帶 task_id 的新 run。"
    if mismatch_type == "worker_task_missing":
        return "先確認 task 是否被移除或改名；若 task 已失效，應停掉 worker，否則重建對應 task。"
    if mismatch_type == "worker_assignment_mismatch":
        return "先對齊 owner/reviewer 與 runtime actor；若已改派，先把 task board assignment 寫回，再重新 dispatch。"
    if mismatch_type == "running_worker_on_todo":
        return "先把 task 狀態推成 in_progress；若 worker 是誤派，則回退 queue 或直接停掉該 run。"
    if mismatch_type == "running_worker_on_done":
        return "先確認這是不是殘留 worker；若 task 已確定 done，應停掉 worker 並清理 queue record。"
    if mismatch_type == "active_task_without_worker":
        return "要嘛重新 dispatch expected actor，要嘛把 task 狀態降回 todo/blocking truth，避免假 active。"
    if mismatch_type == "queue_started_without_worker":
        return "先檢查 queue record 是否卡在 started；如果 worker 已消失，重設 queue 或重新 dispatch。"
    if mismatch_type == "approval_missing_task":
        return "先清掉 stale approval，或先恢復 task board 中的 task，再進行批准。"
    return "先對齊 task board、queue、runtime 三者的真相，再決定是重派、回退，還是清理殘留記錄。"


MERGED_DELIVERY_RE = re.compile(
    r"\b(?:PR\s*#?\d+[^.\n;]*?)?\bmerged\s+(?:to|into)\s+(?:origin/)?(?P<target>dev|main)\s+as\s+(?P<sha>[0-9a-fA-F]{40})\b",
    re.IGNORECASE,
)
EXACT_HEAD_RE = re.compile(
    r"\b(?:exact[- ]head|exact\s+head|head)\s+(?P<sha>[0-9a-fA-F]{40})\b",
    re.IGNORECASE,
)


def task_status_is_nonterminal(task: Mapping[str, Any]) -> bool:
    return str(task.get("status") or "").strip().lower() not in {"done", "superseded"}


def _task_text_fields(task: Mapping[str, Any]) -> str:
    values: list[str] = []
    for key in ("next", "summary_zh", "title", "phase"):
        value = task.get(key)
        if isinstance(value, str):
            values.append(value)
    for key in ("review_notes_zh", "acceptance"):
        value = task.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value if str(item).strip())
    return "\n".join(values)


def merged_delivery_evidence(task: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return local evidence that a PR-backed delivery merged but is not closed.

    This intentionally avoids GitHub API calls so dashboard generation remains
    deterministic and CI-safe.  It recognizes structured status metadata first,
    then the existing Human/Ops closeout notes used by live fleet tasks.
    """

    delivery = task.get("delivery")
    if isinstance(delivery, Mapping):
        if delivery.get("head_merged_to_target") is True or str(delivery.get("state") or "").upper() == "MERGED":
            commit = str(delivery.get("merge_target_sha") or delivery.get("merge_commit") or delivery.get("commit") or "").strip()
            return {
                "source": "delivery",
                "merge_commit": commit or None,
                "merge_target": str(delivery.get("merge_target_branch") or delivery.get("merge_target_ref") or "").strip() or None,
            }

    for key in ("source_ref", "github", APPROVAL_BINDING_KEY, GITHUB_REVIEW_BRIDGE_KEY):
        payload = task.get(key)
        if not isinstance(payload, Mapping):
            continue
        state = str(payload.get("state") or payload.get("status") or "").strip().upper()
        merged = payload.get("merged") is True or state == "MERGED" or bool(payload.get("merged_at"))
        commit = str(
            payload.get("merge_commit")
            or payload.get("merge_commit_sha")
            or payload.get("merged_commit")
            or payload.get("merged_to_dev_sha")
            or ""
        ).strip()
        if merged or commit:
            return {
                "source": key,
                "merge_commit": commit or None,
                "merge_target": str(payload.get("base") or payload.get("target") or payload.get("merge_target") or "").strip() or None,
            }

    text = _task_text_fields(task)
    match = MERGED_DELIVERY_RE.search(text)
    if match:
        return {
            "source": "task_text",
            "merge_commit": match.group("sha").lower(),
            "merge_target": match.group("target").lower(),
        }
    return None


def delivery_binding_stale_evidence(task: Mapping[str, Any]) -> dict[str, Any] | None:
    source_ref = task.get("source_ref")
    if not isinstance(source_ref, Mapping):
        return None
    recorded = str(source_ref.get("head_sha") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", recorded):
        return None

    candidates: list[tuple[str, str]] = []
    for key in (APPROVAL_BINDING_KEY, GITHUB_REVIEW_BRIDGE_KEY, "github"):
        payload = task.get(key)
        if not isinstance(payload, Mapping):
            continue
        head = str(payload.get("head_sha") or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{40}", head):
            candidates.append((key, head))

    text = _task_text_fields(task)
    for match in EXACT_HEAD_RE.finditer(text):
        candidates.append(("task_text_exact_head", match.group("sha").lower()))

    for source, candidate in candidates:
        if candidate != recorded:
            return {
                "source": source,
                "recorded_head_sha": recorded,
                "evidence_head_sha": candidate,
            }
    return None


def detect_truth_mismatches(
    state: dict[str, Any],
    workers: list[dict[str, Any]],
    queue_events: list[dict[str, Any]],
    approval_state: dict[str, Any],
    resolver: TaskResolver,
    orchestrator_state: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    task_map = {task["id"]: task for task in state.get("tasks", [])}
    orchestrator = orchestrator_state or {}
    provider_guardrails = orchestrator.get("provider_guardrails") if isinstance(orchestrator.get("provider_guardrails"), dict) else {}
    dispatch_pauses = (
        provider_guardrails.get("dispatch_pauses")
        if isinstance(provider_guardrails.get("dispatch_pauses"), dict)
        else orchestrator.get("dispatch_pauses")
        if isinstance(orchestrator.get("dispatch_pauses"), dict)
        else {}
    )
    live_workers = [
        worker
        for worker in workers
        if worker.get("bucket") in {"running", "pending"} and worker.get("is_live_runtime")
    ]
    live_workers_by_task: dict[str, list[dict[str, Any]]] = {}
    mismatches: list[dict[str, Any]] = []
    seen: set[str] = set()
    pending_approval_run_ids = {
        str(approval.get("worker_run_id") or "").strip()
        for approval in (approval_state.get("pending") or [])
        if str(approval.get("worker_run_id") or "").strip()
    }
    pending_approval_task_ids = {
        str(approval.get("task_id") or "").strip()
        for approval in (approval_state.get("pending") or [])
        if str(approval.get("task_id") or "").strip()
    }
    approval_worker_modes = {
        str(worker.get("run_id") or "").strip(): str(worker.get("dispatch_mode") or "").strip()
        for worker in workers
        if str(worker.get("run_id") or "").strip()
    }

    def related_live_worker_covers_task(task: dict[str, Any]) -> bool:
        expected_actor = expected_task_actor(task)
        if not expected_actor:
            return False
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            return False

        parent_id = str(task.get("helper_parent") or "").strip()
        if parent_id:
            for worker in live_workers_by_task.get(parent_id, []):
                if canonical_agent_name(worker.get("actor") or worker.get("agent_id")) == expected_actor:
                    return True

        for related_id, related_task in task_map.items():
            if str(related_task.get("helper_parent") or "").strip() != task_id:
                continue
            for worker in live_workers_by_task.get(related_id, []):
                if canonical_agent_name(worker.get("actor") or worker.get("agent_id")) == expected_actor:
                    return True

        return False

    def push(payload: dict[str, Any]) -> None:
        key = str(payload.get("id") or f"{payload.get('type')}:{payload.get('task_id')}:{payload.get('worker_run_id')}:{payload.get('queue_event_id')}")
        if key in seen:
            return
        payload.setdefault("resolution_hint", mismatch_resolution_hint(payload))
        seen.add(key)
        mismatches.append(payload)

    for worker in live_workers:
        task_id = str(worker.get("task_id") or "").strip()
        if task_id:
            live_workers_by_task.setdefault(task_id, []).append(worker)
        else:
            if str(worker.get("dispatch_mode") or "").strip() == "chair_review":
                continue
            push(
                {
                    "id": f"worker-without-task:{worker.get('run_id')}",
                    "type": "worker_without_task",
                    "severity": "medium",
                    "title": "Live worker 沒有綁到 task",
                    "summary": f"{worker.get('actor') or '-'} 的 worker 已在跑，但沒有 task_id。",
                    "worker_run_id": worker.get("run_id"),
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )
            continue

        task = task_map.get(task_id)
        if task is None:
            if str(worker.get("dispatch_mode") or "").strip() in {"discussion_planning", "coordination", "chair_review"}:
                continue
            if resolver.source(task_id) == "archive":
                continue
            if worker.get("task_source") == "handoff":
                continue
            push(
                {
                    "id": f"worker-task-missing:{worker.get('run_id')}",
                    "type": "worker_task_missing",
                    "severity": "high",
                    "title": "Live worker 指向不存在的 task",
                    "summary": f"{worker.get('actor') or '-'} 的 worker 綁到 {task_id}，但 task board 找不到這個 task。",
                    "task_id": task_id,
                    "worker_run_id": worker.get("run_id"),
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )
            continue

        task_status = str(task.get("status") or "").lower()
        expected_actor = expected_task_actor(task)
        actual_actor = canonical_agent_name(worker.get("actor") or worker.get("agent_id"))
        if expected_actor and actual_actor and expected_actor != actual_actor:
            push(
                {
                    "id": f"worker-assignment:{worker.get('run_id')}",
                    "type": "worker_assignment_mismatch",
                    "severity": "medium" if task_status == "review" else "high",
                    "title": "Live worker 與 task 指派對不上",
                    "summary": f"{task_id} 目前應由 {expected_actor} 接手，但 live worker 來自 {actual_actor}。",
                    "task_id": task_id,
                    "worker_run_id": worker.get("run_id"),
                    "expected_actor": expected_actor,
                    "actual_actor": actual_actor,
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )

        if worker.get("bucket") == "running" and task_status == "todo":
            push(
                {
                    "id": f"running-worker-on-todo:{worker.get('run_id')}",
                    "type": "running_worker_on_todo",
                    "severity": "medium",
                    "title": "Worker 已在跑，但 task 還是 todo",
                    "summary": f"{task_id} 有 live running worker，但 task status 仍是 todo。",
                    "task_id": task_id,
                    "worker_run_id": worker.get("run_id"),
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )

        if worker.get("bucket") == "running" and task_status == "done":
            push(
                {
                    "id": f"running-worker-on-done:{worker.get('run_id')}",
                    "type": "running_worker_on_done",
                    "severity": "high",
                    "title": "Task 已完成，但 worker 仍在跑",
                    "summary": f"{task_id} 已是 done，但還有 live running worker。",
                    "task_id": task_id,
                    "worker_run_id": worker.get("run_id"),
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )

    for task in state.get("tasks", []):
        task_status = str(task.get("status") or "").lower()
        if task_status_is_nonterminal(task):
            merged_evidence = merged_delivery_evidence(task)
            if merged_evidence is not None:
                push(
                    {
                        "id": f"delivery-merged-needs-closeout:{task['id']}",
                        "type": "delivery_merged_needs_closeout",
                        "severity": "high",
                        "title": "Delivery PR 已 merged，但 task 尚未 closeout",
                        "summary": (
                            f"{task['id']} 已有 merged-dev delivery evidence，"
                            f"但 task status 仍是 {task_status or 'unknown'}。"
                        ),
                        "task_id": task["id"],
                        "delivery_evidence": merged_evidence,
                        "detected_at": task.get("last_update"),
                    }
                )
            stale_evidence = delivery_binding_stale_evidence(task)
            if stale_evidence is not None:
                push(
                    {
                        "id": f"delivery-binding-stale:{task['id']}",
                        "type": "delivery_binding_stale",
                        "severity": "high",
                        "title": "Task delivery binding 指向舊 exact head",
                        "summary": (
                            f"{task['id']} 的 source_ref.head_sha 與後續 "
                            "review/merge evidence 不一致。"
                        ),
                        "task_id": task["id"],
                        "delivery_evidence": stale_evidence,
                        "detected_at": task.get("last_update"),
                    }
                )
        if (
            task_status == "review_approved"
            and (
                isinstance(task.get(APPROVAL_BINDING_KEY), Mapping)
                or task_has_pr_review_target(task)
            )
            and not github_review_bridge_evidence_matches(task)
        ):
            push(
                {
                    "id": f"github-review-gate-missing:{task['id']}",
                    "type": "github_review_gate_missing",
                    "severity": "high",
                    "title": "Internal approval 尚未綁定 GitHub review gate",
                    "summary": (
                        f"{task['id']} 有 exact-head review binding 且狀態為 "
                        "review_approved，但沒有對應的 GitHub review 或 "
                        "branch-policy-recognized canonical status evidence。"
                    ),
                    "task_id": task["id"],
                    "detected_at": task.get("last_update"),
                }
            )
        if task_status != "in_progress":
            continue
        expected_actor = expected_task_actor(task)
        if str(task.get("id") or "").strip() in pending_approval_task_ids:
            continue
        if live_workers_by_task.get(task["id"]):
            continue
        if related_live_worker_covers_task(task):
            continue
        push(
            {
                "id": f"active-task-without-worker:{task['id']}",
                "type": "active_task_without_worker",
                "severity": "medium",
                "title": "Active task 沒有 live worker",
                "summary": f"{task['id']} 在 task board 上是 {task_status}，但目前沒有對應的 live worker。",
                "task_id": task["id"],
                "expected_actor": expected_actor,
                "detected_at": task.get("last_update"),
            }
        )

    live_queue_ids = {str(worker.get("queue_event_id") or "") for worker in live_workers if worker.get("queue_event_id")}
    for event in queue_events:
        event_status = str(event.get("status") or "").lower()
        if event_status not in {"started", "manual_pending"}:
            continue
        if (
            str(event.get("run_id") or "").strip() in pending_approval_run_ids
            or str(event.get("task_id") or "").strip() in pending_approval_task_ids
        ):
            continue
        if str(event.get("id") or "") in live_queue_ids:
            continue
        push(
            {
                "id": f"queue-without-worker:{event.get('id')}",
                "type": "queue_started_without_worker",
                "severity": "medium",
                "title": "Queue record 已啟動，但找不到 live worker",
                "summary": f"{event.get('task_id') or event.get('id')} 的 queue record 已是 {event_status}，但 runtime 沒有對應 worker。",
                "task_id": event.get("task_id"),
                "queue_event_id": event.get("id"),
                "detected_at": event.get("last_event_at"),
            }
        )

    for approval in (approval_state.get("pending") or []):
        task_id = str(approval.get("task_id") or "").strip()
        worker_run_id = str(approval.get("worker_run_id") or "").strip()
        if approval_worker_modes.get(worker_run_id) in {"discussion_planning", "coordination"}:
            continue
        if not task_id or task_id in task_map or resolver.source(task_id) == "archive":
            continue
        push(
            {
                "id": f"approval-missing-task:{approval.get('id') or approval.get('approval_id') or task_id}",
                "type": "approval_missing_task",
                "severity": "medium",
                "title": "Approval queue 指向不存在的 task",
                "summary": f"待批准項目 {task_id} 已不在 task board 中。",
                "task_id": task_id,
                "detected_at": approval.get("created_at"),
            }
        )

    severity_order = {"high": 0, "medium": 1, "low": 2}
    mismatches.sort(
        key=lambda item: (
            severity_order.get(str(item.get("severity") or "medium"), 9),
            str(item.get("detected_at") or ""),
        )
    )
    return live_workers, mismatches


def normalized_source_ref(task: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    payload = task.get("source_ref")
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        normalized[str(key)] = text
    return normalized


def build_bridge_summary(state: dict[str, Any], planning: dict[str, Any]) -> dict[str, Any]:
    resolver = task_resolver(state)
    task_map = resolver.active_task_map()
    proposed = planning.get("proposed_execution_tasks") or []
    contract = planning.get("materialization_contract") if isinstance(planning.get("materialization_contract"), dict) else {}
    artifacts = planning.get("artifacts") if isinstance(planning.get("artifacts"), dict) else {}
    consensus_packet = str(contract.get("consensus_packet") or ((artifacts.get("consensus_packet") or {}).get("path")) or "").strip()
    execution_materialization = str(
        contract.get("execution_materialization")
        or ((artifacts.get("execution_materialization") or {}).get("path"))
        or ""
    ).strip()
    session_id = str(contract.get("session_id") or planning.get("session_id") or "").strip()
    planning_backed_tasks = [
        task
        for task in state.get("tasks", [])
        if str(task.get("source_plane") or "").strip().lower() == "planning"
    ]

    status_counts = {
        "done": 0,
        "review_approved": 0,
        "in_progress": 0,
        "review": 0,
        "todo": 0,
        "blocked": 0,
    }
    pending_proposals: list[dict[str, Any]] = []
    active_materialized: list[dict[str, Any]] = []
    materialized_task_ids: list[str] = []
    missing_source_ref_count = 0
    current_session_materialized = 0

    for proposal in proposed:
        task_id = str(proposal.get("id") or "").strip()
        if not task_id:
            continue
        current = resolver.get(task_id)
        if current is None:
            pending_proposals.append(
                {
                    "id": task_id,
                    "title": str(proposal.get("title") or "").strip(),
                    "summary_zh": str(proposal.get("summary_zh") or "").strip(),
                    "owner": str(proposal.get("owner") or "").strip(),
                    "reviewer": str(proposal.get("reviewer") or "").strip(),
                }
            )
            continue

        materialized_task_ids.append(task_id)
        current_status = str(current.get("status") or "").lower()
        if current_status in status_counts:
            status_counts[current_status] += 1
        source_ref = normalized_source_ref(current)
        if not source_ref:
            missing_source_ref_count += 1
        elif session_id and str(source_ref.get("session_id") or "").strip() == session_id:
            current_session_materialized += 1
        if current_status != "done":
            active_materialized.append(
                {
                    "id": task_id,
                    "title": str(current.get("title") or proposal.get("title") or "").strip(),
                    "summary_zh": str(current.get("summary_zh") or proposal.get("summary_zh") or "").strip(),
                    "status": str(current.get("status") or "").strip(),
                    "owner": str(current.get("owner") or "").strip(),
                    "reviewer": str(current.get("reviewer") or "").strip(),
                }
            )

    return {
        "source_plane": "planning",
        "session_id": session_id,
        "phase": str(contract.get("phase") or planning.get("phase") or "").strip(),
        "profile": str(contract.get("profile") or planning.get("profile") or "").strip(),
        "planning_dir": str(contract.get("planning_dir") or planning.get("planning_dir") or "").strip(),
        "session_file": str(contract.get("session_file") or planning.get("session_file") or "").strip(),
        "consensus_packet": consensus_packet,
        "execution_materialization": execution_materialization,
        "proposed_total": len(proposed),
        "materialized_count": len(materialized_task_ids),
        "pending_materialization_count": len(pending_proposals),
        "done": status_counts["done"],
        "review_approved": status_counts["review_approved"],
        "in_progress": status_counts["in_progress"],
        "review": status_counts["review"],
        "todo": status_counts["todo"],
        "blocked": status_counts["blocked"],
        "materialized_task_ids": materialized_task_ids,
        "pending_proposals": pending_proposals,
        "active_materialized_tasks": active_materialized,
        "planning_backed_total": len(planning_backed_tasks),
        "planning_backed_active": sum(
            1 for task in planning_backed_tasks if str(task.get("status") or "").lower() in {"todo", "in_progress", "review", "review_approved", "blocked"}
        ),
        "current_session_materialized": current_session_materialized,
        "missing_source_ref_count": missing_source_ref_count,
    }


def coordination_payload_resolved(entry: dict[str, Any] | None) -> bool:
    if not isinstance(entry, dict):
        return False
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    if payload.get("resolved_at"):
        return True
    status = str(payload.get("status") or "").strip().lower()
    return status in {"resolved", "completed", "done"}


def normalize_coordination_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def coordination_payload_status(entry: dict[str, Any] | None) -> str:
    if not isinstance(entry, dict):
        return ""
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    return normalize_coordination_token(payload.get("status"))


def coordination_payload_field(entry: dict[str, Any] | None, field: str) -> Any:
    if not isinstance(entry, dict):
        return None
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    return payload.get(field)


def load_local_coordination_payload(path_value: str) -> dict[str, Any] | None:
    candidate = str(path_value or "").strip()
    if not candidate or candidate.startswith("../") or "://" in candidate:
        return None
    local_path = ROOT / candidate
    if not local_path.exists() or not local_path.is_file():
        return None
    try:
        text = local_path.read_text(encoding="utf-8")
        if local_path.suffix == ".json":
            payload = json.loads(text)
        else:
            if yaml is None:
                return None
            payload = yaml.safe_load(text)
    except (OSError, json.JSONDecodeError, *YAML_ERROR_TYPES):
        return None
    return payload if isinstance(payload, dict) else None


def coordination_repo_root(repo_id: str) -> Path | None:
    config = load_config()
    root = repository_local_path(config, repo_id)
    if isinstance(root, Path):
        return root
    if repo_id == "front_ai_trading_system":
        fallback = ROOT.parent / "front-ai-trading-system"
        return fallback if fallback.exists() else None
    if repo_id == "pantheon":
        return ROOT
    return None


def coordination_audit_matches(repo_root: Path | None, feature_id: str, marker: str) -> bool:
    if repo_root is None:
        return False
    audit_dir = repo_root / ".coordination" / "audit"
    if not audit_dir.exists():
        return False
    return any(audit_dir.glob(f"{feature_id}-{marker}-*.json"))


def coordination_repo_payload_exists(repo_root: Path | None, rel_path: str | None) -> bool:
    candidate = str(rel_path or "").strip()
    if repo_root is None or not candidate:
        return False
    if candidate.startswith("/") or ".." in candidate:
        return False
    return (repo_root / candidate).is_file()


def coordination_payload_has_runtime_verification(entry: dict[str, Any] | None) -> bool:
    payload = entry.get("payload") if isinstance(entry, dict) else None
    if not isinstance(payload, dict):
        return False
    if payload.get("runtime_verified_at") or payload.get("verified_runtime_ref"):
        return True
    payload_type = normalize_coordination_token(payload.get("type"))
    return payload_type in {"needs_runtime", "bff_gap"} and bool(payload.get("resolved_at"))


def coordination_state_flags(feature: dict[str, Any]) -> dict[str, bool]:
    feature_id = str(feature.get("feature_id") or "").strip()
    contract_ready = coordination_payload_entry(feature, "responses", "contract-ready")
    lovable_task = coordination_payload_entry(feature, "responses", "lovable-ui-task")
    backend_delivery = coordination_payload_entry(feature, "responses", "backend-delivery")
    ui_done = coordination_payload_entry(feature, "requests", "ui-done")
    frontend_feedback = coordination_payload_entry(feature, "requests", "frontend-feedback")
    frontend_feedback_response = coordination_payload_entry(feature, "responses", "frontend-feedback")
    bff_gap = coordination_payload_entry(feature, "requests", "bff-gap")
    needs_runtime = coordination_payload_entry(feature, "requests", "needs-runtime")

    front_root = coordination_repo_root("front_ai_trading_system")
    pantheon_root = coordination_repo_root("pantheon")
    mirrored_contract_path = f".coordination/responses/{feature_id}-contract-ready.yaml" if feature_id else None
    mirrored_delivery_path = f".coordination/responses/{feature_id}-backend-delivery.yaml" if feature_id else None

    mirrored_to_front = bool(feature.get("mirrored_to_target_repo")) or coordination_repo_payload_exists(front_root, mirrored_contract_path)
    if not mirrored_to_front:
        mirrored_to_front = coordination_repo_payload_exists(front_root, mirrored_delivery_path)

    dispatch_recorded = bool(feature.get("last_dispatched_at")) or coordination_audit_matches(
        pantheon_root, feature_id, "dispatch-emitted"
    )
    receiver_visible = mirrored_to_front and (
        bool(feature.get("last_dispatched_at"))
        or coordination_audit_matches(front_root, feature_id, "received")
    )

    lovable_consumed = any(bool(entry) for entry in (ui_done, frontend_feedback, frontend_feedback_response, bff_gap, needs_runtime))
    ui_activated = any(bool(entry) for entry in (ui_done, frontend_feedback, frontend_feedback_response))
    runtime_verified = any(
        coordination_payload_has_runtime_verification(entry)
        for entry in (needs_runtime, bff_gap, ui_done, frontend_feedback, frontend_feedback_response, backend_delivery)
    )

    return {
        "backend_route_live": bool(contract_ready or backend_delivery),
        "pantheon_handoff_published": bool(contract_ready or lovable_task or backend_delivery),
        "mirrored_to_front_default_branch": mirrored_to_front,
        "dispatch_emitted": dispatch_recorded,
        "front_receiver_applied": receiver_visible,
        "lovable_consumed": lovable_consumed,
        "ui_activated": ui_activated,
        "runtime_verified": runtime_verified,
    }


def coordination_local_response_path(feature: dict[str, Any], payload_type: str) -> str | None:
    feature_id = str(feature.get("feature_id") or "").strip()
    if not feature_id:
        return None
    candidate = ROOT / ".coordination" / "responses" / f"{feature_id}-{payload_type}.yaml"
    if candidate.exists():
        return str(candidate.relative_to(ROOT))
    return None


def coordination_payload_entry(feature: dict[str, Any], bucket: str, payload_type: str) -> dict[str, Any] | None:
    typed_key = f"{bucket}_by_type"
    typed_entries = feature.get(typed_key)
    if isinstance(typed_entries, dict):
        candidate = typed_entries.get(payload_type)
        if isinstance(candidate, dict):
            if bucket == "responses" and payload_type == "frontend-feedback":
                local_payload = load_local_coordination_payload(str(candidate.get("path") or ""))
                if isinstance(local_payload, dict):
                    overlaid = dict(candidate)
                    overlaid["payload"] = local_payload
                    return overlaid
            return candidate

    latest_key = "latest_request" if bucket == "requests" else "latest_response"
    latest_path_key = f"{latest_key}_path"
    latest_payload = feature.get(latest_key)
    if isinstance(latest_payload, dict) and str(latest_payload.get("type") or "").strip() == payload_type:
        return {
            "type": payload_type,
            "path": feature.get(latest_path_key) or feature.get("latest_path"),
            "payload": latest_payload,
            "updated_at": latest_payload.get("updated_at") or latest_payload.get("created_at") or feature.get("last_updated_at"),
            "source_repo_id": feature.get("source_repo_id"),
            "target_repo_id": feature.get("target_repo_id"),
        }

    if bucket == "responses" and payload_type == "frontend-feedback":
        local_path = coordination_local_response_path(feature, payload_type)
        if local_path:
            local_payload = load_local_coordination_payload(local_path)
            if isinstance(local_payload, dict):
                return {
                    "type": payload_type,
                    "path": local_path,
                    "payload": local_payload,
                    "updated_at": local_payload.get("reviewed_at") or feature.get("last_updated_at"),
                    "source_repo_id": feature.get("source_repo_id"),
                    "target_repo_id": feature.get("target_repo_id"),
                }

    if bucket == "responses" and payload_type == "lovable-ui-task" and isinstance(feature.get("lovable_task"), dict):
        lovable_payload = feature["lovable_task"]
        return {
            "type": payload_type,
            "path": feature.get("lovable_task_path"),
            "payload": lovable_payload,
            "updated_at": lovable_payload.get("updated_at") or lovable_payload.get("created_at") or feature.get("last_updated_at"),
            "source_repo_id": feature.get("source_repo_id"),
            "target_repo_id": feature.get("target_repo_id"),
        }

    return None


def coordination_review_snapshot(feature_id: str | None) -> dict[str, str] | None:
    candidate = str(feature_id or "").strip()
    if not candidate:
        return None
    review_path = ROOT / ".coordination" / "reviews" / f"{candidate}-review.md"
    if not review_path.exists():
        return None
    try:
        text = review_path.read_text(encoding="utf-8")
    except OSError:
        return {"path": str(review_path.relative_to(ROOT)), "disposition": "reviewed"}

    lowered = text.lower()
    disposition = "reviewed"
    decision_sections = re.findall(
        r"(?ims)^##\s+(?:final\s+decision|decision)\s*\n+(.*?)(?=^##\s|\Z)",
        text,
    )
    scoped_text = decision_sections[-1].lower() if decision_sections else lowered
    if (
        "follow-up required" in scoped_text
        or "not loop-complete" in scoped_text
        or "required follow-up" in scoped_text
        or "changes requested" in scoped_text
        or "blocked" in scoped_text
    ):
        disposition = "follow_up_required"
    elif "task scope" in scoped_text or "acceptance gate" in scoped_text:
        disposition = "reviewed"
    elif (
        "approved" in scoped_text
        or "loop is complete" in scoped_text
        or "loop-complete" in scoped_text
        or "loop complete" in scoped_text
        or "loop can close" in scoped_text
    ):
        disposition = "approved"
    return {
        "path": str(review_path.relative_to(ROOT)),
        "disposition": disposition,
    }


def coordination_stage(feature: dict[str, Any]) -> tuple[str, str]:
    frontend_feedback = coordination_payload_entry(feature, "requests", "frontend-feedback")
    frontend_feedback_response = coordination_payload_entry(feature, "responses", "frontend-feedback")
    ui_done = coordination_payload_entry(feature, "requests", "ui-done")
    bff_gap = coordination_payload_entry(feature, "requests", "bff-gap")
    contract_ready = coordination_payload_entry(feature, "responses", "contract-ready")
    lovable_task = coordination_payload_entry(feature, "responses", "lovable-ui-task")
    backend_delivery = coordination_payload_entry(feature, "responses", "backend-delivery")
    review = coordination_review_snapshot(feature.get("feature_id"))

    response_disposition = normalize_coordination_token(coordination_payload_field(frontend_feedback_response, "disposition"))
    response_can_close = bool(coordination_payload_field(frontend_feedback_response, "can_close"))
    response_lovable_status = normalize_coordination_token(coordination_payload_field(frontend_feedback_response, "lovable_ui_task_status"))
    response_coordination_stage = normalize_coordination_token(coordination_payload_field(frontend_feedback_response, "coordination_stage"))
    response_next_action = str(coordination_payload_field(frontend_feedback_response, "next_action") or "").strip()
    ui_status = coordination_payload_status(ui_done)
    ui_disposition = normalize_coordination_token(coordination_payload_field(ui_done, "pantheon_disposition"))
    lovable_status = coordination_payload_status(lovable_task)
    backend_status = coordination_payload_status(backend_delivery)

    response_marks_complete = response_can_close or response_disposition in {"approved", "close", "loop_complete"}
    response_marks_followup = response_disposition in {"blocked", "follow_up", "follow_up_required"} or response_coordination_stage in {"blocked", "follow_up", "follow_up_required"}
    explicit_loop_complete = (
        response_marks_complete
        or ui_disposition == "loop_complete"
        or lovable_status == "loop_complete"
        or backend_status == "loop_complete"
        or response_lovable_status == "loop_complete"
    )
    explicit_closed = (
        ui_status == "closed"
        or lovable_status == "closed"
        or response_lovable_status == "closed"
    )

    if explicit_loop_complete:
        return "loop_complete", "Pantheon closeout record marks the current packet loop complete."

    if response_marks_followup:
        if explicit_closed and not response_coordination_stage and response_next_action and response_next_action.lower() != "none":
            # Keep the loop visible as follow-up when the response explicitly names a next action.
            pass
        elif explicit_closed and not response_coordination_stage:
            return "closed", "Current packet record is closed for this scope; reopen only if a later follow-up cycle is dispatched."
        return "frontend_feedback_reviewed_followup", (
            f"Pantheon review is complete; follow-up remains ({response_next_action})."
            if response_next_action and response_next_action.lower() != "none"
            else "Pantheon review is complete; follow-up remains per the closeout response."
        )

    if explicit_closed:
        return "closed", "Current packet record is closed for this scope; reopen only if a later follow-up cycle is dispatched."

    if frontend_feedback:
        if review:
            if review.get("disposition") == "approved":
                return "frontend_feedback_reviewed", "Pantheon review packet approves loop closeout; finalize the closure record."
            if review.get("disposition") == "follow_up_required":
                return "frontend_feedback_reviewed_followup", "Pantheon review is complete; follow-up remains per the review packet."
            return "frontend_feedback_reviewed", "Pantheon review packet exists; inspect the recorded disposition."
        return "frontend_feedback_received", "Pantheon should review the frontend feedback bundle and decide follow-up work."
    if ui_done:
        if review:
            if review.get("disposition") == "approved":
                return "ui_done_reviewed", "Pantheon reviewed the ui-done handoff; finalize the next closure or publish step."
            if review.get("disposition") == "follow_up_required":
                return "ui_done_reviewed_followup", "Pantheon reviewed the ui-done handoff; follow-up remains per the review packet."
            return "ui_done_reviewed", "Pantheon review packet exists for the ui-done handoff; inspect the recorded disposition."
        return "ui_done_received", "Pantheon should pick up review and integration from the returned ui-done handoff."
    if bff_gap and not coordination_payload_resolved(bff_gap):
        return "bff_gap_open", "Pantheon must resolve the open BFF gap before the front-end lane can continue."
    if lovable_task or feature.get("lovable_task_path"):
        return "waiting_for_lovable", "Lovable or the front-end lane can implement the screen and emit ui-done when finished."
    if contract_ready:
        return "contract_ready", "Supervisor should publish or mirror the Lovable task packet from the contract-ready bundle."
    current_type = str(feature.get("current_payload_type") or feature.get("status") or "unknown").strip().lower().replace("-", "_")
    return current_type or "unknown", str(feature.get("next_step") or feature.get("summary") or "Awaiting next coordination payload.")


def build_coordination_summary(orchestrator_state: dict[str, Any] | None) -> dict[str, Any]:
    orchestrator = orchestrator_state or {}
    coordination = orchestrator.get("coordination") if isinstance(orchestrator.get("coordination"), dict) else {}
    raw_features = coordination.get("features") if isinstance(coordination.get("features"), dict) else {}

    features: list[dict[str, Any]] = []
    counts = {
        "tracked_features": 0,
        "lovable_ready": 0,
        "mirrored_to_target_repo": 0,
        "waiting_for_lovable": 0,
        "ui_done_received": 0,
        "frontend_feedback_received": 0,
        "open_bff_gaps": 0,
        "backend_route_live": 0,
        "pantheon_handoff_published": 0,
        "mirrored_to_front_default_branch": 0,
        "dispatch_emitted": 0,
        "front_receiver_applied": 0,
        "lovable_consumed": 0,
        "ui_activated": 0,
        "runtime_verified": 0,
    }

    for feature_id in sorted(raw_features):
        feature = raw_features.get(feature_id)
        if not isinstance(feature, dict):
            continue

        contract_ready = coordination_payload_entry(feature, "responses", "contract-ready")
        lovable_task = coordination_payload_entry(feature, "responses", "lovable-ui-task")
        ui_done = coordination_payload_entry(feature, "requests", "ui-done")
        frontend_feedback_request = coordination_payload_entry(feature, "requests", "frontend-feedback")
        frontend_feedback_response = coordination_payload_entry(feature, "responses", "frontend-feedback")
        frontend_feedback = frontend_feedback_request or frontend_feedback_response
        bff_gap = coordination_payload_entry(feature, "requests", "bff-gap")
        review = coordination_review_snapshot(feature_id)
        stage, next_action = coordination_stage(feature)
        state_flags = coordination_state_flags(feature)
        mirrored = bool(state_flags.get("mirrored_to_front_default_branch"))
        lovable_ready = bool(lovable_task or feature.get("lovable_task_path"))
        open_bff_gap = bool(stage == "bff_gap_open" and bff_gap and not coordination_payload_resolved(bff_gap))

        feature_summary = {
            "feature_id": feature_id,
            "screen": feature.get("screen"),
            "summary": feature.get("summary"),
            "source_repo": feature.get("source_repo"),
            "source_repo_id": feature.get("source_repo_id"),
            "target_repo_id": feature.get("target_repo_id"),
            "target_agent": feature.get("target_agent"),
            "worker_kind": feature.get("worker_kind"),
            "current_payload_type": feature.get("current_payload_type"),
            "stage": stage,
            "next_action": next_action,
            "last_updated_at": feature.get("last_updated_at"),
            "last_dispatched_at": feature.get("last_dispatched_at"),
            "lovable_ready": lovable_ready,
            "mirrored_to_target_repo": mirrored,
            "has_contract_ready": bool(contract_ready),
            "has_lovable_task": bool(lovable_task or feature.get("lovable_task_path")),
            "has_ui_done": bool(ui_done),
            "has_frontend_feedback": bool(frontend_feedback),
            "has_bff_gap": bool(bff_gap),
            "bff_gap_open": open_bff_gap,
            "state_flags": state_flags,
            "review_path": review.get("path") if isinstance(review, dict) else None,
            "review_disposition": review.get("disposition") if isinstance(review, dict) else None,
            "paths": {
                "contract_ready": contract_ready.get("path") if isinstance(contract_ready, dict) else None,
                "lovable_task": lovable_task.get("path") if isinstance(lovable_task, dict) else feature.get("lovable_task_path"),
                "lovable_prompt": feature.get("lovable_prompt_path"),
                "ui_done": ui_done.get("path") if isinstance(ui_done, dict) else None,
                "frontend_feedback": frontend_feedback.get("path") if isinstance(frontend_feedback, dict) else None,
                "bff_gap": bff_gap.get("path") if isinstance(bff_gap, dict) else None,
                "review": review.get("path") if isinstance(review, dict) else None,
            },
        }
        features.append(feature_summary)

        counts["tracked_features"] += 1
        counts["lovable_ready"] += int(lovable_ready)
        counts["mirrored_to_target_repo"] += int(mirrored)
        counts["waiting_for_lovable"] += int(stage == "waiting_for_lovable")
        counts["ui_done_received"] += int(bool(ui_done))
        counts["frontend_feedback_received"] += int(bool(frontend_feedback))
        counts["open_bff_gaps"] += int(open_bff_gap)
        for flag_name, enabled in state_flags.items():
            counts[flag_name] += int(bool(enabled))

    return {
        "last_scan_at": coordination.get("last_scan_at"),
        "counts": counts,
        "features": features,
    }


def build_dashboard_bundle(
    state: dict[str, Any],
    planning_state: dict[str, Any] | None,
    orchestrator_state: dict[str, Any] | None,
    approval_state: dict[str, Any] | None,
) -> dict[str, Any]:
    planning = planning_state or {}
    orchestrator = orchestrator_state or {}
    approvals = approval_state or {}
    config = load_config()
    dispatch_policy = build_dispatch_policy_summary(config)
    resolver = task_resolver(state)
    task_map = resolver.active_task_map()
    archive_index = load_archive_index()
    archive_counts = archive_index.get("counts", {}) if isinstance(archive_index.get("counts"), dict) else {}
    recent_terminal_tasks = orchestrator.get("recent_terminal_tasks")
    if not isinstance(recent_terminal_tasks, list):
        recent_terminal_tasks = recent_terminal_summaries(limit=task_archive_recent_limit())
    workers = normalize_runtime_workers(state, orchestrator)
    queue_events = [
        event
        for event in normalize_runtime_queue(orchestrator)
        if str(event.get("status") or "").lower() not in {"completed", "failed"}
        and resolver.dependency_status(str(event.get("task_id") or "")) not in {"done", TASK_TERMINAL_SUPERSEDED}
    ]
    live_workers, mismatches = detect_truth_mismatches(
        state,
        workers,
        queue_events,
        approvals,
        resolver,
        orchestrator,
    )
    bridge_summary = build_bridge_summary(state, planning)
    coordination_summary = build_coordination_summary(orchestrator)
    supervisor_state = orchestrator.get("supervisor") if isinstance(orchestrator.get("supervisor"), dict) else {}

    live_workers_by_task: dict[str, list[dict[str, Any]]] = {}
    for worker in live_workers:
        task_id = str(worker.get("task_id") or "").strip()
        if task_id:
            live_workers_by_task.setdefault(task_id, []).append(worker)

    dispatch_pauses = (
        orchestrator.get("provider_guardrails", {}).get("dispatch_pauses")
        if isinstance(orchestrator.get("provider_guardrails"), dict)
        and isinstance(orchestrator.get("provider_guardrails", {}).get("dispatch_pauses"), dict)
        else orchestrator.get("dispatch_pauses")
        if isinstance(orchestrator.get("dispatch_pauses"), dict)
        else {}
    )
    paused_actors = {str(actor or "").strip().lower() for actor in dispatch_pauses.keys() if str(actor or "").strip()}
    actor_loads: dict[str, int] = {}
    for worker in live_workers:
        if str(worker.get("bucket") or "").lower() not in {"running", "pending"}:
            continue
        actor = canonical_agent_name(worker.get("actor") or worker.get("provider"))
        if not actor:
            continue
        actor_key = actor.lower()
        actor_loads[actor_key] = actor_loads.get(actor_key, 0) + 1

    ready_now = 0
    dependency_ready = 0
    in_progress = 0
    in_review = 0
    blocked = 0
    review_approved = 0
    done = int(archive_counts.get("completed") or 0)
    superseded = int(archive_counts.get(TASK_TERMINAL_SUPERSEDED) or 0)
    for task in state.get("tasks", []):
        status = str(task.get("status") or "").lower()
        if status == "todo" and all(dependency_is_satisfied(resolver, dep_id) for dep_id in task.get("depends_on", [])):
            dependency_ready += 1
            owner = canonical_agent_name(task.get("owner"))
            owner_key = owner.lower()
            if not owner_key:
                continue
            if owner_key in paused_actors:
                continue
            if any(worker.get("bucket") in {"running", "pending"} for worker in live_workers_by_task.get(str(task.get("id") or ""), [])):
                continue
            if actor_loads.get(owner_key, 0) >= dashboard_agent_capacity(config, owner):
                continue
            ready_now += 1
        elif status == "in_progress":
            in_progress += 1
        elif status == "review":
            in_review += 1
        elif status == "blocked":
            blocked += 1
        elif status == "review_approved":
            review_approved += 1

    worker_task_links: list[dict[str, Any]] = []
    mismatch_index: dict[tuple[str, str], list[str]] = {}
    mismatch_detail_index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for mismatch in mismatches:
        task_id = str(mismatch.get("task_id") or "")
        run_id = str(mismatch.get("worker_run_id") or "")
        mismatch_index.setdefault((task_id, run_id), []).append(str(mismatch.get("type") or "mismatch"))
        mismatch_detail_index.setdefault((task_id, run_id), []).append(mismatch)
    queue_map = {str(event.get("id") or ""): event for event in queue_events}
    for worker in live_workers:
        task_id = str(worker.get("task_id") or "")
        task = task_map.get(task_id, {})
        if not task and worker.get("task_source") == "handoff" and isinstance(worker.get("handoff"), dict):
            handoff = worker["handoff"]
            task = {
                "id": task_id,
                "title": "Pending handoff",
                "summary_zh": handoff.get("message"),
                "next": handoff.get("message"),
                "status": handoff.get("status") or "pending",
                "owner": handoff.get("to"),
                "reviewer": handoff.get("from"),
                "source_plane": "handoff",
                "source_ref": {"handoff_from": handoff.get("from"), "handoff_to": handoff.get("to")},
            }
        queue_event = queue_map.get(str(worker.get("queue_event_id") or ""), {})
        linked_mismatches = mismatch_detail_index.get((task_id, str(worker.get("run_id") or "")), [])
        worker_task_links.append(
            {
                "task_id": task_id or None,
                "task_title": task.get("title"),
                "task_summary": task.get("summary_zh"),
                "task_next": task.get("next"),
                "task_status": task.get("status"),
                "owner": task.get("owner"),
                "reviewer": task.get("reviewer"),
                "github_review_bridge": task.get(GITHUB_REVIEW_BRIDGE_KEY),
                "expected_actor": expected_task_actor(task) if task else None,
                "source_plane": task.get("source_plane"),
                "source_ref": normalized_source_ref(task),
                "worker_run_id": worker.get("run_id"),
                "queue_event_id": worker.get("queue_event_id"),
                "queue_status": queue_event.get("status"),
                "queue_last_event_at": queue_event.get("last_event_at"),
                "actor": worker.get("actor"),
                "provider": worker.get("provider"),
                "task_source": worker.get("task_source"),
                "worker_status": worker.get("status"),
                "runtime_bucket": worker.get("bucket"),
                "dispatch_reason": worker.get("reason"),
                "mode": worker.get("dispatch_mode"),
                "delivery_mode": worker.get("delivery_mode"),
                "last_event_at": worker.get("last_event_at"),
                "last_error": worker.get("last_error"),
                "mismatch_flags": mismatch_index.get((task_id, str(worker.get("run_id") or "")), []),
                "mismatch_count": len(linked_mismatches),
                "resolution_hints": [str(item.get("resolution_hint") or "") for item in linked_mismatches if str(item.get("resolution_hint") or "")],
            }
        )

    proposed = planning.get("proposed_execution_tasks") or []
    materialized_count = int(bridge_summary.get("materialized_count") or 0)
    runtime_mode = str(planning.get("runtime_mode") or "supervisor_managed_execution")
    planning_status = str(planning.get("status") or "inactive")
    supervisor_focus = str(supervisor_state.get("focus_mode") or "").strip()
    if supervisor_focus in {"planning", "execution"}:
        focus_mode = supervisor_focus
        focus_mode_source = "supervisor"
    else:
        focus_mode = "planning" if planning_status in {"active", "human_required"} else "execution" if runtime_mode == "supervisor_managed_execution" else "execution"
        focus_mode_source = "planning_state_fallback"

    live_worker_queue_ids = {str(worker.get("queue_event_id") or "") for worker in live_workers if str(worker.get("queue_event_id") or "")}
    computed_mode_occupancy = {
        "planning": {"running": 0, "pending": 0, "queued": 0},
        "execution": {"running": 0, "pending": 0, "queued": 0},
        "coordination": {"running": 0, "pending": 0, "queued": 0},
        "chair_review": {"running": 0, "pending": 0, "queued": 0},
    }
    dispatch_mode_map = {
        "discussion_planning": "planning",
        "execution": "execution",
        "coordination": "coordination",
        "chair_review": "chair_review",
    }
    for worker in live_workers:
        mode_name = dispatch_mode_map.get(str(worker.get("dispatch_mode") or "").strip())
        if not mode_name:
            continue
        bucket_name = "running" if worker.get("bucket") == "running" else "pending"
        computed_mode_occupancy[mode_name][bucket_name] += 1
    for event in queue_events:
        event_id = str(event.get("id") or "")
        if event_id and event_id in live_worker_queue_ids:
            continue
        mode_name = dispatch_mode_map.get(str(event.get("dispatch_mode") or "").strip())
        if not mode_name:
            continue
        computed_mode_occupancy[mode_name]["queued"] += 1
    mode_occupancy = computed_mode_occupancy

    chair_rotation = orchestrator.get("chair_rotation") if isinstance(orchestrator.get("chair_rotation"), dict) else {}
    chair_summary = {
        "current_index": int(chair_rotation.get("current_index") or 0),
        "last_chair_agent": chair_rotation.get("last_chair_agent"),
        "last_chair_run_at": chair_rotation.get("last_chair_run_at"),
        "last_chair_reason": chair_rotation.get("last_chair_reason"),
        "last_review_path": chair_rotation.get("last_review_path"),
        "last_review_summary": chair_rotation.get("last_review_summary") or [],
        "pending_review_path": chair_rotation.get("pending_review_path"),
        "pending_review_agent": chair_rotation.get("pending_review_agent"),
        "sidecar_approved_until": chair_rotation.get("sidecar_approved_until"),
    }

    lanes: dict[str, dict[str, int]] = {}
    for worker in workers:
        actor = str(worker.get("actor") or "-")
        lane = lanes.setdefault(actor, {"running": 0, "pending": 0, "transition": 0, "completed": 0, "failed": 0})
        bucket = str(worker.get("bucket") or "pending")
        if bucket in {"running", "pending"} and not worker.get("is_live_runtime"):
            continue
        lane[bucket] = lane.get(bucket, 0) + 1
        if worker.get("status") == "failed":
            lane["failed"] += 1

    dispatch_targets = {name: meta["target_workload"] for name, meta in KNOWN_AGENTS.items()}

    sprint_started_at_value = str(state.get("sprint_started_at") or "").strip() or None
    completed_in_sprint, superseded_in_sprint = count_terminal_since(sprint_started_at_value)

    bff_consol_archived_ids: list[str] = []
    if ARCHIVE_TASKS_DIR.exists():
        for path in ARCHIVE_TASKS_DIR.glob("BFF-CONSOL-*.json"):
            try:
                st = os.lstat(path)
                import stat
                if stat.S_ISLNK(st.st_mode):
                    raise RuntimeError(f"archive-leaf cannot be a symlink: {path}")
                if not stat.S_ISREG(st.st_mode):
                    continue
            except OSError:
                continue
            stem = path.stem
            if stem.endswith("-SIDECAR-BFF-HANDOFF") or stem.endswith("-SIDECAR-ACCEPTANCE") or stem.endswith("-SIDECAR-REVIEW"):
                continue
            bff_consol_archived_ids.append(stem)
    bff_consol_archived_ids.sort()

    return {
        "generated_at": iso_now(),
        "focus_mode": focus_mode,
        "focus_mode_source": focus_mode_source,
        "runtime_summary": {
            "supervisor_pid": supervisor_state.get("pid"),
            "heartbeat_at": supervisor_state.get("last_heartbeat_at") or orchestrator.get("last_heartbeat_at"),
            "queue_depth": len(queue_events),
            "pending_approvals": len(approvals.get("pending") or []),
            "running_workers": sum(1 for worker in live_workers if worker.get("bucket") == "running"),
            "pending_workers": sum(1 for worker in live_workers if worker.get("bucket") == "pending"),
            "mismatch_count": len(mismatches),
            "mode_status": supervisor_state.get("mode_status") or ("active" if focus_mode == "planning" else "idle"),
            "mode_switch_requested": supervisor_state.get("mode_switch_requested"),
            "mode_occupancy": mode_occupancy,
            "lanes": lanes,
            "dispatch_targets": dispatch_targets,
        },
        "execution_summary": {
            "ready_now": ready_now,
            "dependency_ready": dependency_ready,
            "in_progress": in_progress,
            "in_review": in_review,
            "blocked": blocked,
            "review_approved": review_approved,
            "done": done,
            "superseded": superseded,
            "live_attached": sum(1 for linked in live_workers_by_task.values() if any(worker.get("bucket") == "running" for worker in linked)),
            "mismatch_count": len(mismatches),
            "planning_backed_total": int(bridge_summary.get("planning_backed_total") or 0),
            "planning_backed_active": int(bridge_summary.get("planning_backed_active") or 0),
        },
        "planning_summary": {
            "status": planning.get("status"),
            "consensus_status": planning.get("consensus_status"),
            "human_gate_status": planning.get("human_gate_status"),
            "counts": planning.get("counts") or {},
            "materialized_count": materialized_count,
            "proposed_execution_tasks": len(proposed),
            "active_session": planning.get("active_session")
            or {
                "session_id": planning.get("session_id"),
                "planning_dir": planning.get("planning_dir"),
                "session_file": planning.get("session_file"),
                "status": planning.get("status"),
            },
            "recent_sessions": planning.get("recent_sessions") or [],
        },
        "archive_summary": {
            "updated_at": archive_index.get("updated_at"),
            "counts": {
                "total": int(archive_counts.get("total") or 0),
                "completed": done,
                "superseded": superseded,
                "completed_in_sprint": completed_in_sprint,
                "superseded_in_sprint": superseded_in_sprint,
            },
            "sprint_started_at": sprint_started_at_value,
            "recent_terminal_ids": archive_index.get("recent_terminal_ids") or [],
            "recent_terminal_tasks": recent_terminal_tasks,
            "bff_consol_archived_ids": bff_consol_archived_ids,
        },
        "coordination_summary": coordination_summary,
        "bridge_summary": bridge_summary,
        "chair_summary": chair_summary,
        "dispatch_policy": dispatch_policy,
        "recent_helper_claims": recent_helper_claims(),
        "worker_task_links": worker_task_links,
        "truth_mismatches": mismatches,
    }


def write_dashboard_bundle(state: dict[str, Any]) -> None:
    config = load_config()
    planning_state = load_planning_state()
    try:
        orchestrator_state = load_runtime_state(config)
    except KeyError:
        orchestrator_state = {}
    approval_state = load_json_file(APPROVAL_QUEUE_FILE, {"pending": [], "history": []})
    bundle = build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)
    DASHBOARD_BUNDLE_FILE.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


DASHBOARD_LOG_TAIL_LINES = 5000


def _mirror_log_tail(source: Path, target: Path, max_lines: int) -> None:
    try:
        tail = read_activity_log_tail_bytes(source, max_lines=max_lines)
        if tail is None:
            return
        durable_write_bytes(target, tail)
    except OSError:
        return


def dashboard_orchestrator_state(state: dict[str, Any], orchestrator_state: dict[str, Any]) -> dict[str, Any]:
    dashboard_state = deepcopy(orchestrator_state)
    dashboard_workers = dashboard_state.setdefault("workers", {})
    for worker in normalize_runtime_workers(state, orchestrator_state):
        run_id = str(worker.get("run_id") or "").strip()
        if not run_id or run_id not in dashboard_workers:
            continue
        dashboard_workers[run_id]["pid_alive"] = worker.get("pid_alive")
        dashboard_workers[run_id]["pid_state"] = worker.get("pid_state")
        dashboard_workers[run_id]["is_live_runtime"] = worker.get("is_live_runtime")
        dashboard_workers[run_id]["runtime_bucket"] = worker.get("bucket")
        dashboard_workers[run_id]["dispatch_mode"] = worker.get("dispatch_mode")
    return dashboard_state


def sync_docs_site(state: dict[str, Any]) -> None:
    DOCS_SITE_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config()
    try:
        runtime_state = load_runtime_state(config)
    except KeyError:
        runtime_state = {}
    mirror_files = [
        STATUS_FILE,
        CURRENT_WORK_FILE,
        DASHBOARD_BUNDLE_FILE,
        ORCHESTRATOR_STATE_FILE,
        APPROVAL_QUEUE_FILE,
        PLANNING_STATE_FILE,
    ]
    rename_map = {
        "state.json": "orchestrator-state.json",
        "approval-queue.json": "approval-queue.json",
    }
    for path in mirror_files:
        if path.exists():
            target_name = rename_map.get(path.name, path.name)
            if path.name == "state.json":
                dashboard_state = dashboard_orchestrator_state(state, runtime_state)
                (DOCS_SITE_DIR / target_name).write_text(
                    json.dumps(dashboard_state, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
            else:
                shutil.copy2(path, DOCS_SITE_DIR / target_name)
    _mirror_log_tail(LOG_FILE, DOCS_SITE_DIR / LOG_FILE.name, DASHBOARD_LOG_TAIL_LINES)


def sync_all(
    state: dict[str, Any],
    *,
    refresh_views: bool = True,
) -> None:
    assert_task_archive_root_binding()
    sync_canonical_document_metadata(state)
    normalize_state_agents(state)
    prune_archived_active_tasks(state)
    validate_state(state)
    normalize_handoffs(state)
    recompute_agents(state)
    recompute_workload(state)
    ensure_sprint_started_at(state)
    state["updated_at"] = iso_now()
    buffered = getattr(_ACTIVITY_TRANSACTION_LOCAL, "events", None)
    events = list(buffered) if isinstance(buffered, list) else []
    commit_state_with_activity_outbox(state, events)
    if refresh_views:
        refresh_derived_status_views(state)


def refresh_derived_status_views(state: dict[str, Any]) -> None:
    """Refresh projections after canonical state and audit are durable."""

    logs = load_logs()
    write_current_work(state, logs)
    write_dashboard_bundle(state)
    sync_docs_site(state)


@contextmanager
def derived_status_views_lock():
    """Serialize derived projections without extending the canonical task lock."""

    lock_path = STATUS_FILE.parent / ".orchestrator" / "status-derived-views.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        lock_path,
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError(
                f"derived status views lock must be a regular file: {lock_path}"
            )
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def refresh_derived_status_views_if_current(state: dict[str, Any]) -> bool:
    """Render only if this command still owns the latest canonical projection."""

    expected_sha256 = _canonical_json_sha256(state)
    with derived_status_views_lock():
        try:
            current = json.loads(
                read_regular_file_bytes(
                    STATUS_FILE,
                    source="canonical status projection",
                ).decode("utf-8", errors="strict")
            )
        except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        if (
            not isinstance(current, dict)
            or _canonical_json_sha256(current) != expected_sha256
        ):
            return False
        refresh_derived_status_views(state)
    return True


def mark_blockers_resolved(state: dict[str, Any], task_id: str) -> None:
    for blocker in state.get("blockers", []):
        if blocker["task_id"] == task_id and blocker["status"] == "open":
            blocker["status"] = "resolved"
            blocker["resolved_at"] = iso_now()


def mark_handoffs_done(state: dict[str, Any], task_id: str) -> None:
    for handoff in state.get("handoffs", []):
        if handoff["task_id"] == task_id and handoff["status"] != "done":
            handoff["status"] = "done"
            handoff["resolved_at"] = iso_now()


def mark_handoffs_done_for_actor(state: dict[str, Any], task_id: str, actor: str) -> None:
    for handoff in state.get("handoffs", []):
        if handoff["task_id"] == task_id and handoff.get("to") == actor and handoff["status"] != "done":
            handoff["status"] = "done"
            handoff["resolved_at"] = iso_now()


def normalize_handoffs(state: dict[str, Any]) -> None:
    task_map = {task["id"]: task for task in state["tasks"]}
    pending_by_task: dict[str, list[dict[str, Any]]] = {}
    for handoff in state.get("handoffs", []):
        if handoff.get("status") == "done":
            continue
        pending_by_task.setdefault(handoff["task_id"], []).append(handoff)

    for task_id, pending in pending_by_task.items():
        task = task_map.get(task_id)
        if task:
            task_status = task.get("status")
            if task_status in {"in_progress", "blocked", "done"}:
                for handoff in pending:
                    handoff["status"] = "done"
                    handoff["resolved_at"] = iso_now()
                continue
            if task_status == "review_approved":
                owner = canonical_agent_name(task.get("owner"))
                owner_handoffs = [handoff for handoff in pending if handoff.get("to") == owner]
                for handoff in pending:
                    if handoff not in owner_handoffs:
                        handoff["status"] = "done"
                        handoff["resolved_at"] = iso_now()
                if not owner_handoffs:
                    ensure_review_finalize_handoff(
                        state,
                        task,
                        from_agent=canonical_agent_name(task.get("reviewer")),
                        timestamp=iso_now(),
                        message=task.get("next"),
                    )
                continue

        for handoff in pending[:-1]:
            handoff["status"] = "done"
            handoff["resolved_at"] = iso_now()

    for task in state.get("tasks", []):
        if task.get("status") != "review_approved":
            continue
        task_id = task.get("id")
        owner = canonical_agent_name(task.get("owner"))
        pending = [
            handoff
            for handoff in state.get("handoffs", [])
            if handoff.get("task_id") == task_id and handoff.get("status") != "done"
        ]
        owner_handoffs = [handoff for handoff in pending if handoff.get("to") == owner]
        for handoff in pending:
            if handoff not in owner_handoffs:
                handoff["status"] = "done"
                handoff["resolved_at"] = iso_now()
        if not owner_handoffs:
            ensure_review_finalize_handoff(
                state,
                task,
                from_agent=canonical_agent_name(task.get("reviewer")),
                timestamp=iso_now(),
                message=task.get("next"),
            )


def _bridge_assignment_from_metadata(
    metadata: dict[str, Any],
    *,
    task_id: str,
    owner: str,
    reviewer: str,
    title: str | None,
) -> dict[str, Any] | None:
    bridge = metadata.get("dev_bridge")
    if bridge is None:
        return None
    if not isinstance(bridge, dict):
        raise SystemExit("TASK_METADATA_JSON.dev_bridge must be an object")
    packet_id = str(bridge.get("packet_id") or "").strip()
    packet_digest = str(bridge.get("packet_digest") or "").strip()
    expected_hash = str(bridge.get("task_spec_hash") or "").strip()
    spec = bridge.get("task_spec")
    if (
        not packet_id
        or not re.fullmatch(r"[0-9a-f]{64}", packet_digest)
        or not expected_hash
        or not isinstance(spec, dict)
    ):
        raise SystemExit(
            "TASK_METADATA_JSON.dev_bridge requires packet_id, a SHA-256 packet_digest, "
            "task_spec_hash, and task_spec"
        )

    conversation_id = bridge.get("conversation_id")
    source_turn_ids = bridge.get("source_turn_ids")
    documents = bridge.get("documents")
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        raise SystemExit("Bridge assignment conversation_id is required")
    if not isinstance(source_turn_ids, list) or any(
        not isinstance(item, str) for item in source_turn_ids
    ):
        raise SystemExit("Bridge assignment source_turn_ids must be a string list")
    if not isinstance(documents, list) or any(
        not isinstance(item, dict) for item in documents
    ):
        raise SystemExit("Bridge assignment documents must be an object list")
    if any(
        not isinstance(item.get("path"), str) or not item["path"].strip()
        for item in documents
    ):
        raise SystemExit("Bridge assignment documents entries require a non-empty path")

    required_text = {
        "id": task_id,
        "owner": owner,
        "reviewer": reviewer,
    }
    normalized_spec = deepcopy(spec)
    for field, expected in required_text.items():
        actual = str(spec.get(field) or "").strip()
        if field in {"owner", "reviewer"}:
            actual = canonical_agent_name(actual)
        if actual != expected:
            raise SystemExit(
                f"Bridge assignment {field} mismatch: command={expected!r} metadata={actual!r}"
            )
        normalized_spec[field] = actual
    spec_title = str(spec.get("title") or "").strip()
    if not spec_title:
        raise SystemExit("Bridge assignment task_spec.title is required")
    if title and spec_title != title:
        raise SystemExit(
            f"Bridge assignment title mismatch: command={title!r} metadata={spec_title!r}"
        )
    normalized_spec["title"] = spec_title

    for field in ("depends_on", "artifacts", "acceptance"):
        value = spec.get(field)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise SystemExit(f"Bridge assignment task_spec.{field} must be a string list")
        normalized_spec[field] = list(value)
    for field in ("phase", "summary"):
        value = spec.get(field)
        if value is not None and not isinstance(value, str):
            raise SystemExit(f"Bridge assignment task_spec.{field} must be a string or null")
        normalized_spec[field] = value

    encoded = json.dumps(
        normalized_spec,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    actual_hash = hashlib.sha256(encoded).hexdigest()
    if actual_hash != expected_hash:
        raise SystemExit("Bridge assignment task_spec_hash does not match task_spec")
    normalized_bridge = deepcopy(bridge)
    normalized_bridge["packet_id"] = packet_id
    normalized_bridge["packet_digest"] = packet_digest
    normalized_bridge["conversation_id"] = conversation_id.strip()
    normalized_bridge["source_turn_ids"] = list(source_turn_ids)
    normalized_bridge["documents"] = deepcopy(documents)
    normalized_bridge["task_spec"] = normalized_spec
    metadata["dev_bridge"] = normalized_bridge
    return normalized_bridge


def _normalized_task_artifact_scope(task: Mapping[str, Any]) -> list[tuple[str, str]]:
    raw_artifacts = task.get("artifacts")
    if not isinstance(raw_artifacts, list):
        return []
    target_repo = str(task.get("target_repo") or "").strip() or "pantheon"
    normalized: list[tuple[str, str]] = []
    for raw in raw_artifacts:
        if not isinstance(raw, str) or not raw.strip():
            continue
        value = raw.strip().rstrip("/")
        prefix, separator, suffix = value.partition(":")
        if separator and prefix in {"execute-plans", "frontend-checkout"}:
            normalized.append(("execute-plans", suffix.lstrip("/")))
        elif value.startswith("execute-plans/"):
            normalized.append(
                ("execute-plans", value.removeprefix("execute-plans/"))
            )
        elif value.startswith("frontend-checkout/"):
            normalized.append(
                ("execute-plans", value.removeprefix("frontend-checkout/"))
            )
        else:
            normalized.append((target_repo, value))
    return normalized


def _artifact_paths_overlap(left: str, right: str) -> bool:
    left_parts = PurePosixPath(left.rstrip("/")).parts
    right_parts = PurePosixPath(right.rstrip("/")).parts
    shorter = min(len(left_parts), len(right_parts))
    return left_parts[:shorter] == right_parts[:shorter]


def _validated_artifact_conflict_guard(
    task: Mapping[str, Any],
) -> dict[str, Any] | None:
    guard = task.get("artifact_conflict_guard")
    if guard is None:
        return None
    required = {
        "schema_version",
        "program_id",
        "catalog_sha256",
        "task_id",
        "artifact_scope",
        "allowed_overlap_task_ids",
    }
    if not isinstance(guard, dict) or set(guard) != required:
        raise SystemExit("artifact conflict guard contract is not exact")
    task_id = str(task.get("id") or "").strip()
    if (
        guard.get("schema_version") != 1
        or guard.get("task_id") != task_id
        or not isinstance(guard.get("program_id"), str)
        or not guard["program_id"].strip()
        or not isinstance(guard.get("catalog_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", guard["catalog_sha256"])
    ):
        raise SystemExit(f"artifact conflict guard identity is invalid for {task_id}")
    allowed = guard.get("allowed_overlap_task_ids")
    if (
        not isinstance(allowed, list)
        or any(not isinstance(item, str) or not item.strip() for item in allowed)
        or len(allowed) != len(set(allowed))
    ):
        raise SystemExit(f"artifact conflict guard allowlist is invalid for {task_id}")
    scope = guard.get("artifact_scope")
    if (
        not isinstance(scope, list)
        or any(
            not isinstance(item, dict)
            or set(item) != {"repo", "path"}
            or not isinstance(item.get("repo"), str)
            or not item["repo"].strip()
            or not isinstance(item.get("path"), str)
            or not item["path"].strip()
            for item in scope
        )
    ):
        raise SystemExit(f"artifact conflict guard scope is invalid for {task_id}")
    expected_scope = [
        {"repo": repo, "path": path}
        for repo, path in sorted(_normalized_task_artifact_scope(task))
    ]
    if scope != expected_scope:
        raise SystemExit(f"artifact conflict guard scope mismatch for {task_id}")
    return guard


def enforce_artifact_conflict_admission(
    state: dict[str, Any],
    candidate: dict[str, Any],
) -> None:
    candidate_id = str(candidate.get("id") or "").strip()
    candidate_guard = _validated_artifact_conflict_guard(candidate)
    candidate_scope = _normalized_task_artifact_scope(candidate)
    for other in state.get("tasks", []):
        if not isinstance(other, dict):
            continue
        other_id = str(other.get("id") or "").strip()
        if not other_id or other_id == candidate_id:
            continue
        if str(other.get("status") or "") in {"done", "cancelled", "superseded"}:
            continue
        other_guard = _validated_artifact_conflict_guard(other)
        if candidate_guard is None and other_guard is None:
            continue
        overlap = any(
            left_repo == right_repo and _artifact_paths_overlap(left_path, right_path)
            for left_repo, left_path in candidate_scope
            for right_repo, right_path in _normalized_task_artifact_scope(other)
        )
        if not overlap:
            continue
        if (
            candidate_guard is not None
            and other_id not in candidate_guard["allowed_overlap_task_ids"]
        ):
            raise SystemExit(
                f"artifact conflict guard rejected overlap: {candidate_id} <-> {other_id}"
            )
        if (
            other_guard is not None
            and candidate_id not in other_guard["allowed_overlap_task_ids"]
        ):
            raise SystemExit(
                f"artifact conflict guard rejected overlap: {candidate_id} <-> {other_id}"
            )


def _catalog_assignment_revision_allows_guard_change(
    task: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> bool:
    current_guard = task.get("artifact_conflict_guard")
    incoming_guard = metadata.get("artifact_conflict_guard")
    if current_guard == incoming_guard:
        return True

    from_sha = str(
        os.environ.get("TASK_ASSIGN_CATALOG_REVISION_FROM_SHA") or ""
    ).strip()
    to_sha = str(
        os.environ.get("TASK_ASSIGN_CATALOG_REVISION_TO_SHA") or ""
    ).strip()
    if not from_sha and not to_sha:
        return False
    if current_actor() != "Human/Ops":
        raise SystemExit(
            "Only Human/Ops can revise a catalog-bound assignment."
        )
    if (
        not re.fullmatch(r"[0-9a-f]{64}", from_sha)
        or not re.fullmatch(r"[0-9a-f]{64}", to_sha)
        or from_sha == to_sha
    ):
        raise SystemExit("Catalog assignment revision SHA binding is invalid.")
    if not isinstance(current_guard, dict) or not isinstance(incoming_guard, dict):
        raise SystemExit("Catalog assignment revision requires both exact guards.")
    if current_guard.get("catalog_sha256") != from_sha:
        raise SystemExit("Catalog assignment revision source SHA is not current.")
    if incoming_guard.get("catalog_sha256") != to_sha:
        raise SystemExit("Catalog assignment revision target SHA is not exact.")

    current_without_sha = {
        key: value
        for key, value in current_guard.items()
        if key != "catalog_sha256"
    }
    incoming_without_sha = {
        key: value
        for key, value in incoming_guard.items()
        if key != "catalog_sha256"
    }
    if current_without_sha != incoming_without_sha:
        raise SystemExit(
            "Catalog assignment revision cannot change artifact scope or overlap authority."
        )
    if task.get("program_id") != metadata.get("program_id"):
        raise SystemExit("Catalog assignment revision program identity is not exact.")
    contract_sha = str(
        metadata.get("catalog_task_contract_sha256") or ""
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{64}", contract_sha):
        raise SystemExit(
            "Catalog assignment revision task contract digest is invalid."
        )
    return True


def command_assign(state: dict[str, Any], args: list[str]) -> bool | None:
    from wave_guards import WaveGuardError, check_wave_assign

    if len(args) < 3:
        raise SystemExit("Usage: assign <task-id> <owner> <reviewer> [title]")
    try:
        check_wave_assign(state.get("wave_state") or {})
    except WaveGuardError as exc:
        raise SystemExit(f"Wave guard rejected assign: {exc}") from exc
    task_id, owner, reviewer = args[0], canonical_agent_name(args[1]), canonical_agent_name(args[2])
    title = args[3] if len(args) > 3 else os.environ.get("TASK_TITLE")
    summary_zh = os.environ.get("TASK_SUMMARY_ZH")
    assignment_next = os.environ.get("TASK_NEXT", "").strip()
    metadata = task_metadata_from_env()
    ensure_agent(owner)
    ensure_agent(reviewer)
    if owner == reviewer:
        raise SystemExit("Reviewer cannot equal owner")

    bridge = _bridge_assignment_from_metadata(
        metadata,
        task_id=task_id,
        owner=owner,
        reviewer=reviewer,
        title=title,
    )
    if bridge is not None:
        spec = bridge["task_spec"]
        title = spec["title"]
        summary_zh = spec.get("summary")
        phase = spec.get("phase") or "Unassigned"
        depends_on = list(spec.get("depends_on") or [])
        artifacts = list(spec.get("artifacts") or [])
        acceptance = list(spec.get("acceptance") or [])
    else:
        phase = os.environ.get("TASK_PHASE", "Unassigned")
        depends_on = parse_csv_env("TASK_DEPENDS_ON")
        artifacts = parse_csv_env("TASK_ARTIFACTS")
        acceptance = parse_csv_env("TASK_ACCEPTANCE")

    task = get_task(state, task_id)
    if task is not None and parse_bool_env("TASK_ASSIGN_CREATE_ONLY") is True:
        raise SystemExit(
            f"Task {task_id} already exists; create-only assignment refused."
        )
    artifact_guard_changed = bool(
        task is not None
        and task.get("artifact_conflict_guard") is not None
        and "artifact_conflict_guard" in metadata
        and metadata["artifact_conflict_guard"] != task["artifact_conflict_guard"]
    )
    catalog_assignment_revision = False
    if artifact_guard_changed:
        catalog_assignment_revision = (
            _catalog_assignment_revision_allows_guard_change(task, metadata)
        )
        if not catalog_assignment_revision:
            raise SystemExit(
                f"Task {task_id} artifact conflict guard is immutable."
            )
    if task is None:
        candidate = {
            "id": task_id,
            "artifacts": artifacts,
            **metadata,
        }
    else:
        candidate = deepcopy(task)
        candidate.update(metadata)
    candidate.update(
        {
            "id": task_id,
            "owner": owner,
            "reviewer": reviewer,
            "title": title,
        }
    )
    if catalog_assignment_revision:
        # This transition does not admit a new scope. Validate that the revised
        # candidate still matches the pre-existing exact guard, but do not
        # retroactively reject it because another already-active task later
        # acquired a conflicting scope.
        _validated_artifact_conflict_guard(candidate)
    else:
        enforce_artifact_conflict_admission(state, candidate)
    timestamp = iso_now()
    if task is None:
        if archived_task_snapshot(task_id):
            raise SystemExit(
                f"Task {task_id} is archived. Create a new follow-up task instead of reusing the archived task id."
            )
        task = {
            "id": task_id,
            "title": title,
            "summary_zh": summary_zh,
            "phase": phase,
            "owner": owner,
            "reviewer": reviewer,
            "status": "todo",
            "depends_on": depends_on,
            "artifacts": artifacts,
            "acceptance": acceptance,
            "next": assignment_next or "Assignment created",
            "last_update": timestamp,
        }
        task.update(metadata)
        state["tasks"].append(task)
    else:
        if bridge is not None:
            existing_bridge = task.get("dev_bridge")
            if not isinstance(existing_bridge, dict):
                raise SystemExit(
                    f"Bridge assignment conflict: task {task_id} already exists without bridge provenance"
                )
            existing_packet = str(existing_bridge.get("packet_id") or "").strip()
            existing_digest = str(existing_bridge.get("packet_digest") or "").strip()
            existing_hash = str(existing_bridge.get("task_spec_hash") or "").strip()
            if (
                existing_packet == bridge["packet_id"]
                and existing_digest == bridge["packet_digest"]
                and existing_hash == bridge["task_spec_hash"]
                and existing_bridge == bridge
            ):
                return False
            raise SystemExit(
                f"Bridge assignment conflict: task {task_id} is already bound to "
                f"packet={existing_packet!r} digest={existing_digest!r} spec={existing_hash!r}"
            )
        task["owner"] = owner
        task["reviewer"] = reviewer
        if title:
            task["title"] = title
        if summary_zh:
            task["summary_zh"] = summary_zh
        if metadata:
            task.update(metadata)
        task["last_update"] = timestamp
        task["next"] = assignment_next or "Ownership updated"

    agent = get_agent(state, owner)
    if os.environ.get("TASK_BRANCH"):
        agent["branch"] = os.environ["TASK_BRANCH"]

    append_log(
        {
            "ts": timestamp,
            "agent": current_actor(),
            "type": "assign",
            "task_id": task_id,
            "message": f"Assigned {task_id} to {owner} with reviewer {reviewer}",
        }
    )


def command_start(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: start <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor()
    ensure_agent(actor)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("owner") != actor:
        raise SystemExit(f"Only the owner ({task.get('owner')}) can start {task_id}")
    timestamp = iso_now()
    task["status"] = "in_progress"
    task["last_update"] = timestamp
    task["next"] = message
    mark_handoffs_done_for_actor(state, task_id, actor)
    mark_blockers_resolved(state, task_id)
    append_log({"ts": timestamp, "agent": actor, "type": "start", "task_id": task_id, "message": message})


def command_progress(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: progress <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor()
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("owner") != actor:
        raise SystemExit(f"Only the owner ({task.get('owner')}) can progress {task_id}")
    timestamp = iso_now()
    if task["status"] == "todo":
        task["status"] = "in_progress"
    task["last_update"] = timestamp
    task["next"] = message
    mark_handoffs_done_for_actor(state, task_id, actor)
    append_log({"ts": timestamp, "agent": actor, "type": "progress", "task_id": task_id, "message": message})


def command_note(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: note <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor()
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    timestamp = iso_now()
    task["last_update"] = timestamp
    task["next"] = message
    append_log({"ts": timestamp, "agent": actor, "type": "note", "task_id": task_id, "message": message})


def command_reopen(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: reopen <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor()
    ensure_agent(actor)
    task = get_task(state, task_id)
    if task is None:
        if archived_task_snapshot(task_id):
            raise SystemExit(
                f"Task {task_id} is archived and cannot be reopened in place. Create a new follow-up task that references {task_id}."
            )
        raise SystemExit(f"Unknown task: {task_id}")
    owner = canonical_agent_name(task.get("owner"))
    reviewer = canonical_agent_name(task.get("reviewer"))
    if actor not in {owner, reviewer, "Human/Ops"}:
        raise SystemExit(
            f"Only the owner ({owner}), reviewer ({reviewer}), or Human/Ops can reopen {task_id}"
        )
    binding: dict[str, Any] = {}
    github_review_bridge: dict[str, Any] = {}
    if actor == reviewer:
        explicit_binding = bool(
            os.environ.get("REVIEW_PR", "").strip()
            or os.environ.get("REVIEW_HEAD_SHA", "").strip()
        )
        if explicit_binding:
            binding = resolve_approval_binding(task, warn_if_unbound=False)
        elif isinstance(task.get(APPROVAL_BINDING_KEY), Mapping):
            binding = dict(task[APPROVAL_BINDING_KEY])
        elif task_has_pr_review_target(task):
            raise SystemExit(
                f"{task_id} is PR-backed but reviewer reopen has no exact-head "
                "binding. Set REVIEW_PR and REVIEW_HEAD_SHA so the rejection "
                "also closes the GitHub review gate."
            )
        if binding:
            github_review_bridge = bridge_github_review_decision(
                task,
                actor=actor,
                decision="reopen",
                message=message,
                binding=binding,
            )
    timestamp = iso_now()
    task["status"] = "in_progress"
    task["last_update"] = timestamp
    task["next"] = message
    task.pop("waiting_for", None)
    if github_review_bridge:
        task[GITHUB_REVIEW_BRIDGE_KEY] = dict(github_review_bridge)
    mark_blockers_resolved(state, task_id)
    mark_handoffs_done(state, task_id)
    if actor == reviewer and owner and owner != reviewer:
        state.setdefault("handoffs", []).append(
            {
                "task_id": task_id,
                "from": reviewer,
                "to": owner,
                "message": message,
                "status": "pending",
                "created_at": timestamp,
            }
        )
    append_log(
        {
            "ts": timestamp,
            "agent": actor,
            "type": "reopen",
            "task_id": task_id,
            "message": message,
            **(
                {GITHUB_REVIEW_BRIDGE_KEY: dict(github_review_bridge)}
                if github_review_bridge
                else {}
            ),
        }
    )


def command_handoff(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 3:
        raise SystemExit("Usage: handoff <task-id> <to-agent> <message>")
    task_id, to_agent, message = args[0], canonical_agent_name(args[1]), args[2]
    actor = current_actor()
    ensure_agent(actor)
    ensure_agent(to_agent)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("owner") != actor:
        raise SystemExit(f"Only the owner ({task.get('owner')}) can hand off {task_id} for review")
    if task.get("reviewer") != to_agent:
        raise SystemExit(
            f"{task_id} handoff target must match the assigned reviewer ({task.get('reviewer')}); reassign reviewer first if needed"
        )
    timestamp = iso_now()
    task["status"] = "review"
    task["last_update"] = timestamp
    task["next"] = message
    mark_handoffs_done_for_actor(state, task_id, actor)
    mark_blockers_resolved(state, task_id)
    state.setdefault("handoffs", []).append(
        {
            "task_id": task_id,
            "from": actor,
            "to": to_agent,
            "message": message,
            "status": "pending",
            "created_at": timestamp,
        }
    )
    append_log({"ts": timestamp, "agent": actor, "type": "handoff", "task_id": task_id, "message": f"Handoff to {to_agent}: {message}"})


def command_blocker(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 3:
        raise SystemExit("Usage: blocker <task-id> <message> <waiting-for>")
    task_id, message, waiting_for = args[0], args[1], canonical_agent_name(args[2])
    actor = current_actor()
    ensure_agent(actor)
    ensure_agent(waiting_for)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("owner") != actor:
        raise SystemExit(f"Only the owner ({task.get('owner')}) can block {task_id}")
    timestamp = iso_now()
    task["status"] = "blocked"
    task["waiting_for"] = waiting_for
    task["last_update"] = timestamp
    task["next"] = message
    mark_handoffs_done_for_actor(state, task_id, actor)
    state.setdefault("blockers", []).append(
        {
            "task_id": task_id,
            "owner": actor,
            "waiting_for": waiting_for,
            "message": message,
            "status": "open",
            "created_at": timestamp,
        }
    )
    append_log({"ts": timestamp, "agent": actor, "type": "blocker", "task_id": task_id, "message": f"Blocked on {waiting_for}: {message}"})


def command_restore_approved(state: dict[str, Any], args: list[str]) -> None:
    """Recover a task that was incorrectly downgraded from review_approved to in_progress by the supervisor.

    Only allowed when:
    - actor is the owner
    - current status is in_progress
    - task has review_notes_zh (evidence of a prior approval)
    """
    if len(args) < 2:
        raise SystemExit("Usage: restore_approved <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor()
    ensure_agent(actor)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("owner") != actor:
        raise SystemExit(f"Only the owner ({task.get('owner')}) can restore {task_id}")
    if task.get("status") != "in_progress":
        raise SystemExit(f"restore_approved is only valid when status is in_progress (current: {task.get('status')})")
    if not task.get("review_notes_zh"):
        raise SystemExit(
            f"restore_approved requires review_notes_zh to be present as evidence of a prior approval. "
            f"Use the normal review lifecycle if the task has not been reviewed yet."
        )
    verdict_ref = validate_protected_closeout_transition(
        task,
        transition="review_approved",
    )
    timestamp = iso_now()
    task["status"] = "review_approved"
    task["last_update"] = timestamp
    task["next"] = message
    if verdict_ref is not None:
        task["protected_closeout_verdict"] = verdict_ref
    append_log(
        {
            "ts": timestamp,
            "agent": actor,
            "type": "restore_approved",
            "task_id": task_id,
            "message": message,
        }
    )


def _required_reconcile_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise SystemExit(f"{name} is required for reconcile_merged_done")
    return value


def _validated_git_root(raw_root: str, *, label: str) -> Path:
    candidate = Path(os.path.expanduser(raw_root))
    if not candidate.is_absolute():
        raise SystemExit(f"{label} must be an absolute path")
    symlink_component = _first_symlink_component(candidate)
    if symlink_component is not None:
        raise SystemExit(f"{label} cannot include a symlink component: {symlink_component}")
    root = candidate.resolve()
    if not root.is_dir() or _git_toplevel(root) != root:
        raise SystemExit(f"{label} must be a git repository root: {root}")
    return root


def _merged_commit(
    repository_root: Path,
    raw_commit: str,
    target_ref: str,
    *,
    label: str,
) -> tuple[str, str]:
    commit = run_git_command(
        ["rev-parse", "--verify", f"{raw_commit}^{{commit}}"],
        cwd=repository_root,
        failure_message=f"Cannot reconcile task: {label} commit is unavailable.",
    )
    target_sha = run_git_command(
        ["rev-parse", "--verify", target_ref],
        cwd=repository_root,
        failure_message=f"Cannot reconcile task: {label} target ref {target_ref} is unavailable.",
    )
    if not git_command_succeeds(
        ["merge-base", "--is-ancestor", commit, target_ref],
        cwd=repository_root,
    ):
        raise SystemExit(
            f"Cannot reconcile task: {label} commit {commit} is not merged into {target_ref}."
        )
    return commit, target_sha


def _verified_reviewer_reassignment(
    task: dict[str, Any],
    *,
    evidence_reviewer: str,
    current_reviewer: str,
) -> dict[str, Any]:
    """Return the exact canonical reassignment that explains reviewer drift."""

    try:
        payload = read_regular_file_bytes(
            LOG_FILE,
            source="canonical reviewer reassignment evidence",
        )
    except FileNotFoundError as exc:
        raise SystemExit(
            "Cannot reconcile task: canonical reviewer differs from merged evidence and "
            "the activity audit is unavailable."
        ) from exc

    task_id = str(task.get("id") or "").strip()
    owner = canonical_agent_name(task.get("owner"))
    task_last_update = str(task.get("last_update") or "").strip()
    task_next = str(task.get("next") or "").strip()
    for raw_line in payload.splitlines():
        if not raw_line.strip():
            continue
        event = strict_activity_json_loads(raw_line)
        if not isinstance(event, dict):
            continue
        if (
            event.get("type") == "task_reassigned"
            and str(event.get("task_id") or "").strip() == task_id
            and canonical_agent_name(event.get("old_owner")) == owner
            and canonical_agent_name(event.get("new_owner")) == owner
            and canonical_agent_name(event.get("old_reviewer")) == evidence_reviewer
            and canonical_agent_name(event.get("new_reviewer")) == current_reviewer
            and str(event.get("ts") or "").strip() == task_last_update
            and str(event.get("message") or "").strip() == task_next
        ):
            return {
                "event_id": str(event.get("event_id") or "").strip() or None,
                "ts": task_last_update,
                "old_reviewer": evidence_reviewer,
                "new_reviewer": current_reviewer,
                "message": task_next,
            }
    raise SystemExit(
        "Cannot reconcile task: merged evidence does not bind the canonical reviewer metadata "
        "and no exact task_reassigned audit event explains the drift."
    )


def _supervisor_reassignment_event_id(event: Mapping[str, Any]) -> str:
    payload = (
        f"{event.get('task_id') or ''}\0{event.get('ts') or ''}\0"
        f"{event.get('old_owner') or ''}\0{event.get('new_owner') or ''}\0"
        f"{event.get('old_reviewer') or ''}\0{event.get('new_reviewer') or ''}\0"
        f"{event.get('message') or ''}"
    )
    return "supervisor-reassign-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _verified_done_owner_reassignment(
    task: dict[str, Any],
    *,
    commit_owner: str,
    current_owner: str,
    commit_timestamp: str,
) -> dict[str, Any]:
    """Prove that the latest audited supervisor reassignment explains owner drift."""

    try:
        payload = read_regular_file_bytes(
            LOG_FILE,
            source="canonical done owner reassignment evidence",
        )
    except FileNotFoundError as exc:
        raise SystemExit(
            "Cannot finalize task: prior-owner LLM-Agent trailer requires an exact "
            "audited supervisor task_reassigned event, but the activity audit is unavailable."
        ) from exc

    task_id = str(task.get("id") or "").strip()
    reviewer = canonical_agent_name(task.get("reviewer"))
    audited: list[tuple[datetime, dict[str, Any]]] = []
    for raw_line in payload.splitlines():
        if not raw_line.strip():
            continue
        event = strict_activity_json_loads(raw_line)
        if not isinstance(event, dict):
            continue
        if (
            event.get("type") != "task_reassigned"
            or str(event.get("task_id") or "").strip() != task_id
            or event.get("agent") != "Orchestrator"
            or not event.get("old_owner")
            or not event.get("new_owner")
            or str(event.get("event_id") or "")
            != _supervisor_reassignment_event_id(event)
        ):
            continue
        event_timestamp = _parse_utc_timestamp(event.get("ts"))
        if event_timestamp is None:
            continue
        audited.append((event_timestamp, event))

    owner_changes = [
        item
        for item in audited
        if canonical_agent_name(item[1].get("old_owner"))
        != canonical_agent_name(item[1].get("new_owner"))
    ]
    if not owner_changes:
        raise SystemExit(
            "Cannot finalize task: prior-owner LLM-Agent trailer requires an exact "
            "audited supervisor task_reassigned event."
        )

    latest_timestamp = max(item[0] for item in owner_changes)
    latest_owner_changes = [
        item for item in owner_changes if item[0] == latest_timestamp
    ]
    if len(latest_owner_changes) != 1:
        raise SystemExit(
            "Cannot finalize task: latest audited owner reassignment ordering is ambiguous."
        )
    event_timestamp, event = latest_owner_changes[0]
    if not (
        canonical_agent_name(event.get("old_owner")) == canonical_agent_name(commit_owner)
        and canonical_agent_name(event.get("new_owner")) == canonical_agent_name(current_owner)
        and canonical_agent_name(event.get("old_reviewer")) == reviewer
        and canonical_agent_name(event.get("new_reviewer")) == reviewer
    ):
        raise SystemExit(
            "Cannot finalize task: the latest audited owner reassignment does not "
            "bind the commit owner to the current owner with reviewer continuity."
        )
    later_reviewer_change = next(
        (
            later_event
            for later_timestamp, later_event in audited
            if later_timestamp > event_timestamp
            and canonical_agent_name(later_event.get("old_reviewer"))
            != canonical_agent_name(later_event.get("new_reviewer"))
        ),
        None,
    )
    if later_reviewer_change is not None:
        raise SystemExit(
            "Cannot finalize task: reviewer continuity changed after the audited "
            "owner reassignment."
        )

    delivered_at = _parse_utc_timestamp(commit_timestamp)
    if delivered_at is None:
        raise SystemExit(
            "Cannot finalize task: delivered commit timestamp is unavailable for "
            "owner reassignment ordering."
        )
    if event_timestamp < delivered_at:
        raise SystemExit(
            "Cannot finalize task: audited owner reassignment must follow the delivered commit."
        )

    return {
        "event_id": str(event.get("event_id")),
        "ts": str(event.get("ts")),
        "old_owner": canonical_agent_name(event.get("old_owner")),
        "new_owner": canonical_agent_name(event.get("new_owner")),
        "reviewer": reviewer,
        "message": str(event.get("message") or ""),
        "commit_timestamp": commit_timestamp,
    }


def validate_merged_done_evidence(task: dict[str, Any]) -> dict[str, Any]:
    """Validate immutable, dev-merged review and delivery evidence.

    This recovery path is intentionally stricter than the normal owner closeout:
    it is only for an already-delivered task whose canonical row lost the
    review-approved transition.  Both the review artifact and the delivered
    commit must already be reachable from their respective dev refs.
    """

    task_id = str(task.get("id") or "").strip()
    owner = canonical_agent_name(task.get("owner"))
    reviewer = canonical_agent_name(task.get("reviewer"))
    if not task_id or not owner or not reviewer or owner == reviewer:
        raise SystemExit("Cannot reconcile task: task owner/reviewer metadata is invalid.")

    raw_evidence_file = _required_reconcile_env("RECONCILE_EVIDENCE_FILE")
    evidence_rel = Path(raw_evidence_file)
    if evidence_rel.is_absolute() or ".." in evidence_rel.parts:
        raise SystemExit("RECONCILE_EVIDENCE_FILE must be a repository-relative path without '..'.")
    evidence_path = ROOT / evidence_rel
    symlink_component = _first_symlink_component(evidence_path)
    if symlink_component is not None:
        raise SystemExit(
            f"RECONCILE_EVIDENCE_FILE cannot include a symlink component: {symlink_component}"
        )
    evidence_path = evidence_path.resolve()
    try:
        evidence_path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise SystemExit("RECONCILE_EVIDENCE_FILE escapes the command repository.") from exc
    if not evidence_path.is_file():
        raise SystemExit(f"RECONCILE_EVIDENCE_FILE is not a regular file: {evidence_path}")
    evidence_posix = evidence_rel.as_posix()
    if not git_command_succeeds(
        ["ls-files", "--error-unmatch", "--", evidence_posix],
        cwd=ROOT,
    ):
        raise SystemExit("Cannot reconcile task: evidence file is not tracked by Pantheon git.")

    evidence_target_ref = str(
        os.environ.get("RECONCILE_EVIDENCE_TARGET_REF") or "origin/dev"
    ).strip()
    evidence_commit, evidence_target_sha = _merged_commit(
        ROOT,
        _required_reconcile_env("RECONCILE_EVIDENCE_COMMIT"),
        evidence_target_ref,
        label="evidence",
    )
    evidence_at_commit = run_git_command(
        ["show", f"{evidence_commit}:{evidence_posix}"],
        cwd=ROOT,
        failure_message=(
            "Cannot reconcile task: evidence file is absent from the supplied evidence commit."
        ),
    )
    evidence_text = evidence_path.read_text(encoding="utf-8")
    if evidence_text.rstrip("\n") != evidence_at_commit.rstrip("\n"):
        raise SystemExit(
            "Cannot reconcile task: working evidence file differs from the supplied merged commit."
        )

    required_lines = {
        "task": rf"^# Task Brief:\s*{re.escape(task_id)}\s*$",
        "status": r"^- Status:\s*review_approved\s*$",
        "owner": rf"^- Owner:\s*{re.escape(owner)}\s*$",
    }
    missing = [
        label
        for label, pattern in required_lines.items()
        if re.search(pattern, evidence_text, flags=re.MULTILINE) is None
    ]
    if missing:
        raise SystemExit(
            "Cannot reconcile task: merged evidence does not bind the canonical "
            f"{', '.join(missing)} metadata."
        )
    evidence_reviewer_match = re.search(
        r"^- Reviewer:\s*(?P<reviewer>.+?)\s*$",
        evidence_text,
        flags=re.MULTILINE,
    )
    evidence_reviewer = canonical_agent_name(
        evidence_reviewer_match.group("reviewer") if evidence_reviewer_match else ""
    )
    if not evidence_reviewer or evidence_reviewer == owner:
        raise SystemExit(
            "Cannot reconcile task: merged evidence has invalid independent reviewer metadata."
        )
    reviewer_reassignment = None
    if evidence_reviewer != reviewer:
        reviewer_reassignment = _verified_reviewer_reassignment(
            task,
            evidence_reviewer=evidence_reviewer,
            current_reviewer=reviewer,
        )

    config = load_config()
    repository_id = task_primary_repository_id(config, task)
    repository_slug_value = repository_slug(config, repository_id)
    if repository_id is None or not repository_slug_value:
        raise SystemExit("Cannot reconcile task: a single delivery repository is required.")
    requested_slug = _normalize_github_repo_slug(
        _required_reconcile_env("RECONCILE_DELIVERY_REPOSITORY")
    )
    expected_slug = _normalize_github_repo_slug(repository_slug_value)
    if requested_slug != expected_slug:
        raise SystemExit(
            "Cannot reconcile task: delivery repository does not match task artifacts "
            f"({requested_slug} != {expected_slug})."
        )
    delivery_root = _validated_git_root(
        _required_reconcile_env("RECONCILE_DELIVERY_ROOT"),
        label="RECONCILE_DELIVERY_ROOT",
    )
    actual_slug = _normalize_github_repo_slug(
        run_git_command(
            ["remote", "get-url", "origin"],
            cwd=delivery_root,
            failure_message="Cannot reconcile task: delivery origin remote is unavailable.",
        )
    )
    if actual_slug != expected_slug:
        raise SystemExit(
            "Cannot reconcile task: delivery checkout origin does not match task artifacts "
            f"({actual_slug} != {expected_slug})."
        )
    delivery_target_ref = str(
        os.environ.get("RECONCILE_DELIVERY_TARGET_REF") or "origin/dev"
    ).strip()
    delivery_commit, delivery_target_sha = _merged_commit(
        delivery_root,
        _required_reconcile_env("RECONCILE_DELIVERY_COMMIT"),
        delivery_target_ref,
        label="delivery",
    )
    if expected_slug not in evidence_text or delivery_commit not in evidence_text:
        raise SystemExit(
            "Cannot reconcile task: merged review evidence does not cite the verified "
            "delivery repository and full commit."
        )

    return {
        "recorded_at": iso_now(),
        "reconciled_from_merged_evidence": True,
        "repository_id": repository_id,
        "repository_slug": expected_slug,
        "repository_path": str(delivery_root),
        "commit": delivery_commit,
        "merge_target_ref": delivery_target_ref,
        "merge_target_sha": delivery_target_sha,
        "head_merged_to_target": True,
        "review_evidence": {
            "file": evidence_posix,
            "commit": evidence_commit,
            "merge_target_ref": evidence_target_ref,
            "merge_target_sha": evidence_target_sha,
            "owner": owner,
            "reviewer": evidence_reviewer,
            "canonical_reviewer": reviewer,
            "status": "review_approved",
            **(
                {"reviewer_reassignment": reviewer_reassignment}
                if reviewer_reassignment is not None
                else {}
            ),
        },
    }


def command_reconcile_merged_done(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: reconcile_merged_done <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor()
    ensure_agent(actor)
    if actor != "Human/Ops":
        raise SystemExit("Only Human/Ops can reconcile an already-merged task to done")
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if str(task.get("status") or "") not in {
        "todo",
        "in_progress",
        "blocked",
        "review",
        "review_approved",
    }:
        raise SystemExit(
            f"{task_id} cannot be reconciled from status {task.get('status') or 'missing'}"
        )

    delivery = validate_merged_done_evidence(task)
    timestamp = iso_now()
    delivery["recorded_at"] = timestamp
    verdict_ref = validate_protected_closeout_transition(
        task,
        transition="done",
        consume=True,
        transition_actor=actor,
    )
    if verdict_ref is not None:
        task["protected_closeout_verdict"] = verdict_ref
    task["status"] = "done"
    task["terminal_outcome"] = "completed"
    task["last_update"] = timestamp
    task["next"] = message
    task["delivery"] = delivery
    task.pop("waiting_for", None)
    mark_blockers_resolved(state, task_id)
    mark_handoffs_done(state, task_id)
    archive_terminal_task_from_state(state, task, archived_at=timestamp)
    append_log(
        {
            "ts": timestamp,
            "agent": actor,
            "type": "reconcile_merged_done",
            "task_id": task_id,
            "message": message,
            "delivery": delivery,
        }
    )


def command_done(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: done <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor()
    ensure_agent(actor)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("owner") != actor:
        raise SystemExit(f"Only the owner ({task.get('owner')}) can finalize {task_id} to done")
    if task.get("status") != "review_approved":
        raise SystemExit(f"{task_id} must be review_approved before it can move to done")
    # Allow owner to supply review evidence at done time when reviewer did not set it,
    # but only when it was already present at the head the reviewer actually
    # approved -- not added afterward. See review_evidence_file_committed().
    done_review_file = os.environ.get("REVIEW_FILE", "").strip()
    if done_review_file and not task.get("review_file"):
        approved_head_sha = str((task.get(APPROVAL_BINDING_KEY) or {}).get("head_sha") or "").strip()
        if approved_head_sha:
            config = load_config()
            repository_id = task_primary_repository_id(config, task)
            repository_slug_value = repository_slug(config, repository_id)
            if not repository_slug_value or not review_evidence_file_committed(
                repository=repository_slug_value,
                head_sha=approved_head_sha,
                review_file=done_review_file,
            ):
                raise SystemExit(
                    f"{task_id}: REVIEW_FILE={done_review_file!r} was not present at "
                    f"the reviewed head {approved_head_sha[:12]} the reviewer actually "
                    "approved. Evidence added after approval invalidates the exact-head "
                    "binding and requires a fresh independent review of the commit that "
                    "adds it -- see task-closeout-finalization.md 'Review Evidence "
                    "Manifest Rule'. Do not bind a manifest that was only added "
                    "post-approval."
                )
        task["review_file"] = done_review_file
    validate_loop_completion_claim(task)
    timestamp = iso_now()
    delivery = collect_done_delivery_metadata(task, actor)
    delivery["recorded_at"] = timestamp
    verdict_ref = validate_protected_closeout_transition(
        task,
        transition="done",
        consume=True,
        transition_actor=actor,
    )
    if verdict_ref is not None:
        task["protected_closeout_verdict"] = verdict_ref
    task["status"] = "done"
    task["terminal_outcome"] = "completed"
    task["last_update"] = timestamp
    task["next"] = message
    task["delivery"] = delivery
    task.pop("waiting_for", None)
    mark_blockers_resolved(state, task_id)
    mark_handoffs_done(state, task_id)
    archive_terminal_task_from_state(state, task, archived_at=timestamp)
    append_log(
        {
            "ts": timestamp,
            "agent": actor,
            "type": "done",
            "task_id": task_id,
            "message": message,
            "delivery": delivery,
        }
    )


def command_supersede(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: supersede <task-id> <message> [replacement-task-id]")
    task_id, message = args[0], args[1]
    replacement_task_id = args[2].strip() if len(args) > 2 and args[2].strip() else ""
    actor = current_actor()
    ensure_agent(actor)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    owner = canonical_agent_name(task.get("owner"))
    reviewer = canonical_agent_name(task.get("reviewer"))
    if actor not in {owner, reviewer}:
        raise SystemExit(f"Only the owner ({owner}) or reviewer ({reviewer}) can supersede {task_id}")
    timestamp = iso_now()
    task["status"] = "done"
    task["terminal_outcome"] = TASK_TERMINAL_SUPERSEDED
    task["last_update"] = timestamp
    task["next"] = message
    if replacement_task_id:
        task["superseded_by"] = replacement_task_id
    task.pop("waiting_for", None)
    mark_blockers_resolved(state, task_id)
    mark_handoffs_done(state, task_id)
    archive_terminal_task_from_state(state, task, archived_at=timestamp)
    append_log(
        {
            "ts": timestamp,
            "agent": actor,
            "type": "superseded",
            "task_id": task_id,
            "message": message,
            **({"replacement_task_id": replacement_task_id} if replacement_task_id else {}),
        }
    )


APPROVAL_BINDING_KEY = "review_binding"
GITHUB_REVIEW_BRIDGE_KEY = "github_review_bridge"
APPROVAL_HEAD_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
DEFAULT_APPROVAL_BASE_BRANCH = "dev"
GITHUB_CANONICAL_REVIEW_CONTEXT = "Pantheon canonical review gate"
GITHUB_REVIEW_MODES = {
    "pull_request_review",
    "pull_request_review_and_required_status",
    "required_commit_status",
}


def resolve_approval_binding(
    task: dict[str, Any],
    *,
    warn_if_unbound: bool = True,
) -> dict[str, Any]:
    """Bind an approval to the exact pull-request head the reviewer inspected.

    `scripts/git/task_review_merge_gate.py` compares these identities against
    the PR standing at merge time.  Without them an approval is only a
    timestamp, and a timestamp cannot tell an unchanged head from a head that
    was replaced with an *older* commit -- which is a head no reviewer saw.

    A PR-backed task therefore refuses an unbound approval. Not every task
    produces a PR, so non-PR tasks may still approve without a binding; when
    requested, they receive a warning that a later merge gate would fail
    closed with `approval_head_binding_missing`.
    """

    task_id = str(task.get("id") or "").strip()
    raw_pr = os.environ.get("REVIEW_PR", "").strip().lstrip("#")
    raw_head = os.environ.get("REVIEW_HEAD_SHA", "").strip()
    base_branch = (
        os.environ.get("REVIEW_BASE", "").strip() or DEFAULT_APPROVAL_BASE_BRANCH
    )
    head_branch = (
        os.environ.get("REVIEW_HEAD_BRANCH", "").strip() or f"task/{task_id}"
    )

    owner = str(task.get("owner") or "").strip().casefold()
    reviewer = str(task.get("reviewer") or "").strip().casefold()
    independent = bool(reviewer) and reviewer != owner

    if not raw_pr and not raw_head:
        if independent and task_has_pr_review_target(task):
            raise SystemExit(
                f"{task_id} is PR-backed but approve has no exact reviewed-head "
                "binding. Set REVIEW_PR and REVIEW_HEAD_SHA; internal activity "
                "approval alone is not a GitHub review gate."
            )
        if independent and warn_if_unbound:
            print(
                f"warning: {task_id} is approved without a reviewed-head binding. "
                "If this task has a PR, the review-before-merge gate will refuse to "
                "merge it (approval_head_binding_missing). Re-approve with "
                "REVIEW_PR=<pr-number> and REVIEW_HEAD_SHA=<40-hex head oid> "
                f"(optionally REVIEW_BASE, default {DEFAULT_APPROVAL_BASE_BRANCH!r}).",
                file=sys.stderr,
            )
        return {}

    if not raw_pr:
        raise SystemExit(
            "REVIEW_HEAD_SHA was supplied without REVIEW_PR; both are required."
        )
    if not raw_head:
        raise SystemExit(
            "REVIEW_PR was supplied without REVIEW_HEAD_SHA; both are required."
        )
    if not raw_pr.isdigit() or int(raw_pr) <= 0:
        raise SystemExit(f"REVIEW_PR must be a positive PR number, got {raw_pr!r}")
    if not APPROVAL_HEAD_SHA_RE.match(raw_head):
        raise SystemExit(
            f"REVIEW_HEAD_SHA must be a full 40-hex commit oid, got {raw_head!r}. "
            "An abbreviated sha cannot be compared exactly."
        )

    return {
        "pr": int(raw_pr),
        "head_sha": raw_head.lower(),
        "head_branch": head_branch,
        "base": base_branch,
    }


def task_has_pr_review_target(task: Mapping[str, Any]) -> bool:
    """Return whether durable task metadata identifies a delivery PR."""

    if isinstance(task.get(APPROVAL_BINDING_KEY), Mapping):
        return True
    for key in ("source_ref", "github"):
        payload = task.get(key)
        if not isinstance(payload, Mapping):
            continue
        raw_pr = payload.get("pr") or payload.get("pull_request")
        if isinstance(raw_pr, int) and raw_pr > 0:
            return True
        text = str(raw_pr or "").strip().lstrip("#")
        if text.isdigit() and int(text) > 0:
            return True
    return False


def review_evidence_file_committed(
    *, repository: str, head_sha: str, review_file: str
) -> bool:
    """Return whether `review_file` exists as a real file at `head_sha` on GitHub.

    Queries the GitHub Contents API directly against the exact commit rather
    than local git objects, because the head being checked (an approved PR
    head, or the head at approval time) is not guaranteed to be fetched into
    the local checkout at command-run time.

    SUP-REVIEW-EVIDENCE-BINDING-ENFORCEMENT-20260804: this is what makes the
    "owner may bind the same already committed and reviewed manifest" fallback
    in task-closeout-finalization.md actually true instead of merely
    documented. Without it, `done` accepted any REVIEW_FILE string at face
    value, including one that only exists in a commit added *after* approval
    -- exactly the SHA-shifting, re-review-forcing loop diagnosed in
    SUP-REVIEW-PIPELINE-INTEGRITY-20260804.
    """
    review_file = (review_file or "").strip().lstrip("/")
    if not repository or not head_sha or not review_file:
        return False
    encoded_path = urllib.parse.quote(review_file, safe="/")
    result = run_gh_json_command(
        ["api", f"repos/{repository}/contents/{encoded_path}", "-f", f"ref={head_sha}"]
    )
    return isinstance(result, Mapping) and str(result.get("type") or "") == "file"


def bridge_github_review_decision(
    task: dict[str, Any],
    *,
    actor: str,
    decision: str,
    message: str,
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Represent an exact-head governed verdict on the delivery PR.

    The bridge runs before canonical state changes.  A failed GitHub write
    therefore leaves the task in its prior lifecycle state instead of
    manufacturing an internal-only approval.
    """

    scripts_git = ROOT / "scripts" / "git"
    if str(scripts_git) not in sys.path:
        sys.path.insert(0, str(scripts_git))
    try:
        import github_review_bridge
    except ImportError as exc:  # pragma: no cover - deployment packaging guard
        raise SystemExit(
            "GitHub review bridge is unavailable; refusing an internal-only "
            f"{decision} decision for {task.get('id') or '?'}."
        ) from exc

    config = load_config()
    repository_id = task_primary_repository_id(config, task)
    repository_slug_value = repository_slug(config, repository_id)
    if repository_id is None or not repository_slug_value:
        raise SystemExit(
            "GitHub review bridge requires one delivery repository with a "
            f"configured GitHub slug for {task.get('id') or '?'}."
        )
    try:
        result = github_review_bridge.bridge_review_decision(
            repository=repository_slug_value,
            task_id=str(task.get("id") or ""),
            actor=actor,
            decision=decision,
            message=message,
            binding=binding,
        )
    except github_review_bridge.GitHubReviewBridgeError as exc:
        raise SystemExit(
            f"GitHub review bridge rejected {decision} for "
            f"{task.get('id') or '?'}: {exc}"
        ) from exc
    payload = result.as_dict()
    if not isinstance(payload, dict):
        raise SystemExit("GitHub review bridge returned invalid evidence")
    return payload


def github_review_bridge_evidence_matches(task: Mapping[str, Any]) -> bool:
    """Return whether task evidence recognizes its exact approved PR head."""

    binding = task.get(APPROVAL_BINDING_KEY)
    evidence = task.get(GITHUB_REVIEW_BRIDGE_KEY)
    if not isinstance(binding, Mapping) or not isinstance(evidence, Mapping):
        return False
    if str(evidence.get("decision") or "").lower() != "approve":
        return False
    if str(evidence.get("mode") or "") not in GITHUB_REVIEW_MODES:
        return False
    try:
        if int(evidence.get("pr") or 0) != int(binding.get("pr") or 0):
            return False
    except (TypeError, ValueError):
        return False
    for key in ("head_sha", "head_branch", "base"):
        if str(evidence.get(key) or "").strip() != str(binding.get(key) or "").strip():
            return False

    mode = str(evidence.get("mode") or "")
    review_recorded = bool(evidence.get("github_review_id"))
    required_status_recorded = bool(
        evidence.get("status_id")
        and evidence.get("status_context") == GITHUB_CANONICAL_REVIEW_CONTEXT
        and str(evidence.get("status_state") or "").lower() == "success"
    )
    if mode == "pull_request_review":
        return review_recorded
    if mode == "required_commit_status":
        return required_status_recorded
    return review_recorded and required_status_recorded


def command_approve(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: approve <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor()
    ensure_agent(actor)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("reviewer") != actor:
        raise SystemExit(f"Only the reviewer ({task.get('reviewer')}) can approve {task_id}")
    if task.get("status") != "review":
        raise SystemExit(f"{task_id} must be in review before it can move to review_approved")

    review_notes = parse_delimited_env("REVIEW_NOTES_ZH")
    review_file = os.environ.get("REVIEW_FILE", "").strip()
    binding = resolve_approval_binding(task)
    if review_file and binding:
        config = load_config()
        repository_id = task_primary_repository_id(config, task)
        repository_slug_value = repository_slug(config, repository_id)
        if not repository_slug_value or not review_evidence_file_committed(
            repository=repository_slug_value,
            head_sha=binding["head_sha"],
            review_file=review_file,
        ):
            raise SystemExit(
                f"{task_id}: REVIEW_FILE={review_file!r} was not found at the reviewed "
                f"head {binding['head_sha'][:12]} in {repository_slug_value or '?'}. "
                "The evidence manifest must already be committed and present in the PR "
                "diff before approval."
            )
    transition_candidate = dict(task)
    if review_notes:
        transition_candidate["review_notes_zh"] = review_notes
    if review_file:
        transition_candidate["review_file"] = review_file
    verdict_ref = validate_protected_closeout_transition(
        transition_candidate,
        transition="review_approved",
    )
    github_review_bridge = (
        bridge_github_review_decision(
            task,
            actor=actor,
            decision="approve",
            message=message,
            binding=binding,
        )
        if binding
        else {}
    )

    timestamp = iso_now()
    task["status"] = "review_approved"
    task["last_update"] = timestamp
    task["next"] = message
    task.pop("waiting_for", None)
    if review_notes:
        task["review_notes_zh"] = review_notes
    if review_file:
        task["review_file"] = review_file
    if binding:
        task[APPROVAL_BINDING_KEY] = dict(binding)
    else:
        task.pop(APPROVAL_BINDING_KEY, None)
    if github_review_bridge:
        task[GITHUB_REVIEW_BRIDGE_KEY] = dict(github_review_bridge)
    else:
        task.pop(GITHUB_REVIEW_BRIDGE_KEY, None)
    if verdict_ref is not None:
        task["protected_closeout_verdict"] = verdict_ref
    mark_blockers_resolved(state, task_id)
    mark_handoffs_done_for_actor(state, task_id, actor)
    ensure_review_finalize_handoff(
        state,
        task,
        from_agent=actor,
        timestamp=timestamp,
        message=message,
    )
    append_log(
        {
            "ts": timestamp,
            "agent": actor,
            "type": "review_approved",
            "task_id": task_id,
            "message": message,
            # The audit event is the immutable copy the merge gate reads; the
            # task row copy is a convenience for `show`.
            **({APPROVAL_BINDING_KEY: dict(binding)} if binding else {}),
            **(
                {GITHUB_REVIEW_BRIDGE_KEY: dict(github_review_bridge)}
                if github_review_bridge
                else {}
            ),
        }
    )


def command_sync(state: dict[str, Any], _args: list[str]) -> None:
    return None



def command_archive_migrate(state: dict[str, Any], _args: list[str]) -> None:
    archived_at = iso_now()
    archived_ids = archive_terminal_tasks_in_state(state, archived_at=archived_at)
    append_log(
        {
            "ts": archived_at,
            "agent": current_actor(),
            "type": "archive_migrate",
            "message": f"Archived {len(archived_ids)} terminal tasks from ai-status.json.",
            "task_ids": archived_ids,
        }
    )


def validate_archive_review_file_target(task_id: str, review_file: str) -> tuple[str, str]:
    normalized = task_archive_module.normalize_archive_review_file(review_file)
    candidate = ROOT / normalized
    symlink_component = _first_symlink_component(candidate)
    if symlink_component is not None:
        raise SystemExit(
            f"Archive review_file target cannot include a symlink component: "
            f"{symlink_component}"
        )
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(ROOT.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(
            f"Archive review_file target must be a regular file inside the command root: "
            f"{normalized}"
        ) from exc
    if not resolved.is_file():
        raise SystemExit(
            f"Archive review_file target must be a regular file: {normalized}"
        )
    try:
        evidence = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"Archive review_file target is not readable JSON: {normalized}: {exc}"
        ) from exc
    evidence_task = evidence.get("task") if isinstance(evidence, dict) else None
    if not isinstance(evidence_task, dict):
        raise SystemExit(
            f"Archive review_file target is missing a task object: {normalized}"
        )
    if str(evidence_task.get("id") or "").strip() != task_id:
        raise SystemExit(
            f"Archive review_file target task.id does not match {task_id}: {normalized}"
        )
    if str(evidence_task.get("review_file") or "").strip() != normalized:
        raise SystemExit(
            f"Archive review_file target task.review_file does not match {normalized}"
        )

    snapshot = task_archive_module.load_archived_snapshot(task_id)
    if snapshot is None:
        raise SystemExit(f"Unknown archived task: {task_id}")
    candidate_task = deepcopy(snapshot["task"])
    candidate_task["review_file"] = normalized
    try:
        validate_loop_completion_claim(candidate_task)
    except SystemExit as exc:
        raise SystemExit(
            f"Archive review_file target fails loop completion validation: {exc}"
        ) from exc
    return normalized, hashlib.sha256(resolved.read_bytes()).hexdigest()


def command_archive_correct_review_file(
    state: dict[str, Any],
    args: list[str],
) -> None:
    if len(args) < 3:
        raise SystemExit(
            "Usage: archive_correct_review_file "
            "<task-id> <repo-relative-review-file> <reason>"
        )
    task_id, review_file, reason = args[0], args[1], args[2]
    actor = current_actor()
    ensure_agent(actor)
    if actor != "Human/Ops":
        raise SystemExit(
            "Only Human/Ops can correct an archived task review_file"
        )
    if get_task(state, task_id) is not None:
        raise SystemExit(
            f"Cannot correct archive review_file while {task_id} is active"
        )
    normalized, digest = validate_archive_review_file_target(task_id, review_file)
    corrected = task_archive_module.correct_archived_task_review_file(
        task_id,
        normalized,
        actor=actor,
        reason=reason,
        evidence_sha256=digest,
        canonical_lock_held=True,
    )
    context = corrected["correction_context"]
    append_log(
        {
            "ts": context["corrected_at"],
            "agent": actor,
            "type": "archive_review_file_corrected",
            "task_id": task_id,
            "message": reason,
            "review_file": normalized,
            "evidence_sha256": digest,
        }
    )


def _normalized_repo_relative_json_path(raw: str, *, label: str) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.suffix != ".json"
    ):
        raise SystemExit(f"{label} must be a normalized repository-relative JSON path")
    return path.as_posix()


def _active_task_ancestors(
    state: Mapping[str, Any],
    task_id: str,
    *,
    visiting: set[str] | None = None,
) -> set[str]:
    tasks = {
        str(task.get("id") or ""): task
        for task in state.get("tasks", [])
        if isinstance(task, Mapping) and str(task.get("id") or "")
    }
    if task_id not in tasks:
        raise SystemExit(f"Proof ownership references inactive task: {task_id}")
    visiting = set(visiting or ())
    if task_id in visiting:
        raise SystemExit(f"Proof ownership dependency cycle includes {task_id}")
    visiting.add(task_id)
    ancestors: set[str] = set()
    for dependency_id in tasks[task_id].get("depends_on", []) or []:
        dependency_id = str(dependency_id or "").strip()
        if not dependency_id:
            continue
        ancestors.add(dependency_id)
        if dependency_id in tasks:
            ancestors.update(
                _active_task_ancestors(
                    state,
                    dependency_id,
                    visiting=visiting,
                )
            )
    return ancestors


def validate_active_proof_ownership(
    state: dict[str, Any],
    task_id: str,
    proof_ownership_file: str,
) -> dict[str, Any]:
    normalized = _normalized_repo_relative_json_path(
        proof_ownership_file,
        label="proof ownership file",
    )
    candidate = ROOT / normalized
    symlink_component = _first_symlink_component(candidate)
    if symlink_component is not None:
        raise SystemExit(
            f"Proof ownership file cannot include a symlink component: "
            f"{symlink_component}"
        )
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(ROOT.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(
            f"Proof ownership file must be inside the command root: {normalized}"
        ) from exc
    if not resolved.is_file():
        raise SystemExit(f"Proof ownership file must be a regular file: {normalized}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"Proof ownership file is not readable JSON: {normalized}: {exc}"
        ) from exc
    required = {
        "schema_version",
        "program_id",
        "base_catalog_sha256",
        "generated_at",
        "delegations",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise SystemExit("Proof ownership root contract is not exact")
    if payload.get("schema_version") != 1:
        raise SystemExit("Proof ownership schema_version must be 1")
    program_id = str(payload.get("program_id") or "").strip()
    base_catalog_sha256 = str(payload.get("base_catalog_sha256") or "").strip()
    if not program_id or not re.fullmatch(r"[0-9a-f]{64}", base_catalog_sha256):
        raise SystemExit("Proof ownership identity is invalid")

    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown active task: {task_id}")
    guard = _validated_artifact_conflict_guard(task)
    if (
        str(task.get("program_id") or "").strip() != program_id
        or guard is None
        or guard["program_id"] != program_id
        or guard["catalog_sha256"] != base_catalog_sha256
    ):
        raise SystemExit(
            f"Proof ownership base catalog does not match active task {task_id}"
        )

    raw_delegations = payload.get("delegations")
    if not isinstance(raw_delegations, list) or not raw_delegations:
        raise SystemExit("Proof ownership delegations must be a non-empty list")
    delegation_fields = {
        "source_task_id",
        "proof",
        "owner_task_id",
        "final_witness_task_id",
        "reason",
    }
    task_delegations: list[dict[str, str]] = []
    identities: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_delegations):
        if not isinstance(raw, dict) or set(raw) != delegation_fields:
            raise SystemExit(
                f"Proof ownership delegation {index} contract is not exact"
            )
        delegation = {
            key: str(raw.get(key) or "").strip()
            for key in sorted(delegation_fields)
        }
        if any(not value for value in delegation.values()):
            raise SystemExit(
                f"Proof ownership delegation {index} contains an empty field"
            )
        identity = (
            delegation["source_task_id"],
            delegation["proof"],
        )
        if identity in identities:
            raise SystemExit("Proof ownership contains a duplicate delegation")
        identities.add(identity)
        if delegation["source_task_id"] != task_id:
            continue
        if delegation["proof"] not in (task.get("proof_required") or []):
            raise SystemExit(
                f"Delegated proof is not required by active task {task_id}"
            )
        owner_id = delegation["owner_task_id"]
        witness_id = delegation["final_witness_task_id"]
        if task_id not in _active_task_ancestors(state, owner_id):
            raise SystemExit(
                f"Proof owner {owner_id} is not a descendant of {task_id}"
            )
        if (
            owner_id != witness_id
            and owner_id not in _active_task_ancestors(state, witness_id)
        ):
            raise SystemExit(
                f"Proof witness {witness_id} is not a descendant of {owner_id}"
            )
        task_delegations.append(delegation)
    if not task_delegations:
        raise SystemExit(
            f"Proof ownership file has no delegation for active task {task_id}"
        )

    return {
        "schema_version": 1,
        "program_id": program_id,
        "base_catalog_sha256": base_catalog_sha256,
        "proof_ownership_file": normalized,
        "proof_ownership_sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "delegations": task_delegations,
    }


def command_attach_proof_ownership(
    state: dict[str, Any],
    args: list[str],
) -> None:
    if len(args) < 3:
        raise SystemExit(
            "Usage: attach_proof_ownership "
            "<task-id> <repo-relative-proof-ownership-file> <reason>"
        )
    task_id, proof_ownership_file, reason = args[0], args[1], args[2]
    actor = current_actor()
    ensure_agent(actor)
    if actor != "Human/Ops":
        raise SystemExit(
            "Only Human/Ops can attach program proof ownership"
        )
    context = validate_active_proof_ownership(
        state,
        task_id,
        proof_ownership_file,
    )
    task = get_task(state, task_id)
    assert task is not None
    immutable = {
        key: context[key]
        for key in (
            "schema_version",
            "program_id",
            "base_catalog_sha256",
            "proof_ownership_file",
            "proof_ownership_sha256",
            "delegations",
        )
    }
    existing = task.get("proof_ownership")
    if existing is not None:
        existing_immutable = {
            key: existing.get(key)
            for key in immutable
        } if isinstance(existing, dict) else {}
        if existing_immutable != immutable:
            raise SystemExit(
                f"Active task {task_id} already has different proof ownership"
            )
    timestamp = iso_now()
    task["proof_ownership"] = {
        **immutable,
        "attached_by": actor,
        "attached_at": (
            existing.get("attached_at")
            if isinstance(existing, dict) and existing.get("attached_at")
            else timestamp
        ),
        "reason": reason,
    }
    task["last_update"] = timestamp
    task["next"] = (
        f"{reason} Delegated proof remains required from "
        + ", ".join(
            delegation["owner_task_id"]
            for delegation in context["delegations"]
        )
        + "; current task review must not claim that delegated proof as locally or "
        "hosted-complete."
    )
    append_log(
        {
            "ts": timestamp,
            "agent": actor,
            "type": "program_proof_ownership_attached",
            "task_id": task_id,
            "message": reason,
            "proof_ownership_file": context["proof_ownership_file"],
            "proof_ownership_sha256": context["proof_ownership_sha256"],
            "delegations": context["delegations"],
        }
    )


def command_prompt(state: dict[str, Any], _args: list[str]) -> None:
    print(build_onboarding_prompt(state))


def command_show(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 1:
        raise SystemExit("Usage: show <task-id>")
    task_id = args[0]
    active_task = get_task(state, task_id)
    if active_task is not None:
        print(
            json.dumps(
                {
                    "source": "active",
                    "task": active_task,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    snapshot = archived_task_snapshot(task_id)
    if snapshot is None:
        raise SystemExit(f"Unknown task: {task_id}")
    print(
        json.dumps(
            {
                "source": "archive",
                "snapshot_path": archive_display_path(archive_task_path(task_id)),
                "snapshot": snapshot,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def _emit_fail_closed(error: ActivityAuditInvariantError) -> None:
    print(
        json.dumps(
            {
                "status": "fail_closed",
                "diagnostic": error.diagnostic,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )


def command_wave(state: dict[str, Any], args: list[str]) -> None:
    """wave open <wave-id> | wave close | wave freeze"""
    from wave_guards import (  # lazy: only needed for this command
        WaveGuardError,
        check_wave_close,
        check_wave_freeze,
        check_wave_open,
    )

    if not args:
        raise SystemExit("Usage: wave <open <wave-id> | close | freeze>")

    subcommand = args[0]
    actor = current_actor()
    timestamp = iso_now()
    wave_state: dict[str, Any] = state.setdefault("wave_state", {})
    planning_state = load_planning_state()

    if subcommand == "open":
        if len(args) < 2:
            raise SystemExit("Usage: wave open <wave-id>")
        new_wave_id = args[1]
        try:
            check_wave_open(wave_state, new_wave_id, actor, planning_state)
        except WaveGuardError as exc:
            raise SystemExit(f"Wave guard rejected open: {exc}") from exc
        wave_state["current_wave_id"] = new_wave_id
        wave_state["status"] = "open"
        wave_state["opened_at"] = timestamp
        wave_state["frozen_at"] = None
        wave_state["closed_at"] = None
        wave_state["branch"] = f"wave/{new_wave_id}"
        wave_state.setdefault("history", []).append(
            {"ts": timestamp, "event": "open", "wave_id": new_wave_id, "actor": actor, "branch": f"wave/{new_wave_id}"}
        )
        append_log({"ts": timestamp, "agent": actor, "type": "wave_open", "wave_id": new_wave_id})

    elif subcommand == "close":
        try:
            check_wave_close(wave_state, actor, planning_state)
        except WaveGuardError as exc:
            raise SystemExit(f"Wave guard rejected close: {exc}") from exc
        current_wave_id = wave_state.get("current_wave_id", "")
        wave_state["status"] = "closed"
        wave_state["closed_at"] = timestamp
        wave_state.setdefault("history", []).append(
            {"ts": timestamp, "event": "close", "wave_id": current_wave_id, "actor": actor}
        )
        append_log({"ts": timestamp, "agent": actor, "type": "wave_close", "wave_id": current_wave_id})

    elif subcommand == "freeze":
        try:
            check_wave_freeze(wave_state, actor, planning_state)
        except WaveGuardError as exc:
            raise SystemExit(f"Wave guard rejected freeze: {exc}") from exc
        current_wave_id = wave_state.get("current_wave_id", "")
        wave_state["status"] = "frozen"
        wave_state["frozen_at"] = timestamp
        wave_state.setdefault("history", []).append(
            {"ts": timestamp, "event": "freeze", "wave_id": current_wave_id, "actor": actor}
        )
        append_log({"ts": timestamp, "agent": actor, "type": "wave_freeze", "wave_id": current_wave_id})

    else:
        raise SystemExit(f"Unknown wave subcommand: {subcommand!r}. Use: open <wave-id>, close, freeze")


def load_supervisor_dispatch_batch(path_value: str) -> list[dict[str, Any]]:
    """Load one bounded, exact dispatch mutation packet from a regular file."""

    if not path_value:
        raise SystemExit(
            f"Usage: {SUPERVISOR_DISPATCH_BATCH_COMMAND} <absolute-payload-path>"
        )
    path = Path(os.path.expanduser(path_value))
    if not path.is_absolute():
        raise SystemExit("Supervisor dispatch batch payload path must be absolute")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SystemExit(f"Unable to inspect supervisor dispatch batch payload: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise SystemExit("Supervisor dispatch batch payload must be a non-symlink regular file")
    if metadata.st_size > 1024 * 1024:
        raise SystemExit("Supervisor dispatch batch payload exceeds 1 MiB")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Invalid supervisor dispatch batch payload: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "mutations"}:
        raise SystemExit("Supervisor dispatch batch payload schema is not exact")
    if payload.get("schema_version") != SUPERVISOR_DISPATCH_BATCH_SCHEMA_VERSION:
        raise SystemExit("Unsupported supervisor dispatch batch schema version")
    mutations = payload.get("mutations")
    if (
        not isinstance(mutations, list)
        or not mutations
        or len(mutations) > SUPERVISOR_DISPATCH_BATCH_MAX_MUTATIONS
    ):
        raise SystemExit(
            "Supervisor dispatch batch mutations must contain between 1 and "
            f"{SUPERVISOR_DISPATCH_BATCH_MAX_MUTATIONS} rows"
        )

    allowed_commands = {"start", "progress", "note"}
    normalized: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    exact_keys = {
        "actor",
        "command",
        "expected_statuses",
        "message",
        "run_id",
        "task_id",
        "workspace_path",
    }
    for index, mutation in enumerate(mutations):
        if not isinstance(mutation, dict) or set(mutation) != exact_keys:
            raise SystemExit(f"Supervisor dispatch batch row {index} schema is not exact")
        row = {key: mutation.get(key) for key in exact_keys}
        for field, limit in (
            ("actor", 80),
            ("command", 32),
            ("message", 4096),
            ("run_id", 256),
            ("task_id", 256),
            ("workspace_path", 4096),
        ):
            value = row[field]
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise SystemExit(
                    f"Supervisor dispatch batch row {index} has invalid {field}"
                )
            row[field] = value.strip()
        if row["command"] not in allowed_commands:
            raise SystemExit(
                f"Supervisor dispatch batch row {index} command is not dispatch-safe"
            )
        expected_statuses = row["expected_statuses"]
        if (
            not isinstance(expected_statuses, list)
            or not expected_statuses
            or len(expected_statuses) > 4
            or any(
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 64
                for value in expected_statuses
            )
        ):
            raise SystemExit(
                f"Supervisor dispatch batch row {index} has invalid expected_statuses"
            )
        row["expected_statuses"] = sorted(
            {str(value).strip().lower() for value in expected_statuses}
        )
        task_id = str(row["task_id"])
        if task_id in seen_task_ids:
            raise SystemExit(
                f"Supervisor dispatch batch repeats task mutation: {task_id}"
            )
        seen_task_ids.add(task_id)
        normalized.append(row)
    return normalized


@contextmanager
def supervisor_dispatch_mutation_environment(mutation: Mapping[str, Any]):
    """Bind one batch row to the exact worker lease it claims."""

    bindings = {
        "AI_NAME": str(mutation["actor"]),
        "ORCH_RUN_ID": str(mutation["run_id"]),
        "ORCH_TASK_ID": str(mutation["task_id"]),
        "PANTHEON_WORKTREE_ROOT": str(mutation["workspace_path"]),
        "ORCH_WORKSPACE_PATH": str(mutation["workspace_path"]),
    }
    previous = {name: os.environ.get(name) for name in bindings}
    try:
        os.environ.update(bindings)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        _clear_status_command_lease_binding()


def run_supervisor_dispatch_batch(
    state: dict[str, Any],
    mutations: list[dict[str, Any]],
    *,
    commands: Mapping[str, Any],
    runtime_snapshot: Mapping[str, Any],
    config: dict[str, Any],
) -> None:
    """Apply dispatch status CAS rows to one in-memory canonical snapshot."""

    for mutation in mutations:
        task_id = str(mutation["task_id"])
        actor = str(mutation["actor"])
        command = str(mutation["command"])
        task = get_task(state, task_id)
        if task is None:
            raise RuntimeError(f"Supervisor dispatch batch task is missing: {task_id}")
        if str(task.get("owner") or "").strip() != actor:
            raise RuntimeError(
                f"Supervisor dispatch batch owner CAS failed for {task_id}: "
                f"{task.get('owner')} != {actor}"
            )
        current_status = str(task.get("status") or "").strip().lower()
        if current_status not in set(mutation["expected_statuses"]):
            raise RuntimeError(
                f"Supervisor dispatch batch status CAS failed for {task_id}: "
                f"{current_status or 'missing'} not in {mutation['expected_statuses']}"
            )
        command_args = [task_id, str(mutation["message"])]
        with supervisor_dispatch_mutation_environment(mutation):
            validate_active_status_command_lease(
                command,
                command_args,
                runtime_state_snapshot=runtime_snapshot,
                config_snapshot=config,
            )
            commands[command](state, command_args)


def main(argv: list[str]) -> int:
    validate_status_command_runtime_binding()
    validate_status_root_binding()

    command = argv[1] if len(argv) > 1 else "sync"
    args = argv[2:]

    read_only_commands = {
        "prompt": command_prompt,
        "show": command_show,
    }

    commands = {
        "assign": command_assign,
        "start": command_start,
        "progress": command_progress,
        "note": command_note,
        "reopen": command_reopen,
        "handoff": command_handoff,
        "blocker": command_blocker,
        "done": command_done,
        "restore_approved": command_restore_approved,
        "reconcile_merged_done": command_reconcile_merged_done,
        "supersede": command_supersede,
        "approve": command_approve,
        "archive_migrate": command_archive_migrate,
        "archive_correct_review_file": command_archive_correct_review_file,
        "attach_proof_ownership": command_attach_proof_ownership,
        "sync": command_sync,
        "wave": command_wave,
    }

    if command == SUPERVISOR_DISPATCH_BATCH_COMMAND:
        if len(args) != 1:
            raise SystemExit(
                f"Usage: {SUPERVISOR_DISPATCH_BATCH_COMMAND} <absolute-payload-path>"
            )
        # Payload/config reads and command-runtime git validation happen before
        # the global lock order begins.  The transaction below is strictly:
        # runtime_admission(shared) -> task_state(exclusive) -> activity_audit.
        mutations = load_supervisor_dispatch_batch(args[0])
        config = load_config()
        committed_state: dict[str, Any] | None = None
        with runtime_state_lock(config, shared=True):
            runtime_snapshot = load_runtime_state_snapshot(config)
            with canonical_task_state_lock(shared=False):
                with authoritative_task_state_transaction():
                    state = load_state()
                    recover_status_archive_outbox(state)
                    recover_status_activity_outbox(state)
                    with buffer_activity_events():
                        run_supervisor_dispatch_batch(
                            state,
                            mutations,
                            commands=commands,
                            runtime_snapshot=runtime_snapshot,
                            config=config,
                        )
                        sync_all(state, refresh_views=False)
                    committed_state = deepcopy(state)
        if committed_state is not None:
            refresh_derived_status_views_if_current(committed_state)
        print(
            json.dumps(
                {
                    "status": "committed",
                    "mutation_count": len(mutations),
                    "task_ids": [mutation["task_id"] for mutation in mutations],
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
        return 0

    if command == "recover":
        if args:
            raise SystemExit("Usage: recover")
        try:
            with canonical_task_state_lock(shared=False, nonblocking=True):
                with authoritative_task_state_transaction():
                    state = load_state()
                    pending_planes = [
                        key
                        for key in (
                            STATUS_ARCHIVE_OUTBOX_KEY,
                            STATUS_ACTIVITY_OUTBOX_KEY,
                        )
                        if state.get(key) not in (None, {}, [])
                    ]
                    try:
                        archive_recovered = recover_status_archive_outbox(state)
                        activity_recovered = recover_status_activity_outbox(state)
                        if archive_recovered or activity_recovered:
                            refresh_derived_status_views(state)
                    except ActivityAuditInvariantError:
                        raise
                    except RuntimeError as exc:
                        raise ActivityAuditInvariantError(
                            "canonical status recovery failed integrity checks",
                            invariant="status_recovery_integrity",
                            evidence={
                                "command": command,
                                "pending_planes": pending_planes,
                                "error": str(exc),
                            },
                        ) from exc
        except BlockingIOError:
            _emit_fail_closed(
                ActivityAuditInvariantError(
                    "canonical task-state lock is busy",
                    invariant="status_task_lock_busy",
                    evidence={
                        "command": command,
                        "lock_path": str(canonical_task_state_lock_path(STATUS_FILE)),
                    },
                )
            )
            return 75
        except ActivityAuditInvariantError as exc:
            _emit_fail_closed(exc)
            return 2
        print(
            json.dumps(
                {
                    "status": (
                        "recovered"
                        if archive_recovered or activity_recovered
                        else "no_pending_recovery"
                    ),
                    "archive_outbox_recovered": archive_recovered,
                    "activity_outbox_recovered": activity_recovered,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
        return 0

    if command in read_only_commands:
        # Read-only commands must never join the writer convoy or mutate
        # recovery state. Writers/supervisor own outbox recovery; a reader
        # reports a bounded fail-closed diagnostic instead.
        try:
            with canonical_task_state_lock(shared=True, nonblocking=True):
                state = load_state()
                pending_planes = [
                    key
                    for key in (
                        STATUS_ARCHIVE_OUTBOX_KEY,
                        STATUS_ACTIVITY_OUTBOX_KEY,
                    )
                    if state.get(key) not in (None, {}, [])
                ]
                if pending_planes:
                    raise ActivityAuditInvariantError(
                        "canonical status recovery is pending",
                        invariant="status_recovery_pending",
                        evidence={"pending_planes": pending_planes},
                    )
                read_only_commands[command](state, args)
        except BlockingIOError as exc:
            _emit_fail_closed(
                ActivityAuditInvariantError(
                    "canonical task-state lock is busy",
                    invariant="status_task_lock_busy",
                    evidence={
                        "command": command,
                        "lock_path": str(canonical_task_state_lock_path(STATUS_FILE)),
                    },
                )
            )
            return 75
        except ActivityAuditInvariantError as exc:
            _emit_fail_closed(exc)
            return 2
        return 0

    if command not in commands:
        raise SystemExit(f"Unknown command: {command}")

    def run_mutation() -> dict[str, Any] | None:
        state = load_state()
        recover_status_archive_outbox(state)
        recover_status_activity_outbox(state)
        with buffer_activity_events():
            command_result = commands[command](state, args)
            if command_result is False:
                return None
            sync_all(state, refresh_views=False)
        return deepcopy(state)

    committed_state: dict[str, Any] | None
    if str(os.environ.get("ORCH_RUN_ID") or "").strip():
        config = load_config()
        with runtime_state_lock(config, shared=True):
            validate_active_status_command_lease(command, args)
            with canonical_task_state_lock(shared=False):
                with authoritative_task_state_transaction():
                    committed_state = run_mutation()
    else:
        with canonical_task_state_lock(shared=False):
            validate_active_status_command_lease(command, args)
            with authoritative_task_state_transaction():
                committed_state = run_mutation()
    if committed_state is not None:
        refresh_derived_status_views_if_current(committed_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
