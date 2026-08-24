#!/usr/bin/env python3
from __future__ import annotations

import gzip
import base64
import binascii
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
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from threading import local
from typing import Any, Generator, Mapping
from zoneinfo import ZoneInfo

# Status-command integrity validation runs after repo-local imports. Prevent
# those imports from creating an untracked __pycache__ that would make the
# command runtime fail its own dirty executable/import check.
sys.dont_write_bytecode = True

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[1]
STATUS_ROOT_ENV = "PANTHEON_STATUS_ROOT"
STATUS_COMMAND_ROOT_ENV = "PANTHEON_COMMAND_ROOT"
STATUS_COMMAND_SHA_ENV = "PANTHEON_COMMAND_RUNTIME_SHA"
STATUS_COMMAND_REMOTE_ENV = "PANTHEON_COMMAND_REMOTE"
STATUS_COMMAND_BASE_REF_ENV = "PANTHEON_COMMAND_BASE_REF"
TASK_STATE_STORE_MODE_ENV = "PANTHEON_TASK_STATE_STORE_MODE"
TASK_STATE_EVENT_LOG_ENV = "PANTHEON_TASK_STATE_EVENT_LOG"
STATUS_OUTBOX_VISIBILITY_ENABLED_ENV = "PANTHEON_STATUS_OUTBOX_VISIBILITY_ENABLED"
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
)
from multi_repo_registry import (
    artifact_explicit_repository_id,
    artifact_repository_id,
    repository_configured_local_path,
    repository_local_path,
    repository_relative_artifact_path,
    repository_slug,
    resolve_repository,
    task_artifact_repository_ids,
    task_target_repository_id,
    validate_task_repository_scope,
)
from runtime_state import (
    activity_audit_lock_file,
    canonical_task_state_lock_file,
    load_runtime_state_snapshot,
    runtime_state_lock,
)
from rewrite.task_state_store import (
    append_state_commit,
    load_snapshot,
    snapshot_transaction,
)
from rewrite import task_machine, task_state_store
from common import (
    ActivityAuditInvariantError,
    CANONICAL_TASK_STATE_IDENTITY_ENV,
    DuplicateActivityJSONKeyError,
    WORKER_PROCESS_GENERATION_PREFIX,
    WORKER_PROCESS_GENERATION_SCHEMA_VERSION,
    activity_audit_invariant_error,
    activity_audit_lock_path,
    activity_audit_source_paths_unlocked,
    append_activity_log_entries_unlocked,
    canonical_task_state_lock_path,
    durable_write_bytes,
    first_symlink_component,
    git_toplevel,
    normalize_github_repo_slug,
    prepare_activity_audit_unlocked,
    read_activity_log_tail_bytes,
    read_regular_file_bytes,
    strict_activity_json_loads,
    utc_now as iso_now,
    canonical_task_state_identity_from_environment,
    validate_status_command_runtime,
    validated_activity_event_digests_unlocked,
    worker_process_generation_id,
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
STATUS_ARCHIVE_OUTBOX_SCHEMA_VERSION = task_archive_module.STATUS_ARCHIVE_OUTBOX_SCHEMA_VERSION
TERMINAL_FACTS_KEY = "terminal_facts"
ARCHIVE_RECEIPTS_KEY = "archive_receipts"
ARCHIVE_RECEIPT_SCHEMA_VERSION = 1
SUPERVISOR_DISPATCH_BATCH_SCHEMA_VERSION = 1
SUPERVISOR_DISPATCH_BATCH_MAX_MUTATIONS = 64
SUPERVISOR_DISPATCH_BATCH_COMMAND = "supervisor-dispatch-batch"
DEV_BRIDGE_BATCH_SCHEMA_VERSION = 1
DEV_BRIDGE_BATCH_MAX_TASKS = 64
DEV_BRIDGE_BATCH_MATERIALIZE_COMMAND = "dev-bridge-materialize-batch"
DEV_BRIDGE_BATCH_READBACK_COMMAND = "dev-bridge-materialize-readback"
DEV_BRIDGE_BATCH_ACTOR = "assistant.dev.source"
GLOBAL_STATUS_LOCK_ORDER = (
    "runtime_admission",
    "task_state",
    "activity_audit",
)
_ACTIVITY_TRANSACTION_LOCAL = local()
_TASK_STATE_TRANSACTION_LOCAL = local()
_STATUS_COMMAND_LEASE_LOCAL = local()
_DEV_BRIDGE_MATERIALIZATION_LOCAL = local()
_EXTERNAL_MUTATION_PREFLIGHT_LOCAL = local()
LOCAL_HUMAN_OPS_ENV = "PANTHEON_LOCAL_HUMAN_OPS"
LOCAL_HUMAN_OPS_ACTIONS = frozenset(
    {
        "assign",
        "reopen",
        "note",
        "reconcile_merged_done",
        "supersede",
        "retire_archive_collision",
        "sync",
        "archive_correct_review_file",
        "archive_reconcile",
        "record_terminal_fact",
    }
)
DEV_BRIDGE_CONSUMED_KEY = "consumed_dev_bridge_packets"
LEGACY_OPERATOR_ASSERTION_KEYS = (
    "consumed_operator_assertions",
    "consumed_canonical_mutation_assertions",
)
CURRENT_WORK_FILE = STATUS_ROOT / "current-work.md"
DOCS_SITE_DIR = STATUS_ROOT / "docs-site"
CONFIG_FILE = ROOT / ".orchestrator" / "config.json"
ORCHESTRATOR_STATE_FILE = STATUS_ROOT / ".orchestrator" / "state.json"
APPROVAL_QUEUE_FILE = STATUS_ROOT / ".orchestrator" / "approval-queue.json"
DASHBOARD_BUNDLE_FILE = STATUS_ROOT / "dashboard-bundle.json"


def configure_status_root_paths(status_root: str | Path) -> Path:
    """Bind every governed status/archive/audit path to one root."""

    global STATUS_ROOT
    global STATUS_FILE, LOG_FILE, CURRENT_WORK_FILE, DOCS_SITE_DIR
    global ORCHESTRATOR_STATE_FILE, APPROVAL_QUEUE_FILE
    global DASHBOARD_BUNDLE_FILE, ARCHIVE_TASKS_DIR

    root = Path(status_root).expanduser().resolve()
    STATUS_ROOT = root
    STATUS_FILE = root / "ai-status.json"
    LOG_FILE = root / "ai-activity-log.jsonl"
    CURRENT_WORK_FILE = root / "current-work.md"
    DOCS_SITE_DIR = root / "docs-site"
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
    env_names = ("PANTHEON_WORKTREE_ROOT", "ORCH_WORKSPACE_PATH")
    present: list[str] = []
    for env_name in env_names:
        raw = str(os.environ.get(env_name) or "").strip()
        if not raw:
            continue
        present.append(env_name)
        expanded = Path(os.path.expanduser(raw))
        if not expanded.is_absolute():
            raise RuntimeError(f"{env_name} must be an absolute path when set")
        symlink_component = first_symlink_component(expanded)
        if symlink_component is not None:
            raise RuntimeError(
                f"{env_name} cannot include a symlink component: {symlink_component}"
            )
        roots.append((env_name, expanded.resolve()))
    if not roots:
        return None
    if len(present) != len(env_names):
        missing = [name for name in env_names if name not in present]
        raise RuntimeError(
            "delivery workspace binding requires both PANTHEON_WORKTREE_ROOT and "
            f"ORCH_WORKSPACE_PATH; missing {', '.join(missing)}"
        )
    first_name, first_root = roots[0]
    for env_name, root in roots[1:]:
        if root != first_root:
            raise RuntimeError(
                f"{first_name} and {env_name} disagree on delivery worktree root: "
                f"{first_root} != {root}"
            )
    return first_root


def _status_root_from_runtime_path(raw: str, *, label: str) -> Path:
    path = Path(os.path.expanduser(raw))
    if not path.is_absolute():
        raise RuntimeError(f"{label} must be absolute when set")
    symlink_comp = first_symlink_component(path)
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


def _command_env(name: str, default: str = "") -> str:
    return str(os.environ.get(name) or default).strip()


def validate_status_command_runtime_binding() -> None:
    """Ensure auto-worker status commands run from the installed command root."""

    raw_root = _command_env(STATUS_COMMAND_ROOT_ENV)
    if not raw_root:
        if _auto_worker_requires_explicit_status_root():
            raise RuntimeError(
                "PANTHEON_COMMAND_ROOT is required for auto-worker status commands"
            )
        return

    expected_sha = _command_env(STATUS_COMMAND_SHA_ENV)
    if not expected_sha:
        raise RuntimeError("PANTHEON_COMMAND_RUNTIME_SHA is required for auto-worker status commands")

    expected_remote = _command_env(STATUS_COMMAND_REMOTE_ENV, "ajoe734/pantheon")
    base_ref = _command_env(STATUS_COMMAND_BASE_REF_ENV, "origin/dev") or "origin/dev"
    runtime = validate_status_command_runtime(
        Path(os.path.expanduser(raw_root)),
        expected_sha=expected_sha,
        expected_remote=expected_remote,
        base_ref=base_ref,
    )
    command_root = Path(runtime["root"])

    current_root = ROOT.resolve()
    if current_root != command_root:
        raise RuntimeError(
            "auto-worker status command must execute the installed command runtime: "
            f"running {current_root}, expected {command_root}"
        )

STATUS_WORKER_PROCESS_GENERATION_SCHEMA_VERSION = (
    WORKER_PROCESS_GENERATION_SCHEMA_VERSION
)
STATUS_WORKER_PROCESS_GENERATION_PREFIX = WORKER_PROCESS_GENERATION_PREFIX
status_worker_process_generation_id = worker_process_generation_id


def _clear_status_command_lease_binding() -> None:
    try:
        delattr(_STATUS_COMMAND_LEASE_LOCAL, "binding")
    except AttributeError:
        pass


def local_human_ops_requested() -> bool:
    if str(os.environ.get("ORCH_RUN_ID") or "").strip():
        return False
    return str(os.environ.get(LOCAL_HUMAN_OPS_ENV) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def local_human_ops_audit_fields() -> dict[str, Any]:
    if not local_human_ops_requested():
        return {}
    fields: dict[str, Any] = {"operator_mode": "local_human_ops"}
    reason = str(os.environ.get("HUMAN_OPS_REASON") or "").strip()
    if reason:
        fields["operator_reason"] = reason
    return fields


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
    request_snapshot = worker.get("request_snapshot")
    snapshot_generation = (
        request_snapshot.get("task_generation")
        if isinstance(request_snapshot, Mapping)
        else None
    )
    metadata_generation = (
        (request_snapshot.get("metadata") or {}).get("task_generation")
        if isinstance(request_snapshot, Mapping)
        and isinstance(request_snapshot.get("metadata"), Mapping)
        else None
    )
    raw_task_generation = worker.get("task_generation")
    generation_values = [
        value
        for value in (raw_task_generation, snapshot_generation, metadata_generation)
        if value is not None
    ]
    if not generation_values or any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        for value in generation_values
    ) or len(set(generation_values)) != 1:
        raise RuntimeError(
            f"active status command lease for ORCH_RUN_ID={run_id} has no exact task generation"
        )
    task_generation = int(generation_values[0])
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
        "task_generation": task_generation,
        "actor": normalize_worker_actor(dict(worker)),
        "workspace_repository_id": str(
            _worker_metadata_value(worker, "workspace_repository_id") or ""
        ).strip(),
        "workspace_branch": str(
            _worker_metadata_value(worker, "workspace_branch") or ""
        ).strip(),
        "workspace_source_root": str(
            _worker_metadata_value(worker, "workspace_source_root") or ""
        ).strip(),
    }


def status_command_metadata() -> dict[str, Any] | None:
    raw_root = _command_env(STATUS_COMMAND_ROOT_ENV)
    raw_sha = _command_env(STATUS_COMMAND_SHA_ENV)
    if not raw_root and not raw_sha:
        return None
    delivery_root = _worker_workspace_root()
    payload: dict[str, Any] = {
        "command_root": str(Path(os.path.expanduser(raw_root)).resolve()) if raw_root else None,
        "source_sha": raw_sha or None,
        "base_ref": _command_env(STATUS_COMMAND_BASE_REF_ENV) or None,
        "remote": normalize_github_repo_slug(
            _command_env(STATUS_COMMAND_REMOTE_ENV)
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
    "reconcile_merged_done": 0,
    "supersede": 0,
    "retire_archive_collision": 0,
    "approve": 0,
    "archive_correct_review_file": 0,
}
ACTIVE_WORKER_LEASE_STATUSES = {
    "running",
    "started",
    "waiting_approval",
    "suspended_approval",
    "retry_backoff",
    "stalled",
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
    symlink_component = first_symlink_component(path)
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

    if not run_id:
        if local_human_ops_requested():
            if command not in LOCAL_HUMAN_OPS_ACTIONS:
                raise RuntimeError(
                    f"local Human/Ops cannot authorize {command}; allowed actions are "
                    + ", ".join(sorted(LOCAL_HUMAN_OPS_ACTIONS))
                )
            return
        raise RuntimeError(
            "canonical mutation requires an exact active worker lease or explicit "
            f"local Human/Ops mode ({LOCAL_HUMAN_OPS_ENV}=1)"
        )

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
    worker_repository_id = str(
        _worker_metadata_value(worker, "workspace_repository_id") or ""
    ).strip()
    lease_repository_id = str(lease.get("repository_id") or "").strip()
    if not worker_repository_id or not lease_repository_id:
        raise RuntimeError(
            f"worktree lease {lease_key} has no exact delivery repository identity"
        )
    if worker_repository_id != lease_repository_id:
        raise RuntimeError(
            f"worktree lease repository mismatch for {lease_key}: "
            f"{lease_repository_id} != {worker_repository_id}"
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
    symlink_component = first_symlink_component(expanded_root)
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

    git_root = git_toplevel(root)
    if git_root != root:
        raise RuntimeError(
            f"PANTHEON_STATUS_ROOT must be a git repository root: {root}"
        )
    if not STATUS_FILE.exists() or not STATUS_FILE.is_file():
        raise RuntimeError(
            f"PANTHEON_STATUS_ROOT is missing required ai-status.json: {STATUS_FILE}"
        )
    symlink_comp_status = first_symlink_component(STATUS_FILE)
    if symlink_comp_status is not None:
        raise RuntimeError(f"ai-status.json cannot be a symlink: {STATUS_FILE} (contains symlink component: {symlink_comp_status})")
    if _existing_path_is_symlink(STATUS_FILE):
        raise RuntimeError(f"ai-status.json cannot be a symlink: {STATUS_FILE}")

    for label, path in {
        "activity_log": LOG_FILE,
        "current_work": CURRENT_WORK_FILE,
        "docs_site": DOCS_SITE_DIR,
        "dashboard_bundle": DASHBOARD_BUNDLE_FILE,
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
        "docs_site_ai_activity_log": DOCS_SITE_DIR / "ai-activity-log.jsonl",
    }.items():
        if not _path_parent_under_root(Path(path), root):
            raise RuntimeError(
                f"PANTHEON_STATUS_ROOT path binding for {label} escapes root: {path}"
            )
        symlink_comp = first_symlink_component(Path(path))
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
        symlink_comp = first_symlink_component(path)
        if symlink_comp is not None:
            raise RuntimeError(f"PANTHEON_STATUS_ROOT {label} component cannot be a symlink: {symlink_comp}")
        _validate_directory_no_symlinks_recursive(path, label)

    assert_task_archive_root_binding()

KNOWN_AGENTS = {
    "Claude": {
        "capability_lane": ["execution", "control-plane", "governance-review"],
        "default_branch": "feat/claude-execution-control",
    },
    "Claude2": {
        "capability_lane": ["execution", "control-plane", "governance-review"],
        "default_branch": "feat/claude2-execution-control",
    },
    "Antigravity": {
        "capability_lane": ["gcp", "ci-cd", "runtime-packaging", "worker-ops"],
        "default_branch": "feat/antigravity-research-runtime",
    },
    "Antigravity2": {
        "capability_lane": ["gcp", "ci-cd", "runtime-packaging", "worker-ops"],
        "default_branch": "feat/antigravity2-research-runtime",
    },
    "Codex": {
        "capability_lane": ["integration", "status-system", "schema", "acceptance"],
        "default_branch": "feat/codex-collab-system",
    },
    "Codex2": {
        "capability_lane": ["integration", "status-system", "schema", "acceptance"],
        "default_branch": "feat/codex-collab-system",
    },
    "Copilot": {
        "capability_lane": ["research-ingest", "external-search", "spec-review", "critique"],
        "default_branch": "feat/copilot-research-critique",
    },
    "Human/Ops": {
        "capability_lane": ["human-gate", "operations", "signoff"],
        "default_branch": "human/ops",
    },
}

AGENT_ALIASES = {
    "claude2": "Claude2",
    "claude 2": "Claude2",
    # Gemini/Gemini2 were retired as standalone worker identities in
    # OPS-RETIRE-GHOST-AGENT-IDENTITIES.  Canonical task history can still
    # contain those display names (and older materializers can replay them),
    # so normalize them to the current Antigravity lanes without restoring
    # retired capacity or adding them to KNOWN_AGENTS.
    "gemini": "Antigravity",
    "gemini2": "Antigravity2",
    "gemini 2": "Antigravity2",
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
    parts.append("Treat generated views as derived from machine-readable state.")
    parts.append("Follow the canonical lifecycle todo -> in_progress -> review -> review_approved -> done.")
    parts.append("Use scripts/ai-status.sh for every state change.")
    return " ".join(parts)


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
    try:
        return parsed.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, ValueError):
        if isinstance(value, str) and value.strip():
            return value.strip()
        return parsed.isoformat()


def localize_embedded_timestamps(text: Any) -> str:
    if text is None:
        return "-"
    rendered = str(text)
    if not rendered:
        return "-"
    return ISO_TIMESTAMP_RE.sub(lambda match: format_display_timestamp(match.group(0)), rendered)


def canonical_agent_name(name: str | None) -> str:
    # .orchestrator/supervisor.py has a separate, newer canonical_agent_name
    # (config, value) that resolves against the live config.json agent
    # registry instead of KNOWN_AGENTS/AGENT_ALIASES below. This function
    # predates that (present since the initial commit) and is missing
    # worker-slot ids (e.g. codex1_1) the supervisor.py version resolves.
    # Investigated 2026-08-17: no evidence in ai-status.json/activity-log/
    # task-archive of this ever causing a real mismatch (owner/reviewer
    # values observed in practice are always pre-normalized display names),
    # so left as a known, low-risk divergence rather than unified across
    # ~60 call sites here with no existing test coverage.
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



def current_actor(default: str = "Codex") -> str:
    if local_human_ops_requested():
        return "Human/Ops"
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
                "reviewer": "Claude2",
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
                "owner": "Claude2",
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
                "reviewer": "Claude2",
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
        # Terminal dependency truth remains in the authoritative TaskStore
        # after the richer, human-facing archive snapshot has been written.
        # The archive is deliberately not consulted by scheduling.
        TERMINAL_FACTS_KEY: {},
        # A receipt is created only after the rich archive snapshot and index
        # have both been read back from this canonical root.
        ARCHIVE_RECEIPTS_KEY: {},
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
    # Unit-level callers may bind temporary module globals without a process
    # status root. Real commands always require PANTHEON_STATUS_ROOT before
    # they enter this path, and therefore must carry the issued identity.
    if store_mode == "authoritative" and (
        str(os.environ.get(STATUS_ROOT_ENV) or "").strip()
        or str(os.environ.get(CANONICAL_TASK_STATE_IDENTITY_ENV) or "").strip()
    ):
        canonical_task_state_identity_from_environment(
            status_root=STATUS_ROOT,
            event_log=_task_state_event_path(store_mode),
        )


def load_state() -> dict[str, Any]:
    store_mode = str(os.environ.get(TASK_STATE_STORE_MODE_ENV) or "").strip().lower()
    if store_mode and store_mode != "authoritative":
        raise SystemExit(
            f"{TASK_STATE_STORE_MODE_ENV} must be authoritative when configured"
        )
    if store_mode == "authoritative":
        _validate_task_state_projection_binding(store_mode)
        event_path = _task_state_event_path(store_mode)
        # One compact V2 head read plus a crash tail only.  The frozen archive
        # and prior transition prefix are deliberately outside this hot path.
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
        normalize_terminal_facts(state)
        normalize_archive_receipts(state)
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
    normalize_terminal_facts(state)
    normalize_archive_receipts(state)
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
    if store_mode and store_mode != "authoritative":
        raise RuntimeError(
            f"{TASK_STATE_STORE_MODE_ENV} must be authoritative when configured"
        )
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
                "approval_queue": str(APPROVAL_QUEUE_FILE),
                "provider_capabilities": str(STATUS_ROOT / ".orchestrator" / "provider_capabilities.json"),
            }
        )
    return payload


def int_config_setting(settings: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(settings.get(key, default))
    except (TypeError, ValueError):
        return default


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
    account_caps = int_mapping_config_setting(ready_dispatcher, "max_concurrent_per_account")
    agent_caps = {
        str(agent_id): max(0, int((agent or {}).get("max_parallel", 0) or 0))
        for agent_id, agent in (config.get("agents", {}) or {}).items()
        if not str((agent or {}).get("dispatch_slot_for") or "").strip()
    }
    return {
        "mode": "single_dispatch_planner",
        "max_dispatches_per_tick": int_config_setting(ready_dispatcher, "max_dispatches_per_tick", 4),
        "max_parallel_by_agent": agent_caps,
        "max_concurrent_per_account": account_caps,
        "sidecar_only_agents": ready_dispatcher.get("sidecar_only_agents") or [],
    }


def save_state(state: dict[str, Any]) -> None:
    store_mode = str(os.environ.get(TASK_STATE_STORE_MODE_ENV) or "").strip().lower()
    if store_mode and store_mode != "authoritative":
        raise RuntimeError(
            f"{TASK_STATE_STORE_MODE_ENV} must be authoritative when configured"
        )
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


def _task_state_event_path(mode: str) -> Path:
    raw_path = str(os.environ.get(TASK_STATE_EVENT_LOG_ENV) or "").strip()
    if not raw_path:
        raise SystemExit(f"{TASK_STATE_EVENT_LOG_ENV} is required in {mode} mode")
    event_path = Path(os.path.expanduser(raw_path))
    if not event_path.is_absolute():
        raise SystemExit(f"{TASK_STATE_EVENT_LOG_ENV} must be an absolute path")
    return event_path


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
    normalize_terminal_facts(state)
    return TaskResolver(
        state.get("tasks", []),
        terminal_facts=state.get(TERMINAL_FACTS_KEY),
        allow_archive_lookup=False,
    )



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


def _terminal_fact_for_task(
    task: Mapping[str, Any],
    *,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Create the small immutable dependency fact retained after archival."""

    task_id = str(task.get("id") or "").strip()
    outcome = _status_archive_terminal_outcome(dict(task))
    try:
        generation = int(task.get("generation", 1))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"terminal task has invalid generation: {task_id}") from exc
    if not task_id or not outcome or generation < 1:
        raise RuntimeError(f"terminal task cannot produce a dependency fact: {task_id}")
    return {
        "status": "done",
        "terminal_outcome": outcome,
        "generation": generation,
        "recorded_at": str(recorded_at or iso_now()),
    }


def normalize_terminal_facts(state: dict[str, Any]) -> None:
    """Validate the TaskStore's compact terminal-dependency index.

    This is authoritative state, not an archive index: an invalid fact must
    stop a scheduler rather than fall through to an archive lookup.
    """

    raw_facts = state.get(TERMINAL_FACTS_KEY)
    if raw_facts in (None, {}):
        state[TERMINAL_FACTS_KEY] = {}
        return
    if not isinstance(raw_facts, dict):
        raise RuntimeError("terminal_facts must be an object")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_task_id, raw_fact in raw_facts.items():
        task_id = str(raw_task_id or "").strip()
        if not task_id or not isinstance(raw_fact, Mapping):
            raise RuntimeError("terminal_facts contains an invalid entry")
        try:
            generation = int(raw_fact.get("generation"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"terminal fact has invalid generation: {task_id}") from exc
        outcome = str(raw_fact.get("terminal_outcome") or "").strip().lower()
        recorded_at = str(raw_fact.get("recorded_at") or "").strip()
        if (
            raw_fact.get("status") != "done"
            or outcome not in {"completed", "superseded"}
            or generation < 1
            or not recorded_at
        ):
            raise RuntimeError(f"terminal fact has invalid lifecycle data: {task_id}")
        normalized[task_id] = {
            "status": "done",
            "terminal_outcome": outcome,
            "generation": generation,
            "recorded_at": recorded_at,
        }
    state[TERMINAL_FACTS_KEY] = normalized


def record_terminal_fact(
    state: dict[str, Any],
    task: Mapping[str, Any],
    *,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Atomically retain a terminal fact; conflicting terminal history fails."""

    normalize_terminal_facts(state)
    task_id = str(task.get("id") or "").strip()
    candidate = _terminal_fact_for_task(task, recorded_at=recorded_at)
    facts = state[TERMINAL_FACTS_KEY]
    existing = facts.get(task_id)
    if existing is not None:
        if {
            key: existing.get(key)
            for key in ("status", "terminal_outcome", "generation")
        } != {
            key: candidate[key]
            for key in ("status", "terminal_outcome", "generation")
        }:
            raise RuntimeError(f"terminal fact conflicts with existing TaskStore fact: {task_id}")
        return deepcopy(existing)
    facts[task_id] = candidate
    return deepcopy(candidate)


def has_terminal_fact(state: Mapping[str, Any], task_id: str) -> bool:
    facts = state.get(TERMINAL_FACTS_KEY)
    return isinstance(facts, Mapping) and str(task_id or "").strip() in facts


def normalize_archive_receipts(state: dict[str, Any]) -> None:
    """Validate archive write receipts retained with terminal facts.

    Receipts are part of the canonical task projection, not a second archive
    index.  They prove which root was read back before an outbox was cleared;
    an old terminal fact is valid without one and is explicitly reported as
    needing reconciliation.
    """

    raw = state.get(ARCHIVE_RECEIPTS_KEY)
    if raw in (None, {}):
        state[ARCHIVE_RECEIPTS_KEY] = {}
        return
    if not isinstance(raw, dict):
        raise RuntimeError("archive_receipts must be an object")
    facts = state.get(TERMINAL_FACTS_KEY)
    if not isinstance(facts, Mapping):
        raise RuntimeError("archive_receipts requires terminal_facts")
    normalized: dict[str, dict[str, str | int]] = {}
    required = {
        "schema_version",
        "archive_root",
        "snapshot_sha256",
        "index_sha256",
        "recorded_at",
    }
    for raw_task_id, raw_receipt in raw.items():
        task_id = str(raw_task_id or "").strip()
        if (
            not task_id
            or task_id not in facts
            or not isinstance(raw_receipt, Mapping)
            or set(raw_receipt) != required
            or raw_receipt.get("schema_version") != ARCHIVE_RECEIPT_SCHEMA_VERSION
        ):
            raise RuntimeError("archive_receipts contains an invalid entry")
        archive_root = str(raw_receipt.get("archive_root") or "").strip()
        snapshot_sha256 = str(raw_receipt.get("snapshot_sha256") or "").strip()
        index_sha256 = str(raw_receipt.get("index_sha256") or "").strip()
        recorded_at = str(raw_receipt.get("recorded_at") or "").strip()
        if (
            not archive_root
            or not recorded_at
            or not re.fullmatch(r"[0-9a-f]{64}", snapshot_sha256)
            or not re.fullmatch(r"[0-9a-f]{64}", index_sha256)
        ):
            raise RuntimeError("archive_receipt fields are invalid")
        normalized[task_id] = {
            "schema_version": ARCHIVE_RECEIPT_SCHEMA_VERSION,
            "archive_root": archive_root,
            "snapshot_sha256": snapshot_sha256,
            "index_sha256": index_sha256,
            "recorded_at": recorded_at,
        }
    state[ARCHIVE_RECEIPTS_KEY] = normalized


def _archive_root_identity() -> str:
    assert_task_archive_root_binding()
    return str(task_archive_module.ARCHIVE_DIR.expanduser().resolve())


def _archive_receipt_for_snapshot(
    *,
    archive_root: str,
    snapshot: Mapping[str, Any],
    index: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": ARCHIVE_RECEIPT_SCHEMA_VERSION,
        "archive_root": archive_root,
        "snapshot_sha256": _canonical_json_sha256(snapshot),
        "index_sha256": _canonical_json_sha256(index),
        "recorded_at": iso_now(),
    }


def terminal_archive_projection(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return terminal facts with their current archive availability.

    The rich archive may be lost or pending reconciliation without invalidating
    the compact dependency fact.  Exposing that distinction prevents a
    completed task from becoming an "Unknown task" in management views.
    """

    normalize_terminal_facts(state)
    normalize_archive_receipts(state)
    facts = state[TERMINAL_FACTS_KEY]
    receipts = state[ARCHIVE_RECEIPTS_KEY]
    rows: list[dict[str, Any]] = []
    for task_id in sorted(facts):
        snapshot = load_archived_snapshot(task_id)
        receipt = receipts.get(task_id)
        snapshot_sha256 = (
            _canonical_json_sha256(snapshot) if isinstance(snapshot, Mapping) else None
        )
        receipt_matches = bool(
            isinstance(receipt, Mapping)
            and snapshot_sha256
            and receipt.get("archive_root") == _archive_root_identity()
            and receipt.get("snapshot_sha256") == snapshot_sha256
        )
        rows.append(
            {
                "task_id": task_id,
                **deepcopy(facts[task_id]),
                "archive_missing": snapshot is None,
                "archive_receipt_valid": receipt_matches,
            }
        )
    return rows


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
    existing = load_archived_snapshot(task_id)
    task_clean = deepcopy(task)
    task_clean.pop("status_write_pending", None)
    task_clean.pop("status_write_pending_count", None)
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
        "task": task_clean,
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

    # The archive is only a rich derived record.  Dependency resolution keeps
    # this compact fact in the TaskStore even after recovery removes the active
    # terminal row.
    record_terminal_fact(state, task, recorded_at=str(snapshot["archived_at"]))

    pending = state.get(STATUS_ARCHIVE_OUTBOX_KEY)
    snapshots: list[dict[str, Any]] = []
    if pending not in (None, {}, []):
        snapshots = list(
            task_archive_module.validate_status_archive_outbox(
                pending,
                expected_archive_root=_archive_root_identity(),
            )["snapshots"]
        )
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
    state[STATUS_ARCHIVE_OUTBOX_KEY] = task_archive_module.status_archive_outbox_payload(
        snapshots,
        archive_root=_archive_root_identity(),
    )
    # Retain the terminal row and its references until the archive and rebuilt
    # index are durable. Recovery removes them in the same final status write
    # that clears this outbox, so readers never observe a vanished task.
    return snapshot


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
    pending = task_archive_module.validate_status_archive_outbox(
        pending,
        expected_archive_root=_archive_root_identity(),
    )
    state[STATUS_ARCHIVE_OUTBOX_KEY] = pending
    for expected in pending["snapshots"]:
        # A receipt repair may intentionally queue an already-existing rich
        # snapshot (including a governed correction context). Do not rebuild a
        # second shape from its task row; verify the exact stored bytes first.
        actual = load_archived_snapshot(str(expected["task_id"]))
        if actual is None:
            actual = archive_task_snapshot(
                deepcopy(expected["task"]),
                handoffs=deepcopy(expected["handoffs"]),
                blockers=deepcopy(expected["blockers"]),
                archived_at=str(expected["archived_at"]),
                recent_limit=task_archive_recent_limit(),
            )
        actual_digest = _canonical_json_sha256(actual)
        if actual_digest != pending["snapshot_sha256s"][expected["task_id"]]:
            raise RuntimeError(
                f"status archive outbox readback mismatch: {expected['task_id']}"
            )
    rebuilt_index = rebuild_archive_index(recent_limit=task_archive_recent_limit())
    readback_index = load_archive_index()
    if _canonical_json_sha256(readback_index) != _canonical_json_sha256(rebuilt_index):
        raise RuntimeError("status archive index readback mismatch")
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
        if active is not None:
            active_clean = deepcopy(active)
            active_clean.pop("status_write_pending", None)
            active_clean.pop("status_write_pending_count", None)
            expected_clean = deepcopy(expected["task"])
            expected_clean.pop("status_write_pending", None)
            expected_clean.pop("status_write_pending_count", None)
            if _canonical_json_sha256(active_clean) != _canonical_json_sha256(expected_clean):
                raise RuntimeError(
                    f"active terminal task changed during archive recovery: {task_id}"
                )
        record_terminal_fact(
            state,
            expected["task"],
            recorded_at=str(expected["archived_at"]),
        )
    normalize_archive_receipts(state)
    archive_root = str(pending["archive_root"])
    receipts = state[ARCHIVE_RECEIPTS_KEY]
    for expected in pending["snapshots"]:
        task_id = str(expected["task_id"])
        receipts[task_id] = _archive_receipt_for_snapshot(
            archive_root=archive_root,
            snapshot=expected,
            index=readback_index,
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
    _update_pending_outbox_indicators(state)
    save_state(state)
    return True


def is_status_outbox_visibility_enabled() -> bool:
    val = os.environ.get(STATUS_OUTBOX_VISIBILITY_ENABLED_ENV)
    if val is not None:
        return val.strip().lower() in ("1", "true", "yes", "on")
    return False


def _pending_outbox_write_counts(state: dict[str, Any]) -> dict[str, int]:
    """Count queued writes per task id across both outbox planes.

    Only writes that name a task are counted. A board-wide event (a wave
    open/close, for example) must never make an untouched task look stale.
    """

    pending_activity = state.get(STATUS_ACTIVITY_OUTBOX_KEY)
    pending_events = (
        pending_activity.get("events")
        if isinstance(pending_activity, dict)
        else None
    )
    if not isinstance(pending_events, list):
        pending_events = []

    pending_archive = state.get(STATUS_ARCHIVE_OUTBOX_KEY)
    archive_snapshots = (
        pending_archive.get("snapshots") if isinstance(pending_archive, dict) else None
    )
    if not isinstance(archive_snapshots, list):
        archive_snapshots = []

    counts: dict[str, int] = {}
    for queued in (*pending_events, *archive_snapshots):
        if not isinstance(queued, dict):
            continue
        task_id = queued.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            continue
        counts[task_id] = counts.get(task_id, 0) + 1
    return counts


def _update_pending_outbox_indicators(state: dict[str, Any]) -> None:
    """Stamp per-task markers for status writes queued behind an integrity block.

    The outbox already makes the write itself durable. Without these markers the
    task row stays byte-identical to its pre-attempt state, so a stale board row
    is indistinguishable from a task nobody touched. Flag off restores exactly
    the incumbent shape by removing the markers.
    """

    counts = (
        _pending_outbox_write_counts(state)
        if is_status_outbox_visibility_enabled()
        else {}
    )
    for task in state.get("tasks", []):
        if not isinstance(task, dict):
            continue
        pending = counts.get(str(task.get("id") or ""), 0)
        if pending > 0:
            task["status_write_pending"] = True
            task["status_write_pending_count"] = pending
        else:
            task.pop("status_write_pending", None)
            task.pop("status_write_pending_count", None)


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
        try:
            _append_logs_unlocked(missing)
        except ActivityAuditInvariantError:
            _update_pending_outbox_indicators(state)
            save_state(state)
            refresh_derived_status_views(state)
            raise
        final = _active_activity_event_digests_unlocked(pending_event_ids)
        if set(final) != pending_event_ids:
            final = _activity_event_index_unlocked(pending_event_ids)
        if any(
            final.get(str(event["event_id"])) != _canonical_json_sha256(event)
            for event in pending["events"]
        ):
            raise RuntimeError("status activity outbox append/readback mismatch")
        state[STATUS_ACTIVITY_OUTBOX_KEY] = None
        _update_pending_outbox_indicators(state)
        save_state(state)
    return True


def commit_state_with_activity_outbox(
    state: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    defer_activity_recovery: bool = False,
) -> None:
    if events:
        state[STATUS_ACTIVITY_OUTBOX_KEY] = {
            "schema_version": STATUS_ACTIVITY_OUTBOX_SCHEMA_VERSION,
            "transaction_id": "ai-status-tx-" + _canonical_json_sha256(events),
            "events": deepcopy(events),
        }
    _update_pending_outbox_indicators(state)
    save_state(state)
    if state.get(STATUS_ARCHIVE_OUTBOX_KEY) not in (None, {}, []):
        _status_archive_fault("pending_status")
    recover_status_archive_outbox(state)
    # `defer_activity_recovery` leaves a non-empty activity outbox as durable,
    # self-describing recovery state instead of flushing it inline: the flush
    # is a second task-state event, and a caller that promised its own
    # canonical mutation is exactly one commit (e.g. the dev-bridge batch
    # materializer) must not fold that into its own atomicity accounting. The
    # next governed write command's leading recovery step -- or an explicit
    # `recover` -- picks it up later as ordinary, non-gating projection work.
    if events and not defer_activity_recovery:
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


def task_assignment_generation(task: Mapping[str, Any] | None) -> int:
    """Return the persisted assignment epoch; legacy snapshots start at one."""

    if not isinstance(task, Mapping):
        return 0
    value = task.get("generation", 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuntimeError(
            f"Task {task.get('id') or '?'} has invalid assignment generation"
        )
    return value


def validate_bound_status_command_task_authority(
    state: dict[str, Any], command: str, args: list[str]
) -> None:
    """Revalidate the worker lease against the locked canonical task row."""

    binding = getattr(_STATUS_COMMAND_LEASE_LOCAL, "binding", None)
    if not isinstance(binding, Mapping):
        return
    task_id = _command_task_id(command, args)
    task = get_task(state, task_id)
    if task is None:
        raise RuntimeError(f"active status command task is missing: {task_id}")
    lease_generation = binding.get("task_generation")
    current_generation = task_assignment_generation(task)
    if lease_generation != current_generation:
        raise RuntimeError(
            f"active status command task generation mismatch: "
            f"lease {lease_generation} != canonical {current_generation}"
        )
    actor = canonical_agent_name(str(binding.get("actor") or current_actor()))
    current_roles = {
        canonical_agent_name(task.get("owner")),
        canonical_agent_name(task.get("reviewer")),
    }
    if actor not in current_roles:
        raise RuntimeError(
            f"active status command actor {actor} no longer owns a canonical role for {task_id}"
        )


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
    delivery_ref: str = "HEAD",
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
        ["merge-base", "--is-ancestor", delivery_ref, target_ref],
        cwd=repository_root,
    )
    delivery["merge_test_commit"] = delivery_ref
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


def approved_closeout_commit_ref(
    task: Mapping[str, Any],
    *,
    repository_root: Path,
    branch: str,
) -> str | None:
    """Return the immutable reviewed head used for an approved closeout.

    A reusable worker worktree may be fast-forwarded to a later ``dev`` tip
    after the task PR merges.  That workspace movement must not replace the
    exact task commit that the canonical reviewer approved.  Only a complete
    canonical review-bridge binding may select this path; legacy or incomplete
    rows retain the existing HEAD-based validation.
    """

    if str(task.get("status") or "").strip() != "review_approved":
        return None
    if not github_review_bridge_evidence_matches(task):
        return None
    binding = task.get(APPROVAL_BINDING_KEY)
    if not isinstance(binding, Mapping):
        return None
    approved_head = str(binding.get("head_sha") or "").strip().lower()
    approved_branch = str(binding.get("head_branch") or "").strip()
    if not APPROVAL_HEAD_SHA_RE.fullmatch(approved_head):
        raise SystemExit(
            "Cannot finalize task: canonical approval has an invalid exact head SHA."
        )
    if approved_branch != branch:
        raise SystemExit(
            "Cannot finalize task: delivery branch does not match canonical approved "
            f"head branch ({branch} != {approved_branch or 'missing'})."
        )

    commit_ref = f"{approved_head}^{{commit}}"
    if not git_command_succeeds(["cat-file", "-e", commit_ref], cwd=repository_root):
        subprocess.run(
            ["git", "fetch", "--quiet", "origin", approved_head],
            cwd=repository_root,
            capture_output=True,
            text=True,
            check=False,
        )
    if not git_command_succeeds(["cat-file", "-e", commit_ref], cwd=repository_root):
        raise SystemExit(
            "Cannot finalize task: canonical approved head is unavailable in the "
            f"delivery repository ({approved_head})."
        )
    return approved_head


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


def _resolved_git_common_dir(repository_root: Path) -> Path:
    raw_common_dir = run_git_command(
        ["rev-parse", "--git-common-dir"],
        cwd=repository_root,
        failure_message=(
            "Cannot finalize task: delivery repository git common directory is unavailable."
        ),
    )
    common_dir = Path(raw_common_dir)
    if not common_dir.is_absolute():
        common_dir = repository_root / common_dir
    return common_dir.resolve()


def _registered_worktree_paths(repository_root: Path) -> set[Path]:
    output = run_git_command(
        ["worktree", "list", "--porcelain"],
        cwd=repository_root,
        failure_message=(
            "Cannot finalize task: registered delivery worktrees are unavailable."
        ),
    )
    paths: set[Path] = set()
    for line in output.splitlines():
        if not line.startswith("worktree "):
            continue
        paths.add(Path(line.removeprefix("worktree ")).expanduser().resolve())
    return paths


def _validate_delivery_workspace_repository(
    config: dict[str, Any],
    *,
    repository_id: str,
    repository_root: Path,
    registered_root: Path,
) -> None:
    if not repository_root.is_dir() or git_toplevel(repository_root) != repository_root:
        raise SystemExit(
            f"Cannot finalize task: delivery workspace must be a git repository root: {repository_root}."
        )
    if not registered_root.is_dir() or git_toplevel(registered_root) != registered_root:
        raise SystemExit(
            "Cannot finalize task: registered repository local_path is not a git root: "
            f"{registered_root}."
        )
    if _resolved_git_common_dir(repository_root) != _resolved_git_common_dir(registered_root):
        raise SystemExit(
            "Cannot finalize task: delivery workspace is not registered to the configured "
            f"{repository_id} checkout."
        )
    if repository_root not in _registered_worktree_paths(registered_root):
        raise SystemExit(
            "Cannot finalize task: delivery workspace is not present in the configured "
            "repository worktree registry."
        )
    expected_slug = normalize_github_repo_slug(repository_slug(config, repository_id))
    if not expected_slug:
        raise SystemExit(
            f"Cannot finalize task: repository `{repository_id}` has no configured GitHub slug."
        )
    actual_slug = normalize_github_repo_slug(
        run_git_command(
            ["remote", "get-url", "origin"],
            cwd=repository_root,
            failure_message=(
                "Cannot finalize task: delivery workspace origin remote is unavailable."
            ),
        )
    )
    if actual_slug != expected_slug:
        raise SystemExit(
            "Cannot finalize task: delivery workspace origin does not match task "
            f"repository ({actual_slug or 'missing'} != {expected_slug})."
        )


def _done_delivery_repository_root(
    config: dict[str, Any],
    task: dict[str, Any],
    repository_id: str,
) -> tuple[Path, dict[str, Any]]:
    configured_root = repository_configured_local_path(config, repository_id)
    if configured_root is None:
        raise SystemExit(
            f"Cannot finalize task: repository `{repository_id}` has no local_path configured."
        )
    configured_symlink = first_symlink_component(configured_root)
    if configured_symlink is not None:
        raise SystemExit(
            "Cannot finalize task: registered repository local_path cannot include a "
            f"symlink component: {configured_symlink}."
        )
    registered_root = repository_local_path(config, repository_id)
    if registered_root is None:
        raise SystemExit(
            f"Cannot finalize task: repository `{repository_id}` has no local_path configured."
        )
    registered_root = registered_root.resolve(strict=False)
    try:
        workspace_root = _worker_workspace_root()
    except RuntimeError as exc:
        raise SystemExit(f"Cannot finalize task: {exc}.") from exc
    if workspace_root is None:
        if not registered_root.is_dir():
            raise SystemExit(
                "Cannot finalize task: registered delivery repository does not exist: "
                f"{registered_root}."
            )
        return registered_root, {
            "repository_path_source": "repository_registry",
            "workspace_env_names": [],
            "workspace_env_match": False,
            "workspace_lease_validated": False,
        }

    canonical_status_root = STATUS_ROOT.resolve()
    if workspace_root == canonical_status_root:
        raise SystemExit(
            "Cannot finalize task: delivery workspace must differ from the canonical status root."
        )
    _validate_delivery_workspace_repository(
        config,
        repository_id=repository_id,
        repository_root=workspace_root,
        registered_root=registered_root,
    )

    run_id = str(os.environ.get("ORCH_RUN_ID") or "").strip()
    source = "explicit_workspace_env"
    lease_validated = False
    if run_id:
        binding = getattr(_STATUS_COMMAND_LEASE_LOCAL, "binding", None)
        if not isinstance(binding, Mapping):
            raise SystemExit(
                "Cannot finalize task: active worker delivery workspace has no validated lease binding."
            )
        lease_repository_id = str(
            binding.get("workspace_repository_id") or ""
        ).strip()
        if lease_repository_id != repository_id:
            raise SystemExit(
                "Cannot finalize task: worker lease repository does not match task artifacts "
                f"({lease_repository_id or 'missing'} != {repository_id})."
            )
        binding_task_id = str(binding.get("task_id") or "").strip()
        if binding_task_id != str(task.get("id") or "").strip():
            raise SystemExit(
                "Cannot finalize task: worker lease task does not match closeout task."
            )
        source = "worker_lease"
        lease_validated = True

    return workspace_root, {
        "repository_path_source": source,
        "workspace_env_names": ["PANTHEON_WORKTREE_ROOT", "ORCH_WORKSPACE_PATH"],
        "workspace_env_match": True,
        "workspace_lease_validated": lease_validated,
    }


def _delivered_commit_timestamp(repository_root: Path, task: Mapping[str, Any]) -> str:
    """Return the ISO timestamp the delivered content was actually authored at.

    A squash merge creates a brand-new commit object with a fresh
    author/committer date stamped at merge time, while copying the
    original commit's message -- including any LLM-Agent/Reviewer trailer
    -- verbatim. Reading HEAD's own timestamp after such a merge makes a
    trailer that was correct when written look like it postdates a
    reassignment that in reality only happened after the real authoring,
    not before it.

    When the task recorded an exact reviewed head (APPROVAL_BINDING_KEY),
    prefer that commit's own timestamp instead -- fetching it from origin
    first if the local checkout does not already have the object. GitHub
    keeps every PR commit reachable by SHA even after a squash-merge
    deletes the source branch, so this is a targeted, bounded fetch of one
    already-known object, not a discovery search. Falls back to HEAD's own
    timestamp when no reviewed head is recorded or that exact object
    cannot be resolved even after the fetch attempt.
    """

    binding = task.get(APPROVAL_BINDING_KEY)
    reviewed_head = (
        str(binding.get("head_sha") or "").strip()
        if isinstance(binding, Mapping)
        else ""
    )
    if reviewed_head and APPROVAL_HEAD_SHA_RE.fullmatch(reviewed_head):
        reachable = git_command_succeeds(
            ["cat-file", "-e", reviewed_head], cwd=repository_root
        )
        if not reachable:
            subprocess.run(
                ["git", "fetch", "--quiet", "origin", reviewed_head],
                cwd=repository_root,
                capture_output=True,
                text=True,
                check=False,
            )
            reachable = git_command_succeeds(
                ["cat-file", "-e", reviewed_head], cwd=repository_root
            )
        if reachable:
            return run_git_command(
                ["show", "-s", "--format=%cI", reviewed_head],
                cwd=repository_root,
                failure_message=(
                    "Cannot finalize task: delivered commit timestamp is "
                    "unavailable for reassignment verification."
                ),
            )
    return run_git_command(
        ["show", "-s", "--format=%cI", "HEAD"],
        cwd=repository_root,
        failure_message=(
            "Cannot finalize task: delivered commit timestamp is "
            "unavailable for reassignment verification."
        ),
    )


def collect_done_delivery_metadata(task: dict[str, Any], actor: str) -> dict[str, Any]:
    settings = delivery_gate_settings()
    commit_rules = commit_convention_settings()
    config = load_config()
    try:
        repository_id = validate_task_repository_scope(config, task)
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(f"Cannot finalize task: {exc}") from exc
    repository_root, repository_path_metadata = _done_delivery_repository_root(
        config, task, repository_id
    )
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
        "canonical_status_root": str(STATUS_ROOT.resolve()),
        "canonical_status_root_source": STATUS_ROOT_ENV,
        "roots_separated": repository_root != STATUS_ROOT.resolve(),
        **repository_path_metadata,
    }
    command_metadata = status_command_metadata()
    if command_metadata:
        delivery["status_command_runtime"] = command_metadata
    if repository_path_metadata["repository_path_source"] == "worker_lease":
        binding = getattr(_STATUS_COMMAND_LEASE_LOCAL, "binding", {})
        expected_branch = str(binding.get("workspace_branch") or "").strip()
        if expected_branch and branch != expected_branch:
            raise SystemExit(
                "Cannot finalize task: delivery branch does not match worker lease "
                f"({branch} != {expected_branch})."
            )

    if settings["require_commit_hash"]:
        workspace_head = run_git_command(
            ["rev-parse", "HEAD"],
            cwd=repository_root,
            failure_message="Cannot finalize task: a HEAD commit hash is required before moving to done.",
        )
        if not workspace_head:
            raise SystemExit("Cannot finalize task: a HEAD commit hash is required before moving to done.")
        approved_ref = approved_closeout_commit_ref(
            task,
            repository_root=repository_root,
            branch=branch,
        )
        commit_ref = approved_ref or "HEAD"
        commit_hash = (
            run_git_command(
                ["rev-parse", commit_ref],
                cwd=repository_root,
                failure_message="Cannot finalize task: the delivered commit hash is unavailable.",
            )
            if approved_ref
            else workspace_head
        )
        if not commit_hash:
            raise SystemExit(
                "Cannot finalize task: the delivered commit hash is required before moving to done."
            )
        delivery["commit"] = commit_hash
        delivery["commit_source"] = (
            "canonical_approved_head" if approved_ref else "workspace_head"
        )
        if approved_ref:
            delivery["workspace_head"] = workspace_head
        subject = run_git_command(
            ["show", "-s", "--format=%s", commit_ref],
            cwd=repository_root,
            failure_message="Cannot finalize task: latest commit subject is unavailable.",
        )
        body = run_git_command(
            ["show", "-s", "--format=%b", commit_ref],
            cwd=repository_root,
            failure_message="Cannot finalize task: latest commit body is unavailable.",
        )
        author_name = run_git_command(
            ["show", "-s", "--format=%an", commit_ref],
            cwd=repository_root,
            failure_message="Cannot finalize task: latest commit author name is unavailable.",
        )
        author_email = run_git_command(
            ["show", "-s", "--format=%ae", commit_ref],
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
        commit_timestamp = ""
        if trailer_skip_reason is None:
            for field_name in required_fields:
                actual_value = metadata_fields.get(field_name)
                if not actual_value:
                    missing_fields.append(field_name)
                    continue
                expected_value = expected_fields.get(field_name)
                if expected_value and actual_value != expected_value:
                    # The supervisor reassigns owner and reviewer as a pair when
                    # a lane goes unavailable, so a merged delivery can carry
                    # stale `LLM-Agent` and `Reviewer` trailers at once. Both are
                    # verified against the audited reassignment chain instead of
                    # failing closed and requiring a Human/Ops sign-off.
                    if field_name in {"LLM-Agent", "Reviewer"} and not commit_timestamp:
                        commit_timestamp = _delivered_commit_timestamp(
                            repository_root, task
                        )
                    if field_name == "LLM-Agent":
                        delivery["commit_owner_reassignment"] = (
                            _verified_done_owner_reassignment(
                                task,
                                commit_owner=actual_value,
                                current_owner=actor,
                                commit_timestamp=commit_timestamp,
                            )
                        )
                        continue
                    if field_name == "Reviewer":
                        delivery["commit_reviewer_reassignment"] = (
                            _verified_done_reviewer_reassignment(
                                task,
                                commit_reviewer=actual_value,
                                current_reviewer=expected_value,
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
            delivery_ref=str(delivery.get("commit") or "HEAD"),
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
    normalize_terminal_facts(state)
    for task in state["tasks"]:
        ensure_agent(task["owner"])
        ensure_agent(task["reviewer"])
        if task["owner"] == task["reviewer"]:
            raise SystemExit(f"Task {task['id']} has identical owner and reviewer")
        if task["status"] == "blocked" and not task.get("waiting_for"):
            block_reason = task.get("block_reason")
            if not (
                isinstance(block_reason, dict)
                and str(block_reason.get("kind") or "").strip()
                and str(block_reason.get("required_action") or "").strip()
            ):
                raise SystemExit(
                    f"Blocked task {task['id']} is missing waiting_for or a structured block_reason"
                )
    for blocker in state.get("blockers", []):
        ensure_agent(blocker["owner"])
        ensure_agent(blocker["waiting_for"])

    for handoff in state.get("handoffs", []):
        ensure_agent(handoff["from"])
        ensure_agent(handoff["to"])


def normalize_state_agents(state: dict[str, Any]) -> None:
    for task in state.get("tasks", []):
        task["owner"] = canonical_agent_name(task.get("owner"))
        task["reviewer"] = canonical_agent_name(task.get("reviewer"))
        if task.get("waiting_for"):
            task["waiting_for"] = canonical_agent_name(task.get("waiting_for"))

    for blocker in state.get("blockers", []):
        blocker["owner"] = canonical_agent_name(blocker.get("owner"))
        blocker["waiting_for"] = canonical_agent_name(blocker.get("waiting_for"))

    for handoff in state.get("handoffs", []):
        handoff["from"] = canonical_agent_name(handoff.get("from"))
        handoff["to"] = canonical_agent_name(handoff.get("to"))

    for agent in state.get("agents", []):
        agent["name"] = canonical_agent_name(agent.get("name"))


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


def pending_status_write_count(task: dict[str, Any]) -> int:
    """Return how many status writes are queued behind a canonical integrity block."""

    if not isinstance(task, dict) or not task.get("status_write_pending"):
        return 0
    count = task.get("status_write_pending_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        return 0
    return count


def display_task_status(task: dict[str, Any]) -> str:
    """Render a status, flagging a row a queued write has not been able to update."""

    status = task.get("status")
    text = "" if status is None else str(status)
    pending = pending_status_write_count(task)
    if not pending:
        return text
    noun = "write" if pending == 1 else "writes"
    return f"{text} (stale: {pending} {noun} queued)"


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
                    status=cell(display_task_status(task)),
                    depends=cell(depends),
                    summary=cell(task.get("summary_zh") or "-"),
                )
            )

    current_logs = logs[-20:]
    canonical_files = canonical_file_set(state)
    tier_labels = canonical_tier_labels(state)
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

    pending_write_tasks = [
        task for task in state["tasks"] if pending_status_write_count(task)
    ]
    if pending_write_tasks:
        lines.extend(
            [
                "",
                "## Status Write Backlog",
                "",
                "Canonical status writes for these tasks are durably queued behind an",
                "integrity block. Their rows below may be stale; a stale row here is",
                "not evidence that the task was never touched.",
                "",
                "| Task | Owner | Displayed Status | Queued Writes |",
                "|---|---|---|---|",
            ]
        )
        for task in pending_write_tasks:
            lines.append(
                "| `{id}` | {owner} | {status} | {count} |".format(
                    id=cell(task.get("id")),
                    owner=cell(task.get("owner")),
                    status=cell(task.get("status")),
                    count=pending_status_write_count(task),
                )
            )

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
                status=cell(display_task_status(task)),
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
    if status in {"suspended_approval", "retry_backoff", "stalled"}:
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
        intent = event.get("intent") if isinstance(event.get("intent"), dict) else {}
        linked_worker = workers_by_event.get(str(event_id), {})
        rows.append(
            {
                "id": event_id,
                "task_id": intent.get("task_id") or linked_worker.get("task_id"),
                "status": event.get("status"),
                "agent": canonical_agent_name(intent.get("target_display_name") or intent.get("target_agent") or linked_worker.get("agent_id")),
                "provider": intent.get("provider") or linked_worker.get("provider"),
                "reason": intent.get("reason") or linked_worker.get("reason") or (linked_worker.get("request_snapshot") or {}).get("reason"),
                "run_id": intent.get("run_id") or linked_worker.get("run_id"),
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
    del orchestrator_state
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
                or (
                    isinstance(task.get(DELIVERY_BINDING_KEY), Mapping)
                    and task.get(DELIVERY_BINDING_KEY, {}).get("kind") == "pull_request"
                )
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
        if event_status not in {"started", "waiting_approval"}:
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


def build_dashboard_bundle(
    state: dict[str, Any],
    orchestrator_state: dict[str, Any] | None,
    approval_state: dict[str, Any] | None,
) -> dict[str, Any]:
    orchestrator = orchestrator_state or {}
    approvals = approval_state or {}
    config = load_config()
    dispatch_policy = build_dispatch_policy_summary(config)
    resolver = task_resolver(state)
    task_map = resolver.active_task_map()
    archive_index = load_archive_index()
    archive_counts = archive_index.get("counts", {}) if isinstance(archive_index.get("counts"), dict) else {}
    terminal_projection = terminal_archive_projection(state)
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
    supervisor_state = orchestrator.get("supervisor") if isinstance(orchestrator.get("supervisor"), dict) else {}

    live_workers_by_task: dict[str, list[dict[str, Any]]] = {}
    for worker in live_workers:
        task_id = str(worker.get("task_id") or "").strip()
        if task_id:
            live_workers_by_task.setdefault(task_id, []).append(worker)

    ready_now = 0
    dependency_ready = 0
    in_progress = 0
    in_review = 0
    blocked = 0
    review_approved = 0
    done = int(archive_counts.get("completed") or 0)
    superseded = int(archive_counts.get(TASK_TERMINAL_SUPERSEDED) or 0)
    for terminal in terminal_projection:
        if not terminal["archive_missing"]:
            continue
        if terminal["terminal_outcome"] == TASK_TERMINAL_SUPERSEDED:
            superseded += 1
        else:
            done += 1
    for task in state.get("tasks", []):
        status = str(task.get("status") or "").lower()
        if status == "todo" and all(dependency_is_satisfied(resolver, dep_id) for dep_id in task.get("depends_on", [])):
            dependency_ready += 1
            if any(worker.get("bucket") in {"running", "pending"} for worker in live_workers_by_task.get(str(task.get("id") or ""), [])):
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
                "last_event_at": worker.get("last_event_at"),
                "last_error": worker.get("last_error"),
                "mismatch_flags": mismatch_index.get((task_id, str(worker.get("run_id") or "")), []),
                "mismatch_count": len(linked_mismatches),
                "resolution_hints": [str(item.get("resolution_hint") or "") for item in linked_mismatches if str(item.get("resolution_hint") or "")],
            }
        )

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
        "runtime_summary": {
            "supervisor_pid": supervisor_state.get("pid"),
            "heartbeat_at": supervisor_state.get("last_heartbeat_at") or orchestrator.get("last_heartbeat_at"),
            "queue_depth": len(queue_events),
            "pending_approvals": len(approvals.get("pending") or []),
            "running_workers": sum(1 for worker in live_workers if worker.get("bucket") == "running"),
            "pending_workers": sum(1 for worker in live_workers if worker.get("bucket") == "pending"),
            "mismatch_count": len(mismatches),
            "lanes": lanes,
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
            "terminal_facts": terminal_projection,
            "archive_missing_task_ids": [
                item["task_id"] for item in terminal_projection if item["archive_missing"]
            ],
        },
        "dispatch_policy": dispatch_policy,
        "worker_task_links": worker_task_links,
        "truth_mismatches": mismatches,
    }


def write_dashboard_bundle(state: dict[str, Any]) -> None:
    config = load_config()
    try:
        orchestrator_state = load_runtime_state(config)
    except KeyError:
        orchestrator_state = {}
    approval_state = load_json_file(APPROVAL_QUEUE_FILE, {"pending": [], "history": []})
    bundle = build_dashboard_bundle(state, orchestrator_state, approval_state)
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
    defer_activity_recovery: bool = False,
) -> None:
    assert_task_archive_root_binding()
    sync_canonical_document_metadata(state)
    normalize_state_agents(state)
    normalize_terminal_facts(state)
    normalize_archive_receipts(state)
    validate_state(state)
    normalize_handoffs(state)
    recompute_agents(state)
    recompute_workload(state)
    ensure_sprint_started_at(state)
    state["updated_at"] = iso_now()
    buffered = getattr(_ACTIVITY_TRANSACTION_LOCAL, "events", None)
    events = list(buffered) if isinstance(buffered, list) else []
    commit_state_with_activity_outbox(
        state, events, defer_activity_recovery=defer_activity_recovery
    )
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
    if len(args) < 3:
        raise SystemExit("Usage: assign <task-id> <owner> <reviewer> [title]")
    task_id, owner, reviewer = args[0], canonical_agent_name(args[1]), canonical_agent_name(args[2])
    title = args[3] if len(args) > 3 else os.environ.get("TASK_TITLE")
    summary_zh = os.environ.get("TASK_SUMMARY_ZH")
    assignment_next = os.environ.get("TASK_NEXT", "").strip()
    metadata = task_metadata_from_env()
    if "dev_bridge" in metadata and not bool(
        getattr(_DEV_BRIDGE_MATERIALIZATION_LOCAL, "active", False)
    ):
        raise SystemExit(
            "Bridge provenance can only be created by "
            f"{DEV_BRIDGE_BATCH_MATERIALIZE_COMMAND}."
        )
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
            "owner": owner,
            "reviewer": reviewer,
            "title": title,
        }
        config = load_config()
        try:
            validate_task_repository_scope(config, candidate)
        except (ValueError, RuntimeError) as exc:
            raise SystemExit(f"Cannot assign task {task_id}: {exc}") from exc
        enforce_artifact_conflict_admission(state, candidate)
    elif bridge is not None:
        # Existing bridge rows have their own exact packet/digest/spec replay
        # check below.  Keep its precise conflict semantics instead of treating
        # a mismatched signed packet as an ordinary metadata edit.
        pass
    elif catalog_assignment_revision:
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
        # This transition does not admit a new scope. Validate that the revised
        # candidate still matches the pre-existing exact guard, but do not
        # retroactively reject it because another already-active task later
        # acquired a conflicting scope.
        _validated_artifact_conflict_guard(candidate)
    else:
        # An existing task is already admitted.  Replacing its current runtime
        # owner/reviewer must not re-run repository artifact admission: a task
        # that later overlaps the same path would otherwise make Human/Ops
        # unable to revoke a broken assignment.  Conversely, assignment is not
        # a back door for changing the admitted source contract.
        changed_metadata = sorted(
            key for key, value in metadata.items() if task.get(key) != value
        )
        if changed_metadata:
            raise SystemExit(
                f"Task {task_id} contract metadata is immutable during reassignment: "
                + ", ".join(changed_metadata)
            )
        if title and title != task.get("title"):
            raise SystemExit(
                f"Task {task_id} title is immutable during reassignment."
            )
    timestamp = iso_now()
    old_owner = str(task.get("owner") or "") if task is not None else ""
    old_reviewer = str(task.get("reviewer") or "") if task is not None else ""
    if task is None:
        if has_terminal_fact(state, task_id):
            raise SystemExit(
                f"Task {task_id} is terminal. Create a new follow-up task instead of reusing the terminal task id."
            )
        task = {
            "id": task_id,
            "generation": 1,
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
        if current_actor() != "Human/Ops":
            raise SystemExit(
                "Only Human/Ops may change an existing task assignment."
            )
        assignment_reason = (
            os.environ.get("TASK_ASSIGN_REASON", "").strip()
            or os.environ.get("HUMAN_OPS_REASON", "").strip()
            or assignment_next
            or "Human/Ops updated the current runtime assignment."
        )
        try:
            assignment = task_machine.assignment_transition(
                old_owner,
                old_reviewer,
                owner,
                reviewer,
                actor=current_actor(),
                reason=assignment_reason,
                expected_owner=(
                    os.environ.get("TASK_ASSIGN_EXPECTED_OWNER") or None
                ),
                expected_reviewer=(
                    os.environ.get("TASK_ASSIGN_EXPECTED_REVIEWER") or None
                ),
            )
        except task_machine.TransitionError as exc:
            raise SystemExit(
                f"Task {task_id} assignment transition rejected: {exc}"
            ) from exc
        old_generation = task_assignment_generation(task)
        task["owner"] = assignment.new_owner
        task["reviewer"] = assignment.new_reviewer
        task["generation"] = old_generation + 1
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

    event = {
            "ts": timestamp,
            "agent": current_actor(),
            "type": "task_reassigned" if old_owner or old_reviewer else "assign",
            "task_id": task_id,
            "message": f"Assigned {task_id} to {owner} with reviewer {reviewer}",
        }
    if old_owner or old_reviewer:
        event = task_machine.build_assignment_activity_event(
            task_id=task_id,
            timestamp=timestamp,
            assignment=assignment,
            old_generation=old_generation,
            new_generation=task["generation"],
        )
        event["reason"] = assignment_reason
    else:
        # A first-ever assignment (no prior owner/reviewer to reassign from) is
        # structurally distinct from a reassignment, but a `done` closeout still
        # needs to verify it the same way when a commit's Reviewer trailer was
        # written before this event landed. Carry the same old/new fields a
        # task_reassigned event carries so that verification never has to parse
        # the free-text `message`.
        event["old_owner"] = ""
        event["new_owner"] = owner
        event["old_reviewer"] = ""
        event["new_reviewer"] = reviewer
    event.update(local_human_ops_audit_fields())
    append_log(event)


def apply_task_lifecycle_transition(
    task: dict[str, Any], action: str
) -> task_machine.TaskState:
    """Apply the target returned by the sole canonical lifecycle authority."""

    try:
        target = task_machine.transition(task.get("status"), action)
    except task_machine.TransitionError as exc:
        raise SystemExit(
            f"Task {task.get('id') or '?'} cannot {action}: {exc}"
        ) from exc
    task["status"] = target.value
    task.pop("failure_streak", None)
    return target


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
    apply_task_lifecycle_transition(task, "start")
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
    apply_task_lifecycle_transition(task, "progress")
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
    append_log(
        {
            "ts": timestamp,
            "agent": actor,
            "type": "note",
            "task_id": task_id,
            "message": message,
            **local_human_ops_audit_fields(),
        }
    )


def command_reopen(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: reopen <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor()
    ensure_agent(actor)
    task = get_task(state, task_id)
    if task is None:
        if has_terminal_fact(state, task_id):
            raise SystemExit(
                f"Task {task_id} is terminal and cannot be reopened in place. Create a new follow-up task that references {task_id}."
            )
        raise SystemExit(f"Unknown task: {task_id}")
    owner = canonical_agent_name(task.get("owner"))
    reviewer = canonical_agent_name(task.get("reviewer"))
    if actor not in {owner, reviewer, "Human/Ops"}:
        raise SystemExit(
            f"Only the owner ({owner}), reviewer ({reviewer}), or Human/Ops can reopen {task_id}"
        )
    preflight = consume_external_mutation_preflight("reopen", task)
    github_review_bridge = dict(preflight.get(GITHUB_REVIEW_BRIDGE_KEY) or {})
    binding_mismatch = str(
        preflight.get(REVIEW_BINDING_MISMATCH_PREFLIGHT_KEY) or ""
    ).strip()
    timestamp = iso_now()
    apply_task_lifecycle_transition(task, "reopen")
    task["last_update"] = timestamp
    task["next"] = message
    task.pop("waiting_for", None)
    # A reviewer rejection returns the work to the owner.  A subsequent
    # handoff must freeze the new deliverable instead of reusing this head.
    task.pop(DELIVERY_BINDING_KEY, None)
    task.pop(APPROVAL_BINDING_KEY, None)
    if github_review_bridge:
        task[GITHUB_REVIEW_BRIDGE_KEY] = dict(github_review_bridge)
    else:
        task.pop(GITHUB_REVIEW_BRIDGE_KEY, None)
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
            **local_human_ops_audit_fields(),
            **(
                {GITHUB_REVIEW_BRIDGE_KEY: dict(github_review_bridge)}
                if github_review_bridge
                else {}
            ),
            **(
                {REVIEW_BINDING_MISMATCH_PREFLIGHT_KEY: binding_mismatch}
                if binding_mismatch
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
    binding = resolve_handoff_delivery_binding(task, load_config())
    timestamp = iso_now()
    apply_task_lifecycle_transition(task, "handoff")
    task["last_update"] = timestamp
    task["next"] = message
    task[DELIVERY_BINDING_KEY] = deepcopy(binding)
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
    append_log(
        {
            "ts": timestamp,
            "agent": actor,
            "type": "handoff",
            "task_id": task_id,
            "message": f"Handoff to {to_agent}: {message}",
            DELIVERY_BINDING_KEY: deepcopy(binding),
        }
    )


def validate_blocker_reason(
    state: dict[str, Any],
    task: Mapping[str, Any],
    args: list[str],
) -> None:
    """Validate dependency claims synchronously; never create a second scheduler.

    Dependency readiness already belongs to ``TaskResolver`` and dispatch
    admission.  A task that declares dependencies must therefore classify a
    blocker as either ``external`` or ``task_dependency <task-id>``.  The latter
    is checked against current canonical truth before any lifecycle mutation.
    Nothing here is persisted for a future reconciler.
    """

    dependencies = [
        str(item or "").strip()
        for item in (task.get("depends_on") or [])
        if str(item or "").strip()
    ]
    if not args:
        if dependencies:
            raise SystemExit(
                "Tasks with declared dependencies must classify blockers as "
                "external or task_dependency <task-id>"
            )
        return

    reason_kind = str(args[0] or "").strip().lower()
    if reason_kind == "external":
        if len(args) != 1:
            raise SystemExit(
                "Usage: blocker <task-id> <message> <waiting-for> external"
            )
        return
    if reason_kind != "task_dependency" or len(args) != 2:
        raise SystemExit(
            "Usage: blocker <task-id> <message> <waiting-for> "
            "[external | task_dependency <task-id>]"
        )

    dependency_task_id = str(args[1] or "").strip()
    if dependency_task_id not in dependencies:
        raise SystemExit(
            f"{dependency_task_id or '(missing)'} is not a declared dependency of "
            f"{task.get('id') or '?'}"
        )
    resolver = task_resolver(state)
    dependency_status = resolver.dependency_status(dependency_task_id)
    if resolver.dependency_satisfied(dependency_task_id):
        raise SystemExit(
            f"{dependency_task_id} is already canonically satisfied "
            f"({dependency_status}); refusing to block {task.get('id') or '?'}"
        )


def command_blocker(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 3:
        raise SystemExit(
            "Usage: blocker <task-id> <message> <waiting-for> "
            "[external | task_dependency <task-id>]"
        )
    task_id, message, waiting_for = args[0], args[1], canonical_agent_name(args[2])
    actor = current_actor()
    ensure_agent(actor)
    ensure_agent(waiting_for)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("owner") != actor:
        raise SystemExit(f"Only the owner ({task.get('owner')}) can block {task_id}")
    validate_blocker_reason(state, task, args[3:])
    timestamp = iso_now()
    apply_task_lifecycle_transition(task, "block")
    task["waiting_for"] = waiting_for
    task["last_update"] = timestamp
    task["next"] = message
    mark_handoffs_done_for_actor(state, task_id, actor)
    blocker = {
        "task_id": task_id,
        "owner": actor,
        "waiting_for": waiting_for,
        "message": message,
        "status": "open",
        "created_at": timestamp,
    }
    state.setdefault("blockers", []).append(blocker)
    append_log({"ts": timestamp, "agent": actor, "type": "blocker", "task_id": task_id, "message": f"Blocked on {waiting_for}: {message}"})


def _required_reconcile_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise SystemExit(f"{name} is required for reconcile_merged_done")
    return value


def _validated_git_root(raw_root: str, *, label: str) -> Path:
    candidate = Path(os.path.expanduser(raw_root))
    if not candidate.is_absolute():
        raise SystemExit(f"{label} must be an absolute path")
    symlink_component = first_symlink_component(candidate)
    if symlink_component is not None:
        raise SystemExit(f"{label} cannot include a symlink component: {symlink_component}")
    root = candidate.resolve()
    if not root.is_dir() or git_toplevel(root) != root:
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


def _activity_events_across_sources(
    log_path: Path,
    *,
    source: str,
) -> Generator[dict[str, Any], None, None]:
    """Yield activity-log event dicts across live + archived sources, oldest first.

    Rotation moves the oldest lines out of the live tail into an immutable
    gzip archive once the log exceeds LOG_ROTATE_MAX_BYTES -- under normal
    fleet write volume that can happen within hours, not as some rare edge
    case. A governed check that needs to find one specific historical event
    (e.g. an audited task_reassigned row proving an owner/reviewer handoff)
    must not assume that event is still in the live tail: it has to walk the
    same disjoint, ordered live+archive source list
    activity_audit_source_paths_unlocked already validates, not just LOG_FILE.

    Malformed lines are not tolerated here, matching how every other reader of
    these canonical sources behaves: a source that will not parse strictly is
    an audit-integrity problem, and silently skipping it would let a tampered
    line drop a reassignment hop out of the chain.
    """

    for audit_source in activity_audit_source_paths_unlocked(log_path):
        if audit_source.suffix == ".gz":
            with gzip.open(audit_source, "rb") as handle:
                payload = handle.read()
        else:
            payload = read_regular_file_bytes(audit_source, source=source)
        for raw_line in payload.splitlines():
            if not raw_line.strip():
                continue
            event = strict_activity_json_loads(raw_line)
            if not isinstance(event, dict):
                continue
            if str(event.get("record_type") or "") == "pantheon.activity.lineage_head.v1":
                # Rotation lineage control row, not an activity event.
                continue
            yield event


def _audited_reassignment_events(
    task_id: str,
    *,
    source: str,
    unavailable_message: str,
) -> list[tuple[datetime, dict[str, Any]]]:
    """Return canonical audited `task_reassigned` events for a task, oldest first.

    Events written by the supervisor and the local Human/Ops assignment command
    both qualify because they use the shared canonical assignment writer. Their
    actor-specific `event_id` is a digest over the event payload, so a
    hand-appended activity line cannot manufacture a reassignment hop. The
    narrative `task_reassigned` lines `write_activity_log` emits alongside them
    use `from_owner`/`to_owner` keys and carry no identity digest; they are
    skipped on purpose.

    The search spans the live tail *and* the rotated archives. Reading only
    LOG_FILE made a legitimate, audited reassignment vanish the moment routine
    rotation moved it out of the tail, which permanently stranded the task at
    `done`.
    """

    try:
        events = list(_activity_events_across_sources(LOG_FILE, source=source))
    except (FileNotFoundError, RuntimeError) as exc:
        raise SystemExit(unavailable_message) from exc

    audited: list[tuple[datetime, dict[str, Any]]] = []
    for event in events:
        validated = task_machine.validate_assignment_activity_event(event)
        if validated is None or validated.task_id != task_id or not validated.old_owner:
            continue
        event_timestamp = _parse_utc_timestamp(validated.timestamp)
        if event_timestamp is None:
            continue
        audited.append((event_timestamp, validated.as_dict()))
    audited.sort(key=lambda item: item[0])
    return audited


def _walk_audited_role_chain(
    audited: list[tuple[datetime, dict[str, Any]]],
    *,
    role: str,
    start: str,
    end: str,
    failure_message: str,
) -> list[tuple[datetime, dict[str, Any]]]:
    """Return the audited hops that carry `role` from `start` to `end`.

    Reassignment is routine and repeatable -- a provider runs out of quota and
    the supervisor hands the lane to a fallback -- so a role can legitimately
    move several times before closeout. The walk therefore accepts a chain of
    any length. What it will not accept is a gap: once the chain is anchored at
    `start`, every later audited hop for this role must continue it. An audited
    hop that starts somewhere else means the audit no longer explains the
    canonical row, and the caller must fail closed rather than guess.
    """

    old_key = f"old_{role}"
    new_key = f"new_{role}"
    changes = [
        item
        for item in audited
        if canonical_agent_name(item[1].get(old_key))
        != canonical_agent_name(item[1].get(new_key))
    ]

    chain: list[tuple[datetime, dict[str, Any]]] = []
    cursor = ""
    for index, (event_timestamp, event) in enumerate(changes):
        old_value = canonical_agent_name(event.get(old_key))
        new_value = canonical_agent_name(event.get(new_key))
        if not chain:
            if old_value != start:
                # Audited history that predates the identity we start from.
                continue
        elif old_value != cursor:
            raise SystemExit(failure_message)
        if any(
            other_index != index and other_timestamp == event_timestamp
            for other_index, (other_timestamp, _) in enumerate(changes)
        ):
            raise SystemExit(
                f"Cannot verify {role} reassignment: audited {role} reassignment "
                "ordering is ambiguous."
            )
        chain.append((event_timestamp, event))
        cursor = new_value

    if not chain or cursor != end:
        raise SystemExit(failure_message)
    return chain


def _verified_reassignment_chain(
    task: dict[str, Any],
    *,
    role: str,  # "owner" or "reviewer"
    evidence_agent: str,
    current_agent: str,
) -> dict[str, Any]:
    """Prove an audited chain of reassignments carries `role` from the merged
    evidence identity to the canonical one."""

    task_id = str(task.get("id") or "").strip()
    failure_message = (
        f"Cannot reconcile task: merged evidence does not bind the canonical {role} metadata "
        "and no exact task_reassigned audit event chain explains the drift."
    )
    audited = _audited_reassignment_events(
        task_id,
        source=f"canonical {role} reassignment evidence",
        unavailable_message=(
            f"Cannot reconcile task: canonical {role} differs from merged evidence and "
            "the activity audit is unavailable."
        ),
    )
    chain = _walk_audited_role_chain(
        audited,
        role=role,
        start=canonical_agent_name(evidence_agent),
        end=canonical_agent_name(current_agent),
        failure_message=failure_message,
    )

    last_event = chain[-1][1]
    return {
        "event_id": str(last_event.get("event_id") or "").strip() or None,
        "ts": str(last_event.get("ts") or "").strip(),
        f"old_{role}": canonical_agent_name(evidence_agent),
        f"new_{role}": canonical_agent_name(current_agent),
        "hops": len(chain),
        "message": str(last_event.get("message") or "").strip(),
    }


def _verified_reviewer_reassignment(
    task: dict[str, Any],
    *,
    evidence_reviewer: str,
    current_reviewer: str,
) -> dict[str, Any]:
    """Return the exact canonical reassignment chain that explains reviewer drift."""
    return _verified_reassignment_chain(
        task,
        role="reviewer",
        evidence_agent=evidence_reviewer,
        current_agent=current_reviewer,
    )


def _verified_owner_reassignment(
    task: dict[str, Any],
    *,
    evidence_owner: str,
    current_owner: str,
) -> dict[str, Any]:
    """Return the exact canonical reassignment chain that explains owner drift."""
    return _verified_reassignment_chain(
        task,
        role="owner",
        evidence_agent=evidence_owner,
        current_agent=current_owner,
    )


def _verified_done_owner_reassignment(
    task: dict[str, Any],
    *,
    commit_owner: str,
    current_owner: str,
    commit_timestamp: str,
) -> dict[str, Any]:
    """Prove that canonical audited reassignments explain owner drift at done.

    Owner reassignment is a normal, recurring event rather than an anomaly: a
    provider hits its quota or goes unreachable and the supervisor hands the
    lane to a fallback, often swapping the reviewer in the same event and more
    than once before the task closes out. Demanding a single hop with a frozen
    reviewer is what forced Human/Ops to hand-run `reconcile_merged_done` for
    every reassigned task, so walk the whole audited chain instead. The audit
    still has to account for the drift end to end -- this is a verification
    path, not a waiver.
    """

    task_id = str(task.get("id") or "").strip()
    reviewer = canonical_agent_name(task.get("reviewer"))
    audited = _audited_reassignment_events(
        task_id,
        source="canonical done owner reassignment evidence",
        unavailable_message=(
            "Cannot finalize task: prior-owner LLM-Agent trailer requires an exact "
            "canonical audited task_reassigned event, but the activity audit is unavailable."
        ),
    )
    if not any(
        canonical_agent_name(event.get("old_owner"))
        != canonical_agent_name(event.get("new_owner"))
        for _, event in audited
    ):
        raise SystemExit(
            "Cannot finalize task: prior-owner LLM-Agent trailer requires an exact "
            "canonical audited task_reassigned event."
        )

    chain = _walk_audited_role_chain(
        audited,
        role="owner",
        start=canonical_agent_name(commit_owner),
        end=canonical_agent_name(current_owner),
        failure_message=(
            "Cannot finalize task: the latest audited owner reassignment chain does "
            "not bind the commit owner to the current owner."
        ),
    )

    delivered_at = _parse_utc_timestamp(commit_timestamp)
    if delivered_at is None:
        raise SystemExit(
            "Cannot finalize task: delivered commit timestamp is unavailable for "
            "owner reassignment ordering."
        )
    if chain[0][0] < delivered_at:
        raise SystemExit(
            "Cannot finalize task: audited owner reassignment must follow the delivered commit."
        )

    # The supervisor picks a new owner/reviewer pair in one event, so the
    # reviewer in force when the owner chain opened must still reach the
    # canonical reviewer through the same audit. A reviewer that drifts with no
    # audited hop to explain it is exactly the forgery this gate exists to stop.
    reviewer_continuity_failure = (
        "Cannot finalize task: reviewer continuity is not explained by the audited "
        "reassignment chain."
    )
    chain_opened_at, first_event = chain[0]
    reviewer_at_chain_start = canonical_agent_name(first_event.get("old_reviewer"))
    reviewer_chain: list[tuple[datetime, dict[str, Any]]] = []
    if reviewer_at_chain_start != reviewer:
        reviewer_chain = _walk_audited_role_chain(
            audited,
            role="reviewer",
            start=reviewer_at_chain_start,
            end=reviewer,
            failure_message=reviewer_continuity_failure,
        )
    elif any(
        canonical_agent_name(event.get("old_reviewer"))
        != canonical_agent_name(event.get("new_reviewer"))
        for event_timestamp, event in audited
        if event_timestamp >= chain_opened_at
    ):
        raise SystemExit(reviewer_continuity_failure)

    last_event = chain[-1][1]
    return {
        "event_id": str(last_event.get("event_id")),
        "ts": str(last_event.get("ts")),
        "old_owner": canonical_agent_name(commit_owner),
        "new_owner": canonical_agent_name(current_owner),
        "hops": len(chain),
        "reviewer": reviewer,
        "message": str(last_event.get("message") or ""),
        "commit_timestamp": commit_timestamp,
        **({"reviewer_hops": len(reviewer_chain)} if reviewer_chain else {}),
    }


def _self_consistent_event_id_matches(event: Mapping[str, Any]) -> bool:
    """Verify the generic digest `append_log` stamps on an unbespoke event.

    `_activity_event()` sets `event_id` to `ai-status-event-<sha256 of the
    rest of the event>` whenever the caller does not supply one of its own --
    the scheme a plain `assign` event gets. Recomputing and comparing catches
    a hand-edited log line the same way the canonical task-machine validator
    does for reassignment audit events.
    """

    payload = {key: value for key, value in event.items() if key != "event_id"}
    expected = "ai-status-event-" + _canonical_json_sha256(payload)
    return str(event.get("event_id") or "") == expected


LEGACY_ASSIGN_MESSAGE_RE = re.compile(
    r"^Assigned (?P<task_id>\S+) to (?P<owner>\S+) with reviewer (?P<reviewer>\S+)$"
)


def _legacy_assign_event_new_reviewer(
    event: Mapping[str, Any], task_id: str
) -> str | None:
    """Recover new_reviewer from a pre-OPS-DONE-REVIEWER-ASSIGN-AUDIT-GAP-20260818
    assign event.

    command_assign did not always stamp old_reviewer/new_reviewer on a
    first-ever assignment; before that fix landed, the event carried only a
    free-text `message` in one exact, unchanging format (the single f-string
    command_assign has ever used to build it: "Assigned {task_id} to {owner}
    with reviewer {reviewer}"). Parsing that one known historical shape here
    is precise, not a heuristic -- and the caller still requires the event's
    digest to self-consistently match, so this trusts the same audited
    record the structured fields would have, just read differently.
    """

    match = LEGACY_ASSIGN_MESSAGE_RE.match(str(event.get("message") or ""))
    if match is None or match.group("task_id") != task_id:
        return None
    return match.group("reviewer")


def _audited_initial_reviewer_assignment(
    task_id: str,
    *,
    source: str,
    unavailable_message: str,
) -> tuple[datetime, dict[str, Any]] | None:
    """Return the task's audited first-ever reviewer assignment, if any.

    A commit's Reviewer trailer can only fail to name a real prior identity
    when the task itself had no reviewer yet -- there is no task_reassigned
    hop to walk in that case, only the `assign` event that first bound one.
    Trust mirrors `_audited_reassignment_events`: only an `assign` event from
    a privileged actor (`Orchestrator`, or the governed `Human/Ops`
    local-operator path) with an explicitly empty `old_reviewer` (proving it
    was genuinely the first assignment, not a later reassignment) and a
    self-consistent event digest qualifies. `new_reviewer` is read from the
    event's own structured field when present, or recovered from its
    `message` for a legacy pre-fix event that never had the field (see
    _legacy_assign_event_new_reviewer) -- both are verified against the same
    digest before being trusted.
    """

    try:
        events = list(_activity_events_across_sources(LOG_FILE, source=source))
    except (FileNotFoundError, RuntimeError) as exc:
        raise SystemExit(unavailable_message) from exc

    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for event in events:
        if (
            event.get("type") != "assign"
            or str(event.get("task_id") or "").strip() != task_id
            or event.get("agent") not in {"Orchestrator", "Human/Ops"}
            or event.get("old_reviewer")
            or not _self_consistent_event_id_matches(event)
        ):
            continue
        new_reviewer = event.get("new_reviewer") or _legacy_assign_event_new_reviewer(
            event, task_id
        )
        if not new_reviewer:
            continue
        event_timestamp = _parse_utc_timestamp(event.get("ts"))
        if event_timestamp is None:
            continue
        candidates.append((event_timestamp, {**event, "new_reviewer": new_reviewer}))
    candidates.sort(key=lambda item: item[0])
    return candidates[0] if candidates else None


def _verified_done_reviewer_reassignment(
    task: dict[str, Any],
    *,
    commit_reviewer: str,
    current_reviewer: str,
    commit_timestamp: str,
) -> dict[str, Any]:
    """Prove that canonical audited reassignments explain reviewer drift at done.

    A delivered commit whose `LLM-Agent` trailer went stale almost always has a
    stale `Reviewer` trailer too, because the supervisor reassigns the pair
    together. Failing that trailer outright left the owner with a merged
    delivery it could never finalize, so verify it against the same audit the
    owner trailer uses.

    A commit can also land while the task had no reviewer at all yet (the
    trailer then carries whatever free-text placeholder a worker wrote, e.g.
    "pending", not a real prior identity). There is no reassignment chain to
    walk from a placeholder, so that case is instead explained by the task's
    audited first-ever reviewer assignment.
    """

    task_id = str(task.get("id") or "").strip()
    delivered_at = _parse_utc_timestamp(commit_timestamp)
    if delivered_at is None:
        raise SystemExit(
            "Cannot finalize task: delivered commit timestamp is unavailable for "
            "reviewer reassignment ordering."
        )

    commit_reviewer_canonical = canonical_agent_name(commit_reviewer)
    if commit_reviewer_canonical not in KNOWN_AGENTS:
        first_assignment = _audited_initial_reviewer_assignment(
            task_id,
            source="canonical done reviewer reassignment evidence",
            unavailable_message=(
                "Cannot finalize task: prior-reviewer Reviewer trailer requires an "
                "audited first reviewer assignment event, but the activity audit is "
                "unavailable."
            ),
        )
        if (
            first_assignment is None
            or canonical_agent_name(first_assignment[1].get("new_reviewer"))
            != canonical_agent_name(current_reviewer)
            or first_assignment[0] < delivered_at
        ):
            raise SystemExit(
                "Cannot finalize task: no audited first reviewer assignment explains "
                "the commit's unset Reviewer trailer binding to the current reviewer."
            )
        event = first_assignment[1]
        return {
            "event_id": str(event.get("event_id")),
            "ts": str(event.get("ts")),
            "old_reviewer": "",
            "new_reviewer": canonical_agent_name(current_reviewer),
            "hops": 1,
            "owner": canonical_agent_name(task.get("owner")),
            "message": str(event.get("message") or ""),
            "commit_timestamp": commit_timestamp,
        }

    audited = _audited_reassignment_events(
        task_id,
        source="canonical done reviewer reassignment evidence",
        unavailable_message=(
            "Cannot finalize task: prior-reviewer Reviewer trailer requires an exact "
            "canonical audited task_reassigned event, but the activity audit is unavailable."
        ),
    )
    chain = _walk_audited_role_chain(
        audited,
        role="reviewer",
        start=commit_reviewer_canonical,
        end=canonical_agent_name(current_reviewer),
        failure_message=(
            "Cannot finalize task: the audited reviewer reassignment chain does not "
            "bind the commit reviewer to the current reviewer."
        ),
    )

    if chain[0][0] < delivered_at:
        raise SystemExit(
            "Cannot finalize task: audited reviewer reassignment must follow the delivered commit."
        )

    last_event = chain[-1][1]
    return {
        "event_id": str(last_event.get("event_id")),
        "ts": str(last_event.get("ts")),
        "old_reviewer": commit_reviewer_canonical,
        "new_reviewer": canonical_agent_name(current_reviewer),
        "hops": len(chain),
        "owner": canonical_agent_name(task.get("owner")),
        "message": str(last_event.get("message") or ""),
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
    symlink_component = first_symlink_component(evidence_path)
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

    evidence_owner_match = re.search(
        r"^- Owner:\s*(?P<owner>.+?)\s*$",
        evidence_text,
        flags=re.MULTILINE,
    )
    evidence_owner = canonical_agent_name(
        evidence_owner_match.group("owner") if evidence_owner_match else ""
    )
    if not evidence_owner:
        raise SystemExit(
            "Cannot reconcile task: merged evidence has invalid owner metadata."
        )
    owner_reassignment = None
    if evidence_owner != owner:
        owner_reassignment = _verified_owner_reassignment(
            task,
            evidence_owner=evidence_owner,
            current_owner=owner,
        )

    evidence_reviewer_match = re.search(
        r"^- Reviewer:\s*(?P<reviewer>.+?)\s*$",
        evidence_text,
        flags=re.MULTILINE,
    )
    evidence_reviewer = canonical_agent_name(
        evidence_reviewer_match.group("reviewer") if evidence_reviewer_match else ""
    )
    if not evidence_reviewer or evidence_reviewer == evidence_owner:
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
    try:
        repository_id = validate_task_repository_scope(config, task)
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(f"Cannot reconcile task: {exc}") from exc
    repository_slug_value = repository_slug(config, repository_id)
    if not repository_slug_value:
        raise SystemExit("Cannot reconcile task: a single delivery repository is required.")
    requested_slug = normalize_github_repo_slug(
        _required_reconcile_env("RECONCILE_DELIVERY_REPOSITORY")
    )
    expected_slug = normalize_github_repo_slug(repository_slug_value)
    if requested_slug != expected_slug:
        raise SystemExit(
            "Cannot reconcile task: delivery repository does not match task artifacts "
            f"({requested_slug} != {expected_slug})."
        )
    delivery_root = _validated_git_root(
        _required_reconcile_env("RECONCILE_DELIVERY_ROOT"),
        label="RECONCILE_DELIVERY_ROOT",
    )
    actual_slug = normalize_github_repo_slug(
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
            "owner": evidence_owner,
            "canonical_owner": owner,
            "reviewer": evidence_reviewer,
            "canonical_reviewer": reviewer,
            "status": "review_approved",
            **(
                {"owner_reassignment": owner_reassignment}
                if owner_reassignment is not None
                else {}
            ),
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
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    current_reviewer = canonical_agent_name(task.get("reviewer"))
    if actor != "Human/Ops" and actor != current_reviewer:
        raise SystemExit(
            "Only Human/Ops or the task's current reviewer "
            f"({current_reviewer or 'unknown'}) can reconcile an already-merged "
            "task to done"
        )
    validate_task_lifecycle_transition(task, "reconcile_done")

    preflight = consume_external_mutation_preflight(
        "reconcile_merged_done", task
    )
    delivery = deepcopy(preflight["delivery"])
    timestamp = iso_now()
    delivery["recorded_at"] = timestamp
    verdict_ref = deepcopy(preflight.get("protected_closeout_verdict"))
    if verdict_ref is not None:
        task["protected_closeout_verdict"] = verdict_ref
    apply_task_lifecycle_transition(task, "reconcile_done")
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
    preflight = consume_external_mutation_preflight("done", task)
    done_review_file = str(preflight.get("review_file") or "")
    if done_review_file:
        task["review_file"] = done_review_file
    timestamp = iso_now()
    delivery = deepcopy(preflight["delivery"])
    delivery["recorded_at"] = timestamp
    verdict_ref = deepcopy(preflight.get("protected_closeout_verdict"))
    if verdict_ref is not None:
        task["protected_closeout_verdict"] = verdict_ref
    apply_task_lifecycle_transition(task, "done")
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
    if actor not in {owner, reviewer, "Human/Ops"}:
        raise SystemExit(
            f"Only the owner ({owner}), reviewer ({reviewer}), or Human/Ops "
            f"can supersede {task_id}"
        )
    timestamp = iso_now()
    apply_task_lifecycle_transition(task, "supersede")
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
            **local_human_ops_audit_fields(),
            **({"replacement_task_id": replacement_task_id} if replacement_task_id else {}),
        }
    )


DELIVERY_BINDING_KEY = "delivery_binding"
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
REVIEW_BINDING_MISMATCH_PREFLIGHT_KEY = "review_binding_mismatch"


class ReviewBindingMismatchError(RuntimeError):
    """A persisted review binding definitively differs from GitHub truth."""


def _github_review_bridge_module():
    scripts_git = ROOT / "scripts" / "git"
    if str(scripts_git) not in sys.path:
        sys.path.insert(0, str(scripts_git))
    try:
        import github_review_bridge
    except ImportError as exc:  # pragma: no cover - deployment packaging guard
        raise SystemExit("GitHub review bridge is unavailable") from exc
    return github_review_bridge


def _delivery_contract_payload(task: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable work contract a non-PR reviewer must inspect."""

    return dict(task_machine.delivery_contract_payload(task))


def _validated_pr_binding(binding: Mapping[str, Any], task_id: str) -> dict[str, Any]:
    raw_pr = str(binding.get("pr") or "").strip().lstrip("#")
    head_sha = str(binding.get("head_sha") or "").strip().lower()
    if not raw_pr.isdigit() or int(raw_pr) <= 0 or not APPROVAL_HEAD_SHA_RE.fullmatch(head_sha):
        raise SystemExit(f"{task_id} has an invalid pull-request delivery binding")
    return {
        "pr": int(raw_pr),
        "head_sha": head_sha,
        "head_branch": str(binding.get("head_branch") or "").strip()
        or f"task/{task_id}",
        "base": str(binding.get("base") or DEFAULT_APPROVAL_BASE_BRANCH).strip()
        or DEFAULT_APPROVAL_BASE_BRANCH,
    }


def validate_handoff_pr_delivery_binding(
    task: Mapping[str, Any],
    config: dict[str, Any],
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a PR binding only after the shared GitHub validator accepts it."""

    task_id = str(task.get("id") or "").strip()
    normalized = _validated_pr_binding(binding, task_id)
    try:
        repository_id = validate_task_repository_scope(config, dict(task))
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(
            f"PR handoff requires one delivery repository with a configured "
            f"GitHub slug for {task_id or '?'}: {exc}"
        ) from exc
    repository_slug_value = repository_slug(config, repository_id)
    if not repository_slug_value:
        raise SystemExit(
            "PR handoff requires one delivery repository with a configured "
            f"GitHub slug for {task_id or '?'}"
        )
    github_review_bridge = _github_review_bridge_module()
    try:
        validated = github_review_bridge.validate_review_binding(
            repository=repository_slug_value,
            binding=normalized,
        )
    except github_review_bridge.GitHubReviewBridgeError as exc:
        raise SystemExit(
            f"GitHub rejected the proposed delivery binding for {task_id or '?'}: {exc}"
        ) from exc
    return {
        "pr": validated.pr,
        "head_sha": validated.head_sha,
        "head_branch": validated.head_branch,
        "base": validated.base,
    }


def _discover_open_pull_request_for_branch(
    *, repository: str, head_branch: str, base: str
) -> dict[str, Any] | None:
    """Return the exact open PR for a branch pair, if exactly one exists.

    Handoff is the one place delivery identity may be discovered -- review
    must never infer it (see resolve_approval_binding). A task with no
    required_artifacts PR marker and no explicit REVIEW_PR/REVIEW_HEAD_SHA
    still frequently delivers via a real PR in practice; without this check
    its binding silently falls to artifact_contract, and approval never
    reaches GitHub (resolve_approval_binding returns no binding for that
    kind), permanently blocking the PR on branch protection with no way out
    except reopen+re-handoff.

    Any failure (missing repo config, gh error, ambiguous match) returns
    None so the caller falls through to its existing artifact_contract path
    -- this only adds a positive match, never blocks or changes behavior
    for a task that genuinely has no open PR.
    """

    owner, _, _ = repository.partition("/")
    if not owner or "/" not in repository:
        return None
    result = subprocess.run(
        [
            "gh", "api", f"repos/{repository}/pulls",
            "--method", "GET",
            "-f", f"head={owner}:{head_branch}",
            "-f", f"base={base}",
            "-f", "state=open",
        ],
        cwd=ROOT,
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
    if not isinstance(payload, list) or len(payload) != 1:
        return None
    pr = payload[0]
    if not isinstance(pr, dict):
        return None
    number = pr.get("number")
    head_sha = str((pr.get("head") or {}).get("sha") or "")
    if (
        not isinstance(number, int)
        or number <= 0
        or not APPROVAL_HEAD_SHA_RE.fullmatch(head_sha)
    ):
        return None
    return {"pr": number, "head_sha": head_sha.lower()}


def resolve_handoff_delivery_binding(
    task: Mapping[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    """Create the one delivery contract before a task becomes reviewable.

    A PR task receives a full exact-head identity.  A task with no PR receives
    a hash of its declared artifact/acceptance contract.  Both are persisted on
    the task at handoff, eliminating the old late discovery at approval.
    """

    task_id = str(task.get("id") or "").strip()
    try:
        repository_id = validate_task_repository_scope(config, dict(task))
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(
            f"Handoff requires valid repository scope for {task_id or '?'}: {exc}"
        ) from exc

    explicit_pr = bool(os.environ.get("REVIEW_PR", "").strip())
    explicit_head = bool(os.environ.get("REVIEW_HEAD_SHA", "").strip())
    if explicit_pr != explicit_head:
        raise SystemExit(
            "REVIEW_PR and REVIEW_HEAD_SHA must be supplied together for a PR handoff"
        )
    head_branch = (
        os.environ.get("REVIEW_HEAD_BRANCH", "").strip() or f"task/{task_id}"
    )
    base_branch = (
        os.environ.get("REVIEW_BASE", "").strip() or DEFAULT_APPROVAL_BASE_BRANCH
    )
    if explicit_pr:
        candidate = _validated_pr_binding(
            {
                "pr": os.environ.get("REVIEW_PR", "").strip().lstrip("#"),
                "head_sha": os.environ.get("REVIEW_HEAD_SHA", "").strip(),
                "head_branch": head_branch,
                "base": base_branch,
            },
            task_id,
        )
        return {
            "kind": "pull_request",
            **validate_handoff_pr_delivery_binding(task, config, candidate),
        }

    if requires_pr_delivery_binding(task):
        raise SystemExit(
            f"{task_id} requires a PR delivery binding at handoff. Set REVIEW_PR "
            "and REVIEW_HEAD_SHA after pushing the delivery branch; historical "
            "source_ref/github metadata is not a reviewable delivery identity."
        )

    repository_slug_value = repository_slug(config, repository_id)
    if repository_slug_value:
        discovered = _discover_open_pull_request_for_branch(
            repository=repository_slug_value,
            head_branch=head_branch,
            base=base_branch,
        )
        if discovered:
            return {
                "kind": "pull_request",
                **_validated_pr_binding(
                    {**discovered, "head_branch": head_branch, "base": base_branch},
                    task_id,
                ),
            }

    contract = _delivery_contract_payload(task)
    return {
        "kind": "artifact_contract",
        **contract,
        "contract_sha256": _canonical_json_sha256(contract),
    }


def validate_delivery_binding_for_approval(
    task: Mapping[str, Any],
    review_binding: Mapping[str, Any],
) -> None:
    """Reject a review that is not for the delivery frozen at handoff."""

    delivery = task.get(DELIVERY_BINDING_KEY)
    if not isinstance(delivery, Mapping):
        raise SystemExit(
            f"{task.get('id') or 'task'} has no handoff delivery binding; reopen "
            "and hand off the current delivery before review."
        )
    kind = str(delivery.get("kind") or "").strip()
    task_id = str(task.get("id") or "").strip()
    if kind == "pull_request":
        expected = _validated_pr_binding(delivery, task_id)
        actual = _validated_pr_binding(review_binding, task_id)
        if actual != expected:
            raise SystemExit(
                f"{task_id} review binding does not match the exact PR head frozen at handoff; "
                "reopen and hand off the new delivery head for review."
            )
        return
    if kind == "artifact_contract":
        expected = dict(delivery)
        contract = _delivery_contract_payload(task)
        if (
            expected.get("contract_sha256") != _canonical_json_sha256(contract)
            or expected.get("task_id") != contract["task_id"]
        ):
            raise SystemExit(
                f"{task_id} artifact delivery contract changed after handoff; "
                "reopen and hand off the current contract for review."
            )
        return
    raise SystemExit(f"{task_id} has an unknown delivery binding kind: {kind}")


def resolve_approval_binding(
    task: dict[str, Any],
) -> dict[str, Any]:
    """Read the delivery identity frozen at handoff for approval.

    Review never discovers or infers a delivery identity.  That would let a
    reviewer approve one head while later closeout sees a different head.
    Every reviewable task therefore arrives from handoff with one immutable
    PR or artifact contract, and reopening is the only way to replace it.
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
    if raw_head and not raw_pr:
        raise SystemExit(
            "REVIEW_HEAD_SHA was supplied without REVIEW_PR; both are required."
        )
    if raw_pr and not raw_head:
        raise SystemExit(
            "REVIEW_PR was supplied without REVIEW_HEAD_SHA; both are required."
        )
    if raw_pr and (not raw_pr.isdigit() or int(raw_pr) <= 0):
        raise SystemExit(f"REVIEW_PR must be a positive PR number, got {raw_pr!r}")
    if raw_head and not APPROVAL_HEAD_SHA_RE.fullmatch(raw_head):
        raise SystemExit(
            f"REVIEW_HEAD_SHA must be a full 40-hex commit oid, got {raw_head!r}. "
            "An abbreviated sha cannot be compared exactly."
        )

    delivery = task.get(DELIVERY_BINDING_KEY)
    if not isinstance(delivery, Mapping):
        raise SystemExit(
            f"{task_id} has no handoff delivery binding; reopen and hand off "
            "the current delivery before approval."
        )
    kind = str(delivery.get("kind") or "").strip()
    if kind == "pull_request":
        persisted = _validated_pr_binding(delivery, task_id)
        if raw_pr or raw_head:
            explicit = _validated_pr_binding(
                {
                    "pr": raw_pr,
                    "head_sha": raw_head,
                    "head_branch": head_branch,
                    "base": base_branch,
                },
                task_id,
            )
            if explicit != persisted:
                raise SystemExit(
                    f"{task_id} supplied review head does not match its handoff delivery binding; "
                    "reopen and hand off the new PR head first."
                )
        return persisted
    if kind == "artifact_contract":
        if raw_pr or raw_head:
            raise SystemExit(
                f"{task_id} is artifact-bound at handoff and cannot receive a PR review binding "
                "without a new handoff."
            )
        return {}
    raise SystemExit(f"{task_id} has an unknown delivery binding kind: {kind}")


def requires_pr_delivery_binding(task: Mapping[str, Any]) -> bool:
    """Whether the current task contract requires a pull-request delivery.

    Historical ``source_ref`` and ``github`` fields are provenance, never a
    future delivery identity.
    """

    required_artifacts = task.get("required_artifacts")
    if not isinstance(required_artifacts, list):
        return False
    for artifact in required_artifacts:
        normalized = " ".join(str(artifact or "").casefold().split())
        if normalized in {"pr", "pull request", "merge sha"}:
            return True
        if "exact-head" in normalized or "pull request" in normalized:
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
    query = urllib.parse.urlencode({"ref": head_sha})
    result = run_gh_json_command(
        ["api", "--method", "GET", f"repos/{repository}/contents/{encoded_path}?{query}"]
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

    github_review_bridge = _github_review_bridge_module()

    config = load_config()
    try:
        repository_id = validate_task_repository_scope(config, task)
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(
            f"GitHub review bridge rejected {decision} for {task.get('id') or '?'}: {exc}"
        ) from exc
    repository_slug_value = repository_slug(config, repository_id)
    if not repository_slug_value:
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
    except github_review_bridge.ReviewBindingMismatch as exc:
        raise ReviewBindingMismatchError(str(exc)) from exc
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


EXTERNAL_MUTATION_COMMANDS = frozenset(
    {"approve", "reopen", "done", "reconcile_merged_done"}
)


def task_mutation_cas_digest(task: Mapping[str, Any]) -> str:
    """Digest the exact task row inspected before external review I/O."""

    encoded = json.dumps(
        task,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_task_lifecycle_transition(task: Mapping[str, Any], action: str) -> None:
    try:
        task_machine.transition(task.get("status"), action)
    except task_machine.TransitionError as exc:
        raise SystemExit(
            f"Task {task.get('id') or '?'} cannot {action}: {exc}"
        ) from exc


def prepare_external_mutation_preflight(
    command: str,
    task: dict[str, Any],
    args: list[str],
) -> dict[str, Any]:
    """Perform slow review/delivery evidence work without canonical locks.

    The returned task digest is checked again under the exclusive task-state
    lock immediately before applying the lifecycle transition.  A concurrent
    mutation therefore invalidates the prepared result instead of committing
    evidence for a task row the reviewer did not inspect.
    """

    if command not in EXTERNAL_MUTATION_COMMANDS:
        raise RuntimeError(f"unsupported external mutation preflight: {command}")
    if len(args) < 2:
        raise SystemExit(f"Usage: {command} <task-id> <message>")
    task_id, message = args[0], args[1]
    if str(task.get("id") or "") != task_id:
        raise SystemExit(f"Unknown task: {task_id}")
    actor = current_actor()
    ensure_agent(actor)
    digest = task_mutation_cas_digest(task)
    payload: dict[str, Any] = {
        "command": command,
        "task_id": task_id,
        "task_digest": digest,
    }

    if command == "approve":
        if canonical_agent_name(task.get("reviewer")) != actor:
            raise SystemExit(
                f"Only the reviewer ({task.get('reviewer')}) can approve {task_id}"
            )
        validate_task_lifecycle_transition(task, "approve")
        review_notes = parse_delimited_env("REVIEW_NOTES_ZH")
        review_file = os.environ.get("REVIEW_FILE", "").strip()
        config = load_config()
        try:
            repository_id = validate_task_repository_scope(config, task)
        except (ValueError, RuntimeError) as exc:
            raise SystemExit(f"Cannot approve task {task_id}: {exc}") from exc
        repository_slug_value = repository_slug(config, repository_id)
        binding = resolve_approval_binding(task)
        validate_delivery_binding_for_approval(task, binding)
        if review_file and binding and (
            not repository_slug_value
            or not review_evidence_file_committed(
                repository=repository_slug_value,
                head_sha=binding["head_sha"],
                review_file=review_file,
            )
        ):
            raise SystemExit(
                f"{task_id}: REVIEW_FILE={review_file!r} was not found at the reviewed "
                f"head {binding['head_sha'][:12]} in {repository_slug_value or '?'}. "
                "The evidence manifest must already be committed and present in the PR "
                "diff before approval."
            )
        candidate = deepcopy(task)
        if review_notes:
            candidate["review_notes_zh"] = review_notes
        if review_file:
            candidate["review_file"] = review_file
        verdict_ref = validate_protected_closeout_transition(
            candidate,
            transition="review_approved",
        )
        try:
            bridge_result = (
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
        except ReviewBindingMismatchError as exc:
            raise SystemExit(
                f"GitHub rejected approval for {task_id}: {exc}. Reopen the task "
                "and hand off the actual PR head before approving it."
            ) from exc
        payload.update(
            {
                "review_notes_zh": review_notes,
                "review_file": review_file,
                APPROVAL_BINDING_KEY: dict(binding),
                GITHUB_REVIEW_BRIDGE_KEY: dict(bridge_result),
                "protected_closeout_verdict": deepcopy(verdict_ref),
            }
        )
        return payload

    if command == "reopen":
        owner = canonical_agent_name(task.get("owner"))
        reviewer = canonical_agent_name(task.get("reviewer"))
        if actor not in {owner, reviewer, "Human/Ops"}:
            raise SystemExit(
                f"Only the owner ({owner}), reviewer ({reviewer}), or Human/Ops "
                f"can reopen {task_id}"
            )
        validate_task_lifecycle_transition(task, "reopen")
        binding: dict[str, Any] = {}
        bridge_result: dict[str, Any] = {}
        if actor == reviewer:
            explicit_binding = bool(
                os.environ.get("REVIEW_PR", "").strip()
                or os.environ.get("REVIEW_HEAD_SHA", "").strip()
            )
            if explicit_binding:
                binding = resolve_approval_binding(task)
            elif isinstance(task.get(DELIVERY_BINDING_KEY), Mapping):
                binding = resolve_approval_binding(task)
            elif isinstance(task.get(APPROVAL_BINDING_KEY), Mapping):
                binding = dict(task[APPROVAL_BINDING_KEY])
            if binding:
                try:
                    bridge_result = bridge_github_review_decision(
                        task,
                        actor=actor,
                        decision="reopen",
                        message=message,
                        binding=binding,
                    )
                except ReviewBindingMismatchError as exc:
                    # A definitive PR identity mismatch is itself proof that
                    # this canonical binding cannot stay reviewable.  Reopen
                    # through the existing CAS-bound lifecycle command and let
                    # the owner hand off the actual head.  Network/CLI errors
                    # remain fail-closed in bridge_github_review_decision.
                    payload[REVIEW_BINDING_MISMATCH_PREFLIGHT_KEY] = str(exc)
        payload.update(
            {
                APPROVAL_BINDING_KEY: dict(binding),
                GITHUB_REVIEW_BRIDGE_KEY: dict(bridge_result),
            }
        )
        return payload

    if command == "reconcile_merged_done":
        current_reviewer = canonical_agent_name(task.get("reviewer"))
        if actor != "Human/Ops" and actor != current_reviewer:
            raise SystemExit(
                "Only Human/Ops or the task's current reviewer "
                f"({current_reviewer or 'unknown'}) can reconcile an already-merged "
                "task to done"
            )
        validate_task_lifecycle_transition(task, "reconcile_done")
        delivery = validate_merged_done_evidence(task)
        verdict_ref = validate_protected_closeout_transition(
            task,
            transition="done",
            consume=True,
            transition_actor=actor,
        )
        payload.update(
            {
                "delivery": deepcopy(delivery),
                "protected_closeout_verdict": deepcopy(verdict_ref),
            }
        )
        return payload

    if canonical_agent_name(task.get("owner")) != actor:
        raise SystemExit(
            f"Only the owner ({task.get('owner')}) can finalize {task_id} to done"
        )
    validate_task_lifecycle_transition(task, "done")
    candidate = deepcopy(task)
    done_review_file = os.environ.get("REVIEW_FILE", "").strip()
    if done_review_file and not candidate.get("review_file"):
        approved_head_sha = str(
            (candidate.get(APPROVAL_BINDING_KEY) or {}).get("head_sha") or ""
        ).strip()
        if approved_head_sha:
            config = load_config()
            try:
                repository_id = validate_task_repository_scope(config, candidate)
            except (ValueError, RuntimeError) as exc:
                raise SystemExit(f"Cannot finalize task {task_id}: {exc}") from exc
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
        candidate["review_file"] = done_review_file
    validate_loop_completion_claim(candidate)
    delivery = collect_done_delivery_metadata(candidate, actor)
    verdict_ref = validate_protected_closeout_transition(
        candidate,
        transition="done",
        consume=True,
        transition_actor=actor,
    )
    payload.update(
        {
            "review_file": done_review_file if not task.get("review_file") else "",
            "delivery": deepcopy(delivery),
            "protected_closeout_verdict": deepcopy(verdict_ref),
        }
    )
    return payload


@contextmanager
def bound_external_mutation_preflight(
    preflight: Mapping[str, Any] | None,
) -> Generator[None, None, None]:
    previous = getattr(_EXTERNAL_MUTATION_PREFLIGHT_LOCAL, "value", None)
    _EXTERNAL_MUTATION_PREFLIGHT_LOCAL.value = deepcopy(preflight)
    try:
        yield
    finally:
        if previous is None:
            try:
                delattr(_EXTERNAL_MUTATION_PREFLIGHT_LOCAL, "value")
            except AttributeError:
                pass
        else:
            _EXTERNAL_MUTATION_PREFLIGHT_LOCAL.value = previous


def consume_external_mutation_preflight(
    command: str, task: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate prepared external evidence under the canonical write lock."""

    value = getattr(_EXTERNAL_MUTATION_PREFLIGHT_LOCAL, "value", None)
    if not isinstance(value, Mapping):
        raise RuntimeError(
            f"{command} requires lock-free external mutation preflight"
        )
    task_id = str(task.get("id") or "")
    if value.get("command") != command or value.get("task_id") != task_id:
        raise RuntimeError(
            f"external mutation preflight identity mismatch for {command} {task_id}"
        )
    current_digest = task_mutation_cas_digest(task)
    if value.get("task_digest") != current_digest:
        raise SystemExit(
            f"{task_id} changed after external review evidence was prepared; "
            f"discarding stale {command} result and requiring a fresh attempt"
        )
    return deepcopy(dict(value))


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
    preflight = consume_external_mutation_preflight("approve", task)
    review_notes = list(preflight.get("review_notes_zh") or [])
    review_file = str(preflight.get("review_file") or "")
    binding = dict(preflight.get(APPROVAL_BINDING_KEY) or {})
    verdict_ref = deepcopy(preflight.get("protected_closeout_verdict"))
    github_review_bridge = dict(preflight.get(GITHUB_REVIEW_BRIDGE_KEY) or {})

    timestamp = iso_now()
    apply_task_lifecycle_transition(task, "approve")
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


def command_retire_archive_collision(
    state: dict[str, Any], args: list[str]
) -> None:
    """Retire one blocked row that improperly reused an archived task id.

    This is deliberately narrower than ``supersede``.  The immutable archive
    remains the authority for the original task id, while a distinct completed
    replacement proves where the later delivery was preserved.  No archive is
    rewritten and no active scope is declared complete by this repair.
    """

    if current_actor() != "Human/Ops":
        raise SystemExit(
            "Only local Human/Ops may retire an active/archive collision"
        )
    if len(args) != 3:
        raise SystemExit(
            "Usage: retire_archive_collision "
            "<task-id> <completed-replacement-task-id> <message>"
        )
    task_id = str(args[0] or "").strip()
    replacement_task_id = str(args[1] or "").strip()
    message = str(args[2] or "").strip()
    if (
        not task_id
        or not replacement_task_id
        or task_id == replacement_task_id
        or not message
    ):
        raise SystemExit("Archive-collision retirement fields are invalid")

    active = get_task(state, task_id)
    if active is None:
        raise SystemExit(f"Unknown active task: {task_id}")
    if active.get("status") != "blocked":
        raise SystemExit(
            f"Archive-collision retirement requires a blocked active task: {task_id}"
        )

    normalize_terminal_facts(state)
    normalize_archive_receipts(state)
    if has_terminal_fact(state, task_id):
        raise SystemExit(
            f"Active task {task_id} already has a terminal fact; use ordinary "
            "archive recovery instead"
        )
    if get_task(state, replacement_task_id) is not None:
        raise SystemExit(
            f"Replacement task is still active: {replacement_task_id}"
        )
    replacement_fact = state[TERMINAL_FACTS_KEY].get(replacement_task_id)
    if not isinstance(replacement_fact, Mapping) or (
        replacement_fact.get("status") != "done"
        or replacement_fact.get("terminal_outcome") != "completed"
    ):
        raise SystemExit(
            f"Replacement task is not canonically completed: {replacement_task_id}"
        )

    archived = load_archived_snapshot(task_id)
    replacement_archive = load_archived_snapshot(replacement_task_id)
    try:
        archived = _validate_status_archive_snapshot(archived)
        replacement_archive = _validate_status_archive_snapshot(
            replacement_archive
        )
    except RuntimeError as exc:
        raise SystemExit(f"Archive-collision evidence is invalid: {exc}") from exc
    archived_task = archived["task"]
    if archived.get("terminal_outcome") != "completed":
        raise SystemExit(
            f"Original archive is not completed: {task_id}"
        )
    if task_assignment_generation(active) != task_assignment_generation(
        archived_task
    ):
        raise SystemExit(
            f"Active/archive generation mismatch for {task_id}"
        )
    if not _snapshot_matches_terminal_fact(
        replacement_archive,
        replacement_fact,
        task_id=replacement_task_id,
    ):
        raise SystemExit(
            f"Replacement archive conflicts with its terminal fact: "
            f"{replacement_task_id}"
        )

    # Rebuild only the derived index, then prove the exact immutable snapshots
    # still read back before the active row is removed from TaskStore.
    rebuilt_index = rebuild_archive_index(
        recent_limit=task_archive_recent_limit()
    )
    readback_index = load_archive_index()
    if _canonical_json_sha256(readback_index) != _canonical_json_sha256(
        rebuilt_index
    ):
        raise RuntimeError("archive index readback mismatch")
    archived_readback = load_archived_snapshot(task_id)
    replacement_readback = load_archived_snapshot(replacement_task_id)
    if (
        _canonical_json_sha256(archived_readback)
        != _canonical_json_sha256(archived)
        or _canonical_json_sha256(replacement_readback)
        != _canonical_json_sha256(replacement_archive)
    ):
        raise RuntimeError("archive snapshot changed during collision retirement")

    recorded_fact = record_terminal_fact(
        state,
        archived_task,
        recorded_at=str(archived["archived_at"]),
    )
    state[ARCHIVE_RECEIPTS_KEY][task_id] = _archive_receipt_for_snapshot(
        archive_root=_archive_root_identity(),
        snapshot=archived,
        index=readback_index,
    )

    retired_at = iso_now()
    state[task_state_store.DRAIN_MARKER_KEY] = {
        "reason": message,
        "actor": current_actor(),
        "approved_at": retired_at,
        "task_ids": [task_id],
    }
    active_digest = _canonical_json_sha256(active)
    active_delivery = deepcopy(active.get(DELIVERY_BINDING_KEY))
    active_review = deepcopy(active.get(APPROVAL_BINDING_KEY))
    related_handoffs = sum(
        handoff.get("task_id") == task_id
        for handoff in state.get("handoffs", [])
        if isinstance(handoff, Mapping)
    )
    related_blockers = sum(
        blocker.get("task_id") == task_id
        for blocker in state.get("blockers", [])
        if isinstance(blocker, Mapping)
    )
    state["tasks"] = [
        task
        for task in state.get("tasks", [])
        if str(task.get("id") or "") != task_id
    ]
    state["handoffs"] = [
        handoff
        for handoff in state.get("handoffs", [])
        if str(handoff.get("task_id") or "") != task_id
    ]
    state["blockers"] = [
        blocker
        for blocker in state.get("blockers", [])
        if str(blocker.get("task_id") or "") != task_id
    ]
    append_log(
        {
            "ts": retired_at,
            "agent": current_actor(),
            "type": "active_archive_collision_retired",
            "task_id": task_id,
            "replacement_task_id": replacement_task_id,
            "message": message,
            "active_task_sha256": active_digest,
            "archived_snapshot_sha256": _canonical_json_sha256(archived),
            "replacement_snapshot_sha256": _canonical_json_sha256(
                replacement_archive
            ),
            "terminal_fact": recorded_fact,
            "removed_handoff_count": related_handoffs,
            "removed_blocker_count": related_blockers,
            **(
                {"active_delivery_binding": active_delivery}
                if isinstance(active_delivery, Mapping)
                else {}
            ),
            **(
                {"active_review_binding": active_review}
                if isinstance(active_review, Mapping)
                else {}
            ),
            **local_human_ops_audit_fields(),
        }
    )


def command_record_terminal_fact(state: dict[str, Any], args: list[str]) -> None:
    """Record one verified historical terminal dependency without archive lookup.

    This is a local Human/Ops repair primitive for a coordination root that
    was initialized after completed work.  It never changes an active row and
    cannot reopen or rewrite a recorded fact.
    """

    if current_actor() != "Human/Ops":
        raise SystemExit("Only local Human/Ops may record a terminal fact")
    if len(args) != 3:
        raise SystemExit(
            "Usage: record_terminal_fact <task-id> <generation> <completed|superseded>"
        )
    task_id = str(args[0] or "").strip()
    try:
        generation = int(args[1])
    except (TypeError, ValueError) as exc:
        raise SystemExit("Terminal fact generation must be a positive integer") from exc
    outcome = str(args[2] or "").strip().lower()
    if not task_id or generation < 1 or outcome not in {"completed", "superseded"}:
        raise SystemExit("Terminal fact fields are invalid")
    if get_task(state, task_id) is not None:
        raise SystemExit(
            f"Cannot record terminal fact for active task {task_id}; transition its canonical row instead"
        )
    normalize_terminal_facts(state)
    existing = state[TERMINAL_FACTS_KEY].get(task_id)
    fact = record_terminal_fact(
        state,
        {
            "id": task_id,
            "generation": generation,
            "status": "done",
            "terminal_outcome": outcome,
        },
    )
    if existing is None:
        append_log(
            {
                "ts": fact["recorded_at"],
                "agent": current_actor(),
                "type": "terminal_fact_recorded",
                "task_id": task_id,
                "message": f"Recorded historical terminal dependency fact ({outcome}, generation {generation})",
                **local_human_ops_audit_fields(),
            }
        )


def _read_reconciliation_snapshot(source_tasks_dir: Path, task_id: str) -> dict[str, Any] | None:
    if Path(task_id).name != task_id or task_id in {".", ".."}:
        raise SystemExit(f"Terminal fact has unsafe task id for archive reconciliation: {task_id}")
    candidate = source_tasks_dir / f"{task_id}.json"
    if not candidate.exists():
        return None
    if candidate.is_symlink() or not candidate.is_file():
        raise SystemExit(f"Archive reconciliation source is not a regular file: {candidate}")
    try:
        snapshot = json.loads(task_archive_module.read_task_archive_file_safe(candidate))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Archive reconciliation source is unreadable: {candidate}: {exc}") from exc
    try:
        task_archive_module.validate_archive_snapshot(snapshot)
    except RuntimeError as exc:
        raise SystemExit(
            f"Archive reconciliation source has an invalid snapshot: {candidate}: {exc}"
        ) from exc
    return snapshot


def _snapshot_matches_terminal_fact(
    snapshot: Mapping[str, Any],
    fact: Mapping[str, Any],
    *,
    task_id: str,
) -> bool:
    task = snapshot.get("task")
    if not isinstance(task, Mapping):
        return False
    return (
        str(snapshot.get("task_id") or "") == task_id
        and str(snapshot.get("terminal_outcome") or "")
        == str(fact.get("terminal_outcome") or "")
        and task_assignment_generation(task) == int(fact.get("generation") or 0)
    )


def command_archive_reconcile(state: dict[str, Any], args: list[str]) -> None:
    """Reconcile rich snapshots only for already-canonical terminal facts.

    This deliberately does not bulk-copy a former status root.  The current
    TaskStore terminal facts select the only records that may be imported;
    every selected source snapshot must match its fact exactly and enters via
    the normal receipt-bearing archive outbox.
    """

    if current_actor() != "Human/Ops":
        raise SystemExit("Only local Human/Ops may reconcile a terminal archive")
    if len(args) != 1:
        raise SystemExit("Usage: archive_reconcile <absolute-source-archive-tasks-dir>")
    raw_source = Path(os.path.expanduser(str(args[0] or "").strip()))
    if not raw_source.is_absolute():
        raise SystemExit("Archive reconciliation source must be an absolute tasks directory")
    symlink_component = first_symlink_component(raw_source)
    if symlink_component is not None:
        raise SystemExit(
            f"Archive reconciliation source cannot contain a symlink component: {symlink_component}"
        )
    source_tasks_dir = raw_source.resolve()
    canonical_tasks_dir = task_archive_module.ARCHIVE_TASKS_DIR.expanduser().resolve()
    if source_tasks_dir == canonical_tasks_dir:
        raise SystemExit("Archive reconciliation source must not be the canonical archive root")
    if not source_tasks_dir.is_dir():
        raise SystemExit(f"Archive reconciliation source directory is missing: {source_tasks_dir}")

    normalize_terminal_facts(state)
    normalize_archive_receipts(state)
    snapshots: list[dict[str, Any]] = []
    missing_source_ids: list[str] = []
    existing_ids: list[str] = []
    for task_id, fact in sorted(state[TERMINAL_FACTS_KEY].items()):
        canonical_snapshot = load_archived_snapshot(task_id)
        source_snapshot = _read_reconciliation_snapshot(source_tasks_dir, task_id)
        if source_snapshot is not None and not _snapshot_matches_terminal_fact(
            source_snapshot, fact, task_id=task_id
        ):
            raise SystemExit(
                f"Archive reconciliation source conflicts with canonical terminal fact: {task_id}"
            )
        if canonical_snapshot is not None:
            if source_snapshot is not None and (
                _canonical_json_sha256(canonical_snapshot)
                != _canonical_json_sha256(source_snapshot)
            ):
                raise SystemExit(
                    f"Archive reconciliation source conflicts with canonical snapshot: {task_id}"
                )
            snapshots.append(canonical_snapshot)
            existing_ids.append(task_id)
        elif source_snapshot is not None:
            snapshots.append(source_snapshot)
        else:
            missing_source_ids.append(task_id)

    if snapshots:
        state[STATUS_ARCHIVE_OUTBOX_KEY] = task_archive_module.status_archive_outbox_payload(
            snapshots,
            archive_root=_archive_root_identity(),
        )
    append_log(
        {
            "ts": iso_now(),
            "agent": current_actor(),
            "type": "terminal_archive_reconciled",
            "message": "Reconciled canonical terminal archive snapshots from an explicitly selected former root.",
            "source_archive_tasks_dir": str(source_tasks_dir),
            "reconciled_task_ids": [str(snapshot["task_id"]) for snapshot in snapshots],
            "existing_canonical_task_ids": existing_ids,
            "missing_source_task_ids": missing_source_ids,
            **local_human_ops_audit_fields(),
        }
    )


def validate_archive_review_file_target(task_id: str, review_file: str) -> tuple[str, str]:
    normalized = task_archive_module.normalize_archive_review_file(review_file)
    candidate = ROOT / normalized
    symlink_component = first_symlink_component(candidate)
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
    symlink_component = first_symlink_component(candidate)
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
    resolver = task_resolver(state)
    source = resolver.source(task_id)
    active_task = resolver.get(task_id) if source == "active" else None
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

    snapshot = load_archived_snapshot(task_id)
    if snapshot is not None:
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
        return
    if source == "terminal_fact":
        facts = state.get(TERMINAL_FACTS_KEY) or {}
        print(
            json.dumps(
                {
                    "source": "terminal_fact",
                    "task": deepcopy(facts[task_id]),
                    "archive_missing": True,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    raise SystemExit(f"Unknown task: {task_id}")


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
            validate_bound_status_command_task_authority(
                state, command, command_args
            )
            commands[command](state, command_args)


def load_dev_bridge_materialize_batch(path_value: str) -> dict[str, Any]:
    """Load one bounded, exact packet-materialization payload from a file.

    The payload names the packet id/digest once and every task row's already
    signed ``TASK_METADATA_JSON``-shaped envelope, so the caller never
    re-derives bridge provenance -- it only carries what the dispatcher
    already verified.
    """

    if not path_value:
        raise SystemExit(
            f"Usage: {DEV_BRIDGE_BATCH_MATERIALIZE_COMMAND} <absolute-payload-path>"
        )
    path = Path(os.path.expanduser(path_value))
    if not path.is_absolute():
        raise SystemExit("Dev bridge materialize batch payload path must be absolute")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SystemExit(f"Unable to inspect dev bridge materialize batch payload: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise SystemExit("Dev bridge materialize batch payload must be a non-symlink regular file")
    if metadata.st_size > 4 * 1024 * 1024:
        raise SystemExit("Dev bridge materialize batch payload exceeds 4 MiB")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Invalid dev bridge materialize batch payload: {exc}") from exc

    exact_keys = {
        "schema_version", "packet_id", "packet_digest", "actor",
        "signed_packet", "tasks",
    }
    if not isinstance(payload, dict) or set(payload) != exact_keys:
        raise SystemExit("Dev bridge materialize batch payload schema is not exact")
    if payload.get("schema_version") != DEV_BRIDGE_BATCH_SCHEMA_VERSION:
        raise SystemExit("Unsupported dev bridge materialize batch schema version")

    packet_id = payload.get("packet_id")
    packet_digest = payload.get("packet_digest")
    actor = payload.get("actor")
    if not isinstance(packet_id, str) or not packet_id.strip() or len(packet_id) > 256:
        raise SystemExit("Dev bridge materialize batch packet_id is invalid")
    if not isinstance(packet_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", packet_digest):
        raise SystemExit("Dev bridge materialize batch packet_digest must be a SHA-256 hex digest")
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 80:
        raise SystemExit("Dev bridge materialize batch actor is invalid")
    if actor.strip() != DEV_BRIDGE_BATCH_ACTOR:
        raise SystemExit(
            "Dev bridge materialize batch actor must be the trusted bridge actor"
        )

    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks or len(tasks) > DEV_BRIDGE_BATCH_MAX_TASKS:
        raise SystemExit(
            "Dev bridge materialize batch tasks must contain between 1 and "
            f"{DEV_BRIDGE_BATCH_MAX_TASKS} rows"
        )

    row_keys = {"task_id", "owner", "reviewer", "title", "assignment_next", "task_metadata"}
    normalized_tasks: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    for index, row in enumerate(tasks):
        if not isinstance(row, dict) or set(row) != row_keys:
            raise SystemExit(f"Dev bridge materialize batch row {index} schema is not exact")
        normalized: dict[str, Any] = {}
        for field, limit in (
            ("task_id", 256),
            ("owner", 80),
            ("reviewer", 80),
            ("title", 240),
        ):
            value = row[field]
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise SystemExit(f"Dev bridge materialize batch row {index} has invalid {field}")
            normalized[field] = value.strip()
        assignment_next = row["assignment_next"]
        if assignment_next is None:
            normalized["assignment_next"] = ""
        elif isinstance(assignment_next, str) and len(assignment_next) <= 4096:
            normalized["assignment_next"] = assignment_next.strip()
        else:
            raise SystemExit(
                f"Dev bridge materialize batch row {index} has invalid assignment_next"
            )
        task_metadata = row["task_metadata"]
        if not isinstance(task_metadata, dict):
            raise SystemExit(
                f"Dev bridge materialize batch row {index} task_metadata must be an object"
            )
        bridge = task_metadata.get("dev_bridge")
        if not isinstance(bridge, dict):
            raise SystemExit(
                f"Dev bridge materialize batch row {index} task_metadata.dev_bridge is required"
            )
        if str(bridge.get("packet_id") or "") != packet_id:
            raise SystemExit(
                f"Dev bridge materialize batch row {index} packet_id does not match the batch"
            )
        if str(bridge.get("packet_digest") or "") != packet_digest:
            raise SystemExit(
                f"Dev bridge materialize batch row {index} packet_digest does not match the batch"
            )
        normalized["task_metadata"] = deepcopy(task_metadata)
        task_id = normalized["task_id"]
        if task_id in seen_task_ids:
            raise SystemExit(f"Dev bridge materialize batch repeats task id: {task_id}")
        seen_task_ids.add(task_id)
        normalized_tasks.append(normalized)

    signed_packet = payload.get("signed_packet")
    if not isinstance(signed_packet, dict):
        raise SystemExit("Dev bridge materialize batch signed_packet is required")

    return {
        "schema_version": DEV_BRIDGE_BATCH_SCHEMA_VERSION,
        "packet_id": packet_id.strip(),
        "packet_digest": packet_digest,
        "actor": actor.strip(),
        "signed_packet": deepcopy(signed_packet),
        "tasks": normalized_tasks,
    }


def dev_bridge_replay_ledger(state: dict[str, Any]) -> dict[str, Any]:
    """Return the sole dev-bridge replay ledger and retire operator ledgers."""

    current = state.get(DEV_BRIDGE_CONSUMED_KEY)
    if current is None:
        current = {}
        state[DEV_BRIDGE_CONSUMED_KEY] = current
    if not isinstance(current, dict):
        raise ValueError("Dev bridge replay ledger must be a JSON object")

    legacy = state.pop(LEGACY_OPERATOR_ASSERTION_KEYS[0], None)
    if legacy is not None:
        if not isinstance(legacy, Mapping):
            raise ValueError("Legacy operator replay ledger must be a JSON object")
        for receipt_id, receipt in legacy.items():
            if str(receipt_id).startswith("bridge:"):
                current.setdefault(str(receipt_id), deepcopy(receipt))
    state.pop(LEGACY_OPERATOR_ASSERTION_KEYS[1], None)
    return current


def verify_signed_dev_bridge_packet(
    batch: Mapping[str, Any], *, state: dict[str, Any] | None = None
) -> None:
    """Verify BFF packet authority and optionally consume it atomically."""

    packet = batch.get("signed_packet")
    if not isinstance(packet, Mapping):
        raise SystemExit("Dev bridge signed packet is missing")
    signature = packet.get("signature")
    if not isinstance(signature, Mapping):
        raise SystemExit("Dev bridge signed packet signature is missing")
    if signature.get("algorithm") != "Ed25519":
        raise SystemExit("Dev bridge signed packet signature algorithm is invalid")
    raw_keys = str(os.environ.get("BRIDGE_SIGNING_PUBLIC_KEYS_JSON") or "").strip()
    if not raw_keys:
        raise SystemExit(
            "BRIDGE_SIGNING_PUBLIC_KEYS_JSON is required; no dev fallback may authorize canonical mutation"
        )
    try:
        public_keys = json.loads(raw_keys)
    except json.JSONDecodeError as exc:
        raise SystemExit("Dev bridge public key policy is invalid JSON") from exc
    if not isinstance(public_keys, Mapping) or not public_keys:
        raise SystemExit("Dev bridge public key policy must contain at least one key")
    key_id = str(signature.get("key_id") or signature.get("keyId") or "").strip()
    encoded_public_key = public_keys.get(key_id)
    if not isinstance(encoded_public_key, str):
        raise SystemExit("Dev bridge signed packet key is not trusted")
    body = deepcopy(dict(packet))
    body.pop("signature", None)
    canonical = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    try:
        public_key = base64.urlsafe_b64decode(
            encoded_public_key + "=" * (-len(encoded_public_key) % 4)
        )
        signature_value = str(signature.get("value") or "")
        signature_bytes = base64.urlsafe_b64decode(
            signature_value + "=" * (-len(signature_value) % 4)
        )
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature_bytes,
            canonical,
        )
    except (ValueError, binascii.Error, InvalidSignature) as exc:
        raise SystemExit("Dev bridge signed packet verification failed")
    digest = hashlib.sha256(canonical).hexdigest()
    if packet.get("packet_id") != batch.get("packet_id") or digest != batch.get("packet_digest"):
        raise SystemExit("Dev bridge signed packet identity binding failed")
    source = packet.get("actor")
    authorization = packet.get("operator_authorization")
    if not isinstance(source, Mapping) or not isinstance(authorization, Mapping):
        raise SystemExit("Dev bridge source and operator authorization must be separate")
    if authorization.get("capability") != "assistant.canonical.mutate":
        raise SystemExit("Dev bridge operator capability is invalid")
    if authorization.get("mfa_verified") is not True:
        raise SystemExit("Dev bridge operator authorization requires MFA")
    operator_id = str(authorization.get("operator_id") or "").strip()
    activation_id = str(authorization.get("control_activation_id") or "").strip()
    nonce = str(authorization.get("nonce") or "").strip()
    if not operator_id or not activation_id or not nonce:
        raise SystemExit("Dev bridge operator authorization binding is incomplete")
    issued = _parse_utc_timestamp(authorization.get("issued_at"))
    expires = _parse_utc_timestamp(authorization.get("expires_at"))
    now = datetime.now(timezone.utc)
    if issued is None or expires is None or expires <= issued:
        raise SystemExit("Dev bridge operator authorization lifetime is invalid")
    if (expires - issued).total_seconds() > 300 or now < issued:
        raise SystemExit("Dev bridge operator authorization is not yet valid")
    # Expiry bounds admission at the authenticated BFF boundary.  The signed
    # packet is the durable receipt; a queued packet may drain later without
    # turning supervisor wall-clock latency into an authorization failure.
    packet_tasks = packet.get("tasks")
    if not isinstance(packet_tasks, list) or len(packet_tasks) != len(batch["tasks"]):
        raise SystemExit("Dev bridge signed packet task count does not match batch")
    for index, (packet_task, row) in enumerate(zip(packet_tasks, batch["tasks"])):
        if not isinstance(packet_task, Mapping):
            raise SystemExit(f"Dev bridge signed packet task {index} is invalid")
        for field in ("id", "owner", "reviewer", "title"):
            row_field = "task_id" if field == "id" else field
            if packet_task.get(field) != row.get(row_field):
                raise SystemExit(
                    f"Dev bridge signed packet task {index} {field} binding failed"
                )
    if state is not None:
        try:
            consumed = dev_bridge_replay_ledger(state)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if state.get(DEV_BRIDGE_CONSUMED_KEY) is not consumed:
            raise SystemExit("Dev bridge replay ledger is invalid")
        cutoff = now - timedelta(days=7)
        for consumed_id, record in list(consumed.items()):
            consumed_at = (
                _parse_utc_timestamp(record.get("consumed_at"))
                if isinstance(record, Mapping)
                else None
            )
            if consumed_at is None or consumed_at < cutoff:
                consumed.pop(consumed_id, None)
        if len(consumed) >= 2048:
            ordered = sorted(
                consumed,
                key=lambda item: str(consumed[item].get("consumed_at") or ""),
            )
            for consumed_id in ordered[: len(consumed) - 2047]:
                consumed.pop(consumed_id, None)
        assertion_id = f"bridge:{batch['packet_id']}:{nonce}"
        if assertion_id in consumed:
            raise SystemExit("Dev bridge operator authorization was already consumed")
        consumed[assertion_id] = {
            "nonce": nonce,
            "task_id": batch["packet_id"],
            "action": DEV_BRIDGE_BATCH_MATERIALIZE_COMMAND,
            "operator_id": operator_id,
            "consumed_at": iso_now(),
        }


def validate_dev_bridge_batch_dependency_closure(
    state: Mapping[str, Any], batch: Mapping[str, Any]
) -> None:
    """Require every new dependency to be canonical at the batch boundary.

    Scheduler reads never fall back to the human archive.  The bridge must
    therefore reject a packet whose dependency is neither an active task, a
    durable terminal fact, nor another row in this same atomic packet.
    """

    active_ids = {
        str(task.get("id") or "").strip()
        for task in (state.get("tasks") or [])
        if isinstance(task, Mapping) and str(task.get("id") or "").strip()
    }
    terminal_ids = {
        str(task_id).strip()
        for task_id in (state.get(TERMINAL_FACTS_KEY) or {})
        if str(task_id).strip()
    }
    batch_ids = {
        str(row.get("task_id") or "").strip()
        for row in (batch.get("tasks") or [])
        if isinstance(row, Mapping) and str(row.get("task_id") or "").strip()
    }
    for row in batch.get("tasks") or []:
        if not isinstance(row, Mapping):
            raise SystemExit("Dev bridge materialize batch row is invalid")
        task_id = str(row.get("task_id") or "").strip()
        bridge = ((row.get("task_metadata") or {}).get("dev_bridge") or {})
        task_spec = bridge.get("task_spec") if isinstance(bridge, Mapping) else None
        dependencies = task_spec.get("depends_on") if isinstance(task_spec, Mapping) else None
        if not isinstance(dependencies, list):
            raise SystemExit(
                f"Dev bridge task {task_id or '(unknown)'} has invalid dependency declaration"
            )
        missing = sorted(
            {
                dependency
                for dependency in (str(item or "").strip() for item in dependencies)
                if dependency
                and dependency != task_id
                and dependency not in active_ids
                and dependency not in terminal_ids
                and dependency not in batch_ids
            }
        )
        if missing:
            raise SystemExit(
                f"Dev bridge task {task_id} has unresolved canonical dependencies: "
                + ", ".join(missing)
            )


@contextmanager
def dev_bridge_materialize_mutation_environment(row: Mapping[str, Any], actor: str):
    """Bind one packet task row's signed metadata for exactly one assign call."""

    tracked = ("AI_NAME", "TASK_METADATA_JSON", "TASK_TITLE", "TASK_NEXT")
    previous = {name: os.environ.get(name) for name in tracked}
    previous_active = getattr(_DEV_BRIDGE_MATERIALIZATION_LOCAL, "active", None)
    try:
        for name in tracked:
            os.environ.pop(name, None)
        os.environ["AI_NAME"] = actor
        os.environ["TASK_METADATA_JSON"] = json.dumps(
            row["task_metadata"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        os.environ["TASK_TITLE"] = str(row["title"])
        if row.get("assignment_next"):
            os.environ["TASK_NEXT"] = str(row["assignment_next"])
        _DEV_BRIDGE_MATERIALIZATION_LOCAL.active = True
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if previous_active is None:
            try:
                delattr(_DEV_BRIDGE_MATERIALIZATION_LOCAL, "active")
            except AttributeError:
                pass
        else:
            _DEV_BRIDGE_MATERIALIZATION_LOCAL.active = previous_active
        _clear_status_command_lease_binding()


def run_dev_bridge_materialize_batch(
    state: dict[str, Any],
    batch: Mapping[str, Any],
    *,
    commands: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Materialize every packet task against one in-memory canonical snapshot.

    A row that raises (owner==reviewer, wave-guard rejection, artifact-scope
    conflict, forged/mismatched bridge provenance, ...) propagates immediately.
    The caller's transaction never reaches the single commit at the end of the
    batch, so a second-row failure commits nothing -- not even the first row.
    """

    actor = str(batch["actor"])
    results: list[dict[str, Any]] = []
    for row in batch["tasks"]:
        task_id = str(row["task_id"])
        with dev_bridge_materialize_mutation_environment(row, actor):
            outcome = commands["assign"](
                state,
                [task_id, row["owner"], row["reviewer"], row["title"]],
            )
        results.append({"task_id": task_id, "changed": outcome is not False})
    changed_count = sum(bool(item["changed"]) for item in results)
    if changed_count not in {0, len(results)}:
        raise SystemExit(
            "Dev bridge materialize batch found a partial pre-existing packet; "
            "refusing to commit a missing-task suffix"
        )
    return results


def read_dev_bridge_materialized_batch(
    state: dict[str, Any],
    batch: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Validate one whole packet directly from authoritative task state.

    This deliberately ignores live top-level owner/reviewer routing. The
    originally signed assignment and every other immutable packet field remain
    frozen inside ``dev_bridge`` and must match the batch payload byte-for-byte.
    """

    results: list[dict[str, Any]] = []
    immutable_fields = {
        "id": "id",
        "title": "title",
        "phase": "phase",
        "depends_on": "depends_on",
        "artifacts": "artifacts",
        "acceptance": "acceptance",
        "summary": "summary_zh",
    }
    for row in batch["tasks"]:
        task_id = str(row["task_id"])
        task = get_task(state, task_id)
        if task is None:
            raise SystemExit(
                f"Dev bridge materialize readback task is missing: {task_id}"
            )
        metadata = deepcopy(row["task_metadata"])
        expected_bridge = _bridge_assignment_from_metadata(
            metadata,
            task_id=task_id,
            owner=canonical_agent_name(str(row["owner"])),
            reviewer=canonical_agent_name(str(row["reviewer"])),
            title=str(row["title"]),
        )
        if expected_bridge is None or task.get("dev_bridge") != expected_bridge:
            raise SystemExit(
                f"Dev bridge materialize readback provenance mismatch: {task_id}"
            )
        signed_spec = expected_bridge["task_spec"]
        for spec_field, task_field in immutable_fields.items():
            expected = signed_spec.get(spec_field)
            observed = task.get(task_field)
            if spec_field in {"depends_on", "artifacts", "acceptance"}:
                expected = list(expected or [])
                observed = list(observed or []) if isinstance(observed, list) else observed
            if observed != expected:
                raise SystemExit(
                    "Dev bridge materialize readback immutable task-spec mismatch: "
                    f"{task_id}.{spec_field}"
                )
        results.append(
            {
                "taskId": task_id,
                "source": "active",
                "taskSpecHash": expected_bridge["task_spec_hash"],
            }
        )
    return results


def canonical_external_mutation_preflight(
    command: str,
    args: list[str],
) -> dict[str, Any]:
    """Snapshot one task under shared locks, then do all external I/O unlocked."""

    task_id = args[0] if args else ""

    def read_task_snapshot() -> dict[str, Any]:
        with canonical_task_state_lock(shared=True):
            with authoritative_task_state_transaction():
                state = load_state()
                task = get_task(state, task_id)
                if task is None:
                    if command == "reopen" and has_terminal_fact(state, task_id):
                        raise SystemExit(
                            f"Task {task_id} is terminal and cannot be reopened in place. "
                            f"Create a new follow-up task that references {task_id}."
                        )
                    raise SystemExit(f"Unknown task: {task_id}")
                return deepcopy(task)

    config = load_config()
    with runtime_state_lock(config, shared=True):
        validate_active_status_command_lease(command, args)
        task_snapshot = read_task_snapshot()

    # No runtime, task-state, or audit lock is held beyond this point.
    return prepare_external_mutation_preflight(command, task_snapshot, args)


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
        "reconcile_merged_done": command_reconcile_merged_done,
        "supersede": command_supersede,
        "retire_archive_collision": command_retire_archive_collision,
        "approve": command_approve,
        "record_terminal_fact": command_record_terminal_fact,
        "archive_reconcile": command_archive_reconcile,
        "archive_correct_review_file": command_archive_correct_review_file,
        "attach_proof_ownership": command_attach_proof_ownership,
        "sync": command_sync,
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

    if command == DEV_BRIDGE_BATCH_MATERIALIZE_COMMAND:
        if len(args) != 1:
            raise SystemExit(
                f"Usage: {DEV_BRIDGE_BATCH_MATERIALIZE_COMMAND} <absolute-payload-path>"
            )
        # Same lock order as every other governed mutation:
        # runtime_admission(shared) -> task_state(exclusive) -> activity_audit.
        # Every packet task row is applied to one in-memory
        # snapshot and reaches the journal/status file through exactly one
        # save at the bottom of the transaction, so a failure on any row -- or
        # the process dying before that save -- leaves zero rows committed.
        batch = load_dev_bridge_materialize_batch(args[0])
        verify_signed_dev_bridge_packet(batch)
        committed_state: dict[str, Any] | None = None
        commit_happened = False
        config = load_config()
        with runtime_state_lock(config, shared=True):
            with canonical_task_state_lock(shared=False):
                with authoritative_task_state_transaction():
                    state = load_state()
                    verify_signed_dev_bridge_packet(batch, state=state)
                    validate_dev_bridge_batch_dependency_closure(state, batch)
                # Receipt, archive, activity and dashboard projections are
                # audit-only for packet materialization. Do not recover them
                # inside this command: recovery would add an unrelated journal
                # event and make a successful batch larger than one commit (or
                # make an immediate exact retry larger than zero). Preserve any
                # valid pending activity events in the next durable outbox.
                    pending_activity = state.get(STATUS_ACTIVITY_OUTBOX_KEY)
                    pending_events: list[dict[str, Any]] = []
                    if pending_activity not in (None, {}, []):
                        pending_events = deepcopy(
                            _validate_status_activity_outbox(pending_activity)["events"]
                        )
                    with buffer_activity_events() as activity_events:
                        activity_events.extend(pending_events)
                        results = run_dev_bridge_materialize_batch(
                            state, batch, commands=commands
                        )
                    # The one-shot operator authorization is itself canonical
                    # state, so even a fully pre-existing packet commits its
                    # nonce consumption exactly once. The activity-log outbox
                    # remains deferred so this is still one canonical commit.
                        sync_all(
                            state,
                            refresh_views=False,
                            defer_activity_recovery=True,
                        )
                        commit_happened = True
                    if commit_happened:
                        committed_state = deepcopy(state)
        if committed_state is not None:
            refresh_derived_status_views_if_current(committed_state)
        print(
            json.dumps(
                {
                    "status": "committed" if commit_happened else "replayed",
                    "packetId": batch["packet_id"],
                    "packetDigest": batch["packet_digest"],
                    "taskCount": len(results),
                    "taskIds": [item["task_id"] for item in results],
                    "changedTaskIds": [
                        item["task_id"] for item in results if item["changed"]
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
        return 0

    if command == DEV_BRIDGE_BATCH_READBACK_COMMAND:
        if len(args) != 1:
            raise SystemExit(
                f"Usage: {DEV_BRIDGE_BATCH_READBACK_COMMAND} <absolute-payload-path>"
            )
        if str(os.environ.get(TASK_STATE_STORE_MODE_ENV) or "").strip().lower() != "authoritative":
            raise SystemExit(
                "Dev bridge materialize readback requires authoritative task-state mode"
            )
        batch = load_dev_bridge_materialize_batch(args[0])
        try:
            with canonical_task_state_lock(shared=True, nonblocking=True):
                with authoritative_task_state_transaction():
                    state = load_state()
                    results = read_dev_bridge_materialized_batch(state, batch)
                    transaction = getattr(
                        _TASK_STATE_TRANSACTION_LOCAL, "transaction", None
                    )
                    snapshot = (
                        transaction.load_snapshot()
                        if transaction is not None
                        else load_snapshot(
                            _task_state_event_path("authoritative")
                        )
                    )
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
        pending_projections = [
            key
            for key in (STATUS_ARCHIVE_OUTBOX_KEY, STATUS_ACTIVITY_OUTBOX_KEY)
            if state.get(key) not in (None, {}, [])
        ]
        print(
            json.dumps(
                {
                    "status": "verified",
                    "packetId": batch["packet_id"],
                    "packetDigest": batch["packet_digest"],
                    "taskIds": [item["taskId"] for item in results],
                    "tasks": results,
                    "pendingAuditProjections": pending_projections,
                    "checkpoint": {
                        "eventCount": snapshot.get("event_count"),
                        "lastEventId": snapshot.get("last_event_id"),
                        "stateSha256": snapshot.get("state_sha256"),
                    },
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
            config = load_config()
            with runtime_state_lock(config, shared=True):
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

    external_preflight = (
        canonical_external_mutation_preflight(command, args)
        if command in EXTERNAL_MUTATION_COMMANDS
        else None
    )

    def run_mutation() -> dict[str, Any] | None:
        state = load_state()
        if local_human_ops_requested():
            dev_bridge_replay_ledger(state)
        validate_bound_status_command_task_authority(state, command, args)
        recover_status_archive_outbox(state)
        recover_status_activity_outbox(state)
        with bound_external_mutation_preflight(external_preflight):
            with buffer_activity_events():
                command_result = commands[command](state, args)
                if command_result is False:
                    return None
                sync_all(state, refresh_views=False)
        return deepcopy(state)

    committed_state: dict[str, Any] | None
    config = load_config()
    with runtime_state_lock(config, shared=True):
        if str(os.environ.get("ORCH_RUN_ID") or "").strip():
            validate_active_status_command_lease(command, args)
        with canonical_task_state_lock(shared=False):
            if not str(os.environ.get("ORCH_RUN_ID") or "").strip():
                validate_active_status_command_lease(command, args)
            with authoritative_task_state_transaction():
                committed_state = run_mutation()
    if committed_state is not None:
        refresh_derived_status_views_if_current(committed_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
