"""BFF-STRATEGY-SYNTHETIC-STUBS-20261120 (RC6 WP6.2/WP6.3).

Hosted sweep 2026-11-20 (BFF fb8ed951d): GET /bff/strategies?exclude_state=draft
led with two unbound rows - "TW official tw_price_daily TWSE 2330 2026-10-15"
(raw market seed, no rules) and "SIMULATION paper-only SPY anchor
dev-product-20260913T040439Z-..." - above the live Paper strategies. They must
be hidden from the default list with an explicit count, shown on request, and
the detail must say why a stub is a stub.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.meetings.router import create_meetings_router
from services.control_plane.bff.models import utc_now

from services.control_plane.bff.strategies.routes.common import (
    STUB_RAW_MARKET_SEED,
    STUB_SIMULATION_FIXTURE,
    STUB_TEST_PERSONA_PROBE,
    PersonaStateLookup,
    strategy_stub_reason,
)
from services.control_plane.bff.tests.test_strategy_operational_status import (
    HEADERS,
    TOTAL_COUNT,
    _World,
    _build_app,
    _permissive_auth,  # noqa: F401 - autouse fixture
    _spec_entry,
)

TW_SEED_ID = "strat-tw-official:tw_price_daily:TWSE:2330:2026-10-15"
TW_SEED_TITLE = "TW official tw_price_daily TWSE 2330 2026-10-15"
SIM_ID = "strat-src-dev-product-20260913T040439Z-7935481ca72f49629606e01098c3a54e"
SIM_TITLE = "SIMULATION paper-only SPY anchor dev-product-20260913T040439Z-7935481ca72f49629606e01098c3a54e"


def _world_with_stubs() -> _World:
    world = _World()
    world.entries.append(_spec_entry(strategy_id=TW_SEED_ID, title=TW_SEED_TITLE, persona_ids=[],
                                     updated_at="2026-10-16T07:46:19Z", symbols=[]))
    world.entries.append(_spec_entry(strategy_id=SIM_ID, title=SIM_TITLE, persona_ids=[],
                                     updated_at="2026-09-13T13:21:38Z", symbols=[]))
    return world


def _get(monkeypatch, world, query):
    app, _ = _build_app(monkeypatch, world)
    return TestClient(app).get(query, headers=HEADERS).json()


@pytest.mark.parametrize("query", [
    "/bff/strategies?page_size=200",
    "/bff/strategies?exclude_state=draft&page_size=200",
    "/bff/strategies?exclude_state=draft&hide_retired_personas=true&page_size=200",
])
def test_unbound_stubs_hidden_with_counts(monkeypatch, query):
    body = _get(monkeypatch, _world_with_stubs(), query)
    ids = {r["id"] for r in body["items"]}
    assert TW_SEED_ID not in ids and SIM_ID not in ids
    assert body["meta"]["hidden_unbound_stub_strategy_count"] == 2
    assert body["meta"]["include_test_personas"] is False
    # genuine unbound strategies (no stub pattern) stay visible
    assert any(r["operationalStatus"] == "unbound" for r in body["items"])


def test_include_test_personas_shows_stubs(monkeypatch):
    body = _get(monkeypatch, _world_with_stubs(),
                "/bff/strategies?exclude_state=draft&page_size=200&include_test_personas=true")
    ids = {r["id"] for r in body["items"]}
    assert {TW_SEED_ID, SIM_ID} <= ids
    assert body["meta"]["hidden_unbound_stub_strategy_count"] == 0
    assert body["page_info"]["total"] == TOTAL_COUNT + 2


def test_total_excludes_hidden_stubs(monkeypatch):
    body = _get(monkeypatch, _world_with_stubs(), "/bff/strategies?exclude_state=draft&page_size=200")
    assert body["page_info"]["total"] == TOTAL_COUNT


def test_rows_carry_persona_display_names(monkeypatch):
    world = _world_with_stubs()
    pid = next(iter(world.persona_records))
    world.persona_records[pid]["display_name"] = "青禾"
    body = _get(monkeypatch, world, "/bff/strategies?exclude_state=draft&page_size=200&hide_retired_personas=false")
    row = next(r for r in body["items"] if pid in r["personaIds"])
    assert row["persona_display_names"][pid] == "青禾"
    unbound = next(r for r in body["items"] if not r["personaIds"])
    assert unbound["persona_display_names"] == {}


def test_one_persona_directory_read_per_request(monkeypatch):
    world = _world_with_stubs()
    _get(monkeypatch, world, "/bff/strategies?exclude_state=draft&page_size=200")
    assert len(world.directory_reads) == 1


@pytest.mark.parametrize("sid,reason", [(TW_SEED_ID, STUB_RAW_MARKET_SEED), (SIM_ID, STUB_SIMULATION_FIXTURE)])
def test_detail_flags_stub_and_reason(monkeypatch, sid, reason):
    body = _get(monkeypatch, _world_with_stubs(), f"/bff/strategies/{sid}")
    assert body["data"]["is_synthetic_or_stub"] is True
    assert body["data"]["stub_reason"] == reason
    assert body["data"]["persona_display_names"] == {}


def test_detail_of_live_strategy_is_not_a_stub(monkeypatch):
    world = _world_with_stubs()
    sid = world.running_strategy_ids[0]
    data = _get(monkeypatch, world, f"/bff/strategies/{sid}")["data"]
    assert data["is_synthetic_or_stub"] is False and data["stub_reason"] is None
    assert list(data["persona_display_names"]) == data["personaIds"]


def test_stub_reason_is_pure_classification():
    lookup = PersonaStateLookup(available=True, test_persona_ids=frozenset({"p-test"}))
    assert strategy_stub_reason({"strategy_id": "s", "persona_ids": ["p-test"]}, lookup) == STUB_TEST_PERSONA_PROBE
    assert strategy_stub_reason({"strategy_id": "s", "metadata": {"data_scope": "synthetic"}}, lookup) == STUB_TEST_PERSONA_PROBE
    assert strategy_stub_reason({"strategy_id": "strat-tw-official:x"}, lookup) == STUB_RAW_MARKET_SEED
    # a live persona binding is never a stub, whatever the name
    assert strategy_stub_reason({"strategy_id": "strat-tw-official:x", "persona_ids": ["p-live"]}, lookup) is None
    assert strategy_stub_reason({"strategy_id": "s", "title": "Pantheon Dev Paper Release 1"}, lookup) is None


# --- AC4: meetings include_failed forward ---------------------------------------

PERSONA = "persona-6507750312870d03a241"


class _Identity:
    operator_id = "viewer-1"
    claims: Dict[str, Any] = {}


class _FakeClient:
    calls: List[Dict[str, Any]] = []

    def __init__(self, tenant_id: str, actor_id: str) -> None:
        pass

    def list_meetings(self, **kwargs: Any) -> Dict[str, Any]:
        _FakeClient.calls.append(kwargs)
        items = [{
            "meeting_id": "mtg-492d824b116f9ace", "status": "upcoming", "type": "MT-1",
            "chair_persona_id": PERSONA, "execution_state": {"state": "dead_letter", "last_error": "provider timeout"},
            "effective_status": "execution_failed",
        }] if kwargs.get("include_failed") else []
        return {"items": items, "next_cursor": None, "summary": {"dead_letter_count": 1}}


def _meetings_client() -> TestClient:
    app = FastAPI()
    app.include_router(create_meetings_router(
        extract_identity=lambda *_a, **_k: _Identity(),
        require_read_role=lambda *_a, **_k: None,
        require_operator_role=lambda *_a, **_k: None,
        utc_now=utc_now,
        client_factory=lambda t, a: _FakeClient(t, a),
    ))
    return TestClient(app)


def test_include_failed_forwarded_and_dead_letter_preserved() -> None:
    _FakeClient.calls.clear()
    hidden = _meetings_client().get("/bff/meetings?status=upcoming")
    shown = _meetings_client().get("/bff/meetings?status=upcoming&include_failed=true")
    assert hidden.status_code == shown.status_code == 200
    assert [c.get("include_failed") for c in _FakeClient.calls] == [False, True]
    assert hidden.json()["items"] == []
    body = shown.json()
    row = body["items"][0]
    assert row["effective_status"] == "execution_failed"
    assert row["execution_state"]["last_error"] == "provider timeout"
    assert body["data"]["summary"]["dead_letter_count"] == 1


def test_legacy_release_probe_title_is_classified_as_test_probe():
    from services.control_plane.bff.strategies.routes import common
    row = {"id": "s1", "title": "Pantheon Dev Paper Release 123 Attempt 2", "persona_ids": ["p1"]}
    assert common.strategy_stub_reason(row, None) == common.STUB_TEST_PERSONA_PROBE
    row2 = {"id": "s2", "title": "Pantheon Dev Paper Release 3", "persona_ids": ["p1"]}
    assert common.strategy_stub_reason(row2, None) is None
