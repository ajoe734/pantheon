"""TW-OFFICIAL-RECONCILE-AFTER-COLLECTOR-20261117.

A session covered only by provisional collector closes must make the bounded refresh preflight
proceed once the official next-morning publication time has passed, so the existing official
refresh ingests the official rows and the snapshot store reconciles (official wins). Official rows
come from the recorded OpenAPI payload; the collector close from the recorded MI_INDEX page values.
"""

from __future__ import annotations

from typing import Any

from scripts.test_deploy_nonprod_vm import _run_pinned_taiwan_preflight
from services.source_ingestion.requirement_state import LatestMarketSnapshotStore
from services.source_ingestion.tests.test_tw_close_reconcile import (
    _collector_records,
    _official_records,
    _row,
)
from services.source_ingestion.tw_close_reconcile import compare_close  # noqa: F401  (shared comparison)

SYMBOL = "2330.TWSE"
SESSION = "2026-06-10"  # recorded official history: 2330.TWSE close 955.0 on this session


def _served(store: LatestMarketSnapshotStore) -> dict[str, Any]:
    from services.source_ingestion.connectors.taiwan_official import governed_taiwan_calendar_evidence

    public = store.get(SYMBOL).to_public_dict()
    # The governed 2026 calendar was fetched after this June fixture clock; backdate the fetch only.
    public["calendar_evidence"] = {
        **governed_taiwan_calendar_evidence(venue="TWSE", trade_date=SESSION),
        "fetched_at": "2026-06-01T00:00:00Z",
    }
    return public


def _collector_only_store(tmp_path, collector_close: float) -> LatestMarketSnapshotStore:
    store = LatestMarketSnapshotStore(tmp_path / "snapshots.jsonl")
    old = [r for r in _official_records() if r.metadata["normalized_row"]["date"] == "2026-06-09"]
    store.append_normalized_records(old, ingest_run_id="o1", observed_at="2026-06-09T10:00:00Z")
    store.append_normalized_records(
        _collector_records(tmp_path, [_row(SESSION, collector_close)]),
        ingest_run_id="collector", observed_at="2026-06-10T07:00:00Z",
    )
    return store


def _preflight(store, now_utc: str) -> dict[str, Any]:
    out = _run_pinned_taiwan_preflight(now_utc, {SYMBOL: _served(store)}, symbols=SYMBOL)
    return out


def test_collector_covered_session_proceeds_after_official_publication_and_official_wins(tmp_path) -> None:
    store = _collector_only_store(tmp_path, 957.5)  # one injected mismatch vs official 955.0
    assert "collector_close" in store.get(SYMBOL).lineage

    # 2026-06-11 07:30 Taipei: official next-morning publication time (07:00) has passed.
    result = _preflight(store, "2026-06-10T23:30:00Z")
    assert result["status"] == "proceed"
    assert result["reason"] == "official_rows_missing_collector_only"
    assert result["pending_trade_dates"] == [SESSION] and result["session_date"] == SESSION

    # The existing bounded refresh ingests the official rows for the session.
    store.append_normalized_records(_official_records(), ingest_run_id="official", observed_at="2026-06-11T01:00:00Z")
    snap = store.get(SYMBOL)
    assert snap.closes[-1] == 955.0 and "collector_close" not in snap.lineage
    readback = snap.to_public_dict()["close_reconciliation"]
    assert readback["compared"] == 1 and readback["agreed"] == 0 and readback["mismatched"] == 1
    mismatch = readback["mismatches"][0]
    assert mismatch["reason_code"] == "collector_close_mismatch" and mismatch["kept"] == "official"
    assert (mismatch["collector_close"], mismatch["official_close"]) == (957.5, 955.0)
    # agreement ratio over the compared collector symbols
    assert readback["agreed"] / readback["compared"] == 0.0


def test_agreeing_collector_close_is_reported_in_the_ratio(tmp_path) -> None:
    store = _collector_only_store(tmp_path, 955.0)
    store.append_normalized_records(_official_records(), ingest_run_id="official", observed_at="2026-06-11T01:00:00Z")
    readback = store.get(SYMBOL).to_public_dict()["close_reconciliation"]
    assert (readback["compared"], readback["agreed"], readback["mismatched"]) == (1, 1, 0)


def test_session_with_official_rows_is_noop_and_before_publication_does_not_proceed(tmp_path) -> None:
    # Before the official publication time (same evening, 20:00 Taipei): still covered, never proceeds.
    collector_only = _collector_only_store(tmp_path, 957.5)
    early = _preflight(collector_only, "2026-06-10T12:00:00Z")
    assert early["status"] == "noop" and early["reason"] == "already_fresh"
    # Just before 07:00 next morning: before publication (and before close of the new session).
    before_publish = _preflight(collector_only, "2026-06-10T22:59:00Z")
    assert before_publish["status"] != "proceed"

    # A session that already holds official rows stays noop already_fresh.
    held = LatestMarketSnapshotStore(tmp_path / "official.jsonl")
    held.append_normalized_records(_official_records(), ingest_run_id="official", observed_at="2026-06-10T11:00:00Z")
    result = _preflight(held, "2026-06-10T12:00:00Z")
    assert result["status"] == "noop" and result["reason"] == "already_fresh"

