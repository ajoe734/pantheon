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
    gross = stock_value = futures_unrealized = margin_reserved = 0.0
    for row in rows:
        if not isinstance(row, Mapping):
            raise ExpressionInputsUnavailable("ledger", "runtime summary position is not an object")
        quantity = _finite(row.get("quantity"))
        if quantity is None:
            raise ExpressionInputsUnavailable("ledger", "position quantity unreadable")
        if quantity == 0:
            continue
        underlying = normalize_underlying_code(str(row.get("symbol") or ""))
        multiplier = _finite(row.get("multiplier")) or 1.0
        is_future = row.get("kind") == "stock_future"
        price = _unit_price(row)
        if price is None:
            raise ExpressionInputsUnavailable(
                "ledger", f"position {row.get('instrument_id') or underlying} has no mark price"
            )
        notional = abs(quantity) * multiplier * price
        gross += notional
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
    return {
        "positions": positions,
        "gross_notional": round(gross, 2),
        "stock_value": stock_value,
        "futures_unrealized": futures_unrealized,
        "margin_reserved": margin_reserved,
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
    if policy.gross_limit is None:
        raise ExpressionInputsUnavailable("risk_policy", "gross_limit is not set in the pool policy")
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
    equity = cash + book["stock_value"] + book["futures_unrealized"]
    if equity <= 0:
        raise ExpressionInputsUnavailable("ledger", f"non-positive equity {equity:.2f}")
    free_margin = equity - book["margin_reserved"]
    risk_budget = {
        "equity": round(equity, 2),
        "available_cash": round(max(min(cash, free_margin), 0.0), 2),
        "gross_limit": round(float(policy.gross_limit) * equity, 2),
        "gross_used": book["gross_notional"],
        "max_leverage": policy.max_leverage,
        "margin_reserved": round(book["margin_reserved"], 2),
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
