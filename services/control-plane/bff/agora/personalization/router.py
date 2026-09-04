"""Agora personalization router — agora.personalization.v1."""
from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query

from ...models import CommandType, ErrorCode, ObjectType
from ..service import AgoraService


def create_personalization_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    service: Optional[AgoraService] = None,
    require_write_role: Optional[Callable[..., None]] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    get_command_store: Optional[Callable[[], Any]] = None,
    idempotency_store: Optional[Dict[str, Any]] = None,
) -> APIRouter:
    """Personalization router — insights and institutional memory."""
    router = APIRouter(tags=["agora-personalization"])

    svc = service or AgoraService(
        get_read_store=get_read_store,
        get_command_store=get_command_store,
        idempotency_store=idempotency_store,
        utc_now=utc_now,
        bff_error=bff_error,
    )

    def _require_operator(identity: Any) -> None:
        if require_write_role is not None:
            require_write_role(identity)
            return
        roles = set(getattr(identity, "roles", []) or [])
        if not roles.intersection({"operator", "approver", "admin", "reviewer"}):
            raise bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "Operator role required",
                "Action requires operator-level access",
                precondition_failed="role_check",
            )

    @router.get("/bff/agora/insights")
    async def bff_agora_insights(
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: Agora insight inbox."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        snapshot_at = utc_now()
        return svc.agora_list_response(
            dataset="insight_cards",
            surface_key="agora_insight_list",
            items=svc.list_insights(),
            page_token=page_token,
            page_size=page_size,
            snapshot_at=snapshot_at,
        )

    @router.post("/bff/agora/insights", status_code=201)
    async def bff_create_agora_insight(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        x_dry_run: Optional[str] = Header(default=None, alias="X-Dry-Run"),
    ):
        """BFF: create an Agora insight card."""
        identity = extract_identity(authorization)
        _require_operator(identity)
        svc.reject_body_idempotency_key(payload)
        summary = svc.agora_required_text(payload, "summary", "title")
        resolved_key = svc.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = svc.stable_json_hash({"route": "POST /bff/agora/insights", "payload": payload})
        dry_run = bool(x_dry_run and x_dry_run.strip().lower() in ("true", "1", "yes"))
        if not dry_run:
            cached = svc.check_idempotency(resolved_key, request_hash)
            if cached is not None:
                return cached
        snapshot_at = utc_now()
        insight_id = str(payload.get("id") or payload.get("insight_id") or f"ins-agora-{uuid.uuid4().hex[:10]}")
        if dry_run:
            return svc.dry_run_success_response(
                {
                    "id": insight_id,
                    "insightId": insight_id,
                    "summary": summary,
                    "actorId": identity.operator_id,
                    "createdAt": snapshot_at,
                    **payload,
                },
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="agora.insight.create",
            )
        created = svc.create_insight(
            insight_id=insight_id,
            summary=summary,
            actor_id=identity.operator_id,
            payload=payload,
            created_at=snapshot_at,
        )
        result = {
            "data": created,
            "meta": {"snapshot_at": snapshot_at},
        }
        svc.record_idempotency(resolved_key, request_hash, result)
        return result

    @router.post("/bff/agora/insights/{insightId}/actions/{actionId}", status_code=202)
    async def bff_agora_insight_action(
        insightId: str,
        actionId: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: route an Agora insight action through command admission."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        svc.reject_body_idempotency_key(payload)
        resolved_key = svc.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        if not svc.get_insight(insightId):
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Agora insight not found",
                f"Agora insight {insightId} does not exist",
                precondition_failed="insight_id",
            )
        return svc.submit_action_command(
            route="POST /bff/agora/insights/{insightId}/actions/{actionId}",
            entity_type=ObjectType.AGORA_INSIGHT,
            entity_id=insightId,
            action_id=actionId,
            resolved_key=resolved_key,
            identity=identity,
            payload=payload,
            command_type=CommandType.AGORA_INSIGHT_ACTION,
        )

    @router.get("/bff/agora/memory")
    async def bff_agora_memory(
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: Agora institutional memory review list."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        snapshot_at = utc_now()
        return svc.agora_list_response(
            dataset="institutional_memory_entries",
            surface_key="agora_memory_list",
            items=svc.list_memory(),
            page_token=page_token,
            page_size=page_size,
            snapshot_at=snapshot_at,
        )

    @router.post("/bff/agora/memory/{memoryId}/actions/{actionId}", status_code=202)
    async def bff_agora_memory_action(
        memoryId: str,
        actionId: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: route an Agora memory action through command admission."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        svc.reject_body_idempotency_key(payload)
        resolved_key = svc.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        if not svc.get_memory_entry(memoryId):
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Agora memory entry not found",
                f"Agora memory entry {memoryId} does not exist",
                precondition_failed="memory_id",
            )
        return svc.submit_action_command(
            route="POST /bff/agora/memory/{memoryId}/actions/{actionId}",
            entity_type=ObjectType.AGORA_MEMORY,
            entity_id=memoryId,
            action_id=actionId,
            resolved_key=resolved_key,
            identity=identity,
            payload=payload,
            command_type=CommandType.AGORA_MEMORY_ACTION,
        )

    @router.post("/bff/memory/{memoryId}/actions/quarantine", status_code=202)
    async def bff_memory_quarantine_action(
        memoryId: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: execute-plans compatibility alias for memory quarantine."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        svc.reject_body_idempotency_key(payload)
        resolved_key = svc.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        if not svc.get_memory_entry(memoryId):
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Memory entry not found",
                f"Memory entry {memoryId} does not exist",
                precondition_failed="memory_id",
            )
        return svc.submit_action_command(
            route="POST /bff/memory/{memoryId}/actions/quarantine",
            entity_type=ObjectType.AGORA_MEMORY,
            entity_id=memoryId,
            action_id="quarantine",
            resolved_key=resolved_key,
            identity=identity,
            payload=payload,
            command_type=CommandType.AGORA_MEMORY_ACTION,
        )

    @router.post("/bff/insights/{insightId}/actions/attach-strategy", status_code=202)
    async def bff_insight_attach_strategy_action(
        insightId: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: execute-plans compatibility alias for attaching an insight to a strategy."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        svc.reject_body_idempotency_key(payload)
        resolved_key = svc.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        if not svc.get_insight(insightId):
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Insight not found",
                f"Insight {insightId} does not exist",
                precondition_failed="insight_id",
            )
        return svc.submit_action_command(
            route="POST /bff/insights/{insightId}/actions/attach-strategy",
            entity_type=ObjectType.AGORA_INSIGHT,
            entity_id=insightId,
            action_id="attach-strategy",
            resolved_key=resolved_key,
            identity=identity,
            payload=payload,
            command_type=CommandType.AGORA_INSIGHT_ACTION,
        )

    return router
