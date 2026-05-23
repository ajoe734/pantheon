from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore


HEADERS = {"Authorization": "Bearer op-pm12:operator,reviewer"}


def _fresh_client(td: str, *, fallback: bool = True) -> TestClient:
    bff_main.read_store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=fallback,
    )
    bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
    bff_main._STRATEGY_BFF_OVERLAY.clear()
    bff_main._PERSONA_BFF_OVERLAY.clear()
    return TestClient(bff_main.app, raise_server_exceptions=False)


def _create_persona(client: TestClient, name: str, *, archetype: str, key: str) -> str:
    response = client.post(
        "/bff/personas",
        headers={**HEADERS, "Idempotency-Key": key},
        json={"name": name, "archetype": archetype},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["data"]["id"])


def test_pm12_persona_league_returns_composed_table() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get("/bff/management/persona-league", headers=HEADERS)

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["items"] == body["data"]
            assert body["page_info"]["total"] >= 1
            assert "GET /bff/personas/{id}/capabilities" in body["meta"]["composition_sources"]
            assert body["meta"]["surfaces"]["persona_league"]["status"] == "ok"
            assert "persona_sessions" in body["meta"]["surfaces"]

            rows = {row["id"]: row for row in body["data"]}
            row = rows["persona-alpha"]
            assert row["personaId"] == "persona-alpha"
            assert row["routePolicy"]["ruleCount"] >= 0
            assert row["capabilities"]["skillCount"] >= 0
            assert row["bindings"]["total"] >= 1
            assert row["sessions"]["total"] >= 1
            assert row["evaluations"]["total"] >= 1
            assert row["memory"]["total"] >= 0
            assert row["health"]["health"] in {"healthy", "degraded"}
            assert row["links"]["detail"] == "/bff/personas/persona-alpha"
            assert "allowedActions" in row
        finally:
            bff_main.read_store = original


def test_pm12_persona_league_filters_searches_and_paginates() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td, fallback=False)
            macro_id = _create_persona(client, "Macro PM12", archetype="macro", key="pm12-macro")
            _create_persona(client, "Risk PM12", archetype="risk", key="pm12-risk")

            response = client.get(
                "/bff/management/persona-league",
                headers=HEADERS,
                params={"state": "draft", "archetype": "macro", "q": "macro", "page_size": 1},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["page_info"]["total"] == 1
            assert body["page_info"]["next_page_token"] is None
            assert body["data"][0]["id"] == macro_id
            assert body["data"][0]["archetype"] == "macro"
        finally:
            bff_main.read_store = original


def test_pm12_persona_league_rankings_returns_computed_blocks() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get(
                "/bff/management/persona-league/rankings",
                headers=HEADERS,
                params={"criteria": "overall,pnl", "limit": 1},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["items"] == body["data"]
            assert [block["criteria"] for block in body["items"]] == ["overall", "pnl"]
            assert body["items"][0]["items"][0]["rank"] == 1
            assert body["items"][0]["items"][0]["personaId"]
            assert "overallScore" in body["items"][0]["items"][0]
            assert body["summary"]["personaCount"] >= 1
            assert body["meta"]["surfaces"]["persona_league_rankings"]["status"] in {"ok", "degraded"}
            assert "GET /bff/management/persona-league" in body["meta"]["composition_sources"]
        finally:
            bff_main.read_store = original


def test_pm12_persona_league_tiers_returns_config_and_current_assignments() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get("/bff/management/persona-league/tiers", headers=HEADERS)

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["items"] == body["data"]
            assert len(body["items"]) == 4
            assert body["items"][0]["tierId"] == "tier-1"
            assert body["summary"]["formulaVersion"] == "pm12-default-v1"
            assert body["summary"]["personaCount"] == len(body["assignments"])
            assert body["meta"]["policy"] == "read_only_governance_advisory"
            assert body["meta"]["surfaces"]["persona_league_tiers"]["status"] in {"ok", "degraded"}
        finally:
            bff_main.read_store = original


def test_pm12_quarterly_ranking_returns_formula_window_and_evidence() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q1", "page_size": 1},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["items"] == body["data"]["items"]
            assert body["rankings"] == body["items"]
            assert body["summary"]["quarter"] == "2026-Q1"
            assert body["summary"]["formulaVersion"] == "pm12-default-v1"
            assert body["quarterWindow"]["startAt"] == "2026-01-01T00:00:00Z"
            assert body["quarterWindow"]["endExclusiveAt"] == "2026-04-01T00:00:00Z"
            assert body["formula"]["weights"]["pnl"] == 0.35
            assert body["page_info"]["page_size"] == 1
            assert body["page_info"]["total"] >= 1
            assert body["items"][0]["rank"] == 1
            assert body["items"][0]["quarter"] == "2026-Q1"
            assert body["items"][0]["scoreField"] == "overallScore"
            assert body["evidenceRefs"]
            assert body["summary"]["evidenceRefCount"] == len(body["evidenceRefs"])
            assert body["meta"]["policy"] == "read_only_governance_advisory"
            assert body["meta"]["surfaces"]["quarterly_ranking"]["status"] in {"ok", "degraded"}
            assert "GET /bff/management/persona-league" in body["meta"]["composition_sources"]
            assert "GET /api/v1/knowledge/evidence" in body["meta"]["composition_sources"]
        finally:
            bff_main.read_store = original


def test_pm12_quarterly_ranking_formula_returns_weights_and_governance_trace() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get(
                "/bff/management/quarterly-ranking/formula",
                headers=HEADERS,
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["data"] == body["formula"]
            assert body["formula"]["formulaVersion"] == "pm12-default-v1"
            assert body["formula"]["weights"] == {
                "pnl": 0.35,
                "risk": 0.25,
                "execution": 0.25,
                "activity": 0.15,
            }
            assert body["summary"]["weightTotal"] == 1.0
            assert body["summary"]["evidenceRefCount"] == len(body["evidenceRefs"])
            assert body["versionHistory"][0]["formulaVersion"] == "pm12-default-v1"
            assert body["versionHistory"][0]["governanceEvidenceRefs"]
            assert body["formula"]["changeControl"]["requiresGovernanceEvidence"] is True
            assert body["meta"]["version_policy"] == "formula_version_changes_require_governance_evidence"
            assert body["meta"]["surfaces"]["quarterly_ranking_formula"]["status"] == "ok"
        finally:
            bff_main.read_store = original


def test_pm12_quarterly_ranking_recommendations_are_governance_only() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={"quarter": "2026-Q1", "page_size": 3},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["items"] == body["data"]["recommendations"]
            assert body["recommendations"] == body["items"]
            assert body["summary"]["quarter"] == "2026-Q1"
            assert body["quarterWindow"]["startAt"] == "2026-01-01T00:00:00Z"
            assert body["page_info"]["page_size"] == 3
            assert body["page_info"]["total"] >= len(body["items"]) >= 1
            assert body["meta"]["policy"] == "read_only_governance_advisory"
            assert body["meta"]["live_capital_mutation"] is False
            assert body["summary"]["liveCapitalMutationCount"] == 0
            assert body["summary"]["humanGateDecisionCount"] == body["page_info"]["total"]
            assert "human_gate_decision" in body["meta"]["governance_destinations"]
            assert "GET /bff/management/human-inbox" in body["meta"]["composition_sources"]
            assert body["meta"]["surfaces"]["quarterly_ranking_recommendations"]["status"] in {"ok", "degraded"}

            allowed = set(body["summary"]["allowedActions"])
            assert allowed == {
                "promote_to_canary_candidate",
                "increase_research_budget",
                "grant_tool_access",
                "reduce_capital_access",
                "require_retraining",
                "freeze_persona",
                "suspend_persona",
                "retire_persona",
            }
            for recommendation in body["items"]:
                assert recommendation["actionId"] in allowed
                assert recommendation["recommendationType"] == "governance_advisory"
                assert recommendation["requiresHumanGateDecision"] is True
                assert recommendation["liveCapitalMutation"] is False
                assert recommendation["governance"]["liveCapitalMutation"] is False
                assert "human_inbox" in recommendation["governance"]["destinations"]
        finally:
            bff_main.read_store = original


def test_pm12_quarterly_ranking_rejects_invalid_quarter() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-05"},
            )

            assert response.status_code == 422, response.text
            assert response.json()["detail"]["error"] == "invalid_quarter"

            recommendations = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={"quarter": "2026-05"},
            )

            assert recommendations.status_code == 422, recommendations.text
            assert recommendations.json()["detail"]["error"] == "invalid_quarter"
        finally:
            bff_main.read_store = original


def test_pm12_persona_league_requires_auth() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get("/bff/management/persona-league")

            assert response.status_code == 401, response.text

            rankings = client.get("/bff/management/persona-league/rankings")
            assert rankings.status_code == 401, rankings.text

            tiers = client.get("/bff/management/persona-league/tiers")
            assert tiers.status_code == 401, tiers.text

            quarterly = client.get("/bff/management/quarterly-ranking")
            assert quarterly.status_code == 401, quarterly.text

            recommendations = client.get("/bff/management/quarterly-ranking/recommendations")
            assert recommendations.status_code == 401, recommendations.text

            formula = client.get("/bff/management/quarterly-ranking/formula")
            assert formula.status_code == 401, formula.text
        finally:
            bff_main.read_store = original
