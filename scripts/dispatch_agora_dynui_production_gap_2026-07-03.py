#!/usr/bin/env python3
"""Dispatch Agora dynamic UI production-gap tasks for 2026-07-03."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

REPO_ROOT = Path(os.environ.get("PANTHEON_REPO_ROOT", Path(__file__).resolve().parents[1])).resolve()
STATUS_PATH = REPO_ROOT / "ai-status.json"
LOG_PATH = REPO_ROOT / "ai-activity-log.jsonl"
AUTO_BY = "dispatch_agora_dynui_production_gap_2026-07-03"
ARCHIVE = "docs/04/pantheon_agora_dynui_production_gap_2026-07-03/INDEX.md"
PACKET = "docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/INDEX.md"
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

GLOBAL_RULES = [
    "Do not rebuild from imagination or static screenshots.",
    "Keep V10/V11 dynamic UI behavior: proposal, grid edit, widget revision, version history, rollback.",
    "Use strict BFF contracts, auth, scope isolation, validators, and widget allowlists.",
    "If the design source is missing or conflicts with code, raise a blocker with the exact file/contract.",
    "Close only after branch, PR, checks, merge, deploy when needed, and hosted proof.",
]

TASKS = [
    {
        "id": "AG-DYNUI-PROD-001",
        "title": "Restore Agora DYNUI source and task truth",
        "summary_zh": "修復 Agora DYNUI 設計來源與任務真相：確認 AI Trading Desk Design.zip 或替代 closure pack 的 canonical 位置，恢復缺失 archive/task truth，列出舊 DYNUI PR 完成與未完成的邊界，讓後續 fleet 能接續。",
        "phase": "Agora DYNUI Production Gap / Source Truth",
        "owner": "Codex",
        "reviewer": "Claude",
        "depends_on": [],
        "artifacts": [
            ARCHIVE,
            PACKET,
            "docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-001-source-task-truth.md",
            "ai-task-archive/tasks/",
            ".orchestrator/task-briefs/",
            "/home/lupin/code/execute-plans",
            "/home/lupin/code/pantheon/.fe-ep",
        ],
        "acceptance": [
            "canonical design source is recorded or exact blocker is opened",
            "canonical frontend deploy source is recorded and stale nested checkout risk is resolved or assigned",
            "completed vs incomplete DYNUI work is reconciled with PR and merge SHA evidence",
            "missing archive/task truth no longer blocks downstream production-gap tasks",
        ],
    },
    {
        "id": "AG-DYNUI-PROD-002",
        "title": "Agora standalone workbench shell",
        "summary_zh": "修 execute-plans Agora shell 架構：/agora/* 不應只是被包在 Management PlatformShell 裡的 tab skeleton；建立符合設計稿的 Agora workbench shell 或提交明確批准的例外，並保留 auth/live 狀態。",
        "phase": "Agora DYNUI Production Gap / Shell Architecture",
        "owner": "Claude",
        "reviewer": "Codex",
        "depends_on": ["AG-DYNUI-PROD-001"],
        "artifacts": [
            "execute-plans/src/App.tsx",
            "execute-plans/src/platform/PlatformShell.tsx",
            "execute-plans/src/agora/TradingDeskLayout.tsx",
            "execute-plans/src/routes/agora.tsx",
            "docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-002-standalone-workbench-shell.md",
        ],
        "acceptance": [
            "hosted /agora/trading-room shell is intentionally standalone or has documented approved exception",
            "Agora workbench IA is not reduced to the old three-tab empty shell",
            "desktop and mobile screenshots show corrected shell",
        ],
    },
    {
        "id": "AG-DYNUI-PROD-003",
        "title": "Trading Room default dynamic entry",
        "summary_zh": "修 /agora/trading-room 預設路徑：沒有 strategyId/strategyVersion 時不得停在 All Strategies 空殼；必須由 BFF data 進入設計稿的動態 entry/readiness/workshop/proposal workflow。",
        "phase": "Agora DYNUI Production Gap / Default Route",
        "owner": "Claude2",
        "reviewer": "Codex",
        "depends_on": ["AG-DYNUI-PROD-001"],
        "artifacts": [
            "execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx",
            "execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx",
            "execute-plans/src/lib/bff-v1/agora/tradingRoom.ts",
            "services/control-plane/bff/agora/trading_room.py",
            "docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-003-default-route-dynamic-entry.md",
        ],
        "acceptance": [
            "hosted default route never lands on inert empty table shell without meaningful dynamic next action",
            "ready strategy path reaches proposal preview without manual URL surgery",
            "no fake hardcoded strategy data is introduced",
        ],
    },
    {
        "id": "AG-DYNUI-PROD-004",
        "title": "Trading Room error diagnostics and stale bundle recovery",
        "summary_zh": "修 Trading Room production error state：不能只顯示 Failed to load Trading Room；保留 BFF status/code/request/correlation，提供 retry/safe reload，並讓 probe 能抓出 stale bundle/cache/header 問題。",
        "phase": "Agora DYNUI Production Gap / Observability",
        "owner": "Codex2",
        "reviewer": "Claude",
        "depends_on": [],
        "artifacts": [
            "execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx",
            "execute-plans/src/lib/bff-v1/agora/tradingRoom.ts",
            "deploy/caddy/dev.Caddyfile.tmpl",
            "deploy/caddy/sync-caddy.sh",
            "docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-004-error-cache-diagnostics.md",
        ],
        "acceptance": [
            "root error state exposes safe actionable diagnostics and retry behavior",
            "hosted probe fails on generic-only Failed to load Trading Room",
            "cache-header policy remains verified after changes",
        ],
    },
    {
        "id": "AG-DYNUI-PROD-005",
        "title": "Close Agora dynamic workflow wiring",
        "summary_zh": "補齊 V11 dynamic workflow：proposal generation/accept、workspace load、layout patch、widget revision proposal、apply/keep-copy、version history、rollback 都必須走 strict BFF 和 allowlisted WidgetSpec/ChartSpec。",
        "phase": "Agora DYNUI Production Gap / Dynamic Workflow",
        "owner": "Claude",
        "reviewer": "Codex2",
        "depends_on": ["AG-DYNUI-PROD-002", "AG-DYNUI-PROD-003", "AG-DYNUI-PROD-004"],
        "artifacts": [
            "execute-plans/src/agora/trading-room/WorkspaceProposalPreview.tsx",
            "execute-plans/src/agora/trading-room/WorkspaceGridEditor.tsx",
            "execute-plans/src/agora/trading-room/WorkspaceWidgetRevisionDrawer.tsx",
            "execute-plans/src/lib/bff-v1/agora/tradingRoom.ts",
            "services/control-plane/bff/agora/trading_room.py",
            "services/control-plane/specs/agora/",
            "docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-005-dynamic-workflow-closeout.md",
        ],
        "acceptance": [
            "full V11 workflow succeeds through strict BFF calls",
            "idempotency optimistic concurrency scope isolation and widget allowlists are tested",
            "no arbitrary frontend code or fake success fallback is accepted",
        ],
    },
    {
        "id": "AG-DYNUI-PROD-006",
        "title": "Hosted Winner Branch E2E publish gate",
        "summary_zh": "最後 production gate：用 hosted dev FE + live BFF 跑 Winner Branch V10-to-V11 E2E，覆蓋 Strategy Workshop、readiness、join Trading Room、proposal、accept、grid edit、widget revision、version history、rollback、desktop/mobile screenshots。",
        "phase": "Agora DYNUI Production Gap / Hosted Acceptance",
        "owner": "Codex",
        "reviewer": "Claude2",
        "depends_on": [
            "AG-DYNUI-PROD-001",
            "AG-DYNUI-PROD-002",
            "AG-DYNUI-PROD-003",
            "AG-DYNUI-PROD-004",
            "AG-DYNUI-PROD-005",
        ],
        "artifacts": [
            "execute-plans/tests/",
            "execute-plans/playwright.config.ts",
            "docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-006-hosted-e2e-publish-gate.md",
            "/tmp/agora-dynui-prod-e2e-*.png",
            "/tmp/agora-dynui-prod-e2e-*.json",
        ],
        "acceptance": [
            "hosted E2E passes against dev FE and live BFF",
            "screenshots do not show old empty Trading Desk shell",
            "PR merge SHAs deploy run IDs and live probe artifacts are recorded before closeout",
        ],
    },
]

TASK_BRIEFS = {
    "AG-DYNUI-PROD-001": "docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-001-source-task-truth.md",
    "AG-DYNUI-PROD-002": "docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-002-standalone-workbench-shell.md",
    "AG-DYNUI-PROD-003": "docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-003-default-route-dynamic-entry.md",
    "AG-DYNUI-PROD-004": "docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-004-error-cache-diagnostics.md",
    "AG-DYNUI-PROD-005": "docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-005-dynamic-workflow-closeout.md",
    "AG-DYNUI-PROD-006": "docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-006-hosted-e2e-publish-gate.md",
}


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_state() -> dict:
    return json.loads(STATUS_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATUS_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_log(entry: dict) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def upsert_task(state: dict, spec: dict, timestamp: str) -> tuple[bool, dict]:
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
        "acceptance": [*spec["acceptance"], *GLOBAL_RULES],
        "next": "Assignment created from Agora DYNUI production-gap packet.",
        "last_update": timestamp,
        "task_class": "execution",
        "auto_created_by": AUTO_BY,
        "source_ref": {
            "archive": ARCHIVE,
            "packet": PACKET,
            "task_brief": TASK_BRIEFS[spec["id"]],
        },
        "delivery_layer": "primary",
        "mutates_canonical": True,
        "fleet_lane": "agora-dynui-production-gap",
    }
    tasks = state.setdefault("tasks", [])
    for index, existing in enumerate(tasks):
        if existing.get("id") == spec["id"]:
            merged = {**existing, **task}
            if existing.get("status") and existing.get("status") != "todo":
                for key in PROGRESS_FIELDS:
                    if key in existing:
                        merged[key] = existing[key]
            tasks[index] = merged
            return False, merged
    tasks.append(task)
    return True, task


def assign_agent(state: dict, owner: str, task_id: str, timestamp: str, inserted: bool) -> None:
    for agent in state.get("agents", []):
        if agent.get("name") != owner:
            continue
        ids = agent.setdefault("current_task_ids", [])
        if task_id not in ids:
            ids.append(task_id)
        if inserted or not str(agent.get("next") or "").strip():
            agent["status"] = "waiting"
            agent["next"] = f"Pick up {task_id} from Agora DYNUI production-gap packet."
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
        inserted, current = upsert_task(state, spec, timestamp)
        if current.get("status") in TERMINAL_STATUSES:
            remove_terminal_task_from_agents(state, spec["id"])
        else:
            assign_agent(state, spec["owner"], spec["id"], timestamp, inserted)
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
        print(f"{'CREATE' if inserted else 'UPSERT'} {spec['id']} owner={spec['owner']} reviewer={spec['reviewer']}")
    state["updated_at"] = timestamp
    save_state(state)
    print("Done. Do not run ai_status.py sync for this packet until board-pruning behavior is repaired.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
