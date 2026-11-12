"""K1: KBar point-in-time availability (SD §3, §6.3): no backdating to bar_end."""

from __future__ import annotations

from datetime import datetime, timezone

from services.source_ingestion.connectors.finmind_taiwan import (
    FinMindTaiwanKBarAdapter,
    compute_bar_availability_ts,
    observe_publication_time,
)

DAY = "2026-11-10"
ROWS = [{"date": DAY, "minute": "09:00:00", "stock_id": "2330", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 3}]


def _n(**kw):
    return FinMindTaiwanKBarAdapter().normalize_kbar_payload(
        ROWS, symbol="2330", session_date=DAY, fetched_at=kw.pop("fetched_at", "2026-11-10T08:00:00Z"), **kw)


def test_after_close_fetch_is_not_backdated_to_bar_end():
    # bar 09:00 local ends 01:01Z; fetched 08:00Z (16:00 local), reconciled 09:00Z
    r = _n(reconciled_at="2026-11-10T09:00:00Z").records[0].metadata["normalized_row"]
    assert r["bar_availability_ts"] == "2026-11-10T09:00:00Z"
    assert r["bar_availability_ts"] > "2026-11-10T01:01:00Z"


def test_unreconciled_bar_is_pending_and_has_no_availability():
    rec = _n().records[0]
    assert rec.metadata["normalized_row"]["bar_availability_ts"] is None
    assert rec.metadata["available_time"] is None
    assert rec.metadata["availability_status"] == "pending_reconciliation"


def test_availability_is_max_of_three():
    end = datetime(2026, 11, 10, 1, 1, tzinfo=timezone.utc)
    f = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)
    rc = datetime(2026, 11, 10, 7, 0, tzinfo=timezone.utc)
    assert compute_bar_availability_ts(end, f, rc) == f
    assert compute_bar_availability_ts(end, rc, f) == f
    assert compute_bar_availability_ts(end, None, rc) is None


def test_provider_published_at_only_when_supplied():
    assert _n(reconciled_at="2026-11-10T09:00:00Z").records[0].metadata["normalized_row"]["provider_published_at"] is None
    r = _n(reconciled_at="2026-11-10T09:00:00Z", provider_published_at="2026-11-10T07:52:00Z")
    assert r.records[0].metadata["normalized_row"]["provider_published_at"] == "2026-11-10T07:52:00Z"


def test_publication_time_observation_bounds():
    obs = [
        {"session_date": DAY, "polled_at": "2026-11-10T07:30:00Z", "row_count": 0},  # 15:30 local
        {"session_date": DAY, "polled_at": "2026-11-10T07:45:00Z", "row_count": 0},  # 15:45
        {"session_date": DAY, "polled_at": "2026-11-10T07:55:00Z", "row_count": 270},  # 15:55
    ]
    s = observe_publication_time(obs)["sessions"][DAY]
    assert s["published_between_local"] == ["15:45", "15:55"]
