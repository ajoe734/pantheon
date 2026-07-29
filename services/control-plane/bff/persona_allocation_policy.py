"""Stage-aware persona ranking and real-capital allocation policy."""
from __future__ import annotations

import math
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
_PM12_FORMULA_VERSION = "pm12-default-v1"
_PM12_ALLOCATION_INPUT_SCHEMA_VERSION = "persona-allocation-policy-input/v1"
_PM12_ALLOCATION_ADAPTER_VERSION = "pm12-quarterly-overall-tier-v1"
_ALLOCATION_POLICY_VERSION = "persona-real-allocation-v1"
_PAPER_SIMULATION_POLICY_VERSION = "persona-paper-allocation-simulation-v1"
_PM12_TIER_CROSSWALK = {
    "tier-1": "s",
    "tier-2": "a",
    "tier-3": "b",
    "tier-4": "watch",
}


def stage_recommendation(stage: str, *, hard_risk_breach: bool = False) -> str:
    if hard_risk_breach:
        return "containment"
    return {
        "paper_running": "paper_to_canary_review",
        "canary_running": "canary_to_live_review",
        "live_running": "allocation_increase_or_retain_review",
    }.get(stage, "no_positive_action")


def build_pm12_allocation_policy_input(
    row: Dict[str, Any],
    *,
    policy_version: str = _ALLOCATION_POLICY_VERSION,
) -> Dict[str, Any]:
    """Adapt one PM-12 row into the governed allocation-policy input schema.

    PM-12 publishes one composite ``overall_score`` and tier-1..4.  The
    allocation policy historically consumed seven unrelated top-level score
    fields plus S/A/B.  This adapter makes that semantic boundary explicit
    without fabricating missing Sharpe, drawdown-control, or improvement
    components.
    """
    formula_version = str(row.get("formula_version") or "").strip()
    if not formula_version and ("overall_score" in row or "score" in row):
        formula_version = _PM12_FORMULA_VERSION
    if formula_version != _PM12_FORMULA_VERSION:
        raise ValueError(
            f"unsupported PM-12 formula_version: {formula_version or 'missing'}"
        )
    source_tier = str(row.get("tier") or row.get("tier_id") or "").strip().lower()
    if source_tier in {"s", "a", "b", "watch"}:
        allocation_tier = source_tier
    else:
        allocation_tier = _PM12_TIER_CROSSWALK.get(source_tier)
    if allocation_tier is None:
        raise ValueError(f"unsupported PM-12 tier: {source_tier or 'missing'}")
    raw_score = row.get("overall_score")
    if raw_score is None:
        raw_score = row.get("score")
    if isinstance(raw_score, bool):
        raise ValueError("PM-12 overall_score must be a finite number")
    try:
        rank_score = float(raw_score)
    except (TypeError, ValueError) as exc:
        raise ValueError("PM-12 overall_score must be a finite number") from exc
    if not math.isfinite(rank_score) or not 0.0 <= rank_score <= 100.0:
        raise ValueError("PM-12 overall_score must be between 0 and 100")

    expected = {
        "schema_version": _PM12_ALLOCATION_INPUT_SCHEMA_VERSION,
        "adapter_version": _PM12_ALLOCATION_ADAPTER_VERSION,
        "policy_version": policy_version,
        "source_formula_version": formula_version,
        "rank_score_source": "overall_score",
        "rank_score": rank_score,
        "source_tier": source_tier,
        "allocation_tier": allocation_tier,
    }
    supplied = row.get("allocation_policy_input")
    if supplied is not None:
        if not isinstance(supplied, dict):
            raise ValueError("allocation_policy_input must be an object")
        for key, value in expected.items():
            if supplied.get(key) != value:
                raise ValueError(
                    f"allocation_policy_input.{key} does not match the PM-12 row"
                )
    return expected


def _allocation_policy_input(row: Dict[str, Any]) -> Dict[str, Any] | None:
    tier = str(row.get("tier") or row.get("tier_id") or "").strip().lower()
    formula_version = str(row.get("formula_version") or "").strip()
    if (
        "allocation_policy_input" in row
        or tier.startswith("tier-")
        or formula_version == _PM12_FORMULA_VERSION
        or "overall_score" in row
        or "score" in row
    ):
        return build_pm12_allocation_policy_input(row)
    return None


def _rank_score(row: Dict[str, Any]) -> float:
    policy_input = row.get("allocation_policy_input")
    if isinstance(policy_input, dict):
        return float(policy_input["rank_score"])
    score = sum(float(row.get(key) or 0.0) * weight for key, weight in _SCORE_WEIGHTS.items())
    return score - float(row.get("hard_penalty") or 0.0)


def _tier_cap(row: Dict[str, Any]) -> tuple[float, str | None]:
    stage = str(row.get("stage") or "")
    policy_input = row.get("allocation_policy_input")
    tier = str(
        policy_input.get("allocation_tier")
        if isinstance(policy_input, dict)
        else row.get("tier") or ""
    ).lower()
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
        policy_input = _allocation_policy_input(row)
        if policy_input is not None:
            row["allocation_policy_input"] = policy_input
        stage = str(row.get("stage") or "")
        exclusions = [flag for flag in _EXCLUSION_FLAGS if bool(row.get(flag))]
        exclusions.extend(
            str(value)
            for value in row.get("exclusion_codes") or []
            if str(value) and str(value) not in exclusions
        )
        if row.get("eligible") is False and not exclusions:
            exclusions.append("ranking_ineligible")
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
        source_current = row.get("current_weight")
        current = max(0.0, float(source_current or 0.0))
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
        projected_current = current if source_current is not None else None
        result.append({
            "ranking_snapshot_id": row.get("ranking_snapshot_id"),
            "persona_id": row.get("persona_id"),
            "stage": row.get("stage"),
            "capital_scope": row.get("capital_scope") or "real",
            "capital_pool_id": row.get("capital_pool_id"),
            "capital_sleeve_id": row.get("capital_sleeve_id"),
            "current_weight": projected_current,
            "target_weight": target,
            "delta": round(target - current, 8),
            "rank_score": round(row["rank_score"], 8),
            "capacity_adjusted_score": round(row["capacity_adjusted_score"], 8),
            "allocation_policy_input": row.get("allocation_policy_input"),
            "recommendation": stage_recommendation(str(row.get("stage") or ""), hard_risk_breach=bool(row.get("hard_risk_breach"))),
            "cap_reasons": cap_reasons,
            "exclusions": row["exclusions"],
            "evidence_refs": list(row.get("evidence_refs") or []),
            "eligible": row.get("eligible"),
            "exclusion_reasons": list(row.get("exclusion_reasons") or []),
            "exclusion_codes": list(row.get("exclusion_codes") or []),
            "requires_human_approval": target > current,
        })
    return result


def calculate_paper_simulation_allocations(
    rows: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return positive targets inside isolated paper ledgers only.

    This is deliberately separate from ``calculate_target_allocations``:
    paper-stage rows remain ineligible for real capital and the real allocation
    policy is never parameterized into admitting them.  Callers must already
    have joined each authoritative ranking row to its governed promotion review,
    internal paper pool, and unique paper-owner binding.
    """

    prepared: List[Dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        stage = str(row.get("stage") or "").strip().lower()
        capital_scope = str(row.get("capital_scope") or "").strip().lower()
        if stage != "paper_running":
            raise ValueError("paper simulation requires stage=paper_running")
        if capital_scope != "paper_ledger":
            raise ValueError("paper simulation requires capital_scope=paper_ledger")
        if not str(row.get("paper_ledger_id") or "").strip():
            raise ValueError("paper simulation requires paper_ledger_id")
        if not str(row.get("capital_pool_id") or "").strip():
            raise ValueError("paper simulation requires an internal capital_pool_id")
        if not str(row.get("binding_id") or "").strip():
            raise ValueError("paper simulation requires a paper-owner binding_id")
        if row.get("capital_sleeve_id") not in (None, ""):
            raise ValueError("paper simulation cannot target a capital sleeve")
        if row.get("eligible") is not True:
            raise ValueError("paper simulation requires an eligible ranking row")

        exclusions = [
            flag for flag in _EXCLUSION_FLAGS if bool(row.get(flag))
        ]
        exclusions.extend(
            str(value)
            for value in row.get("exclusion_codes") or []
            if str(value) and str(value) not in exclusions
        )
        if exclusions:
            raise ValueError(
                "paper simulation ranking row has exclusion codes: "
                + ", ".join(exclusions)
            )

        # Ranking snapshots may carry the real-policy adapter projection.  The
        # paper evaluator owns a distinct policy version, so rebuild the adapter
        # from the authoritative PM-12 score/tier rather than reusing that claim.
        row.pop("allocation_policy_input", None)
        policy_input = build_pm12_allocation_policy_input(
            row,
            policy_version=_PAPER_SIMULATION_POLICY_VERSION,
        )
        rank_score = float(policy_input["rank_score"])
        if rank_score <= 0:
            raise ValueError("paper simulation requires a positive PM-12 rank score")
        current = float(row.get("current_weight") or 0.0)
        if not math.isfinite(current) or not 0.0 <= current <= 1.0:
            raise ValueError("paper simulation current_weight must be between 0 and 1")
        prepared.append(
            {
                **row,
                "allocation_policy_input": policy_input,
                "rank_score": rank_score,
                "current_weight": current,
            }
        )

    if len(prepared) != 1:
        raise ValueError(
            "paper simulation requires exactly one isolated Persona allocation row"
        )

    row = prepared[0]
    current = round(float(row["current_weight"]), 8)
    target = 1.0
    return [
        {
            "ranking_snapshot_id": row.get("ranking_snapshot_id"),
            "persona_id": row.get("persona_id"),
            "stage": "paper_running",
            "capital_scope": "paper_ledger",
            "paper_ledger_id": row.get("paper_ledger_id"),
            "capital_pool_id": row.get("capital_pool_id"),
            "capital_sleeve_id": None,
            "binding_id": row.get("binding_id"),
            "current_weight": current,
            "target_weight": target,
            "delta": round(target - current, 8),
            "rank_score": round(float(row["rank_score"]), 8),
            "capacity_adjusted_score": round(float(row["rank_score"]), 8),
            "allocation_policy_input": row.get("allocation_policy_input"),
            "recommendation": "governed_paper_allocation_simulation",
            "cap_reasons": ["paper_simulation_isolated_ledger_cap"],
            "exclusions": [],
            "evidence_refs": list(row.get("evidence_refs") or []),
            "eligible": True,
            "paper_allocation_eligible": True,
            "exclusion_reasons": [],
            "exclusion_codes": [],
            "requires_human_approval": True,
            "live_capital_side_effects": False,
        }
    ]


def validate_emergency_lines(lines: Iterable[Dict[str, Any]]) -> None:
    for line in lines:
        if float(line.get("target_weight") or 0.0) > float(line.get("current_weight") or 0.0):
            raise ValueError("emergency containment cannot increase allocation")
        if str(line.get("recommendation") or "") in {"paper_to_canary_review", "canary_to_live_review"}:
            raise ValueError("emergency containment cannot promote a persona")
