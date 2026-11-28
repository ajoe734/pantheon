"""Owner-scoped readback of a trader's paper book (fills, positions, and intent progress).

AGORA-PAPER-BOOK-READBACK-20261127 (SD_AGORA_SERVANT_CORE_2026-11-27 §8.3 step 4, §12 T9):
- Reads the trader's own paper orders and fills from the paper broker read surface
  and runtime binding telemetry from the telemetry read surface.
- Labels every paper fill as ``pantheon_paper_proxy`` (never a real or verified broker
  fill: ``actual_fill_claimed=False``, ``execution_verified=False``, ``is_real_fill=False``).
- Populates the linked ``AgoraDecisionLock``'s ``human_actual`` arm with the
  ``pantheon_paper_proxy`` fill observation.
- Enforces strict owner scoping on ``(tenant_id, user_id)``: another trader (or any
  query targeting another trader's book, pool, binding, decision event, or intent)
  receives a non-enumerating 404.
- Agora remains read-only here: this module only issues GET requests to paper broker
  and telemetry read endpoints and never submits, modifies, or cancels orders.
"""
from __future__ import annotations

import json
import logging
import math
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from services.agora.shadow_mode import DEFAULT_CONFIDENCE, DISPLAY_LABELS
from services.control_plane.bff.agora.servant.router import _stable_servant_persona_id
from services.control_plane.bff.personas.trader_paper_book import (
    DEFAULT_TRADER_PAPER_STARTING_CAPITAL,
    read_trader_paper_book,
    trader_paper_book_ids,
    trader_paper_runtime_id,
)

_logger = logging.getLogger("agora.trading_room.paper_book_readback")

PAPER_PROXY_OBSERVATION_KIND = "pantheon_paper_proxy"
PAPER_PROXY_DISPLAY_LABEL = DISPLAY_LABELS[PAPER_PROXY_OBSERVATION_KIND]
PAPER_PROXY_CONFIDENCE = DEFAULT_CONFIDENCE[PAPER_PROXY_OBSERVATION_KIND]
PAPER_BOOK_READBACK_PROOF = "agora_paper_book_readback_only"

_SIGNAL_NAMESPACE = uuid.UUID("6f1d3c52-6a9b-4f0e-9a57-0b6a1e4c2d11")

_BROKER_PAPER_URL_ENVS = (
    "PANTHEON_BROKER_PAPER_URL",
    "BROKER_PAPER_URL",
    "PANTHEON_BROKER_API_URL",
    "BROKER_SERVICE_URL",
)
_TELEMETRY_URL_ENVS = (
    "PANTHEON_TELEMETRY_API_URL",
    "PANTHEON_TELEMETRY_URL",
    "TELEMETRY_SERVICE_URL",
    "TELEMETRY_API_URL",
)
_TELEMETRY_TOKEN_ENVS = (
    "TELEMETRY_SERVICE_TOKEN",
    "PANTHEON_TELEMETRY_SERVICE_TOKEN",
    "AGORA_PAPER_EXECUTION_SERVICE_TOKEN",
    "AGORA_HANDOFF_SERVICE_TOKEN",
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _opt_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return num


def agora_intent_signal_id(handoff_id: str) -> str:
    """Deterministic signal/client_order_id for an Agora paper handoff (matches consumer)."""
    return str(uuid.uuid5(_SIGNAL_NAMESPACE, f"agora-paper-intent:{_clean(handoff_id)}"))


def _direction_or_action_to_side(direction: Optional[str], action: Optional[str]) -> str:
    act = _clean(action).lower()
    dir_norm = _clean(direction).lower()
    if act in {"buy", "sell", "short", "cover"}:
        return act
    if dir_norm == "short":
        return "Buy" if act in {"exit", "reduce", "close"} else "Sell"
    if act in {"exit", "reduce", "close"}:
        return "Sell"
    return "Buy"


def _signed_position_delta(side: Optional[str], fill_qty: float) -> float:
    norm = _clean(side).lower()
    if norm in {"sell", "short", "exit", "reduce"}:
        return -abs(fill_qty)
    return abs(fill_qty)


class BrokerPaperBookOrderClient:
    """Read-only client for the paper broker's GET /api/broker/paper/orders surface."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        timeout_seconds: float = 3.0,
        urlopen: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._urlopen = urlopen

    def _resolve_base_url(self) -> str:
        if self._base_url is not None and _clean(self._base_url):
            return _clean(self._base_url).rstrip("/")
        for env_name in _BROKER_PAPER_URL_ENVS:
            val = _clean(os.getenv(env_name))
            if val:
                return val.rstrip("/")
        return ""

    def list_paper_orders(
        self,
        *,
        capital_pool_id: str,
        limit: int = 200,
    ) -> Dict[str, Any]:
        clean_pool = _clean(capital_pool_id)
        if not clean_pool:
            return {
                "status": "unavailable",
                "orders": [],
                "source": "unconfigured",
                "unavailable_reason": "capital_pool_id_missing",
            }
        base_url = self._resolve_base_url()
        if not base_url and self._urlopen is None:
            return {
                "status": "unavailable",
                "orders": [],
                "source": "unconfigured",
                "unavailable_reason": "broker_paper_url_unconfigured",
            }
        effective_base = base_url or "http://broker:8102"
        bounded_limit = max(1, min(int(limit), 500))
        qs = urllib.parse.urlencode({"capital_pool_id": clean_pool, "limit": str(bounded_limit)})
        url = f"{effective_base}/api/broker/paper/orders?{qs}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        opener = self._urlopen or urllib.request.urlopen
        try:
            with opener(req, timeout=max(0.1, float(self._timeout_seconds))) as resp:
                raw = resp.read()
                text = (
                    raw.decode("utf-8").strip()
                    if isinstance(raw, (bytes, bytearray))
                    else str(raw or "").strip()
                )
                payload = json.loads(text) if text else {}
        except urllib.error.HTTPError as exc:
            _logger.warning("BrokerPaperBookOrderClient HTTP %s from %s", exc.code, url)
            return {
                "status": "unavailable",
                "orders": [],
                "source": "broker_paper_orders",
                "unavailable_reason": f"broker_paper_http_{exc.code}",
            }
        except Exception as exc:
            _logger.warning("BrokerPaperBookOrderClient request failed for %s: %s", url, exc)
            return {
                "status": "unavailable",
                "orders": [],
                "source": "broker_paper_orders",
                "unavailable_reason": "broker_paper_request_failed",
            }

        raw_orders = payload.get("orders") if isinstance(payload, Mapping) else None
        if not isinstance(raw_orders, Sequence):
            return {
                "status": "unavailable",
                "orders": [],
                "source": "broker_paper_orders",
                "unavailable_reason": "broker_paper_invalid_payload",
            }
        scoped_orders: List[Dict[str, Any]] = []
        for item in raw_orders:
            if not isinstance(item, Mapping):
                continue
            item_pool = _clean(item.get("capital_pool_id"))
            if item_pool and item_pool != clean_pool:
                continue
            scoped_orders.append(dict(item))
        return {
            "status": "ok",
            "orders": scoped_orders,
            "source": "broker_paper_orders",
            "unavailable_reason": None,
        }


class BindingTelemetryReadClient:
    """Read-only client for the trader paper book binding's runtime telemetry summary."""

    def __init__(
        self,
        *,
        get_read_store: Optional[Callable[[], Any]] = None,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_seconds: float = 3.0,
        urlopen: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._get_read_store = get_read_store
        self._base_url = base_url
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._urlopen = urlopen

    def _resolve_base_url(self) -> str:
        if self._base_url is not None and _clean(self._base_url):
            return _clean(self._base_url).rstrip("/")
        for env_name in _TELEMETRY_URL_ENVS:
            val = _clean(os.getenv(env_name))
            if val:
                return val.rstrip("/")
        return ""

    def _resolve_token(self) -> str:
        if self._token is not None and _clean(self._token):
            return _clean(self._token)
        for env_name in _TELEMETRY_TOKEN_ENVS:
            val = _clean(os.getenv(env_name))
            if val:
                return val
        return ""

    def get_binding_telemetry(
        self,
        *,
        tenant_id: str,
        runtime_id: str,
        runtime_binding_id: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        clean_runtime_id = _clean(runtime_id)
        clean_tenant = _clean(tenant_id)
        if not clean_runtime_id:
            return {
                "available": False,
                "source": "unconfigured",
                "unavailable_reason": "runtime_id_missing",
                "summary": None,
            }

        # 1. Try canonical BFF read_store / read_surface if wired.
        if self._get_read_store is not None:
            try:
                read_store = self._get_read_store()
                if read_store is not None and hasattr(read_store, "get_telemetry_summary"):
                    summary = read_store.get_telemetry_summary(clean_runtime_id)
                    if isinstance(summary, Mapping) and summary:
                        summary_tenant = _clean(summary.get("tenant_id"))
                        if not summary_tenant or summary_tenant == clean_tenant:
                            return {
                                "available": True,
                                "source": "read_surface",
                                "unavailable_reason": None,
                                "summary": dict(summary),
                            }
            except Exception as exc:
                _logger.warning(
                    "BindingTelemetryReadClient read_surface lookup failed for runtime_id=%s: %s",
                    clean_runtime_id,
                    exc,
                )

        # 2. Try telemetry service HTTP GET /api/telemetry/runtime-summaries/{runtime_id}.
        base_url = self._resolve_base_url()
        if not base_url and self._urlopen is None:
            return {
                "available": False,
                "source": "unconfigured",
                "unavailable_reason": "telemetry_url_unconfigured",
                "summary": None,
            }
        effective_base = base_url or "http://telemetry:8105"
        quoted_runtime = urllib.parse.quote(clean_runtime_id, safe="")
        url = f"{effective_base}/api/telemetry/runtime-summaries/{quoted_runtime}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        token = self._resolve_token()
        if token:
            req.add_unredirected_header("Authorization", f"Bearer {token}")
        if clean_tenant:
            req.add_unredirected_header("X-Tenant-Id", clean_tenant)
            req.add_unredirected_header("X-Pantheon-Tenant", clean_tenant)
        opener = self._urlopen or urllib.request.urlopen
        try:
            with opener(req, timeout=max(0.1, float(self._timeout_seconds))) as resp:
                raw = resp.read()
                text = (
                    raw.decode("utf-8").strip()
                    if isinstance(raw, (bytes, bytearray))
                    else str(raw or "").strip()
                )
                payload = json.loads(text) if text else {}
        except urllib.error.HTTPError as exc:
            return {
                "available": False,
                "source": "telemetry_service",
                "unavailable_reason": f"telemetry_http_{exc.code}",
                "summary": None,
            }
        except Exception as exc:
            _logger.warning("BindingTelemetryReadClient request failed for %s: %s", url, exc)
            return {
                "available": False,
                "source": "telemetry_service",
                "unavailable_reason": "telemetry_request_failed",
                "summary": None,
            }

        summary_obj: Optional[Mapping[str, Any]] = None
        if isinstance(payload, Mapping):
            if isinstance(payload.get("summary"), Mapping):
                summary_obj = payload["summary"]
            elif isinstance(payload.get("data"), Mapping):
                summary_obj = payload["data"]
            elif payload.get("runtime_id") or payload.get("positions") is not None:
                summary_obj = payload

        if not isinstance(summary_obj, Mapping):
            return {
                "available": False,
                "source": "telemetry_service",
                "unavailable_reason": "telemetry_summary_empty",
                "summary": None,
            }
        summary_tenant = _clean(summary_obj.get("tenant_id"))
        if summary_tenant and clean_tenant and summary_tenant != clean_tenant:
            return {
                "available": False,
                "source": "telemetry_service",
                "unavailable_reason": "telemetry_scope_mismatch",
                "summary": None,
            }
        summary_pool = _clean(summary_obj.get("capital_pool_id"))
        if summary_pool and _clean(capital_pool_id) and summary_pool != _clean(capital_pool_id):
            return {
                "available": False,
                "source": "telemetry_service",
                "unavailable_reason": "telemetry_pool_mismatch",
                "summary": None,
            }
        summary_binding = _clean(summary_obj.get("runtime_binding_id") or summary_obj.get("binding_id"))
        if summary_binding and _clean(runtime_binding_id) and summary_binding != _clean(runtime_binding_id):
            return {
                "available": False,
                "source": "telemetry_service",
                "unavailable_reason": "telemetry_binding_mismatch",
                "summary": None,
            }
        return {
            "available": True,
            "source": "telemetry_service",
            "unavailable_reason": None,
            "summary": dict(summary_obj),
        }


def _extract_telemetry_positions(
    telemetry_summary: Optional[Mapping[str, Any]],
    *,
    capital_pool_id: str,
    runtime_binding_id: str,
    runtime_id: str,
    servant_persona_id: str,
) -> Dict[str, Dict[str, Any]]:
    """Extract per-symbol position records from a binding telemetry summary payload."""
    if not isinstance(telemetry_summary, Mapping):
        return {}
    perf = (
        telemetry_summary.get("performance_telemetry")
        if isinstance(telemetry_summary.get("performance_telemetry"), Mapping)
        else {}
    )
    candidates: Any = (
        telemetry_summary.get("positions")
        or telemetry_summary.get("instrument_positions")
        or telemetry_summary.get("holdings")
        or perf.get("instrument_positions")
        or perf.get("positions")
        or []
    )
    raw_items: List[Mapping[str, Any]] = []
    if isinstance(candidates, Mapping):
        for sym_key, val in candidates.items():
            if isinstance(val, Mapping):
                raw_items.append({"symbol": sym_key, **dict(val)})
            elif _opt_float(val) is not None:
                raw_items.append({"symbol": sym_key, "quantity": _opt_float(val)})
    elif isinstance(candidates, Sequence) and not isinstance(candidates, (str, bytes)):
        for item in candidates:
            if isinstance(item, Mapping):
                raw_items.append(item)

    by_symbol: Dict[str, Dict[str, Any]] = {}
    for item in raw_items:
        symbol = _clean(item.get("symbol") or item.get("instrument") or item.get("ticker"))
        if not symbol:
            continue
        qty = _opt_float(
            item.get("quantity")
            if item.get("quantity") is not None
            else (
                item.get("net_quantity")
                if item.get("net_quantity") is not None
                else (item.get("qty") if item.get("qty") is not None else item.get("position"))
            )
        )
        if qty is None:
            qty = 0.0
        avg_price = _opt_float(
            item.get("avg_entry_price")
            if item.get("avg_entry_price") is not None
            else (item.get("avg_price") if item.get("avg_price") is not None else item.get("entry_price"))
        )
        mark_price = _opt_float(
            item.get("mark_price")
            if item.get("mark_price") is not None
            else (item.get("last_price") if item.get("last_price") is not None else item.get("market_price"))
        )
        unrealized_pnl = _opt_float(item.get("unrealized_pnl"))
        realized_pnl = _opt_float(item.get("realized_pnl"))
        direction = _clean(item.get("direction") or item.get("side")).lower()
        if not direction:
            direction = "long" if qty > 0 else ("short" if qty < 0 else "flat")
        by_symbol[symbol] = {
            "symbol": symbol,
            "quantity": qty,
            "net_quantity": qty,
            "direction": direction,
            "avg_entry_price": avg_price,
            "mark_price": mark_price,
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": realized_pnl,
            "fill_count": int(item.get("fill_count") or 0),
            "last_fill_price": _opt_float(item.get("last_fill_price")) or avg_price,
            "last_filled_at": _clean(item.get("last_filled_at") or item.get("updated_at")) or None,
            "capital_pool_id": capital_pool_id,
            "runtime_binding_id": runtime_binding_id,
            "runtime_id": runtime_id,
            "servant_persona_id": servant_persona_id,
            "source": "binding_telemetry",
            "telemetry_reported": True,
            "environment": "paper",
            "execution_observation_kind": PAPER_PROXY_OBSERVATION_KIND,
            "execution_display_label": PAPER_PROXY_DISPLAY_LABEL,
            "execution_confidence": PAPER_PROXY_CONFIDENCE,
            "execution_verified": False,
            "actual_fill_claimed": False,
            "is_real_position": False,
            "fill_ids": list(item.get("fill_ids") or []),
            "broker_order_ids": list(item.get("broker_order_ids") or []),
            "intent_ids": list(item.get("intent_ids") or []),
        }
    return by_symbol


def _derive_positions_from_fills(
    fills: Sequence[Mapping[str, Any]],
    *,
    telemetry_positions: Mapping[str, Dict[str, Any]],
    capital_pool_id: str,
    runtime_binding_id: str,
    runtime_id: str,
    servant_persona_id: str,
) -> List[Dict[str, Any]]:
    """Combine binding telemetry positions with the trader's paper fills."""
    merged: Dict[str, Dict[str, Any]] = {
        sym: dict(pos) for sym, pos in telemetry_positions.items()
    }
    fill_accum: Dict[str, Dict[str, Any]] = {}
    for fill in sorted(
        fills,
        key=lambda f: (_clean(f.get("filled_at")), _clean(f.get("fill_id"))),
    ):
        symbol = _clean(fill.get("symbol"))
        fill_qty = _opt_float(fill.get("fill_qty"))
        if not symbol or fill_qty is None or fill_qty <= 0:
            continue
        fill_price = _opt_float(fill.get("fill_price"))
        delta = _signed_position_delta(fill.get("side"), fill_qty)
        acc = fill_accum.setdefault(
            symbol,
            {
                "net_quantity": 0.0,
                "cost_basis_qty": 0.0,
                "cost_basis_notional": 0.0,
                "fill_count": 0,
                "last_fill_price": None,
                "last_filled_at": None,
                "fill_ids": [],
                "broker_order_ids": [],
                "intent_ids": [],
            },
        )
        prev_net = float(acc["net_quantity"])
        new_net = prev_net + delta
        if fill_price is not None:
            if prev_net == 0 or (prev_net > 0 and delta > 0) or (prev_net < 0 and delta < 0):
                acc["cost_basis_qty"] = abs(prev_net) + abs(delta)
                acc["cost_basis_notional"] = float(acc["cost_basis_notional"]) + abs(delta) * fill_price
            elif abs(new_net) > 0 and ((prev_net > 0 > new_net) or (prev_net < 0 < new_net)):
                acc["cost_basis_qty"] = abs(new_net)
                acc["cost_basis_notional"] = abs(new_net) * fill_price
            elif abs(new_net) == 0:
                acc["cost_basis_qty"] = 0.0
                acc["cost_basis_notional"] = 0.0
            acc["last_fill_price"] = fill_price
        acc["net_quantity"] = new_net
        acc["fill_count"] = int(acc["fill_count"]) + 1
        if _clean(fill.get("filled_at")):
            acc["last_filled_at"] = _clean(fill.get("filled_at"))
        for key, list_key in (
            ("fill_id", "fill_ids"),
            ("broker_order_id", "broker_order_ids"),
            ("intent_id", "intent_ids"),
        ):
            val = _clean(fill.get(key))
            if val and val not in acc[list_key]:
                acc[list_key].append(val)

    for symbol, acc in fill_accum.items():
        avg_entry = (
            round(float(acc["cost_basis_notional"]) / float(acc["cost_basis_qty"]), 6)
            if float(acc["cost_basis_qty"]) > 0
            else acc["last_fill_price"]
        )
        net_qty = float(acc["net_quantity"])
        direction = "long" if net_qty > 0 else ("short" if net_qty < 0 else "flat")
        if symbol in merged:
            existing = merged[symbol]
            if not existing.get("avg_entry_price") and avg_entry is not None:
                existing["avg_entry_price"] = avg_entry
            if not existing.get("last_fill_price") and acc["last_fill_price"] is not None:
                existing["last_fill_price"] = acc["last_fill_price"]
            if not existing.get("last_filled_at") and acc["last_filled_at"] is not None:
                existing["last_filled_at"] = acc["last_filled_at"]
            existing["fill_count"] = max(int(existing.get("fill_count") or 0), int(acc["fill_count"]))
            for list_key in ("fill_ids", "broker_order_ids", "intent_ids"):
                cur_list = list(existing.get(list_key) or [])
                for v in acc[list_key]:
                    if v not in cur_list:
                        cur_list.append(v)
                existing[list_key] = cur_list
            existing["source"] = "binding_telemetry+broker_paper_fills"
        else:
            merged[symbol] = {
                "symbol": symbol,
                "quantity": net_qty,
                "net_quantity": net_qty,
                "direction": direction,
                "avg_entry_price": avg_entry,
                "mark_price": acc["last_fill_price"],
                "unrealized_pnl": 0.0 if acc["last_fill_price"] is not None else None,
                "realized_pnl": None,
                "fill_count": int(acc["fill_count"]),
                "last_fill_price": acc["last_fill_price"],
                "last_filled_at": acc["last_filled_at"],
                "capital_pool_id": capital_pool_id,
                "runtime_binding_id": runtime_binding_id,
                "runtime_id": runtime_id,
                "servant_persona_id": servant_persona_id,
                "source": "broker_paper_fills",
                "telemetry_reported": False,
                "environment": "paper",
                "execution_observation_kind": PAPER_PROXY_OBSERVATION_KIND,
                "execution_display_label": PAPER_PROXY_DISPLAY_LABEL,
                "execution_confidence": PAPER_PROXY_CONFIDENCE,
                "execution_verified": False,
                "actual_fill_claimed": False,
                "is_real_position": False,
                "fill_ids": list(acc["fill_ids"]),
                "broker_order_ids": list(acc["broker_order_ids"]),
                "intent_ids": list(acc["intent_ids"]),
            }

    return [merged[sym] for sym in sorted(merged.keys())]


def _intent_owned_by_trader(
    intent: Mapping[str, Any],
    *,
    tenant_id: str,
    user_id: str,
) -> bool:
    meta = intent.get("metadata") if isinstance(intent.get("metadata"), Mapping) else {}
    intent_tenant = _clean(intent.get("tenant_id") or meta.get("tenant_id"))
    intent_user = _clean(
        meta.get("user_id")
        or meta.get("agora_user_id")
        or intent.get("user_id")
        or intent.get("operator_id")
    )
    if intent_tenant and intent_tenant != _clean(tenant_id):
        return False
    return bool(intent_user and intent_user == _clean(user_id))


def _handoff_owned_by_trader(
    handoff: Mapping[str, Any],
    *,
    tenant_id: str,
    user_id: str,
    owned_intent_ids: set[str],
) -> bool:
    pe = handoff.get("paper_execution") if isinstance(handoff.get("paper_execution"), Mapping) else {}
    owner = pe.get("owner") if isinstance(pe.get("owner"), Mapping) else {}
    hof_tenant = _clean(handoff.get("tenant_id") or owner.get("tenant_id"))
    hof_user = _clean(owner.get("agora_user_id") or owner.get("user_id"))
    if hof_tenant and hof_tenant != _clean(tenant_id):
        return False
    if hof_user:
        return hof_user == _clean(user_id)
    req_by = handoff.get("requested_by") if isinstance(handoff.get("requested_by"), Mapping) else {}
    actor_ref = _clean(req_by.get("actor_ref"))
    if actor_ref and actor_ref == _clean(user_id) and (not hof_tenant or hof_tenant == _clean(tenant_id)):
        return True
    return _clean(handoff.get("intent_id")) in owned_intent_ids


def _extract_handoff_lock_refs(pe: Mapping[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    raw_ref = pe.get("decision_lock_ref")
    human_decision_id = ""
    decision_event_id = _clean(pe.get("decision_event_id"))
    if isinstance(raw_ref, Mapping):
        human_decision_id = _clean(raw_ref.get("human_decision_id") or raw_ref.get("lock_id"))
        if not decision_event_id:
            decision_event_id = _clean(raw_ref.get("decision_event_id"))
    elif isinstance(raw_ref, str):
        human_decision_id = _clean(raw_ref)
    return (human_decision_id or None), (decision_event_id or None)


def sync_decision_lock_paper_proxy_fill(
    *,
    decision_lock_owner: Any,
    tenant_id: str,
    user_id: Optional[str],
    human_decision_id: Optional[str],
    decision_event_id: Optional[str],
    fill: Mapping[str, Any],
    actor_roles: Sequence[str] = ("system",),
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Best-effort idempotent write of a ``pantheon_paper_proxy`` fill onto the
    decision lock's ``human_actual`` arm.
    """
    if decision_lock_owner is None or not hasattr(
        decision_lock_owner, "record_decision_lock_paper_proxy_fill"
    ):
        return False, None
    clean_lock_id = _clean(human_decision_id)
    clean_event_id = _clean(decision_event_id)
    if not clean_lock_id and not clean_event_id:
        return False, None
    try:
        updated = decision_lock_owner.record_decision_lock_paper_proxy_fill(
            clean_lock_id,
            tenant_id=_clean(tenant_id),
            user_id=_clean(user_id) or None,
            decision_event_id=clean_event_id or None,
            fill=dict(fill),
            actor_roles=actor_roles,
        )
        if updated is not None:
            return True, dict(updated)
    except Exception as exc:
        _logger.warning(
            "Failed to sync pantheon_paper_proxy fill to decision lock %s (event=%s): %s",
            clean_lock_id,
            clean_event_id,
            exc,
        )
    return False, None


def build_trader_paper_book_readback(
    *,
    store: Any,
    decision_lock_owner: Any,
    tenant_id: str,
    user_id: str,
    actor_roles: Sequence[str] = ("trader",),
    broker_order_client: Optional[Any] = None,
    binding_telemetry_client: Optional[Any] = None,
    paper_book_reader: Optional[Callable[..., Optional[Dict[str, Any]]]] = None,
    intent_id_filter: Optional[str] = None,
    decision_event_id_filter: Optional[str] = None,
    handoff_id_filter: Optional[str] = None,
    requested_user_id: Optional[str] = None,
    requested_tenant_id: Optional[str] = None,
    requested_servant_persona_id: Optional[str] = None,
    requested_capital_pool_id: Optional[str] = None,
    requested_runtime_binding_id: Optional[str] = None,
    requested_runtime_id: Optional[str] = None,
    allow_unprovisioned_empty: bool = False,
    utc_now: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    """Assemble the owner-scoped paper book readback for ``(tenant_id, user_id)``.

    Raises ``LookupError`` with reason code (``"paper_book_not_found"`` or
    ``"intent_not_found"``) whenever a filter targets another trader's resource or
    when the caller has no paper book or matching intent.
    """
    clean_tenant = _clean(tenant_id)
    clean_user = _clean(user_id)
    if not clean_tenant or not clean_user:
        raise LookupError("paper_book_not_found")

    # 1. Reject any explicit query filter targeting another tenant or user with 404.
    if _clean(requested_tenant_id) and _clean(requested_tenant_id) != clean_tenant:
        raise LookupError("paper_book_not_found")
    if _clean(requested_user_id) and _clean(requested_user_id) != clean_user:
        raise LookupError("paper_book_not_found")

    # 2. Compute deterministic trader paper book identifiers.
    expected_servant_persona_id = _stable_servant_persona_id(
        tenant_id=clean_tenant,
        agora_user_id=clean_user,
    )
    expected_ids = trader_paper_book_ids(clean_tenant, expected_servant_persona_id)
    expected_runtime_id = trader_paper_runtime_id(expected_ids)
    expected_runtime_binding_id = f"rb-persona-paper-{expected_ids.token}"

    # 3. Attempt to read provisioned paper book record (if persona store is available).
    provisioned_book: Optional[Dict[str, Any]] = None
    reader = paper_book_reader or read_trader_paper_book
    try:
        raw_book = reader(
            clean_tenant,
            expected_servant_persona_id,
            agora_user_id=clean_user,
        )
        if isinstance(raw_book, Mapping):
            book_user = _clean(raw_book.get("agora_user_id") or raw_book.get("owner_user_id"))
            book_tenant = _clean(raw_book.get("tenant_id"))
            if (not book_tenant or book_tenant == clean_tenant) and (
                not book_user or book_user == clean_user
            ):
                provisioned_book = dict(raw_book)
    except Exception:
        provisioned_book = None

    # 4. Collect the caller's own intents, paper handoffs, and decision locks.
    owned_intents: List[Dict[str, Any]] = []
    owned_intent_by_id: Dict[str, Dict[str, Any]] = {}
    owned_handoffs: List[Dict[str, Any]] = []
    owned_handoffs_by_intent: Dict[str, List[Dict[str, Any]]] = {}
    owned_handoff_by_id: Dict[str, Dict[str, Any]] = {}

    if hasattr(store, "list_trader_paper_book_records"):
        records = store.list_trader_paper_book_records(
            tenant_id=clean_tenant,
            user_id=clean_user,
        )
        for intent in records.get("intents") or []:
            if not isinstance(intent, Mapping):
                continue
            intent_copy = dict(intent)
            iid = _clean(intent_copy.get("intent_id"))
            owned_intents.append(intent_copy)
            if iid:
                owned_intent_by_id[iid] = intent_copy
        for handoff in records.get("handoffs") or []:
            if not isinstance(handoff, Mapping):
                continue
            hof_copy = dict(handoff)
            hid = _clean(hof_copy.get("handoff_id"))
            iid = _clean(hof_copy.get("intent_id"))
            owned_handoffs.append(hof_copy)
            if hid:
                owned_handoff_by_id[hid] = hof_copy
            if iid:
                owned_handoffs_by_intent.setdefault(iid, []).append(hof_copy)
    else:
        all_intents = (
            list(store.list_intents())
            if hasattr(store, "list_intents")
            else (
                list(getattr(store, "_intents").values())
                if hasattr(store, "_intents") and isinstance(getattr(store, "_intents"), Mapping)
                else []
            )
        )
        for intent in all_intents:
            if not isinstance(intent, Mapping):
                continue
            iid = _clean(intent.get("intent_id"))
            if _intent_owned_by_trader(intent, tenant_id=clean_tenant, user_id=clean_user):
                intent_copy = dict(intent)
                owned_intents.append(intent_copy)
                if iid:
                    owned_intent_by_id[iid] = intent_copy

        all_handoffs_raw: List[Mapping[str, Any]] = []
        if hasattr(store, "_handoffs") and isinstance(getattr(store, "_handoffs"), Mapping):
            all_handoffs_raw = list(getattr(store, "_handoffs").values())
        owned_intent_ids = set(owned_intent_by_id.keys())
        for handoff in all_handoffs_raw:
            if not isinstance(handoff, Mapping):
                continue
            if not _handoff_owned_by_trader(
                handoff,
                tenant_id=clean_tenant,
                user_id=clean_user,
                owned_intent_ids=owned_intent_ids,
            ):
                continue
            hof_copy = dict(handoff)
            hid = _clean(hof_copy.get("handoff_id"))
            iid = _clean(hof_copy.get("intent_id"))
            owned_handoffs.append(hof_copy)
            if hid:
                owned_handoff_by_id[hid] = hof_copy
            if iid:
                owned_handoffs_by_intent.setdefault(iid, []).append(hof_copy)

    # If intent_id_filter is provided, verify it exists AND belongs to this trader.
    clean_intent_filter = _clean(intent_id_filter)
    if clean_intent_filter:
        if clean_intent_filter not in owned_intent_by_id:
            raise LookupError("intent_not_found")

    clean_handoff_filter = _clean(handoff_id_filter)
    if clean_handoff_filter and clean_handoff_filter not in owned_handoff_by_id:
        raise LookupError("intent_not_found")

    # Collect trader's decision locks.
    owned_locks_by_id: Dict[str, Dict[str, Any]] = {}
    owned_locks_by_event: Dict[str, Dict[str, Any]] = {}
    if decision_lock_owner is not None:
        raw_locks: Sequence[Any] = ()
        try:
            if hasattr(decision_lock_owner, "store") and hasattr(
                decision_lock_owner.store, "list_trader_decision_locks"
            ):
                raw_locks = decision_lock_owner.store.list_trader_decision_locks(
                    clean_tenant, clean_user
                )
            elif hasattr(decision_lock_owner, "list_decision_locks"):
                raw_locks = decision_lock_owner.list_decision_locks(
                    tenant_id=clean_tenant,
                    user_id=clean_user,
                    actor_roles=actor_roles or ("trader",),
                )
        except Exception:
            raw_locks = ()
        for lk in raw_locks:
            if not isinstance(lk, Mapping):
                continue
            if _clean(lk.get("tenant_id")) != clean_tenant or _clean(lk.get("user_id")) != clean_user:
                continue
            lk_dict = dict(lk)
            hid = _clean(lk_dict.get("human_decision_id"))
            ev_id = _clean(lk_dict.get("decision_event_id"))
            if hid:
                owned_locks_by_id[hid] = lk_dict
            if ev_id:
                owned_locks_by_event[ev_id] = lk_dict

    # 5. Resolve canonical paper book IDs for this trader (preferring provisioned_book,
    # then trader's handoff results, then deterministic IDs).
    active_servant_persona_id = _clean(
        (provisioned_book or {}).get("servant_persona_id") or expected_servant_persona_id
    )
    active_capital_pool_id = _clean((provisioned_book or {}).get("capital_pool_id"))
    active_runtime_binding_id = _clean((provisioned_book or {}).get("runtime_binding_id"))
    active_runtime_id = _clean((provisioned_book or {}).get("runtime_id"))
    active_persona_capital_binding_id = _clean(
        (provisioned_book or {}).get("persona_capital_binding_id")
        or expected_ids.persona_capital_binding_id
    )

    for hof in owned_handoffs:
        pe = hof.get("paper_execution") if isinstance(hof.get("paper_execution"), Mapping) else {}
        res = pe.get("result") if isinstance(pe.get("result"), Mapping) else {}
        owner_block = pe.get("owner") if isinstance(pe.get("owner"), Mapping) else {}
        if not active_capital_pool_id and _clean(res.get("capital_pool_id")):
            active_capital_pool_id = _clean(res.get("capital_pool_id"))
        if not active_runtime_binding_id and _clean(res.get("runtime_binding_id")):
            active_runtime_binding_id = _clean(res.get("runtime_binding_id"))
        if not active_runtime_id and _clean(res.get("runtime_id")):
            active_runtime_id = _clean(res.get("runtime_id"))
        if _clean(owner_block.get("servant_persona_id")):
            active_servant_persona_id = _clean(owner_block.get("servant_persona_id"))

    if not active_capital_pool_id:
        active_capital_pool_id = expected_ids.capital_pool_id
    if not active_runtime_binding_id:
        active_runtime_binding_id = expected_runtime_binding_id
    if not active_runtime_id:
        active_runtime_id = expected_runtime_id

    allowed_persona_ids = {expected_servant_persona_id, active_servant_persona_id}
    allowed_pool_ids = {expected_ids.capital_pool_id, active_capital_pool_id}
    allowed_binding_ids = {expected_runtime_binding_id, active_runtime_binding_id}
    allowed_runtime_ids = {expected_runtime_id, active_runtime_id}

    if _clean(requested_servant_persona_id) and _clean(requested_servant_persona_id) not in allowed_persona_ids:
        raise LookupError("paper_book_not_found")
    if _clean(requested_capital_pool_id) and _clean(requested_capital_pool_id) not in allowed_pool_ids:
        raise LookupError("paper_book_not_found")
    if _clean(requested_runtime_binding_id) and _clean(requested_runtime_binding_id) not in allowed_binding_ids:
        raise LookupError("paper_book_not_found")
    if _clean(requested_runtime_id) and _clean(requested_runtime_id) not in allowed_runtime_ids:
        raise LookupError("paper_book_not_found")

    clean_event_filter = _clean(decision_event_id_filter)
    if clean_event_filter:
        matches_owned_event = clean_event_filter in owned_locks_by_event
        if not matches_owned_event:
            for intent in owned_intents:
                linked = [str(x) for x in (intent.get("linked_event_ids") or []) if _clean(x)]
                meta = intent.get("metadata") if isinstance(intent.get("metadata"), Mapping) else {}
                if clean_event_filter in linked or _clean(meta.get("decision_event_id")) == clean_event_filter:
                    matches_owned_event = True
                    break
        if not matches_owned_event:
            for hof in owned_handoffs:
                pe = hof.get("paper_execution") if isinstance(hof.get("paper_execution"), Mapping) else {}
                _hof_lock_id, hof_event_id = _extract_handoff_lock_refs(pe)
                if hof_event_id == clean_event_filter:
                    matches_owned_event = True
                    break
        if not matches_owned_event:
            raise LookupError("intent_not_found")

    # 6. Query broker paper orders for this trader's capital_pool_id.
    order_client = broker_order_client or BrokerPaperBookOrderClient()
    broker_orders_resp = order_client.list_paper_orders(capital_pool_id=active_capital_pool_id)
    broker_orders: List[Dict[str, Any]] = [
        dict(o)
        for o in (broker_orders_resp.get("orders") or [])
        if isinstance(o, Mapping)
        and (not _clean(o.get("capital_pool_id")) or _clean(o.get("capital_pool_id")) in allowed_pool_ids)
    ]

    # 7. Query binding telemetry for this trader's runtime_id.
    telemetry_client = binding_telemetry_client or BindingTelemetryReadClient()
    telemetry_resp = telemetry_client.get_binding_telemetry(
        tenant_id=clean_tenant,
        runtime_id=active_runtime_id,
        runtime_binding_id=active_runtime_binding_id,
        capital_pool_id=active_capital_pool_id,
    )
    telemetry_summary = (
        dict(telemetry_resp["summary"])
        if isinstance(telemetry_resp.get("summary"), Mapping)
        else None
    )

    # Check if this caller has any paper book presence at all.
    has_paper_book_presence = bool(
        provisioned_book is not None
        or owned_intents
        or owned_handoffs
        or owned_locks_by_id
        or broker_orders
        or telemetry_summary is not None
        or allow_unprovisioned_empty
    )
    if not has_paper_book_presence:
        raise LookupError("paper_book_not_found")

    orders_by_client_id: Dict[str, Dict[str, Any]] = {}
    orders_by_order_id: Dict[str, Dict[str, Any]] = {}
    for order in broker_orders:
        cid = _clean(order.get("client_order_id") or order.get("signal_id"))
        oid = _clean(order.get("order_id") or order.get("broker_order_id"))
        if cid:
            orders_by_client_id[cid] = order
        if oid:
            orders_by_order_id[oid] = order

    now_iso = utc_now() if callable(utc_now) else "2026-11-27T02:00:00Z"
    matched_order_ids: set[str] = set()
    fills: List[Dict[str, Any]] = []
    intent_progress_rows: List[Dict[str, Any]] = []

    def _process_intent_and_handoff(
        intent: Optional[Mapping[str, Any]],
        handoff: Optional[Mapping[str, Any]],
    ) -> None:
        intent_dict = dict(intent) if isinstance(intent, Mapping) else {}
        handoff_dict = dict(handoff) if isinstance(handoff, Mapping) else {}
        meta = intent_dict.get("metadata") if isinstance(intent_dict.get("metadata"), Mapping) else {}
        subject = intent_dict.get("subject") if isinstance(intent_dict.get("subject"), Mapping) else {}
        pe = (
            handoff_dict.get("paper_execution")
            if isinstance(handoff_dict.get("paper_execution"), Mapping)
            else {}
        )
        pe_order = pe.get("order") if isinstance(pe.get("order"), Mapping) else {}
        hof_human_decision_id, hof_decision_event_id = _extract_handoff_lock_refs(pe)
        pe_result = dict(pe.get("result")) if isinstance(pe.get("result"), Mapping) else {}
        proposal = (
            handoff_dict.get("action_proposal")
            if isinstance(handoff_dict.get("action_proposal"), Mapping)
            else {}
        )

        iid = _clean(intent_dict.get("intent_id") or handoff_dict.get("intent_id"))
        hid = _clean(handoff_dict.get("handoff_id"))
        if clean_intent_filter and iid != clean_intent_filter:
            return
        if clean_handoff_filter and hid != clean_handoff_filter:
            return

        linked_events = [str(e) for e in (intent_dict.get("linked_event_ids") or []) if _clean(e)]
        decision_event_id = (
            hof_decision_event_id
            or _clean(meta.get("decision_event_id"))
            or (linked_events[0] if linked_events else "")
            or None
        )
        if clean_event_filter and decision_event_id != clean_event_filter:
            return

        human_decision_id = (
            hof_human_decision_id
            or _clean(meta.get("human_decision_id"))
            or (
                _clean(owned_locks_by_event[decision_event_id].get("human_decision_id"))
                if decision_event_id and decision_event_id in owned_locks_by_event
                else ""
            )
            or None
        )
        if not decision_event_id and human_decision_id and human_decision_id in owned_locks_by_id:
            decision_event_id = _clean(owned_locks_by_id[human_decision_id].get("decision_event_id")) or None

        expected_sig = agora_intent_signal_id(hid) if hid else ""
        signal_id = _clean(pe_result.get("signal_id")) or expected_sig or None
        broker_order_id = _clean(pe_result.get("broker_order_id")) or None

        matched_order: Optional[Dict[str, Any]] = None
        if signal_id and signal_id in orders_by_client_id:
            matched_order = orders_by_client_id[signal_id]
        elif broker_order_id and broker_order_id in orders_by_order_id:
            matched_order = orders_by_order_id[broker_order_id]
        elif expected_sig and expected_sig in orders_by_client_id:
            matched_order = orders_by_client_id[expected_sig]
            if not signal_id:
                signal_id = expected_sig

        if matched_order is not None:
            oid = _clean(matched_order.get("order_id") or matched_order.get("broker_order_id"))
            cid = _clean(matched_order.get("client_order_id") or matched_order.get("signal_id"))
            if oid:
                matched_order_ids.add(oid)
                broker_order_id = oid
            if cid:
                matched_order_ids.add(cid)
                signal_id = cid

        symbol = (
            _clean(pe_order.get("symbol"))
            or _clean(proposal.get("symbol"))
            or _clean((matched_order or {}).get("symbol"))
            or _clean(subject.get("symbol"))
            or None
        )
        strategy_id = (
            _clean(handoff_dict.get("strategy_id"))
            or _clean((matched_order or {}).get("strategy_id"))
            or _clean(subject.get("strategy_ref"))
            or None
        )
        direction = (
            _clean(pe_order.get("direction"))
            or _clean(proposal.get("direction"))
            or _clean(intent_dict.get("direction"))
            or "long"
        )
        action = (
            _clean(pe_order.get("action"))
            or _clean(proposal.get("action"))
            or _clean(intent_dict.get("intent_type"))
            or "enter"
        )
        side = (
            _clean((matched_order or {}).get("side"))
            or _direction_or_action_to_side(direction, action)
        )
        order_type = (
            _clean(pe_order.get("order_type"))
            or _clean(proposal.get("order_type"))
            or _clean((matched_order or {}).get("order_type"))
            or "market"
        )
        requested_qty = _opt_float(
            pe_order.get("quantity")
            if pe_order.get("quantity") is not None
            else (
                proposal.get("quantity")
                if proposal.get("quantity") is not None
                else (matched_order or {}).get("qty")
            )
        )
        limit_price = _opt_float(
            pe_order.get("limit_price")
            if pe_order.get("limit_price") is not None
            else (
                proposal.get("limit_price")
                if proposal.get("limit_price") is not None
                else (matched_order or {}).get("limit_price")
            )
        )

        broker_status = _clean((matched_order or {}).get("status")).lower() or None
        handoff_state = _clean(handoff_dict.get("state") or pe.get("status")).lower() or None
        fill_qty = _opt_float(
            (matched_order or {}).get("fill_qty")
            if (matched_order or {}).get("fill_qty") is not None
            else pe_result.get("fill_qty")
        )
        fill_price = _opt_float(
            (matched_order or {}).get("fill_price")
            if (matched_order or {}).get("fill_price") is not None
            else pe_result.get("fill_price")
        )
        filled_at = (
            _clean((matched_order or {}).get("filled_at"))
            or _clean(pe_result.get("filled_at"))
            or None
        )
        reason_code = (
            _clean(pe_result.get("reason_code"))
            or _clean((matched_order or {}).get("reject_reason"))
            or None
        )
        detail = _clean(pe_result.get("detail")) or None

        # Reconcile state if broker paper order shows fill/rejection before writeback completed.
        if broker_status in {"filled", "partially_filled"} and handoff_state not in {
            "filled",
            "partially_filled",
        }:
            handoff_state = broker_status
            if not detail:
                detail = "paper_order_filled"
            if hid and hasattr(store, "reconcile_paper_execution_fill"):
                store.reconcile_paper_execution_fill(
                    hid,
                    tenant_id=clean_tenant,
                    state=broker_status,
                    result={
                        "signal_id": signal_id,
                        "broker_order_id": broker_order_id,
                        "capital_pool_id": active_capital_pool_id,
                        "runtime_binding_id": active_runtime_binding_id,
                        "fill_qty": fill_qty,
                        "fill_price": fill_price,
                        "filled_at": filled_at,
                        "detail": detail,
                    },
                    updated_at=filled_at or now_iso,
                )
        elif broker_status == "rejected" and handoff_state not in {"rejected", "cancelled", "expired"}:
            handoff_state = "rejected"

        if not broker_status and handoff_state in {"filled", "partially_filled", "rejected"}:
            broker_status = handoff_state

        is_filled = (
            handoff_state in {"filled", "partially_filled"}
            or broker_status in {"filled", "partially_filled"}
            or (fill_qty is not None and fill_qty > 0 and fill_price is not None)
        )
        if is_filled and fill_qty is None and requested_qty is not None:
            fill_qty = requested_qty
        if is_filled and not filled_at:
            filled_at = _clean(handoff_dict.get("updated_at") or handoff_dict.get("created_at")) or now_iso

        intent_fills: List[Dict[str, Any]] = []
        lock_populated = False
        if is_filled:
            fill_status = (
                "partially_filled"
                if (handoff_state == "partially_filled" or broker_status == "partially_filled")
                else "filled"
            )
            fill_id = (
                f"pfill-{broker_order_id}"
                if broker_order_id
                else (f"pfill-{signal_id}" if signal_id else f"pfill-{hid or iid}")
            )
            fill_entry: Dict[str, Any] = {
                "fill_id": fill_id,
                "decision_id": human_decision_id,
                "human_decision_id": human_decision_id,
                "arm": "human_actual",
                "broker_order_id": broker_order_id,
                "client_order_id": signal_id,
                "signal_id": signal_id,
                "handoff_id": hid or None,
                "intent_id": iid or None,
                "decision_event_id": decision_event_id,
                "capital_pool_id": active_capital_pool_id,
                "runtime_binding_id": active_runtime_binding_id,
                "runtime_id": active_runtime_id,
                "servant_persona_id": active_servant_persona_id,
                "strategy_id": strategy_id,
                "symbol": symbol,
                "action": action,
                "side": side,
                "direction": direction,
                "order_type": order_type,
                "requested_quantity": requested_qty,
                "quantity": requested_qty if requested_qty is not None else fill_qty,
                "fill_qty": fill_qty,
                "fill_price": fill_price,
                "filled_at": filled_at,
                "fill_status": fill_status,
                "fill_source": (
                    "broker_paper_orders" if matched_order is not None else "paper_execution_writeback"
                ),
                "environment": "paper",
                "settlement_source": PAPER_PROXY_OBSERVATION_KIND,
                "execution_observation_kind": PAPER_PROXY_OBSERVATION_KIND,
                "execution_display_label": PAPER_PROXY_DISPLAY_LABEL,
                "execution_confidence": PAPER_PROXY_CONFIDENCE,
                "execution_verified": False,
                "actual_fill_claimed": False,
                "is_real_fill": False,
                "no_order_route_proof": PAPER_BOOK_READBACK_PROOF,
            }
            lock_synced, synced_lock = sync_decision_lock_paper_proxy_fill(
                decision_lock_owner=decision_lock_owner,
                tenant_id=clean_tenant,
                user_id=clean_user,
                human_decision_id=human_decision_id,
                decision_event_id=decision_event_id,
                fill=fill_entry,
                actor_roles=("system", "trader"),
            )
            if lock_synced and isinstance(synced_lock, Mapping):
                lock_populated = True
                synced_hid = _clean(synced_lock.get("human_decision_id"))
                if synced_hid:
                    owned_locks_by_id[synced_hid] = dict(synced_lock)
                    fill_entry["human_decision_id"] = synced_hid
                    human_decision_id = synced_hid
            elif human_decision_id and human_decision_id in owned_locks_by_id:
                existing_lock = owned_locks_by_id[human_decision_id]
                ha = (
                    (existing_lock.get("arms") or {}).get("human_actual")
                    if isinstance(existing_lock.get("arms"), Mapping)
                    else {}
                )
                if isinstance(ha, Mapping) and isinstance(ha.get("paper_fill"), Mapping):
                    lock_populated = True
            fill_entry["human_actual_arm_populated"] = lock_populated
            fills.append(fill_entry)
            intent_fills.append(fill_entry)

        if handoff_state == "filled" or (is_filled and handoff_state != "partially_filled"):
            lifecycle_state = "filled"
            progress_stage = "filled"
        elif handoff_state == "partially_filled":
            lifecycle_state = "PartiallyFilled"
            progress_stage = "partially_filled"
        elif handoff_state in {"queued", "running", "accepted"}:
            lifecycle_state = "handoff_in_progress"
            progress_stage = handoff_state
        elif handoff_state == "submitted":
            lifecycle_state = "handoff_submitted"
            progress_stage = "submitted"
        elif handoff_state in {"rejected", "cancelled", "expired"}:
            lifecycle_state = f"handoff_{handoff_state}"
            progress_stage = handoff_state
        else:
            lifecycle_state = "recorded"
            progress_stage = "intent_recorded"

        intent_progress_rows.append(
            {
                "intent_id": iid or None,
                "decision_event_id": decision_event_id,
                "human_decision_id": human_decision_id,
                "strategy_id": strategy_id,
                "symbol": symbol,
                "intent_type": _clean(intent_dict.get("intent_type")) or None,
                "direction": direction,
                "action": action,
                "side": side,
                "order_type": order_type,
                "requested_quantity": requested_qty,
                "limit_price": limit_price,
                "lifecycle_state": lifecycle_state,
                "handoff_id": hid or None,
                "handoff_state": handoff_state,
                "progress_stage": progress_stage,
                "target_system": _clean(handoff_dict.get("target_system")) or (
                    "paper_execution" if hid else None
                ),
                "signal_id": signal_id,
                "broker_order_id": broker_order_id,
                "broker_order_status": broker_status,
                "fill_qty": fill_qty,
                "fill_price": fill_price,
                "filled_at": filled_at,
                "reason_code": reason_code,
                "detail": detail,
                "capital_pool_id": active_capital_pool_id,
                "runtime_binding_id": active_runtime_binding_id,
                "runtime_id": active_runtime_id,
                "servant_persona_id": active_servant_persona_id,
                "environment": "paper",
                "execution_observation_kind": PAPER_PROXY_OBSERVATION_KIND,
                "execution_display_label": PAPER_PROXY_DISPLAY_LABEL,
                "execution_confidence": PAPER_PROXY_CONFIDENCE,
                "execution_verified": False,
                "actual_fill_claimed": False,
                "is_real_fill": False,
                "human_actual_arm_populated": lock_populated,
                "binding_telemetry": {
                    "runtime_id": active_runtime_id,
                    "runtime_binding_id": active_runtime_binding_id,
                    "capital_pool_id": active_capital_pool_id,
                    "telemetry_available": bool(telemetry_resp.get("available")),
                    "telemetry_source": _clean(telemetry_resp.get("source")) or "unconfigured",
                    "last_event_at": (
                        _clean((telemetry_summary or {}).get("last_event_at"))
                        or _clean((telemetry_summary or {}).get("updated_at"))
                        or None
                    ),
                },
                "fills": intent_fills,
                "no_order_route_proof": PAPER_BOOK_READBACK_PROOF,
            }
        )

    processed_handoff_ids: set[str] = set()
    for intent in owned_intents:
        iid = _clean(intent.get("intent_id"))
        handoffs_for_intent = owned_handoffs_by_intent.get(iid, [])
        if handoffs_for_intent:
            for hof in handoffs_for_intent:
                hid = _clean(hof.get("handoff_id"))
                if hid:
                    processed_handoff_ids.add(hid)
                _process_intent_and_handoff(intent, hof)
        else:
            _process_intent_and_handoff(intent, None)

    for hof in owned_handoffs:
        hid = _clean(hof.get("handoff_id"))
        if hid and hid in processed_handoff_ids:
            continue
        iid = _clean(hof.get("intent_id"))
        _process_intent_and_handoff(owned_intent_by_id.get(iid), hof)

    # Include any additional filled paper orders in this trader's pool not yet linked above
    # (unless filtered to a specific intent_id or decision_event_id).
    if not clean_intent_filter and not clean_event_filter and not clean_handoff_filter:
        for order in broker_orders:
            oid = _clean(order.get("order_id") or order.get("broker_order_id"))
            cid = _clean(order.get("client_order_id") or order.get("signal_id"))
            if (oid and oid in matched_order_ids) or (cid and cid in matched_order_ids):
                continue
            status_norm = _clean(order.get("status")).lower()
            fill_qty = _opt_float(order.get("fill_qty"))
            fill_price = _opt_float(order.get("fill_price"))
            if status_norm not in {"filled", "partially_filled"} and (fill_qty is None or fill_qty <= 0):
                continue
            symbol = _clean(order.get("symbol")) or None
            side = _clean(order.get("side")) or "Buy"
            direction = "short" if side.lower() in {"sell", "short"} else "long"
            qty = _opt_float(order.get("qty")) or fill_qty
            fills.append(
                {
                    "fill_id": f"pfill-{oid or cid}",
                    "broker_order_id": oid or None,
                    "client_order_id": cid or None,
                    "signal_id": cid or None,
                    "handoff_id": None,
                    "intent_id": None,
                    "decision_event_id": None,
                    "human_decision_id": None,
                    "capital_pool_id": active_capital_pool_id,
                    "runtime_binding_id": active_runtime_binding_id,
                    "runtime_id": active_runtime_id,
                    "servant_persona_id": active_servant_persona_id,
                    "strategy_id": _clean(order.get("strategy_id")) or None,
                    "symbol": symbol,
                    "action": "exit" if side.lower() in {"sell", "short"} else "enter",
                    "side": side,
                    "direction": direction,
                    "order_type": _clean(order.get("order_type")) or "market",
                    "requested_quantity": qty,
                    "quantity": qty,
                    "fill_qty": fill_qty if fill_qty is not None else qty,
                    "fill_price": fill_price,
                    "filled_at": _clean(order.get("filled_at")) or now_iso,
                    "fill_status": "partially_filled" if status_norm == "partially_filled" else "filled",
                    "fill_source": "broker_paper_orders",
                    "environment": "paper",
                    "execution_observation_kind": PAPER_PROXY_OBSERVATION_KIND,
                    "execution_display_label": PAPER_PROXY_DISPLAY_LABEL,
                    "execution_confidence": PAPER_PROXY_CONFIDENCE,
                    "execution_verified": False,
                    "actual_fill_claimed": False,
                    "is_real_fill": False,
                    "human_actual_arm_populated": False,
                    "no_order_route_proof": PAPER_BOOK_READBACK_PROOF,
                }
            )

    telemetry_positions = _extract_telemetry_positions(
        telemetry_summary,
        capital_pool_id=active_capital_pool_id,
        runtime_binding_id=active_runtime_binding_id,
        runtime_id=active_runtime_id,
        servant_persona_id=active_servant_persona_id,
    )
    positions = _derive_positions_from_fills(
        fills,
        telemetry_positions=telemetry_positions,
        capital_pool_id=active_capital_pool_id,
        runtime_binding_id=active_runtime_binding_id,
        runtime_id=active_runtime_id,
        servant_persona_id=active_servant_persona_id,
    )

    telemetry_block: Dict[str, Any] = {
        "runtime_id": active_runtime_id,
        "runtime_binding_id": active_runtime_binding_id,
        "capital_pool_id": active_capital_pool_id,
        "available": bool(telemetry_resp.get("available")),
        "source": _clean(telemetry_resp.get("source")) or "unconfigured",
        "unavailable_reason": telemetry_resp.get("unavailable_reason"),
        "last_event_at": (
            _clean((telemetry_summary or {}).get("last_event_at"))
            or _clean((telemetry_summary or {}).get("updated_at"))
            or None
        ),
        "last_heartbeat_at": _clean((telemetry_summary or {}).get("last_heartbeat_at")) or None,
        "status": _clean((telemetry_summary or {}).get("status") or (telemetry_summary or {}).get("state"))
        or ("active" if telemetry_resp.get("available") else "paper_book_bound"),
        "equity": _opt_float((telemetry_summary or {}).get("equity")),
        "cash": _opt_float((telemetry_summary or {}).get("cash")),
        "realized_pnl": _opt_float((telemetry_summary or {}).get("realized_pnl")),
        "unrealized_pnl": _opt_float((telemetry_summary or {}).get("unrealized_pnl")),
        "position_count": len(positions),
        "fill_count": len(fills),
    }

    paper_book_summary: Dict[str, Any] = {
        "tenant_id": clean_tenant,
        "agora_user_id": clean_user,
        "user_id": clean_user,
        "servant_persona_id": active_servant_persona_id,
        "capital_pool_id": active_capital_pool_id,
        "persona_capital_binding_id": active_persona_capital_binding_id,
        "runtime_binding_id": active_runtime_binding_id,
        "runtime_id": active_runtime_id,
        "environment": "paper",
        "starting_capital": float(
            (provisioned_book or {}).get("starting_capital") or DEFAULT_TRADER_PAPER_STARTING_CAPITAL
        ),
        "currency": _clean((provisioned_book or {}).get("currency")) or "TWD",
        "execution_observation_kind": PAPER_PROXY_OBSERVATION_KIND,
        "execution_display_label": PAPER_PROXY_DISPLAY_LABEL,
        "actual_fill_claimed": False,
        "execution_verified": False,
        "is_real_book": False,
    }

    return {
        "spec_version": "1.0",
        "tenant_id": clean_tenant,
        "user_id": clean_user,
        "agora_user_id": clean_user,
        "environment": "paper",
        "execution_observation_kind": PAPER_PROXY_OBSERVATION_KIND,
        "execution_display_label": PAPER_PROXY_DISPLAY_LABEL,
        "execution_confidence": PAPER_PROXY_CONFIDENCE,
        "execution_verified": False,
        "actual_fill_claimed": False,
        "is_real_fill": False,
        "paper_book": paper_book_summary,
        "binding_telemetry": telemetry_block,
        "broker_paper_orders_source": {
            "status": _clean(broker_orders_resp.get("status")) or "unavailable",
            "source": _clean(broker_orders_resp.get("source")) or "broker_paper_orders",
            "order_count": len(broker_orders),
            "unavailable_reason": broker_orders_resp.get("unavailable_reason"),
        },
        "fills": fills,
        "positions": positions,
        "intents": intent_progress_rows,
        "total_fills": len(fills),
        "total_positions": len(positions),
        "total_intents": len(intent_progress_rows),
        "no_order_route_proof": PAPER_BOOK_READBACK_PROOF,
    }


__all__ = [
    "BindingTelemetryReadClient",
    "BrokerPaperBookOrderClient",
    "PAPER_BOOK_READBACK_PROOF",
    "PAPER_PROXY_CONFIDENCE",
    "PAPER_PROXY_DISPLAY_LABEL",
    "PAPER_PROXY_OBSERVATION_KIND",
    "agora_intent_signal_id",
    "build_trader_paper_book_readback",
    "sync_decision_lock_paper_proxy_fill",
]
