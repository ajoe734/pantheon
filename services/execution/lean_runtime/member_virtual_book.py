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
from services.foundation.reliable_delivery import atomic_write_text, sweep_stale_temp_siblings
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


# since_inception rule.  A member book states ``since_inception: true`` only when its history starts at
# the member's admission to the pool: a member view carried the pool admission time (``joined_at``),
# which the book records once as ``admitted_at``, and the first recorded mark falls on or after the admission date and
# within ADMISSION_FIRST_MARK_GRACE_DAYS calendar days of it (a weekend/holiday gap to the next
# session).  The admission time is stored on the book (``admitted_at``) and never rewritten; carried
# books keep the original one, and a book without one (no view carried ``joined_at`` yet, or written
# by an earlier runtime version) records the first carried value.  A book with no ``admitted_at``, a
# first mark before admission or too long after it are all ``since_inception: false`` -- unknown
# history is never presented as complete.
ADMISSION_FIRST_MARK_GRACE_DAYS = 5


def _since_inception(book):
    return _since_inception_reason(book) is None


def _since_inception_reason(book):
    """``None`` when the book's history starts at admission, else why it does not."""
    joined, rows = book.get("admitted_at"), book.get("observations")
    if not joined:
        return "admission_time_unknown"
    if not rows:
        return "no_observation"
    fill = book.get("admission_fill")
    if isinstance(fill, dict) and fill.get("gaps"):
        # A session between admission and the first live mark that neither recorded views nor the
        # governed replay produced: the history is incomplete, never presented as since inception.
        return "admission_gap_unfilled:" + ",".join(sorted({g["reason"] for g in fill["gaps"]}))
    if isinstance(fill, dict) and fill.get("sessions") and rows[0]["event_time"][:10] == fill["sessions"][0]:
        # Every governed session from admission was filled: the history starts at the first session on
        # or after admission, even when a long market holiday puts it past the grace days.
        return None
    try:
        start = datetime.fromisoformat(str(joined).replace("Z", "+00:00")).date()
        first = datetime.fromisoformat(rows[0]["event_time"].replace("Z", "+00:00")).date()
    except (ValueError, TypeError, KeyError):
        return "admission_or_mark_time_invalid"
    if first < start:
        return "first_mark_before_admission"
    if (first - start).days > ADMISSION_FIRST_MARK_GRACE_DAYS:
        return "first_mark_later_than_admission_grace"
    return None


# Admission fill (MEMBER-BOOK-ADMISSION-GAP-FILL-20261126).  A book whose first mark is later than its
# admission plus ADMISSION_FIRST_MARK_GRACE_DAYS is completed once, from the first governed trading
# session on or after the admission date up to (not including) its first live mark.  Recorded member
# views come first: books of the same member written under earlier binding files are carried in as they
# were recorded.  Every remaining session comes only from ``history_source`` -- in the runtime the
# existing pool admission replay (``services.research.pool_admission._replay``: registry interpreter,
# envelope netting, close-decided exposure filled at the next open) on the governed daily bars -- and
# names its source in ``source_ref``.  A session neither source produces stays a gap with its reason and
# the book stays ``since_inception: false``.  Live observations are never changed; filled rows are only
# inserted before the first live mark.  The outcome is recorded on the book as ``admission_fill`` so a
# carried book (restart, envelope or binding replacement) never fills again; a fill that left gaps is
# retried at most once per process (the source may have become available) and adds only missing sessions.
ADMISSION_REPLAY_SOURCE_PREFIX = "governed-replay:"


def _mark_date(event_time):
    return datetime.fromisoformat(str(event_time).replace("Z", "+00:00")).date()


def admission_gap_sessions(admitted_at, first_event_time, trading_day):
    """Governed trading sessions from the admission date up to the day before the first live mark."""
    from datetime import timedelta
    start, end = _mark_date(admitted_at), _mark_date(first_event_time)
    days, day = [], start
    while day < end:
        if trading_day(day):
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _session_event_time(session, first_event_time):
    """The live first mark's time of day (and offset) on ``session``: filled rows sort like live rows."""
    from datetime import date as _date
    first = datetime.fromisoformat(str(first_event_time).replace("Z", "+00:00"))
    shifted = first + (_date.fromisoformat(session) - first.date())
    text = shifted.isoformat()
    return text.replace("+00:00", "Z") if str(first_event_time).endswith("Z") else text


def _required_warmup_days(artifact):
    from services.registry.portfolio_envelope import required_portfolio_history

    warmup = max(required_portfolio_history(artifact) - 1, 0)
    logic = artifact.get("strategy_logic") or {}
    params = artifact.get("parameters") or {}
    hold = (logic.get("exit") or {}).get("max_holding_bars")
    if isinstance(hold, dict) and "const_param" in hold:
        val = params.get(hold["const_param"])
        if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
            warmup += int(val)
    return warmup


def _prepare_replay_dataset(dataset):
    """Prepare a governed daily dataset for ``pool_admission._replay`` without the backtest gate's
    two-instrument / thirty-bar floor, so a one-symbol member fill reads only its own symbol and sessions."""
    from services.research.vectorbt.adapter.vectorbt_adapter import (
        GovernedVectorbtInputAdapter,
        PreparedVectorbtDataset,
        REQUIRED_OHLCV_FIELDS,
        VectorbtWorkflowError,
        normalize_data_frequency,
    )

    adapter = GovernedVectorbtInputAdapter()
    dataset_id = adapter._req_str(dataset, "dataset_id")
    strategy_id = adapter._req_str(dataset, "strategy_id")
    source_dataset_refs = tuple(adapter._normalize_refs(dataset))
    source_strategy_spec_id = adapter._opt_str(dataset.get("source_strategy_spec_id"))
    data_frequency = normalize_data_frequency(adapter._opt_str(dataset.get("data_frequency")) or "daily")
    if data_frequency not in ("daily", "1d"):
        raise ValueError("backtest_requires_daily_bars")
    records = dataset.get("records")
    if not isinstance(records, (list, tuple)) or not records:
        raise VectorbtWorkflowError("dataset.records must be a non-empty list of OHLCV dicts")
    by_inst = {}
    for rec in records:
        if not isinstance(rec, dict):
            raise VectorbtWorkflowError("Each record must be a mapping")
        inst = adapter._req_str(rec, "instrument")
        day = adapter._req_iso_date(rec, "date", inst)
        bar = []
        for field in REQUIRED_OHLCV_FIELDS:
            val = rec.get(field)
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise VectorbtWorkflowError(f"record for {inst} missing numeric field '{field}'")
            bar.append(float(val))
        extras = {
            str(k): float(v)
            for k, v in rec.items()
            if k not in {"instrument", "date", *REQUIRED_OHLCV_FIELDS}
            and isinstance(v, (int, float))
            and not isinstance(v, bool)
        }
        if isinstance(rec.get("features"), dict):
            for fk, fv in rec["features"].items():
                if isinstance(fv, (int, float)) and not isinstance(fv, bool):
                    extras[str(fk)] = float(fv)
        by_inst.setdefault(inst, []).append((day, tuple(bar), extras))
    instruments = tuple(sorted(by_inst))
    bars_per_inst, ohlcv_by_inst, dates_by_inst, features_by_inst = {}, {}, {}, {}
    raw_features = dataset.get("features_by_instrument") or dataset.get("features")
    for inst in instruments:
        ordered = sorted(by_inst[inst], key=lambda item: item[0])
        dates = tuple(item[0] for item in ordered)
        bars = tuple(item[1] for item in ordered)
        extras_list = [item[2] for item in ordered]
        bars_per_inst[inst] = len(bars)
        ohlcv_by_inst[inst] = bars
        dates_by_inst[inst] = dates
        inst_features = {
            "open": [b[0] for b in bars],
            "high": [b[1] for b in bars],
            "low": [b[2] for b in bars],
            "close": [b[3] for b in bars],
            "volume": [b[4] for b in bars],
        }
        for ek in sorted({k for ex in extras_list for k in ex}):
            if all(ek in ex for ex in extras_list):
                inst_features[ek] = [ex[ek] for ex in extras_list]
        if isinstance(raw_features, dict):
            if inst in raw_features and isinstance(raw_features[inst], dict):
                for fk, fv in raw_features[inst].items():
                    inst_features[str(fk)] = fv
            else:
                for fk, fv in raw_features.items():
                    if fk not in instruments:
                        inst_features[str(fk)] = fv
        features_by_inst[inst] = inst_features
    return PreparedVectorbtDataset(
        dataset_id=dataset_id,
        strategy_id=strategy_id,
        source_dataset_refs=source_dataset_refs,
        source_strategy_spec_id=source_strategy_spec_id,
        instruments=instruments,
        bars_per_instrument=bars_per_inst,
        ohlcv_by_instrument=ohlcv_by_inst,
        data_frequency=data_frequency,
        num_instruments=len(instruments),
        total_bars=sum(bars_per_inst.values()),
        dates_by_instrument=dates_by_inst,
        features_by_instrument=features_by_inst,
        candidate_pool_market=copy.deepcopy(dataset.get("candidate_pool_market")),
    )


def governed_admission_replay_source(*, entry_reader, tenant_id, env=None, dataset_resolver=None, now=None):
    """Admission fill source of the runtime: the existing pool admission replay on governed daily bars.

    The member's immutable strategy artifact is read from the registry owner (``entry_reader(artifact_ref,
    tenant_id)``, the reader the member baselines already use) and must carry the member's checksum; the
    dataset is read from the stored daily bars
    (``services.research.governed_market_dataset.resolve_governed_dataset``) for only the member's own
    symbols and the sessions it needs (plus warmup); the per-session views come
    from ``services.research.pool_admission._replay`` (``rows_out``), so no second replay exists.  Any
    failure raises with its reason and the caller leaves the sessions as gaps.  One registry read and one
    dataset build per (member, checksum) per process.
    """
    cache = {}

    def build(member_id, member, sessions):
        from services.execution.shioaji_adapter import normalize_taiwan_symbol
        from services.research.pool_admission import _replay

        entry = entry_reader(member_id, tenant_id) if callable(entry_reader) else None
        if not entry:
            raise ValueError("member_artifact_not_in_registry")
        if entry.get("checksum") != member["checksum"]:
            raise ValueError("member_artifact_checksum_mismatch")
        artifact = ((entry.get("metadata") or {}).get("strategy_artifact"))
        if not isinstance(artifact, dict):
            raise ValueError("member_strategy_artifact_missing")
        if dataset_resolver is None:
            from services.research.governed_market_dataset import resolve_governed_dataset as resolver
        else:
            resolver = dataset_resolver
        dataset = resolver(
            symbols=list(artifact["parameters"]["symbols"]),
            sessions=list(sessions),
            warmup_days=_required_warmup_days(artifact),
            min_instruments=1,
            strategy_id=str(member_id),
            now=now,
            env=env,
        )
        prepared = _prepare_replay_dataset(dataset)
        return normalize_taiwan_symbol, _replay, prepared, artifact

    def source(member_id, member, symbol, sessions):
        key = (member_id, member["checksum"])
        if key not in cache:
            try:
                cache[key] = build(member_id, member, sessions)
            except Exception as exc:  # noqa: BLE001 - remembered so one failure is not retried per mark
                cache[key] = exc
        built = cache[key]
        if isinstance(built, Exception):
            raise built
        normalize, replay, prepared, artifact = built
        wanted = normalize(symbol)
        instrument = next((i for i in prepared.instruments if normalize(i) == wanted), None)
        if instrument is None:
            raise ValueError("book_symbol_not_in_governed_dataset")
        calendar = [str(day)[:10] for day in prepared.dates_by_instrument[instrument]]
        native = dict(zip(calendar, prepared.dates_by_instrument[instrument]))
        dates = [native[day] for day in sessions if day in native]
        gaps = {day: "session_not_in_governed_dataset" for day in sessions if day not in native}
        rows_out = []
        if dates:
            replay(prepared, artifact, dates, rows_out=rows_out)
        rows = {str(r["date"])[:10]: r for r in rows_out if normalize(r["symbol"]) == wanted}
        for day in sessions:
            if day not in rows and day not in gaps:
                gaps[day] = "replay_gave_no_view_for_session"
        return {"source_ref": f"{ADMISSION_REPLAY_SOURCE_PREFIX}{prepared.dataset_id}:pool_admission._replay",
                "rows": rows, "gaps": gaps}

    return source


def _default_trading_day(day):
    from services.execution.lean_runtime.instrument_candidates import is_trading_day
    return is_trading_day(day)


class MemberVirtualBooks:
    def __init__(self, path=None, *, legacy_paths=(), history_source=None, trading_day=None):
        """``legacy_paths``: earlier per-binding book files of this persona, read once to carry a member's
        recorded marks into this store.  ``history_source(member_id, member, symbol, sessions)`` returns
        ``{"source_ref", "rows": {session: {"exposure", "direction", "open", "close"}}, "gaps": {session:
        reason}}`` for the admission fill (or raises with the reason); without one no session is filled."""
        self.path = Path(path) if path else None
        self.legacy_paths = [Path(p) for p in legacy_paths]
        self.history_source = history_source
        self.trading_day = trading_day or _default_trading_day
        self._legacy_books = None
        self._fill_attempted = set()
        if self.path:
            sweep_stale_temp_siblings(self.path)
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
                else:
                    legacy = self._carry_legacy(member_id, member, symbol)
                    if legacy is not None:
                        legacy.update(binding_id=binding_id, envelope_checksum=envelope_checksum,
                                      virtual_only=member["virtual_only"], admitted_at=member.get("joined_at"))
                        candidate[key] = legacy
            book = candidate.setdefault(key, {"binding_id": binding_id,
                "envelope_checksum": envelope_checksum, "member_id": member_id,
                "artifact_id": member["artifact_id"], "version": member["version"],
                "checksum": member["checksum"], "virtual_only": member["virtual_only"],
                "symbol": symbol, "underlying_count": member.get("underlying_count", 1),
                "admitted_at": member.get("joined_at"), "observations": []})
            if not book.get("admitted_at") and member.get("joined_at"):
                # A book created (or carried) before its views carried the admission time records
                # the first one exactly once; a recorded admitted_at is never rewritten.
                book["admitted_at"] = member["joined_at"]
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
            self._fill_admission(key, book, member_id, member, symbol)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.path, json.dumps(
                {"schema_version": "member_virtual_books.v1", "books": candidate}, allow_nan=False))
        self.books = candidate
        return self.snapshot(binding_id=binding_id, envelope_checksum=envelope_checksum)

    def _carry_legacy(self, member_id, member, symbol):
        """The member's recorded marks from earlier per-binding book files, merged by event time, or ``None``.

        Only the exact immutable member (id, artifact, version, checksum, symbol, underlying count) is
        carried; marks are copied unchanged and a mark time recorded twice keeps the first row read from
        the newest file.  The admission time is not carried: the current member view supplies it."""
        if not self.legacy_paths:
            return None
        if self._legacy_books is None:
            self._legacy_books = []
            for path in sorted(self.legacy_paths, key=lambda p: (p.stat().st_mtime if p.exists() else 0),
                               reverse=True):
                try:
                    state = json.loads(path.read_text())
                except (OSError, ValueError):
                    continue  # unreadable legacy file: nothing is carried from it, nothing is invented
                if not isinstance(state, dict) or state.get("schema_version") != "member_virtual_books.v1":
                    continue
                for book in (state.get("books") or {}).values():
                    if isinstance(book, dict) and book.get("observations"):
                        self._legacy_books.append((path.name, book))
        matches = [(name, book) for name, book in self._legacy_books
                   if book.get("member_id") == member_id and book.get("artifact_id") == member["artifact_id"]
                   and book.get("version") == member["version"] and book.get("checksum") == member["checksum"]
                   and book.get("symbol") == symbol
                   and book.get("underlying_count") == member.get("underlying_count", 1)]
        if not matches:
            return None
        rows = {}
        for _, book in matches:
            for row in book["observations"]:
                rows.setdefault(row["event_time"], copy.deepcopy(row))
        ordered = sorted(rows.values(), key=lambda r: datetime.fromisoformat(r["event_time"].replace("Z", "+00:00")))
        carried = copy.deepcopy(matches[0][1])
        carried.pop("admission_fill", None)
        carried.update(observations=ordered, carried_from=sorted({name for name, _ in matches}))
        return carried

    def _fill_admission(self, key, book, member_id, member, symbol):
        """Complete the book from admission to its first live mark once (see the module notes above)."""
        fill = book.get("admission_fill")
        if isinstance(fill, dict) and (not fill.get("gaps") or key in self._fill_attempted):
            return
        rows = book["observations"]
        if not book.get("admitted_at") or not rows:
            return
        if fill is None:
            if _since_inception_reason(book) != "first_mark_later_than_admission_grace":
                return
            first_live = rows[0]["event_time"]
        else:
            first_live = fill["first_live_mark"]
        self._fill_attempted.add(key)
        try:
            sessions = admission_gap_sessions(book["admitted_at"], first_live, self.trading_day)
        except (ValueError, TypeError) as exc:
            book["admission_fill"] = {"first_live_mark": first_live, "sessions": [], "filled": [],
                                      "gaps": [{"date": None, "reason": f"calendar_unavailable:{type(exc).__name__}"}],
                                      "source_refs": []}
            return
        present = {r["event_time"][:10] for r in rows}
        wanted = [day for day in sessions if day not in present]
        gaps, produced, source_refs = {}, {}, set(fill.get("source_refs", [])) if fill else set()
        if wanted and self.history_source is None:
            gaps = {day: "admission_replay_source_unconfigured" for day in wanted}
        elif wanted:
            try:
                result = self.history_source(member_id, member, symbol, list(wanted))
                for day in wanted:
                    row = (result.get("rows") or {}).get(day)
                    if row is None:
                        gaps[day] = (result.get("gaps") or {}).get(day) or "session_not_in_governed_replay"
                        continue
                    price, opened = row.get("close"), usable_open(row.get("open"))
                    if isinstance(price, bool) or not isinstance(price, (int, float)) \
                            or not math.isfinite(price) or price <= 0:
                        gaps[day] = "replay_close_invalid"
                        continue
                    produced[day] = {"event_time": _session_event_time(day, first_live), "price": float(price),
                                     "open": opened, "source_ref": result["source_ref"],
                                     "exposure": float(row["exposure"]), "direction": int(row["direction"])}
                if produced:
                    source_refs.add(result["source_ref"])
            except Exception as exc:  # noqa: BLE001 - an unavailable source leaves a named gap, never a mark
                reason = str(exc).split(":", 1)[0].strip() or type(exc).__name__
                gaps = {day: f"admission_replay_unavailable:{reason}"[:200] for day in wanted}
                produced = {}
        if produced:
            # Inserted strictly before the first live mark; every live row is kept as recorded.
            live_start = datetime.fromisoformat(first_live.replace("Z", "+00:00"))
            if any(datetime.fromisoformat(r["event_time"].replace("Z", "+00:00")) >= live_start
                   for r in produced.values()):
                raise ValueError("admission fill must precede the first live mark")
            rows[:] = sorted(list(produced.values()) + rows,
                             key=lambda r: datetime.fromisoformat(r["event_time"].replace("Z", "+00:00")))
        filled = sorted(set((fill or {}).get("filled", [])) | set(produced))
        book["admission_fill"] = {"first_live_mark": first_live, "sessions": sessions, "filled": filled,
                                  "gaps": [{"date": day, "reason": reason} for day, reason in sorted(gaps.items())],
                                  "source_refs": sorted(source_refs)}

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
                           "since_inception": all(_since_inception(b) for b, _, _, _ in group),
                           "since_inception_reason": next((r for r in (_since_inception_reason(b)
                                                                        for b, _, _, _ in group) if r), None),
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


def idle_pool_observations(dates, *, source_ref, gross_limit, drawdown_limit):
    """Risk budget observations of a pool envelope with no member: nothing is held.

    Gross notional and drawdown are known and zero against the stated limits, so pool health reads the
    pool as idle (low utilisation) instead of unknown. Dates must be an ordered trading calendar.
    """
    if not source_ref or not (gross_limit > 0 and drawdown_limit > 0):
        raise ValueError("idle_observation_inputs_invalid")
    return [{"date": day, "source_ref": source_ref, "gross_notional": 0.0, "gross_limit": gross_limit,
             "drawdown": 0.0, "drawdown_limit": drawdown_limit} for day in dates]
