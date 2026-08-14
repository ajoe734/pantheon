#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from provision_live_supervisor_config import validate_approval_queue_marker


HEX_40_PATTERN = re.compile(r"^[0-9a-f]{40}$")
PROCESS_ENVIRONMENT_ALLOWLIST = (
    "PANTHEON_COMMAND_ROOT",
    "PANTHEON_COMMAND_RUNTIME_SHA",
    "PANTHEON_STATUS_ROOT",
    "PYTHONDONTWRITEBYTECODE",
)
def parse_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return default


def _load_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if path.is_symlink():
        return None, "symlink"
    try:
        payload = path.read_bytes()
    except OSError as exc:
        return None, f"{type(exc).__name__}:{exc}"
    try:
        decoded = json.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}:{exc}"
    if not isinstance(decoded, dict):
        return None, "not_object"
    return decoded, None


def load_latest_contention_probe(path: Path, *, max_bytes: int = 1024 * 1024) -> dict[str, Any] | None:
    """Return the newest valid lock-contention probe from a bounded JSONL tail."""
    if path.is_symlink():
        return None
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - max_bytes)
            handle.seek(start)
            payload = handle.read(max_bytes)
    except OSError:
        return None

    if start:
        _, separator, payload = payload.partition(b"\n")
        if not separator:
            return None

    for raw_line in reversed(payload.splitlines()):
        try:
            event = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        if (
            event.get("version") == 1
            and event.get("decision") == "skip"
            and event.get("reason") == "lock_contention"
            and event.get("lock_held") is True
            and parse_utc_timestamp(event.get("at")) is not None
        ):
            return event
    return None


def resolve_repo_path(repo_root: Path, value: str | None, default: str) -> Path:
    raw = Path(value or default).expanduser()
    if not raw.is_absolute():
        raw = repo_root / raw
    return raw.absolute()


def config_path(repo_root: Path, config: dict[str, Any], key: str, default: str) -> Path:
    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    return resolve_repo_path(repo_root, str(paths.get(key) or default), default)


def read_pid(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 and raw == str(value) else None


def pid_is_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_process_generation(pid: int) -> tuple[int, str]:
    try:
        text = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
    except FileNotFoundError as exc:
        raise ProcessLookupError(f"process {pid} vanished") from exc
    close_paren = text.rfind(")")
    open_paren = text.find("(")
    if open_paren <= 0 or close_paren <= open_paren:
        raise ValueError("invalid process stat")
    fields = text[close_paren + 1 :].strip().split()
    try:
        recorded_pid = int(text[:open_paren].strip())
        state = fields[0]
        starttime_ticks = int(fields[19])
    except (IndexError, ValueError) as exc:
        raise ValueError("process stat is missing generation fields") from exc
    if recorded_pid != pid or len(state) != 1 or starttime_ticks <= 0:
        raise ValueError("invalid process generation")
    return starttime_ticks, state


def _read_process_environment(pid: int) -> dict[str, str]:
    raw = (Path("/proc") / str(pid) / "environ").read_bytes()
    allowlisted = {name.encode("ascii"): name for name in PROCESS_ENVIRONMENT_ALLOWLIST}
    result: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        raw_name, separator, raw_value = entry.partition(b"=")
        name = allowlisted.get(raw_name)
        if name is None:
            continue
        if not separator or name in result:
            raise ValueError(f"duplicate or malformed environment {name}")
        result[name] = raw_value.decode("utf-8", errors="strict")
    return result


def inspect_supervisor_process(pid: int) -> dict[str, Any]:
    """Capture the process generation and the exact governed identity surface."""
    starttime_ticks, state = _read_process_generation(pid)
    proc_dir = Path("/proc") / str(pid)
    raw_cmdline = proc_dir.joinpath("cmdline").read_bytes()
    if not raw_cmdline or not raw_cmdline.endswith(b"\0"):
        raise ValueError("supervisor argv is empty or malformed")
    argv = tuple(part.decode("utf-8", errors="strict") for part in raw_cmdline[:-1].split(b"\0"))
    cwd_raw = os.readlink(proc_dir / "cwd")
    if cwd_raw.endswith(" (deleted)"):
        raise ValueError("supervisor cwd was deleted")
    cwd = Path(cwd_raw).resolve(strict=True)
    return {
        "pid": pid,
        "starttime_ticks": starttime_ticks,
        "state": state,
        "argv": argv,
        "cwd": str(cwd),
        "environment": _read_process_environment(pid),
    }


def lock_held(lock_path: Path) -> bool:
    if not lock_path.exists():
        return False
    try:
        handle = lock_path.open("a+", encoding="utf-8")
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


def inspect_singleton_owner(lock_path: Path) -> dict[str, Any]:
    """Bind one FLOCK row and its lock-file PID to an exact process generation."""
    if lock_path.is_symlink():
        raise ValueError("singleton lock is a symlink")
    descriptor = os.open(lock_path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        lock_stat = os.fstat(descriptor)
        owner_raw = os.read(descriptor, 64).decode("ascii", errors="strict").strip()
    finally:
        os.close(descriptor)
    owner_pid = int(owner_raw)
    if owner_pid <= 0 or owner_raw != str(owner_pid):
        raise ValueError("singleton lock PID is invalid")
    starttime_ticks, state = _read_process_generation(owner_pid)
    expected_device = (os.major(lock_stat.st_dev), os.minor(lock_stat.st_dev))
    matches: list[tuple[str, ...]] = []
    for row in Path("/proc/locks").read_text(encoding="ascii", errors="strict").splitlines():
        fields = tuple(row.split())
        if len(fields) < 8 or fields[1] == "->":
            continue
        try:
            major_hex, minor_hex, inode_text = fields[5].split(":", 2)
            row_device = (int(major_hex, 16), int(minor_hex, 16))
            row_inode = int(inode_text)
            row_pid = int(fields[4])
        except (IndexError, ValueError):
            continue
        if row_device == expected_device and row_inode == lock_stat.st_ino:
            matches.append(fields)
            if row_pid != owner_pid:
                raise ValueError("singleton lock file and kernel owner differ")
    if len(matches) != 1:
        raise ValueError("singleton lock must have exactly one kernel owner")
    fields = matches[0]
    if fields[1:4] != ("FLOCK", "ADVISORY", "WRITE") or fields[6:8] != ("0", "EOF"):
        raise ValueError("singleton lock has the wrong kernel mode")
    return {"pid": owner_pid, "starttime_ticks": starttime_ticks, "state": state}


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if HEX_40_PATTERN.fullmatch(value) else None


def _validated_jsonl_access(path: Path, *, max_bytes: int = 1024 * 1024) -> tuple[bool, dict[str, Any]]:
    if path.is_symlink():
        return False, {"path": str(path), "error": "symlink"}
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - max_bytes)
            handle.seek(start)
            payload = handle.read(max_bytes)
    except OSError as exc:
        return False, {"path": str(path), "error": f"{type(exc).__name__}:{exc}"}
    if start:
        _, separator, payload = payload.partition(b"\n")
        if not separator:
            return False, {"path": str(path), "error": "record_exceeds_bounded_probe"}
    try:
        records = [json.loads(line.decode("utf-8", errors="strict")) for line in payload.splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return False, {"path": str(path), "error": f"{type(exc).__name__}:{exc}"}
    return all(isinstance(item, dict) for item in records), {"path": str(path), "byte_size": size, "records_probed": len(records)}


def check(name: str, ok: bool, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), **(detail or {})}


def resolved_coordinator_status_root(repo_root: Path, config: dict[str, Any]) -> Path:
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    status_value = paths.get("status_file")
    if status_value:
        return resolve_repo_path(repo_root, str(status_value), "ai-status.json").parent.resolve()
    state_value = paths.get("state_file")
    if state_value:
        state_path = resolve_repo_path(repo_root, str(state_value), ".orchestrator/state.json").resolve()
        return state_path.parent.parent if state_path.parent.name == ".orchestrator" else state_path.parent
    env_val = os.environ.get("PANTHEON_STATUS_ROOT")
    if env_val and env_val.strip():
        return Path(os.path.expanduser(env_val.strip())).resolve()
    return repo_root.resolve()


def _expected_runtime_command(repo_root: Path, config: Mapping[str, Any]) -> tuple[tuple[str, ...], Path | None]:
    watchdog = config.get("watchdog") if isinstance(config.get("watchdog"), dict) else {}
    raw = watchdog.get("supervisor_command")
    if not isinstance(raw, list) or not raw or any(not isinstance(item, str) or not item for item in raw):
        return (), None
    argv = tuple(raw)
    entrypoints = tuple(Path(item) for item in argv[1:] if PurePosixPath(item).name == "supervisor.py")
    if len(entrypoints) != 1:
        return argv, None
    entrypoint = entrypoints[0]
    if not entrypoint.is_absolute():
        entrypoint = repo_root / entrypoint
    return argv, entrypoint.absolute().parent.parent


def _task_head_path(repo_root: Path, config: Mapping[str, Any]) -> Path | None:
    store = config.get("task_state_store") if isinstance(config.get("task_state_store"), dict) else {}
    event_log = str(store.get("event_log") or "").strip()
    if not event_log:
        return None
    event_path = resolve_repo_path(repo_root, event_log, event_log)
    return event_path.with_name(f"{event_path.name}.head.json")


def evaluate_runtime_health(
    repo_root: Path,
    *,
    config_path_arg: Path | None = None,
    now: datetime | None = None,
    max_heartbeat_age: float | None = None,
    max_cycle_elapsed: float | None = None,
    max_dispatch_latency: float | None = None,
    require_watchdog: bool = False,
    max_watchdog_age: float = 180.0,
    expected_command_root: Path | None = None,
    expected_source_commit: str | None = None,
    expected_config_sha256: str | None = None,
    expected_process_generation: tuple[int, int] | None = None,
    verified_runtime_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate identity, liveness, readiness, and scheduling progress independently."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    repo_root = repo_root.expanduser().absolute()
    config_path_resolved = (config_path_arg or (repo_root / ".orchestrator" / "config.json")).absolute()
    config, config_error = _load_json_object(config_path_resolved)
    config = config or {}
    try:
        config_bytes = config_path_resolved.read_bytes()
        config_sha256 = hashlib.sha256(config_bytes).hexdigest()
    except OSError:
        config_sha256 = None

    state_path = config_path(repo_root, config, "state_file", ".orchestrator/state.json")
    state, state_error = _load_json_object(state_path)
    state = state or {}
    supervisor = state.get("supervisor") if isinstance(state.get("supervisor"), dict) else {}
    coord_root = resolved_coordinator_status_root(repo_root, config)
    pid_path = state_path.parent / "supervisor.pid"
    lock_path = coord_root / ".orchestrator" / "supervisor.lock"
    pid = read_pid(pid_path)
    state_pid = supervisor.get("pid") if isinstance(supervisor.get("pid"), int) else None

    process_error: str | None = None
    if verified_runtime_identity is not None:
        process = dict(verified_runtime_identity)
    elif pid is not None:
        try:
            process = inspect_supervisor_process(pid)
        except (OSError, ProcessLookupError, UnicodeDecodeError, ValueError) as exc:
            process = {}
            process_error = f"{type(exc).__name__}:{exc}"
    else:
        process = {}
        process_error = "pid_missing"

    expected_argv, configured_command_root = _expected_runtime_command(repo_root, config)
    expected_root = (expected_command_root or configured_command_root)
    expected_root = expected_root.expanduser().absolute() if expected_root is not None else None
    process_argv = tuple(process.get("argv") or ())
    environment = process.get("environment") if isinstance(process.get("environment"), Mapping) else {}
    process_pid = process.get("pid") if isinstance(process.get("pid"), int) else None
    process_start = process.get("starttime_ticks") if isinstance(process.get("starttime_ticks"), int) else None
    actual_generation = (process_pid, process_start) if process_pid and process_start else None
    expected_generation = expected_process_generation or ((pid, process_start) if pid and process_start else None)
    actual_root_raw = str(environment.get("PANTHEON_COMMAND_ROOT") or process.get("cwd") or "").strip()
    actual_root = Path(actual_root_raw).expanduser().absolute() if actual_root_raw else None
    actual_source_commit = str(environment.get("PANTHEON_COMMAND_RUNTIME_SHA") or "").strip() or None
    root_head = _git_head(expected_root) if expected_root is not None else None
    expected_commit = expected_source_commit or root_head

    identity_checks = [
        check("identity_config_readable", config_error is None, {"config_path": str(config_path_resolved), "error": config_error}),
        check(
            "identity_config_sha256_exact",
            config_sha256 is not None and (expected_config_sha256 is None or config_sha256 == expected_config_sha256),
            {"config_sha256": config_sha256, "expected_config_sha256": expected_config_sha256},
        ),
        check(
            "identity_command_root_exact",
            expected_root is not None and actual_root == expected_root,
            {"command_root": str(actual_root) if actual_root else None, "expected_command_root": str(expected_root) if expected_root else None},
        ),
        check(
            "identity_source_commit_exact",
            expected_commit is not None
            and actual_source_commit == expected_commit
            and (root_head is None or root_head == expected_commit)
            and (not HEX_40_PATTERN.fullmatch(expected_root.name) or expected_root.name == expected_commit),
            {"source_commit": actual_source_commit, "expected_source_commit": expected_commit, "root_head": root_head},
        ),
        check(
            "identity_pid_generation_exact",
            expected_generation is not None and actual_generation == expected_generation and pid == state_pid == process_pid,
            {"pid_file": pid, "state_pid": state_pid, "process_generation": actual_generation, "expected_process_generation": expected_generation},
        ),
        check(
            "identity_supervisor_argv_exact",
            bool(expected_argv) and process_argv == expected_argv,
            {"argv": list(process_argv), "expected_argv": list(expected_argv)},
        ),
    ]

    singleton_error: str | None = None
    if verified_runtime_identity is not None:
        singleton = {
            "pid": process.get("singleton_owner_pid"),
            "starttime_ticks": process.get("singleton_owner_starttime_ticks"),
            "state": process.get("state"),
        }
    else:
        try:
            singleton = inspect_singleton_owner(lock_path)
        except (OSError, ProcessLookupError, UnicodeDecodeError, ValueError) as exc:
            singleton = {}
            singleton_error = f"{type(exc).__name__}:{exc}"
    singleton_generation = (
        (singleton.get("pid"), singleton.get("starttime_ticks"))
        if isinstance(singleton.get("pid"), int) and isinstance(singleton.get("starttime_ticks"), int)
        else None
    )
    process_alive = bool(
        process
        and process.get("state") != "Z"
        and actual_generation == expected_generation
        and process_error is None
    )
    liveness_checks = [
        check(
            "liveness_exact_process_generation_alive",
            process_alive,
            {"process_error": process_error, "generation": actual_generation, "state": process.get("state")},
        ),
        check(
            "liveness_singleton_owned_by_generation",
            singleton_generation is not None and singleton_generation == actual_generation and singleton.get("state") != "Z",
            {"lock_path": str(lock_path), "owner_generation": singleton_generation, "process_generation": actual_generation, "error": singleton_error},
        ),
    ]

    status_path = config_path(repo_root, config, "status_file", "ai-status.json")
    status, status_error = _load_json_object(status_path)
    status = status or {}
    approval_queue_path = config_path(
        repo_root, config, "approval_queue", ".orchestrator/approval-queue.json"
    )
    try:
        validate_approval_queue_marker(approval_queue_path)
        approval_queue_error = None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        approval_queue_error = f"{type(exc).__name__}:{exc}"
    queue_path = config_path(repo_root, config, "event_queue", ".orchestrator/event-queue.jsonl")
    queue_ok, queue_detail = _validated_jsonl_access(queue_path)
    task_head_path = _task_head_path(repo_root, config)
    task_head, task_head_error = _load_json_object(task_head_path) if task_head_path is not None else (None, "not_configured")
    projection = (
        supervisor.get("task_state_projection")
        if isinstance(supervisor.get("task_state_projection"), dict)
        else {}
    )
    projection_ok = bool(
        projection.get("mode") == "authoritative"
        and projection.get("ok") is True
        and projection.get("caught_up") is True
        and projection.get("last_error") is None
        and projection.get("projected_state_sha256")
        and projection.get("projected_state_sha256") == projection.get("expected_state_sha256")
    )
    readiness_checks = [
        check("readiness_runtime_state_accessible", state_error is None, {"state_file": str(state_path), "error": state_error}),
        check(
            "readiness_task_head_accessible",
            isinstance(task_head, dict) and isinstance(task_head.get("sequence"), int) and isinstance(task_head.get("state"), dict),
            {"task_head": str(task_head_path) if task_head_path else None, "error": task_head_error, "sequence": task_head.get("sequence") if task_head else None},
        ),
        check(
            "readiness_task_projection_accessible",
            status_error is None and isinstance(status.get("tasks"), list),
            {"status_file": str(status_path), "error": status_error},
        ),
        check(
            "readiness_task_projection_caught_up",
            projection_ok,
            {"task_state_projection": projection},
        ),
        check("readiness_queue_accessible", queue_ok, queue_detail),
        check(
            "readiness_approval_queue_marker_accessible",
            approval_queue_error is None,
            {"approval_queue": str(approval_queue_path), "error": approval_queue_error},
        ),
    ]

    heartbeat = parse_utc_timestamp(supervisor.get("last_heartbeat_at"))
    heartbeat_age = (now - heartbeat).total_seconds() if heartbeat is not None else None
    configured_watchdog = config.get("watchdog") if isinstance(config.get("watchdog"), dict) else {}
    configured_supervisor = config.get("supervisor") if isinstance(config.get("supervisor"), dict) else {}
    poll_interval = float(configured_supervisor.get("poll_interval_seconds", 300.0))
    stall_after = float(configured_supervisor.get("stall_after_seconds", max(900.0, poll_interval * 3.0)))
    if max_heartbeat_age is None:
        max_heartbeat_age = float(configured_watchdog.get("heartbeat_stale_seconds", max(900.0, poll_interval * 3.0)))
    if max_cycle_elapsed is None:
        max_cycle_elapsed = float(configured_supervisor.get("cycle_budget_seconds", stall_after))
    if max_dispatch_latency is None:
        max_dispatch_latency = float(configured_supervisor.get("dispatch_latency_budget_seconds", stall_after))
    successful_loop = parse_utc_timestamp(supervisor.get("last_successful_loop_at"))
    loop_started = parse_utc_timestamp(supervisor.get("last_loop_started_at"))
    loop_finished = parse_utc_timestamp(supervisor.get("last_loop_finished_at"))
    loop_age = (now - successful_loop).total_seconds() if successful_loop is not None else None
    loop_sequence_ok = bool(
        successful_loop is not None
        and loop_started is not None
        and loop_finished is not None
        and loop_started <= loop_finished == successful_loop
        and supervisor.get("last_loop_error") is None
        and loop_age is not None
        and 0 <= loop_age <= stall_after
    )
    last_cycle = supervisor.get("last_cycle_metrics") if isinstance(supervisor.get("last_cycle_metrics"), dict) else {}
    cycle_elapsed = last_cycle.get("cycle_elapsed_seconds", supervisor.get("cycle_elapsed_seconds"))
    cycle_elapsed_value = float(cycle_elapsed) if isinstance(cycle_elapsed, (int, float)) else None
    dispatch_latency = supervisor.get("queue_to_start_latency_seconds")
    queue_to_start = last_cycle.get("queue_to_start") if isinstance(last_cycle.get("queue_to_start"), dict) else {}
    if isinstance(queue_to_start.get("max_seconds"), (int, float)):
        dispatch_latency = queue_to_start["max_seconds"]
    dispatch_latency_value = float(dispatch_latency) if isinstance(dispatch_latency, (int, float)) else None
    progress_checks = [
        check("progress_heartbeat_present", heartbeat is not None, {"last_heartbeat_at": supervisor.get("last_heartbeat_at")}),
        check(
            "progress_heartbeat_fresh",
            heartbeat_age is not None and 0 <= heartbeat_age <= max_heartbeat_age,
            {"age_seconds": heartbeat_age, "max_age_seconds": max_heartbeat_age},
        ),
        check(
            "progress_fresh_successful_loop",
            loop_sequence_ok,
            {"last_successful_loop_at": supervisor.get("last_successful_loop_at"), "age_seconds": loop_age, "max_age_seconds": stall_after},
        ),
        check(
            "progress_cycle_within_budget",
            cycle_elapsed_value is not None and 0 <= cycle_elapsed_value <= max_cycle_elapsed,
            {"cycle_elapsed_seconds": cycle_elapsed_value, "max_cycle_elapsed_seconds": max_cycle_elapsed},
        ),
        check(
            "progress_dispatch_latency_within_budget",
            dispatch_latency_value is None or 0 <= dispatch_latency_value <= max_dispatch_latency,
            {"dispatch_latency_seconds": dispatch_latency_value, "max_dispatch_latency_seconds": max_dispatch_latency},
        ),
        check(
            "progress_supervisor_not_degraded",
            str(supervisor.get("lifecycle") or "") in {"active", "idle", "running"},
            {"lifecycle": supervisor.get("lifecycle"), "last_loop_error": supervisor.get("last_loop_error")},
        ),
    ]

    watchdog_report: dict[str, Any] | None = None
    if require_watchdog:
        watchdog_state_path = resolve_repo_path(repo_root, str(configured_watchdog.get("state_file") or ".orchestrator/watchdog-state.json"), ".orchestrator/watchdog-state.json")
        watchdog_state = load_json(watchdog_state_path, default={})
        watchdog_updated = parse_utc_timestamp(watchdog_state.get("updated_at") if isinstance(watchdog_state, dict) else None)
        contention_metrics_path = resolve_repo_path(repo_root, str(configured_watchdog.get("contention_metrics_file") or ".orchestrator/metrics/supervisor-watchdog-contention.jsonl"), ".orchestrator/metrics/supervisor-watchdog-contention.jsonl")
        contention_probe = load_latest_contention_probe(contention_metrics_path)
        contention_updated = parse_utc_timestamp(contention_probe.get("at") if contention_probe else None)
        probe_source, probe_updated = max(
            ((source, updated) for source, updated in (("watchdog_state", watchdog_updated), ("contention_metric", contention_updated)) if updated is not None),
            key=lambda item: item[1],
            default=(None, None),
        )
        watchdog_age = (now - probe_updated).total_seconds() if probe_updated is not None else None
        watchdog_report = {
            "state_file": str(watchdog_state_path),
            "updated_at": watchdog_updated.isoformat().replace("+00:00", "Z") if watchdog_updated else None,
            "contention_metrics_file": str(contention_metrics_path),
            "contention_updated_at": contention_updated.isoformat().replace("+00:00", "Z") if contention_updated else None,
            "probe_source": probe_source,
            "probe_updated_at": probe_updated.isoformat().replace("+00:00", "Z") if probe_updated else None,
            "age_seconds": watchdog_age,
            "max_age_seconds": max_watchdog_age,
        }
        readiness_checks.append(check("readiness_watchdog_probe_present", probe_updated is not None, watchdog_report))
        progress_checks.append(check("progress_watchdog_probe_fresh", watchdog_age is not None and 0 <= watchdog_age <= max_watchdog_age, watchdog_report))

    dimensions = {
        "identity": {"healthy": all(item["ok"] for item in identity_checks), "checks": identity_checks},
        "liveness": {"healthy": all(item["ok"] for item in liveness_checks), "checks": liveness_checks},
        "readiness": {"healthy": all(item["ok"] for item in readiness_checks), "checks": readiness_checks},
        "progress": {"healthy": all(item["ok"] for item in progress_checks), "checks": progress_checks},
    }
    checks = [item for dimension in dimensions.values() for item in dimension["checks"]]
    return {
        "healthy": all(dimension["healthy"] for dimension in dimensions.values()),
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "repo_root": str(repo_root),
        "state_file": str(state_path),
        "dimensions": dimensions,
        "identity": {
            "command_root": str(actual_root) if actual_root else None,
            "source_commit": actual_source_commit,
            "config_sha256": config_sha256,
            "pid_generation": list(actual_generation) if actual_generation else None,
        },
        "supervisor": {
            "pid": pid,
            "alive": process_alive and singleton_generation == actual_generation,
            "process_alive": process_alive,
            "lock_held": singleton_generation == actual_generation,
            "process_generation": list(actual_generation) if actual_generation else None,
            "last_heartbeat_at": supervisor.get("last_heartbeat_at"),
            "heartbeat_age_seconds": heartbeat_age,
            "max_heartbeat_age_seconds": max_heartbeat_age,
            "lifecycle": supervisor.get("lifecycle"),
            "last_loop_error": supervisor.get("last_loop_error"),
            "task_state_projection": supervisor.get("task_state_projection"),
        },
        "watchdog": watchdog_report,
        "checks": checks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Pantheon supervisor identity, liveness, readiness, and scheduling progress.")
    parser.add_argument("--repo", default=".", help="Pantheon command runtime root. Defaults to cwd.")
    parser.add_argument("--config-path", default=None, help="Path to the exact live supervisor config.")
    parser.add_argument("--max-heartbeat-age", type=float, default=None)
    parser.add_argument("--max-cycle-elapsed", type=float, default=None)
    parser.add_argument("--max-dispatch-latency", type=float, default=None)
    parser.add_argument("--require-watchdog", action="store_true")
    parser.add_argument("--max-watchdog-age", type=float, default=180.0)
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo).expanduser().absolute()
    report = evaluate_runtime_health(
        repo_root,
        config_path_arg=Path(args.config_path).expanduser().absolute() if args.config_path else None,
        max_heartbeat_age=args.max_heartbeat_age,
        max_cycle_elapsed=args.max_cycle_elapsed,
        max_dispatch_latency=args.max_dispatch_latency,
        require_watchdog=args.require_watchdog,
        max_watchdog_age=args.max_watchdog_age,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        status = "healthy" if report["healthy"] else "unhealthy"
        supervisor = report["supervisor"]
        dimensions = " ".join(
            f"{name}={'ok' if value['healthy'] else 'FAIL'}"
            for name, value in report["dimensions"].items()
        )
        print(
            "supervisor_runtime_health=%s pid=%s generation=%s %s"
            % (status, supervisor.get("pid"), supervisor.get("process_generation"), dimensions)
        )
        for item in report["checks"]:
            print(f"check {item['name']}: {'ok' if item['ok'] else 'FAIL'}")
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
