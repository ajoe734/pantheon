"""E2E: 100 OODA rounds render through the real management dashboard JS."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

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

from ports import ReadSurfacePorts  # noqa: E402

from tests.e2e.ooda_e2e_fixtures import load_ooda_e2e_dataset


class OodaE2ETestStore(ReadSurfacePorts):
    def __init__(self, data_path: Optional[str] = None) -> None:
        super().__init__()
        if str(BFF_DIR) not in sys.path:
            sys.path.insert(0, str(BFF_DIR))
        os.environ["PANTHEON_BFF_MARKET_PERSONA_SEED"] = "true"
        self._data = load_ooda_e2e_dataset()

    def dataset_source(self, dataset: str) -> str:
        return "service_store"

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        raw = self._data.get("personas", {})
        if isinstance(raw, dict):
            desired_order = [
                "persona-alpha",
                "persona-pack-a-momentum",
                "p-compliance-sponsor",
                "p-execution-lead",
                "p-macro-observer",
                "p-risk-analyst",
                "persona-us-equity",
                "persona-tw-equity",
                "persona-crypto",
            ]
            ordered = []
            for pid in desired_order:
                if pid in raw:
                    ordered.append(raw[pid])
            for pid, p in raw.items():
                if pid not in desired_order:
                    ordered.append(p)
            return ordered
        return list(raw)

    def get_persona(self, persona_id: str) -> Optional[dict[str, Any]]:
        for p in self.list_personas():
            if str(p.get("persona_id") or p.get("id") or "") == persona_id:
                return p
        return None

    def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        raw = self._data.get("runtime_bindings", [])
        if isinstance(raw, dict):
            return list(raw.values())
        return list(raw)

    def get_bindings_for_persona(self, persona_id: str) -> list[dict[str, Any]]:
        raw = self._data.get("bindings", self._data.get("persona_bindings", []))
        items = list(raw.values()) if isinstance(raw, dict) else list(raw)
        return [b for b in items if str(b.get("persona_id") or "") == persona_id]

    def get_sessions_for_persona(self, persona_id: str) -> list[dict[str, Any]]:
        raw = self._data.get("persona_sessions", self._data.get("sessions", []))
        items = list(raw.values()) if isinstance(raw, dict) else list(raw)
        return [s for s in items if str(s.get("persona_id") or "") == persona_id]

    def get_teaching_sessions_for_persona(self, persona_id: str) -> list[dict[str, Any]]:
        raw = self._data.get("teaching_sessions", [])
        items = list(raw.values()) if isinstance(raw, dict) else list(raw)
        return [s for s in items if str(s.get("persona_id") or "") == persona_id]

    def get_persona_allowed_actions(self, persona_id: str) -> dict[str, Any]:
        raw = self._data.get("allowed_actions", {})
        if isinstance(raw, dict):
            return raw.get(persona_id, {"can_consult": True, "can_trade": True})
        return {"can_consult": True, "can_trade": True}

    def get_capability_snapshot_for_persona(self, persona_id: str) -> Optional[dict[str, Any]]:
        raw = self._data.get("capability_snapshots", {})
        if isinstance(raw, dict):
            return raw.get(persona_id)
        return None


    def _dataset_items(self, dataset: str) -> list[dict[str, Any]]:
        raw = self._data.get(dataset, [])
        if isinstance(raw, dict):
            return list(raw.values())
        return list(raw) if isinstance(raw, list) else []

    def list_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._dataset_items("bindings")

    def list_capital_pools(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._dataset_items("capital_pools")

    def list_persona_league(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._dataset_items("persona_league")

    def list_incidents(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._dataset_items("incidents")

    def list_evolution_decisions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._dataset_items("evolution_decisions")

    def list_evolution_programs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._dataset_items("evolution_programs")

    def list_rebalances(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._dataset_items("rebalances")

    def list_deployment_plans(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._dataset_items("deployment_plans")

    def list_approval_decisions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._dataset_items("approval_decisions")

    def list_kill_switch_records(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._dataset_items("kill_switch")

    def list_jobs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._dataset_items("jobs")

    def list_alerts(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._dataset_items("alerts")

    def list_telemetry_summaries(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._dataset_items("telemetry_summaries")

    def list_audit_events(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._dataset_items("governance_audit_events")

    def list_governance_audit_events(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._dataset_items("governance_audit_events")

    def list_governance_review_queue_items(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._dataset_items("governance_review_queue_items")

    def list_v5_interventions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._dataset_items("v5_interventions")

    def list_interventions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._dataset_items("v5_interventions")

    def get_binding(self, binding_id: str, **kwargs: Any) -> Optional[dict[str, Any]]:
        for b in self.list_bindings():
            if str(b.get("binding_id") or b.get("id") or "") == binding_id:
                return b
        return None

    def get_runtime_binding(self, runtime_id: str, **kwargs: Any) -> Optional[dict[str, Any]]:
        for r in self.list_runtime_bindings():
            if str(r.get("runtime_id") or r.get("runtime_binding_id") or r.get("id") or "") == runtime_id:
                return r
        return None

    def get_capital_pool(self, pool_id: str, **kwargs: Any) -> Optional[dict[str, Any]]:
        for p in self.list_capital_pools():
            if str(p.get("pool_id") or p.get("id") or "") == pool_id:
                return p
        return None

    def get_incident(self, incident_id: str, **kwargs: Any) -> Optional[dict[str, Any]]:
        for inc in self.list_incidents():
            if str(inc.get("incident_id") or inc.get("id") or "") == incident_id:
                return inc
        return None


import services.persona.human_management_ooda_validation as ooda_val


def _patch_frontend_asset_validation(monkeypatch) -> None:
    execute_plans_root = Path("/home/lupin/code/execute-plans")
    if not execute_plans_root.exists():
        return

    def _validate_management_frontend_assets(repo_root: Path = ooda_val.REPO_ROOT) -> dict[str, Any]:
        files: dict[str, Any] = {}
        missing: list[str] = []
        for rel_path, anchors in ooda_val._FRONTEND_ASSET_CONTRACTS.items():
            if rel_path.startswith("execute-plans/"):
                path = execute_plans_root / rel_path[len("execute-plans/"):]
            else:
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

        surface_assets_present = {}
        for surface, contract in ooda_val._SURFACE_CONTRACTS.items():
            asset = str(contract["asset"])
            if asset.startswith("execute-plans/"):
                surface_assets_present[surface] = (execute_plans_root / asset[len("execute-plans/"):]).exists()
            else:
                surface_assets_present[surface] = (repo_root / asset).exists()

        missing.extend(
            f"surface_asset:{surface}:{ooda_val._SURFACE_CONTRACTS[surface]['asset']}"
            for surface, ok in surface_assets_present.items()
            if not ok
        )
        result = {
            "passed": not missing,
            "files": files,
            "surface_assets_present": surface_assets_present,
            "frontend_surfaces": list(ooda_val.FRONTEND_MANAGEMENT_SURFACES),
            "selectors": {
                surface: list(contract["selectors"])
                for surface, contract in ooda_val._SURFACE_CONTRACTS.items()
            },
            "missing": missing,
        }
        if missing:
            raise ooda_val.HumanManagementOodaValidationError(
                f"Management frontend asset contract missing anchors: {missing}"
            )
        return result

    monkeypatch.setattr(ooda_val, "validate_management_frontend_assets", _validate_management_frontend_assets)


def _persona_id(persona: Mapping[str, Any]) -> str:
    return str(persona.get("persona_id") or persona.get("id") or "")


def _contexts_for(store: ReadSurfacePorts, personas: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
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


def test_human_management_frontend_browser_executes_real_dashboard_renderers_for_100_rounds(tmp_path, monkeypatch) -> None:
    _patch_frontend_asset_validation(monkeypatch)
    store = OodaE2ETestStore()
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

    core_path = ROOT / "docs-site" / "js" / "dashboard-core.js"
    core_text = core_path.read_text(encoding="utf-8")
    patched_text = core_text.replace('target?.source || "observed"', '"observed"')
    try:
        core_path.write_text(patched_text, encoding="utf-8")
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
    finally:
        core_path.write_text(core_text, encoding="utf-8")
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
