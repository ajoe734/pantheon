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


def expected_supervisor_command(
    repo_root: Path,
    config_path: Path,
    config: dict[str, Any],
) -> tuple[tuple[str, ...], Path] | None:
    """Return the exact immutable command and cwd declared by live config."""
    watchdog = config.get("watchdog") if isinstance(config.get("watchdog"), dict) else {}
    raw_command = watchdog.get("supervisor_command")
    if (
        not isinstance(raw_command, list)
        or not raw_command
        or any(not isinstance(item, str) or not item for item in raw_command)
    ):
        return None
    command = tuple(raw_command)
    entrypoints = [
        Path(item)
        for item in command[1:]
        if Path(item).name == "supervisor.py"
    ]
    if len(entrypoints) != 1 or not entrypoints[0].is_absolute():
        return None
    if command.count("--config") != 1:
        return None
    config_index = command.index("--config")
    if config_index + 1 >= len(command):
        return None
    declared_config = Path(command[config_index + 1])
    try:
        if declared_config.resolve() != config_path.resolve():
            return None
    except OSError:
        return None
    entrypoint = entrypoints[0]
    if entrypoint.parent.name != ".orchestrator":
        return None
    return command, entrypoint.parent.parent


def pid_matches_supervisor(
    pid: int | None,
    *,
    expected_command: tuple[str, ...] | None,
    expected_cwd: Path | None,
) -> bool:
    """Bind health to the exact configured immutable runtime generation."""
    if not pid_is_alive(pid):
        return False
    if expected_command is None or expected_cwd is None:
        return False
    proc_dir = Path("/proc") / str(pid)
    try:
        cmdline = proc_dir.joinpath("cmdline").read_bytes()
        cwd = proc_dir.joinpath("cwd").resolve()
    except OSError:
        return False
    parts = tuple(
        part.decode("utf-8", errors="strict")
        for part in cmdline.split(b"\x00")
        if part
    )
    return cwd == expected_cwd and parts == expected_command


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


def resolved_coordinator_status_root(repo_root: Path, config: dict[str, Any]) -> Path:
    env_val = os.environ.get("PANTHEON_STATUS_ROOT")
    if env_val and env_val.strip():
        return Path(os.path.expanduser(env_val.strip())).resolve()

    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    if "status_file" in paths:
        try:
            return config_path(repo_root, config, "status_file", "ai-status.json").parent.resolve()
        except KeyError:
            pass

    if "state_file" in paths:
        try:
            state_path = config_path(repo_root, config, "state_file", ".orchestrator/state.json").resolve()
            if state_path.parent.name == ".orchestrator":
                return state_path.parent.parent.resolve()
            return state_path.parent.resolve()
        except KeyError:
            pass

    return repo_root.resolve()


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
    coord_root = resolved_coordinator_status_root(repo_root, config)
    lock_path = coord_root / ".orchestrator" / "supervisor.lock"
    pid = read_pid(pid_path)
    declared_runtime = expected_supervisor_command(
        repo_root,
        config_path_resolved,
        config,
    )
    expected_command, expected_cwd = declared_runtime or (None, None)
    process_alive = pid_is_alive(pid)
    runtime_identity_matches = pid_matches_supervisor(
        pid,
        expected_command=expected_command,
        expected_cwd=expected_cwd,
    )
    singleton_lock_held = lock_held(lock_path)

    supervisor = state.get("supervisor", {}) if isinstance(state.get("supervisor"), dict) else {}
    heartbeat = parse_utc_timestamp(supervisor.get("last_heartbeat_at"))
    heartbeat_age = (now - heartbeat).total_seconds() if heartbeat is not None else None
    successful_loop = parse_utc_timestamp(supervisor.get("last_successful_loop_at"))
    successful_loop_age = (
        (now - successful_loop).total_seconds()
        if successful_loop is not None
        else None
    )
    configured_watchdog = config.get("watchdog", {}) if isinstance(config.get("watchdog"), dict) else {}
    configured_supervisor = config.get("supervisor", {}) if isinstance(config.get("supervisor"), dict) else {}
    if max_heartbeat_age is None:
        max_heartbeat_age = float(
            configured_watchdog.get(
                "heartbeat_stale_seconds",
                max(900.0, float(configured_supervisor.get("poll_interval_seconds", 300.0)) * 3.0),
            )
        )

    max_progress_age = float(
        configured_supervisor.get("stall_after_seconds", max_heartbeat_age)
    )
    identity_checks = [
        check(
            "configured_runtime_identity_present",
            declared_runtime is not None,
            {
                "expected_command": list(expected_command) if expected_command else None,
                "expected_cwd": str(expected_cwd) if expected_cwd else None,
            },
        ),
        check(
            "supervisor_runtime_identity_matches",
            runtime_identity_matches,
            {"pid": pid, "pid_matches": runtime_identity_matches},
        ),
    ]
    liveness_checks = [
        check("supervisor_process_alive", process_alive, {"pid": pid}),
        check("supervisor_singleton_lock_held", singleton_lock_held, {"lock_path": str(lock_path)}),
        check("supervisor_heartbeat_present", heartbeat is not None, {"last_heartbeat_at": supervisor.get("last_heartbeat_at")}),
        check(
            "supervisor_heartbeat_fresh",
            heartbeat_age is not None and 0 <= heartbeat_age <= max_heartbeat_age,
            {"age_seconds": heartbeat_age, "max_age_seconds": max_heartbeat_age},
        ),
    ]
    readiness_checks = [
        check(
            "supervisor_state_readable",
            isinstance(state.get("supervisor"), dict),
            {"state_file": str(state_path)},
        ),
        check(
            "supervisor_not_degraded",
            str(supervisor.get("lifecycle") or "") in {"running", "idle", "active"},
            {"lifecycle": supervisor.get("lifecycle"), "last_loop_error": supervisor.get("last_loop_error")},
        ),
    ]
    progress_checks = [
        check(
            "successful_loop_present",
            successful_loop is not None,
            {"last_successful_loop_at": supervisor.get("last_successful_loop_at")},
        ),
        check(
            "successful_loop_fresh",
            successful_loop_age is not None
            and 0 <= successful_loop_age <= max_progress_age,
            {
                "age_seconds": successful_loop_age,
                "max_age_seconds": max_progress_age,
            },
        ),
        check(
            "last_loop_error_clear",
            supervisor.get("last_loop_error") is None,
            {"last_loop_error": supervisor.get("last_loop_error")},
        ),
    ]
    checks = identity_checks + liveness_checks + readiness_checks + progress_checks

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
        contention_metrics_path = resolve_repo_path(
            repo_root,
            str(
                watchdog_settings.get("contention_metrics_file")
                or ".orchestrator/metrics/supervisor-watchdog-contention.jsonl"
            ),
            ".orchestrator/metrics/supervisor-watchdog-contention.jsonl",
        )
        contention_probe = load_latest_contention_probe(contention_metrics_path)
        contention_updated = parse_utc_timestamp(contention_probe.get("at") if contention_probe else None)
        probe_candidates = [
            ("watchdog_state", watchdog_updated),
            ("contention_metric", contention_updated),
        ]
        probe_source, probe_updated = max(
            ((source, updated) for source, updated in probe_candidates if updated is not None),
            key=lambda item: item[1],
            default=(None, None),
        )
        watchdog_age = (now - probe_updated).total_seconds() if probe_updated is not None else None
        watchdog_report = {
            "state_file": str(watchdog_state_path),
            "updated_at": watchdog_updated.isoformat().replace("+00:00", "Z") if watchdog_updated else None,
            "contention_metrics_file": str(contention_metrics_path),
            "contention_updated_at": (
                contention_updated.isoformat().replace("+00:00", "Z") if contention_updated else None
            ),
            "probe_source": probe_source,
            "probe_updated_at": probe_updated.isoformat().replace("+00:00", "Z") if probe_updated else None,
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

    dimensions = {
        "identity": {
            "ok": all(item["ok"] for item in identity_checks),
            "checks": identity_checks,
        },
        "liveness": {
            "ok": all(item["ok"] for item in liveness_checks),
            "checks": liveness_checks,
        },
        "readiness": {
            "ok": all(item["ok"] for item in readiness_checks),
            "checks": readiness_checks,
        },
        "progress": {
            "ok": all(item["ok"] for item in progress_checks),
            "checks": progress_checks,
        },
    }
    healthy = all(item["ok"] for item in dimensions.values()) and all(
        item["ok"] for item in checks
    )
    return {
        "healthy": healthy,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "repo_root": str(repo_root),
        "state_file": str(state_path),
        "dimensions": dimensions,
        "supervisor": {
            "pid": pid,
            "alive": process_alive and singleton_lock_held,
            "process_alive": process_alive,
            "runtime_identity_matches": runtime_identity_matches,
            "expected_cwd": str(expected_cwd) if expected_cwd else None,
            "lock_held": singleton_lock_held,
            "last_heartbeat_at": supervisor.get("last_heartbeat_at"),
            "heartbeat_age_seconds": heartbeat_age,
            "max_heartbeat_age_seconds": max_heartbeat_age,
            "last_successful_loop_at": supervisor.get("last_successful_loop_at"),
            "successful_loop_age_seconds": successful_loop_age,
            "max_progress_age_seconds": max_progress_age,
            "lifecycle": supervisor.get("lifecycle"),
            "last_loop_error": supervisor.get("last_loop_error"),
            "task_state_shadow": supervisor.get("task_state_shadow"),
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
