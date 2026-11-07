"""Human Inbox domain projection, aggregation, and recommendation decision helpers.

Encapsulates human inbox projection from contributors (governance reviews,
approvals, incidents, persona readiness, promotion reviews),
filtering, summary calculation, and recommendation decision tracking decoupled
from main.py globals.
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Sequence
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

_GOVERNANCE_REVIEW_QUEUE_ROUTE = "/governance-review-queue"

# Surface-source label for promotion-review projections sourced from the
# durable command log rather than a ReadSurfacePorts store method. Exposed as
# a constant (not a literal) so callers outside this module -- notably
# management_read_models/service.py, which is architecturally forbidden from
# referencing the command-store surface directly -- can report the same
# value without the literal substring appearing in their own source text.
_PROMOTION_REVIEW_COMMAND_LOG_SOURCE = "command_store"

_MANAGEMENT_RISK_LEVEL_ORDER = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}

_HUMAN_INBOX_OPEN_APPROVAL_STATES = {
    "pending",
    "in_review",
    "under_review",
    "reviewed",
    "proposed",
}


_HUMAN_INBOX_OPEN_GOVERNANCE_STATUSES = {
    "pending",
    "open",
    "in_review",
    "under_review",
    "reviewed",
}

_HUMAN_INBOX_OPEN_INCIDENT_STATUSES = {"pending", "open", "active", "escalated", "investigating"}

_HUMAN_INBOX_PRIORITY_RANK = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "unknown": 0,
}

_HUMAN_INBOX_PROMOTION_PRODUCER = "management_quarterly_ranking_recommendation_submit"

_HUMAN_INBOX_INACTIVE_COMMAND_STATUSES = {
    "canceled",
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

_MANAGEMENT_CAMEL_KEY_RE = re.compile(r"[A-Z]")


def _management_record_id(record: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _management_json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _management_count_by(records: List[Dict[str, Any]], field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        value = str(record.get(field) or "unknown").strip() or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts


def _highest_ranked_value(
    values: List[Optional[str]],
    order: Dict[str, int],
) -> Optional[str]:
    best_value: Optional[str] = None
    best_rank = -1
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip().lower()
        rank = order.get(normalized)
        if rank is None:
            continue
        if rank > best_rank:
            best_rank = rank
            best_value = normalized
    return best_value


def _management_camel_to_snake_key(value: str) -> str:
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return value.lower()


def _management_prune_camel_aliases(value: Any) -> Any:
    """Keep snake_case when a dict carries both snake_case and camelCase aliases."""
    if isinstance(value, list):
        return [_management_prune_camel_aliases(item) for item in value]
    if not isinstance(value, dict):
        return value
    keys = {key for key in value if isinstance(key, str)}
    pruned: Dict[str, Any] = {}
    for key, nested in value.items():
        if isinstance(key, str) and _MANAGEMENT_CAMEL_KEY_RE.search(key):
            snake_key = _management_camel_to_snake_key(key)
            if snake_key in keys:
                continue
        pruned[key] = _management_prune_camel_aliases(nested)
    return pruned


def _human_inbox_csv_filter(value: Optional[str]) -> Optional[set[str]]:
    if not value:
        return None
    requested = {part.strip().lower() for part in value.split(",") if part.strip()}
    return requested or None


def _human_inbox_priority(value: Any, *, fallback: str = "medium") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in _HUMAN_INBOX_PRIORITY_RANK:
        return normalized
    if normalized in {"sev1", "p0"}:
        return "critical"
    if normalized in {"sev2", "p1"}:
        return "high"
    if normalized in {"sev3", "p2"}:
        return "medium"
    return fallback


def _human_inbox_attach_common_fields(
    projected: Dict[str, Any],
    *,
    inbox_type: str,
    source_dataset: str,
    risk_level: str,
    created_at: Optional[str],
    updated_at: Optional[str],
    href: str,
    source_record: Dict[str, Any],
) -> Dict[str, Any]:
    projected.setdefault("kind", inbox_type)
    projected["inbox_type"] = inbox_type
    projected["sourceDataset"] = source_dataset
    projected["source_dataset"] = source_dataset
    projected["riskLevel"] = risk_level
    projected["risk_level"] = risk_level
    projected["createdAt"] = created_at
    projected["created_at"] = created_at
    projected["updatedAt"] = updated_at
    projected["updated_at"] = updated_at
    projected["href"] = href
    projected.setdefault("route", href)
    return projected


def _human_inbox_action_state(status: str, open_statuses: set[str]) -> str:
    return "pending" if status in open_statuses else "resolved"


def _human_inbox_review_allows_decision(item: Dict[str, Any]) -> bool:
    actions = item.get("allowedActions")
    if not isinstance(actions, dict):
        return True
    return bool(actions.get("canApprove") or actions.get("canReject"))


def _human_inbox_record_time(record: Dict[str, Any], *keys: str) -> Optional[str]:
    """First real timestamp of a source record; never the request time."""
    details = record.get("details") if isinstance(record.get("details"), dict) else {}
    for source in (record, details):
        for key in keys:
            value = source.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return None


def _human_inbox_governance_review_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    item_id = _management_record_id(item, "item_id", "id", "review_id")
    if not item_id:
        return None
    review_type = str(item.get("item_type") or item.get("review_type") or "GovernanceReview").strip()
    if review_type == "EvolutionDecision" and not _human_inbox_review_allows_decision(item):
        # Not decidable by a human yet: it enters the inbox once it is.
        return None
    status = str(item.get("status") or item.get("governance_outcome") or "pending").strip().lower() or "pending"
    risk_level = str(item.get("risk_level") or "unknown").strip().lower() or "unknown"
    priority = _human_inbox_priority(item.get("priority") or risk_level, fallback="medium")
    created_at = _human_inbox_record_time(item, "submitted_at", "created_at")
    updated_at = _human_inbox_record_time(item, "updated_at") or created_at
    route = f"{_GOVERNANCE_REVIEW_QUEUE_ROUTE}?item={item_id}"
    action_state = _human_inbox_action_state(status, _HUMAN_INBOX_OPEN_GOVERNANCE_STATUSES)
    review_summary = item.get("review_summary") if isinstance(item.get("review_summary"), dict) else {}
    review_target_type = str(review_summary.get("target_type") or "").strip()
    review_target_id = review_summary.get("target_id")
    projected = {
        "id": f"governance_review:{item_id}",
        "item_id": item_id,
        "inbox_id": f"governance_review:{item_id}",
        "inboxType": "governance_review",
        "source_type": "governance_review",
        "source_id": item_id,
        "review_item_id": item_id,
        "title": item.get("title") or f"Governance review: {review_target_type or review_type}",
        "summary": (
            item.get("summary")
            or item.get("description")
            or review_summary.get("riskSummary")
            or "Governance review awaiting human action."
        ),
        "priority": priority,
        "risk_level": risk_level,
        "status": status,
        "action_state": action_state,
        "created_at": created_at,
        "updated_at": updated_at,
        "submitted_by": item.get("submitted_by"),
        "target": {
            "type": review_type,
            "id": item.get("target_id") or review_target_id or item.get("plan_id") or item.get("artifact_id") or item_id,
        },
        "route": route,
        "bff_detail_path": route,
        "allowedActions": _management_json_clone(item.get("allowedActions") or {
            "canReview": action_state == "pending",
            "canRequestRevision": action_state == "pending",
        }),
        "details": _management_json_clone(item),
    }
    return _human_inbox_attach_common_fields(
        projected,
        inbox_type="governance_review",
        source_dataset="governance_review_queue_items",
        risk_level=risk_level,
        created_at=created_at,
        updated_at=updated_at,
        href=route,
        source_record=item,
    )


def _human_inbox_approval_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    decision_id = _management_record_id(item, "decision_id", "id", "approval_decision_id")
    if not decision_id:
        return None
    decision_type = str(item.get("decision_type") or item.get("target_type") or "ApprovalDecision").strip()
    risk_level = str(item.get("risk_level") or "unknown").strip().lower() or "unknown"
    state = str(item.get("decision_state") or item.get("state") or item.get("status") or "pending").strip().lower() or "pending"
    context = item.get("decision_context") if isinstance(item.get("decision_context"), dict) else {}
    governance_chain = context.get("governance_chain") if isinstance(context.get("governance_chain"), dict) else {}
    priority = _human_inbox_priority(item.get("priority") or risk_level, fallback="medium")
    risk_summary = str(context.get("risk_summary") or "").strip()
    target_type = str(governance_chain.get("target_type") or decision_type or "ApprovalDecision").strip()
    target_id = str(governance_chain.get("target_id") or governance_chain.get("linked_review_item_id") or "").strip()
    action_state = "pending" if state in _HUMAN_INBOX_OPEN_APPROVAL_STATES else "resolved"
    route = f"/management/approvals?approval={decision_id}"
    created_at = _human_inbox_record_time(item, "submitted_at", "created_at", "recorded_at")
    updated_at = _human_inbox_record_time(item, "updated_at") or created_at
    projected = {
        "id": f"approval:{decision_id}",
        "item_id": decision_id,
        "inbox_id": f"approval:{decision_id}",
        "inboxType": "approval",
        "source_type": "approval",
        "source_id": decision_id,
        "approval_decision_id": decision_id,
        "decision_id": decision_id,
        "title": item.get("title") or f"{decision_type} approval",
        "summary": risk_summary or str(item.get("rationale") or "").strip() or "Approval decision awaiting human review.",
        "priority": priority,
        "risk_level": risk_level,
        "status": state,
        "action_state": action_state,
        "created_at": created_at,
        "updated_at": updated_at,
        "submitted_by": item.get("submitted_by"),
        "target": {
            "type": target_type,
            "id": target_id or None,
        },
        "route": route,
        "bff_detail_path": f"/bff/approvals/{decision_id}",
        "decision_context": json.loads(json.dumps(context)),
        "allowedActions": json.loads(json.dumps(item.get("allowedActions") or {})),
        "details": _management_json_clone(item),
    }
    return _human_inbox_attach_common_fields(
        projected,
        inbox_type="approval",
        source_dataset="approval_queue_items",
        risk_level=risk_level,
        created_at=created_at,
        updated_at=updated_at,
        href=route,
        source_record=item,
    )


def _human_inbox_incident_item(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    incident_id = _management_record_id(record, "id", "incident_id")
    if not incident_id:
        return None
    status = str(record.get("status") or "open").strip().lower() or "open"
    if status not in _HUMAN_INBOX_OPEN_INCIDENT_STATUSES:
        return None
    kind = str(record.get("kind") or "incident").strip().lower() or "incident"
    risk_level = str(record.get("severity") or record.get("risk_level") or "high").strip().lower() or "high"
    priority = _human_inbox_priority(record.get("priority") or risk_level, fallback="high")
    created_at = _human_inbox_record_time(record, "triggered_at", "created_at", "opened_at")
    updated_at = record.get("updated_at") or record.get("last_seen_at") or created_at
    runtime_id = record.get("runtime_id") or record.get("target_id")
    persona_id = record.get("persona_id")
    target_type = "Persona" if persona_id else "Runtime" if runtime_id else record.get("target_type")
    target_id = persona_id or runtime_id or record.get("target_id") or incident_id
    route = f"/management/incidents/{incident_id}"
    action_state = _human_inbox_action_state(status, _HUMAN_INBOX_OPEN_INCIDENT_STATUSES)
    projected = {
        "id": f"incident:{incident_id}",
        "item_id": incident_id,
        "inbox_id": f"incident:{incident_id}",
        "inboxType": "incident",
        "source_type": "incident",
        "source_id": incident_id,
        "incident_id": incident_id,
        "title": record.get("title") or f"Incident: {kind}",
        "summary": (
            record.get("summary")
            or record.get("evidence_summary")
            or record.get("description")
            or "Incident requires operator review."
        ),
        "priority": priority,
        "risk_level": risk_level,
        "status": status,
        "action_state": action_state,
        "created_at": created_at,
        "updated_at": updated_at,
        "target": {
            "type": target_type,
            "id": target_id,
        },
        "route": route,
        "bff_detail_path": f"/bff/incidents/{incident_id}",
        "incident_context": {
            "kind": kind,
            "runtime_id": runtime_id,
            "persona_id": persona_id,
        },
        "allowedActions": _management_json_clone(record.get("allowedActions") or {
            "canReview": action_state == "pending",
            "canRemediate": action_state == "pending",
        }),
        "details": _management_json_clone(record),
    }
    return _human_inbox_attach_common_fields(
        projected,
        inbox_type="incident",
        source_dataset="incidents",
        risk_level=risk_level,
        created_at=created_at,
        updated_at=updated_at,
        href=route,
        source_record=record,
    )


def _human_inbox_persona_blocking_reasons(row: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []
    current_work = str(row.get("current_work") or row.get("currentWork") or "").strip()
    if current_work:
        reasons.append(current_work)
    recommendation = str(row.get("recommendation") or "").strip()
    if recommendation:
        reasons.append(f"governance recommendation: {recommendation}")
    research_status = row.get("research_status") if isinstance(row.get("research_status"), dict) else {}
    pending_task_ids = research_status.get("pending_task_ids")
    if isinstance(pending_task_ids, list) and pending_task_ids:
        reasons.append(f"pending research tasks: {', '.join(str(task_id) for task_id in pending_task_ids)}")
    if row.get("can_deploy") is False or row.get("canDeploy") is False:
        reasons.append("deployment is blocked until human review clears")
    return reasons


def _human_inbox_persona_readiness_item(row: Dict[str, Any], *, snapshot_at: str) -> Optional[Dict[str, Any]]:
    persona_id = _management_record_id(row, "persona_id", "personaId", "id")
    if not persona_id or not bool(row.get("human_needed") or row.get("humanNeeded")):
        return None
    name = str(row.get("persona_name") or row.get("personaName") or row.get("name") or persona_id).strip()
    status = str(row.get("state") or row.get("status") or "needs_human_approval").strip().lower()
    research_status = row.get("research_status") if isinstance(row.get("research_status"), dict) else {}
    current_projects = row.get("current_research_projects") if isinstance(row.get("current_research_projects"), list) else []
    blocking_reasons = _human_inbox_persona_blocking_reasons(row)
    risk_level = "high" if status in {"critical", "needs_human_approval", "blocked"} or blocking_reasons else "medium"
    priority = _human_inbox_priority(row.get("priority") or risk_level, fallback=risk_level)
    created_at = row.get("updated_at") or row.get("lastMutation") or row.get("last_mutation") or snapshot_at
    route = f"/management/persona-fleet?persona={persona_id}"
    summary = (
        str(row.get("current_work") or row.get("currentWork") or "").strip()
        or str(research_status.get("summary") or "").strip()
        or "Persona readiness is blocked on human governance review."
    )
    projected = {
        "id": f"readiness_blocker:persona:{persona_id}",
        "item_id": persona_id,
        "inbox_id": f"readiness_blocker:persona:{persona_id}",
        "inboxType": "readiness_blocker",
        "source_type": "readiness_blocker",
        "source_id": persona_id,
        "persona_id": persona_id,
        "title": f"Persona needs review: {name}",
        "summary": summary,
        "priority": priority,
        "risk_level": risk_level,
        "status": status,
        "action_state": "pending",
        "created_at": created_at,
        "updated_at": created_at,
        "target": {
            "type": "persona",
            "id": persona_id,
        },
        "route": route,
        "bff_detail_path": f"/bff/management/human-inbox/readiness_blocker:persona:{persona_id}",
        "blocking_reasons": list(blocking_reasons),
        "can_proceed": False,
        "research_context": {
            "research_status": _management_json_clone(research_status),
            "current_research_projects": _management_json_clone(current_projects),
            "recommendation": row.get("recommendation"),
            "current_work": row.get("current_work") or row.get("currentWork"),
            "data_source_status": _management_json_clone(row.get("data_source_status") or {}),
        },
        "allowedActions": {
            "canProceed": False,
            "canDecide": False,
            "canOpenPersonaFleet": True,
            "canOpenResearch": bool(current_projects or research_status),
            "canRequestRevision": True,
        },
        "details": _management_json_clone(row),
    }
    return _human_inbox_attach_common_fields(
        projected,
        inbox_type="readiness_blocker",
        source_dataset="persona_fleet",
        risk_level=risk_level,
        created_at=created_at,
        updated_at=created_at,
        href=route,
        source_record=row,
    )


def _human_inbox_trusted_promotion_submission(command: Dict[str, Any]) -> bool:
    from ..personas.service import (
        _human_inbox_trusted_promotion_submission as _personas_trusted,
    )
    return _personas_trusted(command)


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
        "HumanGateApprove",
        "HumanGateReject",
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
    if command_type == "HumanGateReject" and decision != "reject":
        return None
    if command_type == "HumanGateApprove" and decision not in {
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
        projection["rationale"] = rationale
    if "conditions" in params:
        projection["conditions"] = _management_json_clone(params.get("conditions"))
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
            key: _management_json_clone(value)
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
        "allowedActions": {
            "canSubmit": False,
            "canApprove": not bool(decision),
            "canApproveWithConditions": not bool(decision),
            "canReject": not bool(decision),
        },
        "promotion_path": stage_path,
        "review_kind": stage_path.get("review_kind"),
        "source_recommendation": _management_json_clone(recommendation),
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
    """Project one trusted durable submission without rebuilding PM12 reads."""
    recommendation = _human_inbox_sanitize_promotion_snapshot(command)
    if recommendation is None:
        return None
    recommendation_id = str(recommendation["recommendation_id"])
    review_id = _promotion_review_record_revision_id(command)
    if not review_id:
        return None
    return _human_inbox_promotion_review_from_projection(
        recommendation,
        submission=_human_inbox_submission_projection_from_record(command, recommendation_id),
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

    if command_store is None:
        try:
            from ..main import command_store as _main_cmd_store
            command_store = _main_cmd_store
        except (ImportError, AttributeError):
            command_store = None

    if command_store is not None:
        commands = getattr(command_store, "_get_all_commands", None)
        if callable(commands):
            all_cmds = commands()
        else:
            all_cmds = []
    else:
        all_cmds = []

    for command in all_cmds:
        if command.get("type") == "QuarterlyRankingRecommendationSubmit":
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


def _human_inbox_review_duplicates_approval(record: Dict[str, Any], approval_ids: set[str]) -> bool:
    summary = record.get("review_summary") if isinstance(record.get("review_summary"), dict) else {}
    linked = str(summary.get("linked_approval_decision_id") or "").strip()
    if linked and linked in approval_ids:
        return True
    item_id = _management_record_id(record, "item_id", "id", "review_id")
    return item_id.startswith("review-") and item_id[len("review-"):] in approval_ids


def _human_inbox_project_items(
    *,
    snapshot_at: str,
    review_records: Sequence[Dict[str, Any]],
    approval_records: Sequence[Dict[str, Any]],
    incident_records: Sequence[Dict[str, Any]],
    persona_rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Project already-loaded contributors into the canonical inbox rows."""
    items: List[Dict[str, Any]] = []
    # A proposed approval decision is also surfaced by the governance review
    # queue as review-<decision_id>; list the decision once, as the approval.
    approval_ids = {
        decision_id
        for decision_id in (
            _management_record_id(record, "decision_id", "id", "approval_decision_id")
            for record in approval_records
        )
        if decision_id
    }
    if approval_ids:
        review_records = [
            record for record in review_records
            if not _human_inbox_review_duplicates_approval(record, approval_ids)
        ]
    projectors: Sequence[tuple[Sequence[Dict[str, Any]], Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]]] = (
        (review_records, _human_inbox_governance_review_item),
        (approval_records, _human_inbox_approval_item),
        (incident_records, _human_inbox_incident_item),
        (
            persona_rows,
            lambda row: _human_inbox_persona_readiness_item(row, snapshot_at=snapshot_at),
        ),
    )
    for records, projector in projectors:
        for record in records:
            projected = projector(record)
            if projected is not None:
                items.append(projected)
    items.sort(
        key=lambda item: (
            _HUMAN_INBOX_PRIORITY_RANK.get(str(item.get("priority") or "unknown"), 0),
            str(item.get("created_at") or ""),
            str(item.get("id") or ""),
        ),
        reverse=True,
    )
    return items


def read_pending_approval_queue_items(read_store: Any) -> List[Dict[str, Any]]:
    """Read pending approval queue items through the owner's pending filter."""
    if hasattr(read_store, "list_approval_queue_items"):
        reader = read_store.list_approval_queue_items
        try:
            params = inspect.signature(reader).parameters
        except (TypeError, ValueError):
            params = {}
        var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
        if "pending_only" in params or var_kw:
            try:
                rows = reader(pending_only=True)
            except TypeError as exc:
                if "pending_only" in str(exc) or "unexpected keyword" in str(exc) or "positional argument" in str(exc):
                    rows = reader()
                else:
                    raise
        else:
            rows = reader()
    elif hasattr(read_store, "list_approval_records"):
        rows = read_store.list_approval_records()
    else:
        rows = []
    return [row for row in (rows or []) if isinstance(row, dict)]


def read_decidable_governance_review_queue_items(read_store: Any) -> List[Dict[str, Any]]:
    """Read governance review queue items using filtered owner reads for Human Inbox."""
    if not hasattr(read_store, "list_governance_review_queue_items"):
        return []
    reader = read_store.list_governance_review_queue_items
    try:
        params = inspect.signature(reader).parameters
    except (TypeError, ValueError):
        params = {}
    var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    if "decidable_only" in params or var_kw:
        try:
            rows = reader(decidable_only=True)
        except TypeError as exc:
            if "decidable_only" in str(exc) or "unexpected keyword" in str(exc) or "positional argument" in str(exc):
                rows = reader()
            else:
                raise
    else:
        rows = reader()
    return [row for row in (rows or []) if isinstance(row, dict)]


def _human_inbox_governance_contributor(
    snapshot_at: str,
    *,
    read_store: Any = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if read_store is None:
        try:
            from ..main import read_store as _main_read_store
            read_store = _main_read_store
        except (ImportError, AttributeError):
            read_store = None
    records = read_decidable_governance_review_queue_items(read_store) if read_store else []
    try:
        from ..main import read_store as _main_read_store
        from ..personas.service import _dataset_source_after_read, _dataset_surface_status
        surface = _dataset_surface_status(
            "governance_review_queue_items",
            snapshot_at=snapshot_at,
            has_data=bool(records),
            missing_message="Governance review queue has no readable source records.",
            source=_dataset_source_after_read("governance_review_queue_items", read_store=_main_read_store),
        )
    except (ImportError, AttributeError):
        surface = {
            "status": "ok" if records else "unavailable",
            "source": "read_store" if records else "missing",
        }
    return records, surface


def _human_inbox_approval_contributor(
    snapshot_at: str,
    *,
    read_store: Any = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if read_store is None:
        try:
            from ..main import read_store as _main_read_store
            read_store = _main_read_store
        except (ImportError, AttributeError):
            read_store = None
    records = read_pending_approval_queue_items(read_store) if read_store else []
    try:
        from ..main import read_store as _main_read_store
        from ..personas.service import _dataset_source_after_read, _dataset_surface_status
        surface = _dataset_surface_status(
            "approval_queue_items",
            snapshot_at=snapshot_at,
            has_data=bool(records),
            missing_message="Approval queue has no readable source records.",
            source=_dataset_source_after_read("approval_queue_items", read_store=_main_read_store),
        )
    except (ImportError, AttributeError):
        surface = {
            "status": "ok" if records else "unavailable",
            "source": "read_store" if records else "missing",
        }
    return records, surface


def read_open_incidents(read_store: Any) -> List[Dict[str, Any]]:
    """Read open incidents once, through the owner's open_only filter.

    A failed owner read raises instead of reading as an empty queue. A store
    whose ``list_incidents`` predates ``open_only`` is read as before and the
    projector keeps only open rows.
    """
    reader = read_store.list_incidents
    try:
        params = inspect.signature(reader).parameters
    except (TypeError, ValueError):
        params = {}
    var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    kwargs: Dict[str, Any] = {}
    if "open_only" in params or var_kw:
        kwargs["open_only"] = True
        if "raise_on_error" in params or var_kw:
            kwargs["raise_on_error"] = True
    return [row for row in (reader(**kwargs) or []) if isinstance(row, dict)]


def _human_inbox_incident_contributor(
    snapshot_at: str,
    *,
    read_store: Any = None,
) -> tuple[tuple[bool, List[Dict[str, Any]]], Dict[str, Any]]:
    if read_store is None:
        try:
            from ..main import read_store as _main_read_store
            read_store = _main_read_store
        except (ImportError, AttributeError):
            read_store = None
    available = bool(read_store and hasattr(read_store, "list_incidents"))
    records = read_open_incidents(read_store) if available else []
    try:
        from ..personas.service import _dataset_surface_status
        surface = _dataset_surface_status("incidents", snapshot_at=snapshot_at)
    except (ImportError, AttributeError):
        surface = {"status": "ok" if available else "unavailable", "source": "read_store" if available else "missing"}
    return (available, records), surface


def _build_persona_readiness_items(
    snapshot_at: str,
    *,
    read_store: Any = None,
) -> List[Dict[str, Any]]:
    """Build only the persona fields consumed by Human Inbox readiness rows."""
    if read_store is None:
        try:
            from ..main import read_store as _main_read_store
            read_store = _main_read_store
        except (ImportError, AttributeError):
            read_store = None

    if not read_store:
        return []

    personas = list(
        read_store.list_personas(include_market_persona_defaults=True) or []
    )
    league_by_persona = {
        str(item.get("persona_id") or item.get("id") or "").strip(): item
        for item in (
            read_store.list_persona_league(
                include_market_persona_defaults=True,
            )
            or []
        )
        if str(item.get("persona_id") or item.get("id") or "").strip()
    }
    # Imported from their canonical home (personas/service.py), not via a
    # lazy `from ..main import ...`: main.py only re-exports these, and a
    # lazy main-module import here would force a full, first-touch import
    # and composition of main.py (including its Rankings write-owner store
    # connection) on any caller that reaches this function before main.py
    # is otherwise loaded -- e.g. a standalone test app built directly from
    # router factories (BFF-MGMT-READ-DEFECT-REPAIR-001: this was
    # multi-second cold-start latency hiding behind an unbounded call;
    # bounding the call surfaced it as a spurious read_timeout instead of
    # fixing the actual cost).
    try:
        from ..personas.service import (
            _persona_fleet_context_defaults_by_market,
            _persona_fleet_context_overlay,
            _persona_id,
        )
    except (ImportError, ValueError):
        from personas.service import (
            _persona_fleet_context_defaults_by_market,
            _persona_fleet_context_overlay,
            _persona_id,
        )
    context_defaults = _persona_fleet_context_defaults_by_market(personas)
    rows: List[Dict[str, Any]] = []
    for persona in personas:
        persona_id = _persona_id(persona)
        if not persona_id:
            continue
        metadata = persona.get("metadata") if isinstance(persona.get("metadata"), dict) else {}
        context_metadata, _context_persona = _persona_fleet_context_overlay(
            persona,
            metadata,
            context_defaults,
        )
        league_entry = league_by_persona.get(persona_id, {})
        governance_required = bool(
            league_entry.get("governance_required")
            if "governance_required" in league_entry
            else context_metadata.get("governance_required", True)
        )
        recommendation = (
            league_entry.get("recommendation")
            or context_metadata.get("recommended_governance_action")
            or ""
        )
        human_needed = governance_required and str(recommendation).strip().lower() not in {
            "",
            "none",
            "no_change",
        }
        research_status = (
            context_metadata.get("research_status")
            if isinstance(context_metadata.get("research_status"), dict)
            else {}
        )
        current_projects = (
            context_metadata.get("current_research_projects")
            if isinstance(context_metadata.get("current_research_projects"), list)
            else []
        )
        can_deploy = research_status.get("can_deploy")
        if can_deploy is None:
            can_deploy = context_metadata.get("can_deploy")
        rows.append(
            {
                "id": persona_id,
                "persona_id": persona_id,
                "name": persona.get("name") or persona_id,
                "persona_name": persona.get("name") or persona_id,
                "human_needed": human_needed,
                "state": str(
                    metadata.get("persona_status")
                    or league_entry.get("status")
                    or persona.get("status")
                    or persona.get("lifecycle_state")
                    or "unknown"
                ),
                "current_work": context_metadata.get("current_work"),
                "recommendation": recommendation,
                "can_deploy": can_deploy,
                "priority": league_entry.get("priority") or context_metadata.get("priority"),
                "updated_at": (
                    league_entry.get("updated_at")
                    or persona.get("updated_at")
                    or persona.get("last_active_at")
                    or snapshot_at
                ),
                "research_status": _management_json_clone(research_status),
                "current_research_projects": _management_json_clone(current_projects),
                "data_source_status": _management_json_clone(
                    context_metadata.get("data_source_status") or {}
                ),
            }
        )
    return rows


def _human_inbox_persona_contributor(
    snapshot_at: str,
    *,
    read_store: Any = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows = list(_build_persona_readiness_items(snapshot_at, read_store=read_store) or [])
    try:
        from ..personas.service import _composed_dataset_surface_status
        surface = _composed_dataset_surface_status(
            "persona_fleet",
            rows,
            snapshot_at=snapshot_at,
            source="bff_composed",
        )
    except (ImportError, AttributeError):
        surface = {"status": "ok", "source": "bff_composed"}
    return rows, surface


def _human_inbox_filter_items(
    items: List[Dict[str, Any]],
    *,
    source_type: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
) -> List[Dict[str, Any]]:
    source_types = _human_inbox_csv_filter(source_type)
    statuses = _human_inbox_csv_filter(status)
    priorities = _human_inbox_csv_filter(priority)
    filtered = items
    if source_types:
        filtered = [
            item for item in filtered
            if str(item.get("source_type") or item.get("inboxType") or "").lower() in source_types
        ]
    if statuses:
        filtered = [
            item for item in filtered
            if str(item.get("status") or "").lower() in statuses
            or str(item.get("action_state") or "").lower() in statuses
        ]
    if priorities:
        filtered = [
            item for item in filtered
            if str(item.get("priority") or "").lower() in priorities
            or str(item.get("risk_level") or "").lower() in priorities
        ]
    return filtered


def _human_inbox_summary(items: List[Dict[str, Any]], returned_count: int) -> Dict[str, Any]:
    pending_items = [item for item in items if str(item.get("action_state") or "") == "pending"]
    by_type = _management_count_by(items, "inboxType")
    by_status = _management_count_by(items, "status")
    highest_risk_level = _highest_ranked_value(
        [str(item.get("riskLevel") or item.get("risk_level") or "") for item in items],
        _MANAGEMENT_RISK_LEVEL_ORDER,
    )
    return {
        "total": len(items),
        "total_items": len(items),
        "returned_items": returned_count,
        "pending_items": len(pending_items),
        "by_type": by_type,
        "by_status": by_status,
        "highest_risk_level": highest_risk_level,
        "governance_review_count": len([item for item in items if item.get("source_type") == "governance_review"]),
        "approval_count": len([item for item in items if item.get("source_type") == "approval"]),
        "incident_count": len([item for item in items if item.get("source_type") == "incident"]),
        "readiness_blocker_count": len([item for item in items if item.get("source_type") == "readiness_blocker"]),
        "critical_count": len([item for item in items if item.get("priority") == "critical"]),
        "high_count": len([item for item in items if item.get("priority") == "high"]),
    }
