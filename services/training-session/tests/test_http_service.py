from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

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


def test_canonical_dataset_validation_and_fail_closed_and_evidence() -> None:
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
