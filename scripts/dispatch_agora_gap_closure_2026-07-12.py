#!/usr/bin/env python3
"""Dispatch Agora gap-closure tasks (AG-GAP-001..013) for 2026-07-12.

Source: docs/04/pantheon_agora_gap_assessment_2026-07-12/INDEX.md
Packet: docs/bff/execution-tasks/2026-07-12-agora-gap-closure/INDEX.md
"""
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
AUTO_BY = "dispatch_agora_gap_closure_2026-07-12"
ARCHIVE = "docs/04/pantheon_agora_gap_assessment_2026-07-12/INDEX.md"
PACKET = "docs/bff/execution-tasks/2026-07-12-agora-gap-closure/INDEX.md"
SOURCE_REF = {
    "archive": ARCHIVE,
    "packet": PACKET,
    "prior_packet": "docs/bff/execution-tasks/2026-07-05-agora-dynui-full-production-recovery/INDEX.md",
    "prior_gate": "docs/bff/execution-tasks/2026-07-08-agora-live-tabs-production/AG-DYNUI-LIVE-TABS-GATE-011.md",
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
    "Assignment created from Agora gap-closure packet",
}

OWNER = "Codex"
REVIEWER = "Codex2"
BRIEF_DIR = "docs/bff/execution-tasks/2026-07-12-agora-gap-closure"


def _brief(name: str) -> str:
    return f"{BRIEF_DIR}/{name}"


TASKS = [
    {
        "id": "AG-GAP-001",
        "title": "Enable and prove durable workshop Postgres backend on dev",
        "summary_zh": "確認/落地 dev BFF 的 AGORA_WORKSHOP_STORE_BACKEND=postgres（版本化 deploy config），加 startup backend log；live 證明 workshop 重啟後仍在。",
        "brief": _brief("AG-GAP-001-workshop-postgres-live.md"),
        "depends_on": [],
        "wave": 0,
        "fleet_lane": "agora-gap-persistence",
        "next": "Verify dev workshop store backend; enforce postgres via versioned deploy config with restart-persistence proof.",
    },
    {
        "id": "AG-GAP-002",
        "title": "Durable Postgres store for trading_room",
        "summary_zh": "trading_room in-memory 單例改為可選 Postgres backend（比照 PostgresWorkshopStore），保留 no_order_route_proof 與 ETag 不變式；live 重啟持久化證明。",
        "brief": _brief("AG-GAP-002-trading-room-postgres.md"),
        "depends_on": ["AG-GAP-001"],
        "wave": 0,
        "fleet_lane": "agora-gap-persistence",
        "next": "Implement Postgres trading_room store after AG-GAP-001 lands the backend convention.",
    },
    {
        "id": "AG-GAP-003",
        "title": "Durable Postgres store for research",
        "summary_zh": "research plans/runs/artifacts 與 candidate pools 落 Postgres（目前純 in-memory）；保留 plan-first 治理與 scope 隔離。",
        "brief": _brief("AG-GAP-003-research-postgres.md"),
        "depends_on": ["AG-GAP-001"],
        "wave": 0,
        "fleet_lane": "agora-gap-persistence",
        "next": "Implement Postgres research store after AG-GAP-001 lands the backend convention.",
    },
    {
        "id": "AG-GAP-004",
        "title": "Durable Postgres store for dashboard recipes",
        "summary_zh": "dashboard recipe 的 module-level dict 抽成 store 介面 + Postgres backend；保留 ETag/版本/rollback 語意。",
        "brief": _brief("AG-GAP-004-dashboard-postgres.md"),
        "depends_on": ["AG-GAP-001"],
        "wave": 0,
        "fleet_lane": "agora-gap-persistence",
        "next": "Implement Postgres dashboard store after AG-GAP-001 lands the backend convention.",
    },
    {
        "id": "AG-GAP-005",
        "title": "Contract honesty: resolve 501 routes + refresh compatibility manifest",
        "summary_zh": "v1_1 OpenAPI 承諾的 6 條 workshop 501 stub 路由逐條實作或正式標註 deferred；dev-compatibility-manifest 從 2026-06-21 pending 更新到 v1_5 現況。",
        "brief": _brief("AG-GAP-005-contract-honesty.md"),
        "depends_on": [],
        "wave": 0,
        "fleet_lane": "agora-gap-contract",
        "next": "Audit every 501-stub route against OpenAPI; implement or annotate, then regenerate the compatibility manifest.",
    },
    {
        "id": "AG-GAP-006",
        "title": "Migrate identity/personalization/shadow routes out of main.py",
        "summary_zh": "把 main.py 舊軌的 Agora sessions/ask/inbox 與 memory/insights 行為不變地搬進空殼 sub-router，收斂雙軌路由架構；合約測試綠為 gate。",
        "brief": _brief("AG-GAP-006-mainpy-route-migration.md"),
        "depends_on": [],
        "wave": 1,
        "fleet_lane": "agora-gap-architecture",
        "next": "Move identity-family routes first; behavior-preserving, contract tests unchanged.",
    },
    {
        "id": "AG-GAP-007",
        "title": "Fix /bff/agora/capabilities mismatch + clean dev probe residue",
        "summary_zh": "capabilities 端點回空陣列與 /me granted_capabilities 不一致，修投影 + 合約測試；清 dev journal 的 dry-run 探測殘留（記錄 ops 程序）。",
        "brief": _brief("AG-GAP-007-capabilities-mismatch.md"),
        "depends_on": [],
        "wave": 1,
        "fleet_lane": "agora-gap-hygiene",
        "next": "Fix capabilities projection to agree with /me; documented cleanup of dev journal probe entries.",
    },
    {
        "id": "AG-GAP-008",
        "title": "Implement typed Trading Room SSE stream",
        "summary_zh": "把 /bff/agora/trading-room/stream 從空 SSE stub 改為 typed event stream（比照 workshop SSE，first-ack<2s，scope 隔離）。",
        "brief": _brief("AG-GAP-008-trading-room-typed-sse.md"),
        "depends_on": [],
        "wave": 1,
        "fleet_lane": "agora-gap-streaming",
        "next": "Model on the working workshop SSE; emit events from trading_room store mutations.",
    },
    {
        "id": "AG-GAP-009",
        "title": "Real PrivateContentStore replacing priv-content-stub refs",
        "summary_zh": "依 sw001 deep closure 實作 PrivateContentStore（no-list、owner-scoped、pcnt_ULID ref、redacted_summary），移除 priv-content-stub:// 佔位。",
        "brief": _brief("AG-GAP-009-private-content-store.md"),
        "depends_on": ["AG-GAP-001"],
        "wave": 1,
        "fleet_lane": "agora-gap-privacy",
        "next": "Implement the deep-closure PrivateContentStore on the AG-GAP-001 Postgres backend.",
    },
    {
        "id": "AG-GAP-010",
        "title": "Declare design parity baseline (design zip lost)",
        "summary_zh": "最後一次記錄式搜尋 AI Trading Desk Design.zip；找不到就正式宣告遺失，改以 closure pack 規格 + TABS-GATE-011 截圖（釘 deploy SHA）為 parity 基準。",
        "brief": _brief("AG-GAP-010-design-parity-baseline.md"),
        "depends_on": [],
        "wave": 2,
        "fleet_lane": "agora-gap-docs",
        "next": "Documented final search, then merge the baseline declaration and update FULL-008 references.",
    },
    {
        "id": "AG-GAP-011",
        "title": "Reconcile nested FE checkouts; enforce canonical execute-plans",
        "summary_zh": "盤點 .fe-ep 與 .fe-human-inbox-persona-focus 未推送的工作、salvage 後清除 stale checkout；成文規則：FE 只經 ajoe734/execute-plans@dev。",
        "brief": _brief("AG-GAP-011-fe-checkout-hygiene.md"),
        "depends_on": [],
        "wave": 2,
        "fleet_lane": "agora-gap-hygiene",
        "next": "Salvage unpushed FE work, remove stale nested checkouts, write the canonical-repo rule.",
    },
    {
        "id": "AG-GAP-012",
        "title": "12-block completeness additive contract (bundle v1_6)",
        "summary_zh": "以 additive bundle v1_6 定 12 具名 Winner Branch block 的 completeness 合約，7 維度相容映射；BFF readiness 投影跟上，舊 bundle byte 不動。",
        "brief": _brief("AG-GAP-012-twelve-block-completeness.md"),
        "depends_on": [],
        "wave": 2,
        "fleet_lane": "agora-gap-contract",
        "next": "Author v1_6 additive completeness contract with the 7-to-12 compatibility mapping.",
    },
    {
        "id": "AG-GAP-013",
        "title": "Agora market-data activation readback (SRCLIVE line)",
        "summary_zh": "SRCLIVE-001 live activation 驗收後，把真實 ingest 資料投影進 /bff/agora/markets 與 daily watchlist/signals；空的先掛 blocker 不得空手 done。",
        "brief": _brief("AG-GAP-013-market-data-activation.md"),
        "depends_on": [],
        "wave": 2,
        "fleet_lane": "agora-gap-data",
        "next": "Externally gated on SRCLIVE-001 acceptance; record a blocker until that evidence exists.",
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
    for spec in TASKS:
        if find_task(state, spec["id"]) is None and archived_terminal_task(spec["id"]):
            remove_terminal_task_from_agents(state, spec["id"])
            print(f"SKIP   {spec['id']:12} archived terminal task")
            continue
        task = {
            "id": spec["id"],
            "title": spec["title"],
            "summary_zh": spec["summary_zh"],
            "phase": f"Agora Gap Closure 2026-07-12 / Wave {spec['wave']}",
            "owner": OWNER,
            "reviewer": REVIEWER,
            "status": "todo",
            "depends_on": spec["depends_on"],
            "artifacts": [ARCHIVE, PACKET, spec["brief"]],
            "acceptance": [f"all acceptance criteria in {spec['brief']}"],
            "next": spec["next"],
            "last_update": timestamp,
            "task_class": "execution",
            "auto_created_by": AUTO_BY,
            "auto_generated": True,
            "delivery_layer": "primary",
            "mutates_canonical": True,
            "source_ref": SOURCE_REF,
            "wave": spec["wave"],
            "fleet_lane": spec["fleet_lane"],
        }
        inserted, status_after = upsert_task(state, task)
        if status_after in TERMINAL_STATUSES:
            remove_terminal_task_from_agents(state, spec["id"])
        else:
            assign_agent(state, OWNER, spec["id"], timestamp, spec["next"], inserted)
        if inserted:
            append_log(
                {
                    "ts": timestamp,
                    "agent": os.environ.get("AI_NAME", "Claude"),
                    "type": "assign",
                    "task_id": spec["id"],
                    "message": f"Assigned {spec['id']} to {OWNER} with reviewer {REVIEWER}",
                }
            )
        action = "CREATE" if inserted else "UPSERT"
        print(f"{action} {spec['id']:12} owner={OWNER:8} reviewer={REVIEWER:8} wave={spec['wave']}")
    state["updated_at"] = timestamp
    save_state(state)
    print("Done. Run `python3 scripts/ai_status.py sync` only if generated views need refresh.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
