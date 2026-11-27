"""Agora servant router — servant persona profile (agora.servant.v1).

New routes registered here:
  POST /bff/agora/servant/ensure   — AG-BE-ID-002

Migration note: All other identity routes are currently in main.py.
This module is the intended home for servant-specific logic.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Callable, Dict, Iterable, Iterator, Literal, Mapping, Optional

from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from .clarification import (
    extract_clarification_choices_from_messages,
    extract_clarification_habit_facts_from_messages,
    latest_pending_clarification,
    resolve_servant_clarification_choice,
)
from .data_tools import normalize_command_context
from .institutional_knowledge import (
    InstitutionalKnowledgeReader,
    InstitutionalKnowledgeUnavailableError,
)
from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
from ..models import (
    AGORA_CAPABILITIES,
    AgoraEnvelope,
    AgoraMeta,
    AgoraServantPolicy,
    ServantCapabilitySummary,
    ServantProfile,
)
from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError
from services.control_plane.bff.personas.trader_paper_book import (
    PersonaProvisioningCoordinationError,
    ensure_trader_paper_book,
    read_trader_paper_book,
    resolve_trader_paper_starting_capital,
    validate_paper_only_stage,
    validate_servant_persona_paper_ceiling,
)
from services.control_plane.bff.ports.persona_write_owner import (
    PersonaWriteConflict,
    PersonaWriteOwnerUnavailable,
)


_SERVANT_CAPABILITY = "agora.servant.v1"
_SERVANT_SESSION_CAPABILITY = "agora.session.v1"
_PERSONA_OPINION_CAPABILITY = "persona_opinion"
_SERVANT_POLICY_REFS = [
    "docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/03_servant_and_workshop_contracts.md",
]
_PROHIBITED_AUTHORITY = ("runtime_binding", "broker_order", "capital_binding")
_PROFILE_STATUSES = {"active", "suspended", "paper_only", "shadow_only", "retired"}
_OPENCLAW_DEGRADED_CODE = "OPENCLAW_UPSTREAM_DEGRADED"
_UNSET_SERVANT_OWNER = object()
# Mirrors services.agora.store.SERVANT_MESSAGE_MAX_CHARS (the owner enforces it); the request
# rejects a longer message instead of letting the owner truncate what the trader wrote.
_SERVANT_MESSAGE_MAX_CHARS = 4000


class ServantSessionCreateRequest(BaseModel):
    """v1.2 ServantSessionCreateRequest.

    Mirrors services/control-plane/openapi/agora_v1_2.openapi.yaml. The body is
    optional at the route layer; an omitted body is equivalent to `{}` and
    therefore defaults to an interactive session.
    """

    model_config = {"extra": "forbid"}

    intent: Optional[str] = None
    strategy_ref: Optional[str] = None
    session_type: Literal["interactive", "trainer", "research_task"] = "interactive"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ServantCommandContext(BaseModel):
    """Command-bar context (AGORA-SERVANT-DATA-TOOLS-20261127, SD §4.3).

    The command bar and the conversation share one route; 「分析分點」 sends
    ``quick_task=analyze_broker_branches`` with the selected symbols.
    """

    model_config = {"extra": "forbid"}

    page: Optional[str] = Field(default=None, max_length=80)
    strategy_id: Optional[str] = Field(default=None, max_length=120)
    widget_id: Optional[str] = Field(default=None, max_length=120)
    selected_symbols: list[str] = Field(default_factory=list, max_length=20)
    quick_task: Optional[str] = Field(default=None, max_length=64)


class ServantClarificationChoice(BaseModel):
    """Trader choice answering a servant clarification card (AGORA-SERVANT-CLARIFY-20261127, SD §5)."""

    model_config = {"extra": "forbid"}

    decision_id: Optional[str] = Field(default=None, max_length=120)
    question_id: Optional[str] = Field(default=None, max_length=120)
    option_id: Optional[str] = Field(default=None, max_length=64)
    selected_option_id: Optional[str] = Field(default=None, max_length=64)
    option_label: Optional[str] = Field(default=None, max_length=500)
    custom_response: Optional[str] = Field(default=None, max_length=1000)
    use_servant_default: bool = False
    resolves: list[str] = Field(default_factory=list, max_length=12)


class ServantMessageRequest(BaseModel):
    """v1/v1.2 ServantMessageRequest (v1.14 adds command-bar ``context`` and ``clarification_choice``)."""

    model_config = {"extra": "forbid"}

    content: str = Field(default="", max_length=_SERVANT_MESSAGE_MAX_CHARS)
    attachment_refs: list[str] = Field(default_factory=list)
    context: Optional[ServantCommandContext] = None
    clarification_choice: Optional[ServantClarificationChoice] = None

    @model_validator(mode="after")
    def _require_content_or_choice(self) -> "ServantMessageRequest":
        if not self.content and self.clarification_choice is None:
            raise ValueError("content must be non-empty when clarification_choice is not provided")
        return self


class ServantPaperBookEnsureRequest(BaseModel):
    """Request payload for POST /bff/agora/servant/paper-book/ensure."""

    model_config = {"extra": "forbid"}

    target_stage: str = "paper"
    stage: Optional[str] = None
    deployment_stage: Optional[str] = None
    deployment_mode: Optional[str] = None
    allowed_deployment_scope: Optional[str] = None
    environment: Optional[str] = None
    environment_ceiling: Optional[str] = None
    execution_mode: Optional[str] = None


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _new_prefixed_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _request_id(value: Optional[str]) -> str:
    clean = str(value or "").strip()
    return clean or _new_prefixed_id("req")


def _trace_id(value: Optional[str]) -> str:
    clean = str(value or "").strip()
    return clean or _new_prefixed_id("trace")


def _audit_fields(
    *,
    scope: Any,
    persona_id: str,
    session_id: str,
    request_id: str,
    trace_id: str,
) -> Dict[str, str]:
    return {
        "trace_id": trace_id,
        "request_id": request_id,
        "actor_id": str(getattr(scope, "operator_id", "") or ""),
        "user_id": str(getattr(scope, "user_id", "") or ""),
        "persona_id": str(persona_id or ""),
        "session_id": str(session_id or ""),
    }


def _default_servant_session_owner() -> Optional[Any]:
    """Agora owner holding servant conversations; None (fail closed, 503) if unreachable."""
    import logging
    import os

    if not (os.getenv("AGORA_STORE_DSN") or os.getenv("DATABASE_URL")):
        return None
    try:
        from services.agora.service import build_agora_write_service

        return build_agora_write_service()
    except Exception:  # messages then fail closed with 503
        logging.getLogger(__name__).exception("agora servant session owner unavailable")
        return None


_default_owner_cache: Dict[str, Any] = {}


def _lazy_default_servant_session_owner() -> Optional[Any]:
    """Build the production owner on first use (not at app import) and keep it."""
    owner = _default_owner_cache.get("owner")
    if owner is None:
        owner = _default_servant_session_owner()
        if owner is not None:
            _default_owner_cache["owner"] = owner
    return owner


def _stable_digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]


def _message_event(session_id: str, message: Mapping[str, Any]) -> Dict[str, Any]:
    """Project one durable owner message into the SSE event the trader's stream replays."""
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    base = {
        "session_id": session_id,
        "message_id": message.get("id"),
        "created_at": message.get("createdAt"),
        "audit": metadata.get("audit") or {},
    }
    if message.get("role") == "assistant":
        tool_turn = metadata.get("tool_turn") if isinstance(metadata.get("tool_turn"), dict) else {}
        status = str(metadata.get("provider_status") or "completed")
        degraded = status == "degraded"
        clarification = metadata.get("clarification") or tool_turn.get("clarification") or None
        stated_assumptions = list(tool_turn.get("stated_assumptions") or [])
        return {
            "id": str(message.get("id")),
            "type": "servant.message.degraded" if degraded else "servant.message.completed",
            "data": {
                **base,
                "turn_id": metadata.get("turn_id"),
                "reply_to": metadata.get("reply_to"),
                "provider_status": status,
                "answer": None if degraded else message.get("content"),
                "error": metadata.get("error"),
                "truncated": bool(message.get("truncated")),
                # AGORA-SERVANT-DATA-TOOLS-20261127: every number shown is one of these cited facts.
                "citations": list(message.get("citations") or []),
                "missing_data": list(tool_turn.get("missing_data") or []),
                "tool_rounds": int(tool_turn.get("tool_rounds") or 0),
                "answer_mode": tool_turn.get("answer_mode"),
                # AGORA-SERVANT-CLARIFY-20261127: typed clarification card (WorkshopNextDecisionV114 shape).
                "clarification": clarification,
                "cards": [{"card_type": "clarification", **clarification}] if isinstance(clarification, dict) else [],
                "stated_assumptions": stated_assumptions,
            },
        }
    return {
        "id": str(message.get("id")),
        "type": "servant.message.accepted",
        "data": {
            **base,
            "role": message.get("role"),
            "content": message.get("content"),
            "attachment_refs": [
                a.get("ref") if isinstance(a, dict) else a for a in message.get("attachments") or []
            ],
            "context": metadata.get("command_context") or None,
            **({"clarification_choice": metadata["clarification_choice"]} if metadata.get("clarification_choice") else {}),
            **({"habit_fact": metadata["habit_fact"]} if metadata.get("habit_fact") else {}),
        },
    }


def _events_for_session(
    owner: Any, session: Mapping[str, Any], last_event_id: Optional[str] = None
) -> list[Dict[str, Any]]:
    """Replayable event list built from the Agora owner (survives a BFF restart)."""
    session_id = str(session.get("id"))
    events: list[Dict[str, Any]] = [
        {
            "id": f"{session_id}:created",
            "type": "servant.session.created",
            "data": {
                "session_id": session_id,
                "session_type": session.get("sessionType") or "interactive",
                "servant_persona_id": session.get("personaId"),
                "created_at": session.get("createdAt"),
            },
        }
    ]
    messages = [m for m in session.get("messages") or [] if isinstance(m, dict)]
    answered = {
        (m.get("metadata") or {}).get("turn_id") for m in messages if m.get("role") == "assistant"
    }
    for m in messages:
        events.append(_message_event(session_id, m))
        meta = m.get("metadata") if isinstance(m.get("metadata"), dict) else {}
        choice = meta.get("clarification_choice")
        if m.get("role") == "user" and isinstance(choice, dict):
            events.append(
                {
                    "id": f"{m.get('id')}:clarification",
                    "type": "servant.clarification.answered",
                    "data": {
                        "session_id": session_id,
                        "message_id": m.get("id"),
                        "created_at": m.get("createdAt"),
                        "audit": meta.get("audit") or {},
                        **choice,
                        "habit_fact": meta.get("habit_fact") or None,
                    },
                }
            )
    for turn in owner.list_servant_turns(session_id, ("queued", "running")):
        if turn.get("turn_id") in answered:
            continue
        events.append(
            {
                "id": f"{turn.get('turn_id')}:{turn.get('status')}",
                "type": "servant.message.queued",
                "data": {
                    "session_id": session_id,
                    "turn_id": turn.get("turn_id"),
                    "message_id": turn.get("user_message_id"),
                    "status": turn.get("status"),
                },
            }
        )
    if session.get("status") == "closed":
        events.append(
            {
                "id": f"{session_id}:terminated",
                "type": "servant.session.terminated",
                "data": {"session_id": session_id, "status": session.get("outcome") or "canceled"},
            }
        )
    if not last_event_id:
        return events
    for idx, event in enumerate(events):
        if event.get("id") == last_event_id:
            return events[idx + 1 :]
    # A "<turn>:queued|running" marker disappears once the answer lands: resume at the answer.
    turn_id = last_event_id.rsplit(":", 1)[0]
    for idx, event in enumerate(events):
        if (event.get("data") or {}).get("turn_id") == turn_id and event["type"].startswith(
            "servant.message."
        ) and event["type"] != "servant.message.queued":
            return events[idx:]
    return events


def _format_sse(event: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            f"id: {event.get('id')}",
            f"event: {event.get('type')}",
            f"data: {json.dumps(event.get('data') or {}, ensure_ascii=True)}",
            "",
            "",
        ]
    )


def _string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        raw = [part.strip() for part in value.replace(",", " ").split()]
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, dict)):
        raw = [str(item).strip() for item in value if str(item).strip()]
    else:
        raw = [str(value).strip()]
    result: list[str] = []
    seen = set()
    for item in raw:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _metadata(record: Mapping[str, Any]) -> Dict[str, Any]:
    meta = record.get("metadata")
    return dict(meta) if isinstance(meta, dict) else {}


def _record_value(record: Mapping[str, Any], *keys: str) -> str:
    meta = _metadata(record)
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value).strip()
        value = meta.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _stable_servant_persona_id(*, tenant_id: str, agora_user_id: str) -> str:
    digest = hashlib.sha256(
        f"{tenant_id}\0{agora_user_id}\0agora_servant".encode("utf-8")
    ).hexdigest()[:20]
    return f"agora-servant-{digest}"


def _stable_servant_capability_snapshot_id(*, persona_id: str) -> str:
    digest = hashlib.sha256(f"{persona_id}\0persona_opinion".encode("utf-8")).hexdigest()[:20]
    return f"cap-servant-{digest}"


def _allowed_agora_capabilities(scope: Any) -> list[str]:
    allowed = set(AGORA_CAPABILITIES)
    capabilities = []
    for capability in getattr(scope, "granted_capabilities", []) or []:
        clean = str(capability or "").strip()
        if clean in allowed and not any(token in clean for token in _PROHIBITED_AUTHORITY):
            capabilities.append(clean)
    return capabilities


def _capability_summary(scope: Any, record: Optional[Mapping[str, Any]] = None) -> ServantCapabilitySummary:
    metadata = _metadata(record or {})
    allowed = _allowed_agora_capabilities(scope)
    return ServantCapabilitySummary(
        can_ask="agora.session.v1" in allowed,
        can_research="agora.research.v1" in allowed,
        can_workshop="agora.workshop.v1" in allowed,
        can_shadow=False,
        asset_classes=_string_list(metadata.get("asset_classes") or metadata.get("assetClasses")),
        strategy_families=_string_list(metadata.get("strategy_families") or metadata.get("strategyFamilies")),
        allowed_agora_capabilities=allowed,
    )


def _servant_status(record: Mapping[str, Any]) -> str:
    metadata = _metadata(record)
    raw = str(
        metadata.get("servant_status")
        or record.get("status")
        or record.get("lifecycle_state")
        or metadata.get("status")
        or ""
    ).strip().lower()
    if raw in _PROFILE_STATUSES:
        return raw
    if raw in {"paper", "paper_running", "paper-only"}:
        return "paper_only"
    if raw in {"shadow", "shadow_running", "shadow-only"}:
        return "shadow_only"
    if raw in {"paused", "blocked"}:
        return "suspended"
    if raw in {"archived"}:
        return "retired"
    return "active"


def _base_servant_metadata(
    *,
    scope: Any,
    capability_summary: ServantCapabilitySummary,
    policy: AgoraServantPolicy,
    capability_snapshot_id: str,
    now: str,
    existing: Optional[Mapping[str, Any]] = None,
    sync_result: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    metadata = _metadata(existing or {})
    starting_capital = resolve_trader_paper_starting_capital(as_of=now)
    metadata.update(
        {
            "tenant_id": scope.tenant_id,
            "tenantId": scope.tenant_id,
            "agora_user_id": scope.user_id,
            "agoraUserId": scope.user_id,
            "persona_class": "agora_servant",
            "owner_scope": "user_private",
            "visibility_scope": "private",
            "memory_scope": "private_user",
            "deployment_stage": "paper",
            "environment_ceiling": "paper",
            "interaction_capabilities": [_PERSONA_OPINION_CAPABILITY],
            "capability_snapshot_id": capability_snapshot_id,
            "execution_authority": "none",
            "capability_summary": capability_summary.model_dump(),
            "policy": policy.model_dump(),
            "policy_refs": list(_SERVANT_POLICY_REFS),
            "starting_capital": starting_capital,
            "last_reconciled_at": now,
            "provisioned_by": "POST /bff/agora/servant/ensure",
        }
    )
    if sync_result:
        metadata["openclaw_agent"] = {
            "status": str(sync_result.get("status") or "unknown"),
            "agent_id": str(sync_result.get("agent_id") or ""),
            "model_id": str(sync_result.get("model_id") or ""),
            "model": str(sync_result.get("model") or ""),
            "workspace_ref": str(sync_result.get("workspace_ref") or ""),
            "last_synced_at": now,
        }
    return metadata


def _is_servant_for_scope(
    record: Mapping[str, Any],
    *,
    tenant_id: str,
    agora_user_id: str,
    expected_persona_id: str,
) -> bool:
    persona_id = str(record.get("persona_id") or record.get("id") or "").strip()
    persona_class = _record_value(record, "persona_class", "personaClass")
    record_tenant = _record_value(record, "tenant_id", "tenantId")
    record_user = _record_value(record, "agora_user_id", "agoraUserId", "user_id", "userId")
    exact_id = persona_id == expected_persona_id
    if persona_class and persona_class != "agora_servant":
        return False
    if record_tenant and record_tenant != tenant_id:
        return False
    if record_user and record_user != agora_user_id:
        return False
    return exact_id or (
        persona_class == "agora_servant"
        and record_tenant == tenant_id
        and record_user == agora_user_id
    )


def _find_servant_persona(
    read_store: Any,
    *,
    tenant_id: str,
    agora_user_id: str,
    expected_persona_id: str,
) -> Optional[Dict[str, Any]]:
    get_persona = getattr(read_store, "get_persona", None)
    if callable(get_persona):
        exact = get_persona(expected_persona_id)
        if isinstance(exact, dict) and _is_servant_for_scope(
            exact,
            tenant_id=tenant_id,
            agora_user_id=agora_user_id,
            expected_persona_id=expected_persona_id,
        ):
            return _json_clone(exact)
    list_personas = getattr(read_store, "list_personas", None)
    if not callable(list_personas):
        return None
    for record in list_personas() or []:
        if isinstance(record, dict) and _is_servant_for_scope(
            record,
            tenant_id=tenant_id,
            agora_user_id=agora_user_id,
            expected_persona_id=expected_persona_id,
        ):
            return _json_clone(record)
    return None


def _create_servant_persona(
    write_owner: Any,
    *,
    persona_id: str,
    scope: Any,
    now: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    create_persona = getattr(write_owner, "create_persona", None)
    if not callable(create_persona):
        raise PersonaWriteOwnerUnavailable(
            "persona_registry_write_owner",
            "Persona Registry writer does not support Persona creation",
        )
    return create_persona(
        persona_id=persona_id,
        name="Agora Servant",
        actor_id=scope.operator_id,
        created_at=now,
        archetype="agora_servant",
        # Admission is completed only after the OpenClaw identity sync and the
        # explicit capability snapshot both succeed.
        lifecycle_state="draft",
        risk_level="low",
        mandate="user_private_agora_servant",
        strategy_family="agora_servant",
        traits={
            "decision_style": "operator-guided",
            "hard_rules": "no runtime binding, broker order, or capital binding authority",
            "persona_voice": "concise, evidence-grounded",
        },
        metadata=metadata,
    )


def _update_servant_persona(
    write_owner: Any,
    *,
    persona_id: str,
    scope: Any,
    now: str,
    existing: Mapping[str, Any],
    metadata: Dict[str, Any],
    activate: bool = False,
) -> Dict[str, Any]:
    update_persona = getattr(write_owner, "update_persona", None)
    if not callable(update_persona):
        raise PersonaWriteOwnerUnavailable(
            "persona_registry_write_owner",
            "Persona Registry writer does not support Persona updates",
        )
    current_status = _servant_status(existing)
    lifecycle_state: Optional[str] = None
    if activate:
        metadata = dict(metadata)
        metadata["servant_status"] = (
            current_status
            if current_status in {"suspended", "retired"}
            else "paper_only"
        )
        lifecycle_state = metadata["servant_status"]
    updated = update_persona(
        persona_id,
        name=str(existing.get("name") or "Agora Servant"),
        actor_id=scope.operator_id,
        updated_at=now,
        archetype=None,
        lifecycle_state=lifecycle_state,
        risk_level=str(_metadata(existing).get("risk_level") or "low"),
        metadata=metadata,
    )
    return _json_clone(updated or existing)


def _persona_owner_call(
    call: Callable[[], Any],
    *,
    dependency: str,
    bff_error: Callable[..., HTTPException],
) -> Any:
    from services.control_plane.bff.models import ErrorCode

    try:
        return call()
    except PersonaWriteConflict as exc:
        raise bff_error(
            409,
            ErrorCode.RESOURCE_CONFLICT,
            "Persona write owner rejected divergent semantics",
            str(exc)[:300],
            precondition_failed=dependency,
        ) from exc
    except PersonaWriteOwnerUnavailable as exc:
        failed_dependency = str(exc.dependency or dependency)
        raise bff_error(
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Persona write owner is unavailable",
            str(exc.reason)[:300],
            precondition_failed=failed_dependency,
            details_extra={"dependency": failed_dependency},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise bff_error(
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Persona write owner is unavailable",
            str(exc)[:300] or type(exc).__name__,
            precondition_failed=dependency,
            details_extra={"dependency": dependency},
        ) from exc


def _profile_from_persona(
    record: Mapping[str, Any],
    *,
    scope: Any,
    capability_summary: ServantCapabilitySummary,
    policy: AgoraServantPolicy,
) -> ServantProfile:
    metadata = _metadata(record)
    profile_metadata: Dict[str, Any] = {
        "identity_key": {
            "tenant_id": scope.tenant_id,
            "agora_user_id": scope.user_id,
            "persona_class": "agora_servant",
        },
        "openclaw_agent": _json_clone(metadata.get("openclaw_agent") or {}),
        "policy_refs": list(metadata.get("policy_refs") or _SERVANT_POLICY_REFS),
        "starting_capital": _json_clone(
            metadata.get("starting_capital") or resolve_trader_paper_starting_capital()
        ),
    }
    if isinstance(metadata.get("paper_book"), Mapping):
        profile_metadata["paper_book"] = _json_clone(dict(metadata["paper_book"]))
    return ServantProfile(
        persona_id=str(record.get("persona_id") or record.get("id") or ""),
        display_name=str(record.get("name") or "Agora Servant"),
        status=_servant_status(record),
        tenant_id=str(metadata.get("tenant_id") or metadata.get("tenantId") or scope.tenant_id),
        agora_user_id=str(metadata.get("agora_user_id") or metadata.get("agoraUserId") or scope.user_id),
        capability_summary=capability_summary,
        policy=policy,
        description=metadata.get("description"),
        avatar_ref=metadata.get("avatar_ref") or metadata.get("avatarRef"),
        last_active_at=metadata.get("last_active_at") or metadata.get("lastActiveAt"),
        metadata=profile_metadata,
    )


def _require_header(value: Optional[str], name: str, bff_error: Callable[..., HTTPException]) -> str:
    clean = str(value or "").strip()
    if clean:
        return clean
    from services.control_plane.bff.models import ErrorCode

    raise bff_error(
        422,
        ErrorCode.VALIDATION_FAILED,
        f"{name} header is required",
        f"Missing required {name} header for Agora servant ensure",
        precondition_failed=name,
    )


def _raise_scope_error(exc: AgoraScopeResolutionError, bff_error: Callable[..., HTTPException]) -> None:
    from services.control_plane.bff.models import ErrorCode

    code = ErrorCode.AUTH_REQUIRED if exc.status_code == 401 else ErrorCode.FORBIDDEN
    raise bff_error(
        exc.status_code,
        code,
        exc.message,
        exc.reason,
        precondition_failed="agora_user_scope",
        details_extra=exc.details,
    )


def _raise_cross_user_session_forbidden(
    *,
    bff_error: Callable[..., HTTPException],
    audit: Mapping[str, Any],
) -> None:
    from services.control_plane.bff.models import ErrorCode

    raise bff_error(
        403,
        ErrorCode.FORBIDDEN,
        "Servant session is outside the current Agora user scope",
        "CROSS_USER_ACCESS_FORBIDDEN",
        precondition_failed="agora_user_scope",
        details_extra={"resource": "servant_session", "audit": dict(audit)},
    )


def _servant_profile_for_scope(
    *,
    read_store: Any,
    scope: Any,
    bff_error: Callable[..., HTTPException],
) -> ServantProfile:
    persona_id = _stable_servant_persona_id(
        tenant_id=scope.tenant_id,
        agora_user_id=scope.user_id,
    )
    existing = _find_servant_persona(
        read_store,
        tenant_id=scope.tenant_id,
        agora_user_id=scope.user_id,
        expected_persona_id=persona_id,
    )
    if existing is None:
        from services.control_plane.bff.models import ErrorCode

        raise bff_error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            "Agora servant is not provisioned",
            "Call POST /bff/agora/servant/ensure before opening servant sessions",
            precondition_failed="servant_profile",
        )
    policy = AgoraServantPolicy()
    capability_summary = _capability_summary(scope, existing)
    return _profile_from_persona(
        existing,
        scope=scope,
        capability_summary=capability_summary,
        policy=policy,
    )


def _scope_for_servant_session(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    authorization: Optional[str],
    x_tenant_id: Optional[str] = None,
    x_pantheon_tenant: Optional[str] = None,
) -> Any:
    identity = extract_identity(authorization)
    require_read_role(identity)
    try:
        return resolve_agora_user_scope(
            identity,
            utc_now=utc_now,
            requested_tenant_id=x_tenant_id or x_pantheon_tenant,
        )
    except AgoraScopeResolutionError as exc:
        _raise_scope_error(exc, bff_error)


def _openclaw_error_code(exc: OpenClawOpsClientError) -> Any:
    from services.control_plane.bff.models import ErrorCode

    if exc.status_code == 404:
        return ErrorCode.RESOURCE_NOT_FOUND
    if exc.status_code == 409:
        return ErrorCode.RESOURCE_CONFLICT
    if exc.status_code in {400, 422}:
        return ErrorCode.VALIDATION_FAILED
    if exc.status_code in {0, 503, 504}:
        return ErrorCode.DEPENDENCY_UNAVAILABLE
    return ErrorCode.UPSTREAM_ERROR


def _raise_openclaw_error(
    exc: OpenClawOpsClientError,
    *,
    bff_error: Callable[..., HTTPException],
    message: str,
    precondition_failed: str,
    audit: Optional[Mapping[str, Any]] = None,
) -> None:
    details: Dict[str, Any] = {
        "upstream": {
            "code": _OPENCLAW_DEGRADED_CODE,
            "source_error_code": exc.error_code,
            "status_code": exc.status_code,
            "message": exc.message,
        }
    }
    if audit is not None:
        details["audit"] = dict(audit)
    raise bff_error(
        exc.status_code if exc.status_code else 503,
        _openclaw_error_code(exc),
        message,
        exc.message,
        precondition_failed=precondition_failed,
        details_extra=details,
    )


def _session_record(payload: Mapping[str, Any]) -> Dict[str, Any]:
    candidates = [
        payload.get("session"),
        (payload.get("data") or {}).get("session") if isinstance(payload.get("data"), dict) else None,
        payload,
    ]
    for candidate in candidates:
        if isinstance(candidate, dict):
            return _json_clone(candidate)
    return {}


def _session_id_from_record(record: Mapping[str, Any]) -> str:
    return str(record.get("session_id") or record.get("id") or "").strip()


def _session_type_from_record(record: Mapping[str, Any]) -> str:
    context_bundle = record.get("context_bundle") if isinstance(record.get("context_bundle"), dict) else {}
    return str(record.get("session_type") or context_bundle.get("session_type") or "interactive")


def _session_belongs_to_scope(record: Mapping[str, Any], *, scope: Any, profile: ServantProfile) -> bool:
    operator_id = str(record.get("operator_id") or "").strip()
    agent_id = str(record.get("agent_id") or "").strip()
    if operator_id and operator_id != scope.operator_id:
        return False
    if agent_id and agent_id != profile.persona_id:
        return False
    return True


def _session_view(record: Mapping[str, Any], *, profile: ServantProfile) -> Dict[str, Any]:
    session_id = _session_id_from_record(record)
    context_bundle = record.get("context_bundle") if isinstance(record.get("context_bundle"), dict) else {}
    state = str(record.get("state") or record.get("status") or "unknown")
    return {
        "session_id": session_id,
        "openclaw_session_id": record.get("upstream_session_id"),
        "servant_persona_id": profile.persona_id,
        "session_type": _session_type_from_record(record),
        "status": state,
        "state": state,
        "intent": context_bundle.get("intent"),
        "strategy_ref": context_bundle.get("strategy_ref"),
        "metadata": context_bundle.get("metadata") if isinstance(context_bundle.get("metadata"), dict) else {},
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "links": {
            "self": f"/bff/agora/servant/sessions/{session_id}",
            "messages": f"/bff/agora/servant/sessions/{session_id}/messages",
            "terminate": f"/bff/agora/servant/sessions/{session_id}/terminate",
            "stream": f"/bff/agora/servant/sessions/{session_id}/stream",
        },
        "openclaw_lifecycle": {
            "state": state,
            "agent_id": record.get("agent_id"),
            "operator_id": record.get("operator_id"),
            "last_error": record.get("last_error"),
        },
    }


def _servant_meta(
    *,
    now: str,
    scope: Any,
    capability: str,
    audit: Mapping[str, Any],
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    meta = {
        "snapshot_at": now,
        "capability": capability,
        "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
        "audit": dict(audit),
    }
    if extra:
        meta.update(_json_clone(dict(extra)))
    return meta


def _extract_caller_identity(extract_identity: Callable[..., Any], authorization: Optional[str], pantheon_session: Optional[str] = None) -> Any:
    if pantheon_session is not None:
        try:
            return extract_identity(authorization, session_cookie=pantheon_session)
        except TypeError:
            pass
    eff_auth = authorization or (f"Bearer {pantheon_session}" if pantheon_session else None)
    return extract_identity(eff_auth)


def _servant_persona_id_for_scope(read_store: Any, scope: Any) -> str:
    expected = _stable_servant_persona_id(tenant_id=scope.tenant_id, agora_user_id=scope.user_id)
    if read_store is not None:
        existing = _find_servant_persona(read_store, tenant_id=scope.tenant_id, agora_user_id=scope.user_id, expected_persona_id=expected)
        if isinstance(existing, Mapping):
            pid = str(existing.get("persona_id") or existing.get("id") or "").strip()
            if pid:
                return pid
    return expected


def _raise_institutional_knowledge_error(exc: InstitutionalKnowledgeUnavailableError, *, bff_error: Callable[..., HTTPException]) -> None:
    from services.control_plane.bff.models import ErrorCode

    detail_dict = exc.to_detail()
    http_exc = bff_error(
        exc.status_code, ErrorCode.DEPENDENCY_UNAVAILABLE, f"Institutional knowledge {exc.owner} owner is unavailable",
        exc.reason, precondition_failed=f"{exc.owner}_owner", details_extra=detail_dict,
    )
    if isinstance(http_exc.detail, dict):
        for k, v in (("status", "unavailable"), ("owner", exc.owner), ("reason", exc.reason), ("fallback_used", False)):
            http_exc.detail.setdefault(k, v)
    raise http_exc from exc


def create_servant_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_write_role: Callable[..., None] = lambda _i: None,
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    get_read_store: Callable[[], Any] = lambda: None,
    sync_servant_agent: Callable[[Mapping[str, Any]], Mapping[str, Any]] = lambda _p: {},
    get_persona_write_owner: Optional[Callable[[], Any]] = None,
    trading_room_service: Optional[Any] = None,
    trading_room_store: Optional[Any] = None,
    institutional_knowledge_reader: Optional[InstitutionalKnowledgeReader] = None,
    servant_session_owner: Any = _UNSET_SERVANT_OWNER,
    ensure_paper_book: Optional[Callable[..., Mapping[str, Any]]] = None,
    read_paper_book: Optional[Callable[..., Optional[Mapping[str, Any]]]] = None,
) -> APIRouter:
    router = APIRouter(tags=["agora-servant"])
    knowledge_reader = institutional_knowledge_reader or InstitutionalKnowledgeReader()

    router_owner_cache: Dict[str, Any] = {}

    def _resolve_session_owner() -> Any:
        if servant_session_owner is not _UNSET_SERVANT_OWNER:
            return servant_session_owner() if callable(servant_session_owner) else servant_session_owner
        owner = router_owner_cache.get("owner")
        if owner is None:
            owner = _default_servant_session_owner()
            if owner is not None:
                router_owner_cache["owner"] = owner
        return owner

    def _session_owner(audit: Optional[Mapping[str, Any]] = None) -> Any:
        """The Agora owner that holds servant conversations; none means fail closed."""
        resolved_session_owner = _resolve_session_owner()
        if resolved_session_owner is None:
            from services.control_plane.bff.models import ErrorCode

            raise bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Agora owner is unavailable for servant conversations",
                "servant_session_owner_unavailable",
                precondition_failed="agora_owner",
                details_extra={"audit": dict(audit)} if audit else None,
            )
        return resolved_session_owner

    def _resolve_trading_room_service() -> Any:
        if trading_room_service is not None:
            if getattr(trading_room_service, "institutional_knowledge_reader", None) is None:
                trading_room_service.institutional_knowledge_reader = knowledge_reader
            return trading_room_service
        from ..trading_room.service import TradingRoomService, get_active_trading_room_service

        if trading_room_store is not None:
            return TradingRoomService(store=trading_room_store, utc_now=utc_now, bff_error=bff_error, institutional_knowledge_reader=knowledge_reader)
        active = get_active_trading_room_service()
        if getattr(active, "institutional_knowledge_reader", None) is None:
            active.institutional_knowledge_reader = knowledge_reader
        return active

    def _ensure_owner_session(
        owner: Any,
        session_id: str,
        *,
        scope: Any,
        persona_id: str,
        session_type: str,
        audit: Mapping[str, Any],
    ) -> Any:
        """Create the durable conversation row once; another trader's row is forbidden."""
        from services.agora.store import ServantSessionConflictError

        try:
            return owner.ensure_servant_session(
                session_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                operator_id=scope.operator_id,
                persona_id=persona_id,
                session_type=session_type,
                actor_roles=("trader",),
            )
        except ServantSessionConflictError:
            _raise_cross_user_session_forbidden(bff_error=bff_error, audit=audit)

    def _reconcile_servant_persona_for_scope(
        *,
        scope: Any,
        idempotency_key: str,
        now: str,
    ) -> tuple[Any, str, Dict[str, Any], ServantCapabilitySummary, AgoraServantPolicy]:
        write_owner = _persona_owner_call(
            lambda: get_persona_write_owner() if get_persona_write_owner else None,
            dependency="persona_registry_write_owner",
            bff_error=bff_error,
        )
        if write_owner is None:
            write_owner = _persona_owner_call(
                lambda: (_ for _ in ()).throw(
                    PersonaWriteOwnerUnavailable(
                        "persona_registry_write_owner",
                        "Persona Registry writer is not configured",
                    )
                ),
                dependency="persona_registry_write_owner",
                bff_error=bff_error,
            )
        persona_id = _stable_servant_persona_id(
            tenant_id=scope.tenant_id,
            agora_user_id=scope.user_id,
        )
        existing = _persona_owner_call(
            lambda: _find_servant_persona(
                write_owner,
                tenant_id=scope.tenant_id,
                agora_user_id=scope.user_id,
                expected_persona_id=persona_id,
            ),
            dependency="persona_registry_write_owner",
            bff_error=bff_error,
        )
        policy = AgoraServantPolicy()
        capability_summary = _capability_summary(scope, existing)
        capability_snapshot_id = _stable_servant_capability_snapshot_id(
            persona_id=persona_id,
        )
        metadata = _base_servant_metadata(
            scope=scope,
            capability_summary=capability_summary,
            policy=policy,
            capability_snapshot_id=capability_snapshot_id,
            now=now,
            existing=existing,
        )
        if existing is None:
            persona = _persona_owner_call(
                lambda: _create_servant_persona(
                    write_owner,
                    persona_id=persona_id,
                    scope=scope,
                    now=now,
                    metadata=metadata,
                ),
                dependency="persona_registry_write_owner",
                bff_error=bff_error,
            )
        else:
            persona_id = str(existing.get("persona_id") or existing.get("id") or persona_id)
            persona = _persona_owner_call(
                lambda: _update_servant_persona(
                    write_owner,
                    persona_id=persona_id,
                    scope=scope,
                    now=now,
                    existing=existing,
                    metadata=metadata,
                ),
                dependency="persona_registry_write_owner",
                bff_error=bff_error,
            )

        try:
            sync_persona = _json_clone(persona)
            sync_persona["_agent_sync_idempotency_key"] = str(idempotency_key)
            sync_result = sync_servant_agent(sync_persona)
        except Exception as exc:  # noqa: BLE001
            from services.control_plane.bff.models import ErrorCode

            raise bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "OpenClaw servant agent provisioning failed",
                str(exc)[:300],
                precondition_failed="openclaw_agent_sync",
            ) from exc

        upsert_capability_snapshot = getattr(
            write_owner,
            "upsert_persona_capability_snapshot",
            None,
        )
        if not callable(upsert_capability_snapshot):
            _persona_owner_call(
                lambda: (_ for _ in ()).throw(
                    PersonaWriteOwnerUnavailable(
                        "persona_capability_write_owner",
                        "Persona capability snapshot writer is not configured",
                    )
                ),
                dependency="persona_capability_write_owner",
                bff_error=bff_error,
            )
        resolved_persona_id = str(persona.get("persona_id") or persona.get("id") or persona_id)
        _persona_owner_call(
            lambda: upsert_capability_snapshot(
                snapshot_id=capability_snapshot_id,
                persona_id=resolved_persona_id,
                capabilities=[_PERSONA_OPINION_CAPABILITY],
                generated_at=now,
                source_refs=[
                    f"persona:{resolved_persona_id}",
                    "policy:agora-servant-paper-opinion",
                ],
                metadata={
                    "tenant_id": scope.tenant_id,
                    "agora_user_id": scope.user_id,
                    "environment_ceiling": "paper",
                    "execution_authority": "none",
                },
                actor_id=scope.operator_id,
            ),
            dependency="persona_capability_write_owner",
            bff_error=bff_error,
        )

        metadata = _base_servant_metadata(
            scope=scope,
            capability_summary=capability_summary,
            policy=policy,
            capability_snapshot_id=capability_snapshot_id,
            now=now,
            existing=persona,
            sync_result=sync_result,
        )
        persona = _persona_owner_call(
            lambda: _update_servant_persona(
                write_owner,
                persona_id=resolved_persona_id,
                scope=scope,
                now=now,
                existing=persona,
                metadata=metadata,
                activate=True,
            ),
            dependency="persona_registry_write_owner",
            bff_error=bff_error,
        )
        return write_owner, resolved_persona_id, persona, capability_summary, policy

    @router.get("/bff/agora/servant")
    def get_agora_servant(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        """Return the provisioned servant profile scoped to the caller."""
        scope = _scope_for_servant_session(
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            bff_error=bff_error,
            utc_now=utc_now,
            authorization=authorization,
            x_tenant_id=x_tenant_id,
            x_pantheon_tenant=x_pantheon_tenant,
        )
        profile = _servant_profile_for_scope(
            read_store=get_read_store(),
            scope=scope,
            bff_error=bff_error,
        )
        return AgoraEnvelope(
            data=profile.model_dump(),
            meta=AgoraMeta(
                snapshot_at=utc_now(),
                capability=_SERVANT_CAPABILITY,
                audience=f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            ),
        ).model_dump()

    @router.post("/bff/agora/servant/ensure")
    def agora_servant_ensure(
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        """Provision or reconcile the user-private Agora servant persona."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        require_write_role(identity)
        _require_header(idempotency_key, "Idempotency-Key", bff_error)
        request_id = _require_header(x_request_id, "X-Request-Id", bff_error)
        try:
            scope = resolve_agora_user_scope(
                identity,
                utc_now=utc_now,
                requested_tenant_id=x_tenant_id or x_pantheon_tenant,
            )
        except AgoraScopeResolutionError as exc:
            _raise_scope_error(exc, bff_error)

        now = utc_now()
        write_owner, persona_id, persona, capability_summary, policy = _reconcile_servant_persona_for_scope(
            scope=scope,
            idempotency_key=str(idempotency_key),
            now=now,
        )
        if callable(ensure_paper_book):
            try:
                paper_book = ensure_paper_book(
                    tenant_id=scope.tenant_id,
                    persona_id=persona_id,
                    requested_by=scope.operator_id,
                    agora_user_id=scope.user_id,
                    target_stage="paper",
                    persona_record=persona,
                    persona_write_owner=write_owner,
                )
            except PersonaProvisioningCoordinationError as exc:
                from services.control_plane.bff.models import ErrorCode

                raise bff_error(
                    409,
                    ErrorCode.PRECONDITION_FAILED,
                    "Trader servant paper book provisioning failed",
                    str(exc)[:300],
                    precondition_failed="paper_book_provisioning",
                ) from exc
            if isinstance(paper_book, Mapping):
                persona_meta = _metadata(persona)
                persona_meta["paper_book"] = _json_clone(dict(paper_book))
                if isinstance(paper_book.get("starting_capital"), Mapping):
                    persona_meta["starting_capital"] = _json_clone(dict(paper_book["starting_capital"]))
                persona = dict(persona)
                persona["metadata"] = persona_meta
        profile = _profile_from_persona(
            persona,
            scope=scope,
            capability_summary=capability_summary,
            policy=policy,
        )
        envelope = AgoraEnvelope(
            data=profile.model_dump(),
            meta=AgoraMeta(
                snapshot_at=now,
                capability=_SERVANT_CAPABILITY,
                audience=f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            ),
        )
        payload = envelope.model_dump()
        payload["meta"]["request_id"] = request_id
        payload["meta"]["idempotency_key"] = idempotency_key
        return payload

    @router.post("/bff/agora/servant/paper-book/ensure")
    def ensure_agora_servant_paper_book(
        body: Optional[ServantPaperBookEnsureRequest] = Body(default=None),
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        """Provision or verify the caller's single paper capital pool and paper RuntimeBinding on first paper use."""
        from services.control_plane.bff.models import ErrorCode

        identity = _extract_caller_identity(extract_identity, authorization, pantheon_session)
        require_read_role(identity)
        require_write_role(identity)
        eff_idempotency = str(idempotency_key or "").strip() or _new_prefixed_id("idem-paper-book")
        request_id = _request_id(x_request_id)
        try:
            scope = resolve_agora_user_scope(
                identity,
                utc_now=utc_now,
                requested_tenant_id=x_tenant_id or x_pantheon_tenant,
            )
        except AgoraScopeResolutionError as exc:
            _raise_scope_error(exc, bff_error)

        request = body or ServantPaperBookEnsureRequest()
        try:
            validate_paper_only_stage(
                target_stage=request.target_stage,
                stage=request.stage,
                deployment_stage=request.deployment_stage,
                deployment_mode=request.deployment_mode,
                allowed_deployment_scope=request.allowed_deployment_scope,
                environment=request.environment,
                environment_ceiling=request.environment_ceiling,
                execution_mode=request.execution_mode,
            )
        except PersonaProvisioningCoordinationError as exc:
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "Trader servant paper book only supports paper stage",
                str(exc)[:300],
                precondition_failed="paper_only_stage",
                details_extra={"error": str(exc)[:300]},
            ) from exc

        now = utc_now()
        expected_persona_id = _stable_servant_persona_id(
            tenant_id=scope.tenant_id,
            agora_user_id=scope.user_id,
        )
        write_owner = get_persona_write_owner() if callable(get_persona_write_owner) else None
        read_store = get_read_store()
        persona: Optional[Dict[str, Any]] = None
        if write_owner is not None:
            persona = _find_servant_persona(
                write_owner,
                tenant_id=scope.tenant_id,
                agora_user_id=scope.user_id,
                expected_persona_id=expected_persona_id,
            )
        if persona is None and read_store is not None:
            persona = _find_servant_persona(
                read_store,
                tenant_id=scope.tenant_id,
                agora_user_id=scope.user_id,
                expected_persona_id=expected_persona_id,
            )
        if persona is None and write_owner is not None:
            write_owner, persona_id, persona, _cap, _pol = _reconcile_servant_persona_for_scope(
                scope=scope,
                idempotency_key=eff_idempotency,
                now=now,
            )
        else:
            persona_id = (
                str(persona.get("persona_id") or persona.get("id") or expected_persona_id)
                if isinstance(persona, Mapping)
                else expected_persona_id
            )
        try:
            validate_servant_persona_paper_ceiling(
                persona,
                tenant_id=scope.tenant_id,
                persona_id=persona_id,
            )
        except PersonaProvisioningCoordinationError as exc:
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "Trader servant persona violates paper-only ceiling",
                str(exc)[:300],
                precondition_failed="servant_paper_ceiling",
                details_extra={"error": str(exc)[:300]},
            ) from exc

        provision_fn = ensure_paper_book or ensure_trader_paper_book
        try:
            book = provision_fn(
                tenant_id=scope.tenant_id,
                persona_id=persona_id,
                requested_by=scope.operator_id,
                agora_user_id=scope.user_id,
                target_stage=request.target_stage,
                stage=request.stage,
                deployment_stage=request.deployment_stage,
                deployment_mode=request.deployment_mode,
                allowed_deployment_scope=request.allowed_deployment_scope,
                environment=request.environment,
                environment_ceiling=request.environment_ceiling,
                execution_mode=request.execution_mode,
                persona_record=persona,
                persona_write_owner=write_owner,
            )
        except PersonaProvisioningCoordinationError as exc:
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "Trader servant paper book provisioning failed",
                str(exc)[:300],
                precondition_failed="paper_book_provisioning",
                details_extra={"error": str(exc)[:300]},
            ) from exc

        envelope = AgoraEnvelope(
            data=_json_clone(dict(book)),
            meta=AgoraMeta(
                snapshot_at=now,
                capability=_SERVANT_CAPABILITY,
                audience=f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            ),
        )
        payload = envelope.model_dump()
        payload["meta"]["request_id"] = request_id
        payload["meta"]["idempotency_key"] = eff_idempotency
        payload["meta"]["environment_ceiling"] = "paper"
        return payload

    @router.get("/bff/agora/servant/paper-book")
    def get_agora_servant_paper_book(
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        """Return the caller's provisioned paper capital pool and paper RuntimeBinding."""
        from services.control_plane.bff.models import ErrorCode

        identity, scope, servant_persona_id, _eff_auth = _resolve_knowledge_caller(
            authorization,
            pantheon_session,
            x_tenant_id,
            x_pantheon_tenant,
        )
        _ = identity
        read_fn = read_paper_book or read_trader_paper_book
        try:
            book = read_fn(
                tenant_id=scope.tenant_id,
                persona_id=servant_persona_id,
                agora_user_id=scope.user_id,
            )
        except PersonaProvisioningCoordinationError as exc:
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "Trader servant paper book readback failed",
                str(exc)[:300],
                precondition_failed="paper_book_readback",
                details_extra={"error": str(exc)[:300]},
            ) from exc
        if book is None:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Trader servant paper book is not provisioned",
                "Call POST /bff/agora/servant/paper-book/ensure on first paper use",
                precondition_failed="paper_book",
            )
        return AgoraEnvelope(
            data=_json_clone(dict(book)),
            meta=AgoraMeta(
                snapshot_at=utc_now(),
                capability=_SERVANT_CAPABILITY,
                audience=f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            ),
        ).model_dump()

    @router.post("/bff/agora/servant/sessions", status_code=201)
    def create_servant_session(
        body: Optional[ServantSessionCreateRequest] = Body(default=None),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
        x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        scope = _scope_for_servant_session(
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            bff_error=bff_error,
            utc_now=utc_now,
            authorization=authorization,
            x_tenant_id=x_tenant_id,
            x_pantheon_tenant=x_pantheon_tenant,
        )
        _require_header(idempotency_key, "Idempotency-Key", bff_error)
        request_id = _require_header(x_request_id, "X-Request-Id", bff_error)
        trace_id = _trace_id(x_trace_id)
        profile = _servant_profile_for_scope(
            read_store=get_read_store(),
            scope=scope,
            bff_error=bff_error,
        )
        request = body or ServantSessionCreateRequest()
        now = utc_now()
        context_bundle = {
            "capability": _SERVANT_CAPABILITY,
            "session_type": request.session_type,
            "intent": request.intent,
            "strategy_ref": request.strategy_ref,
            "metadata": _json_clone(request.metadata),
            "audit": _audit_fields(
                scope=scope,
                persona_id=profile.persona_id,
                session_id="pending",
                request_id=request_id,
                trace_id=trace_id,
            ),
            "safety": {
                "execution_authority": "none",
                "prohibited_authority": list(_PROHIBITED_AUTHORITY),
            },
        }
        try:
            raw = OpenClawOpsClient().create_session(
                agent_id=profile.persona_id,
                session_type=request.session_type,
                operator_id=scope.operator_id,
                idempotency_key=str(idempotency_key),
                context_bundle=context_bundle,
            )
        except OpenClawOpsClientError as exc:
            audit = _audit_fields(
                scope=scope,
                persona_id=profile.persona_id,
                session_id="pending",
                request_id=request_id,
                trace_id=trace_id,
            )
            _raise_openclaw_error(
                exc,
                bff_error=bff_error,
                message="OpenClaw servant session creation failed",
                precondition_failed="openclaw_session_create",
                audit=audit,
            )
        record = _session_record(raw)
        session_id = _session_id_from_record(record)
        audit = _audit_fields(
            scope=scope,
            persona_id=profile.persona_id,
            session_id=session_id,
            request_id=request_id,
            trace_id=trace_id,
        )
        owner = _resolve_session_owner()
        if owner is not None:
            _ensure_owner_session(
                owner,
                session_id,
                scope=scope,
                persona_id=profile.persona_id,
                session_type=request.session_type,
                audit=audit,
            )
        return {
            "data": _session_view(record, profile=profile),
            "meta": _servant_meta(
                now=now,
                scope=scope,
                capability=_SERVANT_CAPABILITY,
                audit=audit,
                extra={"idempotency_key": idempotency_key},
            ),
        }

    @router.get("/bff/agora/servant/sessions/{session_id}")
    def get_servant_session(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
        x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        scope = _scope_for_servant_session(
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            bff_error=bff_error,
            utc_now=utc_now,
            authorization=authorization,
            x_tenant_id=x_tenant_id,
            x_pantheon_tenant=x_pantheon_tenant,
        )
        profile = _servant_profile_for_scope(
            read_store=get_read_store(),
            scope=scope,
            bff_error=bff_error,
        )
        request_id = _request_id(x_request_id)
        trace_id = _trace_id(x_trace_id)
        audit = _audit_fields(
            scope=scope,
            persona_id=profile.persona_id,
            session_id=session_id,
            request_id=request_id,
            trace_id=trace_id,
        )
        try:
            raw = OpenClawOpsClient().get_session(
                session_id=session_id,
                operator_id=scope.operator_id,
            )
        except OpenClawOpsClientError as exc:
            _raise_openclaw_error(
                exc,
                bff_error=bff_error,
                message="OpenClaw servant session lookup failed",
                precondition_failed="openclaw_session_get",
                audit=audit,
            )
        record = _session_record(raw)
        if not _session_belongs_to_scope(record, scope=scope, profile=profile):
            _raise_cross_user_session_forbidden(bff_error=bff_error, audit=audit)
        view = _session_view(record, profile=profile)
        owner = _resolve_session_owner()
        if owner is not None:
            durable = owner.get_servant_session(session_id, tenant_id=scope.tenant_id, user_id=scope.user_id)
            if isinstance(durable, Mapping):
                msgs = [m for m in durable.get("messages") or [] if isinstance(m, dict)]
                view["pending_clarification"] = latest_pending_clarification(msgs)
                view["clarification_choices"] = extract_clarification_choices_from_messages(msgs)
                view["habit_facts"] = extract_clarification_habit_facts_from_messages(msgs)
        return {
            "data": view,
            "meta": _servant_meta(
                now=utc_now(),
                scope=scope,
                capability=_SERVANT_CAPABILITY,
                audit=audit,
            ),
        }

    @router.post("/bff/agora/servant/sessions/{session_id}/messages", status_code=202)
    def post_servant_session_message(
        session_id: str,
        body: ServantMessageRequest,
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
        x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        scope = _scope_for_servant_session(
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            bff_error=bff_error,
            utc_now=utc_now,
            authorization=authorization,
            x_tenant_id=x_tenant_id,
            x_pantheon_tenant=x_pantheon_tenant,
        )
        _require_header(idempotency_key, "Idempotency-Key", bff_error)
        request_id = _require_header(x_request_id, "X-Request-Id", bff_error)
        trace_id = _trace_id(x_trace_id)
        profile = _servant_profile_for_scope(
            read_store=get_read_store(),
            scope=scope,
            bff_error=bff_error,
        )
        audit = _audit_fields(
            scope=scope,
            persona_id=profile.persona_id,
            session_id=session_id,
            request_id=request_id,
            trace_id=trace_id,
        )
        try:
            raw = OpenClawOpsClient().get_session(
                session_id=session_id,
                operator_id=scope.operator_id,
            )
        except OpenClawOpsClientError as exc:
            _raise_openclaw_error(
                exc,
                bff_error=bff_error,
                message="OpenClaw servant session lookup failed",
                precondition_failed="openclaw_session_get",
                audit=audit,
            )
        record = _session_record(raw)
        if not _session_belongs_to_scope(record, scope=scope, profile=profile):
            _raise_cross_user_session_forbidden(bff_error=bff_error, audit=audit)
        state = str(record.get("state") or record.get("status") or "").lower()
        if state in {"canceled", "cancelled", "failed"}:
            from services.control_plane.bff.models import ErrorCode

            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Servant session cannot accept messages",
                f"Session state is {state}",
                precondition_failed="session_state",
                details_extra={"audit": audit},
            )
        session_type = _session_type_from_record(record)
        owner = _session_owner(audit)
        durable_session = _ensure_owner_session(
            owner,
            session_id,
            scope=scope,
            persona_id=profile.persona_id,
            session_type=session_type,
            audit=audit,
        )
        # AD-AG-05: the request only makes the turn durable. The provider call runs in
        # agora-interaction-worker; the answer reaches the trader over /stream.
        digest = _stable_digest(scope.tenant_id, scope.user_id, session_id, str(idempotency_key))
        message_id = f"servant-msg-{digest}"
        command_context = normalize_command_context(
            body.context.model_dump(exclude_none=True) if body.context is not None else None
        )
        turn_id = f"servant-turn-{digest}"
        now = utc_now()
        current_session = (
            owner.get_servant_session(session_id, tenant_id=scope.tenant_id, user_id=scope.user_id)
            or durable_session
            or {}
        )
        prior_messages = [
            m
            for m in (current_session.get("messages") or [])
            if isinstance(m, dict) and m.get("id") != message_id
        ]
        strategy_ref = (
            (command_context or {}).get("strategy_id")
            or ((record.get("context_bundle") or {}).get("strategy_ref") if isinstance(record.get("context_bundle"), dict) else None)
        )
        try:
            choice_event, habit_fact, eff_content = resolve_servant_clarification_choice(
                messages=prior_messages,
                explicit_choice=(
                    body.clarification_choice.model_dump(exclude_none=True)
                    if body.clarification_choice is not None
                    else None
                ),
                content=body.content,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                session_id=session_id,
                message_id=message_id,
                created_at=now,
                strategy_id=str(strategy_ref) if strategy_ref else None,
            )
        except ValueError as exc:
            from services.control_plane.bff.models import ErrorCode

            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid servant clarification choice",
                str(exc),
                precondition_failed="clarification_choice",
                details_extra={"audit": audit},
            ) from exc

        from services.agora.store import ServantSessionMessageCapError

        try:
            stored = owner.append_session_message(
                session_id,
                message_id,
                eff_content,
                scope.user_id,
                ("trader",),
                payload={
                    "role": "user",
                    "sender": {"type": "trader", "id": scope.user_id},
                    "attachments": [{"ref": ref} for ref in body.attachment_refs],
                    "metadata": {
                        "audit": audit,
                        **(
                            {"command_context": command_context}
                            if command_context
                            else {}
                        ),
                        **(
                            {"clarification_choice": choice_event}
                            if choice_event is not None
                            else {}
                        ),
                        **(
                            {"habit_fact": habit_fact}
                            if habit_fact is not None
                            else {}
                        ),
                    },
                },
                created_at=now,
            )
        except ServantSessionMessageCapError:
            from services.control_plane.bff.models import ErrorCode

            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Servant session is full",
                "The conversation reached its message limit; start a new session",
                precondition_failed="servant_session_message_cap",
                details_extra={"audit": audit},
            )
        if stored is None:
            _raise_cross_user_session_forbidden(bff_error=bff_error, audit=audit)
        if stored.get("content") != eff_content and not stored.get("truncated"):
            from services.control_plane.bff.models import ErrorCode

            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Idempotency-Key was already used with a different message",
                "idempotency_key_reuse",
                precondition_failed="idempotency_key",
                details_extra={"audit": audit},
            )
        if habit_fact is not None:
            stale_fn = getattr(owner, "mark_habit_profiles_stale", None) or getattr(
                getattr(owner, "store", None), "mark_habit_profiles_stale", None
            )
            if callable(stale_fn):
                try:
                    stale_fn(scope.tenant_id, scope.user_id)
                except Exception:
                    pass
        _inserted, turn = owner.enqueue_servant_turn(
            {
                "turn_id": turn_id,
                "session_id": session_id,
                "session_type": session_type,
                "tenant_id": scope.tenant_id,
                "user_id": scope.user_id,
                "operator_id": scope.operator_id,
                "servant_persona_id": profile.persona_id,
                "user_message_id": message_id,
                "audit": audit,
                "created_at": now,
            },
            ("trader",),
        )
        return {
            "status": "accepted",
            "data": {
                "message_id": message_id,
                "turn_id": turn_id,
                "session_id": session_id,
                "session_type": session_type,
                "servant_persona_id": profile.persona_id,
                "provider": {
                    "status": str(turn.get("status") or "queued"),
                    "answer": None,
                    "error": None,
                },
                **(
                    {"clarification_choice": choice_event}
                    if choice_event is not None
                    else {}
                ),
                **(
                    {"habit_fact": habit_fact}
                    if habit_fact is not None
                    else {}
                ),
                "links": {"stream": f"/bff/agora/servant/sessions/{session_id}/stream"},
            },
            "meta": _servant_meta(
                now=utc_now(),
                scope=scope,
                capability=_SERVANT_SESSION_CAPABILITY,
                audit=audit,
                extra={"idempotency_key": idempotency_key},
            ),
        }

    @router.post("/bff/agora/servant/sessions/{session_id}/terminate")
    def terminate_servant_session(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
        x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        scope = _scope_for_servant_session(
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            bff_error=bff_error,
            utc_now=utc_now,
            authorization=authorization,
            x_tenant_id=x_tenant_id,
            x_pantheon_tenant=x_pantheon_tenant,
        )
        _require_header(idempotency_key, "Idempotency-Key", bff_error)
        request_id = _request_id(x_request_id)
        trace_id = _trace_id(x_trace_id)
        profile = _servant_profile_for_scope(
            read_store=get_read_store(),
            scope=scope,
            bff_error=bff_error,
        )
        audit = _audit_fields(
            scope=scope,
            persona_id=profile.persona_id,
            session_id=session_id,
            request_id=request_id,
            trace_id=trace_id,
        )
        try:
            raw = OpenClawOpsClient().cancel_session(
                session_id=session_id,
                operator_id=scope.operator_id,
                idempotency_key=str(idempotency_key),
            )
        except OpenClawOpsClientError as exc:
            _raise_openclaw_error(
                exc,
                bff_error=bff_error,
                message="OpenClaw servant session termination failed",
                precondition_failed="openclaw_session_terminate",
                audit=audit,
            )
        record = _session_record(raw)
        if not _session_belongs_to_scope(record, scope=scope, profile=profile):
            _raise_cross_user_session_forbidden(bff_error=bff_error, audit=audit)
        owner = _resolve_session_owner()
        if owner is not None and owner.get_servant_session(session_id, tenant_id=scope.tenant_id, user_id=scope.user_id):
            owner.close_session(
                session_id,
                scope.user_id,
                ("trader",),
                outcome=str(record.get("state") or record.get("status") or "canceled"),
            )
        return {
            "data": _session_view(record, profile=profile),
            "meta": _servant_meta(
                now=utc_now(),
                scope=scope,
                capability=_SERVANT_CAPABILITY,
                audit=audit,
                extra={"idempotency_key": idempotency_key},
            ),
        }

    @router.get("/bff/agora/servant/sessions/{session_id}/stream")
    def stream_servant_session(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
        last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
        x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> StreamingResponse:
        scope = _scope_for_servant_session(
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            bff_error=bff_error,
            utc_now=utc_now,
            authorization=authorization,
            x_tenant_id=x_tenant_id,
            x_pantheon_tenant=x_pantheon_tenant,
        )
        profile = _servant_profile_for_scope(
            read_store=get_read_store(),
            scope=scope,
            bff_error=bff_error,
        )
        request_id = _request_id(x_request_id)
        trace_id = _trace_id(x_trace_id)
        audit = _audit_fields(
            scope=scope,
            persona_id=profile.persona_id,
            session_id=session_id,
            request_id=request_id,
            trace_id=trace_id,
        )
        try:
            raw = OpenClawOpsClient().get_session(
                session_id=session_id,
                operator_id=scope.operator_id,
            )
        except OpenClawOpsClientError as exc:
            _raise_openclaw_error(
                exc,
                bff_error=bff_error,
                message="OpenClaw servant session lookup failed",
                precondition_failed="openclaw_session_get",
                audit=audit,
            )
        record = _session_record(raw)
        if not _session_belongs_to_scope(record, scope=scope, profile=profile):
            _raise_cross_user_session_forbidden(bff_error=bff_error, audit=audit)

        owner = _session_owner(audit)
        durable = owner.get_servant_session(
            session_id, tenant_id=scope.tenant_id, user_id=scope.user_id
        )

        def _iter_events() -> Iterator[str]:
            replay_events = (
                _events_for_session(owner, durable, last_event_id) if durable is not None else []
            )
            if not replay_events:
                yield _format_sse(
                    {
                        "id": _new_prefixed_id("evt-servant"),
                        "type": "servant.session.snapshot",
                        "data": {
                            "session_id": session_id,
                            "status": record.get("state") or record.get("status") or "unknown",
                            "audit": audit,
                        },
                    }
                )
                return
            for event in replay_events:
                yield _format_sse(event)

        return StreamingResponse(
            _iter_events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-SSE-Channel": f"agora-servant:{session_id}",
            },
        )

    def _resolve_knowledge_caller(
        authorization: Optional[str], pantheon_session: Optional[str], x_tenant_id: Optional[str], x_pantheon_tenant: Optional[str]
    ) -> tuple[Any, Any, str, Optional[str]]:
        identity = _extract_caller_identity(extract_identity, authorization, pantheon_session)
        require_read_role(identity)
        try:
            scope = resolve_agora_user_scope(identity, utc_now=utc_now, requested_tenant_id=x_tenant_id or x_pantheon_tenant)
        except AgoraScopeResolutionError as exc:
            _raise_scope_error(exc, bff_error)
        servant_persona_id = _servant_persona_id_for_scope(get_read_store(), scope)
        eff_auth = authorization or (f"Bearer {pantheon_session}" if pantheon_session else None)
        return identity, scope, servant_persona_id, eff_auth

    def _knowledge_envelope(data: Dict[str, Any], *, scope: Any, capability: str, proof: str) -> Dict[str, Any]:
        return {
            "status": "ok", "data": data,
            "meta": {
                "snapshot_at": utc_now(), "capability": capability, "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "read_only": True, "advice_only": True, "execution_authority": "none", "environment_ceiling": "paper", "no_order_route_proof": proof,
            },
        }

    @router.get("/bff/agora/servant/institutional-knowledge")
    def get_servant_institutional_knowledge(
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
        fact_kind: Optional[str] = Query(default=None),
        symbol: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None),
        strategy_family: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        _identity, scope, servant_persona_id, eff_auth = _resolve_knowledge_caller(authorization, pantheon_session, x_tenant_id, x_pantheon_tenant)
        try:
            data = knowledge_reader.read_servant_institutional_knowledge(
                tenant_id=scope.tenant_id, user_id=scope.user_id, servant_persona_id=servant_persona_id,
                operator_id=scope.operator_id, authorization=eff_auth, fact_kind=fact_kind,
                symbol=symbol, strategy_id=strategy_id, strategy_family=strategy_family,
            )
        except InstitutionalKnowledgeUnavailableError as exc:
            _raise_institutional_knowledge_error(exc, bff_error=bff_error)
        return _knowledge_envelope(data, scope=scope, capability=_SERVANT_CAPABILITY, proof="agora_institutional_knowledge_read_only")

    @router.get("/bff/agora/trading-room/system-suggestions")
    def list_trading_room_system_suggestions(
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
        decision_event_id: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None),
        symbol: Optional[str] = Query(default=None),
        strategy_family: Optional[str] = Query(default=None),
        event_kind: Optional[str] = Query(default=None),
        action: Optional[str] = Query(default=None),
        regime_ref: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        identity, scope, servant_persona_id, eff_auth = _resolve_knowledge_caller(authorization, pantheon_session, x_tenant_id, x_pantheon_tenant)
        data = _resolve_trading_room_service().get_system_suggestions(
            identity=identity, decision_event_id=decision_event_id, strategy_id=strategy_id, symbol=symbol,
            strategy_family=strategy_family, event_kind=event_kind, action=action, regime_ref=regime_ref,
            servant_persona_id=servant_persona_id, authorization=eff_auth,
        )
        return _knowledge_envelope(data, scope=scope, capability="agora.trading.v1", proof="agora_decision_support_only")

    @router.get("/bff/agora/trading-room/decision-events/{decision_event_id}/system-suggestions")
    def get_decision_event_system_suggestions(
        decision_event_id: str,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        identity, scope, servant_persona_id, eff_auth = _resolve_knowledge_caller(authorization, pantheon_session, x_tenant_id, x_pantheon_tenant)
        data = _resolve_trading_room_service().get_decision_system_suggestions(
            decision_event_id, identity, servant_persona_id=servant_persona_id, authorization=eff_auth,
        )
        return _knowledge_envelope(data, scope=scope, capability="agora.trading.v1", proof="agora_decision_support_only")

    return router
