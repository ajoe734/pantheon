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
    assert {"FinMind", "TWSE/TPEx", "MOPS", "Yahoo Taiwan Stock", "SEC EDGAR", "FRED"} <= providers
    assert payload["summary"]["data_source_count"] == 6
    assert all(entry.source_kind == "data_source" for entry in entries.values())
    assert all(entry.lifecycle_state.value == "candidate" for entry in entries.values())
    assert entries["ds-finmind-tw-data"].metadata["template_status"] == "candidate_not_live_ingestion_claim"
    assert entries["ds-yahoo-tw-news-broker"].metadata["not_official_reference_truth"] is True


def test_catalog_config_templates_are_secret_ref_only_and_cover_active_universe_sources() -> None:
    templates = {template["template_id"]: template for template in initial_financial_data_source_config_templates()}
    encoded = json.dumps(templates, sort_keys=True)

    assert "template-tw-finmind-datasets" in templates
    assert "template-tw-twse-tpex-official-market" in templates
    assert "template-tw-mops-official-disclosures" in templates
    assert "template-tw-yahoo-broker-top15" in templates
    assert "template-us-sec-edgar-filings" in templates
    assert "template-us-fred-macro" in templates
    assert "raw-key" not in encoded
    assert templates["template-tw-finmind-broker-daily-report"]["fetch"]["normalized_target"] == "tw_broker_top"
    assert templates["template-tw-yahoo-stock-rss"]["fetch"]["full_text_allowed_by_default"] is False
    assert templates["template-us-fred-macro"]["fetch"]["symbol_scope"] == "global_no_symbol_filter"


def test_catalog_embeds_active_universe_scheduling_policy() -> None:
    payload = financial_data_source_catalog_payload()
    policy = payload["active_universe_policy"]

    assert policy["schema_version"] == "active_universe_scheduling_policy.v1"
    assert policy["summary"]["archive_baseline_rule_count"] >= 3
    assert "tw-twse-tpex-official-market" in policy["summary"]["archive_baseline_connector_ids"]
    assert "tw-finmind-broker-daily-report" in policy["summary"]["candidate_detail_connector_ids"]
    assert payload["summary"]["active_universe_rule_count"] == policy["summary"]["rule_count"]
