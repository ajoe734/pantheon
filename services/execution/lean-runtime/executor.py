"""
Signal Executor
Translates a validated signal dict (schema v1, locked) into LEAN order calls.

Called from signal_consumer.py inside a LEAN algorithm's OnData() or a
scheduled event. This module must NOT import QuantConnect directly so it
can be unit-tested outside the LEAN runtime.  The `algo` parameter passed
to execute() is the live LEAN QCAlgorithm instance (duck-typed).

Action × Direction × quantity_type dispatch table
──────────────────────────────────────────────────
action   direction  quantity_type      LEAN call
───────  ─────────  ───────────────    ──────────────────────────────────────────────
BUY      LONG       PERCENT_PORTFOLIO  algo.SetHoldings(sym, +pct)
BUY      LONG       SHARES             algo.MarketOrder(sym, +int(qty))
BUY      LONG       CASH_VALUE         algo.MarketOrder(sym, +int(qty/price))
SELL     SHORT      PERCENT_PORTFOLIO  algo.SetHoldings(sym, -pct)
SELL     SHORT      SHARES             algo.MarketOrder(sym, -int(qty))
SELL     SHORT      CASH_VALUE         algo.MarketOrder(sym, -int(qty/price))
SELL     LONG       PERCENT_PORTFOLIO  algo.SetHoldings(sym,  0)   # close long
SELL     LONG       SHARES             algo.Liquidate(sym)          # close long
SELL     LONG       CASH_VALUE         algo.Liquidate(sym)          # close long
EXIT     LONG       *                  algo.Liquidate(sym) on long leg only
EXIT     SHORT      *                  algo.Liquidate(sym) on short leg only
HOLD     *          *                  no-op (logged only)
"""
from __future__ import annotations

import logging
from typing import Any

from .symbol_parser import ParsedSymbol, SymbolParseError, parse as parse_symbol

log = logging.getLogger(__name__)

# Maximum confidence-scaled position floor (avoid sub-penny orders)
_CONFIDENCE_FLOOR = 0.5


class ExecutionError(RuntimeError):
    pass


def execute(signal: dict[str, Any], algo: Any) -> None:
    """
    Translate one validated signal into LEAN order call(s).

    Parameters
    ----------
    signal : dict
        Fully validated signal payload matching schema.json v1.
    algo : QCAlgorithm
        Live LEAN algorithm instance.  Duck-typed to allow unit testing
        with a mock.

    Raises
    ------
    ExecutionError
        For unrecoverable signal errors that should be logged and skipped.
    """
    signal_id = signal.get("signal_id", "<unknown>")
    action = signal["action"]           # BUY | SELL | HOLD | EXIT
    direction = signal["direction"]     # LONG | SHORT
    quantity = float(signal["quantity"])
    quantity_type = signal["quantity_type"]  # SHARES | PERCENT_PORTFOLIO | CASH_VALUE
    order_type = signal.get("order_type", "MARKET")
    limit_price = signal.get("limit_price")
    confidence = float(
        (signal.get("metadata") or {}).get("confidence_score", 1.0)
    )

    # --- HOLD: informational only, no execution ---
    if action == "HOLD":
        log.info("[%s] HOLD signal — no order placed (symbol=%s)", signal_id, signal["symbol"])
        return

    # --- Parse symbol ---
    try:
        parsed: ParsedSymbol = parse_symbol(signal["symbol"])
    except SymbolParseError as exc:
        raise ExecutionError(f"[{signal_id}] {exc}") from exc

    # Build LEAN Symbol object by calling algo helper
    lean_symbol = _resolve_symbol(algo, parsed)

    # --- Confidence floor: avoid near-zero orders ---
    effective_confidence = max(confidence, _CONFIDENCE_FLOOR)
    # Only scale PERCENT_PORTFOLIO; shares/cash are absolute intents
    if quantity_type == "PERCENT_PORTFOLIO":
        quantity = quantity * effective_confidence
        if confidence < _CONFIDENCE_FLOOR:
            log.warning(
                "[%s] confidence %.2f below floor; scaled to %.2f × %.4f",
                signal_id, confidence, _CONFIDENCE_FLOOR, quantity,
            )

    # --- Dispatch ---
    if action == "BUY" and direction == "LONG":
        _place_order(algo, lean_symbol, quantity, quantity_type,
                     order_type, limit_price, sign=+1, signal_id=signal_id)

    elif action == "SELL" and direction == "SHORT":
        _place_order(algo, lean_symbol, quantity, quantity_type,
                     order_type, limit_price, sign=-1, signal_id=signal_id)

    elif action == "SELL" and direction == "LONG":
        # Close existing long position
        log.info("[%s] SELL+LONG → Liquidate long on %s", signal_id, parsed.raw)
        algo.Liquidate(lean_symbol)

    elif action == "EXIT" and direction == "LONG":
        # Close long leg only (leave short untouched if any)
        log.info("[%s] EXIT+LONG → Liquidate long on %s", signal_id, parsed.raw)
        algo.Liquidate(lean_symbol)

    elif action == "EXIT" and direction == "SHORT":
        # Close short leg: set holdings to 0 if short, else no-op
        log.info("[%s] EXIT+SHORT → close short on %s", signal_id, parsed.raw)
        holdings = _get_holdings_quantity(algo, lean_symbol)
        if holdings < 0:
            algo.MarketOrder(lean_symbol, abs(int(holdings)))
        else:
            log.warning(
                "[%s] EXIT+SHORT but no short position found on %s — no-op",
                signal_id, parsed.raw,
            )
    else:
        raise ExecutionError(
            f"[{signal_id}] Unhandled action/direction combination: "
            f"action={action} direction={direction}"
        )

    # --- Risk: stop-loss / take-profit bracket ---
    risk = (signal.get("metadata") or {}).get("risk_parameters") or {}
    if risk.get("stop_loss_pct") or risk.get("take_profit_pct"):
        log.info(
            "[%s] Risk parameters present (stop=%.2f%%, tp=%.2f%%) — "
            "bracket order not yet implemented; log only",
            signal_id,
            risk.get("stop_loss_pct", 0) * 100,
            risk.get("take_profit_pct", 0) * 100,
        )
        # TODO (P3-001 follow-up): implement StopMarketOrder + LimitOrder bracket
        # after verifying broker support via algo.BrokerageModel


def _place_order(
    algo: Any,
    lean_symbol: Any,
    quantity: float,
    quantity_type: str,
    order_type: str,
    limit_price: float | None,
    sign: int,
    signal_id: str,
) -> None:
    """
    Place a directional order.  sign=+1 for long, -1 for short.
    Logs lossy float→int conversion for SHARES and CASH_VALUE.
    """
    if quantity_type == "PERCENT_PORTFOLIO":
        pct = sign * quantity
        log.info("[%s] SetHoldings %s → %.4f", signal_id, lean_symbol, pct)
        algo.SetHoldings(lean_symbol, pct)

    elif quantity_type == "SHARES":
        shares = sign * int(round(quantity))
        if shares == 0:
            log.warning(
                "[%s] SHARES quantity %.4f rounded to 0 — order not placed",
                signal_id, quantity,
            )
            return
        if abs(round(quantity) - quantity) > 0.01:
            log.info(
                "[%s] SHARES lossy conversion: %.4f → %d (audit)",
                signal_id, quantity, shares,
            )
        if order_type == "LIMIT" and limit_price is not None:
            log.info("[%s] LimitOrder %s %d @ %.4f", signal_id, lean_symbol, shares, limit_price)
            algo.LimitOrder(lean_symbol, shares, limit_price)
        else:
            log.info("[%s] MarketOrder %s %d", signal_id, lean_symbol, shares)
            algo.MarketOrder(lean_symbol, shares)

    elif quantity_type == "CASH_VALUE":
        price = _get_price(algo, lean_symbol)
        if price <= 0:
            raise ExecutionError(
                f"[{signal_id}] CASH_VALUE order failed: cannot get price for {lean_symbol}"
            )
        shares = sign * int(round(quantity / price))
        if shares == 0:
            log.warning(
                "[%s] CASH_VALUE %.2f / price %.4f = 0 shares — order not placed",
                signal_id, quantity, price,
            )
            return
        log.info(
            "[%s] CASH_VALUE %.2f → %d shares @ ~%.4f (audit)",
            signal_id, quantity, shares, price,
        )
        algo.MarketOrder(lean_symbol, shares)

    else:
        raise ExecutionError(f"[{signal_id}] Unknown quantity_type: {quantity_type}")


# ---------------------------------------------------------------------------
# LEAN helpers (duck-typed for testability)
# ---------------------------------------------------------------------------

def _resolve_symbol(algo: Any, parsed: ParsedSymbol) -> Any:
    """
    Resolve a ParsedSymbol to a LEAN Symbol object via Symbol.Create().

    In a live LEAN runtime, imports QuantConnect and calls:
        Symbol.Create(ticker, SecurityType.<X>, Market.<Y>)
    using the string constants from ParsedSymbol.lean_security_type /
    lean_market (e.g. "SecurityType.Equity", "Market.USA").

    In unit tests (QuantConnect not installed), returns the ticker string
    as a fallback so the dispatch table can still be exercised.

    Raises ExecutionError if QuantConnect is present but the enum attribute
    cannot be resolved — this means _MARKET_MAP in symbol_parser.py has
    a value that does not match a real QuantConnect enum member.
    """
    try:
        from QuantConnect import Symbol, SecurityType, Market  # type: ignore[import]
        sec_type = _resolve_lean_enum(SecurityType, parsed.lean_security_type, "SecurityType")
        market = _resolve_lean_enum(Market, parsed.lean_market, "Market")
        return Symbol.Create(parsed.ticker, sec_type, market)
    except ImportError:
        # QuantConnect not installed — unit-test / dev fallback
        return parsed.ticker


def _resolve_lean_enum(enum_class: Any, dotted_name: str, prefix: str) -> Any:
    """
    Convert a dotted string such as "Market.USA" or "SecurityType.Equity"
    into the actual QuantConnect enum value.

    Strips the class prefix if present ("Market.USA" → attr "USA"),
    then returns enum_class.USA.  Raises ExecutionError on unknown attr.
    """
    attr = dotted_name.split(".", 1)[1] if "." in dotted_name else dotted_name
    try:
        return getattr(enum_class, attr)
    except AttributeError as exc:
        raise ExecutionError(
            f"Cannot resolve {prefix}.{attr} from '{dotted_name}' — "
            "check symbol_parser._MARKET_MAP for unsupported market codes"
        ) from exc


def _get_holdings_quantity(algo: Any, lean_symbol: Any) -> float:
    try:
        return algo.Portfolio[lean_symbol].Quantity
    except Exception:
        return 0.0


def _get_price(algo: Any, lean_symbol: Any) -> float:
    try:
        return float(algo.Securities[lean_symbol].Price)
    except Exception:
        return 0.0
