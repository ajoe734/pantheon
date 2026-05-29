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

_REQUEST_TIMEOUT = int(os.getenv("PANTHEON_COMMAND_TIMEOUT_SECONDS", "30"))
_RUNTIME_MANAGER_CLIENT = None


def _configured_base_url(primary_env: str, *fallback_envs: str) -> str:
    for env_name in (primary_env, *fallback_envs):
        value = os.getenv(env_name, "").strip()
        if value:
            return value.rstrip("/")
    raise RuntimeError(
        f"Command backend is unconfigured: set {primary_env}"
        + (
            " or one of " + ", ".join(fallback_envs)
            if fallback_envs
            else ""
        )
        + "."
    )


def _internal_url(path: str) -> str:
    base = _configured_base_url("PANTHEON_INTERNAL_API_URL")
    return f"{base}{path}"


def _governance_url(path: str) -> str:
    base = _configured_base_url(
        "PANTHEON_GOVERNANCE_API_URL",
        "PANTHEON_EVOLUTION_API_URL",
    )
    return f"{base}{path}"


def _runtime_manager_client():
    global _RUNTIME_MANAGER_CLIENT
    if _RUNTIME_MANAGER_CLIENT is not None:
        return _RUNTIME_MANAGER_CLIENT

    import importlib.util

    module_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "runtime-manager", "runtime_manager_client.py")
    )
    spec = importlib.util.spec_from_file_location("pantheon_runtime_manager_client", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load runtime_manager_client from {module_path!r}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _RUNTIME_MANAGER_CLIENT = mod.RuntimeManagerClient()
    return _RUNTIME_MANAGER_CLIENT


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _actor_context(
    params: Dict[str, Any],
    auth_token: Optional[str] = None,
) -> tuple[str, str]:
    """Resolve actor_id/actor_role for governance-owned evolution commands."""
    token_actor_id: Optional[str] = None
    token_roles: list[str] = []
    if auth_token:
        token_parts = auth_token.split(":")
        if token_parts:
            raw_actor_id = token_parts[0].strip()
            token_actor_id = raw_actor_id or None
        if len(token_parts) > 1:
            token_roles = [role.strip() for role in token_parts[1].split(",") if role.strip()]

    actor_id = (
        params.get("approved_by_id")
        or params.get("actor_id")
        or token_actor_id
    )
    actor_role = params.get("approved_by_role") or params.get("actor_role")
    if not actor_role:
        for preferred_role in ("admin", "approver", "reviewer", "operator"):
            if preferred_role in token_roles:
                actor_role = preferred_role
                break

    if not actor_id:
        raise ValueError("Evolution command requires actor_id or an authenticated operator token.")
    if not actor_role:
        raise ValueError("Evolution command requires actor_role/approved_by_role or a role-bearing operator token.")
    return str(actor_id), str(actor_role)


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


def _execute_approve_decision(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch ApproveDecision to the approval-decision authority endpoint."""
    decision_id = str(params.get("decision_id") or "").strip()
    if not decision_id:
        raise ValueError("ApproveDecision requires decision_id.")
    payload = {
        "approval_notes": params.get("approval_notes"),
    }
    url = _internal_url(f"/api/internal/v1/approval-decisions/{decision_id}/approve")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    return {
        "command_id": command_id,
        "decision_id": body.get("decision_id", decision_id),
        "decision_state": body.get("decision_state", "approved"),
        "status": body.get("status"),
        "audit_id": body.get("audit_id"),
        "approved_at": body.get("approved_at"),
    }


def _execute_reject_decision(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch RejectDecision to the approval-decision authority endpoint."""
    decision_id = str(params.get("decision_id") or "").strip()
    if not decision_id:
        raise ValueError("RejectDecision requires decision_id.")
    payload = {
        "rejection_reason": params.get("rejection_reason"),
    }
    url = _internal_url(f"/api/internal/v1/approval-decisions/{decision_id}/reject")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    return {
        "command_id": command_id,
        "decision_id": body.get("decision_id", decision_id),
        "decision_state": body.get("decision_state", "rejected"),
        "status": body.get("status"),
        "audit_id": body.get("audit_id"),
        "rejected_at": body.get("rejected_at"),
    }


def _execute_request_approval_revision(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch RequestApprovalRevision to the approval-decision authority endpoint."""
    decision_id = str(params.get("decision_id") or "").strip()
    if not decision_id:
        raise ValueError("RequestApprovalRevision requires decision_id.")
    payload = {
        "revision_notes": params.get("revision_notes"),
    }
    url = _internal_url(f"/api/internal/v1/approval-decisions/{decision_id}/request-revision")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    return {
        "command_id": command_id,
        "decision_id": body.get("decision_id", decision_id),
        "decision_state": body.get("decision_state", "pending_revision"),
        "status": body.get("status"),
        "audit_id": body.get("audit_id"),
        "requested_at": body.get("requested_at"),
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
    if "pause_new_entries" in params:
        payload["pause_new_entries"] = params.get("pause_new_entries")
    if "cancel_open_orders" in params:
        payload["cancel_open_orders"] = params.get("cancel_open_orders")
    url = _internal_url(f"/api/internal/v1/runtimes/{binding_id}/pause")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    return {
        "command_id": command_id,
        "runtime_id": params.get("runtime_id"),
        "runtime_binding_id": body.get("runtime_binding_id"),
        "pause_action": body.get("pause_action", pause_action),
        "pause_expires_at": body.get("pause_expires_at"),
        "status": body.get("status"),
        "status_after": body.get("status_after"),
        "duration_seconds": body.get("duration_seconds"),
        "reason": body.get("reason"),
        "pause_new_entries": params.get("pause_new_entries"),
        "cancel_open_orders": params.get("cancel_open_orders"),
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
    if "rollback_action_type" in params:
        payload["rollback_action_type"] = params.get("rollback_action_type")
    if "target_artifact_id" in params:
        payload["target_artifact_id"] = params.get("target_artifact_id")
    url = _internal_url("/api/internal/v1/rollbacks/execute")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    return {
        "rollback_id": body.get("rollback_id"),
        "command_id": command_id,
        "runtime_id": params.get("runtime_id"),
        "runtime_binding_id": params.get("runtime_binding_id") or params.get("target_id"),
        "target_artifact_id": params.get("target_artifact_id"),
        "rollback_action_type": params.get("rollback_action_type"),
        "status": body.get("status"),
        "tracking_url": body.get("tracking_url"),
    }


def _execute_approve_rollback(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch ApproveRollback to the rollback authority endpoint."""
    rollback_id = str(params.get("rollback_id") or "").strip()
    if not rollback_id:
        raise ValueError("ApproveRollback requires rollback_id.")
    payload = {
        "approval_notes": params.get("approval_notes"),
    }
    url = _internal_url(f"/api/internal/v1/rollbacks/{rollback_id}/approve")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    return {
        "command_id": command_id,
        "rollback_id": body.get("rollback_id", rollback_id),
        "decision": body.get("decision", "approved"),
        "status": body.get("status"),
        "audit_id": body.get("audit_id"),
        "approved_at": body.get("approved_at"),
    }


def _execute_reject_rollback(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch RejectRollback to the rollback authority endpoint."""
    rollback_id = str(params.get("rollback_id") or "").strip()
    if not rollback_id:
        raise ValueError("RejectRollback requires rollback_id.")
    payload = {
        "rejection_reason": params.get("rejection_reason"),
    }
    url = _internal_url(f"/api/internal/v1/rollbacks/{rollback_id}/reject")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    return {
        "command_id": command_id,
        "rollback_id": body.get("rollback_id", rollback_id),
        "decision": body.get("decision", "rejected"),
        "status": body.get("status"),
        "audit_id": body.get("audit_id"),
        "rejected_at": body.get("rejected_at"),
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
        "reason": params.get("trigger_reason") or params.get("reason", "operator_emergency_stop"),
    }
    if "action_override" in params:
        payload["action_override"] = params.get("action_override")
    url = _internal_url("/api/internal/v1/kill-switch")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    return {
        "kill_switch_order_id": body.get("kill_switch_order_id"),
        "command_id": command_id,
        "runtime_id": params.get("runtime_id"),
        "runtime_binding_id": params.get("runtime_binding_id"),
        "capital_pool_id": params.get("capital_pool_id") or params.get("scope_id"),
        "reduce_exposure_pct": params.get("reduce_exposure_pct"),
        "action": body.get("action"),
        "scope": body.get("scope"),
        "status": body.get("status"),
        "safe_mode_after": body.get("safe_mode_after"),
        "audit_id": body.get("audit_id"),
    }


def _execute_escalate_diff(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch EscalateDiff to the deployment-plan governance endpoint."""
    plan_id = str(params.get("plan_id") or "").strip()
    if not plan_id:
        raise ValueError("EscalateDiff requires plan_id.")
    payload = {
        "escalation_reason": params.get("escalation_reason"),
    }
    url = _internal_url(f"/api/internal/v1/deployment-plans/{plan_id}/escalate-diff")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    return {
        "command_id": command_id,
        "plan_id": body.get("plan_id", plan_id),
        "status": body.get("status"),
        "audit_id": body.get("audit_id"),
        "escalated_at": body.get("escalated_at"),
    }


def _execute_issue_safe_mode(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Advance runtime-manager safe mode for IssueSafeMode drawer commands."""
    del auth_token, mfa_token

    capital_pool_id = str(params.get("capital_pool_id") or "").strip()
    if not capital_pool_id:
        raise ValueError("IssueSafeMode requires capital_pool_id.")

    target_state = str(params.get("target_state") or "guarded").strip()
    actor_id = str(params.get("actor_id") or "operator-command").strip()
    client = _runtime_manager_client()
    body = client.advance_safe_mode(
        capital_pool_id,
        target_state,
        actor_id=actor_id,
        note=params.get("reason"),
    )
    return {
        "command_id": command_id,
        "runtime_id": params.get("runtime_id"),
        "capital_pool_id": capital_pool_id,
        "safe_mode_level": params.get("safe_mode_level"),
        "safe_mode_after": body.get("safe_mode_state"),
        "actor_id": actor_id,
        "status": "executed",
    }


def _execute_approve_evolution_decision(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch ApproveEvolutionDecision to the governance-owned evolution API."""
    decision_id = str(params.get("evolution_decision_id") or "").strip()
    approval_action = str(params.get("approval_action") or "").strip().lower()
    if not decision_id:
        raise ValueError("ApproveEvolutionDecision requires evolution_decision_id.")
    if approval_action not in {"approve", "reject"}:
        raise ValueError("ApproveEvolutionDecision requires approval_action=approve|reject.")

    actor_id, actor_role = _actor_context(params, auth_token=auth_token)
    payload: Dict[str, Any] = {
        "actor_id": actor_id,
        "actor_role": actor_role,
    }
    approval_decision_id = params.get("approval_decision_id")
    if approval_decision_id:
        payload["approval_decision_id"] = approval_decision_id
    note = (
        params.get("approval_rationale")
        or params.get("rationale")
        or params.get("note")
    )
    if note:
        payload["note"] = note

    url = _governance_url(f"/api/evolution/proposals/{decision_id}/{approval_action}")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    return {
        "command_id": command_id,
        "evolution_decision_id": body.get("decision_id", decision_id),
        "approval_action": approval_action,
        "decision_state": body.get("decision_state"),
        "approval_decision_id": body.get("approval_decision_id"),
        "risk_level": body.get("risk_level"),
    }


def _execute_evolution_action(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch ExecuteEvolutionAction to the governance-owned evolution API."""
    decision_id = str(params.get("evolution_decision_id") or "").strip()
    if not decision_id:
        raise ValueError("ExecuteEvolutionAction requires evolution_decision_id.")

    actor_id, actor_role = _actor_context(params, auth_token=auth_token)
    payload: Dict[str, Any] = {
        "actor_id": actor_id,
        "actor_role": actor_role,
    }
    for optional_key in (
        "has_active_runtime",
        "active_binding_id",
        "freeze_mode",
        "rollback_action_type",
        "fallback_artifact_id",
        "fallback_artifact_version",
        "force_stage_freeze",
    ):
        if optional_key in params:
            payload[optional_key] = params.get(optional_key)
    note = params.get("note") or params.get("rationale")
    if note:
        payload["note"] = note

    url = _governance_url(f"/api/evolution/proposals/{decision_id}/execute")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    execution_result = body.get("execution_result") or {}
    return {
        "command_id": command_id,
        "evolution_decision_id": body.get("decision_id", decision_id),
        "action_type": body.get("action_type") or params.get("action_type"),
        "decision_state": body.get("decision_state"),
        "execution_result": execution_result,
        "execution_ref_id": execution_result.get("execution_ref_id"),
        "cooldown_ends_at": body.get("cooldown_ends_at"),
        "observation_window_ends_at": body.get("observation_window_ends_at"),
    }


def _execute_approve_mutation(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch ApproveMutation to the governance-owned evolution API."""
    decision_id = str(params.get("decision_id") or params.get("evolution_decision_id") or "").strip()
    if not decision_id:
        raise ValueError("ApproveMutation requires decision_id.")

    actor_id, actor_role = _actor_context(params, auth_token=auth_token)
    payload: Dict[str, Any] = {
        "actor_id": actor_id,
        "actor_role": actor_role,
    }
    approval_decision_id = params.get("approval_decision_id")
    if approval_decision_id:
        payload["approval_decision_id"] = approval_decision_id
    note = params.get("note") or params.get("rationale")
    if note:
        payload["note"] = note

    url = _governance_url(f"/api/evolution/proposals/{decision_id}/approve")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    committed_at = body.get("updated_at") or body.get("decided_at") or _utc_now()
    return {
        "command_id": command_id,
        "command_accepted": True,
        "decision_id": body.get("decision_id", decision_id),
        "new_state": body.get("decision_state", "approved"),
        "decision_state": body.get("decision_state", "approved"),
        "approval_decision_id": body.get("approval_decision_id"),
        "risk_level": body.get("risk_level"),
        "committed_at": committed_at,
    }


def _execute_reject_mutation(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch RejectMutation to the governance-owned evolution API."""
    decision_id = str(params.get("decision_id") or params.get("evolution_decision_id") or "").strip()
    if not decision_id:
        raise ValueError("RejectMutation requires decision_id.")

    actor_id, actor_role = _actor_context(params, auth_token=auth_token)
    payload: Dict[str, Any] = {
        "actor_id": actor_id,
        "actor_role": actor_role,
    }
    approval_decision_id = params.get("approval_decision_id")
    if approval_decision_id:
        payload["approval_decision_id"] = approval_decision_id
    note = params.get("note") or params.get("rationale")
    if note:
        payload["note"] = note

    url = _governance_url(f"/api/evolution/proposals/{decision_id}/reject")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    committed_at = body.get("updated_at") or body.get("decided_at") or _utc_now()
    return {
        "command_id": command_id,
        "command_accepted": True,
        "decision_id": body.get("decision_id", decision_id),
        "new_state": body.get("decision_state", "rejected"),
        "decision_state": body.get("decision_state", "rejected"),
        "approval_decision_id": body.get("approval_decision_id"),
        "risk_level": body.get("risk_level"),
        "committed_at": committed_at,
    }


def _execute_remediate_sentinel_intervention(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch RemediateSentinelIntervention to the sentinel remediation endpoint.

    Two-man authorization must have been validated by the BFF precondition layer
    before this executor is called.
    """
    intervention_id = str(params.get("intervention_id") or "").strip()
    if not intervention_id:
        raise ValueError("RemediateSentinelIntervention requires intervention_id.")
    two_man_signature_id = (
        params.get("twoManSignatureId")
        or params.get("two_man_signature_id")
        or params.get("twoManApprovalId")
        or params.get("two_man_approval_id")
        or params.get("secondOperatorId")
        or params.get("second_operator_id")
        or ""
    )
    payload: Dict[str, Any] = {
        "intervention_id": intervention_id,
        "remediation_action": params.get("remediation_action", "resolve"),
        "two_man_signature_id": two_man_signature_id,
        "operator_note": params.get("operator_note") or params.get("reason") or "",
    }
    url = _internal_url(f"/api/internal/v1/sentinel/interventions/{intervention_id}/remediate")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    return {
        "command_id": command_id,
        "intervention_id": body.get("intervention_id", intervention_id),
        "status": body.get("status"),
        "remediated_at": body.get("remediated_at"),
        "two_man_signature_id": body.get("two_man_signature_id", two_man_signature_id),
    }


_PERSONA_LIFECYCLE_TRANSITIONS: dict[str, list[str]] = {
    "draft": ["paper_owner", "retired"],
    "paper_owner": ["live_owner", "retired"],
    "live_owner": ["retired"],
    "retired": [],
}


def _execute_advance_lifecycle(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch AdvanceLifecycle to internal API /personas/{id}/advance-lifecycle.

    State machine: draft → paper_owner → live_owner → retired.
    No skip transitions; retire allowed from any non-retired state.
    """
    persona_id = str(params.get("persona_id") or params.get("entity_id") or "").strip()
    if not persona_id:
        raise ValueError("AdvanceLifecycle requires persona_id.")

    target_state = str(params.get("target_state") or "").strip()
    allowed_targets = {"paper_owner", "live_owner", "retired"}
    if target_state not in allowed_targets:
        raise ValueError(
            f"AdvanceLifecycle: target_state must be one of {sorted(allowed_targets)}, got {target_state!r}."
        )

    confirm_token = str(params.get("confirm_token") or "").strip()
    if not confirm_token:
        raise ValueError("AdvanceLifecycle requires confirm_token.")

    payload: Dict[str, Any] = {
        "target_state": target_state,
        "confirm_token": confirm_token,
    }
    if params.get("memo"):
        payload["memo"] = str(params["memo"])

    url = _internal_url(f"/api/internal/v1/personas/{persona_id}/advance-lifecycle")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    return {
        "command_id": command_id,
        "status": "accepted",
        "persona_id": body.get("persona_id", persona_id),
        "from_state": body.get("from_state"),
        "to_state": body.get("to_state", target_state),
        "audit_id": body.get("audit_id"),
        "advanced_at": body.get("advanced_at"),
    }


def _execute_approve_pool(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch ApprovePool to internal API /capital-pools/{id}/approve."""
    pool_id = str(params.get("pool_id") or params.get("entity_id") or "").strip()
    if not pool_id:
        raise ValueError("ApprovePool requires pool_id.")

    memo = str(params.get("memo") or "").strip()
    if len(memo) < 8:
        raise ValueError("ApprovePool requires memo of at least 8 characters.")

    payload: Dict[str, Any] = {"memo": memo}
    if params.get("confirm_token"):
        payload["confirm_token"] = str(params["confirm_token"])

    url = _internal_url(f"/api/internal/v1/capital-pools/{pool_id}/approve")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    return {
        "command_id": command_id,
        "status": "accepted",
        "pool_id": body.get("pool_id", pool_id),
        "state": body.get("state", "approved"),
        "audit_id": body.get("audit_id"),
        "approved_at": body.get("approved_at"),
    }


def _execute_start_runtime(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch StartRuntime to internal API /runtimes/<runtime_id>/start.

    Card P0-3 (BFF-WRITE-P0-LIFECYCLE-003): stopped → starting → running.
    Two-man authorization must have been validated by the BFF precondition
    layer before this executor is called for live runtimes.
    EvidenceKind: runtime.start  SSE: runtimes:{id}, management.runtime-status
    """
    runtime_id = str(params.get("runtime_id") or "").strip()
    if not runtime_id:
        raise ValueError("StartRuntime requires runtime_id.")
    confirm_token = str(params.get("confirm_token") or "").strip()
    if not confirm_token:
        raise ValueError("StartRuntime requires confirm_token.")

    two_man_token = (
        params.get("two_man_token")
        or params.get("twoManToken")
        or params.get("two_man_signature_id")
        or ""
    )
    payload: Dict[str, Any] = {
        "confirm_token": confirm_token,
        "command_id": command_id,
    }
    if two_man_token:
        payload["two_man_token"] = two_man_token

    url = _internal_url(f"/api/internal/v1/runtimes/{runtime_id}/start")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    return {
        "command_id": command_id,
        "runtime_id": body.get("runtime_id", runtime_id),
        "status": body.get("status", "accepted"),
        "state": body.get("state", "starting"),
        "audit_id": body.get("audit_id"),
        "started_at": body.get("started_at"),
        "two_man_token": two_man_token or None,
    }


def _execute_bff_action_adapter(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Record adapter-only execution for BFF resource action envelopes.

    The generic /bff/actions/* adapter is an admission bridge. It must not
    directly mutate BFF read state or call live broker/runtime side effects.
    Domain-specific authorities can later consume the persisted command record.
    """
    del auth_token, mfa_token
    source_route = params.get("frontend_source_route") or params.get("adapter_source_route")
    two_man_signature_id = (
        params.get("twoManSignatureId")
        or params.get("two_man_signature_id")
        or params.get("twoManApprovalId")
        or params.get("two_man_approval_id")
        or params.get("secondOperatorId")
        or params.get("second_operator_id")
    )
    return {
        "command_id": command_id,
        "dispatch_path": "bff_action_adapter",
        "status": "admitted",
        "action_id": params.get("action_id"),
        "entity_type": params.get("entity_type"),
        "entity_id": params.get("entity_id"),
        "audit_event": params.get("audit_event"),
        "source_route": source_route,
        "two_man_signature_id": two_man_signature_id,
        "deprecated_action_receipt": (
            params.get("adapter_source_route")
            == "POST /bff/actions/{entityType}/{entityId}/{actionId}"
        ),
        "live_capital_side_effects": False,
    }


# Dispatch table: CommandType -> execution function
_EXECUTORS = {
    CommandType.ADVANCE_LIFECYCLE: _execute_advance_lifecycle,
    CommandType.APPROVE_POOL: _execute_approve_pool,
    CommandType.START_RUNTIME: _execute_start_runtime,
    CommandType.APPROVE_DEPLOYMENT: _execute_approve_deployment,
    CommandType.APPROVE_DECISION: _execute_approve_decision,
    CommandType.REJECT_DECISION: _execute_reject_decision,
    CommandType.REQUEST_APPROVAL_REVISION: _execute_request_approval_revision,
    CommandType.PAUSE_RUNTIME: _execute_pause_runtime,
    CommandType.PAUSE_EXECUTION: _execute_pause_runtime,
    CommandType.ESCALATE_DIFF: _execute_escalate_diff,
    CommandType.ISSUE_RISK_OFF: _execute_activate_kill_switch,
    CommandType.LIQUIDATE_ALL: _execute_activate_kill_switch,
    CommandType.HARD_ROLLBACK: _execute_rollback,
    CommandType.ISSUE_SAFE_MODE: _execute_issue_safe_mode,
    CommandType.EXECUTE_ROLLBACK: _execute_rollback,
    CommandType.APPROVE_ROLLBACK: _execute_approve_rollback,
    CommandType.REJECT_ROLLBACK: _execute_reject_rollback,
    CommandType.ACTIVATE_KILL_SWITCH: _execute_activate_kill_switch,
    CommandType.APPROVE_EVOLUTION_DECISION: _execute_approve_evolution_decision,
    CommandType.EXECUTE_EVOLUTION_ACTION: _execute_evolution_action,
    CommandType.APPROVE_MUTATION: _execute_approve_mutation,
    CommandType.REJECT_MUTATION: _execute_reject_mutation,
    CommandType.REMEDIATE_SENTINEL_INTERVENTION: _execute_remediate_sentinel_intervention,
    CommandType.CAPITAL_POOL_ACTION: _execute_bff_action_adapter,
    CommandType.RANKING_FORMULA_ACTION: _execute_bff_action_adapter,
    CommandType.REBALANCE_ACTION: _execute_bff_action_adapter,
    CommandType.RANKING_ACTION: _execute_bff_action_adapter,
    CommandType.STRATEGY_ACTION: _execute_bff_action_adapter,
    CommandType.PERSONA_ACTION: _execute_bff_action_adapter,
    CommandType.TOOL_ACTION: _execute_bff_action_adapter,
    CommandType.MCP_SERVER_ACTION: _execute_bff_action_adapter,
    CommandType.SKILL_ACTION: _execute_bff_action_adapter,
    CommandType.REVIEW_ACTION: _execute_bff_action_adapter,
    CommandType.DEPLOYMENT_ACTION: _execute_bff_action_adapter,
    CommandType.RUNTIME_ACTION: _execute_bff_action_adapter,
    CommandType.RISK_ALERT_ACTION: _execute_bff_action_adapter,
    CommandType.INCIDENT_ACTION: _execute_bff_action_adapter,
    CommandType.EVOLUTION_PROGRAM_ACTION: _execute_bff_action_adapter,
    CommandType.EXPERIMENT_ACTION: _execute_bff_action_adapter,
    CommandType.JOB_ACTION: _execute_bff_action_adapter,
    CommandType.ALERT_ACKNOWLEDGE: _execute_bff_action_adapter,
    CommandType.HUMAN_GATE_APPROVE: _execute_bff_action_adapter,
    CommandType.HUMAN_GATE_REJECT: _execute_bff_action_adapter,
    CommandType.HUMAN_GATE_REQUEST_MORE_EVIDENCE: _execute_bff_action_adapter,
    CommandType.HUMAN_GATE_REVOKE: _execute_bff_action_adapter,
    CommandType.HUMAN_GATE_EXTEND_TTL: _execute_bff_action_adapter,
    CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT: _execute_bff_action_adapter,
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
    except urllib.error.HTTPError as exc:
        error = {
            "code": "DOWNSTREAM_ERROR",
            "message": f"Command backend returned {exc.code} for {command_id}",
            "started_at": started_at,
            "failed_at": _utc_now(),
            "downstream_status": exc.code,
            "suggestion": "Check internal/governance API health and retry if appropriate",
        }
        log.error("Command %s HTTP error: %s", command_id, error["message"])
        return CommandStatus.FAILED, None, error
    except urllib.error.URLError as exc:
        # Covers connection failures, timeouts, SSL errors
        reason = str(getattr(exc, "reason", exc))
        is_timeout = "timed out" in reason.lower() or "timeout" in reason.lower()
        code = "COMMAND_TIMEOUT" if is_timeout else "DOWNSTREAM_UNAVAILABLE"
        status = CommandStatus.TIMEOUT if is_timeout else CommandStatus.FAILED
        error = {
            "code": code,
            "message": f"Command backend unreachable for {command_id}: {reason}",
            "started_at": started_at,
            "failed_at": _utc_now(),
            "suggestion": "Check internal/governance API availability and network connectivity",
        }
        log.error("Command %s URL error: %s", command_id, error["message"])
        return status, None, error
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
    except RuntimeError as exc:
        error = {
            "code": "COMMAND_BACKEND_UNCONFIGURED",
            "message": f"Command backend is not configured for {command_id}: {exc}",
            "started_at": started_at,
            "failed_at": _utc_now(),
            "suggestion": "Configure the internal/governance API URL or use the secondary control path.",
        }
        log.error("Command %s backend configuration error: %s", command_id, error["message"])
        return CommandStatus.FAILED, None, error
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
