from __future__ import annotations

import os
import sys
import tempfile
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from ports import ReadSurfacePorts, create_in_memory_read_surface_ports, create_read_surface_ports
from read_store import _merge_market_persona_fleet


@pytest.fixture(autouse=True)
def _enable_market_persona_seed(monkeypatch):
    """This module tests the retired fixture itself, so opt in explicitly."""

    monkeypatch.setenv("PANTHEON_BFF_MARKET_PERSONA_SEED", "1")


HEADERS = {"Authorization": "Bearer op-pathreon-fleet:operator,reviewer,admin:mfa"}
PERSONA_FLEET_DEFAULT_TARGET_BYTES = 250_000
PERSONA_FLEET_DEFAULT_HARD_LIMIT_BYTES = 1_000_000
PERSONA_FLEET_ROW_HARD_LIMIT_BYTES = 8_000
PERSONA_FLEET_FORBIDDEN_LIST_KEYS = {
    "currentResearchProjects",
    "current_research_projects",
    "dataSourceRefs",
    "data_source_refs",
    "dataSourceStatus",
    "data_source_status",
    "dataSources",
    "requiredDataSources",
    "required_data_sources",
    "researchRefs",
    "research_refs",
    "researchStatus",
    "research_status",
    "sourceHealthBindings",
    "source_health_bindings",
}


def test_market_persona_seed_is_disabled_without_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("PANTHEON_BFF_MARKET_PERSONA_SEED", raising=False)
    target: dict[str, object] = {}

    assert _merge_market_persona_fleet(target) is False
    assert target == {}


MARKET_PERSONAS = {
    "US": "persona-us-equity",
    "TW": "persona-tw-equity",
    "CRYPTO": "persona-crypto",
}


def _make_store(*, allow_local_snapshot_fallback: bool = True) -> ReadSurfacePorts:
    if not allow_local_snapshot_fallback:
        return create_read_surface_ports()
    data: dict[str, Any] = {}
    _merge_market_persona_fleet(data)
    store = create_in_memory_read_surface_ports(
        operations_consultation_kwargs={
            "agora_signals": data.get("agora_signals") or {},
            "agora_sessions": data.get("agora_sessions") or {},
            "agora_watchlist": data.get("agora_watchlist") or {},
        },
        persona_capital_runtime_kwargs={
            "personas": data.get("personas") or {},
            "capital_pools": data.get("capital_pools") or {},
            "bindings": data.get("bindings") or data.get("persona_bindings") or {},
            "runtime_bindings": data.get("runtime_bindings") or {},
            "deployment_plans": data.get("deployment_plans") or {},
            "rankings": data.get("persona_rankings") or data.get("rankings") or {},
            "persona_league": list((data.get("persona_league") or {}).values()) if isinstance(data.get("persona_league"), dict) else (data.get("persona_league") or []),
            "rebalances": data.get("rebalances") or {},
            "capital_allocations": data.get("capital_allocations") or {},
            "containments": data.get("containments") or {},
        },
        persona_training_kwargs={
            "personas": data.get("personas") or {},
            "sessions": data.get("sessions") or {},
            "teaching_sessions": data.get("teaching_sessions") or {},
            "capability_snapshots": data.get("capability_snapshots") or {},
        },
        ooda_management_kwargs={
            "ooda_packets": list((data.get("ooda_packets") or {}).values()) if isinstance(data.get("ooda_packets"), dict) else (data.get("ooda_packets") or []),
            "deployment_plans": list((data.get("deployment_plans") or {}).values()) if isinstance(data.get("deployment_plans"), dict) else (data.get("deployment_plans") or []),
            "approval_decisions": list((data.get("governance_approvals") or {}).values()) if isinstance(data.get("governance_approvals"), dict) else (data.get("governance_approvals") or []),
        },
        research_knowledge_source_kwargs={
            "strategy_specs_store": data.get("strategy_specs") or {},
            "research_experiments_store": data.get("research_experiments") or {},
            "research_artifacts_store": data.get("research_artifacts") or {},
            "research_tickets_store": data.get("research_tickets") or {},
            "research_notes_store": data.get("research_notes") or {},
        },
        lifecycle_telemetry_governance_kwargs={
            "telemetry_summaries": list((data.get("telemetry_summaries") or {}).values()) if isinstance(data.get("telemetry_summaries"), dict) else (data.get("telemetry_summaries") or []),
            "incidents": list((data.get("incidents") or {}).values()) if isinstance(data.get("incidents"), dict) else (data.get("incidents") or []),
        },
        capability_snapshots=data.get("capability_snapshots") or {},
    )
    return store


@contextmanager
def _fleet_client() -> Iterator[TestClient]:
    original_store = bff_main.read_store
    original_env = os.environ.get("PANTHEON_OODA_PACKET_ENABLED")
    os.environ.pop("PANTHEON_OODA_PACKET_ENABLED", None)
    bff_main.read_store = _make_store(allow_local_snapshot_fallback=True)
    try:
        yield TestClient(bff_main.app, raise_server_exceptions=False)
    finally:
        bff_main.read_store = original_store
        if original_env is None:
            os.environ.pop("PANTHEON_OODA_PACKET_ENABLED", None)
        else:
            os.environ["PANTHEON_OODA_PACKET_ENABLED"] = original_env


@contextmanager
def _client_with_store(store: ReadSurfacePorts) -> Iterator[TestClient]:
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
    store = _make_store(allow_local_snapshot_fallback=True)

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
        assert pool["capital_mode"] == "paper"

        runtime = store.get_runtime_binding_by_runtime_id(f"runtime-{market.lower()}-equity-paper")
        if market == "CRYPTO":
            runtime = store.get_runtime_binding_by_runtime_id("runtime-crypto-paper")
        assert runtime is not None
        assert runtime["deployment_stage"] == "paper"
        assert runtime["runtime_binding_id"].endswith("-paper")
        assert runtime["metadata"]["live_write_enabled"] is False

        capabilities = store.get_capability_snapshot_for_persona(persona_id)
        assert capabilities is not None
        assert "governance_handoff" in capabilities["effective_tools"]
        assert "no_live_trade_without_approval" in capabilities["restrictions"]

    tw_persona = store.get_persona("persona-tw-equity")
    assert tw_persona is not None
    required_sources = {source["dataset"]: source for source in tw_persona["required_data_sources"]}
    assert required_sources["tw_price_daily"]["source_class"] == "live_pull"
    assert required_sources["tw_broker_top"]["source_class"] == "live_push"
    assert "tw-finmind-broker-daily-report" in required_sources["tw_broker_top"]["connector_candidates"]
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
        assert catalog[persona_id]["paperLedgerId"] == f"paper-ledger-{persona_id}"
        assert "capitalPoolId" not in catalog[persona_id]
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
        assert row["paper_ledger_id"] == f"paper-ledger-{persona_id}"
        assert row["capital_pool_id"] is None
        assert isinstance(row["score"], (int, float))
        assert row["routed_strategies"] >= 1
        assert row["metrics"]["violation_count"] == 0


def test_management_persona_fleet_hydrates_live_persona_market_context() -> None:
    store = _make_store(allow_local_snapshot_fallback=True)
    for persona_id, name in (
        ("persona-20260528-04688755", "Crypto-Alt-Hunter"),
        ("persona-20260528-5937dea1", "TW-Index-Arbitrage"),
        ("persona-20260528-597cbad2", "US-Macro-Hedger"),
    ):
        store.create_persona(
            persona_id=persona_id,
            name=name,
            actor_id="pantheon-dev-browser",
            created_at="2026-05-28T00:00:00Z",
            lifecycle_state="deployed",
            metadata={},
        )
    with _client_with_store(store) as client:
        response = client.get("/bff/management/persona-fleet?page_size=50", headers=HEADERS)

    assert response.status_code == 200, response.text
    rows = {item["persona_id"]: item for item in response.json()["data"]["items"]}
    crypto = rows["persona-20260528-04688755"]
    assert crypto["name"] == "Crypto-Alt-Hunter"
    assert crypto["owner"] == "pantheon-dev-browser"
    assert crypto["state"] == "deployed"
    assert crypto["capital_mode"] == "none"
    assert crypto["paper_ledger_id"] is None
    assert crypto["paper_ledger"] is None
    assert crypto["capital_pool_id"] is None
    assert crypto["deployment_stage"] == "none"
    assert crypto["runtime_id"] is None
    assert crypto["runtime_binding_id"] is None
    assert crypto["data_source_summary"]["state"] == "datasource_smoke_ok"
    assert crypto["data_source_summary"]["provider_count"] >= 1
    assert crypto["data_source_summary"]["provider_status_counts"]["datasource_smoke_ok"] >= 1
    assert crypto["current_work"] is None
    assert crypto["perf_delta"] is None
    assert crypto["performance_summary"]["source"] == "unavailable"
    assert crypto["performance_summary"]["pnl"] is None
    assert crypto["performance_summary"]["max_drawdown"] is None
    assert crypto["performance_summary"]["total_trades"] is None

    tw = rows["persona-20260528-5937dea1"]
    assert tw["state"] == "deployed"
    assert tw["capital_mode"] == "none"
    assert tw["paper_ledger_id"] is None
    assert tw["capital_pool_id"] is None
    assert tw["data_source_summary"]["state"] == "partial_readback"
    assert tw["data_source_summary"]["provider_status_counts"]["read_ok"] >= 1
    assert tw["research_summary"]["current_project_count"] >= 1
    assert tw["research_summary"]["stage"] == "management_review_linked"

    us = rows["persona-20260528-597cbad2"]
    assert us["state"] == "deployed"
    assert us["capital_mode"] == "none"
    assert us["paper_ledger_id"] is None
    assert us["current_work"] is None


def test_management_persona_fleet_prefers_declared_runtime_identity_over_market_default() -> None:
    persona_id = "persona-20260528-04688755"
    runtime_id = f"runtime-{persona_id}-paper"
    persona_binding_id = f"binding-{persona_id}-paper"
    pool_id = f"pool-{persona_id}-paper"
    store = _make_store(allow_local_snapshot_fallback=True)
    store.create_persona(
        persona_id=persona_id,
        name="Crypto-Alt-Hunter",
        actor_id="pantheon-dev-browser",
        created_at="2026-05-28T00:00:00Z",
        lifecycle_state="deployed",
        metadata={
            "capital_mode": "paper",
            "deployment_stage": "paper",
            "legacy_paper_capital_pool_id": pool_id,
            "runtime_id": runtime_id,
            "runtime_binding_id": persona_binding_id,
        },
    )
    runtime_record = store.create_runtime_binding(
        runtime_id=runtime_id,
        name="Crypto-Alt-Hunter paper runtime",
        persona_id=persona_id,
        binding_id=persona_binding_id,
        deployment_plan_id=f"paper-plan-{persona_id}",
        runtime_kind="paper",
        actor_id="pantheon-dev-browser",
        params={"capital_pool_id": pool_id},
        state="active",
    )
    runtime_record["persona_id"] = None
    store._save()

    with _client_with_store(store) as client:
        fleet_response = client.get(
            "/bff/management/persona-fleet?page_size=100",
            headers=HEADERS,
        )
        runtime_response = client.get("/bff/runtimes?page_size=200", headers=HEADERS)

    assert fleet_response.status_code == 200, fleet_response.text
    fleet_row = next(
        item
        for item in fleet_response.json()["data"]["items"]
        if item["persona_id"] == persona_id
    )
    assert fleet_row["legacy_paper_capital_pool_id"] == pool_id
    assert fleet_row["runtime_id"] == runtime_id
    assert fleet_row["runtime_binding_id"] == persona_binding_id

    assert runtime_response.status_code == 200, runtime_response.text
    runtime_row = next(
        item for item in runtime_response.json()["items"] if item["runtime_id"] == runtime_id
    )
    assert runtime_row["persona_id"] == persona_id
    assert runtime_row["persona_capital_binding_id"] == persona_binding_id


def test_real_paper_runtime_identity_drives_formal_persona_attribution_and_fleet(
    tmp_path: Path,
    monkeypatch,
) -> None:
    persona_a = "persona-paper-alpha"
    persona_b = "persona-paper-beta"
    runtime_a = "runtime-persona-paper-alpha-paper"
    runtime_b = "runtime-persona-paper-beta-paper"
    runtime_binding_a = "rb-paper-alpha"
    runtime_binding_b = "rb-paper-beta"
    capital_binding_a = "binding-persona-paper-alpha-paper"
    capital_binding_b = "binding-persona-paper-beta-paper"
    pool_a = "pool-persona-paper-alpha-paper"
    pool_b = "pool-persona-paper-beta-paper"
    observed_at = bff_main.utc_now()

    def write_store(name: str, payload: object) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    stores = {
        "PANTHEON_BFF_PERSONA_REGISTRY_STORE": write_store(
            "personas.json",
            {
                persona_a: {
                    "persona_id": persona_a,
                    "name": "Paper Alpha",
                    "lifecycle_state": "paper_running",
                    "status": "paper_running",
                    "created_at": "2026-07-13T00:00:00Z",
                    "metadata": {
                        "market_scope": ["US"],
                        "capital_mode": "paper",
                        "deployment_stage": "paper",
                        "runtimeId": runtime_a,
                        "runtimeBindingId": capital_binding_a,
                        "legacyPaperCapitalPoolId": pool_a,
                    },
                },
                persona_b: {
                    "persona_id": persona_b,
                    "name": "Paper Beta",
                    "lifecycle_state": "paper_running",
                    "status": "paper_running",
                    "created_at": "2026-07-13T00:01:00Z",
                    "metadata": {
                        "market_scope": ["US"],
                        "capital_mode": "paper",
                        "deployment_stage": "paper",
                        "runtime_id": runtime_b,
                        "runtime_binding_id": capital_binding_b,
                        "legacy_paper_capital_pool_id": pool_b,
                    },
                },
            },
        ),
            "PANTHEON_BFF_PERSONA_SESSION_STORE": write_store(
                "sessions.json",
                {
                    "session-paper-alpha": {
                        "session_id": "session-paper-alpha",
                        "persona_id": persona_a,
                        "runtime_id": runtime_a,
                        "runtime_binding_id": runtime_binding_a,
                        "status": "active",
                        "active": True,
                        "last_heartbeat_at": observed_at,
                    },
                    "session-paper-beta": {
                        "session_id": "session-paper-beta",
                        "persona_id": persona_b,
                        "runtime_id": runtime_b,
                        "runtime_binding_id": runtime_binding_b,
                        "status": "active",
                        "active": True,
                        "last_heartbeat_at": observed_at,
                    },
                },
            ),
        "PANTHEON_BFF_PERSONA_BINDING_STORE": write_store(
            "persona_capital_bindings.json",
            {
                capital_binding_a: {
                    "binding_id": capital_binding_a,
                    "persona_id": persona_a,
                    "capital_pool_id": pool_a,
                    "status": "active",
                },
                capital_binding_b: {
                    "binding_id": capital_binding_b,
                    "persona_id": persona_b,
                    "capital_pool_id": pool_b,
                    "status": "active",
                },
            },
        ),
        "PANTHEON_BFF_RUNTIME_BINDING_STORE": write_store(
            "runtime_bindings.json",
            {
                runtime_binding_a: {
                    "binding_id": runtime_binding_a,
                    "runtime_id": runtime_a,
                    "persona_id": "persona-us-equity",
                    "persona_capital_binding_id": capital_binding_a,
                    "capital_pool_id": pool_a,
                    "plan_id": "plan-paper-alpha",
                    "deployment_mode": "paper",
                    "status": "active",
                },
                runtime_binding_b: {
                    "binding_id": runtime_binding_b,
                    "runtime_id": runtime_b,
                    "persona_capital_binding_id": capital_binding_b,
                    "capital_pool_id": pool_b,
                    "plan_id": "plan-paper-beta",
                    "deployment_mode": "paper",
                    "status": "active",
                },
            },
        ),
        "PANTHEON_BFF_DEPLOYMENT_PLAN_STORE": write_store(
            "deployment_plans.json",
            {
                "plan-paper-alpha": {
                    "plan_id": "plan-paper-alpha",
                    "binding_ids": [capital_binding_a],
                    "capital_pool_id": pool_a,
                    "strategy_id": "strategy-paper-alpha",
                    "target_stage": "paper",
                    "status": "executed",
                },
                "plan-paper-beta": {
                    "plan_id": "plan-paper-beta",
                    "binding_ids": [capital_binding_b],
                    "capital_pool_id": pool_b,
                    "strategy_id": "strategy-paper-beta",
                    "target_stage": "paper",
                    "status": "executed",
                },
            },
        ),
        "PANTHEON_BFF_TELEMETRY_SUMMARY_STORE": write_store(
            "telemetry_summaries.json",
            {
                runtime_a: {
                    "runtime_id": runtime_a,
                    "projection_source": "telemetry_ingest",
                    "collected_at": "2026-07-13T00:10:00Z",
                    "pnl": 0.12,
                    "drawdown": 0.02,
                    "fill_rate": 0.99,
                    "avg_slippage_bps": 1.0,
                    "sharpe_ratio": 1.8,
                    "total_trades": 17,
                },
                runtime_b: {
                    "runtime_id": runtime_b,
                    "projection_source": "telemetry_ingest",
                    "collected_at": "2026-07-13T00:11:00Z",
                    "summary": {
                        "total_pnl": -0.08,
                        "max_drawdown": 0.08,
                        "fill_rate": 0.80,
                        "avg_slippage_bps": 5.0,
                        "sharpe": 0.3,
                        "total_trades": 0,
                    },
                },
            },
        ),
    }
    for env_name in (
        "PANTHEON_PERSONA_DATA_DIR",
        "PANTHEON_GOVERNANCE_DATA_DIR",
        "PANTHEON_RUNTIME_DATA_DIR",
        "PANTHEON_PERSONA_SERVICE_URL",
        "PANTHEON_RUNTIME_MANAGER_URL",
        "PANTHEON_TELEMETRY_API_URL",
        "PANTHEON_TELEMETRY_URL",
    ):
        monkeypatch.delenv(env_name, raising=False)
    for env_name, path in stores.items():
        monkeypatch.setenv(env_name, str(path))

    store = _make_store(allow_local_snapshot_fallback=False)
    runtimes = {runtime["runtime_id"]: runtime for runtime in store.list_runtime_bindings()}
    assert runtimes[runtime_a]["persona_id"] == persona_a
    assert runtimes[runtime_b]["persona_id"] == persona_b
    assert runtimes[runtime_a]["runtime_binding_id"] == runtime_binding_a
    assert runtimes[runtime_b]["runtime_binding_id"] == runtime_binding_b
    assert runtimes[runtime_a]["persona_capital_binding_id"] == capital_binding_a
    assert runtimes[runtime_b]["persona_capital_binding_id"] == capital_binding_b

    with _client_with_store(store) as client:
        attribution_response = client.get(
            "/bff/management/performance-attribution/by-persona?page_size=100",
            headers=HEADERS,
        )
        rankings_response = client.get(
            "/bff/management/persona-league/rankings?criteria=overall&limit=100",
            headers=HEADERS,
        )
        fleet_response = client.get(
            "/bff/management/persona-fleet?page_size=100",
            headers=HEADERS,
        )

    assert attribution_response.status_code == 200, attribution_response.text
    attribution_rows = {
        item["dimension_key"]: item
        for item in attribution_response.json()["data"]["items"]
    }
    assert attribution_rows[persona_a]["data_confidence"] == "formal"
    assert attribution_rows[persona_b]["data_confidence"] == "formal"
    assert attribution_rows[persona_a]["source_refs"]["runtime_ids"] == [runtime_a]
    assert attribution_rows[persona_b]["source_refs"]["runtime_ids"] == [runtime_b]
    assert attribution_rows[persona_a]["metrics"]["total_pnl"] == 0.12
    assert attribution_rows[persona_b]["metrics"]["total_pnl"] == -0.08
    assert attribution_rows[persona_a]["metrics"]["total_trades"] == 17
    assert attribution_rows[persona_b]["metrics"]["total_trades"] == 0
    assert all(
        runtime_a not in item["source_refs"]["runtime_ids"]
        and runtime_b not in item["source_refs"]["runtime_ids"]
        for item in attribution_rows.values()
        if item["dimension_key"] == "unassigned"
    )

    assert rankings_response.status_code == 200, rankings_response.text
    ranking_rows = {
        item["persona_id"]: item
        for item in rankings_response.json()["data"]["items"][0]["items"]
    }
    assert ranking_rows[persona_a]["eligible"] is True
    assert ranking_rows[persona_b]["eligible"] is True
    assert ranking_rows[persona_a]["exclusion_reason"] is None
    assert ranking_rows[persona_b]["exclusion_reason"] is None
    assert ranking_rows[persona_a]["evidence_coverage"] > 0
    assert ranking_rows[persona_b]["evidence_coverage"] > 0
    assert ranking_rows[persona_a]["source_confidence"] == "formal"
    assert ranking_rows[persona_b]["source_confidence"] == "formal"
    assert ranking_rows[persona_a]["metrics"]["runtime_ids"] == [runtime_a]
    assert ranking_rows[persona_b]["metrics"]["runtime_ids"] == [runtime_b]
    assert ranking_rows[persona_a]["metrics"]["total_trades"] == 17
    assert ranking_rows[persona_b]["metrics"]["total_trades"] == 0
    assert ranking_rows[persona_a]["score"] > ranking_rows[persona_b]["score"]

    assert fleet_response.status_code == 200, fleet_response.text
    fleet_rows = {
        item["persona_id"]: item
        for item in fleet_response.json()["data"]["items"]
    }
    assert fleet_rows[persona_a]["runtime_id"] == runtime_a
    assert fleet_rows[persona_b]["runtime_id"] == runtime_b
    assert fleet_rows[persona_a]["runtime_binding_id"] == runtime_binding_a
    assert fleet_rows[persona_b]["runtime_binding_id"] == runtime_binding_b
    assert fleet_rows[persona_a]["performance_summary"]["source"] == "telemetry_summaries"
    assert fleet_rows[persona_b]["performance_summary"]["source"] == "telemetry_summaries"
    assert fleet_rows[persona_a]["performance_summary"]["pnl"] == 0.12
    assert fleet_rows[persona_b]["performance_summary"]["pnl"] == -0.08
    assert fleet_rows[persona_a]["performance_summary"]["total_trades"] == 17
    assert fleet_rows[persona_b]["performance_summary"]["total_trades"] == 0
    seed_values = {24560.0, 426000.0, 48000.0, 0.057, 0.071, 0.064}
    for persona_id in (persona_a, persona_b):
        performance = fleet_rows[persona_id]["performance_summary"]
        assert performance["pnl"] not in seed_values
        assert performance["max_drawdown"] not in seed_values


def test_runtime_registry_identity_reconciliation_is_unique_and_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    persona_store = tmp_path / "personas.json"
    runtime_store = tmp_path / "runtime_bindings.json"
    binding_store = tmp_path / "persona_capital_bindings.json"
    persona_store.write_text(
        json.dumps(
            {
                "persona-candidate-a": {
                    "persona_id": "persona-candidate-a",
                    "metadata": {"runtimeId": "runtime-ambiguous"},
                },
                "persona-candidate-b": {
                    "persona_id": "persona-candidate-b",
                    "metadata": {"runtime_id": "runtime-ambiguous"},
                },
                "persona-unique": {
                    "persona_id": "persona-unique",
                    "metadata": {"runtimeId": "runtime-unique"},
                },
            }
        ),
        encoding="utf-8",
    )
    runtime_store.write_text(
        json.dumps(
            {
                "rb-ambiguous": {
                    "binding_id": "rb-ambiguous",
                    "runtime_id": "runtime-ambiguous",
                    "persona_id": "persona-us-equity",
                    "deployment_mode": "paper",
                },
                "rb-unique": {
                    "binding_id": "rb-unique",
                    "runtime_id": "runtime-unique",
                    "persona_id": "persona-us-equity",
                    "deployment_mode": "paper",
                },
            }
        ),
        encoding="utf-8",
    )
    binding_store.write_text("{}", encoding="utf-8")
    for env_name in (
        "PANTHEON_PERSONA_DATA_DIR",
        "PANTHEON_GOVERNANCE_DATA_DIR",
        "PANTHEON_RUNTIME_DATA_DIR",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("PANTHEON_BFF_PERSONA_REGISTRY_STORE", str(persona_store))
    monkeypatch.setenv("PANTHEON_BFF_RUNTIME_BINDING_STORE", str(runtime_store))
    monkeypatch.setenv("PANTHEON_BFF_PERSONA_BINDING_STORE", str(binding_store))

    store = _make_store(allow_local_snapshot_fallback=False)
    runtimes = {runtime["runtime_id"]: runtime for runtime in store.list_runtime_bindings()}

    assert runtimes["runtime-unique"]["persona_id"] == "persona-unique"
    assert runtimes["runtime-ambiguous"]["persona_id"] is None


def test_pm12_authoritative_runtime_id_avoids_stale_alias_probe_and_reuses_summary(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def telemetry_summary(runtime_id: str):
        calls.append(runtime_id)
        return {
            "runtime_id": runtime_id,
            "pnl": 0.0,
            "drawdown": 0.0,
            "total_trades": 0,
            "collected_at": "2026-07-13T00:00:00Z",
        }

    monkeypatch.setattr(bff_main.read_store, "get_telemetry_summary", telemetry_summary)
    row = {
        "binding_summary": {"runtime_ids": ["runtime-authoritative"]},
        "session_summary": {
            "runtime_ids": [],
            "runtime_binding_ids": ["rb-stale-session-alias"],
        },
    }

    metrics = bff_main._pm12_persona_telemetry_metrics(row)

    assert calls == ["runtime-authoritative"]
    assert metrics["runtime_ids"] == ["runtime-authoritative"]
    assert metrics["pnl"] == 0.0
    assert metrics["drawdown"] == 0.0
    assert metrics["total_trades"] == 0


def test_persona_league_filters_and_requires_governance_for_rank_actions() -> None:
    with _fleet_client() as client:
        all_rows = client.get("/bff/persona-league", headers=HEADERS)
        tw_rows = client.get("/bff/persona-league?market_scope=TW", headers=HEADERS)
        detail = client.get("/bff/persona-league/persona-crypto", headers=HEADERS)

    assert all_rows.status_code == 200, all_rows.text
    rows = all_rows.json()["data"]["items"]
    assert [row["persona_id"] for row in rows[:3]] == [
        "persona-crypto",
        "persona-us-equity",
        "persona-tw-equity",
    ]
    assert all(row["governance_required"] is True for row in rows[:3])

    assert tw_rows.status_code == 200, tw_rows.text
    assert [row["persona_id"] for row in tw_rows.json()["data"]["items"]] == ["persona-tw-equity"]

    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["recommendation"] is None


def test_management_persona_fleet_composes_personas_ooda_capital_runtime_and_human_gate() -> None:
    with _fleet_client() as client:
        response = client.get("/bff/management/persona-fleet", headers=HEADERS)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert set(data) == {"items", "summary"}
    assert "persona_fleet" not in data
    assert "persona_league" not in data
    assert "capital_pools" not in data
    assert "runtime_bindings" not in data
    assert "human_inbox" not in data
    fleet_ids = {item["persona_id"] for item in data["items"]}
    assert data["summary"]["human_inbox_summary"]["pending_count"] == 0
    assert data["summary"]["by_capital_mode"]["paper"] >= 3
    assert data["summary"]["by_lifecycle_state"]["paper_running"] >= 2
    meta_surfaces = response.json()["meta"]["surfaces"]
    assert meta_surfaces["persona_league"]["status"] in {"ok", "degraded"}
    assert meta_surfaces["persona_league"]["source"] != "missing"
    assert meta_surfaces["ooda_control_room_status"]["status"] in {"ok", "degraded"}
    assert meta_surfaces["ooda_control_room_status"]["source"] != "missing"
    assert response.json()["meta"]["related"]["human_inbox"]["href"] == "/bff/management/human-inbox"
    assert data["summary"]["execution_boundary"] == {
        "approved_artifacts_only": True,
        "live_capital_side_effects": False,
        "human_gate_required_for_capital_changes": True,
    }


def test_management_persona_fleet_returns_slim_ui_safe_rows() -> None:
    with _fleet_client() as client:
        response = client.get("/bff/management/persona-fleet", headers=HEADERS)

    assert response.status_code == 200, response.text
    assert len(response.content) < PERSONA_FLEET_DEFAULT_TARGET_BYTES
    assert len(response.content) < PERSONA_FLEET_DEFAULT_HARD_LIMIT_BYTES
    payload = response.json()
    data = payload["data"]
    assert "items" not in payload
    assert "summary" not in payload
    assert set(data) == {"items", "summary"}
    assert "persona_fleet" not in data

    rows = {item["persona_id"]: item for item in data["items"]}
    assert set(MARKET_PERSONAS.values()).issubset(rows)
    for row in rows.values():
        assert not PERSONA_FLEET_FORBIDDEN_LIST_KEYS.intersection(row)
        assert len(json.dumps(row).encode("utf-8")) < PERSONA_FLEET_ROW_HARD_LIMIT_BYTES

    tw = rows["persona-tw-equity"]
    assert tw["owner"] == "pathreon-management"
    assert tw["human_needed"] is False
    assert tw["state"] == "needs_human_approval"
    assert tw["capital_mode"] == "paper"
    assert tw["paper_ledger_id"] == "paper-ledger-persona-tw-equity"
    assert tw["paper_ledger"]["is_isolated"] is True
    assert tw["legacy_paper_capital_pool_id"] == "pool-tw-equity-paper"
    assert tw["capital_pool_id"] is None
    assert tw["capital_pool"] is None
    assert tw["runtime_id"] == "runtime-tw-equity-paper"
    assert tw["runtime_binding_id"] == "runtime-tw-equity-paper"
    assert tw["runtime_binding"]["deployment_stage"] == "paper"
    assert tw["review_id"] == "approval-tw-equity-paper"
    assert tw["review_type"] is None
    assert tw["inbox_id"] is None
    assert tw["review"]["requires_human_gate"] is False
    assert tw["league_rank"] == 3
    assert tw["league_score"] == 82.925
    assert tw["rank"]["league_rank"] == 3
    assert tw["rank"]["league_score"] == 82.925
    assert tw["rank"]["basis"] == "quarterly_ranking"
    assert tw["current_work"] is None
    assert tw["data_source_summary"]["state"] == "partial_readback"
    assert tw["data_source_summary"]["provider_count"] == 5
    assert tw["data_source_summary"]["provider_status_counts"]["read_ok"] == 1
    assert tw["data_source_summary"]["provider_status_counts"]["read_unavailable"] == 3
    assert [source["provider_key"] for source in tw["data_sources"]] == [
        "shioaji",
        "twse",
        "tpex",
        "mops",
        "finmind",
    ]
    assert tw["data_sources"][0] == {
        "provider_key": "shioaji",
        "provider": "Shioaji quote",
        "market": "TW",
        "source_class": "broker_execution",
        "status": "read_ok",
        "order_capable_provider": True,
        "read_only": True,
        "order_side_effects_allowed": False,
        "capital_side_effects_allowed": False,
    }
    assert "evidence_ref" not in tw["data_sources"][0]
    assert tw["research_summary"]["stage"] == "management_review_linked"
    assert tw["research_summary"]["framework"] == "qlib"
    assert tw["research_summary"]["artifact_id"] == "qlib-tw-cross-sectional-alpha-model-draft-v1"
    assert tw["research_summary"]["registry_admission_status"] == "pending_upstream_task"
    assert tw["research_summary"]["can_deploy"] is False
    assert tw["research_summary"]["current_project_count"] >= 1
    assert tw["research_summary"]["evidence_ref_count"] >= 1
    for duplicate_key in (
        "personaId",
        "personaName",
        "humanNeeded",
        "dataSourceStatus",
        "data_source_status",
        "dataSources",
        "dataSourceRefs",
        "data_source_refs",
        "requiredDataSources",
        "required_data_sources",
        "sourceHealthBindings",
        "source_health_bindings",
        "researchStatus",
        "research_status",
        "currentResearchProjects",
        "current_research_projects",
        "researchRefs",
        "research_refs",
    ):
        assert duplicate_key not in tw


def test_management_persona_fleet_keeps_market_personas_with_live_dev_overlay_only() -> None:
    store = _make_store(allow_local_snapshot_fallback=False)
    store.create_persona(
        persona_id="persona-dev-probe",
        name="dev-probe",
        actor_id="pantheon-dev-browser",
        lifecycle_state="paper",
        created_at="2026-06-03T08:27:44Z",
        metadata={"owner": "pantheon-dev-browser"},
    )
    with _client_with_store(store) as client:
        response = client.get("/bff/management/persona-fleet", headers=HEADERS)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    rows = {item["persona_id"]: item for item in data["items"]}
    assert "persona-dev-probe" in rows
    assert not any(market_id in rows for market_id in MARKET_PERSONAS.values())
    assert data["summary"]["total_personas"] == 1
    assert data["summary"]["canonical_total"] == 1
    assert data["summary"]["catalog_default_total"] > 0
    assert "capital_pools" not in data
    assert "persona_league" not in data


def test_unadmitted_catalog_defaults_do_not_fabricate_ghost_fleet_rows_or_detail() -> None:
    store = _make_store(allow_local_snapshot_fallback=False)
    with _client_with_store(store) as client:
        fleet = client.get("/bff/management/persona-fleet", headers=HEADERS)
        detail = client.get("/bff/personas/persona-crypto", headers=HEADERS)
        unknown = client.get("/bff/personas/persona-not-in-catalog", headers=HEADERS)

    assert fleet.status_code == 200, fleet.text
    fleet_ids = {item["persona_id"] for item in fleet.json()["data"]["items"]}
    assert "persona-crypto" not in fleet_ids
    summary = fleet.json()["data"]["summary"]
    assert summary["canonical_total"] == 0
    assert summary["catalog_default_total"] > 0

    assert detail.status_code == 404, detail.text
    assert detail.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_tw_qlib_research_experiment_drilldown_is_governed_default_not_seed() -> None:
    store = _make_store(allow_local_snapshot_fallback=False)
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
    surface = payload["meta"]["surfaces"]["research_experiment_detail"]
    assert surface["status"] == "ok"
    assert surface["source"] == "composed_market_persona_defaults"

    assert listing.status_code == 200, listing.text
    list_payload = listing.json()
    ids = {item["experiment_id"] for item in list_payload["items"]}
    surface_list = list_payload["meta"]["surfaces"]["research_experiments"]
    assert surface_list["status"] == "ok"
    assert surface_list["source"] == "composed_market_persona_defaults"


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


def test_overlay_live_finmind_health_flips_to_read_ok(monkeypatch):
    dss = {
        "state": "partial_readback",
        "provider_statuses": {"finmind": "read_unavailable", "shioaji": "read_ok"},
    }
    sources = [
        {"provider_key": "finmind", "status": "read_unavailable"},
        {"provider_key": "shioaji", "status": "read_ok"},
    ]
    monkeypatch.setattr(
        bff_main,
        "_live_source_health_by_connector",
        lambda: {"tw-finmind-datasets": {"status": "ok", "last_success_at": "2026-06-27T05:00:00Z", "row_count_last_run": 8}},
    )
    out_dss, out_sources = bff_main._overlay_live_finmind_health(dss, sources)
    assert out_dss["provider_statuses"]["finmind"] == "read_ok"
    assert out_dss["state"] == "live_partial_readback"
    assert out_dss["finmind_live_row_count_last_run"] == 8
    by_key = {s["provider_key"]: s for s in out_sources}
    assert by_key["finmind"]["status"] == "read_ok"


def test_overlay_live_finmind_health_noop_when_unavailable(monkeypatch):
    dss = {"state": "partial_readback", "provider_statuses": {"finmind": "read_unavailable"}}
    monkeypatch.setattr(bff_main, "_live_source_health_by_connector", lambda: {})
    out_dss, _ = bff_main._overlay_live_finmind_health(
        dss, [{"provider_key": "finmind", "status": "read_unavailable"}]
    )
    assert out_dss["provider_statuses"]["finmind"] == "read_unavailable"
    assert out_dss["state"] == "partial_readback"


def test_source_health_truth_overlay_projects_connector_panel_fields(monkeypatch):
    dss = {
        "state": "partial_readback",
        "provider_statuses": {
            "finmind": "read_unavailable",
            "twse": "read_unavailable",
            "tpex": "read_unavailable",
        },
    }
    sources = [
        {"provider_key": "finmind", "status": "read_unavailable"},
        {"provider_key": "twse", "status": "read_unavailable"},
        {"provider_key": "tpex", "status": "read_unavailable"},
    ]
    required_sources = [
        {
            "dataset": "tw_broker_top",
            "market": "TW",
            "cadence": "daily",
            "source_class": "live_push",
            "connector_candidates": ["tw-finmind-broker-daily-report"],
        }
    ]
    monkeypatch.setattr(
        bff_main,
        "_source_ingest_truth_by_connector",
        lambda: {
            "tw-finmind-broker-daily-report": {
                "health": {
                    "source_id": "tw-finmind-broker-daily-report",
                    "status": "failed",
                    "last_success_at": "2026-06-27T05:00:00Z",
                    "last_failure_at": "2026-06-27T06:00:00Z",
                    "latest_watermark": "2026-06-26",
                    "row_count_last_run": 0,
                    "metadata": {"source_error": "FinMind quota exhausted"},
                },
                "connector": {
                    "connector_id": "tw-finmind-broker-daily-report",
                    "status": "enabled",
                    "schedule": {
                        "configured": True,
                        "enabled": True,
                        "interval_seconds": 86400,
                    },
                    "freshness": {
                        "status": "degraded",
                        "latest_run": {
                            "ingest_run_id": "run-finmind-001",
                            "status": "failed",
                            "finished_at": "2026-06-27T06:00:00Z",
                        },
                    },
                    "health_metrics": {"source_error": "FinMind quota exhausted"},
                },
            },
            "tw-twse-tpex-official-market": {
                "health": {
                    "source_id": "tw-twse-tpex-official-market",
                    "status": "ok",
                    "last_success_at": "2026-06-27T04:30:00Z",
                    "row_count_last_run": 42,
                    "metadata": {},
                },
                "connector": {
                    "connector_id": "tw-twse-tpex-official-market",
                    "status": "enabled",
                    "schedule": {"configured": True, "enabled": True, "interval_seconds": 86400},
                    "freshness": {"status": "fresh", "last_success_at": "2026-06-27T04:30:00Z"},
                    "health_metrics": {},
                },
            },
        },
    )

    out_dss, out_sources, bindings = bff_main._overlay_source_health_truth(
        dss,
        sources,
        required_data_sources=required_sources,
    )

    assert out_dss["source_health_source"] == "source_ingest"
    assert out_dss["live_ingestion_enabled"] is True
    assert out_dss["provider_statuses"]["finmind"] == "source_health_failed"
    assert out_dss["provider_statuses"]["twse"] == "read_ok"
    by_provider = {source["provider_key"]: source for source in out_sources}
    finmind = by_provider["finmind"]
    assert finmind["health_source"] == "source_ingest"
    assert finmind["connectorSchedule"]["enabled"] is True
    assert finmind["lastFetchAt"] == "2026-06-27T06:00:00Z"
    assert finmind["lastPushAt"] == "2026-06-27T05:00:00Z"
    assert finmind["failureReason"] == "FinMind quota exhausted"
    assert bindings[0]["source_class"] == "live_push"
    assert bindings[0]["selectedConnectorId"] == "tw-finmind-broker-daily-report"
    assert bindings[0]["failureReason"] == "FinMind quota exhausted"


def test_overlay_preserves_credential_unavailable_when_health_degraded(monkeypatch):
    """polygon/alphavantage must stay credential_unavailable when source-ingest
    reports degraded health (missing key).  The only valid upgrade path is
    health.status=ok.  Regression probe for SRCLIVE-002 review issue (1)."""
    dss = {
        "state": "partial_readback",
        "provider_statuses": {
            "polygon": "credential_unavailable",
            "alphavantage": "credential_unavailable",
        },
    }
    sources = [
        {
            "provider_key": "polygon",
            "status": "credential_unavailable",
            "reason": "API key not configured; set env://POLYGON_API_KEY",
            "secret_ref": "env://POLYGON_API_KEY",
        },
        {
            "provider_key": "alphavantage",
            "status": "credential_unavailable",
            "reason": "API key not configured; set env://ALPHA_VANTAGE_API_KEY",
            "secret_ref": "env://ALPHA_VANTAGE_API_KEY",
        },
    ]
    monkeypatch.setattr(
        bff_main,
        "_source_ingest_truth_by_connector",
        lambda: {
            "us-polygon-daily-ohlcv": {
                "health": {
                    "source_id": "us-polygon-daily-ohlcv",
                    "status": "degraded",
                    "metadata": {"credential_status": "credential_unavailable"},
                },
                "connector": {
                    "connector_id": "us-polygon-daily-ohlcv",
                    "status": "enabled",
                    "schedule": {"configured": True, "enabled": True, "interval_seconds": 86400},
                    "freshness": {"status": "degraded"},
                    "health_metrics": {},
                },
            },
            "us-alpha-vantage-daily-ohlcv": {
                "health": {
                    "source_id": "us-alpha-vantage-daily-ohlcv",
                    "status": "degraded",
                    "metadata": {"credential_status": "credential_unavailable"},
                },
                "connector": {
                    "connector_id": "us-alpha-vantage-daily-ohlcv",
                    "status": "enabled",
                    "schedule": {"configured": True, "enabled": True, "interval_seconds": 86400},
                    "freshness": {"status": "degraded"},
                    "health_metrics": {},
                },
            },
        },
    )

    out_dss, out_sources, _bindings = bff_main._overlay_source_health_truth(dss, sources)

    by_provider = {s["provider_key"]: s for s in out_sources}

    polygon = by_provider["polygon"]
    assert polygon["status"] == "credential_unavailable", (
        "polygon must not be projected as source_health_degraded when credential is missing"
    )
    assert polygon.get("secret_ref") == "env://POLYGON_API_KEY"
    assert "POLYGON_API_KEY" in (polygon.get("reason") or "")

    alphavantage = by_provider["alphavantage"]
    assert alphavantage["status"] == "credential_unavailable", (
        "alphavantage must not be projected as source_health_degraded when credential is missing"
    )
    assert alphavantage.get("secret_ref") == "env://ALPHA_VANTAGE_API_KEY"
    assert "ALPHA_VANTAGE_API_KEY" in (alphavantage.get("reason") or "")

    assert out_dss["provider_statuses"]["polygon"] == "credential_unavailable"
    assert out_dss["provider_statuses"]["alphavantage"] == "credential_unavailable"


def test_overlay_upgrades_credential_unavailable_when_health_ok(monkeypatch):
    """When source-ingest confirms health.status=ok (key is now present and working),
    credential_unavailable must be upgraded to read_ok."""
    dss = {
        "state": "partial_readback",
        "provider_statuses": {"polygon": "credential_unavailable"},
    }
    sources = [
        {
            "provider_key": "polygon",
            "status": "credential_unavailable",
            "reason": "API key not configured; set env://POLYGON_API_KEY",
            "secret_ref": "env://POLYGON_API_KEY",
        },
    ]
    monkeypatch.setattr(
        bff_main,
        "_source_ingest_truth_by_connector",
        lambda: {
            "us-polygon-daily-ohlcv": {
                "health": {
                    "source_id": "us-polygon-daily-ohlcv",
                    "status": "ok",
                    "last_success_at": "2026-06-28T01:00:00Z",
                    "row_count_last_run": 500,
                    "metadata": {},
                },
                "connector": {
                    "connector_id": "us-polygon-daily-ohlcv",
                    "status": "enabled",
                    "schedule": {"configured": True, "enabled": True, "interval_seconds": 86400},
                    "freshness": {"status": "fresh", "last_success_at": "2026-06-28T01:00:00Z"},
                    "health_metrics": {},
                },
            },
        },
    )

    out_dss, out_sources, _bindings = bff_main._overlay_source_health_truth(dss, sources)

    by_provider = {s["provider_key"]: s for s in out_sources}
    polygon = by_provider["polygon"]
    assert polygon["status"] == "read_ok", (
        "polygon must be read_ok when source-ingest confirms health.status=ok"
    )
    assert out_dss["provider_statuses"]["polygon"] == "read_ok"


def test_source_health_truth_overlay_maps_stooq_and_preserves_fred_key_gate(monkeypatch):
    dss = {
        "state": "partial_readback",
        "provider_statuses": {
            "stooq": "read_unavailable",
            "fred": "credential_unavailable",
        },
    }
    sources = [
        {"provider_key": "stooq", "status": "read_unavailable"},
        {
            "provider_key": "fred",
            "status": "credential_unavailable",
            "reason": "FRED_API_KEY is not configured",
            "secret_ref": "env://FRED_API_KEY",
        },
    ]
    monkeypatch.setattr(
        bff_main,
        "_source_ingest_truth_by_connector",
        lambda: {
            "us-stooq-daily-ohlcv": {
                "health": {
                    "source_id": "us-stooq-daily-ohlcv",
                    "status": "ok",
                    "last_success_at": "2026-06-28T01:00:00Z",
                    "row_count_last_run": 3,
                    "metadata": {"provider": "Stooq"},
                },
                "connector": {
                    "connector_id": "us-stooq-daily-ohlcv",
                    "status": "enabled",
                    "schedule": {"configured": True, "enabled": True, "interval_seconds": 86400},
                    "freshness": {"status": "fresh", "last_success_at": "2026-06-28T01:00:00Z"},
                    "health_metrics": {},
                },
            },
            "us-fred-macro": {
                "health": {
                    "source_id": "us-fred-macro",
                    "status": "degraded",
                    "metadata": {"credential_status": "credential_unavailable"},
                },
                "connector": {
                    "connector_id": "us-fred-macro",
                    "status": "enabled",
                    "schedule": {"configured": True, "enabled": True, "interval_seconds": 86400},
                    "freshness": {"status": "degraded"},
                    "health_metrics": {},
                },
            },
        },
    )

    out_dss, out_sources, _bindings = bff_main._overlay_source_health_truth(dss, sources)

    by_provider = {s["provider_key"]: s for s in out_sources}
    assert by_provider["stooq"]["status"] == "read_ok"
    assert by_provider["stooq"]["connectorId"] == "us-stooq-daily-ohlcv"
    assert by_provider["fred"]["status"] == "credential_unavailable"
    assert by_provider["fred"]["secret_ref"] == "env://FRED_API_KEY"
    assert out_dss["provider_statuses"]["stooq"] == "read_ok"
    assert out_dss["provider_statuses"]["fred"] == "credential_unavailable"


def test_source_health_truth_overlay_maps_coingecko_provider_to_crypto_connector(monkeypatch):
    dss = {"state": "datasource_smoke_ok", "provider_statuses": {"coingecko": "read_unavailable"}}
    sources = [{"provider_key": "coingecko", "status": "read_unavailable"}]
    monkeypatch.setattr(
        bff_main,
        "_source_ingest_truth_by_connector",
        lambda: {
            "crypto-coingecko-spot": {
                "health": {
                    "source_id": "crypto-coingecko-spot",
                    "status": "ok",
                    "last_success_at": "2026-06-27T05:00:00Z",
                    "latest_watermark": "2026-06-27",
                    "row_count_last_run": 2,
                    "metadata": {"provider": "CoinGecko", "market": "CRYPTO"},
                },
                "connector": {
                    "connector_id": "crypto-coingecko-spot",
                    "status": "enabled",
                    "schedule": {"configured": True, "enabled": True, "interval_seconds": 86400},
                    "freshness": {"status": "fresh", "last_success_at": "2026-06-27T05:00:00Z"},
                    "health_metrics": {},
                },
            }
        },
    )

    out_dss, out_sources, _ = bff_main._overlay_source_health_truth(dss, sources)

    assert out_dss["provider_statuses"]["coingecko"] == "read_ok"
    assert out_dss["live_source_connector_ids"] == ["crypto-coingecko-spot"]
    assert out_sources[0]["connectorId"] == "crypto-coingecko-spot"
    assert out_sources[0]["sourceHealthAvailable"] is True


def test_unassigned_runtime_telemetry_isolation_and_no_seed_leaks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    persona_custom = "persona-custom-empty"
    runtime_unassigned = "runtime-devloop-unassigned"
    runtime_binding_unassigned = "rb-devloop-unassigned"

    def write_store(name: str, payload: object) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    stores = {
        "PANTHEON_BFF_PERSONA_REGISTRY_STORE": write_store(
            "personas.json",
            {
                persona_custom: {
                    "persona_id": persona_custom,
                    "name": "Custom Empty US",
                    "lifecycle_state": "deployed",
                    "status": "deployed",
                    "created_at": "2026-07-13T00:00:00Z",
                    "metadata": {
                        "market_scope": ["US"],
                        "capital_mode": "paper",
                        "deployment_stage": "paper",
                    },
                },
            },
        ),
        "PANTHEON_BFF_PERSONA_SESSION_STORE": write_store("sessions.json", {}),
        "PANTHEON_BFF_PERSONA_BINDING_STORE": write_store("persona_capital_bindings.json", {}),
        "PANTHEON_BFF_RUNTIME_BINDING_STORE": write_store(
            "runtime_bindings.json",
            {
                runtime_binding_unassigned: {
                    "binding_id": runtime_binding_unassigned,
                    "runtime_id": runtime_unassigned,
                    "persona_id": "persona-us-equity",  # stale seed persona_id
                    "deployment_mode": "paper",
                    "status": "active",
                },
            },
        ),
        "PANTHEON_BFF_DEPLOYMENT_PLAN_STORE": write_store("deployment_plans.json", {}),
        "PANTHEON_BFF_TELEMETRY_SUMMARY_STORE": write_store(
            "telemetry_summaries.json",
            {
                runtime_unassigned: {
                    "runtime_id": runtime_unassigned,
                    "projection_source": "telemetry_ingest",
                    "collected_at": "2026-07-13T00:10:00Z",
                    "pnl": 0.55,
                    "drawdown": 0.05,
                    "fill_rate": 0.95,
                    "avg_slippage_bps": 2.0,
                    "sharpe_ratio": 2.0,
                    "total_trades": 6841,
                },
            },
        ),
    }
    for env_name in (
        "PANTHEON_PERSONA_DATA_DIR",
        "PANTHEON_GOVERNANCE_DATA_DIR",
        "PANTHEON_RUNTIME_DATA_DIR",
        "PANTHEON_PERSONA_SERVICE_URL",
        "PANTHEON_RUNTIME_MANAGER_URL",
        "PANTHEON_TELEMETRY_API_URL",
        "PANTHEON_TELEMETRY_URL",
    ):
        monkeypatch.delenv(env_name, raising=False)
    for env_name, path in stores.items():
        monkeypatch.setenv(env_name, str(path))

    store = _make_store(allow_local_snapshot_fallback=False)
    runtimes = {runtime["runtime_id"]: runtime for runtime in store.list_runtime_bindings()}
    # The unassigned devloop runtime has no canonical binding or unique declaration, so it must reconcile to None.
    assert runtimes[runtime_unassigned]["persona_id"] is None

    with _client_with_store(store) as client:
        attribution_response = client.get(
            "/bff/management/performance-attribution/by-persona?page_size=100",
            headers=HEADERS,
        )
        fleet_response = client.get(
            "/bff/management/persona-fleet?page_size=100",
            headers=HEADERS,
        )

    assert attribution_response.status_code == 200, attribution_response.text
    attribution_rows = {
        item["dimension_key"]: item
        for item in attribution_response.json()["data"]["items"]
    }
    # Unassigned telemetry remains categorized as unassigned
    assert attribution_rows["unassigned"]["metrics"]["total_trades"] == 6841
    # Custom empty persona does not get any telemetry
    assert persona_custom not in attribution_rows

    assert fleet_response.status_code == 200, fleet_response.text
    fleet_rows = {
        item["persona_id"]: item
        for item in fleet_response.json()["data"]["items"]
    }
    # Custom empty persona has no telemetry, so performance fields must be null (not faked from same-market seed)
    custom_perf = fleet_rows[persona_custom]["performance_summary"]
    assert custom_perf["source"] == "unavailable"
    assert custom_perf["pnl"] is None
    assert custom_perf["max_drawdown"] is None
    assert custom_perf["total_trades"] is None


def test_canonical_binding_precedence_and_mixed_topology(
    tmp_path: Path,
    monkeypatch,
) -> None:
    persona_test = "persona-test-precedence"
    persona_missing = "persona-test-missing"
    rt_stale = "rt-stale"
    binding_stale = "binding-stale"
    rt_assigned = "rt-assigned"
    binding_canonical = "binding-canonical"
    rt_devloop = "rt-devloop"
    rt_missing = "rt-missing"
    binding_missing = "binding-missing"
    observed_at = bff_main.utc_now()

    def write_store(name: str, payload: object) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    stores = {
        "PANTHEON_BFF_PERSONA_REGISTRY_STORE": write_store(
            "personas.json",
            {
                persona_test: {
                    "persona_id": persona_test,
                    "name": "Precedence Persona",
                    "lifecycle_state": "deployed",
                    "status": "deployed",
                    "created_at": "2026-07-13T00:00:00Z",
                    "metadata": {
                        "market_scope": ["US"],
                        "capital_mode": "paper",
                        "deployment_stage": "paper",
                    },
                },
                persona_missing: {
                    "persona_id": persona_missing,
                    "name": "Missing Telemetry Persona",
                    "lifecycle_state": "deployed",
                    "status": "deployed",
                    "created_at": "2026-07-13T00:00:00Z",
                    "metadata": {
                        "market_scope": ["US"],
                        "capital_mode": "paper",
                        "deployment_stage": "paper",
                    },
                },
            },
        ),
        "PANTHEON_BFF_PERSONA_SESSION_STORE": write_store(
            "sessions.json",
            {
                "session-assigned": {
                    "session_id": "session-assigned",
                    "persona_id": persona_test,
                    "runtime_id": rt_assigned,
                    "runtime_binding_id": "rb-assigned",
                    "status": "active",
                    "active": True,
                    "last_heartbeat_at": observed_at,
                }
            },
        ),
        "PANTHEON_BFF_PERSONA_BINDING_STORE": write_store(
            "persona_capital_bindings.json",
            {
                binding_canonical: {
                    "binding_id": binding_canonical,
                    "persona_capital_binding_id": binding_canonical,
                    "persona_id": persona_test,
                    "status": "active",
                    "validity": "active",
                },
                binding_missing: {
                    "binding_id": binding_missing,
                    "persona_capital_binding_id": binding_missing,
                    "persona_id": persona_missing,
                    "status": "active",
                    "validity": "active",
                }
            }
        ),
        "PANTHEON_BFF_RUNTIME_BINDING_STORE": write_store(
            "runtime_bindings.json",
            {
                "rb-stale": {
                    "binding_id": "rb-stale",
                    "runtime_id": rt_stale,
                    "persona_capital_binding_id": binding_stale,
                    "persona_id": persona_test,
                    "deployment_mode": "paper",
                    "status": "active",
                },
                "rb-assigned": {
                    "binding_id": "rb-assigned",
                    "runtime_id": rt_assigned,
                    "persona_capital_binding_id": binding_canonical,
                    "persona_id": None,
                    "deployment_mode": "paper",
                    "status": "active",
                },
                "rb-devloop": {
                    "binding_id": "rb-devloop",
                    "runtime_id": rt_devloop,
                    "persona_capital_binding_id": "binding-devloop-unassigned",
                    "persona_id": "persona-us-equity",  # stale seed
                    "deployment_mode": "paper",
                    "status": "active",
                },
                "rb-missing": {
                    "binding_id": "rb-missing",
                    "runtime_id": rt_missing,
                    "persona_capital_binding_id": binding_missing,
                    "persona_id": None,
                    "deployment_mode": "paper",
                    "status": "active",
                }
            },
        ),
        "PANTHEON_BFF_DEPLOYMENT_PLAN_STORE": write_store("deployment_plans.json", {}),
        "PANTHEON_BFF_TELEMETRY_SUMMARY_STORE": write_store("telemetry_summaries.json", {}),
    }

    for env_name in (
        "PANTHEON_PERSONA_DATA_DIR",
        "PANTHEON_GOVERNANCE_DATA_DIR",
        "PANTHEON_RUNTIME_DATA_DIR",
    ):
        monkeypatch.delenv(env_name, raising=False)
    for env_name, path in stores.items():
        monkeypatch.setenv(env_name, str(path))

    # Set service URL env to simulate HTTP service-backed client
    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", "http://telemetry-service.pantheon")

    # Mock HTTP GET for telemetry summaries
    def mock_http_json_get(base_url, path, **kwargs):
        if "runtime-summaries" in path:
            return True, {
                "summaries": [
                    {
                        "runtime_id": rt_assigned,
                        "collected_at": "2026-07-13T01:00:00Z",
                        "pnl": 0.0,
                        "drawdown": 0.0,
                        "fill_rate": 0.0,
                        "avg_slippage_bps": 0.0,
                        "total_trades": 0,
                    },
                    {
                        "runtime_id": rt_devloop,
                        "collected_at": "2026-07-13T02:00:00Z",
                        "pnl": 120.5,
                        "drawdown": 0.01,
                        "fill_rate": 0.99,
                        "avg_slippage_bps": 0.5,
                        "total_trades": 45,
                    }
                ]
            }
        return False, None

    import read_store
    monkeypatch.setattr(read_store, "_http_json_get", mock_http_json_get)

    store = _make_store(allow_local_snapshot_fallback=False)

    # 1. Verify Canonical-binding precedence without registry fallback
    runtimes = {runtime["runtime_id"]: runtime for runtime in store.list_runtime_bindings()}
    # Active runtime binding resolves to persona_test via binding-canonical
    assert runtimes[rt_assigned]["persona_capital_binding_id"] == binding_canonical
    assert runtimes[rt_assigned]["persona_id"] == persona_test

    # Stale runtime binding resolves to None because binding-stale is not active/canonical
    assert runtimes[rt_stale]["persona_id"] is None

    # Devloop runtime binding resolves to None because binding-devloop-unassigned is not active/canonical
    assert runtimes[rt_devloop]["persona_id"] is None

    with _client_with_store(store) as client:
        attribution_response = client.get(
            "/bff/management/performance-attribution/by-persona?page_size=100",
            headers=HEADERS,
        )
        fleet_response = client.get(
            "/bff/management/persona-fleet?page_size=100",
            headers=HEADERS,
        )
        league_response = client.get(
            "/bff/management/persona-league/rankings",
            headers=HEADERS,
        )

    assert attribution_response.status_code == 200, attribution_response.text
    attribution_rows = {
        item["dimension_key"]: item
        for item in attribution_response.json()["data"]["items"]
    }
    # Assigned persona has its own zero metrics record
    assert attribution_rows[persona_test]["metrics"]["total_trades"] == 0
    # Devloop telemetry stays fail-closed in unassigned
    assert attribution_rows["unassigned"]["metrics"]["total_trades"] == 45

    assert fleet_response.status_code == 200, fleet_response.text
    fleet_rows = {
        item["persona_id"]: item
        for item in fleet_response.json()["data"]["items"]
    }
    # Assigned persona must show exact telemetry fields and source 'telemetry_summaries'
    assigned_perf = fleet_rows[persona_test]["performance_summary"]
    assert assigned_perf["source"] == "telemetry_summaries"
    assert assigned_perf["pnl"] == 0.0
    assert assigned_perf["max_drawdown"] == 0.0
    assert assigned_perf["total_trades"] == 0

    # Absent persona-owned evidence on custom persona must not leak seed values
    assert fleet_rows[persona_test]["perf_delta"] is None

    # Missing telemetry persona must have "unavailable" source in fleet
    missing_perf = fleet_rows[persona_missing]["performance_summary"]
    assert missing_perf["source"] == "unavailable"

    assert league_response.status_code == 200, league_response.text
    ranking_rows = {
        item["persona_id"]: item
        for item in league_response.json()["data"]["items"][0]["items"]
    }
    assert ranking_rows[persona_missing]["eligible"] is False
    assert ranking_rows[persona_missing]["metrics"]["telemetry_coverage_count"] == 0

    assert ranking_rows[persona_test]["eligible"] is True
    assert ranking_rows[persona_test]["source_confidence"] == "formal"
