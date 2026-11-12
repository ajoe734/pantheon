"""Active-universe planning for market-data ingestion.

The planner keeps high-volume connectors scoped to the symbols that still
matter. Core/candidate names get daily detail updates; archived names keep only
cheap baseline maintenance elsewhere.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


class UniverseTier(str, Enum):
    CORE = "core_universe"
    CANDIDATE = "candidate_universe"
    ARCHIVE = "archive_universe"


def _coerce_tier(value: UniverseTier | str) -> UniverseTier:
    if isinstance(value, UniverseTier):
        return value
    try:
        return UniverseTier(str(value))
    except ValueError as exc:
        allowed = ", ".join(tier.value for tier in UniverseTier)
        raise ValueError(f"tier must be one of: {allowed}") from exc


@dataclass(frozen=True)
class ActiveUniverseMember:
    symbol: str
    tier: UniverseTier | str
    market: str = "TW"
    venue: str | None = None
    reason: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        symbol = str(self.symbol or "").strip().upper()
        if not symbol:
            raise ValueError("symbol is required")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "tier", _coerce_tier(self.tier))
        object.__setattr__(self, "market", str(self.market or "TW").strip().upper())
        venue = str(self.venue or "").strip() or None
        object.__setattr__(self, "venue", venue)
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "tier": self.tier.value,
            "market": self.market,
            "venue": self.venue,
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class UniverseTransition:
    symbol: str
    from_tier: UniverseTier | str
    to_tier: UniverseTier | str
    reason: str
    triggered_by: str
    effective_at: str
    market: str = "TW"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        symbol = str(self.symbol or "").strip().upper()
        if not symbol:
            raise ValueError("symbol is required")
        reason = str(self.reason or "").strip()
        if not reason:
            raise ValueError("reason is required")
        triggered_by = str(self.triggered_by or "").strip()
        if not triggered_by:
            raise ValueError("triggered_by is required")
        effective_at = str(self.effective_at or "").strip()
        if not effective_at:
            raise ValueError("effective_at is required")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "from_tier", _coerce_tier(self.from_tier))
        object.__setattr__(self, "to_tier", _coerce_tier(self.to_tier))
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "triggered_by", triggered_by)
        object.__setattr__(self, "effective_at", effective_at)
        object.__setattr__(self, "market", str(self.market or "TW").strip().upper())
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "market": self.market,
            "from_tier": self.from_tier.value,
            "to_tier": self.to_tier.value,
            "reason": self.reason,
            "triggered_by": self.triggered_by,
            "effective_at": self.effective_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class SourceUpdateRule:
    connector_id: str
    dataset: str
    eligible_tiers: Sequence[UniverseTier | str]
    cadence: str
    market: str = "TW"
    priority: int = 100
    max_symbols_per_run: int | None = None
    reason: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        connector_id = str(self.connector_id or "").strip()
        dataset = str(self.dataset or "").strip()
        if not connector_id:
            raise ValueError("connector_id is required")
        if not dataset:
            raise ValueError("dataset is required")
        tiers = tuple(_coerce_tier(tier) for tier in self.eligible_tiers)
        if not tiers:
            raise ValueError("eligible_tiers is required")
        object.__setattr__(self, "connector_id", connector_id)
        object.__setattr__(self, "dataset", dataset)
        object.__setattr__(self, "eligible_tiers", tiers)
        object.__setattr__(self, "market", str(self.market or "TW").strip().upper())
        object.__setattr__(self, "priority", int(self.priority))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "dataset": self.dataset,
            "eligible_tiers": [tier.value for tier in self.eligible_tiers],
            "cadence": self.cadence,
            "market": self.market,
            "priority": self.priority,
            "max_symbols_per_run": self.max_symbols_per_run,
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }


DEFAULT_SOURCE_UPDATE_RULES: tuple[SourceUpdateRule, ...] = (
    SourceUpdateRule(
        connector_id="tw-twse-tpex-official-market",
        dataset="tw_industry_classification",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE),
        cadence="daily_after_close",
        priority=6,
        reason="Prospectively accumulate official OpenAPI industry observations through the normal refresh path",
        metadata={
            "source_role": "official_reference",
            "normalized_target": "tw_industry_classification",
            "storage_targets": ["normalized/tw_industry_classification"],
            "archive_behavior": "skip",
            "backfill": False,
        },
    ),
    SourceUpdateRule(
        connector_id="tw-twse-tpex-official-market",
        dataset="tw_price_daily",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE, UniverseTier.ARCHIVE),
        cadence="daily_after_close",
        priority=5,
        reason="TWSE/TPEx official daily price is the baseline market-maintenance source for all active-universe tiers",
        metadata={
            "detail_level": "official_daily_price_baseline",
            "source_role": "preferred_official_reference",
            "storage_targets": ["normalized/tw_price_daily", "features/returns"],
            "archive_behavior": "daily_price_only",
        },
    ),
    SourceUpdateRule(
        connector_id="tw-finmind-kbar-1m",
        dataset="tw_price_1m",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE),
        cadence="daily_after_close",
        priority=12,
        max_symbols_per_run=200,
        reason="FinMind KBar 1-minute post-close feed for the active universe; stored by the intraday store job, never in tw_price_daily",
        metadata={
            "job_class": "intraday_minute_store_backfill",
            "detail_level": "one_minute_bars_post_close",
            "source_dataset": "TaiwanStockKBar",
            "entitlement_tier": "sponsor",
            "normalized_target": "tw_price_1m",
            "storage_targets": ["normalized/tw_price_1m"],
            "store_root_env": "INTRADAY_STORE_ROOT",
            "runner": "services.source_ingestion.intraday_backfill.run_backfill",
            "history_days": 730,
            "hourly_request_limit": 4500,
            "default_universe_size": 200,
            "daily_snapshot_projection": "excluded",
            "archive_behavior": "skip",
        },
    ),
    SourceUpdateRule(
        connector_id="tw-finmind-broker-daily-report",
        dataset="tw_broker_top",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE),
        cadence="daily_after_close",
        priority=10,
        reason="FinMind Sponsor branch-trading daily report is the low-cost primary top20 broker source for active symbols",
        metadata={
            "detail_level": "top20_buy_sell_aggregated_from_full_branch_daily_report",
            "source_dataset": "TaiwanStockTradingDailyReport",
            "entitlement_tier": "sponsor",
            "history_start": "2021-06-30",
            "archive_behavior": "skip",
        },
    ),
    SourceUpdateRule(
        connector_id="tw-finmind-datasets",
        dataset="tw_daily_price_and_chip",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE),
        cadence="daily_after_close",
        priority=15,
        reason="FinMind normalized API covers active-universe Taiwan daily price, institutional, margin, lending, holding, and news metadata",
        metadata={
            "datasets": [
                "TaiwanStockPrice",
                "TaiwanStockDayTrading",
                "TaiwanStockInstitutionalInvestorsBuySell",
                "TaiwanStockMarginPurchaseShortSale",
                "TaiwanStockSecuritiesLending",
                "TaiwanStockShareholding",
                "TaiwanStockNews",
            ],
            "entitlement_tier": "sponsor",
            "archive_behavior": "baseline_only_elsewhere",
        },
    ),
    SourceUpdateRule(
        connector_id="tw-mops-official-disclosures",
        dataset="tw_material_events",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE, UniverseTier.ARCHIVE),
        cadence="event_poll_10m_to_30m",
        priority=25,
        reason="MOPS material events remain official-reference event truth even after a symbol moves to archive",
        metadata={
            "detail_level": "official_event_metadata",
            "source_role": "official_reference",
            "normalized_target": "tw_material_event",
            "storage_targets": ["raw/mops", "normalized/tw_material_event"],
            "archive_behavior": "material_events_only",
        },
    ),
    SourceUpdateRule(
        connector_id="tw-mops-official-disclosures",
        dataset="tw_monthly_revenue",
        eligible_tiers=(UniverseTier.CORE,),
        cadence="daily_scan_monthly_event_facts",
        priority=30,
        reason="core universe keeps full MOPS monthly revenue refresh from official disclosure routes",
        metadata={
            "normalized_target": "tw_monthly_revenue",
            "source_role": "official_reference",
            "storage_targets": ["normalized/tw_monthly_revenue"],
            "backup_source": "TEJ research-grade fallback only",
            "candidate_behavior": "defer_until_promoted_to_core",
            "archive_behavior": "skip_except_material_events",
        },
    ),
    SourceUpdateRule(
        connector_id="tw-mops-official-disclosures",
        dataset="tw_financial_statement",
        eligible_tiers=(UniverseTier.CORE,),
        cadence="daily_scan_quarterly_event_facts",
        priority=31,
        reason="core universe keeps MOPS financial statement and restatement/correction route refresh",
        metadata={
            "normalized_target": "tw_financial_statement",
            "source_role": "official_reference",
            "storage_targets": ["normalized/tw_financial_statement"],
            "restatement_correction_gap_report": "mops_restatement_correction_gap_report.v1",
            "backup_source": "TEJ research-grade fallback only",
            "candidate_behavior": "defer_until_promoted_to_core",
            "archive_behavior": "skip_except_material_events",
        },
    ),
    SourceUpdateRule(
        connector_id="tw-tdcc-shareholding-distribution",
        dataset="tdcc_shareholding_distribution",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE),
        cadence="weekly_after_tdcc_publication",
        priority=40,
        reason="TDCC weekly shareholding distribution supports holding-concentration features for active Taiwan symbols only",
        metadata={
            "detail_level": "weekly_shareholding_distribution",
            "source_role": "official_reference",
            "normalized_target": "tdcc_shareholding_distribution",
            "storage_targets": [
                "raw/tdcc/shareholding_distribution",
                "normalized/tdcc_shareholding_distribution",
                "features/tdcc_holding_concentration",
            ],
            "raw_storage_policy": {
                "compression": "gzip",
                "retention_days": 2555,
                "partition": "raw/tdcc/shareholding_distribution/week=YYYY-WW/",
            },
            "archive_behavior": "skip_except_repair_selected",
            "repair_override_required": True,
        },
    ),
    SourceUpdateRule(
        connector_id="tw-taifex-futures-options-chip",
        dataset="taifex_futures_chip",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE),
        cadence="daily_after_taifex_publication",
        priority=45,
        reason="TAIFEX futures participant and open-interest context is market-level Taiwan chip evidence, not per-symbol archive fanout",
        metadata={
            "detail_level": "daily_derivatives_participant_flow",
            "source_role": "official_reference",
            "normalized_target": "taifex_futures_chip",
            "symbol_scope": "market_context_no_symbol_filter",
            "storage_targets": [
                "raw/taifex/futures_chip",
                "normalized/taifex_futures_chip",
                "features/taifex_oi_regime",
            ],
            "raw_storage_policy": {
                "compression": "gzip",
                "retention_days": 2555,
                "partition": "raw/taifex/futures_chip/date=YYYY-MM-DD/",
            },
            "archive_behavior": "skip_symbol_archive_detail",
            "repair_override_required": True,
        },
    ),
    SourceUpdateRule(
        connector_id="tw-taifex-futures-options-chip",
        dataset="taifex_options_chip",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE),
        cadence="daily_after_taifex_publication",
        priority=46,
        reason="TAIFEX options put/call and open-interest context is market-level Taiwan regime evidence, not per-symbol archive fanout",
        metadata={
            "detail_level": "daily_options_participant_flow",
            "source_role": "official_reference",
            "normalized_target": "taifex_options_chip",
            "symbol_scope": "market_context_no_symbol_filter",
            "storage_targets": [
                "raw/taifex/options_chip",
                "normalized/taifex_options_chip",
                "features/taifex_put_call_regime",
            ],
            "raw_storage_policy": {
                "compression": "gzip",
                "retention_days": 2555,
                "partition": "raw/taifex/options_chip/date=YYYY-MM-DD/",
            },
            "archive_behavior": "skip_symbol_archive_detail",
            "repair_override_required": True,
        },
    ),
    SourceUpdateRule(
        connector_id="tw-taifex-futures-options-chip",
        dataset="taifex_ssf_contracts",
        eligible_tiers=(UniverseTier.CORE,),
        cadence="daily_after_taifex_publication",
        priority=47,
        reason="TAIFEX single stock and ETF futures margin, settlement and unit tables feed the instrument candidate list for active Taiwan paper personas",
        metadata={
            "detail_level": "daily_single_stock_futures_contract_master",
            "source_role": "official_reference",
            "normalized_target": "taifex_ssf_contracts",
            "symbol_scope": "active_tw_paper_persona_underlyings",
            "storage_targets": [
                "raw/taifex/ssf",
                "normalized/taifex_ssf_contracts",
            ],
            "raw_storage_policy": {
                "compression": "gzip",
                "retention_days": 2555,
                "partition": "raw/taifex/ssf/date=YYYY-MM-DD/",
            },
            "archive_behavior": "skip_symbol_archive_detail",
            "repair_override_required": True,
        },
    ),
    SourceUpdateRule(
        connector_id="tw-finmind-broker-bulk-parquet",
        dataset="tw_broker_trading_daily_backfill",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE),
        cadence="manual_one_time_historical_backfill",
        priority=200,
        reason="FinMind SponsorPro daily parquet backfills 2021-06-30 to now before falling back to TEJ or TWSE/TPEx purchased history",
        metadata={
            "source_dataset": "TaiwanStockTradingDailyReport",
            "entitlement_tier": "sponsorpro",
            "history_start": "2021-06-30",
            "raw_storage_partition": "raw/finmind/TaiwanStockTradingDailyReport/date=YYYY-MM-DD/",
            "normalization_target": "tw_broker_top",
            "archive_behavior": "filter_after_raw_partition_load",
            "run_by_default": False,
        },
    ),
    SourceUpdateRule(
        connector_id="tw-tej-research-datasets",
        dataset="tw_paid_historical_gap_fill",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE),
        cadence="manual_one_time_historical_backfill",
        priority=220,
        reason="TEJ paid tables fill older or vendor-specific Taiwan gaps only after public, FinMind, and Yahoo sources are exhausted",
        metadata={
            "candidate_dataset_codes": ["TWN/APRCD1", "TWN/AMTOP1", "TWN/ABSR20"],
            "normalization_targets": ["tw_price_daily", "tw_broker_top", "tw_financial_statement"],
            "purchased_table_allowlist_required": True,
            "credential_secret_ref_id": "env://TEJ_API_KEY",
            "archive_behavior": "gap_report_selected_price_only",
            "run_by_default": False,
        },
    ),
    SourceUpdateRule(
        connector_id="us-sec-edgar-filings",
        dataset="sec_filing_event",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE, UniverseTier.ARCHIVE),
        cadence="event_poll_daily",
        market="US",
        priority=30,
        reason="SEC EDGAR filing events are the official US event baseline for all tracked US symbols",
        metadata={
            "detail_level": "filing_event_metadata",
            "source_role": "official_reference",
            "storage_targets": ["raw/sec-edgar", "normalized/sec_filing_event"],
            "archive_behavior": "filing_events_only",
        },
    ),
    SourceUpdateRule(
        connector_id="us-fred-macro",
        dataset="macro_fred_observation",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE, UniverseTier.ARCHIVE),
        cadence="daily_weekly_monthly_by_series_frequency",
        market="GLOBAL",
        priority=80,
        reason="FRED macro series are global context and are maintained outside per-symbol detail fanout",
        metadata={
            "detail_level": "symbolless_macro_context",
            "source_role": "public_macro_reference",
            "symbol_scope": "global_no_symbol_filter",
            "storage_targets": ["normalized/macro_fred_observation", "features/macro_regime_features"],
            "archive_behavior": "not_symbol_scoped",
        },
    ),
    SourceUpdateRule(
        connector_id="us-finra-short-sale",
        dataset="us_short_volume_daily",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE),
        cadence="daily_after_finra_publication_window",
        market="US",
        priority=85,
        max_symbols_per_run=500,
        reason="FINRA public daily short-volume files provide US short-sale pressure context for active research symbols",
        metadata={
            "detail_level": "daily_short_volume",
            "source_role": "public_short_sale_reference",
            "storage_targets": ["raw/finra/regsho", "normalized/us_short_volume_daily"],
            "expected_publication_delay_hours": 26,
            "archive_behavior": "skip",
        },
    ),
    SourceUpdateRule(
        connector_id="crypto-coingecko-spot",
        dataset="crypto_spot_ohlc_and_price",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE, UniverseTier.ARCHIVE),
        cadence="daily_crypto_24x7_poll",
        market="CRYPTO",
        priority=55,
        max_symbols_per_run=100,
        reason="CoinGecko keyless public API provides crypto spot OHLC and price research evidence for persona-crypto",
        metadata={
            "detail_level": "spot_ohlc_and_price_public_reference",
            "source_role": "public_crypto_market_reference",
            "provider_owned_adapter": "CoinGeckoSpotMarketAdapter.records_from_payload",
            "storage_targets": [
                "raw/coingecko/spot",
                "normalized/crypto_spot_ohlc",
                "normalized/crypto_spot_price",
                "features/crypto_returns",
            ],
            "symbol_id_mapping": {
                "BTC": "bitcoin",
                "ETH": "ethereum",
                "SOL": "solana",
            },
            "archive_behavior": "daily_price_baseline",
            "order_capable_provider": False,
        },
    ),
)


def active_universe_policy_payload(
    rules: Sequence[SourceUpdateRule | Mapping[str, Any]] = DEFAULT_SOURCE_UPDATE_RULES,
) -> dict[str, Any]:
    normalized_rules = tuple(_rule(rule) for rule in rules)
    archive_baseline_rules = [
        rule
        for rule in normalized_rules
        if UniverseTier.ARCHIVE in tuple(rule.eligible_tiers)
    ]
    archive_skipped_rules = [
        rule
        for rule in normalized_rules
        if UniverseTier.ARCHIVE not in tuple(rule.eligible_tiers)
    ]
    return {
        "schema_version": "active_universe_scheduling_policy.v1",
        "tiers": [
            {
                "tier": UniverseTier.CORE.value,
                "definition": "holdings, active strategies, and active research targets",
                "data_policy": "full price, financials, chip, broker top N, news, and event refresh",
            },
            {
                "tier": UniverseTier.CANDIDATE.value,
                "definition": "likely research or trading candidates",
                "data_policy": "price, chip summary, broker top N, and news metadata refresh",
            },
            {
                "tier": UniverseTier.ARCHIVE.value,
                "definition": "symbols removed from research and trading focus",
                "data_policy": "daily price and material events only; skip broker and detailed news fanout",
            },
        ],
        "transition_event_schema": {
            "schema_version": "universe_transition.v1",
            "required_fields": [
                "symbol",
                "market",
                "from_tier",
                "to_tier",
                "reason",
                "triggered_by",
                "effective_at",
            ],
            "tier_values": [tier.value for tier in UniverseTier],
        },
        "scheduling_rules": [rule.to_dict() for rule in normalized_rules],
        "summary": {
            "rule_count": len(normalized_rules),
            "archive_baseline_rule_count": len(archive_baseline_rules),
            "archive_detail_skip_rule_count": len(archive_skipped_rules),
            "core_detail_connector_ids": sorted(
                {
                    rule.connector_id
                    for rule in normalized_rules
                    if UniverseTier.CORE in tuple(rule.eligible_tiers)
                }
            ),
            "candidate_detail_connector_ids": sorted(
                {
                    rule.connector_id
                    for rule in normalized_rules
                    if UniverseTier.CANDIDATE in tuple(rule.eligible_tiers)
                }
            ),
            "archive_baseline_connector_ids": sorted({rule.connector_id for rule in archive_baseline_rules}),
        },
    }


def build_active_universe_update_plan(
    members: Sequence[ActiveUniverseMember | Mapping[str, Any]],
    *,
    rules: Sequence[SourceUpdateRule | Mapping[str, Any]] = DEFAULT_SOURCE_UPDATE_RULES,
    personas: Sequence[Mapping[str, Any] | str] | None = None,
    runtime_bindings: Sequence[Mapping[str, Any]] | None = None,
    active_paper_symbols: Sequence[str] | None = None,
) -> dict[str, Any]:
    normalized_members = tuple(_member(member) for member in members)
    normalized_rules = tuple(_rule(rule) for rule in rules)
    members_by_tier: dict[str, list[str]] = {tier.value: [] for tier in UniverseTier}
    for member in normalized_members:
        members_by_tier[member.tier.value].append(member.symbol)

    connector_updates: list[dict[str, Any]] = []
    skipped_archives: set[str] = set()
    for rule in sorted(normalized_rules, key=lambda item: (item.priority, item.connector_id)):
        eligible = [
            member
            for member in normalized_members
            if member.market == rule.market and member.tier in set(rule.eligible_tiers)
        ]
        symbols = _unique_symbols(member.symbol for member in eligible)
        symbol_scope = str(rule.metadata.get("symbol_scope") or "").strip()
        if symbol_scope in {"global_no_symbol_filter", "market_context_no_symbol_filter"}:
            symbols = []
        elif symbol_scope == "active_tw_paper_persona_underlyings":
            symbols = resolve_active_tw_paper_persona_underlyings(
                eligible,
                personas=personas,
                runtime_bindings=runtime_bindings,
                active_paper_symbols=active_paper_symbols,
                rule_metadata=rule.metadata,
            )
        if rule.max_symbols_per_run is not None:
            symbols = symbols[: int(rule.max_symbols_per_run)]
        for member in normalized_members:
            if member.market == rule.market and member.tier == UniverseTier.ARCHIVE:
                skipped_archives.add(member.symbol)
        update_entry: dict[str, Any] = {
            "connector_id": rule.connector_id,
            "dataset": rule.dataset,
            "cadence": rule.cadence,
            "market": rule.market,
            "eligible_tiers": [tier.value for tier in rule.eligible_tiers],
            "symbols": symbols,
            "symbol_count": len(symbols),
            "priority": rule.priority,
            "reason": rule.reason,
            "metadata": dict(rule.metadata),
        }
        if symbol_scope == "active_tw_paper_persona_underlyings":
            update_entry["contracts"] = list(symbols)
        connector_updates.append(update_entry)

    return {
        "schema_version": "active_universe_update_plan.v1",
        "policy_ref": "active_universe_scheduling_policy.v1",
        "members": [member.to_dict() for member in normalized_members],
        "rules": [rule.to_dict() for rule in normalized_rules],
        "connector_updates": connector_updates,
        "summary": {
            "member_count": len(normalized_members),
            "connector_update_count": len(connector_updates),
            "core_count": len(members_by_tier[UniverseTier.CORE.value]),
            "candidate_count": len(members_by_tier[UniverseTier.CANDIDATE.value]),
            "archive_count": len(members_by_tier[UniverseTier.ARCHIVE.value]),
            "archive_detail_updates_skipped": sorted(skipped_archives),
        },
    }


def build_active_universe_job_fanout(
    members: Sequence[ActiveUniverseMember | Mapping[str, Any]],
    *,
    rules: Sequence[SourceUpdateRule | Mapping[str, Any]] = DEFAULT_SOURCE_UPDATE_RULES,
    run_date: str,
    default_max_symbols_per_job: int = 50,
    personas: Sequence[Mapping[str, Any] | str] | None = None,
    runtime_bindings: Sequence[Mapping[str, Any]] | None = None,
    active_paper_symbols: Sequence[str] | None = None,
) -> dict[str, Any]:
    if default_max_symbols_per_job < 1:
        raise ValueError("default_max_symbols_per_job must be >= 1")
    normalized_members = tuple(_member(member) for member in members)
    normalized_rules = tuple(_rule(rule) for rule in rules)
    plan = build_active_universe_update_plan(
        normalized_members,
        rules=normalized_rules,
        personas=personas,
        runtime_bindings=runtime_bindings,
        active_paper_symbols=active_paper_symbols,
    )

    jobs: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for rule in sorted(normalized_rules, key=lambda item: (item.priority, item.connector_id, item.dataset)):
        eligible = [
            member
            for member in normalized_members
            if member.market == rule.market and member.tier in set(rule.eligible_tiers)
        ]
        skipped.extend(
            {
                "symbol": member.symbol,
                "market": member.market,
                "tier": member.tier.value,
                "connector_id": rule.connector_id,
                "dataset": rule.dataset,
                "reason": "not-in-universe",
            }
            for member in normalized_members
            if member.market == rule.market and member.tier not in set(rule.eligible_tiers)
        )
        symbols = _unique_symbols(member.symbol for member in eligible)
        symbol_scope = str(rule.metadata.get("symbol_scope") or "").strip()
        if symbol_scope in {"global_no_symbol_filter", "market_context_no_symbol_filter"}:
            symbols = []
            batches = [[]]
        elif symbol_scope == "active_tw_paper_persona_underlyings":
            symbols = resolve_active_tw_paper_persona_underlyings(
                eligible,
                personas=personas,
                runtime_bindings=runtime_bindings,
                active_paper_symbols=active_paper_symbols,
                rule_metadata=rule.metadata,
            )
            if symbols:
                batch_size = int(rule.max_symbols_per_run or default_max_symbols_per_job)
                batches = [symbols[index : index + batch_size] for index in range(0, len(symbols), batch_size)]
            else:
                batches = []
        elif symbols:
            batch_size = int(rule.max_symbols_per_run or default_max_symbols_per_job)
            batches = [symbols[index : index + batch_size] for index in range(0, len(symbols), batch_size)]
        else:
            batches = []

        for batch_index, batch in enumerate(batches, start=1):
            job_entry: dict[str, Any] = {
                "schema_version": "active_universe_ingest_job.v1",
                "connector_id": rule.connector_id,
                "dataset": rule.dataset,
                "run_date": run_date,
                "market": rule.market,
                "cadence": rule.cadence,
                "priority": rule.priority,
                "symbols": list(batch),
                "symbol_count": len(batch),
                "batch_index": batch_index,
                "batch_count": len(batches),
                "eligible_tiers": [tier.value for tier in rule.eligible_tiers],
                "metadata": dict(rule.metadata),
            }
            if symbol_scope == "active_tw_paper_persona_underlyings":
                job_entry["contracts"] = list(batch)
            jobs.append(job_entry)

    return {
        "schema_version": "active_universe_ingest_fanout.v1",
        "run_date": run_date,
        "plan": plan,
        "jobs": jobs,
        "skipped": skipped,
        "summary": {
            "job_count": len(jobs),
            "skipped_count": len(skipped),
            "connector_count": len({job["connector_id"] for job in jobs}),
            "default_max_symbols_per_job": default_max_symbols_per_job,
        },
    }


_MEMBER_RESERVED_KEYS = {"symbol", "tier", "market", "venue", "reason", "metadata"}


def _member(value: ActiveUniverseMember | Mapping[str, Any]) -> ActiveUniverseMember:
    if isinstance(value, ActiveUniverseMember):
        return value
    extra_meta = {str(k): v for k, v in value.items() if str(k) not in _MEMBER_RESERVED_KEYS}
    explicit_meta = dict(value.get("metadata") or {})
    merged_meta = {**extra_meta, **explicit_meta}
    return ActiveUniverseMember(
        symbol=str(value["symbol"]),
        tier=value.get("tier", UniverseTier.CANDIDATE.value),
        market=str(value.get("market") or "TW"),
        venue=value.get("venue"),
        reason=value.get("reason"),
        metadata=merged_meta,
    )


def _rule(value: SourceUpdateRule | Mapping[str, Any]) -> SourceUpdateRule:
    if isinstance(value, SourceUpdateRule):
        return value
    return SourceUpdateRule(
        connector_id=str(value["connector_id"]),
        dataset=str(value["dataset"]),
        eligible_tiers=value.get("eligible_tiers") or (),
        cadence=str(value.get("cadence") or "daily"),
        market=str(value.get("market") or "TW"),
        priority=int(value.get("priority") or 100),
        max_symbols_per_run=value.get("max_symbols_per_run"),
        reason=str(value.get("reason") or ""),
        metadata=dict(value.get("metadata") or {}),
    )


def _unique_symbols(symbols: Sequence[str] | Any) -> list[str]:
    result: list[str] = []
    for symbol in symbols:
        text = str(symbol or "").strip().upper()
        if text and text not in result:
            result.append(text)
    return result


_TW_UNDERLYING_VENUE_SUFFIXES = {"TW", "TWSE", "TSE", "TWO", "TPEX", "OTC"}

# Governed active Taiwan paper personas (PAPER-PERSONA-BASELINES-20261105 /
# PAPER-DRAWDOWN-CURRENT0040-CONFIG-20261021 in threshold_sweep_baselines.json).
GOVERNED_ACTIVE_TW_PAPER_PERSONAS: tuple[dict[str, str], ...] = (
    {
        "persona_id": "persona-be19a1fac471247f2b2a",
        "artifact_id": "artifact-persona-paper-298e138c136c9840b0e4",
        "binding_id": "rb-a1c3ada3d5894d57bc2e012f337753be",
        "symbol": "2330.TWSE",
        "market": "TW",
        "mode": "paper",
        "status": "active",
    },
    {
        "persona_id": "persona-9d4fb3f44cbc5e7cb84f",
        "artifact_id": "artifact-persona-paper-01390a4b72713295b001",
        "binding_id": "rb-fd733d7c192f45898bbe0e90c8555878",
        "symbol": "0050.TWSE",
        "market": "TW",
        "mode": "paper",
        "status": "active",
    },
    {
        "persona_id": "persona-d9363cffc0efbbbcbeb9",
        "artifact_id": "artifact-persona-paper-8ae996980eeccb80d928",
        "binding_id": "rb-eda9b9a3379d4fb5a09f0e8432c84f20",
        "symbol": "2317.TWSE",
        "market": "TW",
        "mode": "paper",
        "status": "active",
    },
    {
        "persona_id": "persona-current0040",
        "artifact_id": "artifact-persona-paper-00400aa279b49774cde4",
        "binding_id": "rb-2eebe2b7",
        "symbol": "2330.TW",
        "market": "TW",
        "mode": "paper",
        "status": "active",
    },
)

_PAPER_MODE_TOKENS = {
    "paper",
    "paper_active",
    "active_paper",
    "paper_persona",
    "active_paper_persona",
    "paper_trading",
    "sim",
    "simulation",
}
_NON_PAPER_MODE_TOKENS = {
    "research",
    "research_only",
    "backtest",
    "backtest_only",
    "live",
    "live_only",
    "production",
    "prod",
    "real",
    "canary",
    "shadow",
    "holding",
    "watchlist",
    "observation",
}
_INACTIVE_STATUS_TOKENS = {
    "inactive",
    "paused",
    "retired",
    "archived",
    "disabled",
    "stopped",
    "suspended",
    "draft",
    "pending",
    "superseded",
    "terminated",
    "closed",
    "halted",
    "deactivated",
    "rejected",
    "removed",
}
_ACTIVE_STATUS_TOKENS = {
    "active",
    "enabled",
    "running",
    "ready",
    "paper_active",
    "active_paper",
    "active_paper_persona",
}
_MODE_KEYS = (
    "mode",
    "execution_mode",
    "deployment_mode",
    "observed_deployment_mode",
    "trading_mode",
    "persona_mode",
    "runtime_mode",
    "stage",
    "deployment_stage",
    "lifecycle_stage",
    "environment",
    "env",
    "scope",
    "usage",
    "role",
    "target_mode",
    "expression_mode",
)
_STATUS_KEYS = (
    "status",
    "state",
    "lifecycle_state",
    "persona_status",
    "persona_state",
    "binding_status",
    "observed_binding_status",
    "binding_state",
    "runtime_status",
    "activation_status",
    "artifact_state",
)
_NESTED_PERSONA_KEYS = (
    "persona",
    "personas",
    "active_personas",
    "paper_personas",
    "active_paper_personas",
    "binding",
    "bindings",
    "runtime_bindings",
    "persona_bindings",
)


def _norm_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"[\s\-]+", "_", text)


def _reason_tokens(reason: Any) -> set[str]:
    text = str(reason or "").strip().lower()
    if not text:
        return set()
    normalized = _norm_token(text)
    parts = {
        _norm_token(part)
        for part in re.split(r"[,;/|\s]+", text)
        if _norm_token(part)
    }
    parts.add(normalized)
    return parts


def _mapping_is_inactive(data: Mapping[str, Any]) -> bool:
    for false_key in ("active", "is_active", "enabled", "paper_active", "active_paper_persona", "is_active_paper_persona"):
        if data.get(false_key) is False:
            return True
    for true_key in ("inactive", "paused", "retired", "archived", "disabled"):
        if data.get(true_key) is True:
            return True
    for key in _STATUS_KEYS:
        val = data.get(key)
        if val is None or isinstance(val, (Mapping, list, tuple, set)):
            continue
        tok = _norm_token(val)
        if tok in _INACTIVE_STATUS_TOKENS:
            return True
    return False


def _mapping_is_non_paper(data: Mapping[str, Any]) -> bool:
    for true_key in ("research_only", "is_research_only", "research", "live", "live_only", "is_live"):
        if data.get(true_key) is True:
            return True
    for false_key in ("paper", "paper_active", "active_paper_persona", "is_active_paper_persona", "paper_enabled"):
        if data.get(false_key) is False:
            return True
    for key in _MODE_KEYS:
        val = data.get(key)
        if val is None or isinstance(val, (Mapping, list, tuple, set)):
            continue
        tok = _norm_token(val)
        if tok in _NON_PAPER_MODE_TOKENS:
            return True
    for list_key in ("modes", "allowed_modes", "execution_modes", "stages"):
        seq = data.get(list_key)
        if isinstance(seq, (list, tuple, set)) and not isinstance(seq, (str, bytes)):
            tokens = {_norm_token(item) for item in seq if _norm_token(item)}
            if tokens and tokens.isdisjoint(_PAPER_MODE_TOKENS):
                return True
    return False


def _mapping_is_positive_paper(data: Mapping[str, Any], *, default_paper: bool = False) -> bool:
    if _mapping_is_inactive(data) or _mapping_is_non_paper(data):
        return False
    market = str(data.get("market") or "").strip().upper()
    if market and market != "TW":
        return False
    for true_key in ("paper_active", "active_paper_persona", "is_active_paper_persona", "paper_persona", "paper_enabled", "paper"):
        if data.get(true_key) is True:
            return True
    for key in (*_MODE_KEYS, *_STATUS_KEYS):
        val = data.get(key)
        if val is None or isinstance(val, (Mapping, list, tuple, set)):
            continue
        if _norm_token(val) in _PAPER_MODE_TOKENS:
            return True
    for list_key in ("modes", "allowed_modes", "execution_modes", "stages"):
        seq = data.get(list_key)
        if isinstance(seq, (list, tuple, set)) and not isinstance(seq, (str, bytes)):
            if any(_norm_token(item) in _PAPER_MODE_TOKENS for item in seq):
                return True
    return default_paper


def _extract_symbols_from_persona_entry(entry: Any) -> list[str]:
    if isinstance(entry, str):
        code = _normalize_tw_underlying_code(entry)
        return [code] if code else []
    if not isinstance(entry, Mapping):
        return []
    nested_intent = entry.get("binding_intent")
    check_obj = dict(entry)
    if isinstance(nested_intent, Mapping):
        for k, v in nested_intent.items():
            check_obj.setdefault(k, v)
    if not _mapping_is_positive_paper(check_obj):
        return []
    raw_symbols: list[Any] = []
    for single_key in ("symbol", "underlying", "underlying_code", "underlying_id", "security_id"):
        if check_obj.get(single_key):
            raw_symbols.append(check_obj[single_key])
    for multi_key in ("symbols", "underlyings", "underlying_codes"):
        seq = check_obj.get(multi_key)
        if isinstance(seq, (list, tuple, set)) and not isinstance(seq, (str, bytes)):
            raw_symbols.extend(seq)
    for nested_key in ("parameters", "market_scope", "strategy_artifact"):
        nested = check_obj.get(nested_key)
        if isinstance(nested, Mapping):
            if nested.get("symbol"):
                raw_symbols.append(nested["symbol"])
            seq = nested.get("symbols")
            if isinstance(seq, (list, tuple, set)) and not isinstance(seq, (str, bytes)):
                raw_symbols.extend(seq)
            inner_params = nested.get("parameters")
            if isinstance(inner_params, Mapping) and isinstance(inner_params.get("symbols"), (list, tuple, set)):
                raw_symbols.extend(inner_params["symbols"])
    return _unique_underlying_codes(raw_symbols)


def _member_nested_persona_state(metadata: Mapping[str, Any]) -> tuple[bool, bool]:
    """Return (has_nested_spec, has_active_paper_nested)."""
    has_spec = False
    has_active_paper = False
    for key in _NESTED_PERSONA_KEYS:
        if key not in metadata:
            continue
        has_spec = True
        val = metadata.get(key)
        default_paper = key in {"paper_personas", "active_paper_personas"}
        if isinstance(val, Mapping):
            if _mapping_is_positive_paper(val, default_paper=default_paper):
                has_active_paper = True
        elif isinstance(val, (list, tuple, set)) and not isinstance(val, (str, bytes)):
            for item in val:
                if isinstance(item, Mapping):
                    if _mapping_is_positive_paper(item, default_paper=default_paper):
                        has_active_paper = True
                elif isinstance(item, str) and item.strip():
                    if default_paper or _norm_token(item) in _PAPER_MODE_TOKENS:
                        has_active_paper = True
        elif isinstance(val, str) and val.strip():
            if default_paper or _norm_token(val) in _PAPER_MODE_TOKENS:
                has_active_paper = True
    return has_spec, has_active_paper


def _is_excluded_member(member: ActiveUniverseMember) -> bool:
    if member.market != "TW":
        return True
    r_tokens = _reason_tokens(member.reason)
    if r_tokens & (_NON_PAPER_MODE_TOKENS | _INACTIVE_STATUS_TOKENS):
        return True
    meta = member.metadata or {}
    if _mapping_is_inactive(meta) or _mapping_is_non_paper(meta):
        return True
    has_nested, has_active_paper_nested = _member_nested_persona_state(meta)
    if has_nested and not has_active_paper_nested:
        return True
    return False


def _is_explicit_active_paper_member(member: ActiveUniverseMember) -> bool:
    if _is_excluded_member(member):
        return False
    r_tokens = _reason_tokens(member.reason)
    if r_tokens & _PAPER_MODE_TOKENS:
        return True
    meta = member.metadata or {}
    if _mapping_is_positive_paper(meta):
        return True
    _has_nested, has_active_paper_nested = _member_nested_persona_state(meta)
    return has_active_paper_nested


def resolve_active_tw_paper_persona_underlyings(
    members: Sequence[ActiveUniverseMember | Mapping[str, Any]],
    *,
    personas: Sequence[Mapping[str, Any] | str] | None = None,
    runtime_bindings: Sequence[Mapping[str, Any]] | None = None,
    active_paper_symbols: Sequence[str] | None = None,
    rule_metadata: Mapping[str, Any] | None = None,
) -> list[str]:
    """Resolve normalized TW underlying codes for active Taiwan paper personas.

    Excludes ``research``/``research_only``, ``live``, and ``inactive``/``paused``/
    ``retired`` members or persona bindings while preserving governed active paper
    persona underlyings.
    """
    normalized_members = tuple(_member(m) for m in members)
    eligible_core = [
        m
        for m in normalized_members
        if m.market == "TW" and m.tier == UniverseTier.CORE and not _is_excluded_member(m)
    ]
    if not eligible_core:
        return []

    rule_meta = dict(rule_metadata or {})
    explicit_governed_codes: list[str] = []
    has_explicit_governed_input = False

    for symbol_seq in (
        active_paper_symbols,
        rule_meta.get("active_paper_symbols"),
        rule_meta.get("active_paper_underlyings"),
        rule_meta.get("paper_persona_underlyings"),
    ):
        if symbol_seq is not None:
            has_explicit_governed_input = True
            if isinstance(symbol_seq, str):
                explicit_governed_codes.extend(
                    _unique_underlying_codes(part for part in re.split(r"[,;\s]+", symbol_seq) if part)
                )
            elif isinstance(symbol_seq, (list, tuple, set)):
                explicit_governed_codes.extend(_unique_underlying_codes(symbol_seq))

    env_symbols = os.getenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "").strip()
    if env_symbols:
        has_explicit_governed_input = True
        explicit_governed_codes.extend(
            _unique_underlying_codes(part for part in re.split(r"[,;\s]+", env_symbols) if part)
        )

    for persona_seq in (
        personas,
        runtime_bindings,
        rule_meta.get("personas"),
        rule_meta.get("active_paper_personas"),
        rule_meta.get("runtime_bindings"),
        rule_meta.get("persona_bindings"),
    ):
        if persona_seq is not None:
            has_explicit_governed_input = True
            if isinstance(persona_seq, Mapping):
                explicit_governed_codes.extend(_extract_symbols_from_persona_entry(persona_seq))
            elif isinstance(persona_seq, (list, tuple, set)):
                for entry in persona_seq:
                    explicit_governed_codes.extend(_extract_symbols_from_persona_entry(entry))

    if has_explicit_governed_input:
        allowed_codes = set(_unique_underlying_codes(explicit_governed_codes))
        return _unique_underlying_codes(
            m.symbol for m in eligible_core if _normalize_tw_underlying_code(m.symbol) in allowed_codes
        )

    explicit_paper_members = [m for m in eligible_core if _is_explicit_active_paper_member(m)]
    if explicit_paper_members:
        return _unique_underlying_codes(m.symbol for m in explicit_paper_members)

    default_governed_codes = set(
        _unique_underlying_codes(
            code
            for entry in GOVERNED_ACTIVE_TW_PAPER_PERSONAS
            for code in _extract_symbols_from_persona_entry(entry)
        )
    )
    return _unique_underlying_codes(
        m.symbol for m in eligible_core if _normalize_tw_underlying_code(m.symbol) in default_governed_codes
    )


def _normalize_tw_underlying_code(symbol: Any) -> str:
    text = str(symbol or "").strip().upper()
    if text.startswith("EQ:"):
        text = text[3:].strip()
    if "." in text:
        base, suffix = text.rsplit(".", 1)
        if suffix in _TW_UNDERLYING_VENUE_SUFFIXES and base:
            return base
    return text


def _unique_underlying_codes(symbols: Sequence[str] | Any) -> list[str]:
    return _unique_symbols(_normalize_tw_underlying_code(symbol) for symbol in symbols)
