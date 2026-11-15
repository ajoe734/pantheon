"""TW-SUSPENSION-HOLIDAYS-INTAKE-20261115: official holiday schedule and trading suspension records become
governed datasets (``tw_trading_holidays`` / ``tw_trading_suspension``) through the real collector bundle
connector, and the market feed attests survivorship from them.

Every response is a recorded official TWSE / TPEx response (``deploy/collector/tests/fixtures``) run through the
real collector spiders and the production pull entry point.  No capture with personal data exists in them.
Nothing here sets an ``includes_*`` flag: the feed derives it from the stored partitions.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.source_ingestion import candidate_pool_market_feed as feed
from services.source_ingestion.connector_definitions import collector_host, get_connector_definition
from services.source_ingestion.connectors.base import SourceEvidenceError
from services.source_ingestion.connectors.collector_bundle import (
    COLLECTOR_BUNDLE_ADAPTER_TOKEN, BundleTransport, CollectorBundleAdapter)
from services.source_ingestion.market_data_storage import MarketDataStorageWriter
from services.source_ingestion.provider_adapters import execute_provider_owned_adapter

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "deploy/collector/tests/fixtures"
OFFICIAL = FIXTURES / "tw_suspension_holidays"
HOLIDAYS, SUSPENSION = "collector-tw-trading-holidays", "collector-tw-trading-suspension"
TWSE_PRICE, TPEX_PRICE = "collector-tw-twse-price-history", "collector-tw-tpex-price-history"
TWSE_PAGE = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20261110&type=ALLBUT0999&response=json"
TPEX_PAGE = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=2026/06/18&id=&response=json"
INPUTS = [{"name": "price", "dataset": "tw_price_daily", "field": "close"}]


@pytest.fixture(autouse=True)
def _clear():
    feed._CACHE.clear()


def _collector():
    sys.path.insert(0, str(REPO / "deploy/collector"))
    try:
        from collector_stack import spiders
        from collector_stack.config import DEFAULT_POLICY_PATH, load_collector_config
        from collector_stack.fetchers import FixtureTransport
        from collector_stack.privacy import AuthorHasher
        from collector_stack.runner import CollectorRunner
    finally:
        sys.path.remove(str(REPO / "deploy/collector"))
    return spiders, DEFAULT_POLICY_PATH, load_collector_config, FixtureTransport, AuthorHasher, CollectorRunner


def collector_files(tmp_path: Path, *, holidays=True, suspension=True, twse_page=True, tpex_page=True,
                    suspension_end="2026-11-15") -> dict[str, bytes]:
    """Run the real collector spiders over the recorded official responses; return the bundle tree."""
    spiders, policy, load_config, Transport, Hasher, Runner = _collector()
    transport = Transport()
    for year in (2024, 2025, 2026):
        transport.register(spiders.TwTradingHolidayScheduleSpider.build_twse_url(year),
                           (OFFICIAL / f"twse_holiday_schedule_{year}.json").read_bytes())
        transport.register(spiders.TwTradingHolidayScheduleSpider.build_tpex_url(year),
                           (OFFICIAL / f"tpex_trading_date_{year}.json").read_bytes())
        transport.register(spiders.TwTradingSuspensionSpider.build_tpex_url(year),
                           (OFFICIAL / f"tpex_sprc_his_{year}.json").read_bytes())
    transport.register(
        spiders.TwTradingSuspensionSpider.build_twse_url(
            start_yyyymmdd="20240101", end_yyyymmdd=suspension_end.replace("-", "")),
        (OFFICIAL / "twse_twtawu_2024_2026.json").read_bytes())
    transport.register(TWSE_PAGE, (FIXTURES / "twse_mi_index_full_20261110.json").read_bytes())
    transport.register(TPEX_PAGE, (OFFICIAL / "tpex_daily_quotes_20260618_excerpt.json").read_bytes())
    tmp_path.mkdir(parents=True, exist_ok=True)
    salt = tmp_path / "salt"
    salt.write_bytes(b"holidays-intake-test-salt-0123456789abcdef-0123456789")
    root = tmp_path / "collector_root"
    runner = Runner(load_config(policy, env={}), root_dir=root, hasher=Hasher.from_file(salt), transport=transport)
    runs = []
    if holidays:
        runs.append((HOLIDAYS, "holidays-2024-2026", {}, True))
    if suspension:
        runs.append((SUSPENSION, "suspension-2024-2026", {"end_date": suspension_end}, True))
    if twse_page:
        runs.append((TWSE_PRICE, "close-2026-11-10", {"dates": ["2026-11-10"]}, False))
    if tpex_page:
        runs.append((TPEX_PRICE, "backfill-2026-06-18", {"dates": ["2026-06-18"]}, False))
    for source_id, run_id, kwargs, disabled in runs:  # the two new sources are disabled by default
        outcome = runner.run_source(source_id, run_id=run_id, started_at="2026-11-15T09:00:00Z",
                                    finished_at="2026-11-15T09:05:00Z", collect_kwargs=kwargs or None,
                                    allow_disabled=disabled)
        assert outcome.status == "complete", (source_id, outcome.status)
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def pull(files, tmp_path, monkeypatch, source_id, *, max_records=2000):
    """Production pull entry point (execute_provider_owned_adapter) over a collector bundle tree."""
    class _Transport(BundleTransport):
        def read(self, relative_path: str, *, max_bytes: int) -> bytes:
            return files[relative_path]

    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: _Transport())
    monkeypatch.setenv("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT", str(tmp_path / "raw_store"))
    definition = get_connector_definition(source_id)
    config = {"source_id": source_id, "source_type": "market", "dataset": definition.datasets[0],
              "attribution_required": True, "max_records": max_records}
    adapter = CollectorBundleAdapter(connector_id=source_id, **config)
    records = execute_provider_owned_adapter(
        connector=adapter.connector(),
        fetch={"adapter": COLLECTOR_BUNDLE_ADAPTER_TOKEN, "adapter_config": {**config, "host": collector_host()},
               "request": {}, "max_records": max_records},
        trace_id="tw-suspension-holidays-intake")
    return adapter, records


def store_all(files, root: Path, tmp_path, monkeypatch, sources=(HOLIDAYS, SUSPENSION, TWSE_PRICE, TPEX_PRICE)):
    """Pull every source through the production entry point and write it with the production storage writer."""
    by_source = {}
    for source_id in sources:
        adapter, records = pull(files, tmp_path / source_id, monkeypatch, source_id)
        MarketDataStorageWriter(root).write_run(
            result=SimpleNamespace(records=list(records), run=SimpleNamespace(ingest_run_id=f"run-{source_id}")),
            connector=adapter.connector())
        by_source[source_id] = records
    return by_source


@pytest.fixture(scope="module")
def official_files(tmp_path_factory):
    return collector_files(tmp_path_factory.mktemp("official"))


def test_sources_are_registered_definitions_and_admitted_additively():
    desired = json.loads((Path(__file__).resolve().parents[1] / "default_desired_state.json").read_text())
    admitted = {(r["dataset"], c) for p in desired["personas"] for r in p["required_data_sources"]
                for c in r["connector_candidates"]}
    for source_id, dataset in ((HOLIDAYS, "tw_trading_holidays"), (SUSPENSION, "tw_trading_suspension")):
        definition = get_connector_definition(source_id)
        assert definition.adapter_token == COLLECTOR_BUNDLE_ADAPTER_TOKEN and definition.datasets == (dataset,)
        assert definition.metadata["collector"]["source_id"] == source_id
        assert (dataset, source_id) in admitted and source_id not in desired["collector_ingestion_exclusions"]
    # the existing collector requirements are untouched
    assert ("tw_index_daily", "collector-tw-twse-index-breadth") in admitted
    assert ("tw_institutional_flow", "collector-tw-twse-t86") in admitted


def test_holiday_schedule_becomes_governed_rows_with_point_in_time_fields(official_files, tmp_path, monkeypatch):
    _, records = pull(official_files, tmp_path, monkeypatch, HOLIDAYS)
    rows = [r.metadata["normalized_row"] for r in records]
    assert {r["venue"] for r in rows} == {"TWSE", "TPEx"} and all(r["dataset"] == "tw_trading_holidays" for r in rows)
    assert all(r["date"] == r["trade_date"] and isinstance(r["closed"], bool) and r["api_endpoint"] for r in rows)
    closed = {(r["venue"], r["date"]) for r in rows if r["closed"]}
    assert {("TWSE", "2026-09-25"), ("TPEx", "2026-09-25"), ("TWSE", "2026-10-09"), ("TPEx", "2026-10-26")} <= closed
    assert any(not r["closed"] for r in rows)  # a "開始交易" marker row is not a closure
    sample = records[0].metadata
    # available at the verified fetch, never back-dated to the holiday; bounded provenance of its bundle
    assert sample["available_time"] == "2026-11-15T09:05:00Z" and sample["event_time"] <= sample["available_time"]
    assert sample["collector"]["bundle_sha256"] and sample["collector"]["source_id"] == HOLIDAYS
    assert sample["raw_uri"].startswith(f"raw/{HOLIDAYS}/tw_trading_holidays/")


def test_suspension_records_keep_the_raw_official_values(official_files, tmp_path, monkeypatch):
    _, records = pull(official_files, tmp_path, monkeypatch, SUSPENSION)
    rows = [r.metadata["normalized_row"] for r in records]
    assert {r["venue"] for r in rows} == {"TWSE", "TPEx"}
    twse_2314 = [r for r in rows if r["venue"] == "TWSE" and r["symbol"] == "2314" and r["suspended_date"] == "2026-11-10"]
    assert len(twse_2314) == 1
    row = twse_2314[0]  # recorded official TWTAWU row [2310, "2314", "台揚", "115/11/10", "8:00", "115/11/11", "8:00"]
    assert (row["serial_no"], row["security_name"], row["suspended_date_raw"], row["resumed_date"],
            row["resumed_time_raw"]) == (2310, "台揚", "115/11/10", "2026-11-11", "8:00")
    assert "close" not in row  # a suspension record never becomes a price, so no latest-close projection


def test_not_traded_rows_of_the_real_price_pages_are_stored_and_never_priced(official_files, tmp_path, monkeypatch):
    root = tmp_path / "store"
    by_source = store_all(official_files, root, tmp_path, monkeypatch, sources=(TWSE_PRICE, TPEX_PRICE))
    twse = [r.metadata["normalized_row"] for r in by_source[TWSE_PRICE]]
    assert len(twse) == 1389 and sum(1 for r in twse if r["trading_status"] == "not_traded") == 14
    bars, sessions, captures = feed._read_bars(feed._partitions(root, "tw_price_daily"))
    assert not any(symbol.startswith("2314") for symbol in bars)  # no price was invented for 2314
    twse_capture = captures[("2026-11-10", "TWSE")]
    assert "2314.TWSE" in twse_capture["non_traded"] and len(twse_capture["non_traded"]) == 14
    assert captures[("2026-06-18", "TPEx")]["non_traded"] == {"1788.TPEx"}
    assert twse_capture["ref"].startswith("bundle://collector-tw-twse-price-history/tw_price_daily/2026-11-10@MI_INDEX#")


def test_suspension_is_known_only_where_page_and_official_list_both_cover_the_session(official_files, tmp_path, monkeypatch):
    root = tmp_path / "store"
    store_all(official_files, root, tmp_path, monkeypatch)
    _, _, captures = feed._read_bars(feed._partitions(root, "tw_price_daily"))
    known, unknown = feed._suspension_captures(
        feed._partitions(root, "tw_trading_suspension"), ["2026-06-18", "2026-11-10", "2026-11-16"], captures)
    # TWSE 2026-11-10: the page's 14 no-close rows plus the TWTAWU record (2314 halted 11/10, resumes 11/11)
    assert "2314.TWSE" in known[("2026-11-10", "TWSE")]["suspended_symbols"]
    assert known[("2026-06-18", "TPEx")]["suspended_symbols"] and "1788.TPEx" in known[("2026-06-18", "TPEx")]["suspended_symbols"]
    # no stored page for TPEx on 11-10 and for any venue on 11-16: nothing is known, nothing is guessed
    assert ("2026-11-10", "TPEx") not in known and ("2026-11-16", "TWSE") not in known


def test_list_requested_before_the_session_does_not_cover_it(tmp_path, monkeypatch):
    files = collector_files(tmp_path / "files", suspension_end="2026-11-09")  # TWTAWU requested through 11-09 only
    root = tmp_path / "store"
    store_all(files, root, tmp_path, monkeypatch)
    _, _, captures = feed._read_bars(feed._partitions(root, "tw_price_daily"))
    known, unknown = feed._suspension_captures(feed._partitions(root, "tw_trading_suspension"), ["2026-11-10"], captures)
    assert ("2026-11-10", "TWSE") not in known and "covering 2026-11-10" in unknown[("2026-11-10", "TWSE")]


def test_one_venue_closed_does_not_account_for_a_day(official_files, tmp_path, monkeypatch):
    root = tmp_path / "store"
    store_all(official_files, root, tmp_path, monkeypatch, sources=(HOLIDAYS,))
    closed = feed._read_closed_days(feed._partitions(root, "tw_trading_holidays"))
    assert "2026-09-25" in closed and "twse.com.tw" in closed["2026-09-25"]["ref"] and "tpex.org.tw" in closed["2026-09-25"]["ref"]
    # keep only the TWSE rows: no day is closed on both venues any more
    only_twse = [p for p in feed._partitions(root, "tw_trading_holidays")]
    rows = []
    for path in only_twse:
        rows += [json.loads(line) for line in path.read_text().splitlines() if '"venue": "TWSE"' in line]
    twse_dir = tmp_path / "twse_only" / "normalized" / "tw_trading_holidays" / "date=2026-11-15"
    twse_dir.mkdir(parents=True)
    (twse_dir / "r.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert feed._read_closed_days(feed._partitions(tmp_path / "twse_only", "tw_trading_holidays")) == {}


def test_malformed_rows_and_changed_formats_fail_closed():
    mapper = CollectorBundleAdapter._survivorship_evidence_row
    holiday = {"dataset": "tw_trading_holidays", "trade_date": "2026-09-25", "venue": "TWSE", "is_closed": True,
               "api_endpoint": "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?date=20260101&response=json"}
    assert mapper("i", holiday, {})["closed"] is True
    for bad in ({**holiday, "venue": "OTC"}, {**holiday, "trade_date": ""}, {**holiday, "is_closed": "yes"},
                {**holiday, "api_endpoint": ""}):
        with pytest.raises(SourceEvidenceError):
            mapper("i", bad, {})
    suspension = {"dataset": "tw_trading_suspension", "trade_date": "2026-11-10", "venue": "TWSE", "symbol": "2314",
                  "suspended_date": "2026-11-10", "api_endpoint": holiday["api_endpoint"]}
    assert mapper("i", suspension, {})["date"] == "2026-11-10"
    for bad in ({**suspension, "symbol": ""}, {k: v for k, v in suspension.items() if k != "suspended_date"}):
        with pytest.raises(SourceEvidenceError):
            mapper("i", bad, {})


def test_without_any_evidence_nothing_is_attested(tmp_path):
    path = tmp_path / "normalized" / "tw_price_daily" / "date=2026-10-30"
    path.mkdir(parents=True)
    (path / "r.jsonl").write_text(json.dumps({"metadata": {"available_time": "2026-10-30T18:00:00+08:00", "normalized_row": {
        "symbol_canonical": "2330.TWSE", "date": "2026-10-30", "close": 10.0, "volume": 1.0}}}) + "\n")
    market = feed.build_candidate_pool_market(tmp_path, INPUTS)
    assert market["includes_delisted"] is False and market["includes_suspended"] is False
