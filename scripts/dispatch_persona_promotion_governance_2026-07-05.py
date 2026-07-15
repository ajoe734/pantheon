#!/usr/bin/env python3
"""Dispatch persona promotion-governance execution tasks for 2026-07-05.

Spec: docs/04/pantheon_persona_promotion_governance_gap_2026-07-05/PERSONA_PROMOTION_GOVERNANCE_GAP_SPEC.md
Packet: docs/bff/execution-tasks/2026-07-05-persona-promotion-governance-gap/INDEX.md
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

from canonical_writer_guard import assert_isolated_legacy_write_target

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS_PATH = Path(REPO_ROOT) / "ai-status.json"
LOG_PATH = Path(REPO_ROOT) / "ai-activity-log.jsonl"
AUTO_BY = "dispatch_persona_promotion_governance_2026-07-05"
PACKET = "docs/bff/execution-tasks/2026-07-05-persona-promotion-governance-gap/INDEX.md"
SPEC = "docs/04/pantheon_persona_promotion_governance_gap_2026-07-05/PERSONA_PROMOTION_GOVERNANCE_GAP_SPEC.md"
SOURCE_REF = {
    "doc": SPEC,
    "packet": PACKET,
    "policy": "PAPER_CANARY_LIVE_POLICY.md",
    "runtime_model": "PERSONA_RUNTIME_MODEL.md",
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
    "Assignment created from persona promotion-governance execution packet",
}
PRIMARY_AGENT_NEXT_TASK = {
    "Codex": "PPL-GOV-001",
    "Claude2": "PPL-GOV-002",
    "Gemini2": "PPL-GOV-003",
    "Codex2": "PPL-GOV-004",
    "Claude": "PPL-GOV-005",
    "Gemini": "PPL-GOV-006",
}
NEXT_BY_TASK = {
    "PPL-GOV-001": "Lock the promotion-governance gap spec and current-state guard before code changes are accepted.",
    "PPL-GOV-002": "Implement BFF promotion-review list/detail/decision routes with role gates and idempotent decisions.",
    "PPL-GOV-003": "Implement BFF recommendation submit bridge into governance/Human Inbox without live capital mutation.",
    "PPL-GOV-004": "Wire Persona League and Quarterly Ranking submit actions to the BFF submit bridge.",
    "PPL-GOV-005": "Finish Human Inbox / Human Gate promotion-review decision UX with receipts and honest disabled states.",
    "PPL-GOV-006": "Encode emergency containment rules separately from quarterly promotion approval.",
    "PPL-GOV-007": "Run production closeout, PR merge, dev publish, and hosted smoke evidence.",
}


# (task_id, title, summary_zh, owner, reviewer, phase, depends_on, acceptance, artifacts, metadata)
TASKS = [
    (
        "PPL-GOV-001",
        "Gap spec and current-state guard",
        "鎖定 paper/canary/live 晉升治理 gap spec，確認推薦、審核、Human Inbox、資金變更的現況與缺口。",
        "Codex",
        "Claude",
        "Persona Promotion Governance / Wave 0 source truth",
        "",
        "gap spec records current implemented behavior; production acceptance is explicit; recommendation submit and approval do not directly mutate live capital",
        "docs/04/pantheon_persona_promotion_governance_gap_2026-07-05,docs/bff/execution-tasks/2026-07-05-persona-promotion-governance-gap",
        {"wave": 0, "fleet_lane": "source-truth"},
    ),
    (
        "PPL-GOV-002",
        "BFF promotion review routes",
        "新增 promotion-review list/detail/decision 管理 API，讓 paper->canary、canary->live、live ranking review 可被 Human Gate 審核。",
        "Claude2",
        "Codex",
        "Persona Promotion Governance / Wave 1 BFF review routes",
        "PPL-GOV-001",
        "read routes auth-gated; decision route approver/admin-gated; reject requires rationale; idempotency stable; liveCapitalMutation remains false",
        "services/control-plane/bff,services/control-plane/bff/tests/test_bff_promotion_reviews.py",
        {"wave": 1, "fleet_lane": "bff-promotion-reviews"},
    ),
    (
        "PPL-GOV-003",
        "BFF recommendation submit bridge",
        "把 PM-12 推薦 row 送入治理佇列並回傳 promotion review / Human Inbox id，不再只靠前端 local id。",
        "Gemini2",
        "Codex",
        "Persona Promotion Governance / Wave 1 BFF submit bridge",
        "PPL-GOV-001",
        "valid recommendation submit returns review id and links; duplicate idempotency key does not duplicate; route never mutates live capital/stage/broker state",
        "services/control-plane/bff,services/control-plane/bff/tests/test_bff_promotion_reviews.py,services/control-plane/bff/tests/test_bff_b5_humangate_commands.py",
        {"wave": 1, "fleet_lane": "bff-recommendation-submit"},
    ),
    (
        "PPL-GOV-004",
        "Frontend recommendation submit UI",
        "Persona League / Quarterly Ranking 的推薦按鈕改接 BFF 寫入治理，不再把 local-only id 當成實際審核成功。",
        "Codex2",
        "Claude",
        "Persona Promotion Governance / Wave 2 frontend submit",
        "PPL-GOV-002,PPL-GOV-003",
        "submit success links to promotion review; disabled local fallback is visibly local; tests cover success, failure, and write-disabled states",
        "execute-plans:src/lib/v5/management,execute-plans:src/management/pages/oversight,execute-plans:src/lib/bff-v1",
        {"wave": 2, "fleet_lane": "fe-recommendation-submit"},
    ),
    (
        "PPL-GOV-005",
        "Human Inbox promotion decision UX",
        "Human Inbox / Human Gate 明確呈現 promotion review，並提供 approve、approve_with_conditions、reject 的真實決策流程。",
        "Claude",
        "Codex",
        "Persona Promotion Governance / Wave 2 frontend decision",
        "PPL-GOV-002,PPL-GOV-004",
        "promotion review detail shows source recommendation, stage target, evidence, required roles, decision history, and receipt after BFF decision",
        "execute-plans:src/management/pages/oversight/HumanGateDetail.tsx,execute-plans:src/lib/bff-v1/management.ts",
        {"wave": 2, "fleet_lane": "fe-human-gate-decision"},
    ),
    (
        "PPL-GOV-006",
        "Policy and emergency risk actions",
        "把大額虧損、hard risk breach、reconciliation anomaly 等立即處置規則獨立出 quarterly promotion；只允許降風險，不允許晉升或加資金。",
        "Gemini",
        "Claude2",
        "Persona Promotion Governance / Wave 2 risk containment",
        "PPL-GOV-001",
        "emergency triggers are explicit; containment actions are audited and role-gated; tests prove emergency path cannot promote or increase live capital",
        "PAPER_CANARY_LIVE_POLICY.md,services/control-plane/bff,execute-plans:src/management",
        {"wave": 2, "fleet_lane": "risk-containment-policy"},
    ),
    (
        "PPL-GOV-007",
        "Production closeout and dev publish",
        "完成 Pantheon/Execute Plans PR、測試、merge、dev publish 與 hosted smoke，證明推薦送審到人類決策閉環已可交付。",
        "Codex",
        "Claude",
        "Persona Promotion Governance / Wave 3 closeout",
        "PPL-GOV-002,PPL-GOV-003,PPL-GOV-004,PPL-GOV-005,PPL-GOV-006",
        "PR numbers and merge SHAs recorded; hosted smoke covers recommendation submit and human decision; residual risks have owner and expiry",
        "docs/04/pantheon_persona_promotion_governance_gap_2026-07-05/archive,services/control-plane/bff,execute-plans:src",
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
    assert_isolated_legacy_write_target(STATUS_PATH, tool=Path(__file__).name)
    STATUS_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_log(entry: dict) -> None:
    assert_isolated_legacy_write_target(LOG_PATH, tool=Path(__file__).name)
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
            "helper_kind": "persona_promotion_governance_execution_slice",
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
            "next": NEXT_BY_TASK.get(task_id, "Assignment created from persona promotion-governance execution packet"),
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
        print(f"{action} {task_id:11} owner={owner:8} reviewer={reviewer:8} deps={deps or '-'}")
    state["updated_at"] = timestamp
    save_state(state)
    print("Done. Run `python3 scripts/ai_status.py sync` to refresh generated status views.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
