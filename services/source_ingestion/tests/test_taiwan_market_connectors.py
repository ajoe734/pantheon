from __future__ import annotations

from services.research.adapters.taiwan_market_client import TaiwanMarketClient
from services.source_ingestion.connectors import MopsSourceIngestAdapter, TejSourceIngestAdapter


def test_mops_source_ingest_adapter_emits_official_filing_records() -> None:
    adapter = MopsSourceIngestAdapter(connector_id="conn-mops-test")
    connector = adapter.connector()
    route = TaiwanMarketClient().mops_route("t05st02")
    payload = {
        "code": 200,
        "message": "查詢成功",
        "result": {
            "titles": [{"main": "發言日期"}, {"main": "發言時間"}, {"main": "公司代號"}, {"main": "公司名稱"}, {"main": "主旨"}],
            "data": [["115/06/08", "14:07:19", "2428", "興勤", "本公司115年05月份自結合併營業收入公告"]],
        },
        "datetime": "115/06/08 23:11:35",
    }

    records = adapter.records_from_payload(route, payload, trace_id="trace-mops")

    assert connector.source_type.value == "filing"
    assert connector.metadata["source_class"] == "official_reference"
    assert records[0].connector_id == "conn-mops-test"
    assert records[0].source_type.value == "filing"
    assert records[0].content_ref.startswith("mops://t05st02/2428/")
    assert records[0].metadata["source_class"] == "official_reference"
    assert records[0].metadata["route_id"] == "t05st02"


def test_tej_source_ingest_adapter_emits_research_market_records_without_raw_secret() -> None:
    adapter = TejSourceIngestAdapter(connector_id="conn-tej-test", secret_ref_id="env://TEJ_API_KEY")
    connector = adapter.connector()
    table = TaiwanMarketClient().tej_trial_table_inventory_from_payload(
        {"tables": [{"tableName": "TATINST1", "cName": "三大法人買賣超", "groupName": "公司交易面資料"}]}
    )[0]

    records = adapter.records_from_rows(
        table,
        [{"coid": "2330", "mdate": "2026-04-24", "foreign_buy": 1000, "dealer_sell": 200}],
        trace_id="trace-tej",
    )

    assert connector.source_type.value == "market"
    assert connector.auth_policy.secret_ref.secret_ref_id == "env://TEJ_API_KEY"
    assert connector.metadata["source_class"] == "research_grade"
    assert records[0].content_ref.startswith("tej://TRAIL/TATINST1/2330/")
    assert records[0].metadata["source_class"] == "research_grade"
    assert records[0].metadata["dataset_code"] == "TRAIL/TATINST1"
    assert "TEJ_API_KEY" not in records[0].metadata["body"]
