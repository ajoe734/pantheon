"""Agora servant router — servant persona profile (agora.servant.v1).

New routes registered here:
  POST /bff/agora/servant/ensure   — AG-BE-ID-002

Migration note: All other identity routes are currently in main.py.
This module is the intended home for servant-specific logic.
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from typing import Any, Callable, Dict, Iterable, Iterator, Literal, Mapping, Optional

from fastapi import APIRouter, Body, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
from ..models import (
    AGORA_CAPABILITIES,
    AgoraEnvelope,
    AgoraMeta,
    AgoraServantPolicy,
    ServantCapabilitySummary,
    ServantProfile,
)
try:
    from openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError
except ImportError:  # pragma: no cover - package entrypoint fallback
    from ...openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError
try:
    from ports.persona_write_owner import (
        PersonaWriteConflict,
        PersonaWriteOwnerUnavailable,
    )
except ImportError:
    from services.control_plane.bff.ports.persona_write_owner import (  # type: ignore[no-redef]
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
_SERVANT_EVENT_LOCK = threading.RLock()
_SERVANT_SESSION_EVENTS: Dict[str, list[Dict[str, Any]]] = {}


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


class ServantMessageRequest(BaseModel):
    """v1/v1.2 ServantMessageRequest."""

    model_config = {"extra": "forbid"}

    content: str = Field(min_length=1)
    attachment_refs: list[str] = Field(default_factory=list)


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


def _record_servant_event(session_id: str, event_type: str, data: Mapping[str, Any]) -> Dict[str, Any]:
    event = {
        "id": _new_prefixed_id("evt-servant"),
        "type": event_type,
        "data": _json_clone(dict(data)),
    }
    with _SERVANT_EVENT_LOCK:
        _SERVANT_SESSION_EVENTS.setdefault(session_id, []).append(event)
    return event


def _events_for_session(session_id: str, last_event_id: Optional[str] = None) -> list[Dict[str, Any]]:
    with _SERVANT_EVENT_LOCK:
        events = list(_SERVANT_SESSION_EVENTS.get(session_id, []))
    if not last_event_id:
        return events
    for idx, event in enumerate(events):
        if event.get("id") == last_event_id:
            return events[idx + 1 :]
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
    from models import ErrorCode

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
        metadata={
            "identity_key": {
                "tenant_id": scope.tenant_id,
                "agora_user_id": scope.user_id,
                "persona_class": "agora_servant",
            },
            "openclaw_agent": _json_clone(metadata.get("openclaw_agent") or {}),
            "policy_refs": list(metadata.get("policy_refs") or _SERVANT_POLICY_REFS),
        },
    )


def _require_header(value: Optional[str], name: str, bff_error: Callable[..., HTTPException]) -> str:
    clean = str(value or "").strip()
    if clean:
        return clean
    from models import ErrorCode

    raise bff_error(
        422,
        ErrorCode.VALIDATION_FAILED,
        f"{name} header is required",
        f"Missing required {name} header for Agora servant ensure",
        precondition_failed=name,
    )


def _raise_scope_error(exc: AgoraScopeResolutionError, bff_error: Callable[..., HTTPException]) -> None:
    from models import ErrorCode

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
    from models import ErrorCode

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
        from models import ErrorCode

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
    from models import ErrorCode

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


def _provider_answer_from(data: Mapping[str, Any]) -> Optional[str]:
    for key in ("output", "answer"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    provider = data.get("provider")
    if isinstance(provider, dict):
        value = provider.get("answer")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


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


def create_servant_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_write_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    get_read_store: Callable[[], Any],
    sync_servant_agent: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    get_persona_write_owner: Optional[Callable[[], Any]] = None,
) -> APIRouter:
    router = APIRouter(tags=["agora-servant"])

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
            from models import ErrorCode

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
        _persona_owner_call(
            lambda: upsert_capability_snapshot(
                snapshot_id=capability_snapshot_id,
                persona_id=str(persona.get("persona_id") or persona.get("id") or persona_id),
                capabilities=[_PERSONA_OPINION_CAPABILITY],
                generated_at=now,
                source_refs=[
                    f"persona:{persona.get('persona_id') or persona.get('id') or persona_id}",
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
                persona_id=str(persona.get("persona_id") or persona.get("id") or persona_id),
                scope=scope,
                now=now,
                existing=persona,
                metadata=metadata,
                activate=True,
            ),
            dependency="persona_registry_write_owner",
            bff_error=bff_error,
        )
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
        _record_servant_event(
            session_id,
            "servant.session.created",
            {
                "session_id": session_id,
                "session_type": request.session_type,
                "servant_persona_id": profile.persona_id,
                "audit": audit,
            },
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
        return {
            "data": _session_view(record, profile=profile),
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
            from models import ErrorCode

            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Servant session cannot accept messages",
                f"Session state is {state}",
                precondition_failed="session_state",
                details_extra={"audit": audit},
            )
        message_id = _new_prefixed_id("servant-msg")
        session_type = _session_type_from_record(record)
        provider_status = "accepted"
        provider_answer: Optional[str] = None
        provider_error: Optional[Dict[str, Any]] = None
        try:
            provider_raw = OpenClawOpsClient().invoke_assistant_provider(
                provider="openclaw",
                mode="user",
                prompt=body.content,
                context_pack={
                    "source": "agora.servant.session",
                    "session_id": session_id,
                    "session_type": session_type,
                    "servant_persona_id": profile.persona_id,
                    "audit": audit,
                },
                operator_id=scope.operator_id,
                metadata={
                    "audit": audit,
                    "session_type": session_type,
                    "servant_persona_id": profile.persona_id,
                    "openclaw_lifecycle_session_id": session_id,
                },
                trace_id=trace_id,
                messages=[
                    {
                        "role": "user",
                        "content": body.content,
                        "attachment_refs": list(body.attachment_refs),
                    }
                ],
                attachments=[{"ref": ref} for ref in body.attachment_refs],
            )
            data = provider_raw.get("data") if isinstance(provider_raw, dict) else {}
            if isinstance(data, dict):
                provider_answer = _provider_answer_from(data)
                provider_status = str(data.get("status") or provider_raw.get("status") or "accepted")
            else:
                provider_status = (
                    str(provider_raw.get("status") or "accepted")
                    if isinstance(provider_raw, dict)
                    else "accepted"
                )
        except OpenClawOpsClientError as exc:
            provider_status = "degraded"
            provider_error = {
                "code": _OPENCLAW_DEGRADED_CODE,
                "source_error_code": exc.error_code,
                "status_code": exc.status_code,
                "message": exc.message,
            }
        _record_servant_event(
            session_id,
            "servant.message.completed" if provider_status != "degraded" else "servant.message.degraded",
            {
                "session_id": session_id,
                "message_id": message_id,
                "provider_status": provider_status,
                "answer": provider_answer,
                "error": provider_error,
                "audit": audit,
            },
        )
        return {
            "status": "accepted",
            "data": {
                "message_id": message_id,
                "session_id": session_id,
                "session_type": session_type,
                "servant_persona_id": profile.persona_id,
                "provider": {
                    "status": provider_status,
                    "answer": provider_answer,
                    "error": provider_error,
                },
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
        _record_servant_event(
            session_id,
            "servant.session.terminated",
            {
                "session_id": session_id,
                "status": record.get("state") or record.get("status") or "canceled",
                "audit": audit,
            },
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

        def _iter_events() -> Iterator[str]:
            replay_events = _events_for_session(session_id, last_event_id)
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

    return router
