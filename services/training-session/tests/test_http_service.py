from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import pytest
import uvicorn
from fastapi.testclient import TestClient

from strict_test_support import (
    FIXED_TRUSTED_NOW,
    make_fake_persona_target_commit,
    make_fake_real_vectorbt_workflow,
    make_fake_target_precondition_reader,
    materialize_strict_authority,
    seed_changed_supported_controls,
)


SERVICE_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def consultation_owner(monkeypatch):
    """Real Consultation owner over HTTP.

    A failed evaluation hands off to Consultation and fails closed when the
    owner is not reachable, so tests that fail validation need the real service.
    """
    from services.consultation import main as consultation_main
    from services.consultation.store import ConsultationStore

    with tempfile.TemporaryDirectory() as data_dir:
        monkeypatch.setattr(consultation_main, "store", ConsultationStore(data_dir))
        monkeypatch.setenv("CONSULTATION_AUTH_REQUIRED", "true")
        monkeypatch.setenv("CONSULTATION_SERVICE_TOKEN", "test-service-token-secret")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = int(listener.getsockname()[1])
        server = uvicorn.Server(
            uvicorn.Config(consultation_main.app, host="127.0.0.1", port=port, log_level="error")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() >= deadline:
                raise RuntimeError("consultation test server did not start")
            time.sleep(0.01)
        monkeypatch.setenv("PANTHEON_CONSULTATION_API_URL", f"http://127.0.0.1:{port}")
        try:
            yield consultation_main.store
        finally:
            server.should_exit = True
            thread.join(timeout=5)


def _load_service_module():
    fixture = materialize_strict_authority(tempfile.mkdtemp())
    os.environ.update(fixture.environment())
    with mock.patch.dict("os.environ", fixture.environment(), clear=False):
        sys.path.insert(0, str(SERVICE_DIR))
        spec = importlib.util.spec_from_file_location("training_session_test_main", SERVICE_DIR / "main.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["training_session_test_main"] = module
        spec.loader.exec_module(module)
    module.store = module.TrainingSessionStore(fixture.data_dir)
    module._trusted_now = lambda: FIXED_TRUSTED_NOW
    module.run_vectorbt_workflow = make_fake_real_vectorbt_workflow()
    module._read_target_precondition = make_fake_target_precondition_reader()
    module._commit_authoritative_persona_target = make_fake_persona_target_commit()
    module._strict_authority_fixture = fixture
    return module


def _run_worker_preview(module, client: TestClient, session_id: str, *, key: str) -> tuple[dict, dict]:
    seed_changed_supported_controls(module, session_id)
    queued = client.post(
        f"/api/training/sessions/{session_id}/preview-jobs",
        json={
            "mode": "refresh",
            "requested_by": "operator-1",
            "requested_at": "2026-07-15T12:00:00Z",
        },
        headers={"Idempotency-Key": key},
    )
    assert queued.status_code == 201, queued.text
    job_id = queued.json()["job_id"]
    run = client.post(f"/api/training/preview-jobs/{job_id}/run", json={})
    assert run.status_code == 200, run.text
    job = run.json()
    return job["preview"], job


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

    preview, _job = _run_worker_preview(module, client, session_id, key="lifecycle-preview-001")
    assert preview["status"] == "completed"
    assert preview["candidate_snapshot_at"]
    assert preview["evaluation_proof"]["status"] == "passed"
    assert preview["evaluation_proof"]["governance_gate_state"] == "passed"

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
        headers={"Idempotency-Key": "trn004-stale-commit"},
    )
    assert stale_commit.status_code == 409

    commit_body = {
        "expected_candidate_snapshot_at": candidate_snapshot_at,
        "actor_id": "operator-1",
        "note": "Accept candidate.",
        "decided_at": "2026-04-28T18:05:00Z",
    }
    committed = client.post(
        f"/api/training/replays/{session_id}/commit",
        json=commit_body,
        headers={"Idempotency-Key": "trn004-commit-001"},
    )
    assert committed.status_code == 200
    module.TeachingEvent.from_dict(committed.json()["events"][-1])
    assert committed.json()["replay_resolution"]["state"] == "committed"
    assert committed.json()["replay_resolution"]["idempotency"]["replayed"] is False
    assert committed.json()["events"][-1]["event_type"] == "commit"
    artifact_refs = committed.json()["events"][-1]["artifact_refs"]
    assert artifact_refs["decision_record_ref"].startswith("runtime-evidence:")
    assert artifact_refs["evaluation_proof_ref"] == preview["evaluation_proof"]["proof_ref"]
    assert artifact_refs["evaluation_governance_gate_state"] == "passed"
    assert artifact_refs["persona_policy_ref"] == artifact_refs["persona_target_controller_record_ref"]
    assert artifact_refs["policy_lineage_store_ref"] == "lineage-read:training-session-policy-lineage"
    assert artifact_refs["policy_lineage_edge_ids"]
    assert reloaded_store.list_event_log(session_id)[-1]["event_type"] == "commit"

    replayed_commit = client.post(
        f"/api/training/replays/{session_id}/commit",
        json=commit_body,
        headers={"Idempotency-Key": "trn004-commit-001"},
    )
    assert replayed_commit.status_code == 200
    assert replayed_commit.json()["replay_resolution"]["idempotency"]["replayed"] is True
    assert [event["event_type"] for event in replayed_commit.json()["events"]].count("commit") == 1

    duplicate_complete = client.post(f"/api/training/sessions/{session_id}/complete")
    assert duplicate_complete.status_code == 409


def test_async_preview_job_completes_eval_proof_for_commit() -> None:
    module = _load_service_module()
    client = TestClient(module.app)
    created = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-alpha",
            "objective": "Evaluate async trainer patch",
            "actor_id": "operator-async",
            "created_at": "2026-04-28T22:00:00Z",
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    seed_changed_supported_controls(module, session_id)

    queued = client.post(
        f"/api/training/sessions/{session_id}/preview-jobs",
        json={
            "mode": "refresh",
            "requested_by": "operator-async",
            "requested_at": "2026-04-28T22:01:00Z",
        },
        headers={"Idempotency-Key": "async-preview-001"},
    )
    assert queued.status_code == 201, queued.text
    assert queued.json()["status"] == "queued"
    job_id = queued.json()["job_id"]

    replayed_queue = client.post(
        f"/api/training/sessions/{session_id}/preview-jobs",
        json={
            "mode": "refresh",
            "requested_by": "operator-async",
            "requested_at": "2026-04-28T22:01:00Z",
        },
        headers={"Idempotency-Key": "async-preview-001"},
    )
    assert replayed_queue.status_code == 201
    assert replayed_queue.json()["job_id"] == job_id
    assert replayed_queue.json()["replayed"] is True

    listed = client.get("/api/training/preview-jobs", params={"status": "queued"})
    assert listed.status_code == 200
    assert [job["job_id"] for job in listed.json()] == [job_id]

    run = client.post(
        f"/api/training/preview-jobs/{job_id}/run",
        json={},
    )
    assert run.status_code == 200, run.text
    job = run.json()
    assert job["status"] == "completed"
    assert job["evaluation_proof_ref"].startswith("trainer-eval-proof:")
    assert job["governance_gate_state"] == "passed"
    assert job["preview"]["evaluation_proof"]["status"] == "passed"

    rerun = client.post(
        f"/api/training/preview-jobs/{job_id}/run",
        json={},
    )
    assert rerun.status_code == 200
    assert rerun.json()["replayed"] is True

    completed = client.post(f"/api/training/sessions/{session_id}/complete")
    assert completed.status_code == 201
    candidate_snapshot_at = completed.json()["events"][-1]["eval_ref"]["candidate_snapshot_at"]
    committed = client.post(
        f"/api/training/replays/{session_id}/commit",
        json={
            "expected_candidate_snapshot_at": candidate_snapshot_at,
            "actor_id": "operator-async",
            "decided_at": "2026-04-28T22:05:00Z",
        },
        headers={"Idempotency-Key": "async-commit-001"},
    )
    assert committed.status_code == 200, committed.text
    artifacts = committed.json()["artifacts"]
    assert artifacts["evaluation_proof_ref"] == job["evaluation_proof_ref"]
    assert artifacts["evaluation_governance_gate_state"] == "passed"
    assert artifacts["lineage_audit"]["evaluation_proof_ref"] == job["evaluation_proof_ref"]


def test_commit_rejects_persona_patch_without_eval_proof() -> None:
    module = _load_service_module()
    client = TestClient(module.app)
    session_id = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-alpha",
            "objective": "Reject unproven patch",
            "created_at": "2026-04-28T23:00:00Z",
        },
    ).json()["session_id"]

    completed = client.post(f"/api/training/sessions/{session_id}/complete")
    assert completed.status_code == 409
    assert "passing worker evaluation proof" in completed.text


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
            "tenant_id": "tenant-test",
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
            "tenant_id": "tenant-test",
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
    _preview, _job = _run_worker_preview(module, client, "trn-legacy-001", key="legacy-preview")

    completed = client.post("/api/training/sessions/trn-legacy-001/complete")

    assert completed.status_code == 201
    payload = completed.json()
    module.TeachingSession.from_dict(payload)
    module.TeachingEvent.from_dict(payload["events"][-1])
    assert payload["mode"] == "coaching"
    assert payload["trace_id"] == "trace-trn-legacy-001"


def test_discard_replay_records_decision_lineage_and_idempotent_replay() -> None:
    module = _load_service_module()
    client = TestClient(module.app)
    created = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-beta",
            "objective": "Discard a candidate patch",
            "actor_id": "operator-2",
            "created_at": "2026-04-29T10:00:00Z",
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    _preview, _job = _run_worker_preview(module, client, session_id, key="discard-preview")
    completed = client.post(f"/api/training/sessions/{session_id}/complete")
    assert completed.status_code == 201
    candidate_snapshot_at = completed.json()["events"][-1]["eval_ref"]["candidate_snapshot_at"]
    discard_body = {
        "expected_candidate_snapshot_at": candidate_snapshot_at,
        "actor_id": "operator-2",
        "note": "Discard candidate after review.",
        "decided_at": "2026-04-29T10:05:00Z",
    }

    discarded = client.post(
        f"/api/training/replays/{session_id}/discard",
        json=discard_body,
        headers={"X-Idempotency-Key": "trn004-discard-001"},
    )

    assert discarded.status_code == 200
    payload = discarded.json()
    module.TeachingEvent.from_dict(payload["events"][-1])
    assert payload["replay_resolution"]["state"] == "discarded"
    assert payload["replay_resolution"]["idempotency"]["key"] == "trn004-discard-001"
    artifact_refs = payload["events"][-1]["artifact_refs"]
    assert artifact_refs["decision_record_ref"].startswith("runtime-evidence:")
    assert artifact_refs["after_artifact_ref"] is None

    replayed_discard = client.post(
        f"/api/training/replays/{session_id}/discard",
        json=discard_body,
        headers={"X-Idempotency-Key": "trn004-discard-001"},
    )
    assert replayed_discard.status_code == 200
    assert replayed_discard.json()["replay_resolution"]["idempotency"]["replayed"] is True
    assert [event["event_type"] for event in replayed_discard.json()["events"]].count("discard") == 1


def test_replay_commit_idempotency_replays_without_duplicate_event() -> None:
    module = _load_service_module()
    client = TestClient(module.app)
    session_id = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-alpha",
            "objective": "Commit idempotently",
            "created_at": "2026-04-28T19:00:00Z",
        },
    ).json()["session_id"]

    _preview, _job = _run_worker_preview(module, client, session_id, key="commit-idempotency-preview")
    completed = client.post(f"/api/training/sessions/{session_id}/complete")
    assert completed.status_code == 201
    candidate_snapshot_at = completed.json()["events"][-1]["eval_ref"]["candidate_snapshot_at"]
    body = {
        "expected_candidate_snapshot_at": candidate_snapshot_at,
        "actor_id": "operator-1",
        "note": "Accept candidate.",
        "decided_at": "2026-04-28T19:05:00Z",
    }

    first = client.post(
        f"/api/training/replays/{session_id}/commit",
        json=body,
        headers={"Idempotency-Key": "trn004-commit-001"},
    )
    replay = client.post(
        f"/api/training/replays/{session_id}/commit",
        json=body,
        headers={"Idempotency-Key": "trn004-commit-001"},
    )
    conflict = client.post(
        f"/api/training/replays/{session_id}/commit",
        json={**body, "note": "Changed note."},
        headers={"Idempotency-Key": "trn004-commit-001"},
    )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert conflict.status_code == 409, conflict.text
    assert first.json()["events"][-1]["event_id"] == replay.json()["events"][-1]["event_id"]
    assert first.json()["replay_resolution"]["idempotency"]["replayed"] is False
    assert replay.json()["replay_resolution"]["idempotency"]["replayed"] is True
    events = module.TrainingSessionStore(module.store.data_dir).list_event_log(session_id)
    assert [event["event_type"] for event in events].count("commit") == 1


def test_concurrent_replay_commit_serializes_persona_commit_and_evidence() -> None:
    module = _load_service_module()
    client = TestClient(module.app)
    session_id = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-alpha",
            "objective": "Commit concurrently",
            "created_at": "2026-04-28T19:30:00Z",
        },
    ).json()["session_id"]

    _preview, _job = _run_worker_preview(module, client, session_id, key="concurrent-commit-preview")
    completed = client.post(f"/api/training/sessions/{session_id}/complete")
    assert completed.status_code == 201
    candidate_snapshot_at = completed.json()["events"][-1]["eval_ref"]["candidate_snapshot_at"]
    body = {
        "expected_candidate_snapshot_at": candidate_snapshot_at,
        "actor_id": "operator-1",
        "note": "Accept candidate.",
        "decided_at": "2026-04-28T19:35:00Z",
    }

    base_commit = make_fake_persona_target_commit()
    call_lock = threading.Lock()
    commit_calls: list[str] = []

    def counted_commit(**kwargs):
        with call_lock:
            commit_calls.append(str(kwargs.get("idempotency_key") or ""))
        time.sleep(0.05)
        return base_commit(**kwargs)

    module._commit_authoritative_persona_target = counted_commit
    start = threading.Event()

    def post_commit() -> tuple[int, dict]:
        local_client = TestClient(module.app)
        start.wait(timeout=5)
        response = local_client.post(
            f"/api/training/replays/{session_id}/commit",
            json=body,
            headers={"Idempotency-Key": "trn004-concurrent-commit"},
        )
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(post_commit) for _ in range(2)]
        start.set()
        results = [future.result(timeout=10) for future in futures]

    assert [status for status, _payload in results] == [200, 200], results
    payloads = [payload for _status, payload in results]
    replayed_flags = sorted(
        payload["replay_resolution"]["idempotency"]["replayed"]
        for payload in payloads
    )
    assert replayed_flags == [False, True]
    assert commit_calls == ["trn004-concurrent-commit"]
    events = module.TrainingSessionStore(module.store.data_dir).list_event_log(session_id)
    assert [event["event_type"] for event in events].count("commit") == 1

    evidence_event_types = [
        record["event_type"]
        for record in module._runtime_evidence_log().read_verified()
    ]
    assert evidence_event_types.count("persona_commit_admission_intent") == 1
    assert evidence_event_types.count("persona_commit_terminal_readback") == 1


def test_replay_commit_records_persona_route_policy_lineage_refs() -> None:
    module = _load_service_module()
    client = TestClient(module.app)
    session_id = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-alpha",
            "objective": "Record lineage refs",
            "created_at": "2026-04-28T20:00:00Z",
        },
    ).json()["session_id"]
    _preview, _job = _run_worker_preview(module, client, session_id, key="lineage-preview")
    completed = client.post(f"/api/training/sessions/{session_id}/complete")
    candidate_snapshot_at = completed.json()["events"][-1]["eval_ref"]["candidate_snapshot_at"]

    committed = client.post(
        f"/api/training/replays/{session_id}/commit",
        json={
            "expected_candidate_snapshot_at": candidate_snapshot_at,
            "actor_id": "operator-1",
            "decided_at": "2026-04-28T20:05:00Z",
        },
        headers={"Idempotency-Key": "trn004-commit-lineage"},
    )

    assert committed.status_code == 200, committed.text
    artifacts = committed.json()["artifacts"]
    assert artifacts["decision_record_ref"].startswith("runtime-evidence:")
    assert artifacts["persona_policy_ref"] == artifacts["persona_target_controller_record_ref"]
    assert artifacts["after_artifact_ref"] == artifacts["persona_target_controller_record_ref"]
    assert artifacts["approval_controller_record_ref"]
    assert artifacts["approval_decision_ref"]
    event_refs = committed.json()["events"][-1]["artifact_refs"]
    assert event_refs["persona_target_controller_record_ref"] == artifacts["persona_target_controller_record_ref"]
    assert event_refs["approval_controller_record_ref"] == artifacts["approval_controller_record_ref"]
    assert event_refs["policy_lineage_edge_ids"] == artifacts["policy_lineage_edge_ids"]
    assert artifacts["policy_lineage_store_ref"] == "lineage-read:training-session-policy-lineage"
    lineage_edges = module.build_lineage_read_store(module.store.data_dir).list_edges()
    assert [edge["edge_id"] for edge in lineage_edges] == artifacts["policy_lineage_edge_ids"]
    assert lineage_edges[0]["source"] == "trainer_session"
    assert lineage_edges[0]["producer"] == session_id
    assert lineage_edges[0]["target"] == "persona_policy_artifact"
    assert lineage_edges[0]["target_id"] == artifacts["persona_policy_ref"]


def test_replay_discard_idempotency_keeps_after_artifact_empty() -> None:
    module = _load_service_module()
    client = TestClient(module.app)
    session_id = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-alpha",
            "objective": "Discard idempotently",
            "created_at": "2026-04-28T21:00:00Z",
        },
    ).json()["session_id"]
    _preview, _job = _run_worker_preview(module, client, session_id, key="discard-idempotency-preview")
    completed = client.post(f"/api/training/sessions/{session_id}/complete")
    candidate_snapshot_at = completed.json()["events"][-1]["eval_ref"]["candidate_snapshot_at"]
    body = {
        "expected_candidate_snapshot_at": candidate_snapshot_at,
        "actor_id": "operator-1",
        "decided_at": "2026-04-28T21:05:00Z",
    }

    first = client.post(
        f"/api/training/replays/{session_id}/discard",
        json=body,
        headers={"X-Idempotency-Key": "trn004-discard-001"},
    )
    replay = client.post(
        f"/api/training/replays/{session_id}/discard",
        json=body,
        headers={"X-Idempotency-Key": "trn004-discard-001"},
    )

    assert replay.json()["replay_resolution"]["state"] == "discarded"
    assert replay.json()["replay_resolution"]["idempotency"]["replayed"] is True
    assert replay.json()["artifacts"]["after_artifact_ref"] is None
    assert "persona_policy_ref" not in replay.json()["artifacts"]
    events = module.TrainingSessionStore(module.store.data_dir).list_event_log(session_id)
    assert [event["event_type"] for event in events].count("discard") == 1


def test_canonical_dataset_validation_and_fail_closed_and_evidence(consultation_owner) -> None:
    module = _load_service_module()
    client = TestClient(module.app)
    fixture = module._strict_authority_fixture
    target_commit = module._commit_authoritative_persona_target
    persona_mutations: list[str] = []

    def counted_target_commit(**kwargs):
        persona_mutations.append(str(kwargs["session_id"]))
        return target_commit(**kwargs)

    module._commit_authoritative_persona_target = counted_target_commit

    failing_policy = dict(fixture.policy)
    failing_policy["min_sharpe_ratio"] = 99.0
    fixture.policy_path.write_text(json.dumps(failing_policy), encoding="utf-8")

    session_id = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-alpha",
            "objective": "Test fail closed validation",
            "created_at": "2026-04-28T22:00:00Z",
        },
    ).json()["session_id"]

    preview, job = _run_worker_preview(module, client, session_id, key="fail-closed-preview")
    assert job["status"] == "completed"
    assert preview["validation_status"] == "failed"
    assert preview["evaluation_proof"]["status"] == "failed"
    assert preview["evaluation_proof"]["governance_gate_state"] == "failed"

    complete = client.post(f"/api/training/sessions/{session_id}/complete")
    assert complete.status_code == 409
    assert "evaluation governance gate is not passed" in complete.text
    assert persona_mutations == []
    ready_async = client.get("/readyz")
    assert ready_async.status_code == 200
    assert ready_async.json()["dependencies"]["functional"]["status"] == "ok"
    assert ready_async.json()["dependencies"]["functional"]["failure_count"] == 0

    session_id_sync = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-alpha",
            "objective": "Test sync fail closed validation",
            "created_at": "2026-04-28T22:30:00Z",
        },
    ).json()["session_id"]
    seed_changed_supported_controls(module, session_id_sync)
    sync_preview_resp = client.post(
        f"/api/training/sessions/{session_id_sync}/preview",
        json={"mode": "refresh"},
    )
    assert sync_preview_resp.status_code == 201, sync_preview_resp.text
    sync_preview = sync_preview_resp.json()
    assert sync_preview["validation_status"] == "failed"
    assert sync_preview["evaluation_proof"]["status"] == "failed"
    assert sync_preview["evaluation_proof"]["governance_gate_state"] == "failed"
    complete_sync = client.post(f"/api/training/sessions/{session_id_sync}/complete")
    assert complete_sync.status_code == 409
    assert "evaluation governance gate is not passed" in complete_sync.text
    assert persona_mutations == []
    ready_sync = client.get("/readyz")
    assert ready_sync.status_code == 200
    assert ready_sync.json()["dependencies"]["functional"]["status"] == "ok"
    assert ready_sync.json()["dependencies"]["functional"]["failure_count"] == 0

    test_tenant_id = job["tenant_id"]
    persisted_negative_record = {
        "status": "failed",
        "checked_at": "2026-10-15T20:03:12Z",
        "actor_service": "training-session-preview-worker",
        "detail": {
            "evaluation_proof_ref": preview["evaluation_proof"]["proof_ref"],
            "governance_gate_state": "failed",
            "job_id": job["job_id"],
            "session_id": session_id,
        },
    }
    module.store.put_functional_result(
        "preview_evaluation",
        test_tenant_id,
        persisted_negative_record,
    )
    records_before = module.store.list_functional_results()
    ready_reconciled = client.get("/readyz")
    assert ready_reconciled.status_code == 200
    assert ready_reconciled.json()["dependencies"]["functional"]["status"] == "ok"
    assert ready_reconciled.json()["dependencies"]["functional"]["failure_count"] == 0
    # No-write case: health read must be read-only and preserve raw failure audit in store
    records_after = module.store.list_functional_results()
    assert records_after == records_before
    persisted_raw = [
        r for r in records_after
        if r.get("operation") == "preview_evaluation" and r.get("tenant_id") == test_tenant_id
    ][0]
    assert persisted_raw["status"] == "failed"

    # Missing authority: bare detail proof_ref / governance state with missing job or session degrades
    module.store.put_functional_result(
        "preview_evaluation",
        test_tenant_id,
        {
            "status": "failed",
            "checked_at": "2026-10-15T20:04:00Z",
            "actor_service": "training-session-preview-worker",
            "detail": {
                "evaluation_proof_ref": "missing-original-proof",
                "governance_gate_state": "failed",
                "job_id": "missing-original-job",
                "session_id": "missing-original-session",
            },
        },
    )
    ready_missing_auth = client.get("/readyz")
    assert ready_missing_auth.status_code == 503
    assert ready_missing_auth.json()["dependencies"]["functional"]["status"] == "degraded"
    assert ready_missing_auth.json()["dependencies"]["functional"]["failure_count"] == 1

    # Store error: storage backend lookup error remains honestly degraded
    module.store.put_functional_result(
        "preview_evaluation",
        test_tenant_id,
        persisted_negative_record,
    )
    original_get_preview_job = module.store.get_preview_job

    def _exploding_get_preview_job(jid: str):
        raise OSError("storage backend disk fault")

    module.store.get_preview_job = _exploding_get_preview_job
    try:
        ready_store_error = client.get("/readyz")
        assert ready_store_error.status_code == 503
        assert ready_store_error.json()["dependencies"]["functional"]["status"] == "degraded"
        assert ready_store_error.json()["dependencies"]["functional"]["failure_count"] == 1
    finally:
        module.store.get_preview_job = original_get_preview_job

    # Valid persisted sync negative record: valid session and bundle owner authority recognized as ok
    persisted_sync_negative = {
        "status": "failed",
        "checked_at": "2026-10-15T20:04:30Z",
        "actor_service": "training-session",
        "detail": {
            "evaluation_proof_ref": sync_preview["evaluation_proof"]["proof_ref"],
            "governance_gate_state": "failed",
            "session_id": session_id_sync,
        },
    }
    module.store.put_functional_result(
        "preview_evaluation",
        test_tenant_id,
        persisted_sync_negative,
    )
    ready_sync_reconciled = client.get("/readyz")
    assert ready_sync_reconciled.status_code == 200
    assert ready_sync_reconciled.json()["dependencies"]["functional"]["status"] == "ok"
    assert ready_sync_reconciled.json()["dependencies"]["functional"]["failure_count"] == 0

    # Sync missing authority: non-existent session_id degrades to 503
    module.store.put_functional_result(
        "preview_evaluation",
        test_tenant_id,
        {
            "status": "failed",
            "checked_at": "2026-10-15T20:04:45Z",
            "actor_service": "training-session",
            "detail": {
                "evaluation_proof_ref": "missing-sync-proof",
                "governance_gate_state": "failed",
                "session_id": "missing-sync-session",
            },
        },
    )
    ready_missing_sync = client.get("/readyz")
    assert ready_missing_sync.status_code == 503
    assert ready_missing_sync.json()["dependencies"]["functional"]["status"] == "degraded"
    assert ready_missing_sync.json()["dependencies"]["functional"]["failure_count"] == 1

    module.store.put_functional_result(
        "preview_evaluation",
        test_tenant_id,
        {
            "status": "failed",
            "checked_at": "2026-10-15T20:05:00Z",
            "actor_service": "training-session",
            "detail": {
                "session_id": session_id,
                "error_code": "preview_evidence_unavailable",
            },
        },
    )
    degraded = client.get("/readyz")
    assert degraded.status_code == 503
    assert degraded.json()["dependencies"]["functional"]["status"] == "degraded"
    assert degraded.json()["dependencies"]["functional"]["failure_count"] == 1

    fixture.policy_path.write_text(json.dumps(fixture.policy), encoding="utf-8")

    session_id_pass = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-alpha",
            "objective": "Test passing validation",
            "created_at": "2026-04-28T23:00:00Z",
        },
    ).json()["session_id"]

    preview_pass, _job_pass = _run_worker_preview(module, client, session_id_pass, key="pass-preview")
    assert preview_pass["validation_status"] == "passed"
    assert preview_pass["evaluation_proof"]["status"] == "passed"

    complete_pass = client.post(f"/api/training/sessions/{session_id_pass}/complete")
    assert complete_pass.status_code == 201
    commit_pass = client.post(
        f"/api/training/replays/{session_id_pass}/commit",
        json={
            "expected_candidate_snapshot_at": complete_pass.json()["events"][-1]["eval_ref"]["candidate_snapshot_at"],
            "actor_id": "operator-1",
            "decided_at": "2026-04-28T23:05:00Z",
        },
        headers={"Idempotency-Key": "pass-commit"},
    )
    assert commit_pass.status_code == 200, commit_pass.text
    assert persona_mutations == [session_id_pass]
    recovered = client.get("/readyz")
    assert recovered.status_code == 200
    assert recovered.json()["dependencies"]["functional"]["status"] == "ok"

    records = module._runtime_evidence_log().read_verified()
    event_types = {record["event_type"] for record in records}
    assert "preview_evaluation_terminal" in event_types
    assert "persona_commit_terminal_readback" in event_types
    for record in records:
        payload = record["payload"]
        assert payload.get("decision_by", None) in (None, "[REDACTED]")
        assert payload.get("requested_by", None) in (None, "[REDACTED]")


def test_source_authority_read_sends_reader_bearer_and_tenant_or_fails_closed(tmp_path: Path) -> None:
    module = _load_service_module()
    captured: dict = {}

    def fake_materialize(**kwargs):
        kwargs["http_get"]("http://source-ingest:8097/api/source-ingest/evidence/bundles")
        raise module.SourceDatasetAuthorityError("stop after transport")

    def fake_get(url, *, timeout_seconds, headers=None):
        captured["headers"] = headers
        return {}

    token = tmp_path / "token"
    token.write_text("reader.jwt.token\n", encoding="utf-8")
    token.chmod(0o600)
    env = {
        "TRAINING_SESSION_CANONICAL_DATASET_PATH": "",
        "SOURCE_INGEST_API_URL": "http://source-ingest:8097",
        "TRAINING_SESSION_SOURCE_CONNECTOR_ID": "c",
        "TRAINING_SESSION_SOURCE_DATASET_ID": "d",
        "TRAINING_SESSION_SOURCE_VOLUME_ROOT": str(tmp_path),
        "TRAINING_SESSION_DATASET_AUTHORITY_OUTPUT_ROOT": str(tmp_path / "out"),
        "TRAINING_SESSION_SOURCE_READ_TOKEN_FILE": str(token),
        "TRAINING_SESSION_SOURCE_READ_TENANT_ID": "tenant-dev",
    }
    with mock.patch.dict("os.environ", env), mock.patch.object(
        module, "materialize_source_dataset_version", fake_materialize
    ), mock.patch.object(module, "urllib_json_get", fake_get):
        try:
            module._load_authority_snapshot(trusted_now=FIXED_TRUSTED_NOW, strategy_id="s")
        except module.AuthorityValidationError:
            pass
    assert captured["headers"] == {
        "Authorization": "Bearer reader.jwt.token",
        "X-Tenant-Id": "tenant-dev",
    }

    token.unlink()
    captured.clear()
    with mock.patch.dict("os.environ", env), mock.patch.object(
        module, "materialize_source_dataset_version", fake_materialize
    ), mock.patch.object(module, "urllib_json_get", fake_get):
        try:
            module._load_authority_snapshot(trusted_now=FIXED_TRUSTED_NOW, strategy_id="s")
            raised = None
        except module.AuthorityValidationError as exc:
            raised = exc
    assert raised is not None and "token file" in str(raised)
    assert "headers" not in captured


def test_evaluate_then_complete_passes_when_controller_and_requirement_sequences_advance(tmp_path: Path) -> None:
    module = _load_service_module()
    client = TestClient(module.app)

    from test_source_dataset_authority import (
        _make_case,
        _policy,
        BASE_URL,
        CONNECTOR_ID,
        DATASET_ID,
    )

    case = _make_case(tmp_path)
    policy_path = tmp_path / "threshold_policy.json"
    policy_path.write_text(json.dumps(_policy()), encoding="utf-8")

    token_file = tmp_path / "reader_token"
    token_file.write_text("reader.jwt.token\n")
    token_file.chmod(0o600)

    env = {
        "TRAINING_SESSION_CANONICAL_DATASET_PATH": "",
        "SOURCE_INGEST_API_URL": BASE_URL,
        "TRAINING_SESSION_SOURCE_CONNECTOR_ID": CONNECTOR_ID,
        "TRAINING_SESSION_SOURCE_DATASET_ID": DATASET_ID,
        "TRAINING_SESSION_SOURCE_VOLUME_ROOT": str(case.source_root),
        "TRAINING_SESSION_DATASET_AUTHORITY_OUTPUT_ROOT": str(case.output_root),
        "TRAINING_SESSION_THRESHOLD_POLICY_PATH": str(policy_path),
        "TRAINING_SESSION_SOURCE_READ_TOKEN_FILE": str(token_file),
        "TRAINING_SESSION_SOURCE_READ_TENANT_ID": "tenant-dev",
    }

    def fake_get(url, *, timeout_seconds, headers=None):
        return case.get(url)

    with mock.patch.dict("os.environ", env), mock.patch.object(module, "urllib_json_get", fake_get):
        created = client.post(
            "/api/training/sessions",
            json={
                "persona_id": "persona-seq-test",
                "objective": "Test sequence advance between eval and complete",
                "actor_id": "operator-1",
            },
        )
        assert created.status_code == 201
        session_id = created.json()["session_id"]
        seed_changed_supported_controls(module, session_id)

        queued = client.post(
            f"/api/training/sessions/{session_id}/preview-jobs",
            json={"mode": "refresh", "requested_by": "operator-1", "terminalize_session": True},
            headers={"Idempotency-Key": "seq-advance-001"},
        )
        assert queued.status_code == 201
        job_id = queued.json()["job_id"]

        run_res = client.post(f"/api/training/preview-jobs/{job_id}/run", json={})
        assert run_res.status_code == 200
        assert run_res.json()["status"] == "completed"

        # Advance controller sequence from 42 to 45 and requirement sequence from 7 to 9
        readback = case.responses[f"{BASE_URL}/api/source-ingest/controller/readback"]
        readback["controller_state"]["sequence_no"] = 45
        readback["requirement_snapshot"]["sequence"] = 9

        # Complete succeeds because operational sequences only advanced
        comp = client.post(f"/api/training/sessions/{session_id}/complete")
        assert comp.status_code == 201, comp.text
        assert comp.json()["status"] == "completed"


def test_complete_fails_closed_when_controller_sequence_regresses_or_semantic_authority_changes(tmp_path: Path) -> None:
    module = _load_service_module()
    client = TestClient(module.app)

    from test_source_dataset_authority import (
        _make_case,
        _policy,
        BASE_URL,
        CONNECTOR_ID,
        DATASET_ID,
    )

    case = _make_case(tmp_path)
    policy_path = tmp_path / "threshold_policy.json"
    policy_path.write_text(json.dumps(_policy()), encoding="utf-8")

    token_file = tmp_path / "reader_token"
    token_file.write_text("reader.jwt.token\n")
    token_file.chmod(0o600)

    env = {
        "TRAINING_SESSION_CANONICAL_DATASET_PATH": "",
        "SOURCE_INGEST_API_URL": BASE_URL,
        "TRAINING_SESSION_SOURCE_CONNECTOR_ID": CONNECTOR_ID,
        "TRAINING_SESSION_SOURCE_DATASET_ID": DATASET_ID,
        "TRAINING_SESSION_SOURCE_VOLUME_ROOT": str(case.source_root),
        "TRAINING_SESSION_DATASET_AUTHORITY_OUTPUT_ROOT": str(case.output_root),
        "TRAINING_SESSION_THRESHOLD_POLICY_PATH": str(policy_path),
        "TRAINING_SESSION_SOURCE_READ_TOKEN_FILE": str(token_file),
        "TRAINING_SESSION_SOURCE_READ_TENANT_ID": "tenant-dev",
    }

    def fake_get(url, *, timeout_seconds, headers=None):
        return case.get(url)

    with mock.patch.dict("os.environ", env), mock.patch.object(module, "urllib_json_get", fake_get):
        created = client.post(
            "/api/training/sessions",
            json={
                "persona_id": "persona-regress-test",
                "objective": "Test sequence regress between eval and complete",
                "actor_id": "operator-1",
            },
        )
        assert created.status_code == 201
        session_id = created.json()["session_id"]
        seed_changed_supported_controls(module, session_id)

        queued = client.post(
            f"/api/training/sessions/{session_id}/preview-jobs",
            json={"mode": "refresh", "requested_by": "operator-1", "terminalize_session": True},
            headers={"Idempotency-Key": "seq-regress-001"},
        )
        assert queued.status_code == 201
        job_id = queued.json()["job_id"]

        run_res = client.post(f"/api/training/preview-jobs/{job_id}/run", json={})
        assert run_res.status_code == 200
        assert run_res.json()["status"] == "completed"

        # Regress controller sequence from 42 to 40
        readback = case.responses[f"{BASE_URL}/api/source-ingest/controller/readback"]
        readback["controller_state"]["sequence_no"] = 40

        # Complete fails closed with 409
        comp = client.post(f"/api/training/sessions/{session_id}/complete")
        assert comp.status_code == 409
        assert "evaluation authority is no longer admissible" in comp.text


def test_terminal_session_recovery_claim_lease_fences_duplicate_claims_before_expiry() -> None:
    from datetime import timedelta

    module = _load_service_module()
    client = TestClient(module.app)
    current_clock = FIXED_TRUSTED_NOW
    module._trusted_now = lambda: current_clock

    # 1. Duplicate recovery claims before lease expiry do not consume another attempt or invalidate completion
    created = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-claim-fence-test",
            "objective": "Verify recovery claims respect lease fence",
            "actor_id": "operator-1",
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    seed_changed_supported_controls(module, session_id)

    queued = client.post(
        f"/api/training/sessions/{session_id}/preview-jobs",
        json={"mode": "refresh", "requested_by": "operator-1", "terminalize_session": True},
        headers={"Idempotency-Key": "claim-fence-001"},
    )
    assert queued.status_code == 201
    job_id = queued.json()["job_id"]

    # Call 1: initial run completes evaluation
    run1 = client.post(f"/api/training/preview-jobs/{job_id}/run", json={})
    assert run1.status_code == 200
    job1 = run1.json()
    assert job1["status"] == "completed"
    assert job1["attempt_count"] == 1
    assert job1.get("lease_expires_at") is None

    # Call 2: first recovery claim (e.g. lost response before complete)
    run2 = client.post(f"/api/training/preview-jobs/{job_id}/run", json={})
    assert run2.status_code == 200
    job2 = run2.json()
    assert job2["status"] == "completed"
    assert job2["reclaimed"] is True
    assert job2["replayed"] is True
    assert job2["attempt_count"] == 2
    assert job2.get("lease_expires_at") is not None

    # Call 3: duplicate claim at same injected clock before expiry returns 409 and does not increment attempts
    run3 = client.post(f"/api/training/preview-jobs/{job_id}/run", json={})
    assert run3.status_code == 409
    assert run3.json()["detail"] == "preview job is not claimable"

    # Call 4: another duplicate claim before expiry (e.g. 30s later) returns 409
    current_clock += timedelta(seconds=30)
    run4 = client.post(f"/api/training/preview-jobs/{job_id}/run", json={})
    assert run4.status_code == 409
    assert run4.json()["detail"] == "preview job is not claimable"

    # Job in store retains attempt_count=2, completed status, and original evaluation proof
    stored_job = module.store.get_preview_job(job_id)
    assert stored_job["status"] == "completed"
    assert stored_job["attempt_count"] == 2
    assert stored_job.get("error_code") is None

    # Valid completion succeeds without worker provenance error
    comp = client.post(f"/api/training/sessions/{session_id}/complete")
    assert comp.status_code == 201, comp.text
    assert comp.json()["status"] == "completed"
    assert comp.json()["ended_at"] is not None

    # Once session is terminal, job is no longer claimable even after lease expiry
    current_clock += timedelta(seconds=121)
    run_after_term = client.post(f"/api/training/preview-jobs/{job_id}/run", json={})
    assert run_after_term.status_code == 200
    assert run_after_term.json()["replayed"] is True

    # 2. Advance injected clock past lease: genuine recovery and exhaustion still works
    created2 = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-exhaustion-api-test",
            "objective": "Verify genuine recovery exhaustion after advancing past lease",
            "actor_id": "operator-1",
        },
    )
    assert created2.status_code == 201
    session_id2 = created2.json()["session_id"]
    seed_changed_supported_controls(module, session_id2)

    queued2 = client.post(
        f"/api/training/sessions/{session_id2}/preview-jobs",
        json={"mode": "refresh", "requested_by": "operator-1", "terminalize_session": True},
        headers={"Idempotency-Key": "claim-fence-002"},
    )
    assert queued2.status_code == 201
    job_id2 = queued2.json()["job_id"]

    # Call 1: initial run
    res1 = client.post(f"/api/training/preview-jobs/{job_id2}/run", json={})
    assert res1.status_code == 200
    assert res1.json()["attempt_count"] == 1

    # Call 2: first recovery claim (attempt 2)
    res2 = client.post(f"/api/training/preview-jobs/{job_id2}/run", json={})
    assert res2.status_code == 200
    assert res2.json()["attempt_count"] == 2

    # Duplicate claim before expiry returns 409
    dup = client.post(f"/api/training/preview-jobs/{job_id2}/run", json={})
    assert dup.status_code == 409

    # Advance clock past lease -> second recovery claim succeeds (attempt 3)
    current_clock += timedelta(seconds=121)
    res3 = client.post(f"/api/training/preview-jobs/{job_id2}/run", json={})
    assert res3.status_code == 200
    assert res3.json()["attempt_count"] == 3
    assert res3.json()["retryable"] is False

    # Duplicate claim before expiry returns 409
    dup2 = client.post(f"/api/training/preview-jobs/{job_id2}/run", json={})
    assert dup2.status_code == 409

    # Advance clock past lease -> attempt budget exhausted (3 >= max 3), job marked failed
    current_clock += timedelta(seconds=121)
    res4 = client.post(f"/api/training/preview-jobs/{job_id2}/run", json={})
    assert res4.status_code == 200
    failed_job = res4.json()
    assert failed_job["status"] == "failed"
    assert failed_job["error_code"] == "terminal_session_completion_exhausted"
    assert failed_job["retryable"] is False
    assert failed_job["failed_at"] == current_clock.isoformat().replace("+00:00", "Z")

    # Subsequent claims fail closed
    res5 = client.post(f"/api/training/preview-jobs/{job_id2}/run", json={})
    assert res5.status_code == 409
    assert res5.json()["detail"] == "preview job is not claimable"

    # Session stays active
    session2 = module.store.get_session(session_id2)
    assert session2["status"] == "active"
    assert session2.get("ended_at") is None


# --- Canonical model-bound teaching sessions ---------------------------------

# --- Persona/model fixture (mirrors a real provisioning readback) ---
import copy as _fx_copy
import json as _fx_json

from services.registry.strategy_artifact import strategy_artifact_checksum as _fx_checksum

_FX_PERSONA_ID = "persona-132519698e6199e80326"
_FX_TENANT_ID = "tenant-dev"
_FX_ARTIFACT_ID = "artifact-persona-paper-aa060d53ff712cbba704"
_FX_STRATEGY_ID = "strategy-persona-aa060d53ff712cbba704"


def _fx_momentum_artifact(
    *, lookback: int = 2, threshold: float = 0.0, symbols: tuple[str, ...] = ("2330.TW",)
) -> dict[str, Any]:
    return {
        "artifact_id": _FX_ARTIFACT_ID,
        "strategy_id": _FX_STRATEGY_ID,
        "version": "1.0.0",
        "artifact_schema_version": "1.0",
        "lineage": {
            "source_run_ids": ["persona-provisioning-forward-artifact-aa060d53ff712cbba704"],
            "parent_registry_ids": ["artifact-persona-paper-baseline-aa060d53ff712cbba704"],
            "source_strategy_spec_id": "reg-strategy-spec-persona-aa060d53ff712cbba704",
        },
        "algorithm_ref": {
            "path": "services/registry/strategy_artifact.py",
            "commit": "1cfaf933fbcc119605a455ebea483d54d2521ebb",
            "engine": "lean",
            "entrypoint": "services.registry.strategy_artifact:evaluate_strategy_action",
            "repository": "ajoe734/pantheon",
            "signal_interface": "services.execution.lean_runtime.paper_signal_producer:Strategy",
            "logic_interpreter": "services.registry.strategy_artifact:evaluate_strategy_action",
            "signal_schema_version": "1.0",
        },
        "strategy_logic": {
            "kind": "close_to_close_momentum",
            "positive_action": "BUY",
            "non_positive_action": "SELL",
            "lookback_parameter": "lookback_bars",
            "threshold_parameter": "momentum_threshold",
        },
        "parameters": {
            "market": "TW",
            "symbols": list(symbols),
            "data_source": "source-ingest:paper/persona-bootstrap",
            "bar_frequency": "1d",
            "lookback_bars": lookback,
            "quantity_type": "SHARES",
            "order_quantity": 1,
            "momentum_threshold": threshold,
            "zero_momentum_action": "SELL",
        },
        "provenance_refs": ["task:LOOP-PROD-PER-001"],
        "mutation_surface": {
            "controls": [
                {"parameter_key": "lookback_bars", "value_type": "integer", "current_value": lookback,
                 "allowed_range": {"min": 2, "max": 60}, "step": 1},
                {"parameter_key": "momentum_threshold", "value_type": "number", "current_value": threshold,
                 "allowed_range": {"min": 0.0, "max": 0.05}, "step": 0.001},
            ],
            "immutable_parameters": [
                "symbols", "bar_frequency", "data_source", "order_quantity",
                "quantity_type", "zero_momentum_action", "market",
            ],
        },
    }


def _fx_runtime_binding(artifact: dict[str, Any] | None = None, *, tenant_id: str = _FX_TENANT_ID) -> dict[str, Any]:
    artifact = _fx_copy.deepcopy(artifact or _fx_momentum_artifact())
    payload = _fx_json.dumps(artifact, sort_keys=True, separators=(",", ":"))
    checksum = _fx_checksum(artifact)
    metadata = {
        "version": artifact["version"],
        "checksum": checksum,
        "registry_id": artifact["artifact_id"],
        "strategy_id": artifact["strategy_id"],
        "artifact_type": "execution_bundle",
        "artifact_state": "approved",
        "runtime_action": "deploy_new_binding",
        "promotion_state": "paper",
        "deployment_stage": "paper",
        "created_at": "2026-10-15T19:19:47Z",
        "approved_at": "2026-10-15T19:19:48Z",
        "lineage": _fx_copy.deepcopy(artifact["lineage"]),
        "rollback": {
            "action_type": "pause_then_replace",
            "target_version": "0.0.1",
            "target_registry_id": "artifact-persona-paper-baseline-aa060d53ff712cbba704",
        },
        "capital_pool_id": "pool-persona-paper-aa060d53ff712cbba704",
        "gross_scale_pct": 100.0,
        "capital_scale_pct": 0.0,
        "deployment_plan_id": "plan-persona-paper-aa060d53ff712cbba704",
        "sponsor_persona_id": _FX_PERSONA_ID,
        "approval_decision_id": "apv-persona-paper-artifact-aa060d53ff712cbba704",
    }
    base = f"openclaw/registry/{artifact['strategy_id']}/{artifact['version']}"
    return {
        "status": "active",
        "binding_id": "rb-ece1ab6cfcdc4c518c40874a97a58a66",
        "runtime_id": "rt-a47d6729",
        "artifact_id": artifact["artifact_id"],
        "artifact_version": artifact["version"],
        "execution_mode": "paper",
        "metadata": {
            "tenant_id": tenant_id,
            "persona_id": _FX_PERSONA_ID,
            "strategy_id": artifact["strategy_id"],
            "artifact_checksum": checksum,
            "object_store": {f"{base}/artifact.bin": payload, f"{base}/metadata.json": metadata},
        },
    }


def _fx_persona_with_model(artifact: dict[str, Any] | None = None, *, tenant_id: str = _FX_TENANT_ID) -> dict[str, Any]:
    return {
        "persona_id": _FX_PERSONA_ID,
        "tenant_id": tenant_id,
        "metadata": {
            "tenant_id": tenant_id,
            "provisioning_authoritative_readback": {"runtime_binding": _fx_runtime_binding(artifact, tenant_id=tenant_id)},
        },
    }

def _model_authority(*, tenant_id: str = "tenant-test", **artifact_kwargs) -> dict:
    """Persona served by the (faked) owner readback; the service derives the model itself."""
    return _fx_persona_with_model(_fx_momentum_artifact(**artifact_kwargs), tenant_id=tenant_id)


def _create_model_session(module, client: TestClient, persona: dict | None, persona_id: str | None = None):
    """Create through the service, serving ``persona`` from the owner readback it performs itself."""
    env = {"TRAINING_SESSION_PERSONA_READBACK_URL_TEMPLATE": "http://persona:8002/api/personas/{persona_id}/training-target"}
    requested: list[str] = []

    def _owner_get(_transport, _method, url, **_kwargs):
        requested.append(url)
        if url.endswith("/training-target"):
            # Real training-target shape: generation/controller state, no model metadata.
            return {"persona_id": persona_id or _FX_PERSONA_ID, "generation": 1, "controller": {}}
        return {"persona": persona}

    with mock.patch.dict(os.environ, env), \
            mock.patch.object(module, "_persona_authority_token", return_value="tok"), \
            mock.patch.object(module, "_request_json_object", side_effect=_owner_get):
        response = _post_create_model_session(client, persona_id)
    assert requested and not requested[0].endswith("/training-target"), requested
    return response


def _post_create_model_session(client: TestClient, persona_id: str | None):
    return client.post(
        "/api/training/sessions",
        json={
            "persona_id": persona_id or _FX_PERSONA_ID,
            "objective": "Teach the canonical momentum model",
            "created_at": "2026-04-28T22:00:00Z",
            # A caller-carried model is ignored: only the owner readback selects it.
            "model_authority": {"artifact_id": "forged", "strategy_artifact": {"parameters": {"lookback_bars": 9}}},
        },
    )


def test_model_bound_session_controls_come_from_artifact_mutation_surface() -> None:
    module = _load_service_module()
    client = TestClient(module.app)
    persona = _model_authority()
    created = _create_model_session(module, client, persona)
    assert created.status_code == 201, created.text
    session_id = created.json()["session_id"]
    frozen = created.json()["metadata"]["model_authority"]
    assert frozen["artifact_id"] == _FX_ARTIFACT_ID
    assert frozen["strategy_artifact"]["parameters"]["lookback_bars"] == 2

    controls = client.get(f"/api/training/controls/{session_id}").json()["controls"]
    assert [c["parameter_key"] for c in controls] == ["lookback_bars", "momentum_threshold"]
    assert controls[0]["allowed_range"] == {"min": 2, "max": 60}
    assert controls[0]["baseline_value"] == controls[0]["current_value"] == 2

    rejected = client.patch(
        f"/api/training/sessions/{session_id}/controls",
        json={"patches": [{"parameter_key": "lookback_bars", "proposed_value": 61}]},
    ).json()
    assert rejected["status"] == "rejected"
    sma_key = client.patch(
        f"/api/training/sessions/{session_id}/controls",
        json={"patches": [{"parameter_key": "short_window", "proposed_value": 7}]},
    ).json()
    assert sma_key["field_errors"][0]["reason"] == "unknown_parameter_key"


def test_model_bound_create_rejects_tampered_or_foreign_owner_model() -> None:
    module = _load_service_module()
    client = TestClient(module.app)
    persona = _model_authority()

    tampered = json.loads(json.dumps(persona))
    store = tampered["metadata"]["provisioning_authoritative_readback"]["runtime_binding"]["metadata"]["object_store"]
    artifact_key = next(key for key in store if key.endswith("artifact.bin"))
    store[artifact_key] = store[artifact_key].replace('"order_quantity":1', '"order_quantity":9')
    assert _create_model_session(module, client, tampered).status_code == 409

    assert _create_model_session(module, client, persona, persona_id="persona-someone-else").status_code == 409
    assert _create_model_session(module, client, _model_authority(tenant_id="tenant-other")).status_code == 409


def test_persona_without_runtime_binding_or_readback_stays_unbound() -> None:
    module = _load_service_module()
    client = TestClient(module.app)
    plain = {"persona_id": _FX_PERSONA_ID, "tenant_id": "tenant-test", "metadata": {}}
    created = _create_model_session(module, client, plain)
    assert created.status_code == 201, created.text
    assert "model_authority" not in (created.json().get("metadata") or {})
    controls = client.get(f"/api/training/controls/{created.json()['session_id']}").json()["controls"]
    assert "short_window" in {c["parameter_key"] for c in controls}


def test_model_bound_preview_runs_real_backend_on_derived_candidate(consultation_owner) -> None:
    from services.research.vectorbt.adapter import vectorbt_adapter as adapter

    module = _load_service_module()
    seen: dict = {}

    def real_workflow_on_tw_dataset(dataset, *, backend=None, config=None):
        # The strict fixture dataset is crypto; swap in a TW daily dataset that
        # holds the artifact symbol plus a second instrument, then run the REAL backend.
        from datetime import date, timedelta

        start = date(2024, 1, 1)
        series = [100.0, 101.0, 100.5, 102.0, 101.0, 103.0, 102.0, 104.0] * 5
        records = [
            {"instrument": symbol, "date": (start + timedelta(days=i)).isoformat(),
             "open": c, "high": c, "low": c, "close": c, "volume": 1000}
            for symbol, offset in (("2330.TWSE", 0.0), ("2317.TWSE", 10.0))
            for i, c in enumerate(series)
            for c in (c + offset,)
        ]
        seen["config"] = config
        return adapter.run_vectorbt_workflow(
            {**dataset, "records": records}, backend=backend, config=config
        )

    module.run_vectorbt_workflow = real_workflow_on_tw_dataset
    client = TestClient(module.app)
    authority = _model_authority()
    session_id = _create_model_session(module, client, authority).json()["session_id"]
    authority = {"artifact_id": _FX_ARTIFACT_ID}
    base_checksum = _fx_checksum(_fx_momentum_artifact())
    patched = client.patch(
        f"/api/training/sessions/{session_id}/controls",
        json={"patches": [
            {"parameter_key": "lookback_bars", "proposed_value": 5},
            {"parameter_key": "momentum_threshold", "proposed_value": 0.01},
        ]},
    ).json()
    assert patched["status"] == "accepted"

    with mock.patch.dict(os.environ, {"PANTHEON_VECTORBT_BACKEND": "real"}):
        queued = client.post(
            f"/api/training/sessions/{session_id}/preview-jobs",
            json={"mode": "refresh", "requested_by": "operator-1", "requested_at": "2026-07-15T12:00:00Z"},
            headers={"Idempotency-Key": "model-preview-001"},
        )
        assert queued.status_code == 201, queued.text
        run = client.post(f"/api/training/preview-jobs/{queued.json()['job_id']}/run", json={})
    assert run.status_code == 200, run.text

    candidate = seen["config"].strategy_artifact
    assert candidate["strategy_logic"]["kind"] == "close_to_close_momentum"
    assert candidate["parameters"]["lookback_bars"] == 5
    assert candidate["parameters"]["momentum_threshold"] == 0.01
    assert candidate["lineage"]["parent_registry_ids"] == [authority["artifact_id"]]

    proof = run.json()["preview"]["evaluation_proof"]
    model = proof["backend"]["model"]
    assert proof["backend"]["run_id"].startswith("vbt-real-")
    assert model["base_artifact_checksum"] == base_checksum
    assert model["candidate_artifact_checksum"] != model["base_artifact_checksum"]
    assert model["symbols"] == ["2330.TW"]
    assert proof["candidate_binding"]["model"]
    assert {c["parameter_key"] for c in proof["controls"]} == {"lookback_bars", "momentum_threshold"}
