"""K4: latest-snapshot store is isolated by (symbol, dataset/frequency); default stays 1d."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from services.source_ingestion.requirement_state import (
    LatestMarketSnapshotStore,
    MarketSnapshotStateError,
    snapshot_dataset_for_frequency,
)


def _rec(symbol, event_time, close, *, dataset=None, cid="c1"):
    row = {"symbol": symbol, "close": close, "event_time": event_time}
    meta = {"normalized_row": row}
    if dataset:
        row["dataset"] = dataset
        meta["dataset"] = dataset
    return SimpleNamespace(source_id=f"s-{event_time}", connector_id=cid, content_ref=f"r://{symbol}/{event_time}",
                           metadata=meta, is_rejected=False)


DAILY = [_rec("2330.TWSE", "2026-11-09T06:30:00Z", 100.0), _rec("2330.TWSE", "2026-11-10T06:30:00Z", 101.0)]
MINUTE = [_rec("2330.TWSE", f"2026-11-10T01:0{i}:00Z", 500.0 + i, dataset="tw_price_1m") for i in range(1, 4)]


def _store(tmp_path, **kwargs):
    return LatestMarketSnapshotStore(tmp_path / "snap.jsonl", **kwargs)


def test_same_symbol_two_datasets_do_not_overwrite_each_other(tmp_path):
    st = _store(tmp_path)
    st.append_normalized_records(DAILY, ingest_run_id="d1", observed_at="2026-11-10T07:00:00Z")
    st.append_normalized_records(MINUTE, ingest_run_id="m1", observed_at="2026-11-10T08:00:00Z", dataset="tw_price_1m")
    assert st.get("2330.TWSE").closes == (100.0, 101.0)  # default = daily
    assert st.get("2330.TWSE", "tw_price_1m").closes == (501.0, 502.0, 503.0)
    assert st.get("2330.TWSE", "tw_price_5m") is None
    fresh = _store(tmp_path)  # durable, reloaded from the checksummed log
    assert fresh.get("2330.TWSE").closes == (100.0, 101.0)
    assert fresh.get("2330.TWSE", "tw_price_1m").closes == (501.0, 502.0, 503.0)


def test_point_from_record_rejects_other_dataset(tmp_path):
    st = _store(tmp_path)
    # a minute record offered to the default (daily) projection is rejected
    out = st.append_normalized_records(MINUTE, ingest_run_id="x", observed_at="2026-11-10T08:00:00Z")
    assert out["accepted_record_count"] == 0 and st.get("2330.TWSE") is None
    # an undeclared (daily-looking) record offered to the minute projection is rejected
    out = st.append_normalized_records(DAILY, ingest_run_id="y", observed_at="2026-11-10T08:00:00Z", dataset="tw_price_1m")
    assert out["accepted_record_count"] == 0 and st.get("2330.TWSE", "tw_price_1m") is None
    # a record declaring another intraday dataset is rejected by the minute projection
    other = [_rec("2330.TWSE", "2026-11-10T01:01:00Z", 1.0, dataset="tw_price_5m")]
    assert st.append_normalized_records(other, ingest_run_id="z", observed_at="2026-11-10T08:00:00Z",
                                        dataset="tw_price_1m")["accepted_record_count"] == 0


def test_daily_state_is_byte_identical_to_pre_k4_layout(tmp_path):
    st = _store(tmp_path, store_mode="journal")  # append order is a journal property; sqlite twin below
    out = st.append_normalized_records(DAILY, ingest_run_id="d1", observed_at="2026-11-10T07:00:00Z")
    line = (tmp_path / "snap.jsonl").read_text()
    assert '"dataset"' not in line
    assert "dataset" not in out["snapshots"][0]
    before = line
    st.append_normalized_records(MINUTE, ingest_run_id="m1", observed_at="2026-11-10T08:00:00Z", dataset="tw_price_1m")
    assert (tmp_path / "snap.jsonl").read_text().startswith(before)  # daily line untouched, minute appended


def test_unknown_dataset_and_frequency_fail_closed(tmp_path):
    st = _store(tmp_path)
    with pytest.raises(MarketSnapshotStateError):
        st.get("2330.TWSE", "tw_price_weekly")
    with pytest.raises(MarketSnapshotStateError):
        st.append_normalized_records(DAILY, ingest_run_id="d", dataset="bogus")
    assert snapshot_dataset_for_frequency("1d") == "tw_price_daily"
    assert snapshot_dataset_for_frequency("1m") == "tw_price_1m"
    for bad in ("2m", "1D", "daily", "", None, "1h"):
        with pytest.raises(MarketSnapshotStateError):
            snapshot_dataset_for_frequency(bad)


# --- SOURCE-SNAPSHOT-CURRENT-RECORD-20261117: one current record, derived frequencies never stored ---

DERIVED = ("tw_price_5m", "tw_price_15m", "tw_price_30m", "tw_price_60m")


@pytest.mark.parametrize("mode", ["journal", "dual", "sqlite"])
@pytest.mark.parametrize("dataset", DERIVED)
def test_derived_frequency_writes_are_refused_in_every_mode(tmp_path, mode, dataset):
    st = _store(tmp_path, store_mode=mode)
    rows = [_rec("2330.TWSE", "2026-11-10T01:01:00Z", 1.0, dataset=dataset)]
    with pytest.raises(MarketSnapshotStateError, match="not_stored_derive_on_read"):
        st.append_normalized_records(rows, ingest_run_id="r", observed_at="2026-11-10T08:00:00Z", dataset=dataset)
    assert st.get("2330.TWSE", dataset) is None
    assert st.live_symbols(dataset) == []


@pytest.mark.parametrize("frequency", ["5m", "15m", "30m", "60m"])
def test_endpoint_answers_not_stored_derive_on_read(tmp_path, frequency):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from services.source_ingestion.routers.ingest_operations import create_ingest_operations_router

    st = _store(tmp_path, store_mode="sqlite")
    st.append_normalized_records(MINUTE, ingest_run_id="m1", observed_at="2026-11-10T08:00:00Z", dataset="tw_price_1m")
    app = FastAPI()
    app.include_router(create_ingest_operations_router(SimpleNamespace(latest_market_snapshot_store=st)))
    client = TestClient(app)
    answer = client.get("/api/source-ingest/snapshots/latest", params={"symbol": "2330.TWSE", "frequency": frequency})
    assert answer.status_code == 404
    assert answer.json()["detail"]["code"] == "not_stored_derive_on_read"
    assert answer.json()["detail"]["derive_from"] == "tw_price_1m"
    stored = client.get("/api/source-ingest/snapshots/latest", params={"symbol": "2330.TWSE", "frequency": "1m"})
    assert stored.status_code == 200 and stored.json()["closes"] == [501.0, 502.0, 503.0]


def test_sqlite_isolates_datasets_and_replaces_the_minute_record_in_place(tmp_path):
    st = _store(tmp_path, store_mode="sqlite")
    st.append_normalized_records(DAILY, ingest_run_id="d1", observed_at="2026-11-10T07:00:00Z")
    st.append_normalized_records(MINUTE, ingest_run_id="m1", observed_at="2026-11-10T08:00:00Z", dataset="tw_price_1m")
    assert st.snapshot_current_stats()["record_count"] == 2
    newer = [_rec("2330.TWSE", "2026-11-10T01:04:00Z", 504.0, dataset="tw_price_1m")]
    st.append_normalized_records(newer, ingest_run_id="m2", observed_at="2026-11-10T09:00:00Z", dataset="tw_price_1m")
    assert st.snapshot_current_stats()["record_count"] == 2  # replaced, not appended
    assert st.get("2330.TWSE", "tw_price_1m").closes == (501.0, 502.0, 503.0, 504.0)
    assert st.get("2330.TWSE").closes == (100.0, 101.0)
    assert _store(tmp_path, store_mode="sqlite").get("2330.TWSE", "tw_price_1m").closes == (501.0, 502.0, 503.0, 504.0)


def test_sqlite_export_keeps_the_pre_k4_daily_line_bytes(tmp_path):
    journal = _store(tmp_path / "j", store_mode="journal")
    journal.append_normalized_records(DAILY, ingest_run_id="d1", observed_at="2026-11-10T07:00:00Z")
    sql = _store(tmp_path / "s", store_mode="sqlite")
    sql.append_normalized_records(DAILY, ingest_run_id="d1", observed_at="2026-11-10T07:00:00Z")
    assert (tmp_path / "s" / "snap.jsonl").read_text() == (tmp_path / "j" / "snap.jsonl").read_text()
    assert '"dataset"' not in (tmp_path / "s" / "snap.jsonl").read_text()
