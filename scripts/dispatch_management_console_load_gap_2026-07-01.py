#!/usr/bin/env python3
"""Dispatch MGMT-LOAD execution tasks for the 2026-07-01 management load gap.

Spec: docs/04/pantheon_management_console_load_gap_2026-07-01/MANAGEMENT_CONSOLE_LOAD_GAP_SPEC.md
Packet: docs/bff/execution-tasks/2026-07-01-management-console-load-gap/INDEX.md
Parent: MGMT-GAP-010
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS_PATH = Path(REPO_ROOT) / "ai-status.json"
LOG_PATH = Path(REPO_ROOT) / "ai-activity-log.jsonl"
AUTO_BY = "dispatch_management_console_load_gap_2026-07-01"
PARENT_TASK_ID = "MGMT-GAP-010"
PACKET = "docs/bff/execution-tasks/2026-07-01-management-console-load-gap/INDEX.md"
SPEC = "docs/04/pantheon_management_console_load_gap_2026-07-01/MANAGEMENT_CONSOLE_LOAD_GAP_SPEC.md"
SOURCE_REF = {
    "doc": SPEC,
    "packet": PACKET,
    "parent_packet": "docs/bff/execution-tasks/2026-06-30-management-console-production-gap/INDEX.md",
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
PRIMARY_AGENT_NEXT_TASK = {
    "Gemini2": "MGMT-LOAD-001",
    "Claude2": "MGMT-LOAD-002",
}
GENERIC_NEXT_MESSAGES = {
    None,
    "",
    "Assignment created",
    "Assignment created from management load-gap execution packet",
}
NEXT_BY_TASK = {
    "MGMT-LOAD-001": "Start hosted route-load baseline and BFF fanout probes; do not use networkidle as readiness proof.",
    "MGMT-LOAD-002": "Start BFF shell-summary endpoint and /bff/jobs route canonicalization.",
    "MGMT-LOAD-003": "Continue MGMT-GAP-008 if active; MGMT-LOAD-003 waits for MGMT-LOAD-001 and MGMT-LOAD-002.",
    "MGMT-LOAD-004": "Wait for MGMT-LOAD-001 baseline, then start management route code splitting.",
    "MGMT-LOAD-005": "Continue MGMT-GAP-005 if active; MGMT-LOAD-005 waits for MGMT-LOAD-001 and MGMT-LOAD-002.",
    "MGMT-LOAD-006": "Wait for MGMT-LOAD-001 through MGMT-LOAD-005, then wire release load budgets.",
    "MGMT-LOAD-007": "Continue MGMT-GAP-004 if active; MGMT-LOAD-007 waits for MGMT-LOAD-006 closeout evidence.",
}


# (task_id, title, summary_zh, owner, reviewer, phase, depends_on, acceptance, artifacts, metadata)
TASKS = [
    (
        "MGMT-LOAD-001",
        "Management load baseline and route-ready probes",
        "建立 /management/evidence hosted browser route-load baseline 與 BFF fanout baseline；readiness 改用 heading/row/API milestone，不用 networkidle 判定 SSE 頁面就緒。",
        "Gemini2",
        "Codex",
        "MGMT Console Load Gap / Wave 0 baseline probes",
        "MGMT-GAP-001,MGMT-GAP-002",
        "route timing JSON + request waterfall + Markdown baseline archived; probe does not wait on networkidle; BFF fanout probe captures /health/evidence/alerts/approvals/jobs timings",
        "docs/04/pantheon_management_console_load_gap_2026-07-01,frontend-checkout:scripts,frontend-checkout:e2e,services/control-plane/bff",
        {"wave": 0, "fleet_lane": "release-probe-baseline"},
    ),
    (
        "MGMT-LOAD-002",
        "BFF shell summary and jobs route canonicalization",
        "新增 /bff/management/shell-summary，提供 session/transport 與 cheap badge counts；避免 full list aggregation；整理 duplicate /bff/jobs route definitions。",
        "Claude2",
        "Codex",
        "MGMT Console Load Gap / Wave 1 BFF shell summary",
        "MGMT-GAP-003",
        "shell-summary 不回 full approvals/alerts/jobs lists; count freshness/degraded surfaces explicit; /bff/jobs only one canonical route; OpenAPI + contract tests + dev timing evidence",
        "services/control-plane/bff,docs/04/pantheon_management_console_load_gap_2026-07-01,docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-002-bff-shell-summary.md",
        {"wave": 1, "fleet_lane": "bff-shell-summary"},
    ),
    (
        "MGMT-LOAD-004",
        "Management route code splitting",
        "把 management route graph 拆成 lazy route clusters，讓 Evidence route 不再下載/解析整個 console；保留 direct navigation、redirect alias、chunk error handling。",
        "Codex2",
        "Claude",
        "MGMT Console Load Gap / Wave 1 FE route splitting",
        "MGMT-GAP-001,MGMT-LOAD-001",
        "initial management JS gzip <= 800KB or approved exception; Evidence route chunk gzip <= 150KB excluding shared vendor; route smoke + hosted timing evidence archived",
        "frontend-checkout:src/App.tsx,frontend-checkout:src/management,frontend-checkout:e2e,docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-004-management-route-code-split.md",
        {"wave": 1, "fleet_lane": "frontend-route-splitting"},
    ),
    (
        "MGMT-LOAD-003",
        "Frontend shell fanout reduction",
        "TopBar 改接 shell-summary；summary unavailable 時 defer full list reads；JobProgressDrawer 不在 first route load 重複抓 /bff/jobs；drawer hydration 延後到使用者開啟或 primary content 後。",
        "Claude",
        "Codex",
        "MGMT Console Load Gap / Wave 2 FE shell fanout",
        "MGMT-LOAD-001,MGMT-LOAD-002",
        "Evidence first row 前 non-primary BFF requests <= 2; no duplicate /bff/jobs before first row/empty state; tests cover summary success/degraded/unavailable and lazy drawer hydration",
        "frontend-checkout:src/platform,frontend-checkout:src/lib/bff-v1,frontend-checkout:e2e,docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-003-fe-shell-fanout.md",
        {"wave": 2, "fleet_lane": "frontend-shell-fanout"},
    ),
    (
        "MGMT-LOAD-005",
        "BFF read concurrency isolation",
        "隔離 shell summary/Evidence/alerts/approvals/jobs 的同步 read aggregation；/health 不可被 management read fanout 卡住；慢路徑要 timeout/degraded 而不是拖住 unrelated routes。",
        "Gemini",
        "Claude2",
        "MGMT Console Load Gap / Wave 2 BFF read concurrency",
        "MGMT-LOAD-001,MGMT-LOAD-002",
        "/health p95 <= 200ms during shell-summary/Evidence fanout; Evidence p95 <= 750ms during fanout or blocker names exact read path; timeout/degraded behavior tested",
        "services/control-plane/bff,docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-005-bff-read-concurrency.md",
        {"wave": 2, "fleet_lane": "bff-read-concurrency"},
    ),
    (
        "MGMT-LOAD-006",
        "Management load release gate",
        "把 route-load baseline 變成 release gate：bundle budget、route-ready milestones、startup request count、duplicate jobs detection、BFF fanout latency 全部可 fail gate 並輸出 JSON/Markdown artifact。",
        "Gemini2",
        "Codex",
        "MGMT Console Load Gap / Wave 3 release gate",
        "MGMT-LOAD-001,MGMT-LOAD-002,MGMT-LOAD-003,MGMT-LOAD-004,MGMT-LOAD-005",
        "release gate fails on budget breach, duplicate startup /bff/jobs, networkidle-only readiness, excess non-primary startup requests, or BFF fanout latency regression; artifacts linked to MGMT-GAP-006 handoff",
        "frontend-checkout:scripts,frontend-checkout:e2e,scripts/aggregate-release-gate.mjs,docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-006-release-load-gate.md",
        {"wave": 3, "fleet_lane": "release-load-gate"},
    ),
    (
        "MGMT-LOAD-007",
        "Load gap closeout and parent gate",
        "彙整 MGMT-LOAD 全任務 closeout，更新 MGMT-GAP-010，交付 MGMT-GAP-006 可驗收的 hosted load-gate artifact paths 與 residual risk。",
        "Codex",
        "Claude",
        "MGMT Console Load Gap / Wave 4 closeout",
        "MGMT-LOAD-006",
        "all MGMT-LOAD tasks done or reviewed superseded; final archive includes before/after timing, waterfall, bundle sizes, BFF fanout, PR SHAs, deploy evidence, residual risks; MGMT-GAP-010 reviewer-approved",
        "ai-status.json,docs/04/pantheon_management_console_load_gap_2026-07-01/archive,docs/bff/execution-tasks/2026-07-01-management-console-load-gap",
        {"wave": 4, "fleet_lane": "oversight-closeout"},
    ),
]


PARENT_UPDATE = {
    "depends_on": ["MGMT-GAP-001", "MGMT-GAP-002", "MGMT-LOAD-007"],
    "artifacts": [
        "docs/04/pantheon_management_console_load_gap_2026-07-01",
        "docs/bff/execution-tasks/2026-07-01-management-console-load-gap",
        "frontend-checkout:src",
        "frontend-checkout:scripts",
        "frontend-checkout:e2e",
        "services/control-plane/bff",
    ],
    "acceptance": [
        "MGMT-LOAD-007 done or reviewed superseded",
        "load closeout includes route-ready timings, request waterfall, bundle sizes, BFF fanout, PR SHAs, deploy evidence, and residual risks",
        "MGMT-GAP-006 receives exact load-gate artifact paths for hosted management production acceptance",
    ],
    "next": "Wait for MGMT-LOAD-007 child closeout; do not close umbrella from a single local probe or partial optimization.",
    "source_ref": SOURCE_REF,
    "load_gap_children": [task[0] for task in TASKS],
}


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


def find_task(state: dict, task_id: str) -> dict | None:
    for task in state.get("tasks", []):
        if task.get("id") == task_id:
            return task
    return None


def remove_terminal_task_from_agents(state: dict, task_id: str) -> None:
    for agent in state.get("agents", []):
        ids = agent.get("current_task_ids")
        if not isinstance(ids, list):
            continue
        agent["current_task_ids"] = [item for item in ids if item != task_id]


def assign_agent(state: dict, owner: str, task_id: str, timestamp: str, next_note: str, inserted: bool) -> None:
    for agent in state.get("agents", []):
        if agent.get("name") != owner:
            continue
        ids = agent.setdefault("current_task_ids", [])
        if task_id not in ids:
            ids.append(task_id)
        should_update = (
            inserted
            or agent.get("next") in GENERIC_NEXT_MESSAGES
            or PRIMARY_AGENT_NEXT_TASK.get(owner) == task_id
        )
        if should_update:
            agent["status"] = "waiting"
            agent["next"] = next_note
            agent["last_update"] = timestamp
        return


def update_parent(state: dict) -> None:
    parent = find_task(state, PARENT_TASK_ID)
    if not parent:
        return
    for key, value in PARENT_UPDATE.items():
        parent[key] = value


def main() -> int:
    state = load_state()
    timestamp = iso_now()
    update_parent(state)
    for task_id, title, summary, owner, reviewer, phase, deps, acceptance, artifacts, metadata in TASKS:
        task_metadata = {
            "source_ref": SOURCE_REF,
            "delivery_layer": "primary",
            "mutates_canonical": True,
            "helper_parent": PARENT_TASK_ID,
            "helper_kind": "load_gap_execution_slice",
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
            "next": NEXT_BY_TASK.get(task_id, "Assignment created from management load-gap execution packet"),
            "last_update": timestamp,
            "task_class": "execution",
            "auto_created_by": AUTO_BY,
            "auto_generated": True,
        }
        task.update(task_metadata)
        inserted, status_after = upsert_task(state, task)
        if status_after in TERMINAL_STATUSES:
            remove_terminal_task_from_agents(state, task_id)
        else:
            assign_agent(state, owner, task_id, timestamp, task["next"], inserted)
        if inserted:
            append_log(
                {
                    "ts": timestamp,
                    "agent": os.environ.get("AI_NAME", "Codex"),
                    "type": "assign",
                    "task_id": task_id,
                    "message": f"Assigned {task_id} to {owner} with reviewer {reviewer}",
                }
            )
        action = "CREATE" if inserted else "UPSERT"
        print(f"{action} {task_id:13} owner={owner:8} reviewer={reviewer:8} deps={deps or '-'}")
    state["updated_at"] = timestamp
    save_state(state)
    print(f"Updated {PARENT_TASK_ID} to wait on MGMT-LOAD-007 closeout.")
    print("Done. Run `python3 scripts/ai_status.py sync` to refresh generated status views.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
