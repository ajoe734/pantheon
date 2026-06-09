#!/usr/bin/env python3
"""Detect drift between the repo source-of-truth config and the live runtime
config, and detect a stale dev-root checkout.

Two failure modes this guards against, both seen in production:

1. A control toggle (e.g. `chair_review.enabled`) gets hand-disabled in the
   live config during incident "止血" and never restored, so the running
   supervisor silently stops chair review / reassignment for days. The repo
   config still says it should be on.

2. The dev-root checkout the supervisor runs from drifts behind origin/dev
   (the deploy sync stops), so merged code never goes live.

NOT every live != repo difference is drift. Some live overrides are
legitimate and environment-specific (e.g. `coordination.enabled` is off
because this host has no `front-ai-trading-system` sibling checkout that the
coordination mirror requires). Those live on an allowlist and are reported as
informational, never auto-fixed.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Control toggles that must match the repo source-of-truth unless explicitly
# allowlisted as an environment override below.
CRITICAL_FLAGS: tuple[str, ...] = (
    "chair_review.enabled",
    "worker_reassignment.enabled",
    "ready_dispatcher.enabled",
    "coordination.enabled",
    "github_bus.enabled",
    "watchdog.enabled",
)

# Paths where live is ALLOWED to diverge from repo for legitimate
# environment reasons. Reported as info, never counted as drift, never fixed.
DEFAULT_INTENTIONAL_OVERRIDES: frozenset[str] = frozenset(
    {
        # coordination mirror requires a sibling front-ai-trading-system
        # checkout that is absent on supervisor-only hosts; enabling it there
        # crashes every scan.
        "coordination.enabled",
        # GitHub event bus is intentionally off on hosts without the relay
        # wired up.
        "github_bus.enabled",
    }
)

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
    missing: flag absent from one or both configs (informational).
    """
    drift, intentional, missing = [], [], []
    for path in critical_flags:
        repo_val = get_dotted(repo_cfg, path)
        live_val = get_dotted(live_cfg, path)
        if repo_val is _SENTINEL or live_val is _SENTINEL:
            missing.append({"path": path,
                            "repo": None if repo_val is _SENTINEL else repo_val,
                            "live": None if live_val is _SENTINEL else live_val})
            continue
        if repo_val == live_val:
            continue
        record = {"path": path, "repo": repo_val, "live": live_val}
        (intentional if path in overrides else drift).append(record)
    return {"drift": drift, "intentional": intentional, "missing": missing}


def git_commits_behind(checkout: Path, ref: str, runner=subprocess.run) -> int | None:
    """How many commits `checkout` HEAD is behind `ref`. None if undeterminable."""
    try:
        runner(["git", "-C", str(checkout), "fetch", "--quiet",
                "origin", ref.split("/", 1)[-1]],
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
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    repo_cfg = _load(Path(args.repo_config))
    live_path = Path(args.live_config)
    live_cfg = _load(live_path)

    report = find_drift(repo_cfg, live_cfg)
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
    drift_fail = bool(report["drift"]) and not args.fix
    exit_code = 1 if (drift_fail or behind_fail) else 0

    if args.json:
        print(json.dumps({**report, "dev_root_behind": behind,
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
    if behind is not None:
        flag = " (STALE)" if behind_fail else ""
        print(f"[dev-root] {args.dev_root} is {behind} commit(s) behind {args.ref}{flag}")
    if exit_code == 0 and not report["drift"]:
        print("OK: no actionable config drift.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
