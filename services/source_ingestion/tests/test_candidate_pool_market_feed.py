"""candidate_pool_market.v1 is built from the governed normalized tw_price_daily partitions."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.registry.candidate_pool import _market
from services.source_ingestion import candidate_pool_market_feed as feed
from services.source_ingestion.routers.ingest_operations import create_ingest_operations_router

INPUTS = [{"name": "price", "dataset": "tw_price_daily", "field": "close"},
          {"name": "turnover", "dataset": "tw_price_daily", "field": "volume"}]


def _row(symbol, day, close, volume=1000, value=100000, available=None):
    return {"source_id": f"{symbol}-{day}", "dataset": "tw_price_daily", "metadata": {"normalized_row": {
        "date": day, "symbol_canonical": symbol, "symbol": symbol[:4], "venue": "TWSE", "close": close,
        "volume": volume, "value": value, "available_time": available or f"{day}T18:00:00+08:00"}}}


def _write(root, rows, run="run-1"):
    for row in rows:
        day = row["metadata"]["normalized_row"]["date"]
        path = root / "normalized" / "tw_price_daily" / f"date={day}" / f"{run}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(json.dumps(row) + "\n")


@pytest.fixture(autouse=True)
def _clear():
    feed._CACHE.clear()


def test_builds_a_market_the_screen_accepts(tmp_path):
    _write(tmp_path, [_row(s, d, 100 + i) for s in ("2330.TW", "2317.TW")
                      for i, d in enumerate(["2026-10-29", "2026-10-30"])])
    market = feed.build_candidate_pool_market(tmp_path, INPUTS, decision_day="2026-11-02")
    raw, symbols = _market(market)  # the registry's own validator
    assert raw["scope"] == "all_governed_tw" and set(symbols) == {"2330.TW", "2317.TW"}
    assert market["trading_sessions"] == ["2026-10-29", "2026-10-30"]
    assert market["calendar_coverage_through"] == "2026-11-02"
    bar = symbols["2330.TW"]["bars"][-1]
    assert bar["traded_value"] == 100000 and bar["suspended"] is False and bar["available_time"]


def test_zero_volume_is_suspended_and_missing_volume_is_not_imputed(tmp_path):
    rows = [_row("2330.TW", "2026-10-30", 10, volume=0), _row("2317.TW", "2026-10-30", 10)]
    del rows[1]["metadata"]["normalized_row"]["volume"], rows[1]["metadata"]["normalized_row"]["value"]
    _write(tmp_path, rows)
    bars = {s: p["bars"][-1] for s, p in feed.build_candidate_pool_market(tmp_path, INPUTS)["symbols"].items()}
    assert bars["2330.TW"]["suspended"] is True
    assert "suspended" not in bars["2317.TW"] and "traded_value" not in bars["2317.TW"]


def test_late_revision_replaces_the_earlier_version(tmp_path):
    _write(tmp_path, [_row("2330.TW", "2026-10-30", 10)])
    _write(tmp_path, [_row("2330.TW", "2026-10-30", 11, available="2026-10-31T09:00:00+08:00")], run="run-2")
    [bar] = feed.build_candidate_pool_market(tmp_path, INPUTS)["symbols"]["2330.TW"]["bars"]
    assert bar["close"] == 11


def test_missing_dataset_is_needs_data_naming_it(tmp_path):
    with pytest.raises(feed.CandidatePoolMarketNeedsData) as err:
        feed.build_candidate_pool_market(tmp_path, INPUTS)
    assert err.value.dataset == "tw_price_daily"


def test_missing_feature_dataset_is_needs_data_naming_it(tmp_path):
    _write(tmp_path, [_row("2330.TW", "2026-10-30", 10)])
    inputs = INPUTS + [{"name": "pe", "dataset": "tw_valuation_daily", "field": "pe_ratio"}]
    with pytest.raises(feed.CandidatePoolMarketNeedsData) as err:
        feed.build_candidate_pool_market(tmp_path, inputs)
    assert err.value.dataset == "tw_valuation_daily"


def test_unaccounted_weekday_gap_refuses_rather_than_guess_a_calendar(tmp_path):
    _write(tmp_path, [_row("2330.TW", "2026-10-28", 10)])
    with pytest.raises(feed.CandidatePoolMarketNeedsData, match="no governed holiday evidence"):
        feed.build_candidate_pool_market(tmp_path, INPUTS, decision_day="2026-11-02")
    # a weekend alone is not a gap
    feed.build_candidate_pool_market(tmp_path, INPUTS, decision_day="2026-10-29")


def test_second_call_reads_no_partition_until_one_changes(tmp_path, monkeypatch):
    _write(tmp_path, [_row("2330.TW", "2026-10-30", 10)])
    feed.build_candidate_pool_market(tmp_path, INPUTS, decision_day="2026-11-02")
    monkeypatch.setattr(feed, "_read_bars", lambda *_: (_ for _ in ()).throw(AssertionError("re-read")))
    feed.build_candidate_pool_market(tmp_path, INPUTS, decision_day="2026-11-02")
    _write(tmp_path, [_row("2330.TW", "2026-11-02", 12)])
    with pytest.raises(AssertionError):
        feed.build_candidate_pool_market(tmp_path, INPUTS, decision_day="2026-11-03")


def test_endpoint_serves_market_and_names_missing_dataset(tmp_path):
    app = FastAPI()
    app.include_router(create_ingest_operations_router(SimpleNamespace(MARKET_DATA_STORAGE_ROOT=tmp_path)))
    client = TestClient(app)
    missing = client.post("/api/source-ingest/candidate-pool-market", json={"inputs": INPUTS})
    assert missing.status_code == 404 and missing.json()["detail"] == {
        "code": "needs_data", "dataset": "tw_price_daily", "detail": missing.json()["detail"]["detail"]}
    _write(tmp_path, [_row("2330.TW", "2026-10-30", 10)])
    ok = client.post("/api/source-ingest/candidate-pool-market", json={"inputs": INPUTS})
    assert ok.status_code == 200 and ok.json()["schema_version"] == "candidate_pool_market.v1"
    assert client.post("/api/source-ingest/candidate-pool-market", json={"inputs": "x"}).status_code == 422


def test_market_without_capture_evidence_attests_nothing_and_names_missing_dataset(tmp_path):
    _write(tmp_path, [_row("2330.TW", "2026-10-29", 10), _row("2330.TW", "2026-10-30", 11)])
    market = feed.build_candidate_pool_market(tmp_path, INPUTS)
    assert market["includes_delisted"] is False and market["includes_suspended"] is False
    att = market["survivorship_attestation"]
    assert att["suspended"]["missing_dataset"].startswith("tw_trading_suspension")
    _, _ = _market(market)


# --- SOURCE-MARKET-STORE-BACKFILL-AND-CLEANUP-20261118-R2: bars through DailyBarsReader -----------------

def _bars_state():
    from services.source_ingestion.security_listing import ListingState

    state = ListingState(last_success_date="2026-11-17")
    for sym in ("2330", "2317"):
        state.allowlist[sym] = {"venue": "TWSE", "category": "x", "cfi": "ESVUFR", "is_warrant": False,
                                "first_seen": "2026-01-01", "last_seen": "2026-11-17"}
    return state


def _backfilled(tmp_path):
    pytest.importorskip("pyarrow")
    from scripts import backfill_daily_bars as bf

    _write(tmp_path, [_row(s, d, 100 + i, volume=i * 10) for s in ("2330.TW", "2317.TW")
                      for i, d in enumerate(["2026-10-29", "2026-10-30"])])
    bf.backfill(tmp_path, _bars_state(), today="2026-11-17", apply=True)


def _core(market):
    return {s: [{k: b[k] for k in ("event_time", "close", "open", "high", "low", "volume", "suspended") if k in b}
                for b in p["bars"]] for s, p in market["symbols"].items()}


def test_daily_bars_reader_gives_the_same_bars_and_sessions_and_rollback_setting(tmp_path, monkeypatch):
    _backfilled(tmp_path)
    monkeypatch.delenv("PANTHEON_DAILY_PRICE_READER", raising=False)
    old = feed.build_candidate_pool_market(tmp_path, INPUTS, decision_day="2026-11-02")
    feed._CACHE.clear()
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "daily_bars")
    new = feed.build_candidate_pool_market(tmp_path, INPUTS, decision_day="2026-11-02")
    assert _core(new) == _core(old)
    assert new["trading_sessions"] == old["trading_sessions"]
    assert new["calendar_coverage_through"] == old["calendar_coverage_through"]
    assert new["source_refs"][0].startswith("daily_bars@sha256:")
    _market(new)  # the registry's validator accepts it
    feed._CACHE.clear()
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "run_files")  # rollback state
    assert feed.build_candidate_pool_market(tmp_path, INPUTS, decision_day="2026-11-02")["source_refs"][0].startswith("normalized/")


def test_daily_bars_mode_has_no_run_files_needed_for_bars_and_attestation_fails_closed(tmp_path, monkeypatch):
    import shutil

    _backfilled(tmp_path)
    shutil.rmtree(tmp_path / "normalized")
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "daily_bars")
    market = feed.build_candidate_pool_market(tmp_path, INPUTS)
    assert set(market["symbols"]) == {"2330.TW", "2317.TW"}
    assert market["includes_delisted"] is False  # no capture evidence left: never assumed


def test_daily_bars_mode_names_the_dataset_when_daily_bars_are_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "daily_bars")
    with pytest.raises(feed.CandidatePoolMarketNeedsData) as err:
        feed.build_candidate_pool_market(tmp_path, INPUTS)
    assert err.value.dataset == "tw_price_daily"


def test_daily_bars_mode_cache_follows_the_manifests(tmp_path, monkeypatch):
    _backfilled(tmp_path)
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "daily_bars")
    first = feed.build_candidate_pool_market(tmp_path, INPUTS)
    assert feed.build_candidate_pool_market(tmp_path, INPUTS) is first


def test_switched_feed_lists_symbols_through_the_reader_not_the_store(tmp_path, monkeypatch):
    from services.source_ingestion import daily_bars_store as dbs

    _backfilled(tmp_path)
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "daily_bars")
    monkeypatch.setattr(dbs.DailyBarsStore, "symbol_stats",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("DailyBarsStore.symbol_stats used")))
    market = feed.build_candidate_pool_market(tmp_path, INPUTS)
    assert set(market["symbols"]) == {"2330.TW", "2317.TW"}
    assert all(len(p["bars"]) == 2 for p in market["symbols"].values())


def test_survivorship_attestation_reads_daily_bars_and_the_cleanup_guard_marks_it_switched():
    """G2 closed: in daily_bars mode the attestation reads status and lineage from daily bars; the guard lists
    the part as switched, and the old read stays in the file for rollback."""
    from scripts.cleanup_market_run_files import KNOWN_RUN_FILE_READERS

    parts = [r for r in KNOWN_RUN_FILE_READERS if r["file"].endswith("candidate_pool_market_feed.py")]
    assert {r["part"]: r["switched"] for r in parts} == {"bars and sessions": True, "survivorship attestation (G2)": True}
    text = open(feed.__file__, encoding="utf-8").read()
    assert "def _read_bars(" in text and "_read_captures_from_daily_bars" in text and "_read_bars_from_daily_bars" in text


# --- SOURCE-DAILY-BARS-VALUE-TIME-STATUS-COLUMNS-20261118: traded value and available time -----------------------

def _instant(text):
    from services.registry.factor_rule import _parse_timestamp_utc

    return _parse_timestamp_utc(text)


def test_daily_bars_feed_available_time_and_traded_value_equal_the_run_file_read(tmp_path, monkeypatch):
    pytest.importorskip("pyarrow")
    from scripts import backfill_daily_bars as bf

    rows = [_row("2330.TW", "2026-10-29", 100, volume=10, value=1234.5, available="2026-10-29T18:05:07.250000+08:00"),
            _row("2330.TW", "2026-10-30", 101, volume=0, value=0.0),  # a suspended day: zero value is a value
            _row("2317.TW", "2026-10-29", 50, volume=5, available="2026-10-29T16:00:00Z"),
            _row("2317.TW", "2026-10-30", 51, volume=5)]
    del rows[3]["metadata"]["normalized_row"]["value"]  # the source gave no value: no traded_value, never zero
    _write(tmp_path, rows)
    bf.backfill(tmp_path, _bars_state(), today="2026-11-17", apply=True)
    monkeypatch.delenv("PANTHEON_DAILY_PRICE_READER", raising=False)
    old = feed.build_candidate_pool_market(tmp_path, INPUTS)
    feed._CACHE.clear()
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "daily_bars")
    new = feed.build_candidate_pool_market(tmp_path, INPUTS)
    mismatches = 0
    for symbol, entry in old["symbols"].items():
        for a, b in zip(entry["bars"], new["symbols"][symbol]["bars"]):
            mismatches += _instant(a["available_time"]) != _instant(b["available_time"]) or a.get("traded_value") != b.get("traded_value")
    assert mismatches == 0
    assert new["symbols"]["2330.TW"]["bars"][0]["traded_value"] == 1234.5
    assert new["symbols"]["2330.TW"]["bars"][1]["traded_value"] == 0.0
    assert "traded_value" not in new["symbols"]["2317.TW"]["bars"][1]
    assert _instant(new["symbols"]["2330.TW"]["bars"][0]["available_time"]) == _instant("2026-10-29T10:05:07.250000Z")


def test_daily_bars_feed_omits_a_bar_with_no_available_time_and_never_invents_1430(tmp_path, monkeypatch):
    pytest.importorskip("pyarrow")
    from services.source_ingestion import daily_bars_store as dbs

    store = dbs.DailyBarsStore(tmp_path)
    store.write_day("2026-10-30", [{"market": "TW", "symbol": "2330.TW", "trade_date": "2026-10-30", "open": 1.0, "high": 1.0,
                                    "low": 1.0, "close": 1.0, "volume": 1.0, "adjusted": False, "source_id": "s",
                                    "ingest_run_id": "r", "lineage_ref": "r", "content_hash": "h"}], ingest_run_id="r")
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "daily_bars")
    with pytest.raises(feed.CandidatePoolMarketNeedsData, match="no usable daily bars"):
        feed.build_candidate_pool_market(tmp_path, INPUTS)


# --- the real collector pages (recorded official responses) through the production storage writer -------------

@pytest.fixture(scope="module")
def _official_files(tmp_path_factory):
    from services.source_ingestion.tests import test_tw_suspension_holidays_intake as intake

    return intake.collector_files(tmp_path_factory.mktemp("official"))


def test_daily_bars_captures_equal_the_run_file_captures_on_the_real_pages(_official_files, tmp_path, monkeypatch):
    """G2: the attestation reads status and lineage from daily bars; ref, source id, upstream endpoint and the
    not traded sets equal the run file read."""
    pytest.importorskip("pyarrow")
    from services.source_ingestion.tests import test_tw_suspension_holidays_intake as intake

    root = tmp_path / "store"
    intake.store_all(_official_files, root, tmp_path, monkeypatch, sources=(intake.TWSE_PRICE, intake.TPEX_PRICE))
    _, _, old = feed._read_bars(feed._partitions(root, "tw_price_daily"), captures_only=True)
    new = feed._read_captures_from_daily_bars(root, sorted({session for session, _ in old}))
    assert set(new) == set(old) == {("2026-11-10", "TWSE"), ("2026-06-18", "TPEx")}
    for key in old:
        assert new[key]["ref"] == old[key]["ref"], key
        assert new[key]["non_traded"] == old[key]["non_traded"], key
    assert len(new[("2026-11-10", "TWSE")]["non_traded"]) == 14
    assert "@MI_INDEX#" in new[("2026-11-10", "TWSE")]["ref"] and "@dailyQuotes#" in new[("2026-06-18", "TPEx")]["ref"]


def test_survivorship_attestation_in_daily_bars_mode_equals_the_run_file_read_without_run_files(_official_files, tmp_path, monkeypatch):
    pytest.importorskip("pyarrow")
    import shutil

    from services.source_ingestion.tests import test_tw_suspension_holidays_intake as intake

    root = tmp_path / "store"
    intake.store_all(_official_files, root, tmp_path, monkeypatch)
    monkeypatch.delenv("PANTHEON_DAILY_PRICE_READER", raising=False)
    old = feed.build_candidate_pool_market(root, INPUTS)["survivorship_attestation"]
    feed._CACHE.clear()
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "daily_bars")
    shutil.rmtree(root / "normalized" / "tw_price_daily")  # the price run files are gone; the others stay
    new = feed.build_candidate_pool_market(root, INPUTS)["survivorship_attestation"]
    assert new == old
