#!/usr/bin/env python3
"""Dispatch Persona Fleet mutation/evolution gap tasks for 2026-07-10."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATUS_ROOT = Path(os.path.expanduser(os.environ.get("PANTHEON_STATUS_ROOT", str(REPO_ROOT)))).resolve()
STATUS_PATH = STATUS_ROOT / "ai-status.json"
LOG_PATH = STATUS_ROOT / "ai-activity-log.jsonl"

AUTO_BY = "dispatch_persona_fleet_mutation_evolution_gap_2026-07-10"
PACKET = "docs/bff/execution-tasks/2026-07-10-persona-fleet-mutation-evolution-gap/INDEX.md"
SPEC = "docs/04/pantheon_management_console_mutation_evolution_gap_2026-07-10/PERSONA_FLEET_MUTATION_EVOLUTION_GAP.md"
SOURCE_REF = {
    "doc": SPEC,
    "packet": PACKET,
    "extends": "docs/bff/execution-tasks/2026-07-07-management-console-operations-workflow/INDEX.md",
    "trigger": "2026-07-10 Persona Fleet recent mutation links open Evolution Journal fallback with mutation:nan",
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
    "Assignment created from Persona Fleet mutation/evolution gap packet",
}
PRIMARY_AGENT_NEXT_TASK = {
    "Claude2": "MGMT-OPS-008",
    "Codex": "MGMT-OPS-009",
    "Antigravity": "MGMT-OPS-010",
    "Codex2": "MGMT-OPS-011",
}
NEXT_BY_TASK = {
    "MGMT-OPS-008": "Lock BFF/adapter recent mutation fields; no nan/date-as-id output.",
    "MGMT-OPS-009": "Fix Persona Fleet recent mutation links and Evolution Journal fallback semantics.",
    "MGMT-OPS-010": "Run hosted Persona Fleet click-map regression and archive target-page evidence.",
    "MGMT-OPS-011": "Close with PRs, deploy evidence, hosted screenshots, and residual-risk notes.",
}


TASKS = [
    (
        "MGMT-OPS-008",
        "Mutation / Evolution contract for Persona Fleet",
        "鎖定 Persona Fleet 最近 mutation 與 Evolution Journal 的共同契約：正式 mutation id、日期、fallback summary、confidence、diagnostics 必須分開，不准把 nan 或日期當成 mutation id。",
        "Claude2",
        "Codex",
        "Management Console Operations / Mutation Evolution Wave 0 contract",
        [],
        [
            "Persona Fleet rows expose mutation identity timestamp source kind and confidence as separate fields",
            "BFF and adapter output never use nan undefined empty strings labels or dates as mutation ids",
            "contract tests cover persona-20260528-04688755 fallback-only state",
            "formal mutation/evolution row is covered when the source exists",
        ],
        [
            "services/control-plane/bff",
            "services/control-plane/bff/tests",
            "execute-plans:src/lib/bff-v1",
            "execute-plans:src/management",
            "docs/bff/execution-tasks/2026-07-10-persona-fleet-mutation-evolution-gap/MGMT-OPS-008-mutation-evolution-contract.md",
        ],
        {"wave": 0, "fleet_lane": "ops-mutation-evolution-contract"},
    ),
    (
        "MGMT-OPS-009",
        "Persona Fleet and Evolution Journal link semantics",
        "修正 Persona Fleet 最近 MUTATION 點到 Evolution Journal 的語義：有正式 id 就進正式 entry，沒有正式 id 就進 persona fallback summary；頁面不得顯示 mutation:nan，也不得把日期顯示成 Action。",
        "Codex",
        "Claude2",
        "Management Console Operations / Mutation Evolution Wave 1 frontend",
        ["MGMT-OPS-008"],
        [
            "Persona Fleet recent mutation links remain present for formal and useful fallback states",
            "Evolution Journal shows exact formal entry content when a formal id exists",
            "fallback content says Persona Fleet status summary and no formal mutation id is available",
            "operator-facing UI and URLs do not contain mutation=nan mutation:nan source=nan or fake keys",
            "date strings render only as dates or timestamps and never as action ids",
        ],
        [
            "execute-plans:src/management/pages/oversight/personaFleetLinks.ts",
            "execute-plans:src/management/pages/oversight/evolutionJournalFocus.ts",
            "execute-plans:src/management/pages/oversight/_core.tsx",
            "execute-plans:e2e",
            "docs/bff/execution-tasks/2026-07-10-persona-fleet-mutation-evolution-gap/MGMT-OPS-009-persona-fleet-evolution-links.md",
        ],
        {"wave": 1, "fleet_lane": "fe-persona-fleet-evolution-links"},
    ),
    (
        "MGMT-OPS-010",
        "Hosted click-map regression for Persona Fleet links",
        "完整點驗 Persona Fleet 連出去的頁面，特別是最近 mutation 到 Evolution Journal：formal、fallback、missing-data 三條路都要有 hosted evidence，不能只看有畫面就算正常。",
        "Antigravity",
        "Codex",
        "Management Console Operations / Mutation Evolution Wave 2 hosted regression",
        ["MGMT-OPS-009"],
        [
            "hosted evidence proves persona-20260528-04688755 fallback path has no mutation:nan and no date-as-action field",
            "click-map catches target pages that render but show wrong content",
            "smoke is repeatable against the dev FE/BFF management target",
            "unavailable formal mutation source is recorded as an explicit diagnostic",
        ],
        [
            "execute-plans:e2e",
            "execute-plans:test-results",
            "docs/bff/execution-tasks/2026-07-10-persona-fleet-mutation-evolution-gap/MGMT-OPS-010-hosted-click-map-regression.md",
        ],
        {"wave": 2, "fleet_lane": "hosted-fleet-click-map"},
    ),
    (
        "MGMT-OPS-011",
        "Mutation / Evolution gap closeout",
        "彙整 MGMT-OPS-008/009/010 的 PR、merge commit、dev publish、hosted click-map evidence，確認 Persona Fleet 最近 mutation 連結仍存在且目標頁語義正確。",
        "Codex2",
        "Human/Ops",
        "Management Console Operations / Mutation Evolution Wave 3 closeout",
        ["MGMT-OPS-010"],
        [
            "closeout links every implementation PR and hosted proof artifact",
            "hosted dev management console no longer shows mutation:nan or fallback summaries as formal mutation entries",
            "Persona Fleet hyperlinks remain present and point to correct target pages",
            "remaining upstream formal mutation coverage gaps are explicitly recorded",
        ],
        [
            "docs/04/pantheon_management_console_mutation_evolution_gap_2026-07-10/archive",
            "docs/bff/execution-tasks/2026-07-10-persona-fleet-mutation-evolution-gap/MGMT-OPS-011-closeout.md",
            "execute-plans:test-results",
        ],
        {"wave": 3, "fleet_lane": "mutation-evolution-closeout"},
    ),
]


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_state() -> dict:
    if not STATUS_PATH.exists():
        raise FileNotFoundError(f"status file not found: {STATUS_PATH}")
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
        "next": NEXT_BY_TASK.get(task_id, "Assignment created from Persona Fleet mutation/evolution gap packet"),
        "last_update": timestamp,
        "task_class": "execution",
        "auto_created_by": AUTO_BY,
        "auto_generated": True,
        "source_ref": SOURCE_REF,
        "delivery_layer": "primary",
        "mutates_canonical": True,
        "helper_kind": "persona_fleet_mutation_evolution_gap_execution_slice",
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
        print(
            f"{'CREATE' if inserted else 'UPSERT'} {task_id:12} "
            f"owner={owner:12} reviewer={reviewer:10} deps={','.join(deps) if deps else '-'}"
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
