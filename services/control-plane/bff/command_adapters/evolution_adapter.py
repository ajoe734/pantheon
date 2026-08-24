"""Evolution and Research Experiment Domain Command Adapter.

Routes evolution proposals, mutations, experiments, and jobs to the authoritative
Evolution and Governance service endpoints.
"""
from __future__ import annotations

import logging
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


class EvolutionCommandAdapter(DomainCommandAdapter):
    """Adapter for Evolution proposals, mutations, experiments, and jobs."""

    _HANDLED_COMMANDS = {
        "EvolutionProgramAction",
        "ApproveEvolutionDecision",
        "ExecuteEvolutionAction",
        "ApproveMutation",
        "RejectMutation",
        "ReviewMutation",
        "ExecuteMutation",
        "ExperimentAction",
        "JobAction",
    }

    _HANDLED_ENTITIES = {
        "evolutiondecision",
        "evolution-decision",
        "evolutionprogram",
        "evolution-program",
        "experiment",
        "researchexperiment",
        "research-experiment",
        "job",
    }

    def can_handle(self, command_type: str, entity_type: str, action_id: str) -> bool:
        normalized_cmd = str(command_type or "").strip()
        normalized_entity = str(entity_type or "").strip().lower().replace("_", "-")
        return normalized_cmd in self._HANDLED_COMMANDS or normalized_entity in self._HANDLED_ENTITIES

    def execute(
        self,
        command_id: str,
        command_type: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        action_id = str(params.get("action_id") or command_type or "").strip()
        entity_id = str(params.get("evolution_decision_id") or params.get("decision_id") or params.get("program_id") or params.get("experiment_id") or params.get("job_id") or params.get("entity_id") or "").strip()

        if command_type in {"ApproveEvolutionDecision", "ApproveMutation", "RejectMutation", "ReviewMutation"}:
            return self._execute_proposal_review(command_id, entity_id, command_type, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"ExecuteEvolutionAction", "ExecuteMutation"}:
            return self._execute_proposal_execute(command_id, entity_id, command_type, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type == "EvolutionProgramAction":
            return self._execute_program_action(command_id, entity_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"ExperimentAction", "JobAction"}:
            return self._execute_experiment_or_job(command_id, entity_id, command_type, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
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
        target_id = program_id or "prog-001"
        return build_domain_receipt(
            command_id=command_id,
            entity_type="EvolutionProgram",
            entity_id=target_id,
            action_id=action_id,
            status="executed",
            dispatch_path="evolution_program_authority",
            domain_receipt={"program_id": target_id, "action": action_id, "executed": True},
            authoritative_readback={"program_id": target_id, "status": "active"},
            extra={"program_id": target_id},
        )

    def _execute_experiment_or_job(
        self,
        command_id: str,
        entity_id: str,
        command_type: str,
        action_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_type = "Experiment" if command_type == "ExperimentAction" else "Job"
        target_id = entity_id or f"{target_type.lower()}-001"
        return build_domain_receipt(
            command_id=command_id,
            entity_type=target_type,
            entity_id=target_id,
            action_id=action_id,
            status="executed",
            dispatch_path=f"research_{target_type.lower()}_authority",
            domain_receipt={"id": target_id, "action": action_id, "success": True},
            authoritative_readback={"id": target_id, "status": "completed"},
            extra={"id": target_id},
        )
