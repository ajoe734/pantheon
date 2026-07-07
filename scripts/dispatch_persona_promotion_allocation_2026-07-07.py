#!/usr/bin/env python3
"""Dispatch persona promotion/allocation execution tasks for 2026-07-07.

Spec: docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/PERSONA_PROMOTION_ALLOCATION_GAP_SPEC.md
Packet: docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/INDEX.md
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS_PATH = Path(REPO_ROOT) / "ai-status.json"
LOG_PATH = Path(REPO_ROOT) / "ai-activity-log.jsonl"
AUTO_BY = "dispatch_persona_promotion_allocation_2026-07-07"
PACKET = "docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/INDEX.md"
SPEC = "docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/PERSONA_PROMOTION_ALLOCATION_GAP_SPEC.md"
SOURCE_REF = {
    "doc": SPEC,
    "packet": PACKET,
    "extends": "docs/04/pantheon_persona_promotion_governance_gap_2026-07-05/PERSONA_PROMOTION_GOVERNANCE_GAP_SPEC.md",
    "prior_closeout": "docs/04/pantheon_persona_promotion_governance_gap_2026-07-05/archive/PPL-GOV-007-PRODUCTION-CLOSEOUT-2026-07-05.md",
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
    "Assignment created from persona promotion/allocation execution packet",
}
PRIMARY_AGENT_NEXT_TASK = {
    "Codex": "PPL-ALLOC-001",
    "Claude2": "PPL-ALLOC-002",
    "Gemini2": "PPL-ALLOC-003",
    "Gemini": "PPL-ALLOC-004",
    "Codex2": "PPL-ALLOC-005",
    "Claude": "PPL-ALLOC-006",
    "Antigravity": "PPL-ALLOC-007",
    "Antigravity2": "PPL-ALLOC-008",
}
NEXT_BY_TASK = {
    "PPL-ALLOC-001": "Lock current-state page inventory and acceptance before implementation.",
    "PPL-ALLOC-002": "Implement BFF create-paper-bundle so new personas enter paper_running with ledger/runtime bindings.",
    "PPL-ALLOC-003": "Normalize paper ledger, canary sleeve, and live pool/sleeve binding read models.",
    "PPL-ALLOC-004": "Implement stage-aware ranking, target weights, caps, and rebalance proposal contract.",
    "PPL-ALLOC-005": "Replace generic persona create UI with Create Paper Persona flow.",
    "PPL-ALLOC-006": "Expand Promotion & Allocation into the primary operator workbench.",
    "PPL-ALLOC-007": "Fix binding visibility and prune/demote duplicate workflow routes.",
    "PPL-ALLOC-008": "Implement emergency containment rules without promotion or allocation-increase side effects.",
    "PPL-ALLOC-009": "Close with PRs, tests, dev publish, hosted smoke, and residual-risk evidence.",
}


# (task_id, title, summary_zh, owner, reviewer, phase, depends_on, acceptance, artifacts, metadata)
TASKS = [
    (
        "PPL-ALLOC-001",
        "Current state and page inventory guard",
        "盤點 persona 建立、paper/real 晉升、資金權重調整與 management pages，鎖定哪些頁面要保留、改造、降級或 redirect。",
        "Codex",
        "Claude",
        "Persona Promotion Allocation / Wave 0 source truth",
        "",
        "current-state audit names every relevant page; creation/paper_running invariant proven or disproven; downstream blockers listed",
        "docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07,docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap",
        {"wave": 0, "fleet_lane": "source-truth-page-inventory"},
    ),
    (
        "PPL-ALLOC-002",
        "BFF create paper persona bundle",
        "新增 idempotent create-paper-bundle：建立 persona 時一次完成 mandate、資料源、風險偏好、paper ledger、paper runtime、paper deployment plan。",
        "Claude2",
        "Codex",
        "Persona Promotion Allocation / Wave 1 BFF create bundle",
        "PPL-ALLOC-001",
        "successful create returns stage=paper_running with paper_ledger_id and runtime_binding_id; partial failure is repairable; no live capital or broker side effects",
        "services/control-plane/bff,services/control-plane/bff/tests/test_bff_persona_create_paper_bundle.py",
        {"wave": 1, "fleet_lane": "bff-create-paper-bundle"},
    ),
    (
        "PPL-ALLOC-003",
        "Capital binding read model",
        "讓 persona fleet / capital rows 清楚顯示 paper ledger、canary sleeve、live sleeve/pool、current weight、target weight 與 binding state。",
        "Gemini2",
        "Claude",
        "Persona Promotion Allocation / Wave 1 binding read models",
        "PPL-ALLOC-001",
        "paper rows show isolated ledgers; canary/live rows show sleeve/pool and weights; legacy paper pool ids are migration trace only",
        "services/control-plane/bff,services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py,services/control-plane/bff/tests/test_bff_capital_pool_bindings.py",
        {"wave": 1, "fleet_lane": "capital-binding-read-model"},
    ),
    (
        "PPL-ALLOC-004",
        "Ranking allocation policy and rebalance proposal contract",
        "實作 stage-aware ranking、real allocation target weights、caps/smoothing/exclusions，並產出 auditable rebalance proposal 而非直接改資金。",
        "Gemini",
        "Claude2",
        "Persona Promotion Allocation / Wave 1 allocation policy",
        "PPL-ALLOC-001,PPL-ALLOC-003",
        "target weights include current/target/delta/cap reason/evidence; live increases require human approval; emergency reduction cannot promote or increase",
        "services/control-plane/bff,services/control-plane/bff/tests/test_bff_persona_allocation_policy.py,services/control-plane/bff/tests/test_bff_rebalance_proposals.py",
        {"wave": 1, "fleet_lane": "allocation-policy-rebalance"},
    ),
    (
        "PPL-ALLOC-005",
        "Frontend create paper persona flow",
        "把 Personas 的 generic create 改成 Create Paper Persona；成功後看到 paper_running，失敗則進 setup repair。",
        "Codex2",
        "Claude",
        "Persona Promotion Allocation / Wave 2 frontend create flow",
        "PPL-ALLOC-002,PPL-ALLOC-003",
        "create UI never reports success without paper ledger/runtime binding; PersonaOnboarding is repair-only; component tests cover success/partial/error",
        "execute-plans:src/management/pages,execute-plans:src/lib/bff-v1",
        {"wave": 2, "fleet_lane": "fe-create-paper-persona"},
    ),
    (
        "PPL-ALLOC-006",
        "Promotion and allocation workbench",
        "把 Promotion & Allocation 擴成唯一操作工作台：paper candidates、real ranking、quarterly capital、emergency actions。",
        "Claude",
        "Codex",
        "Persona Promotion Allocation / Wave 2 frontend workbench",
        "PPL-ALLOC-003,PPL-ALLOC-004",
        "workbench distinguishes recommendation/review/approved/applied; real ranking shows current/target weights and cap reasons; rebalance proposal links to detail",
        "execute-plans:src/management/pages/oversight,execute-plans:src/lib/v5/management,execute-plans:src/lib/bff-v1",
        {"wave": 2, "fleet_lane": "fe-promotion-allocation-workbench"},
    ),
    (
        "PPL-ALLOC-007",
        "Binding visibility and route prune",
        "修 Persona Fleet / Capital 顯示不同 persona 綁定到不同 paper ledger 或 real sleeve；legacy/diagnostic 頁面不再搶主流程。",
        "Antigravity",
        "Codex2",
        "Persona Promotion Allocation / Wave 2 IA and binding visibility",
        "PPL-ALLOC-003,PPL-ALLOC-006",
        "capital/persona pages show distinct binding identity; legacy routes redirect; ranking/readiness pages are diagnostics/readiness only",
        "execute-plans:src/management,execute-plans:src/App.tsx,execute-plans:e2e",
        {"wave": 2, "fleet_lane": "fe-binding-visibility-route-prune"},
    ),
    (
        "PPL-ALLOC-008",
        "Emergency containment policy",
        "實作大額虧損、hard risk breach、binding mismatch 等立即處置；只能降風險，不能晉升或加資金。",
        "Antigravity2",
        "Claude2",
        "Persona Promotion Allocation / Wave 2 emergency containment",
        "PPL-ALLOC-001,PPL-ALLOC-004",
        "containment commands are role-gated/audited; tests reject emergency promotion or allocation increase; UI labels actions as containment",
        "services/control-plane/bff,execute-plans:src/management/pages/v5,execute-plans:src/management/pages/oversight",
        {"wave": 2, "fleet_lane": "emergency-containment"},
    ),
    (
        "PPL-ALLOC-009",
        "Closeout and dev publish",
        "彙整所有任務 PR、測試、merge、dev publish 與 hosted smoke，證明 create->paper、paper->real review、real allocation、emergency containment 閉環。",
        "Codex",
        "Claude",
        "Persona Promotion Allocation / Wave 3 closeout",
        "PPL-ALLOC-002,PPL-ALLOC-003,PPL-ALLOC-004,PPL-ALLOC-005,PPL-ALLOC-006,PPL-ALLOC-007,PPL-ALLOC-008",
        "all child tasks done or reviewed superseded; hosted smoke proves full path; closeout records PRs, SHAs, deployed commits, validation, residual risks",
        "docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/archive,services/control-plane/bff,execute-plans:src",
        {"wave": 3, "fleet_lane": "production-closeout"},
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


def main() -> int:
    state = load_state()
    timestamp = iso_now()
    for task_id, title, summary, owner, reviewer, phase, deps, acceptance, artifacts, metadata in TASKS:
        task_metadata = {
            "source_ref": SOURCE_REF,
            "delivery_layer": "primary",
            "mutates_canonical": True,
            "helper_kind": "persona_promotion_allocation_execution_slice",
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
            "next": NEXT_BY_TASK.get(task_id, "Assignment created from persona promotion/allocation execution packet"),
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
        print(f"{action} {task_id:13} owner={owner:12} reviewer={reviewer:8} deps={deps or '-'}")
    state["updated_at"] = timestamp
    save_state(state)
    print("Done. Run `python3 scripts/ai_status.py sync` to refresh generated status views.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
