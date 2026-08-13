#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUS_COMMAND_ROOT_ENV = "PANTHEON_COMMAND_ROOT"
STATUS_COMMAND_SHA_ENV = "PANTHEON_COMMAND_RUNTIME_SHA"
STATUS_COMMAND_REMOTE_ENV = "PANTHEON_COMMAND_REMOTE"
STATUS_COMMAND_BASE_REF_ENV = "PANTHEON_COMMAND_BASE_REF"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def normalize_command(raw: list[str]) -> list[str]:
    if raw and raw[0] == "--":
        raw = raw[1:]
    return raw


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
    return Path(top).resolve() if top else None


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


def _normalize_github_slug(value: str | None) -> str:
    candidate = str(value or "").strip()
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


def _command_env(name: str, default: str = "") -> str:
    return str(os.environ.get(name) or default).strip()


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



def _status_root_from_runtime_path(path: Path, *, label: str) -> Path:
    expanded = Path(os.path.expanduser(str(path)))
    if not expanded.is_absolute():
        raise RuntimeError(f"{label} must be absolute: {expanded}")
    symlink_comp = _first_symlink_component(expanded)
    if symlink_comp is not None:
        raise RuntimeError(f"{label} path contains a symlink component: {symlink_comp}")
    if expanded.is_symlink():
        raise RuntimeError(f"{label} cannot be a symlink: {expanded}")
    resolved = expanded.resolve()
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


def _expected_coordination_root(
    *,
    heartbeat_path: Path | None,
    status_path: Path | None,
) -> Path | None:
    roots: list[Path] = []
    for label, path in (
        ("heartbeat_path", heartbeat_path),
        ("status_path", status_path),
    ):
        if path is None:
            continue
        root = _status_root_from_runtime_path(path, label=label)
        if root not in roots:
            roots.append(root)
    if len(roots) > 1:
        raise RuntimeError(
            "worker_runner heartbeat/status paths disagree on the supervisor "
            f"coordination root: {roots[0]} != {roots[1]}"
        )
    return roots[0] if roots else None


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


def validate_coordination_root(
    workspace_path: Path | None,
    *,
    heartbeat_path: Path | None = None,
    status_path: Path | None = None,
) -> Path | None:
    """Validate the central coordination root before launching the worker CLI."""

    raw = str(os.environ.get("PANTHEON_STATUS_ROOT") or "").strip()
    if not raw:
        if workspace_path is not None:
            raise RuntimeError(
                "PANTHEON_STATUS_ROOT is required when worker_runner isolates "
                "a task worktree"
            )
        return None

    expanded = Path(os.path.expanduser(raw))
    if not expanded.is_absolute():
        raise RuntimeError("PANTHEON_STATUS_ROOT must be an absolute path")
    symlink_component = _first_symlink_component(expanded)
    if symlink_component is not None:
        raise RuntimeError(
            f"PANTHEON_STATUS_ROOT cannot include a symlink component: {symlink_component}"
        )

    root = expanded.resolve()
    if not root.exists() or not root.is_dir():
        raise RuntimeError(f"PANTHEON_STATUS_ROOT does not exist or is not a directory: {root}")
    if workspace_path is not None and root == workspace_path.resolve():
        raise RuntimeError(
            "PANTHEON_STATUS_ROOT must point at the supervisor coordination "
            "root, not the isolated task worktree"
        )
    expected_root = _expected_coordination_root(
        heartbeat_path=heartbeat_path,
        status_path=status_path,
    )
    if expected_root is not None and root != expected_root:
        raise RuntimeError(
            "PANTHEON_STATUS_ROOT does not match the worker_runner runtime "
            f"coordination root: {root} != {expected_root}"
        )
    if _git_toplevel(root) != root:
        raise RuntimeError(f"PANTHEON_STATUS_ROOT must be a git repository root: {root}")

    status_file = root / "ai-status.json"
    if not status_file.exists() or not status_file.is_file():
        raise RuntimeError(
            f"PANTHEON_STATUS_ROOT is missing required ai-status.json: {status_file}"
        )
    if status_file.is_symlink():
        raise RuntimeError(f"ai-status.json cannot be a symlink: {status_file}")

    # Enforce supervisor marker paths exist
    for marker_path in (
        root / ".orchestrator" / "state.json",
        root / ".orchestrator" / "approval-queue.json",
        root / ".orchestrator" / "config.json",
    ):
        if not marker_path.exists() or not marker_path.is_file():
            raise RuntimeError(
                f"PANTHEON_STATUS_ROOT is missing required supervisor marker: {marker_path}"
            )

    for path in (
        root / "ai-activity-log.jsonl",
        root / "current-work.md",
        root / "docs-site",
        root / "docs-site" / "ai-status.json",
        root / "docs-site" / "current-work.md",
        root / "docs-site" / "dashboard-bundle.json",
        root / "docs-site" / "orchestrator-state.json",
        root / "docs-site" / "approval-queue.json",
        root / "docs-site" / "ai-activity-log.jsonl",
        root / "ai-task-archive",
        root / "ai-task-archive" / "index.json",
        root / "ai-task-archive" / "tasks",
        root / ".orchestrator" / "state.json",
        root / ".orchestrator" / "approval-queue.json",
        root / ".orchestrator" / "config.json",
        root / ".orchestrator" / "task-state.lock",
        root / ".orchestrator" / "activity-audit.lock",
    ):
        symlink_comp = _first_symlink_component(path)
        if symlink_comp is not None:
            raise RuntimeError(f"coordination path cannot be a symlink: {path} (contains symlink component: {symlink_comp})")
        if path.is_symlink():
            raise RuntimeError(f"coordination path cannot be a symlink: {path}")

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

    os.environ["PANTHEON_STATUS_ROOT"] = str(root)
    return root


def validate_status_command_runtime() -> dict[str, str]:
    raw = _command_env(STATUS_COMMAND_ROOT_ENV)
    if not raw:
        raise RuntimeError(
            "PANTHEON_COMMAND_ROOT is required when worker_runner launches an auto worker"
        )
    expanded = Path(os.path.expanduser(raw))
    if not expanded.is_absolute():
        raise RuntimeError(f"{STATUS_COMMAND_ROOT_ENV} must be an absolute path")
    symlink_component = _first_symlink_component(expanded)
    if symlink_component is not None:
        raise RuntimeError(
            f"{STATUS_COMMAND_ROOT_ENV} cannot include a symlink component: {symlink_component}"
        )
    root = expanded.resolve()
    if not root.exists() or not root.is_dir():
        raise RuntimeError(f"{STATUS_COMMAND_ROOT_ENV} does not exist or is not a directory: {root}")
    if _git_toplevel(root) != root:
        raise RuntimeError(f"{STATUS_COMMAND_ROOT_ENV} must be a git repository root: {root}")

    source_sha = _git_stdout(root, ["rev-parse", "HEAD"])
    expected_sha = _command_env(STATUS_COMMAND_SHA_ENV)
    if not expected_sha:
        raise RuntimeError("PANTHEON_COMMAND_RUNTIME_SHA is required when worker_runner launches an auto worker")
    if source_sha != expected_sha:
        raise RuntimeError(
            f"PANTHEON_COMMAND_RUNTIME_SHA mismatch: command root is {source_sha}, expected {expected_sha}"
        )

    expected_remote = _normalize_github_slug(
        _command_env(STATUS_COMMAND_REMOTE_ENV, "ajoe734/pantheon")
    )
    remote_url = _git_stdout(root, ["remote", "get-url", "origin"])
    actual_remote = _normalize_github_slug(remote_url)
    if expected_remote and actual_remote != expected_remote:
        raise RuntimeError(
            f"{STATUS_COMMAND_ROOT_ENV} remote mismatch: {actual_remote or remote_url} != {expected_remote}"
        )

    base_ref = _command_env(STATUS_COMMAND_BASE_REF_ENV, "origin/dev") or "origin/dev"
    _git_stdout(root, ["rev-parse", "--verify", base_ref])
    proc = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", source_sha, base_ref],
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
    res_status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "-uall"],
        capture_output=True,
        text=True,
        check=False,
    )
    if res_status.returncode != 0:
        raise RuntimeError(f"Failed to check git status on command root: {res_status.stderr}")

    for line in res_status.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) < 2:
            continue
        status_code, filepath = parts[0], parts[1]
        filepath = filepath.strip('"\'')
        if filepath.endswith((".py", ".sh", ".pyc", ".so", ".pl", ".rb")):
            raise RuntimeError(
                f"PANTHEON_COMMAND_ROOT contains dirty executable/import file: {filepath} (status: {status_code})"
            )

    return {
        "command_root": str(root),
        "source_sha": source_sha,
        "remote": expected_remote or actual_remote,
        "base_ref": base_ref,
    }


import re as _re


def derive_agent(run_id: str) -> str:
    """Agent label from a worker run-id, e.g. 'claude-1-2026...' -> 'claude-1'."""
    m = _re.match(r"([a-zA-Z][a-zA-Z0-9]*(?:-[0-9]+)?)-[0-9]{8}T", run_id or "")
    return m.group(1) if m else (run_id or "").split("-")[0]


def derive_task_id(cmd):
    """Parse the dispatched task id ('Task ID: <ID>') from the worker command."""
    m = _re.search(r"Task ID:\s*([A-Z][A-Z0-9_-]+)", " ".join(str(c) for c in cmd))
    return m.group(1) if m else None


def _get_task_roles(coordination_root: Path | None, task_id: str | None) -> dict[str, str]:
    roles = {"owner": "", "reviewer": ""}
    if not coordination_root or not task_id:
        return roles
    status_file = coordination_root / "ai-status.json"
    if not status_file.exists():
        return roles
    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for t in data.get("tasks", []):
                if isinstance(t, dict) and t.get("id") == task_id:
                    roles["owner"] = str(t.get("owner") or "").strip()
                    roles["reviewer"] = str(t.get("reviewer") or "").strip()
                    break
    except Exception:
        pass
    return roles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an auto-worker command with heartbeat and terminal markers.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--heartbeat-path", required=True)
    parser.add_argument("--status-path", required=True)
    parser.add_argument("--heartbeat-interval-seconds", type=float, default=15.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = normalize_command(list(args.command))
    if not command:
        print("worker_runner: missing command after --", file=sys.stderr)
        return 2

    agent = derive_agent(args.run_id)
    task_id = derive_task_id(command)

    raw_heartbeat_path = Path(args.heartbeat_path)
    raw_status_path = Path(args.status_path)

    # Validate raw supplied paths to prevent symlink bypasses
    for path, label in [
        (raw_heartbeat_path, "heartbeat_path"),
        (raw_status_path, "status_path"),
    ]:
        expanded = Path(os.path.expanduser(str(path)))
        if not expanded.is_absolute():
            raise RuntimeError(f"{label} must be absolute: {expanded}")
        symlink_comp = _first_symlink_component(expanded)
        if symlink_comp is not None:
            raise RuntimeError(f"{label} contains a symlink component: {symlink_comp}")
        if expanded.is_symlink():
            raise RuntimeError(f"{label} cannot be a symlink: {expanded}")

    pw = os.environ.get("PANTHEON_WORKTREE_ROOT")
    ow = os.environ.get("ORCH_WORKSPACE_PATH")
    if pw and ow:
        pw_resolved = Path(os.path.expanduser(pw)).resolve()
        ow_resolved = Path(os.path.expanduser(ow)).resolve()
        if pw_resolved != ow_resolved:
            raise RuntimeError(
                f"Conflicting workspace roots: PANTHEON_WORKTREE_ROOT={pw_resolved} != ORCH_WORKSPACE_PATH={ow_resolved}"
            )
    workspace_path = pw or ow
    if workspace_path:
        workspace_path = Path(os.path.expanduser(workspace_path)).resolve()
    coordination_root = validate_coordination_root(
        workspace_path if isinstance(workspace_path, Path) else None,
        heartbeat_path=raw_heartbeat_path,
        status_path=raw_status_path,
    )
    heartbeat_path = raw_heartbeat_path.resolve()
    status_path = raw_status_path.resolve()
    command_runtime = validate_status_command_runtime()
    if workspace_path:
        try:
            os.chdir(workspace_path)
            print(f"worker_runner: isolated working directory to {workspace_path}", file=sys.stderr)
        except OSError as exc:
            print(f"worker_runner: failed to isolate working directory to {workspace_path}: {exc}", file=sys.stderr)

    task_roles = _get_task_roles(coordination_root, task_id)
    active_role = ""
    if agent:
        agent_lower = agent.lower()
        normalized_agent = agent_lower.split("-")[0]
        owner_lower = task_roles["owner"].lower() if task_roles["owner"] else ""
        reviewer_lower = task_roles["reviewer"].lower() if task_roles["reviewer"] else ""
        if normalized_agent == owner_lower.split("-")[0] or agent_lower == owner_lower:
            active_role = "owner"
        elif normalized_agent == reviewer_lower.split("-")[0] or agent_lower == reviewer_lower:
            active_role = "reviewer"

    interval = max(1.0, float(args.heartbeat_interval_seconds or 15.0))
    started_at = utc_now()
    child: subprocess.Popen[str] | None = None
    terminating_signal: int | None = None

    status: dict[str, Any] = {
        "run_id": args.run_id,
        "agent": agent,
        "task_id": task_id,
        "role": active_role,
        "owner": task_roles["owner"],
        "reviewer": task_roles["reviewer"],
        "status": "starting",
        "pid": os.getpid(),
        "child_pid": None,
        "command": command,
        "started_at": started_at,
        "last_heartbeat_at": started_at,
        "finished_at": None,
        "exit_code": None,
        "signal": None,
        "status_command_runtime": command_runtime,
    }

    def publish(next_status: str) -> None:
        now = utc_now()
        status["status"] = next_status
        status["last_heartbeat_at"] = now
        write_json(heartbeat_path, {
            "run_id": args.run_id,
            "agent": agent,
            "task_id": task_id,
            "role": active_role,
            "owner": task_roles["owner"],
            "reviewer": task_roles["reviewer"],
            "status": next_status,
            "pid": os.getpid(),
            "child_pid": status.get("child_pid"),
            "updated_at": now,
            "status_command_runtime": command_runtime,
        })
        write_json(status_path, status)

    signal_received_at: float | None = None

    def forward_signal(signum: int, _frame: Any) -> None:
        nonlocal terminating_signal, signal_received_at
        if terminating_signal is None:
            terminating_signal = signum
            signal_received_at = time.monotonic()
        status["signal"] = signum
        if child is not None and child.poll() is None:
            try:
                os.killpg(child.pid, signum)
            except OSError:
                try:
                    child.send_signal(signum)
                except OSError:
                    pass

    orig_sigterm = signal.getsignal(signal.SIGTERM)
    orig_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, forward_signal)
    signal.signal(signal.SIGINT, forward_signal)

    try:
        publish("starting")
        child = subprocess.Popen(
            command,
            text=True,
            cwd=str(workspace_path) if workspace_path else None,
            start_new_session=True,
        )
        status["child_pid"] = child.pid
        publish("running")
        next_heartbeat = time.monotonic() + interval
        direct_exit_code: int | None = None
        while True:
            if direct_exit_code is None:
                direct_exit_code = child.poll()
                if direct_exit_code is not None:
                    status["exit_code"] = direct_exit_code
                    status["finished_at"] = utc_now()

            # Normal path: child exited and we aren't terminating
            if direct_exit_code is not None and terminating_signal is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                    start_wait = time.monotonic()
                    group_dead = False
                    while time.monotonic() - start_wait < 2.0:
                        try:
                            os.killpg(child.pid, 0)
                            time.sleep(0.05)
                        except OSError:
                            group_dead = True
                            break
                    if not group_dead:
                        os.killpg(child.pid, signal.SIGKILL)
                except OSError:
                    pass
                publish("completed" if direct_exit_code == 0 else "failed")
                if direct_exit_code < 0:
                    return 128 + abs(direct_exit_code)
                return direct_exit_code

            # Termination path: check if group contains survivors
            if terminating_signal is not None and signal_received_at is not None:
                group_alive = False
                try:
                    os.killpg(child.pid, 0)
                    group_alive = True
                except OSError:
                    pass

                if not group_alive:
                    exit_code = 128 + terminating_signal
                    status["exit_code"] = exit_code
                    status["finished_at"] = utc_now()
                    publish("failed")
                    return exit_code

                # Group is still alive, check 5-second deadline
                elapsed = time.monotonic() - signal_received_at
                if elapsed > 5.0:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except OSError:
                        pass
                    if direct_exit_code is None:
                        try:
                            child.wait(timeout=1.0)
                        except subprocess.TimeoutExpired:
                            pass
                        direct_exit_code = child.poll()
                    exit_code = 128 + terminating_signal
                    status["exit_code"] = exit_code
                    status["finished_at"] = utc_now()
                    publish("failed")
                    return exit_code

            if time.monotonic() >= next_heartbeat:
                publish("running")
                next_heartbeat = time.monotonic() + interval
            time.sleep(min(1.0, interval))
    except BaseException as exc:
        status["status"] = "failed"
        status["finished_at"] = utc_now()
        status["error"] = f"{type(exc).__name__}: {exc}"
        if terminating_signal is not None:
            status["signal"] = terminating_signal
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
                start_wait = time.monotonic()
                group_dead = False
                while time.monotonic() - start_wait < 2.0:
                    try:
                        os.killpg(child.pid, 0)
                        time.sleep(0.05)
                    except OSError:
                        group_dead = True
                        break
                if not group_dead:
                    os.killpg(child.pid, signal.SIGKILL)
            except OSError:
                try:
                    child.terminate()
                    try:
                        child.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
                except OSError:
                    pass
            if child.poll() is None:
                try:
                    child.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    try:
                        child.kill()
                        child.wait(timeout=1.0)
                    except OSError:
                        pass
        try:
            write_json(status_path, status)
        except OSError:
            pass
        raise
    finally:
        signal.signal(signal.SIGTERM, orig_sigterm)
        signal.signal(signal.SIGINT, orig_sigint)


if __name__ == "__main__":
    raise SystemExit(main())
