#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
ROOT = THIS_DIR.parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from common import config_path, durable_write_bytes, load_config, repo_root_for_config, utc_now, write_activity_log, LockContentionError, resolved_coordinator_status_root
from runtime_state import default_state, runtime_state_lock, save_runtime_state


ACTIVE_WORKER_STATUSES = {
    "running",
    "started",
    "waiting_approval",
    "suspended_approval",
    "retry_backoff",
    "stalled",
}

SUPERVISOR_PUBLIC_AUTHORITY_ENV_NAMES = (
    "BRIDGE_SIGNING_PUBLIC_KEYS_JSON",
    "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON",
)
SUPERVISOR_FORBIDDEN_AUTHORITY_ENV_NAMES = (
    "BRIDGE_SIGNING_PRIVATE_KEY",
    "BRIDGE_SIGNING_KEY",
    "BRIDGE_SIGNING_KEY_ID",
    "PANTHEON_CANONICAL_MUTATION_ASSERTION_PRIVATE_KEY",
    "PANTHEON_CANONICAL_MUTATION_ASSERTION_KEY",
    "PANTHEON_CANONICAL_MUTATION_ASSERTION_KEY_ID",
    "PANTHEON_CANONICAL_MUTATION_ASSERTION_JSON",
)


def supervisor_restart_environment(source: dict[str, str]) -> dict[str, str]:
    """Build the final supervisor environment with verifier-only authority."""

    env = dict(source)
    for name in SUPERVISOR_FORBIDDEN_AUTHORITY_ENV_NAMES:
        env.pop(name, None)
    for name in SUPERVISOR_PUBLIC_AUTHORITY_ENV_NAMES:
        raw = str(env.get(name) or "").strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{name} must be valid JSON") from exc
        if not isinstance(payload, dict) or not payload or any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            or not value.strip()
            for key, value in payload.items()
        ):
            raise ValueError(f"{name} must be a non-empty public-key map")
        env[name] = raw
    return env


def parse_utc_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def resolve_repo_path(value: str | Path | None, default: str) -> Path:
    raw = Path(str(value or default))
    if not raw.is_absolute():
        raw = ROOT / raw
    return raw


def watchdog_settings(config: dict[str, Any]) -> dict[str, Any]:
    supervisor_settings = config.get("supervisor", {}) if isinstance(config.get("supervisor"), dict) else {}
    settings = dict(config.get("watchdog", {}) if isinstance(config.get("watchdog"), dict) else {})
    settings.setdefault("enabled", True)
    settings.setdefault("heartbeat_stale_seconds", max(900, int(float(supervisor_settings.get("stall_after_seconds", 300))) * 3))
    settings.setdefault("state_file", ".orchestrator/watchdog-state.json")
    settings.setdefault("metrics_file", ".orchestrator/metrics/supervisor-watchdog.jsonl")
    settings.setdefault("contention_metrics_file", ".orchestrator/metrics/supervisor-watchdog-contention.jsonl")
    settings.setdefault("restart_budget_window_seconds", 900)
    settings.setdefault("max_restarts_per_window", 2)
    settings.setdefault("max_restarts_per_hour", 4)
    settings.setdefault("backoff_schedule_seconds", [30, 120, 300, 900])
    settings.setdefault("circuit_cooldown_seconds", 1800)
    settings.setdefault("safe_mode_seconds", 120)
    settings.setdefault("min_disk_free_gb", 2.0)
    settings.setdefault("max_disk_used_percent", 95.0)
    settings.setdefault("min_memory_available_mb", 512)
    settings.setdefault("max_load_1m", max(4.0, float(os.cpu_count() or 1) * 4.0))
    settings.setdefault("max_active_workers", 12)
    settings.setdefault("supervisor_command", ["python3", "-u", ".orchestrator/supervisor.py", "--verbose"])
    settings.setdefault("contention_deadline_seconds", 2.0)
    settings.setdefault("intentional_restart_ttl_seconds", 300)
    return settings


def supervisor_pid_path(config: dict[str, Any]) -> Path:
    return config_path(config, "state_file").parent / "supervisor.pid"


def supervisor_lock_path(config: dict[str, Any]) -> Path:
    coord_root = resolved_coordinator_status_root(config)
    return coord_root / ".orchestrator" / "supervisor.lock"


def supervisor_promotion_lock_path(config: dict[str, Any]) -> Path:
    coord_root = resolved_coordinator_status_root(config)
    return coord_root / ".orchestrator" / "supervisor-runtime-promotion.lock"


def exclusive_lock_held(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        handle = open(path, "a+", encoding="utf-8")
    except OSError:
        return False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        handle.close()


def supervisor_lock_held(config: dict[str, Any]) -> bool:
    """Return True if a live supervisor holds the singleton flock.

    This is the authoritative liveness signal: supervisor.py holds an exclusive
    fcntl.flock on supervisor.lock for its whole lifetime and the kernel releases
    it on death. Unlike supervisor.pid (which atexit clear_supervisor_pid unlinks,
    so it is legitimately absent during every clean-restart seam), the flock never
    spuriously reads as "missing" while a supervisor is alive. We probe by trying a
    NON-BLOCKING exclusive lock: failure means someone else holds it (alive);
    success means nobody holds it, so we release immediately and report dead.
    """
    return exclusive_lock_held(supervisor_lock_path(config))


def watchdog_state_path(config: dict[str, Any], settings: dict[str, Any] | None = None) -> Path:
    settings = settings or watchdog_settings(config)
    return resolve_repo_path(settings.get("state_file"), ".orchestrator/watchdog-state.json")


def watchdog_metrics_path(config: dict[str, Any], settings: dict[str, Any] | None = None) -> Path:
    settings = settings or watchdog_settings(config)
    return resolve_repo_path(settings.get("metrics_file"), ".orchestrator/metrics/supervisor-watchdog.jsonl")


def intentional_restart_path(config: dict[str, Any], settings: dict[str, Any] | None = None) -> Path:
    settings = settings or watchdog_settings(config)
    configured = settings.get("intentional_restart_file")
    if configured:
        raw = Path(str(configured))
        return raw if raw.is_absolute() else config_path(config, "state_file").parent / raw
    return config_path(config, "state_file").parent / "supervisor-restart-intent.json"


def _assert_regular_watchdog_leaf(path: Path, descriptor: int, *, label: str) -> None:
    descriptor_stat = os.fstat(descriptor)
    path_stat = path.lstat()
    if (
        not stat.S_ISREG(descriptor_stat.st_mode)
        or stat.S_ISLNK(path_stat.st_mode)
        or path_stat.st_dev != descriptor_stat.st_dev
        or path_stat.st_ino != descriptor_stat.st_ino
    ):
        raise RuntimeError(f"{label} data leaf changed during I/O: {path}")


def _read_watchdog_bytes(path: Path, *, label: str) -> bytes | None:
    if path.is_symlink():
        raise RuntimeError(f"{label} data leaf cannot be a symlink: {path}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    try:
        _assert_regular_watchdog_leaf(path, descriptor, label=label)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        _assert_regular_watchdog_leaf(path, descriptor, label=label)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _pread_exact(descriptor: int, size: int, offset: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.pread(descriptor, remaining, offset)
        if not chunk:
            break
        chunk = chunk[:remaining]
        chunks.append(chunk)
        offset += len(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _write_watchdog_json_locked(path: Path, payload: dict[str, Any], *, label: str) -> None:
    try:
        existing_stat = path.lstat()
    except FileNotFoundError:
        existing_stat = None
    if existing_stat is not None and stat.S_ISLNK(existing_stat.st_mode):
        raise RuntimeError(f"{label} data leaf cannot be a symlink: {path}")
    if existing_stat is not None and not stat.S_ISREG(existing_stat.st_mode):
        raise RuntimeError(f"{label} data leaf must be a regular file: {path}")
    serialized = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    durable_write_bytes(path, serialized)
    if _read_watchdog_bytes(path, label=label) != serialized:
        raise RuntimeError(f"{label} readback mismatch: {path}")


def record_intentional_restart(
    config: dict[str, Any],
    *,
    old_pid: int,
    target_sha: str,
    now: datetime | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Durably declare a planned supervisor stop before deployment sends TERM."""
    settings = settings or watchdog_settings(config)
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    target = str(target_sha or "").strip().lower()
    if old_pid <= 0:
        raise ValueError("intentional restart old_pid must be positive")
    if not re.fullmatch(r"[0-9a-f]{40,64}", target):
        raise ValueError("intentional restart target_sha must be a full git SHA")
    ttl_seconds = max(30, int(settings.get("intentional_restart_ttl_seconds", 300)))
    intent = {
        "version": 1,
        "kind": "intentional_deploy_restart",
        "created_at": isoformat_utc(now),
        "expires_at": isoformat_utc(now + timedelta(seconds=ttl_seconds)),
        "old_pid": old_pid,
        "target_sha": target,
    }
    # Serialize the handoff declaration with watchdog decisions. The deploy
    # command waits for an in-flight probe instead of racing it, and only sends
    # TERM after the durable record is visible.
    with runtime_state_lock(config, shared=False, nonblocking=False):
        _write_watchdog_json_locked(
            intentional_restart_path(config, settings),
            intent,
            label="intentional restart",
        )
    return intent


def load_valid_intentional_restart(
    config: dict[str, Any],
    *,
    now: datetime,
    candidate_pids: set[int],
    settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return a fresh, PID-bound deploy restart intent; stale/mismatched means none."""
    settings = settings or watchdog_settings(config)
    raw_bytes = _read_watchdog_bytes(
        intentional_restart_path(config, settings),
        label="intentional restart",
    )
    if not raw_bytes or not raw_bytes.strip():
        return None
    try:
        intent = json.loads(raw_bytes)
    except json.JSONDecodeError:
        return None
    if not isinstance(intent, dict) or intent.get("kind") != "intentional_deploy_restart":
        return None
    created_at = parse_utc_timestamp(str(intent.get("created_at") or ""))
    expires_at = parse_utc_timestamp(str(intent.get("expires_at") or ""))
    try:
        old_pid = int(intent.get("old_pid"))
    except (TypeError, ValueError):
        return None
    target_sha = str(intent.get("target_sha") or "").strip().lower()
    if (
        created_at is None
        or expires_at is None
        or created_at > now + timedelta(seconds=30)
        or now > expires_at
        or old_pid not in candidate_pids
        or not re.fullmatch(r"[0-9a-f]{40,64}", target_sha)
    ):
        return None
    return intent


def consume_intentional_restart(
    config: dict[str, Any],
    settings: dict[str, Any] | None = None,
) -> None:
    path = intentional_restart_path(config, settings)
    if path.is_symlink():
        raise RuntimeError(f"intentional restart data leaf cannot be a symlink: {path}")
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _append_watchdog_jsonl_locked(path: Path, payload: dict[str, Any], *, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError(f"{label} data leaf cannot be a symlink: {path}")
    serialized = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        _assert_regular_watchdog_leaf(path, descriptor, label=label)
        offset = os.lseek(descriptor, 0, os.SEEK_END)
        if offset and os.pread(descriptor, 1, offset - 1) != b"\n":
            raise RuntimeError(f"{label} is not newline terminated: {path}")
        view = memoryview(serialized)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(f"{label} append made no progress")
            view = view[written:]
        os.fsync(descriptor)
        _assert_regular_watchdog_leaf(path, descriptor, label=label)
        if _pread_exact(descriptor, len(serialized), offset) != serialized:
            raise RuntimeError(f"{label} readback mismatch: {path}")
        _assert_regular_watchdog_leaf(path, descriptor, label=label)
    finally:
        os.close(descriptor)
    directory_descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def read_pid_file(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def pid_is_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        waited_pid, _ = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return True
    except OSError:
        return True
    return waited_pid == 0


def active_worker_count(runtime_state: dict[str, Any]) -> int:
    """Return the supervisor's recorded active-worker count for diagnostics.

    Runtime state is intentionally not a liveness authority.  A dead supervisor
    cannot reap its own stale worker rows, so using this value as a restart gate
    can permanently suppress the watchdog that would repair that supervisor.
    """
    workers = runtime_state.get("workers", {}) if isinstance(runtime_state.get("workers"), dict) else {}
    return sum(1 for worker in workers.values() if str(worker.get("status") or "") in ACTIVE_WORKER_STATUSES)


def cmdline_is_worker_runner(parts: list[str]) -> bool:
    """Match only the one-per-worker wrapper, never CLI children or prompts."""
    for part in parts[:4]:
        if not part.startswith(("/", ".")) or any(character.isspace() for character in part):
            continue
        path = Path(part)
        if path.name == "worker_runner.py" and ".orchestrator" in path.parts:
            return True
    return False


def worker_runner_process_identity(proc_dir: Path) -> tuple[int, int]:
    """Return a PID plus Linux start-time identity to reject PID reuse."""
    pid = int(proc_dir.name)
    raw_stat = (proc_dir / "stat").read_text(encoding="utf-8")
    command_end = raw_stat.rfind(")")
    if command_end < 0:
        raise ValueError(f"malformed stat record for pid {pid}")
    # Fields after the command start with field 3 (state); starttime is field
    # 22, therefore index 19 in this suffix.
    suffix = raw_stat[command_end + 1 :].split()
    if len(suffix) <= 19:
        raise ValueError(f"stat record missing starttime for pid {pid}")
    return pid, int(suffix[19])


def process_working_directory(
    pid: int | None,
    *,
    proc_root: Path = Path("/proc"),
) -> tuple[str | None, str | None]:
    """Resolve the checkout a live process is actually executing from.

    ``ROOT`` only says where this watchdog module lives. The live supervisor can
    be running from a different checkout entirely -- the split-root incident had
    the supervisor in ``dev-root-6692d51c9bc5`` while worker runners launched
    from ``dev-root-29054ab270d5``. Only ``/proc/<pid>/cwd`` knows which one is
    live, so root coherence has to be read from the process, not assumed.
    """
    if pid is None or pid <= 0:
        return None, "no_pid"
    try:
        return str(Path(os.readlink(proc_root / str(pid) / "cwd")).resolve()), None
    except OSError as exc:
        return None, f"cwd_unreadable:{type(exc).__name__}"


def scan_worker_runner_roots(proc_root: Path = Path("/proc")) -> tuple[set[str], str | None]:
    """Return the distinct checkouts live worker runners execute from."""
    roots: set[str] = set()
    errors: list[str] = []
    try:
        proc_dirs = list(proc_root.iterdir())
    except OSError as exc:
        return roots, f"proc_scan_failed:{type(exc).__name__}"

    for proc_dir in proc_dirs:
        if not proc_dir.name.isdigit():
            continue
        try:
            raw_cmdline = (proc_dir / "cmdline").read_bytes()
        except FileNotFoundError:
            continue
        except OSError as exc:
            if exc.errno in {errno.ENOENT, errno.ESRCH}:
                continue
            errors.append(f"pid={proc_dir.name}:cmdline:{type(exc).__name__}")
            continue
        if not raw_cmdline:
            continue
        parts = [part.decode("utf-8", errors="ignore") for part in raw_cmdline.split(b"\x00") if part]
        if not cmdline_is_worker_runner(parts):
            continue
        # Prefer the runner script's own checkout: a worker runs inside a task
        # worktree lease, so its cwd is not the control-plane root.
        runner_root: str | None = None
        for part in parts[:4]:
            path = Path(part)
            if path.name == "worker_runner.py" and ".orchestrator" in path.parts:
                runner_root = str(path.resolve().parent.parent)
                break
        if runner_root is None:
            runner_root, _error = process_working_directory(int(proc_dir.name), proc_root=proc_root)
        if runner_root:
            roots.add(runner_root)

    return roots, (";".join(errors[:8]) if errors else None)


def supervisor_root_report(
    config: dict[str, Any],
    pid: int | None,
    *,
    proc_root: Path = Path("/proc"),
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Report the checkout the live supervisor and worker runners actually use.

    ``sync-dev-root.sh`` used to repair a *default* ``dev-root`` path, so a
    supervisor started from any other checkout silently stayed 63 commits behind
    ``origin/dev`` while the sync log reported success. Publishing the observed
    roots makes that split visible as evidence rather than as a mystery.
    """
    settings = settings or watchdog_settings(config)
    expected_root = str(resolve_repo_path(settings.get("supervisor_root"), str(ROOT)).resolve())
    active_root, active_root_error = process_working_directory(pid, proc_root=proc_root)
    worker_roots, worker_root_error = scan_worker_runner_roots(proc_root)
    return {
        "expected_root": expected_root,
        "active_root": active_root,
        "active_root_error": active_root_error,
        "split_from_expected": bool(active_root and active_root != expected_root),
        "worker_runner_roots": sorted(worker_roots),
        "worker_runner_root_error": worker_root_error,
        "split_from_worker_runners": bool(
            active_root and worker_roots and {active_root} != worker_roots
        ),
    }


def scan_live_worker_runner_identities(
    proc_root: Path = Path("/proc"),
) -> tuple[set[tuple[int, int]], str | None]:
    """Scan live worker wrappers and return deduplicated process identities.

    The wrapper is the only one-per-worker process.  Its CLI shim and model
    binary inherit the same prompt, so substring matching would count one run
    roughly three times.  Any scan failure is returned to the resource gate so
    the watchdog fails closed instead of restarting from an unproven count.
    """
    identities: set[tuple[int, int]] = set()
    errors: list[str] = []
    try:
        proc_dirs = list(proc_root.iterdir())
    except OSError as exc:
        return identities, f"proc_scan_failed:{type(exc).__name__}:{exc}"

    for proc_dir in proc_dirs:
        if not proc_dir.name.isdigit():
            continue
        try:
            raw_cmdline = (proc_dir / "cmdline").read_bytes()
        except FileNotFoundError:
            # Normal exit race while walking /proc.
            continue
        except OSError as exc:
            if exc.errno in {errno.ENOENT, errno.ESRCH}:
                continue
            errors.append(f"pid={proc_dir.name}:cmdline:{type(exc).__name__}")
            continue
        if not raw_cmdline:
            continue
        parts = [part.decode("utf-8", errors="ignore") for part in raw_cmdline.split(b"\x00") if part]
        if not cmdline_is_worker_runner(parts):
            continue
        try:
            identities.add(worker_runner_process_identity(proc_dir))
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            errors.append(f"pid={proc_dir.name}:identity:{type(exc).__name__}")

    error = ";".join(errors[:8]) if errors else None
    return identities, error


def load_watchdog_state(config: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    with runtime_state_lock(config, shared=True, nonblocking=False):
        raw_bytes = _read_watchdog_bytes(watchdog_state_path(config, settings), label="watchdog state")
        raw = json.loads(raw_bytes) if raw_bytes and raw_bytes.strip() else {}
        state = raw if isinstance(raw, dict) else {}
        state.setdefault("version", 1)
        state.setdefault("updated_at", None)
        state.setdefault("restart_attempts", [])
        state.setdefault("circuit", {"open": False, "reason": None, "opened_at": None, "until": None})
        state.setdefault("last_decision", None)
        return state


def save_watchdog_state(config: dict[str, Any], state: dict[str, Any], settings: dict[str, Any] | None = None) -> None:
    with runtime_state_lock(config, shared=False, nonblocking=False):
        state["version"] = 1
        state["updated_at"] = utc_now()
        _write_watchdog_json_locked(watchdog_state_path(config, settings), state, label="watchdog state")


def append_watchdog_metric(config: dict[str, Any], payload: dict[str, Any], settings: dict[str, Any] | None = None) -> None:
    with runtime_state_lock(config, shared=False, nonblocking=False):
        event = {
            "version": 1,
            "event_id": f"watchdog-{int(time.time() * 1000)}-{os.getpid()}",
            "at": utc_now(),
            **payload,
        }
        _append_watchdog_jsonl_locked(
            watchdog_metrics_path(config, settings),
            event,
            label="watchdog metrics",
        )


def append_watchdog_contention_metric(config: dict[str, Any], payload: dict[str, Any], settings: dict[str, Any] | None = None) -> None:
    settings = settings or watchdog_settings(config)
    path = resolve_repo_path(settings.get("contention_metrics_file"), ".orchestrator/metrics/supervisor-watchdog-contention.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError(f"contention metrics data leaf cannot be a symlink: {path}")

    event = {
        "version": 1,
        "event_id": f"watchdog-contention-{int(time.time() * 1000)}-{os.getpid()}",
        "at": utc_now(),
        **payload,
    }
    serialized = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")

    lock_path = path.with_suffix(".lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)

    lock_descriptor = None
    try:
        lock_descriptor = os.open(lock_path, flags, 0o600)
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            if getattr(exc, "errno", None) in (errno.EAGAIN, errno.EWOULDBLOCK):
                sys.stderr.write("watchdog contention metric write dropped due to lock contention\n")
                sys.stderr.flush()
                return
            raise

        write_flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, write_flags, 0o600)
        try:
            _assert_regular_watchdog_leaf(path, descriptor, label="contention metrics")
            offset = os.lseek(descriptor, 0, os.SEEK_END)
            if offset and os.pread(descriptor, 1, offset - 1) != b"\n":
                raise RuntimeError(f"contention metrics is not newline terminated: {path}")
            view = memoryview(serialized)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("contention metrics append made no progress")
                view = view[written:]
            os.fsync(descriptor)
            _assert_regular_watchdog_leaf(path, descriptor, label="contention metrics")
            if _pread_exact(descriptor, len(serialized), offset) != serialized:
                raise RuntimeError(f"contention metrics readback mismatch: {path}")
        finally:
            os.close(descriptor)

        directory_descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)

        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
    finally:
        if lock_descriptor is not None:
            os.close(lock_descriptor)


def load_runtime_state_file(config: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    path = config_path(config, "state_file")
    try:
        if not path.is_file():
            # A missing cache is the one normal first-start shape.  Return the
            # complete V2 envelope so the watchdog can stamp safe mode without
            # attempting to persist an invalid partial object.
            return default_state(), "runtime_state_missing"
        text = path.read_text(encoding="utf-8", errors="strict")
        if not text.strip():
            return {}, "runtime_state_empty"
        raw = json.loads(text)
    except Exception as exc:  # noqa: BLE001 - watchdog must report state I/O failures without crashing.
        return {}, f"{type(exc).__name__}: {exc}"
    if (
        not isinstance(raw, dict)
        or raw.get("version") != 2
        or not isinstance(raw.get("workers"), dict)
        or not isinstance(raw.get("queue"), dict)
        or not isinstance((raw.get("queue") or {}).get("events"), dict)
    ):
        return {}, "runtime_state_schema_invalid"
    return raw, None


def resource_snapshot(
    config: dict[str, Any],
    runtime_state: dict[str, Any],
    settings: dict[str, Any],
    *,
    skip_proc_scan: bool = False,
) -> dict[str, Any]:
    state_path = config_path(config, "state_file")
    usage = os.statvfs(str(ROOT))
    disk_free_gb = (usage.f_bavail * usage.f_frsize) / (1024 ** 3)
    disk_total_gb = (usage.f_blocks * usage.f_frsize) / (1024 ** 3)
    disk_used_percent = 0.0 if disk_total_gb <= 0 else 100.0 * (1.0 - disk_free_gb / disk_total_gb)
    memory_available_mb = None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                memory_available_mb = int(line.split()[1]) / 1024
                break
    except OSError:
        memory_available_mb = None
    try:
        load_1m = float(os.getloadavg()[0])
    except OSError:
        load_1m = 0.0

    if skip_proc_scan:
        live_worker_identities = set()
        worker_scan_error = "skipped_during_lock_contention"
    else:
        live_worker_identities, worker_scan_error = scan_live_worker_runner_identities()

    recorded_worker_count = active_worker_count(runtime_state)
    if worker_scan_error:
        # The explicit scan error below suppresses restart.  Keep the larger
        # count as a second fail-safe so consumers that only understand the
        # legacy numeric field cannot accidentally understate pressure.
        effective_worker_count = max(len(live_worker_identities), recorded_worker_count)
        worker_count_source = "fail_safe_max_live_and_runtime_state"
    else:
        effective_worker_count = len(live_worker_identities)
        worker_count_source = "live_worker_runner_pid_identity"
    return {
        "disk_free_gb": round(disk_free_gb, 3),
        "disk_used_percent": round(disk_used_percent, 2),
        "memory_available_mb": round(memory_available_mb, 1) if memory_available_mb is not None else None,
        "load_1m": round(load_1m, 2),
        "active_worker_count": effective_worker_count,
        "active_worker_count_source": worker_count_source,
        "active_worker_live_count": len(live_worker_identities),
        "active_worker_runtime_state_count": recorded_worker_count,
        "active_worker_scan_error": worker_scan_error,
        "state_parent_writable": os.access(state_path.parent, os.W_OK),
    }


def resource_pressure_reasons(snapshot: dict[str, Any], settings: dict[str, Any], state_error: str | None = None) -> list[str]:
    reasons: list[str] = []
    # A brand-new split-root dev VM has no runtime state until the supervisor's
    # guarded bootstrap writes it. Treat only that exact absence as a valid
    # first-start condition; empty, corrupt, unreadable, or schema-invalid
    # state remains fail-closed. enter_watchdog_safe_mode writes a minimal
    # durable envelope before start_supervisor launches the canonical bootstrap.
    if state_error and state_error != "runtime_state_missing":
        reasons.append("state_read_failed")
    if snapshot.get("active_worker_scan_error"):
        reasons.append("active_worker_scan_failed")
    if not snapshot.get("state_parent_writable"):
        reasons.append("state_parent_not_writable")
    if float(snapshot.get("disk_free_gb") or 0) < float(settings.get("min_disk_free_gb")):
        reasons.append("disk_free_below_threshold")
    if float(snapshot.get("disk_used_percent") or 0) > float(settings.get("max_disk_used_percent")):
        reasons.append("disk_used_above_threshold")
    memory_available = snapshot.get("memory_available_mb")
    if memory_available is not None and float(memory_available) < float(settings.get("min_memory_available_mb")):
        reasons.append("memory_available_below_threshold")
    if float(snapshot.get("load_1m") or 0) > float(settings.get("max_load_1m")):
        reasons.append("load_above_threshold")
    if int(snapshot.get("active_worker_count") or 0) > int(settings.get("max_active_workers")):
        reasons.append("active_worker_count_above_threshold")
    return reasons


def trim_restart_attempts(attempts: list[dict[str, Any]], now: datetime, max_age_seconds: int = 86400) -> list[dict[str, Any]]:
    cutoff = now - timedelta(seconds=max_age_seconds)
    kept: list[dict[str, Any]] = []
    for attempt in attempts:
        at = parse_utc_timestamp(str(attempt.get("at") or ""))
        if at is not None and at >= cutoff:
            kept.append(attempt)
    return kept


def restart_attempt_counts(attempts: list[dict[str, Any]], now: datetime, settings: dict[str, Any]) -> dict[str, int]:
    window_seconds = int(settings.get("restart_budget_window_seconds"))
    in_window = 0
    in_hour = 0
    for attempt in attempts:
        # Planned deploy restarts are operational handoffs, not crash-loop
        # evidence. Keep the historical row for audit while excluding it from
        # the protection budget.
        if attempt.get("classification") == "intentional_deploy":
            continue
        at = parse_utc_timestamp(str(attempt.get("at") or ""))
        if at is None:
            continue
        age = (now - at).total_seconds()
        if 0 <= age <= window_seconds:
            in_window += 1
        if 0 <= age <= 3600:
            in_hour += 1
    return {"window": in_window, "hour": in_hour}


def early_close_cleared_pressure_circuit(
    watchdog_state: dict[str, Any],
    now: datetime,
    pressure_reasons: list[str] | None = None,
) -> bool:
    """Close only when this tick explicitly proved prior resource pressure cleared."""
    circuit = watchdog_state.setdefault("circuit", {"open": False, "reason": None, "opened_at": None, "until": None})
    circuit_reason = str(circuit.get("reason") or "")
    if (
        not circuit.get("open")
        or not circuit_reason.startswith("resource_pressure:")
        or pressure_reasons != []
    ):
        return False
    circuit["open"] = False
    circuit["closed_at"] = isoformat_utc(now)
    return True


def budget_suppression_reason(watchdog_state: dict[str, Any], now: datetime, settings: dict[str, Any]) -> str | None:
    circuit = watchdog_state.setdefault("circuit", {"open": False, "reason": None, "opened_at": None, "until": None})
    until = parse_utc_timestamp(str(circuit.get("until") or ""))
    if circuit.get("open") and until is not None and now < until:
        return "watchdog_circuit_open"
    if circuit.get("open"):
        circuit["open"] = False
        circuit["closed_at"] = isoformat_utc(now)

    attempts = watchdog_state.setdefault("restart_attempts", [])
    budget_attempts = [
        attempt
        for attempt in attempts
        if attempt.get("classification") != "intentional_deploy"
    ]
    counts = restart_attempt_counts(budget_attempts, now, settings)
    if counts["window"] >= int(settings.get("max_restarts_per_window")):
        return "restart_budget_window_exhausted"
    if counts["hour"] >= int(settings.get("max_restarts_per_hour")):
        return "restart_budget_hour_exhausted"
    if budget_attempts:
        last_at = parse_utc_timestamp(str(budget_attempts[-1].get("at") or ""))
        if last_at is not None:
            schedule = [int(value) for value in settings.get("backoff_schedule_seconds", [])]
            if schedule:
                backoff = schedule[min(len(schedule) - 1, max(0, counts["window"] - 1))]
                if now < last_at + timedelta(seconds=backoff):
                    return "restart_backoff_active"
    return None


def open_circuit(watchdog_state: dict[str, Any], now: datetime, reason: str, settings: dict[str, Any]) -> None:
    cooldown = int(settings.get("circuit_cooldown_seconds"))
    watchdog_state["circuit"] = {
        "open": True,
        "reason": reason,
        "opened_at": isoformat_utc(now),
        "until": isoformat_utc(now + timedelta(seconds=max(60, cooldown))),
    }


def heartbeat_age_seconds(runtime_state: dict[str, Any], now: datetime) -> float | None:
    supervisor_state = runtime_state.get("supervisor", {}) if isinstance(runtime_state.get("supervisor"), dict) else {}
    heartbeat = parse_utc_timestamp(str(supervisor_state.get("last_heartbeat_at") or ""))
    if heartbeat is None:
        return None
    return max(0.0, (now - heartbeat).total_seconds())


def evaluate_supervisor_health(runtime_state: dict[str, Any], pid: int | None, alive: bool, now: datetime, settings: dict[str, Any]) -> dict[str, Any]:
    supervisor_state = runtime_state.get("supervisor", {}) if isinstance(runtime_state.get("supervisor"), dict) else {}
    heartbeat_age = heartbeat_age_seconds(runtime_state, now)
    stale_after = float(settings.get("heartbeat_stale_seconds"))
    # Liveness is authoritative via the singleton flock (folded into `alive` by the
    # caller as lock_held or pid_is_alive). The pid file is only a hint: it is
    # legitimately gone during clean-restart seams, so a missing pid alone must NOT
    # trigger a restart while the lock is still held. Only treat the supervisor as
    # dead when nothing is alive; then label by whether a pid file remained.
    if not alive:
        return {
            "healthy": False,
            "reason": "missing_pid" if pid is None else "pid_not_alive",
            "heartbeat_age_seconds": heartbeat_age,
        }
    if heartbeat_age is None:
        return {"healthy": False, "reason": "missing_heartbeat", "heartbeat_age_seconds": None}
    if heartbeat_age > stale_after:
        return {"healthy": False, "reason": "stale_heartbeat", "heartbeat_age_seconds": heartbeat_age}
    lifecycle = str(supervisor_state.get("lifecycle") or "")
    if lifecycle == "degraded" and supervisor_state.get("last_loop_error") and heartbeat_age > stale_after / 2:
        return {"healthy": False, "reason": "degraded_loop_error", "heartbeat_age_seconds": heartbeat_age}
    return {"healthy": True, "reason": "healthy", "heartbeat_age_seconds": heartbeat_age}


def enter_watchdog_safe_mode(config: dict[str, Any], runtime_state: dict[str, Any], now: datetime, settings: dict[str, Any], reason: str) -> None:
    safe_for = max(30, int(settings.get("safe_mode_seconds")))
    watchdog = runtime_state.setdefault("watchdog", {})
    watchdog["safe_mode_until"] = isoformat_utc(now + timedelta(seconds=safe_for))
    watchdog["safe_mode_reason"] = reason
    watchdog["safe_mode_started_at"] = isoformat_utc(now)
    watchdog["last_decision"] = "restart_supervisor"
    save_runtime_state(config, runtime_state)


def start_supervisor(config: dict[str, Any], settings: dict[str, Any], now: datetime) -> tuple[int, Path]:
    log_dir = config_path(config, "state_file").parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    log_path = log_dir / f"supervisor-watchdog-restart-{stamp}.log"
    command = [str(value) for value in settings.get("supervisor_command") or ["python3", "-u", ".orchestrator/supervisor.py", "--verbose"]]
    # Pin the supervisor's archive/status root to the configured status file's
    # repo, regardless of where the supervisor module lives on disk. The
    # supervisor runs from the dev-root checkout but operates on the canonical
    # worktree named in config.paths; without this, task_archive falls back to
    # its own module location and resolves freshly-archived task dependencies as
    # "missing", stalling ready-dispatch down to a single self-claiming worker.
    # See docs/decisions/supervisor-status-root-split-brain-2026-06-09.md.
    env = supervisor_restart_environment(dict(os.environ))
    # Persist the immutable-runtime no-bytecode contract across watchdog
    # restarts.  The supervisor's ``-B`` argv protects its own interpreter;
    # this inherited setting protects every Python child it later launches.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        env["PANTHEON_STATUS_ROOT"] = str(repo_root_for_config(config))
    except KeyError:
        pass
    with log_path.open("ab") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
    return process.pid, log_path


def summarize_decision(
    *,
    decision: str,
    reason: str,
    pid: int | None,
    health: dict[str, Any],
    resource: dict[str, Any],
    restart_counts: dict[str, int],
    new_pid: int | None = None,
    log_path: Path | None = None,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "reason": reason,
        "pid": pid,
        "new_pid": new_pid,
        "heartbeat_age_seconds": health.get("heartbeat_age_seconds"),
        "resource": resource,
        "restart_count_window": restart_counts.get("window", 0),
        "restart_count_hour": restart_counts.get("hour", 0),
        "log_path": str(log_path) if log_path else None,
    }


def run_watchdog(config: dict[str, Any], *, restart: bool = False, dry_run: bool = False) -> dict[str, Any]:
    lock_manager = runtime_state_lock(
        config,
        shared=False,
        nonblocking=True,
    )
    acquired = False
    try:
        lock_manager.__enter__()
        acquired = True
    except LockContentionError:
        # We hit lock contention.
        # Construct a skip/contention result without writing to the locked state files.
        import threading

        settings = watchdog_settings(config)
        deadline = float(settings.get("contention_deadline_seconds", 2.0))

        thread_result = {}

        def contention_worker():
            try:
                now = datetime.now(timezone.utc).replace(microsecond=0)
                pid = read_pid_file(supervisor_pid_path(config))
                runtime_state, state_error = load_runtime_state_file(config)
                heartbeat_age = heartbeat_age_seconds(runtime_state, now)
                resource = resource_snapshot(config, runtime_state, settings, skip_proc_scan=True)
                lock_held = supervisor_lock_held(config)

                result = {
                    "decision": "skip",
                    "reason": "lock_contention",
                    "pid": pid,
                    "new_pid": None,
                    "heartbeat_age_seconds": heartbeat_age,
                    "resource": resource,
                    "restart_count_window": None,
                    "restart_count_hour": None,
                    "log_path": None,
                    "lock_held": lock_held,
                }

                try:
                    append_watchdog_contention_metric(config, result, settings)
                except Exception as metric_exc:
                    sys.stderr.write(f"watchdog contention metric write failed: {metric_exc}\n")
                    sys.stderr.flush()

                thread_result["res"] = result
            except Exception as e:
                thread_result["error"] = e

        t = threading.Thread(target=contention_worker)
        t.daemon = True
        t.start()
        t.join(timeout=deadline)

        if t.is_alive():
            sys.stderr.write("watchdog contention path execution timed out (decoupled)\n")
            sys.stderr.flush()
            return {
                "decision": "skip",
                "reason": "lock_contention_timeout",
                "pid": None,
                "new_pid": None,
                "heartbeat_age_seconds": None,
                "resource": {
                    "disk_free_gb": 0.0,
                    "disk_used_percent": 0.0,
                    "memory_available_mb": None,
                    "load_1m": 0.0,
                    "active_worker_count": 0,
                    "active_worker_count_source": "skipped_due_to_timeout",
                    "active_worker_live_count": 0,
                    "active_worker_runtime_state_count": 0,
                    "active_worker_scan_error": "timeout",
                    "state_parent_writable": False,
                },
                "restart_count_window": None,
                "restart_count_hour": None,
                "log_path": None,
                "lock_held": None,
            }

        if "res" in thread_result:
            return thread_result["res"]
        else:
            err = thread_result.get("error", "unknown thread failure")
            sys.stderr.write(f"watchdog contention path failed: {err}\n")
            sys.stderr.flush()
            return {
                "decision": "skip",
                "reason": "lock_contention_error",
                "pid": None,
                "new_pid": None,
                "heartbeat_age_seconds": None,
                "resource": {
                    "disk_free_gb": 0.0,
                    "disk_used_percent": 0.0,
                    "memory_available_mb": None,
                    "load_1m": 0.0,
                    "active_worker_count": 0,
                    "active_worker_count_source": "skipped_due_to_error",
                    "active_worker_live_count": 0,
                    "active_worker_runtime_state_count": 0,
                    "active_worker_scan_error": str(err),
                    "state_parent_writable": False,
                },
                "restart_count_window": None,
                "restart_count_hour": None,
                "log_path": None,
                "lock_held": None,
            }

    try:
        result = _run_watchdog_locked(config, restart=restart, dry_run=dry_run)
    except BaseException:
        lock_manager.__exit__(*sys.exc_info())
        raise
    else:
        lock_manager.__exit__(None, None, None)
    return result


def _run_watchdog_locked(config: dict[str, Any], *, restart: bool = False, dry_run: bool = False) -> dict[str, Any]:
    settings = watchdog_settings(config)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    runtime_state, state_error = load_runtime_state_file(config)
    watchdog_state = load_watchdog_state(config, settings)
    attempts = trim_restart_attempts(watchdog_state.setdefault("restart_attempts", []), now)
    watchdog_state["restart_attempts"] = attempts
    pid = read_pid_file(supervisor_pid_path(config))
    candidate_pids = {pid} if pid is not None and pid > 0 else set()
    runtime_supervisor = runtime_state.get("supervisor", {}) if isinstance(runtime_state.get("supervisor"), dict) else {}
    try:
        runtime_pid = int(runtime_supervisor.get("pid"))
    except (TypeError, ValueError):
        runtime_pid = 0
    if runtime_pid > 0:
        candidate_pids.add(runtime_pid)
    intentional_restart = load_valid_intentional_restart(
        config,
        now=now,
        candidate_pids=candidate_pids,
        settings=settings,
    )
    lock_held = supervisor_lock_held(config)
    promotion_in_progress = exclusive_lock_held(
        supervisor_promotion_lock_path(config)
    )
    # The flock is the authoritative liveness signal; the pid file is a best-effort
    # hint that is absent during clean-restart seams. Folding lock_held into `alive`
    # stops the watchdog from restarting a live supervisor just because its pid file
    # was momentarily unlinked.
    alive = lock_held or pid_is_alive(pid)
    health = evaluate_supervisor_health(runtime_state, pid, alive, now, settings)
    resource = resource_snapshot(config, runtime_state, settings)
    pressure_reasons = resource_pressure_reasons(resource, settings, state_error)
    if settings.get("enabled", True):
        early_close_cleared_pressure_circuit(watchdog_state, now, pressure_reasons)
    restart_counts = restart_attempt_counts(attempts, now, settings)
    decision = "observe_only"
    reason = str(health.get("reason") or "healthy")
    new_pid: int | None = None
    log_path: Path | None = None

    if not settings.get("enabled", True):
        decision = "observe_only"
        reason = "watchdog_disabled"
    elif promotion_in_progress:
        decision = "suppress_restart"
        reason = "supervisor_runtime_promotion_in_progress"
    elif pressure_reasons:
        decision = "suppress_restart"
        reason = "resource_pressure:" + ",".join(pressure_reasons)
        if not health.get("healthy"):
            circuit = watchdog_state.get("circuit", {})
            circuit_open = bool(circuit.get("open")) if isinstance(circuit, dict) else False
            circuit_reason = str(circuit.get("reason") or "") if circuit_open else ""
            # Transient pressure must not relabel a genuine crash-loop circuit;
            # otherwise the next clean scan could early-close its cooldown.
            if not circuit_open or circuit_reason.startswith("resource_pressure:"):
                open_circuit(watchdog_state, now, reason, settings)
    elif health.get("healthy"):
        decision = "observe_only"
        reason = "supervisor_healthy"
    else:
        # A short-lived deploy intent is bound to the exact process the sync
        # script stopped. Resource pressure still wins, but crash budgets and
        # an already-open crash circuit must not block this planned handoff.
        budget_reason = None if intentional_restart is not None else budget_suppression_reason(watchdog_state, now, settings)
        if budget_reason:
            decision = "suppress_restart"
            reason = budget_reason
            if budget_reason.endswith("exhausted"):
                open_circuit(watchdog_state, now, budget_reason, settings)
        elif not restart:
            decision = "observe_only"
            reason = f"unhealthy:{health.get('reason')}"
        elif dry_run:
            decision = "restart_supervisor"
            dry_run_reason = "intentional_deploy_restart" if intentional_restart is not None else health.get("reason")
            reason = f"dry_run:{dry_run_reason}"
        else:
            decision = "restart_supervisor"
            intentional = intentional_restart is not None
            reason = "intentional_deploy_restart" if intentional else str(health.get("reason") or "unhealthy")
            enter_watchdog_safe_mode(config, runtime_state, now, settings, reason)
            new_pid, log_path = start_supervisor(config, settings, now)
            if intentional:
                circuit = watchdog_state.setdefault("circuit", {})
                previous_circuit_reason = circuit.get("reason") if circuit.get("open") else None
                watchdog_state["circuit"] = {
                    "open": False,
                    "reason": None,
                    "opened_at": circuit.get("opened_at"),
                    "until": None,
                    "closed_at": isoformat_utc(now),
                    "closed_reason": "intentional_deploy_restart",
                    "previous_reason": previous_circuit_reason,
                }
                intentional_attempts = watchdog_state.setdefault("intentional_restart_attempts", [])
                intentional_attempts.append(
                    {
                        "at": isoformat_utc(now),
                        "reason": reason,
                        "old_pid": intentional_restart["old_pid"],
                        "new_pid": new_pid,
                        "target_sha": intentional_restart["target_sha"],
                        "log_path": str(log_path),
                    }
                )
                consume_intentional_restart(config, settings)
            else:
                attempts.append(
                    {
                        "at": isoformat_utc(now),
                        "reason": reason,
                        "old_pid": pid,
                        "new_pid": new_pid,
                        "log_path": str(log_path),
                    }
                )
                watchdog_state["restart_attempts"] = attempts

    result = summarize_decision(
        decision=decision,
        reason=reason,
        pid=pid,
        health=health,
        resource=resource,
        restart_counts=restart_attempt_counts(attempts, now, settings),
        new_pid=new_pid,
        log_path=log_path,
    )
    result["lock_held"] = lock_held
    result["intentional_restart"] = bool(intentional_restart is not None and new_pid is not None)
    # Report the checkout that is actually live, not the default dev-root path.
    result["supervisor_root"] = supervisor_root_report(
        config,
        new_pid or pid,
        settings=settings,
    )
    watchdog_state["last_decision"] = result
    save_watchdog_state(config, watchdog_state, settings)
    append_watchdog_metric(
        config,
        {
            "event_type": "watchdog_probe",
            **result,
        },
        settings,
    )
    activity_type = {
        "restart_supervisor": "supervisor_restart_attempted",
        "suppress_restart": "supervisor_restart_suppressed",
        "observe_only": "watchdog_probe",
    }.get(decision, "watchdog_probe")
    write_activity_log(
        config,
        {
            "type": activity_type,
            "message": f"Watchdog decision {decision}: {reason}",
            **result,
        },
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe and optionally restart the Pantheon supervisor safely.")
    parser.add_argument("--config", default=".orchestrator/config.json")
    parser.add_argument("--restart", action="store_true", help="Restart unhealthy supervisor when resource and budget gates allow it.")
    parser.add_argument("--dry-run", action="store_true", help="Report the restart decision without launching a process.")
    parser.add_argument("--record-intent-pid", type=int, help="Record a planned deploy restart for this live supervisor PID.")
    parser.add_argument("--record-intent-target", help="Full target git SHA for a planned deploy restart.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable result.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    if (args.record_intent_pid is None) != (args.record_intent_target is None):
        raise SystemExit("--record-intent-pid and --record-intent-target must be provided together")
    if args.record_intent_pid is not None:
        result = record_intentional_restart(
            config,
            old_pid=args.record_intent_pid,
            target_sha=args.record_intent_target,
        )
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(
                "recorded intentional deploy restart "
                f"pid={result['old_pid']} target={result['target_sha']} expires_at={result['expires_at']}"
            )
        return 0
    result = run_watchdog(config, restart=args.restart, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"watchdog decision={result['decision']} reason={result['reason']} pid={result.get('pid')} new_pid={result.get('new_pid')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
