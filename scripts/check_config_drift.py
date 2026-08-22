#!/usr/bin/env python3
"""Detect drift between the repo source-of-truth config and the live runtime
config, and detect a stale dev-root checkout.

Two failure modes this guards against, both seen in production:

1. A reviewed account or agent capacity gets hand-edited in the live config
   during incident mitigation and never restored.

2. The dev-root checkout the supervisor runs from drifts behind origin/dev
   (the deploy sync stops), so merged code never goes live.

NOT every live != repo difference is drift. Some live overrides are
legitimate and environment-specific. Those live on an allowlist and are
reported as informational, never auto-fixed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Control toggles that must match the repo source-of-truth unless explicitly
# allowlisted as an environment override below.
CRITICAL_FLAGS: tuple[str, ...] = (
    "ready_dispatcher.enabled",
    "ready_dispatcher.sidecar_only_agents",
    "ready_dispatcher.max_dispatches_per_tick",
    "ready_dispatcher.max_active_workers_per_task",
    "ready_dispatcher.max_concurrent_per_account",
    "ready_dispatcher.max_concurrent_workers",
    "watchdog.enabled",
    "supervisor.observe_worker_commit_progress",
    "supervisor.lease_requires_work_progress",
    "worker_runtime.worker_lease_seconds",
    "worker_runtime.work_progress_stale_seconds",
    "task_state_store.mode",
    # worker_reassignment governs who a task's owner/reviewer fails over to
    # (see supervisor.py's plan_task_assignment_pair / persist_task_reassignment).
    # Unlike agents.<id>.max_parallel below, it has no per-agent dynamic
    # expansion, so a hand edit to enabled/limits/fallback graphs during
    # incident mitigation would otherwise drift silently forever.
    "worker_reassignment.enabled",
    "worker_reassignment.max_reassignments_per_cycle",
    "worker_reassignment.owner_fallbacks",
    "worker_reassignment.reviewer_fallbacks",
    # load_balance is the saturated-but-healthy-lane reassignment policy
    # (see supervisor.py's assignment_saturated_recoverable); off by default,
    # so a live-only enable/threshold edit is exactly the kind of change
    # this allowlist exists to catch.
    "worker_reassignment.load_balance",
    # failure_loop is the repeated-failure auto-governance policy (see
    # supervisor.py's reconcile_failure_loops) -- same reasoning as
    # load_balance above: a live-only change to thresholds or the
    # auto-reassignment budget must not drift silently from repo.
    "worker_reassignment.failure_loop",
)

# Paths where live is ALLOWED to diverge from repo for legitimate
# environment reasons. Reported as info, never counted as drift, never fixed.
DEFAULT_INTENTIONAL_OVERRIDES: frozenset[str] = frozenset()

_SENTINEL = object()


def get_dotted(data: dict, path: str):
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _SENTINEL
        cur = cur[part]
    return cur


def set_dotted(data: dict, path: str, value) -> None:
    cur = data
    parts = path.split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def find_drift(
    repo_cfg: dict,
    live_cfg: dict,
    critical_flags=CRITICAL_FLAGS,
    overrides=DEFAULT_INTENTIONAL_OVERRIDES,
) -> dict:
    """Return {"drift": [...], "intentional": [...], "missing": [...]}.

    drift: non-allowlisted critical flag where repo != live (actionable).
    intentional: allowlisted flag where repo != live (informational).
    missing: flag absent from the repo config (informational). A repo-owned flag
    missing from live is actionable drift so --fix can provision it.
    """
    drift, intentional, missing = [], [], []
    effective_flags = list(critical_flags)
    for provider_id, provider in (repo_cfg.get("providers") or {}).items():
        if isinstance(provider, dict):
            effective_flags.append(f"providers.{provider_id}.account")
    for agent_id, agent in (repo_cfg.get("agents") or {}).items():
        if not isinstance(agent, dict):
            continue
        effective_flags.append(f"agents.{agent_id}.provider")
        if agent.get("dispatch_slot_for"):
            effective_flags.append(f"agents.{agent_id}.dispatch_slot_for")
        else:
            effective_flags.extend(
                (
                    f"agents.{agent_id}.max_parallel",
                    f"agents.{agent_id}.worker_slots",
                )
            )
    for path in effective_flags:
        repo_val = get_dotted(repo_cfg, path)
        live_val = get_dotted(live_cfg, path)
        if repo_val is _SENTINEL:
            missing.append({"path": path,
                            "repo": None,
                            "live": None if live_val is _SENTINEL else live_val})
            continue
        if live_val is _SENTINEL:
            record = {"path": path, "repo": repo_val, "live": None}
            (intentional if path in overrides else drift).append(record)
            continue
        if repo_val == live_val:
            continue
        record = {"path": path, "repo": repo_val, "live": live_val}
        (intentional if path in overrides else drift).append(record)
    return {"drift": drift, "intentional": intentional, "missing": missing}


def git_commits_behind(checkout: Path, ref: str, runner=subprocess.run) -> int | None:
    """How many commits `checkout` HEAD is behind `ref`. None if undeterminable."""
    fetch_ref = ref.split("/", 1)[-1]
    if ref.startswith("origin/"):
        # Deployment roots intentionally use narrow fetch refspecs. A bare
        # `git fetch origin dev` updates FETCH_HEAD only in those checkouts and
        # can leave refs/remotes/origin/dev stale, hiding real dev-root lag.
        branch = ref.split("/", 1)[1]
        fetch_ref = f"{branch}:refs/remotes/origin/{branch}"
    try:
        runner(["git", "-C", str(checkout), "fetch", "--quiet",
                "origin", fetch_ref],
               check=False, capture_output=True, timeout=120)
        result = runner(["git", "-C", str(checkout), "rev-list", "--count",
                         f"HEAD..{ref}"], check=False, capture_output=True,
                        text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    out = (getattr(result, "stdout", "") or "").strip()
    try:
        return int(out)
    except ValueError:
        return None


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_repository_source_roots(values: list[str] | None) -> dict[str, str]:
    """Parse deployment topology without treating it as repo policy."""

    roots: dict[str, str] = {}
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
        roots[repository_id] = str(candidate.resolve(strict=False))
    return roots


def find_repository_source_drift(
    live_cfg: dict,
    expected_roots: dict[str, str],
) -> list[dict[str, str | None]]:
    """Return host-topology mismatches which require a promotion to repair."""

    repositories = ((live_cfg.get("coordination") or {}).get("repositories") or {})
    if not isinstance(repositories, dict):
        repositories = {}
    drift: list[dict[str, str | None]] = []
    for repository_id, expected in expected_roots.items():
        entry = repositories.get(repository_id)
        actual_raw = entry.get("local_path") if isinstance(entry, dict) else None
        actual = (
            str(Path(str(actual_raw)).expanduser().resolve(strict=False))
            if isinstance(actual_raw, str) and actual_raw.strip()
            else None
        )
        if actual != expected:
            drift.append(
                {
                    "repository_id": repository_id,
                    "expected_local_path": expected,
                    "live_local_path": actual,
                }
            )
    return drift


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-config", default=str(repo_root / ".orchestrator" / "config.json"))
    parser.add_argument("--live-config", required=True,
                        help="Path to the runtime config the supervisor actually loads.")
    parser.add_argument("--dev-root", default=None,
                        help="Checkout the supervisor runs from; checked for staleness vs --ref.")
    parser.add_argument("--ref", default="origin/dev")
    parser.add_argument("--max-behind", type=int, default=None,
                        help="If set, fail when --dev-root is behind --ref by more than this.")
    parser.add_argument("--fix", action="store_true",
                        help="Align non-allowlisted drifted flags in the live config to the repo value.")
    parser.add_argument(
        "--repository-source-root",
        action="append",
        default=[],
        metavar="REPOSITORY_ID=/ABSOLUTE/GIT/ROOT",
        help="Fail when a live registry source root differs from deployment topology.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    repo_cfg = _load(Path(args.repo_config))
    live_path = Path(args.live_config)
    live_cfg = _load(live_path)

    report = find_drift(repo_cfg, live_cfg)
    repository_source_roots = parse_repository_source_roots(args.repository_source_root)
    repository_source_drift = find_repository_source_drift(
        live_cfg, repository_source_roots
    )
    behind = None
    if args.dev_root:
        behind = git_commits_behind(Path(args.dev_root), args.ref)

    fixed = []
    if args.fix and report["drift"]:
        for item in report["drift"]:
            set_dotted(live_cfg, item["path"], item["repo"])
            fixed.append(item["path"])
        live_path.write_text(json.dumps(live_cfg, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")

    behind_fail = (args.max_behind is not None and behind is not None
                   and behind > args.max_behind)
    # After --fix, drift is resolved; only unresolved drift fails.
    drift_fail = (bool(report["drift"]) and not args.fix) or bool(repository_source_drift)
    exit_code = 1 if (drift_fail or behind_fail) else 0

    if args.json:
        print(json.dumps({**report, "repository_source_drift": repository_source_drift,
                          "dev_root_behind": behind,
                          "fixed": fixed, "exit_code": exit_code}, indent=2))
        return exit_code

    if report["drift"]:
        label = "FIXED" if args.fix else "DRIFT"
        for d in report["drift"]:
            print(f"[{label}] {d['path']}: repo={d['repo']!r} live={d['live']!r}")
    for d in report["intentional"]:
        print(f"[override] {d['path']}: repo={d['repo']!r} live={d['live']!r} (allowlisted env override)")
    for d in report["missing"]:
        print(f"[missing] {d['path']}: repo={d['repo']!r} live={d['live']!r}")
    for item in repository_source_drift:
        print(
            "[SOURCE_ROOT_DRIFT] "
            f"{item['repository_id']}: expected={item['expected_local_path']!r} "
            f"live={item['live_local_path']!r}"
        )
    if behind is not None:
        flag = " (STALE)" if behind_fail else ""
        print(f"[dev-root] {args.dev_root} is {behind} commit(s) behind {args.ref}{flag}")
    if exit_code == 0 and not report["drift"] and not repository_source_drift:
        print("OK: no actionable config drift.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
