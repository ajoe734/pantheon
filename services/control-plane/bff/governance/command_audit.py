"""Governance projector for CommandStore records.

Projects persistent CommandStore records into normalized governance audit events
with deterministic ordering and role/tenant visibility filtering.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional

from ..models import CommandStatus, utc_now


def _command_audit_action_from_record(record: Dict[str, Any]) -> Dict[str, Any]:
    foundation = record.get("foundation") if isinstance(record.get("foundation"), dict) else {}
    audit_action = foundation.get("audit_action") if isinstance(foundation.get("audit_action"), dict) else None
    if audit_action:
        return dict(audit_action)
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    audit_foundation = audit.get("foundation") if isinstance(audit.get("foundation"), dict) else {}
    audit_action = (
        audit_foundation.get("audit_action")
        if isinstance(audit_foundation.get("audit_action"), dict)
        else None
    )
    return dict(audit_action or {})


def _audit_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def project_command_record_audit_event(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    command_id = str(record.get("command_id") or "").strip()
    if not command_id:
        return None
    target = record.get("target") if isinstance(record.get("target"), dict) else {}
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    foundation = record.get("foundation") if isinstance(record.get("foundation"), dict) else {}
    audit_action = _command_audit_action_from_record(record)
    idempotency_record = (
        foundation.get("idempotency_record")
        if isinstance(foundation.get("idempotency_record"), dict)
        else {}
    )
    audit_actor_ref = audit_action.get("actor_ref") if isinstance(audit_action.get("actor_ref"), dict) else {}
    metadata = audit_action.get("metadata") if isinstance(audit_action.get("metadata"), dict) else {}
    trace_context = foundation.get("trace_context") if isinstance(foundation.get("trace_context"), dict) else {}
    action_type = str(record.get("type") or metadata.get("command") or "").strip()
    target_type = str(target.get("type") or "").strip()
    target_id = str(target.get("id") or "").strip()
    timestamp = str(
        audit.get("timestamp")
        or audit_action.get("timestamp")
        or record.get("submitted_at")
        or utc_now()
    )
    reason = str(audit.get("reason") or audit_action.get("reason") or action_type or "operator command")
    event = {
        "entry_id": str(audit_action.get("action_id") or f"audit-{command_id}"),
        "actor": str(
            audit.get("operator_id")
            or audit.get("actor")
            or audit_actor_ref.get("actor_id")
            or "operator"
        ),
        "action_type": action_type,
        "target_type": target_type,
        "target_id": target_id,
        "timestamp": timestamp,
        "outcome": "accepted" if record.get("status") == CommandStatus.SUBMITTED.value else record.get("status"),
        "audit_context": {
            "reason": reason,
            "command_id": command_id,
            "receipt_id": command_id,
            "idempotency_key": (
                idempotency_record.get("idempotency_key")
                or metadata.get("idempotency_key")
                or audit.get("idempotency_key")
            ),
            "action_id": audit.get("action_id"),
            "foundation_action_type": audit_action.get("action_type"),
        },
        "evidence_refs": audit.get("evidence_refs") if isinstance(audit.get("evidence_refs"), list) else [],
        "command_ref": command_id,
        "trace_id": audit_action.get("trace_id") or trace_context.get("trace_id"),
        "correlation_id": (
            audit_action.get("correlation_id")
            or trace_context.get("correlation_id")
        ),
        "payload_checksum": audit_action.get("payload_checksum"),
        "audit_action": audit_action or None,
        "metadata": {
            "source": "command_store",
            "route": metadata.get("route"),
            "source_route": metadata.get("source_route"),
            "live_capital_side_effects": audit.get("live_capital_side_effects", False),
        },
    }
    return json.loads(json.dumps(event))


def audit_event_matches(
    event: Dict[str, Any],
    *,
    actor: Optional[str] = None,
    action_types: Optional[List[str]] = None,
    target_type: Optional[str] = None,
    from_ts: Optional[datetime] = None,
    to_ts: Optional[datetime] = None,
) -> bool:
    if actor and event.get("actor") != actor:
        return False
    if action_types:
        allowed = {value for value in action_types if value}
        if event.get("action_type") not in allowed:
            return False
    if target_type and event.get("target_type") != target_type:
        return False
    event_dt = _audit_datetime(event.get("timestamp"))
    if from_ts is not None and (event_dt is None or event_dt < from_ts):
        return False
    if to_ts is not None and (event_dt is None or event_dt > to_ts):
        return False
    return True


def _record_tenant_id(record: Dict[str, Any]) -> Optional[str]:
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    for key in ("tenant_id", "tenant"):
        value = str(audit.get(key) or "").strip()
        if value:
            return value

    foundation = record.get("foundation") if isinstance(record.get("foundation"), dict) else {}
    trace = foundation.get("trace_context") if isinstance(foundation.get("trace_context"), dict) else {}
    tenant_ref = trace.get("tenant_ref") if isinstance(trace.get("tenant_ref"), dict) else {}
    value = str(tenant_ref.get("tenant_id") or trace.get("tenant_id") or "").strip()
    return value or None


def list_projected_governance_audit_events(
    command_store: Any,
    *,
    actor: Optional[str] = None,
    action_types: Optional[List[str]] = None,
    target_type: Optional[str] = None,
    from_ts: Optional[datetime] = None,
    to_ts: Optional[datetime] = None,
    role: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Project CommandStore records into governance audit events with visibility filtering."""
    if command_store is None:
        return []
    records = (
        command_store._get_all_commands()
        if hasattr(command_store, "_get_all_commands")
        else (command_store() if callable(command_store) else [])
    )
    events: List[Dict[str, Any]] = []
    clean_tenant_id = str(tenant_id or "").strip()

    for record in records:
        if clean_tenant_id:
            cmd_tenant = _record_tenant_id(record)
            if cmd_tenant and cmd_tenant != clean_tenant_id:
                continue

        event = project_command_record_audit_event(record)
        if not event:
            continue
        if not audit_event_matches(
            event,
            actor=actor,
            action_types=action_types,
            target_type=target_type,
            from_ts=from_ts,
            to_ts=to_ts,
        ):
            continue
        events.append(event)

    events.sort(key=lambda event: str(event.get("timestamp") or ""), reverse=True)
    return events
