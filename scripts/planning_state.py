#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
ROOT = THIS_DIR.parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import ai_status

PLANNING_DIR = ROOT / "docs" / "02-architecture" / "consensus" / "phase1"
SESSION_FILE = PLANNING_DIR / "planning-session.json"
README_FILE = PLANNING_DIR / "README.md"
READOUT_TEMPLATE_FILE = PLANNING_DIR / "LLM_READOUT_TEMPLATE.md"
CHECKLIST_FILE = PLANNING_DIR / "pantheon-backend-completion-checklist.md"
STARTER_DRAFT_FILE = PLANNING_DIR / "starter-draft.md"
BATON_LOG_FILE = PLANNING_DIR / "baton-log.md"
SUPERVISOR_QUEUE_FILE = PLANNING_DIR / "supervisor-queue.md"
CONSENSUS_PACKET_FILE = PLANNING_DIR / "consensus-packet.md"
ROUND_GLOB = "review-round-*.md"
DERIVED_STATE_FILE = ROOT / ".orchestrator" / "planning-state.json"

AGENT_ORDER = ["Claude", "Codex", "Gemini", "Qwen", "Copilot"]
BATON_SEQUENCE = ["Codex", "Qwen", "Gemini", "Copilot", "Claude"]
REVIEW_SEQUENCE = ["Qwen", "Gemini", "Copilot", "Claude"]
ROUND_WILDCARD_PATH = "docs/02-architecture/consensus/phase1/review-round-*.md"
PLANNING_STATUS_LABELS = {
    "inactive",
    "active",
    "human_required",
    "accepted",
}
CONSENSUS_STATUS_LABELS = {
    "not_started",
    "draft",
    "in_review",
    "ready_for_human",
    "human_required",
    "accepted",
}
HUMAN_GATE_STATUS_LABELS = {
    "not_requested",
    "pending",
    "approved",
    "rejected",
}


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def relative_to_root(path: Path) -> str:
    return str(path.relative_to(ROOT))


def readout_file_for(agent: str) -> Path:
    return PLANNING_DIR / f"{agent.lower()}-readout.md"


def command_usage() -> str:
    return """\
Usage:
  python3 scripts/planning_state.py sync
  python3 scripts/planning_state.py start <session-id> [summary]
  python3 scripts/planning_state.py baton <owner> [next-reviewer] [message]
  python3 scripts/planning_state.py readout <agent> [status] [message]
  python3 scripts/planning_state.py round <round-number> <status> [message]
  python3 scripts/planning_state.py issue <issue-id> <severity> <status> <summary>
  python3 scripts/planning_state.py consensus <status> [message]
  python3 scripts/planning_state.py human-gate <status> [message]
  python3 scripts/planning_state.py propose-task <task-id> <owner> <reviewer> <title>
"""


def default_artifacts() -> dict[str, dict[str, Any]]:
    return {
        "planning_readme": {
            "path": relative_to_root(README_FILE),
            "status": "ready",
        },
        "planning_session": {
            "path": relative_to_root(SESSION_FILE),
            "status": "ready",
        },
        "readout_template": {
            "path": relative_to_root(READOUT_TEMPLATE_FILE),
            "status": "ready",
        },
        "backend_completion_checklist": {
            "path": relative_to_root(CHECKLIST_FILE),
            "status": "ready",
        },
        "starter_draft": {
            "path": relative_to_root(STARTER_DRAFT_FILE),
            "status": "draft",
        },
        "baton_log": {
            "path": relative_to_root(BATON_LOG_FILE),
            "status": "ready",
        },
        "supervisor_queue": {
            "path": relative_to_root(SUPERVISOR_QUEUE_FILE),
            "status": "ready",
        },
        "consensus_packet": {
            "path": relative_to_root(CONSENSUS_PACKET_FILE),
            "status": "not_started",
        },
        "review_rounds": {
            "path": ROUND_WILDCARD_PATH,
            "status": "not_started",
        },
    }


def default_readouts() -> dict[str, dict[str, Any]]:
    return {
        agent: {
            "path": relative_to_root(readout_file_for(agent)),
            "status": "pending",
            "updated_at": None,
        }
        for agent in AGENT_ORDER
    }


def default_session() -> dict[str, Any]:
    timestamp = iso_now()
    return {
        "session_id": "phase1-bootstrap",
        "phase": "phase1",
        "status": "inactive",
        "planning_mode": "discussion_planning",
        "runtime_mode": "supervisor_managed_execution",
        "summary": "Align architecture, delivery order, and task slicing before materializing execution work.",
        "facilitator": "Claude",
        "baton_owner": "Codex",
        "starter_owner": "Codex",
        "next_reviewer": "Qwen",
        "baton_sequence": BATON_SEQUENCE,
        "review_sequence": REVIEW_SEQUENCE,
        "current_round": 0,
        "consensus_status": "not_started",
        "human_gate_status": "not_requested",
        "artifacts": default_artifacts(),
        "readouts": default_readouts(),
        "cross_review_rounds": [],
        "unresolved_items": [],
        "proposed_execution_tasks": [],
        "recent_events": [],
        "updated_at": timestamp,
    }


def planning_readme_template() -> str:
    return """# Discussion Planning Mode

This directory is the canonical workspace for `discussion_planning`.

## Goal

Before execution tasks are created, every lane should align on:

- architecture and ownership boundaries
- delivery order / wave order
- task slicing and reviewer assignment

## Canonical Files

Read in this order when a planning session is active:

1. `README.md`
2. `planning-session.json`
3. `pantheon-backend-completion-checklist.md`
4. `starter-draft.md`
5. `consensus-packet.md`
6. the current `*-readout.md` and `review-round-*.md` files

## Baton Loop

1. all lanes read canonical docs in L0 -> L1 -> L2 order
2. each lane writes an independent readout using `LLM_READOUT_TEMPLATE.md`
3. `Codex` starts the first `starter-draft.md`
4. `Qwen -> Gemini -> Copilot -> Claude` perform cited cross-review round by round
5. unresolved disagreements become explicit `human_required` items
6. `Claude` synthesizes `consensus-packet.md`
7. after human acceptance, convert the agreed slices into execution tasks through `scripts/ai-status.sh`

## Rules

- only the current baton owner edits `starter-draft.md`
- reviewers do not directly rewrite the shared draft
- `planning-session.json` is the machine-readable source of truth for planning state
- `.orchestrator/planning-state.json` is derived for the dashboard
- execution tasks stay in `ai-status.json`; do not mix planning drafts into the execution board too early

## Commands

```bash
./scripts/planning-state.sh start phase1 "Kick off the planning session"
./scripts/planning-state.sh readout Codex submitted "Codex readout is ready"
./scripts/planning-state.sh baton Qwen Gemini "Baton moved to Qwen for cross-review"
./scripts/planning-state.sh round 1 open "Opened cited cross-review round 1"
./scripts/planning-state.sh consensus ready_for_human "Consensus packet drafted and ready"
./scripts/planning-state.sh human-gate approved "Human accepted the packet"
./scripts/planning-state.sh propose-task W3-001A Qwen Claude "Callcenter & CTI correlation baseline"
./scripts/sync-state.sh
```
"""


def readout_template() -> str:
    return """# LLM Readout Template

## Lane

- Agent:
- Capability focus:

## Canonical Sources Read

- L0:
- L1:
- L2:

## Working Interpretation

- Architecture summary:
- Delivery order:
- Ownership boundaries:

## Risks / Contradictions

- Risk 1:
- Risk 2:

## Suggested Task Slices

- Slice 1:
- Slice 2:

## Citations

- [source] claim
"""


def backend_completion_checklist_template() -> str:
    return """# Pantheon Backend Completion Checklist

Use this file to anchor discussion planning around the real implementation gap between Pantheon contracts and executable backend surfaces.

## Completed and Code-Backed

- Core governance, runtime, telemetry, lineage, and incident foundations exist in code and are already represented on the execution board.
- APP-001 and APP-002 architecture / contract documents are complete enough to use as the shared design baseline.
- Multi-repo delivery coordination, Lovable handoff publishing, and GitHub coordination bus scaffolding now exist in the orchestrator.

## Implemented API Surfaces Today

- BFF currently exposes:
  - `GET /health`
  - `POST /api/v1/operator/commands`
  - `GET /api/v1/operator/commands/{command_id}`
- Internal control path currently exposes:
  - deployment approve
  - runtime pause
  - rollback execute
  - kill-switch activate
  - command status lookup
- `pantheon-admin` exists as a scaffold CLI, not a fully wired production operator path.

## Contract-Defined but Not Fully Implemented Yet

- 33 BFF read routes in `services/control-plane/bff/BFF_API_CONTRACT.md`
- 4 operator composed views
- 3 SSE feeds
- Generic operator command flow still uses stub execution instead of the full governance/runtime path
- Internal API and CLI still contain placeholder behavior

## Discussion Goals

- Confirm which read surfaces are must-have for the first front-end integration wave.
- Decide whether the first write path remains generic `POST /api/v1/operator/commands` or if resource-shaped routes are required immediately.
- Slice the missing backend work into execution-ready tasks only after all lane readouts and cited review are complete.
"""


def starter_draft_template() -> str:
    return """# Starter Draft

Current rule: only the baton owner edits this file directly.

## Shared Draft

- Objective:
- Scope boundary:
- Proposed wave order:
- Proposed task slices:
- Open disagreements:
"""


def baton_log_template() -> str:
    return """# Baton Log

| Timestamp | From | To | Message |
|---|---|---|---|
| _(pending)_ | - | Codex | Bootstrap baton owner |
"""


def supervisor_queue_template() -> str:
    return """# Supervisor Queue

| Order | Item | Owner | Status | Notes |
|---|---|---|---|---|
| 1 | Collect lane readouts | All lanes | pending | Use `LLM_READOUT_TEMPLATE.md` |
| 2 | Create starter draft | Codex | pending | First editable shared draft |
| 3 | Run cited cross-review | Qwen -> Gemini -> Copilot -> Claude | pending | One round at a time |
| 4 | Draft consensus packet | Claude | pending | Escalate unresolved semantic conflicts |
| 5 | Wait for human acceptance | Human | pending | Required before execution task materialization |
"""


def consensus_packet_template() -> str:
    return """# Consensus Packet

## Decision Summary

- Scope:
- Accepted architecture:
- Delivery order:

## Agreed Task Slices

- Task 1:
- Task 2:

## Open Questions / Human Gate

- Item 1:

## Acceptance Note

- Waiting for human acceptance.
"""


def readout_file_template(agent: str) -> str:
    return f"""# {agent} Readout

Use `LLM_READOUT_TEMPLATE.md` as the structure for this lane.
"""


def review_round_template(round_no: int) -> str:
    return f"""# Review Round {round_no:02d}

Use cited comments only. Do not directly rewrite `starter-draft.md` unless you currently hold the baton.

## Reviewer Order

- Qwen
- Gemini
- Copilot
- Claude

## Comments

- _(pending)_
"""


def ensure_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def ensure_artifact_files() -> None:
    ensure_text_file(README_FILE, planning_readme_template())
    ensure_text_file(READOUT_TEMPLATE_FILE, readout_template())
    ensure_text_file(CHECKLIST_FILE, backend_completion_checklist_template())
    ensure_text_file(STARTER_DRAFT_FILE, starter_draft_template())
    ensure_text_file(BATON_LOG_FILE, baton_log_template())
    ensure_text_file(SUPERVISOR_QUEUE_FILE, supervisor_queue_template())
    ensure_text_file(CONSENSUS_PACKET_FILE, consensus_packet_template())
    for agent in AGENT_ORDER:
        ensure_text_file(readout_file_for(agent), readout_file_template(agent))
    ensure_text_file(PLANNING_DIR / "review-round-01.md", review_round_template(1))


def read_text_or_empty(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def path_updated_at(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def file_differs_from_template(path: Path, template: str) -> bool:
    content = read_text_or_empty(path).strip()
    if not content:
        return False
    return content != template.strip()


def round_number_from_path(path: Path) -> int | None:
    name = path.stem
    if not name.startswith("review-round-"):
        return None
    suffix = name.replace("review-round-", "", 1)
    try:
        return int(suffix)
    except ValueError:
        return None


def deep_merge_dict(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_dict(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def canonical_agent(value: Any, fallback: str = "") -> str:
    candidate = ai_status.canonical_agent_name(str(value or "").strip())
    return candidate or fallback


def mapping_entry(mapping: dict[str, Any], key: str) -> Any:
    if key in mapping:
        return mapping[key]
    lowered = key.lower()
    for existing_key, value in mapping.items():
        if str(existing_key).lower() == lowered:
            return value
    return None


def load_session() -> dict[str, Any]:
    ensure_artifact_files()
    if not SESSION_FILE.exists():
        session = default_session()
        save_session(session)
        return session
    raw = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("planning-session.json must contain a JSON object")
    return normalize_session(raw)


def normalize_session(raw: dict[str, Any]) -> dict[str, Any]:
    session = deep_merge_dict(default_session(), raw)
    session["status"] = str(session.get("status") or "inactive")
    if session["status"] not in PLANNING_STATUS_LABELS:
        session["status"] = "inactive"

    session["planning_mode"] = "discussion_planning"
    session["runtime_mode"] = "supervisor_managed_execution"
    session["facilitator"] = canonical_agent(session.get("facilitator"), "Claude")
    session["baton_owner"] = canonical_agent(session.get("baton_owner"), "Codex")
    session["starter_owner"] = canonical_agent(session.get("starter_owner"), "Codex")
    session["next_reviewer"] = canonical_agent(session.get("next_reviewer"), "Qwen")

    baton_sequence = [canonical_agent(item) for item in session.get("baton_sequence", BATON_SEQUENCE)]
    session["baton_sequence"] = [item for item in baton_sequence if item in AGENT_ORDER] or BATON_SEQUENCE
    review_sequence = [canonical_agent(item) for item in session.get("review_sequence", REVIEW_SEQUENCE)]
    session["review_sequence"] = [item for item in review_sequence if item in AGENT_ORDER] or REVIEW_SEQUENCE

    session["consensus_status"] = str(session.get("consensus_status") or "not_started")
    if session["consensus_status"] not in CONSENSUS_STATUS_LABELS:
        session["consensus_status"] = "not_started"
    session["human_gate_status"] = str(session.get("human_gate_status") or "not_requested")
    if session["human_gate_status"] not in HUMAN_GATE_STATUS_LABELS:
        session["human_gate_status"] = "not_requested"

    artifacts = default_artifacts()
    incoming_artifacts = session.get("artifacts") if isinstance(session.get("artifacts"), dict) else {}
    for key, artifact in artifacts.items():
        incoming = mapping_entry(incoming_artifacts, key)
        if isinstance(incoming, dict):
            artifact.update({k: v for k, v in incoming.items() if v is not None})
    session["artifacts"] = artifacts

    readouts = default_readouts()
    incoming_readouts = session.get("readouts") if isinstance(session.get("readouts"), dict) else {}
    for agent in AGENT_ORDER:
        incoming = mapping_entry(incoming_readouts, agent)
        if isinstance(incoming, dict):
            readouts[agent].update({k: v for k, v in incoming.items() if v is not None})
    session["readouts"] = readouts

    rounds: list[dict[str, Any]] = []
    for entry in session.get("cross_review_rounds", []):
        if not isinstance(entry, dict):
            continue
        try:
            round_no = int(entry.get("round"))
        except (TypeError, ValueError):
            continue
        rounds.append(
            {
                "round": round_no,
                "status": str(entry.get("status") or "open"),
                "reviewers": [canonical_agent(item) for item in entry.get("reviewers", session["review_sequence"]) if canonical_agent(item)],
                "updated_at": entry.get("updated_at"),
                "summary": entry.get("summary") or "",
            }
        )
    rounds.sort(key=lambda item: item["round"])
    session["cross_review_rounds"] = rounds
    session["current_round"] = max([item["round"] for item in rounds], default=int(session.get("current_round") or 0))

    unresolved_items: list[dict[str, Any]] = []
    for entry in session.get("unresolved_items", []):
        if not isinstance(entry, dict):
            continue
        unresolved_items.append(
            {
                "id": str(entry.get("id") or "").strip(),
                "severity": str(entry.get("severity") or "medium"),
                "status": str(entry.get("status") or "open"),
                "summary": str(entry.get("summary") or "").strip(),
            }
        )
    session["unresolved_items"] = [item for item in unresolved_items if item["id"] or item["summary"]]

    proposed_execution_tasks: list[dict[str, Any]] = []
    for entry in session.get("proposed_execution_tasks", []):
        if not isinstance(entry, dict):
            continue
        task_id = str(entry.get("id") or "").strip()
        if not task_id:
            continue
        proposed_execution_tasks.append(
            {
                "id": task_id,
                "title": str(entry.get("title") or "").strip(),
                "owner": canonical_agent(entry.get("owner"), "Codex"),
                "reviewer": canonical_agent(entry.get("reviewer"), "Claude"),
                "phase": str(entry.get("phase") or "Planning Materialized"),
                "summary_zh": str(entry.get("summary_zh") or "").strip(),
            }
        )
    session["proposed_execution_tasks"] = proposed_execution_tasks

    recent_events: list[dict[str, Any]] = []
    for entry in session.get("recent_events", []):
        if not isinstance(entry, dict):
            continue
        payload = {
            "ts": entry.get("ts") or entry.get("updated_at") or iso_now(),
            "agent": canonical_agent(entry.get("agent"), ai_status.current_actor("Codex")),
            "type": str(entry.get("type") or "planning_event"),
            "message": str(entry.get("message") or "").strip(),
        }
        for extra_key in ("task_id", "issue_id", "status", "round", "owner", "reviewer"):
            if entry.get(extra_key) is not None:
                payload[extra_key] = entry.get(extra_key)
        recent_events.append(payload)
    session["recent_events"] = recent_events[-40:]
    session["updated_at"] = session.get("updated_at") or iso_now()
    auto_detect_artifact_activity(session)
    return session


def save_session(session: dict[str, Any]) -> None:
    ensure_artifact_files()
    session["updated_at"] = iso_now()
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps(session, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_event(session: dict[str, Any], event_type: str, message: str, **extra: Any) -> None:
    event = {
        "ts": iso_now(),
        "agent": ai_status.current_actor("Codex"),
        "type": event_type,
        "message": message,
    }
    event.update({key: value for key, value in extra.items() if value is not None})
    session.setdefault("recent_events", []).append(event)
    session["recent_events"] = session["recent_events"][-40:]


def next_in_sequence(sequence: list[str], current: str, fallback: str) -> str:
    if current not in sequence:
        return fallback
    index = sequence.index(current)
    if index + 1 < len(sequence):
        return sequence[index + 1]
    return fallback


def derive_switch_gate(session: dict[str, Any]) -> dict[str, bool]:
    submitted_statuses = {"submitted", "accepted", "waived"}
    all_readouts_submitted = all(
        str(entry.get("status") or "").lower() in submitted_statuses
        for entry in session.get("readouts", {}).values()
    )
    cross_review_round_present = len(session.get("cross_review_rounds", [])) > 0
    divergence_resolved_or_escalated = all(
        str(item.get("status") or "").lower() in {"resolved", "human_required", "accepted", "tracking"}
        for item in session.get("unresolved_items", [])
    )
    consensus_packet_drafted = session.get("consensus_status") in {
        "draft",
        "in_review",
        "ready_for_human",
        "human_required",
        "accepted",
    }
    human_approved = session.get("human_gate_status") == "approved"
    prereqs_satisfied = (
        all_readouts_submitted
        and cross_review_round_present
        and divergence_resolved_or_escalated
        and consensus_packet_drafted
    )
    return {
        "all_readouts_submitted": all_readouts_submitted,
        "cross_review_round_present": cross_review_round_present,
        "divergence_resolved_or_escalated": divergence_resolved_or_escalated,
        "consensus_packet_drafted": consensus_packet_drafted,
        "human_approved": human_approved,
        "ready_for_human": prereqs_satisfied or human_approved,
        "ready_to_materialize": human_approved or prereqs_satisfied,
    }


def auto_detect_artifact_activity(session: dict[str, Any]) -> None:
    activity_detected = False

    if file_differs_from_template(CHECKLIST_FILE, backend_completion_checklist_template()):
        session["artifacts"]["backend_completion_checklist"]["status"] = "active"
        activity_detected = True

    if file_differs_from_template(STARTER_DRAFT_FILE, starter_draft_template()):
        session["artifacts"]["starter_draft"]["status"] = "active"
        activity_detected = True
        if session.get("consensus_status") == "not_started":
            session["consensus_status"] = "draft"

    if file_differs_from_template(BATON_LOG_FILE, baton_log_template()):
        session["artifacts"]["baton_log"]["status"] = "active"
        activity_detected = True

    if file_differs_from_template(SUPERVISOR_QUEUE_FILE, supervisor_queue_template()):
        session["artifacts"]["supervisor_queue"]["status"] = "active"
        activity_detected = True

    if file_differs_from_template(CONSENSUS_PACKET_FILE, consensus_packet_template()):
        session["artifacts"]["consensus_packet"]["status"] = "draft"
        activity_detected = True
        if session.get("consensus_status") == "not_started":
            session["consensus_status"] = "draft"

    for agent in AGENT_ORDER:
        readout_path = readout_file_for(agent)
        if file_differs_from_template(readout_path, readout_file_template(agent)):
            current_status = str(session["readouts"][agent].get("status") or "pending")
            if current_status == "pending":
                session["readouts"][agent]["status"] = "submitted"
            session["readouts"][agent]["updated_at"] = path_updated_at(readout_path)
            activity_detected = True

    discovered_rounds: list[dict[str, Any]] = []
    existing_rounds = {entry["round"]: entry for entry in session.get("cross_review_rounds", [])}
    for round_path in sorted(PLANNING_DIR.glob(ROUND_GLOB)):
        round_no = round_number_from_path(round_path)
        if round_no is None:
            continue
        if file_differs_from_template(round_path, review_round_template(round_no)):
            activity_detected = True
            existing = existing_rounds.get(round_no)
            discovered_rounds.append(
                {
                    "round": round_no,
                    "status": existing.get("status") if existing else "open",
                    "reviewers": existing.get("reviewers") if existing else REVIEW_SEQUENCE,
                    "updated_at": path_updated_at(round_path),
                    "summary": existing.get("summary") if existing else f"Detected updates in {round_path.name}",
                }
            )

    if discovered_rounds:
        merged_rounds = {entry["round"]: entry for entry in session.get("cross_review_rounds", [])}
        for entry in discovered_rounds:
            merged_rounds[entry["round"]] = entry
        session["cross_review_rounds"] = [merged_rounds[key] for key in sorted(merged_rounds)]
        session["current_round"] = max(session.get("current_round", 0), max(item["round"] for item in discovered_rounds))

    if session.get("status") not in {"accepted", "human_required"}:
        if session.get("consensus_status") == "accepted" or session.get("human_gate_status") == "approved":
            session["status"] = "accepted"
        elif session.get("consensus_status") == "human_required" or any(
            str(item.get("status") or "").lower() == "human_required"
            for item in session.get("unresolved_items", [])
        ):
            session["status"] = "human_required"
        elif activity_detected or session.get("proposed_execution_tasks") or session.get("recent_events"):
            session["status"] = "active"


def derive_artifact_statuses(session: dict[str, Any]) -> None:
    session["artifacts"]["planning_session"]["status"] = session["status"]
    if session["artifacts"]["starter_draft"]["status"] == "draft" and session["consensus_status"] != "not_started":
        session["artifacts"]["starter_draft"]["status"] = "active"
    if session["artifacts"]["baton_log"]["status"] == "ready" and session["status"] != "inactive":
        session["artifacts"]["baton_log"]["status"] = "active"
    if session["artifacts"]["supervisor_queue"]["status"] == "ready" and session["status"] != "inactive":
        session["artifacts"]["supervisor_queue"]["status"] = "active"
    consensus_status = str(session.get("consensus_status") or "not_started")
    if consensus_status in CONSENSUS_STATUS_LABELS and consensus_status != "not_started":
        session["artifacts"]["consensus_packet"]["status"] = consensus_status
    elif session["artifacts"]["consensus_packet"]["status"] == "not_started":
        session["artifacts"]["consensus_packet"]["status"] = consensus_status
    session["artifacts"]["review_rounds"]["status"] = (
        "not_started"
        if not session.get("cross_review_rounds")
        else "completed"
        if all(item.get("status") == "completed" for item in session.get("cross_review_rounds", []))
        else "in_progress"
    )


def build_derived_state(session: dict[str, Any]) -> dict[str, Any]:
    derived = normalize_session(session)
    derive_artifact_statuses(derived)
    derived["switch_gate"] = derive_switch_gate(derived)
    resolved_readout_statuses = {"submitted", "accepted", "waived"}
    actionable_item_statuses = {"resolved", "accepted", "tracking"}
    derived["counts"] = {
        "readouts_submitted": sum(
            1
            for entry in derived.get("readouts", {}).values()
            if str(entry.get("status") or "").lower() in {"submitted", "accepted"}
        ),
        "readouts_resolved": sum(
            1
            for entry in derived.get("readouts", {}).values()
            if str(entry.get("status") or "").lower() in resolved_readout_statuses
        ),
        "rounds_total": len(derived.get("cross_review_rounds", [])),
        "open_items": sum(
            1
            for item in derived.get("unresolved_items", [])
            if str(item.get("status") or "").lower() not in actionable_item_statuses
        ),
        "proposed_execution_tasks": len(derived.get("proposed_execution_tasks", [])),
    }
    return derived


def save_derived_state(session: dict[str, Any]) -> None:
    derived = build_derived_state(session)
    DERIVED_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    DERIVED_STATE_FILE.write_text(json.dumps(derived, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sync_all(session: dict[str, Any]) -> None:
    normalized = normalize_session(session)
    ensure_artifact_files()
    derive_artifact_statuses(normalized)
    save_session(normalized)
    save_derived_state(normalized)


def command_sync(session: dict[str, Any], _args: list[str]) -> None:
    return None


def command_start(session: dict[str, Any], args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: start <session-id> [summary]")
    session["session_id"] = args[0]
    if len(args) > 1:
        session["summary"] = args[1]
    session["status"] = "active"
    if session.get("consensus_status") == "not_started":
        session["consensus_status"] = "draft"
    session["human_gate_status"] = "not_requested"
    append_event(
        session,
        "planning_session_started",
        args[1] if len(args) > 1 else f"Started planning session {args[0]}",
    )


def command_baton(session: dict[str, Any], args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: baton <owner> [next-reviewer] [message]")
    owner = canonical_agent(args[0])
    if owner not in AGENT_ORDER:
        raise SystemExit(f"Unknown agent: {args[0]}")
    next_reviewer = canonical_agent(args[1]) if len(args) > 1 and args[1] else next_in_sequence(BATON_SEQUENCE, owner, "Claude")
    message = args[2] if len(args) > 2 else f"Baton moved to {owner}"
    session["status"] = "active"
    session["baton_owner"] = owner
    session["next_reviewer"] = next_reviewer if next_reviewer in AGENT_ORDER else "Claude"
    append_event(
        session,
        "baton_transferred",
        message,
        owner=owner,
        reviewer=session["next_reviewer"],
    )


def command_readout(session: dict[str, Any], args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: readout <agent> [status] [message]")
    agent = canonical_agent(args[0])
    if agent not in AGENT_ORDER:
        raise SystemExit(f"Unknown agent: {args[0]}")
    status = args[1] if len(args) > 1 else "submitted"
    message = args[2] if len(args) > 2 else f"{agent} readout marked {status}"
    session["readouts"][agent]["status"] = status
    session["readouts"][agent]["updated_at"] = iso_now()
    append_event(
        session,
        "readout_submitted" if status == "submitted" else "readout_updated",
        message,
        owner=agent,
        status=status,
    )


def command_round(session: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: round <round-number> <status> [message]")
    try:
        round_no = int(args[0])
    except ValueError as exc:
        raise SystemExit("round-number must be an integer") from exc
    status = args[1]
    message = args[2] if len(args) > 2 else f"Round {round_no} updated to {status}"

    existing = next((entry for entry in session["cross_review_rounds"] if entry["round"] == round_no), None)
    if existing is None:
        existing = {
            "round": round_no,
            "status": status,
            "reviewers": REVIEW_SEQUENCE,
            "updated_at": iso_now(),
            "summary": message,
        }
        session["cross_review_rounds"].append(existing)
    else:
        existing["status"] = status
        existing["updated_at"] = iso_now()
        existing["summary"] = message
    session["cross_review_rounds"].sort(key=lambda item: item["round"])
    session["current_round"] = max(session.get("current_round", 0), round_no)
    append_event(
        session,
        "cross_review_round_opened" if status == "open" else "cross_review_round_completed" if status == "completed" else "cross_review_round_updated",
        message,
        round=round_no,
        status=status,
    )


def command_issue(session: dict[str, Any], args: list[str]) -> None:
    if len(args) < 4:
        raise SystemExit("Usage: issue <issue-id> <severity> <status> <summary>")
    issue_id, severity, status, summary = args[0], args[1], args[2], args[3]
    existing = next((item for item in session["unresolved_items"] if item["id"] == issue_id), None)
    payload = {
        "id": issue_id,
        "severity": severity,
        "status": status,
        "summary": summary,
    }
    if existing is None:
        session["unresolved_items"].append(payload)
    else:
        existing.update(payload)
    if status == "human_required":
        session["status"] = "human_required"
    append_event(
        session,
        "consensus_human_required" if status == "human_required" else "planning_issue_updated",
        summary,
        issue_id=issue_id,
        status=status,
    )


def command_consensus(session: dict[str, Any], args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: consensus <status> [message]")
    status = args[0]
    if status not in CONSENSUS_STATUS_LABELS:
        raise SystemExit(f"Unknown consensus status: {status}")
    message = args[1] if len(args) > 1 else f"Consensus status updated to {status}"
    session["consensus_status"] = status
    if status == "human_required":
        session["status"] = "human_required"
    elif status == "accepted":
        session["status"] = "accepted"
        session["human_gate_status"] = "approved"
    elif session.get("status") == "inactive":
        session["status"] = "active"
    append_event(
        session,
        "consensus_packet_drafted" if status in {"draft", "ready_for_human"} else "consensus_status_updated",
        message,
        status=status,
    )


def command_human_gate(session: dict[str, Any], args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: human-gate <status> [message]")
    status = args[0]
    if status not in HUMAN_GATE_STATUS_LABELS:
        raise SystemExit(f"Unknown human gate status: {status}")
    message = args[1] if len(args) > 1 else f"Human gate updated to {status}"
    session["human_gate_status"] = status
    if status == "approved":
        session["status"] = "accepted"
        session["consensus_status"] = "accepted"
    elif status == "pending":
        session["status"] = "active"
        if session.get("consensus_status") == "not_started":
            session["consensus_status"] = "ready_for_human"
    append_event(
        session,
        "consensus_accepted" if status == "approved" else "human_gate_updated",
        message,
        status=status,
    )


def command_propose_task(session: dict[str, Any], args: list[str]) -> None:
    if len(args) < 4:
        raise SystemExit("Usage: propose-task <task-id> <owner> <reviewer> <title>")
    task_id, owner, reviewer, title = args[0], canonical_agent(args[1]), canonical_agent(args[2]), args[3]
    if owner not in AGENT_ORDER:
        raise SystemExit(f"Unknown owner: {args[1]}")
    if reviewer not in AGENT_ORDER:
        raise SystemExit(f"Unknown reviewer: {args[2]}")
    if owner == reviewer:
        raise SystemExit("Reviewer cannot equal owner")
    entry = next((item for item in session["proposed_execution_tasks"] if item["id"] == task_id), None)
    payload = {
        "id": task_id,
        "title": title,
        "owner": owner,
        "reviewer": reviewer,
        "phase": "Planning Materialized",
        "summary_zh": "",
    }
    if entry is None:
        session["proposed_execution_tasks"].append(payload)
    else:
        entry.update(payload)
    append_event(
        session,
        "execution_slice_proposed",
        f"Proposed execution slice {task_id}: {title}",
        task_id=task_id,
        owner=owner,
        reviewer=reviewer,
    )


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "sync"
    args = argv[2:]
    session = load_session()

    commands = {
        "sync": command_sync,
        "start": command_start,
        "baton": command_baton,
        "readout": command_readout,
        "round": command_round,
        "issue": command_issue,
        "consensus": command_consensus,
        "human-gate": command_human_gate,
        "propose-task": command_propose_task,
    }
    if command not in commands:
        raise SystemExit(command_usage().rstrip())
    commands[command](session, args)
    sync_all(session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
