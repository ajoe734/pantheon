"""E2E: every current management persona completes 15 OODA cycles."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from services.persona.ooda_cycle_runtime import (
    ALPHA_SEED_SOURCES,
    CYCLES_PER_PERSONA,
    DEFAULT_BACKTEST_COUNT,
    HISTORICAL_OHLCV_DATASET_ID,
    HISTORICAL_OHLCV_FIXTURE,
    OODA_SCENARIOS,
    run_management_persona_ooda_cycles,
)


ROOT = Path(__file__).resolve().parents[2]
BFF_DIR = ROOT / "services" / "control-plane" / "bff"
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))

import json
import os
import main as bff_main  # noqa: E402
from ports import ReadSurfacePorts  # noqa: E402

from tests.e2e.ooda_e2e_fixtures import load_ooda_e2e_dataset


class OodaE2ETestStore(ReadSurfacePorts):
    def __init__(self, data_path: Optional[str] = None) -> None:
        super().__init__()
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

    def list_ooda_packets(
        self,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        strategy_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
        evolution_program_id: Optional[str] = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        store_env = os.environ.get("PANTHEON_BFF_OODA_PACKET_STORE")
        packets: list[dict[str, Any]] = []
        if store_env and Path(store_env).exists():
            content = Path(store_env).read_text(encoding="utf-8").strip()
            packets_by_id: dict[str, dict[str, Any]] = {}
            for line in content.splitlines():
                line = line.strip()
                if line:
                    try:
                        record = json.loads(line)
                        packet = record
                        if isinstance(record, dict) and str(record.get("schema_version") or "") == "ooda_loop_packet_record.v1":
                            payload = record.get("payload")
                            if isinstance(payload, dict):
                                rtype = str(record.get("record_type") or "")
                                if rtype == "packet_snapshot":
                                    packet = payload
                                elif rtype == "stage_transition":
                                    packet = payload.get("packet")
                                else:
                                    packet = None
                                if isinstance(packet, dict):
                                    packet = dict(packet)
                                    packet.setdefault("packet_id", record.get("packet_id"))
                        if isinstance(packet, dict):
                            pid = str(packet.get("packet_id") or packet.get("id") or "")
                            if pid:
                                packets_by_id[pid] = packet
                    except json.JSONDecodeError:
                        pass
            if packets_by_id:
                packets = list(packets_by_id.values())
        if not packets:
            raw = self._data.get("ooda_packets", [])
            if isinstance(raw, dict):
                packets = list(raw.values())
            else:
                packets = list(raw)

        if status:
            requested = {s.strip().lower() for s in status.split(",") if s.strip()}
            packets = [p for p in packets if str(p.get("status") or "").lower() in requested]
        if stage:
            requested = {s.strip().lower() for s in stage.split(",") if s.strip()}
            packets = [p for p in packets if str(p.get("stage") or "").lower() in requested]
        if strategy_id:
            packets = [p for p in packets if str(p.get("strategy_id") or p.get("strategyId") or "") == strategy_id]
        if runtime_id:
            packets = [p for p in packets if str(p.get("runtime_id") or p.get("runtimeId") or "") == runtime_id]
        if evolution_program_id:
            packets = [p for p in packets if str(p.get("evolution_program_id") or "") == evolution_program_id]

        return packets

    def list_ooda_packets_for_strategy(self, strategy_id: str) -> list[dict[str, Any]]:
        return [
            p for p in self.list_ooda_packets()
            if str(p.get("strategy_id") or p.get("strategyId") or "") == strategy_id
            or strategy_id in (p.get("strategy_ids") or [])
        ]

    def list_ooda_packets_for_runtime(self, runtime_id: str) -> list[dict[str, Any]]:
        return [
            p for p in self.list_ooda_packets()
            if str(p.get("runtime_id") or p.get("runtimeId") or "") == runtime_id
            or str(p.get("runtime_binding_id") or "") == runtime_id
        ]

    def list_ooda_packets_for_evolution_program(self, program_id: str) -> list[dict[str, Any]]:
        return [
            p for p in self.list_ooda_packets()
            if str(p.get("evolution_program_id") or p.get("program_id") or "") == program_id
        ]

    def list_ooda_packets_for_stage(self, stage: str) -> list[dict[str, Any]]:
        return [
            p for p in self.list_ooda_packets()
            if str(p.get("stage") or "").lower() == stage.lower()
        ]

    def list_ooda_packets_for_persona(self, persona_id: str) -> list[dict[str, Any]]:
        return [
            p for p in self.list_ooda_packets()
            if str(p.get("persona_id") or "") == persona_id
        ]

    def get_ooda_packet(self, packet_id: str) -> Optional[dict[str, Any]]:
        for packet in self.list_ooda_packets():
            if str(packet.get("packet_id") or packet.get("id") or "") == packet_id:
                return packet
        return None


HEADERS = {"Authorization": "Bearer op-persona-ooda15:operator,reviewer,admin:mfa"}
EXPECTED_STAGE_COUNTS = {"observe": 27, "orient": 27, "decide": 27, "act": 27, "learn": 27}


def _persona_id(persona: dict[str, Any]) -> str:
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


def test_every_current_management_persona_completes_15_real_ooda_cycles(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        store = OodaE2ETestStore()
        personas = list(store.list_personas())
        persona_ids = [_persona_id(persona) for persona in personas]

        assert persona_ids == [
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

        batch = run_management_persona_ooda_cycles(
            personas,
            persona_contexts=_contexts_for(store, personas),
            store_path=Path(td) / "ooda_loop_packets.jsonl",
            cycles_per_persona=CYCLES_PER_PERSONA,
            backtest_count=DEFAULT_BACKTEST_COUNT,
            reset_store=True,
        )

        assert batch.summary["persona_count"] == 9
        assert batch.summary["cycles_per_persona"] == 15
        assert batch.summary["total_cycles"] == 135
        assert batch.summary["closed_cycles"] == 135
        assert batch.summary["stage_counts"] == EXPECTED_STAGE_COUNTS
        assert set(batch.summary["scenario_counts"]) == {scenario.scenario_id for scenario in OODA_SCENARIOS}
        assert set(batch.summary["scenario_counts"].values()) == {9}
        assert set(batch.summary["per_persona_cycle_counts"].values()) == {15}
        assert all(
            coverage == ["act", "decide", "learn", "observe", "orient"]
            for coverage in batch.summary["per_persona_stage_coverage"].values()
        )

        assert len(batch.backtest_results) == 5
        assert 3 <= len(batch.backtest_results) <= 5
        for result in batch.backtest_results:
            assert result["component"] == "vectorbt"
            assert result["status"] == "completed"
            assert result["artifact_family"] == "vectorbt_backtest"
            assert result["dataset_summary"]["dataset_id"] == HISTORICAL_OHLCV_DATASET_ID
            assert HISTORICAL_OHLCV_DATASET_ID in result["source_dataset_refs"]
            assert result["dataset_summary"]["num_instruments"] == 2
            assert result["dataset_summary"]["total_bars"] >= 60
            assert all(
                instrument.startswith("TWSE_")
                for instrument in result["dataset_summary"]["instruments"]
            )
            assert all(
                bars >= 30
                for bars in result["dataset_summary"]["bars_per_instrument"].values()
            )
            assert result["backtest_backend"] in {"stub_backtest", "vectorbt_portfolio"}
            assert result["persona_followup"]["ooda_phase"] == "decide"
            assert result["persona_followup"]["next_action"] == "draft_strategy_proposal"
            assert result["metrics"]["num_instruments"] == 2
            assert result["metrics"]["total_trades"] > 0
            assert result["seed_evidence_path"]
            assert (ROOT / result["seed_evidence_path"]).exists()
        assert (ROOT / HISTORICAL_OHLCV_FIXTURE).exists()

        for source in ALPHA_SEED_SOURCES:
            evidence = ROOT / source.evidence_path
            assert evidence.exists(), source.evidence_path
            text = evidence.read_text(encoding="utf-8")
            for anchor in source.anchors:
                assert anchor in text

        assert len(batch.session_results) == 9
        assert {result["persona_id"] for result in batch.session_results} == set(persona_ids)
        assert all(result["component"] == "openclaw" for result in batch.session_results)

        packets = list(batch.packets)
        assert len(packets) == 135
        assert all(packet["status"] == "closed" for packet in packets)
        assert all(packet["closed_at"] for packet in packets)
        assert all(packet["act"]["live_capital_side_effects"] is False for packet in packets)
        assert all(packet["management_summary"]["autonomy_state"] == "autonomous_closed_loop" for packet in packets)
        assert all(packet["management_summary"]["visible_to_management"] is True for packet in packets)
        assert all(packet["autonomous_next_work"]["daily_work_queue_ref"] for packet in packets)
        assert all(packet["source_truth"]["persona_catalog_source"] == "ReadSurfaceStore.list_personas" for packet in packets)
        assert all((ROOT / packet["source_truth"]["alpha_seed_source_ref"]).exists() for packet in packets)
        assert all(packet["oss_results"]["backtest_metrics"]["num_instruments"] == 2 for packet in packets)
        assert all(
            packet["oss_results"]["backtest_dataset_summary"]["dataset_id"] == HISTORICAL_OHLCV_DATASET_ID
            for packet in packets
        )
        assert all(
            HISTORICAL_OHLCV_DATASET_ID in packet["oss_results"]["backtest_source_dataset_refs"]
            for packet in packets
        )
        assert all(
            packet["oss_results"]["backtest_persona_followup"]["next_action"] == "draft_strategy_proposal"
            for packet in packets
        )

        record_lines = batch.store_path.read_text(encoding="utf-8").splitlines()
        assert len(record_lines) == 135 * 7

        original_store = bff_main.read_store
        monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
        monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
        monkeypatch.setenv("PANTHEON_BFF_OODA_PACKET_STORE", str(batch.store_path))
        monkeypatch.delenv("PANTHEON_OODA_PACKET_ENABLED", raising=False)
        bff_main.read_store = OodaE2ETestStore()
        try:
            client = TestClient(bff_main.app, raise_server_exceptions=False)

            listed = client.get("/bff/ooda/packets?page_size=200", headers=HEADERS)
            assert listed.status_code == 200, listed.text
            listed_body = listed.json()
            assert listed_body["page_info"]["total"] == 135
            listed_packets = listed_body["items"]
            assert len(listed_packets) == 135
            assert {packet["persona_id"] for packet in listed_packets} == set(persona_ids)
            assert listed_body["meta"]["surfaces"]["ooda_packets"]["source"] == "service_store"

            for stage, expected_count in EXPECTED_STAGE_COUNTS.items():
                response = client.get(f"/bff/ooda/packets?stage={stage}&page_size=200", headers=HEADERS)
                assert response.status_code == 200, response.text
                assert response.json()["page_info"]["total"] == expected_count

            closed = client.get("/bff/ooda/packets?status=closed&page_size=200", headers=HEADERS)
            assert closed.status_code == 200, closed.text
            assert closed.json()["page_info"]["total"] == 135

            detail_packet = next(packet for packet in listed_packets if packet["stage"] == "act")
            detail = client.get(f"/bff/ooda/packets/{detail_packet['packet_id']}", headers=HEADERS)
            assert detail.status_code == 200, detail.text
            detail_data = detail.json()["data"]
            assert detail_data["management_summary"]["action_taken"]
            assert detail_data["oss_results"]["backtest_ref"].startswith("oss://vectorbt/")
            assert detail_data["autonomous_next_work"]["daily_work_queue_ref"]

            strategy = client.get(
                f"/bff/strategies/{detail_packet['strategy_id']}/ooda?page_size=200",
                headers=HEADERS,
            )
            runtime = client.get(
                f"/bff/runtimes/{detail_packet['runtime_id']}/ooda?page_size=200",
                headers=HEADERS,
            )
            evolution = client.get(
                f"/bff/evolution-programs/{detail_packet['evolution_program_id']}/ooda?page_size=200",
                headers=HEADERS,
            )
            for response in (strategy, runtime, evolution):
                assert response.status_code == 200, response.text
                assert response.json()["page_info"]["total"] >= 1
                assert detail_packet["packet_id"] in {item["packet_id"] for item in response.json()["items"]}

            control_room = client.get("/bff/v5/control-room", headers=HEADERS)
            assert control_room.status_code == 200, control_room.text
            ooda_card = control_room.json()["ooda_status"]
            assert ooda_card["enabled"] is True
            assert ooda_card["total_packet_count"] == 135
            assert ooda_card["closed_loop_count"] == 135
            assert ooda_card["open_loop_count"] == 0
            assert ooda_card["failed_loop_count"] == 0
            assert ooda_card["live_capital_side_effects"] is False
            assert control_room.json()["meta"]["surfaces"]["ooda_control_room_status"]["source"] == "service_store"

            fleet = client.get("/bff/management/persona-fleet?page_size=50", headers=HEADERS)
            assert fleet.status_code == 200, fleet.text
            fleet_ids = {item["persona_id"] for item in fleet.json()["data"]["items"]}
            assert set(persona_ids).issubset(fleet_ids)
        finally:
            bff_main.read_store = original_store
