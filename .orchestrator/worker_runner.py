#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Runtime validation must not create the bytecode it is about to inspect.
# The supervisor also exports PYTHONDONTWRITEBYTECODE, but this executable is a
# security boundary and must preserve the contract when invoked directly.
sys.dont_write_bytecode = True

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from common import (  # noqa: E402 - worker_runner must bootstrap its sibling module
    STATUS_COMMAND_BASE_REF_ENV,
    STATUS_COMMAND_REMOTE_ENV,
    STATUS_COMMAND_ROOT_ENV,
    STATUS_COMMAND_SHA_ENV,
    first_symlink_component as _first_symlink_component,
    git_toplevel as _git_toplevel,
    validate_status_command_runtime as _validate_status_command_runtime,
)


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


def _command_env(name: str, default: str = "") -> str:
    return str(os.environ.get(name) or default).strip()


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


def _ensure_governed_lock_file(path: Path, *, label: str) -> None:
    """Create one regular coordination lock before the worker mount is sealed."""

    symlink_comp = _first_symlink_component(path)
    if symlink_comp is not None or path.is_symlink():
        raise RuntimeError(
            f"PANTHEON_STATUS_ROOT {label} cannot contain a symlink: "
            f"{symlink_comp or path}"
        )
    descriptor = os.open(
        path,
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError(
                f"PANTHEON_STATUS_ROOT {label} must be a regular file: {path}"
            )
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


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

    _ensure_governed_lock_file(
        root / ".orchestrator" / "status-derived-views.lock",
        label="derived status views lock",
    )

    for path in (
        root / "ai-activity-log.jsonl",
        root / "current-work.md",
        root / "dashboard-bundle.json",
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
        root / ".orchestrator" / "status-derived-views.lock",
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
    expected_sha = _command_env(STATUS_COMMAND_SHA_ENV)
    if not expected_sha:
        raise RuntimeError("PANTHEON_COMMAND_RUNTIME_SHA is required when worker_runner launches an auto worker")
    expected_remote = _command_env(STATUS_COMMAND_REMOTE_ENV, "ajoe734/pantheon")
    base_ref = _command_env(STATUS_COMMAND_BASE_REF_ENV, "origin/dev") or "origin/dev"
    runtime = _validate_status_command_runtime(
        expanded,
        expected_sha=expected_sha,
        expected_remote=expected_remote,
        base_ref=base_ref,
    )
    return {
        "command_root": runtime["root"],
        "source_sha": runtime["source_sha"],
        "remote": runtime["remote"],
        "base_ref": runtime["base_ref"],
    }


def _append_leased_git_metadata_mounts(
    bwrap_cmd: list[str], workspace: Path
) -> None:
    """Make only the selected linked worktree's Git control surface usable.

    A linked worktree keeps its index/HEAD under the source repository's
    ``.git/worktrees`` directory and shares objects/refs with that repository.
    Binding only the delivery directory therefore makes source files writable
    but leaves every real Git mutation read-only.  Mount the selected common
    Git directory writable, then overlay the source checkout's control files,
    every other local branch, tags, and every other linked-worktree directory
    read-only before reopening only this worktree's metadata directory.
    """

    dot_git = workspace / ".git"
    if dot_git.is_symlink():
        raise RuntimeError(f"leased-worktree .git cannot be a symlink: {dot_git}")
    if dot_git.is_dir():
        # Standalone repositories keep all Git state under the already writable
        # leased workspace and require no shared metadata exception.
        return
    if not dot_git.is_file():
        raise RuntimeError(f"leased workspace has no regular .git marker: {dot_git}")
    marker = dot_git.read_text(encoding="utf-8").strip()
    prefix = "gitdir:"
    if not marker.lower().startswith(prefix):
        raise RuntimeError(f"leased-worktree .git marker is invalid: {dot_git}")
    git_dir = Path(marker[len(prefix) :].strip())
    if not git_dir.is_absolute():
        git_dir = workspace / git_dir
    symlink = _first_symlink_component(git_dir)
    if symlink is not None:
        raise RuntimeError(f"leased-worktree git metadata contains a symlink: {symlink}")
    git_dir = git_dir.resolve()
    commondir_file = git_dir / "commondir"
    if commondir_file.is_symlink() or not commondir_file.is_file():
        raise RuntimeError(f"leased-worktree commondir is unavailable: {commondir_file}")
    common_dir = Path(commondir_file.read_text(encoding="utf-8").strip())
    if not common_dir.is_absolute():
        common_dir = git_dir / common_dir
    common_dir = common_dir.resolve()
    symlink = _first_symlink_component(common_dir)
    if symlink is not None:
        raise RuntimeError(f"leased-worktree common git metadata contains a symlink: {symlink}")
    try:
        git_dir.relative_to(common_dir / "worktrees")
    except ValueError as exc:
        raise RuntimeError(
            "leased workspace gitdir is not task-scoped under the selected common git dir"
        ) from exc

    head_file = git_dir / "HEAD"
    if head_file.is_symlink() or not head_file.is_file():
        raise RuntimeError(f"leased-worktree HEAD is unavailable: {head_file}")
    head_marker = head_file.read_text(encoding="utf-8").strip()
    branch_ref = head_marker[4:].strip() if head_marker.startswith("ref:") else ""
    if not branch_ref.startswith("refs/heads/task/"):
        raise RuntimeError(
            "leased linked worktree must be attached to a task/* branch before launch"
        )

    bwrap_cmd.extend(["--bind", str(common_dir), str(common_dir)])

    protected: list[Path] = []
    for relative in (
        "HEAD",
        "index",
        "config",
        "config.worktree",
        "hooks",
        "info",
        "refs/tags",
        "logs/HEAD",
        "worktrees",
    ):
        candidate = common_dir / relative
        if candidate.exists():
            protected.append(candidate)
    heads_root = common_dir / "refs" / "heads"
    if heads_root.is_dir():
        for candidate in heads_root.rglob("*"):
            if not candidate.is_file():
                continue
            relative_ref = candidate.relative_to(common_dir).as_posix()
            if relative_ref != branch_ref:
                protected.append(candidate)

    # Parent protections are installed before the selected nested gitdir is
    # reopened.  Bubblewrap applies later mounts on top of earlier mounts.
    for candidate in sorted(set(protected), key=lambda item: (len(item.parts), str(item))):
        bwrap_cmd.extend(["--ro-bind", str(candidate), str(candidate)])
    bwrap_cmd.extend(["--bind", str(git_dir), str(git_dir)])


def _append_task_store_mounts(bwrap_cmd: list[str], raw_event_log: str) -> None:
    """Expose the atomic TaskStore surface without exposing sibling runtime data."""

    expanded_event_path = Path(os.path.expanduser(raw_event_log))
    if not expanded_event_path.is_absolute():
        raise RuntimeError("PANTHEON_TASK_STATE_EVENT_LOG must be absolute")
    event_path = expanded_event_path.absolute()
    parent = event_path.parent
    symlink = _first_symlink_component(parent)
    if symlink is not None or parent.is_symlink():
        raise RuntimeError(
            f"task-state store parent cannot contain a symlink: {symlink or parent}"
        )
    if not parent.is_dir() or not event_path.is_file():
        raise RuntimeError(f"task-state store is unavailable: {event_path}")

    allowed_names = {
        event_path.name,
        f"{event_path.name}.head.json",
        f"{event_path.name}.lock",
        f"{event_path.name}.legacy-anchor.json",
    }
    for required in (
        event_path,
        parent / f"{event_path.name}.head.json",
        parent / f"{event_path.name}.lock",
    ):
        if required.is_symlink() or not required.is_file():
            raise RuntimeError(f"task-state governed file is unavailable: {required}")

    # The V2 store replaces its head through a same-directory temporary file,
    # so binding individual files is insufficient.  Bind the parent writable,
    # then remount every non-TaskStore sibling read-only.  Existing protected
    # mountpoints cannot be replaced or unlinked from inside the namespace.
    bwrap_cmd.extend(["--bind", str(parent), str(parent)])
    for sibling in sorted(parent.iterdir(), key=lambda item: item.name):
        if sibling.name in allowed_names:
            continue
        if sibling.is_symlink():
            raise RuntimeError(f"runtime sibling cannot be a symlink: {sibling}")
        bwrap_cmd.extend(["--ro-bind", str(sibling), str(sibling)])


def _append_coordination_state_mounts(
    bwrap_cmd: list[str], coordination_root: Path
) -> None:
    """Expose atomic status projections while keeping repository source read-only."""

    root = coordination_root.absolute()
    symlink = _first_symlink_component(root)
    if symlink is not None or root.is_symlink():
        raise RuntimeError(
            f"coordination root cannot contain a symlink: {symlink or root}"
        )
    if not root.is_dir():
        raise RuntimeError(f"coordination root is unavailable: {root}")

    # ai_status.save_state() deliberately writes a same-directory temporary
    # file and atomically replaces ai-status.json.  Binding only the existing
    # file makes that rename fail with EROFS.  Reopen the stable parent, then
    # cover every existing non-governed sibling with a read-only mount.  The
    # remaining writable top-level names are projection/audit surfaces only;
    # source, Git metadata, scripts, services, and all other repository content
    # remain protected mountpoints.
    writable_top_level_names = {
        "ai-status.json",
        "ai-activity-log.jsonl",
        "current-work.md",
        "dashboard-bundle.json",
        "docs-site",
        "ai-task-archive",
    }
    bwrap_cmd.extend(["--bind", str(root), str(root)])
    for sibling in sorted(root.iterdir(), key=lambda item: item.name):
        if sibling.name in writable_top_level_names:
            continue
        if sibling.is_symlink():
            raise RuntimeError(f"coordination sibling cannot be a symlink: {sibling}")
        bwrap_cmd.extend(["--ro-bind", str(sibling), str(sibling)])


def bind_worker_sandbox(
    command: list[str],
    command_root: Path,
    *,
    workspace_path: Path | None = None,
    coordination_root: Path | None = None,
    extra_readonly_roots: Iterable[Path | str] | None = None,
    sandbox_binary: str | None = None,
) -> list[str]:
    """Wrap one provider command in a mount namespace with a leased-worktree write boundary.

    Enforces:
    - Default host root filesystem is strictly read-only (--ro-bind / /).
    - Exactly one leased delivery worktree (workspace_path) and designated
      governance state interfaces in coordination_root are writable.
    - Standard temporary directories (/tmp, /var/tmp, /run/user/<uid>) and user tool
      cache/config directories (~/.cache, ~/.config, ~/.gemini, etc.) are writable
      so CLI providers and development tools can execute.
    - Other worker worktrees under /tmp/pantheon-worker-worktrees, all shared source
      repositories, the command runtime, and coordination-root source code/git remain
      strictly read-only.
    - Outer worker_runner remains outside the namespace to publish heartbeats.
    - Private PID namespace and procfs isolate the process tree.
    """

    binary = sandbox_binary or os.environ.get("PANTHEON_SANDBOX_BINARY") or shutil.which("bwrap")
    if not binary:
        raise RuntimeError(
            "bubblewrap (bwrap) is required to enforce the read-only "
            "PANTHEON_COMMAND_ROOT worker boundary"
        )
    root = Path(command_root).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"validated command runtime is not a directory: {root}")

    ws_resolved = Path(workspace_path).expanduser().resolve() if workspace_path else None
    coord_resolved = Path(coordination_root).expanduser().resolve() if coordination_root else None

    # 1. Base command: isolate pid namespace, root read-only by default, dev bind
    bwrap_cmd = [
        str(Path(binary).expanduser().resolve()),
        "--die-with-parent",
        "--unshare-pid",
        "--ro-bind",
        "/",
        "/",
        "--dev-bind",
        "/dev",
        "/dev",
    ]

    # 2. Mount standard temporary directories writable
    for tmp_dir in ("/tmp", "/var/tmp"):
        p = Path(tmp_dir)
        if p.exists() and p.is_dir():
            bwrap_cmd.extend(["--bind-try", str(p), str(p)])

    try:
        uid = os.getuid()
        user_run = Path(f"/run/user/{uid}")
        if user_run.exists() and user_run.is_dir():
            bwrap_cmd.extend(["--bind-try", str(user_run), str(user_run)])
    except (AttributeError, OSError):
        pass

    # 3. Protect other worker worktrees under /tmp/pantheon-worker-worktrees
    worktrees_root = Path("/tmp/pantheon-worker-worktrees")
    if worktrees_root.exists() and worktrees_root.is_dir():
        bwrap_cmd.extend(["--ro-bind-try", str(worktrees_root.resolve()), str(worktrees_root.resolve())])

    # 4. User tool cache and configuration directories
    # Providers (agy, codex, claude, etc.) and compilers/tools write cache, auth tokens, logs to these dirs
    home = Path.home().resolve()
    candidate_user_dirs = [
        home / ".cache",
        home / ".config",
        home / ".local" / "state",
        home / ".local" / "share",
        home / ".local" / "cache",
        home / ".gemini",
        home / ".claude",
        home / ".codex",
        home / ".npm",
        home / ".cargo",
        home / ".rustup",
        home / ".antigravity",
    ]
    for env_var in ("ANTIGRAVITY_HOME", "CODEX_HOME", "CLAUDE_HOME", "CLAUDE_CONFIG_DIR", "GH_CONFIG_DIR"):
        val = os.environ.get(env_var)
        if val and val.strip():
            p = Path(os.path.expanduser(val.strip())).resolve()
            if p != home and p != Path("/"):
                candidate_user_dirs.append(p)

    for u_dir in candidate_user_dirs:
        try:
            if u_dir.exists():
                u_res = u_dir.resolve()
                if u_res != Path("/") and (coord_resolved is None or u_res != coord_resolved):
                    bwrap_cmd.extend(["--bind-try", str(u_res), str(u_res)])
        except OSError:
            pass

    # 5. Extra readonly roots if specified
    if extra_readonly_roots:
        for item in extra_readonly_roots:
            p = Path(os.path.expanduser(str(item))).resolve()
            if p.exists():
                bwrap_cmd.extend(["--ro-bind-try", str(p), str(p)])

    # 6. Leased delivery worktree is explicitly writable
    if ws_resolved:
        bwrap_cmd.extend(["--bind", str(ws_resolved), str(ws_resolved)])
        _append_leased_git_metadata_mounts(bwrap_cmd, ws_resolved)

    # 7. Governed coordination state interfaces
    if coord_resolved and (ws_resolved is None or coord_resolved != ws_resolved):
        _append_coordination_state_mounts(bwrap_cmd, coord_resolved)
        governed_candidates = [
            coord_resolved / ".orchestrator" / "state.json",
            coord_resolved / ".orchestrator" / "approval-queue.json",
            coord_resolved / ".orchestrator" / "runtime-admission.lock",
            coord_resolved / ".orchestrator" / "task-state.lock",
            coord_resolved / ".orchestrator" / "activity-audit.lock",
            coord_resolved / ".orchestrator" / "status-derived-views.lock",
            coord_resolved / ".orchestrator" / "worker-runtime",
            coord_resolved / "archive" / "logs",
            coord_resolved / ".orchestrator" / "logs",
        ]
        event_log = os.environ.get("PANTHEON_TASK_STATE_EVENT_LOG")
        if event_log and event_log.strip():
            _append_task_store_mounts(bwrap_cmd, event_log.strip())

        for p in governed_candidates:
            if p.exists():
                bwrap_cmd.extend(["--bind", str(p.resolve()), str(p.resolve())])

    # 8. Procfs and user command
    bwrap_cmd.extend([
        "--proc",
        "/proc",
        "--",
        *command,
    ])
    return bwrap_cmd


def bind_command_runtime_readonly(
    command: list[str],
    command_root: Path,
    *,
    sandbox_binary: str | None = None,
) -> list[str]:
    return bind_worker_sandbox(
        command,
        command_root=command_root,
        sandbox_binary=sandbox_binary,
    )


def bind_relative_command_to_runtime(command: list[str], command_root: Path) -> list[str]:
    """Bind path-based provider commands to the validated command runtime.

    The worker's delivery cwd may belong to a different repository.  A
    configured wrapper such as ``.orchestrator/bin/agy`` is command-runtime
    code, so resolving it after switching to the delivery cwd would either
    fail or execute an unrelated file from that repository.  Bare command
    names remain PATH-resolved and absolute commands retain their existing
    authority boundary.
    """

    if not command:
        raise RuntimeError("worker command cannot be empty")

    executable = str(command[0] or "").strip()
    if not executable:
        raise RuntimeError("worker command executable cannot be empty")

    configured_path = Path(os.path.expanduser(executable))
    has_path_separator = os.sep in executable or bool(os.altsep and os.altsep in executable)
    if configured_path.is_absolute() or not has_path_separator:
        return list(command)

    root = Path(command_root)
    if not root.is_absolute():
        raise RuntimeError(f"validated command root must be absolute: {root}")
    root = root.resolve()

    candidate = root / configured_path
    symlink_component = _first_symlink_component(candidate)
    if symlink_component is not None:
        raise RuntimeError(
            "relative worker command cannot include a symlink component: "
            f"{symlink_component}"
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(
            f"relative worker command is missing from command runtime: {candidate}"
        ) from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(
            f"relative worker command escapes command runtime: {candidate}"
        ) from exc
    if not resolved.is_file():
        raise RuntimeError(f"relative worker command is not a file: {resolved}")
    if not os.access(resolved, os.X_OK):
        raise RuntimeError(f"relative worker command is not executable: {resolved}")

    return [str(resolved), *command[1:]]


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
    command_root = Path(command_runtime["command_root"])
    if coordination_root is not None and command_root == coordination_root:
        raise RuntimeError(
            "PANTHEON_COMMAND_ROOT must be separate from PANTHEON_STATUS_ROOT "
            "so the provider runtime can be mounted read-only"
        )
    command = bind_relative_command_to_runtime(
        command,
        command_root,
    )
    sandboxed_command = bind_worker_sandbox(
        command,
        command_root=command_root,
        workspace_path=workspace_path if isinstance(workspace_path, Path) else None,
        coordination_root=coordination_root,
    )
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
            sandboxed_command,
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
