"""
Core evaluator: accepts artifact linkage + telemetry snapshot and produces EvaluatorResult.

The evaluator is advisory-only. Scores inform promotion decisions but do not bypass gates.
Callers supply pre-computed score components; this module validates, assembles, and returns
the governed EvaluatorResult artifact.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from .models import (
    EvaluationDataSnapshot,
    EvaluatorResult,
    Recommendation,
    ScoreComponent,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _compute_overall_score(components: Dict[str, ScoreComponent]) -> float:
    """Weighted sum of dimension scores."""
    return round(sum(c.weight * c.dimension_score for c in components.values()), 6)


def _derive_recommendation(overall_score: float, promotion_state: str) -> str:
    """Simple threshold-based recommendation derivation."""
    if promotion_state == "candidate":
        return Recommendation.CANDIDATE_TO_PAPER.value if overall_score >= 0.75 else Recommendation.CANDIDATE_HOLD.value
    if promotion_state == "paper":
        return Recommendation.PAPER_TO_LIVE.value if overall_score >= 0.80 else Recommendation.PAPER_HOLD.value
    return Recommendation.NEEDS_CRITIQUE.value


def _coalesce_artifact_field(
    artifact: Mapping[str, Any],
    *keys: str,
    default: Optional[Any] = None,
) -> Optional[Any]:
    for key in keys:
        value = artifact.get(key)
        if value is not None:
            return value
    return default


def _require_non_empty(value: Optional[str], field_name: str) -> str:
    if value is None or not str(value).strip():
        raise ValueError(f"{field_name} is required")
    return value


def evaluate(
    *,
    strategy_id: str,
    target_artifact_id: str,
    target_artifact_type: str,
    target_promotion_state: str,
    target_artifact_version: str,
    target_artifact_checksum: str,
    score_components: Dict[str, ScoreComponent],
    evaluator_id: str = "evaluator_primary_v1",
    evaluator_version: str = "1.0.0",
    registry_id: Optional[str] = None,
    evaluation_timestamp: Optional[str] = None,
    target_created_at: Optional[str] = None,
    execution_telemetry_window: Optional[Dict[str, Any]] = None,
    feedback_events_considered: Optional[Dict[str, Any]] = None,
    preference_model_used: Optional[Dict[str, Any]] = None,
    rationale: Optional[str] = None,
    auditable_fields: Optional[Dict[str, Any]] = None,
    confidence: Optional[float] = None,
    recommendation: Optional[str] = None,
) -> EvaluatorResult:
    """
    Build and return a validated EvaluatorResult.

    score_components must have weights summing to 1.0 ± 0.01.
    overall_score and recommendation are derived automatically unless overridden.
    confidence defaults to the mean of per-component confidence values if provided,
    otherwise 0.8.
    """
    overall_score = _compute_overall_score(score_components)

    if confidence is None:
        conf_vals = [c.confidence for c in score_components.values() if c.confidence is not None]
        confidence = round(sum(conf_vals) / len(conf_vals), 6) if conf_vals else 0.8

    if recommendation is None:
        recommendation = _derive_recommendation(overall_score, target_promotion_state)

    snapshot = EvaluationDataSnapshot(
        target_artifact_version=target_artifact_version,
        target_artifact_checksum=target_artifact_checksum,
        target_created_at=target_created_at,
        execution_telemetry_window=execution_telemetry_window,
        feedback_events_considered=feedback_events_considered,
        preference_model_used=preference_model_used,
    )

    return EvaluatorResult(
        registry_id=registry_id or f"eval-{strategy_id}-{uuid.uuid4().hex[:8]}",
        artifact_type="evaluation_result",
        strategy_id=strategy_id,
        target_artifact_id=target_artifact_id,
        target_artifact_type=target_artifact_type,
        target_promotion_state=target_promotion_state,
        evaluator_id=evaluator_id,
        evaluator_version=evaluator_version,
        evaluation_timestamp=evaluation_timestamp or _utc_now(),
        evaluation_data_snapshot=snapshot,
        score_components=score_components,
        overall_score=overall_score,
        recommendation=recommendation,
        confidence=confidence,
        rationale=rationale,
        auditable_fields=auditable_fields,
    )


def evaluate_artifact(
    *,
    artifact: Optional[Mapping[str, Any]],
    score_components: Optional[Dict[str, ScoreComponent]] = None,
    evaluator_id: str = "evaluator_primary_v1",
    evaluator_version: str = "1.0.0",
    registry_id: Optional[str] = None,
    evaluation_timestamp: Optional[str] = None,
    execution_telemetry_window: Optional[Dict[str, Any]] = None,
    feedback_events_considered: Optional[Dict[str, Any]] = None,
    preference_model_used: Optional[Dict[str, Any]] = None,
    rationale: Optional[str] = None,
    auditable_fields: Optional[Dict[str, Any]] = None,
    confidence: Optional[float] = None,
    recommendation: Optional[str] = None,
    strategy_id: Optional[str] = None,
    target_artifact_id: Optional[str] = None,
    target_artifact_type: Optional[str] = None,
    target_promotion_state: Optional[str] = None,
    target_artifact_version: Optional[str] = None,
    target_artifact_checksum: Optional[str] = None,
    target_created_at: Optional[str] = None,
) -> EvaluatorResult:
    """
    Evaluate a registry-style artifact payload.

    This is the main service entrypoint for callers that already have the governed artifact
    record in memory and want the evaluator to derive linkage fields automatically.

    If artifact metadata is unavailable, return a low-confidence advisory result that requests
    critique instead of raising. This matches the service error-handling guidance.
    """
    score_components = score_components or {}

    if artifact is None:
        if strategy_id is None or target_artifact_id is None:
            raise ValueError("strategy_id and target_artifact_id are required when artifact is None")
        fallback_snapshot = EvaluationDataSnapshot(
            target_artifact_version=target_artifact_version or "unknown",
            target_artifact_checksum=target_artifact_checksum or "missing-artifact",
            target_created_at=target_created_at,
            execution_telemetry_window=execution_telemetry_window,
            feedback_events_considered=feedback_events_considered,
            preference_model_used=preference_model_used,
        )
        fallback_fields = dict(auditable_fields or {})
        fallback_fields["missing_artifact"] = {
            "status": "not_found",
            "requested_target_artifact_id": target_artifact_id,
        }
        return EvaluatorResult(
            registry_id=registry_id or f"eval-{strategy_id}-{uuid.uuid4().hex[:8]}",
            artifact_type="evaluation_result",
            strategy_id=strategy_id,
            target_artifact_id=target_artifact_id,
            target_artifact_type=_require_non_empty(target_artifact_type, "target_artifact_type"),
            target_promotion_state=target_promotion_state or "candidate",
            evaluator_id=evaluator_id,
            evaluator_version=evaluator_version,
            evaluation_timestamp=evaluation_timestamp or _utc_now(),
            evaluation_data_snapshot=fallback_snapshot,
            score_components=score_components,
            overall_score=0.0,
            recommendation=recommendation or Recommendation.NEEDS_CRITIQUE.value,
            confidence=0.0 if confidence is None else confidence,
            rationale=rationale or "Artifact metadata unavailable at evaluation time; manual critique required.",
            auditable_fields=fallback_fields,
        )

    resolved_strategy_id = strategy_id or _coalesce_artifact_field(artifact, "strategy_id")
    resolved_artifact_id = target_artifact_id or _coalesce_artifact_field(artifact, "registry_id", "target_artifact_id")
    resolved_artifact_type = target_artifact_type or _coalesce_artifact_field(artifact, "artifact_type", "target_artifact_type")
    resolved_promotion_state = target_promotion_state or _coalesce_artifact_field(
        artifact, "promotion_state", "lifecycle_state", "target_promotion_state", default="candidate"
    )
    resolved_version = target_artifact_version or _coalesce_artifact_field(artifact, "version", default="unknown")
    resolved_checksum = target_artifact_checksum or _coalesce_artifact_field(artifact, "checksum", default="unknown")
    resolved_created_at = target_created_at or _coalesce_artifact_field(artifact, "created_at")

    if resolved_strategy_id is None:
        raise ValueError("artifact must provide strategy_id")
    if resolved_artifact_id is None:
        raise ValueError("artifact must provide registry_id or target_artifact_id")
    resolved_artifact_type = _require_non_empty(resolved_artifact_type, "target_artifact_type")

    merged_auditable_fields = dict(auditable_fields or {})
    if artifact.get("metadata") is not None:
        merged_auditable_fields.setdefault("target_metadata", artifact["metadata"])

    snapshot = EvaluationDataSnapshot(
        target_artifact_version=resolved_version,
        target_artifact_checksum=resolved_checksum,
        target_created_at=resolved_created_at,
        target_lineage=artifact.get("lineage"),
        execution_telemetry_window=execution_telemetry_window,
        feedback_events_considered=feedback_events_considered,
        preference_model_used=preference_model_used,
    )

    overall_score = _compute_overall_score(score_components)
    if confidence is None:
        conf_vals = [c.confidence for c in score_components.values() if c.confidence is not None]
        confidence = round(sum(conf_vals) / len(conf_vals), 6) if conf_vals else 0.8
    if recommendation is None:
        recommendation = _derive_recommendation(overall_score, resolved_promotion_state)

    return EvaluatorResult(
        registry_id=registry_id or f"eval-{resolved_strategy_id}-{uuid.uuid4().hex[:8]}",
        artifact_type="evaluation_result",
        strategy_id=resolved_strategy_id,
        target_artifact_id=resolved_artifact_id,
        target_artifact_type=resolved_artifact_type,
        target_promotion_state=resolved_promotion_state,
        evaluator_id=evaluator_id,
        evaluator_version=evaluator_version,
        evaluation_timestamp=evaluation_timestamp or _utc_now(),
        evaluation_data_snapshot=snapshot,
        score_components=score_components,
        overall_score=overall_score,
        recommendation=recommendation,
        confidence=confidence,
        rationale=rationale,
        auditable_fields=merged_auditable_fields or None,
    )
