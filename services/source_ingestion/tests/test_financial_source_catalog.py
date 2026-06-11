from __future__ import annotations

import json

from services.source_ingestion.financial_source_catalog import (
    financial_data_source_catalog_payload,
    initial_financial_data_source_config_templates,
    initial_financial_data_source_entries,
)


def test_initial_financial_catalog_covers_required_sources_as_data_sources() -> None:
    payload = financial_data_source_catalog_payload()
    providers = set(payload["summary"]["providers"])
    entries = {entry.data_source_id: entry for entry in initial_financial_data_source_entries()}

    assert payload["schema_version"] == "financial_data_source_catalog.v1"
    assert {
        "Anue Cnyes",
        "FINRA",
        "FRED",
        "FinMind",
        "MOPS",
        "SEC EDGAR",
        "Stooq",
        "TEJ",
        "TWSE/TPEx",
        "Yahoo Taiwan Stock",
    } <= providers
    assert payload["summary"]["data_source_count"] == 10
    assert all(entry.source_kind == "data_source" for entry in entries.values())
    assert all(entry.lifecycle_state.value == "candidate" for entry in entries.values())
    assert entries["ds-finmind-tw-data"].metadata["template_status"] == "candidate_not_live_ingestion_claim"
    assert entries["ds-tej-tw-research-backfill"].source_class.value == "vendor_backfill"
    assert entries["ds-tej-tw-research-backfill"].metadata["purchased_table_allowlist_required"] is True
    assert entries["ds-yahoo-tw-news-broker"].metadata["not_official_reference_truth"] is True
    assert entries["ds-anue-tw-news"].metadata["not_official_reference_truth"] is True
    assert entries["ds-anue-tw-news"].metadata["template_status"] == "candidate_not_live_ingestion_claim"


def test_catalog_config_templates_are_secret_ref_only_and_cover_active_universe_sources() -> None:
    templates = {template["template_id"]: template for template in initial_financial_data_source_config_templates()}
    encoded = json.dumps(templates, sort_keys=True)

    assert "template-tw-finmind-datasets" in templates
    assert "template-tw-tej-research-backfill" in templates
    assert "template-tw-twse-tpex-official-market" in templates
    assert "template-tw-mops-official-disclosures" in templates
    assert "template-tw-yahoo-broker-top15" in templates
    assert "template-tw-anue-news-rss" in templates
    assert "template-us-sec-edgar-filings" in templates
    assert "template-us-fred-macro" in templates
    assert "template-us-finra-short-sale" in templates
    assert "template-us-stooq-daily-ohlcv" in templates
    assert "raw-key" not in encoded
    assert templates["template-tw-finmind-broker-daily-report"]["fetch"]["normalized_target"] == "tw_broker_top"
    tej_template = templates["template-tw-tej-research-backfill"]
    assert tej_template["auth"]["secret_ref_id"] == "env://TEJ_API_KEY"
    assert tej_template["fetch"]["allowlist_required"] is True
    assert tej_template["fetch"]["credential_smoke"]["without_key_status"] == "credential_unavailable"
    assert {"TWN/APRCD1", "TWN/AMTOP1", "TWN/ABSR20"} <= {
        table["dataset_code"] for table in tej_template["fetch"]["candidate_tables"]
    }
    assert templates["template-tw-yahoo-stock-rss"]["fetch"]["full_text_allowed_by_default"] is False
    assert templates["template-tw-yahoo-stock-rss"]["fetch"]["adapter"] == "YahooTaiwanRssAdapter.records_from_rss"
    assert templates["template-tw-yahoo-broker-top15"]["fetch"]["adapter"] == (
        "YahooTaiwanBrokerTopAdapter.records_from_html"
    )
    assert templates["template-tw-anue-news-rss"]["fetch"]["adapter"] == "AnueTaiwanRssAdapter.records_from_rss"
    assert templates["template-tw-anue-news-rss"]["fetch"]["full_text_allowed_by_default"] is False
    assert templates["template-tw-anue-news-rss"]["fetch"]["feed_url_ref"] == "operator_configured_rss_feed_url"
    assert templates["template-us-sec-edgar-filings"]["fetch"]["adapter"] == (
        "SecEdgarFilingAdapter.records_from_payload"
    )
    assert templates["template-us-fred-macro"]["fetch"]["adapter"] == (
        "FredMacroSeriesAdapter.records_from_observations_payload"
    )
    assert templates["template-us-fred-macro"]["fetch"]["symbol_scope"] == "global_no_symbol_filter"
    assert templates["template-us-finra-short-sale"]["fetch"]["expected_publication_delay_hours"] == 26
    assert templates["template-us-stooq-daily-ohlcv"]["lifecycle_state"] == "disabled"
    assert templates["template-us-stooq-daily-ohlcv"]["fetch"]["disabled_reason"] == (
        "stooq_endpoint_unverified_2026-06-11"
    )
    mops_template = templates["template-tw-mops-official-disclosures"]
    assert mops_template["fetch"]["normalized_targets"] == [
        "tw_material_event",
        "tw_monthly_revenue",
        "tw_financial_statement",
        "tw_company_master",
        "tw_corporate_action",
    ]
    assert mops_template["schedule"]["event_universe_tiers"] == [
        "core_universe",
        "candidate_universe",
        "archive_universe",
    ]
    assert mops_template["schedule"]["fundamental_universe_tiers"] == ["core_universe"]
    assert mops_template["fetch"]["restatement_correction_gap_report"] == "mops_restatement_correction_gap_report.v1"


def test_catalog_embeds_active_universe_scheduling_policy() -> None:
    payload = financial_data_source_catalog_payload()
    policy = payload["active_universe_policy"]

    assert policy["schema_version"] == "active_universe_scheduling_policy.v1"
    assert policy["summary"]["archive_baseline_rule_count"] >= 3
    assert "tw-twse-tpex-official-market" in policy["summary"]["archive_baseline_connector_ids"]
    assert "tw-finmind-broker-daily-report" in policy["summary"]["candidate_detail_connector_ids"]
    assert "tw-anue-news-rss" in policy["summary"]["candidate_detail_connector_ids"]
    assert "tw-tej-research-datasets" in policy["summary"]["candidate_detail_connector_ids"]
    assert payload["summary"]["active_universe_rule_count"] == policy["summary"]["rule_count"]
