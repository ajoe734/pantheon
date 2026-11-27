"""GET /api/source-ingest/broker-branch-flow (AGORA-SERVANT-DATA-TOOLS-20261127).

The bounded owner read of the keyed ``tw_broker_top`` dataset that the Agora servant's
``broker_branch_flow`` tool calls: by symbol, session range and side, never the tenant-wide
source-records list.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.runtime_auth_inbound import encode_jwt_hs256
from services.source_ingestion.connectors.finmind_taiwan import FinMindTaiwanBrokerDailyReportAdapter
from services.source_ingestion.daily_bars_store import keyed_dataset_store
from services.source_ingestion.market_data_storage import MarketDataStorageWriter
from services.source_ingestion.routers.ingest_operations import create_ingest_operations_router
from services.source_ingestion.tests.test_datasets_keyed_write import _finmind_broker_session_payload, _result

URL = "/api/source-ingest/broker-branch-flow"
SECRET = "broker-flow-test-secret"


class _NoEvidence:
    """The route must never touch the tenant-wide source-records owner."""

    def __getattr__(self, name):
        raise AssertionError(f"evidence repository touched: {name}")


def _write_session(root, symbol: str, trade_date: str, per_side: int = 20) -> None:
    adapter = FinMindTaiwanBrokerDailyReportAdapter(max_rank=per_side)
    records = adapter.records_from_daily_report_payload(
        _finmind_broker_session_payload(symbol, trade_date, per_side),
        symbol=symbol,
        trade_date=trade_date,
        available_time=f"{trade_date}T10:30:00Z",
    )
    MarketDataStorageWriter(root).write_run(
        result=_result(records, f"run-{symbol}-{trade_date}"), connector=adapter.connector()
    )


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", SECRET)
    for day in ("2026-11-24", "2026-11-25", "2026-11-26"):
        _write_session(tmp_path, "2330", day)
        _write_session(tmp_path, "2317", day)
    runtime = SimpleNamespace(MARKET_DATA_STORAGE_ROOT=tmp_path, evidence_repository=_NoEvidence())
    app = FastAPI()
    app.include_router(create_ingest_operations_router(runtime))
    return TestClient(app)


def _headers(roles=("source_ingest_reader",)):
    token = encode_jwt_hs256(
        {"sub": "agora-servant-tools", "roles": list(roles), "tenant_id": "tenant-dev", "exp": int(time.time()) + 600},
        secret=SECRET,
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tenant-dev"}


def test_latest_session_both_sides_by_default(client):
    body = client.get(URL, params={"symbols": "2330"}, headers=_headers()).json()
    assert body["schema_version"] == "broker_branch_flow.v1"
    assert body["connector_id"] == "tw-finmind-broker-daily-report"
    assert body["sessions"] == ["2026-11-26"] and body["latest_stored_session"] == "2026-11-26"
    rows = body["rows"]
    assert {r["date"] for r in rows} == {"2026-11-26"} and {r["symbol"] for r in rows} == {"2330"}
    buy = [r for r in rows if r["side"] == "buy"]
    sell = [r for r in rows if r["side"] == "sell"]
    # The buy side survives the keyed write (SOURCE-BROKER-TOP-KEYED-SIDE) and the default top is 15.
    assert len(buy) == 15 and len(sell) == 15
    first = buy[0]
    assert first["rank"] == 1 and first["broker"] == "BuyBroker-01" and first["unit"] == "shares"
    assert first["net_qty"] == first["buy_qty"] - first["sell_qty"] and first["net_qty"] > 0
    assert first["source_id"] and first["available_time"] == "2026-11-26T10:30:00Z"
    assert body["symbols_without_rows"] == [] and body["truncated"] is False


def test_symbol_side_and_range_filters(client):
    body = client.get(
        URL,
        params={"symbols": "2330.TW,2317,9999", "date_from": "2026-11-25", "date_to": "2026-11-26",
                "side": "sell", "top": 3},
        headers=_headers(),
    ).json()
    assert body["sessions"] == ["2026-11-25", "2026-11-26"]
    assert {r["side"] for r in body["rows"]} == {"sell"}
    assert {(r["date"], r["symbol"]) for r in body["rows"]} == {
        ("2026-11-25", "2317"), ("2026-11-25", "2330"), ("2026-11-26", "2317"), ("2026-11-26", "2330")}
    assert max(r["rank"] for r in body["rows"]) == 3 and len(body["rows"]) == 12
    assert body["symbols_without_rows"] == ["9999"]


def test_mixed_legacy_and_compact_keyed_rows_return_both_sides(client, tmp_path):
    """One session query must serve historical metadata rows and whole-market compact rows."""
    day = "2026-11-26"
    adapter = FinMindTaiwanBrokerDailyReportAdapter()
    compact_rows = [
        {"symbol": "2454", "date": day, "side": side, "rank": 1,
         "broker": f"Compact-{side}", "broker_id": f"branch-{side}",
         "buy_qty": buy, "sell_qty": sell, "net_qty": buy - sell,
         "available_time": f"{day}T10:30:00Z"}
        for side, buy, sell in (("buy", 9000, 1000), ("sell", 2000, 7000))
    ]
    compact_records = adapter.compact_keyed_records(compact_rows, run_id="whole-market-test")
    store = keyed_dataset_store(tmp_path, "tw_broker_top")
    store.write_records(compact_records)
    stored = store.records(day)
    assert any(r["metadata"].get("side") == "buy" for r in stored)
    assert any(r["metadata"].get("side") == "sell" for r in stored)
    assert all("side" not in r["metadata"] for r in compact_records)

    response = client.get(URL, params={"symbols": "2330,2454", "top": 1}, headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["symbols_without_rows"] == [] and body["truncated"] is False
    assert {(r["symbol"], r["side"]) for r in body["rows"]} == {
        ("2330", "buy"), ("2330", "sell"), ("2454", "buy"), ("2454", "sell")}
    for expected in compact_rows:
        row = next(r for r in body["rows"] if r["symbol"] == "2454" and r["side"] == expected["side"])
        for field in ("date", "symbol", "side", "rank", "broker", "broker_id",
                      "buy_qty", "sell_qty", "net_qty", "available_time"):
            assert row[field] == expected[field]
        assert row["source_id"] == f"finmind-broker-top:2454:{day}:{expected['side']}:1"
        assert row["unit"] == "shares" and row["source"] == "FinMind TaiwanStockTradingDailyReport"


def test_bounds_are_enforced(client):
    too_many = ",".join(str(1000 + i) for i in range(21))
    assert client.get(URL, params={"symbols": too_many}, headers=_headers()).status_code == 422
    assert client.get(URL, params={"symbols": "2330", "top": 21}, headers=_headers()).status_code == 422
    assert client.get(URL, params={"symbols": "2330", "side": "net"}, headers=_headers()).status_code == 422
    bad_range = {"symbols": "2330", "date_from": "2026-11-26", "date_to": "2026-11-24"}
    assert client.get(URL, params=bad_range, headers=_headers()).status_code == 422


def test_requires_an_authenticated_reader(client):
    assert client.get(URL, params={"symbols": "2330"}).status_code == 401
    assert client.get(URL, params={"symbols": "2330"}, headers=_headers(roles=("nobody",))).status_code == 403


def test_empty_store_is_empty_not_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", SECRET)
    app = FastAPI()
    app.include_router(create_ingest_operations_router(SimpleNamespace(MARKET_DATA_STORAGE_ROOT=tmp_path)))
    body = TestClient(app).get(URL, params={"symbols": "2330"}, headers=_headers()).json()
    assert body["rows"] == [] and body["sessions"] == [] and body["latest_stored_session"] is None
    assert body["symbols_without_rows"] == ["2330"]
