"""K4: governed bar aggregation, partial/half buckets, strict PIT and the fail-closed loader."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.research.intraday_bars import (
    InsufficientCleanHistory,
    IntradayBarsError,
    aggregate_session,
    load_bars,
    minutes_for_frequency,
)
from services.source_ingestion.intraday_reconcile import OfficialDaily, load_config, reconcile_day
from services.source_ingestion.tests.test_intraday_reconcile import DAY, NOW, _bars, _official, _store

CFG = load_config()


def _rows(skip=()):
    out = []
    for i, r in enumerate(_bars(skip=skip)):
        out.append({**r, "bar_availability_ts": "2026-11-10T09:00:00Z"})
    return out


def _by_start(bars):
    return {b.bar_start[11:16]: b for b in bars}


def test_session_anchored_buckets_and_half_bucket():
    bars = aggregate_session(_rows(), "60m", symbol="2330", session_date=DAY)
    assert [b.bar_start[11:16] for b in bars] == ["09:00", "10:00", "11:00", "12:00", "13:00"]
    last = bars[-1]
    assert last.half_bucket and last.expected_bars == 30 and last.bar_count == 31 and last.bar_end[11:16] == "13:30" and not last.partial
    assert not any(b.half_bucket for b in bars[:-1])
    assert all(b.dataset == "tw_price_60m" for b in bars)
    first = bars[0]  # real aggregation of the source rows
    assert (first.open, first.high, first.low, first.volume) == (100.0, 110.0, 95.0, 60 * 100)
    assert first.close == 100.5
    five = aggregate_session(_rows(), "5m", symbol="2330", session_date=DAY)
    assert len(five) == 54 and five[-1].expected_bars == 5 and not five[-1].half_bucket and five[-1].bar_count == 6
    assert five[-1].close == 104.0


def test_missing_minute_marks_partial_never_imputed():
    bars = _by_start(aggregate_session(_rows(skip=(3,)), "5m", symbol="2330", session_date=DAY))
    assert bars["09:00"].partial and bars["09:00"].bar_count == 4
    assert not bars["09:05"].partial
    # nothing imputed: volume is the sum of the 4 present bars
    assert bars["09:00"].volume == 400


def test_bucket_not_available_before_its_window_closes():
    rows = _rows()
    for r in rows:
        r["bar_availability_ts"] = "2026-11-10T00:00:00Z"  # claim availability earlier than bar_end
    first = aggregate_session(rows, "5m", symbol="2330", session_date=DAY)[0]
    assert first.bar_availability_ts == "2026-11-10T01:05:00Z"  # 09:05 +08, never earlier


def test_daily_unknown_and_other_frequencies_rejected():
    for bad in ("1d", "2m", "10m", "1h", "daily", "", None):
        with pytest.raises((IntradayBarsError, Exception)):
            minutes_for_frequency(bad)
    assert [minutes_for_frequency(f) for f in ("1m", "5m", "15m", "30m", "60m")] == [1, 5, 15, 30, 60]
    with pytest.raises(IntradayBarsError, match="daily"):
        load_bars(None, "2330", [DAY], "1d", decision_time=NOW)


def test_malformed_rows_fail_closed():
    rows = _rows()
    with pytest.raises(IntradayBarsError):
        aggregate_session(rows + [dict(rows[0])], "5m", symbol="2330", session_date=DAY)  # duplicate minute
    bad = [dict(rows[0], close=None)]
    with pytest.raises(IntradayBarsError):
        aggregate_session(bad, "5m", symbol="2330", session_date=DAY)
    with pytest.raises(IntradayBarsError):
        aggregate_session([dict(rows[0], bar_start="2026-11-10T08:30:00+08:00")], "5m", symbol="2330", session_date=DAY)


def _reconciled(tmp_path, official):
    istore = _store(tmp_path)
    istore.put_symbol_day("2330", DAY, _bars())
    reconcile_day(istore, official, DAY, CFG, clock=lambda: NOW)
    return istore


def test_loader_reads_reconciled_days_and_enforces_pit(tmp_path):
    istore = _reconciled(tmp_path, OfficialDaily([_official()], [DAY]))
    after = datetime(2026, 11, 11, tzinfo=timezone.utc)
    one = load_bars(istore, "2330", [DAY], "1m", decision_time=after)
    assert len(one) == 271 and one[-1].close == 104.0 and one[0].dataset == "tw_price_1m"
    sixty = load_bars(istore, "2330", [DAY], "60m", decision_time=after)
    assert len(sixty) == 5 and sixty[-1].half_bucket
    # NOW (09:00Z) == reconciled availability: usable at 09:00Z, not one second earlier
    assert len(load_bars(istore, "2330", [DAY], "5m", decision_time=NOW)) == 54
    assert load_bars(istore, "2330", [DAY], "5m", decision_time=datetime(2026, 11, 10, 8, 59, 59, tzinfo=timezone.utc)) == []
    # minute-unit availability lag shifts the gate
    assert load_bars(istore, "2330", [DAY], "5m", decision_time=NOW, availability_lag_minutes=1) == []
    with pytest.raises(IntradayBarsError):
        load_bars(istore, "2330", [DAY], "5m", decision_time=datetime(2026, 11, 11))  # naive time refused


def test_loader_fails_closed_on_quarantined_or_pending_or_missing_day(tmp_path):
    quarantined = _reconciled(tmp_path / "q", OfficialDaily([_official(c=150.0)], [DAY]))
    with pytest.raises(InsufficientCleanHistory) as exc:
        load_bars(quarantined, "2330", [DAY], "1m", decision_time=NOW)
    assert exc.value.missing_days == (DAY,)
    pending = _reconciled(tmp_path / "p", OfficialDaily([], []))
    with pytest.raises(InsufficientCleanHistory):
        load_bars(pending, "2330", [DAY], "15m", decision_time=NOW)
    with pytest.raises(InsufficientCleanHistory):  # a day never stored: no daily/synthetic substitute
        load_bars(_store(tmp_path / "e"), "2330", [DAY], "30m", decision_time=NOW)
