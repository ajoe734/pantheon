from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from services.foundation.health import register_fastapi_health_routes
from store import TrainingSessionStore, build_training_session_store


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _data_dir() -> str:
    return os.getenv("TRAINING_SESSION_DATA_DIR", "/tmp/pantheon/training-session")


def _session_id(timestamp: str, existing: set[str]) -> str:
    prefix = timestamp[:10].replace("-", "")
    index = len(existing) + 1
    candidate = f"trn-{prefix}-{index:03d}"
    while candidate in existing:
        index += 1
        candidate = f"trn-{prefix}-{index:03d}"
    return candidate


def _next_event_id(timestamp: str, events: List[Dict[str, Any]]) -> str:
    prefix = timestamp[:10].replace("-", "")
    existing_ids = {str(event.get("event_id") or "") for event in events if isinstance(event, dict)}
    next_sequence = max((int(event.get("sequence_number") or 0) for event in events), default=0) + 1
    event_id = f"tevt-{prefix}-{next_sequence:03d}"
    while event_id in existing_ids:
        next_sequence += 1
        event_id = f"tevt-{prefix}-{next_sequence:03d}"
    return event_id


def _replay_candidate_snapshot_at(replay: Dict[str, Any]) -> Optional[str]:
    for event in reversed(list(replay.get("events") or [])):
        if not isinstance(event, dict):
            continue
        eval_ref = event.get("eval_ref")
        if isinstance(eval_ref, dict) and eval_ref.get("candidate_snapshot_at"):
            return str(eval_ref["candidate_snapshot_at"])
    return None


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


class CreateSessionBody(BaseModel):
    persona_id: str
    objective: str
    context_refs: List[Dict[str, Any]] = Field(default_factory=list)
    actor_id: str = "operator"
    created_at: Optional[str] = None


class AppendEventBody(BaseModel):
    actor: str = "operator"
    actor_label: Optional[str] = None
    event_type: str = "message"
    message_body: Optional[str] = None
    summary: Optional[str] = None
    outcome_signal: Optional[str] = None
    evidence_ref: Optional[Dict[str, Any]] = None
    patch_delta: Optional[List[Dict[str, Any]]] = None
    eval_ref: Optional[Dict[str, Any]] = None
    artifact_refs: Optional[Dict[str, Any]] = None
    emitted_at: Optional[str] = None


class PatchControlsBody(BaseModel):
    patches: List[Dict[str, Any]]
    patched_at: Optional[str] = None


class RefreshPreviewBody(BaseModel):
    mode: str = "refresh"
    refreshed_at: Optional[str] = None


class ReplayDecisionBody(BaseModel):
    expected_candidate_snapshot_at: Optional[str] = None
    actor_id: str = "operator"
    note: Optional[str] = None
    decided_at: Optional[str] = None


app = FastAPI(title="Pantheon Training Session Service", version="0.1.0")
store = build_training_session_store(_data_dir())
register_fastapi_health_routes(
    app,
    "training-session",
    metrics=lambda: {
        "session_count": len(store.list_sessions()),
        "event_log_count": len(store.list_event_log()),
    },
    details=lambda: {"data_dir": _data_dir()},
)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "training-session",
        "data_dir": _data_dir(),
        "session_count": len(store.list_sessions()),
        "event_log_count": len(store.list_event_log()),
    }


@app.get("/api/training/sessions")
def list_sessions(persona_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    sessions = store.list_sessions()
    if persona_id:
        sessions = [session for session in sessions if session.get("persona_id") == persona_id]
    if status:
        sessions = [session for session in sessions if str(session.get("status") or "").lower() == status.lower()]
    return sessions


@app.post("/api/training/sessions", status_code=201)
def create_session(body: CreateSessionBody) -> Dict[str, Any]:
    timestamp = body.created_at or utc_now()
    existing_ids = {str(session.get("session_id") or "") for session in store.list_sessions()}
    session_id = _session_id(timestamp, existing_ids)
    session = {
        "id": session_id,
        "session_id": session_id,
        "persona_id": body.persona_id,
        "session_type": "trainer",
        "objective": body.objective,
        "topic": body.objective,
        "status": "active",
        "started_at": timestamp,
        "ended_at": None,
        "opened_by": body.actor_id,
        "context_refs": body.context_refs,
        "events": [],
        "outcomes": [],
    }
    store.put_session(session)
    store.put_controls(session_id, {"session_id": session_id, "controls": []})
    store.put_preview_bundle(
        session_id,
        {
            "session_id": session_id,
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
    return session


@app.post("/api/training/sessions/{session_id}/events", status_code=201)
def append_event(session_id: str, body: AppendEventBody) -> Dict[str, Any]:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="training session not found")
    if str(session.get("status") or "").lower() != "active":
        raise HTTPException(status_code=409, detail="training session is not active")
    timestamp = body.emitted_at or utc_now()
    events = session.setdefault("events", [])
    event_id = _next_event_id(timestamp, events)
    sequence_number = max((int(event.get("sequence_number") or 0) for event in events), default=0) + 1
    event = {
        "event_id": event_id,
        "session_id": session_id,
        "actor": body.actor,
        "actor_label": body.actor_label,
        "event_type": body.event_type,
        "message_body": body.message_body,
        "summary": body.summary,
        "emitted_at": timestamp,
        "sequence_number": sequence_number,
        "outcome_signal": body.outcome_signal,
        "evidence_ref": body.evidence_ref,
        "patch_delta": body.patch_delta,
        "eval_ref": body.eval_ref,
        "artifact_refs": body.artifact_refs,
    }
    events.append(event)
    if body.outcome_signal:
        outcomes = session.setdefault("outcomes", [])
        if body.outcome_signal not in outcomes:
            outcomes.append(body.outcome_signal)
    store.put_session(session)
    store.append_event(event)
    return {"accepted_at": timestamp, "event": event, "session": session}


@app.get("/api/training/sessions/{session_id}/events")
def list_events(session_id: str) -> List[Dict[str, Any]]:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="training session not found")
    return store.list_event_log(session_id)


@app.get("/api/training/controls")
def list_controls(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    records = store.list_controls()
    if session_id:
        records = [record for record in records if record.get("session_id") == session_id]
    return records


@app.get("/api/training/controls/{session_id}")
def get_controls(session_id: str) -> Dict[str, Any]:
    record = store.get_controls(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="trainer controls not found")
    return record


@app.post("/api/training/sessions/{session_id}/controls")
@app.patch("/api/training/sessions/{session_id}/controls")
def patch_controls(session_id: str, body: PatchControlsBody) -> Dict[str, Any]:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="training session not found")
    if str(session.get("status") or "").lower() != "active":
        raise HTTPException(status_code=409, detail="training session is not active")

    timestamp = body.patched_at or utc_now()
    record = store.get_controls(session_id) or {"session_id": session_id, "controls": []}
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

    store.put_controls(session_id, {"session_id": session_id, "controls": controls})
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
    records = store.list_previews()
    if session_id:
        records = [record for record in records if record.get("session_id") == session_id]
    return records


@app.get("/api/training/previews/{session_id}")
def get_preview(session_id: str) -> Dict[str, Any]:
    record = store.get_preview_bundle(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="trainer preview not found")
    return record


@app.post("/api/training/sessions/{session_id}/preview", status_code=201)
def refresh_preview(session_id: str, body: RefreshPreviewBody) -> Dict[str, Any]:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="training session not found")
    timestamp = body.refreshed_at or utc_now()
    controls = (store.get_controls(session_id) or {}).get("controls") or []
    bundle = store.get_preview_bundle(session_id) or {"session_id": session_id, "evaluations": {}}
    evaluations = bundle.setdefault("evaluations", {})
    eval_id = f"teval-{uuid.uuid4().hex[:12]}"
    control_diff = [
        {
            "field": control.get("parameter_key"),
            "before": control.get("baseline_value", control.get("current_value")),
            "after": control.get("current_value"),
            "validation_status": "accepted",
        }
        for control in controls
        if isinstance(control, dict) and control.get("parameter_key")
    ]
    preview = {
        "eval_id": eval_id,
        "session_id": session_id,
        "status": "completed",
        "baseline_snapshot_at": session.get("started_at"),
        "candidate_snapshot_at": timestamp,
        "metric_delta": {"return_delta": 0.0, "drawdown_delta": 0.0},
        "control_diff": control_diff,
        "preview_quality": "directional_only",
    }
    evaluations[eval_id] = preview
    bundle["preview"] = preview
    store.put_preview_bundle(session_id, bundle)
    return preview


@app.get("/api/training/replays")
def list_replays(persona_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    records = store.list_replays()
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
        replay = dict(session)
        replay.setdefault("replay_resolution", {"state": "not_applicable"})
        replay.setdefault("artifacts", {})
    return replay


@app.post("/api/training/sessions/{session_id}/complete", status_code=201)
def complete_session(session_id: str) -> Dict[str, Any]:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="training session not found")
    if str(session.get("status") or "").lower() not in {"active", "paused"}:
        raise HTTPException(status_code=409, detail="training session cannot be completed")
    timestamp = utc_now()
    session["status"] = "completed"
    session["ended_at"] = timestamp
    store.put_session(session)
    preview = (store.get_preview_bundle(session_id) or {}).get("preview") or {}
    candidate_snapshot_at = preview.get("candidate_snapshot_at") or timestamp
    replay = dict(session)
    replay["replay_resolution"] = {"state": "pending_decision", "decision_at": None, "decision_by": None, "note": None}
    replay["artifacts"] = {
        "before_artifact_ref": f"{session_id}-before-artifact",
        "candidate_artifact_ref": f"{session_id}-candidate-artifact",
        "after_artifact_ref": None,
    }
    replay_event = {
        "event_id": _next_event_id(timestamp, replay.get("events") or []),
        "session_id": session_id,
        "actor": "system",
        "actor_label": "Training Session Service",
        "event_type": "preview_trigger",
        "summary": "Replay candidate materialized from preview.",
        "message_body": None,
        "emitted_at": timestamp,
        "sequence_number": max(
            (int(event.get("sequence_number") or 0) for event in replay.get("events", [])),
            default=0,
        )
        + 1,
        "outcome_signal": "teaching-complete",
        "eval_ref": {
            "eval_id": preview.get("eval_id"),
            "baseline_snapshot_at": preview.get("baseline_snapshot_at"),
            "candidate_snapshot_at": candidate_snapshot_at,
        },
    }
    replay.setdefault("events", []).append(replay_event)
    store.append_event(replay_event)
    store.put_replay(session_id, replay)
    return replay


def _decide_replay(session_id: str, body: ReplayDecisionBody, state: str) -> Dict[str, Any]:
    replay = get_replay(session_id)
    timestamp = body.decided_at or utc_now()
    resolution = replay.setdefault("replay_resolution", {})
    if resolution.get("state") not in {"pending_decision", None}:
        raise HTTPException(status_code=409, detail="replay already decided")
    candidate_snapshot_at = _replay_candidate_snapshot_at(replay)
    if body.expected_candidate_snapshot_at and body.expected_candidate_snapshot_at != candidate_snapshot_at:
        raise HTTPException(status_code=409, detail="candidate snapshot mismatch")
    resolution["state"] = state
    resolution["decision_at"] = timestamp
    resolution["decision_by"] = body.actor_id
    resolution["note"] = body.note
    artifacts = replay.setdefault("artifacts", {})
    if state == "committed":
        artifacts["after_artifact_ref"] = f"{session_id}-committed-artifact"
    decision_event = {
        "event_id": _next_event_id(timestamp, replay.get("events") or []),
        "session_id": session_id,
        "actor": "system",
        "actor_label": "Training Session Service",
        "event_type": "commit" if state == "committed" else "discard",
        "summary": f"Replay candidate {state} by {body.actor_id}.",
        "message_body": None,
        "emitted_at": timestamp,
        "sequence_number": max(
            (int(event.get("sequence_number") or 0) for event in replay.get("events", [])),
            default=0,
        )
        + 1,
        "outcome_signal": None,
        "evidence_ref": None,
        "patch_delta": None,
        "eval_ref": {"candidate_snapshot_at": candidate_snapshot_at},
        "artifact_refs": {
            "before_artifact_ref": artifacts.get("before_artifact_ref"),
            "candidate_artifact_ref": artifacts.get("candidate_artifact_ref"),
            "after_artifact_ref": artifacts.get("after_artifact_ref"),
        },
    }
    replay.setdefault("events", []).append(decision_event)
    store.append_event(decision_event)
    store.put_replay(session_id, replay)
    return replay


@app.post("/api/training/replays/{session_id}/commit")
def commit_replay(session_id: str, body: ReplayDecisionBody) -> Dict[str, Any]:
    return _decide_replay(session_id, body, "committed")


@app.post("/api/training/replays/{session_id}/discard")
def discard_replay(session_id: str, body: ReplayDecisionBody) -> Dict[str, Any]:
    return _decide_replay(session_id, body, "discarded")
