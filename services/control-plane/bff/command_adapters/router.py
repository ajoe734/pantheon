"""BFF Domain Command Adapters router.

Owns the canonical generic action route:
  POST /bff/actions/{type}/{id}/{action}

Matrix item: ACG-01-011
  - Preserves schema naming in the shared helper
  - Eliminates the duplicate POST /bff/actions/{entityType}/{entityId}/{actionId} route
  - Provides a single runtime handler and OpenAPI operation
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Header, HTTPException, Response
from starlette.responses import JSONResponse

try:
    from models import CommandType, ErrorCode, ObjectType
except ImportError:
    class ErrorCode:
        VALIDATION_FAILED = "VALIDATION_FAILED"
        AUTH_REQUIRED = "AUTH_REQUIRED"
        FORBIDDEN = "FORBIDDEN"
        RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
        INTERNAL_ERROR = "INTERNAL_ERROR"

    class CommandType:
        STRATEGY_ACTION = "StrategyAction"
        PERSONA_ACTION = "PersonaAction"
        CAPITAL_POOL_ACTION = "CapitalPoolAction"
        REBALANCE_ACTION = "RebalanceAction"
        RANKING_FORMULA_ACTION = "RankingFormulaAction"
        RANKING_ACTION = "RankingAction"
        DEPLOYMENT_ACTION = "DeploymentAction"
        RUNTIME_ACTION = "RuntimeAction"
        REVIEW_ACTION = "ReviewAction"
        RISK_ALERT_ACTION = "RiskAlertAction"
        INCIDENT_ACTION = "IncidentAction"
        EVOLUTION_PROGRAM_ACTION = "EvolutionProgramAction"
        EXPERIMENT_ACTION = "ExperimentAction"
        JOB_ACTION = "JobAction"
        TOOL_ACTION = "ToolAction"
        MCP_SERVER_ACTION = "McpServerAction"
        SKILL_ACTION = "SkillAction"
        APPROVED_APPLY = "ApprovedApply"
        EMERGENCY_CONTAINMENT = "EmergencyContainment"

    class ObjectType:
        STRATEGY = "Strategy"
        PERSONA = "Persona"
        CAPITAL_POOL = "CapitalPool"
        REBALANCE = "Rebalance"
        RANKING_FORMULA = "RankingFormula"
        RANKING = "Ranking"
        DEPLOYMENT = "Deployment"
        RUNTIME = "Runtime"
        REVIEW = "Review"
        APPROVAL_DECISION = "ApprovalDecision"
        RISK_ALERT = "RiskAlert"
        INCIDENT = "Incident"
        EVOLUTION_PROGRAM = "EvolutionProgram"
        EXPERIMENT = "Experiment"
        JOB = "Job"
        TOOL = "Tool"
        MCP_SERVER = "McpServer"
        SKILL = "Skill"

log = logging.getLogger(__name__)

_ACTIONS_TO_COMMANDS_SOURCE_ROUTE = "POST /bff/actions/{entityType}/{entityId}/{actionId}"
_CANONICAL_ACTIONS_ROUTE = "POST /bff/actions/{type}/{id}/{action}"
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
    "approval": {
        "target_type": ObjectType.APPROVAL_DECISION,
        "command_type": CommandType.REVIEW_ACTION,
        "audit_namespace": "approval",
    },
    "alert": {
        "target_type": ObjectType.RISK_ALERT,
        "command_type": CommandType.RISK_ALERT_ACTION,
        "audit_namespace": "alert",
    },
    "incident": {
        "target_type": ObjectType.INCIDENT,
        "command_type": CommandType.INCIDENT_ACTION,
        "audit_namespace": "incident",
    },
    "evolution-program": {
        "target_type": ObjectType.EVOLUTION_PROGRAM,
        "command_type": CommandType.EVOLUTION_PROGRAM_ACTION,
        "audit_namespace": "evolution",
    },
    "evolutionprogram": {
        "target_type": ObjectType.EVOLUTION_PROGRAM,
        "command_type": CommandType.EVOLUTION_PROGRAM_ACTION,
        "audit_namespace": "evolution",
    },
    "research-experiment": {
        "target_type": ObjectType.EXPERIMENT,
        "command_type": CommandType.EXPERIMENT_ACTION,
        "audit_namespace": "research",
    },
    "researchexperiment": {
        "target_type": ObjectType.EXPERIMENT,
        "command_type": CommandType.EXPERIMENT_ACTION,
        "audit_namespace": "research",
    },
    "experiment": {
        "target_type": ObjectType.EXPERIMENT,
        "command_type": CommandType.EXPERIMENT_ACTION,
        "audit_namespace": "research",
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
    "mcp-tool": {
        "target_type": ObjectType.TOOL,
        "command_type": CommandType.TOOL_ACTION,
        "audit_namespace": "mcptool",
    },
    "mcptool": {
        "target_type": ObjectType.TOOL,
        "command_type": CommandType.TOOL_ACTION,
        "audit_namespace": "mcptool",
    },
    "skill": {
        "target_type": ObjectType.SKILL,
        "command_type": CommandType.SKILL_ACTION,
        "audit_namespace": "skill",
    },
    "artifact": {
        "target_type": ObjectType.REVIEW,
        "command_type": CommandType.REVIEW_ACTION,
        "audit_namespace": "artifact",
    },
    "channel": {
        "target_type": ObjectType.REVIEW,
        "command_type": CommandType.REVIEW_ACTION,
        "audit_namespace": "channel",
    },
}


def _default_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_bff_error(
    status_code: int,
    code: str,
    message: str,
    reason: Optional[str] = None,
    precondition_failed: Optional[str] = None,
    suggestion: Optional[str] = None,
    details_extra: Optional[Dict[str, Any]] = None,
) -> HTTPException:
    detail: Dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "reason": reason or message,
            "status_code": status_code,
        }
    }
    if precondition_failed:
        detail["error"]["details"] = {"precondition_failed": precondition_failed}
    if suggestion:
        detail["error"]["suggestion"] = suggestion
    if details_extra:
        detail["error"].setdefault("details", {}).update(details_extra)
    return HTTPException(status_code=status_code, detail=detail)


def _default_extract_identity(
    authorization: Optional[str] = None,
    mfa_token: Optional[str] = None,
) -> Any:
    class DummyIdentity:
        operator_id = "op-default"
        roles = {"operator", "admin"}

    ident = DummyIdentity()
    if authorization:
        token = authorization.replace("Bearer ", "").strip()
        parts = token.split(":")
        ident.operator_id = parts[0]
        if len(parts) > 1:
            ident.roles = set(parts[1].split(","))
    return ident


def _default_require_operator_role(identity: Any, err_fn=None) -> None:
    roles = getattr(identity, "roles", set())
    if not ({"operator", "admin", "approver"}.intersection(roles)):
        _err = err_fn or _default_bff_error
        detail_extra = {
            "foundation_error": {"error_kind": "policy_denial"},
            "policy_decision": {"decision": "deny"},
            "audit_action": {
                "metadata": {
                    "route": _FINAL_COMMAND_ROUTE,
                    "source_route": _ACTIONS_TO_COMMANDS_SOURCE_ROUTE,
                }
            },
        }
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "Operator role required",
            "Operator role required to submit action commands",
            precondition_failed="role_check",
            details_extra=detail_extra,
        )


def normalize_action_adapter_entity_type(entity_type: str) -> str:
    return str(entity_type or "").strip().lower().replace("_", "-")


def action_adapter_spec(entity_type: str, err_fn: Optional[Callable[..., HTTPException]] = None) -> Dict[str, Any]:
    normalized = normalize_action_adapter_entity_type(entity_type)
    spec = _ACTION_ADAPTER_ENTITY_SPECS.get(normalized)
    if spec is None:
        _err = err_fn or _default_bff_error
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Unsupported action entity type",
            f"/bff/actions does not admit entityType={entity_type!r}",
            precondition_failed="entity_type",
            suggestion="Submit a documented BFF action entity type from BFF_COMMAND_API_CONTRACT.md section 8",
        )
    return spec


def action_adapter_audit_event(spec: Dict[str, Any], action_id: str) -> str:
    namespace = str(spec.get("audit_namespace") or "action").strip()
    action = str(action_id or "").strip()
    return f"{namespace}.{action}" if action else namespace


def apply_legacy_action_deprecation_headers(response: Response) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "Mon, 15 Jun 2026 00:00:00 GMT"
    response.headers["X-Pantheon-Deprecated-Route"] = "/bff/actions/*"


def build_action_adapter_command_payload(
    *,
    entity_type: str,
    entity_id: str,
    action_id: str,
    payload: Dict[str, Any],
    err_fn: Optional[Callable[..., HTTPException]] = None,
) -> Dict[str, Any]:
    normalized_entity_type = normalize_action_adapter_entity_type(entity_type)
    clean_entity_id = str(entity_id or "").strip()
    clean_action_id = str(action_id or "").strip()
    _err = err_fn or _default_bff_error

    if not clean_entity_id:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Action target id is required",
            "entityId must be a non-empty string",
            precondition_failed="entity_id",
        )
    if not clean_action_id:
        raise _err(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Action id is required",
            "actionId must be a non-empty string",
            precondition_failed="action_id",
        )

    spec = action_adapter_spec(normalized_entity_type, err_fn=_err)
    audit_event = action_adapter_audit_event(spec, clean_action_id)
    body = dict(payload or {})
    reason = str(
        body.get("reason")
        or body.get("operator_note")
        or body.get("note")
        or audit_event
    ).strip()

    params = {
        **body,
        "action_id": clean_action_id,
        "entity_type": normalized_entity_type,
        "entity_id": clean_entity_id,
        "audit_event": audit_event,
        "adapter_source_route": _ACTIONS_TO_COMMANDS_SOURCE_ROUTE,
    }

    raw_command = spec["command_type"]
    command_type = raw_command.value if hasattr(raw_command, "value") else str(raw_command)
    raw_target = spec["target_type"]
    target_type = raw_target.value if hasattr(raw_target, "value") else str(raw_target)

    if normalized_entity_type == "rebalance" and clean_action_id.lower() == "apply":
        command_type = CommandType.APPROVED_APPLY.value if hasattr(CommandType.APPROVED_APPLY, "value") else str(CommandType.APPROVED_APPLY)
        target_type = ObjectType.REBALANCE.value if hasattr(ObjectType.REBALANCE, "value") else str(ObjectType.REBALANCE)
        params["entity_type"] = "Rebalance"
        if "rebalance_id" not in params:
            params["rebalance_id"] = clean_entity_id
    elif normalized_entity_type == "persona" and clean_action_id.lower() == "emergencycontainment":
        command_type = CommandType.EMERGENCY_CONTAINMENT.value if hasattr(CommandType.EMERGENCY_CONTAINMENT, "value") else str(CommandType.EMERGENCY_CONTAINMENT)
        target_type = ObjectType.PERSONA.value if hasattr(ObjectType.PERSONA, "value") else str(ObjectType.PERSONA)
        params["entity_type"] = "Persona"
        if "persona_id" not in params:
            params["persona_id"] = clean_entity_id

    return {
        "command": command_type,
        "target": {
            "type": target_type,
            "id": clean_entity_id,
        },
        "action": clean_action_id,
        "params": params,
        "audit_context": {"reason": reason},
    }


def create_action_command_router(
    *,
    submit_command_admission: Optional[Callable[..., Any]] = None,
    extract_identity: Optional[Callable[..., Any]] = None,
    require_operator_role: Optional[Callable[..., None]] = None,
    bff_error: Optional[Callable[..., HTTPException]] = None,
    utc_now: Optional[Callable[[], str]] = None,
    command_store: Optional[Any] = None,
    dispatch_command: Optional[Callable[..., Any]] = None,
) -> APIRouter:
    """Create the canonical Action Command Adapter APIRouter.

    Registers exactly ONE route:
      POST /bff/actions/{type}/{id}/{action}
    """
    router = APIRouter()

    _extract_ident = extract_identity or _default_extract_identity
    _require_op = require_operator_role or (lambda ident: _default_require_operator_role(ident, bff_error))
    _err = bff_error or _default_bff_error
    _utc_now = utc_now or _default_utc_now

    @router.post(
        "/bff/actions/{type}/{id}/{action}",
        status_code=202,
        deprecated=True,
        operation_id="submit_bff_action_generic",
        summary="Submit deprecated generic BFF action",
    )
    async def sem_canonical_action_command(
        background_tasks: BackgroundTasks,
        response: Response,
        type: str,
        id: str,
        action: str,
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
        apply_legacy_action_deprecation_headers(response)

        payload_dict = dict(payload or {})
        if "idempotency_key" in payload_dict or "idempotencyKey" in payload_dict:
            raise _err(
                400,
                ErrorCode.VALIDATION_FAILED,
                "Idempotency keys must be provided via the Idempotency-Key header, not in the request body",
                "Request body contained an idempotencyKey/idempotency_key field",
                precondition_failed="body_idempotency_key",
                suggestion="Move the idempotency key to the Idempotency-Key request header",
            )

        resolved_key = (
            str(idempotency_key).strip()
            if idempotency_key
            else (str(x_idempotency_key).strip() if x_idempotency_key else "")
        )
        if not resolved_key:
            raise _err(
                400,
                ErrorCode.VALIDATION_FAILED,
                "An Idempotency-Key header is required for state-mutating requests",
                "Missing required Idempotency-Key header",
                precondition_failed="idempotency_key",
                suggestion="Include an Idempotency-Key: <unique-key> header in the request",
            )

        command_payload = build_action_adapter_command_payload(
            entity_type=type,
            entity_id=id,
            action_id=action,
            payload=payload_dict,
            err_fn=_err,
        )
        params = command_payload["params"]

        deprecation = {
            "deprecated": True,
            "route": _CANONICAL_ACTIONS_ROUTE,
            "sunset": "2026-06-15",
            "replacement": "/bff/v1/commands",
            "migration_guide": "docs/02-architecture/BFF_COMMAND_API_CONTRACT.md",
        }

        if submit_command_admission is not None:
            return submit_command_admission(
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

        if command_store is not None:
            if hasattr(command_store, "append_command"):
                command_store.append_command(record)
            elif hasattr(command_store, "_update_commands"):
                existing = command_store._get_all_commands() if hasattr(command_store, "_get_all_commands") else []
                command_store._update_commands(existing + [record])

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
