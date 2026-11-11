"""K1: FinMind TaiwanStockKBar connector contract (SD_INTRADAY_MINUTE_BARS §3)."""

from __future__ import annotations

import urllib.error
from datetime import date

import pytest

from services.source_ingestion.connector_definitions import DEPLOYED_CONNECTOR_DEFINITIONS
from services.source_ingestion.connectors import finmind_taiwan as fm
from services.source_ingestion.connectors.finmind_taiwan import (
    FINMIND_TAIWAN_DATASETS,
    FinMindCredentialError,
    FinMindLiveFetcher,
    FinMindTaiwanKBarAdapter,
    KBarHourlyBudget,
    classify_range_probe,
    fetch_kbar,
    measure_volume_unit,
)

DAY = "2026-11-10"


def _row(minute, o=100.0, h=101.0, l=99.0, c=100.5, v=10, sid="2330", d=DAY):
    return {"date": d, "minute": minute, "stock_id": sid, "open": o, "high": h, "low": l, "close": c, "volume": v}


def test_dataset_row_maps_to_tw_price_1m_only():
    row = next(d for d in FINMIND_TAIWAN_DATASETS if d["dataset"] == "TaiwanStockKBar")
    assert row["normalized_dataset"] == "tw_price_1m"
    assert row["cadence"] == "daily_after_close"
    # daily truth is untouched
    assert all(d["normalized_dataset"] != "tw_price_daily" for d in FINMIND_TAIWAN_DATASETS)


def test_connector_definition_registered_for_tw_price_1m():
    d = next(x for x in DEPLOYED_CONNECTOR_DEFINITIONS if x.definition_id == "tw-finmind-kbar-1m")
    assert tuple(d.datasets) == ("tw_price_1m",)
    assert "tw_price_daily" not in d.datasets
    assert tuple(d.allowed_host_patterns) == ("api.finmindtrade.com",)


def test_normalization_keeps_raw_hash_and_session_filter():
    rows = [_row("08:45:00"), _row("09:00:00"), _row("09:01:00"), _row("13:30:00"), _row("14:00:00")]
    res = FinMindTaiwanKBarAdapter().normalize_kbar_payload(
        {"data": rows}, symbol="2330", session_date=DAY, fetched_at="2026-11-10T08:30:00Z",
        quota_meta={"ratelimit_remaining": 5000, "request_url": "https://x?token=secret"},
    )
    assert res.kept_bars == 3 and res.dropped_outside_session == 2
    rec = res.records[0]
    m = rec.metadata
    assert m["dataset"] == "tw_price_1m" and m["source"] == "finmind" and m["source_dataset"] == "TaiwanStockKBar"
    assert m["raw_row"]["minute"] == "09:00:00" and m["source_hash"] and m["request_id"]
    assert m["quota_meta"] == {"ratelimit_remaining": 5000}  # url stripped
    n = m["normalized_row"]
    assert n["bar_start"] == "2026-11-10T09:00:00+08:00" and n["bar_end"] == "2026-11-10T09:01:00+08:00"
    for k in ("provider_published_at", "fetched_at", "reconciled_at", "bar_availability_ts"):
        assert k in n


def test_conflicting_duplicate_flagged_identical_ignored():
    rows = [_row("09:00:00"), _row("09:00:00"), _row("09:01:00"), _row("09:01:00", c=105.0)]
    res = FinMindTaiwanKBarAdapter().normalize_kbar_payload(
        rows, symbol="2330", session_date=DAY, fetched_at="2026-11-10T08:30:00Z")
    assert res.identical_duplicates == 1
    assert res.conflicting_duplicates == ("2026-11-10T09:01:00+08:00",)


def test_non_trading_day_rejected():
    res = FinMindTaiwanKBarAdapter().normalize_kbar_payload(
        [_row("09:00:00")], symbol="2330", session_date=DAY, fetched_at="2026-11-10T08:30:00Z",
        is_trading_day=lambda d: d != date(2026, 11, 10))
    assert res.rejected_non_trading_day and res.records == ()


def test_malformed_and_foreign_rows_dropped():
    rows = [_row("09:00:00"), _row("09:01:00", sid="2317"), {"date": DAY, "minute": "bad", "stock_id": "2330"},
            _row("09:02:00", v=None)]
    res = FinMindTaiwanKBarAdapter().normalize_kbar_payload(
        rows, symbol="2330", session_date=DAY, fetched_at="2026-11-10T08:30:00Z")
    assert res.kept_bars == 1 and res.dropped_malformed == 3


def test_credential_missing_raises_no_placeholder(monkeypatch):
    monkeypatch.delenv("FINMIND_API_TOKEN", raising=False)
    with pytest.raises(FinMindCredentialError):
        FinMindLiveFetcher().fetch_kbar("2330", DAY)


def test_fetch_kbar_request_shape_and_fetched_at(monkeypatch):
    seen = {}

    def fake_fetch_dataset(self, dataset, **kw):
        seen.update(dataset=dataset, **kw)
        return {"status": 200, "data": []}, {"http_status": 200}

    monkeypatch.setattr(FinMindLiveFetcher, "fetch_dataset", fake_fetch_dataset)
    _, meta = FinMindLiveFetcher().fetch_kbar("2330", DAY)
    assert seen == {"dataset": "TaiwanStockKBar", "symbol": "2330", "start_date": DAY, "end_date": None}
    assert meta["fetched_at"].endswith("Z")


class _Clock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t


def test_budget_caps_at_limit_and_resets_next_hour():
    clk = _Clock(3600 * 100 + 10)
    b = KBarHourlyBudget(3, clock=clk)
    assert [b.acquire() for _ in range(4)] == [True, True, True, False]
    assert b.seconds_until_next_window() <= 3600
    clk.t += 3600
    assert b.acquire()
    assert KBarHourlyBudget().limit == 4500


def test_402_stops_until_next_hour_without_retry(monkeypatch):
    calls = []

    def boom(self, symbol, date, **kw):
        calls.append(symbol)
        raise fm.FinMindQuotaError("402")

    monkeypatch.setattr(FinMindLiveFetcher, "fetch_kbar", boom)
    clk = _Clock(3600 * 7 + 5)
    b = KBarHourlyBudget(clock=clk)
    f = FinMindLiveFetcher()
    r1 = fetch_kbar(f, "2330", DAY, budget=b)
    r2 = fetch_kbar(f, "2317", DAY, budget=b)
    assert r1.status == "quota_402" and r2.status == "budget_exhausted"
    assert calls == ["2330"]  # no second network call in the blocked hour
    clk.t += 3600
    assert b.acquire()


def test_volume_unit_probe_detects_shares_vs_lots():
    shares = measure_volume_unit([{"volume": 600_000}, {"volume": 400_000}], 1_000_000)
    lots = measure_volume_unit([{"volume": 600}, {"volume": 400}], 1_000_000)
    off = measure_volume_unit([{"volume": 5}], 1_000_000)
    assert shares["unit_factor"] == 1 and lots["unit_factor"] == 1000 and off["unit_factor"] is None


def test_range_probe_classification():
    one = [_row("09:00:00")]
    multi = [_row("09:00:00", d="2026-11-09"), _row("09:00:00", d="2026-11-10")]
    assert classify_range_probe(one, multi, start_date="2026-11-09", end_date="2026-11-10")["range_supported"]
    assert not classify_range_probe(one, one, start_date="2026-11-09", end_date="2026-11-10")["range_supported"]
    assert not classify_range_probe(one, None, start_date="a", end_date="b", range_error="HTTP 400")["range_supported"]


def test_provider_path_fetches_through_shared_budget(monkeypatch):
    from services.source_ingestion import provider_adapters as pa

    monkeypatch.setenv("FINMIND_API_TOKEN", "x")
    budget = KBarHourlyBudget(1)
    monkeypatch.setattr(pa, "_finmind_shared_kbar_budget", lambda: budget)
    calls = []

    def fake(self, symbol, date, **kw):
        calls.append(symbol)
        return {"status": 200, "data": [_row("09:00:00")]}, {"fetched_at": "2026-11-10T08:00:00Z"}

    monkeypatch.setattr(FinMindLiveFetcher, "fetch_kbar", fake)
    req = {"symbol": "2330", "date": DAY}
    pa._finmind_kbar(FinMindTaiwanKBarAdapter(), req, "t")
    assert calls == ["2330"]
    with pytest.raises(pa.SourceEvidenceError):
        pa._finmind_kbar(FinMindTaiwanKBarAdapter(), req, "t")
    assert calls == ["2330"]  # budget spent: no second network call


def test_provider_path_missing_credential_no_network(monkeypatch):
    from services.source_ingestion import provider_adapters as pa

    monkeypatch.delenv("FINMIND_API_TOKEN", raising=False)
    with pytest.raises(FinMindCredentialError):
        pa._finmind_kbar(FinMindTaiwanKBarAdapter(), {"symbol": "2330", "date": DAY}, "t")
