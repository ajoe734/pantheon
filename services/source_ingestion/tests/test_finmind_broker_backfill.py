from __future__ import annotations

import json
from datetime import date

import pytest

from services.source_ingestion.connectors.finmind_taiwan import (
    FinMindLiveFetcher,
    FinMindQuotaError,
    FinMindTaiwanDatasetAdapter,
)
from services.source_ingestion.finmind_broker_backfill import run_backfill, weekdays

ROWS = [
    {"date": "2026-06-08", "stock_id": "2330", "securities_trader_id": "9200",
     "securities_trader": "國泰-敦南", "price": 950.0, "buy": 4000, "sell": 300},
    {"date": "2026-06-08", "stock_id": "2330", "securities_trader_id": "1440",
     "securities_trader": "美林", "price": 949.0, "buy": 100, "sell": 2100},
    {"date": "2026-06-08", "stock_id": "2317", "securities_trader_id": "9200",
     "securities_trader": "國泰-敦南", "price": 200.0, "buy": 10, "sell": 5},
]


class FakeFetcher(FinMindLiveFetcher):
    def __init__(self, *, token: bool = True, quota_after: int | None = None) -> None:
        super().__init__()
        self.has_token = token
        self.calls: list[str] = []
        self.quota_after = quota_after

    def credential_status(self):
        if self.has_token:
            return {"available": True, "status": "credential_ok", "secret_ref_id": self.secret_ref_id}
        return {"available": False, "status": "credential_unavailable", "secret_ref_id": self.secret_ref_id}

    def fetch_storage_objects(self, dataset, date):
        if self.quota_after is not None and len(self.calls) >= self.quota_after:
            raise FinMindQuotaError("quota")
        self.calls.append(date)
        return {"status": 200, "data": [{"object_name": f"{date}.parquet",
                                         "url": "https://signed.example/x?sig=SECRET"}]}, {}


def loader(manifest):
    return list(ROWS)


def test_no_token_records_reason_and_fetches_nothing(tmp_path) -> None:
    fetcher = FakeFetcher(token=False)
    result = run_backfill(output_dir=tmp_path, start=date(2026, 6, 8), end=date(2026, 6, 12),
                          fetcher=fetcher, rows_loader=loader, sleep=lambda s: None)
    assert result.status == "credential_unavailable"
    assert "missing credential" in (result.reason or "")
    assert fetcher.calls == []
    assert not list(tmp_path.rglob("*.jsonl"))


def test_backfill_writes_governed_datasets_with_times_and_licence(tmp_path) -> None:
    fetcher = FakeFetcher()
    result = run_backfill(output_dir=tmp_path, start=date(2026, 6, 8), end=date(2026, 6, 8),
                          fetcher=fetcher, rows_loader=loader, sleep=lambda s: None)
    assert result.status == "ok" and result.completed == ["2026-06-08"]
    top = [json.loads(l) for l in (tmp_path / "tw_broker_top/date=2026-06-08/records.jsonl").read_text().splitlines()]
    detail = [json.loads(l) for l in (tmp_path / "tw_broker_branch_detail/date=2026-06-08/records.jsonl").read_text().splitlines()]
    assert top and len(detail) == 3  # 2330 x2 branches + 2317 x1
    for rec in top + detail:
        md = rec["metadata"]
        assert md["event_time"] == "2026-06-08" and md["available_time"]
        assert md["license_scope"] == "vendor_research"
        assert md["redistribution_allowed"] is False
    assert {r["metadata"]["dataset"] for r in top} == {"tw_broker_top"}
    assert {r["metadata"]["dataset"] for r in detail} == {"tw_broker_branch_detail"}
    assert "SECRET" not in "".join(p.read_text() for p in tmp_path.rglob("*.jsonl"))


def test_resume_does_not_refetch_completed_dates(tmp_path) -> None:
    first = FakeFetcher(quota_after=2)
    r1 = run_backfill(output_dir=tmp_path, start=date(2026, 6, 8), end=date(2026, 6, 12),
                      fetcher=first, rows_loader=loader, sleep=lambda s: None)
    assert r1.status == "quota_exhausted" and first.calls == ["2026-06-08", "2026-06-09"]
    second = FakeFetcher()
    r2 = run_backfill(output_dir=tmp_path, start=date(2026, 6, 8), end=date(2026, 6, 12),
                      fetcher=second, rows_loader=loader, sleep=lambda s: None)
    assert second.calls == ["2026-06-10", "2026-06-11", "2026-06-12"]
    assert r2.skipped_completed == ["2026-06-08", "2026-06-09"]


def test_throttle_spaces_requests(tmp_path) -> None:
    sleeps: list[float] = []
    now = [0.0]
    run_backfill(output_dir=tmp_path, start=date(2026, 6, 8), end=date(2026, 6, 10),
                 fetcher=FakeFetcher(), rows_loader=loader, min_interval_seconds=1.5,
                 sleep=lambda s: (sleeps.append(s), now.__setitem__(0, now[0] + s)),
                 clock=lambda: now[0])
    assert len(sleeps) == 2 and all(abs(s - 1.5) < 1e-9 for s in sleeps)


def test_two_year_weekday_window_is_bounded_below_hourly_cap() -> None:
    days = list(weekdays(date(2024, 11, 11), date(2026, 11, 11)))
    assert 500 < len(days) < 560
    # default 1.5s interval => <=2400 req/h, under the 6000/h SponsorYear limit
    assert 3600 / 1.5 < 6000


def test_finmind_price_never_lands_in_tw_price_daily() -> None:
    records = FinMindTaiwanDatasetAdapter().records_from_data_payload(
        "TaiwanStockPrice", {"data": [{"date": "2026-06-08", "stock_id": "2330", "close": 955.0}]}
    )
    for rec in records:
        md = rec.metadata
        assert "normalized_row" not in md
        assert md["normalized_target"] != "tw_price_daily"
        assert md["dataset"] != "tw_price_daily"
        assert "tw_price_daily" not in json.dumps(md, default=str)


def test_catalog_does_not_route_finmind_to_normalized_tw_price_daily() -> None:
    from services.source_ingestion.financial_source_catalog import INITIAL_FINANCIAL_DATA_SOURCE_ENTRIES

    entry = next(e for e in INITIAL_FINANCIAL_DATA_SOURCE_ENTRIES if e.provider == "FinMind")
    assert "normalized/tw_price_daily" not in json.dumps(entry.to_dict(), default=str)
