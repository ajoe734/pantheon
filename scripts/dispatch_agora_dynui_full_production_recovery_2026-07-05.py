#!/usr/bin/env python3
"""Dispatch Agora DYNUI full production recovery tasks for 2026-07-05."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(os.environ.get("PANTHEON_STATUS_ROOT", str(DEFAULT_REPO_ROOT))).resolve()
STATUS_PATH = REPO_ROOT / "ai-status.json"
LOG_PATH = REPO_ROOT / "ai-activity-log.jsonl"
ARCHIVE_TASKS_PATH = REPO_ROOT / "ai-task-archive" / "tasks"
AUTO_BY = "dispatch_agora_dynui_full_production_recovery_2026-07-05"
ARCHIVE = "docs/04/pantheon_agora_dynui_full_production_recovery_2026-07-05/INDEX.md"
PACKET = "docs/bff/execution-tasks/2026-07-05-agora-dynui-full-production-recovery/INDEX.md"
SOURCE_REF = {
    "archive": ARCHIVE,
    "packet": PACKET,
    "prior_packet": "docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/INDEX.md",
    "prior_hosted_gate": "docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-006-hosted-e2e-publish-gate.md",
    "live_bff_deploy_sha": "341924e2c29ee185c925b8f4291485beb08e851e",
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
    "blocker",
    "blocked_on",
}
TERMINAL_STATUSES = {"done", "superseded", "cancelled"}
GENERIC_NEXT_MESSAGES = {
    None,
    "",
    "Assignment created",
    "Assignment created from Agora DYNUI full production recovery packet",
}


TASKS = [
    {
        "id": "AG-DYNUI-FULL-001",
        "title": "Recover Agora design truth and production gap matrix",
        "summary_zh": (
            "重新找回 AI Trading Desk Design.zip 或正式確認 closure pack 為 canonical source；"
            "建立設計稿/現有 FE/live BFF/舊 PROD closeout 的差異矩陣，標出哪些能繼續、哪些必須 blocker。"
        ),
        "phase": "Agora DYNUI Full Production Recovery / Wave 0 source truth",
        "owner": "Codex",
        "reviewer": "Claude2",
        "depends_on": [],
        "artifacts": [
            ARCHIVE,
            PACKET,
            "docs/04/agora_design_pack_dynui_2026-06-28/",
            "Pantheon_Agora_Design_Closure_Pack_2026-06-20.zip",
            "Pantheon_Agora_Design_Closure_Round2_v1_3_2026-06-21.zip",
            "/home/lupin/code/pantheon/AI%20Trading%20Desk%20Design.zip",
        ],
        "acceptance": [
            "record whether the exact AI Trading Desk Design.zip exists and where",
            "if missing, record the exact blocker and prove whether closure packs are acceptable replacements",
            "produce a screen/state parity matrix for Strategy Workshop, readiness, Trading Room, proposal, workspace, revision, version, rollback",
            "identify every gap that must be code work versus evidence-only work",
            "do not approve frontend visual completion from memory or stale screenshots",
        ],
        "next": "Recover source truth and write the gap matrix before visual parity closeout.",
        "metadata": {"wave": 0, "fleet_lane": "agora-dynui-source-truth"},
    },
    {
        "id": "AG-DYNUI-FULL-002",
        "title": "Implement live Strategy Workshop cards and readiness BFF",
        "summary_zh": (
            "補齊 live BFF /bff/agora/workshops/{id}/cards、/readiness、/readiness/reassess；"
            "資料必須來自 scoped workshop state/store，不能由前端 fixture 或假資料代替。"
        ),
        "phase": "Agora DYNUI Full Production Recovery / Wave 1 workshop BFF",
        "owner": "Codex",
        "reviewer": "Codex2",
        "depends_on": ["AG-DYNUI-FULL-001"],
        "artifacts": [
            "services/control-plane/bff/agora/strategy_workshop/router.py",
            "services/control-plane/bff/agora/strategy_workshop/store.py",
            "services/control-plane/bff/agora/strategy_workshop/test_*.py",
            "services/control-plane/specs/agora/",
            "execute-plans/src/lib/bff-v1/agora/workshops.ts",
        ],
        "acceptance": [
            "live GET /bff/agora/workshops/{id}/cards returns 200 for an existing scoped workshop",
            "live GET /bff/agora/workshops/{id}/readiness returns 200 for an existing scoped workshop",
            "live POST /bff/agora/workshops/{id}/readiness/reassess updates or returns a fresh readiness assessment",
            "unknown or cross-tenant workshop ids return proper 404/403 envelopes, not INTERNAL_ERROR",
            "backend tests cover dict and OperatorIdentity identities",
            "OpenAPI/spec or contract snapshot updated if the route manifest requires it",
            "post-deploy live curl proof is recorded",
        ],
        "next": "Implement missing workshop cards/readiness routes; current live routes are 404.",
        "metadata": {"wave": 1, "fleet_lane": "agora-workshop-readiness-bff"},
    },
    {
        "id": "AG-DYNUI-FULL-003",
        "title": "Materialize ready Strategy Workshop output into Trading Room",
        "summary_zh": (
            "把 workshop readiness/evidence 轉成可查詢的 ready strategy/version，投影進 "
            "/bff/agora/trading-room aggregate；live strategies 不能再是空陣列。"
        ),
        "phase": "Agora DYNUI Full Production Recovery / Wave 2 ready strategy projection",
        "owner": "Codex2",
        "reviewer": "Codex",
        "depends_on": ["AG-DYNUI-FULL-002"],
        "artifacts": [
            "services/control-plane/bff/agora/strategy_workshop/",
            "services/control-plane/bff/agora/trading_room/router.py",
            "services/control-plane/bff/agora/trading_room/store.py",
            "services/control-plane/bff/agora/trading_room/test_trading_room.py",
            "services/control-plane/specs/agora/",
        ],
        "acceptance": [
            "a workshop whose readiness reaches trading_room creates or exposes a strategy_id and strategy_version",
            "GET /bff/agora/trading-room returns strategies.length > 0 for that scoped user after readiness",
            "GET /bff/agora/trading-room/strategies/{strategy_id} returns strategy detail for the materialized strategy",
            "projection is tenant/user scoped and does not leak strategies across identities",
            "readiness state, evidence refs, strategy version, and dashboard recipe/workspace refs are durable enough for the E2E",
            "post-deploy live curl proof shows non-empty strategies from a real created/restored workflow",
        ],
        "next": "Wire ready strategy projection after AG-DYNUI-FULL-002 exposes readiness.",
        "metadata": {"wave": 2, "fleet_lane": "agora-ready-strategy-projection"},
    },
    {
        "id": "AG-DYNUI-FULL-004",
        "title": "Wire frontend Workshop to Trading Room handoff",
        "summary_zh": (
            "修 execute-plans Strategy Workshop CTA：ready 後必須帶 strategyId/strategyVersion/readiness context "
            "進 /agora/trading-room/:strategyId，不可只 navigate('/agora/trading-room')。"
        ),
        "phase": "Agora DYNUI Full Production Recovery / Wave 2 frontend handoff",
        "owner": "Codex2",
        "reviewer": "Codex",
        "depends_on": ["AG-DYNUI-FULL-002"],
        "artifacts": [
            "execute-plans/src/routes/agora.tsx",
            "execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx",
            "execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx",
            "execute-plans/src/lib/bff-v1/agora/workshops.ts",
            "execute-plans/src/lib/bff-v1/agora/tradingRoom.ts",
            "execute-plans/e2e/agora-winner-branch-hosted.spec.ts",
        ],
        "acceptance": [
            "Add to Trading Room is enabled only by live readiness reaching trading_room",
            "CTA navigates to /agora/trading-room/{strategyId}?strategyVersion={version}",
            "explicit strategy route loads even when aggregate default route initially has no selected strategy",
            "frontend does not fabricate ready strategies if BFF readiness is blocked",
            "unit tests cover blocked, ready, missing strategy id, and explicit strategy route cases",
            "execute-plans PR, checks, merge SHA, dev FE deploy, and hosted screenshot are recorded",
        ],
        "next": "Update frontend handoff once AG-DYNUI-FULL-002 route contracts are available.",
        "metadata": {"wave": 2, "fleet_lane": "agora-frontend-handoff"},
    },
    {
        "id": "AG-DYNUI-FULL-005",
        "title": "Connect live dynamic workspace workflow end to end",
        "summary_zh": (
            "用真實 ready strategy 串 proposal generation、accept、workspace load、layout patch、"
            "widget revision、version history、rollback；禁止用 page.route fixture 當通過證明。"
        ),
        "phase": "Agora DYNUI Full Production Recovery / Wave 3 live dynamic workflow",
        "owner": "Copilot",
        "reviewer": "Codex",
        "depends_on": ["AG-DYNUI-FULL-003", "AG-DYNUI-FULL-004"],
        "artifacts": [
            "services/control-plane/bff/agora/trading_room/router.py",
            "services/control-plane/bff/agora/trading_room/store.py",
            "services/control-plane/bff/agora/trading_room/test_trading_room.py",
            "execute-plans/src/agora/trading-room/",
            "execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx",
            "execute-plans/src/lib/bff-v1/agora/tradingRoom.ts",
        ],
        "acceptance": [
            "live proposal generation succeeds for a real ready strategy/version",
            "live accept creates a workspace and returns an ETag",
            "live workspace load returns the accepted workspace",
            "live layout PATCH requires and honors If-Match/idempotency",
            "live widget revision proposal and accept persist and update workspace/version state",
            "live version history lists all generated versions",
            "live rollback creates a new version and visible workspace state",
            "all write routes preserve tenant/user scope and WidgetSpec allowlists",
            "evidence includes live transcript with request ids and no page.route fixtures",
        ],
        "next": "Connect and prove the dynamic workspace chain after strategy projection and FE handoff land.",
        "metadata": {"wave": 3, "fleet_lane": "agora-live-dynamic-workflow"},
    },
    {
        "id": "AG-DYNUI-FULL-006",
        "title": "Replace fixture-backed hosted E2E with production gate",
        "summary_zh": (
            "重寫 hosted Winner Branch E2E：production gate path 不准 page.route/mock BFF；修復 execute-plans "
            "FE-BFF Integration Gate failure，desktop/mobile screenshots 必須來自 live flow。"
        ),
        "phase": "Agora DYNUI Full Production Recovery / Wave 4 no-fixture hosted gate",
        "owner": "Codex2",
        "reviewer": "Codex",
        "depends_on": ["AG-DYNUI-FULL-005"],
        "artifacts": [
            "execute-plans/e2e/agora-winner-branch-hosted.spec.ts",
            "execute-plans/.github/workflows/",
            "execute-plans/scripts/",
            "docs/bff/execution-tasks/2026-07-05-agora-dynui-full-production-recovery/",
        ],
        "acceptance": [
            "production-gate E2E contains no page.route/mock fulfillments for Agora BFF routes",
            "E2E creates or restores a real workshop and reaches readiness through live BFF",
            "E2E joins Trading Room with real strategy/version route context",
            "E2E completes proposal, accept, grid edit, widget revision, version history, rollback live",
            "desktop and mobile screenshots show the live production UI states",
            "execute-plans FE-BFF Integration Gate is success on PR and dev push",
            "Dev FE Deploy succeeds and deployment.json matches the merge SHA",
        ],
        "next": "Replace the fixture-backed E2E only after live workflow APIs are ready.",
        "metadata": {"wave": 4, "fleet_lane": "agora-no-fixture-hosted-e2e"},
    },
    {
        "id": "AG-DYNUI-FULL-007",
        "title": "Agora DYNUI production closeout and publish proof",
        "summary_zh": (
            "統整 FULL-001~006 的 PR、CI、deploy、live curl、browser screenshots、設計 parity；"
            "只有所有 production gate 都綠時才能關閉，否則建立 blocker 而不是 done。"
        ),
        "phase": "Agora DYNUI Full Production Recovery / Wave 5 closeout",
        "owner": "Codex",
        "reviewer": "Codex2",
        "depends_on": ["AG-DYNUI-FULL-006"],
        "artifacts": [
            ARCHIVE,
            PACKET,
            "docs/bff/execution-tasks/2026-07-05-agora-dynui-full-production-recovery/AG-DYNUI-FULL-007-closeout.md",
            "ai-status.json",
            "/tmp/agora-dynui-full-production-*.png",
            "/tmp/agora-dynui-full-production-*.json",
        ],
        "acceptance": [
            "all AG-DYNUI-FULL tasks are done or explicitly superseded with reviewer approval",
            "final evidence includes design parity matrix and residual risk audit",
            "live /trading-room aggregate has non-empty strategies after workflow proof",
            "live /workshops/{id}/cards and /readiness return 200",
            "hosted browser proof shows full flow without Failed to load Trading Room",
            "execute-plans and Pantheon required checks are green",
            "dev deploy SHAs are recorded for both frontend and BFF/runtime changes",
            "no fixture-backed E2E is cited as production proof",
        ],
        "next": "Wait for AG-DYNUI-FULL-006; do not close until every production gate is proven live.",
        "metadata": {"wave": 5, "fleet_lane": "agora-production-closeout"},
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


def find_task(state: dict, task_id: str) -> dict | None:
    for task in state.get("tasks", []):
        if task.get("id") == task_id:
            return task
    return None


def archived_terminal_task(task_id: str) -> bool:
    archive_path = ARCHIVE_TASKS_PATH / f"{task_id}.json"
    if not archive_path.exists():
        return False
    try:
        snapshot = json.loads(archive_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    terminal_status = str(
        snapshot.get("terminal_status")
        or snapshot.get("status")
        or snapshot.get("outcome")
        or ""
    ).strip().lower()
    return terminal_status in TERMINAL_STATUSES


def upsert_task(state: dict, task: dict) -> tuple[bool, str]:
    tasks = state.setdefault("tasks", [])
    for index, existing in enumerate(tasks):
        if existing.get("id") == task["id"]:
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


def assign_agent(state: dict, owner: str, task_id: str, timestamp: str, next_note: str, inserted: bool) -> None:
    for agent in state.get("agents", []):
        if agent.get("name") != owner:
            continue
        ids = agent.setdefault("current_task_ids", [])
        if task_id not in ids:
            ids.append(task_id)
        if inserted or agent.get("next") in GENERIC_NEXT_MESSAGES:
            agent["status"] = "waiting"
            agent["next"] = next_note
            agent["last_update"] = timestamp
        return


def main() -> int:
    state = load_state()
    timestamp = iso_now()
    task_ids = [task["id"] for task in TASKS]
    for spec in TASKS:
        if find_task(state, spec["id"]) is None and archived_terminal_task(spec["id"]):
            remove_terminal_task_from_agents(state, spec["id"])
            print(f"SKIP   {spec['id']:18} archived terminal task")
            continue
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
            "mutates_canonical": True,
            "source_ref": SOURCE_REF,
            "production_recovery_children": task_ids,
        }
        task.update(spec["metadata"])
        inserted, status_after = upsert_task(state, task)
        if status_after in TERMINAL_STATUSES:
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
        print(f"{action} {spec['id']:18} owner={spec['owner']:8} reviewer={spec['reviewer']:8}")
    state["updated_at"] = timestamp
    save_state(state)
    print("Done. Run `python3 scripts/ai_status.py sync` only if generated views need refresh.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
