from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main  # noqa: E402
from read_store import ReadSurfaceStore  # noqa: E402


HEADERS = {
    "Authorization": "Bearer op-pm12-delta:operator,reviewer",
    "X-Correlation-Id": "corr-bff-pm12-delta-001",
}


def _fresh_client(td: str) -> TestClient:
    bff_main.read_store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=True,
    )
    return TestClient(bff_main.app, raise_server_exceptions=False)


def test_quarterly_ranking_drilldown_returns_persona_contribution_breakdown() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)

            anonymous = client.get(
                "/bff/management/quarterly-ranking/drilldown",
                params={"personaId": "persona-alpha", "quarter": "2026-Q1"},
            )
            assert anonymous.status_code == 401, anonymous.text

            response = client.get(
                "/bff/management/quarterly-ranking/drilldown",
                headers=HEADERS,
                params={"personaId": "persona-alpha", "quarter": "2026-Q1"},
            )

            assert response.status_code == 200, response.text
            assert response.headers["X-Correlation-Id"] == "corr-bff-pm12-delta-001"
            body = response.json()
            data = body["data"]

            assert data["personaId"] == "persona-alpha"
            assert data["quarter"] == "2026-Q1"
            assert data["quarterWindow"]["startAt"] == "2026-01-01T00:00:00Z"
            assert data["quarterWindow"]["endExclusiveAt"] == "2026-04-01T00:00:00Z"
            assert data["rankingItem"]["personaId"] == "persona-alpha"
            assert body["rankingItem"] == data["rankingItem"]
            assert body["contributionBreakdown"] == data["contributionBreakdown"]
            assert body["summary"]["personaId"] == "persona-alpha"
            assert body["summary"]["quarter"] == "2026-Q1"
            assert body["summary"]["componentCount"] == 4
            assert body["summary"]["rankedCount"] >= 1
            assert body["summary"]["totalWeightedContribution"] == data["summary"]["totalWeightedContribution"]
            assert body["meta"]["correlationId"] == "corr-bff-pm12-delta-001"
            assert body["meta"]["policy"] == "read_only_governance_advisory"
            assert body["meta"]["surfaces"]["quarterly_ranking_drilldown"]["status"] in {"ok", "degraded"}
            assert "GET /bff/management/quarterly-ranking" in body["meta"]["composition_sources"]
            assert "GET /api/v1/knowledge/evidence" in body["meta"]["composition_sources"]

            contribution_keys = {row["key"] for row in data["contributions"]}
            assert contribution_keys == {"pnl", "risk", "execution", "activity"}
            for row in data["contributions"]:
                assert row["basis"] == "component_score_x_formula_weight"
                assert row["weightedContribution"] == row["weighted_contribution"]
                assert 0 <= row["contributionShare"] <= 1
        finally:
            bff_main.read_store = original


def test_quarterly_ranking_drilldown_accepts_cors_preflight() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.options(
                "/bff/management/quarterly-ranking/drilldown",
                headers={
                    "Origin": "https://pantheon-dev.lovable.app",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "Authorization, X-Correlation-Id",
                },
            )

            assert response.status_code in {200, 204}
            assert response.headers["access-control-allow-origin"] == "https://pantheon-dev.lovable.app"
            assert "authorization" in response.headers["access-control-allow-headers"].lower()
        finally:
            bff_main.read_store = original
