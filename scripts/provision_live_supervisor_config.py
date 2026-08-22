#!/usr/bin/env python3
"""Render the split-root supervisor config used by the Pantheon dev VM.

The supervisor code runs from an immutable deployment worktree while all
control-plane state remains in the canonical Pantheon checkout mounted into the
BFF. Relative config paths would otherwise resolve under the command checkout
and create a second task/status universe.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

# This command is executed from the immutable candidate before its cleanliness
# gate runs.  Importing the promotion verifier must not create executable
# ``scripts/__pycache__`` debris in the candidate that the verifier then
# rejects.  The command is short-lived, so keep bytecode writes disabled for
# its entire lifetime, including imports performed by the verifier.
sys.dont_write_bytecode = True


WATCHDOG_RUNTIME_PATH_DEFAULTS = {
    "state_file": ".orchestrator/watchdog-state.json",
    "metrics_file": ".orchestrator/metrics/supervisor-watchdog.jsonl",
    "contention_metrics_file": ".orchestrator/metrics/supervisor-watchdog-contention.jsonl",
}
TASK_STATE_STORE_DEFAULT_FILENAME = "task-state-events.jsonl"


def load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"config must contain a JSON object: {path}")
    return payload


def first_symlink_component(path: Path) -> Path | None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            if current.is_symlink():
                return current
        except OSError:
            return current
    return None


def canonical_status_paths(repo_config: dict[str, Any], status_root: Path) -> dict[str, str]:
    paths = repo_config.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise ValueError("repo config must define a non-empty paths object")

    rendered: dict[str, str] = {}
    for key, raw_value in paths.items():
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise ValueError(f"repo config path {key!r} must be a non-empty string")
        source = Path(os.path.expanduser(raw_value))
        candidate = source if source.is_absolute() else status_root / source
        candidate = candidate.absolute()
        symlink = first_symlink_component(candidate)
        if symlink is not None:
            raise ValueError(f"repo config path {key!r} contains a symlink component: {symlink}")
        candidate = candidate.resolve()
        try:
            candidate.relative_to(status_root)
        except ValueError as exc:
            raise ValueError(
                f"repo config path {key!r} escapes canonical status root: {candidate}"
            ) from exc
        rendered[key] = str(candidate)

    expected_status_file = status_root / "ai-status.json"
    if Path(rendered.get("status_file", "")) != expected_status_file:
        raise ValueError(
            "live supervisor status_file must resolve to the canonical status root: "
            f"expected {expected_status_file}, got {rendered.get('status_file')!r}"
        )
    return rendered


def canonical_watchdog_runtime_paths(
    watchdog: dict[str, Any],
    status_root: Path,
) -> dict[str, str]:
    """Pin watchdog-owned state to the canonical split-root checkout."""
    rendered: dict[str, str] = {}
    for key, default in WATCHDOG_RUNTIME_PATH_DEFAULTS.items():
        raw_value = watchdog.get(key) or default
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise ValueError(f"watchdog path {key!r} must be a non-empty string")
        source = Path(os.path.expanduser(raw_value))
        candidate = source if source.is_absolute() else status_root / source
        candidate = candidate.absolute()
        symlink = first_symlink_component(candidate)
        if symlink is not None:
            raise ValueError(f"watchdog path {key!r} contains a symlink component: {symlink}")
        candidate = candidate.resolve()
        try:
            candidate.relative_to(status_root)
        except ValueError as exc:
            raise ValueError(
                f"watchdog path {key!r} escapes canonical status root: {candidate}"
            ) from exc
        rendered[key] = str(candidate)
    return rendered


def parse_repository_source_roots(values: list[str] | None) -> dict[str, Path]:
    """Parse repeatable ``repository-id=/absolute/git/root`` CLI values."""

    roots: dict[str, Path] = {}
    for raw_value in values or []:
        repository_id, separator, raw_path = str(raw_value).partition("=")
        repository_id = repository_id.strip()
        raw_path = raw_path.strip()
        if (
            not separator
            or not re.fullmatch(r"[a-z][a-z0-9_]*", repository_id)
            or not raw_path
        ):
            raise ValueError(
                "repository source root must use repository_id=/absolute/git/root"
            )
        candidate = Path(os.path.expanduser(raw_path))
        if not candidate.is_absolute():
            raise ValueError(
                f"repository source root for {repository_id} must be absolute: {raw_path}"
            )
        roots[repository_id] = candidate
    return roots


def _validated_repository_source_root(repository_id: str, raw_root: Path) -> Path:
    source_root = raw_root.expanduser().absolute()
    symlink = first_symlink_component(source_root)
    if symlink is not None:
        raise ValueError(
            f"repository source root for {repository_id} contains a symlink component: {symlink}"
        )
    source_root = source_root.resolve()
    if not source_root.is_dir():
        raise ValueError(
            f"repository source root for {repository_id} is not a directory: {source_root}"
        )
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=source_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or Path(proc.stdout.strip()).resolve() != source_root:
        raise ValueError(
            f"repository source root for {repository_id} is not a Git checkout: {source_root}"
        )
    return source_root


def apply_repository_source_roots(
    rendered: dict[str, Any],
    repository_source_roots: Mapping[str, Path | str] | None,
) -> dict[str, str]:
    """Render deployment-owned repository roots into the one live registry.

    Repository paths are host topology, not source policy.  The candidate
    config retains portable logical registry entries; promotion materializes
    the absolute checkout which Worker Manager must use.  This prevents the
    coordination/status root from becoming an implicit Git source authority.
    """

    applied: dict[str, str] = {}
    if not repository_source_roots:
        return applied
    coordination = rendered.setdefault("coordination", {})
    if not isinstance(coordination, dict):
        raise ValueError("coordination config must be a JSON object")
    repository_config = coordination.setdefault("repositories", {})
    if not isinstance(repository_config, dict):
        raise ValueError("coordination.repositories must be a JSON object")
    for repository_id, raw_root in repository_source_roots.items():
        normalized_id = str(repository_id or "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", normalized_id):
            raise ValueError(f"invalid repository source id: {repository_id!r}")
        source_root = _validated_repository_source_root(
            normalized_id,
            Path(raw_root),
        )
        entry = repository_config.setdefault(normalized_id, {})
        if not isinstance(entry, dict):
            raise ValueError(
                f"coordination.repositories.{normalized_id} must be a JSON object"
            )
        entry["local_path"] = str(source_root)
        applied[normalized_id] = str(source_root)
    return applied


def validate_provider_accounts(config: Mapping[str, Any]) -> None:
    """Accept only explicit V2 provider account identities."""

    providers = config.get("providers")
    if not isinstance(providers, dict) or not providers:
        raise ValueError("providers config must be a non-empty JSON object")
    for provider, provider_cfg in providers.items():
        if not isinstance(provider_cfg, dict):
            raise ValueError(f"providers.{provider} must be a JSON object")
        for retired_key in ("account_group", "quota_group", "dispatch_group"):
            if retired_key in provider_cfg:
                raise ValueError(f"providers.{provider}.{retired_key} is retired; use account")
        if not str(provider_cfg.get("account") or "").strip():
            raise ValueError(f"providers.{provider}.account is required")


def apply_task_state_store(
    repo_config: dict[str, Any],
    rendered: dict[str, Any],
    *,
    command_root: Path,
    status_root: Path,
    live_config_path: Path,
) -> None:
    """Pin the task-state journal to the git-external live runtime directory."""
    repo_store = repo_config.get("task_state_store")
    if repo_store is None:
        return
    if not isinstance(repo_store, dict):
        raise ValueError("task_state_store config must be a JSON object")
    mode = str(repo_store.get("mode") or "").strip().lower()
    if mode != "authoritative":
        raise ValueError("task_state_store.mode must be 'authoritative'")
    raw_event_log = str(
        repo_store.get("event_log") or TASK_STATE_STORE_DEFAULT_FILENAME
    ).strip()
    filename = Path(os.path.expanduser(raw_event_log)).name
    if filename in {"", ".", ".."}:
        raise ValueError("task_state_store.event_log must name a file")

    runtime_parent = live_config_path.expanduser().absolute().parent
    parent_symlink = first_symlink_component(runtime_parent)
    if parent_symlink is not None:
        raise ValueError(
            f"task-state store runtime parent contains a symlink component: {parent_symlink}"
        )
    event_log_candidate = runtime_parent / filename
    event_symlink = first_symlink_component(event_log_candidate)
    if event_symlink is not None:
        raise ValueError(f"task-state event log contains a symlink component: {event_symlink}")
    event_log = event_log_candidate.resolve()
    if event_log == live_config_path.expanduser().resolve():
        raise ValueError("task-state event log cannot replace the live supervisor config")
    if event_log.exists() and not event_log.is_file():
        raise ValueError(f"task-state event log must be a regular file: {event_log}")
    for label, root in (("command", command_root), ("status", status_root)):
        resolved_root = root.expanduser().resolve()
        if event_log == resolved_root or resolved_root in event_log.parents:
            raise ValueError(
                f"task-state event log must remain outside the {label} git root: {event_log}"
            )

    rendered_store = rendered.setdefault("task_state_store", {})
    if not isinstance(rendered_store, dict):
        raise ValueError("task_state_store config must be a JSON object")
    rendered_store["mode"] = mode
    rendered_store["event_log"] = str(event_log)


def build_live_config(
    repo_config: dict[str, Any],
    *,
    existing_live_config: dict[str, Any] | None,
    command_root: Path,
    status_root: Path,
    live_config_path: Path,
    python_executable: Path,
    repository_source_roots: Mapping[str, Path | str] | None = None,
) -> dict[str, Any]:
    # The live file is a deployment projection, never a policy overlay.  In
    # particular, carrying keys that are merely absent from the candidate
    # config forward from an incumbent reintroduces retired dispatch paths and
    # can make a new supervisor fail its own schema validation at startup.
    #
    # Runtime-specific values are derived below (canonical status paths,
    # task-store location, watchdog paths, and immutable command argv).  They
    # do not need, and must not use, the previous live policy as a fallback.
    # Keep the argument for the public renderer API and callers that capture
    # the incumbent for promotion evidence, but deliberately do not merge it.
    del existing_live_config
    rendered = copy.deepcopy(repo_config)
    validate_provider_accounts(rendered)
    apply_repository_source_roots(rendered, repository_source_roots)
    apply_task_state_store(
        repo_config,
        rendered,
        command_root=command_root,
        status_root=status_root,
        live_config_path=live_config_path,
    )
    rendered["paths"] = canonical_status_paths(repo_config, status_root)

    watchdog = rendered.setdefault("watchdog", {})
    if not isinstance(watchdog, dict):
        raise ValueError("watchdog config must be a JSON object")
    watchdog.update(canonical_watchdog_runtime_paths(watchdog, status_root))
    watchdog["supervisor_command"] = [
        str(python_executable),
        "-u",
        "-B",
        str(command_root / ".orchestrator" / "supervisor.py"),
        "--config",
        str(live_config_path),
        "--verbose",
    ]
    return rendered


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise ValueError(f"refusing to replace symlink live config: {path}")
    symlink = first_symlink_component(path.parent)
    if symlink is not None:
        raise ValueError(f"live config parent contains a symlink component: {symlink}")

    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), stat.S_IRUSR | stat.S_IWUSR)
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary_path.unlink(missing_ok=True)


def validate_approval_queue_marker(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"approval queue marker must be a regular non-symlink file: {path}")
    payload = load_json_object(path)
    if payload.get("version") != 2:
        raise ValueError(f"approval queue marker must use version 2: {path}")
    if not isinstance(payload.get("pending"), list):
        raise ValueError(f"approval queue marker pending must be a list: {path}")
    if not isinstance(payload.get("history"), list):
        raise ValueError(f"approval queue marker history must be a list: {path}")
    if any(not isinstance(item, dict) for item in payload["pending"]):
        raise ValueError(f"approval queue marker pending items must be objects: {path}")
    if any(not isinstance(item, dict) for item in payload["history"]):
        raise ValueError(f"approval queue marker history items must be objects: {path}")
    return payload


def ensure_approval_queue_marker(path: Path) -> bool:
    """Create the split-root worker marker once without replacing live approvals."""

    path = path.expanduser().absolute()
    parent_symlink = first_symlink_component(path.parent)
    if parent_symlink is not None:
        raise ValueError(f"approval queue marker parent contains a symlink component: {parent_symlink}")
    if not path.parent.is_dir():
        raise ValueError(f"approval queue marker parent is not a directory: {path.parent}")

    if path.exists() or path.is_symlink():
        validate_approval_queue_marker(path)
        return False

    payload = {
        "version": 2,
        "updated_at": None,
        "pending": [],
        "history": [],
    }
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), stat.S_IRUSR | stat.S_IWUSR)
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            # Another bootstrap may have won the race. Preserve and validate it.
            validate_approval_queue_marker(path)
            return False
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return True
    finally:
        temporary_path.unlink(missing_ok=True)


def validated_root(path: Path, *, label: str, required: tuple[str, ...]) -> Path:
    expanded = path.expanduser().absolute()
    symlink = first_symlink_component(expanded)
    if symlink is not None:
        raise ValueError(f"{label} contains a symlink component: {symlink}")
    resolved = expanded.resolve()
    if not resolved.is_dir():
        raise ValueError(f"{label} is not a directory: {resolved}")
    for relative in required:
        candidate = resolved / relative
        if not candidate.exists() or candidate.is_symlink():
            raise ValueError(f"{label} is missing regular path {relative}: {candidate}")
    return resolved


def validated_immutable_command_root(path: Path) -> dict[str, str]:
    """Validate the one V2 command tree used to launch the supervisor.

    This is deliberately local and small: a V2 replacement only needs an
    exact, clean Git source tree containing the launch entry points.  It does
    not inspect or reconstruct a retired runtime in order to promote it.
    """

    root = validated_root(
        path,
        label="immutable command root",
        required=(".git", ".orchestrator/config.json", ".orchestrator/supervisor.py"),
    )
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        remote_url = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise ValueError("immutable command root has unusable Git metadata") from exc
    if len(head) != 40 or any(character not in "0123456789abcdef" for character in head):
        raise ValueError("immutable command root has invalid HEAD")
    if not remote_url:
        raise ValueError("immutable command root has no origin remote")
    if dirty:
        raise ValueError("immutable command root must be clean")
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    for relative, require_executable in (
        (".orchestrator/supervisor.py", False),
        ("scripts/run-supervisor-watchdog.sh", True),
        ("scripts/promote-supervisor-runtime.sh", True),
    ):
        candidate = root / relative
        symlink = first_symlink_component(candidate)
        if symlink is not None or not candidate.is_file():
            raise ValueError(
                f"immutable command root is missing regular non-symlink path "
                f"{relative}: {candidate}"
            )
        if require_executable and not candidate.stat().st_mode & stat.S_IXUSR:
            raise ValueError(f"immutable command root path is not executable: {candidate}")
    return {
        "root": str(root),
        "head": head,
        "tree": tree,
        "remote": remote_url,
        "repository": remote_url,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-config")
    parser.add_argument("--live-config")
    parser.add_argument("--command-root", required=True)
    parser.add_argument("--status-root")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--repository-source-root",
        action="append",
        default=[],
        metavar="REPOSITORY_ID=/ABSOLUTE/GIT/ROOT",
        help="Render one deployment-owned repository source root into coordination.repositories.",
    )
    parser.add_argument(
        "--validate-command-root-only",
        action="store_true",
        help="Validate immutable command runtime identity without writing state.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.validate_command_root_only:
        for option in ("repo_config", "live_config", "status_root"):
            if not getattr(args, option):
                parser.error(f"--{option.replace('_', '-')} is required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        command_identity = validated_immutable_command_root(Path(args.command_root))
        command_root = Path(command_identity["root"])
        if args.validate_command_root_only:
            if args.json:
                print(json.dumps(command_identity, indent=2, sort_keys=True))
            else:
                print(
                    "validated immutable supervisor command root: "
                    f"root={command_root} head={command_identity['head']}"
                )
            return 0

        status_root = validated_root(
            Path(args.status_root),
            label="status root",
            required=(".git", "ai-status.json"),
        )
        if command_root == status_root:
            raise ValueError("split-root dev supervisor requires distinct command and status roots")

        repo_config_path = Path(args.repo_config).expanduser().absolute()
        live_config_path = Path(args.live_config).expanduser().absolute()
        repo_config_symlink = first_symlink_component(repo_config_path)
        if repo_config_symlink is not None or not repo_config_path.is_file():
            raise ValueError(f"repo config must be a regular non-symlink file: {repo_config_path}")
        repo_config_path = repo_config_path.resolve()

        repo_config = load_json_object(repo_config_path)
        existing = None
        if live_config_path.exists():
            if live_config_path.is_symlink() or not live_config_path.is_file():
                raise ValueError(f"live config must be a regular non-symlink file: {live_config_path}")
            existing = load_json_object(live_config_path)

        python_executable = Path(args.python).expanduser().resolve()
        if not python_executable.is_file():
            raise ValueError(f"python executable does not exist: {python_executable}")
        rendered = build_live_config(
            repo_config,
            existing_live_config=existing,
            command_root=command_root,
            status_root=status_root,
            live_config_path=live_config_path,
            python_executable=python_executable,
            repository_source_roots=parse_repository_source_roots(
                args.repository_source_root
            ),
        )
        if existing is not None and rendered != existing:
            raise ValueError(
                "existing live supervisor config differs from the admitted immutable "
                "runtime; use the governed promotion transaction instead of "
                "prewriting config"
            )
        approval_queue_value = rendered["paths"].get("approval_queue")
        if not isinstance(approval_queue_value, str) or not approval_queue_value.strip():
            raise ValueError("repo config must define paths.approval_queue for split-root workers")
        approval_queue_path = Path(approval_queue_value)
        approval_queue_created = ensure_approval_queue_marker(approval_queue_path)
        config_created = existing is None
        if config_created:
            write_json_atomic(live_config_path, rendered)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"live supervisor config provisioning failed: {exc}", file=sys.stderr)
        return 2

    summary = {
        "command_root": str(command_root),
        "status_root": str(status_root),
        "live_config": str(live_config_path),
        "approval_queue": str(approval_queue_path),
        "approval_queue_created": approval_queue_created,
        "config_created": config_created,
        "command_runtime": command_identity,
        "supervisor_command": rendered["watchdog"]["supervisor_command"],
        "repository_source_roots": {
            repository_id: str(entry.get("local_path"))
            for repository_id, entry in (
                (rendered.get("coordination") or {}).get("repositories") or {}
            ).items()
            if isinstance(entry, dict) and entry.get("local_path")
        },
    }
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            "provisioned live supervisor config: "
            f"command_root={command_root} status_root={status_root} "
            f"config={live_config_path} config_created={str(config_created).lower()}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
