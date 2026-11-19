"""GET /api/source-ingest/history/availability: first date, last date and count per symbol."""
from __future__ import annotations

import statistics
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("pyarrow")
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.source_ingestion.daily_bars_store import DailyBarsStore
from services.source_ingestion.routers.ingest_operations import create_ingest_operations_router
from services.source_ingestion.tests import wall_clock_bounds
from services.source_ingestion.tests.daily_bars_hosted_shape import (
    HOSTED_SYMBOLS, HOSTED_WARRANTS, build_hosted_shaped,
)
from services.source_ingestion.tests.test_daily_bars_reader import trading_days
from services.source_ingestion.tests.test_daily_bars_store import make_rows

URL = "/api/source-ingest/history/availability"


def _client(root):
    runtime = SimpleNamespace(MARKET_DATA_STORAGE_ROOT=root)
    app = FastAPI()
    app.include_router(create_ingest_operations_router(runtime))
    return TestClient(app)


def _tree(root):
    return sorted((str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file())


def _build(root, days, symbols, warrants=0, late=None, skip=None):
    store = DailyBarsStore(root)
    ds = trading_days(days)
    for i, d in enumerate(ds):
        rows = make_rows(symbols, trade_date=d, warrants=warrants)
        if late and i < late[1]:  # the symbol only exists from day index late[1]
            rows = [r for r in rows if r["symbol"] != late[0]]
        if skip and d == skip[1]:
            rows = [r for r in rows if r["symbol"] != skip[0]]
        store.write_day(d, rows, ingest_run_id=f"r-{d}")
    return ds


def test_first_last_count_alias_and_missing(tmp_path):
    ds = _build(tmp_path, 30, 20, late=("1003.TW", 10), skip=("1004.TW", None))
    c = _client(tmp_path)
    body = c.get(URL, params={"symbols": "1001,1003.TWSE,9999.TW"}).json()["symbols"]
    assert body["1001"]["count"] == 30 and body["1001"]["first_date"] == ds[0] and body["1001"]["last_date"] == ds[-1]
    assert body["1001"]["stored_symbol"] == "1001.TW"
    assert body["1003.TWSE"]["count"] == 20 and body["1003.TWSE"]["first_date"] == ds[10]
    assert body["9999.TW"] == {"first_date": None, "last_date": None, "count": 0}


def test_suspension_day_is_a_missing_close_not_filled(tmp_path):
    ds = trading_days(30)
    _build(tmp_path, 30, 5, skip=("1002.TW", ds[12]))
    body = _client(tmp_path).get(URL, params={"symbols": "1002.TW"}).json()["symbols"]["1002.TW"]
    assert body["count"] == 29 and body["first_date"] == ds[0] and body["last_date"] == ds[-1]


def test_symbol_cap_and_empty_fail_closed(tmp_path):
    _build(tmp_path, 3, 5)
    c = _client(tmp_path)
    many = ",".join(f"{1000 + i}.TW" for i in range(101))
    r = c.get(URL, params={"symbols": many})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "too_many_symbols"
    assert c.get(URL, params={"symbols": ",,"}).status_code == 422
    assert c.get(URL).status_code == 422


def test_empty_store_reports_zero_and_writes_nothing(tmp_path):
    body = _client(tmp_path).get(URL, params={"symbols": "1001.TW"}).json()["symbols"]["1001.TW"]
    assert body["count"] == 0
    assert _tree(tmp_path) == []


def test_read_only_and_repeat_opens_no_file(tmp_path, monkeypatch):
    _build(tmp_path, 12, 30)
    c = _client(tmp_path)
    before = _tree(tmp_path)
    first = c.get(URL, params={"symbols": "1001.TW,1002.TW"}).json()
    from services.source_ingestion import daily_bars_reader as m
    monkeypatch.setattr(m.DailyBarsReader, "_present_one_date",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("opened a partition")))
    assert c.get(URL, params={"symbols": "1001.TW,1002.TW"}).json() == first
    assert _tree(tmp_path) == before  # no stored data is added


def test_hosted_shape_builder_matches_write_day(tmp_path):
    """One full hosted-shaped day built by the cheap builder equals the same day written by write_day."""
    d0, d1 = trading_days(2)
    fast = tmp_path / "fast"
    build_hosted_shaped(fast, [d0, d1])
    real = tmp_path / "real"
    DailyBarsStore(real).write_day(d1, make_rows(HOSTED_SYMBOLS, trade_date=d1, warrants=HOSTED_WARRANTS),
                                   ingest_run_id=f"r-{d1}")
    want = {p.relative_to(real).as_posix(): p.read_bytes() for p in real.rglob("*") if p.is_file() and p.name != ".lock"}
    got = {p.relative_to(fast).as_posix(): p.read_bytes() for p in fast.rglob("*")
           if p.is_file() and p.name != ".lock" and d0 not in p.name and f"date={d0}" not in p.as_posix()}
    assert want.keys() == got.keys() and want == got  # same layout, parts, manifest (row count, digest) and lineage
    manifest = DailyBarsStore(fast).read_manifest(d1)
    assert manifest["row_count"] == HOSTED_SYMBOLS and len(manifest["files"]) > 1


def test_p95_50_symbols_on_hosted_shaped_data_and_no_stored_bytes(tmp_path, monkeypatch):
    """52,958 symbols per day (50,883 warrants: design of record as amended 2026-11-17), 500 trading days, 50 symbols, warm manifests."""
    ds = trading_days(500)
    build_hosted_shaped(tmp_path, ds)
    c = _client(tmp_path)
    before = _tree(tmp_path)
    symbols = ",".join(f"{1000 + i * 7}.TW" for i in range(50))
    cold = time.perf_counter()
    first = c.get(URL, params={"symbols": symbols}).json()["symbols"]
    cold_ms = (time.perf_counter() - cold) * 1000
    assert all(v["count"] == 500 and v["first_date"] == ds[0] for v in first.values())
    reads = wall_clock_bounds.count_parquet_reads(monkeypatch)
    timings = []
    for _ in range(40):
        t = time.perf_counter()
        assert c.get(URL, params={"symbols": symbols}).status_code == 200
        timings.append((time.perf_counter() - t) * 1000)
    timings.sort()
    p95 = timings[int(len(timings) * 0.95) - 1]
    print(f"cold_ms={cold_ms:.0f} p95_ms={p95:.1f} median_ms={statistics.median(timings):.1f}")
    # Counter that does not depend on host speed: a warm request is answered from the per-date presence cache and
    # opens no file and decodes no row group (the cold request above paid for all of them).
    assert reads == {"opens": 0, "row_groups": 0}, reads
    wall_clock_bounds.check("history_availability.warm_p95", p95, 100, unit="ms")
    assert _tree(tmp_path) == before
