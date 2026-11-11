"""BFF-DISCOVERY-READ-SURFACES-20261110 contract tests.

Fixtures use the owning services' real shapes:
- research app: GET /api/research/alpha-templates/evaluations (real evaluator output)
- source_ingestion: finalize_card() output (hypothesis_card.v1)
- source_ingestion connector definitions (data.gov.tw collectors)
- replication worker metric keys (in_sample_/out_of_sample_*)
"""
from __future__ import annotations

import dataclasses
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.research import discovery_projection as dp
from services.control_plane.bff.evolution.service import evolution_journal_decision_item
from services.source_ingestion.hypothesis_card import finalize_card

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_af_contract", _HERE / "test_bff_alpha_factory_contract.py")
af = importlib.util.module_from_spec(_spec)
sys.modules["_af_contract"] = af
_spec.loader.exec_module(af)

_SEED_SPEC = importlib.util.spec_from_file_location(
    "_seed_review", _HERE.parent / "test_datastrat_seed_review_bff.py"
)
seed_mod = importlib.util.module_from_spec(_SEED_SPEC)
sys.modules["_seed_review"] = seed_mod
_SEED_SPEC.loader.exec_module(seed_mod)

TEXT = "Momentum in TWSE large caps persists for about three months after earnings surprises."
SRC = "src-paper-001"


def _real_card() -> Dict[str, Any]:
    start = TEXT.index("Momentum")
    structured = {
        "hypothesis_card": {
            "claim": "Post-earnings momentum persists for three months in TWSE large caps.",
            "mechanism": "Under-reaction to earnings news.",
            "signal_inputs": ["earnings_surprise", "ohlcv"],
            "rule_sketch": "Buy top decile surprise, hold 60 days.",
            "horizon": "3m",
            "universe": "TWSE large caps",
            "direction": "long",
            "conditions": [],
            "cited_spans": [
                {
                    "source_record_id": SRC,
                    "start": start,
                    "end": len(TEXT),
                    "exact_text": TEXT[start:],
                }
            ],
            "testability": "testable",
        }
    }
    return finalize_card(structured, text=TEXT, source_record_id=SRC, origin="paper")


@pytest.fixture(scope="module")
def real_template_list() -> Dict[str, Any]:
    """Real owner response from the research service app."""
    import os

    os.environ["REGISTRY_STORE_BACKEND"] = "memory"
    from services.registry.storage import reset_store
    from services.research.main import app

    reset_store()
    with TestClient(app) as client:
        r = client.post(
            "/api/research-orchestrator/alpha-templates/evaluate",
            json={"template_ids": ["qlib_alpha158:KMID"], "run_id": "rrun-disc-001"},
        )
        assert r.status_code == 201, r.text
        listed = client.get(
            "/api/research-orchestrator/alpha-templates/evaluations",
            params={"status": "evaluated"},
        )
        assert listed.status_code == 200
        body = listed.json()
    reset_store()
    return body


def test_alpha_factory_returns_templates_with_evaluation_metrics(monkeypatch, real_template_list) -> None:
    def fake_get(url, **kw):
        if url.endswith("/alpha-templates/evaluations"):
            return 200, real_template_list
        return 200, af._hosted_owner_entries()

    client = af._make_bff_client(monkeypatch, http_get=fake_get)
    resp = client.get("/bff/alpha-factory", headers=af._auth_headers("tenant-dev"))
    assert resp.status_code == 200
    body = resp.json()
    templates = body["data"]["templates"]
    kmid = next(t for t in templates if t["template_id"] == "qlib_alpha158:KMID")
    assert kmid["status"] == "evaluated"
    assert kmid["evaluation_ref"] == "rrun-disc-001"
    assert set(kmid["evaluation"]["pooled_ic"]) == {"1d", "5d", "20d"}
    assert body["meta"]["surfaces"]["alpha_factory_templates"]["status"] == "ok"


def test_alpha_factory_template_failure_degrades_without_dropping_cards(monkeypatch) -> None:
    def fake_get(url, **kw):
        if url.endswith("/alpha-templates/evaluations"):
            return 503, None
        return 200, af._hosted_owner_entries()

    client = af._make_bff_client(monkeypatch, http_get=fake_get)
    body = client.get("/bff/alpha-factory", headers=af._auth_headers("tenant-dev")).json()
    assert body["data"]["templates"] == []
    assert body["meta"]["surfaces"]["alpha_factory_templates"]["status"] == "unavailable"
    assert body["page_info"]["total"] > 0


def test_alpha_factory_card_passes_through_in_and_out_of_sample_metrics(monkeypatch) -> None:
    entries = af._hosted_owner_entries()
    entries[0]["metrics"] = {
        "in_sample_sharpe_ratio": 1.4,
        "out_of_sample_sharpe_ratio": 0.7,
        "max_drawdown": -0.12,
        "total_return": 0.2,
        "round_trips": 31,
        "deflated_sharpe_ratio": 0.4,
        "trial_count": 5,
    }

    def fake_get(url, **kw):
        if url.endswith("/alpha-templates/evaluations"):
            return 200, {"count": 0, "items": []}
        return 200, entries

    client = af._make_bff_client(monkeypatch, http_get=fake_get)
    body = client.get("/bff/alpha-factory", headers=af._auth_headers("tenant-dev")).json()
    card = next(c for c in body["items"] if c["admission_id"] == entries[0]["admission_id"])
    assert card["metrics"]["in_sample"]["sharpe_ratio"] == 1.4
    assert card["metrics"]["out_of_sample"]["sharpe_ratio"] == 0.7
    assert card["metrics"]["trial_count"] == 5
    assert card["lineage"]["strategy_spec_id"] == entries[0]["strategy_spec_id"]
    other = next(c for c in body["items"] if c["admission_id"] != entries[0]["admission_id"])
    assert other["metrics"] is None


def test_hypothesis_card_links_cited_spans_to_source_records() -> None:
    card = dp.project_hypothesis_card(_real_card())
    assert card["claim"].startswith("Post-earnings")
    span = card["cited_spans"][0]
    assert span["exact_text"] and span["source_record"]["source_record_id"] == SRC
    assert span["source_record"]["href"].endswith(f"/{SRC}")
    assert card["source_records"] == [span["source_record"]]
    assert dp.project_hypothesis_card(None) is None


def test_strategy_seed_route_returns_card_lineage_and_gov_tw_attribution() -> None:
    with seed_mod._review_client() as (client, store_path):
        base = seed_mod._seed("seed-disc-card")
        seed = dataclasses.replace(
            base,
            metadata={**base.metadata, "hypothesis_card": _real_card(), "provider": "NDC via data.gov.tw"},
            lineage={**base.lineage, "source_record_refs": ["content://rec/1"]},
        )
        seed_mod.StrategySpecSeedStore(path=store_path).save(seed)
        resp = client.get("/bff/management/strategy-seeds", headers=seed_mod.REVIEWER_HEADERS)
        assert resp.status_code == 200, resp.text
        cards = resp.json()["data"]["items"]
        card = next(c for c in cards if c["seed_id"] == "seed-disc-card")
        assert card["hypothesis_card"]["cited_spans"][0]["source_record"]["source_record_id"] == SRC
        assert card["lineage"]["source_record_refs"] == ["content://rec/1"]
        assert card["lineage"]["source_records"][0]["source_record_id"] == "src-seed-disc-card"
        assert card["attribution"]["provider"] == "data.gov.tw"
        assert "data.gov.tw" in card["attribution"]["text"]
        plain = next(c for c in cards if c["seed_id"] == seed_mod.SEED_ID)
        assert plain["hypothesis_card"] is None and plain["attribution"] is None


def test_gov_tw_attribution_follows_real_collector_definitions() -> None:
    from services.source_ingestion import connector_definitions as cd

    gov = [s for s in cd.COLLECTOR_SOURCE_SPECS if "data.gov.tw" in s["provider"]]
    other = [s for s in cd.COLLECTOR_SOURCE_SPECS if "data.gov.tw" not in s["provider"]]
    assert gov and other
    for spec in gov:
        assert dp.attribution_block(spec["provider"])["text"]
    for spec in other:
        assert dp.attribution_block(spec["provider"]) is None


def test_source_health_reports_pause_reasons_from_observed_state() -> None:
    health = dp.project_source_health(
        {"desired_lifecycle": "degraded_disabled"},
        {
            "effective_lifecycle": "degraded_disabled",
            "health_state": "degraded",
            "reasons": ["canary_failed", "dlq_unresolved"],
            "dlq_unresolved_count": 3,
            "freshness": {"state": "stale"},
            "last_run": {"status": "failed"},
        },
    )
    assert health["paused"] is True
    assert health["pause_reasons"] == ["canary_failed", "dlq_unresolved"]
    enabled = dp.project_source_health({"desired_lifecycle": "enabled"}, {"effective_lifecycle": "enabled", "health_state": "healthy"})
    assert enabled["paused"] is False and enabled["pause_reasons"] is None


def test_evolution_journal_decision_carries_discovery_block() -> None:
    decision = {
        "decision_id": "evd-1",
        "action_type": "retrain",
        "status": "pending",
        "created_at": "2026-11-10T00:00:00Z",
        "backtest_summary": {
            "in_sample_sharpe_ratio": 1.1,
            "out_of_sample_sharpe_ratio": 0.5,
            "out_of_sample_max_drawdown": -0.1,
        },
        "lineage": {"source_ids": [SRC], "evidence_bundle_id": "bundle-1"},
    }
    item = evolution_journal_decision_item(decision)
    disc = item["discovery"]
    assert disc["metrics"]["in_sample"]["sharpe_ratio"] == 1.1
    assert disc["metrics"]["out_of_sample"]["max_drawdown"] == -0.1
    assert disc["lineage"]["source_records"][0]["source_record_id"] == SRC
    bare = evolution_journal_decision_item({"decision_id": "evd-2", "status": "pending"})["discovery"]
    assert bare["metrics"] is None and bare["lineage"] is None
