import json
from datetime import datetime, timezone
from typing import Any, Dict

_MAX_HEARTBEAT_AGE_SECONDS = 900
_MAX_FUTURE_SKEW_SECONDS = 60


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _controller_status(row: Dict[str, Any], *, now: datetime) -> tuple[str, str | None]:
    heartbeat = _as_utc(row.get("last_heartbeat_at"))
    lease_expires = _as_utc(row.get("lease_expires_at"))
    last_success = _as_utc(row.get("last_success_at"))
    last_failure = _as_utc(row.get("last_failure_at"))
    if heartbeat is None:
        return "unobserved", "controller heartbeat is missing"
    heartbeat_age = (now - heartbeat).total_seconds()
    if heartbeat_age > _MAX_HEARTBEAT_AGE_SECONDS:
        return "degraded", "controller heartbeat is stale"
    if heartbeat_age < -_MAX_FUTURE_SKEW_SECONDS:
        return "degraded", "controller heartbeat is in the future"
    if row.get("lease_expires_at") and lease_expires is None:
        return "degraded", "controller lease timestamp is invalid"
    if lease_expires is not None and not str(row.get("lease_token") or "").strip():
        return "degraded", "controller lease fencing token is missing"
    if last_failure is not None and (last_success is None or last_failure > last_success):
        return "unhealthy", row.get("last_failure_reason") or "latest controller event failed"
    if lease_expires is not None and lease_expires <= now:
        return "degraded", "controller lease expired"
    return "healthy", None


def _mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _desired_state_presence(row: Dict[str, Any]) -> Dict[str, Any]:
    observed = _mapping(row.get("desired_state"))
    checked_at = observed.get("checked_at")
    if observed and checked_at:
        present = bool(observed.get("present"))
        return {
            "status": "present" if present else "absent",
            "present": present,
            "authoritative": True,
            "source": observed.get("source"),
            "summary": observed.get("summary"),
            "checked_at": (
                checked_at.isoformat()
                if isinstance(checked_at, datetime)
                else checked_at
            ),
            "sources": list(observed.get("sources") or []),
            "query": row.get("desired_state_query"),
        }
    return {
        "status": (
            "configured_unobserved"
            if row.get("desired_state_query")
            else "missing"
        ),
        "present": None,
        "authoritative": False,
        "source": "controller_store" if row.get("desired_state_query") else "missing",
        "summary": row.get("desired_state_query"),
        "checked_at": None,
        "sources": [],
        "query": row.get("desired_state_query"),
    }


def _downstream_actual_state(row: Dict[str, Any]) -> Dict[str, Any]:
    observed = _mapping(row.get("downstream_actual_state"))
    checked_at = observed.get("checked_at")
    if observed and checked_at:
        return {
            "status": observed.get("status") or "unknown",
            "authoritative": True,
            "source": observed.get("source"),
            "summary": observed.get("summary"),
            "checked_at": (
                checked_at.isoformat()
                if isinstance(checked_at, datetime)
                else checked_at
            ),
            "sources": list(observed.get("sources") or []),
            "query": row.get("actual_state_query"),
        }
    return {
        "status": "unobserved",
        "authoritative": False,
        "source": "controller_store" if row.get("actual_state_query") else "missing",
        "summary": row.get("actual_state_query"),
        "checked_at": None,
        "sources": [],
        "query": row.get("actual_state_query"),
    }


def project_controller_record_to_bff(
    row: Dict[str, Any], *, now: datetime | None = None
) -> Dict[str, Any]:
    """Project a durable row without manufacturing liveness or evidence.

    The BFF performs the final catalog/controller admission.  This projector
    only reports what the row actually contains: missing refs stay missing and
    expired leases or later failures are never labelled healthy.
    """
    # Handle datetime serialization safely
    def to_iso(dt: Any) -> Any:
        if isinstance(dt, datetime):
            return dt.isoformat()
        return dt

    evidence_refs = []
    if row.get("evidence_refs"):
        if isinstance(row["evidence_refs"], list):
            evidence_refs = row["evidence_refs"]
        elif isinstance(row["evidence_refs"], str):
            try:
                evidence_refs = json.loads(row["evidence_refs"])
            except (TypeError, json.JSONDecodeError):
                pass

    payload = {}
    if row.get("payload"):
        if isinstance(row["payload"], dict):
            payload = row["payload"]
        elif isinstance(row["payload"], str):
            try:
                payload = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError):
                pass

    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    controller_status, degraded_reason = _controller_status(row, now=observed_at)

    last_heartbeat_at = to_iso(row.get("last_heartbeat_at"))
    last_tick_at = to_iso(row.get("last_tick_at"))

    controller_health = {
        "status": controller_status,
        "controller_name": row.get("controller_name"),
        "controller_id": row.get("controller_id"),
        "last_heartbeat_at": last_heartbeat_at,
        "last_tick_at": last_tick_at,
        "liveness_metric": "heartbeat",
        "degraded_reason": degraded_reason,
        "lease_fenced": bool(str(row.get("lease_token") or "").strip()),
    }

    last_success = None
    if row.get("last_success_at"):
        last_success = {
            "at": to_iso(row["last_success_at"]),
            "status": "success",
            "reason": None,
            "summary": payload.get("last_success_summary") or "Execution succeeded",
            "evidence_refs": evidence_refs,
        }

    last_failure = None
    if row.get("last_failure_at"):
        last_failure = {
            "at": to_iso(row["last_failure_at"]),
            "status": "failed",
            "reason": row.get("last_failure_reason"),
            "summary": row.get("last_failure_reason") or "Execution failed",
            "evidence_refs": evidence_refs,
        }

    # Format expected by services/control-plane/bff/loop_inventory.py
    projected = {
        "loop_id": row.get("loop_id"),
        "id": row.get("loop_id"),
        "tenant_id": row.get("tenant_id"),
        "environment": row.get("environment"),
        "truth_level": row.get("truth_level"),
        "truth_status": "present" if evidence_refs else "missing_evidence",
        "truth_note": f"Durable controller state via Postgres store. Instance: {row.get('controller_id')}",
        "evidence_basis": "controller_runtime",
        "evidence_bases": ["controller_runtime"],
        "refs": evidence_refs,
        "evidence_refs": evidence_refs,
        "controller_health": controller_health,
        "controller": controller_health,
        "last_heartbeat_at": last_heartbeat_at,
        "last_success": last_success,
        "last_failure": last_failure,
        "desired_state_presence": _desired_state_presence(row),
        "downstream_actual_state": _downstream_actual_state(row),
        "evidence_packet": {
            "truth_level": row.get("truth_level"),
            "highest_truth_level": row.get("truth_level"),
            "refs": evidence_refs,
            "artifacts": evidence_refs,
            "captured_at": last_tick_at or last_heartbeat_at,
        },
        "lease_expires_at": to_iso(row.get("lease_expires_at")),
        "lease_fenced": bool(str(row.get("lease_token") or "").strip()),
        "desired_state_query": row.get("desired_state_query"),
        "actual_state_query": row.get("actual_state_query"),
        "backlog": row.get("backlog"),
        "lag": row.get("lag"),
        "dlq_count": row.get("dlq_count"),
        "deployment_sha": row.get("deployment_sha"),
        "payload": payload,
    }
    return projected
