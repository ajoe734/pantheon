"""Focused selection and restart-persistence tests for the trading-room store."""
from __future__ import annotations

import os
import sys
import uuid

import pytest


from bff.agora.trading_room import store as store_module


def test_factory_defaults_to_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(store_module.BACKEND_ENV, raising=False)
    assert type(store_module.make_trading_room_store()) is store_module.TradingRoomStore


def test_factory_selects_postgres_without_logging_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    class FakePostgresStore(store_module.TradingRoomStore):
        def __init__(self, *, dsn: str, schema: str) -> None:
            super().__init__()
            captured.update(dsn=dsn, schema=schema)

    monkeypatch.setattr(store_module, "PostgresTradingRoomStore", FakePostgresStore)
    result = store_module.make_trading_room_store(
        backend="postgres", dsn="postgresql://secret@example/pantheon", schema="agora_test"
    )
    assert isinstance(result, FakePostgresStore)
    assert captured == {
        "dsn": "postgresql://secret@example/pantheon",
        "schema": "agora_test",
    }


def test_postgres_store_survives_new_instance_and_preserves_proof() -> None:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    schema = f"agora_tr_{uuid.uuid4().hex[:12]}"
    first = store_module.PostgresTradingRoomStore(dsn=dsn, schema=schema)
    event = {
        "decision_event_id": f"evt-{uuid.uuid4().hex}",
        "event_kind": "strategy_signal",
        "state": "pending",
        "triggered_at": "2026-07-12T00:00:00Z",
        "no_order_route_proof": "agora_decision_support_only",
    }
    first.upsert_decision_event(event)

    restarted = store_module.PostgresTradingRoomStore(dsn=dsn, schema=schema)
    assert restarted.get_decision_event(event["decision_event_id"]) == event
    with pytest.raises(ValueError, match="no_order_route_proof"):
        restarted.upsert_decision_event({
            **event,
            "decision_event_id": f"evt-{uuid.uuid4().hex}",
            "no_order_route_proof": "unsafe",
        })


@pytest.mark.parametrize("decision", ["approve", "modify"])
def test_mounted_readback_survives_postgres_restart(decision) -> None:
    from .test_trading_room import _client, _make_event, _make_handoff, _write_headers

    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    schema = f"agora_readback_{uuid.uuid4().hex[:12]}"

    def store():
        return store_module.PostgresTradingRoomStore(dsn=dsn, schema=schema)

    first = store()
    first.upsert_decision_event(_make_event(event_id="persisted"))
    client = _client(first)
    url = "/bff/agora/trading-room/decision-events/persisted"
    etag = client.get(url, headers=_write_headers()).headers["etag"]
    result = client.post(url + "/decisions", headers={**_write_headers(), "If-Match": etag}, json={"decision": decision})
    assert result.status_code == 201, result.text
    intent_id = result.json()["data"]["intent_ref"]
    client = _client(store())
    event = client.get(url, headers=_write_headers())
    assert event.json()["intent_ref"] == intent_id
    assert event.headers["etag"] != etag
    assert client.post(url + "/decisions", headers={**_write_headers("stale"), "If-Match": etag}, json={"decision": decision}).status_code == 412
    intent_url = f"/bff/agora/trading-intents/{intent_id}"
    assert client.get(intent_url, headers=_write_headers()).json()["status"] == "draft"
    assert client.post(intent_url + "/handoffs", headers=_write_headers("handoff"), json=_make_handoff(intent_id=intent_id)).status_code == 202
    detail = _client(store()).get(intent_url, headers=_write_headers()).json()
    assert detail["status"] == "submitted"
    assert detail["handoffs"][0]["handoff_id"] == "hof-001"
    assert detail["handoffs"][0]["no_order_route_proof"] == "agora_request_only_no_order_route"
    for scope in ({"tenant_id": "foreign"}, {"user_id": "foreign"}):
        foreign = _client(store(), **scope)
        assert foreign.get(url, headers=_write_headers()).status_code == 404
        assert foreign.get(intent_url, headers=_write_headers()).status_code == 404
        assert foreign.post(intent_url + "/withdraw", headers=_write_headers("withdraw")).status_code == 404
    assert _client(store()).post(intent_url + "/withdraw", headers=_write_headers("withdraw")).status_code == 200
    detail = _client(store()).get(intent_url, headers=_write_headers()).json()
    assert detail["status"] == detail["handoffs"][0]["state"] == "withdrawn"
