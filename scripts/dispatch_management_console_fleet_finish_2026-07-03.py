#!/usr/bin/env python3
"""Dispatch Management Console fleet finish tasks for 2026-07-03."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = REPO_ROOT / "ai-status.json"
LOG_PATH = REPO_ROOT / "ai-activity-log.jsonl"
AUTO_BY = "dispatch_management_console_fleet_finish_2026-07-03"
ARCHIVE = (
    "docs/04/pantheon_management_console_gap_2026-06-30/archive/"
    "management-fleet-finish-plan-2026-07-03.md"
)
PACKET = "docs/bff/execution-tasks/2026-07-03-management-console-fleet-finish/INDEX.md"
SOURCE_REF = {
    "archive": ARCHIVE,
    "packet": PACKET,
    "reaudit": (
        "docs/04/pantheon_management_console_gap_2026-06-30/archive/"
        "complete-reaudit-rerun-2026-07-02.md"
    ),
    "prior_adjustment_plan": (
        "docs/04/pantheon_management_console_gap_2026-06-30/archive/"
        "management-adjustment-development-plan-2026-07-02.md"
    ),
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


def task_ref(filename: str) -> str:
    return f"docs/bff/execution-tasks/2026-07-03-management-console-fleet-finish/{filename}"


TASKS = [
    {
        "id": "MGMT-FLEET-001",
        "title": "Management Console current-state guard",
        "summary_zh": (
            "先校準最新 dev、已合併 PR、open PR、髒 worktree 與 stale WIP；"
            "確認哪些 Management work 已完成、哪些仍需派工，禁止直接沿用舊 AI Ops WIP。"
        ),
        "phase": "Management Console fleet finish / state guard",
        "owner": "Codex",
        "reviewer": "Claude",
        "depends_on": [],
        "artifacts": [
            ARCHIVE,
            PACKET,
            task_ref("MGMT-FLEET-001-current-state-guard.md"),
        ],
        "acceptance": [
            "current-state archive records base commit, open PRs, stale WIP, completed work, and remaining work",
            "PR #2793 and PR #2794 are confirmed present in the base",
            "all downstream MGMT-FLEET tasks are confirmed still needed or explicitly superseded",
            "no source behavior changes are made by this guard task",
        ],
        "next": "Audit current dev before any Management fleet implementation starts.",
    },
    {
        "id": "MGMT-FLEET-002",
        "title": "Management AI/NL active workflow",
        "summary_zh": (
            "完成 /management/nl/ask 與 /management/ai/conversations 的可路由 active panel；"
            "使用真 BFF assistant client，補 loading/empty/degraded/success 測試與 hosted endpoint evidence。"
        ),
        "phase": "Management Console fleet finish / AI Ops",
        "owner": "Claude",
        "reviewer": "Codex",
        "depends_on": ["MGMT-FLEET-001"],
        "artifacts": [
            PACKET,
            task_ref("MGMT-FLEET-002-ai-ops-nl-workflow.md"),
            "execute-plans/src/entries/management-main.tsx",
            "execute-plans/src/lib/bff-v1/managementAssistant.ts",
        ],
        "acceptance": [
            "/management/nl/ask renders a route-specific active panel",
            "/management/ai/conversations renders a route-specific active panel",
            "AI ask action returns durable metadata or clear degraded state, not local-only success",
            "focused tests cover loading, empty, auth failure, backend degraded, and success states",
            "preview or hosted browser evidence proves intended Management AI BFF endpoints are called",
        ],
        "next": "Implement Management AI/NL workflow from current dev, not stale local WIP.",
    },
    {
        "id": "MGMT-FLEET-003",
        "title": "Management decision workbench",
        "summary_zh": (
            "把 human inbox、interventions、approvals、sentinel、governance、incidents、alerts/jobs "
            "整成一致的 decision/operations workbench，保留 canonical deep links。"
        ),
        "phase": "Management Console fleet finish / decision workbench",
        "owner": "Gemini",
        "reviewer": "Claude2",
        "depends_on": ["MGMT-FLEET-001"],
        "artifacts": [
            PACKET,
            task_ref("MGMT-FLEET-003-decision-workbench.md"),
            "execute-plans/src/entries/management-main.tsx",
        ],
        "acceptance": [
            "existing bookmarked decision and operations routes still resolve",
            "queues expose consistent owner, severity, status, evidence, and next-action columns",
            "route-specific panels replace generic repeated list shells",
            "write-looking actions are receipt-backed or explicitly disabled as non-production",
        ],
        "next": "Cluster decision and operations queues into one coherent operator workflow.",
    },
    {
        "id": "MGMT-FLEET-004",
        "title": "Management readiness suite",
        "summary_zh": (
            "完成 broker live、capital binding live、BFF HA、EP5、strict publish readiness；"
            "遷移或刪除 apps/management orphan readiness widgets。"
        ),
        "phase": "Management Console fleet finish / readiness",
        "owner": "Claude2",
        "reviewer": "Codex2",
        "depends_on": ["MGMT-FLEET-001"],
        "artifacts": [
            PACKET,
            task_ref("MGMT-FLEET-004-readiness-suite.md"),
            "apps/management/src/screens",
            "execute-plans/src/entries/management-main.tsx",
        ],
        "acceptance": [
            "direct readiness routes render active route panels with live or clearly degraded data",
            "go/no-go status, blocker count, source freshness, and evidence links are visible",
            "orphan readiness widgets are migrated, archived, or deleted with evidence",
            "preview or hosted evidence proves intended readiness BFF endpoint calls",
        ],
        "next": "Build readiness workflows and settle orphan widget ownership.",
    },
    {
        "id": "MGMT-FLEET-005",
        "title": "Management performance review suite",
        "summary_zh": (
            "把 ranking、persona league、portfolio book、performance attribution、trading pulse、cost "
            "整理成有 domain-specific columns 的 performance review workflows。"
        ),
        "phase": "Management Console fleet finish / performance review",
        "owner": "Codex2",
        "reviewer": "Gemini",
        "depends_on": ["MGMT-FLEET-001"],
        "artifacts": [
            PACKET,
            task_ref("MGMT-FLEET-005-performance-review-suite.md"),
            "execute-plans/src/entries/management-main.tsx",
            "docs/architecture/management-list-contract-baseline.json",
        ],
        "acceptance": [
            "each migrated performance route has route-specific columns, summaries, and degraded states",
            "list payloads stay bounded and do not add new list-contract smells",
            "detail evidence moves to explicit drilldowns or bounded previews",
            "browser probes prove intended BFF endpoint calls",
        ],
        "next": "Deepen performance review workflows without adding list payload debt.",
    },
    {
        "id": "MGMT-FLEET-006",
        "title": "Management registry and orphan prune",
        "summary_zh": (
            "盤點 apps/management widgets、historical aliases、duplicate route names、empty registries；"
            "逐一決定 migrate/redirect/demote/archive/delete，保留有效 operator viewpoints。"
        ),
        "phase": "Management Console fleet finish / prune",
        "owner": "Gemini2",
        "reviewer": "Codex",
        "depends_on": ["MGMT-FLEET-001"],
        "artifacts": [
            PACKET,
            task_ref("MGMT-FLEET-006-registry-orphan-prune.md"),
            "apps/management",
            "execute-plans/src/entries/management-main.tsx",
        ],
        "acceptance": [
            "every orphan or duplicate surface has migrate, redirect, demote, archive, or delete evidence",
            "deleted or archived surfaces are not imported by active Management builds",
            "old bookmarks redirect to canonical routes without duplicate UI",
            "valid operator jobs are not deleted solely because their table shells look similar",
        ],
        "next": "Prune false surfaces and document final route behavior.",
    },
    {
        "id": "MGMT-FLEET-007",
        "title": "Management command runner or demotion",
        "summary_zh": (
            "重掃所有 write-looking CTAs 與 capability studios；enabled action 必須有 command/receipt/audit/readback，"
            "否則明確 demote/disable。"
        ),
        "phase": "Management Console fleet finish / command safety",
        "owner": "Claude",
        "reviewer": "Codex2",
        "depends_on": [
            "MGMT-FLEET-002",
            "MGMT-FLEET-003",
            "MGMT-FLEET-004",
            "MGMT-FLEET-005",
            "MGMT-FLEET-006",
        ],
        "artifacts": [
            PACKET,
            task_ref("MGMT-FLEET-007-command-runner-demotion.md"),
            "execute-plans",
        ],
        "acceptance": [
            "no enabled production action succeeds by local toast alone",
            "runner-backed controls expose job id, status readback, trace/evidence, and failure reason",
            "demoted surfaces remain readable but remove production-looking execution CTAs",
            "remaining allow-list entries have owner, expiry, and linked follow-up",
        ],
        "next": "Burn down write CTA and capability runner/demotion debt after active workflows settle.",
    },
    {
        "id": "MGMT-FLEET-008",
        "title": "Management fleet finish closeout acceptance",
        "summary_zh": (
            "彙整所有 MGMT-FLEET PR/merge SHA、hosted route probes、BFF smoke、list-contract audit、write scan，"
            "完成 final closeout archive。"
        ),
        "phase": "Management Console fleet finish / closeout",
        "owner": "Codex",
        "reviewer": "Claude",
        "depends_on": [
            "MGMT-FLEET-002",
            "MGMT-FLEET-003",
            "MGMT-FLEET-004",
            "MGMT-FLEET-005",
            "MGMT-FLEET-006",
            "MGMT-FLEET-007",
        ],
        "artifacts": [
            ARCHIVE,
            PACKET,
            task_ref("MGMT-FLEET-008-closeout-acceptance.md"),
            "docs/architecture/management-list-contract-baseline.json",
        ],
        "acceptance": [
            "all prerequisite tasks are merged or explicitly superseded with evidence",
            "hosted route/control evidence shows no blank route, nav failure, or fake production write success",
            "management list-contract audit reports no new issues",
            "final archive lists adjusted, deleted/demoted, and deepened surfaces",
            "residual risks have owner, expiry, and follow-up task id",
        ],
        "next": "Close the fleet packet only after merged implementation and hosted proof exist.",
    },
]


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_state() -> dict:
    return json.loads(STATUS_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATUS_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_log(entry: dict) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def upsert_task(state: dict, task: dict) -> tuple[bool, dict]:
    tasks = state.setdefault("tasks", [])
    for index, existing in enumerate(tasks):
        if existing.get("id") == task["id"]:
            merged = {**existing, **task}
            if existing.get("status") and existing.get("status") != "todo":
                for key in PROGRESS_FIELDS:
                    if key in existing:
                        merged[key] = existing[key]
            tasks[index] = merged
            return False, merged
    tasks.append(task)
    return True, task


def assign_agent(state: dict, owner: str, task_id: str, timestamp: str, next_note: str, inserted: bool) -> None:
    for agent in state.get("agents", []):
        if agent.get("name") != owner:
            continue
        ids = agent.setdefault("current_task_ids", [])
        if task_id not in ids:
            ids.append(task_id)
        if inserted or not str(agent.get("next") or "").strip():
            agent["status"] = "waiting"
            agent["next"] = next_note
            agent["last_update"] = timestamp
        return


def remove_terminal_task_from_agents(state: dict, task_id: str) -> None:
    for agent in state.get("agents", []):
        ids = agent.get("current_task_ids")
        if isinstance(ids, list):
            agent["current_task_ids"] = [item for item in ids if item != task_id]


def main() -> int:
    state = load_state()
    timestamp = iso_now()
    for spec in TASKS:
        task = {
            "id": spec["id"],
            "title": spec["title"],
            "summary_zh": spec["summary_zh"],
            "phase": spec["phase"],
            "owner": spec["owner"],
            "reviewer": spec["reviewer"],
            "status": "todo",
            "depends_on": spec["depends_on"],
            "artifacts": spec["artifacts"],
            "acceptance": spec["acceptance"],
            "next": spec["next"],
            "last_update": timestamp,
            "task_class": "execution",
            "auto_created_by": AUTO_BY,
            "auto_generated": True,
            "delivery_layer": "primary",
            "fleet_lane": "management-console-fleet-finish",
            "source_ref": SOURCE_REF,
        }
        inserted, current = upsert_task(state, task)
        if current.get("status") in TERMINAL_STATUSES:
            remove_terminal_task_from_agents(state, spec["id"])
        else:
            assign_agent(state, spec["owner"], spec["id"], timestamp, spec["next"], inserted)
        if inserted:
            append_log(
                {
                    "ts": timestamp,
                    "agent": os.environ.get("AI_NAME", "Codex"),
                    "type": "assign",
                    "task_id": spec["id"],
                    "message": f"Assigned {spec['id']} to {spec['owner']} with reviewer {spec['reviewer']}",
                }
            )
        action = "CREATE" if inserted else "UPSERT"
        print(f"{action} {spec['id']} owner={spec['owner']} reviewer={spec['reviewer']}")
    state["updated_at"] = timestamp
    save_state(state)
    print("Done. Run `python3 scripts/ai_status.py sync` to refresh generated status views.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
