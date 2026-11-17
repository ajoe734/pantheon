"""Management read routes for outliers, attribution records and shadow comparisons."""
from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from services.control_plane.bff.meetings.client import MeetingsOwnerClient
from services.control_plane.bff.meetings.router import _caller_context, _default_bff_error
from services.control_plane.bff.models import ErrorCode, utc_now as default_utc_now

from .client import (
    DIRECTIONS,
    SUBJECT_KINDS,
    AttributionOwnerClient,
    AttributionOwnerNotFoundError,
    AttributionOwnerUnavailableError,
    AttributionOwnerValidationError,
    shape_record_detail,
    shape_record_list,
    shape_shadow,
    unavailable_attribution_envelope,
)

LockReader = Callable[[str, str, str, list], Optional[Mapping[str, Any]]]


def create_attribution_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_operator_role: Optional[Callable[..., None]] = None,
    bff_error: Optional[Callable[..., HTTPException]] = None,
    utc_now: Optional[Callable[[], str]] = None,
    client_factory: Optional[Callable[[str, str], AttributionOwnerClient]] = None,
    lock_reader: Optional[LockReader] = None,
    meeting_reader: Optional[Callable[[str, str, str, str], Mapping[str, Any]]] = None,
) -> APIRouter:
    """``lock_reader(decision_id, tenant_id, user_id, roles)`` reads the Agora owner's decision lock
    (None when unknown or not readable by the caller; raises when the owner is unavailable)."""
    router = APIRouter(tags=["attribution"])
    err_fn = bff_error or _default_bff_error
    now_fn = utc_now or default_utc_now
    make_client = client_factory or (lambda t, a: AttributionOwnerClient(t, a))

    def _read_meeting(meeting_id: str, tenant_id: str, actor_id: str) -> Mapping[str, Any]:
        if meeting_reader is not None:
            return meeting_reader(meeting_id, tenant_id, actor_id, "")
        return MeetingsOwnerClient(tenant_id, actor_id).get_meeting(meeting_id)

    def _ctx(request: Request):
        identity, tenant_id, is_operator, caller = _caller_context(
            request,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=err_fn,
            utc_now=now_fn,
            write=False,
        )
        actor = getattr(identity, "operator_id", None) or caller or "viewer"
        roles = [str(r) for r in (getattr(identity, "roles", None) or [])] or ["trader"]
        return tenant_id, is_operator, caller, actor, roles

    def _bad(reason: str, status: int = 422) -> HTTPException:
        return err_fn(status, ErrorCode.VALIDATION_FAILED, f"Invalid attribution query: {reason}", reason)

    def _list(
        request: Request,
        since: Optional[str],
        subject_kind: Optional[str],
        direction: Optional[str],
        cursor: Optional[str],
        limit: int,
    ) -> Dict[str, Any]:
        tenant_id, is_operator, caller, actor, _roles = _ctx(request)
        if subject_kind and subject_kind not in SUBJECT_KINDS:
            raise _bad("invalid_subject_kind")
        if direction and direction not in DIRECTIONS:
            raise _bad("invalid_direction")
        snap = now_fn()
        try:
            payload = make_client(tenant_id, actor).list_records(
                since=since, subject_kind=subject_kind, direction=direction, cursor=cursor, limit=limit
            )
            return shape_record_list(
                payload, is_operator=is_operator, caller_user_id=caller, snapshot_at=snap, limit=limit
            )
        except AttributionOwnerUnavailableError as exc:
            return unavailable_attribution_envelope("attribution_records", exc.reason, snapshot_at=snap)
        except AttributionOwnerValidationError as exc:
            raise _bad(exc.reason, exc.status_code) from exc
        except AttributionOwnerNotFoundError as exc:
            raise err_fn(404, ErrorCode.RESOURCE_NOT_FOUND, "Attribution route not found", exc.reason) from exc

    @router.get("/bff/management/attribution/records")
    def list_records(
        request: Request,
        since: Optional[str] = Query(default=None),
        subject_kind: Optional[str] = Query(default=None),
        direction: Optional[str] = Query(default=None),
        cursor: Optional[str] = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> Dict[str, Any]:
        return _list(request, since, subject_kind, direction, cursor, limit)

    @router.get("/bff/management/attribution/outliers")
    def list_outliers(
        request: Request,
        since: Optional[str] = Query(default=None),
        subject_kind: Optional[str] = Query(default=None),
        direction: Optional[str] = Query(default=None),
        cursor: Optional[str] = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> Dict[str, Any]:
        return _list(request, since, subject_kind, direction, cursor, limit)

    @router.get("/bff/management/attribution/records/{record_id}")
    def get_record(record_id: str, request: Request) -> Dict[str, Any]:
        tenant_id, is_operator, caller, actor, _roles = _ctx(request)
        snap = now_fn()

        def _nf(reason: str = "attribution_not_found") -> HTTPException:
            return err_fn(404, ErrorCode.RESOURCE_NOT_FOUND, f"Attribution record {record_id!r} was not found", reason)

        try:
            record = make_client(tenant_id, actor).get_record(record_id)
        except AttributionOwnerNotFoundError as exc:
            raise _nf(exc.reason) from exc
        except AttributionOwnerUnavailableError as exc:
            return unavailable_attribution_envelope("attribution_record", exc.reason, snapshot_at=snap)
        except AttributionOwnerValidationError as exc:
            raise _bad(exc.reason, exc.status_code) from exc
        meeting: Optional[Mapping[str, Any]] = None
        meeting_reason: Optional[str] = None
        meeting_id = str((record or {}).get("meeting_id") or "").strip() if isinstance(record, Mapping) else ""
        if meeting_id:
            try:
                meeting = _read_meeting(meeting_id, tenant_id, actor)
            except Exception as exc:  # meeting link is best effort; the record stays readable
                meeting_reason = str(getattr(exc, "reason", "") or "meeting_owner_unavailable")
        try:
            shaped = shape_record_detail(
                record,
                is_operator=is_operator,
                caller_user_id=caller,
                snapshot_at=snap,
                meeting=meeting if isinstance(meeting, Mapping) else None,
                meeting_unavailable_reason=meeting_reason,
            )
        except AttributionOwnerUnavailableError as exc:
            return unavailable_attribution_envelope("attribution_record", exc.reason, snapshot_at=snap)
        if shaped is None:
            raise _nf()
        return shaped

    @router.get("/bff/management/attribution/shadow/{decision_id}")
    def get_shadow(decision_id: str, request: Request) -> Dict[str, Any]:
        tenant_id, is_operator, caller, actor, roles = _ctx(request)
        snap = now_fn()
        attribution: Optional[Mapping[str, Any]] = None
        attr_reason: Optional[str] = None
        attr_missing = False
        try:
            attribution = make_client(tenant_id, actor).get_shadow(decision_id)
            if not isinstance(attribution, Mapping):
                attribution, attr_reason = None, "attribution_owner_invalid_payload"
        except AttributionOwnerNotFoundError:
            attr_missing = True
            attr_reason = "shadow_not_settled_or_unknown"
        except AttributionOwnerUnavailableError as exc:
            attr_reason = exc.reason
        except AttributionOwnerValidationError as exc:
            raise _bad(exc.reason, exc.status_code) from exc
        lock: Optional[Mapping[str, Any]] = None
        lock_reason: Optional[str] = None
        if lock_reader is None:
            lock_reason = "agora_owner_unconfigured"
        else:
            try:
                lock = lock_reader(decision_id, tenant_id, caller, roles)
                if lock is None:
                    lock_reason = "agora_lock_not_found"
            except Exception as exc:
                lock_reason = str(getattr(exc, "reason", "") or "agora_owner_unavailable")
        # Unknown decision: no lock recorded and attribution does not know it -> 404.
        # A trader must prove ownership through the lock, so no lock is 404 for him.
        if lock_reason == "agora_lock_not_found" and (attr_missing or not is_operator):
            raise err_fn(404, ErrorCode.RESOURCE_NOT_FOUND, f"Decision {decision_id!r} was not found", "decision_not_found")
        return shape_shadow(
            decision_id,
            attribution=attribution,
            attribution_unavailable_reason=attr_reason,
            lock=lock,
            lock_unavailable_reason=lock_reason,
            is_operator=is_operator,
            snapshot_at=snap,
        )

    return router
