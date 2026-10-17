from __future__ import annotations

import base64
import copy
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from ports import ReadSurfacePorts, TrainingSessionTrainerPort, create_in_memory_read_surface_ports
from services.control_plane.bff.ports.read_surface_ports import create_read_surface_ports
from services.control_plane.bff.ports.persona_training import TrainingSessionHttpStore
from services.control_plane.bff.core.owner_reads import authorization, selected_tenant
from services.control_plane.bff.training.router import create_training_router
from services.control_plane.bff.auth.policy import (
    bff_error,
    default_utc_now,
    extract_identity_stub,
    require_read_role,
)


def _page_slice(
    items: list[dict[str, Any]],
    page_token: str | None,
    page_size: int,
) -> tuple[list[dict[str, Any]], str | None]:
    start = int(page_token) if page_token else 0
    end = start + page_size
    next_page = str(end) if end < len(items) else None
    return items[start:end], next_page


def _dataset_surface_status(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {"status": "ok"}


_ACTIVE_SESSION = "trn-20260419-001"
_COMPLETED_SESSION = "trn-20260418-003"


def _event(
    event_id: str,
    sequence_number: int,
    *,
    event_type: str = "message",
    message_body: str = "Trainer event",
    outcome_signal: str | None = None,
    evidence_ref: dict[str, Any] | None = None,
    patch_delta: list[dict[str, Any]] | None = None,
    eval_ref: dict[str, Any] | None = None,
    artifact_refs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "session_id": _COMPLETED_SESSION,
        "actor": "operator",
        "actor_label": "Operator",
        "event_type": event_type,
        "message_body": message_body,
        "summary": message_body,
        "emitted_at": f"2026-04-18T08:{sequence_number:02d}:00Z",
        "sequence_number": sequence_number,
        "outcome_signal": outcome_signal,
        "evidence_ref": evidence_ref,
        "patch_delta": patch_delta,
        "eval_ref": eval_ref,
        "artifact_refs": artifact_refs,
    }


def _initial_sessions() -> dict[str, dict[str, Any]]:
    actor_context = {
        "persona_display_name": "Alpha Persona",
        "persona_role_context": "systematic momentum coach",
    }
    return {
        _ACTIVE_SESSION: {
            "session_id": _ACTIVE_SESSION,
            "persona_id": "persona-alpha",
            "session_type": "trainer",
            "objective": (
                "Tighten event-window response and reduce premature signal reversals "
                "during macro surprise sessions."
            ),
            "status": "active",
            "started_at": "2026-04-19T19:30:00Z",
            "ended_at": None,
            "opened_by": "operator-hedging-desk",
            "context_refs": [],
            "actor_context": actor_context,
            "events": [
                {
                    "event_id": f"tevt-20260419-00{index}",
                    "session_id": _ACTIVE_SESSION,
                    "actor": "operator" if index != 2 else "persona",
                    "message_body": f"Active trainer message {index}",
                    "emitted_at": timestamp,
                    "sequence_number": index,
                    "outcome_signal": outcome,
                }
                for index, timestamp, outcome in (
                    (1, "2026-04-19T19:30:11Z", None),
                    (2, "2026-04-19T19:31:04Z", "acknowledged-adjustment"),
                    (3, "2026-04-19T19:37:40Z", "candidate-adjustment-ready"),
                )
            ],
        },
        _COMPLETED_SESSION: {
            "session_id": _COMPLETED_SESSION,
            "persona_id": "persona-alpha",
            "session_type": "trainer",
            "objective": "Reduce regime-switch whipsaw sensitivity in drawdown containment mode.",
            "status": "completed",
            "started_at": "2026-04-18T08:00:00Z",
            "ended_at": "2026-04-18T08:42:00Z",
            "opened_by": "operator-risk-desk",
            "context_refs": [],
            "actor_context": actor_context,
            "events": [],
        },
    }


def _initial_replays() -> dict[str, dict[str, Any]]:
    evidence_ref = {
        "type": "paper_live_drift",
        "id": "runtime-042",
        "display_label": "Runtime drawdown evidence",
        "url_pattern": "/operator/paper-live-drift/runtime-042",
    }
    artifacts = {
        "before_artifact_ref": "artifact-042-before",
        "candidate_artifact_ref": "artifact-042-candidate",
        "after_artifact_ref": None,
    }
    completed_events = [
        _event("tevt-20260418-001", 1, message_body="Review drawdown containment."),
        _event(
            "tevt-20260418-002",
            2,
            event_type="control_patch",
            message_body="Increase containment confirmation.",
            evidence_ref=evidence_ref,
            patch_delta=[
                {
                    "parameter_key": "minimum_hold_bars",
                    "previous_value": 3,
                    "new_value": 4,
                }
            ],
        ),
        _event(
            "tevt-20260418-003",
            3,
            event_type="preview_trigger",
            message_body="Evaluate the candidate containment controls.",
            eval_ref={
                "eval_id": "teval-20260418-003",
                "baseline_snapshot_at": "2026-04-18T08:10:00Z",
                "candidate_snapshot_at": "2026-04-18T08:18:00Z",
            },
            artifact_refs=artifacts,
        ),
    ]
    return {
        _COMPLETED_SESSION: {
            **_initial_sessions()[_COMPLETED_SESSION],
            "events": completed_events,
            "replay_resolution": {
                "state": "pending_decision",
                "decision_at": None,
                "decision_by": None,
                "note": None,
            },
            "artifacts": artifacts,
        },
        "trn-20260417-001": {
            "session_id": "trn-20260417-001",
            "persona_id": "persona-alpha",
            "session_type": "trainer",
            "objective": "Previously committed trainer replay.",
            "status": "completed",
            "started_at": "2026-04-17T08:00:00Z",
            "ended_at": "2026-04-17T08:30:00Z",
            "events": [
                _event(
                    "tevt-20260417-001",
                    1,
                    event_type="preview_trigger",
                    eval_ref={
                        "eval_id": "teval-20260417-001",
                        "baseline_snapshot_at": "artifact-041-baseline",
                        "candidate_snapshot_at": "artifact-041-candidate",
                    },
                )
            ],
            "replay_resolution": {
                "state": "committed",
                "decision_at": "2026-04-17T08:30:00Z",
                "decision_by": "operator-risk-desk",
                "note": None,
            },
            "artifacts": {
                "before_artifact_ref": "artifact-041-before",
                "candidate_artifact_ref": "artifact-041-candidate",
                "after_artifact_ref": "artifact-041-after",
            },
        },
    }


def _complete_preview() -> dict[str, Any]:
    warnings = [
        {"level": "high", "message": "Upper bound pressure."},
        {"level": "medium", "message": "Limited regime coverage."},
        {"level": "informational", "message": "Spread gate unchanged."},
    ]
    return {
        "session_id": _ACTIVE_SESSION,
        "status": "complete",
        "eval_id": "teval-20260419-014",
        "baseline_snapshot_at": "2026-04-19T19:43:30Z",
        "candidate_snapshot_at": "2026-04-19T19:48:00Z",
        "control_diff": [],
        "metric_delta": [],
        "warnings": warnings,
        "warning_count_by_level": {
            "critical": 0,
            "high": 1,
            "medium": 1,
            "informational": 1,
        },
        "preview_quality": "directional_only",
        "allowedActions": {"canRefreshPreview": True},
        "polling": {
            "enabled": False,
            "poll_interval_ms": 3000,
            "max_wait_ms": 45000,
            "deadline_at": None,
        },
        "degraded_copy": {
            "title": "Trainer preview is served from a local snapshot",
            "body": "Treat this compare as directional until the owner service is available.",
        },
        "meta": {"surfaces": {"trainer_preview": "stale"}},
    }


def _pending_preview(eval_id: str = "teval-20260419-015") -> dict[str, Any]:
    return {
        "session_id": _ACTIVE_SESSION,
        "status": "pending",
        "eval_id": eval_id,
        "baseline_snapshot_at": "2026-04-19T19:43:30Z",
        "candidate_snapshot_at": "2026-04-19T19:50:00Z",
        "control_diff": [],
        "metric_delta": [],
        "warnings": [],
        "warning_count_by_level": {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "informational": 0,
        },
        "preview_quality": "directional_only",
        "allowedActions": {"canRefreshPreview": False},
        "polling": {
            "enabled": True,
            "poll_interval_ms": 3000,
            "max_wait_ms": 45000,
            "deadline_at": "2026-04-20T19:50:45Z",
        },
        "degraded_copy": {"title": "Trainer preview is still running"},
        "meta": {"surfaces": {"trainer_preview": "stale"}},
    }


class TrainingReadSurfaceDouble(ReadSurfacePorts):
    """Writable typed double for the Training Session-owned BFF boundary."""

    def __init__(self) -> None:
        base = create_in_memory_read_surface_ports(
            persona_capital_runtime_kwargs={
                "personas": [
                    {
                        "id": "persona-alpha",
                        "persona_id": "persona-alpha",
                        "name": "Alpha Persona",
                        "mandate": "systematic momentum coach",
                    }
                ]
            }
        )
        super().__init__(
            operations_consultation=base.operations_consultation,
            persona_capital_runtime=base.persona_capital_runtime,
            ooda_management=base.ooda_management,
            research_knowledge_source=base.research_knowledge_source,
            lifecycle_telemetry_governance=base.lifecycle_telemetry_governance,
            persona_training=base.persona_training,
        )
        self.sessions = _initial_sessions()
        self.replays = _initial_replays()
        self.previews = {
            _ACTIVE_SESSION: {
                "latest_eval_id": "teval-20260419-014",
                "evaluations": {
                    "teval-20260419-014": _complete_preview(),
                    "teval-20260419-015": _pending_preview(),
                },
            }
        }
        self.rapid_evals: dict[str, dict[str, Any]] = {}
        self._next_session = 4
        self._next_eval = 1

    def dataset_source(self, dataset: str) -> str:
        if dataset in {"teaching_sessions", "trainer_replays"}:
            return "local_snapshot"
        return super().dataset_source(dataset)

    @staticmethod
    def _allowed(status: str) -> dict[str, bool]:
        return {"canSendMessage": status == "active"}

    @staticmethod
    def _summary(session: dict[str, Any]) -> dict[str, Any]:
        events = list(session.get("events") or [])
        return {
            "message_count": len(events),
            "last_event_at": events[-1].get("emitted_at") if events else None,
            "latest_outcome_signal": next(
                (event.get("outcome_signal") for event in reversed(events) if event.get("outcome_signal")),
                None,
            ),
        }

    def _detail(self, session: dict[str, Any]) -> dict[str, Any]:
        session_id = str(session["session_id"])
        status = str(session.get("status") or "")
        events = sorted(copy.deepcopy(session.get("events") or []), key=lambda row: row["sequence_number"])
        return {
            "session_id": session_id,
            "persona_id": session.get("persona_id"),
            "session_type": session.get("session_type") or "trainer",
            "objective": session.get("objective"),
            "status": status,
            "started_at": session.get("started_at"),
            "ended_at": session.get("ended_at"),
            "opened_by": session.get("opened_by"),
            "context_refs": copy.deepcopy(session.get("context_refs") or []),
            "actor_context": copy.deepcopy(session.get("actor_context") or {}),
            "session_summary": self._summary(session),
            "events": events,
            "allowedActions": self._allowed(status),
            "links": {
                "self": f"/api/v1/trainer/sessions/{session_id}",
                "workbench_detail": f"/trainer/sessions/{session_id}",
            },
        }

    def create_trainer_session(self, **kwargs: Any) -> dict[str, Any]:
        timestamp = kwargs.get("created_at") or "2026-04-20T00:00:00Z"
        session_id = f"trn-{str(timestamp)[:10].replace('-', '')}-{self._next_session:03d}"
        self._next_session += 1
        session = {
            "session_id": session_id,
            "persona_id": kwargs["persona_id"],
            "session_type": "trainer",
            "objective": kwargs["objective"],
            "status": "active",
            "started_at": timestamp,
            "ended_at": None,
            "opened_by": kwargs.get("actor_id"),
            "context_refs": copy.deepcopy(kwargs.get("context_refs") or []),
            "actor_context": {
                "persona_display_name": "Alpha Persona",
                "persona_role_context": "systematic momentum coach",
            },
            "events": [],
        }
        self.sessions[session_id] = session
        self._persist_sessions()
        return self._detail(session)

    def list_trainer_sessions(self, **kwargs: Any) -> list[dict[str, Any]]:
        persona_id = kwargs.get("persona_id")
        status = kwargs.get("status")
        rows = [
            self._detail(session)
            for session in self.sessions.values()
            if session.get("persona_id") == persona_id and (not status or session.get("status") == status)
        ]
        rows.sort(key=lambda row: row.get("started_at") or "", reverse=True)
        for row in rows:
            row.pop("events", None)
            row.pop("session_summary", None)
            row.pop("context_refs", None)
            row.pop("opened_by", None)
            row.pop("links", None)
            summary = self._summary(self.sessions[row["session_id"]])
            row.update(summary)
            row["links"] = {"workbench_detail": f"/trainer/sessions/{row['session_id']}"}
        return rows

    def get_trainer_session(self, session_id: str | None) -> dict[str, Any] | None:
        session = self.sessions.get(str(session_id))
        return self._detail(session) if session else None

    def append_trainer_message(self, session_id: str, **kwargs: Any) -> dict[str, Any] | None:
        session = self.sessions.get(session_id)
        if not session:
            return None
        timestamp = kwargs.get("accepted_at") or "2026-04-20T00:00:00Z"
        sequence = len(session.get("events") or []) + 1
        event = {
            "event_id": f"tevt-{session_id}-{sequence:03d}",
            "session_id": session_id,
            "actor": "operator",
            "message_body": kwargs["message_body"],
            "emitted_at": timestamp,
            "sequence_number": sequence,
            "outcome_signal": None,
        }
        session.setdefault("events", []).append(event)
        self._persist_sessions()
        return {"accepted_at": timestamp, "event": copy.deepcopy(event), "session": self._detail(session)}

    @staticmethod
    def _control_defaults() -> dict[str, dict[str, Any]]:
        return {
            _ACTIVE_SESSION: {
                "session_id": _ACTIVE_SESSION,
                "status": "active",
                "controls": [
                    {
                        "parameter_key": "reversal_threshold",
                        "current_value": 0.55,
                        "allowed_range": {"min": 0.1, "max": 1.0},
                    },
                    {
                        "parameter_key": "minimum_hold_bars",
                        "current_value": 3,
                        "allowed_range": {"min": 1, "max": 8},
                    },
                ],
            },
            _COMPLETED_SESSION: {
                "session_id": _COMPLETED_SESSION,
                "status": "completed",
                "controls": [],
            },
        }

    def _load_controls(self) -> tuple[dict[str, dict[str, Any]], Path | None]:
        path_value = os.getenv("PANTHEON_BFF_TRAINER_CONTROL_STORE")
        if not path_value:
            return self._control_defaults(), None
        path = Path(path_value)
        if not path.exists():
            return {}, path
        return json.loads(path.read_text(encoding="utf-8")), path

    def get_trainer_controls(self, session_id: str, **kwargs: Any) -> dict[str, Any] | None:
        records, path = self._load_controls()
        record = records.get(session_id)
        if record is None:
            session = self.sessions.get(session_id)
            if not session:
                return None
            record = {"session_id": session_id, "status": session["status"], "controls": []}
        controls = copy.deepcopy(record.get("controls") or [])
        for control in controls:
            if control.get("parameter_key") == "reversal_threshold":
                control.setdefault("allowed_range", {"min": 0.1, "max": 1.0})
            elif control.get("parameter_key") == "minimum_hold_bars":
                control.setdefault("allowed_range", {"min": 1, "max": 8})
        status = str(record.get("status") or self.sessions.get(session_id, {}).get("status") or "")
        writable = path is not None and status == "active"
        return {
            "object_ref": {"type": "TrainerControlState", "id": session_id},
            "session_id": session_id,
            "status": status,
            "controls": controls,
            "allowedActions": {"canPatchControls": writable},
            "meta": {
                "surfaces": {"trainer_controls": {"state": "ok" if path else "degraded"}},
                "staleness": {"status": "fresh" if path else "stale"},
            },
        }

    def patch_trainer_controls(self, session_id: str, **kwargs: Any) -> dict[str, Any] | None:
        records, path = self._load_controls()
        if path is None or session_id not in records:
            return None
        controls = records[session_id].setdefault("controls", [])
        by_key = {row["parameter_key"]: row for row in controls}
        field_errors = []
        updated = []
        for patch in kwargs.get("patches") or []:
            key = patch["parameter_key"]
            requested = patch["proposed_value"]
            current = by_key.get(key)
            ranges = {
                "reversal_threshold": {"min": 0.1, "max": 1.0},
                "minimum_hold_bars": {"min": 1, "max": 8},
            }
            allowed = ranges.get(key)
            if current is None or allowed is None:
                field_errors.append({"field": key, "reason": "unknown_parameter_key"})
            elif requested < allowed["min"] or requested > allowed["max"]:
                field_errors.append(
                    {
                        "field": key,
                        "reason": "exceeds_allowed_range",
                        "current_value": current["current_value"],
                        "requested_value": requested,
                        "allowed_range": allowed,
                    }
                )
            else:
                before = current["current_value"]
                current["current_value"] = requested
                updated.append(
                    {
                        "field": key,
                        "before": before,
                        "after": requested,
                        "validation_status": "accepted",
                    }
                )
        path.write_text(json.dumps(records, indent=2, ensure_ascii=True), encoding="utf-8")
        current_controls = copy.deepcopy(controls)
        return {
            "session_id": session_id,
            "status": "rejected" if field_errors else "accepted",
            "error_code": "CONTROL_PATCH_VALIDATION_FAILED" if field_errors else None,
            "field_errors": field_errors,
            "rejected_changes": [],
            "warnings": [],
            "diff": {"updated_controls": updated},
            "current_controls": current_controls,
            "allowedActions": {"canPatchControls": True},
            "meta": {"surfaces": {"trainer_controls": {"state": "ok"}}},
        }

    def build_trainer_preview_unavailable(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
        can_refresh = str(kwargs.get("session_status") or "") in {"active", "paused"}
        return {
            "session_id": session_id,
            "status": "preview_unavailable",
            "eval_id": None,
            "metric_delta": [],
            "control_diff": [],
            "warnings": [],
            "warning_count_by_level": {
                "critical": 0,
                "high": 0,
                "medium": 0,
                "informational": 0,
            },
            "preview_quality": "not_available",
            "allowedActions": {"canRefreshPreview": can_refresh},
            "polling": {"enabled": False, "poll_interval_ms": 3000, "max_wait_ms": 45000, "deadline_at": None},
            "meta": {"surfaces": {"trainer_preview": "degraded"}},
            "degraded_copy": {"title": "Trainer preview is temporarily unavailable"},
        }

    def get_trainer_preview(self, session_id: str, **kwargs: Any) -> dict[str, Any] | None:
        bundle = self.previews.get(session_id)
        eval_id = kwargs.get("eval_id")
        if os.getenv("PANTHEON_BFF_TRAINER_PREVIEW_STORE") and not bundle:
            return None
        if not bundle:
            return None
        selected = eval_id or bundle.get("latest_eval_id")
        preview = bundle.get("evaluations", {}).get(selected)
        return copy.deepcopy(preview) if preview else None

    def refresh_trainer_preview(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
        eval_id = f"teval-service-{self._next_eval:03d}"
        self._next_eval += 1
        preview = _pending_preview(eval_id)
        self.previews[session_id] = {"latest_eval_id": eval_id, "evaluations": {eval_id: preview}}
        path_value = os.getenv("PANTHEON_BFF_TRAINER_PREVIEW_STORE")
        if path_value:
            Path(path_value).write_text(json.dumps(self.previews, indent=2), encoding="utf-8")
        return copy.deepcopy(preview)

    def create_rapid_eval(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
        eval_id = f"reval-{self._next_eval:06d}"
        self._next_eval += 1
        record = {
            "rapid_eval_id": eval_id,
            "session_id": session_id,
            "status": "queued",
            "completed_at": None,
            "advisory_note": "Rapid evaluation is advisory and does not activate production writes.",
            **kwargs,
            "meta": {"surfaces": {"rapid_eval": "ok"}},
        }
        self.rapid_evals[eval_id] = record
        path_value = os.getenv("PANTHEON_BFF_RAPID_EVAL_STORE")
        if path_value:
            Path(path_value).write_text(json.dumps(self.rapid_evals, indent=2), encoding="utf-8")
        return copy.deepcopy(record)

    def get_rapid_eval(self, eval_id: str | None, **kwargs: Any) -> dict[str, Any] | None:
        record = self.rapid_evals.get(str(eval_id))
        return copy.deepcopy(record) if record else None

    def add_replay(self, replay: dict[str, Any]) -> None:
        record = copy.deepcopy(replay)
        for event in record.get("events") or []:
            ref = event.get("evidence_ref")
            if isinstance(ref, dict) and ref.get("url_pattern") == "/telemetry/drawdown/tel-drawdown-2026-04-18":
                ref["url_pattern"] = "/operator/paper-live-drift/runtime-042"
        self.replays[str(record["session_id"])] = record

    def _replay_detail(self, replay: dict[str, Any], *, surface: str | None = None) -> dict[str, Any]:
        record = copy.deepcopy(replay)
        events = sorted(record.get("events") or [], key=lambda row: int(row.get("sequence_number") or 0))
        state = str((record.get("replay_resolution") or {}).get("state") or "pending_decision")
        surface_state = surface or str(record.get("meta", {}).get("surfaces", {}).get("trainer_replay") or "stale")
        actionable = state == "pending_decision" and surface_state != "degraded"
        record["events"] = events
        record["event_summary"] = {
            "event_count": len(events),
            "first_sequence_number": events[0]["sequence_number"] if events else None,
            "last_sequence_number": events[-1]["sequence_number"] if events else None,
        }
        record["allowedActions"] = {
            "canReplay": surface_state != "unavailable",
            "canCommit": actionable,
            "canDiscard": actionable,
        }
        session_id = record["session_id"]
        record["links"] = {
            "self": f"/trainer/replay/{session_id}",
            "session_detail": f"/trainer/sessions/{session_id}",
            "replay_detail": f"/trainer/replay/{session_id}",
        }
        record["meta"] = {"surfaces": {"trainer_replay": surface_state}}
        return record

    def list_trainer_replays(self, **kwargs: Any):
        persona_id = kwargs.get("persona_id")
        status = kwargs.get("status")
        rows = []
        surfaces = []
        for replay in self.replays.values():
            if replay.get("persona_id") != persona_id or (status and replay.get("status") != status):
                continue
            detail = self._replay_detail(replay)
            surfaces.append(detail["meta"]["surfaces"]["trainer_replay"])
            rows.append(
                {
                    key: detail.get(key)
                    for key in (
                        "session_id",
                        "persona_id",
                        "objective",
                        "status",
                        "started_at",
                        "ended_at",
                        "replay_resolution",
                        "allowedActions",
                        "links",
                    )
                }
                | {
                    "event_count": detail["event_summary"]["event_count"],
                    "latest_event_type": detail["events"][-1]["event_type"] if detail["events"] else None,
                    "latest_outcome_signal": next(
                        (e.get("outcome_signal") for e in reversed(detail["events"]) if e.get("outcome_signal")),
                        None,
                    ),
                }
            )
        surface = "degraded" if "degraded" in surfaces else "stale"
        return rows, surface

    def get_trainer_replay(self, session_id: str | None, **kwargs: Any) -> dict[str, Any] | None:
        replay = self.replays.get(str(session_id))
        return self._replay_detail(replay) if replay else None

    def _resolve_replay(self, session_id: str, state: str, actor_key: str, at_key: str, **kwargs: Any):
        replay = self.replays.get(session_id)
        if replay is None:
            return None
        timestamp = kwargs.get(at_key) or "2026-04-20T00:00:00Z"
        actor = kwargs.get("actor_id") or "operator"
        replay["replay_resolution"] = {
            "state": state,
            "decision_at": timestamp,
            "decision_by": actor,
            "note": kwargs.get("note"),
        }
        artifacts = copy.deepcopy(replay.get("artifacts") or {})
        if state == "committed":
            artifacts["after_artifact_ref"] = artifacts.get("candidate_artifact_ref")
        else:
            artifacts["after_artifact_ref"] = None
        replay["artifacts"] = artifacts
        sequence = len(replay.get("events") or []) + 1
        event = _event(
            f"tevt-{session_id}-{state}",
            sequence,
            event_type="commit" if state == "committed" else "discard",
            message_body=kwargs.get("note") or f"Trainer candidate {state}.",
            artifact_refs=artifacts,
        )
        event["session_id"] = session_id
        replay.setdefault("events", []).append(event)
        detail = self._replay_detail(replay)
        return {
            "session_id": session_id,
            "replay_resolution": detail["replay_resolution"],
            "allowedActions": detail["allowedActions"],
            actor_key: actor,
            at_key: timestamp,
            "event": event,
            "artifacts": artifacts,
            "meta": detail["meta"],
        }

    def commit_trainer_replay(self, session_id: str, **kwargs: Any):
        return self._resolve_replay(session_id, "committed", "committed_by", "committed_at", **kwargs)

    def discard_trainer_replay(self, session_id: str, **kwargs: Any):
        return self._resolve_replay(session_id, "discarded", "discarded_by", "discarded_at", **kwargs)

    def _persist_sessions(self) -> None:
        path_value = os.getenv("PANTHEON_BFF_TEACHING_SESSION_STORE")
        if path_value:
            Path(path_value).write_text(json.dumps(self.sessions, indent=2), encoding="utf-8")


def create_training_read_surface_double() -> TrainingReadSurfaceDouble:
    return TrainingReadSurfaceDouble()


class _TrainingSessionDouble:
    def __init__(self) -> None:
        self.calls = []

    def create_trainer_session(self, **kwargs):
        self.calls.append(("create", kwargs))
        return {"session_id": "trn-service-001", **kwargs, "allowedActions": {"canSendMessage": True}}

    def append_trainer_message(self, session_id, **kwargs):
        self.calls.append(("message", session_id, kwargs))
        return {"event": {"session_id": session_id, "message_body": kwargs["message_body"]}}


def test_trainer_session_port_delegates_to_training_service_double() -> None:
    training = _TrainingSessionDouble()
    port = TrainingSessionTrainerPort(training=training)

    session = port.create_trainer_session(
        persona_id="persona-alpha", objective="Service-backed trainer session", context_refs=[], actor_id="operator-1"
    )
    message = port.append_trainer_message("trn-service-001", message_body="Adjust max drawdown.", actor_id="operator-1")

    assert session["session_id"] == "trn-service-001"
    assert session["allowedActions"]["canSendMessage"] is True
    assert message["event"]["message_body"] == "Adjust max drawdown."
    assert [call[0] for call in training.calls] == ["create", "message"]


# ---------------------------------------------------------------------------
# Live Training Session Owner Service Integration Tests (AC-1, AC-3, AC-5)
# ---------------------------------------------------------------------------

from services.runtime_auth_inbound import encode_jwt_hs256


def _load_isolated_module(name: str, filepath: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, filepath)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module {name} from {filepath}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def live_training_session_service():
    repo_root = Path(__file__).resolve().parents[3]
    service_dir = repo_root / "services" / "training-session"
    tests_dir = service_dir / "tests"

    strict_support = _load_isolated_module("strict_test_support", tests_dir / "strict_test_support.py")
    fixture = strict_support.materialize_strict_authority(tempfile.mkdtemp())
    env_backup = dict(os.environ)
    os.environ.update(fixture.environment())
    jwt_secret = "owned-original-training-inbound-test-secret-only"
    jwt_issuer = "pantheon-dev"
    jwt_audience = "bff-operators"
    os.environ["TRAINING_SESSION_AUTH_DISABLED"] = "false"
    os.environ["TRAINING_SESSION_AUTH_MODE"] = "strict"
    os.environ["TRAINING_SESSION_JWT_SECRET"] = jwt_secret
    os.environ["TRAINING_SESSION_JWT_ISSUER"] = jwt_issuer
    os.environ["TRAINING_SESSION_JWT_AUDIENCE"] = jwt_audience
    os.environ["TRAINING_SESSION_MFA_REQUIRED"] = "false"
    os.environ["TRAINING_SESSION_ALLOWED_CALLER_SERVICES"] = "control-plane-bff,training-session-preview-worker"
    os.environ["TRAINING_SESSION_ALLOWED_GATEWAY_SERVICES"] = "control-plane-bff"

    os.environ["PANTHEON_ENV"] = "dev"
    os.environ["PANTHEON_DEPLOYMENT_STAGE"] = "dev"
    os.environ["PANTHEON_BFF_JWT_SECRET"] = jwt_secret
    os.environ["PANTHEON_BFF_JWT_ISSUER"] = jwt_issuer
    os.environ["PANTHEON_BFF_JWT_AUDIENCE"] = jwt_audience
    os.environ["PANTHEON_BFF_MFA_REQUIRED"] = "false"
    os.environ["PANTHEON_BFF_DEV_LOGIN_CLIENT_ID"] = "pantheon-dev-operator"
    os.environ["PANTHEON_BFF_DEV_LOGIN_CLIENT_SECRET"] = "dev-operator-secret-123"
    os.environ["PANTHEON_BFF_TENANT_ID"] = "tenant-live"
    os.environ["PANTHEON_BFF_ALLOWED_TENANTS"] = "tenant-live,tenant-legit,tenant-alpha,tenant-beta"
    os.environ["PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_ID"] = "pantheon-dev-viewer"
    os.environ["PANTHEON_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET"] = "dev-viewer-secret-123"
    os.environ["PANTHEON_BFF_DEV_LOGIN_VIEWER_TENANT_ID"] = "tenant-live"
    os.environ["PANTHEON_BFF_DEV_LOGIN_VIEWER_ALLOWED_TENANTS"] = "tenant-live,tenant-alpha"

    modules_to_load = [
        "models",
        "policy_lineage",
        "inbound_authority",
        "evaluation_authority",
        "source_dataset_authority",
        "persona_target",
        "consultation_client",
        "store",
    ]
    for mod_name in modules_to_load:
        _load_isolated_module(mod_name, service_dir / f"{mod_name}.py")

    training_service = _load_isolated_module("training_session_live_app", service_dir / "main.py")
    training_service.store = training_service.TrainingSessionStore(fixture.data_dir)
    training_service._trusted_now = lambda: strict_support.FIXED_TRUSTED_NOW
    training_service.run_vectorbt_workflow = strict_support.make_fake_real_vectorbt_workflow()
    training_service._read_target_precondition = strict_support.make_fake_target_precondition_reader()
    training_service._commit_authoritative_persona_target = strict_support.make_fake_persona_target_commit()

    import uvicorn

    config = uvicorn.Config(training_service.app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    while not server.started:
        time.sleep(0.05)

    port = server.servers[0].sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    os.environ["PANTHEON_TRAINING_SESSION_API_URL"] = base_url

    try:
        yield base_url, training_service
    finally:
        server.should_exit = True
        thread.join(timeout=2)
        os.environ.clear()
        os.environ.update(env_backup)


def _signed_caller_jwt(tenant: str, actor: str = "control-plane-bff") -> str:
    secret = os.environ.get("TRAINING_SESSION_JWT_SECRET", "owned-original-training-inbound-test-secret-only")
    return encode_jwt_hs256(
        {
            "sub": "control-plane-bff",
            "service": "control-plane-bff",
            "tenant_ids": [tenant],
            "delegated_actor_id": actor,
            "roles": ["training-service"],
            "iss": os.environ.get("TRAINING_SESSION_JWT_ISSUER", "pantheon-dev"),
            "aud": os.environ.get("TRAINING_SESSION_JWT_AUDIENCE", "bff-operators"),
            "exp": time.time() + 3600,
        },
        secret=secret,
    )


def _dev_login(
    *,
    client_id: str = "pantheon-dev-operator",
    client_secret: str = "dev-operator-secret-123",
    roles: list[str] | None = None,
    tenant_id: str = "tenant-live",
) -> tuple[str, dict[str, Any]]:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.control_plane.bff.auth.policy import create_auth_dependencies
    from services.control_plane.bff.auth.handlers import create_auth_handlers
    from services.control_plane.bff.auth.service import AuthFacadeService
    from services.control_plane.bff.auth.router import create_auth_router

    deps = create_auth_dependencies()
    handlers = create_auth_handlers(dependencies=deps)
    auth_service = AuthFacadeService(local_readiness=handlers["bff_auth_readiness"], handlers=handlers)
    auth_router = create_auth_router(service=auth_service)
    app = FastAPI()
    app.include_router(auth_router)
    client = TestClient(app)

    payload: dict[str, Any] = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "tenant_id": tenant_id,
    }
    if roles is not None:
        payload["roles"] = roles

    resp = client.post("/bff/auth/dev-login", json=payload)
    assert resp.status_code == 200, f"Dev login failed: {resp.status_code} {resp.text}"
    token = resp.json()["access_token"]
    raw_payload = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "==").decode("utf-8"))
    return token, raw_payload


def test_default_production_factory_live_owner_roundtrip(live_training_session_service):
    """Verify default create_read_surface_ports against genuine Training Session service with strict signed auth."""
    base_url, training_service = live_training_session_service
    ports = create_read_surface_ports()
    assert ports.persona_training.trainer._training.__class__.__name__ == "TrainingSessionHttpStore"

    token, claims = _dev_login(
        client_id="pantheon-dev-operator",
        client_secret="dev-operator-secret-123",
        roles=["operator"],
        tenant_id="tenant-live",
    )
    assert "service" not in claims
    assert "service_id" not in claims
    assert "training-service" not in claims.get("roles", [])
    assert "operator" in claims.get("roles", [])
    assert "mfa" not in claims
    assert "amr" not in claims
    assert claims.get("sub") == "pantheon-dev-operator"
    assert claims.get("tenant_id") == "tenant-live"

    auth_token = authorization.set(f"Bearer {token}")
    tenant_token = selected_tenant.set("tenant-live")

    try:
        # 1. Create session
        created = ports.create_trainer_session(
            persona_id="persona-alpha",
            objective="Tune momentum response during macro surprise",
            actor_id="pantheon-dev-operator",
        )
        assert created is not None
        session_id = created["session_id"]
        assert session_id.startswith("trn-")
        assert created["status"] == "active"
        assert created["opened_by"] == "pantheon-dev-operator"
        assert created["actor_context"]["authenticated_actor_id"] == "pantheon-dev-operator"
        assert created["actor_context"]["actor_service"] == "control-plane-bff"
        assert created["allowedActions"]["canSendMessage"] is True

        # 1a. Initial preview for genuine newly created session: no computation yet -> canRefreshPreview is True
        initial_preview = ports.get_trainer_preview(session_id, session_status="active")
        assert initial_preview is not None
        assert initial_preview["status"] == "preview_unavailable"
        assert initial_preview["allowedActions"]["canRefreshPreview"] is True


        # 2. List sessions
        sessions = ports.list_trainer_sessions()
        assert len(sessions) >= 1
        listed = next((s for s in sessions if s["session_id"] == session_id), None)
        assert listed is not None
        assert listed["allowedActions"]["canSendMessage"] is True
        assert listed["links"]["workbench_detail"] == f"/trainer/sessions/{session_id}"

        # 3. Readback session detail
        detail = ports.get_trainer_session(session_id)
        assert detail is not None
        assert detail["session_id"] == session_id
        assert detail["objective"] == "Tune momentum response during macro surprise"
        assert detail["opened_by"] == "pantheon-dev-operator"

        # 4. Append trainer message
        msg = ports.append_trainer_message(
            session_id,
            message_body="Adjust minimum hold bars to 4",
            actor_id="pantheon-dev-operator",
        )
        assert msg is not None
        assert msg["event"]["message_body"] == "Adjust minimum hold bars to 4"
        assert msg["event"]["actor"] == "pantheon-dev-operator"
        assert len(msg["session"]["events"]) == 1


        # 5. Controls: test rejected unknown control first
        patch_rejected = ports.patch_trainer_controls(
            session_id,
            patches=[{"parameter_key": "unknown_param", "proposed_value": 1}],
        )
        assert patch_rejected is not None
        assert patch_rejected["status"] == "rejected"
        assert patch_rejected["error_code"] == "CONTROL_PATCH_VALIDATION_FAILED"

        # Seed controls on the session and test accepted control patch
        training_service.store.put_controls(
            session_id,
            {
                "session_id": session_id,
                "tenant_id": "tenant-live",
                "controls": [
                    {
                        "parameter_key": "risk.max_drawdown",
                        "current_value": 0.08,
                        "allowed_range": {"min": 0.02, "max": 0.2},
                    }
                ],
            },
        )
        ctrls = ports.get_trainer_controls(session_id)
        assert ctrls is not None
        assert ctrls["session_id"] == session_id
        assert ctrls["allowedActions"]["canPatchControls"] is True

        patch_accepted = ports.patch_trainer_controls(
            session_id,
            patches=[{"parameter_key": "risk.max_drawdown", "proposed_value": 0.1}],
        )
        assert patch_accepted is not None
        assert patch_accepted["status"] == "accepted"
        assert patch_accepted["current_controls"][0]["current_value"] == 0.1

        # 6. Preview refresh via BFF original router:
        # authentic owner-present active initial preview requests the FIRST original async preview job
        app = FastAPI()
        @app.exception_handler(HTTPException)
        @app.exception_handler(StarletteHTTPException)
        async def _http_exception_handler(request: Any, exc: Any) -> JSONResponse:
            if isinstance(exc.detail, dict) and "error" in exc.detail:
                return JSONResponse(status_code=exc.status_code, content=exc.detail)
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": {"code": "HTTP_ERROR", "message": str(exc.detail)}},
            )
        router = create_training_router(
            read_surface=ports,
            extract_identity=extract_identity_stub,
            require_read_role=require_read_role,
            bff_error=bff_error,
            utc_now=default_utc_now,
            page_slice=_page_slice,
            dataset_surface_status=_dataset_surface_status,
        )
        app.include_router(router)
        client = TestClient(app)

        resp = client.post(
            f"/api/v1/trainer/sessions/{session_id}/preview",
            json={"refresh_mode": "manual"},
            headers={"Authorization": "Bearer pantheon-dev-operator:operator"},
        )
        assert resp.status_code == 200, f"Expected 200 from BFF POST /preview, got {resp.status_code}: {resp.text}"
        preview = resp.json()
        assert preview is not None
        assert preview["status"] == "pending"
        assert preview["job_id"] is not None
        assert preview["eval_id"] is not None
        assert preview["owner_job_ref"]["job_id"] == preview["job_id"]
        assert preview["owner_job_ref"]["eval_id"] == preview["eval_id"]
        assert preview["polling"]["enabled"] is True
        assert preview["allowedActions"]["canRefreshPreview"] is False

        # 6a. Pending reread & no second admission (both existing ports and fresh store instance)
        reread_preview = ports.get_trainer_preview(session_id, session_status="active")
        assert reread_preview is not None
        assert reread_preview["status"] == "pending"
        assert reread_preview["allowedActions"]["canRefreshPreview"] is False
        assert reread_preview["job_id"] == preview["job_id"]

        # Validate same original owner pending truth after fresh TrainingSessionHttpStore instance (no local cache dependency)
        fresh_store = TrainingSessionHttpStore()
        fresh_preview = fresh_store.get_trainer_preview(session_id, session_status="active")
        assert fresh_preview is not None
        assert fresh_preview["status"] == "pending"
        assert fresh_preview["allowedActions"]["canRefreshPreview"] is False
        assert fresh_preview["job_id"] == preview["job_id"]

        # Verify exactly one queued job in owner store before repeat
        owner_jobs_before = [j for j in training_service.store.list_preview_jobs() if j.get("session_id") == session_id]
        assert len(owner_jobs_before) == 1
        assert owner_jobs_before[0]["job_id"] == preview["job_id"]

        resp_repeat = client.post(
            f"/api/v1/trainer/sessions/{session_id}/preview",
            json={"refresh_mode": "manual"},
            headers={"Authorization": "Bearer pantheon-dev-operator:operator"},
        )
        assert resp_repeat.status_code == 200
        assert resp_repeat.json()["job_id"] == preview["job_id"]
        assert resp_repeat.json()["eval_id"] == preview["eval_id"]

        # Verify still exactly one queued job in owner store after repeat (no second admission)
        owner_jobs_after = [j for j in training_service.store.list_preview_jobs() if j.get("session_id") == session_id]
        assert len(owner_jobs_after) == 1

        # 6b. Native worker route POST /api/training/preview-jobs/{job_id}/run with legitimate lab worker JWT/header:
        # Candidate controls have not changed, so native admission rejects candidate controls before compute
        worker_jwt = encode_jwt_hs256(
            {
                "sub": "training-session-preview-worker",
                "service": "training-session-preview-worker",
                "tenant_ids": ["tenant-live"],
                "roles": ["training-service"],
                "iss": "pantheon-dev",
                "aud": "bff-operators",
                "exp": time.time() + 3600,
            },
            secret=os.environ.get("TRAINING_SESSION_JWT_SECRET", "owned-original-training-inbound-test-secret-only"),
        )
        worker_headers = {
            "Authorization": f"Bearer {worker_jwt}",
            "X-Tenant-Id": "tenant-live",
            "X-Pantheon-Service": "training-session-preview-worker",
            "Content-Type": "application/json",
        }
        worker_req = urllib.request.Request(
            f"{base_url}/api/training/preview-jobs/{preview['job_id']}/run",
            data=b"{}",
            headers=worker_headers,
            method="POST",
        )
        with urllib.request.urlopen(worker_req) as run_resp:
            assert run_resp.status == 200
            run_data = json.loads(run_resp.read().decode())
            assert run_data["status"] == "failed"
            assert run_data["error_code"] == "evaluation_admission_rejected"
            assert run_data["failure_reason"] == "candidate controls do not contain an effective change"
            assert run_data["retryable"] is False

        # 6c. Fresh BFF readback of failed preview:
        # Port and router truthfully propagate failed job identity, error code, reason, and warnings
        failed_preview = ports.get_trainer_preview(session_id, session_status="active")
        assert failed_preview is not None
        assert failed_preview["status"] == "failed"
        assert failed_preview["job_id"] == preview["job_id"]
        assert failed_preview["eval_id"] == preview["eval_id"]
        assert failed_preview["owner_job_ref"] == {"job_id": preview["job_id"], "eval_id": preview["eval_id"]}
        assert failed_preview["error_code"] == "evaluation_admission_rejected"
        assert failed_preview["failure_reason"] == "candidate controls do not contain an effective change"
        assert failed_preview["allowedActions"]["canRefreshPreview"] is True
        assert failed_preview["polling"]["enabled"] is False
        assert len(failed_preview["warnings"]) == 1
        assert failed_preview["warnings"][0]["level"] == "high"
        assert failed_preview["warnings"][0]["message"] == "candidate controls do not contain an effective change"
        assert failed_preview["warning_count_by_level"]["high"] == 1
        assert failed_preview["preview_quality"] == "insufficient_data"
        assert failed_preview["meta"]["surfaces"]["trainer_preview"] == "degraded"

        # Reread via fresh store instance also propagates failure truthfully
        fresh_failed = fresh_store.get_trainer_preview(session_id, session_status="active")
        assert fresh_failed is not None
        assert fresh_failed["status"] == "failed"
        assert fresh_failed["job_id"] == preview["job_id"]
        assert fresh_failed["error_code"] == "evaluation_admission_rejected"

        # Lookup by eval_id propagates failed preview
        by_eval = ports.get_trainer_preview(session_id, eval_id=preview["eval_id"], session_status="active")
        assert by_eval is not None
        assert by_eval["status"] == "failed"
        assert by_eval["job_id"] == preview["job_id"]

        # Lookup by unknown eval_id returns None
        assert ports.get_trainer_preview(session_id, eval_id="teval-unknown-999") is None

        # Router GET /preview readback matches failed preview truth
        resp_failed = client.get(
            f"/api/v1/trainer/sessions/{session_id}/preview",
            headers={"Authorization": "Bearer pantheon-dev-operator:operator"},
        )
        assert resp_failed.status_code == 200
        router_failed = resp_failed.json()
        assert router_failed["status"] == "failed"
        assert router_failed["job_id"] == preview["job_id"]
        assert router_failed["eval_id"] == preview["eval_id"]
        assert router_failed["owner_job_ref"] == {"job_id": preview["job_id"], "eval_id": preview["eval_id"]}
        assert router_failed["error_code"] == "evaluation_admission_rejected"
        assert router_failed["failure_reason"] == "candidate controls do not contain an effective change"
        assert router_failed["allowedActions"]["canRefreshPreview"] is True
        assert router_failed["polling"]["enabled"] is False
        assert len(router_failed["warnings"]) == 1
        assert router_failed["warnings"][0]["message"] == "candidate controls do not contain an effective change"

        # Post-failure preview refresh: active session can request a new preview job
        resp_retry = client.post(
            f"/api/v1/trainer/sessions/{session_id}/preview",
            json={"refresh_mode": "manual"},
            headers={"Authorization": "Bearer pantheon-dev-operator:operator"},
        )
        assert resp_retry.status_code == 200
        retry_preview = resp_retry.json()
        assert retry_preview["status"] == "pending"
        assert retry_preview["job_id"] != preview["job_id"]
        assert retry_preview["allowedActions"]["canRefreshPreview"] is False

        # 6d. Original negative / failure distinctions:
        # Non-existent session returns 404
        resp_404 = client.post(
            "/api/v1/trainer/sessions/trn-nonexistent/preview",
            json={"refresh_mode": "manual"},
            headers={"Authorization": "Bearer pantheon-dev-operator:operator"},
        )
        assert resp_404.status_code == 404

        # Owner unavailable distinction: build_trainer_preview_unavailable retains canRefreshPreview=False
        unavail = ports.persona_training.trainer._training.build_trainer_preview_unavailable("trn-probe")
        assert unavail["status"] == "preview_unavailable"
        assert unavail["allowedActions"]["canRefreshPreview"] is False
        assert unavail["meta"]["surfaces"]["trainer_preview"] == "unavailable"

        # Terminal session rejects preview refresh with 409 OPERATION_NOT_ALLOWED
        sess = dict(training_service.store.get_session(session_id))
        sess["status"] = "completed"
        training_service.store.put_session(sess)
        resp_terminal = client.post(
            f"/api/v1/trainer/sessions/{session_id}/preview",
            json={"refresh_mode": "manual"},
            headers={"Authorization": "Bearer pantheon-dev-operator:operator"},
        )
        assert resp_terminal.status_code == 409
        assert resp_terminal.json()["error"]["code"] == "OPERATION_NOT_ALLOWED"

        # Restore status for subsequent steps
        sess["status"] = "active"
        training_service.store.put_session(sess)

        # 7. Rapid eval: reject fabricated queued/healthy state on default production factory.
        with pytest.raises(
            RuntimeError,
            match="RapidEvaluationPort has no backend bound to its owner",
        ):
            ports.create_rapid_eval(session_id, eval_scope="persona_patch")
    finally:
        authorization.reset(auth_token)
        selected_tenant.reset(tenant_token)


def test_live_foreign_tenant_rejected(live_training_session_service):
    """Verify tenant mismatch raises 403 TENANT_MISMATCH, not converted to 500 or fake success."""
    ports = create_read_surface_ports()

    token = _signed_caller_jwt("tenant-legit", "operator-live")
    auth_token = authorization.set(f"Bearer {token}")
    tenant_token = selected_tenant.set("tenant-attacker")

    try:
        with pytest.raises(StarletteHTTPException) as exc_info:
            ports.create_trainer_session(persona_id="p-1", objective="infiltrate")
        assert exc_info.value.status_code == 403
    finally:
        authorization.reset(auth_token)
        selected_tenant.reset(tenant_token)


def test_live_tampered_credential_rejected_by_owner(live_training_session_service):
    """Verify tampered signed JWT credential is rejected by native owner with HTTP 401."""
    ports = create_read_surface_ports()

    token = _signed_caller_jwt("tenant-live", "operator-live")
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    auth_token = authorization.set(f"Bearer {tampered}")
    tenant_token = selected_tenant.set("tenant-live")

    try:
        with pytest.raises(HTTPException) as exc_info:
            ports.create_trainer_session(persona_id="p-tampered", objective="infiltrate")
        assert exc_info.value.status_code == 401
    finally:
        authorization.reset(auth_token)
        selected_tenant.reset(tenant_token)


def test_live_different_signed_tenant_cannot_read_owner_session(live_training_session_service):
    """Verify different signed tenant cannot read another tenant's session."""
    ports = create_read_surface_ports()

    token_alpha = _signed_caller_jwt("tenant-alpha", "operator-live")
    auth_alpha = authorization.set(f"Bearer {token_alpha}")
    tenant_alpha = selected_tenant.set("tenant-alpha")
    try:
        session = ports.create_trainer_session(persona_id="persona-alpha", objective="alpha session")
        session_id = session["session_id"]
    finally:
        authorization.reset(auth_alpha)
        selected_tenant.reset(tenant_alpha)

    token_beta = _signed_caller_jwt("tenant-beta", "operator-live")
    auth_beta = authorization.set(f"Bearer {token_beta}")
    tenant_beta = selected_tenant.set("tenant-beta")
    try:
        result = ports.get_trainer_session(session_id)
        assert result is None
    finally:
        authorization.reset(auth_beta)
        selected_tenant.reset(tenant_beta)


def test_owner_outage_returns_truthful_503(monkeypatch):
    """Verify owner outage returns truthful 503 DEPENDENCY_UNAVAILABLE, never 500 null store or empty success."""
    monkeypatch.setenv("PANTHEON_TRAINING_SESSION_API_URL", "http://127.0.0.1:1")
    ports = create_read_surface_ports()

    with pytest.raises(HTTPException) as exc_info:
        ports.list_trainer_sessions()
    assert exc_info.value.status_code == 503
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail.get("error", {}).get("code") == "DEPENDENCY_UNAVAILABLE"


def test_live_malformed_preview_job_raises_503(live_training_session_service):
    """Verify malformed preview-job missing job_id or eval_id raises truthful 503 DEPENDENCY_UNAVAILABLE, never fake pending."""
    base_url, training_service = live_training_session_service
    ports = create_read_surface_ports()

    token = _signed_caller_jwt("tenant-live", "operator-live")
    auth_token = authorization.set(f"Bearer {token}")
    tenant_token = selected_tenant.set("tenant-live")

    try:
        session = ports.create_trainer_session(persona_id="p-malformed", objective="test malformed preview")
        session_id = session["session_id"]

        orig_request = ports.persona_training.trainer._training._request

        def mock_request(path: str, *args: Any, **kwargs: Any) -> Any:
            if "preview-jobs" in path:
                return {"status": "queued"}
            return orig_request(path, *args, **kwargs)

        ports.persona_training.trainer._training._request = mock_request
        try:
            with pytest.raises(HTTPException) as exc_info:
                ports.refresh_trainer_preview(session_id, actor_id="operator-live")
            assert exc_info.value.status_code == 503
            detail = exc_info.value.detail
            assert isinstance(detail, dict)
            assert detail.get("error", {}).get("code") == "DEPENDENCY_UNAVAILABLE"
        finally:
            ports.persona_training.trainer._training._request = orig_request
    finally:
        authorization.reset(auth_token)
        selected_tenant.reset(tenant_token)


def test_live_malformed_preview_job_list_raises_503(live_training_session_service):
    """Verify malformed preview-job list response raises truthful 503 DEPENDENCY_UNAVAILABLE, never silent success."""
    base_url, training_service = live_training_session_service
    ports = create_read_surface_ports()

    token = _signed_caller_jwt("tenant-live", "operator-live")
    auth_token = authorization.set(f"Bearer {token}")
    tenant_token = selected_tenant.set("tenant-live")

    try:
        session = ports.create_trainer_session(persona_id="p-malformed-list", objective="test malformed list")
        session_id = session["session_id"]

        orig_request = ports.persona_training.trainer._training._request

        def mock_request(path: str, *args: Any, **kwargs: Any) -> Any:
            if "preview-jobs" in path:
                return {"not_a_list": True}
            return orig_request(path, *args, **kwargs)

        ports.persona_training.trainer._training._request = mock_request
        try:
            with pytest.raises(HTTPException) as exc_info:
                ports.get_trainer_preview(session_id, session_status="active")
            assert exc_info.value.status_code == 503
            detail = exc_info.value.detail
            assert isinstance(detail, dict)
            assert detail.get("error", {}).get("code") == "DEPENDENCY_UNAVAILABLE"
        finally:
            ports.persona_training.trainer._training._request = orig_request
    finally:
        authorization.reset(auth_token)
        selected_tenant.reset(tenant_token)


def test_live_malformed_preview_job_running_without_job_id_raises_503(live_training_session_service):
    """Verify running job without job_id raises truthful 503 DEPENDENCY_UNAVAILABLE, never fake pending."""
    base_url, training_service = live_training_session_service
    ports = create_read_surface_ports()

    token = _signed_caller_jwt("tenant-live", "operator-live")
    auth_token = authorization.set(f"Bearer {token}")
    tenant_token = selected_tenant.set("tenant-live")

    try:
        session = ports.create_trainer_session(persona_id="p-malformed-no-jobid", objective="test no jobid")
        session_id = session["session_id"]

        orig_request = ports.persona_training.trainer._training._request

        def mock_request(path: str, *args: Any, **kwargs: Any) -> Any:
            if "preview-jobs" in path and not path.endswith("/preview-jobs"):
                return [{"session_id": session_id, "eval_id": "eval-source", "status": "running"}]
            return orig_request(path, *args, **kwargs)

        ports.persona_training.trainer._training._request = mock_request
        try:
            with pytest.raises(HTTPException) as exc_info:
                ports.get_trainer_preview(session_id, session_status="active")
            assert exc_info.value.status_code == 503
            assert exc_info.value.detail.get("error", {}).get("code") == "DEPENDENCY_UNAVAILABLE"
        finally:
            ports.persona_training.trainer._training._request = orig_request
    finally:
        authorization.reset(auth_token)
        selected_tenant.reset(tenant_token)


def test_live_completed_job_without_evaluation_truthfully_unavailable_or_not_found(live_training_session_service):
    """Verify completed job without genuine evaluation is truthfully unavailable/error, never fake pending."""
    base_url, training_service = live_training_session_service
    ports = create_read_surface_ports()

    token = _signed_caller_jwt("tenant-live", "operator-live")
    auth_token = authorization.set(f"Bearer {token}")
    tenant_token = selected_tenant.set("tenant-live")

    try:
        session = ports.create_trainer_session(persona_id="p-completed-no-eval", objective="test completed no eval")
        session_id = session["session_id"]

        orig_request = ports.persona_training.trainer._training._request

        def mock_request(path: str, *args: Any, **kwargs: Any) -> Any:
            if "preview-jobs" in path and not path.endswith("/preview-jobs"):
                return [{"session_id": session_id, "job_id": "job-source", "eval_id": "eval-source", "status": "completed"}]
            return orig_request(path, *args, **kwargs)

        ports.persona_training.trainer._training._request = mock_request
        try:
            res_eval = ports.get_trainer_preview(session_id, eval_id="eval-source", session_status="active")
            assert res_eval is None

            res_none = ports.get_trainer_preview(session_id, session_status="active")
            assert res_none is not None
            assert res_none["status"] == "preview_unavailable"
        finally:
            ports.persona_training.trainer._training._request = orig_request
    finally:
        authorization.reset(auth_token)
        selected_tenant.reset(tenant_token)


def test_service_caller_token_accepted(live_training_session_service):
    """Verify genuine service caller token (with service claim and training-service role) is accepted."""
    ports = create_read_surface_ports()
    token = _signed_caller_jwt("tenant-live", "control-plane-bff")
    auth_token = authorization.set(f"Bearer {token}")
    tenant_token = selected_tenant.set("tenant-live")

    try:
        created = ports.create_trainer_session(
            persona_id="persona-alpha",
            objective="Service-originated training session",
            actor_id="control-plane-bff",
        )
        assert created is not None
        assert created["opened_by"] == "control-plane-bff"
        assert created["actor_context"]["actor_service"] == "control-plane-bff"
    finally:
        authorization.reset(auth_token)
        selected_tenant.reset(tenant_token)


def test_live_expired_jwt_rejected(live_training_session_service):
    """Verify expired token is rejected with HTTP 401."""
    ports = create_read_surface_ports()
    secret = os.environ.get("TRAINING_SESSION_JWT_SECRET", "owned-original-training-inbound-test-secret-only")
    expired_token = encode_jwt_hs256(
        {
            "sub": "pantheon-dev-operator",
            "roles": ["operator"],
            "tenant_id": "tenant-live",
            "allowed_tenants": ["tenant-live"],
            "iss": "pantheon-dev",
            "aud": "bff-operators",
            "exp": time.time() - 3600,
        },
        secret=secret,
    )
    auth_token = authorization.set(f"Bearer {expired_token}")
    tenant_token = selected_tenant.set("tenant-live")

    try:
        with pytest.raises(HTTPException) as exc_info:
            ports.create_trainer_session(persona_id="p-1", objective="expired test")
        assert exc_info.value.status_code == 401
    finally:
        authorization.reset(auth_token)
        selected_tenant.reset(tenant_token)


def test_live_anonymous_caller_rejected(live_training_session_service):
    """Verify anonymous caller without Bearer token is rejected with HTTP 401 by native owner."""
    import urllib.error
    import urllib.request

    base_url, _ = live_training_session_service
    req = urllib.request.Request(
        f"{base_url}/api/training/sessions",
        data=json.dumps({"persona_id": "p-1", "objective": "anonymous test"}).encode(),
        headers={
            "X-Tenant-Id": "tenant-live",
            "X-Pantheon-Service": "control-plane-bff",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 401



def test_live_viewer_role_write_rejected(live_training_session_service):
    """Verify viewer dev-login user can read sessions but is rejected on mutations with 403."""
    ports = create_read_surface_ports()
    token, claims = _dev_login(
        client_id="pantheon-dev-viewer",
        client_secret="dev-viewer-secret-123",
        roles=["viewer"],
        tenant_id="tenant-live",
    )
    assert "viewer" in claims.get("roles", [])

    auth_token = authorization.set(f"Bearer {token}")
    tenant_token = selected_tenant.set("tenant-live")

    try:
        sessions = ports.list_trainer_sessions()
        assert isinstance(sessions, list)

        with pytest.raises(HTTPException) as exc_info:
            ports.create_trainer_session(persona_id="p-1", objective="viewer mutation")
        assert exc_info.value.status_code == 403
    finally:
        authorization.reset(auth_token)
        selected_tenant.reset(tenant_token)


def test_live_user_cannot_call_worker_only_run(live_training_session_service):
    """Verify user authority cannot invoke worker-only endpoints such as /preview-jobs/{id}/run."""
    import urllib.error
    import urllib.request

    base_url, _ = live_training_session_service
    token, _ = _dev_login(
        client_id="pantheon-dev-operator",
        client_secret="dev-operator-secret-123",
        roles=["operator"],
        tenant_id="tenant-live",
    )

    req = urllib.request.Request(
        f"{base_url}/api/training/preview-jobs/job-123/run",
        data=b"{}",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": "tenant-live",
            "X-Pantheon-Service": "control-plane-bff",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 403
    body = json.loads(exc_info.value.read().decode())
    assert body.get("error", {}).get("code") == "AUTH_FORBIDDEN"


def test_live_forged_gateway_rejected(live_training_session_service):
    """Verify user token sent through unauthorized gateway service is rejected with 403."""
    import urllib.error
    import urllib.request

    base_url, _ = live_training_session_service
    token, _ = _dev_login(
        client_id="pantheon-dev-operator",
        client_secret="dev-operator-secret-123",
        roles=["operator"],
        tenant_id="tenant-live",
    )

    req = urllib.request.Request(
        f"{base_url}/api/training/sessions",
        data=json.dumps({"persona_id": "persona-alpha", "objective": "test"}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": "tenant-live",
            "X-Pantheon-Service": "unauthorized-gateway",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 403
    body = json.loads(exc_info.value.read().decode())
    assert body.get("error", {}).get("code") == "ACTOR_SERVICE_FORBIDDEN"


def test_live_actor_spoof_rejected(live_training_session_service):
    """Verify body actor_id that does not match authenticated user is rejected with 403."""
    ports = create_read_surface_ports()
    token, _ = _dev_login(
        client_id="pantheon-dev-operator",
        client_secret="dev-operator-secret-123",
        roles=["operator"],
        tenant_id="tenant-live",
    )
    auth_token = authorization.set(f"Bearer {token}")
    tenant_token = selected_tenant.set("tenant-live")

    try:
        with pytest.raises(HTTPException) as exc_info:
            ports.create_trainer_session(
                persona_id="persona-alpha",
                objective="test",
                actor_id="spoofed-impostor-actor",
            )
        assert exc_info.value.status_code == 403
    finally:
        authorization.reset(auth_token)
        selected_tenant.reset(tenant_token)


def test_live_jwt_issuer_audience_mismatch_rejected(live_training_session_service):
    """Verify tokens with wrong issuer or audience are rejected with 401."""
    ports = create_read_surface_ports()
    secret = os.environ.get("TRAINING_SESSION_JWT_SECRET", "owned-original-training-inbound-test-secret-only")

    # Wrong issuer
    bad_iss = encode_jwt_hs256(
        {
            "sub": "pantheon-dev-operator",
            "roles": ["operator"],
            "tenant_id": "tenant-live",
            "allowed_tenants": ["tenant-live"],
            "iss": "wrong-issuer",
            "aud": "bff-operators",
            "exp": time.time() + 3600,
        },
        secret=secret,
    )
    auth_token = authorization.set(f"Bearer {bad_iss}")
    tenant_token = selected_tenant.set("tenant-live")
    try:
        with pytest.raises(HTTPException) as exc_info:
            ports.create_trainer_session(persona_id="p-1", objective="test")
        assert exc_info.value.status_code == 401
    finally:
        authorization.reset(auth_token)
        selected_tenant.reset(tenant_token)

    # Wrong audience
    bad_aud = encode_jwt_hs256(
        {
            "sub": "pantheon-dev-operator",
            "roles": ["operator"],
            "tenant_id": "tenant-live",
            "allowed_tenants": ["tenant-live"],
            "iss": "pantheon-dev",
            "aud": "wrong-audience",
            "exp": time.time() + 3600,
        },
        secret=secret,
    )
    auth_token = authorization.set(f"Bearer {bad_aud}")
    tenant_token = selected_tenant.set("tenant-live")
    try:
        with pytest.raises(HTTPException) as exc_info:
            ports.create_trainer_session(persona_id="p-1", objective="test")
        assert exc_info.value.status_code == 401
    finally:
        authorization.reset(auth_token)
        selected_tenant.reset(tenant_token)


def test_live_replay_commit_requires_verified_mfa_negatives(live_training_session_service, monkeypatch):
    """Verify replay commit/discard reject missing or unverified MFA when MFA is required."""
    import urllib.error
    import urllib.request

    base_url, _ = live_training_session_service
    token, _ = _dev_login(
        client_id="pantheon-dev-operator",
        client_secret="dev-operator-secret-123",
        roles=["operator"],
        tenant_id="tenant-live",
    )
    monkeypatch.setenv("TRAINING_SESSION_MFA_REQUIRED", "true")

    req_no_mfa = urllib.request.Request(
        f"{base_url}/api/training/replays/not-found/commit",
        data=b"{}",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": "tenant-live",
            "X-Pantheon-Service": "control-plane-bff",
            "Content-Type": "application/json",
            "Idempotency-Key": "test-key-1",
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_no_mfa)
    assert exc_info.value.code == 401
    body = json.loads(exc_info.value.read().decode())
    assert body.get("error", {}).get("code") == "MFA_REQUIRED"

    req_unverified_mfa = urllib.request.Request(
        f"{base_url}/api/training/replays/not-found/commit",
        data=b"{}",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": "tenant-live",
            "X-Pantheon-Service": "control-plane-bff",
            "X-MFA-Token": "123456",
            "Content-Type": "application/json",
            "Idempotency-Key": "test-key-2",
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_unverified_mfa)
    assert exc_info.value.code == 401
    body = json.loads(exc_info.value.read().decode())
    assert body.get("error", {}).get("code") == "MFA_NOT_VERIFIED"





def test_live_mounted_replay_get_and_idempotency_key_forwarding(live_training_session_service):
    """Default factory + mounted routes + real localhost owner: replay GET DTO and caller Idempotency-Key forwarding."""
    base_url, training_service = live_training_session_service
    ports = create_read_surface_ports()
    token, _ = _dev_login(roles=["operator"], tenant_id="tenant-live")
    session_id = "trn-replay-wiring-test-001"
    candidate_snapshot_at = "2026-10-13T00:00:00Z"
    training_service.store.put_session(
        {
            "session_id": session_id,
            "tenant_id": "tenant-live",
            "persona_id": "persona-alpha",
            "objective": "isolated test-only replay fixture",
            "status": "completed",
            "started_at": "2026-10-13T00:00:00Z",
            "ended_at": "2026-10-13T00:05:00Z",
            "replay_resolution": {"state": "pending_decision"},
            "artifacts": {"candidate_artifact_ref": "art-test-1"},
            "events": [
                {
                    "event_id": "tevt-replay-1",
                    "session_id": session_id,
                    "sequence_number": 1,
                    "event_type": "preview_trigger",
                    "actor": "operator",
                    "emitted_at": "2026-10-13T00:01:00Z",
                    "eval_ref": {"eval_id": "teval-replay-1", "candidate_snapshot_at": candidate_snapshot_at},
                }
            ],
        }
    )

    auth_token = authorization.set(f"Bearer {token}")
    tenant_token = selected_tenant.set("tenant-live")
    try:
        app = FastAPI()

        @app.exception_handler(HTTPException)
        @app.exception_handler(StarletteHTTPException)
        async def _http_exception_handler(request: Any, exc: Any) -> JSONResponse:
            if isinstance(exc.detail, dict) and "error" in exc.detail:
                return JSONResponse(status_code=exc.status_code, content=exc.detail)
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": {"code": "HTTP_ERROR", "message": str(exc.detail)}},
            )

        app.include_router(
            create_training_router(
                read_surface=ports,
                extract_identity=extract_identity_stub,
                require_read_role=require_read_role,
                bff_error=bff_error,
                utc_now=default_utc_now,
                page_slice=_page_slice,
                dataset_surface_status=_dataset_surface_status,
            )
        )
        client = TestClient(app)
        headers = {"Authorization": "Bearer pantheon-dev-operator:operator"}

        # Layer 1: ordinary GET yields the owner-backed DTO (no snapshot_at TypeError).
        got = client.get(f"/api/v1/trainer/replay/{session_id}", headers=headers)
        assert got.status_code == 200, got.text
        detail = got.json()
        assert detail["session_id"] == session_id
        assert detail["allowedActions"]["canDiscard"] is True
        assert detail["links"]["self"] == f"/trainer/replay/{session_id}"
        assert detail["events"][0]["event_type"] == "preview_trigger"
        assert detail["events"][0]["eval_ref"]["candidate_snapshot_at"] == candidate_snapshot_at

        body = {"expected_candidate_snapshot_at": candidate_snapshot_at}

        # Layer 3: owner rejects a missing key truthfully and leaves state unchanged.
        missing = client.post(f"/api/v1/trainer/sessions/{session_id}/discard", json=body, headers=headers)
        assert missing.status_code == 400, missing.text
        assert training_service.store.get_session(session_id)["replay_resolution"]["state"] == "pending_decision"

        # Wrong candidate snapshot with a key is a truthful rejection at the BFF precondition.
        wrong = client.post(
            f"/api/v1/trainer/sessions/{session_id}/discard",
            json={"expected_candidate_snapshot_at": "2026-01-01T00:00:00Z"},
            headers={**headers, "Idempotency-Key": "replay-wiring-key-1"},
        )
        assert wrong.status_code == 409
        assert training_service.store.get_session(session_id)["replay_resolution"]["state"] == "pending_decision"

        # Same actual caller key reaches the owner and survives retry.
        key_headers = {**headers, "Idempotency-Key": "replay-wiring-key-1"}
        first = client.post(f"/api/v1/trainer/sessions/{session_id}/discard", json=body, headers=key_headers)
        assert first.status_code == 200, first.text
        assert first.json()["replay_resolution"]["state"] == "discarded"
        # The mounted route precondition rejects a second decision on a decided replay; owner-level
        # same-key retry goes through the same facade/store path and is replayed, not re-applied.
        assert client.post(
            f"/api/v1/trainer/sessions/{session_id}/discard", json=body, headers=key_headers
        ).status_code == 409
        retry = ports.discard_trainer_replay(
            session_id, expected_candidate_snapshot_at=candidate_snapshot_at, idempotency_key="replay-wiring-key-1"
        )
        assert retry["replay_resolution"]["state"] == "discarded"
        with pytest.raises(HTTPException) as conflict:
            ports.discard_trainer_replay(
                session_id, expected_candidate_snapshot_at=candidate_snapshot_at, note="different", idempotency_key="replay-wiring-key-1"
            )
        assert conflict.value.status_code == 409
    finally:
        authorization.reset(auth_token)
        selected_tenant.reset(tenant_token)


def test_live_mounted_trainer_controls_snapshot_and_patch_pre_read(live_training_session_service):
    """Default factory + mounted routes + real localhost owner: controls GET request-time snapshot and POST patch pre-read."""
    base_url, training_service = live_training_session_service
    ports = create_read_surface_ports()
    token, _ = _dev_login(roles=["operator"], tenant_id="tenant-live")
    session_id = "trn-controls-snapshot-test-001"
    training_service.store.put_session(
        {
            "session_id": session_id,
            "tenant_id": "tenant-live",
            "persona_id": "persona-alpha",
            "objective": "isolated test-only controls snapshot fixture",
            "status": "active",
            "started_at": "2026-10-15T10:00:00Z",
            "ended_at": None,
        }
    )
    training_service.store.put_controls(
        session_id,
        {
            "session_id": session_id,
            "tenant_id": "tenant-live",
            "controls": [
                {
                    "parameter_key": "risk.max_drawdown",
                    "current_value": 0.08,
                    "allowed_range": {"min": 0.02, "max": 0.2},
                }
            ],
        },
    )

    auth_token = authorization.set(f"Bearer {token}")
    tenant_token = selected_tenant.set("tenant-live")
    try:
        app = FastAPI()

        @app.exception_handler(HTTPException)
        @app.exception_handler(StarletteHTTPException)
        async def _http_exception_handler(request: Any, exc: Any) -> JSONResponse:
            if isinstance(exc.detail, dict) and "error" in exc.detail:
                return JSONResponse(status_code=exc.status_code, content=exc.detail)
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": {"code": "HTTP_ERROR", "message": str(exc.detail)}},
            )

        frozen_snapshot = "2026-10-15T10:19:36.765000Z"
        app.include_router(
            create_training_router(
                read_surface=ports,
                extract_identity=extract_identity_stub,
                require_read_role=require_read_role,
                bff_error=bff_error,
                utc_now=lambda: frozen_snapshot,
                page_slice=_page_slice,
                dataset_surface_status=_dataset_surface_status,
            )
        )
        client = TestClient(app)
        headers = {"Authorization": "Bearer pantheon-dev-operator:operator"}

        # 1. Mounted GET controls forwards snapshot_at from router to ReadSurfacePorts facade and returns owner data.
        got = client.get(f"/api/v1/trainer/sessions/{session_id}/controls", headers=headers)
        assert got.status_code == 200, got.text
        ctrls = got.json()
        assert ctrls["session_id"] == session_id
        assert ctrls["status"] == "active"
        assert ctrls["allowedActions"]["canPatchControls"] is True
        assert len(ctrls["controls"]) == 1
        assert ctrls["controls"][0]["parameter_key"] == "risk.max_drawdown"
        assert ctrls["controls"][0]["current_value"] == 0.08
        assert ctrls["meta"]["snapshot_at"] == frozen_snapshot
        assert ctrls["meta"]["staleness"]["status"] == "fresh"

        # 2. Direct facade call with explicit keyword snapshot_at works and preserves timestamp.
        explicit_snapshot = "2026-10-15T10:20:00Z"
        direct_with_kw = ports.get_trainer_controls(session_id, snapshot_at=explicit_snapshot)
        assert direct_with_kw is not None
        assert direct_with_kw["session_id"] == session_id
        assert direct_with_kw["meta"]["snapshot_at"] == explicit_snapshot

        # 3. Direct facade call without kwargs continues to work (preserving original direct calls).
        direct_no_kw = ports.get_trainer_controls(session_id)
        assert direct_no_kw is not None
        assert direct_no_kw["session_id"] == session_id
        assert direct_no_kw["meta"]["snapshot_at"] is not None

        # 4. Mounted POST patch pre-reads controls using the same facade snapshot_at contract and succeeds.
        patch_payload = {
            "patches": [
                {"parameter_key": "risk.max_drawdown", "proposed_value": 0.1}
            ]
        }
        patched = client.post(
            f"/api/v1/trainer/sessions/{session_id}/patch",
            json=patch_payload,
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        patch_data = patched.json()
        assert patch_data["status"] == "accepted"
        assert patch_data["current_controls"][0]["current_value"] == 0.1

        # 5. Mounted GET on missing session truthfully returns 404.
        missing = client.get("/api/v1/trainer/sessions/trn-nonexistent/controls", headers=headers)
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    finally:
        authorization.reset(auth_token)
        selected_tenant.reset(tenant_token)
