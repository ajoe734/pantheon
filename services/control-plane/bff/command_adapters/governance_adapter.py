"""Governance Domain Command Adapter.

Routes approval decisions, human gate transitions, sponsor decisions,
and review requests to the authoritative Governance and Consultation endpoints.
"""
from __future__ import annotations

import logging
import re
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
try:
    from ..auth.policy import bff_error as _bff_error
    from ..models import ErrorCode
except (ImportError, ValueError):
    from auth.policy import bff_error as _bff_error
    from models import ErrorCode

log = logging.getLogger(__name__)


class GovernanceCommandAdapter(DomainCommandAdapter):
    """Adapter for Governance and Consultation domain authority commands."""

    _HANDLED_COMMANDS = {
        "ApproveDecision",
        "RejectDecision",
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
        from .retired import reject_retired_command
        reject_retired_command(command_type)
        action_id = str(params.get("action_id") or command_type or "").strip()
        entity_id = str(params.get("decision_id") or params.get("gate_id") or params.get("committee_id") or params.get("review_id") or params.get("entity_id") or "").strip()

        raw_candidates = [str(command_type or ""), str(action_id or ""), str(params.get("action") or ""), str(params.get("decision") or ""), str(params.get("verb") or ""), str(params.get("action_id") or ""), str(params.get("actionId") or ""), str(params.get("outcome") or "")]
        if any(re.sub(r"[^a-z0-9]", "", v.lower()) in {"requestrevision", "requestapprovalrevision", "requestchanges", "requestchange"} for v in raw_candidates if v) or params.get("revision_notes") or params.get("revisionNotes"):
            raise _bff_error(410, ErrorCode.VALIDATION_FAILED, "RequestApprovalRevision is retired", "Use RejectDecision with notes")
        elif command_type == "ApproveDecision":
            return self._execute_decision_action(command_id, entity_id, "approve", params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type == "RejectDecision":
            return self._execute_decision_action(command_id, entity_id, "reject", params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type.startswith("HumanGate"):
            return self._execute_human_gate_action(command_id, entity_id, command_type or action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type == "RecordSponsorDecision":
            return self._execute_sponsor_decision(command_id, entity_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"ReviewAction", "RequestReview"}:
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
        from ..governance import approval_owner

        target_id = decision_id or str(params.get("decision_id") or "").strip()
        if not target_id:
            raise ValueError(f"ApprovalDecision action {verb} requires decision_id.")

        for k in ("decision", "outcome", "action", "verb", "action_id", "actionId"):
            v = params.get(k)
            if v is not None and not isinstance(v, str):
                raise approval_owner.InvalidApprovalRequest(f"Invalid {k} carrier type: must be a string")

        cand_verbs = set()
        for k in ("decision", "outcome", "action", "verb", "action_id", "actionId"):
            v = params.get(k)
            if isinstance(v, str) and v.strip():
                cand_verbs.add(re.sub(r"[^a-z0-9]", "", v.lower()))

        has_app = any(v in {"approve", "approved", "approvedecision"} for v in cand_verbs)
        has_cond = any(v in {"approvedwithconditions", "approvewithconditions", "conditional"} for v in cand_verbs)
        has_rej = any(v in {"reject", "rejected", "rejectdecision"} for v in cand_verbs)

        if (has_app or has_cond) and has_rej:
            raise approval_owner.InvalidApprovalRequest("ApprovalDecision params contain conflicting verbs")
        if verb in {"approve", "approved"} and has_rej:
            raise approval_owner.InvalidApprovalRequest("ApprovalDecision approve conflicts with reject in params")
        if verb in {"reject", "rejected"} and (has_app or has_cond):
            raise approval_owner.InvalidApprovalRequest("ApprovalDecision reject conflicts with approve in params")
        if any(v in {"stage", "freeze", "escalate"} for v in cand_verbs) or any(params.get(k) not in (None, "") for k in ("stage_name", "stageName", "stage_id", "stageId", "stage")):
            raise approval_owner.UnsupportedApprovalAction("Unsupported approval action")

        if verb in {"approve", "approved"} and has_cond:
            verb = "approved_with_conditions"

        decision = approval_owner.decide(auth_token, target_id, {**params, "decision": verb, "outcome": verb}, command_id)
        return build_domain_receipt(
            command_id=command_id,
            entity_type="ApprovalDecision",
            entity_id=target_id,
            action_id=f"Decision:{verb}",
            status=decision["decision_state"],
            dispatch_path=approval_owner.owner_url(f"/api/governance/approvals/{quote(target_id, safe='')}/decide"),
            domain_receipt=decision,
            authoritative_readback={"decision_id": target_id, "decision_state": decision["decision_state"], "version": decision.get("version")},
            extra={"decision_id": target_id, "decision_state": decision["decision_state"]},
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

        from .retired import reject_retired_command
        reject_retired_command(action_name)
        subpath = "revoke"
        payload = {
            "command_id": command_id,
            "operator_id": params.get("operator_id") or params.get("actor_id") or "operator",
            "reason": params.get("reason") or f"Human gate {action_name}",
        }

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
            authoritative_readback=body,
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
            authoritative_readback=body,
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
        raw_candidates = [str(action_id or ""), str(params.get("decision") or ""), str(params.get("action") or ""), str(params.get("verb") or ""), str(params.get("action_id") or ""), str(params.get("actionId") or ""), str(params.get("outcome") or "")]
        if any(re.sub(r"[^a-z0-9]", "", v.lower()) in {"requestrevision", "requestapprovalrevision", "requestchanges", "requestchange"} for v in raw_candidates if v) or params.get("revision_notes") or params.get("revisionNotes"):
            raise _bff_error(410, ErrorCode.VALIDATION_FAILED, "RequestApprovalRevision is retired", "Use RejectDecision with notes")
        verbs = {re.sub(r"[^a-z0-9]", "", v.lower()) for v in raw_candidates if v and v.strip()}
        has_app = any(v in {"approve", "approved", "approvedecision"} for v in verbs)
        has_cond = any(v in {"approvedwithconditions", "approvewithconditions", "conditional"} for v in verbs)
        has_rej = any(v in {"reject", "rejected", "rejectdecision"} for v in verbs)
        if (has_app or has_cond) and has_rej:
            raise _bff_error(422, ErrorCode.VALIDATION_FAILED, "Conflicting action and decision", "ReviewAction carriers contain conflicting verbs")
        norm_verb = "approved_with_conditions" if has_cond else ("approve" if has_app else ("reject" if has_rej else ""))
        if not norm_verb or any(v in {"stage", "freeze", "escalate"} for v in verbs) or any(params.get(k) not in (None, "") for k in ("stage_name", "stageName", "stage_id", "stageId", "stage")):
            from ..governance.approval_owner import UnsupportedApprovalAction
            raise UnsupportedApprovalAction(f"review action {action_id!r} has no Governance owner transition")
        return self._execute_decision_action(command_id, review_id, norm_verb, params, auth_token=auth_token, mfa_token=mfa_token)
