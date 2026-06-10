from __future__ import annotations

import json

from services.source_ingestion.connectors.finmind_taiwan import (
    FINMIND_BROKER_REPORT_DATASET,
    FinMindTaiwanBrokerBulkBackfillAdapter,
    FinMindTaiwanBrokerDailyReportAdapter,
    FinMindTaiwanDatasetAdapter,
)


BROKER_DAILY_REPORT_PAYLOAD = {
    "msg": "success",
    "status": 200,
    "data": [
        {
            "date": "2026-06-08",
            "stock_id": "2330",
            "securities_trader_id": "9200",
            "securities_trader": "國泰-敦南",
            "price": 950.0,
            "buy": 4000,
            "sell": 300,
        },
        {
            "date": "2026-06-08",
            "stock_id": "2330",
            "securities_trader_id": "9200",
            "securities_trader": "國泰-敦南",
            "price": 951.0,
            "buy": 500,
            "sell": 100,
        },
        {
            "date": "2026-06-08",
            "stock_id": "2330",
            "securities_trader_id": "1020",
            "securities_trader": "合庫-台中",
            "price": 950.0,
            "buy": 1400,
            "sell": 200,
        },
        {
            "date": "2026-06-08",
            "stock_id": "2330",
            "securities_trader_id": "1440",
            "securities_trader": "美林",
            "price": 949.0,
            "buy": 100,
            "sell": 2100,
        },
    ],
}


def test_finmind_broker_daily_report_aggregates_to_top_buy_sell_rows() -> None:
    adapter = FinMindTaiwanBrokerDailyReportAdapter(max_rank=2)
    rows = adapter.top_rows_from_payload(
        BROKER_DAILY_REPORT_PAYLOAD,
        source_url="https://api.finmindtrade.com/api/v4/taiwan_stock_trading_daily_report?data_id=2330&date=2026-06-08",
        available_time="2026-06-08T13:00:00Z",
    )

    assert len(rows) == 3
    assert rows[0]["side"] == "buy"
    assert rows[0]["rank"] == 1
    assert rows[0]["broker"] == "國泰-敦南"
    assert rows[0]["broker_id"] == "9200"
    assert rows[0]["buy_qty"] == 4500
    assert rows[0]["sell_qty"] == 400
    assert rows[0]["net_qty"] == 4100
    assert rows[2]["side"] == "sell"
    assert rows[2]["rank"] == 1
    assert rows[2]["net_qty"] == -2000


def test_finmind_broker_daily_report_adapter_emits_tw_broker_top_records() -> None:
    adapter = FinMindTaiwanBrokerDailyReportAdapter(max_rank=2)
    connector = adapter.connector()
    records = adapter.records_from_daily_report_payload(
        BROKER_DAILY_REPORT_PAYLOAD,
        available_time="2026-06-08T13:00:00Z",
        trace_id="trace-finmind-broker",
    )

    assert connector.provider == "FinMind"
    assert connector.auth_policy.secret_ref.secret_ref_id == "env://FINMIND_API_TOKEN"
    assert connector.metadata["source_plan"] == "finmind_first_low_cost_paid_layer"
    assert connector.metadata["fallback_connector_id"] == "tw-yahoo-broker-top15"
    assert len(records) == 3
    assert records[0].connector_id == "tw-finmind-broker-daily-report"
    assert records[0].metadata["dataset"] == "tw_broker_top"
    assert records[0].metadata["source_dataset"] == FINMIND_BROKER_REPORT_DATASET
    assert records[0].metadata["schema_hash"] == "tw_broker_top.v1"


def test_finmind_generic_dataset_adapter_emits_research_records_without_raw_token() -> None:
    adapter = FinMindTaiwanDatasetAdapter(secret_ref_id="env://FINMIND_API_TOKEN")
    records = adapter.records_from_data_payload(
        "TaiwanStockInstitutionalInvestorsBuySell",
        {"data": [{"date": "2026-06-08", "stock_id": "2330", "buy": 1000, "sell": 250}]},
        trace_id="trace-finmind-dataset",
    )

    encoded = json.dumps(records[0].to_dict(), ensure_ascii=False)
    assert records[0].metadata["provider"] == "FinMind"
    assert records[0].metadata["dataset"] == "TaiwanStockInstitutionalInvestorsBuySell"
    assert "FINMIND_API_TOKEN" not in encoded


def test_finmind_sponsorpro_storage_object_records_redact_signed_url() -> None:
    adapter = FinMindTaiwanBrokerBulkBackfillAdapter()
    records = adapter.records_from_storage_objects_payload(
        {
            "data": [
                {
                    "object_name": "TaiwanStockTradingDailyReport/2026-06-08.parquet",
                    "url": "https://signed.example.test/download?token=raw-signed-token",
                    "expires_at": "2026-06-09T00:00:00Z",
                }
            ]
        },
        date="2026-06-08",
        trace_id="trace-finmind-storage",
    )

    encoded = json.dumps(records[0].to_dict(), ensure_ascii=False)
    assert records[0].metadata["signed_url_present"] is True
    assert records[0].metadata["signed_url_redacted"] is True
    assert records[0].metadata["raw_storage_partition"] == (
        "raw/finmind/TaiwanStockTradingDailyReport/date=2026-06-08/"
    )
    assert "raw-signed-token" not in encoded
    assert "https://signed.example.test" not in encoded

