"""Unit tests for Agora trading-room aggregate and decision-event queue.

Covers:
  - TradingRoomStore: upsert/get/list decision events, record trader decisions
  - Pydantic models field-aligned with v4 schemas
  - Router creation (smoke test)
  - Safety invariants: no_order_route_proof, no order routing
  - Regression: model_dump(exclude_none=True) passes v4 jsonschema
  - Regression: store rejects invalid no_order_route_proof (D1 invariant)
  - Regression: pagination token does not repeat previous page's last item
"""
from __future__ import annotations

import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from bff.agora.trading_room.store import TradingRoomStore, make_trading_room_store
from bff.agora.trading_room.router import (
    TradingDecisionEvent,
    TradingRoomAggregate,
    QueueSummary,
    RiskSummary,
    TradingRoomStrategy,
    PendingEventCounts,
    ConfidenceAssessment,
    ProbabilityForecast,
    ExpectedValue,
    RationaleItem,
    RiskNote,
    InvalidationState,
    EvidenceRef,
    TraderDecisionRequest,
    GovernedIntentHandoffRequest,
    create_trading_room_router,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_event(
    event_kind: str = "entry",
    state: str = "pending_review",
    event_id: str | None = None,
    strategy_id: str = "strat-001",
) -> dict:
    return {
        "spec_version": "1.0",
        "decision_event_id": event_id or str(uuid.uuid4()),
        "event_kind": event_kind,
        "origin": "strategy_signal",
        "strategy_id": strategy_id,
        "strategy_spec_registry_id": "reg-001",
        "subject": {"symbol": "AAPL"},
        "state": state,
        "triggered_at": "2026-06-22T10:00:00Z",
        "confidence": {
            "value": 0.75,
            "basis": "model",
            "calibration_state": "calibrated",
            "sample_size": 120,
        },
        "probability": {
            "target_outcome": "breakout",
            "horizon": "5d",
            "value": 0.65,
            "ci_lower": 0.55,
            "ci_upper": 0.75,
        },
        "expected_value": {
            "horizon": "5d",
            "unit": "pct_return",
            "gross": 0.03,
            "cost": 0.001,
            "net": 0.029,
            "downside": -0.02,
        },
        "rationale": [
            {
                "claim": "Momentum signal crossed threshold",
                "confidence": 0.75,
                "evidence_refs": [],
            }
        ],
        "risk_notes": [
            {
                "severity": "watch",
                "domain": "market_risk",
                "summary": "Earnings release in 3 days",
            }
        ],
        "evidence_refs": [
            {
                "ref_type": "evidence_bundle",
                "ref_id": "evb-001",
                "summary": "Backtest pack",
            }
        ],
        "invalidation": {
            "conditions": ["price < 150", "VIX > 30"],
            "current_state": "valid",
            "last_checked_at": "2026-06-22T09:55:00Z",
        },
        "suggested_action": "enter",
        "no_order_route_proof": "agora_decision_support_only",
    }


def _make_intent(intent_id: str = "int-001") -> dict:
    return {
        "spec_version": "1.0",
        "intent_id": intent_id,
        "operator_id": "user-001",
        "intent_type": "entry_interest",
        "direction": "neutral",
        "subject": {"symbol": "AAPL", "strategy_ref": "strat-001"},
        "rationale": "Decision-support intent only",
        "linked_event_ids": ["evt-001"],
        "learning_eligible": True,
        "no_order_route_proof": "agora_intent_record_only",
        "expressed_at": "2026-06-22T10:00:00Z",
    }


def _make_handoff(
    *,
    intent_id: str = "int-001",
    handoff_id: str = "hof-001",
    requested_stage: str = "paper",
    handoff_type: str = "paper_validation_request",
    target_queue: str | None = None,
    state: str = "submitted",
) -> dict:
    body = {
        "spec_version": "1.0",
        "handoff_id": handoff_id,
        "intent_id": intent_id,
        "requested_stage": requested_stage,
        "handoff_type": handoff_type,
        "state": state,
        "strategy_id": "strat-001",
        "strategy_spec_registry_id": "reg-001",
        "requested_by": {"actor_type": "trader", "actor_ref": "user-001"},
        "evidence_refs": [{"ref_type": "evidence_bundle", "ref_id": "evb-001"}],
        "no_order_route_proof": "agora_request_only_no_order_route",
        "created_at": "2026-06-22T10:00:00Z",
        "action_proposal": {
            "action": "enter",
            "symbol": "AAPL",
            "direction": "neutral",
            "non_binding": True,
        },
    }
    if target_queue is not None:
        body["target_queue"] = target_queue
    return body


def _write_headers(idempotency_key: str = "idem-001") -> dict:
    return {
        "Authorization": "Bearer test",
        "If-Match": "*",
        "Idempotency-Key": idempotency_key,
        "X-Request-Id": f"req-{idempotency_key}",
    }


def _client(store: TradingRoomStore, *, user_id: str = "user-001", tenant_id: str = "tenant-001") -> TestClient:
    def _bff_error(status_code: int, code: object, message: str, reason: str, **kw) -> HTTPException:
        code_value = getattr(code, "value", code)
        return HTTPException(
            status_code=status_code,
            detail={"code": code_value, "message": message, "reason": reason, **kw},
        )

    def _extract_identity(_auth: str | None, session_cookie: str | None = None) -> dict:
        if not _auth and not session_cookie:
            raise _bff_error(401, "AUTH_REQUIRED", "Missing credentials", "no_credentials")
        return {"user_id": user_id, "tenant_id": tenant_id, "session_id": "session-001"}

    def _require_read_role(_identity: dict) -> None:
        pass

    def _utc_now() -> str:
        return "2026-06-22T10:00:00Z"

    app = FastAPI()
    app.include_router(
        create_trading_room_router(
            extract_identity=_extract_identity,
            require_read_role=_require_read_role,
            bff_error=_bff_error,
            utc_now=_utc_now,
            trading_room_store=store,
        )
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------

def test_store_upsert_and_get():
    store = make_trading_room_store()
    event = _make_event(event_id="evt-001")
    store.upsert_decision_event(event)
    result = store.get_decision_event("evt-001")
    assert result is not None
    assert result["decision_event_id"] == "evt-001"
    print("✅ store: upsert and get decision event")


def test_store_list_empty():
    store = make_trading_room_store()
    page = store.list_decision_events()
    assert page["items"] == []
    assert page["page_info"]["has_more"] is False
    assert page["page_info"]["page_size"] == 0
    print("✅ store: list returns empty on fresh store")


def test_store_list_filter_by_kind():
    store = make_trading_room_store()
    for kind in ("entry", "add", "reduce", "exit", "review"):
        store.upsert_decision_event(_make_event(event_kind=kind))
    page = store.list_decision_events(event_kind="entry")
    assert len(page["items"]) == 1
    assert page["items"][0]["event_kind"] == "entry"
    print("✅ store: list filters by event_kind")


def test_store_list_filter_by_state():
    store = make_trading_room_store()
    store.upsert_decision_event(_make_event(event_id="e1", state="pending_review"))
    store.upsert_decision_event(_make_event(event_id="e2", state="approaching"))
    page = store.list_decision_events(state="approaching")
    assert len(page["items"]) == 1
    assert page["items"][0]["state"] == "approaching"
    print("✅ store: list filters by state")


def test_store_record_trader_decision_approve():
    store = make_trading_room_store()
    event = _make_event(event_id="evt-approve", state="pending_review")
    store.upsert_decision_event(event)
    store.record_trader_decision("evt-approve", {
        "decision_record_id": "rec-001",
        "decision": "approve",
        "decided_at": "2026-06-22T10:05:00Z",
    })
    updated = store.get_decision_event("evt-approve")
    assert updated["state"] == "decided"
    assert updated["decision_state"] == "approved_by_trader"
    print("✅ store: approve decision transitions event to decided/approved_by_trader")


def test_store_record_trader_decision_reject():
    store = make_trading_room_store()
    event = _make_event(event_id="evt-reject", state="pending_review")
    store.upsert_decision_event(event)
    store.record_trader_decision("evt-reject", {
        "decision_record_id": "rec-002",
        "decision": "reject",
        "decided_at": "2026-06-22T10:06:00Z",
    })
    updated = store.get_decision_event("evt-reject")
    assert updated["state"] == "decided"
    assert updated["decision_state"] == "rejected_by_trader"
    print("✅ store: reject decision transitions event to decided/rejected_by_trader")


def test_store_record_trader_decision_defer():
    store = make_trading_room_store()
    event = _make_event(event_id="evt-defer", state="pending_review")
    store.upsert_decision_event(event)
    store.record_trader_decision("evt-defer", {
        "decision_record_id": "rec-003",
        "decision": "defer",
        "decided_at": "2026-06-22T10:07:00Z",
    })
    updated = store.get_decision_event("evt-defer")
    assert updated["decision_state"] == "deferred"
    print("✅ store: defer decision transitions to deferred")


def test_store_intent_upsert_and_get():
    store = make_trading_room_store()
    intent = _make_intent("int-001")
    store.upsert_intent(intent)
    result = store.get_intent("int-001")
    assert result is not None
    assert result["intent_id"] == "int-001"
    assert result["no_order_route_proof"] == "agora_intent_record_only"
    assert store.get_intent_state("int-001") == "draft"
    print("✅ store: upsert and get trading intent")


def test_store_get_missing_returns_none():
    store = make_trading_room_store()
    assert store.get_decision_event("nonexistent") is None
    assert store.get_intent("nonexistent") is None
    print("✅ store: get missing returns None")


# ---------------------------------------------------------------------------
# Pydantic model tests
# ---------------------------------------------------------------------------

def test_trading_decision_event_model():
    event = _make_event(event_id="evt-model")
    model = TradingDecisionEvent(**event)
    assert model.no_order_route_proof == "agora_decision_support_only"
    assert model.event_kind == "entry"
    assert model.state == "pending_review"
    assert model.confidence.value == 0.75
    assert model.probability.value == 0.65
    assert model.expected_value.net == 0.029
    assert len(model.rationale) == 1
    assert len(model.risk_notes) == 1
    assert len(model.evidence_refs) == 1
    assert model.invalidation.current_state == "valid"
    print("✅ model: TradingDecisionEvent validates all required fields")


def test_trading_decision_event_no_order_route_proof_is_const():
    """no_order_route_proof must be 'agora_decision_support_only' always."""
    event = _make_event()
    model = TradingDecisionEvent(**event)
    assert model.no_order_route_proof == "agora_decision_support_only"
    print("✅ model: no_order_route_proof is always agora_decision_support_only")


def test_trading_room_aggregate_model():
    now = "2026-06-22T10:00:00Z"
    agg = TradingRoomAggregate(
        spec_version="1.0",
        user_scope_ref="operator:user-001",
        strategies=[],
        queue_summary=QueueSummary(entry=2, add=1, reduce=0, exit=0, review=3),
        top_decision_events=[],
        position_summaries=[],
        risk_summary=RiskSummary(state="normal"),
        snapshot_at=now,
        data_cutoff=now,
    )
    assert agg.queue_summary.entry == 2
    assert agg.risk_summary.state == "normal"
    print("✅ model: TradingRoomAggregate validates required fields")


def test_confidence_and_probability_are_distinct():
    """SD D4: confidence ≠ probability — both must carry their own fields."""
    event = _make_event()
    model = TradingDecisionEvent(**event)
    assert hasattr(model.confidence, "basis")
    assert hasattr(model.confidence, "calibration_state")
    assert hasattr(model.probability, "target_outcome")
    assert hasattr(model.probability, "horizon")
    print("✅ model: confidence and probability are distinct field groups")


def test_ev_gross_cost_net_downside():
    """SD D4: EV must carry gross, cost, net, downside."""
    event = _make_event()
    model = TradingDecisionEvent(**event)
    ev = model.expected_value
    assert ev.gross == 0.03
    assert ev.cost == 0.001
    assert ev.net == 0.029
    assert ev.downside == -0.02
    print("✅ model: expected_value carries gross/cost/net/downside")


def test_all_event_kinds_accepted():
    """All five event_kind values must be accepted."""
    for kind in ("entry", "add", "reduce", "exit", "review"):
        model = TradingDecisionEvent(**_make_event(event_kind=kind))
        assert model.event_kind == kind
    print("✅ model: all five event_kind values accepted")


def test_trader_decision_request():
    req = TraderDecisionRequest(decision="approve", rationale="looks good")
    assert req.decision == "approve"
    print("✅ model: TraderDecisionRequest validates")


def test_governed_intent_handoff_no_order_route_proof():
    """GovernedIntentHandoff.no_order_route_proof must be agora_request_only."""
    req = GovernedIntentHandoffRequest(
        handoff_id="hof-001",
        intent_id="int-001",
        requested_stage="paper",
        handoff_type="paper_validation_request",
        state="submitted",
        strategy_id="strat-001",
        strategy_spec_registry_id="reg-001",
        requested_by={"actor_type": "trader", "actor_ref": "user-001"},
        evidence_refs=[],
        no_order_route_proof="agora_request_only_no_order_route",
        created_at="2026-06-22T10:00:00Z",
    )
    assert req.no_order_route_proof == "agora_request_only_no_order_route"
    print("✅ model: GovernedIntentHandoff no_order_route_proof validated")


# ---------------------------------------------------------------------------
# AG-BE-TR-002: governed TradingIntent / handoff route semantics
# ---------------------------------------------------------------------------

def test_approve_decision_persists_schema_valid_trading_intent():
    import jsonschema

    store = make_trading_room_store()
    store.upsert_decision_event(_make_event(event_id="evt-approve-intent"))
    client = _client(store)

    resp = client.post(
        "/bff/agora/trading-room/decision-events/evt-approve-intent/decisions",
        headers=_write_headers("idem-decision-001"),
        json={"decision": "approve", "rationale": "Approve as non-binding intent"},
    )
    assert resp.status_code == 201, resp.text
    intent_id = resp.json()["data"]["intent_ref"]
    assert intent_id

    intent = store.get_intent(intent_id)
    assert intent is not None
    assert intent["no_order_route_proof"] == "agora_intent_record_only"
    assert store.get_intent_state(intent_id) == "draft"
    with open(_TRADING_INTENT_SCHEMA_PATH) as f:
        schema = json.load(f)
    jsonschema.validate(intent, schema)
    print("✅ route: approve decision persists schema-valid TradingIntent without order route")


def test_decision_requires_idempotency_key():
    store = make_trading_room_store()
    store.upsert_decision_event(_make_event(event_id="evt-no-idem"))
    client = _client(store)

    headers = _write_headers("unused")
    headers.pop("Idempotency-Key")
    resp = client.post(
        "/bff/agora/trading-room/decision-events/evt-no-idem/decisions",
        headers=headers,
        json={"decision": "approve"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["reason"] == "missing_idempotency_key"
    print("✅ route: decision write requires Idempotency-Key")


def test_submit_handoff_paper_persists_request_only_record():
    import jsonschema

    store = make_trading_room_store()
    store.upsert_intent(_make_intent("int-paper"))
    client = _client(store)

    resp = client.post(
        "/bff/agora/trading-intents/int-paper/handoffs",
        headers=_write_headers("idem-handoff-paper"),
        json=_make_handoff(intent_id="int-paper", handoff_id="hof-paper"),
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()["data"]
    assert data["requested_stage"] == "paper"
    assert data["target_queue"] == "management_governance"
    assert data["state"] == "submitted"
    assert data["no_order_route_proof"] == "agora_request_only_no_order_route"

    handoff = store.get_handoff("hof-paper")
    assert handoff is not None
    assert handoff["state"] == "submitted"
    assert handoff["target_queue"] == "management_governance"
    assert store.get_intent_state("int-paper") == "submitted"
    with open(_GOVERNED_HANDOFF_SCHEMA_PATH) as f:
        schema = json.load(f)
    jsonschema.validate(handoff, schema)
    print("✅ route: paper handoff persists request-only governed handoff")


def test_canary_and_live_handoffs_are_promotion_review_request_only():
    for stage in ("canary", "live"):
        store = make_trading_room_store()
        intent_id = f"int-{stage}"
        handoff_id = f"hof-{stage}"
        store.upsert_intent(_make_intent(intent_id))
        client = _client(store)

        resp = client.post(
            f"/bff/agora/trading-intents/{intent_id}/handoffs",
            headers=_write_headers(f"idem-handoff-{stage}"),
            json=_make_handoff(
                intent_id=intent_id,
                handoff_id=handoff_id,
                requested_stage=stage,
                handoff_type="promotion_review_request",
            ),
        )
        assert resp.status_code == 202, resp.text
        data = resp.json()["data"]
        assert data["requested_stage"] == stage
        assert data["handoff_type"] == "promotion_review_request"
        assert data["target_queue"] == "promotion_review"
        assert data["no_order_route_proof"] == "agora_request_only_no_order_route"
    print("✅ route: canary/live handoffs are promotion-review requests only")


def test_handoff_rejects_mismatched_stage_type():
    store = make_trading_room_store()
    store.upsert_intent(_make_intent("int-mismatch"))
    client = _client(store)

    resp = client.post(
        "/bff/agora/trading-intents/int-mismatch/handoffs",
        headers=_write_headers("idem-handoff-mismatch"),
        json=_make_handoff(
            intent_id="int-mismatch",
            handoff_id="hof-mismatch",
            requested_stage="paper",
            handoff_type="promotion_review_request",
        ),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["reason"] == "stage_handoff_type_mismatch"
    print("✅ route: handoff rejects stage/type mismatch")


def test_handoff_requires_idempotency_key():
    store = make_trading_room_store()
    store.upsert_intent(_make_intent("int-no-idem"))
    client = _client(store)

    headers = _write_headers("unused")
    headers.pop("Idempotency-Key")
    resp = client.post(
        "/bff/agora/trading-intents/int-no-idem/handoffs",
        headers=headers,
        json=_make_handoff(intent_id="int-no-idem", handoff_id="hof-no-idem"),
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["reason"] == "missing_idempotency_key"
    print("✅ route: handoff write requires Idempotency-Key")


def test_withdraw_marks_pending_handoff_withdrawn():
    store = make_trading_room_store()
    store.upsert_intent(_make_intent("int-withdraw"))
    client = _client(store)

    submit = client.post(
        "/bff/agora/trading-intents/int-withdraw/handoffs",
        headers=_write_headers("idem-handoff-withdraw"),
        json=_make_handoff(intent_id="int-withdraw", handoff_id="hof-withdraw"),
    )
    assert submit.status_code == 202, submit.text

    withdraw = client.post(
        "/bff/agora/trading-intents/int-withdraw/withdraw",
        headers=_write_headers("idem-withdraw"),
    )
    assert withdraw.status_code == 200, withdraw.text
    data = withdraw.json()["data"]
    assert data["state"] == "withdrawn"
    assert data["withdrawn_handoff_ids"] == ["hof-withdraw"]
    assert store.get_intent_state("int-withdraw") == "withdrawn"
    assert store.get_handoff("hof-withdraw")["state"] == "withdrawn"
    print("✅ route: withdraw marks pending governed handoff withdrawn")


# ---------------------------------------------------------------------------
# AG-BE-DYNUI-001: V11 Trading Room workspace proposals and workspace routes
# ---------------------------------------------------------------------------

def _workspace_schema_validate(payload: dict) -> None:
    import jsonschema
    from pathlib import Path

    schema_path = Path(_WORKSPACE_SCHEMA_PATH).resolve()
    with schema_path.open() as f:
        schema = json.load(f)
    chart_schema_path = schema_path.parent / "v2" / "chart_spec_v1.schema.json"
    with chart_schema_path.open() as f:
        chart_schema = json.load(f)
    resolver = jsonschema.RefResolver(
        base_uri=schema_path.parent.as_uri() + "/",
        referrer=schema,
        store={
            chart_schema_path.as_uri(): chart_schema,
            chart_schema.get("$id"): chart_schema,
        },
    )
    jsonschema.Draft7Validator(schema, resolver=resolver).validate(payload)


def _create_proposal_response(client: TestClient, body: dict | None = None):
    resp = client.post(
        "/bff/agora/strategies/strat-wb/trading-room/proposals",
        headers={"Authorization": "Bearer test", "Idempotency-Key": f"idem-proposal-{uuid.uuid4()}"},
        json=body or {"strategyVersion": "V4", "personalizationHints": {"density": "compact"}},
    )
    assert resp.status_code == 201, resp.text
    return resp


def _create_proposal(client: TestClient) -> dict:
    resp = _create_proposal_response(client)
    return resp.json()["data"]


def _accept_workspace(client: TestClient, proposal: dict) -> tuple[dict, str]:
    resp = client.post(
        f"/bff/agora/strategies/strat-wb/trading-room/proposals/{proposal['proposalId']}/accept",
        headers={"Authorization": "Bearer test", "Idempotency-Key": f"idem-accept-{uuid.uuid4()}"},
        json={"expectedStatus": "preview"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["workspace"], resp.headers["etag"]


def test_workspace_proposal_returns_complete_v11_view_set_and_schema_valid():
    store = make_trading_room_store()
    client = _client(store)
    proposal = _create_proposal(client)

    assert proposal["strategyId"] == "strat-wb"
    assert proposal["strategyVersion"] == "V4"
    assert proposal["status"] == "preview"
    assert [view["id"] for view in proposal["views"]] == [
        "strategy_overview",
        "candidates_entry",
        "winner_branch_intelligence",
        "related_party_flow_migration",
        "event_lead",
        "positions_exit",
        "evidence_monitoring",
    ]
    assert proposal["rationale"]
    assert proposal["dataAvailability"]["status"] == "partial"
    assert proposal["warnings"]
    assert proposal["personalizationApplied"]["status"] == "applied"
    assert all(view["widgetCount"] == len(view["widgets"]) for view in proposal["views"])
    assert all(widget["widgetType"] and widget["chartSpec"]["kind"] for view in proposal["views"] for widget in view["widgets"])
    _workspace_schema_validate(proposal)
    print("✅ workspace proposal: complete V11 view set and schema-valid payload")


def test_workspace_proposal_preserves_generator_metadata_on_create_and_get():
    store = make_trading_room_store()
    client = _client(store)
    resp = _create_proposal_response(
        client,
        {
            "strategyVersion": "V4",
            "tradingRoomReady": True,
            "evidenceRefs": ["ev-wb-v4-001"],
            "dataFreshness": {
                "agora.candidate.members": {
                    "status": "complete",
                    "dataCutoff": "2026-06-28T23:00:00Z",
                }
            },
            "personalizationHints": {
                "density": "compact",
                "javascript": "alert(1)",
            },
        },
    )
    body = resp.json()
    proposal = body["data"]
    generator = body["meta"]["generator"]

    assert generator["status"] == "completed"
    assert generator["evidenceRefs"] == ["ev-wb-v4-001"]
    assert generator["dataFreshness"]["agora.candidate.members"]["dataCutoff"] == "2026-06-28T23:00:00Z"
    assert proposal["personalizationApplied"]["items"] == [{"key": "density", "value": "compact"}]
    assert any("Unsafe personalization hints ignored" in warning for warning in proposal["warnings"])
    candidate_source = next(
        source for source in proposal["dataAvailability"]["sources"]
        if source["dataSource"] == "agora.candidate.members"
    )
    assert candidate_source["status"] == "complete"
    assert "ev-wb-v4-001" in candidate_source["reason"]

    get_resp = client.get(
        f"/bff/agora/strategies/strat-wb/trading-room/proposals/{proposal['proposalId']}",
        headers={"Authorization": "Bearer test"},
    )
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["meta"]["generator"]["evidenceRefs"] == ["ev-wb-v4-001"]
    print("✅ workspace proposal generator metadata: create/get preserve evidence and freshness")


def test_workspace_proposal_get_and_accept_materializes_active_workspace():
    store = make_trading_room_store()
    client = _client(store)
    proposal = _create_proposal(client)

    get_resp = client.get(
        f"/bff/agora/strategies/strat-wb/trading-room/proposals/{proposal['proposalId']}",
        headers={"Authorization": "Bearer test"},
    )
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.headers["etag"].startswith('"tr-proposal:')
    assert get_resp.json()["data"]["proposalId"] == proposal["proposalId"]

    workspace, etag = _accept_workspace(client, proposal)
    assert workspace["status"] == "active"
    assert workspace["generatedBy"] == "trading_servant"
    assert workspace["dashboardVersion"] == 1
    assert workspace["activeViewId"] == "strategy_overview"
    assert len(workspace["views"]) == 7
    assert etag.startswith('"tr-workspace:')
    _workspace_schema_validate(workspace)
    print("✅ workspace accept: preview proposal materializes active workspace")


def test_workspace_layout_requires_etag_and_supports_remove_restore():
    store = make_trading_room_store()
    client = _client(store)
    workspace, etag = _accept_workspace(client, _create_proposal(client))
    workspace_id = workspace["id"]
    widget_id = "overview_candidate_funnel"

    missing = client.patch(
        f"/bff/agora/trading-room/workspaces/{workspace_id}/layout",
        headers={"Authorization": "Bearer test"},
        json={"operations": [{"kind": "move_widget", "widgetId": widget_id, "payload": {"x": 1}}]},
    )
    assert missing.status_code == 428, missing.text

    moved = client.patch(
        f"/bff/agora/trading-room/workspaces/{workspace_id}/layout",
        headers={"Authorization": "Bearer test", "If-Match": etag, "Idempotency-Key": "idem-layout-move"},
        json={"operations": [{"kind": "move_widget", "widgetId": widget_id, "payload": {"x": 1, "y": 1}}]},
    )
    assert moved.status_code == 200, moved.text
    moved_workspace = moved.json()["data"]
    assert moved_workspace["dashboardVersion"] == 2
    moved_widget = next(w for v in moved_workspace["views"] for w in v["widgets"] if w["id"] == widget_id)
    assert moved_widget["placement"]["x"] == 1
    assert moved_widget["placement"]["y"] == 1

    stale = client.patch(
        f"/bff/agora/trading-room/workspaces/{workspace_id}/layout",
        headers={"Authorization": "Bearer test", "If-Match": etag, "Idempotency-Key": "idem-layout-stale"},
        json={"operations": [{"kind": "resize_widget", "widgetId": widget_id, "payload": {"width": 5}}]},
    )
    assert stale.status_code == 412, stale.text

    remove = client.patch(
        f"/bff/agora/trading-room/workspaces/{workspace_id}/layout",
        headers={"Authorization": "Bearer test", "If-Match": moved.headers["etag"], "Idempotency-Key": "idem-layout-remove"},
        json={"operations": [{"kind": "remove_widget", "widgetId": widget_id, "payload": {}}]},
    )
    assert remove.status_code == 200, remove.text
    removed_widget = next(w for v in remove.json()["data"]["views"] for w in v["widgets"] if w["id"] == widget_id)
    assert removed_widget["visible"] is False

    restore = client.patch(
        f"/bff/agora/trading-room/workspaces/{workspace_id}/layout",
        headers={"Authorization": "Bearer test", "If-Match": remove.headers["etag"], "Idempotency-Key": "idem-layout-restore"},
        json={"operations": [{"kind": "add_registered_widget", "payload": {"widgetId": widget_id}}]},
    )
    assert restore.status_code == 200, restore.text
    restored_widget = next(w for v in restore.json()["data"]["views"] for w in v["widgets"] if w["id"] == widget_id)
    assert restored_widget["visible"] is True
    print("✅ workspace layout: ETag, stale-write, remove, and restore semantics")


def test_workspace_view_and_widget_mutations_are_registry_validated():
    store = make_trading_room_store()
    client = _client(store)
    workspace, etag = _accept_workspace(client, _create_proposal(client))
    workspace_id = workspace["id"]
    view = dict(workspace["views"][0])
    view["id"] = "custom_evidence"
    view["title"] = "Custom Evidence"
    view["order"] = 8
    view["widgets"] = []
    view["widgetCount"] = 0

    add_view = client.post(
        f"/bff/agora/trading-room/workspaces/{workspace_id}/views",
        headers={"Authorization": "Bearer test", "If-Match": etag, "Idempotency-Key": "idem-add-view"},
        json={"viewSpec": view},
    )
    assert add_view.status_code == 201, add_view.text

    widget = dict(workspace["views"][0]["widgets"][0])
    widget["id"] = "custom_candidate_funnel"
    add_widget = client.post(
        f"/bff/agora/trading-room/workspaces/{workspace_id}/widgets",
        headers={"Authorization": "Bearer test", "If-Match": add_view.headers["etag"], "Idempotency-Key": "idem-add-widget"},
        json={"viewId": "custom_evidence", "widgetSpec": widget},
    )
    assert add_widget.status_code == 201, add_widget.text
    custom_view = next(v for v in add_widget.json()["data"]["views"] if v["id"] == "custom_evidence")
    assert custom_view["widgetCount"] == 1

    bad_widget = dict(widget)
    bad_widget["id"] = "bad_widget"
    bad_widget["widgetType"] = "not_allowlisted"
    bad = client.post(
        f"/bff/agora/trading-room/workspaces/{workspace_id}/widgets",
        headers={"Authorization": "Bearer test", "If-Match": add_widget.headers["etag"], "Idempotency-Key": "idem-add-widget-bad"},
        json={"viewId": "custom_evidence", "widgetSpec": bad_widget},
    )
    assert bad.status_code == 422, bad.text
    assert "not found in widget_registry.v1" in bad.text
    print("✅ workspace view/widget mutation: add routes and registry validation")


def test_workspace_rejects_servant_direct_patch_and_code_injection():
    store = make_trading_room_store()
    client = _client(store)
    workspace, etag = _accept_workspace(client, _create_proposal(client))
    workspace_id = workspace["id"]
    widget = workspace["views"][0]["widgets"][0]
    widget_id = widget["id"]

    servant = client.patch(
        f"/bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}",
        headers={"Authorization": "Bearer test", "If-Match": etag, "Idempotency-Key": "idem-servant-direct"},
        json={"initiatedBy": "trading_servant", "title": "Servant direct mutation"},
    )
    assert servant.status_code == 409, servant.text
    assert servant.json()["detail"]["reason"] == "servant_direct_widget_patch_not_allowed"

    fresh = client.get(
        f"/bff/agora/trading-room/workspaces/{workspace_id}",
        headers={"Authorization": "Bearer test"},
    )
    bad_chart = dict(widget["chartSpec"])
    bad_chart["options"] = {"formatter": "<script>alert(1)</script>"}
    injected = client.patch(
        f"/bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}",
        headers={"Authorization": "Bearer test", "If-Match": fresh.headers["etag"], "Idempotency-Key": "idem-code-injection"},
        json={"chartSpec": bad_chart},
    )
    assert injected.status_code == 422, injected.text
    assert "Forbidden content pattern" in injected.text
    print("✅ workspace widget patch: servant direct mutation and code injection rejected")


def test_workspace_cross_user_read_is_forbidden():
    store = make_trading_room_store()
    owner_client = _client(store, user_id="owner-user", tenant_id="tenant-001")
    other_client = _client(store, user_id="other-user", tenant_id="tenant-001")
    workspace, _etag = _accept_workspace(owner_client, _create_proposal(owner_client))

    resp = other_client.get(
        f"/bff/agora/trading-room/workspaces/{workspace['id']}",
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["reason"] == "cross_user_workspace_access_forbidden"
    print("✅ workspace scope: cross-user reads are forbidden")


def _make_revised_widget(widget: dict, *, title_suffix: str = "Revised") -> dict:
    proposed = json.loads(json.dumps(widget))
    proposed["title"] = f"{widget['title']} {title_suffix}"
    proposed["purpose"] = f"{widget['purpose']} Revised for faster comparison."
    proposed["whyIncluded"] = f"{widget['whyIncluded']} Revision keeps the same allowlisted data source."
    proposed["chartSpec"] = {
        **proposed["chartSpec"],
        "kind": "bar",
        "encodings": {
            "x": {"field": "label", "type": "nominal"},
            "y": {"field": "value", "type": "quantitative"},
        },
    }
    return proposed


def test_widget_revision_proposal_apply_preserves_before_after_and_records_version():
    store = make_trading_room_store()
    client = _client(store)
    workspace, etag = _accept_workspace(client, _create_proposal(client))
    workspace_id = workspace["id"]
    widget = next(w for v in workspace["views"] for w in v["widgets"] if w["id"] == "overview_candidate_funnel")
    proposed = _make_revised_widget(widget, title_suffix="Bar")

    create = client.post(
        f"/bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget['id']}/revision-proposals",
        headers={"Authorization": "Bearer test", "Idempotency-Key": "idem-widget-revision-create"},
        json={
            "instruction": "改成長條圖，方便快速比較候選狀態。",
            "proposedSpec": proposed,
            "rationale": "目前漏斗適合流程探索，長條圖較適合快速比較各狀態數量。",
            "warnings": ["兩個候選狀態仍為推定資料。"],
            "dataAvailability": "partial",
        },
    )
    assert create.status_code == 201, create.text
    proposal = create.json()["data"]
    assert proposal["status"] == "preview"
    assert proposal["beforeSpec"] == widget
    assert proposal["proposedSpec"]["title"].endswith("Bar")
    assert proposal["rationale"]
    assert proposal["warnings"]
    assert proposal["dataAvailability"] == "partial"
    _workspace_schema_validate(proposal)

    accept = client.post(
        f"/bff/agora/trading-room/widget-revision-proposals/{proposal['id']}/accept",
        headers={"Authorization": "Bearer test", "If-Match": etag, "Idempotency-Key": "idem-widget-revision-accept"},
        json={"acceptanceAction": "apply"},
    )
    assert accept.status_code == 200, accept.text
    data = accept.json()["data"]
    assert data["proposal"]["status"] == "accepted"
    assert data["proposal"]["beforeSpec"] == widget
    assert data["proposal"]["proposedSpec"] == proposed
    assert data["workspace"]["dashboardVersion"] == 2
    applied = next(w for v in data["workspace"]["views"] for w in v["widgets"] if w["id"] == widget["id"])
    assert applied["title"].endswith("Bar")
    assert data["version"]["changeLog"]["sourceRevisionProposalId"] == proposal["id"]
    assert data["version"]["changeLog"]["affectedWidgets"] == [widget["id"]]
    _workspace_schema_validate(data["version"])

    versions = client.get(
        f"/bff/agora/trading-room/workspaces/{workspace_id}/versions",
        headers={"Authorization": "Bearer test"},
    )
    assert versions.status_code == 200, versions.text
    assert [v["dashboardVersion"] for v in versions.json()["data"]] == [1, 2]
    assert versions.json()["data"][1]["changeSummary"].startswith("accepted widget revision")
    print("✅ widget revision: apply preserves before/proposed specs and records change log")


def test_widget_revision_keep_original_adds_copy_and_rollback_creates_new_version():
    store = make_trading_room_store()
    client = _client(store)
    workspace, etag = _accept_workspace(client, _create_proposal(client))
    workspace_id = workspace["id"]
    widget = next(w for v in workspace["views"] for w in v["widgets"] if w["id"] == "overview_strategy_health")
    proposed = _make_revised_widget(widget, title_suffix="Copy")

    create = client.post(
        f"/bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget['id']}/revision-proposals",
        headers={"Authorization": "Bearer test", "Idempotency-Key": "idem-widget-copy-create"},
        json={
            "instruction": "保留原圖，再新增長條圖版本。",
            "proposedSpec": proposed,
            "rationale": "原始 gauge 適合單點狀態，長條圖適合拆解健康度來源。",
            "warnings": [],
            "dataAvailability": "partial",
        },
    )
    assert create.status_code == 201, create.text
    proposal = create.json()["data"]

    keep_copy = client.post(
        f"/bff/agora/trading-room/widget-revision-proposals/{proposal['id']}/accept",
        headers={"Authorization": "Bearer test", "If-Match": etag, "Idempotency-Key": "idem-widget-copy-accept"},
        json={
            "acceptanceAction": "keep_original_add_modified_copy",
            "copyWidgetId": "overview_strategy_health_copy",
        },
    )
    assert keep_copy.status_code == 200, keep_copy.text
    copied_workspace = keep_copy.json()["data"]["workspace"]
    copied_widgets = [w for v in copied_workspace["views"] for w in v["widgets"]]
    assert any(w["id"] == widget["id"] and w["title"] == widget["title"] for w in copied_widgets)
    assert any(w["id"] == "overview_strategy_health_copy" and w["title"].endswith("Copy") for w in copied_widgets)
    assert keep_copy.json()["data"]["version"]["changeLog"]["affectedWidgets"] == [
        widget["id"],
        "overview_strategy_health_copy",
    ]

    versions = client.get(
        f"/bff/agora/trading-room/workspaces/{workspace_id}/versions",
        headers={"Authorization": "Bearer test"},
    )
    assert versions.status_code == 200, versions.text
    first_version_id = versions.json()["data"][0]["id"]
    assert [v["dashboardVersion"] for v in versions.json()["data"]] == [1, 2]

    rollback = client.post(
        f"/bff/agora/trading-room/workspaces/{workspace_id}/versions/{first_version_id}/rollback",
        headers={
            "Authorization": "Bearer test",
            "If-Match": keep_copy.headers["etag"],
            "Idempotency-Key": "idem-widget-copy-rollback",
        },
        json={"reason": "回到交易僕人的初始提案。"},
    )
    assert rollback.status_code == 200, rollback.text
    restored = rollback.json()["data"]["workspace"]
    assert restored["dashboardVersion"] == 3
    assert not any(w["id"] == "overview_strategy_health_copy" for v in restored["views"] for w in v["widgets"])
    assert rollback.json()["data"]["version"]["changeLog"]["rollbackOfVersionId"] == first_version_id
    _workspace_schema_validate(rollback.json()["data"]["version"])
    print("✅ widget revision: keep-original-copy and rollback are append-only versioned")


# ---------------------------------------------------------------------------
# Regression: jsonschema compliance (Fix 1)
# ---------------------------------------------------------------------------

_SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "specs", "agora", "v4", "trading_decision_event.schema.json",
)
_TRADING_INTENT_SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "specs", "agora", "trading_intent.schema.json",
)
_GOVERNED_HANDOFF_SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "specs", "agora", "v4", "governed_intent_handoff.schema.json",
)
_WORKSPACE_SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "specs", "agora", "trading_room_workspace.schema.json",
)


def test_model_dump_exclude_none_passes_schema():
    """model_dump(exclude_none=True) must pass v4 trading_decision_event schema."""
    import jsonschema

    with open(_SCHEMA_PATH) as f:
        schema = json.load(f)
    model = TradingDecisionEvent(**_make_event(event_id="evt-schema-valid"))
    dumped = model.model_dump(exclude_none=True)
    jsonschema.validate(dumped, schema)
    print("✅ schema: model_dump(exclude_none=True) passes v4 trading_decision_event schema")


def test_model_dump_without_exclude_none_emits_null_fields_that_fail_schema():
    """Regression: model_dump() without exclude_none emits null optional fields that violate v4 schema."""
    import jsonschema

    with open(_SCHEMA_PATH) as f:
        schema = json.load(f)
    model = TradingDecisionEvent(**_make_event(event_id="evt-schema-null"))
    dumped = model.model_dump()
    assert dumped.get("dedupe_key") is None, "dedupe_key should be None when not set"
    assert dumped["subject"].get("asset_class") is None, "asset_class should be None when not set"
    try:
        jsonschema.validate(dumped, schema)
        raise AssertionError("Expected schema validation failure due to null optional fields")
    except jsonschema.ValidationError:
        pass
    print("✅ schema: model_dump() emits null optional fields that fail v4 schema (confirms fix is needed)")


# ---------------------------------------------------------------------------
# Regression: D1 safety invariant at store boundary (Fix 2)
# ---------------------------------------------------------------------------

def test_store_rejects_invalid_no_order_route_proof():
    """Store must reject events with no_order_route_proof != 'agora_decision_support_only'."""
    import pytest

    store = make_trading_room_store()
    bad_event = _make_event(event_id="evt-bad-proof")
    bad_event["no_order_route_proof"] = "BROKER_ORDER_ALLOWED"
    with pytest.raises(ValueError, match="D1 safety invariant"):
        store.upsert_decision_event(bad_event)
    print("✅ store: rejects event with BROKER_ORDER_ALLOWED no_order_route_proof (D1 invariant)")


def test_store_rejects_missing_no_order_route_proof():
    """Store must reject events without no_order_route_proof."""
    import pytest

    store = make_trading_room_store()
    bad_event = _make_event(event_id="evt-no-proof")
    del bad_event["no_order_route_proof"]
    with pytest.raises(ValueError, match="D1 safety invariant"):
        store.upsert_decision_event(bad_event)
    print("✅ store: rejects event with missing no_order_route_proof (D1 invariant)")


# ---------------------------------------------------------------------------
# Regression: pagination token semantics (Fix 3)
# ---------------------------------------------------------------------------

def test_store_pagination_no_repeat_on_second_page():
    """next_page_token must not repeat the last item of the previous page."""
    store = make_trading_room_store()
    for i in range(4):
        ev = _make_event(event_id=f"pg-e{i}")
        ev["triggered_at"] = f"2026-06-22T10:0{i}:00Z"
        store.upsert_decision_event(ev)

    page1 = store.list_decision_events(page_size=2)
    assert len(page1["items"]) == 2
    token = page1["page_info"]["next_page_token"]
    assert token is not None, "Expected a next_page_token for page 1"
    last_id_page1 = page1["items"][-1]["decision_event_id"]

    page2 = store.list_decision_events(page_size=2, next_page_token=token)
    page2_ids = [e["decision_event_id"] for e in page2["items"]]
    assert last_id_page1 not in page2_ids, (
        f"Token item {last_id_page1!r} must not repeat in page 2: {page2_ids}"
    )
    assert len(page2["items"]) == 2, f"Expected 2 items on page 2, got {len(page2['items'])}"
    print(f"✅ store: page 2 starts after token {token!r}, no repeat of {last_id_page1!r}")


def test_store_pagination_full_coverage_no_overlap():
    """All items must appear exactly once across paginated pages."""
    store = make_trading_room_store()
    for i in range(6):
        ev = _make_event(event_id=f"cov-e{i}")
        ev["triggered_at"] = f"2026-06-22T10:0{i}:00Z"
        store.upsert_decision_event(ev)

    all_ids = []
    token = None
    for _ in range(10):
        page = store.list_decision_events(page_size=2, next_page_token=token)
        all_ids.extend(e["decision_event_id"] for e in page["items"])
        token = page["page_info"]["next_page_token"]
        if not page["page_info"]["has_more"]:
            break

    assert len(all_ids) == 6, f"Expected 6 unique items across pages, got {len(all_ids)}: {all_ids}"
    assert len(set(all_ids)) == 6, f"Duplicate items found across pages: {all_ids}"
    print("✅ store: all 6 items covered exactly once across paginated pages")


# ---------------------------------------------------------------------------
# Router smoke test
# ---------------------------------------------------------------------------

def test_router_creation_smoke():
    """Router must be creatable without errors."""
    import uuid as _uuid

    def _extract_identity(_auth: str | None, session_cookie: str | None = None) -> dict:
        return {"user_id": "test-user", "tenant_id": "test-tenant"}

    def _require_read_role(_identity: dict) -> None:
        pass

    from fastapi import HTTPException

    def _bff_error(status_code: int, code: str, message: str, reason: str, **kw) -> HTTPException:
        return HTTPException(status_code=status_code, detail={"code": code, "message": message})

    def _utc_now() -> str:
        return "2026-06-22T10:00:00Z"

    router = create_trading_room_router(
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        bff_error=_bff_error,
        utc_now=_utc_now,
    )
    routes = [r.path for r in router.routes]
    assert "/bff/agora/trading-room" in routes, f"Missing /bff/agora/trading-room in {routes}"
    assert "/bff/agora/trading-room/decision-events" in routes
    assert "/bff/agora/trading-room/decision-events/{decision_event_id}" in routes
    assert "/bff/agora/trading-room/decision-events/{decision_event_id}/decisions" in routes
    assert "/bff/agora/trading-room/stream" in routes
    assert "/bff/agora/trading-intents/{intent_id}" in routes
    assert "/bff/agora/trading-intents/{intent_id}/handoffs" in routes
    assert "/bff/agora/trading-intents/{intent_id}/withdraw" in routes
    assert "/bff/agora/strategies/{strategy_id}/trading-room/proposals" in routes
    assert "/bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}" in routes
    assert "/bff/agora/strategies/{strategy_id}/trading-room/proposals/{proposal_id}/accept" in routes
    assert "/bff/agora/trading-room/workspaces/{workspace_id}" in routes
    assert "/bff/agora/trading-room/workspaces/{workspace_id}/layout" in routes
    assert "/bff/agora/trading-room/workspaces/{workspace_id}/views" in routes
    assert "/bff/agora/trading-room/workspaces/{workspace_id}/views/{view_id}" in routes
    assert "/bff/agora/trading-room/workspaces/{workspace_id}/widgets" in routes
    assert "/bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}" in routes
    assert "/bff/agora/trading-room/workspaces/{workspace_id}/widgets/{widget_id}/revision-proposals" in routes
    assert "/bff/agora/trading-room/widget-revision-proposals/{proposal_id}/accept" in routes
    assert "/bff/agora/trading-room/workspaces/{workspace_id}/versions" in routes
    assert "/bff/agora/trading-room/workspaces/{workspace_id}/versions/{version_id}/rollback" in routes
    print(f"✅ router: created with {len(routes)} routes")
    print(f"   Routes: {sorted(routes)}")


# ---------------------------------------------------------------------------
# Cookie session auth (AG-DYNUI-LIVE-AUTH-002 regression)
#
# Trading Room routes must accept the same cookie-based browser session that
# /bff/me and /bff/management/shell-summary already accept, not only a
# Bearer Authorization header. Live browsers send `credentials: "include"`
# and rely on the `pantheon_session` cookie when no bearer token is issued
# (e.g. dev-login disabled). Before this fix these routes only forwarded the
# Authorization header to extract_identity(), so a valid cookie session
# still got AUTH_REQUIRED.
# ---------------------------------------------------------------------------

def test_get_trading_room_accepts_cookie_session_without_authorization_header():
    store = make_trading_room_store()
    client = _client(store)
    client.cookies.set("pantheon_session", "session-token-abc")
    resp = client.get("/bff/agora/trading-room")
    assert resp.status_code == 200, resp.text
    print("✅ router: GET /bff/agora/trading-room accepts cookie session without Authorization header")


def test_list_decision_events_accepts_cookie_session_without_authorization_header():
    store = make_trading_room_store()
    store.upsert_decision_event(_make_event(event_id="evt-cookie-001"))
    client = _client(store)
    client.cookies.set("pantheon_session", "session-token-abc")
    resp = client.get("/bff/agora/trading-room/decision-events")
    assert resp.status_code == 200, resp.text
    print("✅ router: GET decision-events accepts cookie session without Authorization header")


def test_get_trading_room_rejects_request_with_no_credentials():
    store = make_trading_room_store()
    client = _client(store)
    resp = client.get("/bff/agora/trading-room")
    assert resp.status_code == 401, resp.text
    print("✅ router: GET /bff/agora/trading-room rejects request with no Authorization header and no cookie")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Direct-run mode: insert service root so relative package imports resolve
    _svc_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
    if _svc_root not in sys.path:
        sys.path.insert(0, _svc_root)

    test_store_upsert_and_get()
    test_store_list_empty()
    test_store_list_filter_by_kind()
    test_store_list_filter_by_state()
    test_store_record_trader_decision_approve()
    test_store_record_trader_decision_reject()
    test_store_record_trader_decision_defer()
    test_store_intent_upsert_and_get()
    test_store_get_missing_returns_none()
    test_trading_decision_event_model()
    test_trading_decision_event_no_order_route_proof_is_const()
    test_trading_room_aggregate_model()
    test_confidence_and_probability_are_distinct()
    test_ev_gross_cost_net_downside()
    test_all_event_kinds_accepted()
    test_trader_decision_request()
    test_governed_intent_handoff_no_order_route_proof()
    test_approve_decision_persists_schema_valid_trading_intent()
    test_decision_requires_idempotency_key()
    test_submit_handoff_paper_persists_request_only_record()
    test_canary_and_live_handoffs_are_promotion_review_request_only()
    test_handoff_rejects_mismatched_stage_type()
    test_handoff_requires_idempotency_key()
    test_withdraw_marks_pending_handoff_withdrawn()
    test_workspace_proposal_returns_complete_v11_view_set_and_schema_valid()
    test_workspace_proposal_get_and_accept_materializes_active_workspace()
    test_workspace_layout_requires_etag_and_supports_remove_restore()
    test_workspace_view_and_widget_mutations_are_registry_validated()
    test_workspace_rejects_servant_direct_patch_and_code_injection()
    test_workspace_cross_user_read_is_forbidden()
    test_widget_revision_proposal_apply_preserves_before_after_and_records_version()
    test_widget_revision_keep_original_adds_copy_and_rollback_creates_new_version()
    test_model_dump_exclude_none_passes_schema()
    test_model_dump_without_exclude_none_emits_null_fields_that_fail_schema()
    test_store_rejects_invalid_no_order_route_proof()
    test_store_rejects_missing_no_order_route_proof()
    test_store_pagination_no_repeat_on_second_page()
    test_store_pagination_full_coverage_no_overlap()
    test_router_creation_smoke()
    print("\n✅ All trading room tests passed.")
