"""Minimal Protected Internal API scaffold for APP-002

Implements endpoints per APP-002 Secondary Control Path spec. Authentication and MFA
are implemented as lightweight stubs suitable for integration and unit testing.
"""
from flask import Flask, request, jsonify
from datetime import datetime
import re

app = Flask(__name__)


def require_bearer_token(required: bool = True):
    def decorator(func):
        def wrapper(*args, **kwargs):
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                if required:
                    return jsonify({"error": {"code": "401", "message": "Unauthorized: missing Bearer token"}}), 401
                else:
                    return func(*args, **kwargs)
            token = auth.split(None, 1)[1]
            # NOTE: In real implementation validate JWT signature and claims.
            if not token:
                return jsonify({"error": {"code": "401", "message": "Unauthorized: empty token"}}), 401
            request._validated_token = token
            return func(*args, **kwargs)

        wrapper.__name__ = func.__name__
        return wrapper

    return decorator


def require_mfa_if_present(func):
    def wrapper(*args, **kwargs):
        # For critical endpoints, X-MFA-Token header must be a 6-digit OTP
        mfa = request.headers.get("X-MFA-Token", "")
        if mfa:
            if not re.fullmatch(r"\d{6}", mfa):
                return jsonify({"error": {"code": "MFA_VALIDATION_FAILED", "message": "MFA token invalid"}}), 400
            request._mfa_token = mfa
        return func(*args, **kwargs)

    wrapper.__name__ = func.__name__
    return wrapper


@app.route("/api/internal/v1/deployments/<plan_id>/approve", methods=["POST"])
@require_bearer_token()
@require_mfa_if_present
def approve_deployment(plan_id):
    body = request.get_json() or {}
    decision = body.get("approval_decision", "approve")
    verification_timestamp = body.get("verification_timestamp") or datetime.utcnow().isoformat() + "Z"
    # Create a placeholder approval decision id
    approval_id = f"ad-{plan_id}-{int(datetime.utcnow().timestamp())}"
    return (
        jsonify(
            {
                "approval_decision_id": approval_id,
                "target_plan_id": plan_id,
                "state_after": "approved" if decision == "approve" else "rejected",
                "audit_id": f"audit-{approval_id}",
                "command_id": f"cmd-{approval_id}",
                "verification_timestamp": verification_timestamp,
            }
        ),
        202,
    )


@app.route("/api/internal/v1/runtimes/<binding_id>/pause", methods=["POST"])
@require_bearer_token()
@require_mfa_if_present
def pause_runtime(binding_id):
    body = request.get_json() or {}
    duration = body.get("duration_seconds") or body.get("duration") or 3600
    reason = body.get("reason", "")
    command_id = f"cmd-runtime-pause-{binding_id}-{int(datetime.utcnow().timestamp())}"
    return (
        jsonify(
            {
                "command_id": command_id,
                "runtime_binding_id": binding_id,
                "pause_expires_at": datetime.utcnow().isoformat() + "Z",
                "status": "submitted",
                "duration_seconds": duration,
                "reason": reason,
            }
        ),
        202,
    )


@app.route("/api/internal/v1/rollbacks/execute", methods=["POST"])
@require_bearer_token()
@require_mfa_if_present
def execute_rollback():
    body = request.get_json() or {}
    target_type = body.get("rollback_target_type") or body.get("target_type") or "deployment"
    target_id = body.get("target_id") or body.get("target") or "unknown"
    rollback_to = body.get("rollback_to_version") or body.get("rollback_to") or "previous"
    rollback_id = f"rb-{target_id}-{int(datetime.utcnow().timestamp())}"
    return (
        jsonify(
            {
                "rollback_id": rollback_id,
                "command_id": f"cmd-{rollback_id}",
                "status": "submitted",
                "tracking_url": f"/api/internal/v1/commands/cmd-{rollback_id}",
            }
        ),
        202,
    )


@app.route("/api/internal/v1/kill-switch", methods=["POST"])
@require_bearer_token()
@require_mfa_if_present
def kill_switch():
    body = request.get_json() or {}
    action = body.get("action") or "activate"
    scope = body.get("scope") or "all"
    scope_id = body.get("scope_id")
    order_id = f"ks-{int(datetime.utcnow().timestamp())}"
    return (
        jsonify(
            {
                "kill_switch_order_id": order_id,
                "command_id": f"cmd-{order_id}",
                "action": action,
                "scope": scope,
                "status": "submitted",
            }
        ),
        202,
    )


@app.route("/api/internal/v1/commands/<command_id>", methods=["GET"])
@require_bearer_token()
def get_command(command_id):
    # Return a placeholder executed command status
    return (
        jsonify(
            {
                "command_id": command_id,
                "type": "placeholder",
                "status": "executed",
                "submitted_at": datetime.utcnow().isoformat() + "Z",
                "result": {"ok": True},
                "error": None,
            }
        ),
        200,
    )


# Simple smoke route
@app.route("/__health__", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200
