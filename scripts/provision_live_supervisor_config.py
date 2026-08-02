#!/usr/bin/env python3
"""Render the split-root supervisor config used by the Pantheon dev VM.

The supervisor code runs from an immutable deployment worktree while all
coordination state remains in the canonical Pantheon checkout mounted into the
BFF. Relative config paths would otherwise resolve under the command checkout
and create a second task/status universe.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

import promote_supervisor_runtime as runtime_promotion


LEGACY_PROVIDER_ACCOUNT_KEYS = ("account_group", "quota_group", "dispatch_group")
WATCHDOG_RUNTIME_PATH_DEFAULTS = {
    "state_file": ".orchestrator/watchdog-state.json",
    "metrics_file": ".orchestrator/metrics/supervisor-watchdog.jsonl",
    "contention_metrics_file": ".orchestrator/metrics/supervisor-watchdog-contention.jsonl",
}
REPO_OWNED_SUPERVISOR_LEASE_POLICY = {
    "supervisor": (
        "observe_worker_commit_progress",
        "lease_requires_work_progress",
    ),
    "worker_runtime": (
        "worker_lease_seconds",
        "work_progress_stale_seconds",
    ),
}
REPO_OWNED_READY_DISPATCHER_POLICY = (
    "enabled",
    "disabled_agents",
    "sidecar_only_agents",
    "target_workload",
    "max_tasks_per_agent_by_agent",
    "max_dispatches_per_tick",
    "max_active_workers_per_task",
    "max_concurrent_per_account",
    "max_concurrent_workers",
    "require_explicit_provider_accounts",
    "allow_legacy_provider_account_aliases",
)
REPO_OWNED_COORDINATION_POLICY = (
    "enabled",
)
TASK_STATE_STORE_DEFAULT_FILENAME = "task-state-events.jsonl"


def load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"config must contain a JSON object: {path}")
    return payload


def deep_merge(base: Any, overlay: Any) -> Any:
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = copy.deepcopy(base)
        for key, value in overlay.items():
            merged[key] = deep_merge(merged[key], value) if key in merged else copy.deepcopy(value)
        return merged
    return copy.deepcopy(overlay)


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


def apply_provider_account_schema(
    repo_config: dict[str, Any], rendered: dict[str, Any]
) -> None:
    """Make repo-owned account identity fields win over stale live overlays."""
    repo_ready = repo_config.get("ready_dispatcher")
    if repo_ready is None:
        return
    rendered_ready = rendered.setdefault("ready_dispatcher", {})
    if not isinstance(repo_ready, dict) or not isinstance(rendered_ready, dict):
        raise ValueError("ready_dispatcher config must be a JSON object")

    for key in (
        "require_explicit_provider_accounts",
        "allow_legacy_provider_account_aliases",
        "max_concurrent_per_account",
    ):
        if key in repo_ready:
            rendered_ready[key] = copy.deepcopy(repo_ready[key])

    allow_legacy = bool(rendered_ready.get("allow_legacy_provider_account_aliases", True))
    if not allow_legacy:
        rendered_ready.pop("max_concurrent_per_quota_group", None)

    repo_providers = repo_config.get("providers") or {}
    rendered_providers = rendered.get("providers") or {}
    if not isinstance(repo_providers, dict) or not isinstance(rendered_providers, dict):
        raise ValueError("providers config must be a JSON object")

    missing_accounts: list[str] = []
    require_explicit = bool(rendered_ready.get("require_explicit_provider_accounts", False))
    for provider, provider_cfg in rendered_providers.items():
        if not isinstance(provider_cfg, dict):
            raise ValueError(f"providers.{provider} must be a JSON object")
        repo_provider_cfg = repo_providers.get(provider)
        if isinstance(repo_provider_cfg, dict) and repo_provider_cfg.get("account"):
            provider_cfg["account"] = copy.deepcopy(repo_provider_cfg["account"])
        if not allow_legacy:
            for key in LEGACY_PROVIDER_ACCOUNT_KEYS:
                provider_cfg.pop(key, None)
        if require_explicit and not str(provider_cfg.get("account") or "").strip():
            missing_accounts.append(str(provider))

    if missing_accounts:
        raise ValueError(
            "strict provider account migration left providers without account: "
            + ", ".join(sorted(missing_accounts))
        )


def apply_ready_dispatcher_policy(
    repo_config: dict[str, Any], rendered: dict[str, Any]
) -> None:
    """Keep fleet enablement and capacity aligned with the reviewed repo policy.

    The split-root live config may carry environment-specific paths and
    credentials, but a stale overlay must not silently disable all healthy
    worker lanes after a supervisor restart. Emergency capacity changes belong
    in the reviewed repo policy so provisioning, drift repair, and the running
    supervisor converge on the same frontier.
    """

    repo_ready = repo_config.get("ready_dispatcher")
    if repo_ready is None:
        return
    rendered_ready = rendered.setdefault("ready_dispatcher", {})
    if not isinstance(repo_ready, dict) or not isinstance(rendered_ready, dict):
        raise ValueError("ready_dispatcher config must be a JSON object")
    for key in REPO_OWNED_READY_DISPATCHER_POLICY:
        if key in repo_ready:
            rendered_ready[key] = copy.deepcopy(repo_ready[key])


def apply_supervisor_lease_policy(
    repo_config: dict[str, Any], rendered: dict[str, Any]
) -> None:
    """Make safety-critical worker lease policy win over stale live overlays."""
    for section_name, keys in REPO_OWNED_SUPERVISOR_LEASE_POLICY.items():
        repo_section = repo_config.get(section_name)
        if repo_section is None:
            continue
        rendered_section = rendered.setdefault(section_name, {})
        if not isinstance(repo_section, dict) or not isinstance(rendered_section, dict):
            raise ValueError(f"{section_name} config must be a JSON object")
        for key in keys:
            if key in repo_section:
                rendered_section[key] = copy.deepcopy(repo_section[key])


def apply_coordination_policy(
    repo_config: dict[str, Any], rendered: dict[str, Any]
) -> None:
    """Make the reviewed coordination publisher policy win over stale live overlays."""

    repo_coordination = repo_config.get("coordination")
    if repo_coordination is None:
        return
    rendered_coordination = rendered.setdefault("coordination", {})
    if not isinstance(repo_coordination, dict) or not isinstance(rendered_coordination, dict):
        raise ValueError("coordination config must be a JSON object")
    for key in REPO_OWNED_COORDINATION_POLICY:
        if key in repo_coordination:
            rendered_coordination[key] = copy.deepcopy(repo_coordination[key])


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
    if mode not in {"off", "shadow", "authoritative"}:
        raise ValueError("task_state_store.mode must be 'off', 'shadow', or 'authoritative'")
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
) -> dict[str, Any]:
    rendered = deep_merge(repo_config, existing_live_config or {})
    apply_provider_account_schema(repo_config, rendered)
    apply_ready_dispatcher_policy(repo_config, rendered)
    apply_supervisor_lease_policy(repo_config, rendered)
    apply_coordination_policy(repo_config, rendered)
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
    """Bind provisioning to the promotion layer's immutable runtime identity.

    Provisioning is intentionally not a second, weaker admission path.  The
    command root must pass the same direct-child, no-follow Git, trusted remote,
    accepted-dev ancestry, tree identity, and cleanliness checks used by the
    transactional promotion operator.
    """

    root = runtime_promotion.resolve_candidate_root(path)
    remote_url = runtime_promotion.parse_origin_url(root)
    remote = runtime_promotion.validate_remote_url(remote_url)
    head = runtime_promotion.verify_git_head_and_dev_ancestry(root, root.name)
    tree = runtime_promotion.verify_working_tree_cleanliness(
        root,
        expected_head=head,
    )
    for relative in (
        ".orchestrator/supervisor.py",
        "scripts/run-supervisor-watchdog.sh",
        "scripts/promote-supervisor-runtime.sh",
    ):
        candidate = root / relative
        symlink = first_symlink_component(candidate)
        if symlink is not None or not candidate.is_file():
            raise ValueError(
                f"immutable command root is missing regular non-symlink path "
                f"{relative}: {candidate}"
            )
    return {
        "root": str(root),
        "head": head,
        "tree": tree,
        "remote": remote_url,
        "repository": remote.slug,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-config")
    parser.add_argument("--live-config")
    parser.add_argument("--command-root", required=True)
    parser.add_argument("--status-root")
    parser.add_argument("--python", default=sys.executable)
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
