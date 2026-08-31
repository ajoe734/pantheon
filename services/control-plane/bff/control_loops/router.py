"""Control Loops domain router.

This prepared router owns the 24 route decorators catalogued for
``OPGAP-BE-CONTROL-LOOPS-V2-20260830``.  It has no reverse dependency on
``main.py``; the BFF composition root mounts it with
``app.include_router(create_control_loops_router(...))`` during the assembly
cutover.
"""
from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Header, Query, Request

try:
    from loop_inventory import (
        LoopHealthDetailEnvelope,
        LoopHealthListEnvelope,
        LoopInventoryDetailEnvelope,
        LoopInventoryListEnvelope,
    )
    from models import (
        CommandType,
        ErrorCode,
        InterventionListResponse,
        ObjectType,
        OperatorIdentity,
    )
except ImportError:
    from ..loop_inventory import (  # type: ignore[no-redef]
        LoopHealthDetailEnvelope,
        LoopHealthListEnvelope,
        LoopInventoryDetailEnvelope,
        LoopInventoryListEnvelope,
    )
    from ..models import (  # type: ignore[no-redef]
        CommandType,
        ErrorCode,
        InterventionListResponse,
        ObjectType,
        OperatorIdentity,
    )

from .service import ControlLoopsService, default_bff_error


IdentityExtractor = Callable[[Optional[str]], Any]
RoleChecker = Callable[[Any], None]

_TWO_MAN_SIGNER_FIELDS = {
    "signerOperatorId",
    "signer_operator_id",
    "operatorId",
    "operator_id",
}
_TWO_MAN_SIGNER_LIST_FIELDS = {"signerOperatorIds", "signer_operator_ids"}
_V5_TWO_MAN_EVIDENCE_PRODUCER = "bff.v5.intervention.two-man-sign"


def _default_extract_identity(authorization: Optional[str] = None) -> OperatorIdentity:
    if not authorization or not authorization.startswith("Bearer "):
        raise default_bff_error(
            401,
            ErrorCode.AUTH_REQUIRED,
            "Missing or invalid Authorization header",
            "Token is absent or not a Bearer token",
        )
    token = authorization[len("Bearer ") :].strip()
    if not token:
        raise default_bff_error(
            401,
            ErrorCode.AUTH_REQUIRED,
            "Missing or invalid Authorization header",
            "Token is absent or not a Bearer token",
        )
    parts = token.split(":")
    roles = [role.strip() for role in (parts[1] if len(parts) > 1 else "viewer").split(",") if role.strip()]
    claims: Dict[str, Any] = {}
    if len(parts) > 4 and parts[4].strip():
        claims["tenant_id"] = parts[4].strip()
        claims["allowed_tenants"] = [parts[4].strip()]
    return OperatorIdentity(
        operator_id=parts[0] or "operator",
        roles=roles or ["viewer"],
        mfa_verified=any(part.strip().lower() == "mfa" for part in parts[2:]),
        claims=claims,
    )


def _default_require_read_role(identity: Any) -> None:
    roles = set(getattr(identity, "roles", []) or [])
    if not {"viewer", "view_only", "operator", "approver", "admin", "reviewer"}.intersection(roles):
        raise default_bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Read access requires viewer-level role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )


def _default_require_operator_role(identity: Any) -> None:
    roles = set(getattr(identity, "roles", []) or [])
    if not {"operator", "approver", "admin"}.intersection(roles):
        raise default_bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Control-loop command access requires operator authority",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )


def _identity_tenant(identity: Any) -> Optional[str]:
    claims = getattr(identity, "claims", {})
    if not isinstance(claims, dict):
        return None
    for key in ("tenant_id", "tenantId", "tid", "org_id"):
        value = claims.get(key)
        if value:
            return str(value).strip() or None
    tenant = claims.get("tenant")
    if isinstance(tenant, dict) and tenant.get("id"):
        return str(tenant["id"]).strip() or None
    return None


def create_control_loops_router(
    *,
    service: Optional[ControlLoopsService] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    loop_truth_adapter: Optional[Any] = None,
    downstream_health_monitor: Optional[Any] = None,
    command_submitter: Optional[Callable[..., Any]] = None,
    final_command_submitter: Optional[Callable[..., Any]] = None,
    health_findings_provider: Optional[Callable[..., Any]] = None,
    extract_identity: Optional[IdentityExtractor] = None,
    require_read_role: Optional[RoleChecker] = None,
    require_operator_role: Optional[RoleChecker] = None,
    bff_error: Optional[Callable[..., Exception]] = None,
    utc_now_fn: Optional[Callable[[], str]] = None,
    deployed_environment: Optional[str] = None,
) -> APIRouter:
    """Build the exact 24-decorator Control Loops router."""

    router = APIRouter()
    _extract = extract_identity or _default_extract_identity
    _require_read = require_read_role or _default_require_read_role
    _require_operator = require_operator_role or _default_require_operator_role
    _err = bff_error or default_bff_error

    if service is None:
        read_store = get_read_store() if get_read_store else None
        service = ControlLoopsService(
            read_store=read_store,
            loop_truth_adapter=loop_truth_adapter,
            downstream_health_monitor=downstream_health_monitor,
            command_submitter=command_submitter,
            final_command_submitter=final_command_submitter,
            health_findings_provider=health_findings_provider,
            utc_now_fn=utc_now_fn,
            bff_error_fn=_err,
            deployed_environment=deployed_environment,
        )
    resolved_service = service

    def _read_identity(authorization: Optional[str]) -> Any:
        identity = _extract(authorization)
        _require_read(identity)
        return identity

    def _operator_identity(authorization: Optional[str]) -> Any:
        identity = _extract(authorization)
        _require_operator(identity)
        return identity

    # 1-2: OODA packet management reads.
    @router.get("/bff/ooda/packets")
    async def bff_list_ooda_packets(
        status: Optional[str] = None,
        stage: Optional[str] = None,
        strategy_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
        evolution_program_id: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _read_identity(authorization)
        return resolved_service.list_ooda_packets(
            status=status,
            stage=stage,
            strategy_id=strategy_id,
            runtime_id=runtime_id,
            evolution_program_id=evolution_program_id,
            page_token=page_token,
            page_size=page_size,
        )

    @router.get("/bff/ooda/packets/{packet_id}")
    async def bff_get_ooda_packet(
        packet_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _read_identity(authorization)
        return resolved_service.get_ooda_packet(str(packet_id or "").strip())

    # 3-4: intervention list and critical remediation admission.
    @router.get("/bff/v5/interventions", response_model=InterventionListResponse)
    async def list_v5_interventions(
        status: Optional[str] = Query(default=None),
        kind: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _read_identity(authorization)
        return resolved_service.list_interventions(status=status, kind=kind)

    @router.post("/bff/v5/interventions/{intervention_id}/remediate", status_code=202)
    async def remediate_v5_intervention(
        intervention_id: str,
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
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        resolved_service.reject_body_idempotency_key(payload)
        action = resolved_service.validate_remediation(payload, identity)
        signature = str(
            payload.get("twoManSignatureId")
            or payload.get("two_man_signature_id")
            or payload.get("secondOperatorId")
            or payload.get("second_operator_id")
            or ""
        ).strip()
        if not signature:
            raise _err(
                409,
                ErrorCode.TWO_MAN_SIGNATURE_REQUIRED,
                "Two-man signature is required",
                "Sentinel remediation requires a second-operator evidence reference",
                precondition_failed="two_man_signature",
            )
        if not x_confirm_token:
            raise _err(
                428,
                ErrorCode.CONFIRMATION_REQUIRED,
                "Confirm token is required",
                "Critical sentinel remediation requires a bound confirm token",
                precondition_failed="confirm_token",
            )
        approval_id = str(payload.get("approvalId") or payload.get("approval_id") or "").strip()
        if not approval_id:
            raise _err(
                428,
                ErrorCode.CONFIRMATION_REQUIRED,
                "Approval evidence is required",
                "Critical sentinel remediation requires an approval reference",
                precondition_failed="approval_id",
            )
        clean_id = str(intervention_id or "").strip()
        params = {**payload, "intervention_id": clean_id, "remediation_action": action}
        command_payload = {
            "command": CommandType.REMEDIATE_SENTINEL_INTERVENTION.value,
            "target": {"type": ObjectType.SENTINEL_INTERVENTION.value, "id": clean_id},
            "action": "remediate_sentinel_intervention",
            "params": params,
            "audit_context": {
                "reason": str(payload.get("reason") or "HIQ Sentinel remediation"),
                "incident_id": str(payload.get("incident_id") or "").strip() or None,
            },
        }
        return await resolved_service.submit_typed_command(
            command_type=CommandType.REMEDIATE_SENTINEL_INTERVENTION,
            target_type=ObjectType.SENTINEL_INTERVENTION,
            target_id=clean_id,
            payload=command_payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            action=action,
            route="POST /bff/v5/interventions/{intervention_id}/remediate",
            headers={
                "X-MFA-Token": x_mfa_token,
                "X-Trace-Id": x_trace_id,
                "X-Correlation-Id": x_correlation_id,
                "X-Request-Id": x_request_id,
                "X-Confirm-Token": x_confirm_token,
            },
            background_tasks=background_tasks,
        )

    # 5: dedicated intervention decision admission.
    @router.post("/bff/v5/interventions/{id}/decide", status_code=202)
    async def sem_v5_intervention_decide_command(
        id: str,
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
    ) -> Dict[str, Any]:
        identity = _extract(authorization)
        clean_id = str(id or "").strip()
        if not clean_id:
            raise _err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Intervention id is required",
                "id must be a non-empty string",
                precondition_failed="intervention_id",
            )
        resolved_service.reject_body_idempotency_key(payload)
        decision = resolved_service.validate_decision(
            str(payload.get("decision") or payload.get("outcome") or ""), identity
        )
        reason = str(
            payload.get("reason") or payload.get("memo") or f"intervention.{decision}"
        ).strip()
        params = {
            **payload,
            "intervention_id": clean_id,
            "interventionId": clean_id,
            "decision": decision,
            "action_id": "decide",
            "audit_event": f"intervention.{decision}",
        }
        command_payload = {
            "command": CommandType.DECIDE_V5_INTERVENTION.value,
            "target": {"type": ObjectType.SENTINEL_INTERVENTION.value, "id": clean_id},
            "action": "decide",
            "params": params,
            "audit_context": {
                "reason": reason,
                "incident_id": str(payload.get("incident_id") or payload.get("incidentId") or "").strip() or None,
            },
        }
        return await resolved_service.submit_typed_command(
            command_type=CommandType.DECIDE_V5_INTERVENTION,
            target_type=ObjectType.SENTINEL_INTERVENTION,
            target_id=clean_id,
            payload=command_payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            action="decide",
            route="POST /bff/v5/interventions/{id}/decide",
            headers={
                "X-MFA-Token": x_mfa_token,
                "X-Trace-Id": x_trace_id,
                "X-Correlation-Id": x_correlation_id,
                "X-Request-Id": x_request_id,
                "X-Confirm-Token": x_confirm_token,
            },
            background_tasks=background_tasks,
        )

    # 6-9: shared intervention action handler owns four decorators.
    @router.post("/bff/v5/interventions/{id}/claim", status_code=202)
    @router.post("/bff/v5/interventions/{id}/escalate", status_code=202)
    @router.post("/bff/v5/interventions/{id}/release", status_code=202)
    @router.post("/bff/v5/interventions/{id}/two-man-sign", status_code=202)
    async def sem_v5_intervention_command(
        id: str,
        request: Request,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = _operator_identity(authorization)
        resolved_service.reject_body_idempotency_key(payload)
        action = request.url.path.rsplit("/", 1)[-1]
        target_id = str(id or "").strip()
        trusted_evidence_producer: Optional[str] = None
        terminal_on_persist = False
        if action == "two-man-sign":
            signature_id = str(
                payload.get("twoManSignatureId") or payload.get("two_man_signature_id") or ""
            ).strip()
            guarded_command = str(payload.get("command") or "").strip()
            guarded_target = payload.get("target")
            if (
                not signature_id
                or not guarded_command
                or not isinstance(guarded_target, dict)
                or not str(guarded_target.get("type") or "").strip()
                or not str(guarded_target.get("id") or "").strip()
            ):
                raise _err(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Two-man evidence must be fully bound",
                    "signature id, command, and target are required",
                    precondition_failed="two_man_evidence_binding",
                )
            payload = dict(payload)
            for alias in _TWO_MAN_SIGNER_FIELDS | _TWO_MAN_SIGNER_LIST_FIELDS:
                payload.pop(alias, None)
            payload["signerOperatorIds"] = [identity.operator_id]
            target_id = signature_id
            trusted_evidence_producer = _V5_TWO_MAN_EVIDENCE_PRODUCER
            terminal_on_persist = True
        return await resolved_service.submit_typed_command(
            command_type=CommandType.V5_INTERVENTION_ACTION,
            target_type=ObjectType.SENTINEL_INTERVENTION,
            target_id=target_id,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            action=action,
            terminal_on_persist=terminal_on_persist,
            trusted_evidence_producer=trusted_evidence_producer,
        )

    # 10-12: Sentinel finding/remediation trigger commands.
    @router.post("/bff/v5/sentinel/findings/{id}/status", status_code=202)
    async def sem_v5_sentinel_status_command(
        id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = _read_identity(authorization)
        resolved_service.reject_body_idempotency_key(payload)
        return await resolved_service.submit_typed_command(
            command_type=CommandType.SENTINEL_FINDING_STATUS,
            target_type=ObjectType.SENTINEL_FINDING,
            target_id=id,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            action="status",
        )

    @router.post("/bff/v5/sentinel/remediation/build", status_code=202)
    async def sem_v5_sentinel_remediation_build_command(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = _read_identity(authorization)
        resolved_service.reject_body_idempotency_key(payload)
        provided_finding = payload.get("finding_id") or payload.get("findingId")
        target_id = str(provided_finding or f"remediation-{uuid.uuid4().hex[:8]}")
        return await resolved_service.submit_typed_command(
            command_type=CommandType.SENTINEL_REMEDIATION_BUILD,
            target_type=ObjectType.SENTINEL_REMEDIATION,
            target_id=target_id,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            action="build",
        )

    @router.post("/bff/v5/sentinel/remediation/{actionId}/execute", status_code=202)
    async def sem_v5_sentinel_remediation_execute_command(
        actionId: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = _read_identity(authorization)
        resolved_service.reject_body_idempotency_key(payload)
        return await resolved_service.submit_typed_command(
            command_type=CommandType.SENTINEL_REMEDIATION_EXECUTE,
            target_type=ObjectType.SENTINEL_REMEDIATION,
            target_id=actionId,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            action="execute",
        )

    # 13-17: Sentinel, loop inventory, and controller-health reads.
    @router.get("/bff/v5/sentinel/findings")
    async def bff_v5_sentinel_findings_list(
        kind: Optional[str] = Query(default=None),
        status: Optional[str] = Query(default=None),
        severity: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _read_identity(authorization)
        return resolved_service.list_sentinel_findings(
            kind=kind,
            status=status,
            severity=severity,
            tenant_id=_identity_tenant(identity),
        )

    @router.get("/bff/v5/loop-inventory", response_model=LoopInventoryListEnvelope)
    async def bff_v5_loop_inventory(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _read_identity(authorization)
        return resolved_service.loop_inventory()

    @router.get("/bff/v5/loop-health", response_model=LoopHealthListEnvelope)
    async def bff_v5_loop_health(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        environment: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        identity = _read_identity(authorization)
        return await resolved_service.loop_health(
            identity,
            requested_tenant=x_tenant_id,
            requested_environment=environment,
        )

    @router.get("/bff/v5/loop-health/{loop_id}", response_model=LoopHealthDetailEnvelope)
    async def bff_v5_loop_health_detail(
        loop_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        environment: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        identity = _read_identity(authorization)
        return await resolved_service.loop_health_detail(
            loop_id,
            identity,
            requested_tenant=x_tenant_id,
            requested_environment=environment,
        )

    @router.get("/bff/v5/loop-inventory/{loop_id}", response_model=LoopInventoryDetailEnvelope)
    async def bff_v5_loop_inventory_detail(
        loop_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _read_identity(authorization)
        return resolved_service.loop_inventory_detail(loop_id)

    # 18-19: downstream monitor read and audited delivery replay.
    @router.get("/bff/v5/downstream-health")
    async def bff_v5_downstream_health(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _read_identity(authorization)
        return resolved_service.downstream_health()

    @router.post("/bff/v5/downstream-health/dlq/replay")
    async def bff_v5_downstream_health_dlq_replay(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _operator_identity(authorization)
        return await resolved_service.replay_downstream_health_dead_letters(
            identity=identity,
            payload=payload,
        )

    # 20-22: loop-run and Sentinel detail read models.
    @router.get("/bff/v5/loop-runs")
    async def bff_list_loop_runs(
        status: Optional[str] = None,
        tenant_id: Optional[str] = None,
        environment: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _read_identity(authorization)
        return await resolved_service.list_loop_runs(
            identity,
            status=status,
            tenant_id=tenant_id,
            environment=environment,
            page_token=page_token,
            page_size=page_size,
        )

    @router.get("/bff/v5/loop-runs/{loop_run_id}")
    async def bff_get_loop_run(
        loop_run_id: str,
        tenant_id: Optional[str] = None,
        environment: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _read_identity(authorization)
        return await resolved_service.get_loop_run(
            str(loop_run_id or "").strip(),
            identity,
            tenant_id=tenant_id,
            environment=environment,
        )

    @router.get("/bff/v5/sentinel/findings/{finding_id}")
    async def bff_get_sentinel_finding(
        finding_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _read_identity(authorization)
        return resolved_service.get_sentinel_finding(str(finding_id or "").strip())

    # 23-24: aggregate control room and intervention detail.
    @router.get("/bff/v5/control-room")
    async def bff_v5_control_room(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _read_identity(authorization)
        return resolved_service.control_room()

    @router.get("/bff/v5/interventions/{intervention_id}")
    async def bff_v5_intervention_detail(
        intervention_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _read_identity(authorization)
        return resolved_service.get_intervention(intervention_id)

    return router


create_loops_router = create_control_loops_router

__all__ = ["create_control_loops_router", "create_loops_router"]
