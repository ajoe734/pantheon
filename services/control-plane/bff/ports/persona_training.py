"""Domain port for Persona reads, Training Session trainer/replay, and rapid-eval.

This module formalizes three disposition-matrix items from the BFF
architecture-cleanup program into typed, testable domain ports:

- ACG-02-014 (MIGRATE): "Persona, teaching-session, and capability reads" --
  route reads through the Persona Registry read surface; extract pure DTO
  projectors.
- ACG-02-016 (MIGRATE): "Trainer session, controls, message, preview, and
  replay flows" -- route reads and lifecycle commands through the Training
  Session service; extract pure DTO projectors.
- ACG-02-017 (MERGE): "Rapid-evaluation requests" -- assign canonical owner
  based on caller evidence.

Every method in this module delegates to an injected store/backend or to
caller-provided callables. None of these ports owns raw entity storage or
direct file/DB access.
"""

from __future__ import annotations

import copy
import json
import os
import urllib.error
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException

try:
    from ..command_adapters.base import get_base_url, http_request_json
    from ..core.owner_reads import authorization, selected_tenant
except (ImportError, ValueError):
    from services.control_plane.bff.command_adapters.base import get_base_url, http_request_json
    from services.control_plane.bff.core.owner_reads import authorization, selected_tenant


# ---------------------------------------------------------------------------
# Persona Registry Reads Port
# ---------------------------------------------------------------------------

class PersonaRegistryReadsPort:
    """Port for persona, session, teaching, and capability reads.

    Delegates to the injected ``store`` (duck-typed to the Persona
    Registry-backed methods: ``list_personas``, ``get_persona``,
    ``get_bindings_for_persona``, ``list_sessions_for_persona``,
    ``list_teaching_sessions_for_persona``, and
    ``get_capability_snapshot_for_persona``).
    """

    def __init__(self, *, store: Optional[Any] = None) -> None:
        self._store = store

    def _require_store(self) -> Any:
        if self._store is None:
            raise RuntimeError(
                "PersonaRegistryReadsPort requires a Persona Registry-backed store"
            )
        return self._store

    def list_personas(
        self,
        *,
        lifecycle_state: Optional[str] = None,
        mandate: Optional[str] = None,
        strategy_family: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        store = self._require_store()
        return (
            store.list_personas(
                lifecycle_state=lifecycle_state,
                mandate=mandate,
                strategy_family=strategy_family,
            )
            or []
        )

    def get_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        store = self._require_store()
        persona = store.get_persona(persona_id)
        if not persona:
            return None
        payload = dict(persona)
        payload["bindings"] = store.get_bindings_for_persona(persona_id) or []
        return payload

    def list_persona_sessions(
        self, persona_id: str, *, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        store = self._require_store()
        return store.list_sessions_for_persona(persona_id, status=status) or []

    def list_persona_teaching_sessions(
        self, persona_id: str, *, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        store = self._require_store()
        return store.list_teaching_sessions_for_persona(persona_id, status=status) or []

    def get_persona_capabilities(self, persona_id: str) -> Optional[Dict[str, Any]]:
        store = self._require_store()
        return store.get_capability_snapshot_for_persona(persona_id)

    def get_capability_snapshot(
        self,
        snapshot_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if not snapshot_id:
            return None
        return self._require_store().get_capability_snapshot(snapshot_id)

    def get_capability_snapshot_for_persona(
        self,
        persona_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if not persona_id:
            return None
        return self._require_store().get_capability_snapshot_for_persona(persona_id)


# ---------------------------------------------------------------------------
# Training Session Trainer / Replay Port
# ---------------------------------------------------------------------------

class TrainingSessionTrainerPort:
    """Port for trainer session, controls, message, preview, and replay flows.

    Delegates to the injected ``training`` store, which is expected to be
    backed by the Training Session service (e.g. ``TrainingSessionHttpStore``)
    or a test double. This port owns no HTTP transport or local persistence
    of its own.
    """

    def __init__(self, *, training: Optional[Any] = None) -> None:
        self._training = training

    def _require_training(self) -> Any:
        if self._training is None:
            raise RuntimeError(
                "TrainingSessionTrainerPort requires a Training Session-backed store"
            )
        return self._training

    def create_trainer_session(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self._require_training().create_trainer_session(**kwargs)

    def list_trainer_sessions(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self._require_training().list_trainer_sessions(**kwargs) or []

    def get_trainer_session(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._require_training().get_trainer_session(session_id)

    def get_trainer_controls(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self._require_training().get_trainer_controls(session_id, **kwargs)

    def patch_trainer_controls(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self._require_training().patch_trainer_controls(session_id, **kwargs)

    def append_trainer_message(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self._require_training().append_trainer_message(session_id, **kwargs)

    def get_trainer_preview(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self._require_training().get_trainer_preview(session_id, **kwargs)

    def build_trainer_preview_unavailable(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        store = self._require_training()
        if hasattr(store, "build_trainer_preview_unavailable"):
            return store.build_trainer_preview_unavailable(session_id, **kwargs)
        return self.get_trainer_preview(session_id, **kwargs)

    def refresh_trainer_preview(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self._require_training().refresh_trainer_preview(session_id, **kwargs)

    def list_trainer_replays(self, **kwargs: Any) -> Any:
        return self._require_training().list_trainer_replays(**kwargs)

    def get_trainer_replay(self, session_id: Optional[str], **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self._require_training().get_trainer_replay(session_id, **kwargs)

    def commit_trainer_replay(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self._require_training().commit_trainer_replay(session_id, **kwargs)

    def discard_trainer_replay(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self._require_training().discard_trainer_replay(session_id, **kwargs)


# ---------------------------------------------------------------------------
# Rapid Evaluation Ownership (ACG-02-017)
# ---------------------------------------------------------------------------

RAPID_EVAL_OWNER = "training-session"
RAPID_EVAL_OWNER_EVIDENCE = (
    "services/training-session/rapid_eval_integration.py defines run_rapid_eval "
    "and is the only implementation module for rapid-evaluation execution in "
    "this repository, with its own dedicated test suite "
    "(services/training-session/test_rapid_eval_integration.py). "
    "services/control-plane/persona/persona_strategy_discovery.py only emits a "
    "run_rapid_eval RecommendedAction label; it does not import "
    "rapid_eval_integration or implement an evaluation backend, so it is not a "
    "competing owner. No implementation exists under services/research."
)


@dataclass(frozen=True)
class RapidEvaluationOwnership:
    """Caller-evidence record for the rapid-evaluation canonical owner.

    ACG-02-017 requires tracing production/test callers and selecting an
    existing owner before migration, and forbids inventing a new owner
    solely to retain unused JSON behavior. See ``RAPID_EVAL_OWNER_EVIDENCE``
    for the traced evidence backing this assignment.
    """

    owner: str = RAPID_EVAL_OWNER
    evidence: str = RAPID_EVAL_OWNER_EVIDENCE
    implementation_module: str = "services/training-session/rapid_eval_integration.py"
    implementation_symbol: str = "run_rapid_eval"


class RapidEvaluationPort:
    """Port for rapid-evaluation requests, bound to the Training Session owner.

    This port does not implement rapid-evaluation execution or persistence.
    It delegates to injected ``create``/``get`` callables that a caller binds
    to the Training Session owner (``rapid_eval_integration.run_rapid_eval``
    or an HTTP client for the Training Session service). Construction
    without a backend still exposes the resolved ownership so callers can
    assert it without invoking execution.
    """

    ownership: RapidEvaluationOwnership = RapidEvaluationOwnership()

    def __init__(
        self,
        *,
        create: Optional[Callable[..., Optional[Dict[str, Any]]]] = None,
        get: Optional[Callable[..., Optional[Dict[str, Any]]]] = None,
    ) -> None:
        self._create = create
        self._get = get

    @property
    def owner(self) -> str:
        return self.ownership.owner

    def create_rapid_eval(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        if self._create is None:
            raise RuntimeError(
                f"RapidEvaluationPort has no backend bound to its owner ({self.owner})"
            )
        return self._create(session_id, **kwargs)

    def get_rapid_eval(self, eval_id: Optional[str], **kwargs: Any) -> Optional[Dict[str, Any]]:
        if self._get is None:
            raise RuntimeError(
                f"RapidEvaluationPort has no backend bound to its owner ({self.owner})"
            )
        return self._get(eval_id, **kwargs)


# ---------------------------------------------------------------------------
# Composed Domain Port
# ---------------------------------------------------------------------------

class PersonaTrainingDomainPort:
    """Consolidated domain port for Persona, Trainer/Replay, and Rapid-Eval."""

    def __init__(
        self,
        *,
        persona_port: Optional[PersonaRegistryReadsPort] = None,
        trainer_port: Optional[TrainingSessionTrainerPort] = None,
        rapid_eval_port: Optional[RapidEvaluationPort] = None,
    ) -> None:
        self.persona = persona_port or PersonaRegistryReadsPort()
        self.trainer = trainer_port or TrainingSessionTrainerPort()
        self.rapid_eval = rapid_eval_port or RapidEvaluationPort()

    # Persona delegates
    def list_personas(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona.list_personas(**kwargs)

    def get_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.persona.get_persona(persona_id)

    def list_persona_sessions(self, persona_id: str, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona.list_persona_sessions(persona_id, **kwargs)

    def list_persona_teaching_sessions(self, persona_id: str, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona.list_persona_teaching_sessions(persona_id, **kwargs)

    def get_persona_capabilities(self, persona_id: str) -> Optional[Dict[str, Any]]:
        return self.persona.get_persona_capabilities(persona_id)

    def get_capability_snapshot(
        self,
        snapshot_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        return self.persona.get_capability_snapshot(snapshot_id)

    def get_capability_snapshot_for_persona(
        self,
        persona_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        return self.persona.get_capability_snapshot_for_persona(persona_id)

    # Trainer delegates
    def create_trainer_session(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.trainer.create_trainer_session(**kwargs)

    def list_trainer_sessions(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.trainer.list_trainer_sessions(**kwargs)

    def get_trainer_session(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.trainer.get_trainer_session(session_id)

    def get_trainer_controls(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.trainer.get_trainer_controls(session_id, **kwargs)

    def patch_trainer_controls(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.trainer.patch_trainer_controls(session_id, **kwargs)

    def append_trainer_message(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.trainer.append_trainer_message(session_id, **kwargs)

    def get_trainer_preview(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.trainer.get_trainer_preview(session_id, **kwargs)

    def build_trainer_preview_unavailable(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.trainer.build_trainer_preview_unavailable(session_id, **kwargs)

    def refresh_trainer_preview(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.trainer.refresh_trainer_preview(session_id, **kwargs)

    def list_trainer_replays(self, **kwargs: Any) -> Any:
        return self.trainer.list_trainer_replays(**kwargs)

    def get_trainer_replay(self, session_id: Optional[str], **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.trainer.get_trainer_replay(session_id, **kwargs)

    def commit_trainer_replay(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.trainer.commit_trainer_replay(session_id, **kwargs)

    def discard_trainer_replay(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.trainer.discard_trainer_replay(session_id, **kwargs)

    # Rapid-eval delegates
    @property
    def rapid_eval_owner(self) -> str:
        return self.rapid_eval.owner

    def create_rapid_eval(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.rapid_eval.create_rapid_eval(session_id, **kwargs)

    def get_rapid_eval(self, eval_id: Optional[str], **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.rapid_eval.get_rapid_eval(eval_id, **kwargs)


# ---------------------------------------------------------------------------
# Training Session DTO / Projection Mappings
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _trainer_session_allowed_actions(status: Optional[str]) -> Dict[str, bool]:
    return {"canSendMessage": str(status or "").strip().lower() == "active"}


def _project_teaching_event(session_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "event_id": event.get("event_id"),
        "session_id": session_id,
        "actor": event.get("actor"),
        "message_body": event.get("message_body") or event.get("summary"),
        "emitted_at": event.get("emitted_at") or event.get("timestamp"),
        "sequence_number": event.get("sequence_number"),
        "outcome_signal": event.get("outcome_signal"),
    }


def _extract_sorted_events(session_id: str, session: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = [e for e in (session.get("events") or []) if isinstance(e, dict)]
    raw.sort(key=lambda item: int(item.get("sequence_number") or 0))
    return [_project_teaching_event(session_id, e) for e in raw]


def _project_trainer_session_summary(
    session: Dict[str, Any], events: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    session_id = str(session.get("session_id") or session.get("id") or "")
    evs = events if events is not None else _extract_sorted_events(session_id, session)
    latest_signal = next((e.get("outcome_signal") for e in reversed(evs) if e.get("outcome_signal")), None)
    return {
        "message_count": len(evs),
        "last_event_at": evs[-1].get("emitted_at") if evs else None,
        "latest_outcome_signal": latest_signal,
    }


def _project_trainer_session_list_item(session: Dict[str, Any]) -> Dict[str, Any]:
    sid = str(session.get("session_id") or session.get("id") or "")
    summary = _project_trainer_session_summary(session)
    status = session.get("status")
    return {
        "session_id": sid,
        "persona_id": session.get("persona_id"),
        "session_type": session.get("session_type") or "trainer",
        "objective": session.get("objective") or session.get("topic"),
        "status": status,
        "started_at": session.get("started_at"),
        "ended_at": session.get("ended_at") or session.get("completed_at"),
        "message_count": summary["message_count"],
        "last_event_at": summary["last_event_at"],
        "latest_outcome_signal": summary["latest_outcome_signal"],
        "actor_context": session.get("actor_context") or {"persona_display_name": None, "persona_role_context": None},
        "allowedActions": _trainer_session_allowed_actions(status),
        "links": {"workbench_detail": f"/trainer/sessions/{sid}"},
    }


def _project_trainer_session_detail(session: Dict[str, Any]) -> Dict[str, Any]:
    sid = str(session.get("session_id") or session.get("id") or "")
    events = _extract_sorted_events(sid, session)
    status = session.get("status")
    return {
        "session_id": sid,
        "persona_id": session.get("persona_id"),
        "session_type": session.get("session_type") or "trainer",
        "objective": session.get("objective") or session.get("topic"),
        "status": status,
        "started_at": session.get("started_at"),
        "ended_at": session.get("ended_at") or session.get("completed_at"),
        "opened_by": session.get("opened_by") or session.get("operator_id"),
        "context_refs": list(session.get("context_refs") or []),
        "actor_context": session.get("actor_context") or {"persona_display_name": None, "persona_role_context": None},
        "session_summary": _project_trainer_session_summary(session, events=events),
        "events": events,
        "allowedActions": _trainer_session_allowed_actions(status),
        "links": {"self": f"/api/v1/trainer/sessions/{sid}", "workbench_detail": f"/trainer/sessions/{sid}"},
    }


def _project_trainer_preview_payload(
    preview: Dict[str, Any], *, session_status: Optional[str] = None, snapshot_at: Optional[str] = None
) -> Dict[str, Any]:
    payload = copy.deepcopy(preview)
    now = snapshot_at or _utc_now()
    status = str(payload.get("status") or "preview_unavailable").strip().lower()
    warnings = list(payload.get("warnings") or [])
    counts = {"critical": 0, "high": 0, "medium": 0, "informational": 0}
    for w in warnings:
        if isinstance(w, dict) and (lvl := str(w.get("level") or "").strip().lower()) in counts:
            counts[lvl] += 1
    can_refresh = str(session_status or "").strip().lower() in ("active", "paused") and status != "pending"
    payload.update({
        "status": status,
        "warnings": warnings,
        "warning_count_by_level": payload.get("warning_count_by_level") or counts,
        "allowedActions": {"canRefreshPreview": can_refresh},
    })
    if not (isinstance(payload.get("polling"), dict) and payload["polling"].get("deadline_at")):
        payload["polling"] = {"enabled": status == "pending", "poll_interval_ms": 3000, "max_wait_ms": 45000, "deadline_at": None}
    meta = payload.setdefault("meta", {})
    meta["snapshot_at"] = str(meta.get("snapshot_at") or now)
    meta.setdefault("surfaces", {}).setdefault("trainer_preview", "ok")
    if status == "preview_unavailable":
        payload["degraded_copy"] = {"title": "Trainer preview is temporarily unavailable", "body": "No preview evaluation is currently available."}
    elif status == "failed" and not payload.get("degraded_copy"):
        payload["degraded_copy"] = {"title": "Trainer preview could not complete", "body": payload.get("failure_reason") or "Pantheon could not finish the rapid-eval for this candidate."}
    else:
        payload.setdefault("degraded_copy", None)
    for field in ("control_diff", "metric_delta"):
        payload.setdefault(field, [])
    for field in ("baseline_snapshot_at", "candidate_snapshot_at", "error_code", "failure_reason", "owner_job_ref", "job_id"):
        payload.setdefault(field, None)
    return payload


def _project_job_trainer_preview(
    session_id: str,
    job: Dict[str, Any],
    *,
    session_status: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(job, dict) or not str(job.get("job_id") or "").strip() or not str(job.get("eval_id") or "").strip():
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "DEPENDENCY_UNAVAILABLE",
                    "message": "Training session service returned malformed preview job response missing job_id or eval_id",
                    "status_code": 503,
                }
            },
        )
    st = str(job.get("status") or "").strip().lower()
    if st not in ("queued", "running", "failed"):
        return None
    job_id, eval_id = str(job["job_id"]).strip(), str(job["eval_id"]).strip()
    ts = timestamp or str(job.get("requested_at") or _utc_now())
    is_failed = st == "failed"
    reason = str(job.get("failure_reason") or job.get("error_code") or "").strip()
    warnings = [{"level": "high", "message": reason}] if (is_failed and reason) else []
    can_refresh = str(session_status or "").strip().lower() in ("active", "paused") if is_failed else False
    return {
        "session_id": session_id,
        "status": "failed" if is_failed else "pending",
        "eval_id": eval_id,
        "job_id": job_id,
        "owner_job_ref": {"job_id": job_id, "eval_id": eval_id},
        "error_code": job.get("error_code"),
        "failure_reason": job.get("failure_reason"),
        "baseline_snapshot_at": ts,
        "candidate_snapshot_at": ts,
        "control_diff": [],
        "metric_delta": [],
        "warnings": warnings,
        "warning_count_by_level": {"critical": 0, "high": len(warnings), "medium": 0, "informational": 0},
        "preview_quality": "insufficient_data" if is_failed else "directional_only",
        "allowedActions": {"canRefreshPreview": can_refresh},
        "polling": {"enabled": not is_failed, "poll_interval_ms": 3000, "max_wait_ms": 45000, "deadline_at": None},
        "degraded_copy": {
            "title": "Trainer preview could not complete",
            "body": reason or "Pantheon could not finish the rapid-eval for this candidate.",
        } if is_failed else None,
        "meta": {"snapshot_at": ts, "surfaces": {"trainer_preview": "degraded" if is_failed else "ok"}},
    }


def _can_act_replay(status: Any, resolution_state: str, surface_state: str, candidate_ref: Any) -> bool:
    return (
        str(status or "").strip().lower() == "completed"
        and resolution_state == "pending_decision"
        and surface_state not in ("degraded", "unavailable")
        and bool(candidate_ref)
    )


def _project_trainer_replay_list_item(session: Dict[str, Any], *, surface_state: str = "ok") -> Dict[str, Any]:
    sid = str(session.get("session_id") or session.get("id") or "")
    events = sorted([e for e in (session.get("events") or []) if isinstance(e, dict)], key=lambda e: int(e.get("sequence_number") or 0))
    resolution = session.get("replay_resolution") or {}
    res_state = str(resolution.get("state") or "pending_decision")
    artifacts = session.get("artifacts") or {}
    can_act = _can_act_replay(session.get("status"), res_state, surface_state, artifacts.get("candidate_artifact_ref"))
    latest_signal = next((e.get("outcome_signal") for e in reversed(events) if e.get("outcome_signal")), None)
    return {
        "session_id": sid,
        "persona_id": session.get("persona_id"),
        "objective": session.get("objective"),
        "status": session.get("status"),
        "started_at": session.get("started_at"),
        "ended_at": session.get("ended_at"),
        "event_count": len(events),
        "latest_event_type": events[-1].get("event_type") if events else None,
        "latest_outcome_signal": latest_signal,
        "replay_resolution": {"state": res_state},
        "allowedActions": {"canReplay": surface_state != "unavailable", "canCommit": can_act, "canDiscard": can_act},
        "links": {"replay_detail": f"/trainer/replay/{sid}"},
    }


def _extract_replay_events(session_id: str, session: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Teaching events plus the owner replay event_type/eval_ref used for candidate comparison."""
    raw = [e for e in (session.get("events") or []) if isinstance(e, dict)]
    raw.sort(key=lambda item: int(item.get("sequence_number") or 0))
    events = []
    for source in raw:
        event = _project_teaching_event(session_id, source)
        for field in ("event_type", "eval_ref"):
            if field in source:
                event[field] = source[field]
        events.append(event)
    return events


def _project_trainer_replay_detail(
    session: Dict[str, Any], *, surface_state: str = "ok", snapshot_at: Optional[str] = None
) -> Dict[str, Any]:
    sid = str(session.get("session_id") or session.get("id") or "")
    events = _extract_replay_events(sid, session)
    resolution = session.get("replay_resolution") or {"state": "pending_decision", "decision_at": None, "decision_by": None, "note": None}
    res_state = str(resolution.get("state") or "pending_decision")
    artifacts = session.get("artifacts") or {"before_artifact_ref": None, "candidate_artifact_ref": None, "after_artifact_ref": None}
    can_act = _can_act_replay(session.get("status"), res_state, surface_state, artifacts.get("candidate_artifact_ref"))
    latest_signal = next((e.get("outcome_signal") for e in reversed(events) if e.get("outcome_signal")), None)
    return {
        "session_id": sid,
        "persona_id": session.get("persona_id"),
        "objective": session.get("objective"),
        "status": session.get("status"),
        "started_at": session.get("started_at"),
        "ended_at": session.get("ended_at"),
        "replay_resolution": resolution,
        "artifacts": artifacts,
        "event_summary": {
            "event_count": len(events),
            "first_sequence_number": events[0]["sequence_number"] if events else None,
            "last_sequence_number": events[-1]["sequence_number"] if events else None,
            "latest_outcome_signal": latest_signal,
        },
        "events": events,
        "allowedActions": {"canReplay": surface_state != "unavailable", "canCommit": can_act, "canDiscard": can_act},
        "links": {"self": f"/trainer/replay/{sid}", "session_detail": f"/trainer/sessions/{sid}", "replay_detail": f"/trainer/replay/{sid}"},
        "meta": {"snapshot_at": snapshot_at or _utc_now(), "surfaces": {"trainer_replay": surface_state}},
    }


# ---------------------------------------------------------------------------
# Training Session HTTP Store (Owner-Backed)
# ---------------------------------------------------------------------------

class TrainingSessionHttpStore:
    """Production HTTP-backed store for the Training Session owner service.

    Talks to the existing Training Session service at PANTHEON_TRAINING_SESSION_API_URL
    using http_request_json and request-scoped caller auth/tenant forwarding.
    """

    def __init__(self, *, base_url: Optional[str] = None) -> None:
        self._explicit_base_url = base_url

    def _resolve_base_url(self) -> str:
        return self._explicit_base_url or get_base_url("PANTHEON_TRAINING_SESSION_API_URL", "PANTHEON_TRAINING_SESSION_URL")

    def _request(
        self,
        path: str,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
        allow_404: bool = False,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        url = f"{self._resolve_base_url().rstrip('/')}{path}"
        auth = authorization.get()
        token = auth.removeprefix("Bearer ").strip() if auth else None
        tenant = selected_tenant.get()
        try:
            extra = {"headers": headers} if headers else {}
            return http_request_json(url, method=method, payload=payload, auth_token=token, tenant_id=tenant, **extra)
        except urllib.error.HTTPError as exc:
            if allow_404 and exc.code == 404:
                return None
            err_body = {}
            try:
                raw = exc.read().decode("utf-8")
                err_body = json.loads(raw) if raw else {}
                if hasattr(exc, "fp") and hasattr(exc.fp, "seek"):
                    exc.fp.seek(0)
            except Exception:
                pass
            detail = err_body.get("detail") or f"Training session service returned HTTP {exc.code}"
            if 400 <= exc.code < 500:
                raise HTTPException(status_code=exc.code, detail=detail) from exc
            raise HTTPException(
                status_code=503,
                detail={"error": {"code": "DEPENDENCY_UNAVAILABLE", "message": f"Training session service returned HTTP {exc.code}", "status_code": 503}},
            ) from exc
        except Exception as exc:
            if isinstance(exc, (HTTPException, StarletteHTTPException)):
                raise
            raise HTTPException(
                status_code=503,
                detail={"error": {"code": "DEPENDENCY_UNAVAILABLE", "message": f"Training session service unavailable: {exc}", "status_code": 503}},
            ) from exc

    def create_trainer_session(
        self,
        *,
        persona_id: str,
        objective: str,
        context_refs: Optional[List[Dict[str, Any]]] = None,
        actor_id: str = "operator",
        created_at: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        payload = {"persona_id": persona_id, "objective": objective, "context_refs": context_refs or [], "actor_id": actor_id, "created_at": created_at}
        raw = self._request("/api/training/sessions", method="POST", payload=payload)
        return _project_trainer_session_detail(raw) if isinstance(raw, dict) else None

    def list_trainer_sessions(
        self, *, persona_id: Optional[str] = None, status: Optional[str] = None, **kwargs: Any
    ) -> List[Dict[str, Any]]:
        params = [f"persona_id={urllib.parse.quote(str(persona_id))}"] if persona_id else []
        if status:
            params.append(f"status={urllib.parse.quote(str(status))}")
        raw = self._request(f"/api/training/sessions{('?' + '&'.join(params)) if params else ''}", method="GET")
        if not isinstance(raw, list):
            return []
        items = [_project_trainer_session_list_item(s) for s in raw if isinstance(s, dict)]
        items.sort(key=lambda item: item.get("last_event_at") or item.get("started_at") or "", reverse=True)
        return items

    def get_trainer_session(self, session_id: Optional[str], **kwargs: Any) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        raw = self._request(f"/api/training/sessions/{urllib.parse.quote(str(session_id))}", method="GET", allow_404=True)
        return _project_trainer_session_detail(raw) if isinstance(raw, dict) else None

    def get_trainer_controls(
        self, session_id: str, *, snapshot_at: Optional[str] = None, **kwargs: Any
    ) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        session = self.get_trainer_session(session_id)
        if session is None:
            return None
        now = snapshot_at or _utc_now()
        raw = self._request(f"/api/training/controls/{urllib.parse.quote(str(session_id))}", method="GET", allow_404=True)
        controls = list(raw.get("controls") or []) if isinstance(raw, dict) else []
        status = str(session.get("status") or "")
        return {
            "object_ref": {"type": "TrainerControlState", "id": session_id},
            "session_id": session_id,
            "status": session.get("status"),
            "controls": controls,
            "allowedActions": {"canPatchControls": status.strip().lower() == "active"},
            "meta": {
                "snapshot_at": now,
                "staleness": {"served_from": "owner_records", "last_known_at": now, "status": "fresh"},
                "surfaces": {"trainer_controls": {"state": "ok"}},
            },
        }

    def patch_trainer_controls(
        self, session_id: str, patches: List[Dict[str, Any]], *, patched_at: Optional[str] = None, **kwargs: Any
    ) -> Optional[Dict[str, Any]]:
        now = patched_at or _utc_now()
        raw = self._request(f"/api/training/sessions/{urllib.parse.quote(str(session_id))}/controls", method="POST", payload={"patches": patches, "patched_at": patched_at})
        if not isinstance(raw, dict):
            return None
        controls = list(raw.get("controls") or raw.get("current_controls") or [])
        meta = {
            "snapshot_at": now,
            "staleness": {"served_from": "owner_records", "last_known_at": now, "status": "fresh"},
            "surfaces": {"trainer_controls": {"state": "ok"}},
        }
        if raw.get("status") == "rejected":
            return {
                "session_id": session_id,
                "status": "rejected",
                "error_code": "CONTROL_PATCH_VALIDATION_FAILED",
                "message": "Patch contains invalid control updates.",
                "field_errors": list(raw.get("field_errors") or []),
                "rejected_changes": [],
                "current_controls": controls,
                "allowedActions": {"canPatchControls": True},
                "meta": meta,
            }
        updated_diff = [
            {"field": row.get("parameter_key"), "before": row.get("previous_value"), "after": row.get("new_value"), "validation_status": "accepted"}
            for row in list(raw.get("patch_delta") or [])
            if isinstance(row, dict)
        ]
        return {
            "session_id": session_id,
            "status": "accepted",
            "message": "Patch applied successfully.",
            "warnings": [],
            "diff": {"updated_controls": updated_diff},
            "current_controls": controls,
            "allowedActions": {"canPatchControls": True},
            "meta": meta,
        }

    def append_trainer_message(
        self, session_id: str, *, message_body: str, actor_id: str = "operator", accepted_at: Optional[str] = None, **kwargs: Any
    ) -> Optional[Dict[str, Any]]:
        timestamp = accepted_at or _utc_now()
        payload: Dict[str, Any] = {"actor": actor_id, "event_type": kwargs.get("event_type", "message"), "message_body": message_body, "emitted_at": timestamp}
        for opt in ("summary", "outcome_signal", "evidence_ref", "patch_delta", "eval_ref", "artifact_refs"):
            if opt in kwargs:
                payload[opt] = kwargs[opt]
        raw = self._request(f"/api/training/sessions/{urllib.parse.quote(str(session_id))}/events", method="POST", payload=payload)
        if not isinstance(raw, dict) or not isinstance(raw.get("session"), dict):
            return None
        event = raw.get("event") if isinstance(raw.get("event"), dict) else {}
        return {
            "accepted_at": raw.get("accepted_at") or timestamp,
            "event": _project_teaching_event(session_id, event),
            "session": _project_trainer_session_detail(raw["session"]),
        }

    def get_trainer_preview(
        self, session_id: str, *, session_status: Optional[str] = None, eval_id: Optional[str] = None, snapshot_at: Optional[str] = None, **kwargs: Any
    ) -> Optional[Dict[str, Any]]:
        raw = self._request(f"/api/training/previews/{urllib.parse.quote(str(session_id))}", method="GET", allow_404=True)
        if not isinstance(raw, dict):
            return None
        jobs = self._request(f"/api/training/preview-jobs?session_id={urllib.parse.quote(str(session_id))}", method="GET")
        if not isinstance(jobs, list):
            raise HTTPException(
                status_code=503,
                detail={"error": {"code": "DEPENDENCY_UNAVAILABLE", "message": "Training session service returned malformed preview job list response", "status_code": 503}},
            )
        evaluations = raw.get("evaluations") if isinstance(raw.get("evaluations"), dict) else {}
        if eval_id:
            if eval_id in evaluations and isinstance(evaluations[eval_id], dict):
                return _project_trainer_preview_payload(evaluations[eval_id], session_status=session_status, snapshot_at=snapshot_at)
            matching_job = next((j for j in reversed(jobs) if isinstance(j, dict) and str(j.get("eval_id") or "") == eval_id), None)
            if matching_job:
                return _project_job_trainer_preview(session_id, matching_job, session_status=session_status, timestamp=snapshot_at)
            return None
        if jobs:
            latest_job = jobs[-1]
            if isinstance(latest_job, dict):
                job_status = str(latest_job.get("status") or "").lower()
                job_eval_id = str(latest_job.get("eval_id") or "")
                if job_status in ("queued", "running", "failed") and (not evaluations or str(raw.get("latest_eval_id") or "") != job_eval_id):
                    return _project_job_trainer_preview(session_id, latest_job, session_status=session_status, timestamp=snapshot_at)
        preview = None
        latest_id = str(raw.get("latest_eval_id") or "").strip()
        if latest_id and isinstance(evaluations.get(latest_id), dict):
            preview = evaluations[latest_id]
        elif isinstance(raw.get("preview"), dict):
            preview = raw.get("preview")
        elif evaluations:
            preview = next(iter(evaluations.values()))
        if not isinstance(preview, dict):
            return None
        return _project_trainer_preview_payload(preview, session_status=session_status, snapshot_at=snapshot_at)

    def build_trainer_preview_unavailable(
        self, session_id: str, *, session_status: Optional[str] = None, snapshot_at: Optional[str] = None, control_diff: Optional[List[Dict[str, Any]]] = None, **kwargs: Any
    ) -> Dict[str, Any]:
        now = snapshot_at or _utc_now()
        return {
            "session_id": session_id,
            "status": "preview_unavailable",
            "eval_id": None,
            "job_id": None,
            "owner_job_ref": None,
            "error_code": None,
            "failure_reason": None,
            "baseline_snapshot_at": None,
            "candidate_snapshot_at": None,
            "control_diff": list(control_diff or []),
            "metric_delta": [],
            "warnings": [],
            "warning_count_by_level": {"critical": 0, "high": 0, "medium": 0, "informational": 0},
            "preview_quality": "not_available",
            "allowedActions": {"canRefreshPreview": False},
            "polling": {"enabled": False, "poll_interval_ms": 3000, "max_wait_ms": 45000, "deadline_at": None},
            "degraded_copy": {"title": "Trainer preview is unavailable", "body": "No evaluation has been completed for this session."},
            "meta": {"snapshot_at": now, "surfaces": {"trainer_preview": "unavailable"}},
        }

    def refresh_trainer_preview(
        self, session_id: str, *, session_status: Optional[str] = None, refreshed_at: Optional[str] = None, **kwargs: Any
    ) -> Optional[Dict[str, Any]]:
        timestamp = refreshed_at or _utc_now()
        existing = self.get_trainer_preview(session_id, session_status=session_status, snapshot_at=timestamp)
        if existing and existing.get("status") == "pending":
            return existing
        payload = {"mode": kwargs.get("mode") or "refresh", "requested_by": kwargs.get("actor_id") or "operator", "requested_at": timestamp}
        job = self._request(f"/api/training/sessions/{urllib.parse.quote(str(session_id))}/preview-jobs", method="POST", payload=payload)
        return _project_job_trainer_preview(session_id, job, session_status=session_status, timestamp=timestamp)

    def list_trainer_replays(
        self, *, persona_id: Optional[str] = None, status: Optional[str] = None, snapshot_at: Optional[str] = None, **kwargs: Any
    ) -> Tuple[List[Dict[str, Any]], str]:
        params = [f"persona_id={urllib.parse.quote(str(persona_id))}"] if persona_id else []
        if status:
            params.append(f"status={urllib.parse.quote(str(status))}")
        raw = self._request(f"/api/training/replays{('?' + '&'.join(params)) if params else ''}", method="GET")
        if not isinstance(raw, list):
            return [], "ok"
        items = [_project_trainer_replay_list_item(s, surface_state="ok") for s in raw if isinstance(s, dict)]
        items.sort(key=lambda item: item.get("ended_at") or item.get("started_at") or "", reverse=True)
        return items, "ok"

    def get_trainer_replay(
        self, session_id: Optional[str], *, snapshot_at: Optional[str] = None, **kwargs: Any
    ) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        raw = self._request(f"/api/training/replays/{urllib.parse.quote(str(session_id))}", method="GET", allow_404=True)
        return _project_trainer_replay_detail(raw, surface_state="ok", snapshot_at=snapshot_at) if isinstance(raw, dict) else None

    def _decide_trainer_replay(
        self, session_id: str, *, action: str, expected_snapshot: str, note: Optional[str] = None, actor_id: str = "operator", decided_at: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        timestamp = decided_at or _utc_now()
        payload = {"expected_candidate_snapshot_at": expected_snapshot, "note": note, "actor_id": actor_id, "decided_at": timestamp}
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        raw = self._request(
            f"/api/training/replays/{urllib.parse.quote(str(session_id))}/{action}", method="POST", payload=payload, headers=headers
        )
        if not isinstance(raw, dict):
            return None
        projected = _project_trainer_replay_detail(raw, surface_state="ok", snapshot_at=timestamp)
        res = projected.get("replay_resolution") or {}
        events = projected.get("events") or []
        return {
            "session_id": session_id,
            "status": projected.get("status"),
            "replay_resolution": res,
            "artifacts": projected.get("artifacts"),
            f"{action}ed_at": res.get("decision_at") or timestamp,
            f"{action}ed_by": res.get("decision_by") or actor_id,
            "event": events[-1] if events else None,
            "allowedActions": projected.get("allowedActions"),
            "meta": projected.get("meta"),
        }

    def commit_trainer_replay(
        self, session_id: str, *, expected_candidate_snapshot_at: str, note: Optional[str] = None, actor_id: str = "operator", committed_at: Optional[str] = None, idempotency_key: Optional[str] = None, **kwargs: Any
    ) -> Optional[Dict[str, Any]]:
        return self._decide_trainer_replay(session_id, action="commit", expected_snapshot=expected_candidate_snapshot_at, note=note, actor_id=actor_id, decided_at=committed_at, idempotency_key=idempotency_key)

    def discard_trainer_replay(
        self, session_id: str, *, expected_candidate_snapshot_at: str, note: Optional[str] = None, actor_id: str = "operator", discarded_at: Optional[str] = None, idempotency_key: Optional[str] = None, **kwargs: Any
    ) -> Optional[Dict[str, Any]]:
        return self._decide_trainer_replay(session_id, action="discard", expected_snapshot=expected_candidate_snapshot_at, note=note, actor_id=actor_id, decided_at=discarded_at, idempotency_key=idempotency_key)
