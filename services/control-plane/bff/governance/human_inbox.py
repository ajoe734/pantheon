"""Human Inbox domain projection, aggregation, and recommendation decision helpers.

Encapsulates human inbox projection from contributors (governance reviews,
approvals, interventions, sentinel findings, persona readiness, promotion reviews),
filtering, summary calculation, and recommendation decision tracking decoupled
from main.py globals.
"""
from __future__ import annotations

import json
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

_HUMAN_INBOX_OPEN_INTERVENTION_STATUSES = {"pending", "escalated"}

_HUMAN_INBOX_OPEN_GOVERNANCE_STATUSES = {
    "pending",
    "open",
    "in_review",
    "under_review",
    "reviewed",
}

_HUMAN_INBOX_OPEN_SENTINEL_STATUSES = {"pending", "open", "active", "escalated"}

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


def human_inbox_surface_timeout_seconds() -> float:
    raw = os.getenv("PANTHEON_BFF_HUMAN_INBOX_TIMEOUT_SECONDS", "3.0").strip()
    try:
        val = float(raw)
        return max(0.1, val)
    except (TypeError, ValueError):
        return 3.0


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


def _human_inbox_governance_review_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    item_id = _management_record_id(item, "item_id", "id", "review_id")
    if not item_id:
        return None
    review_type = str(item.get("item_type") or item.get("review_type") or "GovernanceReview").strip()
    status = str(item.get("status") or item.get("governance_outcome") or "pending").strip().lower() or "pending"
    risk_level = str(item.get("risk_level") or "unknown").strip().lower() or "unknown"
    priority = _human_inbox_priority(item.get("priority") or risk_level, fallback="medium")
    created_at = item.get("submitted_at") or item.get("created_at")
    updated_at = item.get("updated_at") or created_at
    route = f"{_GOVERNANCE_REVIEW_QUEUE_ROUTE}?item={item_id}"
    action_state = _human_inbox_action_state(status, _HUMAN_INBOX_OPEN_GOVERNANCE_STATUSES)
    projected = {
        "id": f"governance_review:{item_id}",
        "inbox_id": f"governance_review:{item_id}",
        "inboxType": "governance_review",
        "source_type": "governance_review",
        "source_id": item_id,
        "review_item_id": item_id,
        "title": item.get("title") or f"Governance review: {review_type}",
        "summary": item.get("summary") or item.get("description") or "Governance review awaiting human action.",
        "priority": priority,
        "risk_level": risk_level,
        "status": status,
        "action_state": action_state,
        "created_at": created_at,
        "updated_at": updated_at,
        "submitted_by": item.get("submitted_by"),
        "target": {
            "type": review_type,
            "id": item.get("target_id") or item.get("plan_id") or item.get("artifact_id") or item_id,
        },
        "route": route,
        "bff_detail_path": route,
        "allowedActions": _management_json_clone(item.get("allowedActions") or {
            "canReview": action_state == "pending",
            "canRequestRevision": action_state == "pending",
        }),
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
    state = str(item.get("decision_state") or item.get("state") or "pending").strip().lower() or "pending"
    context = item.get("decision_context") if isinstance(item.get("decision_context"), dict) else {}
    governance_chain = context.get("governance_chain") if isinstance(context.get("governance_chain"), dict) else {}
    priority = _human_inbox_priority(item.get("priority") or risk_level, fallback="medium")
    risk_summary = str(context.get("risk_summary") or "").strip()
    target_type = str(governance_chain.get("target_type") or decision_type or "ApprovalDecision").strip()
    target_id = str(governance_chain.get("target_id") or governance_chain.get("linked_review_item_id") or "").strip()
    action_state = "pending" if state in _HUMAN_INBOX_OPEN_APPROVAL_STATES else "resolved"
    route = f"/management/approvals?approval={decision_id}"
    created_at = item.get("submitted_at")
    updated_at = item.get("updated_at") or created_at
    projected = {
        "id": f"approval:{decision_id}",
        "inbox_id": f"approval:{decision_id}",
        "inboxType": "approval",
        "source_type": "approval",
        "source_id": decision_id,
        "approval_decision_id": decision_id,
        "title": item.get("title") or f"{decision_type} approval",
        "summary": risk_summary or "Approval decision awaiting human review.",
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


def _human_inbox_intervention_item(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    intervention_id = _management_record_id(record, "intervention_id", "id")
    if not intervention_id:
        return None
    status = str(record.get("status") or "pending").strip().lower() or "pending"
    kind = str(record.get("kind") or "hiq_sentinel").strip().lower() or "hiq_sentinel"
    priority = _human_inbox_priority(
        record.get("priority") or record.get("severity") or record.get("risk_level"),
        fallback="critical" if status == "pending" and kind == "hiq_sentinel" else "high",
    )
    action_state = "pending" if status in _HUMAN_INBOX_OPEN_INTERVENTION_STATUSES else "resolved"
    raw_allowed_actions = record.get("allowedActions") if isinstance(record.get("allowedActions"), dict) else {}
    allowed_actions = {
        "canClaim": status == "pending",
        "canRelease": status == "claimed",
        "canEscalate": status == "pending",
        "canDecide": status in _HUMAN_INBOX_OPEN_INTERVENTION_STATUSES,
        "canRemediate": status in _HUMAN_INBOX_OPEN_INTERVENTION_STATUSES,
        **raw_allowed_actions,
    }
    route = f"/management/interventions?intervention={intervention_id}"
    created_at = record.get("triggered_at") or record.get("created_at")
    updated_at = record.get("remediated_at") or record.get("updated_at") or created_at
    projected = {
        "id": f"intervention:{intervention_id}",
        "inbox_id": f"intervention:{intervention_id}",
        "inboxType": "intervention",
        "source_type": "intervention",
        "source_id": intervention_id,
        "intervention_id": intervention_id,
        "title": record.get("title") or f"{kind.replace('_', ' ').title()} intervention",
        "summary": record.get("description") or "Human intervention is required before the loop can continue.",
        "priority": priority,
        "risk_level": str(record.get("risk_level") or priority).strip().lower(),
        "status": status,
        "action_state": action_state,
        "created_at": created_at,
        "updated_at": updated_at,
        "triggered_by": record.get("triggered_by"),
        "target": {
            "type": record.get("target_type"),
            "id": record.get("target_id"),
        },
        "route": route,
        "bff_detail_path": f"/bff/v5/interventions/{intervention_id}",
        "remediation_context": {
            "kind": kind,
            "remediation_action": record.get("remediation_action"),
            "two_man_signature_id": record.get("two_man_signature_id"),
            "correlation_id": record.get("correlation_id"),
        },
        "allowedActions": json.loads(json.dumps(allowed_actions)),
    }
    return _human_inbox_attach_common_fields(
        projected,
        inbox_type="intervention",
        source_dataset="v5_interventions",
        risk_level=str(record.get("risk_level") or priority).strip().lower(),
        created_at=created_at,
        updated_at=updated_at,
        href=route,
        source_record=record,
    )


def _human_inbox_sentinel_item(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    finding_id = _management_record_id(record, "id", "finding_id", "incident_id")
    if not finding_id:
        return None
    status = str(record.get("status") or "open").strip().lower() or "open"
    if status not in _HUMAN_INBOX_OPEN_SENTINEL_STATUSES:
        return None
    kind = str(record.get("kind") or "sentinel_finding").strip().lower() or "sentinel_finding"
    risk_level = str(record.get("severity") or record.get("risk_level") or "high").strip().lower() or "high"
    priority = _human_inbox_priority(record.get("priority") or risk_level, fallback="high")
    created_at = record.get("triggered_at") or record.get("created_at") or record.get("opened_at")
    updated_at = record.get("updated_at") or record.get("last_seen_at") or created_at
    runtime_id = record.get("runtime_id") or record.get("target_id")
    persona_id = record.get("persona_id")
    target_type = "Persona" if persona_id else "Runtime" if runtime_id else record.get("target_type")
    target_id = persona_id or runtime_id or record.get("target_id") or finding_id
    route = f"/management/sentinel?finding={finding_id}"
    action_state = _human_inbox_action_state(status, _HUMAN_INBOX_OPEN_SENTINEL_STATUSES)
    projected = {
        "id": f"sentinel_finding:{finding_id}",
        "inbox_id": f"sentinel_finding:{finding_id}",
        "inboxType": "sentinel_finding",
        "source_type": "sentinel_finding",
        "source_id": finding_id,
        "finding_id": finding_id,
        "title": record.get("title") or f"Sentinel finding: {kind}",
        "summary": record.get("summary") or record.get("description") or "Sentinel finding requires operator review.",
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
        "bff_detail_path": f"/bff/v5/sentinel/findings/{finding_id}",
        "sentinel_context": {
            "kind": kind,
            "runtime_id": runtime_id,
            "persona_id": persona_id,
            "derived_from_incident_id": record.get("derived_from_incident_id"),
        },
        "allowedActions": _management_json_clone(record.get("allowedActions") or {
            "canReview": action_state == "pending",
            "canRemediate": action_state == "pending",
        }),
    }
    return _human_inbox_attach_common_fields(
        projected,
        inbox_type="sentinel_finding",
        source_dataset="sentinel_findings",
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
        "allowed_decisions": sorted(_PROMOTION_REVIEW_DECISIONS),
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
    submission = review.get("submission") if isinstance(review.get("submission"), dict) else {}
    created_at = submission.get("submitted_at") or review.get("created_at")
    updated_at = (review.get("decision") or {}).get("decided_at") if isinstance(review.get("decision"), dict) else None
    updated_at = updated_at or created_at
    inbox_id = _promotion_review_target_id(review_id)
    route = f"/management/human-inbox/{quote(inbox_id, safe='')}"
    action_state = "pending" if status == "pending" else "resolved"
    stage_path = review.get("promotion_path") if isinstance(review.get("promotion_path"), dict) else {}
    projected = {
        "id": inbox_id,
        "inbox_id": inbox_id,
        "item_id": inbox_id,
        "human_inbox_id": inbox_id,
        "category": "promotion_review",
        "action_kind": "promotion_review",
        "inboxType": "promotion_review",
        "source_type": "promotion_review",
        "source_id": review_id,
        "review_id": review_id,
        "promotion_review_id": review_id,
        "recommendation_id": review.get("recommendation_id"),
        "persona_id": review.get("persona_id"),
        "quarter": review.get("quarter"),
        "ranking_snapshot_id": review.get("ranking_snapshot_id"),
        "data": review,
        "title": f"Persona governance review: {review.get('name') or review.get('persona_id')}",
        "summary": review.get("rationale") or "Persona ranking recommendation requires Human Gate approval.",
        "priority": priority,
        "risk_level": risk_level,
        "status": status,
        "decision_status": decision_status,
        "action_state": action_state,
        "created_at": created_at,
        "updated_at": updated_at,
        "submitted_by": submission.get("submitted_by"),
        "target": {
            "type": "persona",
            "id": review.get("persona_id"),
        },
        "route": route,
        "bff_detail_path": f"/bff/management/promotion-reviews/{quote(review_id, safe='')}",
        "decisionHref": f"/bff/management/promotion-reviews/{quote(review_id, safe='')}/decisions",
        "detailHref": route,
        "promotion_review": _management_json_clone(review),
        "promotion_context": {
            "from_stage": stage_path.get("from_stage"),
            "target_stage": stage_path.get("target_stage"),
            "review_kind": review.get("review_kind") or stage_path.get("review_kind"),
            "action_id": review.get("action_id"),
            "ranking_snapshot_id": review.get("ranking_snapshot_id"),
            "live_capital_mutation": False,
        },
        "allowedActions": _management_json_clone(review.get("allowedActions") or {
            "canApprove": action_state == "pending",
            "canApproveWithConditions": action_state == "pending",
            "canReject": action_state == "pending",
        }),
        "requires_human_gate_decision": True,
        "live_capital_mutation": False,
    }
    return _human_inbox_attach_common_fields(
        projected,
        inbox_type="promotion_review",
        source_dataset="promotion_reviews",
        risk_level=risk_level,
        created_at=created_at,
        updated_at=updated_at,
        href=route,
        source_record=review,
    )


def _human_inbox_project_items(
    *,
    snapshot_at: str,
    review_records: Sequence[Dict[str, Any]],
    approval_records: Sequence[Dict[str, Any]],
    intervention_records: Sequence[Dict[str, Any]],
    sentinel_records: Sequence[Dict[str, Any]],
    persona_rows: Sequence[Dict[str, Any]],
    promotion_review_records: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Project already-loaded contributors into the canonical inbox rows."""
    items: List[Dict[str, Any]] = []
    projectors: Sequence[tuple[Sequence[Dict[str, Any]], Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]]] = (
        (review_records, _human_inbox_governance_review_item),
        (approval_records, _human_inbox_approval_item),
        (intervention_records, _human_inbox_intervention_item),
        (sentinel_records, _human_inbox_sentinel_item),
        (
            persona_rows,
            lambda row: _human_inbox_persona_readiness_item(row, snapshot_at=snapshot_at),
        ),
        (promotion_review_records, _human_inbox_promotion_review_item),
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
    records = list(read_store.list_governance_review_queue_items() or []) if read_store else []
    try:
        from ..main import _dataset_source_after_read, _dataset_surface_status
        surface = _dataset_surface_status(
            "governance_review_queue_items",
            snapshot_at=snapshot_at,
            has_data=bool(records),
            missing_message="Governance review queue has no readable source records.",
            source=_dataset_source_after_read("governance_review_queue_items"),
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
    records = list(read_store.list_approval_queue_items() or []) if read_store else []
    try:
        from ..main import _dataset_source_after_read, _dataset_surface_status
        surface = _dataset_surface_status(
            "approval_queue_items",
            snapshot_at=snapshot_at,
            has_data=bool(records),
            missing_message="Approval queue has no readable source records.",
            source=_dataset_source_after_read("approval_queue_items"),
        )
    except (ImportError, AttributeError):
        surface = {
            "status": "ok" if records else "unavailable",
            "source": "read_store" if records else "missing",
        }
    return records, surface


def _human_inbox_intervention_contributor(
    snapshot_at: str,
    *,
    v5_records: Optional[List[Dict[str, Any]]] = None,
    v5_store: Optional[List[Dict[str, Any]]] = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if v5_records is None:
        try:
            from ..main import _v5_intervention_records
            records = list(_v5_intervention_records())
        except (ImportError, AttributeError):
            records = []
    else:
        records = list(v5_records)

    try:
        from ..main import _dataset_source_after_read, _dataset_surface_status
        surface = _dataset_surface_status(
            "v5_interventions",
            snapshot_at=snapshot_at,
            has_data=bool(records),
            missing_message="V5 interventions have no readable source records.",
            source=_dataset_source_after_read("v5_interventions"),
        )
    except (ImportError, AttributeError):
        surface = {"status": "ok" if records else "unavailable", "source": "read_store" if records else "missing"}

    if v5_store is None:
        try:
            from ..main import _V5_INTERVENTIONS_STORE
            store = _V5_INTERVENTIONS_STORE
        except (ImportError, AttributeError):
            store = []
    else:
        store = v5_store

    local_ids = {
        str(record.get("intervention_id") or record.get("id") or "")
        for record in store
        if isinstance(record, dict)
    }
    has_local_record = any(
        str(record.get("intervention_id") or record.get("id") or "") in local_ids
        for record in records
    )
    if has_local_record and surface.get("source") == "missing":
        try:
            from ..main import _surface_status
            surface = {**_surface_status(), "source": "bff_local_registry"}
        except (ImportError, AttributeError):
            surface = {"status": "ok", "source": "bff_local_registry"}
    return records, surface


def _human_inbox_sentinel_contributor(
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
    if read_store and hasattr(read_store, "list_sentinel_findings"):
        available, raw_records = read_store.list_sentinel_findings()
    else:
        available, raw_records = False, []
    records = list(raw_records or [])
    try:
        from ..main import _dataset_source_after_read, _dataset_surface_status
        incidents_source = _dataset_source_after_read("incidents")
        if incidents_source != "missing":
            surface = _dataset_surface_status("incidents", snapshot_at=snapshot_at)
        else:
            surface = _dataset_surface_status(
                "sentinel_findings",
                snapshot_at=snapshot_at,
                source=_dataset_source_after_read("sentinel_findings") if available else "missing",
            )
    except (ImportError, AttributeError):
        surface = {"status": "ok" if available else "unavailable", "source": "read_store" if available else "missing"}
    return (bool(available), records), surface


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
    from ..main import (
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
        from ..main import _composed_dataset_surface_status
        surface = _composed_dataset_surface_status(
            "persona_fleet",
            rows,
            snapshot_at=snapshot_at,
            source="bff_composed",
        )
    except (ImportError, AttributeError):
        surface = {"status": "ok", "source": "bff_composed"}
    return rows, surface


def _human_inbox_promotion_contributor(
    identity: Any,
    snapshot_at: str,
    *,
    command_store: Any = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    records = _submitted_promotion_review_records(identity, snapshot_at=snapshot_at, command_store=command_store)
    try:
        from ..main import _surface_status
        surface = dict(_surface_status())
    except (ImportError, AttributeError):
        surface = {"status": "ok"}
    surface["source"] = "command_store"
    return records, surface


def _human_inbox_all_items(
    snapshot_at: Optional[str] = None,
    *,
    identity: Optional[OperatorIdentity] = None,
    source_types: Optional[set[str]] = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not snapshot_at:
        try:
            from ..main import utc_now
            snapshot_at = utc_now()
        except (ImportError, AttributeError):
            from datetime import datetime, timezone
            snapshot_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    include_all = not source_types
    review_records: List[Dict[str, Any]] = []
    approval_records: List[Dict[str, Any]] = []
    intervention_records: List[Dict[str, Any]] = []
    sentinel_available = False
    sentinel_records: List[Dict[str, Any]] = []
    persona_rows: List[Dict[str, Any]] = []
    promotion_review_records: List[Dict[str, Any]] = []
    surfaces: Dict[str, Dict[str, Any]] = {}
    if include_all or "governance_review" in source_types:
        review_records, surfaces["governance_review_queue"] = _human_inbox_governance_contributor(snapshot_at)
    if include_all or "approval" in source_types:
        approval_records, surfaces["approval_queue"] = _human_inbox_approval_contributor(snapshot_at)
    if include_all or "intervention" in source_types:
        intervention_records, surfaces["v5_interventions"] = _human_inbox_intervention_contributor(snapshot_at)
    if include_all or "sentinel_finding" in source_types:
        sentinel_result, surfaces["sentinel_findings"] = _human_inbox_sentinel_contributor(snapshot_at)
        sentinel_available, sentinel_records = sentinel_result
    if include_all or "readiness_blocker" in source_types:
        persona_rows, surfaces["persona_readiness"] = _human_inbox_persona_contributor(snapshot_at)
    if identity is not None and (include_all or "promotion_review" in source_types):
        promotion_review_records, surfaces["promotion_reviews"] = _human_inbox_promotion_contributor(
            identity,
            snapshot_at,
        )
    items = _human_inbox_project_items(
        snapshot_at=snapshot_at,
        review_records=review_records,
        approval_records=approval_records,
        intervention_records=intervention_records,
        sentinel_records=sentinel_records,
        persona_rows=persona_rows,
        promotion_review_records=promotion_review_records,
    )
    return items, {
        "governance_review_records": review_records,
        "approval_records": approval_records,
        "intervention_records": intervention_records,
        "sentinel_available": sentinel_available,
        "sentinel_records": sentinel_records,
        "persona_rows": persona_rows,
        "promotion_review_records": promotion_review_records,
        "surfaces": surfaces,
    }


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
        "intervention_count": len([item for item in items if item.get("source_type") == "intervention"]),
        "sentinel_finding_count": len([item for item in items if item.get("source_type") == "sentinel_finding"]),
        "readiness_blocker_count": len([item for item in items if item.get("source_type") == "readiness_blocker"]),
        "critical_count": len([item for item in items if item.get("priority") == "critical"]),
        "high_count": len([item for item in items if item.get("priority") == "high"]),
    }


def _human_inbox_loaded_surface(
    *,
    snapshot_at: str,
    source: str,
    available: bool = True,
    has_data: Optional[bool] = None,
    empty_is_unavailable: bool = False,
    missing_message: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        from ..main import _surface_status
        surface = dict(_surface_status())
    except (ImportError, AttributeError):
        surface = {"status": "ok"}
    surface["source"] = source
    if not available or (empty_is_unavailable and has_data is False):
        surface["status"] = "unavailable"
        surface["source"] = "missing" if not available else source
        if missing_message:
            surface["message"] = missing_message
        surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at},
        )
    return surface


def _human_inbox_surfaces(
    *,
    snapshot_at: str,
    governance_review_records: List[Dict[str, Any]],
    approval_records: List[Dict[str, Any]],
    intervention_records: List[Dict[str, Any]],
    sentinel_available: bool,
    sentinel_records: List[Dict[str, Any]],
    persona_rows: List[Dict[str, Any]],
    promotion_review_records: List[Dict[str, Any]],
    source_types: Optional[set[str]] = None,
    surface_failures: Optional[Dict[str, Dict[str, Any]]] = None,
    loaded_surfaces: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    include_all = not source_types
    failures = surface_failures or {}
    provenance = loaded_surfaces or {}
    contributor_surfaces: Dict[str, Dict[str, Any]] = {}

    if include_all or "governance_review" in source_types:
        contributor_surfaces["governance_review_queue"] = failures.get(
            "governance_review_queue"
        ) or provenance.get("governance_review_queue") or _human_inbox_loaded_surface(
            snapshot_at=snapshot_at,
            source="read_store",
            has_data=bool(governance_review_records),
            empty_is_unavailable=True,
            missing_message="Governance review queue has no readable source records.",
        )
    if include_all or "approval" in source_types:
        contributor_surfaces["approval_queue"] = failures.get(
            "approval_queue"
        ) or provenance.get("approval_queue") or _human_inbox_loaded_surface(
            snapshot_at=snapshot_at,
            source="read_store",
            has_data=bool(approval_records),
            empty_is_unavailable=True,
            missing_message="Approval queue has no readable source records.",
        )
    if include_all or "intervention" in source_types:
        try:
            from ..main import _V5_INTERVENTIONS_STORE
            v5_store = _V5_INTERVENTIONS_STORE
        except (ImportError, AttributeError):
            v5_store = []
        local_intervention_ids = {
            str(record.get("intervention_id") or record.get("id") or "")
            for record in v5_store
            if isinstance(record, dict)
        }
        has_local_intervention = any(
            str(record.get("intervention_id") or record.get("id") or "") in local_intervention_ids
            for record in intervention_records
        )
        contributor_surfaces["v5_interventions"] = failures.get(
            "v5_interventions"
        ) or provenance.get("v5_interventions") or _human_inbox_loaded_surface(
            snapshot_at=snapshot_at,
            source="bff_local_registry" if has_local_intervention else "read_store",
            has_data=bool(intervention_records),
            empty_is_unavailable=True,
            missing_message="V5 interventions have no readable source records.",
        )
    if include_all or "sentinel_finding" in source_types:
        contributor_surfaces["sentinel_findings"] = failures.get(
            "sentinel_findings"
        ) or provenance.get("sentinel_findings") or _human_inbox_loaded_surface(
            snapshot_at=snapshot_at,
            source="read_store" if sentinel_available else "missing",
            available=sentinel_available,
            has_data=bool(sentinel_records),
            missing_message="Sentinel findings have no readable source records.",
        )
    if include_all or "readiness_blocker" in source_types:
        contributor_surfaces["persona_readiness"] = failures.get(
            "persona_readiness"
        ) or provenance.get("persona_readiness") or _human_inbox_loaded_surface(
            snapshot_at=snapshot_at,
            source="bff_composed",
            has_data=bool(persona_rows),
        )
    if include_all or "promotion_review" in source_types:
        contributor_surfaces["promotion_reviews"] = failures.get(
            "promotion_reviews"
        ) or provenance.get("promotion_reviews") or _human_inbox_loaded_surface(
            snapshot_at=snapshot_at,
            source="command_store",
            has_data=bool(promotion_review_records),
        )

    try:
        from ..main import _aggregate_group_surface
        aggregate_surface = _aggregate_group_surface(
            "human_inbox",
            list(contributor_surfaces.values()),
            snapshot_at=snapshot_at,
            unavailable_message="Human inbox aggregate unavailable.",
            degraded_message="Human inbox aggregate is available, but one or more contributing surfaces are degraded.",
        )
    except (ImportError, AttributeError):
        aggregate_surface = {"status": "ok"}
    return {
        "human_inbox": aggregate_surface,
        **contributor_surfaces,
    }


def _human_inbox_payload_from_loaded(
    snapshot_at: str,
    *,
    items: List[Dict[str, Any]],
    sources: Dict[str, Any],
    source_types: Optional[set[str]],
    source_type: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: Optional[int] = 20,
    surface_failures: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    filtered = _human_inbox_filter_items(
        items,
        source_type=source_type,
        status=status,
        priority=priority,
    )
    total = len(filtered)
    from ..main import _page_slice, _snapshot_meta
    if page_size is None:
        page_items = filtered
        next_page_token = None
        returned_page_size = len(page_items)
    else:
        page_items, next_page_token = _page_slice(filtered, page_token, page_size)
        returned_page_size = page_size
    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = _human_inbox_surfaces(
        snapshot_at=snapshot_at,
        governance_review_records=sources["governance_review_records"],
        approval_records=sources["approval_records"],
        intervention_records=sources["intervention_records"],
        sentinel_available=bool(sources["sentinel_available"]),
        sentinel_records=sources["sentinel_records"],
        persona_rows=sources["persona_rows"],
        promotion_review_records=sources["promotion_review_records"],
        source_types=source_types,
        surface_failures=surface_failures,
        loaded_surfaces=sources.get("surfaces"),
    )
    if surface_failures:
        meta["partial"] = True
        meta["degradation"] = {
            "reason": "one_or_more_human_inbox_contributors_incomplete",
            "contributors": sorted(surface_failures),
        }
    summary = _human_inbox_summary(filtered, len(page_items))
    canonical_page_items = _management_prune_camel_aliases(page_items)
    return {
        "data": {
            "id": "management-human-inbox",
            "items": canonical_page_items,
            "summary": summary,
        },
        "page_info": {
            "next_page_token": next_page_token,
            "total": total,
            "page_size": returned_page_size,
        },
        "meta": meta,
    }


def _human_inbox_payload(
    snapshot_at: str,
    *,
    identity: Optional[OperatorIdentity] = None,
    source_type: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: Optional[int] = 20,
) -> Dict[str, Any]:
    source_types = _human_inbox_csv_filter(source_type)
    items, sources = _human_inbox_all_items(snapshot_at, identity=identity, source_types=source_types)
    return _human_inbox_payload_from_loaded(
        snapshot_at,
        items=items,
        sources=sources,
        source_types=source_types,
        source_type=source_type,
        status=status,
        priority=priority,
        page_token=page_token,
        page_size=page_size,
    )
