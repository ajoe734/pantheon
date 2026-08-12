#!/usr/bin/env python3
"""Replace the supervisor with one exact authoritative-V2 runtime.

This command intentionally has no incumbent compatibility path.  A promotion
is a short replacement operation: render the candidate's V2 config, stop the
existing supervisor, atomically install that config, and launch the candidate.
It never reconstructs a retired runtime or tries to restore one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from provision_live_supervisor_config import (
    build_live_config,
    load_json_object,
    validated_immutable_command_root,
    validated_root,
    write_json_atomic,
)


LIVE_SUPERVISOR_CONFIG_PATH = Path(
    "/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json"
)
TASK_STATE_MODE = "authoritative"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _path(value: str | Path) -> Path:
    return Path(value).expanduser().absolute()


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file: {path}")
    return load_json_object(path)


def _git_output(root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise ValueError("candidate V2 source Git identity is unavailable") from exc


def candidate_runtime_identity(repo_root: Path) -> dict[str, str]:
    """Return the only source identity allowed to be launched."""

    identity = validated_immutable_command_root(repo_root)
    root = Path(identity["root"])
    if _git_output(root, "status", "--porcelain"):
        raise ValueError("candidate V2 source tree must be clean")
    return identity


def _validate_authoritative_store(config: Mapping[str, Any]) -> None:
    store = config.get("task_state_store")
    if not isinstance(store, Mapping):
        raise ValueError("V2 config must define task_state_store")
    if str(store.get("mode") or "").strip().lower() != TASK_STATE_MODE:
        raise ValueError("V2 config requires task_state_store.mode=authoritative")
    event_log = Path(str(store.get("event_log") or "")).expanduser()
    if not event_log.is_absolute() or event_log.name in {"", ".", ".."}:
        raise ValueError("V2 config requires an absolute task-state event log")


def render_v2_config(
    repo_root: Path,
    *,
    status_root: Path,
    live_config_path: Path,
    python_executable: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Render a new V2 runtime config without using an incumbent overlay."""

    identity = candidate_runtime_identity(repo_root)
    repo_config = _load_json(repo_root / ".orchestrator" / "config.json", label="candidate config")
    rendered = build_live_config(
        repo_config,
        existing_live_config=None,
        command_root=repo_root,
        status_root=status_root,
        live_config_path=live_config_path,
        python_executable=python_executable,
    )
    _validate_authoritative_store(rendered)
    return rendered, identity


def _pid_path(rendered: Mapping[str, Any]) -> Path:
    paths = rendered.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("V2 config must define paths")
    state_file = Path(str(paths.get("state_file") or "")).expanduser()
    if not state_file.is_absolute():
        raise ValueError("rendered V2 state_file must be absolute")
    return state_file.parent / "supervisor.pid"


def _read_pid(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
        pid = int(value)
    except (OSError, ValueError):
        return None
    return pid if pid > 0 and value == str(pid) else None


def _process_is_supervisor(pid: int) -> bool:
    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except OSError:
        return False
    argv = [item for item in raw.decode("utf-8", errors="ignore").split("\0") if item]
    return any(Path(item).name == "supervisor.py" for item in argv)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_existing_supervisor(pid_path: Path, *, timeout_seconds: float) -> int | None:
    """Stop the recorded supervisor.  No replacement is attempted on failure."""

    pid = _read_pid(pid_path)
    if pid is None or not _pid_alive(pid):
        return None
    if not _process_is_supervisor(pid):
        raise ValueError(f"pid file does not identify a supervisor process: {pid}")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout_seconds
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _pid_alive(pid):
        raise RuntimeError(f"existing supervisor did not stop within {timeout_seconds:g}s")
    return pid


def launch_v2_supervisor(
    rendered: Mapping[str, Any],
    *,
    identity: Mapping[str, str],
    status_root: Path,
) -> int:
    watchdog = rendered.get("watchdog")
    if not isinstance(watchdog, Mapping):
        raise ValueError("V2 config must define watchdog")
    argv = watchdog.get("supervisor_command")
    if not isinstance(argv, list) or not argv or any(not isinstance(item, str) for item in argv):
        raise ValueError("V2 supervisor command is invalid")
    root = Path(identity["root"])
    environment = os.environ.copy()
    environment.update(
        {
            "PANTHEON_COMMAND_ROOT": str(root),
            "PANTHEON_COMMAND_RUNTIME_SHA": identity["head"],
            "PANTHEON_COMMAND_REMOTE": identity["repository"],
            "PANTHEON_COMMAND_BASE_REF": "origin/dev",
            "PANTHEON_STATUS_ROOT": str(status_root),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    process = subprocess.Popen(
        argv,
        cwd=root,
        env=environment,
        start_new_session=True,
        close_fds=True,
    )
    return int(process.pid)


def _write_evidence(path: Path | None, result: Mapping[str, Any]) -> None:
    if path is None:
        return
    if not path.is_absolute():
        raise ValueError("evidence path must be absolute")
    write_json_atomic(path, dict(result))


def replace_supervisor(
    repo_root: Path,
    *,
    status_root: Path,
    live_config_path: Path,
    python_executable: Path,
    termination_timeout: float,
    evidence_path: Path | None = None,
) -> dict[str, Any]:
    """Stop old, install exact V2 config, then launch exact V2 source."""

    if termination_timeout <= 0:
        raise ValueError("termination timeout must be positive")
    rendered, identity = render_v2_config(
        repo_root,
        status_root=status_root,
        live_config_path=live_config_path,
        python_executable=python_executable,
    )
    rendered_bytes = (json.dumps(rendered, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    result: dict[str, Any] = {
        "schema_version": 2,
        "kind": "supervisor_v2_replacement",
        "recorded_at": _utc_now(),
        "candidate": identity,
        "live_config": str(live_config_path),
        "config_sha256": _sha256(rendered_bytes),
        "task_state_store": dict(rendered["task_state_store"]),
        "stopped_pid": None,
        "launched_pid": None,
        "outcome": "failed",
    }
    try:
        stopped_pid = stop_existing_supervisor(
            _pid_path(rendered), timeout_seconds=termination_timeout
        )
        result["stopped_pid"] = stopped_pid
        write_json_atomic(live_config_path, rendered)
        result["launched_pid"] = launch_v2_supervisor(
            rendered,
            identity=identity,
            status_root=status_root,
        )
        result["outcome"] = "launched"
        result["exit_code"] = 0
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["exit_code"] = 1
    _write_evidence(evidence_path, result)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Exact V2 command source root.")
    parser.add_argument("--status-root", default=os.environ.get("PANTHEON_STATUS_ROOT"))
    parser.add_argument("--live-config", default=str(LIVE_SUPERVISOR_CONFIG_PATH))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--termination-timeout", type=float, default=15.0)
    parser.add_argument("--evidence-path")
    parser.add_argument("--promote", action="store_true", help="Stop and replace the runtime.")
    parser.add_argument("--discover-only", action="store_true", help="Render and validate only.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.promote == args.discover_only:
        parser.error("select exactly one of --promote or --discover-only")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = _path(args.repo)
    status_root = _path(args.status_root) if args.status_root else repo_root
    live_config_path = _path(args.live_config)
    python_executable = _path(args.python)
    try:
        if not python_executable.is_file():
            raise ValueError(f"python executable does not exist: {python_executable}")
        validated_root(status_root, label="status root", required=(".git", "ai-status.json"))
        if args.discover_only:
            rendered, identity = render_v2_config(
                repo_root,
                status_root=status_root,
                live_config_path=live_config_path,
                python_executable=python_executable,
            )
            result: dict[str, Any] = {
                "schema_version": 2,
                "kind": "supervisor_v2_replacement_preflight",
                "outcome": "ready",
                "candidate": identity,
                "live_config": str(live_config_path),
                "task_state_store": dict(rendered["task_state_store"]),
                "supervisor_command": rendered["watchdog"]["supervisor_command"],
            }
        else:
            evidence_path = _path(args.evidence_path) if args.evidence_path else None
            result = replace_supervisor(
                repo_root,
                status_root=status_root,
                live_config_path=live_config_path,
                python_executable=python_executable,
                termination_timeout=args.termination_timeout,
                evidence_path=evidence_path,
            )
    except (OSError, ValueError) as exc:
        result = {"outcome": "failed", "exit_code": 1, "error": f"{type(exc).__name__}: {exc}"}
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"supervisor_v2_replacement={result['outcome']}")
        if result.get("error"):
            print(result["error"], file=sys.stderr)
    return int(result.get("exit_code", 0 if result.get("outcome") == "ready" else 1))


if __name__ == "__main__":
    raise SystemExit(main())
