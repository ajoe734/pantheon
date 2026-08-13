"""Tests for Agora decision projection producer, store, idempotency, tenant isolation, and fail-closed rules."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
import pytest

from .models import DecisionEventEvidenceRef, DecisionProjectionCommand
from .producer import DecisionEventProducer
from .store import DecisionEventStore


def test_decision_projection_idempotency():
    store = DecisionEventStore()
    producer = DecisionEventProducer(store=store)

    cmd = DecisionProjectionCommand(
        idempotency_key="idempotent-key-001",
        strategy_id="strat-alpha",
        event_type="signal_eval",
        signal_data={"confidence": 0.85, "expected_value": 1.25},
        risk_data={"max_drawdown": 0.03, "risk_score": 0.1, "risk_passed": True},
        signal_as_of=datetime.now(timezone.utc).isoformat(),
        risk_as_of=datetime.now(timezone.utc).isoformat(),
    )

    event1 = producer.produce_decision_event(cmd, tenant_id="tenant-1", user_id="user-1")
    event2 = producer.produce_decision_event(cmd, tenant_id="tenant-1", user_id="user-1")

    assert event1.decision_event_id == event2.decision_event_id
    assert event1.created_at == event2.created_at
    assert len(store.list_events(tenant_id="tenant-1", user_id="user-1")) == 1


def test_two_tenant_negative_isolation():
    store = DecisionEventStore()
    producer = DecisionEventProducer(store=store)

    cmd = DecisionProjectionCommand(
        idempotency_key="key-tenant-A",
        strategy_id="strat-alpha",
        event_type="signal_eval",
        signal_data={"confidence": 0.9, "expected_value": 2.0},
        risk_data={"max_drawdown": 0.02, "risk_score": 0.05, "risk_passed": True},
        signal_as_of=datetime.now(timezone.utc).isoformat(),
        risk_as_of=datetime.now(timezone.utc).isoformat(),
    )

    event_a = producer.produce_decision_event(cmd, tenant_id="tenant-A", user_id="user-A")

    # Tenant B tries to access Tenant A's event by ID
    event_b_query = store.get_event(tenant_id="tenant-B", user_id="user-B", decision_event_id=event_a.decision_event_id)
    assert event_b_query is None

    # Tenant B lists events
    events_b = store.list_events(tenant_id="tenant-B", user_id="user-B")
    assert len(events_b) == 0

    # Tenant B tries to query by Tenant A's idempotency key
    key_query = store.get_by_idempotency_key(tenant_id="tenant-B", user_id="user-B", idempotency_key="key-tenant-A")
    assert key_query is None


def test_stale_or_missing_risk_data_fails_closed():
    store = DecisionEventStore()
    producer = DecisionEventProducer(store=store)

    # 1. Stale timestamp
    stale_time = "2020-01-01T00:00:00Z"
    cmd_stale = DecisionProjectionCommand(
        idempotency_key="key-stale",
        strategy_id="strat-alpha",
        event_type="signal_eval",
        signal_data={"confidence": 0.95, "expected_value": 5.0},
        risk_data={"max_drawdown": 0.01, "risk_score": 0.01, "risk_passed": True},
        signal_as_of=stale_time,
        risk_as_of=datetime.now(timezone.utc).isoformat(),
        max_staleness_sec=300.0,
    )
    rec_stale = producer.produce_decision_event(cmd_stale, tenant_id="tenant-1", user_id="user-1")
    assert rec_stale.status in ("invalidated", "stale")
    assert rec_stale.probability == 0.0
    assert rec_stale.expected_value == 0.0
    assert "STALE_SIGNAL_DATA" in rec_stale.invalidation_conditions

    # 2. Risk check failed
    cmd_failed_risk = DecisionProjectionCommand(
        idempotency_key="key-failed-risk",
        strategy_id="strat-alpha",
        event_type="signal_eval",
        signal_data={"confidence": 0.95, "expected_value": 5.0},
        risk_data={"max_drawdown": 0.50, "risk_score": 0.9, "risk_passed": False},
        signal_as_of=datetime.now(timezone.utc).isoformat(),
        risk_as_of=datetime.now(timezone.utc).isoformat(),
    )
    rec_failed_risk = producer.produce_decision_event(cmd_failed_risk, tenant_id="tenant-1", user_id="user-1")
    assert rec_failed_risk.status == "invalidated"
    assert rec_failed_risk.probability == 0.0
    assert "RISK_CHECK_FAILED" in rec_failed_risk.invalidation_conditions


def test_restart_recovery():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        store1 = DecisionEventStore(storage_filepath=tmp_path)
        producer1 = DecisionEventProducer(store=store1)

        cmd = DecisionProjectionCommand(
            idempotency_key="key-restart-001",
            strategy_id="strat-beta",
            event_type="signal_eval",
            signal_data={"confidence": 0.88, "expected_value": 1.5},
            risk_data={"max_drawdown": 0.03, "risk_score": 0.1, "risk_passed": True},
            signal_as_of=datetime.now(timezone.utc).isoformat(),
            risk_as_of=datetime.now(timezone.utc).isoformat(),
        )

        evt_saved = producer1.produce_decision_event(cmd, tenant_id="t-1", user_id="u-1")

        # Instantiate brand new store loading from same disk path
        store2 = DecisionEventStore(storage_filepath=tmp_path)
        evt_reloaded = store2.get_event("t-1", "u-1", evt_saved.decision_event_id)

        assert evt_reloaded is not None
        assert evt_reloaded.decision_event_id == evt_saved.decision_event_id
        assert evt_reloaded.strategy_id == "strat-beta"
        assert evt_reloaded.idempotency_key == "key-restart-001"
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_absence_of_broker_order_authority():
    store = DecisionEventStore()
    producer = DecisionEventProducer(store=store)

    cmd = DecisionProjectionCommand(
        idempotency_key="key-no-order-001",
        strategy_id="strat-alpha",
        event_type="signal_eval",
        signal_data={"confidence": 0.90, "expected_value": 2.0},
        risk_data={"max_drawdown": 0.02, "risk_score": 0.05, "risk_passed": True},
        signal_as_of=datetime.now(timezone.utc).isoformat(),
        risk_as_of=datetime.now(timezone.utc).isoformat(),
    )

    record = producer.produce_decision_event(cmd, tenant_id="tenant-1", user_id="user-1")
    assert record.has_broker_authority is False
    assert not hasattr(producer, "submit_order")
    assert not hasattr(producer, "execute_trade")
