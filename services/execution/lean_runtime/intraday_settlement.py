"""Next-session paper settlement and grading of intraday decisions.

SD_INTRADAY_MINUTE_BARS section 6.4 (K7).  KBar data for day D only exists after the
close (about 15:50) and after reconciliation, so no bar of day D is available during
day D.  An intraday paper decision therefore follows six steps (Asia/Taipei):

1. D >= 15:50 the minute bars of D are fetched.
2. They are reconciled against the official daily row; ``bar_availability_ts`` is stamped.
3. After ``bar_availability_ts`` and before D+1 08:55 the decision is submitted; its
   execution target is the D+1 first regular bar (``bar_start`` 09:00).
4. During D+1 the order is ``pending_settlement``.  It is never filled at D's close, at a
   past D bar open, or at an estimate.
5. After D+1 is reconciled, the real D+1 first-bar open fills the order as a
   ``delayed_simulated`` settlement and ``price_observed_at`` is recorded.
6. Once the horizon bar is reconciled, the return from that open to the horizon-th bar's
   close (truncated at 13:30 and flagged) is graded.

If D+1 is not reconciled by D+2 08:55 the decision is closed ``expired_no_price``: no fill,
no grade and no substitute price.  A ``delayed_simulated`` fill is never live-fill evidence
and says nothing about execution latency.

This module is pure: it never reads a provider or a store.  Callers hand it bars that a
reconciled-only loader produced.  The trading calendar and session helpers come from
``instrument_candidates`` (the governed calendar); no calendar is defined here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Collection, Mapping, Sequence

from services.execution.lean_runtime.instrument_candidates import (
    TAIPEI_TZ,
    in_regular_session,
    next_trading_day,
    session_bounds,
    submission_cutoff,
)
from services.registry.strategy_artifact import BAR_FREQUENCY_DATASETS

SETTLEMENT_MODE = "delayed_simulated"
STATUS_PENDING = "pending_settlement"
STATUS_SETTLED = "settled"
STATUS_EXPIRED = "expired_no_price"
GRADE_PENDING = "pending_grade"
GRADE_DONE = "graded"

REASON_NO_LIVE_FEED = "no_live_intraday_feed"
REASON_BAR_NOT_AVAILABLE = "intraday_bar_not_yet_available"
REASON_WINDOW_CLOSED = "intraday_submission_window_closed"
REASON_BAR_INVALID = "intraday_bar_invalid"

SESSION_MINUTES = 270  # 09:00..13:30 regular session

PENDING_RECORD_SUFFIX = "#intraday-pending"
SETTLEMENT_RECORD_SUFFIX = "#intraday-settlement"
GRADE_RECORD_SUFFIX = "#intraday-grade"

INTRADAY_FREQUENCIES = tuple(f for f in BAR_FREQUENCY_DATASETS if f != "1d")


class IntradayWindowError(ValueError):
    """A decision or bar was rejected; ``reason`` is a stable code."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def is_intraday_frequency(bar_frequency: Any) -> bool:
    return isinstance(bar_frequency, str) and bar_frequency in INTRADAY_FREQUENCIES


def frequency_minutes(bar_frequency: str) -> int:
    if not is_intraday_frequency(bar_frequency):
        raise IntradayWindowError(REASON_BAR_INVALID, f"{bar_frequency!r} is not an intraday bar frequency")
    return int(bar_frequency[:-1])


def bars_per_session(bar_frequency: str) -> int:
    """Bucket count of a regular session; the last bucket may be a flagged half bucket."""
    width = frequency_minutes(bar_frequency)
    return -(-SESSION_MINUTES // width)


def parse_ts(value: Any, *, field: str = "timestamp") -> datetime:
    """Strict timezone-aware ISO timestamp; naive or unparsable values are rejected."""
    text = str(value or "").strip()
    if not text:
        raise IntradayWindowError(REASON_BAR_INVALID, f"{field} is missing")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntradayWindowError(REASON_BAR_INVALID, f"{field} {text!r} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise IntradayWindowError(REASON_BAR_INVALID, f"{field} {text!r} has no timezone")
    return parsed


def _iso(moment: datetime) -> str:
    return moment.astimezone(TAIPEI_TZ).isoformat()


@dataclass(frozen=True)
class ExecutionPlan:
    """Step-3 outcome: when the decision may be filled and until when it may wait."""

    bar_frequency: str
    horizon_bars: int
    decision_day: str
    target_day: str
    decision_time: str
    bar_event_time: str
    bar_availability_ts: str
    window_closes_at: str
    execution_target_bar_start: str
    expires_at: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def plan_next_session_decision(
    *,
    event_time: Any,
    bar_availability_ts: Any,
    decision_time: Any,
    bar_frequency: str,
    horizon_bars: int = 1,
    holidays: Collection[date] = (),
) -> ExecutionPlan:
    """Admit a decision on the bar closing at ``event_time`` for next-session execution.

    Rejected (``IntradayWindowError``) when the decision clock lies inside a regular session
    (``no_live_intraday_feed``: no authorised intraday feed exists), before the bar's real
    availability, or at/after D+1 08:55.
    """
    frequency_minutes(bar_frequency)
    if isinstance(horizon_bars, bool) or not isinstance(horizon_bars, int) or horizon_bars < 1:
        raise IntradayWindowError(REASON_BAR_INVALID, "horizon_bars must be a positive integer")
    decided = parse_ts(decision_time, field="decision_time")
    if in_regular_session(decided, holidays):
        raise IntradayWindowError(
            REASON_NO_LIVE_FEED,
            "no authorised live intraday feed: no bar of the current session exists during the session",
        )
    ended = parse_ts(event_time, field="event_time")
    available = parse_ts(bar_availability_ts, field="bar_availability_ts")
    if available < ended:
        raise IntradayWindowError(REASON_BAR_INVALID, "bar_availability_ts precedes the bar end")
    day = ended.astimezone(TAIPEI_TZ).date()
    try:
        opened, closed = session_bounds(day, holidays)
    except ValueError as exc:
        raise IntradayWindowError(REASON_BAR_INVALID, str(exc)) from exc
    if ended > closed or ended <= opened:
        raise IntradayWindowError(REASON_BAR_INVALID, "bar end lies outside the regular session")
    if available <= closed:
        raise IntradayWindowError(
            REASON_BAR_INVALID, "bar_availability_ts precedes the session close; the day cannot be reconciled yet"
        )
    if decided < available:
        raise IntradayWindowError(
            REASON_BAR_NOT_AVAILABLE,
            f"decision time {_iso(decided)} precedes bar_availability_ts {_iso(available)}",
        )
    target_day = next_trading_day(day, holidays)
    cutoff = submission_cutoff(target_day)
    if decided >= cutoff:
        raise IntradayWindowError(
            REASON_WINDOW_CLOSED, f"decision time {_iso(decided)} is not before {_iso(cutoff)}"
        )
    target_open, _ = session_bounds(target_day, holidays)
    expiry = submission_cutoff(next_trading_day(target_day, holidays))
    return ExecutionPlan(
        bar_frequency=bar_frequency,
        horizon_bars=horizon_bars,
        decision_day=day.isoformat(),
        target_day=target_day.isoformat(),
        decision_time=_iso(decided),
        bar_event_time=_iso(ended),
        bar_availability_ts=_iso(available),
        window_closes_at=_iso(cutoff),
        execution_target_bar_start=_iso(target_open),
        expires_at=_iso(expiry),
    )


def pending_record(
    plan: ExecutionPlan,
    *,
    decision_id: str,
    symbol: str,
    direction: str,
) -> dict[str, Any]:
    """The durable ``pending_settlement`` record created with the decision (step 4)."""
    return {
        "decision_id": decision_id,
        "symbol": symbol,
        "direction": direction,
        "status": STATUS_PENDING,
        "settlement_mode": SETTLEMENT_MODE,
        "plan": plan.to_dict(),
        "fill": None,
    }


def _field(bar: Any, name: str) -> Any:
    return bar.get(name) if isinstance(bar, Mapping) else getattr(bar, name, None)


def _number(bar: Any, name: str) -> float:
    value = _field(bar, name)
    if isinstance(value, bool):
        raise IntradayWindowError(REASON_BAR_INVALID, f"bar {name} is not numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise IntradayWindowError(REASON_BAR_INVALID, f"bar {name} is not numeric") from exc
    if not math.isfinite(number) or number <= 0:
        raise IntradayWindowError(REASON_BAR_INVALID, f"bar {name} must be positive")
    return number


def _usable(bar: Any, plan: Mapping[str, Any], symbol: str, now: datetime) -> bool:
    """A bar is usable only when real, reconciled-available and not partial.

    Availability is the bar's own stamp, so a bar fetched after the close is never treated
    as available earlier than it was.  A bar available before the target session closed
    cannot be a reconciled post-close observation and is refused.
    """
    bar_symbol = _field(bar, "symbol")
    if bar_symbol not in (None, "") and str(bar_symbol) != symbol:
        return False
    if _field(bar, "partial") is True:
        return False
    stamp = _field(bar, "bar_availability_ts")
    if not stamp:
        return False
    available = parse_ts(stamp, field="bar_availability_ts")
    target_day = date.fromisoformat(plan["target_day"])
    _, target_close = session_bounds(target_day)
    return target_close <= available <= now


def _session_bars(bars: Sequence[Any], plan: Mapping[str, Any], symbol: str, now: datetime) -> list[Any]:
    target_day = plan["target_day"]
    kept = []
    for bar in bars:
        start = parse_ts(_field(bar, "bar_start"), field="bar_start")
        if start.astimezone(TAIPEI_TZ).date().isoformat() != target_day:
            continue
        declared = _field(bar, "bar_frequency")
        if declared not in (None, "") and declared != plan["bar_frequency"]:
            raise IntradayWindowError(
                REASON_BAR_INVALID,
                f"bar frequency {declared!r} does not match the declared {plan['bar_frequency']!r}",
            )
        if _usable(bar, plan, symbol, now):
            kept.append((start, bar))
    kept.sort(key=lambda pair: pair[0])
    return [bar for _, bar in kept]


def settle(
    pending: Mapping[str, Any],
    bars: Sequence[Any],
    *,
    now: Any,
) -> dict[str, Any]:
    """Step 5: fill from the real observed first bar of the target session, or expire.

    ``bars`` are bars of the target session from a reconciled-only loader.  Returns the
    next state: ``pending_settlement`` (no usable bar yet), ``settled`` or
    ``expired_no_price``.  The fill price is the observed first bar's open; it is never the
    decision bar's close, a past bar's open, or an estimate.
    """
    if pending.get("status") != STATUS_PENDING:
        return dict(pending)
    plan = pending["plan"]
    moment = parse_ts(now, field="now")
    expires_at = parse_ts(plan["expires_at"], field="expires_at")
    first = None
    target_start = parse_ts(plan["execution_target_bar_start"], field="execution_target_bar_start")
    for bar in _session_bars(bars, plan, str(pending["symbol"]), moment):
        if parse_ts(_field(bar, "bar_start")) == target_start:
            first = bar
            break
    if first is not None:
        available = parse_ts(_field(first, "bar_availability_ts"))
        if available > expires_at:
            first = None  # reconciled too late: the contract has already expired
    if first is None:
        if moment >= expires_at:
            return {**pending, "status": STATUS_EXPIRED, "fill": None, "expired_at": _iso(moment),
                    "reason": "target_session_not_reconciled_by_expiry"}
        return dict(pending)
    return {
        **pending,
        "status": STATUS_SETTLED,
        "fill": {
            "price": _number(first, "open"),
            "bar_start": _iso(target_start),
            "price_observed_at": _iso(parse_ts(_field(first, "bar_availability_ts"))),
            "settlement_mode": SETTLEMENT_MODE,
            "is_live_fill_evidence": False,
            "execution_latency_claim": None,
        },
    }


def grade(
    settled: Mapping[str, Any],
    bars: Sequence[Any],
    *,
    now: Any,
) -> dict[str, Any]:
    """Step 6: return from the settled open to the horizon-th bar's close.

    The horizon is counted in bars of the target session starting at the 09:00 bar.  A
    horizon beyond the session is graded to the last bar (13:30) and flagged ``truncated``.
    Until that bar is available and reconciled the grade stays ``pending_grade``.  A
    decision that was never settled has no grade.
    """
    if settled.get("status") != STATUS_SETTLED or not isinstance(settled.get("fill"), Mapping):
        return {"decision_id": settled.get("decision_id"), "status": "not_gradable",
                "reason": f"decision is {settled.get('status')}, not settled"}
    plan = settled["plan"]
    moment = parse_ts(now, field="now")
    usable = _session_bars(bars, plan, str(settled["symbol"]), moment)
    per_session = bars_per_session(plan["bar_frequency"])
    horizon = int(plan["horizon_bars"])
    index = min(horizon, per_session) - 1
    # The horizon bar is the horizon-th *grid* bucket; require the exact bucket, not the
    # nth usable one, so a skipped partial bar can never shift the grading bar.
    width = frequency_minutes(plan["bar_frequency"])
    opened, _ = session_bounds(date.fromisoformat(plan["target_day"]))
    want = opened.timestamp() + index * width * 60
    horizon_bar = next((b for b in usable if parse_ts(_field(b, "bar_start")).timestamp() == want), None)
    base = {"decision_id": settled["decision_id"], "bar_frequency": plan["bar_frequency"],
            "horizon_bars": horizon}
    if horizon_bar is None:
        return {**base, "status": GRADE_PENDING, "reason": "horizon_bar_not_available_or_not_reconciled"}
    entry = float(settled["fill"]["price"])
    exit_close = _number(horizon_bar, "close")
    gross = exit_close / entry - 1.0
    sign = -1.0 if str(settled.get("direction")).upper() == "SHORT" else 1.0
    return {
        **base,
        "status": GRADE_DONE,
        "entry_open": entry,
        "exit_close": exit_close,
        "gross_return": gross,
        "signed_return": sign * gross,
        "horizon_bar_start": _iso(parse_ts(_field(horizon_bar, "bar_start"))),
        "truncated": horizon > per_session or _field(horizon_bar, "half_bucket") is True,
        "price_observed_at": settled["fill"]["price_observed_at"],
        "graded_at": _iso(moment),
        "settlement_mode": SETTLEMENT_MODE,
    }


BarReader = Callable[[str, str, str], Sequence[Any]]


def advance_decision(
    store: Any,
    decision_id: str,
    bar_reader: BarReader,
    *,
    now: Any,
) -> dict[str, Any]:
    """Move one decision through steps 5 and 6 using immutable first-write records.

    ``bar_reader(symbol, session_date, bar_frequency)`` must return reconciled bars of
    that session only (``services.research.intraday_bars.load_bars``); a reader failure
    leaves the decision untouched.  Terminal records are written once and never edited.
    """
    pending = store.get_decision_record(decision_id + PENDING_RECORD_SUFFIX)
    if not isinstance(pending, Mapping):
        return {"decision_id": decision_id, "status": "no_pending_record"}
    settled = store.get_decision_record(decision_id + SETTLEMENT_RECORD_SUFFIX)
    plan = pending["plan"]
    if settled is None:
        bars = bar_reader(str(pending["symbol"]), plan["target_day"], plan["bar_frequency"])
        outcome = settle(pending, bars, now=now)
        if outcome["status"] == STATUS_PENDING:
            return outcome
        settled = store.put_decision_record(decision_id + SETTLEMENT_RECORD_SUFFIX, outcome)
    if settled["status"] != STATUS_SETTLED:
        return dict(settled)
    graded = store.get_decision_record(decision_id + GRADE_RECORD_SUFFIX)
    if graded is not None:
        return dict(graded)
    bars = bar_reader(str(settled["symbol"]), plan["target_day"], plan["bar_frequency"])
    result = grade(settled, bars, now=now)
    if result["status"] == GRADE_DONE:
        return store.put_decision_record(decision_id + GRADE_RECORD_SUFFIX, result)
    return result


__all__ = [
    "ExecutionPlan",
    "IntradayWindowError",
    "advance_decision",
    "bars_per_session",
    "grade",
    "is_intraday_frequency",
    "pending_record",
    "plan_next_session_decision",
    "settle",
]
