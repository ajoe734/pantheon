#!/usr/bin/env python3
from __future__ import annotations

import gzip
import fcntl
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import hashlib
import urllib.error
import urllib.request
from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import local
from typing import Any, Mapping, Generator, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DIR = ROOT / ".orchestrator"
TASK_STATE_STORE_MODE_ENV = "PANTHEON_TASK_STATE_STORE_MODE"
TASK_STATE_EVENT_LOG_ENV = "PANTHEON_TASK_STATE_EVENT_LOG"
CANONICAL_TASK_STATE_IDENTITY_ENV = "PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON"
DEFAULT_CONFIG_PATH = ORCHESTRATOR_DIR / "config.json"
LOCAL_CONFIG_PATH = ORCHESTRATOR_DIR / "config.local.json"
CLAUDE_OAUTH_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLAUDE_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CLAUDE_OAUTH_SCOPES = (
    "user:profile",
    "user:inference",
    "user:sessions:claude_code",
    "user:mcp_servers",
    "user:file_upload",
)
CLAUDE_OAUTH_REFRESH_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://claude.ai",
    "Referer": "https://claude.ai/",
    "User-Agent": "claude-code/2.1.117",
}


RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_VERSION = 1
RUNTIME_TASK_AUDIT_LOCK_PROTOCOL_ID = "pantheon-runtime-task-audit-lock-v1"
RUNTIME_TASK_AUDIT_LOCK_ORDER = (
    "runtime_admission",
    "task_state",
    "activity_audit",
)
_LOCK_RANKS = {
    name: index
    for index, name in enumerate(RUNTIME_TASK_AUDIT_LOCK_ORDER, start=1)
}
_STABLE_LOCK_LOCAL = local()


def _assert_no_symlink_components(path: str | Path, *, source: str) -> Path:
    """Return an absolute lexical path after rejecting every existing symlink.

    Resolving before validation hides ancestor symlinks and can move the
    activity root, its lock, or an archive/control directory outside the
    caller-selected status root. Missing suffix components are allowed so the
    same helper can guard paths that are about to be created.
    """

    absolute = Path(path).expanduser().absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise RuntimeError(f"{source} path contains a symlink: {current}")
    return absolute


def _reset_stable_lock_state_after_fork() -> None:
    """Drop inherited lock handles without unlocking the parent's file description."""

    held = getattr(_STABLE_LOCK_LOCAL, "held", None)
    if isinstance(held, dict):
        closed: set[int] = set()
        for entry in held.values():
            handle = entry.get("handle") if isinstance(entry, dict) else None
            if handle is None or id(handle) in closed:
                continue
            closed.add(id(handle))
            try:
                # The child inherited the same open-file description.  Closing
                # its duplicate leaves the parent's descriptor (and flock)
                # intact; LOCK_UN here would incorrectly release the parent.
                handle.close()
            except OSError:
                pass
    _STABLE_LOCK_LOCAL.held = {}
    _STABLE_LOCK_LOCAL.stack = []
    _STABLE_LOCK_LOCAL.pid = os.getpid()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_stable_lock_state_after_fork)


def _stable_lock_state() -> tuple[dict[str, dict[str, Any]], list[str]]:
    current_pid = os.getpid()
    inherited_pid = getattr(_STABLE_LOCK_LOCAL, "pid", current_pid)
    if inherited_pid != current_pid:
        # Defense in depth for runtimes where the at-fork callback was not
        # installed or a process image inherited state before registration.
        _reset_stable_lock_state_after_fork()
    _STABLE_LOCK_LOCAL.pid = current_pid
    held = getattr(_STABLE_LOCK_LOCAL, "held", None)
    if held is None:
        held = {}
        _STABLE_LOCK_LOCAL.held = held
    stack = getattr(_STABLE_LOCK_LOCAL, "stack", None)
    if stack is None:
        stack = []
        _STABLE_LOCK_LOCAL.stack = stack
    return held, stack


def _assert_stable_lock_identity(lock_path: Path, descriptor: int) -> None:
    descriptor_stat = os.fstat(descriptor)
    if not stat.S_ISREG(descriptor_stat.st_mode):
        raise RuntimeError(
            f"stable lock sidecar must be a regular file: {lock_path}"
        )
    path_stat = lock_path.lstat()
    if (
        stat.S_ISLNK(path_stat.st_mode)
        or path_stat.st_dev != descriptor_stat.st_dev
        or path_stat.st_ino != descriptor_stat.st_ino
    ):
        raise RuntimeError(f"stable lock sidecar changed while opening: {lock_path}")


def _trace_stable_lock(action: str, plane: str, path: Path) -> None:
    """Append an optional process-test trace without touching canonical audit."""

    trace_value = (
        os.environ.get("PANTHEON_RUNTIME_LOCK_TRACE")
        or os.environ.get("LOOP_TEST_LOCK_TRACE")
        or ""
    ).strip()
    if not trace_value:
        return
    trace_path = Path(trace_value).expanduser()
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"{action}:{plane}:{os.getpid()}:{path}\n".encode("utf-8")
    descriptor = os.open(trace_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


import errno

class LockContentionError(BlockingIOError):
    """Raised when a non-blocking lock request fails due to contention."""
    pass

@contextmanager
def stable_sidecar_lock(
    path: str | Path,
    *,
    plane: str,
    shared: bool = False,
    nonblocking: bool = False,
):
    """Hold one never-replaced flock sidecar and enforce the global lock order.

    Locks are process-thread re-entrant for the same path. A shared lock cannot
    be upgraded in place, and acquiring an earlier or peer plane while a later
    plane is held fails before entering the kernel, preventing hidden reverse
    ordering in nested writer helpers.
    """

    if plane not in _LOCK_RANKS:
        raise ValueError(f"unknown canonical lock plane: {plane}")
    requested_path = Path(path).expanduser()
    # Resolve the directory, not the lock leaf.  Resolving the complete path
    # would silently follow a sidecar symlink and let a retargeted link move
    # future contenders to a different inode.
    lock_path = requested_path.parent.resolve() / requested_path.name
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.is_symlink():
        raise RuntimeError(f"stable lock sidecar cannot be a symlink: {lock_path}")
    key = str(lock_path)
    held, stack = _stable_lock_state()
    existing = held.get(key)
    if existing is not None:
        if bool(existing["shared"]) and not shared:
            raise RuntimeError(
                f"cannot upgrade shared {plane} lock to exclusive: {lock_path}"
            )
        existing["depth"] += 1
        stack.append(key)
        try:
            yield existing["handle"]
        finally:
            stack.pop()
            existing["depth"] -= 1
        return

    rank = _LOCK_RANKS[plane]
    active_ranks = [int(held[item]["rank"]) for item in stack if item in held]
    if active_ranks and rank <= max(active_ranks):
        active_planes = [str(held[item]["plane"]) for item in stack if item in held]
        raise RuntimeError(
            "canonical lock order violation: "
            f"cannot acquire {plane} after {','.join(active_planes)}"
        )

    flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        _assert_stable_lock_identity(lock_path, descriptor)
        handle = os.fdopen(descriptor, "a+", encoding="utf-8")
    except BaseException:
        os.close(descriptor)
        raise
    operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    if nonblocking:
        operation |= fcntl.LOCK_NB
    _trace_stable_lock("request", plane, lock_path)
    try:
        try:
            fcntl.flock(handle.fileno(), operation)
        except (BlockingIOError, OSError) as exc:
            if nonblocking and getattr(exc, "errno", None) in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise LockContentionError(
                    errno.EAGAIN,
                    f"lock contention on {lock_path}: {exc}",
                    str(lock_path),
                ) from exc
            raise
        # A pathname swap between open(2) and flock(2) would otherwise leave
        # this process holding an orphaned inode while the next contender opens
        # the replacement.  Verify the pathname still names our locked FD.
        _assert_stable_lock_identity(lock_path, handle.fileno())
    except BaseException:
        handle.close()
        raise
    _trace_stable_lock("acquire", plane, lock_path)
    held[key] = {
        "handle": handle,
        "depth": 1,
        "rank": rank,
        "plane": plane,
        "shared": bool(shared),
    }
    stack.append(key)
    try:
        yield handle
    finally:
        stack.pop()
        entry = held.pop(key)
        _trace_stable_lock("release", plane, lock_path)
        try:
            fcntl.flock(entry["handle"].fileno(), fcntl.LOCK_UN)
        finally:
            entry["handle"].close()


def _canonical_data_parent(data_file: str | Path, *, plane: str) -> Path:
    data_path = _assert_no_symlink_components(
        data_file,
        source=f"canonical {plane} data",
    )
    # Atomic replacement changes the data-file inode.  Deriving the sidecar
    # from the resolved data leaf would therefore be unsafe when that leaf is
    # a symlink: replacing it changes where the next caller resolves the lock.
    # Resolve only the stable parent directory.
    return data_path.parent


def canonical_task_state_lock_path(status_file: str | Path) -> Path:
    root = _canonical_data_parent(status_file, plane="task-state")
    return root / ".orchestrator" / "task-state.lock"


@contextmanager
def canonical_task_state_lock_file(
    status_file: str | Path,
    *,
    shared: bool = False,
    nonblocking: bool = False,
):
    with stable_sidecar_lock(
        canonical_task_state_lock_path(status_file),
        plane="task_state",
        shared=shared,
        nonblocking=nonblocking,
    ) as handle:
        yield handle


def activity_audit_lock_path(activity_file: str | Path) -> Path:
    root = _canonical_data_parent(activity_file, plane="activity-audit")
    return root / ".orchestrator" / "activity-audit.lock"


@contextmanager
def activity_audit_lock_file(
    activity_file: str | Path,
    *,
    shared: bool = False,
    nonblocking: bool = False,
):
    with stable_sidecar_lock(
        activity_audit_lock_path(activity_file),
        plane="activity_audit",
        shared=shared,
        nonblocking=nonblocking,
    ) as handle:
        yield handle


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


WORKER_PROCESS_GENERATION_SCHEMA_VERSION = 1
WORKER_PROCESS_GENERATION_PREFIX = "worker-process-generation-sha256:"


def worker_process_generation_id(
    *,
    task_id: str,
    worker_run_id: str,
    queue_event_id: str,
    pid: int,
    pid_start_ticks: int,
) -> str:
    payload = {
        "schema_version": WORKER_PROCESS_GENERATION_SCHEMA_VERSION,
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
    return WORKER_PROCESS_GENERATION_PREFIX + hashlib.sha256(encoded).hexdigest()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return deepcopy(default)
    last_error: json.JSONDecodeError | None = None
    for attempt in range(10):
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return deepcopy(default)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            sanitized = re.sub(r"//.*?$", "", text, flags=re.MULTILINE)
            sanitized = re.sub(r"/\*.*?\*/", "", sanitized, flags=re.DOTALL)
            sanitized = re.sub(r",(\s*[}\]])", r"\1", sanitized)
            if sanitized != text:
                try:
                    return json.loads(sanitized)
                except json.JSONDecodeError as sanitized_exc:
                    last_error = sanitized_exc
            else:
                last_error = exc
            if attempt < 9:
                time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise last_error
    return deepcopy(default)


def write_json(path: Path, payload: Any) -> None:
    ensure_parent(path)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    os.replace(temp_path, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    last_error: json.JSONDecodeError | None = None
    for attempt in range(10):
        rows: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
            return rows
        except json.JSONDecodeError as exc:
            last_error = exc
            if attempt < 9:
                time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise last_error
    return []


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def deep_merge(base: Any, overlay: Any) -> Any:
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = deepcopy(base)
        for key, value in overlay.items():
            if key in merged:
                merged[key] = deep_merge(merged[key], value)
            else:
                merged[key] = deepcopy(value)
        return merged
    if isinstance(base, list) and isinstance(overlay, list):
        return deepcopy(overlay)
    return deepcopy(overlay)


def resolve_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path


def first_symlink_component(path: Path) -> Path | None:
    if ".." in path.parts:
        raise RuntimeError(f"Path contains parent directory references (..): {path}")
    current = Path(path.anchor)
    parts = path.parts[1:] if path.is_absolute() else path.parts
    for part in parts:
        current = current / part
        try:
            if current.is_symlink():
                return current
            if not current.exists():
                return None
        except OSError:
            return current
    return None


def relpath(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def runtime_sidecar_dir() -> Path:
    """Return the external sidecar root for governed runtimes when available."""

    status_root_value = os.environ.get("PANTHEON_STATUS_ROOT", "").strip()
    if not status_root_value:
        return ORCHESTRATOR_DIR
    status_root = Path(status_root_value).expanduser()
    if not status_root.is_absolute():
        raise ValueError("PANTHEON_STATUS_ROOT must be absolute for runtime sidecars")
    return status_root / ".orchestrator"


def evidence_dir(config: dict[str, Any]) -> Path:
    configured = config.get("paths", {}).get("evidence_dir")
    if configured:
        path = resolve_path(configured)
        if path is not None:
            return path
    return runtime_sidecar_dir() / "evidence"


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    config_file = resolve_path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if config_file is None:
        raise RuntimeError("Unable to resolve orchestrator config path")
    config = load_json(config_file, default={})
    if LOCAL_CONFIG_PATH.exists():
        config = deep_merge(config, load_json(LOCAL_CONFIG_PATH, default={}))
    return config


def config_path(config: dict[str, Any], key: str, default: str | None = None) -> Path:
    value = config.get("paths", {}).get(key, default)
    path = resolve_path(value)
    if path is None:
        raise KeyError(f"Missing config path for {key}")
    return path


def repo_root_for_config(config: dict[str, Any]) -> Path:
    return config_path(config, "status_file").parents[0]


def config_status_root(config: dict[str, Any]) -> Path:
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    if "status_file" in paths:
        try:
            return config_path(config, "status_file").parent.resolve()
        except KeyError:
            pass

    if "state_file" in paths:
        try:
            state_path = config_path(config, "state_file").resolve()
            if state_path.parent.name == ".orchestrator":
                return state_path.parent.parent.resolve()
            return state_path.parent.resolve()
        except KeyError:
            pass

    return ROOT.resolve()


def canonical_task_state_identity_for_paths(
    *,
    status_root: Path,
    event_log: Path,
) -> dict[str, Any]:
    """Describe the one V2 state domain without relocating its journal.

    The canonical projection and archive intentionally live below the
    coordination root, while the append-only journal lives in the external
    runtime directory.  They are nevertheless one state domain and every
    status-command environment carries this exact binding.
    """

    root = _assert_no_symlink_components(status_root, source="canonical status root")
    journal = _assert_no_symlink_components(event_log, source="task-state event log")
    status_file = root / "ai-status.json"
    archive_root = root / "ai-task-archive"
    if journal == root or root in journal.parents:
        raise RuntimeError(
            "task-state event log must remain outside the canonical status root"
        )
    payload = {
        "schema_version": 1,
        "status_root": str(root),
        "status_file": str(status_file),
        "archive_root": str(archive_root),
        "event_log": str(journal),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {**payload, "identity_sha256": hashlib.sha256(encoded).hexdigest()}


def canonical_task_state_identity(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return the live V2 projection/journal/archive identity from config."""

    status_root = config_status_root(dict(config))
    status_file = config_path(dict(config), "status_file").resolve()
    if status_file != status_root / "ai-status.json":
        raise RuntimeError(
            "authoritative task-state status_file must be canonical "
            f"ai-status.json: {status_file}"
        )
    store = config.get("task_state_store")
    if not isinstance(store, Mapping):
        raise RuntimeError("authoritative task-state store configuration is required")
    mode = str(store.get("mode") or "").strip().lower()
    if mode != "authoritative":
        raise RuntimeError("task_state_store.mode must be authoritative")
    raw_event_log = str(store.get("event_log") or "").strip()
    if not raw_event_log:
        raise RuntimeError("authoritative task-state store requires an event_log")
    event_log = Path(os.path.expanduser(raw_event_log))
    if not event_log.is_absolute():
        raise RuntimeError(
            "authoritative task-state store requires a provisioned absolute event_log"
        )
    return canonical_task_state_identity_for_paths(
        status_root=status_root,
        event_log=event_log,
    )


def canonical_task_state_identity_from_environment(
    *,
    status_root: Path,
    event_log: Path,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate the supervisor-issued binding for one status command.

    This is deliberately an integrity binding, not another policy source: the
    supervisor renders it from the live config and status commands only verify
    that their root, archive and journal still match that one identity.
    """

    env = environment if environment is not None else os.environ
    raw = str(env.get(CANONICAL_TASK_STATE_IDENTITY_ENV) or "").strip()
    if not raw:
        raise RuntimeError(
            f"{CANONICAL_TASK_STATE_IDENTITY_ENV} is required in authoritative mode"
        )
    try:
        supplied = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{CANONICAL_TASK_STATE_IDENTITY_ENV} must be valid JSON"
        ) from exc
    expected = canonical_task_state_identity_for_paths(
        status_root=status_root,
        event_log=event_log,
    )
    if not isinstance(supplied, dict) or supplied != expected:
        raise RuntimeError(
            "canonical task-state identity mismatch between status root, archive root, "
            "and event log"
        )
    return expected


def resolved_coordinator_status_root(config: dict[str, Any]) -> Path:
    env_val = os.environ.get("PANTHEON_STATUS_ROOT")
    if env_val and env_val.strip():
        return Path(os.path.expanduser(env_val.strip())).resolve()
    return config_status_root(config)


def _expand_workspace_path(value: Any, *, base: Path) -> Path:
    path = Path(os.path.expanduser(str(value)))
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def delivery_workspace_root(config: dict[str, Any], metadata: dict[str, Any] | None = None) -> Path:
    repo_root = repo_root_for_config(config)
    raw_path = (metadata or {}).get("workspace_path")
    if raw_path:
        return _expand_workspace_path(raw_path, base=repo_root)
    return repo_root


def delivery_status_root(config: dict[str, Any], metadata: dict[str, Any] | None = None) -> Path:
    repo_root = repo_root_for_config(config)
    raw_path = (metadata or {}).get("status_root")
    if raw_path:
        return _expand_workspace_path(raw_path, base=repo_root)
    return repo_root


def supervisor_issued_access_roots(metadata: dict[str, Any] | None) -> list[Path]:
    """Return absolute roots that the supervisor issued for governed task work.

    Workers limit file access to their working directory unless extra roots
    are declared explicitly. Workers need read access to task briefs in the
    canonical status root and execute access to the immutable command runtime
    in order to report lifecycle transitions through ``ai-status.sh``.
    Only supervisor-issued metadata is accepted here; task context and target
    paths are intentionally not promoted into additional access roots.
    """

    metadata = metadata or {}
    command_runtime = metadata.get("status_command_runtime") or {}
    candidates = [metadata.get("status_root")]
    if isinstance(command_runtime, dict):
        candidates.append(command_runtime.get("command_root"))

    roots: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        value = str(candidate or "").strip()
        if not value:
            continue
        path = Path(os.path.expanduser(value))
        if not path.is_absolute():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        roots.append(resolved)
    return roots


STATUS_COMMAND_ROOT_ENV = "PANTHEON_COMMAND_ROOT"
STATUS_COMMAND_SHA_ENV = "PANTHEON_COMMAND_RUNTIME_SHA"
STATUS_COMMAND_REMOTE_ENV = "PANTHEON_COMMAND_REMOTE"
STATUS_COMMAND_BASE_REF_ENV = "PANTHEON_COMMAND_BASE_REF"


def git_stdout(cwd: Path, args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "git command failed").strip()
        raise RuntimeError(detail)
    return proc.stdout.strip()


def git_toplevel(path: Path) -> Path | None:
    try:
        top = git_stdout(path, ["rev-parse", "--show-toplevel"])
    except RuntimeError:
        return None
    return Path(top).resolve() if top else None


def normalize_github_repo_slug(value: str | None) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    candidate = candidate.removesuffix(".git")
    if candidate.startswith("git@github.com:"):
        candidate = candidate[len("git@github.com:") :]
    elif candidate.startswith("ssh://git@github.com/"):
        candidate = candidate[len("ssh://git@github.com/") :]
    elif candidate.startswith("https://github.com/"):
        candidate = candidate[len("https://github.com/") :]
    elif candidate.startswith("http://github.com/"):
        candidate = candidate[len("http://github.com/") :]
    return candidate.strip("/")


def status_command_expected_remote(config: dict[str, Any]) -> str:
    repositories = (config.get("coordination") or {}).get("repositories") or {}
    pantheon = repositories.get("pantheon") or {}
    configured = str(pantheon.get("repo") or "").strip()
    return configured or "ajoe734/pantheon"


def status_command_base_ref(config: dict[str, Any]) -> str:
    branch = str(((config.get("branch_workflow") or {}).get("dev_branch")) or "dev").strip() or "dev"
    return f"origin/{branch}"


def validate_status_command_runtime(
    root: Path,
    *,
    expected_sha: str | None = None,
    expected_remote: str | None = None,
    base_ref: str | None = None,
    require_merged: bool = True,
) -> dict[str, str]:
    """Validate the installed status command checkout without reading task state."""

    if not root.is_absolute():
        raise RuntimeError(f"{STATUS_COMMAND_ROOT_ENV} must be an absolute path")
    symlink_component = first_symlink_component(root)
    if symlink_component is not None:
        raise RuntimeError(
            f"{STATUS_COMMAND_ROOT_ENV} cannot include a symlink component: {symlink_component}"
        )
    resolved = root.resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise RuntimeError(f"{STATUS_COMMAND_ROOT_ENV} does not exist or is not a directory: {resolved}")
    if git_toplevel(resolved) != resolved:
        raise RuntimeError(f"{STATUS_COMMAND_ROOT_ENV} must be a git repository root: {resolved}")

    source_sha = git_stdout(resolved, ["rev-parse", "HEAD"])
    if expected_sha and source_sha != expected_sha:
        raise RuntimeError(
            f"{STATUS_COMMAND_SHA_ENV} mismatch: command root is {source_sha}, expected {expected_sha}"
        )

    expected_slug = normalize_github_repo_slug(expected_remote)
    remote_url = git_stdout(resolved, ["remote", "get-url", "origin"])
    actual_slug = normalize_github_repo_slug(remote_url)
    if expected_slug and actual_slug != expected_slug:
        raise RuntimeError(
            f"{STATUS_COMMAND_ROOT_ENV} remote mismatch: {actual_slug or remote_url} != {expected_slug}"
        )

    if require_merged:
        target_ref = str(base_ref or "origin/dev").strip() or "origin/dev"
        git_stdout(resolved, ["rev-parse", "--verify", target_ref])
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", source_sha, target_ref],
            cwd=resolved,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(
                f"{STATUS_COMMAND_ROOT_ENV} source SHA {source_sha} is not merged into {target_ref}{suffix}"
            )
    else:
        target_ref = str(base_ref or "").strip()

    status = subprocess.run(
        ["git", "status", "--porcelain", "-uall"],
        cwd=resolved,
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode != 0:
        detail = (status.stderr or status.stdout or "git status failed").strip()
        raise RuntimeError(
            f"Failed to check git status on {STATUS_COMMAND_ROOT_ENV}: {detail}"
        )
    for line in status.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) < 2:
            continue
        status_code, filepath = parts
        filepath = filepath.strip("\"'")
        if filepath.endswith((".py", ".sh", ".pyc", ".so", ".pl", ".rb")):
            raise RuntimeError(
                f"{STATUS_COMMAND_ROOT_ENV} contains dirty executable/import "
                f"file: {filepath} (status: {status_code})"
            )

    return {
        "root": str(resolved),
        "source_sha": source_sha,
        "remote": expected_slug or actual_slug,
        "base_ref": target_ref,
    }


def status_command_runtime_record_from_env(env: Mapping[str, str]) -> dict[str, str]:
    """Return the supervisor-issued command runtime fields safe for state files."""

    return {
        "command_root": str(env.get(STATUS_COMMAND_ROOT_ENV) or ""),
        "source_sha": str(env.get(STATUS_COMMAND_SHA_ENV) or ""),
        "remote": str(env.get(STATUS_COMMAND_REMOTE_ENV) or ""),
        "base_ref": str(env.get(STATUS_COMMAND_BASE_REF_ENV) or ""),
    }


def _status_command_runtime_env_from_record(record: Mapping[str, Any]) -> dict[str, str]:
    raw_root = str(record.get("command_root") or "").strip()
    raw_sha = str(record.get("source_sha") or "").strip()
    if not raw_root or not raw_sha:
        raise RuntimeError(
            "issued status_command_runtime requires command_root and source_sha"
        )
    remote = str(record.get("remote") or "").strip()
    base_ref = str(record.get("base_ref") or "").strip() or "origin/dev"
    metadata = validate_status_command_runtime(
        Path(raw_root).expanduser(),
        expected_sha=raw_sha,
        expected_remote=remote or None,
        base_ref=base_ref,
        require_merged=False,
    )
    return {
        STATUS_COMMAND_ROOT_ENV: metadata["root"],
        STATUS_COMMAND_SHA_ENV: metadata["source_sha"],
        STATUS_COMMAND_REMOTE_ENV: metadata["remote"],
        STATUS_COMMAND_BASE_REF_ENV: base_ref,
    }


def task_state_store_runtime_env(config: Mapping[str, Any]) -> dict[str, str]:
    """Return the one authoritative V2 task-state binding.

    A status command must never silently fall back to a repository projection
    (or an earlier shadow store) when its live journal binding is incomplete.
    The rendered live config is the only source of this binding.
    """

    identity = canonical_task_state_identity(config)
    return {
        TASK_STATE_STORE_MODE_ENV: "authoritative",
        TASK_STATE_EVENT_LOG_ENV: str(identity["event_log"]),
        CANONICAL_TASK_STATE_IDENTITY_ENV: json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
        ),
    }


def status_command_runtime_env(
    config: dict[str, Any],
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    env: dict[str, str] | None = None
    if isinstance(metadata, Mapping):
        issued = metadata.get("status_command_runtime")
        if isinstance(issued, Mapping):
            env = _status_command_runtime_env_from_record(issued)

    if env is None:
        expected_remote = status_command_expected_remote(config)
        base_ref = status_command_base_ref(config)
        runtime_metadata = validate_status_command_runtime(
            ROOT.resolve(),
            expected_remote=expected_remote,
            base_ref=base_ref,
            require_merged=False,
        )
        env = {
            STATUS_COMMAND_ROOT_ENV: runtime_metadata["root"],
            STATUS_COMMAND_SHA_ENV: runtime_metadata["source_sha"],
            STATUS_COMMAND_REMOTE_ENV: runtime_metadata["remote"],
            STATUS_COMMAND_BASE_REF_ENV: base_ref,
        }
    env.update(task_state_store_runtime_env(config))
    return env


def delivery_runtime_env(config: dict[str, Any], metadata: dict[str, Any] | None = None) -> dict[str, str]:
    workspace_root = delivery_workspace_root(config, metadata)
    status_root = delivery_status_root(config, metadata)
    env = {
        "PANTHEON_WORKTREE_ROOT": str(workspace_root),
        "PANTHEON_STATUS_ROOT": str(status_root),
        "ORCH_WORKSPACE_PATH": str(workspace_root),
    }
    env.update(status_command_runtime_env(config, metadata))
    task_generation = (metadata or {}).get("task_generation")
    try:
        normalized_generation = int(task_generation)
    except (TypeError, ValueError):
        normalized_generation = 0
    if normalized_generation > 0:
        env["ORCH_TASK_GENERATION"] = str(normalized_generation)
    return env


WORKER_AUTHORITY_SECRET_ENV_NAMES = frozenset(
    {
        "BRIDGE_SIGNING_KEY",
        "BRIDGE_SIGNING_PRIVATE_KEY",
        "BRIDGE_SIGNING_KEY_ID",
    }
)


def scrub_worker_authority_secrets(env: dict[str, str]) -> None:
    """Remove control-plane signing material at the final worker spawn boundary."""

    for name in WORKER_AUTHORITY_SECRET_ENV_NAMES:
        env.pop(name, None)


def github_cli_config_dir(env: Mapping[str, str] | None = None) -> Path:
    source = env or os.environ
    configured = str(source.get("GH_CONFIG_DIR") or "").strip()
    if configured:
        return Path(os.path.expanduser(configured))
    xdg_config_home = str(source.get("XDG_CONFIG_HOME") or "").strip()
    if xdg_config_home:
        return Path(os.path.expanduser(xdg_config_home)) / "gh"
    home = str(source.get("HOME") or str(Path.home())).strip() or str(Path.home())
    return Path(os.path.expanduser(home)) / ".config" / "gh"


def preserve_github_cli_auth_env(env: dict[str, str], source_env: Mapping[str, str] | None = None) -> None:
    if env.get("GH_CONFIG_DIR"):
        env["GH_CONFIG_DIR"] = os.path.expanduser(str(env["GH_CONFIG_DIR"]))
        return
    config_dir = github_cli_config_dir(source_env)
    if config_dir.exists():
        env["GH_CONFIG_DIR"] = str(config_dir)


def is_github_cli_auth_failure(reason: str | None) -> bool:
    normalized = compact_whitespace(reason).lower()
    if not normalized:
        return False
    markers = (
        "github cli is not authenticated",
        "gh cli is not authenticated",
        "gh is not authenticated",
        "require authenticated gh session",
        "require authenticated `gh` session",
        "requires authenticated gh session",
        "requires authenticated `gh` session",
        "you are not logged into any github hosts",
        "to log in, run: gh auth login",
        "run gh auth status",
        "run `gh auth status`",
    )
    return any(marker in normalized for marker in markers)


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: float | None = None,
    check: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd or ROOT),
        check=check,
        timeout=timeout,
        text=True,
        capture_output=True,
        env=env,
    )


def claude_credentials_path(env: dict[str, str] | None = None) -> Path:
    source = env or os.environ
    configured = str(source.get("CLAUDE_CONFIG_DIR") or "").strip()
    if configured:
        config_dir = Path(os.path.expanduser(configured))
    else:
        home = str(source.get("HOME") or str(Path.home())).strip() or str(Path.home())
        config_dir = Path(os.path.expanduser(home)) / ".claude"
    return config_dir / ".credentials.json"


def load_claude_oauth_tokens(env: dict[str, str] | None = None) -> tuple[dict[str, Any], dict[str, Any], Path] | None:
    credentials_path = claude_credentials_path(env)
    payload = load_json(credentials_path, default={}) or {}
    oauth = payload.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    return payload, oauth, credentials_path


def claude_oauth_token_expired(oauth: dict[str, Any], *, skew_seconds: int = 300) -> bool:
    if not oauth.get("accessToken"):
        return True
    expires_at = oauth.get("expiresAt")
    if expires_at in (None, ""):
        return False
    try:
        expires_at_ms = int(expires_at)
    except (TypeError, ValueError):
        return True
    return expires_at_ms <= int(time.time() * 1000) + (skew_seconds * 1000)


def claude_oauth_token_from_env(env: dict[str, str] | None = None) -> str | None:
    source = env or os.environ
    token = str(source.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
    return token if token.startswith("sk-ant-") else None


def apply_claude_oauth_token_file(env: dict[str, str], runtime: dict[str, Any]) -> dict[str, str]:
    if claude_oauth_token_from_env(env):
        return env
    token_file = str(runtime.get("oauth_token_file") or runtime.get("oauth_token_path") or "").strip()
    if not token_file:
        return env
    path = Path(os.path.expanduser(token_file))
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return env
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


@contextmanager
def _claude_oauth_refresh_serialization(account_lock_key: str | None) -> Generator[None, None, None]:
    """Serialize outbound OAuth refresh calls sharing one Claude account.

    Several CLI identities (claude, claude2, claude1-1..4, ...) can hold
    separate token pairs against the same underlying Anthropic account.
    Independent, concurrent refresh calls from those identities have been
    observed to intermittently fail (Cloudflare/API rate limiting reads as a
    plain network error here, surfacing as "OAuth refresh failed"). This
    does not change refresh semantics; it only prevents this process from
    overlapping its own account's refresh call with a sibling identity's.
    """

    if not account_lock_key:
        yield
        return
    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", account_lock_key)
    lock_path = Path(tempfile.gettempdir()) / f"pantheon-claude-oauth-refresh-{safe_key}.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        handle = os.fdopen(descriptor, "a+")
    except BaseException:
        os.close(descriptor)
        raise
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def refresh_claude_oauth_tokens(
    env: dict[str, str] | None = None,
    *,
    timeout: float = 15.0,
    account_lock_key: str | None = None,
) -> dict[str, Any] | None:
    loaded = load_claude_oauth_tokens(env)
    if not loaded:
        return None
    payload, oauth, credentials_path = loaded
    refresh_token = str(oauth.get("refreshToken") or "").strip()
    if not refresh_token:
        return None
    request_body = json.dumps(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLAUDE_OAUTH_CLIENT_ID,
            "scope": " ".join(CLAUDE_OAUTH_SCOPES),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        CLAUDE_OAUTH_TOKEN_URL,
        data=request_body,
        headers=CLAUDE_OAUTH_REFRESH_HEADERS,
        method="POST",
    )
    try:
        with _claude_oauth_refresh_serialization(account_lock_key):
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    updated = deepcopy(oauth)
    updated["accessToken"] = response_payload.get("access_token") or updated.get("accessToken") or ""
    updated["refreshToken"] = response_payload.get("refresh_token") or refresh_token
    expires_in = response_payload.get("expires_in")
    if expires_in is not None:
        try:
            updated["expiresAt"] = int(time.time() * 1000) + (int(expires_in) * 1000)
        except (TypeError, ValueError):
            pass
    scopes = response_payload.get("scope")
    if isinstance(scopes, str) and scopes.strip():
        updated["scopes"] = scopes.split()
    elif not updated.get("scopes"):
        updated["scopes"] = list(CLAUDE_OAUTH_SCOPES)
    payload["claudeAiOauth"] = updated
    write_json(credentials_path, payload)
    return updated


def claude_auth_ready(
    binary: str | None,
    *,
    env: dict[str, str] | None = None,
    refresh_if_needed: bool = True,
    account_lock_key: str | None = None,
) -> bool:
    if not binary:
        return False
    env_token = claude_oauth_token_from_env(env)
    if env_token:
        loaded = load_claude_oauth_tokens(env)
        if not loaded:
            return True
        _, oauth, _ = loaded
        stored_token = str(oauth.get("accessToken") or "").strip()
        if stored_token and stored_token != env_token:
            if not claude_oauth_token_expired(oauth):
                if env is not None:
                    env["CLAUDE_CODE_OAUTH_TOKEN"] = stored_token
            return True
        if stored_token and stored_token == env_token and not claude_oauth_token_expired(oauth):
            return True
        if not refresh_if_needed:
            return False
        refreshed = refresh_claude_oauth_tokens(env, account_lock_key=account_lock_key)
        if refreshed and not claude_oauth_token_expired(refreshed, skew_seconds=0):
            refreshed_token = str(refreshed.get("accessToken") or "").strip()
            if refreshed_token.startswith("sk-ant-") and env is not None:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = refreshed_token
            return True
        return False
    status = run_command([binary, "auth", "status"], env=env)
    if status.returncode != 0 or not status.stdout:
        return False
    try:
        payload = json.loads(status.stdout)
    except json.JSONDecodeError:
        return False
    if not payload.get("loggedIn"):
        return False
    loaded = load_claude_oauth_tokens(env)
    if not loaded:
        return True
    _, oauth, _ = loaded
    if not claude_oauth_token_expired(oauth):
        return True
    if not refresh_if_needed:
        return False
    refreshed = refresh_claude_oauth_tokens(env, account_lock_key=account_lock_key)
    return bool(refreshed and not claude_oauth_token_expired(refreshed, skew_seconds=0))


def command_exists(name: str) -> str | None:
    return shutil.which(name)


def shell_quote(parts: list[str]) -> str:
    return " ".join(subprocess.list2cmdline([part]) if os.name == "nt" else __import__("shlex").quote(part) for part in parts)


def normalize_agent_id(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def display_name_for(config: dict[str, Any], agent_id: str) -> str:
    agent = config.get("agents", {}).get(normalize_agent_id(agent_id), {})
    return agent.get("display_name") or agent.get("name") or agent_id


def agent_config_for(config: dict[str, Any], agent_id: str) -> dict[str, Any]:
    normalized = normalize_agent_id(agent_id)
    agent = config.get("agents", {}).get(normalized)
    if isinstance(agent, Mapping):
        merged = deepcopy(agent)
        merged.setdefault("id", normalized)
        merged.setdefault("display_name", agent_id)
        return merged
    raise ValueError(
        "agent configuration is required for delivery identity: "
        f"{normalized or str(agent_id or '').strip()!r}"
    )


def render_template(path: Path, variables: dict[str, Any]) -> str:
    text = path.read_text(encoding="utf-8")
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


def bound_commit_subject(task_id: str | None, description: str | None, max_len: int = 72) -> str:
    r"""Format a commit subject to guarantee max_len (default 72 chars) and match SUBJECT_PATTERN.

    Format: '<PREFIX>: <description>'
    Matches pattern: ^[A-Z][A-Z0-9-]*[A-Z0-9]:\s+\S
    Full Task-ID remains in required trailers.
    """
    raw_prefix = str(task_id or "").strip()
    clean_prefix = re.sub(r"[^A-Za-z0-9-]+", "-", raw_prefix).strip("-").upper()
    if not clean_prefix:
        clean_prefix = "TASK"

    raw_desc = str(description or "").strip()
    raw_desc = re.sub(r"\s+", " ", raw_desc)
    if not raw_desc:
        raw_desc = "commit"

    candidate = f"{clean_prefix}: {raw_desc}"
    if len(candidate) <= max_len:
        return candidate

    if "anchor recovered worktree WIP" in raw_desc:
        short_desc = raw_desc.replace("anchor recovered worktree WIP", "anchor WIP")
        candidate = f"{clean_prefix}: {short_desc}"
        if len(candidate) <= max_len:
            return candidate

    prefix_cost = len(clean_prefix) + 2
    avail_desc = max_len - prefix_cost
    if avail_desc >= 10:
        if avail_desc > 3 and len(raw_desc) > avail_desc:
            trunc_desc = raw_desc[: avail_desc - 3].rstrip() + "..."
        else:
            trunc_desc = raw_desc[:avail_desc].rstrip()
        candidate = f"{clean_prefix}: {trunc_desc}"
        if len(candidate) <= max_len:
            return candidate

    max_prefix_len = min(35, max(10, max_len - 15))
    compact_prefix = re.sub(r"-+$", "", clean_prefix[:max_prefix_len])
    if not compact_prefix:
        compact_prefix = "TASK"

    prefix_cost = len(compact_prefix) + 2
    avail_desc = max_len - prefix_cost
    if len(raw_desc) > avail_desc and avail_desc > 3:
        trunc_desc = raw_desc[: avail_desc - 3].rstrip() + "..."
    else:
        trunc_desc = raw_desc[:avail_desc].rstrip()

    candidate = f"{compact_prefix}: {trunc_desc}"
    if len(candidate) > max_len:
        candidate = candidate[:max_len].rstrip()
    return candidate



ACTIVITY_LOG_ROTATE_BYTES_DEFAULT = 50 * 1024 * 1024  # 50 MiB
ACTIVITY_LOG_ARCHIVE_SUBDIR = Path("archive") / "logs"
ACTIVITY_LOG_LEGACY_ARCHIVE_SUBDIR = (
    Path(".orchestrator") / "logs" / "activity-log-archive"
)
ACTIVITY_LOG_ROTATION_SUBDIR = Path(".orchestrator") / "logs" / "activity-rotation"
ACTIVITY_LOG_ROTATION_SCHEMA_VERSION = 2
ACTIVITY_ROTATION_LINEAGE_RECORD_TYPE = "pantheon.activity.rotation_lineage.v1"
ACTIVITY_ROTATION_HEAD_RECORD_TYPE = "pantheon.activity.lineage_head.v1"
ACTIVITY_ROTATION_RESOLUTION_RECORD_TYPE = "pantheon.activity.rotation_resolution.v1"
ACTIVITY_ROTATION_RESOLUTION_TYPE_SUPERSEDED = "superseded-by-legacy-rotation"
ACTIVITY_ROTATION_RESOLUTION_TYPE_LEGACY_SUPERSEDED = (
    "legacy-rotation-superseded-by-content-rotation"
)
ACTIVITY_ROTATION_WRITER_GUARD_ENV = "PANTHEON_ACTIVITY_ROTATION_PAUSE"
ACTIVITY_LOG_STRANDED_V1_INTENT_ERROR = (
    "stranded schema-v1 activity rotation intent requires the reviewed "
    "pending-intent recovery transaction "
    "(OPS-ACTIVITY-ROTATION-PENDING-INTENT-RECOVERY-001)"
)


def activity_rotation_writer_guard_active() -> bool:
    """All-writer rotation guard: pauses new rotations and intent recovery.

    Every current-code writer (scripts/ai_status.py append/rotate and the
    supervisor/watchdog/common writer path) funnels rotation through
    rotate_activity_log_unlocked/prepare_activity_audit_unlocked, so one
    environment switch pauses both mechanisms. Old-vintage checkouts do not
    read this switch; the transition runbook must also stop those processes
    and read back their absence before relying on the guard.
    """

    return str(os.environ.get(ACTIVITY_ROTATION_WRITER_GUARD_ENV) or "").strip() == "1"


def _activity_log_rotate_threshold(config: dict[str, Any]) -> int:
    raw = (config.get("paths") or {}).get("activity_log_rotate_bytes")
    try:
        threshold = int(raw)
    except (TypeError, ValueError):
        return ACTIVITY_LOG_ROTATE_BYTES_DEFAULT
    return threshold if threshold > 0 else ACTIVITY_LOG_ROTATE_BYTES_DEFAULT


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_write_bytes(
    path: Path,
    payload: bytes,
    *,
    mode: int | None = None,
) -> None:
    """Atomically replace one file and durably publish its directory entry."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            if mode is not None:
                os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    finally:
        temp_path.unlink(missing_ok=True)


def read_regular_file_snapshot(
    path: Path,
    *,
    source: str,
) -> tuple[bytes, os.stat_result]:
    """Read one stable regular-file leaf and return bytes plus its FD stat."""

    path = _assert_no_symlink_components(path, source=source)

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if path.is_symlink():
            raise RuntimeError(f"{source} must be a stable regular file: {path}") from exc
        raise
    try:
        descriptor_stat = os.fstat(descriptor)
        path_stat = path.lstat()
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or stat.S_ISLNK(path_stat.st_mode)
            or (path_stat.st_dev, path_stat.st_ino)
            != (descriptor_stat.st_dev, descriptor_stat.st_ino)
        ):
            raise RuntimeError(f"{source} must be a stable regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after_stat = path.lstat()
        if (
            stat.S_ISLNK(after_stat.st_mode)
            or (after_stat.st_dev, after_stat.st_ino)
            != (descriptor_stat.st_dev, descriptor_stat.st_ino)
        ):
            raise RuntimeError(f"{source} changed during read: {path}")
        return b"".join(chunks), descriptor_stat
    finally:
        os.close(descriptor)


def read_regular_file_bytes(path: Path, *, source: str) -> bytes:
    """Read one stable regular-file leaf without following a symlink."""

    payload, _ = read_regular_file_snapshot(path, source=source)
    return payload


def _durable_write_gzip(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as raw:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw,
                mtime=0,
            ) as compressed:
                compressed.write(payload)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    finally:
        temp_path.unlink(missing_ok=True)


def _activity_rotation_dir(log_path: Path) -> Path:
    return log_path.parent / ACTIVITY_LOG_ROTATION_SUBDIR


def activity_rotation_intent_path(log_path: Path) -> Path:
    return _activity_rotation_dir(log_path) / f"{log_path.name}.intent.json"


def activity_rotation_lineage_path(log_path: Path) -> Path:
    return _activity_rotation_dir(log_path) / f"{log_path.name}.lineage.jsonl"


def activity_rotation_resolutions_path(log_path: Path) -> Path:
    return _activity_rotation_dir(log_path) / f"{log_path.name}.resolutions.jsonl"


def _canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_line(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


class ActivityAuditInvariantError(RuntimeError):
    """Fail-closed activity reader error with machine-readable evidence."""

    def __init__(
        self,
        message: str,
        *,
        invariant: str,
        evidence: Mapping[str, Any],
    ) -> None:
        evidence_payload = {
            "invariant": invariant,
            "evidence": dict(evidence),
        }
        evidence_sha256 = _canonical_json_sha256(evidence_payload)
        self.invariant = invariant
        self.evidence_sha256 = evidence_sha256
        self.diagnostic = {
            "record_type": "pantheon.activity.fail_closed.v1",
            "schema_version": 1,
            "invariant": invariant,
            "message": message,
            "evidence_sha256": evidence_sha256,
            "evidence": dict(evidence),
        }
        encoded = json.dumps(
            self.diagnostic,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        super().__init__(f"{message}; diagnostic={encoded}")


def activity_audit_invariant_error(
    error: RuntimeError,
    *,
    log_path: Path,
    operation: str,
) -> ActivityAuditInvariantError:
    """Normalize activity integrity failures into one structured contract."""

    if isinstance(error, ActivityAuditInvariantError):
        return error
    detail = str(error)
    normalized = detail.lower()
    if "symlink" in normalized or "escapes status root" in normalized:
        invariant = "activity_source_path"
    elif "missing" in normalized:
        invariant = "activity_source_missing"
    elif "fork" in normalized or "sequence" in normalized:
        invariant = "activity_lineage_order"
    elif any(
        word in normalized
        for word in ("digest", "hash", "content", "identity", "mismatch")
    ):
        invariant = "activity_content_identity"
    elif any(word in normalized for word in ("changed", "replaced", "mutated", "truncated")):
        invariant = "activity_source_stability"
    elif "recovery" in normalized or "intent" in normalized:
        invariant = "activity_rotation_recovery"
    else:
        invariant = "activity_audit_integrity"
    return ActivityAuditInvariantError(
        detail,
        invariant=invariant,
        evidence={
            "log_path": str(log_path),
            "operation": operation,
            "error_type": type(error).__name__,
        },
    )


class DuplicateActivityJSONKeyError(ValueError):
    """Raised before an ambiguous activity JSON object can become a dict."""


def _reject_duplicate_activity_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateActivityJSONKeyError(
                f"duplicate JSON key is forbidden: {key}"
            )
        result[key] = value
    return result


def strict_activity_json_loads(payload: str | bytes | bytearray) -> Any:
    """Decode activity JSON while rejecting duplicate keys at every depth."""

    return json.loads(
        payload,
        object_pairs_hook=_reject_duplicate_activity_json_keys,
    )


def _jsonl_line_count(payload: bytes) -> int:
    return len(payload.splitlines()) if payload else 0


def _activity_lineage_head_bytes(record: dict[str, Any]) -> bytes:
    return _canonical_json_line(record)


def _split_activity_lineage_head(
    payload: bytes,
    *,
    source: Path | None = None,
) -> tuple[dict[str, Any] | None, bytes, bytes]:
    if not payload:
        return None, b"", payload
    newline_at = payload.find(b"\n")
    if newline_at < 0:
        first_line = payload
        rest = b""
    else:
        first_line = payload[: newline_at + 1]
        rest = payload[newline_at + 1 :]
    try:
        parsed = strict_activity_json_loads(first_line.decode("utf-8").strip())
    except DuplicateActivityJSONKeyError as exc:
        location = f" in {source}:1" if source is not None else ""
        raise RuntimeError(f"active lineage-head contains {exc}{location}") from exc
    except (UnicodeError, json.JSONDecodeError):
        return None, b"", payload
    if (
        isinstance(parsed, dict)
        and parsed.get("record_type") == ACTIVITY_ROTATION_HEAD_RECORD_TYPE
    ):
        return parsed, first_line, rest
    return None, b"", payload


def _is_activity_lineage_head(entry: Any) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("record_type") == ACTIVITY_ROTATION_HEAD_RECORD_TYPE
    )


def _activity_rotation_fault(point: str) -> None:
    """Process-test-only SIGKILL seam used to prove restart convergence."""

    requested = str(
        os.environ.get("LOOP_TEST_ACTIVITY_ROTATION_SIGKILL_AFTER") or ""
    ).strip()
    if requested == point:
        os.kill(os.getpid(), 9)


def _validated_activity_rotation_intent(
    log_path: Path,
    payload: Any,
) -> dict[str, Any]:
    required = {
        "schema_version",
        "transaction_id",
        "log_name",
        "source_sha256",
        "source_payload_sha256",
        "archive_payload_sha256",
        "archive_gzip_sha256",
        "tail_sha256",
        "tail_byte_count",
        "tail_line_count",
        "archive_relative_path",
        "lineage_relative_path",
        "lineage_previous_sha256",
        "lineage_sha256",
        "lineage_row_sha256",
        "lineage_row",
        "active_control",
        "active_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise RuntimeError("activity rotation intent schema is not exact")
    row = payload.get("lineage_row")
    control = payload.get("active_control")
    if not isinstance(row, dict) or not isinstance(control, dict):
        raise RuntimeError("activity rotation intent contract is invalid")
    seed_row = dict(row)
    seed_row["transaction_id"] = ""
    seed_row["archive_gzip_sha256"] = ""
    seed_keys = {
        "schema_version",
        "log_name",
        "source_sha256",
        "source_payload_sha256",
        "archive_payload_sha256",
        "tail_sha256",
        "tail_byte_count",
        "tail_line_count",
        "archive_relative_path",
        "lineage_relative_path",
        "lineage_previous_sha256",
    }
    seed = {key: payload[key] for key in sorted(seed_keys)}
    seed["lineage_row"] = seed_row
    expected_id = "activity-rotation-" + _canonical_json_sha256(seed)
    if (
        payload.get("schema_version") != ACTIVITY_LOG_ROTATION_SCHEMA_VERSION
        or payload.get("transaction_id") != expected_id
        or payload.get("log_name") != log_path.name
        or any(
            not isinstance(payload.get(key), str)
            or len(str(payload[key])) != 64
            for key in (
                "source_sha256",
                "source_payload_sha256",
                "archive_payload_sha256",
                "archive_gzip_sha256",
                "tail_sha256",
                "lineage_previous_sha256",
                "lineage_sha256",
                "lineage_row_sha256",
                "active_sha256",
            )
        )
        or payload.get("lineage_row_sha256") != _canonical_json_sha256(row)
    ):
        raise RuntimeError("activity rotation intent contract is invalid")
    for key in ("tail_byte_count", "tail_line_count"):
        if not isinstance(payload.get(key), int) or int(payload[key]) < 0:
            raise RuntimeError("activity rotation intent contract is invalid")
    relative = Path(str(payload.get("archive_relative_path") or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("activity rotation archive path is invalid")
    archive_path = (log_path.parent / relative).resolve()
    try:
        archive_path.relative_to(log_path.parent.resolve())
    except ValueError as exc:
        raise RuntimeError("activity rotation archive escapes status root") from exc
    lineage_relative = Path(str(payload.get("lineage_relative_path") or ""))
    if (
        not lineage_relative.parts
        or lineage_relative.is_absolute()
        or ".." in lineage_relative.parts
    ):
        raise RuntimeError("activity rotation lineage path is invalid")
    lineage_path = (log_path.parent / lineage_relative).resolve()
    try:
        lineage_path.relative_to(log_path.parent.resolve())
    except ValueError as exc:
        raise RuntimeError("activity rotation lineage escapes status root") from exc
    if lineage_path != activity_rotation_lineage_path(log_path).resolve():
        raise RuntimeError("activity rotation lineage path is invalid")
    if row.get("transaction_id") != payload.get("transaction_id"):
        raise RuntimeError("activity rotation intent row transaction mismatch")
    if control.get("transaction_id") != payload.get("transaction_id"):
        raise RuntimeError("activity rotation intent control transaction mismatch")
    return payload


SCHEMA_V1_ROTATION_INTENT_KEYS = frozenset(
    {
        "schema_version",
        "transaction_id",
        "log_name",
        "source_sha256",
        "archive_sha256",
        "tail_sha256",
        "archive_relative_path",
    }
)


def validated_schema_v1_rotation_intent(
    log_path: Path,
    payload: Any,
) -> dict[str, Any]:
    """Validate the exact retired schema-v1 rotation intent contract.

    This is not a compatibility acceptance path for normal readers/writers:
    only the reviewed pending-intent recovery transaction may consume a
    schema-v1 intent, and only after proving every byte relationship
    required by the 2026-07-16 incident plan.
    """

    if not isinstance(payload, dict) or set(payload) != SCHEMA_V1_ROTATION_INTENT_KEYS:
        raise RuntimeError("schema-v1 activity rotation intent schema is not exact")
    seed = {
        key: payload[key]
        for key in sorted(SCHEMA_V1_ROTATION_INTENT_KEYS - {"transaction_id"})
    }
    expected_id = "activity-rotation-" + _canonical_json_sha256(seed)
    if (
        payload.get("schema_version") != 1
        or payload.get("transaction_id") != expected_id
        or payload.get("log_name") != log_path.name
        or any(
            not isinstance(payload.get(key), str)
            or len(str(payload[key])) != 64
            for key in ("source_sha256", "archive_sha256", "tail_sha256")
        )
    ):
        raise RuntimeError("schema-v1 activity rotation intent contract is invalid")
    relative = Path(str(payload.get("archive_relative_path") or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("schema-v1 activity rotation archive path is invalid")
    archive_path = (log_path.parent / relative).resolve()
    try:
        archive_path.relative_to(log_path.parent.resolve())
    except ValueError as exc:
        raise RuntimeError(
            "schema-v1 activity rotation archive escapes status root"
        ) from exc
    return payload


def _is_schema_v1_rotation_intent_shape(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and set(payload) == SCHEMA_V1_ROTATION_INTENT_KEYS
        and payload.get("schema_version") == 1
    )


def _load_activity_rotation_intent(log_path: Path) -> dict[str, Any] | None:
    path = activity_rotation_intent_path(log_path)
    if not path.exists() and not path.is_symlink():
        return None
    try:
        intent_bytes = read_regular_file_bytes(
            path,
            source="activity rotation intent",
        )
        payload = strict_activity_json_loads(intent_bytes.decode("utf-8"))
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        DuplicateActivityJSONKeyError,
    ) as exc:
        raise RuntimeError("activity rotation intent is unreadable") from exc
    if _is_schema_v1_rotation_intent_shape(payload):
        # Never silently accept or auto-recover a schema-v1 intent in
        # schema-v2 code: fail closed with an explicit incident-class error.
        raise RuntimeError(ACTIVITY_LOG_STRANDED_V1_INTENT_ERROR)
    return _validated_activity_rotation_intent(log_path, payload)


def _activity_rotation_relative_path(log_path: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(log_path.parent.resolve()))
    except ValueError as exc:
        raise RuntimeError("activity rotation path escapes status root") from exc


def _activity_archive_payload(path: Path) -> tuple[bytes, bytes]:
    compressed = read_regular_file_bytes(path, source="activity rotation archive")
    try:
        payload = gzip.decompress(compressed)
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise RuntimeError(f"activity rotation archive is unreadable: {path}") from exc
    return compressed, payload


def _activity_archive_backup_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.bak")


@dataclass(frozen=True, slots=True)
class _ActivityArchiveMetrics:
    gzip_sha256: str
    gzip_byte_count: int
    payload_sha256: str
    payload_byte_count: int
    payload_line_count: int


class _HashingActivityArchiveReader:
    """Record the exact compressed bytes consumed by ``gzip.GzipFile``."""

    def __init__(self, file_obj: Any) -> None:
        self._file_obj = file_obj
        self._hasher = hashlib.sha256()
        self.byte_count = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._file_obj.read(size)
        self._hasher.update(chunk)
        self.byte_count += len(chunk)
        return chunk

    def hexdigest(self) -> str:
        return self._hasher.hexdigest()


def _stream_activity_archive_metrics(path: Path) -> _ActivityArchiveMetrics:
    """Hash and count a gzip archive without retaining either full byte stream."""

    path = _assert_no_symlink_components(
        path,
        source="activity rotation archive",
    )
    try:
        path_stat_before = path.lstat()
    except OSError as exc:
        raise RuntimeError(
            f"activity rotation archive is unreadable: {path}"
        ) from exc
    if stat.S_ISLNK(path_stat_before.st_mode) or not stat.S_ISREG(
        path_stat_before.st_mode
    ):
        raise RuntimeError(
            f"activity rotation archive must be a regular file: {path}"
        )
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise RuntimeError(f"activity rotation archive is unreadable: {path}") from exc
    try:
        descriptor_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or stat.S_ISLNK(path_stat_before.st_mode)
            or path_stat_before.st_dev != descriptor_stat.st_dev
            or path_stat_before.st_ino != descriptor_stat.st_ino
        ):
            raise RuntimeError(f"activity rotation archive changed: {path}")

        payload_hasher = hashlib.sha256()
        payload_byte_count = 0
        payload_newline_count = 0
        payload_last_byte = b""
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as file_obj:
                compressed_reader = _HashingActivityArchiveReader(file_obj)
                with gzip.GzipFile(fileobj=compressed_reader, mode="rb") as handle:
                    while True:
                        chunk = handle.read(1024 * 1024)
                        if not chunk:
                            break
                        payload_hasher.update(chunk)
                        payload_byte_count += len(chunk)
                        payload_newline_count += chunk.count(b"\n")
                        payload_last_byte = chunk[-1:]
        except (EOFError, gzip.BadGzipFile, OSError) as exc:
            raise RuntimeError(
                f"activity rotation archive is unreadable: {path}"
            ) from exc

        if compressed_reader.byte_count != descriptor_stat.st_size:
            raise RuntimeError(
                f"activity rotation archive changed during validation: {path}"
            )

        path_stat_after = path.lstat()
        if (
            stat.S_ISLNK(path_stat_after.st_mode)
            or path_stat_after.st_dev != descriptor_stat.st_dev
            or path_stat_after.st_ino != descriptor_stat.st_ino
            or path_stat_after.st_size != descriptor_stat.st_size
            or path_stat_after.st_mtime_ns != descriptor_stat.st_mtime_ns
        ):
            raise RuntimeError(
                f"activity rotation archive changed during validation: {path}"
            )
        payload_line_count = payload_newline_count + int(
            payload_byte_count > 0 and payload_last_byte != b"\n"
        )
        return _ActivityArchiveMetrics(
            gzip_sha256=compressed_reader.hexdigest(),
            gzip_byte_count=compressed_reader.byte_count,
            payload_sha256=payload_hasher.hexdigest(),
            payload_byte_count=payload_byte_count,
            payload_line_count=payload_line_count,
        )
    finally:
        os.close(descriptor)


def _stream_activity_archive_tail(path: Path, line_count: int) -> bytes:
    """Read a bounded decompressed tail while pinning one stable archive FD."""

    path = _assert_no_symlink_components(
        path,
        source="activity boundary predecessor",
    )
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        descriptor_stat = os.fstat(descriptor)
        path_stat_before = path.lstat()
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or stat.S_ISLNK(path_stat_before.st_mode)
            or path_stat_before.st_dev != descriptor_stat.st_dev
            or path_stat_before.st_ino != descriptor_stat.st_ino
        ):
            raise RuntimeError(
                f"activity boundary predecessor changed: {path}"
            )
        tail: deque[bytes] = deque(maxlen=line_count)
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as file_obj:
                with gzip.GzipFile(fileobj=file_obj, mode="rb") as handle:
                    while True:
                        line = handle.readline()
                        if not line:
                            break
                        tail.append(line)
        except (EOFError, gzip.BadGzipFile, OSError) as exc:
            raise RuntimeError(
                f"activity boundary predecessor is unreadable: {path}"
            ) from exc
        path_stat_after = path.lstat()
        if (
            stat.S_ISLNK(path_stat_after.st_mode)
            or path_stat_after.st_dev != descriptor_stat.st_dev
            or path_stat_after.st_ino != descriptor_stat.st_ino
            or path_stat_after.st_size != descriptor_stat.st_size
            or path_stat_after.st_mtime_ns != descriptor_stat.st_mtime_ns
        ):
            raise RuntimeError(
                f"activity boundary predecessor changed during validation: {path}"
            )
        return b"".join(tail)
    finally:
        os.close(descriptor)


def _normalize_activity_boundary_predecessor_path(
    log_path: Path,
    value: Any,
) -> Path:
    relative = Path(str(value or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("activity boundary predecessor path is invalid")
    predecessor = _assert_no_symlink_components(
        log_path.parent / relative,
        source="activity boundary predecessor",
    )
    try:
        predecessor.relative_to(log_path.parent.absolute())
    except ValueError as exc:
        raise RuntimeError(
            "activity boundary predecessor escapes status root"
        ) from exc
    if classify_source(predecessor) not in ("legacy_ts_std", "legacy_ts_old"):
        raise RuntimeError("activity boundary predecessor is not a legacy archive")
    return predecessor


def _normalize_activity_lineage_archive_path(log_path: Path, value: Any) -> Path:
    relative = Path(str(value or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("activity lineage archive path is invalid")
    archive_path = _assert_no_symlink_components(
        log_path.parent / relative,
        source="activity lineage archive",
    )
    try:
        archive_path.relative_to(log_path.parent.absolute())
    except ValueError as exc:
        raise RuntimeError("activity lineage archive escapes status root") from exc
    if classify_source(archive_path) != "content_addressed":
        raise RuntimeError("activity lineage archive path is not content-addressed")
    return archive_path


def _assert_content_addressed_archive_identity(
    archive_path: Path,
    payload_sha256: Any,
) -> None:
    match = re.fullmatch(r".+\.jsonl-([a-f0-9]{64})\.gz", archive_path.name)
    if match is None or match.group(1) != payload_sha256:
        raise RuntimeError(
            "activity content-addressed archive basename digest mismatch"
        )


def _validate_activity_rotation_lineage_row(
    log_path: Path,
    row: Any,
    *,
    expected_sequence: int,
    previous_row: dict[str, Any] | None,
    previous_lineage_bytes: bytes,
    validate_archive: bool,
) -> Path:
    required = {
        "record_type",
        "schema_version",
        "log_name",
        "sequence",
        "transaction_id",
        "archive_relative_path",
        "archive_payload_sha256",
        "archive_gzip_sha256",
        "archive_byte_count",
        "archive_line_count",
        "source_sha256",
        "source_payload_sha256",
        "source_byte_count",
        "source_line_count",
        "tail_sha256",
        "tail_byte_count",
        "tail_line_count",
        "previous_sequence",
        "previous_transaction_id",
        "previous_lineage_sha256",
        "boundary_normalization",
    }
    if not isinstance(row, dict) or set(row) != required:
        raise RuntimeError("activity lineage row schema is not exact")
    if (
        row.get("record_type") != ACTIVITY_ROTATION_LINEAGE_RECORD_TYPE
        or row.get("schema_version") != ACTIVITY_LOG_ROTATION_SCHEMA_VERSION
        or row.get("log_name") != log_path.name
        or row.get("sequence") != expected_sequence
    ):
        raise RuntimeError("activity lineage row identity is invalid")
    for key in (
        "archive_payload_sha256",
        "archive_gzip_sha256",
        "source_sha256",
        "source_payload_sha256",
        "tail_sha256",
        "previous_lineage_sha256",
    ):
        if not isinstance(row.get(key), str) or len(str(row[key])) != 64:
            raise RuntimeError("activity lineage digest is invalid")
    for key in (
        "archive_byte_count",
        "archive_line_count",
        "source_byte_count",
        "source_line_count",
        "tail_byte_count",
        "tail_line_count",
        "previous_sequence",
    ):
        if not isinstance(row.get(key), int) or int(row[key]) < 0:
            raise RuntimeError("activity lineage count is invalid")
    expected_previous_sha = _sha256_bytes(previous_lineage_bytes)
    if row.get("previous_lineage_sha256") != expected_previous_sha:
        raise RuntimeError("activity lineage previous digest mismatch")
    if previous_row is None:
        if row.get("previous_sequence") != 0 or row.get("previous_transaction_id") is not None:
            raise RuntimeError("activity lineage first row has a predecessor")
    else:
        if (
            row.get("previous_sequence") != previous_row.get("sequence")
            or row.get("previous_transaction_id")
            != previous_row.get("transaction_id")
        ):
            raise RuntimeError("activity lineage predecessor fork")
    if row.get("boundary_normalization") is not None:
        boundary = row.get("boundary_normalization")
        if expected_sequence != 1 or not isinstance(boundary, dict):
            raise RuntimeError("activity lineage has invalid boundary normalization")
        boundary_required = {
            "type",
            "predecessor_relative_path",
            "predecessor_sha256",
            "predecessor_byte_count",
            "predecessor_line_count",
            "excluded_prefix_sha256",
            "excluded_prefix_byte_count",
            "excluded_prefix_line_count",
            "active_source_sha256",
        }
        if set(boundary) != boundary_required:
            raise RuntimeError("activity boundary normalization schema is invalid")
        if (
            boundary.get("type") != "legacy-active-prefix-1000"
            or boundary.get("excluded_prefix_line_count") != 1000
            or boundary.get("active_source_sha256") != row.get("source_sha256")
        ):
            raise RuntimeError("activity boundary normalization is invalid")
        for key in (
            "predecessor_sha256",
            "excluded_prefix_sha256",
            "active_source_sha256",
        ):
            if not isinstance(boundary.get(key), str) or len(boundary[key]) != 64:
                raise RuntimeError("activity boundary normalization digest is invalid")
        for key in (
            "predecessor_byte_count",
            "predecessor_line_count",
            "excluded_prefix_byte_count",
            "excluded_prefix_line_count",
        ):
            if not isinstance(boundary.get(key), int) or boundary[key] < 0:
                raise RuntimeError("activity boundary normalization count is invalid")
        predecessor_path = _normalize_activity_boundary_predecessor_path(
            log_path,
            boundary.get("predecessor_relative_path"),
        )
        if validate_archive:
            predecessor_metrics = _stream_activity_archive_metrics(predecessor_path)
            if (
                predecessor_metrics.payload_sha256
                != boundary.get("predecessor_sha256")
                or predecessor_metrics.payload_byte_count
                != boundary.get("predecessor_byte_count")
                or predecessor_metrics.payload_line_count
                != boundary.get("predecessor_line_count")
            ):
                raise RuntimeError(
                    "activity boundary predecessor identity mismatch"
                )
            excluded_prefix = _stream_activity_archive_tail(predecessor_path, 1000)
            if (
                _sha256_bytes(excluded_prefix)
                != boundary.get("excluded_prefix_sha256")
                or len(excluded_prefix)
                != boundary.get("excluded_prefix_byte_count")
                or _jsonl_line_count(excluded_prefix)
                != boundary.get("excluded_prefix_line_count")
            ):
                raise RuntimeError(
                    "activity boundary excluded prefix mismatch"
                )
        # A first-boundary source has no lineage-head control row yet, so its
        # raw source count must be conserved exactly across the archived
        # payload, retained tail, and byte-proven excluded legacy prefix.
        expected_source_bytes = (
            row["archive_byte_count"]
            + row["tail_byte_count"]
            + boundary["excluded_prefix_byte_count"]
        )
        expected_source_lines = (
            row["archive_line_count"]
            + row["tail_line_count"]
            + boundary["excluded_prefix_line_count"]
        )
        if (
            row["source_byte_count"] != expected_source_bytes
            or row["source_line_count"] != expected_source_lines
        ):
            raise RuntimeError("activity lineage source conservation mismatch")
    archive_path = _normalize_activity_lineage_archive_path(
        log_path,
        row.get("archive_relative_path"),
    )
    _assert_content_addressed_archive_identity(
        archive_path,
        row.get("archive_payload_sha256"),
    )
    if validate_archive:
        if not archive_path.exists():
            raise RuntimeError("activity lineage archive is missing")
        metrics = _stream_activity_archive_metrics(archive_path)
        if metrics.gzip_sha256 != row.get("archive_gzip_sha256"):
            raise RuntimeError("activity lineage archive gzip digest mismatch")
        if metrics.payload_sha256 != row.get("archive_payload_sha256"):
            raise RuntimeError("activity lineage archive payload digest mismatch")
        if metrics.payload_byte_count != row.get("archive_byte_count"):
            raise RuntimeError("activity lineage archive byte count mismatch")
        if metrics.payload_line_count != row.get("archive_line_count"):
            raise RuntimeError("activity lineage archive line count mismatch")
    return archive_path


def _load_activity_rotation_lineage_unlocked(
    log_path: Path,
    *,
    validate_archives: bool = True,
) -> tuple[bytes, list[dict[str, Any]], list[Path]]:
    lineage_path = activity_rotation_lineage_path(log_path)
    if not lineage_path.exists() and not lineage_path.is_symlink():
        return b"", [], []
    lineage_bytes = read_regular_file_bytes(
        lineage_path,
        source="activity rotation lineage",
    )
    if not lineage_bytes:
        raise RuntimeError("activity lineage file is empty")
    if not lineage_bytes.endswith(b"\n"):
        raise RuntimeError("activity lineage file is truncated")
    rows: list[dict[str, Any]] = []
    archive_paths: list[Path] = []
    previous_bytes = b""
    previous_row: dict[str, Any] | None = None
    offset = 0
    seen_sequences: set[int] = set()
    seen_transactions: set[str] = set()
    seen_archives: set[Path] = set()
    boundary_count = 0
    for expected_sequence, raw_line in enumerate(
        lineage_bytes.splitlines(keepends=True),
        start=1,
    ):
        if not raw_line.strip():
            raise RuntimeError("activity lineage contains a blank row")
        try:
            row = strict_activity_json_loads(raw_line.decode("utf-8"))
        except (
            UnicodeError,
            json.JSONDecodeError,
            DuplicateActivityJSONKeyError,
        ) as exc:
            raise RuntimeError("activity lineage row is unreadable") from exc
        archive_path = _validate_activity_rotation_lineage_row(
            log_path,
            row,
            expected_sequence=expected_sequence,
            previous_row=previous_row,
            previous_lineage_bytes=previous_bytes,
            validate_archive=validate_archives,
        )
        sequence = int(row["sequence"])
        transaction_id = str(row["transaction_id"])
        if sequence in seen_sequences:
            raise RuntimeError("activity lineage duplicate sequence")
        if transaction_id in seen_transactions:
            raise RuntimeError("activity lineage duplicate transaction")
        if archive_path in seen_archives:
            raise RuntimeError("activity lineage duplicate archive")
        if row.get("boundary_normalization") is not None:
            boundary_count += 1
            if boundary_count > 1:
                raise RuntimeError("activity lineage has a second boundary exception")
        seen_sequences.add(sequence)
        seen_transactions.add(transaction_id)
        seen_archives.add(archive_path)
        rows.append(row)
        archive_paths.append(archive_path)
        offset += len(raw_line)
        previous_bytes = lineage_bytes[:offset]
        previous_row = row
    return lineage_bytes, rows, archive_paths


ACTIVITY_ROTATION_RESOLUTION_ROW_KEYS = frozenset(
    {
        "record_type",
        "schema_version",
        "resolution_type",
        "log_name",
        "sequence",
        "resolution_id",
        "previous_resolutions_sha256",
        "resolved_transaction_id",
        "intent_schema_version",
        "intent_sha256",
        "intent_payload",
        "archive_relative_path",
        "archive_gzip_sha256",
        "archive_payload_sha256",
        "archive_byte_count",
        "archive_line_count",
        "stage_tail_sha256",
        "stage_tail_byte_count",
        "stage_tail_line_count",
        "source_sha256",
        "source_byte_count",
        "source_line_count",
        "superseding_relative_path",
        "superseding_gzip_sha256",
        "superseding_payload_sha256",
        "superseding_byte_count",
        "superseding_line_count",
        "post_intent_suffix_sha256",
        "post_intent_suffix_byte_count",
        "post_intent_suffix_line_count",
        "active_sha256",
        "active_byte_count",
        "active_line_count",
        "retained_overlap_sha256",
        "retained_overlap_byte_count",
        "retained_overlap_line_count",
        "post_rotation_suffix_sha256",
        "post_rotation_suffix_byte_count",
        "post_rotation_suffix_line_count",
        "inventory_sha256",
        "writer_guard_attestation",
        "preserved_relative_dir",
    }
)

_ACTIVITY_RESOLUTION_DIGEST_KEYS = (
    "previous_resolutions_sha256",
    "intent_sha256",
    "archive_gzip_sha256",
    "archive_payload_sha256",
    "stage_tail_sha256",
    "source_sha256",
    "superseding_gzip_sha256",
    "superseding_payload_sha256",
    "post_intent_suffix_sha256",
    "active_sha256",
    "retained_overlap_sha256",
    "post_rotation_suffix_sha256",
    "inventory_sha256",
)

_ACTIVITY_RESOLUTION_COUNT_KEYS = (
    "sequence",
    "archive_byte_count",
    "archive_line_count",
    "stage_tail_byte_count",
    "stage_tail_line_count",
    "source_byte_count",
    "source_line_count",
    "superseding_byte_count",
    "superseding_line_count",
    "post_intent_suffix_byte_count",
    "post_intent_suffix_line_count",
    "active_byte_count",
    "active_line_count",
    "retained_overlap_byte_count",
    "retained_overlap_line_count",
    "post_rotation_suffix_byte_count",
    "post_rotation_suffix_line_count",
)


def activity_rotation_resolution_id(row: dict[str, Any]) -> str:
    seed = dict(row)
    seed["resolution_id"] = ""
    return "activity-intent-resolution-" + _canonical_json_sha256(seed)


def _normalize_activity_resolution_superseding_path(
    log_path: Path,
    value: Any,
) -> Path:
    relative = Path(str(value or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("activity resolution superseding path is invalid")
    superseding_path = _assert_no_symlink_components(
        log_path.parent / relative,
        source="activity resolution superseding archive",
    )
    try:
        superseding_path.relative_to(log_path.parent.absolute())
    except ValueError as exc:
        raise RuntimeError(
            "activity resolution superseding path escapes status root"
        ) from exc
    if classify_source(superseding_path) not in ("legacy_ts_std", "legacy_ts_old"):
        raise RuntimeError(
            "activity resolution superseding path is not a legacy archive"
        )
    return superseding_path


def _validated_activity_rotation_resolution_row(
    log_path: Path,
    row: Any,
    *,
    expected_sequence: int,
    previous_resolutions_bytes: bytes,
    validate_archives: bool,
) -> Path:
    if not isinstance(row, dict) or set(row) != ACTIVITY_ROTATION_RESOLUTION_ROW_KEYS:
        raise RuntimeError("activity resolution row schema is not exact")
    resolution_type = row.get("resolution_type")
    intent_schema_version = row.get("intent_schema_version")
    if (
        row.get("record_type") != ACTIVITY_ROTATION_RESOLUTION_RECORD_TYPE
        or row.get("schema_version") != ACTIVITY_LOG_ROTATION_SCHEMA_VERSION
        or row.get("log_name") != log_path.name
        or row.get("sequence") != expected_sequence
        or (
            intent_schema_version == 1
            and resolution_type != ACTIVITY_ROTATION_RESOLUTION_TYPE_SUPERSEDED
        )
        or (
            intent_schema_version == ACTIVITY_LOG_ROTATION_SCHEMA_VERSION
            and resolution_type
            != ACTIVITY_ROTATION_RESOLUTION_TYPE_LEGACY_SUPERSEDED
        )
        or intent_schema_version not in (1, ACTIVITY_LOG_ROTATION_SCHEMA_VERSION)
    ):
        raise RuntimeError("activity resolution row identity is invalid")
    for key in _ACTIVITY_RESOLUTION_DIGEST_KEYS:
        if not isinstance(row.get(key), str) or len(str(row[key])) != 64:
            raise RuntimeError("activity resolution digest is invalid")
    for key in _ACTIVITY_RESOLUTION_COUNT_KEYS:
        if not isinstance(row.get(key), int) or int(row[key]) < 0:
            raise RuntimeError("activity resolution count is invalid")
    if (
        not isinstance(row.get("writer_guard_attestation"), str)
        or not str(row["writer_guard_attestation"]).strip()
    ):
        raise RuntimeError("activity resolution guard attestation is missing")
    if row.get("previous_resolutions_sha256") != _sha256_bytes(
        previous_resolutions_bytes
    ):
        raise RuntimeError("activity resolution previous digest mismatch")
    if row.get("resolution_id") != activity_rotation_resolution_id(row):
        raise RuntimeError("activity resolution id mismatch")
    if intent_schema_version == 1:
        intent_payload = validated_schema_v1_rotation_intent(
            log_path,
            row.get("intent_payload"),
        )
        intent_archive_sha256 = intent_payload["archive_sha256"]
    else:
        intent_payload = _validated_activity_rotation_intent(
            log_path,
            row.get("intent_payload"),
        )
        intent_archive_sha256 = intent_payload["archive_payload_sha256"]
    if (
        intent_payload["transaction_id"] != row.get("resolved_transaction_id")
        or intent_payload["archive_relative_path"]
        != row.get("archive_relative_path")
        or intent_archive_sha256 != row.get("archive_payload_sha256")
        or intent_payload["tail_sha256"] != row.get("stage_tail_sha256")
        or intent_payload["source_sha256"] != row.get("source_sha256")
    ):
        raise RuntimeError("activity resolution intent binding mismatch")
    preserved_relative = Path(str(row.get("preserved_relative_dir") or ""))
    if (
        not preserved_relative.parts
        or preserved_relative.is_absolute()
        or ".." in preserved_relative.parts
    ):
        raise RuntimeError("activity resolution preserved dir is invalid")
    payload_byte_count = row["archive_byte_count"] + row["stage_tail_byte_count"]
    payload_line_count = row["archive_line_count"] + row["stage_tail_line_count"]
    if intent_schema_version == 1:
        source_counts_valid = (
            row["source_byte_count"] == payload_byte_count
            and row["source_line_count"] == payload_line_count
        )
    else:
        intent_lineage_row = intent_payload["lineage_row"]
        source_counts_valid = (
            row["source_byte_count"] == intent_lineage_row["source_byte_count"]
            and row["source_line_count"] == intent_lineage_row["source_line_count"]
            and row["archive_byte_count"]
            == intent_lineage_row["archive_byte_count"]
            and row["archive_line_count"]
            == intent_lineage_row["archive_line_count"]
            and row["stage_tail_byte_count"] == intent_payload["tail_byte_count"]
            and row["stage_tail_line_count"] == intent_payload["tail_line_count"]
            and row["source_byte_count"] > payload_byte_count
            and row["source_line_count"] == payload_line_count + 1
        )
    if (
        not source_counts_valid
        or row["superseding_byte_count"]
        != row["source_byte_count"] + row["post_intent_suffix_byte_count"]
        or row["superseding_line_count"]
        != row["source_line_count"] + row["post_intent_suffix_line_count"]
        or row["active_byte_count"]
        != row["retained_overlap_byte_count"]
        + row["post_rotation_suffix_byte_count"]
        or row["active_line_count"]
        != row["retained_overlap_line_count"]
        + row["post_rotation_suffix_line_count"]
    ):
        raise RuntimeError("activity resolution conservation counts are inconsistent")
    archive_path = _normalize_activity_lineage_archive_path(
        log_path,
        row.get("archive_relative_path"),
    )
    _assert_content_addressed_archive_identity(
        archive_path,
        row.get("archive_payload_sha256"),
    )
    superseding_path = _normalize_activity_resolution_superseding_path(
        log_path,
        row.get("superseding_relative_path"),
    )
    if validate_archives:
        validation_path = archive_path
        if not archive_path.exists() and not archive_path.is_symlink():
            backup_path = _activity_archive_backup_path(archive_path)
            if not backup_path.exists() and not backup_path.is_symlink():
                raise RuntimeError(
                    "activity resolution superseded archive is missing"
                )
            # A preserved incident backup is only a validation source. The
            # resolution row's compressed/payload digests and counts below
            # still have to match, and the streaming reader rejects symlinks.
            validation_path = backup_path
        metrics = _stream_activity_archive_metrics(validation_path)
        if metrics.gzip_sha256 != row.get("archive_gzip_sha256"):
            raise RuntimeError(
                "activity resolution superseded archive gzip digest mismatch"
            )
        if metrics.payload_sha256 != row.get("archive_payload_sha256"):
            raise RuntimeError(
                "activity resolution superseded archive payload digest mismatch"
            )
        if metrics.payload_byte_count != row.get("archive_byte_count"):
            raise RuntimeError(
                "activity resolution superseded archive byte count mismatch"
            )
        if metrics.payload_line_count != row.get("archive_line_count"):
            raise RuntimeError(
                "activity resolution superseded archive line count mismatch"
            )
        if not superseding_path.exists() and not superseding_path.is_symlink():
            raise RuntimeError("activity resolution superseding archive is missing")
        superseding_metrics = _stream_activity_archive_metrics(superseding_path)
        if superseding_metrics.gzip_sha256 != row.get("superseding_gzip_sha256"):
            raise RuntimeError(
                "activity resolution superseding archive gzip digest mismatch"
            )
        if superseding_metrics.payload_sha256 != row.get(
            "superseding_payload_sha256"
        ):
            raise RuntimeError(
                "activity resolution superseding archive payload digest mismatch"
            )
        if superseding_metrics.payload_byte_count != row.get(
            "superseding_byte_count"
        ):
            raise RuntimeError(
                "activity resolution superseding archive byte count mismatch"
            )
        if superseding_metrics.payload_line_count != row.get(
            "superseding_line_count"
        ):
            raise RuntimeError(
                "activity resolution superseding archive line count mismatch"
            )
    return (
        archive_path
        if intent_schema_version == 1
        else superseding_path
    )


def _load_activity_rotation_resolutions_unlocked(
    log_path: Path,
    *,
    validate_archives: bool = True,
) -> tuple[bytes, list[dict[str, Any]], list[Path]]:
    resolutions_path = activity_rotation_resolutions_path(log_path)
    if not resolutions_path.exists() and not resolutions_path.is_symlink():
        return b"", [], []
    resolutions_bytes = read_regular_file_bytes(
        resolutions_path,
        source="activity rotation resolutions",
    )
    if not resolutions_bytes:
        raise RuntimeError("activity resolutions file is empty")
    if not resolutions_bytes.endswith(b"\n"):
        raise RuntimeError("activity resolutions file is truncated")
    rows: list[dict[str, Any]] = []
    archive_paths: list[Path] = []
    previous_bytes = b""
    offset = 0
    seen_ids: set[str] = set()
    seen_transactions: set[str] = set()
    seen_archives: set[Path] = set()
    for expected_sequence, raw_line in enumerate(
        resolutions_bytes.splitlines(keepends=True),
        start=1,
    ):
        if not raw_line.strip():
            raise RuntimeError("activity resolutions contains a blank row")
        try:
            row = strict_activity_json_loads(raw_line.decode("utf-8"))
        except (
            UnicodeError,
            json.JSONDecodeError,
            DuplicateActivityJSONKeyError,
        ) as exc:
            raise RuntimeError("activity resolution row is unreadable") from exc
        archive_path = _validated_activity_rotation_resolution_row(
            log_path,
            row,
            expected_sequence=expected_sequence,
            previous_resolutions_bytes=previous_bytes,
            validate_archives=validate_archives,
        )
        resolution_id = str(row["resolution_id"])
        transaction_id = str(row["resolved_transaction_id"])
        if resolution_id in seen_ids:
            raise RuntimeError("activity resolutions duplicate resolution id")
        if transaction_id in seen_transactions:
            raise RuntimeError("activity resolutions duplicate transaction")
        if archive_path in seen_archives:
            raise RuntimeError("activity resolutions duplicate archive")
        seen_ids.add(resolution_id)
        seen_transactions.add(transaction_id)
        seen_archives.add(archive_path)
        rows.append(row)
        archive_paths.append(archive_path)
        offset += len(raw_line)
        previous_bytes = resolutions_bytes[:offset]
    return resolutions_bytes, rows, archive_paths


def _validate_active_lineage_head_unlocked(
    log_path: Path,
    lineage_bytes: bytes,
    rows: list[dict[str, Any]],
) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            log_path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except FileNotFoundError:
        if rows:
            raise RuntimeError("missing active lineage-head control record")
        return
    try:
        descriptor_stat = os.fstat(descriptor)
        path_stat = log_path.lstat()
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or stat.S_ISLNK(path_stat.st_mode)
            or path_stat.st_dev != descriptor_stat.st_dev
            or path_stat.st_ino != descriptor_stat.st_ino
        ):
            raise RuntimeError("active activity source changed while opening")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            first_line = handle.readline()
            control, _control_line, _rest = _split_activity_lineage_head(
                first_line,
                source=log_path,
            )
            if not rows:
                if control is not None:
                    raise RuntimeError(
                        "unexpected active lineage-head control record without lineage"
                    )
                return
            if control is None:
                raise RuntimeError("missing active lineage-head control record")
            last = rows[-1]
            expected = {
                "record_type": ACTIVITY_ROTATION_HEAD_RECORD_TYPE,
                "schema_version": ACTIVITY_LOG_ROTATION_SCHEMA_VERSION,
                "log_name": log_path.name,
                "sequence": last["sequence"],
                "transaction_id": last["transaction_id"],
                "archive_payload_sha256": last["archive_payload_sha256"],
                "archive_gzip_sha256": last["archive_gzip_sha256"],
                "lineage_sha256": _sha256_bytes(lineage_bytes),
                "lineage_row_sha256": _canonical_json_sha256(last),
                "tail_sha256": last["tail_sha256"],
                "tail_byte_count": last["tail_byte_count"],
                "tail_line_count": last["tail_line_count"],
            }
            if control != expected:
                raise RuntimeError("active lineage-head control record mismatch")
            tail_byte_count = int(control["tail_byte_count"])
            retained_tail = handle.read(tail_byte_count)
            if len(retained_tail) < tail_byte_count:
                raise RuntimeError("active lineage-head retained tail is truncated")
            if _sha256_bytes(retained_tail) != control["tail_sha256"]:
                raise RuntimeError("active lineage-head retained tail digest mismatch")
            if _jsonl_line_count(retained_tail) != control["tail_line_count"]:
                raise RuntimeError(
                    "active lineage-head retained tail line count mismatch"
                )
        path_stat_after = log_path.lstat()
        if (
            stat.S_ISLNK(path_stat_after.st_mode)
            or path_stat_after.st_dev != descriptor_stat.st_dev
            or path_stat_after.st_ino != descriptor_stat.st_ino
            or path_stat_after.st_size != descriptor_stat.st_size
            or path_stat_after.st_mtime_ns != descriptor_stat.st_mtime_ns
        ):
            raise RuntimeError("active activity source changed during head validation")
    finally:
        os.close(descriptor)


def _read_active_payload_without_lineage_head(log_path: Path) -> tuple[bytes, bytes, dict[str, Any] | None]:
    data = log_path.read_bytes()
    control, control_line, payload = _split_activity_lineage_head(
        data,
        source=log_path,
    )
    return data, payload, control


def _activity_rotation_stage_paths(
    log_path: Path,
    transaction_id: str,
) -> tuple[Path, Path]:
    root = _activity_rotation_dir(log_path)
    return (
        root / f"{transaction_id}.archive.gz",
        root / f"{transaction_id}.tail",
    )


def _read_gzip_bytes(path: Path) -> bytes:
    with gzip.open(path, "rb") as handle:
        return handle.read()


def recover_activity_log_rotation_unlocked(log_path: Path) -> Path | None:
    """Finish a durable archive/tail/lineage transaction while audit EX is held."""

    log_path = log_path.expanduser().resolve()
    intent = _load_activity_rotation_intent(log_path)
    if intent is None:
        return None
    transaction_id = str(intent["transaction_id"])
    stage_archive, stage_tail = _activity_rotation_stage_paths(
        log_path,
        transaction_id,
    )
    archive_path = (
        log_path.parent / str(intent["archive_relative_path"])
    ).resolve()
    lineage_path = activity_rotation_lineage_path(log_path)

    try:
        tail = read_regular_file_bytes(
            stage_tail,
            source="activity rotation tail stage",
        )
    except OSError as exc:
        raise RuntimeError("activity rotation tail stage is missing") from exc
    if hashlib.sha256(tail).hexdigest() != intent["tail_sha256"]:
        raise RuntimeError("activity rotation tail stage digest mismatch")
    if len(tail) != intent["tail_byte_count"]:
        raise RuntimeError("activity rotation tail stage byte count mismatch")
    if _jsonl_line_count(tail) != intent["tail_line_count"]:
        raise RuntimeError("activity rotation tail stage line count mismatch")
    active_control = intent["active_control"]
    active_bytes = _activity_lineage_head_bytes(active_control) + tail
    if _sha256_bytes(active_bytes) != intent["active_sha256"]:
        raise RuntimeError("activity rotation active control digest mismatch")

    if stage_archive.exists() or stage_archive.is_symlink():
        try:
            _compressed, archive_payload = _activity_archive_payload(stage_archive)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise RuntimeError("activity rotation archive stage is unreadable") from exc
    elif archive_path.exists() or archive_path.is_symlink():
        try:
            _compressed, archive_payload = _activity_archive_payload(archive_path)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise RuntimeError("activity rotation archive is unreadable") from exc
    else:
        raise RuntimeError("activity rotation archive stage is missing")
    if hashlib.sha256(archive_payload).hexdigest() != intent["archive_payload_sha256"]:
        raise RuntimeError("activity rotation archive digest mismatch")

    if archive_path.exists():
        try:
            installed_archive = _read_gzip_bytes(archive_path)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise RuntimeError("activity rotation installed archive is unreadable") from exc
        if installed_archive != archive_payload:
            raise RuntimeError("activity rotation installed archive conflicts")
    else:
        _durable_write_gzip(archive_path, archive_payload)
    _activity_rotation_fault("archive")

    try:
        active = log_path.read_bytes()
    except FileNotFoundError:
        active = None
    except OSError as exc:
        raise RuntimeError("activity log is unreadable during recovery") from exc
    active_digest = hashlib.sha256(active).hexdigest() if active is not None else None
    if active_digest == intent["source_sha256"] or active is None:
        durable_write_bytes(log_path, active_bytes)
    elif active_digest != intent["active_sha256"]:
        raise RuntimeError("activity log changed during rotation recovery")
    _activity_rotation_fault("tail")

    row_bytes = _canonical_json_line(intent["lineage_row"])
    previous_lineage_sha = intent["lineage_previous_sha256"]
    current_lineage = (
        read_regular_file_bytes(lineage_path, source="activity rotation lineage")
        if lineage_path.exists() or lineage_path.is_symlink()
        else b""
    )
    current_lineage_sha = _sha256_bytes(current_lineage)
    if current_lineage_sha == intent["lineage_sha256"]:
        pass
    elif current_lineage_sha == previous_lineage_sha:
        lineage_bytes = current_lineage + row_bytes
        if _sha256_bytes(lineage_bytes) != intent["lineage_sha256"]:
            raise RuntimeError("activity rotation lineage digest mismatch")
        durable_write_bytes(lineage_path, lineage_bytes)
    else:
        raise RuntimeError("activity rotation lineage changed during recovery")
    _activity_rotation_fault("lineage")

    if log_path.read_bytes() != active_bytes:
        raise RuntimeError("activity rotation active readback mismatch")
    if _read_gzip_bytes(archive_path) != archive_payload:
        raise RuntimeError("activity rotation archive readback mismatch")
    lineage_readback = read_regular_file_bytes(
        lineage_path,
        source="activity rotation lineage",
    )
    if _sha256_bytes(lineage_readback) != intent["lineage_sha256"]:
        raise RuntimeError("activity rotation lineage readback mismatch")
    lineage_bytes, rows, _archives = _load_activity_rotation_lineage_unlocked(
        log_path,
        validate_archives=True,
    )
    _validate_active_lineage_head_unlocked(log_path, lineage_bytes, rows)

    intent_path = activity_rotation_intent_path(log_path)
    intent_path.unlink(missing_ok=True)
    stage_archive.unlink(missing_ok=True)
    stage_tail.unlink(missing_ok=True)
    _fsync_directory(intent_path.parent)
    return archive_path


def prepare_activity_audit_unlocked(log_path: Path) -> None:
    """Recover rotation and one interrupted, non-newline append under audit EX."""

    if activity_rotation_writer_guard_active():
        return
    recover_activity_log_rotation_unlocked(log_path)
    repair_activity_log_tail_unlocked(log_path)


def assert_activity_audit_stable_unlocked(log_path: Path) -> None:
    """Fail closed when a shared reader observes unfinished writer recovery."""

    intent_path = activity_rotation_intent_path(log_path)
    if intent_path.exists() or intent_path.is_symlink():
        raise RuntimeError("activity rotation recovery is pending")
    if log_path.is_symlink():
        raise RuntimeError(
            f"activity audit source leaf cannot be a symlink: {log_path}"
        )
    if not log_path.is_file():
        return
    descriptor = os.open(
        log_path,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        descriptor_stat = os.fstat(descriptor)
        path_stat = log_path.lstat()
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or stat.S_ISLNK(path_stat.st_mode)
            or path_stat.st_dev != descriptor_stat.st_dev
            or path_stat.st_ino != descriptor_stat.st_ino
        ):
            raise RuntimeError("activity audit source changed while opening")
        if descriptor_stat.st_size:
            os.lseek(descriptor, -1, os.SEEK_END)
            if os.read(descriptor, 1) != b"\n":
                raise RuntimeError("activity audit has an interrupted trailing row")
        path_stat_after = log_path.lstat()
        if (
            stat.S_ISLNK(path_stat_after.st_mode)
            or path_stat_after.st_dev != descriptor_stat.st_dev
            or path_stat_after.st_ino != descriptor_stat.st_ino
            or path_stat_after.st_size != descriptor_stat.st_size
            or path_stat_after.st_mtime_ns != descriptor_stat.st_mtime_ns
        ):
            raise RuntimeError("activity audit source changed during tail validation")
    finally:
        os.close(descriptor)


def repair_activity_log_tail_unlocked(log_path: Path) -> bool:
    """Repair only a non-newline tail; complete malformed rows remain fatal."""

    try:
        data = log_path.read_bytes()
    except FileNotFoundError:
        return False
    if not data or data.endswith(b"\n"):
        return False
    split_at = data.rfind(b"\n")
    prefix = data[: split_at + 1] if split_at >= 0 else b""
    fragment = data[split_at + 1 :]
    try:
        decoded = fragment.decode("utf-8")
        parsed = strict_activity_json_loads(decoded)
        complete = isinstance(parsed, dict)
    except DuplicateActivityJSONKeyError as exc:
        raise RuntimeError(f"activity audit trailing row contains {exc}") from exc
    except (UnicodeError, json.JSONDecodeError):
        complete = False
    repaired = data + b"\n" if complete else prefix
    durable_write_bytes(log_path, repaired)
    return True


def _partition_activity_rotation_payload(
    payload: bytes,
    *,
    keep_lines: int,
) -> tuple[bytes, bytes]:
    if keep_lines > 0:
        lines = payload.splitlines(keepends=True)
        if len(lines) > keep_lines:
            return b"".join(lines[:-keep_lines]), b"".join(lines[-keep_lines:])
        return payload, b""
    return payload, b""


def _legacy_activity_source_paths_unlocked(log_path: Path) -> list[Path]:
    archive_dir = _assert_no_symlink_components(
        log_path.parent / ACTIVITY_LOG_ARCHIVE_SUBDIR,
        source="activity archive directory",
    )
    legacy_dir = _assert_no_symlink_components(
        log_path.parent / ACTIVITY_LOG_LEGACY_ARCHIVE_SUBDIR,
        source="legacy activity archive directory",
    )
    sources: list[Path] = []
    sources.extend(sorted(archive_dir.glob(f"{log_path.name}-*.gz")))
    sources.extend(sorted(legacy_dir.glob(f"{log_path.stem}-*.jsonl.gz")))
    legacy_old: list[Path] = []
    legacy_std: list[Path] = []
    for source in sources:
        if source.is_symlink():
            legacy_std.append(source)
            continue
        source_class = classify_source(source)
        if source_class == "content_addressed":
            continue
        if source_class == "legacy_ts_old":
            legacy_old.append(source)
        elif source_class == "legacy_ts_std":
            legacy_std.append(source)
        elif source_class == "unknown":
            raise RuntimeError(f"Unknown source format: {source.name}")
    return legacy_old + legacy_std


def _activity_source_payload(path: Path) -> bytes:
    if path.suffix == ".gz":
        compressed, payload = _activity_archive_payload(path)
        if not compressed:
            raise RuntimeError(f"activity source is empty: {path}")
        return payload
    return read_regular_file_bytes(path, source="activity audit source")


def _suffix_lines(payload: bytes, count: int) -> bytes | None:
    lines = payload.splitlines(keepends=True)
    if len(lines) < count:
        return None
    return b"".join(lines[-count:])


def _prefix_lines(payload: bytes, count: int) -> bytes | None:
    lines = payload.splitlines(keepends=True)
    if len(lines) < count:
        return None
    return b"".join(lines[:count])


def _activity_boundary_normalization_unlocked(
    log_path: Path,
    active_payload: bytes,
    *,
    source_sha256: str,
    existing_lineage_rows: list[dict[str, Any]],
    existing_content_archives: list[Path],
) -> tuple[bytes, dict[str, Any] | None]:
    if existing_lineage_rows or existing_content_archives:
        return active_payload, None
    legacy_sources = _legacy_activity_source_paths_unlocked(log_path)
    if not legacy_sources:
        return active_payload, None
    predecessor = legacy_sources[-1]
    predecessor_payload = _activity_source_payload(predecessor)
    predecessor_lines = predecessor_payload.splitlines(keepends=True)
    active_lines = active_payload.splitlines(keepends=True)
    if len(predecessor_lines) < 999 or len(active_lines) < 999:
        return active_payload, None
    overlap_len = 0
    for count in (999, 1000, 1001):
        if len(predecessor_lines) >= count and len(active_lines) >= count:
            if b"".join(predecessor_lines[-count:]) == b"".join(active_lines[:count]):
                overlap_len = count
    if overlap_len in (999, 1001):
        raise RuntimeError(
            f"invalid first content-addressed boundary overlap length {overlap_len}"
        )
    prefix_1000 = _prefix_lines(active_payload, 1000)
    predecessor_suffix_1000 = _suffix_lines(predecessor_payload, 1000)
    if prefix_1000 is not None and predecessor_suffix_1000 is not None:
        line_matches = sum(
            1
            for prev_line, active_line in zip(
                predecessor_lines[-1000:],
                active_lines[:1000],
            )
            if prev_line == active_line
        )
        if line_matches == 999 and prefix_1000 != predecessor_suffix_1000:
            raise RuntimeError("invalid first content-addressed boundary mismatch")
    for older in legacy_sources[:-1]:
        older_payload = _activity_source_payload(older)
        older_suffix = _suffix_lines(older_payload, 1000)
        if (
            older_suffix is not None
            and prefix_1000 is not None
            and older_suffix == prefix_1000
        ):
            raise RuntimeError("first content-addressed boundary matches non-adjacent legacy source")
    if overlap_len != 1000:
        return active_payload, None
    excluded_prefix = prefix_1000
    assert excluded_prefix is not None
    predecessor_relative = _activity_rotation_relative_path(log_path, predecessor)
    boundary = {
        "type": "legacy-active-prefix-1000",
        "predecessor_relative_path": predecessor_relative,
        "predecessor_sha256": _sha256_bytes(predecessor_payload),
        "predecessor_byte_count": len(predecessor_payload),
        "predecessor_line_count": _jsonl_line_count(predecessor_payload),
        "excluded_prefix_sha256": _sha256_bytes(excluded_prefix),
        "excluded_prefix_byte_count": len(excluded_prefix),
        "excluded_prefix_line_count": 1000,
        "active_source_sha256": source_sha256,
    }
    return active_payload[len(excluded_prefix):], boundary


def rotate_activity_log_unlocked(
    log_path: Path,
    *,
    max_bytes: int,
    keep_lines: int = 0,
    archive_dir: Path | None = None,
) -> Path | None:
    """Durably partition active bytes into one archive and one active tail."""

    log_path = _assert_no_symlink_components(
        log_path,
        source="activity log",
    )
    if activity_rotation_writer_guard_active():
        return None
    prepare_activity_audit_unlocked(log_path)
    if max_bytes <= 0:
        return None
    # Validate source order, registered content archives, and any active
    # lineage-head before deciding whether this writer may publish bytes.
    existing_sources = activity_audit_source_paths_unlocked(log_path)
    try:
        data, active_payload, _active_control = _read_active_payload_without_lineage_head(
            log_path
        )
    except FileNotFoundError:
        return None
    if len(data) <= max_bytes:
        return None
    lineage_bytes, lineage_rows, lineage_archives = (
        _load_activity_rotation_lineage_unlocked(
            log_path,
            validate_archives=True,
        )
    )
    existing_content_archives = [
        source
        for source in existing_sources
        if classify_source(source) == "content_addressed"
    ]
    source_sha256 = _sha256_bytes(data)
    source_payload, boundary_normalization = (
        _activity_boundary_normalization_unlocked(
            log_path,
            active_payload,
            source_sha256=source_sha256,
            existing_lineage_rows=lineage_rows,
            existing_content_archives=existing_content_archives,
        )
    )
    archive_payload, tail = _partition_activity_rotation_payload(
        source_payload,
        keep_lines=keep_lines,
    )
    if not archive_payload:
        return None

    archive_digest = hashlib.sha256(archive_payload).hexdigest()
    archive_dir = _assert_no_symlink_components(
        archive_dir
        if archive_dir is not None
        else log_path.parent / ACTIVITY_LOG_ARCHIVE_SUBDIR,
        source="activity archive directory",
    )
    try:
        archive_relative = archive_dir.relative_to(log_path.parent)
    except ValueError as exc:
        raise RuntimeError("activity archive directory escapes status root") from exc
    archive_path = archive_dir / f"{log_path.name}-{archive_digest}.gz"
    if archive_path in lineage_archives:
        raise RuntimeError("activity rotation archive path is already registered")
    _, _, superseded_for_rotate = _load_activity_rotation_resolutions_unlocked(
        log_path,
        validate_archives=False,
    )
    if archive_path.resolve() in {path.resolve() for path in superseded_for_rotate}:
        raise RuntimeError("activity rotation archive path is already superseded")
    lineage_relative = activity_rotation_lineage_path(log_path).relative_to(
        log_path.parent
    )
    previous_row = lineage_rows[-1] if lineage_rows else None
    next_sequence = int(previous_row["sequence"]) + 1 if previous_row else 1
    previous_transaction_id = (
        str(previous_row["transaction_id"]) if previous_row else None
    )
    previous_lineage_sha = _sha256_bytes(lineage_bytes)
    seed = {
        "schema_version": ACTIVITY_LOG_ROTATION_SCHEMA_VERSION,
        "log_name": log_path.name,
        "source_sha256": source_sha256,
        "source_payload_sha256": _sha256_bytes(source_payload),
        "archive_payload_sha256": archive_digest,
        "tail_sha256": hashlib.sha256(tail).hexdigest(),
        "tail_byte_count": len(tail),
        "tail_line_count": _jsonl_line_count(tail),
        "archive_relative_path": str(archive_relative / archive_path.name),
        "lineage_relative_path": str(lineage_relative),
        "lineage_previous_sha256": previous_lineage_sha,
        "lineage_row": {
            "record_type": ACTIVITY_ROTATION_LINEAGE_RECORD_TYPE,
            "schema_version": ACTIVITY_LOG_ROTATION_SCHEMA_VERSION,
            "log_name": log_path.name,
            "sequence": next_sequence,
            "transaction_id": "",
            "archive_relative_path": str(archive_relative / archive_path.name),
            "archive_payload_sha256": archive_digest,
            "archive_gzip_sha256": "",
            "archive_byte_count": len(archive_payload),
            "archive_line_count": _jsonl_line_count(archive_payload),
            "source_sha256": source_sha256,
            "source_payload_sha256": _sha256_bytes(source_payload),
            "source_byte_count": len(data),
            "source_line_count": _jsonl_line_count(data),
            "tail_sha256": hashlib.sha256(tail).hexdigest(),
            "tail_byte_count": len(tail),
            "tail_line_count": _jsonl_line_count(tail),
            "previous_sequence": int(previous_row["sequence"]) if previous_row else 0,
            "previous_transaction_id": previous_transaction_id,
            "previous_lineage_sha256": previous_lineage_sha,
            "boundary_normalization": boundary_normalization,
        },
    }
    transaction_id = "activity-rotation-" + _canonical_json_sha256(seed)
    rotation_dir = _activity_rotation_dir(log_path)
    rotation_dir.mkdir(parents=True, exist_ok=True)
    stage_archive, stage_tail = _activity_rotation_stage_paths(
        log_path,
        transaction_id,
    )
    _durable_write_gzip(stage_archive, archive_payload)
    _activity_rotation_fault("stage_archive")
    archive_gzip_sha = _sha256_bytes(
        read_regular_file_bytes(stage_archive, source="activity rotation archive")
    )
    lineage_row = dict(seed["lineage_row"])
    lineage_row["transaction_id"] = transaction_id
    lineage_row["archive_gzip_sha256"] = archive_gzip_sha
    lineage_row_sha = _canonical_json_sha256(lineage_row)
    lineage_row_bytes = _canonical_json_line(lineage_row)
    new_lineage_bytes = lineage_bytes + lineage_row_bytes
    lineage_sha = _sha256_bytes(new_lineage_bytes)
    active_control = {
        "record_type": ACTIVITY_ROTATION_HEAD_RECORD_TYPE,
        "schema_version": ACTIVITY_LOG_ROTATION_SCHEMA_VERSION,
        "log_name": log_path.name,
        "sequence": next_sequence,
        "transaction_id": transaction_id,
        "archive_payload_sha256": archive_digest,
        "archive_gzip_sha256": archive_gzip_sha,
        "lineage_sha256": lineage_sha,
        "lineage_row_sha256": lineage_row_sha,
        "tail_sha256": hashlib.sha256(tail).hexdigest(),
        "tail_byte_count": len(tail),
        "tail_line_count": _jsonl_line_count(tail),
    }
    active_bytes = _activity_lineage_head_bytes(active_control) + tail
    intent = {
        "schema_version": ACTIVITY_LOG_ROTATION_SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "log_name": log_path.name,
        "source_sha256": source_sha256,
        "source_payload_sha256": _sha256_bytes(source_payload),
        "archive_payload_sha256": archive_digest,
        "archive_gzip_sha256": archive_gzip_sha,
        "tail_sha256": hashlib.sha256(tail).hexdigest(),
        "tail_byte_count": len(tail),
        "tail_line_count": _jsonl_line_count(tail),
        "archive_relative_path": str(archive_relative / archive_path.name),
        "lineage_relative_path": str(lineage_relative),
        "lineage_previous_sha256": previous_lineage_sha,
        "lineage_sha256": lineage_sha,
        "lineage_row_sha256": lineage_row_sha,
        "lineage_row": lineage_row,
        "active_control": active_control,
        "active_sha256": _sha256_bytes(active_bytes),
    }
    durable_write_bytes(stage_tail, tail)
    _activity_rotation_fault("stage_tail")
    write_json(activity_rotation_intent_path(log_path), intent)
    _activity_rotation_fault("intent")
    return recover_activity_log_rotation_unlocked(log_path)


def activity_audit_source_paths_unlocked(log_path: Path) -> list[Path]:
    """Return disjoint rotated sources plus active while audit SH/EX is held."""

    log_path = _resolved_activity_log_path(log_path)
    assert_activity_audit_stable_unlocked(log_path)
    archive_dir = _assert_no_symlink_components(
        log_path.parent / ACTIVITY_LOG_ARCHIVE_SUBDIR,
        source="activity archive directory",
    )
    archive_sources = sorted(archive_dir.glob(f"{log_path.name}-*.gz"))
    legacy_sources = _legacy_activity_source_paths_unlocked(log_path)
    lineage_bytes, lineage_rows, lineage_archives = (
        _load_activity_rotation_lineage_unlocked(
            log_path,
            validate_archives=True,
        )
    )
    _validate_active_lineage_head_unlocked(log_path, lineage_bytes, lineage_rows)
    _resolution_bytes, _resolution_rows, superseded_archives = (
        _load_activity_rotation_resolutions_unlocked(
            log_path,
            validate_archives=True,
        )
    )
    registered_content = {path.resolve() for path in lineage_archives}
    superseded_content = {
        path.resolve()
        for path in superseded_archives
        if classify_source(path) == "content_addressed"
    }
    superseded_legacy = {
        path.resolve()
        for path in superseded_archives
        if classify_source(path) in ("legacy_ts_std", "legacy_ts_old")
    }
    if registered_content & superseded_content:
        raise RuntimeError(
            "activity archive is both lineage-registered and resolution-superseded"
        )
    discovered_content = [
        source.resolve()
        for source in archive_sources
        if classify_source(source) == "content_addressed"
    ]
    backed_up_superseded = {
        path.resolve()
        for path in superseded_archives
        if classify_source(path) == "content_addressed"
        if not path.exists()
        and not path.is_symlink()
        and (
            _activity_archive_backup_path(path).exists()
            or _activity_archive_backup_path(path).is_symlink()
        )
    }
    if (
        set(discovered_content) | backed_up_superseded
        != registered_content | superseded_content
    ):
        raise RuntimeError("activity content-addressed archives do not match lineage")
    sources = [
        source
        for source in legacy_sources
        if source.resolve() not in superseded_legacy
    ]
    sources.extend(lineage_archives)
    if log_path.is_file():
        sources.append(log_path)
    for source in sources:
        # A rotated or active audit source can never be a symlink: callers
        # read these paths with a plain `gzip.open()` / `read_text()`, which
        # follows symlinks, and a forged or conflicting external payload
        # injected through a symlinked archive leaf would otherwise be
        # accepted into activity_event_index()/program_activity_records().
        if source.is_symlink():
            raise RuntimeError(
                f"activity audit source leaf cannot be a symlink: {source}"
            )
        source_class = classify_source(source)
        if source_class == "unknown":
            raise RuntimeError(f"Unknown source format: {source.name}")
    return sources


def classify_source(path: Path) -> str:
    name = path.name
    if name.endswith(".jsonl"):
        return "active"
    if re.match(r"^.+\.jsonl-\d{4}-\d{2}-\d{2}T\d{4}Z\.gz$", name):
        return "legacy_ts_std"
    if re.match(r"^.+\.jsonl-\d{4}\.gz$", name):
        return "legacy_ts_std"
    if re.match(r"^.+-\d{8}T\d{6}Z\.jsonl\.gz$", name):
        return "legacy_ts_old"
    if re.match(r"^.+\.jsonl-[a-f0-9]{64}\.gz$", name):
        return "content_addressed"
    return "unknown"


def extract_timestamp_from_name(name: str) -> str:
    m = re.match(r"^.+\.jsonl-(\d{4}-\d{2}-\d{2}T\d{4})Z\.gz$", name)
    if m:
        return m.group(1)
    m = re.match(r"^.+-(\d{8}T\d{6})Z\.jsonl\.gz$", name)
    if m:
        return m.group(1)
    return ""


@dataclass(frozen=True, slots=True)
class _ActivitySourceSnapshot:
    path: Path
    st_dev: int
    st_ino: int
    st_size: int
    st_mtime_ns: int
    raw_sha256: str


def _ordered_activity_sources_unlocked(log_path: Path) -> list[Path]:
    sources = activity_audit_source_paths_unlocked(log_path)
    legacy_old = [
        source for source in sources if classify_source(source) == "legacy_ts_old"
    ]
    remaining = [
        source for source in sources if classify_source(source) != "legacy_ts_old"
    ]
    return legacy_old + remaining


def _sha256_file_descriptor(descriptor: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    hasher = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        hasher.update(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return hasher.hexdigest()


def _snapshot_activity_source_descriptor(
    descriptor: int,
    *,
    source: Path,
    expected_size: int,
) -> tuple[Any, str, int]:
    """Copy one live source into a private file and bind the copied raw bytes.

    Parsing the live descriptor after hashing leaves an ABA window: a writer
    can replace same-size bytes, let the reader consume them, then restore the
    original bytes before final validation.  The private temporary file makes
    the parsed bytes immutable to external writers; its digest must also match
    a fresh digest of the still-open live descriptor before it is returned.
    """

    snapshot_file = tempfile.TemporaryFile(
        mode="w+b",
        prefix="pantheon-activity-source-",
        suffix=".raw",
    )
    hasher = hashlib.sha256()
    byte_count = 0
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            snapshot_file.write(chunk)
            hasher.update(chunk)
            byte_count += len(chunk)
        snapshot_file.flush()
        raw_sha256 = hasher.hexdigest()
        if (
            byte_count != expected_size
            or _sha256_file_descriptor(descriptor) != raw_sha256
        ):
            raise RuntimeError(f"Source changed while snapshotting: {source}")
        snapshot_file.seek(0)
        return snapshot_file, raw_sha256, byte_count
    except BaseException:
        snapshot_file.close()
        raise


def _assert_activity_sources_stable_unlocked(
    log_path: Path,
    sources: list[Path],
    snapshots: list[_ActivitySourceSnapshot],
) -> None:
    final_sources = _ordered_activity_sources_unlocked(log_path)
    if [str(source) for source in final_sources] != [str(source) for source in sources]:
        raise RuntimeError("activity audit source set changed during validation")
    if len(sources) != len(snapshots):
        raise RuntimeError("activity source snapshot count mismatch")
    for source, snapshot in zip(sources, snapshots):
        if source != snapshot.path:
            raise RuntimeError("activity source snapshot identity mismatch")
        descriptor = os.open(
            source,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            descriptor_stat = os.fstat(descriptor)
            path_stat = source.lstat()
            if (
                not stat.S_ISREG(descriptor_stat.st_mode)
                or stat.S_ISLNK(path_stat.st_mode)
                or path_stat.st_dev != descriptor_stat.st_dev
                or path_stat.st_ino != descriptor_stat.st_ino
                or descriptor_stat.st_dev != snapshot.st_dev
                or descriptor_stat.st_ino != snapshot.st_ino
            ):
                raise RuntimeError(f"Source replaced during validation: {source}")
            if (
                descriptor_stat.st_size != snapshot.st_size
                or descriptor_stat.st_mtime_ns != snapshot.st_mtime_ns
            ):
                raise RuntimeError(
                    f"Source mutated or truncated during validation: {source}"
                )
            final_sha256 = _sha256_file_descriptor(descriptor)
            if final_sha256 != snapshot.raw_sha256:
                raise RuntimeError(
                    f"Source content changed during validation: {source}"
                )
            path_stat_after = source.lstat()
            if (
                stat.S_ISLNK(path_stat_after.st_mode)
                or path_stat_after.st_dev != snapshot.st_dev
                or path_stat_after.st_ino != snapshot.st_ino
                or path_stat_after.st_size != snapshot.st_size
                or path_stat_after.st_mtime_ns != snapshot.st_mtime_ns
            ):
                raise RuntimeError(f"Source changed during final validation: {source}")
        finally:
            os.close(descriptor)


def _open_ephemeral_activity_snapshot_database() -> sqlite3.Connection:
    """Open an unlink-on-create SQLite store that cannot survive process death."""

    db_file = tempfile.NamedTemporaryFile(
        prefix="pantheon-activity-snapshot-",
        suffix=".db",
        delete=False,
    )
    db_path = Path(db_file.name)
    db_file.close()
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path)
        # The open connection owns the only reference after unlink. A normal
        # close, SIGTERM, or SIGKILL therefore releases the disk allocation;
        # no multi-gigabyte snapshot can be orphaned in the system temp dir.
        db_path.unlink()
        # This is an ephemeral validation cache, never a durable authority.
        # Small in-memory rollback journals keep each bounded transaction
        # atomic without fsyncing temporary data on every 1,000 rows.
        conn.execute("PRAGMA journal_mode = MEMORY")
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA cache_size = -2048")
        conn.execute("PRAGMA temp_store = FILE")
        return conn
    except BaseException:
        if conn is not None:
            conn.close()
        db_path.unlink(missing_ok=True)
        raise


def _build_logical_activity_snapshot_unlocked(
    log_path: Path,
    *,
    capture_logical_entries: bool = True,
    recent_task_id: str | None = None,
    recent_limit: int | None = None,
) -> sqlite3.Connection:
    """Validate every source into an unlink-on-create, disk-backed snapshot."""

    if (recent_task_id is None) != (recent_limit is None):
        raise RuntimeError("recent activity snapshot query is incomplete")
    if recent_task_id is not None and (
        capture_logical_entries
        or not isinstance(recent_task_id, str)
        or not recent_task_id
        or recent_task_id != recent_task_id.strip()
        or not isinstance(recent_limit, int)
        or recent_limit <= 0
    ):
        raise RuntimeError("recent activity snapshot query is invalid")

    sources = _ordered_activity_sources_unlocked(log_path)
    conn = _open_ephemeral_activity_snapshot_database()
    snapshots: list[_ActivitySourceSnapshot] = []
    recent_entries: deque[tuple[str, str, int]] | None = (
        deque(maxlen=recent_limit)
        if recent_task_id is not None and recent_limit is not None
        else None
    )
    try:
        conn.execute(
            "CREATE TABLE seen_events (event_id TEXT PRIMARY KEY, digest TEXT)"
        )
        conn.execute(
            "CREATE TABLE source_events ("
            "source_idx INTEGER, event_id TEXT, "
            "PRIMARY KEY (source_idx, event_id))"
        )
        conn.execute(
            "CREATE TABLE file_digests ("
            "path TEXT PRIMARY KEY, prefix_digest TEXT, suffix_digest TEXT)"
        )
        conn.execute(
            "CREATE TABLE logical_entries ("
            "sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
            "payload TEXT NOT NULL, source_path TEXT NOT NULL, "
            "line_number INTEGER NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE collapse_events ("
            "sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
            "predecessor_path TEXT, successor_path TEXT NOT NULL, "
            "line_count INTEGER NOT NULL, byte_count INTEGER NOT NULL, "
            "sha256 TEXT NOT NULL)"
        )

        def capture_entry(
            entry: dict[str, Any],
            decoded: str,
            source: Path,
            line_number: int,
        ) -> None:
            if capture_logical_entries:
                conn.execute(
                    "INSERT INTO logical_entries "
                    "(payload, source_path, line_number) VALUES (?, ?, ?)",
                    (decoded, str(source), line_number),
                )
                return
            if recent_entries is not None and (
                str(entry.get("task_id") or "").strip() == recent_task_id
            ):
                recent_entries.append((decoded, str(source), line_number))

        prev_source: Path | None = None
        prev_buffer_1001: list[bytes] = []
        max_ts_std: str | None = None
        max_ts_old: str | None = None

        for source_idx, source in enumerate(sources):
            current_line_count = 0
            source_class = classify_source(source)
            if source_class == "unknown":
                raise RuntimeError(f"Unknown source format: {source.name}")

            if source_class == "legacy_ts_std":
                timestamp = extract_timestamp_from_name(source.name)
                if timestamp:
                    if max_ts_std is not None and timestamp <= max_ts_std:
                        raise RuntimeError(
                            f"Strict name sequence violation: {source.name} "
                            "is out of chronological order"
                        )
                    max_ts_std = timestamp
            elif source_class == "legacy_ts_old":
                timestamp = extract_timestamp_from_name(source.name)
                if timestamp:
                    if max_ts_old is not None and timestamp <= max_ts_old:
                        raise RuntimeError(
                            f"Strict name sequence violation: {source.name} "
                            "is out of chronological order"
                        )
                    max_ts_old = timestamp

            descriptor = os.open(
                source,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                descriptor_stat = os.fstat(descriptor)
                if not stat.S_ISREG(descriptor_stat.st_mode):
                    raise RuntimeError(f"Source is not a regular file: {source}")
                path_stat_before = source.lstat()
                if (
                    stat.S_ISLNK(path_stat_before.st_mode)
                    or path_stat_before.st_dev != descriptor_stat.st_dev
                    or path_stat_before.st_ino != descriptor_stat.st_ino
                ):
                    raise RuntimeError(f"Source is a symlink or changed: {source}")
                file_obj, raw_sha256, raw_byte_count = (
                    _snapshot_activity_source_descriptor(
                        descriptor,
                        source=source,
                        expected_size=descriptor_stat.st_size,
                    )
                )
                snapshot = _ActivitySourceSnapshot(
                    path=source,
                    st_dev=descriptor_stat.st_dev,
                    st_ino=descriptor_stat.st_ino,
                    st_size=descriptor_stat.st_size,
                    st_mtime_ns=descriptor_stat.st_mtime_ns,
                    raw_sha256=raw_sha256,
                )

                try:
                    binary_stream = (
                        gzip.GzipFile(fileobj=file_obj, mode="rb")
                        if source.suffix == ".gz"
                        else file_obj
                    )
                    line_number = 0
                    current_buffer_1001: list[bytes] = []
                    active_source = source.resolve() == log_path.resolve()
                    if active_source:
                        first_position = binary_stream.tell()
                        first_line = binary_stream.readline()
                        if first_line:
                            try:
                                first_entry = strict_activity_json_loads(
                                    first_line.decode("utf-8").strip()
                                )
                            except (
                                UnicodeError,
                                json.JSONDecodeError,
                                DuplicateActivityJSONKeyError,
                            ):
                                first_entry = None
                            if _is_activity_lineage_head(first_entry):
                                line_number = 1
                            else:
                                binary_stream.seek(first_position)

                    buffer_first_line_number = line_number + 1
                    while True:
                        line = binary_stream.readline()
                        if not line:
                            break
                        line_number += 1
                        current_line_count += 1
                        current_buffer_1001.append(line)
                        if len(current_buffer_1001) == 1001:
                            break

                    should_collapse = False
                    overlap_len = 0
                    max_check = min(
                        len(prev_buffer_1001), len(current_buffer_1001)
                    )
                    for count in (999, 1000, 1001):
                        if (
                            count <= max_check
                            and current_buffer_1001[:count]
                            == prev_buffer_1001[-count:]
                        ):
                            overlap_len = count

                    if overlap_len > 0:
                        if prev_source is None:
                            raise RuntimeError(
                                f"Overlap detected with no predecessor: {source.name}"
                            )
                        if overlap_len == 1000:
                            previous_class = classify_source(prev_source)
                            if (
                                previous_class == "legacy_ts_std"
                                and source_class in ("legacy_ts_std", "active")
                            ) or (
                                previous_class == "legacy_ts_old"
                                and source_class == "legacy_ts_old"
                            ):
                                should_collapse = True
                            else:
                                raise RuntimeError(
                                    "Non-collapsible 1000-line overlap between "
                                    f"{prev_source.name} and {source.name}"
                                )
                        else:
                            raise RuntimeError(
                                f"Invalid overlap length {overlap_len} between "
                                f"{prev_source.name} and {source.name}"
                            )
                    elif (
                        len(prev_buffer_1001) >= 1000
                        and len(current_buffer_1001) >= 1000
                    ):
                        matches = sum(
                            1
                            for previous_line, current_line in zip(
                                prev_buffer_1001[-1000:],
                                current_buffer_1001[:1000],
                            )
                            if previous_line == current_line
                        )
                        if matches == 999:
                            raise RuntimeError(
                                "Invalid overlap: mismatch in 1000-line candidate "
                                f"between {prev_source.name} and {source.name}"
                            )

                    if len(current_buffer_1001) >= 1000:
                        prefix_1000_digest = hashlib.sha256(
                            b"".join(current_buffer_1001[:1000])
                        ).hexdigest()
                        cursor = conn.execute(
                            "SELECT path FROM file_digests "
                            "WHERE suffix_digest = ? LIMIT 2",
                            (prefix_1000_digest,),
                        )
                        for (matched_path_raw,) in cursor:
                            matched_path = Path(matched_path_raw)
                            if prev_source is not None and (
                                matched_path == prev_source
                                or matched_path.resolve() == prev_source.resolve()
                            ):
                                continue
                            raise ActivityAuditInvariantError(
                                "Matching non-adjacent older tail detected: "
                                f"{matched_path_raw} -> {source}",
                                invariant="activity_non_adjacent_tail",
                                evidence={
                                    "matched_source": str(matched_path),
                                    "current_source": str(source),
                                    "immediate_predecessor": str(prev_source)
                                    if prev_source is not None
                                    else None,
                                    "source_index": source_idx,
                                    "source_class": source_class,
                                    "prefix_1000_sha256": prefix_1000_digest,
                                    "matched_suffix_1000_sha256": prefix_1000_digest,
                                },
                            )

                    def process_line(
                        raw_line: bytes,
                        current_line_number: int,
                        *,
                        is_collapsed_prefix: bool,
                    ) -> tuple[dict[str, Any], str] | None:
                        if not raw_line.strip():
                            return None
                        try:
                            decoded = raw_line.decode("utf-8", errors="strict")
                        except UnicodeError as exc:
                            raise RuntimeError(
                                f"Bad UTF-8 in {source}:{current_line_number}: {exc}"
                            ) from exc
                        try:
                            entry = strict_activity_json_loads(decoded)
                        except DuplicateActivityJSONKeyError as exc:
                            raise RuntimeError(
                                f"{exc} in {source}:{current_line_number}"
                            ) from exc
                        except json.JSONDecodeError as exc:
                            raise RuntimeError(
                                f"Bad JSON in {source}:{current_line_number}: {exc}"
                            ) from exc
                        if not isinstance(entry, dict):
                            raise RuntimeError(
                                "activity audit row is not an object: "
                                f"{source}:{current_line_number}"
                            )
                        if _is_activity_lineage_head(entry):
                            raise RuntimeError(
                                "activity lineage-head control record is not "
                                f"allowed in payload: {source}:{current_line_number}"
                            )

                        event_id = str(entry.get("event_id") or "").strip()
                        if event_id:
                            try:
                                conn.execute(
                                    "INSERT INTO source_events "
                                    "(source_idx, event_id) VALUES (?, ?)",
                                    (source_idx, event_id),
                                )
                            except sqlite3.IntegrityError as exc:
                                raise RuntimeError(
                                    f"duplicate activity event_id in {source}: "
                                    f"{event_id}"
                                ) from exc
                            digest = _canonical_json_sha256(entry)
                            if not is_collapsed_prefix:
                                try:
                                    conn.execute(
                                        "INSERT INTO seen_events "
                                        "(event_id, digest) VALUES (?, ?)",
                                        (event_id, digest),
                                    )
                                except sqlite3.IntegrityError as exc:
                                    row = conn.execute(
                                        "SELECT digest FROM seen_events "
                                        "WHERE event_id = ?",
                                        (event_id,),
                                    ).fetchone()
                                    detail = (
                                        "payload mismatch"
                                        if row is None or row[0] != digest
                                        else "duplicate across sources"
                                    )
                                    raise RuntimeError(
                                        f"activity event_id {detail}: {event_id}"
                                    ) from exc
                        return entry, decoded

                    for index, raw_line in enumerate(current_buffer_1001):
                        is_collapsed = should_collapse and index < overlap_len
                        physical_line_number = buffer_first_line_number + index
                        processed = process_line(
                            raw_line,
                            physical_line_number,
                            is_collapsed_prefix=is_collapsed,
                        )
                        if processed is not None and not is_collapsed:
                            entry, decoded = processed
                            capture_entry(
                                entry,
                                decoded,
                                source,
                                physical_line_number,
                            )
                    conn.commit()

                    sliding_window_1001 = list(current_buffer_1001)
                    while True:
                        line = binary_stream.readline()
                        if not line:
                            break
                        line_number += 1
                        current_line_count += 1
                        sliding_window_1001.append(line)
                        if len(sliding_window_1001) > 1001:
                            sliding_window_1001.pop(0)
                        processed = process_line(
                            line,
                            line_number,
                            is_collapsed_prefix=False,
                        )
                        if processed is not None:
                            entry, decoded = processed
                            capture_entry(entry, decoded, source, line_number)
                        if current_line_count % 1000 == 0:
                            conn.commit()

                    if len(current_buffer_1001) >= 1000:
                        prefix_1000_digest = hashlib.sha256(
                            b"".join(current_buffer_1001[:1000])
                        ).hexdigest()
                        suffix_1000_digest = hashlib.sha256(
                            b"".join(sliding_window_1001[-1000:])
                        ).hexdigest()
                        conn.execute(
                            "INSERT INTO file_digests "
                            "(path, prefix_digest, suffix_digest) "
                            "VALUES (?, ?, ?)",
                            (
                                str(source),
                                prefix_1000_digest,
                                suffix_1000_digest,
                            ),
                        )

                    if should_collapse:
                        collapsed_bytes = b"".join(
                            current_buffer_1001[:overlap_len]
                        )
                        conn.execute(
                            "INSERT INTO collapse_events "
                            "(predecessor_path, successor_path, line_count, "
                            "byte_count, sha256) VALUES (?, ?, ?, ?, ?)",
                            (
                                str(prev_source) if prev_source is not None else None,
                                str(source),
                                overlap_len,
                                len(collapsed_bytes),
                                hashlib.sha256(collapsed_bytes).hexdigest(),
                            ),
                        )

                    prev_source = source
                    prev_buffer_1001 = list(sliding_window_1001)
                    conn.commit()
                except (EOFError, gzip.BadGzipFile, OSError) as exc:
                    raise RuntimeError(
                        f"Truncated or corrupt gzip/file {source}: {exc}"
                    ) from exc
                finally:
                    file_obj.close()

                path_stat_after = source.lstat()
                if (
                    stat.S_ISLNK(path_stat_after.st_mode)
                    or path_stat_after.st_dev != snapshot.st_dev
                    or path_stat_after.st_ino != snapshot.st_ino
                ):
                    raise RuntimeError(f"Source replaced during read: {source}")
                if (
                    path_stat_after.st_size != snapshot.st_size
                    or path_stat_after.st_mtime_ns != snapshot.st_mtime_ns
                ):
                    raise RuntimeError(
                        f"Source mutated or truncated during read: {source}"
                    )
                snapshots.append(snapshot)
            finally:
                os.close(descriptor)

        _assert_activity_sources_stable_unlocked(log_path, sources, snapshots)
        if recent_entries is not None:
            conn.executemany(
                "INSERT INTO logical_entries "
                "(payload, source_path, line_number) VALUES (?, ?, ?)",
                recent_entries,
            )
        conn.commit()
    except BaseException:
        conn.close()
        raise
    return conn


def _replay_logical_activity_snapshot(
    conn: sqlite3.Connection,
    on_collapse: Callable[[Path | None, Path, int, int, str], None] | None,
) -> Generator[tuple[dict[str, Any], Path, int], None, None]:
    if on_collapse is not None:
        for row in conn.execute(
            "SELECT predecessor_path, successor_path, line_count, "
            "byte_count, sha256 FROM collapse_events ORDER BY sequence"
        ):
            predecessor = Path(row[0]) if row[0] is not None else None
            on_collapse(predecessor, Path(row[1]), row[2], row[3], row[4])
    for payload, source_path, line_number in conn.execute(
        "SELECT payload, source_path, line_number "
        "FROM logical_entries ORDER BY sequence"
    ):
        entry = strict_activity_json_loads(payload)
        if not isinstance(entry, dict):
            raise RuntimeError("validated activity snapshot row is not an object")
        yield entry, Path(source_path), line_number


def validated_activity_event_digests_unlocked(
    log_path: Path,
    event_ids: Iterable[str],
) -> dict[str, str]:
    """Validate all history and return digests only for requested event IDs.

    The caller must hold the activity audit lock. Every source and event is
    still parsed and validated; only the redundant logical replay copy is
    omitted, and Python memory is bounded by the small requested ID set.
    """

    requested: set[str] = set()
    for event_id in event_ids:
        if (
            not isinstance(event_id, str)
            or not event_id
            or event_id != event_id.strip()
        ):
            raise RuntimeError("requested activity event_id is not canonical")
        requested.add(event_id)
    try:
        conn = _build_logical_activity_snapshot_unlocked(
            log_path,
            capture_logical_entries=False,
        )
    except RuntimeError as exc:
        raise activity_audit_invariant_error(
            exc,
            log_path=log_path,
            operation="event_digest_validation",
        ) from exc
    try:
        result: dict[str, str] = {}
        for event_id in requested:
            row = conn.execute(
                "SELECT digest FROM seen_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if row is not None:
                result[event_id] = str(row[0])
        return result
    finally:
        conn.close()


def _resolved_activity_log_path(log_path: Path) -> Path:
    requested_log_path = log_path.expanduser()
    if requested_log_path.is_symlink():
        raise RuntimeError(
            f"activity audit source leaf cannot be a symlink: {requested_log_path}"
        )
    # Keep the lexical path so an ancestor alias cannot silently relocate the
    # governed status root. The component walk also closes the leaf swap
    # window between the compatibility check above and later O_NOFOLLOW opens.
    return _assert_no_symlink_components(
        requested_log_path,
        source="activity audit source",
    )


def stream_logical_activity(
    log_path: Path,
    on_collapse: Callable[[Path | None, Path, int, int, str], None] | None = None,
) -> Generator[tuple[dict[str, Any], Path, int], None, None]:
    """Replay a validation-complete, bounded-disk logical activity snapshot.

    The first row and every collapse callback are withheld until all sources
    pass ordering, JSON, duplicate, identity, metadata, and before/after raw
    digest checks under the shared activity lock. Early stop therefore cannot
    turn incomplete source validation into apparent success.
    """

    requested_log_path = Path(log_path)
    snapshot: sqlite3.Connection | None = None
    try:
        try:
            log_path = _resolved_activity_log_path(requested_log_path)
            with activity_audit_lock_file(log_path, shared=True):
                snapshot = _build_logical_activity_snapshot_unlocked(log_path)
        except RuntimeError as exc:
            raise activity_audit_invariant_error(
                exc,
                log_path=requested_log_path,
                operation="logical_stream_validation",
            ) from exc
        yield from _replay_logical_activity_snapshot(snapshot, on_collapse)
    finally:
        if snapshot is not None:
            snapshot.close()


def _activity_log_exceeds_rotation_threshold_unlocked(
    log_path: Path,
    max_bytes: int,
) -> bool:
    """Check the active leaf size without opening immutable history."""

    if max_bytes <= 0:
        return False
    try:
        descriptor = os.open(
            log_path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except FileNotFoundError:
        return False
    try:
        descriptor_stat = os.fstat(descriptor)
        path_stat = log_path.lstat()
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or stat.S_ISLNK(path_stat.st_mode)
            or (path_stat.st_dev, path_stat.st_ino)
            != (descriptor_stat.st_dev, descriptor_stat.st_ino)
        ):
            raise RuntimeError(
                f"activity audit source must be a stable regular file: {log_path}"
            )
        return descriptor_stat.st_size > max_bytes
    finally:
        os.close(descriptor)


def append_activity_log_entries_unlocked(
    log_path: Path,
    entries: list[dict[str, Any]],
    *,
    rotate_bytes: int | None = None,
    keep_lines: int = 0,
) -> None:
    """Recover, optionally rotate, and durably append while audit EX is held."""

    log_path = _assert_no_symlink_components(
        log_path,
        source="activity log",
    )
    prepare_activity_audit_unlocked(log_path)
    if activity_rotation_writer_guard_active():
        # The guard may intentionally suppress intent recovery, but no writer
        # may append behind that unresolved transaction and invalidate its
        # source digest. Stable, intent-free logs remain appendable while new
        # rotations are paused.
        assert_activity_audit_stable_unlocked(log_path)
    if (
        rotate_bytes is not None
        and _activity_log_exceeds_rotation_threshold_unlocked(
            log_path,
            rotate_bytes,
        )
    ):
        rotate_activity_log_unlocked(
            log_path,
            max_bytes=rotate_bytes,
            keep_lines=keep_lines,
        )
    if not entries:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    created = not log_path.exists()
    with log_path.open("ab") as handle:
        for entry in entries:
            handle.write(
                (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
            )
        handle.flush()
        os.fsync(handle.fileno())
    if created:
        _fsync_directory(log_path.parent)


def _tail_bytes_unlocked(path: Path, max_lines: int | None) -> bytes | None:
    if not path.exists():
        return None
    if max_lines is None or max_lines <= 0:
        return path.read_bytes()
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        buffer = bytearray()
        line_count = 0
        while position > 0 and line_count <= max_lines:
            read_size = min(1 << 16, position)
            position -= read_size
            handle.seek(position)
            buffer[0:0] = handle.read(read_size)
            line_count = buffer.count(b"\n")
    tail = bytes(buffer)
    if line_count > max_lines:
        split_at = -1
        for _ in range(line_count - max_lines):
            split_at = tail.find(b"\n", split_at + 1)
            if split_at < 0:
                break
        if split_at >= 0:
            tail = tail[split_at + 1 :]
    return tail


def read_activity_log_tail_bytes(
    log_path: Path,
    *,
    max_lines: int | None,
) -> bytes | None:
    """Recover under EX, then return one consistent active-tail snapshot under SH."""

    with activity_audit_lock_file(log_path, shared=False, nonblocking=False):
        prepare_activity_audit_unlocked(log_path)
    with activity_audit_lock_file(log_path, shared=True, nonblocking=False):
        assert_activity_audit_stable_unlocked(log_path)
        return _tail_bytes_unlocked(log_path, max_lines)


def _activity_write_is_resilient(config: dict[str, Any]) -> bool:
    """SUPERVISOR-REWRITE Phase 2 (§3.2): the append hot path must never `raise`
    on an integrity/recovery fault — that class of failure crash-looped the fleet
    for ~4 hours. Integrity is now owned by the offline verifier
    (rewrite/verify_activity_integrity.py); a write only needs to durably append
    and let the cycle continue. Default on; set PANTHEON_ACTIVITY_LOG_STRICT=1 or
    config.activity_log_strict_hot_path=true to restore fail-closed writes (e.g.
    for tests that assert the incumbent raise)."""
    if str(os.environ.get("PANTHEON_ACTIVITY_LOG_STRICT") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }:
        return False
    return not bool(config.get("activity_log_strict_hot_path", False))


# SUPERVISOR-REWRITE Phase 2 (§3.2): only genuine *lineage-integrity drift* is
# safe to append through — appending never corrupts already-broken lineage, and
# the offline verifier owns the alert. Security faults (symlink / non-regular
# leaf) and correctness guards (a rotation intent is mid-flight → "recovery is
# pending") must STILL fail closed: appending there would write through a symlink
# or invalidate an in-progress rotation's source digest. Discriminate by an
# explicit allow-list; anything not recognised as drift re-raises unchanged.
_ACTIVITY_LINEAGE_DRIFT_MARKERS = (
    "lineage archive is missing",
    "archives do not match lineage",
    "conservation mismatch",
    "conservation counts are inconsistent",
    "basename digest mismatch",
    "content-addressed boundary mismatch",
    "boundary matches non-adjacent",
)


def _activity_fault_is_lineage_drift(exc: BaseException) -> bool:
    if isinstance(exc, ActivityAuditInvariantError):
        return True  # fail-closed reader integrity error = drift by construction
    message = str(exc).lower()
    return any(marker in message for marker in _ACTIVITY_LINEAGE_DRIFT_MARKERS)


def _force_append_activity_log_unlocked(log_path: Path, entries: list[dict[str, Any]]) -> None:
    """Append-only, no recovery, no lineage validation, no rotation — the §3.2
    fallback used only when the validated path faulted. Guarantees the entry is
    durably recorded; any lineage drift is surfaced later by the offline verifier."""
    if not entries:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    created = not log_path.exists()
    with log_path.open("ab") as handle:
        for entry in entries:
            handle.write((json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    if created:
        _fsync_directory(log_path.parent)


def write_activity_log(config: dict[str, Any], entry: dict[str, Any]) -> None:
    payload = {
        "ts": utc_now(),
        "agent": "Orchestrator",
        **entry,
    }
    log_path = config_path(config, "activity_log")
    try:
        with activity_audit_lock_file(log_path, shared=False, nonblocking=False):
            append_activity_log_entries_unlocked(
                log_path,
                [payload],
                rotate_bytes=_activity_log_rotate_threshold(config),
                keep_lines=0,
            )
    except (ActivityAuditInvariantError, RuntimeError) as exc:
        # Only lineage-integrity drift is made non-fatal; security/correctness
        # faults keep their fail-closed contract even in resilient mode.
        if not _activity_write_is_resilient(config) or not _activity_fault_is_lineage_drift(exc):
            raise
        # §3.2: warn (never raise), then guarantee the append lands so the cycle
        # keeps dispatching/finalizing/archiving. Offline verifier owns integrity.
        print(
            "activity-log integrity/recovery fault on write "
            f"({type(exc).__name__}: {str(exc)[:200]}); appending without recovery "
            "and continuing (run rewrite/verify_activity_integrity.py to inspect)",
            file=sys.stderr,
        )
        try:
            with activity_audit_lock_file(log_path, shared=False, nonblocking=False):
                _force_append_activity_log_unlocked(log_path, [payload])
        except (ActivityAuditInvariantError, RuntimeError, OSError):
            # Last-resort append without the audit lock — losing an activity row
            # is worse than a brief lock gap; still never raises into the cycle.
            _force_append_activity_log_unlocked(log_path, [payload])


def runtime_log_path(prefix: str, target: str) -> Path:
    slug = normalize_agent_id(target) or "unknown"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    suffix = uuid.uuid4().hex[:6]
    return runtime_sidecar_dir() / "logs" / f"{stamp}-{prefix}-{slug}-{suffix}.log"


def new_runtime_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def worker_runtime_paths(config: dict[str, Any], run_id: str) -> dict[str, Path]:
    safe_run_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(run_id or "worker")).strip("-") or "worker"
    try:
        root = config_path(config, "state_file").parent / "worker-runtime"
    except KeyError:
        try:
            root = config_path(config, "status_file").parent / ".orchestrator" / "worker-runtime"
        except KeyError:
            root = ORCHESTRATOR_DIR / "worker-runtime"
    return {
        "heartbeat_path": root / "heartbeats" / f"{safe_run_id}.json",
        "status_path": root / "status" / f"{safe_run_id}.json",
    }


def spawn_background_process(
    command: list[str],
    *,
    cwd: Path | None = None,
    log_path: Path,
    env: dict[str, str] | None = None,
    run_id: str | None = None,
    heartbeat_path: Path | None = None,
    status_path: Path | None = None,
    heartbeat_interval_seconds: int = 15,
    runner_enabled: bool = True,
) -> tuple[subprocess.Popen[str], Path]:
    ensure_parent(log_path)
    command_to_spawn = list(command)
    spawn_env = dict(env or os.environ)
    scrub_worker_authority_secrets(spawn_env)
    if runner_enabled and run_id:
        if heartbeat_path is None:
            heartbeat_path = log_path.with_suffix(log_path.suffix + ".heartbeat.json")
        if status_path is None:
            status_path = log_path.with_suffix(log_path.suffix + ".status.json")
        ensure_parent(heartbeat_path)
        ensure_parent(status_path)
        spawn_env["ORCH_RUN_ID"] = str(run_id)
        spawn_env["ORCH_HEARTBEAT_PATH"] = str(heartbeat_path)
        spawn_env["ORCH_RUNNER_STATUS_PATH"] = str(status_path)
        command_to_spawn = [
            sys.executable,
            str(ORCHESTRATOR_DIR / "worker_runner.py"),
            "--run-id",
            str(run_id),
            "--heartbeat-path",
            str(heartbeat_path),
            "--status-path",
            str(status_path),
            "--heartbeat-interval-seconds",
            str(max(1, int(heartbeat_interval_seconds or 15))),
            "--",
            *command,
        ]
    handle = log_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command_to_spawn,
            cwd=str(cwd or ROOT),
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            env=spawn_env,
            start_new_session=True,
        )
    finally:
        # Popen duplicates the descriptor for the child.  The supervisor must
        # not retain one parent descriptor for every worker lifetime.
        handle.close()
    return process, log_path


def load_status(config: dict[str, Any]) -> dict[str, Any]:
    runtime_env = task_state_store_runtime_env(config)
    from rewrite import task_state_store

    event_log = runtime_env[TASK_STATE_EVENT_LOG_ENV]
    # One validated pass. Pairing load_events with project_latest_state
    # replayed and revalidated the whole journal twice, and this runs many
    # times per supervisor cycle.
    snapshot = task_state_store.load_snapshot(event_log)
    if not snapshot["event_count"]:
        raise RuntimeError(
            "authoritative task-state journal is empty; refusing ai-status.json fallback"
        )
    state = snapshot["state"]
    if not isinstance(state, dict) or not state:
        raise RuntimeError("authoritative task-state projection is not a non-empty object")
    return state


def write_status(config: dict[str, Any], payload: dict[str, Any], *, source: str) -> None:
    """Persist canonical task state, journaling before derived-file projection."""

    runtime_env = task_state_store_runtime_env(config)
    from rewrite import task_state_store

    task_state_store.append_state_commit(
        runtime_env[TASK_STATE_EVENT_LOG_ENV],
        payload,
        source=source,
    )
    write_json(config_path(config, "status_file"), payload)


def compact_whitespace(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def approval_tool_input_signature(tool_input: Any) -> str:
    try:
        payload = stable_json(tool_input if tool_input is not None else {})
    except TypeError:
        payload = compact_whitespace(tool_input)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def approval_tool_input_preview(tool_input: Any, *, limit: int = 220) -> str:
    if isinstance(tool_input, dict):
        for key in ("command", "cmd", "raw_command", "query", "path", "file", "url"):
            value = compact_whitespace(tool_input.get(key))
            if value:
                return value[:limit]
        preview = compact_whitespace(stable_json(tool_input))
        return preview[:limit]
    if isinstance(tool_input, list):
        preview = compact_whitespace(stable_json(tool_input))
        return preview[:limit]
    return compact_whitespace(tool_input)[:limit]


def summarize_failure_reason(reason: str | None, provider: str | None = None, *, limit: int = 180) -> dict[str, str]:
    raw = compact_whitespace(reason)
    provider_label = str(provider or "").strip() or "provider"
    if not raw:
        return {"kind": "unknown", "summary": f"{provider_label} failure", "detail": ""}

    lowered = raw.lower()
    if is_github_cli_auth_failure(raw):
        return {"kind": "tool_auth", "summary": "GitHub CLI auth unavailable", "detail": raw[: max(420, limit)]}
    if "you have no quota" in lowered:
        return {"kind": "quota", "summary": "402 You have no quota", "detail": raw[: max(420, limit)]}
    if "credit balance is too low" in lowered or "billing_error" in lowered:
        return {"kind": "quota", "summary": "Credit balance is too low", "detail": raw[: max(420, limit)]}
    if "free daily quota has been reached" in lowered:
        return {"kind": "quota", "summary": "Daily quota exceeded", "detail": raw[: max(420, limit)]}
    if "hit your usage limit" in lowered:
        return {"kind": "quota", "summary": "Codex usage limit reached", "detail": raw[: max(420, limit)]}
    if "hit your limit" in lowered:
        return {"kind": "quota", "summary": "Rate limit reached", "detail": raw[: max(420, limit)]}
    if "rate limit" in lowered or "rate limited" in lowered or "capacity" in lowered or "quota exceeded" in lowered:
        return {"kind": "capacity", "summary": "Capacity / rate limit failure", "detail": raw[: max(420, limit)]}
    # Codex (OpenAI CLI) revoked/expired-token failures. Anchor on Codex's real
    # error strings rather than a bare "401" so chair narratives that merely
    # mention a 401 are not misclassified as auth.
    codex_auth_markers = (
        "refresh_token_invalidated",
        "token_invalidated",
        "refresh token was revoked",
        "access token could not be refreshed",
        "failed to refresh token: 401",
        "your session has ended. please log in again",
        "authentication token has been invalidated",
    )
    if any(marker in lowered for marker in codex_auth_markers):
        return {"kind": "auth", "summary": "Authentication failure", "detail": raw[: max(420, limit)]}
    if "responses_websocket" in lowered and "http error: 401" in lowered:
        return {"kind": "auth", "summary": "Authentication failure", "detail": raw[: max(420, limit)]}
    if "unauthorized" in lowered or "authentication" in lowered or "invalid api key" in lowered:
        return {"kind": "auth", "summary": "Authentication failure", "detail": raw[: max(420, limit)]}
    if "an unexpected critical error occurred" in lowered:
        return {"kind": "unknown_critical", "summary": "Unexpected critical provider failure", "detail": raw[: max(420, limit)]}
    return {"kind": "terminal", "summary": raw[:limit], "detail": raw[: max(420, limit)]}


def write_failure_evidence(
    config: dict[str, Any],
    *,
    worker: dict[str, Any],
    reason: str | None,
    failure_kind: str | None = None,
) -> str | None:
    run_id = str(worker.get("run_id") or "").strip()
    if not run_id:
        return None
    path = evidence_dir(config) / f"{normalize_agent_id(run_id) or run_id}.json"
    ensure_parent(path)
    payload = {
        "recorded_at": utc_now(),
        "task_id": worker.get("task_id"),
        "run_id": run_id,
        "provider": worker.get("provider"),
        "agent_id": worker.get("agent_id"),
        "failure_kind": failure_kind,
        "reason": reason or "",
        "log_path": worker.get("log_path"),
        "session_id": worker.get("session_id"),
        "queue_event_id": worker.get("queue_event_id"),
    }
    write_json(path, payload)
    return relpath(path)


def write_approval_evidence(
    config: dict[str, Any],
    *,
    approval_id: str | None,
    stage: str,
    payload: dict[str, Any],
) -> str | None:
    approval_slug = normalize_agent_id(approval_id or "approval") or "approval"
    stage_slug = normalize_agent_id(stage) or "event"
    path = evidence_dir(config) / f"{approval_slug}-{stage_slug}.json"
    ensure_parent(path)
    write_json(
        path,
        {
            "recorded_at": utc_now(),
            "approval_id": approval_id,
            "stage": stage,
            **payload,
        },
    )
    return relpath(path)


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


if __name__ == "__main__":
    print("This module is shared by the orchestrator scripts and is not meant to be run directly.", file=sys.stderr)
    raise SystemExit(1)
