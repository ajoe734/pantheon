"""Local integration: mounted BFF, signed JWTs, real owner routes/stores.

Only HTTP transport is in-process; fixtures are synthetic, not hosted evidence.

GENUINE BLOCKER: the audited defect was on the default mounted journal routes,
including browser-session middleware. A router-only app cannot prove that the
production composition forwards caller authority to the real tenant owners or
ignores configured legacy file projections. This narrow owner/composition suite
is explicitly allowlisted; the non-whitelisted-main-importer ceiling stays zero.
"""
from __future__ import annotations

import io
import json
import time
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff import trade_journal
from services.persona.write_owner import CreatePersonaRequest, PersistentPersonaOwner, create_app
from services.runtime_auth_inbound import encode_jwt_hs256
from services.telemetry.trade_episode_projection import TradeEpisodeProjectionStore

SECRET = "journal-read-regression-only-not-a-live-secret"
PATH = "/bff/personas/p1"


def headers(tenant="alpha", **claims):
    payload = {"sub": "test-operator", "roles": ["operator"], "exp": int(time.time()) + 300, **claims}
    if tenant is not None:
        payload["tenant_id"] = tenant
    return {"Authorization": "Bearer " + encode_jwt_hs256(payload, secret=SECRET)}


@pytest.fixture
def owners(monkeypatch, tmp_path):
    for prefix in ("PANTHEON_BFF", "PANTHEON_RUNTIME", "PANTHEON_TELEMETRY", "PERSONA"):
        monkeypatch.setenv(prefix + "_JWT_SECRET", SECRET)
        monkeypatch.setenv(prefix + "_JWT_ISSUER", "")
        monkeypatch.setenv(prefix + "_JWT_AUDIENCE", "")
        monkeypatch.setenv(prefix + "_AUTH_MODE", "strict")
    monkeypatch.delenv("PANTHEON_BFF_AUTH_STUB", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_DATA_DIR", str(tmp_path / "bff"))
    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", "http://test-telemetry")
    monkeypatch.setenv("PERSONA_URL", "http://test-persona")
    # Poison retired paths: no read may consult them, even if configured.
    poison = tmp_path / "unscoped.json"
    poison.write_text(json.dumps([{"persona_id": "p1", "account_id": "do-not-read"}]))
    for kind in ("EPISODES", "REFLECTIONS", "PATTERNS"):
        monkeypatch.setenv(f"PANTHEON_BFF_TRADE_{kind}_STORE", str(poison))
    store = TradeEpisodeProjectionStore(projections_path=tmp_path / "episodes.json", events_path=tmp_path / "events.json")
    for i, tenant in enumerate(("alpha", "alpha", "beta")):
        store.project_event({
            "event_id": f"event-{i}", "event_type": "trade_episode.opened", "schema_version": "1.0",
            "trade_episode_id": f"e{i}", "persona_id": "p1", "tenant_id": tenant,
            "environment": "paper", "occurred_at": f"2026-10-05T00:00:0{i}Z", "sequence_number": 1,
            "payload": {"instrument_id": "SPY", "requested_quantity": 4, "invalidation_conditions": ["halt"]},
        })
    reflection = {
        "reflection_id": "r1", "persona_id": "p1", "trade_episode_id": "e0", "trigger": "manual_retry",
        "environment": "paper", "review_state": "proposed", "version": "1.0.0", "mistakes": [],
        "counterfactuals": [{"alternative_action": "wait", "estimated_impact": "unknown", "assumptions": "same facts"}],
        "lesson_candidates": [{"lesson_candidate_id": "lesson-1", "proposed_change": "review sizing"}],
    }
    persona = PersistentPersonaOwner.from_json_path(tmp_path / "personas.json")
    persona.create(CreatePersonaRequest(actor_id="fixture", persona_id="p1", name="Fixture", mandate="Test only", tenant_id="alpha", metadata={
        "trade_reflections": [reflection, {**reflection, "reflection_id": "pattern-1", "trigger": "scheduled_pattern"}],
    }))
    persona_client = TestClient(create_app(persona))
    from services.telemetry import main as telemetry
    monkeypatch.setattr(telemetry, "_get_service", lambda: SimpleNamespace(
        list_trade_episode_projections=store.list, get_trade_episode_projection=store.get,
    ))
    telemetry_client = telemetry.app.test_client()
    calls = []

    def transport(req, timeout=5):
        calls.append((req.full_url, dict(req.header_items())))
        assert req.get_method() == "GET", "reads must never trigger commands or provider work"
        if req.full_url.startswith("http://test-telemetry"):
            response = telemetry_client.get(req.full_url.removeprefix("http://test-telemetry"), headers=dict(req.header_items()))
            status, content = response.status_code, response.data
        else:
            assert req.full_url.startswith("http://test-persona")
            response = persona_client.get(req.full_url.removeprefix("http://test-persona"), headers=dict(req.header_items()))
            status, content = response.status_code, response.content
        if status >= 400:
            raise HTTPError(req.full_url, status, "test owner rejection", {}, io.BytesIO(content))
        result = io.BytesIO(content)
        result.status = status
        return result

    monkeypatch.setattr(trade_journal.urllib_request, "urlopen", transport)
    from services.control_plane.bff import main
    return TestClient(main.app), calls


def test_actual_telemetry_scope_pagination_and_dto(owners):
    client, calls = owners
    response = client.get(PATH + "/trade-journal?limit=1&environment=paper", headers=headers())
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["meta"]["count"] == 2
    assert first["data"][0]["trade_episode_id"] == "e1"
    assert first["data"][0]["requested_qty"] == 4
    assert first["data"][0]["invalidation_conditions"] == "halt"
    assert first["data"][0]["rejects"] == 0
    assert first["data"][0]["coverage"]["telemetry"]["state"] == "complete"
    cursor = first["page_info"]["next_cursor"]
    assert isinstance(cursor, str)
    second = client.get(PATH + "/trade-journal", params={"cursor": cursor, "limit": 1}, headers=headers()).json()
    assert second["data"][0]["trade_episode_id"] == "e0"
    assert second["page_info"]["next_cursor"] is None
    beta = client.get(PATH + "/trade-journal", headers=headers("beta")).json()
    assert [row["trade_episode_id"] for row in beta["data"]] == ["e2"]
    assert client.get(PATH + "/trade-journal/e0", headers=headers("beta")).status_code == 404
    assert client.get(PATH + "/trade-journal/e0?environment=live", headers=headers()).status_code == 404
    detail = client.get(PATH + "/trade-journal/e0", headers=headers())
    assert detail.status_code == 200
    assert detail.json()["data"]["filled_qty"] == 0
    assert all("Authorization" in sent and sent.get("X-tenant-id") in ("alpha", "beta") for _, sent in calls)


@pytest.mark.parametrize("route", ["trade-journal", "trade-journal/e0", "trade-reflections", "trade-patterns"])
def test_auth_tenant_and_persona_guards_run_before_read(owners, route):
    client, calls = owners
    for auth, expected in [({}, 401), (headers(None), 403), (headers(persona_ids=["other"]), 403),
                           (headers(persona_ids=[]), 403), (headers(persona_ids="p1"), 403),
                           ({**headers("beta"), "X-Tenant-Id": "alpha"}, 403)]:
        calls.clear()
        response = client.get(PATH + "/" + route, headers=auth)
        assert response.status_code == expected, response.text
        assert calls == []


def test_saved_reflections_and_patterns_use_existing_tenant_owner(owners):
    client, _ = owners
    for route in ("trade-reflections", "trade-patterns"):
        assert client.get(PATH + "/" + route, headers=headers("beta")).status_code == 403
    refs = client.get(PATH + "/trade-reflections?limit=1", headers=headers()).json()
    assert refs["data"][0]["lesson_candidates"][0]["id"] == "lesson-1"
    assert refs["data"][0]["counterfactuals"][0]["action"] == "wait"
    assert refs["data"][0]["prompt_version"] == "1.0.0"
    assert refs["page_info"]["next_cursor"] == 1
    patterns = client.get(PATH + "/trade-patterns", headers=headers()).json()
    assert [row["reflection_id"] for row in patterns["data"]] == ["pattern-1"]
    empty = client.get(PATH + "/trade-patterns?environment=live", headers=headers()).json()
    assert empty["data"] == [] and empty["meta"]["coverage_state"] == "empty"
    client.cookies.set("pantheon_session", headers()["Authorization"].removeprefix("Bearer "))
    assert client.get(PATH + "/trade-journal").status_code == 200


def test_no_fallback_on_missing_or_malformed_owner(owners, monkeypatch):
    client, _ = owners
    monkeypatch.delenv("PANTHEON_TELEMETRY_API_URL")
    assert client.get(PATH + "/trade-journal", headers=headers()).status_code == 503
    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", "http://test-telemetry")
    for body in ([], {}, {"projections": [{"persona_id": "p1", "tenant_id": "beta", "account_id": "secret"}], "count": 1},
                 {"projections": [], "count": 0, "next_cursor": 7}):
        monkeypatch.setattr(trade_journal, "_http_call", lambda *a, **kw: (200, body))
        response = client.get(PATH + "/trade-journal", headers=headers())
        assert response.status_code == 503
        assert "secret" not in response.text
    monkeypatch.setattr(trade_journal, "_http_call", lambda *a, **kw: (200, {"data": [], "meta": {"tenant_id": "beta"}}))
    assert client.get(PATH + "/trade-reflections", headers=headers()).status_code == 503


@pytest.mark.parametrize("bad", [{"mistakes": None}, {"mistakes": {}}, {"counterfactuals": None},
                                  {"counterfactuals": ["bad"]}, {"lesson_candidates": [None]}])
def test_malformed_nested_reflection_is_dependency_error(owners, monkeypatch, bad):
    client, _ = owners
    artifact = {"persona_id": "p1", "reflection_id": "r1", "trigger": "scheduled_pattern",
                "mistakes": [], "counterfactuals": [], "lesson_candidates": [], **bad}
    monkeypatch.setattr(trade_journal, "_http_call", lambda *a, **kw: (200, {
        "data": [artifact], "meta": {"tenant_id": "alpha"},
    }))
    for route in ("trade-reflections", "trade-patterns"):
        assert client.get(PATH + "/" + route, headers=headers()).status_code == 503


@pytest.mark.parametrize("bad", [{"coverage": "bad"}, {"invalidation_conditions": [1]}])
def test_malformed_nested_episode_is_dependency_error(owners, monkeypatch, bad):
    client, _ = owners
    row = {"persona_id": "p1", "tenant_id": "alpha", "trade_episode_id": "e0", **bad}
    monkeypatch.setattr(trade_journal, "_http_call", lambda *a, **kw: (200, {
        "projections": [row], "count": 1,
    }))
    assert client.get(PATH + "/trade-journal", headers=headers()).status_code == 503
    monkeypatch.setattr(trade_journal, "_http_call", lambda *a, **kw: (200, row))
    assert client.get(PATH + "/trade-journal/e0", headers=headers()).status_code == 503
