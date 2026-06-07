from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException
from pydantic import ValidationError

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
from .dev_bridge_models import BridgeActor, BridgeDocument, BridgeTask, DevTaskPacket
from .dev_bridge_signer import sign_packet
from .dev_docs_archiver import archive_packet, infer_repo_root
from .dev_docs_generator import generate_dev_doc_packet
from .dev_docs_models import DevDocGenerateRequest, DevDocGenerateResponse, DevDocPacket
from .models import (
    AssistantContextPack,
    AssistantContextPackRequest,
    AssistantContextPackResponse,
    AssistantMode,
    OrchestratorStatusResponse,
)
from .orchestrator_status import read_orchestrator_status
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

DEFAULT_DEV_DOC_CONTEXT_SOURCES = [
    "ui",
    "control_room",
    "jobs",
    "alerts",
    "audit",
    "recent_sse",
    "persona_health",
    "strategy_health",
    "management_nl",
    "docs_rag",
    "repo_status",
]


def create_assistant_router(
    *,
    build_context_pack: BuildContextPack,
    extract_identity: ExtractIdentity,
    require_read_role: RequireReadRole,
    bff_error: Optional[BffErrorFactory] = None,
    session_store: Optional[Any] = None,
    transcript_store: Optional[Any] = None,
    control_mode_store: Optional[ControlModeStore] = None,
    dev_docs_repo_root: Optional[str] = None,
    bridge_key_store: Optional[Dict[str, bytes]] = None,
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
    # Orchestrator status readback (ASST-INTEG-007)
    # ------------------------------------------------------------------

    @router.get("/orchestrator/status")
    async def get_orchestrator_status(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        """Return the current orchestrator status, tasks, and worker activity.

        Assistant uses this to report CI/CD and worker progress to the user
        without needing direct shell or provider credentials.
        """
        identity = extract_identity(authorization)
        require_read_role(identity)

        status = read_orchestrator_status()
        return {"data": status.model_dump(mode="json", by_alias=True)}

    # ------------------------------------------------------------------
    # SA/SD generation and signed dev task packet bridge
    # ------------------------------------------------------------------

    @router.post("/dev-docs/generate", status_code=201)
    async def generate_assistant_dev_docs(
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        """Generate SA/SD artifacts from a Management AI conversation.

        Requires active control mode because archive=true writes repo docs and
        task briefs. BFF may emit a signed packet but never dispatches it.
        """
        identity = extract_identity(authorization)
        require_read_role(identity)
        control_status = _require_active_control_mode(
            identity,
            _control_mode_store,
            bff_error=bff_error,
        )

        request = _parse_dev_doc_request(payload, bff_error=bff_error)
        turns = _conversation_turns_for_request(_transcript_store, request)
        context_pack = _context_pack_for_dev_docs(
            payload,
            request,
            identity,
            control_status=control_status,
            build_context_pack=build_context_pack,
            bff_error=bff_error,
        )

        packet = generate_dev_doc_packet(
            request=request,
            turns=turns,
            context_pack=context_pack,
        )

        archive_locations = None
        if request.archive:
            archive_locations = archive_packet(
                packet,
                repo_root=_dev_docs_repo_root(dev_docs_repo_root),
            )
            packet = packet.model_copy(update={"archive_locations": archive_locations})

        meta: Dict[str, Any] = {
            "archived": archive_locations is not None,
            "archiveLocations": archive_locations.model_dump(mode="json", by_alias=True)
            if archive_locations is not None
            else None,
            "devBridge": _dev_bridge_meta(),
        }

        if _truthy(
            payload.get(
                "emitTaskPacket",
                payload.get(
                    "emit_task_packet",
                    payload.get("signTaskPacket", payload.get("sign_task_packet")),
                ),
            )
        ):
            task_packet = _signed_dev_task_packet(
                packet,
                identity,
                mode=str(control_status.get("mode") or AssistantMode.KERNEL_DEBUG.value),
                key_store=bridge_key_store,
            )
            meta["taskPacket"] = task_packet.model_dump(mode="json", by_alias=True)

        response = DevDocGenerateResponse(data=packet, meta=meta)
        return response.model_dump(mode="json", by_alias=True)

    @router.get("/dev-docs/{packet_id}")
    async def get_assistant_dev_doc_packet(
        packet_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        _require_active_control_mode(identity, _control_mode_store, bff_error=bff_error)

        packet = _load_archived_dev_doc_packet(
            packet_id,
            repo_root=_dev_docs_repo_root(dev_docs_repo_root),
            bff_error=bff_error,
        )
        return {"data": packet.model_dump(mode="json", by_alias=True)}

    @router.post("/dev-bridge/task-packet", status_code=201)
    async def create_assistant_dev_task_packet(
        payload: dict = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        """Sign a DevTaskPacket for repo-local dispatch.

        This route intentionally does not call the dispatcher. Trusted
        repo-local automation verifies and materialises the packet.
        """
        identity = extract_identity(authorization)
        require_read_role(identity)
        control_status = _require_active_control_mode(
            identity,
            _control_mode_store,
            bff_error=bff_error,
        )

        packet = _parse_dev_doc_packet(payload, bff_error=bff_error)
        task_packet = _signed_dev_task_packet(
            packet,
            identity,
            mode=str(payload.get("mode") or control_status.get("mode") or AssistantMode.KERNEL_DEBUG.value),
            key_store=bridge_key_store,
        )
        return {
            "data": task_packet.model_dump(mode="json", by_alias=True),
            "meta": _dev_bridge_meta(),
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


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _dev_docs_repo_root(configured_root: Optional[str]) -> str:
    if configured_root:
        return str(configured_root)
    return infer_repo_root()


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


def _parse_dev_doc_request(
    payload: dict,
    *,
    bff_error: Optional[BffErrorFactory],
) -> DevDocGenerateRequest:
    try:
        return DevDocGenerateRequest(**payload)
    except ValidationError as exc:
        _raise_error(
            bff_error,
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid assistant dev docs request",
            str(exc),
            field="payload",
            errors=exc.errors(),
        )
    raise AssertionError("unreachable")


def _parse_dev_doc_packet(
    payload: dict,
    *,
    bff_error: Optional[BffErrorFactory],
) -> DevDocPacket:
    raw_packet = (
        payload.get("devDocPacket")
        or payload.get("dev_doc_packet")
        or payload.get("packet")
        or payload
    )
    if isinstance(raw_packet, dict) and isinstance(raw_packet.get("data"), dict):
        raw_packet = raw_packet["data"]
    if not isinstance(raw_packet, dict):
        _raise_error(
            bff_error,
            422,
            ErrorCode.VALIDATION_FAILED,
            "devDocPacket must be an object",
            field="devDocPacket",
        )
    try:
        return DevDocPacket(**raw_packet)
    except ValidationError as exc:
        _raise_error(
            bff_error,
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid assistant dev doc packet",
            str(exc),
            field="devDocPacket",
            errors=exc.errors(),
        )
    raise AssertionError("unreachable")


def _conversation_turns_for_request(
    transcript_store: Any,
    request: DevDocGenerateRequest,
) -> List[Dict[str, Any]]:
    turns = [turn_to_dict(turn) for turn in transcript_store.list_turns(request.conversation_id)]
    if not request.turn_ids:
        return turns
    wanted = {str(turn_id) for turn_id in request.turn_ids}
    return [turn for turn in turns if str(turn.get("turn_id")) in wanted]


def _context_pack_for_dev_docs(
    payload: dict,
    request: DevDocGenerateRequest,
    identity: Any,
    *,
    control_status: Dict[str, Any],
    build_context_pack: BuildContextPack,
    bff_error: Optional[BffErrorFactory],
) -> Any:
    explicit_pack = payload.get("contextPack")
    if explicit_pack is None:
        explicit_pack = payload.get("context_pack")
    if explicit_pack is not None:
        return explicit_pack

    raw_request = payload.get("contextPackRequest")
    if raw_request is None:
        raw_request = payload.get("context_pack_request")
    if raw_request is None:
        raw_request = {
            "mode": payload.get("mode") or control_status.get("mode") or AssistantMode.KERNEL_DEBUG.value,
            "include": payload.get("include") or DEFAULT_DEV_DOC_CONTEXT_SOURCES,
            "question": request.feature_summary,
        }
    if not isinstance(raw_request, dict):
        _raise_error(
            bff_error,
            422,
            ErrorCode.VALIDATION_FAILED,
            "contextPackRequest must be an object",
            field="contextPackRequest",
        )

    try:
        context_request = AssistantContextPackRequest(**raw_request)
    except ValidationError as exc:
        _raise_error(
            bff_error,
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid assistant context pack request",
            str(exc),
            field="contextPackRequest",
            errors=exc.errors(),
        )
        raise AssertionError("unreachable")

    try:
        assert_kernel_allowed(context_request.mode)
    except ModePolicyViolation as exc:
        _raise_error(
            bff_error,
            403,
            ErrorCode.FORBIDDEN,
            f"Mode policy violation: {exc}",
            str(exc),
            field=exc.field,
        )

    try:
        return build_context_pack(request.conversation_id, context_request, identity)
    except AssistantContextPolicyError as exc:
        _raise_error(
            bff_error,
            403,
            ErrorCode.FORBIDDEN,
            "Assistant context source is not allowed for this mode",
            str(exc),
            precondition_failed="assistant_context_mode_policy",
            denied_sources=exc.denied_sources,
        )
    raise AssertionError("unreachable")


def _load_archived_dev_doc_packet(
    packet_id: str,
    *,
    repo_root: str,
    bff_error: Optional[BffErrorFactory],
) -> DevDocPacket:
    clean_packet_id = str(packet_id or "").strip()
    if not clean_packet_id or any(not (ch.isalnum() or ch in {"_", "-"}) for ch in clean_packet_id):
        _raise_error(
            bff_error,
            400,
            ErrorCode.VALIDATION_FAILED,
            "Invalid packet_id",
            field="packet_id",
        )

    root = Path(repo_root)
    matches = sorted(root.glob(f"docs/04/sa_sd_{clean_packet_id}_*/dev_doc_packet.json"))
    if not matches:
        _raise_error(
            bff_error,
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            f"Archived dev doc packet not found: {clean_packet_id!r}",
            field="packet_id",
        )
    try:
        raw = json.loads(matches[0].read_text(encoding="utf-8"))
        return DevDocPacket(**raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        _raise_error(
            bff_error,
            500,
            ErrorCode.INTERNAL_ERROR,
            f"Archived dev doc packet is unreadable: {clean_packet_id!r}",
            str(exc),
            field="packet_id",
        )
    raise AssertionError("unreachable")


def _signed_dev_task_packet(
    packet: DevDocPacket,
    identity: Any,
    *,
    mode: str,
    key_store: Optional[Dict[str, bytes]],
) -> DevTaskPacket:
    task_packet = DevTaskPacket(
        packetId=f"bridge_{packet.packet_id}",
        emittedAt=_utc_now_z(),
        actor=BridgeActor(
            id=str(getattr(identity, "operator_id", None) or packet.actor_id or "unknown"),
            roles=[str(role) for role in (getattr(identity, "roles", []) or [])],
            capabilities=actor_capabilities(identity),
        ),
        mode=mode,
        sourceConversationId=packet.conversation_id,
        sourceTurnIds=[
            ref.turn_id for ref in (packet.requirement_capture.source_turn_refs or [])
        ],
        documents=_bridge_documents(packet),
        tasks=_bridge_tasks(packet),
        auditConversationHref=f"/bff/assistant/sessions/{packet.conversation_id}/transcript",
    )
    return sign_packet(task_packet, key_store=key_store)


def _bridge_documents(packet: DevDocPacket) -> List[BridgeDocument]:
    locations = packet.archive_locations
    docs: List[BridgeDocument] = []
    if locations is not None:
        doc_specs = [
            (
                locations.requirement_capture,
                "REQUIREMENT_CAPTURE",
                packet.requirement_capture.source_refs,
            ),
            (
                locations.system_analysis,
                "SYSTEM_ANALYSIS",
                packet.system_analysis.source_refs,
            ),
            (
                locations.system_design,
                "SYSTEM_DESIGN",
                packet.system_design.source_refs,
            ),
        ]
        for path, kind, refs in doc_specs:
            if path:
                docs.append(
                    BridgeDocument(
                        path=path,
                        kind=kind,
                        sourceRefs=_source_ref_ids(refs),
                    )
                )
        for path in locations.task_briefs or []:
            docs.append(
                BridgeDocument(
                    path=path,
                    kind="TASK_BRIEF",
                    sourceRefs=_source_ref_ids(packet.source_refs),
                )
            )
        return docs

    seen_paths = set()
    for task in packet.execution_tasks:
        for path in task.artifacts or []:
            clean_path = str(path or "").strip()
            if not clean_path or clean_path in seen_paths:
                continue
            if clean_path.startswith("docs/") or clean_path.startswith(".orchestrator/task-briefs/"):
                seen_paths.add(clean_path)
                docs.append(
                    BridgeDocument(
                        path=clean_path,
                        kind="PLANNED_ARTIFACT",
                        sourceRefs=_source_ref_ids(task.source_refs or packet.source_refs),
                    )
                )
    return docs


def _bridge_tasks(packet: DevDocPacket) -> List[BridgeTask]:
    return [
        BridgeTask(
            id=task.task_id,
            title=task.title,
            owner=task.owner,
            reviewer=task.reviewer,
            phase=task.phase,
            dependsOn=list(task.depends_on or []),
            artifacts=list(task.artifacts or []),
            acceptance=list(task.acceptance or []),
            summary=task.summary,
        )
        for task in packet.execution_tasks
    ]


def _source_ref_ids(refs: List[Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for ref in refs or []:
        if isinstance(ref, dict):
            source_id = ref.get("source_id") or ref.get("sourceId")
        else:
            source_id = getattr(ref, "source_id", None)
        clean = str(source_id or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _dev_bridge_meta() -> Dict[str, Any]:
    return {
        "dispatchMode": "repo_local_required",
        "handoffMode": "repo_local_supervisor_inbox",
        "noDirectShellFromWeb": True,
        "dispatcher": "assistant.dev_bridge_dispatcher.dispatch_task_packet",
        "queueCommand": "python3 scripts/queue_assistant_dev_task_packet.py",
        "drainCommand": "python3 scripts/drain_assistant_dev_task_packet_inbox.py",
        "supervisorInboxPath": ".orchestrator/assistant-dev-packets",
        "orchestratorStatusHref": "/bff/assistant/orchestrator/status",
    }


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
