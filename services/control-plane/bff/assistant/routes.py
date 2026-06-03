from __future__ import annotations

from typing import Any, Callable, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException

from models import ErrorCode

from .context_composer import AssistantContextPolicyError
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
from .models import AssistantContextPack, AssistantContextPackRequest, AssistantContextPackResponse, AssistantMode
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


def create_assistant_router(
    *,
    build_context_pack: BuildContextPack,
    extract_identity: ExtractIdentity,
    require_read_role: RequireReadRole,
    bff_error: Optional[BffErrorFactory] = None,
    session_store: Optional[Any] = None,
    transcript_store: Optional[Any] = None,
    control_mode_store: Optional[ControlModeStore] = None,
) -> APIRouter:
    router = APIRouter(prefix="/bff/assistant", tags=["assistant"])

    _session_store = session_store if session_store is not None else InMemorySessionStore()
    _transcript_store = transcript_store if transcript_store is not None else InMemoryTranscriptStore()
    _control_mode_store = control_mode_store if control_mode_store is not None else ControlModeStore()

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
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        _require_control_mode_actor(identity, bff_error=bff_error)

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

        ttl_seconds = _positive_int(payload.get("ttlSeconds", payload.get("ttl_seconds")), DEFAULT_KERNEL_TTL_SECONDS)
        idle_ttl_seconds = _positive_int(
            payload.get("idleTtlSeconds", payload.get("idle_ttl_seconds")),
            default_idle_ttl(ttl_seconds),
        )
        try:
            activation = _control_mode_store.activate(
                actor_id=identity.operator_id,
                mode=mode,
                capabilities=actor_capabilities(identity),
                reason=str(payload.get("reason") or "").strip(),
                passphrase=str(payload.get("passphrase") or payload.get("phrase") or ""),
                ttl_seconds=ttl_seconds,
                idle_ttl_seconds=idle_ttl_seconds,
                management_session_id=payload.get("managementSessionId") or payload.get("management_session_id"),
            )
        except ControlModeError as exc:
            _raise_control_mode_error(bff_error, exc)
        return {"data": activation}

    @router.post("/control-mode/deactivate", status_code=202)
    async def deactivate_control_mode(
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return {
            "data": _control_mode_store.deactivate(
                identity.operator_id,
                reason=str(payload.get("reason") or "operator_deactivated").strip(),
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
