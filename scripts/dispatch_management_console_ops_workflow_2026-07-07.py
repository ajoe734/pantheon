#!/usr/bin/env python3
"""Dispatch management console operations workflow tasks for 2026-07-07.

Spec: docs/04/pantheon_management_console_operations_workflow_2026-07-07/MANAGEMENT_CONSOLE_OPERATIONS_WORKFLOW_PLAN.md
Packet: docs/bff/execution-tasks/2026-07-07-management-console-operations-workflow/INDEX.md
"""
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
AUTO_BY = "dispatch_management_console_ops_workflow_2026-07-07"
PACKET = "docs/bff/execution-tasks/2026-07-07-management-console-operations-workflow/INDEX.md"
SPEC = "docs/04/pantheon_management_console_operations_workflow_2026-07-07/MANAGEMENT_CONSOLE_OPERATIONS_WORKFLOW_PLAN.md"
SOURCE_REF = {
    "doc": SPEC,
    "packet": PACKET,
    "trigger": "2026-07-07 management console persona fleet/performance attribution operations review",
    "focus_persona": "persona-20260528-04688755",
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
    "Assignment created from management console operations workflow packet",
}
PRIMARY_AGENT_NEXT_TASK = {
    "Claude2": "MGMT-OPS-001",
    "Codex2": "MGMT-OPS-002",
    "Gemini2": "MGMT-OPS-003",
    "Antigravity2": "MGMT-OPS-004",
    "Gemini": "MGMT-OPS-005",
    "Antigravity": "MGMT-OPS-006",
}
NEXT_BY_TASK = {
    "MGMT-OPS-001": "Lock the shared operations read model and source-confidence contract.",
    "MGMT-OPS-002": "Normalize management frontend adapters and data-confidence UI rules.",
    "MGMT-OPS-003": "Turn Portfolio Book into the capital, exposure, and risk monitor.",
    "MGMT-OPS-004": "Fix Performance Attribution drilldown, fallback labeling, and diagnostics.",
    "MGMT-OPS-005": "Reframe Persona League and Quarterly Ranking as governed ranking inputs.",
    "MGMT-OPS-006": "Wire operator actions through Human Review and auditable receipts.",
    "MGMT-OPS-007": "Close with merged PRs, dev publish, hosted smoke, and residual-risk evidence.",
}


# (task_id, title, summary_zh, owner, reviewer, phase, depends_on, acceptance, artifacts, metadata)
TASKS = [
    (
        "MGMT-OPS-001",
        "Operations read model and source confidence contract",
        "鎖定 management console 共同 operations read model，讓 Persona Fleet、Portfolio Book、績效歸因、排行榜與 Human Review 使用一致的 identity、source confidence 與 action state。",
        "Claude2",
        "Codex2",
        "Management Console Operations / Wave 0 source truth",
        [],
        [
            "shared read model covers persona runtime ledger pool sleeve strategy artifact broker period and as_of identity",
            "formal partial fallback degraded unavailable states are represented in BFF payloads",
            "missing joins are diagnostics not dropped rows or nan metrics",
            "tests cover normal partial fallback degraded and unavailable states",
        ],
        [
            "services/control-plane/bff",
            "services/control-plane/bff/tests",
            "docs/bff/execution-tasks/2026-07-07-management-console-operations-workflow/MGMT-OPS-001-operations-read-model.md",
        ],
        {"wave": 0, "fleet_lane": "ops-read-model-source-confidence"},
    ),
    (
        "MGMT-OPS-002",
        "Frontend adapters and data confidence display",
        "統一前端 adapter、snake_case/camelCase 正規化、資料信心顯示與 nan 抑制，讓各管理頁不再各自發明 fallback。",
        "Codex2",
        "Claude2",
        "Management Console Operations / Wave 1 frontend foundation",
        ["MGMT-OPS-001"],
        [
            "all targeted pages share data-confidence labels and empty states",
            "nan NaN undefined and missing metrics are never rendered as operator-facing values",
            "Persona Fleet performance links preserve persona runtime period and source hints",
            "frontend tests cover field normalization and fallback attribution routing",
        ],
        [
            "execute-plans:src/management",
            "execute-plans:src/lib",
            "execute-plans:e2e",
            "docs/bff/execution-tasks/2026-07-07-management-console-operations-workflow/MGMT-OPS-002-frontend-adapters-data-confidence.md",
        ],
        {"wave": 1, "fleet_lane": "fe-adapters-data-confidence"},
    ),
    (
        "MGMT-OPS-003",
        "Portfolio capital and risk monitor",
        "把 Portfolio Book 變成資金、曝險、telemetry coverage、stale data 與風險事件的第一站，並連回 Persona Fleet、績效歸因與 Human Review。",
        "Gemini2",
        "Codex2",
        "Management Console Operations / Wave 1 portfolio monitor",
        ["MGMT-OPS-001"],
        [
            "Portfolio Book shows capital exposure owner persona runtime source coverage and stale status",
            "paper canary and live exposure are visually and semantically separated",
            "missing or degraded holdings appear as incidents",
            "tests prove degraded holdings cannot create false formal attribution",
        ],
        [
            "services/control-plane/bff",
            "execute-plans:src/management/pages",
            "execute-plans:src/lib",
            "execute-plans:e2e",
            "docs/bff/execution-tasks/2026-07-07-management-console-operations-workflow/MGMT-OPS-003-portfolio-risk-monitor.md",
        ],
        {"wave": 1, "fleet_lane": "portfolio-capital-risk-monitor"},
    ),
    (
        "MGMT-OPS-004",
        "Performance attribution drilldown and diagnostics",
        "修正從 Persona Fleet 點績效進去的頁面：formal attribution、fallback summary、missing holdings 與 degraded diagnostics 必須分清楚。",
        "Antigravity2",
        "Claude2",
        "Management Console Operations / Wave 1 attribution drilldown",
        ["MGMT-OPS-001", "MGMT-OPS-002"],
        [
            "screenshot focus persona is labeled fallback or degraded diagnostic when formal rows are absent",
            "formal matches fallback summaries and diagnostics have separate counts",
            "missing holdings are explicit and actionable",
            "route and component tests cover persona-20260528-04688755 fallback case",
        ],
        [
            "services/control-plane/bff",
            "execute-plans:src/management/pages",
            "execute-plans:src/lib",
            "execute-plans:e2e",
            "docs/bff/execution-tasks/2026-07-07-management-console-operations-workflow/MGMT-OPS-004-performance-attribution-drilldown.md",
        ],
        {"wave": 1, "fleet_lane": "performance-attribution-drilldown"},
    ),
    (
        "MGMT-OPS-005",
        "Persona League and Quarterly governance inputs",
        "把 Persona League 與 Quarterly Ranking 定位成 ranking/governance input，顯示 criteria、eligibility、evidence coverage 與 review state，不直接暗示加資金或晉升。",
        "Gemini",
        "Codex2",
        "Management Console Operations / Wave 2 ranking governance",
        ["MGMT-OPS-001", "MGMT-OPS-002"],
        [
            "League ranking rows are separated from status or readiness summaries",
            "Quarterly Ranking shows governance-cycle state and evidence coverage",
            "ranking pages link to Persona Fleet Performance Attribution and Human Review",
            "tests cover degraded telemetry null metrics and field normalization",
        ],
        [
            "services/control-plane/bff",
            "execute-plans:src/management/pages",
            "execute-plans:src/lib",
            "execute-plans:e2e",
            "docs/bff/execution-tasks/2026-07-07-management-console-operations-workflow/MGMT-OPS-005-league-quarterly-governance.md",
        ],
        {"wave": 2, "fleet_lane": "league-quarterly-governance"},
    ),
    (
        "MGMT-OPS-006",
        "Governed operator actions and Human Review",
        "把 observe、request review、pause/resume、demote、promotion candidate、rebalance proposal、approved apply 與 emergency containment 全部接到 Human Review 與 audit receipt。",
        "Antigravity",
        "Claude2",
        "Management Console Operations / Wave 2 governed actions",
        ["MGMT-OPS-003", "MGMT-OPS-004", "MGMT-OPS-005"],
        [
            "mutating actions are represented as request approval apply and receipt",
            "ranking pages only create recommendations or review packets",
            "emergency containment cannot promote or increase allocation",
            "tests cover action gating idempotency rejected preconditions and receipt linking",
        ],
        [
            "services/control-plane/bff",
            "execute-plans:src/management/pages",
            "execute-plans:src/lib",
            "execute-plans:e2e",
            "docs/bff/execution-tasks/2026-07-07-management-console-operations-workflow/MGMT-OPS-006-operator-actions-human-review.md",
        ],
        {"wave": 2, "fleet_lane": "human-review-governed-actions"},
    ),
    (
        "MGMT-OPS-007",
        "Hosted operations acceptance and closeout",
        "彙整所有 PR、測試、merge、dev publish 與 hosted smoke，證明 Portfolio Book -> Persona Fleet -> 績效歸因 -> Human Review 的操作閉環。",
        "Codex2",
        "Human/Ops",
        "Management Console Operations / Wave 3 closeout",
        ["MGMT-OPS-002", "MGMT-OPS-003", "MGMT-OPS-004", "MGMT-OPS-005", "MGMT-OPS-006"],
        [
            "all child tasks are done merged or explicitly superseded with evidence",
            "hosted smoke proves the full operator loop",
            "fallback attribution is labeled and nan is absent",
            "closeout names PRs merge SHAs deployment target validation and residual risks",
        ],
        [
            "docs/04/pantheon_management_console_operations_workflow_2026-07-07/archive",
            "services/control-plane/bff",
            "execute-plans:src",
            "docs/bff/execution-tasks/2026-07-07-management-console-operations-workflow/MGMT-OPS-007-hosted-ops-acceptance-closeout.md",
        ],
        {"wave": 3, "fleet_lane": "hosted-ops-closeout"},
    ),
]


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_state() -> dict:
    if not STATUS_PATH.exists():
        raise FileNotFoundError(f"status file not found: {STATUS_PATH}")
    return json.loads(STATUS_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    assert_isolated_legacy_write_target(STATUS_PATH, tool=AUTO_BY)
    STATUS_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_log(entry: dict) -> None:
    assert_isolated_legacy_write_target(LOG_PATH, tool=AUTO_BY)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        assert_isolated_legacy_write_target(LOG_PATH, tool=AUTO_BY)
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


def remove_terminal_task_from_agents(state: dict, task_id: str) -> None:
    for agent in state.get("agents", []):
        ids = agent.get("current_task_ids")
        if not isinstance(ids, list):
            continue
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


def build_task(
    task_id: str,
    title: str,
    summary: str,
    owner: str,
    reviewer: str,
    phase: str,
    deps: list[str],
    acceptance: list[str],
    artifacts: list[str],
    metadata: dict,
    timestamp: str,
) -> dict:
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
        "next": NEXT_BY_TASK.get(task_id, "Assignment created from management console operations workflow packet"),
        "last_update": timestamp,
        "task_class": "execution",
        "auto_created_by": AUTO_BY,
        "auto_generated": True,
        "source_ref": SOURCE_REF,
        "delivery_layer": "primary",
        "mutates_canonical": True,
        "helper_kind": "management_console_operations_workflow_execution_slice",
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
        task_id, title, summary, owner, reviewer, phase, deps, acceptance, artifacts, metadata = task_tuple
        task = build_task(
            task_id,
            title,
            summary,
            owner,
            reviewer,
            phase,
            deps,
            acceptance,
            artifacts,
            metadata,
            timestamp,
        )
        inserted, status_after = upsert_task(state, task)
        if status_after in TERMINAL_STATUSES:
            remove_terminal_task_from_agents(state, task_id)
        else:
            assign_agent(state, owner, task_id, timestamp, task["next"])
        if inserted:
            inserted_logs.append(
                {
                    "ts": timestamp,
                    "agent": os.environ.get("AI_NAME", "Codex"),
                    "type": "assign",
                    "task_id": task_id,
                    "message": f"Assigned {task_id} to {owner} with reviewer {reviewer}",
                }
            )
        action = "CREATE" if inserted else "UPSERT"
        print(
            f"{action} {task_id:12} owner={owner:12} reviewer={reviewer:10} "
            f"deps={','.join(deps) if deps else '-'}"
        )

    state["updated_at"] = timestamp
    if args.dry_run:
        print(f"Dry run only. No writes made. status_root={STATUS_ROOT}")
        return 0

    save_state(state)
    for entry in inserted_logs:
        append_log(entry)
    print(f"Done. Updated {STATUS_PATH}.")
    print("Run `PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py sync` to refresh generated status views.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
