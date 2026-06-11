"""Active-universe planning for market-data ingestion.

The planner keeps high-volume connectors scoped to the symbols that still
matter. Core/candidate names get daily detail updates; archived names keep only
cheap baseline maintenance elsewhere.
"""

from __future__ import annotations

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
            "fallback_connector_id": "tw-yahoo-broker-top15",
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
        connector_id="tw-yahoo-stock-rss",
        dataset="tw_news_metadata",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE),
        cadence="10m_to_30m",
        priority=20,
        reason="cheap news metadata for symbols still under research",
        metadata={
            "detail_level": "rss_metadata",
            "archive_behavior": "skip",
            "full_text_allowed_by_default": False,
        },
    ),
    SourceUpdateRule(
        connector_id="tw-anue-news-rss",
        dataset="tw_news_metadata",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE),
        cadence="10m_to_30m",
        priority=21,
        reason="secondary public Taiwan finance news metadata for active research symbols",
        metadata={
            "detail_level": "rss_metadata",
            "archive_behavior": "skip",
            "full_text_allowed_by_default": False,
            "feed_url_configurable": True,
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
        connector_id="tw-yahoo-broker-top15",
        dataset="tw_broker_top",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE),
        cadence="daily_after_close_fallback",
        priority=90,
        reason="Yahoo Taiwan top15 broker page remains the public fallback when FinMind quota, entitlement, or endpoint health is unavailable",
        metadata={
            "detail_level": "top15_buy_sell_only",
            "fallback_for_connector_id": "tw-finmind-broker-daily-report",
            "archive_behavior": "skip",
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
        connector_id="us-stooq-daily-ohlcv",
        dataset="us_price_daily",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE, UniverseTier.ARCHIVE),
        cadence="daily_after_close",
        market="US",
        priority=95,
        max_symbols_per_run=200,
        reason="Stooq is the public US daily OHLCV fallback only after runtime endpoint verification",
        metadata={
            "detail_level": "daily_ohlcv_public_fallback",
            "source_role": "disabled_public_fallback_until_runtime_smoke",
            "storage_targets": ["raw/stooq", "normalized/us_price_daily", "features/us_returns"],
            "disabled_reason": "stooq_endpoint_unverified_2026-06-11",
            "archive_behavior": "daily_price_only_when_enabled",
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
        if rule.max_symbols_per_run is not None:
            symbols = symbols[: int(rule.max_symbols_per_run)]
        for member in normalized_members:
            if member.market == rule.market and member.tier == UniverseTier.ARCHIVE:
                skipped_archives.add(member.symbol)
        connector_updates.append(
            {
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
        )

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
) -> dict[str, Any]:
    if default_max_symbols_per_job < 1:
        raise ValueError("default_max_symbols_per_job must be >= 1")
    normalized_members = tuple(_member(member) for member in members)
    normalized_rules = tuple(_rule(rule) for rule in rules)
    plan = build_active_universe_update_plan(normalized_members, rules=normalized_rules)

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
        elif symbols:
            batch_size = int(rule.max_symbols_per_run or default_max_symbols_per_job)
            batches = [symbols[index : index + batch_size] for index in range(0, len(symbols), batch_size)]
        else:
            batches = []

        for batch_index, batch in enumerate(batches, start=1):
            jobs.append(
                {
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
            )

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


def _member(value: ActiveUniverseMember | Mapping[str, Any]) -> ActiveUniverseMember:
    if isinstance(value, ActiveUniverseMember):
        return value
    return ActiveUniverseMember(
        symbol=str(value["symbol"]),
        tier=value.get("tier", UniverseTier.CANDIDATE.value),
        market=str(value.get("market") or "TW"),
        venue=value.get("venue"),
        reason=value.get("reason"),
        metadata=dict(value.get("metadata") or {}),
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
