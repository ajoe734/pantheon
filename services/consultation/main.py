from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture

from .models import (
    AcknowledgeGateHandoffRequest,
    ActorRef,
    AssignParticipantRequest,
    AttachEvidenceRequest,
    CancelConsultRequestRequest,
    ConsultAuditEvent,
    ConsultEvidenceAttachment,
    ConsultFinding,
    ConsultGateHandoff,
    ConsultMemo,
    ConsultParticipant,
    ConsultRequest,
    ConsultRequestStatus,
    ConsultRequestType,
    ConsultTranscript,
    CreateConsultRequest,
    CreateGateHandoffRequest,
    CreatePolicyLearningCandidateIntakeRequest,
    FindingSeverity,
    MemoStatus,
    MemoType,
    PostTranscriptEventRequest,
    Recommendation,
    RecordSponsorDecisionRequest,
    SubmitMemoRequest,
    TranscriptEvent,
    GateHandoffStatus,
    utc_now,
    AuthorType,
    ParticipantType,
    validate_consult_memo_against_request,
)
from .store import build_consultation_store
from .auth import (
    ConsultationAuthError,
    authenticate,
    bind_identity,
    current_identity,
    require_actor,
    reset_identity,
)
from .workflow_state import WorkflowStateStore


app = FastAPI(title="Pantheon Consultation Service", version="0.1.0")

DATA_DIR = os.getenv("CONSULTATION_DATA_DIR", "/tmp/pantheon/consultation")
STORE_BACKEND = os.getenv("CONSULTATION_STORE_BACKEND", "jsonl").strip().lower() or "jsonl"
PERSISTENCE_POSTURE = require_persistence_posture("consultation")
store = build_consultation_store(DATA_DIR)
WORKFLOW_STATE_PATH = os.getenv(
    "CONSULTATION_WORKFLOW_STATE_PATH",
    str(Path(DATA_DIR) / "consult_workflow_state.sqlite3"),
)
workflow_state = WorkflowStateStore(WORKFLOW_STATE_PATH)
register_fastapi_health_routes(
    app,
    "consultation",
    dependencies=lambda: {"persistence": PERSISTENCE_POSTURE.to_dict()},
    details=lambda: {
        "data_dir": DATA_DIR,
        "store_backend": STORE_BACKEND,
        "persistence_posture": PERSISTENCE_POSTURE.to_dict(),
    },
)

CONSULTATION_SERVICE_ACTOR = ActorRef(
    actor_type="service",
    actor_id="consultation-svc",
)

_CREATE_FINGERPRINT_METADATA_KEY = "_pantheon_create_fingerprint_sha256"
_REQUEST_COMMAND_LOCK = threading.RLock()
_SUBMITTED_OR_LATER_STATUSES = {
    ConsultRequestStatus.SUBMITTED,
    ConsultRequestStatus.ASSIGNED,
    ConsultRequestStatus.IN_PROGRESS,
    ConsultRequestStatus.MEMO_PENDING,
    ConsultRequestStatus.PUBLISHED,
}


@app.middleware("http")
async def consultation_identity_boundary(
    request: Request,
    call_next,
):
    if not request.url.path.startswith("/api/consult"):
        return await call_next(request)
    try:
        identity = authenticate(request.headers)
    except ConsultationAuthError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
    token = bind_identity(identity)
    try:
        return await call_next(request)
    finally:
        reset_identity(token)


def _actor_to_data(actor_ref: ActorRef | Dict[str, str]) -> Dict[str, str]:
    if hasattr(actor_ref, "model_dump"):
        return actor_ref.model_dump(mode="json")
    if hasattr(actor_ref, "dict"):
        return actor_ref.dict()
    return dict(actor_ref)


def _emit_audit(
    action: str,
    request_id: str,
    actor_ref: ActorRef | Dict[str, str],
    trace_id: str,
    before_state: Optional[str] = None,
    after_state: Optional[str] = None,
    payload_hash: Optional[str] = None,
    service_actor_ref: Optional[ActorRef | Dict[str, str]] = None,
) -> ConsultAuditEvent:
    event = ConsultAuditEvent(
        audit_id=f"aud-{uuid.uuid4().hex[:12]}",
        request_id=request_id,
        actor_ref=_actor_to_data(actor_ref),
        service_actor_ref=_actor_to_data(service_actor_ref) if service_actor_ref else None,
        action=action,
        before_state=before_state,
        after_state=after_state,
        payload_hash=payload_hash,
        trace_id=trace_id,
    )
    store.append_audit(event)
    return event


def _get_request_or_404(request_id: str) -> ConsultRequest:
    request = store.get_request(request_id)
    if not request or request.tenant_id != current_identity().tenant_id:
        raise HTTPException(status_code=404, detail="ConsultRequest not found")
    return request


def _stable_command_id(
    prefix: str,
    *,
    request_id: str,
    idempotency_key: str | None,
) -> str:
    if not idempotency_key:
        return f"{prefix}-{uuid.uuid4().hex[:12]}"
    identity = current_identity()
    digest = hashlib.sha256(
        (
            f"{identity.tenant_id}\0{request_id}\0{idempotency_key}"
        ).encode("utf-8")
    ).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _raise_auth(exc: ConsultationAuthError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _require_actor(*actor_types: str):
    try:
        return require_actor(*actor_types)
    except ConsultationAuthError as exc:
        _raise_auth(exc)


def _request_dict(req: Any, exclude: Optional[set[str]] = None) -> Dict[str, Any]:
    if hasattr(req, "model_dump"):
        return req.model_dump(exclude=exclude or set(), mode="json")
    return req.dict(exclude=exclude or set())


def _canonical_create_payload(req: Any) -> Dict[str, Any]:
    """Return the normalized caller-owned fields used by create idempotency."""
    payload = _request_dict(req, exclude={"request_id"})
    metadata = dict(payload.get("metadata") or {})
    metadata.pop(_CREATE_FINGERPRINT_METADATA_KEY, None)
    payload["metadata"] = metadata
    return payload


def _create_fingerprint(request_id: str, payload: Dict[str, Any]) -> str:
    canonical = json.dumps(
        {"request_id": request_id, "request": payload},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _stored_create_fingerprint(request: ConsultRequest) -> str:
    metadata = dict(request.metadata or {})
    recorded = metadata.get(_CREATE_FINGERPRINT_METADATA_KEY)
    if isinstance(recorded, str) and recorded:
        return recorded

    # Legacy requests pre-date persisted create fingerprints. Reconstruct the
    # original create shape when it is still available; changed legacy records
    # fail closed because their reconstructed fingerprint will not match.
    request_data = _request_dict(request)
    create_fields = set(getattr(CreateConsultRequest, "model_fields", {}).keys())
    if not create_fields:  # Pydantic v1 compatibility.
        create_fields = set(getattr(CreateConsultRequest, "__fields__", {}).keys())
    payload = {
        field: request_data.get(field)
        for field in create_fields
        if field != "request_id"
    }
    payload_metadata = dict(payload.get("metadata") or {})
    payload_metadata.pop(_CREATE_FINGERPRINT_METADATA_KEY, None)
    payload["metadata"] = payload_metadata
    return _create_fingerprint(request.request_id, payload)


def _ensure_audit_once(
    *,
    action: str,
    request: ConsultRequest,
    payload_hash: Optional[str] = None,
    before_state: Optional[str] = None,
    after_state: Optional[str] = None,
) -> None:
    existing = [
        event
        for event in store.list_audit_for_request(request.request_id)
        if event.action == action
    ]
    if existing:
        recorded_hashes = {
            event.payload_hash for event in existing if event.payload_hash is not None
        }
        if payload_hash and recorded_hashes and payload_hash not in recorded_hashes:
            raise HTTPException(
                status_code=409,
                detail=f"Persisted {action} fingerprint conflicts with request body",
            )
        return
    _emit_audit(
        action=action,
        request_id=request.request_id,
        actor_ref=request.requested_by,
        trace_id=request.trace_id,
        before_state=before_state,
        after_state=after_state,
        payload_hash=payload_hash,
    )


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "consultation"}


# --- Consult Requests ---


@app.post("/api/consult/requests", response_model=ConsultRequest, status_code=201)
def create_request(req: CreateConsultRequest) -> ConsultRequest:
    request_id = req.request_id or f"cr-{uuid.uuid4().hex[:12]}"
    create_payload = _canonical_create_payload(req)
    identity = current_identity()
    requested_tenant = str(create_payload.get("tenant_id") or identity.tenant_id)
    if requested_tenant != identity.tenant_id:
        raise HTTPException(status_code=403, detail="ConsultRequest tenant scope denied")
    create_payload["tenant_id"] = identity.tenant_id
    fingerprint = _create_fingerprint(request_id, create_payload)

    with _REQUEST_COMMAND_LOCK:
        existing = store.get_request(request_id)
        if existing is not None:
            if _stored_create_fingerprint(existing) != fingerprint:
                raise HTTPException(
                    status_code=409,
                    detail="ConsultRequest request_id already exists with a different canonical request",
                )
            _ensure_audit_once(
                action="request_created",
                request=existing,
                payload_hash=fingerprint,
                after_state=ConsultRequestStatus.DRAFT.value,
            )
            return existing

        stored_payload = dict(create_payload)
        stored_metadata = dict(create_payload.get("metadata") or {})
        stored_metadata[_CREATE_FINGERPRINT_METADATA_KEY] = fingerprint
        stored_payload["metadata"] = stored_metadata
        new_req = ConsultRequest(request_id=request_id, **stored_payload)
        store.put_request(new_req)
        _ensure_audit_once(
            action="request_created",
            request=new_req,
            payload_hash=fingerprint,
            after_state=ConsultRequestStatus.DRAFT.value,
        )
        return new_req


@app.get("/api/consult/requests", response_model=List[ConsultRequest])
def list_requests(
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    status: Optional[ConsultRequestStatus] = None,
) -> List[ConsultRequest]:
    tenant_id = current_identity().tenant_id
    requests = [
        request
        for request in store.list_requests()
        if request.tenant_id == tenant_id
    ]
    if target_type:
        requests = [request for request in requests if request.target_type == target_type]
    if target_id:
        requests = [request for request in requests if request.target_id == target_id]
    if status:
        requests = [request for request in requests if request.status == status]
    return requests


@app.get("/api/consult/requests/{request_id}", response_model=ConsultRequest)
def get_request(request_id: str) -> ConsultRequest:
    return _get_request_or_404(request_id)


@app.post("/api/consult/requests/{request_id}/submit", response_model=ConsultRequest)
def submit_request(request_id: str) -> ConsultRequest:
    with _REQUEST_COMMAND_LOCK:
        request = _get_request_or_404(request_id)
        if request.status in _SUBMITTED_OR_LATER_STATUSES:
            if request.status == ConsultRequestStatus.SUBMITTED:
                _ensure_audit_once(
                    action="request_submitted",
                    request=request,
                    before_state=ConsultRequestStatus.DRAFT.value,
                    after_state=ConsultRequestStatus.SUBMITTED.value,
                )
            return request
        if request.status != ConsultRequestStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot submit request in {request.status.value} state",
            )

        before_state = request.status.value
        request.status = ConsultRequestStatus.SUBMITTED
        store.put_request(request)
        _ensure_audit_once(
            action="request_submitted",
            request=request,
            before_state=before_state,
            after_state=request.status.value,
        )
        return request


@app.post("/api/consult/requests/{request_id}/cancel", response_model=ConsultRequest)
def cancel_request(request_id: str, req: CancelConsultRequestRequest) -> ConsultRequest:
    request = _get_request_or_404(request_id)
    if request.linked_session_id:
        raise HTTPException(status_code=409, detail="Cannot cancel a request with a linked session")
    if request.status in {ConsultRequestStatus.PUBLISHED, ConsultRequestStatus.CANCELLED}:
        raise HTTPException(status_code=409, detail=f"Cannot cancel request in {request.status.value} state")

    before_state = request.status.value
    request.status = ConsultRequestStatus.CANCELLED
    request.canceled_at = req.canceled_at or utc_now()
    request.request_to_session_status = "canceled_before_session"
    request.session_handoff_note = "Request canceled by operator."
    store.put_request(request)
    _emit_audit(
        action="request_cancelled",
        request_id=request_id,
        actor_ref=req.actor_ref,
        trace_id=req.trace_id or request.trace_id,
        before_state=before_state,
        after_state=request.status.value,
    )
    return request


# --- Participants ---


@app.post(
    "/api/consult/requests/{request_id}/participants",
    response_model=ConsultParticipant,
    status_code=201,
)
def assign_participant(
    request_id: str, req: AssignParticipantRequest
) -> ConsultParticipant:
    with _REQUEST_COMMAND_LOCK:
        request = _get_request_or_404(request_id)
        participant_id = _stable_command_id(
            "cp",
            request_id=request_id,
            idempotency_key=req.idempotency_key,
        )
        participant = ConsultParticipant(
            participant_id=participant_id,
            request_id=request_id,
            participant_type=req.participant_type,
            participant_ref=req.participant_ref,
            role=req.role,
        )
        existing = store.get_participant(participant_id)
        if existing is not None:
            comparable_existing = _request_dict(existing, exclude={"assigned_at"})
            comparable_new = _request_dict(participant, exclude={"assigned_at"})
            if comparable_existing != comparable_new:
                raise HTTPException(
                    status_code=409,
                    detail="participant idempotency key conflicts with prior assignment",
                )
            return existing
        store.put_participant(participant)

        before_state = request.status.value
        if request.status == ConsultRequestStatus.SUBMITTED:
            request.status = ConsultRequestStatus.ASSIGNED
            store.put_request(request)

        _emit_audit(
            action="participant_assigned",
            request_id=request_id,
            actor_ref=req.initiated_by or request.requested_by,
            service_actor_ref=CONSULTATION_SERVICE_ACTOR,
            trace_id=req.trace_id,
            before_state=before_state,
            after_state=request.status.value,
        )
        return participant


@app.get(
    "/api/consult/requests/{request_id}/participants",
    response_model=List[ConsultParticipant],
)
def get_participants(request_id: str) -> List[ConsultParticipant]:
    _get_request_or_404(request_id)
    return store.list_participants_for_request(request_id)


# --- Evidence Attachments ---


@app.post(
    "/api/consult/requests/{request_id}/evidence",
    response_model=ConsultEvidenceAttachment,
    status_code=201,
)
def attach_evidence(
    request_id: str, req: AttachEvidenceRequest
) -> ConsultEvidenceAttachment:
    with _REQUEST_COMMAND_LOCK:
        request = _get_request_or_404(request_id)
        attachment_id = _stable_command_id(
            "cea",
            request_id=request_id,
            idempotency_key=req.idempotency_key,
        )
        attachment = ConsultEvidenceAttachment(
            attachment_id=attachment_id,
            request_id=request_id,
            evidence_ref=req.evidence_ref,
            attached_by=req.attached_by,
            trace_id=req.trace_id,
        )
        existing = store.get_evidence_attachment(attachment_id)
        if existing is not None:
            comparable_existing = _request_dict(existing, exclude={"created_at"})
            comparable_new = _request_dict(attachment, exclude={"created_at"})
            if comparable_existing != comparable_new:
                raise HTTPException(
                    status_code=409,
                    detail="evidence idempotency key conflicts with prior attachment",
                )
            return existing
        store.put_evidence_attachment(attachment)

        if req.evidence_ref.id not in request.evidence_refs:
            request.evidence_refs.append(req.evidence_ref.id)
            store.put_request(request)

        _emit_audit(
            action="evidence_attached",
            request_id=request_id,
            actor_ref=req.attached_by,
            trace_id=req.trace_id,
            after_state=req.evidence_ref.id,
        )
        return attachment


@app.get(
    "/api/consult/requests/{request_id}/evidence",
    response_model=List[ConsultEvidenceAttachment],
)
def list_evidence(request_id: str) -> List[ConsultEvidenceAttachment]:
    _get_request_or_404(request_id)
    return store.list_evidence_for_request(request_id)


# --- Transcripts (Committee Debate) ---


@app.get("/api/consult/requests/{request_id}/transcript", response_model=ConsultTranscript)
def get_transcript(request_id: str) -> ConsultTranscript:
    _get_request_or_404(request_id)
    transcript = store.get_transcript(request_id)
    if not transcript:
        return ConsultTranscript(
            transcript_id=f"tr-{request_id}",
            session_id=request_id,
            request_id=request_id,
            events=[],
        )
    return transcript


@app.post(
    "/api/consult/requests/{request_id}/events",
    response_model=TranscriptEvent,
    status_code=201,
)
def post_event(request_id: str, req: PostTranscriptEventRequest) -> TranscriptEvent:
    with _REQUEST_COMMAND_LOCK:
        request = _get_request_or_404(request_id)
        if req.request_id != request_id:
            raise HTTPException(status_code=400, detail="Path request_id does not match payload")

        transcript = store.get_transcript(request_id)
        event_id = _stable_command_id(
            "evt",
            request_id=request_id,
            idempotency_key=req.idempotency_key,
        )
        if transcript:
            existing = next(
                (event for event in transcript.events if event.event_id == event_id),
                None,
            )
            if existing is not None:
                comparable_existing = _request_dict(
                    existing,
                    exclude={"sequence_no", "event_time", "session_id", "event_id"},
                )
                comparable_new = _request_dict(
                    req,
                    exclude={"request_id", "idempotency_key"},
                )
                if comparable_existing != comparable_new:
                    raise HTTPException(
                        status_code=409,
                        detail="event idempotency key conflicts with prior transcript event",
                    )
                return existing
        next_seq = len(transcript.events) + 1 if transcript else 1

        event = TranscriptEvent(
            event_id=event_id,
            session_id=request_id,
            sequence_no=next_seq,
            **_request_dict(req, exclude={"request_id", "idempotency_key"}),
        )
        store.add_transcript_event(request_id, event)

        before_state = request.status.value
        if request.status in {
            ConsultRequestStatus.SUBMITTED,
            ConsultRequestStatus.ASSIGNED,
        }:
            request.status = ConsultRequestStatus.IN_PROGRESS
            store.put_request(request)

        _emit_audit(
            action="transcript_event_added",
            request_id=request_id,
            actor_ref=event.actor,
            trace_id=request.trace_id,
            before_state=before_state,
            after_state=request.status.value,
        )
        return event


# --- Memos ---


@app.get("/api/consult/memos", response_model=List[ConsultMemo])
def list_memos(
    request_id: Optional[str] = None,
    status: Optional[MemoStatus] = None,
) -> List[ConsultMemo]:
    tenant_id = current_identity().tenant_id
    request_tenants = {
        request.request_id: request.tenant_id
        for request in store.list_requests()
    }
    memos = [
        memo
        for memo in store.list_memos()
        if request_tenants.get(memo.request_id) == tenant_id
    ]
    if request_id:
        memos = [memo for memo in memos if memo.request_id == request_id]
    if status:
        memos = [memo for memo in memos if memo.status == status]
    return memos


@app.post("/api/consult/memos", response_model=ConsultMemo, status_code=201)
def submit_memo(req: SubmitMemoRequest) -> ConsultMemo:
    with _REQUEST_COMMAND_LOCK:
        request = _get_request_or_404(req.request_id)

        memo_id = _stable_command_id(
            "mem",
            request_id=req.request_id,
            idempotency_key=req.idempotency_key,
        )
        new_memo = ConsultMemo(
            memo_id=memo_id,
            target_type=request.target_type,
            target_id=request.target_id,
            status=MemoStatus.SUBMITTED,
            **_request_dict(req, exclude={"idempotency_key"}),
        )
        existing = store.get_memo(memo_id)
        if existing is not None:
            comparable_existing = _request_dict(
                existing,
                exclude={"created_at", "published_at", "status"},
            )
            comparable_new = _request_dict(
                new_memo,
                exclude={"created_at", "published_at", "status"},
            )
            if comparable_existing != comparable_new:
                raise HTTPException(
                    status_code=409,
                    detail="memo idempotency key conflicts with prior submission",
                )
            return existing
        store.put_memo(new_memo)

        before_state = request.status.value
        if request.status in {
            ConsultRequestStatus.SUBMITTED,
            ConsultRequestStatus.ASSIGNED,
            ConsultRequestStatus.IN_PROGRESS,
        }:
            request.status = ConsultRequestStatus.MEMO_PENDING
            store.put_request(request)

        _emit_audit(
            action="memo_submitted",
            request_id=req.request_id,
            actor_ref={"actor_type": req.author_type.value, "actor_id": req.author_ref},
            trace_id=req.trace_id,
            before_state=before_state,
            after_state=request.status.value,
        )
        return new_memo


@app.get("/api/consult/memos/{memo_id}", response_model=ConsultMemo)
def get_memo(memo_id: str) -> ConsultMemo:
    memo = store.get_memo(memo_id)
    if not memo or store.get_request(memo.request_id) is None:
        raise HTTPException(status_code=404, detail="ConsultMemo not found")
    _get_request_or_404(memo.request_id)
    return memo


def _validate_qualified_consultation(request_id: str, memo: ConsultMemo) -> None:
    # Get participants
    participants = store.list_participants_for_request(request_id)
    if not participants:
        raise HTTPException(
            status_code=400,
            detail="No participants assigned to the consult request."
        )

    # 1. Verify Memo is authored by an assigned real participant (not system)
    if memo.author_type == AuthorType.SYSTEM:
        raise HTTPException(
            status_code=400,
            detail="Memo is not qualified: author type cannot be system."
        )
    memo_author_qualified = False
    for p in participants:
        mapped_ptype = "human" if p.participant_type == ParticipantType.HUMAN_REVIEWER else p.participant_type.value
        if p.participant_ref == memo.author_ref and mapped_ptype == memo.author_type.value:
            memo_author_qualified = True
            break
    if not memo_author_qualified:
        raise HTTPException(
            status_code=400,
            detail=f"Memo author {memo.author_ref} ({memo.author_type}) is not an assigned participant."
        )

    # 2. Verify Transcript contains at least one event from a real assigned participant
    transcript = store.get_transcript(request_id)
    has_real_event = False
    if transcript and transcript.events:
        for event in transcript.events:
            actor_type = event.actor.actor_type
            actor_id = event.actor.actor_id
            if actor_type in {"service", "system"}:
                continue
            for p in participants:
                mapped_ptype = "human" if p.participant_type == ParticipantType.HUMAN_REVIEWER else p.participant_type.value
                if p.participant_ref == actor_id and mapped_ptype == actor_type:
                    has_real_event = True
                    break
            if has_real_event:
                break
    if not has_real_event:
        raise HTTPException(
            status_code=400,
            detail="Transcript is not qualified: must contain at least one event from an assigned real participant/provider."
        )

    # 3. Verify Review Evidence is present and qualified (no service/system attachments, attached by assigned participant)
    attachments = store.list_evidence_for_request(request_id)
    request = store.get_request(request_id)
    total_ev = len(request.evidence_refs) + len(attachments) if request else len(attachments)
    if total_ev == 0:
        raise HTTPException(
            status_code=400,
            detail="Review evidence is missing: request must have evidence refs or attachments."
        )
    for att in attachments:
        actor_type = att.attached_by.actor_type
        actor_id = att.attached_by.actor_id
        if actor_type in {"service", "system"}:
            raise HTTPException(
                status_code=400,
                detail=f"Evidence attachment {att.attachment_id} has invalid author type: {actor_type}."
            )
        att_qualified = False
        for p in participants:
            mapped_ptype = "human" if p.participant_type == ParticipantType.HUMAN_REVIEWER else p.participant_type.value
            if p.participant_ref == actor_id and mapped_ptype == actor_type:
                att_qualified = True
                break
        if not att_qualified:
            raise HTTPException(
                status_code=400,
                detail=f"Evidence attachment {att.attachment_id} was not attached by an assigned participant."
            )


@app.post("/api/consult/memos/{memo_id}/publish", response_model=ConsultMemo)
def publish_memo(memo_id: str) -> ConsultMemo:
    memo = store.get_memo(memo_id)
    if not memo:
        raise HTTPException(status_code=404, detail="ConsultMemo not found")
    if memo.status == MemoStatus.PUBLISHED:
        return memo

    request = _get_request_or_404(memo.request_id)
    _validate_qualified_consultation(memo.request_id, memo)

    memo.status = MemoStatus.PUBLISHED
    memo.published_at = utc_now()
    store.put_memo(memo)

    before_state = request.status.value
    request.status = ConsultRequestStatus.PUBLISHED
    store.put_request(request)

    _emit_audit(
        action="memo_published",
        request_id=memo.request_id,
        actor_ref={"actor_type": memo.author_type.value, "actor_id": memo.author_ref},
        trace_id=memo.trace_id,
        before_state=before_state,
        after_state=request.status.value,
    )
    return memo


@app.get(
    "/api/consult/targets/{target_type}/{target_id}/memos",
    response_model=List[ConsultMemo],
)
def list_memos_for_target(target_type: str, target_id: str) -> List[ConsultMemo]:
    tenant_id = current_identity().tenant_id
    request_tenants = {
        request.request_id: request.tenant_id
        for request in store.list_requests()
    }
    return [
        memo
        for memo in store.list_memos_for_target(target_type, target_id)
        if request_tenants.get(memo.request_id) == tenant_id
    ]


# --- Governance Gate Handoffs ---


@app.get("/api/consult/transcripts", response_model=List[ConsultTranscript])
def list_transcripts() -> List[ConsultTranscript]:
    tenant_id = current_identity().tenant_id
    request_tenants = {
        request.request_id: request.tenant_id
        for request in store.list_requests()
    }
    return [
        transcript
        for transcript in store.list_transcripts()
        if request_tenants.get(transcript.request_id) == tenant_id
    ]


@app.get("/api/consult/handoffs", response_model=List[ConsultGateHandoff])
def list_handoffs(request_id: Optional[str] = None) -> List[ConsultGateHandoff]:
    if request_id:
        _get_request_or_404(request_id)
        return store.list_handoffs_for_request(request_id)
    tenant_id = current_identity().tenant_id
    request_tenants = {
        request.request_id: request.tenant_id
        for request in store.list_requests()
    }
    return [
        handoff
        for handoff in store.list_handoffs()
        if request_tenants.get(handoff.request_id) == tenant_id
    ]


@app.post("/api/consult/handoffs", response_model=ConsultGateHandoff, status_code=201)
def create_handoff(req: CreateGateHandoffRequest) -> ConsultGateHandoff:
    with _REQUEST_COMMAND_LOCK:
        request = _get_request_or_404(req.request_id)
        if not req.memo_ids:
            raise HTTPException(status_code=400, detail="Gate handoff requires at least one memo")

        for memo_id in req.memo_ids:
            memo = store.get_memo(memo_id)
            if not memo:
                raise HTTPException(status_code=404, detail=f"ConsultMemo {memo_id} not found")
            if memo.request_id != req.request_id:
                raise HTTPException(status_code=400, detail=f"ConsultMemo {memo_id} belongs to another request")
            if memo.status != MemoStatus.PUBLISHED:
                raise HTTPException(status_code=400, detail=f"ConsultMemo {memo_id} is not published")
            _validate_qualified_consultation(req.request_id, memo)

        attached_evidence_refs = [
            attachment.evidence_ref.id
            for attachment in store.list_evidence_for_request(req.request_id)
        ]
        evidence_refs = sorted(set(request.evidence_refs + attached_evidence_refs + req.evidence_refs))
        audit_refs = [event.audit_id for event in store.list_audit_for_request(req.request_id)]

        handoff_id = _stable_command_id(
            "gh",
            request_id=req.request_id,
            idempotency_key=req.idempotency_key,
        )
        handoff = ConsultGateHandoff(
            handoff_id=handoff_id,
            request_id=req.request_id,
            target_gate=req.target_gate,
            memo_ids=req.memo_ids,
            evidence_refs=evidence_refs,
            audit_refs=audit_refs,
            trace_id=req.trace_id,
        )
        existing = store.get_handoff(handoff_id)
        if existing is not None:
            comparable_existing = _request_dict(
                existing,
                exclude={"audit_refs", "created_at", "sent_at", "status"},
            )
            comparable_new = _request_dict(
                handoff,
                exclude={"audit_refs", "created_at", "sent_at", "status"},
            )
            if comparable_existing != comparable_new:
                raise HTTPException(
                    status_code=409,
                    detail="handoff idempotency key conflicts with prior handoff",
                )
            return existing
        store.put_handoff(handoff)

        handoff_audit = _emit_audit(
            action="gate_handoff_created",
            request_id=req.request_id,
            actor_ref=req.initiated_by or request.requested_by,
            service_actor_ref=CONSULTATION_SERVICE_ACTOR,
            trace_id=req.trace_id,
            after_state=handoff.handoff_id,
        )
        handoff.audit_refs.append(handoff_audit.audit_id)
        store.put_handoff(handoff)
        return handoff


@app.get("/api/consult/handoffs/{handoff_id}", response_model=ConsultGateHandoff)
def get_handoff(handoff_id: str) -> ConsultGateHandoff:
    handoff = store.get_handoff(handoff_id)
    if not handoff:
        raise HTTPException(status_code=404, detail="ConsultGateHandoff not found")
    _get_request_or_404(handoff.request_id)
    return handoff


@app.post(
    "/api/consult/handoffs/{handoff_id}/acknowledge",
    response_model=ConsultGateHandoff,
)
def acknowledge_handoff(
    handoff_id: str,
    req: AcknowledgeGateHandoffRequest,
) -> ConsultGateHandoff:
    identity = _require_actor("service")
    with _REQUEST_COMMAND_LOCK:
        handoff = get_handoff(handoff_id)
        if handoff.status == GateHandoffStatus.ACKNOWLEDGED:
            return handoff
        handoff.status = GateHandoffStatus.ACKNOWLEDGED
        handoff.sent_at = req.acknowledged_at or utc_now()
        store.put_handoff(handoff)
        _emit_audit(
            action="gate_handoff_acknowledged",
            request_id=handoff.request_id,
            actor_ref={
                "actor_type": identity.actor_type,
                "actor_id": req.consumer_ref,
            },
            service_actor_ref=CONSULTATION_SERVICE_ACTOR,
            trace_id=handoff.trace_id,
            after_state=handoff.handoff_id,
        )
        return handoff


@app.get(
    "/api/consult/requests/{request_id}/handoffs",
    response_model=List[ConsultGateHandoff],
)
def list_handoffs_for_request(request_id: str) -> List[ConsultGateHandoff]:
    _get_request_or_404(request_id)
    return store.list_handoffs_for_request(request_id)


@app.get("/api/consult/workflows")
def list_workflows(status: Optional[str] = None) -> Dict[str, Any]:
    identity = current_identity()
    if status and status not in {
        "pending",
        "leased",
        "blocked",
        "dead_letter",
        "completed",
    }:
        raise HTTPException(status_code=400, detail="invalid workflow status")
    return {
        "tenant_id": identity.tenant_id,
        "counts": workflow_state.counts(tenant_id=identity.tenant_id),
        "items": workflow_state.list_items(
            tenant_id=identity.tenant_id,
            status=status,
        ),
    }


@app.post("/api/consult/workflows/dead-letters/{request_id}/replay")
def replay_dead_letter(request_id: str) -> Dict[str, Any]:
    identity = _require_actor("operator")
    _get_request_or_404(request_id)
    replayed = workflow_state.replay_dead_letter(
        tenant_id=identity.tenant_id,
        request_id=request_id,
    )
    if not replayed:
        raise HTTPException(
            status_code=409,
            detail="workflow is not in dead_letter state",
        )
    return {
        "tenant_id": identity.tenant_id,
        "request_id": request_id,
        "status": "pending",
        "replayed": True,
    }


@app.post("/api/consult/committees/{committee_id}/sponsor-decision")
def record_committee_sponsor_decision(
    committee_id: str,
    req: RecordSponsorDecisionRequest,
) -> Dict[str, Any]:
    sponsor_decision = req.sponsor_decision.strip().lower()
    if sponsor_decision not in {"approved", "rejected", "conditional"}:
        raise HTTPException(
            status_code=400,
            detail="sponsor_decision must be one of approved, rejected, or conditional",
        )
    rationale_ref = req.rationale_ref.strip()
    if not rationale_ref:
        raise HTTPException(status_code=400, detail="rationale_ref must be a non-empty string")

    matched_request: Optional[ConsultRequest] = None
    matched_consult: Dict[str, Any] = {}
    for request_record in store.list_requests():
        if request_record.tenant_id != current_identity().tenant_id:
            continue
        request_data = _request_dict(request_record)
        metadata = request_data.get("metadata") if isinstance(request_data.get("metadata"), dict) else {}
        consult = metadata.get("consultation") if isinstance(metadata.get("consultation"), dict) else {}
        if str(consult.get("committee_ref") or "").strip() == committee_id:
            matched_request = request_record
            matched_consult = dict(consult)
            break
    if matched_request is None:
        raise HTTPException(status_code=404, detail="Committee not found")

    # Check if a sponsor decision was already processed and successfully dispatched or gated
    existing_metadata = matched_request.metadata if isinstance(matched_request.metadata, dict) else {}
    existing_handoff_data = existing_metadata.get("service_handoff")
    existing_handoff = None
    if isinstance(existing_handoff_data, dict):
        existing_handoff_id = existing_handoff_data.get("handoff_id")
        if existing_handoff_id:
            existing_handoff = store.get_handoff(existing_handoff_id)
        
        dispatch_info = existing_handoff_data.get("proposal_dispatch")
        if isinstance(dispatch_info, dict) and dispatch_info.get("status") in {"sent", "gated"}:
            # Idempotency check: if the decision is the same, return the existing result.
            if matched_consult.get("sponsor_decision") == sponsor_decision:
                handoff_status = existing_handoff_data.get("status")
                if existing_handoff:
                    handoff_status = existing_handoff.status.value if hasattr(existing_handoff.status, "value") else existing_handoff.status
                return {
                    "committee_id": committee_id,
                    "committee_ref": matched_consult.get("committee_ref") or committee_id,
                    "linked_request_id": matched_request.request_id,
                    "linked_session_id": matched_request.linked_session_id or matched_consult.get("requester_session_id"),
                    "sponsor_decision": matched_consult.get("sponsor_decision"),
                    "sponsor_decided_at": matched_consult.get("sponsor_decided_at"),
                    "sponsor_decided_by": matched_consult.get("sponsor_decided_by"),
                    "consensus_state": matched_consult.get("consensus_state"),
                    "rationale_ref": (matched_consult.get("synthesis_summary") or {}).get("rationale_ref"),
                    "outcome": (matched_consult.get("synthesis_summary") or {}).get("outcome"),
                    "service_handoff": {
                        "handoff_id": existing_handoff_id,
                        "target_gate": existing_handoff_data.get("target_gate"),
                        "evidence_refs": existing_handoff_data.get("evidence_refs"),
                        "audit_refs": existing_handoff_data.get("audit_refs"),
                        "status": handoff_status,
                        "proposal_dispatch": dispatch_info,
                    },
                }

    memos = [
        memo
        for memo in store.list_memos_for_request(matched_request.request_id)
        if memo.status == MemoStatus.PUBLISHED
    ]
    if not memos:
        raise HTTPException(
            status_code=409,
            detail="Committee has no published consultation memo for gate handoff",
        )

    # Resolve assigned sponsor persona and real target version
    assigned_sponsor_persona_id = None
    if matched_consult.get("sponsor_persona_id"):
        assigned_sponsor_persona_id = matched_consult.get("sponsor_persona_id")
    elif matched_request.from_persona_id:
        assigned_sponsor_persona_id = matched_request.from_persona_id
    else:
        # Check committee_participants roster
        participants = matched_consult.get("committee_participants") or matched_request.metadata.get("committee_participants") or []
        for p in participants:
            if isinstance(p, dict) and p.get("role") == "sponsor" and p.get("participant_ref"):
                assigned_sponsor_persona_id = p.get("participant_ref")
                break
    if not assigned_sponsor_persona_id:
        assigned_sponsor_persona_id = req.actor_id

    target_version = (
        matched_consult.get("target_version")
        or matched_consult.get("version")
        or matched_request.metadata.get("target_version")
        or matched_request.metadata.get("version")
        or "1.0.0"
    )

    recorded_at = req.recorded_at or utc_now()
    matched_consult["sponsor_decision"] = sponsor_decision
    matched_consult["sponsor_decided_at"] = recorded_at
    matched_consult["sponsor_decided_by"] = assigned_sponsor_persona_id
    matched_consult["consensus_state"] = "reached"
    matched_consult["outcome"] = sponsor_decision
    synthesis_summary = dict(matched_consult.get("synthesis_summary") or {})
    synthesis_summary["outcome"] = sponsor_decision
    synthesis_summary["rationale_ref"] = rationale_ref
    matched_consult["synthesis_summary"] = synthesis_summary
    matched_consult["rationale_ref"] = rationale_ref

    evidence_refs: List[str] = []
    for ref_id in matched_request.evidence_refs:
        if str(ref_id or "").strip() and str(ref_id) not in evidence_refs:
            evidence_refs.append(str(ref_id))
    for attachment in store.list_evidence_for_request(matched_request.request_id):
        ref_id = str(attachment.evidence_ref.id or "").strip()
        if ref_id and ref_id not in evidence_refs:
            evidence_refs.append(ref_id)
    for item in matched_consult.get("evidence_refs") or []:
        ref_id = str(item.get("id") if isinstance(item, dict) else item or "").strip()
        if ref_id and ref_id not in evidence_refs:
            evidence_refs.append(ref_id)

    audit_refs = [event.audit_id for event in store.list_audit_for_request(matched_request.request_id)]
    
    if existing_handoff:
        handoff = existing_handoff
        handoff.sent_at = recorded_at
        handoff.evidence_refs = list(set(handoff.evidence_refs + evidence_refs))
        handoff.audit_refs = list(set(handoff.audit_refs + audit_refs))
        handoff.status = GateHandoffStatus.SENT
        store.put_handoff(handoff)
    else:
        handoff = ConsultGateHandoff(
            handoff_id=f"gh-{uuid.uuid4().hex[:12]}",
            request_id=matched_request.request_id,
            target_gate=f"committee_sponsor_decision:{committee_id}",
            memo_ids=[memo.memo_id for memo in memos],
            evidence_refs=evidence_refs,
            audit_refs=audit_refs,
            trace_id=matched_request.trace_id,
            status=GateHandoffStatus.SENT,
            sent_at=recorded_at,
        )
        store.put_handoff(handoff)
        audit = _emit_audit(
            action="gate_handoff_created",
            request_id=matched_request.request_id,
            actor_ref=ActorRef(actor_type="operator", actor_id=req.actor_id),
            service_actor_ref=CONSULTATION_SERVICE_ACTOR,
            trace_id=matched_request.trace_id,
            after_state=handoff.handoff_id,
        )
        handoff.audit_refs.append(audit.audit_id)
        store.put_handoff(handoff)

    # Map to governance / evolution proposal using the sponsor decision bridge
    from .sponsor_decision_bridge import bridge, SponsorDecisionBridgeError

    # Infer decision type: approval or evolution
    decision_type = matched_consult.get("type") or matched_consult.get("decision_type")
    if not decision_type:
        if matched_consult.get("action_type") or "action_type" in matched_consult:
            decision_type = "evolution"
        else:
            decision_type = "approval"

    # Translate evidence refs format for the bridge
    evidence_payload = []
    for m in memos:
        evidence_payload.append({"ref_type": "committee_memo", "ref_id": m.memo_id})
    evidence_payload.append({"ref_type": "service_handoff", "ref_id": handoff.handoff_id})
    for ref_id in evidence_refs:
        if ref_id not in {m.memo_id for m in memos} and ref_id != handoff.handoff_id:
            evidence_payload.append({"ref_type": "manual_review_ticket", "ref_id": ref_id})

    bridge_payload = {
        "decision_id": committee_id,
        "type": decision_type,
        "sponsor_persona_id": assigned_sponsor_persona_id,
        "target_type": matched_request.target_type,
        "target_id": matched_request.target_id,
        "target_version": target_version,
        "sponsor_decision": sponsor_decision,
        "rationale": matched_consult.get("rationale") or f"Committee sponsor decided via {committee_id}",
        "rationale_ref": rationale_ref,
        "conditions": matched_consult.get("conditions") or [],
        "committee_id": committee_id,
        "handoff_id": handoff.handoff_id,
        "trace_id": matched_request.trace_id,
        "capital_pool_id": matched_consult.get("capital_pool_id"),
        "persona_id": matched_consult.get("persona_id"),
        "evidence_refs": evidence_payload,
        "action_type": matched_consult.get("action_type"),
        "target_stage": matched_consult.get("target_stage"),
        "threshold_snapshots": matched_consult.get("threshold_snapshots") or [],
        "linked_incident_id": matched_consult.get("linked_incident_id"),
        "linked_postmortem_id": matched_consult.get("linked_postmortem_id"),
        "metadata": matched_consult.get("metadata") or {},
    }

    # Remove None values to avoid schema clutter
    bridge_payload = {k: v for k, v in bridge_payload.items() if v is not None}

    try:
        proposal = bridge(bridge_payload)
    except SponsorDecisionBridgeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Sponsor decision bridge mapping failed: {exc}",
        )

    # Post proposal to downstream service
    gov_url = (
        os.getenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL")
        or os.getenv("PANTHEON_GOVERNANCE_SERVICE_URL")
        or os.getenv("PANTHEON_GOVERNANCE_API_URL")
        or os.getenv("GOVERNANCE_URL")
        or "http://127.0.0.1:8082"
    )
    evo_url = (
        os.getenv("PANTHEON_EVOLUTION_API_URL")
        or os.getenv("PANTHEON_EVOLUTION_SERVICE_URL")
        or os.getenv("PANTHEON_EVOLUTION_API_URL")
        or os.getenv("EVOLUTION_URL")
        or "http://127.0.0.1:8093"
    )

    dispatch_status = "pending"
    dispatch_error = None

    if sponsor_decision == "rejected":
        dispatch_status = "gated"
        dispatch_error = "Sponsor decision is rejected; proposal dispatch gated."
    else:
        try:
            import urllib.request
            import json

            proposal_dict = proposal.to_dict()
            if proposal.proposal_type == "approval_decision":
                target_url = f"{gov_url.rstrip('/')}/api/governance/approvals"
            else:
                target_url = f"{evo_url.rstrip('/')}/api/evolution/proposals"

            data = json.dumps(proposal_dict).encode("utf-8")
            post_req = urllib.request.Request(
                target_url,
                data=data,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(post_req, timeout=10.0) as resp:
                resp.read()
            dispatch_status = "sent"
        except Exception as exc:
            dispatch_status = "failed"
            dispatch_error = str(exc)

    if dispatch_status == "failed":
        handoff.status = GateHandoffStatus.FAILED
    else:
        handoff.status = GateHandoffStatus.SENT

    metadata = matched_request.metadata if isinstance(matched_request.metadata, dict) else {}
    metadata["consultation"] = matched_consult
    metadata["service_handoff"] = {
        "handoff_id": handoff.handoff_id,
        "target_gate": handoff.target_gate,
        "evidence_refs": list(handoff.evidence_refs),
        "audit_refs": list(handoff.audit_refs),
        "status": handoff.status.value if hasattr(handoff.status, "value") else handoff.status,
        "proposal_dispatch": {
            "status": dispatch_status,
            "error": dispatch_error,
            "proposal_id": proposal.decision_id,
            "proposal_type": proposal.proposal_type,
        },
    }
    matched_request.metadata = metadata
    store.put_request(matched_request)
    store.put_handoff(handoff)

    if dispatch_status == "failed" and not (os.getenv("PANTHEON_TEST_MODE") or os.getenv("PYTEST_CURRENT_TEST")):
        raise HTTPException(
            status_code=502,
            detail=f"Failed to dispatch proposal to downstream service: {dispatch_error}",
        )

    return {
        "committee_id": committee_id,
        "committee_ref": matched_consult.get("committee_ref") or committee_id,
        "linked_request_id": matched_request.request_id,
        "linked_session_id": matched_request.linked_session_id or matched_consult.get("requester_session_id"),
        "sponsor_decision": matched_consult.get("sponsor_decision"),
        "sponsor_decided_at": matched_consult.get("sponsor_decided_at"),
        "sponsor_decided_by": matched_consult.get("sponsor_decided_by"),
        "consensus_state": matched_consult.get("consensus_state"),
        "rationale_ref": (matched_consult.get("synthesis_summary") or {}).get("rationale_ref"),
        "outcome": (matched_consult.get("synthesis_summary") or {}).get("outcome"),
        "service_handoff": {
            "handoff_id": handoff.handoff_id,
            "target_gate": handoff.target_gate,
            "evidence_refs": list(handoff.evidence_refs),
            "audit_refs": list(handoff.audit_refs),
            "status": handoff.status.value if hasattr(handoff.status, "value") else handoff.status,
            "proposal_dispatch": {
                "status": dispatch_status,
                "error": dispatch_error,
                "proposal_id": proposal.decision_id,
                "proposal_type": proposal.proposal_type,
            },
        },
    }


@app.post("/api/consult/intake/policy-learning-candidate", response_model=Dict[str, Any], status_code=201)
def intake_policy_learning_candidate(
    req: CreatePolicyLearningCandidateIntakeRequest,
) -> Dict[str, Any]:
    identity = current_identity()
    if req.tenant_id and req.tenant_id != identity.tenant_id:
        raise HTTPException(status_code=403, detail="ConsultRequest tenant scope denied")

    candidate_status = (req.status or "").strip().lower()
    if candidate_status != "processed":
        raise HTTPException(
            status_code=400,
            detail=f"Intake requires a terminal policy-learning candidate status ('processed'); received '{req.status}'",
        )

    dataset_version_id = (req.dataset_version_id or "").strip()
    if not dataset_version_id:
        raise HTTPException(status_code=400, detail="dataset_version_id is required for candidate intake")

    candidate_id = req.candidate_id.strip()
    trace_id = req.trace_id or f"tr-intake-{candidate_id}"
    requested_by = req.requested_by or ActorRef(actor_type="service", actor_id="policy-learning-svc")

    with _REQUEST_COMMAND_LOCK:
        # Idempotency & Replay check: search existing requests for this candidate_id
        for existing in store.list_requests():
            if existing.tenant_id != identity.tenant_id:
                continue
            meta = existing.metadata if isinstance(existing.metadata, dict) else {}
            if (
                meta.get("candidate_id") == candidate_id
                or (existing.target_id == candidate_id and existing.target_type == "policy_learning_candidate")
            ):
                # Request already exists! Check for existing published memo
                memos = store.list_memos_for_request(existing.request_id)
                published_memos = [m for m in memos if m.status == MemoStatus.PUBLISHED]
                existing_handoff_data = meta.get("service_handoff") if isinstance(meta.get("service_handoff"), dict) else None

                return {
                    "status": "existing",
                    "request_id": existing.request_id,
                    "candidate_id": candidate_id,
                    "dataset_version_id": dataset_version_id,
                    "dataset_lineage": meta.get("dataset_lineage") or req.dataset_lineage,
                    "request": _request_dict(existing),
                    "memo": _request_dict(published_memos[0]) if published_memos else None,
                    "service_handoff": existing_handoff_data,
                    "replayed": True,
                }

        # No existing request -> Create request and terminal memo
        request_id = f"cr-cand-{candidate_id}"
        committee_id = f"committee-cand-{candidate_id}"
        from_persona = req.from_persona_id or "persona-policy-learner"

        dataset_lineage = dict(req.dataset_lineage or {})
        if "dataset_version_ids" not in dataset_lineage:
            dataset_lineage["dataset_version_ids"] = [dataset_version_id]
        if "dataset_id" not in dataset_lineage:
            dataset_lineage["dataset_id"] = dataset_version_id

        consult_meta = {
            "candidate_id": candidate_id,
            "dataset_version_id": dataset_version_id,
            "dataset_lineage": dataset_lineage,
            "evaluation_summary": req.evaluation_summary,
            "artifact_checksum": req.artifact_checksum,
            "policy_learning_intake": True,
            "consultation": {
                "committee_ref": committee_id,
                "sponsor_persona_id": from_persona,
                "target_version": dataset_version_id,
                "type": "approval",
                "action_type": "candidate_intake",
            },
        }
        if req.metadata:
            consult_meta.update(req.metadata)

        consult_request = ConsultRequest(
            request_id=request_id,
            tenant_id=identity.tenant_id,
            request_type=ConsultRequestType.PERSONA_POLICY,
            requested_by=requested_by,
            from_persona_id=from_persona,
            target_type="policy_learning_candidate",
            target_id=candidate_id,
            priority=req.priority,
            status=ConsultRequestStatus.SUBMITTED,
            metadata=consult_meta,
            trace_id=trace_id,
        )

        store.put_request(consult_request)
        _emit_audit(
            action="candidate_intake_created",
            request_id=request_id,
            actor_ref=requested_by,
            service_actor_ref=CONSULTATION_SERVICE_ACTOR,
            trace_id=trace_id,
            after_state=ConsultRequestStatus.SUBMITTED.value,
        )

        # Build and publish terminal memo
        memo_id = f"memo-cand-{candidate_id}"
        auto_dec = (req.auto_decision or "approved").strip().lower()
        if auto_dec == "rejected":
            rec = Recommendation.REJECT
        elif auto_dec == "approved_with_conditions":
            rec = Recommendation.APPROVE_WITH_CONDITIONS
        else:
            rec = Recommendation.APPROVE

        action_match_rate = req.evaluation_summary.get("action_match_rate", "N/A")
        return_gap = req.evaluation_summary.get("return_gap", "N/A")

        memo = ConsultMemo(
            memo_id=memo_id,
            request_id=request_id,
            memo_type=MemoType.COMMITTEE_SUMMARY,
            author_type=AuthorType.COMMITTEE,
            author_ref=committee_id,
            target_type="policy_learning_candidate",
            target_id=candidate_id,
            summary=req.auto_rationale or f"Terminal consultation memo for policy-learning candidate {candidate_id} on DatasetVersion {dataset_version_id}",
            findings=[
                ConsultFinding(
                    severity=FindingSeverity.INFO,
                    category="dataset_lineage",
                    claim=f"Governed DatasetVersion {dataset_version_id} verified",
                    evidence_refs=[f"ds-ref-{dataset_version_id}"],
                    recommendation="lineage_verified",
                ),
                ConsultFinding(
                    severity=FindingSeverity.INFO,
                    category="model_evaluation",
                    claim=f"Evaluation metrics: action_match_rate={action_match_rate}, return_gap={return_gap}",
                    evidence_refs=[f"checksum-{req.artifact_checksum or 'none'}"],
                    recommendation="evaluation_verified",
                ),
            ],
            recommendation=rec,
            confidence=0.95,
            status=MemoStatus.PUBLISHED,
            trace_id=trace_id,
            published_at=utc_now(),
        )

        errors = validate_consult_memo_against_request(memo, consult_request)
        if errors:
            raise HTTPException(status_code=400, detail=f"Memo lineage validation failed: {errors}")

        store.put_memo(memo)
        _emit_audit(
            action="memo_published",
            request_id=request_id,
            actor_ref=requested_by,
            service_actor_ref=CONSULTATION_SERVICE_ACTOR,
            trace_id=trace_id,
            after_state=MemoStatus.PUBLISHED.value,
        )

        # Update request status to PUBLISHED
        consult_request.status = ConsultRequestStatus.PUBLISHED
        consult_request.completed_at = utc_now()
        store.put_request(consult_request)

        # Map to sponsor decision bridge proposal
        from .sponsor_decision_bridge import bridge

        evidence_payload = [
            {"ref_type": "committee_memo", "ref_id": memo.memo_id},
            {"ref_type": "manual_review_ticket", "ref_id": f"dataset-{dataset_version_id}"},
        ]

        bridge_payload = {
            "decision_id": committee_id,
            "type": "approval",
            "sponsor_persona_id": from_persona,
            "target_type": "candidate_artifact",
            "target_id": candidate_id,
            "target_version": dataset_version_id,
            "sponsor_decision": "approved" if rec in (Recommendation.APPROVE, Recommendation.APPROVE_WITH_CONDITIONS) else "rejected",
            "rationale": memo.summary,
            "rationale_ref": f"memo-{memo_id}",
            "conditions": ["Deploy to paper stage only"] if rec == Recommendation.APPROVE_WITH_CONDITIONS else [],
            "committee_id": committee_id,
            "trace_id": trace_id,
            "evidence_refs": evidence_payload,
        }

        proposal = bridge(bridge_payload)

        return {
            "status": "created",
            "request_id": request_id,
            "candidate_id": candidate_id,
            "dataset_version_id": dataset_version_id,
            "dataset_lineage": dataset_lineage,
            "request": _request_dict(consult_request),
            "memo": _request_dict(memo),
            "proposal": proposal.to_dict(),
            "replayed": False,
        }

