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


def _client(store: TradingRoomStore) -> TestClient:
    def _extract_identity(_auth: str | None) -> dict:
        return {"user_id": "user-001", "tenant_id": "tenant-001", "session_id": "session-001"}

    def _require_read_role(_identity: dict) -> None:
        pass

    def _bff_error(status_code: int, code: object, message: str, reason: str, **kw) -> HTTPException:
        code_value = getattr(code, "value", code)
        return HTTPException(
            status_code=status_code,
            detail={"code": code_value, "message": message, "reason": reason, **kw},
        )

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

    def _extract_identity(_auth: str | None) -> dict:
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
    print(f"✅ router: created with {len(routes)} routes")
    print(f"   Routes: {sorted(routes)}")


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
    test_model_dump_exclude_none_passes_schema()
    test_model_dump_without_exclude_none_emits_null_fields_that_fail_schema()
    test_store_rejects_invalid_no_order_route_proof()
    test_store_rejects_missing_no_order_route_proof()
    test_store_pagination_no_repeat_on_second_page()
    test_store_pagination_full_coverage_no_overlap()
    test_router_creation_smoke()
    print("\n✅ All trading room tests passed.")
