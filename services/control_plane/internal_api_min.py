"""Minimal pure-Python API implementation for unit testing (no Flask).
Provides functions that mirror the endpoints' behavior and return (status_code, payload).
"""
from datetime import datetime
import re


def _unauthorized(payload_message="Unauthorized"):
    return 401, {"error": {"code": "401", "message": payload_message}}


def _bad_request(code, message):
    return 400, {"error": {"code": code, "message": message}}


def _ok(payload, code=200):
    return code, payload


def _check_bearer(auth_header: str):
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    token = auth_header.split(None, 1)[1]
    return bool(token)


def _check_mfa(mfa_token: str):
    if not mfa_token:
        return True
    return bool(re.fullmatch(r"\d{6}", mfa_token))


def health():
    return _ok({"status": "ok"}, 200)


def approve_deployment(plan_id: str, headers: dict, body: dict):
    if not _check_bearer(headers.get("Authorization", "")):
        return _unauthorized("missing Bearer token")
    mfa = headers.get("X-MFA-Token", "")
    if mfa and not _check_mfa(mfa):
        return _bad_request("MFA_VALIDATION_FAILED", "MFA token invalid")
    decision = (body or {}).get("approval_decision", "approve")
    verification_timestamp = (body or {}).get("verification_timestamp") or datetime.utcnow().isoformat() + "Z"
    approval_id = f"ad-{plan_id}-{int(datetime.utcnow().timestamp())}"
    payload = {
        "approval_decision_id": approval_id,
        "target_plan_id": plan_id,
        "state_after": "approved" if decision == "approve" else "rejected",
        "audit_id": f"audit-{approval_id}",
        "command_id": f"cmd-{approval_id}",
        "verification_timestamp": verification_timestamp,
    }
    return _ok(payload, 202)


def pause_runtime(binding_id: str, headers: dict, body: dict):
    if not _check_bearer(headers.get("Authorization", "")):
        return _unauthorized("missing Bearer token")
    mfa = headers.get("X-MFA-Token", "")
    if mfa and not _check_mfa(mfa):
        return _bad_request("MFA_VALIDATION_FAILED", "MFA token invalid")
    duration = (body or {}).get("duration_seconds") or (body or {}).get("duration") or 3600
    reason = (body or {}).get("reason", "")
    command_id = f"cmd-runtime-pause-{binding_id}-{int(datetime.utcnow().timestamp())}"
    payload = {
        "command_id": command_id,
        "runtime_binding_id": binding_id,
        "pause_expires_at": datetime.utcnow().isoformat() + "Z",
        "status": "submitted",
        "duration_seconds": duration,
        "reason": reason,
    }
    return _ok(payload, 202)


def execute_rollback(body: dict, headers: dict):
    if not _check_bearer(headers.get("Authorization", "")):
        return _unauthorized("missing Bearer token")
    mfa = headers.get("X-MFA-Token", "")
    if mfa and not _check_mfa(mfa):
        return _bad_request("MFA_VALIDATION_FAILED", "MFA token invalid")
    target_type = (body or {}).get("rollback_target_type") or (body or {}).get("target_type") or "deployment"
    target_id = (body or {}).get("target_id") or (body or {}).get("target") or "unknown"
    rollback_id = f"rb-{target_id}-{int(datetime.utcnow().timestamp())}"
    payload = {
        "rollback_id": rollback_id,
        "command_id": f"cmd-{rollback_id}",
        "status": "submitted",
        "tracking_url": f"/api/internal/v1/commands/cmd-{rollback_id}",
    }
    return _ok(payload, 202)


def kill_switch(body: dict, headers: dict):
    if not _check_bearer(headers.get("Authorization", "")):
        return _unauthorized("missing Bearer token")
    mfa = headers.get("X-MFA-Token", "")
    if mfa and not _check_mfa(mfa):
        return _bad_request("MFA_VALIDATION_FAILED", "MFA token invalid")
    action = (body or {}).get("action") or "activate"
    scope = (body or {}).get("scope") or "all"
    order_id = f"ks-{int(datetime.utcnow().timestamp())}"
    payload = {
        "kill_switch_order_id": order_id,
        "command_id": f"cmd-{order_id}",
        "action": action,
        "scope": scope,
        "status": "submitted",
    }
    return _ok(payload, 202)


def get_command(command_id: str, headers: dict):
    if not _check_bearer(headers.get("Authorization", "")):
        return _unauthorized("missing Bearer token")
    payload = {
        "command_id": command_id,
        "type": "placeholder",
        "status": "executed",
        "submitted_at": datetime.utcnow().isoformat() + "Z",
        "result": {"ok": True},
        "error": None,
    }
    return _ok(payload, 200)
