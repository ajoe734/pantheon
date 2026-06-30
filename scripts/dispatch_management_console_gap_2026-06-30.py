#!/usr/bin/env python3
"""Dispatch MGMT-GAP execution tasks for the 2026-06-30 management-console gap.

Spec: docs/04/pantheon_management_console_gap_2026-06-30/README.md
Packet: docs/bff/execution-tasks/2026-06-30-management-console-production-gap/INDEX.md
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS_PATH = Path(REPO_ROOT) / "ai-status.json"
LOG_PATH = Path(REPO_ROOT) / "ai-activity-log.jsonl"
AUTO_BY = "dispatch_management_console_gap_2026-06-30"
SOURCE_REF = {
    "doc": "docs/04/pantheon_management_console_gap_2026-06-30/README.md",
    "archive": "docs/04/pantheon_management_console_gap_2026-06-30/archive/live-audit-2026-06-30.md",
    "packet": "docs/bff/execution-tasks/2026-06-30-management-console-production-gap/INDEX.md",
}


# (task_id, title, summary_zh, owner, reviewer, phase, depends_on, acceptance, artifacts, metadata)
TASKS = [
    (
        "MGMT-GAP-001",
        "Management route and IA cleanup",
        "清除 management console 真重複入口: control-room-legacy 不再 render 舊 ControlRoom; "
        "deployment/deployment/:id 改 canonical deployments redirect; 收斂一級 nav 中非 production 的 studios/empty registry/loop 子頁。",
        "Codex2",
        "Claude",
        "MGMT Console Production Gap / Batch 1 route IA",
        "",
        "control-room-legacy 不再 render 舊頁; deployment singular routes redirect; nav 收斂或每項有 production readiness; route tests + hosted probe 綠",
        "frontend-checkout:src/App.tsx,frontend-checkout:src/management/ManagementLayout.tsx,frontend-checkout:e2e,docs/04/pantheon_management_console_gap_2026-06-30",
        {"batch": 1, "fleet_lane": "frontend-route-ia"},
    ),
    (
        "MGMT-GAP-003",
        "BFF management DTO contract hardening",
        "為 /bff/management/data-sources、permissions、memory-governance、consult-rules、/bff/lineage、/bff/workflows、/bff/hooks、/bff/knowledge 補齊 DTO 契約、degraded envelope、OpenAPI schema 與 contract tests。",
        "Claude2",
        "Codex",
        "MGMT Console Production Gap / Batch 2 BFF contracts",
        "",
        "所有端點 OpenAPI + contract test + hosted curl 200; 空資料為明確 degraded/unavailable 而不是 ambiguous []",
        "services/control-plane/bff,docs/04/pantheon_management_console_gap_2026-06-30",
        {"batch": 2, "fleet_lane": "bff-contract"},
    ),
    (
        "MGMT-GAP-002",
        "Frontend canonical management read wiring",
        "將 Data Sources、permissions、memory、consult、lineage、workflows、hooks、ranking 改接 canonical management endpoints；移除 strict live seed/mock 偽裝。",
        "Claude",
        "Codex",
        "MGMT Console Production Gap / Batch 2 FE canonical reads",
        "MGMT-GAP-003",
        "hosted browser probe 捕捉每頁 intended endpoint; success/degraded 測試綠; 無 seed/mock 被顯示為 live truth",
        "frontend-checkout:src/lib/bff-v1,frontend-checkout:src/management,frontend-checkout:e2e,docs/04/pantheon_management_console_gap_2026-06-30",
        {"batch": 2, "fleet_lane": "frontend-bff-integration"},
    ),
    (
        "MGMT-GAP-004",
        "Management command receipts and write truth",
        "盤點 ranking/governance/workflows/hooks/settings/detail panels 的 write-like CTA；全部改為 governed command receipt/audit flow，或明確 disabled non-production。",
        "Codex",
        "Claude2",
        "MGMT Console Production Gap / Batch 3 command truth",
        "MGMT-GAP-002,MGMT-GAP-003",
        "無 in-scope CTA 只靠 toast/local state 成功; dry-run/real-writes-off probe 證明無隱性 side effect; high-risk action 有 confirm + command/audit evidence",
        "frontend-checkout:src/management,frontend-checkout:src/lib/bff,services/control-plane/bff,docs/04/pantheon_management_console_gap_2026-06-30",
        {"batch": 3, "fleet_lane": "command-governance"},
    ),
    (
        "MGMT-GAP-005",
        "Studios and capabilities to production level",
        "Formula Studio/Skill Sandbox 接真 backtest/skill runner 或從一級 nav 降級；Tools/MCP/Skills create/import/publish/retire actions 全部 governed 或 disabled。",
        "Gemini",
        "Claude",
        "MGMT Console Production Gap / Batch 4 studios capabilities",
        "MGMT-GAP-003",
        "hosted probe 無 mock trace/backtest 被標成 live success; runner contracts 有 tests/limits; capability actions 有 command/job id 或 disabled",
        "frontend-checkout:src/management/pages/studios,frontend-checkout:src/management/pages,services/control-plane/bff,docs/04/pantheon_management_console_gap_2026-06-30",
        {"batch": 4, "fleet_lane": "runtime-capability"},
    ),
    (
        "MGMT-GAP-006",
        "Hosted management production acceptance harness",
        "建立 hosted management probe: visible nav、hidden aliases、canonical final paths、endpoint capture、strict-live no seed fallback、write CTA mock detection、console/CORS failures。",
        "Gemini2",
        "Codex",
        "MGMT Console Production Gap / Batch 5 acceptance harness",
        "MGMT-GAP-001,MGMT-GAP-002,MGMT-GAP-004,MGMT-GAP-005",
        "probe 覆蓋所有 visible management nav + hidden aliases; 輸出 JSON/Markdown evidence; release gate 可 fail legacy render/missing endpoint/mock write success",
        "frontend-checkout:scripts,frontend-checkout:e2e,scripts/aggregate-release-gate.mjs,docs/04/pantheon_management_console_gap_2026-06-30/archive",
        {"batch": 5, "fleet_lane": "integration-qa"},
    ),
    (
        "MGMT-GAP-007",
        "Management production closeout and archive proof",
        "緊盯 MGMT-GAP 全任務到 done/superseded；確認 PR/merge/deploy/probe evidence；歸檔最終 production proof 與 residual risk owner/expiry。",
        "Codex",
        "Claude",
        "MGMT Console Production Gap / Batch 5 oversight closeout",
        "MGMT-GAP-006",
        "所有 MGMT-GAP 任務 done 或 reviewed superseded; final archive 含 FE deploy/BFF/OpenAPI/hosted probe/PR SHA; closeout 清楚列 completion 或 blocker",
        "ai-status.json,docs/04/pantheon_management_console_gap_2026-06-30/archive,docs/bff/execution-tasks/2026-06-30-management-console-production-gap",
        {"batch": 5, "fleet_lane": "oversight-closeout"},
    ),
]


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def load_state() -> dict:
    return json.loads(STATUS_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATUS_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_log(entry: dict) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def upsert_task(state: dict, task: dict) -> None:
    tasks = state.setdefault("tasks", [])
    for index, existing in enumerate(tasks):
        if existing.get("id") == task["id"]:
            merged = {**existing, **task}
            tasks[index] = merged
            return
    tasks.append(task)


def assign_agent(state: dict, owner: str, task_id: str, timestamp: str) -> None:
    for agent in state.get("agents", []):
        if agent.get("name") != owner:
            continue
        ids = agent.setdefault("current_task_ids", [])
        if task_id not in ids:
            ids.append(task_id)
        agent["status"] = "waiting"
        agent["next"] = "Assignment created"
        agent["last_update"] = timestamp
        return


def main() -> int:
    state = load_state()
    timestamp = iso_now()
    for task_id, title, summary, owner, reviewer, phase, deps, acceptance, artifacts, metadata in TASKS:
        task_metadata = {
            "source_ref": SOURCE_REF,
            "delivery_layer": "primary",
            "mutates_canonical": True,
            **metadata,
        }
        task = {
            "id": task_id,
            "title": title,
            "summary_zh": summary,
            "phase": phase,
            "owner": owner,
            "reviewer": reviewer,
            "status": "todo",
            "depends_on": split_csv(deps),
            "artifacts": split_csv(artifacts),
            "acceptance": split_csv(acceptance),
            "next": "Assignment created",
            "last_update": timestamp,
            "TASK_CLASS": "execution",
        }
        task.update(task_metadata)
        task["task_class"] = "execution"
        task["auto_created_by"] = AUTO_BY
        task["auto_generated"] = True
        task["mutates_canonical"] = True
        task.pop("TASK_CLASS", None)
        upsert_task(state, task)
        assign_agent(state, owner, task_id, timestamp)
        append_log(
            {
                "ts": timestamp,
                "agent": os.environ.get("AI_NAME", "Codex"),
                "type": "assign",
                "task_id": task_id,
                "message": f"Assigned {task_id} to {owner} with reviewer {reviewer}",
            }
        )
        print(f"ASSIGN {task_id:13} owner={owner:8} reviewer={reviewer:8} deps={deps or '-'}")
    state["updated_at"] = timestamp
    save_state(state)
    print("Done. Existing active tasks were preserved; dashboard sync is intentionally not run here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
