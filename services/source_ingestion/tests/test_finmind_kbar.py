"""K1: FinMind TaiwanStockKBar connector contract (SD_INTRADAY_MINUTE_BARS §3)."""

from __future__ import annotations

import urllib.error
from datetime import date, datetime, timedelta

import pytest

from services.source_ingestion.connector_definitions import DEPLOYED_CONNECTOR_DEFINITIONS
from services.source_ingestion.connectors import finmind_taiwan as fm
from services.source_ingestion.connectors.finmind_taiwan import (
    FINMIND_KBAR_CATALOG_ENTRY,
    FINMIND_TAIWAN_DATASETS,
    FinMindCredentialError,
    FinMindLiveFetcher,
    FinMindTaiwanKBarAdapter,
    KBarHourlyBudget,
    classify_range_probe,
    fetch_kbar,
    measure_volume_unit,
)

DAY = "2026-11-10"


def _row(minute, o=100.0, h=101.0, l=99.0, c=100.5, v=10, sid="2330", d=DAY):
    return {"date": d, "minute": minute, "stock_id": sid, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _public_dispatch(rows, *, day=DAY, parameters=None, fetch_changes=None):
    from services.source_ingestion.provider_adapters import execute_provider_owned_adapter

    adapter = FinMindTaiwanKBarAdapter()
    return execute_provider_owned_adapter(
        connector=adapter.connector(),
        fetch={**adapter.fetch_config(), **(fetch_changes or {})},
        trace_id="kbar-public-dispatch",
        job_parameters={
            "symbol": "2330", "date": day, "payload": {"data": rows},
            "fetched_at": f"{day}T08:00:00Z", "reconciled_at": f"{day}T09:00:00Z",
            **(parameters or {}),
        },
    )


def _full_session():
    start = datetime.fromisoformat(f"{DAY}T09:00:00")
    return [_row((start + timedelta(minutes=i)).strftime("%H:%M:%S")) for i in range(271)]


def test_public_dispatch_preserves_complete_symbol_day():
    records = _public_dispatch(_full_session())
    assert len(records) == 271
    assert records[0].metadata["raw_row"]["minute"] == "09:00:00"
    assert records[-1].metadata["raw_row"]["minute"] == "13:30:00"
    assert all(r.metadata["normalization_summary"]["kept_bars"] == len(records) for r in records)


def test_public_dispatch_default_without_fetch_limit_preserves_complete_symbol_day():
    assert len(_public_dispatch(_full_session(), fetch_changes={"max_records": None})) == 271


@pytest.mark.parametrize("limit", [100, 270, 271, 300])
@pytest.mark.parametrize("location", ["fetch", "request"])
def test_public_dispatch_explicit_limit_never_silently_truncates(limit, location):
    from services.source_ingestion.connectors.base import SourceEvidenceError

    fetch_changes = {"max_records": limit} if location == "fetch" else {}
    parameters = {"max_records": limit} if location == "request" else {}
    if limit < 271:
        with pytest.raises(SourceEvidenceError, match=r"complete symbol-day.*required=271.*allowed=" + str(limit)):
            _public_dispatch(_full_session(), parameters=parameters, fetch_changes=fetch_changes)
    else:
        assert len(_public_dispatch(_full_session(), parameters=parameters, fetch_changes=fetch_changes)) == 271


@pytest.mark.parametrize("limit", [0, -1])
@pytest.mark.parametrize("location", ["fetch", "request"])
def test_public_dispatch_nonpositive_limit_is_rejected(limit, location):
    from services.source_ingestion.connectors.base import SourceEvidenceError

    with pytest.raises(SourceEvidenceError, match="max_records must be > 0"):
        _public_dispatch(
            _full_session(),
            fetch_changes={"max_records": limit} if location == "fetch" else {},
            parameters={"max_records": limit} if location == "request" else {},
        )


@pytest.mark.parametrize("day", ["2026-10-09", "2026-02-16", "2026-11-08"])
@pytest.mark.parametrize("overrides", [{}, {"is_trading_day": lambda _: True},
                                      {"closed_dates": [], "holidays": []}])
def test_public_dispatch_governed_closures_cannot_be_reopened(day, overrides):
    from services.source_ingestion.connectors.base import SourceEvidenceError

    with pytest.raises(SourceEvidenceError, match="non-trading day"):
        _public_dispatch([_row("09:00:00", d=day)], day=day, parameters=overrides)


@pytest.mark.parametrize("overrides", [
    {"is_trading_day": lambda _: False}, {"closed_dates": [DAY]}, {"holidays": [DAY]},
    {"closed_dates": ["2026-11-09"], "holidays": [DAY]},
    {"is_trading_day": lambda _: True, "closed_dates": [DAY]},
])
def test_public_dispatch_caller_calendar_can_add_closures(overrides):
    from services.source_ingestion.connectors.base import SourceEvidenceError

    with pytest.raises(SourceEvidenceError, match="non-trading day"):
        _public_dispatch([_row("09:00:00")], parameters=overrides)


def test_dataset_row_maps_to_tw_price_1m_only():
    row = FINMIND_KBAR_CATALOG_ENTRY
    assert row["dataset"] == "TaiwanStockKBar"
    assert row["normalized_dataset"] == "tw_price_1m"
    assert row["cadence"] == "daily_after_close"
    # daily truth is untouched
    assert all(d["normalized_dataset"] != "tw_price_daily" for d in FINMIND_TAIWAN_DATASETS)


def test_connector_definition_registered_for_tw_price_1m():
    d = next(x for x in DEPLOYED_CONNECTOR_DEFINITIONS if x.definition_id == "tw-finmind-kbar-1m")
    assert tuple(d.datasets) == ("tw_price_1m",)
    assert "tw_price_daily" not in d.datasets
    assert tuple(d.allowed_host_patterns) == ("api.finmindtrade.com",)


def test_normalization_keeps_raw_hash_and_session_filter():
    rows = [_row("08:45:00"), _row("09:00:00"), _row("09:01:00"), _row("13:30:00"), _row("14:00:00")]
    res = FinMindTaiwanKBarAdapter().normalize_kbar_payload(
        {"data": rows}, symbol="2330", session_date=DAY, fetched_at="2026-11-10T08:30:00Z",
        quota_meta={"ratelimit_remaining": 5000, "request_url": "https://x?token=secret"},
    )
    assert res.kept_bars == 3 and res.dropped_outside_session == 2
    rec = res.records[0]
    m = rec.metadata
    assert m["dataset"] == "tw_price_1m" and m["source"] == "finmind" and m["source_dataset"] == "TaiwanStockKBar"
    assert m["raw_row"]["minute"] == "09:00:00" and m["source_hash"] and m["request_id"]
    assert m["quota_meta"] == {"ratelimit_remaining": 5000}  # url stripped
    n = m["normalized_row"]
    assert n["bar_start"] == "2026-11-10T09:00:00+08:00" and n["bar_end"] == "2026-11-10T09:01:00+08:00"
    for k in ("provider_published_at", "fetched_at", "reconciled_at", "bar_availability_ts"):
        assert k in n


def test_conflicting_duplicate_flagged_identical_ignored():
    rows = [_row("09:00:00"), _row("09:00:00"), _row("09:01:00"), _row("09:01:00", c=105.0)]
    res = FinMindTaiwanKBarAdapter().normalize_kbar_payload(
        rows, symbol="2330", session_date=DAY, fetched_at="2026-11-10T08:30:00Z")
    assert res.identical_duplicates == 1
    assert res.conflicting_duplicates == ("2026-11-10T09:01:00+08:00",)


def test_non_trading_day_rejected():
    res = FinMindTaiwanKBarAdapter().normalize_kbar_payload(
        [_row("09:00:00")], symbol="2330", session_date=DAY, fetched_at="2026-11-10T08:30:00Z",
        is_trading_day=lambda d: d != date(2026, 11, 10))
    assert res.rejected_non_trading_day and res.records == ()


def test_malformed_and_foreign_rows_dropped():
    rows = [_row("09:00:00"), _row("09:01:00", sid="2317"), {"date": DAY, "minute": "bad", "stock_id": "2330"},
            _row("09:02:00", v=None)]
    res = FinMindTaiwanKBarAdapter().normalize_kbar_payload(
        rows, symbol="2330", session_date=DAY, fetched_at="2026-11-10T08:30:00Z")
    assert res.kept_bars == 1 and res.dropped_malformed == 3


def test_credential_missing_raises_no_placeholder(monkeypatch):
    monkeypatch.delenv("FINMIND_API_TOKEN", raising=False)
    with pytest.raises(FinMindCredentialError):
        FinMindLiveFetcher().fetch_kbar("2330", DAY)


def test_fetch_kbar_request_shape_and_fetched_at(monkeypatch):
    seen = {}

    def fake_fetch_dataset(self, dataset, **kw):
        seen.update(dataset=dataset, **kw)
        return {"status": 200, "data": []}, {"http_status": 200}

    monkeypatch.setattr(FinMindLiveFetcher, "fetch_dataset", fake_fetch_dataset)
    _, meta = FinMindLiveFetcher().fetch_kbar("2330", DAY)
    assert seen == {"dataset": "TaiwanStockKBar", "symbol": "2330", "start_date": DAY, "end_date": None}
    assert meta["fetched_at"].endswith("Z")


class _Clock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t


def test_budget_caps_at_limit_and_resets_next_hour():
    clk = _Clock(3600 * 100 + 10)
    b = KBarHourlyBudget(3, clock=clk)
    assert [b.acquire() for _ in range(4)] == [True, True, True, False]
    assert b.seconds_until_next_window() <= 3600
    clk.t += 3600
    assert b.acquire()
    assert KBarHourlyBudget().limit == 4500


def test_402_stops_until_next_hour_without_retry(monkeypatch):
    calls = []

    def boom(self, symbol, date, **kw):
        calls.append(symbol)
        raise fm.FinMindQuotaError("402")

    monkeypatch.setattr(FinMindLiveFetcher, "fetch_kbar", boom)
    clk = _Clock(3600 * 7 + 5)
    b = KBarHourlyBudget(clock=clk)
    f = FinMindLiveFetcher()
    r1 = fetch_kbar(f, "2330", DAY, budget=b)
    r2 = fetch_kbar(f, "2317", DAY, budget=b)
    assert r1.status == "quota_402" and r2.status == "budget_exhausted"
    assert calls == ["2330"]  # no second network call in the blocked hour
    clk.t += 3600
    assert b.acquire()


def test_volume_unit_probe_detects_shares_vs_lots():
    shares = measure_volume_unit([{"volume": 600_000}, {"volume": 400_000}], 1_000_000)
    lots = measure_volume_unit([{"volume": 600}, {"volume": 400}], 1_000_000)
    off = measure_volume_unit([{"volume": 5}], 1_000_000)
    assert shares["unit_factor"] == 1 and lots["unit_factor"] == 1000 and off["unit_factor"] is None


def test_range_probe_classification():
    one = [_row("09:00:00")]
    multi = [_row("09:00:00", d="2026-11-09"), _row("09:00:00", d="2026-11-10")]
    assert classify_range_probe(one, multi, start_date="2026-11-09", end_date="2026-11-10")["range_supported"]
    assert not classify_range_probe(one, one, start_date="2026-11-09", end_date="2026-11-10")["range_supported"]
    assert not classify_range_probe(one, None, start_date="a", end_date="b", range_error="HTTP 400")["range_supported"]


def test_provider_path_fetches_through_shared_budget(monkeypatch):
    from services.source_ingestion import provider_adapters as pa

    monkeypatch.setenv("FINMIND_API_TOKEN", "x")
    budget = KBarHourlyBudget(1)
    monkeypatch.setattr(pa, "_finmind_shared_kbar_budget", lambda: budget)
    calls = []

    def fake(self, symbol, date, **kw):
        calls.append(symbol)
        return {"status": 200, "data": [_row("09:00:00")]}, {"fetched_at": "2026-11-10T08:00:00Z"}

    monkeypatch.setattr(FinMindLiveFetcher, "fetch_kbar", fake)
    req = {"symbol": "2330", "date": DAY}
    pa._finmind_kbar(FinMindTaiwanKBarAdapter(), req, "t")
    assert calls == ["2330"]
    with pytest.raises(pa.SourceEvidenceError):
        pa._finmind_kbar(FinMindTaiwanKBarAdapter(), req, "t")
    assert calls == ["2330"]  # budget spent: no second network call


def test_provider_path_missing_credential_no_network(monkeypatch):
    from services.source_ingestion import provider_adapters as pa

    monkeypatch.delenv("FINMIND_API_TOKEN", raising=False)
    with pytest.raises(FinMindCredentialError):
        pa._finmind_kbar(FinMindTaiwanKBarAdapter(), {"symbol": "2330", "date": DAY}, "t")


def test_dispatch_conflicting_duplicate_fails_closed():
    from services.source_ingestion import provider_adapters as pa

    payload = {"data": [_row("09:00:00", c=101.0), _row("09:00:00", c=102.0)]}
    req = {"symbol": "2330", "date": DAY, "payload": payload, "fetched_at": "2026-11-10T08:00:00Z"}
    with pytest.raises(pa.SourceEvidenceError, match="conflicting"):
        pa._finmind_kbar(FinMindTaiwanKBarAdapter(), req, "t")


def test_dispatch_rejects_non_trading_day_default_and_calendar():
    from services.source_ingestion import provider_adapters as pa

    sunday = "2026-11-08"
    req = {"symbol": "2330", "date": sunday, "payload": {"data": [_row("09:00:00", d=sunday)]},
           "fetched_at": "2026-11-08T08:00:00Z"}
    with pytest.raises(pa.SourceEvidenceError, match="non-trading"):
        pa._finmind_kbar(FinMindTaiwanKBarAdapter(), req, "t")
    holiday = {"symbol": "2330", "date": DAY, "payload": {"data": [_row("09:00:00")]},
               "fetched_at": "2026-11-10T08:00:00Z", "is_trading_day": lambda d: False}
    with pytest.raises(pa.SourceEvidenceError, match="non-trading"):
        pa._finmind_kbar(FinMindTaiwanKBarAdapter(), holiday, "t")
    ok = {**holiday, "is_trading_day": lambda d: True}
    assert len(pa._finmind_kbar(FinMindTaiwanKBarAdapter(), ok, "t")) == 1


def test_volume_unit_real_probe_residual_still_lots():
    # real 2026-11-12 probe: official/KBar-sum = 1069.4 (6.9 % residual) -> lots
    r = measure_volume_unit([{"volume": 34201.0}], 36_575_797)
    assert r["unit_factor"] == 1000 and 0.05 < r["unexplained_residual_ratio"] < 0.09


def test_after_hours_1430_bar_excluded_and_counted():
    rows = [_row("09:00:00"), _row("13:30:00"), _row("14:30:00")]
    res = FinMindTaiwanKBarAdapter().normalize_kbar_payload(
        {"data": rows}, symbol="2330", session_date=DAY, fetched_at="2026-11-10T08:30:00Z",
    )
    assert res.kept_bars == 2 and res.dropped_outside_session == 1
    assert all("14:30" not in r.metadata["normalized_row"]["bar_start"] for r in res.records)


def test_complete_raw_response_survives_filtering_and_rejection():
    rows = [_row("09:00:00"), _row("09:00:00"), _row("13:33:00"),
            _row("14:30:00"), _row("bad")]
    adapter = FinMindTaiwanKBarAdapter()
    kwargs = dict(symbol="2330", session_date=DAY, fetched_at="2026-11-10T08:30:00Z")
    res = adapter.normalize_kbar_payload({"data": rows}, **kwargs)
    assert res.raw_rows == tuple(rows)
    assert res.kept_bars == 1 and res.dropped_outside_session == 2
    assert res.dropped_malformed == 1 and res.identical_duplicates == 1
    assert res.unsupported_session_minutes == ("2026-11-10T13:33:00+08:00",)
    assert res.response_content_hash == res.records[0].metadata["response_content_hash"]
    assert res.records[0].metadata["normalization_summary"]["dropped_outside_session"] == 2
    rejected = adapter.normalize_kbar_payload(rows, **kwargs, is_trading_day=lambda _: False)
    assert rejected.records == () and rejected.raw_rows == tuple(rows)
    assert rejected.response_content_hash == res.response_content_hash
    # Even a change to an excluded row must change the full-response hash.
    changed = adapter.normalize_kbar_payload([*rows[:-1], _row("bad", v=99)], **kwargs)
    assert changed.response_content_hash != res.response_content_hash


def test_dispatch_quarantines_unsupported_delayed_close_without_widening_session():
    from services.source_ingestion import provider_adapters as pa

    rows = [_row("09:00:00"), _row("13:30:00"), _row("13:33:00")]
    request = {"symbol": "2330", "date": DAY, "payload": {"data": rows},
               "fetched_at": "2026-11-10T08:30:00Z"}
    with pytest.raises(pa.SourceEvidenceError, match="unsupported session shape"):
        pa._finmind_kbar(FinMindTaiwanKBarAdapter(), request, "t")


@pytest.mark.parametrize("minute", ["09x00:00", "09:00:59", "09:00:00.1", "09:00:00+08:00"])
def test_invalid_minute_is_retained_raw_without_rounding(minute):
    row = _row(minute)
    res = FinMindTaiwanKBarAdapter().normalize_kbar_payload(
        [row], symbol="2330", session_date=DAY, fetched_at="2026-11-10T08:30:00Z")
    assert res.records == () and res.dropped_malformed == 1 and res.raw_rows == (row,)


@pytest.mark.parametrize("total,official,residual", [
    (34201, 36575797, 0.06492809985794705),
    (30457, 33056476, 0.07863742039532587),
    (44604, 45344218, 0.016324418694352607),
    (23834, 24491770, 0.02685677678665119),
])
def test_observed_unit_inference_does_not_approve_daily_reconciliation(total, official, residual):
    # Aggregates from the permitted nonsecret receipt include 14:30 after-hours;
    # they cannot establish regular-session reconciliation, even below 2 %.
    result = measure_volume_unit([{"volume": total}], official)
    assert result["unit_factor"] == 1000
    assert result["relative_error"] == pytest.approx(residual)
    assert result["inference_scope"] == "sample_only"
    assert result["reconciliation_status"] == "not_evaluated"


def test_kbar_not_in_shared_catalog_and_generic_adapter_fails_closed():
    from services.source_ingestion.connectors.finmind_taiwan import FinMindTaiwanDatasetAdapter
    from services.source_ingestion import persona_source_reconciler as psr

    assert all(d["dataset"] != "TaiwanStockKBar" for d in FINMIND_TAIWAN_DATASETS)
    # persona reconciler must not auto-provision tw_price_1m through the generic adapter
    assert "tw_price_1m" not in psr._FINMIND_DATASET_BY_NORMALIZED
    import pytest

    with pytest.raises(ValueError):
        FinMindTaiwanDatasetAdapter().records_from_data_payload("TaiwanStockKBar", {"data": [_row("09:00:00")]})
