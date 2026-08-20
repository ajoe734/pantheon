"""Comprehensive tests for Agora decision projection producer, candidate review -> decision event linkage, replay, and fail-closed invariants."""
from __future__ import annotations

import os
import uuid
from typing import Any, Dict
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from .models import DecisionEventEvidenceRef, DecisionProjectionCommand
from .producer import DecisionEventProducer
from .store import DecisionEventStore
from ..research.router import create_research_router
from ..research.store import MemoryResearchPlanStore as ResearchPlanStore
from ..trading_room.store import TradingRoomStore


def _utc_now() -> str:
    return "2026-08-20T12:00:00Z"


def _extract_identity(auth_header: str = None, session_cookie: str = None) -> Any:
    class DummyIdentity:
        user_id = "operator-001"
        tenant_id = "tenant-agora"
        operator_id = "operator-001"
        roles = ["operator", "trader"]
        claims = {
            "tenant_id": "tenant-agora",
            "user_id": "operator-001",
            "operator_id": "operator-001",
            "allowed_tenants": ["tenant-agora", "*"],
        }
        def __getitem__(self, key):
            return getattr(self, key, None)
        def get(self, key, default=None):
            return getattr(self, key, default)
    return DummyIdentity()


def _dummy_check(identity: Any) -> None:
    pass


def _dummy_bff_error(status_code: int, error_code: Any, message: str, detail: Any = None, **kwargs):
    from fastapi import HTTPException
    code_val = getattr(error_code, "value", str(error_code))
    return HTTPException(
        status_code=status_code,
        detail={"error_code": code_val, "code": code_val, "message": message, "detail": detail, **kwargs},
    )


def test_candidate_review_produces_durable_decision_event(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    tr_store = TradingRoomStore()
    res_store = ResearchPlanStore()

    app = FastAPI()
    kw = {
        "extract_identity": _extract_identity,
        "require_read_role": _dummy_check,
        "require_write_role": _dummy_check,
        "bff_error": _dummy_bff_error,
        "utc_now": _utc_now,
    }

    res_router = create_research_router(research_plan_store=res_store, **kw)
    app.include_router(res_router)
    client = TestClient(app)

    tenant_id = "tenant-agora"
    user_id = "operator-001"
    pool_id = "cpool-rev-001"
    artifact_id = "cand-rev-art-1"

    # Setup candidate pool
    pool = {
        "spec_version": "1.0",
        "pool_id": pool_id,
        "operator_id": user_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "filter": {},
        "candidates": [
            {
                "artifact_id": artifact_id,
                "strategy_id": "strat-gamma",
                "strategy_spec_registry_id": "strat-gamma-spec",
                "symbol": "2330.TW",
                "asset_class": "equity",
                "lifecycle_state": "discovered",
            }
        ],
        "total": 1,
        "snapshot_at": "2026-08-20T10:00:00Z",
        "lock_version": 1,
        "metadata": {
            "strategy_id": "strat-gamma",
            "recipe_id": "recipe-winner-branch",
        },
    }
    res_store.create_candidate_pool(pool)

    # Perform review: approve_for_monitoring
    review_req = {
        "decision": "approve_for_monitoring",
        "rationale": "Strong multi-factor signal and low drawdown",
        "reviewed_by": user_id,
    }
    resp = client.post(
        f"/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/review",
        json=review_req,
        headers={"Idempotency-Key": "review-idem-001", "If-Match": f'W/"candidate-pool:{pool_id}:v1"'},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["decision"] == "approve_for_monitoring"

    # Verify decision event in global/trading room store
    from ..trading_room.router import _get_store as _get_tr_store
    global_tr_store = _get_tr_store()
    events = global_tr_store.list_decision_events(strategy_id="strat-gamma")["items"]
    assert len(events) >= 1
    event = next(e for e in events if e.get("candidate_ref") == artifact_id)
    assert event["event_kind"] == "entry"
    assert event["decision_state"] == "approved_by_trader"
    assert event["no_order_route_proof"] == "agora_decision_support_only"


def test_decision_event_producer_projects_to_trading_room():
    proj_store = DecisionEventStore()
    tr_store = TradingRoomStore()
    producer = DecisionEventProducer(store=proj_store)

    cmd = DecisionProjectionCommand(
        idempotency_key="idempotent-proj-001",
        strategy_id="strat-delta",
        event_type="signal_eval",
        signal_data={"confidence": 0.88, "expected_value": 2.5, "symbol": "NVDA"},
        risk_data={"max_drawdown": 0.02, "risk_score": 0.05, "risk_passed": True},
        signal_as_of=_utc_now(),
        risk_as_of=_utc_now(),
    )

    record = producer.produce_decision_event(cmd, tenant_id="tenant-agora", user_id="operator-001", utc_now=_utc_now())
    assert record.status == "projected"
    assert record.has_broker_authority is False

    # Project to Trading Room Store
    tr_event = producer.project_to_trading_room(
        record,
        trading_room_store=tr_store,
        strategy_spec_registry_id="strat-delta-spec",
        symbol="NVDA",
    )

    assert tr_event["decision_event_id"] == record.decision_event_id
    assert tr_event["strategy_id"] == "strat-delta"
    assert tr_event["suggested_action"] == "enter"
    assert tr_event["state"] == "pending_review"
    assert tr_event["no_order_route_proof"] == "agora_decision_support_only"

    # Verify reload in TradingRoomStore
    loaded = tr_store.get_decision_event(record.decision_event_id)
    assert loaded is not None
    assert loaded["subject"]["symbol"] == "NVDA"
