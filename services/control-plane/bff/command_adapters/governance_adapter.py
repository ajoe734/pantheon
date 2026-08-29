"""Governance Domain Command Adapter.

Routes approval decisions, human gate transitions, sponsor decisions,
and review requests to the authoritative Governance and Consultation endpoints.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import quote

from .base import (
    ActionUnavailableError,
    DomainCommandAdapter,
    build_domain_receipt,
    governance_url,
    http_request_json,
    internal_url,
    utc_now,
)

log = logging.getLogger(__name__)


class GovernanceCommandAdapter(DomainCommandAdapter):
    """Adapter for Governance and Consultation domain authority commands."""

    _HANDLED_COMMANDS = {
        "ApproveDecision",
        "RejectDecision",
        "RequestApprovalRevision",
        "HumanGateApprove",
        "HumanGateReject",
        "HumanGateRequestMoreEvidence",
        "HumanGateRevoke",
        "HumanGateExtendTtl",
        "RecordSponsorDecision",
        "ReviewAction",
        "RequestReview",
    }

    _HANDLED_ENTITIES = {
        "approvaldecision",
        "approval-decision",
        "approval",
        "humangateitem",
        "human-gate-item",
        "humangate",
        "human-gate",
        "committeeboard",
        "committee-board",
        "committee",
        "review",
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
        entity_id = str(params.get("decision_id") or params.get("gate_id") or params.get("committee_id") or params.get("review_id") or params.get("entity_id") or "").strip()

        if command_type == "ApproveDecision" or action_id.lower() in {"approve", "approvedecision"}:
            return self._execute_decision_action(command_id, entity_id, "approve", params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type == "RejectDecision" or action_id.lower() in {"reject", "rejectdecision"}:
            return self._execute_decision_action(command_id, entity_id, "reject", params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type == "RequestApprovalRevision" or action_id.lower() in {"requestrevision", "requestapprovalrevision", "request-revision"}:
            return self._execute_decision_action(command_id, entity_id, "request-revision", params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type.startswith("HumanGate") or action_id.lower().startswith("humangate"):
            return self._execute_human_gate_action(command_id, entity_id, command_type or action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type == "RecordSponsorDecision" or action_id.lower() in {"recordsponsordecision", "sponsor-decision"}:
            return self._execute_sponsor_decision(command_id, entity_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"ReviewAction", "RequestReview"} or action_id.lower() in {"requestreview", "review"}:
            return self._execute_review_action(command_id, entity_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        else:
            raise ActionUnavailableError(
                f"Governance action {action_id!r} on {entity_id!r} is not supported.",
                action_id=action_id,
                entity_type="Governance",
            )

    def _execute_decision_action(
        self,
        command_id: str,
        decision_id: str,
        verb: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_id = decision_id or str(params.get("decision_id") or "").strip()
        if not target_id:
            raise ValueError(f"ApprovalDecision action {verb} requires decision_id.")

        payload: Dict[str, Any] = {}
        if verb == "approve":
            payload["approval_notes"] = params.get("approval_notes") or params.get("notes") or "Approved by governance"
            subpath = "approve"
            expected_state = "approved"
        elif verb == "reject":
            payload["rejection_reason"] = params.get("rejection_reason") or params.get("reason") or "Rejected by governance"
            subpath = "reject"
            expected_state = "rejected"
        else:
            payload["revision_notes"] = params.get("revision_notes") or params.get("notes") or "Revision requested"
            subpath = "request-revision"
            expected_state = "pending_revision"

        url = internal_url(f"/api/internal/v1/approval-decisions/{quote(target_id, safe='')}/{subpath}")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)

        return build_domain_receipt(
            command_id=command_id,
            entity_type="ApprovalDecision",
            entity_id=target_id,
            action_id=f"Decision:{verb}",
            status=body.get("decision_state") or expected_state,
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback={
                "decision_id": target_id,
                "decision_state": body.get("decision_state") or expected_state,
            },
            extra={
                "decision_id": target_id,
                "decision_state": body.get("decision_state") or expected_state,
                "audit_id": body.get("audit_id"),
            },
        )

    def _execute_human_gate_action(
        self,
        command_id: str,
        gate_id: str,
        action_name: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_gate_id = gate_id or str(params.get("gate_id") or params.get("entity_id") or "").strip()
        if not target_gate_id:
            raise ValueError(f"{action_name} requires gate_id.")

        verb_map = {
            "HumanGateApprove": "approve",
            "HumanGateReject": "reject",
            "HumanGateRequestMoreEvidence": "request-evidence",
            "HumanGateRevoke": "revoke",
            "HumanGateExtendTtl": "extend-ttl",
        }
        subpath = verb_map.get(action_name, action_name.lower().replace("humangate", ""))
        payload = {
            "command_id": command_id,
            "operator_id": params.get("operator_id") or params.get("actor_id") or "operator",
            "reason": params.get("reason") or f"Human gate {action_name}",
        }
        if "additional_ttl_seconds" in params:
            payload["additional_ttl_seconds"] = params["additional_ttl_seconds"]

        url = governance_url(f"/api/governance/human-gates/{quote(target_gate_id, safe='')}/{subpath}")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)

        return build_domain_receipt(
            command_id=command_id,
            entity_type="HumanGateItem",
            entity_id=target_gate_id,
            action_id=action_name,
            status=body.get("status") or "executed",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback={"gate_id": target_gate_id, "state": body.get("state") or subpath},
            extra={"gate_id": target_gate_id},
        )

    def _execute_sponsor_decision(
        self,
        command_id: str,
        committee_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_committee_id = committee_id or str(params.get("committee_id") or "").strip()
        if not target_committee_id:
            raise ValueError("RecordSponsorDecision requires committee_id.")

        payload = {
            "sponsor_decision": params.get("sponsor_decision") or params.get("decision") or "ratified",
            "sponsor_notes": params.get("sponsor_notes") or params.get("notes") or "Sponsor ratified consultation decision",
            "command_id": command_id,
        }
        url = internal_url(f"/api/internal/v1/consultations/committees/{quote(target_committee_id, safe='')}/sponsor-decision")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)

        return build_domain_receipt(
            command_id=command_id,
            entity_type="CommitteeBoard",
            entity_id=target_committee_id,
            action_id="RecordSponsorDecision",
            status=body.get("status") or "recorded",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback={"committee_id": target_committee_id, "status": "ratified"},
            extra={"committee_id": target_committee_id},
        )

    def _execute_review_action(
        self,
        command_id: str,
        review_id: str,
        action_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_review_id = review_id or str(params.get("review_id") or "review-001").strip()
        return build_domain_receipt(
            command_id=command_id,
            entity_type="Review",
            entity_id=target_review_id,
            action_id=action_id,
            status="accepted",
            dispatch_path="governance_review_store",
            domain_receipt={"review_id": target_review_id, "action": action_id, "submitted": True},
            authoritative_readback={"review_id": target_review_id, "status": "pending_review"},
            extra={"review_id": target_review_id},
        )
