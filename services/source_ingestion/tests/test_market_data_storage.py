"""Dual write of tw_price_daily into daily_bars by MarketDataStorageWriter."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("pyarrow")

from services.source_ingestion.connectors import SourceConnector, SourceRecord
from services.source_ingestion.daily_bars_reader import DailyBarsReader
from services.source_ingestion.market_data_storage import MarketDataStorageWriter


def _connector():
    return SourceConnector(connector_id="twse-daily", source_type="market", provider="t", license_scope="internal",
                           metadata={"market": "TW"})


def _result(records, run_id="run-1"):
    return SimpleNamespace(records=records, run=SimpleNamespace(ingest_run_id=run_id))


def _rec(symbol, date, close=10.0, dataset="tw_price_daily", with_date=True):
    meta = {"symbol": symbol, "close": close, "dataset": dataset}
    if with_date:
        meta["event_time"] = f"{date}T05:30:00Z"
    return SourceRecord(source_id=f"s-{symbol}-{date}", connector_id="twse-daily", source_type="market",
                        title="t", content_ref=f"r-{symbol}", metadata=meta)


def test_write_run_dual_writes_and_keeps_current_stores(tmp_path):
    writer = MarketDataStorageWriter(tmp_path)
    manifest = writer.write_run(
        result=_result([_rec("2330.TW", "2026-11-13", 900.0), _rec("2317.TW", "2026-11-13"),
                        _rec("2330.TW", "2026-11-13", dataset="tw_index_daily")]),
        connector=_connector()).to_dict()
    # current behaviour unchanged: raw + normalized partitions per dataset, refs unchanged in shape
    assert (tmp_path / "raw" / "twse-daily" / "tw_price_daily" / "date=2026-11-13" / "run-1.jsonl").is_file()
    assert (tmp_path / "normalized" / "tw_price_daily" / "date=2026-11-13" / "run-1.jsonl").is_file()
    assert set(manifest) == {"schema_version", "ingest_run_id", "created_at", "raw_refs", "normalized_refs",
                             "feature_refs", "summary"}
    # and the daily bars store also holds exactly the two daily rows (other dataset not included)
    got = DailyBarsReader(tmp_path).read_range(["2330.TW", "2317.TW"], "2026-11-13", "2026-11-13")
    assert [len(v) for v in got.values()] == [1, 1] and got["2330.TW"][0]["close"] == 900.0
    # a re-run with another run id and the same content writes nothing new to daily_bars
    writer.write_run(result=_result([_rec("2330.TW", "2026-11-13", 900.0), _rec("2317.TW", "2026-11-13")], "run-2"),
                     connector=_connector())
    assert writer.daily_bars_store.counters["days_unchanged"] == 1


def test_record_without_date_is_not_filed_in_daily_bars(tmp_path):
    writer = MarketDataStorageWriter(tmp_path)
    writer.write_run(result=_result([_rec("2330.TW", "x", with_date=False)]), connector=_connector())
    assert writer.daily_bars_store.counters["rejected_no_trade_date"] == 1
    assert DailyBarsReader(tmp_path).trade_dates() == []


def test_dual_write_failure_never_breaks_the_current_write(tmp_path):
    writer = MarketDataStorageWriter(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("disk full")

    writer.daily_bars_store.write_records = boom  # type: ignore[assignment]
    writer.write_run(result=_result([_rec("2330.TW", "2026-11-13")]), connector=_connector())
    assert (tmp_path / "normalized" / "tw_price_daily" / "date=2026-11-13" / "run-1.jsonl").is_file()
    health = writer.daily_bars_store.health()
    assert health["counters"]["dual_write_errors"] == 1 and "disk full" in health["last_error"]


def test_health_payload_carries_daily_bars_alarm_fields(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from services.source_ingestion.routers.observability import create_observability_router

    writer = MarketDataStorageWriter(tmp_path)
    writer.write_run(result=_result([_rec("2330.TW", "2026-11-13")]), connector=_connector())
    runtime = SimpleNamespace(
        market_data_storage_writer=writer,
        source_health_store=SimpleNamespace(list=lambda source_kind=None: []),
    )
    app = FastAPI()
    app.include_router(create_observability_router(runtime))
    body = TestClient(app).get("/api/source-ingest/health").json()
    daily = body["daily_bars"]
    assert daily["alarms"] == [] and daily["counters"]["rows_written"] == 1
    assert {"daily_bars_total_bytes", "daily_bars_max_day_bytes", "lineage_max_day_bytes"} <= set(daily["budget"])
