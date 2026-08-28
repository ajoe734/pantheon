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
import shutil
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from provision_live_supervisor_config import (
    build_live_config,
    ensure_approval_queue_marker,
    load_json_object,
    parse_repository_integration_roots,
    parse_repository_source_roots,
    validated_immutable_command_root,
    validated_root,
    write_json_atomic,
)


LIVE_SUPERVISOR_CONFIG_PATH = Path(
    "/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json"
)
COMMAND_RUNTIME_PARENT = Path("/home/lupin/pantheon-ci-deploy/command-runtimes")
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
    runtime_parent = COMMAND_RUNTIME_PARENT.expanduser().absolute().resolve()
    if root.parent != runtime_parent or root.name != identity["head"]:
        raise ValueError(
            "candidate V2 command source must be the exact "
            f"command-runtimes/<HEAD> checkout under {runtime_parent}"
        )
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
    repository_source_roots: Mapping[str, Path | str] | None = None,
    repository_integration_roots: Mapping[str, Path | str] | None = None,
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
        repository_source_roots=repository_source_roots,
        repository_integration_roots=repository_integration_roots,
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


def _incumbent_pid_path(live_config_path: Path, rendered: Mapping[str, Any]) -> Path:
    """Read the incumbent PID location only from its installed config.

    Status-root replacement is an ordinary V2 operation: command source and
    coordination state may both change together.  The prior config is the
    only durable identity for the process to stop; process cwd and a global
    product-root PID file are not authority.
    """

    if not live_config_path.exists():
        return _pid_path(rendered)
    incumbent = _load_json(live_config_path, label="installed live config")
    return _pid_path(incumbent)


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
    log_path = status_root / ".orchestrator" / "logs" / "supervisor.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fd = os.open(
        log_path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC,
        0o600,
    )
    os.chmod(log_path, 0o600)
    with os.fdopen(log_fd, "ab") as log_output:
        process = subprocess.Popen(
            argv,
            cwd=root,
            env=environment,
            stdout=log_output,
            stderr=subprocess.STDOUT,
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


def _sync_directory_tree(source: Path, dest: Path) -> None:
    if dest.exists():
        _make_tree_owner_writable(dest)
        shutil.rmtree(dest)
    if source.exists():
        shutil.copytree(source, dest)
        _make_tree_owner_writable(dest)


def _make_tree_owner_writable(root: Path) -> None:
    """Keep the coordination-root code mirror mutable after a sealed copy."""

    if root.is_symlink() or not root.exists():
        return
    for current_root, dirnames, filenames in os.walk(root, topdown=False, followlinks=False):
        current = Path(current_root)
        for name in filenames:
            path = current / name
            if not path.is_symlink():
                mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
                os.chmod(path, mode | stat.S_IWUSR, follow_symlinks=False)
        for name in dirnames:
            path = current / name
            if not path.is_symlink():
                mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
                os.chmod(
                    path,
                    mode | stat.S_IWUSR | stat.S_IXUSR,
                    follow_symlinks=False,
                )
        mode = stat.S_IMODE(current.stat(follow_symlinks=False).st_mode)
        os.chmod(
            current,
            mode | stat.S_IWUSR | stat.S_IXUSR,
            follow_symlinks=False,
        )


def _sync_top_level_py_files(source_dir: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(dest_dir.stat(follow_symlinks=False).st_mode)
    os.chmod(
        dest_dir,
        mode | stat.S_IWUSR | stat.S_IXUSR,
        follow_symlinks=False,
    )
    wanted = {path.name for path in source_dir.glob("*.py")} if source_dir.exists() else set()
    existing = {path.name for path in dest_dir.glob("*.py")}
    for name in existing - wanted:
        (dest_dir / name).unlink()
    for name in wanted:
        destination = dest_dir / name
        shutil.copy2(source_dir / name, destination)
        mode = stat.S_IMODE(destination.stat(follow_symlinks=False).st_mode)
        os.chmod(destination, mode | stat.S_IWUSR, follow_symlinks=False)


def seal_command_runtime(root: Path) -> dict[str, Any]:
    """Remove write bits from one validated immutable command runtime.

    Auto workers execute status commands from this tree but never need to
    mutate it.  Sealing every non-symlink entry turns an accidental edit into
    an immediate permission error instead of poisoning every later worker's
    command-runtime integrity check.  Execute bits and all read bits are
    preserved.
    """

    root = root.expanduser().absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"command runtime seal target must be a direct directory: {root}")
    root = root.resolve()
    changed_paths = 0
    sealed_paths = 0
    for current_root, dirnames, filenames in os.walk(root, topdown=False, followlinks=False):
        current = Path(current_root)
        for name in (*filenames, *dirnames):
            path = current / name
            if path.is_symlink():
                continue
            mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
            sealed_mode = mode & ~0o222
            if mode != sealed_mode:
                os.chmod(path, sealed_mode, follow_symlinks=False)
                changed_paths += 1
            sealed_paths += 1
    root_mode = stat.S_IMODE(root.stat(follow_symlinks=False).st_mode)
    sealed_root_mode = root_mode & ~0o222
    if root_mode != sealed_root_mode:
        os.chmod(root, sealed_root_mode, follow_symlinks=False)
        changed_paths += 1
    sealed_paths += 1
    return {
        "outcome": "sealed",
        "root": str(root),
        "sealed_paths": sealed_paths,
        "changed_paths": changed_paths,
    }


def verify_worker_sandbox(root: Path) -> dict[str, Any]:
    """Prove bubblewrap can enforce the provider's read-only runtime mount."""

    binary = shutil.which("bwrap")
    if not binary:
        raise ValueError(
            "bubblewrap (bwrap) is required before promoting a worker command runtime"
        )
    runtime_root = root.expanduser().resolve()
    probe = subprocess.run(
        [
            binary,
            "--die-with-parent",
            "--unshare-pid",
            "--bind",
            "/",
            "/",
            "--dev-bind",
            "/dev",
            "/dev",
            "--ro-bind",
            str(runtime_root),
            str(runtime_root),
            "--proc",
            "/proc",
            "--",
            "/bin/true",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or "bubblewrap probe failed").strip()
        raise ValueError(f"worker command-runtime sandbox is unavailable: {detail}")
    return {
        "outcome": "available",
        "binary": str(Path(binary).resolve()),
        "command_root": str(runtime_root),
    }


def sync_coordination_root_code(candidate_root: Path, status_root: Path) -> dict[str, Any]:
    """Best-effort refresh of status_root's own governance-tool code.

    status_root (coordination-root) is a stable worktree that also holds
    live, locally-mutated task/activity/archive data
    (ai-status.json, ai-task-archive/, current-work.md, .orchestrator/
    state.json, .orchestrator/logs/, lock files, ...) which this must never
    touch. Scope is therefore an explicit allowlist of pure-code paths this
    directory's own scripts/ai-status.sh actually executes when invoked
    without PANTHEON_COMMAND_ROOT (a bare manual Human/Ops command) --
    anything not listed here is left alone, never inferred from a directory
    scan, so a future data file added under an already-synced directory
    can't be silently overwritten by widening the sweep without a matching
    allowlist change.

    Mirrors rather than only adds: a file removed from candidate_root's
    scope is also removed from status_root's copy, so a promotion cannot
    leave retired code behind.

    Best-effort and independent of the supervisor replacement outcome: a
    failure here must never fail or roll back an otherwise-successful
    supervisor replacement. It only affects a bare manual Human/Ops
    invocation of status_root/scripts/ai-status.sh -- real auto workers
    always route through PANTHEON_COMMAND_ROOT (the exact candidate_root
    this copies from), so they are unaffected either way.
    """

    result: dict[str, Any] = {"outcome": "skipped", "paths": []}
    try:
        _sync_directory_tree(candidate_root / "scripts", status_root / "scripts")
        result["paths"].append("scripts")
        _sync_top_level_py_files(
            candidate_root / ".orchestrator", status_root / ".orchestrator"
        )
        result["paths"].append(".orchestrator/*.py")
        _sync_directory_tree(
            candidate_root / ".orchestrator" / "rewrite",
            status_root / ".orchestrator" / "rewrite",
        )
        result["paths"].append(".orchestrator/rewrite")
        result["outcome"] = "synced"
    except Exception as exc:
        result["outcome"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def replace_supervisor(
    repo_root: Path,
    *,
    status_root: Path,
    live_config_path: Path,
    python_executable: Path,
    termination_timeout: float,
    evidence_path: Path | None = None,
    repository_source_roots: Mapping[str, Path | str] | None = None,
    repository_integration_roots: Mapping[str, Path | str] | None = None,
) -> dict[str, Any]:
    """Stop old, install exact V2 config, then launch exact V2 source."""

    if termination_timeout <= 0:
        raise ValueError("termination timeout must be positive")
    rendered, identity = render_v2_config(
        repo_root,
        status_root=status_root,
        live_config_path=live_config_path,
        python_executable=python_executable,
        repository_source_roots=repository_source_roots,
        repository_integration_roots=repository_integration_roots,
    )
    rendered_bytes = (json.dumps(rendered, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    approval_queue_value = rendered.get("paths", {}).get("approval_queue")
    if not isinstance(approval_queue_value, str) or not approval_queue_value.strip():
        raise ValueError("rendered V2 config must define paths.approval_queue")
    approval_queue_path = Path(approval_queue_value).expanduser().absolute()
    incumbent_pid_path = _incumbent_pid_path(live_config_path, rendered)
    result: dict[str, Any] = {
        "schema_version": 2,
        "kind": "supervisor_v2_replacement",
        "recorded_at": _utc_now(),
        "candidate": identity,
        "live_config": str(live_config_path),
        "config_sha256": _sha256(rendered_bytes),
        "task_state_store": dict(rendered["task_state_store"]),
        "repository_source_roots": {
            repository_id: str(entry.get("local_path"))
            for repository_id, entry in (
                (rendered.get("coordination") or {}).get("repositories") or {}
            ).items()
            if isinstance(entry, dict) and entry.get("local_path")
        },
        "repository_integration_roots": {
            repository_id: str(entry.get("integration_path"))
            for repository_id, entry in (
                (rendered.get("coordination") or {}).get("repositories") or {}
            ).items()
            if isinstance(entry, dict) and entry.get("integration_path")
        },
        "approval_queue": str(approval_queue_path),
        "incumbent_pid_file": str(incumbent_pid_path),
        "stopped_pid": None,
        "launched_pid": None,
        "outcome": "failed",
    }
    try:
        # The candidate is already identity- and cleanliness-validated above.
        # Seal it before TERM so a failure cannot interrupt the incumbent, and
        # so workers can only read the exact command source after launch.
        result["command_runtime_seal"] = seal_command_runtime(Path(identity["root"]))
        # The provider boundary is enforced by worker_runner, not by mode bits
        # alone. Prove the host can create that namespace before TERM so a
        # missing/disabled bubblewrap cannot replace a healthy incumbent with
        # a supervisor that is unable to launch any worker safely.
        result["worker_sandbox_preflight"] = verify_worker_sandbox(
            Path(identity["root"])
        )
        # Workers require this split-root marker before their adapter starts.
        # Creating it is idempotent and happens before TERM, so a malformed
        # coordination root cannot turn a healthy incumbent into an outage.
        ensure_approval_queue_marker(approval_queue_path)
        stopped_pid = stop_existing_supervisor(
            incumbent_pid_path, timeout_seconds=termination_timeout
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
        # Best-effort and fully self-contained: sync_coordination_root_code
        # already catches its own errors, but this call site never lets an
        # unexpected exception from it reach the replacement's own outcome
        # or exit_code either, since the supervisor above already launched
        # successfully.
        try:
            result["coordination_code_sync"] = sync_coordination_root_code(
                Path(identity["root"]), status_root
            )
        except Exception as sync_exc:
            result["coordination_code_sync"] = {
                "outcome": "failed",
                "error": f"{type(sync_exc).__name__}: {sync_exc}",
            }
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["exit_code"] = 1
    _write_evidence(evidence_path, result)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Exact V2 command source root.")
    parser.add_argument("--status-root", required=True)
    parser.add_argument("--live-config", default=str(LIVE_SUPERVISOR_CONFIG_PATH))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--termination-timeout", type=float, default=15.0)
    parser.add_argument("--evidence-path")
    parser.add_argument(
        "--repository-source-root",
        action="append",
        default=[],
        metavar="REPOSITORY_ID=/ABSOLUTE/GIT/ROOT",
        help="Render an absolute source checkout into coordination.repositories.",
    )
    parser.add_argument(
        "--repository-integration-root",
        action="append",
        default=[],
        metavar="REPOSITORY_ID=/ABSOLUTE/GIT/ROOT",
        help="Render a dedicated clean merge checkout into coordination.repositories.",
    )
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
    status_root = _path(args.status_root)
    live_config_path = _path(args.live_config)
    python_executable = _path(args.python)
    try:
        repository_source_roots = parse_repository_source_roots(
            args.repository_source_root
        )
        repository_integration_roots = parse_repository_integration_roots(
            args.repository_integration_root
        )
        if not python_executable.is_file():
            raise ValueError(f"python executable does not exist: {python_executable}")
        validated_root(status_root, label="status root", required=(".git", "ai-status.json"))
        if args.discover_only:
            rendered, identity = render_v2_config(
                repo_root,
                status_root=status_root,
                live_config_path=live_config_path,
                python_executable=python_executable,
                repository_source_roots=repository_source_roots,
                repository_integration_roots=repository_integration_roots,
            )
            result: dict[str, Any] = {
                "schema_version": 2,
                "kind": "supervisor_v2_replacement_preflight",
                "outcome": "ready",
                "candidate": identity,
                "live_config": str(live_config_path),
                "task_state_store": dict(rendered["task_state_store"]),
                "supervisor_command": rendered["watchdog"]["supervisor_command"],
                "repository_source_roots": {
                    repository_id: str(entry.get("local_path"))
                    for repository_id, entry in (
                        (rendered.get("coordination") or {}).get("repositories") or {}
                    ).items()
                    if isinstance(entry, dict) and entry.get("local_path")
                },
                "repository_integration_roots": {
                    repository_id: str(entry.get("integration_path"))
                    for repository_id, entry in (
                        (rendered.get("coordination") or {}).get("repositories") or {}
                    ).items()
                    if isinstance(entry, dict) and entry.get("integration_path")
                },
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
                repository_source_roots=repository_source_roots,
                repository_integration_roots=repository_integration_roots,
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
