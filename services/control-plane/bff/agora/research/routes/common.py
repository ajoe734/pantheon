"""Shared models, context, and helpers for Agora research subrouters."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, Field

from ..dispatcher import ALLOWLISTED_STAGE_BACKENDS
ResearchDispatcher = None



# ---------------------------------------------------------------------------
# Stage -> preferred_backend routing policy (MASTER_SD_RESPONSE.md §B3)
# ---------------------------------------------------------------------------

_STAGE_TO_BACKEND: Dict[str, str] = dict(ALLOWLISTED_STAGE_BACKENDS)
_VALID_STAGE_TYPES = frozenset(_STAGE_TO_BACKEND.keys())
_FORBIDDEN_ENVIRONMENTS = frozenset({"live", "canary"})
_PLAN_NO_ORDER_ROUTE_PROOF = "research_plan_no_order_route"
_RUN_NO_ORDER_ROUTE_PROOF = "research_only_not_direct_action"
_CAPABILITY = "agora.research.v1"

_PLAN_ALLOWED_ACTIONS: Dict[str, Dict[str, bool]] = {
    "draft":     {"approve": True,  "cancel": True,  "dispatch": False},
    "approved":  {"approve": False, "cancel": True,  "dispatch": True},
    "running":   {"approve": False, "cancel": True,  "dispatch": False},
    "completed": {"approve": False, "cancel": False, "dispatch": False},
    "cancelled": {"approve": False, "cancel": False, "dispatch": False},
}

_RUN_ALLOWED_ACTIONS: Dict[str, Dict[str, bool]] = {
    "queued":      {"cancel": True},
    "dispatching": {"cancel": True},
    "running":     {"cancel": True},
    "succeeded":   {"cancel": False},
    "failed":      {"cancel": False},
    "cancelled":   {"cancel": False},
    "timed_out":   {"cancel": False},
}

_CANDIDATE_NO_ORDER_ROUTE_PROOF = "candidate_pool_bff_request_only_no_order_route"
_DEFAULT_RECIPE_PATH = (
    Path(__file__).resolve().parents[6]
    / "docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/"
    / "candidate_scoring_recipe.winner_branch.default.json"
)
if not _DEFAULT_RECIPE_PATH.exists():
    _DEFAULT_RECIPE_PATH = (
        Path(__file__).resolve().parents[5]
        / "docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/"
        / "candidate_scoring_recipe.winner_branch.default.json"
    )

_POOL_ALLOWED_ACTIONS: Dict[str, bool] = {
    "score": True,
    "discuss": True,
}

_MEMBER_ALLOWED_ACTIONS: Dict[str, Dict[str, bool]] = {
    "candidate": {
        "review": True,
        "approve_for_monitoring": True,
        "send_to_shadow": True,
        "needs_more_research": True,
        "park": True,
        "reject": True,
    },
    "review": {
        "review": True,
        "approve_for_monitoring": True,
        "send_to_shadow": True,
        "needs_more_research": True,
        "park": True,
        "reject": True,
    },
    "approved": {
        "review": True,
        "approve_for_monitoring": False,
        "send_to_shadow": True,
        "needs_more_research": True,
        "park": True,
        "reject": True,
    },
    "rejected": {
        "review": False,
        "approve_for_monitoring": False,
        "send_to_shadow": False,
        "needs_more_research": False,
        "park": False,
        "reject": False,
    },
}

_REVIEW_DECISION_TO_LIFECYCLE = {
    "approve_for_monitoring": "approved",
    "send_to_shadow": "review",
    "needs_more_research": "review",
    "park": "rejected",
    "reject": "rejected",
}

# ---------------------------------------------------------------------------
# Candidate truth projection
# ---------------------------------------------------------------------------

_FIELD_SOURCE_MEMBER = "candidate_pool_member"
_FIELD_SOURCE_SCORE = "candidate_score_result"
_FIELD_SOURCE_REVIEW = "candidate_review"
_FIELD_SOURCE_MONITORING = "candidate_monitoring"

_FIELD_REASON_SCORE_NOT_RUN = "score_not_run"
_FIELD_REASON_NO_GOVERNED_SOURCE = "no_governed_source"
_FIELD_REASON_NOT_RECORDED = "not_recorded"

_EVIDENCE_REDACTION_LIST = "list_response"
_EVIDENCE_REDACTION_VIEWER = "viewer_role"

_MEMBER_PAGE_TOKEN_PREFIX = "cpm-offset-"
_MEMBER_ORDER_BY = "created_at,artifact_id"
_RATIONALE_TOP_COMPONENT_LIMIT = 3


def _available_field(
    value: Any,
    *,
    source_type: str,
    source_ref: str,
    as_of: str,
) -> Dict[str, Any]:
    return {
        "availability": "available",
        "value": value,
        "provenance": {
            "source_type": source_type,
            "source_ref": source_ref,
            "as_of": as_of,
        },
    }


def _unavailable_field(reason: str) -> Dict[str, Any]:
    return {"availability": "unavailable", "reason": reason}


def _score_source_ref(pool_id: str, artifact_id: str, score: Dict[str, Any]) -> str:
    return f"candidate-score:{pool_id}:{artifact_id}:{score['scored_at']}"


def _component_digest(
    component: Dict[str, Any],
    *,
    include_explanation: bool,
) -> Dict[str, Any]:
    return {
        "component_id": component.get("component_id"),
        "label": component.get("label"),
        "contribution": component.get("contribution"),
        "explanation": component.get("explanation") if include_explanation else None,
    }


def _score_without_private_explanations(score: Dict[str, Any]) -> Dict[str, Any]:
    projected = dict(score)
    projected["components"] = [
        {
            key: value
            for key, value in component.items()
            if key != "explanation"
        }
        for component in score.get("components", [])
    ]
    return projected


def _operator_grade_scope(scope: Any) -> bool:
    try:
        from ...models import AGORA_REQUIRED_ROLES
    except ImportError:
        from services.control_plane.bff.agora.models import AGORA_REQUIRED_ROLES
    return bool(AGORA_REQUIRED_ROLES.intersection(set(getattr(scope, "roles", []) or [])))


def _member_truth_projection(
    *,
    pool: Dict[str, Any],
    member: Dict[str, Any],
    score: Optional[Dict[str, Any]],
    reviews: List[Dict[str, Any]],
    monitoring: Optional[Dict[str, Any]],
    recipe: Dict[str, Any],
    evidence_summary_mode: str,
    operator_grade: bool,
) -> Dict[str, Any]:
    pool_id = pool["pool_id"]
    artifact_id = member["artifact_id"]
    snapshot_at = str(pool.get("snapshot_at") or "")
    member_as_of = str(
        member.get("_updated_at")
        or member.get("created_at")
        or snapshot_at
    )
    include_private_explanations = (
        evidence_summary_mode == "detail" and operator_grade
    )
    penalty_ids = {
        component["component_id"]
        for component in recipe.get("penalty_components", [])
    }

    fields: Dict[str, Any] = {
        "details": _available_field(
            {
                "kind": "candidate_identity",
                "title": member.get("title"),
                "strategy_ref": member.get("strategy_ref"),
                "run_ref": member.get("run_ref"),
                "producing_persona_id": member.get("producing_persona_id"),
                "lifecycle_state": member.get("lifecycle_state"),
                "created_at": member.get("created_at"),
            },
            source_type=_FIELD_SOURCE_MEMBER,
            source_ref=f"candidate-pool-member:{pool_id}:{artifact_id}",
            as_of=member_as_of,
        ),
    }

    rationale_reviews = [
        review for review in reviews
        if str(review.get("rationale") or "").strip()
    ]
    rationale_reviews.sort(key=lambda review: str(review.get("reviewed_at") or ""))
    latest_review = rationale_reviews[-1] if rationale_reviews else None

    if latest_review is not None:
        fields["rationale"] = _available_field(
            {
                "kind": "operator_review_rationale",
                "decision": latest_review.get("decision"),
                "rationale": latest_review.get("rationale"),
                "reviewed_by": latest_review.get("reviewed_by"),
                "reviewed_at": latest_review.get("reviewed_at"),
            },
            source_type=_FIELD_SOURCE_REVIEW,
            source_ref=(
                f"candidate-review:{pool_id}:{artifact_id}:{latest_review.get('review_id')}"
            ),
            as_of=str(latest_review.get("reviewed_at") or ""),
        )
    elif score is not None:
        positives = [
            component for component in score.get("components", [])
            if component.get("component_id") not in penalty_ids
        ]
        positives.sort(
            key=lambda component: float(component.get("contribution") or 0.0),
            reverse=True,
        )
        fields["rationale"] = _available_field(
            {
                "kind": "score_component_attribution",
                "band": score.get("band"),
                "effective_score": score.get("effective_score"),
                "top_components": [
                    _component_digest(
                        component,
                        include_explanation=include_private_explanations,
                    )
                    for component in positives[:_RATIONALE_TOP_COMPONENT_LIMIT]
                ],
            },
            source_type=_FIELD_SOURCE_SCORE,
            source_ref=_score_source_ref(pool_id, artifact_id, score),
            as_of=str(score.get("scored_at") or ""),
        )
    else:
        fields["rationale"] = _unavailable_field(_FIELD_REASON_SCORE_NOT_RUN)

    if score is not None:
        penalties = [
            component for component in score.get("components", [])
            if component.get("component_id") in penalty_ids
            and float(component.get("contribution") or 0.0) > 0.0
        ]
        penalties.sort(
            key=lambda component: float(component.get("contribution") or 0.0),
            reverse=True,
        )
        fields["concerns"] = _available_field(
            {
                "kind": "score_risk_attribution",
                "blockers": [str(blocker) for blocker in (score.get("blockers") or [])],
                "penalty_components": [
                    _component_digest(
                        component,
                        include_explanation=include_private_explanations,
                    )
                    for component in penalties
                ],
            },
            source_type=_FIELD_SOURCE_SCORE,
            source_ref=_score_source_ref(pool_id, artifact_id, score),
            as_of=str(score.get("scored_at") or ""),
        )

        include_summary = evidence_summary_mode == "detail" and operator_grade
        redaction_reason = (
            _EVIDENCE_REDACTION_VIEWER
            if evidence_summary_mode == "detail"
            else _EVIDENCE_REDACTION_LIST
        )
        evidence_items: List[Dict[str, Any]] = []
        total_refs = 0
        for component in score.get("components", []):
            refs = [str(ref) for ref in (component.get("evidence_refs") or [])]
            if not refs:
                continue
            total_refs += len(refs)
            item: Dict[str, Any] = {
                "component_id": component.get("component_id"),
                "label": component.get("label"),
                "evidence_refs": refs,
                "summary": component.get("explanation") if include_summary else None,
                "summary_redacted": not include_summary,
            }
            if not include_summary:
                item["redaction_reason"] = redaction_reason
            evidence_items.append(item)
        if total_refs:
            fields["evidence"] = _available_field(
                {
                    "kind": "score_evidence_refs",
                    "items": evidence_items,
                    "total_refs": total_refs,
                },
                source_type=_FIELD_SOURCE_SCORE,
                source_ref=_score_source_ref(pool_id, artifact_id, score),
                as_of=str(score.get("scored_at") or ""),
            )
        else:
            fields["evidence"] = _unavailable_field(
                _FIELD_REASON_NO_GOVERNED_SOURCE
            )
    else:
        fields["concerns"] = _unavailable_field(_FIELD_REASON_SCORE_NOT_RUN)
        fields["evidence"] = _unavailable_field(_FIELD_REASON_SCORE_NOT_RUN)

    active_monitoring = (
        monitoring
        if monitoring is not None
        and monitoring.get("monitoring_state") in {"active", "paused"}
        else None
    )
    if active_monitoring is not None and (
        active_monitoring.get("review_due_at")
        or active_monitoring.get("trigger_conditions")
    ):
        fields["next_event"] = _available_field(
            {
                "kind": "monitoring_schedule",
                "monitoring_state": active_monitoring.get("monitoring_state"),
                "review_due_at": active_monitoring.get("review_due_at"),
                "trigger_conditions": list(active_monitoring.get("trigger_conditions") or []),
                "added_by": active_monitoring.get("added_by"),
                "added_at": active_monitoring.get("added_at"),
            },
            source_type=_FIELD_SOURCE_MONITORING,
            source_ref=f"candidate-monitoring:{pool_id}:{artifact_id}",
            as_of=str(active_monitoring.get("added_at") or ""),
        )
    else:
        fields["next_event"] = _unavailable_field(_FIELD_REASON_NO_GOVERNED_SOURCE)

    if score is not None:
        effective_semantics: Dict[str, Any] = {
            "kind": "recipe_weighted_score",
            "availability": "available",
            "is_confidence_score": False,
            "scale_min": 0,
            "scale_max": 100,
            "recipe_id": score.get("recipe_id"),
            "recipe_version": score.get("recipe_version"),
            "source_ref": _score_source_ref(pool_id, artifact_id, score),
            "as_of": str(score.get("scored_at") or ""),
        }
    else:
        effective_semantics = {
            "kind": "recipe_weighted_score",
            "availability": "unavailable",
            "is_confidence_score": False,
            "reason": _FIELD_REASON_SCORE_NOT_RUN,
        }
    if member.get("sharpe_summary") is not None:
        sharpe_semantics: Dict[str, Any] = {
            "kind": "sharpe_ratio",
            "availability": "available",
            "is_confidence_score": False,
            "transformation": "sharpe_ratio_from_producing_research_run",
            "source_ref": (
                member.get("run_ref")
                or f"candidate-pool-member:{pool_id}:{artifact_id}"
            ),
            "as_of": snapshot_at,
        }
    else:
        sharpe_semantics = {
            "kind": "sharpe_ratio",
            "availability": "unavailable",
            "is_confidence_score": False,
            "reason": _FIELD_REASON_NOT_RECORDED,
        }

    as_of_values = [
        field["provenance"]["as_of"]
        for field in fields.values()
        if field.get("availability") == "available" and field["provenance"].get("as_of")
    ]
    return {
        "fields": fields,
        "as_of": max(as_of_values) if as_of_values else snapshot_at,
        "score_semantics": {
            "effective_score": effective_semantics,
            "sharpe_summary": sharpe_semantics,
        },
    }


# ---------------------------------------------------------------------------
# Request body models
# ---------------------------------------------------------------------------

class _StageRoutingRequest(BaseModel):
    model_config = {"extra": "forbid"}
    preferred_backend: Optional[str] = None
    fallback_policy: Optional[str] = None
    backend_mode: Optional[str] = None
    routing_reason: Optional[str] = None


class _StageRequest(BaseModel):
    model_config = {"extra": "forbid"}
    stage_id: Optional[str] = None
    stage_type: str
    status: Optional[str] = "pending"
    dependencies: List[str] = Field(default_factory=list)
    required_capability: Optional[str] = None
    routing: Optional[_StageRoutingRequest] = None
    input_refs: Optional[List[str]] = None
    output_refs: Optional[List[str]] = None
    parameters: Optional[Dict[str, Any]] = None
    blocking_reasons: Optional[List[str]] = None


class _ExecutionConstraintsRequest(BaseModel):
    model_config = {"extra": "forbid"}
    max_runtime_hours: Optional[float] = None
    compute_tier: Optional[str] = None
    environments: Optional[List[str]] = None


class _PlanBudgetRequest(BaseModel):
    model_config = {"extra": "forbid"}
    compute_tier: Optional[str] = None
    max_runtime_seconds: Optional[int] = None
    max_parallel_stages: Optional[int] = None
    external_data_spend_allowed: Optional[bool] = None


class ResearchPlanCreateRequest(BaseModel):
    """Request body for POST /bff/agora/workshops/{workshop_id}/research-plans."""
    model_config = {"extra": "forbid"}
    spec_version: str
    strategy_id: str = Field(min_length=1)
    strategy_spec_registry_id: str = Field(min_length=1)
    stages: List[_StageRequest] = Field(min_length=1)
    budget: Optional[_PlanBudgetRequest] = None
    execution_constraints: Optional[_ExecutionConstraintsRequest] = None


class ServantResearchProposalRequest(BaseModel):
    model_config = {"extra": "forbid"}
    prompt: str = Field(min_length=1, max_length=4000)


class CandidatePoolFilterRequest(BaseModel):
    model_config = {"extra": "forbid"}
    asset_classes: List[str] = Field(default_factory=list)
    strategy_families: List[str] = Field(default_factory=list)
    lifecycle_states: List[str] = Field(default_factory=lambda: ["candidate"])
    persona_ids: List[str] = Field(default_factory=list)


class CandidatePoolCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}
    operator_id: str = Field(min_length=1)
    filter: Optional[CandidatePoolFilterRequest] = None
    recipe_id: Optional[str] = None
    candidates: Optional[List[Dict[str, Any]]] = None
    metrics_by_artifact: Optional[Dict[str, Dict[str, Any]]] = None
    profile: Optional[str] = None
    strategy_id: Optional[str] = None
    strategy_version: Optional[str] = None
    strategy_ref: Optional[str] = None


class CandidateScoreRunRequest(BaseModel):
    model_config = {"extra": "forbid"}
    recipe_id: Optional[str] = None
    force_rescore: bool = False


class CandidateMemberReviewRequest(BaseModel):
    model_config = {"extra": "forbid"}
    decision: str
    rationale: Optional[str] = None
    score_override: Optional[Dict[str, Any]] = None
    reviewed_by: str = Field(min_length=1)
    reviewed_at: Optional[str] = None
    negative_example_tags: List[str] = Field(default_factory=list)


class CandidateDiscussionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    discussion_id: Optional[str] = None
    subject_type: Optional[str] = None
    subject_id: Optional[str] = None
    pool_id: Optional[str] = None
    parent_discussion_id: Optional[str] = None
    author: Optional[str] = None
    body: str = Field(min_length=1)
    kind: str = "comment"
    tags: List[str] = Field(default_factory=list)
    resolved: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CandidateMonitoringRequest(BaseModel):
    model_config = {"extra": "forbid"}
    artifact_id: Optional[str] = None
    pool_id: Optional[str] = None
    monitoring_state: str = "active"
    trigger_conditions: List[Dict[str, Any]] = Field(default_factory=list)
    last_score_result_id: Optional[str] = None
    review_due_at: Optional[str] = None
    added_by: Optional[str] = None
    added_at: Optional[str] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _plan_etag(plan_id: str, lock_version: int) -> str:
    return f"W/\"research-plan:{plan_id}:v{lock_version}\""


def _parse_plan_lock_version(if_match: str, plan_id: str) -> int:
    prefix = f"W/\"research-plan:{plan_id}:v"
    if if_match.startswith(prefix) and if_match.endswith("\""):
        try:
            return int(if_match[len(prefix):-1])
        except ValueError:
            pass
    return 0


def _candidate_pool_etag(pool_id: str, lock_version: int) -> str:
    return f"W/\"candidate-pool:{pool_id}:v{lock_version}\""


def _parse_candidate_pool_lock_version(if_match: str, pool_id: str) -> int:
    prefix = f"W/\"candidate-pool:{pool_id}:v"
    if if_match.startswith(prefix) and if_match.endswith("\""):
        try:
            return int(if_match[len(prefix):-1])
        except ValueError:
            pass
    return 0


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _public_candidate_pool(pool: Dict[str, Any]) -> Dict[str, Any]:
    public = {
        "spec_version": pool.get("spec_version", "1.0"),
        "pool_id": pool["pool_id"],
        "operator_id": pool["operator_id"],
        "candidates": [
            _candidate_public_member(candidate)
            for candidate in pool.get("candidates", [])
        ],
        "snapshot_at": pool["snapshot_at"],
    }
    if "filter" in pool:
        public["filter"] = dict(pool.get("filter") or {})
    public["total"] = int(pool.get("total", len(public["candidates"])))
    metadata = dict(pool.get("metadata") or {})
    metadata.setdefault("no_order_route_proof", _CANDIDATE_NO_ORDER_ROUTE_PROOF)
    public["metadata"] = metadata
    return public


def _public_candidate_monitoring(m: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in m.items()
        if key not in ("tenant_id", "user_id") and not key.startswith("_")
    }


def _public_candidate_discussion(d: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in d.items()
        if key not in ("tenant_id", "user_id") and not key.startswith("_")
    }


def _candidate_list_envelope(
    *,
    items: List[Dict[str, Any]],
    utc_now: Callable[[], str],
    scope: Any,
    page_info: Optional[Dict[str, Any]] = None,
    meta_extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "items": items,
        "page_info": page_info if page_info is not None else {
            "next_page_token": None,
            "page_size": len(items),
            "has_more": False,
            "total": len(items),
        },
        "meta": {
            "snapshot_at": utc_now(),
            "capability": _CAPABILITY,
            "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
            **(meta_extra or {}),
        },
    }


def _candidate_pool_detail_envelope(
    *,
    pool: Dict[str, Any],
    utc_now: Callable[[], str],
    scope: Any,
) -> Dict[str, Any]:
    pool_id = pool["pool_id"]
    lock_version = int(pool.get("lock_version", 1))
    etag = _candidate_pool_etag(pool_id, lock_version)
    return {
        "object_ref": {"type": "candidate_pool", "id": pool_id},
        "status": "snapshot",
        "lifecycle_state": "snapshot",
        "allowedActions": dict(_POOL_ALLOWED_ACTIONS),
        "data": _public_candidate_pool(pool),
        "meta": {
            "snapshot_at": utc_now(),
            "capability": _CAPABILITY,
            "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            "etag": etag,
            "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
        },
        "links": {
            "self": f"/bff/agora/candidate-pools/{pool_id}",
            "members": f"/bff/agora/candidate-pools/{pool_id}/members",
            "score": f"/bff/agora/candidate-pools/{pool_id}/score",
            "discussions": f"/bff/agora/candidate-pools/{pool_id}/discussions",
            "monitoring": f"/bff/agora/candidate-pools/{pool_id}/monitoring",
        },
    }


def _candidate_detail_envelope(
    *,
    pool: Dict[str, Any],
    artifact_id: str,
    data: Dict[str, Any],
    utc_now: Callable[[], str],
    scope: Any,
    object_type: str = "candidate_pool_member",
) -> Dict[str, Any]:
    pool_id = pool["pool_id"]
    lock_version = int(pool.get("lock_version", 1))
    status = str(data.get("lifecycle_state") or data.get("monitoring_state") or "recorded")
    return {
        "object_ref": {"type": object_type, "id": artifact_id},
        "status": status,
        "lifecycle_state": status,
        "allowedActions": _MEMBER_ALLOWED_ACTIONS.get(status, {}),
        "data": data,
        "meta": {
            "snapshot_at": utc_now(),
            "capability": _CAPABILITY,
            "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            "etag": _candidate_pool_etag(pool_id, lock_version),
            "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
        },
        "links": {
            "pool": f"/bff/agora/candidate-pools/{pool_id}",
            "self": f"/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}",
            "review": f"/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/review",
            "discussions": f"/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions",
            "monitor": f"/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor",
        },
    }


def _load_default_scoring_recipe() -> Dict[str, Any]:
    with _DEFAULT_RECIPE_PATH.open(encoding="utf-8") as fh:
        recipe = json.load(fh)
    return dict(recipe)


def _score_band(recipe: Dict[str, Any], effective_score: float) -> str:
    for band in recipe.get("score_bands", []):
        if (
            float(band.get("min_inclusive", 0)) <= effective_score
            <= float(band.get("max_inclusive", 100))
        ):
            return str(band.get("name"))
    return "park"


def _normalize_component_value(component: Dict[str, Any], raw_value: Any) -> Optional[float]:
    if raw_value is None:
        return None
    try:
        normalized = _clamp(float(raw_value), 0.0, 1.0)
    except (TypeError, ValueError):
        return None
    if component.get("direction") == "lower_better":
        normalized = 1.0 - normalized
    if component.get("transform") == "inverse_percentile":
        normalized = 1.0 - normalized
    return _clamp(normalized, 0.0, 1.0)


def _component_evidence_refs(
    component: Dict[str, Any],
    metrics: Dict[str, Any],
) -> List[str]:
    refs = (metrics.get("evidence_refs") or {}).get(component["component_id"])
    if isinstance(refs, list):
        return [str(ref) for ref in refs if str(ref).strip()]
    return []


def _score_component(
    artifact_id: str,
    component: Dict[str, Any],
    metrics: Dict[str, Any],
    blockers: List[str],
) -> Dict[str, Any]:
    component_id = component["component_id"]
    raw_value = (metrics.get("components") or {}).get(component_id)
    normalized = _normalize_component_value(component, raw_value)
    missing_policy = component.get("missing_policy", "score_zero")
    if normalized is None:
        if missing_policy == "impute_median":
            normalized = 0.5
        elif missing_policy in {"score_zero", "not_applicable"}:
            normalized = 0.0
        elif missing_policy == "reject_candidate":
            blockers.append(f"{component_id}: missing critical component")
        elif missing_policy == "mark_needs_research":
            blockers.append(f"{component_id}: missing; needs more research")
        elif missing_policy == "cap_final_score":
            blockers.append(f"{component_id}: missing; final score capped")

    contribution = 0.0 if normalized is None else 100.0 * normalized * float(component["weight"])
    max_contribution = component.get("max_contribution")
    if max_contribution is not None:
        contribution = min(contribution, float(max_contribution))
    return {
        "component_id": component_id,
        "label": component["label"],
        "category": component["category"],
        "raw_value": None if raw_value is None else float(raw_value),
        "normalized_value": normalized,
        "transform": component["transform"],
        "direction": component["direction"],
        "weight": float(component["weight"]),
        "contribution": round(contribution, 4),
        "missing_policy": missing_policy,
        "evidence_refs": _component_evidence_refs(component, metrics),
        "explanation": (
            "A2 CandidateScoringRecipe contribution computed from the stored "
            f"{component_id} normalized component value."
        ),
    }


def _score_candidate(
    *,
    pool_id: str,
    candidate: Dict[str, Any],
    metrics: Dict[str, Any],
    recipe: Dict[str, Any],
    data_cutoff: str,
    scored_at: str,
) -> Dict[str, Any]:
    artifact_id = candidate["artifact_id"]
    blockers: List[str] = []
    positive_components = [
        _score_component(artifact_id, component, metrics, blockers)
        for component in recipe.get("positive_components", [])
    ]
    penalty_components = [
        _score_component(artifact_id, component, metrics, blockers)
        for component in recipe.get("penalty_components", [])
    ]
    base_score = sum(component["contribution"] for component in positive_components)
    penalty_score = sum(component["contribution"] for component in penalty_components)
    raw_score = _clamp(base_score - penalty_score, 0.0, 100.0)

    confidence_value = metrics.get("evidence_confidence")
    if confidence_value is None:
        confidence_components = [
            component for component in positive_components
            if component["category"] in {"confidence", "data_quality"}
            and component["normalized_value"] is not None
        ]
        if confidence_components:
            confidence_value = sum(
                float(component["normalized_value"]) for component in confidence_components
            ) / len(confidence_components)
        else:
            confidence_value = 0.25
            blockers.append("evidence_confidence: missing; defaulted to 0.25")
    evidence_confidence = _clamp(float(confidence_value), 0.0, 1.0)
    confidence_policy = recipe.get("confidence_policy") or {}
    confidence_multiplier = (
        float(confidence_policy.get("multiplier_floor", 0.60))
        + float(confidence_policy.get("multiplier_weight", 0.40)) * evidence_confidence
    )
    effective_score = raw_score * confidence_multiplier
    forced_band: Optional[str] = None

    component_by_id = {
        component["component_id"]: component
        for component in [*positive_components, *penalty_components]
    }
    data_quality = component_by_id.get("data_quality", {}).get("normalized_value")
    if data_quality is not None and data_quality < 0.50:
        effective_score = min(effective_score, 49.0)
        forced_band = "needs_research"
        blockers.append("data_quality below 0.50; effective_score capped at 49")

    distribution_risk = component_by_id.get(
        "related_branch_distribution_risk", {}
    ).get("normalized_value")
    if distribution_risk is not None and distribution_risk > 0.80:
        effective_score = min(effective_score, 64.999)
        forced_band = "needs_research"
        blockers.append("related_branch_distribution_risk above 0.80; capped at needs_research")

    liquidity = component_by_id.get("liquidity_capacity", {}).get("normalized_value")
    if liquidity is None:
        forced_band = "suppressed"
        blockers.append("liquidity_capacity missing; candidate cannot be approved for monitoring")

    if metrics.get("suppressed") is True:
        forced_band = "suppressed"
        blockers.append("suppressed by compliance or governance policy")

    effective_score = round(_clamp(effective_score, 0.0, 100.0), 4)
    band = forced_band or _score_band(recipe, effective_score)
    return {
        "candidate_id": artifact_id,
        "pool_id": pool_id,
        "recipe_id": recipe["recipe_id"],
        "recipe_version": int(recipe["version"]),
        "raw_score": round(raw_score, 4),
        "penalty_score": round(penalty_score, 4),
        "evidence_confidence": round(evidence_confidence, 4),
        "effective_score": effective_score,
        "rank": None,
        "band": band,
        "components": [*positive_components, *penalty_components],
        "blockers": blockers,
        "data_cutoff": data_cutoff,
        "scored_at": scored_at,
        "override_reason": None,
    }


def _rank_scores(scores: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rankable = [
        score for score in scores
        if score.get("band") != "suppressed"
    ]
    rankable.sort(key=lambda score: float(score.get("effective_score") or 0.0), reverse=True)
    rank_by_candidate = {
        score["candidate_id"]: index
        for index, score in enumerate(rankable, start=1)
    }
    ranked = []
    for score in scores:
        updated = dict(score)
        updated["rank"] = rank_by_candidate.get(score["candidate_id"])
        ranked.append(updated)
    return ranked


def _default_registry_candidates(now: str) -> List[Dict[str, Any]]:
    """Test and demo fixture candidates only. Never used for unprovided production input."""
    return [
        {
            "artifact_id": "candidate-winner-branch-priority",
            "strategy_ref": "strategy://winner-branch/default",
            "title": "Winner branch accumulation candidate",
            "lifecycle_state": "candidate",
            "producing_persona_id": "persona-winner-branch",
            "sharpe_summary": 1.24,
            "run_ref": "research-run://winner-branch/prototype-backtest-001",
            "created_at": now,
            "_strategy_family": "winner_branch",
            "_asset_classes": ["equity"],
            "_metrics": {
                "evidence_confidence": 0.82,
                "components": {
                    "branch_historical_profitability": 0.91,
                    "branch_identity_confidence": 0.78,
                    "information_lead_proxy": 0.76,
                    "accumulation_persistence": 0.88,
                    "expected_value": 0.84,
                    "liquidity_capacity": 0.71,
                    "catalyst_alignment": 0.66,
                    "data_quality": 0.86,
                    "related_branch_distribution_risk": 0.21,
                    "price_extension_risk": 0.28,
                    "concentration_risk": 0.34,
                    "capacity_shortfall": 0.25,
                },
            },
        },
        {
            "artifact_id": "candidate-winner-branch-research",
            "strategy_ref": "strategy://winner-branch/default",
            "title": "Winner branch low data-quality candidate",
            "lifecycle_state": "candidate",
            "producing_persona_id": "persona-winner-branch",
            "sharpe_summary": 0.73,
            "run_ref": "research-run://winner-branch/prototype-backtest-002",
            "created_at": now,
            "_strategy_family": "winner_branch",
            "_asset_classes": ["equity"],
            "_metrics": {
                "evidence_confidence": 0.44,
                "components": {
                    "branch_historical_profitability": 0.64,
                    "branch_identity_confidence": 0.42,
                    "information_lead_proxy": 0.50,
                    "accumulation_persistence": 0.58,
                    "expected_value": 0.55,
                    "liquidity_capacity": 0.62,
                    "catalyst_alignment": 0.35,
                    "data_quality": 0.42,
                    "related_branch_distribution_risk": 0.33,
                    "price_extension_risk": 0.46,
                    "concentration_risk": 0.49,
                    "capacity_shortfall": 0.30,
                },
            },
        },
    ]


def _candidate_matches_filter(
    candidate: Dict[str, Any],
    pool_filter: Dict[str, Any],
) -> bool:
    lifecycle_states = pool_filter.get("lifecycle_states") or ["candidate"]
    if candidate.get("lifecycle_state") not in lifecycle_states:
        return False
    strategy_families = pool_filter.get("strategy_families") or []
    if strategy_families and candidate.get("_strategy_family") not in strategy_families:
        return False
    persona_ids = pool_filter.get("persona_ids") or []
    if persona_ids and candidate.get("producing_persona_id") not in persona_ids:
        return False
    asset_classes = pool_filter.get("asset_classes") or []
    candidate_asset_classes = candidate.get("_asset_classes") or []
    if asset_classes and not (set(asset_classes) & set(candidate_asset_classes)):
        return False
    return True


def _candidate_public_member(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in candidate.items()
        if not key.startswith("_")
    }


def _normalize_metrics_to_dict(raw_metrics: Any) -> Dict[str, Any]:
    """Normalize owner metrics list or dict to a flat {metric_name: value} dictionary."""
    if not raw_metrics:
        return {}
    if isinstance(raw_metrics, dict):
        return dict(raw_metrics)
    norm: Dict[str, Any] = {}
    if isinstance(raw_metrics, (list, tuple)):
        for item in raw_metrics:
            if isinstance(item, dict):
                m_name = None
                for k in ("metric", "metric_name", "name", "key"):
                    if k in item and item[k] is not None:
                        m_name = str(item[k]).strip()
                        break
                m_val = None
                for k in ("value", "metric_value", "score", "val"):
                    if k in item and item[k] is not None:
                        m_val = item[k]
                        break
                if m_name and m_val is not None:
                    norm[m_name] = m_val
    return norm


def _extract_run_artifact_identities(run: Dict[str, Any]) -> Tuple[Set[str], Dict[str, str]]:
    """Extract canonical artifact IDs and known digests from the run owner result."""
    known_ids: Set[str] = set()
    digests: Dict[str, str] = {}

    checksums = run.get("checksums") or {}
    if isinstance(checksums, dict):
        for k, v in checksums.items():
            if isinstance(k, str) and isinstance(v, str):
                if k.lower() in ("artifact",):
                    continue
                base = k.split("/")[-1]
                known_ids.add(k)
                digests[k] = v
                if base.lower() not in ("artifact",):
                    known_ids.add(base)
                    digests[base] = v

    for key in ("artifact_refs", "artifacts", "artifact_ids"):
        items = run.get(key)
        if not items:
            continue
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    item_ids: Set[str] = set()
                    for id_key in ("artifact_id", "ref_id", "id", "name"):
                        val = item.get(id_key)
                        if val:
                            val_str = str(val).strip()
                            if val_str and val_str.lower() not in ("artifact",):
                                item_ids.add(val_str)
                                base = val_str.split("/")[-1]
                                if base.lower() not in ("artifact",):
                                    item_ids.add(base)
                    ref = item.get("ref") or item.get("uri")
                    if ref:
                        ref_str = str(ref).strip()
                        if ref_str and ref_str.lower() not in ("artifact",):
                            item_ids.add(ref_str)
                            base = ref_str.split("/")[-1]
                            if base.lower() not in ("artifact",):
                                item_ids.add(base)

                    known_ids.update(item_ids)
                    digest = item.get("digest") or item.get("checksum") or item.get("artifact_digest")
                    if digest:
                        digest_str = str(digest).strip()
                        for art_id in item_ids:
                            digests[art_id] = digest_str

                elif isinstance(item, str):
                    s = item.strip()
                    if s and s.lower() not in ("artifact",):
                        known_ids.add(s)
                        base = s.split("/")[-1]
                        if base.lower() not in ("artifact",):
                            known_ids.add(base)
        elif isinstance(items, dict):
            for k, v in items.items():
                k_str = str(k).strip()
                if k_str and k_str.lower() not in ("artifact",):
                    known_ids.add(k_str)
                    base = k_str.split("/")[-1]
                    if base.lower() not in ("artifact",):
                        known_ids.add(base)
                        if isinstance(v, str):
                            digests[base] = v.strip()
                    if isinstance(v, str):
                        digests[k_str] = v.strip()

    single_art = run.get("artifact_id")
    if single_art:
        s = str(single_art).strip()
        if s and s.lower() not in ("artifact",):
            known_ids.add(s)
            base = s.split("/")[-1]
            if base.lower() not in ("artifact",):
                known_ids.add(base)
            single_digest = run.get("artifact_digest") or run.get("checksum")
            if single_digest:
                d_str = str(single_digest).strip()
                digests[s] = d_str
                if base.lower() not in ("artifact",):
                    digests[base] = d_str

    return known_ids, digests


def _plan_detail_envelope(
    plan: Dict[str, Any],
    utc_now: Callable[[], str],
    scope: Any,
) -> Dict[str, Any]:
    status = plan.get("status", "draft")
    plan_id = plan["plan_id"]
    lock_version = plan.get("lock_version", 1)
    etag = _plan_etag(plan_id, lock_version)
    return {
        "object_ref": {"type": "research_plan", "id": plan_id},
        "status": status,
        "lifecycle_state": status,
        "allowedActions": _PLAN_ALLOWED_ACTIONS.get(status, {}),
        "data": plan,
        "meta": {
            "snapshot_at": utc_now(),
            "capability": _CAPABILITY,
            "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            "etag": etag,
        },
        "links": {
            "self": f"/bff/agora/research-plans/{plan_id}",
            "runs": f"/bff/agora/research-plans/{plan_id}/runs",
        },
    }


def _run_projection_with_defaults(run: Dict[str, Any], store: Optional[Any] = None) -> Dict[str, Any]:
    backend = dict(run.get("backend") or {})
    backend.setdefault("requested", _STAGE_TO_BACKEND.get(run.get("stage_type", ""), ""))
    backend.setdefault("effective", _STAGE_TO_BACKEND.get(run.get("stage_type", ""), ""))
    backend.setdefault("mode", "real")

    projected: Dict[str, Any] = {
        "spec_version": run.get("spec_version", "1.0"),
        "run_id": run["run_id"],
        "plan_id": run["plan_id"],
        "workshop_id": run.get("workshop_id", ""),
        "strategy_id": run.get("strategy_id", ""),
        "strategy_spec_registry_id": run.get("strategy_spec_registry_id", ""),
        "stage_id": run.get("stage_id", ""),
        "stage_type": run.get("stage_type", ""),
        "execution_status": run.get("execution_status", "queued"),
        "outcome": run.get("outcome", "pending"),
        "progress": dict(run.get("progress") or {
            "phase": "queued",
            "percent": 0,
            "message": "Run queued for dispatch",
            "updated_at": run.get("updated_at") or run.get("created_at"),
        }),
        "backend": backend,
        "metrics": list(run.get("metrics") or []),
        "findings": list(run.get("findings") or []),
        "warnings": list(run.get("warnings") or []),
        "blocking_reasons": list(run.get("blocking_reasons") or []),
        "artifact_refs": list(run.get("artifact_refs") or []),
        "evidence_refs": list(run.get("evidence_refs") or []),
        "lineage_refs": list(run.get("lineage_refs") or []),
        "no_order_route_proof": run.get("no_order_route_proof") or _RUN_NO_ORDER_ROUTE_PROOF,
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
    }
    if run.get("started_at"):
        projected["started_at"] = run["started_at"]
    if run.get("completed_at"):
        projected["completed_at"] = run["completed_at"]
    if run.get("failure"):
        projected["failure"] = dict(run["failure"])
    if run.get("data_cutoff"):
        projected["data_cutoff"] = run["data_cutoff"]
    from ..receipt import resolve_run_provenance

    plan = None
    if store and hasattr(store, "get_plan") and run.get("plan_id"):
        try:
            plan = store.get_plan(run["plan_id"])
        except Exception:
            plan = None

    expected_correlation = (
        run.get("correlation_id")
        or run.get("trace_id")
        or (plan.get("correlation_id") if plan else None)
        or (plan.get("trace_id") if plan else None)
    )
    expected_owner = (
        run.get("executor")
        or run.get("owner")
        or (plan.get("executor") if plan else None)
        or (plan.get("owner") if plan else None)
    )

    resolved_prov, _ = resolve_run_provenance(
        store,
        run,
        expected_correlation_id=expected_correlation,
        expected_owner=expected_owner,
    )
    projected["provenance"] = resolved_prov
    return projected


def _build_run_projection(
    *,
    plan: Dict[str, Any],
    stage: Dict[str, Any],
    run_id: str,
    now: str,
    scope: Any,
) -> Dict[str, Any]:
    routing = stage.get("routing", {})
    backend = routing.get("effective_backend") or routing.get("preferred_backend", "")
    run = {
        "spec_version": "1.0",
        "run_id": run_id,
        "plan_id": plan["plan_id"],
        "workshop_id": plan.get("workshop_id", ""),
        "correlation_id": plan.get("correlation_id") or (f"workshop:{plan.get('workshop_id')}" if plan.get("workshop_id") else f"plan:{plan['plan_id']}"),
        "strategy_id": plan.get("strategy_id", ""),
        "strategy_spec_registry_id": plan.get("strategy_spec_registry_id", ""),
        "stage_id": stage["stage_id"],
        "stage_type": stage["stage_type"],
        "tenant_id": scope.tenant_id,
        "user_id": scope.user_id,
        "execution_status": "queued",
        "outcome": "pending",
        "progress": {
            "phase": "queued",
            "percent": 0,
            "message": "Run queued for dispatch",
            "updated_at": now,
        },
        "backend": {
            "requested": routing.get("preferred_backend", "") or _STAGE_TO_BACKEND.get(stage["stage_type"], ""),
            "effective": backend or _STAGE_TO_BACKEND.get(stage["stage_type"], ""),
            "mode": routing.get("backend_mode", "real"),
        },
        "provenance": "unavailable",
        "no_order_route_proof": _RUN_NO_ORDER_ROUTE_PROOF,
        "created_at": now,
        "updated_at": now,
    }
    return run


def _validate_create_body(
    body: ResearchPlanCreateRequest,
    workshop_id: str,
    bff_error_fn: Callable[..., HTTPException],
    error_code_enum_fn: Callable[[], Any],
) -> None:
    ErrorCode = error_code_enum_fn()
    if body.spec_version != "1.0":
        raise bff_error_fn(
            422, ErrorCode.VALIDATION_FAILED,
            "spec_version must be '1.0'",
            f"received: {body.spec_version!r}",
        )
    if body.execution_constraints and body.execution_constraints.environments:
        forbidden = _FORBIDDEN_ENVIRONMENTS & set(body.execution_constraints.environments)
        if forbidden:
            raise bff_error_fn(
                422, ErrorCode.VALIDATION_FAILED,
                f"execution_constraints.environments must not include: {', '.join(sorted(forbidden))}",
                "forbidden_environments",
            )
    for i, stage in enumerate(body.stages):
        if stage.stage_type not in _VALID_STAGE_TYPES:
            raise bff_error_fn(
                422, ErrorCode.VALIDATION_FAILED,
                f"stages[{i}].stage_type '{stage.stage_type}' is not a recognised stage type",
                stage.stage_type,
            )


def _resolve_originating_correlation(
    workshop_id: Optional[str],
    *,
    workshop_store: Optional[Any] = None,
    trace_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    fallback_id: Optional[str] = None,
) -> str:
    """Resolve originating interaction trace/correlation.

    Prefers explicit request trace/correlation ID, then queries the workshop store
    for the latest event's trace_id or correlation_id, and falls back to workshop/plan ID.
    """
    if trace_id and str(trace_id).strip():
        return str(trace_id).strip()
    if correlation_id and str(correlation_id).strip():
        return str(correlation_id).strip()

    store = workshop_store

    if workshop_id and store is not None and hasattr(store, "list_events"):
        try:
            events = store.list_events(workshop_id)
            if events:
                for ev in reversed(events):
                    ev_trace = ev.get("trace_id") or ev.get("correlation_id")
                    if ev_trace:
                        return str(ev_trace).strip()
        except Exception:
            pass

    if workshop_id:
        return f"workshop:{workshop_id}"
    if fallback_id:
        return f"plan:{fallback_id}"
    return ""


def _build_plan(
    body: ResearchPlanCreateRequest,
    workshop_id: str,
    plan_id: str,
    now: str,
    scope: Any,
    *,
    workshop_store: Optional[Any] = None,
    trace_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    stages: List[Dict[str, Any]] = []
    for stage in body.stages:
        canonical_backend = _STAGE_TO_BACKEND[stage.stage_type]
        routing_in = stage.routing
        routing: Dict[str, Any] = {
            "preferred_backend": (
                routing_in.preferred_backend if routing_in and routing_in.preferred_backend
                else canonical_backend
            ),
            "fallback_policy": (
                routing_in.fallback_policy if routing_in and routing_in.fallback_policy
                else "fail_closed"
            ),
        }
        if routing_in and routing_in.backend_mode:
            routing["backend_mode"] = routing_in.backend_mode
        if routing_in and routing_in.routing_reason:
            routing["routing_reason"] = routing_in.routing_reason

        normalized: Dict[str, Any] = {
            "stage_id": stage.stage_id or str(uuid.uuid4()),
            "stage_type": stage.stage_type,
            "status": stage.status or "pending",
            "dependencies": stage.dependencies or [],
            "required_capability": stage.required_capability or canonical_backend,
            "routing": routing,
        }
        if stage.input_refs is not None:
            normalized["input_refs"] = stage.input_refs
        if stage.output_refs is not None:
            normalized["output_refs"] = stage.output_refs
        if stage.parameters is not None:
            normalized["parameters"] = stage.parameters
        if stage.blocking_reasons is not None:
            normalized["blocking_reasons"] = stage.blocking_reasons
        stages.append(normalized)

    resolved_correlation = _resolve_originating_correlation(
        workshop_id,
        workshop_store=workshop_store,
        trace_id=trace_id,
        correlation_id=correlation_id,
        fallback_id=plan_id,
    )

    plan: Dict[str, Any] = {
        "spec_version": "1.0",
        "plan_id": plan_id,
        "workshop_id": workshop_id,
        "correlation_id": resolved_correlation,
        "strategy_id": body.strategy_id,
        "strategy_spec_registry_id": body.strategy_spec_registry_id,
        "tenant_id": scope.tenant_id,
        "user_id": scope.user_id,
        "status": "draft",
        "stages": stages,
        "no_order_route_proof": _PLAN_NO_ORDER_ROUTE_PROOF,
        "created_at": now,
        "lock_version": 1,
        "run_ids": [],
    }
    if body.budget:
        plan["budget"] = body.budget.model_dump(exclude_none=True)
    if body.execution_constraints:
        plan["execution_constraints"] = body.execution_constraints.model_dump(exclude_none=True)
    return plan


def _validate_pool_filter(
    pool_filter: Dict[str, Any],
    bff_error_fn: Callable[..., HTTPException],
    error_code_enum_fn: Callable[[], Any],
) -> None:
    allowed_create_states = {"candidate", "review", "approved"}
    requested_states = set(pool_filter.get("lifecycle_states") or ["candidate"])
    unknown = requested_states - allowed_create_states
    if unknown:
        ErrorCode = error_code_enum_fn()
        raise bff_error_fn(
            422, ErrorCode.VALIDATION_FAILED,
            "candidate pool filter lifecycle_states must use the v1.4 create-pool enum",
            f"unsupported lifecycle_states: {sorted(unknown)}",
        )


def _parse_member_page_token(
    page_token: Optional[str],
    bff_error_fn: Callable[..., HTTPException],
    error_code_enum_fn: Callable[[], Any],
) -> int:
    if page_token in (None, ""):
        return 0
    if isinstance(page_token, str) and page_token.startswith(_MEMBER_PAGE_TOKEN_PREFIX):
        suffix = page_token[len(_MEMBER_PAGE_TOKEN_PREFIX):]
        if suffix.isdigit():
            return int(suffix)
    ErrorCode = error_code_enum_fn()
    raise bff_error_fn(
        422, ErrorCode.VALIDATION_FAILED,
        "Invalid candidate member page_token",
        str(page_token),
    )


def _validate_review_body(
    body: CandidateMemberReviewRequest,
    bff_error_fn: Callable[..., HTTPException],
    error_code_enum_fn: Callable[[], Any],
) -> None:
    allowed = set(_REVIEW_DECISION_TO_LIFECYCLE)
    if body.decision not in allowed:
        ErrorCode = error_code_enum_fn()
        raise bff_error_fn(
            422, ErrorCode.VALIDATION_FAILED,
            "Candidate review decision is not in the v1.4 contract enum",
            body.decision,
        )
    if body.decision in {"reject", "park"} and not (body.rationale or "").strip():
        ErrorCode = error_code_enum_fn()
        raise bff_error_fn(
            422, ErrorCode.VALIDATION_FAILED,
            "rationale is required when decision is reject or park",
            "missing_rationale",
        )


def _discussion_record(
    *,
    body: CandidateDiscussionRequest,
    pool_id: str,
    subject_type: str,
    subject_id: str,
    scope: Any,
    now: str,
    bff_error_fn: Callable[..., HTTPException],
    error_code_enum_fn: Callable[[], Any],
) -> Dict[str, Any]:
    allowed_kinds = {"comment", "research_task", "score_question", "risk_flag", "approval_note"}
    if body.kind not in allowed_kinds:
        ErrorCode = error_code_enum_fn()
        raise bff_error_fn(
            422, ErrorCode.VALIDATION_FAILED,
            "Candidate discussion kind is not in the v1.4 contract enum",
            body.kind,
        )
    if body.subject_type and body.subject_type not in (subject_type, "member", "pool", "candidate_pool", "candidate_pool_member"):
        ErrorCode = error_code_enum_fn()
        raise bff_error_fn(422, ErrorCode.VALIDATION_FAILED, "subject_type does not match route", body.subject_type)
    if body.subject_id and body.subject_id != subject_id:
        ErrorCode = error_code_enum_fn()
        raise bff_error_fn(422, ErrorCode.VALIDATION_FAILED, "subject_id does not match route", body.subject_id)
    if body.pool_id and body.pool_id != pool_id:
        ErrorCode = error_code_enum_fn()
        raise bff_error_fn(422, ErrorCode.VALIDATION_FAILED, "pool_id does not match route", body.pool_id)
    return {
        "discussion_id": body.discussion_id or f"cdisc-{uuid.uuid4().hex[:16]}",
        "subject_type": subject_type,
        "subject_id": subject_id,
        "pool_id": pool_id,
        "parent_discussion_id": body.parent_discussion_id,
        "author": body.author or scope.user_id,
        "tenant_id": scope.tenant_id,
        "user_id": scope.user_id,
        "body": body.body,
        "kind": body.kind,
        "tags": body.tags,
        "resolved": body.resolved,
        "created_at": body.created_at or now,
        "updated_at": body.updated_at,
    }


def _validate_monitoring_body(
    body: CandidateMonitoringRequest,
    *,
    pool_id: str,
    artifact_id: str,
    bff_error_fn: Callable[..., HTTPException],
    error_code_enum_fn: Callable[[], Any],
) -> None:
    allowed_states = {"active", "paused", "graduated", "removed"}
    if body.monitoring_state not in allowed_states:
        ErrorCode = error_code_enum_fn()
        raise bff_error_fn(
            422, ErrorCode.VALIDATION_FAILED,
            "Candidate monitoring_state is not in the v1.4 contract enum",
            body.monitoring_state,
        )
    if body.pool_id and body.pool_id != pool_id:
        ErrorCode = error_code_enum_fn()
        raise bff_error_fn(422, ErrorCode.VALIDATION_FAILED, "pool_id does not match route", body.pool_id)
    if body.artifact_id and body.artifact_id != artifact_id:
        ErrorCode = error_code_enum_fn()
        raise bff_error_fn(422, ErrorCode.VALIDATION_FAILED, "artifact_id does not match route", body.artifact_id)


# ---------------------------------------------------------------------------
# Route context
# ---------------------------------------------------------------------------

@dataclass
class AgoraResearchRouteContext:
    extract_identity: Callable[..., Any]
    require_read_role: Callable[..., None]
    bff_error: Callable[..., HTTPException]
    utc_now: Callable[[], str]
    require_write_role: Optional[Callable[..., None]] = None
    store: Any = None
    dispatcher: Optional[ResearchDispatcher] = None
    workshop_store: Optional[Any] = None
    dataset_store: Optional[Any] = None
    service: Optional[Any] = None

    def __post_init__(self) -> None:
        raw_store = getattr(self, "store", None)
        if self.service is None and raw_store is not None:
            from ..service import AgoraResearchService
            self.service = AgoraResearchService(
                store=raw_store,
                dispatcher=self.dispatcher,
                workshop_store=self.workshop_store,
                dataset_store=self.dataset_store,
                utc_now=self.utc_now,
                bff_error=self.bff_error,
            )

    def error_code_enum(self) -> Any:
        try:
            from ....models import ErrorCode
            return ErrorCode
        except Exception:
            pass
        try:
            from ...models import ErrorCode
            return ErrorCode
        except Exception:
            pass
        try:
            from services.control_plane.bff.models import ErrorCode
            if hasattr(ErrorCode, "FORBIDDEN"):
                return ErrorCode
        except Exception:
            pass
        class FallbackErrorCode(str, Enum):
            AUTH_REQUIRED = "AUTH_REQUIRED"
            FORBIDDEN = "FORBIDDEN"
            RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
            VALIDATION_FAILED = "VALIDATION_FAILED"
            RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
            PRECONDITION_FAILED = "PRECONDITION_FAILED"
            OPERATION_NOT_ALLOWED = "OPERATION_NOT_ALLOWED"
            IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
        return FallbackErrorCode

    def read_scope(self, authorization: Optional[str], x_tenant_id: Optional[str] = None) -> Any:
        try:
            from ...identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
        except ImportError:
            from services.control_plane.bff.agora.identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope

        identity = self.extract_identity(authorization)
        self.require_read_role(identity)
        try:
            return resolve_agora_user_scope(
                identity,
                utc_now=self.utc_now,
                requested_tenant_id=x_tenant_id,
            )
        except AgoraScopeResolutionError as exc:
            ErrorCode = self.error_code_enum()
            code = ErrorCode.AUTH_REQUIRED if exc.status_code == 401 else ErrorCode.FORBIDDEN
            raise self.bff_error(
                exc.status_code, code, exc.message, exc.reason,
                precondition_failed="agora_user_scope",
                details_extra=exc.details,
            )

    def write_scope(self, authorization: Optional[str], x_tenant_id: Optional[str] = None) -> Any:
        try:
            from ...identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
            from ...models import AGORA_REQUIRED_ROLES
        except ImportError:
            from services.control_plane.bff.agora.identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
            from services.control_plane.bff.agora.models import AGORA_REQUIRED_ROLES

        identity = self.extract_identity(authorization)
        if self.require_write_role is not None:
            self.require_write_role(identity)
        else:
            roles = set(getattr(identity, "roles", []) or [])
            if not (roles & AGORA_REQUIRED_ROLES):
                ErrorCode = self.error_code_enum()
                raise self.bff_error(
                    403, ErrorCode.FORBIDDEN,
                    "Write authority required for Agora research mutations",
                    "operator_write_role_required",
                    suggestion="Ensure caller holds one of operator, approver, admin, reviewer roles",
                )
        try:
            return resolve_agora_user_scope(
                identity,
                utc_now=self.utc_now,
                requested_tenant_id=x_tenant_id,
            )
        except AgoraScopeResolutionError as exc:
            ErrorCode = self.error_code_enum()
            code = ErrorCode.AUTH_REQUIRED if exc.status_code == 401 else ErrorCode.FORBIDDEN
            raise self.bff_error(
                exc.status_code, code, exc.message, exc.reason,
                precondition_failed="agora_user_scope",
                details_extra=exc.details,
            )

    def require_idempotency_key(self, key: Optional[str]) -> None:
        if key is None:
            ErrorCode = self.error_code_enum()
            raise self.bff_error(
                400, ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key header is required",
                "missing_idempotency_key",
                suggestion="Supply a UUID v4 in the Idempotency-Key request header",
            )

    def check_idempotency(self, scope: Any, endpoint: str, key: str) -> None:
        self.service.check_idempotency(scope, endpoint, key)

    def require_if_match(self, header: Optional[str]) -> None:
        if header is None:
            ErrorCode = self.error_code_enum()
            raise self.bff_error(
                428, ErrorCode.PRECONDITION_FAILED,
                "If-Match header is required",
                "missing_if_match",
                suggestion="GET the resource first and supply the returned ETag in If-Match",
            )

    def check_plan_if_match(self, plan: Dict[str, Any], if_match: str) -> None:
        lock_version = plan.get("lock_version", 1)
        provided = _parse_plan_lock_version(if_match, plan["plan_id"])
        if provided != lock_version:
            ErrorCode = self.error_code_enum()
            raise self.bff_error(
                412, ErrorCode.PRECONDITION_FAILED,
                "ETag mismatch; plan was modified since it was last read",
                f"expected v{lock_version}, supplied ETag resolved to v{provided}",
            )

    def require_candidate_pool_if_match(self, pool: Dict[str, Any], if_match: Optional[str]) -> None:
        self.require_if_match(if_match)
        lock_version = int(pool.get("lock_version", 1))
        provided = _parse_candidate_pool_lock_version(if_match, pool["pool_id"])
        if provided != lock_version:
            ErrorCode = self.error_code_enum()
            raise self.bff_error(
                412, ErrorCode.PRECONDITION_FAILED,
                "ETag mismatch; candidate pool was modified since it was last read",
                f"expected v{lock_version}, supplied ETag resolved to v{provided}",
            )

    def get_plan_or_404(self, plan_id: str, scope: Any) -> Dict[str, Any]:
        return self.service.get_plan_or_404(plan_id, scope=scope)

    def get_run_or_404(self, run_id: str, scope: Any) -> Dict[str, Any]:
        return self.service.get_run_or_404(run_id, scope=scope)

    def get_candidate_pool_or_404(self, pool_id: str) -> Dict[str, Any]:
        return self.service.get_candidate_pool_or_404(pool_id)

    def require_pool_access(self, pool: Dict[str, Any], scope: Any) -> None:
        self.service.require_pool_access(pool, scope)

    def get_member_or_404(self, pool_id: str, artifact_id: str) -> Dict[str, Any]:
        return self.service.get_member_or_404(pool_id, artifact_id)

    def publish_research_event(self, workshop_id: str, event_type: str, data: Dict[str, Any]) -> None:
        publisher = _resolve_workshop_publisher()
        publisher(workshop_id, event_type, data, utc_now_fn=self.utc_now)

    def build_candidate_pool(self, body: CandidatePoolCreateRequest, scope: Any, now: str) -> Dict[str, Any]:
        return self.service.create_candidate_pool(body, scope=scope)

    def compute_and_store_candidate_scores(
        self,
        pool: Dict[str, Any],
        *,
        recipe_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self.service.compute_and_store_candidate_scores(pool, recipe_id=recipe_id)

    def member_projection(
        self,
        pool: Dict[str, Any],
        member: Dict[str, Any],
        scope: Any,
        recipe: Dict[str, Any],
        *,
        evidence_summary_mode: str = "list_response",
    ) -> Dict[str, Any]:
        return self.service.member_projection(pool, member, scope, recipe, evidence_summary_mode=evidence_summary_mode)


def _resolve_workshop_publisher() -> Callable[..., str]:
    import sys
    for mod_name in (
        "agora.strategy_workshop.events",
        "services.control_plane.bff.agora.strategy_workshop.events",
    ):
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            fn = getattr(mod, "ws_publish", None)
            if fn is not None:
                return fn
    try:
        from agora.strategy_workshop.events import ws_publish
        return ws_publish
    except ImportError:
        pass
    from services.control_plane.bff.agora.strategy_workshop.events import ws_publish
    return ws_publish


def publish_research_progress(
    workshop_id: str,
    run_id: str,
    progress_pct: float,
    message: str = "",
    *,
    phase: str = "running",
    utc_now_fn: Optional[Callable[[], str]] = None,
) -> str:
    publisher = _resolve_workshop_publisher()
    return publisher(
        workshop_id,
        "research.run.progress",
        {
            "run_id": run_id,
            "phase": phase,
            "percent": progress_pct,
            "message": message,
        },
        utc_now_fn=utc_now_fn,
    )


def publish_openclaw_degraded(
    workshop_id: str,
    reason: str = "OPENCLAW_UPSTREAM_DEGRADED",
    *,
    utc_now_fn: Optional[Callable[[], str]] = None,
) -> str:
    publisher = _resolve_workshop_publisher()
    return publisher(
        workshop_id,
        "workshop.openclaw.degraded",
        {
            "error_code": "OPENCLAW_UPSTREAM_DEGRADED",
            "reason": reason,
        },
        utc_now_fn=utc_now_fn,
    )
