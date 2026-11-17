"""snapshots/latest?min_closes=N: newest 60 from the current record, the rest from daily bars."""
from __future__ import annotations

import statistics
import time
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("pyarrow")
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.source_ingestion.daily_bars_store import DailyBarsStore
from services.source_ingestion.requirement_state import LatestMarketSnapshot, MarketSnapshotPoint
from services.source_ingestion.routers.ingest_operations import create_ingest_operations_router
from services.source_ingestion.tests.test_daily_bars_store import make_rows
from services.source_ingestion.tests.test_daily_bars_reader import trading_days


def _point(d: str, close: float) -> MarketSnapshotPoint:
    return MarketSnapshotPoint(event_time=f"{d}T00:00:00+08:00", close=close, source_id="s", connector_id="c",
                               content_ref=f"r-{d}", ingest_run_id="run", market="TW")


class _Store:
    def __init__(self, snaps): self.snaps, self.gets = snaps, 0
    def get(self, symbol, dataset):
        self.gets += 1
        return self.snaps.get(symbol)


def rows_for(symbols, d, i):
    rows = make_rows(symbols, trade_date=d, bump=float(i))
    for r in rows:  # the stored exchange identity is X.TWSE in both stores
        r["symbol"] = r["symbol"].replace(".TW", ".TWSE")
    return rows


def build(tmp_path, days=520, symbols=60, skip: str | None = None):
    store = DailyBarsStore(tmp_path)
    ds = trading_days(days)
    for i, d in enumerate(ds):
        rows = rows_for(symbols, d, i)
        if d == skip:  # one symbol has no bar on a stored trading day (suspension): a calendar gap for it
            rows = [r for r in rows if r["symbol"] != "1001.TWSE"]
        store.write_day(d, rows, ingest_run_id=f"r-{d}")
    snaps = {}
    for s in range(symbols):
        sym = f"{1000 + s}.TWSE"
        snaps[sym] = LatestMarketSnapshot(
            symbol=sym, observed_at="2026-11-13T07:00:00Z",
            points=tuple(_point(d, 10.5 + (s % 50) + i) for i, d in enumerate(ds) if i >= days - 60))
    return ds, snaps


def client(tmp_path, snaps):
    runtime = SimpleNamespace(MARKET_DATA_STORAGE_ROOT=tmp_path, latest_market_snapshot_store=_Store(snaps),
                              snapshot_universe=None)
    app = FastAPI()
    app.include_router(create_ingest_operations_router(runtime))
    return TestClient(app), runtime


def tree(root):
    return sorted((str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file())


def test_requested_length_exact_and_equals_direct_read(tmp_path):
    ds, snaps = build(tmp_path)
    c, _ = client(tmp_path, snaps)
    for n in (120, 504):
        body = c.get("/api/source-ingest/snapshots/latest", params={"symbol": "1005.TWSE", "min_closes": n}).json()
        assert len(body["closes"]) == n and body["window_source"] == {"snapshot": 60, "daily_bars": n - 60}
        from services.source_ingestion.daily_bars_reader import DailyBarsReader
        direct = [r["close"] for r in DailyBarsReader(tmp_path).read_close_window("1005.TWSE", ds[-1], n)]
        assert body["closes"] == direct
        assert {"schema_version", "snapshot_id", "symbol", "event_time", "observed_at", "lineage", "source_ref"} <= set(body)


def test_60_or_less_or_absent_is_today_and_opens_no_partition(tmp_path, monkeypatch):
    ds, snaps = build(tmp_path, days=70, symbols=5)
    c, _ = client(tmp_path, snaps)
    from services.source_ingestion import daily_bars_reader as m
    opened = []
    monkeypatch.setattr(m.DailyBarsReader, "_read_one_date", lambda self, *a: opened.append(a) or [])
    monkeypatch.setattr(m.DailyBarsReader, "trade_dates", lambda self: opened.append("cal") or [])
    base = c.get("/api/source-ingest/snapshots/latest", params={"symbol": "1001.TWSE"}).json()
    for n in (1, 60):
        assert c.get("/api/source-ingest/snapshots/latest", params={"symbol": "1001.TWSE", "min_closes": n}).json() == base
    assert opened == [] and len(base["closes"]) == 60 and "window_source" not in base


def test_bounds_422(tmp_path):
    _, snaps = build(tmp_path, days=70, symbols=2)
    c, _ = client(tmp_path, snaps)
    for n in (0, 505, "x"):
        assert c.get("/api/source-ingest/snapshots/latest", params={"symbol": "1001.TWSE", "min_closes": n}).status_code == 422


def test_history_insufficient_never_short_success(tmp_path):
    ds, snaps = build(tmp_path, days=100, symbols=3)
    c, _ = client(tmp_path, snaps)
    body = c.get("/api/source-ingest/snapshots/latest", params={"symbol": "1001.TWSE", "min_closes": 200}).json()
    assert body["closes"] == [] and body["reason"] == "history_insufficient"
    assert body["requested"] == 200 and body["available"] == 100 and body["first_available_date"] == ds[0]


def test_calendar_gap_reported_not_filled(tmp_path):
    ds = trading_days(100)
    _, snaps = build(tmp_path, days=100, symbols=3, skip=ds[20])
    c, _ = client(tmp_path, snaps)
    body = c.get("/api/source-ingest/snapshots/latest", params={"symbol": "1001.TWSE", "min_closes": 90}).json()
    assert body["closes"] == [] and body["reason"] == "history_insufficient"
    assert "contiguous" in body["detail"] or "stored" in body["detail"]


def test_read_writes_no_file(tmp_path):
    _, snaps = build(tmp_path, days=80, symbols=3)
    c, _ = client(tmp_path, snaps)
    before = tree(tmp_path)
    c.get("/api/source-ingest/snapshots/latest", params={"symbol": "1001.TWSE", "min_closes": 75})
    assert tree(tmp_path) == before


def test_latency_hosted_shape_50_symbols_504(tmp_path):
    ds, snaps = build(tmp_path, days=520, symbols=500)
    c, _ = client(tmp_path, snaps)
    syms = [f"{1000 + i}.TWSE" for i in range(50)]
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        for s in syms:
            assert len(c.get("/api/source-ingest/snapshots/latest", params={"symbol": s, "min_closes": 504}).json()["closes"]) == 504
        times.append((time.perf_counter() - t0) / len(syms))
    per_call = sorted(times)
    print("min_closes=504 per call seconds", per_call)
    assert max(per_call) <= 2.0
    t60 = []
    for s in syms:
        t0 = time.perf_counter(); c.get("/api/source-ingest/snapshots/latest", params={"symbol": s}); t60.append(time.perf_counter() - t0)
    assert statistics.quantiles(t60, n=20)[-1] <= 0.1


def _route_urlopen(c, monkeypatch, strip_min_closes=False):
    """Route the producer's urllib call to the real endpoint; strip_min_closes models the previous endpoint."""
    import io, urllib.parse

    def fake(req, timeout=0):
        parts = urllib.parse.urlsplit(req.full_url)
        q = dict(urllib.parse.parse_qsl(parts.query))
        if strip_min_closes:
            q.pop("min_closes", None)
        r = c.get(parts.path, params=q)
        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return Resp(r.content)
    monkeypatch.setattr("urllib.request.urlopen", fake)


def test_producer_against_real_endpoint_before_and_after(tmp_path, monkeypatch):
    from services.execution.lean_runtime.paper_signal_producer import SignalDecisionUnavailable, _market_input_for_binding

    ds, snaps = build(tmp_path, days=520, symbols=3)
    c, _ = client(tmp_path, snaps)
    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_URL", "http://source")
    now = "2026-11-13T07:00:00Z"
    binding = {"binding_id": "rb-120", "symbol": "1001.TWSE",
               "market_data_policy": {"owner": "source-ingest", "contract": "latest_stored_normalized",
                                      "max_age_seconds": 10 ** 9, "minimum_closes": 120}}
    # previous endpoint (ignores min_closes): today's reproduced refusal, 120 required got 60
    _route_urlopen(c, monkeypatch, strip_min_closes=True)
    with pytest.raises(SignalDecisionUnavailable) as err:
        _market_input_for_binding(binding, {}, now_iso=now)
    assert "requires 120 closes, got 60" in str(err.value) and getattr(err.value, "code", "market_input_insufficient") == "market_input_insufficient"
    # new endpoint: 120 and 504 closes reach the decision, equal to a direct daily bars read
    _route_urlopen(c, monkeypatch)
    from services.execution import market_snapshot_admission as msa
    monkeypatch.setattr("services.execution.lean_runtime.paper_signal_producer.admit_market_snapshot",
                        lambda snapshot, **_: msa.admitted("x", "2026-11-13T05:30:00Z", 1.0))  # admission is not under test
    from services.source_ingestion.daily_bars_reader import DailyBarsReader
    for n in (120, 504):
        binding["market_data_policy"]["minimum_closes"] = n
        got = _market_input_for_binding(binding, {}, now_iso=now)
        direct = [r["close"] for r in DailyBarsReader(tmp_path).read_close_window("1001.TWSE", ds[-1], n)]
        assert [float(x) for x in got["closes"]] == direct and len(direct) == n
