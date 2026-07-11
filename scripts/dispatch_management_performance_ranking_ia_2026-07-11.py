#!/usr/bin/env python3
"""Dispatch management performance and ranking IA tasks for 2026-07-11.

Spec: docs/04/pantheon_management_performance_ranking_ia_gap_2026-07-11/INDEX.md
Packet: docs/bff/execution-tasks/2026-07-11-management-performance-ranking-ia/INDEX.md
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STATUS_ROOT = Path(
    os.path.expanduser(os.environ.get("PANTHEON_STATUS_ROOT", str(REPO_ROOT)))
).resolve()
STATUS_PATH = STATUS_ROOT / "ai-status.json"
LOG_PATH = STATUS_ROOT / "ai-activity-log.jsonl"
AUTO_BY = "dispatch_management_performance_ranking_ia_2026-07-11"
PACKET = "docs/bff/execution-tasks/2026-07-11-management-performance-ranking-ia/INDEX.md"
SPEC = "docs/04/pantheon_management_performance_ranking_ia_gap_2026-07-11/INDEX.md"
SOURCE_REF = {
    "doc": SPEC,
    "packet": PACKET,
    "trigger": "2026-07-11 complete performance ranking page and menu information architecture audit",
    "frontend_repo": "ajoe734/execute-plans",
    "backend_repo": "ajoe734/pantheon",
}
PROGRESS_FIELDS = {
    "status",
    "branch",
    "next",
    "updated_at",
    "last_update",
    "started_at",
    "completed_at",
    "closed_at",
    "pr",
    "pr_number",
    "pr_url",
    "merge_commit",
    "merge_sha",
    "review",
    "reviewer_approval",
    "closeout_ref",
    "evidence_ref",
}
TERMINAL_STATUSES = {"done", "superseded", "cancelled"}
GENERIC_NEXT_MESSAGES = {
    None,
    "",
    "Assignment created",
    "Assignment created from management performance and ranking IA packet",
}
PRIMARY_AGENT_NEXT_TASK = {
    "Claude2": "MGMT-PERF-IA-001",
    "Codex2": "MGMT-PERF-IA-002",
    "Antigravity2": "MGMT-PERF-IA-003",
    "Gemini2": "MGMT-PERF-IA-004",
    "Antigravity": "MGMT-PERF-IA-005",
    "Gemini": "MGMT-PERF-IA-006",
    "Claude": "MGMT-PERF-IA-007",
}
NEXT_BY_TASK = {
    "MGMT-PERF-IA-001": "Lock the canonical management route, menu, tab, and redirect manifest.",
    "MGMT-PERF-IA-002": "Lock the shared performance, ranking, and governance read model.",
    "MGMT-PERF-IA-003": "Build the canonical Performance Center after Wave 0 contracts merge.",
    "MGMT-PERF-IA-004": "Build the canonical Rankings Center after Wave 0 contracts merge.",
    "MGMT-PERF-IA-005": "Refactor Promotion Allocation into Governance Decisions after Wave 0 contracts merge.",
    "MGMT-PERF-IA-006": "Integrate Fleet, Cockpit, entity details, Human Inbox, and Agora after Wave 1.",
    "MGMT-PERF-IA-007": "Clean aliases, dead pages, and duplicate navigation after center integration.",
    "MGMT-PERF-IA-008": "Close with hosted workflow, route migration, deployment, and governance evidence.",
}


# id, title, summary_zh, owner, reviewer, phase, dependencies, acceptance,
# artifacts, metadata
TASKS = [
    (
        "MGMT-PERF-IA-001",
        "Canonical route and menu manifest",
        "建立 management sidebar、三個 canonical centers、tabs、command palette、breadcrumb 與 legacy redirects 的單一來源。",
        "Claude2",
        "Codex2",
        "Management Performance Ranking IA / Wave 0 frontend routes",
        [],
        [
            "every management sidebar item is assigned exactly once",
            "canonical centers and compatibility redirects share one typed manifest",
            "legacy query context is preserved without redirect loops",
            "execute-plans PR is tested merged to dev and records its merge SHA",
        ],
        [
            "execute-plans:src/management",
            "execute-plans:src/App.tsx",
            "execute-plans:scripts/lib/management-routes.mjs",
            f"{PACKET.rsplit('/', 1)[0]}/MGMT-PERF-IA-001-canonical-route-menu-manifest.md",
        ],
        {"wave": 0, "fleet_lane": "canonical-route-menu-manifest", "target_repo": "execute-plans"},
    ),
    (
        "MGMT-PERF-IA-002",
        "Performance and ranking read model",
        "鎖定 performance、exposure、ranking、recommendation 與 governed apply 共用 identity、filter、source confidence 與 snapshot contract。",
        "Codex2",
        "Claude2",
        "Management Performance Ranking IA / Wave 0 BFF contract",
        [],
        [
            "shared identity and query vocabulary covers every canonical center",
            "formal partial fallback degraded and unavailable states are explicit",
            "ranking evidence recommendation review and apply receipt are separate",
            "Pantheon PR is tested merged to dev and records its merge SHA",
        ],
        [
            "services/control-plane/bff",
            "services/control-plane/bff/tests",
            f"{PACKET.rsplit('/', 1)[0]}/MGMT-PERF-IA-002-performance-ranking-read-model.md",
        ],
        {"wave": 0, "fleet_lane": "performance-ranking-read-model", "target_repo": "pantheon"},
    ),
    (
        "MGMT-PERF-IA-003",
        "Performance Center consolidation",
        "合併 Portfolio Book 與 Performance Attribution 成為 Overview、Attribution、Exposure and Holdings 三個 tabs 的正式 Performance Center。",
        "Antigravity2",
        "Codex2",
        "Management Performance Ranking IA / Wave 1 Performance Center",
        ["MGMT-PERF-IA-001", "MGMT-PERF-IA-002"],
        [
            "one center covers performance attribution exposure holdings and coverage",
            "fallback summaries never appear as formal attribution",
            "nan undefined and false zero metrics are not operator facing",
            "legacy routes and hosted desktop mobile behavior are proven",
        ],
        [
            "execute-plans:src/management",
            "execute-plans:src/lib",
            "execute-plans:e2e",
            f"{PACKET.rsplit('/', 1)[0]}/MGMT-PERF-IA-003-performance-center.md",
        ],
        {"wave": 1, "fleet_lane": "performance-center", "target_repo": "execute-plans"},
    ),
    (
        "MGMT-PERF-IA-004",
        "Rankings Center consolidation",
        "把 Persona League 與 Quarterly Ranking 合併為唯一 Rankings Center，區分 rolling operations 與 quarterly governance evidence。",
        "Gemini2",
        "Claude2",
        "Management Performance Ranking IA / Wave 1 Rankings Center",
        ["MGMT-PERF-IA-001", "MGMT-PERF-IA-002"],
        [
            "rolling and quarterly ranking responsibilities are distinct",
            "ranking tables exist only in Rankings Center",
            "rankings link to performance fleet and governed recommendations",
            "legacy routes and hosted desktop mobile behavior are proven",
        ],
        [
            "execute-plans:src/management",
            "execute-plans:src/lib",
            "execute-plans:e2e",
            f"{PACKET.rsplit('/', 1)[0]}/MGMT-PERF-IA-004-rankings-center.md",
        ],
        {"wave": 1, "fleet_lane": "rankings-center", "target_repo": "execute-plans"},
    ),
    (
        "MGMT-PERF-IA-005",
        "Governance Decisions consolidation",
        "把 Promotion Allocation 改成 Recommendations、Capital、Policy 的治理中心，移除內嵌排名並強制 Human Review 與 apply receipt。",
        "Antigravity",
        "Codex2",
        "Management Performance Ranking IA / Wave 1 Governance Decisions",
        ["MGMT-PERF-IA-001", "MGMT-PERF-IA-002"],
        [
            "governance consumes immutable ranking snapshots without duplicate ranking tables",
            "recommendation review approval and applied state are separate",
            "capital access and rebalance mutations require governed apply receipts",
            "legacy routes and hosted desktop mobile behavior are proven",
        ],
        [
            "execute-plans:src/management/pages/oversight",
            "execute-plans:src/lib",
            "execute-plans:e2e",
            f"{PACKET.rsplit('/', 1)[0]}/MGMT-PERF-IA-005-governance-decisions.md",
        ],
        {"wave": 1, "fleet_lane": "governance-decisions", "target_repo": "execute-plans"},
    ),
    (
        "MGMT-PERF-IA-006",
        "Contextual integration",
        "把 Cockpit、Persona Fleet、entity details、Human Inbox 與 Agora 接到 canonical centers，保留上下文且不再新增重複分析頁。",
        "Gemini",
        "Claude2",
        "Management Performance Ranking IA / Wave 2 integration",
        ["MGMT-PERF-IA-003", "MGMT-PERF-IA-004", "MGMT-PERF-IA-005"],
        [
            "all legitimate entry points preserve entity and period context",
            "entity summaries are clearly distinct from formal analysis",
            "Agora execution performance remains separate and linked",
            "empty detail contracts render honest unavailable states",
        ],
        [
            "execute-plans:src/management",
            "execute-plans:src/agora",
            "execute-plans:e2e",
            f"{PACKET.rsplit('/', 1)[0]}/MGMT-PERF-IA-006-contextual-integration.md",
        ],
        {"wave": 2, "fleet_lane": "contextual-integration", "target_repo": "execute-plans"},
    ),
    (
        "MGMT-PERF-IA-007",
        "Migration cleanup and regression",
        "完成 legacy alias、dead page、secondary navigation、route baseline 與 mobile/desktop regression 清理。",
        "Claude",
        "Codex2",
        "Management Performance Ranking IA / Wave 2 cleanup",
        ["MGMT-PERF-IA-003", "MGMT-PERF-IA-004", "MGMT-PERF-IA-005", "MGMT-PERF-IA-006"],
        [
            "no dead exported page remains without an explicit decision",
            "ManagementOperationsNav and duplicate aliases are removed safely",
            "canonical and compatibility routes pass crawl and redirect tests",
            "desktop and mobile navigation share one hierarchy without overlap",
        ],
        [
            "execute-plans:src/App.tsx",
            "execute-plans:src/management",
            "execute-plans:scripts/lib/management-routes.mjs",
            "execute-plans:e2e",
            f"{PACKET.rsplit('/', 1)[0]}/MGMT-PERF-IA-007-migration-cleanup-regression.md",
        ],
        {"wave": 2, "fleet_lane": "migration-cleanup-regression", "target_repo": "execute-plans"},
    ),
    (
        "MGMT-PERF-IA-008",
        "Hosted acceptance and closeout",
        "彙整所有 PR、merge SHA、deploy、desktop/mobile smoke、legacy redirects 與 Human Review receipt，證明完整營運閉環。",
        "Codex2",
        "Human/Ops",
        "Management Performance Ranking IA / Wave 3 closeout",
        [
            "MGMT-PERF-IA-001",
            "MGMT-PERF-IA-002",
            "MGMT-PERF-IA-003",
            "MGMT-PERF-IA-004",
            "MGMT-PERF-IA-005",
            "MGMT-PERF-IA-006",
            "MGMT-PERF-IA-007",
        ],
        [
            "all child tasks are merged or explicitly superseded with evidence",
            "hosted dev proves canonical and legacy navigation on desktop and mobile",
            "the full Fleet to Performance to Rankings to Governance to Review loop is proven",
            "closeout records deployments residual risks and Human Ops verdict",
        ],
        [
            "docs/04/pantheon_management_performance_ranking_ia_gap_2026-07-11/archive",
            "execute-plans:hosted-dev-evidence",
            "services/control-plane/bff",
            f"{PACKET.rsplit('/', 1)[0]}/MGMT-PERF-IA-008-hosted-acceptance-closeout.md",
        ],
        {"wave": 3, "fleet_lane": "hosted-acceptance-closeout", "target_repo": "pantheon+execute-plans"},
    ),
]


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_state() -> dict:
    if not STATUS_PATH.exists():
        raise FileNotFoundError(f"status file not found: {STATUS_PATH}")
    return json.loads(STATUS_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATUS_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_log(entry: dict) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def upsert_task(state: dict, task: dict) -> tuple[bool, str]:
    tasks = state.setdefault("tasks", [])
    for index, existing in enumerate(tasks):
        if existing.get("id") != task["id"]:
            continue
        merged = {**existing, **task}
        if existing.get("status") and existing.get("status") != "todo":
            for key in PROGRESS_FIELDS:
                if key in existing:
                    merged[key] = existing[key]
        tasks[index] = merged
        return False, str(merged.get("status") or "")
    tasks.append(task)
    return True, str(task.get("status") or "")


def remove_terminal_task_from_agents(state: dict, task_id: str) -> None:
    for agent in state.get("agents", []):
        ids = agent.get("current_task_ids")
        if isinstance(ids, list):
            agent["current_task_ids"] = [item for item in ids if item != task_id]


def assign_agent(state: dict, owner: str, task_id: str, timestamp: str, next_note: str) -> None:
    for agent in state.get("agents", []):
        if agent.get("name") != owner:
            continue
        ids = agent.setdefault("current_task_ids", [])
        if task_id not in ids:
            ids.append(task_id)
        should_update = (
            agent.get("next") in GENERIC_NEXT_MESSAGES
            or PRIMARY_AGENT_NEXT_TASK.get(owner) == task_id
        )
        if should_update:
            agent["status"] = "waiting"
            agent["next"] = next_note
            agent["last_update"] = timestamp
        return
    raise ValueError(f"registered owner lane not found: {owner}")


def build_task(task_tuple: tuple, timestamp: str) -> dict:
    task_id, title, summary, owner, reviewer, phase, deps, acceptance, artifacts, metadata = task_tuple
    task = {
        "id": task_id,
        "title": title,
        "summary_zh": summary,
        "phase": phase,
        "owner": owner,
        "reviewer": reviewer,
        "status": "todo",
        "depends_on": deps,
        "artifacts": artifacts,
        "acceptance": acceptance,
        "next": NEXT_BY_TASK[task_id],
        "last_update": timestamp,
        "task_class": "execution",
        "auto_created_by": AUTO_BY,
        "auto_generated": True,
        "source_ref": SOURCE_REF,
        "delivery_layer": "primary",
        "mutates_canonical": True,
        "helper_kind": "management_performance_ranking_ia_execution_slice",
    }
    task.update(metadata)
    return task


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print creates/upserts without writing ai-status.json or ai-activity-log.jsonl.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = load_state()
    timestamp = iso_now()
    inserted_logs: list[dict] = []

    for task_tuple in TASKS:
        task = build_task(task_tuple, timestamp)
        inserted, status_after = upsert_task(state, task)
        if status_after in TERMINAL_STATUSES:
            remove_terminal_task_from_agents(state, task["id"])
        else:
            assign_agent(state, task["owner"], task["id"], timestamp, task["next"])
        if inserted:
            inserted_logs.append(
                {
                    "ts": timestamp,
                    "agent": os.environ.get("AI_NAME", "Codex"),
                    "type": "assign",
                    "task_id": task["id"],
                    "message": f"Assigned {task['id']} to {task['owner']} with reviewer {task['reviewer']}",
                }
            )
        action = "CREATE" if inserted else "UPSERT"
        deps = ",".join(task["depends_on"]) if task["depends_on"] else "-"
        print(
            f"{action} {task['id']:16} owner={task['owner']:12} "
            f"reviewer={task['reviewer']:10} deps={deps}"
        )

    state["updated_at"] = timestamp
    if args.dry_run:
        print(f"Dry run only. No writes made. status_root={STATUS_ROOT}")
        return 0

    save_state(state)
    for entry in inserted_logs:
        append_log(entry)
    print(f"Done. Updated {STATUS_PATH}.")
    print(
        "Run `PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon "
        "python3 scripts/ai_status.py sync` to refresh generated status views."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
