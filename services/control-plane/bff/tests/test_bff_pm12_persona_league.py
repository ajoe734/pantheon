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
            assert set(body) == {"data", "page_info", "meta"}
            assert set(body["data"]) >= {"items", "summary"}
            assert body["page_info"]["total"] >= 1
            assert "GET /bff/personas/{id}/capabilities" in body["meta"]["composition_sources"]
            assert body["meta"]["surfaces"]["persona_league"]["status"] == "ok"
            assert "persona_sessions" in body["meta"]["surfaces"]

            rows = {row["id"]: row for row in body["data"]["items"]}
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
            assert body["data"]["items"][0]["id"] == macro_id
            assert body["data"]["items"][0]["archetype"] == "macro"
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
            assert set(body) == {"data", "page_info", "meta"}
            items = body["data"]["items"]
            summary = body["data"]["summary"]
            assert [block["criteria"] for block in items] == ["overall", "pnl"]
            assert items[0]["items"][0]["rank"] == 1
            assert items[0]["items"][0]["personaId"]
            assert "overallScore" in items[0]["items"][0]
            assert summary["personaCount"] >= 1
            assert body["meta"]["surfaces"]["persona_league_rankings"]["status"] in {"ok", "degraded"}
            assert "GET /bff/management/persona-league" in body["meta"]["composition_sources"]
        finally:
            bff_main.read_store = original


def test_pm12_persona_league_movers_returns_current_snapshot_movers() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get(
                "/bff/management/persona-league/movers",
                headers=HEADERS,
                params={"direction": "new", "limit": 1},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == {"data", "page_info", "meta"}
            items = body["data"]["items"]
            summary = body["data"]["summary"]
            assert "movers" not in body
            assert "movers" not in body["data"]
            assert summary["personaCount"] >= 1
            assert summary["moverCount"] >= 1
            assert summary["returnedCount"] == 1
            assert summary["direction"] == "new"
            assert summary["baselineStatus"] == "unavailable"
            assert summary["newCount"] == summary["personaCount"]
            assert items[0]["currentRank"] == 1
            assert items[0]["previousRank"] is None
            assert items[0]["rankDelta"] is None
            assert items[0]["scoreDelta"] is None
            assert items[0]["direction"] == "new"
            assert items[0]["baselineStatus"] == "unavailable"
            assert items[0]["movement"]["basis"] == "current_persona_league_snapshot_no_historical_baseline"
            assert body["page_info"]["total"] == summary["moverCount"]
            assert body["meta"]["policy"] == "read_only_governance_advisory"
            assert body["meta"]["surfaces"]["persona_league_movers"]["status"] in {"ok", "degraded"}
            assert "GET /bff/management/persona-league/rankings" in body["meta"]["composition_sources"]
        finally:
            bff_main.read_store = original


def test_pm12_persona_league_movers_rejects_invalid_direction() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get(
                "/bff/management/persona-league/movers",
                headers=HEADERS,
                params={"direction": "sideways"},
            )

            assert response.status_code == 422, response.text
            body = response.json()
            assert "detail" not in body
            assert body["error"]["code"] == "VALIDATION_FAILED"
            assert body["field"] == "direction"
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
            assert set(body) == {"data", "page_info", "meta"}
            items = body["data"]["items"]
            summary = body["data"]["summary"]
            assignments = body["data"]["related"]["assignments"]
            assert len(items) == 4
            assert items[0]["tierId"] == "tier-1"
            assert summary["formulaVersion"] == "pm12-default-v1"
            assert summary["personaCount"] == len(assignments)
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
            assert set(body) == {"data", "page_info", "meta"}
            data = body["data"]
            items = data["items"]
            summary = data["summary"]
            assert "rankings" not in body
            assert "rankings" not in data
            assert summary["quarter"] == "2026-Q1"
            assert summary["formula_version"] == "pm12-default-v1"
            assert data["quarter_window"]["start_at"] == "2026-01-01T00:00:00Z"
            assert data["quarter_window"]["end_exclusive_at"] == "2026-04-01T00:00:00Z"
            assert data["formula"]["weights"]["pnl"] == 0.35
            assert body["page_info"]["page_size"] == 1
            assert body["page_info"]["total"] >= 1
            assert items[0]["rank"] == 1
            assert items[0]["quarter"] == "2026-Q1"
            assert items[0]["score_field"] == "overallScore"
            assert data["evidence_refs"]
            assert summary["evidence_ref_count"] == len(data["evidence_refs"])
            assert "quarterWindow" not in data
            assert "formulaVersion" not in summary
            assert "evidenceRefs" not in data
            assert "evidenceRefCount" not in summary
            assert "scoreField" not in items[0]
            assert "quarterWindow" not in items[0]
            assert "formulaVersion" not in items[0]
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
            assert body["formula"]["formula_version"] == "pm12-default-v1"
            assert body["formula"]["weights"] == {
                "pnl": 0.35,
                "risk": 0.25,
                "execution": 0.25,
                "activity": 0.15,
            }
            assert body["summary"]["weight_total"] == 1.0
            assert body["summary"]["evidence_ref_count"] == len(body["evidence_refs"])
            assert body["version_history"][0]["formula_version"] == "pm12-default-v1"
            assert body["version_history"][0]["governance_evidence_refs"]
            assert body["formula"]["change_control"]["requires_governance_evidence"] is True
            assert "formulaVersion" not in body["formula"]
            assert "weightTotal" not in body["summary"]
            assert "evidenceRefs" not in body
            assert "versionHistory" not in body
            assert "changeControl" not in body["formula"]
            assert body["meta"]["version_policy"] == "formula_version_changes_require_governance_evidence"
            assert body["meta"]["surfaces"]["quarterly_ranking_formula"]["status"] == "ok"
        finally:
            bff_main.read_store = original


def test_pm12_quarterly_ranking_drilldown_uses_snake_case_lightweight_sources() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get(
                "/bff/management/quarterly-ranking/drilldown",
                headers=HEADERS,
                params={"quarter": "2026-Q1", "persona_id": "persona-alpha"},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            data = body["data"]
            summary = body["summary"]
            contribution = body["contributions"][0]
            assert data["quarter_window"]["start_at"] == "2026-01-01T00:00:00Z"
            assert data["persona_id"] == "persona-alpha"
            assert data["summary"]["persona_id"] == "persona-alpha"
            assert summary["component_count"] == len(data["contributions"])
            assert contribution["score_field"]
            assert contribution["weighted_contribution"] >= 0
            assert contribution["contribution_share"] >= 0
            assert data["source_breakdown"]["route_policy_summary"]["rule_count"] >= 0
            assert data["source_breakdown"]["session_count"] >= 0
            assert data["links"]["parent_ranking"].endswith("quarter=2026-Q1")

            assert "rankingItem" not in body
            assert "contributionBreakdown" not in body
            assert "sourceBreakdown" not in body
            assert "quarterWindow" not in body
            assert "evidenceRefs" not in body
            assert "correlationId" not in body["meta"]
            assert "quarterWindow" not in data
            assert "personaId" not in data
            assert "rankingItem" not in data
            assert "contributionBreakdown" not in data
            assert "sourceBreakdown" not in data
            assert "evidenceRefs" not in data
            assert "parentRanking" not in data["links"]
            assert "scoreField" not in contribution
            assert "weightedContribution" not in contribution
            assert "contributionShare" not in contribution
            assert "rankedCount" not in summary
            assert "formulaVersion" not in summary
            assert "evidenceRefCount" not in summary
            assert "capabilities" not in data["source_breakdown"]
            assert "sessions" not in data["source_breakdown"]
            assert "memory" not in data["source_breakdown"]
            assert "allowedActions" not in data["source_breakdown"]
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
            assert set(body) == {"data", "page_info", "meta"}
            data = body["data"]
            items = data["items"]
            summary = data["summary"]
            assert "recommendations" not in body
            assert "recommendations" not in data
            assert summary["quarter"] == "2026-Q1"
            assert data["quarter_window"]["start_at"] == "2026-01-01T00:00:00Z"
            assert body["page_info"]["page_size"] == 3
            assert body["page_info"]["total"] >= len(items) >= 1
            assert body["meta"]["policy"] == "read_only_governance_advisory"
            assert body["meta"]["live_capital_mutation"] is False
            assert summary["live_capital_mutation_count"] == 0
            assert summary["human_gate_decision_count"] == body["page_info"]["total"]
            assert "human_gate_decision" in body["meta"]["governance_destinations"]
            assert "GET /bff/management/human-inbox" in body["meta"]["composition_sources"]
            assert body["meta"]["surfaces"]["quarterly_ranking_recommendations"]["status"] in {"ok", "degraded"}

            allowed = set(summary["allowed_actions"])
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
            assert "quarterWindow" not in data
            assert "allowedActions" not in summary
            assert "humanGateDecisionCount" not in summary
            assert "liveCapitalMutationCount" not in summary
            for recommendation in items:
                assert recommendation["action_id"] in allowed
                assert recommendation["recommendation_type"] == "governance_advisory"
                assert recommendation["requires_human_gate_decision"] is True
                assert recommendation["live_capital_mutation"] is False
                assert recommendation["governance"]["live_capital_mutation"] is False
                assert "human_inbox" in recommendation["governance"]["destinations"]
                assert "actionId" not in recommendation
                assert "recommendationType" not in recommendation
                assert "requiresHumanGateDecision" not in recommendation
                assert "liveCapitalMutation" not in recommendation
                assert "liveCapitalMutation" not in recommendation["governance"]
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
            body = response.json()
            assert "detail" not in body
            assert body["error"]["code"] == "VALIDATION_FAILED"
            assert body["field"] == "quarter"

            recommendations = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={"quarter": "2026-05"},
            )

            assert recommendations.status_code == 422, recommendations.text
            recommendations_body = recommendations.json()
            assert "detail" not in recommendations_body
            assert recommendations_body["error"]["code"] == "VALIDATION_FAILED"
            assert recommendations_body["field"] == "quarter"
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

            movers = client.get("/bff/management/persona-league/movers")
            assert movers.status_code == 401, movers.text

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
