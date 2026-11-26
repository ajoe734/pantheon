"""Tests for COLLECTOR-TW-SUSPENSION-HOLIDAYS-20261115 (AC 1 through AC 6)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from collector_stack.bundle import BundleWriter
from collector_stack.config import (
    DEFAULT_POLICY_PATH,
    EXCHANGE_SURVIVORSHIP_SOURCE_IDS,
    CollectorConfigError,
    load_collector_config,
    validate_collector_config,
)
from collector_stack.fetchers import FixtureTransport
from collector_stack.privacy import AuthorHasher
from collector_stack.runner import CollectorRunner, SourceDisabledError
from collector_stack.spiders import (
    TpexDailyPriceHistorySpider,
    TwseDailyPriceHistorySpider,
    TwseRegimeParseError,
    TwTradingHolidayScheduleSpider,
    TwTradingSuspensionSpider,
    estimate_two_year_survivorship_recovery_plan,
)

COLLECTOR_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SURVIVORSHIP_FIXTURES_DIR = FIXTURES_DIR / "tw_suspension_holidays"


def _register_holiday_fixtures(transport: FixtureTransport) -> None:
    for year in (2024, 2025, 2026):
        transport.register(
            TwTradingHolidayScheduleSpider.build_twse_url(year),
            (SURVIVORSHIP_FIXTURES_DIR / f"twse_holiday_schedule_{year}.json").read_bytes(),
        )
        transport.register(
            TwTradingHolidayScheduleSpider.build_tpex_url(year),
            (SURVIVORSHIP_FIXTURES_DIR / f"tpex_trading_date_{year}.json").read_bytes(),
        )


def _register_suspension_fixtures(
    transport: FixtureTransport, *, end_yyyymmdd: str = "20261115"
) -> None:
    transport.register(
        TwTradingSuspensionSpider.build_twse_url(
            start_yyyymmdd="20240101", end_yyyymmdd=end_yyyymmdd
        ),
        (SURVIVORSHIP_FIXTURES_DIR / "twse_twtawu_2024_2026.json").read_bytes(),
    )
    for year in (2024, 2025, 2026):
        transport.register(
            TwTradingSuspensionSpider.build_tpex_url(year),
            (SURVIVORSHIP_FIXTURES_DIR / f"tpex_sprc_his_{year}.json").read_bytes(),
        )


def test_provenance_manifest_matches_recorded_official_fixtures() -> None:
    prov = json.loads((SURVIVORSHIP_FIXTURES_DIR / "provenance.json").read_text(encoding="utf-8"))
    assert prov["task_id"] == "COLLECTOR-TW-SUSPENSION-HOLIDAYS-20261115"
    for entry in prov["files"]:
        fpath = SURVIVORSHIP_FIXTURES_DIR / entry["path"]
        raw = fpath.read_bytes()
        assert len(raw) == entry["size"]
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"]


def test_twse_and_tpex_price_spiders_keep_suspended_and_not_traded_rows_without_inventing_prices(
    tmp_path: Path,
) -> None:
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    transport = FixtureTransport()
    twse_url = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20261110&type=ALLBUT0999&response=json"
    tpex_url = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=2026/06/18&id=&response=json"
    transport.register(twse_url, (FIXTURES_DIR / "twse_mi_index_full_20261110.json").read_bytes())
    transport.register(
        tpex_url,
        (SURVIVORSHIP_FIXTURES_DIR / "tpex_daily_quotes_20260618_excerpt.json").read_bytes(),
    )

    twse_spider = TwseDailyPriceHistorySpider(
        config.get_source("collector-tw-twse-price-history"),
        transport=transport,
        sleep_fn=lambda _: None,
    )
    twse_items = twse_spider.collect(dates=["2026-11-10"], fetched_at="2026-11-10T08:00:00Z")
    # All 1,389 listed securities in tables[8] are retained (1,375 traded + 14 "--" close rows).
    assert len(twse_items) == 1389
    twse_by_symbol = {item.extra_fields["symbol"]: item for item in twse_items}
    not_traded_twse = [item for item in twse_items if item.extra_fields["not_traded"]]
    assert len(not_traded_twse) == 14
    assert {item.extra_fields["symbol"] for item in not_traded_twse} >= {
        "2314",
        "1538",
        "1312A",
        "2891B",
        "6958A",
    }

    halted_2314 = twse_by_symbol["2314"]
    assert halted_2314.extra_fields["security_name"] == "台揚"
    assert halted_2314.extra_fields["open"] is None
    assert halted_2314.extra_fields["high"] is None
    assert halted_2314.extra_fields["low"] is None
    assert halted_2314.extra_fields["close"] is None
    assert halted_2314.extra_fields["change"] is None
    assert halted_2314.extra_fields["not_traded"] is True
    assert halted_2314.extra_fields["suspended_or_not_traded"] is True
    assert halted_2314.extra_fields["trading_status"] == "not_traded"
    assert halted_2314.extra_fields["raw_close"] == "--"
    assert halted_2314.metrics["close"] is None
    assert halted_2314.metrics["not_traded"] is True

    traded_2330 = twse_by_symbol["2330"]
    assert traded_2330.extra_fields["close"] is not None and traded_2330.extra_fields["close"] > 0
    assert traded_2330.extra_fields["not_traded"] is False
    assert traded_2330.extra_fields["suspended_or_not_traded"] is False
    assert traded_2330.extra_fields["trading_status"] == "traded"

    tpex_spider = TpexDailyPriceHistorySpider(
        config.get_source("collector-tw-tpex-price-history"),
        transport=transport,
        sleep_fn=lambda _: None,
    )
    tpex_items = tpex_spider.collect(dates=["2026-06-18"], fetched_at="2026-06-18T08:00:00Z")
    assert len(tpex_items) == 4
    tpex_by_symbol = {item.extra_fields["symbol"]: item for item in tpex_items}
    halted_1788 = tpex_by_symbol["1788"]
    assert halted_1788.extra_fields["security_name"] == "杏昌"
    assert halted_1788.extra_fields["open"] is None
    assert halted_1788.extra_fields["high"] is None
    assert halted_1788.extra_fields["low"] is None
    assert halted_1788.extra_fields["close"] is None
    assert halted_1788.extra_fields["change"] is None
    assert halted_1788.extra_fields["not_traded"] is True
    assert halted_1788.extra_fields["suspended_or_not_traded"] is True
    assert halted_1788.extra_fields["trading_status"] == "not_traded"
    assert halted_1788.extra_fields["raw_close"] == "---"
    assert halted_1788.metrics["close"] is None
    assert halted_1788.metrics["not_traded"] is True

    traded_6488 = tpex_by_symbol["6488"]
    assert traded_6488.extra_fields["close"] == 1110.0
    assert traded_6488.extra_fields["not_traded"] is False
    assert traded_6488.extra_fields["trading_status"] == "traded"


def test_existing_price_consumers_skip_not_traded_rows_with_unchanged_outputs(
    tmp_path: Path,
) -> None:
    from services.source_ingestion.candidate_pool_market_feed import _bar
    from services.source_ingestion.connectors.collector_bundle import (
        CollectorBundleAdapter,
        LocalDirectoryBundleTransport,
    )
    from services.source_ingestion.connectors.taiwan_official import (
        TaiwanOfficialMarketDatasetAdapter,
    )
    from services.source_ingestion.feature_catalogue import observations_from_row
    from services.source_ingestion.requirement_state import LatestMarketSnapshotStore

    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    transport = FixtureTransport()
    tpex_url = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=2026/06/18&id=&response=json"
    transport.register(
        tpex_url,
        (SURVIVORSHIP_FIXTURES_DIR / "tpex_daily_quotes_20260618_excerpt.json").read_bytes(),
    )
    policy = config.get_source("collector-tw-tpex-price-history")
    spider = TpexDailyPriceHistorySpider(policy, transport=transport, sleep_fn=lambda _: None)
    all_items = spider.collect(dates=["2026-06-18"], fetched_at="2026-06-18T08:00:00Z")
    traded_only_items = [item for item in all_items if not item.extra_fields["not_traded"]]
    assert len(all_items) == 4 and len(traded_only_items) == 3

    # Write two bundles: one with all rows (including suspended 1788) and one with traded rows only.
    root_all = tmp_path / "all_rows"
    root_traded = tmp_path / "traded_only"
    BundleWriter(root_all, collector_version=config.collector_version).write_bundle(
        policy=policy,
        run_id="tpex-all",
        items=all_items,
        status="complete",
        started_at="2026-06-18T08:00:00Z",
        finished_at="2026-06-18T08:00:05Z",
    )
    BundleWriter(root_traded, collector_version=config.collector_version).write_bundle(
        policy=policy,
        run_id="tpex-traded",
        items=traded_only_items,
        status="complete",
        started_at="2026-06-18T08:00:00Z",
        finished_at="2026-06-18T08:00:05Z",
    )

    records_all = (
        CollectorBundleAdapter(
            connector_id="collector-tw-tpex-price-history",
            source_id="collector-tw-tpex-price-history",
            source_type="market",
            dataset="tw_price_daily",
            raw_root=str(tmp_path / "raw_all"),
            transport=LocalDirectoryBundleTransport(root_all),
        )
        .pull()
        .records
    )
    records_traded = (
        CollectorBundleAdapter(
            connector_id="collector-tw-tpex-price-history",
            source_id="collector-tw-tpex-price-history",
            source_type="market",
            dataset="tw_price_daily",
            raw_root=str(tmp_path / "raw_traded"),
            transport=LocalDirectoryBundleTransport(root_traded),
        )
        .pull()
        .records
    )
    # CollectorBundleAdapter admits the non-traded record with close=None and not_traded=True.
    assert len(records_all) == 4 and len(records_traded) == 3
    records_traded_same_bundle = [
        r for r in records_all if not r.metadata["normalized_row"]["not_traded"]
    ]
    assert len(records_traded_same_bundle) == 3
    rec_1788 = next(
        r for r in records_all if r.metadata["normalized_row"]["symbol"] == "1788"
    )
    assert rec_1788.metadata["normalized_row"].get("close") is None
    assert rec_1788.metadata["normalized_row"]["not_traded"] is True

    # 1. candidate_pool_market_feed._bar skips the non-traded row and produces identical bars.
    bars_all = [
        b
        for r in records_all
        if (b := _bar(r.metadata, r.metadata["normalized_row"])) is not None
    ]
    bars_traded = [
        b
        for r in records_traded_same_bundle
        if (b := _bar(r.metadata, r.metadata["normalized_row"])) is not None
    ]
    assert len(bars_all) == 3
    assert bars_all == bars_traded

    # 2. LatestMarketSnapshotStore skips the non-traded row and produces identical snapshots.
    snap_all = LatestMarketSnapshotStore(tmp_path / "snap_all.json").append_normalized_records(
        records_all,
        ingest_run_id="run-1",
        observed_at="2026-06-18T08:05:00Z",
    )
    snap_traded = LatestMarketSnapshotStore(
        tmp_path / "snap_traded.json"
    ).append_normalized_records(
        records_traded_same_bundle,
        ingest_run_id="run-1",
        observed_at="2026-06-18T08:05:00Z",
    )
    assert snap_all == snap_traded
    assert snap_all["accepted_record_count"] == 3

    # 3. feature_catalogue.observations_from_row emits no OHLC price observations for close=None.
    obs_1788 = observations_from_row(
        "tw_price_daily",
        {**rec_1788.metadata["normalized_row"], "date": "2026-06-18"},
        ingest_time="2026-06-18T08:05:00Z",
    )
    assert {o["field"] for o in obs_1788}.isdisjoint(
        {"open", "high", "low", "close", "change"}
    )

    # 4. TaiwanOfficialMarketDatasetAdapter.records_from_price_history_payload skips close=None rows.
    official_adapter = TaiwanOfficialMarketDatasetAdapter()
    assert (
        official_adapter.records_from_price_history_payload(
            "1788.TPEX",
            "TPEx",
            {"bundle_records": [r.metadata["normalized_row"] for r in records_all]},
            available_time="2026-06-18T08:05:00Z",
        )
        == ()
    )
    assert official_adapter.records_from_price_history_payload(
        "6488.TPEX",
        "TPEx",
        {"bundle_records": [r.metadata["normalized_row"] for r in records_all]},
        available_time="2026-06-18T08:05:00Z",
    ) == official_adapter.records_from_price_history_payload(
        "6488.TPEX",
        "TPEx",
        {"bundle_records": [r.metadata["normalized_row"] for r in records_traded]},
        available_time="2026-06-18T08:05:00Z",
    )


def test_official_holiday_schedule_spider_and_bundle_2024_through_2026(
    tmp_path: Path,
) -> None:
    from services.execution.market_snapshot_admission import TW_GOVERNED_CALENDAR_PINS

    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    transport = FixtureTransport()
    _register_holiday_fixtures(transport)

    runner = CollectorRunner(
        config,
        root_dir=tmp_path,
        hasher=AuthorHasher(b"test-salt-holidays-32-bytes-long!"),
        transport=transport,
    )
    # Disabled by default until operator deployment (AC 5)
    with pytest.raises(SourceDisabledError):
        runner.run_source("collector-tw-trading-holidays", run_id="holidays-blocked")

    outcome = runner.run_source(
        "collector-tw-trading-holidays",
        run_id="holidays-2024-2026",
        started_at="2026-11-15T09:00:00Z",
        finished_at="2026-11-15T09:00:35Z",
        allow_disabled=True,
    )
    assert outcome.status == "complete"
    assert outcome.bundle.manifest["status"] == "complete"
    assert len(transport.calls) == 6

    items_lines = [
        json.loads(line)
        for line in (outcome.bundle.bundle_dir / "items.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(items_lines) == 145  # 71 TWSE rows + 74 TPEx rows across 2024, 2025, 2026
    venues = {item["venue"] for item in items_lines}
    years = {item["year"] for item in items_lines}
    assert venues == {"TWSE", "TPEx"}
    assert years == {2024, 2025, 2026}

    # Verify a specific holiday and raw values + official publication reference (AC 3, AC 6)
    twse_lny_2026 = next(
        item
        for item in items_lines
        if item["venue"] == "TWSE" and item["date"] == "2026-02-17"
    )
    assert twse_lny_2026["is_closed"] is True
    assert twse_lny_2026["market_status"] == "closed"
    assert "春節" in twse_lny_2026["holiday_name"]
    assert (
        twse_lny_2026["official_publication_ref"]
        == "https://www.twse.com.tw/zh/trading/holiday.html"
    )
    assert twse_lny_2026["raw_values"]["fields"] == ["日期", "名稱", "說明"]

    tpex_nat_2024 = next(
        item
        for item in items_lines
        if item["venue"] == "TPEx" and item["date"] == "2024-10-10"
    )
    assert tpex_nat_2024["is_closed"] is True
    assert tpex_nat_2024["holiday_name"] == "國慶日"
    assert (
        tpex_nat_2024["official_publication_ref"]
        == "https://www.tpex.org.tw/zh-tw/announce/market/holiday.html"
    )
    assert tpex_nat_2024["raw_values"]["headers"] == ["紀念節日名稱", "日期", "星期", "說明"]

    # Pinned 2026 TWSE schedule for market admission remains unchanged.
    assert "twse-2026-schedule-v1" in TW_GOVERNED_CALENDAR_PINS


def _write_listing_for_fixture_symbols(path: Path, transport: FixtureTransport, *, end_yyyymmdd: str) -> None:
    import re
    from services.source_ingestion import security_listing as sl

    symbols: set[str] = set()
    twse = json.loads((SURVIVORSHIP_FIXTURES_DIR / "twse_twtawu_2024_2026.json").read_text(encoding="utf-8"))
    symbols.update(str(r[1]).strip() for r in twse["data"])
    for year in (2024, 2025, 2026):
        t = json.loads((SURVIVORSHIP_FIXTURES_DIR / f"tpex_sprc_his_{year}.json").read_text(encoding="utf-8"))
        symbols.update(str(r[2]).strip() for r in t["tables"][0]["data"])
    warrant_codes = re.compile(r"^0[3-8]\d{3}[0-9A-Za-z]$")  # test-only: classify the recorded fixture codes
    state = sl.ListingState(last_success_date="2026-11-15")
    for sym in sorted(symbols):
        if not warrant_codes.match(sym):
            state.allowlist[sym] = {"venue": "TWSE", "category": "股票", "cfi": "ESVUFR", "is_warrant": False,
                                    "first_seen": "2026-11-15", "last_seen": "2026-11-15"}
    state.save(path)


def test_official_trading_suspension_spider_and_bundle_2024_through_2026(
    tmp_path: Path,
) -> None:
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    transport = FixtureTransport()
    _register_suspension_fixtures(transport, end_yyyymmdd="20261115")

    runner = CollectorRunner(
        config,
        root_dir=tmp_path,
        hasher=AuthorHasher(b"test-salt-suspension-32-bytes-ok!"),
        transport=transport,
    )
    # The exchange listing (not a symbol pattern) decides which codes are collected: list every
    # fixture code that is not a warrant code (the recorded fixtures carry 6-character warrant codes).
    _write_listing_for_fixture_symbols(runner.listing_state_path, transport, end_yyyymmdd="20261115")
    with pytest.raises(SourceDisabledError):
        runner.run_source("collector-tw-trading-suspension", run_id="suspension-blocked")

    outcome = runner.run_source(
        "collector-tw-trading-suspension",
        run_id="suspension-2024-2026",
        started_at="2026-11-15T09:05:00Z",
        finished_at="2026-11-15T09:05:25Z",
        allow_disabled=True,
        collect_kwargs={"end_date": "2026-11-15"},
    )
    assert outcome.status == "complete"
    assert len(transport.calls) == 4

    items_lines = [
        json.loads(line)
        for line in (outcome.bundle.bundle_dir / "items.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(items_lines) == 179  # 62 TWSE non-warrant rows + 117 TPEx rows across 2024-2026
    assert {item["venue"] for item in items_lines} == {"TWSE", "TPEx"}

    # Verify TWSE 2314 台揚 suspended on 2026-11-10 (115/11/10) and resumed 2026-11-11 (115/11/11)
    twse_2314 = next(
        item
        for item in items_lines
        if item["venue"] == "TWSE"
        and item["symbol"] == "2314"
        and item["suspended_date"] == "2026-11-10"
    )
    assert twse_2314["security_name"] == "台揚"
    assert twse_2314["suspended_date_raw"] == "115/11/10"
    assert twse_2314["resumed_date"] == "2026-11-11"
    assert twse_2314["resumed_date_raw"] == "115/11/11"
    assert (
        twse_2314["official_publication_ref"]
        == "https://www.twse.com.tw/zh/trading/historical/twtawu.html"
    )
    assert twse_2314["raw_values"]["fields"] == TwTradingSuspensionSpider.TWSE_FIELDS

    # Verify TPEx 1788 杏昌 suspended on 2026-06-18 (115/06/18) and resumed 2026-06-22 (115/06/22)
    tpex_1788_halt = next(
        item
        for item in items_lines
        if item["venue"] == "TPEx"
        and item["symbol"] == "1788"
        and item["suspended_date"] == "2026-06-18"
    )
    assert tpex_1788_halt["security_name"] == "杏昌"
    assert tpex_1788_halt["suspended_date_raw"] == "115/06/18"
    assert tpex_1788_halt["resumed_date"] is None
    assert (
        tpex_1788_halt["official_publication_ref"]
        == "https://www.tpex.org.tw/zh-tw/announce/market/halt/historical.html"
    )
    tpex_1788_resume = next(
        item
        for item in items_lines
        if item["venue"] == "TPEx"
        and item["symbol"] == "1788"
        and item["resumed_date"] == "2026-06-22"
    )
    assert tpex_1788_resume["suspended_date"] is None
    assert tpex_1788_resume["resumed_date_raw"] == "115/06/22"


@pytest.mark.parametrize(
    ("source_id", "corrupt_target", "corrupt_payload", "expected_match"),
    [
        (
            "collector-tw-trading-holidays",
            "twse_2025",
            {"stat": "ok", "fields": ["日期", "名稱"], "data": [["2025-01-01", "元旦"]]},
            "changed fields",
        ),
        (
            "collector-tw-trading-holidays",
            "tpex_2026",
            {"stat": "ok", "data": {"html": "<table><tr><td>無標題</td></tr></table>"}},
            "missing holiday schedule table",
        ),
        (
            "collector-tw-trading-suspension",
            "twse",
            {"stat": "OK", "fields": ["證券代號", "暫停交易日期"], "data": [["2314", "115/11/10"]]},
            "changed fields",
        ),
        (
            "collector-tw-trading-suspension",
            "tpex_2026",
            {"stat": "ok", "tables": [{"fields": ["代號"], "data": [["1788"]]}]},
            "changed fields",
        ),
    ],
)
def test_holiday_and_suspension_spiders_fail_closed_on_format_change(
    tmp_path: Path,
    source_id: str,
    corrupt_target: str,
    corrupt_payload: dict,
    expected_match: str,
) -> None:
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    transport = FixtureTransport()
    _register_holiday_fixtures(transport)
    _register_suspension_fixtures(transport, end_yyyymmdd="20261115")

    corrupt_bytes = json.dumps(corrupt_payload, ensure_ascii=False).encode("utf-8")
    if corrupt_target == "twse_2025":
        transport.register(TwTradingHolidayScheduleSpider.build_twse_url(2025), corrupt_bytes)
    elif corrupt_target == "tpex_2026" and source_id == "collector-tw-trading-holidays":
        transport.register(TwTradingHolidayScheduleSpider.build_tpex_url(2026), corrupt_bytes)
    elif corrupt_target == "twse":
        transport.register(
            TwTradingSuspensionSpider.build_twse_url(
                start_yyyymmdd="20240101", end_yyyymmdd="20261115"
            ),
            corrupt_bytes,
        )
    else:
        transport.register(TwTradingSuspensionSpider.build_tpex_url(2026), corrupt_bytes)

    runner = CollectorRunner(
        config,
        root_dir=tmp_path,
        hasher=AuthorHasher(b"test-salt-fail-closed-32-bytes!!"),
        transport=transport,
        warrants_mode="off",
    )
    collector = runner.build_collector(config.get_source(source_id))
    kwargs = {"end_date": "2026-11-15"} if source_id == "collector-tw-trading-suspension" else {}
    with pytest.raises(TwseRegimeParseError, match=expected_match):
        collector.collect(**kwargs)

    outcome = runner.run_source(
        source_id,
        run_id=f"fail-closed-{corrupt_target}",
        started_at="2026-11-15T09:10:00Z",
        finished_at="2026-11-15T09:10:05Z",
        allow_disabled=True,
        collect_kwargs=kwargs,
    )
    assert outcome.status == "partial"
    assert outcome.bundle.manifest["items"] == []
    assert expected_match in (outcome.bundle.manifest.get("reason") or "")


def test_two_year_recovery_plan_and_policy_and_readme_contracts() -> None:
    plan = estimate_two_year_survivorship_recovery_plan()
    assert plan["recollected_in_this_task"] is False
    assert plan["requests_executed_for_backfill"] == 0
    assert (
        plan["retained_raw_capture_recovery"][
            "per_item_price_bundle_raw_files_recoverable_dates"
        ]
        == 0
    )
    recollect = plan["bounded_recollection_plan"]
    assert recollect["published_rate_limit"] == {
        "max_connections": 1,
        "min_interval_seconds": 5.0,
    }
    assert recollect["price_history_recollection_requests"] == 976
    assert recollect["holiday_and_suspension_requests"] == 10
    assert recollect["total_requests"] == 986
    assert recollect["total_estimated_wall_seconds"] == 4930.0

    partial_plan = estimate_two_year_survivorship_recovery_plan(
        retained_full_page_dates_twse=40,
        retained_full_page_dates_tpex=35,
    )
    assert (
        partial_plan["retained_raw_capture_recovery"]["retained_full_page_new_requests"]
        == 0
    )
    assert (
        partial_plan["bounded_recollection_plan"]["sources"][
            "collector-tw-twse-price-history"
        ]["requests_required"]
        == 448
    )
    assert (
        partial_plan["bounded_recollection_plan"]["sources"][
            "collector-tw-tpex-price-history"
        ]["requests_required"]
        == 453
    )

    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    for sid in EXCHANGE_SURVIVORSHIP_SOURCE_IDS:
        src = config.get_source(sid)
        assert src.enabled is False
        assert src.max_connections == 1
        assert src.min_interval_seconds >= 5.0
        assert src.offering(env={})["offered"] is False

    raw = json.loads(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
    raw["sources"]["collector-tw-trading-holidays"]["min_interval_seconds"] = 2.0
    with pytest.raises(CollectorConfigError, match="min_interval_seconds must be >= 5.0"):
        validate_collector_config(raw, env={})

    readme = (COLLECTOR_DIR / "README.md").read_text(encoding="utf-8")
    for token in (
        "collector-tw-trading-holidays",
        "collector-tw-trading-suspension",
        "pantheon-collector@collector-tw-trading-holidays.service",
        "pantheon-collector@collector-tw-trading-suspension.service",
        "Rollback step",
        "986",
    ):
        assert token in readme
