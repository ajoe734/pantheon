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
    assert twse[-1].metadata["calendar_evidence"]["version"] == "twse-2026-schedule-v1"
    assert "calendar_evidence" not in tpex[-1].metadata


def test_taiwan_official_adapter_binds_exact_governed_twse_calendar_evidence() -> None:
    from services.source_ingestion.connectors.taiwan_official import (
        TWSE_2026_SCHEDULE_CALENDAR_SHA256,
        TWSE_2026_SCHEDULE_CALENDAR_VERSION,
    )

    adapter = TaiwanOfficialMarketDatasetAdapter(max_records=10)
    records = adapter.records_from_payload(
        "tw_price_daily",
        "TWSE",
        [
            {"Date": "1150210", "Code": "2330", "ClosingPrice": "950.00"},
            {"Date": "1150211", "Code": "2330", "ClosingPrice": "955.00"},
            {"Date": "1150223", "Code": "2330", "ClosingPrice": "956.00"},
            {"Date": "1150224", "Code": "2330", "ClosingPrice": "957.00"},
            {"Date": "1151008", "Code": "2330", "ClosingPrice": "960.00"},
        ],
        trace_id="trace-governed-twse-calendar",
    )

    assert "calendar_evidence" not in records[0].metadata
    evidence_lny = records[1].metadata["calendar_evidence"]
    assert evidence_lny["venue"] == "TWSE"
    assert evidence_lny["version"] == "twse-2026-lny-v1"
    assert evidence_lny["coverage_start"] == "2026-02-11"
    assert evidence_lny["coverage_end"] == "2026-02-23"
    assert evidence_lny["trading_days"] == ["2026-02-11", "2026-02-23"]
    assert evidence_lny["checksum"] == (
        "55b2e23b9bd30af666a99c98da2dbbfad568dcd655631b1c6347d12ee8381596"
    )

    evidence_lny_end = records[2].metadata["calendar_evidence"]
    assert evidence_lny_end["version"] == "twse-2026-lny-v1"

    evidence_sched_start = records[3].metadata["calendar_evidence"]
    assert evidence_sched_start["version"] == TWSE_2026_SCHEDULE_CALENDAR_VERSION
    assert evidence_sched_start["coverage_start"] == "2026-02-24"
    assert evidence_sched_start["coverage_end"] == "2026-12-31"

    evidence_oct = records[4].metadata["calendar_evidence"]
    assert evidence_oct["version"] == TWSE_2026_SCHEDULE_CALENDAR_VERSION
    assert evidence_oct["checksum"] == TWSE_2026_SCHEDULE_CALENDAR_SHA256
    assert "2026-10-09" in evidence_oct["holidays"]
    assert "2026-10-26" in evidence_oct["holidays"]
    assert "2026-12-25" in evidence_oct["holidays"]
    assert "2026-10-08" in evidence_oct["trading_days"]

    catalog = adapter.connector().metadata["governed_calendar_evidence"]
    assert len(catalog) == 3
    assert catalog[2]["version"] == "twse-2026-emergency-closure-20261023-v2"
    assert catalog[0]["version"] == "twse-2026-lny-v1"
    assert catalog[0]["sha256"] == evidence_lny["checksum"]
    assert catalog[1]["version"] == TWSE_2026_SCHEDULE_CALENDAR_VERSION
    assert catalog[1]["sha256"] == TWSE_2026_SCHEDULE_CALENDAR_SHA256
    assert catalog[1]["coverage_start"] == "2026-02-24"
    assert catalog[1]["coverage_end"] == "2026-12-31"

    # The production writer never relabels TWSE proof as TPEx proof or extends
    # the bounded catalog to an uncovered record date (e.g. year 2027).
    tpex = adapter.records_from_payload(
        "tw_price_daily",
        "TPEx",
        [{"Date": "1150211", "SecuritiesCompanyCode": "3105", "Close": "118.50"}],
    )
    future_2027 = adapter.records_from_payload(
        "tw_price_daily",
        "TWSE",
        [{"Date": "1160105", "Code": "2330", "ClosingPrice": "970.00"}],
    )
    assert "calendar_evidence" not in tpex[0].metadata
    assert "calendar_evidence" not in future_2027[0].metadata


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
        today_provider=lambda: "2026-06-10",
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

    with pytest.raises(SourceEvidenceError, match="cannot hold the minimum closes"):
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


def test_tw_history_trading_days_resolution_and_month_derivation(monkeypatch) -> None:
    from services.source_ingestion.connectors.taiwan_official import (
        derive_tw_history_max_months,
        resolve_tw_history_trading_days,
    )

    monkeypatch.delenv("SOURCE_INGEST_TW_HISTORY_TRADING_DAYS", raising=False)
    assert resolve_tw_history_trading_days() == 40
    assert resolve_tw_history_trading_days(30) == 30
    assert resolve_tw_history_trading_days("45") == 45
    assert resolve_tw_history_trading_days(-5) == 40
    assert resolve_tw_history_trading_days("invalid") == 40

    monkeypatch.setenv("SOURCE_INGEST_TW_HISTORY_TRADING_DAYS", "50")
    assert resolve_tw_history_trading_days() == 50
    assert resolve_tw_history_trading_days(25) == 25

    # Month derivation bounds
    assert derive_tw_history_max_months(1) == 2
    assert derive_tw_history_max_months(2) == 2
    assert derive_tw_history_max_months(30) == 3
    assert derive_tw_history_max_months(40) == 4
    assert derive_tw_history_max_months(60) == 5
    assert derive_tw_history_max_months(200) == 12


def test_tw_official_egress_allowlist_contains_twse_web_host() -> None:
    from services.source_ingestion.connector_definitions import get_connector_definition

    definition = get_connector_definition("tw-twse-tpex-official-market")
    assert "www.twse.com.tw" in definition.allowed_host_patterns
    assert "openapi.twse.com.tw" in definition.allowed_host_patterns
    assert "www.tpex.org.tw" in definition.allowed_host_patterns


def test_tw_official_records_and_rows_guarantee_aware_available_time() -> None:
    import re
    from services.source_ingestion.connectors.taiwan_official import _tw_price_available_time

    aware_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")

    # Direct helper normalization
    assert _tw_price_available_time("2026-06-10", "2026-06-10") == "2026-06-10T14:30:00+08:00"
    assert _tw_price_available_time(None, "115/06/10") == "2026-06-10T14:30:00+08:00"
    assert _tw_price_available_time("2026-06-10T14:30:00+08:00", "2026-06-10") == "2026-06-10T14:30:00+08:00"
    assert aware_pattern.match(_tw_price_available_time("2026-06-10", "2026-06-10"))

    # Adapter records guarantee aware available_time on metadata and normalized_row
    adapter = TaiwanOfficialMarketDatasetAdapter(max_records=10)
    records = adapter.records_from_price_history_payload(
        "2330.TWSE",
        "TWSE",
        TWSE_PRICE_HISTORY_PAYLOAD,
        available_time="2026-06-10",
    )
    assert len(records) == 2
    for record in records:
        avail = record.metadata.get("available_time")
        assert avail is not None
        assert aware_pattern.match(avail), f"available_time is not aware: {avail}"
        assert not re.match(r"^\d{4}-\d{2}-\d{2}$", avail), "available_time must not be a bare date"
        row = record.metadata.get("normalized_row")
        assert isinstance(row, dict)
        row_avail = row.get("available_time")
        assert aware_pattern.match(row_avail), f"row.available_time is not aware: {row_avail}"
        assert not re.match(r"^\d{4}-\d{2}-\d{2}$", row_avail), "row.available_time must not be a bare date"


def test_bounded_official_refresh_unions_active_paper_and_history_symbols(monkeypatch) -> None:
    monkeypatch.setenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "2330.TW")
    monkeypatch.setenv("SOURCE_INGEST_TW_HISTORY_SYMBOLS", "2317.TW")

    adapter = TaiwanOfficialMarketDatasetAdapter(max_records=10)
    connector = adapter.connector()

    twse_today = [
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
        },
        {
            "Date": "1150610",
            "Code": "2317",
            "Name": "鴻海",
            "TradeVolume": "25000000",
            "TradeValue": "5000000000",
            "OpeningPrice": "200.00",
            "HighestPrice": "205.00",
            "LowestPrice": "198.00",
            "ClosingPrice": "202.00",
            "Change": "+2.00",
            "Transaction": "12000",
        },
    ]

    twse_2317_history = {
        "stat": "OK",
        "date": "20260610",
        "title": "115年06月 2317 鴻海 各日成交資訊",
        "fields": TWSE_PRICE_HISTORY_PAYLOAD["fields"],
        "data": [
            ["115/06/09", "15,000,000", "3,000,000,000", "198.00", "201.00", "197.00", "200.00", "+1.00", "10,000", ""],
            ["115/06/10", "25,000,000", "5,000,000,000", "200.00", "205.00", "198.00", "202.00", "+2.00", "12,000", ""],
        ],
    }

    records = execute_provider_owned_adapter(
        connector=connector,
        fetch={
            "mode": "provider_owned_adapter",
            "adapter": "TaiwanOfficialMarketDatasetAdapter",
            "adapter_config": {"max_records": 10},
            "request": {
                "dataset": "tw_price_daily",
                "venues": ["TWSE"],
                "payloads": {"TWSE": twse_today},
                "history_payloads": {
                    "2330.TWSE": TWSE_PRICE_HISTORY_PAYLOAD,
                    "2317.TWSE": twse_2317_history,
                },
            },
            "max_records": 10,
        },
        trace_id="trace-union-symbols",
    )

    symbols = {record.metadata["symbol_canonical"] for record in records}
    assert symbols == {"2330.TWSE", "2317.TWSE"}
    assert len([r for r in records if r.metadata["symbol_canonical"] == "2330.TWSE"]) == 2
    assert len([r for r in records if r.metadata["symbol_canonical"] == "2317.TWSE"]) == 2


def test_bounded_official_refresh_retains_configured_history_depth(monkeypatch) -> None:
    import re
    aware_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")

    monkeypatch.setenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "2330.TW")
    monkeypatch.delenv("SOURCE_INGEST_TW_HISTORY_SYMBOLS", raising=False)

    adapter = TaiwanOfficialMarketDatasetAdapter(max_records=100)
    connector = adapter.connector()

    # Generate 45 trading days across two months: May (22 days) and June (23 days)
    may_data = [
        [f"115/05/{day:02d}", "10,000,000", "9,000,000,000", "900.00", "920.00", "890.00", f"{900.0 + day:.2f}", "+1.00", "8,000", ""]
        for day in range(1, 23)
    ]
    june_data = [
        [f"115/06/{day:02d}", "10,000,000", "9,000,000,000", "920.00", "940.00", "910.00", f"{920.0 + day:.2f}", "+1.00", "8,000", ""]
        for day in range(1, 24)
    ]
    all_history_data = may_data + june_data  # 45 distinct trading days

    multi_month_history = {
        "stat": "OK",
        "date": "20260623",
        "title": "115年06月 2330 台積電 各日成交資訊",
        "fields": TWSE_PRICE_HISTORY_PAYLOAD["fields"],
        "data": all_history_data,
    }

    twse_today = [
        {
            "Date": "1150623",
            "Code": "2330",
            "Name": "台積電",
            "TradeVolume": "10000000",
            "TradeValue": "9000000000",
            "OpeningPrice": "920.00",
            "HighestPrice": "940.00",
            "LowestPrice": "910.00",
            "ClosingPrice": "943.00",
            "Change": "+1.00",
            "Transaction": "8000",
        }
    ]

    records = execute_provider_owned_adapter(
        connector=connector,
        fetch={
            "mode": "provider_owned_adapter",
            "adapter": "TaiwanOfficialMarketDatasetAdapter",
            "adapter_config": {"max_records": 100},
            "request": {
                "dataset": "tw_price_daily",
                "venues": ["TWSE"],
                "payloads": {"TWSE": twse_today},
                "history_payloads": {
                    "2330.TWSE": multi_month_history,
                },
                "history_trading_days": 40,
            },
            "max_records": 100,
        },
        trace_id="trace-depth-40",
    )

    # 40 distinct closes retained (instead of previous 2)
    assert len(records) == 40
    distinct_dates = [record.metadata["event_time"] for record in records]
    assert len(set(distinct_dates)) == 40
    # Chronologically sorted
    assert distinct_dates == sorted(distinct_dates)

    # Every record guarantees finite positive close and aware available_time
    for record in records:
        assert record.metadata["symbol_canonical"] == "2330.TWSE"
        norm = record.metadata["normalized_row"]
        assert isinstance(norm["close"], (int, float)) and norm["close"] > 0
        assert aware_pattern.match(record.metadata["available_time"])
        assert aware_pattern.match(norm["available_time"])
        assert not re.match(r"^\d{4}-\d{2}-\d{2}$", record.metadata["available_time"])



def _incident_20261102_request(max_records: int) -> dict:
    """2026-11-02 hosted shape: three TWSE priority symbols, 40-day history, many daily-all rows."""
    fields = TWSE_PRICE_HISTORY_PAYLOAD["fields"]

    def history(code: str, name: str) -> dict:
        rows = [
            [f"115/05/{day:02d}", "1,000", "1,000", "10.00", "11.00", "9.00", f"{10.0 + day / 100:.2f}", "+0.01", "10", ""]
            for day in range(1, 23)
        ] + [
            [f"115/06/{day:02d}", "1,000", "1,000", "10.00", "11.00", "9.00", f"{11.0 + day / 100:.2f}", "+0.01", "10", ""]
            for day in range(1, 24)
        ]
        return {"stat": "OK", "date": "20260623", "title": f"115年06月 {code} {name} 各日成交資訊", "fields": fields, "data": rows}

    def daily(code: str) -> dict:
        return {
            "Date": "1150623",
            "Code": code,
            "Name": code,
            "TradeVolume": "1000",
            "TradeValue": "1000",
            "OpeningPrice": "10.00",
            "HighestPrice": "11.00",
            "LowestPrice": "9.00",
            "ClosingPrice": "11.23",
            "Change": "+0.01",
            "Transaction": "10",
        }

    codes = ("2330", "2317", "0050")
    return {
        "dataset": "tw_price_daily",
        "venues": ["TWSE"],
        "payloads": {"TWSE": [daily(code) for code in codes] + [daily(f"{index:04d}") for index in range(1101, 1201)]},
        "history_payloads": {f"{code}.TWSE": history(code, code) for code in codes},
        "history_trading_days": 40,
        "max_records": max_records,
    }


def test_bounded_official_refresh_keeps_newest_dates_for_every_priority_symbol_under_hosted_limit(monkeypatch) -> None:
    """Hosted fetch.max_records=100: before the fix 2330=40, 2317=40, 0050=20 (oldest rows only)."""
    monkeypatch.setenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "2330.TWSE,2317.TWSE,0050.TWSE")
    monkeypatch.delenv("SOURCE_INGEST_TW_HISTORY_SYMBOLS", raising=False)
    request = _incident_20261102_request(100)

    records = execute_provider_owned_adapter(
        connector=TaiwanOfficialMarketDatasetAdapter(max_records=100).connector(),
        fetch={
            "mode": "provider_owned_adapter",
            "adapter": "TaiwanOfficialMarketDatasetAdapter",
            "adapter_config": {"max_records": 100},
            "request": request,
            "max_records": 100,
        },
        trace_id="trace-20261102-three-priority",
    )

    assert len(records) == 100
    for symbol in ("2330.TWSE", "2317.TWSE", "0050.TWSE"):
        times = [r.metadata["event_time"] for r in records if r.metadata["symbol_canonical"] == symbol]
        assert len(times) == 33, symbol
        assert times == sorted(times)
        assert times[-1] == "2026-06-23", symbol
    assert {r.metadata["symbol_canonical"] for r in records[:99]} == {"2330.TWSE", "2317.TWSE", "0050.TWSE"}


def test_bounded_official_refresh_fails_before_fetch_when_budget_cannot_hold_priority_history(monkeypatch) -> None:
    monkeypatch.setenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "2330.TWSE,2317.TWSE,0050.TWSE")
    monkeypatch.delenv("SOURCE_INGEST_TW_HISTORY_SYMBOLS", raising=False)

    def forbidden_fetch(*args, **kwargs):
        raise AssertionError("provider fetch must not run when the record budget is too small")

    monkeypatch.setattr(TaiwanOfficialMarketDatasetAdapter, "fetch_payload", forbidden_fetch)
    monkeypatch.setattr(TaiwanOfficialMarketDatasetAdapter, "fetch_price_history_records", forbidden_fetch)
    request = _incident_20261102_request(5)
    del request["payloads"]
    del request["history_payloads"]

    with pytest.raises(SourceEvidenceError) as excinfo:
        execute_provider_owned_adapter(
            connector=TaiwanOfficialMarketDatasetAdapter(max_records=5).connector(),
            fetch={
                "mode": "provider_owned_adapter",
                "adapter": "TaiwanOfficialMarketDatasetAdapter",
                "adapter_config": {"max_records": 5},
                "request": request,
                "max_records": 5,
            },
            trace_id="trace-20261102-budget",
        )
    message = str(excinfo.value)
    assert "2330.TWSE,2317.TWSE,0050.TWSE" in message
    assert "required=6" in message
    assert "allowed=5" in message


def test_taiwan_official_adapter_binds_emergency_closure_v2_for_oct22_price() -> None:
    adapter = TaiwanOfficialMarketDatasetAdapter(max_records=10)
    records = adapter.records_from_payload(
        "tw_price_daily",
        "TWSE",
        [
            {"Date": "1151021", "Code": "2330", "ClosingPrice": "960.00"},
            {"Date": "1151022", "Code": "2330", "ClosingPrice": "961.00"},
            {"Date": "1151028", "Code": "2330", "ClosingPrice": "962.00"},
            {"Date": "1151029", "Code": "2330", "ClosingPrice": "963.00"},
        ],
        trace_id="trace-emergency-closure",
    )
    # Records outside the verified window keep the retained v1 evidence.
    assert records[0].metadata["calendar_evidence"]["version"] == "twse-2026-schedule-v1"
    assert records[3].metadata["calendar_evidence"]["version"] == "twse-2026-schedule-v1"
    for record in records[1:3]:
        evidence = record.metadata["calendar_evidence"]
        assert evidence["version"] == "twse-2026-emergency-closure-20261023-v2"
        assert evidence["source_url"].startswith("https://www.twse.com.tw/staticFiles/")
        assert "2026-10-23" in evidence["holidays"]
        assert "2026-10-23" not in evidence["trading_days"]
        assert evidence["trading_days"] == ["2026-10-22", "2026-10-27", "2026-10-28"]


_NOV_BOUNDARY_FIELDS = TWSE_PRICE_HISTORY_PAYLOAD["fields"]


def _patch_month_boundary_history(monkeypatch, *, november_rows, calls):
    def fake_fetch(self, symbol, venue, *, anchor_date, timeout_seconds=20.0):
        del timeout_seconds
        calls.append(anchor_date)
        endpoint = self.price_history_endpoint(symbol, venue, anchor_date=anchor_date)
        if anchor_date == "2026-11-01":
            if not november_rows:
                return {"stat": "很抱歉，沒有符合條件的資料!"}, endpoint
            return {
                "stat": "OK",
                "date": "20261102",
                "title": "115年11月 2330 台積電 各日成交資訊",
                "fields": _NOV_BOUNDARY_FIELDS,
                "data": november_rows,
            }, endpoint
        assert anchor_date == "2026-10-01"
        return {
            "stat": "OK",
            "date": "20261001",
            "title": "115年10月 2330 台積電 各日成交資訊",
            "fields": _NOV_BOUNDARY_FIELDS,
            "data": [
                ["115/10/29", "20,000,000", "1", "2,360.00", "2,380.00", "2,350.00", "2,365.00", "+5.00", "1", ""],
                ["115/10/30", "20,000,000", "1", "2,365.00", "2,380.00", "2,360.00", "2,370.00", "+5.00", "1", ""],
            ],
        }, endpoint

    monkeypatch.setattr(TaiwanOfficialMarketDatasetAdapter, "fetch_price_history_payload", fake_fetch)
    monkeypatch.setattr(
        "services.source_ingestion.connectors.taiwan_official._taipei_today",
        lambda: "2026-11-02",
    )


def test_bounded_refresh_fetches_current_month_when_openapi_row_lags(monkeypatch) -> None:
    monkeypatch.setenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "2330.TW")
    monkeypatch.delenv("SOURCE_INGEST_TW_HISTORY_SYMBOLS", raising=False)
    calls: list[str] = []
    _patch_month_boundary_history(
        monkeypatch,
        november_rows=[
            ["115/11/02", "20,000,000", "1", "2,370.00", "2,380.00", "2,320.00", "2,330.00", "-40.00", "1", ""],
        ],
        calls=calls,
    )
    adapter = TaiwanOfficialMarketDatasetAdapter(max_records=100)
    records = execute_provider_owned_adapter(
        connector=adapter.connector(),
        fetch={
            "mode": "provider_owned_adapter",
            "adapter": "TaiwanOfficialMarketDatasetAdapter",
            "adapter_config": {"max_records": 100},
            "request": {
                "dataset": "tw_price_daily",
                "venues": ["TWSE"],
                "payloads": {
                    "TWSE": [
                        {"Date": "1151030", "Code": "2330", "Name": "台積電", "ClosingPrice": "2370.00"}
                    ]
                },
                "history_trading_days": 3,
            },
            "max_records": 100,
        },
        trace_id="trace-month-boundary",
    )

    assert calls == ["2026-11-01", "2026-10-01"]
    dates = [record.metadata["event_time"] for record in records]
    assert dates == ["2026-10-29", "2026-10-30", "2026-11-02"]
    latest = records[-1].metadata["normalized_row"]
    assert latest["date"] == "2026-11-02"
    assert latest["close"] == 2330.0


def test_bounded_refresh_tolerates_empty_current_month_window(monkeypatch) -> None:
    calls: list[str] = []
    _patch_month_boundary_history(monkeypatch, november_rows=[], calls=calls)
    records = TaiwanOfficialMarketDatasetAdapter(max_records=10).fetch_price_history_records(
        "2330.TWSE",
        "TWSE",
        anchor_date="2026-10-30",
        trace_id="trace-empty-current-month",
    )

    assert calls == ["2026-11-01", "2026-10-01"]
    assert {record.metadata["event_time"] for record in records} == {"2026-10-29", "2026-10-30"}


def test_history_fetch_never_requests_months_after_current_taipei_month(monkeypatch) -> None:
    calls: list[str] = []
    _patch_month_boundary_history(monkeypatch, november_rows=[], calls=calls)
    TaiwanOfficialMarketDatasetAdapter(max_records=10).fetch_price_history_records(
        "2330.TWSE",
        "TWSE",
        anchor_date="2026-10-30",
        trace_id="trace-no-future",
        today_provider=lambda: "2026-10-31",
    )
    assert calls == ["2026-10-01"]


# --- taifex_ssf_contracts (TAIFEX-SSF-CONTRACT-CANDIDATES-20261107) -------------------------------

import csv as _csv
import io as _io
import json as _json
from pathlib import Path as _Path

from services.source_ingestion.connectors import taiwan_official as _tw_official

_SSF_FIXTURES = _Path(__file__).resolve().parents[2] / "execution" / "lean_runtime" / "fixtures" / "taifex_20261106"


def _ssf_payload() -> dict:
    def load(name: str):
        return _json.loads((_SSF_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))

    return {
        "SingleStockFuturesMargining": load("SingleStockFuturesMargining"),
        "SingleStockFuturesETFMargining": load("SingleStockFuturesETFMargining"),
        "DailyMarketReportFut": load("DailyMarketReportFut"),
        "FinalSettlementPriceSSF": load("FinalSettlementPriceSSF"),
        "stockLists": (_SSF_FIXTURES / "stockLists.html").read_text(encoding="utf-8"),
    }


def _ssf_row(rows, contract: str, month: str = "202611") -> dict:
    return next(row for row in rows if row["contract"] == contract and row["contract_month"] == month)


def test_taifex_ssf_dataset_is_declared_on_the_connector_definition() -> None:
    from services.source_ingestion.connector_definitions import get_connector_definition

    definition = get_connector_definition("tw-taifex-futures-options-chip")
    assert "taifex_ssf_contracts" in definition.datasets
    assert {"taifex_futures_chip", "taifex_options_chip"} <= set(definition.datasets)
    assert definition.allowed_host_patterns == ("openapi.taifex.com.tw", "www.taifex.com.tw")


def test_taifex_ssf_normalization_joins_margin_market_unit_and_final_settlement() -> None:
    adapter = _tw_official.TaifexDerivativesChipAdapter()
    rows = adapter.normalized_rows_from_payload(_ssf_payload(), dataset="taifex_ssf_contracts")

    qff = _ssf_row(rows, "QFF")
    assert qff["contract_id"] == "ssf:QFF:202611"
    assert (qff["underlying_code"], qff["unit"], qff["kind"], qff["underlying_kind"]) == ("2330", 100, "stock_future", "stock")
    assert (qff["settlement_price"], qff["volume"], qff["open_interest"], qff["date"]) == (2133.0, 126386, 65034, "2026-11-06")
    assert qff["margin_mode"] == "rate" and qff["margin_tier"] == "級距1"
    assert (qff["clearing_margin_rate"], qff["maintenance_margin_rate"], qff["initial_margin_rate"]) == (0.10, 0.1035, 0.135)
    assert qff["last_final_settlement"] == {"day": "2026-10-21", "contract_month": "202610", "price": 2405.59}
    assert qff["last_trading_day"] == "2026-11-18"
    assert _ssf_row(rows, "CDF")["unit"] == 2000 and _ssf_row(rows, "DHF")["settlement_price"] == 225.0

    srf, nyf = _ssf_row(rows, "SRF"), _ssf_row(rows, "NYF")
    assert (srf["margin_mode"], srf["initial_margin_amount"], srf["maintenance_margin_amount"]) == ("fixed", 8700.0, 6700.0)
    assert (nyf["unit"], nyf["initial_margin_amount"], nyf["underlying_kind"]) == (10000, 87000.0, "etf")
    assert srf["unit"] == 1000 and srf["underlying_code"] == "0050"
    # Spread rows and after-hours rows never become contract rows.
    assert all("/" not in row["contract_month"] for row in rows)
    assert all(row["raw_market_row"]["TradingSession"] == "一般" for row in rows)


def test_taifex_ssf_symbol_scope_limits_underlyings() -> None:
    adapter = _tw_official.TaifexDerivativesChipAdapter()
    for raw_code in ("2317", "2317.TW", "2317.TWSE", "eq:2317"):
        rows = adapter.normalized_rows_from_payload(
            _ssf_payload(), dataset="taifex_ssf_contracts", contracts=[raw_code]
        )
        assert {row["underlying_code"] for row in rows} == {"2317"}


def test_taifex_ssf_csv_with_bom_decodes_as_utf8_sig() -> None:
    payload = _ssf_payload()
    buffer = _io.StringIO()
    writer = _csv.DictWriter(buffer, fieldnames=list(payload["SingleStockFuturesETFMargining"][0]))
    writer.writeheader()
    writer.writerows(payload["SingleStockFuturesETFMargining"])
    bom_bytes = b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")

    assert _tw_official._decode_taifex_bytes(bom_bytes)[0] == "C"
    for variant in (bom_bytes, bom_bytes.decode("utf-8"), _tw_official._decode_taifex_bytes(bom_bytes)):
        payload["SingleStockFuturesETFMargining"] = variant
        rows = _tw_official.TaifexDerivativesChipAdapter().normalized_rows_from_payload(
            payload, dataset="taifex_ssf_contracts"
        )
        assert _ssf_row(rows, "SRF")["initial_margin_amount"] == 8700.0
        assert _ssf_row(rows, "NYF")["initial_margin_amount"] == 87000.0


def test_taifex_ssf_rows_need_a_unit_and_a_numeric_settlement() -> None:
    payload = _ssf_payload()
    payload["stockLists"] = {}
    assert _tw_official.TaifexDerivativesChipAdapter().normalized_rows_from_payload(payload, dataset="taifex_ssf_contracts") == ()
    payload = _ssf_payload()
    payload["DailyMarketReportFut"] = [
        {**row, "SettlementPrice": "NULL"} for row in payload["DailyMarketReportFut"]
    ]
    rows = _tw_official.TaifexDerivativesChipAdapter().normalized_rows_from_payload(
        payload, dataset="taifex_ssf_contracts"
    )
    assert rows
    assert all(row["settlement_price"] is None for row in rows)


def test_taifex_ssf_empty_scoped_snapshot_emits_dated_tombstone_record() -> None:
    adapter = _tw_official.TaifexDerivativesChipAdapter()
    payload = _ssf_payload()
    trimmed = {
        **payload,
        "SingleStockFuturesMargining": [
            r for r in payload["SingleStockFuturesMargining"] if r["Contract"] != "DHF"
        ],
        "DailyMarketReportFut": [
            r for r in payload["DailyMarketReportFut"] if r["Contract"] != "DHF"
        ],
    }
    rows = adapter.normalized_rows_from_payload(
        trimmed,
        dataset="taifex_ssf_contracts",
        contracts=["2317"],
        available_time="2026-11-06T17:00:00Z",
    )
    assert len(rows) == 1
    assert rows[0]["tombstone"] is True
    assert rows[0]["snapshot_scope_mode"] == "scoped"
    assert rows[0]["snapshot_scope"] == ["2317"]
    assert rows[0]["date"] == "2026-11-06"
    records = adapter.records_from_payload(
        trimmed,
        dataset="taifex_ssf_contracts",
        contracts=["2317"],
        available_time="2026-11-06T17:00:00Z",
    )
    assert len(records) == 1
    assert records[0].metadata["date"] == "2026-11-06"
    assert records[0].metadata["normalized_row"]["tombstone"] is True


def test_taifex_ssf_rejects_a_non_mapping_payload() -> None:
    with pytest.raises(SourceEvidenceError):
        _tw_official.TaifexDerivativesChipAdapter().normalized_rows_from_payload([], dataset="taifex_ssf_contracts")


def test_taifex_ssf_stock_lists_units() -> None:
    units = _tw_official.parse_taifex_stock_lists_units((_SSF_FIXTURES / "stockLists.html").read_text(encoding="utf-8"))
    assert {family: row["unit"] for family, row in units.items()} == {
        "CA": 2000, "CC": 2000, "CD": 2000, "DH": 2000, "NY": 10000, "QF": 100, "SR": 1000,
    }
    assert units["NY"]["is_etf"] and units["SR"]["is_etf"] and not units["QF"]["is_etf"]
    assert units["QF"]["underlying_code"] == "2330"


def test_taifex_ssf_records_carry_partition_date_and_schema() -> None:
    adapter = _tw_official.TaifexDerivativesChipAdapter()
    records = adapter.records_from_payload(_ssf_payload(), dataset="taifex_ssf_contracts", trace_id="t")
    assert records
    record = next(r for r in records if r.metadata["contract"] == "QFF" and r.metadata["contract_month"] == "202611")
    assert record.metadata["dataset"] == "taifex_ssf_contracts"
    assert record.metadata["date"] == "2026-11-06"
    assert record.metadata["schema_hash"] == "tw_taifex_ssf_contracts.v1"
    assert record.metadata["normalized_row"]["contract_id"] == "ssf:QFF:202611"
    assert record.metadata["raw_row"]["margin"]["Contract"] == "QFF"


def test_taifex_ssf_fetch_reads_only_the_two_reviewed_hosts(monkeypatch) -> None:
    payload = _ssf_payload()
    seen: list[str] = []

    def fake_fetch(request, *, caller, timeout_seconds, max_bytes, parse):
        seen.append(request.full_url)
        name = request.full_url.rsplit("/", 1)[-1]
        if name == "stockLists":
            return parse(payload["stockLists"].encode("utf-8"))
        return parse(b"\xef\xbb\xbf" + _json.dumps(payload[name]).encode("utf-8"))

    monkeypatch.setattr(_tw_official, "_fetch_official", fake_fetch)
    fetched = _tw_official.TaifexDerivativesChipAdapter().fetch_payload("taifex_ssf_contracts")

    assert sorted(fetched) == sorted(payload)
    hosts = {url.split("/")[2] for url in seen}
    assert hosts == {"openapi.taifex.com.tw", "www.taifex.com.tw"}
    assert len(seen) == 5
    rows = _tw_official.TaifexDerivativesChipAdapter().normalized_rows_from_payload(fetched, dataset="taifex_ssf_contracts")
    assert _ssf_row(rows, "QFF")["settlement_price"] == 2133.0
