"""Stage-aware persona ranking and real-capital allocation policy."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List


_SCORE_WEIGHTS = {
    "pnl_score": 0.25,
    "sharpe_score": 0.20,
    "drawdown_control_score": 0.15,
    "execution_quality_score": 0.15,
    "risk_compliance_score": 0.15,
    "improvement_score": 0.05,
    "human_intervention_penalty": -0.05,
}
_POSITIVE_ALLOCATION_STAGES = {"canary_running", "live_running"}
_ZERO_CAP_TIERS = {"watch", "suspended", "retired"}
_EXCLUSION_FLAGS = (
    "unresolved_severe_incident",
    "hard_risk_breach",
    "missing_required_evidence",
    "reconciliation_anomaly",
    "binding_mismatch",
    "sample_below_minimum",
    "human_review_blocked",
)


def stage_recommendation(stage: str, *, hard_risk_breach: bool = False) -> str:
    if hard_risk_breach:
        return "containment"
    return {
        "paper_running": "paper_to_canary_review",
        "canary_running": "canary_to_live_review",
        "live_running": "allocation_increase_or_retain_review",
    }.get(stage, "no_positive_action")


def _rank_score(row: Dict[str, Any]) -> float:
    score = sum(float(row.get(key) or 0.0) * weight for key, weight in _SCORE_WEIGHTS.items())
    return score - float(row.get("hard_penalty") or 0.0)


def _tier_cap(row: Dict[str, Any]) -> tuple[float, str | None]:
    stage = str(row.get("stage") or "")
    tier = str(row.get("tier") or "").lower()
    if tier in _ZERO_CAP_TIERS or stage in {"frozen", "suspended", "retired"}:
        return 0.0, "ineligible_stage_or_tier"
    if stage == "canary_running":
        override = row.get("risk_owner_cap")
        return min(0.05, float(override)) if override is not None else 0.05, "canary_cap"
    cap = {"s": 0.25, "a": 0.15, "b": 0.08}.get(tier, 0.0)
    return cap, f"live_{tier or 'unrated'}_tier_cap"


def calculate_target_allocations(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return auditable target lines; never mutates a binding or capital store."""
    prepared: List[Dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        stage = str(row.get("stage") or "")
        exclusions = [flag for flag in _EXCLUSION_FLAGS if bool(row.get(flag))]
        if stage not in _POSITIVE_ALLOCATION_STAGES:
            exclusions.append("stage_not_real_allocation_eligible")
        rank_score = _rank_score(row)
        adjusted = max(rank_score, 0.0)
        for factor in ("capacity_factor", "risk_budget_factor", "evidence_confidence_factor"):
            adjusted *= float(row.get(factor, 1.0))
        if exclusions:
            adjusted = 0.0
        prepared.append({**row, "rank_score": rank_score, "capacity_adjusted_score": adjusted, "exclusions": exclusions})

    denominator = sum(row["capacity_adjusted_score"] for row in prepared)
    result: List[Dict[str, Any]] = []
    for row in prepared:
        current = max(0.0, float(row.get("current_weight") or 0.0))
        raw_target = row["capacity_adjusted_score"] / denominator if denominator else 0.0
        cap, tier_reason = _tier_cap(row)
        target = min(raw_target, cap)
        cap_reasons: List[str] = []
        if row["exclusions"]:
            target = min(target, current)  # exclusion may reduce/retain, never increase
            cap_reasons.extend(row["exclusions"])
        if raw_target > cap:
            cap_reasons.append(tier_reason or "stage_tier_cap")
        increase_cap = current * 1.25
        if current > 0 and target > increase_cap:
            target = increase_cap
            cap_reasons.append("quarterly_increase_cap_25pct")
        target = round(target, 8)
        current = round(current, 8)
        result.append({
            "persona_id": row.get("persona_id"),
            "stage": row.get("stage"),
            "capital_scope": row.get("capital_scope") or "real",
            "capital_pool_id": row.get("capital_pool_id"),
            "capital_sleeve_id": row.get("capital_sleeve_id"),
            "current_weight": current,
            "target_weight": target,
            "delta": round(target - current, 8),
            "rank_score": round(row["rank_score"], 8),
            "capacity_adjusted_score": round(row["capacity_adjusted_score"], 8),
            "recommendation": stage_recommendation(str(row.get("stage") or ""), hard_risk_breach=bool(row.get("hard_risk_breach"))),
            "cap_reasons": cap_reasons,
            "exclusions": row["exclusions"],
            "evidence_refs": list(row.get("evidence_refs") or []),
            "requires_human_approval": target > current,
        })
    return result


def validate_emergency_lines(lines: Iterable[Dict[str, Any]]) -> None:
    for line in lines:
        if float(line.get("target_weight") or 0.0) > float(line.get("current_weight") or 0.0):
            raise ValueError("emergency containment cannot increase allocation")
        if str(line.get("recommendation") or "") in {"paper_to_canary_review", "canary_to_live_review"}:
            raise ValueError("emergency containment cannot promote a persona")
