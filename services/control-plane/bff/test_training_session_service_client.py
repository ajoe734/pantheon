from __future__ import annotations

import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
sys.path.insert(0, os.path.dirname(__file__))

from read_store import ReadSurfaceStore


def test_trainer_session_create_uses_explicit_training_session_service_url() -> None:
    calls = []

    def fake_post(base_url, path, *, body, headers=None):
        calls.append((base_url, path, body))
        assert base_url == "http://training-session-svc:8099"
        assert path == "/api/training/sessions"
        return True, {
            "session_id": "trn-service-001",
            "persona_id": body["persona_id"],
            "session_type": "trainer",
            "objective": body["objective"],
            "status": "active",
            "started_at": "2026-04-28T18:00:00Z",
            "events": [],
            "context_refs": body["context_refs"],
            "opened_by": body["actor_id"],
        }

    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(os.environ, {"PANTHEON_TRAINING_SESSION_API_URL": "http://training-session-svc:8099"}):
            with mock.patch("read_store._http_json_post", side_effect=fake_post):
                store = ReadSurfaceStore(os.path.join(td, "read_surfaces.json"), allow_local_snapshot_fallback=True)
                session = store.create_trainer_session(
                    persona_id="persona-alpha",
                    objective="Service-backed trainer session",
                    context_refs=[{"type": "evidence", "id": "ev-1"}],
                    actor_id="operator-1",
                    created_at="2026-04-28T18:00:00Z",
                )

    assert len(calls) == 1
    assert session["session_id"] == "trn-service-001"
    assert session["allowedActions"]["canSendMessage"] is True


def test_trainer_mutations_use_explicit_training_session_service_url() -> None:
    calls = []

    def fake_post(base_url, path, *, body, headers=None):
        calls.append((base_url, path, body))
        assert base_url == "http://training-session-svc:8099"
        if path == "/api/training/sessions/trn-service-001/events":
            return True, {
                "accepted_at": body["emitted_at"],
                "event": {
                    "event_id": "tevt-service-001",
                    "session_id": "trn-service-001",
                    "actor": "operator",
                    "event_type": "message",
                    "message_body": body["message_body"],
                    "emitted_at": body["emitted_at"],
                    "sequence_number": 1,
                },
                "session": _service_session(events=[]),
            }
        if path == "/api/training/sessions/trn-service-001/controls":
            return True, {
                "session_id": "trn-service-001",
                "status": "accepted",
                "controls": [{"parameter_key": "risk.max_drawdown", "current_value": 0.1}],
                "patch_delta": [
                    {
                        "parameter_key": "risk.max_drawdown",
                        "previous_value": 0.08,
                        "new_value": 0.1,
                    }
                ],
            }
        if path == "/api/training/sessions/trn-service-001/preview":
            return True, {
                "session_id": "trn-service-001",
                "eval_id": "teval-service-001",
                "status": "completed",
                "baseline_snapshot_at": "2026-04-28T18:00:00Z",
                "candidate_snapshot_at": body["refreshed_at"],
                "metric_delta": [],
                "control_diff": [],
                "allowedActions": {"canRefreshPreview": True},
            }
        if path == "/api/training/replays/trn-service-001/commit":
            return True, _service_replay("committed", body["decided_at"], body["actor_id"])
        if path == "/api/training/replays/trn-service-001/discard":
            return True, _service_replay("discarded", body["decided_at"], body["actor_id"])
        raise AssertionError(f"Unexpected path: {path}")

    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(os.environ, {"PANTHEON_TRAINING_SESSION_API_URL": "http://training-session-svc:8099"}):
            with mock.patch("read_store._http_json_post", side_effect=fake_post):
                store = ReadSurfaceStore(os.path.join(td, "read_surfaces.json"), allow_local_snapshot_fallback=True)
                message = store.append_trainer_message(
                    "trn-service-001",
                    message_body="Adjust max drawdown.",
                    actor_id="operator-1",
                    accepted_at="2026-04-28T18:01:00Z",
                )
                patch = store.patch_trainer_controls(
                    "trn-service-001",
                    patches=[{"parameter_key": "risk.max_drawdown", "proposed_value": 0.1}],
                    patched_at="2026-04-28T18:02:00Z",
                )
                preview = store.refresh_trainer_preview(
                    "trn-service-001",
                    session_status="active",
                    refreshed_at="2026-04-28T18:03:00Z",
                )
                committed = store.commit_trainer_replay(
                    "trn-service-001",
                    expected_candidate_snapshot_at="2026-04-28T18:03:00Z",
                    note="Accept candidate.",
                    actor_id="operator-1",
                    committed_at="2026-04-28T18:04:00Z",
                )
                discarded = store.discard_trainer_replay(
                    "trn-service-001",
                    expected_candidate_snapshot_at="2026-04-28T18:03:00Z",
                    note="Discard candidate.",
                    actor_id="operator-1",
                    discarded_at="2026-04-28T18:05:00Z",
                )

    assert message["event"]["message_body"] == "Adjust max drawdown."
    assert patch["diff"]["updated_controls"][0]["field"] == "risk.max_drawdown"
    assert preview["eval_id"] == "teval-service-001"
    assert committed["replay_resolution"]["state"] == "committed"
    assert discarded["replay_resolution"]["state"] == "discarded"
    assert [call[1] for call in calls] == [
        "/api/training/sessions/trn-service-001/events",
        "/api/training/sessions/trn-service-001/controls",
        "/api/training/sessions/trn-service-001/preview",
        "/api/training/replays/trn-service-001/commit",
        "/api/training/replays/trn-service-001/discard",
    ]


def test_trainer_session_create_reports_unavailable_when_service_url_is_down() -> None:
    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(os.environ, {"PANTHEON_TRAINING_SESSION_API_URL": "http://training-session-svc:8099"}):
            with mock.patch("read_store._http_json_post", return_value=(False, None)):
                store = ReadSurfaceStore(os.path.join(td, "read_surfaces.json"), allow_local_snapshot_fallback=True)
                session = store.create_trainer_session(
                    persona_id="persona-alpha",
                    objective="Service-backed trainer session",
                    context_refs=[],
                    actor_id="operator-1",
                    created_at="2026-04-28T18:00:00Z",
                )

    assert session is None


def _service_session(events):
    return {
        "session_id": "trn-service-001",
        "persona_id": "persona-alpha",
        "session_type": "trainer",
        "objective": "Service-backed trainer session",
        "status": "active",
        "started_at": "2026-04-28T18:00:00Z",
        "events": events,
        "context_refs": [],
        "opened_by": "operator-1",
    }


def _service_replay(state, decided_at, actor_id):
    return {
        **_service_session(
            [
                {
                    "event_id": "tevt-service-001",
                    "session_id": "trn-service-001",
                    "actor": "operator",
                    "event_type": "message",
                    "message_body": "Adjust max drawdown.",
                    "emitted_at": "2026-04-28T18:01:00Z",
                    "sequence_number": 1,
                }
            ]
        ),
        "status": "completed",
        "ended_at": "2026-04-28T18:03:00Z",
        "replay_resolution": {
            "state": state,
            "decision_at": decided_at,
            "decision_by": actor_id,
            "note": None,
        },
        "artifacts": {
            "before_artifact_ref": "before-artifact",
            "candidate_artifact_ref": "candidate-artifact",
            "after_artifact_ref": "after-artifact" if state == "committed" else None,
        },
    }
