"""AGORA-PAPER-BOOK-READBACK-20261127 (SD_AGORA_SERVANT_CORE_2026-11-27 §8.3 step 4, §12 T9).

Verifies:
1. An owner-scoped Agora route (`GET /bff/agora/trading-room/paper-book` and
   `GET /bff/agora/trading-room/paper-book/intents/{intent_id}`) returns the
   trader's paper fills and positions and each intent's progress from the broker
   paper orders and the binding telemetry; another trader gets 404.
2. Fills are labelled `pantheon_paper_proxy` (` execution_display_label == "交易員決策（紙上代理）"`,
   `actual_fill_claimed is False`, `execution_verified is False`, `is_real_fill is False`)
   and populate the linked `AgoraDecisionLock`'s `human_actual` arm as a paper proxy
   (never a real fill).
3. End-to-end continuity with `AGORA-PAPER-INTENT-EXECUTION-20261127`: a trader
   confirms a decision in the Trading Room, submits the paper handoff, the paper
   execution consumer executes it in the trader's own paper book, and the trader
   reads back the executed decision with its fill, position, intent progress,
   and `human_actual` lock arm.
"""
from __future__ import annotations

import io
import json
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

import jsonschema
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.agora.service import AgoraWriteService
from services.agora.shadow_mode import (
    DISPLAY_LABELS,
    DecisionLockInvalidError,
    apply_paper_proxy_fill_to_decision_lock,
    build_decision_lock,
    verify_checksum,
)
from services.agora.store import AgoraStore
from services.control_plane.bff.agora.servant.router import _stable_servant_persona_id
from services.control_plane.bff.agora.trading_room.paper_book_readback import (
    BindingTelemetryReadClient,
    BrokerPaperBookOrderClient,
    PAPER_BOOK_READBACK_PROOF,
    PAPER_PROXY_OBSERVATION_KIND,
    agora_intent_signal_id,
)
from services.control_plane.bff.agora.trading_room.router import create_trading_room_router
from services.control_plane.bff.agora.trading_room.store import TradingRoomStore, make_trading_room_store
from services.control_plane.bff.personas.trader_paper_book import (
    trader_paper_book_ids,
    trader_paper_runtime_id,
)
from services.evaluation.attribution.settlement import settle_decision_lock
from services.execution.lean_runtime.agora_paper_intent_consumer import AgoraPaperIntentConsumer

ROOT = Path(__file__).resolve().parents[4]
SCHEMA_V15 = ROOT / "services/control-plane/specs/agora/v15/governed_intent_handoff.schema.json"
SERVICE_TOKEN = "test-paper-execution-token"
SERVICE_ACTOR = "paper-signal-producer"


class _MemoryTable:
    def __init__(self, **_kw: Any) -> None:
        self.rows: Dict[str, Dict[str, Any]] = {}

    def put(self, record_id: str, payload: Dict[str, Any]) -> None:
        self.rows[record_id] = dict(payload)

    def insert_if_absent(self, record_id: str, payload: Dict[str, Any], **_kw: Any):
        if record_id in self.rows:
            return False, dict(self.rows[record_id])
        self.rows[record_id] = dict(payload)
        return True, dict(payload)

    def compare_and_set(
        self,
        record_id: str,
        expected: Optional[Dict[str, Any]],
        updated: Dict[str, Any],
    ):
        current = self.rows.get(record_id)
        if expected is None:
            if current is not None:
                return False, dict(current)
            self.rows[record_id] = dict(updated)
            return True, dict(updated)
        if current != expected:
            return False, dict(current) if current is not None else None
        self.rows[record_id] = dict(updated)
        return True, dict(updated)

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        row = self.rows.get(record_id)
        return dict(row) if row is not None else None

    def list_all(self, **_kw: Any) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.rows.values()]

    def select_where(self, _sql: str, params: tuple = (), **_kw: Any) -> List[Dict[str, Any]]:
        if len(params) == 2:
            tenant_id, user_id = str(params[0]), str(params[1])
            return [
                dict(r)
                for r in self.rows.values()
                if str(r.get("tenant_id") or "") == tenant_id
                and str(r.get("user_id") or "") == user_id
            ]
        return self.list_all()


def _make_decision_lock_owner() -> AgoraWriteService:
    from services.control_plane.bff.agora.dataset_extraction.extractor import AgoraDatasetStore

    with mock.patch("services.agora.store.PostgresJsonOwnerStore", _MemoryTable):
        return AgoraWriteService(
            store=AgoraStore(dsn="postgresql://test/none", bootstrap=False),
            dataset_store=AgoraDatasetStore(backend="memory"),
        )


def _bff_error(status_code: int, code: object, message: str, reason: str, **kw: Any) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": getattr(code, "value", code), "message": message, "reason": reason, **kw},
    )


def _decision_event(
    event_id: str,
    *,
    tenant_id: str = "tenant-001",
    user_id: str = "user-001",
    symbol: str = "2330.TW",
    strategy_id: str = "strat-001",
    **extra: Any,
) -> Dict[str, Any]:
    event: Dict[str, Any] = {
        "spec_version": "1.0",
        "decision_event_id": event_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "event_kind": "entry",
        "origin": "strategy_signal",
        "strategy_id": strategy_id,
        "strategy_spec_registry_id": "reg-001",
        "subject": {"symbol": symbol},
        "state": "pending_review",
        "triggered_at": "2026-11-27T01:00:00Z",
        "data_cutoff": "2026-11-27T00:59:00Z",
        "confidence": {"value": 0.78, "basis": "model", "calibration_state": "calibrated", "sample_size": 120},
        "probability": {"target_outcome": "up", "horizon": "5d", "value": 0.64, "ci_lower": 0.54, "ci_upper": 0.74},
        "expected_value": {"horizon": "5d", "unit": "pct_return", "gross": 0.032, "cost": 0.001, "net": 0.031, "downside": -0.018},
        "rationale": [{"claim": "breakout signal", "confidence": 0.78, "evidence_refs": []}],
        "risk_notes": [],
        "evidence_refs": [],
        "invalidation": {"conditions": ["close < 950"], "current_state": "valid", "last_checked_at": "2026-11-27T00:58:00Z"},
        "suggested_action": "enter",
        "servant_suggestion": {"action": "enter", "note": "servant concurs"},
        "institutional_view": {"persona_id": "inst-alpha", "stance": "enter"},
        "no_order_route_proof": "agora_decision_support_only",
    }
    event.update(extra)
    return event


def _handoff_payload(
    intent_id: str,
    *,
    handoff_id: Optional[str] = None,
    user_id: str = "user-001",
    symbol: str = "2330.TW",
    action: str = "enter",
    direction: str = "long",
    quantity: float = 1000,
    limit_price: Optional[float] = None,
) -> Dict[str, Any]:
    proposal: Dict[str, Any] = {
        "action": action,
        "symbol": symbol,
        "direction": direction,
        "quantity": quantity,
        "non_binding": True,
    }
    if limit_price is not None:
        proposal["limit_price"] = limit_price
    return {
        "spec_version": "1.0",
        "handoff_id": handoff_id or f"hof-{intent_id}",
        "intent_id": intent_id,
        "requested_stage": "paper",
        "handoff_type": "paper_execution_request",
        "state": "submitted",
        "strategy_id": "strat-001",
        "strategy_spec_registry_id": "reg-001",
        "requested_by": {"actor_type": "trader", "actor_ref": user_id},
        "evidence_refs": [],
        "no_order_route_proof": "agora_request_only_no_order_route",
        "created_at": "2026-11-27T02:00:00Z",
        "action_proposal": proposal,
    }


def _write_headers(token: str, idem_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "If-Match": "*",
        "Idempotency-Key": idem_key,
        "X-Request-Id": f"req-{idem_key}",
    }


def _paper_book_for(tenant_id: str, user_id: str) -> Dict[str, Any]:
    persona_id = _stable_servant_persona_id(tenant_id=tenant_id, agora_user_id=user_id)
    ids = trader_paper_book_ids(tenant_id, persona_id)
    return {
        "tenant_id": tenant_id,
        "agora_user_id": user_id,
        "servant_persona_id": persona_id,
        "capital_pool_id": ids.capital_pool_id,
        "persona_capital_binding_id": ids.persona_capital_binding_id,
        "runtime_binding_id": f"rb-persona-paper-{ids.token}",
        "runtime_id": trader_paper_runtime_id(ids),
        "environment": "paper",
        "starting_capital": 10_000_000.0,
        "currency": "TWD",
    }


class _InMemoryPaperBookEnvironment:
    """In-memory paper broker + binding telemetry + Trading Room BFF harness."""

    def __init__(self) -> None:
        self.store: TradingRoomStore = make_trading_room_store()
        self.owner: AgoraWriteService = _make_decision_lock_owner()
        self.orders_by_pool: Dict[str, List[Dict[str, Any]]] = {}
        self.telemetry_by_runtime: Dict[str, Dict[str, Any]] = {}
        self.provisioned_books: Dict[tuple[str, str], Dict[str, Any]] = {}
        self._order_seq = 0

        self.broker_client = BrokerPaperBookOrderClient(
            base_url="http://broker.test",
            urlopen=self._broker_urlopen,
        )
        self.telemetry_client = BindingTelemetryReadClient(
            base_url="http://telemetry.test",
            token="telemetry-test-token",
            urlopen=self._telemetry_urlopen,
        )

        self.identities: Dict[str, Dict[str, Any]] = {
            "trader-1": {"user_id": "user-001", "tenant_id": "tenant-001", "roles": ["trader"]},
            "trader-2": {"user_id": "user-002", "tenant_id": "tenant-001", "roles": ["trader"]},
            "trader-other-tenant": {"user_id": "user-001", "tenant_id": "tenant-002", "roles": ["trader"]},
        }

        def _extract_identity(auth: Optional[str], session_cookie: Optional[str] = None) -> Dict[str, Any]:
            if not auth and not session_cookie:
                raise _bff_error(401, "AUTH_REQUIRED", "Missing credentials", "no_credentials")
            tok = str(auth or "").replace("Bearer", "").strip()
            ident = self.identities.get(tok)
            if ident is None:
                return {"user_id": tok or "user-001", "tenant_id": "tenant-001", "roles": ["trader"]}
            return dict(ident)

        app = FastAPI()
        app.include_router(
            create_trading_room_router(
                extract_identity=_extract_identity,
                require_read_role=lambda _i: None,
                require_write_role=lambda _i: None,
                bff_error=_bff_error,
                utc_now=lambda: "2026-11-27T02:05:00Z",
                trading_room_store=self.store,
                decision_lock_owner=self.owner,
                paper_book_order_client=self.broker_client,
                binding_telemetry_client=self.telemetry_client,
                paper_book_reader=self._read_paper_book,
            )
        )
        self.client = TestClient(app)

    def _read_paper_book(
        self,
        tenant_id: str,
        servant_persona_id: str,
        *,
        agora_user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        key = (str(tenant_id), str(agora_user_id or ""))
        book = self.provisioned_books.get(key)
        if book and book.get("servant_persona_id") == servant_persona_id:
            return dict(book)
        return None

    def ensure_book(self, tenant_id: str, user_id: str) -> Dict[str, Any]:
        book = _paper_book_for(tenant_id, user_id)
        self.provisioned_books[(tenant_id, user_id)] = book
        return book

    def record_broker_paper_order(
        self,
        *,
        capital_pool_id: str,
        client_order_id: str,
        symbol: str,
        side: str,
        qty: float,
        fill_price: float = 985.0,
        status: str = "filled",
        strategy_id: str = "strat-001",
        order_type: str = "market",
        filled_at: str = "2026-11-27T02:01:30Z",
    ) -> Dict[str, Any]:
        self._order_seq += 1
        order = {
            "order_id": f"ord-paper-{self._order_seq}",
            "client_order_id": client_order_id,
            "capital_pool_id": capital_pool_id,
            "strategy_id": strategy_id,
            "symbol": symbol,
            "side": side,
            "qty": float(qty),
            "order_type": order_type,
            "status": status,
            "fill_qty": float(qty) if status in {"filled", "partially_filled"} else 0.0,
            "fill_price": float(fill_price) if status in {"filled", "partially_filled"} else None,
            "filled_at": filled_at if status in {"filled", "partially_filled"} else None,
        }
        self.orders_by_pool.setdefault(capital_pool_id, []).append(order)
        return order

    def _broker_urlopen(self, req: Any, timeout: float = 3.0) -> Any:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        parsed = urllib.parse.urlsplit(url)
        params = urllib.parse.parse_qs(parsed.query)
        pool_id = (params.get("capital_pool_id") or [""])[0]
        orders = list(self.orders_by_pool.get(pool_id, []))
        body = json.dumps({"status": "ok", "orders": orders}).encode("utf-8")
        resp = io.BytesIO(body)
        resp.status = 200  # type: ignore[attr-defined]
        return resp

    def _telemetry_urlopen(self, req: Any, timeout: float = 3.0) -> Any:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        runtime_id = urllib.parse.unquote(url.rsplit("/", 1)[-1])
        summary = self.telemetry_by_runtime.get(runtime_id)
        if summary is None:
            body = json.dumps({"status": "empty", "summary": None}).encode("utf-8")
        else:
            body = json.dumps({"status": "ok", "summary": summary}).encode("utf-8")
        resp = io.BytesIO(body)
        resp.status = 200  # type: ignore[attr-defined]
        return resp


@pytest.fixture(autouse=True)
def _paper_execution_service_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGORA_PAPER_EXECUTION_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("AGORA_PAPER_EXECUTION_SERVICE_TENANTS", "tenant-001,tenant-002")
    monkeypatch.delenv("AGORA_PAPER_EXECUTION_ALLOWED_SERVICE_ACTOR", raising=False)
    monkeypatch.delenv("PANTHEON_PERSISTENCE_POSTURE", raising=False)
    monkeypatch.setenv("PANTHEON_ENV", "dev")


def test_trader_decision_executed_in_paper_book_reads_back_fills_positions_and_intent_progress():
    """End-to-end proof (T8 -> T9): decision confirmed in Trading Room -> paper handoff
    executed by AgoraPaperIntentConsumer in trader's paper book -> trader reads back
    paper fills, positions, intent progress, and decision lock human_actual arm.
    """
    env = _InMemoryPaperBookEnvironment()
    book = env.ensure_book("tenant-001", "user-001")
    env.store.upsert_decision_event(_decision_event("evt-t9-1"))

    # 1. Trader confirms the decision in Trading Room.
    decide_resp = env.client.post(
        "/bff/agora/trading-room/decision-events/evt-t9-1/decisions",
        headers=_write_headers("trader-1", "idem-decide-1"),
        json={"decision": "approve", "rationale": "Execute in my paper book"},
    )
    assert decide_resp.status_code == 201, decide_resp.text
    decision_data = decide_resp.json()["data"]
    intent_id = decision_data["intent_ref"]
    human_decision_id = decision_data["human_decision_id"]
    assert intent_id and human_decision_id

    # 2. Trader submits a paper handoff for the recorded intent.
    handoff_id = f"hof-{intent_id}"
    submit_resp = env.client.post(
        f"/bff/agora/trading-intents/{intent_id}/handoffs",
        headers=_write_headers("trader-1", "idem-hof-1"),
        json=_handoff_payload(intent_id, handoff_id=handoff_id, quantity=1000),
    )
    assert submit_resp.status_code == 202, submit_resp.text

    # 3. AgoraPaperIntentConsumer drains the open handoff, enqueues into the paper book,
    #    broker fills the order, and consumer writes state back to BFF.
    class _TestBffHandoffClient:
        def list_open(self, tenant_id: str, *, limit: int = 100) -> List[Dict[str, Any]]:
            r = env.client.get(
                f"/internal/agora/paper-execution/handoffs?limit={limit}",
                headers={
                    "Authorization": f"Bearer {SERVICE_TOKEN}",
                    "X-Pantheon-Service-Actor": SERVICE_ACTOR,
                    "X-Tenant-Id": tenant_id,
                },
            )
            assert r.status_code == 200, r.text
            return list(r.json().get("items") or [])

        def write_state(self, tenant_id: str, hid: str, state: str, result: Dict[str, Any]) -> Dict[str, Any]:
            body = {
                **{k: v for k, v in result.items() if v is not None},
                "state": state,
                "environment": "paper",
                "no_order_route_proof": "agora_request_only_no_order_route",
            }
            r = env.client.post(
                f"/internal/agora/paper-execution/handoffs/{hid}/state",
                headers={
                    "Authorization": f"Bearer {SERVICE_TOKEN}",
                    "X-Pantheon-Service-Actor": SERVICE_ACTOR,
                    "X-Tenant-Id": tenant_id,
                },
                json=body,
            )
            assert r.status_code == 200, r.text
            return r.json()

    class _InMemorySignalStore:
        def __init__(self) -> None:
            self.records: Dict[str, Dict[str, Any]] = {}
            self.signals: List[Dict[str, Any]] = []

        def get_decision_record(self, key: str) -> Optional[Dict[str, Any]]:
            rec = self.records.get(key)
            return dict(rec) if rec is not None else None

        def put_decision_record(self, key: str, value: Dict[str, Any]) -> Dict[str, Any]:
            if key not in self.records:
                self.records[key] = dict(value)
            return dict(self.records[key])

        def enqueue(self, signal: Any) -> Any:
            sig_dict = signal.to_dict() if hasattr(signal, "to_dict") else dict(signal)
            self.signals.append(sig_dict)
            sig_id = str(sig_dict.get("signal_id") or getattr(signal, "signal_id", ""))
            env.record_broker_paper_order(
                capital_pool_id=book["capital_pool_id"],
                client_order_id=sig_id,
                symbol="2330.TW",
                side="Buy",
                qty=1000.0,
                fill_price=985.0,
                status="filled",
                strategy_id="strat-001",
            )
            env.telemetry_by_runtime[book["runtime_id"]] = {
                "tenant_id": "tenant-001",
                "runtime_id": book["runtime_id"],
                "runtime_binding_id": book["runtime_binding_id"],
                "capital_pool_id": book["capital_pool_id"],
                "status": "running",
                "last_event_at": "2026-11-27T02:01:35Z",
                "last_heartbeat_at": "2026-11-27T02:01:35Z",
                "equity": 10_015_000.0,
                "cash": 9_030_000.0,
                "unrealized_pnl": 15_000.0,
                "realized_pnl": 0.0,
                "positions": [
                    {
                        "symbol": "2330.TW",
                        "quantity": 1000.0,
                        "direction": "long",
                        "avg_entry_price": 985.0,
                        "mark_price": 1000.0,
                        "unrealized_pnl": 15_000.0,
                    }
                ],
            }
            return signal

    class _TestOrderReader:
        def find(self, capital_pool_id: str, client_order_id: str) -> Optional[Dict[str, Any]]:
            for order in reversed(env.orders_by_pool.get(capital_pool_id, [])):
                if str(order.get("client_order_id") or "") == client_order_id:
                    return dict(order)
            return None

    sig_store = _InMemorySignalStore()
    consumer = AgoraPaperIntentConsumer(
        client=_TestBffHandoffClient(),
        store_for=lambda _binding: sig_store,
        tenants=["tenant-001"],
        price_reader=lambda symbol: {
            "price": 985.0,
            "as_of": "2026-11-27T02:00:00Z",
            "source_ref": f"bars:{symbol}",
        },
        order_reader=_TestOrderReader(),
    )
    active_paper_binding = {
        "binding_id": book["runtime_binding_id"],
        "runtime_binding_id": book["runtime_binding_id"],
        "runtime_id": book["runtime_id"],
        "capital_pool_id": book["capital_pool_id"],
        "persona_id": book["servant_persona_id"],
        "status": "active",
        "deployment_mode": "paper",
        "execution_mode": "paper",
        "metadata": {
            "tenant_id": "tenant-001",
            "persona_id": book["servant_persona_id"],
            "deployment_mode": "paper",
        },
    }
    tick1 = consumer.consume([active_paper_binding], "2026-11-27T02:01:00Z")
    assert tick1["accepted"] == 1
    assert len(sig_store.signals) == 1
    tick2 = consumer.consume([active_paper_binding], "2026-11-27T02:01:35Z")
    assert tick2["filled"] == 1

    # 4. Trader reads back their paper book via GET /bff/agora/trading-room/paper-book.
    readback_resp = env.client.get(
        "/bff/agora/trading-room/paper-book",
        headers={"Authorization": "Bearer trader-1"},
    )
    assert readback_resp.status_code == 200, readback_resp.text
    payload = readback_resp.json()
    assert payload["status"] == "ok"
    assert payload["environment"] == "paper"
    assert payload["execution_observation_kind"] == PAPER_PROXY_OBSERVATION_KIND
    assert payload["execution_display_label"] == DISPLAY_LABELS["pantheon_paper_proxy"]
    assert payload["actual_fill_claimed"] is False
    assert payload["execution_verified"] is False
    assert payload["is_real_fill"] is False
    assert payload["no_order_route_proof"] == PAPER_BOOK_READBACK_PROOF

    # Verify fills from broker paper orders.
    assert len(payload["fills"]) == 1
    fill = payload["fills"][0]
    assert fill["broker_order_id"] == "ord-paper-1"
    assert fill["signal_id"] == agora_intent_signal_id(handoff_id)
    assert fill["handoff_id"] == handoff_id
    assert fill["intent_id"] == intent_id
    assert fill["decision_event_id"] == "evt-t9-1"
    assert fill["human_decision_id"] == human_decision_id
    assert fill["capital_pool_id"] == book["capital_pool_id"]
    assert fill["runtime_binding_id"] == book["runtime_binding_id"]
    assert fill["runtime_id"] == book["runtime_id"]
    assert fill["symbol"] == "2330.TW"
    assert fill["fill_qty"] == 1000.0
    assert fill["fill_price"] == 985.0
    assert fill["fill_status"] == "filled"
    assert fill["fill_source"] == "broker_paper_orders"
    assert fill["execution_observation_kind"] == "pantheon_paper_proxy"
    assert fill["execution_display_label"] == "交易員決策（紙上代理）"
    assert fill["actual_fill_claimed"] is False
    assert fill["execution_verified"] is False
    assert fill["is_real_fill"] is False
    assert fill["human_actual_arm_populated"] is True

    # Verify positions from binding telemetry + paper fills.
    assert len(payload["positions"]) == 1
    pos = payload["positions"][0]
    assert pos["symbol"] == "2330.TW"
    assert pos["quantity"] == 1000.0
    assert pos["direction"] == "long"
    assert pos["avg_entry_price"] == 985.0
    assert pos["mark_price"] == 1000.0
    assert pos["unrealized_pnl"] == 15_000.0
    assert pos["capital_pool_id"] == book["capital_pool_id"]
    assert pos["runtime_binding_id"] == book["runtime_binding_id"]
    assert pos["execution_observation_kind"] == "pantheon_paper_proxy"
    assert pos["actual_fill_claimed"] is False
    assert pos["is_real_position"] is False
    assert fill["fill_id"] in pos["fill_ids"]
    assert "ord-paper-1" in pos["broker_order_ids"]
    assert intent_id in pos["intent_ids"]

    # Verify intent progress.
    assert len(payload["intents"]) == 1
    intent_prog = payload["intents"][0]
    assert intent_prog["intent_id"] == intent_id
    assert intent_prog["decision_event_id"] == "evt-t9-1"
    assert intent_prog["human_decision_id"] == human_decision_id
    assert intent_prog["lifecycle_state"] == "filled"
    assert intent_prog["handoff_id"] == handoff_id
    assert intent_prog["handoff_state"] == "filled"
    assert intent_prog["progress_stage"] == "filled"
    assert intent_prog["broker_order_id"] == "ord-paper-1"
    assert intent_prog["broker_order_status"] == "filled"
    assert intent_prog["fill_qty"] == 1000.0
    assert intent_prog["fill_price"] == 985.0
    assert intent_prog["execution_observation_kind"] == "pantheon_paper_proxy"
    assert intent_prog["actual_fill_claimed"] is False
    assert intent_prog["human_actual_arm_populated"] is True
    assert intent_prog["binding_telemetry"]["telemetry_available"] is True

    # 5. Trader reads single-intent paper book endpoint.
    single_resp = env.client.get(
        f"/bff/agora/trading-room/paper-book/intents/{intent_id}",
        headers={"Authorization": "Bearer trader-1"},
    )
    assert single_resp.status_code == 200, single_resp.text
    single = single_resp.json()
    assert single["intent"]["intent_id"] == intent_id
    assert single["intent"]["progress_stage"] == "filled"
    assert len(single["fills"]) == 1
    assert single["fills"][0]["broker_order_id"] == "ord-paper-1"
    assert single["fills"][0]["execution_observation_kind"] == "pantheon_paper_proxy"

    # 6. Decision lock's human_actual arm is populated with the pantheon_paper_proxy fill.
    lock_resp = env.client.get(
        "/bff/agora/trading-room/decision-events/evt-t9-1/decision-lock",
        headers={"Authorization": "Bearer trader-1"},
    )
    assert lock_resp.status_code == 200, lock_resp.text
    lock = lock_resp.json()["data"]
    assert verify_checksum(lock) is True
    assert lock["execution_observation_kind"] == "pantheon_paper_proxy"
    assert lock["execution_display_label"] == "交易員決策（紙上代理）"
    assert lock["actual_fill_claimed"] is False
    assert lock["execution_verified"] is False
    assert "paper-fill:ord-paper-1" in lock["fill_refs"]

    human_actual = lock["arms"]["human_actual"]
    assert human_actual["available"] is True
    assert human_actual["execution_observation_kind"] == "pantheon_paper_proxy"
    assert human_actual["execution_display_label"] == "交易員決策（紙上代理）"
    assert human_actual["actual_fill_claimed"] is False
    assert human_actual["execution_verified"] is False
    assert human_actual["is_real_fill"] is False
    assert human_actual["environment"] == "paper"
    assert human_actual["broker_order_id"] == "ord-paper-1"
    assert human_actual["fill_qty"] == 1000.0
    assert human_actual["fill_price"] == 985.0
    assert human_actual["paper_fill"]["execution_observation_kind"] == "pantheon_paper_proxy"
    assert human_actual["paper_fill"]["actual_fill_claimed"] is False

    # 7. Shadow comparison route also exposes the pantheon_paper_proxy fill on human_actual.
    shadow_resp = env.client.get(
        "/bff/agora/trading-room/decision-events/evt-t9-1/shadow-comparison",
        headers={"Authorization": "Bearer trader-1"},
    )
    assert shadow_resp.status_code == 200, shadow_resp.text
    shadow_arms = {
        arm["arm"]: arm["decision_detail"]
        for arm in shadow_resp.json()["data"]["arms"]
    }
    assert shadow_arms["human_actual"]["execution_observation_kind"] == "pantheon_paper_proxy"
    assert shadow_arms["human_actual"]["actual_fill_claimed"] is False
    assert shadow_arms["human_actual"]["fill_qty"] == 1000.0
    assert shadow_arms["human_actual"]["fill_price"] == 985.0

    # 8. Settlement of the lock settles human_actual as pantheon_paper_proxy (never real fill).
    market_rows = [
        {"symbol": "2330.TW", "date": "2026-11-27", "open": 980.0, "high": 990.0, "low": 975.0, "close": 982.0},
        {"symbol": "2330.TW", "date": "2026-11-28", "open": 985.0, "high": 1005.0, "low": 984.0, "close": 1000.0},
        {"symbol": "2330.TW", "date": "2026-11-29", "open": 1000.0, "high": 1012.0, "low": 995.0, "close": 1010.0},
    ]
    settled = settle_decision_lock(
        lock,
        market_rows=market_rows,
        paper_fills=payload["fills"],
        window_sessions=2,
        settled_at="2026-11-29T06:00:00Z",
    ).to_dict()
    settled_ha = next(a for a in settled["arms"] if a["arm"] == "human_actual")
    assert settled_ha["settlement_source"] == "pantheon_paper_proxy"
    assert settled_ha["outcome"] is not None
    assert settled_ha["outcome"]["actual_fill_claimed"] is False
    assert settled_ha["outcome"]["entry_price"] == 985.0
    assert fill["fill_id"] in settled_ha["outcome"]["fill_refs"]


def test_another_trader_gets_404_on_paper_book_and_intent_readback():
    """Owner scoping (AC1): another trader cannot read or enumerate the caller's
    paper book, pool, binding, decision event, or intent; all return 404.
    """
    env = _InMemoryPaperBookEnvironment()
    book_u1 = env.ensure_book("tenant-001", "user-001")
    env.store.upsert_decision_event(_decision_event("evt-u1"))

    decide_resp = env.client.post(
        "/bff/agora/trading-room/decision-events/evt-u1/decisions",
        headers=_write_headers("trader-1", "idem-u1-d"),
        json={"decision": "approve"},
    )
    intent_u1 = decide_resp.json()["data"]["intent_ref"]
    hof_u1 = f"hof-{intent_u1}"
    assert env.client.post(
        f"/bff/agora/trading-intents/{intent_u1}/handoffs",
        headers=_write_headers("trader-1", "idem-u1-h"),
        json=_handoff_payload(intent_u1, handoff_id=hof_u1, quantity=1000),
    ).status_code == 202

    # Unenrolled trader-2 with no paper book gets 404 on bare /paper-book.
    r_bare = env.client.get(
        "/bff/agora/trading-room/paper-book",
        headers={"Authorization": "Bearer trader-2"},
    )
    assert r_bare.status_code == 404
    assert r_bare.json()["detail"]["reason"] == "paper_book_not_found"

    # Even after trader-2 has their own paper book, any query targeting trader-1's
    # identifiers gets a non-enumerating 404.
    env.ensure_book("tenant-001", "user-002")
    for query_params in (
        {"user_id": "user-001"},
        {"trader_id": "user-001"},
        {"tenant_id": "tenant-002"},
        {"servant_persona_id": book_u1["servant_persona_id"]},
        {"capital_pool_id": book_u1["capital_pool_id"]},
        {"runtime_binding_id": book_u1["runtime_binding_id"]},
        {"runtime_id": book_u1["runtime_id"]},
        {"intent_id": intent_u1},
        {"handoff_id": hof_u1},
        {"decision_event_id": "evt-u1"},
    ):
        resp = env.client.get(
            "/bff/agora/trading-room/paper-book",
            params=query_params,
            headers={"Authorization": "Bearer trader-2"},
        )
        assert resp.status_code == 404, f"Expected 404 for {query_params}, got {resp.status_code}: {resp.text}"

    # Direct single-intent readback by trader-2 or another tenant gets 404.
    for token in ("trader-2", "trader-other-tenant"):
        resp_intent = env.client.get(
            f"/bff/agora/trading-room/paper-book/intents/{intent_u1}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_intent.status_code == 404
        assert resp_intent.json()["detail"]["reason"] == "intent_not_found"

    # Trader-2 reading their own book sees 0 fills and 0 intents from trader-1.
    own_u2 = env.client.get(
        "/bff/agora/trading-room/paper-book",
        headers={"Authorization": "Bearer trader-2"},
    )
    assert own_u2.status_code == 200
    assert own_u2.json()["user_id"] == "user-002"
    assert own_u2.json()["fills"] == []
    assert own_u2.json()["intents"] == []


def test_readback_reconciles_broker_paper_order_fill_and_derives_positions_when_telemetry_pending():
    """If the paper broker already filled the order for the handoff's deterministic
    signal_id before the consumer's state writeback arrived, paper-book readback
    reconciles the handoff state, validates against the v15 schema, populates the
    decision lock's human_actual arm, and derives net paper positions from the fills.
    """
    env = _InMemoryPaperBookEnvironment()
    book = env.ensure_book("tenant-001", "user-001")
    env.store.upsert_decision_event(_decision_event("evt-reconcile"))

    decide_resp = env.client.post(
        "/bff/agora/trading-room/decision-events/evt-reconcile/decisions",
        headers=_write_headers("trader-1", "idem-rec-d"),
        json={"decision": "approve"},
    )
    intent_id = decide_resp.json()["data"]["intent_ref"]
    hof_id = f"hof-{intent_id}"
    assert env.client.post(
        f"/bff/agora/trading-intents/{intent_id}/handoffs",
        headers=_write_headers("trader-1", "idem-rec-h"),
        json=_handoff_payload(intent_id, handoff_id=hof_id, quantity=2000),
    ).status_code == 202

    # Broker paper order is filled under the handoff's deterministic signal_id.
    sig_id = agora_intent_signal_id(hof_id)
    env.record_broker_paper_order(
        capital_pool_id=book["capital_pool_id"],
        client_order_id=sig_id,
        symbol="2330.TW",
        side="Buy",
        qty=2000,
        fill_price=990.0,
        status="filled",
    )

    resp = env.client.get(
        "/bff/agora/trading-room/paper-book",
        headers={"Authorization": "Bearer trader-1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["fills"]) == 1
    assert body["fills"][0]["fill_qty"] == 2000.0
    assert body["fills"][0]["fill_price"] == 990.0
    assert body["fills"][0]["execution_observation_kind"] == "pantheon_paper_proxy"
    assert body["fills"][0]["actual_fill_claimed"] is False

    # Position derived from paper fills when telemetry has not yet projected positions.
    assert len(body["positions"]) == 1
    assert body["positions"][0]["symbol"] == "2330.TW"
    assert body["positions"][0]["quantity"] == 2000.0
    assert body["positions"][0]["avg_entry_price"] == 990.0
    assert body["positions"][0]["source"] == "broker_paper_fills"
    assert body["positions"][0]["execution_observation_kind"] == "pantheon_paper_proxy"

    # Handoff in store was reconciled to filled and remains schema-valid.
    reconciled_hof = env.store.get_handoff(hof_id)
    assert reconciled_hof is not None
    assert reconciled_hof["state"] == "filled"
    jsonschema.validate(reconciled_hof, json.loads(SCHEMA_V15.read_text()))


def test_apply_paper_proxy_fill_rejects_real_fill_claims_and_declared_no_trade_locks():
    """Safety invariant (AC2): a paper fill is always pantheon_paper_proxy and can
    never claim a verified/imported real fill or attach to a declared_no_trade lock.
    """
    event = _decision_event("evt-guard")
    trade_lock = build_decision_lock(
        human_decision_id="hdl-guard-1",
        decision_record_id="rec-guard-1",
        event=event,
        decision_event_id="evt-guard",
        tenant_id="tenant-001",
        user_id="user-001",
        body_decision="approve",
        rationale=None,
        modifications=None,
        decision_time="2026-11-27T02:00:00Z",
    )

    for bad_fill in (
        {"execution_observation_kind": "verified_broker_fill", "fill_qty": 1000, "fill_price": 900.0},
        {"execution_observation_kind": "imported_broker_fill", "fill_qty": 1000, "fill_price": 900.0},
        {"actual_fill_claimed": True, "fill_qty": 1000, "fill_price": 900.0},
        {"execution_verified": True, "fill_qty": 1000, "fill_price": 900.0},
        {"is_real_fill": True, "fill_qty": 1000, "fill_price": 900.0},
        {"environment": "live", "fill_qty": 1000, "fill_price": 900.0},
    ):
        with pytest.raises(DecisionLockInvalidError):
            apply_paper_proxy_fill_to_decision_lock(trade_lock, bad_fill)

    no_trade_lock = build_decision_lock(
        human_decision_id="hdl-guard-notrade",
        decision_record_id="rec-guard-notrade",
        event=event,
        decision_event_id="evt-guard",
        tenant_id="tenant-001",
        user_id="user-001",
        body_decision="reject",
        rationale="skip",
        modifications=None,
        decision_time="2026-11-27T02:00:00Z",
    )
    with pytest.raises(DecisionLockInvalidError):
        apply_paper_proxy_fill_to_decision_lock(
            no_trade_lock,
            {"fill_qty": 1000, "fill_price": 900.0, "broker_order_id": "ord-x"},
        )


def test_partial_fill_and_reduce_intent_progression_and_net_position():
    """Multiple intents (enter filled + reduce partially_filled) report distinct intent
    progress stages, populate each decision lock's human_actual arm as pantheon_paper_proxy,
    and compute net paper position accurately.
    """
    env = _InMemoryPaperBookEnvironment()
    book = env.ensure_book("tenant-001", "user-001")
    env.store.upsert_decision_event(_decision_event("evt-enter-1"))
    env.store.upsert_decision_event(_decision_event("evt-reduce-2", **{"event_kind": "reduce", "suggested_action": "reduce"}))

    # Intent 1: enter 1000 shares -> filled at 980.0
    d1 = env.client.post(
        "/bff/agora/trading-room/decision-events/evt-enter-1/decisions",
        headers=_write_headers("trader-1", "idem-m1-d"),
        json={"decision": "approve"},
    ).json()["data"]
    int1 = d1["intent_ref"]
    hof1 = f"hof-{int1}"
    assert env.client.post(
        f"/bff/agora/trading-intents/{int1}/handoffs",
        headers=_write_headers("trader-1", "idem-m1-h"),
        json=_handoff_payload(int1, handoff_id=hof1, action="enter", quantity=1000),
    ).status_code == 202
    env.record_broker_paper_order(
        capital_pool_id=book["capital_pool_id"],
        client_order_id=agora_intent_signal_id(hof1),
        symbol="2330.TW",
        side="Buy",
        qty=1000,
        fill_price=980.0,
        status="filled",
        filled_at="2026-11-27T02:01:00Z",
    )

    # Intent 2: reduce 1000 shares -> partially_filled (400 shares at 1010.0)
    d2 = env.client.post(
        "/bff/agora/trading-room/decision-events/evt-reduce-2/decisions",
        headers=_write_headers("trader-1", "idem-m2-d"),
        json={"decision": "approve"},
    ).json()["data"]
    int2 = d2["intent_ref"]
    hof2 = f"hof-{int2}"
    assert env.client.post(
        f"/bff/agora/trading-intents/{int2}/handoffs",
        headers=_write_headers("trader-1", "idem-m2-h"),
        json=_handoff_payload(int2, handoff_id=hof2, action="reduce", quantity=1000),
    ).status_code == 202

    service_hdrs = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Pantheon-Service-Actor": SERVICE_ACTOR,
        "X-Tenant-Id": "tenant-001",
    }
    assert env.client.post(
        f"/internal/agora/paper-execution/handoffs/{hof2}/state",
        headers=service_hdrs,
        json={
            "state": "accepted",
            "environment": "paper",
            "no_order_route_proof": "agora_request_only_no_order_route",
            "runtime_binding_id": book["runtime_binding_id"],
            "capital_pool_id": book["capital_pool_id"],
            "signal_id": agora_intent_signal_id(hof2),
            "daily_slot": 2,
            "market_price": 1010.0,
            "accepted_at": "2026-11-27T02:02:00Z",
        },
    ).status_code == 200
    assert env.client.post(
        f"/internal/agora/paper-execution/handoffs/{hof2}/state",
        headers=service_hdrs,
        json={
            "state": "partially_filled",
            "environment": "paper",
            "no_order_route_proof": "agora_request_only_no_order_route",
            "runtime_binding_id": book["runtime_binding_id"],
            "capital_pool_id": book["capital_pool_id"],
            "signal_id": agora_intent_signal_id(hof2),
            "broker_order_id": "ord-partial-2",
            "fill_qty": 400.0,
            "fill_price": 1010.0,
            "filled_at": "2026-11-27T02:02:30Z",
        },
    ).status_code == 200

    resp = env.client.get(
        "/bff/agora/trading-room/paper-book",
        headers={"Authorization": "Bearer trader-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["fills"]) == 2
    assert all(f["execution_observation_kind"] == "pantheon_paper_proxy" for f in data["fills"])
    assert all(f["actual_fill_claimed"] is False for f in data["fills"])
    assert all(f["is_real_fill"] is False for f in data["fills"])

    by_intent = {row["intent_id"]: row for row in data["intents"]}
    assert by_intent[int1]["progress_stage"] == "filled"
    assert by_intent[int2]["progress_stage"] == "partially_filled"
    assert by_intent[int2]["fill_qty"] == 400.0

    # Net paper position: +1000 - 400 = +600 shares long.
    assert len(data["positions"]) == 1
    assert data["positions"][0]["symbol"] == "2330.TW"
    assert data["positions"][0]["net_quantity"] == 600.0
    assert data["positions"][0]["direction"] == "long"
    assert data["positions"][0]["fill_count"] == 2

    # Both decision locks have human_actual populated with pantheon_paper_proxy fills.
    for ev_id, expected_qty in (("evt-enter-1", 1000.0), ("evt-reduce-2", 400.0)):
        lk = env.client.get(
            f"/bff/agora/trading-room/decision-events/{ev_id}/decision-lock",
            headers={"Authorization": "Bearer trader-1"},
        ).json()["data"]
        assert lk["arms"]["human_actual"]["execution_observation_kind"] == "pantheon_paper_proxy"
        assert lk["arms"]["human_actual"]["actual_fill_claimed"] is False
        assert lk["arms"]["human_actual"]["fill_qty"] == expected_qty
        assert verify_checksum(lk) is True


def test_read_surface_binding_telemetry_client_enforces_tenant_scope():
    """BindingTelemetryReadClient reads runtime telemetry from read_surface when wired
    and rejects any cross-tenant summary.
    """
    summaries = {
        "rt-1": {
            "tenant_id": "tenant-001",
            "runtime_id": "rt-1",
            "positions": [{"symbol": "2317.TW", "quantity": 500, "avg_entry_price": 180.0}],
        },
        "rt-foreign": {
            "tenant_id": "tenant-999",
            "runtime_id": "rt-foreign",
            "positions": [{"symbol": "2317.TW", "quantity": 9999, "avg_entry_price": 180.0}],
        },
    }

    class _FakeReadStore:
        def get_telemetry_summary(self, runtime_id: str) -> Optional[Dict[str, Any]]:
            return summaries.get(runtime_id)

    client = BindingTelemetryReadClient(get_read_store=lambda: _FakeReadStore())
    own = client.get_binding_telemetry(tenant_id="tenant-001", runtime_id="rt-1")
    assert own["available"] is True
    assert own["source"] == "read_surface"
    assert own["summary"]["positions"][0]["symbol"] == "2317.TW"

    foreign = client.get_binding_telemetry(tenant_id="tenant-001", runtime_id="rt-foreign")
    assert foreign["available"] is False
    assert foreign["summary"] is None

