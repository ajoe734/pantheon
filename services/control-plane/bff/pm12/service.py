"""PM-12 Portfolio and Allocation surface domain service.

Owns allocation equality semantics, ranking snapshot assertion hashing,
portfolio book exposure projection, holding entry projection, recommendation
resolution, and performance attribution integration.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
import re
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException

try:
    from ..auth.policy import bff_error as _default_bff_error
    from ..models import ErrorCode, utc_now as _default_utc_now
except (ImportError, ValueError):
    from auth.policy import bff_error as _default_bff_error
    from models import ErrorCode, utc_now as _default_utc_now

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

_PM12_LEAGUE_FORMULA_VERSION = "pm12-default-v1"
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
        "rationale": "Quarterly score and risk posture support canary-review consideration.",
        "description": "Meets promotion criteria; request canary staging.",
    },
    "increase_research_budget": {
        "label": "Increase research budget",
        "title": "Increase Research Budget",
        "priority": "medium",
        "riskLevel": "low",
        "risk_level": "low",
        "rationale": "Quarterly score supports additional research-only budget.",
        "description": "High search efficiency; grant additional budget.",
    },
    "grant_tool_access": {
        "label": "Grant tool access",
        "title": "Grant Tool Access",
        "priority": "medium",
        "riskLevel": "low",
        "risk_level": "low",
        "rationale": "Quarterly score and execution posture support expanded tool access review.",
        "description": "Eligible for expanded tooling permissions.",
    },
    "reduce_capital_access": {
        "label": "Reduce capital access",
        "title": "Reduce Capital Access",
        "priority": "high",
        "riskLevel": "high",
        "risk_level": "high",
        "rationale": "Risk or overall score calls for capital-access reduction review.",
        "description": "Drawdown or performance degradation detected.",
    },
    "require_retraining": {
        "label": "Require retraining",
        "title": "Require Retraining",
        "priority": "medium",
        "riskLevel": "medium",
        "risk_level": "medium",
        "rationale": "Quarterly component scores indicate retraining should be reviewed.",
        "description": "Execution score below threshold; queue fine-tuning.",
    },
    "freeze_persona": {
        "label": "Freeze persona",
        "title": "Freeze Persona",
        "priority": "critical",
        "riskLevel": "critical",
        "risk_level": "critical",
        "rationale": "Quarterly score is below the freeze-review threshold.",
        "description": "Temporary operational pause for audit.",
    },
    "suspend_persona": {
        "label": "Suspend persona",
        "title": "Suspend Persona",
        "priority": "critical",
        "riskLevel": "critical",
        "risk_level": "critical",
        "rationale": "Quarterly score is below the suspension-review threshold.",
        "description": "Persistent poor performance; revoke execution rights.",
    },
    "retire_persona": {
        "label": "Retire persona",
        "title": "Retire Persona",
        "priority": "critical",
        "riskLevel": "critical",
        "risk_level": "critical",
        "rationale": "Quarterly score is below the retirement-review threshold.",
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
    if isinstance(value, bool):
        return value
    if isinstance(value, _NUMERIC_TYPES):
        try:
            d = Decimal(str(value))
            if not d.is_finite():
                raise ValueError("non-finite numeric value cannot be canonicalized")
            d = d.normalize()
            if d == d.to_integral():
                return int(d)
            return float(d)
        except Exception as exc:
            raise ValueError(f"unsupported numeric value: {value!r}") from exc
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return [_pm12_semantic_json_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _pm12_semantic_json_value(item)
            for key, item in value.items()
        }
    if value is None:
        return None
    raise ValueError(f"unsupported type for semantic value comparison: {type(value)!r}")


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
            "Allocation evaluation requires a BFF-admitted quarterly ranking snapshot.",
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
            "allocation evaluation has no lines",
            "The referenced evaluation record does not contain evaluated allocation lines.",
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


def _pm12_add_recommendation_action(action_ids: List[str], action_id: str) -> None:
    if action_id in _PM12_QUARTERLY_RECOMMENDATION_ACTIONS and action_id not in action_ids:
        action_ids.append(action_id)


def _management_number(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _pm12_recommendation_action_ids(item: Dict[str, Any]) -> List[str]:
    components = item.get("components") if isinstance(item.get("components"), dict) else {}
    overall = _management_number(item.get("score")) or _management_number(item.get("overall_score")) or 0.0
    risk_score = _management_number(components.get("risk_score"))
    execution_score = _management_number(components.get("execution_score"))
    activity_score = _management_number(components.get("activity_score"))
    action_ids: List[str] = []

    if overall >= 85.0 and (risk_score is None or risk_score >= 70.0) and (
        execution_score is None or execution_score >= 65.0
    ):
        _pm12_add_recommendation_action(action_ids, "promote_to_canary_candidate")
        _pm12_add_recommendation_action(action_ids, "increase_research_budget")
        _pm12_add_recommendation_action(action_ids, "grant_tool_access")
    elif overall >= 70.0 and (risk_score is None or risk_score >= 60.0):
        _pm12_add_recommendation_action(action_ids, "increase_research_budget")
        _pm12_add_recommendation_action(action_ids, "grant_tool_access")

    if risk_score is not None and risk_score < 55.0:
        _pm12_add_recommendation_action(action_ids, "reduce_capital_access")
    if (execution_score is not None and execution_score < 55.0) or (
        activity_score is not None and activity_score < 45.0
    ):
        _pm12_add_recommendation_action(action_ids, "require_retraining")
    if overall < 55.0:
        _pm12_add_recommendation_action(action_ids, "require_retraining")
        _pm12_add_recommendation_action(action_ids, "reduce_capital_access")
    if overall < 45.0:
        _pm12_add_recommendation_action(action_ids, "freeze_persona")
    if overall < 35.0:
        _pm12_add_recommendation_action(action_ids, "suspend_persona")
    if overall < 25.0:
        _pm12_add_recommendation_action(action_ids, "retire_persona")

    if not action_ids:
        _pm12_add_recommendation_action(action_ids, "require_retraining")
    return [
        action_id
        for action_id in _PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER
        if action_id in action_ids
    ]


def _pm12_quarterly_recommendation_item(
    item: Dict[str, Any],
    *,
    action_id: str,
    quarter_window: Dict[str, Any],
    evidence_refs: List[Dict[str, Any]],
    command_store: Any = None,
) -> Dict[str, Any]:
    action = _PM12_QUARTERLY_RECOMMENDATION_ACTIONS.get(action_id, {
        "label": action_id.replace("_", " ").title(),
        "title": action_id.replace("_", " ").title(),
        "description": "Governed recommendation action.",
        "priority": "medium",
        "risk_level": "medium",
        "rationale": "Governed recommendation action.",
    })
    persona_id = str(item.get("persona_id") or item.get("personaId") or item.get("id") or "")
    score = _management_number(item.get("score")) or _management_number(item.get("overall_score")) or 0.0
    evidence_sample = list(item.get("evidence_refs") or evidence_refs or [])[:5]
    evidence_ref_ids = [
        str(ref.get("refId") or ref.get("ref_id") or ref.get("id"))
        for ref in evidence_sample
        if ref.get("refId") or ref.get("ref_id") or ref.get("id")
    ]
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
        "rationale": f"{action.get('rationale', '')} Score={score:.2f}; tier={item.get('tier') or 'unknown'}.",
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

    matched_item: Optional[Dict[str, Any]] = None
    matched_action_id = ""
    for item in snapshot.get("items") or []:
        if not isinstance(item, dict):
            continue
        persona_id = str(item.get("persona_id") or "").strip()
        for action_id in _pm12_recommendation_action_ids(item):
            expected_id = f"pm12-{quarter.lower()}-{persona_id}-{action_id}"
            if expected_id == recommendation_id:
                matched_item = item
                matched_action_id = action_id
                break
        if matched_item is not None:
            break
    if matched_item is None:
        raise err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "recommendation is not in the admitted ranking snapshot",
            "The recommendation id/action/persona tuple was not materialized by the snapshot.",
            precondition_failed="recommendation_id",
        )
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
        "source_refs": {
            "runtime_ids": runtime_ids,
            "binding_ids": binding_ids,
            "deployment_ids": deployment_ids,
            "capital_pool_ids": [pool_id] if pool_id else [],
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

    return {
        "id": holding_id,
        "holding_id": holding_id,
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
    }
