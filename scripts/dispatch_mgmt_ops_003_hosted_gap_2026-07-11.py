#!/usr/bin/env python3
"""Dispatch MGMT-OPS-003 hosted gap closure tasks."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from canonical_writer_guard import assert_isolated_legacy_write_target


REPO_ROOT = Path(__file__).resolve().parents[1]
STATUS_ROOT = Path(
    os.path.expanduser(os.environ.get("PANTHEON_STATUS_ROOT", str(REPO_ROOT)))
).resolve()
STATUS_PATH = STATUS_ROOT / "ai-status.json"
LOG_PATH = STATUS_ROOT / "ai-activity-log.jsonl"
AUTO_BY = "dispatch_mgmt_ops_003_hosted_gap_2026-07-11"
PACKET = "docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/INDEX.md"
SPEC = "docs/04/pantheon_mgmt_ops_003_hosted_gap_2026-07-11/MGMT_OPS_003_HOSTED_GAP.md"
REVIEW_CHECKLIST = (
    "docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/"
    "REVIEWER_CHECKLIST.md"
)
SOURCE_REF = {
    "doc": SPEC,
    "packet": PACKET,
    "review_checklist": REVIEW_CHECKLIST,
    "trigger": "2026-07-11 hosted plan-to-live verification",
    "frontend_repo": "ajoe734/execute-plans",
    "backend_repo": "ajoe734/pantheon",
    "dev_frontend": "https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io",
    "dev_bff": "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io",
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
GENERIC_NEXT_MESSAGES = {None, "", "Assignment created", "Assignment created from hosted gap packet"}
PRIMARY_AGENT_NEXT_TASK = {
    "Codex2": "MGMT-OPS-003-GAP-001",
    "Copilot": "MGMT-OPS-003-GAP-002",
    "Codex": "MGMT-OPS-003-GAP-003",
}
NEXT_BY_TASK = {
    "MGMT-OPS-003-GAP-001": "Implement the Portfolio Book incident, filter, stage, and confidence UI in execute-plans.",
    "MGMT-OPS-003-GAP-002": "Repair or quarantine missing runtime identity and telemetry truth without hiding rows.",
    "MGMT-OPS-003-GAP-003": "Run the hosted Portfolio Book to Human Review workflow on desktop and mobile.",
    "MGMT-OPS-003-GAP-004": "Perform independent fail-closed plan-to-hosted difference closeout.",
}
REVIEW_CONTRACT = {
    "mode": "fail_closed_hosted_delta",
    "checklist": REVIEW_CHECKLIST,
    "approval_forbidden_without": [
        "merged_pr_and_merge_sha",
        "deployed_sha_ancestry",
        "authenticated_api_capture",
        "desktop_and_mobile_hosted_evidence",
        "ui_to_api_count_and_label_comparison",
        "console_and_network_failure_counts",
    ],
    "request_changes_when": [
        "any_gap_matrix_row_is_not_pass",
        "ui_confidence_exceeds_source_truth",
        "incident_or_filter_is_hidden",
        "evidence_is_mock_only_or_stale",
        "tested_sha_differs_from_deployed_sha",
    ],
}


# id, title, summary, owner, reviewer, phase, deps, acceptance, artifacts, metadata
TASKS = [
    (
        "MGMT-OPS-003-GAP-001",
        "Frontend Portfolio monitor closure",
        "在 execute-plans 補齊 Portfolio Book incidents、六類 filters、資金 stage 與 source-confidence 顯示。",
        "Codex2",
        "Copilot",
        "MGMT-OPS-003 Hosted Gap / Wave 0 frontend",
        [],
        [
            "all BFF incidents and source issues are operator visible",
            "stage broker runtime source-status stale-telemetry and risk-state filters round trip through the URL",
            "paper canary live and unknown scopes are explicit and accessible",
            "degraded or missing data never renders as formal attribution or fully covered",
            "execute-plans PR is merged to main deployed to dev and reviewed with current hosted evidence",
        ],
        [
            "execute-plans:src/management/pages/oversight/PortfolioBook.tsx",
            "execute-plans:src/lib/v5/management/portfolio.ts",
            "execute-plans:e2e",
            f"{PACKET.rsplit('/', 1)[0]}/MGMT-OPS-003-GAP-001-frontend-monitor.md",
        ],
        {"wave": 0, "target_repo": "execute-plans", "merge_target": "main"},
    ),
    (
        "MGMT-OPS-003-GAP-002",
        "Runtime binding and telemetry truth",
        "修復或隔離 dev runtime 的 persona、broker、ledger、capital scope 與 telemetry 缺口，不得靠隱藏資料改善指標。",
        "Copilot",
        "Codex2",
        "MGMT-OPS-003 Hosted Gap / Wave 0 runtime truth",
        [],
        [
            "every missing binding and telemetry gap is repaired or explicitly quarantined",
            "reconciliation is idempotent audited and never drops unresolved rows",
            "formal attribution is impossible while required joins are degraded",
            "before and after hosted counts are captured from authenticated BFF responses",
            "Pantheon PR is merged to dev deployed and independently sampled by the reviewer",
        ],
        [
            "services/control-plane/bff",
            "services/runtime-manager",
            "services/persona",
            "services/telemetry",
            "docs/deployment/evidence",
            f"{PACKET.rsplit('/', 1)[0]}/MGMT-OPS-003-GAP-002-runtime-data-quality.md",
        ],
        {"wave": 0, "target_repo": "pantheon", "merge_target": "dev"},
    ),
    (
        "MGMT-OPS-003-GAP-003",
        "Hosted Portfolio workflow E2E",
        "以同一份 live BFF response 驗證 Portfolio Book 到 Persona Fleet、績效歸因與 Human Review 的 desktop/mobile workflow。",
        "Codex",
        "Copilot",
        "MGMT-OPS-003 Hosted Gap / Wave 1 hosted E2E",
        ["MGMT-OPS-003-GAP-001", "MGMT-OPS-003-GAP-002"],
        [
            "hosted desktop and mobile workflow reaches Human Review with preserved context",
            "UI counts and labels are asserted against captured live BFF responses",
            "paper canary live and unknown behavior is exercised",
            "console exceptions failed required requests lazy chunk failures and fallback data are zero",
            "frontend and BFF deployed commit identities are recorded",
        ],
        [
            "execute-plans:e2e",
            "execute-plans:hosted-dev-evidence",
            "docs/deployment/evidence/mgmt-ops-003-gap",
            f"{PACKET.rsplit('/', 1)[0]}/MGMT-OPS-003-GAP-003-hosted-workflow-e2e.md",
        ],
        {"wave": 1, "target_repo": "pantheon+execute-plans"},
    ),
    (
        "MGMT-OPS-003-GAP-004",
        "Independent hosted difference closeout",
        "由獨立 reviewer 逐列核對 gap matrix、API、畫面、desktop/mobile 與部署 SHA；任何差異未關閉就退件。",
        "Codex2",
        "Codex",
        "MGMT-OPS-003 Hosted Gap / Wave 2 review gate",
        [
            "MGMT-OPS-003-GAP-001",
            "MGMT-OPS-003-GAP-002",
            "MGMT-OPS-003-GAP-003",
        ],
        [
            "every hosted gap matrix row has a pass verdict and direct evidence",
            "reviewer personally reruns authenticated API desktop and mobile probes",
            "reviewer records console and failed-request counts",
            "stale mock-only or mismatched-SHA evidence is rejected",
            "MGMT-PERF-IA-003 receives a behavior-preserving handoff",
        ],
        [
            "docs/04/pantheon_mgmt_ops_003_hosted_gap_2026-07-11/archive",
            "docs/deployment/evidence/mgmt-ops-003-gap",
            "execute-plans:hosted-dev-evidence",
            f"{PACKET.rsplit('/', 1)[0]}/MGMT-OPS-003-GAP-004-review-closeout.md",
            REVIEW_CHECKLIST,
        ],
        {"wave": 2, "target_repo": "pantheon+execute-plans"},
    ),
]


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_state() -> dict:
    if not STATUS_PATH.exists():
        raise FileNotFoundError(f"status file not found: {STATUS_PATH}")
    return json.loads(STATUS_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    assert_isolated_legacy_write_target(STATUS_PATH, tool=Path(__file__).name)
    STATUS_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_log(entry: dict) -> None:
    assert_isolated_legacy_write_target(LOG_PATH, tool=Path(__file__).name)
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


def remove_task_from_other_agents(state: dict, task_id: str, owner: str) -> None:
    for agent in state.get("agents", []):
        if agent.get("name") == owner:
            continue
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
        if agent.get("next") in GENERIC_NEXT_MESSAGES or PRIMARY_AGENT_NEXT_TASK.get(owner) == task_id:
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
        "helper_kind": "mgmt_ops_003_hosted_gap_closure",
        "review_contract": REVIEW_CONTRACT,
    }
    task.update(metadata)
    return task


def add_performance_center_gate(state: dict) -> bool:
    for task in state.get("tasks", []):
        if task.get("id") != "MGMT-PERF-IA-003" or task.get("status") in TERMINAL_STATUSES:
            continue
        dependencies = task.setdefault("depends_on", [])
        if "MGMT-OPS-003-GAP-004" not in dependencies:
            dependencies.append("MGMT-OPS-003-GAP-004")
            return True
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
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
            remove_task_from_other_agents(state, task["id"], task["owner"])
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
        print(f"{action} {task['id']} owner={task['owner']} reviewer={task['reviewer']} deps={deps}")

    gated = add_performance_center_gate(state)
    print(f"PERFORMANCE_CENTER_GATE {'ADD' if gated else 'UNCHANGED'}")
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
