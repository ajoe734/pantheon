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
