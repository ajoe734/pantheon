"""Shadow mode (design A4): immutable decision locks through the production
Trading Room decision route, writing into the real Agora owner (AgoraWriteService
+ AgoraStore). Only the Postgres JSON table underneath AgoraStore is replaced by
an in-memory table (no Postgres in CI); its insert_if_absent semantics match.
"""
from __future__ import annotations

import uuid
from unittest import mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.agora.service import AgoraWriteService
from services.agora.shadow_mode import (
    ACTUAL_FILL_KINDS,
    DISPLAY_LABELS,
    verify_checksum,
)
from services.agora.store import AgoraStore
from services.agora.write_authority import AgoraWriteForbiddenError
from services.control_plane.bff.agora.trading_room.router import create_trading_room_router
from services.control_plane.bff.agora.trading_room.store import make_trading_room_store


class _MemoryTable:
    def __init__(self, **_kw) -> None:
        self.rows: dict = {}

    def put(self, record_id, payload):
        self.rows[record_id] = dict(payload)

    def insert_if_absent(self, record_id, payload, **_kw):
        if record_id in self.rows:
            return False, dict(self.rows[record_id])
        self.rows[record_id] = dict(payload)
        return True, dict(payload)

    def get(self, record_id):
        row = self.rows.get(record_id)
        return dict(row) if row is not None else None

    def list_all(self, **_kw):
        return [dict(r) for r in self.rows.values()]


def _owner() -> AgoraWriteService:
    with mock.patch("services.agora.store.PostgresJsonOwnerStore", _MemoryTable):
        return AgoraWriteService(store=AgoraStore(dsn="postgresql://test/none", bootstrap=False))


def _event(event_id: str, **extra) -> dict:
    base = {
        "spec_version": "1.0",
        "decision_event_id": event_id,
        "tenant_id": "tenant-001",
        "user_id": "user-001",
        "event_kind": "entry",
        "origin": "strategy_signal",
        "strategy_id": "strat-001",
        "strategy_spec_registry_id": "reg-001",
        "subject": {"symbol": "2330.TW"},
        "state": "pending_review",
        "triggered_at": "2026-11-16T01:00:00Z",
        "confidence": {"value": 0.7, "basis": "model", "calibration_state": "calibrated", "sample_size": 100},
        "probability": {"target_outcome": "up", "horizon": "5d", "value": 0.6, "ci_lower": 0.5, "ci_upper": 0.7},
        "expected_value": {"horizon": "5d", "unit": "pct_return", "gross": 0.03, "cost": 0.001, "net": 0.029, "downside": -0.02},
        "rationale": [{"claim": "signal", "confidence": 0.7, "evidence_refs": []}],
        "risk_notes": [],
        "evidence_refs": [],
        "invalidation": {"conditions": ["x"], "current_state": "valid", "last_checked_at": "2026-11-16T00:55:00Z"},
        "suggested_action": "enter",
        "no_order_route_proof": "agora_decision_support_only",
    }
    base.update(extra)
    return base


def _hdr(key: str) -> dict:
    return {"Authorization": "Bearer t", "If-Match": "*", "Idempotency-Key": key, "X-Request-Id": f"req-{key}"}


def _app(store, owner, identity: dict) -> TestClient:
    def bff_error(status_code, code, message, reason, **kw):
        return HTTPException(
            status_code=status_code,
            detail={"code": getattr(code, "value", code), "message": message, "reason": reason, **kw},
        )

    def extract_identity(auth, session_cookie=None):
        return dict(identity)

    app = FastAPI()
    app.include_router(
        create_trading_room_router(
            extract_identity=extract_identity,
            require_read_role=lambda _i: None,
            require_write_role=lambda _i: None,
            bff_error=bff_error,
            utc_now=lambda: "2026-11-16T02:00:00Z",
            trading_room_store=store,
            decision_lock_owner=owner,
        )
    )
    return TestClient(app)


TRADER = {"user_id": "user-001", "tenant_id": "tenant-001", "roles": ["trader"]}
OPERATOR = {"user_id": "op-9", "tenant_id": "tenant-001", "roles": ["operator"]}
OTHER_TRADER = {"user_id": "user-002", "tenant_id": "tenant-001", "roles": ["trader"]}
URL = "/bff/agora/trading-room/decision-events/{}"


@pytest.fixture
def world():
    store = make_trading_room_store()
    owner = _owner()
    return store, owner


def _decide(client, event_id, body, key="k1"):
    return client.post(URL.format(event_id) + "/decisions", headers=_hdr(key), json=body)


def test_every_decision_writes_one_lock_with_all_arms(world):
    store, owner = world
    store.upsert_decision_event(_event(
        "evt-1",
        data_cutoff="2026-11-16T00:59:00Z",
        strategy_version_id="sv-7",
        servant_suggestion={"action": "enter", "note": "servant raw text"},
        institutional_view={"persona_id": "inst-1", "stance": "enter"},
    ))
    client = _app(store, owner, TRADER)
    resp = _decide(client, "evt-1", {
        "decision": "approve", "rationale": "我的原始想法",
        "account_id": "acct-77", "broker_ids": ["BRK-1", "BRK-2"],
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["execution_observation_kind"] == "pantheon_paper_proxy"

    lock = client.get(URL.format("evt-1") + "/decision-lock").json()["data"]
    assert lock["human_decision_id"] == data["human_decision_id"]
    assert lock["decision_time"] == "2026-11-16T02:00:00Z"
    assert lock["available_data_cutoff"] == "2026-11-16T00:59:00Z"
    assert lock["decision"] == "enter"
    assert lock["user_id"] == "user-001" and lock["tenant_id"] == "tenant-001"
    assert lock["account_id"] == "acct-77" and lock["broker_ids"] == ["BRK-1", "BRK-2"]
    assert lock["raw_rationale"] == "我的原始想法"
    arms = lock["arms"]
    assert arms["base_strategy"]["strategy_version_id"] == "sv-7"
    assert arms["private_servant"]["available"] is True
    assert arms["private_servant"]["note"] == "servant raw text"
    assert arms["institutional_view"]["persona_id"] == "inst-1"
    assert verify_checksum(lock)
    assert len(owner.store.list_decision_locks()) == 1


def test_second_write_for_same_decision_is_rejected_and_lock_unchanged(world):
    store, owner = world
    store.upsert_decision_event(_event("evt-2"))
    client = _app(store, owner, TRADER)
    assert _decide(client, "evt-2", {"decision": "reject", "rationale": "first"}).status_code == 201
    before = owner.store.list_decision_locks()[0]
    # Direct owner write (same decision) is rejected too.
    from services.agora.shadow_mode import DecisionLockExistsError

    with pytest.raises(DecisionLockExistsError):
        owner.create_decision_lock(dict(before), ["trader"])
    # Production route replay: event already decided -> 409, no second lock.
    again = _decide(client, "evt-2", {"decision": "approve", "rationale": "second"}, key="k2")
    assert again.status_code == 409
    after = owner.store.list_decision_locks()
    assert len(after) == 1 and after[0]["raw_rationale"] == "first"


def test_missing_arms_recorded_unavailable_with_reason_nothing_fabricated(world):
    store, owner = world
    store.upsert_decision_event(_event("evt-3"))
    client = _app(store, owner, TRADER)
    assert _decide(client, "evt-3", {"decision": "approve"}).status_code == 201
    lock = owner.store.list_decision_locks()[0]
    servant = lock["arms"]["private_servant"]
    inst = lock["arms"]["institutional_view"]
    assert servant == {"available": False, "unavailable_reason": "no_servant_suggestion_on_decision_event"}
    assert inst == {"available": False, "unavailable_reason": "no_institutional_view_on_decision_event"}
    assert lock["arms"]["base_strategy"]["strategy_version_id"] is None
    assert lock["arms"]["base_strategy"]["strategy_version_unavailable_reason"] == "event_has_no_strategy_version"
    assert lock["available_data_cutoff"] is None
    assert lock["data_cutoff_unavailable_reason"] == "event_has_no_data_cutoff"
    assert lock["outcome"]["available"] is False
    assert lock["account_id"] is None and lock["account_unavailable_reason"] == "account_not_supplied"


def test_paper_proxy_never_labelled_actual_fill_and_no_order_side_effect(world):
    store, owner = world
    store.upsert_decision_event(_event("evt-4"))
    client = _app(store, owner, TRADER)
    assert _decide(client, "evt-4", {"decision": "approve"}).status_code == 201
    lock = owner.store.list_decision_locks()[0]
    assert lock["execution_observation_kind"] == "pantheon_paper_proxy"
    assert lock["execution_observation_kind"] not in ACTUAL_FILL_KINDS
    assert lock["actual_fill_claimed"] is False and lock["fill_refs"] == []
    assert "實際成交" not in lock["execution_display_label"].replace("實際成交（已驗證）", "")
    assert lock["execution_display_label"] == DISPLAY_LABELS["pantheon_paper_proxy"]
    assert lock["no_order_route_proof"] == "agora_decision_lock_record_only"


@pytest.mark.parametrize(
    "verdict,kind",
    [
        ("reject", "declared_no_trade"),
        ("defer", "declared_no_trade"),
        ("shadow", "declared_no_trade"),
    ],
)
def test_no_trade_verdicts_label_declared_no_trade(world, verdict, kind):
    store, owner = world
    store.upsert_decision_event(_event("evt-5"))
    client = _app(store, owner, TRADER)
    resp = _decide(client, "evt-5", {"decision": verdict})
    assert resp.status_code == 201
    lock = owner.store.list_decision_locks()[0]
    assert lock["execution_observation_kind"] == kind
    assert lock["arms"]["human_actual"]["execution_observation_kind"] == kind
    assert resp.json()["data"]["intent_ref"] is None


def test_shadow_decision_rejects_non_no_trade_observation_kind(world):
    store, owner = world
    store.upsert_decision_event(_event("evt-shadow-invalid"))
    client = _app(store, owner, TRADER)
    resp = _decide(
        client,
        "evt-shadow-invalid",
        {"decision": "shadow", "execution_observation_kind": "pantheon_paper_proxy"},
    )
    assert resp.status_code == 422
    assert owner.store.list_decision_locks() == []


def test_shadow_verdict_marks_event_decided_on_copy_on_read_store_and_blocks_later_approve():
    import copy
    from services.control_plane.bff.agora.trading_room.store import TradingRoomStore

    class CopyOnReadStore(TradingRoomStore):
        def get_decision_event(self, decision_event_id: str):
            return copy.deepcopy(super().get_decision_event(decision_event_id))

        def list_decision_events(self, **kwargs):
            return copy.deepcopy(super().list_decision_events(**kwargs))

    store = CopyOnReadStore()
    owner = _owner()
    store.upsert_decision_event(_event("evt-shadow-cor"))
    client = _app(store, owner, TRADER)
    resp = _decide(client, "evt-shadow-cor", {"decision": "shadow", "rationale": "shadow on durable store"}, key="k-cor-1")
    assert resp.status_code == 201, resp.text
    stored = store.get_decision_event("evt-shadow-cor")
    assert stored is not None
    assert stored["state"] == "decided"
    assert stored["decision_state"] == "handed_off"

    later_approve = _decide(client, "evt-shadow-cor", {"decision": "approve", "rationale": "late approve"}, key="k-cor-2")
    assert later_approve.status_code == 409, later_approve.text
    assert later_approve.json()["detail"]["reason"] == "decision_event_not_actionable"
    assert not store._intents
    assert len(owner.store.list_decision_locks()) == 1


def test_broker_fill_kinds_cannot_be_declared_through_decision_route(world):
    store, owner = world
    store.upsert_decision_event(_event("evt-6"))
    client = _app(store, owner, TRADER)
    resp = _decide(client, "evt-6", {"decision": "approve", "execution_observation_kind": "verified_broker_fill"})
    assert resp.status_code == 422
    assert owner.store.list_decision_locks() == []
    # decision was not recorded either
    assert store.get_decision_event("evt-6")["state"] == "pending_review"


def test_reads_operator_sees_any_trader_sees_only_own(world):
    store, owner = world
    store.upsert_decision_event(_event("evt-7"))
    assert _decide(_app(store, owner, TRADER), "evt-7", {"decision": "approve"}).status_code == 201
    lock_url = URL.format("evt-7") + "/decision-lock"
    assert _app(store, owner, TRADER).get(lock_url).status_code == 200
    assert _app(store, owner, OPERATOR).get(lock_url).status_code == 200
    assert _app(store, owner, OTHER_TRADER).get(lock_url).status_code == 404
    assert _app(store, owner, {**OPERATOR, "tenant_id": "tenant-xyz"}).get(lock_url).status_code == 404


def test_unconfigured_owner_fails_closed_and_records_no_decision(world):
    store, _ = world
    store.upsert_decision_event(_event("evt-8"))
    client = _app(store, None, TRADER)
    resp = _decide(client, "evt-8", {"decision": "approve"})
    assert resp.status_code == 503
    assert store.get_decision_event("evt-8")["state"] == "pending_review"


def test_owner_write_authority_denies_roles_outside_matrix(world):
    store, owner = world
    store.upsert_decision_event(_event("evt-9"))
    client = _app(store, owner, {**TRADER, "roles": ["viewer"]})
    assert _decide(client, "evt-9", {"decision": "approve"}).status_code == 403
    assert owner.store.list_decision_locks() == []
    with pytest.raises(AgoraWriteForbiddenError):
        owner.create_decision_lock({"human_decision_id": "x"}, ["viewer"])
