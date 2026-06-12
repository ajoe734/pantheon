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
EXIT     LONG       *                  algo.Liquidate(sym) if long, else no-op
EXIT     SHORT      *                  algo.MarketOrder(round abs holdings) if short, else no-op
HOLD     *          *                  paper_order_simulated no-op telemetry
"""
from __future__ import annotations

import logging
import math
from typing import Any

from .symbol_parser import ParsedSymbol, SymbolParseError, parse as parse_symbol

log = logging.getLogger(__name__)

# Maximum confidence-scaled position floor (avoid sub-penny orders)
_CONFIDENCE_FLOOR = 0.5
BRACKET_ORDER_STATUS_LOGGED_ONLY = "logged_only"
BRACKET_ORDER_STATUS_SUBMITTED_TO_BROKER = "submitted_to_broker"
_BRACKET_EXECUTION_STAGES = {"paper", "sim", "simulation"}
_BRACKET_ENTRY_COMBINATIONS = {("BUY", "LONG"), ("SELL", "SHORT")}


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
    signal_context = _signal_context_metadata(signal)
    action = signal["action"]           # BUY | SELL | HOLD | EXIT
    direction = signal["direction"]     # LONG | SHORT
    quantity = float(signal["quantity"])
    quantity_type = signal["quantity_type"]  # SHARES | PERCENT_PORTFOLIO | CASH_VALUE
    order_type = signal.get("order_type", "MARKET")
    limit_price = signal.get("limit_price")
    confidence = float(
        (signal.get("metadata") or {}).get("confidence_score", 1.0)
    )

    # --- Parse symbol ---
    try:
        parsed: ParsedSymbol = parse_symbol(signal["symbol"])
    except SymbolParseError as exc:
        raise ExecutionError(f"[{signal_id}] {exc}") from exc

    # Build LEAN Symbol object by calling algo helper
    lean_symbol = _resolve_symbol(algo, parsed)
    _seed_signal_market_price(algo, lean_symbol, signal)
    holdings_before = _get_holdings_quantity(algo, lean_symbol)

    # --- HOLD: informational decision, no broker submission ---
    if action == "HOLD":
        _set_signal_context(algo, signal_context)
        try:
            _record_signal_noop(
                algo,
                lean_symbol,
                signal_id,
                noop_reason="hold_signal",
                requested_quantity=quantity,
                quantity_type=quantity_type,
                order_type=order_type,
                price=_get_price(algo, lean_symbol),
            )
        finally:
            _clear_signal_context(algo)
        log.info("[%s] HOLD signal — no order placed (symbol=%s)", signal_id, signal["symbol"])
        return

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

    _set_signal_context(algo, signal_context)
    try:
        # --- Dispatch ---
        if action == "BUY" and direction == "LONG":
            _place_order(algo, lean_symbol, quantity, quantity_type,
                         order_type, limit_price, sign=+1, signal_id=signal_id)

        elif action == "SELL" and direction == "SHORT":
            _place_order(algo, lean_symbol, quantity, quantity_type,
                         order_type, limit_price, sign=-1, signal_id=signal_id)

        elif action == "SELL" and direction == "LONG":
            # Close existing long position
            if quantity_type == "PERCENT_PORTFOLIO":
                # SetHoldings to 0 for portfolio-relative closes
                log.info("[%s] SELL+LONG → SetHoldings %s → 0", signal_id, parsed.raw)
                algo.SetHoldings(lean_symbol, 0)
            else:
                # Liquidate for absolute quantity closes (more robust)
                log.info("[%s] SELL+LONG → Liquidate long on %s", signal_id, parsed.raw)
                algo.Liquidate(lean_symbol)

        elif action == "EXIT" and direction == "LONG":
            # Close long leg only: check actual position direction
            holdings = _get_holdings_quantity(algo, lean_symbol)
            if holdings > 0:
                # Only close if actually long
                log.info("[%s] EXIT+LONG → Liquidate long on %s", signal_id, parsed.raw)
                algo.Liquidate(lean_symbol)
            else:
                log.warning(
                    "[%s] EXIT+LONG but no long position on %s (holdings=%.1f) — no-op",
                    signal_id, parsed.raw, holdings,
                )
                _record_signal_noop(
                    algo,
                    lean_symbol,
                    signal_id,
                    noop_reason="exit_long_without_position",
                    requested_quantity=quantity,
                    computed_quantity=0.0,
                    quantity_type=quantity_type,
                    order_type=order_type,
                    price=_get_price(algo, lean_symbol),
                    metadata={"position_quantity": holdings, "exit_direction": "LONG"},
                )

        elif action == "EXIT" and direction == "SHORT":
            # Close short leg: set holdings to 0 if short, else no-op
            log.info("[%s] EXIT+SHORT → close short on %s", signal_id, parsed.raw)
            holdings = _get_holdings_quantity(algo, lean_symbol)
            if holdings < 0:
                # Use ceil to ensure we fully cover fractional short positions (e.g., crypto)
                shares_to_buy = math.ceil(abs(holdings))
                algo.MarketOrder(lean_symbol, shares_to_buy)
            else:
                log.warning(
                    "[%s] EXIT+SHORT but no short position found on %s (holdings=%.1f) — no-op",
                    signal_id, parsed.raw, holdings,
                )
                _record_signal_noop(
                    algo,
                    lean_symbol,
                    signal_id,
                    noop_reason="exit_short_without_position",
                    requested_quantity=quantity,
                    computed_quantity=0.0,
                    quantity_type=quantity_type,
                    order_type=order_type,
                    price=_get_price(algo, lean_symbol),
                    metadata={"position_quantity": holdings, "exit_direction": "SHORT"},
                )
        else:
            raise ExecutionError(
                f"[{signal_id}] Unhandled action/direction combination: "
                f"action={action} direction={direction}"
            )

        # --- Risk: stop-loss / take-profit bracket ---
        risk = (signal.get("metadata") or {}).get("risk_parameters") or {}
        if risk.get("stop_loss_pct") or risk.get("take_profit_pct"):
            _handle_bracket_order(
                algo=algo,
                lean_symbol=lean_symbol,
                signal_id=signal_id,
                risk=risk,
                action=action,
                direction=direction,
                holdings_before=holdings_before,
            )
    finally:
        _clear_signal_context(algo)


def _signal_context_metadata(signal: dict[str, Any]) -> dict[str, Any]:
    metadata = signal.get("metadata") if isinstance(signal.get("metadata"), dict) else {}
    context: dict[str, Any] = {}
    for key in ("signal_id", "strategy_id", "source_worker", "run_id", "binding_id"):
        value = signal.get(key)
        if value not in (None, ""):
            context[key] = value
    for key in ("quantity_type", "order_type"):
        value = signal.get(key)
        if value not in (None, ""):
            context[key] = value
    for key in (
        "alpha_source",
        "confidence_score",
        "model_id",
        "prompt_bundle_id",
        "llm_prompt_id",
        "llm_response_id",
        "llm_decision_id",
        "research_note_ref",
        "llm_note_ref",
        "market_data_ref",
        "research_data_ref",
        "news_data_ref",
        "normalized_data_ref",
        "source_dataset_ref",
        "source_evidence_refs",
        "ingest_run_id",
    ):
        value = metadata.get(key)
        if value not in (None, ""):
            context[key] = value
    market_price = _signal_market_price(signal)
    if market_price is not None:
        context["market_price"] = market_price
    if "quantity" in signal:
        try:
            context["requested_quantity"] = float(signal["quantity"])
        except (TypeError, ValueError):
            pass
    return context


def _set_signal_context(algo: Any, metadata: dict[str, Any]) -> None:
    setter = getattr(algo, "SetCurrentSignalContext", None)
    if callable(setter):
        setter(metadata)


def _clear_signal_context(algo: Any) -> None:
    clearer = getattr(algo, "ClearCurrentSignalContext", None)
    if callable(clearer):
        clearer()


def _seed_signal_market_price(algo: Any, lean_symbol: Any, signal: dict[str, Any]) -> None:
    price = _signal_market_price(signal)
    if price is None:
        return
    setter = getattr(algo, "SetSecurityPrice", None)
    if callable(setter):
        setter(lean_symbol, price)


def _signal_market_price(signal: dict[str, Any]) -> float | None:
    metadata = signal.get("metadata") if isinstance(signal.get("metadata"), dict) else {}
    market_data = metadata.get("market_data") if isinstance(metadata.get("market_data"), dict) else {}
    for source in (metadata, market_data):
        for key in ("last_price", "market_price", "close", "price"):
            value = source.get(key)
            if value in (None, ""):
                continue
            try:
                price = float(value)
            except (TypeError, ValueError):
                continue
            if price > 0:
                return price
    return None


def _handle_bracket_order(
    *,
    algo: Any,
    lean_symbol: Any,
    signal_id: str,
    risk: dict[str, Any],
    action: str,
    direction: str,
    holdings_before: float,
) -> None:
    stop_loss_pct = float(risk.get("stop_loss_pct") or 0)
    take_profit_pct = float(risk.get("take_profit_pct") or 0)
    guard = _bracket_execution_guard(algo)
    common_metadata = {
        "guard_stage": guard["stage"],
        "guard_reason": guard["reason"],
    }

    if not guard["allowed"]:
        log.info(
            "[%s] Bracket risk parameters present but guard blocked execution; "
            "status=%s reason=%s",
            signal_id,
            BRACKET_ORDER_STATUS_LOGGED_ONLY,
            guard["reason"],
        )
        _record_bracket_order_logged(
            algo,
            lean_symbol,
            signal_id,
            risk,
            broker_submission_status=BRACKET_ORDER_STATUS_LOGGED_ONLY,
            submitted_to_broker=False,
            metadata=common_metadata,
        )
        return

    if (action, direction) not in _BRACKET_ENTRY_COMBINATIONS:
        log.info(
            "[%s] Bracket risk parameters ignored for non-entry signal action=%s direction=%s; "
            "status=%s",
            signal_id,
            action,
            direction,
            BRACKET_ORDER_STATUS_LOGGED_ONLY,
        )
        _record_bracket_order_logged(
            algo,
            lean_symbol,
            signal_id,
            risk,
            broker_submission_status=BRACKET_ORDER_STATUS_LOGGED_ONLY,
            submitted_to_broker=False,
            metadata={**common_metadata, "reason": "not_entry_signal"},
        )
        return

    entry_quantity = _entry_fill_quantity(
        algo=algo,
        lean_symbol=lean_symbol,
        holdings_before=holdings_before,
        action=action,
        direction=direction,
    )
    entry_price = _get_price(algo, lean_symbol)
    legs = _build_bracket_legs(
        action=action,
        direction=direction,
        entry_quantity=entry_quantity,
        entry_price=entry_price,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
    )
    if not legs:
        log.warning(
            "[%s] Bracket guard passed but no valid child legs could be built; "
            "status=%s",
            signal_id,
            BRACKET_ORDER_STATUS_LOGGED_ONLY,
        )
        _record_bracket_order_logged(
            algo,
            lean_symbol,
            signal_id,
            risk,
            broker_submission_status=BRACKET_ORDER_STATUS_LOGGED_ONLY,
            submitted_to_broker=False,
            metadata={
                **common_metadata,
                "reason": "invalid_bracket_quantity_or_price",
                "entry_quantity": entry_quantity,
                "entry_price": entry_price,
            },
        )
        return

    try:
        submission = _submit_bracket_order(
            algo=algo,
            lean_symbol=lean_symbol,
            signal_id=signal_id,
            legs=legs,
            guard=guard,
        )
    except ExecutionError as exc:
        log.warning("[%s] Bracket submission unavailable: %s", signal_id, exc)
        _record_bracket_order_logged(
            algo,
            lean_symbol,
            signal_id,
            risk,
            broker_submission_status=BRACKET_ORDER_STATUS_LOGGED_ONLY,
            submitted_to_broker=False,
            metadata={
                **common_metadata,
                "reason": "bracket_submission_unavailable",
                "error": str(exc),
                "entry_quantity": entry_quantity,
                "entry_price": entry_price,
                "legs": legs,
            },
        )
        return
    _record_bracket_order_logged(
        algo,
        lean_symbol,
        signal_id,
        risk,
        broker_submission_status=BRACKET_ORDER_STATUS_SUBMITTED_TO_BROKER,
        submitted_to_broker=True,
        metadata={
            **common_metadata,
            "entry_quantity": entry_quantity,
            "entry_price": entry_price,
            "legs": legs,
            "submission": submission,
        },
    )


def _bracket_execution_guard(algo: Any) -> dict[str, Any]:
    stage = _runtime_stage(algo)
    enabled = _truthy_attr(
        algo,
        "BracketOrderExecutionEnabled",
        "bracket_order_execution_enabled",
        "bracket_orders_enabled",
    )
    if stage not in _BRACKET_EXECUTION_STAGES:
        return {
            "allowed": False,
            "stage": stage or "unknown",
            "reason": "bracket execution is limited to paper/sim stages",
        }
    if not enabled:
        return {
            "allowed": False,
            "stage": stage,
            "reason": "paper/sim bracket execution guard is disabled",
        }
    return {
        "allowed": True,
        "stage": stage,
        "reason": "paper/sim bracket execution guard passed",
    }


def _runtime_stage(algo: Any) -> str:
    for name in (
        "DeploymentStage",
        "deployment_stage",
        "RuntimeMode",
        "runtime_mode",
        "Environment",
        "environment",
    ):
        value = getattr(algo, name, None)
        if value is not None:
            return str(value).strip().lower()
    return ""


def _truthy_attr(algo: Any, *names: str) -> bool:
    for name in names:
        value = getattr(algo, name, None)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        if value is not None:
            return bool(value)
    return False


def _entry_fill_quantity(
    *,
    algo: Any,
    lean_symbol: Any,
    holdings_before: float,
    action: str,
    direction: str,
) -> float:
    holdings_after = _get_holdings_quantity(algo, lean_symbol)
    delta = holdings_after - holdings_before
    if action == "BUY" and direction == "LONG" and delta > 0:
        return abs(delta)
    if action == "SELL" and direction == "SHORT" and delta < 0:
        return abs(delta)
    return 0.0


def _build_bracket_legs(
    *,
    action: str,
    direction: str,
    entry_quantity: float,
    entry_price: float,
    stop_loss_pct: float,
    take_profit_pct: float,
) -> list[dict[str, Any]]:
    if entry_quantity <= 0 or entry_price <= 0:
        return []
    if action == "BUY" and direction == "LONG":
        exit_quantity = -abs(entry_quantity)
        stop_multiplier = 1 - stop_loss_pct
        take_profit_multiplier = 1 + take_profit_pct
    elif action == "SELL" and direction == "SHORT":
        exit_quantity = abs(entry_quantity)
        stop_multiplier = 1 + stop_loss_pct
        take_profit_multiplier = 1 - take_profit_pct
    else:
        return []

    legs: list[dict[str, Any]] = []
    if stop_loss_pct > 0 and stop_multiplier > 0:
        legs.append(
            {
                "leg_type": "stop_loss",
                "order_type": "STOP_MARKET",
                "quantity": exit_quantity,
                "stop_price": round(entry_price * stop_multiplier, 6),
            }
        )
    if take_profit_pct > 0 and take_profit_multiplier > 0:
        legs.append(
            {
                "leg_type": "take_profit",
                "order_type": "LIMIT",
                "quantity": exit_quantity,
                "limit_price": round(entry_price * take_profit_multiplier, 6),
            }
        )
    return legs


def _submit_bracket_order(
    *,
    algo: Any,
    lean_symbol: Any,
    signal_id: str,
    legs: list[dict[str, Any]],
    guard: dict[str, Any],
) -> dict[str, Any]:
    bracket_submitter = getattr(algo, "SubmitBracketOrder", None)
    if callable(bracket_submitter):
        result = bracket_submitter(
            lean_symbol,
            signal_id=signal_id,
            legs=legs,
            guard_stage=guard["stage"],
            broker_submission_status=BRACKET_ORDER_STATUS_SUBMITTED_TO_BROKER,
            submitted_to_broker=True,
        )
        return result if isinstance(result, dict) else {"result": result}

    submitted_legs: list[dict[str, Any]] = []
    missing_methods = _missing_bracket_order_methods(algo, legs)
    if missing_methods:
        missing = ", ".join(sorted(missing_methods))
        raise ExecutionError(f"[{signal_id}] missing bracket order method(s): {missing}")
    for leg in legs:
        if leg["order_type"] == "STOP_MARKET":
            stop_market = getattr(algo, "StopMarketOrder", None)
            ticket = stop_market(lean_symbol, leg["quantity"], leg["stop_price"])
            submitted_legs.append({**leg, "ticket": str(ticket) if ticket is not None else None})
        elif leg["order_type"] == "LIMIT":
            limit_order = getattr(algo, "LimitOrder", None)
            ticket = limit_order(lean_symbol, leg["quantity"], leg["limit_price"])
            submitted_legs.append({**leg, "ticket": str(ticket) if ticket is not None else None})
    return {"legs": submitted_legs}


def _missing_bracket_order_methods(algo: Any, legs: list[dict[str, Any]]) -> set[str]:
    missing: set[str] = set()
    for leg in legs:
        if leg["order_type"] == "STOP_MARKET" and not callable(getattr(algo, "StopMarketOrder", None)):
            missing.add("StopMarketOrder")
        elif leg["order_type"] == "LIMIT" and not callable(getattr(algo, "LimitOrder", None)):
            missing.add("LimitOrder")
    return missing


def _record_bracket_order_logged(
    algo: Any,
    lean_symbol: Any,
    signal_id: str,
    risk: dict[str, Any],
    *,
    broker_submission_status: str = BRACKET_ORDER_STATUS_LOGGED_ONLY,
    submitted_to_broker: bool = False,
    metadata: dict[str, Any] | None = None,
) -> None:
    recorder = getattr(algo, "RecordBracketOrderLogged", None)
    if not callable(recorder):
        return
    kwargs = {
        "signal_id": signal_id,
        "stop_loss_pct": float(risk.get("stop_loss_pct") or 0),
        "take_profit_pct": float(risk.get("take_profit_pct") or 0),
        "broker_submission_status": broker_submission_status,
        "submitted_to_broker": submitted_to_broker,
    }
    if metadata:
        kwargs["metadata"] = metadata
    try:
        recorder(lean_symbol, **kwargs)
    except TypeError:
        kwargs.pop("metadata", None)
        recorder(lean_symbol, **kwargs)


def _record_order_rejection(
    algo: Any,
    lean_symbol: Any,
    signal_id: str,
    *,
    reject_reason: str,
    requested_quantity: float,
    computed_quantity: float,
    quantity_type: str,
    order_type: str,
    price: float | None = None,
) -> None:
    recorder = getattr(algo, "RecordOrderRejected", None)
    if not callable(recorder):
        return
    kwargs = {
        "signal_id": signal_id,
        "reject_reason": reject_reason,
        "requested_quantity": float(requested_quantity),
        "computed_quantity": float(computed_quantity),
        "quantity_type": quantity_type,
        "order_type": order_type,
        "broker_submission_status": "rejected_before_broker",
        "submitted_to_broker": False,
    }
    if price is not None:
        kwargs["price"] = float(price)
    recorder(lean_symbol, **kwargs)


def _record_signal_noop(
    algo: Any,
    lean_symbol: Any,
    signal_id: str,
    *,
    noop_reason: str,
    requested_quantity: float,
    quantity_type: str,
    order_type: str,
    computed_quantity: float | None = None,
    price: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    recorder = getattr(algo, "RecordSignalNoop", None)
    if not callable(recorder):
        return
    kwargs = {
        "signal_id": signal_id,
        "noop_reason": noop_reason,
        "requested_quantity": float(requested_quantity),
        "quantity_type": quantity_type,
        "order_type": order_type,
        "broker_submission_status": "not_submitted_signal_noop",
        "submitted_to_broker": False,
    }
    if computed_quantity is not None:
        kwargs["computed_quantity"] = float(computed_quantity)
    if price is not None and price > 0:
        kwargs["price"] = float(price)
    if metadata:
        kwargs["metadata"] = dict(metadata)
    recorder(lean_symbol, **kwargs)


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
            _record_order_rejection(
                algo,
                lean_symbol,
                signal_id,
                reject_reason="shares_quantity_rounded_to_zero",
                requested_quantity=quantity,
                computed_quantity=shares,
                quantity_type=quantity_type,
                order_type=order_type,
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
            _record_order_rejection(
                algo,
                lean_symbol,
                signal_id,
                reject_reason="cash_value_resolved_to_zero_shares",
                requested_quantity=quantity,
                computed_quantity=shares,
                quantity_type=quantity_type,
                order_type=order_type,
                price=price,
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
        pass

    ensure_security = getattr(algo, "EnsureSecurity", None)
    if callable(ensure_security):
        try:
            return float(ensure_security(lean_symbol).Price)
        except Exception:
            return 0.0
    return 0.0
