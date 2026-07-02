"""Human-in-the-loop management frontend OODA validation.

This module composes the existing management OODA packet runtime with the
persona cognitive closed-loop runtime and the management frontend contracts.
Each round is intentionally an end-to-end management story: a closed OODA
packet is persisted, a human/operator event is applied, persona memory is
written and read back, a cognitive loop proves collaboration/tool choice, and
the management frontend/BFF read surfaces can be checked by the caller.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from services.memory.persona_memory_store import (
    PersonaMemoryEntry,
    PersonaMemoryStore,
    PersonaMemoryType,
    PersonaRelevanceScope,
    PersonaSourceEventType,
    PersonaWriteAuthority,
)
from services.persona.cognitive_loop_runtime import (
    ALPHA_MODES,
    CLOSED_LOOP_TYPES,
    TOTAL_COGNITIVE_E2E_CASES,
    TOOL_SCENARIOS,
    PersonaCognitiveCase,
    build_persona_cognitive_case,
    run_persona_cognitive_closed_loop,
)
from services.persona.ooda_cycle_runtime import (
    CYCLES_PER_PERSONA,
    DEFAULT_BACKTEST_COUNT,
    OODA_SCENARIOS,
    PersonaOodaBatchRun,
    run_management_persona_ooda_cycles,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
TOTAL_HUMAN_MANAGEMENT_OODA_ROUNDS = 100
SUITE_ID = "human-management-frontend-persona-ooda-100"
ROUND_PHASES = (
    "ask_operator_frontier",
    "plan_management_frontend_flow",
    "run_persona_ooda_packet_readback",
    "apply_human_operator_event",
    "write_and_read_persona_memory",
    "run_persona_collaboration_cognitive_loop",
    "verify_frontend_and_bff_readback",
    "record_round_coverage",
)


@dataclass(frozen=True)
class HumanInteractionFlow:
    flow_id: str
    frontend_surface: str
    human_action: str
    expected_operator_state: str
    requires_human_gate: bool = False
    incident_like: bool = False
    stage_hint: str | None = None
    expected_tool: str | None = None
    cognitive_loop_type: str | None = None


HUMAN_INTERACTION_FLOWS: tuple[HumanInteractionFlow, ...] = (
    HumanInteractionFlow(
        "control_room_status_triage",
        "ooda_control_room_status_card",
        "open_control_room_ooda_card",
        "operator_can_see_closed_loop_health",
    ),
    HumanInteractionFlow(
        "packet_detail_readback",
        "ooda_packet_detail_route",
        "open_packet_detail",
        "operator_can_verify_packet_evidence",
    ),
    HumanInteractionFlow(
        "human_gate_approve_conditions",
        "human_gate_status_component",
        "approve_with_conditions",
        "human_gate_conditionally_approved",
        requires_human_gate=True,
    ),
    HumanInteractionFlow(
        "human_gate_reject_live_mutation",
        "approval_governance_route",
        "reject_live_capital_request",
        "live_mutation_rejected_and_paper_only_kept",
        requires_human_gate=True,
        expected_tool="replication_gate",
        cognitive_loop_type="bad_optimization_rejected",
    ),
    HumanInteractionFlow(
        "safe_mode_request",
        "operator_next_action_card",
        "request_safe_mode",
        "safe_mode_requested_without_live_side_effects",
        requires_human_gate=True,
        stage_hint="act",
    ),
    HumanInteractionFlow(
        "strategy_lineage_drilldown",
        "strategy_ooda_route",
        "open_strategy_ooda_history",
        "strategy_lineage_visible",
    ),
    HumanInteractionFlow(
        "runtime_binding_drilldown",
        "runtime_ooda_route",
        "open_runtime_ooda_history",
        "runtime_binding_visible",
    ),
    HumanInteractionFlow(
        "evolution_followthrough_drilldown",
        "evolution_program_ooda_route",
        "open_evolution_followthrough",
        "evolution_followthrough_visible",
        stage_hint="learn",
    ),
    HumanInteractionFlow(
        "persona_fleet_summary_contract",
        "persona_fleet_summary_contract",
        "review_persona_human_inbox",
        "human_inbox_summary_linked",
    ),
    HumanInteractionFlow(
        "collaboration_compare_stance",
        "control_room_playwright_contract",
        "compare_primary_and_risk_persona",
        "collaboration_consensus_visible",
        cognitive_loop_type="memory_changes_decision",
    ),
    HumanInteractionFlow(
        "governed_search_from_uncertainty",
        "ooda_packet_detail_route",
        "request_governed_search",
        "search_evidence_required_before_action",
        expected_tool="governed_search",
        cognitive_loop_type="uncertainty_triggers_search",
    ),
    HumanInteractionFlow(
        "performance_revalidation_decision",
        "evolution_program_ooda_route",
        "approve_paper_revalidation",
        "revalidation_decision_backed_by_threshold",
        expected_tool="evolution_decision",
        cognitive_loop_type="performance_triggers_optimization",
    ),
    HumanInteractionFlow(
        "tool_selection_review",
        "operator_next_action_card",
        "review_selected_tool",
        "tool_choice_matches_problem",
        cognitive_loop_type="persona_tool_selection",
    ),
    HumanInteractionFlow(
        "broker_go_no_go_review",
        "broker_go_no_go_dashboard",
        "review_broker_go_no_go",
        "broker_go_no_go_blockers_visible",
        expected_tool="lean_paper_handoff",
        cognitive_loop_type="persona_tool_selection",
    ),
    HumanInteractionFlow(
        "capital_binding_go_no_go_review",
        "capital_binding_go_no_go_dashboard",
        "review_capital_binding_go_no_go",
        "capital_binding_blockers_visible",
        requires_human_gate=True,
    ),
    HumanInteractionFlow(
        "approval_two_man_governance",
        "approval_governance_route",
        "request_second_signature",
        "two_man_governance_visible",
        requires_human_gate=True,
    ),
    HumanInteractionFlow(
        "incident_recovery_ack",
        "runtime_health_strip",
        "acknowledge_incident_recovery",
        "incident_recovery_acknowledged",
        incident_like=True,
    ),
    HumanInteractionFlow(
        "stage_filter_bulk_review",
        "ooda_packet_list_route",
        "filter_packets_by_stage",
        "stage_filtered_packet_list_visible",
    ),
    HumanInteractionFlow(
        "operator_note_memory_write_read",
        "docs_site_supervisor_cockpit",
        "add_operator_note",
        "operator_note_changes_next_stance",
    ),
    HumanInteractionFlow(
        "refresh_readback_audit",
        "control_room_playwright_contract",
        "refresh_and_reconcile_readback",
        "readback_routes_reconciled",
    ),
)

FRONTEND_MANAGEMENT_SURFACES = tuple(
    dict.fromkeys(flow.frontend_surface for flow in HUMAN_INTERACTION_FLOWS)
)
HUMAN_ACTIONS = tuple(dict.fromkeys(flow.human_action for flow in HUMAN_INTERACTION_FLOWS))
EXPECTED_BFF_READBACK_ROUTES = (
    "control_room",
    "packet_list",
    "packet_detail",
    "strategy_ooda",
    "runtime_ooda",
    "evolution_ooda",
    "persona_fleet",
)


_FRONTEND_ASSET_CONTRACTS: dict[str, tuple[str, ...]] = {
    "docs-site/index.html": (
        'id="supervisor-cockpit-summary"',
        'id="operator-next-action"',
        'id="runtime-health-strip"',
    ),
    "docs-site/js/dashboard-renderers.js": (
        "export function renderSupervisorCockpit",
        "operator-card",
        "human gate",
        "renderRuntimeLinkDrilldown",
    ),
    "docs-site/style.css": (
        ".operator-card",
        ".runtime-health-strip",
        ".supervisor-cockpit-grid",
    ),
    "apps/management/src/screens/HumanGate/HumanGateStatus.tsx": (
        'data-testid="human-gate-status"',
        'data-testid="signatures-list"',
        'data-testid="evidence-list"',
        "can_proceed",
        "blockingReasons",
    ),
    "apps/management/src/screens/BrokerGoNoGo/BrokerGoNoGoDashboard.tsx": (
        'data-testid="broker-go-no-go-dashboard"',
        "can_activate",
        "blocking_reasons",
    ),
    "apps/management/src/screens/CapitalBindingGoNoGo/CapitalBindingGoNoGoDashboard.tsx": (
        'data-testid="capital-binding-go-no-go-dashboard"',
        "can_bind_live",
        "conflict_log_status",
    ),
    "execute-plans/e2e/02-control-room.spec.ts": (
        "CONTROL_ROOM_PATH",
        "/management/control-room",
        "/bff/v5/control-room",
        "SERVING_MOCK_BANNER",
    ),
    "execute-plans/e2e/12-approvals.spec.ts": (
        "two-man-sign",
        "batch-decide",
        "ApprovalGovernanceDecision",
    ),
}

_SURFACE_CONTRACTS: dict[str, dict[str, Any]] = {
    "docs_site_supervisor_cockpit": {
        "route": "/dashboard.html",
        "selectors": ["#supervisor-cockpit-summary"],
        "asset": "docs-site/index.html",
    },
    "operator_next_action_card": {
        "route": "/dashboard.html#operator-next-action",
        "selectors": ["#operator-next-action", ".operator-card"],
        "asset": "docs-site/js/dashboard-renderers.js",
    },
    "runtime_health_strip": {
        "route": "/dashboard.html#runtime-health-strip",
        "selectors": ["#runtime-health-strip"],
        "asset": "docs-site/js/dashboard-renderers.js",
    },
    "ooda_control_room_status_card": {
        "route": "/management/control-room",
        "selectors": ["ooda_status", "stages.observe", "stages.learn"],
        "asset": "services/control-plane/bff/main.py",
    },
    "ooda_packet_list_route": {
        "route": "/bff/ooda/packets",
        "selectors": ["items", "page_info.total", "meta.surfaces.ooda_packets"],
        "asset": "services/control-plane/bff/main.py",
    },
    "ooda_packet_detail_route": {
        "route": "/bff/ooda/packets/{packet_id}",
        "selectors": ["data.packet_id", "data.management_summary", "data.oss_results"],
        "asset": "services/control-plane/bff/main.py",
    },
    "strategy_ooda_route": {
        "route": "/bff/strategies/{strategy_id}/ooda",
        "selectors": ["items", "meta.related"],
        "asset": "services/control-plane/bff/main.py",
    },
    "runtime_ooda_route": {
        "route": "/bff/runtimes/{runtime_id}/ooda",
        "selectors": ["items", "meta.related"],
        "asset": "services/control-plane/bff/main.py",
    },
    "evolution_program_ooda_route": {
        "route": "/bff/evolution-programs/{program_id}/ooda",
        "selectors": ["items", "meta.related"],
        "asset": "services/control-plane/bff/main.py",
    },
    "persona_fleet_summary_contract": {
        "route": "/bff/management/persona-fleet",
        "selectors": [
            "data.items",
            "data.summary.human_inbox_summary",
            "data.summary.execution_boundary",
            "meta.related.human_inbox",
        ],
        "asset": "services/control-plane/bff/main.py",
    },
    "human_gate_status_component": {
        "route": "/management/human-gate/{decision_id}",
        "selectors": [
            '[data-testid="human-gate-status"]',
            '[data-testid="signatures-list"]',
            '[data-testid="evidence-list"]',
        ],
        "asset": "apps/management/src/screens/HumanGate/HumanGateStatus.tsx",
    },
    "broker_go_no_go_dashboard": {
        "route": "/management/broker-go-no-go",
        "selectors": ['[data-testid="broker-go-no-go-dashboard"]'],
        "asset": "apps/management/src/screens/BrokerGoNoGo/BrokerGoNoGoDashboard.tsx",
    },
    "capital_binding_go_no_go_dashboard": {
        "route": "/management/capital-binding-go-no-go",
        "selectors": ['[data-testid="capital-binding-go-no-go-dashboard"]'],
        "asset": "apps/management/src/screens/CapitalBindingGoNoGo/CapitalBindingGoNoGoDashboard.tsx",
    },
    "approval_governance_route": {
        "route": "/management/approvals",
        "selectors": ["two-man-sign", "batch-decide", "ApprovalGovernanceDecision"],
        "asset": "execute-plans/e2e/12-approvals.spec.ts",
    },
    "control_room_playwright_contract": {
        "route": "/management/control-room",
        "selectors": ["CONTROL_ROOM_PATH", "installBffFixtureRoutes"],
        "asset": "execute-plans/e2e/02-control-room.spec.ts",
    },
}


class HumanManagementOodaValidationError(ValueError):
    """Raised when a human management OODA round is not end-to-end valid."""


BffReadback = Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]
BffReadbackFactory = Callable[[PersonaOodaBatchRun], BffReadback]


def validate_management_frontend_assets(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Check the management frontend files that the E2E surface depends on."""

    files: dict[str, Any] = {}
    missing: list[str] = []
    for rel_path, anchors in _FRONTEND_ASSET_CONTRACTS.items():
        path = repo_root / rel_path
        exists = path.exists()
        text = path.read_text(encoding="utf-8") if exists else ""
        anchor_results = {anchor: anchor in text for anchor in anchors}
        missing.extend(f"{rel_path}:{anchor}" for anchor, ok in anchor_results.items() if not ok)
        files[rel_path] = {
            "exists": exists,
            "anchors": anchor_results,
            "missing_anchors": [anchor for anchor, ok in anchor_results.items() if not ok],
        }
        if not exists:
            missing.append(f"{rel_path}:file")

    surface_assets_present = {
        surface: bool((repo_root / str(contract["asset"])).exists())
        for surface, contract in _SURFACE_CONTRACTS.items()
    }
    missing.extend(
        f"surface_asset:{surface}:{_SURFACE_CONTRACTS[surface]['asset']}"
        for surface, ok in surface_assets_present.items()
        if not ok
    )
    result = {
        "passed": not missing,
        "files": files,
        "surface_assets_present": surface_assets_present,
        "frontend_surfaces": list(FRONTEND_MANAGEMENT_SURFACES),
        "selectors": {
            surface: list(contract["selectors"])
            for surface, contract in _SURFACE_CONTRACTS.items()
        },
        "missing": missing,
    }
    if missing:
        raise HumanManagementOodaValidationError(
            f"Management frontend asset contract missing anchors: {missing}"
        )
    return result


def run_human_management_ooda_100(
    *,
    personas: Sequence[Mapping[str, Any]],
    persona_contexts: Mapping[str, Mapping[str, Any]] | None,
    work_dir: Path,
    bff_readback_factory: BffReadbackFactory | None = None,
) -> dict[str, Any]:
    """Run 100 human/operator management frontend OODA validation rounds."""

    if not personas:
        raise HumanManagementOodaValidationError("At least one persona is required")
    work_dir.mkdir(parents=True, exist_ok=True)
    asset_checks = validate_management_frontend_assets()
    batch = run_management_persona_ooda_cycles(
        personas,
        persona_contexts=persona_contexts,
        store_path=work_dir / "ooda_loop_packets.jsonl",
        cycles_per_persona=CYCLES_PER_PERSONA,
        backtest_count=DEFAULT_BACKTEST_COUNT,
        reset_store=True,
    )
    packets = _ordered_packets(batch.packets)
    if len(packets) < TOTAL_HUMAN_MANAGEMENT_OODA_ROUNDS:
        raise HumanManagementOodaValidationError(
            f"Need at least {TOTAL_HUMAN_MANAGEMENT_OODA_ROUNDS} OODA packets, got {len(packets)}"
        )
    if batch.summary.get("closed_cycles") != batch.summary.get("total_cycles"):
        raise HumanManagementOodaValidationError("All OODA cycles must be closed before frontend validation")

    bff_readback = bff_readback_factory(batch) if bff_readback_factory else None
    operator_memory_path = work_dir / "operator-memory.json"
    if operator_memory_path.exists():
        operator_memory_path.unlink()

    results: list[dict[str, Any]] = []
    used_packet_ids: set[str] = set()
    used_cognitive_ordinals: set[int] = set()
    packets_by_persona = _packets_by_persona(packets)

    for round_number in range(1, TOTAL_HUMAN_MANAGEMENT_OODA_ROUNDS + 1):
        flow = _select_next_flow(results, round_number)
        packet = _select_packet_for_flow(packets, used_packet_ids, flow)
        used_packet_ids.add(str(packet["packet_id"]))
        cognitive_case = _select_cognitive_case(flow, used_cognitive_ordinals, round_number)
        used_cognitive_ordinals.add(cognitive_case.ordinal)
        cognitive_work_dir = work_dir / "cognitive" / f"round-{round_number:03d}"
        cognitive_work_dir.mkdir(parents=True, exist_ok=True)
        cognitive_proof = run_persona_cognitive_closed_loop(
            cognitive_case,
            persona_store_path=cognitive_work_dir / "persona-memory.json",
            institutional_store_path=cognitive_work_dir / "institutional-memory.json",
        )
        plan = build_human_management_round_plan(
            round_number=round_number,
            previous_results=results,
            flow=flow,
            packet=packet,
            cognitive_case=cognitive_case,
        )
        operator_event = _apply_operator_event(plan, packet, flow)
        memory_effect = _write_and_read_operator_memory(
            operator_memory_path,
            plan=plan,
            packet=packet,
            flow=flow,
            cognitive_proof=cognitive_proof,
        )
        evolution_window = _persona_evolution_window(packet, packets_by_persona)
        frontend_projection = _build_frontend_projection(
            flow=flow,
            packet=packet,
            cognitive_proof=cognitive_proof,
            asset_checks=asset_checks,
        )
        readback = (
            dict(bff_readback(plan, packet, frontend_projection))
            if bff_readback
            else None
        )
        result = {
            "round_id": plan["round_id"],
            "round_number": round_number,
            "asked_before_execution": True,
            "questions": plan["questions"],
            "validation_plan": plan["validation_plan"],
            "phase_order": list(ROUND_PHASES),
            "executed_phase_order": list(ROUND_PHASES),
            "selected_flow": _flow_summary(flow),
            "selected_packet": _packet_summary(packet),
            "operator_event": operator_event,
            "memory_effect": memory_effect,
            "ooda_evolution": evolution_window,
            "cognitive_loop": {
                "case": _cognitive_case_summary(cognitive_case),
                "proof": cognitive_proof,
            },
            "frontend_management": frontend_projection,
            "bff_readback": readback,
            "safety": {
                "live_capital_side_effects": bool(packet.get("act", {}).get("live_capital_side_effects")),
                "paper_only": str(packet.get("environment") or "").lower() == "paper",
                "operator_command_live_capital_side_effects": bool(
                    operator_event["command_response"]["meta"]["liveCapitalSideEffects"]
                ),
            },
        }
        defects = _detect_round_defects(result)
        if defects:
            raise HumanManagementOodaValidationError(
                f"{result['round_id']} defects must be fixed before continuing: {defects}"
            )
        result["defects_found"] = []
        result["correction_status"] = "no_defect_detected"
        result["coverage_after_round"] = human_management_coverage_digest([*results, result])
        results.append(result)

    coverage = human_management_coverage_digest(results)
    return {
        "suite_id": SUITE_ID,
        "round_count": len(results),
        "results": results,
        "coverage": coverage,
        "frontend_asset_checks": asset_checks,
        "ooda_batch": {
            "store_path": str(batch.store_path),
            "store_record_count": len(batch.store_path.read_text(encoding="utf-8").splitlines()),
            "summary": batch.summary,
            "backtest_result_count": len(batch.backtest_results),
            "session_result_count": len(batch.session_results),
        },
        "defects_found": [],
        "correction_status": "no_defect_detected",
    }


def build_human_management_round_plan(
    *,
    round_number: int,
    previous_results: Sequence[Mapping[str, Any]],
    flow: HumanInteractionFlow,
    packet: Mapping[str, Any],
    cognitive_case: PersonaCognitiveCase,
) -> dict[str, Any]:
    previous_ids = [str(result["round_id"]) for result in previous_results]
    prior_digest = human_management_coverage_digest(previous_results)
    packet_id = str(packet["packet_id"])
    combination_id = (
        f"{SUITE_ID}-{round_number:03d}|{flow.flow_id}|{flow.frontend_surface}|"
        f"{packet_id}|{cognitive_case.case_id}"
    )
    return {
        "round_id": f"{SUITE_ID}-{round_number:03d}",
        "round_number": round_number,
        "asked_before_execution": True,
        "questions": {
            "not_yet_verified": (
                f"After {prior_digest['round_count']} rounds, has flow={flow.flow_id} "
                f"through surface={flow.frontend_surface} been proven with a persisted OODA packet?"
            ),
            "deeper_validation": (
                "Can this round prove operator action, memory write/read, persona collaboration, "
                "tool choice, BFF readback, and frontend contract in one path?"
            ),
            "realistic_operator_flow": (
                f"Could management realistically perform {flow.human_action} on packet={packet_id} "
                f"while persona loop={cognitive_case.loop_type} resolves tool={cognitive_case.expected_tool}?"
            ),
        },
        "validation_plan": {
            "objective": (
                "Execute a full human-in-the-loop management frontend OODA path with persisted "
                "packet evidence, cognitive collaboration, memory influence, and BFF readback."
            ),
            "references_previous_result_ids": previous_ids,
            "prior_coverage_digest": prior_digest,
            "phase_order": list(ROUND_PHASES),
            "selected_flow": _flow_summary(flow),
            "selected_packet": _packet_summary(packet),
            "selected_cognitive_case": _cognitive_case_summary(cognitive_case),
            "iterative_combination_id": combination_id,
            "fix_policy": (
                "Missing operator event, missing memory readback, wrong tool choice, missing BFF route, "
                "frontend contract drift, live-capital side effects, or non-closed OODA packet fails the round."
            ),
        },
    }


def human_management_coverage_digest(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    flows = [str(result["selected_flow"]["flow_id"]) for result in results]
    surfaces = [str(result["selected_flow"]["frontend_surface"]) for result in results]
    stages = [str(result["selected_packet"]["stage"]) for result in results]
    cognitive_loop_types = [
        str(result["cognitive_loop"]["case"]["loop_type"])
        for result in results
    ]
    alpha_modes = [
        str(result["cognitive_loop"]["case"]["alpha_mode"])
        for result in results
    ]
    tools = [
        str(result["cognitive_loop"]["proof"]["tool_choice"]["selected_tool"])
        for result in results
    ]
    readback_routes: list[str] = []
    for result in results:
        readback = result.get("bff_readback")
        if isinstance(readback, Mapping):
            readback_routes.extend(str(route) for route in readback.get("routes_verified", []))

    return {
        "round_count": len(results),
        "round_ids": [str(result["round_id"]) for result in results],
        "selected_packet_ids": [str(result["selected_packet"]["packet_id"]) for result in results],
        "flow_counts": _ordered_counts(flows, [flow.flow_id for flow in HUMAN_INTERACTION_FLOWS]),
        "frontend_surface_counts": _ordered_counts(surfaces, FRONTEND_MANAGEMENT_SURFACES),
        "human_action_counts": _ordered_counts(
            (str(result["selected_flow"]["human_action"]) for result in results),
            HUMAN_ACTIONS,
        ),
        "ooda_stage_counts": _ordered_counts(stages, ("observe", "orient", "decide", "act", "learn")),
        "cognitive_loop_counts": _ordered_counts(cognitive_loop_types, CLOSED_LOOP_TYPES),
        "alpha_mode_counts": _ordered_counts(alpha_modes, ALPHA_MODES),
        "selected_tool_counts": _ordered_counts(
            tools,
            ("governed_search", "replication_gate", "evolution_decision", "persona_memory", "lean_paper_handoff"),
        ),
        "bff_route_counts": _ordered_counts(readback_routes, EXPECTED_BFF_READBACK_ROUTES),
        "human_gate_round_count": sum(
            1 for result in results if result["selected_packet"]["requires_human_gate"]
        ),
        "incident_round_count": sum(
            1 for result in results if result["selected_packet"]["incident_like"]
        ),
        "memory_changed_decision_count": sum(
            1 for result in results if result["memory_effect"]["changed_persona_stance"]
        ),
        "collaboration_round_count": sum(
            1
            for result in results
            if result["cognitive_loop"]["proof"]["collaboration"]["collaborator_memory_reused"]
        ),
        "frontend_contract_round_count": sum(
            1
            for result in results
            if result["frontend_management"]["verified_asset_contract"] is True
        ),
        "defect_count": sum(len(result.get("defects_found", [])) for result in results),
    }


def _ordered_packets(packets: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (json.loads(json.dumps(packet)) for packet in packets),
        key=lambda packet: (
            str(packet.get("persona_id") or ""),
            int(packet.get("cycle_no") or 0),
            str(packet.get("packet_id") or ""),
        ),
    )


def _packets_by_persona(packets: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for packet in packets:
        persona_id = str(packet.get("persona_id") or "")
        grouped.setdefault(persona_id, []).append(json.loads(json.dumps(packet)))
    for persona_packets in grouped.values():
        persona_packets.sort(key=lambda item: int(item.get("cycle_no") or 0))
    return grouped


def _select_next_flow(
    previous_results: Sequence[Mapping[str, Any]],
    round_number: int,
) -> HumanInteractionFlow:
    counts = Counter(str(result["selected_flow"]["flow_id"]) for result in previous_results)
    ordered = list(HUMAN_INTERACTION_FLOWS)
    offset = (round_number - 1) % len(ordered)
    rotated = ordered[offset:] + ordered[:offset]
    return min(rotated, key=lambda flow: (counts.get(flow.flow_id, 0), ordered.index(flow)))


def _select_packet_for_flow(
    packets: Sequence[Mapping[str, Any]],
    used_packet_ids: set[str],
    flow: HumanInteractionFlow,
) -> dict[str, Any]:
    candidates = [dict(packet) for packet in packets if str(packet.get("packet_id")) not in used_packet_ids]
    if not candidates:
        raise HumanManagementOodaValidationError("No unused OODA packet remains for 100-round validation")

    def matches(packet: Mapping[str, Any]) -> bool:
        summary = packet.get("management_summary") if isinstance(packet.get("management_summary"), Mapping) else {}
        if flow.requires_human_gate and summary.get("requires_human_gate") is not True:
            return False
        if flow.incident_like and summary.get("incident_like") is not True:
            return False
        if flow.stage_hint and str(packet.get("stage") or "") != flow.stage_hint:
            return False
        return True

    matching = [packet for packet in candidates if matches(packet)]
    return matching[0] if matching else candidates[0]


def _select_cognitive_case(
    flow: HumanInteractionFlow,
    used_ordinals: set[int],
    round_number: int,
) -> PersonaCognitiveCase:
    start = ((round_number - 1) * 17) % TOTAL_COGNITIVE_E2E_CASES
    for offset in range(TOTAL_COGNITIVE_E2E_CASES):
        ordinal = ((start + offset) % TOTAL_COGNITIVE_E2E_CASES) + 1
        if ordinal in used_ordinals:
            continue
        case = build_persona_cognitive_case(ordinal)
        if _case_matches_flow(case, flow):
            return case
    raise HumanManagementOodaValidationError(f"No cognitive case matches flow {flow.flow_id}")


def _case_matches_flow(case: PersonaCognitiveCase, flow: HumanInteractionFlow) -> bool:
    if flow.cognitive_loop_type and case.loop_type != flow.cognitive_loop_type:
        return False
    if not flow.expected_tool:
        return True
    if flow.expected_tool == "governed_search":
        return (
            case.loop_type == "uncertainty_triggers_search"
            or (case.loop_type == "persona_tool_selection" and case.expected_tool == "governed_search")
        )
    if flow.expected_tool == "evolution_decision":
        return (
            case.loop_type == "performance_triggers_optimization"
            or (case.loop_type == "persona_tool_selection" and case.expected_tool == "evolution_decision")
        )
    if flow.expected_tool == "replication_gate":
        return (
            case.loop_type == "bad_optimization_rejected"
            or (case.loop_type == "persona_tool_selection" and case.expected_tool == "replication_gate")
        )
    if flow.expected_tool == "lean_paper_handoff":
        return case.loop_type == "persona_tool_selection" and case.expected_tool == "lean_paper_handoff"
    if flow.expected_tool == "persona_memory":
        return (
            case.loop_type == "memory_changes_decision"
            or (case.loop_type == "persona_tool_selection" and case.expected_tool == "persona_memory")
        )
    return case.expected_tool == flow.expected_tool


def _apply_operator_event(
    plan: Mapping[str, Any],
    packet: Mapping[str, Any],
    flow: HumanInteractionFlow,
) -> dict[str, Any]:
    packet_id = str(packet["packet_id"])
    decision = _operator_decision_for(flow)
    return {
        "event_id": f"operator-event-{plan['round_id']}",
        "operator_id": "operator-human-management-e2e",
        "action": flow.human_action,
        "decision": decision,
        "applied": True,
        "applied_to_packet_id": packet_id,
        "requires_human_gate": bool(packet["management_summary"]["requires_human_gate"]),
        "before_autonomy_state": packet["management_summary"]["autonomy_state"],
        "after_operator_state": flow.expected_operator_state,
        "source_refs": [
            packet_id,
            packet["management_summary"]["scenario_id"],
            packet["source_truth"]["alpha_seed_source_ref"],
        ],
        "command_response": {
            "status": "accepted",
            "receipt_id": f"receipt-{plan['round_id']}",
            "command": "HumanManagementOodaDecision",
            "target": {"type": "OodaPacket", "id": packet_id},
            "meta": {
                "durable": True,
                "liveCapitalSideEffects": False,
                "paperOnly": True,
            },
        },
    }


def _write_and_read_operator_memory(
    memory_path: Path,
    *,
    plan: Mapping[str, Any],
    packet: Mapping[str, Any],
    flow: HumanInteractionFlow,
    cognitive_proof: Mapping[str, Any],
) -> dict[str, Any]:
    persona_id = str(packet["persona_id"])
    round_id = str(plan["round_id"])
    memory_id = f"pmem-{round_id}"
    store = PersonaMemoryStore(path=memory_path)
    entry = PersonaMemoryEntry(
        memory_id=memory_id,
        persona_id=persona_id,
        memory_type=PersonaMemoryType.CONSULTATION_OUTCOME.value,
        content={
            "summary": (
                f"Operator {flow.human_action} on {packet['packet_id']} requires "
                f"{flow.expected_operator_state}; cognitive tool "
                f"{cognitive_proof['tool_choice']['selected_tool']} was considered."
            ),
            "structured_payload": {
                "round_id": round_id,
                "flow_id": flow.flow_id,
                "packet_id": packet["packet_id"],
                "stage": packet["stage"],
                "operator_action": flow.human_action,
                "cognitive_case_id": cognitive_proof["case_id"],
                "selected_tool": cognitive_proof["tool_choice"]["selected_tool"],
            },
            "tags": [
                "human_management_ooda_100",
                flow.flow_id,
                flow.frontend_surface,
                str(packet["stage"]),
            ],
        },
        source_event_type=PersonaSourceEventType.OPERATOR_FEEDBACK.value,
        source_event_id=f"operator-feedback-{round_id}",
        written_at=_round_timestamp(int(plan["round_number"])),
        write_authority=PersonaWriteAuthority.PERSONA_MEMORY_SVC.value,
        relevance_scope=PersonaRelevanceScope.PERSONA_AND_COMMITTEE.value,
    )
    store.create(entry)
    reopened = PersonaMemoryStore(path=memory_path)
    hits = reopened.retrieve(
        persona_id=persona_id,
        query=f"{round_id} {flow.flow_id} {packet['packet_id']}",
        tags=["human_management_ooda_100", flow.flow_id],
        limit=3,
    )
    if not hits:
        raise HumanManagementOodaValidationError(f"Operator memory did not read back for {round_id}")
    reused = reopened.mark_reused(hits[0].entry.memory_id)
    baseline_stance = "continue_autonomous_packet_review"
    adjusted_stance = _memory_adjusted_operator_stance(flow, packet, cognitive_proof)
    return {
        "store_path": str(memory_path),
        "memory_written": True,
        "memory_id": reused.memory_id,
        "source_event_id": reused.source_event_id,
        "persona_id": reused.persona_id,
        "readback_hit_ids": [hit.entry.memory_id for hit in hits],
        "reuse_count_after_read": reused.reuse_count,
        "baseline_persona_stance": baseline_stance,
        "memory_adjusted_persona_stance": adjusted_stance,
        "changed_persona_stance": baseline_stance != adjusted_stance,
    }


def _persona_evolution_window(
    packet: Mapping[str, Any],
    packets_by_persona: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    persona_id = str(packet["persona_id"])
    persona_packets = list(packets_by_persona.get(persona_id, []))
    current_index = next(
        (
            index
            for index, item in enumerate(persona_packets)
            if str(item.get("packet_id")) == str(packet["packet_id"])
        ),
        0,
    )
    start = max(0, current_index - 1)
    end = min(len(persona_packets), start + 3)
    start = max(0, end - 3)
    window = persona_packets[start:end]
    return {
        "persona_id": persona_id,
        "full_persona_cycle_count": len(persona_packets),
        "closed_packet_count": sum(1 for item in persona_packets if item.get("status") == "closed"),
        "full_stage_coverage": sorted({str(item.get("stage")) for item in persona_packets}),
        "window_packet_ids": [str(item.get("packet_id")) for item in window],
        "window_cycle_numbers": [int(item.get("cycle_no") or 0) for item in window],
        "window_stages": [str(item.get("stage")) for item in window],
        "current_cycle_no": int(packet.get("cycle_no") or 0),
        "current_next_work_ref": packet["autonomous_next_work"]["daily_work_queue_ref"],
        "learn_followthrough_refs": list(packet.get("learn", {}).get("evolution_followthrough_refs") or []),
        "multi_round_evolution_verified": (
            len(persona_packets) == CYCLES_PER_PERSONA
            and all(item.get("status") == "closed" for item in persona_packets)
            and sorted({str(item.get("stage")) for item in persona_packets})
            == ["act", "decide", "learn", "observe", "orient"]
        ),
    }


def _build_frontend_projection(
    *,
    flow: HumanInteractionFlow,
    packet: Mapping[str, Any],
    cognitive_proof: Mapping[str, Any],
    asset_checks: Mapping[str, Any],
) -> dict[str, Any]:
    contract = _SURFACE_CONTRACTS[flow.frontend_surface]
    route = _route_for_surface(flow.frontend_surface, packet)
    return {
        "surface_id": flow.frontend_surface,
        "route": route,
        "contract_route_template": contract["route"],
        "selectors": list(contract["selectors"]),
        "asset": contract["asset"],
        "verified_asset_contract": bool(asset_checks["passed"]),
        "visible_fields": {
            "packet_id": packet["packet_id"],
            "persona_id": packet["persona_id"],
            "stage": packet["stage"],
            "scenario_id": packet["scenario_id"],
            "strategy_id": packet["strategy_id"],
            "runtime_id": packet["runtime_id"],
            "evolution_program_id": packet["evolution_program_id"],
            "selected_tool": cognitive_proof["tool_choice"]["selected_tool"],
        },
        "operator_controls": {
            "can_filter_stage": True,
            "can_open_detail": True,
            "can_apply_human_decision": True,
            "can_trace_strategy": True,
            "can_trace_runtime": True,
            "can_trace_evolution": True,
            "can_refresh_readback": True,
        },
        "no_mock_fallback_allowed": True,
        "human_gate_badge": bool(packet["management_summary"]["requires_human_gate"]),
        "incident_badge": bool(packet["management_summary"]["incident_like"]),
    }


def _detect_round_defects(result: Mapping[str, Any]) -> list[str]:
    defects: list[str] = []
    if set(result["questions"]) != {"not_yet_verified", "deeper_validation", "realistic_operator_flow"}:
        defects.append("pre-execution questions are incomplete")
    if result["executed_phase_order"] != list(ROUND_PHASES):
        defects.append("round did not execute every planned phase")
    packet = result["selected_packet"]
    if packet["status"] != "closed":
        defects.append("selected OODA packet is not closed")
    if result["operator_event"]["applied"] is not True:
        defects.append("operator event was not applied")
    if result["operator_event"]["command_response"]["meta"]["liveCapitalSideEffects"] is not False:
        defects.append("operator command has live capital side effects")
    if result["memory_effect"]["memory_written"] is not True:
        defects.append("operator memory was not written")
    if result["memory_effect"]["reuse_count_after_read"] < 1:
        defects.append("operator memory was not read and marked reused")
    if result["memory_effect"]["changed_persona_stance"] is not True:
        defects.append("memory readback did not change persona stance")
    if result["ooda_evolution"]["multi_round_evolution_verified"] is not True:
        defects.append("persona does not have full closed multi-stage OODA evolution")
    cognitive = result["cognitive_loop"]["proof"]
    if cognitive["phases"] != ["observe", "orient", "collaborate", "decide", "act", "learn"]:
        defects.append("cognitive loop did not execute full OODA collaboration phases")
    if cognitive["collaboration"]["collaborator_memory_reused"] is not True:
        defects.append("collaborating persona memory was not reused")
    if cognitive["decision_changed_by_memory"] is not True:
        defects.append("cognitive decision did not change after memory read")
    if cognitive["tool_execution"]["tool"] != cognitive["tool_choice"]["selected_tool"]:
        defects.append("tool execution does not match tool choice")
    expected_tool = result["selected_flow"].get("expected_tool")
    if expected_tool and cognitive["tool_choice"]["selected_tool"] != expected_tool:
        defects.append(f"expected tool {expected_tool} but got {cognitive['tool_choice']['selected_tool']}")
    if result["frontend_management"]["verified_asset_contract"] is not True:
        defects.append("management frontend asset contract was not verified")
    if result["safety"]["live_capital_side_effects"] is not False:
        defects.append("OODA packet has live capital side effects")
    if result["safety"]["paper_only"] is not True:
        defects.append("OODA packet is not paper environment")
    readback = result.get("bff_readback")
    if readback is not None:
        defects.extend(_validate_bff_readback(packet, readback))
    return defects


def _validate_bff_readback(packet: Mapping[str, Any], readback: Mapping[str, Any]) -> list[str]:
    defects: list[str] = []
    route_statuses = readback.get("route_statuses")
    if not isinstance(route_statuses, Mapping):
        return ["BFF readback missing route_statuses"]
    for route in EXPECTED_BFF_READBACK_ROUTES:
        if int(route_statuses.get(route, 0)) != 200:
            defects.append(f"BFF route {route} did not return 200")
    if set(readback.get("routes_verified", [])) != set(EXPECTED_BFF_READBACK_ROUTES):
        defects.append("BFF readback did not cover every required route")
    if readback.get("detail_packet_id") != packet["packet_id"]:
        defects.append("BFF detail packet id mismatch")
    if packet["packet_id"] not in set(readback.get("stage_packet_ids", [])):
        defects.append("BFF stage list did not include selected packet")
    for key in ("strategy_packet_ids", "runtime_packet_ids", "evolution_packet_ids"):
        if packet["packet_id"] not in set(readback.get(key, [])):
            defects.append(f"BFF {key} did not include selected packet")
    if int(readback.get("control_room_total_packet_count", 0)) < TOTAL_HUMAN_MANAGEMENT_OODA_ROUNDS:
        defects.append("control-room OODA card does not include enough packets")
    if readback.get("control_room_live_capital_side_effects") is not False:
        defects.append("control-room reports live capital side effects")
    if readback.get("persona_fleet_paper_boundary") is not True:
        defects.append("persona fleet did not expose paper-only execution boundary")
    return defects


def _flow_summary(flow: HumanInteractionFlow) -> dict[str, Any]:
    return {
        "flow_id": flow.flow_id,
        "frontend_surface": flow.frontend_surface,
        "human_action": flow.human_action,
        "expected_operator_state": flow.expected_operator_state,
        "requires_human_gate": flow.requires_human_gate,
        "incident_like": flow.incident_like,
        "stage_hint": flow.stage_hint,
        "expected_tool": flow.expected_tool,
        "cognitive_loop_type": flow.cognitive_loop_type,
    }


def _packet_summary(packet: Mapping[str, Any]) -> dict[str, Any]:
    management = packet["management_summary"]
    return {
        "packet_id": packet["packet_id"],
        "persona_id": packet["persona_id"],
        "status": packet["status"],
        "stage": packet["stage"],
        "cycle_no": packet["cycle_no"],
        "scenario_id": packet["scenario_id"],
        "strategy_id": packet["strategy_id"],
        "runtime_id": packet["runtime_id"],
        "evolution_program_id": packet["evolution_program_id"],
        "requires_human_gate": bool(management["requires_human_gate"]),
        "incident_like": bool(management["incident_like"]),
        "live_capital_side_effects": bool(packet["act"]["live_capital_side_effects"]),
    }


def _cognitive_case_summary(case: PersonaCognitiveCase) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "ordinal": case.ordinal,
        "loop_type": case.loop_type,
        "persona_id": case.persona_id,
        "collaborator_persona_id": case.collaborator_persona_id,
        "alpha_mode": case.alpha_mode,
        "strategy_id": case.strategy_id,
        "tool_problem_kind": case.tool_problem_kind,
        "expected_tool": case.expected_tool,
    }


def _operator_decision_for(flow: HumanInteractionFlow) -> str:
    if "reject" in flow.human_action:
        return "reject"
    if "approve" in flow.human_action:
        return "approve_with_conditions"
    if "safe_mode" in flow.human_action:
        return "safe_mode"
    if "signature" in flow.human_action:
        return "second_signature_required"
    return "acknowledge"


def _memory_adjusted_operator_stance(
    flow: HumanInteractionFlow,
    packet: Mapping[str, Any],
    cognitive_proof: Mapping[str, Any],
) -> str:
    selected_tool = str(cognitive_proof["tool_choice"]["selected_tool"])
    if flow.requires_human_gate:
        return f"hold_for_human_gate_with_{selected_tool}"
    if flow.incident_like or packet["management_summary"]["incident_like"]:
        return "prefer_safe_mode_incident_recovery"
    if selected_tool == "governed_search":
        return "pause_until_cited_evidence_readback"
    if selected_tool == "evolution_decision":
        return "require_threshold_backed_revalidation"
    if selected_tool == "replication_gate":
        return "reject_unreplicable_candidate"
    if selected_tool == "lean_paper_handoff":
        return "paper_handoff_only_after_operator_review"
    return "reuse_memory_before_next_autonomous_step"


def _route_for_surface(surface: str, packet: Mapping[str, Any]) -> str:
    if surface == "ooda_packet_list_route":
        return f"/bff/ooda/packets?stage={packet['stage']}"
    if surface == "ooda_packet_detail_route":
        return f"/bff/ooda/packets/{packet['packet_id']}"
    if surface == "strategy_ooda_route":
        return f"/bff/strategies/{packet['strategy_id']}/ooda"
    if surface == "runtime_ooda_route":
        return f"/bff/runtimes/{packet['runtime_id']}/ooda"
    if surface == "evolution_program_ooda_route":
        return f"/bff/evolution-programs/{packet['evolution_program_id']}/ooda"
    if surface == "human_gate_status_component":
        return f"/management/human-gate/{packet['decide']['approval_decision_id']}"
    return str(_SURFACE_CONTRACTS[surface]["route"])


def _round_timestamp(round_number: int) -> str:
    base = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
    return (base + timedelta(minutes=round_number)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ordered_counts(values: Any, keys: Sequence[str]) -> dict[str, int]:
    counts = Counter(values)
    return {key: int(counts.get(key, 0)) for key in keys}
