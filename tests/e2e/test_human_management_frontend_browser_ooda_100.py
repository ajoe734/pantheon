"""E2E: 100 OODA rounds render through the real management dashboard JS."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from services.persona.human_management_ooda_validation import (
    FRONTEND_MANAGEMENT_SURFACES,
    HUMAN_INTERACTION_FLOWS,
    SUITE_ID,
    TOTAL_HUMAN_MANAGEMENT_OODA_ROUNDS,
    run_human_management_ooda_100,
)


ROOT = Path(__file__).resolve().parents[2]
BFF_DIR = ROOT / "services" / "control-plane" / "bff"
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))

from read_store import ReadSurfaceStore  # noqa: E402


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


def test_human_management_frontend_browser_executes_real_dashboard_renderers_for_100_rounds(tmp_path) -> None:
    store = ReadSurfaceStore(
        str(tmp_path / "read_surfaces.json"),
        allow_local_snapshot_fallback=True,
    )
    personas = list(store.list_personas())
    assert len(personas) == 9

    suite = run_human_management_ooda_100(
        personas=personas,
        persona_contexts=_contexts_for(store, personas),
        work_dir=tmp_path / "human-management-browser-ooda",
    )
    fixture = _browser_fixture_from_suite(suite)
    fixture_path = tmp_path / "human-management-browser-fixture.json"
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")

    completed = subprocess.run(
        [
            "node",
            "scripts/management_frontend_browser_ooda_100.mjs",
            str(fixture_path),
        ],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout

    report = json.loads(completed.stdout)
    assert report["ok"] is True
    assert report["suite_id"] == SUITE_ID
    assert report["round_count"] == TOTAL_HUMAN_MANAGEMENT_OODA_ROUNDS
    assert report["render_count"] == TOTAL_HUMAN_MANAGEMENT_OODA_ROUNDS
    assert report["click_count"] == TOTAL_HUMAN_MANAGEMENT_OODA_ROUNDS
    assert report["refresh_count"] > 0
    assert report["unique_packet_count"] == TOTAL_HUMAN_MANAGEMENT_OODA_ROUNDS
    assert report["unique_cognitive_case_count"] == TOTAL_HUMAN_MANAGEMENT_OODA_ROUNDS
    assert set(report["rendered_selectors"]) == {
        "#supervisor-cockpit-summary",
        "#operator-next-action",
        "#runtime-health-strip",
        "#active-work-matrix",
    }

    coverage = report["coverage"]
    assert set(coverage["flow_counts"]) == {flow.flow_id for flow in HUMAN_INTERACTION_FLOWS}
    assert set(coverage["surface_counts"]) == set(FRONTEND_MANAGEMENT_SURFACES)
    assert set(coverage["stage_counts"]) == {"observe", "orient", "decide", "act", "learn"}
    assert all(count > 0 for count in coverage["flow_counts"].values())
    assert all(count > 0 for count in coverage["surface_counts"].values())
    assert all(count > 0 for count in coverage["stage_counts"].values())
    assert all(count > 0 for count in coverage["action_counts"].values())
    assert all(count > 0 for count in coverage["selected_tool_counts"].values())


def _browser_fixture_from_suite(suite: Mapping[str, Any]) -> dict[str, Any]:
    rounds: list[dict[str, Any]] = []
    for result in suite["results"]:
        rounds.append(
            {
                "round_id": result["round_id"],
                "round_number": result["round_number"],
                "timestamp": f"2026-06-12T14:{result['round_number'] % 60:02d}:00Z",
                "flow": result["selected_flow"],
                "packet": result["selected_packet"],
                "frontend": result["frontend_management"],
                "operator": {
                    "action": result["operator_event"]["action"],
                    "decision": result["operator_event"]["decision"],
                    "after_operator_state": result["operator_event"]["after_operator_state"],
                },
                "memory": {
                    "memory_id": result["memory_effect"]["memory_id"],
                    "memory_adjusted_persona_stance": result["memory_effect"][
                        "memory_adjusted_persona_stance"
                    ],
                },
                "cognitive": {
                    "case_id": result["cognitive_loop"]["case"]["case_id"],
                    "selected_tool": result["cognitive_loop"]["proof"]["tool_choice"]["selected_tool"],
                    "final_action": result["cognitive_loop"]["proof"]["final_decision"]["action"],
                    "collaboration_consensus": result["cognitive_loop"]["proof"]["collaboration"]["consensus"],
                },
            }
        )
    return {
        "suite_id": suite["suite_id"],
        "round_count": suite["round_count"],
        "rounds": rounds,
        "ooda_batch": suite["ooda_batch"],
    }
