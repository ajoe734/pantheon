"""K2: newest-first resumable backfill, 4,500/h limiter, coverage (SD §4)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from services.source_ingestion.connectors.finmind_taiwan import (
    FinMindCredentialError,
    FinMindQuotaError,
    KBarHourlyBudget,
)
from services.source_ingestion.intraday_backfill import (
    DEFAULT_HISTORY_DAYS,
    DEFAULT_UNIVERSE_SIZE,
    coverage_report,
    governed_trading_days,
    run_backfill,
)
from services.source_ingestion.intraday_store import IntradayStore

GB = 1024**3
DAYS = [date(2026, 11, 10), date(2026, 11, 9), date(2026, 11, 6)]  # intentionally any order


def _rows(symbol, day, n=271, close=100.0):
    start = datetime.fromisoformat(f"{day}T09:00:00")
    return [{"date": day, "minute": (start + timedelta(minutes=i)).strftime("%H:%M:%S"), "stock_id": symbol,
             "open": 100.0, "high": 101.0, "low": 99.0, "close": close, "volume": 5} for i in range(n)]


class FakeFetcher:
    def __init__(self, *, fail=None, empty=(), rows=None):
        self.calls = []
        self.fail = fail or {}
        self.empty = set(empty)
        self.rows = rows or {}

    def fetch_kbar(self, symbol, day):
        self.calls.append((symbol, day))
        if (symbol, day) in self.fail:
            raise self.fail[(symbol, day)]
        if (symbol, day) in self.empty:
            data = []
        else:
            data = self.rows.get((symbol, day)) or _rows(symbol, day)
        return {"data": data}, {"fetched_at": f"{day}T08:30:00Z", "request_url": "https://x?token=SECRET"}


def _store(tmp_path, free=1000 * GB):
    return IntradayStore(tmp_path, usage_fn=lambda p: SimpleNamespace(free=free))


def _run(store, fetcher, symbols=("2330", "2317"), **kw):
    kw.setdefault("budget", KBarHourlyBudget(4500, clock=lambda: 0))
    kw.setdefault("trading_days", DAYS)
    return run_backfill(store, fetcher, list(symbols), end_date=date(2026, 11, 10),
                        is_trading_day=lambda d: True, **kw)


def test_defaults_match_contract():
    assert DEFAULT_UNIVERSE_SIZE == 200 and DEFAULT_HISTORY_DAYS == 730
    assert KBarHourlyBudget().limit == 4500


def test_newest_first_complete_271_bar_sessions_and_raw_persisted(tmp_path):
    store, fetcher = _store(tmp_path), FakeFetcher()
    report = _run(store, fetcher)
    assert report.status == "completed" and report.requests == 6 and report.written == 6
    assert [d for _, d in fetcher.calls] == ["2026-11-10"] * 2 + ["2026-11-09"] * 2 + ["2026-11-06"] * 2
    assert len(store.read_rows("2330", "2026-11-10")) == 271
    raw = store.raw_path("2330", "2026-11-10")
    assert raw.exists()
    assert "SECRET" not in raw.read_bytes().decode("latin1")


def test_rerun_is_noop_zero_requests(tmp_path):
    store = _store(tmp_path)
    _run(store, FakeFetcher())
    again = FakeFetcher()
    report = _run(store, again)
    assert again.calls == [] and report.requests == 0 and report.skipped_already_complete == 6


def test_resume_after_max_requests_continues_oldest_side(tmp_path):
    store = _store(tmp_path)
    first = _run(store, FakeFetcher(), max_requests=3)
    assert first.status == "max_requests_reached" and first.requests == 3
    second_fetcher = FakeFetcher()
    second = _run(store, second_fetcher)
    assert second.status == "completed" and len(second_fetcher.calls) == 3
    assert ("2330", "2026-11-10") not in second_fetcher.calls or ("2317", "2026-11-10") not in second_fetcher.calls


def test_hourly_limit_stops_and_reports_resume(tmp_path):
    store, fetcher = _store(tmp_path), FakeFetcher()
    report = _run(store, fetcher, budget=KBarHourlyBudget(4, clock=lambda: 0))
    assert report.status == "budget_exhausted" and len(fetcher.calls) == 4
    assert report.resume_after_seconds > 0 and report.budget["limit_per_hour"] == 4


def test_quota_402_stops_without_retry(tmp_path):
    store = _store(tmp_path)
    fetcher = FakeFetcher(fail={("2317", "2026-11-10"): FinMindQuotaError("402")})
    budget = KBarHourlyBudget(4500, clock=lambda: 0)
    report = _run(store, fetcher, budget=budget)
    assert report.status == "quota_402" and len(fetcher.calls) == 2 and report.resume_after_seconds > 0
    assert budget.snapshot()["blocked_by_402"]
    assert _run(store, fetcher, budget=budget).status == "budget_exhausted"  # same hour: no retry


def test_credential_error_stops_immediately_no_data(tmp_path):
    store = _store(tmp_path)
    fetcher = FakeFetcher(fail={("2330", "2026-11-10"): FinMindCredentialError("no token")})
    report = _run(store, fetcher)
    assert report.status == "credential_error" and len(fetcher.calls) == 1
    assert store.read_manifest("2026-11-10") is None


def test_disk_guard_refuses_to_start(tmp_path):
    fetcher = FakeFetcher()
    report = _run(_store(tmp_path, free=1 * GB), fetcher)
    assert report.status == "disk_budget_exhausted" and fetcher.calls == []
    assert report.detail["required_free_bytes"] >= 20 * GB


def test_disk_guard_stops_mid_run(tmp_path):
    state = {"free": 1000 * GB}
    store = IntradayStore(tmp_path, usage_fn=lambda p: SimpleNamespace(free=state["free"]))

    class Draining(FakeFetcher):
        def fetch_kbar(self, symbol, day):
            if len(self.calls) == 2:
                state["free"] = 1 * GB
            return super().fetch_kbar(symbol, day)

    fetcher = Draining()
    report = _run(store, fetcher)
    assert report.status == "disk_budget_exhausted" and len(fetcher.calls) == 4  # finishes the date, stops before the next


def test_conflicting_duplicate_and_unsupported_session_are_quarantined(tmp_path):
    store = _store(tmp_path)
    dup = _rows("2330", "2026-11-10", n=3) + [{**_rows("2330", "2026-11-10", n=1)[0], "close": 55.0}]
    late = _rows("2317", "2026-11-10") + [{**_rows("2317", "2026-11-10", n=1)[0], "minute": "13:33:00"}]
    fetcher = FakeFetcher(rows={("2330", "2026-11-10"): dup, ("2317", "2026-11-10"): late})
    report = _run(store, fetcher, trading_days=[date(2026, 11, 10)])
    assert report.quarantined == 2
    assert store.quarantine_path("2330", "2026-11-10").exists()
    assert store.quarantine_path("2317", "2026-11-10").exists()
    assert store.read_rows("2330", "2026-11-10") == []


def test_non_trading_day_empty_and_short_day_not_padded(tmp_path):
    store = _store(tmp_path)
    fetcher = FakeFetcher(empty={("2330", "2026-11-10")}, rows={("2317", "2026-11-10"): _rows("2317", "2026-11-10", n=50)})
    holiday = lambda d: d != date(2026, 11, 9)
    run_backfill(store, fetcher, ["2330", "2317"], end_date=date(2026, 11, 10), trading_days=DAYS[:2],
                 is_trading_day=holiday, budget=KBarHourlyBudget(4500, clock=lambda: 0))
    assert store.symbol_day_status("2330", "2026-11-10") == "provider_empty"
    assert len(store.read_rows("2317", "2026-11-10")) == 50  # no fabricated bars
    assert store.symbol_day_status("2330", "2026-11-09") == "non_trading_day"


def test_universe_cap(tmp_path):
    with pytest.raises(ValueError):
        _run(_store(tmp_path), FakeFetcher(), symbols=[str(i) for i in range(201)])


def test_coverage_reports_actual_history_and_gaps(tmp_path):
    store = _store(tmp_path)
    fetcher = FakeFetcher(empty={("2330", "2026-11-09")})
    _run(store, fetcher, max_requests=5)  # 2317 on the oldest day not fetched
    cov = coverage_report(store, ["2330", "2317"], end_date=date(2026, 11, 10), trading_days=DAYS)
    a, b = cov["symbols"]["2330"], cov["symbols"]["2317"]
    assert a["covered_days"] == 2 and a["provider_empty_days"] == ["2026-11-09"] and a["missing_count"] == 0
    assert a["newest_covered"] == "2026-11-10" and a["oldest_covered"] == "2026-11-06"
    assert b["covered_days"] == 2 and b["missing_days"] == ["2026-11-06"]


def test_governed_calendar_excludes_weekend_and_holiday():
    days = governed_trading_days(date(2026, 10, 5), date(2026, 10, 12))
    isos = [d.isoformat() for d in days]
    assert isos == sorted(isos, reverse=True)
    assert "2026-10-10" not in isos and "2026-10-09" not in isos  # Sat / governed closure
