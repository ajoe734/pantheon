"""Bounded window readers over daily_bars."""
from __future__ import annotations

import statistics
import threading
import time
from datetime import date, timedelta

import pytest

pytest.importorskip("pyarrow")

from services.source_ingestion.daily_bars_reader import (
    MAX_RANGE_DAYS, MAX_SYMBOLS, MAX_WINDOW_CLOSES, DailyBarsReader, DailyBarsReadLimit,
)
from services.source_ingestion.daily_bars_store import DailyBarsCorrupt, DailyBarsStore
from services.source_ingestion.tests.test_daily_bars_store import make_rows


def trading_days(n: int, end: str = "2026-11-13"):
    out, d = [], date.fromisoformat(end)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return sorted(out)


def build(tmp_path, days: int, symbols: int, bump_per_day: bool = True):
    store = DailyBarsStore(tmp_path)
    ds = trading_days(days)
    for i, d in enumerate(ds):
        store.write_day(d, make_rows(symbols, trade_date=d, bump=float(i) if bump_per_day else 0.0), ingest_run_id=f"r-{d}")
    return ds


def test_window_returns_last_n_closes_oldest_first(tmp_path):
    ds = build(tmp_path, 10, 100)
    reader = DailyBarsReader(tmp_path)
    got = reader.read_close_window("1005.TW", ds[-1], 3)
    assert [r["trade_date"] for r in got] == ds[-3:]
    assert [r["close"] for r in got] == [10.5 + 5 + i for i in (7, 8, 9)]
    assert [r["trade_date"] for r in reader.read_close_window("1005.TW", ds[4], 3)] == ds[2:5]  # end_date respected
    multi = reader.read_close_window(["1005.TW", "1006.TW"], ds[-1], 2)
    assert set(multi) == {"1005.TW", "1006.TW"} and all(len(v) == 2 for v in multi.values())


def test_range_and_missing_symbol(tmp_path):
    ds = build(tmp_path, 6, 50)
    reader = DailyBarsReader(tmp_path)
    got = reader.read_range(["1001.TW", "9999.TW"], ds[1], ds[3])
    assert len(got["1001.TW"]) == 3 and got["9999.TW"] == []


def test_limits_fail_closed_before_reading(tmp_path):
    ds = build(tmp_path, 3, 20)
    reader = DailyBarsReader(tmp_path)
    for call in (
        lambda: reader.read_close_window("1001.TW", ds[-1], MAX_WINDOW_CLOSES + 1),
        lambda: reader.read_close_window("1001.TW", ds[-1], 0),
        lambda: reader.read_close_window("1001.TW", "not-a-date", 5),
        lambda: reader.read_close_window([f"S{i}" for i in range(MAX_SYMBOLS + 1)], ds[-1], 5),
        lambda: reader.read_range("1001.TW", ds[-1], ds[0]),
        lambda: reader.read_close_window([], ds[-1], 5),
    ):
        with pytest.raises(DailyBarsReadLimit):
            call()


def test_range_day_cap(tmp_path):
    reader = DailyBarsReader(tmp_path)
    reader._dates_cache = (tmp_path.stat().st_mtime_ns, [f"d{i}" for i in range(MAX_RANGE_DAYS + 1)])
    (tmp_path / "daily_bars" / "_manifest").mkdir(parents=True)
    reader._dates_cache = ((tmp_path / "daily_bars" / "_manifest").stat().st_mtime_ns,
                           [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(MAX_RANGE_DAYS + 1)])
    with pytest.raises(DailyBarsReadLimit, match="trading days"):
        reader.read_range("1001.TW", "2020-01-01", "2030-01-01")


def test_corrupt_file_fails_closed(tmp_path):
    ds = build(tmp_path, 2, 30)
    store = DailyBarsStore(tmp_path)
    (tmp_path / "daily_bars" / store.read_manifest(ds[0])["files"][0]["path"]).write_bytes(b"x")
    with pytest.raises(DailyBarsCorrupt):
        DailyBarsReader(tmp_path).read_close_window("1001.TW", ds[-1], 2)


def test_reader_during_replacement_sees_complete_old_or_new_set(tmp_path):
    ds = build(tmp_path, 1, 3000, bump_per_day=False)
    day = ds[0]
    stop, seen, errors = threading.Event(), set(), []

    def read():
        reader = DailyBarsReader(tmp_path)
        while not stop.is_set():
            try:
                rows = reader.read_range([f"{1000 + i}.TW" for i in (0, 1500, 2999)], day, day)
                closes = {tuple(r["close"] for r in v) for v in rows.values()}
                seen.add(len(closes))
                bumps = {round(v[0]["close"] - (10.5 + i % 50), 6) for v, i in zip(rows.values(), (0, 1500, 2999))}
                assert len(bumps) == 1 and len(next(iter(rows.values()))) == 1, bumps  # never a mix
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
                return

    t = threading.Thread(target=read)
    t.start()
    store = DailyBarsStore(tmp_path)
    for k in range(1, 8):
        store.write_day(day, make_rows(3000, trade_date=day, run=f"r{k}", bump=float(k)), ingest_run_id=f"r{k}")
    stop.set()
    t.join()
    assert not errors, errors


def test_p95_60_closes_of_50_symbols_on_hosted_shaped_data(tmp_path):
    """22,500 symbols per day (20,200 warrant-like), 60 trade days, 50 symbols: p95 <= 150 ms."""
    store = DailyBarsStore(tmp_path)
    ds = trading_days(60)
    base = make_rows(22_500, warrants=20_200)
    for d in ds:
        rows = [{**r, "trade_date": d} for r in base]
        store.write_day(d, rows, ingest_run_id=f"r-{d}")
    reader = DailyBarsReader(tmp_path)
    symbols = [r["symbol"] for r in base[:50]]
    timings = []
    for _ in range(40):
        t0 = time.perf_counter()
        out = reader.read_close_window(symbols, ds[-1], 60)
        timings.append((time.perf_counter() - t0) * 1000)
        assert all(len(v) == 60 for v in out.values())
    timings.sort()
    p95 = timings[int(len(timings) * 0.95) - 1]
    print(f"p95_ms={p95:.1f} median_ms={statistics.median(timings):.1f}")
    assert p95 <= 150, p95
