"""Agora research router — agora.research.v1.

Implements the ResearchPlan facade per:
  - docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/MASTER_SD_RESPONSE.md §B
  - services/control-plane/specs/agora/v4/research_plan_execution.schema.json
  - services/control-plane/specs/agora/v4/research_run_projection.schema.json
  - services/control-plane/openapi/agora_v1_3.openapi.yaml (research plan/run routes)

Stage routing per §B3 of MASTER_SD_RESPONSE.md:
  source_discovery          → source_ingestion
  data_validation           → data_validation
  prototype_backtest        → vectorbt
  alpha_training            → qlib
  rolling_oos               → qlib
  econometric_validation    → statsmodels
  derivatives_pricing_risk  → quantlib
  policy_training           → finrl  (activation-gated)
  parameter_search          → ray_tune
  portfolio_synthesis       → optimizer_svc
  robustness_stress         → rllib
  evidence_synthesis        → openclaw_result_synthesis

Governance enforced here:
  - execution_constraints.environments must not include 'live' or 'canary'
  - no_order_route_proof on plans is always 'research_plan_no_order_route'
  - no_order_route_proof on runs  is always 'research_only_not_direct_action'
  - Agora never routes orders, binds capital, or writes RuntimeBinding
"""
from __future__ import annotations

import uuid
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from .store import make_research_plan_store


# ---------------------------------------------------------------------------
# Stage → preferred_backend routing policy (MASTER_SD_RESPONSE.md §B3)
# ---------------------------------------------------------------------------

_STAGE_TO_BACKEND: Dict[str, str] = {
    "source_discovery": "source_ingestion",
    "data_validation": "data_validation",
    "prototype_backtest": "vectorbt",
    "alpha_training": "qlib",
    "rolling_oos": "qlib",
    "econometric_validation": "statsmodels",
    "derivatives_pricing_risk": "quantlib",
    "policy_training": "finrl",
    "parameter_search": "ray_tune",
    "portfolio_synthesis": "optimizer_svc",
    "robustness_stress": "rllib",
    "evidence_synthesis": "openclaw_result_synthesis",
}

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
# AG-CAND-TRUTH-001-BE — candidate member truth projection (bundle v1.12)
#
# Every rendered candidate field is either linked to a durable record of the
# same candidate (with provenance + as-of) or explicitly typed unavailable.
# The BFF never fabricates rationale/concerns/next-event narrative; values are
# projected verbatim from the candidate's own score result, review, or
# monitoring records. See:
#   services/control-plane/specs/agora/v13/candidate_member_truth_projection.schema.json
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
    # Matches the last_score_result_id format used by candidate monitoring.
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
    """Return a score projection safe for lists and viewer-only detail reads."""
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
    """True when the caller holds an operator-level Agora role.

    Viewer-only callers keep read access but get evidence summaries redacted.
    """
    from ..models import AGORA_REQUIRED_ROLES  # noqa: PLC0415 (avoid import cycle)
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
    """Build the per-field truth projection for one candidate pool member.

    ``evidence_summary_mode`` is ``"list_response"`` (summaries always
    redacted; no private content in list responses) or ``"detail"``
    (summaries included only for operator-grade roles).
    """
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
        # No governed source artifact owns a next event for this candidate;
        # the BFF must not invent one.
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
    return f'W/"research-plan:{plan_id}:v{lock_version}"'


def _parse_plan_lock_version(if_match: str, plan_id: str) -> int:
    """Extract lock_version from ETag; returns 0 on parse failure (guaranteed conflict)."""
    prefix = f'W/"research-plan:{plan_id}:v'
    if if_match.startswith(prefix) and if_match.endswith('"'):
        try:
            return int(if_match[len(prefix):-1])
        except ValueError:
            pass
    return 0


def _candidate_pool_etag(pool_id: str, lock_version: int) -> str:
    return f'W/"candidate-pool:{pool_id}:v{lock_version}"'


def _parse_candidate_pool_lock_version(if_match: str, pool_id: str) -> int:
    prefix = f'W/"candidate-pool:{pool_id}:v'
    if if_match.startswith(prefix) and if_match.endswith('"'):
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
    """Return only governed refs persisted with the candidate metrics record.

    Recipe evidence requirements describe what should exist; they are not a
    durable evidence artifact and must never be converted into synthetic
    ``evidence://`` references.
    """
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


def _run_projection_with_defaults(run: Dict[str, Any]) -> Dict[str, Any]:
    """Return a ResearchRunProjection-shaped dict with stable optional arrays."""
    projected = dict(run)
    projected.setdefault("metrics", [])
    projected.setdefault("findings", [])
    projected.setdefault("warnings", [])
    projected.setdefault("blocking_reasons", [])
    projected.setdefault("artifact_refs", [])
    projected.setdefault("evidence_refs", [])
    projected.setdefault("lineage_refs", [])
    return projected


def _build_run_projection(
    *,
    plan: Dict[str, Any],
    stage: Dict[str, Any],
    run_id: str,
    now: str,
) -> Dict[str, Any]:
    routing = stage.get("routing", {})
    backend = routing.get("effective_backend") or routing.get("preferred_backend", "")
    run = {
        "spec_version": "1.0",
        "run_id": run_id,
        "plan_id": plan["plan_id"],
        "workshop_id": plan["workshop_id"],
        "strategy_id": plan["strategy_id"],
        "strategy_spec_registry_id": plan["strategy_spec_registry_id"],
        "stage_id": stage["stage_id"],
        "stage_type": stage["stage_type"],
        "execution_status": "queued",
        "outcome": "pending",
        "progress": {
            "phase": "queued",
            "percent": 0,
            "message": "Run queued for dispatch",
            "updated_at": now,
        },
        "backend": {
            "requested": routing.get("preferred_backend", ""),
            "effective": backend,
            "mode": routing.get("backend_mode", "real"),
        },
        "no_order_route_proof": _RUN_NO_ORDER_ROUTE_PROOF,
        "created_at": now,
        "updated_at": now,
    }
    return _run_projection_with_defaults(run)


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def publish_research_progress(
    workshop_id: str,
    run_id: str,
    progress_pct: float,
    message: str = "",
    *,
    phase: str = "running",
    utc_now_fn: Optional[Callable[[], str]] = None,
) -> str:
    """Publish a canonical research.run.progress event to the workshop SSE stream.

    Imports _ws_publish lazily to avoid a circular import with the workshop router.
    Returns the SSE event_id, or an empty string if the workshop has no active stream.
    """
    from agora.strategy_workshop.router import _ws_publish  # noqa: PLC0415 (lazy import)
    return _ws_publish(
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
    """Publish a workshop.openclaw.degraded event when OpenClaw is unreachable.

    Callers should invoke this whenever they detect OpenClaw degradation while
    serving a request for a specific workshop.  The event carries the canonical
    error_code OPENCLAW_UPSTREAM_DEGRADED so the frontend can show a graceful
    degraded state.
    """
    from agora.strategy_workshop.router import _ws_publish  # noqa: PLC0415 (lazy import)
    return _ws_publish(
        workshop_id,
        "workshop.openclaw.degraded",
        {
            "error_code": "OPENCLAW_UPSTREAM_DEGRADED",
            "reason": reason,
        },
        utc_now_fn=utc_now_fn,
    )


def create_research_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    research_plan_store: Any = None,
) -> APIRouter:
    """Build and return the Agora research APIRouter.

    ``research_plan_store`` may be injected for tests.
    When omitted the store is constructed from AGORA_RESEARCH_PLAN_STORE_BACKEND env.
    """
    store = research_plan_store if research_plan_store is not None else make_research_plan_store()
    router = APIRouter(tags=["agora-research"])

    def _scope(authorization: Optional[str], x_tenant_id: Optional[str] = None) -> Any:
        from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope

        identity = extract_identity(authorization)
        require_read_role(identity)
        try:
            return resolve_agora_user_scope(
                identity,
                utc_now=utc_now,
                requested_tenant_id=x_tenant_id,
            )
        except AgoraScopeResolutionError as exc:
            from models import ErrorCode
            code = ErrorCode.AUTH_REQUIRED if exc.status_code == 401 else ErrorCode.FORBIDDEN
            raise bff_error(
                exc.status_code, code, exc.message, exc.reason,
                precondition_failed="agora_user_scope",
                details_extra=exc.details,
            )

    def _require_idempotency_key(key: Optional[str]) -> None:
        if key is None:
            from models import ErrorCode
            raise bff_error(
                400, ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key header is required",
                "missing_idempotency_key",
                suggestion="Supply a UUID v4 in the Idempotency-Key request header",
            )

    def _check_idempotency(scope: Any, endpoint: str, key: str) -> None:
        scope_str = f"{scope.user_id}:{scope.tenant_id}:{endpoint}"
        if store.check_and_record_idempotency_key(scope_str, key):
            from models import ErrorCode
            raise bff_error(409, ErrorCode.IDEMPOTENCY_CONFLICT, "Duplicate Idempotency-Key", key)

    def _require_if_match(header: Optional[str]) -> None:
        if header is None:
            from models import ErrorCode
            raise bff_error(
                428, ErrorCode.PRECONDITION_FAILED,
                "If-Match header is required",
                "missing_if_match",
                suggestion="GET the plan first and supply the returned ETag in If-Match",
            )

    def _check_plan_if_match(plan: Dict[str, Any], if_match: str) -> None:
        lock_version = plan.get("lock_version", 1)
        provided = _parse_plan_lock_version(if_match, plan["plan_id"])
        if provided != lock_version:
            from models import ErrorCode
            raise bff_error(
                412, ErrorCode.PRECONDITION_FAILED,
                "ETag mismatch; plan was modified since it was last read",
                f"expected v{lock_version}, supplied ETag resolved to v{provided}",
            )

    def _validate_create_body(body: ResearchPlanCreateRequest, workshop_id: str) -> None:
        from models import ErrorCode
        if body.spec_version != "1.0":
            raise bff_error(
                422, ErrorCode.VALIDATION_FAILED,
                "spec_version must be '1.0'",
                f"received: {body.spec_version!r}",
            )
        # Governance gate: environments must not include live or canary
        if body.execution_constraints and body.execution_constraints.environments:
            forbidden = _FORBIDDEN_ENVIRONMENTS & set(body.execution_constraints.environments)
            if forbidden:
                raise bff_error(
                    422, ErrorCode.VALIDATION_FAILED,
                    f"execution_constraints.environments must not include: {', '.join(sorted(forbidden))}",
                    "forbidden_environments",
                )
        # Validate each stage type
        for i, stage in enumerate(body.stages):
            if stage.stage_type not in _VALID_STAGE_TYPES:
                raise bff_error(
                    422, ErrorCode.VALIDATION_FAILED,
                    f"stages[{i}].stage_type '{stage.stage_type}' is not a recognised stage type",
                    stage.stage_type,
                )

    def _build_plan(
        body: ResearchPlanCreateRequest,
        workshop_id: str,
        plan_id: str,
        now: str,
    ) -> Dict[str, Any]:
        """Construct a normalised ResearchPlanExecution dict from the request."""
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

        plan: Dict[str, Any] = {
            "spec_version": "1.0",
            "plan_id": plan_id,
            "workshop_id": workshop_id,
            "strategy_id": body.strategy_id,
            "strategy_spec_registry_id": body.strategy_spec_registry_id,
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

    def _publish_research_event(workshop_id: str, event_type: str, data: Dict[str, Any]) -> None:
        from agora.strategy_workshop.router import _ws_publish  # noqa: PLC0415 (lazy import)

        _ws_publish(workshop_id, event_type, data, utc_now_fn=utc_now)

    def _require_candidate_idempotency(endpoint: str, scope: Any, key: Optional[str]) -> None:
        _require_idempotency_key(key)
        _check_idempotency(scope, endpoint, key)  # type: ignore[arg-type]

    def _require_candidate_pool_if_match(pool: Dict[str, Any], if_match: Optional[str]) -> None:
        _require_if_match(if_match)
        lock_version = int(pool.get("lock_version", 1))
        provided = _parse_candidate_pool_lock_version(if_match, pool["pool_id"])  # type: ignore[arg-type]
        if provided != lock_version:
            from models import ErrorCode
            raise bff_error(
                412, ErrorCode.PRECONDITION_FAILED,
                "ETag mismatch; candidate pool was modified since it was last read",
                f"expected v{lock_version}, supplied ETag resolved to v{provided}",
            )

    def _get_candidate_pool_or_404(pool_id: str) -> Dict[str, Any]:
        pool = store.get_candidate_pool(pool_id)
        if pool is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Candidate pool not found", pool_id)
        return pool

    def _require_pool_access(pool: Dict[str, Any], scope: Any) -> None:
        if pool.get("tenant_id") != scope.tenant_id or pool.get("user_id") != scope.user_id:
            from models import ErrorCode
            raise bff_error(403, ErrorCode.FORBIDDEN, "Candidate pool not owned by caller", pool["pool_id"])

    def _get_member_or_404(pool_id: str, artifact_id: str) -> Dict[str, Any]:
        member = store.get_candidate_member(pool_id, artifact_id)
        if member is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Candidate pool member not found", artifact_id)
        return member

    def _validate_pool_filter(pool_filter: Dict[str, Any]) -> None:
        allowed_create_states = {"candidate", "review", "approved"}
        requested_states = set(pool_filter.get("lifecycle_states") or ["candidate"])
        unknown = requested_states - allowed_create_states
        if unknown:
            from models import ErrorCode
            raise bff_error(
                422, ErrorCode.VALIDATION_FAILED,
                "candidate pool filter lifecycle_states must use the v1.4 create-pool enum",
                f"unsupported lifecycle_states: {sorted(unknown)}",
            )

    def _build_candidate_pool(body: CandidatePoolCreateRequest, scope: Any, now: str) -> Dict[str, Any]:
        operator_id = getattr(scope, "operator_id", scope.user_id)
        if body.operator_id not in {scope.user_id, operator_id}:
            from models import ErrorCode
            raise bff_error(
                422, ErrorCode.VALIDATION_FAILED,
                "operator_id must match the authenticated Agora operator",
                f"operator_id={body.operator_id!r}, authenticated={operator_id!r}",
            )
        recipe = _load_default_scoring_recipe()
        if body.recipe_id and body.recipe_id != recipe["recipe_id"]:
            from models import ErrorCode
            raise bff_error(
                422, ErrorCode.VALIDATION_FAILED,
                "Only the active winner-branch CandidateScoringRecipe is available in this BFF slice",
                body.recipe_id,
            )
        pool_filter = (
            body.filter.model_dump()
            if body.filter is not None
            else CandidatePoolFilterRequest().model_dump()
        )
        _validate_pool_filter(pool_filter)
        candidates: List[Dict[str, Any]] = []
        metrics_by_artifact: Dict[str, Dict[str, Any]] = {}
        for candidate in _default_registry_candidates(now):
            if not _candidate_matches_filter(candidate, pool_filter):
                continue
            public_candidate = _candidate_public_member(candidate)
            # Persist the member mutation clock without widening the frozen
            # v1.4 public member shape. Projection helpers strip private keys.
            public_candidate["_updated_at"] = str(
                candidate.get("_updated_at")
                or public_candidate.get("created_at")
                or now
            )
            candidates.append(public_candidate)
            metrics_by_artifact[public_candidate["artifact_id"]] = candidate.get("_metrics") or {}

        pool_id = f"cpool-{uuid.uuid4().hex[:16]}"
        strategy_family = (
            pool_filter.get("strategy_families", [None])[0]
            if pool_filter.get("strategy_families")
            else recipe.get("strategy_family")
        )
        pool = {
            "spec_version": "1.0",
            "pool_id": pool_id,
            "operator_id": body.operator_id,
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "filter": pool_filter,
            "candidates": candidates,
            "total": len(candidates),
            "snapshot_at": now,
            "lock_version": 1,
            "metadata": {
                "strategy_family": strategy_family,
                "recipe_id": recipe["recipe_id"],
                "recipe_version": int(recipe["version"]),
                "data_cutoff": now,
                "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
            },
        }
        return store.create_candidate_pool(pool, metrics_by_artifact=metrics_by_artifact)

    def _compute_and_store_candidate_scores(
        pool: Dict[str, Any],
        *,
        recipe_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        recipe = _load_default_scoring_recipe()
        if recipe_id and recipe_id != recipe["recipe_id"]:
            from models import ErrorCode
            raise bff_error(
                422, ErrorCode.VALIDATION_FAILED,
                "Unknown CandidateScoringRecipe for candidate pool score run",
                recipe_id,
            )
        pool_id = pool["pool_id"]
        scored_at = utc_now()
        data_cutoff = (pool.get("metadata") or {}).get("data_cutoff") or pool.get("snapshot_at") or scored_at
        scores = _rank_scores([
            _score_candidate(
                pool_id=pool_id,
                candidate=candidate,
                metrics=store.get_candidate_metrics(pool_id, candidate["artifact_id"]),
                recipe=recipe,
                data_cutoff=data_cutoff,
                scored_at=scored_at,
            )
            for candidate in pool.get("candidates", [])
        ])
        store.replace_candidate_scores(
            pool_id,
            {score["candidate_id"]: score for score in scores},
        )
        metadata = dict(pool.get("metadata") or {})
        metadata.update({
            "recipe_id": recipe["recipe_id"],
            "recipe_version": int(recipe["version"]),
            "last_score_run_at": scored_at,
            "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
        })
        store.update_candidate_pool(pool_id, {
            "metadata": metadata,
            "lock_version": int(pool.get("lock_version", 1)) + 1,
        })
        return scores

    def _member_projection(
        pool: Dict[str, Any],
        member: Dict[str, Any],
        scope: Any,
        recipe: Dict[str, Any],
        *,
        evidence_summary_mode: str = "list_response",
    ) -> Dict[str, Any]:
        pool_id = pool["pool_id"]
        artifact_id = member["artifact_id"]
        score = store.get_candidate_score(pool_id, artifact_id)
        projection = _candidate_public_member(member)
        if score is not None:
            projection["current_score"] = _score_without_private_explanations(score)
            projection["band"] = score["band"]
            projection["rank"] = score["rank"]
            projection["effective_score"] = score["effective_score"]
        projection.update(
            _member_truth_projection(
                pool=pool,
                member=member,
                score=score,
                reviews=store.list_candidate_reviews(pool_id, artifact_id),
                monitoring=store.get_candidate_monitoring(pool_id, artifact_id),
                recipe=recipe,
                evidence_summary_mode=evidence_summary_mode,
                operator_grade=_operator_grade_scope(scope),
            )
        )
        return projection

    def _parse_member_page_token(page_token: Optional[str]) -> int:
        if page_token in (None, ""):
            return 0
        if isinstance(page_token, str) and page_token.startswith(_MEMBER_PAGE_TOKEN_PREFIX):
            suffix = page_token[len(_MEMBER_PAGE_TOKEN_PREFIX):]
            if suffix.isdigit():
                return int(suffix)
        from models import ErrorCode
        raise bff_error(
            422, ErrorCode.VALIDATION_FAILED,
            "Invalid candidate member page_token",
            str(page_token),
        )

    def _validate_review_body(body: CandidateMemberReviewRequest) -> None:
        allowed = set(_REVIEW_DECISION_TO_LIFECYCLE)
        if body.decision not in allowed:
            from models import ErrorCode
            raise bff_error(
                422, ErrorCode.VALIDATION_FAILED,
                "Candidate review decision is not in the v1.4 contract enum",
                body.decision,
            )
        if body.decision in {"reject", "park"} and not (body.rationale or "").strip():
            from models import ErrorCode
            raise bff_error(
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
    ) -> Dict[str, Any]:
        allowed_kinds = {"comment", "research_task", "score_question", "risk_flag", "approval_note"}
        if body.kind not in allowed_kinds:
            from models import ErrorCode
            raise bff_error(
                422, ErrorCode.VALIDATION_FAILED,
                "Candidate discussion kind is not in the v1.4 contract enum",
                body.kind,
            )
        if body.subject_type and body.subject_type != subject_type:
            from models import ErrorCode
            raise bff_error(422, ErrorCode.VALIDATION_FAILED, "subject_type does not match route", body.subject_type)
        if body.subject_id and body.subject_id != subject_id:
            from models import ErrorCode
            raise bff_error(422, ErrorCode.VALIDATION_FAILED, "subject_id does not match route", body.subject_id)
        if body.pool_id and body.pool_id != pool_id:
            from models import ErrorCode
            raise bff_error(422, ErrorCode.VALIDATION_FAILED, "pool_id does not match route", body.pool_id)
        return {
            "discussion_id": body.discussion_id or f"cdisc-{uuid.uuid4().hex[:16]}",
            "subject_type": subject_type,
            "subject_id": subject_id,
            "pool_id": pool_id,
            "parent_discussion_id": body.parent_discussion_id,
            "author": body.author or scope.user_id,
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
    ) -> None:
        allowed_states = {"active", "paused", "graduated", "removed"}
        if body.monitoring_state not in allowed_states:
            from models import ErrorCode
            raise bff_error(
                422, ErrorCode.VALIDATION_FAILED,
                "Candidate monitoring_state is not in the v1.4 contract enum",
                body.monitoring_state,
            )
        if body.pool_id and body.pool_id != pool_id:
            from models import ErrorCode
            raise bff_error(422, ErrorCode.VALIDATION_FAILED, "pool_id does not match route", body.pool_id)
        if body.artifact_id and body.artifact_id != artifact_id:
            from models import ErrorCode
            raise bff_error(422, ErrorCode.VALIDATION_FAILED, "artifact_id does not match route", body.artifact_id)

    # -------------------------------------------------------------------
    # GET /bff/agora/candidate-pools
    # -------------------------------------------------------------------
    @router.get("/bff/agora/candidate-pools")
    def list_candidate_pools(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        lifecycle_state: Optional[str] = Query(default=None),
        strategy_family: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        pools = store.list_candidate_pools(
            user_id=scope.user_id,
            tenant_id=scope.tenant_id,
            lifecycle_state=lifecycle_state,
            strategy_family=strategy_family,
        )
        return _candidate_list_envelope(
            items=[_public_candidate_pool(pool) for pool in pools[:page_size]],
            utc_now=utc_now,
            scope=scope,
        )

    # -------------------------------------------------------------------
    # POST /bff/agora/candidate-pools
    # -------------------------------------------------------------------
    @router.post("/bff/agora/candidate-pools", status_code=201)
    def create_candidate_pool(
        body: CandidatePoolCreateRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        _require_candidate_idempotency(scope=scope, endpoint="POST:/bff/agora/candidate-pools", key=idempotency_key)
        pool = _build_candidate_pool(body, scope, utc_now())
        return _candidate_pool_detail_envelope(pool=pool, utc_now=utc_now, scope=scope)

    # -------------------------------------------------------------------
    # GET /bff/agora/candidate-pools/{pool_id}
    # -------------------------------------------------------------------
    @router.get("/bff/agora/candidate-pools/{pool_id}")
    def get_candidate_pool(
        pool_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        pool = _get_candidate_pool_or_404(pool_id)
        _require_pool_access(pool, scope)
        etag = _candidate_pool_etag(pool_id, int(pool.get("lock_version", 1)))
        response.headers["ETag"] = etag
        return _candidate_pool_detail_envelope(pool=pool, utc_now=utc_now, scope=scope)

    # -------------------------------------------------------------------
    # GET /bff/agora/candidate-pools/{pool_id}/score
    # -------------------------------------------------------------------
    @router.get("/bff/agora/candidate-pools/{pool_id}/score")
    def get_candidate_pool_score(
        pool_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        pool = _get_candidate_pool_or_404(pool_id)
        _require_pool_access(pool, scope)
        scores = store.list_candidate_scores(pool_id)
        if not scores:
            return {
                "status": "queued",
                "data": {"pool_id": pool_id, "score_results": 0},
                "meta": {
                    "snapshot_at": utc_now(),
                    "capability": _CAPABILITY,
                    "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                    "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
                },
            }
        scores.sort(
            key=lambda score: (
                score.get("rank") is None,
                int(score.get("rank") or 999999),
                -float(score.get("effective_score") or 0.0),
            )
        )
        return _candidate_list_envelope(
            items=[_score_without_private_explanations(score) for score in scores],
            utc_now=utc_now,
            scope=scope,
        )

    # -------------------------------------------------------------------
    # POST /bff/agora/candidate-pools/{pool_id}/score
    # -------------------------------------------------------------------
    @router.post("/bff/agora/candidate-pools/{pool_id}/score", status_code=202)
    def trigger_candidate_pool_score(
        pool_id: str,
        body: Optional[CandidateScoreRunRequest] = None,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        pool = _get_candidate_pool_or_404(pool_id)
        _require_pool_access(pool, scope)
        _require_candidate_pool_if_match(pool, if_match)
        _require_candidate_idempotency(
            scope=scope,
            endpoint=f"POST:/bff/agora/candidate-pools/{pool_id}/score",
            key=idempotency_key,
        )
        request = body or CandidateScoreRunRequest()
        scores = _compute_and_store_candidate_scores(pool, recipe_id=request.recipe_id)
        return {
            "status": "completed",
            "data": {
                "pool_id": pool_id,
                "score_results": len(scores),
                "recipe_id": scores[0]["recipe_id"] if scores else (request.recipe_id or None),
                "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
            },
            "meta": {
                "snapshot_at": utc_now(),
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
            },
        }

    # -------------------------------------------------------------------
    # GET /bff/agora/candidate-pools/{pool_id}/members
    # -------------------------------------------------------------------
    @router.get("/bff/agora/candidate-pools/{pool_id}/members")
    def list_candidate_pool_members(
        pool_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        lifecycle_state: Optional[str] = Query(default=None),
        band: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=50, ge=1, le=200),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        pool = _get_candidate_pool_or_404(pool_id)
        _require_pool_access(pool, scope)
        recipe = _load_default_scoring_recipe()
        ordered = sorted(
            pool.get("candidates", []),
            key=lambda member: (
                str(member.get("created_at") or ""),
                str(member.get("artifact_id") or ""),
            ),
        )
        members = []
        for member in ordered:
            if lifecycle_state and member.get("lifecycle_state") != lifecycle_state:
                continue
            projection = _member_projection(pool, member, scope, recipe)
            if band and projection.get("band") != band:
                continue
            members.append(projection)
        offset = _parse_member_page_token(page_token)
        page = members[offset:offset + page_size]
        next_token = (
            f"{_MEMBER_PAGE_TOKEN_PREFIX}{offset + page_size}"
            if offset + page_size < len(members)
            else None
        )
        metadata = pool.get("metadata") or {}
        return _candidate_list_envelope(
            items=page,
            utc_now=utc_now,
            scope=scope,
            page_info={
                "next_page_token": next_token,
                "page_size": len(page),
                "has_more": next_token is not None,
                "total": len(members),
                "order_by": _MEMBER_ORDER_BY,
            },
            meta_extra={
                "freshness": {
                    "pool_snapshot_at": pool.get("snapshot_at"),
                    "data_cutoff": metadata.get("data_cutoff"),
                    "last_score_run_at": metadata.get("last_score_run_at"),
                },
            },
        )

    # -------------------------------------------------------------------
    # GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}
    # -------------------------------------------------------------------
    @router.get("/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}")
    def get_candidate_pool_member(
        pool_id: str,
        artifact_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        pool = _get_candidate_pool_or_404(pool_id)
        _require_pool_access(pool, scope)
        member = _get_member_or_404(pool_id, artifact_id)
        score = store.get_candidate_score(pool_id, artifact_id)
        reviews = store.list_candidate_reviews(pool_id, artifact_id)
        monitoring = store.get_candidate_monitoring(pool_id, artifact_id)
        operator_grade = _operator_grade_scope(scope)
        truth = _member_truth_projection(
            pool=pool,
            member=member,
            score=score,
            reviews=reviews,
            monitoring=monitoring,
            recipe=_load_default_scoring_recipe(),
            evidence_summary_mode="detail",
            operator_grade=operator_grade,
        )
        data = {
            "candidate": _candidate_public_member(member),
            "score": (
                score
                if score is None or operator_grade
                else _score_without_private_explanations(score)
            ),
            "reviews": reviews,
            "monitoring": monitoring,
            "negative_examples": [
                review for review in reviews
                if review.get("negative_example") is True
            ],
            "lifecycle_state": member.get("lifecycle_state"),
            **truth,
        }
        return _candidate_detail_envelope(
            pool=pool,
            artifact_id=artifact_id,
            data=data,
            utc_now=utc_now,
            scope=scope,
        )

    # -------------------------------------------------------------------
    # POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/review
    # -------------------------------------------------------------------
    @router.post("/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/review")
    def review_candidate_pool_member(
        pool_id: str,
        artifact_id: str,
        body: CandidateMemberReviewRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        pool = _get_candidate_pool_or_404(pool_id)
        _require_pool_access(pool, scope)
        _require_candidate_pool_if_match(pool, if_match)
        _require_candidate_idempotency(
            scope=scope,
            endpoint=f"POST:/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/review",
            key=idempotency_key,
        )
        member = _get_member_or_404(pool_id, artifact_id)
        if member.get("lifecycle_state") == "rejected":
            from models import ErrorCode
            raise bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                "Rejected candidate members are immutable retained negative examples",
                artifact_id,
            )
        _validate_review_body(body)
        now = utc_now()
        next_lifecycle = _REVIEW_DECISION_TO_LIFECYCLE[body.decision]
        review = {
            "review_id": str(uuid.uuid4()),
            "artifact_id": artifact_id,
            "decision": body.decision,
            "rationale": body.rationale,
            "score_override": body.score_override,
            "reviewed_by": body.reviewed_by,
            "reviewed_at": body.reviewed_at or now,
            "negative_example_tags": body.negative_example_tags,
            "negative_example": body.decision in {"reject", "park"},
            "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
        }
        store.add_candidate_review(pool_id, artifact_id, review)
        updated_member = store.update_candidate_member(
            pool_id,
            artifact_id,
            {
                "lifecycle_state": next_lifecycle,
                "_updated_at": now,
            },
        )
        metadata = dict(pool.get("metadata") or {})
        metadata["last_reviewed_at"] = now
        store.update_candidate_pool(pool_id, {
            "metadata": metadata,
            "lock_version": int(pool.get("lock_version", 1)) + 1,
        })
        return {
            "status": "completed",
            "data": {
                "pool_id": pool_id,
                "artifact_id": artifact_id,
                "decision": body.decision,
                "candidate": (
                    _candidate_public_member(updated_member)
                    if updated_member is not None
                    else None
                ),
                "review": review,
                "negative_example": review["negative_example"],
                "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
            },
            "meta": {
                "snapshot_at": now,
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
            },
        }

    # -------------------------------------------------------------------
    # GET /bff/agora/candidate-pools/{pool_id}/discussions
    # -------------------------------------------------------------------
    @router.get("/bff/agora/candidate-pools/{pool_id}/discussions")
    def list_candidate_pool_discussions(
        pool_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        kind: Optional[str] = Query(default=None),
        resolved: Optional[bool] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        pool = _get_candidate_pool_or_404(pool_id)
        _require_pool_access(pool, scope)
        discussions = store.list_candidate_discussions(
            pool_id,
            subject_type="pool",
            kind=kind,
            resolved=resolved,
        )
        return _candidate_list_envelope(items=discussions, utc_now=utc_now, scope=scope)

    # -------------------------------------------------------------------
    # POST /bff/agora/candidate-pools/{pool_id}/discussions
    # -------------------------------------------------------------------
    @router.post("/bff/agora/candidate-pools/{pool_id}/discussions", status_code=201)
    def create_candidate_pool_discussion(
        pool_id: str,
        body: CandidateDiscussionRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        pool = _get_candidate_pool_or_404(pool_id)
        _require_pool_access(pool, scope)
        _require_candidate_idempotency(
            scope=scope,
            endpoint=f"POST:/bff/agora/candidate-pools/{pool_id}/discussions",
            key=idempotency_key,
        )
        discussion = _discussion_record(
            body=body,
            pool_id=pool_id,
            subject_type="pool",
            subject_id=pool_id,
            scope=scope,
            now=utc_now(),
        )
        discussion = store.add_candidate_discussion(discussion)
        return _candidate_detail_envelope(
            pool=pool,
            artifact_id=discussion["discussion_id"],
            data=discussion,
            utc_now=utc_now,
            scope=scope,
            object_type="candidate_discussion",
        )

    # -------------------------------------------------------------------
    # GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions
    # -------------------------------------------------------------------
    @router.get("/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions")
    def list_candidate_member_discussions(
        pool_id: str,
        artifact_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        resolved: Optional[bool] = Query(default=None),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        pool = _get_candidate_pool_or_404(pool_id)
        _require_pool_access(pool, scope)
        _get_member_or_404(pool_id, artifact_id)
        discussions = store.list_candidate_discussions(
            pool_id,
            subject_type="member",
            subject_id=artifact_id,
            resolved=resolved,
        )
        return _candidate_list_envelope(items=discussions, utc_now=utc_now, scope=scope)

    # -------------------------------------------------------------------
    # POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions
    # -------------------------------------------------------------------
    @router.post("/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions", status_code=201)
    def create_candidate_member_discussion(
        pool_id: str,
        artifact_id: str,
        body: CandidateDiscussionRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        pool = _get_candidate_pool_or_404(pool_id)
        _require_pool_access(pool, scope)
        _get_member_or_404(pool_id, artifact_id)
        _require_candidate_idempotency(
            scope=scope,
            endpoint=f"POST:/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions",
            key=idempotency_key,
        )
        discussion = _discussion_record(
            body=body,
            pool_id=pool_id,
            subject_type="member",
            subject_id=artifact_id,
            scope=scope,
            now=utc_now(),
        )
        discussion = store.add_candidate_discussion(discussion)
        return _candidate_detail_envelope(
            pool=pool,
            artifact_id=discussion["discussion_id"],
            data=discussion,
            utc_now=utc_now,
            scope=scope,
            object_type="candidate_discussion",
        )

    # -------------------------------------------------------------------
    # GET /bff/agora/candidate-pools/{pool_id}/monitoring
    # -------------------------------------------------------------------
    @router.get("/bff/agora/candidate-pools/{pool_id}/monitoring")
    def list_candidate_pool_monitoring(
        pool_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        monitoring_state: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        pool = _get_candidate_pool_or_404(pool_id)
        _require_pool_access(pool, scope)
        monitoring = store.list_candidate_monitoring(pool_id, monitoring_state=monitoring_state)
        return _candidate_list_envelope(items=monitoring, utc_now=utc_now, scope=scope)

    # -------------------------------------------------------------------
    # POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor
    # -------------------------------------------------------------------
    @router.post("/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor", status_code=201)
    def add_candidate_to_monitoring(
        pool_id: str,
        artifact_id: str,
        body: CandidateMonitoringRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        pool = _get_candidate_pool_or_404(pool_id)
        _require_pool_access(pool, scope)
        _require_candidate_pool_if_match(pool, if_match)
        _get_member_or_404(pool_id, artifact_id)
        _require_candidate_idempotency(
            scope=scope,
            endpoint=f"POST:/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor",
            key=idempotency_key,
        )
        _validate_monitoring_body(body, pool_id=pool_id, artifact_id=artifact_id)
        member = store.get_candidate_member(pool_id, artifact_id)
        if member is None or member.get("lifecycle_state") != "approved":
            from models import ErrorCode
            raise bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                "Candidate must be approved_for_monitoring before it can enter monitoring",
                artifact_id,
            )
        existing = store.get_candidate_monitoring(pool_id, artifact_id)
        if existing is not None and existing.get("monitoring_state") != "removed":
            from models import ErrorCode
            raise bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                "Candidate already has an active monitoring entry",
                artifact_id,
            )
        now = utc_now()
        score = store.get_candidate_score(pool_id, artifact_id)
        monitoring = {
            "artifact_id": artifact_id,
            "pool_id": pool_id,
            "monitoring_state": body.monitoring_state,
            "trigger_conditions": body.trigger_conditions,
            "last_score_result_id": (
                body.last_score_result_id
                or (f"candidate-score:{pool_id}:{artifact_id}:{score['scored_at']}" if score else None)
            ),
            "review_due_at": body.review_due_at,
            "added_by": body.added_by or scope.user_id,
            "added_at": body.added_at or now,
            "notes": body.notes or "",
        }
        monitoring = store.upsert_candidate_monitoring(pool_id, artifact_id, monitoring)
        store.update_candidate_pool(pool_id, {"lock_version": int(pool.get("lock_version", 1)) + 1})
        return _candidate_detail_envelope(
            pool={**pool, "lock_version": int(pool.get("lock_version", 1)) + 1},
            artifact_id=artifact_id,
            data=monitoring,
            utc_now=utc_now,
            scope=scope,
            object_type="candidate_monitoring",
        )

    # -------------------------------------------------------------------
    # DELETE /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor
    # -------------------------------------------------------------------
    @router.delete("/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor")
    def remove_candidate_from_monitoring(
        pool_id: str,
        artifact_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        pool = _get_candidate_pool_or_404(pool_id)
        _require_pool_access(pool, scope)
        _require_candidate_pool_if_match(pool, if_match)
        monitoring = store.get_candidate_monitoring(pool_id, artifact_id)
        if monitoring is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Candidate monitoring entry not found", artifact_id)
        updated = {**monitoring, "monitoring_state": "removed"}
        store.upsert_candidate_monitoring(pool_id, artifact_id, updated)
        store.update_candidate_pool(pool_id, {"lock_version": int(pool.get("lock_version", 1)) + 1})
        return {
            "status": "completed",
            "data": {
                "pool_id": pool_id,
                "artifact_id": artifact_id,
                "monitoring_state": "removed",
                "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
            },
            "meta": {
                "snapshot_at": utc_now(),
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
            },
        }

    # -------------------------------------------------------------------
    # GET /bff/agora/workshops/{workshop_id}/research-plans
    # -------------------------------------------------------------------
    @router.get("/bff/agora/workshops/{workshop_id}/research-plans")
    def list_workshop_research_plans(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        cursor: Optional[str] = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        plans = store.list_plans_for_workshop(workshop_id)
        return {
            "items": plans,
            "page_info": {
                "next_page_token": None,
                "page_size": len(plans),
                "has_more": False,
                "total": len(plans),
            },
            "meta": {
                "snapshot_at": utc_now(),
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # -------------------------------------------------------------------
    # POST /bff/agora/workshops/{workshop_id}/research-plans
    # -------------------------------------------------------------------
    @router.post("/bff/agora/workshops/{workshop_id}/research-plans", status_code=201)
    def create_workshop_research_plan(
        workshop_id: str,
        body: ResearchPlanCreateRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        _require_idempotency_key(idempotency_key)
        _check_idempotency(
            scope,
            f"POST:/bff/agora/workshops/{workshop_id}/research-plans",
            idempotency_key,  # type: ignore[arg-type]
        )
        _validate_create_body(body, workshop_id)
        now = utc_now()
        plan_id = str(uuid.uuid4())
        plan = _build_plan(body, workshop_id, plan_id, now)
        plan = store.create_plan(plan)
        _publish_research_event(
            workshop_id,
            "research.plan.created",
            {"plan_id": plan_id, "status": plan["status"]},
        )
        return _plan_detail_envelope(plan, utc_now, scope)

    # -------------------------------------------------------------------
    # GET /bff/agora/research-plans/{plan_id}
    # -------------------------------------------------------------------
    @router.get("/bff/agora/research-plans/{plan_id}")
    def get_agora_research_plan(
        plan_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        plan = store.get_plan(plan_id)
        if plan is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Research plan not found", plan_id)
        return _plan_detail_envelope(plan, utc_now, scope)

    # -------------------------------------------------------------------
    # POST /bff/agora/research-plans/{plan_id}/approve
    # -------------------------------------------------------------------
    @router.post("/bff/agora/research-plans/{plan_id}/approve")
    def approve_agora_research_plan(
        plan_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        _require_idempotency_key(idempotency_key)
        _require_if_match(if_match)
        _check_idempotency(
            scope,
            f"POST:/bff/agora/research-plans/{plan_id}/approve",
            idempotency_key,  # type: ignore[arg-type]
        )
        plan = store.get_plan(plan_id)
        if plan is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Research plan not found", plan_id)
        _check_plan_if_match(plan, if_match)  # type: ignore[arg-type]
        if plan["status"] != "draft":
            from models import ErrorCode
            raise bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                f"Plan cannot be approved from status '{plan['status']}'",
                f"expected status 'draft', got '{plan['status']}'",
            )
        now = utc_now()
        store.update_plan(plan_id, {
            "status": "approved",
            "approved_at": now,
            "approval": {
                "state": "approved",
                "decided_by": scope.user_id,
                "decided_at": now,
            },
            "lock_version": plan.get("lock_version", 1) + 1,
            "updated_at": now,
        })
        _publish_research_event(
            plan["workshop_id"],
            "research.plan.approved",
            {"plan_id": plan_id, "status": "approved"},
        )
        return {
            "status": "completed",
            "data": {"plan_id": plan_id, "status": "approved"},
            "meta": {
                "snapshot_at": now,
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # -------------------------------------------------------------------
    # POST /bff/agora/research-plans/{plan_id}/cancel
    # -------------------------------------------------------------------
    @router.post("/bff/agora/research-plans/{plan_id}/cancel")
    def cancel_agora_research_plan(
        plan_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        _require_idempotency_key(idempotency_key)
        _require_if_match(if_match)
        _check_idempotency(
            scope,
            f"POST:/bff/agora/research-plans/{plan_id}/cancel",
            idempotency_key,  # type: ignore[arg-type]
        )
        plan = store.get_plan(plan_id)
        if plan is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Research plan not found", plan_id)
        _check_plan_if_match(plan, if_match)  # type: ignore[arg-type]
        cancellable = {"draft", "approved", "running"}
        if plan["status"] not in cancellable:
            from models import ErrorCode
            raise bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                f"Plan in status '{plan['status']}' cannot be cancelled",
                f"cancellable statuses: {sorted(cancellable)}",
            )
        now = utc_now()
        store.update_plan(plan_id, {
            "status": "cancelled",
            "lock_version": plan.get("lock_version", 1) + 1,
            "updated_at": now,
        })
        _publish_research_event(
            plan["workshop_id"],
            "research.plan.cancelled",
            {"plan_id": plan_id, "status": "cancelled"},
        )
        return {
            "status": "completed",
            "data": {"plan_id": plan_id, "status": "cancelled"},
            "meta": {
                "snapshot_at": now,
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # -------------------------------------------------------------------
    # GET /bff/agora/research-plans/{plan_id}/runs
    # -------------------------------------------------------------------
    @router.get("/bff/agora/research-plans/{plan_id}/runs")
    def list_agora_research_plan_runs(
        plan_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        plan = store.get_plan(plan_id)
        if plan is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Research plan not found", plan_id)
        runs = store.list_runs_for_plan(plan_id)
        return {
            "items": runs,
            "page_info": {
                "next_page_token": None,
                "page_size": len(runs),
                "has_more": False,
                "total": len(runs),
            },
            "meta": {
                "snapshot_at": utc_now(),
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # -------------------------------------------------------------------
    # POST /bff/agora/research-plans/{plan_id}/runs  (dispatch)
    # -------------------------------------------------------------------
    @router.post("/bff/agora/research-plans/{plan_id}/runs", status_code=202)
    def dispatch_agora_research_plan(
        plan_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        _require_idempotency_key(idempotency_key)
        _require_if_match(if_match)
        _check_idempotency(
            scope,
            f"POST:/bff/agora/research-plans/{plan_id}/runs",
            idempotency_key,  # type: ignore[arg-type]
        )
        plan = store.get_plan(plan_id)
        if plan is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Research plan not found", plan_id)
        _check_plan_if_match(plan, if_match)  # type: ignore[arg-type]
        if plan["status"] != "approved":
            from models import ErrorCode
            raise bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                f"Only approved plans may be dispatched; current status: '{plan['status']}'",
                f"expected 'approved', got '{plan['status']}'",
            )
        # Select the first pending or ready stage
        dispatch_stage: Optional[Dict[str, Any]] = None
        for stage in plan.get("stages", []):
            if stage.get("status") in ("pending", "ready"):
                dispatch_stage = stage
                break
        if dispatch_stage is None:
            from models import ErrorCode
            raise bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                "No pending or ready stages to dispatch",
                "all_stages_dispatched_or_blocked",
            )
        now = utc_now()
        run_id = str(uuid.uuid4())
        run = _build_run_projection(
            plan=plan,
            stage=dispatch_stage,
            run_id=run_id,
            now=now,
        )
        store.create_run(run)
        # Update plan: mark dispatched stage as queued, record run_id, advance to running
        updated_stages = [
            {**s, "status": "queued"} if s["stage_id"] == dispatch_stage["stage_id"] else s
            for s in plan.get("stages", [])
        ]
        plan_run_ids = list(plan.get("run_ids") or [])
        plan_run_ids.append(run_id)
        store.update_plan(plan_id, {
            "stages": updated_stages,
            "run_ids": plan_run_ids,
            "status": "running",
            "lock_version": plan.get("lock_version", 1) + 1,
            "updated_at": now,
        })
        _publish_research_event(
            plan["workshop_id"],
            "research.run.queued",
            {
                "run_id": run_id,
                "plan_id": plan_id,
                "stage_id": dispatch_stage["stage_id"],
                "stage_type": dispatch_stage["stage_type"],
                "percent": 0,
            },
        )
        return {
            "status": "queued",
            "data": {
                "run_id": run_id,
                "plan_id": plan_id,
                "stage_id": dispatch_stage["stage_id"],
                "stage_type": dispatch_stage["stage_type"],
            },
            "meta": {
                "snapshot_at": now,
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # -------------------------------------------------------------------
    # GET /bff/agora/research-runs/{run_id}
    # -------------------------------------------------------------------
    @router.get("/bff/agora/research-runs/{run_id}")
    def get_agora_research_run(
        run_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        _scope(authorization, x_tenant_id)
        run = store.get_run(run_id)
        if run is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Research run not found", run_id)
        return _run_projection_with_defaults(run)

    # -------------------------------------------------------------------
    # POST /bff/agora/research-runs/{run_id}/cancel
    # -------------------------------------------------------------------
    @router.post("/bff/agora/research-runs/{run_id}/cancel", status_code=202)
    def cancel_agora_research_run(
        run_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        _require_idempotency_key(idempotency_key)
        _check_idempotency(
            scope,
            f"POST:/bff/agora/research-runs/{run_id}/cancel",
            idempotency_key,  # type: ignore[arg-type]
        )
        run = store.get_run(run_id)
        if run is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Research run not found", run_id)
        cancellable_statuses = {"queued", "dispatching", "running"}
        current = run.get("execution_status")
        if current not in cancellable_statuses:
            from models import ErrorCode
            raise bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                f"Run in status '{current}' cannot be cancelled",
                f"cancellable statuses: {sorted(cancellable_statuses)}",
            )
        now = utc_now()
        store.update_run(run_id, {
            "execution_status": "cancelled",
            "progress": {
                **run.get("progress", {}),
                "phase": "cancelled",
                "message": "Run cancellation accepted",
                "updated_at": now,
            },
            "completed_at": now,
            "updated_at": now,
        })
        publish_research_progress(
            run["workshop_id"],
            run_id,
            float(run.get("progress", {}).get("percent", 0)),
            "Run cancellation accepted",
            phase="cancelled",
            utc_now_fn=utc_now,
        )
        return {
            "status": "accepted",
            "data": {"run_id": run_id, "execution_status": "cancelled"},
            "meta": {
                "snapshot_at": now,
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # -------------------------------------------------------------------
    # GET /bff/agora/research-runs/{run_id}/artifacts
    # -------------------------------------------------------------------
    @router.get("/bff/agora/research-runs/{run_id}/artifacts")
    def list_agora_research_run_artifacts(
        run_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        run = store.get_run(run_id)
        if run is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Research run not found", run_id)
        artifact_refs = run.get("artifact_refs") or []
        evidence_refs = run.get("evidence_refs") or []
        items: List[Dict[str, Any]] = (
            [{"ref_type": "experiment_artifact", "ref_id": a} for a in artifact_refs]
            + list(evidence_refs)
        )
        return {
            "items": items,
            "page_info": {
                "next_page_token": None,
                "page_size": len(items),
                "has_more": False,
                "total": len(items),
            },
            "meta": {
                "snapshot_at": utc_now(),
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    return router
