#!/usr/bin/env python3
"""Idempotently dispatch Trade Journey E2E fleet tasks into ai-status.json."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from canonical_writer_guard import assert_isolated_legacy_write_target

REPO_ROOT = Path(__file__).resolve().parents[1]
STATUS_ROOT = Path(os.path.expanduser(os.environ.get("PANTHEON_STATUS_ROOT", str(REPO_ROOT)))).resolve()
STATUS_PATH = STATUS_ROOT / "ai-status.json"
LOG_PATH = STATUS_ROOT / "ai-activity-log.jsonl"
PACKET_DIR = "docs/bff/execution-tasks/2026-07-11-trade-journey-e2e"
SPEC = "docs/04/pantheon_trade_journey_e2e_observability_gap_2026-07-11/TRADE_JOURNEY_E2E_OBSERVABILITY_GAP.md"
AUTO_BY = "dispatch_trade_journey_e2e_2026-07-11"

# id, title, owner, reviewer, wave, target_repo, dependencies, packet slug
TASKS = [
    ("TJ-E2E-001", "Producer and correlation inventory", "Claude", "Antigravity", 0, "pantheon", [], "producer-correlation-inventory"),
    ("TJ-E2E-002", "Journey domain and state contract", "Antigravity", "Claude", 0, "pantheon", ["TJ-E2E-001"], "journey-domain-state-contract"),
    ("TJ-E2E-003", "Correlation envelope propagation", "Claude", "Antigravity", 1, "pantheon", ["TJ-E2E-001", "TJ-E2E-002"], "correlation-envelope-propagation"),
    ("TJ-E2E-004", "Journey materializer and reverse index", "Antigravity", "Claude", 1, "pantheon", ["TJ-E2E-002", "TJ-E2E-003"], "materializer-reverse-index"),
    ("TJ-E2E-005", "Canonical BFF read API", "Claude", "Antigravity", 2, "pantheon", ["TJ-E2E-004"], "canonical-bff-read-api"),
    ("TJ-E2E-006", "Trade Journey frontend P0 workbench", "Antigravity", "Claude", 2, "execute-plans", ["TJ-E2E-005"], "frontend-p0-workbench"),
    ("TJ-E2E-007", "Live SSE and attention model", "Claude", "Antigravity", 3, "pantheon+execute-plans", ["TJ-E2E-005", "TJ-E2E-006"], "sse-attention-model"),
    ("TJ-E2E-008", "Governed journey actions", "Antigravity", "Claude", 3, "pantheon+execute-plans", ["TJ-E2E-005", "TJ-E2E-006"], "governed-journey-actions"),
    ("TJ-E2E-009", "Cross-entry IA integration", "Claude", "Antigravity", 3, "execute-plans", ["TJ-E2E-006"], "cross-entry-ia-integration"),
    ("TJ-E2E-010", "Historical replay and legacy backfill", "Antigravity", "Claude", 4, "pantheon", ["TJ-E2E-004", "TJ-E2E-005"], "replay-legacy-backfill"),
    ("TJ-E2E-011", "SLO and data-quality incidents", "Claude", "Antigravity", 4, "pantheon", ["TJ-E2E-004", "TJ-E2E-007", "TJ-E2E-010"], "slo-data-quality"),
    ("TJ-E2E-012", "Hosted acceptance and closeout", "Antigravity", "Human/Ops", 5, "pantheon+execute-plans", [f"TJ-E2E-{i:03d}" for i in range(1, 12)], "hosted-acceptance-closeout"),
]


def load_status() -> dict:
    if not STATUS_PATH.exists():
        return {"agents": {}}
    return json.loads(STATUS_PATH.read_text(encoding="utf-8"))


def task_payload(row: tuple, now: str) -> dict:
    task_id, title, owner, reviewer, wave, target_repo, dependencies, slug = row
    return {
        "task_id": task_id,
        "title": title,
        "summary_zh": f"依 Trade Journey E2E gap 規格執行：{title}。",
        "owner": owner,
        "reviewer": reviewer,
        "status": "queued",
        "phase": f"Trade Journey E2E / Wave {wave}",
        "dependencies": dependencies,
        "next": f"Read {PACKET_DIR}/{task_id}-{slug}.md and satisfy dependency merge gates.",
        "source_ref": {
            "doc": SPEC,
            "packet": f"{PACKET_DIR}/{task_id}-{slug}.md",
            "target_repo": target_repo,
            "merge_target": "main" if target_repo == "execute-plans" else "dev",
        },
        "metadata": {"wave": wave, "fleet_lane": slug, "target_repo": target_repo},
        "created_at": now,
        "updated_at": now,
        "auto_by": AUTO_BY,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    data = load_status()
    agents = data.setdefault("agents", [])
    tasks = data.setdefault("tasks", [])
    now = datetime.now(timezone.utc).isoformat()
    created, preserved = [], []
    for row in TASKS:
        task_id, _, owner, *_ = row
        existing = next((item for item in tasks if item.get("id") == task_id), None)
        if existing:
            preserved.append(task_id)
            continue
        payload = task_payload(row, now)
        payload["id"] = payload.pop("task_id")
        payload["depends_on"] = payload.pop("dependencies")
        payload["last_update"] = payload.pop("updated_at")
        payload["status"] = "todo"
        payload["task_class"] = "execution"
        payload["auto_created_by"] = payload.pop("auto_by")
        tasks.append(payload)
        lane = next((agent for agent in agents if agent.get("name") == owner), None)
        if lane is None:
            raise ValueError(f"registered owner lane not found: {owner}")
        ids = lane.setdefault("current_task_ids", [])
        if task_id not in ids:
            ids.append(task_id)
        created.append(task_id)
    summary = {"status_path": str(STATUS_PATH), "created": created, "preserved": preserved, "dry_run": args.dry_run}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0
    assert_isolated_legacy_write_target(STATUS_PATH, tool=Path(__file__).name)
    STATUS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert_isolated_legacy_write_target(LOG_PATH, tool=Path(__file__).name)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": now, "event": "fleet_dispatch", "auto_by": AUTO_BY, **summary}, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
