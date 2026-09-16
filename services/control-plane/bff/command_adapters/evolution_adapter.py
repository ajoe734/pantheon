"""Evolution and Research Experiment Domain Command Adapter.

Routes evolution proposals, mutations, experiments, and jobs to the authoritative
Evolution and Governance service endpoints.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
from typing import Any, Dict, Optional
from urllib.parse import quote

from .base import (
    ActionUnavailableError,
    DomainCommandAdapter,
    build_domain_receipt,
    evolution_url,
    governance_url,
    http_request_json,
    utc_now,
)

log = logging.getLogger(__name__)

_CMD_TO_ACTION_ID = {
    "SubmitEvolutionReview": "submit_evolution_review",
    "ApproveEvolutionProgram": "approve_program",
    "PauseEvolutionProgram": "pause_program",
    "ResumeEvolutionProgram": "resume_program",
    "CompleteEvolutionProgram": "complete_program",
    "RetireEvolutionProgram": "retire_program",
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
        normalized_action = str(action_id or "").strip()
        return (
            normalized_cmd in self._HANDLED_COMMANDS
            or normalized_action in _CMD_TO_ACTION_ID
            or normalized_action in _CMD_TO_ACTION_ID.values()
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
        if action_id in _CMD_TO_ACTION_ID:
            action_id = _CMD_TO_ACTION_ID[action_id]
        elif command_type in _CMD_TO_ACTION_ID and (not action_id or action_id == "EvolutionProgramAction"):
            action_id = _CMD_TO_ACTION_ID[command_type]

        entity_id = str(params.get("evolution_decision_id") or params.get("decision_id") or params.get("program_id") or params.get("experiment_id") or params.get("job_id") or params.get("entity_id") or "").strip()

        if command_type in {"ApproveEvolutionDecision", "ApproveMutation", "RejectMutation", "ReviewMutation"}:
            return self._execute_proposal_review(command_id, entity_id, command_type, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"ExecuteEvolutionAction", "ExecuteMutation"}:
            return self._execute_proposal_execute(command_id, entity_id, command_type, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type == "EvolutionProgramAction" or command_type in _CMD_TO_ACTION_ID or str(params.get("entity_type") or "").strip().lower() in self._HANDLED_ENTITIES:
            return self._execute_program_action(command_id, entity_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        else:
            raise ActionUnavailableError(
                f"Evolution action {action_id!r} on {entity_id!r} is not supported.",
                action_id=action_id,
                entity_type="EvolutionDecision",
            )

    def _execute_proposal_review(
        self,
        command_id: str,
        decision_id: str,
        command_type: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_id = decision_id or str(params.get("evolution_decision_id") or params.get("decision_id") or "").strip()
        if not target_id:
            raise ValueError(f"{command_type} requires evolution_decision_id.")

        verb_map = {
            "ApproveEvolutionDecision": params.get("approval_action", "approve"),
            "ApproveMutation": "approve",
            "RejectMutation": "reject",
            "ReviewMutation": "review",
        }
        subpath = str(verb_map.get(command_type, "approve")).lower()
        payload = {
            "actor_id": params.get("actor_id") or "operator",
            "actor_role": params.get("actor_role") or "operator",
            "note": params.get("note") or params.get("rationale") or f"Operator {command_type}",
        }
        if "approval_decision_id" in params:
            payload["approval_decision_id"] = params["approval_decision_id"]

        url = governance_url(f"/api/evolution/proposals/{quote(target_id, safe='')}/{subpath}")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)

        return build_domain_receipt(
            command_id=command_id,
            entity_type="EvolutionDecision",
            entity_id=target_id,
            action_id=command_type,
            status=body.get("decision_state") or subpath,
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback={"decision_id": target_id, "decision_state": body.get("decision_state") or subpath},
            extra={
                "evolution_decision_id": target_id,
                "decision_state": body.get("decision_state"),
            },
        )

    def _execute_proposal_execute(
        self,
        command_id: str,
        decision_id: str,
        command_type: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_id = decision_id or str(params.get("evolution_decision_id") or params.get("decision_id") or "").strip()
        if not target_id:
            raise ValueError(f"{command_type} requires evolution_decision_id.")

        payload = {
            "actor_id": params.get("actor_id") or "operator",
            "actor_role": "operator",
            "note": params.get("note") or params.get("rationale") or "Operator execute mutation",
        }
        url = governance_url(f"/api/evolution/proposals/{quote(target_id, safe='')}/execute")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)

        return build_domain_receipt(
            command_id=command_id,
            entity_type="EvolutionDecision",
            entity_id=target_id,
            action_id=command_type,
            status=body.get("decision_state") or "executed",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback={"decision_id": target_id, "decision_state": body.get("decision_state") or "executed"},
            extra={
                "evolution_decision_id": target_id,
                "execution_result": body.get("execution_result"),
            },
        )

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

        clean_action = str(action_id or params.get("action_id") or "").strip()
        if clean_action in _CMD_TO_ACTION_ID:
            clean_action = _CMD_TO_ACTION_ID[clean_action]
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

        sub_payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
        actor_id = params.get("actor_id") or sub_payload.get("actor_id") or "operator"
        actor_role = params.get("actor_role") or sub_payload.get("actor_role") or "operator"
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

        digest = (
            params.get("artifact_digest")
            or sub_payload.get("artifact_digest")
            or params.get("digest")
            or sub_payload.get("digest")
        )
        if digest is not None:
            payload["artifact_digest"] = digest

        payload["payload"] = sub_payload
        idempotency_key = (
            params.get("idempotency_key")
            or params.get("idempotencyKey")
            or sub_payload.get("idempotency_key")
            or sub_payload.get("idempotencyKey")
        )
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key

        tenant_id = (
            params.get("tenant_id")
            or sub_payload.get("tenant_id")
            or os.getenv("EVOLUTION_DEFAULT_TENANT_ID")
            or os.getenv("PANTHEON_TENANT_ID")
            or "default"
        )
        dispatch_headers: Dict[str, str] = {
            "X-Tenant-Id": str(tenant_id),
        }
        if idempotency_key:
            dispatch_headers["Idempotency-Key"] = str(idempotency_key)
            dispatch_headers["X-Idempotency-Key"] = str(idempotency_key)

        auth = auth_token or os.getenv("EVOLUTION_AUTH_TOKEN")

        try:
            body = http_request_json(
                url,
                method="POST",
                payload=payload,
                auth_token=auth,
                mfa_token=mfa_token,
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
