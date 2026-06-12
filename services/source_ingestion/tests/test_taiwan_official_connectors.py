from __future__ import annotations

import json
import os
import urllib.request

import pytest

from services.foundation import DeadLetterQueue
from services.source_ingestion import IngestManager
from services.source_ingestion.connectors import (
    TAIWAN_OFFICIAL_ENDPOINTS,
    TW_OFFICIAL_CONNECTOR_ID,
    TaiwanOfficialMarketDatasetAdapter,
)
from services.source_ingestion.scheduler import IngestBatch, IngestionScheduler, JsonlIngestScheduleStore


TWSE_PRICE_PAYLOAD = [
    {
        "Date": "1150610",
        "Code": "2330",
        "Name": "台積電",
        "TradeVolume": "30000000",
        "TradeValue": "28500000000",
        "OpeningPrice": "950.00",
        "HighestPrice": "960.00",
        "LowestPrice": "945.00",
        "ClosingPrice": "955.00",
        "Change": "+5.00",
        "Transaction": "18000",
    }
]

TPEX_PRICE_PAYLOAD = [
    {
        "Date": "1150610",
        "SecuritiesCompanyCode": "3105",
        "CompanyName": "穩懋",
        "Close": "118.50",
        "Change": "-1.00",
        "Open": "120.00",
        "High": "121.00",
        "Low": "117.50",
        "TradingShares": "3200000",
        "TransactionAmount": "380000000",
        "TransactionNumber": "4200",
    }
]

TWSE_INSTITUTIONAL_PAYLOAD = {
    "stat": "OK",
    "date": "20260610",
    "fields": [
        "證券代號",
        "證券名稱",
        "外陸資買進股數(不含外資自營商)",
        "外陸資賣出股數(不含外資自營商)",
        "外陸資買賣超股數(不含外資自營商)",
        "投信買進股數",
        "投信賣出股數",
        "投信買賣超股數",
        "自營商買賣超股數",
        "自營商買進股數(自行買賣)",
        "自營商賣出股數(自行買賣)",
        "三大法人買賣超股數",
    ],
    "data": [
        [
            "2330",
            "台積電",
            "20,000",
            "5,000",
            "15,000",
            "1,000",
            "250",
            "750",
            "-500",
            "200",
            "700",
            "15,250",
        ]
    ],
}

TPEX_INSTITUTIONAL_PAYLOAD = [
    {
        "Date": "1150610",
        "SecuritiesCompanyCode": "3105",
        "CompanyName": "穩懋",
        "ForeignInvestorsIncludeMainlandAreaInvestors-TotalBuy": "12000",
        "ForeignInvestorsIncludeMainlandAreaInvestors-TotalSell": "5000",
        "ForeignInvestorsIncludeMainlandAreaInvestors-Difference": "7000",
        "SecuritiesInvestmentTrustCompanies-TotalBuy": "100",
        "SecuritiesInvestmentTrustCompanies-TotalSell": "0",
        "SecuritiesInvestmentTrustCompanies-Difference": "100",
        "Dealers-TotalBuy": "300",
        "Dealers-TotalSell": "800",
        "Dealers-Difference": "-500",
        "TotalDifference": "6600",
    }
]

TWSE_MARGIN_PAYLOAD = [
    {
        "股票代號": "2330",
        "股票名稱": "台積電",
        "融資買進": "100",
        "融資賣出": "40",
        "融資現金償還": "5",
        "融資前日餘額": "9000",
        "融資今日餘額": "9055",
        "融資限額": "100000",
        "融券買進": "2",
        "融券賣出": "7",
        "融券現券償還": "0",
        "融券前日餘額": "300",
        "融券今日餘額": "305",
        "融券限額": "100000",
        "資券互抵": "1",
        "註記": " ",
    }
]

TPEX_LENDING_PAYLOAD = [
    {
        "Date": "1150610",
        "SecuritiesCompanyCode": "3105",
        "CompanyName": "穩懋",
        "SaleBalanceOfTheMarketDay": "5000",
        "SecuritiesBorrowingSale": "1000",
        "SecuritiesBorrowingBalanceOfTheMarketDay": "94816000",
        "AvailableVolumesForSBLShortSale": "11533671",
        "Note": "",
    }
]

TWSE_DAY_TRADING_PAYLOAD = [
    {"Date": "1150611", "Code": "2330", "Name": "台積電", "Suspension": ""}
]


def test_taiwan_official_connector_catalog_tier_policy_and_auth() -> None:
    adapter = TaiwanOfficialMarketDatasetAdapter()
    connector = adapter.connector()

    assert connector.connector_id == TW_OFFICIAL_CONNECTOR_ID
    assert connector.provider == "TWSE/TPEx"
    assert connector.auth_policy.auth_type.value == "none"
    assert connector.metadata["official_reference_truth"] is True
    assert {"tw_price_daily", "tw_institutional_flow", "tw_margin_short_balance"} <= set(
        connector.metadata["normalized_datasets"]
    )
    assert connector.metadata["tier_policy"]["archive_universe"] == ["tw_price_daily"]
    assert any(endpoint["dataset"] == "tdcc_shareholding_distribution" for endpoint in TAIWAN_OFFICIAL_ENDPOINTS)
    assert connector.metadata["excluded_followups"][0]["followup_task"] == "DATASTRAT-MARKETDATA-TW-REMAINING-007"


def test_taiwan_official_adapter_emits_twse_and_tpex_daily_price_records() -> None:
    adapter = TaiwanOfficialMarketDatasetAdapter()

    twse = adapter.records_from_payload("tw_price_daily", "TWSE", TWSE_PRICE_PAYLOAD, trace_id="trace-twse-price")
    tpex = adapter.records_from_payload("tw_price_daily", "TPEx", TPEX_PRICE_PAYLOAD, trace_id="trace-tpex-price")

    assert twse[0].connector_id == TW_OFFICIAL_CONNECTOR_ID
    assert twse[0].metadata["dataset"] == "tw_price_daily"
    assert twse[0].metadata["normalized_row"]["symbol_canonical"] == "2330.TWSE"
    assert twse[0].metadata["normalized_row"]["close"] == 955.0
    assert twse[0].metadata["normalized_row"]["volume"] == 30000000
    assert tpex[0].metadata["normalized_row"]["symbol_canonical"] == "3105.TPEX"
    assert tpex[0].metadata["normalized_row"]["close"] == 118.5


def test_taiwan_official_adapter_emits_chip_records_and_skips_archive_detail() -> None:
    adapter = TaiwanOfficialMarketDatasetAdapter(max_records=10)

    institutional = adapter.records_from_payload(
        "tw_institutional_flow",
        "TWSE",
        TWSE_INSTITUTIONAL_PAYLOAD,
        universe_tier="candidate_universe",
        trace_id="trace-inst",
    )
    tpex_institutional = adapter.records_from_payload(
        "tw_institutional_flow",
        "TPEx",
        TPEX_INSTITUTIONAL_PAYLOAD,
        universe_tier="core_universe",
        trace_id="trace-tpex-inst",
    )
    margin = adapter.records_from_payload(
        "tw_margin_short_balance",
        "TWSE",
        TWSE_MARGIN_PAYLOAD,
        trade_date="2026-06-10",
        trace_id="trace-margin",
    )
    lending = adapter.records_from_payload(
        "tw_securities_lending",
        "TPEx",
        TPEX_LENDING_PAYLOAD,
        trace_id="trace-lending",
    )
    day_trading = adapter.records_from_payload(
        "tw_day_trading",
        "TWSE",
        TWSE_DAY_TRADING_PAYLOAD,
        trace_id="trace-day-trading",
    )

    assert institutional[0].metadata["normalized_row"]["foreign_net"] == 15000
    assert institutional[0].metadata["normalized_row"]["total_net"] == 15250
    assert tpex_institutional[0].metadata["normalized_row"]["dealer_net"] == -500
    assert margin[0].metadata["normalized_row"]["margin_balance"] == 9055
    assert margin[0].metadata["normalized_row"]["short_balance"] == 305
    assert lending[0].metadata["normalized_row"]["sbl_short_available_volume"] == 11533671
    assert day_trading[0].metadata["normalized_row"]["short_then_buy_suspended"] is False

    archive_chip = adapter.records_from_payload(
        "tw_institutional_flow",
        "TWSE",
        TWSE_INSTITUTIONAL_PAYLOAD,
        universe_tier="archive_universe",
    )
    archive_price = adapter.records_from_payload(
        "tw_price_daily",
        "TWSE",
        TWSE_PRICE_PAYLOAD,
        universe_tier="archive_universe",
    )
    assert archive_chip == ()
    assert len(archive_price) == 1
    assert archive_price[0].metadata["universe_tier"] == "archive_universe"


def test_taiwan_official_scheduled_run_writes_watermark_and_health(tmp_path) -> None:
    adapter = TaiwanOfficialMarketDatasetAdapter()
    manager = IngestManager()
    manager.register_connector(adapter.connector())
    store = JsonlIngestScheduleStore(tmp_path / "schedule.jsonl")
    scheduler = IngestionScheduler(
        manager=manager,
        store=store,
        dead_letter_queue=DeadLetterQueue(tmp_path / "dlq.jsonl"),
    )
    records = adapter.records_from_payload("tw_price_daily", "TWSE", TWSE_PRICE_PAYLOAD)

    result = scheduler.run_once(
        connector_id=TW_OFFICIAL_CONNECTOR_ID,
        trace_id="trace-tw-official-scheduled",
        fetch_batch=lambda _watermark: IngestBatch(records=records, next_watermark="2026-06-10"),
    )
    health = adapter.source_health_from_result(result)

    assert result.watermark is not None
    assert result.watermark.value == "2026-06-10"
    assert store.get_watermark(TW_OFFICIAL_CONNECTOR_ID).value == "2026-06-10"
    assert health.source_id == TW_OFFICIAL_CONNECTOR_ID
    assert health.status == "ok"
    assert health.latest_watermark == "2026-06-10"
    assert health.row_count_last_run == 1
    assert health.schema_hash == "tw_taiwan_official_market.v1"


@pytest.mark.skipif(
    os.getenv("PANTHEON_TW_OFFICIAL_LIVE_SMOKE") != "1",
    reason="Set PANTHEON_TW_OFFICIAL_LIVE_SMOKE=1 to run read-only official TWSE/TPEx network smoke.",
)
def test_taiwan_official_live_read_only_smoke_for_one_twse_and_tpex_symbol() -> None:
    adapter = TaiwanOfficialMarketDatasetAdapter(max_records=2000)
    with urllib.request.urlopen("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=20) as response:
        twse_payload = json.loads(response.read().decode("utf-8"))
    with urllib.request.urlopen(
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
        timeout=20,
    ) as response:
        tpex_payload = json.loads(response.read().decode("utf-8"))

    twse_records = adapter.records_from_payload("tw_price_daily", "TWSE", twse_payload)
    tpex_records = adapter.records_from_payload("tw_price_daily", "TPEx", tpex_payload)

    assert any(record.metadata["normalized_row"]["symbol"] == "2330" for record in twse_records)
    assert any(record.metadata["normalized_row"]["symbol"] == "3105" for record in tpex_records)
