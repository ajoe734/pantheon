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
        return command_type in {"AdvanceLifecycle", "PersonaAction", "RetirePaperPersonaCase"}

    def execute(
        self, command_id: str, command_type: str, params: Dict[str, Any],
        auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        if command_type == "RetirePaperPersonaCase":
            return self._retire_paper_persona_case(command_id, params, auth_token, mfa_token)
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

    def _retire_paper_persona_case(
        self, command_id: str, params: Dict[str, Any],
        auth_token: Optional[str], mfa_token: Optional[str],
    ) -> Dict[str, Any]:
        from ..personas import service as persona_service

        authorization = f"Bearer {auth_token}" if auth_token else ""
        identity = extract_identity_jwt(authorization, mfa_token=mfa_token)
        tenant_id = str(identity.claims.get("tenant_id") or "").strip()
        if not tenant_id:
            raise ActionUnavailableError(
                "Persona case retirement requires the caller's tenant claim",
                action_id="RetirePaperPersonaCase", entity_type="Persona",
                error_code="tenant_required", downstream_status=403,
            )
        persona_id = str(params["persona_id"])
        reason = str(params.get("reason") or "").strip()

        plan = persona_service.plan_persona_provisioning_case_retirement(tenant_id, persona_id)
        assert_case_retirable(plan)
        summary = persona_service.retire_persona_provisioning_case(
            tenant_id, persona_id, reason=reason
        )
        if summary is None:
            assert_case_retirable(None)
        return build_domain_receipt(
            command_id=command_id, entity_type="Persona", entity_id=persona_id,
            action_id="RetirePaperPersonaCase", status="executed",
            dispatch_path="persona_provisioning.retire_case",
            domain_receipt=summary, authoritative_readback=summary,
        )


def assert_case_retirable(plan: Optional[Dict[str, Any]]) -> None:
    """Refuse a missing ledger or a release case before any owner write."""

    if plan is None:
        raise ActionUnavailableError(
            "Persona has no provisioning ledger",
            action_id="RetirePaperPersonaCase", entity_type="Persona",
            error_code="persona_case_not_found", downstream_status=404,
        )
    if plan.get("release_case"):
        raise ActionUnavailableError(
            "Release cases are retired by the release retire script",
            action_id="RetirePaperPersonaCase", entity_type="Persona",
            error_code="release_case_use_release_retire", downstream_status=409,
            suggestion="Use scripts/retire_dev_paper_release_cases.py for dev-paper-release cases.",
        )
