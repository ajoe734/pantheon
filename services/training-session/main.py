from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture
from models import TeachingEvent, TeachingSession
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


class ReplayDecisionBody(BaseModel):
    expected_candidate_snapshot_at: Optional[str] = None
    actor_id: str = "operator"
    note: Optional[str] = None
    decided_at: Optional[str] = None


app = FastAPI(title="Pantheon Training Session Service", version="0.1.0")
STORE_BACKEND = os.getenv("TRAINING_SESSION_EVENT_STORE_BACKEND", "jsonl").strip().lower() or "jsonl"
PERSISTENCE_POSTURE = require_persistence_posture("training-session")
store = build_training_session_store(_data_dir())
register_fastapi_health_routes(
    app,
    "training-session",
    dependencies=lambda: {"persistence": PERSISTENCE_POSTURE.to_dict()},
    metrics=lambda: {
        "session_count": len(store.list_sessions()),
        "event_log_count": len(store.list_event_log()),
    },
    details=lambda: {
        "data_dir": _data_dir(),
        "store_backend": STORE_BACKEND,
        "persistence_posture": PERSISTENCE_POSTURE.to_dict(),
    },
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
    session = _teaching_session_contract({
        "id": session_id,
        "session_id": session_id,
        "persona_id": body.persona_id,
        "session_type": "trainer",
        "mode": body.mode,
        "objective": body.objective,
        "topic": body.objective,
        "status": "active",
        "started_at": timestamp,
        "ended_at": None,
        "opened_by": body.actor_id,
        "current_control_state_ref": body.current_control_state_ref,
        "trace_id": body.trace_id or f"trace-{uuid.uuid4().hex[:12]}",
        "context_refs": body.context_refs,
        "actor_context": {},
        "events": [],
        "outcomes": [],
    })
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
    event = _build_teaching_event(
        session_id=session_id,
        event_id=event_id,
        event_type=body.event_type,
        actor=body.actor,
        actor_type=body.actor_type,
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


from services.research.vectorbt.adapter.vectorbt_adapter import run_vectorbt_workflow, BacktestConfig

# ... (rest of imports)

# Add stub for OHLCV data
def _get_ohlcv_data(session_id: str) -> List[Dict[str, Any]]:
    # In a real implementation, this would fetch data based on context_refs
    # For now, return a dataset with enough bars for governed vectorbt (MIN_BARS=30)
    data = []
    start_date = datetime(2026, 1, 1)
    for instrument in ["STUB1", "STUB2"]:
        for i in range(35):
            date = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
            data.append({
                "instrument": instrument,
                "date": date,
                "open": 100.0 + i,
                "high": 105.0 + i,
                "low": 95.0 + i,
                "close": 100.0 + i,
                "volume": 1000.0 + i
            })
    return data

# ...

@app.post("/api/training/sessions/{session_id}/preview", status_code=201)
def refresh_preview(session_id: str, body: RefreshPreviewBody) -> Dict[str, Any]:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="training session not found")
    timestamp = body.refreshed_at or utc_now()
    controls = (store.get_controls(session_id) or {}).get("controls") or []
    
    # Extract strategy parameters
    strategy_params = {
        control.get("parameter_key"): control.get("current_value")
        for control in controls
        if isinstance(control, dict) and control.get("parameter_key")
    }
    
    # Prepare data for vectorbt
    ohlcv_records = _get_ohlcv_data(session_id)
    dataset = {
        "dataset_id": f"preview-{session_id}",
        "strategy_id": f"preview-{session_id}",
        "source_dataset_ref": "stub-ref",
        "records": ohlcv_records,
    }
    
    # Run backtest
    config = BacktestConfig(strategy_params=strategy_params)
    try:
        backtest_result = run_vectorbt_workflow(dataset, config=config)
        metrics = backtest_result.backtest_result.aggregate_metrics
        metric_delta = [
            {
                "metric_key": "total_return",
                "display_label": "Total Return",
                "baseline_value": 0.0,
                "candidate_value": metrics.get("mean_total_return", 0.0),
                "delta": metrics.get("mean_total_return", 0.0),
                "delta_pct": metrics.get("mean_total_return", 0.0) * 100,
                "unit": "pct",
                "direction": "up" if metrics.get("mean_total_return", 0.0) >= 0 else "down"
            },
            {
                "metric_key": "sharpe_ratio",
                "display_label": "Sharpe Ratio",
                "baseline_value": 0.0,
                "candidate_value": metrics.get("mean_sharpe_ratio", 0.0),
                "delta": metrics.get("mean_sharpe_ratio", 0.0),
                "delta_pct": 0.0,
                "unit": "ratio",
                "direction": "up"
            },
            {
                "metric_key": "max_drawdown",
                "display_label": "Max Drawdown",
                "baseline_value": 0.0,
                "candidate_value": metrics.get("mean_max_drawdown", 0.0),
                "delta": metrics.get("mean_max_drawdown", 0.0),
                "delta_pct": 0.0,
                "unit": "pct",
                "direction": "down"
            }
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backtest failed: {str(e)}")

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
        "metric_delta": metric_delta,
        "control_diff": control_diff,
        "preview_quality": "vectorbt_real",
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
    session = _teaching_session_contract(session)
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
    replay_eval_ref = {
        "eval_id": preview.get("eval_id"),
        "baseline_snapshot_at": preview.get("baseline_snapshot_at"),
        "candidate_snapshot_at": candidate_snapshot_at,
    }
    replay_event = _build_teaching_event(
        session_id=session_id,
        event_id=_next_event_id(timestamp, replay.get("events") or []),
        actor="system",
        actor_label="Training Session Service",
        event_type="preview_trigger",
        summary="Replay candidate materialized from preview.",
        timestamp=timestamp,
        sequence_number=max(
            (int(event.get("sequence_number") or 0) for event in replay.get("events", [])),
            default=0,
        )
        + 1,
        outcome_signal="teaching-complete",
        eval_ref=replay_eval_ref,
    )
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
    decision_artifact_refs = {
        "before_artifact_ref": artifacts.get("before_artifact_ref"),
        "candidate_artifact_ref": artifacts.get("candidate_artifact_ref"),
        "after_artifact_ref": artifacts.get("after_artifact_ref"),
    }
    decision_event = _build_teaching_event(
        session_id=session_id,
        event_id=_next_event_id(timestamp, replay.get("events") or []),
        actor="system",
        actor_label="Training Session Service",
        event_type="commit" if state == "committed" else "discard",
        summary=f"Replay candidate {state} by {body.actor_id}.",
        timestamp=timestamp,
        sequence_number=max(
            (int(event.get("sequence_number") or 0) for event in replay.get("events", [])),
            default=0,
        )
        + 1,
        eval_ref={"candidate_snapshot_at": candidate_snapshot_at},
        artifact_refs=decision_artifact_refs,
    )
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
