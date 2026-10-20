"""Tools & Integrations Domain Service.

This service encapsulates business logic, in-memory state stores, and
validations for:
- OpenClaw operator ops, live-gate, and session management
- MCP tool registration, descriptor imports, and lifecycle action admission
- Tools, MCP servers, and Skills registry records and compatibility surfaces
- Facades for MCP servers/tools and SSE channels

It does NOT import ``main.py`` or the composition root.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from copy import deepcopy
from contextvars import ContextVar
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse

from services.control_plane.bff.command_adapters.contracts import resolve_final_idempotency_key

from services.control_plane.bff.models import (
    ActionCommandStatus,
    BffActionCatalogEntry,
    CommandResponse,
    CommandStatus,
    CommandType,
    ErrorCode,
    McpImportedTool,
    McpRejectedTool,
    McpToolActionData,
    McpToolActionDescriptor,
    McpToolActionRequest,
    McpToolActionVerb,
    McpToolClass,
    McpToolDescriptor,
    McpToolImportData,
    McpToolImportRequest,
    McpToolLifecycleStatus,
    ObjectType,
    OperatorIdentity,
    RiskLevel,
    TargetObject,
    utc_now,
)

from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError

log = logging.getLogger(__name__)

OPENCLAW_COMMAND_ROLES: Set[str] = {"operator", "admin"}
MCP_TOOL_WRITE_ROLES: Set[str] = {"operator", "admin"}

PATH_DEDUPE_DEPRECATED_SINCE = "2026-06-01"
PATH_DEDUPE_SUNSET_HTTP_DATE = "Wed, 01 Jul 2026 00:00:00 GMT"

SSE_CHANNEL_CATALOG: Tuple[str, ...] = (
    "approval",
    "ask",
    "artifact",
    "runtime",
    "mcp",
    "skill",
    "channel",
    "tool",
    "ranking",
    "rebalance",
    "evolution",
    "research",
    "signal",
    "inbox",
    "journal",
    "postmortem",
    "loop",
    "audit",
    "system",
)

SSE_RESYNC_ROUTES: Dict[str, Tuple[str, ...]] = {
    "approval": ("/bff/approvals",),
    "ask": (
        "/bff/management/ai/conversations",
        "/bff/management/ai/conversations/{id}",
    ),
}


def utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_json_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def page_slice(
    items: Sequence[Any], page_token: Optional[str], page_size: int
) -> Tuple[List[Any], Optional[str]]:
    try:
        start = max(0, int(page_token)) if page_token else 0
    except (TypeError, ValueError):
        start = 0
    end = start + page_size
    return list(items[start:end]), str(end) if end < len(items) else None


def default_bff_error(
    status_code: int,
    code: Any,
    message: str,
    reason: Optional[str] = None,
    **details: Any,
) -> HTTPException:
    error_code = code.value if hasattr(code, "value") else str(code)
    return HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": error_code,
                "message": message,
                "reason": reason or message,
                **details,
            }
        },
    )




def reject_body_idempotency_key(
    payload: Dict[str, Any],
    *,
    bff_error_fn: Optional[Callable[..., Exception]] = None,
) -> None:
    err = bff_error_fn or default_bff_error
    body_key = (
        "idempotencyKey"
        if "idempotencyKey" in payload
        else "idempotency_key"
        if "idempotency_key" in payload
        else None
    )
    if body_key is not None:
        raise err(
            400,
            ErrorCode.VALIDATION_FAILED,
            f"{body_key} must not appear in the request body",
            "Final contract routes require idempotency via the Idempotency-Key header, not the request body",
            precondition_failed="body_idempotency_key",
            suggestion=f"Remove {body_key} from the body and set the Idempotency-Key header",
        )


def deprecated_bff_path_response(*, route: str, replacement: str) -> JSONResponse:
    message = f"{route} is deprecated; use {replacement}."
    headers = {
        "Deprecation": "true",
        "Sunset": PATH_DEDUPE_SUNSET_HTTP_DATE,
        "Link": f'<{replacement}>; rel="successor-version"',
        "Warning": f'299 - "{message}"',
        "X-Deprecated": "true",
        "X-Deprecated-At": PATH_DEDUPE_DEPRECATED_SINCE,
        "X-Pantheon-Deprecated-Route": route,
        "X-Pantheon-Replacement-Route": replacement,
    }
    code_val = (
        ErrorCode.OPERATION_NOT_ALLOWED.value
        if hasattr(ErrorCode.OPERATION_NOT_ALLOWED, "value")
        else str(ErrorCode.OPERATION_NOT_ALLOWED)
    )
    return JSONResponse(
        status_code=410,
        headers=headers,
        content={
            "detail": {
                "error": {
                    "code": code_val,
                    "message": "Deprecated BFF route",
                    "details": {
                        "reason": "route_deprecated",
                        "route": route,
                        "replacement": replacement,
                        "deprecated_since": PATH_DEDUPE_DEPRECATED_SINCE,
                    },
                }
            },
            "meta": {
                "deprecated": True,
                "deprecation": {
                    "route": route,
                    "replacement": replacement,
                    "deprecated_since": PATH_DEDUPE_DEPRECATED_SINCE,
                },
            },
        },
    )


REQUEST_DRY_RUN_CONTEXT: ContextVar[bool] = ContextVar("request_dry_run_context", default=False)


def truthy_header(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_dry_run_requested(
    explicit_header: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    resolver: Optional[Callable[[Optional[str]], bool]] = None,
    context_var: Optional[ContextVar[bool]] = None,
) -> bool:
    if resolver is not None:
        try:
            if resolver(explicit_header):
                return True
        except Exception:
            pass
    if truthy_header(explicit_header):
        return True
    ctx = context_var or REQUEST_DRY_RUN_CONTEXT
    try:
        if ctx.get():
            return True
    except Exception:
        pass
    if payload and isinstance(payload, dict):
        if bool(payload.get("dry_run", False) or payload.get("dryRun", False)):
            return True
    return False


def dry_run_success_response(
    data: Dict[str, Any],
    *,
    status_code: int = 200,
    snapshot_at: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    evidence_kind: Optional[str] = None,
    extra_meta: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> JSONResponse:
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at or utc_now_rfc3339(),
        "dryRun": True,
        "durable": False,
        "liveCapitalSideEffects": False,
    }
    if idempotency_key:
        meta["idempotency"] = {
            "key": idempotency_key,
            "idempotencyKey": idempotency_key,
            "replayed": False,
        }
    if evidence_kind:
        meta["evidenceKind"] = evidence_kind
        meta["evidence_kind"] = evidence_kind
    if extra_meta:
        meta.update(extra_meta)
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder({"data": data, "meta": meta}),
        headers=headers,
    )


class IntegrationsService:
    """Tools & Integrations domain service.

    Owns in-memory registries, idempotency records, OpenClaw interactions,
    and command submission routing.
    """

    def __init__(
        self,
        *,
        read_store: Optional[Any] = None,
        openclaw_client: Optional[Any] = None,
        utc_now_fn: Optional[Callable[[], str]] = None,
        bff_error_fn: Optional[Callable[..., Exception]] = None,
        page_slice_fn: Optional[
            Callable[[Sequence[Any], Optional[str], int], Tuple[List[Any], Optional[str]]]
        ] = None,
        submit_command_fn: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._read_store = read_store
        self._openclaw_client = openclaw_client
        self.utc_now = utc_now_fn or utc_now_rfc3339
        self.bff_error = bff_error_fn or default_bff_error
        self.page_slice = page_slice_fn or page_slice
        self._submit_command = submit_command_fn

        # Registries
        self.tool_registry: Dict[str, Dict[str, Any]] = {}
        self.mcp_server_registry: Dict[str, Dict[str, Any]] = {}
        self.mcp_tool_registry: Dict[str, Dict[str, Any]] = {}
        self.skill_registry: Dict[str, Dict[str, Any]] = {}

        # Idempotency caches
        self.tools_bff_idempotency: Dict[str, Dict[str, Any]] = {}
        self.mcp_server_bff_idempotency: Dict[str, Dict[str, Any]] = {}
        self.mcp_import_idempotency: Dict[str, Dict[str, Any]] = {}
        self.mcp_tool_action_idempotency: Dict[str, Dict[str, Any]] = {}
        self.skills_bff_idempotency: Dict[str, Dict[str, Any]] = {}

    @property
    def read_store(self) -> Optional[Any]:
        return self._read_store

    def get_openclaw_client(self) -> Any:
        if self._openclaw_client is not None:
            return self._openclaw_client
        return OpenClawOpsClient()

    # -----------------------------------------------------------------------
    # OpenClaw Operations
    # -----------------------------------------------------------------------

    def authorized_openclaw_operator_filter(
        self,
        identity: Any,
        operator_id: Optional[str],
    ) -> Optional[str]:
        clean = str(operator_id or "").strip() or None
        roles = set(getattr(identity, "roles", []) or [])
        op_id = getattr(identity, "operator_id", None) or getattr(identity, "id", None)
        if clean and clean != op_id and "admin" not in roles:
            raise self.bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "OpenClaw operator filter is not authorized",
                "Non-admin operators may only filter OpenClaw sessions by their own operator id",
                precondition_failed="operator_filter",
                suggestion="Remove the operator_id filter or use an admin-role operator",
            )
        return clean

    def openclaw_effective_operator_role(self, identity: Any) -> str:
        roles = set(getattr(identity, "roles", []) or [])
        if "admin" in roles:
            return "admin"
        if "operator" in roles:
            return "operator"
        if "approver" in roles or "capability_admin" in roles:
            return "approver"
        if "reviewer" in roles:
            return "reviewer"
        return "viewer"

    def require_openclaw_command_role(self, identity: Any) -> None:
        roles = set(getattr(identity, "roles", []) or [])
        if OPENCLAW_COMMAND_ROLES.intersection(roles):
            return
        raise self.bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "OpenClaw operator commands require operator or admin role",
            "Operator does not hold the required OpenClaw command role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator or admin role",
        )

    def require_openclaw_idempotency_key(self, value: Optional[str]) -> str:
        key = str(value or "").strip()
        if key:
            return key
        raise self.bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            "X-Idempotency-Key is required for OpenClaw operator commands",
            "OpenClaw session lifecycle commands must be idempotent at the BFF boundary",
            precondition_failed="idempotency_key",
            suggestion="Retry with a stable X-Idempotency-Key for this operator action",
        )

    def openclaw_ops_meta(
        self, snapshot_at: str, data: Dict[str, Any], surface_key: str
    ) -> Dict[str, Any]:
        service_surfaces = {
            service: {
                key: value
                for key, value in status.items()
                if key in {"status", "source", "reason", "message", "http_status", "surface"}
            }
            for service, status in data.get("service_status", {}).items()
            if isinstance(status, dict)
        }
        overall = str(data.get("overall_status") or "degraded")
        alias_key = (
            "openclaw_tool_workflow_bridge"
            if surface_key == "openclaw_ops"
            else "openclaw_ops"
        )
        return {
            "snapshot_at": snapshot_at,
            "surfaces": {
                surface_key: {"status": overall, "source": "service_client"},
                alias_key: {"status": overall, "source": "service_client"},
                **service_surfaces,
            },
        }

    def build_openclaw_ops_response(
        self,
        *,
        session_limit: int,
        audit_limit: int,
        state: Optional[str],
        operator_id: Optional[str],
        agent_id: Optional[str],
        effective_tools_session_id: Optional[str],
        requesting_operator_id: str,
        effective_tools_mode: Optional[str],
        requesting_operator_role: Optional[str],
        surface_key: str,
    ) -> Dict[str, Any]:
        snapshot_at = self.utc_now()
        if self._read_store and hasattr(self._read_store, "get_openclaw_ops_snapshot"):
            data = self._read_store.get_openclaw_ops_snapshot(
                session_limit=session_limit,
                audit_limit=audit_limit,
                operator_id=operator_id,
                state=state,
                agent_id=agent_id,
                effective_tools_session_id=effective_tools_session_id,
                requesting_operator_id=requesting_operator_id,
                effective_tools_mode=effective_tools_mode,
                requesting_operator_role=requesting_operator_role,
            )
        else:
            client = self.get_openclaw_client()
            sessions = (
                client.list_lifecycle_sessions(
                    operator_id=operator_id,
                    state=state,
                    agent_id=agent_id,
                    effective_tools_session_id=effective_tools_session_id,
                    effective_tools_mode=effective_tools_mode,
                    limit=session_limit,
                )
                if hasattr(client, "list_lifecycle_sessions")
                else []
            )
            audit = (
                client.list_invocation_audit(
                    operator_id=operator_id,
                    agent_id=agent_id,
                    limit=audit_limit,
                )
                if hasattr(client, "list_invocation_audit")
                else []
            )
            data = {
                "service_status": {
                    "openclaw_adapter": {
                        "status": "ok",
                        "source": "service_client",
                        "reason": "available",
                    }
                },
                "overall_status": "ok",
                "sessions": sessions,
                "audit": audit,
            }
        return {
            "data": data,
            "meta": self.openclaw_ops_meta(snapshot_at, data, surface_key),
        }

    def openclaw_client_error(self, exc: OpenClawOpsClientError) -> HTTPException:
        status_code = getattr(exc, "status_code", None) or 502
        if status_code == 404:
            code = ErrorCode.RESOURCE_NOT_FOUND
        elif status_code == 409:
            code = ErrorCode.IDEMPOTENCY_CONFLICT
        elif status_code == 403:
            code = ErrorCode.PRECONDITION_FAILED
        elif status_code >= 500:
            code = ErrorCode.DEPENDENCY_UNAVAILABLE
        else:
            code = ErrorCode.VALIDATION_FAILED
        return self.bff_error(
            status_code,
            code,
            getattr(exc, "message", str(exc)),
            getattr(exc, "error_code", "OPENCLAW_ERROR"),
            precondition_failed="openclaw_adapter",
            suggestion="Inspect GET /api/v1/operator/openclaw/ops for current adapter degradation state",
        )

    def openclaw_command_payload(
        self,
        *,
        command: str,
        adapter_payload: Dict[str, Any],
        accepted_at: str,
    ) -> Dict[str, Any]:
        return {
            "data": {
                "command": command,
                "status": "accepted",
                "accepted_at": accepted_at,
                "adapter_status": adapter_payload.get("status"),
                "replayed": bool(adapter_payload.get("replayed")),
                "session": adapter_payload.get("session"),
            },
            "meta": {
                "snapshot_at": accepted_at,
                "surfaces": {
                    "openclaw_command": {"status": "ok", "source": "service_client"},
                },
            },
        }

    def get_openclaw_broker_adapter_readiness(self) -> Dict[str, Any]:
        if self._read_store and hasattr(self._read_store, "get_openclaw_broker_adapter_readiness"):
            return self._read_store.get_openclaw_broker_adapter_readiness()
        return {
            "capabilities": {
                "sandbox": {"status": "available", "gate_reason": "sandbox_mode_enabled"},
                "paper": {"status": "available", "gate_reason": "paper_trading_permitted"},
                "canary": {"status": "gated", "gate_reason": "canary_gate_not_evaluated"},
                "live": {"status": "disabled", "gate_reason": "fail_closed_live_gate"},
            },
            "overall_status": "ok",
        }

    # -----------------------------------------------------------------------
    # MCP Server Tool Import & Action Admission
    # -----------------------------------------------------------------------

    def require_mcp_tool_write_role(self, identity: Any) -> None:
        roles = set(getattr(identity, "roles", []) or [])
        if MCP_TOOL_WRITE_ROLES.intersection(roles):
            return
        raise self.bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "MCP tool import requires operator-level role",
            "Operator does not hold a role allowed to import or administer MCP tools",
            precondition_failed="role_check",
            suggestion="Escalate to an operator or admin",
        )

    def validate_mcp_server_id(self, server_id: str) -> str:
        clean = str(server_id or "").strip()
        if clean:
            return clean
        raise self.bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "MCP server id is required",
            "server_id path parameter must be a non-empty string",
            precondition_failed="server_id",
        )

    def parse_mcp_import_payload(self, payload: Dict[str, Any]) -> McpToolImportRequest:
        try:
            request = McpToolImportRequest.model_validate(payload)
        except Exception as exc:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "MCP tool import payload is invalid",
                str(exc),
                precondition_failed="payload_shape",
                suggestion="Submit server metadata and a non-empty tools array of MCP tool descriptors",
            ) from exc
        if not request.tools:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "MCP tool import requires at least one tool descriptor",
                "tools must contain one or more MCP tool descriptors",
                precondition_failed="tools",
            )
        return request

    def parse_mcp_tool_action_payload(self, payload: Dict[str, Any]) -> McpToolActionRequest:
        try:
            request = McpToolActionRequest.model_validate(payload)
        except Exception as exc:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "MCP tool action payload is invalid",
                str(exc),
                precondition_failed="payload_shape",
                suggestion="Submit a reason and optional scope for the tool action",
            ) from exc
        if not str(request.reason or "").strip():
            raise self.bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                "MCP tool action reason is required",
                "reason must be a non-empty string for audit",
                precondition_failed="reason",
            )
        return request

    def approved_mcp_governance_flags(self, governance: Dict[str, Any]) -> Set[str]:
        flags: Set[str] = set()
        for field in ("approvedFlags", "approved_flags", "flags"):
            raw = governance.get(field)
            if isinstance(raw, list):
                flags.update(str(value).strip() for value in raw if str(value).strip())
        if governance.get("allowStandaloneCreate") is True or governance.get("allow_standalone_create") is True:
            flags.add("allow_standalone_create")
        return flags

    def mcp_tool_standalone_create_authorized(
        self, tool: McpToolDescriptor, approved_flags: Set[str]
    ) -> bool:
        authorized = False
        for action in tool.actions:
            if not action.allow_standalone_create:
                continue
            if action.governance_flag and action.governance_flag in approved_flags:
                authorized = True
                continue
            if "allow_standalone_create" in approved_flags:
                authorized = True
                continue
            return False
        return authorized

    def mcp_import_replay_response(
        self, record: Dict[str, Any], request_hash: str, *, conflict_message: str
    ) -> CommandResponse[McpToolImportData]:
        if record.get("request_hash") != request_hash:
            raise self.bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                conflict_message,
                "The same Idempotency-Key is already bound to a different MCP import payload",
                precondition_failed="idempotency_conflict",
                suggestion="Reuse the original payload for this key or submit with a new Idempotency-Key",
            )
        response: CommandResponse[McpToolImportData] = record["response"]
        return CommandResponse[McpToolImportData](
            status=response.status,
            data=response.data.model_copy(update={"replayed": True}),
            meta={**(response.meta or {}), "replayed": True},
        )

    def mcp_action_replay_response(
        self, record: Dict[str, Any], request_hash: str, *, conflict_message: str
    ) -> CommandResponse[McpToolActionData]:
        if record.get("request_hash") != request_hash:
            raise self.bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                conflict_message,
                "The same Idempotency-Key is already bound to a different MCP tool action payload",
                precondition_failed="idempotency_conflict",
                suggestion="Reuse the original payload for this key or submit with a new Idempotency-Key",
            )
        response: CommandResponse[McpToolActionData] = record["response"]
        return CommandResponse[McpToolActionData](
            status=response.status,
            data=response.data.model_copy(update={"replayed": True}),
            meta={**(response.meta or {}), "replayed": True},
        )

    def mcp_tool_action_status(self, action: McpToolActionVerb) -> McpToolLifecycleStatus:
        if action == McpToolActionVerb.GRANT:
            return McpToolLifecycleStatus.GRANTED
        if action == McpToolActionVerb.REVOKE:
            return McpToolLifecycleStatus.REVOKED
        if action == McpToolActionVerb.DISABLE:
            return McpToolLifecycleStatus.DISABLED
        if action == McpToolActionVerb.TEST:
            return McpToolLifecycleStatus.TESTED
        raise self.bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Unsupported MCP tool action",
            f"action={action!r} is not a supported MCP tool lifecycle action",
            precondition_failed="action",
        )

    def require_mcp_action_admitted(
        self,
        *,
        tool_record: Dict[str, Any],
        action: McpToolActionVerb,
        request: McpToolActionRequest,
    ) -> None:
        tool_class = str(tool_record.get("tool_class") or "")
        execution_context = str(
            request.scope.get("executionContext")
            or request.scope.get("execution_context")
            or ""
        ).strip().lower()
        if (
            tool_class == "lean_direct"
            and action == McpToolActionVerb.GRANT
            and execution_context == "live"
        ):
            raise self.bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "lean_direct MCP tools cannot be granted for live execution",
                "OpenClaw tool permission contract denies direct LEAN tool access in live context",
                precondition_failed="lean_direct_live",
                suggestion="Use governed signal/artifact flow or restrict the grant scope to paper/backtest",
            )

    def mcp_tool_registry_key(self, server_id: str, tool_id: str) -> str:
        return f"{server_id}:{tool_id}"

    def resolve_mcp_server_id_for_tool(
        self, clean_tool_id: str, request: McpToolActionRequest
    ) -> str:
        explicit = str(
            request.scope.get("serverId") or request.scope.get("server_id") or ""
        ).strip()
        if explicit:
            return self.validate_mcp_server_id(explicit)
        matches = sorted(
            {
                str(record.get("server_id") or "")
                for record in self.mcp_tool_registry.values()
                if str(record.get("tool_id") or "") == clean_tool_id
            }
        )
        matches = [server_id for server_id in matches if server_id]
        if not matches:
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "MCP tool is not imported",
                f"tool_id={clean_tool_id} has not been imported under any MCP server",
                precondition_failed="tool_import",
                suggestion="Import the server tool descriptors before admitting tool actions",
            )
        if len(matches) > 1:
            raise self.bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "MCP tool id is ambiguous across servers",
                f"tool_id={clean_tool_id} is imported under multiple MCP servers",
                precondition_failed="server_id",
                suggestion="Retry with scope.serverId or use the server-scoped v1 MCP tool action route",
            )
        return matches[0]

    def import_mcp_server_tools(
        self,
        *,
        server_id: str,
        payload: Dict[str, Any],
        identity: Any,
        idempotency_key: Optional[str],
        x_idempotency_key: Optional[str],
        x_request_id: Optional[str],
    ) -> CommandResponse[McpToolImportData]:
        clean_server_id = self.validate_mcp_server_id(server_id)
        self.require_mcp_tool_write_role(identity)
        resolved_key = resolve_final_idempotency_key(
            idempotency_key, x_idempotency_key, bff_error_fn=self.bff_error
        )
        reject_body_idempotency_key(payload, bff_error_fn=self.bff_error)
        request = self.parse_mcp_import_payload(payload)

        request_hash = stable_json_hash(
            {
                "route": "POST /bff/v1/mcp/servers/{server_id}/import-tools",
                "server_id": clean_server_id,
                "payload": request.model_dump(mode="json", by_alias=True),
            }
        )
        existing = self.mcp_import_idempotency.get(resolved_key)
        if existing is not None:
            return self.mcp_import_replay_response(
                existing,
                request_hash,
                conflict_message="Idempotency key was already used with a different MCP tool import",
            )

        approved_flags = self.approved_mcp_governance_flags(request.governance)
        imported: List[McpImportedTool] = []
        rejected: List[McpRejectedTool] = []
        seen_tool_ids: Set[str] = set()
        operator_id = getattr(identity, "operator_id", None) or getattr(identity, "id", "operator-1")

        for tool in request.tools:
            tool_id = str(tool.tool_id or "").strip()
            if not tool_id:
                rejected.append(
                    McpRejectedTool(
                        toolId=None,
                        reason="toolId is required",
                        preconditionFailed="tool_id",
                    )
                )
                continue
            if tool_id in seen_tool_ids:
                rejected.append(
                    McpRejectedTool(
                        toolId=tool_id,
                        reason="Duplicate toolId in import payload",
                        preconditionFailed="duplicate_tool_id",
                    )
                )
                continue
            seen_tool_ids.add(tool_id)
            if not str(tool.name or "").strip():
                rejected.append(
                    McpRejectedTool(
                        toolId=tool_id,
                        reason="Tool name is required",
                        preconditionFailed="tool_name",
                    )
                )
                continue
            standalone_create_enabled = self.mcp_tool_standalone_create_authorized(
                tool, approved_flags
            )
            if (
                any(action.allow_standalone_create for action in tool.actions)
                and not standalone_create_enabled
            ):
                rejected.append(
                    McpRejectedTool(
                        toolId=tool_id,
                        reason="Standalone tool create must be explicitly authorized by governance flags",
                        preconditionFailed="standalone_tool_create",
                    )
                )
                continue

            registry_key = self.mcp_tool_registry_key(clean_server_id, tool_id)
            self.mcp_tool_registry[registry_key] = {
                "server_id": clean_server_id,
                "tool_id": tool_id,
                "id": tool_id,
                "name": tool.name,
                "tool_class": tool.tool_class.value if hasattr(tool.tool_class, "value") else str(tool.tool_class),
                "descriptor": tool.model_dump(mode="json", by_alias=True),
                "schema_url": tool.schema_url or request.schema_url,
                "status": McpToolLifecycleStatus.IMPORTED.value,
                "action_count": len(tool.actions),
                "standalone_create_enabled": standalone_create_enabled,
                "imported_by": operator_id,
                "imported_at": self.utc_now(),
            }
            imported.append(
                McpImportedTool(
                    toolId=tool_id,
                    serverId=clean_server_id,
                    name=tool.name,
                    toolClass=tool.tool_class,
                    status=McpToolLifecycleStatus.IMPORTED,
                    schemaUrl=tool.schema_url or request.schema_url,
                    actionCount=len(tool.actions),
                    standaloneCreateEnabled=standalone_create_enabled,
                )
            )

        data = McpToolImportData(
            importId=f"mcp-import-{uuid.uuid4().hex[:12]}",
            serverId=clean_server_id,
            importedTools=imported,
            rejectedTools=rejected,
            replayed=False,
        )
        response = CommandResponse[McpToolImportData](
            status=ActionCommandStatus.COMPLETED,
            data=data,
            meta={
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "requestId": str(x_request_id or "").strip() or None,
                "canonicalWriteAuthority": "mcp_server_import",
                "standaloneToolCreateRoute": None,
            },
        )
        self.mcp_import_idempotency[resolved_key] = {
            "request_hash": request_hash,
            "response": response,
        }
        return response

    def admit_mcp_tool_action(
        self,
        *,
        server_id: str,
        tool_id: str,
        action: McpToolActionVerb,
        payload: Dict[str, Any],
        identity: Any,
        idempotency_key: Optional[str],
        x_idempotency_key: Optional[str],
        x_request_id: Optional[str],
    ) -> CommandResponse[McpToolActionData]:
        clean_server_id = self.validate_mcp_server_id(server_id)
        clean_tool_id = str(tool_id or "").strip()
        if not clean_tool_id:
            raise self.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "MCP tool id is required",
                "tool_id path parameter must be a non-empty string",
                precondition_failed="tool_id",
            )
        self.require_mcp_tool_write_role(identity)
        resolved_key = resolve_final_idempotency_key(
            idempotency_key, x_idempotency_key, bff_error_fn=self.bff_error
        )
        reject_body_idempotency_key(payload, bff_error_fn=self.bff_error)
        request = self.parse_mcp_tool_action_payload(payload)

        request_hash = stable_json_hash(
            {
                "route": "POST /bff/v1/mcp/servers/{server_id}/tools/{tool_id}/actions/{action}",
                "server_id": clean_server_id,
                "tool_id": clean_tool_id,
                "action": action.value if hasattr(action, "value") else str(action),
                "payload": request.model_dump(mode="json", by_alias=True),
            }
        )
        existing = self.mcp_tool_action_idempotency.get(resolved_key)
        if existing is not None:
            return self.mcp_action_replay_response(
                existing,
                request_hash,
                conflict_message="Idempotency key was already used with a different MCP tool action",
            )

        registry_key = self.mcp_tool_registry_key(clean_server_id, clean_tool_id)
        tool_record = self.mcp_tool_registry.get(registry_key)
        if tool_record is None:
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "MCP tool is not imported for this server",
                f"tool_id={clean_tool_id} has not been imported under server_id={clean_server_id}",
                precondition_failed="tool_import",
                suggestion="Import the server tool descriptors before admitting tool actions",
            )
        self.require_mcp_action_admitted(
            tool_record=tool_record,
            action=action,
            request=request,
        )

        next_status = self.mcp_tool_action_status(action)
        operator_id = getattr(identity, "operator_id", None) or getattr(identity, "id", "operator-1")
        if not request.dry_run:
            tool_record["status"] = (
                next_status.value if hasattr(next_status, "value") else str(next_status)
            )
            tool_record["updated_at"] = self.utc_now()
            tool_record["updated_by"] = operator_id

        data = McpToolActionData(
            toolId=clean_tool_id,
            serverId=clean_server_id,
            action=action,
            status=next_status if not request.dry_run else McpToolLifecycleStatus(tool_record["status"]),
            admitted=True,
            replayed=False,
        )
        response = CommandResponse[McpToolActionData](
            status=ActionCommandStatus.COMPLETED,
            data=data,
            meta={
                "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
                "requestId": str(x_request_id or "").strip() or None,
                "dryRun": request.dry_run,
                "canonicalWriteAuthority": "mcp_tool_action_admission",
            },
        )
        self.mcp_tool_action_idempotency[resolved_key] = {
            "request_hash": request_hash,
            "response": response,
        }
        return response

    # -----------------------------------------------------------------------
    # Registries Merging & Compatibility CRUD
    # -----------------------------------------------------------------------

    def merge_registry_records(
        self,
        fixture_records: List[Dict[str, Any]],
        registry_records: List[Dict[str, Any]],
        id_keys: Tuple[str, ...],
    ) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        for record in fixture_records + registry_records:
            record_id = ""
            for key in id_keys:
                value = record.get(key)
                if value not in (None, ""):
                    record_id = str(value)
                    break
            if record_id:
                merged[record_id] = dict(record)
        return list(merged.values())

    def read_store_fixture_records(self, dataset: str) -> List[Dict[str, Any]]:
        if not self._read_store:
            return []
        data = getattr(self._read_store, "_data", {})
        raw = data.get(dataset) if isinstance(data, dict) else None
        if isinstance(raw, dict):
            return [dict(record) for record in raw.values() if isinstance(record, dict)]
        if isinstance(raw, list):
            return [dict(record) for record in raw if isinstance(record, dict)]
        return []

    def tool_fixture_records(self) -> List[Dict[str, Any]]:
        if self._read_store and hasattr(self._read_store, "list_tools"):
            records = self._read_store.list_tools()
            if records:
                return records
        return self.read_store_fixture_records("tools")

    def skill_fixture_records(self) -> List[Dict[str, Any]]:
        if self._read_store and hasattr(self._read_store, "list_skills"):
            records = self._read_store.list_skills()
            if records:
                return records
        return self.read_store_fixture_records("skills")

    def mcp_server_fixture_records(self) -> List[Dict[str, Any]]:
        if self._read_store and hasattr(self._read_store, "list_mcp_servers"):
            records = self._read_store.list_mcp_servers()
            if records:
                return records
        return self.read_store_fixture_records("mcp_servers")

    def mcp_tool_fixture_records(self) -> List[Dict[str, Any]]:
        if self._read_store and hasattr(self._read_store, "list_mcp_tools"):
            records = self._read_store.list_mcp_tools()
            if records:
                return records
        return self.read_store_fixture_records("mcp_tools")

    def merged_tool_records(self) -> List[Dict[str, Any]]:
        return self.merge_registry_records(
            self.tool_fixture_records(),
            [],
            ("tool_id", "id"),
        )

    def merged_skill_records(self) -> List[Dict[str, Any]]:
        return self.merge_registry_records(
            self.skill_fixture_records(),
            [],
            ("skill_id", "id"),
        )

    def merged_mcp_server_records(self) -> List[Dict[str, Any]]:
        return self.merge_registry_records(
            self.mcp_server_fixture_records(),
            [dict(record) for record in self.mcp_server_registry.values()],
            ("server_id", "id"),
        )

    def merged_mcp_tool_records(self) -> List[Dict[str, Any]]:
        return self.merge_registry_records(
            self.mcp_tool_fixture_records(),
            [dict(record) for record in self.mcp_tool_registry.values()],
            ("tool_id", "id"),
        )

    def find_record_by_id(
        self, records: List[Dict[str, Any]], entity_id: str, id_keys: Tuple[str, ...]
    ) -> Optional[Dict[str, Any]]:
        clean_id = str(entity_id or "").strip()
        return next(
            (
                dict(record)
                for record in records
                if any(str(record.get(key) or "") == clean_id for key in id_keys)
            ),
            None,
        )

    def identity_operator_id(self, identity: Any) -> str:
        return str(getattr(identity, "operator_id", None) or getattr(identity, "id", None) or "operator-1").strip() or "operator-1"

    def identity_tenant_id(self, identity: Any, *, x_tenant_id: Optional[str] = None) -> str:
        requested = str(x_tenant_id or "").strip()
        if not requested:
            try:
                from services.control_plane.bff.core.owner_reads import selected_tenant
                requested = str(selected_tenant.get() or "").strip()
            except Exception:
                requested = ""
        allowed: List[str] = []
        direct_tid = str(getattr(identity, "tenant_id", None) or "").strip()
        if direct_tid:
            allowed.append(direct_tid)
        claims = getattr(identity, "claims", None)
        if isinstance(claims, dict):
            for key in ("tenant_id", "tenantId", "tid", "org_id"):
                val = str(claims.get(key) or "").strip()
                if val and val not in allowed:
                    allowed.append(val)
            tenant_obj = claims.get("tenant")
            if isinstance(tenant_obj, dict) and str(tenant_obj.get("id") or "").strip():
                allowed.append(str(tenant_obj.get("id")).strip())
            for list_key in ("tenant_ids", "tenantIds"):
                for item in (claims.get(list_key) or ()) if isinstance(claims.get(list_key), (list, tuple, set)) else ():
                    if str(item or "").strip() and str(item).strip() not in allowed:
                        allowed.append(str(item).strip())
        if requested:
            if allowed and requested not in allowed:
                raise self.bff_error(
                    403,
                    ErrorCode.FORBIDDEN,
                    "Tenant scope mismatch",
                    f"Caller is not authorized for tenant {requested!r}",
                    precondition_failed="tenant_scope",
                )
            return requested
        return allowed[0] if allowed else "default"

    def idempotency_scope_key(
        self, resolved_key: str, identity: Optional[Any] = None, *, tenant_id: Optional[str] = None
    ) -> str:
        if identity is None and not tenant_id:
            return resolved_key
        resolved_tenant = tenant_id or self.identity_tenant_id(identity)
        operator_id = self.identity_operator_id(identity) if identity is not None else "operator-1"
        return f"{resolved_tenant}:{operator_id}:{resolved_key}"

    def _check_scoped_idempotency(
        self, store: Dict[str, Dict[str, Any]], scope_key: str, resolved_key: str, request_hash: str
    ) -> Optional[Dict[str, Any]]:
        existing = store.get(scope_key)
        if existing is None:
            return None
        if existing.get("request_hash") != request_hash:
            raise self.bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different payload",
                f"Key {resolved_key!r} is bound to a different request hash",
                precondition_failed="idempotency_conflict",
            )
        return existing.get("result")

    def tools_bff_idempotency_check(
        self,
        resolved_key: str,
        request_hash: str,
        *,
        identity: Optional[Any] = None,
        tenant_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        scope_key = self.idempotency_scope_key(resolved_key, identity, tenant_id=tenant_id)
        cached = self._check_scoped_idempotency(self.tools_bff_idempotency, scope_key, resolved_key, request_hash)
        if cached is None:
            return None
        record_id = str(cached.get("tool_id") or cached.get("id") or "").strip()
        readback = self.find_record_by_id(self.merged_tool_records(), record_id, ("tool_id", "id")) if record_id else None
        if readback is None:
            raise self.bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Tool catalog readback is unavailable",
                "Replayed tool record is not present in the authoritative catalog owner",
                precondition_failed="tool_readback_unconfirmed",
            )
        return readback

    def skills_bff_idempotency_check(
        self,
        resolved_key: str,
        request_hash: str,
        *,
        identity: Optional[Any] = None,
        tenant_id: Optional[str] = None,
        require_readback: bool = False,
    ) -> Optional[Dict[str, Any]]:
        scope_key = self.idempotency_scope_key(resolved_key, identity, tenant_id=tenant_id)
        cached = self._check_scoped_idempotency(self.skills_bff_idempotency, scope_key, resolved_key, request_hash)
        if cached is None or not require_readback:
            return cached
        record_id = str(cached.get("skill_id") or cached.get("id") or "").strip()
        readback = self.find_record_by_id(self.merged_skill_records(), record_id, ("skill_id", "id")) if record_id else None
        if readback is None:
            raise self.bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Skill catalog readback is unavailable",
                "Replayed skill record is not present in the authoritative catalog owner",
                precondition_failed="skill_readback_unconfirmed",
            )
        return readback

    def _resolve_catalog_mutation_owner(self, *, kind: str, operation: str) -> Any:
        for candidate in (self._read_store, self._openclaw_client):
            if candidate is None:
                continue
            supports_fn = getattr(candidate, "supports_catalog_metadata_mutation", None)
            if callable(supports_fn) and not supports_fn(operation):
                continue
            if callable(getattr(candidate, operation, None)):
                return candidate
        label = "Tools" if kind == "tool" else "Skills"
        raise self.bff_error(
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            f"{label} catalog write owner is unavailable",
            f"Configured product owner does not support {operation}; BFF process-local registry acknowledgement is disabled",
            precondition_failed=f"{kind}_catalog_owner_unavailable",
        )

    def mutate_catalog_record(
        self,
        *,
        kind: str,
        operation: str,
        payload: Dict[str, Any],
        identity: Any,
        resolved_key: str,
        entity_id: Optional[str] = None,
        x_tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        is_tool = kind == "tool"
        id_field = "tool_id" if is_tool else "skill_id"
        id_keys = ("tool_id", "id") if is_tool else ("skill_id", "id")
        label = "Tool" if is_tool else "Skill"
        tenant_id = self.identity_tenant_id(identity, x_tenant_id=x_tenant_id)
        op_id = self.identity_operator_id(identity)
        scope_key = self.idempotency_scope_key(resolved_key, identity, tenant_id=tenant_id)
        route_sig = f"POST /bff/{kind}s" if entity_id is None else f"PATCH /bff/{kind}s/{{{id_field}}}"
        hash_body: Dict[str, Any] = {"route": route_sig, "payload": payload}
        if entity_id is not None:
            hash_body["id"] = entity_id
        request_hash = stable_json_hash(hash_body)
        cached = (
            self.tools_bff_idempotency_check(resolved_key, request_hash, identity=identity, tenant_id=tenant_id)
            if is_tool
            else self.skills_bff_idempotency_check(
                resolved_key, request_hash, identity=identity, tenant_id=tenant_id, require_readback=True
            )
        )
        if cached is not None:
            return cached
        owner = self._resolve_catalog_mutation_owner(kind=kind, operation=operation)
        call_kwargs: Dict[str, Any] = {
            "payload": dict(payload),
            "operator_id": op_id,
            "tenant_id": tenant_id,
            "idempotency_key": resolved_key,
            "snapshot_at": self.utc_now(),
        }
        if entity_id is not None:
            call_kwargs[id_field] = entity_id
        try:
            owner_record = getattr(owner, operation)(**call_kwargs)
        except OpenClawOpsClientError as exc:
            raise self.openclaw_client_error(exc) from exc
        if entity_id is not None and owner_record is None:
            raise self.bff_error(
                404, ErrorCode.RESOURCE_NOT_FOUND, f"{label} not found", f"{id_field}={entity_id!r} is not registered", precondition_failed=id_field
            )
        record_id = str(
            entity_id
            or (owner_record.get(id_field) if isinstance(owner_record, dict) else "")
            or (owner_record.get("id") if isinstance(owner_record, dict) else "")
            or ""
        ).strip()
        records = self.merged_tool_records() if is_tool else self.merged_skill_records()
        readback = self.find_record_by_id(records, record_id, id_keys) if record_id else None
        if readback is None or (entity_id is not None and any(readback.get(k) != v for k, v in payload.items())):
            raise self.bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                f"{label} catalog readback confirmation failed",
                "Owner mutation was not confirmed by subsequent catalog readback",
                precondition_failed=f"{kind}_readback_unconfirmed",
            )
        idem_store = self.tools_bff_idempotency if is_tool else self.skills_bff_idempotency
        idem_store[scope_key] = {"request_hash": request_hash, "result": readback}
        return readback

    def mcp_server_bff_idempotency_check(
        self, resolved_key: str, request_hash: str
    ) -> Optional[Dict[str, Any]]:
        existing = self.mcp_server_bff_idempotency.get(resolved_key)
        if existing is None:
            return None
        if existing.get("request_hash") != request_hash:
            raise self.bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different payload",
                f"Key {resolved_key!r} is bound to a different request hash",
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
            )
        return existing.get("result")

    # -----------------------------------------------------------------------
    # Semantic Facade Helpers
    # -----------------------------------------------------------------------

    def sem_final_mcp_tool_records(self) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for record in self.merged_mcp_tool_records():
            tool_id = str(record.get("tool_id") or record.get("id") or "").strip()
            if not tool_id:
                continue
            records.append(
                {
                    "id": tool_id,
                    "tool_id": tool_id,
                    "server_id": record.get("server_id"),
                    "name": record.get("name") or tool_id,
                    "status": record.get("status") or "imported",
                    "tool_class": record.get("tool_class") or "",
                    "schema_url": record.get("schema_url"),
                    "action_count": record.get("action_count", 0),
                }
            )
        return sorted(
            records,
            key=lambda item: (str(item.get("server_id") or ""), str(item.get("tool_id") or "")),
        )

    def sem_final_mcp_tool_record(self, tool_id: str) -> Optional[Dict[str, Any]]:
        clean_id = str(tool_id or "").strip()
        for record in self.sem_final_mcp_tool_records():
            if str(record.get("tool_id") or record.get("id") or "") == clean_id:
                return record
        return None

    def sem_final_channel_records(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": channel,
                "channel_id": channel,
                "name": channel,
                "status": "active",
                "replay_supported": channel in SSE_RESYNC_ROUTES,
                "resync_routes": list(SSE_RESYNC_ROUTES.get(channel, ())),
            }
            for channel in SSE_CHANNEL_CATALOG
        ]

    def sem_final_channel_record(self, channel_id: str) -> Optional[Dict[str, Any]]:
        clean_id = str(channel_id or "").strip()
        for record in self.sem_final_channel_records():
            if record["id"] == clean_id:
                return record
        return None

    def sem_final_registry_meta(
        self,
        surface_key: str,
        *,
        snapshot_at: Optional[str] = None,
        total: Optional[int] = None,
    ) -> Dict[str, Any]:
        snapshot_at = snapshot_at or self.utc_now()
        meta: Dict[str, Any] = {
            "snapshot_at": snapshot_at,
            "surfaces": {surface_key: {"status": "ok", "source": "bff_local_registry"}},
        }
        if total is not None:
            meta["total"] = total
        return meta

    def sem_final_list_response(
        self,
        items: List[Dict[str, Any]],
        *,
        dataset: str,
        surface_key: str,
        source: Optional[str] = None,
    ) -> Dict[str, Any]:
        snapshot_at = self.utc_now()
        meta = self.sem_final_registry_meta(
            surface_key, snapshot_at=snapshot_at, total=len(items)
        )
        return {
            "data": items,
            "items": items,
            "page_info": {"next_page_token": None, "total": len(items)},
            "meta": meta,
        }

    def sem_final_registry_detail(
        self,
        record: Optional[Dict[str, Any]],
        *,
        entity_id: str,
        label: str,
        surface_key: str,
    ) -> Dict[str, Any]:
        if not record:
            raise self.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"{label} not found",
                f"{label} {entity_id} does not exist",
                precondition_failed=label.lower().replace(" ", "_"),
            )
        snapshot_at = self.utc_now()
        return {
            "data": record,
            "meta": self.sem_final_registry_meta(surface_key, snapshot_at=snapshot_at),
        }
