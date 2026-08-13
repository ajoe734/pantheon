from __future__ import annotations

import json
import math
import os
import uuid
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from evaluation_authority import (
    AuthorityValidationError,
    EvaluationAuthoritySnapshot,
    load_evaluation_authority,
    stable_json_sha256,
)
from inbound_authority import (
    TrainingInboundAuthority,
    TrainingInboundAuthorityError,
    authenticate_training_request,
    authority_configuration_health,
    current_authority,
    reset_current_authority,
    set_current_authority,
)
from services.knowledge.evidence.runtime_log import RuntimeEvidenceLog
from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture
from services.research.vectorbt.adapter.vectorbt_adapter import (
    BacktestConfig,
    PRIMARY_BACKEND,
    VECTORBT_VERSION_PIN,
    VectorbtBackend,
    VectorbtWorkflowError,
    run_vectorbt_workflow,
)
from models import TeachingEvent, TeachingEvaluationResult, TeachingSession
from consultation_client import TrainingConsultationClient
from persona_target import (
    PersonaTargetError,
    commit_persona_target,
    read_persona_target_precondition,
)
from source_dataset_authority import (
    SourceDatasetAuthorityError,
    materialize_source_dataset_version,
    urllib_json_get,
)
from policy_lineage import (
    PolicyLineageError,
    build_lineage_read_store,
    record_commit as record_policy_lineage_commit,
)
from store import TrainingSessionStore, build_training_session_store


def _trusted_now() -> datetime:
    """Return the service-owned evaluation clock (never a request timestamp)."""

    return datetime.now(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_now() -> str:
    return _utc_iso(_trusted_now())


def _data_dir() -> str:
    return os.getenv("TRAINING_SESSION_DATA_DIR", "/tmp/pantheon/training-session")


def _session_id(timestamp: str, existing: set[str]) -> str:
    prefix = timestamp[:10].replace("-", "")
    candidate = f"trn-{prefix}-{uuid.uuid4().hex[:12]}"
    while candidate in existing:
        candidate = f"trn-{prefix}-{uuid.uuid4().hex[:12]}"
    return candidate


def _request_authority() -> TrainingInboundAuthority:
    try:
        return current_authority()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail="training authority context is unavailable") from exc


def _tenant_id_for(record: Dict[str, Any]) -> str:
    tenant_id = str(record.get("tenant_id") or "").strip()
    if tenant_id:
        return tenant_id
    return _request_authority().tenant_id


def _require_tenant_record(record: Dict[str, Any], not_found_detail: str) -> Dict[str, Any]:
    if str(record.get("tenant_id") or "").strip() != _request_authority().tenant_id:
        # Deliberately return 404 so callers cannot enumerate another tenant's
        # session, job, preview, controls, or replay identifiers.
        raise HTTPException(status_code=404, detail=not_found_detail)
    return record


def _tenant_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    tenant_id = _request_authority().tenant_id
    return [
        record
        for record in records
        if str(record.get("tenant_id") or "").strip() == tenant_id
    ]


def _trusted_actor_id(declared_actor_id: Optional[str] = None) -> str:
    authority = _request_authority()
    if authority.token_kind == "test-disabled":
        return str(declared_actor_id or "").strip() or authority.actor_id
    trusted = authority.delegated_actor_id or authority.actor_id
    declared = str(declared_actor_id or "").strip()
    if declared and declared not in {"operator", trusted}:
        raise HTTPException(
            status_code=403,
            detail="declared actor does not match the verified delegated actor",
        )
    return trusted


def _next_event_id(timestamp: str, events: List[Dict[str, Any]], session_id: str) -> str:
    prefix = timestamp[:10].replace("-", "")
    session_token = sha256(str(session_id or "").encode("utf-8")).hexdigest()[:10]
    existing_ids = {str(event.get("event_id") or "") for event in events if isinstance(event, dict)}
    next_sequence = max((int(event.get("sequence_number") or 0) for event in events), default=0) + 1
    event_id = f"tevt-{prefix}-{session_token}-{next_sequence:03d}"
    while event_id in existing_ids:
        next_sequence += 1
        event_id = f"tevt-{prefix}-{session_token}-{next_sequence:03d}"
    return event_id


def _replay_candidate_snapshot_at(replay: Dict[str, Any]) -> Optional[str]:
    for event in reversed(list(replay.get("events") or [])):
        if not isinstance(event, dict):
            continue
        eval_ref = event.get("eval_ref")
        if isinstance(eval_ref, dict) and eval_ref.get("candidate_snapshot_at"):
            return str(eval_ref["candidate_snapshot_at"])
    return None


def _replay_eval_ref(replay: Dict[str, Any]) -> Dict[str, Any]:
    for event in reversed(list(replay.get("events") or [])):
        if not isinstance(event, dict):
            continue
        eval_ref = event.get("eval_ref")
        if isinstance(eval_ref, dict) and eval_ref:
            return dict(eval_ref)
    return {}


def _stable_hash(payload: Dict[str, Any]) -> str:
    return sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()


def _preview_job_id(session_id: str, idempotency_key: Optional[str] = None) -> str:
    if idempotency_key:
        digest = sha256(f"{session_id}\0{idempotency_key}".encode("utf-8")).hexdigest()[:16]
        return f"pvjob-{digest}"
    return f"pvjob-{uuid.uuid4().hex[:12]}"


def _replay_decision_hash(session_id: str, body: "ReplayDecisionBody", state: str) -> str:
    return _stable_hash(
        {
            "session_id": session_id,
            "state": state,
            "expected_candidate_snapshot_at": body.expected_candidate_snapshot_at,
            "actor_id": body.actor_id,
            "note": body.note,
        }
    )


def _teaching_event_ids(replay: Dict[str, Any]) -> List[str]:
    event_ids: List[str] = []
    for event in replay.get("events") or []:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id") or "").strip()
        if event_id:
            event_ids.append(event_id)
    return event_ids


def _mark_replay_idempotency(
    replay: Dict[str, Any],
    *,
    idempotency_key: Optional[str],
    request_hash: Optional[str],
    replayed: bool,
) -> Dict[str, Any]:
    if not idempotency_key:
        return replay
    resolution = replay.setdefault("replay_resolution", {})
    resolution["idempotency"] = {
        "key": idempotency_key,
        "request_hash": request_hash,
        "replayed": replayed,
    }
    return replay


def _idempotency_key(primary: Optional[str], alias: Optional[str]) -> Optional[str]:
    normalized = str(primary or "").strip()
    if normalized:
        return normalized
    normalized = str(alias or "").strip()
    return normalized or None


def _control_patch_error(control: Dict[str, Any], value: Any) -> Optional[Dict[str, Any]]:
    allowed = control.get("allowed_range")
    if isinstance(allowed, dict) and isinstance(value, (int, float)):
        minimum = allowed.get("min")
        maximum = allowed.get("max")
        if isinstance(minimum, (int, float)) and value < minimum:
            return {"field": control.get("parameter_key"), "reason": "below_minimum", "allowed_range": allowed}
        if isinstance(maximum, (int, float)) and value > maximum:
            return {"field": control.get("parameter_key"), "reason": "above_maximum", "allowed_range": allowed}
    allowed_values = control.get("allowed_values")
    if isinstance(allowed_values, list) and allowed_values and value not in allowed_values:
        return {"field": control.get("parameter_key"), "reason": "outside_allowed_values", "allowed_values": allowed_values}
    return None


def _actor_type_from_actor(actor: Optional[str]) -> str:
    normalized = str(actor or "").strip().lower()
    if normalized in {"persona", "assistant"}:
        return "persona"
    if normalized in {"system", "service", "training-session", "training-session-svc"}:
        return "service"
    return "user"


def _teaching_event_payload(
    *,
    message_body: Optional[str] = None,
    summary: Optional[str] = None,
    outcome_signal: Optional[str] = None,
    evidence_ref: Optional[Dict[str, Any]] = None,
    patch_delta: Optional[List[Dict[str, Any]]] = None,
    eval_ref: Optional[Dict[str, Any]] = None,
    artifact_refs: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = dict(payload or {})
    for key, value in (
        ("message_body", message_body),
        ("summary", summary),
        ("outcome_signal", outcome_signal),
        ("evidence_ref", evidence_ref),
        ("patch_delta", patch_delta),
        ("eval_ref", eval_ref),
        ("artifact_refs", artifact_refs),
    ):
        if value is not None:
            normalized[key] = value
    return normalized


def _build_teaching_event(
    *,
    session_id: str,
    tenant_id: str,
    event_id: str,
    event_type: str,
    actor: str,
    timestamp: str,
    sequence_number: int,
    actor_type: Optional[str] = None,
    actor_label: Optional[str] = None,
    message_body: Optional[str] = None,
    summary: Optional[str] = None,
    outcome_signal: Optional[str] = None,
    evidence_ref: Optional[Dict[str, Any]] = None,
    patch_delta: Optional[List[Dict[str, Any]]] = None,
    eval_ref: Optional[Dict[str, Any]] = None,
    artifact_refs: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    event_payload = _teaching_event_payload(
        message_body=message_body,
        summary=summary,
        outcome_signal=outcome_signal,
        evidence_ref=evidence_ref,
        patch_delta=patch_delta,
        eval_ref=eval_ref,
        artifact_refs=artifact_refs,
        payload=payload,
    )
    return TeachingEvent.from_dict(
        {
            "event_id": event_id,
            "session_id": session_id,
            "tenant_id": tenant_id,
            "actor": actor,
            "actor_type": actor_type or _actor_type_from_actor(actor),
            "actor_label": actor_label,
            "event_type": event_type,
            "payload": event_payload,
            "message_body": message_body,
            "summary": summary,
            "timestamp": timestamp,
            "emitted_at": timestamp,
            "correlation_id": correlation_id or f"{session_id}:{event_id}",
            "sequence_number": sequence_number,
            "outcome_signal": outcome_signal,
            "evidence_ref": evidence_ref,
            "patch_delta": patch_delta,
            "eval_ref": eval_ref,
            "artifact_refs": artifact_refs,
        }
    ).to_dict()


def _teaching_session_contract(session: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(session.get("session_id") or session.get("id") or "").strip()
    contract = {
        "id": session.get("id") or session_id,
        "session_id": session_id,
        "persona_id": session.get("persona_id"),
        "tenant_id": _tenant_id_for(session),
        "opened_by": session.get("opened_by") or session.get("operator_id") or "system",
        "mode": session.get("mode") or "coaching",
        "session_type": session.get("session_type") or "trainer",
        "objective": session.get("objective") or session.get("topic"),
        "topic": session.get("topic") or session.get("objective"),
        "status": session.get("status") or "active",
        "started_at": session.get("started_at") or session.get("created_at"),
        "ended_at": session.get("ended_at") or session.get("completed_at"),
        "current_control_state_ref": session.get("current_control_state_ref"),
        "trace_id": session.get("trace_id") or f"trace-{session_id or uuid.uuid4().hex[:12]}",
        "context_refs": session.get("context_refs") if isinstance(session.get("context_refs"), list) else [],
        "actor_context": session.get("actor_context") if isinstance(session.get("actor_context"), dict) else {},
        "events": session.get("events") if isinstance(session.get("events"), list) else [],
        "outcomes": session.get("outcomes") if isinstance(session.get("outcomes"), list) else [],
    }
    for key in ("replay_resolution", "artifacts", "metadata"):
        if isinstance(session.get(key), dict):
            contract[key] = session[key]
    return TeachingSession.from_dict(contract).to_dict()


class CreateSessionBody(BaseModel):
    persona_id: str
    objective: str
    mode: str = "coaching"
    context_refs: List[Dict[str, Any]] = Field(default_factory=list)
    actor_id: str = "operator"
    current_control_state_ref: Optional[str] = None
    trace_id: Optional[str] = None
    created_at: Optional[str] = None


class AppendEventBody(BaseModel):
    actor: str = "operator"
    actor_type: Optional[str] = None
    actor_label: Optional[str] = None
    event_type: str = "message"
    message_body: Optional[str] = None
    summary: Optional[str] = None
    outcome_signal: Optional[str] = None
    evidence_ref: Optional[Dict[str, Any]] = None
    patch_delta: Optional[List[Dict[str, Any]]] = None
    eval_ref: Optional[Dict[str, Any]] = None
    artifact_refs: Optional[Dict[str, Any]] = None
    payload: Optional[Dict[str, Any]] = None
    correlation_id: Optional[str] = None
    emitted_at: Optional[str] = None


class PatchControlsBody(BaseModel):
    patches: List[Dict[str, Any]]
    patched_at: Optional[str] = None


class RefreshPreviewBody(BaseModel):
    mode: str = "refresh"
    refreshed_at: Optional[str] = None


class QueuePreviewJobBody(BaseModel):
    mode: str = "refresh"
    requested_by: str = "operator"
    requested_at: Optional[str] = None
    terminalize_session: bool = False


class RunPreviewJobBody(BaseModel):
    """Worker trigger body; the service clock is the only evaluation clock."""

    model_config = ConfigDict(extra="forbid")


class ReplayDecisionBody(BaseModel):
    expected_candidate_snapshot_at: Optional[str] = None
    actor_id: str = "operator"
    note: Optional[str] = None
    decided_at: Optional[str] = None


app = FastAPI(title="Pantheon Training Session Service", version="0.2.0")
STORE_BACKEND = os.getenv("TRAINING_SESSION_EVENT_STORE_BACKEND", "jsonl").strip().lower() or "jsonl"
PERSISTENCE_POSTURE = require_persistence_posture("training-session")
store = build_training_session_store(_data_dir())


def _functional_health() -> Dict[str, Any]:
    try:
        results = store.list_functional_results()
    except Exception as exc:  # noqa: BLE001 - health must degrade, never crash.
        return {
            "status": "error",
            "result_count": 0,
            "failure_count": 1,
            "error": type(exc).__name__,
        }
    failures = [
        result
        for result in results
        if str(result.get("status") or "").lower() not in {"ok", "passed", "completed"}
    ]
    return {
        "status": "degraded" if failures else "ok",
        "result_count": len(results),
        "failure_count": len(failures),
        "failures": failures[-10:],
    }


def _service_metrics() -> Dict[str, int]:
    try:
        return {
            "session_count": len(store.list_sessions()),
            "event_log_count": len(store.list_event_log()),
        }
    except Exception:  # noqa: BLE001 - the storage dependency carries detail.
        return {"session_count": 0, "event_log_count": 0}


def _record_functional_result(
    operation: str,
    *,
    status: str,
    detail: Dict[str, Any],
) -> Dict[str, Any]:
    authority = _request_authority()
    return store.put_functional_result(
        operation,
        authority.tenant_id,
        {
            "status": status,
            "checked_at": utc_now(),
            "actor_service": authority.actor_service,
            "detail": detail,
        },
    )


@app.middleware("http")
async def enforce_training_inbound_authority(request: Request, call_next):
    if not request.url.path.startswith("/api/training/"):
        return await call_next(request)
    try:
        authority = authenticate_training_request(
            authorization=request.headers.get("Authorization"),
            mfa_token=request.headers.get("X-MFA-Token"),
            tenant_id=request.headers.get("X-Tenant-Id"),
            actor_service=request.headers.get("X-Pantheon-Service"),
            method=request.method,
            path=request.url.path,
            persistence_enforced=PERSISTENCE_POSTURE.enforced,
        )
    except TrainingInboundAuthorityError as exc:
        return JSONResponse(exc.to_dict(), status_code=exc.status_code)
    token = set_current_authority(authority)
    try:
        response = await call_next(request)
        response.headers["X-Pantheon-Tenant"] = authority.tenant_id
        return response
    finally:
        reset_current_authority(token)


def get_store():
    return store
register_fastapi_health_routes(
    app,
    "training-session",
    dependencies=lambda: {
        "persistence": PERSISTENCE_POSTURE.to_dict(),
        "storage": store.storage_health(),
        "inbound_authority": authority_configuration_health(
            persistence_enforced=PERSISTENCE_POSTURE.enforced
        ),
        "functional": _functional_health(),
    },
    metrics=_service_metrics,
    details=lambda: {
        "data_dir": _data_dir(),
        "store_backend": STORE_BACKEND,
        "persistence_posture": PERSISTENCE_POSTURE.to_dict(),
    },
)


@app.get("/health")
def health() -> Dict[str, Any]:
    functional = _functional_health()
    storage = store.storage_health()
    metrics = _service_metrics()
    return {
        "status": (
            "ok"
            if functional["status"] == "ok" and storage["status"] == "ok"
            else "degraded"
        ),
        "service": "training-session",
        "data_dir": _data_dir(),
        **metrics,
        "functional": functional,
        "storage": storage,
    }


@app.get("/api/training/sessions")
def list_sessions(persona_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    sessions = _tenant_records(store.list_sessions())
    if persona_id:
        sessions = [session for session in sessions if session.get("persona_id") == persona_id]
    if status:
        sessions = [session for session in sessions if str(session.get("status") or "").lower() == status.lower()]
    return sessions


@app.post("/api/training/sessions", status_code=201)
def create_session(body: CreateSessionBody) -> Dict[str, Any]:
    authority = _request_authority()
    timestamp = body.created_at or utc_now()
    existing_ids = {str(session.get("session_id") or "") for session in store.list_sessions()}
    session_id = _session_id(timestamp, existing_ids)
    session = _teaching_session_contract({
        "id": session_id,
        "session_id": session_id,
        "persona_id": body.persona_id,
        "tenant_id": authority.tenant_id,
        "session_type": "trainer",
        "mode": body.mode,
        "objective": body.objective,
        "topic": body.objective,
        "status": "active",
        "started_at": timestamp,
        "ended_at": None,
        "opened_by": _trusted_actor_id(body.actor_id),
        "current_control_state_ref": body.current_control_state_ref,
        "trace_id": body.trace_id or f"trace-{uuid.uuid4().hex[:12]}",
        "context_refs": body.context_refs,
        "actor_context": authority.actor_context(),
        "events": [],
        "outcomes": [],
    })
    store.put_session(session)
    store.put_controls(
        session_id,
        {
            "session_id": session_id,
            "tenant_id": authority.tenant_id,
            "controls": [],
        },
    )
    store.put_preview_bundle(
        session_id,
        {
            "session_id": session_id,
            "tenant_id": authority.tenant_id,
            "evaluations": {},
            "preview": {
                "eval_id": f"teval-{session_id.replace('trn-', '')}-001",
                "session_id": session_id,
                "status": "preview_unavailable",
                "baseline_snapshot_at": timestamp,
                "candidate_snapshot_at": timestamp,
                "metric_delta": {},
                "control_diff": [],
            },
        },
    )
    return session


@app.get("/api/training/sessions/{session_id}")
def get_session(session_id: str) -> Dict[str, Any]:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="training session not found")
    record = _require_tenant_record(session, "training session not found")
    bundle = store.get_preview_bundle(session_id) or {}
    preview = bundle.get("preview") if isinstance(bundle.get("preview"), dict) else {}
    eval_result = preview.get("evaluation_result") if isinstance(preview, dict) else None
    if eval_result:
        record["evaluation_result"] = eval_result
        record["eval_identity"] = eval_result.get("eval_identity")
    return record


@app.get("/api/training/sessions/{session_id}/readback")
@app.get("/api/training/sessions/{session_id}/terminal-readback")
def get_session_terminal_readback(session_id: str) -> Dict[str, Any]:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="training session not found")
    record = _require_tenant_record(session, "training session not found")
    bundle = store.get_preview_bundle(session_id) or {}
    preview = bundle.get("preview") if isinstance(bundle.get("preview"), dict) else {}
    eval_result = preview.get("evaluation_result") if isinstance(preview, dict) else None
    return {
        "session_id": session_id,
        "tenant_id": _tenant_id_for(record),
        "persona_id": record.get("persona_id"),
        "status": record.get("status"),
        "mode": record.get("mode"),
        "started_at": record.get("started_at"),
        "ended_at": record.get("ended_at"),
        "eval_identity": (eval_result or {}).get("eval_identity"),
        "evaluation_result": eval_result,
        "consultation_required": (eval_result or {}).get("consultation_required", False),
        "consult_request_id": (eval_result or {}).get("consult_request_id"),
        "consultation_receipt": (eval_result or {}).get("consultation_receipt"),
        "preview": preview,
        "events": record.get("events") or [],
    }


@app.post("/api/training/sessions/{session_id}/events", status_code=201)
def append_event(session_id: str, body: AppendEventBody) -> Dict[str, Any]:
    timestamp = body.emitted_at or utc_now()
    def build_event(session: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not session:
            raise HTTPException(status_code=404, detail="training session not found")
        _require_tenant_record(session, "training session not found")
        if str(session.get("status") or "").lower() != "active":
            raise HTTPException(status_code=409, detail="training session is not active")
        events = session.get("events") if isinstance(session.get("events"), list) else []
        event_id = _next_event_id(timestamp, events, session_id)
        sequence_number = (
            max((int(event.get("sequence_number") or 0) for event in events), default=0)
            + 1
        )
        return _build_teaching_event(
            session_id=session_id,
            tenant_id=_tenant_id_for(session),
            event_id=event_id,
            event_type=body.event_type,
            actor=_trusted_actor_id(body.actor),
            actor_type=(
                body.actor_type
                if _request_authority().token_kind == "test-disabled"
                else ("user" if _request_authority().delegated_actor_id else "service")
            ),
            actor_label=body.actor_label,
            message_body=body.message_body,
            summary=body.summary,
            timestamp=timestamp,
            sequence_number=sequence_number,
            outcome_signal=body.outcome_signal,
            evidence_ref=body.evidence_ref,
            patch_delta=body.patch_delta,
            eval_ref=body.eval_ref,
            artifact_refs=body.artifact_refs,
            payload=body.payload,
            correlation_id=body.correlation_id,
        )

    session, event = store.append_session_event(
        session_id,
        build_event,
        session_transform=_teaching_session_contract,
    )
    return {"accepted_at": timestamp, "event": event, "session": session}


@app.get("/api/training/sessions/{session_id}/events")
def list_events(session_id: str) -> List[Dict[str, Any]]:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="training session not found")
    _require_tenant_record(session, "training session not found")
    return store.list_event_log(session_id)


@app.get("/api/training/controls")
def list_controls(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    records = _tenant_records(store.list_controls())
    if session_id:
        records = [record for record in records if record.get("session_id") == session_id]
    return records


@app.get("/api/training/controls/{session_id}")
def get_controls(session_id: str) -> Dict[str, Any]:
    record = store.get_controls(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="trainer controls not found")
    return _require_tenant_record(record, "trainer controls not found")


@app.post("/api/training/sessions/{session_id}/controls")
@app.patch("/api/training/sessions/{session_id}/controls")
def patch_controls(session_id: str, body: PatchControlsBody) -> Dict[str, Any]:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="training session not found")
    _require_tenant_record(session, "training session not found")
    if str(session.get("status") or "").lower() != "active":
        raise HTTPException(status_code=409, detail="training session is not active")

    timestamp = body.patched_at or utc_now()
    record = store.get_controls(session_id) or {
        "session_id": session_id,
        "tenant_id": _tenant_id_for(session),
        "controls": [],
    }
    controls = list(record.get("controls") or [])
    by_key = {
        str(control.get("parameter_key") or ""): control
        for control in controls
        if isinstance(control, dict) and control.get("parameter_key")
    }
    field_errors: List[Dict[str, Any]] = []
    updated: List[Dict[str, Any]] = []
    for patch in body.patches:
        key = str(patch.get("parameter_key") or "").strip()
        value = patch.get("proposed_value")
        control = by_key.get(key)
        if not control:
            field_errors.append({"field": key, "reason": "unknown_parameter_key"})
            continue
        error = _control_patch_error(control, value)
        if error:
            field_errors.append(error)
            continue
        before = control.get("current_value")
        control["current_value"] = value
        control["last_modified_at"] = timestamp
        updated.append({"parameter_key": key, "previous_value": before, "new_value": value})
    if field_errors:
        return {"session_id": session_id, "status": "rejected", "field_errors": field_errors, "current_controls": controls}

    store.put_controls(
        session_id,
        {
            "session_id": session_id,
            "tenant_id": _tenant_id_for(session),
            "controls": controls,
        },
    )
    if updated:
        append_event(
            session_id,
            AppendEventBody(
                actor="operator",
                event_type="control_patch",
                summary="Trainer controls patched.",
                patch_delta=updated,
                emitted_at=timestamp,
            ),
        )
    return {"session_id": session_id, "status": "accepted", "controls": controls, "patch_delta": updated}


@app.get("/api/training/previews")
def list_previews(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    records = _tenant_records(store.list_previews())
    if session_id:
        records = [record for record in records if record.get("session_id") == session_id]
    return records


@app.get("/api/training/previews/{session_id}")
def get_preview(session_id: str) -> Dict[str, Any]:
    record = store.get_preview_bundle(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="trainer preview not found")
    return _require_tenant_record(record, "trainer preview not found")


def _runtime_evidence_log() -> RuntimeEvidenceLog:
    path = str(
        os.getenv("TRAINING_SESSION_RUNTIME_EVIDENCE_PATH")
        or Path(_data_dir()) / "runtime-evidence.jsonl"
    ).strip()
    if not path:
        raise RuntimeError("TRAINING_SESSION_RUNTIME_EVIDENCE_PATH is required")
    return RuntimeEvidenceLog(path, owner_service="training-session")


def _required_authority_path(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise AuthorityValidationError(f"{name} is required")
    return value


def _load_authority_snapshot(*, trusted_now: datetime, strategy_id: str) -> EvaluationAuthoritySnapshot:
    dataset_path = str(os.getenv("TRAINING_SESSION_CANONICAL_DATASET_PATH") or "").strip()
    authority_root: Optional[str] = None
    if not dataset_path:
        source_api_url = str(os.getenv("SOURCE_INGEST_API_URL") or "").strip()
        connector_id = str(os.getenv("TRAINING_SESSION_SOURCE_CONNECTOR_ID") or "").strip()
        dataset_id = str(os.getenv("TRAINING_SESSION_SOURCE_DATASET_ID") or "").strip()
        source_root = str(os.getenv("TRAINING_SESSION_SOURCE_VOLUME_ROOT") or "").strip()
        output_root = str(
            os.getenv("TRAINING_SESSION_DATASET_AUTHORITY_OUTPUT_ROOT")
            or Path(_data_dir()) / "dataset-authority"
        ).strip()
        if not all((source_api_url, connector_id, dataset_id, source_root, output_root)):
            raise AuthorityValidationError(
                "source DatasetVersion authority configuration is incomplete"
            )
        try:
            timeout = float(os.getenv("TRAINING_SESSION_SOURCE_AUTHORITY_TIMEOUT_SECONDS", "5"))
            materialized = materialize_source_dataset_version(
                http_get=lambda url: urllib_json_get(url, timeout_seconds=timeout),
                source_api_url=source_api_url,
                connector_id=connector_id,
                dataset_id=dataset_id,
                source_volume_root=source_root,
                output_root=output_root,
                trusted_now=trusted_now,
            )
        except (SourceDatasetAuthorityError, ValueError) as exc:
            raise AuthorityValidationError(f"source DatasetVersion authority rejected: {exc}") from exc
        dataset_path = str(materialized.path)
        authority_root = str(materialized.path.parent)
    return load_evaluation_authority(
        dataset_path,
        _required_authority_path("TRAINING_SESSION_THRESHOLD_POLICY_PATH"),
        trusted_now=trusted_now,
        strategy_id=strategy_id,
        authority_root=authority_root,
    )


def _persona_authority_token() -> str:
    token = str(os.getenv("TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN") or "").strip()
    if not token:
        raise PersonaTargetError("TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN is required")
    return token


def _persona_target_url(
    env_name: str,
    *,
    persona_id: str,
    tenant_id: str = "",
    session_id: str,
    approval_decision_ref: str = "",
    generation: Optional[int] = None,
) -> str:
    template = str(os.getenv(env_name) or "").strip()
    if not template:
        raise PersonaTargetError(f"{env_name} is required")
    try:
        return template.format(
            persona_id=quote(persona_id, safe=""),
            tenant_id=quote(tenant_id, safe=""),
            session_id=quote(session_id, safe=""),
            approval_decision_ref=quote(approval_decision_ref, safe=""),
            generation="" if generation is None else generation,
        )
    except (KeyError, ValueError) as exc:
        raise PersonaTargetError(f"{env_name} contains an invalid URL template") from exc


def _persona_target_timeout_seconds() -> float:
    try:
        value = float(os.getenv("TRAINING_SESSION_PERSONA_AUTHORITY_TIMEOUT_SECONDS", "5"))
    except ValueError as exc:
        raise PersonaTargetError("TRAINING_SESSION_PERSONA_AUTHORITY_TIMEOUT_SECONDS is invalid") from exc
    if not math.isfinite(value) or value <= 0:
        raise PersonaTargetError("TRAINING_SESSION_PERSONA_AUTHORITY_TIMEOUT_SECONDS must be positive")
    return value


def _candidate_approval_decision_ref(session_id: str, eval_id: str) -> str:
    return f"persona-teaching-approval:{session_id}:{eval_id}"


def _read_target_precondition(
    *,
    session: Dict[str, Any],
    session_id: str,
    trusted_now: datetime,
) -> Dict[str, Any]:
    persona_id = str(session.get("persona_id") or "").strip()
    tenant_id = str(session.get("tenant_id") or "").strip()
    if not persona_id:
        raise PersonaTargetError("training session persona_id is required")
    if not tenant_id:
        raise PersonaTargetError("training session tenant_id is required")
    return read_persona_target_precondition(
        persona_id=persona_id,
        tenant_id=tenant_id,
        persona_readback_url=_persona_target_url(
            "TRAINING_SESSION_PERSONA_READBACK_URL_TEMPLATE",
            persona_id=persona_id,
            tenant_id=tenant_id,
            session_id=session_id,
        ),
        authorization_token=_persona_authority_token(),
        trusted_now=trusted_now,
        timeout_seconds=_persona_target_timeout_seconds(),
    )


def _parse_utc_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a timezone-aware ISO 8601 timestamp")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise ValueError(f"{label} must be a timezone-aware ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be a timezone-aware ISO 8601 timestamp")
    return parsed.astimezone(timezone.utc)


def _canonical_controls(session_id: str) -> List[Dict[str, Any]]:
    controls_record = store.get_controls(session_id) or {}
    _require_tenant_record(controls_record, "trainer controls not found")
    raw_controls = controls_record.get("controls") or []
    if not isinstance(raw_controls, list) or not raw_controls:
        raise AuthorityValidationError("candidate has no governed controls to evaluate")
    controls: List[Dict[str, Any]] = []
    seen: set[str] = set()
    changed = False
    for index, raw in enumerate(raw_controls):
        if not isinstance(raw, dict):
            raise AuthorityValidationError(f"candidate control at index {index} must be an object")
        key = str(raw.get("parameter_key") or "").strip()
        if not key or key in seen:
            raise AuthorityValidationError("candidate controls require unique non-empty parameter_key values")
        seen.add(key)
        current = raw.get("current_value")
        baseline = raw.get("baseline_value", current)
        try:
            stable_json_sha256({"current": current, "baseline": baseline})
        except AuthorityValidationError as exc:
            raise AuthorityValidationError(f"candidate control {key!r} is not finite canonical JSON") from exc
        changed = changed or current != baseline
        controls.append(
            {
                "parameter_key": key,
                "baseline_value": baseline,
                "current_value": current,
            }
        )
    if not changed:
        raise AuthorityValidationError("candidate controls do not contain an effective change")
    return sorted(controls, key=lambda control: control["parameter_key"])


def _backtest_config(controls: List[Dict[str, Any]], requested_by: str) -> BacktestConfig:
    supported = {"short_window", "long_window", "strategy.short_window", "strategy.long_window"}
    unsupported = sorted(
        control["parameter_key"] for control in controls if control["parameter_key"] not in supported
    )
    if unsupported:
        raise AuthorityValidationError(
            "candidate controls are not bound to the vectorbt strategy: " + ", ".join(unsupported)
        )
    params: Dict[str, Any] = {}
    for control in controls:
        key = str(control["parameter_key"]).removeprefix("strategy.")
        value = control["current_value"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise AuthorityValidationError(f"candidate control {control['parameter_key']!r} must be a positive integer")
        params[key] = value
    short_window = int(params.get("short_window", 5))
    long_window = int(params.get("long_window", 20))
    if short_window >= long_window:
        raise AuthorityValidationError("candidate short_window must be less than long_window")
    return BacktestConfig(requested_by=requested_by, strategy_params=params)


def _finite_metric(metrics: Dict[str, Any], key: str) -> float:
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AuthorityValidationError(f"vectorbt metric {key} is missing or non-numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise AuthorityValidationError(f"vectorbt metric {key} is non-finite")
    return normalized


def _proof_digest(proof: Dict[str, Any]) -> str:
    unsigned = dict(proof)
    unsigned.pop("proof_digest", None)
    # The durable evidence record contains the proof digest, so its checksum is
    # intentionally an external binding rather than part of the digest itself.
    unsigned.pop("runtime_evidence", None)
    return stable_json_sha256(unsigned)


def _append_training_event(
    session: Dict[str, Any],
    *,
    event_type: str,
    actor: str,
    timestamp: str,
    actor_type: Optional[str] = None,
    actor_label: Optional[str] = None,
    summary: Optional[str] = None,
    outcome_signal: Optional[str] = None,
    evidence_ref: Optional[Dict[str, Any]] = None,
    patch_delta: Optional[List[Dict[str, Any]]] = None,
    eval_ref: Optional[Dict[str, Any]] = None,
    artifact_refs: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    session_id = str(session.get("session_id") or session.get("id") or "").strip()
    def build_event(authoritative: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not authoritative:
            raise HTTPException(status_code=404, detail="training session not found")
        _require_tenant_record(authoritative, "training session not found")
        events = (
            authoritative.get("events")
            if isinstance(authoritative.get("events"), list)
            else []
        )
        return _build_teaching_event(
            session_id=session_id,
            tenant_id=_tenant_id_for(authoritative),
            event_id=_next_event_id(timestamp, events, session_id),
            event_type=event_type,
            actor=actor,
            actor_type=actor_type,
            actor_label=actor_label,
            summary=summary,
            timestamp=timestamp,
            sequence_number=(
                max(
                    (int(item.get("sequence_number") or 0) for item in events),
                    default=0,
                )
                + 1
            ),
            outcome_signal=outcome_signal,
            evidence_ref=evidence_ref,
            patch_delta=patch_delta,
            eval_ref=eval_ref,
            artifact_refs=artifact_refs,
            payload=payload,
        )

    stored_session, event = store.append_session_event(
        session_id,
        build_event,
        session_transform=_teaching_session_contract,
    )
    session.clear()
    session.update(stored_session)
    return event


def _build_preview_evaluation_proof(
    *,
    session_id: str,
    eval_id: str,
    timestamp: str,
    preview: Dict[str, Any],
    authority: EvaluationAuthoritySnapshot,
    backend: Dict[str, Any],
    controls: List[Dict[str, Any]],
    metrics: Dict[str, float],
    target_precondition: Dict[str, Any],
    approval_decision_ref: str,
    job_id: Optional[str],
    worker_run_id: Optional[str],
    attempt_count: Optional[int],
    validation_status: str,
    validation_errors: List[str],
) -> Dict[str, Any]:
    authority_payload = authority.to_dict()
    authority_payload.pop("vectorbt_dataset", None)
    authority_payload["policy"]["approval_decision_ref"] = approval_decision_ref
    controls_digest = stable_json_sha256(controls)
    metrics_digest = stable_json_sha256(metrics)
    candidate_binding = {
        "session_id": session_id,
        "persona_id": preview.get("persona_id"),
        "tenant_id": preview.get("tenant_id"),
        "candidate_snapshot_at": preview.get("candidate_snapshot_at"),
        "controls_digest": controls_digest,
        "metrics_digest": metrics_digest,
        "dataset_digest": authority.dataset_digest,
        "policy_digest": authority.policy_digest,
        "backend_run_id": backend["run_id"],
        "job_id": job_id,
        "worker_run_id": worker_run_id,
        "target_generation": target_precondition["target_generation"],
        "precondition_digest": target_precondition["precondition_digest"],
    }
    proof = {
        "schema_version": "persona_teaching_evaluation_proof.v2",
        "proof_id": f"teval-proof-{eval_id}",
        "proof_ref": f"trainer-eval-proof:{session_id}:{eval_id}",
        "session_id": session_id,
        "persona_id": preview.get("persona_id"),
        "tenant_id": preview.get("tenant_id"),
        "eval_id": eval_id,
        "job_id": job_id,
        "worker_run_id": worker_run_id,
        "attempt_count": attempt_count,
        "status": validation_status,
        "evaluated_at": timestamp,
        "expires_at": authority.expires_at,
        "baseline_snapshot_at": preview.get("baseline_snapshot_at"),
        "candidate_snapshot_at": preview.get("candidate_snapshot_at"),
        "controls": controls,
        "controls_digest": controls_digest,
        "metrics": metrics,
        "metrics_digest": metrics_digest,
        "authority": authority_payload,
        "target_precondition": target_precondition,
        "backend": backend,
        "candidate_binding": candidate_binding,
        "candidate_digest": stable_json_sha256(candidate_binding),
        "validation_errors": list(validation_errors),
        "governance_gate_id": "persona_teaching_eval_proof",
        "governance_gate_state": validation_status,
        "gates": [
            *authority_payload["gates"],
            {
                "gate_id": "persona_target_precondition",
                "state": "passed",
                "controller_record_ref": target_precondition["controller_record_ref"],
                "target_generation": target_precondition["target_generation"],
            },
            {
                "gate_id": "real_vectorbt_backend",
                "state": "passed" if backend.get("name") == authority.required_backend else "failed",
                "backend": backend.get("name"),
                "run_id": backend.get("run_id"),
            },
            {
                "gate_id": "candidate_metric_thresholds",
                "state": "passed" if not validation_errors else "failed",
                "thresholds": authority_payload["thresholds"],
            },
            {
                "gate_id": "default_worker_provenance",
                "state": "passed" if job_id and worker_run_id and attempt_count else "failed",
                "job_id": job_id,
                "worker_run_id": worker_run_id,
                "attempt_count": attempt_count,
            },
        ],
        "required_for_commit": True,
        "commit_eligible": validation_status == "passed",
        "direct_live_influence": False,
    }
    proof["proof_digest"] = _proof_digest(proof)
    return proof


def _run_preview_evaluation(
    session_id: str,
    session: Dict[str, Any],
    *,
    mode: str,
    timestamp: str,
    eval_id: Optional[str] = None,
    job_id: Optional[str] = None,
    worker_run_id: Optional[str] = None,
    attempt_count: Optional[int] = None,
    requested_by: str = "operator",
) -> Dict[str, Any]:
    trusted_now = _parse_utc_timestamp(timestamp, "evaluation timestamp")
    eval_id = eval_id or f"teval-{uuid.uuid4().hex[:12]}"
    controls = _canonical_controls(session_id)
    config = _backtest_config(controls, requested_by)
    authority = _load_authority_snapshot(
        trusted_now=trusted_now,
        strategy_id=f"preview-{session_id}",
    )
    try:
        target_precondition = _read_target_precondition(
            session=session,
            session_id=session_id,
            trusted_now=trusted_now,
        )
    except PersonaTargetError as exc:
        raise AuthorityValidationError(f"persona target authority unavailable: {exc}") from exc
    approval_decision_ref = _candidate_approval_decision_ref(session_id, eval_id)
    try:
        result = run_vectorbt_workflow(
            authority.vectorbt_dataset,
            backend=VectorbtBackend(),
            config=config,
        )
    except (VectorbtWorkflowError, ImportError, RuntimeError, ValueError) as exc:
        raise AuthorityValidationError(f"real vectorbt evaluation failed: {exc}") from exc
    backend_result = result.backtest_result
    if backend_result.backend != authority.required_backend or backend_result.backend != PRIMARY_BACKEND:
        raise AuthorityValidationError("evaluation backend does not match the authoritative required backend")
    if not str(backend_result.run_id or "").startswith("vbt-real-"):
        raise AuthorityValidationError("evaluation backend did not return real vectorbt run provenance")
    aggregate = dict(backend_result.aggregate_metrics)
    metrics = {
        "total_return": _finite_metric(aggregate, "mean_total_return"),
        "sharpe_ratio": _finite_metric(aggregate, "mean_sharpe_ratio"),
        "max_drawdown": _finite_metric(aggregate, "mean_max_drawdown"),
    }
    validation_errors: List[str] = []
    thresholds = dict(authority.thresholds)
    if metrics["sharpe_ratio"] < float(thresholds["min_sharpe_ratio"]):
        validation_errors.append("sharpe_ratio_below_threshold")
    if metrics["total_return"] < float(thresholds["min_total_return"]):
        validation_errors.append("total_return_below_threshold")
    if metrics["max_drawdown"] > float(thresholds["max_drawdown"]):
        validation_errors.append("max_drawdown_above_threshold")
    if not job_id or not worker_run_id or not attempt_count:
        validation_errors.append("default_worker_provenance_required")
    validation_status = "passed" if not validation_errors else "failed"
    metric_delta = [
        {
            "metric_key": key,
            "display_label": label,
            "baseline_value": 0.0,
            "candidate_value": metrics[key],
            "delta": metrics[key],
            "delta_pct": metrics[key] * 100 if key == "total_return" else None,
            "unit": unit,
            "direction": "down" if key == "max_drawdown" else ("up" if metrics[key] >= 0 else "down"),
        }
        for key, label, unit in (
            ("total_return", "Total Return", "pct"),
            ("sharpe_ratio", "Sharpe Ratio", "ratio"),
            ("max_drawdown", "Max Drawdown", "pct"),
        )
    ]

    bundle = store.get_preview_bundle(session_id) or {
        "session_id": session_id,
        "tenant_id": _tenant_id_for(session),
        "evaluations": {},
    }
    _require_tenant_record(bundle, "trainer preview not found")
    evaluations = bundle.setdefault("evaluations", {})
    control_diff = [
        {
            "field": control["parameter_key"],
            "before": control["baseline_value"],
            "after": control["current_value"],
            "validation_status": "accepted",
        }
        for control in controls
    ]
    preview = {
        "eval_id": eval_id,
        "job_id": job_id,
        "session_id": session_id,
        "tenant_id": _tenant_id_for(session),
        "persona_id": session.get("persona_id"),
        "status": "completed",
        "mode": mode,
        "baseline_snapshot_at": session.get("started_at"),
        "candidate_snapshot_at": timestamp,
        "metric_delta": metric_delta,
        "control_diff": control_diff,
        "preview_quality": PRIMARY_BACKEND,
        "validation_status": validation_status,
        "validation_errors": validation_errors,
    }
    proof = _build_preview_evaluation_proof(
        session_id=session_id,
        eval_id=eval_id,
        timestamp=timestamp,
        preview=preview,
        authority=authority,
        backend={
            "name": backend_result.backend,
            "run_id": backend_result.run_id,
            "framework": "vectorbt",
            "framework_version": VECTORBT_VERSION_PIN,
            "artifact_checksum": result.registry_entry.get("checksum"),
        },
        controls=controls,
        metrics=metrics,
        target_precondition=target_precondition,
        approval_decision_ref=approval_decision_ref,
        job_id=job_id,
        worker_run_id=worker_run_id,
        attempt_count=attempt_count,
        validation_status=validation_status,
        validation_errors=validation_errors,
    )
    if job_id:
        active_job = store.get_preview_job(job_id)
        if active_job:
            _require_tenant_record(active_job, "preview job not found")
        try:
            lease_expires_at = _parse_utc_timestamp(
                (active_job or {}).get("lease_expires_at"),
                "preview job lease_expires_at",
            )
        except ValueError as exc:
            raise AuthorityValidationError("preview job lease is missing or invalid") from exc
        if (
            not active_job
            or active_job.get("status") != "running"
            or active_job.get("worker_run_id") != worker_run_id
            or trusted_now >= lease_expires_at
        ):
            raise AuthorityValidationError("preview job lease fencing rejected this worker attempt")
    evidence = _runtime_evidence_log().append(
        "preview_evaluation_terminal",
        {
            "session_id": session_id,
            "persona_id": session.get("persona_id"),
            "tenant_id": _tenant_id_for(session),
            "eval_id": eval_id,
            "job_id": job_id,
            "worker_run_id": worker_run_id,
            "proof_id": proof["proof_id"],
            "proof_digest": proof["proof_digest"],
            "dataset_version_id": authority.dataset_version_id,
            "dataset_digest": authority.dataset_digest,
            "policy_id": authority.policy_id,
            "policy_version": authority.policy_version,
            "policy_digest": authority.policy_digest,
            "approval_decision_ref": approval_decision_ref,
            "target_precondition": target_precondition,
            "backend": proof["backend"],
            "thresholds": thresholds,
            "metrics": metrics,
            "validation_status": validation_status,
            "validation_errors": validation_errors,
        },
        recorded_at=timestamp,
    )
    # Conditional Consultation Handoff:
    # Teaching policy determines whether advisory consultation is required (e.g. evaluation failed thresholds or explicit review mode).
    # When consultation_required=True, Teaching creates a ConsultRequest via TrainingConsultationClient and stores the receipt.
    # Teaching MUST NOT write a ConsultMemo (memo authority belongs to Consultation domain).
    consultation_required = (
        validation_status == "failed"
        or mode in ("consultation_required", "review_required")
        or bool(session.get("consultation_required"))
    )
    consult_request_id: Optional[str] = None
    consultation_receipt: Optional[Dict[str, Any]] = None

    if consultation_required:
        consult_client = TrainingConsultationClient()
        receipt = consult_client.create_teaching_consult_request(
            session_id=session_id,
            tenant_id=_tenant_id_for(session),
            persona_id=str(session.get("persona_id") or ""),
            eval_id=eval_id,
            validation_errors=validation_errors,
            metrics=metrics,
            trace_id=str(session.get("trace_id") or f"trace-{uuid.uuid4().hex[:12]}"),
            requested_by=requested_by,
            created_at=timestamp,
        )
        consultation_receipt = receipt.to_dict()
        consult_request_id = receipt.consult_request_id

    eval_identity_payload = {
        "eval_id": eval_id,
        "session_id": session_id,
        "validation_status": validation_status,
        "metrics": metrics,
        "proof_digest": proof.get("proof_digest"),
    }
    eval_identity = f"teval-identity-{sha256(json.dumps(eval_identity_payload, sort_keys=True).encode('utf-8')).hexdigest()[:16]}"

    eval_result = TeachingEvaluationResult(
        eval_id=eval_id,
        session_id=session_id,
        tenant_id=_tenant_id_for(session),
        persona_id=str(session.get("persona_id") or ""),
        validation_status=validation_status,
        validation_errors=validation_errors,
        metrics=metrics,
        eval_identity=eval_identity,
        evaluated_at=timestamp,
        consultation_required=consultation_required,
        consult_request_id=consult_request_id,
        consultation_receipt=consultation_receipt,
        evaluation_proof_ref=proof.get("proof_ref"),
    )

    proof["runtime_evidence"] = {
        "schema_version": evidence["schema_version"],
        "sequence": evidence["sequence"],
        "checksum": evidence["checksum"],
    }
    preview["evaluation_proof"] = proof
    preview["evaluation_result"] = eval_result.to_dict()
    preview["eval_identity"] = eval_identity
    preview["governance_gate"] = {
        "gate_id": proof["governance_gate_id"],
        "state": proof["governance_gate_state"],
        "required_for_commit": True,
    }
    evaluations[eval_id] = preview
    bundle["preview"] = preview
    store.put_preview_bundle(session_id, bundle)

    artifact_refs: Dict[str, Any] = {
        "preview_ref": f"trainer-preview:{session_id}:{eval_id}",
        "evaluation_proof_ref": proof["proof_ref"],
    }
    if consult_request_id:
        artifact_refs["consult_request_ref"] = f"consult-request:{consult_request_id}"

    _append_training_event(
        session,
        event_type="preview_result",
        actor="training-session",
        actor_type="service",
        actor_label="Training Session Service",
        summary="Trainer preview evaluation completed.",
        timestamp=timestamp,
        outcome_signal="preview-evaluated",
        eval_ref={
            "eval_id": eval_id,
            "job_id": job_id,
            "eval_identity": eval_identity,
            "candidate_snapshot_at": timestamp,
            "evaluation_proof_ref": proof["proof_ref"],
            "governance_gate_state": proof["governance_gate_state"],
            "consultation_required": consultation_required,
            "consult_request_id": consult_request_id,
            "consultation_receipt": consultation_receipt,
        },
        artifact_refs=artifact_refs,
        payload={
            "mode": mode,
            "requested_by": requested_by,
            "job_id": job_id,
            "eval_identity": eval_identity,
            "consultation_required": consultation_required,
            "consult_request_id": consult_request_id,
        },
    )

    return preview


def _preview_for_eval_ref(session_id: str, eval_ref: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    bundle = store.get_preview_bundle(session_id) or {}
    _require_tenant_record(bundle, "trainer preview not found")
    eval_id = str(eval_ref.get("eval_id") or "").strip()
    evaluations = bundle.get("evaluations") if isinstance(bundle.get("evaluations"), dict) else {}
    if eval_id and isinstance(evaluations, dict):
        preview = evaluations.get(eval_id)
        if isinstance(preview, dict):
            return dict(preview)
    preview = bundle.get("preview")
    return dict(preview) if isinstance(preview, dict) else None


def _require_commit_eval_proof(session_id: str, replay: Dict[str, Any]) -> Dict[str, Any]:
    eval_ref = _replay_eval_ref(replay)
    preview = _preview_for_eval_ref(session_id, eval_ref)
    if not preview:
        raise HTTPException(status_code=409, detail="evaluation proof required before commit")
    if preview.get("status") != "completed":
        raise HTTPException(status_code=409, detail="evaluation proof is not completed")

    candidate_snapshot_at = _replay_candidate_snapshot_at(replay)
    if candidate_snapshot_at and preview.get("candidate_snapshot_at") != candidate_snapshot_at:
        raise HTTPException(status_code=409, detail="evaluation proof candidate snapshot mismatch")

    proof = preview.get("evaluation_proof")
    if not isinstance(proof, dict) or not proof.get("proof_ref"):
        raise HTTPException(status_code=409, detail="evaluation proof required before commit")
    if (
        proof.get("schema_version") != "persona_teaching_evaluation_proof.v2"
        or proof.get("status") != "passed"
        or proof.get("governance_gate_state") != "passed"
        or proof.get("commit_eligible") is not True
        or proof.get("validation_errors") != []
    ):
        raise HTTPException(status_code=409, detail="evaluation governance gate is not passed")
    if proof.get("session_id") != session_id or proof.get("persona_id") != replay.get("persona_id"):
        raise HTTPException(status_code=409, detail="evaluation proof target binding mismatch")
    if proof.get("proof_digest") != _proof_digest(proof):
        raise HTTPException(status_code=409, detail="evaluation proof digest mismatch")
    candidate_binding = proof.get("candidate_binding")
    if not isinstance(candidate_binding, dict) or proof.get("candidate_digest") != stable_json_sha256(
        candidate_binding
    ):
        raise HTTPException(status_code=409, detail="evaluation candidate digest mismatch")
    if candidate_binding.get("candidate_snapshot_at") != preview.get("candidate_snapshot_at"):
        raise HTTPException(status_code=409, detail="evaluation candidate snapshot binding mismatch")

    now = _trusted_now().astimezone(timezone.utc)
    try:
        evaluated_at = _parse_utc_timestamp(proof.get("evaluated_at"), "proof.evaluated_at")
        expires_at = _parse_utc_timestamp(proof.get("expires_at"), "proof.expires_at")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if evaluated_at > now or now >= expires_at:
        raise HTTPException(status_code=409, detail="evaluation proof is future-dated or expired")

    try:
        authority = _load_authority_snapshot(
            trusted_now=now,
            strategy_id=f"preview-{session_id}",
        )
        controls = _canonical_controls(session_id)
    except AuthorityValidationError as exc:
        raise HTTPException(status_code=409, detail=f"evaluation authority is no longer admissible: {exc}") from exc
    proof_authority = proof.get("authority")
    if not isinstance(proof_authority, dict):
        raise HTTPException(status_code=409, detail="evaluation authority binding is missing")
    dataset_binding = proof_authority.get("dataset")
    policy_binding = proof_authority.get("policy")
    if (
        not isinstance(dataset_binding, dict)
        or dataset_binding.get("dataset_version_id") != authority.dataset_version_id
        or dataset_binding.get("digest") != authority.dataset_digest
        or not isinstance(policy_binding, dict)
        or policy_binding.get("policy_id") != authority.policy_id
        or policy_binding.get("policy_version") != authority.policy_version
        or policy_binding.get("digest") != authority.policy_digest
    ):
        raise HTTPException(status_code=409, detail="evaluation dataset or policy authority changed")
    if proof.get("controls_digest") != stable_json_sha256(controls):
        raise HTTPException(status_code=409, detail="evaluation proof controls changed after evaluation")

    gates = proof.get("gates")
    if not isinstance(gates, list) or not gates or any(
        not isinstance(gate, dict) or gate.get("state") != "passed" for gate in gates
    ):
        raise HTTPException(status_code=409, detail="evaluation proof contains a failed or invalid gate")
    backend = proof.get("backend")
    if (
        not isinstance(backend, dict)
        or backend.get("name") != PRIMARY_BACKEND
        or not str(backend.get("run_id") or "").startswith("vbt-real-")
    ):
        raise HTTPException(status_code=409, detail="evaluation proof is not backed by real vectorbt")
    metrics = proof.get("metrics")
    if not isinstance(metrics, dict) or proof.get("metrics_digest") != stable_json_sha256(metrics):
        raise HTTPException(status_code=409, detail="evaluation metric digest mismatch")
    try:
        for key in ("total_return", "sharpe_ratio", "max_drawdown"):
            _finite_metric(metrics, key)
    except AuthorityValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    job_id = str(proof.get("job_id") or "").strip()
    worker_run_id = str(proof.get("worker_run_id") or "").strip()
    job = store.get_preview_job(job_id) if job_id else None
    if job:
        _require_tenant_record(job, "preview job not found")
    if (
        not job
        or job.get("status") != "completed"
        or job.get("evaluation_proof_ref") != proof.get("proof_ref")
        or job.get("proof_digest") != proof.get("proof_digest")
        or job.get("worker_run_id") != worker_run_id
    ):
        raise HTTPException(status_code=409, detail="evaluation worker provenance is missing or mismatched")

    evidence_ref = proof.get("runtime_evidence")
    if not isinstance(evidence_ref, dict):
        raise HTTPException(status_code=409, detail="evaluation runtime evidence binding is missing")
    try:
        records = _runtime_evidence_log().read_verified()
        sequence = int(evidence_ref.get("sequence") or 0)
        evidence = records[sequence - 1] if sequence > 0 else None
    except (OSError, ValueError, IndexError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=f"evaluation runtime evidence is unavailable: {exc}") from exc
    if (
        not evidence
        or evidence.get("event_type") != "preview_evaluation_terminal"
        or evidence.get("checksum") != evidence_ref.get("checksum")
        or (evidence.get("payload") or {}).get("proof_digest") != proof.get("proof_digest")
    ):
        raise HTTPException(status_code=409, detail="evaluation runtime evidence binding mismatch")
    return dict(proof)


def _commit_authoritative_persona_target(
    *,
    session_id: str,
    replay: Dict[str, Any],
    proof: Dict[str, Any],
    idempotency_key: str,
    trusted_now: datetime,
) -> Dict[str, Any]:
    persona_id = str(replay.get("persona_id") or "").strip()
    tenant_id = str(replay.get("tenant_id") or "").strip()
    authority = proof.get("authority") if isinstance(proof.get("authority"), dict) else {}
    policy = authority.get("policy") if isinstance(authority.get("policy"), dict) else {}
    approval_decision_ref = str(policy.get("approval_decision_ref") or "").strip()
    precondition = proof.get("target_precondition")
    if not approval_decision_ref or not isinstance(precondition, dict):
        raise PersonaTargetError("evaluation proof target or approval binding is missing")
    generation = precondition.get("target_generation")
    if isinstance(generation, bool) or not isinstance(generation, int):
        raise PersonaTargetError("evaluation proof target_generation is invalid")
    return commit_persona_target(
        persona_id=persona_id,
        tenant_id=tenant_id,
        session_id=session_id,
        candidate=proof.get("candidate_binding"),
        control_state=proof.get("controls"),
        evaluation_proof=proof,
        generation=generation,
        idempotency_key=idempotency_key,
        expected_precondition_digest=str(precondition.get("precondition_digest") or ""),
        approval_decision_ref=approval_decision_ref,
        persona_readback_url=_persona_target_url(
            "TRAINING_SESSION_PERSONA_READBACK_URL_TEMPLATE",
            persona_id=persona_id,
            tenant_id=tenant_id,
            session_id=session_id,
            generation=generation,
            approval_decision_ref=approval_decision_ref,
        ),
        approval_readback_url=_persona_target_url(
            "TRAINING_SESSION_APPROVAL_READBACK_URL_TEMPLATE",
            persona_id=persona_id,
            tenant_id=tenant_id,
            session_id=session_id,
            generation=generation,
            approval_decision_ref=approval_decision_ref,
        ),
        target_write_url=_persona_target_url(
            "TRAINING_SESSION_PERSONA_TARGET_WRITE_URL_TEMPLATE",
            persona_id=persona_id,
            tenant_id=tenant_id,
            session_id=session_id,
            generation=generation,
            approval_decision_ref=approval_decision_ref,
        ),
        target_readback_url=_persona_target_url(
            "TRAINING_SESSION_PERSONA_TARGET_READBACK_URL_TEMPLATE",
            persona_id=persona_id,
            tenant_id=tenant_id,
            session_id=session_id,
            generation=generation,
            approval_decision_ref=approval_decision_ref,
        ),
        authorization_token=_persona_authority_token(),
        trusted_now=trusted_now,
        timeout_seconds=_persona_target_timeout_seconds(),
    )


def _append_or_get_runtime_decision_evidence(
    event_type: str,
    payload: Dict[str, Any],
    *,
    request_hash: str,
    recorded_at: str,
) -> Dict[str, Any]:
    log = _runtime_evidence_log()
    return log.append_or_get(
        event_type,
        {**payload, "request_hash": request_hash},
        match_payload={"request_hash": request_hash},
        recorded_at=recorded_at,
    )


@app.post("/api/training/sessions/{session_id}/preview", status_code=201)
def refresh_preview(session_id: str, body: RefreshPreviewBody) -> Dict[str, Any]:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="training session not found")
    _require_tenant_record(session, "training session not found")
    timestamp = utc_now()
    try:
        preview = _run_preview_evaluation(
            session_id,
            session,
            mode=body.mode,
            timestamp=timestamp,
            requested_by=_trusted_actor_id(),
        )
    except AuthorityValidationError as exc:
        _record_functional_result(
            "preview_evaluation",
            status="failed",
            detail={"session_id": session_id, "error_code": "evaluation_admission_rejected"},
        )
        raise HTTPException(status_code=409, detail=f"preview evaluation rejected: {exc}") from exc
    except OSError as exc:
        _record_functional_result(
            "preview_evaluation",
            status="failed",
            detail={"session_id": session_id, "error_code": "preview_evidence_unavailable"},
        )
        raise HTTPException(status_code=503, detail=f"preview evidence unavailable: {exc}") from exc
    _record_functional_result(
        "preview_evaluation",
        status=(
            "ok"
            if (preview.get("evaluation_proof") or {}).get("governance_gate_state")
            == "passed"
            else "failed"
        ),
        detail={
            "session_id": session_id,
            "evaluation_proof_ref": (
                preview.get("evaluation_proof") or {}
            ).get("proof_ref"),
        },
    )
    return preview


def _preview_job_max_attempts() -> int:
    try:
        value = int(os.getenv("TRAINING_SESSION_PREVIEW_JOB_MAX_ATTEMPTS", "3"))
    except ValueError as exc:
        raise RuntimeError("TRAINING_SESSION_PREVIEW_JOB_MAX_ATTEMPTS must be an integer") from exc
    if value < 1:
        raise RuntimeError("TRAINING_SESSION_PREVIEW_JOB_MAX_ATTEMPTS must be positive")
    return value


def _preview_job_lease_seconds() -> int:
    try:
        value = int(os.getenv("TRAINING_SESSION_PREVIEW_JOB_LEASE_SECONDS", "120"))
    except ValueError as exc:
        raise RuntimeError("TRAINING_SESSION_PREVIEW_JOB_LEASE_SECONDS must be an integer") from exc
    if value < 30:
        raise RuntimeError("TRAINING_SESSION_PREVIEW_JOB_LEASE_SECONDS must be at least 30")
    return value


def _job_is_claimable(job: Dict[str, Any], now: datetime) -> bool:
    status = str(job.get("status") or "").lower()
    attempts = int(job.get("attempt_count") or 0)
    max_attempts = int(job.get("max_attempts") or _preview_job_max_attempts())
    if attempts >= max_attempts:
        return False
    if status == "queued":
        return True
    if status == "failed":
        return job.get("retryable") is True
    if status != "running":
        return False
    try:
        return _parse_utc_timestamp(job.get("lease_expires_at"), "job.lease_expires_at") <= now
    except ValueError:
        return True


@app.get("/api/training/preview-jobs")
def list_preview_jobs(
    session_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> List[Dict[str, Any]]:
    jobs = _tenant_records(store.list_preview_jobs())
    if session_id:
        jobs = [job for job in jobs if job.get("session_id") == session_id]
    if status and status.lower() == "claimable":
        now = _trusted_now().astimezone(timezone.utc)
        jobs = [job for job in jobs if _job_is_claimable(job, now)]
    elif status:
        jobs = [job for job in jobs if str(job.get("status") or "").lower() == status.lower()]
    jobs.sort(key=lambda job: str(job.get("requested_at") or ""))
    return jobs[:limit]


@app.get("/api/training/preview-jobs/{job_id}")
def get_preview_job(job_id: str) -> Dict[str, Any]:
    job = store.get_preview_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="preview job not found")
    return _require_tenant_record(job, "preview job not found")


@app.post("/api/training/sessions/{session_id}/preview-jobs", status_code=201)
def queue_preview_job(
    session_id: str,
    body: QueuePreviewJobBody,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
) -> Dict[str, Any]:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="training session not found")
    _require_tenant_record(session, "training session not found")
    if str(session.get("status") or "").lower() != "active":
        raise HTTPException(status_code=409, detail="training session is not active")

    timestamp = utc_now()
    key = _idempotency_key(idempotency_key, x_idempotency_key)
    requested_by = _trusted_actor_id(body.requested_by)
    job_id = _preview_job_id(session_id, key)
    request_hash = _stable_hash(
        {
            "session_id": session_id,
            "mode": body.mode,
            "requested_by": requested_by,
            "terminalize_session": body.terminalize_session,
        }
    )
    existing = store.get_preview_job(job_id)
    if existing is not None:
        _require_tenant_record(existing, "preview job not found")
        if existing.get("request_hash") != request_hash:
            raise HTTPException(status_code=409, detail="idempotency key conflict")
        existing["replayed"] = True
        return existing

    eval_id = f"teval-{uuid.uuid4().hex[:12]}"
    evidence = _runtime_evidence_log().append(
        "preview_job_admission_intent",
        {
            "job_id": job_id,
            "session_id": session_id,
            "persona_id": session.get("persona_id"),
            "tenant_id": _tenant_id_for(session),
            "eval_id": eval_id,
            "mode": body.mode,
            "requested_by": requested_by,
            "terminalize_session": body.terminalize_session,
            "request_hash": request_hash,
        },
        recorded_at=timestamp,
    )
    job = {
        "job_id": job_id,
        "session_id": session_id,
        "tenant_id": _tenant_id_for(session),
        "eval_id": eval_id,
        "status": "queued",
        "mode": body.mode,
        "terminalize_session": body.terminalize_session,
        "requested_by": requested_by,
        "requested_at": timestamp,
        "client_requested_at": body.requested_at,
        "attempt_count": 0,
        "max_attempts": _preview_job_max_attempts(),
        "retryable": True,
        "idempotency_key": key,
        "request_hash": request_hash,
        "admission_evidence": {
            "sequence": evidence["sequence"],
            "checksum": evidence["checksum"],
        },
    }
    stored = store.put_preview_job(job_id, job)
    _append_training_event(
        session,
        event_type="preview_requested",
        actor=requested_by,
        actor_type=(
            "user" if _request_authority().delegated_actor_id else "service"
        ),
        summary="Async trainer preview evaluation queued.",
        timestamp=timestamp,
        eval_ref={"eval_id": eval_id, "job_id": job_id},
        payload={
            "mode": body.mode,
            "job_id": job_id,
            "terminalize_session": body.terminalize_session,
        },
    )
    return stored


@app.post("/api/training/preview-jobs/{job_id}/run")
def run_preview_job(job_id: str, body: Optional[RunPreviewJobBody] = None) -> Dict[str, Any]:
    del body
    existing_job = store.get_preview_job(job_id)
    if not existing_job:
        raise HTTPException(status_code=404, detail="preview job not found")
    _require_tenant_record(existing_job, "preview job not found")
    now = _trusted_now().astimezone(timezone.utc)
    timestamp = _utc_iso(now)
    lease_expires_at = _utc_iso(now + timedelta(seconds=_preview_job_lease_seconds()))
    worker_run_id = f"preview-worker-{uuid.uuid4().hex[:16]}"

    def claim(existing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if existing is None:
            raise HTTPException(status_code=404, detail="preview job not found")
        _require_tenant_record(existing, "preview job not found")
        if existing.get("status") == "completed":
            replayed = dict(existing)
            replayed["replayed"] = True
            return replayed
        if not _job_is_claimable(existing, now):
            raise HTTPException(status_code=409, detail="preview job is not claimable")
        reclaimed = existing.get("status") == "running"
        existing.update(
            {
                "status": "running",
                "last_attempt_at": timestamp,
                "lease_started_at": timestamp,
                "lease_expires_at": lease_expires_at,
                "worker_run_id": worker_run_id,
                "attempt_count": int(existing.get("attempt_count") or 0) + 1,
                "reclaimed": reclaimed,
                "retryable": False,
                "replayed": False,
            }
        )
        existing.pop("failed_at", None)
        existing.pop("error_code", None)
        return existing

    job = store.mutate_preview_job(job_id, claim)
    if job.get("replayed") is True:
        return job

    session_id = str(job.get("session_id") or "").strip()
    session = store.get_session(session_id)
    if not session:
        failure_code = "training_session_not_found"
        retryable = False
        preview = None
    else:
        _require_tenant_record(session, "training session not found")
        failure_code = None
        retryable = False
        try:
            preview = _run_preview_evaluation(
                session_id,
                session,
                mode=str(job.get("mode") or "refresh"),
                timestamp=timestamp,
                eval_id=str(job.get("eval_id") or "") or None,
                job_id=job_id,
                worker_run_id=worker_run_id,
                attempt_count=int(job.get("attempt_count") or 0),
                requested_by=str(job.get("requested_by") or "operator"),
            )
        except AuthorityValidationError as exc:
            preview = None
            message = str(exc)
            retryable = message.startswith(
                ("real vectorbt evaluation failed", "persona target authority unavailable")
            )
            failure_code = (
                "evaluation_dependency_unavailable" if retryable else "evaluation_admission_rejected"
            )
        except (OSError, RuntimeError):
            preview = None
            retryable = True
            failure_code = "runtime_evidence_unavailable"
        except Exception:  # noqa: BLE001 - every worker attempt must reach durable terminal state.
            preview = None
            retryable = True
            failure_code = "evaluation_internal_error"

    if preview is None:
        retryable = retryable and int(job.get("attempt_count") or 0) < int(job.get("max_attempts") or 1)
        try:
            failure_evidence = _runtime_evidence_log().append(
                "preview_job_failed",
                {
                    "job_id": job_id,
                    "session_id": session_id,
                    "tenant_id": _tenant_id_for(job),
                    "worker_run_id": worker_run_id,
                    "attempt_count": job.get("attempt_count"),
                    "error_code": failure_code,
                    "retryable": retryable,
                },
                recorded_at=timestamp,
            )
        except (OSError, RuntimeError):
            failure_evidence = None

        def fail(active: Optional[Dict[str, Any]]) -> Dict[str, Any]:
            if not active or active.get("worker_run_id") != worker_run_id:
                raise HTTPException(status_code=409, detail="preview job lease was lost")
            active.update(
                {
                    "status": "failed",
                    "failed_at": timestamp,
                    "error_code": failure_code,
                    "retryable": retryable,
                }
            )
            if failure_evidence:
                active["failure_evidence"] = {
                    "sequence": failure_evidence["sequence"],
                    "checksum": failure_evidence["checksum"],
                }
            return active

        failed_job = store.mutate_preview_job(job_id, fail)
        _record_functional_result(
            "preview_evaluation",
            status="failed",
            detail={
                "job_id": job_id,
                "session_id": session_id,
                "error_code": failure_code,
                "retryable": retryable,
            },
        )
        return failed_job

    proof = preview.get("evaluation_proof") if isinstance(preview.get("evaluation_proof"), dict) else {}

    def complete(active: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not active or active.get("worker_run_id") != worker_run_id or active.get("status") != "running":
            raise HTTPException(status_code=409, detail="preview job lease was lost")
        active.update(
            {
                "status": "completed",
                "completed_at": timestamp,
                "candidate_snapshot_at": preview.get("candidate_snapshot_at"),
                "preview_ref": f"trainer-preview:{session_id}:{preview.get('eval_id')}",
                "evaluation_proof_ref": proof.get("proof_ref"),
                "proof_digest": proof.get("proof_digest"),
                "governance_gate_state": proof.get("governance_gate_state"),
                "retryable": False,
                "preview": preview,
            }
        )
        active.pop("error_code", None)
        active.pop("failed_at", None)
        return active

    completed_job = store.mutate_preview_job(job_id, complete)
    _record_functional_result(
        "preview_evaluation",
        status=(
            "ok"
            if completed_job.get("governance_gate_state") == "passed"
            else "failed"
        ),
        detail={
            "job_id": job_id,
            "session_id": session_id,
            "evaluation_proof_ref": completed_job.get("evaluation_proof_ref"),
            "governance_gate_state": completed_job.get("governance_gate_state"),
        },
    )
    return completed_job



@app.get("/api/training/replays")
def list_replays(persona_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    records = _tenant_records(store.list_replays())
    if persona_id:
        records = [record for record in records if record.get("persona_id") == persona_id]
    if status:
        records = [record for record in records if str(record.get("status") or "").lower() == status.lower()]
    return records


@app.get("/api/training/replays/{session_id}")
def get_replay(session_id: str) -> Dict[str, Any]:
    replay = store.get_replay(session_id)
    if not replay:
        session = store.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="training replay not found")
        _require_tenant_record(session, "training replay not found")
        replay = dict(session)
        replay.setdefault("replay_resolution", {"state": "not_applicable"})
        replay.setdefault("artifacts", {})
    return _require_tenant_record(replay, "training replay not found")


@app.post("/api/training/sessions/{session_id}/complete", status_code=201)
def complete_session(session_id: str) -> Dict[str, Any]:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="training session not found")
    _require_tenant_record(session, "training session not found")
    existing_replay = store.get_replay(session_id)
    if existing_replay and (existing_replay.get("replay_resolution") or {}).get("state") == "pending_decision":
        _require_tenant_record(existing_replay, "training replay not found")
        if str(session.get("status") or "").lower() != "completed":
            session["status"] = "completed"
            session["ended_at"] = existing_replay.get("ended_at") or utc_now()
            store.put_session(_teaching_session_contract(session))
        return existing_replay
    if str(session.get("status") or "").lower() not in {"active", "paused"}:
        raise HTTPException(status_code=409, detail="training session cannot be completed")
    timestamp = utc_now()
    preview = (store.get_preview_bundle(session_id) or {}).get("preview") or {}
    proof = preview.get("evaluation_proof") if isinstance(preview.get("evaluation_proof"), dict) else {}
    candidate_snapshot_at = preview.get("candidate_snapshot_at")
    if not proof or not candidate_snapshot_at:
        raise HTTPException(status_code=409, detail="passing worker evaluation proof required before completion")
    replay = dict(session)
    replay["status"] = "completed"
    replay["ended_at"] = timestamp
    replay["replay_resolution"] = {"state": "pending_decision", "decision_at": None, "decision_by": None, "note": None}
    replay["artifacts"] = {
        "before_artifact_ref": (proof.get("target_precondition") or {}).get("controller_record_ref"),
        "candidate_artifact_ref": f"sha256:{proof.get('candidate_digest')}",
        "after_artifact_ref": None,
    }
    replay_eval_ref = {
        "eval_id": preview.get("eval_id"),
        "job_id": preview.get("job_id"),
        "baseline_snapshot_at": preview.get("baseline_snapshot_at"),
        "candidate_snapshot_at": candidate_snapshot_at,
    }
    replay_eval_ref.update(
        {
            "evaluation_proof_ref": proof.get("proof_ref"),
            "governance_gate_state": proof.get("governance_gate_state"),
        }
    )
    replay_event = _build_teaching_event(
        session_id=session_id,
        tenant_id=_tenant_id_for(replay),
        event_id=f"tevt-complete-{str(proof.get('proof_digest') or '')[:20]}",
        actor="system",
        actor_label="Training Session Service",
        event_type="preview_trigger",
        summary="Replay candidate materialized from preview.",
        timestamp=str(proof.get("evaluated_at") or timestamp),
        sequence_number=max(
            (int(event.get("sequence_number") or 0) for event in replay.get("events", [])),
            default=0,
        )
        + 1,
        outcome_signal="teaching-complete",
        eval_ref=replay_eval_ref,
    )
    replay.setdefault("events", []).append(replay_event)
    # Validate the exact event-bound candidate before changing either session
    # or replay state.  This re-reads current data/policy/controls and evidence.
    _require_commit_eval_proof(session_id, replay)
    try:
        materialized_evidence = _append_or_get_runtime_decision_evidence(
            "replay_candidate_materialized",
            {
                "session_id": session_id,
                "persona_id": session.get("persona_id"),
                "tenant_id": _tenant_id_for(session),
                "proof_digest": proof.get("proof_digest"),
                "candidate_digest": proof.get("candidate_digest"),
                "candidate_snapshot_at": candidate_snapshot_at,
            },
            request_hash=str(proof.get("proof_digest") or ""),
            recorded_at=timestamp,
        )
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"candidate evidence unavailable: {exc}") from exc
    replay["artifacts"]["materialization_evidence_ref"] = (
        f"runtime-evidence:{materialized_evidence['checksum']}"
    )
    stored_event = store.append_event(replay_event)
    replay["events"][-1] = stored_event
    store.put_replay(session_id, replay)
    session["status"] = "completed"
    session["ended_at"] = timestamp
    session = _teaching_session_contract(session)
    store.put_session(session)
    return replay


def _decide_replay(
    session_id: str,
    body: ReplayDecisionBody,
    state: str,
    *,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required for replay decisions")
    timestamp = utc_now()
    request_hash = _replay_decision_hash(session_id, body, state)

    def decide_locked(existing_replay: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if existing_replay is None:
            session = store.get_session(session_id)
            if not session:
                raise HTTPException(status_code=404, detail="training replay not found")
            _require_tenant_record(session, "training replay not found")
            replay = dict(session)
            replay.setdefault("replay_resolution", {"state": "not_applicable"})
            replay.setdefault("artifacts", {})
        else:
            replay = existing_replay
            _require_tenant_record(replay, "training replay not found")

        resolution = replay.setdefault("replay_resolution", {})
        existing_idempotency = resolution.get("idempotency")
        if isinstance(existing_idempotency, dict):
            if existing_idempotency.get("key") == idempotency_key:
                if existing_idempotency.get("request_hash") != request_hash:
                    raise HTTPException(status_code=409, detail="idempotency key conflict")
                return _mark_replay_idempotency(
                    replay,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    replayed=True,
                )
        if resolution.get("state") not in {"pending_decision", None}:
            raise HTTPException(status_code=409, detail="replay already decided")
        candidate_snapshot_at = _replay_candidate_snapshot_at(replay)
        if not body.expected_candidate_snapshot_at:
            raise HTTPException(status_code=409, detail="expected_candidate_snapshot_at is required")
        if body.expected_candidate_snapshot_at != candidate_snapshot_at:
            raise HTTPException(status_code=409, detail="candidate snapshot mismatch")

        artifacts = replay.setdefault("artifacts", {})
        proof: Dict[str, Any] = {}
        target_receipt: Dict[str, Any] = {}
        if state == "committed":
            proof = _require_commit_eval_proof(session_id, replay)
            try:
                _append_or_get_runtime_decision_evidence(
                    "persona_commit_admission_intent",
                    {
                        "session_id": session_id,
                        "persona_id": replay.get("persona_id"),
                        "tenant_id": _tenant_id_for(replay),
                        "candidate_snapshot_at": candidate_snapshot_at,
                        "proof_digest": proof.get("proof_digest"),
                        "candidate_digest": proof.get("candidate_digest"),
                        "controls_digest": proof.get("controls_digest"),
                        "target_precondition": proof.get("target_precondition"),
                    },
                    request_hash=request_hash,
                    recorded_at=timestamp,
                )
                target_receipt = _commit_authoritative_persona_target(
                    session_id=session_id,
                    replay=replay,
                    proof=proof,
                    idempotency_key=idempotency_key,
                    trusted_now=_trusted_now().astimezone(timezone.utc),
                )
            except PersonaTargetError as exc:
                try:
                    _append_or_get_runtime_decision_evidence(
                        "persona_commit_rejected",
                        {
                            "session_id": session_id,
                            "persona_id": replay.get("persona_id"),
                            "tenant_id": _tenant_id_for(replay),
                            "proof_digest": proof.get("proof_digest"),
                            "error_code": "persona_target_authority_rejected",
                        },
                        request_hash=request_hash,
                        recorded_at=timestamp,
                    )
                except (OSError, RuntimeError):
                    pass
                raise HTTPException(status_code=409, detail=f"persona target commit rejected: {exc}") from exc
            except (OSError, RuntimeError) as exc:
                raise HTTPException(status_code=503, detail=f"persona commit evidence unavailable: {exc}") from exc

            try:
                terminal_evidence = _append_or_get_runtime_decision_evidence(
                    "persona_commit_terminal_readback",
                    {
                        "session_id": session_id,
                        "persona_id": replay.get("persona_id"),
                        "tenant_id": _tenant_id_for(replay),
                        "proof_digest": proof.get("proof_digest"),
                        "candidate_digest": proof.get("candidate_digest"),
                        "controls_digest": proof.get("controls_digest"),
                        "target_receipt": target_receipt,
                    },
                    request_hash=request_hash,
                    recorded_at=str(target_receipt.get("target_recorded_at") or timestamp),
                )
            except (OSError, RuntimeError) as exc:
                # The target owner may already be committed.  Returning failure
                # is intentional: a retry with the same key converges through
                # exact terminal readback before local state advances.
                raise HTTPException(status_code=503, detail=f"persona terminal evidence unavailable: {exc}") from exc

            target_ref = str(target_receipt.get("target_controller_record_ref") or "").strip()
            if not target_ref:
                raise HTTPException(status_code=409, detail="persona terminal target record ref is missing")
            artifacts.update(
                {
                    "after_artifact_ref": target_ref,
                    "persona_policy_ref": target_ref,
                    "persona_target_controller_record_ref": target_ref,
                    "approval_controller_record_ref": target_receipt.get("approval_controller_record_ref"),
                    "approval_decision_id": target_receipt.get("approval_decision_id"),
                    "approval_decision_ref": target_receipt.get("approval_decision_ref"),
                    "evaluation_proof_ref": proof.get("proof_ref"),
                    "evaluation_proof_id": proof.get("proof_id"),
                    "evaluation_job_id": proof.get("job_id"),
                    "evaluation_governance_gate_id": proof.get("governance_gate_id"),
                    "evaluation_governance_gate_state": proof.get("governance_gate_state"),
                    "decision_record_ref": f"runtime-evidence:{terminal_evidence['checksum']}",
                    "lineage_recorded_at": target_receipt.get("target_recorded_at") or terminal_evidence["recorded_at"],
                }
            )
            try:
                policy_edges = record_policy_lineage_commit(
                    session_id,
                    _teaching_event_ids(replay),
                    str(replay.get("persona_id") or ""),
                    store=build_lineage_read_store(store.data_dir),
                    commit_at=str(artifacts["lineage_recorded_at"]),
                    policy_artifact_id=target_ref,
                )
            except PolicyLineageError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            artifacts["policy_lineage_edge_ids"] = [edge["edge_id"] for edge in policy_edges]
            artifacts["policy_lineage_store_ref"] = "lineage-read:training-session-policy-lineage"
            artifacts["lineage_audit"] = {
                "state": "recorded_from_terminal_target_readback",
                "recorded_at": artifacts["lineage_recorded_at"],
                "policy_lineage_edge_ids": artifacts["policy_lineage_edge_ids"],
                "evaluation_proof_ref": proof.get("proof_ref"),
                "governance_gate_state": proof.get("governance_gate_state"),
                "persona_target_controller_record_ref": target_ref,
            }
        else:
            try:
                terminal_evidence = _append_or_get_runtime_decision_evidence(
                    "replay_discard_terminal",
                    {
                        "session_id": session_id,
                        "persona_id": replay.get("persona_id"),
                        "tenant_id": _tenant_id_for(replay),
                        "candidate_snapshot_at": candidate_snapshot_at,
                        "decision_by": body.actor_id,
                        "note": body.note,
                    },
                    request_hash=request_hash,
                    recorded_at=timestamp,
                )
            except (OSError, RuntimeError) as exc:
                raise HTTPException(status_code=503, detail=f"discard evidence unavailable: {exc}") from exc
            artifacts.update(
                {
                    "after_artifact_ref": None,
                    "decision_record_ref": f"runtime-evidence:{terminal_evidence['checksum']}",
                    "lineage_recorded_at": terminal_evidence["recorded_at"],
                }
            )

        resolution["state"] = state
        resolution["decision_at"] = artifacts.get("lineage_recorded_at") or timestamp
        resolution["decision_by"] = body.actor_id
        resolution["note"] = body.note
        resolution["client_decided_at"] = body.decided_at
        if proof:
            resolution["evaluation_proof"] = proof
            resolution["persona_target_readback"] = target_receipt

        decision_artifact_refs = {
            "before_artifact_ref": artifacts.get("before_artifact_ref"),
            "candidate_artifact_ref": artifacts.get("candidate_artifact_ref"),
            "after_artifact_ref": artifacts.get("after_artifact_ref"),
            "decision_record_ref": artifacts.get("decision_record_ref"),
            "lineage_recorded_at": artifacts.get("lineage_recorded_at"),
            "evaluation_proof_ref": artifacts.get("evaluation_proof_ref"),
            "evaluation_governance_gate_id": artifacts.get("evaluation_governance_gate_id"),
            "evaluation_governance_gate_state": artifacts.get("evaluation_governance_gate_state"),
            "persona_policy_ref": artifacts.get("persona_policy_ref"),
            "persona_target_controller_record_ref": artifacts.get("persona_target_controller_record_ref"),
            "approval_controller_record_ref": artifacts.get("approval_controller_record_ref"),
            "approval_decision_id": artifacts.get("approval_decision_id"),
            "approval_decision_ref": artifacts.get("approval_decision_ref"),
            "policy_lineage_edge_ids": artifacts.get("policy_lineage_edge_ids"),
            "policy_lineage_store_ref": artifacts.get("policy_lineage_store_ref"),
            "lineage_audit": artifacts.get("lineage_audit"),
        }
        decision_event = _build_teaching_event(
            session_id=session_id,
            tenant_id=_tenant_id_for(replay),
            event_id=f"tevt-decision-{request_hash[:20]}",
            actor="system",
            actor_label="Training Session Service",
            event_type="commit" if state == "committed" else "discard",
            summary=f"Replay candidate {state} by {body.actor_id}.",
            timestamp=str(artifacts.get("lineage_recorded_at") or timestamp),
            sequence_number=max(
                (int(event.get("sequence_number") or 0) for event in replay.get("events", [])),
                default=0,
            )
            + 1,
            eval_ref={
                "candidate_snapshot_at": candidate_snapshot_at,
                "evaluation_proof_ref": artifacts.get("evaluation_proof_ref"),
                "governance_gate_state": artifacts.get("evaluation_governance_gate_state"),
            },
            artifact_refs=decision_artifact_refs,
        )
        _mark_replay_idempotency(
            replay,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            replayed=False,
        )
        stored_decision_event = store.append_event(decision_event)
        replay.setdefault("events", []).append(stored_decision_event)
        return replay

    return store.mutate_replay(session_id, decide_locked)


@app.post("/api/training/replays/{session_id}/commit")
def commit_replay(
    session_id: str,
    body: ReplayDecisionBody,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
) -> Dict[str, Any]:
    body = body.model_copy(update={"actor_id": _trusted_actor_id(body.actor_id)})
    try:
        replay = _decide_replay(
            session_id,
            body,
            "committed",
            idempotency_key=_idempotency_key(idempotency_key, x_idempotency_key),
        )
    except HTTPException as exc:
        _record_functional_result(
            "persona_commit",
            status="failed",
            detail={
                "session_id": session_id,
                "status_code": exc.status_code,
                "error": str(exc.detail),
            },
        )
        raise
    _record_functional_result(
        "persona_commit",
        status="ok",
        detail={
            "session_id": session_id,
            "state": (replay.get("replay_resolution") or {}).get("state"),
            "persona_target_controller_record_ref": (
                replay.get("artifacts") or {}
            ).get("persona_target_controller_record_ref"),
        },
    )
    return replay


@app.post("/api/training/replays/{session_id}/discard")
def discard_replay(
    session_id: str,
    body: ReplayDecisionBody,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
) -> Dict[str, Any]:
    body = body.model_copy(update={"actor_id": _trusted_actor_id(body.actor_id)})
    try:
        replay = _decide_replay(
            session_id,
            body,
            "discarded",
            idempotency_key=_idempotency_key(idempotency_key, x_idempotency_key),
        )
    except HTTPException as exc:
        _record_functional_result(
            "replay_discard",
            status="failed",
            detail={
                "session_id": session_id,
                "status_code": exc.status_code,
                "error": str(exc.detail),
            },
        )
        raise
    _record_functional_result(
        "replay_discard",
        status="ok",
        detail={"session_id": session_id, "state": "discarded"},
    )
    return replay
