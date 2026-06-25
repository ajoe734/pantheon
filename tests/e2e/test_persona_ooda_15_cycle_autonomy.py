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

import main as bff_main  # noqa: E402
from read_store import ReadSurfaceStore  # noqa: E402


HEADERS = {"Authorization": "Bearer op-persona-ooda15:operator,reviewer,admin:mfa"}
EXPECTED_STAGE_COUNTS = {"observe": 27, "orient": 27, "decide": 27, "act": 27, "learn": 27}


def _persona_id(persona: dict[str, Any]) -> str:
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


def test_every_current_management_persona_completes_15_real_ooda_cycles(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        store = ReadSurfaceStore(
            str(Path(td) / "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
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
        bff_main.read_store = ReadSurfaceStore(
            str(Path(td) / "bff_read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
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
            fleet_ids = {item["persona_id"] for item in fleet.json()["items"]}
            assert set(persona_ids).issubset(fleet_ids)
        finally:
            bff_main.read_store = original_store
