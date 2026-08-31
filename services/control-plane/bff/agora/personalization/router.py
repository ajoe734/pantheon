"""Agora personalization router — agora.personalization.v1."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException


_MAIN_SYMBOL_NAMES = (
    "_agora_list_response",
    "_require_operator_role",
    "_reject_body_idempotency_key",
    "_resolve_final_idempotency_key",
    "_stable_json_hash",
    "_agora_core_idempotency_check",
    "_request_dry_run_requested",
    "_dry_run_success_response",
    "_agora_required_text",
    "ErrorCode",
    "ObjectType",
    "CommandType",
    "_agora_get_insight",
    "_agora_action_command",
)


def create_personalization_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    main_module: Any = None,
) -> APIRouter:
    """Personalization router — migrated from main.py.

    ``main_module`` should be the caller's own ``sys.modules[__name__]`` (see
    identity/router.py's create_identity_router docstring for why this
    replaces a bare ``import main``).
    """
    router = APIRouter(tags=["agora-personalization"])

    if main_module is None:
        import main as main_module
    main = main_module
    (
        _agora_list_response,
        _require_operator_role,
        _reject_body_idempotency_key,
        _resolve_final_idempotency_key,
        _stable_json_hash,
        _agora_core_idempotency_check,
        _request_dry_run_requested,
        _dry_run_success_response,
        _agora_required_text,
        ErrorCode,
        ObjectType,
        CommandType,
        _agora_get_insight,
        _agora_action_command,
    ) = (getattr(main_module, name) for name in _MAIN_SYMBOL_NAMES)
    import uuid
    from fastapi import Body, Header, Query

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
        return _agora_list_response(
            dataset="insight_cards",
            surface_key="agora_insight_list",
            items=main.read_store.list_agora_insights(),
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
    ):
        """BFF: create an Agora insight card."""
        identity = extract_identity(authorization)
        _require_operator_role(identity)
        _reject_body_idempotency_key(payload)
        summary = _agora_required_text(payload, "summary", "title")
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = _stable_json_hash({"route": "POST /bff/agora/insights", "payload": payload})
        dry_run = _request_dry_run_requested()
        if not dry_run:
            cached = _agora_core_idempotency_check(resolved_key, request_hash)
            if cached is not None:
                return cached
        snapshot_at = utc_now()
        insight_id = str(payload.get("id") or payload.get("insight_id") or f"ins-agora-{uuid.uuid4().hex[:10]}")
        if dry_run:
            return _dry_run_success_response(
                {
                    "id": insight_id,
                    "insight_id": insight_id,
                    "summary": summary,
                    "scope": payload.get("scope") or "global",
                    "status": payload.get("status") or "classified",
                    "tags": list(payload.get("tags") or []),
                    "created_at": snapshot_at,
                    "updated_at": snapshot_at,
                },
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="agora.insight.create",
            )
        result = {
            "data": main.read_store.create_agora_insight(
                insight_id=insight_id,
                summary=summary,
                actor_id=identity.operator_id,
                payload=payload,
                created_at=snapshot_at,
            ),
            "meta": {"snapshot_at": snapshot_at},
        }
        main._AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
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
        _reject_body_idempotency_key(payload)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        if not _agora_get_insight(insightId):
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Agora insight not found",
                f"Agora insight {insightId} does not exist",
                precondition_failed="insight_id",
            )
        return _agora_action_command(
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
        return _agora_list_response(
            dataset="institutional_memory_entries",
            surface_key="agora_memory_list",
            items=main.read_store.list_agora_memory(),
            page_token=page_token,
            page_size=page_size,
            snapshot_at=snapshot_at,
        )

    @router.post("/bff/agora/memory/{memoryId}/actions/{actionId}", status_code=202)
    async def bff_agora_memory_action(
        memoryId: str,
        memoryId_val: Optional[str] = None, # dummy for parameter compatibility if any
        actionId: str = None,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: route an Agora memory action through command admission."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        _reject_body_idempotency_key(payload)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        if not main.read_store.get_agora_memory_entry(memoryId):
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Agora memory entry not found",
                f"Agora memory entry {memoryId} does not exist",
                precondition_failed="memory_id",
            )
        return _agora_action_command(
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
        _reject_body_idempotency_key(payload)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        if not main.read_store.get_agora_memory_entry(memoryId):
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Memory entry not found",
                f"Memory entry {memoryId} does not exist",
                precondition_failed="memory_id",
            )
        return _agora_action_command(
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
        _reject_body_idempotency_key(payload)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        if not _agora_get_insight(insightId):
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Insight not found",
                f"Insight {insightId} does not exist",
                precondition_failed="insight_id",
            )
        return _agora_action_command(
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
