"""Deployment Domain Command Adapter.

Routes deployment creation, validation, status updates, escalation, approval,
and dispatch to the authoritative Deployment Service and internal API endpoints.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import quote

from .base import (
    ActionUnavailableError,
    DomainCommandAdapter,
    build_domain_receipt,
    deployment_url,
    http_request_json,
    internal_url,
    utc_now,
)

log = logging.getLogger(__name__)


class DeploymentCommandAdapter(DomainCommandAdapter):
    """Adapter for Deployment domain authority commands."""

    _HANDLED_COMMANDS = {
        "DeploymentAction",
        "ApproveDeployment",
        "EscalateDiff",
        "CreateDeployment",
        "PatchDeployment",
    }

    _HANDLED_ENTITIES = {
        "deployment",
        "deploymentplan",
        "deployment-plan",
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
        plan_id = str(params.get("deployment_plan_id") or params.get("plan_id") or params.get("entity_id") or "").strip()

        if command_type == "ApproveDeployment" or action_id.lower() in {"approve", "approvedeployment"}:
            return self._execute_approve_deployment(command_id, plan_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type == "EscalateDiff" or action_id.lower() in {"escalatediff", "escalate_diff"}:
            return self._execute_escalate_diff(command_id, plan_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type == "CreateDeployment" or action_id.lower() in {"create", "createdeployment"}:
            return self._execute_create_deployment(command_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type == "PatchDeployment" or action_id.lower() in {"patch", "update", "patchdeployment"}:
            return self._execute_patch_deployment(command_id, plan_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif action_id.lower() == "dispatch":
            return self._execute_dispatch_deployment(command_id, plan_id, params, auth_token=auth_token, mfa_token=mfa_token)
        else:
            raise ActionUnavailableError(
                f"Deployment action {action_id!r} on plan {plan_id!r} is not supported.",
                action_id=action_id,
                entity_type="DeploymentPlan",
            )

    def _execute_approve_deployment(
        self,
        command_id: str,
        plan_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_plan_id = plan_id or str(params.get("deployment_plan_id") or "").strip()
        if not target_plan_id:
            raise ValueError("ApproveDeployment requires deployment_plan_id.")

        payload = {
            "approval_decision": params.get("approval_decision", "approve"),
            "verification_timestamp": params.get("verification_timestamp", utc_now()),
        }
        url = internal_url(f"/api/internal/v1/deployments/{quote(target_plan_id, safe='')}/approve")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)

        # Readback from deployment service if available
        readback = None
        try:
            readback = http_request_json(deployment_url(f"/api/deployment/plans/{quote(target_plan_id, safe='')}"), method="GET", auth_token=auth_token, mfa_token=mfa_token)
        except Exception:
            pass

        return build_domain_receipt(
            command_id=command_id,
            entity_type="DeploymentPlan",
            entity_id=target_plan_id,
            action_id="ApproveDeployment",
            status=body.get("state_after") or "approved",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback=readback or {"plan_id": target_plan_id, "status": "approved"},
            extra={
                "approval_decision_id": body.get("approval_decision_id"),
                "target_plan_id": target_plan_id,
                "state_after": body.get("state_after", "approved"),
                "audit_id": body.get("audit_id"),
            },
        )

    def _execute_escalate_diff(
        self,
        command_id: str,
        plan_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_plan_id = plan_id or str(params.get("plan_id") or "").strip()
        if not target_plan_id:
            raise ValueError("EscalateDiff requires plan_id.")
        payload = {"escalation_reason": params.get("escalation_reason", "Operator escalated diff")}
        url = internal_url(f"/api/internal/v1/deployment-plans/{quote(target_plan_id, safe='')}/escalate-diff")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)
        return build_domain_receipt(
            command_id=command_id,
            entity_type="DeploymentPlan",
            entity_id=target_plan_id,
            action_id="EscalateDiff",
            status=body.get("status") or "escalated",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback={"plan_id": target_plan_id, "status": body.get("status") or "escalated"},
            extra={"plan_id": target_plan_id, "audit_id": body.get("audit_id")},
        )

    def _execute_create_deployment(
        self,
        command_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        url = deployment_url("/api/deployment/plans")
        body = http_request_json(url, method="POST", payload=params, auth_token=auth_token, mfa_token=mfa_token)
        target_plan_id = str(body.get("plan_id") or body.get("id") or "").strip()

        readback = None
        if target_plan_id:
            try:
                readback = http_request_json(deployment_url(f"/api/deployment/plans/{quote(target_plan_id, safe='')}"), method="GET", auth_token=auth_token, mfa_token=mfa_token)
            except Exception:
                pass

        return build_domain_receipt(
            command_id=command_id,
            entity_type="DeploymentPlan",
            entity_id=target_plan_id,
            action_id="CreateDeployment",
            status="created",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback=readback,
            extra={"plan_id": target_plan_id},
        )

    def _execute_patch_deployment(
        self,
        command_id: str,
        plan_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not plan_id:
            raise ValueError("PatchDeployment requires plan_id.")
        url = deployment_url(f"/api/deployment/plans/{quote(plan_id, safe='')}/status")
        payload = {
            "status": params.get("status") or "updated",
            "reason": params.get("reason") or "Operator patch",
        }
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)
        readback = http_request_json(deployment_url(f"/api/deployment/plans/{quote(plan_id, safe='')}"), method="GET", auth_token=auth_token, mfa_token=mfa_token)

        return build_domain_receipt(
            command_id=command_id,
            entity_type="DeploymentPlan",
            entity_id=plan_id,
            action_id="PatchDeployment",
            status="executed",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback=readback,
            extra={"plan_id": plan_id},
        )

    def _execute_dispatch_deployment(
        self,
        command_id: str,
        plan_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not plan_id:
            raise ValueError("Dispatch requires plan_id.")
        url = deployment_url(f"/api/deployment/plans/{quote(plan_id, safe='')}/dispatch")
        body = http_request_json(url, method="POST", payload=params, auth_token=auth_token, mfa_token=mfa_token)
        readback = http_request_json(deployment_url(f"/api/deployment/plans/{quote(plan_id, safe='')}"), method="GET", auth_token=auth_token, mfa_token=mfa_token)

        return build_domain_receipt(
            command_id=command_id,
            entity_type="DeploymentPlan",
            entity_id=plan_id,
            action_id="dispatch",
            status="dispatched",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback=readback,
            extra={"plan_id": plan_id},
        )
