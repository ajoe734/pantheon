from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.parse
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
    meta_surfaces = response.json()["meta"]["surfaces"]
    assert meta_surfaces["persona_league"]["status"] in {"ok", "degraded"}
    assert meta_surfaces["persona_league"]["source"] != "missing"
    assert meta_surfaces["ooda_control_room_status"]["status"] == "ok"
    assert meta_surfaces["ooda_control_room_status"]["source"] != "missing"
    boundary = data["execution_boundary"]
    assert boundary["approved_artifacts_only"] is True
    assert boundary["live_capital_side_effects"] is False
    assert boundary["human_gate_required_for_capital_changes"] is True
    assert boundary["competition_default"] == "unified_paper_canary_live_cohort"
    assert boundary["separate_paper_live_datasets"] is False
    assert boundary["mode_selector"]["semantics"] == "command_safety_context_only"
    assert boundary["mode_selector"]["does_not_filter_competition_tracks"] is True


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
    assert tw["state"] == "paper_running"
    assert tw["personaStatus"] == "needs_human_approval"
    assert tw["lastMutation"] == "2026-06-07"
    assert tw["perfDelta"] == 0.095
    assert tw["currentWork"] == "TW corporate-action and session-boundary evidence review"
    assert tw["competitionTrack"] == "paper_challenger"
    assert tw["capitalScope"] == "paper"
    assert tw["readinessProjection"]["setup_status"] == "paper_runtime_active"
    assert tw["readinessProjection"]["competition_track"] == "paper_challenger"
    assert tw["readinessProjection"]["required_human_review"] == "promotion_to_canary"
    assert tw["rowAction"]["actionId"] == "open_promotion_review"
    assert tw["rowAction"]["label"] == "開啟 Canary 審核"
    assert tw["rowAction"]["href"] == "/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity"
    assert tw["rowAction"]["startupWizardVisible"] is False
    assert "啟動精靈" not in tw["rowAction"]["label"]
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
    assert [source["provider_key"] for source in tw["dataSources"][:4]] == [
        "shioaji",
        "twse",
        "tpex",
        "mops",
    ]
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


def test_persona_fleet_link_targets_mark_missing_detail_targets_unavailable() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = ReadSurfaceStore(
            str(Path(td) / "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        store._data["personas"] = {
            "persona-link-missing": {
                "id": "persona-link-missing",
                "persona_id": "persona-link-missing",
                "name": "Missing Detail Persona",
                "lifecycle_state": "active",
                "status": "active",
                "created_at": "2026-07-04T00:00:00Z",
                "updated_at": "2026-07-04T00:00:00Z",
                "canonicalWriteAuthority": "persona_registry_service",
                "persistenceMode": "bff_local_dev_store",
                "metadata": {
                    "owner": "test",
                    "market_scope": ["US"],
                    "deployment_stage": "live",
                    "capital_pool_id": "pool-link-missing",
                    "runtime_binding_id": "runtime-missing-detail",
                    "performance": {"training_improvement_pct": 12.0},
                    "data_sources": [{"provider_key": "nan", "status": "read_ok"}],
                    "data_source_status": {"provider_statuses": {"nan": "read_ok"}},
                    "research_status": {
                        "experiment_id": "missing-exp",
                        "artifact_id": "missing-artifact",
                        "summary": "Formal research summary exists but no detail target exists.",
                    },
                    "current_research_projects": [
                        {
                            "project_id": "missing-project",
                            "experiment_id": "missing-exp",
                            "artifact_id": "missing-artifact",
                        }
                    ],
                    "evolution_program_id": "not declared",
                    "evolution_decision_id": "nan",
                },
            }
        }
        store._data["persona_league"] = {
            "persona-link-missing": {
                "persona_id": "persona-link-missing",
                "deployment_stage": "live",
                "capital_pool_id": "pool-link-missing",
                "runtime_id": "runtime-missing-detail",
                "league_score": 77.0,
                "league_rank": 4,
                "governance_required": False,
                "recommendation": "",
                "status": "live_running",
            }
        }

        with _client_with_store(store) as client:
            response = client.get("/bff/management/persona-fleet", headers=HEADERS)

    assert response.status_code == 200, response.text
    row = next(item for item in response.json()["items"] if item["persona_id"] == "persona-link-missing")
    assert row["perfDelta"] == 0.12
    assert row["runtimeId"] == "runtime-missing-detail"
    assert row["researchStatus"]["experiment_id"] == "missing-exp"
    assert row["researchStatus"]["artifact_id"] == "missing-artifact"

    targets = row["linkTargets"]
    for key, reason in {
        "runtime": "no_runtime_target",
        "performance": "no_attribution_detail",
        "research": "no_research_detail",
        "artifact": "no_artifact_detail",
        "mutation": "no_mutation_detail",
        "evolution": "no_evolution_program_detail",
    }.items():
        assert targets[key]["available"] is False
        assert targets[key]["id"] is None
        assert targets[key]["href"] is None
        assert targets[key]["reason"] == reason

    assert targets["dataSources"]["available"] is False
    assert targets["dataSources"]["id"] is None
    assert targets["dataSources"]["href"] is None
    assert "nan" not in json.dumps(targets["dataSources"]).lower()
    assert row["rowAction"]["actionId"] == "repair_paper_setup"
    assert row["rowAction"]["available"] is False
    assert row["rowAction"]["href"] is None
    assert row["rowAction"]["reason"] == "no_repair_target"


def test_persona_fleet_human_link_targets_readiness_blocker_before_review_id() -> None:
    with _fleet_client() as client:
        response = client.get("/bff/management/persona-fleet", headers=HEADERS)

    assert response.status_code == 200, response.text
    tw = {item["persona_id"]: item for item in response.json()["items"]}["persona-tw-equity"]
    human = tw["linkTargets"]["human"]
    assert human["available"] is True
    assert human["kind"] == "readiness_blocker"
    assert human["id"] == "readiness_blocker:persona:persona-tw-equity"
    assert human["href"] == "/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity"
    assert human["bffHref"] == "/bff/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity"
    assert "promotion_review" not in human["id"]
    assert tw["rowAction"]["href"] == human["href"]


def test_persona_fleet_research_link_targets_existing_tw_experiment() -> None:
    with _fleet_client() as client:
        response = client.get("/bff/management/persona-fleet", headers=HEADERS)

    assert response.status_code == 200, response.text
    tw = {item["persona_id"]: item for item in response.json()["items"]}["persona-tw-equity"]
    research = tw["linkTargets"]["research"]
    assert research["available"] is True
    assert research["id"] == "exp-mgmt-qlib-006"
    assert research["href"] == "/management/experiments/exp-mgmt-qlib-006"
    assert research["bffHref"] == "/bff/research-experiments/exp-mgmt-qlib-006"


def test_persona_fleet_link_targets_use_execute_plans_management_routes() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = ReadSurfaceStore(
            str(Path(td) / "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        store.get_source_connector_registry = lambda: {
            "source": "service_client",
            "connectors": [
                {
                    "connector_id": "conn-shioaji",
                    "provider_key": "shioaji",
                    "provider": "Shioaji quote",
                    "status": "active",
                    "health": "ok",
                }
            ],
            "provider_examples": [],
            "policy_registry": None,
            "financial_data_source_catalog": None,
            "active_universe_policy": None,
        }

        with _client_with_store(store) as client:
            response = client.get("/bff/management/persona-fleet", headers=HEADERS)

    assert response.status_code == 200, response.text
    tw = {item["persona_id"]: item for item in response.json()["items"]}["persona-tw-equity"]
    targets = tw["linkTargets"]

    persona = targets["persona"]
    assert persona["available"] is True
    assert persona["href"] == "/management/personas/persona-tw-equity"
    assert persona["bffHref"] == "/bff/personas/persona-tw-equity"

    runtime = targets["runtime"]
    assert runtime["available"] is True
    parsed_runtime = urllib.parse.urlsplit(runtime["href"])
    runtime_query = urllib.parse.parse_qs(parsed_runtime.query)
    assert parsed_runtime.path == "/management/runtimes"
    assert runtime_query["persona"] == ["persona-tw-equity"]
    assert runtime_query["runtime"] == ["runtime-tw-equity-paper"]
    assert runtime["bffHref"] == "/bff/runtimes/runtime-tw-equity-paper"
    assert runtime["filters"]["persona"] == "persona-tw-equity"
    assert runtime["filters"]["runtime"] == "runtime-tw-equity-paper"
    drilldown_href = tw["drillDown"]["href"]
    assert urllib.parse.urlsplit(drilldown_href).path == "/management/runtimes"
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(drilldown_href).query)["persona"] == ["persona-tw-equity"]
    assert tw["rowAction"]["href"].startswith("/management/human-inbox/")

    performance = targets["performance"]
    assert performance["available"] is True
    assert performance["href"] == (
        "/management/performance-attribution?dimension=persona&persona=persona-tw-equity"
    )
    assert performance["bffHref"] == (
        "/bff/management/performance-attribution/by-persona?persona_id=persona-tw-equity"
    )
    assert performance["filters"]["persona"] == "persona-tw-equity"

    data_sources = targets["dataSources"]
    assert data_sources["available"] is True
    assert data_sources["href"] == "/management/data-sources?persona=persona-tw-equity&source=shioaji"
    assert data_sources["bffHref"] == "/bff/management/data-sources?provider_key=shioaji"
    assert data_sources["filters"]["persona"] == "persona-tw-equity"
    assert data_sources["filters"]["source"] == "shioaji"


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
    meta_surfaces = response.json()["meta"]["surfaces"]
    assert meta_surfaces["persona_league"]["status"] == "ok"
    assert meta_surfaces["persona_league"]["source"] == "composed_market_persona_defaults"

    tw = rows["persona-tw-equity"]
    assert tw["dataSourceStatus"]["state"] == "partial_readback"
    assert tw["researchStatus"]["stage"] == "management_review_linked"
    assert tw["currentResearchProjects"][0]["project_id"] == "MGMT-QLIB-006"


def test_tw_qlib_research_experiment_drilldown_is_governed_default_not_seed() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = ReadSurfaceStore(
            str(Path(td) / "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )

        with _client_with_store(store) as client:
            detail = client.get(
                "/bff/research-experiments/exp-mgmt-qlib-006",
                headers=HEADERS,
            )
            listing = client.get("/bff/research-experiments", headers=HEADERS)

    assert detail.status_code == 200, detail.text
    payload = detail.json()
    record = payload["data"]
    assert record["experiment_id"] == "exp-mgmt-qlib-006"
    assert record["stage"] == "management_review_linked"
    assert record["framework"] == "qlib"
    assert record["dataset_ref"] == "dataset:tw-equity-ohlcv-top50-2024-daily"
    assert record["dataset_manifest_id"] == (
        "qlib-dataset-manifest:dataset-tw-equity-ohlcv-top50-2024-daily"
    )
    assert record["research_linkage"]["admission_stage"] == "management_review_linked"
    assert record["registry_admission_status"] == "pending_upstream_task"
    assert record["can_deploy"] is False
    assert record["safety_assertions"]["broker_session_opened"] is False
    assert record["safety_assertions"]["order_route"] == "none"
    assert record["safety_assertions"]["live_capital_side_effects"] is False
    assert payload["meta"]["surfaces"]["research_experiment_detail"] == {
        "status": "ok",
        "source": "composed_market_persona_defaults",
    }

    assert listing.status_code == 200, listing.text
    list_payload = listing.json()
    ids = {item["experiment_id"] for item in list_payload["items"]}
    assert "exp-mgmt-qlib-006" in ids
    assert list_payload["meta"]["surfaces"]["research_experiments"] == {
        "status": "ok",
        "source": "composed_market_persona_defaults",
    }


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
