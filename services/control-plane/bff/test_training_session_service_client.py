from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(__file__))

from ports import ReadSurfacePorts, TrainingSessionTrainerPort, create_in_memory_read_surface_ports


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
