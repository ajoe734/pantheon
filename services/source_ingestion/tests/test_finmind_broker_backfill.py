from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from services.source_ingestion.connectors.finmind_taiwan import (
    FinMindFetchError,
    FinMindLiveFetcher,
    FinMindQuotaError,
    FinMindTaiwanDatasetAdapter,
)
from services.source_ingestion import finmind_broker_backfill as fbb
from services.source_ingestion.connectors import finmind_taiwan as ft
from services.source_ingestion.finmind_broker_backfill import run_backfill, weekdays


@pytest.fixture(autouse=True)
def _outside_taiwan_market_hours(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_REFRESH_NOW", "2026-11-27T10:30:00Z")

ROWS = [
    {"date": "2026-06-08", "stock_id": "2330", "securities_trader_id": "9200",
     "securities_trader": "國泰-敦南", "price": 950.0, "buy": 4000, "sell": 300},
    {"date": "2026-06-08", "stock_id": "2330", "securities_trader_id": "1440",
     "securities_trader": "美林", "price": 949.0, "buy": 100, "sell": 2100},
    {"date": "2026-06-08", "stock_id": "2317", "securities_trader_id": "9200",
     "securities_trader": "國泰-敦南", "price": 200.0, "buy": 10, "sell": 5},
]


_CACHED_PARQUET_BYTES: bytes | None = None


def _parquet_bytes() -> bytes:
    global _CACHED_PARQUET_BYTES
    if _CACHED_PARQUET_BYTES is None:
        import io
        import pyarrow as pa
        import pyarrow.parquet as pq

        buf = io.BytesIO()
        pq.write_table(pa.Table.from_pylist(ROWS), buf)
        _CACHED_PARQUET_BYTES = buf.getvalue()
    return _CACHED_PARQUET_BYTES


class FakeFetcher(FinMindLiveFetcher):
    def __init__(self, *, token: bool = True, quota_after: int | None = None, body: bytes | None = None) -> None:
        super().__init__()
        self.has_token = token
        self.calls: list[str] = []
        self.quota_after = quota_after
        self.body = body

    def credential_status(self):
        if self.has_token:
            return {"available": True, "status": "credential_ok", "secret_ref_id": self.secret_ref_id}
        return {"available": False, "status": "credential_unavailable", "secret_ref_id": self.secret_ref_id}

    def fetch_storage_objects_raw(self, dataset, date):
        if self.quota_after is not None and len(self.calls) >= self.quota_after:
            raise FinMindQuotaError("quota")
        self.calls.append(date)
        return (self.body if self.body is not None else _parquet_bytes()), {}


def test_no_token_records_reason_and_fetches_nothing(tmp_path) -> None:
    fetcher = FakeFetcher(token=False)
    result = run_backfill(output_dir=tmp_path, start=date(2026, 6, 8), end=date(2026, 6, 12),
                          fetcher=fetcher, sleep=lambda s: None)
    assert result.status == "credential_unavailable"
    assert "missing credential" in (result.reason or "")
    assert fetcher.calls == []
    assert not list(tmp_path.rglob("*.jsonl"))


def test_backfill_writes_governed_datasets_with_times_and_licence(tmp_path) -> None:
    from services.source_ingestion.daily_bars_store import keyed_dataset_store

    fetcher = FakeFetcher()
    result = run_backfill(output_dir=tmp_path, start=date(2026, 6, 8), end=date(2026, 6, 8),
                          fetcher=fetcher, sleep=lambda s: None)
    assert result.status == "ok" and result.completed == ["2026-06-08"]
    assert result.detail_records == 0 and result.branch_detail_written is False
    # Writes into governed keyed/tw_broker_top, NOT a side directory.
    assert not (tmp_path / "tw_broker_top").exists()
    assert not (tmp_path / "tw_broker_branch_detail").exists()
    assert not (tmp_path / "manifest").exists()
    store = keyed_dataset_store(tmp_path, "tw_broker_top")
    assert store is not None
    top = store.records("2026-06-08")
    assert len(top) == 3  # 2330 buy #1 + 2330 sell #1 + 2317 buy #1
    for rec in top:
        md = rec["metadata"]
        nrow = md["normalized_row"]
        assert md["event_time"] == "2026-06-08" and nrow["trade_date"] == "2026-06-08"
        assert md["available_time"]
        assert md["license_scope"] == "vendor_research"
        assert md["redistribution_allowed"] is False
        assert rec["dataset"] == "tw_broker_top" and md["dataset"] == "tw_broker_top"
        # Compact rows: no raw price-level rows or duplicated body.
        assert "raw_rows" not in json.dumps(rec, ensure_ascii=False) and "body" not in md
    assert "SECRET" not in "".join(json.dumps(r) for r in top)


def test_resume_does_not_refetch_completed_dates(tmp_path) -> None:
    first = FakeFetcher(quota_after=2)
    r1 = run_backfill(output_dir=tmp_path, start=date(2026, 6, 8), end=date(2026, 6, 12),
                      fetcher=first, sleep=lambda s: None)
    assert r1.status == "quota_exhausted" and first.calls == ["2026-06-08", "2026-06-09"]
    second = FakeFetcher()
    r2 = run_backfill(output_dir=tmp_path, start=date(2026, 6, 8), end=date(2026, 6, 12),
                      fetcher=second, sleep=lambda s: None)
    assert second.calls == ["2026-06-10", "2026-06-11", "2026-06-12"]
    assert r2.skipped_completed == ["2026-06-08", "2026-06-09"]


def test_throttle_spaces_requests(tmp_path) -> None:
    sleeps: list[float] = []
    now = [0.0]
    run_backfill(output_dir=tmp_path, start=date(2026, 6, 8), end=date(2026, 6, 10),
                 fetcher=FakeFetcher(), min_interval_seconds=1.5,
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


class _Resp:
    status = 200

    def __init__(self, body: bytes) -> None:
        import io

        self._body = io.BytesIO(body)
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self._body.read(n)  # honours n, like a real HTTP response


def test_real_parquet_body_through_fetch_decode_path(tmp_path, monkeypatch) -> None:
    import gzip

    body = _parquet_bytes()
    assert body[:4] == b"PAR1"
    seen: list[str] = []

    def fake_open(request, **kw):
        seen.append(request.full_url)
        return _Resp(body)

    monkeypatch.setattr(ft, "open_external_url", fake_open)
    monkeypatch.setenv("FINMIND_API_TOKEN", "tok-SECRET-123")
    result = run_backfill(output_dir=tmp_path, start=date(2026, 6, 8), end=date(2026, 6, 8),
                          sleep=lambda s: None)
    assert result.status == "ok" and result.completed == ["2026-06-08"]
    assert result.top_records == 3 and result.detail_records == 0
    assert "tok-SECRET-123" not in "".join(seen)
    assert "tok-SECRET-123" not in "".join(
        gzip.open(p, "rt", encoding="utf-8").read() for p in tmp_path.rglob("*.jsonl.gz")
    )


def test_cli_main_with_parquet_body(tmp_path, monkeypatch, capsys) -> None:
    body = _parquet_bytes()
    monkeypatch.setattr(ft, "open_external_url", lambda request, **kw: _Resp(body))
    monkeypatch.setenv("FINMIND_API_TOKEN", "tok-SECRET-123")
    rc = fbb.main(["--output-dir", str(tmp_path), "--start", "2026-06-08", "--end", "2026-06-08",
                   "--min-interval-seconds", "1.0"])
    out = capsys.readouterr().out
    assert rc == 0 and '"top_records": 3' in out and '"detail_records": 0' in out and "tok-SECRET" not in out


def test_missing_pyarrow_fails_before_any_fetch(tmp_path, monkeypatch) -> None:
    def boom():
        raise fbb.ParquetDependencyError("pyarrow missing")

    monkeypatch.setattr(fbb, "require_pyarrow", boom)
    fetcher = FakeFetcher()
    result = run_backfill(output_dir=tmp_path, start=date(2026, 6, 8), end=date(2026, 6, 9),
                          fetcher=fetcher, sleep=lambda s: None)
    assert result.status == "dependency_unavailable" and fetcher.calls == []


def test_bulk_body_over_json_cap_is_read_in_full(monkeypatch) -> None:
    body = b"PAR1" + b"x" * 21_000_000
    monkeypatch.setattr(ft, "open_external_url", lambda request, **kw: _Resp(body))
    monkeypatch.setenv("FINMIND_API_TOKEN", "tok")
    raw, _ = ft.FinMindLiveFetcher().fetch_storage_objects_raw("D", "2026-06-08")
    assert len(raw) == len(body)


def test_bulk_body_over_bulk_cap_fails_loudly(monkeypatch) -> None:
    monkeypatch.setattr(ft, "_FINMIND_MAX_BULK_RESPONSE_BYTES", 21_000_000)
    monkeypatch.setattr(ft, "open_external_url",
                        lambda request, **kw: _Resp(b"x" * 22_000_000))
    monkeypatch.setenv("FINMIND_API_TOKEN", "tok")
    with pytest.raises(ft.FinMindFetchError):
        ft.FinMindLiveFetcher().fetch_storage_objects_raw("D", "2026-06-08")


def test_large_parquet_body_decodes_end_to_end(tmp_path, monkeypatch) -> None:
    import pyarrow as pa
    import io

    import pyarrow.parquet as pq

    rows = [dict(ROWS[0], pad="x" * 100)]
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), buf)
    # Pad the parquet footer-preserving file past 20 MB via key-value metadata.
    table = pa.Table.from_pylist(rows).replace_schema_metadata({"pad": "y" * 21_000_000})
    buf = io.BytesIO()
    pq.write_table(table, buf)
    body = buf.getvalue()
    assert len(body) > 20_000_000
    monkeypatch.setattr(ft, "open_external_url", lambda request, **kw: _Resp(body))
    monkeypatch.setenv("FINMIND_API_TOKEN", "tok")
    result = run_backfill(output_dir=tmp_path, start=date(2026, 6, 8), end=date(2026, 6, 8),
                          sleep=lambda s: None)
    assert result.status == "ok" and result.completed == ["2026-06-08"]


def test_corrupt_parquet_fails_that_day_without_aborting(tmp_path, monkeypatch) -> None:
    import services.source_ingestion.finmind_broker_backfill as bb

    monkeypatch.setattr(bb, "require_pyarrow", lambda: None)
    monkeypatch.setattr(bb, "decode_parquet_bytes",
                        lambda raw: (_ for _ in ()).throw(ValueError("bad")))
    monkeypatch.setattr(ft, "open_external_url",
                        lambda request, **kw: _Resp(b"PAR1" + b"x" * 100))
    monkeypatch.setenv("FINMIND_API_TOKEN", "tok")
    result = run_backfill(output_dir=tmp_path, start=date(2026, 6, 8), end=date(2026, 6, 9),
                          sleep=lambda s: None)
    assert result.status == "partial" and len(result.failed) == 2


def test_json_application_error_body_is_not_treated_as_data(tmp_path) -> None:
    body = json.dumps({"status": 402, "msg": "Requests reach limit"}).encode()
    result = run_backfill(output_dir=tmp_path, start=date(2026, 6, 8), end=date(2026, 6, 8),
                          fetcher=FakeFetcher(body=body), sleep=lambda s: None)
    assert result.status in {"quota_exhausted", "partial"} and not result.completed


def test_json_empty_data_body_is_a_no_data_day(tmp_path) -> None:
    body = json.dumps({"status": 200, "data": []}).encode()
    result = run_backfill(output_dir=tmp_path, start=date(2026, 6, 8), end=date(2026, 6, 8),
                          fetcher=FakeFetcher(body=body), sleep=lambda s: None)
    assert result.empty_dates == ["2026-06-08"]


def test_json_manifest_body_is_rejected_not_loaded(tmp_path) -> None:
    body = json.dumps({"status": 200, "data": [{"url": "https://signed.example/x"}]}).encode()
    result = run_backfill(output_dir=tmp_path, start=date(2026, 6, 8), end=date(2026, 6, 8),
                          fetcher=FakeFetcher(body=body), sleep=lambda s: None)
    assert result.status == "partial" and "2026-06-08" in result.failed


def test_bounded_window_max_requests_and_wall_seconds_resume_without_refetching(tmp_path) -> None:
    first = FakeFetcher()
    r1 = run_backfill(
        output_dir=tmp_path,
        start=date(2026, 6, 8),
        end=date(2026, 6, 12),
        fetcher=first,
        sleep=lambda s: None,
        max_requests=2,
    )
    assert r1.status == "ok"
    assert r1.stopped_reason == "max_requests_reached"
    assert r1.completed == ["2026-06-08", "2026-06-09"]
    assert r1.remaining_dates_count == 3
    assert first.calls == ["2026-06-08", "2026-06-09"]

    # Second dispatch bounded by max_wall_seconds stops after one more request.
    now = [0.0]
    second = FakeFetcher()
    r2 = run_backfill(
        output_dir=tmp_path,
        start=date(2026, 6, 8),
        end=date(2026, 6, 12),
        fetcher=second,
        min_interval_seconds=1.5,
        sleep=lambda s: now.__setitem__(0, now[0] + s),
        clock=lambda: (now.__setitem__(0, now[0] + 10.0), now[0])[1],
        max_wall_seconds=15.0,
    )
    assert r2.status == "ok"
    assert r2.stopped_reason == "max_wall_seconds_reached"
    assert r2.skipped_completed == ["2026-06-08", "2026-06-09"]
    assert r2.completed == ["2026-06-10"]
    assert r2.remaining_dates_count == 2
    assert second.calls == ["2026-06-10"]

    # Third dispatch finishes the window.
    third = FakeFetcher()
    r3 = run_backfill(
        output_dir=tmp_path,
        start=date(2026, 6, 8),
        end=date(2026, 6, 12),
        fetcher=third,
        sleep=lambda s: None,
    )
    assert r3.status == "ok"
    assert r3.stopped_reason is None
    assert r3.remaining_dates_count == 0
    assert r3.skipped_completed == ["2026-06-08", "2026-06-09", "2026-06-10"]
    assert r3.completed == ["2026-06-11", "2026-06-12"]
    assert third.calls == ["2026-06-11", "2026-06-12"]
    assert r3.total_stored_sessions == 5


def test_backfill_stops_when_stated_cap_would_be_exceeded(tmp_path) -> None:
    from services.source_ingestion.daily_bars_store import keyed_dataset_store

    # 1. Total bytes cap: first date fits, second date would exceed max_total_bytes -> rolled back & stops.
    dir_bytes = tmp_path / "bytes_cap"
    f1 = FakeFetcher()
    first = run_backfill(
        output_dir=dir_bytes,
        start=date(2026, 6, 8),
        end=date(2026, 6, 8),
        fetcher=f1,
        sleep=lambda s: None,
    )
    assert first.status == "ok" and first.bytes_written > 0
    one_day_bytes = first.bytes_written

    f2 = FakeFetcher()
    exceeded = run_backfill(
        output_dir=dir_bytes,
        start=date(2026, 6, 8),
        end=date(2026, 6, 10),
        fetcher=f2,
        sleep=lambda s: None,
        max_total_bytes=one_day_bytes + 10,
    )
    assert exceeded.status == "cap_exceeded"
    assert "total_bytes_cap_exceeded on 2026-06-09" in (exceeded.reason or "")
    assert f2.calls == ["2026-06-09"]  # Stopped immediately; 2026-06-10 was never fetched.
    store = keyed_dataset_store(dir_bytes, "tw_broker_top")
    assert store is not None
    assert store.partitions() == ["2026-06-08"]
    assert exceeded.total_stored_bytes == one_day_bytes <= one_day_bytes + 10

    # 2. Total rows cap: stops before writing the partition that would exceed max_total_rows.
    dir_rows = tmp_path / "rows_cap"
    f_rows = FakeFetcher()
    r_rows = run_backfill(
        output_dir=dir_rows,
        start=date(2026, 6, 8),
        end=date(2026, 6, 9),
        fetcher=f_rows,
        sleep=lambda s: None,
        max_total_rows=4,  # 3 rows per day -> day 1 fits (3 <= 4), day 2 would be 6 > 4.
    )
    assert r_rows.status == "cap_exceeded"
    assert "total_rows_cap_exceeded on 2026-06-09" in (r_rows.reason or "")
    assert r_rows.completed == ["2026-06-08"]
    assert r_rows.total_stored_rows == 3

    # 3. Hourly request cap: refuses a configuration that would exceed FINMIND_PLAN_HOURLY_CAP (6000/h).
    dir_rate = tmp_path / "rate_cap"
    f_rate = FakeFetcher()
    r_rate = run_backfill(
        output_dir=dir_rate,
        start=date(2026, 6, 8),
        end=date(2026, 6, 9),
        fetcher=f_rate,
        sleep=lambda s: None,
        min_interval_seconds=0.2,  # 18,000/h > 6,000/h
    )
    assert r_rate.status == "cap_exceeded"
    assert "plan_hourly_cap" in (r_rate.reason or "")
    assert f_rate.calls == []


def test_backfill_skips_session_already_completed_by_whole_market_daily(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(fbb, "MIN_STORED_SYMBOLS", 1)
    # 2026-06-08 has both buy and sell sides for 2330; add a sell row for 2317 so both symbols are two-sided.
    two_sided_rows = ROWS + [
        {"date": "2026-06-08", "stock_id": "2317", "securities_trader_id": "1440",
         "securities_trader": "美林", "price": 199.0, "buy": 1, "sell": 50},
    ]
    import io
    import pyarrow as pa
    import pyarrow.parquet as pq

    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(two_sided_rows), buf)
    body = buf.getvalue()

    daily_fetcher = FakeFetcher(body=body)
    receipt = fbb.run_whole_market_session(
        market_data_root=tmp_path,
        trade_date="2026-06-08",
        fetcher=daily_fetcher,
        expected_symbols={"2330", "2317"},
    )
    assert receipt["status"] == "complete"

    backfill_fetcher = FakeFetcher(body=body)
    result = run_backfill(
        output_dir=tmp_path,
        start=date(2026, 6, 8),
        end=date(2026, 6, 9),
        fetcher=backfill_fetcher,
        sleep=lambda s: None,
    )
    assert result.status == "ok"
    assert result.skipped_completed == ["2026-06-08"]
    assert result.completed == ["2026-06-09"]
    assert backfill_fetcher.calls == ["2026-06-09"]


def test_verify_two_year_backfill_coverage_is_read_only_and_checks_480_sessions(tmp_path) -> None:
    # Missing directory: read-only failure without creating any directory or file.
    missing_root = tmp_path / "does_not_exist"
    rep_missing = fbb.verify_two_year_backfill_coverage(missing_root, min_sessions=2)
    assert rep_missing["passed"] is False and rep_missing["status"] == "fail"
    assert not missing_root.exists()

    # Build two valid two-sided sessions.
    two_sided_rows = ROWS + [
        {"date": "2026-06-08", "stock_id": "2317", "securities_trader_id": "1440",
         "securities_trader": "美林", "price": 199.0, "buy": 1, "sell": 50},
    ]
    import io
    import pyarrow as pa
    import pyarrow.parquet as pq

    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(two_sided_rows), buf)
    body = buf.getvalue()

    store_root = tmp_path / "mds"
    res = run_backfill(
        output_dir=store_root,
        start=date(2026, 6, 8),
        end=date(2026, 6, 9),
        fetcher=FakeFetcher(body=body),
        sleep=lambda s: None,
    )
    assert res.status == "ok"

    before_snapshot = {
        p.relative_to(store_root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in store_root.rglob("*")
        if p.is_file()
    }
    proof_ok = fbb.verify_two_year_backfill_coverage(
        store_root, min_sessions=2, min_symbols_per_session=2
    )
    after_snapshot = {
        p.relative_to(store_root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in store_root.rglob("*")
        if p.is_file()
    }
    assert before_snapshot == after_snapshot
    assert proof_ok["passed"] is True and proof_ok["status"] == "pass"
    assert proof_ok["keyed_retention_days"] == 750 and ("keyed_retention_" + "sessions") not in proof_ok
    assert proof_ok["two_sided_sessions_count"] == 2
    assert proof_ok["per_session_symbol_counts"] == {"2026-06-08": 2, "2026-06-09": 2}
    assert proof_ok["deficient_sessions"] == []

    # Requiring 480 sessions when only 2 are stored fails closed.
    proof_short = fbb.verify_two_year_backfill_coverage(store_root, min_sessions=480, min_symbols_per_session=2)
    assert proof_short["passed"] is False and "480" in (proof_short["reason"] or "")


def test_two_year_default_backfill_window_survives_delete_mode_retention_sweep(tmp_path) -> None:
    from services.source_ingestion.daily_bars_store import DailyBarsStore, KEYED_DATASETS, keyed_dataset_store
    from services.source_ingestion.pin_source import PinSource
    from services.source_ingestion.retention import MODE_ENV, REPORT_FILENAME, RetentionSweeper

    assert KEYED_DATASETS["tw_broker_top"].retention_days == 750
    end_day = date(2026, 11, 27)
    start_day = end_day - timedelta(days=365 * fbb.BACKFILL_YEARS)
    assert (end_day - start_day).days == 730 < KEYED_DATASETS["tw_broker_top"].retention_days

    root = tmp_path / "mds"
    fetcher = FakeFetcher()
    result = run_backfill(
        output_dir=root,
        start=start_day,
        end=end_day,
        fetcher=fetcher,
        sleep=lambda s: None,
    )
    assert result.status == "ok"
    expected_days = [d.isoformat() for d in weekdays(start_day, end_day)]
    assert len(expected_days) >= 520
    assert result.completed == expected_days

    store = keyed_dataset_store(root, "tw_broker_top")
    assert store is not None
    assert store.partitions() == expected_days

    pin_path = tmp_path / "pins.json"
    pin_path.write_text(json.dumps({"trade_dates": []}), encoding="utf-8")
    sweeper = RetentionSweeper(
        daily_bars_store=DailyBarsStore(root),
        report_path=tmp_path / REPORT_FILENAME,
        pin_source=PinSource([pin_path]),
        keyed_root=root,
        environ={MODE_ENV: "delete"},
        today=lambda: end_day.isoformat(),
    )
    report = sweeper.run()
    assert report["status"] == "completed"
    assert report["stores"]["keyed"]["would_delete_partitions"] == 0
    assert report["stores"]["keyed"]["deleted_partitions"] == 0
    assert report["stores"]["keyed"]["deleted_rows"] == 0
    assert store.partitions() == expected_days

    expired_day = (end_day - timedelta(days=751)).isoformat()
    store.write_records([{"symbol": "2330", "trade_date": expired_day, "side": "buy", "rank": 1, "broker": "A"}], ingest_run_id="bf-old")
    report_expired = RetentionSweeper(
        daily_bars_store=DailyBarsStore(root), report_path=tmp_path / REPORT_FILENAME,
        pin_source=PinSource([pin_path]), keyed_root=root, environ={MODE_ENV: "delete"}, today=lambda: end_day.isoformat(),
    ).run()
    assert report_expired["status"] == "completed"
    assert report_expired["stores"]["keyed"]["would_delete_partitions"] == 1
    assert report_expired["stores"]["keyed"]["deleted_partitions"] == 1
    assert store.partitions() == expected_days


def test_backfill_market_open_guard_blocks_start_inside_window_and_stops_between_sessions(
    tmp_path, monkeypatch
) -> None:
    from services.source_ingestion.daily_bars_store import keyed_dataset_store

    # Use the production defaults: 900s profile + 600s cold-start restore reserve.
    monkeypatch.delenv("PANTHEON_REFRESH_PROFILE_BUDGET_SECONDS", raising=False)
    monkeypatch.delenv("PANTHEON_REFRESH_RESTORE_BUDGET_SECONDS", raising=False)

    # 1. The default guard starts at 00:30Z; no session may start at the boundary.
    monkeypatch.setenv("PANTHEON_REFRESH_NOW", "2026-11-18T00:30:00Z")
    f_inside = FakeFetcher()
    inside = run_backfill(
        output_dir=tmp_path / "inside",
        start=date(2026, 6, 8),
        end=date(2026, 6, 10),
        fetcher=f_inside,
        sleep=lambda s: None,
    )
    assert inside.status == "not_run"
    assert inside.stopped_reason == "market_open_0055z"
    assert "market_open_0055z" in (inside.reason or "") and "00:55Z" in (inside.reason or "")
    assert inside.requests_made == 0 and f_inside.calls == []

    # 2. Start outside the window (Wednesday 00:29Z) and reach 00:30Z between session 1 and session 2.
    monkeypatch.setenv("PANTHEON_REFRESH_NOW", "2026-11-18T00:29:00Z")
    clock_now = [0.0]

    class AdvancingFetcher(FakeFetcher):
        def fetch_storage_objects_raw(self, dataset, day_s):
            out = super().fetch_storage_objects_raw(dataset, day_s)
            # After session 1 finishes, 60 seconds have elapsed -> 00:30Z (inside the guard window).
            clock_now[0] += 60.0
            return out

    between_dir = tmp_path / "between"
    f_between = AdvancingFetcher()
    between = run_backfill(
        output_dir=between_dir,
        start=date(2026, 6, 8),
        end=date(2026, 6, 10),
        fetcher=f_between,
        sleep=lambda s: None,
        clock=lambda: clock_now[0],
    )
    assert between.status == "ok"
    assert between.stopped_reason == "market_open_0055z"
    assert "market_open_0055z" in (between.reason or "") and "00:55Z" in (between.reason or "")
    assert between.completed == ["2026-06-08"]
    assert between.remaining_dates_count == 2
    assert between.requests_made == 1 and f_between.calls == ["2026-06-08"]
    store = keyed_dataset_store(between_dir, "tw_broker_top")
    assert store is not None and store.partitions() == ["2026-06-08"]

    # 3. Crossing 00:30Z during throttle sleep (start 00:29:59Z, default min_interval_seconds=1.5):
    # wait is bounded to 1.0s (00:30:00Z), guard is rechecked after waiting and before fetching, so session 2 is not fetched.
    monkeypatch.setenv("PANTHEON_REFRESH_NOW", "2026-11-18T00:29:59Z")
    sleep_clock, sleeps_crossing, f_sleep_cross = [0.0], [], FakeFetcher()
    sleep_cross = run_backfill(
        output_dir=tmp_path / "sleep_cross", start=date(2026, 6, 8), end=date(2026, 6, 9), fetcher=f_sleep_cross,
        sleep=lambda s: (sleeps_crossing.append(s), sleep_clock.__setitem__(0, sleep_clock[0] + s)),
        clock=lambda: sleep_clock[0],
    )
    assert sleep_cross.status == "ok" and sleep_cross.stopped_reason == "market_open_0055z"
    assert "market_open_0055z" in (sleep_cross.reason or "") and "00:55Z" in (sleep_cross.reason or "")
    assert sleep_cross.completed == ["2026-06-08"] and sleep_cross.remaining_dates_count == 1
    assert sleep_cross.requests_made == 1 and f_sleep_cross.calls == ["2026-06-08"]
    assert sleeps_crossing == [pytest.approx(1.0)] and sleep_clock[0] == pytest.approx(1.0)

    # 4. Long throttle interval (min_interval_seconds=1560, max_wall_seconds=60) starting at 00:29:00Z:
    # wait is bounded to 60s (00:30:00Z), never sleeping to 00:55Z (1560s), and session 2 is not fetched.
    monkeypatch.setenv("PANTHEON_REFRESH_NOW", "2026-11-18T00:29:00Z")
    long_clock, sleeps_long, f_long = [0.0], [], FakeFetcher()
    long_res = run_backfill(
        output_dir=tmp_path / "long_wait", start=date(2026, 6, 8), end=date(2026, 6, 9), fetcher=f_long,
        min_interval_seconds=1560.0, max_wall_seconds=60.0,
        sleep=lambda s: (sleeps_long.append(s), long_clock.__setitem__(0, long_clock[0] + s)),
        clock=lambda: long_clock[0],
    )
    assert long_res.status == "ok" and long_res.stopped_reason == "market_open_0055z"
    assert "market_open_0055z" in (long_res.reason or "") and long_res.completed == ["2026-06-08"]
    assert long_res.requests_made == 1 and f_long.calls == ["2026-06-08"]
    assert sleeps_long == [pytest.approx(60.0)] and long_clock[0] == pytest.approx(60.0)

    # 5. Wall deadline tighter than market deadline during throttle sleep (max_wall_seconds=30 at 00:29:00Z).
    wall_clock, sleeps_wall, f_wall = [0.0], [], FakeFetcher()
    wall_res = run_backfill(
        output_dir=tmp_path / "wall_bound", start=date(2026, 6, 8), end=date(2026, 6, 9), fetcher=f_wall,
        min_interval_seconds=1560.0, max_wall_seconds=30.0,
        sleep=lambda s: (sleeps_wall.append(s), wall_clock.__setitem__(0, wall_clock[0] + s)),
        clock=lambda: wall_clock[0],
    )
    assert wall_res.status == "ok" and wall_res.stopped_reason == "max_wall_seconds_reached"
    assert wall_res.completed == ["2026-06-08"] and wall_res.requests_made == 1 and f_wall.calls == ["2026-06-08"]
    assert sleeps_wall == [pytest.approx(30.0)] and wall_clock[0] == pytest.approx(30.0)


def test_failing_session_prints_redacted_traceback_to_stderr_without_token(
    tmp_path, capsys
) -> None:
    secret = "tok-SECRET-TRACEBACK-999"

    class FailingFetcher(FakeFetcher):
        def resolve_token(self):
            return secret

        def fetch_storage_objects_raw(self, dataset, day_s):
            self.calls.append(day_s)
            if day_s == "2026-06-08":
                raise FinMindFetchError(f"upstream storage_objects 502 with token={secret}")
            return super().fetch_storage_objects_raw(dataset, day_s)

    result = run_backfill(
        output_dir=tmp_path,
        start=date(2026, 6, 8),
        end=date(2026, 6, 9),
        fetcher=FailingFetcher(),
        sleep=lambda s: None,
    )
    captured = capsys.readouterr()
    assert result.status == "partial"
    assert "2026-06-08" in result.failed and secret not in result.failed["2026-06-08"]
    assert result.completed == ["2026-06-09"]
    assert "Traceback (most recent call last):" in captured.err
    assert "session 2026-06-08 failed:" in captured.err
    assert "FinMindFetchError: upstream storage_objects 502 with token=***" in captured.err
    assert secret not in captured.err and secret not in captured.out
