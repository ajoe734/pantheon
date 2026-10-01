"""Forward lifecycle commands to Persona's authenticated write authority."""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..auth.policy import extract_identity_jwt
from ..ports.persona_write_owner import (
    PersonaWriteOwnerUnavailable,
    _PersonaHttpResponseError,
    create_persona_registry_write_owner,
)
from .base import ActionUnavailableError, DomainCommandAdapter, build_domain_receipt


class PersonaCommandAdapter(DomainCommandAdapter):
    def can_handle(self, command_type: str, entity_type: str, action_id: str) -> bool:
        return command_type in {"AdvanceLifecycle", "PersonaAction"}

    def execute(
        self, command_id: str, command_type: str, params: Dict[str, Any],
        auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        if command_type != "AdvanceLifecycle":
            raise ActionUnavailableError("Unsupported Persona action", entity_type="Persona")
        authorization = f"Bearer {auth_token}" if auth_token else ""
        identity = extract_identity_jwt(authorization, mfa_token=mfa_token)
        actor_id = str(identity.claims["sub"])
        tenant_id = str(identity.claims.get("tenant_id") or "").strip()
        persona_id = params["persona_id"]
        try:
            readback = create_persona_registry_write_owner().advance_lifecycle(
                persona_id, actor_id=actor_id, target_state=params["target_state"],
                governance_decision_id=params.get("governance_decision_id"),
                authorization=authorization, expected_tenant_id=tenant_id,
            )
        except (_PersonaHttpResponseError, PersonaWriteOwnerUnavailable) as exc:
            status = getattr(exc, "status_code", 503)
            raise ActionUnavailableError(
                "Persona lifecycle owner rejected or could not confirm the transition",
                action_id="AdvanceLifecycle", entity_type="Persona",
                downstream_status=status, retryable=status >= 500,
            ) from exc
        return build_domain_receipt(
            command_id=command_id, entity_type="Persona", entity_id=persona_id,
            action_id="AdvanceLifecycle", status="executed",
            dispatch_path="persona_service.lifecycle", domain_receipt=readback,
            authoritative_readback=readback,
        )
