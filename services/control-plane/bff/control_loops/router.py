"""Control Loops domain router.

This prepared router owns the 24 route decorators catalogued for
``OPGAP-BE-CONTROL-LOOPS-V2-20260830``.  It has no reverse dependency on
``main.py``; the BFF composition root mounts it with
``app.include_router(create_control_loops_router(...))`` during the assembly
cutover.
"""
from __future__ import annotations

import copy
import inspect
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Header, Query, Request

from services.control_plane.bff.loop_inventory import (
    LoopHealthDetailEnvelope,
    LoopHealthListEnvelope,
    LoopInventoryDetailEnvelope,
    LoopInventoryListEnvelope,
)
from services.control_plane.bff.models import (
    CommandType,
    ErrorCode,
    ObjectType,
    OperatorIdentity,
    redact_evidence_field_items,
    redact_evidence_refs as _default_redact_evidence_refs,
    redact_ooda_packet,
    redact_ooda_packet_items,
    safe_redact_evidence_refs,
)

from .service import ControlLoopsService, default_bff_error


IdentityExtractor = Callable[[Optional[str]], Any]
RoleChecker = Callable[[Any], None]

_TWO_MAN_SIGNER_FIELDS = {
    "signerOperatorId",
    "signer_operator_id",
    "operatorId",
    "operator_id",
    "first_operator_id",
    "firstOperatorId",
    "primary_operator_id",
    "primaryOperatorId",
    "second_operator_id",
    "secondOperatorId",
    "secondOperatorSignature",
    "second_operator_signature",
    "signed_by",
    "signedBy",
    "confirmed_by",
    "confirmedBy",
}
_TWO_MAN_SIGNER_LIST_FIELDS = {
    "signerOperatorIds",
    "signer_operator_ids",
    "operator_ids",
    "operatorIds",
}
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


def create_control_loops_router(
    *,
    service: Optional[ControlLoopsService] = None,
    read_surface: Optional[Any] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    loop_truth_adapter: Optional[Any] = None,
    downstream_health_monitor: Optional[Any] = None,
    submit_sem_command: Optional[Callable[..., Any]] = None,
    reject_body_idempotency_key: Optional[Callable[[Dict[str, Any]], None]] = None,
    extract_identity: Optional[IdentityExtractor] = None,
    require_read_role: Optional[RoleChecker] = None,
    require_operator_role: Optional[RoleChecker] = None,
    bff_error: Optional[Callable[..., Exception]] = None,
    utc_now_fn: Optional[Callable[[], str]] = None,
    deployed_environment: Optional[str] = None,
    served_stages: Optional[Sequence[str]] = None,
    redact_evidence_refs: Optional[Callable[..., Tuple[List[Dict[str, Any]], int]]] = None,
    capabilities_for_identity: Optional[Callable[[Any], Any]] = None,
) -> APIRouter:
    """Build the exact 24-decorator Control Loops router."""

    router = APIRouter()
    _extract = extract_identity or _default_extract_identity
    _require_read = require_read_role or _default_require_read_role
    _require_operator = require_operator_role or _default_require_operator_role
    _err = bff_error or default_bff_error
    _redact = redact_evidence_refs or _default_redact_evidence_refs
    _capabilities = capabilities_for_identity or (lambda identity: [])

    def _redact_items(identity: Any, items: List[Any]) -> Tuple[List[Any], int]:
        return redact_evidence_field_items(
            identity, items, field="evidence_refs", redact_fn=_redact, capabilities_fn=_capabilities
        )

    def _redact_single(identity: Any, item: Any) -> Tuple[Any, int]:
        redacted, count = _redact_items(identity, [item])
        return redacted[0], count

    if service is None:
        if read_surface is not None:
            read_store = read_surface() if callable(read_surface) else read_surface
        elif get_read_store:
            read_store = get_read_store()
        else:
            read_store = None
        service = ControlLoopsService(
            read_store=read_store,
            loop_truth_adapter=loop_truth_adapter,
            downstream_health_monitor=downstream_health_monitor,
            utc_now_fn=utc_now_fn,
            bff_error_fn=_err,
            deployed_environment=deployed_environment,
            served_stages=served_stages,
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

    def _reject_body_key(payload: Dict[str, Any]) -> None:
        if reject_body_idempotency_key is not None:
            reject_body_idempotency_key(payload)
            return
        if any(key in payload for key in ("idempotencyKey", "idempotency_key")):
            raise _err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Idempotency key must be supplied in a header",
                "Body idempotency keys are not accepted",
                precondition_failed="idempotency_key_location",
            )

    async def _submit_sem(**kwargs: Any) -> Any:
        if submit_sem_command is None:
            raise _err(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Control-loop command admission is not composed",
                "The composition root must inject the canonical semantic command owner.",
                precondition_failed="submit_sem_command",
            )
        result = submit_sem_command(**kwargs)
        return await result if inspect.isawaitable(result) else result

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
        identity = _read_identity(authorization)
        response = resolved_service.list_ooda_packets(
            status=status,
            stage=stage,
            strategy_id=strategy_id,
            runtime_id=runtime_id,
            evolution_program_id=evolution_program_id,
            page_token=page_token,
            page_size=page_size,
        )
        redacted_items, redacted_count = redact_ooda_packet_items(
            identity, response["items"], redact_fn=_redact, capabilities_fn=_capabilities
        )
        response["items"] = redacted_items
        response["data"] = redacted_items
        response.setdefault("meta", {})["redacted_evidence_count"] = redacted_count
        return response

    @router.get("/bff/ooda/packets/{packet_id}")
    async def bff_get_ooda_packet(
        packet_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _read_identity(authorization)
        response = resolved_service.get_ooda_packet(str(packet_id or "").strip())
        response["data"], redacted_count = redact_ooda_packet(
            identity, response["data"], redact_fn=_redact, capabilities_fn=_capabilities
        )
        response.setdefault("meta", {})["redacted_evidence_count"] = redacted_count
        return response

    # Guarded-command two-man evidence signing (the only surviving /interventions route).
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
        _reject_body_key(payload)
        action = request.url.path.rsplit("/", 1)[-1]
        target_id = str(id or "").strip()
        trusted_evidence_producer: Optional[str] = None
        terminal_on_persist = False
        if action == "two-man-sign":
            roles = set(getattr(identity, "roles", []) or [])
            if not {"operator", "approver", "admin"}.intersection(roles):
                raise _err(
                    403,
                    ErrorCode.FORBIDDEN,
                    "Two-man evidence requires operator authority",
                    "Reviewer and viewer roles cannot sign guarded command evidence",
                    precondition_failed="role_check",
                )
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
        return await _submit_sem(
            command_type=CommandType.V5_INTERVENTION_ACTION,
            target_type=ObjectType.SENTINEL_INTERVENTION,
            target_id=target_id,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            terminal_on_persist=terminal_on_persist,
            trusted_evidence_producer=trusted_evidence_producer,
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
        identity = _read_identity(authorization)
        raw_result = resolved_service.downstream_health()
        result = copy.deepcopy(raw_result)
        data = result.get("data")
        total_redacted = 0
        if isinstance(data, dict):
            replays = data.get("delivery_replays")
            if isinstance(replays, list) and replays:
                for replay in replays:
                    if isinstance(replay, dict):
                        if replay.get("approval_ref"):
                            redacted_ref, count = safe_redact_evidence_refs(
                                identity,
                                [replay["approval_ref"]],
                                redact_fn=_redact,
                                capabilities_fn=_capabilities,
                                default_kind="approval",
                            )
                            if count > 0:
                                replay["approval_ref"] = redacted_ref[0]
                                total_redacted += count
                        if isinstance(replay.get("evidence_refs"), list) and replay["evidence_refs"]:
                            redacted_refs, count = safe_redact_evidence_refs(
                                identity,
                                replay["evidence_refs"],
                                redact_fn=_redact,
                                capabilities_fn=_capabilities,
                            )
                            replay["evidence_refs"] = redacted_refs
                            total_redacted += count
            incidents = data.get("incidents")
            if isinstance(incidents, dict):
                for inc_row in incidents.values():
                    if isinstance(inc_row, dict) and isinstance(inc_row.get("evidence_refs"), list) and inc_row["evidence_refs"]:
                        redacted_refs, count = safe_redact_evidence_refs(
                            identity,
                            inc_row["evidence_refs"],
                            redact_fn=_redact,
                            capabilities_fn=_capabilities,
                        )
                        inc_row["evidence_refs"] = redacted_refs
                        total_redacted += count
            for key in ("evidence_refs", "linked_evidence"):
                if isinstance(data.get(key), list) and data[key]:
                    redacted_refs, count = safe_redact_evidence_refs(
                        identity,
                        data[key],
                        redact_fn=_redact,
                        capabilities_fn=_capabilities,
                    )
                    data[key] = redacted_refs
                    total_redacted += count
        result.setdefault("meta", {})["redacted_evidence_count"] = total_redacted
        return result

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

    # 23: aggregate control room.
    @router.get("/bff/v5/control-room")
    async def bff_v5_control_room(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _read_identity(authorization)
        response = resolved_service.control_room()
        loops_items, loops_count = _redact_items(identity, response["loops"]["items"])
        response["loops"]["items"] = loops_items
        incident_items, incident_count = _redact_items(identity, response["incidents"]["items"])
        response["incidents"]["items"] = incident_items
        response.setdefault("meta", {})["redacted_evidence_count"] = loops_count + incident_count
        return response

    return router


create_loops_router = create_control_loops_router

__all__ = ["create_control_loops_router", "create_loops_router"]
