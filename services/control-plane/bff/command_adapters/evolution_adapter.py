"""Evolution and Research Experiment Domain Command Adapter.

Routes evolution proposals, mutations, experiments, and jobs to the authoritative
Evolution and Governance service endpoints.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from .base import (
    _token_tenant,
    ActionUnavailableError,
    DomainCommandAdapter,
    build_domain_receipt,
    evolution_owner_request,
    evolution_url,
    http_request_json as _base_http_request_json,
    utc_now,
)
from .contracts import _EVOLUTION_ACTION_ALIASES



http_request_json = _base_http_request_json

log = logging.getLogger(__name__)

_PROPOSAL_COMMANDS = frozenset({
    "ApproveEvolutionDecision", "ApproveMutation", "RejectMutation", "ReviewMutation",
    "ExecuteEvolutionAction", "ExecuteMutation",
})

_EVOLUTION_ACTOR_ROLE_PREFERENCE = (
    "governance_committee",
    "risk_owner",
    "reviewer_on_duty",
    "governance_reviewer",
    "admin",
    "approver",
    "reviewer",
    "operator",
)


def _evolution_forbidden(message: str) -> ActionUnavailableError:
    return ActionUnavailableError(
        message,
        error_code="FORBIDDEN",
        suggestion="Act with an identity that holds the required Evolution role.",
        downstream_status=403,
    )


_ADMISSION_GENERIC_ROLES = {"admin", "approver", "reviewer", "operator"}


def resolve_evolution_actor(
    params: Dict[str, Any],
    auth_token: Optional[str],
    mfa_token: Optional[str] = None,
) -> Tuple[str, str]:
    """Bind Evolution decision actor fields to the verified principal.

    actor_id always comes from the verified identity; a client-supplied
    actor_id/approved_by_id may only equal it. A client-named role must be one
    the principal holds; with none named, pick a held role by preference.
    """
    try:
        from services.control_plane.bff.auth.policy import extract_identity
        raw = str(auth_token or "").strip()
        if not raw:
            raise ValueError("missing token")
        if not raw.lower().startswith("bearer "):
            raw = f"Bearer {raw}"
        identity = extract_identity(raw, mfa_token=mfa_token)
    except Exception as exc:
        raise _evolution_forbidden("Evolution command requires a verified authenticated principal.") from exc
    principal = str(identity.operator_id or "").strip()
    held = [str(r).strip() for r in (identity.roles or []) if str(r).strip()]
    if not principal:
        raise _evolution_forbidden("Evolution command requires a verified authenticated principal.")
    if params.get("request_hash") and str(params.get("actor_role") or "").strip() in _ADMISSION_GENERIC_ROLES:
        # Admission stores a generic role (admin/approver/reviewer/operator) it
        # derived itself; it is not a client claim and cannot express Evolution
        # owner roles such as risk_owner. Resolve from the principal's held roles.
        params = {k: v for k, v in params.items() if k != "actor_role"}
    for key in ("actor_id", "approved_by_id"):
        claimed = str(params.get(key) or "").strip()
        if claimed and claimed != principal:
            raise _evolution_forbidden(f"{key} does not match the authenticated principal.")
    named = {
        str(params.get(key)).strip()
        for key in ("approved_by_role", "actor_role")
        if str(params.get(key) or "").strip()
    }
    if len(named) > 1:
        raise _evolution_forbidden("Conflicting actor_role and approved_by_role.")
    if named:
        role = next(iter(named))
        if role not in held:
            raise _evolution_forbidden(f"Authenticated principal does not hold role {role!r}.")
        return principal, role
    for preferred in _EVOLUTION_ACTOR_ROLE_PREFERENCE:
        if preferred in held:
            return principal, preferred
    raise _evolution_forbidden("Authenticated principal holds no Evolution actor role.")


_SYSTEM_PRINCIPALS = frozenset({"evolution-daily-sweep", "evolution-dispatch-worker"})
_HUMAN_ONLY_PROGRAM_ACTIONS = frozenset({"approve_program", "reject_program"})
_GOVERNANCE_ROLE_ORDER = ("governance_committee", "risk_owner", "governance_reviewer")
_PROGRAM_ROLE_ORDER = ("admin", "approver", "reviewer", "operator")


def _program_principal(
    params: Dict[str, Any],
    sub_payload: Dict[str, Any],
    auth_token: Optional[str],
    mfa_token: Optional[str],
) -> Tuple[str, List[str]]:
    from services.control_plane.bff.auth.policy import extract_identity

    claimed = {
        str(value).strip()
        for value in (params.get("actor_id"), sub_payload.get("actor_id"))
        if str(value or "").strip()
    }
    claimed_id = next(iter(claimed), "") if len(claimed) == 1 else ""
    if len(claimed) > 1:
        raise _evolution_forbidden("Conflicting actor_id claims.")
    raw = str(auth_token or "").strip()
    if raw and not raw.lower().startswith("bearer "):
        raw = f"Bearer {raw}"
    try:
        identity = extract_identity(raw, mfa_token=mfa_token)
        principal = str(identity.operator_id or "").strip()
    except Exception:
        identity, principal = None, ""
    if identity is None or not principal:
        # Admission (preconditions) has already verified the caller; a token
        # this process cannot re-verify falls back to the admitted actor claim,
        # and the owner re-applies the system-identity bar to whatever arrives.
        return claimed_id or "operator", []
    if claimed_id and claimed_id != principal:
        raise _evolution_forbidden("actor_id does not match the authenticated principal.")
    return principal, [str(r).strip() for r in (identity.roles or []) if str(r).strip()]


def _program_actor_role(params: Dict[str, Any], sub_payload: Dict[str, Any], held: List[str]) -> str:
    claimed_roles = {
        str(value).strip()
        for value in (params.get("actor_role"), sub_payload.get("actor_role"))
        if str(value or "").strip()
    }
    if len(claimed_roles) > 1:
        raise _evolution_forbidden("Conflicting actor_role claims.")
    named = next(iter(claimed_roles), "")
    if named and named in held:
        return named
    if held and named and not (params.get("request_hash") and named in _ADMISSION_GENERIC_ROLES):
        raise _evolution_forbidden(f"Authenticated principal does not hold role {named!r}.")
    for preferred in _PROGRAM_ROLE_ORDER:
        if preferred in held:
            return preferred
    for preferred in _GOVERNANCE_ROLE_ORDER:
        if preferred in held:
            return preferred
    if held:
        raise _evolution_forbidden("Authenticated principal holds no Evolution program role.")
    return named or "operator"


_DRAFT_METADATA_PROGRAM_ACTIONS = frozenset({
    "create_constraint",
    "create_fitness_formula",
    "create_mutation_rule",
})

_DRAFT_METADATA_FIELDS = (
    "id",
    "name",
    "scope",
    "operator",
    "value",
    "penalty_weight",
    "enabled",
    "expression",
    "metrics",
    "applied_scope",
    "rate",
    "risk",
    "memo",
)


def _normalize_program_action_alias(raw: Any) -> str:
    clean = str(raw or "").strip()
    if not clean or clean == "EvolutionProgramAction":
        return ""
    if clean in _CMD_TO_ACTION_ID:
        return _CMD_TO_ACTION_ID[clean]
    norm = re.sub(r"[^a-z0-9]", "", clean.lower())
    return _EVOLUTION_ACTION_ALIASES.get(norm, clean)


_CMD_TO_ACTION_ID = {
    "SubmitEvolutionReview": "submit_evolution_review",
    "ApproveEvolutionProgram": "approve_program",
    "PauseEvolutionProgram": "pause_program",
    "ResumeEvolutionProgram": "resume_program",
    "CompleteEvolutionProgram": "complete_program",
    "RetireEvolutionProgram": "retire_program",
    "RejectEvolutionProgram": "reject_program",
    "StopEvolutionProgram": "stop",
    "FreezeEvolutionGeneration": "freeze_generation",
    "PromoteEvolutionCandidatePaper": "promote_candidate_paper",
    "PromoteEvolutionCandidateLive": "promote_candidate_live",
}


class EvolutionCommandAdapter(DomainCommandAdapter):
    """Adapter for Evolution proposals, mutations, experiments, and jobs."""

    # BFF-RESEARCH-JOBS-OWNER-BINDING-CORRECTIVE-001: ExperimentAction and
    # JobAction are deliberately NOT handled here anymore. They previously
    # routed into `_execute_experiment_or_job`, which fabricated a fake
    # status="executed" receipt with zero real domain effects. Experiment and
    # Job actions now route exclusively to `ExperimentCommandAdapter` and
    # `JobCommandAdapter` (registered ahead of this adapter in registry.py),
    # which either perform a real owner mutation or fail closed with
    # `ActionUnavailableError` — never a synthetic success.
    _HANDLED_COMMANDS = {
        "EvolutionProgramAction",
        "SubmitEvolutionReview",
        "ApproveEvolutionProgram",
        "PauseEvolutionProgram",
        "ResumeEvolutionProgram",
        "CompleteEvolutionProgram",
        "RetireEvolutionProgram",
        "RejectEvolutionProgram",
        "StopEvolutionProgram",
        "FreezeEvolutionGeneration",
        "PromoteEvolutionCandidatePaper",
        "PromoteEvolutionCandidateLive",
        "ApproveEvolutionDecision",
        "ExecuteEvolutionAction",
        "ApproveMutation",
        "RejectMutation",
        "ReviewMutation",
        "ExecuteMutation",
    }

    _HANDLED_ENTITIES = {
        "evolutiondecision",
        "evolution-decision",
        "evolutionprogram",
        "evolution-program",
    }

    def can_handle(self, command_type: str, entity_type: str, action_id: str) -> bool:
        normalized_cmd = str(command_type or "").strip()
        normalized_entity = str(entity_type or "").strip().lower().replace("_", "-")
        normalized_action = _normalize_program_action_alias(action_id)
        return (
            normalized_cmd in self._HANDLED_COMMANDS
            or normalized_action in _CMD_TO_ACTION_ID
            or normalized_action in _CMD_TO_ACTION_ID.values()
            or normalized_action in _DRAFT_METADATA_PROGRAM_ACTIONS
            or normalized_entity in self._HANDLED_ENTITIES
        )

    def execute(
        self,
        command_id: str,
        command_type: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        action_id = str(params.get("action_id") or command_type or "").strip()
        if command_type in _CMD_TO_ACTION_ID and (not action_id or action_id == "EvolutionProgramAction"):
            action_id = _CMD_TO_ACTION_ID[command_type]
        elif action_id:
            action_id = _normalize_program_action_alias(action_id) or action_id

        entity_id = str(params.get("evolution_decision_id") or params.get("decision_id") or params.get("program_id") or params.get("experiment_id") or params.get("job_id") or params.get("entity_id") or "").strip()

        if command_type in _PROPOSAL_COMMANDS:
            # Proposal review/approve/reject/execute are served only by command_executor._EXECUTORS
            # (service credential + verified tenant); this adapter never owned a live path for them.
            raise ActionUnavailableError(
                f"{command_type} is executed by the Evolution proposal executor, not the domain adapter.",
                action_id=action_id,
                entity_type="EvolutionDecision",
            )
        elif command_type == "EvolutionProgramAction" or command_type in _CMD_TO_ACTION_ID or str(params.get("entity_type") or "").strip().lower() in self._HANDLED_ENTITIES:
            return self._execute_program_action(command_id, entity_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        else:
            raise ActionUnavailableError(
                f"Evolution action {action_id!r} on {entity_id!r} is not supported.",
                action_id=action_id,
                entity_type="EvolutionDecision",
            )

    def _ensure_program_review_request(
        self, program_id: str, tenant_id: str, actor_id: str, auth_token: Optional[str]
    ) -> str:
        """Create the governance ``evolution_proposal`` request for this program.

        The binding (tenant, program, revision, source-decision content digest)
        is computed by the Evolution owner; this only files it in Governance
        with the operator's own credential, under a deterministic id so a
        resubmission reuses the same request in the approvals queue.
        """
        from services.control_plane.bff.governance import approval_owner

        binding = evolution_owner_request(
            evolution_url(f"/api/evolution/programs/{quote(program_id, safe='')}/approval-binding"),
            caller_token=auth_token,
            method="GET",
            tenant_id=tenant_id,
            headers={"X-Tenant-Id": tenant_id},
        )
        missing = [k for k in ("target_type", "target_id", "target_version", "proposal_id",
                               "proposal_revision", "proposal_content_digest") if not binding.get(k)]
        if missing:
            raise ActionUnavailableError(
                f"Evolution owner returned an unusable approval binding (missing {missing}).",
                action_id="submit_evolution_review",
                entity_type="EvolutionProgram",
                downstream_status=502,
                retryable=False,
            )
        approval_id = f"apv-program-{program_id}-r{binding['proposal_revision']}"
        try:
            approval_owner.propose(
                auth_token,
                {
                    "decision_id": approval_id,
                    "expected_version": 0,
                    "tenant_id": tenant_id,
                    "owner_user_id": actor_id,
                    "target_type": binding["target_type"],
                    "target_id": binding["target_id"],
                    "target_version": binding["target_version"],
                    "risk_level": binding.get("risk_level") or "medium",
                    "proposal_id": binding["proposal_id"],
                    "proposal_revision": binding["proposal_revision"],
                    "proposal_content_digest": binding["proposal_content_digest"],
                },
                approval_id,
            )
        except urllib.error.HTTPError as exc:
            if exc.code != 409:  # 409: the deterministic request already exists -> reuse it
                raise ActionUnavailableError(
                    f"Governance refused the program review request: HTTP {exc.code}",
                    action_id="submit_evolution_review",
                    entity_type="EvolutionProgram",
                    retryable=exc.code in (502, 503, 504),
                    downstream_status=exc.code,
                ) from exc
        return approval_id

    def _execute_program_action(
        self,
        command_id: str,
        program_id: str,
        action_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_id = str(program_id or params.get("program_id") or params.get("entity_id") or "").strip()
        if not target_id:
            raise ValueError("EvolutionProgramAction requires program_id.")

        sub_payload = dict(params.get("payload")) if isinstance(params.get("payload"), dict) else {}
        carrier_actions = {
            norm_act
            for raw_act in (
                action_id,
                params.get("action_id"),
                params.get("actionId"),
                params.get("action"),
                sub_payload.get("action_id"),
                sub_payload.get("actionId"),
                sub_payload.get("action"),
            )
            for norm_act in (_normalize_program_action_alias(raw_act),)
            if norm_act
        }
        if len(carrier_actions) > 1:
            raise ActionUnavailableError(
                f"Conflicting evolution program action carriers on {target_id!r}.",
                action_id=str(action_id or params.get("action_id") or "").strip(),
                entity_type="EvolutionProgram",
                error_code="VALIDATION_FAILED",
                retryable=False,
                downstream_status=422,
            )
        clean_action = next(iter(carrier_actions), "")
        if not clean_action:
            raise ValueError("EvolutionProgramAction requires action_id.")

        url_path = f"/api/evolution/programs/{quote(target_id, safe='')}/actions/{quote(clean_action, safe='')}"
        try:
            url = evolution_url(url_path)
        except RuntimeError as exc:
            raise ActionUnavailableError(
                f"Program action {clean_action!r} on {target_id!r} is unavailable: {exc}",
                action_id=clean_action,
                entity_type="EvolutionProgram",
                suggestion="Configure PANTHEON_EVOLUTION_API_URL or submit a supported domain action.",
                retryable=False,
                downstream_status=422,
            ) from exc

        # The acting identity is the verified principal, never a body claim; a
        # claimed actor_id may only equal it.  System identities are refused
        # here as well as at the owner.
        actor_id, held_roles = _program_principal(params, sub_payload, auth_token, mfa_token)
        if clean_action in _HUMAN_ONLY_PROGRAM_ACTIONS and actor_id.lower() in _SYSTEM_PRINCIPALS:
            raise _evolution_forbidden(f"System identity {actor_id!r} can never {clean_action} a program.")
        actor_role = _program_actor_role(params, sub_payload, held_roles)
        note = params.get("note") or params.get("rationale") or sub_payload.get("note") or f"Operator {clean_action}"

        payload: Dict[str, Any] = {
            "actor_id": actor_id,
            "actor_role": actor_role,
            "note": note,
        }
        for field in (
            "candidate_id",
            "run_id",
            "mutation_id",
            "decision_id",
            "artifact_id",
            "artifact_version",
            "approval_id",
            "generation_id",
            "reason",
            "expected_revision",
            "params",
        ):
            val = params.get(field) if params.get(field) is not None else sub_payload.get(field)
            if val is not None:
                payload[field] = val

        if clean_action in _DRAFT_METADATA_PROGRAM_ACTIONS:
            for field in _DRAFT_METADATA_FIELDS:
                val = params.get(field) if params.get(field) is not None else sub_payload.get(field)
                if val is not None:
                    payload[field] = val
                    sub_payload.setdefault(field, val)

        digest = (
            params.get("artifact_digest")
            or sub_payload.get("artifact_digest")
            or params.get("digest")
            or sub_payload.get("digest")
        )
        if digest is not None:
            payload["artifact_digest"] = digest

        payload["payload"] = sub_payload
        decision_role = next((r for r in _GOVERNANCE_ROLE_ORDER if r in held_roles), None)
        if clean_action == "reject_program" and decision_role:
            payload["decision_actor_role"] = decision_role
        idempotency_key = (
            params.get("idempotency_key")
            or params.get("idempotencyKey")
            or sub_payload.get("idempotency_key")
            or sub_payload.get("idempotencyKey")
        )
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key

        tenant_id = _token_tenant(auth_token)
        if not tenant_id:
            raise ActionUnavailableError(
                f"Program action {clean_action!r} requires a verified caller tenant.",
                action_id=clean_action,
                entity_type="EvolutionProgram",
                error_code="TENANT_REQUIRED",
            )
        for claimed in (params.get("tenant_id"), sub_payload.get("tenant_id")):
            if claimed and str(claimed).strip() != tenant_id:
                raise ActionUnavailableError(
                    f"Program action {clean_action!r} tenant is not the verified caller tenant.",
                    action_id=clean_action,
                    entity_type="EvolutionProgram",
                    error_code="TENANT_MISMATCH",
                )
        dispatch_headers: Dict[str, str] = {"X-Tenant-Id": tenant_id}
        if idempotency_key:
            dispatch_headers["Idempotency-Key"] = str(idempotency_key)

        if clean_action == "submit_evolution_review":
            # Submitting creates (or reuses) the one governance approval
            # request this program's review is decided on; the owner never
            # invents an approval id.
            payload["approval_id"] = self._ensure_program_review_request(
                target_id, tenant_id, actor_id, auth_token
            )

        try:
            body = evolution_owner_request(
                url,
                caller_token=auth_token,
                method="POST",
                payload=payload,
                tenant_id=tenant_id,
                headers=dispatch_headers,
            )
        except urllib.error.HTTPError as exc:
            err_body = {}
            try:
                err_body = json.loads(exc.read().decode("utf-8"))
            except Exception:
                pass
            msg = err_body.get("detail") or f"HTTP {exc.code} from Evolution service"
            raise ActionUnavailableError(
                f"Program action {clean_action!r} on {target_id!r} failed: {msg}",
                action_id=clean_action,
                entity_type="EvolutionProgram",
                suggestion="Verify program state and permissions before retrying.",
                retryable=(exc.code in (502, 503, 504)),
                downstream_status=exc.code,
            ) from exc
        except Exception as exc:
            raise ActionUnavailableError(
                f"Program action {clean_action!r} on {target_id!r} failed: {exc}",
                action_id=clean_action,
                entity_type="EvolutionProgram",
                suggestion="Evolution service is unavailable.",
                retryable=True,
                downstream_status=503,
            ) from exc

        receipt_status = body.get("status") or body.get("program_status") or "completed"
        program_data = body.get("program") or {}
        readback = {
            "program_id": target_id,
            "status": body.get("program_status") or program_data.get("status") or receipt_status,
            "revision": program_data.get("revision"),
            "action_id": clean_action,
            "receipt_id": body.get("receipt_id"),
        }

        return build_domain_receipt(
            command_id=command_id,
            entity_type="EvolutionProgram",
            entity_id=target_id,
            action_id=clean_action,
            status=receipt_status,
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback=readback,
            idempotent_replay=bool(body.get("idempotent_replay", False)),
            extra={
                "evolution_program_id": target_id,
                "program_status": body.get("program_status") or program_data.get("status"),
                "receipt_id": body.get("receipt_id"),
                "live_capital_side_effects": False,
            },
        )


def _decision_approval_error_code(status_code: int):
    from services.control_plane.bff.models import ErrorCode

    return {
        401: ErrorCode.AUTH_REQUIRED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.RESOURCE_NOT_FOUND,
        409: ErrorCode.RESOURCE_CONFLICT,
        422: ErrorCode.VALIDATION_FAILED,
    }.get(status_code, ErrorCode.DEPENDENCY_UNAVAILABLE)


def _reused_approval_conflict(body: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Classify an existing Governance approval request when it cannot be reused.

    Pending (``proposed`` / ``under_review``) and still-valid ``approved``
    requests keep reusing the deterministic id.  Rejected, revoked, or expired
    requests are dead ends and must be refused with 409 so the operator cancels
    the decision and proposes anew.
    """
    state = str(body.get("decision_state") or "").strip().lower()
    decision = str(body.get("decision") or "").strip().lower()
    if state == "rejected" or decision == "rejected":
        return (
            "approval_rejected",
            "Execution approval request was rejected; cancel the decision and propose anew.",
        )
    if state == "revoked" or decision == "revoked":
        return (
            "approval_revoked",
            "Execution approval request was revoked; cancel the decision and propose anew.",
        )
    if state == "expired" or decision == "expired":
        return (
            "approval_expired",
            "Execution approval request has expired; cancel the decision and propose anew.",
        )
    expires_raw = str(body.get("expires_at") or "").strip()
    if expires_raw:
        try:
            expires_dt = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            if expires_dt <= datetime.now(timezone.utc):
                return (
                    "approval_expired",
                    "Execution approval request has expired; cancel the decision and propose anew.",
                )
        except ValueError:
            pass
    return None


def create_evolution_decision_approval_router(
    *,
    extract_identity: Any,
    require_operator_role: Any,
    bff_error: Any,
):
    """Build the BFF route that asks the Evolution owner to file ``evolution_execute``.

    EVOLUTION-BFF-FREEZE-EXEC-20261105: the Evolution owner computes the binding
    and files the Governance request under its propose-only principal; the BFF
    computes no digest and files nothing itself.  The operator then decides it
    once via ``POST /bff/approvals/{id}/decide``.
    """
    from fastapi import APIRouter, Header
    from services.control_plane.bff.models import ErrorCode

    router = APIRouter()

    @router.post("/bff/evolution-decisions/{decision_id}/execution-approval-request", status_code=200)
    def request_decision_execution_approval(
        decision_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_operator_role(identity)
        clean_id = decision_id.strip()
        try:
            body = evolution_owner_request(
                evolution_url(f"/api/evolution/proposals/{quote(clean_id, safe='')}/execution-approval-request"),
                caller_token=authorization,
                method="POST",
                payload={"actor_id": getattr(identity, "operator_id", None)},
            )
        except urllib.error.HTTPError as exc:
            status = exc.code if exc.code in (403, 404, 409, 422, 503) else 502
            detail_msg = f"Evolution owner returned HTTP {exc.code}"
            try:
                raw_err = exc.read().decode("utf-8") if hasattr(exc, "read") else ""
                parsed_err = json.loads(raw_err) if raw_err else {}
                if isinstance(parsed_err, dict) and parsed_err.get("detail"):
                    detail_msg = str(parsed_err["detail"])
            except Exception:
                pass
            raise bff_error(
                status,
                _decision_approval_error_code(status),
                "Evolution owner refused the execute approval request",
                detail_msg,
            ) from exc
        except ActionUnavailableError as exc:
            raise bff_error(
                int(getattr(exc, "downstream_status", None) or 503),
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Evolution owner is unavailable",
                str(exc),
            ) from exc
        conflict = _reused_approval_conflict(body)
        if conflict is not None:
            reason_code, conflict_message = conflict
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                conflict_message,
                reason_code,
                precondition_failed=reason_code,
                suggestion="Cancel the EvolutionDecision and propose anew.",
                details_extra={
                    "approval_decision_id": body.get("approval_decision_id"),
                    "decision_id": body.get("decision_id", clean_id),
                    "decision_state": body.get("decision_state"),
                    "decision": body.get("decision"),
                    "expires_at": body.get("expires_at"),
                },
            )
        return {
            "approval_decision_id": body.get("approval_decision_id"),
            "decision_id": body.get("decision_id", clean_id),
            "target_type": body.get("target_type"),
            "target_id": body.get("target_id"),
            "created": bool(body.get("created")),
            "decision_state": body.get("decision_state"),
            "decision": body.get("decision"),
            "expires_at": body.get("expires_at"),
            "next": "Decide this approval once via POST /bff/approvals/{id}/decide, "
                    "then submit ReviewMutation and ApproveEvolutionDecision with the approval_decision_id.",
        }

    return router
