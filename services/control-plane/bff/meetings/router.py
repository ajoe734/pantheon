"""Management Meetings Center, Meeting Detail, and Operator Convene BFF router."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from services.control_plane.bff.agora.identity.scope import (
    AgoraScopeResolutionError,
    resolve_agora_user_scope,
)
from services.control_plane.bff.models import ErrorCode, utc_now as default_utc_now
from services.control_plane.bff.core.display_names import DisplayNameResolver, current_resolver

from .client import (
    GATE_PANEL_SESSION_CLASS,
    MEETING_OPERATOR_REVIEW_NOTE_MAX_CHARS,
    MeetingsOwnerClient,
    MeetingsOwnerNotFoundError,
    MeetingsOwnerUnavailableError,
    MeetingsOwnerValidationError,
    gate_panel_matches,
    shape_gate_panel_detail,
    shape_gate_panel_item,
    shape_meeting_catalogue,
    shape_meeting_detail,
    shape_meeting_list,
    shape_meeting_proposals,
    unavailable_meetings_envelope,
)

_OPERATOR_ROLES = frozenset({"operator", "approver", "admin"})
_TRADER_READ_ROLES = frozenset({"trader", "agora_user", "agora_trader"})


class ConvenedByInput(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["operator", "persona", "agora_user"] = "operator"
    id: Optional[str] = Field(default=None, min_length=1)


class OperatorConveneMeetingRequest(BaseModel):
    model_config = {"extra": "forbid"}
    meeting_type: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    convened_by: Optional[ConvenedByInput] = None
    subject_ref: Optional[Any] = None
    participants: Optional[List[str]] = None
    evidence_refs: Optional[List[Any]] = None
    attribution_record_id: Optional[str] = Field(default=None, min_length=1)


class OperatorReviewMeetingRequest(BaseModel):
    model_config = {"extra": "forbid"}
    note: Optional[str] = None
    rationale: Optional[str] = None


def _default_bff_error(
    status_code: int,
    code: Any,
    message: str,
    reason: Optional[str] = None,
    **kwargs: Any,
) -> HTTPException:
    code_str = code.value if hasattr(code, "value") else str(code)
    return HTTPException(
        status_code=status_code,
        detail={
            "status": "error",
            "reason": reason or message,
            "error": {
                "code": code_str,
                "message": message,
                "reason": reason or message,
                "status_code": status_code,
            },
        },
    )


def _caller_context(
    request: Request,
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_operator_role: Optional[Callable[..., None]] = None,
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    write: bool = False,
) -> tuple[Any, str, bool, str]:
    auth_header = request.headers.get("Authorization")
    session_cookie = request.cookies.get("pantheon_session")
    try:
        identity = extract_identity(auth_header, session_cookie=session_cookie)
    except TypeError:
        identity = extract_identity(auth_header)

    roles = {str(r).strip().lower() for r in (getattr(identity, "roles", None) or []) if r}
    is_operator = bool(roles.intersection(_OPERATOR_ROLES))

    if write:
        if require_operator_role is not None:
            require_operator_role(identity)
        if not is_operator:
            raise bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "Operator role is required to convene a meeting",
                "insufficient_role",
            )
    else:
        if not roles.intersection(_TRADER_READ_ROLES):
            require_read_role(identity)

    try:
        scope = resolve_agora_user_scope(
            identity,
            utc_now=utc_now,
            requested_tenant_id=request.headers.get("X-Tenant-Id"),
        )
    except AgoraScopeResolutionError as exc:
        raise bff_error(exc.status_code, ErrorCode.FORBIDDEN, exc.message, exc.reason) from exc

    claims = getattr(identity, "claims", None)
    if not isinstance(claims, dict):
        claims = {}
    caller_user_id = str(
        claims.get("agora_user_id")
        or claims.get("trader_id")
        or claims.get("user_id")
        or claims.get("sub")
        or getattr(identity, "operator_id", "")
        or scope.user_id
        or ""
    ).strip()
    return identity, scope.tenant_id, is_operator, caller_user_id


def _annotate_meeting_names(meeting: Any, names: DisplayNameResolver) -> None:
    """Add persona display names to a shaped meeting row or detail, in place."""
    if not isinstance(meeting, dict):
        return
    names.annotate(
        meeting,
        persona_keys=("chair_persona_id", "persona_id", "sponsor_persona_id", "subject_persona_id"),
        strategy_keys=("strategy_id",),
    )
    for participant in meeting.get("participants") or []:
        names.annotate(participant, persona_keys=("persona_id",), strategy_keys=())
    for rnd in meeting.get("rounds") or []:
        if isinstance(rnd, dict):
            for pos in rnd.get("positions") or []:
                names.annotate(pos, persona_keys=("persona_id",), strategy_keys=())


def _annotate_meeting_envelope(payload: Any) -> Any:
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return payload
    names = current_resolver()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    if isinstance(payload.get("items"), list):  # list envelope; data.items shares the same row objects
        for row in payload["items"]:
            _annotate_meeting_names(row, names)
        return payload
    _annotate_meeting_names(payload, names)
    if data is not None:
        _annotate_meeting_names(data, names)
    return payload


_GATE_PANEL_CLOSED = frozenset({"published", "completed", "closed"})


def _merge_gate_panels(envelope: Dict[str, Any], panels: List[Dict[str, Any]], surface: Dict[str, Any]) -> Dict[str, Any]:
    """Add persona gate panels to a shaped meetings list; every row newest first."""
    if not isinstance(envelope, dict) or envelope.get("status") != "ok":
        return envelope
    rows = [r for r in envelope.get("items") or [] if isinstance(r, dict)] + panels
    rows.sort(key=lambda r: (str(r.get("created_at") or ""), str(r.get("meeting_id") or "")), reverse=True)
    envelope["items"] = rows
    envelope["empty"] = not rows
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else None
    if data is not None:
        data["items"] = rows
        data["empty"] = not rows
        summary = data.get("summary") if isinstance(data.get("summary"), dict) else None
        if summary is not None:
            summary["total_items"] = int(summary.get("total_items") or 0) + len(panels)
            summary["returned_items"] = len(rows)
            summary["gate_panel_count"] = len(panels)
            closed = sum(1 for p in panels if str(p.get("status") or "").lower() in _GATE_PANEL_CLOSED)
            summary["closed_count"] = int(summary.get("closed_count") or 0) + closed
            summary["open_count"] = int(summary.get("open_count") or 0) + len(panels) - closed
    page_info = envelope.get("page_info") if isinstance(envelope.get("page_info"), dict) else None
    if page_info is not None:
        page_info["total"] = int(page_info.get("total") or 0) + len(panels)
        page_info["returned"] = len(rows)
    meta = envelope.get("meta") if isinstance(envelope.get("meta"), dict) else None
    if meta is not None and isinstance(meta.get("surfaces"), dict):
        meta["surfaces"]["gate_panels"] = surface
    return envelope


def create_meetings_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_operator_role: Optional[Callable[..., None]] = None,
    bff_error: Optional[Callable[..., HTTPException]] = None,
    utc_now: Optional[Callable[[], str]] = None,
    client_factory: Optional[Callable[[str, str], MeetingsOwnerClient]] = None,
) -> APIRouter:
    router = APIRouter(tags=["meetings"])
    err_fn = bff_error or _default_bff_error
    now_fn = utc_now or default_utc_now
    make_client = client_factory or (lambda tenant_id, actor_id: MeetingsOwnerClient(tenant_id, actor_id))

    def _handle_list_meetings(
        request: Request,
        *,
        status: Optional[str],
        type_: Optional[str],
        meeting_type: Optional[str],
        persona_id: Optional[str],
        persona: Optional[str],
        cursor: Optional[str],
        page_token: Optional[str],
        limit: int,
        page_size: Optional[int],
        include_failed: bool = False,
    ) -> Dict[str, Any]:
        identity, tenant_id, is_operator, caller_user_id = _caller_context(
            request,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=err_fn,
            utc_now=now_fn,
            write=False,
        )
        snap = now_fn()
        eff_type = type_ or meeting_type
        eff_persona = persona_id or persona
        eff_cursor = cursor if cursor is not None else page_token
        eff_limit = page_size if page_size is not None else limit
        actor_id = getattr(identity, "operator_id", None) or caller_user_id or "viewer"
        try:
            owner_payload = make_client(tenant_id, actor_id).list_meetings(
                status=status,
                meeting_type=eff_type,
                persona_id=eff_persona,
                cursor=eff_cursor,
                limit=eff_limit,
                include_failed=include_failed,
            )
        except MeetingsOwnerUnavailableError as exc:
            return unavailable_meetings_envelope("meetings", exc.reason, snapshot_at=snap)
        shaped = shape_meeting_list(
            owner_payload,
            is_operator=is_operator,
            caller_user_id=caller_user_id,
            snapshot_at=snap,
            limit=eff_limit,
        )
        # Persona gate panels (moved out of the Agora committee on 2026-11-26) are
        # listed on the first page beside deliberative meetings.
        panels: List[Dict[str, Any]] = []
        surface: Dict[str, Any] = {"status": "ok", "source": "consultation", "snapshot_at": snap}
        panel_client = make_client(tenant_id, actor_id) if not eff_cursor else None
        if panel_client is not None and not hasattr(panel_client, "list_gate_panels"):
            # An injected owner adapter without gate-panel reads: say so, never guess.
            surface = {"status": "unavailable", "source": "consultation", "snapshot_at": snap,
                       "reason": "gate_panel_reader_unavailable"}
        elif panel_client is not None:
            try:
                panels = _read_gate_panels(
                    panel_client,
                    status=status,
                    meeting_type=eff_type,
                    persona_id=eff_persona,
                )
            except (MeetingsOwnerUnavailableError, MeetingsOwnerNotFoundError) as exc:
                surface = {"status": "unavailable", "source": "consultation", "snapshot_at": snap,
                           "reason": getattr(exc, "reason", "consultation_owner_unavailable")}
        return _annotate_meeting_envelope(_merge_gate_panels(shaped, panels, surface))

    def _read_gate_panels(
        client: MeetingsOwnerClient,
        *,
        status: Optional[str],
        meeting_type: Optional[str],
        persona_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        if meeting_type and meeting_type.strip().lower() not in {GATE_PANEL_SESSION_CLASS, "persona_panel"}:
            return []
        names = current_resolver()
        rows: List[Dict[str, Any]] = []
        for request_row in client.list_gate_panels():
            transcript = client.get_gate_panel_transcript(str(request_row["request_id"]))
            item = shape_gate_panel_item(request_row, transcript, names=names)
            if gate_panel_matches(item, status=status, meeting_type=meeting_type, persona_id=persona_id):
                rows.append(item)
        return rows

    def _handle_list_proposals(
        request: Request,
        *,
        status: Optional[str],
        persona_id: Optional[str],
        meeting_id: Optional[str],
    ) -> Dict[str, Any]:
        identity, tenant_id, _is_operator, caller_user_id = _caller_context(
            request,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=err_fn,
            utc_now=now_fn,
            write=False,
        )
        snap = now_fn()
        actor_id = getattr(identity, "operator_id", None) or caller_user_id or "viewer"
        try:
            owner_payload = make_client(tenant_id, actor_id).list_proposals(
                status=status,
                persona_id=persona_id,
                meeting_id=meeting_id,
            )
        except MeetingsOwnerUnavailableError as exc:
            return unavailable_meetings_envelope("meeting_proposals", exc.reason, snapshot_at=snap)
        return shape_meeting_proposals(owner_payload, snapshot_at=snap)

    def _handle_get_catalogue(
        request: Request,
        *,
        persona_id: Optional[str],
    ) -> Dict[str, Any]:
        identity, tenant_id, _is_operator, caller_user_id = _caller_context(
            request,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=err_fn,
            utc_now=now_fn,
            write=False,
        )
        snap = now_fn()
        actor_id = getattr(identity, "operator_id", None) or caller_user_id or "viewer"
        try:
            owner_payload = make_client(tenant_id, actor_id).get_catalogue()
        except MeetingsOwnerUnavailableError as exc:
            return unavailable_meetings_envelope("meeting_catalogue", exc.reason, snapshot_at=snap)
        return shape_meeting_catalogue(owner_payload, snapshot_at=snap, persona_id=persona_id)

    def _handle_get_meeting_detail(
        meeting_id: str,
        request: Request,
    ) -> Dict[str, Any]:
        identity, tenant_id, is_operator, caller_user_id = _caller_context(
            request,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=err_fn,
            utc_now=now_fn,
            write=False,
        )
        snap = now_fn()
        actor_id = getattr(identity, "operator_id", None) or caller_user_id or "viewer"
        owner_client = make_client(tenant_id, actor_id)
        try:
            raw_detail = owner_client.get_meeting(meeting_id)
        except MeetingsOwnerNotFoundError as exc:
            # Not a deliberative meeting: a persona gate panel is served here too.
            try:
                return _annotate_meeting_envelope(_gate_panel_detail(owner_client, meeting_id, tenant_id, snap))
            except MeetingsOwnerNotFoundError:
                pass
            except MeetingsOwnerUnavailableError as panel_exc:
                return unavailable_meetings_envelope("meeting_detail", panel_exc.reason, snapshot_at=snap)
            raise err_fn(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Meeting {meeting_id!r} was not found",
                exc.reason,
            ) from exc
        except MeetingsOwnerUnavailableError as exc:
            if exc.status_code in (401, 403):
                raise err_fn(exc.status_code, ErrorCode.FORBIDDEN,
                             "Meeting owner denied this read", exc.reason) from exc
            return unavailable_meetings_envelope("meeting_detail", exc.reason, snapshot_at=snap)
        except MeetingsOwnerValidationError as exc:
            raise err_fn(exc.status_code, ErrorCode.FORBIDDEN if exc.status_code == 403 else ErrorCode.VALIDATION_FAILED,
                         "Meeting owner rejected this read", exc.reason) from exc
        return _annotate_meeting_envelope(shape_meeting_detail(
            raw_detail,
            is_operator=is_operator,
            caller_user_id=caller_user_id,
            snapshot_at=snap,
            tenant_id=tenant_id,
        ))

    def _gate_panel_detail(
        client: MeetingsOwnerClient, panel_id: str, tenant_id: str, snap: str
    ) -> Dict[str, Any]:
        if not hasattr(client, "get_gate_panel"):
            raise MeetingsOwnerNotFoundError(panel_id)
        request_row = client.get_gate_panel(panel_id)
        transcript = client.get_gate_panel_transcript(panel_id)
        memos = client.list_gate_panel_memos(panel_id)
        return shape_gate_panel_detail(
            request_row, transcript, memos, tenant_id=tenant_id, snapshot_at=snap, names=current_resolver()
        )

    def _handle_convene_meeting(
        body: OperatorConveneMeetingRequest,
        request: Request,
        idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        identity, tenant_id, _is_operator, caller_user_id = _caller_context(
            request,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=err_fn,
            utc_now=now_fn,
            write=True,
        )
        clean_idem = str(idempotency_key or "").strip()
        if not clean_idem:
            raise err_fn(
                400,
                ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key header is required",
                "missing_idempotency_key",
            )
        operator_id = str(getattr(identity, "operator_id", None) or caller_user_id or "").strip()
        if body.convened_by is not None:
            if body.convened_by.kind != "operator":
                raise err_fn(
                    403,
                    ErrorCode.FORBIDDEN,
                    "Operator convene route requires convened_by.kind='operator'",
                    "operator_mismatch",
                )
            if body.convened_by.id is not None and body.convened_by.id != operator_id:
                raise err_fn(
                    403,
                    ErrorCode.FORBIDDEN,
                    "Convened_by operator id does not match authenticated operator",
                    "operator_mismatch",
                )
        convened_by = {"kind": "operator", "id": operator_id}
        snap = now_fn()
        try:
            created = make_client(tenant_id, operator_id).convene_meeting(
                meeting_type=body.meeting_type,
                topic=body.topic,
                convened_by=convened_by,
                idempotency_key=clean_idem,
                subject_ref=body.subject_ref,
                participants=body.participants,
                evidence_refs=body.evidence_refs,
                attribution_record_id=body.attribution_record_id,
            )
        except MeetingsOwnerValidationError as exc:
            raise err_fn(
                exc.status_code,
                ErrorCode.VALIDATION_FAILED if exc.status_code != 409 else ErrorCode.RESOURCE_CONFLICT,
                f"Consultation owner rejected convene request: {exc.reason}",
                exc.reason,
            ) from exc
        except MeetingsOwnerUnavailableError as exc:
            raise HTTPException(
                status_code=503,
                detail=unavailable_meetings_envelope("meetings", exc.reason, snapshot_at=snap),
            ) from exc

        meeting_id = str(created.get("meeting_id") or "")
        meeting_status = str(created.get("status") or "convened")
        result_data = {
            **created,
            "meeting_id": meeting_id,
            "status": meeting_status,
            "meeting_type": body.meeting_type,
            "topic": body.topic,
            "convened_by": convened_by,
            "idempotency_key": clean_idem,
        }
        return {
            "status": meeting_status,
            "owner": "consultation",
            "meeting_id": meeting_id,
            "meeting_status": meeting_status,
            "data": result_data,
            "meta": {
                "snapshot_at": snap,
                "status": "ok",
                "source": "consultation",
                "idempotency_key": clean_idem,
                "capability": "management.meetings.convene.v1",
            },
        }

    def _handle_review_meeting(
        meeting_id: str,
        body: OperatorReviewMeetingRequest,
        request: Request,
        idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        identity, tenant_id, is_operator, caller_user_id = _caller_context(
            request,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=err_fn,
            utc_now=now_fn,
            write=True,
        )
        raw_note = body.note if body.note is not None else (body.rationale or "")
        clean_note = str(raw_note).strip()
        if not clean_note:
            raise err_fn(
                422,
                ErrorCode.VALIDATION_FAILED,
                "A non-empty operator review note is required",
                "review_note_required",
            )
        if len(clean_note) > MEETING_OPERATOR_REVIEW_NOTE_MAX_CHARS:
            raise err_fn(
                422,
                ErrorCode.VALIDATION_FAILED,
                f"Operator review note exceeds {MEETING_OPERATOR_REVIEW_NOTE_MAX_CHARS} characters",
                "review_note_too_long",
            )
        operator_id = str(getattr(identity, "operator_id", None) or caller_user_id or "").strip()
        if not operator_id:
            raise err_fn(
                403,
                ErrorCode.FORBIDDEN,
                "Authenticated operator identity is required to review a meeting",
                "operator_required",
            )
        clean_idem = str(idempotency_key or "").strip() or None
        snap = now_fn()
        try:
            reviewed_raw = make_client(tenant_id, operator_id).review_meeting(
                meeting_id=meeting_id,
                note=clean_note,
                reviewed_by=operator_id,
                idempotency_key=clean_idem,
            )
        except MeetingsOwnerNotFoundError as exc:
            raise err_fn(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Meeting {meeting_id!r} was not found",
                exc.reason,
            ) from exc
        except MeetingsOwnerValidationError as exc:
            err_code = (
                ErrorCode.FORBIDDEN
                if exc.status_code == 403
                else (
                    ErrorCode.RESOURCE_CONFLICT
                    if exc.status_code == 409
                    else ErrorCode.VALIDATION_FAILED
                )
            )
            raise err_fn(
                exc.status_code,
                err_code,
                f"Consultation owner rejected review request: {exc.reason}",
                exc.reason,
            ) from exc
        except MeetingsOwnerUnavailableError as exc:
            raise HTTPException(
                status_code=503,
                detail=unavailable_meetings_envelope("meeting_detail", exc.reason, snapshot_at=snap),
            ) from exc

        envelope = _annotate_meeting_envelope(
            shape_meeting_detail(
                reviewed_raw,
                is_operator=is_operator,
                caller_user_id=caller_user_id,
                snapshot_at=snap,
            )
        )
        replayed = bool(reviewed_raw.get("replayed"))
        envelope["replayed"] = replayed
        if isinstance(envelope.get("data"), dict):
            envelope["data"]["replayed"] = replayed
        if isinstance(envelope.get("meta"), dict):
            envelope["meta"]["capability"] = "management.meetings.review.v1"
            envelope["meta"]["replayed"] = replayed
            if clean_idem:
                envelope["meta"]["idempotency_key"] = clean_idem
        return envelope

    # --- Static routes registered BEFORE parameterized /{meeting_id} routes ---

    @router.get("/bff/management/meetings/proposals", operation_id="bff_management_list_meeting_proposals")
    def bff_management_list_meeting_proposals(
        request: Request,
        status: Optional[str] = Query(default=None),
        persona_id: Optional[str] = Query(default=None),
        meeting_id: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        return _handle_list_proposals(
            request,
            status=status,
            persona_id=persona_id,
            meeting_id=meeting_id,
        )

    @router.get("/bff/meetings/proposals", operation_id="bff_list_meeting_proposals")
    def bff_list_meeting_proposals(
        request: Request,
        status: Optional[str] = Query(default=None),
        persona_id: Optional[str] = Query(default=None),
        meeting_id: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        return _handle_list_proposals(
            request,
            status=status,
            persona_id=persona_id,
            meeting_id=meeting_id,
        )

    @router.get("/bff/management/meetings/catalogue", operation_id="bff_management_get_meeting_catalogue")
    def bff_management_get_meeting_catalogue(
        request: Request,
        persona_id: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        return _handle_get_catalogue(request, persona_id=persona_id)

    @router.get("/bff/meetings/catalogue", operation_id="bff_get_meeting_catalogue")
    def bff_get_meeting_catalogue(
        request: Request,
        persona_id: Optional[str] = Query(default=None),
    ) -> Dict[str, Any]:
        return _handle_get_catalogue(request, persona_id=persona_id)

    @router.get("/bff/management/meetings", operation_id="bff_management_list_meetings")
    def bff_management_list_meetings(
        request: Request,
        status: Optional[str] = Query(default=None),
        type: Optional[str] = Query(default=None),
        meeting_type: Optional[str] = Query(default=None),
        persona_id: Optional[str] = Query(default=None),
        persona: Optional[str] = Query(default=None),
        cursor: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        page_size: Optional[int] = Query(default=None, ge=1, le=500),
        include_failed: bool = Query(default=False),
    ) -> Dict[str, Any]:
        return _handle_list_meetings(
            request,
            status=status,
            type_=type,
            meeting_type=meeting_type,
            persona_id=persona_id,
            persona=persona,
            cursor=cursor,
            page_token=page_token,
            limit=limit,
            page_size=page_size,
            include_failed=include_failed,
        )

    @router.get("/bff/meetings", operation_id="bff_list_meetings")
    def bff_list_meetings(
        request: Request,
        status: Optional[str] = Query(default=None),
        type: Optional[str] = Query(default=None),
        meeting_type: Optional[str] = Query(default=None),
        persona_id: Optional[str] = Query(default=None),
        persona: Optional[str] = Query(default=None),
        cursor: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        page_size: Optional[int] = Query(default=None, ge=1, le=500),
        include_failed: bool = Query(default=False),
    ) -> Dict[str, Any]:
        return _handle_list_meetings(
            request,
            status=status,
            type_=type,
            meeting_type=meeting_type,
            persona_id=persona_id,
            persona=persona,
            cursor=cursor,
            page_token=page_token,
            limit=limit,
            page_size=page_size,
            include_failed=include_failed,
        )

    @router.post("/bff/management/meetings", status_code=202, operation_id="bff_management_convene_meeting")
    def bff_management_convene_meeting(
        body: OperatorConveneMeetingRequest,
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        return _handle_convene_meeting(body, request, idempotency_key)

    @router.post("/bff/meetings", status_code=202, operation_id="bff_convene_meeting")
    def bff_convene_meeting(
        body: OperatorConveneMeetingRequest,
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        return _handle_convene_meeting(body, request, idempotency_key)

    @router.post(
        "/bff/management/meetings/convene",
        status_code=202,
        operation_id="bff_management_convene_meeting_action",
    )
    def bff_management_convene_meeting_action(
        body: OperatorConveneMeetingRequest,
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        return _handle_convene_meeting(body, request, idempotency_key)

    @router.post("/bff/meetings/convene", status_code=202, operation_id="bff_convene_meeting_action")
    def bff_convene_meeting_action(
        body: OperatorConveneMeetingRequest,
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        return _handle_convene_meeting(body, request, idempotency_key)

    # --- Parameterized /{meeting_id} routes registered AFTER static routes ---

    @router.get(
        "/bff/management/meetings/{meeting_id}",
        operation_id="bff_management_get_meeting_detail",
    )
    def bff_management_get_meeting_detail(meeting_id: str, request: Request) -> Dict[str, Any]:
        return _handle_get_meeting_detail(meeting_id, request)

    @router.get("/bff/meetings/{meeting_id}", operation_id="bff_get_meeting_detail")
    def bff_get_meeting_detail(meeting_id: str, request: Request) -> Dict[str, Any]:
        return _handle_get_meeting_detail(meeting_id, request)

    @router.post(
        "/bff/management/meetings/{meeting_id}/review",
        status_code=200,
        operation_id="bff_management_review_meeting",
    )
    def bff_management_review_meeting(
        meeting_id: str,
        body: OperatorReviewMeetingRequest,
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        return _handle_review_meeting(meeting_id, body, request, idempotency_key)

    @router.post(
        "/bff/meetings/{meeting_id}/review",
        status_code=200,
        operation_id="bff_review_meeting",
    )
    def bff_review_meeting(
        meeting_id: str,
        body: OperatorReviewMeetingRequest,
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        return _handle_review_meeting(meeting_id, body, request, idempotency_key)

    return router
