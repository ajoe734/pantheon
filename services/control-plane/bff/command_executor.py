"""Real command execution path for operator commands.

Replaces the stub _process_command_stub in main.py with actual execution
that dispatches to the Protected Internal API and records authoritative
status, result, and audit data.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from models import CommandStatus, CommandType

log = logging.getLogger(__name__)

# Internal API base URL (configured via env, defaults to localhost)
_INTERNAL_API_BASE = os.getenv(
    "PANTHEON_INTERNAL_API_URL", "http://localhost:5001"
)
_REQUEST_TIMEOUT = int(os.getenv("PANTHEON_COMMAND_TIMEOUT_SECONDS", "30"))


def _internal_url(path: str) -> str:
    base = _INTERNAL_API_BASE.rstrip("/")
    return f"{base}{path}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _post_json(
    url: str,
    payload: Dict[str, Any],
    auth_token: Optional[str] = None,
    mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """POST JSON to url and return parsed response. Raises on HTTP error."""
    data = json.dumps(payload).encode("utf-8")
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    if mfa_token:
        headers["X-MFA-Token"] = mfa_token
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --------------------------------------------------------------------------- #
# Command dispatch table
# --------------------------------------------------------------------------- #

def _execute_approve_deployment(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch ApproveDeployment to internal API /deployments/<plan_id>/approve."""
    plan_id = params.get("deployment_plan_id")
    payload = {
        "approval_decision": params.get("approval_decision"),
        "verification_timestamp": params.get("verification_timestamp", _utc_now()),
    }
    url = _internal_url(f"/api/internal/v1/deployments/{plan_id}/approve")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    return {
        "approval_decision_id": body.get("approval_decision_id"),
        "target_plan_id": body.get("target_plan_id"),
        "state_after": body.get("state_after"),
        "audit_id": body.get("audit_id"),
        "command_id": command_id,
        "verification_timestamp": body.get("verification_timestamp"),
    }


def _execute_pause_runtime(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch PauseRuntime to internal API /runtimes/<binding_id>/pause."""
    binding_id = params.get("runtime_binding_id") or params.get("binding_id")
    pause_action = params.get("pause_action", "pause")
    payload = {
        "pause_action": pause_action,
        "duration_seconds": params.get("duration_seconds", 3600),
        "reason": params.get("reason", ""),
    }
    url = _internal_url(f"/api/internal/v1/runtimes/{binding_id}/pause")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    return {
        "command_id": command_id,
        "runtime_binding_id": body.get("runtime_binding_id"),
        "pause_action": body.get("pause_action", pause_action),
        "pause_expires_at": body.get("pause_expires_at"),
        "status": body.get("status"),
        "status_after": body.get("status_after"),
        "duration_seconds": body.get("duration_seconds"),
        "reason": body.get("reason"),
    }


def _execute_rollback(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch ExecuteRollback to internal API /rollbacks/execute."""
    payload = {
        "rollback_target_type": params.get("rollback_target_type", "deployment"),
        "target_id": params.get("target_id", "unknown"),
        "rollback_to_version": params.get("rollback_to_version", "previous"),
    }
    url = _internal_url("/api/internal/v1/rollbacks/execute")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    return {
        "rollback_id": body.get("rollback_id"),
        "command_id": command_id,
        "status": body.get("status"),
        "tracking_url": body.get("tracking_url"),
    }


def _execute_activate_kill_switch(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch ActivateKillSwitch to internal API /kill-switch."""
    payload = {
        "action": "activate",
        "scope": params.get("scope", "all"),
        "scope_id": params.get("scope_id"),
        "severity": params.get("severity"),
        "reason": params.get("reason", "operator_emergency_stop"),
    }
    url = _internal_url("/api/internal/v1/kill-switch")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    return {
        "kill_switch_order_id": body.get("kill_switch_order_id"),
        "command_id": command_id,
        "action": body.get("action"),
        "scope": body.get("scope"),
        "status": body.get("status"),
        "safe_mode_after": body.get("safe_mode_after"),
        "audit_id": body.get("audit_id"),
    }


def _execute_approve_evolution_decision(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """ApproveEvolutionDecision: internal API not yet defined; record decision locally."""
    return {
        "evolution_decision_id": params.get("evolution_decision_id"),
        "approval_action": params.get("approval_action"),
        "command_id": command_id,
        "approved_by_role": params.get("approved_by_role"),
        "rationale": params.get("rationale", ""),
        "timestamp": _utc_now(),
    }


def _execute_evolution_action(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """ExecuteEvolutionAction: internal API not yet defined; record action locally."""
    return {
        "evolution_action_id": params.get("evolution_action_id", f"ea-{command_id[:8]}"),
        "action_type": params.get("action_type"),
        "command_id": command_id,
        "status": "dispatched",
        "timestamp": _utc_now(),
    }


# Dispatch table: CommandType -> execution function
_EXECUTORS = {
    CommandType.APPROVE_DEPLOYMENT: _execute_approve_deployment,
    CommandType.PAUSE_RUNTIME: _execute_pause_runtime,
    CommandType.EXECUTE_ROLLBACK: _execute_rollback,
    CommandType.ACTIVATE_KILL_SWITCH: _execute_activate_kill_switch,
    CommandType.APPROVE_EVOLUTION_DECISION: _execute_approve_evolution_decision,
    CommandType.EXECUTE_EVOLUTION_ACTION: _execute_evolution_action,
}


# --------------------------------------------------------------------------- #
# Public execution entry point
# --------------------------------------------------------------------------- #

def execute_command(
    command_id: str,
    command_type: CommandType,
    params: Dict[str, Any],
    auth_token: Optional[str] = None,
    mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a command by dispatching to the appropriate internal API endpoint.

    Returns the result payload on success.
    Raises Exception on any failure (caller should catch and record as FAILED).
    """
    executor = _EXECUTORS.get(command_type)
    if executor is None:
        raise ValueError(f"No executor for command type: {command_type}")
    return executor(command_id, params, auth_token=auth_token, mfa_token=mfa_token)


def execute_command_with_status(
    command_id: str,
    command_type: CommandType,
    params: Dict[str, Any],
    auth_token: Optional[str] = None,
    mfa_token: Optional[str] = None,
) -> Tuple[CommandStatus, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Execute a command and return (status, result, error).

    Never raises. On any exception, returns (FAILED/TIMEOUT, None, error_dict).
    On success, returns (EXECUTED, result_dict, None).
    """
    started_at = _utc_now()
    try:
        result = execute_command(
            command_id, command_type, params,
            auth_token=auth_token, mfa_token=mfa_token,
        )
        completed_at = _utc_now()
        result["execution_started_at"] = started_at
        result["execution_completed_at"] = completed_at
        return CommandStatus.EXECUTED, result, None
    except urllib.error.URLError as exc:
        # Covers connection failures, timeouts, SSL errors
        reason = str(getattr(exc, "reason", exc))
        is_timeout = "timed out" in reason.lower() or "timeout" in reason.lower()
        code = "COMMAND_TIMEOUT" if is_timeout else "DOWNSTREAM_UNAVAILABLE"
        status = CommandStatus.TIMEOUT if is_timeout else CommandStatus.FAILED
        error = {
            "code": code,
            "message": f"Internal API unreachable for {command_id}: {reason}",
            "started_at": started_at,
            "failed_at": _utc_now(),
            "suggestion": "Check internal API availability and network connectivity",
        }
        log.error("Command %s URL error: %s", command_id, error["message"])
        return status, None, error
    except urllib.error.HTTPError as exc:
        error = {
            "code": "DOWNSTREAM_ERROR",
            "message": f"Internal API returned {exc.code} for {command_id}",
            "started_at": started_at,
            "failed_at": _utc_now(),
            "downstream_status": exc.code,
            "suggestion": "Check internal API health and retry if appropriate",
        }
        log.error("Command %s HTTP error: %s", command_id, error["message"])
        return CommandStatus.FAILED, None, error
    except TimeoutError:
        error = {
            "code": "COMMAND_TIMEOUT",
            "message": f"Command {command_id} timed out after {_REQUEST_TIMEOUT}s",
            "started_at": started_at,
            "failed_at": _utc_now(),
            "suggestion": "Retry the command or escalate if downstream is unresponsive",
        }
        log.error("Command %s timed out", command_id)
        return CommandStatus.TIMEOUT, None, error
    except Exception as exc:
        error = {
            "code": "EXECUTION_ERROR",
            "message": f"Unexpected error executing command {command_id}: {exc}",
            "started_at": started_at,
            "failed_at": _utc_now(),
            "suggestion": "Review command parameters and retry, or escalate to platform team",
        }
        log.exception("Command %s execution error", command_id)
        return CommandStatus.FAILED, None, error
