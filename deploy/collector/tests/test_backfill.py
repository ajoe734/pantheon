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


def test_daily_default_date_skips_exchange_closures_for_all_sources(fixtures_dir: Path) -> None:
    from collector_stack.spiders import latest_published_trading_date

    # 2026-02-23 10:00 Taipei: Fri 02-20 .. Thu 02-12 are closures, so newest published day is 02-11.
    assert latest_published_trading_date("2026-02-23T02:00:00Z") == "2026-02-11"
    transport = _transport(
        fixtures_dir, "twse_mi_index_20261110.json",
        "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX", compact=True,
    )
    spider = TwseDailyPriceHistorySpider(
        load_collector_config(DEFAULT_POLICY_PATH).get_source(TWSE), transport=transport,
        sleep_fn=lambda _s: None,
    )
    spider.collect(fetched_at="2026-02-23T02:00:00Z")
    assert len(transport.calls) == 1 and "date=20260211" in transport.calls[0]["url"]


def test_non_lny_closures_match_official_twse_schedule() -> None:
    from collector_stack.spiders import latest_published_trading_date

    for day in ("2025-09-29", "2025-10-24", "2025-12-25", "2026-09-28", "2026-10-26", "2026-12-25"):
        assert day in TW_EXCHANGE_HOLIDAYS
    # Teachers' Day 09-28 and the Retrocession Day make-up 10-26 are skipped in ranges.
    assert trading_dates("2026-09-24", "2026-09-29") == ["2026-09-24", "2026-09-29"]
    assert trading_dates("2026-10-22", "2026-10-27") == ["2026-10-22", "2026-10-23", "2026-10-27"]
    # 10:00 Taipei on 2026-10-27: newest published day is Fri 10-23, not the closed Mon 10-26.
    assert latest_published_trading_date("2026-10-27T02:00:00Z") == "2026-10-23"
    assert latest_published_trading_date("2026-09-29T02:00:00Z") == "2026-09-24"


@pytest.mark.parametrize(
    "spider_name,fixture,prefix,compact,expected",
    [
        ("TwseDailyPriceHistorySpider", "twse_mi_index_20261110.json",
         "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX", True, "date=20261023"),
        ("TpexDailyPriceHistorySpider", "tpex_daily_quotes_20261110.json",
         "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes", False, "date=2026/10/23"),
        ("TwseT86DailySpider", "twse_t86_20261110.json",
         "https://www.twse.com.tw/rwd/zh/fund/T86", True, "date=20261023"),
    ],
)
def test_daily_selection_skips_non_lny_closure_for_all_sources(
    fixtures_dir: Path, spider_name, fixture, prefix, compact, expected
) -> None:
    from collector_stack import spiders

    cls = getattr(spiders, spider_name)
    transport = _transport(fixtures_dir, fixture, prefix, compact=compact)
    spider = cls(load_collector_config(DEFAULT_POLICY_PATH).get_source(cls.source_id), transport=transport,
                 sleep_fn=lambda _s: None)
    try:
        spider.collect(fetched_at="2026-10-27T02:00:00Z")
    except Exception:  # fixture payload shape is irrelevant; only the requested date matters
        pass
    assert len(transport.calls) == 1 and expected in transport.calls[0]["url"]


def test_run_rejects_oversized_range_before_any_request(tmp_path: Path, capsys) -> None:
    salt = tmp_path / "salt"
    salt.write_text("x" * 64, encoding="utf-8")
    with pytest.raises(SystemExit):
        cli_main([
            "--root-dir", str(tmp_path), "--salt-file", str(salt), "run", "--source", TWSE,
            "--start-date", "2024-01-01", "--end-date", "2026-11-10",
        ])
    err = capsys.readouterr().err
    assert "backfill" in err and "no request was made" in err


def test_taiex_month_range_cli_and_resume(tmp_path, fixtures_dir, test_hasher, capsys):
    from collector_stack.backfill import MONTHLY_SOURCE, resolve_months
    from collector_stack.spiders import TwseTaiexHistorySpider
    assert resolve_months('2012-01', '2012-03') == ['2012-01', '2012-02', '2012-03']
    assert resolve_months('2012-12', '2013-01') == ['2012-12', '2013-01']
    assert cli_main(['backfill', '--source', MONTHLY_SOURCE, '--start-month', '2012-01',
                     '--end-month', '2026-11', '--dry-run']) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan['requests'] == 179 and plan['min_interval_seconds'] == 5.0
    assert plan['first_date'] == '2012-01' and plan['last_date'] == '2026-11'
    transport = FixtureTransport({TwseTaiexHistorySpider.build_date_url('2012-01'):
                                 (fixtures_dir / 'twse_mi_5mins_hist_201201.json').read_bytes()})
    runner = _runner(tmp_path, test_hasher, transport)
    first = run_backfill(runner, MONTHLY_SOURCE, ['2012-01'], sleep_fn=lambda _: None)
    assert first.complete and first.fetched == ['2012-01']
    # February has no registered response: stop without completing its ledger entry.
    sleeps = []
    second = run_backfill(runner, MONTHLY_SOURCE, ['2012-01', '2012-02', '2012-03'], sleep_fn=sleeps.append)
    assert not second.complete and second.skipped_already_done == ['2012-01']
    assert second.stopped_at == '2012-02' and sleeps == []
    assert len(transport.calls) == 2
    ledger = json.loads((tmp_path / 'state/backfill' / f'{MONTHLY_SOURCE}.json').read_text())
    assert set(ledger['completed']) == {'2012-01'}


def test_taiex_month_request_spacing_without_fabricated_rows(tmp_path, fixtures_dir, test_hasher):
    from collector_stack.backfill import MONTHLY_SOURCE
    from collector_stack.spiders import TwseTaiexHistorySpider
    from datetime import datetime, timezone
    # Replay the full real January page as January. Then the real January page
    # served to a February request must fail closed after the five-second gap.
    transport = FixtureTransport({'https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST':
                                 (fixtures_dir / 'twse_mi_5mins_hist_201201.json').read_bytes()})
    sleeps = []
    runner = _runner(tmp_path, test_hasher, transport)
    result = run_backfill(runner, MONTHLY_SOURCE, ['2012-01', '2012-01-20', '2012-02'], sleep_fn=sleeps.append)
    assert result.fetched == ['2012-01'] and result.stopped_at == '2012-02'
    assert sleeps == [5.0] and len(transport.calls) == 2
    # While January is still current it must remain refreshable, not completed.
    other = _runner(tmp_path / 'current-month', test_hasher, transport)
    for _ in range(2):
        r = run_backfill(other, MONTHLY_SOURCE, ['2012-01'], sleep_fn=lambda _: None,
                         now_fn=lambda: datetime(2012, 1, 31, tzinfo=timezone.utc))
        assert r.fetched == ['2012-01'] and not r.skipped_already_done


@pytest.mark.parametrize('options', [
    ['--start-month', '2011-12'], ['--start-month', '2012-13'],
    ['--start-month', '2012-02', '--end-month', '2012-01'],
    ['--start-month', '2012-01', '--two-years'], ['--start-date', '2012-01-01'],
])
def test_invalid_month_options_fail_before_fetch(options):
    from collector_stack.backfill import MONTHLY_SOURCE
    with pytest.raises(SystemExit):
        cli_main(['backfill', '--source', MONTHLY_SOURCE, '--dry-run', *options])
