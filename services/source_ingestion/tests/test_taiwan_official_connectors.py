from __future__ import annotations

import os

import pytest

from services.foundation import DeadLetterQueue
from services.source_ingestion import IngestManager
from services.source_ingestion.connectors import (
    TAIWAN_OFFICIAL_ENDPOINTS,
    TW_OFFICIAL_CONNECTOR_ID,
    TaiwanOfficialMarketDatasetAdapter,
)
from services.source_ingestion.connectors.base import SourceEvidenceError
from services.source_ingestion.provider_adapters import execute_provider_owned_adapter, provider_adapter_tokens
from services.source_ingestion.requirement_state import LatestMarketSnapshotStore
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

TWSE_PRICE_HISTORY_PAYLOAD = {
    "stat": "OK",
    "date": "20260610",
    "title": "115年06月 2330 台積電 各日成交資訊",
    "fields": [
        "日期",
        "成交股數",
        "成交金額",
        "開盤價",
        "最高價",
        "最低價",
        "收盤價",
        "漲跌價差",
        "成交筆數",
        "註記",
    ],
    "data": [
        ["115/06/09", "20,000,000", "18,900,000,000", "940.00", "955.00", "938.00", "950.00", "+10.00", "12,000", ""],
        ["115/06/10", "30,000,000", "28,500,000,000", "950.00", "960.00", "945.00", "955.00", "+5.00", "18,000", ""],
    ],
}

TPEX_PRICE_HISTORY_PAYLOAD = {
    "stat": "ok",
    "date": "20260601",
    "code": "3105",
    "name": "穩懋",
    "tables": [
        {
            "title": "個股日成交資訊",
            "subtitle": "3105 穩懋 115年06月",
            "date": "20260601",
            "fields": ["日 期", "成交張數", "成交仟元", "開盤", "最高", "最低", "收盤", "漲跌", "筆數"],
            "data": [
                ["115/06/09", "2,900", "340,000", "116.00", "119.00", "115.50", "117.00", "-1.50", "3,900"],
                ["115/06/10", "3,200", "380,000", "120.00", "121.00", "117.50", "118.50", "+1.50", "4,200"],
            ],
        }
    ],
}

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
    assert connector.metadata["active_symbol_history_policy"] == {
        "minimum_distinct_closes": 2,
        "max_months_per_symbol": 2,
    }
    assert {
        item["source_dataset"]
        for item in connector.metadata["price_history_endpoint_inventory"]
    } == {"STOCK_DAY", "tpex_individual_stock_monthly_history"}
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


def test_taiwan_official_adapter_emits_authentic_monthly_history_records() -> None:
    adapter = TaiwanOfficialMarketDatasetAdapter(max_records=10)

    twse = adapter.records_from_price_history_payload(
        "2330.TWSE",
        "TWSE",
        TWSE_PRICE_HISTORY_PAYLOAD,
        api_endpoint="https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date=20260601&stockNo=2330&response=json",
        trace_id="trace-twse-history",
    )
    tpex = adapter.records_from_price_history_payload(
        "3105.TPEX",
        "TPEx",
        TPEX_PRICE_HISTORY_PAYLOAD,
        api_endpoint="https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code=3105&date=2026%2F06%2F01&id=&response=json",
        trace_id="trace-tpex-history",
    )

    assert [record.metadata["event_time"] for record in twse] == ["2026-06-09", "2026-06-10"]
    assert [record.metadata["normalized_row"]["close"] for record in twse] == [950.0, 955.0]
    assert twse[0].metadata["source_dataset"] == "STOCK_DAY"
    assert twse[0].metadata["history_window"] == "official_monthly"
    assert twse[0].metadata["raw_row"]["日期"] == "115/06/09"
    assert twse[0].source_id.startswith("tw-official:tw_price_daily:TWSE:2330:")
    assert [record.metadata["normalized_row"]["close"] for record in tpex] == [117.0, 118.5]
    assert tpex[0].metadata["normalized_row"]["volume_lots"] == 2900
    assert tpex[0].metadata["raw_row"]["日 期"] == "115/06/09"
    assert "calendar_evidence" not in twse[-1].metadata


def test_taiwan_official_adapter_binds_exact_governed_twse_calendar_evidence() -> None:
    adapter = TaiwanOfficialMarketDatasetAdapter(max_records=10)
    records = adapter.records_from_payload(
        "tw_price_daily",
        "TWSE",
        [
            {"Date": "1150210", "Code": "2330", "ClosingPrice": "950.00"},
            {"Date": "1150211", "Code": "2330", "ClosingPrice": "955.00"},
        ],
        trace_id="trace-governed-twse-calendar",
    )

    assert "calendar_evidence" not in records[0].metadata
    evidence = records[1].metadata["calendar_evidence"]
    assert evidence["venue"] == "TWSE"
    assert evidence["version"] == "twse-2026-lny-v1"
    assert evidence["coverage_start"] == "2026-02-11"
    assert evidence["coverage_end"] == "2026-02-23"
    assert evidence["trading_days"] == ["2026-02-11", "2026-02-23"]
    assert evidence["checksum"] == (
        "55b2e23b9bd30af666a99c98da2dbbfad568dcd655631b1c6347d12ee8381596"
    )
    [catalog_entry] = adapter.connector().metadata["governed_calendar_evidence"]
    assert catalog_entry["venue"] == "TWSE"
    assert catalog_entry["year"] == 2026
    assert catalog_entry["sha256"] == evidence["checksum"]

    # The production writer never relabels TWSE proof as TPEx proof or extends
    # the bounded catalog to an uncovered record date.
    tpex = adapter.records_from_payload(
        "tw_price_daily",
        "TPEx",
        [{"Date": "1150211", "SecuritiesCompanyCode": "3105", "Close": "118.50"}],
    )
    august = adapter.records_from_payload(
        "tw_price_daily",
        "TWSE",
        [{"Date": "1150828", "Code": "2330", "ClosingPrice": "960.00"}],
    )
    assert "calendar_evidence" not in tpex[0].metadata
    assert "calendar_evidence" not in august[0].metadata


def test_taiwan_official_history_fetch_is_bounded_and_uses_prior_month(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def fake_fetch(
        self,
        symbol,
        venue,
        *,
        anchor_date,
        timeout_seconds=20.0,
    ):
        del timeout_seconds
        calls.append(anchor_date)
        if anchor_date == "2026-06-01":
            payload = {
                **TWSE_PRICE_HISTORY_PAYLOAD,
                "data": [TWSE_PRICE_HISTORY_PAYLOAD["data"][-1]],
            }
        else:
            payload = {
                **TWSE_PRICE_HISTORY_PAYLOAD,
                "date": "20260501",
                "data": [
                    ["115/05/29", "18,000,000", "16,900,000,000", "930.00", "945.00", "928.00", "940.00", "+5.00", "11,000", ""]
                ],
            }
        return payload, self.price_history_endpoint(
            symbol,
            venue,
            anchor_date=anchor_date,
        )

    monkeypatch.setattr(
        TaiwanOfficialMarketDatasetAdapter,
        "fetch_price_history_payload",
        fake_fetch,
    )
    records = TaiwanOfficialMarketDatasetAdapter(max_records=10).fetch_price_history_records(
        "2330.TWSE",
        "TWSE",
        anchor_date="2026-06-10",
        trace_id="trace-bounded-two-month-history",
    )

    assert calls == ["2026-06-01", "2026-05-01"]
    assert {record.metadata["event_time"] for record in records} == {
        "2026-05-29",
        "2026-06-10",
    }


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


def test_bounded_official_refresh_prioritizes_active_symbols_before_global_cap(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "2330.TW,3105.TWO")
    adapter = TaiwanOfficialMarketDatasetAdapter(max_records=4)
    connector = adapter.connector()
    twse_payload = [
        {
            **TWSE_PRICE_PAYLOAD[0],
            "Code": f"{index:04d}",
            "Name": f"TWSE {index}",
        }
        for index in range(1, 105)
    ] + TWSE_PRICE_PAYLOAD
    tpex_payload = [
        {
            **TPEX_PRICE_PAYLOAD[0],
            "SecuritiesCompanyCode": f"{index:04d}",
            "CompanyName": f"TPEx {index}",
        }
        for index in range(4001, 4105)
    ] + TPEX_PRICE_PAYLOAD

    records = execute_provider_owned_adapter(
        connector=connector,
        fetch={
            "mode": "provider_owned_adapter",
            "adapter": "TaiwanOfficialMarketDatasetAdapter",
            "adapter_config": {"max_records": 4},
            "request": {
                "dataset": "tw_price_daily",
                "venues": ["TWSE", "TPEx"],
                "payloads": {"TWSE": twse_payload, "TPEx": tpex_payload},
                "history_payloads": {
                    "2330.TWSE": TWSE_PRICE_HISTORY_PAYLOAD,
                    "3105.TPEX": TPEX_PRICE_HISTORY_PAYLOAD,
                },
            },
            "max_records": 4,
        },
        trace_id="trace-active-symbol-priority",
    )

    assert [record.metadata["symbol_canonical"] for record in records] == [
        "2330.TWSE",
        "2330.TWSE",
        "3105.TPEX",
        "3105.TPEX",
    ]
    assert [record.metadata["event_time"] for record in records] == [
        "2026-06-09",
        "2026-06-10",
        "2026-06-09",
        "2026-06-10",
    ]
    snapshot_path = tmp_path / "latest-market-snapshots.jsonl"
    store = LatestMarketSnapshotStore(snapshot_path)
    result = store.append_normalized_records(
        records,
        ingest_run_id="ingest-active-symbol-history",
        observed_at="2026-06-10T08:00:00Z",
    )
    assert result["updated_snapshot_count"] == 2
    reloaded = LatestMarketSnapshotStore(snapshot_path)
    assert reloaded.get("2330.TW").closes == (950.0, 955.0)
    assert reloaded.get("3105.TWO").closes == (117.0, 118.5)
    assert {
        point.event_time for point in reloaded.get("2330.TW").points
    } == {"2026-06-09T00:00:00Z", "2026-06-10T00:00:00Z"}


def test_bounded_official_refresh_fails_when_active_symbols_exceed_cap(monkeypatch) -> None:
    monkeypatch.setenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "2330.TW,3105.TWO")

    with pytest.raises(SourceEvidenceError, match="symbol history exceeds"):
        execute_provider_owned_adapter(
            connector=TaiwanOfficialMarketDatasetAdapter(max_records=1).connector(),
            fetch={
                "mode": "provider_owned_adapter",
                "adapter": "TaiwanOfficialMarketDatasetAdapter",
                "adapter_config": {"max_records": 1},
                "request": {
                    "dataset": "tw_price_daily",
                    "venues": ["TWSE", "TPEx"],
                    "payloads": {
                        "TWSE": TWSE_PRICE_PAYLOAD,
                        "TPEx": TPEX_PRICE_PAYLOAD,
                    },
                },
                "max_records": 1,
            },
            trace_id="trace-active-symbol-cap",
        )


def test_bounded_official_refresh_fails_closed_without_two_distinct_closes(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "2330.TW")
    one_day_history = {**TWSE_PRICE_HISTORY_PAYLOAD, "data": [TWSE_PRICE_HISTORY_PAYLOAD["data"][-1]]}

    with pytest.raises(SourceEvidenceError, match="distinct finite close history"):
        execute_provider_owned_adapter(
            connector=TaiwanOfficialMarketDatasetAdapter(max_records=2).connector(),
            fetch={
                "mode": "provider_owned_adapter",
                "adapter": "TaiwanOfficialMarketDatasetAdapter",
                "adapter_config": {"max_records": 2},
                "request": {
                    "dataset": "tw_price_daily",
                    "venues": ["TWSE"],
                    "payloads": {"TWSE": TWSE_PRICE_PAYLOAD},
                    "history_payloads": {"2330.TWSE": one_day_history},
                },
                "max_records": 2,
            },
            trace_id="trace-active-symbol-one-close",
        )


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
def test_taiwan_official_live_read_only_smoke_for_one_twse_and_tpex_symbol(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PANTHEON_EXTERNAL_EGRESS", "allowlist")
    monkeypatch.setenv(
        "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS",
        "openapi.twse.com.tw,www.twse.com.tw,www.tpex.org.tw",
    )
    monkeypatch.setenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "2330.TW,3105.TWO")
    fetch_adapter = TaiwanOfficialMarketDatasetAdapter(max_records=4)
    twse_payload = fetch_adapter.fetch_payload("tw_price_daily", "TWSE")
    tpex_payload = fetch_adapter.fetch_payload("tw_price_daily", "TPEx")

    records = execute_provider_owned_adapter(
        connector=TaiwanOfficialMarketDatasetAdapter(max_records=4).connector(),
        fetch={
            "mode": "provider_owned_adapter",
            "adapter": "TaiwanOfficialMarketDatasetAdapter",
            "adapter_config": {"max_records": 4},
            "request": {
                "dataset": "tw_price_daily",
                "venues": ["TWSE", "TPEx"],
                "payloads": {"TWSE": twse_payload, "TPEx": tpex_payload},
            },
            "max_records": 4,
        },
        trace_id="live-active-symbol-history",
    )

    assert len(records) == 4
    for symbol in ("2330.TWSE", "3105.TPEX"):
        symbol_records = [
            record
            for record in records
            if record.metadata["symbol_canonical"] == symbol
        ]
        assert len(symbol_records) == 2
        assert len({record.metadata["event_time"] for record in symbol_records}) == 2
        assert all(
            str(record.source_id).startswith("tw-official:tw_price_daily:")
            for record in symbol_records
        )


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


def test_tdcc_correction_republication_and_backfill_windows(tmp_path) -> None:
    """Validate TDCC weekly watermark progression, backfill windows, and restatement handling."""
    from services.source_ingestion.connectors import (
        TDCC_SHAREHOLDING_CONNECTOR_ID,
        TdccShareholdingDistributionAdapter,
    )

    adapter = TdccShareholdingDistributionAdapter(max_records=50)

    # 1. Backfill window generation
    weeks = adapter.generate_backfill_weeks("2026-05-01", "2026-05-31")
    assert len(weeks) == 5  # May 1, May 8, May 15, May 22, May 29 (all Fridays in May 2026)
    assert weeks[0] == "2026-05-01"
    assert weeks[-1] == "2026-05-29"

    # 2. Multi-tier level distribution (1 to 15)
    tiers_payload = [
        {"Date": "2026-06-12", "Code": "2330", "HoldLevel": i, "PeopleCount": 100 * (16 - i), "Shares": 1000000 * i, "Percentage": float(i)}
        for i in range(1, 16)
    ]
    records = adapter.records_from_payload(tiers_payload, available_time="2026-06-12T19:00:00Z")
    assert len(records) == 15
    assert records[0].metadata["normalized_row"]["holder_level"] == 1
    assert records[14].metadata["normalized_row"]["holder_level"] == 15

    # 3. Correction / restatement handling
    corrected_payload = [
        {"Date": "2026-06-12", "Code": "2330", "HoldLevel": 15, "PeopleCount": 1520, "Shares": 20100000000, "Percentage": 77.50, "is_correction": True}
    ]
    corr_records = adapter.records_from_payload(
        corrected_payload,
        is_correction=True,
        correction_reason="TDCC official restatement",
        available_time="2026-06-13T10:00:00Z",
    )
    assert len(corr_records) == 1
    assert corr_records[0].metadata["is_correction"] is True
    assert corr_records[0].metadata["correction_reason"] == "TDCC official restatement"
    assert corr_records[0].metadata["available_time"] == "2026-06-13T10:00:00Z"

    # 4. Scheduled ingestion watermark progression
    manager = IngestManager()
    manager.register_connector(adapter.connector())
    store = JsonlIngestScheduleStore(tmp_path / "schedule.jsonl")
    scheduler = IngestionScheduler(
        manager=manager,
        store=store,
        dead_letter_queue=DeadLetterQueue(tmp_path / "dlq.jsonl"),
    )
    result = scheduler.run_once(
        connector_id=TDCC_SHAREHOLDING_CONNECTOR_ID,
        trace_id="trace-tdcc-watermark",
        fetch_batch=lambda _watermark: IngestBatch(records=records, next_watermark="2026-06-12"),
    )
    assert result.watermark is not None
    assert result.watermark.value == "2026-06-12"
    assert store.get_watermark(TDCC_SHAREHOLDING_CONNECTOR_ID).value == "2026-06-12"


def test_taifex_contract_roll_and_calendar_policy(tmp_path) -> None:
    """Validate TAIFEX calendar settlement (3rd Wednesday roll) and daily watermark progression."""
    from services.source_ingestion.connectors import (
        TAIFEX_DERIVATIVES_CONNECTOR_ID,
        TaifexDerivativesChipAdapter,
    )

    adapter = TaifexDerivativesChipAdapter(max_records=10)

    # 1. 3rd Wednesday settlement detection for June 2026 (June 1 = Monday; Wednesdays: June 3 (1st), 10 (2nd), 17 (3rd), 24 (4th))
    assert adapter.is_contract_roll_day("2026-06-17") is True
    assert adapter.is_contract_roll_day("2026-06-10") is False
    assert adapter.is_contract_roll_day("2026-06-24") is False
    assert adapter.is_contract_roll_day("2026-06-18") is False

    # 2. Front-month contract resolution
    assert adapter.resolve_front_month_contract("2026-06-10", "TX") == "TX202606"
    assert adapter.resolve_front_month_contract("2026-06-17", "TX") == "TX202606"
    assert adapter.resolve_front_month_contract("2026-06-18", "TX") == "TX202607"

    # 3. Payload normalization with contract roll flag
    roll_payload = [
        {"Date": "2026-06-17", "Contract": "TX", "ParticipantGroup": "dealers", "LongVolume": 5000, "ShortVolume": 4000, "LongOpenInterest": 10000, "ShortOpenInterest": 8000}
    ]
    roll_records = adapter.records_from_payload(roll_payload, available_time="2026-06-17T16:30:00Z")
    assert len(roll_records) == 1
    assert roll_records[0].metadata["contract_roll_day"] is True
    assert roll_records[0].metadata["front_month_contract"] == "TX202606"

    # 4. Scheduled ingestion daily watermark progression
    manager = IngestManager()
    manager.register_connector(adapter.connector())
    store = JsonlIngestScheduleStore(tmp_path / "schedule.jsonl")
    scheduler = IngestionScheduler(
        manager=manager,
        store=store,
        dead_letter_queue=DeadLetterQueue(tmp_path / "dlq.jsonl"),
    )
    result = scheduler.run_once(
        connector_id=TAIFEX_DERIVATIVES_CONNECTOR_ID,
        trace_id="trace-taifex-watermark",
        fetch_batch=lambda _watermark: IngestBatch(records=roll_records, next_watermark="2026-06-17"),
    )
    assert result.watermark is not None
    assert result.watermark.value == "2026-06-17"
    assert store.get_watermark(TAIFEX_DERIVATIVES_CONNECTOR_ID).value == "2026-06-17"


def test_tdcc_and_taifex_evidence_and_search_canary_readback(tmp_path) -> None:
    """Validate durable source->evidence->search canary readback for TDCC and TAIFEX."""
    from pathlib import Path
    from services.knowledge.evidence import (
        EvidenceBundleBuilder,
        EvidenceItem,
        InMemoryEvidenceRepository,
        JsonlEvidenceRepository,
    )
    from services.search.main import create_app as create_search_app
    from services.source_ingestion.connectors import (
        TaifexDerivativesChipAdapter,
        TdccShareholdingDistributionAdapter,
    )
    from fastapi.testclient import TestClient

    # 1. Generate normalized records
    tdcc_adapter = TdccShareholdingDistributionAdapter()
    taifex_adapter = TaifexDerivativesChipAdapter()

    tdcc_records = tdcc_adapter.records_from_payload(
        [{"Date": "2026-06-12", "Code": "2330", "HoldLevel": 15, "PeopleCount": 1500, "Shares": 20000000000, "Percentage": 77.12}],
        available_time="2026-06-12T19:00:00Z",
    )
    taifex_records = taifex_adapter.records_from_payload(
        [{"Date": "2026-06-10", "Contract": "TX", "ParticipantGroup": "foreign_investors", "LongVolume": 15000, "ShortVolume": 12000, "LongOpenInterest": 45000, "ShortOpenInterest": 50000}],
        dataset="taifex_futures_chip",
        available_time="2026-06-10T16:30:00Z",
    )

    # 2. Build Evidence Bundles and persist to durable evidence store
    evidence_path = Path(tmp_path) / "source_evidence.jsonl"
    repo = JsonlEvidenceRepository(evidence_path)
    builder = EvidenceBundleBuilder(repo)

    tdcc_item = EvidenceItem(
        evidence_item_id="evi-tdcc-2330-w24",
        source_id=tdcc_records[0].source_id,
        item_type="shareholding_distribution",
        content_ref=tdcc_records[0].content_ref,
        citation_label="TDCC Shareholding 2330 Level 15 2026-06-12",
        body="TDCC 2330 Level 15 top shareholders control 77.12% with 20000000000 shares.",
        event_time="2026-06-12T00:00:00Z",
        available_time="2026-06-12T19:00:00Z",
        confidence=1.0,
        access_scope=("research",),
        metadata={"entitlement_tags": ["official_reference"]},
    )
    tdcc_bundle = builder.build_bundle(
        source_records=[tdcc_records[0]],
        evidence_items=[tdcc_item],
        summary="TDCC official shareholding distribution for 2330",
        created_by="source-ingest",
        evidence_bundle_id="evbundle-tdcc-2330-001",
    )
    builder.build_knowledge_object(
        knowledge_object_id="kobj-tdcc-2330-001",
        source_record=tdcc_records[0],
        evidence_item=tdcc_item,
        evidence_bundle=tdcc_bundle,
        title=tdcc_records[0].title,
        text=tdcc_item.body,
        source_type="market",
        keywords=["TDCC", "2330", "shareholding", "top shareholders"],
    )

    taifex_item = EvidenceItem(
        evidence_item_id="evi-taifex-tx-001",
        source_id=taifex_records[0].source_id,
        item_type="derivatives_chip_flow",
        content_ref=taifex_records[0].content_ref,
        citation_label="TAIFEX TX Foreign Investors 2026-06-10",
        body="TAIFEX TX foreign investors net open interest -5000 contracts on 2026-06-10.",
        event_time="2026-06-10T00:00:00Z",
        available_time="2026-06-10T16:30:00Z",
        confidence=1.0,
        access_scope=("research",),
        metadata={"entitlement_tags": ["official_reference"]},
    )
    taifex_bundle = builder.build_bundle(
        source_records=[taifex_records[0]],
        evidence_items=[taifex_item],
        summary="TAIFEX official derivatives chip context for TX",
        created_by="source-ingest",
        evidence_bundle_id="evbundle-taifex-tx-001",
    )
    builder.build_knowledge_object(
        knowledge_object_id="kobj-taifex-tx-001",
        source_record=taifex_records[0],
        evidence_item=taifex_item,
        evidence_bundle=taifex_bundle,
        title=taifex_records[0].title,
        text=taifex_item.body,
        source_type="market",
        keywords=["TAIFEX", "TX", "foreign investors", "open interest"],
    )

    # 3. Trigger Search Index refresh and execute SearchGateway query
    search_app = create_search_app(
        index_store_path=Path(tmp_path) / "search-index.jsonl",
        evidence_store_path=evidence_path,
        materialize_store_path=Path(tmp_path) / "search-materialize.jsonl",
        pipeline_store_path=Path(tmp_path) / "search-pipeline.jsonl",
        freshness_sla_seconds=60,
    )
    search_client = TestClient(search_app)

    refresh_resp = search_client.post("/api/search/index/refresh", json={"triggered_by": "canary_test"})
    assert refresh_resp.status_code == 200

    # Query TDCC
    tdcc_query = search_client.post(
        "/api/search/query",
        json={
            "request_id": "req-tdcc-canary",
            "query": "TDCC shareholding top shareholders",
            "persona_id": "operator-workbench",
            "workspace_id": "research-workbench",
            "source_types": ["market"],
            "access_context": {
                "persona_id": "operator-workbench",
                "workspace_id": "research-workbench",
                "environment": "paper",
                "access_scopes": ["research"],
                "license_scopes": ["official_reference"],
            },
            "top_k": 5,
        },
    )
    assert tdcc_query.status_code == 200
    tdcc_res = tdcc_query.json()
    assert tdcc_res["index_adapter"]["adapter_state"] == "durable"
    assert len(tdcc_res["results"]) >= 1
    assert any("2330" in str(r.get("citations", [])) or "TDCC" in str(r.get("citations", [])) for r in tdcc_res["results"])

    # Query TAIFEX
    taifex_query = search_client.post(
        "/api/search/query",
        json={
            "request_id": "req-taifex-canary",
            "query": "TAIFEX foreign investors open interest",
            "persona_id": "operator-workbench",
            "workspace_id": "research-workbench",
            "source_types": ["market"],
            "access_context": {
                "persona_id": "operator-workbench",
                "workspace_id": "research-workbench",
                "environment": "paper",
                "access_scopes": ["research"],
                "license_scopes": ["official_reference"],
            },
            "top_k": 5,
        },
    )
    assert taifex_query.status_code == 200
    taifex_res = taifex_query.json()
    assert taifex_res["index_adapter"]["adapter_state"] == "durable"
    assert len(taifex_res["results"]) >= 1
    assert any("TAIFEX" in str(r.get("citations", [])) or "foreign" in str(r.get("citations", [])) for r in taifex_res["results"])
