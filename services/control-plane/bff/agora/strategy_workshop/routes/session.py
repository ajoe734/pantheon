"""Session-lifecycle Workshop routes: create/read/list, messages, events,
completeness, cards, readiness, reconstruct.

Split out of the former single-file strategy_workshop/router.py factory
(ACG-06-004). Route bodies below are unchanged from the original
implementation; only the surrounding closure scaffolding (this
build_session_router wrapper binding the shared admission context) is new.
"""
from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query, Response

from .._common import (
    _StrategyVersionProjectionError,
    _parse_etag_lock_version,
    _raise_cross_user_forbidden,
)
from ..events import _ws_publish
from ..operations import CanonicalOperationError
from ..readiness import build_readiness_assessment as _build_readiness_assessment
from ..cards import _build_workshop_cards, _merge_cards
from ..runner import run_reconstruction_worker
from ..schemas import (
    WorkshopCompletenessSnapshotRequest,
    WorkshopCreateRequest,
    WorkshopMessageRequest,
    WorkshopReadinessReassessRequest,
)


def build_session_router(
    *,
    store: Any,
    canonical: Any,
    private_content_store: Any,
    utc_now: Callable[[], str],
    bff_error: Callable[..., HTTPException],
    ctx: Any,
) -> APIRouter:
    router = APIRouter(tags=["agora-workshop"])
    _scope = ctx.scope
    _scoped_session = ctx.scoped_session
    _readiness_from_store_or_state = ctx.readiness_from_store_or_state
    _read_strategy_version = ctx.read_strategy_version

    # ------------------------------------------------------------------ #
    # GET /bff/agora/workshops — list user-scoped workshop sessions
    # ------------------------------------------------------------------ #
    @router.get("/bff/agora/workshops")
    def list_workshops(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        status: Optional[str] = Query(default=None),
        cursor: Optional[str] = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        sessions, next_cursor = store.list_sessions(
            user_id=scope.user_id,
            tenant_id=scope.tenant_id,
            status=status,
            cursor=cursor,
            limit=limit,
        )
        return {
            "data": sessions,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "next_cursor": next_cursor,
            },
        }

    # ------------------------------------------------------------------ #
    # POST /bff/agora/workshops — create workshop session
    # ------------------------------------------------------------------ #
    @router.post("/bff/agora/workshops", status_code=201)
    def create_workshop(
        body: WorkshopCreateRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id, write=True)
        initial_registry_id = str(body.strategy_spec_ref or "").strip()
        initial_strategy_id: Optional[str] = None
        if initial_registry_id:
            try:
                _, _, initial_strategy_id = _read_strategy_version(
                    registry_id=initial_registry_id,
                    scope=scope,
                )
            except CanonicalOperationError as exc:
                from models import ErrorCode

                if exc.status_code == 404:
                    status_code = 404
                    code = ErrorCode.RESOURCE_NOT_FOUND
                elif exc.retryable:
                    status_code = 503
                    code = ErrorCode.DEPENDENCY_UNAVAILABLE
                else:
                    status_code = 502
                    code = ErrorCode.UPSTREAM_ERROR
                raise bff_error(
                    status_code,
                    code,
                    "Referenced StrategySpec is unavailable",
                    exc.reason,
                    precondition_failed="strategy_spec_ref",
                ) from exc
            except _StrategyVersionProjectionError as exc:
                from models import ErrorCode

                code = (
                    ErrorCode.FORBIDDEN
                    if exc.status_code == 403
                    else ErrorCode.UPSTREAM_ERROR
                    if exc.status_code >= 500
                    else ErrorCode.RESOURCE_CONFLICT
                )
                raise bff_error(
                    exc.status_code,
                    code,
                    "Referenced StrategySpec projection is inconsistent",
                    exc.reason,
                    precondition_failed="strategy_spec_ref",
                ) from exc
        # Idempotency-Key is mandatory for all write operations on this endpoint.
        if idempotency_key is None:
            from models import ErrorCode
            raise bff_error(
                400, ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key header is required",
                "missing_idempotency_key",
                suggestion="Supply a UUID v4 in the Idempotency-Key request header",
            )
        # Reject duplicate keys for the same user+tenant+endpoint.
        idem_scope = f"{scope.user_id}:{scope.tenant_id}:POST:/bff/agora/workshops"
        if hasattr(store, "check_and_record_idempotency_key"):
            if store.check_and_record_idempotency_key(idem_scope, idempotency_key):
                from models import ErrorCode
                raise bff_error(
                    409, ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Duplicate Idempotency-Key", idempotency_key,
                )
        workshop_id = str(uuid.uuid4())
        session = store.create_session({
            "workshop_id": workshop_id,
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "strategy_id": initial_strategy_id,
            "active_strategy_spec_registry_id": initial_registry_id or None,
            "status": "open",
        })
        try:
            if private_content_store is None:
                raise bff_error(503, "PRIVATE_CONTENT_STORE_UNAVAILABLE",
                                "Private content store is not configured", "private_content_store")
            initial_event_id = str(uuid.uuid4())
            private = private_content_store.put(
                tenant_id=scope.tenant_id, owner_user_id=scope.user_id,
                workshop_id=workshop_id, event_id=initial_event_id,
                content_type="text/plain", plaintext=body.initial_message.encode("utf-8"),
                retention_class="workshop_default", idempotency_key=idempotency_key,
            )
            try:
                store.create_event({
                    "event_id": initial_event_id,
                    "workshop_id": workshop_id,
                    "actor_type": "operator",
                    "event_type": "message",
                    "private_content_ref": private.private_content_ref,
                    "redacted_summary": "Private workshop message",
                })
            except Exception:
                private_content_store.discard_failed_write(
                    private_content_ref=private.private_content_ref,
                    tenant_id=scope.tenant_id,
                    owner_user_id=scope.user_id,
                )
                raise
        except Exception:
            store.rollback_create_session(
                workshop_id,
                idempotency_scope=idem_scope,
                idempotency_key=idempotency_key,
            )
            raise
        return {
            "data": session,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # ------------------------------------------------------------------ #
    # GET /bff/agora/workshops/{workshop_id} — get workshop with ETag
    # ------------------------------------------------------------------ #
    @router.get("/bff/agora/workshops/{workshop_id}")
    def get_workshop(
        workshop_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        session = store.get_session(workshop_id)
        if session is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Workshop not found", workshop_id)
        # Verify ownership
        if session["user_id"] != scope.user_id or session["tenant_id"] != scope.tenant_id:
            _raise_cross_user_forbidden(
                bff_error=bff_error,
                resource="strategy_workshop",
                resource_id=workshop_id,
            )
        lock_version = session.get("lock_version", 1)
        # ETag format: W/"workshop:{id}:v{lock_version}" per contract §B Concurrency
        etag = f'W/"workshop:{workshop_id}:v{lock_version}"'
        response.headers["ETag"] = etag
        return {
            "data": session,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "etag": etag,
            },
        }

    # ------------------------------------------------------------------ #
    # POST /bff/agora/workshops/{workshop_id}/messages — append event
    # ------------------------------------------------------------------ #
    @router.post("/bff/agora/workshops/{workshop_id}/messages", status_code=202)
    def post_workshop_message(
        workshop_id: str,
        body: WorkshopMessageRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id, write=True)
        # If-Match is mandatory: mutations without a precondition are rejected (RFC 6585 §428).
        if if_match is None:
            from models import ErrorCode
            raise bff_error(
                428, ErrorCode.PRECONDITION_FAILED,
                "If-Match header is required for workshop mutations",
                "missing_if_match",
                suggestion="GET the workshop first and supply the returned ETag in If-Match",
            )
        # Idempotency-Key is mandatory for all write operations on this endpoint.
        if idempotency_key is None:
            from models import ErrorCode
            raise bff_error(
                400, ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key header is required",
                "missing_idempotency_key",
                suggestion="Supply a UUID v4 in the Idempotency-Key request header",
            )
        session = store.get_session(workshop_id)
        if session is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Workshop not found", workshop_id)
        if session["user_id"] != scope.user_id or session["tenant_id"] != scope.tenant_id:
            _raise_cross_user_forbidden(
                bff_error=bff_error,
                resource="strategy_workshop",
                resource_id=workshop_id,
            )
        # Reject duplicate keys for the same user+workshop+endpoint.
        if hasattr(store, "check_and_record_idempotency_key"):
            idem_scope = (
                f"{scope.user_id}:{scope.tenant_id}:{workshop_id}"
                f":POST:/bff/agora/workshops/messages"
            )
            if store.check_and_record_idempotency_key(idem_scope, idempotency_key):
                from models import ErrorCode
                raise bff_error(
                    409, ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Duplicate Idempotency-Key", idempotency_key,
                )
        event_id = str(uuid.uuid4())
        private = private_content_store.put(
            tenant_id=scope.tenant_id, owner_user_id=scope.user_id,
            workshop_id=workshop_id, event_id=event_id, content_type="text/plain",
            plaintext=body.content.encode("utf-8"), retention_class="workshop_default",
            idempotency_key=idempotency_key,
        ) if private_content_store is not None else None
        if private is None:
            raise bff_error(503, "PRIVATE_CONTENT_STORE_UNAVAILABLE",
                            "Private content store is not configured", "private_content_store")
        expected_version = _parse_etag_lock_version(if_match, workshop_id)
        # Atomic CAS: compare expected lock_version, append event, bump version —
        # all in one store transaction so concurrent same-ETag writes both cannot succeed.
        event, _new_version = store.append_event_cas(workshop_id, expected_version, {
            "event_id": event_id,
            "workshop_id": workshop_id,
            "actor_type": "operator",
            "event_type": "message",
            "private_content_ref": private.private_content_ref,
            "redacted_summary": "Private workshop message",
            "payload_refs_json": body.attachment_refs or None,
        })
        if event is None:
            from models import ErrorCode
            private_content_store.discard_failed_write(
                private_content_ref=private.private_content_ref,
                tenant_id=scope.tenant_id,
                owner_user_id=scope.user_id,
            )
            # _new_version is the actual current lock_version (or None if not found)
            current_lock_version = _new_version if _new_version is not None else 1
            current_etag = f'W/"workshop:{workshop_id}:v{current_lock_version}"'
            raise bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                "Concurrent modification: ETag mismatch",
                f"If-Match {if_match!r} does not match current ETag {current_etag!r}",
                details_extra={
                    "current_etag": current_etag,
                    "latest_href": f"/bff/agora/workshops/{workshop_id}",
                },
            )
        # Push SSE ack to any open workshop streams (§8.2 audit: trace_id, sequence_no)
        _ws_publish(
            workshop_id,
            "workshop.message.ack",
            {
                "event_id": event["event_id"],
                "sequence_no": event["sequence_no"],
                "trace_id": event.get("trace_id"),
            },
            utc_now_fn=utc_now,
        )
        # Durably admits this conversation's reconstruction job by invoking
        # the one worker path right after the message event is durably
        # committed.  Best-effort: a reconstruction/Registry-draft hiccup
        # must never fail message acceptance.  An explicit POST /reconstruct
        # (same run_reconstruction_worker) recovers a missed or stale round.
        try:
            run_reconstruction_worker(
                store=store,
                canonical=canonical,
                workshop_id=workshop_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                session=store.get_session(workshop_id) or session,
            )
        except Exception:
            pass
        return {
            "data": {"event_id": event["event_id"], "sequence_no": event["sequence_no"]},
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
            },
        }

    # ------------------------------------------------------------------ #
    # GET /bff/agora/workshops/{workshop_id}/events — list events
    # ------------------------------------------------------------------ #
    @router.get("/bff/agora/workshops/{workshop_id}/events")
    def list_workshop_events(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        after_sequence: Optional[int] = Query(default=None, ge=0),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        session = store.get_session(workshop_id)
        if session is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Workshop not found", workshop_id)
        if session["user_id"] != scope.user_id or session["tenant_id"] != scope.tenant_id:
            _raise_cross_user_forbidden(
                bff_error=bff_error,
                resource="strategy_workshop",
                resource_id=workshop_id,
            )
        events = store.list_events(workshop_id, after_sequence=after_sequence)
        return {
            "data": events,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # ------------------------------------------------------------------ #
    # GET /bff/agora/workshops/{workshop_id}/completeness — latest snapshot
    # ------------------------------------------------------------------ #
    @router.get("/bff/agora/workshops/{workshop_id}/completeness")
    def get_workshop_completeness(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        session = store.get_session(workshop_id)
        if session is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Workshop not found", workshop_id)
        if session["user_id"] != scope.user_id or session["tenant_id"] != scope.tenant_id:
            _raise_cross_user_forbidden(
                bff_error=bff_error,
                resource="strategy_workshop",
                resource_id=workshop_id,
            )
        snapshot = store.get_latest_completeness_snapshot(workshop_id)
        return {
            "data": snapshot,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # ------------------------------------------------------------------ #
    # POST /bff/agora/workshops/{workshop_id}/completeness — persist snapshot
    # ------------------------------------------------------------------ #
    @router.post("/bff/agora/workshops/{workshop_id}/completeness", status_code=201)
    def create_workshop_completeness(
        workshop_id: str,
        response: Response,
        body: WorkshopCompletenessSnapshotRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id, write=True)
        session = _scoped_session(workshop_id, scope)
        if if_match is None:
            from models import ErrorCode
            raise bff_error(
                428,
                ErrorCode.PRECONDITION_FAILED,
                "If-Match header is required for workshop completeness updates",
                "missing_if_match",
                suggestion="GET the workshop first and supply the returned ETag in If-Match",
            )
        if idempotency_key is None:
            from models import ErrorCode
            raise bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key header is required",
                "missing_idempotency_key",
                suggestion="Supply a UUID v4 in the Idempotency-Key request header",
            )
        expected_version = _parse_etag_lock_version(if_match, workshop_id)
        current_version = int(session.get("lock_version", 1))
        if expected_version != current_version:
            from models import ErrorCode
            current_etag = f'W/"workshop:{workshop_id}:v{current_version}"'
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Concurrent modification: ETag mismatch",
                f"If-Match {if_match!r} does not match current ETag {current_etag!r}",
                details_extra={
                    "current_etag": current_etag,
                    "latest_href": f"/bff/agora/workshops/{workshop_id}",
                },
            )
        if hasattr(store, "check_and_record_idempotency_key"):
            idem_scope = (
                f"{scope.user_id}:{scope.tenant_id}:{workshop_id}"
                f":POST:/bff/agora/workshops/completeness"
            )
            if store.check_and_record_idempotency_key(idem_scope, idempotency_key):
                from models import ErrorCode
                raise bff_error(
                    409,
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Duplicate Idempotency-Key",
                    idempotency_key,
                )
        if not hasattr(store, "create_completeness_snapshot"):
            from models import ErrorCode
            raise bff_error(
                501,
                ErrorCode.NOT_IMPLEMENTED,
                "Workshop store does not support completeness snapshots",
                "completeness_store_unavailable",
            )

        strategy_version_id = (
            body.strategy_version_id
            or session.get("selected_version_id")
            or session.get("active_strategy_spec_registry_id")
            or session.get("strategy_id")
            or workshop_id
        )
        snapshot = store.create_completeness_snapshot({
            "workshop_id": workshop_id,
            "strategy_version_id": strategy_version_id,
            "state_map_json": body.state_map_json,
            "blocking_items_json": body.blocking_items_json,
            "next_question_json": body.next_question_json,
        })
        events = store.list_events(workshop_id)
        latest = (
            store.get_latest_readiness_assessment(workshop_id)
            if hasattr(store, "get_latest_readiness_assessment")
            else None
        )
        next_assessment_version = int((latest or {}).get("assessment_version", 0)) + 1
        assessment = _build_readiness_assessment(
            session=session,
            events=events,
            snapshot=snapshot,
            assessed_at=utc_now(),
            assessment_version=next_assessment_version,
        )
        stored_assessment = (
            store.create_readiness_assessment(assessment)
            if body.persist_readiness and hasattr(store, "create_readiness_assessment")
            else assessment
        )
        new_lock_version = store.update_session_lock_version(workshop_id)
        new_etag = f'W/"workshop:{workshop_id}:v{new_lock_version}"'
        response.headers["ETag"] = new_etag
        _ws_publish(
            workshop_id,
            "workshop.completeness.updated",
            {
                "snapshot_id": snapshot["snapshot_id"],
                "strategy_version_id": snapshot.get("strategy_version_id"),
            },
            utc_now_fn=utc_now,
        )
        _ws_publish(
            workshop_id,
            "workshop.readiness.updated",
            {
                "assessment_id": stored_assessment["assessment_id"],
                "assessment_version": stored_assessment["assessment_version"],
                "highest_ready_gate": stored_assessment.get("highest_ready_gate"),
            },
            utc_now_fn=utc_now,
        )
        return {
            "data": {
                "snapshot": snapshot,
                "readiness": stored_assessment,
            },
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "etag": new_etag,
            },
        }

    # ------------------------------------------------------------------ #
    # GET /bff/agora/workshops/{workshop_id}/cards — typed live cards
    # ------------------------------------------------------------------ #
    @router.get("/bff/agora/workshops/{workshop_id}/cards")
    def list_workshop_cards(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        after_sequence: Optional[int] = Query(default=None, ge=0),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        session = _scoped_session(workshop_id, scope)
        events = store.list_events(workshop_id)
        snapshot = store.get_latest_completeness_snapshot(workshop_id)
        readiness = _readiness_from_store_or_state(session)
        projected_cards = _build_workshop_cards(
            session=session,
            events=events,
            snapshot=snapshot,
            readiness=readiness,
        )
        stored_cards = (
            store.list_workshop_cards(workshop_id)
            if hasattr(store, "list_workshop_cards")
            else []
        )
        cards = _merge_cards(stored_cards, projected_cards)
        if after_sequence is not None:
            cards = [card for card in cards if int(card.get("sequence_no", 0)) > after_sequence]
        return {
            "data": cards,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "total": len(cards),
            },
        }

    # ------------------------------------------------------------------ #
    # GET /bff/agora/workshops/{workshop_id}/readiness — latest readiness
    # ------------------------------------------------------------------ #
    @router.get("/bff/agora/workshops/{workshop_id}/readiness")
    def get_workshop_readiness(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        session = _scoped_session(workshop_id, scope)
        readiness = _readiness_from_store_or_state(session)
        return {
            "data": readiness,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # ------------------------------------------------------------------ #
    # POST /bff/agora/workshops/{workshop_id}/readiness/reassess
    # ------------------------------------------------------------------ #
    @router.post("/bff/agora/workshops/{workshop_id}/readiness/reassess", status_code=202)
    def reassess_workshop_readiness(
        workshop_id: str,
        response: Response,
        body: Optional[WorkshopReadinessReassessRequest] = Body(default=None),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        del body
        scope = _scope(authorization, x_tenant_id, write=True)
        session = _scoped_session(workshop_id, scope)
        if if_match is None:
            from models import ErrorCode
            raise bff_error(
                428,
                ErrorCode.PRECONDITION_FAILED,
                "If-Match header is required for workshop readiness reassessment",
                "missing_if_match",
                suggestion="GET the workshop first and supply the returned ETag in If-Match",
            )
        if idempotency_key is None:
            from models import ErrorCode
            raise bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key header is required",
                "missing_idempotency_key",
                suggestion="Supply a UUID v4 in the Idempotency-Key request header",
            )
        expected_version = _parse_etag_lock_version(if_match, workshop_id)
        current_version = int(session.get("lock_version", 1))
        if expected_version != current_version:
            from models import ErrorCode
            current_etag = f'W/"workshop:{workshop_id}:v{current_version}"'
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Concurrent modification: ETag mismatch",
                f"If-Match {if_match!r} does not match current ETag {current_etag!r}",
                details_extra={
                    "current_etag": current_etag,
                    "latest_href": f"/bff/agora/workshops/{workshop_id}",
                },
            )
        if hasattr(store, "check_and_record_idempotency_key"):
            idem_scope = (
                f"{scope.user_id}:{scope.tenant_id}:{workshop_id}"
                f":POST:/bff/agora/workshops/readiness/reassess"
            )
            if store.check_and_record_idempotency_key(idem_scope, idempotency_key):
                from models import ErrorCode
                raise bff_error(
                    409,
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Duplicate Idempotency-Key",
                    idempotency_key,
                )

        events = store.list_events(workshop_id)
        snapshot = store.get_latest_completeness_snapshot(workshop_id)
        latest = (
            store.get_latest_readiness_assessment(workshop_id)
            if hasattr(store, "get_latest_readiness_assessment")
            else None
        )
        next_assessment_version = int((latest or {}).get("assessment_version", 0)) + 1
        assessment = _build_readiness_assessment(
            session=session,
            events=events,
            snapshot=snapshot,
            assessed_at=utc_now(),
            assessment_version=next_assessment_version,
        )
        stored_assessment = (
            store.create_readiness_assessment(assessment)
            if hasattr(store, "create_readiness_assessment")
            else assessment
        )
        new_lock_version = store.update_session_lock_version(workshop_id)
        new_etag = f'W/"workshop:{workshop_id}:v{new_lock_version}"'
        response.headers["ETag"] = new_etag
        _ws_publish(
            workshop_id,
            "workshop.readiness.updated",
            {
                "assessment_id": stored_assessment["assessment_id"],
                "assessment_version": stored_assessment["assessment_version"],
                "highest_ready_gate": stored_assessment.get("highest_ready_gate"),
            },
            utc_now_fn=utc_now,
        )
        return {
            "data": stored_assessment,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "etag": new_etag,
            },
        }

    # ------------------------------------------------------------------ #
    # POST /bff/agora/workshops/{workshop_id}/reconstruct
    # ------------------------------------------------------------------ #
    @router.post("/bff/agora/workshops/{workshop_id}/reconstruct")
    def reconstruct_workshop_strategy(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        # Single worker path (AGORA-WORKSHOP-CORE-20260813 disposition): this
        # endpoint and the message-post durable trigger both call
        # run_reconstruction_worker, so replay/staleness/crash-restart
        # semantics live in one place rather than being duplicated per caller.
        scope = _scope(authorization, x_tenant_id)
        session = _scoped_session(workshop_id, scope)
        outcome = run_reconstruction_worker(
            store=store,
            canonical=canonical,
            workshop_id=workshop_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            session=session,
        )
        return {
            "data": outcome["result"],
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "job_status": outcome["job_status"],
                "registry_draft_ref": outcome.get("registry_draft_ref"),
            },
        }

    return router
