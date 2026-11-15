"""A screened rule through the real Source intake, the real feed (HTTP router) and the real replication gate.

The holiday schedule and trading suspension records are the recorded official TWSE / TPEx responses run through
the real collector spiders, the production pull entry point and the production storage writer.  Prices are the
recorded official TWSE bars (fixture).  Only the dated all-market page markers for the sessions / venue the
recordings do not hold are explicit test-side injections (``_put_price_pages``, in the shape the collector price
bundles produce); production code never writes them.  Nothing sets a flag: the attestation is derived by the
feed from the stored partitions and the gate consumes it unchanged.
"""
import json
import shutil
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
from services.source_ingestion.tests import test_tw_suspension_holidays_intake as intake

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


def _page_row(exchange, upstream, source_id, session, nrow):
    return {"metadata": {"collector": {"source_id": source_id, "bundle_sha256": "f" * 64},
                         "normalized_row": {"date": session, "trade_date": session, "exchange": exchange,
                                            "upstream_endpoint": upstream, "trading_status": "traded", **nrow}}}


def _put_price_pages(root, *, sessions, tpex_sessions):
    for symbol, payload in RECORDED["symbols"].items():  # recorded official TWSE bars
        for bar in payload["bars"]:
            session = bar["event_time"][:10]
            if session in sessions:
                _put(root, "tw_price_daily", session, _page_row(
                    "TWSE", "MI_INDEX", intake.TWSE_PRICE, session, {
                        "symbol": symbol[:4], "symbol_canonical": symbol, "open": bar["open"], "high": bar["high"],
                        "low": bar["low"], "close": bar["close"], "volume": bar["volume"],
                        "value": bar["traded_value"], "available_time": bar["available_time"]}))
    for session in tpex_sessions:  # TEST-SIDE INJECTION: TPEx all-market page marker (no tradable bar)
        _put(root, "tw_price_daily", session, _page_row("TPEX", "dailyQuotes", intake.TPEX_PRICE, session, {}))


@pytest.fixture(scope="module")
def governed(tmp_path_factory):
    """The two governed datasets, produced by the real intake from recorded official responses."""
    tmp = tmp_path_factory.mktemp("governed")
    files = intake.collector_files(tmp / "files", twse_page=False, tpex_page=False)
    root = tmp / "store"
    with pytest.MonkeyPatch.context() as mp:
        intake.store_all(files, root, tmp, mp, sources=(intake.HOLIDAYS, intake.SUSPENSION))
    return root


def _store(governed, tmp_path, *, last_session, tpex_sessions=None, drop_tpex_suspension=False):
    root = tmp_path / "store"
    shutil.copytree(governed, root)
    if drop_tpex_suspension:  # the TPEx official list is missing; nothing replaces it
        for path in (root / "normalized" / "tw_trading_suspension").rglob("*.jsonl*"):
            kept = [line for line in path.read_text().splitlines()
                    if json.loads(line)["metadata"]["normalized_row"]["venue"] != "TPEx"]
            path.write_text("\n".join(kept) + "\n")
    sessions = {s for s in SESSIONS if s <= last_session}
    _put_price_pages(root, sessions=sessions, tpex_sessions=sorted(sessions if tpex_sessions is None else tpex_sessions))
    return root, sorted(sessions)


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


def _scenario_market(name, governed, tmp_path):
    if name == "no_evidence":
        root = tmp_path / "store"
        _put_price_pages(root, sessions=set(SESSIONS), tpex_sessions=[])
    elif name == "complete":
        root, _ = _store(governed, tmp_path, last_session="2026-10-22")
    elif name == "missing_session":  # one TPEx page missing inside the window
        root, _ = _store(governed, tmp_path, last_session="2026-10-22",
                         tpex_sessions=[s for s in SESSIONS if s <= "2026-10-22" and s != SESSIONS[10]])
    elif name == "unknown_suspension":  # the official TPEx suspension list is not stored
        root, _ = _store(governed, tmp_path, last_session="2026-10-22", drop_tpex_suspension=True)
    else:  # "unexplained_weekday": 2026-10-23 is a Friday, not an official closed day, with no recorded session
        root, _ = _store(governed, tmp_path, last_session=SESSIONS[-1])
    return _market_through_feed(root)


def test_no_attestation_evidence_attests_nothing(governed, tmp_path):
    market = _scenario_market("no_evidence", governed, tmp_path)
    assert market["includes_delisted"] is False and market["includes_suspended"] is False


def test_complete_window_is_attested_from_the_governed_datasets(governed, tmp_path):
    # 2026-09-01..2026-10-22: its three interior closed weekdays (09-25, 09-28, 10-09) are in the official schedule
    market = _scenario_market("complete", governed, tmp_path)
    att = market["survivorship_attestation"]
    sessions = [s for s in SESSIONS if s <= "2026-10-22"]
    assert market["includes_delisted"] is True and market["includes_suspended"] is True
    assert att["delisted"]["sessions_covered"] == len(sessions) == att["suspended"]["sessions_covered"]
    assert att["delisted"]["gaps"] == [] and att["suspended"]["gaps"] == []
    assert any("holidaySchedule" in ref for ref in att["delisted"]["evidence_refs"])
    refs = " ".join(att["suspended"]["evidence_refs"])
    assert "afterTrading/TWTAWU?startDate=" in refs and "bulletin/sprcHis?date=2026" in refs


def test_window_with_a_missing_session_is_not_attested(governed, tmp_path):
    market = _scenario_market("missing_session", governed, tmp_path)
    assert market["includes_delisted"] is False and market["includes_suspended"] is False
    assert [g["session"] for g in market["survivorship_attestation"]["delisted"]["gaps"]] == [SESSIONS[10]]


def test_window_with_an_unknown_suspension_list_is_not_attested(governed, tmp_path):
    market = _scenario_market("unknown_suspension", governed, tmp_path)
    att = market["survivorship_attestation"]
    sessions = {s for s in SESSIONS if s <= "2026-10-22"}
    assert market["includes_delisted"] is True and market["includes_suspended"] is False
    assert {g["session"] for g in att["suspended"]["gaps"]} == sessions  # every session named
    assert "TPEx" in att["suspended"]["gaps"][0]["reason"]


def test_unexplained_weekday_keeps_the_recorded_window_unattested(governed, tmp_path):
    market = _scenario_market("unexplained_weekday", governed, tmp_path)
    assert market["includes_delisted"] is False
    assert [g["session"] for g in market["survivorship_attestation"]["delisted"]["gaps"]] == ["2026-10-23"]


@pytest.mark.parametrize("name,limited", [
    ("no_evidence", True), ("complete", False), ("missing_session", True),
    ("unknown_suspension", True), ("unexplained_weekday", True)])
def test_gate_outcome_follows_the_attestation_unchanged(name, limited, governed, tmp_path):
    # the gate's backtest backend (services/research/vectorbt/requirements.txt)
    pytest.importorskip("pandas")
    pytest.importorskip("vectorbt.portfolio")
    result = _gate_result(_scenario_market(name, governed, tmp_path))
    if limited:
        assert result.passed is False and result.details["status"] == "survivorship_limited"
    else:
        assert result.details.get("status") != "survivorship_limited"
        assert result.details.get("survivorship_limited") is not True
