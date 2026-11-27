"""AGORA-SHADOW-COMPARISON-OWNER-ROUTE-20261127: the trader's shadow comparison
is served on an owner-scoped Agora route, reading the one composed Agora
decision-lock owner and forwarding settlement from the attribution owner.

The Agora owner is the real AgoraWriteService/AgoraStore with only the Postgres
JSON table replaced by memory (same harness as test_agora_shadow_mode).
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.trading_room.router import create_trading_room_router
from services.control_plane.bff.agora.trading_room.service import get_active_trading_room_service
from services.control_plane.bff.agora.trading_room.store import make_trading_room_store
from services.control_plane.bff.attribution.client import (
    AttributionOwnerNotFoundError,
    AttributionOwnerUnavailableError,
)
from services.control_plane.bff.tests.test_agora_shadow_mode import (
    OPERATOR,
    OTHER_TRADER,
    TRADER,
    _event,
    _hdr,
    _owner,
)

URL = "/bff/agora/trading-room/decision-events/{}/shadow-comparison"
CANONICAL = ["human_actual", "private_servant", "base_strategy", "committee_variant"]


class _FakeAttribution:
    def __init__(self, behaviour: str = "settled") -> None:
        self.behaviour = behaviour
        self.calls: List[Tuple[str, str, str]] = []

    def factory(self, tenant_id: str, actor_id: str) -> "_FakeAttribution":
        self._ctx = (tenant_id, actor_id)
        return self

    def get_shadow(self, decision_id: str) -> Dict[str, Any]:
        self.calls.append((*self._ctx, decision_id))
        if self.behaviour == "not_found":
            raise AttributionOwnerNotFoundError(decision_id)
        if self.behaviour == "unavailable":
            raise AttributionOwnerUnavailableError("attribution_owner_unavailable")
        return {
            "decision_id": decision_id,
            "arms": [
                {"arm": arm, "settlement_source": "pantheon_paper_proxy",
                 "outcome": {"net_return": 0.01 * (i + 1)}, "settled_at": "2026-11-20T00:00:00Z",
                 "unavailable_reason": None}
                for i, arm in enumerate(CANONICAL)
            ],
            "data_cutoff": "2026-11-16T00:59:00Z",
            "cost_model": {"commission_rate": 0.001425},
            "observation_window": "5d",
            "settled_at": "2026-11-20T00:00:00Z",
        }


def _client(store, owner, identity, attribution: _FakeAttribution) -> TestClient:
    def bff_error(status_code, code, message, reason, **kw):
        return HTTPException(
            status_code=status_code,
            detail={"code": getattr(code, "value", code), "message": message, "reason": reason, **kw},
        )

    app = FastAPI()
    app.include_router(
        create_trading_room_router(
            extract_identity=lambda auth, session_cookie=None: dict(identity),
            require_read_role=lambda _i: None,
            require_write_role=lambda _i: None,
            bff_error=bff_error,
            utc_now=lambda: "2026-11-16T02:00:00Z",
            trading_room_store=store,
            decision_lock_owner=owner,
        )
    )
    service = get_active_trading_room_service()
    assert service.decision_lock_owner is owner
    service.attribution_client_factory = attribution.factory
    return TestClient(app)


@pytest.fixture
def decided():
    """evt-1 decided by TRADER (lock written); evt-2 owned by TRADER, undecided."""
    store = make_trading_room_store()
    owner = _owner()
    store.upsert_decision_event(_event(
        "evt-1",
        data_cutoff="2026-11-16T00:59:00Z",
        servant_suggestion={"action": "enter"},
        institutional_view={"persona_id": "inst-1", "stance": "enter"},
    ))
    store.upsert_decision_event(_event("evt-2"))
    writer = _client(store, owner, TRADER, _FakeAttribution())
    resp = writer.post(
        "/bff/agora/trading-room/decision-events/evt-1/decisions",
        headers=_hdr("k1"),
        json={"decision": "approve", "rationale": "mine", "account_id": "acct-77"},
    )
    assert resp.status_code == 201, resp.text
    lock = owner.get_decision_lock_for_event("evt-1", tenant_id="tenant-001", user_id="user-001", actor_roles=["trader"])
    assert lock is not None
    return store, owner, dict(lock)


def test_owner_reads_four_canonical_arms_with_settlement(decided):
    store, owner, lock = decided
    attribution = _FakeAttribution("settled")
    resp = _client(store, owner, TRADER, attribution).get(URL.format("evt-1"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["empty"] is False
    data = body["data"]
    assert data["decision_event_id"] == "evt-1"
    assert data["decision_id"] == lock["human_decision_id"]
    assert data["comparison_state"] == "settled"
    assert [a["arm"] for a in data["arms"]] == CANONICAL
    arms = {a["arm"]: a for a in data["arms"]}
    assert arms["human_actual"]["decision_detail"]["available"] is True
    assert arms["human_actual"]["decision_detail"]["trading_room_verdict"] == "approve"
    assert arms["committee_variant"]["decision_detail"].get("available") is not False
    for arm in CANONICAL:
        assert arms[arm]["settlement_source"] == "pantheon_paper_proxy"
        assert arms[arm]["settled_at"] == "2026-11-20T00:00:00Z"
    # Attribution is keyed by the lock's human_decision_id, read as the caller in the caller's tenant.
    assert attribution.calls == [("tenant-001", "user-001", lock["human_decision_id"])]
    # Trader audience: no identity block, account only.
    assert "trader_identity" not in data
    assert data["account_id"] == "acct-77"
    assert body["meta"]["sources"] == {"attribution": "ok", "agora": "ok"}


def test_owner_sees_pending_settlement_state(decided):
    store, owner, _lock = decided
    body = _client(store, owner, TRADER, _FakeAttribution("not_found")).get(URL.format("evt-1")).json()
    assert body["status"] == "degraded"
    assert body["data"]["comparison_state"] == "pending_settlement"
    assert [a["arm"] for a in body["data"]["arms"]] == CANONICAL
    assert all(a["unavailable_reason"] == "shadow_not_settled_or_unknown" for a in body["data"]["arms"])


def test_attribution_outage_degrades_without_fabricating(decided):
    store, owner, _lock = decided
    body = _client(store, owner, TRADER, _FakeAttribution("unavailable")).get(URL.format("evt-1")).json()
    assert body["status"] == "degraded"
    assert body["data"]["comparison_state"] == "unavailable"
    assert all(a["outcome"] is None and a["settlement_source"] is None for a in body["data"]["arms"])


@pytest.mark.parametrize("identity", [OTHER_TRADER, OPERATOR], ids=["other_trader", "operator"])
def test_non_owner_gets_non_enumerating_404(decided, identity):
    store, owner, _lock = decided
    attribution = _FakeAttribution("settled")
    client = _client(store, owner, identity, attribution)
    foreign = client.get(URL.format("evt-1"))
    unknown = client.get(URL.format("evt-does-not-exist"))
    assert foreign.status_code == 404
    assert unknown.status_code == 404
    # Same body for a foreign decision and an unknown id (modulo the echoed id).
    assert foreign.json()["detail"]["reason"] == unknown.json()["detail"]["reason"] == "decision_not_found"
    assert foreign.json()["detail"]["message"].replace("evt-1", "X") == unknown.json()["detail"]["message"].replace(
        "evt-does-not-exist", "X"
    )
    # The attribution owner is never asked about a decision the caller does not own.
    assert attribution.calls == []


def test_owner_undecided_event_is_typed_empty_state(decided):
    store, owner, _lock = decided
    attribution = _FakeAttribution("settled")
    resp = _client(store, owner, TRADER, attribution).get(URL.format("evt-2"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "empty"
    assert body["empty"] is True
    assert body["data"]["comparison_state"] == "no_decision_recorded"
    assert [a["arm"] for a in body["data"]["arms"]] == CANONICAL
    assert attribution.calls == []


def test_unconfigured_owner_is_503_not_a_substitute():
    store = make_trading_room_store()
    store.upsert_decision_event(_event("evt-1"))
    resp = _client(store, None, TRADER, _FakeAttribution()).get(URL.format("evt-1"))
    assert resp.status_code == 503
    assert resp.json()["detail"]["reason"] == "decision_lock_owner_unavailable"


def test_route_is_read_only():
    router = create_trading_room_router(
        extract_identity=lambda *a, **k: dict(TRADER),
        require_read_role=lambda _i: None,
        bff_error=lambda *a, **k: HTTPException(500),
        utc_now=lambda: "2026-11-16T02:00:00Z",
        trading_room_store=make_trading_room_store(),
    )
    methods = {
        m for r in router.routes if getattr(r, "path", "").endswith("/shadow-comparison") for m in r.methods
    }
    assert methods == {"GET"}
