"""E2E: 100 human-management frontend persona OODA rounds."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

from fastapi.testclient import TestClient

from services.persona.cognitive_loop_runtime import ALPHA_MODES, CLOSED_LOOP_TYPES
from services.persona.human_management_ooda_validation import (
    EXPECTED_BFF_READBACK_ROUTES,
    FRONTEND_MANAGEMENT_SURFACES,
    HUMAN_INTERACTION_FLOWS,
    ROUND_PHASES,
    SUITE_ID,
    TOTAL_HUMAN_MANAGEMENT_OODA_ROUNDS,
    human_management_coverage_digest,
    run_human_management_ooda_100,
    validate_management_frontend_assets,
)


ROOT = Path(__file__).resolve().parents[2]
BFF_DIR = ROOT / "services" / "control-plane" / "bff"
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
from read_store import ReadSurfaceStore  # noqa: E402


HEADERS = {"Authorization": "Bearer op-human-management-ooda:operator,reviewer,admin:mfa"}


def _persona_id(persona: Mapping[str, Any]) -> str:
    return str(persona.get("persona_id") or persona.get("id") or "")


def _contexts_for(store: ReadSurfaceStore, personas: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    runtime_bindings = list(store.list_runtime_bindings() or [])
    for persona in personas:
        persona_id = _persona_id(persona)
        contexts[persona_id] = {
            "bindings": list(store.get_bindings_for_persona(persona_id) or []),
            "sessions": list(store.get_sessions_for_persona(persona_id) or []),
            "teaching_sessions": list(store.get_teaching_sessions_for_persona(persona_id) or []),
            "allowed_actions": dict(store.get_persona_allowed_actions(persona_id) or {}),
            "capability_snapshot": store.get_capability_snapshot_for_persona(persona_id),
            "runtime_bindings": runtime_bindings,
        }
    return contexts


def test_human_management_frontend_manifest_contract_is_complete() -> None:
    assert TOTAL_HUMAN_MANAGEMENT_OODA_ROUNDS == 100
    assert len(HUMAN_INTERACTION_FLOWS) >= 20
    assert len({flow.flow_id for flow in HUMAN_INTERACTION_FLOWS}) == len(HUMAN_INTERACTION_FLOWS)
    assert len({flow.human_action for flow in HUMAN_INTERACTION_FLOWS}) == len(HUMAN_INTERACTION_FLOWS)
    assert set(FRONTEND_MANAGEMENT_SURFACES) == {
        flow.frontend_surface for flow in HUMAN_INTERACTION_FLOWS
    }
    assert {
        "ooda_control_room_status_card",
        "ooda_packet_list_route",
        "ooda_packet_detail_route",
        "strategy_ooda_route",
        "runtime_ooda_route",
        "evolution_program_ooda_route",
        "persona_fleet_human_inbox",
        "human_gate_status_component",
        "broker_go_no_go_dashboard",
        "capital_binding_go_no_go_dashboard",
        "approval_governance_route",
        "control_room_playwright_contract",
    }.issubset(set(FRONTEND_MANAGEMENT_SURFACES))

    asset_checks = validate_management_frontend_assets()
    assert asset_checks["passed"] is True
    assert asset_checks["missing"] == []
    assert all(asset_checks["surface_assets_present"].values())


def test_human_management_frontend_persona_ooda_runs_100_full_e2e_rounds(monkeypatch) -> None:
    original_store = bff_main.read_store
    with tempfile.TemporaryDirectory() as td:
        work_dir = Path(td)
        store = ReadSurfaceStore(
            str(work_dir / "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        personas = list(store.list_personas())
        assert len(personas) == 9

        def bff_readback_factory(batch):
            monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
            monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
            monkeypatch.setenv("PANTHEON_BFF_OODA_PACKET_STORE", str(batch.store_path))
            monkeypatch.delenv("PANTHEON_OODA_PACKET_ENABLED", raising=False)
            bff_main.read_store = ReadSurfaceStore(
                str(work_dir / "bff_read_surfaces.json"),
                allow_local_snapshot_fallback=True,
            )
            client = TestClient(bff_main.app, raise_server_exceptions=False)

            def readback(plan, packet, projection):
                return _readback_from_bff(client, packet, projection)

            return readback

        try:
            suite = run_human_management_ooda_100(
                personas=personas,
                persona_contexts=_contexts_for(store, personas),
                work_dir=work_dir / "human-management-ooda",
                bff_readback_factory=bff_readback_factory,
            )
        finally:
            bff_main.read_store = original_store

    results = suite["results"]
    assert suite["suite_id"] == SUITE_ID
    assert suite["round_count"] == TOTAL_HUMAN_MANAGEMENT_OODA_ROUNDS
    assert len(results) == TOTAL_HUMAN_MANAGEMENT_OODA_ROUNDS
    assert suite["defects_found"] == []
    assert suite["correction_status"] == "no_defect_detected"
    assert suite["frontend_asset_checks"]["passed"] is True

    ooda_summary = suite["ooda_batch"]["summary"]
    assert ooda_summary["persona_count"] == 9
    assert ooda_summary["total_cycles"] == 135
    assert ooda_summary["closed_cycles"] == 135
    assert ooda_summary["live_capital_side_effects"] is False
    assert suite["ooda_batch"]["backtest_result_count"] == 5
    assert suite["ooda_batch"]["session_result_count"] == 9
    assert suite["ooda_batch"]["store_record_count"] == 135 * 7

    round_ids = [result["round_id"] for result in results]
    selected_packet_ids = [result["selected_packet"]["packet_id"] for result in results]
    cognitive_case_ids = [result["cognitive_loop"]["case"]["case_id"] for result in results]
    assert len(set(round_ids)) == 100
    assert len(set(selected_packet_ids)) == 100
    assert len(set(cognitive_case_ids)) == 100

    for index, result in enumerate(results, start=1):
        previous_results = results[: index - 1]
        expected_previous_ids = [previous["round_id"] for previous in previous_results]
        assert result["round_number"] == index
        assert result["asked_before_execution"] is True
        assert result["validation_plan"]["references_previous_result_ids"] == expected_previous_ids
        assert result["validation_plan"]["prior_coverage_digest"] == human_management_coverage_digest(previous_results)
        assert result["phase_order"] == list(ROUND_PHASES)
        assert result["executed_phase_order"] == list(ROUND_PHASES)
        assert result["defects_found"] == []
        assert result["correction_status"] == "no_defect_detected"
        assert result["coverage_after_round"] == human_management_coverage_digest(results[:index])

        packet = result["selected_packet"]
        flow = result["selected_flow"]
        assert packet["status"] == "closed"
        assert packet["live_capital_side_effects"] is False
        if flow["requires_human_gate"]:
            assert packet["requires_human_gate"] is True
        if flow["incident_like"]:
            assert packet["incident_like"] is True
        if flow["stage_hint"]:
            assert packet["stage"] == flow["stage_hint"]

        operator = result["operator_event"]
        assert operator["applied"] is True
        assert operator["command_response"]["status"] == "accepted"
        assert operator["command_response"]["meta"]["durable"] is True
        assert operator["command_response"]["meta"]["liveCapitalSideEffects"] is False

        memory = result["memory_effect"]
        assert memory["memory_written"] is True
        assert memory["reuse_count_after_read"] == 1
        assert memory["changed_persona_stance"] is True
        assert memory["baseline_persona_stance"] != memory["memory_adjusted_persona_stance"]

        evolution = result["ooda_evolution"]
        assert evolution["full_persona_cycle_count"] == 15
        assert evolution["closed_packet_count"] == 15
        assert evolution["full_stage_coverage"] == ["act", "decide", "learn", "observe", "orient"]
        assert evolution["multi_round_evolution_verified"] is True

        cognitive = result["cognitive_loop"]["proof"]
        assert cognitive["phases"] == ["observe", "orient", "collaborate", "decide", "act", "learn"]
        assert cognitive["memory_reads"]["primary"]["reuse_count"] == 1
        assert cognitive["memory_reads"]["collaborator"]["reuse_count"] == 1
        assert cognitive["collaboration"]["collaborator_memory_reused"] is True
        assert cognitive["decision_changed_by_memory"] is True
        assert cognitive["tool_execution"]["tool"] == cognitive["tool_choice"]["selected_tool"]
        if flow["expected_tool"]:
            assert cognitive["tool_choice"]["selected_tool"] == flow["expected_tool"]

        frontend = result["frontend_management"]
        assert frontend["verified_asset_contract"] is True
        assert frontend["no_mock_fallback_allowed"] is True
        assert frontend["route"]
        assert frontend["selectors"]
        assert frontend["visible_fields"]["packet_id"] == packet["packet_id"]

        readback = result["bff_readback"]
        assert set(readback["routes_verified"]) == set(EXPECTED_BFF_READBACK_ROUTES)
        assert all(status == 200 for status in readback["route_statuses"].values())
        assert readback["detail_packet_id"] == packet["packet_id"]
        assert packet["packet_id"] in readback["stage_packet_ids"]
        assert packet["packet_id"] in readback["strategy_packet_ids"]
        assert packet["packet_id"] in readback["runtime_packet_ids"]
        assert packet["packet_id"] in readback["evolution_packet_ids"]
        assert readback["control_room_total_packet_count"] == 135
        assert readback["control_room_live_capital_side_effects"] is False
        assert readback["persona_fleet_paper_boundary"] is True
        assert readback["persona_fleet_human_inbox_present"] is True

    coverage = suite["coverage"]
    assert coverage == human_management_coverage_digest(results)
    assert coverage["round_count"] == 100
    assert coverage["defect_count"] == 0
    assert set(coverage["flow_counts"]) == {flow.flow_id for flow in HUMAN_INTERACTION_FLOWS}
    assert set(coverage["frontend_surface_counts"]) == set(FRONTEND_MANAGEMENT_SURFACES)
    assert set(coverage["human_action_counts"]) == {flow.human_action for flow in HUMAN_INTERACTION_FLOWS}
    assert set(coverage["ooda_stage_counts"]) == {"observe", "orient", "decide", "act", "learn"}
    assert set(coverage["cognitive_loop_counts"]) == set(CLOSED_LOOP_TYPES)
    assert set(coverage["alpha_mode_counts"]) == set(ALPHA_MODES)
    assert set(coverage["bff_route_counts"]) == set(EXPECTED_BFF_READBACK_ROUTES)
    assert all(count > 0 for count in coverage["flow_counts"].values())
    assert all(count > 0 for count in coverage["frontend_surface_counts"].values())
    assert all(count > 0 for count in coverage["human_action_counts"].values())
    assert all(count > 0 for count in coverage["ooda_stage_counts"].values())
    assert all(count > 0 for count in coverage["cognitive_loop_counts"].values())
    assert all(count > 0 for count in coverage["alpha_mode_counts"].values())
    assert all(count == 100 for count in coverage["bff_route_counts"].values())
    assert all(count > 0 for count in coverage["selected_tool_counts"].values())
    assert coverage["human_gate_round_count"] >= 25
    assert coverage["incident_round_count"] >= 5
    assert coverage["memory_changed_decision_count"] == 100
    assert coverage["collaboration_round_count"] == 100
    assert coverage["frontend_contract_round_count"] == 100


def _readback_from_bff(
    client: TestClient,
    packet: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    paths = {
        "control_room": "/bff/v5/control-room",
        "packet_list": f"/bff/ooda/packets?stage={packet['stage']}&page_size=200",
        "packet_detail": f"/bff/ooda/packets/{packet['packet_id']}",
        "strategy_ooda": f"/bff/strategies/{packet['strategy_id']}/ooda?page_size=200",
        "runtime_ooda": f"/bff/runtimes/{packet['runtime_id']}/ooda?page_size=200",
        "evolution_ooda": (
            f"/bff/evolution-programs/{packet['evolution_program_id']}/ooda?page_size=200"
        ),
        "persona_fleet": "/bff/management/persona-fleet?page_size=50",
    }
    bodies: dict[str, dict[str, Any]] = {}
    statuses: dict[str, int] = {}
    for route_name, path in paths.items():
        response = client.get(path, headers=HEADERS)
        statuses[route_name] = response.status_code
        bodies[route_name] = response.json()

    control_room = bodies["control_room"]
    persona_fleet = bodies["persona_fleet"]["data"]
    execution_boundary = persona_fleet["execution_boundary"]
    return {
        "routes_verified": list(paths),
        "route_statuses": statuses,
        "frontend_route": projection["route"],
        "detail_packet_id": bodies["packet_detail"]["data"]["packet_id"],
        "stage_packet_ids": _packet_ids(bodies["packet_list"]),
        "strategy_packet_ids": _packet_ids(bodies["strategy_ooda"]),
        "runtime_packet_ids": _packet_ids(bodies["runtime_ooda"]),
        "evolution_packet_ids": _packet_ids(bodies["evolution_ooda"]),
        "control_room_total_packet_count": control_room["ooda_status"]["total_packet_count"],
        "control_room_closed_loop_count": control_room["ooda_status"]["closed_loop_count"],
        "control_room_live_capital_side_effects": control_room["ooda_status"]["live_capital_side_effects"],
        "control_room_source": control_room["meta"]["surfaces"]["ooda_control_room_status"]["source"],
        "persona_fleet_paper_boundary": (
            execution_boundary["approved_artifacts_only"] is True
            and execution_boundary["live_capital_side_effects"] is False
            and execution_boundary["human_gate_required_for_capital_changes"] is True
        ),
        "persona_fleet_human_inbox_present": "human_inbox" in persona_fleet,
    }


def _packet_ids(body: Mapping[str, Any]) -> list[str]:
    return [str(item["packet_id"]) for item in body.get("items", [])]
