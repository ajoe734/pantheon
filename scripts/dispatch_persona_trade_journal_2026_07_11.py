#!/usr/bin/env python3
"""Idempotently add Persona Trade Journal execution tasks to ai-status.json."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS_ROOT = Path(os.path.expanduser(os.environ.get("PANTHEON_STATUS_ROOT", str(ROOT)))).resolve()
STATUS = STATUS_ROOT / "ai-status.json"
LOG = STATUS_ROOT / "ai-activity-log.jsonl"
SPEC = "docs/04/persona_trade_journal_gap_2026-07-11/PERSONA_TRADE_JOURNAL_GAP.md"
PACKET = "docs/bff/execution-tasks/2026-07-11-persona-trade-journal/INDEX.md"
AUTO_BY = "dispatch_persona_trade_journal_2026_07_11"

ROWS = [
    ("PTJ-001", "Persona Trade Journal contract and schema lock", "Claude2", "Codex2", 0, [], "contract-schema", ["services/telemetry", "services/persona", "services/lineage-read", SPEC, PACKET]),
    ("PTJ-002", "Trade episode projection and lineage replay", "Gemini2", "Claude2", 1, ["PTJ-001"], "episode-projection", ["services/telemetry", "services/lineage-read", "services/execution/runtime-manager"]),
    ("PTJ-003", "Persona trade reflection pipeline", "Claude", "Codex2", 1, ["PTJ-001"], "reflection-worker", ["services/persona", "services/memory"]),
    ("PTJ-004", "Persona Trade Journal BFF APIs and governed commands", "Codex2", "Claude2", 2, ["PTJ-002", "PTJ-003"], "bff-journal-api", ["services/control-plane/bff"]),
    ("PTJ-005", "Trade lesson memory and evaluation governance", "Gemini", "Codex2", 2, ["PTJ-003"], "lesson-governance", ["services/persona", "services/memory", "services/policy-learning"]),
    ("PTJ-006", "Persona Trade Journal frontend", "Antigravity2", "Codex2", 3, ["PTJ-004"], "frontend-journal", ["execute-plans:src/management", "execute-plans:src/lib", "execute-plans:e2e"]),
    ("PTJ-007", "Persona Trade Journal integration and hosted closeout", "Codex", "Human/Ops", 4, ["PTJ-002", "PTJ-003", "PTJ-004", "PTJ-005", "PTJ-006"], "hosted-closeout", ["services", "execute-plans:src", "execute-plans:e2e", "docs/04/persona_trade_journal_gap_2026-07-11/archive"]),
]

ACCEPTANCE = {
    "PTJ-001": ["versioned episode reflection lesson and event schemas exist", "identity lifecycle truth ownership and unresolved migration rules have contract tests"],
    "PTJ-002": ["replay covers duplicate late correction partial scale reversal and forced exit", "projection exposes source as_of coverage and missing refs without guessed joins"],
    "PTJ-003": ["fill episode and pattern reflections use immutable facts snapshots", "unknown counterfactual retry version and no-mutation guardrails are tested"],
    "PTJ-004": ["journal detail reflection and pattern routes support cursor filters RBAC masking and confidence", "commands enforce idempotency audit receipts and cross-persona isolation"],
    "PTJ-005": ["lesson candidates cannot directly mutate policy risk capital artifact or live behavior", "memory evaluation approval and environment promotion gates fail closed with receipts"],
    "PTJ-006": ["Persona Trade Journal works in strict-live desktop and mobile", "tests cover full paper episode missing refs force close and pending or failed reflection"],
    "PTJ-007": ["all child tasks are merged or explicitly superseded with evidence", "hosted dev smoke proves decision to fill to attribution to reflection to lesson review with no live orders"],
}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    state = json.loads(STATUS.read_text(encoding="utf-8"))
    tasks = state.setdefault("tasks", [])
    existing = {t.get("id") for t in tasks}
    stamp = now()
    added = []
    for task_id, title, owner, reviewer, wave, deps, lane, artifacts in ROWS:
        if task_id in existing:
            continue
        tasks.append({
            "id": task_id, "title": title,
            "summary_zh": f"依 Persona Trade Journal gap 文件執行 {title}。",
            "phase": f"Persona Trade Journal / Wave {wave}", "owner": owner,
            "reviewer": reviewer, "status": "todo", "depends_on": deps,
            "artifacts": artifacts + [PACKET], "acceptance": ACCEPTANCE[task_id],
            "next": f"Read {SPEC} and execute {task_id} within declared scope.",
            "last_update": stamp, "source_ref": {"doc": SPEC, "packet": PACKET},
            "delivery_layer": "primary", "mutates_canonical": task_id != "PTJ-007",
            "wave": wave, "fleet_lane": lane, "task_class": "execution",
            "auto_created_by": AUTO_BY, "auto_generated": True,
            "live_order_side_effects_allowed": False,
        })
        added.append(task_id)
    if args.dry_run:
        print(json.dumps({"would_add": added, "status": str(STATUS)}, ensure_ascii=False))
        return 0
    state["updated_at"] = stamp
    STATUS.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"timestamp": stamp, "type": "execution_tasks_materialized", "actor": AUTO_BY, "task_ids": added, "source_ref": {"doc": SPEC, "packet": PACKET}}, ensure_ascii=False) + "\n")
    print(json.dumps({"added": added, "status": str(STATUS)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
