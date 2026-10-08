"""PM-12 Portfolio and Allocation surface domain service.

Owns allocation equality semantics, ranking snapshot assertion hashing,
portfolio book exposure projection, holding entry projection, recommendation
resolution, and performance attribution integration.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, Set
import urllib.parse

from fastapi import HTTPException

try:
    from ..auth.policy import bff_error as _default_bff_error
    from ..models import ErrorCode, utc_now as _default_utc_now
except (ImportError, ValueError):
    from auth.policy import bff_error as _default_bff_error
    from models import ErrorCode, utc_now as _default_utc_now

from . import evaluator_results
from ..governance.promotion_review import (
    _promotion_review_clean_id,
    _promotion_review_decision_projection,
    _promotion_review_revision_id,
    _promotion_review_stage_path,
    _promotion_review_stored_source,
    _promotion_review_submission_projection,
    _raise_if_promotion_review_direct_mutation_requested,
)

_NUMERIC_TYPES = (int, float, Decimal)

from services.rankings.snapshots import FORMULA_VERSION as _PM12_LEAGUE_FORMULA_VERSION
_PM12_RANKING_SNAPSHOT_DEFAULT_TTL_SECONDS = 3600
_PM12_RANKING_SNAPSHOT_MAX_TTL_SECONDS = 86400 * 7
_PM12_QUARTER_PATTERN = re.compile(r"^(?P<year>\d{4})-Q(?P<quarter>[1-4])$", re.IGNORECASE)

_PM12_ALLOCATION_LINE_DIGEST_FIELDS = (
    "persona_id",
    "target_weight",
    "current_weight",
    "delta",
    "stage",
    "capital_pool_id",
    "binding_id",
    "sleeve_id",
    "paper_ledger_id",
)

_PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER = (
    "promote_to_canary_candidate",
    "increase_research_budget",
    "grant_tool_access",
    "reduce_capital_access",
    "require_retraining",
    "freeze_persona",
    "suspend_persona",
    "retire_persona",
)

_PM12_QUARTERLY_RECOMMENDATION_ACTIONS = {
    "promote_to_canary_candidate": {
        "label": "Promote to canary candidate",
        "title": "Promote to Canary Candidate",
        "priority": "high",
        "riskLevel": "medium",
        "risk_level": "medium",
        "description": "Meets promotion criteria; request canary staging.",
    },
    "increase_research_budget": {
        "label": "Increase research budget",
        "title": "Increase Research Budget",
        "priority": "medium",
        "riskLevel": "low",
        "risk_level": "low",
        "description": "High search efficiency; grant additional budget.",
    },
    "grant_tool_access": {
        "label": "Grant tool access",
        "title": "Grant Tool Access",
        "priority": "medium",
        "riskLevel": "low",
        "risk_level": "low",
        "description": "Eligible for expanded tooling permissions.",
    },
    "reduce_capital_access": {
        "label": "Reduce capital access",
        "title": "Reduce Capital Access",
        "priority": "high",
        "riskLevel": "high",
        "risk_level": "high",
        "description": "Drawdown or performance degradation detected.",
    },
    "require_retraining": {
        "label": "Require retraining",
        "title": "Require Retraining",
        "priority": "medium",
        "riskLevel": "medium",
        "risk_level": "medium",
        "description": "Execution score below threshold; queue fine-tuning.",
    },
    "freeze_persona": {
        "label": "Freeze persona",
        "title": "Freeze Persona",
        "priority": "critical",
        "riskLevel": "critical",
        "risk_level": "critical",
        "description": "Temporary operational pause for audit.",
    },
    "suspend_persona": {
        "label": "Suspend persona",
        "title": "Suspend Persona",
        "priority": "critical",
        "riskLevel": "critical",
        "risk_level": "critical",
        "description": "Persistent poor performance; revoke execution rights.",
    },
    "retire_persona": {
        "label": "Retire persona",
        "title": "Retire Persona",
        "priority": "critical",
        "riskLevel": "critical",
        "risk_level": "critical",
        "description": "Terminal state; initiate formal decommissioning.",
    },
}


def _stable_json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# 1. Numeric semantic equality and hashing
# ---------------------------------------------------------------------------

def _pm12_semantic_json_value(value: Any) -> Any:
    """Canonicalize JSON values without treating booleans as numbers."""
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, (int, float, Decimal)):
        try:
            numeric = (
                value
                if isinstance(value, Decimal)
                else Decimal(str(value))
            )
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("allocation line contains an invalid number") from exc
        if not numeric.is_finite():
            raise ValueError("allocation line contains a non-finite number")
        if numeric == 0:
            numeric = Decimal(0)
        return ["number", format(numeric.normalize(), "f")]
    if isinstance(value, list):
        return ["array", [_pm12_semantic_json_value(item) for item in value]]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("allocation line contains a non-string object key")
        return [
            "object",
            [
                [key, _pm12_semantic_json_value(value[key])]
                for key in sorted(value)
            ],
        ]
    raise ValueError(
        f"allocation line contains unsupported JSON value {type(value).__name__}"
    )


def _pm12_semantic_values_match(asserted: Any, authoritative: Any) -> bool:
    try:
        return (
            _pm12_semantic_json_value(asserted)
            == _pm12_semantic_json_value(authoritative)
        )
    except ValueError:
        return False


def _pm12_allocation_line_assertion_hash(line: Dict[str, Any]) -> str:
    canonical = _pm12_semantic_json_value(line)
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 2. Allocation line digest and snapshot records
# ---------------------------------------------------------------------------

def _pm12_allocation_line_digest(line: Dict[str, Any]) -> str:
    basis = {
        field: line.get(field)
        for field in _PM12_ALLOCATION_LINE_DIGEST_FIELDS
    }
    basis["capital_scope"] = line.get("capital_scope") or "pool"
    basis["cap_reasons"] = list(line.get("cap_reasons") or [])
    basis["evidence_refs"] = list(line.get("evidence_refs") or [])
    return _stable_json_hash(basis)


def _pm12_ranking_snapshot_ttl_seconds() -> int:
    raw = os.getenv(
        "PANTHEON_PM12_RANKING_SNAPSHOT_TTL_SECONDS",
        str(_PM12_RANKING_SNAPSHOT_DEFAULT_TTL_SECONDS),
    ).strip()
    try:
        configured = int(raw)
    except (TypeError, ValueError):
        return 0
    if configured <= 0 or configured > _PM12_RANKING_SNAPSHOT_MAX_TTL_SECONDS:
        return 0
    return configured


def _pm12_allocation_snapshot_record(
    snapshot_id: str,
    read_store: Any = None,
    bff_error_fn: Any = None,
) -> Dict[str, Any]:
    err = bff_error_fn or _default_bff_error
    if read_store is None:
        from ..main import read_store as _main_read_store
        read_store = _main_read_store

    snapshot = read_store.get_ranking_snapshot(snapshot_id) if hasattr(read_store, "get_ranking_snapshot") else None
    if not isinstance(snapshot, dict):
        raise err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "unknown ranking snapshot",
            "Allocation evaluation requires an evaluator-admitted quarterly ranking snapshot.",
            precondition_failed="ranking_snapshot_id",
        )
    expected_content_digest = _stable_json_hash({
        "surface": snapshot.get("surface"),
        "period": snapshot.get("period"),
        "formula_version": snapshot.get("formula_version"),
        "items": snapshot.get("items") or [],
    })
    if str(snapshot.get("content_digest") or "") != expected_content_digest:
        raise err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "ranking snapshot integrity check failed",
            "The durable snapshot content no longer matches its admitted digest.",
            precondition_failed="ranking_snapshot_id",
        )
    if (
        str(snapshot.get("surface") or "") != "quarterly"
        or str(snapshot.get("formula_version") or "") != _PM12_LEAGUE_FORMULA_VERSION
    ):
        raise err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "ranking snapshot is not allocation eligible",
            "Only admitted PM-12 quarterly snapshots can feed allocation evaluation.",
            precondition_failed="ranking_snapshot_id",
        )
    return snapshot


def _audit_datetime(val: Any) -> Optional[datetime]:
    if not val:
        return None
    try:
        s = str(val).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _pm12_recommendation_snapshot_record(
    snapshot_id: str,
    read_store: Any = None,
    bff_error_fn: Any = None,
) -> Dict[str, Any]:
    err = bff_error_fn or _default_bff_error
    snapshot = _pm12_allocation_snapshot_record(snapshot_id, read_store=read_store, bff_error_fn=bff_error_fn)
    created_at = _audit_datetime(snapshot.get("created_at"))
    now = datetime.now(timezone.utc)
    ttl_seconds = _pm12_ranking_snapshot_ttl_seconds()
    if created_at is None or now is None or ttl_seconds <= 0:
        raise err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "ranking snapshot admission window is invalid",
            "Recommendation submission requires a timestamped snapshot and a valid bounded TTL.",
            precondition_failed="ranking_snapshot_id",
        )
    age_seconds = (now - created_at).total_seconds()
    if age_seconds < -300 or age_seconds > ttl_seconds:
        raise err(
            409,
            ErrorCode.PRECONDITION_FAILED,
            "ranking snapshot admission window expired",
            "Fetch a current recommendation and submit its immutable admitted snapshot.",
            precondition_failed="ranking_snapshot_id",
        )
    return snapshot


def _pm12_allocation_evaluation_record(
    evaluation_id: str,
    read_store: Any = None,
    bff_error_fn: Any = None,
) -> Dict[str, Any]:
    err = bff_error_fn or _default_bff_error
    if read_store is None:
        from ..main import read_store as _main_read_store
        read_store = _main_read_store

    evaluation = read_store.get_allocation_evaluation(evaluation_id) if hasattr(read_store, "get_allocation_evaluation") else None
    if not isinstance(evaluation, dict):
        raise err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "unknown allocation evaluation",
            "The proposal must join to a durable server-side allocation evaluation.",
            precondition_failed="allocation_evaluation_id",
        )
    lines = evaluation.get("lines")
    if not isinstance(lines, list) or not lines:
        raise err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "allocation evaluation integrity check failed",
            "The durable allocation evaluation has no admitted lines.",
            precondition_failed="allocation_evaluation_id",
        )
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            raise err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "allocation evaluation integrity check failed",
                f"The durable allocation line at index {index} is invalid.",
                precondition_failed="allocation_line_digest",
            )
        supplied_digest = str(line.get("allocation_line_digest") or "").strip()
        if not supplied_digest or _pm12_allocation_line_digest(line) != supplied_digest:
            raise err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "allocation evaluation integrity check failed",
                f"The durable allocation line at index {index} no longer matches its digest.",
                precondition_failed="allocation_line_digest",
            )
    content_basis = {
        "ranking_snapshot_id": evaluation.get("ranking_snapshot_id"),
        "allocation_evaluation_id": evaluation.get("allocation_evaluation_id"),
        "allocation_policy_version": evaluation.get("allocation_policy_version"),
        "lines": lines,
    }
    for optional_field in ("authority_mode", "promotion_review_id"):
        if evaluation.get(optional_field) not in (None, ""):
            content_basis[optional_field] = evaluation.get(optional_field)
    expected_content_digest = _stable_json_hash(content_basis)
    if str(evaluation.get("content_digest") or "") != expected_content_digest:
        raise err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "allocation evaluation integrity check failed",
            "The durable allocation evaluation no longer matches its admitted digest.",
            precondition_failed="allocation_evaluation_id",
        )
    return evaluation


# ---------------------------------------------------------------------------
# 3. Quarter & Recommendation helpers
# ---------------------------------------------------------------------------

def _pm12_current_quarter_id(snapshot_at: str) -> str:
    timestamp = _audit_datetime(snapshot_at) or datetime.now(timezone.utc)
    quarter = ((timestamp.month - 1) // 3) + 1
    return f"{timestamp.year}-Q{quarter}"


def _pm12_iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _pm12_quarter_window(quarter: Optional[str], snapshot_at: str) -> Dict[str, Any]:
    raw_quarter = str(quarter or "").strip().upper() or _pm12_current_quarter_id(snapshot_at)
    match = _PM12_QUARTER_PATTERN.match(raw_quarter)
    if not match:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_quarter",
                "message": "quarter must use YYYY-Qn format, for example 2026-Q2.",
                "field": "quarter",
            },
        )
    year = int(match.group("year"))
    quarter_number = int(match.group("quarter"))
    start_month = ((quarter_number - 1) * 3) + 1
    start_at = datetime(year, start_month, 1, tzinfo=timezone.utc)
    if quarter_number == 4:
        end_exclusive_at = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end_exclusive_at = datetime(year, start_month + 3, 1, tzinfo=timezone.utc)
    quarter_id = f"{year}-Q{quarter_number}"
    return {
        "quarter": quarter_id,
        "quarter_id": quarter_id,
        "year": year,
        "quarter_number": quarter_number,
        "label": f"{year} Q{quarter_number}",
        "start_at": _pm12_iso_z(start_at),
        "end_exclusive_at": _pm12_iso_z(end_exclusive_at),
        "timezone": "UTC",
    }


def _management_number(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _pm12_quarterly_recommendation_item(
    item: Dict[str, Any],
    *,
    action_id: str,
    quarter_window: Dict[str, Any],
    evidence_refs: List[Dict[str, Any]],
    saved: Dict[str, Any],
    command_store: Any = None,
) -> Dict[str, Any]:
    action = _PM12_QUARTERLY_RECOMMENDATION_ACTIONS[action_id]
    persona_id = str(item.get("persona_id") or item.get("personaId") or item.get("id") or "")
    score = _management_number(item.get("score")) or _management_number(item.get("overall_score")) or 0.0
    evidence_sample = list(item.get("evidence_refs") or evidence_refs or [])[:5]
    evidence_ref_ids = list(saved.get("evidence_ref_ids") or [])
    recommendation_id = f"pm12-{quarter_window['quarter'].lower()}-{persona_id}-{action_id}"
    review_id = _promotion_review_revision_id(
        recommendation_id,
        item.get("ranking_snapshot_id"),
    )
    submission = _promotion_review_submission_projection(review_id, command_store=command_store)
    decision = _promotion_review_decision_projection(review_id, command_store=command_store)

    if decision:
        review_status = "decision_accepted"
        decision_status = str((decision or {}).get("decision_status") or "accepted")
    elif submission:
        review_status = "pending_human_gate"
        decision_status = "pending"
    else:
        review_status = "recommended_not_submitted"
        decision_status = "pending"

    human_review_state = {
        "status": review_status,
        "decision_status": decision_status,
        "submitted": bool(submission),
        "submit_status": (submission or {}).get("submit_status") if submission else "not_submitted",
        "decision": (decision or {}).get("decision") if decision else None,
        "decided_at": (decision or {}).get("decided_at") if decision else None,
        "decided_by": (decision or {}).get("decided_by") if decision else None,
    }

    governance = {
        "requires_human_gate_decision": True,
        "destinations": ["human_inbox", "governance_queue", "human_gate_decision"],
        "human_inbox_route": "/bff/management/human-inbox",
        "governance_queue_route": "/api/v1/operator/governance/approval-queue",
        "decision_type": "HumanGateDecision",
        "live_capital_mutation": False,
    }
    return {
        "id": recommendation_id,
        "recommendation_id": recommendation_id,
        "review_id": review_id,
        "promotion_review_id": review_id,
        "quarter": quarter_window["quarter"],
        "quarter_window": quarter_window,
        "persona_id": persona_id,
        "ranking_snapshot_id": item.get("ranking_snapshot_id"),
        "ranking_evidence_ref": (
            f"ranking-snapshot:{item.get('ranking_snapshot_id')}"
            if item.get("ranking_snapshot_id")
            else f"ranking-evidence:{quarter_window['quarter'].lower()}-{persona_id}"
        ),
        "human_review_state": human_review_state,
        "name": item.get("name"),
        "owner": item.get("owner"),
        "archetype": item.get("archetype"),
        "state": item.get("state"),
        "stage": item.get("stage"),
        "deployment_stage": item.get("deployment_stage"),
        "capital_mode": item.get("capital_mode"),
        "capital_scope": item.get("capital_scope"),
        "capital_scope_id": item.get("capital_scope_id"),
        "capital_pool_id": item.get("capital_pool_id"),
        "capital_sleeve_id": item.get("capital_sleeve_id"),
        "paper_ledger_id": item.get("paper_ledger_id"),
        "current_weight": item.get("current_weight"),
        "target_weight": item.get("target_weight"),
        "delta": item.get("delta"),
        "current_weight_source": item.get("current_weight_source"),
        "binding_state": item.get("binding_state"),
        "binding_resolution": item.get("binding_resolution"),
        "runtime_resolution": item.get("runtime_resolution"),
        "session_resolution": item.get("session_resolution"),
        "telemetry_resolution": item.get("telemetry_resolution"),
        "binding_ids": list(item.get("binding_ids") or []),
        "strategy_ids": list(item.get("strategy_ids") or []),
        "runtime_ids": list(item.get("runtime_ids") or []),
        "capital_pool_ids": list(item.get("capital_pool_ids") or []),
        "sleeve_ids": list(item.get("sleeve_ids") or []),
        "artifact_ids": list(item.get("artifact_ids") or []),
        "broker_ids": list(item.get("broker_ids") or []),
        "eligible": item.get("eligible"),
        "exclusion_reason": item.get("exclusion_reason"),
        "exclusion_reasons": list(item.get("exclusion_reasons") or []),
        "exclusion_codes": list(item.get("exclusion_codes") or []),
        "evidence_coverage": item.get("evidence_coverage"),
        "source_confidence": item.get("source_confidence"),
        "risk": item.get("risk"),
        "rank": item.get("rank"),
        "score": score,
        "tier": item.get("tier"),
        "tier_id": item.get("tier_id"),
        "tier_label": item.get("tier_label"),
        "allocation_policy_input": json.loads(
            json.dumps(item.get("allocation_policy_input") or {})
        ),
        "formula_version": item.get("formula_version") or _PM12_LEAGUE_FORMULA_VERSION,
        "action_id": action_id,
        "action_label": action.get("label") or action.get("title") or action_id,
        "action_description": action.get("description") or "",
        "recommendation_type": "governance_advisory",
        "status": "recommended",
        "priority": action.get("priority") or "medium",
        "risk_level": action.get("risk_level") or "medium",
        "target": {"type": "persona", "id": persona_id},
        "rationale": saved["rationale"],
        "recommendation_source": "persona_evaluator_agent",
        "evaluator_run_id": saved.get("evaluator_run_id"),
        "evaluated_at": saved.get("evaluated_at"),
        "governance_request": saved.get("governance_request"),
        "rationale_codes": [
            f"tier:{item.get('tier') or 'unknown'}",
            f"action:{action_id}",
            "policy:no_direct_live_capital",
        ],
        "metrics": item.get("metrics") or {},
        "components": item.get("components") or {},
        "evidence_refs": evidence_sample,
        "evidence_ref_ids": evidence_ref_ids,
        "governance": governance,
        "requires_human_gate_decision": True,
        "live_capital_mutation": False,
        "policy": "read_only_governance_advisory",
        "links": {
            "persona": f"/bff/personas/{persona_id}",
            "human_inbox": "/bff/management/human-inbox",
            "governance_queue": "/api/v1/operator/governance/approval-queue",
        },
    }


def _pm12_resolve_quarterly_recommendation_submit_params(
    params: Dict[str, Any],
    read_store: Any = None,
    command_store: Any = None,
    bff_error_fn: Any = None,
) -> Dict[str, Any]:
    err = bff_error_fn or _default_bff_error
    _raise_if_promotion_review_direct_mutation_requested(params)

    recommendation_id = str(
        params.get("recommendation_id") or params.get("recommendationId") or ""
    ).strip()
    snapshot_id = str(params.get("ranking_snapshot_id") or "").strip()
    quarter = str(params.get("quarter") or "").strip().upper()
    if not recommendation_id or not snapshot_id or not quarter:
        return dict(params)
    snapshot = _pm12_recommendation_snapshot_record(snapshot_id, read_store=read_store, bff_error_fn=err)
    snapshot_quarter = str(snapshot.get("period") or "").strip().upper()
    if snapshot_quarter != quarter:
        raise err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "quarter does not match the admitted ranking snapshot",
            "The submitted quarter must be the immutable snapshot period.",
            precondition_failed="quarter",
        )

    saved = evaluator_results.saved_recommendation(quarter, snapshot_id, recommendation_id)
    matched_item = next(
        (
            i for i in snapshot.get("items") or []
            if isinstance(i, dict) and str(i.get("persona_id") or "").strip() == (saved or {}).get("persona_id")
        ),
        None,
    )
    if saved is None or saved.get("ranking_snapshot_id") != snapshot_id or matched_item is None:
        raise err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "recommendation is not in the admitted ranking snapshot",
            "The recommendation was not saved by the persona evaluator for this snapshot.",
            precondition_failed="recommendation_id",
        )
    matched_action_id = saved["action_id"]
    review_revision_id = _promotion_review_revision_id(
        recommendation_id,
        snapshot_id,
    )
    for field in ("review_id", "promotion_review_id"):
        asserted_review_id = str(params.get(field) or "").strip()
        if (
            asserted_review_id
            and _promotion_review_clean_id(asserted_review_id)
            != review_revision_id
        ):
            raise err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "promotion review revision assertion mismatch",
                f"{field} does not match the admitted recommendation snapshot.",
                precondition_failed=field,
            )

    asserted_action_id = str(
        params.get("recommendation_action_id")
        or params.get("recommendationActionId")
        or ""
    ).strip()
    if asserted_action_id and asserted_action_id != matched_action_id:
        raise err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "recommendation action does not match the admitted snapshot",
            "The caller-supplied recommendation action is not authoritative.",
            precondition_failed="recommendation_action_id",
        )

    item = {
        **json.loads(json.dumps(matched_item)),
        "ranking_snapshot_id": snapshot_id,
        "evidence_refs": [],
    }
    utc_now_str = _default_utc_now()
    quarter_window = _pm12_quarter_window(quarter, utc_now_str)
    source_recommendation = _pm12_quarterly_recommendation_item(
        item,
        action_id=matched_action_id,
        quarter_window=quarter_window,
        evidence_refs=[],
        saved=saved,
        command_store=command_store,
    )
    source_recommendation["human_review_state"] = {
        "status": "recommended_not_submitted",
        "decision_status": "pending",
        "submitted": False,
        "submit_status": "not_submitted",
        "decision": None,
        "decided_at": None,
        "decided_by": None,
    }
    stored_source = _promotion_review_stored_source(source_recommendation)
    stage_path = _promotion_review_stage_path(source_recommendation)
    canonical_assertions = {
        "persona_id": item.get("persona_id"),
        "stage": item.get("stage"),
        "deployment_stage": item.get("deployment_stage"),
        "stage_from": stage_path.get("from_stage"),
        "stage_to": stage_path.get("target_stage"),
        "review_kind": stage_path.get("review_kind"),
        "current_weight": item.get("current_weight"),
        "target_weight": item.get("target_weight"),
        "delta": item.get("delta"),
        "capital_scope": item.get("capital_scope"),
        "capital_pool_id": item.get("capital_pool_id"),
        "capital_sleeve_id": item.get("capital_sleeve_id"),
        "evidence_ref_ids": sorted(item.get("evidence_ref_ids") or []),
    }
    for field, authoritative_value in canonical_assertions.items():
        if field not in params:
            continue
        asserted_value = params.get(field)
        if field == "evidence_ref_ids":
            asserted_value = sorted(asserted_value or [])
        if not _pm12_semantic_values_match(asserted_value, authoritative_value):
            raise err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "quarterly recommendation assertion mismatch",
                f"{field} does not match the admitted ranking snapshot.",
                precondition_failed=field,
            )
    if params.get("evidence_refs") not in (None, []):
        raise err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "evidence_refs cannot be overridden during recommendation submission",
            "Evidence references are populated exclusively from server-side assertions.",
            precondition_failed="evidence_refs",
        )

    resolved = dict(params)
    resolved["recommendation_id"] = recommendation_id
    resolved["ranking_snapshot_id"] = snapshot_id
    resolved["quarter"] = quarter
    resolved["review_id"] = review_revision_id
    resolved["promotion_review_id"] = review_revision_id
    resolved["recommendation_action_id"] = matched_action_id
    resolved["source_recommendation"] = stored_source
    resolved["stage_from"] = stage_path.get("from_stage")
    resolved["stage_to"] = stage_path.get("target_stage")
    resolved["review_kind"] = stage_path.get("review_kind")
    resolved["persona_id"] = item.get("persona_id")
    resolved["current_weight"] = item.get("current_weight")
    resolved["target_weight"] = item.get("target_weight")
    resolved["delta"] = item.get("delta")
    resolved["capital_scope"] = item.get("capital_scope")
    resolved["capital_pool_id"] = item.get("capital_pool_id")
    resolved["capital_sleeve_id"] = item.get("capital_sleeve_id")
    resolved["evidence_refs"] = []
    resolved["evidence_ref_ids"] = canonical_assertions["evidence_ref_ids"]
    return resolved


# ---------------------------------------------------------------------------
# 4. Performance Attribution delegations
# ---------------------------------------------------------------------------

def _pm12_attribution_metrics(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    from ..agora.performance import service as _agora_perf
    return _agora_perf.pm12_attribution_metrics(entries)


def _pm12_performance_attribution_facts(sources: Dict[str, Any], period_key: str) -> List[Dict[str, Any]]:
    from ..agora.performance import service as _agora_perf
    return _agora_perf.pm12_performance_attribution_facts(sources, period_key)


def _pm12_performance_attribution_rows(
    entries: List[Dict[str, Any]],
    *,
    period_key: str,
    sources: Dict[str, Any],
) -> List[Dict[str, Any]]:
    from ..agora.performance import service as _agora_perf
    return _agora_perf.pm12_performance_attribution_rows(
        entries,
        period_key=period_key,
        sources=sources,
    )


def _pm12_performance_attribution_sources(
    tenant_id: Optional[str] = None,
    read_store: Optional[Any] = None,
    list_persona_records: Optional[Any] = None,
    list_strategy_summaries: Optional[Any] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    from ..agora.performance import service as _agora_perf
    resolved_store = read_store
    if resolved_store is None:
        try:
            from ..main import read_store as _main_read_store
            resolved_store = _main_read_store
        except Exception:
            pass
    return _agora_perf.pm12_performance_attribution_sources(
        tenant_id=tenant_id,
        read_store=resolved_store,
        list_persona_records=list_persona_records,
        list_strategy_summaries=list_strategy_summaries,
    )


def _pm12_performance_attribution_response(
    *args: Any,
    read_store: Optional[Any] = None,
    sources_fn: Optional[Callable[..., Any]] = None,
    rows_fn: Optional[Callable[..., Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    from ..agora.performance import service as _agora_perf
    resolved_store = read_store
    if resolved_store is None:
        try:
            from ..main import read_store as _main_read_store
            resolved_store = _main_read_store
        except Exception:
            pass
    resolved_sources_fn = (
        sources_fn
        if sources_fn is not None
        else (lambda t: _pm12_performance_attribution_sources(t, read_store=resolved_store))
    )
    return _agora_perf.pm12_performance_attribution_response(
        *args,
        read_store=resolved_store,
        sources_fn=resolved_sources_fn,
        rows_fn=rows_fn or _pm12_performance_attribution_rows,
        **kwargs,
    )


_pm12_performance_attribution_response_impl = _pm12_performance_attribution_response


# ---------------------------------------------------------------------------
# 5. Portfolio Book Exposure & Holding Entry helpers
# ---------------------------------------------------------------------------

def _management_as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _management_first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _management_dict_value(record: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def _management_nested_dict(record: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    for key in keys:
        value = record.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _management_record_id(record: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        val = record.get(key)
        if val not in (None, ""):
            return str(val).strip()
    return ""


def _management_exposure_risk_state(utilization: Optional[float]) -> str:
    if utilization is None:
        return "unknown"
    if utilization > 1:
        return "over_budget"
    if utilization >= 0.8:
        return "near_limit"
    return "within_budget"


def _management_portfolio_book_exposure_item(entry: Dict[str, Any]) -> Dict[str, Any]:
    pool_id = str(entry.get("pool_id") or entry.get("id") or "")
    risk_budget = _management_as_float(entry.get("risk_budget"))
    current_exposure = _management_as_float(entry.get("current_exposure"))
    utilization = _management_as_float(entry.get("risk_budget_utilization"))
    if utilization is None and current_exposure is not None and risk_budget not in (None, 0):
        utilization = round(current_exposure / risk_budget, 6)
    exposure = entry.get("exposure") if isinstance(entry.get("exposure"), dict) else {}
    runtime_ids = [
        str(value)
        for value in (entry.get("runtime_ids") or [])
        if str(value).strip()
    ]
    binding_ids = [
        str(value)
        for value in (entry.get("binding_ids") or [])
        if str(value).strip()
    ]
    deployment_ids = [
        str(value)
        for value in (entry.get("deployment_ids") or [])
        if str(value).strip()
    ]
    risk_state = _management_exposure_risk_state(utilization)
    available_budget = (
        round(risk_budget - current_exposure, 6)
        if risk_budget is not None and current_exposure is not None
        else None
    )
    return {
        "id": f"portfolio-book-exposure-{pool_id or 'unassigned'}",
        "pool_id": pool_id,
        "capital_pool_id": pool_id,
        "name": entry.get("name") or pool_id,
        "status": entry.get("status") or "unknown",
        "risk_policy_ref": entry.get("risk_policy_ref"),
        "currency": entry.get("currency"),
        "risk_budget": risk_budget,
        "current_exposure": current_exposure,
        "exposure_amount": current_exposure,
        "risk_budget_utilization": utilization,
        "risk_state": risk_state,
        "exposure_source": exposure.get("source"),
        "available_budget": available_budget,
        "risk": entry.get("risk"),
        "exposure": {
            **exposure,
            "amount": current_exposure,
            "risk_budget": risk_budget,
            "risk_budget_utilization": utilization,
            "risk_state": risk_state,
        },
        "pnl": entry.get("pnl"),
        "total_pnl": entry.get("total_pnl"),
        "pnl_summary": entry.get("pnl_summary"),
        "telemetry": entry.get("telemetry"),
        "binding_count": entry.get("binding_count", 0),
        "active_binding_count": entry.get("active_binding_count", 0),
        "deployment_count": entry.get("deployment_count", 0),
        "approved_deployment_count": entry.get("approved_deployment_count", 0),
        "runtime_count": entry.get("runtime_count", 0),
        "active_runtime_count": entry.get("active_runtime_count", 0),
        "paper_runtime_count": entry.get("paper_runtime_count", 0),
        "live_runtime_count": entry.get("live_runtime_count", 0),
        "deployment_stages": entry.get("deployment_stages") or [],
        "strategy_ids": entry.get("strategy_ids") or [],
        "persona_ids": entry.get("persona_ids") or [],
        "source_refs": {
            "runtime_ids": runtime_ids,
            "binding_ids": binding_ids,
            "deployment_ids": deployment_ids,
            "capital_pool_ids": [pool_id] if pool_id else [],
            "strategy_ids": entry.get("strategy_ids") or [],
            "persona_ids": entry.get("persona_ids") or [],
        },
        "links": {
            "capital_pool": f"/bff/capital-pools/{pool_id}" if pool_id else None,
        },
    }


def _management_portfolio_holding_entry(
    runtime: Dict[str, Any],
    position: Dict[str, Any],
    *,
    position_index: int = 0,
    plan: Optional[Dict[str, Any]] = None,
    persona_binding: Optional[Dict[str, Any]] = None,
    capital_pool: Optional[Dict[str, Any]] = None,
    telemetry: Optional[Dict[str, Any]] = None,
    position_source_count: int = 1,
    **kwargs: Any,
) -> Dict[str, Any]:
    plan_dict = plan or {}
    binding_dict = persona_binding or {}
    pool_dict = capital_pool or {}
    telemetry_dict = telemetry or {}
    summary = telemetry_dict.get("summary") if isinstance(telemetry_dict.get("summary"), dict) else {}
    instrument = _management_nested_dict(position, "instrument", "asset", "contract")
    mark = _management_nested_dict(position, "mark", "mark_price", "market_price")

    runtime_id = _management_record_id(runtime, "runtime_id", "id", "binding_id")
    runtime_binding_id = _management_record_id(runtime, "runtime_binding_id", "binding_id", "id")
    plan_id = _management_record_id(runtime, "plan_id", "deployment_plan_id") or _management_record_id(plan_dict, "plan_id", "id")
    persona_binding_id = (
        _management_record_id(runtime, "persona_capital_binding_id")
        or _management_record_id(binding_dict, "binding_id", "id", "persona_capital_binding_id")
    )
    capital_pool_id = str(
        _management_first_non_empty(
            _management_dict_value(position, "capital_pool_id", "pool_id"),
            _management_dict_value(runtime, "capital_pool_id", "pool_id"),
            _management_dict_value(plan_dict, "capital_pool_id", "target_pool_id", "pool_id"),
            _management_dict_value(binding_dict, "capital_pool_id", "pool_id"),
        )
        or ""
    )
    persona_id = str(
        _management_first_non_empty(
            _management_dict_value(position, "persona_id"),
            _management_dict_value(runtime, "persona_id"),
            _management_dict_value(plan_dict, "persona_id"),
            _management_dict_value(binding_dict, "persona_id"),
        )
        or ""
    )
    strategy_id = str(
        _management_first_non_empty(
            _management_dict_value(position, "strategy_id", "strategy_ref"),
            _management_dict_value(runtime, "strategy_id", "strategy_ref"),
            _management_dict_value(plan_dict, "strategy_id", "strategy_ref"),
            _management_dict_value(binding_dict, "strategy_id"),
        )
        or ""
    )
    artifact_id = str(
        _management_first_non_empty(
            _management_dict_value(position, "artifact_id"),
            _management_dict_value(runtime, "artifact_id"),
            _management_dict_value(plan_dict, "artifact_id"),
        )
        or ""
    )
    artifact_version = str(
        _management_first_non_empty(
            _management_dict_value(position, "artifact_version", "version"),
            _management_dict_value(runtime, "artifact_version", "version"),
            _management_dict_value(plan_dict, "artifact_version", "version"),
        )
        or ""
    )
    broker_id = str(
        _management_first_non_empty(
            _management_dict_value(position, "broker_id", "broker"),
            _management_dict_value(telemetry_dict, "broker_id", "broker"),
            _management_dict_value(runtime, "broker_id", "broker"),
            _management_dict_value(plan_dict, "broker_id", "broker"),
        )
        or ""
    )
    paper_ledger_id = str(
        _management_first_non_empty(
            _management_dict_value(position, "paper_ledger_id"),
            _management_dict_value(runtime, "paper_ledger_id"),
            _management_dict_value(plan_dict, "paper_ledger_id"),
            _management_dict_value(binding_dict, "paper_ledger_id"),
        )
        or ""
    )
    sleeve_id = str(
        _management_first_non_empty(
            _management_dict_value(position, "capital_sleeve_id", "sleeve_id"),
            _management_dict_value(runtime, "capital_sleeve_id", "sleeve_id"),
            _management_dict_value(plan_dict, "capital_sleeve_id", "sleeve_id"),
            _management_dict_value(binding_dict, "capital_sleeve_id", "sleeve_id"),
        )
        or ""
    )
    symbol = str(
        _management_first_non_empty(
            _management_dict_value(position, "symbol", "instrument_id", "asset_id", "contract_id"),
            _management_dict_value(instrument, "symbol", "instrument_id", "asset_id", "contract_id"),
            _management_dict_value(telemetry_dict, "symbol", "instrument_id", "asset_id", "contract_id"),
        )
        or ""
    )
    asset_class = _management_first_non_empty(
        _management_dict_value(position, "asset_class"),
        _management_dict_value(instrument, "asset_class"),
        _management_dict_value(telemetry_dict, "asset_class"),
    )
    currency = _management_first_non_empty(
        _management_dict_value(position, "currency"),
        _management_dict_value(instrument, "currency"),
        _management_dict_value(telemetry_dict, "currency"),
    )
    quantity = _management_as_float(
        _management_first_non_empty(
            _management_dict_value(position, "quantity", "qty", "net_quantity", "position_quantity"),
            _management_dict_value(telemetry_dict, "quantity", "position_quantity"),
            _management_dict_value(summary, "quantity", "position_quantity"),
        )
    )
    mark_price = _management_as_float(
        _management_first_non_empty(
            _management_dict_value(position, "mark_price", "market_price", "last_price"),
            _management_dict_value(mark, "price", "mark_price", "market_price", "last_price"),
            _management_dict_value(telemetry_dict, "mark_price", "market_price", "last_price"),
            _management_dict_value(summary, "mark_price", "market_price", "last_price"),
        )
    )
    average_price = _management_as_float(
        _management_first_non_empty(
            _management_dict_value(position, "average_price", "avg_price", "cost_basis"),
            _management_dict_value(telemetry_dict, "average_price", "avg_price", "cost_basis"),
            _management_dict_value(summary, "average_price", "avg_price", "cost_basis"),
        )
    )
    market_value = _management_as_float(
        _management_first_non_empty(
            _management_dict_value(position, "market_value", "value"),
            _management_dict_value(telemetry_dict, "market_value"),
            _management_dict_value(summary, "market_value"),
        )
    )
    if market_value is None and quantity is not None and mark_price is not None:
        market_value = round(quantity * mark_price, 6)
    notional = _management_as_float(
        _management_first_non_empty(
            _management_dict_value(position, "notional", "gross_notional"),
            _management_dict_value(telemetry_dict, "notional", "gross_notional"),
            _management_dict_value(summary, "notional", "gross_notional"),
        )
    )
    if notional is None and market_value is not None:
        notional = abs(market_value)
    exposure = _management_as_float(
        _management_first_non_empty(
            _management_dict_value(position, "exposure", "gross_exposure"),
            _management_dict_value(telemetry_dict, "exposure", "gross_exposure"),
            _management_dict_value(summary, "exposure", "gross_exposure"),
        )
    )
    weight = _management_as_float(
        _management_first_non_empty(
            _management_dict_value(position, "weight", "portfolio_weight"),
            _management_dict_value(telemetry_dict, "weight", "portfolio_weight"),
            _management_dict_value(summary, "weight", "portfolio_weight"),
        )
    )
    runtime_pnl = _management_as_float(
        _management_first_non_empty(
            _management_dict_value(telemetry_dict, "pnl"),
            _management_dict_value(summary, "total_pnl"),
        )
    )
    total_pnl = _management_as_float(
        _management_first_non_empty(
            _management_dict_value(position, "total_pnl", "pnl"),
            runtime_pnl,
        )
    )
    unrealized_pnl = _management_as_float(
        _management_first_non_empty(
            _management_dict_value(position, "unrealized_pnl", "unrealized"),
            _management_dict_value(telemetry_dict, "unrealized_pnl"),
            _management_dict_value(summary, "unrealized_pnl"),
        )
    )
    realized_pnl = _management_as_float(
        _management_first_non_empty(
            _management_dict_value(position, "realized_pnl", "realized"),
            _management_dict_value(telemetry_dict, "realized_pnl"),
            _management_dict_value(summary, "realized_pnl"),
        )
    )
    side = str(
        _management_first_non_empty(
            _management_dict_value(position, "side", "direction"),
            _management_dict_value(telemetry_dict, "side", "direction"),
        )
        or ""
    ).lower()
    if not side and quantity is not None:
        side = "long" if quantity > 0 else "short" if quantity < 0 else "flat"
    if not side:
        side = "unknown"

    holding_key = str(
        _management_first_non_empty(
            _management_dict_value(position, "holding_id", "position_id", "id"),
            symbol,
            artifact_id,
            position_index,
        )
    )
    holding_id = f"{runtime_id}:{holding_key}" if runtime_id else holding_key
    deployment_stage = str(
        _management_first_non_empty(
            position.get("deployment_stage"),
            runtime.get("deployment_stage"),
            plan_dict.get("deployment_stage"),
            "paper",
        )
    )
    status = str(_management_first_non_empty(position.get("status"), runtime.get("status"), "unknown") or "unknown")

    last_mark_at = str(
        _management_first_non_empty(
            _management_dict_value(position, "marked_at", "mark_time", "updated_at", "collected_at"),
            _management_dict_value(mark, "marked_at", "mark_time", "updated_at", "collected_at"),
            _management_dict_value(telemetry_dict, "collected_at", "updated_at"),
            _management_dict_value(summary, "collected_at", "updated_at"),
        )
        or ""
    )
    source_issues = _management_portfolio_source_issues(
        runtime_id=runtime_id,
        persona_id=persona_id,
        persona_binding_id=persona_binding_id,
        telemetry=telemetry_dict,
        position_source_count=position_source_count,
    )
    source_status = _management_portfolio_source_status(source_issues)
    risk_state = _management_portfolio_risk_state(
        source_status=source_status,
        source_issues=source_issues,
        deployment_stage=deployment_stage,
    )
    identity = _management_portfolio_identity(
        portfolio_id=holding_id,
        capital_pool_id=capital_pool_id,
        sleeve_id=sleeve_id,
        paper_ledger_id=paper_ledger_id,
        persona_id=persona_id,
        runtime_id=runtime_id,
        runtime_binding_id=runtime_binding_id,
        plan_id=plan_id,
        strategy_id=strategy_id,
        artifact_id=artifact_id,
        broker_id=broker_id,
        deployment_stage=deployment_stage,
    )
    capital_scope = _management_portfolio_capital_scope(
        deployment_stage=deployment_stage,
        capital_pool_id=capital_pool_id,
        sleeve_id=sleeve_id,
        paper_ledger_id=paper_ledger_id,
    )
    operator_links = _management_portfolio_operator_links(
        persona_id=persona_id,
        runtime_id=runtime_id,
        holding_id=holding_id,
    )

    return {
        "id": holding_id,
        "holding_id": holding_id,
        "position_id": holding_id,
        "runtime_id": runtime_id,
        "runtime_binding_id": runtime_binding_id,
        "deployment_plan_id": plan_id,
        "capital_pool_id": capital_pool_id,
        "capital_pool": {
            "id": capital_pool_id,
            "name": pool_dict.get("name") or capital_pool_id,
            "status": pool_dict.get("status"),
            "risk_policy_ref": pool_dict.get("risk_policy_ref"),
        },
        "persona_id": persona_id,
        "persona_capital_binding_id": persona_binding_id,
        "strategy_id": strategy_id,
        "artifact_id": artifact_id,
        "artifact_version": artifact_version,
        "broker_id": broker_id,
        "paper_ledger_id": paper_ledger_id or None,
        "sleeve_id": sleeve_id or None,
        "deployment_stage": deployment_stage,
        "status": status,
        "source_status": source_status,
        "source_row_count": position_source_count,
        "source_issues": source_issues,
        "telemetry_available": bool(telemetry_dict),
        "telemetry_stale": any(issue.get("code") == "STALE_TELEMETRY" for issue in source_issues),
        "risk_state": risk_state,
        "identity": identity,
        "capital_scope": capital_scope,
        "instrument": {
            "symbol": symbol,
            "asset_class": asset_class,
            "currency": currency,
            "market": _management_first_non_empty(
                _management_dict_value(position, "market"),
                _management_dict_value(instrument, "market"),
                _management_dict_value(telemetry_dict, "market"),
            ),
        },
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "average_price": average_price,
        "mark_price": mark_price,
        "market_value": market_value,
        "notional": notional,
        "exposure": exposure,
        "weight": weight,
        "pnl": {
            "total": total_pnl,
            "runtime": runtime_pnl,
            "unrealized": unrealized_pnl,
            "realized": realized_pnl,
        },
        "total_pnl": total_pnl,
        "unrealized_pnl": unrealized_pnl,
        "realized_pnl": realized_pnl,
        "last_mark_at": last_mark_at or None,
        "links": {
            "runtime": f"/bff/runtimes/{runtime_id}" if runtime_id else None,
            "capital_pool": f"/bff/capital-pools/{capital_pool_id}" if capital_pool_id else None,
            "persona": f"/bff/personas/{persona_id}" if persona_id else None,
            "strategy": f"/bff/strategies/{strategy_id}" if strategy_id else None,
            "deployment": f"/bff/deployments/{plan_id}" if plan_id else None,
            **operator_links,
        },
    }


def _management_portfolio_source_issues(
    *,
    runtime_id: str,
    persona_id: str,
    persona_binding_id: str,
    telemetry: Dict[str, Any],
    position_source_count: int,
) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    if not persona_id or not persona_binding_id:
        issues.append({
            "source_name": "persona_bindings",
            "code": "MISSING_PERSONA_BINDING",
            "message": f"Runtime {runtime_id or 'unknown'} does not resolve to a persona capital binding.",
        })
    if not telemetry:
        issues.append({
            "source_name": "telemetry_summaries",
            "code": "MISSING_TELEMETRY",
            "message": f"Telemetry summary is unavailable for runtime {runtime_id or 'unknown'}.",
        })
    elif position_source_count <= 0:
        issues.append({
            "source_name": "portfolio_holdings",
            "code": "MISSING_HOLDING_ROW",
            "message": f"Telemetry for runtime {runtime_id or 'unknown'} did not include a holding or position row.",
        })

    freshness = str(
        _management_first_non_empty(
            telemetry.get("source_status"),
            telemetry.get("source_state"),
            telemetry.get("data_status"),
            telemetry.get("freshness_status"),
        )
        or ""
    ).strip().lower()
    if bool(telemetry.get("stale")) or freshness in {"stale", "expired", "lagging"}:
        issues.append({
            "source_name": "telemetry_summaries",
            "code": "STALE_TELEMETRY",
            "message": f"Telemetry for runtime {runtime_id or 'unknown'} is stale.",
        })
    elif freshness in {"degraded", "missing", "partial", "timeout", "unavailable", "unhealthy"}:
        issues.append({
            "source_name": "telemetry_summaries",
            "code": "DEGRADED_TELEMETRY",
            "message": f"Telemetry for runtime {runtime_id or 'unknown'} is degraded.",
        })
    return issues


def _management_portfolio_source_status(issues: List[Dict[str, Any]]) -> str:
    codes = {str(issue.get("code") or "") for issue in issues}
    if "STALE_TELEMETRY" in codes:
        return "stale"
    if codes:
        return "degraded"
    return "ok"


def _management_portfolio_risk_state(
    *,
    source_status: str,
    source_issues: List[Dict[str, Any]],
    deployment_stage: str,
) -> str:
    codes = {str(issue.get("code") or "") for issue in source_issues}
    if "MISSING_PERSONA_BINDING" in codes:
        return "missing_binding"
    if "STALE_TELEMETRY" in codes:
        return "stale_telemetry"
    if source_status == "degraded":
        return "degraded_source"
    if deployment_stage == "live":
        return "live_exposure"
    if deployment_stage == "canary":
        return "canary_exposure"
    if deployment_stage == "paper":
        return "paper_exposure"
    return "unknown"


def _management_portfolio_identity(
    *,
    portfolio_id: str,
    capital_pool_id: str,
    sleeve_id: str,
    paper_ledger_id: str,
    persona_id: str,
    runtime_id: str,
    runtime_binding_id: str,
    plan_id: str,
    strategy_id: str,
    artifact_id: str,
    broker_id: str,
    deployment_stage: str,
) -> Dict[str, Any]:
    stage = deployment_stage or "unknown"
    return {
        "portfolio_id": portfolio_id,
        "capital_pool_id": capital_pool_id,
        "capital_pool_ids": [capital_pool_id] if capital_pool_id else [],
        "sleeve_id": sleeve_id or None,
        "sleeve_ids": [sleeve_id] if sleeve_id else [],
        "paper_ledger_id": paper_ledger_id or None,
        "paper_ledger_ids": [paper_ledger_id] if paper_ledger_id else [],
        "persona_id": persona_id,
        "persona_ids": [persona_id] if persona_id else [],
        "runtime_id": runtime_id,
        "runtime_ids": [runtime_id] if runtime_id else [],
        "runtime_binding_id": runtime_binding_id,
        "runtime_binding_ids": [runtime_binding_id] if runtime_binding_id else [],
        "deployment_plan_id": plan_id,
        "deployment_plan_ids": [plan_id] if plan_id else [],
        "strategy_id": strategy_id,
        "strategy_ids": [strategy_id] if strategy_id else [],
        "artifact_id": artifact_id,
        "artifact_ids": [artifact_id] if artifact_id else [],
        "broker_id": broker_id,
        "broker_ids": [broker_id] if broker_id else [],
        "stage": stage,
        "deployment_stage": stage,
    }


def _management_portfolio_capital_scope(
    *,
    deployment_stage: str,
    capital_pool_id: str,
    sleeve_id: str,
    paper_ledger_id: str,
) -> Dict[str, Any]:
    if deployment_stage == "paper":
        scope_kind = "paper_ledger"
        scope_id = paper_ledger_id
    elif deployment_stage == "canary":
        scope_kind = "canary_sleeve"
        scope_id = sleeve_id
    elif deployment_stage == "live":
        scope_kind = "live_capital_pool"
        scope_id = capital_pool_id
    else:
        scope_kind = "unclassified"
        scope_id = capital_pool_id or sleeve_id or paper_ledger_id
    return {
        "stage": deployment_stage or "unknown",
        "scope_kind": scope_kind,
        "scope_id": scope_id or None,
        "paper_ledger_id": paper_ledger_id or None,
        "canary_sleeve_id": sleeve_id if deployment_stage == "canary" else None,
        "live_capital_pool_id": capital_pool_id if deployment_stage == "live" else None,
        "capital_pool_id": capital_pool_id or None,
        "sleeve_id": sleeve_id or None,
    }


def _management_portfolio_operator_links(
    *,
    persona_id: str,
    runtime_id: str,
    holding_id: str,
) -> Dict[str, Optional[str]]:
    query: Dict[str, str] = {}
    if persona_id:
        query["persona_id"] = persona_id
    if runtime_id:
        query["runtime_id"] = runtime_id
    query_string = urllib.parse.urlencode(query)
    attribution_href = "/management/performance-attribution"
    review_href = "/management/human-inbox"
    if query_string:
        attribution_href = f"{attribution_href}?{query_string}"
        review_href = f"{review_href}?{query_string}"
    return {
        "persona_fleet": f"/management/persona-fleet?persona_id={urllib.parse.quote(persona_id)}" if persona_id else None,
        "performance_attribution": attribution_href if query else None,
        "human_review": (
            f"{review_href}&target_type=portfolio_holding&target_id={urllib.parse.quote(holding_id)}"
            if query_string and holding_id
            else review_href if query else None
        ),
    }


def _management_portfolio_incident(metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    issues = metadata.get("source_issues") if isinstance(metadata.get("source_issues"), list) else []
    if not issues:
        return None
    runtime_id = str(metadata.get("runtime_id") or "")
    holding_id = str(metadata.get("holding_id") or runtime_id or "unassigned")
    risk_state = str(metadata.get("risk_state") or "degraded_source")
    severity = "high" if risk_state in {"missing_binding", "degraded_source"} else "medium"
    return {
        "id": f"portfolio-risk-{risk_state}-{holding_id}",
        "kind": risk_state,
        "status": "open",
        "severity": severity,
        "message": "; ".join(str(issue.get("message") or issue.get("code") or "") for issue in issues if issue),
        "risk_state": risk_state,
        "source_status": metadata.get("source_status"),
        "source_issues": issues,
        "identity": metadata.get("identity") or {},
        "source_refs": {
            "runtime_ids": [runtime_id] if runtime_id else [],
            "persona_ids": [metadata.get("persona_id")] if metadata.get("persona_id") else [],
            "capital_pool_ids": [metadata.get("capital_pool_id")] if metadata.get("capital_pool_id") else [],
        },
        "links": metadata.get("links") or {},
    }


def _management_normalized_status(record: Optional[Dict[str, Any]]) -> str:
    if not isinstance(record, dict):
        return "unknown"
    val = record.get("status") or record.get("state") or record.get("lifecycle_state") or "unknown"
    return str(val).strip().lower()


def _management_first_float(record: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        parts = key.split(".")
        cur: Any = record
        for part in parts:
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                cur = None
                break
        f = _management_as_float(cur)
        if f is not None:
            return f
    return None


def _management_sum_numeric(items: Any, field: str) -> Optional[float]:
    vals = [_management_as_float(it.get(field)) for it in items if _management_as_float(it.get(field)) is not None]
    return round(sum(vals), 6) if vals else None


def _management_latest_timestamp(items: Any, field: str) -> Optional[str]:
    timestamps = [str(it.get(field) or "").strip() for it in items if str(it.get(field) or "").strip()]
    return max(timestamps) if timestamps else None


def _management_count_by(items: Any, field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for it in items:
        k = str(it.get(field) or "unknown").strip().lower()
        counts[k] = counts.get(k, 0) + 1
    return counts


def _management_telemetry_rollup(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    pnl_values: List[float] = []
    drawdown_values: List[float] = []
    fill_rates: List[float] = []
    total_trades = 0
    latest_collected_at: Optional[str] = None
    for record in records:
        pnl = _management_first_float(record, "pnl", "summary.total_pnl", "summary.pnl")
        drawdown = _management_first_float(record, "drawdown", "max_drawdown", "summary.max_drawdown")
        fill_rate = _management_first_float(record, "fill_rate", "summary.fill_rate")
        trades = _management_first_float(record, "total_trades", "summary.total_trades")
        collected_at = str(record.get("collected_at") or record.get("collectedAt") or record.get("updated_at") or "").strip()
        if pnl is not None:
            pnl_values.append(pnl)
        if drawdown is not None:
            drawdown_values.append(drawdown)
        if fill_rate is not None:
            fill_rates.append(fill_rate)
        if trades is not None:
            total_trades += int(trades)
        if collected_at and (latest_collected_at is None or collected_at > latest_collected_at):
            latest_collected_at = collected_at
    return {
        "runtime_count": len(records),
        "total_pnl": round(sum(pnl_values), 6) if pnl_values else None,
        "max_drawdown": max(drawdown_values) if drawdown_values else None,
        "average_fill_rate": round(sum(fill_rates) / len(fill_rates), 6) if fill_rates else None,
        "total_trades": total_trades,
        "latest_collected_at": latest_collected_at,
    }


def _management_portfolio_book_entry(
    pool: Dict[str, Any],
    *,
    bindings: List[Dict[str, Any]],
    deployment_plans: List[Dict[str, Any]],
    runtime_bindings: List[Dict[str, Any]],
    telemetry_by_runtime_id: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    pool_id = str(pool.get("pool_id") or pool.get("id") or "").strip()
    pool_bindings = [
        b for b in bindings
        if str(b.get("capital_pool_id") or b.get("pool_id") or "").strip() == pool_id
    ]
    pool_binding_ids = {
        str(b.get("binding_id") or b.get("id") or b.get("persona_capital_binding_id") or "").strip()
        for b in pool_bindings
    }
    pool_binding_ids.discard("")
    pool_plans = [
        p for p in deployment_plans
        if str(p.get("capital_pool_id") or p.get("target_pool_id") or p.get("pool_id") or "").strip() == pool_id
        or bool(pool_binding_ids.intersection(str(v) for v in (p.get("binding_ids") or [])))
    ]
    pool_plan_ids = {str(p.get("plan_id") or p.get("id") or "").strip() for p in pool_plans}
    pool_plan_ids.discard("")

    pool_runtimes = [
        r for r in runtime_bindings
        if str(r.get("capital_pool_id") or r.get("pool_id") or "").strip() == pool_id
        or str(r.get("plan_id") or r.get("deployment_plan_id") or "").strip() in pool_plan_ids
    ]
    telemetry_records = [
        telemetry_by_runtime_id[rid]
        for rid in (str(r.get("runtime_id") or r.get("id") or r.get("binding_id") or "").strip() for r in pool_runtimes)
        if rid in telemetry_by_runtime_id
    ]
    telemetry = _management_telemetry_rollup(telemetry_records)

    risk_budget = _management_as_float(pool.get("risk_budget"))
    current_exposure = _management_as_float(pool.get("current_exposure"))
    utilization = round(current_exposure / risk_budget, 6) if current_exposure is not None and risk_budget not in (None, 0) else None

    runtime_ids_set = {
        str(r.get("runtime_id") or r.get("id") or r.get("binding_id") or "").strip()
        for r in pool_runtimes
        if str(r.get("runtime_id") or r.get("id") or r.get("binding_id") or "").strip()
    }
    if pool.get("runtime_id"):
        runtime_ids_set.add(str(pool["runtime_id"]).strip())
    runtime_ids = sorted(runtime_ids_set)

    persona_ids_set = {str(b.get("persona_id") or "").strip() for b in pool_bindings}
    if pool.get("persona_id"):
        persona_ids_set.add(str(pool["persona_id"]).strip())
    persona_ids_set.discard("")

    strategy_ids_set = {
        str(x.get("strategy_id") or "").strip()
        for x in pool_bindings + pool_plans + pool_runtimes
    }
    if pool.get("strategy_id"):
        strategy_ids_set.add(str(pool["strategy_id"]).strip())
    strategy_ids_set.discard("")

    sleeve_ids_set = {str(x.get("sleeve_id") or "").strip() for x in pool_bindings + pool_plans + pool_runtimes}
    if pool.get("sleeve_id"):
        sleeve_ids_set.add(str(pool["sleeve_id"]).strip())
    sleeve_ids_set.discard("")

    artifact_ids_set = {str(x.get("artifact_id") or "").strip() for x in pool_plans + pool_runtimes}
    if pool.get("artifact_id"):
        artifact_ids_set.add(str(pool["artifact_id"]).strip())
    artifact_ids_set.discard("")

    broker_ids_set = {str(x.get("broker_id") or "").strip() for x in pool_bindings + pool_plans + pool_runtimes}
    if pool.get("broker_id"):
        broker_ids_set.add(str(pool["broker_id"]).strip())
    broker_ids_set.discard("")

    active_bindings = [b for b in pool_bindings if _management_normalized_status(b) == "active" or str(b.get("validity") or "").strip().lower() == "active"]
    approved_plans = [p for p in pool_plans if _management_normalized_status(p) in {"approved", "executing", "executed", "active"}]
    active_runtimes = [r for r in pool_runtimes if _management_normalized_status(r) in {"active", "running", "healthy"}]

    return {
        "id": pool_id,
        "pool_id": pool_id,
        "capital_pool_id": pool_id,
        "name": pool.get("name") or pool_id,
        "status": pool.get("status") or "unknown",
        "risk_policy_ref": pool.get("risk_policy_ref"),
        "owner": {"id": pool.get("owner_id") or pool.get("owner"), "type": pool.get("owner_type")},
        "currency": pool.get("currency"),
        "risk_budget": risk_budget,
        "current_exposure": current_exposure,
        "risk_budget_utilization": utilization,
        "risk_state": _management_exposure_risk_state(utilization),
        "exposure": {
            "amount": current_exposure,
            "risk_budget": risk_budget,
            "risk_budget_utilization": utilization,
            "source": "capital_pool",
        },
        "pnl": telemetry["total_pnl"],
        "total_pnl": telemetry["total_pnl"],
        "pnl_summary": telemetry,
        "binding_count": len(pool_bindings),
        "active_binding_count": len(active_bindings),
        "deployment_count": len(pool_plans),
        "approved_deployment_count": len(approved_plans),
        "runtime_count": len(pool_runtimes),
        "active_runtime_count": len(active_runtimes),
        "paper_runtime_count": len([r for r in pool_runtimes if str(r.get("deployment_stage") or r.get("deployment_mode") or "").lower() == "paper"]),
        "live_runtime_count": len([r for r in pool_runtimes if str(r.get("deployment_stage") or r.get("deployment_mode") or "").lower() == "live"]),
        "deployment_stages": sorted({str(x.get("deployment_stage") or x.get("target_stage") or "").strip() for x in pool_plans + pool_runtimes if str(x.get("deployment_stage") or x.get("target_stage") or "").strip()}),
        "binding_ids": sorted(pool_binding_ids),
        "deployment_ids": sorted(pool_plan_ids),
        "runtime_ids": runtime_ids,
        "persona_ids": sorted(persona_ids_set),
        "strategy_ids": sorted(strategy_ids_set),
        "sleeve_ids": sorted(sleeve_ids_set),
        "artifact_ids": sorted(artifact_ids_set),
        "broker_ids": sorted(broker_ids_set),
        "telemetry": telemetry,
        "links": {"capital_pool": f"/bff/capital-pools/{pool_id}" if pool_id else None},
    }


def _management_portfolio_book_pool_sources(
    read_store: Any,
    *,
    status: Optional[str] = None,
    risk_policy_ref: Optional[str] = None,
) -> Dict[str, Any]:
    capital_pools = (read_store.list_capital_pools(status=status, risk_policy_ref=risk_policy_ref) or []) if hasattr(read_store, "list_capital_pools") else []
    bindings = (read_store.list_bindings() or []) if hasattr(read_store, "list_bindings") else []
    deployment_plans = (read_store.list_deployment_plans() or []) if hasattr(read_store, "list_deployment_plans") else []
    runtime_bindings = (read_store.list_runtime_bindings() or []) if hasattr(read_store, "list_runtime_bindings") else []

    telemetry_by_runtime_id: Dict[str, Dict[str, Any]] = {}
    for runtime in runtime_bindings:
        runtime_id = _management_record_id(runtime, "runtime_id", "id", "binding_id")
        if not runtime_id:
            continue
        telemetry = read_store.get_telemetry_summary(runtime_id) if hasattr(read_store, "get_telemetry_summary") else None
        if telemetry is not None:
            telemetry_by_runtime_id[runtime_id] = telemetry

    entries = [
        _management_portfolio_book_entry(
            pool,
            bindings=bindings,
            deployment_plans=deployment_plans,
            runtime_bindings=runtime_bindings,
            telemetry_by_runtime_id=telemetry_by_runtime_id,
        )
        for pool in capital_pools
    ]
    return {
        "capital_pools": capital_pools,
        "bindings": bindings,
        "deployment_plans": deployment_plans,
        "runtime_bindings": runtime_bindings,
        "telemetry_by_runtime_id": telemetry_by_runtime_id,
        "entries": sorted(entries, key=lambda entry: str(entry.get("pool_id") or "")),
    }


def _pm12_portfolio_book_response(
    read_store: Any,
    *,
    query_params: Optional[Dict[str, Any]] = None,
    page_token: Optional[str] = None,
    page_size: int = 50,
    utc_now_fn: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    from ..personas.service import _filter_by_common_identifiers
    from ..agora.performance.service import _pm12_page_slice
    qp = query_params or {}
    snapshot_at = utc_now_fn() if utc_now_fn else _default_utc_now()
    sources = _management_portfolio_book_pool_sources(
        read_store,
        status=qp.get("status"),
        risk_policy_ref=qp.get("risk_policy_ref"),
    )
    entries = sources["entries"]
    entries = _filter_by_common_identifiers(
        entries,
        persona_id=qp.get("personaId") or qp.get("persona_id"),
        persona=qp.get("persona"),
        runtime_id=qp.get("runtimeId") or qp.get("runtime_id"),
        runtime=qp.get("runtime"),
        strategy_id=qp.get("strategyId") or qp.get("strategy_id"),
        strategy=qp.get("strategy"),
        capital_pool_id=qp.get("capitalPoolId") or qp.get("capital_pool_id") or qp.get("pool"),
        pool=qp.get("pool"),
        sleeve_id=qp.get("sleeveId") or qp.get("sleeve_id"),
        sleeve=qp.get("sleeve"),
        artifact_id=qp.get("artifactId") or qp.get("artifact_id"),
        artifact=qp.get("artifact"),
        broker_id=qp.get("brokerId") or qp.get("broker_id"),
        broker=qp.get("broker"),
        stage=qp.get("stage"),
        period=qp.get("period"),
        as_of=qp.get("asOf") or qp.get("as_of"),
    )
    total = len(entries)
    page_items, next_page_token = _pm12_page_slice(entries, page_token, page_size)
    portfolio_telemetry = _management_telemetry_rollup(list(sources["telemetry_by_runtime_id"].values()))

    dataset_source = getattr(read_store, "dataset_source", None) or (lambda ds: "canonical")
    tel_src = dataset_source("telemetry_summaries")
    tel_status = "unavailable" if tel_src in ("missing", "unavailable") or not sources["telemetry_by_runtime_id"] else "ok"

    summary = {
        "portfolio_book_status": "ready" if total else "empty",
        "capital_pool_count": len(sources["capital_pools"]),
        "active_capital_pool_count": len([p for p in sources["capital_pools"] if _management_normalized_status(p) in {"active", "ready"}]),
        "binding_count": len(sources["bindings"]),
        "active_binding_count": len([b for b in sources["bindings"] if _management_normalized_status(b) == "active" or str(b.get("validity") or "").strip().lower() == "active"]),
        "deployment_count": len(sources["deployment_plans"]),
        "approved_deployment_count": len([p for p in sources["deployment_plans"] if _management_normalized_status(p) in {"approved", "executing", "executed", "active"}]),
        "runtime_count": len(sources["runtime_bindings"]),
        "active_runtime_count": len([r for r in sources["runtime_bindings"] if _management_normalized_status(r) in {"active", "running", "healthy"}]),
        "paper_runtime_count": len([r for r in sources["runtime_bindings"] if str(r.get("deployment_stage") or r.get("deployment_mode") or "").lower() == "paper"]),
        "live_runtime_count": len([r for r in sources["runtime_bindings"] if str(r.get("deployment_stage") or r.get("deployment_mode") or "").lower() == "live"]),
        "telemetry_runtime_count": portfolio_telemetry["runtime_count"],
        "total_pnl": portfolio_telemetry["total_pnl"],
        "max_drawdown": portfolio_telemetry["max_drawdown"],
        "average_fill_rate": portfolio_telemetry["average_fill_rate"],
        "total_trades": portfolio_telemetry["total_trades"],
        "latest_telemetry_at": portfolio_telemetry["latest_collected_at"],
    }
    surfaces = {
        "portfolio_book": {"status": "degraded" if tel_status == "unavailable" else "ok", "source": "bff_composed", "snapshot_at": snapshot_at},
        "capital_pools": {"status": "ok", "source": dataset_source("capital_pools"), "snapshot_at": snapshot_at},
        "persona_bindings": {"status": "ok", "source": dataset_source("persona_bindings"), "snapshot_at": snapshot_at},
        "deployment_plans": {"status": "ok", "source": dataset_source("deployment_plans"), "snapshot_at": snapshot_at},
        "runtime_bindings": {"status": "ok", "source": dataset_source("runtime_bindings"), "snapshot_at": snapshot_at},
        "telemetry_summaries": {"status": tel_status, "source": tel_src, "snapshot_at": snapshot_at},
    }
    return {
        "data": {
            "summary": summary,
            "items": page_items,
        },
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": surfaces,
            "total": total,
        },
    }


def _pm12_portfolio_book_pools_response(
    read_store: Any,
    *,
    query_params: Optional[Dict[str, Any]] = None,
    page_token: Optional[str] = None,
    page_size: int = 50,
    utc_now_fn: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    from ..personas.service import _filter_by_common_identifiers
    from ..agora.performance.service import _pm12_page_slice
    qp = query_params or {}
    snapshot_at = utc_now_fn() if utc_now_fn else _default_utc_now()
    sources = _management_portfolio_book_pool_sources(
        read_store,
        status=qp.get("status"),
        risk_policy_ref=qp.get("risk_policy_ref"),
    )
    entries = sources["entries"]
    entries = _filter_by_common_identifiers(
        entries,
        persona_id=qp.get("personaId") or qp.get("persona_id"),
        persona=qp.get("persona"),
        runtime_id=qp.get("runtimeId") or qp.get("runtime_id"),
        runtime=qp.get("runtime"),
        strategy_id=qp.get("strategyId") or qp.get("strategy_id"),
        strategy=qp.get("strategy"),
        capital_pool_id=qp.get("capitalPoolId") or qp.get("capital_pool_id") or qp.get("pool"),
        pool=qp.get("pool"),
        sleeve_id=qp.get("sleeveId") or qp.get("sleeve_id"),
        sleeve=qp.get("sleeve"),
        artifact_id=qp.get("artifactId") or qp.get("artifact_id"),
        artifact=qp.get("artifact"),
        broker_id=qp.get("brokerId") or qp.get("broker_id"),
        broker=qp.get("broker"),
        stage=qp.get("stage"),
        period=qp.get("period"),
        as_of=qp.get("asOf") or qp.get("as_of"),
    )
    total = len(entries)
    page_items, next_page_token = _pm12_page_slice(entries, page_token, page_size)
    portfolio_telemetry = _management_telemetry_rollup(list(sources["telemetry_by_runtime_id"].values()))

    dataset_source = getattr(read_store, "dataset_source", None) or (lambda ds: "canonical")
    tel_src = dataset_source("telemetry_summaries")
    tel_status = "unavailable" if tel_src in ("missing", "unavailable") or not sources["telemetry_by_runtime_id"] else "ok"

    risk_budgets = [item["risk_budget"] for item in entries if item.get("risk_budget") is not None]
    current_exposures = [item["current_exposure"] for item in entries if item.get("current_exposure") is not None]
    rb_total = round(sum(risk_budgets), 6) if risk_budgets else None
    ce_total = round(sum(current_exposures), 6) if current_exposures else None
    util = round(ce_total / rb_total, 6) if ce_total is not None and rb_total not in (None, 0) else None

    summary = {
        "total_pools": total,
        "returned_pools": len(page_items),
        "risk_budget_total": rb_total,
        "current_exposure_total": ce_total,
        "risk_budget_utilization": util,
        "telemetry_runtime_count": portfolio_telemetry["runtime_count"],
        "total_pnl": portfolio_telemetry["total_pnl"],
    }
    return {
        "data": {
            "summary": summary,
            "items": page_items,
        },
        "page_info": {"next_page_token": next_page_token, "total": total, "page_size": page_size},
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {
                "portfolio_book_pools": {"status": "degraded" if tel_status == "unavailable" else "ok", "source": "bff_composed", "snapshot_at": snapshot_at},
                "capital_pools": {"status": "ok", "source": dataset_source("capital_pools"), "snapshot_at": snapshot_at},
            },
            "composition_sources": ["GET /bff/capital-pools"],
            "total": total,
        },
    }


def _pm12_portfolio_book_exposure_response(
    read_store: Any,
    *,
    query_params: Optional[Dict[str, Any]] = None,
    page_token: Optional[str] = None,
    page_size: int = 50,
    utc_now_fn: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    from ..personas.service import _filter_by_common_identifiers
    from ..agora.performance.service import _pm12_page_slice
    qp = query_params or {}
    snapshot_at = utc_now_fn() if utc_now_fn else _default_utc_now()
    sources = _management_portfolio_book_pool_sources(read_store)
    exposure_items = [_management_portfolio_book_exposure_item(entry) for entry in sources["entries"]]
    exposure_items = _filter_by_common_identifiers(
        exposure_items,
        persona_id=qp.get("personaId") or qp.get("persona_id"),
        persona=qp.get("persona"),
        runtime_id=qp.get("runtimeId") or qp.get("runtime_id"),
        runtime=qp.get("runtime"),
        strategy_id=qp.get("strategyId") or qp.get("strategy_id"),
        strategy=qp.get("strategy"),
        capital_pool_id=qp.get("capitalPoolId") or qp.get("capital_pool_id") or qp.get("pool"),
        pool=qp.get("pool"),
        sleeve_id=qp.get("sleeveId") or qp.get("sleeve_id"),
        sleeve=qp.get("sleeve"),
        artifact_id=qp.get("artifactId") or qp.get("artifact_id"),
        artifact=qp.get("artifact"),
        broker_id=qp.get("brokerId") or qp.get("broker_id"),
        broker=qp.get("broker"),
        stage=qp.get("stage"),
        period=qp.get("period"),
        as_of=qp.get("asOf") or qp.get("as_of"),
    )
    total = len(exposure_items)
    page_items, next_page_token = _pm12_page_slice(exposure_items, page_token, page_size)

    dataset_source = getattr(read_store, "dataset_source", None) or (lambda ds: "canonical")
    tel_src = dataset_source("telemetry_summaries")
    tel_status = "unavailable" if tel_src in ("missing", "unavailable") or not sources["telemetry_by_runtime_id"] else "ok"

    risk_budgets = [item["risk_budget"] for item in exposure_items if item.get("risk_budget") is not None]
    current_exposures = [item["current_exposure"] for item in exposure_items if item.get("current_exposure") is not None]
    available_budgets = [item["available_budget"] for item in exposure_items if item.get("available_budget") is not None]
    pnls = [item["pnl"] for item in exposure_items if item.get("pnl") is not None]
    rb_total = round(sum(risk_budgets), 6) if risk_budgets else None
    ce_total = round(sum(current_exposures), 6) if current_exposures else None
    av_total = round(sum(available_budgets), 6) if available_budgets else None
    util = round(ce_total / rb_total, 6) if ce_total is not None and rb_total not in (None, 0) else None

    matched_runtime_ids = {rid for it in exposure_items for rid in (it.get("source_refs") or {}).get("runtime_ids", [])}
    tel_runtime_count = len([rid for rid in matched_runtime_ids if rid in sources["telemetry_by_runtime_id"]])

    summary = {
        "exposure_count": total,
        "returned_exposure_count": len(page_items),
        "risk_budget_total": rb_total,
        "current_exposure_total": ce_total,
        "available_budget_total": av_total,
        "risk_budget_utilization": util,
        "over_budget_count": sum(1 for it in exposure_items if it.get("risk_state") == "over_budget"),
        "near_limit_count": sum(1 for it in exposure_items if it.get("risk_state") == "near_limit"),
        "unknown_exposure_count": sum(1 for it in exposure_items if it.get("risk_state") == "unknown"),
        "telemetry_runtime_count": tel_runtime_count,
        "total_pnl": round(sum(pnls), 6) if pnls else None,
    }
    return {
        "data": {
            "id": "pm12-portfolio-book-exposure",
            "items": page_items,
            "summary": summary,
        },
        "page_info": {"next_page_token": next_page_token, "total": total, "page_size": page_size},
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {
                "portfolio_book_exposure": {"status": "degraded" if tel_status == "unavailable" else "ok", "source": "bff_composed", "snapshot_at": snapshot_at},
                "capital_pools": {"status": "ok", "source": dataset_source("capital_pools"), "snapshot_at": snapshot_at},
            },
            "policy": "read_only_portfolio_exposure",
            "composition_sources": ["GET /api/v1/telemetry/{runtime_id}/summary"],
            "total": total,
        },
    }


def _pm12_portfolio_book_holdings_response(
    read_store: Any,
    *,
    query_params: Optional[Dict[str, Any]] = None,
    page_token: Optional[str] = None,
    page_size: int = 50,
    utc_now_fn: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    from ..agora.performance.service import _pm12_page_slice
    qp = query_params or {}
    snapshot_at = utc_now_fn() if utc_now_fn else _default_utc_now()

    runtime_bindings = (read_store.list_runtime_bindings(include_market_persona_defaults=True) or []) if hasattr(read_store, "list_runtime_bindings") else []
    deployment_plans = (read_store.list_deployment_plans() or []) if hasattr(read_store, "list_deployment_plans") else []
    bindings = (read_store.list_bindings(include_market_persona_defaults=True) or []) if hasattr(read_store, "list_bindings") else []
    capital_pools = (read_store.list_capital_pools(include_market_persona_defaults=True) or []) if hasattr(read_store, "list_capital_pools") else []

    plans_by_id = {str(p.get("plan_id") or p.get("id") or "").strip(): p for p in deployment_plans}
    bindings_by_id = {str(b.get("binding_id") or b.get("id") or b.get("persona_capital_binding_id") or "").strip(): b for b in bindings}
    pools_by_id = {str(p.get("pool_id") or p.get("id") or "").strip(): p for p in capital_pools}

    telemetry_by_runtime_id: Dict[str, Dict[str, Any]] = {}
    for r in runtime_bindings:
        rid = str(r.get("runtime_id") or r.get("id") or r.get("binding_id") or "").strip()
        if rid:
            t = read_store.get_telemetry_summary(rid) if hasattr(read_store, "get_telemetry_summary") else None
            if t is not None:
                telemetry_by_runtime_id[rid] = t

    holding_items: List[Dict[str, Any]] = []
    for runtime in runtime_bindings:
        rid = str(runtime.get("runtime_id") or runtime.get("id") or runtime.get("binding_id") or "").strip()
        telemetry = telemetry_by_runtime_id.get(rid, {})
        plan = plans_by_id.get(str(runtime.get("plan_id") or runtime.get("deployment_plan_id") or "").strip(), {})
        plan_binding_ids = [str(v).strip() for v in (plan.get("binding_ids") or []) if str(v).strip()]
        persona_binding_id = str(runtime.get("persona_capital_binding_id") or (plan_binding_ids[0] if plan_binding_ids else "")).strip()
        persona_binding = bindings_by_id.get(persona_binding_id, {})
        pool_id = str(runtime.get("capital_pool_id") or plan.get("capital_pool_id") or plan.get("target_pool_id") or persona_binding.get("capital_pool_id") or "").strip()
        capital_pool = pools_by_id.get(pool_id, {})
        position_records = telemetry.get("positions") if isinstance(telemetry.get("positions"), list) else []
        positions = position_records or [{}]
        position_source_count = len(position_records)
        for index, position in enumerate(positions):
            entry = _management_portfolio_holding_entry(
                runtime,
                position,
                position_index=index,
                plan=plan,
                persona_binding=persona_binding,
                capital_pool=capital_pool,
                telemetry=telemetry,
                position_source_count=position_source_count,
            )
            holding_items.append(entry)

    capital_pool_id = qp.get("capital_pool_id") or qp.get("pool")
    if capital_pool_id:
        req = {x.strip() for x in str(capital_pool_id).split(",") if x.strip()}
        holding_items = [it for it in holding_items if str(it.get("capital_pool_id") or "") in req]
    persona_id = qp.get("persona_id") or qp.get("personaId")
    if persona_id:
        req = {x.strip() for x in str(persona_id).split(",") if x.strip()}
        holding_items = [it for it in holding_items if str(it.get("persona_id") or "") in req]
    runtime_id = qp.get("runtime_id") or qp.get("runtimeId")
    if runtime_id:
        req = {x.strip() for x in str(runtime_id).split(",") if x.strip()}
        holding_items = [it for it in holding_items if str(it.get("runtime_id") or "") in req]
    deployment_stage = qp.get("deployment_stage") or qp.get("stage")
    if deployment_stage:
        req = {x.strip().lower() for x in str(deployment_stage).split(",") if x.strip()}
        holding_items = [it for it in holding_items if str(it.get("deployment_stage") or "").lower() in req]
    broker_id = qp.get("broker_id") or qp.get("brokerId")
    if broker_id:
        req = {x.strip() for x in str(broker_id).split(",") if x.strip()}
        holding_items = [it for it in holding_items if str(it.get("broker_id") or "") in req]
    status = qp.get("status")
    if status:
        req = {x.strip().lower() for x in str(status).split(",") if x.strip()}
        holding_items = [it for it in holding_items if str(it.get("status") or "").lower() in req]
    source_status = qp.get("source_status")
    if source_status:
        req = {x.strip().lower() for x in str(source_status).split(",") if x.strip()}
        holding_items = [it for it in holding_items if str(it.get("source_status") or "").lower() in req]
    stale_telemetry = qp.get("stale_telemetry")
    if stale_telemetry is not None:
        val = str(stale_telemetry).strip().lower() in ("true", "1")
        holding_items = [it for it in holding_items if bool(it.get("telemetry_stale")) is val]
    risk_state = qp.get("risk_state")
    if risk_state:
        req = {x.strip().lower() for x in str(risk_state).split(",") if x.strip()}
        holding_items = [it for it in holding_items if str(it.get("risk_state") or "").lower() in req]
    q = qp.get("q")
    if q:
        needle = str(q).strip().lower()
        holding_items = [it for it in holding_items if needle in " ".join(str(it.get(k) or "").lower() for k in ("holding_id", "symbol", "runtime_id", "capital_pool_id", "persona_id", "strategy_id"))]

    holding_items = sorted(
        holding_items,
        key=lambda item: (
            str(item.get("capital_pool_id") or ""),
            str(item.get("runtime_id") or ""),
            str(item.get("symbol") or ""),
            str(item.get("holding_id") or ""),
        ),
    )
    total = len(holding_items)
    page_items, next_page_token = _pm12_page_slice(holding_items, page_token, page_size)
    incidents = [
        incident for incident in (_management_portfolio_incident(item) for item in holding_items)
        if incident is not None
    ]
    active_statuses = {"active", "running", "healthy", "bound"}
    summary = {
        "holding_count": total,
        "returned_holding_count": len(page_items),
        "source_row_count": sum(int(item.get("source_row_count") or 0) for item in holding_items),
        "active_holding_count": len([item for item in holding_items if str(item.get("status") or "").lower() in active_statuses]),
        "paper_holding_count": len([item for item in holding_items if str(item.get("deployment_stage") or "").lower() == "paper"]),
        "live_holding_count": len([item for item in holding_items if str(item.get("deployment_stage") or "").lower() == "live"]),
        "runtime_count": len({str(item.get("runtime_id") or "") for item in holding_items if str(item.get("runtime_id") or "")}),
        "telemetry_runtime_count": len({str(item.get("runtime_id") or "") for item in holding_items if item.get("telemetry_available")}),
        "stale_row_count": len([item for item in holding_items if item.get("telemetry_stale")]),
        "missing_binding_count": len([item for item in holding_items if any(issue.get("code") == "MISSING_PERSONA_BINDING" for issue in (item.get("source_issues") or []))]),
        "degraded_source_count": len([item for item in holding_items if str(item.get("source_status") or "").lower() in {"degraded", "stale", "unavailable"}]),
        "incident_count": len(incidents),
        "total_notional": _management_sum_numeric(holding_items, "notional"),
        "total_market_value": _management_sum_numeric(holding_items, "market_value"),
        "total_unrealized_pnl": _management_sum_numeric(holding_items, "unrealized_pnl"),
        "total_realized_pnl": _management_sum_numeric(holding_items, "realized_pnl"),
        "total_pnl": _management_sum_numeric(holding_items, "total_pnl"),
        "latest_mark_at": _management_latest_timestamp(holding_items, "last_mark_at"),
        "source_status_counts": _management_count_by(holding_items, "source_status"),
        "risk_state_counts": _management_count_by(holding_items, "risk_state"),
        "by_stage": _management_count_by(holding_items, "deployment_stage"),
        "by_broker": _management_count_by(holding_items, "broker_id"),
    }
    dataset_source = getattr(read_store, "dataset_source", None) or (lambda ds: "canonical")
    tel_src = dataset_source("telemetry_summaries")
    tel_status = "unavailable" if tel_src in ("missing", "unavailable") or not telemetry_by_runtime_id else "ok"

    surfaces = {
        "portfolio_book_holdings": {"status": "degraded" if tel_status == "unavailable" else "ok", "source": "bff_composed", "snapshot_at": snapshot_at},
        "runtime_bindings": {"status": "ok", "source": dataset_source("runtime_bindings"), "snapshot_at": snapshot_at},
        "telemetry_summaries": {"status": tel_status, "source": tel_src, "snapshot_at": snapshot_at},
    }
    filters_dict = {k: v for k, v in qp.items() if v is not None}
    return {
        "data": {
            "summary": summary,
            "items": page_items,
        },
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": surfaces,
            "total": total,
            "incidents": incidents,
            "filters": filters_dict,
            "composition_sources": ["GET /api/v1/telemetry/{runtime_id}/summary"],
        },
    }
