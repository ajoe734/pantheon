from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient


SERVICE_DIR = Path(__file__).resolve().parents[1]


def _load_service_module():
    with mock.patch.dict("os.environ", {"TRAINING_SESSION_DATA_DIR": tempfile.mkdtemp()}):
        sys.path.insert(0, str(SERVICE_DIR))
        spec = importlib.util.spec_from_file_location("training_session_test_main", SERVICE_DIR / "main.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["training_session_test_main"] = module
        spec.loader.exec_module(module)
        return module


def test_training_session_lifecycle_event_preview_and_replay_contract() -> None:
    module = _load_service_module()
    client = TestClient(module.app)

    created = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-alpha",
            "objective": "Tune momentum controls",
            "context_refs": [{"type": "evidence", "id": "ev-1"}],
            "actor_id": "operator-1",
            "created_at": "2026-04-28T18:00:00Z",
        },
    )
    assert created.status_code == 201
    created_payload = created.json()
    module.TeachingSession.from_dict(created_payload)
    assert created_payload["mode"] == "coaching"
    assert created_payload["trace_id"]
    session_id = created_payload["session_id"]

    first = client.post(
        f"/api/training/sessions/{session_id}/events",
        json={"message_body": "Decrease aggressiveness.", "emitted_at": "2026-04-28T18:01:00Z"},
    )
    second = client.post(
        f"/api/training/sessions/{session_id}/events",
        json={"message_body": "Keep drawdown capped.", "emitted_at": "2026-04-28T18:02:00Z"},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    module.TeachingEvent.from_dict(first.json()["event"])
    module.TeachingEvent.from_dict(second.json()["event"])
    assert first.json()["event"]["actor_type"] == "user"
    assert first.json()["event"]["timestamp"] == "2026-04-28T18:01:00Z"
    assert first.json()["event"]["payload"]["message_body"] == "Decrease aggressiveness."
    assert first.json()["event"]["sequence_number"] == 1
    assert second.json()["event"]["sequence_number"] == 2

    events = client.get(f"/api/training/sessions/{session_id}/events")
    assert events.status_code == 200
    assert [event["sequence_number"] for event in events.json()] == [1, 2]

    reloaded_store = module.TrainingSessionStore(module.store.data_dir)
    assert [event["message_body"] for event in reloaded_store.list_event_log(session_id)] == [
        "Decrease aggressiveness.",
        "Keep drawdown capped.",
    ]

    preview = client.post(f"/api/training/sessions/{session_id}/preview", json={"mode": "refresh"})
    assert preview.status_code == 201
    assert preview.json()["status"] == "completed"
    assert preview.json()["candidate_snapshot_at"]

    completed = client.post(f"/api/training/sessions/{session_id}/complete")
    assert completed.status_code == 201
    module.TeachingSession.from_dict(completed.json())
    module.TeachingEvent.from_dict(completed.json()["events"][-1])
    assert completed.json()["replay_resolution"]["state"] == "pending_decision"

    replay = client.get(f"/api/training/replays/{session_id}")
    assert replay.status_code == 200
    assert replay.json()["events"][-1]["event_type"] == "preview_trigger"
    assert reloaded_store.list_event_log(session_id)[-1]["event_type"] == "preview_trigger"
    candidate_snapshot_at = replay.json()["events"][-1]["eval_ref"]["candidate_snapshot_at"]

    stale_commit = client.post(
        f"/api/training/replays/{session_id}/commit",
        json={
            "expected_candidate_snapshot_at": "2026-04-28T17:00:00Z",
            "actor_id": "operator-1",
            "decided_at": "2026-04-28T18:04:00Z",
        },
    )
    assert stale_commit.status_code == 409

    committed = client.post(
        f"/api/training/replays/{session_id}/commit",
        json={
            "expected_candidate_snapshot_at": candidate_snapshot_at,
            "actor_id": "operator-1",
            "note": "Accept candidate.",
            "decided_at": "2026-04-28T18:05:00Z",
        },
    )
    assert committed.status_code == 200
    module.TeachingEvent.from_dict(committed.json()["events"][-1])
    assert committed.json()["replay_resolution"]["state"] == "committed"
    assert committed.json()["events"][-1]["event_type"] == "commit"
    assert reloaded_store.list_event_log(session_id)[-1]["event_type"] == "commit"

    duplicate_complete = client.post(f"/api/training/sessions/{session_id}/complete")
    assert duplicate_complete.status_code == 409


def test_control_patch_rejects_unknown_key_and_accepts_known_key() -> None:
    module = _load_service_module()
    client = TestClient(module.app)
    session_id = client.post(
        "/api/training/sessions",
        json={"persona_id": "persona-alpha", "objective": "Patch controls"},
    ).json()["session_id"]
    module.store.put_controls(
        session_id,
        {
            "session_id": session_id,
            "controls": [
                {
                    "parameter_key": "risk.max_drawdown",
                    "current_value": 0.08,
                    "allowed_range": {"min": 0.02, "max": 0.2},
                }
            ],
        },
    )

    rejected = client.post(
        f"/api/training/sessions/{session_id}/controls",
        json={"patches": [{"parameter_key": "missing", "proposed_value": 1}]},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    accepted = client.post(
        f"/api/training/sessions/{session_id}/controls",
        json={"patches": [{"parameter_key": "risk.max_drawdown", "proposed_value": 0.1}]},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["controls"][0]["current_value"] == 0.1


def test_complete_upgrades_legacy_session_to_schema_contract() -> None:
    module = _load_service_module()
    client = TestClient(module.app)
    module.store.put_session(
        {
            "id": "trn-legacy-001",
            "session_id": "trn-legacy-001",
            "persona_id": "persona-alpha",
            "session_type": "trainer",
            "objective": "Legacy trainer session",
            "status": "active",
            "started_at": "2026-04-28T18:00:00Z",
            "ended_at": None,
            "opened_by": "operator-1",
            "context_refs": [],
            "events": [],
            "outcomes": [],
        }
    )
    module.store.put_preview_bundle(
        "trn-legacy-001",
        {
            "session_id": "trn-legacy-001",
            "preview": {
                "eval_id": "teval-legacy-001",
                "baseline_snapshot_at": "2026-04-28T18:00:00Z",
                "candidate_snapshot_at": "2026-04-28T18:03:00Z",
            },
        },
    )

    completed = client.post("/api/training/sessions/trn-legacy-001/complete")

    assert completed.status_code == 201
    payload = completed.json()
    module.TeachingSession.from_dict(payload)
    module.TeachingEvent.from_dict(payload["events"][-1])
    assert payload["mode"] == "coaching"
    assert payload["trace_id"] == "trace-trn-legacy-001"
