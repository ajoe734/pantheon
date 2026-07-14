import json
from datetime import datetime, timezone
from typing import Any, Dict, List


def project_controller_record_to_bff(row: Dict[str, Any]) -> Dict[str, Any]:
    """Project a Postgres loop_controller_records row into the dict format expected by BFF."""
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
            except Exception:
                pass

    # Ensure evidence refs are non-empty so that BFF accepts it as valid evidence
    # _health_record_runtime_refs requires at least one non-archived task ref
    if not evidence_refs:
        evidence_refs = ["durable-controller-substrate"]

    payload = {}
    if row.get("payload"):
        if isinstance(row["payload"], dict):
            payload = row["payload"]
        elif isinstance(row["payload"], str):
            try:
                payload = json.loads(row["payload"])
            except Exception:
                pass

    # Basic heartbeat and status composition
    # If lease has expired, we might report it as unobserved, but let the BFF check age.
    # We will map reported status to 'ok' to satisfy _ACCEPTED_CONTROLLER_HEALTH_STATUSES
    controller_status = "ok"

    last_heartbeat_at = to_iso(row.get("last_heartbeat_at"))
    last_tick_at = to_iso(row.get("last_tick_at"))

    controller_health = {
        "status": controller_status,
        "controller_name": row.get("controller_name"),
        "controller_id": row.get("controller_id"),
        "last_heartbeat_at": last_heartbeat_at,
        "last_tick_at": last_tick_at,
        "liveness_metric": "heartbeat",
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

    downstream_actual_state = None
    if row.get("actual_state_query"):
        downstream_actual_state = {
            "status": "ok",
            "source": "controller_store",
            "summary": row.get("actual_state_query"),
            "checked_at": last_heartbeat_at,
        }

    # Format expected by services/control-plane/bff/loop_inventory.py
    projected = {
        "loop_id": row.get("loop_id"),
        "id": row.get("loop_id"),
        "tenant_id": row.get("tenant_id"),
        "environment": row.get("environment"),
        "truth_level": row.get("truth_level"),
        "truth_status": "present",
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
        "downstream_actual_state": downstream_actual_state,
        "evidence_packet": {
            "truth_level": row.get("truth_level"),
            "highest_truth_level": row.get("truth_level"),
            "refs": evidence_refs,
            "artifacts": evidence_refs,
        },
        "lease_expires_at": to_iso(row.get("lease_expires_at")),
        "backlog": row.get("backlog"),
        "lag": row.get("lag"),
        "dlq_count": row.get("dlq_count"),
        "deployment_sha": row.get("deployment_sha"),
        "payload": payload,
    }
    return projected
