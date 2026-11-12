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


def _store(tmp_path):
    return LatestMarketSnapshotStore(tmp_path / "snap.jsonl")


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
    st = _store(tmp_path)
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
