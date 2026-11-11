"""Governed point-in-time feature datasets (task GOVERNED-FEATURE-DATASETS-20261110)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.registry import factor_rule
from services.source_ingestion import feature_catalogue as fc
from services.source_ingestion.connectors.base import SourceRecord
from services.source_ingestion.connectors.collector_bundle import (
    community_daily_feature_rows,
    macro_feature_rows,
)
from services.source_ingestion.connectors.taiwan_official import (
    TAIWAN_OFFICIAL_FUNDAMENTAL_ENDPOINTS,
    fetch_fundamental_rows,
    fundamental_rows_to_records,
    normalize_financial_statement_rows,
    normalize_monthly_revenue_rows,
    normalize_valuation_rows,
)
from services.source_ingestion.market_data_storage import (
    MarketDataStorageWriter,
    measure_stored_history_depth,
)

REPO = Path(__file__).resolve().parents[3]
INGEST = "2026-11-11T00:00:00Z"
KW = {"source_dataset": "x", "api_endpoint": "https://openapi.twse.com.tw/v1/x", "ingest_time": INGEST}


def test_catalogue_document_matches_schema_and_lists_every_field_with_lag() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((REPO / "docs/contracts/feature_catalogue.v1.schema.json").read_text())
    doc = fc.catalogue_document()
    jsonschema.validate(doc, schema)
    listed = {(d["dataset"], f["field"]): f["availability_lag_days"] for d in doc["datasets"] for f in d["fields"]}
    assert listed == {(f.dataset, f.field): fc.lag_days_for(f.dataset, f.field) for f in fc.catalogue_fields()}
    assert listed[("tw_monthly_revenue", "yoy")] == 10
    assert listed[("tw_macro_ndc", "monitoring_score")] == 28


def test_factor_rule_catalogue_lags_match_source_ingestion_catalogue() -> None:
    for f in fc.catalogue_fields():
        definition = factor_rule.get_feature_definition(f.dataset, f.field)
        assert definition is not None, f"{f.dataset}.{f.field} missing from factor_rule catalogue"
        assert definition.min_availability_lag_days == fc.lag_days_for(f.dataset, f.field)


def test_monthly_revenue_row_has_event_available_and_ingest_time() -> None:
    payload = [{
        "出表日期": "1151105", "資料年月": "11510", "公司代號": "2330", "公司名稱": "台積電",
        "營業收入-當月營收": "300,000", "營業收入-上月營收": "280,000",
        "營業收入-上月比較增減(%)": "7.14", "營業收入-去年同月增減(%)": "25.5",
    }]
    (row,) = normalize_monthly_revenue_rows("TWSE", payload, **KW)
    assert row["event_time"] == "2026-10-31"
    assert row["availability_lag_days"] == 10
    assert row["available_time"] == "2026-11-10T10:00:00Z"  # 18:00 Asia/Taipei
    assert row["ingest_time"] == INGEST
    assert (row["revenue"], row["yoy"], row["mom"]) == (300000.0, 25.5, 7.14)
    obs = {o["field"]: o for o in fc.observations_from_row("tw_monthly_revenue", row)}
    assert set(obs) == {"yoy", "mom", "revenue"}


def test_quarterly_statements_join_equity_and_q4_lag() -> None:
    income = [
        {"年度": "115", "季別": "3", "公司代號": "2330", "營業收入": "1000", "營業毛利（毛損）淨額": "500",
         "營業利益（損失）": "400", "歸屬於母公司業主之淨利（損）": "300", "基本每股盈餘（元）": "12.5"},
        {"年度": "114", "季別": "4", "公司代號": "2330", "營業收入": "1000", "營業利益（損失）": "100"},
    ]
    balance = [{"年度": "115", "季別": "3", "公司代號": "2330", "歸屬於母公司業主之權益合計": "3000"}]
    rows = {r["fiscal_quarter"]: r for r in normalize_financial_statement_rows("TWSE", income, balance, **KW)}
    q3, q4 = rows[3], rows[4]
    assert q3["event_time"] == "2026-09-30" and q3["availability_lag_days"] == 45
    assert (q3["gross_margin"], q3["operating_margin"], q3["roe"], q3["eps"]) == (0.5, 0.4, 0.1, 12.5)
    assert q4["event_time"] == "2025-12-31" and q4["availability_lag_days"] == 90
    assert q4["roe"] is None and q4["eps"] is None
    obs = fc.observations_from_row("tw_quarterly_statements", q4)
    assert {o["field"] for o in obs} == {"operating_margin"}
    assert obs[0]["available_time"] == "2026-03-31T10:00:00Z"


def test_valuation_rows_for_twse_and_tpex() -> None:
    twse = normalize_valuation_rows("TWSE", [{"Date": "1141105", "Code": "2330", "PEratio": "20.1", "DividendYield": "1.5", "PBratio": "5.2"}], **KW)
    tpex = normalize_valuation_rows("TPEx", [{"Date": "1141105", "SecuritiesCompanyCode": "6488", "PriceEarningRatio": "15", "YieldRatio": "2", "PriceBookRatio": "3"}], **KW)
    assert twse[0]["event_time"] == "2025-11-05" and twse[0]["pe_ratio"] == 20.1 and twse[0]["pb_ratio"] == 5.2
    assert tpex[0]["symbol_canonical"].startswith("6488") and tpex[0]["dividend_yield"] == 2.0
    assert fc.observations_from_row("tw_valuation_bwibbu", twse[0])[0]["availability_lag_days"] == 0


def test_fetch_uses_openapi_only_and_rejects_website_endpoints() -> None:
    seen: list[str] = []

    def fake(url: str):
        seen.append(url)
        return []

    for dataset, venues in TAIWAN_OFFICIAL_FUNDAMENTAL_ENDPOINTS.items():
        for venue in venues:
            assert fetch_fundamental_rows(dataset, venue, fetch=fake) == ()
    assert seen and all("openapi" in u or "/openapi/" in u for u in seen)
    with pytest.raises(ValueError):
        fetch_fundamental_rows("tw_monthly_revenue", "TWSE-unknown", fetch=fake)


def _macro_record(series: str, value, event: str, available: str, **extra) -> SourceRecord:
    meta = {"series_id": series, "value": value, "event_time": event, "available_time": available,
            "ingest_time": "2026-11-11T00:00:00Z", "unit": "percent", **extra}
    return SourceRecord(source_id=f"m:{series}:{event}", connector_id="c", source_type="macro",
                        title=series, content_ref="https://example.test/x", metadata=meta, trace_id="t")


def test_macro_series_rows_and_rejections() -> None:
    records = [
        _macro_record("cbc_policy_rate", 2.0, "2026-09-17T00:00:00Z", "2026-09-17T08:00:00Z"),
        _macro_record("cbc_unknown", 1.0, "2026-09-17T00:00:00Z", "2026-09-17T08:00:00Z"),
        _macro_record("ndc_monitoring_score", "n/a", "2026-09-30T00:00:00Z", "2026-10-29T08:00:00Z"),
        _macro_record("dgbas_cpi_yoy", 1.7, "2026-10-31T00:00:00Z", "2026-10-30T08:00:00Z"),
    ]
    rows, rejected = macro_feature_rows(records)
    assert len(rows) == 1 and len(rejected) == 3
    obs = fc.observations_from_row("tw_macro_cbc", rows[0])
    assert [(o["field"], o["entity"], o["availability_lag_days"]) for o in obs] == [("policy_rate", "cbc_policy_rate", 1)]
    assert fc.observations_from_row("tw_macro_ndc", rows[0]) == []


def _post(i: int, day: str, symbols, stance: str | None = None) -> SourceRecord:
    meta = {"symbols": symbols, "event_time": f"{day}T02:00:00Z", "available_time": f"{day}T03:00:00Z",
            "metrics": {"sentiment": stance} if stance else {}}
    return SourceRecord(source_id=f"p{i}", connector_id="c", source_type="social", title="t",
                        content_ref="https://example.test/p", metadata=meta, trace_id="t")


def test_community_daily_features_are_available_after_day_close() -> None:
    prior = {"2330": {f"2026-10-{d:02d}": 2 + (d % 2) for d in range(20, 31)}}
    posts = [_post(i, "2026-11-10", ["2330"], "bullish" if i < 3 else "bearish") for i in range(6)]
    posts.append(_post(99, "2026-11-10", ["2317"]))
    rows = {r["symbol"]: r for r in community_daily_feature_rows(posts, prior_daily_counts=prior)}
    r = rows["2330"]
    assert r["post_volume"] == 6.0 and r["bull_bear_ratio"] == 1.0
    assert r["attention_zscore"] > 3
    assert r["available_time"] == "2026-11-10T16:00:00Z"  # next Taipei midnight, after the day closed
    assert "attention_zscore" not in rows["2317"] and "bull_bear_ratio" not in rows["2317"]
    obs = fc.observations_from_row("tw_community_attention", r)
    assert {o["field"] for o in obs} == {"post_volume", "attention_zscore", "bull_bear_ratio"}


def test_read_feature_series_applies_lag_and_rejects_uncatalogued() -> None:
    row = normalize_monthly_revenue_rows(
        "TWSE", [{"資料年月": "11510", "公司代號": "2330", "營業收入-當月營收": "1", "營業收入-去年同月增減(%)": "9"}], **KW
    )[0]
    observations = fc.observations_from_row("tw_monthly_revenue", row)
    assert fc.read_feature_series(observations, "tw_monthly_revenue", "yoy", decision_time="2026-11-09") == ()
    visible = fc.read_feature_series(observations, "tw_monthly_revenue", "yoy", decision_time="2026-11-10")
    assert [o["value"] for o in visible] == [9.0]
    with pytest.raises(fc.FeatureCatalogueError):
        fc.read_feature_series(observations, "tw_made_up", "x", decision_time="2026-11-10")
    inputs = [{"name": "rev_yoy", "dataset": "tw_monthly_revenue", "field": "yoy"}]
    feats = fc.feature_inputs_for_factor_rule(observations, inputs, decision_time="2026-11-10")
    logic = {"kind": "factor_rule_v1", "inputs": [{**inputs[0], "availability_lag_days": 10}],
             "signal": {"ref": "rev_yoy"}}
    env = factor_rule.resolve_point_in_time_inputs(logic, [100.0], features=feats, decision_time="2026-11-10")
    assert env["rev_yoy"] == (9.0,)


def test_history_depth_reports_per_dataset_and_per_daily_bar_symbol(tmp_path: Path) -> None:
    from services.source_ingestion.connectors.base import SourceConnector, SourceType

    connector = SourceConnector(connector_id="tw-test", source_type=SourceType.MARKET, provider="p", license_scope="official_reference")
    writer = MarketDataStorageWriter(tmp_path)

    def write(rows, run: str) -> None:
        recs = fundamental_rows_to_records(rows, connector_id="tw-test")
        result = SimpleNamespace(records=list(recs), run=SimpleNamespace(ingest_run_id=run))
        writer.write_run(result=result, connector=connector)

    rev = []
    for ym in ("11508", "11509", "11510"):
        rev += normalize_monthly_revenue_rows("TWSE", [{"資料年月": ym, "公司代號": "2330", "營業收入-當月營收": "1"}], **KW)
    write(rev, "run-rev")
    bars = tmp_path / "normalized/tw_price_daily"
    for sym, dates in (("2330.TWSE", ("2026-11-03", "2026-11-04")), ("1101.TWSE", ("2026-11-04",))):
        for d in dates:
            p = bars / f"date={d}"
            p.mkdir(parents=True, exist_ok=True)
            with (p / f"{sym}.jsonl").open("a") as h:
                h.write(json.dumps({"dataset": "tw_price_daily", "metadata": {"symbol_canonical": sym, "date": d}}) + "\n")
    report = measure_stored_history_depth(tmp_path)
    assert report["daily_bar_symbol_count"] == 2
    revenue = report["datasets"]["tw_monthly_revenue"]
    assert revenue["distinct_event_dates"] == 3 and revenue["first_event_date"] == "2026-08-31"
    per = revenue["per_daily_bar_symbol"]
    assert per["2330.TWSE"]["distinct_event_dates"] == 3
    assert per["1101.TWSE"]["distinct_event_dates"] == 0
    assert revenue["daily_bar_symbols_without_history"] == 1
    assert set(per) == {"1101.TWSE", "2330.TWSE"}
    assert report["datasets"]["tw_price_daily"]["per_entity"]["2330.TWSE"]["distinct_event_dates"] == 2


def test_release_calendar_file_is_valid_and_cites_sources() -> None:
    doc = json.loads((REPO / "services/source_ingestion/calendars/tw_macro_release_calendar_2026.json").read_text())
    fc.validate_release_calendar(doc)
    assert {"cbc", "ndc", "dgbas"} <= set(doc["sources"])
    bad = json.loads(json.dumps(doc))
    bad["entries"][0]["feature"] = "tw_macro_cbc.nope"
    with pytest.raises(fc.FeatureCatalogueError):
        fc.validate_release_calendar(bad)


def test_factor_rule_rejects_uncatalogued_inputs_for_paper_producer() -> None:
    ok = {"kind": "factor_rule_v1", "inputs": [{"name": "a", "dataset": "tw_monthly_revenue", "field": "yoy", "availability_lag_days": 10}]}
    factor_rule.assert_governed_feature_inputs(ok)
    bad = {"kind": "factor_rule_v1", "inputs": [{"name": "a", "dataset": "tw_secret", "field": "x", "availability_lag_days": 0}]}
    with pytest.raises(factor_rule.FactorRuleValidationError):
        factor_rule.assert_governed_feature_inputs(bad)


def test_history_depth_reports_zero_for_missing_governed_datasets(tmp_path: Path) -> None:
    d = tmp_path / "normalized/tw_price_daily/date=2026-11-04"
    d.mkdir(parents=True)
    (d / "r.jsonl").write_text(
        json.dumps({"dataset": "tw_price_daily", "metadata": {"symbol_canonical": "2330.TWSE", "date": "2026-11-04"}}) + "\n"
    )
    report = measure_stored_history_depth(tmp_path)
    for spec in fc.dataset_specs():
        entry = report["datasets"][spec.normalized_dataset]
        if spec.normalized_dataset == "tw_price_daily":
            continue
        assert entry["row_count"] == 0 and entry["distinct_event_dates"] == 0
    zero = report["datasets"]["tw_monthly_revenue"]["per_daily_bar_symbol"]["2330.TWSE"]
    assert zero["distinct_event_dates"] == 0


def test_adapter_ingests_governed_fundamentals_end_to_end(tmp_path: Path) -> None:
    from services.source_ingestion.connectors.taiwan_official import TaiwanOfficialMarketDatasetAdapter

    adapter = TaiwanOfficialMarketDatasetAdapter()
    rev = [{"資料年月": "11509", "公司代號": "2330", "營業收入-當月營收": "100", "營業收入-去年同月增減(%)": "12.5"}]
    inc = [{"年度": "115", "季別": "2", "公司代號": "2330", "營業收入": "100", "基本每股盈餘（元）": "5"}]
    val = [{"Code": "2330", "Date": "1151110", "PEratio": "20", "PBratio": "5", "DividendYield": "2"}]
    payloads = {"tw_monthly_revenue": rev, "tw_financial_statement": {"income": inc}, "tw_valuation_bwibbu": val}
    for dataset, payload in payloads.items():
        records = adapter.records_from_payload(dataset, "TWSE", payload)
        assert records, dataset
        meta = records[0].metadata
        assert meta["dataset"] == dataset and meta["ingest_time"] and "availability_lag_days" in meta
        assert fc.observations_from_row(dataset_to_catalogue[dataset], meta["normalized_row"])

    fetched = adapter.fundamental_records("tw_monthly_revenue", "TWSE", fetch=lambda url: rev, ingest_time=INGEST)
    assert fetched and fetched[0].metadata["dataset"] == "tw_monthly_revenue"


dataset_to_catalogue = {
    "tw_monthly_revenue": "tw_monthly_revenue",
    "tw_financial_statement": "tw_quarterly_statements",
    "tw_valuation_bwibbu": "tw_valuation_bwibbu",
}


def test_collector_adapter_derives_storable_macro_and_community_records() -> None:
    from services.source_ingestion.connectors.collector_bundle import CollectorBundleAdapter

    macro = CollectorBundleAdapter(connector_id="m", source_id="m", source_type="macro", dataset="tw_macro_series")
    out, rejected = macro.governed_feature_records(
        [_macro_record("cbc_policy_rate", 2.0, "2026-09-17T00:00:00Z", "2026-09-17T08:00:00Z")]
    )
    assert len(out) == 1 and not rejected
    assert out[0].metadata["dataset"] == "tw_macro_series" and out[0].metadata["normalized_row"]["series_id"] == "cbc_policy_rate"
    social = CollectorBundleAdapter(connector_id="s", source_id="s", source_type="social", dataset="tw_community_daily")
    posts, _ = social.governed_feature_records([_post(1, "2026-11-10", ["2330"], "bullish")])
    assert posts and posts[0].metadata["dataset"] == "tw_community_daily"


def test_provider_entrypoint_fetches_and_joins_income_and_balance_statements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The configured provider path (no prewrapped payload) must fetch both endpoints."""
    from services.source_ingestion.connectors import taiwan_official as tw
    from services.source_ingestion.connectors.base import SourceConnector, SourceType
    from services.source_ingestion.provider_adapters import execute_provider_owned_adapter

    spec = TAIWAN_OFFICIAL_FUNDAMENTAL_ENDPOINTS["tw_financial_statement"]["TWSE"]
    income = [{"年度": "115", "季別": "3", "公司代號": "2330", "營業收入": "1000",
               "營業利益（損失）": "400", "歸屬於母公司業主之淨利（損）": "300", "基本每股盈餘（元）": "12.5"}]
    balance = [{"年度": "115", "季別": "3", "公司代號": "2330", "歸屬於母公司業主之權益合計": "3000"}]
    seen: list[str] = []

    def fake_fetch(request, **_kwargs):
        seen.append(request.full_url)
        return {spec["endpoint"]: income, spec["balance_endpoint"]: balance}[request.full_url]

    monkeypatch.setattr(tw, "_fetch_official", fake_fetch)
    connector = SourceConnector(
        connector_id="tw-official", source_type=SourceType.MARKET, provider="twse", license_scope="official_reference"
    )
    records = execute_provider_owned_adapter(
        connector=connector,
        fetch={
            "adapter": "TaiwanOfficialMarketDatasetAdapter.records_from_payload",
            "request": {"dataset": "tw_financial_statement", "venues": ["TWSE"]},
            "max_records": 50,
        },
        trace_id="t",
    )
    assert seen == [spec["endpoint"], spec["balance_endpoint"]]
    assert records and records[0].metadata["dataset"] == "tw_financial_statement"
    row = records[0].metadata["normalized_row"]
    assert row["eps"] == 12.5 and row["operating_margin"] == 0.4 and row["roe"] is not None
    assert "available_time" in row and "ingest_time" in row


@pytest.mark.parametrize(
    "dataset", ["tw_monthly_revenue", "tw_financial_statement", "tw_valuation_bwibbu"]
)
def test_governed_fundamentals_are_creatable_through_management_and_stored(
    tmp_path: Path, dataset: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Management create -> provider fetch -> storage for each new TWSE/TPEx fundamental."""
    from services.source_ingestion.connector_definitions import get_connector_definition
    from services.source_ingestion.connectors.base import SourceConnector, SourceType
    from services.source_ingestion.connectors.taiwan_official import TAIWAN_OFFICIAL_FUNDAMENTAL_ENDPOINTS
    from services.source_ingestion.connectors import taiwan_official as tw
    from services.source_ingestion.financial_source_catalog import financial_data_source_catalog_payload
    from services.source_ingestion.provider_adapters import execute_provider_owned_adapter
    from services.source_ingestion.source_management_commands import SourceCommandEngine
    from services.source_ingestion.source_management_models import CommandType, ReceiptStatus, SourceManagementCommand
    from services.source_ingestion.source_management_store import JsonlSourceManagementStore
    from services.source_ingestion.configured import JsonlConfiguredConnectorStore, JsonlConnectorScheduleStore

    definition_id = "tw-twse-tpex-official-market"
    assert dataset in get_connector_definition(definition_id).datasets
    engine = SourceCommandEngine(
        store=JsonlSourceManagementStore(tmp_path),
        connector_store=JsonlConfiguredConnectorStore(tmp_path / "connector_config.jsonl"),
        schedule_config_store=JsonlConnectorScheduleStore(tmp_path / "connector_schedule.jsonl"),
        deployment_sha="test-sha",
    )
    receipt = engine.execute_command(
        SourceManagementCommand(
            command_id=f"cmd-{dataset}",
            idempotency_key=f"idem-{dataset}",
            command_type=CommandType.CREATE,
            source_instance_id=f"ds-{dataset}",
            expected_revision=None,
            actor={"actor_type": "operator", "actor_id": "operator-alice", "roles": ["operator"]},
            reason="register governed fundamentals",
            parameters={
                "definition_id": definition_id,
                "connector_id": f"conn-{dataset}",
                "datasets": [{"dataset_id": dataset, "dataset_class": "financial_fundamental"}],
                "source_class": "financial_fundamental",
                "connector_config": {"public": {"dataset": dataset}},
            },
        )
    )
    assert receipt.status == ReceiptStatus.SUCCEEDED

    catalog_ids = {
        d["dataset_id"]
        for entry in financial_data_source_catalog_payload()["entries"]
        if entry["connector_id"] == definition_id
        for d in entry["datasets"]
    }
    assert dataset in catalog_ids

    payloads = {
        "tw_monthly_revenue": [{"資料年月": "11509", "公司代號": "2330", "營業收入-當月營收": "100",
                                "營業收入-去年同月增減(%)": "12.5"}],
        "tw_financial_statement": {"income": [{"年度": "115", "季別": "2", "公司代號": "2330",
                                                "營業收入": "100", "基本每股盈餘（元）": "5"}]},
        "tw_valuation_bwibbu": [{"Code": "2330", "Date": "1151110", "PEratio": "20", "PBratio": "5",
                                  "DividendYield": "2"}],
    }
    connector = SourceConnector(
        connector_id=f"conn-{dataset}", source_type=SourceType.MARKET, provider="TWSE/TPEx",
        license_scope="official_reference", metadata={"normalized_target": dataset},
    )
    records = execute_provider_owned_adapter(
        connector=connector,
        fetch={
            "adapter": "TaiwanOfficialMarketDatasetAdapter.records_from_payload",
            "request": {"dataset": dataset, "venues": ["TWSE"], "payload": payloads[dataset]},
            "max_records": 50,
        },
        trace_id="t",
    )
    assert records and all(r.metadata["dataset"] == dataset for r in records)
    manifest = MarketDataStorageWriter(tmp_path / "store").write_run(
        result=SimpleNamespace(records=list(records), run=SimpleNamespace(ingest_run_id="run-1")),
        connector=connector,
    )
    assert {r["dataset"] for r in manifest.to_dict()["normalized_refs"]} == {dataset}
    depth = measure_stored_history_depth(tmp_path / "store")
    assert depth["datasets"][dataset]["row_count"] == len(records)
