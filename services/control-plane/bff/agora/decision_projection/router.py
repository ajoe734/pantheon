"""FastAPI router for Agora decision projection endpoints."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Query

from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
from ..models import AgoraEnvelope, AgoraListEnvelope, AgoraListMeta, AgoraMeta
from .models import DecisionEventRecord, DecisionProjectionCommand
from .producer import DecisionEventProducer
from .store import DecisionEventStore


def create_decision_projection_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_write_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    producer: Optional[DecisionEventProducer] = None,
    store: Optional[DecisionEventStore] = None,
) -> APIRouter:
    router = APIRouter(tags=["agora-decision-projection"])
    event_store = store or (producer.store if producer else DecisionEventStore())
    event_producer = producer or DecisionEventProducer(store=event_store)

    @router.post("/bff/agora/decision-projection/events")
    def produce_event(
        cmd: DecisionProjectionCommand,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_write_role(identity)

        try:
            scope = resolve_agora_user_scope(
                identity,
                utc_now=utc_now,
                requested_tenant_id=x_tenant_id or x_pantheon_tenant,
            )
        except AgoraScopeResolutionError as exc:
            raise bff_error(exc.status_code, "FORBIDDEN", exc.message, exc.reason)

        now_str = utc_now()
        record = event_producer.produce_decision_event(
            cmd,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            utc_now=now_str,
        )

        envelope = AgoraEnvelope(
            data=record.model_dump(),
            meta=AgoraMeta(
                snapshot_at=now_str,
                capability="agora.trading.v1",
                audience=f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            ),
        )
        return envelope.model_dump()

    @router.get("/bff/agora/decision-projection/events")
    def list_events(
        strategy_id: Optional[str] = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)

        try:
            scope = resolve_agora_user_scope(
                identity,
                utc_now=utc_now,
                requested_tenant_id=x_tenant_id or x_pantheon_tenant,
            )
        except AgoraScopeResolutionError as exc:
            raise bff_error(exc.status_code, "FORBIDDEN", exc.message, exc.reason)

        now_str = utc_now()
        records = event_store.list_events(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            strategy_id=strategy_id,
            limit=limit,
        )

        items = [r.model_dump() for r in records]
        envelope = AgoraListEnvelope(
            data=items,
            meta=AgoraListMeta(
                snapshot_at=now_str,
                capability="agora.trading.v1",
                audience=f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                total=len(items),
                page_size=limit,
            ),
        )
        return envelope.model_dump()

    @router.get("/bff/agora/decision-projection/events/{event_id}")
    def get_event(
        event_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)

        try:
            scope = resolve_agora_user_scope(
                identity,
                utc_now=utc_now,
                requested_tenant_id=x_tenant_id or x_pantheon_tenant,
            )
        except AgoraScopeResolutionError as exc:
            raise bff_error(exc.status_code, "FORBIDDEN", exc.message, exc.reason)

        now_str = utc_now()
        record = event_store.get_event(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            decision_event_id=event_id,
        )

        if record is None:
            raise bff_error(404, "NOT_FOUND", "Decision event not found or access denied")

        envelope = AgoraEnvelope(
            data=record.model_dump(),
            meta=AgoraMeta(
                snapshot_at=now_str,
                capability="agora.trading.v1",
                audience=f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            ),
        )
        return envelope.model_dump()

    return router
