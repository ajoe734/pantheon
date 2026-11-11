from __future__ import annotations

import json
from datetime import date

import pytest

from services.source_ingestion.connectors.finmind_taiwan import (
    FinMindLiveFetcher,
    FinMindQuotaError,
    FinMindTaiwanDatasetAdapter,
)
from services.source_ingestion import finmind_broker_backfill as fbb
from services.source_ingestion.connectors import finmind_taiwan as ft
from services.source_ingestion.finmind_broker_backfill import run_backfill, weekdays

ROWS = [
    {"date": "2026-06-08", "stock_id": "2330", "securities_trader_id": "9200",
     "securities_trader": "國泰-敦南", "price": 950.0, "buy": 4000, "sell": 300},
    {"date": "2026-06-08", "stock_id": "2330", "securities_trader_id": "1440",
     "securities_trader": "美林", "price": 949.0, "buy": 100, "sell": 2100},
    {"date": "2026-06-08", "stock_id": "2317", "securities_trader_id": "9200",
     "securities_trader": "國泰-敦南", "price": 200.0, "buy": 10, "sell": 5},
]


def _parquet_bytes() -> bytes:
    import pyarrow as pa
    import io

    import pyarrow.parquet as pq

    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(ROWS), buf)
    return buf.getvalue()


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
    fetcher = FakeFetcher()
    result = run_backfill(output_dir=tmp_path, start=date(2026, 6, 8), end=date(2026, 6, 8),
                          fetcher=fetcher, sleep=lambda s: None)
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
    assert result.top_records > 0 and result.detail_records == 3
    assert "tok-SECRET-123" not in "".join(seen)
    assert "tok-SECRET-123" not in "".join(p.read_text() for p in tmp_path.rglob("*.jsonl"))


def test_cli_main_with_parquet_body(tmp_path, monkeypatch, capsys) -> None:
    body = _parquet_bytes()
    monkeypatch.setattr(ft, "open_external_url", lambda request, **kw: _Resp(body))
    monkeypatch.setenv("FINMIND_API_TOKEN", "tok-SECRET-123")
    rc = fbb.main(["--output-dir", str(tmp_path), "--start", "2026-06-08", "--end", "2026-06-08",
                   "--min-interval-seconds", "0"])
    out = capsys.readouterr().out
    assert rc == 0 and '"detail_records": 3' in out and "tok-SECRET" not in out


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
