"""Trader-confirmed Agora intents executed in the trader's own paper book.

AGORA-PAPER-INTENT-EXECUTION-20261127 (SD-AGORA-SERVANT-CORE-CAPABILITIES-20261127
§8.3 step 3, task T8).

This is the reader of Agora ``target_queue=paper_execution`` handoffs. It runs
inside the ``paper-signal-producer`` cycle (no new service) and, for each open
handoff of its tenants:

1. validates identity: the handoff is a paper request with Agora's
   request-only proof, its owner tenant is the drained tenant, and the active
   paper RuntimeBinding it executes on belongs to the trader's servant persona
   (the trader paper book from PAPER-TRADER-BOOK-PROVISION-20261127);
2. validates the order (symbol, side, size, limit) and reads a stored Source
   market price for it (never a fabricated price);
3. enforces the writer budget: at most ``AGORA_PAPER_INTENT_DAILY_CAP``
   executed intents per trader per Taipei day, rejecting the rest with
   ``daily_cap``;
4. writes ``accepted`` back to Agora, then enqueues one paper signal on the
   binding queue through the existing ``DecisionSignalProducer``
   (idempotent on the handoff id). The paper runtime's existing order-level
   risk gate and the broker paper simulator execute it;
5. reads the broker paper order back and writes ``filled`` or
   ``partially_filled`` (or ``rejected`` with ``broker_rejected``).

Agora holds no order route: it only stores the request and the written-back
state. Every signal produced here is ``environment=paper`` with
``is_real_order=False``; a non-paper handoff or binding is rejected and
nothing on this path can reach canary or live.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from services.execution.lean_runtime.instrument_candidates import TAIPEI_TZ
from services.execution.lean_runtime.symbol_parser import is_executable_symbol

log = logging.getLogger(__name__)

# Writer budget (docs/operations/storage-manifest.json runtime_budgets).
AGORA_PAPER_INTENT_DAILY_CAP = 50
AGORA_PAPER_INTENT_DRAIN_LIMIT = 100
SERVICE_ACTOR = "paper-signal-producer"
SOURCE_WORKER = "paper-signal-producer.agora-intent"
NO_ORDER_ROUTE_PROOF = "agora_request_only_no_order_route"
DEFAULT_MAX_PRICE_AGE_SECONDS = 4 * 24 * 60 * 60
DEFAULT_MAX_HANDOFF_AGE_SECONDS = 24 * 60 * 60

_SIGNAL_NAMESPACE = uuid.UUID("6f1d3c52-6a9b-4f0e-9a57-0b6a1e4c2d11")
_NON_PAPER_STAGE_KEYS = ("deployment_mode", "execution_mode", "deployment_stage", "environment", "target_stage")


class AgoraHandoffConflict(RuntimeError):
    """Agora refused the state transition (for example, a withdrawn handoff)."""


def _parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _finite_positive(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def agora_intent_signal_id(handoff_id: str) -> str:
    """Replay-stable signal identity of one Agora paper handoff."""
    return str(uuid.uuid5(_SIGNAL_NAMESPACE, f"agora-paper-intent:{handoff_id}"))


# ---------------------------------------------------------------------------
# Owners this consumer talks to
# ---------------------------------------------------------------------------


class AgoraHandoffClient:
    """Service client for the Agora paper execution boundary (BFF internal routes)."""

    def __init__(self, base_url: str, token: str, *, actor: str = SERVICE_ACTOR, timeout: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._actor = actor
        self._timeout = timeout

    def _request(self, method: str, path: str, tenant_id: str, body: Mapping[str, Any] | None = None) -> Any:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
            "X-Pantheon-Service-Actor": self._actor,
            "X-Tenant-Id": tenant_id,
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self._base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                raise AgoraHandoffConflict(f"Agora refused {method} {path}: 409") from exc
            raise

    def list_open(self, tenant_id: str, *, limit: int = AGORA_PAPER_INTENT_DRAIN_LIMIT) -> list[dict[str, Any]]:
        payload = self._request(
            "GET", f"/internal/agora/paper-execution/handoffs?limit={int(limit)}", tenant_id
        )
        items = payload.get("items") if isinstance(payload, Mapping) else None
        return [dict(item) for item in items or [] if isinstance(item, Mapping)]

    def write_state(self, tenant_id: str, handoff_id: str, state: str, result: Mapping[str, Any]) -> dict[str, Any]:
        body = {
            **{k: v for k, v in result.items() if v is not None},
            "state": state,
            "environment": "paper",
            "no_order_route_proof": NO_ORDER_ROUTE_PROOF,
        }
        path = f"/internal/agora/paper-execution/handoffs/{urllib.parse.quote(handoff_id, safe='')}/state"
        return self._request("POST", path, tenant_id, body)


def source_snapshot_price_reader(symbol: str) -> dict[str, Any] | None:
    """Latest stored Source close for *symbol*; ``None`` when Source has none.

    Transport faults raise so the handoff stays pending instead of being
    rejected for a transient outage.
    """
    base = (os.getenv("PANTHEON_SOURCE_INGEST_URL") or os.getenv("PANTHEON_SOURCE_INGEST_API_URL") or "").rstrip("/")
    if not base:
        raise RuntimeError("PANTHEON_SOURCE_INGEST_URL is required to price an Agora paper intent")
    url = f"{base}/api/source-ingest/snapshots/latest?symbol={urllib.parse.quote(symbol, safe='')}"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET"), timeout=5
        ) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    if not isinstance(payload, Mapping):
        return None
    closes = payload.get("closes")
    if not isinstance(closes, Sequence) or isinstance(closes, (str, bytes)) or not closes:
        return None
    price = _finite_positive(closes[-1])
    if price is None:
        return None
    return {
        "price": price,
        "as_of": str(payload.get("event_time") or payload.get("observed_at") or ""),
        "source_ref": str(payload.get("source_ref") or payload.get("snapshot_id") or ""),
    }


class BrokerPaperOrderReader:
    """Reads the broker paper simulator's order for one signal (client_order_id)."""

    def __init__(self, broker_url: str, *, timeout: float = 5.0) -> None:
        self._base = broker_url.rstrip("/")
        self._timeout = timeout

    def find(self, capital_pool_id: str, client_order_id: str) -> dict[str, Any] | None:
        query = urllib.parse.urlencode({"capital_pool_id": capital_pool_id, "limit": 500})
        req = urllib.request.Request(
            f"{self._base}/api/broker/paper/orders?{query}", headers={"Accept": "application/json"}, method="GET"
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        for order in reversed((payload or {}).get("orders") or []):
            if isinstance(order, Mapping) and str(order.get("client_order_id") or "") == client_order_id:
                return dict(order)
        return None


# ---------------------------------------------------------------------------
# Consumer
# ---------------------------------------------------------------------------


class _Rejected(Exception):
    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail[:300]


class _EnqueueRejected(_Rejected):
    """DecisionSignalProducer refused to build the signal."""


def _binding_meta(binding: Mapping[str, Any]) -> Mapping[str, Any]:
    meta = binding.get("metadata")
    return meta if isinstance(meta, Mapping) else {}


def _binding_persona(binding: Mapping[str, Any]) -> str:
    meta = _binding_meta(binding)
    for source in (meta, binding):
        for key in ("persona_id", "sponsor_persona_id"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def _binding_non_paper(binding: Mapping[str, Any]) -> str | None:
    meta = _binding_meta(binding)
    for source in (binding, meta):
        for key in _NON_PAPER_STAGE_KEYS:
            value = source.get(key)
            if value not in (None, "") and str(value).strip().lower() != "paper":
                return f"{key}={value}"
    return None


class AgoraPaperIntentConsumer:
    """Consume Agora paper handoffs inside the paper-signal-producer cycle."""

    def __init__(
        self,
        *,
        client: Any,
        store_for: Callable[[Any], Any],
        tenants: Sequence[str],
        price_reader: Callable[[str], Mapping[str, Any] | None] = source_snapshot_price_reader,
        order_reader: Any | None = None,
        daily_cap: int = AGORA_PAPER_INTENT_DAILY_CAP,
        max_price_age_seconds: float = DEFAULT_MAX_PRICE_AGE_SECONDS,
        max_handoff_age_seconds: float = DEFAULT_MAX_HANDOFF_AGE_SECONDS,
        drain_limit: int = AGORA_PAPER_INTENT_DRAIN_LIMIT,
    ) -> None:
        if daily_cap < 1 or daily_cap > AGORA_PAPER_INTENT_DAILY_CAP:
            raise ValueError(f"daily_cap must be within 1..{AGORA_PAPER_INTENT_DAILY_CAP}")
        self._client = client
        self._store_for = store_for
        self._tenants = [t for t in (str(x).strip() for x in tenants) if t]
        self._price_reader = price_reader
        self._order_reader = order_reader
        self._daily_cap = int(daily_cap)
        self._max_price_age = float(max_price_age_seconds)
        self._max_handoff_age = float(max_handoff_age_seconds)
        self._drain_limit = int(drain_limit)

    # -- public --------------------------------------------------------------

    def consume(self, bindings: Sequence[Any], now_iso: str) -> dict[str, int]:
        """Process every open handoff of the configured tenants once."""
        summary = {"accepted": 0, "rejected": 0, "filled": 0, "partially_filled": 0, "pending": 0, "errors": 0}
        paper_bindings = [b for b in (self._binding_dict(b) for b in bindings) if b]
        for tenant_id in self._tenants:
            try:
                handoffs = self._client.list_open(tenant_id, limit=self._drain_limit)
            except Exception as exc:  # noqa: BLE001 - one tenant's drain fault must not stop the cycle
                log.warning("agora paper intent drain failed for tenant %s: %s", tenant_id, exc)
                summary["errors"] += 1
                continue
            for handoff in handoffs:
                try:
                    outcome = self._process(tenant_id, handoff, paper_bindings, now_iso)
                except AgoraHandoffConflict as exc:
                    log.info("agora paper intent %s skipped: %s", handoff.get("handoff_id"), exc)
                    outcome = "pending"
                except Exception as exc:  # noqa: BLE001 - keep the cycle alive; retry next tick
                    log.warning("agora paper intent %s failed: %s", handoff.get("handoff_id"), exc)
                    outcome = "errors"
                summary[outcome] = summary.get(outcome, 0) + 1
        return summary

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _binding_dict(binding: Any) -> dict[str, Any] | None:
        if isinstance(binding, Mapping):
            return dict(binding)
        to_dict = getattr(binding, "__dict__", None)
        return dict(to_dict) if isinstance(to_dict, Mapping) else None

    def _process(
        self, tenant_id: str, handoff: Mapping[str, Any], bindings: Sequence[Mapping[str, Any]], now_iso: str
    ) -> str:
        handoff_id = str(handoff.get("handoff_id") or "").strip()
        if not handoff_id:
            return "errors"
        state = str(handoff.get("state") or "")
        try:
            plan = self._validate(tenant_id, handoff, bindings)
        except _Rejected as rejected:
            if state == "submitted":
                return self._reject(tenant_id, handoff_id, rejected)
            # An accepted handoff whose binding vanished keeps waiting for its fill read.
            log.warning("agora paper intent %s no longer validates: %s", handoff_id, rejected)
            return "pending"
        store = self._store_for(plan["binding"])
        if state == "submitted":
            return self._admit(tenant_id, handoff, plan, store, now_iso)
        if state in {"accepted", "partially_filled"}:
            return self._follow(tenant_id, handoff, plan, store, now_iso)
        return "pending"

    def _validate(
        self, tenant_id: str, handoff: Mapping[str, Any], bindings: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        block = handoff.get("paper_execution") if isinstance(handoff.get("paper_execution"), Mapping) else {}
        if (
            handoff.get("target_queue") != "paper_execution"
            or handoff.get("handoff_type") != "paper_execution_request"
            or handoff.get("requested_stage") != "paper"
            or block.get("environment") != "paper"
        ):
            raise _Rejected("non_paper_stage", "only paper_execution_request handoffs at stage paper execute here")
        if handoff.get("no_order_route_proof") != NO_ORDER_ROUTE_PROOF:
            raise _Rejected("non_paper_stage", "handoff lacks Agora's request-only proof")
        owner = block.get("owner") if isinstance(block.get("owner"), Mapping) else {}
        persona_id = str(owner.get("servant_persona_id") or "").strip()
        if str(owner.get("tenant_id") or "") != tenant_id or not persona_id or not owner.get("user_id"):
            raise _Rejected("owner_mismatch", "handoff owner does not match the drained tenant")
        order = block.get("order") if isinstance(block.get("order"), Mapping) else {}
        symbol = str(order.get("symbol") or "").strip()
        side = str(order.get("side") or "")
        quantity = _finite_positive(order.get("quantity"))
        order_type = str(order.get("order_type") or "")
        limit_price = _finite_positive(order.get("limit_price"))
        if (
            not symbol
            or side not in {"buy", "sell"}
            or quantity is None
            or order.get("quantity_type") != "SHARES"
            or order_type not in {"market", "limit"}
            or (order_type == "limit" and limit_price is None)
            or (order_type == "market" and order.get("limit_price") is not None)
        ):
            raise _Rejected("invalid_order", "order needs symbol, side, positive SHARES quantity and a valid limit")
        if not is_executable_symbol(symbol):
            raise _Rejected("symbol_not_executable", f"{symbol!r} is not an executable symbol")

        matches = [b for b in bindings if _binding_persona(b) == persona_id]
        if not matches:
            raise _Rejected("paper_book_missing", f"no active paper RuntimeBinding for servant {persona_id}")
        if len(matches) > 1:
            raise _Rejected("owner_mismatch", f"servant {persona_id} has {len(matches)} active paper bindings")
        binding = matches[0]
        non_paper = _binding_non_paper(binding)
        if non_paper:
            raise _Rejected("binding_not_paper", f"binding {binding.get('binding_id')} has {non_paper}")
        binding_tenant = str(_binding_meta(binding).get("tenant_id") or binding.get("tenant_id") or "").strip()
        if binding_tenant and binding_tenant != tenant_id:
            raise _Rejected("owner_mismatch", "paper binding belongs to another tenant")
        capital_pool_id = str(binding.get("capital_pool_id") or _binding_meta(binding).get("capital_pool_id") or "")
        binding_id = str(binding.get("binding_id") or "").strip()
        if not capital_pool_id or not binding_id:
            raise _Rejected("paper_book_missing", "paper binding lacks binding_id or capital_pool_id")
        return {
            "binding": binding,
            "binding_id": binding_id,
            "runtime_id": str(binding.get("runtime_id") or "").strip() or None,
            "capital_pool_id": capital_pool_id,
            "persona_id": persona_id,
            "owner": dict(owner),
            "block": dict(block),
            "symbol": symbol,
            "side": side,
            "action": str(order.get("action") or ""),
            "quantity": quantity,
            "order_type": order_type,
            "limit_price": limit_price,
        }

    def _reject(self, tenant_id: str, handoff_id: str, rejected: _Rejected) -> str:
        self._client.write_state(
            tenant_id, handoff_id, "rejected", {"reason_code": rejected.reason_code, "detail": rejected.detail}
        )
        log.info("agora paper intent %s rejected: %s", handoff_id, rejected)
        return "rejected"

    def _claim_daily_slot(self, store: Any, persona_id: str, handoff_id: str, now: datetime) -> int | None:
        day = now.astimezone(TAIPEI_TZ).date().isoformat()
        for slot in range(1, self._daily_cap + 1):
            key = f"agora-intent-cap:{persona_id}:{day}:{slot}"
            record = store.put_decision_record(key, {"handoff_id": handoff_id, "day": day})
            if str((record or {}).get("handoff_id") or "") == handoff_id:
                return slot
        return None

    def _admit(
        self, tenant_id: str, handoff: Mapping[str, Any], plan: Mapping[str, Any], store: Any, now_iso: str
    ) -> str:
        handoff_id = str(handoff["handoff_id"])
        now = _parse_ts(now_iso) or datetime.now(timezone.utc)
        created = _parse_ts(handoff.get("created_at"))
        if created is not None and (now - created).total_seconds() > self._max_handoff_age:
            return self._reject(tenant_id, handoff_id, _Rejected("expired", "handoff is older than the execution window"))

        admission = store.get_decision_record(f"agora-intent:{handoff_id}")
        if admission is None:
            try:
                quote = self._price_reader(plan["symbol"])
            except Exception as exc:  # noqa: BLE001 - transient Source fault: retry next tick
                log.warning("agora paper intent %s price read failed: %s", handoff_id, exc)
                return "pending"
            price = _finite_positive((quote or {}).get("price"))
            if price is None:
                return self._reject(
                    tenant_id, handoff_id,
                    _Rejected("market_data_unavailable", f"Source has no stored price for {plan['symbol']}"),
                )
            as_of = _parse_ts((quote or {}).get("as_of"))
            if as_of is None or (now - as_of).total_seconds() > self._max_price_age:
                return self._reject(
                    tenant_id, handoff_id,
                    _Rejected("market_data_stale", f"latest stored price for {plan['symbol']} is too old"),
                )
            slot = self._claim_daily_slot(store, plan["persona_id"], handoff_id, now)
            if slot is None:
                return self._reject(
                    tenant_id, handoff_id,
                    _Rejected("daily_cap", f"{self._daily_cap} paper intents already executed today"),
                )
            admission = store.put_decision_record(
                f"agora-intent:{handoff_id}",
                {
                    "handoff_id": handoff_id,
                    "signal_id": agora_intent_signal_id(handoff_id),
                    "daily_slot": slot,
                    "binding_id": plan["binding_id"],
                    "capital_pool_id": plan["capital_pool_id"],
                    "market_price": price,
                    "market_as_of": str((quote or {}).get("as_of") or ""),
                    "market_source_ref": str((quote or {}).get("source_ref") or ""),
                    "accepted_at": now_iso,
                },
            )
        result = self._accepted_result(admission)
        # Accept first (compare-and-set on submitted): a handoff the trader
        # withdrew meanwhile is refused by Agora and nothing is enqueued.
        self._client.write_state(tenant_id, handoff_id, "accepted", result)
        try:
            self._enqueue(handoff, plan, store, admission, now_iso)
        except _EnqueueRejected as rejected:
            self._client.write_state(
                tenant_id, handoff_id, "rejected",
                {**result, "reason_code": rejected.reason_code, "detail": rejected.detail},
            )
            return "rejected"
        return "accepted"

    @staticmethod
    def _accepted_result(admission: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "runtime_binding_id": admission.get("binding_id"),
            "capital_pool_id": admission.get("capital_pool_id"),
            "signal_id": admission.get("signal_id"),
            "daily_slot": admission.get("daily_slot"),
            "market_price": admission.get("market_price"),
            "market_as_of": admission.get("market_as_of") or None,
            "market_source_ref": admission.get("market_source_ref") or None,
            "accepted_at": admission.get("accepted_at"),
        }

    def _enqueue(
        self,
        handoff: Mapping[str, Any],
        plan: Mapping[str, Any],
        store: Any,
        admission: Mapping[str, Any],
        now_iso: str,
    ) -> None:
        handoff_id = str(handoff["handoff_id"])
        if store.get_decision_record(f"agora-intent-enqueued:{handoff_id}") is not None:
            return
        from services.execution.lean_runtime.signal_producer import DecisionSignalProducer

        signal_id = str(admission["signal_id"])
        price = float(admission["market_price"])
        block = plan["block"]
        decision: dict[str, Any] = {
            "decision_id": f"agora-intent:{handoff_id}",
            "signal_id": signal_id,
            "strategy_id": str(handoff.get("strategy_id") or f"agora-intent:{plan['persona_id']}"),
            "symbol": plan["symbol"],
            "action": "BUY" if plan["side"] == "buy" else "SELL",
            "direction": "LONG",
            "quantity": plan["quantity"],
            "quantity_type": "SHARES",
            "order_type": "LIMIT" if plan["order_type"] == "limit" else "MARKET",
            "tenant_id": plan["owner"]["tenant_id"],
            "environment": "paper",
            "timestamp": now_iso,
            "metadata": {
                "is_real_order": False,
                "is_real_capital": False,
                "environment": "paper",
                "tenant_id": plan["owner"]["tenant_id"],
                "capital_pool_id": plan["capital_pool_id"],
                "market_price": price,
                "market_data": {
                    "price": price,
                    "as_of": admission.get("market_as_of"),
                    "source_ref": admission.get("market_source_ref"),
                },
                "rebalance_batch_size": 1,
                "source": "agora_trader_intent",
                "execution_observation_kind": "pantheon_paper_proxy",
                "agora_handoff_id": handoff_id,
                "agora_intent_id": str(handoff.get("intent_id") or ""),
                "decision_lock_ref": block.get("decision_lock_ref"),
                "decision_event_id": block.get("decision_event_id"),
                "owner_user_id": plan["owner"].get("user_id"),
                "servant_persona_id": plan["persona_id"],
                "daily_slot": admission.get("daily_slot"),
            },
        }
        if plan["limit_price"] is not None:
            decision["limit_price"] = plan["limit_price"]
        try:
            DecisionSignalProducer(store).produce(
                decision,
                now_iso=now_iso,
                strategy_id=decision["strategy_id"],
                source_worker=SOURCE_WORKER,
                binding_id=plan["binding_id"],
                runtime_id=plan["runtime_id"],
                run_id=f"agora-intent-{handoff_id}",
            )
        except ValueError as exc:  # SignalProducerValidationError is a ValueError
            raise _EnqueueRejected("signal_validation_failed", str(exc)) from exc
        store.put_decision_record(
            f"agora-intent-enqueued:{handoff_id}", {"signal_id": signal_id, "enqueued_at": now_iso}
        )
        log.info("agora paper intent %s enqueued as %s on %s", handoff_id, signal_id, plan["binding_id"])

    def _follow(
        self, tenant_id: str, handoff: Mapping[str, Any], plan: Mapping[str, Any], store: Any, now_iso: str
    ) -> str:
        handoff_id = str(handoff["handoff_id"])
        admission = store.get_decision_record(f"agora-intent:{handoff_id}")
        if admission is None:
            log.warning("agora paper intent %s is accepted but has no admission record", handoff_id)
            return "pending"
        try:
            self._enqueue(handoff, plan, store, admission, now_iso)
        except _EnqueueRejected as rejected:
            if handoff.get("state") == "accepted":
                self._client.write_state(
                    tenant_id, handoff_id, "rejected",
                    {**self._accepted_result(admission), "reason_code": rejected.reason_code, "detail": rejected.detail},
                )
                return "rejected"
            raise
        if self._order_reader is None:
            return "pending"
        order = self._order_reader.find(plan["capital_pool_id"], str(admission["signal_id"]))
        if order is None:
            return "pending"
        status = str(order.get("status") or "").lower()
        base = self._accepted_result(admission)
        if status == "rejected":
            if handoff.get("state") != "accepted":
                return "pending"
            self._client.write_state(
                tenant_id, handoff_id, "rejected",
                {**base, "reason_code": "broker_rejected", "detail": str(order.get("reject_reason") or "")[:300],
                 "broker_order_id": order.get("order_id")},
            )
            return "rejected"
        fill_qty = _finite_positive(order.get("fill_qty"))
        fill_price = _finite_positive(order.get("fill_price"))
        if status != "filled" or fill_qty is None or fill_price is None:
            return "pending"
        state = "filled" if fill_qty + 1e-9 >= plan["quantity"] else "partially_filled"
        if state == handoff.get("state") and state == "partially_filled":
            prior = ((handoff.get("paper_execution") or {}).get("result") or {}).get("fill_qty")
            if _finite_positive(prior) == fill_qty:
                return "pending"
        self._client.write_state(
            tenant_id, handoff_id, state,
            {**base, "broker_order_id": order.get("order_id"), "fill_qty": fill_qty,
             "fill_price": fill_price, "filled_at": order.get("filled_at")},
        )
        return state


_PUBLIC_DEFAULT_TOKENS = frozenset({
    "pantheon-local-agora-paper-execution-service-token",
    "replace-me-agora-paper-execution-service-token",
})
_PLACEHOLDER_TOKEN_PREFIXES = ("replace-me", "changeme", "change-me", "example", "dummy", "placeholder")


def _is_public_or_placeholder_token(token: str) -> bool:
    normalized = str(token or "").strip().lower()
    return normalized in _PUBLIC_DEFAULT_TOKENS or normalized.startswith(_PLACEHOLDER_TOKEN_PREFIXES)


def consumer_status_from_env(
    store_for: Callable[[Any], Any],
) -> tuple[AgoraPaperIntentConsumer | None, dict[str, Any]]:
    """Build the consumer and a readable health detail for its state.

    The consumer stays off (``state=not_configured``) when the BFF URL, service
    token or tenant list is missing, or when the token is the public compose
    default or a placeholder outside the explicit local profile
    (``AGORA_PAPER_EXECUTION_TOKEN_PROFILE=local``, never in staging/prod).
    ``PANTHEON_AGORA_PAPER_INTENT_CONSUMER=off`` turns it off. It never raises:
    a missing secret must not stop the producer or turn it unhealthy.
    """
    switch = os.getenv("PANTHEON_AGORA_PAPER_INTENT_CONSUMER", "auto").strip().lower()
    if switch in {"0", "off", "false", "no", "disabled"}:
        return None, {"state": "off", "reason": "disabled_by_switch"}
    base_url = (os.getenv("PANTHEON_AGORA_BFF_INTERNAL_URL") or "").strip()
    token = (os.getenv("AGORA_PAPER_EXECUTION_SERVICE_TOKEN") or "").strip()
    tenants = [t.strip() for t in (os.getenv("AGORA_PAPER_EXECUTION_SERVICE_TENANTS") or "").split(",") if t.strip()]
    posture = (os.getenv("PANTHEON_PERSISTENCE_POSTURE") or os.getenv("PANTHEON_ENV") or "dev").strip().lower()
    profile = (os.getenv("AGORA_PAPER_EXECUTION_TOKEN_PROFILE") or "").strip().lower()
    local_profile = profile == "local" and not posture.startswith(("stage", "staging", "prod"))
    reason: str | None = None
    if not base_url:
        reason = "bff_internal_url_unset"
    elif not token:
        reason = "service_token_unset"
    elif _is_public_or_placeholder_token(token) and not local_profile:
        reason = "service_token_public_default_or_placeholder"
    elif not tenants:
        reason = "service_tenants_unset"
    token_profile = "local" if local_profile else "hosted"
    if reason is not None:
        return None, {"state": "not_configured", "reason": reason, "token_profile": token_profile}
    broker_url = (os.getenv("PANTHEON_BROKER_PAPER_URL") or "http://broker:8102").strip()
    consumer = AgoraPaperIntentConsumer(
        client=AgoraHandoffClient(base_url, token),
        store_for=store_for,
        tenants=tenants,
        order_reader=BrokerPaperOrderReader(broker_url) if broker_url else None,
    )
    return consumer, {"state": "configured", "token_profile": token_profile, "tenant_count": len(tenants)}


def consumer_from_env(store_for: Callable[[Any], Any]) -> AgoraPaperIntentConsumer | None:
    """Return the consumer when configured, else ``None`` (consumer off)."""
    consumer, _status = consumer_status_from_env(store_for)
    return consumer


__all__ = [
    "AGORA_PAPER_INTENT_DAILY_CAP",
    "AgoraHandoffClient",
    "AgoraHandoffConflict",
    "AgoraPaperIntentConsumer",
    "BrokerPaperOrderReader",
    "agora_intent_signal_id",
    "consumer_from_env",
    "consumer_status_from_env",
    "source_snapshot_price_reader",
]
