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
        connector_id="tw-yahoo-broker-top15",
        dataset="tw_broker_top",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE),
        cadence="daily_after_close",
        priority=10,
        reason="low-cost broker top15/top20 substitute for active research symbols",
        metadata={"detail_level": "top15_buy_sell_only", "archive_behavior": "skip"},
    ),
    SourceUpdateRule(
        connector_id="tw-yahoo-stock-rss",
        dataset="tw_news_metadata",
        eligible_tiers=(UniverseTier.CORE, UniverseTier.CANDIDATE),
        cadence="10m_to_30m",
        priority=20,
        reason="cheap news metadata for symbols still under research",
        metadata={"detail_level": "rss_metadata", "archive_behavior": "skip"},
    ),
)


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
