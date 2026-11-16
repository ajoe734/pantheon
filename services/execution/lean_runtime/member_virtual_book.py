"""Durable underlying-return attribution; no broker, capital or ledger writes."""
from __future__ import annotations

import copy
import json
import math
import os
import statistics
import uuid
from datetime import datetime
from pathlib import Path


GROSS_BASIS = "underlying_gross_virtual"
# Exposure-fraction returns with Taiwan statutory costs on every exposure change.
# Not the gate's share-sized/cash-fill basis, so never labelled as it.
NET_BASIS = "underlying_exposure_fraction_net_taiwan_costs_equal_notional"
from services.execution.lean_runtime.taiwan_cash_costs import (
    COMMISSION_RATE as _COMMISSION_RATE,
    EQUITY_TAX_RATE as _EQUITY_TAX_RATE,
    ETF_TAX_RATE as _ETF_TAX_RATE,
)


def _net_interval_costs(symbol, rows):
    """Fractional-notional cost of the exposure change made at each observation."""
    tax = _ETF_TAX_RATE if str(symbol).split(".")[0].startswith("00") else _EQUITY_TAX_RATE
    costs, held = [], 0.0
    for row in rows:
        change = row["exposure"] - held
        costs.append(_COMMISSION_RATE * abs(change) + tax * max(0.0, -change))
        held = row["exposure"]
    return costs


# One daily fill convention for every research/replay path (gate backtest, pool admission replay,
# member virtual book, expression replay, approved baselines): a decision computed from the close
# of session D takes effect at the OPEN of session D+1 with statutory costs.  A session without a
# usable open price gives no fill with a recorded reason; the close is never a fallback.
FILL_CONVENTION = "daily_decision_close_d_fills_open_d_plus_1"
LEGACY_FILL_CONVENTION = "daily_decision_close_fills_same_bar_close"
NO_OPEN_PRICE = "no_open_price"


def usable_open(value):
    """The observed open as a float, or ``None`` when absent or not a positive finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def next_open_fills(rows):
    """Fills of per-session decisions under the shared convention.

    ``rows`` are ordered sessions ``{"exposure", "open", "close"[, "date"]}`` where ``exposure`` is
    decided from that session's close.  The decision of row ``i-1`` fills at ``rows[i]["open"]``;
    a missing open leaves the position unchanged and records ``no_open_price`` for that session.
    Returns one entry per session ``i >= 1``: ``{"index", "date", "decision_index", "price",
    "exposure_before", "exposure_after", "filled", "reason"}``.
    """
    held, out = 0.0, []
    for i in range(1, len(rows)):
        decided = rows[i - 1]["exposure"]
        price = usable_open(rows[i].get("open"))
        entry = {"index": i, "date": rows[i].get("date"), "decision_index": i - 1,
                 "price": price, "exposure_before": held, "decided_exposure": decided}
        if price is None:
            entry.update(filled=False, reason=NO_OPEN_PRICE, exposure_after=held)
        else:
            entry.update(filled=True, reason=None, exposure_after=decided)
            held = decided
        out.append(entry)
    return out


def next_open_net_returns(symbol, rows, *, liquidate_final=False, with_costs=True):
    """Per-session net exposure-fraction returns marked at the close with next-open fills.

    Session ``i`` earns the carried position over close(i-1)->open(i), the newly filled position
    over open(i)->close(i), less statutory costs on the exposure change filled at open(i).  An
    unfilled session carries the previous position close to close.  ``liquidate_final`` charges the
    exit of the last held position (an untouched window's mark-out at the last close).
    Returns ``(returns, fills)``.
    """
    fills = next_open_fills(rows)
    tax = _ETF_TAX_RATE if str(symbol).split(".")[0].startswith("00") else _EQUITY_TAX_RATE
    returns = []
    for fill in fills:
        i, before, after = fill["index"], fill["exposure_before"], fill["exposure_after"]
        prev_close, close = rows[i - 1]["close"], rows[i]["close"]
        if fill["filled"]:
            change = after - before
            value = before * (fill["price"] / prev_close - 1) + after * (close / fill["price"] - 1)
            if with_costs:
                value -= _COMMISSION_RATE * abs(change) + tax * max(0.0, -change)
        else:
            value = before * (close / prev_close - 1)
        returns.append(value)
    if liquidate_final and with_costs and fills:
        returns[-1] -= _COMMISSION_RATE * abs(fills[-1]["exposure_after"]) \
            + tax * max(0.0, fills[-1]["exposure_after"])
    return returns, fills


GATE_INIT_CASH = 100_000.0  # vectorbt adapter BacktestConfig.init_cash default
# pf.sharpe_ratio() with freq='1D' annualises with vectorbt's default year_freq=365 days.
GATE_PERIODS_PER_YEAR = 365


def gate_basis_metrics(books, order_quantity, *, periods_per_year=GATE_PERIODS_PER_YEAR):
    """Replay the gate's share-sized cash portfolio from the recorded directions.

    Mirrors the vectorbt gate path: per instrument, a BUY/SELL view is a signed
    ``order_quantity``-share order filled at the mark (FLAT is no order), buys are
    capped by cash, commission on every fill plus sell tax, equity = cash + shares
    * mark from ``GATE_INIT_CASH``, Sharpe = mean/stdev(ddof=1)*sqrt(365) of the
    daily equity returns (annualised by vectorbt's year_freq=365 days, not 252), and the aggregate is the mean of per-instrument Sharpe
    and drawdown.  Returns ``(metrics, None)`` or ``(None, reason)``.
    """
    if isinstance(order_quantity, bool) or not isinstance(order_quantity, (int, float)) \
            or not math.isfinite(order_quantity) or order_quantity <= 0:
        return None, "order_quantity_unavailable"
    sharpes, drawdowns, counts = [], [], []
    for book in books:
        rows = book["observations"]
        if any("direction" not in row for row in rows):
            return None, "direction_not_recorded"
        tax = _ETF_TAX_RATE if str(book["symbol"]).split(".")[0].startswith("00") else _EQUITY_TAX_RATE
        cash, shares, previous, peak, drawdown = GATE_INIT_CASH, 0.0, GATE_INIT_CASH, None, 0.0
        returns = []
        for index, row in enumerate(rows):
            # The BUY/SELL decided at the previous close fills at this session's open.
            fill_price = usable_open(row.get("open"))
            price = row["price"]
            sign = rows[index - 1]["direction"] if index and fill_price is not None else 0
            if sign > 0:
                size = min(order_quantity, cash / (fill_price * (1 + _COMMISSION_RATE)))
                cash -= size * fill_price * (1 + _COMMISSION_RATE)
                shares += size
            elif sign < 0:
                cash += order_quantity * fill_price * (1 - _COMMISSION_RATE - tax)
                shares -= order_quantity
            equity = cash + shares * price
            returns.append(equity / previous - 1)
            previous = equity
            # vectorbt's max_drawdown peaks from the first bar's value, not init_cash.
            peak = equity if peak is None else max(peak, equity)
            drawdown = max(drawdown, (peak - equity) / peak)
        deviation = statistics.stdev(returns) if len(returns) > 1 else 0.0
        if deviation <= 0:
            return None, "insufficient_or_constant_returns"
        sharpes.append(statistics.mean(returns) / deviation * math.sqrt(periods_per_year))
        drawdowns.append(drawdown)
        counts.append(len(returns))
    if not sharpes:
        return None, "no_underlyings"
    from services.evolution.approved_baselines import RETURN_BASIS
    return {"sharpe_ratio": math.fsum(sharpes) / len(sharpes),
            "max_drawdown": math.fsum(drawdowns) / len(drawdowns),
            "observation_count": min(counts), "basis": RETURN_BASIS,
            "periods_per_year": periods_per_year, "order_quantity": order_quantity,
            "init_cash": GATE_INIT_CASH, "fill_convention": FILL_CONVENTION,
            "is_real_order": False}, None


def virtual_book_metrics(returns, *, periods_per_year=252, basis=GROSS_BASIS):
    equity = peak = 1.0
    drawdown = 0.0
    for value in returns:
        if not math.isfinite(value):
            raise ValueError("virtual returns must be finite")
        equity *= 1 + value
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak)
    deviation = statistics.stdev(returns) if len(returns) > 1 else 0.0
    return {"return_series": list(returns), "total_return": equity - 1,
            "sharpe_ratio": statistics.mean(returns) / deviation * math.sqrt(periods_per_year)
            if deviation > 0 else None,
            "sharpe_undefined_reason": None if deviation > 0 else "insufficient_or_constant_returns",
            "max_drawdown": drawdown, "observation_count": len(returns),
            "basis": basis, "periods_per_year": periods_per_year,
            "is_real_order": False}


class MemberVirtualBooks:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.books = {}
        if self.path and self.path.exists():
            state = json.loads(self.path.read_text())
            if state.get("schema_version") != "member_virtual_books.v1":
                raise ValueError("unsupported virtual book schema")
            self.books = state["books"]
            self.snapshot()  # reject corrupt metrics before accepting new observations

    def observe(self, **observation):
        if self.path is None:
            return self._observe(**observation)
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with Path(str(self.path) + ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                if self.path.exists():
                    self.books = MemberVirtualBooks(self.path).books
                return self._observe(**observation)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _observe(self, *, binding_id, envelope_checksum, symbol, event_time,
                price, source_ref, members, bar_frequency, open_price=None):
        if not source_ref or not event_time or not members:
            raise ValueError("virtual book requires source, time and pinned member views")
        at = datetime.fromisoformat(str(event_time).replace("Z", "+00:00"))
        if at.tzinfo is None:
            raise ValueError("virtual book mark requires timezone")
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            raise ValueError("virtual book mark must be positive and finite")
        # Annualisation is only defined for the currently supported daily bars.
        if bar_frequency not in {"1d", "d", "daily", "1day"}:
            raise ValueError("virtual book annualisation requires daily bar cadence")
        candidate = copy.deepcopy(self.books)
        for member_id, member in members.items():
            key = json.dumps([binding_id, envelope_checksum, member_id, symbol])
            if key not in candidate:
                # A pool publication changes the envelope/binding, not an
                # unchanged member's virtual strategy. Carry only the exact
                # immutable member/symbol history in this persona-scoped store.
                prior = [b for b in candidate.values() if b["member_id"] == member_id
                         and b["artifact_id"] == member["artifact_id"] and b["checksum"] == member["checksum"]
                         and b["version"] == member["version"] and b["symbol"] == symbol
                         and b["underlying_count"] == member.get("underlying_count", 1)]
                if prior:
                    carried = copy.deepcopy(max(prior, key=lambda b: b["observations"][-1]["event_time"]))
                    carried.update(binding_id=binding_id, envelope_checksum=envelope_checksum,
                                   virtual_only=member["virtual_only"])
                    candidate[key] = carried
            book = candidate.setdefault(key, {"binding_id": binding_id,
                "envelope_checksum": envelope_checksum, "member_id": member_id,
                "artifact_id": member["artifact_id"], "version": member["version"],
                "checksum": member["checksum"], "virtual_only": member["virtual_only"],
                "symbol": symbol, "underlying_count": member.get("underlying_count", 1),
                "observations": []})
            view = member["view"]
            strength = 1.0 if view["strength"] is None else view["strength"]
            if not isinstance(strength, (int, float)) or not math.isfinite(strength) or not 0 <= strength <= 1:
                raise ValueError("invalid member virtual strength")
            exposure = {"LONG": 1, "SHORT": -1, "FLAT": 0}[view["direction"]] * strength
            observation = {"event_time": event_time, "price": price, "open": usable_open(open_price),
                           "source_ref": source_ref, "exposure": exposure,
                           "direction": {"LONG": 1, "SHORT": -1, "FLAT": 0}[view["direction"]]}
            rows = book["observations"]
            if rows:
                previous_at = datetime.fromisoformat(rows[-1]["event_time"].replace("Z", "+00:00"))
                if at <= previous_at:
                    prior = next((row for row in rows if row["event_time"] == event_time), None)
                    if prior is not None:
                        if prior != observation:
                            raise ValueError("conflicting virtual mark for the same bar")
                        continue
                    raise ValueError("out-of-order virtual mark")
            rows.append(observation)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                with temporary.open("w") as handle:
                    json.dump({"schema_version": "member_virtual_books.v1", "books": candidate},
                              handle, allow_nan=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                temporary.unlink(missing_ok=True)
        self.books = candidate
        return self.snapshot(binding_id=binding_id, envelope_checksum=envelope_checksum)

    def snapshot(self, *, binding_id=None, envelope_checksum=None, baseline_resolver=None):
        """``baseline_resolver(artifact_id, checksum, symbols)`` returns the member's own baseline."""
        groups = {}
        for book in self.books.values():
            if binding_id and book["binding_id"] != binding_id:
                continue
            if envelope_checksum and book["envelope_checksum"] != envelope_checksum:
                continue
            rows = book["observations"]
            marks = [{"exposure": r["exposure"], "open": r.get("open"), "close": r["price"],
                      "date": r["event_time"]} for r in rows]
            # Decisions of mark D are filled at the open of mark D+1 (shared convention); a mark
            # without a recorded open gives no fill and is listed in ``fill_gaps``.
            gross_series, fills = next_open_net_returns(book["symbol"], marks, with_costs=False)
            net_series, _ = next_open_net_returns(book["symbol"], marks)
            spans = [(previous["event_time"], row["event_time"]) for previous, row in zip(rows, rows[1:])]
            returns = dict(zip(spans, gross_series))
            net = dict(zip(spans, net_series))
            book_fills = [{"symbol": book["symbol"], "decision_time": rows[f["decision_index"]]["event_time"],
                           "fill_time": rows[f["index"]]["event_time"], **{k: f[k] for k in (
                               "price", "filled", "reason", "decided_exposure", "exposure_after")}}
                          for f in fills]
            key = (book["binding_id"], book["envelope_checksum"], book["member_id"])
            groups.setdefault(key, []).append((book, returns, net, book_fills))
        result = []
        for group in groups.values():
            book = group[0][0]
            count = book["underlying_count"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError("invalid virtual book underlying count")
            all_underlyings = len(group) == count
            complete = all_underlyings and len({b["observations"][-1]["event_time"] for b, _, _, _ in group}) == 1
            # Only common, observed return intervals enter member metrics. A
            # missing underlying mark never becomes a fabricated zero return.
            dates = set.intersection(*(set(returns) for _, returns, _, _ in group)) if all_underlyings else set()
            dates = sorted(dates)
            returns = [math.fsum(series[day] for _, series, _, _ in group) / count for day in dates]
            net_returns = [math.fsum(series[day] for _, _, series, _ in group) / count for day in dates]
            underlyings = [{"symbol": subbook["symbol"], **virtual_book_metrics(list(series.values())),
                            "as_of": subbook["observations"][-1]["event_time"]}
                           for subbook, series, _, _ in group]
            result.append({**{k: v for k, v in book.items()
                              if k not in {"observations", "symbol", "underlying_count"}},
                           **virtual_book_metrics(returns), "underlyings": underlyings,
                           "dates": [end[:10] for _, end in dates],
                           "returns": returns,
                           "source_ref": sorted({r["source_ref"] for b, _, _, _ in group for r in b["observations"]}),
                           "net_of_costs": {**virtual_book_metrics(net_returns, basis=NET_BASIS),
                               "returns": net_returns},
                           "aggregation": "equal_notional_per_underlying",
                           "fill_convention": FILL_CONVENTION,
                           "fills": [f for *_, group_fills in group for f in group_fills],
                           "fill_gaps": [f for *_, group_fills in group for f in group_fills if not f["filled"]],
                           "marks_complete": complete,
                           "as_of": dates[-1][1] if dates else None})
            if baseline_resolver is not None:
                from services.evolution.approved_baselines import compare_member_book
                member = result[-1]
                baseline = baseline_resolver(member["member_id"], member["checksum"],
                                             [u["symbol"] for u in member["underlyings"]])
                member["baseline"] = baseline
                if isinstance(baseline, dict) and baseline.get("status") == "approved":
                    # The gate scores one common window; replay only when every
                    # underlying is observed and all share the same bar times.
                    times = {tuple(row["event_time"] for row in b["observations"]) for b, _, _, _ in group}
                    if not all_underlyings:
                        gate_book, reason = None, "underlying_marks_missing"
                    elif not complete or len(times) != 1:
                        gate_book, reason = None, "histories_not_aligned_to_common_window"
                    else:
                        gate_book, reason = gate_basis_metrics(
                            [b for b, _, _, _ in group], baseline.get("order_quantity"))
                    member["gate_basis"] = gate_book if gate_book else {"unavailable_reason": reason}
                member["baseline_comparison"] = compare_member_book(baseline, member)
        return result
