"""Shadow validator for rewrite/concurrency.py (Phase 1a).

Proves the clean `max_parallel()` reproduces the incumbent
`agent_dispatch_capacity()` for every agent in a real config, before the
dispatch loop is switched to it. This is the "shadow validation" discipline from
SUPERVISOR_REWRITE_PLAN.md §4: compute the new decision, diff it against the old
one on live data, require exact agreement before cutover.

Usage:
    python3 -m rewrite.shadow --config /path/to/live-supervisor-config.json

Exit code 0 = every agent agrees; 1 = at least one mismatch (which is a real
finding: the incumbent's extra logic is load-bearing for that agent).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# .orchestrator on the path so we can import the incumbent for comparison.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import supervisor  # noqa: E402  (comparison oracle only)

from rewrite import concurrency  # noqa: E402


def compare_capacity(config: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per agent: incumbent vs clean max-parallel and whether they agree."""
    settings = supervisor.ready_dispatch_settings(config)
    rows: list[dict[str, Any]] = []
    for agent_id in (config.get("agents", {}) or {}):
        norm = supervisor.normalize_agent_id(agent_id)
        if not norm:
            continue
        display = supervisor.display_name_for(config, norm)
        old = supervisor.agent_dispatch_capacity(config, norm, settings)
        new = concurrency.max_parallel(config, norm, settings=settings, display_name=display)
        rows.append({"agent": norm, "old": old, "new": new, "agree": old == new})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to a supervisor config JSON.")
    parser.add_argument("--quiet", action="store_true", help="Only print mismatches + summary.")
    args = parser.parse_args(argv)

    config = json.loads(Path(args.config).read_text())
    rows = compare_capacity(config)
    mismatches = [r for r in rows if not r["agree"]]

    for r in rows:
        if r["agree"] and args.quiet:
            continue
        tag = "OK      " if r["agree"] else "MISMATCH"
        print(f"  {tag} {r['agent']}: old={r['old']} new={r['new']}")

    print(f"\nmax_parallel shadow: {len(rows)} agents, {len(mismatches)} mismatch")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
