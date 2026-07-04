#!/usr/bin/env python3
"""Dispatch Agora dynamic UI live auth recovery task for 2026-07-03."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = REPO_ROOT / "ai-status.json"
LOG_PATH = REPO_ROOT / "ai-activity-log.jsonl"
AUTO_BY = "dispatch_agora_dynamic_ui_live_auth_2026-07-03"
ARCHIVE = "docs/04/pantheon_agora_dynamic_ui_live_recovery_2026-07-03/INDEX.md"
PACKET = "docs/bff/execution-tasks/2026-07-03-agora-dynamic-ui-live-auth/INDEX.md"
TASK_BRIEF = (
    "docs/bff/execution-tasks/2026-07-03-agora-dynamic-ui-live-auth/"
    "AG-DYNUI-LIVE-AUTH-003-frontend-auth-headers.md"
)
SOURCE_REF = {
    "archive": ARCHIVE,
    "packet": PACKET,
    "task_brief": TASK_BRIEF,
    "execute_plans_ui_pr": "https://github.com/ajoe734/execute-plans/pull/147",
    "pantheon_backend_pr": "https://github.com/ajoe734/pantheon/pull/2808",
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

TASKS = [
    {
        "id": "AG-DYNUI-LIVE-AUTH-003",
        "title": "Agora Trading Room frontend BFF auth headers",
        "summary_zh": (
            "修 execute-plans Agora Trading Room frontend client: 所有 tradingRoom.ts "
            "read/write fetch 必須使用 shared BFF auth headers, 保留動態 BFF data flow, "
            "補 Authorization 測試, PR merge 後等待 dev FE deploy 並用 live browser probe "
            "證明 /bff/agora/trading-room 與 decision-events 都回 200。不得重做靜態 UI; "
            "設計/合約不明時先開 blocker。"
        ),
        "phase": "Agora Dynamic UI Live Recovery / frontend auth transport",
        "owner": "Claude",
        "reviewer": "Codex",
        "depends_on": ["AG-DYNUI-LIVE-DEFAULT-001", "AG-DYNUI-LIVE-AUTH-002"],
        "artifacts": [
            "execute-plans/src/lib/bff-v1/agora/tradingRoom.ts",
            "execute-plans/src/lib/bff-v1/agora/tradingRoom.test.ts",
            "execute-plans/src/lib/bff-v1/headers.ts",
            ARCHIVE,
            PACKET,
            TASK_BRIEF,
            "/tmp/agora-live-after-auth002.json",
            "/tmp/agora-live-after-auth002.png",
        ],
        "acceptance": [
            "all Trading Room BFF calls in tradingRoom.ts use shared BFF auth headers",
            "tests prove Authorization for getTradingRoom and listDecisionEvents",
            "tests prove Authorization for at least one decision event mutation",
            "tests prove Authorization for at least one strategy/workspace mutation",
            "caller-provided If-Match, Idempotency-Key, and request/correlation IDs stay intact",
            "execute-plans PR is reviewed, merged, and records merge commit SHA",
            "dev FE deploy from merged commit succeeds",
            "live browser probe shows /bff/agora/trading-room returns 200",
            "live browser probe shows /bff/agora/trading-room/decision-events returns 200",
            "live /agora/trading-room does not show Failed to load Trading Room or old white layout markers",
        ],
        "next": (
            "Start execute-plans frontend auth-header fix; do not rebuild UI and do not close from local-only evidence."
        ),
        "metadata": {
            "task_class": "execution",
            "auto_created_by": AUTO_BY,
            "auto_generated": True,
            "delivery_layer": "primary",
            "mutates_canonical": True,
            "fleet_lane": "agora-frontend-auth-live-recovery",
            "source_ref": SOURCE_REF,
        },
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
            **spec["metadata"],
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

