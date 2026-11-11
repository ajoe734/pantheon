"""Price-history backfill: date ranges, holiday skipping, resume, event time, daily-run behaviour."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from collector_stack.backfill import (
    TW_EXCHANGE_HOLIDAYS,
    estimate,
    resolve_dates,
    run_backfill,
    trading_dates,
    two_year_range,
)
from collector_stack.cli import main as cli_main
from collector_stack.config import DEFAULT_POLICY_PATH, load_collector_config
from collector_stack.fetchers import FetchResponse, FixtureTransport
from collector_stack.runner import CollectorRunner
from collector_stack.spiders import TwseDailyPriceHistorySpider

TWSE = "collector-tw-twse-price-history"
TPEX = "collector-tw-tpex-price-history"
T86 = "collector-tw-twse-t86"
NO_DATA = {"stat": "很抱歉，沒有符合條件的資料!"}


def _transport(fixtures_dir: Path, fixture: str, prefix: str, *, compact: bool, closed=()) -> FixtureTransport:
    base = json.loads((fixtures_dir / fixture).read_text(encoding="utf-8"))

    def route(url: str) -> FetchResponse:
        raw = url.split("date=")[1].split("&")[0]
        iso = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}" if compact else raw.replace("/", "-")
        payload = dict(NO_DATA) if iso in closed else {**base, "date": raw}
        return FetchResponse(
            requested_url=url, final_url=url, http_status=200,
            body=json.dumps(payload).encode("utf-8"), headers={},
        )

    transport = FixtureTransport()
    transport.register(prefix, route)
    return transport


def _runner(tmp_path: Path, test_hasher, transport) -> CollectorRunner:
    return CollectorRunner(
        load_collector_config(DEFAULT_POLICY_PATH), root_dir=tmp_path, hasher=test_hasher, transport=transport
    )


def test_range_yields_one_request_per_trading_day_and_skips_holidays() -> None:
    # 2026-02-09 (Mon) .. 2026-02-23 (Mon): LNY closure 02-12..02-20 and weekends are skipped.
    assert trading_dates("2026-02-09", "2026-02-23") == [
        "2026-02-09", "2026-02-10", "2026-02-11", "2026-02-23",
    ]
    assert "2026-01-01" in TW_EXCHANGE_HOLIDAYS
    assert trading_dates("2026-01-01", "2026-01-01") == []


def test_trading_day_count_and_two_year_estimate() -> None:
    dates = resolve_dates(start_date=None, end_date="2026-11-10", trading_days=5, now_iso="2026-11-11T00:00:00Z")
    assert dates == ["2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10"]
    start, end = two_year_range("2026-11-11T02:00:00Z")
    assert end == "2026-11-10" and start == "2024-11-10"
    n = len(trading_dates(start, end))
    assert 470 <= n <= 500
    assert estimate(trading_dates(start, end))["estimated_wall_seconds"] == n * 5.0


def test_backfill_one_request_per_date_with_event_time_and_gap(tmp_path: Path, fixtures_dir: Path, test_hasher) -> None:
    transport = _transport(
        fixtures_dir, "twse_mi_index_20261110.json",
        "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX", compact=True,
    )
    sleeps: list[float] = []
    dates = trading_dates("2026-02-09", "2026-02-23")
    result = run_backfill(_runner(tmp_path, test_hasher, transport), TWSE, dates, sleep_fn=sleeps.append)
    assert result.complete and result.fetched == dates
    assert len(transport.calls) == len(dates) == 4
    assert all("MI_INDEX" in c["url"] and "stockNo" not in c["url"] for c in transport.calls)
    assert sleeps == [5.0] * 3  # >= 5s between requests, none before the first
    for d in dates:
        bundle = next((tmp_path / "bundles" / TWSE).glob(f"*/backfill-{d}"))
        rows = [json.loads(line) for line in (bundle / "items.jsonl").read_text().splitlines()]
        assert rows
        assert {r["published_at"][:10] for r in rows} == {d}  # event time = own trade date


def test_backfill_resumes_without_refetching_and_records_confirmed_holiday(
    tmp_path: Path, fixtures_dir: Path, test_hasher
) -> None:
    dates = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]
    prefix = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
    transport = _transport(fixtures_dir, "tpex_daily_quotes_20261110.json", prefix, compact=False, closed={"2026-11-03"})
    runner = _runner(tmp_path, test_hasher, transport)
    first = run_backfill(runner, TPEX, dates[:2], sleep_fn=lambda _s: None)
    assert first.fetched == ["2026-11-02"] and first.holidays_confirmed == ["2026-11-03"]
    assert len(transport.calls) == 2

    second = run_backfill(runner, TPEX, dates, sleep_fn=lambda _s: None)
    assert second.skipped_already_done == dates[:2]
    assert second.fetched == dates[2:]
    assert len(transport.calls) == 4  # only the two new dates were requested


def test_t86_and_cli_dry_run(tmp_path: Path, capsys) -> None:
    code = cli_main(["--root-dir", str(tmp_path), "backfill", "--source", T86, "--two-years", "--dry-run"])
    assert code == 0
    plan = json.loads(capsys.readouterr().out)
    assert 470 <= plan["requests"] <= 500
    assert plan["min_interval_seconds"] == 5.0


def test_daily_run_fetches_only_newest_published_trading_day(fixtures_dir: Path) -> None:
    transport = _transport(
        fixtures_dir, "twse_mi_index_20261110.json",
        "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX", compact=True,
    )
    spider = TwseDailyPriceHistorySpider(
        load_collector_config(DEFAULT_POLICY_PATH).get_source(TWSE), transport=transport,
        sleep_fn=lambda _s: None,
    )
    spider.collect(fetched_at="2026-11-11T02:00:00Z")  # 10:00 Taipei, 11-10 is newest published
    assert len(transport.calls) == 1 and "date=20261110" in transport.calls[0]["url"]
