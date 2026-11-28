"""AGORA-PAPER-INTENT-EXECUTION-20261127: the paper execution lane consumes
trader-confirmed Agora paper handoffs inside the paper-signal-producer cycle.

The end-to-end tests drive the real BFF trading-room routes (trader submit,
service drain, state writeback) and the real ``DecisionSignalProducer`` on an
in-memory binding queue.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.execution.lean_runtime import agora_paper_intent_consumer as mod
from services.execution.lean_runtime.agora_paper_intent_consumer import (
    AGORA_PAPER_INTENT_DAILY_CAP,
    AgoraHandoffClient,
    AgoraHandoffConflict,
    AgoraPaperIntentConsumer,
    agora_intent_signal_id,
    consumer_from_env,
)
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore

ROOT = Path(__file__).resolve().parents[3]
TOKEN = "paper-exec-test-token"
TENANT = "tenant-001"
USER = "user-001"
NOW = "2026-11-27T02:00:00Z"


# ---------------------------------------------------------------------------
# Real BFF behind a TestClient-backed service client
# ---------------------------------------------------------------------------


def _bff_error(status_code, code, message, reason, **kw):
    return HTTPException(status_code=status_code, detail={"code": getattr(code, "value", code), "reason": reason})


class _Bff:
    def __init__(self, monkeypatch) -> None:
        from services.control_plane.bff.agora.trading_room.router import create_trading_room_router
        from services.control_plane.bff.agora.trading_room.store import TradingRoomStore

        monkeypatch.setenv("AGORA_PAPER_EXECUTION_SERVICE_TOKEN", TOKEN)
        monkeypatch.setenv("AGORA_PAPER_EXECUTION_SERVICE_TENANTS", TENANT)
        monkeypatch.setenv("PANTHEON_ENV", "dev")
        self.store = TradingRoomStore()
        app = FastAPI()
        app.include_router(
            create_trading_room_router(
                extract_identity=lambda _a, session_cookie=None: {"user_id": USER, "tenant_id": TENANT},
                require_read_role=lambda _i: None,
                require_write_role=lambda _i: None,
                bff_error=_bff_error,
                utc_now=lambda: NOW,
                trading_room_store=self.store,
            )
        )
        self.http = TestClient(app)

    def submit(self, intent_id: str, *, symbol: str = "2330.TW", quantity: float = 1000, action: str = "enter",
               limit_price: float | None = None, created_at: str = NOW) -> str:
        self.store.upsert_intent({
            "spec_version": "1.0",
            "intent_id": intent_id,
            "operator_id": USER,
            "metadata": {"tenant_id": TENANT, "user_id": USER, "decision": "approve",
                         "human_decision_id": f"hdl-{intent_id}"},
            "intent_type": "entry_interest",
            "direction": "long",
            "subject": {"symbol": symbol, "strategy_ref": "strat-001"},
            "linked_event_ids": [f"evt-{intent_id}"],
            "no_order_route_proof": "agora_intent_record_only",
            "expressed_at": NOW,
        })
        proposal: dict[str, Any] = {"action": action, "symbol": symbol, "direction": "long",
                                    "quantity": quantity, "non_binding": True}
        if limit_price is not None:
            proposal["limit_price"] = limit_price
        resp = self.http.post(
            f"/bff/agora/trading-intents/{intent_id}/handoffs",
            headers={"Authorization": "Bearer u", "If-Match": "*", "Idempotency-Key": f"k-{intent_id}",
                     "X-Request-Id": f"r-{intent_id}"},
            json={
                "spec_version": "1.0",
                "handoff_id": f"hof-{intent_id}",
                "intent_id": intent_id,
                "requested_stage": "paper",
                "handoff_type": "paper_execution_request",
                "state": "submitted",
                "strategy_id": "strat-001",
                "strategy_spec_registry_id": "reg-001",
                "requested_by": {"actor_type": "trader", "actor_ref": USER},
                "evidence_refs": [],
                "no_order_route_proof": "agora_request_only_no_order_route",
                "created_at": created_at,
                "action_proposal": proposal,
            },
        )
        assert resp.status_code == 202, resp.text
        return f"hof-{intent_id}"

    def servant_persona(self) -> str:
        from services.control_plane.bff.agora.servant.router import _stable_servant_persona_id

        return _stable_servant_persona_id(tenant_id=TENANT, agora_user_id=USER)

    def client(self) -> AgoraHandoffClient:
        http = self.http

        class _TestClientHandoffClient(AgoraHandoffClient):
            def _request(self, method, path, tenant_id, body=None):
                headers = {"Authorization": f"Bearer {self._token}", "X-Pantheon-Service-Actor": self._actor,
                           "X-Tenant-Id": tenant_id}
                resp = http.request(method, path, headers=headers, json=body)
                if resp.status_code == 409:
                    raise AgoraHandoffConflict(resp.text)
                assert resp.status_code == 200, resp.text
                return resp.json()

        return _TestClientHandoffClient("http://operator-bff:8001", TOKEN)


class _Orders:
    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}

    def find(self, capital_pool_id: str, client_order_id: str):
        order = self.orders.get(client_order_id)
        return dict(order) if order and order["capital_pool_id"] == capital_pool_id else None


def _binding(persona_id: str, **overrides) -> dict[str, Any]:
    binding = {
        "binding_id": "rb-trader-paper",
        "runtime_id": "rt-persona-paper-abc",
        "capital_pool_id": "pool-trader-paper",
        "status": "active",
        "deployment_mode": "paper",
        "metadata": {"tenant_id": TENANT, "persona_id": persona_id, "sponsor_persona_id": persona_id},
    }
    binding.update(overrides)
    return binding


def _quote(price: float = 600.0, as_of: str = "2026-11-26T05:30:00Z"):
    return lambda _symbol: {"price": price, "as_of": as_of, "source_ref": "src:tw-twse:2330:2026-11-26"}


def _consumer(bff: _Bff, store: InMemoryPendingSignalStore, **kw) -> AgoraPaperIntentConsumer:
    kw.setdefault("price_reader", _quote())
    return AgoraPaperIntentConsumer(client=bff.client(), store_for=lambda _b: store, tenants=[TENANT], **kw)


# ---------------------------------------------------------------------------
# Acceptance 1: consumed, enqueued through DecisionSignalProducer, written back
# ---------------------------------------------------------------------------


def test_confirmed_paper_handoff_executes_in_the_trader_paper_book(monkeypatch):
    bff = _Bff(monkeypatch)
    handoff_id = bff.submit("int-1", limit_price=601.0)
    store = InMemoryPendingSignalStore()
    orders = _Orders()
    consumer = _consumer(bff, store, order_reader=orders)
    binding = _binding(bff.servant_persona())

    assert consumer.consume([binding], NOW)["accepted"] == 1
    handoff = bff.store.get_handoff(handoff_id)
    assert handoff["state"] == "accepted"
    result = handoff["paper_execution"]["result"]
    signal_id = agora_intent_signal_id(handoff_id)
    assert result["signal_id"] == signal_id
    assert result["runtime_binding_id"] == "rb-trader-paper"
    assert result["daily_slot"] == 1
    assert result["market_price"] == 600.0
    assert result["updated_by"] == "paper-signal-producer"

    pending = store.get_pending()
    assert len(pending) == 1
    signal = pending[0]
    assert signal["signal_id"] == signal_id
    assert signal["binding_id"] == "rb-trader-paper"
    assert signal["runtime_id"] == "rt-persona-paper-abc"
    assert signal["environment"] == "paper"
    assert signal["tenant_id"] == TENANT
    assert signal["symbol"] == "2330.TW"
    assert (signal["action"], signal["direction"]) == ("BUY", "LONG")
    assert (signal["quantity"], signal["quantity_type"]) == (1000.0, "SHARES")
    assert (signal["order_type"], signal["limit_price"]) == ("LIMIT", 601.0)
    assert signal["source_worker"] == "paper-signal-producer.agora-intent"
    meta = signal["metadata"]
    assert meta["is_real_order"] is False and meta["is_real_capital"] is False
    assert meta["capital_pool_id"] == "pool-trader-paper"
    assert meta["market_price"] == 600.0
    assert meta["decision_lock_ref"] == "hdl-int-1"
    assert meta["execution_observation_kind"] == "pantheon_paper_proxy"
    assert signal["correlation_envelope"]["environment"] == "paper"

    # Idempotent: the next cycle neither re-enqueues nor re-admits.
    assert consumer.consume([binding], NOW)["pending"] == 1
    assert store.get_pending() == []

    # The broker paper simulator filled the runtime's order; state is written back.
    orders.orders[signal_id] = {"order_id": "ord-1", "client_order_id": signal_id, "capital_pool_id": "pool-trader-paper",
                                "status": "filled", "fill_qty": 1000.0, "fill_price": 601.0,
                                "filled_at": "2026-11-27T02:01:00Z"}
    assert consumer.consume([binding], NOW)["filled"] == 1
    handoff = bff.store.get_handoff(handoff_id)
    assert handoff["state"] == "filled"
    assert handoff["paper_execution"]["result"]["broker_order_id"] == "ord-1"
    assert bff.store.get_intent_state("int-1") == "filled"
    # A filled handoff leaves the drain.
    assert consumer.consume([binding], NOW) == {
        "accepted": 0, "rejected": 0, "filled": 0, "partially_filled": 0, "pending": 0, "errors": 0}


def test_partial_fill_then_full_fill_and_broker_rejection(monkeypatch):
    bff = _Bff(monkeypatch)
    first = bff.submit("int-p")
    second = bff.submit("int-r", action="exit", quantity=10)
    store = InMemoryPendingSignalStore()
    orders = _Orders()
    consumer = _consumer(bff, store, order_reader=orders)
    binding = _binding(bff.servant_persona())
    consumer.consume([binding], NOW)
    signals = {s["metadata"]["agora_handoff_id"]: s for s in store.get_pending()}
    assert signals[second]["action"] == "SELL" and signals[second]["order_type"] == "MARKET"

    sid = agora_intent_signal_id(first)
    orders.orders[sid] = {"order_id": "o-p", "client_order_id": sid, "capital_pool_id": "pool-trader-paper",
                          "status": "filled", "fill_qty": 400.0, "fill_price": 600.0}
    sid2 = agora_intent_signal_id(second)
    orders.orders[sid2] = {"order_id": "o-r", "client_order_id": sid2, "capital_pool_id": "pool-trader-paper",
                           "status": "rejected", "reject_reason": "MARKET_PRICE_UNAVAILABLE"}
    consumer.consume([binding], NOW)
    assert bff.store.get_handoff(first)["state"] == "partially_filled"
    rejected = bff.store.get_handoff(second)
    assert rejected["state"] == "rejected"
    assert rejected["paper_execution"]["result"]["reason_code"] == "broker_rejected"

    orders.orders[sid]["fill_qty"] = 1000.0
    consumer.consume([binding], NOW)
    assert bff.store.get_handoff(first)["state"] == "filled"


# ---------------------------------------------------------------------------
# Owner, stage and order checks (typed rejection, nothing enqueued)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "binding_overrides,reason",
    [
        ({"metadata": {"tenant_id": TENANT, "persona_id": "agora-servant-someone-else"}}, "paper_book_missing"),
        ({"metadata": {"tenant_id": "tenant-other", "persona_id": "__SELF__"}}, "owner_mismatch"),
        ({"deployment_mode": "live"}, "binding_not_paper"),
        ({"deployment_mode": "canary"}, "binding_not_paper"),
        ({"capital_pool_id": ""}, "paper_book_missing"),
    ],
)
def test_binding_must_be_the_trader_own_paper_book(monkeypatch, binding_overrides, reason):
    bff = _Bff(monkeypatch)
    handoff_id = bff.submit("int-o")
    persona = bff.servant_persona()
    overrides = json.loads(json.dumps(binding_overrides).replace("__SELF__", persona))
    store = InMemoryPendingSignalStore()
    _consumer(bff, store).consume([_binding(persona, **overrides)], NOW)
    handoff = bff.store.get_handoff(handoff_id)
    assert handoff["state"] == "rejected"
    assert handoff["paper_execution"]["result"]["reason_code"] == reason
    assert store.get_pending() == []


def test_two_paper_books_for_one_servant_is_refused(monkeypatch):
    bff = _Bff(monkeypatch)
    handoff_id = bff.submit("int-two")
    persona = bff.servant_persona()
    store = InMemoryPendingSignalStore()
    _consumer(bff, store).consume([_binding(persona), _binding(persona, binding_id="rb-2")], NOW)
    assert bff.store.get_handoff(handoff_id)["paper_execution"]["result"]["reason_code"] == "owner_mismatch"
    assert store.get_pending() == []


class _FakeClient:
    def __init__(self, handoffs):
        self.handoffs = handoffs
        self.writes: list[tuple[str, str, dict]] = []
        self.conflict_on: set[str] = set()

    def list_open(self, tenant_id, *, limit=100):
        return [h for h in self.handoffs if (h.get("paper_execution") or {}).get("owner", {}).get("tenant_id") == tenant_id]

    def write_state(self, tenant_id, handoff_id, state, result):
        if state in self.conflict_on:
            raise AgoraHandoffConflict("withdrawn")
        self.writes.append((handoff_id, state, dict(result)))
        return {}


def _raw_handoff(**overrides) -> dict[str, Any]:
    handoff = {
        "handoff_id": "hof-raw",
        "intent_id": "int-raw",
        "requested_stage": "paper",
        "handoff_type": "paper_execution_request",
        "target_queue": "paper_execution",
        "state": "submitted",
        "strategy_id": "strat-001",
        "no_order_route_proof": "agora_request_only_no_order_route",
        "created_at": NOW,
        "paper_execution": {
            "environment": "paper",
            "owner": {"tenant_id": TENANT, "user_id": USER, "servant_persona_id": "agora-servant-x"},
            "order": {"symbol": "2330.TW", "action": "enter", "side": "buy", "quantity": 1000.0,
                      "quantity_type": "SHARES", "order_type": "market"},
        },
    }
    handoff.update(overrides)
    return handoff


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"requested_stage": "live"}, "non_paper_stage"),
        ({"requested_stage": "canary"}, "non_paper_stage"),
        ({"target_queue": "promotion_review"}, "non_paper_stage"),
        ({"handoff_type": "promotion_review_request"}, "non_paper_stage"),
        ({"no_order_route_proof": "routes_orders"}, "non_paper_stage"),
        ({"paper_execution": {**_raw_handoff()["paper_execution"], "environment": "live"}}, "non_paper_stage"),
        ({"paper_execution": {**_raw_handoff()["paper_execution"],
                              "order": {**_raw_handoff()["paper_execution"]["order"], "quantity": 0}}}, "invalid_order"),
        ({"paper_execution": {**_raw_handoff()["paper_execution"],
                              "order": {**_raw_handoff()["paper_execution"]["order"], "order_type": "limit"}}}, "invalid_order"),
        ({"paper_execution": {**_raw_handoff()["paper_execution"],
                              "order": {**_raw_handoff()["paper_execution"]["order"], "symbol": "not a symbol!"}}},
         "symbol_not_executable"),
        ({"paper_execution": {**_raw_handoff()["paper_execution"],
                              "owner": {"tenant_id": TENANT, "user_id": USER, "servant_persona_id": ""}}}, "owner_mismatch"),
    ],
)
def test_nothing_on_this_path_reaches_canary_live_or_an_invalid_order(overrides, reason):
    client = _FakeClient([_raw_handoff(**overrides)])
    store = InMemoryPendingSignalStore()
    consumer = AgoraPaperIntentConsumer(client=client, store_for=lambda _b: store, tenants=[TENANT],
                                        price_reader=_quote())
    consumer.consume([_binding("agora-servant-x")], NOW)
    assert client.writes == [("hof-raw", "rejected", client.writes[0][2])]
    assert client.writes[0][2]["reason_code"] == reason
    assert store.get_pending() == []


def test_market_data_missing_stale_or_unreachable(monkeypatch):
    store = InMemoryPendingSignalStore()
    binding = _binding("agora-servant-x")

    client = _FakeClient([_raw_handoff()])
    AgoraPaperIntentConsumer(client=client, store_for=lambda _b: store, tenants=[TENANT],
                             price_reader=lambda _s: None).consume([binding], NOW)
    assert client.writes[0][2]["reason_code"] == "market_data_unavailable"

    client = _FakeClient([_raw_handoff()])
    AgoraPaperIntentConsumer(client=client, store_for=lambda _b: store, tenants=[TENANT],
                             price_reader=_quote(as_of="2026-11-01T05:30:00Z")).consume([binding], NOW)
    assert client.writes[0][2]["reason_code"] == "market_data_stale"

    def _down(_symbol):
        raise OSError("source-ingest unreachable")

    client = _FakeClient([_raw_handoff()])
    summary = AgoraPaperIntentConsumer(client=client, store_for=lambda _b: store, tenants=[TENANT],
                                       price_reader=_down).consume([binding], NOW)
    assert summary["pending"] == 1 and client.writes == []
    assert store.get_pending() == []


def test_old_handoff_expires():
    client = _FakeClient([_raw_handoff(created_at="2026-11-25T00:00:00Z")])
    store = InMemoryPendingSignalStore()
    AgoraPaperIntentConsumer(client=client, store_for=lambda _b: store, tenants=[TENANT],
                             price_reader=_quote()).consume([_binding("agora-servant-x")], NOW)
    assert client.writes[0][2]["reason_code"] == "expired"


def test_withdrawn_meanwhile_is_not_enqueued():
    client = _FakeClient([_raw_handoff()])
    client.conflict_on = {"accepted"}
    store = InMemoryPendingSignalStore()
    summary = AgoraPaperIntentConsumer(client=client, store_for=lambda _b: store, tenants=[TENANT],
                                       price_reader=_quote()).consume([_binding("agora-servant-x")], NOW)
    assert summary["pending"] == 1
    assert store.get_pending() == []


# ---------------------------------------------------------------------------
# Acceptance 3: daily cap of 50 per trader, stated in the runtime budget
# ---------------------------------------------------------------------------


def test_daily_cap_rejects_with_daily_cap_and_resets_next_taipei_day(monkeypatch):
    bff = _Bff(monkeypatch)
    for i in range(4):
        bff.submit(f"int-c{i}")
    store = InMemoryPendingSignalStore()
    consumer = _consumer(bff, store, daily_cap=3)
    binding = _binding(bff.servant_persona())
    summary = consumer.consume([binding], NOW)
    assert summary["accepted"] == 3 and summary["rejected"] == 1
    capped = [h for h in (bff.store.get_handoff(f"hof-int-c{i}") for i in range(4)) if h["state"] == "rejected"]
    assert [h["paper_execution"]["result"]["reason_code"] for h in capped] == ["daily_cap"]
    assert len(store.get_pending()) == 3
    # Taipei day rolls over at 16:00Z; the cap resets.
    bff.submit("int-next", created_at="2026-11-27T16:05:00Z")
    assert consumer.consume([binding], "2026-11-27T16:05:00Z")["accepted"] == 1


def test_daily_cap_is_fifty_and_cannot_be_raised():
    assert AGORA_PAPER_INTENT_DAILY_CAP == 50
    with pytest.raises(ValueError):
        AgoraPaperIntentConsumer(client=None, store_for=lambda _b: None, tenants=[TENANT], daily_cap=51)
    from services.control_plane.bff.agora.trading_room.service import PAPER_INTENT_DAILY_CAP

    assert PAPER_INTENT_DAILY_CAP == AGORA_PAPER_INTENT_DAILY_CAP


def test_daily_cap_is_stated_in_the_runtime_budget():
    manifest = json.loads((ROOT / "docs/operations/storage-manifest.json").read_text())
    entries = [b for b in manifest["runtime_budgets"] if "AGORA-PAPER-INTENT-EXECUTION-20261127" in str(b.get("task"))]
    assert len(entries) == 1
    entry = entries[0]
    assert "50" in entry["cap"] and "daily_cap" in entry["cap"]
    proof = entry["bound_proof"]
    assert proof["contains"] in (ROOT / proof["file"]).read_text()


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_consumer_from_env_is_off_until_configured(monkeypatch):
    for key in ("PANTHEON_AGORA_BFF_INTERNAL_URL", "AGORA_PAPER_EXECUTION_SERVICE_TOKEN",
                "AGORA_PAPER_EXECUTION_SERVICE_TENANTS", "PANTHEON_AGORA_PAPER_INTENT_CONSUMER"):
        monkeypatch.delenv(key, raising=False)
    assert consumer_from_env(lambda _b: None) is None
    monkeypatch.setenv("PANTHEON_AGORA_PAPER_INTENT_CONSUMER", "on")
    # A missing secret never stops the producer: the consumer stays off.
    assert consumer_from_env(lambda _b: None) is None
    monkeypatch.setenv("PANTHEON_AGORA_BFF_INTERNAL_URL", "http://operator-bff:8001")
    monkeypatch.setenv("AGORA_PAPER_EXECUTION_SERVICE_TOKEN", "t")
    monkeypatch.setenv("AGORA_PAPER_EXECUTION_SERVICE_TENANTS", "tenant-a, tenant-b")
    consumer = consumer_from_env(lambda _b: None)
    assert isinstance(consumer, AgoraPaperIntentConsumer)
    assert consumer._tenants == ["tenant-a", "tenant-b"]
    monkeypatch.setenv("PANTHEON_AGORA_PAPER_INTENT_CONSUMER", "off")
    assert consumer_from_env(lambda _b: None) is None


def test_producer_main_runs_the_consumer_in_its_cycle():
    source = (ROOT / "services/execution/lean_runtime/paper_signal_producer.py").read_text()
    main_src = source[source.index("def main() -> int:"):]
    assert "consumer_status_from_env(store_for)" in main_src
    assert "agora_intents.consume(bindings, now_iso)" in main_src
    assert "_require_paper_only_configuration()" in main_src


def test_consumer_module_has_no_live_or_canary_route():
    text = Path(mod.__file__).read_text()
    for forbidden in ("/api/broker/live", "live/orders", "shioaji", "ibkr", "canary_execution"):
        assert forbidden not in text


@pytest.mark.parametrize(
    "token,profile,posture,state,reason",
    [
        ("", "hosted", "dev", "not_configured", "service_token_unset"),
        ("pantheon-local-agora-paper-execution-service-token", "", "dev",
         "not_configured", "service_token_public_default_or_placeholder"),
        ("pantheon-local-agora-paper-execution-service-token", "hosted", "dev",
         "not_configured", "service_token_public_default_or_placeholder"),
        ("replace-me-agora-paper-execution-service-token", "hosted", "dev",
         "not_configured", "service_token_public_default_or_placeholder"),
        ("Placeholder-123", "hosted", "dev",
         "not_configured", "service_token_public_default_or_placeholder"),
        # The local default works only in the explicit local profile, never in stage/prod.
        ("pantheon-local-agora-paper-execution-service-token", "local", "dev", "configured", None),
        ("pantheon-local-agora-paper-execution-service-token", "local", "prod",
         "not_configured", "service_token_public_default_or_placeholder"),
        ("a-real-operator-provisioned-secret", "hosted", "dev", "configured", None),
    ],
)
def test_consumer_stays_off_for_default_or_placeholder_token(monkeypatch, token, profile, posture, state, reason):
    monkeypatch.delenv("PANTHEON_AGORA_PAPER_INTENT_CONSUMER", raising=False)
    monkeypatch.setenv("PANTHEON_AGORA_BFF_INTERNAL_URL", "http://operator-bff:8001")
    monkeypatch.setenv("AGORA_PAPER_EXECUTION_SERVICE_TENANTS", "tenant-a")
    monkeypatch.setenv("AGORA_PAPER_EXECUTION_SERVICE_TOKEN", token)
    monkeypatch.setenv("AGORA_PAPER_EXECUTION_TOKEN_PROFILE", profile)
    monkeypatch.setenv("PANTHEON_ENV", posture)
    monkeypatch.delenv("PANTHEON_PERSISTENCE_POSTURE", raising=False)
    consumer, status = mod.consumer_status_from_env(lambda _b: None)
    assert status["state"] == state
    assert status.get("reason") == reason
    assert (consumer is not None) == (state == "configured")
    assert token not in repr(status) or token == ""


def test_producer_health_reports_the_consumer_state():
    source = (ROOT / "services/execution/lean_runtime/paper_signal_producer.py").read_text()
    main_src = source[source.index("def main() -> int:"):]
    assert '"agora_paper_intent_consumer": agora_intent_status' in main_src
