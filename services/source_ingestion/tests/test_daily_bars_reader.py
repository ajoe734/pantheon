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
from services.source_ingestion.tests import wall_clock_bounds
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


def test_p95_60_closes_of_50_symbols_on_hosted_shaped_data(tmp_path, monkeypatch):
    """22,500 symbols per day (20,200 warrant-like), 60 trade days, 50 symbols: p95 <= 150 ms."""
    store = DailyBarsStore(tmp_path)
    ds = trading_days(60)
    base = make_rows(22_500, warrants=20_200)
    for d in ds:
        rows = [{**r, "trade_date": d} for r in base]
        store.write_day(d, rows, ingest_run_id=f"r-{d}")
    reader = DailyBarsReader(tmp_path)
    symbols = [r["symbol"] for r in base[:50]]
    reads = wall_clock_bounds.count_parquet_reads(monkeypatch)
    timings = []
    for _ in range(40):
        t0 = time.perf_counter()
        out = reader.read_close_window(symbols, ds[-1], 60)
        timings.append((time.perf_counter() - t0) * 1000)
        assert all(len(v) == 60 for v in out.values())
    timings.sort()
    p95 = timings[int(len(timings) * 0.95) - 1]
    print(f"p95_ms={p95:.1f} median_ms={statistics.median(timings):.1f}")
    # Counter that does not depend on host speed: a 60-close window of 50 symbols decodes at most one row group of one
    # file per trade date (the statistics pick it), never a whole day's file set: 2,400 in 40 requests.
    per_request_ceiling = len(ds) * len(timings)
    assert reads["row_groups"] <= per_request_ceiling and reads["opens"] <= per_request_ceiling, reads
    wall_clock_bounds.check("daily_bars_reader.p95_60_closes", p95, 150, unit="ms")


# --- SOURCE-DAILY-BARS-READERS-COMPLETE-20261118: symbol forms, listing, columns, cold read ------------------
from services.source_ingestion.daily_bars_reader import PRICE_COLUMNS, symbol_forms  # noqa: E402


def test_symbol_forms_are_the_same_for_the_bare_code_and_the_canonical_symbol():
    expected = ["2330", "2330.TW", "2330.TWSE", "2330.TWO", "2330.TPEX"]
    for given in ("2330", "2330.TW", "2330.twse", "2330.TWO", "2330.TPEX"):
        assert symbol_forms(given) == expected
    assert symbol_forms("030001") == ["030001", "030001.TW", "030001.TWSE", "030001.TWO", "030001.TPEX"]
    assert symbol_forms("TX.FUT.X") == ["TX.FUT.X"]  # not a venue suffix: untouched


def test_any_form_gives_the_bare_and_the_canonical_caller_the_same_rows(tmp_path):
    ds = build(tmp_path, 6, 30)
    reader = DailyBarsReader(tmp_path)
    canonical = reader.read_range("1005.TW", ds[0], ds[-1])
    assert len(canonical) == 6
    for asked in ("1005", "1005.TW", "1005.TWSE"):
        assert reader.read_range(asked, ds[0], ds[-1], any_form=True) == canonical, asked
        assert reader.read_close_window(asked, ds[-1], 6, any_form=True) == canonical, asked
    multi = reader.read_range(["1005", "1006.TW"], ds[0], ds[-1], any_form=True)
    assert set(multi) == {"1005", "1006.TW"} and all(r["symbol"].endswith(".TW") for v in multi.values() for r in v)
    assert reader.read_range("1005", ds[0], ds[-1]) == []  # without the flag the exact stored symbol is asked
    assert reader.read_range("9999", ds[0], ds[-1], any_form=True) == []


def test_two_stored_forms_with_a_bar_on_one_day_fail_closed(tmp_path):
    ds = build(tmp_path, 3, 5)
    rows = make_rows(5, trade_date=ds[-1], bump=100.0)
    clash = dict(rows[0], symbol="1000.TWSE")  # the same code under another form, on a stored day
    DailyBarsStore(tmp_path).write_day(ds[-1], rows + [clash], ingest_run_id="clash")
    reader = DailyBarsReader(tmp_path)
    with pytest.raises(DailyBarsCorrupt, match="1000.TW.*1000.TWSE.*ambiguous|1000.TWSE.*1000.TW.*ambiguous"):
        reader.read_range("1000", ds[0], ds[-1], any_form=True)
    assert len(reader.read_range("1000.TW", ds[0], ds[-1])) == 3  # the exact form is still readable


def test_columns_limit_what_is_decoded_and_are_validated(tmp_path):
    ds = build(tmp_path, 3, 10)
    reader = DailyBarsReader(tmp_path)
    rows = reader.read_range("1001.TW", ds[0], ds[-1], columns=PRICE_COLUMNS)
    assert set(rows[0]) == set(PRICE_COLUMNS)
    full = reader.read_range("1001.TW", ds[0], ds[-1])
    assert [{k: r[k] for k in PRICE_COLUMNS} for r in full] == rows
    for bad in (["symbol", "close"], ["symbol", "trade_date", "nope"], ["trade_date", "close"]):
        with pytest.raises(DailyBarsReadLimit, match="columns must be stored columns"):
            reader.read_range("1001.TW", ds[0], ds[-1], columns=bad)


def test_list_symbols_matches_the_store_listing_and_a_repeat_opens_no_file(tmp_path, monkeypatch):
    ds = build(tmp_path, 6, 40)
    store = DailyBarsStore(tmp_path)
    reader = DailyBarsReader(tmp_path)
    assert reader.list_symbols() == store.symbol_stats()
    assert reader.list_symbols(ds[1], ds[3]) == store.symbol_stats(ds[1], ds[3])
    assert reader.list_symbols(ds[1], ds[3])["1005.TW"] == (3, ds[1], ds[3])
    import pyarrow.parquet as pq

    def boom(*a, **k):
        raise AssertionError("a repeat listing must come from the manifest signature cache")

    monkeypatch.setattr(pq, "read_table", boom)
    assert reader.list_symbols(ds[1], ds[3])["1005.TW"] == (3, ds[1], ds[3])
    with pytest.raises(DailyBarsReadLimit):
        reader.list_symbols("2026-13-45", None)
    with pytest.raises(DailyBarsReadLimit):
        reader.list_symbols(ds[3], ds[1])
    assert DailyBarsReader(tmp_path / "nothing").list_symbols() == {}


def test_a_rewritten_day_changes_the_listing(tmp_path):
    ds = build(tmp_path, 3, 10)
    reader = DailyBarsReader(tmp_path)
    assert "7777.TW" not in reader.list_symbols()
    extra = make_rows(10, trade_date=ds[-1])
    extra.append(dict(extra[0], symbol="7777.TW"))
    DailyBarsStore(tmp_path).write_day(ds[-1], extra, ingest_run_id="more")
    assert reader.list_symbols()["7777.TW"] == (1, ds[-1], ds[-1])


def test_cold_process_read_of_one_symbol_two_years_returns_every_bar(tmp_path):
    """A new process, no cache: correctness of the path the cold bound is measured on (the bound itself is
    measured in the evidence run on the generated hosted shape: ``test_backfill_daily_bars cold``)."""
    import subprocess
    import sys

    ds = build(tmp_path, 504, 4)
    code = ("import sys,json;sys.path.insert(0,'.');"
            "from services.source_ingestion.daily_bars_reader import DailyBarsReader as R,PRICE_COLUMNS as C;"
            f"r=R({str(tmp_path)!r});d=r.trade_dates();rows=r.read_range('1001',d[-504],d[-1],any_form=True,columns=C);"
            "print(json.dumps([len(rows),rows[0]['trade_date'],rows[-1]['trade_date']]))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         cwd=str(__import__("pathlib").Path(__file__).resolve().parents[3]))
    assert __import__("json").loads(out.stdout) == [504, ds[0], ds[-1]]
