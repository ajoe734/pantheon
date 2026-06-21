"""Agora strategy-workshop router — agora.workshop.v1.

Implements the /bff/agora/workshops/* route family per the AG-BE-SW-001
contract (docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/
03_servant_and_workshop_contracts.md §B).

Routes implemented in this module:
  GET  /bff/agora/workshops                     — list user-scoped sessions
  POST /bff/agora/workshops                     — create workshop session
  GET  /bff/agora/workshops/{workshop_id}       — get session (ETag)
  POST /bff/agora/workshops/{workshop_id}/messages   — append event (private_content_ref only)
  GET  /bff/agora/workshops/{workshop_id}/events     — list events
  GET  /bff/agora/workshops/{workshop_id}/completeness — latest completeness snapshot

Routes still in main.py (migration pending — see router stub comment):
  GET  /bff/agora/training-examples
  POST /bff/agora/training-examples
  ...  (all the old committee/evaluation/persona-lab routes)

Routes deferred to later AG-BE-SW-* tasks (registered as 501 stubs):
  GET/POST /bff/agora/workshops/{id}/versions
  POST     /bff/agora/workshops/{id}/versions/{ver}/select
  POST     /bff/agora/workshops/{id}/research-runs
  POST     /bff/agora/workshops/{id}/consultations
  POST     /bff/agora/workshops/{id}/conclude
  GET      /bff/agora/workshops/{id}/stream
"""
from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from .store import make_workshop_store


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #

def _parse_etag_lock_version(if_match: str, workshop_id: str) -> int:
    """Return the lock_version encoded in the ETag, or 0 if the header is malformed.

    Expected format: W/"workshop:{workshop_id}:v{N}"
    Returns 0 so a malformed header is guaranteed to conflict (lock_version >= 1).
    """
    prefix = f'W/"workshop:{workshop_id}:v'
    if if_match.startswith(prefix) and if_match.endswith('"'):
        try:
            return int(if_match[len(prefix):-1])
        except ValueError:
            pass
    return 0


# --------------------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------------------- #

class WorkshopCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    initial_message: str = Field(min_length=1)
    title: Optional[str] = None
    strategy_spec_ref: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class WorkshopMessageRequest(BaseModel):
    model_config = {"extra": "forbid"}

    content: str = Field(min_length=1)
    attachment_refs: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Router factory
# --------------------------------------------------------------------------- #

def create_strategy_workshop_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    workshop_store: Any = None,
) -> APIRouter:
    """Build and return the strategy-workshop APIRouter.

    ``workshop_store`` may be injected (e.g. a MemoryWorkshopStore in tests).
    When omitted the store is constructed from AGORA_WORKSHOP_STORE_BACKEND env.
    """
    store = workshop_store if workshop_store is not None else make_workshop_store()
    router = APIRouter(tags=["agora-workshop"])

    # Lazy import to avoid circular import at module load time
    def _scope(authorization: Optional[str], x_tenant_id: Optional[str] = None) -> Any:
        from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
        from ..models import AgoraErrorCode

        identity = extract_identity(authorization)
        require_read_role(identity)
        try:
            return resolve_agora_user_scope(
                identity,
                utc_now=utc_now,
                requested_tenant_id=x_tenant_id,
            )
        except AgoraScopeResolutionError as exc:
            from models import ErrorCode  # BFF top-level models
            code = ErrorCode.AUTH_REQUIRED if exc.status_code == 401 else ErrorCode.FORBIDDEN
            raise bff_error(
                exc.status_code,
                code,
                exc.message,
                exc.reason,
                precondition_failed="agora_user_scope",
                details_extra=exc.details,
            )

    def _not_implemented(route: str) -> None:
        from models import ErrorCode
        raise bff_error(
            501,
            ErrorCode.NOT_IMPLEMENTED,
            f"{route} is not yet implemented",
            "stub: see later AG-BE-SW-* tasks",
        )

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
        scope = _scope(authorization, x_tenant_id)
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
        if hasattr(store, "check_and_record_idempotency_key"):
            idem_scope = f"{scope.user_id}:{scope.tenant_id}:POST:/bff/agora/workshops"
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
            "strategy_id": body.strategy_spec_ref,
            "active_strategy_spec_registry_id": body.strategy_spec_ref,
            "status": "open",
        })
        # Privacy rule: raw initial_message must NOT appear in the event payload.
        # In production this content goes to the encrypted private-content store;
        # here we generate a stub ref and leave redacted_summary empty.
        initial_event_id = str(uuid.uuid4())
        store.create_event({
            "event_id": initial_event_id,
            "workshop_id": workshop_id,
            "actor_type": "operator",
            "event_type": "message",
            "private_content_ref": f"priv-content-stub://{initial_event_id}",
            "redacted_summary": None,
        })
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
            from models import ErrorCode
            raise bff_error(403, ErrorCode.FORBIDDEN, "Workshop not owned by caller", workshop_id)
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
        scope = _scope(authorization, x_tenant_id)
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
            from models import ErrorCode
            raise bff_error(403, ErrorCode.FORBIDDEN, "Workshop not owned by caller", workshop_id)
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
        # Privacy rule: raw message content must NOT appear in the event payload.
        # In production the content is handed off to the encrypted private-content store;
        # here we generate a stub ref and leave redacted_summary empty.
        event_id = str(uuid.uuid4())
        expected_version = _parse_etag_lock_version(if_match, workshop_id)
        # Atomic CAS: compare expected lock_version, append event, bump version —
        # all in one store transaction so concurrent same-ETag writes both cannot succeed.
        event, _new_version = store.append_event_cas(workshop_id, expected_version, {
            "event_id": event_id,
            "workshop_id": workshop_id,
            "actor_type": "operator",
            "event_type": "message",
            "private_content_ref": f"priv-content-stub://{event_id}",
            "redacted_summary": None,
            "payload_refs_json": body.attachment_refs or None,
        })
        if event is None:
            from models import ErrorCode
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
            from models import ErrorCode
            raise bff_error(403, ErrorCode.FORBIDDEN, "Workshop not owned by caller", workshop_id)
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
            from models import ErrorCode
            raise bff_error(403, ErrorCode.FORBIDDEN, "Workshop not owned by caller", workshop_id)
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
    # Deferred stubs (later AG-BE-SW-* tasks)
    # ------------------------------------------------------------------ #

    @router.get("/bff/agora/workshops/{workshop_id}/versions")
    def list_workshop_versions(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _scope(authorization)
        _not_implemented("GET /bff/agora/workshops/{workshop_id}/versions")
        return {}  # unreachable

    @router.post("/bff/agora/workshops/{workshop_id}/versions", status_code=201)
    def create_workshop_version(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _scope(authorization)
        _not_implemented("POST /bff/agora/workshops/{workshop_id}/versions")
        return {}

    @router.post("/bff/agora/workshops/{workshop_id}/versions/{version_id}/select")
    def select_workshop_version(
        workshop_id: str,
        version_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _scope(authorization)
        _not_implemented("POST /bff/agora/workshops/{workshop_id}/versions/{version_id}/select")
        return {}

    @router.post("/bff/agora/workshops/{workshop_id}/research-runs", status_code=202)
    def create_workshop_research_run(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _scope(authorization)
        _not_implemented("POST /bff/agora/workshops/{workshop_id}/research-runs")
        return {}

    @router.post("/bff/agora/workshops/{workshop_id}/consultations", status_code=202)
    def create_workshop_consultation(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _scope(authorization)
        _not_implemented("POST /bff/agora/workshops/{workshop_id}/consultations")
        return {}

    @router.post("/bff/agora/workshops/{workshop_id}/conclude")
    def conclude_workshop(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _scope(authorization)
        _not_implemented("POST /bff/agora/workshops/{workshop_id}/conclude")
        return {}

    @router.get("/bff/agora/workshops/{workshop_id}/stream")
    def stream_workshop(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _scope(authorization)
        _not_implemented("GET /bff/agora/workshops/{workshop_id}/stream")
        return {}

    return router
