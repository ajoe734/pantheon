from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException

from models import ErrorCode

from .context_composer import AssistantContextPolicyError
from .command_idempotency import (
    CommandIdempotencyError,
    CommandIdempotencyStore,
    CommandIdempotencyTransaction,
    resolve_command_idempotency_key,
)
from .control_mode import (
    CONTROL_MODE_CAPABILITY_PREFIX,
    CONTROL_MODE_ROLES,
    ControlModeError,
    ControlModeStore,
    actor_capabilities,
    actor_has_control_role,
    actor_has_kernel_capability,
    default_idle_ttl,
)
from .mode_policy import (
    DEFAULT_KERNEL_TTL_SECONDS,
    ModePolicyViolation,
    assert_kernel_allowed,
    check_session_active,
    create_session,
    session_to_dict,
    turn_to_dict,
    user_mode_capability_summary,
    PRODUCT_DEFAULT_MODE,
)
from .models import (
    AssistantContextPack,
    AssistantContextPackRequest,
    AssistantContextPackResponse,
    AssistantMode,
)
from .redaction import RedactionError, redact_assistant_payload
from .tool_contracts import (
    ASSISTANT_TOOL_ALLOWLIST,
    ToolNotAllowedError,
    ToolRbacError,
    ToolValidationError,
    execute_governed_tool,
    preview_tool,
    tool_receipt_to_dict,
    validate_tool,
)
from .transcript_store import (
    InMemorySessionStore,
    InMemoryTranscriptStore,
    SessionNotFoundError,
    SessionRejectedError,
    TurnRole,
    build_turn,
)


BuildContextPack = Callable[[str, AssistantContextPackRequest, Any], AssistantContextPack]
ExtractIdentity = Callable[[Optional[str]], Any]
RequireReadRole = Callable[[Any], None]
BffErrorFactory = Callable[..., HTTPException]
ProviderReadiness = Callable[[], Dict[str, Any]]
ProviderList = Callable[[bool], Dict[str, Any]]
ProviderRegister = Callable[[Dict[str, Any], str, Optional[str]], Dict[str, Any]]
ProviderReauth = Callable[[Dict[str, Any], str, Optional[str]], Dict[str, Any]]
ProviderReauthStatus = Callable[[str, str, str], Dict[str, Any]]
ProviderReauthCode = Callable[[str, str, str, str, Optional[str]], Dict[str, Any]]

def create_assistant_router(
    *,
    build_context_pack: BuildContextPack,
    extract_identity: ExtractIdentity,
    require_read_role: RequireReadRole,
    bff_error: Optional[BffErrorFactory] = None,
    session_store: Optional[Any] = None,
    transcript_store: Optional[Any] = None,
    control_mode_store: Optional[ControlModeStore] = None,
    provider_readiness: Optional[ProviderReadiness] = None,
    provider_list: Optional[ProviderList] = None,
    provider_register: Optional[ProviderRegister] = None,
    provider_reauth: Optional[ProviderReauth] = None,
    provider_reauth_status: Optional[ProviderReauthStatus] = None,
    provider_reauth_code: Optional[ProviderReauthCode] = None,
) -> APIRouter:
    router = APIRouter(prefix="/bff/assistant", tags=["assistant"])

    _session_store = session_store if session_store is not None else InMemorySessionStore()
    _transcript_store = transcript_store if transcript_store is not None else InMemoryTranscriptStore()
    _control_mode_store = control_mode_store if control_mode_store is not None else ControlModeStore()
    _command_idempotency_store = CommandIdempotencyStore()

    @router.post("/sessions/{session_id}/context", status_code=201)
    async def build_session_context_pack(
        session_id: str,
        payload: AssistantContextPackRequest = Body(default_factory=AssistantContextPackRequest),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        try:
            assert_kernel_allowed(payload.mode)
        except ModePolicyViolation as exc:
            _raise_error(
                bff_error, 403, ErrorCode.FORBIDDEN,
                f"Mode policy violation: {exc}",
                str(exc),
                field=exc.field,
            )
        try:
            pack = build_context_pack(session_id, payload, identity)
        except AssistantContextPolicyError as exc:
            if bff_error is not None:
                raise bff_error(
                    403,
                    ErrorCode.FORBIDDEN,
                    "Assistant context source is not allowed for this mode",
                    str(exc),
                    precondition_failed="assistant_context_mode_policy",
                    details_extra={"denied_sources": exc.denied_sources},
                )
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": ErrorCode.FORBIDDEN.value,
                        "message": "Assistant context source is not allowed for this mode",
                        "details": {
                            "reason": str(exc),
                            "denied_sources": exc.denied_sources,
                        },
                    }
                },
            ) from exc

        response = AssistantContextPackResponse(
            data=pack,
            meta={
                "snapshot_at": pack.snapshot_at,
                "requested_sources": payload.include,
                "included_sources": [source.source_id for source in pack.sources],
                "omitted_sources": [
                    source.model_dump(mode="json", by_alias=False) for source in pack.omitted_sources
                ],
            },
        )
        return response.model_dump(mode="json", by_alias=False)

    # ------------------------------------------------------------------
    # Session management routes
    # ------------------------------------------------------------------

    @router.post("/sessions", status_code=201)
    async def create_assistant_session(
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)

        mode_raw = payload.get("mode", PRODUCT_DEFAULT_MODE.value)
        try:
            mode = AssistantMode(mode_raw)
        except ValueError:
            _raise_error(bff_error, 400, ErrorCode.VALIDATION_FAILED, f"Invalid mode: {mode_raw!r}")

        try:
            assert_kernel_allowed(mode)
        except ModePolicyViolation as exc:
            _raise_error(
                bff_error, 403, ErrorCode.FORBIDDEN,
                f"Mode policy violation: {exc}",
                str(exc),
                field=exc.field,
            )

        reason: Optional[str] = payload.get("reason")
        ttl_seconds: Optional[int] = payload.get("ttl_seconds")

        try:
            session = create_session(
                mode=mode,
                actor=identity,
                reason=reason,
                ttl_seconds=ttl_seconds,
            )
        except ModePolicyViolation as exc:
            _raise_error(
                bff_error, 422, ErrorCode.BUSINESS_RULE_VIOLATION,
                f"Mode policy violation: {exc}",
                str(exc),
                field=exc.field,
            )

        _session_store.create(session)
        return {"data": session_to_dict(session)}

    @router.get("/sessions/{session_id}")
    async def get_assistant_session(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)

        try:
            session = _get_session_for_identity(_session_store, session_id, identity)
        except SessionNotFoundError:
            _raise_error(bff_error, 404, ErrorCode.RESOURCE_NOT_FOUND, f"Session not found: {session_id!r}")

        return {"data": session_to_dict(session)}

    @router.post("/sessions/{session_id}/revoke", status_code=200)
    async def revoke_assistant_session(
        session_id: str,
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)

        try:
            _get_session_for_identity(_session_store, session_id, identity)
            session = _session_store.revoke(session_id, reason=payload.get("reason"))
        except SessionNotFoundError:
            _raise_error(bff_error, 404, ErrorCode.RESOURCE_NOT_FOUND, f"Session not found: {session_id!r}")

        return {"data": session_to_dict(session)}

    @router.get("/sessions/{session_id}/transcript")
    async def get_session_transcript(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)

        try:
            _get_session_for_identity(_session_store, session_id, identity)
        except SessionNotFoundError:
            _raise_error(bff_error, 404, ErrorCode.RESOURCE_NOT_FOUND, f"Session not found: {session_id!r}")

        turns = _transcript_store.list_turns(session_id)
        return {"data": [turn_to_dict(t) for t in turns], "meta": {"count": len(turns)}}

    @router.post("/sessions/{session_id}/transcript", status_code=201)
    async def append_transcript_turn(
        session_id: str,
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)

        try:
            session = _get_session_for_identity(_session_store, session_id, identity)
        except SessionNotFoundError:
            _raise_error(bff_error, 404, ErrorCode.RESOURCE_NOT_FOUND, f"Session not found: {session_id!r}")

        try:
            check_session_active(session)
        except SessionRejectedError as exc:
            _raise_error(bff_error, 409, ErrorCode.RESOURCE_CONFLICT, str(exc))

        role_raw = payload.get("role", TurnRole.USER.value)
        try:
            role = TurnRole(role_raw)
        except ValueError:
            _raise_error(bff_error, 400, ErrorCode.VALIDATION_FAILED, f"Invalid role: {role_raw!r}")

        content: str = str(payload.get("content", ""))
        context_pack_id: Optional[str] = payload.get("context_pack_id")
        provider_run_id: Optional[str] = payload.get("provider_run_id")
        source_refs: List[Any] = payload.get("source_refs") or []

        turn = build_turn(
            session_id=session_id,
            role=role,
            content=content,
            context_pack_id=context_pack_id,
            provider_run_id=provider_run_id,
            source_refs=source_refs,
        )
        _transcript_store.append(turn)

        if context_pack_id or provider_run_id:
            _session_store.update_context(
                session_id,
                context_pack_id=context_pack_id,
                provider_run_id=provider_run_id,
            )

        return {"data": turn_to_dict(turn)}

    @router.get("/mode")
    async def get_product_mode(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        """Return the product default mode and its capability summary.

        Action suggestions in user mode route through existing BFF command and
        approval flows; kernel controls are not exposed.
        """
        identity = extract_identity(authorization)
        require_read_role(identity)
        from .mode_policy import kernel_sessions_enabled
        return {
            "data": {
                "product_default_mode": PRODUCT_DEFAULT_MODE.value,
                "kernel_enabled": kernel_sessions_enabled(),
                "user_mode": user_mode_capability_summary(),
                "control_mode": _control_mode_store.status_for_actor(identity.operator_id),
            }
        }

    @router.get("/control-mode")
    async def get_control_mode(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return {"data": _control_mode_store.status_for_actor(identity.operator_id)}

    @router.post("/control-mode/activate", status_code=202)
    async def activate_control_mode(
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        idempotency_recovery_id: Optional[str] = Header(
            default=None, alias="X-Idempotency-Recovery-Id"
        ),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        _require_control_mode_actor(identity, bff_error=bff_error)

        tenant_id = _resolve_identity_tenant(
            identity,
            requested_tenant=_resolve_tenant_header(
                x_tenant_id,
                x_pantheon_tenant,
                bff_error=bff_error,
            ),
            bff_error=bff_error,
        )

        mode_raw = payload.get("mode") or "kernel_debug"
        try:
            mode = AssistantMode(mode_raw)
        except ValueError:
            _raise_error(bff_error, 400, ErrorCode.VALIDATION_FAILED, f"Invalid mode: {mode_raw!r}", field="mode")
        try:
            assert_kernel_allowed(mode)
        except ModePolicyViolation as exc:
            _raise_error(
                bff_error,
                403,
                ErrorCode.FORBIDDEN,
                f"Mode policy violation: {exc}",
                str(exc),
                field=exc.field,
            )
        _require_mode_capability(identity, mode, bff_error=bff_error)

        ttl_seconds = _positive_int(payload.get("ttlSeconds", payload.get("ttl_seconds")), DEFAULT_KERNEL_TTL_SECONDS)
        idle_ttl_seconds = _positive_int(
            payload.get("idleTtlSeconds", payload.get("idle_ttl_seconds")),
            default_idle_ttl(ttl_seconds),
        )
        actor_id = str(getattr(identity, "operator_id", None) or "management-ai")
        with _assistant_command_idempotency(
            _command_idempotency_store,
            actor_id=actor_id,
            route="/bff/assistant/control-mode/activate",
            payload={"payload": payload, "tenant_id": tenant_id},
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            recovery_id=idempotency_recovery_id,
            bff_error=bff_error,
        ) as transaction:
            if transaction is not None and transaction.replayed:
                replayed = transaction.response or {}
                replayed_data = replayed.get("data") if isinstance(replayed, dict) else {}
                replayed_data = replayed_data if isinstance(replayed_data, dict) else {}
                current = _control_mode_store.status_for_actor(
                    identity.operator_id,
                    management_session_id=payload.get("managementSessionId")
                    or payload.get("management_session_id"),
                )
                replayed_activation_id = replayed_data.get("activationId") or replayed_data.get(
                    "activation_id"
                )
                current_activation_id = current.get("activationId") or current.get("activation_id")
                if not current.get("active") or current_activation_id != replayed_activation_id:
                    _raise_error(
                        bff_error,
                        409,
                        ErrorCode.RESOURCE_CONFLICT,
                        "Cached control-mode activation is no longer active",
                        "A BFF restart, expiry, or deactivation invalidated the cached activation. "
                        "Activate again with a new Idempotency-Key.",
                        field="idempotency_key",
                        reason="idempotency_replay_state_stale",
                    )
                return replayed
            try:
                activation = _control_mode_store.activate(
                    actor_id=identity.operator_id,
                    mode=mode,
                    capabilities=actor_capabilities(identity),
                    reason=str(payload.get("reason") or "").strip(),
                    passphrase=str(payload.get("passphrase") or payload.get("phrase") or ""),
                    ttl_seconds=ttl_seconds,
                    idle_ttl_seconds=idle_ttl_seconds,
                    management_session_id=payload.get("managementSessionId")
                    or payload.get("management_session_id"),
                )
            except ControlModeError as exc:
                _raise_control_mode_error(bff_error, exc)
            response = {"data": activation}
            if transaction is not None:
                transaction.complete(response)
            return response

    @router.post("/control-mode/deactivate", status_code=202)
    async def deactivate_control_mode(
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        idempotency_recovery_id: Optional[str] = Header(
            default=None, alias="X-Idempotency-Recovery-Id"
        ),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        _require_control_mode_actor(identity, bff_error=bff_error)
        actor_id = str(getattr(identity, "operator_id", None) or "management-ai")
        with _assistant_command_idempotency(
            _command_idempotency_store,
            actor_id=actor_id,
            route="/bff/assistant/control-mode/deactivate",
            payload=payload,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            recovery_id=idempotency_recovery_id,
            bff_error=bff_error,
        ) as transaction:
            if transaction is not None and transaction.replayed:
                return transaction.response or {}
            response = {
                "data": _control_mode_store.deactivate(
                    identity.operator_id,
                    reason=str(payload.get("reason") or "operator_deactivated").strip(),
                )
            }
            if transaction is not None:
                transaction.complete(response)
            return response

    @router.get("/providers")
    async def list_assistant_providers(
        auth_probe: bool = False,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        """Return assistant provider readiness for Management auth surfaces."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        if provider_list is not None:
            listed = provider_list(auth_probe)
            if isinstance(listed, dict) and isinstance(listed.get("data"), list):
                return listed
            if isinstance(listed, list):
                return {"status": "ok", "data": listed}
            return {"status": "ok", "data": [listed]}
        if provider_readiness is None:
            _raise_error(
                bff_error,
                503,
                ErrorCode.PRECONDITION_FAILED,
                "Assistant provider readiness is not configured",
                "OpenClaw adapter provider readiness is not configured for this BFF.",
                field="openclaw_adapter",
            )
        return {"status": "ok", "data": [provider_readiness()]}

    @router.post("/providers", status_code=201)
    async def register_assistant_provider(
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        """Register assistant provider metadata through the OpenClaw adapter."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        control_status = _require_active_control_mode(
            identity,
            _control_mode_store,
            bff_error=bff_error,
        )
        _require_provider_registration_control(control_status, bff_error=bff_error)
        if provider_register is None:
            _raise_error(
                bff_error,
                503,
                ErrorCode.PRECONDITION_FAILED,
                "Assistant provider registration is not configured",
                "OpenClaw adapter provider registration is not configured for this BFF.",
                field="openclaw_adapter",
            )

        request_payload = dict(payload or {})
        request_payload["mode"] = str(control_status.get("mode") or "")
        request_payload["operator_role"] = _operator_role_from_identity(identity)
        request_payload["confirmed"] = True
        request_payload["control_mode"] = {
            "active": True,
            "mode": control_status.get("mode"),
            "activation_id": control_status.get("activation_id") or control_status.get("activationId"),
        }
        trace_id = str(request_payload.get("traceId") or request_payload.get("trace_id") or "").strip() or None
        actor_id = str(getattr(identity, "operator_id", None) or "management-ai")
        registered = provider_register(request_payload, actor_id, trace_id)
        if isinstance(registered, dict) and isinstance(registered.get("data"), dict):
            return {
                "data": registered["data"],
                "meta": {
                    "openclawAdapterStatus": registered.get("status"),
                    "openclaw_adapter_status": registered.get("status"),
                },
            }
        return {"data": registered}

    @router.post("/provider/reauth", status_code=202)
    async def start_assistant_provider_reauth(
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        """Start a provider device-auth reauth flow through the OpenClaw adapter.

        BFF gates operator authorization, but it does not enter assistant kernel
        control mode and never receives or forwards provider credentials.  The
        adapter returns only browser-safe device flow fields.
        """
        identity = extract_identity(authorization)
        require_read_role(identity)
        _require_provider_reauth_operator(identity, bff_error=bff_error)
        if provider_reauth is None:
            _raise_error(
                bff_error,
                503,
                ErrorCode.PRECONDITION_FAILED,
                "Assistant provider reauth is not configured",
                "OpenClaw adapter provider reauth is not configured for this BFF.",
                field="openclaw_adapter",
            )

        request_payload = dict(payload or {})
        provider = str(request_payload.get("provider") or "codex").strip().lower() or "codex"
        request_payload["provider"] = provider
        request_payload["mode"] = AssistantMode.USER.value
        request_payload["operator_role"] = _operator_role_from_identity(identity)
        request_payload["confirmed"] = True
        request_payload["control_mode"] = {
            "active": False,
            "mode": AssistantMode.USER.value,
            "activation_id": None,
        }
        trace_id = str(request_payload.get("traceId") or request_payload.get("trace_id") or "").strip() or None
        actor_id = str(getattr(identity, "operator_id", None) or "management-ai")
        started = provider_reauth(request_payload, actor_id, trace_id)
        if isinstance(started, dict) and isinstance(started.get("data"), dict):
            safe_data = _sanitize_provider_reauth_payload(
                started["data"],
                mode=AssistantMode.USER.value,
                bff_error=bff_error,
            )
            return {
                "data": safe_data,
                "meta": {
                    "openclawAdapterStatus": started.get("status"),
                    "openclaw_adapter_status": started.get("status"),
                },
            }
        safe_started = _sanitize_provider_reauth_payload(
            started,
            mode=AssistantMode.USER.value,
            bff_error=bff_error,
        )
        return {"data": safe_started}

    @router.get("/provider/reauth/{session_id}")
    async def get_assistant_provider_reauth_status(
        session_id: str,
        provider: str = "codex",
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        _require_provider_reauth_operator(identity, bff_error=bff_error)
        if provider_reauth_status is None:
            _raise_error(
                bff_error,
                503,
                ErrorCode.PRECONDITION_FAILED,
                "Assistant provider reauth status is not configured",
                "OpenClaw adapter provider reauth status is not configured for this BFF.",
                field="openclaw_adapter",
            )
        actor_id = str(getattr(identity, "operator_id", None) or "management-ai")
        status = provider_reauth_status(provider, session_id, actor_id)
        if isinstance(status, dict) and isinstance(status.get("data"), dict):
            safe_data = _sanitize_provider_reauth_payload(
                status["data"],
                mode=AssistantMode.USER.value,
                bff_error=bff_error,
            )
            return {
                "data": safe_data,
                "meta": {
                    "openclawAdapterStatus": status.get("status"),
                    "openclaw_adapter_status": status.get("status"),
                },
            }
        return {
            "data": _sanitize_provider_reauth_payload(
                status,
                mode=AssistantMode.USER.value,
                bff_error=bff_error,
            )
        }

    @router.post("/provider/reauth/{session_id}/code")
    async def submit_assistant_provider_reauth_code(
        session_id: str,
        payload: dict = Body(default_factory=dict),
        provider: str = "claude",
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        _require_provider_reauth_operator(identity, bff_error=bff_error)
        if provider_reauth_code is None:
            _raise_error(
                bff_error,
                503,
                ErrorCode.PRECONDITION_FAILED,
                "Assistant provider reauth code submission is not configured",
                "OpenClaw adapter provider reauth code submission is not configured for this BFF.",
                field="openclaw_adapter",
            )

        request_payload = dict(payload or {})
        requested_provider = str(request_payload.get("provider") or provider or "claude").strip().lower() or "claude"
        code = str(
            request_payload.get("code")
            or request_payload.get("authorizationCode")
            or request_payload.get("authorization_code")
            or ""
        ).strip()
        if not code:
            _raise_error(
                bff_error,
                422,
                ErrorCode.VALIDATION_FAILED,
                "Assistant provider reauth requires an authorization code",
                "The authorization code field is required.",
                field="authorization_code",
            )

        trace_id = str(request_payload.get("traceId") or request_payload.get("trace_id") or "").strip() or None
        actor_id = str(getattr(identity, "operator_id", None) or "management-ai")
        submitted = provider_reauth_code(requested_provider, session_id, code, actor_id, trace_id)
        if isinstance(submitted, dict) and isinstance(submitted.get("data"), dict):
            safe_data = _sanitize_provider_reauth_payload(
                submitted["data"],
                mode=AssistantMode.USER.value,
                bff_error=bff_error,
            )
            return {
                "data": safe_data,
                "meta": {
                    "openclawAdapterStatus": submitted.get("status"),
                    "openclaw_adapter_status": submitted.get("status"),
                },
            }
        return {
            "data": _sanitize_provider_reauth_payload(
                submitted,
                mode=AssistantMode.USER.value,
                bff_error=bff_error,
            )
        }

    # ------------------------------------------------------------------
    # Governed tool contract routes (ASST-INTEG-004)
    # preview → validate → execute → receipt
    # All mutations route through action_catalog + command_executor.
    # ------------------------------------------------------------------

    @router.get("/tools")
    async def list_assistant_tools(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        """Return the allowlisted tool action_ids the assistant may invoke."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        return {"data": {"allowlist": sorted(ASSISTANT_TOOL_ALLOWLIST)}}

    @router.post("/tools/preview", status_code=200)
    async def preview_assistant_tool(
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        """Return a preview descriptor for a tool without executing it."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        action_id = str(payload.get("action_id") or "").strip()
        if not action_id:
            _raise_error(bff_error, 400, ErrorCode.VALIDATION_FAILED, "action_id is required", field="action_id")

        try:
            preview = preview_tool(action_id)
        except ToolNotAllowedError as exc:
            _raise_error(
                bff_error, 403, ErrorCode.FORBIDDEN,
                f"Tool not in assistant allowlist: {action_id!r}",
                str(exc),
            )

        return {
            "data": {
                "action_id": preview.action_id,
                "entity_type": preview.entity_type,
                "description": preview.description,
                "risk_level": preview.risk_level,
                "requires_reason": preview.requires_reason,
                "requires_confirmation": preview.requires_confirmation,
                "requires_confirm_token": preview.requires_confirm_token,
                "requires_two_man": preview.requires_two_man,
                "endpoint": preview.endpoint,
                "required_roles": preview.required_roles,
                "in_allowlist": preview.in_allowlist,
            }
        }

    @router.post("/tools/validate", status_code=200)
    async def validate_assistant_tool(
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        """Validate a tool request against RBAC and risk policy without executing."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        action_id = str(payload.get("action_id") or "").strip()
        if not action_id:
            _raise_error(bff_error, 400, ErrorCode.VALIDATION_FAILED, "action_id is required", field="action_id")

        try:
            result = validate_tool(
                action_id,
                payload.get("params") or {},
                list(getattr(identity, "roles", []) or []),
                reason=payload.get("reason"),
                confirm_token=payload.get("confirm_token"),
            )
        except ToolNotAllowedError as exc:
            _raise_error(
                bff_error, 403, ErrorCode.FORBIDDEN,
                f"Tool not in assistant allowlist: {action_id!r}",
                str(exc),
            )

        return {
            "data": {
                "ok": result.ok,
                "action_id": result.action_id,
                "actor_roles": result.actor_roles,
                "errors": result.errors,
                "missing_reason": result.missing_reason,
                "missing_confirm_token": result.missing_confirm_token,
            }
        }

    @router.post("/tools/execute", status_code=201)
    async def execute_assistant_tool(
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        """Execute a governed tool and return an audit receipt.

        Routes through action_catalog + command_executor. Never calls shell or
        submits hidden DOM actions.
        """
        identity = extract_identity(authorization)
        require_read_role(identity)

        action_id = str(payload.get("action_id") or "").strip()
        if not action_id:
            _raise_error(bff_error, 400, ErrorCode.VALIDATION_FAILED, "action_id is required", field="action_id")

        entity_type = str(payload.get("entity_type") or "").strip() or "Unknown"
        entity_id: Optional[str] = payload.get("entity_id")
        params = payload.get("params") or {}
        reason: Optional[str] = payload.get("reason")
        confirmed: bool = payload.get("confirmed") is True
        confirm_token: Optional[str] = payload.get("confirm_token")
        trace_id: Optional[str] = payload.get("trace_id")

        actor_id = str(getattr(identity, "operator_id", None) or "unknown")
        actor_roles: List[Any] = list(getattr(identity, "roles", []) or [])

        try:
            receipt = execute_governed_tool(
                action_id=action_id,
                entity_type=entity_type,
                entity_id=entity_id,
                params=params,
                actor_id=actor_id,
                actor_roles=actor_roles,
                reason=reason,
                confirmed=confirmed,
                confirm_token=confirm_token,
                trace_id=trace_id,
                auth_token=authorization,
            )
        except ToolNotAllowedError as exc:
            _raise_error(
                bff_error, 403, ErrorCode.FORBIDDEN,
                f"Tool not in assistant allowlist: {action_id!r}",
                str(exc),
            )
        except ToolRbacError as exc:
            _raise_error(
                bff_error, 403, ErrorCode.FORBIDDEN,
                f"Actor lacks required role for {action_id!r}",
                str(exc),
            )
        except ToolValidationError as exc:
            _raise_error(
                bff_error, 422, ErrorCode.BUSINESS_RULE_VIOLATION,
                str(exc),
                str(exc),
                field=exc.field_name,
            )

        return {"data": tool_receipt_to_dict(receipt)}

    @router.post("/control-mode/passphrase", status_code=202)
    async def update_control_mode_passphrase(
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        if "admin" not in set(getattr(identity, "roles", []) or []):
            _raise_error(
                bff_error,
                403,
                ErrorCode.FORBIDDEN,
                "Control mode passphrase changes require admin role",
                "Operator does not hold the admin role",
                field="roles",
            )
        if not getattr(identity, "mfa_verified", False):
            _raise_error(
                bff_error,
                403,
                ErrorCode.AUTH_REQUIRED,
                "Control mode passphrase changes require MFA",
                "Admin action requires MFA validation",
                field="mfa",
            )
        try:
            was_configured = _control_mode_store.configured()
            _control_mode_store.set_passphrase(
                current_passphrase=payload.get("currentPassphrase") or payload.get("current_passphrase"),
                new_passphrase=str(payload.get("newPassphrase") or payload.get("new_passphrase") or ""),
                require_current=was_configured,
            )
        except ControlModeError as exc:
            _raise_control_mode_error(bff_error, exc)
        return {
            "data": {
                "configured": True,
                "initialized": not was_configured,
                "changePassphraseHref": "/bff/assistant/control-mode/passphrase",
                "change_passphrase_href": "/bff/assistant/control-mode/passphrase",
            }
        }

    return router


def _raise_error(
    bff_error: Optional[BffErrorFactory],
    status_code: int,
    error_code: Any,
    message: str,
    detail: str = "",
    **kwargs: Any,
) -> None:
    if bff_error is not None:
        raise bff_error(
            status_code,
            error_code,
            message,
            detail or message,
            details_extra=kwargs or None,
        )
    raise HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": error_code.value if hasattr(error_code, "value") else str(error_code),
                "message": message,
                "details": kwargs if kwargs else {},
            }
        },
    )


@contextmanager
def _assistant_command_idempotency(
    store: CommandIdempotencyStore,
    *,
    actor_id: str,
    route: str,
    payload: Any,
    idempotency_key: Optional[str],
    x_idempotency_key: Optional[str],
    recovery_id: Optional[str],
    bff_error: Optional[BffErrorFactory],
) -> Iterator[Optional[CommandIdempotencyTransaction]]:
    try:
        resolved_key = resolve_command_idempotency_key(idempotency_key, x_idempotency_key)
        if resolved_key is None:
            yield None
            return
        if str(recovery_id or "").strip():
            store.recover_uncertain(
                actor_id=actor_id,
                route=route,
                idempotency_key=resolved_key,
                request_payload=payload,
                recovery_id=str(recovery_id),
            )
        with store.transaction(
            actor_id=actor_id,
            route=route,
            idempotency_key=resolved_key,
            request_payload=payload,
        ) as transaction:
            yield transaction
    except CommandIdempotencyError as exc:
        if exc.status_code == 409:
            error_code = ErrorCode.RESOURCE_CONFLICT
        elif exc.status_code == 503:
            error_code = ErrorCode.PRECONDITION_FAILED
        else:
            error_code = ErrorCode.VALIDATION_FAILED
        _raise_error(
            bff_error,
            exc.status_code,
            error_code,
            str(exc),
            str(exc),
            field="idempotency_key",
            reason=exc.reason,
            recovery=(
                "Use an authenticated operational recovery workflow after the recovery delay; "
                "never retry an uncertain mutation with a new key."
                if exc.reason == "idempotency_recovery_required"
                else None
            ),
        )


def _get_session_for_identity(session_store: Any, session_id: str, identity: Any) -> Any:
    getter = getattr(session_store, "get_for_identity", None)
    if callable(getter):
        return getter(session_id, identity)
    return session_store.get(session_id)


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed


def _require_active_control_mode(
    identity: Any,
    control_mode_store: ControlModeStore,
    *,
    bff_error: Optional[BffErrorFactory],
) -> Dict[str, Any]:
    _require_control_mode_actor(identity, bff_error=bff_error)
    actor_id = str(getattr(identity, "operator_id", None) or "")
    status = control_mode_store.status_for_actor(actor_id, touch=True)
    if not status.get("active"):
        _raise_error(
            bff_error,
            409,
            ErrorCode.PRECONDITION_FAILED,
            "Assistant dev workflow requires active control mode",
            "Activate assistant control mode before generating SA/SD or task packets",
            field="control_mode",
            reason=status.get("reason") or status.get("state") or "inactive",
        )
    return status


def _operator_role_from_identity(identity: Any) -> str:
    roles = {str(role) for role in (getattr(identity, "roles", []) or [])}
    if "admin" in roles:
        return "admin"
    if "operator" in roles:
        return "operator"
    if "approver" in roles or "capability_admin" in roles:
        return "approver"
    if "reviewer" in roles:
        return "reviewer"
    return "viewer"


def _sanitize_provider_reauth_payload(
    payload: Any,
    *,
    mode: str,
    bff_error: Optional[BffErrorFactory],
) -> Any:
    try:
        return redact_assistant_payload(
            payload,
            mode=mode,
            stage="provider_reauth_response",
            allow_kernel_override=False,
        ).value
    except RedactionError as exc:
        _raise_error(
            bff_error,
            502,
            ErrorCode.PRECONDITION_FAILED,
            "Assistant provider reauth response failed redaction",
            str(exc),
            field="provider_reauth",
            reason="redaction_failed",
        )


def _require_provider_reauth_operator(
    identity: Any,
    *,
    bff_error: Optional[BffErrorFactory],
) -> None:
    if not actor_has_control_role(identity):
        _raise_error(
            bff_error,
            403,
            ErrorCode.FORBIDDEN,
            "Assistant provider reauth requires operator or admin role",
            "Actor does not hold a role allowed to reauthenticate assistant providers",
            field="roles",
            required_roles=sorted(CONTROL_MODE_ROLES),
        )
    if not getattr(identity, "mfa_verified", False):
        _raise_error(
            bff_error,
            403,
            ErrorCode.AUTH_REQUIRED,
            "Assistant provider reauth requires MFA",
            "Actor must complete MFA before reauthenticating assistant providers",
            field="mfa",
        )


def _require_provider_registration_control(
    control_status: Dict[str, Any],
    *,
    bff_error: Optional[BffErrorFactory],
) -> None:
    mode = str(control_status.get("mode") or "")
    capabilities = {str(value) for value in (control_status.get("capabilities") or [])}
    allowed_modes = {AssistantMode.KERNEL_DEBUG.value, AssistantMode.KERNEL_REPAIR.value}
    if mode not in allowed_modes:
        _raise_error(
            bff_error,
            409,
            ErrorCode.PRECONDITION_FAILED,
            "Assistant provider registration requires active kernel_debug or kernel_repair control mode",
            "Activate assistant control mode in kernel_debug or kernel_repair before registering provider metadata",
            field="control_mode",
            reason="kernel_debug_or_repair_required",
            mode=mode or None,
        )
    if not capabilities.intersection({"assistant.kernel.debug", "assistant.kernel.repair"}):
        _raise_error(
            bff_error,
            403,
            ErrorCode.FORBIDDEN,
            "Assistant provider registration requires assistant.kernel.debug or assistant.kernel.repair capability",
            "The active control-mode activation does not include a provider-registration-capable kernel capability",
            field="capabilities",
            required_capability="assistant.kernel.debug",
            alternate_capability="assistant.kernel.repair",
        )


def _require_control_mode_actor(
    identity: Any,
    *,
    bff_error: Optional[BffErrorFactory],
) -> None:
    if not actor_has_control_role(identity):
        _raise_error(
            bff_error,
            403,
            ErrorCode.FORBIDDEN,
            "Control mode requires operator or admin role",
            "Actor does not hold a role allowed to activate control mode",
            field="roles",
            required_roles=sorted(CONTROL_MODE_ROLES),
        )
    if not getattr(identity, "mfa_verified", False):
        _raise_error(
            bff_error,
            403,
            ErrorCode.AUTH_REQUIRED,
            "Control mode requires MFA",
            "Actor must complete MFA before activating control mode",
            field="mfa",
        )
    if not actor_has_kernel_capability(identity):
        _raise_error(
            bff_error,
            422,
            ErrorCode.BUSINESS_RULE_VIOLATION,
            "Control mode requires assistant kernel capability",
            f"Actor capabilities must include a value starting with {CONTROL_MODE_CAPABILITY_PREFIX!r}",
            field="capabilities",
            required_capability_prefix=CONTROL_MODE_CAPABILITY_PREFIX,
        )


def _require_mode_capability(
    identity: Any,
    mode: AssistantMode,
    *,
    bff_error: Optional[BffErrorFactory],
) -> None:
    required = f"assistant.{mode.value.replace('_', '.')}"
    capabilities = set(actor_capabilities(identity))
    if required in capabilities:
        return
    _raise_error(
        bff_error,
        403,
        ErrorCode.FORBIDDEN,
        f"Control mode {mode.value} requires {required} capability",
        "The authenticated actor does not hold the exact capability required for the requested mode.",
        field="capabilities",
        reason="mode_capability_missing",
        required_capability=required,
    )


def _identity_tenant_values(identity: Any) -> List[str]:
    claims = getattr(identity, "claims", {}) if identity is not None else {}
    if not isinstance(claims, dict):
        claims = {}
    raw_values: List[Any] = []
    for key in ("tenant_id", "tenantId", "tenant_ids", "tenantIds", "tid"):
        value = claims.get(key)
        if isinstance(value, (list, tuple, set)):
            raw_values.extend(value)
        elif value not in (None, ""):
            raw_values.append(value)
    result: List[str] = []
    seen = set()
    for value in raw_values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _resolve_identity_tenant(
    identity: Any,
    *,
    requested_tenant: Optional[str],
    bff_error: Optional[BffErrorFactory],
) -> str:
    allowed = _identity_tenant_values(identity)
    default_tenant = str(os.getenv("PANTHEON_BFF_TENANT_ID") or "pantheon-dev").strip()
    effective_allowed = allowed or ([default_tenant] if default_tenant else [])
    requested = str(requested_tenant or "").strip()
    if requested:
        if "*" not in effective_allowed and requested not in effective_allowed:
            _raise_error(
                bff_error,
                403,
                ErrorCode.FORBIDDEN,
                "Requested tenant is outside the authenticated identity scope",
                "Repair receipts are bound to the authenticated tenant and cannot cross tenant boundaries.",
                field="tenant_id",
                reason="tenant_mismatch",
                requested_tenant=requested,
            )
        return requested
    concrete = [value for value in effective_allowed if value != "*"]
    if len(concrete) == 1:
        return concrete[0]
    if len(concrete) > 1:
        _raise_error(
            bff_error,
            422,
            ErrorCode.VALIDATION_FAILED,
            "Repair worktree preparation requires an explicit tenant",
            "Send X-Tenant-Id when the authenticated identity is scoped to more than one tenant.",
            field="tenant_id",
            reason="tenant_required",
        )
    return default_tenant or "pantheon-dev"


def _resolve_tenant_header(
    x_tenant_id: Optional[str],
    x_pantheon_tenant: Optional[str],
    *,
    bff_error: Optional[BffErrorFactory],
) -> Optional[str]:
    canonical = str(x_tenant_id or "").strip()
    alias = str(x_pantheon_tenant or "").strip()
    if canonical and alias and canonical != alias:
        _raise_error(
            bff_error,
            400,
            ErrorCode.VALIDATION_FAILED,
            "X-Tenant-Id and X-Pantheon-Tenant must match when both are supplied",
            "Conflicting tenant headers are not accepted for assistant control operations.",
            field="tenant_id",
            reason="tenant_header_conflict",
        )
    return canonical or alias or None


def _raise_control_mode_error(
    bff_error: Optional[BffErrorFactory],
    exc: ControlModeError,
) -> None:
    if exc.status_code == 403:
        code = ErrorCode.FORBIDDEN
    elif exc.status_code == 409:
        code = ErrorCode.RESOURCE_CONFLICT
    else:
        code = ErrorCode.VALIDATION_FAILED
    _raise_error(
        bff_error,
        exc.status_code,
        code,
        f"Control mode policy violation: {exc}",
        str(exc),
        field=exc.field,
        reason=exc.reason,
    )
