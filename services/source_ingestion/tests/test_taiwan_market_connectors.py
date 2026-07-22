from __future__ import annotations

from services.research.adapters.taiwan_market_client import TaiwanMarketClient
from services.source_ingestion.connectors import MopsSourceIngestAdapter, TejSourceIngestAdapter
from services.source_ingestion.provider_adapters import execute_provider_owned_adapter, provider_adapter_tokens


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
    assert records[0].metadata["normalized_target"] == "tw_material_event"
    assert records[0].metadata["schema_hash"] == "tw_material_event.v1"
    assert records[0].metadata["schedule_profile"]["universe_tiers"] == [
        "core_universe",
        "candidate_universe",
        "archive_universe",
    ]


def test_mops_provider_owned_adapter_alias_fetches_allowlisted_route(monkeypatch) -> None:
    adapter = MopsSourceIngestAdapter(connector_id="conn-mops-test")
    payload = {
        "code": 200,
        "message": "查詢成功",
        "result": {
            "titles": [{"main": "發言日期"}, {"main": "發言時間"}, {"main": "公司代號"}, {"main": "公司名稱"}, {"main": "主旨"}],
            "data": [["115/06/08", "14:07:19", "2428", "興勤", "本公司115年05月份自結合併營業收入公告"]],
        },
        "datetime": "115/06/08 23:11:35",
    }

    def fake_fetch(self, route_id, params=None):  # noqa: ANN001 - monkeypatch keeps the original method shape.
        assert route_id == "t05st02"
        assert params == {"year": "115", "month": "06", "day": "08"}
        return payload

    monkeypatch.setattr(TaiwanMarketClient, "fetch_mops_route", fake_fetch)
    records = execute_provider_owned_adapter(
        connector=adapter.connector(),
        fetch={
            "mode": "provider_owned_adapter",
            "adapter": "MopsSourceIngestAdapter",
            "adapter_config": {"max_records": 10},
            "request": {
                "route_id": "t05st02",
                "params": {"year": "115", "month": "06", "day": "08"},
            },
            "max_records": 10,
        },
        trace_id="trace-mops-provider-owned",
    )

    assert "MopsSourceIngestAdapter" in set(provider_adapter_tokens())
    assert records[0].connector_id == "conn-mops-test"
    assert records[0].metadata["provider_owned_adapter"] == "MopsSourceIngestAdapter.records_from_payload"
    assert records[0].metadata["route_id"] == "t05st02"


def test_mops_monthly_revenue_rows_preserve_fiscal_and_availability_fields() -> None:
    adapter = MopsSourceIngestAdapter(connector_id="conn-mops-test")
    route = TaiwanMarketClient().mops_route("t05st10_ifrs")
    payload = {
        "result": {
            "titles": [
                {"main": "營收發布日期"},
                {"main": "資料年月"},
                {"main": "公司代號"},
                {"main": "公司名稱"},
                {"main": "當月營收"},
                {"main": "去年同月增減(%)"},
                {"main": "備註"},
            ],
            "data": [["115/06/10", "115/05", "2330", "台積電", "123,456,789", "39.6", ""]],
        },
        "datetime": "115/06/10 19:00:00",
    }

    record = adapter.records_from_payload(route, payload, trace_id="trace-mops-revenue")[0]
    normalized = record.metadata["normalized_record"]

    assert record.metadata["normalized_target"] == "tw_monthly_revenue"
    assert record.metadata["fiscal_year"] == "115"
    assert record.metadata["fiscal_month"] == "05"
    assert record.metadata["announcement_date"] == "115/06/10"
    assert record.metadata["available_time"] == "115/06/10 19:00:00"
    assert normalized["raw_route_id"] == "t05st10_ifrs"
    assert normalized["monthly_revenue"]["current_month_revenue"] == 123456789
    assert normalized["monthly_revenue"]["year_over_year_pct"] == 39.6
    assert record.metadata["schedule_profile"]["universe_tiers"] == ["core_universe"]


def test_mops_financial_statement_and_restatement_gap_metadata() -> None:
    adapter = MopsSourceIngestAdapter(connector_id="conn-mops-test")
    route = TaiwanMarketClient().mops_route("t164sb04")
    payload = {
        "result": {
            "data": [
                {
                    "公司代號": "2330",
                    "公司名稱": "台積電",
                    "年度": "115",
                    "季別": "1",
                    "公告日期": "115/05/15",
                    "會計項目": "營業收入",
                    "金額": "1,234,567",
                }
            ]
        },
        "datetime": "115/05/15 18:30:00",
    }

    record = adapter.records_from_payload(route, payload, trace_id="trace-mops-financial")[0]
    normalized = record.metadata["normalized_record"]
    gap_report = adapter.fetch_config()["restatement_correction_gap_report"]

    assert record.metadata["normalized_target"] == "tw_financial_statement"
    assert record.metadata["fiscal_year"] == "115"
    assert record.metadata["fiscal_quarter"] == "1"
    assert normalized["raw_route_id"] == "t164sb04"
    assert normalized["financial_statement"]["statement_type"] == "income_statement"
    assert normalized["financial_statement"]["line_items"]["會計項目"] == "營業收入"
    assert any(route["route_id"] == "t56sb31_q1" for route in gap_report["represented_routes"])
    assert any("correction" in route["tags"] for route in gap_report["represented_routes"])


def test_mops_company_master_and_corporate_action_targets_are_normalized() -> None:
    adapter = MopsSourceIngestAdapter(connector_id="conn-mops-test")
    client = TaiwanMarketClient()

    company_record = adapter.records_from_payload(
        client.mops_route("t05st03"),
        {
            "result": {
                "data": [
                    {
                        "公司代號": "2330",
                        "公司名稱": "台灣積體電路製造股份有限公司",
                        "產業別": "半導體業",
                        "董事長": "劉德音",
                    }
                ]
            },
            "datetime": "115/06/10 08:00:00",
        },
        trace_id="trace-mops-company",
    )[0]
    action_record = adapter.records_from_payload(
        client.mops_route("t05st09_2"),
        {
            "result": {
                "data": [
                    {
                        "公司代號": "2330",
                        "公司名稱": "台積電",
                        "年度": "115",
                        "公告日期": "115/06/20",
                        "現金股利": "4.0",
                    }
                ]
            },
            "datetime": "115/06/20 18:00:00",
        },
        trace_id="trace-mops-action",
    )[0]

    assert company_record.metadata["normalized_target"] == "tw_company_master"
    assert company_record.metadata["normalized_record"]["company_master"]["industry"] == "半導體業"
    assert action_record.metadata["normalized_target"] == "tw_corporate_action"
    assert action_record.metadata["normalized_record"]["corporate_action"]["action_type"] == "dividend"
    assert action_record.metadata["normalized_record"]["corporate_action"]["cash_dividend"] == 4


def test_tej_source_ingest_adapter_emits_research_market_records_without_raw_secret() -> None:
    adapter = TejSourceIngestAdapter(
        connector_id="conn-tej-test",
        secret_ref_id="env://TEJ_API_KEY",
        purchased_table_allowlist=("TWN/AMTOP1",),
    )
    connector = adapter.connector()
    fetch_config = adapter.fetch_config()
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
    assert connector.metadata["run_by_default"] is False
    assert fetch_config["secret_ref_id"] == "env://TEJ_API_KEY"
    assert fetch_config["allowlist_required"] is True
    assert records[0].content_ref.startswith("tej://TRAIL/TATINST1/2330/")
    assert records[0].metadata["source_class"] == "research_grade"
    assert records[0].metadata["dataset_code"] == "TRAIL/TATINST1"
    assert records[0].metadata["table_code"] == "TATINST1"
    assert records[0].metadata["license_scope"] == "vendor_research"
    assert records[0].metadata["point_in_time_available"] is True
    assert records[0].metadata["schema_hash"] == "tej_taiwan_research_dataset.v1"
    assert "TEJ_API_KEY" not in records[0].metadata["body"]


def test_tej_adapter_reports_credential_unavailable_health_without_key() -> None:
    adapter = TejSourceIngestAdapter(connector_id="conn-tej-test")

    health = adapter.credential_health(api_key_available=False, checked_at="2026-06-11T00:00:00Z")

    assert health.source_id == "conn-tej-test"
    assert health.source_kind == "data_source"
    assert health.status == "degraded"
    assert health.metadata["reason"] == "credential_unavailable"
    assert health.metadata["required_secret_ref_id"] == "env://TEJ_API_KEY"


def test_tej_backfill_planner_requires_allowlisted_paid_table_and_keeps_secret_ref_only() -> None:
    adapter = TejSourceIngestAdapter(
        connector_id="conn-tej-test",
        purchased_table_allowlist=("AMTOP1",),
    )

    ready_plan = adapter.plan_historical_backfill(
        dataset_code="TWN/AMTOP1",
        start_date="2020-01-01",
        end_date="2020-01-31",
        symbol_universe=["2330", "2317", "2330"],
        entitlement_metadata={"quote_id": "tej-quote-2026-06", "purchased_table_allowlist": ["AMTOP1"]},
    )
    blocked_plan = adapter.plan_historical_backfill(
        dataset_code="TWN/ABSR20",
        start_date="2020-01-01",
        end_date="2020-01-31",
        symbol_universe=["2330"],
        entitlement_metadata={"quote_id": "tej-quote-2026-06"},
    )

    assert ready_plan["schema_version"] == "tej_taiwan_backfill_plan.v1"
    assert ready_plan["dataset_code"] == "TWN/AMTOP1"
    assert ready_plan["table_code"] == "AMTOP1"
    assert ready_plan["symbol_universe"] == ["2330", "2317"]
    assert ready_plan["plan_state"] == "ready"
    assert ready_plan["run_by_default"] is False
    assert ready_plan["secret_ref_id"] == "env://TEJ_API_KEY"
    assert ready_plan["jobs"][0]["license_scope"] == "vendor_research"
    assert ready_plan["jobs"][0]["point_in_time_available"] is True
    assert blocked_plan["plan_state"] == "requires_entitlement_confirmation"
