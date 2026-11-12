"""K2: tw_price_1m ingest never changes the daily latest close (SD §4/§6)."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from services.source_ingestion.connectors.base import SourceRecord
from services.source_ingestion.connectors.finmind_taiwan import FinMindTaiwanKBarAdapter
from services.source_ingestion.market_snapshot import LatestMarketSnapshotStore
from services.source_ingestion.pipeline import is_minute_bar_record, persist_latest_market_snapshots

DAY = "2026-11-10"


def _result(records, run_id):
    run = SimpleNamespace(
        status=SimpleNamespace(value="completed"), ingest_run_id=run_id,
        to_dict=lambda: {"finished_at": "2026-11-10T09:00:00Z"},
    )
    return SimpleNamespace(run=run, records=tuple(records))


def _daily(close, day=DAY):
    return SourceRecord(
        source_id=f"daily-{day}", connector_id="tw-twse-tpex-official-market", source_type="market",
        title="2330 daily", content_ref=f"ref://daily/{day}",
        metadata={"dataset": "tw_price_daily", "symbol": "2330", "close": close,
                  "event_time": f"{day}T00:00:00Z", "market": "TW"},
    )


def _minute_records(close_of_last=777.0):
    start = datetime.fromisoformat(f"{DAY}T09:00:00")
    rows = [{"date": DAY, "minute": (start + timedelta(minutes=i)).strftime("%H:%M:%S"), "stock_id": "2330",
             "open": 100.0, "high": 101.0, "low": 99.0, "close": close_of_last if i == 270 else 100.0,
             "volume": 10} for i in range(271)]
    return FinMindTaiwanKBarAdapter().records_from_kbar_payload(
        {"data": rows}, symbol="2330", session_date=DAY, fetched_at=f"{DAY}T08:00:00Z",
        reconciled_at=f"{DAY}T09:00:00Z", is_trading_day=lambda d: True)


def _snapshot(store):
    snap = store.get("2330")
    return None if snap is None else (snap.closes, snap.snapshot_id, snap.to_public_dict())


def test_minute_records_are_recognised_as_minute_bars():
    records = _minute_records()
    assert len(records) == 271 and all(is_minute_bar_record(r) for r in records)
    assert not is_minute_bar_record(_daily(600.0))


def test_minute_ingest_does_not_change_daily_latest_close(tmp_path):
    store = LatestMarketSnapshotStore(tmp_path / "snap.jsonl")
    out = persist_latest_market_snapshots(store, _result([_daily(600.0)], "run-daily"))
    assert out["updated_snapshot_count"] == 1
    before = _snapshot(store)
    assert before[0][-1] == 600.0

    minute = persist_latest_market_snapshots(store, _result(_minute_records(), "run-minute"))
    assert minute["accepted_record_count"] == 0 and minute["updated_snapshot_count"] == 0
    assert minute["excluded_minute_record_count"] == 271
    assert _snapshot(store) == before
    store.reload()
    assert _snapshot(store) == before  # also after a reload from the log


def test_minute_only_ingest_creates_no_daily_snapshot(tmp_path):
    store = LatestMarketSnapshotStore(tmp_path / "snap.jsonl")
    persist_latest_market_snapshots(store, _result(_minute_records(), "run-minute"))
    assert store.get("2330") is None


def test_mixed_batch_keeps_daily_and_drops_minute(tmp_path):
    store = LatestMarketSnapshotStore(tmp_path / "snap.jsonl")
    out = persist_latest_market_snapshots(store, _result([*_minute_records(), _daily(601.0)], "run-mixed"))
    assert out["excluded_minute_record_count"] == 271
    assert store.get("2330").closes[-1] == 601.0


def test_connector_declaring_minute_dataset_excludes_everything(tmp_path):
    store = LatestMarketSnapshotStore(tmp_path / "snap.jsonl")
    connector = SimpleNamespace(connector_id="other", metadata={"normalized_dataset": "tw_price_1m"}, datasets=())
    out = persist_latest_market_snapshots(store, _result([_daily(602.0)], "run-x"), connector=connector)
    assert out["updated_snapshot_count"] == 0 and store.get("2330") is None
