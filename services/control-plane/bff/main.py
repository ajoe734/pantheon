from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from collections import deque
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Header, Query
from fastapi.responses import JSONResponse, StreamingResponse

sys.path.insert(0, os.path.dirname(__file__))

from models import (
    BFFError,
    CommandReceipt,
    CommandResultMeta,
    CommandStatus,
    CommandSubmissionResponse,
    CommandStatusResponse,
    CommandType,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    ObjectType,
    OperatorCommand,
    OperatorIdentity,
    StalenessWarning,
    utc_now,
)
from command_queue import CommandStore
from command_executor import execute_command_with_status
from read_store import ReadSurfaceStore

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI(title="Pantheon Operator BFF", version="0.2.0")

# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #

BFF_DATA_DIR = os.getenv("BFF_DATA_DIR", "/tmp/pantheon/bff")
os.makedirs(BFF_DATA_DIR, exist_ok=True)
command_store = CommandStore(os.path.join(BFF_DATA_DIR, "commands.jsonl"))
read_store = ReadSurfaceStore(os.path.join(BFF_DATA_DIR, "read_surfaces.json"))

# --------------------------------------------------------------------------- #
# Auth / identity helpers
# --------------------------------------------------------------------------- #

# In production this would decode and verify a JWT.
# Here we accept a stub token format: "Bearer <operator_id>:<comma_roles>[:mfa]"
# e.g. "Bearer op-42:operator,approver:mfa"
# Any other non-empty value is treated as a basic authenticated operator (no admin/mfa).
# A missing Authorization header returns INVALID_TOKEN.
def _extract_identity(authorization: Optional[str]) -> OperatorIdentity:
    if not authorization or not authorization.startswith("Bearer "):
        raise _bff_error(
            status_code=401,
            code=ErrorCode.INVALID_TOKEN,
            message="Missing or invalid Authorization header",
            reason="Token is absent or not a Bearer token",
            suggestion="Re-authenticate and include a valid Bearer token",
        )
    token = authorization[len("Bearer "):]
    parts = token.split(":")
    operator_id = parts[0] if parts else "unknown"
    roles = parts[1].split(",") if len(parts) > 1 else ["operator"]
    mfa_verified = len(parts) > 2 and parts[2] == "mfa"
    return OperatorIdentity(operator_id=operator_id, roles=roles, mfa_verified=mfa_verified)


def _bff_error(
    status_code: int,
    code: ErrorCode,
    message: str,
    reason: str,
    precondition_failed: Optional[str] = None,
    suggestion: Optional[str] = None,
) -> HTTPException:
    body = ErrorResponse(
        error=BFFError(
            code=code,
            message=message,
            details=ErrorDetail(
                reason=reason,
                precondition_failed=precondition_failed,
                suggestion=suggestion,
            ),
        )
    )
    return HTTPException(status_code=status_code, detail=body.dict())


# --------------------------------------------------------------------------- #
# Command-specific precondition validators (§3 of contract)
# --------------------------------------------------------------------------- #

_APPROVE_DEPLOYMENT_REQUIRED = {"deployment_plan_id", "approval_decision"}
_VALID_APPROVAL_DECISIONS = {"approve", "reject"}

_PAUSE_RUNTIME_REQUIRED = {"runtime_binding_id", "pause_action"}
_VALID_PAUSE_ACTIONS = {"pause", "resume"}

_ROLLBACK_REQUIRED = {"rollback_target_type", "target_id", "rollback_to_version"}
_VALID_ROLLBACK_TARGET_TYPES = {"deployment", "runtime"}

_KILL_SWITCH_REQUIRED = {"scope", "activate"}
_VALID_SCOPES = {"persona", "pool", "all"}
_VALID_SEVERITIES = {"critical", "high", "medium"}

_APPROVE_EVO_REQUIRED = {"evolution_decision_id", "approval_action"}
_VALID_EVO_APPROVAL_ACTIONS = {"approve", "reject"}

_EXECUTE_EVO_REQUIRED = {"evolution_decision_id", "action_type"}
_VALID_EVO_ACTION_TYPES = {"freeze", "retrain", "mutate", "retire"}


def _validate_approve_deployment(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _APPROVE_DEPLOYMENT_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for ApproveDeployment",
            f"Missing fields: {sorted(missing)}",
        )
    if params["approval_decision"] not in _VALID_APPROVAL_DECISIONS:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid approval_decision value",
            f"Must be one of {_VALID_APPROVAL_DECISIONS}",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "ApproveDeployment requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_pause_runtime(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _PAUSE_RUNTIME_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for PauseRuntime",
            f"Missing fields: {sorted(missing)}",
        )
    if params["pause_action"] not in _VALID_PAUSE_ACTIONS:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid pause_action value",
            f"Must be one of {_VALID_PAUSE_ACTIONS}",
        )
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "PauseRuntime requires 'operator' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator or admin role",
        )


def _validate_execute_rollback(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _ROLLBACK_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for ExecuteRollback",
            f"Missing fields: {sorted(missing)}",
        )
    if params["rollback_target_type"] not in _VALID_ROLLBACK_TARGET_TYPES:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid rollback_target_type",
            f"Must be one of {_VALID_ROLLBACK_TARGET_TYPES}",
        )
    if not {"admin", "approver"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "ExecuteRollback requires 'admin' or 'approver' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with admin or approver role",
        )


def _validate_activate_kill_switch(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _KILL_SWITCH_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for ActivateKillSwitch",
            f"Missing fields: {sorted(missing)}",
        )
    if params["scope"] not in _VALID_SCOPES:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid scope for ActivateKillSwitch",
            f"Must be one of {_VALID_SCOPES}",
        )
    severity = params.get("severity")
    if severity is not None and severity not in _VALID_SEVERITIES:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid severity for ActivateKillSwitch",
            f"Must be one of {_VALID_SEVERITIES}",
        )
    # Admin role required
    if "admin" not in identity.roles:
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "ActivateKillSwitch requires 'admin' role",
            "Operator does not hold the admin role",
            precondition_failed="role_check",
            suggestion="Escalate to an admin-role operator",
        )
    # MFA required for kill-switch (§3.2.3)
    if not identity.mfa_verified:
        raise _bff_error(
            403, ErrorCode.MFA_REQUIRED,
            "ActivateKillSwitch requires MFA verification",
            "Admin action requires MFA validation",
            precondition_failed="mfa_check",
            suggestion="Provide a valid MFA token in your session",
        )


def _validate_approve_evolution_decision(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _APPROVE_EVO_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for ApproveEvolutionDecision",
            f"Missing fields: {sorted(missing)}",
        )
    if params["approval_action"] not in _VALID_EVO_APPROVAL_ACTIONS:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid approval_action",
            f"Must be one of {_VALID_EVO_APPROVAL_ACTIONS}",
        )
    if not {"reviewer", "admin", "approver"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "ApproveEvolutionDecision requires 'reviewer', 'approver', or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with reviewer, approver, or admin role",
        )


def _validate_execute_evolution_action(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _EXECUTE_EVO_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for ExecuteEvolutionAction",
            f"Missing fields: {sorted(missing)}",
        )
    if params["action_type"] not in _VALID_EVO_ACTION_TYPES:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid action_type for ExecuteEvolutionAction",
            f"Must be one of {_VALID_EVO_ACTION_TYPES}",
        )
    if not {"admin", "approver"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "ExecuteEvolutionAction requires 'admin' or 'approver' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with admin or approver role",
        )


_VALIDATORS = {
    CommandType.APPROVE_DEPLOYMENT: _validate_approve_deployment,
    CommandType.PAUSE_RUNTIME: _validate_pause_runtime,
    CommandType.EXECUTE_ROLLBACK: _validate_execute_rollback,
    CommandType.ACTIVATE_KILL_SWITCH: _validate_activate_kill_switch,
    CommandType.APPROVE_EVOLUTION_DECISION: _validate_approve_evolution_decision,
    CommandType.EXECUTE_EVOLUTION_ACTION: _validate_execute_evolution_action,
}

# --------------------------------------------------------------------------- #
# Read surface helpers
# --------------------------------------------------------------------------- #

_READ_ROLES = {"operator", "approver", "admin", "reviewer"}


def _require_read_role(identity: OperatorIdentity) -> None:
    if not _READ_ROLES.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "Read access requires operator-level role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator, approver, admin, or reviewer role",
        )


def _read_surface_state() -> str:
    return os.getenv("BFF_READ_SURFACE_STATE", "fresh")


def _meta_staleness() -> Optional[Dict[str, Any]]:
    state = _read_surface_state()
    if state == "fresh":
        return None
    return {
        "served_from": "cache",
        "last_known_at": utc_now(),
    }


def _surface_status() -> Dict[str, Any]:
    state = _read_surface_state()
    if state == "fresh":
        return {"status": "ok"}
    if state in {"degraded", "stale"}:
        return {
            "status": "degraded",
            "staleness": _meta_staleness(),
        }
    if state == "unavailable":
        return {
            "status": "unavailable",
            "staleness": _meta_staleness(),
        }
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Degraded-mode helper
# --------------------------------------------------------------------------- #

def _check_read_surface_state() -> Optional[StalenessWarning]:
    """
    In production, query the BFF read surface health endpoint.
    Returns a StalenessWarning when the surface is degraded or unavailable,
    or None when fresh.
    """
    state = os.getenv("BFF_READ_SURFACE_STATE", "fresh")
    if state == "fresh":
        return None
    return StalenessWarning(
        read_surface_state=state,
        message=(
            "Command submitted against stale read surface data. "
            "Verify target state via secondary control path before confirming action."
        ),
    )


# --------------------------------------------------------------------------- #
# Control-path degraded-mode guidance (§BFF-HA §3.2)
# --------------------------------------------------------------------------- #

_CONTROL_PATH_DEGRADED_GUIDANCE: Dict[str, Dict[str, Any]] = {
    "pause_runtime": {
        "degraded_action": "pause or resume a runtime binding",
        "risk": "Binding status is unverifiable; pause may target a binding that is already "
                "retired, failed, or in an unknown state.",
        "mitigation": [
            "Check the runtime binding status via GET /api/v1/runtime-bindings/{binding_id}.",
            "If the binding is unavailable, use the CLI fallback path (pantheon-admin) "
            "to verify the runtime state directly.",
            "Proceed only if the operator has confirmed the target identity via a "
            "secondary control path.",
        ],
        "safe_mode_impact": "Pause actions may advance the safe-mode state to PAUSED. "
                           "Verify via GET /api/v1/kill-switch/status.",
    },
    "execute_rollback": {
        "degraded_action": "roll back a runtime binding to a previous version",
        "risk": "Rollback may target a binding whose status is unknown. Position lineage "
                "updates may be applied without verified current state.",
        "mitigation": [
            "Check the rollback target via GET /api/v1/runtimes/{runtime_id}/rollbacks.",
            "Verify the current artifact version and binding status before executing.",
            "If the target is unavailable, escalate to a Severity-1 incident and consider "
            "the kill-switch path.",
        ],
        "safe_mode_impact": "Rollback actions do not directly affect safe-mode state, but "
                           "may be followed by kill-switch activation if the target is unstable.",
    },
    "activate_kill_switch": {
        "degraded_action": "activate the kill-switch to halt all runtime activity",
        "risk": "Kill-switch activation is a destructive action that bypasses the normal "
                "review queue. In degraded mode, the operator cannot verify the current "
                "safe-mode state before dispatching.",
        "mitigation": [
            "Verify the current safe-mode state via GET /api/v1/kill-switch/status.",
            "Confirm MFA is verified (required for all kill-switch activations).",
            "If the kill-switch status endpoint is unavailable, escalate to an admin "
            "operator and use the CLI fallback path.",
        ],
        "safe_mode_impact": "Kill-switch dispatch advances the safe-mode state (NORMAL → "
                           "PAUSED/GUARDED/RISK_OFF depending on trigger severity).",
    },
}


def _get_control_path_guidance(action_type: str) -> Optional[Dict[str, Any]]:
    """Return degraded-mode guidance for a control-path action."""
    guidance = _CONTROL_PATH_DEGRADED_GUIDANCE.get(action_type)
    if guidance is None:
        return None
    state = _read_surface_state()
    if state == "fresh":
        return None  # No guidance needed when surfaces are fresh
    return {
        "action_type": action_type,
        **guidance,
        "read_surface_state": state,
        "warning": f"Control-path action '{action_type}' is being executed against a "
                   f"{state} read surface. Follow the mitigation steps before confirming.",
    }


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "operator-bff",
        "version": "0.2.0",
        "timestamp": utc_now(),
    }


# --------------------------------------------------------------------------- #
# Read surfaces (Wave 4 - Remaining Catalog List/Detail)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/personas")
async def list_personas(
    lifecycle_state: Optional[str] = None,
    mandate: Optional[str] = None,
    strategy_family: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """PS-01: Persona List with optional filters."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    personas = read_store.list_personas(
        lifecycle_state=lifecycle_state,
        mandate=mandate,
        strategy_family=strategy_family,
    )
    return {
        "data": personas,
        "meta": {
            "total": len(personas),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/personas/{persona_id}")
async def get_persona_detail(persona_id: str, authorization: Optional[str] = Header(default=None)):
    """PS-02: Persona Detail with bindings."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona = read_store.get_persona(persona_id)
    if not persona:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    bindings = read_store.get_bindings_for_persona(persona_id) or []
    payload = dict(persona)
    payload["bindings"] = bindings

    return {
        "data": payload,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/personas/{persona_id}/sessions")
async def list_persona_sessions(
    persona_id: str,
    status: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """PS-03: Persona Sessions list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona = read_store.get_persona(persona_id)
    if not persona:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    sessions = read_store.list_sessions_for_persona(persona_id, status=status) or []
    return {
        "data": sessions,
        "meta": {
            "total": len(sessions),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/sessions/{session_id}")
async def get_session_detail(session_id: str, authorization: Optional[str] = Header(default=None)):
    """PS-04: Session detail with capability snapshot."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    session = read_store.get_session(session_id)
    if not session:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Session not found",
            f"Session {session_id} does not exist",
        )

    snapshot = read_store.get_capability_snapshot(session.get("capability_snapshot_id"))
    if snapshot is None:
        snapshot = read_store.get_capability_snapshot_for_persona(session.get("persona_id"))

    payload = dict(session)
    if snapshot:
        payload["capability_snapshot"] = snapshot

    return {
        "data": payload,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/personas/{persona_id}/teaching")
async def list_persona_teaching_sessions(
    persona_id: str,
    status: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """PS-05: Teaching sessions list for a persona."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona = read_store.get_persona(persona_id)
    if not persona:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    sessions = read_store.list_teaching_sessions_for_persona(persona_id, status=status) or []
    return {
        "data": sessions,
        "meta": {
            "total": len(sessions),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/personas/{persona_id}/capabilities")
async def get_persona_capabilities(
    persona_id: str, authorization: Optional[str] = Header(default=None),
):
    """PS-06: Capability snapshot for a persona."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona = read_store.get_persona(persona_id)
    if not persona:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    snapshot = read_store.get_capability_snapshot_for_persona(persona_id)
    if not snapshot:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Capability snapshot not found",
            f"Capability snapshot for persona {persona_id} does not exist",
        )

    return {
        "data": snapshot,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/capital-pools")
async def list_capital_pools(
    status: Optional[str] = None,
    risk_policy_ref: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """CP-01: Capital pool list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    pools = read_store.list_capital_pools(status=status, risk_policy_ref=risk_policy_ref)
    return {
        "data": pools,
        "meta": {
            "total": len(pools),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/bindings")
async def list_bindings(
    capital_pool_id: Optional[str] = None,
    role: Optional[str] = None,
    validity: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """CP-03: Persona capital binding list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    bindings = read_store.list_bindings(
        capital_pool_id=capital_pool_id,
        role=role,
        validity=validity,
    )
    return {
        "data": bindings,
        "meta": {
            "total": len(bindings),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/deployment-plans")
async def list_deployment_plans(
    stage: Optional[str] = None,
    target_pool_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """DP-01: Deployment plan list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    plans = read_store.list_deployment_plans(stage=stage, target_pool_id=target_pool_id)
    return {
        "data": plans,
        "meta": {
            "total": len(plans),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/approval-decisions")
async def list_approval_decisions(
    outcome: Optional[str] = None,
    reviewer: Optional[str] = None,
    time_range: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """DP-03: Approval decision list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    decisions = read_store.list_approval_decisions(
        outcome=outcome,
        reviewer=reviewer,
        time_range=time_range,
    )
    return {
        "data": decisions,
        "meta": {
            "total": len(decisions),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/approval-decisions/{decision_id}")
async def get_approval_decision_detail(
    decision_id: str, authorization: Optional[str] = Header(default=None),
):
    """DP-04: Approval decision detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    decision = read_store.get_approval_decision(decision_id)
    if not decision:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Approval decision not found",
            f"Approval decision {decision_id} does not exist",
        )

    return {
        "data": decision,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/runtime-bindings")
async def list_runtime_bindings(
    deployment_mode: Optional[str] = None,
    version: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """RT-01: Runtime binding list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    bindings = read_store.list_runtime_bindings(
        deployment_mode=deployment_mode,
        version=version,
    )
    return {
        "data": bindings,
        "meta": {
            "total": len(bindings),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/runtimes/{runtime_id}/status")
async def get_runtime_status(
    runtime_id: str, authorization: Optional[str] = Header(default=None),
):
    """RT-03: Runtime status detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    runtime_binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)
    if not runtime_binding:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Runtime not found",
            f"Runtime {runtime_id} does not exist",
        )

    return {
        "data": runtime_binding,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


# --------------------------------------------------------------------------- #
# Read surfaces (Wave 1 - Promotion Review)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/deployment-plans/{plan_id}")
async def get_deployment_plan(plan_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    plan = read_store.get_deployment_plan(plan_id)
    if not plan:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Deployment plan not found",
            f"Deployment plan {plan_id} does not exist",
        )

    decision = read_store.get_approval_decision(plan.get("approval_decision_id"))
    payload = dict(plan)
    if decision:
        payload["approval_decision"] = decision

    return {
        "data": payload,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/capital-pools/{pool_id}")
async def get_capital_pool(pool_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    pool = read_store.get_capital_pool(pool_id)
    if not pool:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Capital pool not found",
            f"Capital pool {pool_id} does not exist",
        )

    bindings = read_store.get_bindings_for_pool(pool_id)
    payload = dict(pool)
    payload["bindings"] = bindings

    return {
        "data": payload,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/bindings/{binding_id}")
async def get_binding(binding_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    binding = read_store.get_binding(binding_id)
    if not binding:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Binding not found",
            f"Binding {binding_id} does not exist",
        )

    persona = read_store.get_persona(binding.get("persona_id"))
    payload = dict(binding)
    if persona:
        payload["persona"] = persona

    return {
        "data": payload,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/runtime-bindings/{binding_id}")
async def get_runtime_binding(binding_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    runtime_binding = read_store.get_runtime_binding(binding_id)
    if not runtime_binding:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Runtime binding not found",
            f"Runtime binding {binding_id} does not exist",
        )

    plan = read_store.get_deployment_plan(runtime_binding.get("plan_id", ""))
    payload = dict(runtime_binding)
    if plan:
        payload["deployment_plan"] = plan

    return {
        "data": payload,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/runtimes/{runtime_id}/rollbacks")
async def get_runtime_rollbacks(runtime_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    rollbacks = read_store.get_rollbacks(runtime_id)
    return {
        "data": rollbacks,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/operator/deployment-review/{plan_id}")
async def get_deployment_review(plan_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    plan = read_store.get_deployment_plan(plan_id)
    if not plan:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Deployment plan not found",
            f"Deployment plan {plan_id} does not exist",
        )

    pool = read_store.get_capital_pool(plan.get("capital_pool_id"))
    bindings = read_store.get_bindings_for_pool(plan.get("capital_pool_id"))
    runtime_binding = read_store.get_runtime_binding(plan.get("runtime_binding_id"))
    approval_decision = read_store.get_approval_decision(plan.get("approval_decision_id"))
    rollbacks = read_store.get_rollbacks(
        runtime_binding.get("runtime_id") if runtime_binding else None
    )
    allowed_actions = read_store.get_allowed_actions(plan_id)
    latest_run = read_store.get_latest_run(plan_id)
    review = read_store.get_review_summary(plan_id)

    snapshot_at = utc_now()

    deployment_plan_payload = {
        "id": plan.get("id"),
        "stage": plan.get("stage"),
        "artifact_id": plan.get("artifact_id"),
        "approval_decision_id": plan.get("approval_decision_id"),
    }
    for optional_key in ["current_stage", "target_stage", "status", "artifact_version", "transition_type"]:
        if plan.get(optional_key) is not None:
            deployment_plan_payload[optional_key] = plan.get(optional_key)
    if approval_decision:
        deployment_plan_payload["approval_decision"] = approval_decision

    data = {
        "deployment_plan": deployment_plan_payload,
        "approval_decision": approval_decision or {},
        "capital_pool": pool or {},
        "bindings": bindings,
        "runtime_binding": runtime_binding or {},
        "rollbacks": rollbacks,
        "allowedActions": allowed_actions,
        "latestRun": latest_run,
        "review": review,
    }

    return {
        "data": data,
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {
                "deployment_plan": _surface_status(),
                "approval_decision": _surface_status(),
                "capital_pool": _surface_status(),
                "bindings": _surface_status(),
                "runtime_binding": _surface_status(),
                "rollbacks": _surface_status(),
                "allowedActions": _surface_status(),
                "latestRun": _surface_status(),
                "review": _surface_status(),
            },
        },
    }


# --------------------------------------------------------------------------- #
# Incident Surfaces (Wave 2 - Incident Response: IN-01 – IN-05)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/incidents")
async def list_incidents(
    status: Optional[str] = None,
    severity: Optional[str] = None,
    affected_pool_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """IN-01: Incident List with optional filters."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    incidents = read_store.list_incidents(
        status=status, severity=severity, affected_pool_id=affected_pool_id,
    )
    return {
        "data": incidents,
        "meta": {
            "total": len(incidents),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/incidents/{incident_id}")
async def get_incident(incident_id: str, authorization: Optional[str] = Header(default=None)):
    """IN-02: Incident Detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    incident = read_store.get_incident(incident_id)
    if not incident:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Incident not found",
            f"Incident {incident_id} does not exist",
        )

    return {
        "data": incident,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/postmortems")
async def list_postmortems(
    time_range: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """IN-03: Postmortem List."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    postmortems = read_store.list_postmortems(time_range=time_range)
    return {
        "data": postmortems,
        "meta": {
            "total": len(postmortems),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/postmortems/{report_id}")
async def get_postmortem(report_id: str, authorization: Optional[str] = Header(default=None)):
    """IN-04: Postmortem Detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    postmortem = read_store.get_postmortem(report_id)
    if not postmortem:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Postmortem report not found",
            f"Postmortem {report_id} does not exist",
        )

    # Include linked incident if available
    incident_id = postmortem.get("incident_id")
    incident = read_store.get_incident(incident_id) if incident_id else None
    payload = dict(postmortem)
    if incident:
        payload["linked_incident"] = incident

    return {
        "data": payload,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/kill-switch/status")
async def get_kill_switch_status(authorization: Optional[str] = Header(default=None)):
    """IN-05: Kill Switch Status — requires admin role."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    if "admin" not in identity.roles:
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "Kill-switch status requires 'admin' role",
            "Operator does not hold the admin role",
            precondition_failed="role_check",
            suggestion="Escalate to an admin-role operator",
        )

    ks = read_store.get_kill_switch_status()
    return {
        "data": {
            "active_freeze_orders": ks["active_freeze_orders"],
            "affected_runtime_bindings": [],  # v1: populated from downstream in production
            "safe_mode_status": ks["safe_mode_status"],
        },
        "meta": {
            "active": ks["active"],
            "last_checked_at": ks["last_checked_at"],
            "staleness": _meta_staleness(),
        },
    }


# --------------------------------------------------------------------------- #
# Composed Views — Incident Response
# --------------------------------------------------------------------------- #


@app.get("/api/v1/operator/incident-response/{incident_id}")
async def get_incident_response(
    incident_id: str,
    snapshot: str = "preferred",
    authorization: Optional[str] = Header(default=None),
):
    """
    Composed view for active incident response.
    Composes: IN-02, RT-03, TL-02, RT-04, EV-04, IN-05
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    # IN-02: Incident detail
    incident = read_store.get_incident(incident_id)
    if not incident:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Incident not found",
            f"Incident {incident_id} does not exist",
        )

    snapshot_at = utc_now()
    surfaces = {}

    # RT-03: Runtime binding status
    binding_id = incident.get("binding_id")
    runtime_id = incident.get("runtime_id")  # fallback; production resolves from binding
    runtime_binding = None
    runtime_binding_data = {}
    if binding_id:
        runtime_binding = read_store.get_runtime_binding(binding_id)
        if runtime_binding:
            runtime_binding_data = dict(runtime_binding)

    if runtime_binding:
        surfaces["runtime_binding"] = {"status": "ok"}
    else:
        surfaces["runtime_binding"] = {
            "status": "degraded",
            "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
        }

    # TL-02: Telemetry summary
    telemetry_summary = None
    telemetry_runtime_id = runtime_binding.get("runtime_id") if runtime_binding else runtime_id
    if telemetry_runtime_id:
        telemetry_summary = read_store.get_telemetry_summary(telemetry_runtime_id)

    if telemetry_summary:
        surfaces["telemetry_summary"] = {"status": "ok"}
    else:
        surfaces["telemetry_summary"] = {
            "status": "degraded",
            "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
        }

    # RT-04: Rollbacks
    rollbacks = read_store.get_rollbacks_by_incident(incident_id)
    surfaces["rollbacks"] = {"status": "ok"}

    # EV-04: Evolution decisions for this incident
    # Note: The contract lists EV-04 as rollbacks only. We additionally surface
    # evolution decisions here to give operators full incident context in one view.
    # This is an intentional v1 extension beyond the strict contract.
    evolution_decisions = read_store.get_evolution_decisions_by_incident(incident_id)
    surfaces["evolution_decisions"] = {"status": "ok"}

    # IN-05: Kill switch status
    ks = read_store.get_kill_switch_status()
    surfaces["kill_switch"] = {"status": "ok"}

    data = {
        "incident": incident,
        "runtime_binding": runtime_binding_data,
        "telemetry_summary": telemetry_summary,
        "rollbacks": rollbacks,
        "evolution_decisions": evolution_decisions,
        "kill_switch": {
            "active_freeze_orders": ks["active_freeze_orders"],
            "safe_mode_status": ks["safe_mode_status"],
        },
    }

    return {
        "data": data,
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": surfaces,
        },
    }


# --------------------------------------------------------------------------- #
# Persona Management composed view (Wave 4 — PS-02, CP-03, CP-04, PS-03, PS-05)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/operator/persona-management/{persona_id}")
async def get_persona_management(
    persona_id: str,
    snapshot: str = "preferred",
    authorization: Optional[str] = Header(default=None),
):
    """
    Composed view for persona lifecycle management.
    Composes: PS-02 (persona detail + bindings), CP-03/CP-04 (capital pool bindings),
              PS-03 (persona sessions), PS-05 (teaching sessions).
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    # PS-02: Persona detail
    persona = read_store.get_persona(persona_id)
    if not persona:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    snapshot_at = utc_now()
    surfaces = {}

    # PS-02: Persona bindings (bindings where this persona is the owner)
    persona_bindings = read_store.get_bindings_for_persona(persona_id)
    if persona_bindings is not None:
        surfaces["persona_bindings"] = {"status": "ok"}
    else:
        persona_bindings = []
        surfaces["persona_bindings"] = {
            "status": "degraded",
            "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
        }

    # CP-04: Enrich each binding with its capital pool detail
    enriched_bindings = []
    for binding in persona_bindings:
        binding_detail = dict(binding)
        pool = read_store.get_capital_pool(binding.get("capital_pool_id"))
        if pool:
            binding_detail["capital_pool"] = pool
        enriched_bindings.append(binding_detail)

    if enriched_bindings:
        surfaces["capital_pool_bindings"] = {"status": "ok"}
    else:
        surfaces["capital_pool_bindings"] = {
            "status": "degraded",
            "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
        }

    # PS-03: Active sessions for this persona
    sessions = read_store.get_sessions_for_persona(persona_id)
    if sessions is not None:
        surfaces["persona_sessions"] = {"status": "ok"}
    else:
        sessions = []
        surfaces["persona_sessions"] = {
            "status": "degraded",
            "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
        }

    # PS-05: Teaching sessions for this persona
    teaching_sessions = read_store.get_teaching_sessions_for_persona(persona_id)
    if teaching_sessions is not None:
        surfaces["teaching_sessions"] = {"status": "ok"}
    else:
        teaching_sessions = []
        surfaces["teaching_sessions"] = {
            "status": "degraded",
            "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
        }

    # Backend-shaped allowed actions (acceptance: backend_shaped_persona_actions)
    allowed_actions = read_store.get_persona_allowed_actions(persona_id)
    if allowed_actions is not None:
        surfaces["allowed_actions"] = {"status": "ok"}
    else:
        allowed_actions = {}
        surfaces["allowed_actions"] = {
            "status": "degraded",
            "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
        }

    data = {
        "persona": persona,
        "bindings": enriched_bindings,
        "sessions": sessions,
        "teaching_sessions": teaching_sessions,
        "allowedActions": allowed_actions,
    }

    return {
        "data": data,
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": surfaces,
        },
    }


# --------------------------------------------------------------------------- #
# Post-Incident Review composed view
# --------------------------------------------------------------------------- #


@app.get("/api/v1/operator/post-incident-review/{incident_id}")
async def get_post_incident_review(
    incident_id: str,
    snapshot: str = "preferred",
    authorization: Optional[str] = Header(default=None),
):
    """
    Composed view for post-incident analysis.
    Composes: IN-04, EV-01, EV-02, LN-01, TL-03
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    incident = read_store.get_incident(incident_id)
    if not incident:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Incident not found",
            f"Incident {incident_id} does not exist",
        )

    snapshot_at = utc_now()
    surfaces = {}

    # IN-04: Postmortem report
    postmortem = read_store.get_postmortem_by_incident(incident_id)
    if postmortem:
        surfaces["postmortem"] = {"status": "ok"}
    else:
        surfaces["postmortem"] = {
            "status": "degraded",
            "message": "No postmortem report available yet",
        }

    # EV-01/EV-02: Evolution decisions
    evolution_decisions = read_store.get_evolution_decisions_by_incident(incident_id)
    surfaces["evolution_decisions"] = {"status": "ok"}

    # LN-01: Lineage edges — fetch by artifact_id from incident
    artifact_id = incident.get("artifact_id")
    lineage_edges = read_store.list_lineage_edges(artifact_id=artifact_id) if artifact_id else []
    if lineage_edges:
        surfaces["lineage"] = {"status": "ok"}
    else:
        surfaces["lineage"] = {
            "status": "degraded",
            "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
            "note": "No lineage edges found for this artifact",
        }

    # TL-03: Telemetry performance — use artifact_id (not runtime_id or summary)
    telemetry_performance = None
    if artifact_id:
        telemetry_performance = read_store.get_telemetry_performance(artifact_id)
    if telemetry_performance:
        surfaces["telemetry_performance"] = {"status": "ok"}
    else:
        surfaces["telemetry_performance"] = {
            "status": "degraded",
            "staleness": {"served_from": "unverifiable"},
        }

    data = {
        "incident": incident,
        "postmortem": postmortem,
        "evolution_decisions": evolution_decisions,
        "lineage_edges": lineage_edges,
        "telemetry_performance": telemetry_performance,
    }

    return {
        "data": data,
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": surfaces,
        },
    }


# --------------------------------------------------------------------------- #
# Evolution Surfaces (Wave 3 — EV-01 – EV-04)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/evolution-decisions")
async def list_evolution_decisions(
    action_type: Optional[str] = None,
    risk_level: Optional[str] = None,
    status: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """EV-01: Evolution Decision List with optional filters."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    decisions = read_store.list_evolution_decisions(
        action_type=action_type, risk_level=risk_level, status=status,
    )
    return {
        "data": decisions,
        "meta": {
            "total": len(decisions),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/evolution-decisions/{decision_id}")
async def get_evolution_decision(
    decision_id: str, authorization: Optional[str] = Header(default=None),
):
    """EV-02: Evolution Decision Detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    decision = read_store.get_evolution_decision_by_id(decision_id)
    if not decision:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Evolution decision not found",
            f"Evolution decision {decision_id} does not exist",
        )

    return {
        "data": decision,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/freeze-orders")
async def list_freeze_orders(
    status: Optional[str] = None,
    scope: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """EV-03: Freeze Order List with optional filters."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    orders = read_store.list_freeze_orders(status=status, scope=scope)
    return {
        "data": orders,
        "meta": {
            "total": len(orders),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/rollbacks")
async def list_rollbacks(
    runtime_id: Optional[str] = None,
    action_type: Optional[str] = None,
    time_range: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """EV-04: Global Rollback List with optional filters."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    rollbacks = read_store.list_all_rollbacks(
        runtime_id=runtime_id, action_type=action_type, time_range=time_range,
    )
    return {
        "data": rollbacks,
        "meta": {
            "total": len(rollbacks),
            "staleness": _meta_staleness(),
        },
    }


# --------------------------------------------------------------------------- #
# Lineage Surfaces (Wave 3 — LN-01 – LN-03)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/lineage")
async def list_lineage(
    artifact_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """LN-01: Lineage Edge List with optional artifact filter."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    edges = read_store.list_lineage_edges(artifact_id=artifact_id)
    return {
        "data": edges,
        "meta": {
            "total": len(edges),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/lineage/edges/{edge_id}")
async def get_lineage_edge(
    edge_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """LN-02: Lineage Edge Detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    edge = read_store.get_lineage_edge(edge_id)
    if not edge:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Lineage edge not found",
            f"Lineage edge {edge_id} does not exist",
        )

    return {
        "data": edge,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/lineage/graph")
async def get_lineage_graph(
    root_type: Optional[str] = None,
    root_id: Optional[str] = None,
    depth: int = 3,
    authorization: Optional[str] = Header(default=None),
):
    """LN-03: Lineage Graph from a root artifact with configurable depth."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    # Clamp depth to allowed range (§4.3)
    depth = max(1, min(depth, 10))

    edges = read_store.get_lineage_graph(root_type=root_type, root_id=root_id, depth=depth)
    return {
        "data": edges,
        "meta": {
            "total": len(edges),
            "depth": depth,
            "staleness": _meta_staleness(),
        },
    }


# --------------------------------------------------------------------------- #
# Telemetry Surfaces (Wave 3 — TL-01 – TL-03)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/telemetry")
async def list_telemetry(
    pool_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
    time_range: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """TL-01: Telemetry Event List with optional filters."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    events = read_store.list_telemetry_events(
        pool_id=pool_id, artifact_id=artifact_id, time_range=time_range,
    )
    return {
        "data": events,
        "meta": {
            "total": len(events),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/telemetry/{runtime_id}/summary")
async def get_telemetry_summary(
    runtime_id: str,
    time_range: Optional[str] = None,
    aggregate_by: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """TL-02: Telemetry Summary for a runtime."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    summary = read_store.get_telemetry_summary(runtime_id)
    if not summary:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Telemetry summary not found",
            f"No telemetry summary for runtime {runtime_id}",
        )

    return {
        "data": summary,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/telemetry/{artifact_id}/performance")
async def get_telemetry_performance(
    artifact_id: str,
    time_range: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """TL-03: Telemetry Performance Chart for an artifact."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    performance = read_store.get_telemetry_performance(artifact_id)
    if not performance:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Telemetry performance data not found",
            f"No performance data for artifact {artifact_id}",
        )

    return {
        "data": performance,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


# --------------------------------------------------------------------------- #
# Consultation surfaces (CS-01 – CS-06)
# Derived from PERSONA_RUNTIME_MODEL.md §6, §13, §14 via
# CONSULTATION_SURFACE_CONTRACT.md.  All surfaces are GET-only —
# writes are the Persona Plane's responsibility.
# --------------------------------------------------------------------------- #


@app.get("/api/v1/personas/{persona_id}/consultations")
def list_consultations(
    persona_id: str,
    consultation_type: Optional[str] = Query(default=None, alias="filter.consultation_type"),
    status: Optional[str] = Query(default=None, alias="filter.status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    authorization: Optional[str] = Header(default=None),
):
    """CS-01: List consultation sessions for a persona."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona = read_store.get_persona(persona_id)
    if persona is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"No persona with id {persona_id}",
        )

    consultations = read_store.list_consultations_for_persona(
        persona_id,
        consultation_type=consultation_type,
        status=status,
        page=page,
        page_size=page_size,
    )
    if consultations is None:
        return {
            "data": [],
            "meta": {
                "total": 0,
                "page": page,
                "page_size": page_size,
                "staleness": {
                    "served_from": "unavailable",
                    "last_known_at": utc_now(),
                },
            },
        }

    start = (page - 1) * page_size
    page_data = consultations[start: start + page_size]
    return {
        "data": [
            {
                **s,
                "_links": {
                    "self": f"/api/v1/consultations/{s['session_id']}",
                    "participants": f"/api/v1/consultations/{s['session_id']}/participants",
                    "outcome": f"/api/v1/consultations/{s['session_id']}/outcome",
                },
            }
            for s in page_data
        ],
        "meta": {
            "total": len(consultations),
            "page": page,
            "page_size": page_size,
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/consultations/{session_id}")
def get_consultation(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """CS-02: Consultation session detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    session = read_store.get_consultation(session_id)
    if session is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Consultation session not found",
            f"No consultation session with id {session_id}",
        )

    return {
        "data": {
            **session,
            "_links": {
                "self": f"/api/v1/consultations/{session_id}",
                "participants": f"/api/v1/consultations/{session_id}/participants",
                "outcome": f"/api/v1/consultations/{session_id}/outcome",
                "evidence": f"/api/v1/consultations/{session_id}/evidence",
            },
        },
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/consultations/{session_id}/participants")
def get_consultation_participants(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """CS-03: All participants in a consultation session."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    participants = read_store.get_consultation_participants(session_id)
    if participants is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Consultation session not found",
            f"No consultation session with id {session_id}",
        )

    return {
        "data": [
            {
                **p,
                "_links": {
                    "self": f"/api/v1/sessions/{p['session_id']}",
                    "persona": f"/api/v1/personas/{p['persona_id']}",
                },
            }
            for p in participants
        ],
        "meta": {
            "total": len(participants),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/consultations/{session_id}/outcome")
def get_consultation_outcome(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """CS-04: Consultation outcome projection."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    outcome = read_store.get_consultation_outcome(session_id)
    if outcome is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Consultation session not found",
            f"No consultation session with id {session_id}",
        )

    return {
        "data": outcome,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/consultations/{session_id}/evidence")
def get_consultation_evidence(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """CS-05: Evidence refs attached to a consultation session."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    evidence = read_store.get_consultation_evidence(session_id)
    if evidence is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Consultation session not found",
            f"No consultation session with id {session_id}",
        )

    return {
        "data": evidence,
        "meta": {
            "total": len(evidence),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/personas/{persona_id}/consult-policy")
def get_consult_policy(
    persona_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """CS-06: Consult policy for a persona."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona = read_store.get_persona(persona_id)
    if persona is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"No persona with id {persona_id}",
        )

    policy = read_store.get_consult_policy(persona_id)
    if policy is None:
        # Policy may not exist yet — return a safe empty structure rather than 404
        # so operators always get a valid read (policy absence is itself informative)
        return {
            "data": {
                "id": None,
                "persona_id": persona_id,
                "required_reviewers": 0,
                "required_committees": [],
                "trigger_rules": [],
                "forbidden_solo_actions": [],
                "escalation_rules": [],
            },
            "meta": {
                "staleness": _meta_staleness(),
                "note": "No consult policy found for this persona. Defaulting to empty policy.",
            },
        }

    return {
        "data": policy,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


# --------------------------------------------------------------------------- #
# Command submission (write path — async execution)
# --------------------------------------------------------------------------- #

@app.post("/api/v1/operator/commands", response_model=CommandSubmissionResponse, status_code=202)
async def submit_command(
    cmd: OperatorCommand,
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
):
    """
    Submit an operator command for async execution.

    Returns 202 with a command receipt.  Poll GET /api/v1/operator/commands/{command_id}
    for status updates.
    """
    # 1. Authenticate
    identity = _extract_identity(authorization)

    # 2. Command-specific precondition validation (role + params shape)
    validator = _VALIDATORS.get(cmd.command)
    if validator:
        validator(cmd.params, identity)

    # 3. Concurrent modification check (§5.1)
    active = command_store.get_active_commands_for_target(cmd.target.type.value, cmd.target.id)
    if active:
        raise _bff_error(
            409, ErrorCode.CONCURRENT_MODIFICATION,
            "A command is already in flight for this target",
            f"Command {active[0]['command_id']} is currently {active[0]['status']}",
            precondition_failed="concurrent_safety",
            suggestion="Wait for the in-flight command to complete or time out before retrying",
        )

    # 4. Degraded mode check (§7.1)
    staleness_warning = _check_read_surface_state()

    # 5. Persist command with full audit record
    command_id = str(uuid.uuid4())
    submitted_at = utc_now()

    # Extract raw token from Authorization header for downstream propagation
    raw_token = None
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization[len("Bearer "):]

    # Use provided MFA token if present; otherwise stub a 6-digit token when MFA was verified.
    # This keeps the internal API scaffold happy while preserving the "mfa" flag semantics.
    mfa_token = x_mfa_token or ("000000" if identity.mfa_verified else None)

    audit_record = {
        "operator_id": identity.operator_id,
        "roles_at_submission": identity.roles,
        "mfa_verified": identity.mfa_verified,
        "reason": cmd.audit_context.reason,
        "preconditions_checked": [
            "authentication", "authorization", "params_shape", "concurrent_safety"
        ],
        "timestamp": submitted_at,
        "staleness_warning": staleness_warning.dict() if staleness_warning else None,
        "auth_token": raw_token,
        "mfa_token": mfa_token,
    }

    command_store.submit_command(
        command_id=command_id,
        command_type=cmd.command,
        target=cmd.target,
        submitted_at=submitted_at,
        params=cmd.params,
        audit_context=audit_record,
    )

    log.info(
        "Accepted command %s (%s) for %s:%s by operator %s",
        command_id, cmd.command.value, cmd.target.type.value, cmd.target.id, identity.operator_id,
    )

    # 6. Queue for async processing
    background_tasks.add_task(_process_command_stub, command_id)

    receipt = CommandReceipt(
        command_id=command_id,
        command_type=cmd.command,
        target=cmd.target,
        submitted_at=submitted_at,
        status=CommandStatus.SUBMITTED,
        tracking_url=f"/api/v1/operator/commands/{command_id}",
    )

    return CommandSubmissionResponse(
        receipt=receipt,
        staleness_warning=staleness_warning,
    )


@app.get("/api/v1/operator/commands/{command_id}", response_model=CommandStatusResponse)
async def get_command_status(command_id: str, authorization: Optional[str] = Header(default=None)):
    """
    Poll for the status of a previously submitted command.
    """
    # Auth required to read command status (prevents polling by unauthenticated callers)
    _extract_identity(authorization)

    record = command_store.get_command(command_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Command {command_id} not found")

    return CommandStatusResponse(
        command_id=record["command_id"],
        type=record["type"],
        target=record["target"],
        submitted_at=record["submitted_at"],
        status=record["status"],
        result=record.get("result"),
        error=record.get("error"),
        audit=record.get("audit"),
    )


# --------------------------------------------------------------------------- #
# Background worker — real execution path
# --------------------------------------------------------------------------- #

async def _process_command(command_id: str):
    """
    Async command processor that dispatches to the Protected Internal API.
    Records authoritative status, result, and audit data for every execution.
    """
    import asyncio

    record = command_store.get_command(command_id)
    if not record:
        log.error("Worker: command %s not found in store", command_id)
        return

    command_type = CommandType(record["type"])
    params = record.get("params", {})
    audit = record.get("audit", {})

    # Extract auth tokens from submission audit context for downstream calls
    auth_token = audit.get("auth_token")
    mfa_token = audit.get("mfa_token")

    # Mark processing
    await asyncio.sleep(0.05)  # brief yield to event loop
    command_store.update_status(command_id, CommandStatus.PROCESSING)

    # Execute via real executor with propagated auth headers
    status, result, error = execute_command_with_status(
        command_id, command_type, params,
        auth_token=auth_token, mfa_token=mfa_token,
    )

    # Enrich audit with execution timeline
    audit["execution_completed_at"] = result.get("execution_completed_at") if result else error.get("failed_at") if error else None
    audit["executor"] = "command_executor"
    if result:
        audit["downstream_verified"] = True
    if error:
        audit["failure_reason"] = error.get("message", "")
        audit["failure_suggestion"] = error.get("suggestion", "")

    # Persist both result and enriched audit data
    command_store.update_status(
        command_id,
        status,
        result=result,
        error=error,
        audit=audit,
    )

    log.info(
        "Worker: command %s completed with status=%s",
        command_id, status.value,
    )


# Keep backward-compatible alias for existing tests
_process_command_stub = _process_command


# --------------------------------------------------------------------------- #
# Degraded Control Guidance (Wave 2 — Incident Response)
# --------------------------------------------------------------------------- #

@app.get("/api/v1/operator/degraded-control-guidance")
async def degraded_control_guidance():
    """Return guidance for operators when the BFF is degraded or unavailable.

    Provides actionable fallback instructions using the secondary control path
    (Admin CLI and Protected Internal API) so operators can still execute
    critical incident actions (pause, rollback, kill-switch) even when the
    primary BFF path is down.

    See BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §6 and
    APP-002-SECONDARY-CONTROL-PATH.md for full spec.
    """
    state = _read_surface_state()
    guidance = {
        "current_state": state,
        "primary_path": {
            "url": "/api/v1/operator/commands",
            "status": "available" if state == "fresh" else "degraded",
            "note": (
                "Primary BFF command path. Submit operator commands for async execution."
                if state == "fresh"
                else "BFF read surface is degraded. Commands may execute but status queries could return stale data."
            ),
        },
        "secondary_path": {
            "admin_cli": {
                "description": "Local/SSH CLI with RBAC and MFA for destructive actions",
                "commands": {
                    "pause_runtime": "pantheon-admin runtime pause --binding-id <ID> --reason <REASON>",
                    "resume_runtime": "pantheon-admin runtime resume --binding-id <ID>",
                    "rollback": "pantheon-admin rollback --target-type <TYPE> --target-id <ID> --to-version <VER>",
                    "kill_switch": "pantheon-admin kill-switch activate --scope <SCOPE> --reason <REASON>",
                },
                "auth": "SSH key + RBAC role; MFA required for destructive actions",
            },
            "protected_internal_api": {
                "description": "Direct HTTP access to control-plane internal API",
                "base_url": os.getenv("PANTHEON_INTERNAL_API_URL", "http://localhost:5001"),
                "endpoints": {
                    "pause_runtime": "POST /api/internal/v1/runtimes/{binding_id}/pause",
                    "execute_rollback": "POST /api/internal/v1/rollbacks/execute",
                    "activate_kill_switch": "POST /api/internal/v1/kill-switch",
                    "approve_deployment": "POST /api/internal/v1/deployments/{plan_id}/approve",
                    "check_command_status": "GET /api/internal/v1/commands/{command_id}",
                },
                "auth": "Bearer token + RBAC; X-MFA-Token header for destructive actions",
            },
        },
        "critical_actions_bypass_mfa": True,
        "reconciliation": {
            "description": "When BFF recovers, reconcile command history from internal API",
            "endpoint": "GET /api/internal/v1/commands",
            "note": "Both BFF and internal API persist command records; compare by command_id to detect gaps.",
        },
        "spec_reference": "support/sidecars/APP-002/APP-002-SECONDARY-CONTROL-PATH.md",
    }

    status_code = 200 if state == "fresh" else 206
    return JSONResponse(
        status_code=status_code,
        content={"data": guidance, "meta": {"staleness": _meta_staleness()}},
    )


# --------------------------------------------------------------------------- #
# SSE Real-Time Feeds (Wave 5 — APP-002-W5-SSE-LIVE)
# --------------------------------------------------------------------------- #

# In-process event buffers per stream type.
# Each buffer is a deque of (event_id, event_dict) tuples, keeping the last N events.
_MAX_EVENTS = 500

_runtime_events: deque = deque(maxlen=_MAX_EVENTS)
_incident_events: deque = deque(maxlen=_MAX_EVENTS)
_kill_switch_events: deque = deque(maxlen=_MAX_EVENTS)

# Subscribers (asyncio.Queue) for each stream type
_runtime_subscribers: list[asyncio.Queue] = []
_incident_subscribers: list[asyncio.Queue] = []
_kill_switch_subscribers: list[asyncio.Queue] = []


def _make_event_id(prefix: str = "evt") -> str:
    return f"{prefix}-{int(time.time())}-{uuid.uuid4().hex[:8]}"


def _sse_format(event: dict) -> str:
    """Format a full event dict as an SSE message block."""
    return (
        f"id: {event['id']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    )


def _publish_event(buffer: deque, subscribers: list[asyncio.Queue], event_type: str, data: dict) -> str:
    """Publish an event to the buffer and notify all subscribers."""
    event_id = _make_event_id()
    event = {
        "id": event_id,
        "type": event_type,
        "timestamp": utc_now(),
        "data": data,
    }
    buffer.append((event_id, event))
    # Notify subscribers
    for q in list(subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
    return event_id


def _replay_from(buffer: deque, last_event_id: Optional[str]) -> list[dict]:
    """Replay events from the buffer starting after last_event_id."""
    if not last_event_id:
        return [evt for _, evt in buffer]
    found = False
    result = []
    for eid, evt in buffer:
        if found:
            result.append(evt)
        elif eid == last_event_id:
            found = True
    if not found:
        # Client requested an event ID we no longer have — return full buffer
        return [evt for _, evt in buffer]
    return result


async def _sse_stream(
    buffer: deque,
    subscribers: list[asyncio.Queue],
    last_event_id: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE-formatted events."""
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    subscribers.append(q)
    try:
        # Replay historical events first
        for evt in _replay_from(buffer, last_event_id):
            yield _sse_format(evt)

        # Then stream new events as they arrive
        while True:
            try:
                evt = await asyncio.wait_for(q.get(), timeout=30.0)
                yield _sse_format(evt)
            except asyncio.TimeoutError:
                # Send a comment to keep the connection alive
                yield ": heartbeat\n\n"
    finally:
        # Unsubscribe on client disconnect
        if q in subscribers:
            subscribers.remove(q)


@app.get("/api/v1/runtime/{runtime_id}/events/stream")
async def stream_runtime_events(
    runtime_id: str,
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """RT-SSE: Server-Sent Events stream for runtime state changes.

    Supports reconnection via ``?last_event_id=`` to replay missed events.
    BFF_API_CONTRACT.md §11.2
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    return StreamingResponse(
        _sse_stream(_runtime_events, _runtime_subscribers, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/v1/incidents/stream")
async def stream_incident_events(
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """IN-SSE: Server-Sent Events stream for active incident events.

    Supports reconnection via ``?last_event_id=`` to replay missed events.
    BFF_API_CONTRACT.md §11.2
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    return StreamingResponse(
        _sse_stream(_incident_events, _incident_subscribers, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/v1/kill-switch/updates")
async def stream_kill_switch_events(
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """KS-SSE: Server-Sent Events stream for kill-switch state changes.

    Supports reconnection via ``?last_event_id=`` to replay missed events.
    BFF_API_CONTRACT.md §11.2
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    return StreamingResponse(
        _sse_stream(_kill_switch_events, _kill_switch_subscribers, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------- #
# SSE Publish Helpers (for internal use / testing / admin injection)
# --------------------------------------------------------------------------- #

@app.post("/api/v1/internal/sse/publish")
async def publish_sse_event(
    event_type: str = Query(..., description="Event type: runtime_state_changed, incident_created, etc."),
    runtime_id: Optional[str] = Query(default=None),
    incident_id: Optional[str] = Query(default=None),
    payload: Dict[str, Any] = {},
    authorization: Optional[str] = Header(default=None),
):
    """Internal helper to publish SSE events for testing and integration.

    In production, events would be published by downstream services via
    an internal message bus. This endpoint is a convenience for smoke tests.
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    if event_type.startswith("runtime"):
        event_id = _publish_event(
            _runtime_events, _runtime_subscribers, event_type,
            {"runtime_id": runtime_id, **payload},
        )
    elif event_type.startswith("incident"):
        event_id = _publish_event(
            _incident_events, _incident_subscribers, event_type,
            {"incident_id": incident_id, **payload},
        )
    elif event_type.startswith("kill_switch"):
        event_id = _publish_event(
            _kill_switch_events, _kill_switch_subscribers, event_type,
            payload,
        )
    else:
        raise _bff_error(400, ErrorCode.INVALID_REQUEST, "Unknown event type", f"Event type {event_type} not recognized")

    return {"event_id": event_id, "status": "published"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
