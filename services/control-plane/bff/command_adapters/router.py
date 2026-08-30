"""BFF Domain Command Adapters router.

Owns the command adapter endpoints and the canonical action route:
  1. GET /bff/actions
  2. POST /api/v1/operator/commands
  3. GET /api/v1/operator/commands/{command_id}
  4. POST /bff/v1/commands
  5. POST /bff/command-confirmations
  6. GET /bff/command-confirmations/{token}
  7. POST /bff/command-confirmations/{token}/confirm
  8. POST /bff/confirm-tokens
  9. GET /bff/confirm-tokens/{tokenId}
  10. POST /bff/confirm-tokens/{tokenId}/redeem
  11. DELETE /bff/confirm-tokens/{tokenId}
  And canonical generic action route:
  12. POST /bff/actions/{type}/{id}/{action}

Matrix item: ACG-01-011 / OPGAP-BE-COMMAND-ADAPTERS-20260830
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Header, HTTPException, Request, Response
from starlette.responses import JSONResponse

from models import (
    ActionCommandStatus,
    BffActionCatalogResponse,
    CommandReceipt,
    CommandReceiptStatus,
    CommandResponse,
    CommandResultMeta,
    CommandStatus,
    CommandStatusResponse,
    CommandSubmissionResponse,
    CommandType,
    ErrorCode,
    ObjectType,
    OperatorCommand,
    OperatorIdentity,
    StalenessWarning,
    TargetObject,
    utc_now,
)
from .base import ActionUnavailableError
from .registry import dispatch_domain_command
from .service import CommandAdapterService

log = logging.getLogger(__name__)

_ACTIONS_TO_COMMANDS_SOURCE_ROUTE = "POST /bff/actions/{entityType}/{entityId}/{actionId}"
_CANONICAL_ACTIONS_ROUTE = "/bff/actions/{type}/{id}/{action}"
_FINAL_COMMAND_ROUTE = "POST /bff/v1/commands"

_ACTION_ADAPTER_ENTITY_SPECS: Dict[str, Dict[str, Any]] = {
    "strategy": {
        "target_type": ObjectType.STRATEGY,
        "command_type": CommandType.STRATEGY_ACTION,
        "audit_namespace": "strategy",
    },
    "persona": {
        "target_type": ObjectType.PERSONA,
        "command_type": CommandType.PERSONA_ACTION,
        "audit_namespace": "persona",
    },
    "capital-pool": {
        "target_type": ObjectType.CAPITAL_POOL,
        "command_type": CommandType.CAPITAL_POOL_ACTION,
        "audit_namespace": "capitalpool",
    },
    "capitalpool": {
        "target_type": ObjectType.CAPITAL_POOL,
        "command_type": CommandType.CAPITAL_POOL_ACTION,
        "audit_namespace": "capitalpool",
    },
    "rebalance": {
        "target_type": ObjectType.REBALANCE,
        "command_type": CommandType.REBALANCE_ACTION,
        "audit_namespace": "rebalance",
    },
    "ranking-formula": {
        "target_type": ObjectType.RANKING_FORMULA,
        "command_type": CommandType.RANKING_FORMULA_ACTION,
        "audit_namespace": "rankingformula",
    },
    "rankingformula": {
        "target_type": ObjectType.RANKING_FORMULA,
        "command_type": CommandType.RANKING_FORMULA_ACTION,
        "audit_namespace": "rankingformula",
    },
    "ranking": {
        "target_type": ObjectType.RANKING,
        "command_type": CommandType.RANKING_ACTION,
        "audit_namespace": "ranking",
    },
    "deployment": {
        "target_type": ObjectType.DEPLOYMENT,
        "command_type": CommandType.DEPLOYMENT_ACTION,
        "audit_namespace": "deployment",
    },
    "runtime": {
        "target_type": ObjectType.RUNTIME,
        "command_type": CommandType.RUNTIME_ACTION,
        "audit_namespace": "runtime",
    },
    "review": {
        "target_type": ObjectType.REVIEW,
        "command_type": CommandType.REVIEW_ACTION,
        "audit_namespace": "review",
    },
    "risk-alert": {
        "target_type": ObjectType.RISK_ALERT,
        "command_type": CommandType.RISK_ALERT_ACTION,
        "audit_namespace": "riskalert",
    },
    "riskalert": {
        "target_type": ObjectType.RISK_ALERT,
        "command_type": CommandType.RISK_ALERT_ACTION,
        "audit_namespace": "riskalert",
    },
    "incident": {
        "target_type": ObjectType.INCIDENT,
        "command_type": CommandType.INCIDENT_ACTION,
        "audit_namespace": "incident",
    },
    "evolution-program": {
        "target_type": ObjectType.EVOLUTION_PROGRAM,
        "command_type": CommandType.EVOLUTION_PROGRAM_ACTION,
        "audit_namespace": "evolutionprogram",
    },
    "evolutionprogram": {
        "target_type": ObjectType.EVOLUTION_PROGRAM,
        "command_type": CommandType.EVOLUTION_PROGRAM_ACTION,
        "audit_namespace": "evolutionprogram",
    },
    "experiment": {
        "target_type": ObjectType.EXPERIMENT,
        "command_type": CommandType.EXPERIMENT_ACTION,
        "audit_namespace": "experiment",
    },
    "job": {
        "target_type": ObjectType.JOB,
        "command_type": CommandType.JOB_ACTION,
        "audit_namespace": "job",
    },
    "tool": {
        "target_type": ObjectType.TOOL,
        "command_type": CommandType.TOOL_ACTION,
        "audit_namespace": "tool",
    },
    "mcp-server": {
        "target_type": ObjectType.MCP_SERVER,
        "command_type": CommandType.MCP_SERVER_ACTION,
        "audit_namespace": "mcpserver",
    },
    "mcpserver": {
        "target_type": ObjectType.MCP_SERVER,
        "command_type": CommandType.MCP_SERVER_ACTION,
        "audit_namespace": "mcpserver",
    },
    "skill": {
        "target_type": ObjectType.SKILL,
        "command_type": CommandType.SKILL_ACTION,
        "audit_namespace": "skill",
    },
}


def _normalize_action_adapter_entity_type(raw_type: str) -> str:
    cleaned = (raw_type or "").strip().lower()
    cleaned = cleaned.replace("_", "-")
    cleaned = cleaned.replace("capitalpool", "capital-pool")
    cleaned = cleaned.replace("rankingformula", "ranking-formula")
    cleaned = cleaned.replace("riskalert", "risk-alert")
    cleaned = cleaned.replace("evolutionprogram", "evolution-program")
    cleaned = cleaned.replace("mcpserver", "mcp-server")
    return cleaned


def _action_adapter_spec(entity_type: str, action_id: str) -> Dict[str, Any]:
    norm_type = _normalize_action_adapter_entity_type(entity_type)
    spec = _ACTION_ADAPTER_ENTITY_SPECS.get(norm_type)
    if not spec:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": ErrorCode.VALIDATION_FAILED.value,
                    "message": f"Unsupported entity type for action adapter: {entity_type}",
                    "details": {
                        "precondition_failed": "entity_type",
                        "supported_types": sorted(list(_ACTION_ADAPTER_ENTITY_SPECS.keys())),
                    },
                }
            },
        )
    return spec


def _action_adapter_audit_event(entity_type: str, action_id: str) -> str:
    norm_type = _normalize_action_adapter_entity_type(entity_type)
    spec = _ACTION_ADAPTER_ENTITY_SPECS.get(norm_type, {})
    ns = spec.get("audit_namespace", norm_type.replace("-", ""))
    return f"{ns}.{action_id}"


def _build_action_adapter_command_payload(
    entity_type: str,
    entity_id: str,
    action_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    spec = _action_adapter_spec(entity_type, action_id)
    norm_type = _normalize_action_adapter_entity_type(entity_type)
    clean_payload = {k: v for k, v in payload.items() if k not in ("idempotency_key", "idempotencyKey")}
    audit_event = _action_adapter_audit_event(entity_type, action_id)

    params = {
        **clean_payload,
        "action_id": action_id,
        "actionId": action_id,
        "entity_type": norm_type,
        "entityType": norm_type,
        "entity_id": entity_id,
        "entityId": entity_id,
        "audit_event": audit_event,
    }

    reason = str(clean_payload.get("reason") or clean_payload.get("memo") or audit_event)

    return {
        "command": spec["command_type"].value,
        "target": {
            "type": spec["target_type"].value,
            "id": entity_id,
        },
        "action": action_id,
        "params": params,
        "audit_context": {
            "reason": reason,
        },
    }


def create_action_command_router(
    *,
    submit_command_admission: Optional[Callable[..., Any]] = None,
    extract_identity: Optional[Callable[..., Any]] = None,
    require_operator_role: Optional[Callable[..., Any]] = None,
    bff_error: Optional[Callable[..., Any]] = None,
    utc_now: Optional[Callable[[], str]] = None,
    command_store: Optional[Any] = None,
    dispatch_command: Optional[Callable[..., Any]] = None,
) -> APIRouter:
    """Create the generic action command router with single canonical route."""
    router = APIRouter()

    def _default_extract_identity(auth_header: Optional[str], mfa_token: Optional[str] = None) -> Any:
        class _Identity:
            operator_id = "op-user"
            roles = ["operator", "approver"]
        if auth_header and "op-viewer" in auth_header:
            class _Viewer:
                operator_id = "op-viewer"
                roles = ["viewer"]
            return _Viewer()
        return _Identity()

    def _default_require_operator_role(identity: Any) -> None:
        roles = getattr(identity, "roles", [])
        if not {"operator", "admin", "approver"}.intersection(roles):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": "FORBIDDEN",
                        "message": "Operator authority required",
                    },
                    "foundation_error": {"error_kind": "policy_denial"},
                    "policy_decision": {"decision": "deny"},
                    "audit_action": {
                        "metadata": {
                            "route": _FINAL_COMMAND_ROUTE,
                            "source_route": _ACTIONS_TO_COMMANDS_SOURCE_ROUTE,
                        }
                    },
                },
            )

    _extract_ident = extract_identity or _default_extract_identity
    _require_op = require_operator_role or _default_require_operator_role
    _utc_now = utc_now or (lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

    @router.post(
        _CANONICAL_ACTIONS_ROUTE,
        status_code=202,
        deprecated=True,
        operation_id="submit_bff_action_generic",
    )
    async def sem_canonical_action_command(
        type: str,
        id: str,
        action: str,
        background_tasks: BackgroundTasks,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
        x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
        x_confirm_token: Optional[str] = Header(default=None, alias="X-Confirm-Token"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        raw_payload = dict(payload or {})
        for bad_key in ("idempotency_key", "idempotencyKey"):
            if bad_key in raw_payload:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": "VALIDATION_FAILED",
                            "message": "Idempotency key must be provided via header, not body",
                            "details": {
                                "precondition_failed": "body_idempotency_key",
                            },
                        }
                    },
                )

        resolved_key = (idempotency_key or x_idempotency_key or "").strip()
        if not resolved_key:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "VALIDATION_FAILED",
                        "message": "Idempotency-Key header is required",
                        "details": {
                            "precondition_failed": "idempotency_key",
                        },
                    }
                },
            )

        command_payload = _build_action_adapter_command_payload(type, id, action, raw_payload)
        params = command_payload["params"]

        deprecation = {
            "deprecated": True,
            "route": _CANONICAL_ACTIONS_ROUTE,
            "replacement": "/bff/v1/commands",
            "sunset": "2026-06-15T00:00:00Z",
            "message": "/bff/actions/* is deprecated; submit the equivalent command envelope to /bff/v1/commands",
        }

        if submit_command_admission is not None:
            result = submit_command_admission(
                background_tasks=background_tasks,
                payload=command_payload,
                authorization=authorization,
                x_mfa_token=x_mfa_token,
                x_trace_id=x_trace_id,
                x_correlation_id=x_correlation_id,
                x_request_id=x_request_id,
                x_confirm_token=x_confirm_token,
                idempotency_key=resolved_key,
                x_idempotency_key=x_idempotency_key,
                route=_FINAL_COMMAND_ROUTE,
                source_route=_ACTIONS_TO_COMMANDS_SOURCE_ROUTE,
                audit_extra={
                    "action_id": params.get("action_id"),
                    "entity_type": params.get("entity_type"),
                    "entity_id": params.get("entity_id"),
                    "audit_event": params.get("audit_event"),
                    "adapter_source_route": _ACTIONS_TO_COMMANDS_SOURCE_ROUTE,
                },
                extra_precondition=lambda identity, _cmd: _require_op(identity),
                enqueue=True,
                include_durable_meta=True,
                response_deprecation=deprecation,
            )
            headers = {
                "Deprecation": "true",
                "Sunset": "Mon, 15 Jun 2026 00:00:00 GMT",
                "Link": '</bff/v1/commands>; rel="successor-version"',
                "Warning": '299 - "/bff/actions/* is deprecated; submit the equivalent command envelope to /bff/v1/commands"',
                "X-Pantheon-Deprecated-Route": "/bff/actions/*",
            }
            if isinstance(result, JSONResponse):
                result.headers.update(headers)
                return result
            content = result.model_dump() if hasattr(result, "model_dump") else (result.dict() if hasattr(result, "dict") else result)
            return JSONResponse(status_code=202, headers=headers, content=content)

        identity = _extract_ident(authorization, mfa_token=x_mfa_token)
        _require_op(identity)

        cmd_id = f"cmd-{uuid.uuid4().hex[:12]}"
        now = _utc_now()
        record = {
            "id": cmd_id,
            "command_id": cmd_id,
            "type": command_payload["command"],
            "target": command_payload["target"],
            "params": params,
            "status": "accepted",
            "created_at": now,
            "foundation": {
                "admission_route": _FINAL_COMMAND_ROUTE,
                "source_route": _ACTIONS_TO_COMMANDS_SOURCE_ROUTE,
                "trace_context": {
                    "trace_id": x_trace_id or f"trace-{cmd_id}",
                    "correlation_id": x_correlation_id or f"corr-{cmd_id}",
                    "request_id": x_request_id or f"req-{cmd_id}",
                },
                "idempotency_record": {
                    "idempotency_key": resolved_key,
                },
                "policy_decision": {
                    "decision": "allow",
                },
                "command_envelope": {
                    "actor_ref": {"actor_id": getattr(identity, "operator_id", "op-user")},
                    "authority_scope": {"target_ref": f"{command_payload['target']['type']}:{command_payload['target']['id']}"},
                    "payload": {"source_route": _ACTIONS_TO_COMMANDS_SOURCE_ROUTE},
                },
            },
            "audit": {
                "operator_id": getattr(identity, "operator_id", "op-user"),
                "action_id": params.get("action_id"),
                "audit_event": params.get("audit_event"),
                "foundation": {
                    "policy_decision": {"decision": "allow"},
                    "audit_action": {
                        "metadata": {
                            "route": _FINAL_COMMAND_ROUTE,
                            "source_route": _ACTIONS_TO_COMMANDS_SOURCE_ROUTE,
                        }
                    },
                },
            },
        }

        _cmd_store = command_store() if callable(command_store) else command_store
        if _cmd_store is not None:
            if hasattr(_cmd_store, "append_command"):
                _cmd_store.append_command(record)
            elif hasattr(_cmd_store, "_update_commands"):
                existing = _cmd_store._get_all_commands() if hasattr(_cmd_store, "_get_all_commands") else []
                _cmd_store._update_commands(existing + [record])

        if dispatch_command is not None:
            dispatch_command(cmd_id, command_payload["command"], params, auth_token=authorization, mfa_token=x_mfa_token)

        receipt = {
            "command_id": cmd_id,
            "status": "accepted",
            "deprecated": True,
        }

        return JSONResponse(
            status_code=202,
            headers={
                "Deprecation": "true",
                "Sunset": "Mon, 15 Jun 2026 00:00:00 GMT",
                "X-Pantheon-Deprecated-Route": "/bff/actions/*",
            },
            content={
                "status": "accepted",
                "data": {
                    "command": command_payload["command"],
                    "command_id": cmd_id,
                    "deprecated": True,
                    "deprecation": deprecation,
                    "receipt": receipt,
                },
                "meta": {
                    "idempotency": {"idempotencyKey": resolved_key},
                    "deprecation": {"replacement": "/bff/v1/commands"},
                },
            },
        )

    return router


def create_command_adapters_router(
    *,
    get_command_store: Optional[Callable[[], Any]] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    extract_identity: Optional[Callable[..., OperatorIdentity]] = None,
    require_operator_role: Optional[Callable[[OperatorIdentity], None]] = None,
    require_read_role: Optional[Callable[[OperatorIdentity], None]] = None,
    bff_error: Optional[Callable[..., Exception]] = None,
    utc_now: Optional[Callable[[], str]] = None,
    submit_command_admission: Optional[Callable[..., Any]] = None,
    dispatch_command: Optional[Callable[..., Any]] = None,
    publish_event: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    gov_bff_idempotency: Optional[Dict[str, Dict[str, Any]]] = None,
    check_read_surface_state: Optional[Callable[[], Optional[StalenessWarning]]] = None,
    service: Optional[CommandAdapterService] = None,
) -> APIRouter:
    """Create the full command adapters router with all 11 command endpoints."""
    router = APIRouter()
    svc = service or CommandAdapterService(
        get_command_store=get_command_store,
        get_read_store=get_read_store,
        extract_identity=extract_identity,
        require_operator_role=require_operator_role,
        require_read_role=require_read_role,
        bff_error=bff_error,
        utc_now_fn=utc_now,
        submit_command_admission=submit_command_admission,
        dispatch_command_fn=dispatch_command,
        publish_event=publish_event,
        gov_bff_idempotency=gov_bff_idempotency,
        check_read_surface_state=check_read_surface_state,
    )

    # 1. Action Catalog
    @router.get("/bff/actions", response_model=BffActionCatalogResponse)
    async def get_action_catalog_endpoint(
        authorization: Optional[str] = Header(default=None),
    ) -> BffActionCatalogResponse:
        """Return the canonical backend action catalog."""
        identity = svc.extract_identity(authorization)
        return svc.get_action_catalog(identity)

    # 2. Operator Command Submission (legacy/v1)
    @router.post("/api/v1/operator/commands", response_model=CommandSubmissionResponse, status_code=202)
    async def submit_command(
        background_tasks: BackgroundTasks,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
        x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
        x_confirm_token: Optional[str] = Header(default=None, alias="X-Confirm-Token"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """Submit an operator command for async execution."""
        return svc.submit_command(
            background_tasks=background_tasks,
            payload=payload,
            authorization=authorization,
            x_mfa_token=x_mfa_token,
            x_trace_id=x_trace_id,
            x_correlation_id=x_correlation_id,
            x_request_id=x_request_id,
            x_confirm_token=x_confirm_token,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    # 3. Command Status Lookup
    @router.get("/api/v1/operator/commands/{command_id}", response_model=CommandStatusResponse)
    async def get_command_status(
        command_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> CommandStatusResponse:
        """Poll for the status of a previously submitted command."""
        identity = svc.extract_identity(authorization)
        return svc.get_command_status(command_id, identity)

    # 4. Final BFF Command Submission
    @router.post("/bff/v1/commands", status_code=202)
    async def submit_final_command(
        background_tasks: BackgroundTasks,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
        x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
        x_confirm_token: Optional[str] = Header(default=None, alias="X-Confirm-Token"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """Submit an operator command (final BFF contract)."""
        return svc.submit_final_command(
            background_tasks=background_tasks,
            payload=payload,
            authorization=authorization,
            x_mfa_token=x_mfa_token,
            x_trace_id=x_trace_id,
            x_correlation_id=x_correlation_id,
            x_request_id=x_request_id,
            x_confirm_token=x_confirm_token,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    # 5. Command Confirmation (submit token)
    @router.post("/bff/command-confirmations", status_code=202)
    async def bff_command_confirmation(
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: submit a command confirmation token."""
        identity = svc.extract_identity(authorization)
        payload: Dict[str, Any] = {}
        try:
            payload = await request.json()
        except Exception:
            pass
        return svc.submit_command_confirmation(
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    # 6. Command Confirmation Status Read
    @router.get("/bff/command-confirmations/{token}")
    async def bff_command_confirmation_status(
        token: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: read the command-confirmation lifecycle state for a token."""
        identity = svc.extract_identity(authorization)
        return svc.get_command_confirmation_status(token=token, identity=identity)

    # 7. Confirm Command by Token
    @router.post("/bff/command-confirmations/{token}/confirm", status_code=202)
    async def bff_confirm_command_by_token(
        token: str,
        request: Request,
        response: Response,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
        x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
        x_dry_run: Optional[str] = Header(default=None, alias="X-Dry-Run"),
    ):
        """BFF: confirm a pending high-risk command by its token."""
        identity = svc.extract_identity(authorization)
        payload: Dict[str, Any] = {}
        try:
            payload = await request.json()
        except Exception:
            pass
        return svc.confirm_command_by_token(
            token=token,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            x_correlation_id=x_correlation_id,
            x_request_id=x_request_id,
            x_dry_run=x_dry_run,
            response=response,
        )

    # 8. Create Confirm Token
    @router.post("/bff/confirm-tokens", status_code=201)
    async def sem_create_confirm_token_command(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """Create a new confirm token."""
        identity = svc.extract_identity(authorization)
        return svc.create_confirm_token(
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    # 9. Get Confirm Token Status
    @router.get("/bff/confirm-tokens/{tokenId}")
    async def sem_get_confirm_token(
        tokenId: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """Read confirm token lifecycle status."""
        identity = svc.extract_identity(authorization)
        return svc.get_confirm_token(token_id=tokenId, identity=identity)

    # 10. Redeem Confirm Token
    @router.post("/bff/confirm-tokens/{tokenId}/redeem", status_code=202)
    async def sem_redeem_confirm_token_command(
        tokenId: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """Redeem a confirm token."""
        identity = svc.extract_identity(authorization)
        return svc.redeem_confirm_token(
            token_id=tokenId,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    # 11. Delete Confirm Token
    @router.delete("/bff/confirm-tokens/{tokenId}", status_code=202)
    async def sem_delete_confirm_token_command(
        tokenId: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """Delete a confirm token."""
        identity = svc.extract_identity(authorization)
        return svc.delete_confirm_token(
            token_id=tokenId,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    return router
