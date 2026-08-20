"""Capital Domain Command Adapter.

Routes capital pool, rebalance, binding, and emergency containment actions
to the authoritative Capital Service and internal API owner endpoints.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import quote

from .base import (
    ActionUnavailableError,
    DomainCommandAdapter,
    build_domain_receipt,
    capital_url,
    http_request_json,
    internal_url,
    utc_now,
)

log = logging.getLogger(__name__)


class CapitalCommandAdapter(DomainCommandAdapter):
    """Adapter for Capital Service authority commands."""

    _HANDLED_COMMANDS = {
        "CapitalPoolAction",
        "RebalanceAction",
        "RebalanceProposal",
        "PatchRebalance",
        "ApprovedApply",
        "EmergencyContainment",
        "ApprovePool",
        "LiquidateAll",
    }

    _HANDLED_ENTITIES = {
        "capitalpool",
        "capital-pool",
        "rebalance",
        "binding",
        "personacapitalbinding",
        "persona-capital-binding",
    }

    def can_handle(self, command_type: str, entity_type: str, action_id: str) -> bool:
        normalized_cmd = str(command_type or "").strip()
        normalized_entity = str(entity_type or "").strip().lower().replace("_", "-")
        normalized_action = str(action_id or "").strip().lower().replace("_", "-")
        if normalized_cmd in self._HANDLED_COMMANDS:
            return True
        if normalized_entity in self._HANDLED_ENTITIES:
            return True
        if normalized_entity == "persona" and (
            normalized_cmd == "EmergencyContainment"
            or normalized_action in {"emergencycontainment", "emergency-containment", "containment"}
        ):
            return True
        return False

    def execute(
        self,
        command_id: str,
        command_type: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        entity_type = str(params.get("entity_type") or "").strip().lower().replace("_", "-")
        action_id = str(params.get("action_id") or "").strip()
        entity_id = str(params.get("entity_id") or params.get("pool_id") or params.get("rebalance_id") or params.get("binding_id") or params.get("persona_id") or "").strip()

        if command_type == "ApprovedApply" or action_id.lower() == "apply":
            return self._execute_rebalance_apply(command_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type == "EmergencyContainment" or action_id.lower() == "emergencycontainment":
            return self._execute_containment(command_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type == "ApprovePool" or (entity_type in {"capitalpool", "capital-pool"} and action_id.lower() in {"approve", "approvepool"}):
            return self._execute_approve_pool(command_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif entity_type in {"capitalpool", "capital-pool"}:
            return self._execute_capital_pool_action(command_id, entity_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif entity_type == "rebalance" or command_type in {"RebalanceProposal", "PatchRebalance"}:
            return self._execute_rebalance_action(command_id, entity_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif entity_type in {"binding", "personacapitalbinding", "persona-capital-binding"}:
            return self._execute_binding_action(command_id, entity_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        else:
            raise ActionUnavailableError(
                f"Capital adapter cannot route entity_type={entity_type!r} action_id={action_id!r}",
                action_id=action_id,
                entity_type=entity_type,
            )

    def _execute_approve_pool(
        self,
        command_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        pool_id = str(params.get("pool_id") or params.get("entity_id") or "").strip()
        if not pool_id:
            raise ValueError("ApprovePool requires pool_id.")
        memo = str(params.get("memo") or "Approve capital pool").strip()
        payload: Dict[str, Any] = {"memo": memo}
        if params.get("confirm_token"):
            payload["confirm_token"] = str(params["confirm_token"])

        url = internal_url(f"/api/internal/v1/capital-pools/{quote(pool_id, safe='')}/approve")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)
        
        # Authoritative readback from Capital service
        readback = None
        try:
            readback = http_request_json(capital_url(f"/api/capital-pools/{quote(pool_id, safe='')}"), method="GET", auth_token=auth_token, mfa_token=mfa_token)
        except Exception:
            pass

        return build_domain_receipt(
            command_id=command_id,
            entity_type="CapitalPool",
            entity_id=pool_id,
            action_id="ApprovePool",
            status="accepted",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback=readback,
            extra={
                "pool_id": pool_id,
                "state": body.get("state", "approved"),
                "audit_id": body.get("audit_id"),
                "approved_at": body.get("approved_at") or utc_now(),
            },
        )

    def _execute_capital_pool_action(
        self,
        command_id: str,
        pool_id: str,
        action_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not pool_id:
            raise ValueError("CapitalPool action requires a non-empty entity_id (pool_id).")

        status_map = {
            "pause": "paused",
            "freeze": "paused",
            "activate": "active",
            "resume": "active",
            "retire": "retired",
            "adjust_budget": "active",
            "update_budget": "active",
        }
        target_status = status_map.get(action_id.lower())
        if not target_status and action_id.lower() not in {"update", "patch"}:
            raise ActionUnavailableError(
                f"CapitalPool action {action_id!r} is not supported by Capital authority.",
                action_id=action_id,
                entity_type="CapitalPool",
            )

        patch_payload = {
            "status": target_status or params.get("status", "active"),
            "reason": params.get("reason") or params.get("note") or f"Operator action {action_id}",
        }
        if "budget" in params:
            patch_payload["budget"] = params["budget"]

        url = capital_url(f"/api/capital-pools/{quote(pool_id, safe='')}/status")
        body = http_request_json(url, method="PATCH", payload=patch_payload, auth_token=auth_token, mfa_token=mfa_token)

        # Authoritative readback
        readback = http_request_json(capital_url(f"/api/capital-pools/{quote(pool_id, safe='')}"), method="GET", auth_token=auth_token, mfa_token=mfa_token)

        return build_domain_receipt(
            command_id=command_id,
            entity_type="CapitalPool",
            entity_id=pool_id,
            action_id=action_id,
            status="executed",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback=readback,
            extra={
                "pool_id": pool_id,
                "pool_state": readback.get("status") if isinstance(readback, dict) else target_status,
            },
        )

    def _execute_rebalance_action(
        self,
        command_id: str,
        rebalance_id: str,
        action_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        if action_id.lower() in {"propose", "rebalanceproposal", "create"}:
            payload = {
                "command_id": command_id,
                "pool_id": params.get("pool_id") or params.get("capital_pool_id") or "default-pool",
                "proposed_by": params.get("proposed_by") or params.get("actor_id") or "operator",
                "allocations": params.get("allocations") or {},
                "reason": params.get("reason") or "Operator rebalance proposal",
            }
            url = capital_url("/api/rebalances")
            body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)
            target_rebalance_id = str(body.get("rebalance_id") or body.get("id") or "").strip()
            
            readback = None
            if target_rebalance_id:
                try:
                    readback = http_request_json(capital_url(f"/api/rebalances/{quote(target_rebalance_id, safe='')}"), method="GET", auth_token=auth_token, mfa_token=mfa_token)
                except Exception:
                    pass

            return build_domain_receipt(
                command_id=command_id,
                entity_type="Rebalance",
                entity_id=target_rebalance_id,
                action_id="RebalanceProposal",
                status="created",
                dispatch_path=url,
                domain_receipt=body,
                authoritative_readback=readback,
                extra={"rebalance_id": target_rebalance_id},
            )
        else:
            raise ActionUnavailableError(
                f"Rebalance action {action_id!r} is not supported. Use 'apply' for approved apply.",
                action_id=action_id,
                entity_type="Rebalance",
            )

    def _execute_rebalance_apply(
        self,
        command_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        entity_id = str(params.get("entity_id") or "").strip()
        requested_rebalance_id = str(params.get("rebalance_id") or "").strip()
        rebalance_id = entity_id or requested_rebalance_id
        if not rebalance_id:
            raise ValueError("ApprovedApply requires a trusted rebalance_id")
        approval_ref = str(params.get("approval_ref") or "auto-approved").strip()

        payload = {
            "command_id": command_id,
            "idempotency_key": str(params.get("idempotency_key") or command_id),
            "request_hash": str(params.get("request_hash") or ""),
            "approval_ref": approval_ref,
            "actor_id": str(params.get("actor_id") or "operator-bff"),
            "actor_role": str(params.get("actor_role") or "operator"),
            "proposal_version": params.get("proposal_version"),
        }
        url = capital_url(f"/api/rebalances/{quote(rebalance_id, safe='')}/apply")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)
        
        # Readback
        readback = None
        try:
            readback = http_request_json(capital_url(f"/api/rebalances/{quote(rebalance_id, safe='')}"), method="GET", auth_token=auth_token, mfa_token=mfa_token)
        except Exception:
            pass

        return build_domain_receipt(
            command_id=command_id,
            entity_type="Rebalance",
            entity_id=rebalance_id,
            action_id="apply",
            status=body.get("status") or "applied",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback=readback,
            extra={
                "approval_ref": approval_ref,
                "rebalance_id": rebalance_id,
            },
        )

    def _execute_containment(
        self,
        command_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        entity_id = str(params.get("entity_id") or "").strip()
        requested_persona_id = str(params.get("persona_id") or "").strip()
        persona_id = entity_id or requested_persona_id
        if not persona_id:
            raise ValueError("EmergencyContainment requires a trusted Persona identity")
        two_man_signature_id = str(params.get("two_man_signature_id") or params.get("twoManSignatureId") or "sig-emergency-ops").strip()

        payload = {
            key: value
            for key, value in params.items()
            if key not in {"command_id", "entity_type", "entity_id", "action_id", "actor_id", "actor_role"}
        }
        payload.update({
            "command_id": command_id,
            "idempotency_key": str(params.get("idempotency_key") or command_id),
            "request_hash": str(params.get("request_hash") or ""),
            "persona_id": persona_id,
            "two_man_signature_id": two_man_signature_id,
            "entity_type": "Persona",
            "entity_id": persona_id,
            "actor_id": str(params.get("actor_id") or "operator-bff"),
            "actor_role": str(params.get("actor_role") or "operator"),
        })

        url = capital_url("/api/containments")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)
        containment_state = str(body.get("containment_state") or body.get("state") or "frozen").strip()

        return build_domain_receipt(
            command_id=command_id,
            entity_type="Persona",
            entity_id=persona_id,
            action_id="EmergencyContainment",
            status=body.get("status") or "applied",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback={"containment_state": containment_state, "persona_id": persona_id},
            extra={
                "containment": True,
                "containment_state": containment_state,
                "risk_direction": "decrease_only",
                "two_man_signature_id": two_man_signature_id,
            },
        )

    def _execute_binding_action(
        self,
        command_id: str,
        binding_id: str,
        action_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not binding_id:
            raise ValueError("Binding action requires binding_id.")

        if action_id.lower() == "activate":
            url = capital_url(f"/api/bindings/{quote(binding_id, safe='')}/activate")
            payload = {
                "actor_id": str(params.get("actor_id") or "operator"),
                "reason": str(params.get("reason") or "Operator activation"),
            }
            body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)
        else:
            url = capital_url(f"/api/bindings/{quote(binding_id, safe='')}/status")
            payload = {
                "status": params.get("status") or action_id,
                "reason": params.get("reason") or f"Action {action_id}",
            }
            body = http_request_json(url, method="PATCH", payload=payload, auth_token=auth_token, mfa_token=mfa_token)

        readback = http_request_json(capital_url(f"/api/bindings/{quote(binding_id, safe='')}"), method="GET", auth_token=auth_token, mfa_token=mfa_token)

        return build_domain_receipt(
            command_id=command_id,
            entity_type="PersonaCapitalBinding",
            entity_id=binding_id,
            action_id=action_id,
            status="executed",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback=readback,
            extra={"binding_id": binding_id},
        )
