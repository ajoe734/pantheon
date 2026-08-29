#!/usr/bin/env python3
"""Delete command-runtimes/<SHA> checkouts that promotion never retires.

``sync-dev-root.sh`` materializes one new sealed checkout under
``command-runtimes/`` on every promotion and has never deleted one: the
promoter's own contract ("It never reconstructs a retired runtime or tries to
restore one") only ever covered *launch*, not disk retention. Left alone the
directory grows without bound.

This prunes command runtimes under ``--parent`` and versioned repository
integration clones under ``--integration-parent`` except:

  * the exact SHA the installed live config currently launches,
  * the newest ``--keep`` checkouts by materialization time,
  * any SHA a currently-active worker lease still has pinned as its
    ``status_command_runtime.command_root`` -- a worker started under an
    older promotion keeps running against that exact checkout for the rest
    of its lifetime, so an LRU-only policy could delete a runtime out from
    under a live task,
  * every ``coordination.repositories.*.integration_path`` referenced by the
    atomically installed live config.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path
from typing import Mapping

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
GIT_SCRIPTS_DIR = SCRIPTS_DIR / "git"
if str(GIT_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(GIT_SCRIPTS_DIR))

import auto_integrator  # noqa: E402  (shared stable integration lock)

DEFAULT_COMMAND_RUNTIME_PARENT = Path("/home/lupin/pantheon-ci-deploy/command-runtimes")
DEFAULT_INTEGRATION_RUNTIME_PARENT = Path(
    "/home/lupin/pantheon-ci-deploy/integration-runtimes"
)
DEFAULT_LIVE_CONFIG = Path(
    "/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json"
)
DEFAULT_KEEP = 5
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _live_root(live_config_path: Path) -> str | None:
    try:
        payload = json.loads(live_config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    watchdog = payload.get("watchdog")
    argv = watchdog.get("supervisor_command") if isinstance(watchdog, Mapping) else None
    if not isinstance(argv, list):
        return None
    entries = [
        Path(item)
        for item in argv
        if isinstance(item, str) and Path(item).name == "supervisor.py"
    ]
    if len(entries) != 1 or not entries[0].is_absolute():
        return None
    return str(entries[0].parent.parent.resolve())


def _live_integration_roots(live_config_path: Path) -> set[str]:
    try:
        payload = json.loads(live_config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    repositories = ((payload.get("coordination") or {}).get("repositories") or {})
    if not isinstance(repositories, Mapping):
        return set()
    roots: set[str] = set()
    for entry in repositories.values():
        raw = entry.get("integration_path") if isinstance(entry, Mapping) else None
        if isinstance(raw, str) and raw.strip() and Path(raw).is_absolute():
            roots.add(str(Path(raw).resolve()))
    return roots


def _active_leased_roots(status_root: Path) -> set[str]:
    """Command roots a currently-running worker lease still pins to."""

    import ai_status
    from runtime_state import load_runtime_state_snapshot

    ai_status.configure_status_root_paths(status_root)
    config = ai_status.load_config()
    state = load_runtime_state_snapshot(config)
    workers = state.get("workers", {})
    roots: set[str] = set()
    if not isinstance(workers, Mapping):
        return roots
    for worker in workers.values():
        if not isinstance(worker, Mapping):
            continue
        if str(worker.get("status") or "") not in ai_status.ACTIVE_WORKER_LEASE_STATUSES:
            continue
        runtime = ai_status._worker_status_command_runtime(worker)
        if not isinstance(runtime, Mapping):
            continue
        root = str(runtime.get("command_root") or "").strip()
        if not root:
            continue
        try:
            roots.add(str(Path(root).resolve()))
        except OSError:
            roots.add(root)
    return roots


def _unseal_and_delete(path: Path) -> None:
    """Remove the read-only seal (OPS-COMMAND-RUNTIME-READONLY-20260821) then delete."""

    for walk_root, dirs, files in os.walk(path):
        for name in dirs + files:
            entry = Path(walk_root) / name
            if entry.is_symlink():
                continue
            try:
                mode = entry.stat().st_mode
                os.chmod(entry, mode | stat.S_IWUSR | stat.S_IXUSR)
            except OSError:
                pass
    os.chmod(path, path.stat().st_mode | stat.S_IWUSR | stat.S_IXUSR)
    shutil.rmtree(path)


def plan_deletions(
    parent: Path,
    *,
    keep: int,
    live_root: str | None,
    leased_roots: set[str],
) -> tuple[list[Path], list[Path]]:
    """Return (delete, retain) sha-named checkouts directly under ``parent``."""

    candidates = [
        entry
        for entry in parent.iterdir()
        if entry.is_dir() and not entry.is_symlink() and SHA_PATTERN.match(entry.name)
    ]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    protected = set(leased_roots)
    if live_root:
        protected.add(live_root)
    newest = {str(p.resolve()) for p in candidates[:keep]}

    retain: list[Path] = []
    delete: list[Path] = []
    for entry in candidates:
        resolved = str(entry.resolve())
        if resolved in protected or resolved in newest:
            retain.append(entry)
        else:
            delete.append(entry)
    return delete, retain


def _main_locked(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", default=str(DEFAULT_COMMAND_RUNTIME_PARENT))
    parser.add_argument(
        "--integration-parent",
        default=str(DEFAULT_INTEGRATION_RUNTIME_PARENT),
    )
    parser.add_argument("--live-config", default=str(DEFAULT_LIVE_CONFIG))
    parser.add_argument(
        "--status-root",
        required=True,
        help="Coordination root to read active worker leases from.",
    )
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.keep < 1:
        parser.error("--keep must be >= 1")

    parent = Path(args.parent).expanduser().resolve()
    live_root = _live_root(Path(args.live_config).expanduser())
    live_integration_roots = _live_integration_roots(
        Path(args.live_config).expanduser()
    )
    leased_roots = _active_leased_roots(Path(args.status_root).expanduser().resolve())

    delete, retain = plan_deletions(
        parent, keep=args.keep, live_root=live_root, leased_roots=leased_roots
    )
    integration_delete: list[Path] = []
    integration_retain: list[Path] = []
    integration_parent = Path(args.integration_parent).expanduser().resolve()
    if integration_parent.is_dir() and not integration_parent.is_symlink():
        for repository_parent in integration_parent.iterdir():
            if not repository_parent.is_dir() or repository_parent.is_symlink():
                continue
            planned, kept = plan_deletions(
                repository_parent,
                keep=args.keep,
                live_root=None,
                leased_roots=live_integration_roots,
            )
            integration_delete.extend(planned)
            integration_retain.extend(kept)

    result: dict[str, object] = {
        "live_root": live_root,
        "leased_roots": sorted(leased_roots),
        "live_integration_roots": sorted(live_integration_roots),
        "retained": sorted(str(p) for p in retain),
        "integration_retained": sorted(str(p) for p in integration_retain),
        "deleted": [],
        "planned": sorted(str(p) for p in delete),
        "integration_deleted": [],
        "integration_planned": sorted(str(p) for p in integration_delete),
    }
    if not args.dry_run:
        deleted: list[str] = []
        for entry in delete:
            _unseal_and_delete(entry)
            deleted.append(str(entry))
        result["deleted"] = sorted(deleted)
        integration_deleted: list[str] = []
        for entry in integration_delete:
            _unseal_and_delete(entry)
            integration_deleted.append(str(entry))
        result["integration_deleted"] = sorted(integration_deleted)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        verb = "would delete" if args.dry_run else "deleted"
        print(
            f"prune_command_runtimes: {verb} {len(delete)} command and "
            f"{len(integration_delete)} integration runtimes; retained "
            f"{len(retain)} command and {len(integration_retain)} integration "
            f"(live={live_root or 'unknown'}, leased={len(leased_roots)})"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    # Parse only the lock binding first; all live-config reads, planning and
    # deletion happen after the same stable EX lock used by the merge owner.
    probe = argparse.ArgumentParser(add_help=False)
    probe.add_argument("--status-root")
    probe.add_argument("--json", action="store_true")
    known, _ = probe.parse_known_args(argv)
    if not known.status_root:
        return _main_locked(argv)
    lock_path = (
        Path(known.status_root).expanduser().resolve()
        / auto_integrator.DEFAULT_LOCK
    )
    try:
        with auto_integrator.lock_file(lock_path):
            return _main_locked(argv)
    except auto_integrator.IntegrationLockHeld as exc:
        payload = {
            "skipped": True,
            "reason": "integration_lock_held",
            "detail": str(exc),
            "deleted": [],
            "integration_deleted": [],
        }
        if known.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"prune_command_runtimes: skipped active integration runner ({exc})")
        return 0
    except auto_integrator.IntegrationLockError as exc:
        print(f"prune_command_runtimes: integration lock error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
