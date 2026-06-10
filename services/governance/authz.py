"""Small authorization policy surface used by service-to-service callers."""
from __future__ import annotations

from typing import Any, Dict, Iterable


MEMORY_RETRIEVE_ACTION = "memory.retrieve"
_INSTITUTIONAL_READ_ROLES = {
    "operator",
    "admin",
    "reviewer",
    "auditor",
    "persona_session",
    "research_session",
    "trainer_session",
    "consultation_session",
    "automated_gate",
}
_PERSONA_SESSION_ROLES = {
    "persona_session",
    "research_session",
    "trainer_session",
    "consultation_session",
}
_PERSONA_OPERATOR_ROLES = {
    "operator",
    "admin",
    "reviewer",
    "auditor",
}
_VALID_MEMORY_SCOPES = {"institutional", "persona", "both"}


def _normalized_roles(roles: Iterable[str]) -> set[str]:
    return {str(role).strip().lower() for role in roles if str(role).strip()}


def _deny(reason: str) -> Dict[str, Any]:
    return {"allowed": False, "reason": reason}


def _allow(reason: str) -> Dict[str, Any]:
    return {"allowed": True, "reason": reason}


def evaluate_authz_request(
    *,
    action: str,
    actor_id: str,
    actor_roles: Iterable[str],
    resource: Dict[str, Any] | None = None,
    context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Evaluate a minimal service AuthZ request.

    The first production consumer is the memory retrieval facade. It is
    intentionally fail-closed for persona-scoped reads: a persona session can
    only read memory for its own persona unless a future policy explicitly
    grants a cross-persona exception.
    """

    if action != MEMORY_RETRIEVE_ACTION:
        return _deny("unsupported_action")
    if not str(actor_id or "").strip():
        return _deny("missing_actor_id")

    roles = _normalized_roles(actor_roles)
    if not roles:
        return _deny("missing_actor_roles")

    resource = resource or {}
    context = context or {}
    session_id = str(context.get("session_id") or "").strip()
    if not session_id:
        return _deny("missing_session_id")

    memory_scope = str(resource.get("scope") or "institutional").strip().lower()
    if memory_scope not in _VALID_MEMORY_SCOPES:
        return _deny("invalid_memory_scope")

    if memory_scope in {"institutional", "both"} and not roles.intersection(_INSTITUTIONAL_READ_ROLES):
        return _deny("institutional_memory_role_denied")

    if memory_scope in {"persona", "both"}:
        persona_id = str(resource.get("persona_id") or "").strip()
        session_persona_id = str(
            context.get("session_persona_id")
            or context.get("persona_id")
            or ""
        ).strip()
        if not persona_id:
            return _deny("missing_resource_persona_id")
        has_persona_session_role = bool(roles.intersection(_PERSONA_SESSION_ROLES))
        has_persona_operator_role = bool(roles.intersection(_PERSONA_OPERATOR_ROLES))
        if not has_persona_session_role and not has_persona_operator_role:
            return _deny("persona_memory_role_denied")
        if has_persona_session_role:
            if not session_persona_id:
                return _deny("missing_session_persona_id")
            if session_persona_id != persona_id:
                return _deny("persona_scope_mismatch")
        if "consultation_session" in roles:
            relevance_scope = str(resource.get("relevance_scope") or "").strip()
            if relevance_scope != "persona_and_committee":
                return _deny("consultation_scope_denied")

    return _allow("authorized")
