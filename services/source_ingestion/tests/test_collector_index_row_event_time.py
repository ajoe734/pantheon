"""Index / breadth / flow rows with a conventional close label are bounded like daily prices.

Rows are built in the recorded collector shape (trade date plus a fetch time and no stated
publish time). Hosted failure 2026-11-17: collector-tw-twse-taiex-history raised
"available_time must be >= event_time" for a row fetched before its 14:30 label.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from services.source_ingestion.connectors import base, collector_bundle as cb
from services.source_ingestion.external_sources import validate_external_source_record
from services.source_ingestion.tests.test_collector_bundle_connector import FakeTransport

SOURCE = "collector-tw-twse-taiex-history"
DAY = "2026-11-17"
CLOSE = f"{DAY}T05:30:00Z"  # 13:30 Taipei

BREADTH = {g: {k: 1 for k in cb.BREADTH_COUNT_KEYS} for g in cb.BREADTH_GROUPS}
DATASETS = {
    "tw_index_daily": {"index_name": "發行量加權股價指數", "close": 42711.92},
    "tw_index_ohlc_daily": {"index_name": "發行量加權股價指數", "close": 42711.92, "open": 1.0, "high": 2.0, "low": 0.5},
    "tw_market_breadth_daily": BREADTH,
    "tw_institutional_flow": {"symbol": "2330", "security_name": "TSMC", "foreign_net_shares": 1,
                              "investment_trust_net_shares": 1, "dealer_net_shares": 1,
                              "total_institutional_net_shares": 3},
}
LABEL = {"tw_index_daily": "06:30:00", "tw_index_ohlc_daily": "06:30:00",
         "tw_market_breadth_daily": "06:30:00", "tw_institutional_flow": "08:30:00"}


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    cb._METADATA_CACHE.clear()
    monkeypatch.setattr(base, "_utc_now", lambda: datetime(2026, 11, 18, tzinfo=timezone.utc))
    real = cb.observed_from_collector_status
    monkeypatch.setattr(cb, "observed_from_collector_status",
                        lambda *a, **k: real(*a, **{**k, "now": k.get("now") or base._utc_now()}))
    yield
    cb._METADATA_CACHE.clear()


def make(dataset, rows, run="run-1", finished=None):
    """rows: list of (item_id, trade_date, fetched_at)."""
    finished = finished or max(f for _, _, f in rows)
    base_path = f"bundles/{SOURCE}/{finished[:10]}/{run}"
    files, items, out = {}, [], []
    for item_id, trade_date, fetched in rows:
        body = json.dumps({"id": item_id, "d": trade_date}).encode()
        sha = hashlib.sha256(body).hexdigest()
        items.append({"item_id": item_id, "url": "https://www.twse.com.tw/x", "http_status": 200,
                      "content_sha256": sha, "bytes": len(body), "raw_path": f"raw/{sha}.json"})
        files[f"{base_path}/raw/{sha}.json"] = body
        out.append({"id": item_id, "url": "https://www.twse.com.tw/x", "fetched_at": fetched,
                    "dataset": dataset, "trade_date": trade_date, **DATASETS[dataset]})
    manifest = {"schema_version": "collector_bundle.v1", "source_id": SOURCE, "run_id": run,
                "collector_version": "1.0.0", "started_at": finished, "finished_at": finished,
                "fetch_policy_hash": "fixture", "robots_decision": "allowed", "terms_ref": "https://example.test/t",
                "attribution": "TWSE", "status": "complete", "items": items,
                "bundle_sha256": cb.compute_bundle_sha256([m["content_sha256"] for m in items])}
    files[f"{base_path}/manifest.json"] = json.dumps(manifest).encode()
    files[f"{base_path}/items.jsonl"] = "\n".join(json.dumps(r) for r in out).encode()
    return files, {"source_id": SOURCE, "date": finished[:10], "run_id": run, "finished_at": finished}


def adapter(tmp_path, dataset, bundles):
    files = {}
    for f, _ in bundles:
        files.update(f)
    files["bundles/index.json"] = json.dumps({"bundles": [e for _, e in bundles]}).encode()
    files["status.json"] = json.dumps({"sources": {SOURCE: {
        "status": "ok", "last_run_at": bundles[-1][1]["finished_at"], "cadence_seconds": 86400}}}).encode()
    return cb.CollectorBundleAdapter(connector_id=SOURCE, source_id=SOURCE, source_type="market", dataset=dataset,
                                     cadence_seconds=86400, max_records=100, read_budget_seconds=None,
                                     raw_root=str(tmp_path), transport=FakeTransport(files), attribution_required=True)


@pytest.mark.parametrize("dataset", list(DATASETS))
def test_label_after_fetch_on_trade_date_is_the_recorded_failure_shape(dataset):
    """Reproduce: the label event time is later than the fetch time, so the PIT rule refuses it."""
    label = datetime.fromisoformat(f"{DAY}T{LABEL[dataset]}+00:00")
    fetched = datetime.fromisoformat(f"{DAY}T06:00:00+00:00")
    if dataset == "tw_institutional_flow":
        fetched = datetime.fromisoformat(f"{DAY}T08:00:00+00:00")
    assert label > fetched


@pytest.mark.parametrize("dataset", list(DATASETS))
@pytest.mark.parametrize("fetched,event", [
    (f"{DAY}T05:30:00Z", f"{DAY}T05:30:00Z"),            # exactly at the session close
    (f"{DAY}T05:45:10Z", f"{DAY}T05:45:10Z"),            # after close, before the label: bounded by fetch
    (f"{DAY}T10:00:00Z", None),                          # after every label: the label stands
])
def test_same_day_fetch_after_close_is_bounded_by_fetch(tmp_path, dataset, fetched, event):
    event = event or f"{DAY}T{LABEL[dataset]}Z"
    if dataset == "tw_institutional_flow" and fetched.endswith("05:45:10Z"):
        event = fetched
    a = adapter(tmp_path, dataset, [make(dataset, [("r1", DAY, fetched)])])
    result = a.pull()
    assert len(result.records) == 1
    rec = result.records[0]
    assert rec.metadata["event_time"] == event
    assert rec.metadata["available_time"] == fetched
    assert rec.metadata["event_time"] <= rec.metadata["available_time"]
    assert rec.metadata["event_time"] >= CLOSE
    validate_external_source_record(rec, connector=a.connector(), force=True)  # PIT rule untouched


@pytest.mark.parametrize("dataset", list(DATASETS))
def test_label_between_close_and_fetch_for_flow_and_index(tmp_path, dataset):
    fetched = f"{DAY}T08:00:00Z"  # after the 06:30 label, before the 08:30 flow label
    a = adapter(tmp_path, dataset, [make(dataset, [("r1", DAY, fetched)])])
    rec = a.pull().records[0]
    expected = fetched if dataset == "tw_institutional_flow" else f"{DAY}T06:30:00Z"
    assert rec.metadata["event_time"] == expected


@pytest.mark.parametrize("dataset", list(DATASETS))
def test_preclose_row_is_rejected_alone_and_batch_is_ingested(tmp_path, dataset):
    early = f"{DAY}T05:29:59Z"
    good_day = "2026-11-16"
    rows = [("good-old", good_day, f"{good_day}T12:00:00Z"), ("early", DAY, early),
            ("good-new", DAY, f"{DAY}T09:00:00Z")]
    a = adapter(tmp_path, dataset, [make(dataset, rows, finished=f"{DAY}T09:00:00Z")])
    result = a.pull()
    assert sorted(r.metadata["trade_date"] for r in result.records) == [good_day, DAY]
    assert {r.source_id.split(":")[2] for r in result.records} == {"good-old", "good-new"}
    rejected = result.observed["rejected_items"]
    assert result.observed["rejected_item_count"] == 1
    assert rejected[0]["item_id"] == "early" and rejected[0]["trade_date"] == DAY
    assert rejected[0]["reason"] == "market_row_not_final"
    assert rejected[0]["fetched_at"] == early
    assert f"{DAY}T{LABEL[dataset]}Z" in rejected[0]["detail"] and early in rejected[0]["detail"]


@pytest.mark.parametrize("dataset", list(DATASETS))
def test_fetch_on_the_day_before_is_not_final(tmp_path, dataset):
    a = adapter(tmp_path, dataset, [make(dataset, [("x", DAY, "2026-11-16T12:00:00Z")])])
    result = a.pull()
    assert result.records == ()
    assert result.observed["rejected_items"][0]["reason"] == "market_row_not_final"


@pytest.mark.parametrize("dataset", list(DATASETS))
@pytest.mark.parametrize("late_first", [True, False])
def test_same_trade_date_late_and_same_day_fetch_share_one_key(tmp_path, dataset, late_first):
    """Dedup mechanism: the record key is collector:<source>:<item id>:<content sha12>.

    Event time is not part of the key (nor of the stored row's keyed identity: dataset, series
    and trade date), so moving the event time from the label to the bounded value yields the same
    key and the keyed store upserts rather than adding a second row. No known-marker is involved:
    markers apply only to derived macro/community rows.
    """
    late, same_day = f"{DAY}T10:00:00Z", f"{DAY}T05:45:10Z"
    order = [late, same_day] if late_first else [same_day, late]
    recs = []
    for n, fetched in enumerate(order):
        a = adapter(tmp_path / str(n), dataset, [make(dataset, [("r1", DAY, fetched)], run=f"run-{n}")])
        recs.extend(a.pull().records)
    assert len(recs) == 2
    assert recs[0].source_id == recs[1].source_id
    assert recs[0].metadata["event_time"] != recs[1].metadata["event_time"]
    if dataset != "tw_institutional_flow":
        norm = [r.metadata["normalized_row"] for r in recs]
        assert norm[0] == norm[1]  # same stored row identity and values (date, series, close)


def test_validation_error_names_source_item_and_both_times(tmp_path):
    a = adapter(tmp_path, "tw_index_daily", [make("tw_index_daily", [("r1", DAY, f"{DAY}T10:00:00Z")])])
    rec = a.pull().records[0]
    bad = replace(rec, metadata={**rec.metadata, "event_time": f"{DAY}T11:00:00Z"})
    with pytest.raises(cb.SourceEvidenceError) as err:
        validate_external_source_record(bad, connector=a.connector(), force=True)
    text = str(err.value)
    assert "available_time must be >= event_time" in text
    assert SOURCE in text and "r1" in text and f"{DAY}T11:00:00Z" in text and f"{DAY}T10:00:00Z" in text
