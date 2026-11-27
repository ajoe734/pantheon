"""AGORA-PAPER-INTENT-EXECUTION-20261127 (SD-AGORA-SERVANT-CORE-CAPABILITIES-20261127 T8).

BFF side of a trader-confirmed decision executing in the trader's own paper
book:

- the ``paper`` stage handoff targets ``paper_execution`` (the removed
  ``paper_validation_request -> management_governance`` pair had no reader);
- the paper request carries the confirmed order, the owner and the decision
  lock reference, and is rejected when it is not executable;
- the paper execution lane drains open handoffs and writes state back through
  a service-only boundary;
- Agora keeps no order route: nothing here routes, submits or reaches canary
  or live.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.trading_room.router import create_trading_room_router
from services.control_plane.bff.agora.trading_room.store import TradingRoomStore

ROOT = Path(__file__).resolve().parents[4]
SCHEMA_V15 = ROOT / "services/control-plane/specs/agora/v15/governed_intent_handoff.schema.json"
SCHEMA_V4 = ROOT / "services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json"
TOKEN = "test-paper-execution-token"
ACTOR = "paper-signal-producer"


def _bff_error(status_code: int, code: object, message: str, reason: str, **kw) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": getattr(code, "value", code), "message": message, "reason": reason, **kw},
    )


def _client(store: TradingRoomStore, *, user_id: str = "user-001", tenant_id: str = "tenant-001") -> TestClient:
    def _extract_identity(_auth, session_cookie=None):
        if not _auth and not session_cookie:
            raise _bff_error(401, "AUTH_REQUIRED", "Missing credentials", "no_credentials")
        return {"user_id": user_id, "tenant_id": tenant_id, "session_id": "session-001"}

    app = FastAPI()
    app.include_router(
        create_trading_room_router(
            extract_identity=_extract_identity,
            require_read_role=lambda _i: None,
            require_write_role=lambda _i: None,
            bff_error=_bff_error,
            utc_now=lambda: "2026-11-27T02:00:00Z",
            trading_room_store=store,
        )
    )
    return TestClient(app)


def _intent(intent_id: str, *, decision: str = "approve", tenant_id: str = "tenant-001", user_id: str = "user-001") -> dict:
    return {
        "spec_version": "1.0",
        "intent_id": intent_id,
        "operator_id": user_id,
        "metadata": {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "decision": decision,
            "human_decision_id": f"hdl-{intent_id}",
        },
        "intent_type": "entry_interest",
        "direction": "long",
        "subject": {"symbol": "2330.TW", "strategy_ref": "strat-001"},
        "linked_event_ids": [f"evt-{intent_id}"],
        "no_order_route_proof": "agora_intent_record_only",
        "expressed_at": "2026-11-27T01:59:00Z",
    }


def _handoff(intent_id: str, *, stage: str = "paper", **proposal) -> dict:
    handoff_type = {
        "paper": "paper_execution_request",
        "shadow": "shadow_start",
        "canary": "promotion_review_request",
        "live": "promotion_review_request",
    }[stage]
    action_proposal = {"action": "enter", "symbol": "2330.TW", "direction": "long", "quantity": 1000, "non_binding": True}
    action_proposal.update(proposal)
    action_proposal = {k: v for k, v in action_proposal.items() if v is not None}
    return {
        "spec_version": "1.0",
        "handoff_id": f"hof-{intent_id}",
        "intent_id": intent_id,
        "requested_stage": stage,
        "handoff_type": handoff_type,
        "state": "submitted",
        "strategy_id": "strat-001",
        "strategy_spec_registry_id": "reg-001",
        "requested_by": {"actor_type": "trader", "actor_ref": "user-001"},
        "evidence_refs": [],
        "no_order_route_proof": "agora_request_only_no_order_route",
        "created_at": "2026-11-27T02:00:00Z",
        "action_proposal": action_proposal,
    }


def _write_headers(key: str) -> dict:
    return {"Authorization": "Bearer user", "If-Match": "*", "Idempotency-Key": key, "X-Request-Id": f"req-{key}"}


def _service_headers(tenant: str = "tenant-001", token: str = TOKEN, actor: str = ACTOR) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Pantheon-Service-Actor": actor, "X-Tenant-Id": tenant}


@pytest.fixture(autouse=True)
def _service_env(monkeypatch):
    monkeypatch.setenv("AGORA_PAPER_EXECUTION_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("AGORA_PAPER_EXECUTION_SERVICE_TENANTS", "tenant-001")
    monkeypatch.delenv("AGORA_PAPER_EXECUTION_ALLOWED_SERVICE_ACTOR", raising=False)
    monkeypatch.delenv("PANTHEON_PERSISTENCE_POSTURE", raising=False)
    monkeypatch.setenv("PANTHEON_ENV", "dev")


def _submit(store: TradingRoomStore, intent_id: str, **proposal):
    store.upsert_intent(_intent(intent_id))
    return _client(store).post(
        f"/bff/agora/trading-intents/{intent_id}/handoffs",
        headers=_write_headers(f"idem-{intent_id}"),
        json=_handoff(intent_id, **proposal),
    )


def _state(client: TestClient, handoff_id: str, state: str, **result):
    body = {"state": state, "no_order_route_proof": "agora_request_only_no_order_route", **result}
    return client.post(
        f"/internal/agora/paper-execution/handoffs/{handoff_id}/state",
        headers=_service_headers(),
        json=body,
    )


# ---------------------------------------------------------------------------
# Stage table and paper request validation
# ---------------------------------------------------------------------------

def test_paper_handoff_targets_paper_execution_with_owner_order_and_lock():
    store = TradingRoomStore()
    resp = _submit(store, "int-a", limit_price=601.5)
    assert resp.status_code == 202, resp.text
    data = resp.json()["data"]
    assert data["handoff_type"] == "paper_execution_request"
    assert data["target_queue"] == "paper_execution"
    assert data["no_order_route_proof"] == "agora_request_only_no_order_route"

    handoff = store.get_handoff("hof-int-a")
    block = handoff["paper_execution"]
    assert block["environment"] == "paper"
    assert block["owner"]["tenant_id"] == "tenant-001"
    assert block["owner"]["user_id"] == "user-001"
    assert block["owner"]["servant_persona_id"].startswith("agora-servant-")
    assert block["order"] == {
        "symbol": "2330.TW",
        "action": "enter",
        "side": "buy",
        "quantity": 1000.0,
        "quantity_type": "SHARES",
        "order_type": "limit",
        "limit_price": 601.5,
    }
    assert block["decision_lock_ref"] == "hdl-int-a"
    assert block["decision_event_id"] == "evt-int-a"
    assert block["daily_cap"] == 50
    assert not handoff.get("runtime_binding_ref")
    jsonschema.validate(handoff, json.loads(SCHEMA_V15.read_text()))


def test_servant_persona_matches_the_trader_paper_book_identity():
    from services.control_plane.bff.agora.servant.router import _stable_servant_persona_id

    store = TradingRoomStore()
    assert _submit(store, "int-p").status_code == 202
    owner = store.get_handoff("hof-int-p")["paper_execution"]["owner"]
    assert owner["servant_persona_id"] == _stable_servant_persona_id(
        tenant_id="tenant-001", agora_user_id="user-001"
    )


@pytest.mark.parametrize(
    "proposal,reason",
    [
        ({"quantity": None}, "paper_quantity_invalid"),
        ({"quantity": 0}, "paper_quantity_invalid"),
        ({"quantity": -5}, "paper_quantity_invalid"),
        ({"limit_price": 0}, "paper_limit_price_invalid"),
        ({"action": "review"}, "paper_action_not_executable"),
        ({"direction": "short"}, "paper_short_not_supported"),
        ({"symbol": "2317.TW"}, "paper_symbol_mismatch"),
    ],
)
def test_unexecutable_paper_request_is_refused_before_any_record(proposal, reason):
    store = TradingRoomStore()
    resp = _submit(store, "int-bad", **proposal)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["reason"] == reason
    assert store.get_handoff("hof-int-bad") is None
    assert store.get_intent_state("int-bad") == "draft"


def test_unconfirmed_intent_cannot_execute_in_paper():
    store = TradingRoomStore()
    store.upsert_intent(_intent("int-defer", decision="defer"))
    resp = _client(store).post(
        "/bff/agora/trading-intents/int-defer/handoffs",
        headers=_write_headers("idem-defer"),
        json=_handoff("int-defer"),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "paper_intent_not_confirmed"


def test_paper_validation_request_and_management_governance_are_removed():
    store = TradingRoomStore()
    store.upsert_intent(_intent("int-old"))
    body = _handoff("int-old")
    body["handoff_type"] = "paper_validation_request"
    resp = _client(store).post(
        "/bff/agora/trading-intents/int-old/handoffs", headers=_write_headers("idem-old"), json=body
    )
    assert resp.status_code == 422
    body = _handoff("int-old")
    body["target_queue"] = "management_governance"
    resp = _client(store).post(
        "/bff/agora/trading-intents/int-old/handoffs", headers=_write_headers("idem-old2"), json=body
    )
    assert resp.status_code == 422
    schema = json.loads(SCHEMA_V15.read_text())
    assert "paper_validation_request" not in schema["properties"]["handoff_type"]["enum"]
    assert "management_governance" not in schema["properties"]["target_queue"]["enum"]
    # The v1.3 bundle bytes stay frozen; v1.14 carries the superseding schema.
    bundle = json.loads((ROOT / "services/control-plane/specs/agora/bundle_index.v1_14.json").read_text())
    assert "specs/agora/v15/governed_intent_handoff.schema.json" in bundle["files"]
    assert SCHEMA_V4.exists()
    service_src = (ROOT / "services/control-plane/bff/agora/trading_room/service.py").read_text()
    assert '"paper_validation_request"' not in service_src


# ---------------------------------------------------------------------------
# Service boundary: drain and writeback
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "env,headers,status",
    [
        ({"AGORA_PAPER_EXECUTION_SERVICE_TOKEN": ""}, _service_headers(), 503),
        ({"AGORA_PAPER_EXECUTION_SERVICE_TENANTS": ""}, _service_headers(), 503),
        ({}, _service_headers(token="wrong"), 401),
        ({}, {"X-Pantheon-Service-Actor": ACTOR, "X-Tenant-Id": "tenant-001"}, 401),
        ({}, _service_headers(actor="policy-learning-agora-handoff-drainer"), 403),
        ({}, _service_headers(tenant="tenant-other"), 403),
        (
            {"PANTHEON_ENV": "prod", "AGORA_PAPER_EXECUTION_SERVICE_TOKEN": "pantheon-local-agora-paper-execution-service-token"},
            _service_headers(token="pantheon-local-agora-paper-execution-service-token"),
            503,
        ),
    ],
)
def test_service_boundary_fails_closed(monkeypatch, env, headers, status):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    client = _client(TradingRoomStore())
    assert client.get("/internal/agora/paper-execution/handoffs", headers=headers).status_code == status
    resp = client.post(
        "/internal/agora/paper-execution/handoffs/hof-x/state",
        headers=headers,
        json={"state": "accepted", "no_order_route_proof": "agora_request_only_no_order_route"},
    )
    assert resp.status_code == status


def test_browser_identity_cannot_reach_the_service_boundary():
    client = _client(TradingRoomStore())
    resp = client.get("/internal/agora/paper-execution/handoffs", headers={"Authorization": "Bearer user"})
    assert resp.status_code in {401, 403}


def test_drain_returns_only_open_paper_execution_handoffs_of_the_tenant():
    store = TradingRoomStore()
    assert _submit(store, "int-1").status_code == 202
    assert _submit(store, "int-2").status_code == 202
    for stage in ("shadow", "canary", "live"):
        intent_id = f"int-{stage}"
        store.upsert_intent(_intent(intent_id))
        assert _client(store).post(
            f"/bff/agora/trading-intents/{intent_id}/handoffs",
            headers=_write_headers(f"idem-{stage}"),
            json=_handoff(intent_id, stage=stage),
        ).status_code == 202
    # Another tenant's paper handoff stays invisible.
    store.upsert_intent(_intent("int-foreign", tenant_id="tenant-002", user_id="user-002"))
    assert _client(store, tenant_id="tenant-002", user_id="user-002").post(
        "/bff/agora/trading-intents/int-foreign/handoffs",
        headers=_write_headers("idem-foreign"),
        json=_handoff("int-foreign"),
    ).status_code == 202

    client = _client(store)
    assert _state(client, "hof-int-2", "rejected", reason_code="daily_cap").status_code == 200
    resp = client.get("/internal/agora/paper-execution/handoffs", headers=_service_headers())
    assert resp.status_code == 200, resp.text
    ids = [item["handoff_id"] for item in resp.json()["items"]]
    assert ids == ["hof-int-1"]
    assert resp.json()["meta"]["no_order_route_proof"] == "agora_request_only_no_order_route"
    assert client.get(
        "/internal/agora/paper-execution/handoffs", headers=_service_headers(), params={"limit": 500}
    ).status_code == 422


def test_writeback_accepted_then_filled_is_idempotent_and_schema_valid():
    store = TradingRoomStore()
    assert _submit(store, "int-f").status_code == 202
    client = _client(store)
    accepted = dict(
        runtime_binding_id="rb-paper-1",
        capital_pool_id="pool-paper-1",
        signal_id="sig-1",
        daily_slot=1,
        market_price=600.0,
        market_as_of="2026-11-26T05:30:00Z",
        accepted_at="2026-11-27T02:01:00Z",
    )
    first = _state(client, "hof-int-f", "accepted", **accepted)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "updated"
    replay = _state(client, "hof-int-f", "accepted", **accepted)
    assert replay.json() ["status"] == "exists" and replay.json()["idempotent"] is True
    assert store.get_intent_state("int-f") == "accepted"

    fill = dict(broker_order_id="ord-1", fill_qty=1000, fill_price=600.0, filled_at="2026-11-27T02:01:30Z")
    filled = _state(client, "hof-int-f", "filled", **accepted, **fill)
    assert filled.status_code == 200, filled.text
    handoff = store.get_handoff("hof-int-f")
    assert handoff["state"] == "filled"
    result = handoff["paper_execution"]["result"]
    assert result["broker_order_id"] == "ord-1"
    assert result["environment"] == "paper"
    assert result["updated_by"] == ACTOR
    assert [h["state"] for h in handoff["paper_execution"]["state_history"]] == ["accepted", "filled"]
    assert store.get_intent_state("int-f") == "filled"
    jsonschema.validate(handoff, json.loads(SCHEMA_V15.read_text()))
    # Terminal: no further transition.
    assert _state(client, "hof-int-f", "rejected", reason_code="broker_rejected").status_code == 409
    # The trader reads the written-back state on the intent detail.
    detail = client.get("/bff/agora/trading-intents/int-f", headers={"Authorization": "Bearer user"}).json()
    assert detail["status"] == "filled"
    assert detail["handoffs"][0]["paper_execution"]["result"]["fill_qty"] == 1000


@pytest.mark.parametrize(
    "body,status,reason",
    [
        ({"state": "rejected"}, 422, "paper_execution_reason_required"),
        ({"state": "rejected", "reason_code": "made_up"}, 422, None),
        ({"state": "filled", "fill_qty": 1}, 422, "paper_execution_fill_incomplete"),
        ({"state": "accepted", "environment": "live"}, 422, None),
        ({"state": "accepted", "environment": "canary"}, 422, None),
        ({"state": "filled", "broker_order_id": "o", "fill_qty": 1, "fill_price": 1.0}, 409, "paper_execution_transition_invalid"),
    ],
)
def test_writeback_rejects_untyped_or_non_paper_state(body, status, reason):
    store = TradingRoomStore()
    assert _submit(store, "int-w").status_code == 202
    resp = _state(_client(store), "hof-int-w", **body)
    assert resp.status_code == status, resp.text
    if reason:
        assert resp.json()["detail"]["reason"] == reason


def test_writeback_requires_no_order_route_proof():
    store = TradingRoomStore()
    assert _submit(store, "int-n").status_code == 202
    resp = _client(store).post(
        "/internal/agora/paper-execution/handoffs/hof-int-n/state",
        headers=_service_headers(),
        json={"state": "accepted"},
    )
    assert resp.status_code == 422


def test_writeback_cannot_touch_canary_live_or_foreign_tenant_handoffs():
    store = TradingRoomStore()
    store.upsert_intent(_intent("int-live"))
    assert _client(store).post(
        "/bff/agora/trading-intents/int-live/handoffs",
        headers=_write_headers("idem-live"),
        json=_handoff("int-live", stage="live"),
    ).status_code == 202
    client = _client(store)
    resp = _state(client, "hof-int-live", "accepted")
    assert resp.status_code == 404
    assert store.get_handoff("hof-int-live")["state"] == "submitted"
    assert store.get_handoff("hof-int-live")["target_queue"] == "promotion_review"

    store.upsert_intent(_intent("int-t2", tenant_id="tenant-002", user_id="user-002"))
    assert _client(store, tenant_id="tenant-002", user_id="user-002").post(
        "/bff/agora/trading-intents/int-t2/handoffs",
        headers=_write_headers("idem-t2"),
        json=_handoff("int-t2"),
    ).status_code == 202
    assert _state(client, "hof-int-t2", "accepted").status_code == 404


def test_withdrawn_paper_handoff_is_not_drained_or_accepted():
    store = TradingRoomStore()
    assert _submit(store, "int-wd").status_code == 202
    client = _client(store)
    assert client.post(
        "/bff/agora/trading-intents/int-wd/withdraw", headers=_write_headers("idem-wd")
    ).status_code == 200
    assert client.get("/internal/agora/paper-execution/handoffs", headers=_service_headers()).json()["items"] == []
    assert _state(client, "hof-int-wd", "accepted").status_code == 409


# ---------------------------------------------------------------------------
# Agora keeps no order route
# ---------------------------------------------------------------------------

def test_agora_trading_room_exposes_no_order_route():
    app = FastAPI()
    app.include_router(
        create_trading_room_router(
            extract_identity=lambda *_a, **_k: {},
            require_read_role=lambda _i: None,
            bff_error=_bff_error,
            utc_now=lambda: "2026-11-27T02:00:00Z",
            trading_room_store=TradingRoomStore(),
        )
    )
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any("order" in path.lower() or "broker" in path.lower() for path in paths)
    assert not any("/api/" in path for path in paths)
    # Every handoff record must keep the request-only proof (store invariant).
    store = TradingRoomStore()
    store.upsert_intent(_intent("int-proof"))
    record = _handoff("int-proof")
    record["no_order_route_proof"] = "routes_orders"
    with pytest.raises(ValueError, match="no_order_route_proof"):
        store.upsert_handoff(record)


def test_paper_execution_module_imports_no_broker_or_order_client():
    sources = [
        ROOT / "services/control-plane/bff/agora/trading_room/routes/paper_execution.py",
        ROOT / "services/control-plane/bff/agora/trading_room/service.py",
        ROOT / "services/control-plane/bff/agora/trading_room/store.py",
    ]
    for source in sources:
        text = source.read_text()
        for forbidden in ("/api/broker", "paper/orders", "live/orders", "shioaji", "ibkr"):
            assert forbidden not in text, (source.name, forbidden)


def test_postgres_store_routes_paper_execution_methods_through_the_locked_row():
    from services.control_plane.bff.agora.trading_room import store as store_module

    calls = []

    class _Probe(store_module.PostgresTradingRoomStore):
        def __init__(self):  # no database
            self.__dict__["_inside_persisted_call"] = False

        def _call(self, method, *args, write=False, **kwargs):
            calls.append((method, write))
            return None

    probe = _Probe()
    probe.apply_paper_execution_state("h", tenant_id="t", state="accepted", result={}, updated_at="now")
    probe.list_paper_execution_handoffs(tenant_id="t")
    assert calls == [("apply_paper_execution_state", True), ("list_paper_execution_handoffs", False)]
