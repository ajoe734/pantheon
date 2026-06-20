"""Agora servant router — servant persona profile (agora.servant.v1).

New routes registered here:
  POST /bff/agora/servant/ensure   — AG-BE-ID-002

Migration note: All other identity routes are currently in main.py.
This module is the intended home for servant-specific logic.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

from fastapi import APIRouter, Header, HTTPException

from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
from ..models import (
    AGORA_CAPABILITIES,
    AgoraEnvelope,
    AgoraMeta,
    AgoraServantPolicy,
    ServantCapabilitySummary,
    ServantProfile,
)


_SERVANT_CAPABILITY = "agora.servant.v1"
_SERVANT_POLICY_REFS = [
    "docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/03_servant_and_workshop_contracts.md",
]
_PROHIBITED_AUTHORITY = {"runtime_binding", "broker_order", "capital_binding"}
_PROFILE_STATUSES = {"active", "suspended", "paper_only", "shadow_only", "retired"}


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value))


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
    raw = str(
        record.get("status")
        or record.get("lifecycle_state")
        or _metadata(record).get("status")
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
    read_store: Any,
    *,
    persona_id: str,
    scope: Any,
    now: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    create_persona = getattr(read_store, "create_persona", None)
    if not callable(create_persona):
        raise RuntimeError("read store does not support persona creation")
    return create_persona(
        persona_id=persona_id,
        name="Agora Servant",
        actor_id=scope.operator_id,
        created_at=now,
        archetype="agora_servant",
        lifecycle_state="active",
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
    read_store: Any,
    *,
    persona_id: str,
    scope: Any,
    now: str,
    existing: Mapping[str, Any],
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    update_persona = getattr(read_store, "update_persona", None)
    if not callable(update_persona):
        raise RuntimeError("read store does not support persona update")
    updated = update_persona(
        persona_id,
        name=str(existing.get("name") or "Agora Servant"),
        actor_id=scope.operator_id,
        updated_at=now,
        archetype=None,
        lifecycle_state=_servant_status(existing),
        risk_level=str(_metadata(existing).get("risk_level") or "low"),
        metadata=metadata,
    )
    return _json_clone(updated or existing)


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


def create_servant_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    get_read_store: Callable[[], Any],
    sync_servant_agent: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> APIRouter:
    router = APIRouter(tags=["agora-identity"])

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
        read_store = get_read_store()
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
        policy = AgoraServantPolicy()
        capability_summary = _capability_summary(scope, existing)
        metadata = _base_servant_metadata(
            scope=scope,
            capability_summary=capability_summary,
            policy=policy,
            now=now,
            existing=existing,
        )
        if existing is None:
            persona = _create_servant_persona(
                read_store,
                persona_id=persona_id,
                scope=scope,
                now=now,
                metadata=metadata,
            )
        else:
            persona_id = str(existing.get("persona_id") or existing.get("id") or persona_id)
            persona = _update_servant_persona(
                read_store,
                persona_id=persona_id,
                scope=scope,
                now=now,
                existing=existing,
                metadata=metadata,
            )

        try:
            sync_result = sync_servant_agent(persona)
        except Exception as exc:  # noqa: BLE001
            from models import ErrorCode

            raise bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "OpenClaw servant agent provisioning failed",
                str(exc)[:300],
                precondition_failed="openclaw_agent_sync",
            ) from exc

        metadata = _base_servant_metadata(
            scope=scope,
            capability_summary=capability_summary,
            policy=policy,
            now=now,
            existing=persona,
            sync_result=sync_result,
        )
        persona = _update_servant_persona(
            read_store,
            persona_id=str(persona.get("persona_id") or persona.get("id") or persona_id),
            scope=scope,
            now=now,
            existing=persona,
            metadata=metadata,
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

    return router
