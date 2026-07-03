#!/usr/bin/env python3
"""Dispatch OpenClaw persona/model/memory gap execution tasks.

Spec: docs/04/pantheon_openclaw_persona_memory_gap_2026-07-03/OPENCLAW_PERSONA_MEMORY_GAP_SPEC.md
Packet: docs/bff/execution-tasks/2026-07-03-openclaw-persona-memory-gap/INDEX.md
Parent: OCLAW-PMEM-000
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
STATUS_PATH = REPO_ROOT / "ai-status.json"
LOG_PATH = REPO_ROOT / "ai-activity-log.jsonl"
AUTO_BY = "dispatch_openclaw_persona_memory_gap_2026-07-03"
PARENT_TASK_ID = "OCLAW-PMEM-000"
PACKET = "docs/bff/execution-tasks/2026-07-03-openclaw-persona-memory-gap/INDEX.md"
SPEC = "docs/04/pantheon_openclaw_persona_memory_gap_2026-07-03/OPENCLAW_PERSONA_MEMORY_GAP_SPEC.md"
SOURCE_REF = {
    "doc": SPEC,
    "packet": PACKET,
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
    "review_notes_zh",
    "review_file",
    "delivery",
    "terminal_outcome",
}
TERMINAL_STATUSES = {"done", "superseded", "cancelled"}
GENERIC_NEXT_MESSAGES = {
    None,
    "",
    "Assignment created",
    "Assignment created from OpenClaw persona/memory gap execution packet",
}


def task(
    *,
    task_id: str,
    title: str,
    summary_zh: str,
    owner: str,
    reviewer: str,
    phase: str,
    depends_on: list[str],
    artifacts: list[str],
    acceptance: list[str],
    next_note: str,
    wave: int,
    fleet_lane: str,
) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": title,
        "summary_zh": summary_zh,
        "phase": phase,
        "owner": owner,
        "reviewer": reviewer,
        "status": "todo",
        "depends_on": depends_on,
        "artifacts": artifacts,
        "acceptance": acceptance,
        "next": next_note,
        "task_class": "execution",
        "auto_created_by": AUTO_BY,
        "auto_generated": True,
        "source_ref": SOURCE_REF,
        "delivery_layer": "primary",
        "mutates_canonical": True,
        "helper_parent": PARENT_TASK_ID,
        "helper_kind": "openclaw_persona_memory_gap_execution_slice",
        "wave": wave,
        "fleet_lane": fleet_lane,
    }


PARENT_TASK = task(
    task_id=PARENT_TASK_ID,
    title="OpenClaw persona model routing and memory architecture gap",
    summary_zh=(
        "釐清並修正 Persona/OpenClaw/provider pool/Memory Plane 的 source-of-truth 邊界；"
        "把 runtime profile、OpenClaw agent sync、canonical memory bridge、BFF/UI surfaces "
        "與 dev gates 拆成可驗收 execution tasks。"
    ),
    owner="Codex",
    reviewer="Claude",
    phase="OpenClaw Persona Memory Gap / Umbrella",
    depends_on=["OCLAW-PMEM-005"],
    artifacts=[
        "docs/04/pantheon_openclaw_persona_memory_gap_2026-07-03",
        "docs/bff/execution-tasks/2026-07-03-openclaw-persona-memory-gap",
        "integrations/openclaw",
        "services/control-plane/bff",
        "services/control-plane/persona",
        "services/memory",
        "services/persona",
    ],
    acceptance=[
        "OCLAW-PMEM-005 done or reviewer-approved superseded",
        "final closeout includes PR SHAs, validation, hosted dev evidence, and residual risks",
        "Management UI explains persona model routing and canonical memory materialization",
        "dev gates prove provider live smoke, persona OpenClaw response, memory retrieval/materialization, and no private memory leakage",
    ],
    next_note="Wait for OCLAW-PMEM-005 closeout; do not mark done from local UI state alone.",
    wave=99,
    fleet_lane="oversight-closeout",
)
PARENT_TASK["gap_children"] = [
    "OCLAW-PMEM-001",
    "OCLAW-PMEM-002",
    "OCLAW-PMEM-003",
    "OCLAW-PMEM-004",
    "OCLAW-PMEM-005",
]


TASKS = [
    task(
        task_id="OCLAW-PMEM-001",
        title="Persona runtime profile and model routing contract",
        summary_zh=(
            "定義 PersonaRuntimeProfile 與 model_routing contract，讓 persona 使用 shared "
            "provider/model pool，而不是隱性 preferred_model 或一 persona 一 auth。"
        ),
        owner="Claude",
        reviewer="Codex",
        phase="OpenClaw Persona Memory Gap / Wave 0 contract",
        depends_on=[],
        artifacts=[
            "services/control-plane/persona",
            "services/control-plane/bff",
            "integrations/openclaw/model-pool-and-persona-routing.md",
            "docs/bff/execution-tasks/2026-07-03-openclaw-persona-memory-gap/OCLAW-PMEM-001-runtime-profile-contract.md",
        ],
        acceptance=[
            "PersonaRuntimeProfile contract/schema exists with model_routing and memory_policy",
            "BFF exposes read-only runtime profile surface with source_refs",
            "invalid provider/model references fail closed with operator-visible reason",
            "tests prove personas map many-to-few onto the shared provider pool",
        ],
        next_note="Define the runtime profile contract before OpenClaw sync or UI work proceeds.",
        wave=0,
        fleet_lane="persona-runtime-contract",
    ),
    task(
        task_id="OCLAW-PMEM-002",
        title="OpenClaw persona agent reconciliation",
        summary_zh=(
            "把 general persona create/update 接到 shared OpenClaw reconciler；既有 agent 要能"
            "同步 identity/workspace/model/SOUL，並消除 deploy script 與 library 的 SOUL drift。"
        ),
        owner="Codex2",
        reviewer="Claude",
        phase="OpenClaw Persona Memory Gap / Wave 1 agent reconcile",
        depends_on=["OCLAW-PMEM-001"],
        artifacts=[
            "integrations/openclaw/persona_agent_sync.py",
            "scripts/openclaw-sync-persona-agents.py",
            "services/control-plane/bff/main.py",
            "services/control-plane/bff/agora/servant",
            "docs/bff/execution-tasks/2026-07-03-openclaw-persona-memory-gap/OCLAW-PMEM-002-openclaw-agent-reconcile.md",
        ],
        acceptance=[
            "BFF persona create/update produces a reachable openclaw/{persona_id} agent or a failed reconcile reason",
            "existing-agent model drift is repaired or blocked with a precise repair action",
            "SOUL renderer parity tests cover deploy script and library output including Memory section",
            "dev evidence shows a persona response through model=openclaw/{persona_id}",
        ],
        next_note="Wait for OCLAW-PMEM-001, then reconcile existing and new OpenClaw persona agents.",
        wave=1,
        fleet_lane="openclaw-agent-reconcile",
    ),
    task(
        task_id="OCLAW-PMEM-003",
        title="Canonical memory bridge to OpenClaw workspace",
        summary_zh=(
            "建立 Memory Plane 到 OpenClaw workspace 的 materialization bridge；OpenClaw workspace "
            "只能是 cache，不是第二個 memory source of truth。"
        ),
        owner="Gemini2",
        reviewer="Codex",
        phase="OpenClaw Persona Memory Gap / Wave 1 memory bridge",
        depends_on=["OCLAW-PMEM-001"],
        artifacts=[
            "services/memory",
            "services/persona",
            "integrations/openclaw",
            "docs/bff/execution-tasks/2026-07-03-openclaw-persona-memory-gap/OCLAW-PMEM-003-memory-bridge.md",
        ],
        acceptance=[
            "canonical PersonaMemory/InstitutionalMemory hits materialize into OpenClaw workspace with source IDs",
            "private PersonaMemory is not materialized for another persona",
            "OpenClaw memory candidates cannot directly mutate canonical memory without authorized writeback",
            "dev evidence shows generated workspace memory context from canonical PersonaMemory",
        ],
        next_note="Wait for OCLAW-PMEM-001, then implement Memory Plane materialization and writeback candidate flow.",
        wave=1,
        fleet_lane="canonical-memory-bridge",
    ),
    task(
        task_id="OCLAW-PMEM-004",
        title="BFF and Management runtime surfaces",
        summary_zh=(
            "把 BFF/UI 改成顯示 runtime profile、provider pool health、persona memory source、quota/usage "
            "與 reauth 狀態；不要用 mount ready 假裝 provider 可用。"
        ),
        owner="Claude2",
        reviewer="Codex",
        phase="OpenClaw Persona Memory Gap / Wave 2 BFF UI surfaces",
        depends_on=["OCLAW-PMEM-002", "OCLAW-PMEM-003"],
        artifacts=[
            "services/control-plane/bff/main.py",
            "services/memory",
            "frontend-checkout:src",
            "docs/bff/execution-tasks/2026-07-03-openclaw-persona-memory-gap/OCLAW-PMEM-004-bff-ui-runtime-surfaces.md",
        ],
        acceptance=[
            "BFF persona memory surface reads canonical Memory Plane or reports precise unavailable reason",
            "LLM Auth panel separates provider auth, live smoke, quota source, persona dependencies, and reauth flow state",
            "reauth code-entry UX appears when the provider flow requires code entry and rechecks readiness after completion",
            "tests cover BFF DTOs and UI degraded states for codex/claude/openclaw",
        ],
        next_note="Wait for OCLAW-PMEM-002 and OCLAW-PMEM-003, then wire BFF and Management UI surfaces.",
        wave=2,
        fleet_lane="bff-management-runtime-surfaces",
    ),
    task(
        task_id="OCLAW-PMEM-005",
        title="Dev gates and gap closeout",
        summary_zh=(
            "建立 end-to-end dev gates，證明 provider live smoke、persona OpenClaw response、canonical memory "
            "retrieval/materialization 與 private memory isolation，再關閉 parent gap。"
        ),
        owner="Codex",
        reviewer="Claude",
        phase="OpenClaw Persona Memory Gap / Wave 3 gates closeout",
        depends_on=["OCLAW-PMEM-002", "OCLAW-PMEM-003", "OCLAW-PMEM-004"],
        artifacts=[
            "scripts",
            "integrations/openclaw",
            "services/control-plane/bff",
            "services/memory",
            "docs/04/pantheon_openclaw_persona_memory_gap_2026-07-03/archive",
            "docs/bff/execution-tasks/2026-07-03-openclaw-persona-memory-gap/OCLAW-PMEM-005-dev-gates-closeout.md",
        ],
        acceptance=[
            "final closeout lists child task PRs, merge SHAs, validation commands, dev evidence, and residual risks",
            "gate fails when provider mount is ready but live provider smoke fails",
            "gate fails when BFF persona memory does not return canonical memory",
            "gate fails when workspace memory lacks canonical source IDs or leaks private memory across personas",
        ],
        next_note="Wait for OCLAW-PMEM-002/003/004, then add dev gates and close OCLAW-PMEM-000 with evidence.",
        wave=3,
        fleet_lane="dev-gates-closeout",
    ),
]


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_state() -> dict[str, Any]:
    return json.loads(STATUS_PATH.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any]) -> None:
    STATUS_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_log(entry: dict[str, Any]) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def upsert_task(state: dict[str, Any], new_task: dict[str, Any]) -> tuple[bool, str]:
    tasks = state.setdefault("tasks", [])
    for index, existing in enumerate(tasks):
        if existing.get("id") == new_task["id"]:
            merged = {**existing, **new_task}
            if existing.get("status") and existing.get("status") != "todo":
                for key in PROGRESS_FIELDS:
                    if key in existing:
                        merged[key] = existing[key]
            tasks[index] = merged
            return False, str(merged.get("status") or "")
    tasks.append(new_task)
    return True, str(new_task.get("status") or "")


def remove_terminal_task_from_agents(state: dict[str, Any], task_id: str) -> None:
    for agent in state.get("agents", []):
        ids = agent.get("current_task_ids")
        if isinstance(ids, list):
            agent["current_task_ids"] = [item for item in ids if item != task_id]


def assign_agent(
    state: dict[str, Any],
    owner: str,
    task_id: str,
    timestamp: str,
    next_note: str,
    inserted: bool,
) -> None:
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


def dispatch(dry_run: bool) -> int:
    state = load_state()
    timestamp = iso_now()
    pending_logs: list[dict[str, Any]] = []
    created_count = 0

    for item in [PARENT_TASK, *TASKS]:
        task_record = {**item, "last_update": timestamp}
        inserted, status_after = upsert_task(state, task_record)
        if inserted:
            created_count += 1
            pending_logs.append(
                {
                    "ts": timestamp,
                    "agent": os.environ.get("AI_NAME", "Codex"),
                    "type": "assign",
                    "task_id": item["id"],
                    "message": f"Assigned {item['id']} to {item['owner']} with reviewer {item['reviewer']}",
                }
            )
        if status_after in TERMINAL_STATUSES:
            remove_terminal_task_from_agents(state, item["id"])
        else:
            assign_agent(state, item["owner"], item["id"], timestamp, item["next"], inserted)
        action = "CREATE" if inserted else "UPSERT"
        print(
            f"{action} {item['id']:15} owner={item['owner']:8} "
            f"reviewer={item['reviewer']:8} deps={','.join(item['depends_on']) or '-'}"
        )

    state["updated_at"] = timestamp
    if dry_run:
        print(f"DRY-RUN: would create {created_count} new task(s); ai-status.json not written.")
        return 0

    save_state(state)
    for entry in pending_logs:
        append_log(entry)
    print("Done. Run `python3 scripts/ai_status.py sync` to refresh generated status views.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return dispatch(dry_run=bool(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
