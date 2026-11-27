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


def test_shadow_decision_lock_settled_by_real_evaluation_on_owner_route():
    """T6 end-to-end: 送 Shadow writes an immutable declared_no_trade lock,
    real services/evaluation settles all 4 arms, and the owner-scoped shadow
    comparison route serves the settled comparison.
    """
    from services.evaluation.main import AttributionOwnerRuntime
    from services.evaluation.tests.test_shadow_settlement import load_recorded_twse_rows

    store = make_trading_room_store()
    owner = _owner()
    store.upsert_decision_event(_event(
        "evt-shadow-e2e",
        subject={"symbol": "2330.TW"},
        data_cutoff="2024-10-01T05:30:00Z",
        strategy_version_id="sv-10",
        suggested_action="enter",
        servant_suggestion={"action": "enter", "persona_id": "servant-lin"},
        institutional_view={"persona_id": "inst-1", "stance": "exit"},
    ))

    class _RealEvalClient:
        def __init__(self, eval_client: TestClient, tenant_id: str) -> None:
            self._eval_client = eval_client
            self._tenant_id = tenant_id

        def get_shadow(self, decision_id: str) -> Dict[str, Any]:
            resp = self._eval_client.get(
                f"/api/attribution/shadow/{decision_id}",
                headers={"Authorization": "Bearer svc-tok", "X-Tenant-Id": self._tenant_id},
            )
            if resp.status_code == 404:
                raise AttributionOwnerNotFoundError(decision_id)
            assert resp.status_code == 200, resp.text
            return resp.json()

    writer = _client(store, owner, TRADER, _FakeAttribution())
    decide_resp = writer.post(
        "/bff/agora/trading-room/decision-events/evt-shadow-e2e/decisions",
        headers=_hdr("k-shadow-e2e"),
        json={"decision": "shadow", "rationale": "不下單但追蹤", "account_id": "acct-shadow-1"},
    )
    assert decide_resp.status_code == 201, decide_resp.text
    decide_data = decide_resp.json()["data"]
    assert decide_data["decision"] == "shadow"
    assert decide_data["intent_ref"] is None
    assert decide_data["execution_observation_kind"] == "declared_no_trade"

    lock = owner.get_decision_lock_for_event(
        "evt-shadow-e2e",
        tenant_id="tenant-001",
        user_id="user-001",
        actor_roles=["trader"],
    )
    assert lock is not None
    assert lock["execution_observation_kind"] == "declared_no_trade"
    assert lock["arms"]["human_actual"]["execution_observation_kind"] == "declared_no_trade"
    assert lock["immutable"] is True

    from services.evaluation.main import create_evaluation_app

    rt = AttributionOwnerRuntime(
        service_tokens=["svc-tok"],
        market_rows=load_recorded_twse_rows(),
        decision_locks=[dict(lock)],
    )
    eval_client = TestClient(create_evaluation_app(rt))
    service = get_active_trading_room_service()
    service.attribution_client_factory = lambda tenant_id, _actor_id: _RealEvalClient(eval_client, tenant_id)

    comp_resp = writer.get(URL.format("evt-shadow-e2e"))
    assert comp_resp.status_code == 200, comp_resp.text
    comp = comp_resp.json()
    assert comp["status"] == "ok"
    assert comp["empty"] is False
    assert comp["data"]["comparison_state"] == "settled"
    assert comp["data"]["decision_id"] == lock["human_decision_id"]
    arms = {a["arm"]: a for a in comp["data"]["arms"]}
    assert list(arms.keys()) == [*CANONICAL, "trader_imitation"]
    assert arms["human_actual"]["settlement_source"] == "declared_no_trade"
    assert arms["human_actual"]["outcome"]["action"] == "no_trade"
    assert arms["human_actual"]["outcome"]["return_pct"] == 0.0
    assert arms["human_actual"]["outcome"]["cost_pct"] == 0.0
    for arm_name in ("private_servant", "base_strategy", "committee_variant"):
        assert arms[arm_name]["settlement_source"] == "pantheon_paper_proxy"
        assert arms[arm_name]["outcome"] is not None
        assert arms[arm_name]["outcome"]["return_pct"] != 0.0
        assert arms[arm_name]["settled_at"] is not None
    assert arms["trader_imitation"]["outcome"] is None
    assert arms["trader_imitation"]["unavailable_reason"] == "no_trader_imitation_candidate_on_decision_event"


def test_trader_imitation_comparison_not_enough_decisions_under_30(decided):
    """Under 30 decisions, GET /bff/agora/trading-room/imitation-comparison returns not_enough_decisions."""
    store, owner, _lock = decided
    client = _client(store, owner, TRADER, _FakeAttribution("settled"))
    resp = client.get("/bff/agora/trading-room/imitation-comparison")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "empty"
    assert body["empty"] is True
    assert body["comparison_state"] == "not_enough_decisions"
    data = body["data"]
    assert data["comparison_state"] == "not_enough_decisions"
    assert data["minimum_decisions_required"] == 30
    assert data["decision_count"] == 1
    assert data["remaining_decisions_needed"] == 29
    assert data["candidate"] is None
    assert data["candidates"] == []


def test_trader_imitation_comparison_ready_with_bootstrap_luck_and_owner_isolation():
    """T10 end-to-end: with >= 30 approved decisions and a trained behaviour candidate,
    each new decision lock carries and settles trader_imitation, the owner-scoped
    imitation-comparison route returns 1,000-resample bootstrap intervals with 0-containing
    intervals labelled luck (可能是運氣), and another trader cannot see the caller's data.
    """
    from services.evaluation.main import AttributionOwnerRuntime, create_evaluation_app
    from services.evaluation.tests.test_shadow_settlement import load_recorded_twse_rows

    store = make_trading_room_store()
    owner = _owner()
    candidate = {
        "candidate_id": "cand-trader-imitation-u1-strat1-v3",
        "version_id": "0.1.3",
        "candidate_version": 3,
        "tenant_id": "tenant-001",
        "user_id": "user-001",
        "strategy_id": "strat-001",
        "status": "processed",
        "decision_count": 32,
        "policy_weights": {
            "policy_type": "linear_softmax",
            "observation_dim": 5,
            "action_labels": ["enter", "no_trade"],
            "weights": [
                [3.0, 1.0, 0.5, 0.0, 1.0],
                [-2.0, -1.0, 0.0, 0.0, -1.0],
            ],
            "bias": [1.5, -1.0],
        },
    }
    older_candidates = [
        candidate,
        {**candidate, "candidate_id": "cand-trader-imitation-u1-strat1-v2", "version_id": "0.1.2", "candidate_version": 2},
        {**candidate, "candidate_id": "cand-trader-imitation-u1-strat1-v1", "version_id": "0.1.1", "candidate_version": 1},
        {**candidate, "candidate_id": "cand-trader-imitation-u1-strat1-v0", "version_id": "0.1.0", "candidate_version": 0},
    ]

    def candidate_reader(*, tenant_id: str, user_id: str, strategy_id=None):
        if tenant_id == "tenant-001" and user_id == "user-001":
            return {
                "candidate": candidate,
                "candidates": older_candidates,
                "decision_count": 32,
                "training_approved": True,
            }
        return {
            "candidate": None,
            "candidates": [],
            "decision_count": 0,
            "training_approved": False,
        }

    for idx, verdict in enumerate(("approve", "shadow"), start=1):
        evt_id = f"evt-imit-{idx}"
        store.upsert_decision_event(_event(
            evt_id,
            subject={"symbol": "2330.TW"},
            data_cutoff="2024-10-01T05:30:00Z",
            strategy_id="strat-001",
            strategy_version_id="sv-10",
            suggested_action="enter",
            servant_suggestion={"action": "enter", "persona_id": "servant-lin"},
            institutional_view={"persona_id": "inst-1", "stance": "exit"},
        ))

    writer = _client(store, owner, TRADER, _FakeAttribution())
    service = get_active_trading_room_service()
    service.imitation_candidate_reader = candidate_reader

    for idx, verdict in enumerate(("approve", "shadow"), start=1):
        evt_id = f"evt-imit-{idx}"
        resp = writer.post(
            f"/bff/agora/trading-room/decision-events/{evt_id}/decisions",
            headers=_hdr(f"k-imit-{idx}"),
            json={"decision": verdict, "rationale": f"decision-{idx}", "account_id": "acct-imit-1"},
        )
        assert resp.status_code == 201, resp.text

    lock1 = owner.get_decision_lock_for_event("evt-imit-1", tenant_id="tenant-001", user_id="user-001", actor_roles=["trader"])
    lock2 = owner.get_decision_lock_for_event("evt-imit-2", tenant_id="tenant-001", user_id="user-001", actor_roles=["trader"])
    assert lock1 is not None and lock2 is not None
    assert lock1["arms"]["trader_imitation"]["available"] is True
    assert lock1["arms"]["trader_imitation"]["action"] == "enter"
    assert lock1["arms"]["trader_imitation"]["candidate_id"] == "cand-trader-imitation-u1-strat1-v3"

    rt = AttributionOwnerRuntime(
        service_tokens=["svc-tok"],
        market_rows=load_recorded_twse_rows(),
        decision_locks=[dict(lock1), dict(lock2)],
    )
    eval_client = TestClient(create_evaluation_app(rt))

    class _RealEvalClient:
        def __init__(self, tenant_id: str) -> None:
            self._tenant_id = tenant_id

        def get_shadow(self, decision_id: str) -> Dict[str, Any]:
            r = eval_client.get(
                f"/api/attribution/shadow/{decision_id}",
                headers={"Authorization": "Bearer svc-tok", "X-Tenant-Id": self._tenant_id},
            )
            if r.status_code == 404:
                raise AttributionOwnerNotFoundError(decision_id)
            assert r.status_code == 200, r.text
            return r.json()

    service.attribution_client_factory = lambda tenant_id, _actor_id: _RealEvalClient(tenant_id)

    # 1. Per-decision shadow-comparison route includes settled trader_imitation arm
    shadow_resp = writer.get(URL.format("evt-imit-1"))
    assert shadow_resp.status_code == 200, shadow_resp.text
    shadow_arms = {a["arm"]: a for a in shadow_resp.json()["data"]["arms"]}
    assert list(shadow_arms.keys()) == [*CANONICAL, "trader_imitation"]
    assert shadow_arms["trader_imitation"]["settlement_source"] == "pantheon_paper_proxy"
    assert shadow_arms["trader_imitation"]["outcome"]["return_pct"] == shadow_arms["human_actual"]["outcome"]["return_pct"]

    # 2. Owner-scoped trader imitation comparison route returns agreement + gap + 1000-resample luck interval
    imit_resp = writer.get("/bff/agora/trading-room/imitation-comparison", params={"strategy_id": "strat-001"})
    assert imit_resp.status_code == 200, imit_resp.text
    imit_body = imit_resp.json()
    assert imit_body["status"] == "ok"
    assert imit_body["empty"] is False
    assert imit_body["comparison_state"] == "ready"
    data = imit_body["data"]
    assert data["comparison_state"] == "ready"
    assert data["decision_count"] == 32
    assert data["minimum_decisions_required"] == 30
    assert data["compared_decision_count"] == 2
    assert data["settled_comparison_count"] == 2
    assert data["agreement_rate"] == 0.5
    assert data["agreement_interval"]["resamples"] == 1000
    assert data["outcome_gap_interval"]["resamples"] == 1000
    assert data["outcome_gap_interval"]["zero_inside"] is True
    assert data["outcome_gap_label"] == "luck"
    assert data["outcome_gap_label_zh"] == "可能是運氣"
    assert data["finding_stated"] is False
    assert len(data["differences"]) == 1
    assert data["candidate"]["candidate_id"] == "cand-trader-imitation-u1-strat1-v3"
    assert data["candidate_count"] == 3
    assert data["max_candidates_kept"] == 3

    # 3. Strict owner isolation: OTHER_TRADER cannot see TRADER's decisions or candidates
    other_client = _client(store, owner, OTHER_TRADER, _FakeAttribution())
    other_service = get_active_trading_room_service()
    other_service.imitation_candidate_reader = candidate_reader
    other_resp = other_client.get("/bff/agora/trading-room/imitation-comparison")
    assert other_resp.status_code == 200, other_resp.text
    other_body = other_resp.json()
    assert other_body["comparison_state"] == "not_enough_decisions"
    assert other_body["data"]["decision_count"] == 0
    assert other_body["data"]["compared_decision_count"] == 0
    assert other_body["data"]["candidate"] is None
