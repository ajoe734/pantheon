"""Regime classifier fit: the production entry point through the Research run path.

The integration test runs the route over real recorded governed index and breadth rows: the
unedited official TWSE responses committed under ``deploy/collector/tests/fixtures`` pushed
through the real collector spiders, the production pull entry point and the normalized store
writer. That recorded history is short (16 TAIEX sessions, one breadth day), so the correct
outcome is a fail-closed result with recorded reasons and nothing registered. A model fit over
long history needs the collector host's TAIEX backfill; no synthetic history is substituted.
"""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.research.regime.tests.fixtures import FakeRegistry, synthetic_observations
from services.research.tests.test_persona_demand_routes_auth import (
    ISSUER, AUDIENCE, SECRET, TENANT, _cycle_headers, _headers,
)
from services.research.tests.test_research_orchestrator_http_service import _load_service_module

URL = "/api/research/regime-models/fit"


@pytest.fixture
def research(tmp_path, monkeypatch):
    for key, value in {"AUTH_MODE": "strict", "JWT_SECRET": SECRET, "JWT_ISSUER": ISSUER,
                       "JWT_AUDIENCE": AUDIENCE, "MFA_REQUIRED": "false"}.items():
        monkeypatch.setenv("PANTHEON_RESEARCH_" + key, value)
    module = _load_service_module(data_dir=str(tmp_path / "research"))
    return module, TestClient(module.app)


def _wait(module, run_id, timeout=240.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = module.store.get_run(run_id)
        if run and run["status"] in ("completed", "failed"):
            return run
        time.sleep(0.2)
    raise AssertionError("regime fit run did not finish")


def _fail_if_registry_called(module, monkeypatch):
    def refuse(**kwargs):
        raise AssertionError(f"registry must not be called: {kwargs['url']}")
    monkeypatch.setattr(module, "_retrain_call", refuse)


def test_route_requires_the_cycle_principal(research):
    _, client = research
    assert client.post(URL, json={}).status_code in (401, 403)
    assert client.post(URL, headers=_headers(), json={}).status_code == 403  # an unrelated viewer
    assert client.post(URL, headers=_cycle_headers(), json={"market": "US"}).status_code == 422
    assert client.post(URL, headers=_cycle_headers(), json={"demand_id": "no-such-demand"}).status_code == 404


def test_missing_governed_store_fails_closed(research, monkeypatch, tmp_path):
    module, client = research
    monkeypatch.setenv("RESEARCH_REGIME_STORE_ROOT", str(tmp_path / "absent"))
    _fail_if_registry_called(module, monkeypatch)
    response = client.post(URL, headers=_cycle_headers(), json={})
    assert response.status_code == 202 and response.json()["status"] == "queued"
    run = _wait(module, response.json()["run_id"])
    assert run["status"] == "completed" and run["outcome"] == "unavailable"
    assert run["outcome_reason"] == "governed_regime_data_missing:store_root_not_found"
    assert run["paper_only"] is True and "regime_fit" not in run


def test_real_recorded_index_and_breadth_rows_fail_closed_with_recorded_reasons(research, monkeypatch, tmp_path):
    from services.source_ingestion.market_data_storage import MarketDataStorageWriter

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "source_ingestion" / "tests"))
    try:
        bundle = importlib.import_module("test_collector_bundle_connector")
    finally:
        sys.path.pop(0)
    files = bundle.regime_collector_files(tmp_path / "real")
    store = tmp_path / "store"
    for source_id in bundle._REGIME_SOURCES:
        adapter, records = bundle.pull_regime_records(files, tmp_path, monkeypatch, source_id)
        MarketDataStorageWriter(store).write_run(
            result=SimpleNamespace(records=list(records), run=SimpleNamespace(ingest_run_id=f"run-{source_id}")),
            connector=adapter.connector())

    module, client = research
    monkeypatch.setenv("RESEARCH_REGIME_STORE_ROOT", str(store))
    # The recorded rows are stamped with their acquisition time (the test run), so decide after it.
    from datetime import datetime, timedelta, timezone
    monkeypatch.setenv("RESEARCH_REGIME_TRUSTED_NOW", (datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
    _fail_if_registry_called(module, monkeypatch)
    response = client.post(URL, headers=_cycle_headers(), json={})
    assert response.status_code == 202
    run = _wait(module, response.json()["run_id"])
    assert run["status"] == "completed" and run["outcome"] == "regime_fit_unavailable"
    result = run["regime_fit"]
    assert result["status"] == "unavailable" and result["approved"] == [] and result["paper_only"] is True
    # The real rows were read: 71 index rows (56 on 2026-11-10 + 15 TAIEX 2012-01 history) and one breadth day.
    assert result["data_notes"]["tw_index_daily"]["rows"] == 71
    assert result["data_notes"]["tw_market_breadth_daily"]["rows"] == 1
    by_axis = {(a["axis"], a.get("horizon")): a for a in result["axes"]}
    assert len(by_axis) == 7
    for horizon in ("short", "medium"):
        assert by_axis[("volatility", horizon)]["reason"] == "tw_index_daily.close_history_short:16<500"
        assert by_axis[("trend", horizon)]["reason"].startswith("tw_index_daily.close_history_short")
        assert by_axis[("risk_appetite", horizon)]["status"] == "unavailable"
    assert by_axis[("macro", None)]["status"] == "unavailable"
    # Idempotent per tenant, market and day while the run is active or after finishing does not double-fit silently.
    assert all("registration" not in a for a in result["axes"])


def test_fit_request_registers_models_through_the_registry_owner(research, monkeypatch, tmp_path):
    """Route -> run executor -> registry calls, over synthetic observations (protocol test only)."""
    module, client = research
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("RESEARCH_REGIME_STORE_ROOT", str(root))
    monkeypatch.setenv("RESEARCH_REGIME_TRUSTED_NOW", "2030-01-01T00:00:00+00:00")
    observations, _, _ = synthetic_observations(1400, seed=9, with_chip=False, with_macro=False)
    import services.research.regime.features as features

    monkeypatch.setattr(features, "load_governed_observations", lambda r: (observations, {"synthetic": True}))
    registry = FakeRegistry()
    seen = []

    def fake_call(**kw):
        seen.append((kw["method"], kw["url"], kw["token_variable"], kw["tenant_id"]))
        if kw["url"].endswith("/api/registry/entries"):
            return registry.create_entry(kw["body"], idempotency_key=kw["extra_headers"]["Idempotency-Key"])
        registry_id = kw["url"].split("/entries/")[1].split("/advance")[0]
        return registry.advance(registry_id, kw["body"])

    monkeypatch.setattr(module, "_retrain_call", fake_call)
    first = client.post(URL, headers=_cycle_headers(), json={})
    assert first.status_code == 202
    run = _wait(module, first.json()["run_id"])
    assert run["status"] == "completed" and run["outcome"] == "regime_fit_completed", (
        run.get("rejection"), run.get("events", [])[-1:])
    result = run["regime_fit"]
    registered = [a for a in result["axes"] if a.get("registration")]
    assert registered and all(a["registration"]["status"] == "candidate" for a in registered)  # 1400 < 10 years
    assert result["approved"] == []
    assert all(call[2] == "RESEARCH_REGISTRY_SERVICE_TOKEN" and call[3] == TENANT for call in seen)
    assert {e["artifact_type"] for e in registry.entries.values()} == {"model_artifact"}
    assert all(e["lineage"]["source_run_ids"] == [first.json()["run_id"]] for e in registry.entries.values())
