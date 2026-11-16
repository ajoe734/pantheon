"""One daily fill convention everywhere: D-close decision, D+1-open fill, on recorded TWSE bars.

The gate backtest orders, the pool admission replay, the member virtual book and the paper path
(``plan_daily_next_open_decision`` + ``settle``, PAPER-DAILY-FILL-TIMING-AND-FLUSH-20261115) must
produce the same fills (session and price) for a recorded sequence of sessions.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from services.execution.lean_runtime.intraday_settlement import (
    plan_daily_next_open_decision, pending_record, settle,
)
from services.execution.lean_runtime.member_virtual_book import (
    FILL_CONVENTION, MemberVirtualBooks, next_open_fills,
)
from services.registry.strategy_artifact import evaluate_strategy_view
from services.research.pool_admission import _replay
from services.research.tests.test_portfolio_envelope_parity import recorded_dataset
from services.research.vectorbt.adapter.vectorbt_adapter import (
    GovernedVectorbtInputAdapter, _artifact_order_sizes, _bar_features_for_index,
    build_canonical_tw_strategy_artifact,
)

SYMBOL, INSTRUMENT = "2330.TW", "2330.TWSE"
WINDOW = range(8, 14)  # six decision sessions -> five fills, all inside recorded data


@pytest.fixture(scope="module")
def recorded():
    prepared = GovernedVectorbtInputAdapter().prepare(recorded_dataset())
    artifact = build_canonical_tw_strategy_artifact(symbols=[SYMBOL])
    dates = list(prepared.dates_by_instrument[INSTRUMENT])
    bars = prepared.ohlcv_by_instrument[INSTRUMENT]
    views = {}
    for index in WINDOW:
        features, at = _bar_features_for_index(prepared, INSTRUMENT, index)
        views[index] = evaluate_strategy_view(
            artifact, [b[3] for b in bars[: index + 1]], features=features, decision_time=at, symbol=INSTRUMENT)
    return prepared, artifact, dates, bars, views


def _gate_fills(recorded):
    prepared, artifact, dates, bars, _ = recorded
    sizes = _artifact_order_sizes(prepared, artifact)[INSTRUMENT]
    return [(dates[i], bars[i][0], 1 if sizes[i] > 0 else -1)
            for i in range(WINDOW.start + 1, WINDOW.stop) if not math.isnan(sizes[i])]


def _pool_fills(recorded):
    prepared, artifact, dates, bars, _ = recorded
    collected = []
    _replay(prepared, artifact, [dates[i] for i in WINDOW], fills_out=collected)
    return [(f["date"], f["price"], 1 if f["decided_exposure"] > 0 else -1) for f in collected if f["filled"]]


def _book_fills(recorded):
    _, _, dates, bars, views = recorded
    books = MemberVirtualBooks()
    for index in WINDOW:
        [summary] = books.observe(
            binding_id="b", envelope_checksum="e", symbol=SYMBOL, event_time=f"{dates[index]}T05:30:00Z",
            price=bars[index][3], open_price=bars[index][0], source_ref="recorded:twse", bar_frequency="1d",
            members={"m": {"artifact_id": "a", "version": "1", "checksum": "sha256:m", "virtual_only": True,
                           "view": {"direction": views[index]["direction"], "strength": 1}}})
    return [(f["fill_time"][:10], f["price"], 1 if f["decided_exposure"] > 0 else -1)
            for f in summary["fills"] if f["filled"]]


def _paper_fills(recorded):
    _, _, dates, bars, views = recorded
    sessions = {date.fromisoformat(d) for d in dates}
    first, last = min(sessions), max(sessions)
    holidays = [first + timedelta(days=n) for n in range((last - first).days + 1)
                if (first + timedelta(days=n)) not in sessions and (first + timedelta(days=n)).weekday() < 5]
    out = []
    for index in range(WINDOW.start, WINDOW.stop - 1):
        decided = dates[index]
        plan = plan_daily_next_open_decision(
            event_time=decided, bar_availability_ts=f"{decided}T14:30:00+08:00",
            decision_time=f"{decided}T16:00:00+08:00", holidays=holidays)
        assert plan.target_day == dates[index + 1] and plan.bar_frequency == "1d"
        direction = views[index]["direction"]
        pending = pending_record(plan, decision_id=f"d-{index}", symbol=SYMBOL, direction=direction)
        bar = {"symbol": SYMBOL, "bar_start": plan.execution_target_bar_start, "bar_frequency": "1d",
               "bar_availability_ts": f"{plan.target_day}T14:00:00+08:00", "open": bars[index + 1][0]}
        settled = settle(pending, [bar], now=f"{plan.target_day}T15:00:00+08:00")
        assert settled["status"] == "settled"
        out.append((plan.target_day, settled["fill"]["price"], 1 if direction == "LONG" else -1))
    return out


def test_all_four_paths_fill_the_same_sessions_at_the_same_opens(recorded):
    _, _, dates, bars, views = recorded
    expected = [(dates[i + 1], bars[i + 1][0], 1 if views[i]["direction"] == "LONG" else -1)
                for i in range(WINDOW.start, WINDOW.stop - 1)]
    assert len(expected) >= 2
    assert len({day for day, _, _ in expected}) == len(expected)
    gate, pool, book, paper = (f(recorded) for f in (_gate_fills, _pool_fills, _book_fills, _paper_fills))
    assert gate == pool == book == paper == expected


def test_session_without_an_open_gives_no_fill_in_every_path_and_never_the_close(recorded):
    prepared, artifact, dates, bars, views = recorded
    gap = WINDOW.start + 2
    blank = dict(prepared.ohlcv_by_instrument)
    blank[INSTRUMENT] = tuple((0.0,) + tuple(b[1:]) if i == gap else b for i, b in enumerate(bars))
    patched = type(prepared)(**{**prepared.__dict__, "ohlcv_by_instrument": blank})
    assert next(f for f in next_open_fills([
        {"exposure": 1.0, "open": 100.0, "close": 100.0}, {"exposure": 1.0, "open": 0.0, "close": 101.0}]))["reason"] \
        == "no_open_price"
    sizes = _artifact_order_sizes(patched, artifact)[INSTRUMENT]
    assert math.isnan(sizes[gap])
    collected = []
    _replay(patched, artifact, [dates[i] for i in WINDOW], fills_out=collected)
    assert [(f["date"], f["reason"]) for f in collected if not f["filled"]] == [(dates[gap], "no_open_price")]
    assert all(f["price"] != bars[gap][3] for f in collected if f["filled"])
    # the member book records the same gap
    books = MemberVirtualBooks()
    for index in WINDOW:
        [summary] = books.observe(
            binding_id="b", envelope_checksum="e", symbol=SYMBOL, event_time=f"{dates[index]}T05:30:00Z",
            price=bars[index][3], open_price=None if index == gap else bars[index][0], source_ref="r",
            bar_frequency="1d", members={"m": {"artifact_id": "a", "version": "1", "checksum": "sha256:m",
                "virtual_only": True, "view": {"direction": views[index]["direction"], "strength": 1}}})
    assert [(g["fill_time"][:10], g["reason"]) for g in summary["fill_gaps"]] == [(dates[gap], "no_open_price")]
    assert summary["fill_convention"] == FILL_CONVENTION
