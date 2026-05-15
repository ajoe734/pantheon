#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import fcntl
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
ROOT = THIS_DIR.parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import ai_status

DEFAULT_PHASE = "phase1"
ROUND_GLOB = "review-round-*.md"
DERIVED_STATE_FILE = ROOT / ".orchestrator" / "planning-state.json"
PLANNING_POINTER_FILE = ROOT / ".orchestrator" / "planning-session-pointer.json"
ORCHESTRATOR_STATE_FILE = ROOT / ".orchestrator" / "state.json"
PLANNING_LOCK_FILE = ROOT / ".orchestrator" / "planning-state.lock"

AGENT_ORDER = ["Claude", "Claude2", "Codex", "Codex2", "Gemini", "Copilot"]
BATON_SEQUENCE = ["Codex", "Codex2", "Gemini", "Copilot", "Claude", "Claude2"]
REVIEW_SEQUENCE = ["Codex2", "Gemini", "Copilot", "Claude", "Claude2"]
PHASE2_SESSION_ID = "phase2-2026-04-12-blueprint-gap-convergence"
SESSION_PROFILE_GENERIC = "generic"
SESSION_PROFILE_BACKEND_COMPLETION = "backend-completion"
SESSION_PROFILE_BLUEPRINT_GAP = "blueprint-gap-convergence"
LEGACY_SESSION_PATHS = {
    "phase1-bootstrap": "phase1",
    "phase1-2026-04-11-backend-completion": "phase1",
    PHASE2_SESSION_ID: "phase2",
}
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
DOCUMENT_RECONCILIATION_STATUS_LABELS = {
    "not_started",
    "in_progress",
    "completed",
    "not_needed",
    "human_required",
}
SESSION_PROFILE_ALIASES = {
    "generic": SESSION_PROFILE_GENERIC,
    "default": SESSION_PROFILE_GENERIC,
    "backend-completion": SESSION_PROFILE_BACKEND_COMPLETION,
    "backend_completion": SESSION_PROFILE_BACKEND_COMPLETION,
    "phase1": SESSION_PROFILE_BACKEND_COMPLETION,
    "blueprint-gap-convergence": SESSION_PROFILE_BLUEPRINT_GAP,
    "blueprint_gap_convergence": SESSION_PROFILE_BLUEPRINT_GAP,
    "blueprint-gap": SESSION_PROFILE_BLUEPRINT_GAP,
    "phase2-blueprint-gap-convergence": SESSION_PROFILE_BLUEPRINT_GAP,
}


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@contextmanager
def planning_lock():
    PLANNING_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PLANNING_LOCK_FILE.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def relative_to_root(path: Path) -> str:
    return str(path.relative_to(ROOT))


def phase_from_session_id(session_id: str | None, fallback: str = DEFAULT_PHASE) -> str:
    candidate = str(session_id or "").strip()
    match = re.match(r"^(phase\d+)\b", candidate, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return fallback


def canonical_session_profile(value: Any) -> str:
    candidate = str(value or "").strip().lower().replace("_", "-")
    if not candidate:
        return ""
    return SESSION_PROFILE_ALIASES.get(candidate, "")


def resolve_session_profile(session_id: str | None, phase: str | None, explicit_profile: Any = None) -> str:
    explicit = canonical_session_profile(explicit_profile)
    if explicit:
        return explicit

    candidate = str(session_id or "").strip()
    if candidate == PHASE2_SESSION_ID:
        return SESSION_PROFILE_BLUEPRINT_GAP
    if candidate in {"phase1-bootstrap", "phase1-2026-04-11-backend-completion"}:
        return SESSION_PROFILE_BACKEND_COMPLETION
    if str(phase or "").strip().lower() == "phase1":
        return SESSION_PROFILE_BACKEND_COMPLETION
    return SESSION_PROFILE_GENERIC


def profile_has_backend_checklist(profile: str) -> bool:
    return profile == SESSION_PROFILE_BACKEND_COMPLETION


def profile_has_gap_outputs(profile: str) -> bool:
    return profile == SESSION_PROFILE_BLUEPRINT_GAP


def profile_fixed_baton_owner(profile: str) -> str | None:
    if profile == SESSION_PROFILE_BLUEPRINT_GAP:
        return "Codex"
    return None


def planning_dir_for_phase(phase: str) -> Path:
    return ROOT / "docs" / "02-architecture" / "consensus" / (str(phase or DEFAULT_PHASE).strip() or DEFAULT_PHASE)


def planning_sessions_root() -> Path:
    return ROOT / "docs" / "02-architecture" / "consensus" / "sessions"


def legacy_phase_for_session(session_id: str | None) -> str | None:
    candidate = str(session_id or "").strip()
    return LEGACY_SESSION_PATHS.get(candidate)


def planning_dir_for_session(session_id: str | None, phase: str | None = None) -> Path:
    resolved_phase = str(phase or "").strip() or phase_from_session_id(session_id)
    legacy_phase = legacy_phase_for_session(session_id)
    if legacy_phase:
        return planning_dir_for_phase(legacy_phase)
    session_slug = str(session_id or "").strip() or "bootstrap-session"
    return planning_sessions_root() / session_slug


def configure_session_paths(planning_dir: Path) -> None:
    global PLANNING_DIR
    global SESSION_FILE
    global README_FILE
    global READOUT_TEMPLATE_FILE
    global CHECKLIST_FILE
    global STARTER_DRAFT_FILE
    global BATON_LOG_FILE
    global SUPERVISOR_QUEUE_FILE
    global CONSENSUS_PACKET_FILE
    global ROUND_WILDCARD_PATH

    PLANNING_DIR = planning_dir
    SESSION_FILE = PLANNING_DIR / "planning-session.json"
    README_FILE = PLANNING_DIR / "README.md"
    READOUT_TEMPLATE_FILE = PLANNING_DIR / "LLM_READOUT_TEMPLATE.md"
    CHECKLIST_FILE = PLANNING_DIR / "pantheon-backend-completion-checklist.md"
    STARTER_DRAFT_FILE = PLANNING_DIR / "starter-draft.md"
    BATON_LOG_FILE = PLANNING_DIR / "baton-log.md"
    SUPERVISOR_QUEUE_FILE = PLANNING_DIR / "supervisor-queue.md"
    CONSENSUS_PACKET_FILE = PLANNING_DIR / "consensus-packet.md"
    ROUND_WILDCARD_PATH = f"{relative_to_root(PLANNING_DIR)}/{ROUND_GLOB}"


configure_session_paths(planning_dir_for_phase(DEFAULT_PHASE))


def list_session_files() -> list[Path]:
    session_files: list[Path] = []
    seen: set[Path] = set()
    for phase in sorted(set(LEGACY_SESSION_PATHS.values())):
        candidate = planning_dir_for_phase(phase) / "planning-session.json"
        if candidate.exists() and candidate not in seen:
            seen.add(candidate)
            session_files.append(candidate)
    sessions_dir = planning_sessions_root()
    if sessions_dir.exists():
        for candidate in sorted(sessions_dir.glob("*/planning-session.json")):
            if candidate not in seen:
                seen.add(candidate)
                session_files.append(candidate)
    return session_files


def discover_recent_sessions(*, active_session_id: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    active_id = str(active_session_id or "").strip()
    for session_file in list_session_files():
        try:
            payload = json.loads(session_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        session_id = str(payload.get("session_id") or session_file.parent.name).strip()
        phase = str(payload.get("phase") or "").strip() or phase_from_session_id(session_id, session_file.parent.name)
        planning_dir = str(payload.get("planning_dir") or relative_to_root(session_file.parent)).strip()
        sessions.append(
            {
                "session_id": session_id,
                "phase": phase,
                "status": str(payload.get("status") or "inactive"),
                "consensus_status": str(payload.get("consensus_status") or "not_started"),
                "human_gate_status": str(payload.get("human_gate_status") or "not_requested"),
                "planning_dir": planning_dir,
                "session_file": relative_to_root(session_file),
                "updated_at": str(payload.get("updated_at") or path_updated_at(session_file) or ""),
                "archived": session_id != active_id,
            }
        )
    sessions.sort(
        key=lambda item: (
            0 if str(item.get("session_id") or "") == active_id else 1,
            str(item.get("updated_at") or ""),
        ),
        reverse=False,
    )
    sessions.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    if active_id:
        sessions.sort(key=lambda item: 0 if str(item.get("session_id") or "") == active_id else 1)
    return sessions[:limit]


def load_active_session_pointer() -> dict[str, Any]:
    default_planning_dir = planning_dir_for_phase(DEFAULT_PHASE)
    default_payload = {
        "session_id": "phase1-2026-04-11-backend-completion",
        "phase": DEFAULT_PHASE,
        "planning_dir": relative_to_root(default_planning_dir),
        "session_file": relative_to_root(default_planning_dir / "planning-session.json"),
    }
    if not PLANNING_POINTER_FILE.exists():
        return default_payload
    try:
        payload = json.loads(PLANNING_POINTER_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default_payload
    if not isinstance(payload, dict):
        return default_payload
    planning_dir = str(payload.get("planning_dir") or "").strip()
    if not planning_dir:
        session_id = str(payload.get("session_id") or "").strip()
        phase = str(payload.get("phase") or "").strip() or phase_from_session_id(session_id)
        planning_dir = relative_to_root(planning_dir_for_session(session_id, phase))
    payload["planning_dir"] = planning_dir
    payload["session_file"] = str(payload.get("session_file") or f"{planning_dir}/planning-session.json")
    payload["phase"] = str(payload.get("phase") or "").strip() or phase_from_session_id(payload.get("session_id"))
    return payload


def save_active_session_pointer(session: dict[str, Any]) -> None:
    payload = {
        "session_id": session.get("session_id"),
        "phase": session.get("phase"),
        "planning_dir": relative_to_root(PLANNING_DIR),
        "session_file": relative_to_root(SESSION_FILE),
        "updated_at": iso_now(),
    }
    write_json_atomic(PLANNING_POINTER_FILE, payload)


def unique_strings(items: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def blueprint_gap_brief_files() -> list[str]:
    layers = ai_status.default_canonical_document_layers()
    l1 = list(layers.get("L1 Platform Architecture & Policy", []))
    l2 = [
        item
        for item in layers.get("L2 Planning & Execution", [])
        if "/consensus/phase1/" not in item
    ]
    return unique_strings(
        [
            "Pantheon_Blueprint_Gap_Review_v1.md",
            "Pantheon_Market_Data_Scope_and_Source_Plan_v1.md",
            "ai-status.json",
            *l1,
            *l2,
        ]
    )


def planning_output_path(session: dict[str, Any], artifact_id: str) -> str:
    artifacts = session.get("artifacts") if isinstance(session.get("artifacts"), dict) else {}
    artifact = artifacts.get(artifact_id) if isinstance(artifacts.get(artifact_id), dict) else {}
    path = str(artifact.get("path") or "").strip()
    if path:
        return path
    for output in session.get("expected_outputs", []):
        if not isinstance(output, dict):
            continue
        if str(output.get("id") or "").strip() != artifact_id:
            continue
        output_path = str(output.get("path") or "").strip()
        if output_path:
            return output_path
    return ""


def planning_task_source_ref(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "discussion_planning",
        "session_id": str(session.get("session_id") or "").strip(),
        "phase": str(session.get("phase") or "").strip(),
        "profile": str(session.get("profile") or "").strip(),
        "planning_dir": str(session.get("planning_dir") or "").strip(),
        "session_file": str(session.get("session_file") or "").strip(),
        "consensus_packet": planning_output_path(session, "consensus_packet"),
        "execution_materialization": planning_output_path(session, "execution_materialization"),
    }


def materialization_contract(session: dict[str, Any]) -> dict[str, Any]:
    proposed = session.get("proposed_execution_tasks") or []
    initial_task_ids = [
        str(item).strip()
        for item in session.get("initial_materialization_task_ids", [])
        if str(item).strip()
    ]
    return {
        **planning_task_source_ref(session),
        "source_plane": "planning",
        "planning_mode": str(session.get("planning_mode") or "discussion_planning"),
        "document_reconciliation_status": str(session.get("document_reconciliation_status") or "not_started"),
        "runtime_mode": str(session.get("runtime_mode") or "supervisor_managed_execution"),
        "consensus_status": str(session.get("consensus_status") or "not_started"),
        "human_gate_status": str(session.get("human_gate_status") or "not_requested"),
        "proposed_execution_tasks": len(proposed),
        "initial_materialization_task_ids": initial_task_ids,
        "initial_materialization_task_count": len(initial_task_ids),
    }


def document_reconciliation_complete(session: dict[str, Any]) -> bool:
    return str(session.get("document_reconciliation_status") or "not_started") in {
        "completed",
        "not_needed",
    }


def default_brief_files(profile: str) -> list[str]:
    if profile == SESSION_PROFILE_BLUEPRINT_GAP:
        return blueprint_gap_brief_files()
    if profile == SESSION_PROFILE_BACKEND_COMPLETION:
        return [relative_to_root(CHECKLIST_FILE)]
    return []


def default_expected_outputs(profile: str) -> list[dict[str, Any]]:
    base = [
        {
            "id": "document_reconciliation",
            "path": f"{relative_to_root(PLANNING_DIR)}/document-reconciliation.md",
            "owner": "Codex",
            "status": "not_started",
        },
        {
            "id": "consensus_packet",
            "path": relative_to_root(CONSENSUS_PACKET_FILE),
            "owner": "Claude",
            "status": "not_started",
        }
    ]
    if profile == SESSION_PROFILE_BLUEPRINT_GAP:
        return [
            {
                "id": "gap_response_matrix",
                "path": f"{relative_to_root(PLANNING_DIR)}/gap-response-matrix.md",
                "owner": "Claude",
                "status": "not_started",
            },
            {
                "id": "execution_materialization",
                "path": f"{relative_to_root(PLANNING_DIR)}/execution-materialization.md",
                "owner": "Codex",
                "status": "not_started",
            },
            *base,
        ]
    return base


def default_lane_focus(profile: str) -> dict[str, str]:
    generic = {
        "Claude": "Facilitate consensus, synthesize cited disagreements, and prepare the human gate packet.",
        "Codex": "Ground the plan in repo evidence and turn converged decisions into execution slices.",
        "Codex2": "Audit schemas, object boundaries, and contract formalization gaps.",
        "Gemini": "Stress-test runtime, replay, and tooling feasibility.",
        "Copilot": "Pressure-test research readiness, external source assumptions, and acceptance wording.",
    }
    if profile == SESSION_PROFILE_BLUEPRINT_GAP:
        return {
            "Claude": "Facilitate the blueprint-gap session, integrate readouts and unresolved items, and draft the final consensus packet after every lane is resolved or waived.",
            "Codex": "Verify each gap claim against repo evidence, own the shared starter draft, and draft execution materialization for the next delivery wave.",
            "Codex2": "Audit schema and object formalization for GAP-01, GAP-03, and GAP-06, especially canonical object boundaries and acceptance surface coverage.",
            "Gemini": "Evaluate runtime, replay, and tooling feasibility for GAP-02 and GAP-05; report blockers with cited implementation constraints.",
            "Copilot": "Critique market-source scope, research backend maturity, and product-facing acceptance language for GAP-00, GAP-02, and GAP-07.",
        }
    return generic


def default_fallback_policy(profile: str) -> dict[str, Any]:
    if profile == SESSION_PROFILE_BLUEPRINT_GAP:
        return {
            "Gemini": {
                "waive_after_seconds": 1800,
                "waive_on_terminal_failure": True,
                "issue_id": "DISC-GEMINI-PLANNING",
                "summary": "Gemini planning lane failed; waive the lane, track the issue, and rely on Copilot critique plus Codex task slicing to keep the session moving.",
                "covering_agents": ["Copilot", "Codex"],
            }
        }
    return {}


def phase2_proposed_execution_tasks() -> list[dict[str, Any]]:
    return [
        {
            "id": "PLAN-002",
            "title": "Generalize discussion planning for reusable sessions",
            "owner": "Codex",
            "reviewer": "Claude",
            "phase": "Planning Bootstrap",
            "summary_zh": "把 discussion planning runtime 改成可重複使用的 session-driven 模式。",
            "depends_on": [],
        },
        {
            "id": "BG-000",
            "title": "Canonicalize market scope, instrument policy, and source-class matrix",
            "owner": "Codex",
            "reviewer": "Copilot",
            "phase": "Blueprint Gap P0",
            "summary_zh": "把市場範圍、標的政策與 source-class matrix 提升成可執行的 canonical 規格。",
            "depends_on": ["PLAN-002"],
        },
        {
            "id": "BG-001",
            "title": "Formalize security master, contract master, market calendar, and dataset lineage objects",
            "owner": "Codex2",
            "reviewer": "Codex",
            "phase": "Blueprint Gap P0",
            "summary_zh": "正式定義 SecurityMaster、ContractMaster、MarketCalendarSession 與各級 dataset 物件。",
            "depends_on": ["PLAN-002"],
        },
        {
            "id": "BG-002",
            "title": "Publish research backend maturity matrix and production-path mapping",
            "owner": "Copilot",
            "reviewer": "Gemini",
            "phase": "Blueprint Gap P1",
            "summary_zh": "整理 research backend maturity matrix 與 production path 對照。",
            "depends_on": ["PLAN-002"],
        },
        {
            "id": "BG-003",
            "title": "Formalize decision-front objects and adjudication boundaries",
            "owner": "Codex2",
            "reviewer": "Claude",
            "phase": "Blueprint Gap P0",
            "summary_zh": "正式定義 RegimeState、UniverseSelection、SignalInference、AllocationDecision、RiskAdjudication。",
            "depends_on": ["PLAN-002"],
        },
        {
            "id": "BG-004",
            "title": "Publish memory layer design note for persona, institutional memory, and write-back",
            "owner": "Claude",
            "reviewer": "Codex",
            "phase": "Blueprint Gap P1",
            "summary_zh": "補齊 persona memory、institutional memory、retrieval 與 write-back 的設計說明。",
            "depends_on": ["PLAN-002"],
        },
        {
            "id": "BG-005",
            "title": "Define golden replay scenario and acceptance runbook",
            "owner": "Codex",
            "reviewer": "Codex2",
            "phase": "Blueprint Gap P2",
            "summary_zh": "定義 golden replay scenario 與 acceptance runbook，銜接資料面與決策面前段。",
            "depends_on": ["BG-000", "BG-001", "BG-003"],
        },
        {
            "id": "BG-006",
            "title": "Publish operator acceptance matrix across BFF, internal API, CLI, and fallback paths",
            "owner": "Codex2",
            "reviewer": "Claude",
            "phase": "Blueprint Gap P1",
            "summary_zh": "整理 BFF、internal API、CLI、fallback、support-only path 的 operator acceptance matrix。",
            "depends_on": ["PLAN-002"],
        },
        {
            "id": "BG-007",
            "title": "Publish product-facing glossary and stage-status language pack",
            "owner": "Copilot",
            "reviewer": "Codex",
            "phase": "Blueprint Gap P1",
            "summary_zh": "整理 glossary、action→object map 與 stage/status wording 的對外語言包。",
            "depends_on": ["PLAN-002"],
        },
    ]


def default_proposed_execution_tasks(profile: str) -> list[dict[str, Any]]:
    if profile == SESSION_PROFILE_BLUEPRINT_GAP:
        return phase2_proposed_execution_tasks()
    return []


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
  python3 scripts/planning_state.py reconcile-docs <status> [message]
  python3 scripts/planning_state.py consensus <status> [message]
  python3 scripts/planning_state.py human-gate <status> [message]
  python3 scripts/planning_state.py propose-task <task-id> <owner> <reviewer> <title>
  python3 scripts/planning_state.py materialize
"""


def default_artifacts(profile: str = SESSION_PROFILE_BACKEND_COMPLETION) -> dict[str, dict[str, Any]]:
    artifacts = {
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
        "document_reconciliation": {
            "path": f"{relative_to_root(PLANNING_DIR)}/document-reconciliation.md",
            "status": "not_started",
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
    if profile_has_backend_checklist(profile):
        artifacts["backend_completion_checklist"] = {
            "path": relative_to_root(CHECKLIST_FILE),
            "status": "ready",
        }
    if profile_has_gap_outputs(profile):
        artifacts["gap_response_matrix"] = {
            "path": f"{relative_to_root(PLANNING_DIR)}/gap-response-matrix.md",
            "status": "not_started",
        }
        artifacts["execution_materialization"] = {
            "path": f"{relative_to_root(PLANNING_DIR)}/execution-materialization.md",
            "status": "not_started",
        }
    return artifacts


def default_readouts() -> dict[str, dict[str, Any]]:
    return {
        agent: {
            "path": relative_to_root(readout_file_for(agent)),
            "status": "pending",
            "updated_at": None,
        }
        for agent in AGENT_ORDER
    }


def default_session(
    session_id: str = "phase1-bootstrap",
    phase: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    resolved_phase = str(phase or "").strip() or phase_from_session_id(session_id)
    configure_session_paths(planning_dir_for_session(session_id, resolved_phase))
    resolved_profile = resolve_session_profile(session_id, resolved_phase, profile)
    timestamp = iso_now()
    session = {
        "session_id": session_id,
        "phase": resolved_phase,
        "profile": resolved_profile,
        "status": "inactive",
        "planning_mode": "discussion_planning",
        "runtime_mode": "supervisor_managed_execution",
        "summary": "Align architecture, delivery order, and task slicing before materializing execution work.",
        "objective": "Align architecture, delivery order, and task slicing before materializing execution work.",
        "facilitator": "Claude",
        "baton_owner": "Codex",
        "starter_owner": "Codex",
        "next_reviewer": "Codex2",
        "baton_sequence": BATON_SEQUENCE,
        "review_sequence": REVIEW_SEQUENCE,
        "brief_files": default_brief_files(resolved_profile),
        "expected_outputs": default_expected_outputs(resolved_profile),
        "lane_focus": default_lane_focus(resolved_profile),
        "fallback_policy": default_fallback_policy(resolved_profile),
        "current_round": 0,
        "document_reconciliation_status": "not_started",
        "consensus_status": "not_started",
        "human_gate_status": "not_requested",
        "artifacts": default_artifacts(resolved_profile),
        "readouts": default_readouts(),
        "cross_review_rounds": [],
        "unresolved_items": [],
        "proposed_execution_tasks": default_proposed_execution_tasks(resolved_profile),
        "recent_events": [],
        "updated_at": timestamp,
    }
    fixed_baton_owner = profile_fixed_baton_owner(resolved_profile)
    if fixed_baton_owner:
        session["baton_owner"] = fixed_baton_owner
        session["starter_owner"] = fixed_baton_owner
    if resolved_profile == SESSION_PROFILE_BLUEPRINT_GAP:
        session.update(
            {
                "summary": "Run a blueprint-gap convergence session that turns gap review findings into cited consensus outputs and the next execution wave plan.",
                "objective": (
                    "Use the blueprint gap review, market data scope/source plan, current execution state, and canonical L1/L2 truth to converge on the next "
                    "Pantheon delivery wave. Required outputs are gap-response-matrix.md, execution-materialization.md, and consensus-packet.md."
                ),
                "baton_owner": "Codex",
                "starter_owner": "Codex",
                "next_reviewer": "Codex2",
            }
        )
    return session


def planning_readme_template(session: dict[str, Any]) -> str:
    brief_files = session.get("brief_files", [])
    expected_outputs = session.get("expected_outputs", [])
    shared_draft_owner = session.get("starter_owner", "Codex")
    brief_lines = "\n".join(f"- `{path}`" for path in brief_files) or "- _(none)_"
    output_lines = "\n".join(
        f"- `{entry.get('path')}` (owner: `{entry.get('owner', '-')}`)"
        for entry in expected_outputs
        if str(entry.get("path") or "").strip()
    ) or "- _(none)_"
    return f"""# Discussion Planning Mode

This directory is the canonical workspace for `discussion_planning`.

## Session

- Session ID: `{session.get("session_id", "phase1-bootstrap")}`
- Objective: {session.get("objective", "").strip() or "Align architecture, delivery order, and task slicing before materializing execution work."}
- Shared draft owner: `{shared_draft_owner}`

## Brief Files

{brief_lines}

## Expected Outputs

{output_lines}

## Planning Stages

1. audit the canonical blueprint and planning documents relevant to the session
2. write down insufficiencies and either patch the canonical docs or explicitly conclude that no canonical update is needed
3. only after document reconciliation is complete may the session finalize execution planning for human approval and materialization

## Baton Loop

1. every lane reads the session brief and writes an independent readout using `LLM_READOUT_TEMPLATE.md`
2. only `{shared_draft_owner}` seeds `starter-draft.md`
3. cited cross-review happens round by round
4. unresolved disagreements become explicit `human_required` or `tracking` items
5. the facilitator drafts `consensus-packet.md`
6. after human acceptance, convert `proposed_execution_tasks` into execution tasks through `scripts/planning-state.sh materialize`
7. execution tasks should receive planning references, not copied planning narrative

## Rules

- only the shared draft owner edits `starter-draft.md`
- reviewers do not directly rewrite the shared draft
- `planning-session.json` is the machine-readable source of truth for planning state
- `.orchestrator/planning-state.json` is the derived dashboard state
- every planning round keeps its own session directory; archived sessions are immutable history
- document reconciliation must be completed before final human approval or execution materialization
- execution tasks stay in `ai-status.json`; do not mix planning drafts into the execution board too early
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


def gap_response_matrix_template() -> str:
    return """# Gap Response Matrix

Use this file to answer GAP-00 through GAP-07 with:

- current repo evidence
- what is already done
- what remains a real gap
- the canonicalization or delivery action required next
- cited references to source docs or code

## GAP-00

- Gap statement:
- Repo evidence:
- Decision:
- Next action:
- Citations:

## GAP-01

- Gap statement:
- Repo evidence:
- Decision:
- Next action:
- Citations:

## GAP-02

- Gap statement:
- Repo evidence:
- Decision:
- Next action:
- Citations:

## GAP-03

- Gap statement:
- Repo evidence:
- Decision:
- Next action:
- Citations:

## GAP-04

- Gap statement:
- Repo evidence:
- Decision:
- Next action:
- Citations:

## GAP-05

- Gap statement:
- Repo evidence:
- Decision:
- Next action:
- Citations:

## GAP-06

- Gap statement:
- Repo evidence:
- Decision:
- Next action:
- Citations:

## GAP-07

- Gap statement:
- Repo evidence:
- Decision:
- Next action:
- Citations:
"""


def execution_materialization_template() -> str:
    return """# Execution Materialization

This file is the bridge contract from planning into execution.
When these rows are materialized into `ai-status.json`, each execution task should keep `source_plane = planning`
and structured `source_ref` metadata back to `planning-session.json`, `consensus-packet.md`, and this file.

## P0

| Task ID | Owner | Reviewer | Depends On | Wave | Notes |
|---|---|---|---|---|---|
| _(pending)_ | - | - | - | - | - |

## P1

| Task ID | Owner | Reviewer | Depends On | Wave | Notes |
|---|---|---|---|---|---|
| _(pending)_ | - | - | - | - | - |

## P2

| Task ID | Owner | Reviewer | Depends On | Wave | Notes |
|---|---|---|---|---|---|
| _(pending)_ | - | - | - | - | - |
"""


def document_reconciliation_template() -> str:
    return """# Document Reconciliation

Use this file to prove that planning reviewed the canonical blueprint and planning docs before cutting execution work.

## Canonical Inputs Reviewed

- Canonical planning docs:
- Canonical architecture or policy docs:

## Insufficiencies Found

- Gap 1:
- Gap 2:

## Canonical Updates Required

- Document:
  - Required change:
  - Status:

## Outcome

- `completed` when canonical docs were updated and the planning gap is closed
- `not_needed` when the session explicitly concluded that no canonical doc change is required
- do not move execution planning to final approval until one of those two outcomes is true
"""


def starter_draft_template(session: dict[str, Any]) -> str:
    return f"""# Starter Draft

Current rule: only `{session.get("starter_owner", "Codex")}` edits this file directly.

## Shared Draft

- Objective:
- Scope boundary:
- Proposed wave order:
- Proposed task slices:
- Open disagreements:
"""


def baton_log_template(session: dict[str, Any]) -> str:
    return f"""# Baton Log

| Timestamp | From | To | Message |
|---|---|---|---|
| _(pending)_ | - | {session.get("baton_owner", "Codex")} | Bootstrap baton owner |
"""


def supervisor_queue_template(session: dict[str, Any]) -> str:
    return f"""# Supervisor Queue

| Order | Item | Owner | Status | Notes |
|---|---|---|---|---|
| 1 | Reconcile canonical docs and planning gaps | Codex | pending | Complete `document-reconciliation.md` first |
| 2 | Collect lane readouts | All lanes | pending | Use `LLM_READOUT_TEMPLATE.md` |
| 3 | Create starter draft | {session.get("starter_owner", "Codex")} | pending | First editable shared draft |
| 4 | Run cited cross-review | {' -> '.join(session.get('review_sequence', REVIEW_SEQUENCE))} | pending | One round at a time |
| 5 | Draft consensus packet | {session.get("facilitator", "Claude")} | pending | Escalate unresolved semantic conflicts |
| 6 | Wait for human acceptance | Human | pending | Required before execution task materialization |
"""


def consensus_packet_template(session: dict[str, Any]) -> str:
    return f"""# Consensus Packet

## Decision Summary

- Session: `{session.get("session_id", "phase1-bootstrap")}`
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


def review_round_template(round_no: int, reviewers: list[str] | None = None) -> str:
    reviewer_list = reviewers or REVIEW_SEQUENCE
    reviewer_lines = "\n".join(f"- {reviewer}" for reviewer in reviewer_list)
    return f"""# Review Round {round_no:02d}

Use cited comments only. Do not directly rewrite `starter-draft.md` unless you currently hold the baton.

## Reviewer Order

{reviewer_lines}

## Comments

- _(pending)_
"""


def ensure_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def ensure_artifact_files(session: dict[str, Any] | None = None) -> None:
    session = session or default_session()
    ensure_text_file(README_FILE, planning_readme_template(session))
    ensure_text_file(READOUT_TEMPLATE_FILE, readout_template())
    if profile_has_backend_checklist(str(session.get("profile") or "")):
        ensure_text_file(CHECKLIST_FILE, backend_completion_checklist_template())
    ensure_text_file(STARTER_DRAFT_FILE, starter_draft_template(session))
    ensure_text_file(BATON_LOG_FILE, baton_log_template(session))
    ensure_text_file(SUPERVISOR_QUEUE_FILE, supervisor_queue_template(session))
    ensure_text_file(CONSENSUS_PACKET_FILE, consensus_packet_template(session))
    for output in session.get("expected_outputs", []):
        output_path = str(output.get("path") or "").strip()
        if not output_path:
            continue
        output_file = ROOT / output_path
        if output_file == CONSENSUS_PACKET_FILE:
            continue
        if output_file.name == "document-reconciliation.md":
            ensure_text_file(output_file, document_reconciliation_template())
        elif output_file.name == "gap-response-matrix.md":
            ensure_text_file(output_file, gap_response_matrix_template())
        elif output_file.name == "execution-materialization.md":
            ensure_text_file(output_file, execution_materialization_template())
        else:
            ensure_text_file(output_file, f"# {output_file.stem.replace('-', ' ').title()}\n")
    for agent in AGENT_ORDER:
        ensure_text_file(readout_file_for(agent), readout_file_template(agent))
    ensure_text_file(PLANNING_DIR / "review-round-01.md", review_round_template(1, session.get("review_sequence")))


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


def path_from_relative(path_value: str) -> Path:
    return ROOT / str(path_value).strip()


def load_orchestrator_runtime_state() -> dict[str, Any]:
    if not ORCHESTRATOR_STATE_FILE.exists():
        return {}
    try:
        payload = json.loads(ORCHESTRATOR_STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def execution_mode_has_inflight_work(runtime_state: dict[str, Any]) -> bool:
    supervisor_state = runtime_state.get("supervisor", {}) if isinstance(runtime_state.get("supervisor"), dict) else {}
    occupancy = supervisor_state.get("mode_occupancy", {}) if isinstance(supervisor_state.get("mode_occupancy"), dict) else {}
    execution = occupancy.get("execution", {}) if isinstance(occupancy.get("execution"), dict) else {}
    if any(int(execution.get(key) or 0) > 0 for key in ("running", "pending", "queued")):
        return True
    focus_mode = str(supervisor_state.get("focus_mode") or "").strip()
    mode_status = str(supervisor_state.get("mode_status") or "").strip()
    return focus_mode == "execution" and mode_status == "draining"


def load_session() -> dict[str, Any]:
    pointer = load_active_session_pointer()
    configure_session_paths(path_from_relative(pointer["planning_dir"]))
    if not SESSION_FILE.exists():
        session = default_session(
            str(pointer.get("session_id") or "phase1-bootstrap"),
            str(pointer.get("phase") or DEFAULT_PHASE),
        )
        ensure_artifact_files(session)
        save_session(session)
        return session
    raw = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("planning-session.json must contain a JSON object")
    session = normalize_session(raw)
    ensure_artifact_files(session)
    return session


def normalize_session(raw: dict[str, Any]) -> dict[str, Any]:
    session_id = str(raw.get("session_id") or "phase1-bootstrap").strip() or "phase1-bootstrap"
    phase = str(raw.get("phase") or "").strip() or phase_from_session_id(session_id, PLANNING_DIR.name)
    configure_session_paths(planning_dir_for_session(session_id, phase))
    profile = resolve_session_profile(session_id, phase, raw.get("profile"))
    session = deep_merge_dict(default_session(session_id, phase, profile), raw)
    session["session_id"] = session_id
    session["phase"] = phase
    session["profile"] = profile
    session["planning_dir"] = relative_to_root(PLANNING_DIR)
    session["session_file"] = relative_to_root(SESSION_FILE)
    session["status"] = str(session.get("status") or "inactive")
    if session["status"] not in PLANNING_STATUS_LABELS:
        session["status"] = "inactive"

    session["planning_mode"] = "discussion_planning"
    session["runtime_mode"] = "supervisor_managed_execution"
    session["summary"] = str(session.get("summary") or "").strip()
    session["objective"] = str(session.get("objective") or "").strip() or session["summary"]
    session["facilitator"] = canonical_agent(session.get("facilitator"), "Claude")
    session["baton_owner"] = canonical_agent(session.get("baton_owner"), "Codex")
    session["starter_owner"] = canonical_agent(session.get("starter_owner"), "Codex")
    session["next_reviewer"] = canonical_agent(session.get("next_reviewer"), "Codex2")
    fixed_baton_owner = profile_fixed_baton_owner(profile)
    if fixed_baton_owner:
        session["starter_owner"] = fixed_baton_owner
        session["baton_owner"] = fixed_baton_owner

    baton_sequence = [canonical_agent(item) for item in session.get("baton_sequence", BATON_SEQUENCE)]
    session["baton_sequence"] = [item for item in baton_sequence if item in AGENT_ORDER] or BATON_SEQUENCE
    review_sequence = [canonical_agent(item) for item in session.get("review_sequence", REVIEW_SEQUENCE)]
    session["review_sequence"] = [item for item in review_sequence if item in AGENT_ORDER] or REVIEW_SEQUENCE

    session["brief_files"] = unique_strings(
        [str(item).strip() for item in session.get("brief_files", default_brief_files(profile)) if str(item).strip()]
    )

    expected_outputs: list[dict[str, Any]] = []
    for entry in session.get("expected_outputs", default_expected_outputs(profile)):
        if isinstance(entry, str):
            candidate = {"path": entry}
        elif isinstance(entry, dict):
            candidate = dict(entry)
        else:
            continue
        path_value = str(candidate.get("path") or "").strip()
        if not path_value:
            continue
        output_id = str(candidate.get("id") or Path(path_value).stem.replace("-", "_")).strip()
        expected_outputs.append(
            {
                "id": output_id,
                "path": path_value,
                "owner": canonical_agent(candidate.get("owner"), "Claude"),
                "status": str(candidate.get("status") or "not_started"),
            }
        )
    session["expected_outputs"] = expected_outputs

    lane_focus_raw = session.get("lane_focus") if isinstance(session.get("lane_focus"), dict) else {}
    lane_focus_defaults = default_lane_focus(profile)
    session["lane_focus"] = {
        agent: str(mapping_entry(lane_focus_raw, agent) or lane_focus_defaults.get(agent) or "").strip()
        for agent in AGENT_ORDER
    }

    fallback_policy_raw = session.get("fallback_policy") if isinstance(session.get("fallback_policy"), dict) else {}
    fallback_defaults = default_fallback_policy(profile)
    normalized_fallbacks: dict[str, dict[str, Any]] = {}
    for agent_name in AGENT_ORDER:
        incoming = mapping_entry(fallback_policy_raw, agent_name)
        base = deepcopy(mapping_entry(fallback_defaults, agent_name) or {})
        if incoming is None and not base:
            continue
        payload = deep_merge_dict(base, incoming if isinstance(incoming, dict) else {})
        normalized_fallbacks[agent_name] = {
            "waive_after_seconds": int(payload.get("waive_after_seconds") or 0),
            "waive_on_terminal_failure": bool(payload.get("waive_on_terminal_failure", False)),
            "issue_id": str(payload.get("issue_id") or "").strip(),
            "summary": str(payload.get("summary") or "").strip(),
            "covering_agents": [canonical_agent(item) for item in payload.get("covering_agents", []) if canonical_agent(item)],
        }
    session["fallback_policy"] = normalized_fallbacks

    session["consensus_status"] = str(session.get("consensus_status") or "not_started")
    if session["consensus_status"] not in CONSENSUS_STATUS_LABELS:
        session["consensus_status"] = "not_started"
    session["document_reconciliation_status"] = str(session.get("document_reconciliation_status") or "").strip()
    if session["document_reconciliation_status"] not in DOCUMENT_RECONCILIATION_STATUS_LABELS:
        if (
            session.get("consensus_status") == "accepted"
            or session.get("human_gate_status") == "approved"
            or session.get("materialized_at")
        ):
            session["document_reconciliation_status"] = "not_needed"
        else:
            session["document_reconciliation_status"] = "not_started"
    session["human_gate_status"] = str(session.get("human_gate_status") or "not_requested")
    if session["human_gate_status"] not in HUMAN_GATE_STATUS_LABELS:
        session["human_gate_status"] = "not_requested"

    artifacts = default_artifacts(profile)
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

    source_ref_base = planning_task_source_ref(session)
    proposed_execution_tasks: list[dict[str, Any]] = []
    for entry in session.get("proposed_execution_tasks", default_proposed_execution_tasks(profile)):
        if not isinstance(entry, dict):
            continue
        task_id = str(entry.get("id") or "").strip()
        if not task_id:
            continue
        existing_source_ref = entry.get("source_ref") if isinstance(entry.get("source_ref"), dict) else {}
        normalized_source_ref = {
            **source_ref_base,
            **{
                str(key): str(value).strip()
                for key, value in existing_source_ref.items()
                if value is not None and str(value).strip()
            },
        }
        proposed_execution_tasks.append(
            {
                "id": task_id,
                "title": str(entry.get("title") or "").strip(),
                "owner": canonical_agent(entry.get("owner"), "Codex"),
                "reviewer": canonical_agent(entry.get("reviewer"), "Claude"),
                "phase": str(entry.get("phase") or "Planning Materialized"),
                "summary_zh": str(entry.get("summary_zh") or "").strip(),
                "depends_on": [str(item).strip() for item in entry.get("depends_on", []) if str(item).strip()],
                "artifacts": [str(item).strip() for item in entry.get("artifacts", []) if str(item).strip()],
                "acceptance": [str(item).strip() for item in entry.get("acceptance", []) if str(item).strip()],
                "source_plane": str(entry.get("source_plane") or "planning").strip() or "planning",
                "source_ref": normalized_source_ref,
            }
        )
    session["proposed_execution_tasks"] = proposed_execution_tasks
    session["initial_materialization_task_ids"] = [
        str(item).strip()
        for item in session.get("initial_materialization_task_ids", [])
        if str(item).strip()
    ]
    session["materialization_contract"] = materialization_contract(session)

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
    configure_session_paths(planning_dir_for_session(session.get("session_id"), session.get("phase")))
    ensure_artifact_files(session)
    session["updated_at"] = iso_now()
    write_json_atomic(SESSION_FILE, session)
    save_active_session_pointer(session)


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
    reconciliation_complete = document_reconciliation_complete(session)
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
        reconciliation_complete
        and
        all_readouts_submitted
        and cross_review_round_present
        and divergence_resolved_or_escalated
        and consensus_packet_drafted
    )
    return {
        "document_reconciliation_complete": reconciliation_complete,
        "all_readouts_submitted": all_readouts_submitted,
        "cross_review_round_present": cross_review_round_present,
        "divergence_resolved_or_escalated": divergence_resolved_or_escalated,
        "consensus_packet_drafted": consensus_packet_drafted,
        "human_approved": human_approved,
        "ready_for_human": prereqs_satisfied or (human_approved and reconciliation_complete),
        "ready_to_materialize": human_approved and reconciliation_complete,
    }


def artifact_template_for(session: dict[str, Any], key: str, path: Path) -> str | None:
    if key == "planning_readme":
        return planning_readme_template(session)
    if key == "readout_template":
        return readout_template()
    if key == "backend_completion_checklist":
        return backend_completion_checklist_template()
    if key == "starter_draft":
        return starter_draft_template(session)
    if key == "baton_log":
        return baton_log_template(session)
    if key == "supervisor_queue":
        return supervisor_queue_template(session)
    if key == "document_reconciliation":
        return document_reconciliation_template()
    if key == "consensus_packet":
        return consensus_packet_template(session)
    if key == "gap_response_matrix":
        return gap_response_matrix_template()
    if key == "execution_materialization":
        return execution_materialization_template()
    if path.name == "document-reconciliation.md":
        return document_reconciliation_template()
    if path.name == "gap-response-matrix.md":
        return gap_response_matrix_template()
    if path.name == "execution-materialization.md":
        return execution_materialization_template()
    return None


def auto_detect_artifact_activity(session: dict[str, Any]) -> None:
    activity_detected = False

    for key, artifact in session.get("artifacts", {}).items():
        path_value = str((artifact or {}).get("path") or "").strip()
        if not path_value or "*" in path_value:
            continue
        path = path_from_relative(path_value)
        template = artifact_template_for(session, key, path)
        if template is None or not file_differs_from_template(path, template):
            continue
        activity_detected = True
        if key == "starter_draft":
            session["artifacts"][key]["status"] = "active"
            if session.get("consensus_status") == "not_started":
                session["consensus_status"] = "draft"
            continue
        if key == "document_reconciliation":
            current_status = str(session["artifacts"][key].get("status") or "not_started")
            if current_status in {"ready", "draft", "not_started"}:
                session["artifacts"][key]["status"] = "active"
            if str(session.get("document_reconciliation_status") or "not_started") == "not_started":
                session["document_reconciliation_status"] = "in_progress"
            continue
        if key == "consensus_packet":
            session["artifacts"][key]["status"] = "draft"
            if session.get("consensus_status") == "not_started":
                session["consensus_status"] = "draft"
            continue
        current_status = str(session["artifacts"][key].get("status") or "ready")
        if current_status in {"ready", "draft", "not_started"}:
            session["artifacts"][key]["status"] = "active"

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
        if file_differs_from_template(round_path, review_round_template(round_no, session.get("review_sequence"))):
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
    reconciliation_status = str(session.get("document_reconciliation_status") or "not_started")
    if reconciliation_status in DOCUMENT_RECONCILIATION_STATUS_LABELS and reconciliation_status != "not_started":
        session["artifacts"]["document_reconciliation"]["status"] = reconciliation_status
    elif session["artifacts"]["document_reconciliation"]["status"] == "not_started":
        session["artifacts"]["document_reconciliation"]["status"] = reconciliation_status
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
    derived["materialization_contract"] = materialization_contract(derived)
    derived["active_session"] = {
        "session_id": derived.get("session_id"),
        "phase": derived.get("phase"),
        "planning_dir": derived.get("planning_dir"),
        "session_file": derived.get("session_file"),
        "status": derived.get("status"),
        "document_reconciliation_status": derived.get("document_reconciliation_status"),
        "consensus_status": derived.get("consensus_status"),
        "human_gate_status": derived.get("human_gate_status"),
        "updated_at": derived.get("updated_at"),
        "archived": False,
    }
    derived["recent_sessions"] = discover_recent_sessions(active_session_id=str(derived.get("session_id") or ""))
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
    write_json_atomic(DERIVED_STATE_FILE, derived)


def sync_all(session: dict[str, Any]) -> None:
    normalized = normalize_session(session)
    ensure_artifact_files(normalized)
    derive_artifact_statuses(normalized)
    save_session(normalized)
    save_derived_state(normalized)


def command_sync(session: dict[str, Any], _args: list[str]) -> None:
    return None


def command_start(session: dict[str, Any], args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: start <session-id> [summary]")
    session_id = str(args[0]).strip()
    phase = phase_from_session_id(session_id, str(session.get("phase") or DEFAULT_PHASE))
    profile_override = canonical_session_profile(os.environ.get("PLANNING_PROFILE"))
    if os.environ.get("PLANNING_PROFILE") and not profile_override:
        raise SystemExit(f"Unknown planning profile: {os.environ['PLANNING_PROFILE']}")
    configure_session_paths(planning_dir_for_session(session_id, phase))
    if SESSION_FILE.exists():
        raw = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise SystemExit("planning-session.json must contain a JSON object")
        if profile_override:
            raw["profile"] = profile_override
        target_session = normalize_session(raw)
    else:
        target_session = default_session(session_id, phase, profile_override)
    session.clear()
    session.update(target_session)
    session["session_id"] = session_id
    session["phase"] = phase
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
    fixed_baton_owner = profile_fixed_baton_owner(str(session.get("profile") or ""))
    if fixed_baton_owner and owner != fixed_baton_owner:
        raise SystemExit(f"Session profile keeps {fixed_baton_owner} as the fixed baton owner.")
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


def command_reconcile_docs(session: dict[str, Any], args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: reconcile-docs <status> [message]")
    status = args[0]
    if status not in DOCUMENT_RECONCILIATION_STATUS_LABELS:
        raise SystemExit(f"Unknown document reconciliation status: {status}")
    message = args[1] if len(args) > 1 else f"Document reconciliation updated to {status}"
    session["document_reconciliation_status"] = status
    if status == "human_required":
        session["status"] = "human_required"
    elif session.get("status") == "inactive":
        session["status"] = "active"
    append_event(
        session,
        "document_reconciliation_updated",
        message,
        status=status,
    )


def command_consensus(session: dict[str, Any], args: list[str]) -> None:
    if not args:
        raise SystemExit("Usage: consensus <status> [message]")
    status = args[0]
    if status not in CONSENSUS_STATUS_LABELS:
        raise SystemExit(f"Unknown consensus status: {status}")
    if status in {"ready_for_human", "accepted"} and not document_reconciliation_complete(session):
        raise SystemExit(
            "Document reconciliation must be completed or marked not_needed before consensus can move to final approval."
        )
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
    if status == "approved" and not document_reconciliation_complete(session):
        raise SystemExit(
            "Document reconciliation must be completed or marked not_needed before human approval."
        )
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
        "phase": os.environ.get("TASK_PHASE", "Planning Materialized"),
        "summary_zh": os.environ.get("TASK_SUMMARY_ZH", "").strip(),
        "depends_on": ai_status.parse_csv_env("TASK_DEPENDS_ON"),
        "artifacts": ai_status.parse_csv_env("TASK_ARTIFACTS"),
        "acceptance": ai_status.parse_csv_env("TASK_ACCEPTANCE"),
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


def upsert_materialized_task(
    state: dict[str, Any],
    payload: dict[str, Any],
    *,
    materialization_ref: dict[str, Any] | None = None,
) -> str:
    task_id = str(payload.get("id") or "").strip()
    existing = ai_status.get_task(state, task_id)
    archived = ai_status.archived_task_snapshot(task_id)
    timestamp = iso_now()
    task_payload = {
        "id": task_id,
        "title": str(payload.get("title") or "").strip(),
        "summary_zh": str(payload.get("summary_zh") or "").strip(),
        "phase": str(payload.get("phase") or "Planning Materialized").strip() or "Planning Materialized",
        "owner": canonical_agent(payload.get("owner"), "Codex"),
        "reviewer": canonical_agent(payload.get("reviewer"), "Claude"),
        "depends_on": [str(item).strip() for item in payload.get("depends_on", []) if str(item).strip()],
        "artifacts": [str(item).strip() for item in payload.get("artifacts", []) if str(item).strip()],
        "acceptance": [str(item).strip() for item in payload.get("acceptance", []) if str(item).strip()],
        "source_plane": str(payload.get("source_plane") or "planning").strip() or "planning",
        "source_ref": payload.get("source_ref") if isinstance(payload.get("source_ref"), dict) else planning_task_source_ref(payload),
    }
    if materialization_ref:
        task_payload["materialization_ref"] = {
            str(key): value
            for key, value in materialization_ref.items()
            if value is not None and str(value).strip()
        }
    if existing is None:
        if archived is not None:
            return "archived"
        state.setdefault("tasks", []).append(
            {
                **task_payload,
                "status": "todo",
                "next": "Assignment created from accepted planning session",
                "last_update": timestamp,
            }
        )
        return "created"

    for key in ("source_plane", "source_ref", "materialization_ref"):
        if key in task_payload:
            existing[key] = task_payload[key]
    for key in ("phase", "title", "summary_zh", "depends_on", "artifacts", "acceptance"):
        current_value = existing.get(key)
        if current_value in (None, "", []):
            existing[key] = task_payload[key]
    existing["next"] = existing.get("next") or "Planning task metadata refreshed"
    return "updated"


def command_materialize(session: dict[str, Any], _args: list[str]) -> None:
    derived = build_derived_state(session)
    if not document_reconciliation_complete(derived):
        raise SystemExit("Document reconciliation must be completed or marked not_needed before materializing tasks.")
    if derived.get("human_gate_status") != "approved":
        raise SystemExit("Human gate must be approved before materializing proposed execution tasks.")

    save_derived_state(derived)
    state = ai_status.load_state()
    created = 0
    updated = 0
    archived = 0
    selected_task_ids = {
        str(item).strip()
        for item in derived.get("initial_materialization_task_ids", [])
        if str(item).strip()
    }
    selected_payloads = [
        payload
        for payload in derived.get("proposed_execution_tasks", [])
        if not selected_task_ids or str(payload.get("id") or "").strip() in selected_task_ids
    ]
    materialization_ref = {
        "materialized_at": iso_now(),
        "session_id": str(derived.get("session_id") or "").strip(),
        "consensus_status": str(derived.get("consensus_status") or "").strip(),
        "human_gate_status": str(derived.get("human_gate_status") or "").strip(),
        "execution_materialization": planning_output_path(derived, "execution_materialization"),
    }
    if selected_task_ids:
        materialization_ref["initial_materialization_task_ids"] = ",".join(sorted(selected_task_ids))
    for payload in selected_payloads:
        result = upsert_materialized_task(state, payload, materialization_ref=materialization_ref)
        if result == "created":
            created += 1
        elif result == "updated":
            updated += 1
        elif result == "archived":
            archived += 1
    session["materialized_at"] = materialization_ref["materialized_at"]
    derived["materialized_at"] = materialization_ref["materialized_at"]
    message = f"Materialized {created} new tasks and refreshed {updated} existing tasks in ai-status.json."
    if archived:
        message += f" Skipped {archived} archived terminal tasks."
    append_event(
        session,
        "execution_tasks_materialized",
        message,
        status="approved",
    )
    save_session(session)
    save_derived_state(derived)
    ai_status.sync_all(state)


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "sync"
    args = argv[2:]
    commands = {
        "sync": command_sync,
        "start": command_start,
        "baton": command_baton,
        "readout": command_readout,
        "round": command_round,
        "issue": command_issue,
        "reconcile-docs": command_reconcile_docs,
        "consensus": command_consensus,
        "human-gate": command_human_gate,
        "propose-task": command_propose_task,
        "materialize": command_materialize,
    }
    if command not in commands:
        raise SystemExit(command_usage().rstrip())
    with planning_lock():
        session = load_session()
        commands[command](session, args)
        sync_all(session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
