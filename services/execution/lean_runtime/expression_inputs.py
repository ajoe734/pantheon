"""Production inputs for the instrument expression layer (layer B/C).

The expression step needs two records per decision:

* ``risk_budget``: ``equity``, ``available_cash``, ``gross_limit`` (an absolute
  amount), ``gross_used`` and ``max_leverage``.
* ``persona_book``: the open positions per instrument and ``gross_notional``.

Both come from published truth, never from process state, because the signal
producer is a separate process from the paper runtime ledger:

* limits come only from the persona pool's governed risk policy, resolved
  through ``services.capital.risk_policy.resolve_bound_pool_risk_policy``. The
  policy states ``gross_limit`` and ``max_leverage`` as multiples of equity;
  this module turns the multiple into an absolute amount and hardcodes no
  number;
* cash and positions come only from the telemetry runtime summary of the
  binding's runtime (``ledger_cash`` and ``positions``), or from the pool's
  provisioned budget while the ledger has no fill yet.

When any input cannot be read the provider raises
``ExpressionInputsUnavailable`` naming the missing input, and the caller
records a ``candidates_unavailable`` NoTrade with detail
``budget_unavailable: <input>``. It never substitutes a default.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping

from services.capital.risk_policy import (
    RiskPolicy,
    RiskPolicyLoadError,
    resolve_bound_pool_risk_policy,
    risk_policy_digest,
)

log = logging.getLogger(__name__)

BUDGET_UNAVAILABLE = "budget_unavailable"
DEFAULT_CACHE_TTL_SECONDS = 30.0
DEFAULT_TIMEOUT_SECONDS = 10.0

JsonFetch = Callable[[str, Mapping[str, str], float], Any]


class ExpressionInputsUnavailable(RuntimeError):
    """A required expression input could not be read; the decision fails closed."""

    def __init__(self, missing: str, detail: str = "") -> None:
        self.missing = missing
        self.detail = detail
        super().__init__(f"{missing}: {detail}" if detail else missing)


class CapitalPoolReadError(RiskPolicyLoadError):
    """The capital service could not be read for a pool."""


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _http_get_json(url: str, headers: Mapping[str, str], timeout: float) -> Any:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class _TtlCache:
    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._items.get(key)
            if entry is None or self._clock() - entry[0] > self._ttl:
                return None
            return entry[1]

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._items[key] = (self._clock(), value)


class CapitalPoolReader:
    """``capital_pool_reader`` backed by ``GET /api/capital-pools/{pool_id}``.

    Returns the pool record (``budget``, ``currency``, ``metadata.risk_policy``
    ...) or ``None`` when the pool does not exist. A transport or auth failure
    raises ``CapitalPoolReadError`` so the caller fails closed instead of
    treating an unreachable service as "no policy".
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        tenant_id: str = "",
        service_name: str = "runtime-manager",
        fetch_json: JsonFetch | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token.strip()
        self._tenant_id = tenant_id.strip()
        self._service_name = service_name
        self._fetch_json = fetch_json or _http_get_json
        self._timeout = timeout_seconds
        self._cache = _TtlCache(cache_ttl_seconds)

    def _headers(self) -> dict[str, str]:
        token = self._token
        headers = {
            "Accept": "application/json",
            "Authorization": token if token.lower().startswith("bearer ") else f"Bearer {token}",
            "X-Pantheon-Service": self._service_name,
        }
        if self._tenant_id:
            headers["X-Tenant-Id"] = self._tenant_id
        return headers

    def __call__(self, pool_id: str) -> Mapping[str, Any] | None:
        clean = str(pool_id or "").strip()
        if not clean:
            return None
        cached = self._cache.get(clean)
        if cached is not None:
            return cached
        url = f"{self._base_url}/api/capital-pools/{urllib.parse.quote(clean, safe='')}"
        try:
            body = self._fetch_json(url, self._headers(), self._timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise CapitalPoolReadError(f"capital pool {clean!r} read failed: HTTP {exc.code}") from exc
        except Exception as exc:  # noqa: BLE001 - transport failure fails closed
            raise CapitalPoolReadError(
                f"capital pool {clean!r} read failed: {type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(body, Mapping):
            raise CapitalPoolReadError(f"capital pool {clean!r} read returned a non-object body")
        self._cache.put(clean, body)
        return body


class RuntimeSummaryReader:
    """Reads one telemetry runtime summary (the published ledger truth)."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        tenant_id: str = "",
        fetch_json: JsonFetch | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token.strip()
        self._tenant_id = tenant_id.strip()
        self._fetch_json = fetch_json or _http_get_json
        self._timeout = timeout_seconds

    def __call__(self, runtime_id: str) -> Mapping[str, Any]:
        clean = str(runtime_id or "").strip()
        if not clean:
            raise ExpressionInputsUnavailable("ledger", "binding has no runtime_id")
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._tenant_id:
            headers["X-Tenant-Id"] = self._tenant_id
        url = f"{self._base_url}/api/telemetry/runtime-summaries/{urllib.parse.quote(clean, safe='')}"
        try:
            body = self._fetch_json(url, headers, self._timeout)
        except urllib.error.HTTPError as exc:
            raise ExpressionInputsUnavailable(
                "ledger", f"runtime summary read failed: HTTP {exc.code}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ExpressionInputsUnavailable(
                "ledger", f"runtime summary read failed: {type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(body, Mapping):
            raise ExpressionInputsUnavailable("ledger", "runtime summary is not an object")
        return body


def capital_pool_reader_from_env(
    env: Mapping[str, str] | None = None,
    *,
    fetch_json: JsonFetch | None = None,
) -> CapitalPoolReader | None:
    """Build the capital reader from the environment, or ``None`` when unconfigured."""
    from services.service_token_file import configured_service_token

    source = os.environ if env is None else env
    base_url = (
        source.get("PANTHEON_CAPITAL_API_URL") or source.get("PANTHEON_CAPITAL_SERVICE_URL") or ""
    ).strip()
    try:
        token = configured_service_token(
            "RUNTIME_MANAGER_CAPITAL_SERVICE_TOKEN", source
        ) or configured_service_token("DEPLOYMENT_CAPITAL_SERVICE_TOKEN", source)
    except RuntimeError as exc:
        log.warning("capital reader credential unavailable: %s", exc)
        return None
    if not base_url or not token:
        return None
    return CapitalPoolReader(
        base_url=base_url,
        token=token,
        tenant_id=(source.get("PANTHEON_TENANT_ID") or "").strip(),
        fetch_json=fetch_json,
    )


def runtime_summary_reader_from_env(
    env: Mapping[str, str] | None = None,
    *,
    fetch_json: JsonFetch | None = None,
) -> RuntimeSummaryReader | None:
    source = os.environ if env is None else env
    base_url = (
        source.get("PANTHEON_TELEMETRY_API_URL") or source.get("PANTHEON_TELEMETRY_URL") or ""
    ).strip()
    if not base_url:
        return None
    return RuntimeSummaryReader(
        base_url=base_url,
        token=(source.get("PANTHEON_TELEMETRY_SERVICE_TOKEN") or "").strip(),
        tenant_id=(source.get("PANTHEON_TENANT_ID") or "").strip(),
        fetch_json=fetch_json,
    )


def _unit_price(row: Mapping[str, Any]) -> float | None:
    for key in ("mark_price", "last_known_mark_price"):
        price = _finite(row.get(key))
        if price is not None and price > 0:
            return price
    return None


def _reservations_from_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Extract pending order reservations and reserved cash/margin/exposure from a summary."""
    from services.execution.lean_runtime.instrument_candidates import normalize_underlying_code

    raw_list = summary.get("order_reservations")
    if raw_list is None:
        raw_list = summary.get("reservations")
    if isinstance(raw_list, Mapping):
        items: list[ Any ] = list(raw_list.values())
    elif isinstance(raw_list, list):
        items = raw_list
    else:
        items = []

    reservations: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    reserved_cash = 0.0
    reserved_margin = 0.0
    reserved_gross = 0.0
    reserved_signed_net = 0.0
    reserved_underlying: dict[str, float] = {}
    reserved_underlying_signed: dict[str, float] = {}

    for item in items:
        if not isinstance(item, Mapping):
            continue
        res_id = str(
            item.get("reservation_id")
            or item.get("signal_id")
            or item.get("client_order_id")
            or item.get("decision_id")
            or ""
        ).strip()
        if res_id and res_id in seen_ids:
            continue
        if res_id:
            seen_ids.add(res_id)
        is_reducing = bool(item.get("is_risk_reducing") or item.get("close_leg") or item.get("reason_code") == "close_leg")
        underlying = normalize_underlying_code(
            str(item.get("underlying") or item.get("symbol") or item.get("instrument_id") or "")
        )
        side = str(item.get("side") or item.get("action") or "").strip().lower()
        direction = str(item.get("direction") or ("SHORT" if side == "sell" else "LONG")).strip().upper()
        kind = str(item.get("kind") or ("stock_future" if str(item.get("instrument_id") or "").startswith("ssf:") else "cash_share"))
        qty = abs(_finite(item.get("quantity") if item.get("quantity") is not None else item.get("units")) or 0.0)
        mult = _finite(item.get("multiplier") if item.get("multiplier") is not None else item.get("unit")) or 1.0
        price = _finite(item.get("price") if item.get("price") is not None else item.get("mark_price")) or 0.0
        notional = _finite(item.get("notional") if item.get("notional") is not None else item.get("gross_notional"))
        if notional is None:
            notional = qty * mult * price
        notional = max(0.0, float(notional))
        cash_req = _finite(item.get("cash_reserved") if item.get("cash_reserved") is not None else item.get("cash_required"))
        if cash_req is None:
            if not is_reducing and kind == "cash_share" and direction == "LONG" and qty > 0 and price > 0:
                from services.execution.lean_runtime.taiwan_cash_costs import buy_cash_required
                cash_req = buy_cash_required(underlying or "0000", int(round(qty)), price)
            else:
                cash_req = 0.0
        margin_req = _finite(item.get("margin_reserved") if item.get("margin_reserved") is not None else item.get("margin_required")) or 0.0
        signed_notional = -notional if direction == "SHORT" or side == "sell" else notional
        if not is_reducing:
            reserved_cash += max(0.0, float(cash_req))
            reserved_margin += max(0.0, float(margin_req))
            reserved_gross += notional
            reserved_signed_net += signed_notional
            if underlying:
                reserved_underlying[underlying] = round(reserved_underlying.get(underlying, 0.0) + notional, 2)
                reserved_underlying_signed[underlying] = round(
                    reserved_underlying_signed.get(underlying, 0.0) + signed_notional, 2
                )
        reservations.append(
            {
                "reservation_id": res_id or f"res-{len(reservations) + 1}",
                "underlying": underlying,
                "instrument_id": str(item.get("instrument_id") or (f"eq:{underlying}" if underlying else "")),
                "kind": kind,
                "direction": direction,
                "side": side or ("sell" if direction == "SHORT" else "buy"),
                "units": qty,
                "multiplier": mult,
                "price": price,
                "notional": round(notional, 2),
                "cash_reserved": round(max(0.0, float(cash_req)), 2),
                "margin_reserved": round(max(0.0, float(margin_req)), 2),
                "is_risk_reducing": is_reducing,
            }
        )

    scalar_cash = _finite(summary.get("cash_reserved") if summary.get("cash_reserved") is not None else summary.get("reserved_cash"))
    if scalar_cash is not None and scalar_cash > reserved_cash:
        reserved_cash = scalar_cash
    scalar_margin = _finite(summary.get("order_margin_reserved") if summary.get("order_margin_reserved") is not None else summary.get("reserved_margin"))
    if scalar_margin is not None and scalar_margin > reserved_margin:
        reserved_margin = scalar_margin
    scalar_gross = _finite(summary.get("gross_reserved") if summary.get("gross_reserved") is not None else summary.get("reserved_gross_notional"))
    if scalar_gross is not None and scalar_gross > reserved_gross:
        reserved_gross = scalar_gross

    return {
        "reservations": reservations,
        "reserved_cash": round(reserved_cash, 2),
        "reserved_margin": round(reserved_margin, 2),
        "reserved_gross_notional": round(reserved_gross, 2),
        "reserved_signed_net_notional": round(reserved_signed_net, 2),
        "reserved_underlying_notional": reserved_underlying,
        "reserved_underlying_signed_notional": reserved_underlying_signed,
    }


def _book_from_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Positions, gross, stock value, futures P&L and reserved margin from a summary."""
    from services.execution.lean_runtime.instrument_candidates import normalize_underlying_code

    if summary.get("positions_status") == "unavailable":
        raise ExpressionInputsUnavailable(
            "ledger", str(summary.get("positions_unavailable_reason") or "positions unavailable")
        )
    rows = summary.get("positions") or []
    if not isinstance(rows, list):
        raise ExpressionInputsUnavailable("ledger", "runtime summary positions is not a list")
    positions: list[dict[str, Any]] = []
    gross = signed_net = stock_value = futures_unrealized = margin_reserved = 0.0
    underlying_gross: dict[str, float] = {}
    underlying_signed: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ExpressionInputsUnavailable("ledger", "runtime summary position is not an object")
        quantity = _finite(row.get("quantity"))
        if quantity is None:
            raise ExpressionInputsUnavailable("ledger", "position quantity unreadable")
        if quantity == 0:
            continue
        underlying = normalize_underlying_code(str(row.get("symbol") or row.get("underlying") or ""))
        multiplier = _finite(row.get("multiplier")) or 1.0
        is_future = row.get("kind") == "stock_future"
        price = _unit_price(row)
        if price is None:
            raise ExpressionInputsUnavailable(
                "ledger", f"position {row.get('instrument_id') or underlying} has no mark price"
            )
        notional = abs(quantity) * multiplier * price
        signed_notional = quantity * multiplier * price
        gross += notional
        signed_net += signed_notional
        if underlying:
            underlying_gross[underlying] = round(underlying_gross.get(underlying, 0.0) + notional, 2)
            underlying_signed[underlying] = round(underlying_signed.get(underlying, 0.0) + signed_notional, 2)
        if is_future:
            unrealized = _finite(row.get("unrealized_pnl"))
            average = _finite(row.get("average_price"))
            if unrealized is None and average is not None:
                unrealized = quantity * multiplier * (price - average)
            if unrealized is None:
                raise ExpressionInputsUnavailable(
                    "ledger", f"future {row.get('instrument_id')} has no cost basis"
                )
            futures_unrealized += unrealized
            margin_reserved += _finite(row.get("margin_reserved")) or 0.0
        else:
            stock_value += quantity * multiplier * price
        positions.append(
            {
                "underlying": underlying,
                "instrument_id": str(row.get("instrument_id") or f"eq:{underlying}"),
                "kind": row.get("kind") or "cash_share",
                "direction": "LONG" if quantity > 0 else "SHORT",
                "units": int(abs(quantity)),
                "multiplier": multiplier,
                "notional": round(notional, 2),
            }
        )
    res_info = _reservations_from_summary(summary)
    combined_underlying_gross = dict(underlying_gross)
    for u_code, u_res in res_info["reserved_underlying_notional"].items():
        combined_underlying_gross[u_code] = round(combined_underlying_gross.get(u_code, 0.0) + u_res, 2)
    combined_underlying_signed = dict(underlying_signed)
    for u_code, u_res_s in res_info["reserved_underlying_signed_notional"].items():
        combined_underlying_signed[u_code] = round(combined_underlying_signed.get(u_code, 0.0) + u_res_s, 2)
    return {
        "positions": positions,
        "gross_notional": round(gross, 2),
        "signed_net_notional": round(signed_net, 2),
        "stock_value": stock_value,
        "futures_unrealized": futures_unrealized,
        "margin_reserved": margin_reserved,
        "underlying_gross_by_code": combined_underlying_gross,
        "underlying_signed_by_code": combined_underlying_signed,
        **res_info,
    }


def _ledger_has_fills(summary: Mapping[str, Any]) -> bool:
    for key in ("ledger_fill_count", "executed_trade_count"):
        count = _finite(summary.get(key))
        if count is not None and count > 0:
            return True
    return False


def build_expression_inputs(
    *,
    pool: Mapping[str, Any],
    policy: RiskPolicy,
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Pure assembly of ``{"persona_book", "risk_budget"}`` from the three truths."""
    if policy.gross_limit is None or float(policy.gross_limit) <= 0:
        raise ExpressionInputsUnavailable("risk_policy", "gross_limit is not set in the pool policy")
    for pos_field in ("net_limit", "max_leverage", "max_single_name_weight"):
        val = getattr(policy, pos_field, None)
        if val is not None and (_finite(val) is None or float(val) <= 0):
            raise ExpressionInputsUnavailable("risk_policy", f"{pos_field} must be a positive finite number")
    min_reserve_pct = getattr(policy, "min_cash_reserve_pct", None)
    if min_reserve_pct is not None and (_finite(min_reserve_pct) is None or float(min_reserve_pct) < 0 or float(min_reserve_pct) >= 1.0):
        raise ExpressionInputsUnavailable("risk_policy", "min_cash_reserve_pct must be in [0, 1)")
    min_reserve_abs = getattr(policy, "min_cash_reserve", None)
    if min_reserve_abs is not None and (_finite(min_reserve_abs) is None or float(min_reserve_abs) < 0):
        raise ExpressionInputsUnavailable("risk_policy", "min_cash_reserve must be non-negative")

    pool_budget = _finite(pool.get("budget"))
    if not _ledger_has_fills(summary) and (pool_budget is None or pool_budget <= 0):
        raise ExpressionInputsUnavailable("pool_budget", "capital pool has no positive budget")
    book = _book_from_summary(summary)
    if _ledger_has_fills(summary):
        cash = _finite(summary.get("ledger_cash"))
        if cash is None:
            raise ExpressionInputsUnavailable(
                "ledger", "runtime summary has fills but no ledger_cash"
            )
    else:
        cash = pool_budget
    assert cash is not None
    equity = cash + book["stock_value"] + book["futures_unrealized"]
    if equity <= 0:
        raise ExpressionInputsUnavailable("ledger", f"non-positive equity {equity:.2f}")

    total_margin_reserved = book["margin_reserved"] + book["reserved_margin"]
    cash_reserved = book["reserved_cash"]
    free_margin = equity - total_margin_reserved
    pct_reserve_req = float(min_reserve_pct) * equity if min_reserve_pct is not None else 0.0
    abs_reserve_req = float(min_reserve_abs) if min_reserve_abs is not None else 0.0
    cash_reserve_required = max(pct_reserve_req, abs_reserve_req, 0.0)
    raw_buying_power = max(min(cash - cash_reserved, free_margin), 0.0)
    available_cash = round(max(raw_buying_power - cash_reserve_required, 0.0), 2)
    gross_used = round(book["gross_notional"] + book["reserved_gross_notional"], 2)
    signed_net_used = round(book["signed_net_notional"] + book["reserved_signed_net_notional"], 2)

    risk_budget: dict[str, Any] = {
        "equity": round(equity, 2),
        "raw_cash": round(cash, 2),
        "free_margin": round(free_margin, 2),
        "available_cash": available_cash,
        "available_buying_power": available_cash,
        "gross_limit": round(float(policy.gross_limit) * equity, 2),
        "gross_limit_multiple": float(policy.gross_limit),
        "gross_used": gross_used,
        "net_limit": round(float(policy.net_limit) * equity, 2) if policy.net_limit is not None else None,
        "net_limit_multiple": float(policy.net_limit) if policy.net_limit is not None else None,
        "net_used": round(abs(signed_net_used), 2),
        "signed_net_used": signed_net_used,
        "max_leverage": policy.max_leverage,
        "max_single_name_weight": policy.max_single_name_weight,
        "max_single_name_notional": (
            round(float(policy.max_single_name_weight) * equity, 2)
            if policy.max_single_name_weight is not None
            else None
        ),
        "min_cash_reserve_pct": min_reserve_pct,
        "min_cash_reserve": min_reserve_abs,
        "cash_reserve_required": round(cash_reserve_required, 2),
        "margin_reserved": round(total_margin_reserved, 2),
        "order_margin_reserved": round(book["reserved_margin"], 2),
        "cash_reserved": round(cash_reserved, 2),
        "underlying_exposures": book["underlying_gross_by_code"],
        "underlying_signed_exposures": book["underlying_signed_by_code"],
        "reserved_underlying_notional": book["reserved_underlying_notional"],
        "order_reservations": book["reservations"],
        "risk_policy_id": policy.risk_policy_id,
        "risk_policy_version": str(policy.version),
        "risk_policy_digest": risk_policy_digest(policy),
    }
    persona_book = {
        "positions": book["positions"],
        "gross_notional": book["gross_notional"],
    }
    return {"persona_book": persona_book, "risk_budget": risk_budget}


class ExpressionInputsProvider:
    """``expression_inputs(binding) -> {"persona_book", "risk_budget"}`` for the producer."""

    def __init__(
        self,
        *,
        capital_pool_reader: Callable[[str], Any] | None,
        runtime_summary_reader: Callable[[str], Mapping[str, Any]] | None,
    ) -> None:
        self._capital_pool_reader = capital_pool_reader
        self._runtime_summary_reader = runtime_summary_reader

    @property
    def capital_reader_configured(self) -> bool:
        return self._capital_pool_reader is not None

    @property
    def ledger_reader_configured(self) -> bool:
        return self._runtime_summary_reader is not None

    def __call__(self, binding: Mapping[str, Any]) -> dict[str, Any]:
        pool_id = str(binding.get("capital_pool_id") or "").strip()
        if not pool_id:
            raise ExpressionInputsUnavailable("capital_pool", "binding has no capital_pool_id")
        if self._capital_pool_reader is None:
            raise ExpressionInputsUnavailable("capital_pool", "capital service is not configured")
        try:
            pool = self._capital_pool_reader(pool_id)
        except Exception as exc:  # noqa: BLE001
            raise ExpressionInputsUnavailable("capital_pool", str(exc)) from exc
        if not isinstance(pool, Mapping):
            raise ExpressionInputsUnavailable("capital_pool", f"pool {pool_id} not found")
        try:
            policy = resolve_bound_pool_risk_policy(
                capital_pool_id=pool_id,
                binding=binding,
                capital_pool_reader=lambda _pool_id: pool,
            )
        except Exception as exc:  # noqa: BLE001
            raise ExpressionInputsUnavailable("risk_policy", str(exc)) from exc
        if policy is None or str(policy.status).lower() != "active":
            raise ExpressionInputsUnavailable(
                "risk_policy", "no active risk policy resolved for the pool"
            )
        if self._runtime_summary_reader is None:
            raise ExpressionInputsUnavailable("ledger", "telemetry service is not configured")
        runtime_id = str(binding.get("runtime_id") or "").strip()
        summary = self._runtime_summary_reader(runtime_id)
        return build_expression_inputs(pool=pool, policy=policy, summary=summary)


def build_production_expression_inputs(
    env: Mapping[str, str] | None = None,
) -> ExpressionInputsProvider:
    """The provider ``_runner_strategy`` installs; unreadable sources fail closed at call time."""
    return ExpressionInputsProvider(
        capital_pool_reader=capital_pool_reader_from_env(env),
        runtime_summary_reader=runtime_summary_reader_from_env(env),
    )
