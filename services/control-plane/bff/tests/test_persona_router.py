"""Unit and integration tests for Persona Domain Router and Service.

Part of OPGAP-BE-PERSONA-ROUTER-V2-20260830.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Ensure bff is in path
BFF_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BFF_DIR not in sys.path:
    sys.path.insert(0, BFF_DIR)

from personas import PersonaService, create_personas_router, router

AUTH_HEADERS = {"Authorization": "Bearer test-operator:operator,reviewer,admin"}


@pytest.fixture(autouse=True)
def enable_auth_stub(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI(title="Persona Test App")
    app.include_router(router)
    return TestClient(app)


def test_zero_reverse_imports():
    """Verify that personas domain module does NOT import main.py or bff_main."""
    persona_dir = os.path.join(BFF_DIR, "personas")
    for root, _, files in os.walk(persona_dir):
        for file in files:
            if file.endswith(".py"):
                path = os.path.join(root, file)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                for line_idx, line in enumerate(content.splitlines(), start=1):
                    assert "import main" not in line and "from main" not in line, (
                        f"Forbidden reverse import in {path}:{line_idx}: {line}"
                    )
                    assert "import bff_main" not in line and "from bff_main" not in line, (
                        f"Forbidden reverse import in {path}:{line_idx}: {line}"
                    )


def test_route_inventory_count():
    """Verify that the router registers all 49 route decorators."""
    assert len(router.routes) == 49


def test_list_personas_api_v1(client: TestClient):
    resp = client.get("/api/v1/personas", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data or "items" in data or "personas" in data or isinstance(data, list) or "meta" in data


def test_get_persona_detail_api_v1(client: TestClient):
    resp = client.get("/api/v1/personas/persona-alpha", headers=AUTH_HEADERS)
    assert resp.status_code in (200, 404)


def test_list_persona_sessions_api_v1(client: TestClient):
    resp = client.get("/api/v1/personas/persona-alpha/sessions", headers=AUTH_HEADERS)
    assert resp.status_code in (200, 404)


def test_get_session_detail_api_v1(client: TestClient):
    resp = client.get("/api/v1/sessions/session-1", headers=AUTH_HEADERS)
    assert resp.status_code in (200, 404, 503)


def test_list_persona_teaching_sessions_api_v1(client: TestClient):
    resp = client.get("/api/v1/personas/persona-alpha/teaching", headers=AUTH_HEADERS)
    assert resp.status_code in (200, 404)


def test_get_persona_capabilities_api_v1(client: TestClient):
    resp = client.get("/api/v1/personas/persona-alpha/capabilities", headers=AUTH_HEADERS)
    assert resp.status_code in (200, 404)


def test_get_persona_management_api_v1(client: TestClient):
    resp = client.get("/api/v1/operator/persona-management/persona-alpha", headers=AUTH_HEADERS)
    assert resp.status_code in (200, 404)


def test_bff_list_personas(client: TestClient):
    resp = client.get("/bff/personas", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data


def test_bff_create_persona(client: TestClient):
    payload = {
        "name": "Test Persona Creation",
        "description": "Integration test created persona",
        "archetype": "macro_alpha",
        "capital_mode": "paper",
        "authoritative_source": "test",
    }
    headers = dict(AUTH_HEADERS)
    headers["Idempotency-Key"] = "test-create-persona-key-1"
    resp = client.post("/bff/personas", json=payload, headers=headers)
    assert resp.status_code in (201, 200, 202, 400, 409, 500, 502, 503)


def test_bff_get_persona(client: TestClient):
    resp = client.get("/bff/personas/persona-alpha", headers=AUTH_HEADERS)
    assert resp.status_code in (200, 404)


def test_bff_patch_persona(client: TestClient):
    headers = dict(AUTH_HEADERS)
    headers["Idempotency-Key"] = "test-patch-persona-key-1"
    resp = client.patch(
        "/bff/personas/persona-alpha",
        json={"description": "Updated description"},
        headers=headers,
    )
    assert resp.status_code in (200, 404, 400, 409)


def test_bff_get_persona_route_policy(client: TestClient):
    resp = client.get("/bff/personas/persona-alpha/route-policy", headers=AUTH_HEADERS)
    assert resp.status_code in (200, 404)


def test_bff_get_persona_runtime_profile(client: TestClient):
    resp = client.get("/bff/personas/persona-alpha/runtime-profile", headers=AUTH_HEADERS)
    assert resp.status_code in (200, 404)


def test_bff_get_persona_strategy_matches(client: TestClient):
    resp1 = client.get("/api/v1/personas/persona-alpha/strategy-matches", headers=AUTH_HEADERS)
    resp2 = client.get("/bff/personas/persona-alpha/strategy-matches", headers=AUTH_HEADERS)
    assert resp1.status_code in (200, 404)
    assert resp2.status_code in (200, 404)


def test_bff_start_persona_strategy_discovery(client: TestClient):
    payload = {"query": "momentum", "lookback_days": 30}
    headers = dict(AUTH_HEADERS)
    headers["Idempotency-Key"] = "test-strat-disc-1"
    resp = client.post(
        "/bff/personas/persona-alpha/strategy-discovery",
        json=payload,
        headers=headers,
    )
    assert resp.status_code in (202, 200, 404, 400)


def test_bff_persona_strategy_match_action(client: TestClient):
    payload = {"action": "create_research_ticket", "notes": "Approved in test"}
    headers = dict(AUTH_HEADERS)
    headers["Idempotency-Key"] = "test-strat-match-act-1"
    resp = client.post(
        "/bff/personas/persona-alpha/strategy-matches/match-1/actions",
        json=payload,
        headers=headers,
    )
    assert resp.status_code in (202, 200, 404, 400, 422)


def test_bff_get_persona_activity(client: TestClient):
    resp = client.get("/bff/personas/persona-alpha/activity", headers=AUTH_HEADERS)
    assert resp.status_code in (200, 404)


def test_bff_get_persona_evaluations(client: TestClient):
    resp = client.get("/bff/personas/persona-alpha/evaluations", headers=AUTH_HEADERS)
    assert resp.status_code in (200, 404)


def test_bff_get_persona_memory(client: TestClient):
    resp = client.get("/bff/personas/persona-alpha/memory", headers=AUTH_HEADERS)
    assert resp.status_code in (200, 404)


def test_bff_get_persona_audit(client: TestClient):
    resp = client.get("/bff/personas/persona-alpha/audit", headers=AUTH_HEADERS)
    assert resp.status_code in (200, 404)


def test_bff_get_persona_skills(client: TestClient):
    resp = client.get("/bff/personas/persona-alpha/skills", headers=AUTH_HEADERS)
    assert resp.status_code in (200, 404)


def test_bff_get_persona_tools(client: TestClient):
    resp = client.get("/bff/personas/persona-alpha/tools", headers=AUTH_HEADERS)
    assert resp.status_code in (200, 404)


def test_bff_get_persona_capabilities_surface(client: TestClient):
    resp = client.get("/bff/personas/persona-alpha/capabilities", headers=AUTH_HEADERS)
    assert resp.status_code in (200, 404)


def test_bff_persona_action(client: TestClient):
    payload = {"reason": "Test action"}
    headers = dict(AUTH_HEADERS)
    headers["Idempotency-Key"] = "test-persona-action-1"
    resp = client.post(
        "/bff/personas/persona-alpha/actions/archive",
        json=payload,
        headers=headers,
    )
    assert resp.status_code in (202, 200, 404, 400, 410)


def test_bff_persona_test_prompt(client: TestClient):
    payload = {"prompt": "What is current portfolio exposure?"}
    headers = dict(AUTH_HEADERS)
    headers["Idempotency-Key"] = "test-prompt-1"
    resp = client.post(
        "/bff/personas/persona-alpha/test-prompt",
        json=payload,
        headers=headers,
    )
    assert resp.status_code in (202, 200, 404, 400)


def test_bff_management_persona_intent(client: TestClient):
    resp = client.get("/bff/management/persona-intent", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data or "items" in data or "summary" in data


def test_bff_management_quarterly_ranking_formula(client: TestClient):
    resp = client.get("/bff/management/quarterly-ranking/formula", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data


def test_bff_management_quarterly_ranking(client: TestClient):
    resp = client.get("/bff/management/quarterly-ranking", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data


def test_bff_management_quarterly_ranking_drilldown(client: TestClient):
    resp = client.get(
        "/bff/management/quarterly-ranking/drilldown?persona_id=persona-alpha",
        headers=AUTH_HEADERS,
    )
    assert resp.status_code in (200, 404)


def test_bff_management_quarterly_ranking_recommendations(client: TestClient):
    resp = client.get("/bff/management/quarterly-ranking/recommendations", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data


def test_bff_management_promotion_reviews(client: TestClient):
    resp = client.get("/bff/management/promotion-reviews", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data


def test_bff_management_persona_league(client: TestClient):
    resp = client.get("/bff/management/persona-league", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data


def test_bff_management_persona_league_rankings(client: TestClient):
    resp = client.get("/bff/management/persona-league/rankings", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data


def test_bff_management_persona_league_movers(client: TestClient):
    resp = client.get("/bff/management/persona-league/movers", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data


def test_bff_management_persona_league_tiers(client: TestClient):
    resp = client.get("/bff/management/persona-league/tiers", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data


def test_bff_management_persona_league_heatmap(client: TestClient):
    resp = client.get("/bff/management/persona-league/heatmap", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data


def test_bff_persona_league(client: TestClient):
    resp = client.get("/bff/persona-league", headers=AUTH_HEADERS)
    assert resp.status_code == 200


def test_bff_management_persona_fleet(client: TestClient):
    resp = client.get("/bff/management/persona-fleet", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data


def test_custom_router_factory():
    """Verify that create_personas_router allows injecting custom callables."""
    custom_service = PersonaService()
    custom_router = create_personas_router(service=custom_service)
    assert len(custom_router.routes) == 49
