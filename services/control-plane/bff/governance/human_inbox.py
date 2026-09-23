"""Human Inbox domain projection and recommendation decision helpers.

Encapsulates human inbox projection from command-store records,
sanitization of promotion recommendations, decision tracking, and
timeout configuration decoupled from main.py globals.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
from urllib.parse import quote

try:
    from ..models import CommandType, ObjectType, OperatorIdentity
except (ImportError, ValueError):
    from models import CommandType, ObjectType, OperatorIdentity

from .promotion_review import (
    _PROMOTION_REVIEW_DECISIONS,
    _promotion_review_clean_id,
    _promotion_review_quarter_from_id,
    _promotion_review_record_revision_id,
    _promotion_review_revision_id,
    _promotion_review_revision_recommendation_id,
    _promotion_review_stage_path,
    _promotion_review_target_id,
)

_HUMAN_INBOX_INACTIVE_COMMAND_STATUSES = {
    "rejected",
    "cancelled",
    "expired",
    "failed",
    "timed_out",
    "timeout",
}

_HUMAN_INBOX_PROMOTION_SNAPSHOT_SCALARS = {
    "action_id",
    "action_label",
    "archetype",
    "binding_state",
    "capital_mode",
    "capital_pool_id",
    "capital_scope",
    "capital_scope_id",
    "capital_sleeve_id",
    "current_weight",
    "current_weight_source",
    "deployment_stage",
    "eligible",
    "exclusion_reason",
    "formula_version",
    "id",
    "name",
    "owner",
    "paper_ledger_id",
    "priority",
    "quarter",
    "rank",
    "ranking_snapshot_id",
    "rationale",
    "recommendation_id",
    "risk",
    "risk_level",
    "score",
    "source_confidence",
    "stage",
    "state",
    "target_weight",
    "tier",
    "tier_id",
    "tier_label",
    "persona_id",
}

_HUMAN_INBOX_PROMOTION_SNAPSHOT_STRING_LISTS = {
    "artifact_ids",
    "binding_ids",
    "broker_ids",
    "capital_pool_ids",
    "exclusion_codes",
    "exclusion_reasons",
    "rationale_codes",
    "runtime_ids",
    "sleeve_ids",
    "strategy_ids",
}


def human_inbox_surface_timeout_seconds() -> float:
    raw = os.getenv("PANTHEON_BFF_HUMAN_INBOX_TIMEOUT_SECONDS", "3.0").strip()
    try:
        val = float(raw)
        return max(0.1, val)
    except (TypeError, ValueError):
        return 3.0


def _human_inbox_trusted_promotion_submission(command: Dict[str, Any]) -> bool:
    from ..personas.service import (
        _human_inbox_trusted_promotion_submission as _personas_trusted,
    )
    return _personas_trusted(command)


def _human_inbox_priority(value: Any, *, fallback: str = "medium") -> str:
    cleaned = str(value or "").strip().lower()
    if cleaned in {"critical", "high", "medium", "low"}:
        return cleaned
    return fallback


def _human_inbox_promotion_recommendation_id(command: Dict[str, Any]) -> str:
    params = command.get("params") if isinstance(command.get("params"), dict) else {}
    target = command.get("target") if isinstance(command.get("target"), dict) else {}
    return str(
        params.get("recommendation_id")
        or params.get("recommendationId")
        or params.get("review_id")
        or params.get("promotion_review_id")
        or target.get("id")
        or ""
    ).strip()


def _human_inbox_sanitize_promotion_snapshot(
    command: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not _human_inbox_trusted_promotion_submission(command):
        return None
    params = command.get("params") if isinstance(command.get("params"), dict) else {}
    recommendation_id = _human_inbox_promotion_recommendation_id(command)
    expected_quarter = str(_promotion_review_quarter_from_id(recommendation_id) or "").upper()
    persona_id = str(params.get("persona_id") or "").strip()
    action_id = str(
        params.get("recommendation_action_id")
        or params.get("recommendationActionId")
        or ""
    ).strip()
    raw_snapshot = params.get("source_recommendation")
    if raw_snapshot is not None and not isinstance(raw_snapshot, dict):
        return None
    raw = raw_snapshot if isinstance(raw_snapshot, dict) else {}

    for snapshot_id in (raw.get("id"), raw.get("recommendation_id")):
        if snapshot_id not in (None, "") and str(snapshot_id).strip() != recommendation_id:
            return None
    snapshot_quarter = str(raw.get("quarter") or expected_quarter).strip().upper()
    snapshot_persona = str(raw.get("persona_id") or persona_id).strip()
    snapshot_action = str(raw.get("action_id") or action_id).strip()
    if (
        snapshot_quarter != expected_quarter
        or snapshot_persona != persona_id
        or snapshot_action != action_id
    ):
        return None
    params_snapshot_id = str(params.get("ranking_snapshot_id") or "").strip()
    raw_snapshot_id = str(raw.get("ranking_snapshot_id") or "").strip()
    if params_snapshot_id and raw_snapshot_id != params_snapshot_id:
        return None

    sanitized: Dict[str, Any] = {}
    for key in _HUMAN_INBOX_PROMOTION_SNAPSHOT_SCALARS:
        value = raw.get(key)
        if value is None or isinstance(value, (dict, list)):
            continue
        sanitized[key] = value
    for key in _HUMAN_INBOX_PROMOTION_SNAPSHOT_STRING_LISTS:
        value = raw.get(key)
        if isinstance(value, list):
            sanitized[key] = [str(item) for item in value if isinstance(item, (str, int, float))]
    for key in ("components", "metrics"):
        value = raw.get(key)
        if isinstance(value, dict):
            sanitized[key] = {
                str(metric): number
                for metric, number in value.items()
                if isinstance(number, (int, float)) and not isinstance(number, bool)
            }

    sanitized.update(
        {
            "id": recommendation_id,
            "recommendation_id": recommendation_id,
            "quarter": expected_quarter,
            "persona_id": persona_id,
            "action_id": action_id,
            "name": sanitized.get("name") or params.get("persona_name") or persona_id,
            "priority": sanitized.get("priority") or params.get("priority") or "high",
            "risk_level": sanitized.get("risk_level") or params.get("risk_level") or "high",
            "rationale": sanitized.get("rationale")
            or params.get("rationale")
            or "Submitted ranking recommendation requires Human Gate review.",
            "evidence_refs": [],
            "evidence_ref_ids": [],
        }
    )
    if params_snapshot_id:
        sanitized["ranking_snapshot_id"] = params_snapshot_id
    review_revision_id = _promotion_review_record_revision_id(command)
    if not review_revision_id:
        return None
    sanitized["review_id"] = review_revision_id
    sanitized["promotion_review_id"] = review_revision_id
    stage_from = str(params.get("stage_from") or sanitized.get("stage") or sanitized.get("state") or "").strip()
    if stage_from:
        sanitized.setdefault("stage", stage_from)
        sanitized.setdefault("state", stage_from)
    expected_path = _promotion_review_stage_path(sanitized)
    for param_key, path_key in (
        ("stage_from", "from_stage"),
        ("stage_to", "target_stage"),
        ("review_kind", "review_kind"),
    ):
        value = str(params.get(param_key) or "").strip()
        if value and value != str(expected_path.get(path_key) or ""):
            return None
    return sanitized


def _human_inbox_submission_projection_from_record(
    command: Dict[str, Any],
    recommendation_id: str,
) -> Dict[str, Any]:
    params = command.get("params") if isinstance(command.get("params"), dict) else {}
    audit = command.get("audit") if isinstance(command.get("audit"), dict) else {}
    review_revision_id = _promotion_review_record_revision_id(command)
    return {
        "submitted": True,
        "submit_status": command.get("status"),
        "command_id": command.get("command_id"),
        "commandId": command.get("command_id"),
        "receipt_id": command.get("command_id"),
        "submitted_at": command.get("submitted_at"),
        "submitted_by": audit.get("operator_id") or audit.get("actor") or audit.get("actor_id"),
        "recommendation_id": recommendation_id,
        "review_id": review_revision_id,
        "promotion_review_id": review_revision_id,
        "recommendation_action_id": params.get("recommendation_action_id")
        or params.get("recommendationActionId"),
        "ranking_snapshot_id": params.get("ranking_snapshot_id"),
        "quarter": params.get("quarter"),
        "persona_id": params.get("persona_id"),
        "stage_from": params.get("stage_from"),
        "stage_to": params.get("stage_to"),
        "review_kind": params.get("review_kind"),
        "human_inbox_id": _promotion_review_target_id(review_revision_id),
        "live_capital_mutation": False,
        "requires_human_gate_decision": True,
    }


def _human_inbox_decision_recommendation_id(command: Dict[str, Any]) -> str:
    command_type = str(command.get("type") or "")
    if command_type not in {
        CommandType.HUMAN_GATE_APPROVE.value,
        CommandType.HUMAN_GATE_REJECT.value,
    }:
        return ""
    target = command.get("target") if isinstance(command.get("target"), dict) else {}
    if target.get("type") != ObjectType.HUMAN_GATE_ITEM.value:
        return ""
    params = command.get("params") if isinstance(command.get("params"), dict) else {}
    raw_target_id = str(target.get("id") or "").strip()
    review_revision_id = _promotion_review_clean_id(raw_target_id)
    if (
        not review_revision_id
        or raw_target_id != _promotion_review_target_id(review_revision_id)
    ):
        return ""
    recommendation_id = str(
        params.get("recommendation_id")
        or params.get("recommendationId")
        or _promotion_review_revision_recommendation_id(review_revision_id)
    ).strip()
    if (
        not recommendation_id
        or _promotion_review_revision_recommendation_id(review_revision_id)
        != recommendation_id
    ):
        return ""
    for key in (
        "human_gate_item_id",
        "humanGateItemId",
        "review_id",
        "reviewId",
        "promotion_review_id",
        "promotionReviewId",
    ):
        alias = params.get(key)
        if (
            alias not in (None, "")
            and _promotion_review_clean_id(alias) != review_revision_id
        ):
            return ""
    for key in ("recommendation_id", "recommendationId"):
        alias = params.get(key)
        if alias not in (None, "") and str(alias).strip() != recommendation_id:
            return ""
    ranking_snapshot_id = str(params.get("ranking_snapshot_id") or "").strip()
    if ranking_snapshot_id:
        if review_revision_id != _promotion_review_revision_id(
            recommendation_id,
            ranking_snapshot_id,
        ):
            return ""
    elif review_revision_id != recommendation_id:
        # A revision-aware decision without its snapshot lineage is unsafe.
        return ""
    return review_revision_id


def _human_inbox_decision_projection_from_record(command: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if str(command.get("status") or "").strip().lower() in _HUMAN_INBOX_INACTIVE_COMMAND_STATUSES:
        return None
    review_revision_id = _human_inbox_decision_recommendation_id(command)
    if not review_revision_id:
        return None
    params = command.get("params") if isinstance(command.get("params"), dict) else {}
    decision = str(params.get("decision") or "").strip().lower()
    if decision not in _PROMOTION_REVIEW_DECISIONS:
        return None
    command_type = str(command.get("type") or "")
    if command_type == CommandType.HUMAN_GATE_REJECT.value and decision != "reject":
        return None
    if command_type == CommandType.HUMAN_GATE_APPROVE.value and decision not in {
        "approve",
        "approve_with_conditions",
    }:
        return None
    audit = command.get("audit") if isinstance(command.get("audit"), dict) else {}
    projection: Dict[str, Any] = {
        "decision": decision,
        "decision_status": "accepted",
        "command_id": command.get("command_id"),
        "commandId": command.get("command_id"),
        "receipt_id": command.get("command_id"),
        "submitted_at": command.get("submitted_at"),
        "decided_at": command.get("submitted_at"),
        "decided_by": audit.get("operator_id") or audit.get("actor") or audit.get("actor_id"),
        "command_status": command.get("status"),
        "review_id": review_revision_id,
        "promotion_review_id": review_revision_id,
        "recommendation_id": params.get("recommendation_id")
        or params.get("recommendationId")
        or _promotion_review_revision_recommendation_id(
            review_revision_id
        ),
        "ranking_snapshot_id": params.get("ranking_snapshot_id"),
        "live_capital_mutation": False,
        "requires_human_gate_decision": True,
    }
    rationale = params.get("rationale") or params.get("reason") or params.get("rejection_reason") or params.get("memo")
    if rationale not in (None, ""):
        projection["rationale"] = str(rationale)
    if decision == "approve_with_conditions":
        conditions = params.get("conditions") or params.get("approval_conditions")
        if isinstance(conditions, list):
            projection["conditions"] = conditions
    return projection


def _human_inbox_promotion_review_from_projection(
    recommendation: Dict[str, Any],
    *,
    submission: Dict[str, Any],
    decision: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    recommendation_id = str(
        recommendation.get("recommendation_id")
        or recommendation.get("id")
        or ""
    )
    review_id = str(
        recommendation.get("promotion_review_id")
        or recommendation.get("review_id")
        or _promotion_review_revision_id(
            recommendation_id,
            recommendation.get("ranking_snapshot_id"),
        )
    )
    stage_path = _promotion_review_stage_path(recommendation)
    decision_status = "accepted" if decision else "pending"
    item: Dict[str, Any] = {
        **{
            key: json.loads(json.dumps(value))
            for key, value in recommendation.items()
            if key not in {"id", "status"}
        },
        "id": review_id,
        "review_id": review_id,
        "promotion_review_id": review_id,
        "recommendation_id": recommendation_id,
        "status": "decision_accepted" if decision else "pending_human_gate",
        "decision_status": decision_status,
        "submitted": True,
        "submit_status": submission.get("submit_status"),
        "human_inbox_id": _promotion_review_target_id(review_id),
        "allowed_decisions": sorted(_PROMOTION_REVIEW_DECISIONS),
        "allowedActions": {
            "canSubmit": False,
            "canApprove": not bool(decision),
            "canApproveWithConditions": not bool(decision),
            "canReject": not bool(decision),
        },
        "promotion_path": stage_path,
        "review_kind": stage_path.get("review_kind"),
        "source_recommendation": json.loads(json.dumps(recommendation)),
        "submission": submission,
        "governance": {
            "requires_human_gate_decision": True,
            "decision_status": decision_status,
            "live_capital_mutation": False,
            "direct_live_capital_mutation": False,
            "policy": "promotion_governance_human_gate_no_direct_live_capital",
        },
        "requires_human_gate_decision": True,
        "live_capital_mutation": False,
        "direct_live_capital_mutation": False,
        "policy": "promotion_governance_human_gate_no_direct_live_capital",
        "links": {
            "persona": f"/bff/personas/{recommendation.get('persona_id')}",
            "recommendation": "/bff/management/quarterly-ranking/recommendations",
            "detail": f"/bff/management/promotion-reviews/{quote(review_id, safe='')}",
            "decisions": f"/bff/management/promotion-reviews/{quote(review_id, safe='')}/decisions",
            "human_inbox": f"/bff/management/human-inbox/{quote(_promotion_review_target_id(review_id), safe='')}",
        },
    }
    if decision:
        item["decision"] = decision
    return item


def _submitted_promotion_review_record_from_command(
    command: Dict[str, Any],
    *,
    decision: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    recommendation = _human_inbox_sanitize_promotion_snapshot(command)
    if recommendation is None:
        return None
    recommendation_id = str(recommendation["recommendation_id"])
    review_id = _promotion_review_record_revision_id(command)
    if not review_id:
        return None
    submission = _human_inbox_submission_projection_from_record(
        command,
        recommendation_id,
    )
    return _human_inbox_promotion_review_from_projection(
        recommendation,
        submission=submission,
        decision=decision,
    )


def _submitted_promotion_review_records(
    identity: Any = None,
    *,
    snapshot_at: str = "",
    command_store: Any = None,
) -> List[Dict[str, Any]]:
    del identity, snapshot_at
    submissions: Dict[str, Dict[str, Any]] = {}
    decisions: Dict[str, Dict[str, Any]] = {}

    if command_store is not None:
        commands = getattr(command_store, "_get_all_commands", None)
        if callable(commands):
            all_cmds = commands()
        else:
            all_cmds = []
    else:
        all_cmds = []

    for command in all_cmds:
        if command.get("type") == CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT.value:
            recommendation = _human_inbox_sanitize_promotion_snapshot(command)
            if recommendation is not None:
                review_id = _promotion_review_record_revision_id(command)
                if review_id:
                    submissions[review_id] = command
            continue
        review_id = _human_inbox_decision_recommendation_id(command)
        decision = _human_inbox_decision_projection_from_record(command)
        if review_id and decision is not None:
            decisions[review_id] = decision

    records: List[Dict[str, Any]] = []
    for review_id, command in submissions.items():
        review = _submitted_promotion_review_record_from_command(
            command,
            decision=decisions.get(review_id),
        )
        if review is not None:
            records.append(review)
    return records


def _human_inbox_promotion_review_item(review: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    review_id = str(review.get("review_id") or review.get("promotion_review_id") or "").strip()
    if not review_id:
        return None
    decision_status = str(review.get("decision_status") or "pending").strip().lower() or "pending"
    status = "accepted" if decision_status == "accepted" else "pending"
    risk_level = str(review.get("risk_level") or "high").strip().lower() or "high"
    priority = _human_inbox_priority(review.get("priority") or risk_level, fallback="high")
    inbox_id = _promotion_review_target_id(review_id)
    return {
        "id": inbox_id,
        "item_id": inbox_id,
        "human_inbox_id": inbox_id,
        "category": "promotion_review",
        "action_kind": "promotion_review",
        "title": f"Promotion Review: {review.get('name') or review.get('persona_id') or review_id}",
        "status": status,
        "decision_status": decision_status,
        "priority": priority,
        "risk_level": risk_level,
        "persona_id": review.get("persona_id"),
        "quarter": review.get("quarter"),
        "review_id": review_id,
        "promotion_review_id": review_id,
        "recommendation_id": review.get("recommendation_id"),
        "ranking_snapshot_id": review.get("ranking_snapshot_id"),
        "data": review,
    }
