#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
    except FileNotFoundError:
        return default


def resolve_repo_path(repo_root: Path, value: str | None, default: str) -> Path:
    raw = Path(value or default)
    if not raw.is_absolute():
        raw = repo_root / raw
    return raw


def config_path(repo_root: Path, config: dict[str, Any], key: str, default: str) -> Path:
    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    return resolve_repo_path(repo_root, str(paths.get(key) or default), default)


def read_pid(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(raw)
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
    return True


def pid_matches_supervisor(pid: int | None, repo_root: Path) -> bool:
    if not pid_is_alive(pid):
        return False
    proc_dir = Path("/proc") / str(pid)
    try:
        cmdline = proc_dir.joinpath("cmdline").read_bytes()
        cwd = proc_dir.joinpath("cwd").resolve()
    except OSError:
        return False
    parts = [part.decode("utf-8", errors="ignore") for part in cmdline.split(b"\x00") if part]
    joined = " ".join(parts)
    return cwd == repo_root and ".orchestrator/supervisor.py" in joined


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


def check(name: str, ok: bool, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), **(detail or {})}


def evaluate_runtime_health(
    repo_root: Path,
    *,
    config_path_arg: Path | None = None,
    now: datetime | None = None,
    max_heartbeat_age: float | None = None,
    require_watchdog: bool = False,
    max_watchdog_age: float = 180.0,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    config_path_resolved = config_path_arg or (repo_root / ".orchestrator" / "config.json")
    config = load_json(config_path_resolved, default={})
    if not isinstance(config, dict):
        config = {}

    state_path = config_path(repo_root, config, "state_file", ".orchestrator/state.json")
    state = load_json(state_path, default={})
    if not isinstance(state, dict):
        state = {}

    state_dir = state_path.parent
    pid_path = state_dir / "supervisor.pid"
    lock_path = state_dir / "supervisor.lock"
    pid = read_pid(pid_path)
    process_alive = pid_matches_supervisor(pid, repo_root)
    singleton_lock_held = lock_held(lock_path)
    supervisor_alive = process_alive or singleton_lock_held

    supervisor = state.get("supervisor", {}) if isinstance(state.get("supervisor"), dict) else {}
    heartbeat = parse_utc_timestamp(supervisor.get("last_heartbeat_at"))
    heartbeat_age = (now - heartbeat).total_seconds() if heartbeat is not None else None
    configured_watchdog = config.get("watchdog", {}) if isinstance(config.get("watchdog"), dict) else {}
    configured_supervisor = config.get("supervisor", {}) if isinstance(config.get("supervisor"), dict) else {}
    if max_heartbeat_age is None:
        max_heartbeat_age = float(
            configured_watchdog.get(
                "heartbeat_stale_seconds",
                max(900.0, float(configured_supervisor.get("poll_interval_seconds", 300.0)) * 3.0),
            )
        )

    checks = [
        check(
            "supervisor_process_alive",
            supervisor_alive,
            {"pid": pid, "pid_matches": process_alive, "lock_held": singleton_lock_held},
        ),
        check("supervisor_heartbeat_present", heartbeat is not None, {"last_heartbeat_at": supervisor.get("last_heartbeat_at")}),
        check(
            "supervisor_heartbeat_fresh",
            heartbeat_age is not None and heartbeat_age <= max_heartbeat_age,
            {"age_seconds": heartbeat_age, "max_age_seconds": max_heartbeat_age},
        ),
        check(
            "supervisor_not_degraded",
            str(supervisor.get("lifecycle") or "") != "degraded",
            {"lifecycle": supervisor.get("lifecycle"), "last_loop_error": supervisor.get("last_loop_error")},
        ),
    ]

    watchdog_report: dict[str, Any] | None = None
    if require_watchdog:
        watchdog_settings = config.get("watchdog", {}) if isinstance(config.get("watchdog"), dict) else {}
        watchdog_state_path = resolve_repo_path(
            repo_root,
            str(watchdog_settings.get("state_file") or ".orchestrator/watchdog-state.json"),
            ".orchestrator/watchdog-state.json",
        )
        watchdog_state = load_json(watchdog_state_path, default={})
        watchdog_updated = parse_utc_timestamp(watchdog_state.get("updated_at") if isinstance(watchdog_state, dict) else None)
        watchdog_age = (now - watchdog_updated).total_seconds() if watchdog_updated is not None else None
        watchdog_report = {
            "state_file": str(watchdog_state_path),
            "updated_at": watchdog_updated.isoformat().replace("+00:00", "Z") if watchdog_updated else None,
            "age_seconds": watchdog_age,
            "max_age_seconds": max_watchdog_age,
        }
        checks.append(check("watchdog_state_present", watchdog_updated is not None, watchdog_report))
        checks.append(
            check(
                "watchdog_probe_fresh",
                watchdog_age is not None and watchdog_age <= max_watchdog_age,
                watchdog_report,
            )
        )

    healthy = all(item["ok"] for item in checks)
    return {
        "healthy": healthy,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "repo_root": str(repo_root),
        "state_file": str(state_path),
        "supervisor": {
            "pid": pid,
            "alive": supervisor_alive,
            "process_alive": process_alive,
            "lock_held": singleton_lock_held,
            "last_heartbeat_at": supervisor.get("last_heartbeat_at"),
            "heartbeat_age_seconds": heartbeat_age,
            "max_heartbeat_age_seconds": max_heartbeat_age,
            "lifecycle": supervisor.get("lifecycle"),
            "last_loop_error": supervisor.get("last_loop_error"),
        },
        "watchdog": watchdog_report,
        "checks": checks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Pantheon supervisor/watchdog runtime health.")
    parser.add_argument("--repo", default=".", help="Pantheon repository root. Defaults to cwd.")
    parser.add_argument("--config-path", default=None, help="Path to .orchestrator/config.json.")
    parser.add_argument("--max-heartbeat-age", type=float, default=None)
    parser.add_argument("--require-watchdog", action="store_true")
    parser.add_argument("--max-watchdog-age", type=float, default=180.0)
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo).expanduser().resolve()
    report = evaluate_runtime_health(
        repo_root,
        config_path_arg=Path(args.config_path).expanduser().resolve() if args.config_path else None,
        max_heartbeat_age=args.max_heartbeat_age,
        require_watchdog=args.require_watchdog,
        max_watchdog_age=args.max_watchdog_age,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        status = "healthy" if report["healthy"] else "unhealthy"
        supervisor = report["supervisor"]
        print(
            "supervisor_runtime_health=%s pid=%s alive=%s heartbeat_age=%s lifecycle=%s"
            % (
                status,
                supervisor.get("pid"),
                supervisor.get("alive"),
                supervisor.get("heartbeat_age_seconds"),
                supervisor.get("lifecycle"),
            )
        )
        for item in report["checks"]:
            print(f"check {item['name']}: {'ok' if item['ok'] else 'FAIL'}")
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
