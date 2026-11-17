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
