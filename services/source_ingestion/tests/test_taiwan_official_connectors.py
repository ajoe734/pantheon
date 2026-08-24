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
from services.source_ingestion.provider_adapters import execute_provider_owned_adapter, provider_adapter_tokens
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
    fetch = adapter.fetch_config()

    assert connector.connector_id == TW_OFFICIAL_CONNECTOR_ID
    assert connector.provider == "TWSE/TPEx"
    assert connector.auth_policy.auth_type.value == "none"
    assert fetch["mode"] == "provider_owned_adapter"
    assert fetch["adapter"] == "TaiwanOfficialMarketDatasetAdapter.records_from_payload"
    assert fetch["request"]["venues"] == ["TWSE", "TPEx"]
    assert connector.metadata["official_reference_truth"] is True
    assert {"tw_price_daily", "tw_institutional_flow", "tw_margin_short_balance"} <= set(
        connector.metadata["normalized_datasets"]
    )
    assert connector.metadata["tier_policy"]["archive_universe"] == ["tw_price_daily"]
    assert any(endpoint["dataset"] == "tdcc_shareholding_distribution" and endpoint["status"] == "implemented" for endpoint in TAIWAN_OFFICIAL_ENDPOINTS)
    assert any(endpoint["dataset"] == "taifex_futures_chip" and endpoint["status"] == "implemented" for endpoint in TAIWAN_OFFICIAL_ENDPOINTS)


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


def test_taiwan_official_provider_owned_adapter_is_allowlisted_and_emits_both_venues() -> None:
    adapter = TaiwanOfficialMarketDatasetAdapter(max_records=10)
    connector = adapter.connector()
    tokens = set(provider_adapter_tokens())

    records = execute_provider_owned_adapter(
        connector=connector,
        fetch={
            "mode": "provider_owned_adapter",
            "adapter": "TaiwanOfficialMarketDatasetAdapter",
            "adapter_config": {"max_records": 10},
            "request": {
                "dataset": "tw_price_daily",
                "venues": ["TWSE", "TPEx"],
                "payloads": {
                    "TWSE": TWSE_PRICE_PAYLOAD,
                    "TPEx": TPEX_PRICE_PAYLOAD,
                },
            },
            "max_records": 10,
        },
        trace_id="trace-tw-official-provider-owned",
    )

    assert "TaiwanOfficialMarketDatasetAdapter" in tokens
    assert "TaiwanOfficialMarketDatasetAdapter.records_from_payload" in tokens
    assert [record.metadata["venue"] for record in records] == ["TWSE", "TPEx"]
    assert records[0].metadata["provider_owned_adapter"] == "TaiwanOfficialMarketDatasetAdapter.records_from_payload"
    assert records[0].metadata["normalized_row"]["symbol_canonical"] == "2330.TWSE"
    assert records[1].metadata["normalized_row"]["symbol_canonical"] == "3105.TPEX"


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


def test_tdcc_shareholding_distribution_adapter_and_pit() -> None:
    from services.source_ingestion.connectors import (
        TDCC_SHAREHOLDING_CONNECTOR_ID,
        TDCC_SHAREHOLDING_SCHEMA_HASH,
        TdccShareholdingDistributionAdapter,
    )

    tdcc_payload = [
        {
            "資料日期": "2026-06-12",
            "證券代號": "2330",
            "持股分級": 15,
            "持股分級說明": "1,000,001以上",
            "人數": "1500",
            "股數": "20000000000",
            "占集保庫存數比例%": "77.12",
        },
        {
            "Date": "1150612",
            "Code": "2330",
            "HoldLevel": 1,
            "LevelDescription": "1-999",
            "PeopleCount": "800000",
            "Shares": "150000000",
            "Percentage": "0.58",
        },
    ]

    adapter = TdccShareholdingDistributionAdapter(max_records=10)
    records = adapter.records_from_payload(
        tdcc_payload,
        available_time="2026-06-12T19:00:00Z",
        trace_id="trace-tdcc-test",
    )

    assert len(records) == 2
    assert records[0].connector_id == TDCC_SHAREHOLDING_CONNECTOR_ID
    assert records[0].source_type == "market"
    assert records[0].metadata["source_class"] == "taiwan_chip"
    assert records[0].metadata["dataset"] == "tdcc_shareholding_distribution"
    assert records[0].metadata["schema_hash"] == TDCC_SHAREHOLDING_SCHEMA_HASH
    assert records[0].metadata["license_scope"] == "official_reference"
    assert records[0].metadata["available_time"] == "2026-06-12T19:00:00Z"

    row0 = records[0].metadata["normalized_row"]
    assert row0["symbol"] == "2330"
    assert row0["symbol_canonical"] == "2330.TWSE"
    assert row0["holder_level"] == 15
    assert row0["people_count"] == 1500
    assert row0["shares"] == 20000000000
    assert row0["percentage"] == 77.12

    row1 = records[1].metadata["normalized_row"]
    assert row1["date"] == "2026-06-12"
    assert row1["holder_level"] == 1
    assert row1["people_count"] == 800000

    # Test via execute_provider_owned_adapter
    dispatched = execute_provider_owned_adapter(
        connector=adapter.connector(),
        fetch={
            "adapter": "TdccShareholdingDistributionAdapter.records_from_payload",
            "request": {"payload": tdcc_payload, "trade_date": "2026-06-12"},
        },
        trace_id="trace-tdcc-dispatch",
    )
    assert len(dispatched) == 2


def test_taifex_derivatives_chip_adapter_and_pit() -> None:
    from services.source_ingestion.connectors import (
        TAIFEX_DERIVATIVES_CONNECTOR_ID,
        TAIFEX_FUTURES_CHIP_SCHEMA_HASH,
        TAIFEX_OPTIONS_CHIP_SCHEMA_HASH,
        TaifexDerivativesChipAdapter,
    )

    futures_payload = [
        {
            "日期": "2026-06-10",
            "契約名稱": "TX",
            "身份別": "foreign_investors",
            "多方交易口數": "15000",
            "空方交易口數": "12000",
            "多空交易口數淨額": "3000",
            "多方未平倉口數": "45000",
            "空方未平倉口數": "50000",
            "多空未平倉口數淨額": "-5000",
        }
    ]
    options_payload = [
        {
            "Date": "1150610",
            "Contract": "TXO",
            "買權成交量": "100000",
            "賣權成交量": "110000",
            "買權未平倉量": "150000",
            "賣權未平倉量": "180000",
            "買賣權未平倉量比率%": "120.00",
        }
    ]

    adapter = TaifexDerivativesChipAdapter(max_records=10)
    futures_records = adapter.records_from_payload(
        futures_payload,
        dataset="taifex_futures_chip",
        available_time="2026-06-10T16:30:00Z",
        trace_id="trace-taifex-fut",
    )
    options_records = adapter.records_from_payload(
        options_payload,
        dataset="taifex_options_chip",
        available_time="2026-06-10T16:30:00Z",
        trace_id="trace-taifex-opt",
    )

    assert len(futures_records) == 1
    assert futures_records[0].connector_id == TAIFEX_DERIVATIVES_CONNECTOR_ID
    assert futures_records[0].metadata["dataset"] == "taifex_futures_chip"
    assert futures_records[0].metadata["schema_hash"] == TAIFEX_FUTURES_CHIP_SCHEMA_HASH
    fut_row = futures_records[0].metadata["normalized_row"]
    assert fut_row["contract"] == "TX"
    assert fut_row["participant_group"] == "foreign_investors"
    assert fut_row["net_volume"] == 3000
    assert fut_row["net_open_interest"] == -5000

    assert len(options_records) == 1
    assert options_records[0].metadata["dataset"] == "taifex_options_chip"
    assert options_records[0].metadata["schema_hash"] == TAIFEX_OPTIONS_CHIP_SCHEMA_HASH
    opt_row = options_records[0].metadata["normalized_row"]
    assert opt_row["date"] == "2026-06-10"
    assert opt_row["put_call_ratio"] == 120.00
    assert opt_row["call_volume"] == 100000
    assert opt_row["put_volume"] == 110000

    # Test via execute_provider_owned_adapter
    dispatched_fut = execute_provider_owned_adapter(
        connector=adapter.connector(),
        fetch={
            "adapter": "TaifexDerivativesChipAdapter.records_from_payload",
            "request": {"payload": futures_payload, "dataset": "taifex_futures_chip"},
        },
        trace_id="trace-taifex-dispatch",
    )
    assert len(dispatched_fut) == 1
