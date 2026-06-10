"""SA/SD generator: produce requirement capture, SA, SD, and execution task
packets from a Management AI conversation and BFF context pack.

ASST-INTEG-005 — owned by Claude2.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .dev_docs_models import (
    ArchiveLocations,
    ConversationTurnRef,
    DevDocPacket,
    DevDocGenerateRequest,
    DocSourceRef,
    ExecutionTask,
    RequirementCapture,
    SystemAnalysis,
    SystemDesign,
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _slug(text: str, max_len: int = 40) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in text.lower())
    return safe[:max_len].strip("_")


def _get(obj: Any, *keys: str, default: Any = None) -> Any:
    """Fetch a nested value from a dict or object, traversing nested keys.

    Supports both dict-shaped BFF context packs and Pydantic/MagicMock objects.
    Returns *default* when any intermediate or final value is None.
    """
    cur: Any = obj
    for k in keys:
        if cur is None:
            return default
        cur = cur.get(k) if isinstance(cur, dict) else getattr(cur, k, None)
    return cur if cur is not None else default


# ---------------------------------------------------------------------------
# Source citation helpers
# ---------------------------------------------------------------------------

def _context_pack_source_refs(context_pack: Optional[Any]) -> List[DocSourceRef]:
    """Convert BFF context pack sources into DocSourceRef list (dict or object)."""
    if context_pack is None:
        return []
    raw_sources = _get(context_pack, "sources", default=[]) or []
    refs: List[DocSourceRef] = []
    for src in raw_sources:
        source_id = _get(src, "source_id") or str(src)
        refs.append(
            DocSourceRef(
                sourceId=source_id,
                href=_get(src, "href"),
                snapshotAt=_get(src, "snapshot_at"),
                kind=_get(src, "source_kind", default="bff") or "bff",
            )
        )
    return refs


def _conversation_source_ref(conversation_id: str) -> DocSourceRef:
    return DocSourceRef(
        sourceId="management_nl",
        href=f"/bff/management/ai/conversations/{conversation_id}",
        kind="conversation",
        note="Source conversation used for requirement extraction",
    )


def _turn_refs(
    turns: List[Dict[str, Any]],
    conversation_id: str,
) -> List[ConversationTurnRef]:
    refs: List[ConversationTurnRef] = []
    for t in turns:
        content = str(t.get("content") or t.get("text") or "")
        refs.append(
            ConversationTurnRef(
                turnId=str(t.get("turn_id") or t.get("id") or _uid("turn")),
                role=str(t.get("role") or "unknown"),
                excerpt=content[:200],
                conversationId=conversation_id,
            )
        )
    return refs


# ---------------------------------------------------------------------------
# Requirement capture
# ---------------------------------------------------------------------------

def build_requirement_capture(
    *,
    request: DevDocGenerateRequest,
    turns: List[Dict[str, Any]],
    context_pack: Optional[Any],
) -> RequirementCapture:
    """Build RequirementCapture from conversation turns and context pack."""
    conversation_id = request.conversation_id
    source_refs: List[DocSourceRef] = [_conversation_source_ref(conversation_id)]
    source_refs.extend(_context_pack_source_refs(context_pack))

    actor_id = _actor_id_from_context(context_pack)

    open_questions = _extract_open_questions(turns)

    return RequirementCapture(
        captureId=_uid("req"),
        conversationId=conversation_id,
        generatedAt=_now(),
        problem=request.feature_summary,
        actors=[actor_id] if actor_id else [],
        userIntent=_infer_user_intent(turns, request.feature_summary),
        affectedModules=list(request.affected_modules),
        constraints=_infer_constraints(context_pack),
        openQuestions=open_questions,
        sourceTurnRefs=_turn_refs(turns, conversation_id),
        sourceRefs=source_refs,
    )


def _actor_id_from_context(context_pack: Optional[Any]) -> Optional[str]:
    actor = _get(context_pack, "actor")
    if actor is None:
        return None
    return str(_get(actor, "operator_id", default="unknown"))


def _infer_user_intent(turns: List[Dict[str, Any]], fallback: str) -> str:
    """Use the last user turn content as user intent, falling back to feature_summary."""
    for t in reversed(turns):
        if str(t.get("role") or "") == "user":
            content = str(t.get("content") or t.get("text") or "").strip()
            if content:
                return content[:500]
    return fallback


def _infer_constraints(context_pack: Optional[Any]) -> List[str]:
    """Extract constraints from context pack mode (dict or object)."""
    mode = _get(context_pack, "mode")
    if mode is None:
        return []
    mode_val = _get(mode, "value", default=str(mode)) or str(mode)
    if mode_val == "user":
        return [
            "No shell access",
            "No repo write",
            "No raw logs or docker access",
            "All mutations through governed BFF actions",
        ]
    return [
        "Kernel mode active: changes must use branch/commit/PR/merge workflow",
        "All repo operations require branch protection compliance",
    ]


def _extract_open_questions(turns: List[Dict[str, Any]]) -> List[str]:
    """Find turns ending with '?' and treat them as open questions."""
    questions: List[str] = []
    for t in turns:
        content = str(t.get("content") or t.get("text") or "").strip()
        if content.endswith("?") and len(content) < 400:
            questions.append(content)
    return questions[:5]


# ---------------------------------------------------------------------------
# System Analysis
# ---------------------------------------------------------------------------

def build_system_analysis(
    *,
    capture: RequirementCapture,
    context_pack: Optional[Any],
    extra_context: Optional[Dict[str, Any]] = None,
) -> SystemAnalysis:
    source_refs: List[DocSourceRef] = [_conversation_source_ref(capture.conversation_id)]
    source_refs.extend(_context_pack_source_refs(context_pack))

    title = f"SA: {capture.problem[:80]}"
    current_state = _describe_current_state(capture, context_pack)
    roles = list(capture.actors) or ["operator", "platform-admin"]
    flows = _infer_flows(capture)
    data = _infer_data_surfaces(capture, context_pack)
    risk = _infer_risk(capture)
    edge_cases = _infer_edge_cases(capture)
    acceptance = _infer_acceptance_scenarios(capture)

    return SystemAnalysis(
        saId=_uid("sa"),
        captureId=capture.capture_id,
        generatedAt=_now(),
        title=title,
        currentState=current_state,
        roles=roles,
        flows=flows,
        data=data,
        risk=risk,
        edgeCases=edge_cases,
        acceptanceScenarios=acceptance,
        sourceRefs=source_refs,
    )


def _describe_current_state(capture: RequirementCapture, context_pack: Optional[Any]) -> str:
    modules = ", ".join(capture.affected_modules) if capture.affected_modules else "unspecified modules"
    mode = _mode_str(context_pack)
    return (
        f"Feature change requested for: {capture.problem}. "
        f"Affected modules: {modules}. "
        f"Actor context: {mode}. "
        f"Intent: {capture.user_intent}."
    )


def _mode_str(context_pack: Optional[Any]) -> str:
    mode = _get(context_pack, "mode")
    return str(_get(mode, "value", default=mode) or "unknown mode")


def _infer_flows(capture: RequirementCapture) -> List[str]:
    flows = [f"Operator initiates change via Management AI: {capture.user_intent[:120]}"]
    if capture.affected_modules:
        flows.append(f"BFF reads affected module state: {', '.join(capture.affected_modules)}")
    flows.append("Assistant generates SA/SD and execution task packet")
    flows.append("Operator reviews and approves generated artifacts")
    flows.append("Execution tasks dispatched through supervisor/autoworker")
    return flows


def _infer_data_surfaces(capture: RequirementCapture, context_pack: Optional[Any]) -> List[str]:
    surfaces = []
    backend = _get(context_pack, "backend")
    if backend is not None:
        for attr in ("control_room", "jobs", "alerts", "audit", "persona_health", "strategy_health"):
            if _get(backend, attr) is not None:
                surfaces.append(f"BFF backend surface: {attr}")
    for mod in (capture.affected_modules or []):
        surfaces.append(f"Module data: {mod}")
    if not surfaces:
        surfaces.append("BFF management AI conversation store")
    return surfaces


def _infer_risk(capture: RequirementCapture) -> List[str]:
    risk = ["Partial implementation leaves modules inconsistent"]
    if capture.constraints:
        risk.append("Mode restrictions may prevent direct execution; kernel mode activation required")
    if capture.open_questions:
        risk.append(f"{len(capture.open_questions)} open question(s) unresolved — acceptance criteria may shift")
    risk.append("Generated artifacts may need human review before dispatch")
    return risk


def _infer_edge_cases(capture: RequirementCapture) -> List[str]:
    cases = [
        "Concurrent conversation turns during generation",
        "Empty or single-turn conversations",
        "Missing context pack sources (degraded provider)",
    ]
    if capture.open_questions:
        cases.append("Generation triggered before open questions are answered")
    return cases


def _infer_acceptance_scenarios(capture: RequirementCapture) -> List[str]:
    return [
        "Requirement capture includes problem, actors, intent, modules, constraints, and source refs",
        "SA includes current state, roles, flows, data, risk, and acceptance scenarios",
        "SD includes architecture, API, DB/migration, UI, tool/action, tests, rollout, rollback",
        "Generated docs include source citations from conversation and context pack",
        "Execution tasks include owner, reviewer, dependencies, artifacts, and acceptance",
        "Artifacts land in docs/04/, docs/02-architecture/, docs/05-ui/, and .orchestrator/task-briefs/",
    ]


# ---------------------------------------------------------------------------
# System Design
# ---------------------------------------------------------------------------

def build_system_design(
    *,
    sa: SystemAnalysis,
    capture: RequirementCapture,
    context_pack: Optional[Any],
) -> SystemDesign:
    source_refs: List[DocSourceRef] = [_conversation_source_ref(capture.conversation_id)]
    source_refs.extend(_context_pack_source_refs(context_pack))

    title = f"SD: {capture.problem[:80]}"
    modules_str = ", ".join(capture.affected_modules) if capture.affected_modules else "BFF assistant surfaces"

    architecture = (
        f"Extend {modules_str} through existing BFF action catalog, context composer, "
        f"and OpenClaw adapter. No second gateway introduced. "
        f"All mutations go through governed BFF actions with preview/validation/confirmation/receipt flow."
    )

    api_contract = _api_contract(capture)
    db_migration = _db_migration(capture)
    ui_routes = _ui_routes(capture)
    tool_action = _tool_action_contract(capture)
    tests = _tests(capture)
    rollout = (
        "Deploy BFF changes behind feature flag. "
        "Validate context pack source refs in dev before enabling in prod. "
        "Monitor audit receipts and trace ids."
    )
    rollback = (
        "Disable feature flag to revert BFF routing. "
        "Existing conversation store remains intact. "
        "No DB migrations needed for rollback."
    )

    return SystemDesign(
        sdId=_uid("sd"),
        saId=sa.sa_id,
        generatedAt=_now(),
        title=title,
        architecture=architecture,
        apiContract=api_contract,
        dbMigration=db_migration,
        uiRoutes=ui_routes,
        toolActionContract=tool_action,
        tests=tests,
        rollout=rollout,
        rollback=rollback,
        sourceRefs=source_refs,
    )


def _api_contract(capture: RequirementCapture) -> List[str]:
    entries = [
        "POST /bff/assistant/dev-docs/generate — generate SA/SD packet from conversation",
        "GET /bff/assistant/dev-docs/{packet_id} — retrieve archived packet",
    ]
    for mod in (capture.affected_modules or []):
        entries.append(f"Existing BFF read surface for {mod}: used as context source")
    return entries


def _db_migration(capture: RequirementCapture) -> List[str]:
    if not capture.affected_modules:
        return ["No schema change required for SA/SD generation; docs are filesystem artifacts"]
    return [
        "No required migrations for SA/SD document archiving",
        "Conversation store schema unchanged",
        "Execution task IDs written through ai_status.py — no new tables",
    ]


def _ui_routes(capture: RequirementCapture) -> List[str]:
    return [
        "FE assistant overlay: no route change required for SA/SD generation",
        "Future: docs/05-ui/ spec for assistant-readable form registry (ASST-INTEG-008)",
    ]


def _tool_action_contract(capture: RequirementCapture) -> List[str]:
    return [
        "Tool: generate_sa_sd — preview and generate SA/SD packet from conversation",
        "Tool: archive_dev_docs — write packet to docs/04/, docs/02-architecture/, task-briefs/",
        "All tools follow preview / validation / confirm / execute / receipt flow",
        "Receipt includes packet_id, archive paths, and source_refs",
    ]


def _tests(capture: RequirementCapture) -> List[str]:
    return [
        "Unit: test_dev_docs_generator.py — models, generator, archiver functions",
        "Test: requirement capture includes all required fields and source refs",
        "Test: SA includes current_state, roles, flows, data, risk, acceptance_scenarios",
        "Test: SD includes architecture, api_contract, db_migration, ui_routes, tests, rollout, rollback",
        "Test: execution tasks include owner, reviewer, depends_on, artifacts, acceptance",
        "Test: archiver writes docs to correct paths and returns archive_locations",
        "Test: source citations link back to conversation_id and context pack sources",
    ]


# ---------------------------------------------------------------------------
# Execution task generation
# ---------------------------------------------------------------------------

def build_execution_tasks(
    *,
    capture: RequirementCapture,
    sa: SystemAnalysis,
    sd: SystemDesign,
    request: DevDocGenerateRequest,
    context_pack: Optional[Any],
    packet_id: str = "",
) -> List[ExecutionTask]:
    """Produce execution task list from the generated SA/SD.

    Pass *packet_id* from generate_dev_doc_packet so artifact paths align with
    the archive_packet output directory (docs/04/sa_sd_{packet_id}_{slug}/).
    """
    owner = request.proposed_owner or _default_owner(context_pack)
    reviewer = request.proposed_reviewer or _default_reviewer(owner)

    source_refs = [_conversation_source_ref(capture.conversation_id)]

    tasks: List[ExecutionTask] = []

    # Task 1: implementation task derived from SD
    tasks.append(
        ExecutionTask(
            taskId=_uid("task"),
            title=f"Implement: {capture.problem[:60]}",
            summary=sd.architecture,
            owner=owner,
            reviewer=reviewer,
            phase=f"Sprint ASST-INTEG / {capture.problem[:40]}",
            dependsOn=[],
            artifacts=_artifact_paths(capture, sd, packet_id),
            acceptance=list(sa.acceptance_scenarios),
            sourceRefs=source_refs,
        )
    )

    # Task 2: validation / test task
    tasks.append(
        ExecutionTask(
            taskId=_uid("task"),
            title=f"Validate and test: {capture.problem[:55]}",
            summary="Run focused tests and validation for the implementation task.",
            owner=reviewer,
            reviewer=owner,
            dependsOn=[tasks[0].task_id],
            artifacts=[p for p in _artifact_paths(capture, sd, packet_id) if "test" in p.lower()],
            acceptance=list(sd.tests),
            sourceRefs=source_refs,
        )
    )

    return tasks


def _default_owner(context_pack: Optional[Any]) -> str:
    actor = _get(context_pack, "actor")
    if actor is None:
        return "Codex"
    roles = list(_get(actor, "roles", default=[]) or [])
    if "admin" in roles:
        return "Claude"
    return "Codex"


def _default_reviewer(owner: str) -> str:
    reviewer_map = {
        "Claude": "Codex",
        "Claude2": "Codex",
        "Codex": "Claude",
        "Codex2": "Claude",
        "Gemini": "Codex",
        "Gemini2": "Codex",
        "Copilot": "Claude2",
    }
    return reviewer_map.get(owner, "Claude")


def _artifact_paths(capture: RequirementCapture, sd: SystemDesign, packet_id: str) -> List[str]:
    """Return artifact paths matching the archive_packet output directory convention.

    Uses docs/04/sa_sd_{packet_id}_{slug}/ to align with archive_packet().
    """
    slug = _slug(capture.problem)
    bundle_prefix = f"docs/04/sa_sd_{packet_id}_{slug}" if packet_id else f"docs/04/sa_sd_{slug}"
    paths = [
        f"{bundle_prefix}/requirement_capture.md",
        f"{bundle_prefix}/system_analysis.md",
        f"{bundle_prefix}/system_design.md",
    ]
    for mod in (capture.affected_modules or []):
        safe = mod.lower().replace("/", "_").replace(" ", "_")
        paths.append(f"services/control-plane/bff/{safe}/")
    paths.append(f"services/control-plane/bff/tests/test_{capture.capture_id}.py")
    return paths


# ---------------------------------------------------------------------------
# Top-level assembler
# ---------------------------------------------------------------------------

def generate_dev_doc_packet(
    *,
    request: DevDocGenerateRequest,
    turns: List[Dict[str, Any]],
    context_pack: Optional[Any],
    archive_locations: Optional[ArchiveLocations] = None,
) -> DevDocPacket:
    """Assemble the full DevDocPacket from conversation turns and context pack.

    Generates packet_id first so execution task artifact paths align with the
    archive_packet() output directory (docs/04/sa_sd_{packet_id}_{slug}/).
    """
    generated_at = _now()
    packet_id = _uid("pkt")
    actor_id = _actor_id_from_context(context_pack) or "unknown"

    capture = build_requirement_capture(
        request=request,
        turns=turns,
        context_pack=context_pack,
    )
    sa = build_system_analysis(
        capture=capture,
        context_pack=context_pack,
        extra_context=request.extra_context,
    )
    sd = build_system_design(
        sa=sa,
        capture=capture,
        context_pack=context_pack,
    )
    tasks = build_execution_tasks(
        capture=capture,
        sa=sa,
        sd=sd,
        request=request,
        context_pack=context_pack,
        packet_id=packet_id,
    )

    top_source_refs: List[DocSourceRef] = [_conversation_source_ref(request.conversation_id)]
    top_source_refs.extend(_context_pack_source_refs(context_pack))

    return DevDocPacket(
        packetId=packet_id,
        conversationId=request.conversation_id,
        generatedAt=generated_at,
        actorId=actor_id,
        requirementCapture=capture,
        systemAnalysis=sa,
        systemDesign=sd,
        executionTasks=tasks,
        archiveLocations=archive_locations,
        sourceRefs=top_source_refs,
    )
