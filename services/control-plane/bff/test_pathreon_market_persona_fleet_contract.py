from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


HEADERS = {"Authorization": "Bearer op-pathreon-fleet:operator,reviewer,admin:mfa"}
MARKET_PERSONAS = {
    "US": "persona-us-equity",
    "TW": "persona-tw-equity",
    "CRYPTO": "persona-crypto",
}


@contextmanager
def _fleet_client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_env = os.environ.get("PANTHEON_OODA_PACKET_ENABLED")
        os.environ.pop("PANTHEON_OODA_PACKET_ENABLED", None)
        bff_main.read_store = ReadSurfaceStore(
            str(Path(td) / "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        try:
            yield TestClient(bff_main.app, raise_server_exceptions=False)
        finally:
            bff_main.read_store = original_store
            if original_env is None:
                os.environ.pop("PANTHEON_OODA_PACKET_ENABLED", None)
            else:
                os.environ["PANTHEON_OODA_PACKET_ENABLED"] = original_env


@contextmanager
def _client_with_store(store: ReadSurfaceStore) -> Iterator[TestClient]:
    original_store = bff_main.read_store
    original_env = os.environ.get("PANTHEON_OODA_PACKET_ENABLED")
    os.environ.pop("PANTHEON_OODA_PACKET_ENABLED", None)
    bff_main.read_store = store
    try:
        yield TestClient(bff_main.app, raise_server_exceptions=False)
    finally:
        bff_main.read_store = original_store
        if original_env is None:
            os.environ.pop("PANTHEON_OODA_PACKET_ENABLED", None)
        else:
            os.environ["PANTHEON_OODA_PACKET_ENABLED"] = original_env


def test_default_read_store_has_us_tw_crypto_persona_execution_chain() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = ReadSurfaceStore(
            str(Path(td) / "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )

        for market, persona_id in MARKET_PERSONAS.items():
            persona = store.get_persona(persona_id)
            assert persona is not None
            assert persona["metadata"]["market_scope"] == [market]

            bindings = store.get_bindings_for_persona(persona_id)
            assert bindings
            pool_id = bindings[0]["capital_pool_id"]
            pool = store.get_capital_pool(pool_id)
            assert pool is not None
            assert pool["pool_id"] == pool_id
            assert pool["live_capital_enabled"] is False

            runtime = store.get_runtime_binding_by_runtime_id(f"runtime-{market.lower()}-equity-paper")
            if market == "CRYPTO":
                runtime = store.get_runtime_binding_by_runtime_id("runtime-crypto-paper")
            assert runtime is not None
            assert runtime["deployment_stage"] == "paper"
            assert runtime["metadata"]["live_write_enabled"] is False

            capabilities = store.get_capability_snapshot_for_persona(persona_id)
            assert capabilities is not None
            assert "governance_handoff" in capabilities["effective_tools"]
            assert "no_live_trade_without_approval" in capabilities["restrictions"]

        tw_persona = store.get_persona("persona-tw-equity")
        assert tw_persona is not None
        tw_metadata = tw_persona["metadata"]
        assert tw_metadata["data_source_status"]["state"] == "partial_readback"
        assert tw_metadata["data_source_status"]["live_ingestion_enabled"] is False
        assert tw_metadata["research_status"]["stage"] == "management_review_linked"
        assert tw_metadata["current_research_projects"][0]["project_id"] == "MGMT-QLIB-006"


def test_persona_catalog_and_health_expose_market_fields() -> None:
    with _fleet_client() as client:
        personas = client.get("/bff/personas", headers=HEADERS)
        health = client.get("/bff/v5/execution/persona-health", headers=HEADERS)

    assert personas.status_code == 200, personas.text
    catalog = {item["id"]: item for item in personas.json()["items"]}
    for market, persona_id in MARKET_PERSONAS.items():
        assert catalog[persona_id]["marketScope"] == [market]
        assert catalog[persona_id]["governanceRequired"] is True
        assert catalog[persona_id]["deploymentStage"] == "paper"
        assert catalog[persona_id]["dataSourceStatus"]["live_ingestion_enabled"] is False
        assert catalog[persona_id]["dataSources"]
        assert catalog[persona_id]["researchStatus"]["can_deploy"] is False

    tw_catalog = catalog["persona-tw-equity"]
    assert tw_catalog["dataSourceStatus"]["state"] == "partial_readback"
    assert tw_catalog["researchStatus"]["experiment_id"] == "exp-mgmt-qlib-006"
    assert tw_catalog["currentResearchProjects"][0]["artifact_id"] == (
        "qlib-tw-cross-sectional-alpha-model-draft-v1"
    )

    assert health.status_code == 200, health.text
    health_by_id = {item["persona_id"]: item for item in health.json()["items"]}
    for market, persona_id in MARKET_PERSONAS.items():
        row = health_by_id[persona_id]
        assert row["market_scope"] == [market]
        assert row["mode"] == "paper"
        assert isinstance(row["score"], (int, float))
        assert row["routed_strategies"] >= 1
        assert row["metrics"]["violation_count"] == 0


def test_persona_league_filters_and_requires_governance_for_rank_actions() -> None:
    with _fleet_client() as client:
        all_rows = client.get("/bff/persona-league", headers=HEADERS)
        tw_rows = client.get("/bff/persona-league?market_scope=TW", headers=HEADERS)
        detail = client.get("/bff/persona-league/persona-crypto", headers=HEADERS)

    assert all_rows.status_code == 200, all_rows.text
    rows = all_rows.json()["items"]
    assert [row["persona_id"] for row in rows[:3]] == [
        "persona-crypto",
        "persona-us-equity",
        "persona-tw-equity",
    ]
    assert all(row["governance_required"] is True for row in rows[:3])

    assert tw_rows.status_code == 200, tw_rows.text
    assert [row["persona_id"] for row in tw_rows.json()["items"]] == ["persona-tw-equity"]

    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["recommendation"] == "prepare_canary_packet"


def test_management_fleet_composes_personas_ooda_capital_runtime_and_human_gate() -> None:
    with _fleet_client() as client:
        response = client.get("/bff/management/fleet", headers=HEADERS)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    fleet_ids = {item["persona_id"] for item in data["persona_fleet"]}
    assert set(MARKET_PERSONAS.values()).issubset(fleet_ids)
    assert data["capital_totals"]["total_nav"] > 0
    assert data["human_inbox"]["pending_count"] >= 3
    assert data["ooda_status"]["enabled"] is True
    assert data["execution_boundary"] == {
        "approved_artifacts_only": True,
        "live_capital_side_effects": False,
        "human_gate_required_for_capital_changes": True,
    }


def test_management_persona_fleet_alias_returns_ui_safe_rows() -> None:
    with _fleet_client() as client:
        response = client.get("/bff/management/persona-fleet", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]
    assert payload["items"] == data["items"] == data["persona_fleet"]

    rows = {item["persona_id"]: item for item in data["items"]}
    assert set(MARKET_PERSONAS.values()).issubset(rows)

    tw = rows["persona-tw-equity"]
    assert tw["personaId"] == "persona-tw-equity"
    assert tw["personaName"] == "Taiwan Equity Persona"
    assert tw["owner"] == "pathreon-management"
    assert tw["ooda"] == "Decide"
    assert tw["autonomy"] == "supervised"
    assert tw["humanNeeded"] is True
    assert tw["state"] == "needs_human_approval"
    assert tw["lastMutation"] == "2026-06-07"
    assert tw["perfDelta"] == 0.095
    assert tw["currentWork"] == "TW corporate-action and session-boundary evidence review"
    assert tw["dataSourceStatus"]["state"] == "partial_readback"
    assert tw["dataSourceStatus"]["order_side_effects_allowed"] is False
    assert tw["dataSourceStatus"]["capital_side_effects_allowed"] is False
    assert tw["dataSourceStatus"]["provider_statuses"] == {
        "mops": "public_reference_unavailable",
        "shioaji": "read_ok",
        "tej": "credential_unavailable",
        "tpex": "read_unavailable",
        "twse": "read_unavailable",
    }
    data_sources = {source["provider_key"]: source for source in tw["dataSources"]}
    assert data_sources["shioaji"]["status"] == "read_ok"
    assert data_sources["shioaji"]["order_path"] == "disabled_for_marketdata_smoke"
    assert data_sources["shioaji"]["order_side_effects_allowed"] is False
    assert data_sources["twse"]["status"] == "read_unavailable"
    assert data_sources["tpex"]["status"] == "read_unavailable"
    assert data_sources["tej"]["status"] == "credential_unavailable"
    assert tw["researchStatus"]["stage"] == "management_review_linked"
    assert tw["researchStatus"]["framework"] == "qlib"
    assert tw["researchStatus"]["artifact_id"] == "qlib-tw-cross-sectional-alpha-model-draft-v1"
    assert tw["researchStatus"]["registry_admission_status"] == "pending_upstream_task"
    assert tw["researchStatus"]["can_deploy"] is False
    assert tw["currentResearchProjects"][0]["project_id"] == "MGMT-QLIB-006"
    assert "support/evidence/MGMT-QLIB-006/management_linkage_packet.json" in {
        ref.get("ref") for ref in tw["researchRefs"]
    }


def test_management_fleet_keeps_market_personas_with_live_dev_overlay_only() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = ReadSurfaceStore(
            str(Path(td) / "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        store._data["personas"] = {
            "persona-dev-probe": {
                "id": "persona-dev-probe",
                "persona_id": "persona-dev-probe",
                "name": "dev-probe",
                "lifecycle_state": "paper",
                "status": "healthy",
                "created_at": "2026-06-03T08:27:44Z",
                "updated_at": "2026-06-03T08:27:44Z",
                "metadata": {"owner": "pantheon-dev-browser"},
                "canonicalWriteAuthority": "persona_registry_service",
                "persistenceMode": "bff_local_dev_store",
            }
        }

        with _client_with_store(store) as client:
            response = client.get("/bff/management/fleet", headers=HEADERS)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    rows = {item["persona_id"]: item for item in data["items"]}
    assert "persona-dev-probe" in rows
    assert set(MARKET_PERSONAS.values()).issubset(rows)
    assert {pool["pool_id"] for pool in data["capital_pools"]}.issuperset(
        {"pool-us-equity-paper", "pool-tw-equity-paper", "pool-crypto-paper"}
    )
    assert {item["persona_id"] for item in data["persona_league"]}.issuperset(
        set(MARKET_PERSONAS.values())
    )

    tw = rows["persona-tw-equity"]
    assert tw["dataSourceStatus"]["state"] == "partial_readback"
    assert tw["researchStatus"]["stage"] == "management_review_linked"
    assert tw["currentResearchProjects"][0]["project_id"] == "MGMT-QLIB-006"


def test_agora_and_ooda_routes_surface_market_persona_work() -> None:
    with _fleet_client() as client:
        signals = client.get("/bff/agora/signals", headers=HEADERS)
        packets = client.get("/bff/ooda/packets", headers=HEADERS)
        crypto_packets = client.get("/bff/runtimes/runtime-crypto-paper/ooda", headers=HEADERS)

    assert signals.status_code == 200, signals.text
    signal_personas = {item.get("persona_id") for item in signals.json()["items"]}
    assert set(MARKET_PERSONAS.values()).issubset(signal_personas)

    assert packets.status_code == 200, packets.text
    packets_by_id = {item["packet_id"]: item for item in packets.json()["items"]}
    packet_ids = set(packets_by_id)
    assert {
        "ooda-us-equity-paper-001",
        "ooda-tw-equity-paper-001",
        "ooda-crypto-paper-001",
    }.issubset(packet_ids)
    tw_packet = packets_by_id["ooda-tw-equity-paper-001"]
    assert tw_packet["observe"]["data_source_status"]["state"] == "partial_readback"
    assert (
        "support/evidence/P2-MARKETDATA-CREDENTIAL-SMOKE-001/"
        "repo-local-quote-readback/shioaji.json"
    ) in tw_packet["observe"]["market_data_refs"]
    assert tw_packet["orient"]["research_status"]["stage"] == "management_review_linked"
    assert (
        "support/evidence/MGMT-QLIB-006/management_linkage_packet.json"
    ) in tw_packet["orient"]["evidence_bundle_refs"]

    assert crypto_packets.status_code == 200, crypto_packets.text
    assert [item["packet_id"] for item in crypto_packets.json()["items"]] == [
        "ooda-crypto-paper-001"
    ]
