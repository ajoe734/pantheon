"""TW-SAME-DAY-CLOSE-VIA-COLLECTOR-20261112: collector close admission, official-wins comparison, preflight.

Official rows come from the recorded OpenAPI-style price payload used by the Taiwan official connector
tests; the collector close uses the recorded MI_INDEX website page values (same fields/URL shape).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.execution.market_snapshot_admission import admit_market_snapshot
from services.source_ingestion import controller_worker
from services.source_ingestion.connectors.taiwan_official import TaiwanOfficialMarketDatasetAdapter
from services.source_ingestion.requirement_state import LatestMarketSnapshotStore
from services.source_ingestion.tests.test_collector_bundle_connector import (
    _TWSE_ROW,
    _market_files,
    adapter_for,
)
from services.source_ingestion.tests.test_taiwan_official_connectors import TWSE_PRICE_HISTORY_PAYLOAD
from services.source_ingestion.tw_close_reconcile import (
    collector_close_lineage,
    compare_close,
    covered_collector_close_session,
)

TWSE = "collector-tw-twse-price-history"
TPEX = "collector-tw-tpex-price-history"
MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20260611&type=ALLBUT0999&response=json"
DAILY_QUOTES_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=2026/06/11&id=&response=json"
# recorded official history: 2330.TWSE 2026-06-09 close 950.0, 2026-06-10 close 955.0
OFFICIAL_LATEST = ("2026-06-10", 955.0)


def _official_records():
    return TaiwanOfficialMarketDatasetAdapter(max_records=10).records_from_price_history_payload(
        "2330.TWSE", "TWSE", TWSE_PRICE_HISTORY_PAYLOAD,
        api_endpoint="https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", trace_id="official",
    )


def _collector_records(tmp_path, rows, *, source_id=TWSE):
    if len(rows) == 1:  # the shared bundle helper builds exactly two items; the second is another symbol
        rows = [*rows, {**rows[0], "symbol": "2454", "security_name": "聯發科"}]
    files, entry = _market_files(source_id, rows)
    adapter = adapter_for(files, [entry], tmp_path, source_id=source_id, source_type="market", platform=None,
                          attribution_required=True, dataset="tw_price_daily")
    return adapter.pull().records


def _row(trade_date, close, url=MI_INDEX_URL):
    return {**_TWSE_ROW, "trade_date": trade_date, "close": close, "url": url}


def _store(tmp_path):
    return LatestMarketSnapshotStore(tmp_path / "snapshots.jsonl")


def _snap(store):
    return store.get("2330.TWSE")


def test_lineage_requires_collector_source_exchange_endpoint_and_digest():
    good = {"provisional": True, "official": False, "collector_source_id": TWSE, "exchange_endpoint": MI_INDEX_URL,
            "bundle_sha256": "a" * 64, "trade_date": "2026-06-11"}
    assert collector_close_lineage({"collector_close": good})["exchange_endpoint"] == MI_INDEX_URL
    for key, bad in (("collector_source_id", "collector-other"), ("exchange_endpoint", "https://example.test/x"),
                     ("bundle_sha256", "short"), ("trade_date", "")):
        assert collector_close_lineage({"collector_close": {**good, key: bad}}) is None
    assert collector_close_lineage({"collector_close": {**good, "official": True}}) is None


def test_collector_close_for_newest_session_is_admitted_with_named_lineage(tmp_path):
    store = _store(tmp_path)
    store.append_normalized_records(_official_records(), ingest_run_id="official-run", observed_at="2026-06-10T10:00:00Z")
    relayed = _collector_records(tmp_path, [_row("2026-06-11", 960.0)])
    store.append_normalized_records(relayed, ingest_run_id="collector-run", observed_at="2026-06-11T07:00:00Z")
    public = _snap(store).to_public_dict()
    close = public["lineage"]["collector_close"][0]
    assert close["collector_source_id"] == TWSE and close["exchange_endpoint"] == MI_INDEX_URL
    assert len(close["bundle_sha256"]) == 64 and close["trade_date"] == "2026-06-11"
    assert public["closes"][-1] == 960.0
    # 15:00 Taipei on the session day: the relayed close is admitted (the official row is not out yet).
    # The recorded calendar-evidence fixture was fetched after this simulated clock, so drop it here;
    # evidence retention is covered by test_collector_close_keeps_prior_governed_calendar_evidence.
    public = {k: v for k, v in public.items() if k != "calendar_evidence"}
    decision = admit_market_snapshot(public, expected_symbol="2330.TWSE", max_age_seconds=86400,
                                     now_iso="2026-06-11T07:00:00Z")
    assert decision.admitted, decision
    # without the collector close the same afternoon is stale: behaviour without collector data is unchanged
    only = LatestMarketSnapshotStore(tmp_path / "official-only.jsonl")
    only.append_normalized_records(_official_records(), ingest_run_id="o", observed_at="2026-06-11T07:00:00Z")
    stale = admit_market_snapshot(only.get("2330.TWSE").to_public_dict(), expected_symbol="2330.TWSE",
                                  max_age_seconds=86400, now_iso="2026-06-11T07:00:00Z")
    assert not stale.admitted and stale.reason_code != "market_input_non_official_lineage"


def test_relayed_close_with_incomplete_lineage_is_not_admitted_and_not_a_non_official_pause(tmp_path):
    store = _store(tmp_path)
    store.append_normalized_records(_official_records(), ingest_run_id="o", observed_at="2026-06-10T10:00:00Z")
    store.append_normalized_records(_collector_records(tmp_path, [_row("2026-06-11", 960.0)]),
                                    ingest_run_id="c", observed_at="2026-06-11T07:00:00Z")
    public = _snap(store).to_public_dict()
    public["lineage"]["collector_close"][0]["bundle_sha256"] = ""
    decision = admit_market_snapshot(public, expected_symbol="2330.TWSE", max_age_seconds=86400,
                                     now_iso="2026-06-11T07:00:00Z")
    assert not decision.admitted and decision.reason_code == "market_input_stale"


def test_collector_row_with_foreign_endpoint_never_enters_the_projection(tmp_path):
    store = _store(tmp_path)
    store.append_normalized_records(_official_records(), ingest_run_id="o", observed_at="2026-06-10T10:00:00Z")
    bad = _collector_records(tmp_path, [_row("2026-06-11", 960.0, url="https://example.test/page")])
    result = store.append_normalized_records(bad, ingest_run_id="c", observed_at="2026-06-11T07:00:00Z")
    assert result["accepted_record_count"] == 0
    assert _snap(store).closes[-1] == OFFICIAL_LATEST[1]


@pytest.mark.parametrize("collector_close,outcome", [(955.0, "agree"), (957.5, "mismatch")])
def test_official_row_replaces_collector_close_and_comparison_is_recorded(tmp_path, collector_close, outcome):
    store = _store(tmp_path)
    old = [r for r in _official_records() if r.metadata["normalized_row"]["date"] == "2026-06-09"]
    store.append_normalized_records(old, ingest_run_id="o1", observed_at="2026-06-09T10:00:00Z")
    store.append_normalized_records(_collector_records(tmp_path, [_row("2026-06-10", collector_close)]),
                                    ingest_run_id="c", observed_at="2026-06-10T07:00:00Z")
    assert _snap(store).closes[-1] == collector_close
    store.append_normalized_records(_official_records(), ingest_run_id="o2", observed_at="2026-06-11T01:00:00Z")
    snap = _snap(store)
    assert snap.closes == (950.0, 955.0)  # official kept either way, one point per session
    assert "collector_close" not in snap.lineage
    readback = snap.to_public_dict()["close_reconciliation"]
    assert readback["compared"] == 1
    if outcome == "agree":
        assert readback["agreed"] == 1 and readback["mismatched"] == 0
    else:
        mismatch = readback["mismatches"][0]
        assert mismatch["reason_code"] == "collector_close_mismatch" and mismatch["kept"] == "official"
        assert (mismatch["collector_close"], mismatch["official_close"]) == (957.5, 955.0)
        assert mismatch["exchange_endpoint"] == MI_INDEX_URL


def test_collector_row_never_replaces_or_hides_an_official_row(tmp_path):
    store = _store(tmp_path)
    store.append_normalized_records(_official_records(), ingest_run_id="o", observed_at="2026-06-11T01:00:00Z")
    before = _snap(store)
    late = _collector_records(tmp_path, [_row("2026-06-10", 999.0)])
    store.append_normalized_records(late, ingest_run_id="c", observed_at="2026-06-11T02:00:00Z")
    after = _snap(store)
    assert after.closes == before.closes == (950.0, 955.0)
    assert "collector_close" not in after.lineage and not after.close_reconciliation


def test_compare_close_is_exact_to_four_decimals():
    lin = {"collector_source_id": TWSE, "exchange_endpoint": MI_INDEX_URL, "bundle_sha256": "b" * 64}
    assert compare_close(symbol="2330.TWSE", trade_date="2026-06-10", collector_close=955.00001,
                         official_close=955.0, lineage=lin)["outcome"] == "agree"
    assert compare_close(symbol="2330.TWSE", trade_date="2026-06-10", collector_close=955.01,
                         official_close=955.0, lineage=lin)["outcome"] == "mismatch"


def _collector_item(connector_id, trade_date, url, *, run_id="run-close", receipt_run_id=None, finished="2026-06-11T07:00:00Z"):
    receipt_run_id = run_id if receipt_run_id is None else receipt_run_id
    event = f"{trade_date}T06:30:00Z"
    return {
        "connector_id": connector_id,
        "latest_source_record": {
            "connector_id": connector_id, "status": "normalized",
            "metadata": {
                "trade_date": trade_date,
                "collector": {"source_id": connector_id, "bundle_status": "complete", "bundle_sha256": "c" * 64},
                "collector_close": {
                    "provisional": True, "official": False, "collector_source_id": connector_id,
                    "exchange_endpoint": url, "bundle_sha256": "c" * 64, "trade_date": trade_date},
            },
            "provenance": {"source_ingest_run_id": run_id, "event_time": event,
                           "available_time": f"{trade_date}T06:50:00Z"},
        },
        "latest_source_record_receipt": {"ingest_run_id": receipt_run_id, "connector_id": connector_id,
                                         "status": "completed", "finished_at": finished},
        # connector-level freshness must never be borrowed
        "freshness": {"last_success_at": "2026-06-12T09:00:00Z"},
    }


def test_preflight_cover_needs_both_exchanges_on_the_same_session():
    both = [_collector_item(TWSE, "2026-06-11", MI_INDEX_URL), _collector_item(TPEX, "2026-06-11", DAILY_QUOTES_URL)]
    cover = covered_collector_close_session(both)
    assert cover["trade_date"] == "2026-06-11" and len(cover["lineage"]) == 2
    assert covered_collector_close_session(both[:1]) is None
    assert covered_collector_close_session(
        [both[0], _collector_item(TPEX, "2026-06-10", DAILY_QUOTES_URL)]) is None
    assert covered_collector_close_session(
        [both[0], _collector_item(TPEX, "2026-06-11", "https://example.test/x")]) is None


def test_preflight_helpers_stamp_session_close_and_event_time_in_utc():
    assert controller_worker._collector_cover_session_close("2026-06-11") == datetime(2026, 6, 11, 5, 30, tzinfo=timezone.utc)
    assert controller_worker._collector_cover_event_time("2026-06-11") == datetime(2026, 6, 11, 6, 30, tzinfo=timezone.utc)


def test_collector_close_keeps_prior_governed_calendar_evidence(tmp_path):
    store = _store(tmp_path)
    old = [r for r in _official_records() if r.metadata["normalized_row"]["date"] == "2026-06-09"]
    store.append_normalized_records(old, ingest_run_id="o1", observed_at="2026-06-09T10:00:00Z")
    before = _snap(store).calendar_evidence
    store.append_normalized_records(_collector_records(tmp_path, [_row("2026-06-10", 955.0)]),
                                    ingest_run_id="c", observed_at="2026-06-10T07:00:00Z")
    after = _snap(store)
    assert after.closes[-1] == 955.0 and after.calendar_evidence == before


def test_cover_binds_receipt_to_the_covered_record_run_and_never_borrows_freshness():
    both = [_collector_item(TWSE, "2026-06-11", MI_INDEX_URL, finished="2026-06-11T07:00:00Z"),
            _collector_item(TPEX, "2026-06-11", DAILY_QUOTES_URL, finished="2026-06-11T06:55:00Z")]
    cover = covered_collector_close_session(both)
    assert cover["receipt_at"] == datetime(2026, 6, 11, 6, 55, tzinfo=timezone.utc)
    # receipt of a different (backfill) run does not attest the selected close
    other = _collector_item(TPEX, "2026-06-11", DAILY_QUOTES_URL, receipt_run_id="run-backfill")
    assert covered_collector_close_session([both[0], other]) is None
    # missing receipt -> fail closed even though connector freshness looks fine
    missing = _collector_item(TPEX, "2026-06-11", DAILY_QUOTES_URL)
    missing["latest_source_record_receipt"] = None
    assert covered_collector_close_session([both[0], missing]) is None
    # fetch after the receipt, or event outside the trade date, is unpaired
    late = _collector_item(TPEX, "2026-06-11", DAILY_QUOTES_URL, finished="2026-06-11T06:40:00Z")
    assert covered_collector_close_session([both[0], late]) is None
    bad = _collector_item(TPEX, "2026-06-11", DAILY_QUOTES_URL)
    bad["latest_source_record"]["provenance"]["event_time"] = "2026-06-10T06:30:00Z"
    assert covered_collector_close_session([both[0], bad]) is None
