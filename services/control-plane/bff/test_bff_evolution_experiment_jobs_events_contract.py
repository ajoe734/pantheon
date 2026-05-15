"""
Contract tests for BFF-LUV-GAP-004: evolution programs, experiments, jobs,
and events BFF compatibility surfaces.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from command_queue import CommandStore
from read_store import ReadSurfaceStore


OPERATOR_TOKEN = "Bearer op-gap-004:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}
IDEMPOTENCY_KEY = "test-evo-exp-jobs-events-001"


@contextmanager
def _isolated_bff() -> Iterator[tuple[TestClient, ReadSurfaceStore]]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        bff_main.read_store = store
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._GOV_BFF_IDEMPOTENCY.clear()
        bff_main._GOV_BFF_INCIDENT_OVERLAY.clear()
        bff_main._GOV_BFF_EVOLUTION_PROGRAM_OVERLAY.clear()
        bff_main._GOV_BFF_EXPERIMENT_OVERLAY.clear()
        bff_main._GOV_BFF_JOB_OVERLAY.clear()
        try:
            yield TestClient(bff_main.app), store
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store
            bff_main._GOV_BFF_IDEMPOTENCY.clear()
            bff_main._GOV_BFF_INCIDENT_OVERLAY.clear()
            bff_main._GOV_BFF_EVOLUTION_PROGRAM_OVERLAY.clear()
            bff_main._GOV_BFF_EXPERIMENT_OVERLAY.clear()
            bff_main._GOV_BFF_JOB_OVERLAY.clear()


def _assert_final_command_envelope(payload: dict, command: str) -> str:
    assert payload["status"] == "accepted"
    assert payload["data"]["command"] == command
    assert payload["data"]["status"] == "accepted"
    assert payload["data"]["receipt"]["status"] == "accepted"
    assert payload["data"]["routing_path"] == "direct"
    return payload["data"]["receipt_id"]


# ---------------------------------------------------------------------------
# Evolution Programs
# ---------------------------------------------------------------------------

def test_evolution_programs_list_empty() -> None:
    with _isolated_bff() as (client, _store):
        response = client.get("/bff/evolution-programs", headers=HEADERS)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert "items" in payload
        assert "meta" in payload
        assert "page_info" in payload


def test_evolution_programs_create_and_get() -> None:
    with _isolated_bff() as (client, _store):
        create_response = client.post(
            "/bff/evolution-programs",
            json={"name": "Test Evolution Program", "description": "A test"},
            headers={**HEADERS, "Idempotency-Key": IDEMPOTENCY_KEY},
        )
        assert create_response.status_code == 201, create_response.text
        program = create_response.json()
        assert "program_id" in program
        program_id = program["program_id"]
        assert program["name"] == "Test Evolution Program"
        assert program["status"] == "active"

        detail = client.get(f"/bff/evolution-programs/{program_id}", headers=HEADERS)
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["program_id"] == program_id


def test_evolution_programs_patch() -> None:
    with _isolated_bff() as (client, _store):
        create_response = client.post(
            "/bff/evolution-programs",
            json={"name": "Before Patch"},
            headers={**HEADERS, "Idempotency-Key": IDEMPOTENCY_KEY + "-patch"},
        )
        assert create_response.status_code == 201
        program_id = create_response.json()["program_id"]

        patch_response = client.patch(
            f"/bff/evolution-programs/{program_id}",
            json={"name": "After Patch", "status": "paused"},
            headers=HEADERS,
        )
        assert patch_response.status_code == 200, patch_response.text
        updated = patch_response.json()["data"]
        assert updated["name"] == "After Patch"
        assert updated["status"] == "paused"


def test_evolution_programs_404_on_missing() -> None:
    with _isolated_bff() as (client, _store):
        response = client.get("/bff/evolution-programs/nonexistent-id", headers=HEADERS)
        assert response.status_code == 404


def test_evolution_programs_runs_list() -> None:
    with _isolated_bff() as (client, _store):
        create_response = client.post(
            "/bff/evolution-programs",
            json={"name": "Run Test Program"},
            headers={**HEADERS, "Idempotency-Key": IDEMPOTENCY_KEY + "-runs"},
        )
        assert create_response.status_code == 201
        program_id = create_response.json()["program_id"]

        runs_response = client.get(
            f"/bff/evolution-programs/{program_id}/runs", headers=HEADERS
        )
        assert runs_response.status_code == 200, runs_response.text
        payload = runs_response.json()
        assert "items" in payload
        assert "meta" in payload


def test_evolution_programs_candidates_list() -> None:
    with _isolated_bff() as (client, _store):
        create_response = client.post(
            "/bff/evolution-programs",
            json={"name": "Candidate Test Program"},
            headers={**HEADERS, "Idempotency-Key": IDEMPOTENCY_KEY + "-cands"},
        )
        assert create_response.status_code == 201
        program_id = create_response.json()["program_id"]

        cands_response = client.get(
            f"/bff/evolution-programs/{program_id}/candidates", headers=HEADERS
        )
        assert cands_response.status_code == 200, cands_response.text
        payload = cands_response.json()
        assert "items" in payload


def test_evolution_programs_action() -> None:
    with _isolated_bff() as (client, _store):
        create_response = client.post(
            "/bff/evolution-programs",
            json={"name": "Action Test Program"},
            headers={**HEADERS, "Idempotency-Key": IDEMPOTENCY_KEY + "-action-create"},
        )
        assert create_response.status_code == 201
        program_id = create_response.json()["program_id"]

        action_response = client.post(
            f"/bff/evolution-programs/{program_id}/actions/pause",
            json={"reason": "Testing pause action"},
            headers={**HEADERS, "Idempotency-Key": IDEMPOTENCY_KEY + "-action"},
        )
        assert action_response.status_code == 202, action_response.text
        _assert_final_command_envelope(action_response.json(), "EvolutionProgramAction")


def test_evolution_programs_action_404() -> None:
    with _isolated_bff() as (client, _store):
        response = client.post(
            "/bff/evolution-programs/nonexistent/actions/pause",
            json={"reason": "test"},
            headers={**HEADERS, "Idempotency-Key": IDEMPOTENCY_KEY + "-404-action"},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------

def test_experiments_list() -> None:
    with _isolated_bff() as (client, _store):
        response = client.get("/bff/experiments", headers=HEADERS)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert "items" in payload
        assert "meta" in payload
        assert "page_info" in payload


def test_experiments_create_and_get() -> None:
    with _isolated_bff() as (client, _store):
        create_response = client.post(
            "/bff/experiments",
            json={"name": "Test BFF Experiment", "description": "exp desc"},
            headers={**HEADERS, "Idempotency-Key": IDEMPOTENCY_KEY + "-exp"},
        )
        assert create_response.status_code == 201, create_response.text
        exp = create_response.json()
        assert "experiment_id" in exp
        exp_id = exp["experiment_id"]
        assert exp["name"] == "Test BFF Experiment"
        assert exp["status"] == "queued"

        detail = client.get(f"/bff/experiments/{exp_id}", headers=HEADERS)
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["experiment_id"] == exp_id


def test_experiments_404_on_missing() -> None:
    with _isolated_bff() as (client, _store):
        response = client.get("/bff/experiments/no-such-exp", headers=HEADERS)
        assert response.status_code == 404


def test_experiments_logs() -> None:
    with _isolated_bff() as (client, _store):
        create_response = client.post(
            "/bff/experiments",
            json={"name": "Logs Test Exp"},
            headers={**HEADERS, "Idempotency-Key": IDEMPOTENCY_KEY + "-exp-logs"},
        )
        assert create_response.status_code == 201
        exp_id = create_response.json()["experiment_id"]

        logs_response = client.get(f"/bff/experiments/{exp_id}/logs", headers=HEADERS)
        assert logs_response.status_code == 200, logs_response.text
        payload = logs_response.json()
        assert payload["experiment_id"] == exp_id
        assert "logs" in payload


def test_experiments_metrics() -> None:
    with _isolated_bff() as (client, _store):
        create_response = client.post(
            "/bff/experiments",
            json={"name": "Metrics Test Exp"},
            headers={**HEADERS, "Idempotency-Key": IDEMPOTENCY_KEY + "-exp-metrics"},
        )
        assert create_response.status_code == 201
        exp_id = create_response.json()["experiment_id"]

        metrics_response = client.get(
            f"/bff/experiments/{exp_id}/metrics", headers=HEADERS
        )
        assert metrics_response.status_code == 200, metrics_response.text
        payload = metrics_response.json()
        assert payload["experiment_id"] == exp_id
        assert "metrics" in payload


def test_experiments_artifacts() -> None:
    with _isolated_bff() as (client, _store):
        create_response = client.post(
            "/bff/experiments",
            json={"name": "Artifacts Test Exp"},
            headers={**HEADERS, "Idempotency-Key": IDEMPOTENCY_KEY + "-exp-arts"},
        )
        assert create_response.status_code == 201
        exp_id = create_response.json()["experiment_id"]

        artifacts_response = client.get(
            f"/bff/experiments/{exp_id}/artifacts", headers=HEADERS
        )
        assert artifacts_response.status_code == 200, artifacts_response.text
        payload = artifacts_response.json()
        assert payload["experiment_id"] == exp_id
        assert "artifacts" in payload


def test_experiments_action() -> None:
    with _isolated_bff() as (client, _store):
        create_response = client.post(
            "/bff/experiments",
            json={"name": "Action Test Exp"},
            headers={**HEADERS, "Idempotency-Key": IDEMPOTENCY_KEY + "-exp-create-a"},
        )
        assert create_response.status_code == 201
        exp_id = create_response.json()["experiment_id"]

        action_response = client.post(
            f"/bff/experiments/{exp_id}/actions/cancel",
            json={"reason": "Testing cancel action"},
            headers={**HEADERS, "Idempotency-Key": IDEMPOTENCY_KEY + "-exp-action"},
        )
        assert action_response.status_code == 202, action_response.text
        _assert_final_command_envelope(action_response.json(), "ExperimentAction")


def test_experiments_idempotency_replay() -> None:
    with _isolated_bff() as (client, _store):
        ikey = IDEMPOTENCY_KEY + "-exp-idem"
        body = {"name": "Idempotent Exp"}
        r1 = client.post(
            "/bff/experiments",
            json=body,
            headers={**HEADERS, "Idempotency-Key": ikey},
        )
        assert r1.status_code == 201
        r2 = client.post(
            "/bff/experiments",
            json=body,
            headers={**HEADERS, "Idempotency-Key": ikey},
        )
        assert r2.status_code == 201
        assert r1.json()["experiment_id"] == r2.json()["experiment_id"]


def test_experiments_idempotency_conflict() -> None:
    with _isolated_bff() as (client, _store):
        ikey = IDEMPOTENCY_KEY + "-exp-idem-conflict"
        r1 = client.post(
            "/bff/experiments",
            json={"name": "First"},
            headers={**HEADERS, "Idempotency-Key": ikey},
        )
        assert r1.status_code == 201
        r2 = client.post(
            "/bff/experiments",
            json={"name": "Different"},
            headers={**HEADERS, "Idempotency-Key": ikey},
        )
        assert r2.status_code == 409


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def _seed_job(client: TestClient, job_id: str) -> dict:
    bff_main._GOV_BFF_JOB_OVERLAY[job_id] = {
        "id": job_id,
        "job_id": job_id,
        "status": "running",
        "name": f"job-{job_id}",
        "created_at": "2026-05-08T10:00:00Z",
        "submitted_at": "2026-05-08T10:00:00Z",
        "progress": {"percent": 50},
        "logs": [{"level": "info", "message": "Job started", "ts": "2026-05-08T10:00:01Z"}],
    }
    return bff_main._GOV_BFF_JOB_OVERLAY[job_id]


def test_jobs_list_empty() -> None:
    with _isolated_bff() as (client, _store):
        response = client.get("/bff/jobs", headers=HEADERS)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert "items" in payload
        assert "meta" in payload
        assert "page_info" in payload


def test_jobs_list_with_seed() -> None:
    with _isolated_bff() as (client, _store):
        _seed_job(client, "job-001")
        response = client.get("/bff/jobs", headers=HEADERS)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert any(j["job_id"] == "job-001" for j in payload["items"])


def test_jobs_get_detail() -> None:
    with _isolated_bff() as (client, _store):
        _seed_job(client, "job-002")
        response = client.get("/bff/jobs/job-002", headers=HEADERS)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["job_id"] == "job-002"


def test_jobs_get_404() -> None:
    with _isolated_bff() as (client, _store):
        response = client.get("/bff/jobs/no-such-job", headers=HEADERS)
        assert response.status_code == 404


def test_jobs_get_logs() -> None:
    with _isolated_bff() as (client, _store):
        _seed_job(client, "job-003")
        response = client.get("/bff/jobs/job-003/logs", headers=HEADERS)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["job_id"] == "job-003"
        assert payload["status"] == "running"
        assert payload["logs"][0]["message"] == "Job started"


def test_jobs_get_logs_404() -> None:
    with _isolated_bff() as (client, _store):
        response = client.get("/bff/jobs/no-such/logs", headers=HEADERS)
        assert response.status_code == 404


def test_jobs_action() -> None:
    with _isolated_bff() as (client, _store):
        _seed_job(client, "job-004")
        action_response = client.post(
            "/bff/jobs/job-004/actions/cancel",
            json={"reason": "Test cancel"},
            headers={**HEADERS, "Idempotency-Key": IDEMPOTENCY_KEY + "-job-action"},
        )
        assert action_response.status_code == 202, action_response.text
        _assert_final_command_envelope(action_response.json(), "JobAction")


def test_jobs_action_404() -> None:
    with _isolated_bff() as (client, _store):
        response = client.post(
            "/bff/jobs/no-such/actions/cancel",
            json={"reason": "test"},
            headers={**HEADERS, "Idempotency-Key": IDEMPOTENCY_KEY + "-job-404"},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Events list
# ---------------------------------------------------------------------------

def test_events_list() -> None:
    with _isolated_bff() as (client, _store):
        response = client.get("/bff/events", headers=HEADERS)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert "items" in payload
        assert "meta" in payload
        assert "page_info" in payload


def test_events_list_filter_by_type() -> None:
    with _isolated_bff() as (client, _store):
        response = client.get(
            "/bff/events?event_type=ApproveDeployment", headers=HEADERS
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert "items" in payload


def test_events_list_degraded_when_unavailable() -> None:
    with _isolated_bff() as (client, store):
        store.dataset_source = lambda ds: "missing" if ds == "audit_log" else "local_snapshot"
        response = client.get("/bff/events", headers=HEADERS)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["items"] == []


# ---------------------------------------------------------------------------
# Events stream alias (should still be 200)
# ---------------------------------------------------------------------------

def test_events_stream_non_404() -> None:
    with _isolated_bff() as (_client, _store):
        response = asyncio.run(
            bff_main.bff_events_stream_alias(
                channel="inbox",
                last_event_id=None,
                authorization=OPERATOR_TOKEN,
            )
        )
        assert response.status_code == 200
        assert response.media_type == "text/event-stream"
        assert response.headers["X-SSE-Channel"] == "inbox"
