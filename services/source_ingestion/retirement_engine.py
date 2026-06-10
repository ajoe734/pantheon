"""Retirement recommendation engine for data and strategy seed sources.

Usage telemetry drives low-value source retirement proposals. No source is
retired directly; the engine produces draft recommendation candidates that
can be turned into governed SourceChangeProposal records.

Rules (DATA_STRATEGY_SOURCE_SYSTEM_DESIGN.md §8.2):
  - high failure + low usage + no dependencies      → propose_disable
  - high cost + low yield + has_replacement          → propose_replace
  - high strategy seed yield                         → keep_increase_schedule
  - stale/failing with critical dependency           → alert_backfill
  - disabled + observation window elapsed + no deps → propose_retire
  - no usage records yet                             → keep_probation
  - otherwise                                        → no_action

Critical invariant: active_strategy_dependency_count > 0 always blocks
propose_disable, propose_replace, and propose_retire.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Sequence

from .source_health import SourceHealth, SourceHealthStatus


_OBSERVATION_WINDOW_SECONDS = 30 * 86400
_PROBATION_WINDOW_SECONDS = 7 * 86400


class RecommendationType(str, Enum):
    PROPOSE_DISABLE = "propose_disable"
    PROPOSE_REPLACE = "propose_replace"
    PROPOSE_RETIRE = "propose_retire"
    KEEP_INCREASE_SCHEDULE = "keep_increase_schedule"
    ALERT_BACKFILL = "alert_backfill"
    KEEP_PROBATION = "keep_probation"
    NO_ACTION = "no_action"


@dataclass(frozen=True)
class RetirementRecommendation:
    source_id: str
    source_kind: str
    recommendation: RecommendationType
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    retirement_blocked: bool = False
    retirement_blocker_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "recommendation": self.recommendation.value,
            "reason": self.reason,
            "evidence": dict(self.evidence),
            "retirement_blocked": self.retirement_blocked,
            "retirement_blocker_reason": self.retirement_blocker_reason,
        }


def compute_recommendation(
    health: SourceHealth,
    usage_window: dict[str, Any],
    *,
    has_replacement: bool = False,
    low_usage_threshold: int = 5,
    high_failure_threshold: float = 0.5,
    high_cost_threshold_30d: float = 1000.0,
    low_yield_threshold: int = 1,
    observation_window_seconds: int = _OBSERVATION_WINDOW_SECONDS,
) -> RetirementRecommendation:
    """Compute one retirement recommendation for a single source.

    ``usage_window`` is the dict returned by
    ``SourceUsageDailyStore.aggregate_for_source``.
    Pure and deterministic: never mutates state.
    """
    source_id = health.source_id
    source_kind = health.source_kind

    total_usage = (
        int(usage_window.get("total_query_count") or 0)
        + int(usage_window.get("total_search_hit_count") or 0)
        + int(usage_window.get("total_persona_match_count") or 0)
        + int(usage_window.get("total_strategy_seed_yield_count") or 0)
        + int(usage_window.get("total_strategy_promotion_count") or 0)
    )
    seed_yield = int(usage_window.get("total_strategy_seed_yield_count") or 0)
    active_deps = int(usage_window.get("max_active_strategy_dependency_count") or 0)
    record_count = int(usage_window.get("record_count") or 0)
    error_rate = float(health.error_rate_7d or 0.0)
    cost_30d: float | None = (
        float(health.cost_estimate_30d) if health.cost_estimate_30d is not None else None
    )

    status_val = health.status if isinstance(health.status, str) else health.status.value
    is_disabled = status_val == SourceHealthStatus.DISABLED.value
    is_retired = status_val == SourceHealthStatus.RETIRED.value
    has_active_deps = active_deps > 0

    evidence: dict[str, Any] = {
        "error_rate_7d": error_rate,
        "total_usage_window": total_usage,
        "seed_yield_window": seed_yield,
        "active_strategy_dependencies": active_deps,
        "cost_estimate_30d": cost_30d,
        "record_count_in_window": record_count,
        "status": status_val,
        "staleness_seconds": health.staleness_seconds,
    }

    if is_retired:
        return RetirementRecommendation(
            source_id=source_id,
            source_kind=source_kind,
            recommendation=RecommendationType.NO_ACTION,
            reason="Source is already retired.",
            evidence=evidence,
        )

    # Critical active dependency blocks retirement and disable.
    if has_active_deps:
        if is_disabled or (
            health.staleness_seconds
            and health.staleness_seconds > 0
            and error_rate >= high_failure_threshold
        ):
            return RetirementRecommendation(
                source_id=source_id,
                source_kind=source_kind,
                recommendation=RecommendationType.ALERT_BACKFILL,
                reason=(
                    f"Source has {active_deps} active strategy dependenc"
                    f"{'y' if active_deps == 1 else 'ies'} but is "
                    f"{'disabled' if is_disabled else 'stale/failing'}. "
                    "Backfill required; retirement is blocked."
                ),
                evidence=evidence,
                retirement_blocked=True,
                retirement_blocker_reason=f"active_strategy_dependency_count={active_deps}",
            )

    # Disabled source: enforce observation window before retire.
    if is_disabled and not has_active_deps:
        staleness = health.staleness_seconds or 0
        if staleness >= observation_window_seconds:
            days_disabled = staleness // 86400
            return RetirementRecommendation(
                source_id=source_id,
                source_kind=source_kind,
                recommendation=RecommendationType.PROPOSE_RETIRE,
                reason=(
                    f"Source has been disabled for {days_disabled} days "
                    f"(≥ {observation_window_seconds // 86400}-day observation window) "
                    "with no active strategy dependencies. Ready to retire."
                ),
                evidence=evidence,
            )
        remaining = max(0, (observation_window_seconds - staleness) // 86400)
        return RetirementRecommendation(
            source_id=source_id,
            source_kind=source_kind,
            recommendation=RecommendationType.NO_ACTION,
            reason=(
                f"Source is disabled; {remaining} days remain in the "
                f"{observation_window_seconds // 86400}-day observation window."
            ),
            evidence=evidence,
        )

    # No usage records: probation window.
    if record_count == 0:
        return RetirementRecommendation(
            source_id=source_id,
            source_kind=source_kind,
            recommendation=RecommendationType.KEEP_PROBATION,
            reason="No usage records available. Source is in probation; do not retire.",
            evidence=evidence,
        )

    # High strategy seed yield → keep and consider schedule increase.
    if source_kind == "strategy_seed_source" and seed_yield >= low_yield_threshold:
        return RetirementRecommendation(
            source_id=source_id,
            source_kind=source_kind,
            recommendation=RecommendationType.KEEP_INCREASE_SCHEDULE,
            reason=(
                f"High strategy seed yield ({seed_yield} over window). "
                "Keep this source and consider increasing schedule frequency."
            ),
            evidence=evidence,
        )

    is_high_failure = error_rate >= high_failure_threshold
    is_low_usage = total_usage < low_usage_threshold
    is_high_cost = cost_30d is not None and cost_30d >= high_cost_threshold_30d
    is_low_yield = seed_yield < low_yield_threshold

    # High failure + low usage → propose disable (or alert if blocked by deps).
    if is_high_failure and is_low_usage:
        if has_active_deps:
            return RetirementRecommendation(
                source_id=source_id,
                source_kind=source_kind,
                recommendation=RecommendationType.ALERT_BACKFILL,
                reason=(
                    f"High failure rate ({error_rate:.0%}) with low usage but "
                    f"{active_deps} active strategy dependenc"
                    f"{'y' if active_deps == 1 else 'ies'} blocks disable. "
                    "Alert: resolve dependency before disabling."
                ),
                evidence=evidence,
                retirement_blocked=True,
                retirement_blocker_reason=f"active_strategy_dependency_count={active_deps}",
            )
        return RetirementRecommendation(
            source_id=source_id,
            source_kind=source_kind,
            recommendation=RecommendationType.PROPOSE_DISABLE,
            reason=(
                f"High failure rate ({error_rate:.0%}) with low usage "
                f"({total_usage} events over window) and no active strategy "
                "dependencies. Propose disabling."
            ),
            evidence=evidence,
        )

    # High cost + low yield + replacement available → propose replace.
    if is_high_cost and is_low_yield and has_replacement and not has_active_deps:
        return RetirementRecommendation(
            source_id=source_id,
            source_kind=source_kind,
            recommendation=RecommendationType.PROPOSE_REPLACE,
            reason=(
                f"High cost (${cost_30d:.0f}/30d) with low strategy yield "
                "and a replacement source is available. Propose replacing."
            ),
            evidence=evidence,
        )

    return RetirementRecommendation(
        source_id=source_id,
        source_kind=source_kind,
        recommendation=RecommendationType.NO_ACTION,
        reason="No retirement action recommended based on current health and usage.",
        evidence=evidence,
    )


def compute_recommendations(
    health_records: Sequence[SourceHealth],
    aggregate_fn: Callable[[str], dict[str, Any]],
    *,
    replacement_map: dict[str, bool] | None = None,
    **threshold_kwargs: Any,
) -> list[RetirementRecommendation]:
    """Compute recommendations for a list of sources.

    ``aggregate_fn`` is called as ``fn(source_id)`` → usage-window dict
    (same shape as ``SourceUsageDailyStore.aggregate_for_source``).
    ``replacement_map`` maps source_id → has_replacement (default False).
    """
    results: list[RetirementRecommendation] = []
    for health in health_records:
        usage = aggregate_fn(health.source_id)
        has_replacement = bool((replacement_map or {}).get(health.source_id, False))
        rec = compute_recommendation(
            health,
            usage,
            has_replacement=has_replacement,
            **threshold_kwargs,
        )
        results.append(rec)
    return results
