"""Real command execution path for operator commands.

Replaces the stub _process_command_stub in main.py with actual execution
that dispatches to the Protected Internal API and records authoritative
status, result, and audit data.
"""
from __future__ import annotations

import json
import http.client
import logging
import os
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

from .models import CommandStatus, CommandType
from .command_adapters import ActionUnavailableError, dispatch_domain_command

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


def _runtime_repair_url(path: str) -> str:
    base = _configured_base_url("PANTHEON_RUNTIME_MANAGER_API_URL", "PANTHEON_INTERNAL_API_URL")
    return f"{base}{path}"


def _governance_url(path: str) -> str:
    base = _configured_base_url(
        "PANTHEON_GOVERNANCE_API_URL",
        "PANTHEON_EVOLUTION_API_URL",
    )
    return f"{base}{path}"


def _governance_approval_url(path: str) -> str:
    base = _configured_base_url(
        "PANTHEON_GOVERNANCE_APPROVAL_API_URL",
        "PANTHEON_GOVERNANCE_SERVICE_URL",
    )
    return f"{base}{path}"


def _write_to_governance(
    path: str,
    payload: Dict[str, Any],
    auth_token: Optional[str] = None,
    mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    url = _governance_approval_url(path)
    return _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)


def _capital_url(path: str) -> str:
    """Resolve the Capital service owner API.

    Rebalance and containment execution must terminate at the Capital service;
    the BFF command store is an audit/receipt surface, not capital authority.
    """
    base = _configured_base_url(
        "PANTHEON_CAPITAL_API_URL",
        "PANTHEON_CAPITAL_SERVICE_URL",
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


def _parse_jwt_payload(token: str) -> Dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) == 3:
            payload_b64 = parts[1]
            padding = len(payload_b64) % 4
            if padding:
                payload_b64 += "=" * (4 - padding)
            import base64
            payload_bytes = base64.b64decode(payload_b64)
            return json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        pass
    return {}


def _extract_actor_id(auth_token: Optional[str]) -> str:
    if not auth_token:
        return "operator-command"
    token = auth_token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if token.startswith("ey") and "." in token:
        jwt_payload = _parse_jwt_payload(token)
        return jwt_payload.get("sub") or jwt_payload.get("actor_id") or "operator-command"
    parts = token.split(":")
    if parts and parts[0].strip():
        return parts[0].strip()
    return "operator-command"


def _actor_context(
    params: Dict[str, Any],
    auth_token: Optional[str] = None,
) -> tuple[str, str]:
    """Resolve actor_id/actor_role for governance-owned evolution commands."""
    token_actor_id: Optional[str] = None
    token_roles: list[str] = []
    token = auth_token.strip() if auth_token else None
    if token and token.lower().startswith("bearer "):
        token = token[7:].strip()
    if token:
        if token.startswith("ey") and "." in token:
            jwt_payload = _parse_jwt_payload(token)
            token_actor_id = jwt_payload.get("sub") or jwt_payload.get("actor_id")
            raw_roles = jwt_payload.get("roles") or jwt_payload.get("role") or []
            if isinstance(raw_roles, str):
                token_roles = [r.strip() for r in raw_roles.split(",") if r.strip()]
            elif isinstance(raw_roles, list):
                token_roles = [str(r) for r in raw_roles]
        else:
            token_parts = token.split(":")
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
        for preferred_role in (
            "governance_committee",
            "risk_owner",
            "governance_reviewer",
            "admin",
            "approver",
            "reviewer",
            "operator",
        ):
            if preferred_role in token_roles:
                actor_role = preferred_role
                break

    if not actor_id:
        raise ValueError("Evolution command requires actor_id or an authenticated operator token.")
    if not actor_role:
        raise ValueError("Evolution command requires actor_role/approved_by_role or a role-bearing operator token.")
    return str(actor_id), str(actor_role)


# The BFF authorizes 'admin' operators to execute evolution mutations
# (_MUTATION_EXECUTION_ROLES / the admin-gated checks in bff/main.py), but
# services/control-plane/governance/evolution_decision.py's EvolutionActorRole
# enum has no 'admin' member — EXECUTION_ROLES only recognizes
# evolution_controller/operator. Sending "admin" straight through used to
# raise an unhandled ValueError inside the evolution service instead of a
# clean 4xx. Map any non-controller user execution role onto "operator" specifically
# for evolution execution payloads.
def _evolution_actor_role(actor_role: str) -> str:
    if actor_role == "evolution_controller":
        return actor_role
    return "operator"


def _record_outcome_for_target(url: str, ok: bool, status_code: int, detail: Optional[str] = None) -> None:
    try:
        from downstream_health_monitor import DownstreamTarget, get_downstream_health_monitor
        monitor = get_downstream_health_monitor()
        if monitor is None:
            return
        registry = monitor._resolve_target_registry()
        matched_target_name = None
        for name, target in registry.items():
            base_url = target.base_url.rstrip("/")
            if url.startswith(base_url):
                matched_target_name = name
                break
        if matched_target_name:
            monitor.record_downstream_outcome(
                target_name=matched_target_name,
                ok=ok,
                status_code=status_code,
                detail=detail,
            )
    except Exception as exc:
        log.debug("failed to record downstream outcome for %s: %s", url, exc)


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
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            status_code = int(resp.status)
            body = json.loads(resp.read().decode("utf-8"))
            _record_outcome_for_target(url, ok=True, status_code=status_code)
            return body
    except urllib.error.HTTPError as exc:
        _record_outcome_for_target(url, ok=False, status_code=int(exc.code), detail=f"HTTP {exc.code}")
        raise
    except Exception as exc:
        _record_outcome_for_target(url, ok=False, status_code=-1, detail=str(exc))
        raise


def _get_json(
    url: str,
    auth_token: Optional[str] = None,
    mfa_token: Optional[str] = None,
) -> Any:
    """GET JSON from an owner API for post-error receipt reconciliation."""
    headers: Dict[str, str] = {"Accept": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    if mfa_token:
        headers["X-MFA-Token"] = mfa_token
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            status_code = int(resp.status)
            body = json.loads(resp.read().decode("utf-8"))
            _record_outcome_for_target(url, ok=True, status_code=status_code)
            return body
    except urllib.error.HTTPError as exc:
        _record_outcome_for_target(url, ok=False, status_code=int(exc.code), detail=f"HTTP {exc.code}")
        raise
    except Exception as exc:
        _record_outcome_for_target(url, ok=False, status_code=-1, detail=str(exc))
        raise


def _owner_post_may_have_committed(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 409 or exc.code >= 500
    return isinstance(
        exc,
        (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.IncompleteRead,
            http.client.RemoteDisconnected,
            http.client.BadStatusLine,
            json.JSONDecodeError,
            UnicodeDecodeError,
            EOFError,
        ),
    )


# The real internal rollback-execute API (services/control-plane/internal/
# internal_api.py::execute_rollback) reports its terminal state as "executed",
# not "completed". The governance canonical status is normalized to
# "completed" for any of these so the same-command replay short-circuit below
# actually recognizes a prior completion instead of re-dispatching the
# rollback action on every retry.
_ROLLBACK_TERMINAL_STATUSES = frozenset({"completed", "executed", "succeeded", "success"})


def _record_matches(record: Dict[str, Any], expected: Dict[str, Any], fields: tuple[str, ...]) -> bool:
    return all(
        field not in expected
        or expected.get(field) is None
        or record.get(field) == expected.get(field)
        for field in fields
    )


_CAPITAL_POOL_SEMANTIC_FIELDS = (
    "pool_id",
    "name",
    "owner_id",
    "owner_type",
    "status",
    "description",
    "currency",
    "budget",
    "risk_policy_ref",
    "single_runtime_enforced",
    "metadata",
)

_CAPITAL_BINDING_SEMANTIC_FIELDS = (
    "binding_id",
    "persona_id",
    "capital_pool_id",
    "capital_sleeve_id",
    "role",
    "allowed_deployment_scope",
    "mandate",
    "budget",
    "effective_from",
    "effective_to",
    "created_by",
    "metadata",
)


def create_capital_pool(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a stable CapitalPool at its owner, reconciling ambiguous outcomes."""
    pool_id = str(payload.get("pool_id") or "").strip()
    if not pool_id:
        raise ValueError("CapitalPool create requires a stable pool_id")
    try:
        body = _post_json(_capital_url("/api/capital-pools"), payload)
    except Exception as exc:
        if not _owner_post_may_have_committed(exc):
            raise
        try:
            body = _get_json(_capital_url(f"/api/capital-pools/{quote(pool_id, safe='')}"))
        except Exception:
            raise exc
        if not _record_matches(body, payload, _CAPITAL_POOL_SEMANTIC_FIELDS):
            raise exc
        body = {**body, "idempotent_replay": True}
    if str(body.get("pool_id") or body.get("id") or "").strip() != pool_id:
        raise RuntimeError("Capital authority returned a pool with the wrong stable identity")
    if not _record_matches(body, payload, _CAPITAL_POOL_SEMANTIC_FIELDS):
        raise RuntimeError("Capital authority returned a pool with mismatched create semantics")
    return body


def create_capital_binding(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a stable PersonaCapitalBinding at its owner with GET reconciliation."""
    binding_id = str(payload.get("binding_id") or "").strip()
    if not binding_id:
        raise ValueError("PersonaCapitalBinding create requires a stable binding_id")
    try:
        body = _post_json(_capital_url("/api/bindings"), payload)
    except Exception as exc:
        if not _owner_post_may_have_committed(exc):
            raise
        try:
            body = _get_json(_capital_url(f"/api/bindings/{quote(binding_id, safe='')}"))
        except Exception:
            raise exc
        if not _record_matches(body, payload, _CAPITAL_BINDING_SEMANTIC_FIELDS):
            raise exc
        body = {**body, "idempotent_replay": True}
    if str(body.get("binding_id") or body.get("id") or "").strip() != binding_id:
        raise RuntimeError("Capital authority returned a binding with the wrong stable identity")
    if not _record_matches(body, payload, _CAPITAL_BINDING_SEMANTIC_FIELDS):
        raise RuntimeError("Capital authority returned a binding with mismatched create semantics")
    return body


def create_capital_rebalance_proposal(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Persist an auditable proposal through the Capital owner boundary."""
    body = _post_json(_capital_url("/api/rebalances"), payload)
    rebalance_id = str(body.get("rebalance_id") or body.get("id") or "").strip()
    if not rebalance_id:
        raise RuntimeError("Capital authority returned a proposal without rebalance_id")
    return body


def _reconcile_rebalance_apply_receipt(
    *,
    rebalance_id: str,
    command_id: str,
    approval_ref: str,
) -> Optional[Dict[str, Any]]:
    receipt = _get_json(
        _capital_url(f"/api/rebalances/receipts/{quote(command_id, safe='')}")
    )
    try:
        return _validate_rebalance_apply_receipt(
            receipt,
            rebalance_id=rebalance_id,
            command_id=command_id,
            approval_ref=approval_ref,
        )
    except RuntimeError:
        return None


def _reconcile_containment_receipt(
    *,
    command_id: str,
    persona_id: str,
    two_man_signature_id: str,
) -> Optional[Dict[str, Any]]:
    receipt = _get_json(
        _capital_url(f"/api/containments/receipts/{quote(command_id, safe='')}")
    )
    try:
        return _validate_containment_receipt(
            receipt,
            command_id=command_id,
            persona_id=persona_id,
            two_man_signature_id=two_man_signature_id,
        )
    except RuntimeError:
        return None


def _validate_rebalance_apply_receipt(
    body: Any,
    *,
    rebalance_id: str,
    command_id: str,
    approval_ref: str,
) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise RuntimeError("Capital authority returned a non-object rebalance receipt")
    if str(body.get("command_id") or "") != command_id:
        raise RuntimeError("Capital authority returned a rebalance receipt for the wrong command")
    if str(body.get("rebalance_id") or "") != rebalance_id:
        raise RuntimeError("Capital authority returned a rebalance receipt for the wrong proposal")
    if str(body.get("approval_ref") or "") != approval_ref:
        raise RuntimeError("Capital authority returned a rebalance receipt for the wrong approval")
    if body.get("authoritative_capital_readback") is not True:
        raise RuntimeError(
            "Capital authority did not confirm authoritative allocation readback"
        )
    if body.get("authoritative_capital_state_applied") is not True:
        raise RuntimeError("Capital authority did not confirm atomic rebalance application")
    return body


def _validate_containment_receipt(
    body: Any,
    *,
    command_id: str,
    persona_id: str,
    two_man_signature_id: str,
) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise RuntimeError("Capital authority returned a non-object containment receipt")
    if str(body.get("command_id") or "") != command_id:
        raise RuntimeError("Capital authority returned a containment receipt for the wrong command")
    if str(body.get("persona_id") or "") != persona_id:
        raise RuntimeError("Capital authority returned a containment receipt for the wrong Persona")
    if str(body.get("two_man_signature_id") or "") != two_man_signature_id:
        raise RuntimeError(
            "Capital authority returned a containment receipt for the wrong two-man signature"
        )
    containment_state = str(
        body.get("containment_state") or body.get("state") or ""
    ).strip()
    if (
        containment_state not in {"frozen", "suspended", "risk_off", "retired"}
        or body.get("authoritative_containment_readback") is not True
        or body.get("authoritative_capital_readback") is not True
        or body.get("authoritative_capital_state_applied") is not True
    ):
        raise RuntimeError("Capital authority did not confirm terminal containment state")
    return body


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
        "status": body.get("status") or body.get("decision_state", "approved"),
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
        "status": body.get("status") or body.get("decision_state", "rejected"),
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
    target_id = params.get("target_id", "unknown")

    # Generate deterministic rollback ID based on command_id
    import hashlib
    h = hashlib.sha256(command_id.encode("utf-8")).hexdigest()[:8]
    rollback_id = f"rb-{target_id}-{h}"

    # Check if this rollback has already been recorded in governance
    existing_gov = None
    try:
        url = _governance_approval_url(f"/api/governance/rollbacks/{rollback_id}")
        existing_gov = _get_json(url, auth_token=auth_token, mfa_token=mfa_token)
    except Exception:
        pass

    if existing_gov and existing_gov.get("status") in _ROLLBACK_TERMINAL_STATUSES:
        return {
            "rollback_id": rollback_id,
            "command_id": command_id,
            "runtime_id": existing_gov.get("runtime_id") or params.get("runtime_id"),
            "runtime_binding_id": existing_gov.get("runtime_binding_id") or params.get("runtime_binding_id") or target_id,
            "target_artifact_id": existing_gov.get("target_artifact_id") or params.get("target_artifact_id"),
            "rollback_action_type": existing_gov.get("action_type") or params.get("rollback_action_type"),
            "status": "completed",
            "tracking_url": f"/api/internal/v1/commands/{command_id}",
        }

    try:
        actor_id, actor_role = _actor_context(params, auth_token=auth_token)
    except Exception:
        actor_id = _extract_actor_id(auth_token)
        actor_role = "operator"

    # The canonical governance record requires runtime_id; callers commonly
    # only supply target_id (see _ROLLBACK_REQUIRED in bff/main.py, which does
    # not list runtime_id). Derive it from the runtime/binding identifiers we
    # already have rather than sending a bare POST that 400s.
    runtime_id = (
        params.get("runtime_id")
        or params.get("runtime_binding_id")
        or params.get("binding_id")
        or target_id
    )

    timestamp = _utc_now()
    gov_payload = {
        "rollback_id": rollback_id,
        "id": rollback_id,
        "runtime_id": runtime_id,
        "runtime_binding_id": params.get("runtime_binding_id") or params.get("target_id") or params.get("binding_id"),
        "action_type": params.get("rollback_action_type") or "replace",
        "status": "initiated",
        "target_artifact_id": params.get("target_artifact_id") or params.get("rollback_to_version"),
        "actor": actor_role,
        "identity": actor_id,
        "initiated_at": timestamp,
        "created_at": timestamp,
        "requested_at": timestamp,
        "source_command_id": command_id,
    }

    # Write initiated to governance first before executing side effects
    if not existing_gov or existing_gov.get("status") != "completed":
        _write_to_governance("/api/governance/rollbacks", gov_payload, auth_token=auth_token, mfa_token=mfa_token)

    payload = {
        "rollback_target_type": params.get("rollback_target_type", "deployment"),
        "target_id": target_id,
        "rollback_to_version": params.get("rollback_to_version", "previous"),
        "rollback_id": rollback_id,
    }
    if "rollback_action_type" in params:
        payload["rollback_action_type"] = params.get("rollback_action_type")
    if "target_artifact_id" in params:
        payload["target_artifact_id"] = params.get("target_artifact_id")

    try:
        url = _internal_url("/api/internal/v1/rollbacks/execute")
        body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    except Exception as exc:
        gov_payload["status"] = "failed"
        try:
            gov_payload["transition_actor"] = actor_role
            gov_payload["transition_identity"] = actor_id
            gov_payload["transition_source_command_id"] = command_id
            _write_to_governance("/api/governance/rollbacks", gov_payload, auth_token=auth_token, mfa_token=mfa_token)
        except Exception:
            pass
        raise exc

    raw_status = body.get("status") or "completed"
    normalized_status = "completed" if raw_status in _ROLLBACK_TERMINAL_STATUSES else raw_status
    gov_payload["status"] = normalized_status
    gov_payload["transition_actor"] = actor_role
    gov_payload["transition_identity"] = actor_id
    gov_payload["transition_source_command_id"] = command_id
    _write_to_governance("/api/governance/rollbacks", gov_payload, auth_token=auth_token, mfa_token=mfa_token)

    return {
        "rollback_id": rollback_id,
        "command_id": command_id,
        "runtime_id": runtime_id,
        "runtime_binding_id": params.get("runtime_binding_id") or params.get("target_id"),
        "target_artifact_id": params.get("target_artifact_id"),
        "rollback_action_type": params.get("rollback_action_type"),
        "status": normalized_status,
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

    try:
        actor_id, actor_role = _actor_context(params, auth_token=auth_token)
    except Exception:
        actor_id = _extract_actor_id(auth_token)
        actor_role = "operator"
    timestamp = _utc_now()
    gov_payload = {
        "rollback_id": rollback_id,
        "id": rollback_id,
        "status": body.get("status") or "approved",
        "actor": actor_role,
        "identity": actor_id,
        "updated_at": timestamp,
        "approved_at": body.get("approved_at") or timestamp,
        "source_command_id": command_id,
        "transition_actor": actor_role,
        "transition_identity": actor_id,
        "transition_source_command_id": command_id,
        "approval_notes": params.get("approval_notes"),
    }
    _write_to_governance("/api/governance/rollbacks", gov_payload, auth_token=auth_token, mfa_token=mfa_token)

    return {
        "command_id": command_id,
        "rollback_id": body.get("rollback_id", rollback_id),
        "decision": body.get("decision", "approved"),
        "status": body.get("status") or "approved",
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

    try:
        actor_id, actor_role = _actor_context(params, auth_token=auth_token)
    except Exception:
        actor_id = _extract_actor_id(auth_token)
        actor_role = "operator"
    timestamp = _utc_now()
    gov_payload = {
        "rollback_id": rollback_id,
        "id": rollback_id,
        "status": body.get("status") or "rejected",
        "actor": actor_role,
        "identity": actor_id,
        "updated_at": timestamp,
        "rejected_at": body.get("rejected_at") or timestamp,
        "source_command_id": command_id,
        "transition_actor": actor_role,
        "transition_identity": actor_id,
        "transition_source_command_id": command_id,
        "rejection_reason": params.get("rejection_reason"),
    }
    _write_to_governance("/api/governance/rollbacks", gov_payload, auth_token=auth_token, mfa_token=mfa_token)

    return {
        "command_id": command_id,
        "rollback_id": body.get("rollback_id", rollback_id),
        "decision": body.get("decision", "rejected"),
        "status": body.get("status") or "rejected",
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

    kill_switch_order_id = body.get("kill_switch_order_id") or f"ks-{uuid.uuid4().hex[:12]}"
    try:
        actor_id, actor_role = _actor_context(params, auth_token=auth_token)
    except Exception:
        actor_id = _extract_actor_id(auth_token)
        actor_role = "operator"
    timestamp = _utc_now()
    freeze_order_id = f"freeze-{kill_switch_order_id}"
    freeze_payload = {
        "freeze_order_id": freeze_order_id,
        "id": freeze_order_id,
        "status": "active",
        "scope": params.get("scope") or "all",
        "target_id": params.get("scope_id") or "all",
        "actor": actor_role,
        "identity": actor_id,
        "created_at": timestamp,
        "issued_at": timestamp,
        "source_command_id": command_id,
        "reason": params.get("trigger_reason") or params.get("reason") or "Kill switch activation freeze.",
    }
    _write_to_governance("/api/governance/freeze-orders", freeze_payload, auth_token=auth_token, mfa_token=mfa_token)

    return {
        "kill_switch_order_id": kill_switch_order_id,
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
        "actor_role": _evolution_actor_role(actor_role),
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

    freeze_mode = params.get("freeze_mode")
    force_stage_freeze = params.get("force_stage_freeze")
    is_real_freeze = freeze_mode and freeze_mode != "governance_only"
    if is_real_freeze or force_stage_freeze:
        timestamp = _utc_now()
        freeze_order_id = f"freeze-{decision_id}"
        freeze_payload = {
            "freeze_order_id": freeze_order_id,
            "id": freeze_order_id,
            "status": "active",
            "scope": str(freeze_mode or "persona"),
            "target_id": body.get("target_id") or params.get("persona_id") or params.get("target_id") or "unknown",
            "actor": actor_role,
            "identity": actor_id,
            "created_at": timestamp,
            "issued_at": timestamp,
            "source_command_id": command_id,
            "reason": params.get("note") or params.get("rationale") or "Evolution action freeze.",
        }
        _write_to_governance("/api/governance/freeze-orders", freeze_payload, auth_token=auth_token, mfa_token=mfa_token)

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


def _execute_review_mutation(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch ReviewMutation to the governance-owned evolution API."""
    decision_id = str(params.get("decision_id") or params.get("evolution_decision_id") or "").strip()
    if not decision_id:
        raise ValueError("ReviewMutation requires decision_id.")
    approval_decision_id = str(params.get("approval_decision_id") or "").strip()
    if not approval_decision_id:
        raise ValueError("ReviewMutation requires approval_decision_id.")

    actor_id, actor_role = _actor_context(params, auth_token=auth_token)
    payload: Dict[str, Any] = {
        "actor_id": actor_id,
        "actor_role": actor_role,
        "approval_decision_id": approval_decision_id,
    }
    note = params.get("note") or params.get("rationale")
    if note:
        payload["note"] = note

    url = _governance_url(f"/api/evolution/proposals/{decision_id}/review")
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    committed_at = body.get("updated_at") or body.get("decided_at") or _utc_now()
    return {
        "command_id": command_id,
        "command_accepted": True,
        "decision_id": body.get("decision_id", decision_id),
        "new_state": body.get("decision_state", "reviewed"),
        "decision_state": body.get("decision_state", "reviewed"),
        "approval_decision_id": body.get("approval_decision_id", approval_decision_id),
        "risk_level": body.get("risk_level"),
        "committed_at": committed_at,
    }


def _execute_execute_mutation(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch ExecuteMutation to the governance-owned evolution API."""
    decision_id = str(params.get("decision_id") or params.get("evolution_decision_id") or "").strip()
    if not decision_id:
        raise ValueError("ExecuteMutation requires decision_id.")

    actor_id, actor_role = _actor_context(params, auth_token=auth_token)
    payload: Dict[str, Any] = {
        "actor_id": actor_id,
        "actor_role": _evolution_actor_role(actor_role),
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
    committed_at = body.get("updated_at") or execution_result.get("executed_at") or _utc_now()

    freeze_mode = params.get("freeze_mode")
    force_stage_freeze = params.get("force_stage_freeze")
    is_real_freeze = freeze_mode and freeze_mode != "governance_only"
    if is_real_freeze or force_stage_freeze:
        timestamp = _utc_now()
        freeze_order_id = f"freeze-{decision_id}"
        freeze_payload = {
            "freeze_order_id": freeze_order_id,
            "id": freeze_order_id,
            "status": "active",
            "scope": str(freeze_mode or "persona"),
            "target_id": body.get("target_id") or params.get("persona_id") or params.get("target_id") or "unknown",
            "actor": actor_role,
            "identity": actor_id,
            "created_at": timestamp,
            "issued_at": timestamp,
            "source_command_id": command_id,
            "reason": params.get("note") or params.get("rationale") or "Evolution sweep freeze.",
        }
        _write_to_governance("/api/governance/freeze-orders", freeze_payload, auth_token=auth_token, mfa_token=mfa_token)

    return {
        "command_id": command_id,
        "command_accepted": True,
        "decision_id": body.get("decision_id", decision_id),
        "new_state": body.get("decision_state", "executed"),
        "decision_state": body.get("decision_state", "executed"),
        "execution_result": execution_result,
        "execution_ref_id": execution_result.get("execution_ref_id"),
        "cooldown_ends_at": body.get("cooldown_ends_at"),
        "observation_window_ends_at": body.get("observation_window_ends_at"),
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


_RUNTIME_REPAIR_ACTION_PATHS: dict[CommandType, tuple[str, tuple[str, ...], str]] = {
    CommandType.RESTART_PAPER_RUNTIME: (
        "/api/internal/v1/runtime-repair/paper-runtimes/{runtime_id}/restart",
        ("runtime_id",),
        "runtime_id",
    ),
    CommandType.RESTART_TELEMETRY_BRIDGE: (
        "/api/internal/v1/runtime-repair/paper-runtimes/{runtime_id}/telemetry-bridge/restart",
        ("runtime_id",),
        "runtime_id",
    ),
    CommandType.TERMINATE_STALE_PAPER_MONITORING_SESSION: (
        "/api/internal/v1/runtime-repair/monitoring-sessions/{session_id}/terminate-stale",
        ("session_id",),
        "session_id",
    ),
    CommandType.START_PAPER_MONITORING_SESSION: (
        "/api/internal/v1/runtime-repair/paper-runtimes/{runtime_id}/monitoring-sessions/start",
        ("runtime_id",),
        "runtime_id",
    ),
    CommandType.PROBE_TELEMETRY_INGEST: (
        "/api/internal/v1/runtime-repair/paper-runtimes/{runtime_id}/telemetry-ingest/probe",
        ("runtime_id",),
        "runtime_id",
    ),
}


def _require_confirm_token(action_id: str, params: Dict[str, Any]) -> str:
    confirm_token = str(params.get("confirm_token") or "").strip()
    if not confirm_token:
        raise ValueError(f"{action_id} requires confirm_token.")
    return confirm_token


def _require_runtime_repair_target(
    action_id: str,
    params: Dict[str, Any],
    required_keys: tuple[str, ...],
) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in required_keys:
        value = str(params.get(key) or "").strip()
        if not value:
            raise ValueError(f"{action_id} requires {key}.")
        values[key] = value
    return values


def _require_staleness_evidence(action_id: str, params: Dict[str, Any]) -> dict[str, Any]:
    evidence = params.get("staleness_evidence")
    if not isinstance(evidence, dict):
        evidence = {
            key: params.get(key)
            for key in (
                "last_heartbeat_at",
                "observed_at",
                "heartbeat_age_seconds",
                "stale_since",
                "session_started_at",
            )
            if params.get(key) is not None
        }
    has_timestamp = bool(evidence.get("last_heartbeat_at") or evidence.get("stale_since"))
    has_age = evidence.get("heartbeat_age_seconds") is not None
    if not has_timestamp and not has_age:
        raise ValueError(
            f"{action_id} requires staleness_evidence with heartbeat age or timestamp."
        )
    return evidence


def _runtime_repair_audit_receipt(
    *,
    command_id: str,
    action_id: str,
    params: Dict[str, Any],
    target_key: str,
    target_id: str,
    body: Dict[str, Any],
) -> dict[str, Any]:
    return {
        "actor_id": params.get("actor_id") or params.get("operator_id"),
        "action_id": action_id,
        "target_key": target_key,
        "target_id": target_id,
        "idempotency_key": params.get("idempotency_key") or params.get("idempotencyKey"),
        "stage": params.get("stage") or "paper",
        "trace_id": params.get("trace_id") or body.get("trace_id"),
        "audit_id": body.get("audit_id"),
        "command_id": command_id,
    }


def _execute_runtime_repair_action(
    command_id: str,
    command_type: CommandType,
    params: Dict[str, Any],
    auth_token: Optional[str] = None,
    mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    action_id = command_type.value
    path_template, required_keys, target_key = _RUNTIME_REPAIR_ACTION_PATHS[command_type]
    confirm_token = _require_confirm_token(action_id, params)
    target_values = _require_runtime_repair_target(action_id, params, required_keys)
    target_id = target_values[target_key]
    evidence = None
    if command_type == CommandType.TERMINATE_STALE_PAPER_MONITORING_SESSION:
        evidence = _require_staleness_evidence(action_id, params)

    payload: Dict[str, Any] = {
        "command_id": command_id,
        "confirm_token": confirm_token,
        "reason": params.get("reason") or "operator_runtime_repair",
        "idempotency_key": params.get("idempotency_key") or params.get("idempotencyKey"),
        "trace_id": params.get("trace_id"),
        "actor_id": params.get("actor_id") or params.get("operator_id"),
        "stage": params.get("stage") or "paper",
    }
    if evidence is not None:
        payload["staleness_evidence"] = evidence

    url = _runtime_repair_url(path_template.format(**target_values))
    body = _post_json(url, payload, auth_token=auth_token, mfa_token=mfa_token)
    audit_receipt = _runtime_repair_audit_receipt(
        command_id=command_id,
        action_id=action_id,
        params=params,
        target_key=target_key,
        target_id=target_id,
        body=body,
    )
    return {
        "command_id": command_id,
        "action_id": action_id,
        "dispatch_path": "runtime_manager_repair_api",
        "status": body.get("status", "accepted"),
        "runtime_id": body.get("runtime_id") or params.get("runtime_id"),
        "session_id": body.get("session_id") or params.get("session_id"),
        "target_id": target_id,
        "audit_id": body.get("audit_id"),
        "audit_receipt": audit_receipt,
        "telemetry_projection": body.get("telemetry_projection"),
        "heartbeat_freshness": body.get("heartbeat_freshness"),
        "success_condition": "heartbeat_freshness",
        "live_broker_side_effects": False,
        "capital_authority_granted": False,
    }


def _execute_restart_paper_runtime(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    return _execute_runtime_repair_action(
        command_id,
        CommandType.RESTART_PAPER_RUNTIME,
        params,
        auth_token=auth_token,
        mfa_token=mfa_token,
    )


def _execute_restart_telemetry_bridge(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    return _execute_runtime_repair_action(
        command_id,
        CommandType.RESTART_TELEMETRY_BRIDGE,
        params,
        auth_token=auth_token,
        mfa_token=mfa_token,
    )


def _execute_terminate_stale_paper_monitoring_session(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    return _execute_runtime_repair_action(
        command_id,
        CommandType.TERMINATE_STALE_PAPER_MONITORING_SESSION,
        params,
        auth_token=auth_token,
        mfa_token=mfa_token,
    )


def _execute_start_paper_monitoring_session(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    return _execute_runtime_repair_action(
        command_id,
        CommandType.START_PAPER_MONITORING_SESSION,
        params,
        auth_token=auth_token,
        mfa_token=mfa_token,
    )


def _execute_probe_telemetry_ingest(
    command_id: str, params: Dict[str, Any],
    auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    return _execute_runtime_repair_action(
        command_id,
        CommandType.PROBE_TELEMETRY_INGEST,
        params,
        auth_token=auth_token,
        mfa_token=mfa_token,
    )


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
    result = {
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
    if params.get("action_id") == "EmergencyContainment":
        from emergency_containment_policy import containment_receipt_fields

        result.update(containment_receipt_fields(params))
    return result


def _execute_approved_rebalance_apply(
    command_id: str,
    params: Dict[str, Any],
    auth_token: Optional[str] = None,
    mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Atomically apply the server-persisted proposal at Capital authority."""
    del auth_token, mfa_token
    entity_id = str(params.get("entity_id") or "").strip()
    requested_rebalance_id = str(params.get("rebalance_id") or "").strip()
    if entity_id and requested_rebalance_id and entity_id != requested_rebalance_id:
        raise ValueError("ApprovedApply rebalance_id does not match trusted target identity")
    if str(params.get("entity_type") or "Rebalance") != "Rebalance":
        raise ValueError("ApprovedApply requires trusted entity_type=Rebalance")
    rebalance_id = entity_id or requested_rebalance_id
    if not rebalance_id:
        raise ValueError("ApprovedApply requires a trusted rebalance_id")
    approval_ref = str(params.get("approval_ref") or "").strip()
    if params.get("approval_required") and not approval_ref:
        raise ValueError("ApprovedApply requires approval_ref")

    payload = {
        "command_id": command_id,
        "idempotency_key": str(params.get("idempotency_key") or command_id),
        "request_hash": str(params.get("request_hash") or ""),
        "approval_ref": approval_ref,
        "actor_id": str(params.get("actor_id") or "operator-bff"),
        "actor_role": str(params.get("actor_role") or "operator"),
        "proposal_version": params.get("proposal_version"),
    }
    try:
        body = _post_json(
            _capital_url(f"/api/rebalances/{quote(rebalance_id, safe='')}/apply"),
            payload,
        )
    except Exception as exc:
        if not _owner_post_may_have_committed(exc):
            raise
        try:
            reconciled = _reconcile_rebalance_apply_receipt(
                rebalance_id=rebalance_id,
                command_id=command_id,
                approval_ref=approval_ref,
            )
        except Exception:
            raise exc
        if reconciled is None:
            raise exc
        body = {**reconciled, "owner_receipt_reconciled": True}
    body = _validate_rebalance_apply_receipt(
        body,
        rebalance_id=rebalance_id,
        command_id=command_id,
        approval_ref=approval_ref,
    )
    return {
        **body,
        "command_id": command_id,
        "dispatch_path": "capital_service_rebalance_authority",
        "status": body.get("status") or "applied",
        "action_id": "apply",
        "entity_type": "Rebalance",
        "entity_id": rebalance_id,
        "approval_ref": approval_ref,
        "live_capital_side_effects": False,
    }


def _execute_emergency_containment_authority(
    command_id: str,
    params: Dict[str, Any],
    auth_token: Optional[str] = None,
    mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist a risk-decreasing containment terminal state at Capital authority."""
    del auth_token, mfa_token
    entity_id = str(params.get("entity_id") or "").strip()
    requested_persona_id = str(params.get("persona_id") or "").strip()
    if entity_id and requested_persona_id and entity_id != requested_persona_id:
        raise ValueError("EmergencyContainment persona_id does not match trusted target identity")
    if str(params.get("entity_type") or "Persona") != "Persona":
        raise ValueError("EmergencyContainment requires trusted entity_type=Persona")
    persona_id = entity_id or requested_persona_id
    if not persona_id:
        raise ValueError("EmergencyContainment requires a trusted Persona identity")
    two_man_signature_id = str(params.get("two_man_signature_id") or "").strip()
    if not two_man_signature_id:
        raise ValueError("EmergencyContainment requires validated two-man evidence")

    # Admission already validates the risk-decreasing-only contract.  Send the
    # admitted fields plus trusted command identity; the owner validates again.
    payload = {
        key: value
        for key, value in params.items()
        if key
        not in {
            "command_id",
            "entity_type",
            "entity_id",
            "action_id",
            "actor_id",
            "actor_role",
        }
    }
    payload.update(
        {
            "command_id": command_id,
            "idempotency_key": str(params.get("idempotency_key") or command_id),
            "request_hash": str(params.get("request_hash") or ""),
            "persona_id": persona_id,
            "two_man_signature_id": two_man_signature_id,
            "entity_type": "Persona",
            "entity_id": persona_id,
            "actor_id": str(params.get("actor_id") or "operator-bff"),
            "actor_role": str(params.get("actor_role") or "operator"),
        }
    )
    try:
        body = _post_json(_capital_url("/api/containments"), payload)
    except Exception as exc:
        if not _owner_post_may_have_committed(exc):
            raise
        try:
            reconciled = _reconcile_containment_receipt(
                command_id=command_id,
                persona_id=persona_id,
                two_man_signature_id=two_man_signature_id,
            )
        except Exception:
            raise exc
        if reconciled is None:
            raise exc
        body = {**reconciled, "owner_receipt_reconciled": True}
    body = _validate_containment_receipt(
        body,
        command_id=command_id,
        persona_id=persona_id,
        two_man_signature_id=two_man_signature_id,
    )
    containment_state = str(
        body.get("containment_state") or body.get("state") or ""
    ).strip()
    return {
        **body,
        "command_id": command_id,
        "dispatch_path": "capital_service_containment_authority",
        "status": body.get("status") or "applied",
        "action_id": "EmergencyContainment",
        "entity_type": "Persona",
        "entity_id": persona_id,
        "containment": True,
        "containment_state": containment_state,
        "risk_direction": "decrease_only",
        "live_capital_side_effects": False,
    }


def _make_adapter_executor(cmd_type: CommandType):
    return lambda cid, p, auth_token=None, mfa_token=None: dispatch_domain_command(
        cid, cmd_type, p, auth_token=auth_token, mfa_token=mfa_token
    )


# Dispatch table: CommandType -> execution function
_EXECUTORS = {
    CommandType.ADVANCE_LIFECYCLE: _execute_advance_lifecycle,
    CommandType.APPROVE_POOL: _execute_approve_pool,
    CommandType.START_RUNTIME: _execute_start_runtime,
    CommandType.RESTART_PAPER_RUNTIME: _execute_restart_paper_runtime,
    CommandType.RESTART_TELEMETRY_BRIDGE: _execute_restart_telemetry_bridge,
    CommandType.TERMINATE_STALE_PAPER_MONITORING_SESSION: _execute_terminate_stale_paper_monitoring_session,
    CommandType.START_PAPER_MONITORING_SESSION: _execute_start_paper_monitoring_session,
    CommandType.PROBE_TELEMETRY_INGEST: _execute_probe_telemetry_ingest,
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
    CommandType.REVIEW_MUTATION: _execute_review_mutation,
    CommandType.EXECUTE_MUTATION: _execute_execute_mutation,
    CommandType.REMEDIATE_SENTINEL_INTERVENTION: _execute_remediate_sentinel_intervention,
    CommandType.CAPITAL_POOL_ACTION: _make_adapter_executor(CommandType.CAPITAL_POOL_ACTION),
    CommandType.RANKING_FORMULA_ACTION: _make_adapter_executor(CommandType.RANKING_FORMULA_ACTION),
    CommandType.REBALANCE_ACTION: _make_adapter_executor(CommandType.REBALANCE_ACTION),
    CommandType.RANKING_ACTION: _make_adapter_executor(CommandType.RANKING_ACTION),
    CommandType.STRATEGY_ACTION: _make_adapter_executor(CommandType.STRATEGY_ACTION),
    CommandType.PERSONA_ACTION: _make_adapter_executor(CommandType.PERSONA_ACTION),
    CommandType.TOOL_ACTION: _make_adapter_executor(CommandType.TOOL_ACTION),
    CommandType.MCP_SERVER_ACTION: _make_adapter_executor(CommandType.MCP_SERVER_ACTION),
    CommandType.SKILL_ACTION: _make_adapter_executor(CommandType.SKILL_ACTION),
    CommandType.REVIEW_ACTION: _make_adapter_executor(CommandType.REVIEW_ACTION),
    CommandType.DEPLOYMENT_ACTION: _make_adapter_executor(CommandType.DEPLOYMENT_ACTION),
    CommandType.RUNTIME_ACTION: _make_adapter_executor(CommandType.RUNTIME_ACTION),
    CommandType.RISK_ALERT_ACTION: _make_adapter_executor(CommandType.RISK_ALERT_ACTION),
    CommandType.INCIDENT_ACTION: _make_adapter_executor(CommandType.INCIDENT_ACTION),
    CommandType.EVOLUTION_PROGRAM_ACTION: _make_adapter_executor(CommandType.EVOLUTION_PROGRAM_ACTION),
    CommandType.EXPERIMENT_ACTION: _make_adapter_executor(CommandType.EXPERIMENT_ACTION),
    CommandType.JOB_ACTION: _make_adapter_executor(CommandType.JOB_ACTION),
    CommandType.AUDIT_EXPORT: _make_adapter_executor(CommandType.AUDIT_EXPORT),
    CommandType.ALERT_ACKNOWLEDGE: _make_adapter_executor(CommandType.ALERT_ACKNOWLEDGE),
    CommandType.HUMAN_GATE_APPROVE: _make_adapter_executor(CommandType.HUMAN_GATE_APPROVE),
    CommandType.HUMAN_GATE_REJECT: _make_adapter_executor(CommandType.HUMAN_GATE_REJECT),
    CommandType.HUMAN_GATE_REQUEST_MORE_EVIDENCE: _make_adapter_executor(CommandType.HUMAN_GATE_REQUEST_MORE_EVIDENCE),
    CommandType.HUMAN_GATE_REVOKE: _make_adapter_executor(CommandType.HUMAN_GATE_REVOKE),
    CommandType.HUMAN_GATE_EXTEND_TTL: _make_adapter_executor(CommandType.HUMAN_GATE_EXTEND_TTL),
    CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT: _make_adapter_executor(CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT),
    CommandType.OBSERVE: _make_adapter_executor(CommandType.OBSERVE),
    CommandType.REQUEST_REVIEW: _make_adapter_executor(CommandType.REQUEST_REVIEW),
    CommandType.PAUSE_PAPER_RUNTIME: _make_adapter_executor(CommandType.PAUSE_PAPER_RUNTIME),
    CommandType.RESUME_PAPER_RUNTIME: _make_adapter_executor(CommandType.RESUME_PAPER_RUNTIME),
    CommandType.DEMOTE: _make_adapter_executor(CommandType.DEMOTE),
    CommandType.PROMOTE_CANDIDATE: _make_adapter_executor(CommandType.PROMOTE_CANDIDATE),
    CommandType.REBALANCE_PROPOSAL: _make_adapter_executor(CommandType.REBALANCE_PROPOSAL),
    CommandType.APPROVED_APPLY: _execute_approved_rebalance_apply,
    CommandType.EMERGENCY_CONTAINMENT: _execute_emergency_containment_authority,
    CommandType.RECORD_SPONSOR_DECISION: _make_adapter_executor(CommandType.RECORD_SPONSOR_DECISION),
    CommandType.DEPLOYMENT_CREATE: _make_adapter_executor(CommandType.DEPLOYMENT_CREATE),
    CommandType.DEPLOYMENT_PATCH: _make_adapter_executor(CommandType.DEPLOYMENT_PATCH),
    CommandType.REBALANCE_PATCH: _make_adapter_executor(CommandType.REBALANCE_PATCH),
    CommandType.V5_INTERVENTION_ACTION: _make_adapter_executor(CommandType.V5_INTERVENTION_ACTION),
    CommandType.DECIDE_V5_INTERVENTION: _make_adapter_executor(CommandType.DECIDE_V5_INTERVENTION),
    CommandType.SENTINEL_FINDING_STATUS: _make_adapter_executor(CommandType.SENTINEL_FINDING_STATUS),
    CommandType.SENTINEL_REMEDIATION_BUILD: _make_adapter_executor(CommandType.SENTINEL_REMEDIATION_BUILD),
    CommandType.SENTINEL_REMEDIATION_EXECUTE: _make_adapter_executor(CommandType.SENTINEL_REMEDIATION_EXECUTE),
    CommandType.AGORA_SIGNAL_FEEDBACK: _make_adapter_executor(CommandType.AGORA_SIGNAL_FEEDBACK),
    CommandType.AGORA_MESSAGE_ACTION: _make_adapter_executor(CommandType.AGORA_MESSAGE_ACTION),
    CommandType.AGORA_INSIGHT_ACTION: _make_adapter_executor(CommandType.AGORA_INSIGHT_ACTION),
    CommandType.AGORA_MEMORY_ACTION: _make_adapter_executor(CommandType.AGORA_MEMORY_ACTION),
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
    """Execute a command by dispatching to the appropriate internal API endpoint or domain adapter.

    Returns the result payload on success.
    Raises Exception on any failure (caller should catch and record as FAILED).
    """
    executor = _EXECUTORS.get(command_type)
    if executor is not None:
        return executor(command_id, params, auth_token=auth_token, mfa_token=mfa_token)
    return dispatch_domain_command(command_id, command_type, params, auth_token=auth_token, mfa_token=mfa_token)


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
    except ActionUnavailableError as exc:
        error = {
            "code": exc.error_code,
            "message": exc.message,
            "action_id": exc.action_id,
            "entity_type": exc.entity_type,
            "started_at": started_at,
            "failed_at": _utc_now(),
            "downstream_status": 422,
            "retryable": False,
            "userActionable": False,
            "suggestion": exc.suggestion,
        }
        log.warning("Command %s action unavailable: %s", command_id, error["message"])
        return CommandStatus.FAILED, None, error
    except urllib.error.HTTPError as exc:
        retryable = exc.code >= 500
        error = {
            "code": "DOWNSTREAM_ERROR",
            "message": f"Command backend returned {exc.code} for {command_id}",
            "started_at": started_at,
            "failed_at": _utc_now(),
            "downstream_status": exc.code,
            "retryable": retryable,
            "suggestion": "Check internal/governance API health and retry if appropriate",
        }
        log.error("Command %s HTTP error: %s", command_id, error["message"])
        return CommandStatus.FAILED, None, error
    except urllib.error.URLError as exc:
        # Covers connection failures, timeouts, SSL errors
        reason = str(getattr(exc, "reason", exc))
        is_timeout = "timed out" in reason.lower() or "timeout" in reason.lower()
        code = "COMMAND_TIMEOUT" if is_timeout else "DEPENDENCY_UNAVAILABLE"
        status = CommandStatus.TIMEOUT if is_timeout else CommandStatus.FAILED
        error = {
            "code": code,
            "message": f"Command backend unreachable for {command_id}: {reason}",
            "started_at": started_at,
            "failed_at": _utc_now(),
            "retryable": True,
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
            "retryable": True,
            "suggestion": "Retry the command or escalate if downstream is unresponsive",
        }
        log.error("Command %s timed out", command_id)
        return CommandStatus.TIMEOUT, None, error
    except (
        ConnectionError,
        http.client.IncompleteRead,
        http.client.RemoteDisconnected,
        http.client.BadStatusLine,
        json.JSONDecodeError,
        UnicodeDecodeError,
        EOFError,
    ) as exc:
        # The owner POST may have committed while its response was truncated,
        # reset, or unparsable.  A failed receipt reconciliation leaves the
        # outcome unknown, so same-key retry must remain available.
        error = {
            "code": "DOWNSTREAM_AMBIGUOUS",
            "message": f"Command owner outcome is ambiguous for {command_id}: {exc}",
            "started_at": started_at,
            "failed_at": _utc_now(),
            "retryable": True,
            "suggestion": "Retry with the same idempotency key so the owner receipt can be reconciled",
        }
        log.error("Command %s ambiguous owner outcome: %s", command_id, exc)
        return CommandStatus.FAILED, None, error
    except RuntimeError as exc:
        error = {
            "code": "COMMAND_BACKEND_UNCONFIGURED",
            "message": f"Command backend is not configured for {command_id}: {exc}",
            "started_at": started_at,
            "failed_at": _utc_now(),
            "retryable": False,
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
            "retryable": False,
            "suggestion": "Review command parameters and retry, or escalate to platform team",
        }
        log.exception("Command %s execution error", command_id)
        return CommandStatus.FAILED, None, error
