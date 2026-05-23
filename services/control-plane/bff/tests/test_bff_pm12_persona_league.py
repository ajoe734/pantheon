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
        finally:
            bff_main.read_store = original
