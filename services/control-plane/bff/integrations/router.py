"""Tools & Integrations Domain Router.

This domain router owns the 35 Tools, MCP, Skills, OpenClaw, and Channels
route decorators catalogued for ``OPGAP-BE-TOOLS-INTEGRATIONS-V2-20260830``.

It has NO reverse dependency on ``main.py``; the BFF composition root mounts
this router via ``app.include_router(create_integrations_router(...))``.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from fastapi import APIRouter, Body, Header, HTTPException, Query
from starlette.responses import JSONResponse

try:
    from models import (
        ActionCommandStatus,
        CommandResponse,
        CommandType,
        ErrorCode,
        McpImportedTool,
        McpRejectedTool,
        McpToolActionData,
        McpToolActionRequest,
        McpToolActionVerb,
        McpToolClass,
        McpToolDescriptor,
        McpToolImportData,
        McpToolImportRequest,
        McpToolLifecycleStatus,
        ObjectType,
        OperatorIdentity,
        TargetObject,
        utc_now,
    )
except ImportError:
    try:
        from ..models import (  # type: ignore[no-redef]
            ActionCommandStatus,
            CommandResponse,
            CommandType,
            ErrorCode,
            McpImportedTool,
            McpRejectedTool,
            McpToolActionData,
            McpToolActionRequest,
            McpToolActionVerb,
            McpToolClass,
            McpToolDescriptor,
            McpToolImportData,
            McpToolImportRequest,
            McpToolLifecycleStatus,
            ObjectType,
            OperatorIdentity,
            TargetObject,
            utc_now,
        )
    except Exception:
        pass

try:
    from openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError
except ImportError:
    try:
        from ..openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError  # type: ignore[no-redef]
    except Exception:
        pass

from .service import (
    IntegrationsService,
    default_bff_error,
    deprecated_bff_path_response,
    dry_run_success_response,
    page_slice,
    reject_body_idempotency_key,
    resolve_final_idempotency_key,
    stable_json_hash,
    utc_now_rfc3339,
)

log = logging.getLogger(__name__)

PageSlice = Callable[[Sequence[Any], Optional[str], int], Tuple[List[Any], Optional[str]]]


def _default_extract_identity(authorization: Optional[str] = None) -> Any:
    class Identity:
        operator_id = "operator-1"
        roles = {"operator", "viewer", "reviewer", "approver", "admin"}

    return Identity()


def _default_require_role(identity: Any) -> None:
    return None


def _default_snapshot_meta(snapshot_at: str) -> Dict[str, Any]:
    return {"snapshot_at": snapshot_at}


def _default_read_surface_meta(
    dataset: str,
    surface_key: str,
    *,
    snapshot_at: str,
    total: Optional[int] = None,
    **_: Any,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surfaces": {
            surface_key: {"status": "ok", "source": "ok", "dataset": dataset, "snapshot_at": snapshot_at}
        },
    }
    if total is not None:
        meta["total"] = total
    return meta


def create_integrations_router(
    *,
    service: Optional[IntegrationsService] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    openclaw_client: Optional[Any] = None,
    extract_identity: Optional[Callable[[Optional[str]], Any]] = None,
    require_read_role: Optional[Callable[[Any], None]] = None,
    require_operator_role: Optional[Callable[[Any], None]] = None,
    require_mcp_tool_write_role: Optional[Callable[[Any], None]] = None,
    require_openclaw_command_role: Optional[Callable[[Any], None]] = None,
    bff_error: Optional[Callable[..., Exception]] = None,
    utc_now_fn: Optional[Callable[[], str]] = None,
    page_slice_fn: Optional[PageSlice] = None,
    snapshot_meta: Optional[Callable[[str], Dict[str, Any]]] = None,
    read_surface_meta: Optional[Callable[..., Dict[str, Any]]] = None,
    submit_command: Optional[Callable[..., Any]] = None,
) -> APIRouter:
    """Build the exact 35-route Tools & Integrations domain router."""

    router = APIRouter()
    _extract = extract_identity or _default_extract_identity
    _require_read = require_read_role or _default_require_role
    _require_operator = require_operator_role or _default_require_role
    _err = bff_error or default_bff_error
    _now = utc_now_fn or utc_now_rfc3339
    _page = page_slice_fn or page_slice
    _snapshot = snapshot_meta or _default_snapshot_meta
    _read_meta = read_surface_meta or _default_read_surface_meta

    resolved_service = service
    if resolved_service is None:
        read_st = get_read_store() if get_read_store else None
        resolved_service = IntegrationsService(
            read_store=read_st,
            openclaw_client=openclaw_client,
            utc_now_fn=_now,
            bff_error_fn=_err,
            page_slice_fn=_page,
            submit_command_fn=submit_command,
        )

    _require_openclaw = require_openclaw_command_role or resolved_service.require_openclaw_command_role
    _require_mcp_write = require_mcp_tool_write_role or resolved_service.require_mcp_tool_write_role

    # ========================================================================
    # 1. OpenClaw Operator Surfaces (7 handlers, 8 decorators)
    # ========================================================================

    @router.get("/api/v1/operator/openclaw/ops")
    async def get_openclaw_ops(
        session_limit: int = Query(default=25, ge=1, le=100),
        audit_limit: int = Query(default=20, ge=1, le=100),
        state: Optional[str] = Query(default=None),
        operator_id: Optional[str] = Query(default=None),
        agent_id: Optional[str] = Query(default=None),
        session_id: Optional[str] = Query(default=None),
        mode: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_read(identity)
        authorized_operator_id = resolved_service.authorized_openclaw_operator_filter(
            identity, operator_id
        )
        op_id = getattr(identity, "operator_id", None) or getattr(identity, "id", "operator-1")
        return resolved_service.build_openclaw_ops_response(
            session_limit=session_limit,
            audit_limit=audit_limit,
            state=state,
            operator_id=authorized_operator_id,
            agent_id=agent_id,
            effective_tools_session_id=session_id,
            requesting_operator_id=op_id,
            effective_tools_mode=mode,
            requesting_operator_role=resolved_service.openclaw_effective_operator_role(identity),
            surface_key="openclaw_ops",
        )

    @router.get("/api/v1/operator/openclaw/tool-workflow-bridge")
    async def get_openclaw_tool_workflow_bridge(
        session_limit: int = Query(default=25, ge=1, le=100),
        audit_limit: int = Query(default=20, ge=1, le=100),
        state: Optional[str] = Query(default=None),
        operator_id: Optional[str] = Query(default=None),
        agent_id: Optional[str] = Query(default=None),
        session_id: Optional[str] = Query(default=None),
        mode: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_read(identity)
        authorized_operator_id = resolved_service.authorized_openclaw_operator_filter(
            identity, operator_id
        )
        op_id = getattr(identity, "operator_id", None) or getattr(identity, "id", "operator-1")
        return resolved_service.build_openclaw_ops_response(
            session_limit=session_limit,
            audit_limit=audit_limit,
            state=state,
            operator_id=authorized_operator_id,
            agent_id=agent_id,
            effective_tools_session_id=session_id,
            requesting_operator_id=op_id,
            effective_tools_mode=mode,
            requesting_operator_role=resolved_service.openclaw_effective_operator_role(identity),
            surface_key="openclaw_tool_workflow_bridge",
        )

    @router.post("/api/v1/operator/openclaw/sessions")
    async def create_openclaw_session(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> JSONResponse:
        identity = _extract(authorization)
        _require_openclaw(identity)
        idempotency_key = resolved_service.require_openclaw_idempotency_key(x_idempotency_key)
        op_id = getattr(identity, "operator_id", None) or getattr(identity, "id", "operator-1")
        client = resolved_service.get_openclaw_client()
        try:
            adapter_payload = client.create_session(
                agent_id=payload.get("agent_id") or payload.get("agentId") or "assistant",
                operator_id=op_id,
                idempotency_key=idempotency_key,
                tools_mode=payload.get("tools_mode") or payload.get("toolsMode") or "read_only",
                metadata=payload.get("metadata") or {},
            )
        except OpenClawOpsClientError as exc:
            raise resolved_service.openclaw_client_error(exc) from exc
        accepted_at = _now()
        status_code = 200 if adapter_payload.get("replayed") else 202
        return JSONResponse(
            status_code=status_code,
            content=resolved_service.openclaw_command_payload(
                command="OpenClawCreateSession",
                adapter_payload=adapter_payload,
                accepted_at=accepted_at,
            ),
        )

    @router.post("/api/v1/operator/openclaw/sessions/{session_id}/cancel")
    async def cancel_openclaw_session(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> JSONResponse:
        identity = _extract(authorization)
        _require_openclaw(identity)
        idempotency_key = resolved_service.require_openclaw_idempotency_key(x_idempotency_key)
        op_id = getattr(identity, "operator_id", None) or getattr(identity, "id", "operator-1")
        client = resolved_service.get_openclaw_client()
        try:
            adapter_payload = client.cancel_session(
                session_id=session_id,
                operator_id=op_id,
                idempotency_key=idempotency_key,
            )
        except OpenClawOpsClientError as exc:
            raise resolved_service.openclaw_client_error(exc) from exc
        accepted_at = _now()
        return JSONResponse(
            status_code=202,
            content=resolved_service.openclaw_command_payload(
                command="OpenClawCancelSession",
                adapter_payload=adapter_payload,
                accepted_at=accepted_at,
            ),
        )

    @router.get("/api/v1/operator/openclaw/live-gate/status")
    async def get_openclaw_live_gate_status(
        authorization: Optional[str] = Header(default=None),
    ) -> JSONResponse:
        identity = _extract(authorization)
        _require_openclaw(identity)
        client = resolved_service.get_openclaw_client()
        try:
            payload = client.get_live_gate_status()
        except OpenClawOpsClientError as exc:
            raise resolved_service.openclaw_client_error(exc) from exc
        return JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "surface": "openclaw_live_gate_status",
                "data": payload,
                "snapshot_at": _now(),
            },
        )

    @router.get("/api/v1/operator/openclaw/live-gate/audit")
    async def get_openclaw_live_gate_audit(
        capital_pool_id: Optional[str] = None,
        limit: int = 100,
        authorization: Optional[str] = Header(default=None),
    ) -> JSONResponse:
        identity = _extract(authorization)
        _require_openclaw(identity)
        roles = set(getattr(identity, "roles", []) or [])
        op_id = getattr(identity, "operator_id", None) or getattr(identity, "id", "operator-1")
        filtered_operator_id = None if "admin" in roles else op_id
        client = resolved_service.get_openclaw_client()
        try:
            payload = client.list_live_gate_audit(
                operator_id=filtered_operator_id,
                capital_pool_id=capital_pool_id,
                limit=limit,
            )
        except OpenClawOpsClientError as exc:
            raise resolved_service.openclaw_client_error(exc) from exc
        return JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "surface": "openclaw_live_gate_audit",
                "data": payload,
                "snapshot_at": _now(),
            },
        )

    @router.get(
        "/api/v1/operator/openclaw/broker-adapter-readiness",
        operation_id="get_openclaw_broker_adapter_readiness_legacy",
    )
    @router.get(
        "/api/v1/operator/openclaw/broker/adapter-readiness",
        operation_id="get_openclaw_broker_adapter_readiness",
    )
    async def get_openclaw_broker_adapter_readiness(
        authorization: Optional[str] = Header(default=None),
    ) -> JSONResponse:
        identity = _extract(authorization)
        _require_openclaw(identity)
        surface = resolved_service.get_openclaw_broker_adapter_readiness()
        return JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "surface": "openclaw_broker_adapter_readiness",
                "data": surface,
                "snapshot_at": _now(),
            },
        )

    # ========================================================================
    # 2. MCP Server Tool Import & Action Admission (3 handlers, 4 decorators)
    # ========================================================================

    @router.post(
        "/bff/v1/mcp/servers/{server_id}/import-tools",
        response_model=CommandResponse[McpToolImportData],
    )
    @router.post(
        "/bff/mcp-servers/{server_id}/import-tools",
        response_model=CommandResponse[McpToolImportData],
    )
    async def import_mcp_server_tools(
        server_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> CommandResponse[McpToolImportData]:
        identity = _extract(authorization)
        _require_mcp_write(identity)
        return resolved_service.import_mcp_server_tools(
            server_id=server_id,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            x_request_id=x_request_id,
        )

    @router.post(
        "/bff/mcp-tools/{tool_id}/{action}",
        response_model=CommandResponse[McpToolActionData],
    )
    async def admit_mcp_tool_action_alias(
        tool_id: str,
        action: McpToolActionVerb,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> CommandResponse[McpToolActionData]:
        clean_tool_id = str(tool_id or "").strip()
        if not clean_tool_id:
            raise _err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "MCP tool id is required",
                "tool_id path parameter must be a non-empty string",
                precondition_failed="tool_id",
            )
        reject_body_idempotency_key(payload, bff_error_fn=_err)
        request = resolved_service.parse_mcp_tool_action_payload(payload)
        resolved_server_id = resolved_service.resolve_mcp_server_id_for_tool(
            clean_tool_id, request
        )
        return await admit_mcp_tool_action(
            server_id=resolved_server_id,
            tool_id=clean_tool_id,
            action=action,
            payload=payload,
            authorization=authorization,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            x_request_id=x_request_id,
        )

    @router.post(
        "/bff/v1/mcp/servers/{server_id}/tools/{tool_id}/actions/{action}",
        response_model=CommandResponse[McpToolActionData],
    )
    async def admit_mcp_tool_action(
        server_id: str,
        tool_id: str,
        action: McpToolActionVerb,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> CommandResponse[McpToolActionData]:
        identity = _extract(authorization)
        _require_mcp_write(identity)
        return resolved_service.admit_mcp_tool_action(
            server_id=server_id,
            tool_id=tool_id,
            action=action,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            x_request_id=x_request_id,
        )

    # ========================================================================
    # 3. Tools, MCP servers, Skills compatibility routes (17 handlers, 17 decorators)
    # ========================================================================

    @router.get("/bff/tools")
    async def bff_list_tools(
        status: Optional[str] = None,
        tool_class: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_read(identity)
        snapshot_at = _now()
        items = resolved_service.merged_tool_records()
        if status:
            items = [t for t in items if t.get("status") == status]
        if tool_class:
            items = [t for t in items if t.get("tool_class") == tool_class]
        total = len(items)
        page_items, next_page_token = _page(items, page_token, page_size)
        return {
            "data": page_items,
            "page_info": {"next_page_token": next_page_token, "total": total},
            "meta": _read_meta("tools", "tool_list", snapshot_at=snapshot_at, total=total),
        }

    @router.post("/bff/tools", status_code=201)
    async def bff_create_tool(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_read(identity)
        reject_body_idempotency_key(payload, bff_error_fn=_err)
        resolved_key = resolve_final_idempotency_key(
            idempotency_key, x_idempotency_key, bff_error_fn=_err
        )
        request_hash = stable_json_hash({"route": "POST /bff/tools", "payload": payload})
        cached = resolved_service.tools_bff_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached

        name = str(payload.get("name") or "").strip()
        if not name:
            raise _err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "name is required",
                "Tool name must be a non-empty string",
                precondition_failed="name",
            )
        snapshot_at = _now()
        tool_id = f"tool-{snapshot_at[:10].replace("-", "")}-{uuid.uuid4().hex[:8]}"
        op_id = getattr(identity, "operator_id", None) or getattr(identity, "id", "operator-1")
        result = {
            "id": tool_id,
            "tool_id": tool_id,
            "name": name,
            "status": "draft",
            "tool_class": payload.get("tool_class") or "generic",
            "description": payload.get("description") or "",
            "input_schema": payload.get("input_schema") or {},
            "output_schema": payload.get("output_schema") or {},
            "mcp_sourced": False,
            "created_at": snapshot_at,
            "updated_at": snapshot_at,
            "created_by": op_id,
        }
        resolved_service.tool_registry[tool_id] = result
        resolved_service.tools_bff_idempotency[resolved_key] = {
            "request_hash": request_hash,
            "result": result,
        }
        return result

    @router.get("/bff/tools/{tool_id}")
    async def bff_get_tool(
        tool_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_read(identity)
        clean_id = str(tool_id or "").strip()
        if not clean_id:
            raise _err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "tool_id is required",
                "tool_id path parameter must be a non-empty string",
                precondition_failed="tool_id",
            )
        record = resolved_service.find_record_by_id(
            resolved_service.merged_tool_records(), clean_id, ("tool_id", "id")
        )
        if record is None:
            for reg_record in resolved_service.merged_mcp_tool_records():
                if reg_record.get("tool_id") == clean_id:
                    record = {
                        "id": clean_id,
                        "tool_id": clean_id,
                        "name": reg_record.get("name", clean_id),
                        "status": reg_record.get("status", "imported"),
                        "tool_class": reg_record.get("tool_class", ""),
                        "mcp_sourced": True,
                        "server_id": reg_record.get("server_id"),
                        "updated_at": _now(),
                    }
                    break
        if record is None:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Tool not found",
                f"tool_id={clean_id!r} is not registered",
                precondition_failed="tool_id",
            )
        return record

    @router.patch("/bff/tools/{tool_id}")
    async def bff_patch_tool(
        tool_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_read(identity)
        clean_id = str(tool_id or "").strip()
        if not clean_id:
            raise _err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "tool_id is required",
                "tool_id path parameter must be a non-empty string",
                precondition_failed="tool_id",
            )
        reject_body_idempotency_key(payload, bff_error_fn=_err)
        resolved_key = resolve_final_idempotency_key(
            idempotency_key, x_idempotency_key, bff_error_fn=_err
        )
        request_hash = stable_json_hash(
            {"route": "PATCH /bff/tools/{tool_id}", "id": clean_id, "payload": payload}
        )
        cached = resolved_service.tools_bff_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached
        record = resolved_service.tool_registry.get(clean_id)
        if record is None:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Tool not found",
                f"tool_id={clean_id!r} is not registered",
                precondition_failed="tool_id",
            )
        allowed_patches = {
            "name",
            "description",
            "status",
            "input_schema",
            "output_schema",
            "tool_class",
        }
        for field in allowed_patches:
            if field in payload:
                record[field] = payload[field]
        record["updated_at"] = _now()
        resolved_service.tools_bff_idempotency[resolved_key] = {
            "request_hash": request_hash,
            "result": record,
        }
        return record

    @router.post("/bff/tools/{tool_id}/actions/{action_id}", status_code=202)
    async def bff_tool_action(
        tool_id: str,
        action_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> JSONResponse:
        return deprecated_bff_path_response(
            route="/bff/tools/{tool_id}/actions/{action_id}",
            replacement="/bff/actions/tool/{tool_id}/{action_id}",
        )

    @router.get("/bff/mcp/servers")
    async def bff_list_mcp_servers(
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> JSONResponse:
        return deprecated_bff_path_response(
            route="/bff/mcp/servers",
            replacement="/bff/mcp-servers",
        )

    @router.post("/bff/mcp/servers", status_code=201)
    async def bff_create_mcp_server(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_mcp_write(identity)
        reject_body_idempotency_key(payload, bff_error_fn=_err)
        resolved_key = resolve_final_idempotency_key(
            idempotency_key, x_idempotency_key, bff_error_fn=_err
        )
        request_hash = stable_json_hash({"route": "POST /bff/mcp/servers", "payload": payload})
        cached = resolved_service.mcp_server_bff_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached

        name = str(payload.get("name") or "").strip()
        if not name:
            raise _err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "name is required",
                "MCP server name must be a non-empty string",
                precondition_failed="name",
            )
        snapshot_at = _now()
        server_id = f"mcp-srv-{snapshot_at[:10].replace("-", "")}-{uuid.uuid4().hex[:8]}"
        op_id = getattr(identity, "operator_id", None) or getattr(identity, "id", "operator-1")
        result = {
            "id": server_id,
            "server_id": server_id,
            "name": name,
            "status": "registered",
            "endpoint": payload.get("endpoint") or "",
            "server_version": payload.get("server_version") or "",
            "governance": payload.get("governance") or {},
            "created_at": snapshot_at,
            "updated_at": snapshot_at,
            "created_by": op_id,
        }
        resolved_service.mcp_server_registry[server_id] = result
        resolved_service.mcp_server_bff_idempotency[resolved_key] = {
            "request_hash": request_hash,
            "result": result,
        }
        return result

    @router.get("/bff/mcp/servers/{server_id}")
    async def bff_get_mcp_server(
        server_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> JSONResponse:
        return deprecated_bff_path_response(
            route="/bff/mcp/servers/{server_id}",
            replacement="/bff/mcp-servers/{server_id}",
        )

    @router.post("/bff/mcp/servers/{server_id}/actions/{action_id}", status_code=202)
    async def bff_mcp_server_action(
        server_id: str,
        action_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_mcp_write(identity)
        reject_body_idempotency_key(payload, bff_error_fn=_err)
        resolved_key = resolve_final_idempotency_key(
            idempotency_key, x_idempotency_key, bff_error_fn=_err
        )
        clean_id = resolved_service.validate_mcp_server_id(server_id)
        return resolved_service.tools_mcp_skills_action_command(
            entity_type=ObjectType.MCP_SERVER,
            entity_id=clean_id,
            action_id=action_id,
            resolved_key=resolved_key,
            identity=identity,
            payload=payload,
            command_type=CommandType.MCP_SERVER_ACTION,
            idempotency_store=resolved_service.mcp_server_bff_idempotency,
        )

    @router.get("/bff/mcp/servers/{server_id}/tools")
    async def bff_list_mcp_server_tools(
        server_id: str,
        authorization: Optional[str] = Header(default=None),
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_read(identity)
        clean_id = resolved_service.validate_mcp_server_id(server_id)
        snapshot_at = _now()
        tools = [
            {
                "tool_id": rec["tool_id"],
                "name": rec.get("name", rec["tool_id"]),
                "tool_class": rec.get("tool_class", ""),
                "status": rec.get("status", "imported"),
                "server_id": rec.get("server_id", clean_id),
                "action_count": rec.get("action_count", 0),
                "schema_url": rec.get("schema_url"),
            }
            for rec in resolved_service.merged_mcp_tool_records()
            if rec.get("server_id") == clean_id
        ]
        total = len(tools)
        page_items, next_page_token = _page(tools, page_token, page_size)
        return {
            "data": page_items,
            "page_info": {"next_page_token": next_page_token, "total": total},
            "meta": _read_meta("mcp_tools", "mcp_server_tool_list", snapshot_at=snapshot_at, total=total),
        }

    @router.post("/bff/mcp/tools/{tool_id}/actions/{action_id}", status_code=202)
    async def bff_mcp_tool_action_compat(
        tool_id: str,
        action_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> JSONResponse:
        return deprecated_bff_path_response(
            route="/bff/mcp/tools/{tool_id}/actions/{action_id}",
            replacement="/bff/mcp-tools/{tool_id}/{action_id}",
        )

    @router.get("/bff/skills")
    async def bff_list_skills(
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_read(identity)
        snapshot_at = _now()
        items = resolved_service.merged_skill_records()
        if status:
            items = [s for s in items if s.get("status") == status]
        total = len(items)
        page_items, next_page_token = _page(items, page_token, page_size)
        return {
            "data": page_items,
            "page_info": {"next_page_token": next_page_token, "total": total},
            "meta": _read_meta("skills", "skill_list", snapshot_at=snapshot_at, total=total),
        }

    @router.post("/bff/skills", status_code=201)
    async def bff_create_skill(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Any:
        identity = _extract(authorization)
        _require_read(identity)
        reject_body_idempotency_key(payload, bff_error_fn=_err)
        resolved_key = resolve_final_idempotency_key(
            idempotency_key, x_idempotency_key, bff_error_fn=_err
        )
        request_hash = stable_json_hash({"route": "POST /bff/skills", "payload": payload})
        dry_run = bool(payload.get("dry_run", False) or payload.get("dryRun", False))
        if not dry_run:
            cached = resolved_service.skills_bff_idempotency_check(resolved_key, request_hash)
            if cached is not None:
                return cached
        name = str(payload.get("name") or "").strip()
        if not name:
            raise _err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "name is required",
                "Skill name must be a non-empty string",
                precondition_failed="name",
            )
        snapshot_at = _now()
        skill_id = f"skill-{snapshot_at[:10].replace("-", "")}-{uuid.uuid4().hex[:8]}"
        op_id = getattr(identity, "operator_id", None) or getattr(identity, "id", "operator-1")
        result = {
            "id": skill_id,
            "skill_id": skill_id,
            "name": name,
            "status": "draft",
            "description": payload.get("description") or "",
            "sandbox_enabled": bool(payload.get("sandbox_enabled", True)),
            "input_schema": payload.get("input_schema") or {},
            "output_schema": payload.get("output_schema") or {},
            "created_at": snapshot_at,
            "updated_at": snapshot_at,
            "created_by": op_id,
        }
        if dry_run:
            return dry_run_success_response(
                result,
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="skill.create",
            )
        resolved_service.skill_registry[skill_id] = result
        resolved_service.skills_bff_idempotency[resolved_key] = {
            "request_hash": request_hash,
            "result": result,
        }
        return result

    @router.get("/bff/skills/{skill_id}")
    async def bff_get_skill(
        skill_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_read(identity)
        clean_id = str(skill_id or "").strip()
        if not clean_id:
            raise _err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "skill_id is required",
                "skill_id path parameter must be a non-empty string",
                precondition_failed="skill_id",
            )
        record = resolved_service.find_record_by_id(
            resolved_service.merged_skill_records(), clean_id, ("skill_id", "id")
        )
        if record is None:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Skill not found",
                f"skill_id={clean_id!r} is not registered",
                precondition_failed="skill_id",
            )
        return record

    @router.patch("/bff/skills/{skill_id}")
    async def bff_patch_skill(
        skill_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_operator(identity)
        clean_id = str(skill_id or "").strip()
        if not clean_id:
            raise _err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "skill_id is required",
                "skill_id path parameter must be a non-empty string",
                precondition_failed="skill_id",
            )
        reject_body_idempotency_key(payload, bff_error_fn=_err)
        resolved_key = resolve_final_idempotency_key(
            idempotency_key, x_idempotency_key, bff_error_fn=_err
        )
        request_hash = stable_json_hash(
            {"route": "PATCH /bff/skills/{skill_id}", "id": clean_id, "payload": payload}
        )
        cached = resolved_service.skills_bff_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached
        record = resolved_service.skill_registry.get(clean_id)
        if record is None:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Skill not found",
                f"skill_id={clean_id!r} is not registered",
                precondition_failed="skill_id",
            )
        allowed_patches = {
            "name",
            "description",
            "status",
            "sandbox_enabled",
            "input_schema",
            "output_schema",
        }
        for field in allowed_patches:
            if field in payload:
                record[field] = payload[field]
        record["updated_at"] = _now()
        resolved_service.skills_bff_idempotency[resolved_key] = {
            "request_hash": request_hash,
            "result": record,
        }
        return record

    @router.post("/bff/skills/{skill_id}/actions/{action_id}", status_code=202)
    async def bff_skill_action(
        skill_id: str,
        action_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> JSONResponse:
        return deprecated_bff_path_response(
            route="/bff/skills/{skill_id}/actions/{action_id}",
            replacement="/bff/actions/skill/{skill_id}/{action_id}",
        )

    @router.post("/bff/skills/{skill_id}/sandbox-eval", status_code=202)
    async def bff_skill_sandbox_eval(
        skill_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_read(identity)
        reject_body_idempotency_key(payload, bff_error_fn=_err)
        resolved_key = resolve_final_idempotency_key(
            idempotency_key, x_idempotency_key, bff_error_fn=_err
        )
        clean_id = str(skill_id or "").strip()
        if not clean_id:
            raise _err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "skill_id is required",
                "skill_id path parameter must be a non-empty string",
                precondition_failed="skill_id",
            )
        request_hash = stable_json_hash(
            {"route": "POST /bff/skills/{id}/sandbox-eval", "id": clean_id, "payload": payload}
        )
        cached = resolved_service.skills_bff_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached
        snapshot_at = _now()
        job_id = f"sandbox-eval-{clean_id}-{uuid.uuid4().hex[:10]}"
        op_id = getattr(identity, "operator_id", None) or getattr(identity, "id", "operator-1")
        roles = list(getattr(identity, "roles", []) or [])
        audit_record = {
            "operator_id": op_id,
            "roles_at_submission": roles,
            "action_id": "sandbox-eval",
            "skill_id": clean_id,
            "timestamp": snapshot_at,
            "idempotency_key": resolved_key,
        }
        result = {
            "job_id": job_id,
            "command": "SkillSandboxEval",
            "skill_id": clean_id,
            "status": "SUBMITTED",
            "submitted_at": snapshot_at,
            "audit": audit_record,
            "result": {
                "evaluation_id": job_id,
                "skill_id": clean_id,
                "mode": "sandbox_isolated",
                "status": "queued",
                "input": payload.get("input") or {},
            },
        }
        resolved_service.skills_bff_idempotency[resolved_key] = {
            "request_hash": request_hash,
            "result": result,
        }
        return result

    # ========================================================================
    # 4. Facades & Channels (6 handlers, 6 decorators)
    # ========================================================================

    @router.get("/bff/mcp-servers")
    async def bff_list_mcp_servers_facade(
        status: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_read(identity)
        records = resolved_service.merged_mcp_server_records()
        if status:
            requested = {s.strip().lower() for s in status.split(",") if s.strip()}
            records = [r for r in records if str(r.get("status") or "").lower() in requested]
        return resolved_service.sem_final_list_response(
            records,
            dataset="mcp_servers",
            surface_key="mcp_servers",
            source="bff_local_registry",
        )

    @router.get("/bff/mcp-servers/{server_id}")
    async def bff_get_mcp_server_facade(
        server_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_read(identity)
        clean_id = str(server_id or "").strip()
        record = resolved_service.find_record_by_id(
            resolved_service.merged_mcp_server_records(), clean_id, ("server_id", "id")
        )
        return resolved_service.sem_final_registry_detail(
            record,
            entity_id=clean_id,
            label="MCP server",
            surface_key="mcp_server_detail",
        )

    @router.get("/bff/mcp-tools")
    async def bff_list_mcp_tools_facade(
        status: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_read(identity)
        records = resolved_service.sem_final_mcp_tool_records()
        if status:
            requested = {s.strip().lower() for s in status.split(",") if s.strip()}
            records = [r for r in records if str(r.get("status") or "").lower() in requested]
        return resolved_service.sem_final_list_response(
            records,
            dataset="mcp_tools",
            surface_key="mcp_tools",
            source="bff_local_registry",
        )

    @router.get("/bff/mcp-tools/{tool_id}")
    async def bff_get_mcp_tool_facade(
        tool_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_read(identity)
        clean_id = str(tool_id or "").strip()
        record = resolved_service.sem_final_mcp_tool_record(clean_id)
        return resolved_service.sem_final_registry_detail(
            record,
            entity_id=clean_id,
            label="MCP tool",
            surface_key="mcp_tool_detail",
        )

    @router.get("/bff/channels")
    async def bff_list_channels(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_read(identity)
        records = resolved_service.sem_final_channel_records()
        return resolved_service.sem_final_list_response(
            records,
            dataset="channels",
            surface_key="channels",
            source="bff_local_registry",
        )

    @router.get("/bff/channels/{channel_id}")
    async def bff_get_channel(
        channel_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        _require_read(identity)
        clean_id = str(channel_id or "").strip()
        record = resolved_service.sem_final_channel_record(clean_id)
        return resolved_service.sem_final_registry_detail(
            record,
            entity_id=clean_id,
            label="Channel",
            surface_key="channel_detail",
        )

    return router


create_tools_integrations_router = create_integrations_router
router = create_integrations_router()
