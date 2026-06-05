#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import os
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

from common import append_jsonl, config_path, load_config, load_json, utc_now, write_activity_log, write_json


ACTIVE_WORKER_STATUSES = {
    "running",
    "started",
    "waiting_approval",
    "suspended_approval",
    "manual_pending",
    "retry_backoff",
    "stalled",
    "fallback",
}


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
    return settings


def supervisor_pid_path(config: dict[str, Any]) -> Path:
    return config_path(config, "state_file").parent / "supervisor.pid"


def supervisor_lock_path(config: dict[str, Any]) -> Path:
    return config_path(config, "state_file").parent / "supervisor.lock"


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
    path = supervisor_lock_path(config)
    if not path.exists():
        return False
    try:
        handle = open(path, "a+", encoding="utf-8")
    except OSError:
        # Cannot open the lock file; fall back to "not held" so the pid-based
        # signal decides rather than masking a genuinely dead supervisor.
        return False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        # We acquired it -> nobody held it. Release so we never starve a relaunch.
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        handle.close()


def watchdog_state_path(config: dict[str, Any], settings: dict[str, Any] | None = None) -> Path:
    settings = settings or watchdog_settings(config)
    return resolve_repo_path(settings.get("state_file"), ".orchestrator/watchdog-state.json")


def watchdog_metrics_path(config: dict[str, Any], settings: dict[str, Any] | None = None) -> Path:
    settings = settings or watchdog_settings(config)
    return resolve_repo_path(settings.get("metrics_file"), ".orchestrator/metrics/supervisor-watchdog.jsonl")


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
    workers = runtime_state.get("workers", {}) if isinstance(runtime_state.get("workers"), dict) else {}
    return sum(1 for worker in workers.values() if str(worker.get("status") or "") in ACTIVE_WORKER_STATUSES)


def load_watchdog_state(config: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    path = watchdog_state_path(config, settings)
    raw = load_json(path, default={})
    state = raw if isinstance(raw, dict) else {}
    state.setdefault("version", 1)
    state.setdefault("updated_at", None)
    state.setdefault("restart_attempts", [])
    state.setdefault("circuit", {"open": False, "reason": None, "opened_at": None, "until": None})
    state.setdefault("last_decision", None)
    return state


def save_watchdog_state(config: dict[str, Any], state: dict[str, Any], settings: dict[str, Any] | None = None) -> None:
    state["version"] = 1
    state["updated_at"] = utc_now()
    write_json(watchdog_state_path(config, settings), state)


def append_watchdog_metric(config: dict[str, Any], payload: dict[str, Any], settings: dict[str, Any] | None = None) -> None:
    event = {
        "version": 1,
        "event_id": f"watchdog-{int(time.time() * 1000)}-{os.getpid()}",
        "at": utc_now(),
        **payload,
    }
    append_jsonl(watchdog_metrics_path(config, settings), event)


def load_runtime_state_file(config: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    path = config_path(config, "state_file")
    try:
        raw = load_json(path, default={})
    except Exception as exc:  # noqa: BLE001 - watchdog must report state I/O failures without crashing.
        return {}, f"{type(exc).__name__}: {exc}"
    return raw if isinstance(raw, dict) else {}, None


def resource_snapshot(config: dict[str, Any], runtime_state: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
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
    return {
        "disk_free_gb": round(disk_free_gb, 3),
        "disk_used_percent": round(disk_used_percent, 2),
        "memory_available_mb": round(memory_available_mb, 1) if memory_available_mb is not None else None,
        "load_1m": round(load_1m, 2),
        "active_worker_count": active_worker_count(runtime_state),
        "state_parent_writable": os.access(state_path.parent, os.W_OK),
    }


def resource_pressure_reasons(snapshot: dict[str, Any], settings: dict[str, Any], state_error: str | None = None) -> list[str]:
    reasons: list[str] = []
    if state_error:
        reasons.append("state_read_failed")
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
        at = parse_utc_timestamp(str(attempt.get("at") or ""))
        if at is None:
            continue
        age = (now - at).total_seconds()
        if 0 <= age <= window_seconds:
            in_window += 1
        if 0 <= age <= 3600:
            in_hour += 1
    return {"window": in_window, "hour": in_hour}


def budget_suppression_reason(watchdog_state: dict[str, Any], now: datetime, settings: dict[str, Any]) -> str | None:
    circuit = watchdog_state.setdefault("circuit", {"open": False, "reason": None, "opened_at": None, "until": None})
    until = parse_utc_timestamp(str(circuit.get("until") or ""))
    if circuit.get("open") and until is not None and now < until:
        return "watchdog_circuit_open"
    if circuit.get("open") and (until is None or now >= until):
        circuit["open"] = False
        circuit["closed_at"] = isoformat_utc(now)

    attempts = watchdog_state.setdefault("restart_attempts", [])
    counts = restart_attempt_counts(attempts, now, settings)
    if counts["window"] >= int(settings.get("max_restarts_per_window")):
        return "restart_budget_window_exhausted"
    if counts["hour"] >= int(settings.get("max_restarts_per_hour")):
        return "restart_budget_hour_exhausted"
    if attempts:
        last_at = parse_utc_timestamp(str(attempts[-1].get("at") or ""))
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
    write_json(config_path(config, "state_file"), runtime_state)


def start_supervisor(config: dict[str, Any], settings: dict[str, Any], now: datetime) -> tuple[int, Path]:
    log_dir = config_path(config, "state_file").parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    log_path = log_dir / f"supervisor-watchdog-restart-{stamp}.log"
    command = [str(value) for value in settings.get("supervisor_command") or ["python3", "-u", ".orchestrator/supervisor.py", "--verbose"]]
    with log_path.open("ab") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
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
    settings = watchdog_settings(config)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    runtime_state, state_error = load_runtime_state_file(config)
    watchdog_state = load_watchdog_state(config, settings)
    attempts = trim_restart_attempts(watchdog_state.setdefault("restart_attempts", []), now)
    watchdog_state["restart_attempts"] = attempts
    pid = read_pid_file(supervisor_pid_path(config))
    lock_held = supervisor_lock_held(config)
    # The flock is the authoritative liveness signal; the pid file is a best-effort
    # hint that is absent during clean-restart seams. Folding lock_held into `alive`
    # stops the watchdog from restarting a live supervisor just because its pid file
    # was momentarily unlinked.
    alive = lock_held or pid_is_alive(pid)
    health = evaluate_supervisor_health(runtime_state, pid, alive, now, settings)
    resource = resource_snapshot(config, runtime_state, settings)
    pressure_reasons = resource_pressure_reasons(resource, settings, state_error)
    restart_counts = restart_attempt_counts(attempts, now, settings)
    decision = "observe_only"
    reason = str(health.get("reason") or "healthy")
    new_pid: int | None = None
    log_path: Path | None = None

    if not settings.get("enabled", True):
        decision = "observe_only"
        reason = "watchdog_disabled"
    elif pressure_reasons:
        decision = "suppress_restart"
        reason = "resource_pressure:" + ",".join(pressure_reasons)
        if not health.get("healthy"):
            open_circuit(watchdog_state, now, reason, settings)
    elif health.get("healthy"):
        decision = "observe_only"
        reason = "supervisor_healthy"
    else:
        budget_reason = budget_suppression_reason(watchdog_state, now, settings)
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
            reason = f"dry_run:{health.get('reason')}"
        else:
            decision = "restart_supervisor"
            reason = str(health.get("reason") or "unhealthy")
            enter_watchdog_safe_mode(config, runtime_state, now, settings, reason)
            new_pid, log_path = start_supervisor(config, settings, now)
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
    parser.add_argument("--json", action="store_true", help="Print machine-readable result.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    result = run_watchdog(config, restart=args.restart, dry_run=args.dry_run)
    if args.json:
        import json

        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"watchdog decision={result['decision']} reason={result['reason']} pid={result.get('pid')} new_pid={result.get('new_pid')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
