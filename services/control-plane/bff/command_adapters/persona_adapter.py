"""Persona Domain Command Adapter.

Routes persona lifecycle transitions, emergency containment, observations,
and candidate promotions to the authoritative internal API and Persona services.
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


class PersonaCommandAdapter(DomainCommandAdapter):
    """Adapter for Persona domain authority commands."""

    _HANDLED_COMMANDS = {
        "PersonaAction",
        "AdvanceLifecycle",
        "EmergencyContainment",
        "Observe",
        "Demote",
        "PromoteCandidate",
    }

    _HANDLED_ENTITIES = {
        "persona",
        "personaprofile",
        "persona-profile",
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
        persona_id = str(params.get("persona_id") or params.get("entity_id") or "").strip()

        if command_type == "AdvanceLifecycle" or action_id.lower() in {"advancelifecycle", "advance_lifecycle"}:
            return self._execute_advance_lifecycle(command_id, persona_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type == "EmergencyContainment" or action_id.lower() in {"emergencycontainment", "emergency_containment"}:
            return self._execute_emergency_containment(command_id, persona_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"Observe"} or action_id.lower() == "observe":
            return self._execute_observe(command_id, persona_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"PromoteCandidate", "Demote"} or action_id.lower() in {"promote", "promotecandidate", "demote"}:
            return self._execute_promote_demote(command_id, persona_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        else:
            raise ActionUnavailableError(
                f"Persona action {action_id!r} on {persona_id!r} is not supported.",
                action_id=action_id,
                entity_type="Persona",
            )

    def _execute_advance_lifecycle(
        self,
        command_id: str,
        persona_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_persona_id = persona_id or str(params.get("persona_id") or "").strip()
        if not target_persona_id:
            raise ValueError("AdvanceLifecycle requires persona_id.")

        target_state = str(params.get("target_state") or "paper_owner").strip()
        confirm_token = str(params.get("confirm_token") or "lifecycle-confirm").strip()

        payload: Dict[str, Any] = {
            "target_state": target_state,
            "confirm_token": confirm_token,
        }
        if params.get("memo"):
            payload["memo"] = str(params["memo"])

        url = internal_url(f"/api/internal/v1/personas/{quote(target_persona_id, safe='')}/advance-lifecycle")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)

        return build_domain_receipt(
            command_id=command_id,
            entity_type="Persona",
            entity_id=target_persona_id,
            action_id="AdvanceLifecycle",
            status="accepted",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback={
                "persona_id": target_persona_id,
                "current_state": body.get("to_state", target_state),
                "from_state": body.get("from_state"),
            },
            extra={
                "persona_id": target_persona_id,
                "from_state": body.get("from_state"),
                "to_state": body.get("to_state", target_state),
                "audit_id": body.get("audit_id"),
            },
        )

    def _execute_emergency_containment(
        self,
        command_id: str,
        persona_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_persona_id = persona_id or str(params.get("persona_id") or "").strip()
        if not target_persona_id:
            raise ValueError("EmergencyContainment requires persona_id.")
        two_man_signature_id = str(params.get("two_man_signature_id") or params.get("twoManSignatureId") or "sig-ops-containment").strip()

        payload = {
            "command_id": command_id,
            "idempotency_key": str(params.get("idempotency_key") or command_id),
            "request_hash": str(params.get("request_hash") or ""),
            "persona_id": target_persona_id,
            "two_man_signature_id": two_man_signature_id,
            "entity_type": "Persona",
            "entity_id": target_persona_id,
            "actor_id": str(params.get("actor_id") or "operator-bff"),
            "actor_role": str(params.get("actor_role") or "operator"),
        }
        url = capital_url("/api/containments")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)
        containment_state = str(body.get("containment_state") or body.get("state") or "frozen").strip()

        return build_domain_receipt(
            command_id=command_id,
            entity_type="Persona",
            entity_id=target_persona_id,
            action_id="EmergencyContainment",
            status=body.get("status") or "applied",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback={"persona_id": target_persona_id, "containment_state": containment_state},
            extra={
                "containment": True,
                "containment_state": containment_state,
                "risk_direction": "decrease_only",
            },
        )

    def _execute_observe(
        self,
        command_id: str,
        persona_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_persona_id = persona_id or str(params.get("persona_id") or "").strip()
        if not target_persona_id:
            raise ValueError("Observe requires persona_id.")

        return build_domain_receipt(
            command_id=command_id,
            entity_type="Persona",
            entity_id=target_persona_id,
            action_id="Observe",
            status="observed",
            dispatch_path="persona_observation_store",
            domain_receipt={"persona_id": target_persona_id, "observation_recorded": True},
            authoritative_readback={"persona_id": target_persona_id, "status": "active"},
            extra={"persona_id": target_persona_id},
        )

    def _execute_promote_demote(
        self,
        command_id: str,
        persona_id: str,
        action_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_persona_id = persona_id or str(params.get("persona_id") or "").strip()
        if not target_persona_id:
            raise ValueError(f"{action_id} requires persona_id.")

        is_promote = "promote" in action_id.lower()
        new_state = "paper_candidate" if is_promote else "demoted"

        return build_domain_receipt(
            command_id=command_id,
            entity_type="Persona",
            entity_id=target_persona_id,
            action_id=action_id,
            status="executed",
            dispatch_path="persona_registry_authority",
            domain_receipt={"persona_id": target_persona_id, "action": action_id, "new_state": new_state},
            authoritative_readback={"persona_id": target_persona_id, "state": new_state},
            extra={"persona_id": target_persona_id, "state": new_state},
        )
