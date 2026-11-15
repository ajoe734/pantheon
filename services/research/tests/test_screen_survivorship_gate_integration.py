"""A screened rule through the real Source feed (HTTP router) and the real replication gate.

Prices are the recorded official TWSE bars (fixture).  Only the capture markers for the venue /
datasets the recording does not hold are explicit test-side injections, named below; production
code never writes them.  Nothing sets a flag: the attestation is derived by the feed from the
stored partitions and the gate consumes it unchanged.
"""
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.research.replication import test_gate as _gate_tests  # noqa: F401
from services.research.replication.gate import ReplicationGate
from services.research.replication.gate_config import GateConfig
from services.research.replication.tests import test_gate_screen_contribution as base
from services.source_ingestion import candidate_pool_market_feed as feed
from services.source_ingestion.routers.ingest_operations import create_ingest_operations_router
from services.source_ingestion.survivorship_attestation import ALL_MARKET_BUNDLES

INPUTS = [{"name": "price", "dataset": "tw_price_daily", "field": "close"},
          {"name": "turnover", "dataset": "tw_price_daily", "field": "volume"}]
RECORDED = json.loads(base.FIXTURE.read_text())["market"]
SESSIONS = RECORDED["trading_sessions"]


@pytest.fixture(autouse=True)
def _clear():
    feed._CACHE.clear()


def _put(root, dataset, session, row, run="run-1"):
    path = root / "normalized" / dataset / f"date={session}" / f"{run}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(row) + "\n")


def _meta(venue, session, nrow, source_dataset):
    return {"metadata": {"venue": venue, "api_endpoint": ALL_MARKET_BUNDLES[venue], "source_dataset": source_dataset,
                         "normalized_row": {"date": session, "venue": venue, **nrow}}}


def _store(root, *, tpex_sessions, suspension_sessions, holidays):
    for symbol, payload in RECORDED["symbols"].items():  # recorded official TWSE bars
        for bar in payload["bars"]:
            session = bar["event_time"][:10]
            _put(root, "tw_price_daily", session, _meta("TWSE", session, {
                "symbol_canonical": symbol, "open": bar["open"], "high": bar["high"], "low": bar["low"],
                "close": bar["close"], "volume": bar["volume"],
                "value": bar["traded_value"], "available_time": bar["available_time"]}, "MI_INDEX_ALLBUT0999"))
    for session in tpex_sessions:  # TEST-SIDE INJECTION: TPEx all-market capture marker (no tradable bar)
        _put(root, "tw_price_daily", session, _meta("TPEx", session, {}, "tpex_daily_quotes_all"))
    for session in suspension_sessions:  # TEST-SIDE INJECTION: official suspended-security captures
        for venue in ("TWSE", "TPEx"):
            _put(root, "tw_trading_suspension", session,
                 _meta(venue, session, {"capture": "all_market", "suspended_symbols": []}, "suspension"))
    for day in holidays:  # TEST-SIDE INJECTION: official exchange closed days
        _put(root, "tw_trading_holidays", day, {"metadata": {
            "api_endpoint": "https://www.twse.com.tw/zh/holidaySchedule/holidaySchedule",
            "normalized_row": {"date": day, "closed": True}}})


def _closed_weekdays():
    day, out = date.fromisoformat(SESSIONS[0]), []
    while day <= date.fromisoformat(SESSIONS[-1]):
        if day.weekday() < 5 and day.isoformat() not in SESSIONS:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _market_through_feed(root):
    app = FastAPI()
    app.include_router(create_ingest_operations_router(SimpleNamespace(MARKET_DATA_STORAGE_ROOT=root)))
    response = TestClient(app).post("/api/source-ingest/candidate-pool-market", json={"inputs": INPUTS})
    assert response.status_code == 200, response.text
    return response.json()


def _gate_result(market):
    gate = ReplicationGate(config=GateConfig(**base.SHORT_HISTORY))
    request = base.build_request(base.artifact("top"), base.dataset(market))
    return base.backtest_result(gate.evaluate_candidate(request))


def test_recorded_history_without_attestation_evidence_is_survivorship_limited(tmp_path):
    _store(tmp_path, tpex_sessions=[], suspension_sessions=[], holidays=[])
    market = _market_through_feed(tmp_path)
    assert market["includes_delisted"] is False and market["includes_suspended"] is False
    result = _gate_result(market)
    assert result.passed is False and result.details["status"] == "survivorship_limited"


def test_complete_evidence_window_is_not_survivorship_limited(tmp_path):
    _store(tmp_path, tpex_sessions=SESSIONS, suspension_sessions=SESSIONS, holidays=_closed_weekdays())
    market = _market_through_feed(tmp_path)
    att = market["survivorship_attestation"]
    assert market["includes_delisted"] is True and market["includes_suspended"] is True
    assert att["delisted"]["sessions_covered"] == len(SESSIONS) and att["delisted"]["evidence_refs"]
    assert att["delisted"]["gaps"] == []
    result = _gate_result(market)
    assert result.details.get("status") != "survivorship_limited"
    assert result.details.get("survivorship_limited") is not True


def test_window_with_a_gap_is_survivorship_limited(tmp_path):
    gap = SESSIONS[10]
    _store(tmp_path, tpex_sessions=[s for s in SESSIONS if s != gap], suspension_sessions=SESSIONS,
           holidays=_closed_weekdays())
    market = _market_through_feed(tmp_path)
    assert market["includes_delisted"] is False and market["includes_suspended"] is False
    assert any(g["session"] == gap for g in market["survivorship_attestation"]["delisted"]["gaps"])
    result = _gate_result(market)
    assert result.passed is False and result.details["status"] == "survivorship_limited"
