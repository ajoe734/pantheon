from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException

from .models import (
    ActorRef,
    AssignParticipantRequest,
    AttachEvidenceRequest,
    ConsultAuditEvent,
    ConsultEvidenceAttachment,
    ConsultGateHandoff,
    ConsultMemo,
    ConsultParticipant,
    ConsultRequest,
    ConsultRequestStatus,
    ConsultTranscript,
    CreateConsultRequest,
    CreateGateHandoffRequest,
    MemoStatus,
    PostTranscriptEventRequest,
    SubmitMemoRequest,
    TranscriptEvent,
    utc_now,
)
from .store import ConsultationStore


app = FastAPI(title="Pantheon Consultation Service", version="0.1.0")

DATA_DIR = os.getenv("CONSULTATION_DATA_DIR", "/tmp/pantheon/consultation")
store = ConsultationStore(DATA_DIR)


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
) -> ConsultAuditEvent:
    event = ConsultAuditEvent(
        audit_id=f"aud-{uuid.uuid4().hex[:12]}",
        request_id=request_id,
        actor_ref=_actor_to_data(actor_ref),
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
    if not request:
        raise HTTPException(status_code=404, detail="ConsultRequest not found")
    return request


def _request_dict(req: Any, exclude: Optional[set[str]] = None) -> Dict[str, Any]:
    if hasattr(req, "model_dump"):
        return req.model_dump(exclude=exclude or set(), mode="json")
    return req.dict(exclude=exclude or set())


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "consultation"}


# --- Consult Requests ---


@app.post("/api/consult/requests", response_model=ConsultRequest, status_code=201)
def create_request(req: CreateConsultRequest) -> ConsultRequest:
    request_id = f"cr-{uuid.uuid4().hex[:12]}"
    new_req = ConsultRequest(request_id=request_id, **_request_dict(req))
    store.put_request(new_req)
    _emit_audit(
        action="request_created",
        request_id=request_id,
        actor_ref=req.requested_by,
        trace_id=req.trace_id,
        after_state=ConsultRequestStatus.DRAFT.value,
    )
    return new_req


@app.get("/api/consult/requests", response_model=List[ConsultRequest])
def list_requests(
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    status: Optional[ConsultRequestStatus] = None,
) -> List[ConsultRequest]:
    requests = store.list_requests()
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
    request = _get_request_or_404(request_id)
    if request.status != ConsultRequestStatus.DRAFT:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot submit request in {request.status.value} state",
        )

    before_state = request.status.value
    request.status = ConsultRequestStatus.SUBMITTED
    store.put_request(request)
    _emit_audit(
        action="request_submitted",
        request_id=request_id,
        actor_ref=request.requested_by,
        trace_id=request.trace_id,
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
    request = _get_request_or_404(request_id)
    participant = ConsultParticipant(
        participant_id=f"cp-{uuid.uuid4().hex[:12]}",
        request_id=request_id,
        participant_type=req.participant_type,
        participant_ref=req.participant_ref,
        role=req.role,
    )
    store.put_participant(participant)

    before_state = request.status.value
    if request.status == ConsultRequestStatus.SUBMITTED:
        request.status = ConsultRequestStatus.ASSIGNED
        store.put_request(request)

    _emit_audit(
        action="participant_assigned",
        request_id=request_id,
        actor_ref={"actor_type": "system", "actor_id": "consultation-svc"},
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
    request = _get_request_or_404(request_id)
    attachment = ConsultEvidenceAttachment(
        attachment_id=f"cea-{uuid.uuid4().hex[:12]}",
        request_id=request_id,
        evidence_ref=req.evidence_ref,
        attached_by=req.attached_by,
        trace_id=req.trace_id,
    )
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
    request = _get_request_or_404(request_id)
    if req.request_id != request_id:
        raise HTTPException(status_code=400, detail="Path request_id does not match payload")

    transcript = store.get_transcript(request_id)
    next_seq = len(transcript.events) + 1 if transcript else 1

    event = TranscriptEvent(
        event_id=f"evt-{uuid.uuid4().hex[:12]}",
        session_id=request_id,
        sequence_no=next_seq,
        **_request_dict(req, exclude={"request_id"}),
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


@app.post("/api/consult/memos", response_model=ConsultMemo, status_code=201)
def submit_memo(req: SubmitMemoRequest) -> ConsultMemo:
    request = _get_request_or_404(req.request_id)

    memo_id = f"mem-{uuid.uuid4().hex[:12]}"
    new_memo = ConsultMemo(
        memo_id=memo_id,
        target_type=request.target_type,
        target_id=request.target_id,
        status=MemoStatus.SUBMITTED,
        **_request_dict(req),
    )
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
    if not memo:
        raise HTTPException(status_code=404, detail="ConsultMemo not found")
    return memo


@app.post("/api/consult/memos/{memo_id}/publish", response_model=ConsultMemo)
def publish_memo(memo_id: str) -> ConsultMemo:
    memo = store.get_memo(memo_id)
    if not memo:
        raise HTTPException(status_code=404, detail="ConsultMemo not found")
    if memo.status == MemoStatus.PUBLISHED:
        return memo

    request = _get_request_or_404(memo.request_id)
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
    return store.list_memos_for_target(target_type, target_id)


# --- Governance Gate Handoffs ---


@app.post("/api/consult/handoffs", response_model=ConsultGateHandoff, status_code=201)
def create_handoff(req: CreateGateHandoffRequest) -> ConsultGateHandoff:
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

    attached_evidence_refs = [
        attachment.evidence_ref.id
        for attachment in store.list_evidence_for_request(req.request_id)
    ]
    evidence_refs = sorted(set(request.evidence_refs + attached_evidence_refs + req.evidence_refs))
    audit_refs = [event.audit_id for event in store.list_audit_for_request(req.request_id)]

    handoff = ConsultGateHandoff(
        handoff_id=f"gh-{uuid.uuid4().hex[:12]}",
        request_id=req.request_id,
        target_gate=req.target_gate,
        memo_ids=req.memo_ids,
        evidence_refs=evidence_refs,
        audit_refs=audit_refs,
        trace_id=req.trace_id,
    )
    store.put_handoff(handoff)

    handoff_audit = _emit_audit(
        action="gate_handoff_created",
        request_id=req.request_id,
        actor_ref={"actor_type": "system", "actor_id": "consultation-svc"},
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
    return handoff


@app.get(
    "/api/consult/requests/{request_id}/handoffs",
    response_model=List[ConsultGateHandoff],
)
def list_handoffs_for_request(request_id: str) -> List[ConsultGateHandoff]:
    _get_request_or_404(request_id)
    return store.list_handoffs_for_request(request_id)
