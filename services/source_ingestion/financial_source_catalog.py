"""Initial financial data-source catalog and connector config templates.

The catalog is a product-level onboarding template, not a live-ingestion claim.
Entries use the DataSourceRegistry contract from DATASTRAT-REG-002 while the
connector templates describe the bounded connector/config shape operators can
admit later through the normal source-ingest lifecycle.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .active_universe import DEFAULT_SOURCE_UPDATE_RULES, active_universe_policy_payload
from .registry.data_source_registry import DataSourceClass, DataSourceEntry, DataSourceLifecycleState


FINANCIAL_DATA_SOURCE_CATALOG_SCHEMA_VERSION = "financial_data_source_catalog.v1"
FINANCIAL_DATA_SOURCE_CONFIG_TEMPLATE_SCHEMA_VERSION = "financial_data_source_config_template.v1"
CATALOG_UPDATED_AT = "2026-06-11T00:00:00Z"
ACTIVE_UNIVERSE_POLICY_REF = "active_universe_scheduling_policy.v1"


TEJ_PAID_BACKFILL_TABLE_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "dataset_code": "TWN/APRCD1",
        "table_code": "APRCD1",
        "dataset_role": "tquant_historical_daily_price",
        "normalized_target": "tw_price_daily",
        "license_scope": "vendor_research",
        "run_by_default": False,
    },
    {
        "dataset_code": "TWN/AMTOP1",
        "table_code": "AMTOP1",
        "dataset_role": "broker_major_participant_gap_fill",
        "normalized_target": "tw_broker_top",
        "license_scope": "vendor_research",
        "run_by_default": False,
    },
    {
        "dataset_code": "TWN/ABSR20",
        "table_code": "ABSR20",
        "dataset_role": "broker_top20_branch_gap_fill",
        "normalized_target": "tw_broker_top",
        "license_scope": "vendor_research",
        "run_by_default": False,
    },
)


def _dataset(
    dataset_id: str,
    dataset_class: str,
    *,
    storage_tier: str,
    update_profile: str,
    storage_targets: Sequence[str],
    universe_tiers: Sequence[str] = ("core_universe", "candidate_universe"),
    notes: str = "",
    backup_source: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "dataset_id": dataset_id,
        "dataset_class": dataset_class,
        "storage_tier": storage_tier,
        "update_profile": update_profile,
        "storage_targets": list(storage_targets),
        "universe_tiers": list(universe_tiers),
    }
    if notes:
        payload["notes"] = notes
    if backup_source:
        payload["backup_source"] = backup_source
    if metadata:
        payload["metadata"] = dict(metadata)
    return payload


def _entry(
    *,
    data_source_id: str,
    provider: str,
    source_class: DataSourceClass,
    datasets: Sequence[Mapping[str, Any]],
    license_scope: str,
    allowed_use: Sequence[str],
    update_frequency: str,
    entitlement_tags: Sequence[str] = (),
    connector_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> DataSourceEntry:
    return DataSourceEntry(
        data_source_id=data_source_id,
        provider=provider,
        source_class=source_class.value,
        datasets=datasets,
        license_scope=license_scope,
        allowed_use=allowed_use,
        update_frequency=update_frequency,
        lifecycle_state=DataSourceLifecycleState.CANDIDATE.value,
        entitlement_tags=entitlement_tags,
        universe_policy_ref=ACTIVE_UNIVERSE_POLICY_REF,
        connector_id=connector_id,
        created_at=CATALOG_UPDATED_AT,
        updated_at=CATALOG_UPDATED_AT,
        metadata={
            "catalog_role": "initial_financial_data_source_template",
            "template_status": "candidate_not_live_ingestion_claim",
            **dict(metadata or {}),
        },
    )


INITIAL_FINANCIAL_DATA_SOURCE_ENTRIES: tuple[DataSourceEntry, ...] = (
    _entry(
        data_source_id="ds-finmind-tw-data",
        provider="FinMind",
        source_class=DataSourceClass.MARKET_DAILY,
        license_scope="vendor_research",
        entitlement_tags=("finmind-sponsor",),
        allowed_use=("research_data", "backtest_data", "feature_generation", "monitoring"),
        update_frequency="daily_after_close plus weekly/monthly/quarterly provider cadence",
        connector_id="tw-finmind-datasets",
        datasets=(
            _dataset(
                "tw_price_daily",
                "market_daily",
                storage_tier="normalized/features",
                update_profile="daily_after_close",
                storage_targets=("normalized/tw_price_daily", "features/returns"),
                universe_tiers=("core_universe", "candidate_universe", "archive_universe"),
                backup_source="TWSE/TPEx official market data",
            ),
            _dataset(
                "tw_daily_price_and_chip",
                "taiwan_chip",
                storage_tier="normalized/features",
                update_profile="daily_after_close",
                storage_targets=(
                    "normalized/tw_day_trading",
                    "normalized/tw_institutional_flow",
                    "normalized/tw_margin_short_balance",
                    "normalized/tw_securities_lending",
                    "normalized/tw_shareholding",
                ),
                notes="FinMind normalized Taiwan chip datasets for core/candidate symbols.",
            ),
            _dataset(
                "tw_broker_top",
                "taiwan_chip",
                storage_tier="normalized/features",
                update_profile="daily_after_close",
                storage_targets=("normalized/tw_broker_top", "features/broker_top_concentration"),
                backup_source="Yahoo Taiwan broker top15 public-web fallback; TEJ AMTOP1/ABSR20 for paid backfill",
                metadata={"max_rank": 20, "source_dataset": "TaiwanStockTradingDailyReport"},
            ),
            _dataset(
                "tw_news_metadata",
                "news",
                storage_tier="raw/normalized",
                update_profile="10m_to_30m_metadata_only",
                storage_targets=("raw/finmind/TaiwanStockNews", "normalized/tw_news_event"),
                notes="Metadata only unless entitlement explicitly allows full text.",
            ),
        ),
        metadata={
            "preferred_role": "low_cost_normalized_taiwan_research_layer",
            "does_not_replace_official_reference_truth": True,
            "config_template_ids": [
                "template-tw-finmind-datasets",
                "template-tw-finmind-broker-daily-report",
                "template-tw-finmind-broker-bulk-parquet",
            ],
        },
    ),
    _entry(
        data_source_id="ds-twse-tpex-official-market",
        provider="TWSE/TPEx",
        source_class=DataSourceClass.MARKET_DAILY,
        license_scope="official_reference",
        allowed_use=("research_data", "backtest_data", "feature_generation", "monitoring"),
        update_frequency="daily_after_close",
        connector_id="tw-twse-tpex-official-market",
        datasets=(
            _dataset(
                "tw_price_daily",
                "market_daily",
                storage_tier="raw/normalized/features",
                update_profile="daily_after_close",
                storage_targets=("raw/twse_tpex", "normalized/tw_price_daily"),
                universe_tiers=("core_universe", "candidate_universe", "archive_universe"),
                notes="Official daily price baseline for all tracked Taiwan symbols.",
            ),
            _dataset(
                "tw_exchange_chip_summary",
                "taiwan_chip",
                storage_tier="normalized/features",
                update_profile="daily_after_close",
                storage_targets=(
                    "normalized/tw_institutional_flow",
                    "normalized/tw_margin_short_balance",
                    "normalized/tw_securities_lending",
                ),
                notes="Official chip summaries; detailed broker top N remains active/candidate only.",
            ),
        ),
        metadata={
            "preferred_role": "official_taiwan_market_reference",
            "official_reference_truth": True,
            "config_template_ids": ["template-tw-twse-tpex-official-market"],
        },
    ),
    _entry(
        data_source_id="ds-mops-official-filings",
        provider="MOPS",
        source_class=DataSourceClass.FILING_EVENT,
        license_scope="official_reference",
        allowed_use=("research_data", "backtest_data", "feature_generation", "monitoring"),
        update_frequency="event_poll_10m_to_30m plus daily/monthly/quarterly scans",
        connector_id="tw-mops-official-disclosures",
        datasets=(
            _dataset(
                "tw_material_events",
                "filing_event",
                storage_tier="raw/normalized",
                update_profile="event_poll_10m_to_30m",
                storage_targets=("raw/mops", "normalized/tw_material_event"),
                universe_tiers=("core_universe", "candidate_universe", "archive_universe"),
                notes="Archived Taiwan symbols continue material-event maintenance.",
            ),
            _dataset(
                "tw_monthly_revenue",
                "financial_fundamental",
                storage_tier="normalized/features",
                update_profile="daily_scan_monthly_event_facts",
                storage_targets=("normalized/tw_monthly_revenue", "features/revenue_surprise"),
                universe_tiers=("core_universe",),
                backup_source="FinMind or TEJ research-grade backfill",
            ),
            _dataset(
                "tw_financial_statement",
                "financial_fundamental",
                storage_tier="normalized/features",
                update_profile="daily_scan_quarterly_event_facts",
                storage_targets=("normalized/tw_financial_statement",),
                universe_tiers=("core_universe",),
                backup_source="FinMind or TEJ research-grade backfill",
            ),
            _dataset(
                "tw_company_master",
                "company_reference",
                storage_tier="normalized",
                update_profile="daily_reference_scan",
                storage_targets=("normalized/tw_company_master",),
                universe_tiers=("core_universe", "candidate_universe", "archive_universe"),
                notes="MOPS company identity rows are normalized where supported by company-master routes.",
            ),
            _dataset(
                "tw_corporate_action",
                "corporate_action",
                storage_tier="normalized/features",
                update_profile="daily_scan_event_facts",
                storage_targets=("normalized/tw_corporate_action",),
                universe_tiers=("core_universe", "candidate_universe"),
                backup_source="TEJ research-grade backfill",
            ),
        ),
        metadata={
            "preferred_role": "official_taiwan_disclosure_reference",
            "official_reference_truth": True,
            "normalized_targets": [
                "tw_material_event",
                "tw_monthly_revenue",
                "tw_financial_statement",
                "tw_company_master",
                "tw_corporate_action",
            ],
            "restatement_correction_gap_policy": "MOPS restatement/correction routes are inventoried as financial-statement gap scans.",
            "config_template_ids": ["template-tw-mops-official-disclosures"],
        },
    ),
    _entry(
        data_source_id="ds-tej-tw-research-backfill",
        provider="TEJ",
        source_class=DataSourceClass.VENDOR_BACKFILL,
        license_scope="vendor_research_paid",
        entitlement_tags=("tej-api", "tej-paid-backfill", "tquant-history-optional"),
        allowed_use=("research_data", "backtest_data", "feature_generation"),
        update_frequency="manual_one_time_historical_backfill plus credentialed smoke",
        connector_id="tw-tej-research-datasets",
        datasets=(
            _dataset(
                "tw_price_daily",
                "market_daily",
                storage_tier="raw/normalized/features",
                update_profile="manual_gap_fill_only",
                storage_targets=("raw/tej/TWN/APRCD1", "normalized/tw_price_daily", "features/returns"),
                universe_tiers=("core_universe", "candidate_universe", "archive_universe"),
                backup_source="TWSE/TPEx official market data and FinMind before TEJ paid backfill",
                metadata={
                    "dataset_code": "TWN/APRCD1",
                    "table_code": "APRCD1",
                    "source_plan": "TQuant historical price gaps after public/FinMind coverage",
                    "run_by_default": False,
                },
            ),
            _dataset(
                "tw_financial_fundamentals",
                "financial_fundamental",
                storage_tier="raw/normalized/features",
                update_profile="manual_gap_fill_only",
                storage_targets=("raw/tej/fundamentals", "normalized/tw_financial_statement"),
                universe_tiers=("core_universe",),
                backup_source="MOPS official disclosures and FinMind before TEJ paid backfill",
                metadata={
                    "purchased_table_allowlist_required": True,
                    "dataset_code_source": "operator_entitlement_metadata",
                    "run_by_default": False,
                },
            ),
            _dataset(
                "tw_broker_top",
                "taiwan_chip",
                storage_tier="raw/normalized/features",
                update_profile="manual_gap_fill_only",
                storage_targets=("raw/tej/TWN/AMTOP1", "raw/tej/TWN/ABSR20", "normalized/tw_broker_top"),
                universe_tiers=("core_universe", "candidate_universe"),
                backup_source="FinMind Sponsor report, Yahoo Taiwan latest fallback, then TEJ paid history",
                metadata={
                    "candidate_dataset_codes": ["TWN/AMTOP1", "TWN/ABSR20"],
                    "max_rank": 20,
                    "purchased_table_allowlist_required": True,
                    "run_by_default": False,
                },
            ),
        ),
        metadata={
            "preferred_role": "paid_taiwan_historical_gap_fill",
            "does_not_replace_official_disclosure_truth": True,
            "does_not_replace_mops_official_truth": True,
            "avoid_full_branch_monthly_subscription_by_default": True,
            "full_market_full_depth_forbidden_by_default": True,
            "credential_health_without_key": "credential_unavailable",
            "purchased_table_allowlist_required": True,
            "table_inventory_cache_required": True,
            "paid_backfill_table_candidates": [dict(table) for table in TEJ_PAID_BACKFILL_TABLE_CANDIDATES],
            "config_template_ids": ["template-tw-tej-research-backfill"],
        },
    ),
    _entry(
        data_source_id="ds-yahoo-tw-news-broker",
        provider="Yahoo Taiwan Stock",
        source_class=DataSourceClass.NEWS,
        license_scope="public_web_research",
        allowed_use=("research_data", "feature_generation", "monitoring"),
        update_frequency="10m_to_30m RSS plus daily_after_close broker page",
        connector_id="tw-yahoo-stock-rss",
        datasets=(
            _dataset(
                "tw_news_metadata",
                "news",
                storage_tier="raw/normalized",
                update_profile="10m_to_30m_metadata_only",
                storage_targets=("raw/yahoo_tw/rss", "normalized/tw_news_event"),
                notes="Public RSS metadata for active/candidate symbols; full text only if license allows.",
            ),
            _dataset(
                "tw_broker_top",
                "taiwan_chip",
                storage_tier="normalized/features",
                update_profile="daily_after_close_fallback",
                storage_targets=("normalized/tw_broker_top", "features/broker_top_concentration"),
                backup_source="FinMind Sponsor daily report primary; TEJ paid backfill",
                metadata={"max_rank": 15, "fallback_for_connector_id": "tw-finmind-broker-daily-report"},
            ),
        ),
        metadata={
            "preferred_role": "public_fallback_news_and_broker_summary",
            "config_template_ids": ["template-tw-yahoo-stock-rss", "template-tw-yahoo-broker-top15"],
            "not_official_reference_truth": True,
        },
    ),
    _entry(
        data_source_id="ds-sec-edgar-filings",
        provider="SEC EDGAR",
        source_class=DataSourceClass.FILING_EVENT,
        license_scope="public_official_reference",
        allowed_use=("research_data", "backtest_data", "feature_generation", "monitoring"),
        update_frequency="event_poll_daily",
        connector_id="us-sec-edgar-filings",
        datasets=(
            _dataset(
                "sec_filing_event",
                "filing_event",
                storage_tier="raw/normalized",
                update_profile="event_poll_daily",
                storage_targets=("raw/sec-edgar", "normalized/sec_filing_event", "features/filing_event_flags"),
                universe_tiers=("core_universe", "candidate_universe", "archive_universe"),
                notes="Official US filing baseline for tracked US symbols.",
            ),
        ),
        metadata={
            "preferred_role": "official_us_filing_reference",
            "official_reference_truth": True,
            "config_template_ids": ["template-us-sec-edgar-filings"],
        },
    ),
    _entry(
        data_source_id="ds-fred-macro",
        provider="FRED",
        source_class=DataSourceClass.MACRO,
        license_scope="public_macro_reference",
        allowed_use=("research_data", "backtest_data", "feature_generation", "monitoring"),
        update_frequency="daily_weekly_monthly_by_series_frequency",
        connector_id="us-fred-macro",
        datasets=(
            _dataset(
                "macro_fred_observation",
                "macro",
                storage_tier="normalized/features",
                update_profile="by_series_frequency",
                storage_targets=("normalized/macro_fred_observation", "features/macro_regime_features"),
                universe_tiers=("core_universe", "candidate_universe", "archive_universe"),
                notes="Global macro context is maintained outside per-symbol fanout.",
                metadata={"symbol_scope": "global_no_symbol_filter"},
            ),
        ),
        metadata={
            "preferred_role": "public_macro_context",
            "config_template_ids": ["template-us-fred-macro"],
        },
    ),
)


INITIAL_FINANCIAL_DATA_SOURCE_CONFIG_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "schema_version": FINANCIAL_DATA_SOURCE_CONFIG_TEMPLATE_SCHEMA_VERSION,
        "template_id": "template-tw-tej-research-backfill",
        "data_source_id": "ds-tej-tw-research-backfill",
        "connector_id": "tw-tej-research-datasets",
        "source_type": "market",
        "provider": "TEJ",
        "auth": {"auth_type": "api_key", "secret_ref_id": "env://TEJ_API_KEY"},
        "fetch": {
            "mode": "provider_owned_adapter",
            "adapter": "TejSourceIngestAdapter",
            "base_url_ref": "https://api.tej.com.tw",
            "metadata_fetcher": "TaiwanMarketClient.fetch_tej_table_metadata",
            "dataset_fetcher": "TaiwanMarketClient.fetch_tej_dataset",
            "table_inventory_cache": {
                "cache_key": "tej://table-inventory/tw-research-backfill",
                "refresh_cadence": "manual_or_weekly_when_credentialed",
                "credential_required": True,
            },
            "credential_smoke": {
                "without_key_status": "credential_unavailable",
                "with_key_checks": ["table_metadata", "small_dataset_read"],
                "small_read_limit": 5,
            },
            "purchased_table_allowlist": [],
            "allowlist_required": True,
            "deny_unlisted_tables": True,
            "candidate_tables": [dict(table) for table in TEJ_PAID_BACKFILL_TABLE_CANDIDATES],
            "purchased_fundamental_tables": {
                "dataset_code_source": "operator_entitlement_metadata",
                "normalized_target": "tw_financial_statement",
                "allowlist_required": True,
            },
            "backfill_request_schema": {
                "required_fields": ["dataset_code", "start_date", "end_date", "symbol_universe", "entitlement_metadata"],
                "date_range_field": "mdate",
                "run_by_default": False,
            },
        },
        "schedule": {
            "cadence": "manual_one_time_historical_backfill",
            "universe_tiers": ["core_universe", "candidate_universe"],
            "archive_behavior": "gap_report_selected_price_only",
            "full_market_default": False,
        },
        "lifecycle_state": "candidate",
    },
    {
        "schema_version": FINANCIAL_DATA_SOURCE_CONFIG_TEMPLATE_SCHEMA_VERSION,
        "template_id": "template-tw-finmind-datasets",
        "data_source_id": "ds-finmind-tw-data",
        "connector_id": "tw-finmind-datasets",
        "source_type": "market",
        "provider": "FinMind",
        "auth": {"auth_type": "api_key", "secret_ref_id": "env://FINMIND_API_TOKEN"},
        "fetch": {
            "mode": "provider_owned_adapter",
            "adapter": "FinMindTaiwanDatasetAdapter",
            "base_url_ref": "https://api.finmindtrade.com/api/v4",
            "datasets": [
                "TaiwanStockPrice",
                "TaiwanStockDayTrading",
                "TaiwanStockInstitutionalInvestorsBuySell",
                "TaiwanStockMarginPurchaseShortSale",
                "TaiwanStockSecuritiesLending",
                "TaiwanStockShareholding",
                "TaiwanStockNews",
            ],
        },
        "schedule": {
            "cadence": "daily_after_close",
            "universe_tiers": ["core_universe", "candidate_universe"],
            "archive_behavior": "daily_price_baseline_only_elsewhere",
        },
        "lifecycle_state": "candidate",
    },
    {
        "schema_version": FINANCIAL_DATA_SOURCE_CONFIG_TEMPLATE_SCHEMA_VERSION,
        "template_id": "template-tw-finmind-broker-daily-report",
        "data_source_id": "ds-finmind-tw-data",
        "connector_id": "tw-finmind-broker-daily-report",
        "source_type": "market",
        "provider": "FinMind",
        "auth": {"auth_type": "api_key", "secret_ref_id": "env://FINMIND_API_TOKEN"},
        "fetch": {
            "mode": "provider_owned_adapter",
            "adapter": "FinMindTaiwanBrokerDailyReportAdapter",
            "dataset": "TaiwanStockTradingDailyReport",
            "normalized_target": "tw_broker_top",
            "max_rank": 20,
        },
        "schedule": {
            "cadence": "daily_after_close",
            "universe_tiers": ["core_universe", "candidate_universe"],
            "archive_behavior": "skip",
        },
        "lifecycle_state": "candidate",
    },
    {
        "schema_version": FINANCIAL_DATA_SOURCE_CONFIG_TEMPLATE_SCHEMA_VERSION,
        "template_id": "template-tw-finmind-broker-bulk-parquet",
        "data_source_id": "ds-finmind-tw-data",
        "connector_id": "tw-finmind-broker-bulk-parquet",
        "source_type": "market",
        "provider": "FinMind",
        "auth": {"auth_type": "api_key", "secret_ref_id": "env://FINMIND_API_TOKEN"},
        "fetch": {
            "mode": "provider_owned_adapter",
            "adapter": "FinMindTaiwanBrokerBulkBackfillAdapter",
            "dataset": "TaiwanStockTradingDailyReport",
            "run_by_default": False,
            "signed_url_redaction_required": True,
        },
        "schedule": {
            "cadence": "manual_one_time_historical_backfill",
            "universe_tiers": ["core_universe", "candidate_universe"],
            "archive_behavior": "filter_after_raw_partition_load",
        },
        "lifecycle_state": "candidate",
    },
    {
        "schema_version": FINANCIAL_DATA_SOURCE_CONFIG_TEMPLATE_SCHEMA_VERSION,
        "template_id": "template-tw-twse-tpex-official-market",
        "data_source_id": "ds-twse-tpex-official-market",
        "connector_id": "tw-twse-tpex-official-market",
        "source_type": "market",
        "provider": "TWSE/TPEx",
        "auth": {"auth_type": "none"},
        "fetch": {
            "mode": "provider_owned_adapter",
            "adapter": "TaiwanOfficialMarketDatasetAdapter",
            "datasets": ["tw_price_daily", "tw_exchange_chip_summary"],
        },
        "schedule": {
            "cadence": "daily_after_close",
            "universe_tiers": ["core_universe", "candidate_universe", "archive_universe"],
            "archive_behavior": "daily_price_only",
        },
        "lifecycle_state": "candidate",
    },
    {
        "schema_version": FINANCIAL_DATA_SOURCE_CONFIG_TEMPLATE_SCHEMA_VERSION,
        "template_id": "template-tw-mops-official-disclosures",
        "data_source_id": "ds-mops-official-filings",
        "connector_id": "tw-mops-official-disclosures",
        "source_type": "filing",
        "provider": "MOPS",
        "auth": {"auth_type": "none"},
        "fetch": {
            "mode": "provider_owned_adapter",
            "adapter": "MopsSourceIngestAdapter",
            "datasets": [
                "tw_material_events",
                "tw_monthly_revenue",
                "tw_financial_statement",
                "tw_company_master",
                "tw_corporate_action",
            ],
            "normalized_targets": [
                "tw_material_event",
                "tw_monthly_revenue",
                "tw_financial_statement",
                "tw_company_master",
                "tw_corporate_action",
            ],
            "restatement_correction_gap_report": "mops_restatement_correction_gap_report.v1",
        },
        "schedule": {
            "cadence": "event_poll_10m_to_30m plus daily_scan",
            "event_cadence": "event_poll_10m_to_30m",
            "daily_scan_cadence": "daily_scan_monthly_quarterly_event_facts",
            "event_universe_tiers": ["core_universe", "candidate_universe", "archive_universe"],
            "fundamental_universe_tiers": ["core_universe"],
            "universe_tiers": ["core_universe", "candidate_universe", "archive_universe"],
            "archive_behavior": "material_events_only",
            "candidate_behavior": "material_events_only_for_fundamentals_until_promoted_to_core",
            "backup_source": "TEJ research-grade fallback only; MOPS remains official reference truth",
        },
        "lifecycle_state": "candidate",
    },
    {
        "schema_version": FINANCIAL_DATA_SOURCE_CONFIG_TEMPLATE_SCHEMA_VERSION,
        "template_id": "template-tw-yahoo-stock-rss",
        "data_source_id": "ds-yahoo-tw-news-broker",
        "connector_id": "tw-yahoo-stock-rss",
        "source_type": "news",
        "provider": "Yahoo Taiwan Stock",
        "auth": {"auth_type": "none"},
        "fetch": {
            "mode": "provider_owned_adapter",
            "adapter": "YahooTaiwanRssAdapter",
            "dataset": "tw_news_metadata",
            "full_text_allowed_by_default": False,
        },
        "schedule": {
            "cadence": "10m_to_30m",
            "universe_tiers": ["core_universe", "candidate_universe"],
            "archive_behavior": "skip",
        },
        "lifecycle_state": "candidate",
    },
    {
        "schema_version": FINANCIAL_DATA_SOURCE_CONFIG_TEMPLATE_SCHEMA_VERSION,
        "template_id": "template-tw-yahoo-broker-top15",
        "data_source_id": "ds-yahoo-tw-news-broker",
        "connector_id": "tw-yahoo-broker-top15",
        "source_type": "market",
        "provider": "Yahoo Taiwan Stock",
        "auth": {"auth_type": "none"},
        "fetch": {
            "mode": "provider_owned_adapter",
            "adapter": "YahooTaiwanBrokerTopAdapter",
            "dataset": "tw_broker_top",
            "max_rank": 15,
            "fallback_for_connector_id": "tw-finmind-broker-daily-report",
        },
        "schedule": {
            "cadence": "daily_after_close_fallback",
            "universe_tiers": ["core_universe", "candidate_universe"],
            "archive_behavior": "skip",
        },
        "lifecycle_state": "candidate",
    },
    {
        "schema_version": FINANCIAL_DATA_SOURCE_CONFIG_TEMPLATE_SCHEMA_VERSION,
        "template_id": "template-us-sec-edgar-filings",
        "data_source_id": "ds-sec-edgar-filings",
        "connector_id": "us-sec-edgar-filings",
        "source_type": "filing",
        "provider": "SEC EDGAR",
        "auth": {"auth_type": "none", "policy_note": "bounded client must set a compliant user-agent"},
        "fetch": {
            "mode": "provider_owned_adapter",
            "adapter": "SecEdgarFilingAdapter",
            "dataset": "sec_filing_event",
        },
        "schedule": {
            "cadence": "event_poll_daily",
            "universe_tiers": ["core_universe", "candidate_universe", "archive_universe"],
            "archive_behavior": "filing_events_only",
        },
        "lifecycle_state": "candidate",
    },
    {
        "schema_version": FINANCIAL_DATA_SOURCE_CONFIG_TEMPLATE_SCHEMA_VERSION,
        "template_id": "template-us-fred-macro",
        "data_source_id": "ds-fred-macro",
        "connector_id": "us-fred-macro",
        "source_type": "macro",
        "provider": "FRED",
        "auth": {"auth_type": "none", "optional_secret_ref_id": "env://FRED_API_KEY"},
        "fetch": {
            "mode": "provider_owned_adapter",
            "adapter": "FredMacroSeriesAdapter",
            "dataset": "macro_fred_observation",
            "symbol_scope": "global_no_symbol_filter",
        },
        "schedule": {
            "cadence": "daily_weekly_monthly_by_series_frequency",
            "universe_tiers": ["core_universe", "candidate_universe", "archive_universe"],
            "archive_behavior": "not_symbol_scoped",
        },
        "lifecycle_state": "candidate",
    },
)


def initial_financial_data_source_entries() -> tuple[DataSourceEntry, ...]:
    return INITIAL_FINANCIAL_DATA_SOURCE_ENTRIES


def initial_financial_data_source_config_templates() -> tuple[dict[str, Any], ...]:
    return tuple(deepcopy(template) for template in INITIAL_FINANCIAL_DATA_SOURCE_CONFIG_TEMPLATES)


def financial_data_source_catalog_payload() -> dict[str, Any]:
    entries = [entry.to_dict() for entry in INITIAL_FINANCIAL_DATA_SOURCE_ENTRIES]
    templates = initial_financial_data_source_config_templates()
    providers = sorted({entry["provider"] for entry in entries})
    active_policy = active_universe_policy_payload(DEFAULT_SOURCE_UPDATE_RULES)
    return {
        "schema_version": FINANCIAL_DATA_SOURCE_CATALOG_SCHEMA_VERSION,
        "catalog_updated_at": CATALOG_UPDATED_AT,
        "catalog_status": "template_only_not_live_ingestion_claim",
        "entries": entries,
        "config_templates": list(templates),
        "active_universe_policy": active_policy,
        "summary": {
            "data_source_count": len(entries),
            "config_template_count": len(templates),
            "provider_count": len(providers),
            "providers": providers,
            "active_universe_rule_count": active_policy["summary"]["rule_count"],
            "archive_baseline_rule_count": active_policy["summary"]["archive_baseline_rule_count"],
        },
    }
