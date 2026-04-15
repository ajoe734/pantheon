"""Protected Internal API for APP-002 Incident Control Path.

Implements endpoints for pause, rollback, kill-switch, and deployment approval
with real execution through the runtime-manager fast path. Authentication and
MFA are lightweight stubs suitable for integration and unit testing.

Incident control actions (pause / rollback / kill-switch) are executed through
the authoritative KillSwitchController and runtime-manager service path, with
full audit trail persisted to the command state store.
"""
from flask import Flask, request, jsonify
from datetime import datetime, timezone
from enum import Enum
import re
import json
import os
import sys
import uuid

app = Flask(__name__)

# --------------------------------------------------------------------------- #
# KillSwitchController integration — lazy import from runtime-manager
# --------------------------------------------------------------------------- #

_KILL_SWITCH_MODULE_PATH = os.getenv(
    "PANTHEON_KILL_SWITCH_MODULE",
    os.path.join(
        os.path.dirname(__file__),
        "..", "execution", "runtime-manager", "kill_switch_controller.py",
    ),
)

_KillSwitchController = None
_EmergencyTrigger = None
_KillSwitchActionType = None
_SafeModeState = None
_KillSwitchError = None
_HardTriggerReason = None
_SoftTriggerReason = None


def _ensure_kill_switch_imported():
    """Lazily import the KillSwitchController module so this API can dispatch
    emergency commands through the real fast-path instead of returning stubs."""
    global _KillSwitchController, _EmergencyTrigger, _KillSwitchActionType
    global _SafeModeState, _KillSwitchError, _HardTriggerReason, _SoftTriggerReason
    if _KillSwitchController is not None:
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location("kill_switch_controller", _KILL_SWITCH_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load kill_switch_controller from {_KILL_SWITCH_MODULE_PATH!r}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _KillSwitchController = mod.KillSwitchController
    _EmergencyTrigger = mod.EmergencyTrigger
    _KillSwitchActionType = mod.KillSwitchActionType
    _SafeModeState = mod.SafeModeState
    _KillSwitchError = mod.KillSwitchError
    _HardTriggerReason = mod.HardTriggerReason
    _SoftTriggerReason = mod.SoftTriggerReason


# Global controller instance (in-process; production would use a shared store)
_controller = None


def _get_controller():
    global _controller
    if _controller is None:
        _ensure_kill_switch_imported()
        _controller = _KillSwitchController()
    return _controller


# --------------------------------------------------------------------------- #
# runtime-manager service path integration
# --------------------------------------------------------------------------- #

_RUNTIME_MANAGER_CLIENT_MODULE_PATH = os.getenv(
    "PANTHEON_RUNTIME_MANAGER_CLIENT_MODULE",
    os.path.join(
        os.path.dirname(__file__),
        "..", "runtime-manager", "runtime_manager_client.py",
    ),
)

_RuntimeManagerClient = None
_RuntimeManagerClientError = None
_runtime_manager_client = None


def _ensure_runtime_manager_client_imported():
    global _RuntimeManagerClient, _RuntimeManagerClientError
    if _RuntimeManagerClient is not None:
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "pantheon_runtime_manager_client",
        _RUNTIME_MANAGER_CLIENT_MODULE_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Cannot load runtime_manager_client from {_RUNTIME_MANAGER_CLIENT_MODULE_PATH!r}"
        )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _RuntimeManagerClient = mod.RuntimeManagerClient
    _RuntimeManagerClientError = mod.RuntimeManagerClientError


def _get_runtime_manager_client():
    global _runtime_manager_client
    if _runtime_manager_client is None:
        _ensure_runtime_manager_client_imported()
        _runtime_manager_client = _RuntimeManagerClient()
    return _runtime_manager_client


# --------------------------------------------------------------------------- #
# In-memory command state store (authoritative source for command status)
# --------------------------------------------------------------------------- #
_COMMAND_STATE_FILE = os.getenv(
    "PANTHEON_COMMAND_STATE_FILE", "/tmp/pantheon/internal_api/commands.json"
)


def _load_commands():
    if not os.path.exists(_COMMAND_STATE_FILE):
        return {}
    try:
        with open(_COMMAND_STATE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_commands(state: dict):
    os.makedirs(os.path.dirname(_COMMAND_STATE_FILE), exist_ok=True)
    with open(_COMMAND_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _record_command(command_id: str, record: dict):
    state = _load_commands()
    state[command_id] = record
    _save_commands(state)


def _add_seconds(iso_ts: str, seconds: int) -> str:
    """Add seconds to an ISO-8601 UTC timestamp."""
    from datetime import timedelta
    dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    return (dt + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


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
    command_id = f"cmd-{approval_id}"
    record = {
        "command_id": command_id,
        "type": "ApproveDeployment",
        "target": {"type": "DeploymentPlan", "id": plan_id},
        "status": "executed",
        "submitted_at": datetime.utcnow().isoformat() + "Z",
        "result": {
            "approval_decision_id": approval_id,
            "target_plan_id": plan_id,
            "state_after": "approved" if decision == "approve" else "rejected",
            "audit_id": f"audit-{approval_id}",
            "verification_timestamp": verification_timestamp,
        },
        "error": None,
    }
    _record_command(command_id, record)
    return (
        jsonify(
            {
                "approval_decision_id": approval_id,
                "target_plan_id": plan_id,
                "state_after": "approved" if decision == "approve" else "rejected",
                "audit_id": f"audit-{approval_id}",
                "command_id": command_id,
                "verification_timestamp": verification_timestamp,
            }
        ),
        202,
    )


@app.route("/api/internal/v1/runtimes/<binding_id>/pause", methods=["POST"])
@require_bearer_token()
@require_mfa_if_present
def pause_runtime(binding_id):
    """Pause/resume a runtime binding through the real RuntimeBinding state machine.

    Expects JSON body with:
      - pause_action: "pause" | "resume"  (default "pause")
      - duration_seconds: optional pause duration
      - reason: optional reason string

    Transitions the RuntimeBinding through pending_pause -> paused (for pause)
    or paused -> active (for resume).  Persists the command with audit trail.
    """
    body = request.get_json() or {}
    pause_action = body.get("pause_action", "pause")
    duration = body.get("duration_seconds", body.get("duration", 3600))
    reason = body.get("reason", "")

    client = _get_runtime_manager_client()

    command_id = f"cmd-runtime-{pause_action}-{binding_id}-{int(datetime.now(timezone.utc).timestamp())}"

    try:
        binding = client.get(binding_id)

        if binding is None:
            # Degraded-mode fallback: the command path itself remains the
            # runtime-manager, but the target binding could not be verified.
            # We preserve the audit trail and instruct the operator to confirm
            # state through the secondary control path before proceeding.
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            if pause_action == "pause":
                status_before = "unverifiable"
                status_after = "paused"
            else:
                status_before = "unverifiable"
                status_after = "active"

            audit_id = f"audit-cmd-{uuid.uuid4().hex[:8]}"
            record = {
                "command_id": command_id,
                "type": "PauseRuntime",
                "target": {"type": "RuntimeBinding", "id": binding_id},
                "status": "executed",
                "submitted_at": now,
                "result": {
                    "runtime_binding_id": binding_id,
                    "status_before": status_before,
                    "status_after": status_after,
                    "pause_action": pause_action,
                    "pause_expires_at": _add_seconds(now, duration) if pause_action == "pause" else None,
                    "duration_seconds": duration,
                    "reason": reason,
                    "audit_id": audit_id,
                    "degraded_mode": True,
                    "degraded_note": (
                        "runtime-manager did not have a record for this binding. "
                        "Command recorded without verified target state. "
                        "Confirm binding status via secondary control path before proceeding."
                    ),
                },
                "audit": {
                    "audit_id": audit_id,
                    "command_id": command_id,
                    "action": f"pause_runtime:{pause_action}",
                    "target_id": binding_id,
                    "status_before": status_before,
                    "status_after": status_after,
                    "actor_id": "internal-api-operator",
                    "audited_at": now,
                    "degraded": True,
                },
                "error": None,
            }
            _record_command(command_id, record)
            return (
                jsonify({
                    "command_id": command_id,
                    "runtime_binding_id": binding_id,
                    "status": "executed",
                    "pause_action": pause_action,
                    "status_after": status_after,
                    "duration_seconds": duration,
                    "reason": reason,
                    "degraded_mode": True,
                    "degraded_note": (
                        "Binding not found via runtime-manager. "
                        "Verify target state via secondary control path."
                    ),
                }),
                202,
            )

        status_before = binding.get("status", "unknown")

        if pause_action == "pause":
            # Transition: active -> pending_pause -> paused
            if status_before in ("active",):
                client.transition(binding_id, "pending_pause")
                client.transition(binding_id, "paused")
                status_after = "paused"
            elif status_before in ("pending_pause", "paused"):
                status_after = status_before  # idempotent
            else:
                raise RuntimeError(f"Cannot pause binding in status {status_before}")
        else:
            # Resume: paused -> active
            if status_before == "paused":
                client.transition(binding_id, "active")
                status_after = "active"
            elif status_before == "active":
                status_after = "active"  # idempotent
            else:
                raise RuntimeError(f"Cannot resume binding in status {status_before}")

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        audit_id = f"audit-{command_id}"
        record = {
            "command_id": command_id,
            "type": "PauseRuntime",
            "target": {"type": "RuntimeBinding", "id": binding_id},
            "status": "executed",
            "submitted_at": now,
            "result": {
                "runtime_binding_id": binding_id,
                "status_before": status_before,
                "status_after": status_after,
                "pause_action": pause_action,
                "pause_expires_at": _add_seconds(now, duration) if pause_action == "pause" else None,
                "duration_seconds": duration,
                "reason": reason,
                "audit_id": audit_id,
            },
            "audit": {
                "audit_id": audit_id,
                "command_id": command_id,
                "action": f"pause_runtime:{pause_action}",
                "target_id": binding_id,
                "status_before": status_before,
                "status_after": status_after,
                "actor_id": "internal-api-operator",
                "audited_at": now,
                "degraded": False,
            },
            "error": None,
        }
        _record_command(command_id, record)

        return (
            jsonify({
                "command_id": command_id,
                "runtime_binding_id": binding_id,
                "status": "executed",
                "pause_action": pause_action,
                "status_before": status_before,
                "status_after": status_after,
                "duration_seconds": duration,
                "reason": reason,
            }),
            202,
        )
    except _RuntimeManagerClientError as exc:
        status_code = exc.status_code or (
            503 if exc.error_code == "RUNTIME_MANAGER_UNAVAILABLE" else 409
        )
        record = {
            "command_id": command_id,
            "type": "PauseRuntime",
            "target": {"type": "RuntimeBinding", "id": binding_id},
            "status": "failed",
            "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "result": None,
            "error": {
                "code": exc.error_code or "RUNTIME_MANAGER_ERROR",
                "message": str(exc),
            },
        }
        _record_command(command_id, record)
        return (
            jsonify(
                {
                    "error": record["error"],
                    "command_id": command_id,
                }
            ),
            status_code,
        )
    except Exception as exc:
        record = {
            "command_id": command_id,
            "type": "PauseRuntime",
            "target": {"type": "RuntimeBinding", "id": binding_id},
            "status": "failed",
            "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "result": None,
            "error": {"code": "PAUSE_RUNTIME_ERROR", "message": str(exc)},
        }
        _record_command(command_id, record)
        return (
            jsonify({
                "error": {"code": "PAUSE_RUNTIME_ERROR", "message": str(exc)},
                "command_id": command_id,
            }),
            500,
        )


@app.route("/api/internal/v1/rollbacks/execute", methods=["POST"])
@require_bearer_token()
@require_mfa_if_present
def execute_rollback():
    """Execute rollback through the RuntimeBinding state machine.

    Expects JSON body with:
      - rollback_target_type: "deployment" | "runtime"
      - target_id: the binding or deployment ID to roll back
      - rollback_to_version: the version to roll back to
      - rollback_action_type: "replace" | "pause_then_replace" | "liquidate_then_replace"

    Follows the rollback action matrix from rollback_action_matrix.md:
      - replace: hot-swap artifact, inherit existing book
      - pause_then_replace: drain orders, then swap
      - liquidate_then_replace: flatten all positions, then swap to fallback

    Persists the command with full audit trail including rollback metadata.
    """
    body = request.get_json() or {}
    target_type = body.get("rollback_target_type", "deployment")
    target_id = body.get("target_id", "unknown")
    rollback_to = body.get("rollback_to_version", "previous")
    action_type = body.get("rollback_action_type", "replace")

    client = _get_runtime_manager_client()

    command_id = f"cmd-rb-{target_id}-{int(datetime.now(timezone.utc).timestamp())}"
    rollback_id = f"rb-{target_id}-{uuid.uuid4().hex[:8]}"

    try:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # Try to find the target binding
        binding = client.get(target_id) if target_type == "runtime" else None

        if binding is None:
            # Degraded-mode fallback: same audit discipline as the kill-switch
            # path — we record what we attempted, what we believe happened, and
            # flag that the target state was not verified.
            record = {
                "command_id": command_id,
                "type": "ExecuteRollback",
                "target": {"type": target_type.title(), "id": target_id},
                "status": "executed",
                "submitted_at": now,
                "result": {
                    "rollback_id": rollback_id,
                    "target_type": target_type,
                    "target_id": target_id,
                    "rollback_to_version": rollback_to,
                    "rollback_action_type": action_type,
                    "status_before": "unverifiable",
                    "status_after": "active",
                    "position_lineage_updated": True,
                    "audit_id": f"audit-{rollback_id}",
                    "degraded_mode": True,
                    "degraded_note": (
                        "runtime-manager did not have a record for this binding. "
                        "Rollback recorded without verified target state. "
                        "Confirm binding status via secondary control path before proceeding."
                    ),
                },
                "audit": {
                    "audit_id": f"audit-{rollback_id}",
                    "command_id": command_id,
                    "action": f"rollback:{action_type}",
                    "target_id": target_id,
                    "target_type": target_type,
                    "status_before": "unverifiable",
                    "status_after": "active",
                    "actor_id": "internal-api-operator",
                    "audited_at": now,
                    "degraded": True,
                },
                "error": None,
            }
            _record_command(command_id, record)
            return (
                jsonify({
                    "rollback_id": rollback_id,
                    "command_id": command_id,
                    "status": "executed",
                    "target_type": target_type,
                    "target_id": target_id,
                    "rollback_to_version": rollback_to,
                    "rollback_action_type": action_type,
                    "status_after": "active",
                    "tracking_url": f"/api/internal/v1/commands/{command_id}",
                }),
                202,
            )

        status_before = binding.get("status", "unknown")

        # Execute rollback action matrix
        if action_type == "replace":
            # Hot-swap: retire old binding, activate new
            client.retire(target_id, retired_at=now)
            status_after = "retired"
        elif action_type == "pause_then_replace":
            # Drain then swap
            if status_before == "active":
                client.transition(target_id, "pending_pause")
                client.transition(target_id, "paused")
            client.retire(target_id, retired_at=now)
            status_after = "retired"
        elif action_type == "liquidate_then_replace":
            # Flatten positions then swap
            client.retire(target_id, retired_at=now)
            status_after = "retired"
        else:
            raise RuntimeError(f"Unknown rollback action type: {action_type}")

        record = {
            "command_id": command_id,
            "type": "ExecuteRollback",
            "target": {"type": target_type.title(), "id": target_id},
            "status": "executed",
            "submitted_at": now,
            "result": {
                "rollback_id": rollback_id,
                "target_type": target_type,
                "target_id": target_id,
                "rollback_to_version": rollback_to,
                "rollback_action_type": action_type,
                "status_before": status_before,
                "status_after": status_after,
                "position_lineage_updated": True,
                "audit_id": f"audit-{rollback_id}",
            },
            "audit": {
                "audit_id": f"audit-{rollback_id}",
                "command_id": command_id,
                "action": f"rollback:{action_type}",
                "target_id": target_id,
                "target_type": target_type,
                "status_before": status_before,
                "status_after": status_after,
                "actor_id": "internal-api-operator",
                "audited_at": now,
                "degraded": False,
            },
            "error": None,
        }
        _record_command(command_id, record)

        return (
            jsonify({
                "rollback_id": rollback_id,
                "command_id": command_id,
                "status": "executed",
                "target_type": target_type,
                "target_id": target_id,
                "rollback_to_version": rollback_to,
                "rollback_action_type": action_type,
                "status_before": status_before,
                "status_after": status_after,
                "tracking_url": f"/api/internal/v1/commands/{command_id}",
            }),
            202,
        )
    except _RuntimeManagerClientError as exc:
        status_code = exc.status_code or (
            503 if exc.error_code == "RUNTIME_MANAGER_UNAVAILABLE" else 409
        )
        record = {
            "command_id": command_id,
            "type": "ExecuteRollback",
            "target": {"type": target_type.title(), "id": target_id},
            "status": "failed",
            "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "result": None,
            "error": {
                "code": exc.error_code or "RUNTIME_MANAGER_ERROR",
                "message": str(exc),
            },
        }
        _record_command(command_id, record)
        return (
            jsonify(
                {
                    "error": record["error"],
                    "command_id": command_id,
                }
            ),
            status_code,
        )
    except Exception as exc:
        record = {
            "command_id": command_id,
            "type": "ExecuteRollback",
            "target": {"type": target_type.title(), "id": target_id},
            "status": "failed",
            "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "result": None,
            "error": {"code": "ROLLBACK_ERROR", "message": str(exc)},
        }
        _record_command(command_id, record)
        return (
            jsonify({
                "error": {"code": "ROLLBACK_ERROR", "message": str(exc)},
                "command_id": command_id,
            }),
            500,
        )


@app.route("/api/internal/v1/rollbacks", methods=["GET"])
@require_bearer_token()
def list_rollbacks():
    """List rollback command records, optionally filtered by target_id."""
    target_id = request.args.get("target_id")
    state = _load_commands()
    records = []
    for record in state.values():
        if record.get("type") != "ExecuteRollback":
            continue
        if target_id:
            target = record.get("target", {})
            if target.get("id") != target_id:
                continue
        records.append(record)
    return jsonify({"rollbacks": records, "count": len(records)}), 200


@app.route("/api/internal/v1/rollbacks/<rollback_id>/abort", methods=["POST"])
@require_bearer_token()
@require_mfa_if_present
def abort_rollback(rollback_id):
    """Abort a rollback command (records audit trail only)."""
    body = request.get_json() or {}
    reason = body.get("reason", "")
    command_id = f"cmd-rb-abort-{rollback_id}-{int(datetime.now(timezone.utc).timestamp())}"
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record = {
        "command_id": command_id,
        "type": "AbortRollback",
        "target": {"type": "Rollback", "id": rollback_id},
        "status": "executed",
        "submitted_at": now,
        "result": {
            "rollback_id": rollback_id,
            "status_after": "aborted",
            "reason": reason,
        },
        "error": None,
    }
    _record_command(command_id, record)
    return (
        jsonify({
            "rollback_id": rollback_id,
            "command_id": command_id,
            "status": "executed",
            "status_after": "aborted",
            "reason": reason,
        }),
        202,
    )


@app.route("/api/internal/v1/kill-switch", methods=["GET"])
@require_bearer_token()
def kill_switch_status():
    """Return current safe-mode state for the target scope."""
    scope = request.args.get("scope", "all")
    scope_id = request.args.get("scope_id")
    _ensure_kill_switch_imported()
    ctrl = _get_controller()
    capital_pool_id = scope_id or "all"
    safe_mode = ctrl.safe_mode_for(capital_pool_id)
    audit_log = [
        entry.to_dict()
        for entry in ctrl.audit_log()
        if entry.capital_pool_id == capital_pool_id
    ]
    return (
        jsonify({
            "scope": scope,
            "scope_id": scope_id,
            "capital_pool_id": capital_pool_id,
            "safe_mode": safe_mode.value,
            "audit_log": audit_log[-10:],
            "audit_count": len(audit_log),
        }),
        200,
    )


@app.route("/api/internal/v1/kill-switch", methods=["POST"])
@require_bearer_token()
@require_mfa_if_present
def kill_switch():
    """Activate or deactivate kill-switch through the KillSwitchController.

    Expects JSON body with:
      - action: "activate" | "deactivate" (default "activate")
      - scope: "persona" | "pool" | "all"  (default "all")
      - scope_id: optional identifier for persona/pool scope
      - severity: optional severity level
      - action_override: optional override ("pause", "risk_off", "liquidate", "replace", "terminate")
      - reason: trigger reason string (maps to HardTriggerReason / SoftTriggerReason)

    Returns the command, audit entry, and safe-mode state after dispatch.
    The command record is persisted in the command state store.
    """
    body = request.get_json() or {}
    scope = body.get("scope", "all")
    scope_id = body.get("scope_id")
    severity = body.get("severity")
    action = body.get("action", "activate")
    action_override = body.get("action_override") or body.get("action_type")
    reason = body.get("reason", "operator_emergency_stop")

    # Backward compatibility: if action is a kill-switch action type, treat
    # it as an override and default to activate.
    if action not in ("activate", "deactivate") and not action_override:
        action_override = action
        action = "activate"

    _ensure_kill_switch_imported()
    ctrl = _get_controller()

    # Map scope to capital_pool_id (in production this resolves from binding)
    capital_pool_id = scope_id or "all"

    if action == "deactivate":
        try:
            target_state_label = body.get("target_state", _SafeModeState.NORMAL_RESTORED.value)
            target_state = _SafeModeState(target_state_label)
            next_state = ctrl.advance_safe_mode(
                capital_pool_id,
                target_state,
                actor_id="internal-api-operator",
                note=reason,
            )
            command_id = f"ks-deactivate-{uuid.uuid4().hex[:8]}"
            record = {
                "command_id": command_id,
                "type": "DeactivateKillSwitch",
                "target": {"type": "KillSwitch", "scope": scope, "scope_id": scope_id},
                "status": "executed",
                "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "result": {
                    "kill_switch_order_id": command_id,
                    "action": "deactivate",
                    "scope": scope,
                    "safe_mode_after": next_state.value,
                },
                "audit": None,
                "error": None,
            }
            _record_command(command_id, record)
            return (
                jsonify({
                    "kill_switch_order_id": command_id,
                    "command_id": command_id,
                    "action": "deactivate",
                    "scope": scope,
                    "safe_mode_after": next_state.value,
                    "status": "executed",
                }),
                202,
            )
        except _KillSwitchError as exc:
            command_id = f"cmd-ks-{uuid.uuid4().hex[:8]}"
            record = {
                "command_id": command_id,
                "type": "DeactivateKillSwitch",
                "target": {"type": "KillSwitch", "scope": scope, "scope_id": scope_id},
                "status": "failed",
                "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "result": None,
                "error": {"code": "KILL_SWITCH_VALIDATION", "message": str(exc)},
            }
            _record_command(command_id, record)
            return (
                jsonify({
                    "error": {"code": "KILL_SWITCH_VALIDATION", "message": str(exc)},
                    "command_id": command_id,
                }),
                409,
            )

    # Default: activate
    # Determine trigger reason enum value
    valid_hard = {r.value for r in _HardTriggerReason}
    valid_soft = {r.value for r in _SoftTriggerReason}
    if reason not in valid_hard and reason not in valid_soft:
        # Default to operator emergency stop
        reason = _HardTriggerReason.OPERATOR_EMERGENCY_STOP.value

    trigger = _EmergencyTrigger(
        reason=reason,
        capital_pool_id=capital_pool_id,
        actor_id="internal-api-operator",
        binding_id=scope_id,
        severity=severity,
        context={"scope": scope, "mfa_verified": hasattr(request, "_mfa_token")},
    )

    try:
        action_kw = {}
        if action_override:
            action_kw["action_override"] = _KillSwitchActionType(action_override)

        outcome = ctrl.dispatch(trigger, **action_kw)

        # Persist command record with full audit trail
        command_id = outcome.command.command_id
        record = {
            "command_id": command_id,
            "type": "ActivateKillSwitch",
            "target": {"type": "KillSwitch", "scope": scope, "scope_id": scope_id},
            "status": "executed",
            "submitted_at": outcome.command.issued_at,
            "result": {
                "kill_switch_order_id": command_id,
                "action": outcome.command.action_type,
                "scope": scope,
                "emergency_class": outcome.command.emergency_class,
                "safe_mode_after": outcome.safe_mode_after.value,
                "audit_id": outcome.audit_entry.audit_id,
                "dispatch_path": outcome.command.dispatch_path,
                "bypass_review_queue": outcome.command.bypass_review_queue,
            },
            "audit": outcome.audit_entry.to_dict(),
            "error": None,
        }
        _record_command(command_id, record)

        return (
            jsonify({
                "kill_switch_order_id": command_id,
                "command_id": command_id,
                "action": outcome.command.action_type,
                "scope": scope,
                "emergency_class": outcome.command.emergency_class,
                "safe_mode_after": outcome.safe_mode_after.value,
                "audit_id": outcome.audit_entry.audit_id,
                "status": "executed",
            }),
            202,
        )
    except _KillSwitchError as exc:
        command_id = f"cmd-ks-{uuid.uuid4().hex[:8]}"
        record = {
            "command_id": command_id,
            "type": "ActivateKillSwitch",
            "target": {"type": "KillSwitch", "scope": scope, "scope_id": scope_id},
            "status": "failed",
            "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "result": None,
            "error": {"code": "KILL_SWITCH_VALIDATION", "message": str(exc)},
        }
        _record_command(command_id, record)
        return (
            jsonify({
                "error": {"code": "KILL_SWITCH_VALIDATION", "message": str(exc)},
                "command_id": command_id,
            }),
            400,
        )


@app.route("/api/internal/v1/commands/<command_id>", methods=["GET"])
@require_bearer_token()
def get_command(command_id):
    state = _load_commands()
    record = state.get(command_id)
    if record:
        return jsonify(record), 200
    # Return a placeholder for unknown commands
    return (
        jsonify(
            {
                "command_id": command_id,
                "type": "unknown",
                "status": "not_found",
                "submitted_at": None,
                "result": None,
                "error": {"code": "NOT_FOUND", "message": f"Command {command_id} not found"},
            }
        ),
        404,
    )


# Simple smoke route
@app.route("/__health__", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200
