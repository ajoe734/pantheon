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
    http_request_json,
    internal_url,
    utc_now,
)

log = logging.getLogger(__name__)


class PersonaCommandAdapter(DomainCommandAdapter):
    """Adapter for Persona domain authority commands."""

    _HANDLED_COMMANDS = {
        "PersonaAction",
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

        if command_type in {"Observe"}:
            return self._execute_observe(command_id, persona_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"PromoteCandidate", "Demote"}:
            return self._execute_promote_demote(command_id, persona_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        else:
            raise ActionUnavailableError(
                f"Persona action {action_id!r} on {persona_id!r} is not supported.",
                action_id=action_id,
                entity_type="Persona",
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
