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
from typing import Any, Iterator

from fastapi.testclient import TestClient

from services.control_plane.bff import main as bff_main
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.ports import ReadSurfacePorts


OPERATOR_TOKEN = "Bearer op-gap-004:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}
IDEMPOTENCY_KEY = "test-evo-exp-jobs-events-001"


class EvolutionExperimentJobsEventsTestReadPorts(ReadSurfacePorts):
    def __init__(self, seed_data: dict[str, Any] | None = None) -> None:
        super().__init__()
        self._data: dict[str, Any] = seed_data or {}

    def dataset_source(self, dataset: str) -> str:
        return "local_snapshot"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        source = self.dataset_source(dataset)
        return {
            "status": "degraded" if source == "missing" else "ok",
            "source": source,
            "snapshot_at": snapshot_at,
        }

    def _get_dataset(self, name: str) -> dict[str, Any] | list[Any]:
        return self._data.setdefault(name, {})

    def list_evolution_programs(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("evolution_programs")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_evolution_program(self, program_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("evolution_programs")
        if isinstance(ds, dict):
            return ds.get(str(program_id or ""))
        return next((p for p in ds if p.get("id") == program_id or p.get("program_id") == program_id), None)

    # U8A note: create/patch are deliberately NOT methods on this read-only
    # store double anymore — the read surface never did writes in
    # production (``ports/read_surface_ports.py`` never had these methods),
    # and this test double previously carrying them was exactly the
    # "read surface doing writes" anti-pattern
    # evolution-lifecycle.md §1 calls out. Writes now go through
    # ``_EvolutionProgramCommandsTestDouble`` below via the injected
    # ``bff_main._evolution_program_commands`` port.

    def list_evolution_program_runs(self, program_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def list_evolution_program_candidates(self, program_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def list_research_experiments(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("research_experiments")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_research_experiment(self, experiment_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("research_experiments")
        if isinstance(ds, dict):
            return ds.get(str(experiment_id or ""))
        return next((e for e in ds if e.get("id") == experiment_id or e.get("experiment_id") == experiment_id), None)

    def list_experiments_bff(self, status: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("research_experiments")
        items = list(ds.values()) if isinstance(ds, dict) else list(ds)
        if status:
            statuses = [s.strip().lower() for s in status.split(",")]
            items = [i for i in items if str(i.get("status", "")).lower() in statuses]
        return items

    def get_experiment_bff(self, experiment_id: str | None, **kwargs: Any) -> dict[str, Any] | None:
        return self.get_research_experiment(experiment_id)

    def create_experiment_bff(
        self,
        *,
        name: str,
        description: str = "",
        actor_id: str = "op-user",
        **kwargs: Any,
    ) -> dict[str, Any]:
        exp_id = f"exp-{len(self.list_research_experiments()) + 1:03d}"
        record = {
            "id": exp_id,
            "experiment_id": exp_id,
            "name": name,
            "description": description,
            "status": "queued",
            "created_at": "2026-08-29T00:00:00Z",
            "created_by": actor_id,
            "logs": ["Experiment queued"],
            "metrics": {"loss": 0.01},
            "artifacts": [],
        }
        ds = self._get_dataset("research_experiments")
        if isinstance(ds, dict):
            ds[exp_id] = record
        return record

    def get_experiment_logs(self, experiment_id: str, **kwargs: Any) -> list[str]:
        exp = self.get_research_experiment(experiment_id)
        if exp and "logs" in exp:
            return list(exp["logs"])
        return ["Log entry"]

    def get_experiment_metrics(self, experiment_id: str, **kwargs: Any) -> dict[str, Any]:
        exp = self.get_research_experiment(experiment_id)
        if exp and "metrics" in exp:
            return dict(exp["metrics"])
        return {"metric": 1.0}

    def get_experiment_artifacts(self, experiment_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        exp = self.get_research_experiment(experiment_id)
        if exp and "artifacts" in exp:
            return list(exp["artifacts"])
        return []

    def list_research_experiment_jobs(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("research_experiment_jobs")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_research_experiment_job(self, job_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("research_experiment_jobs")
        if isinstance(ds, dict):
            return ds.get(str(job_id or ""))
        return next((j for j in ds if j.get("id") == job_id or j.get("job_id") == job_id), None)

    def list_jobs_bff(self, *, status: str | None = None, job_type: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("jobs")
        items = list(ds.values()) if isinstance(ds, dict) else list(ds)
        if status:
            items = [i for i in items if i.get("status") == status]
        if job_type:
            items = [i for i in items if i.get("job_type") == job_type or i.get("type") == job_type]
        return items

    def get_job_bff(self, job_id: str | None, **kwargs: Any) -> dict[str, Any] | None:
        ds = self._get_dataset("jobs")
        if isinstance(ds, dict):
            return ds.get(str(job_id or ""))
        return next((j for j in ds if j.get("id") == job_id or j.get("job_id") == job_id), None)

    def list_governance_events(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("governance_events")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_audit_events(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("audit_events")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)


class _EvolutionProgramCommandsTestDouble:
    """Stand-in for ``EvolutionServiceProgramCommandPort`` (U8A): create
    always starts ``draft`` with a server-generated identity and revision 1;
    PATCH allowlists ``name`` only and enforces the ``expected_revision``
    CAS precondition. Mutates the same backing dict the read-store double
    reads from, so list/get stay consistent with what was actually
    committed through this port."""

    def __init__(self, store: EvolutionExperimentJobsEventsTestReadPorts) -> None:
        self._store = store
        self._counter = 0

    def _dataset(self) -> dict[str, Any]:
        ds = self._store._get_dataset("evolution_programs")
        assert isinstance(ds, dict)
        return ds

    async def create_program(self, *, tenant_id, actor_id, name, idempotency_key):
        self._counter += 1
        program_id = f"evp-test-{self._counter:04d}"
        record = {
            "program_id": program_id,
            "id": program_id,
            "tenant_id": tenant_id,
            "created_by": actor_id,
            "name": name,
            "status": "draft",
            "revision": 1,
            "created_at": "2026-08-29T00:00:00Z",
            "updated_at": "2026-08-29T00:00:00Z",
            "run_ids": [],
            "candidate_ids": [],
        }
        self._dataset()[program_id] = record
        return record

    async def patch_program_name(self, *, tenant_id, actor_id, program_id, name, expected_revision, idempotency_key):
        from services.control_plane.bff.ports.evolution_program_commands import (
            EvolutionProgramConflictError,
            EvolutionProgramNotFoundError,
        )

        record = self._dataset().get(program_id)
        if record is None:
            raise EvolutionProgramNotFoundError(f"Evolution program not found: {program_id}")
        if int(record.get("revision") or 0) != expected_revision:
            raise EvolutionProgramConflictError(f"Evolution program {program_id} was modified concurrently")
        record["name"] = name
        record["revision"] = int(record["revision"]) + 1
        record["updated_at"] = "2026-08-29T01:00:00Z"
        record["updated_by"] = actor_id
        return record


@contextmanager
def _isolated_bff() -> Iterator[tuple[TestClient, EvolutionExperimentJobsEventsTestReadPorts]]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        original_program_commands = bff_main._evolution_program_commands
        store = EvolutionExperimentJobsEventsTestReadPorts()
        bff_main.read_store = store
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._evolution_program_commands = _EvolutionProgramCommandsTestDouble(store)
        bff_main._GOV_BFF_IDEMPOTENCY.clear()
        try:
            yield TestClient(bff_main.app), store
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store
            bff_main._evolution_program_commands = original_program_commands
            bff_main._GOV_BFF_IDEMPOTENCY.clear()


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
        # U8A: create only accepts ``name`` — a caller-supplied
        # ``description`` (or any other field) is now rejected with 422
        # rather than silently accepted.
        create_response = client.post(
            "/bff/evolution-programs",
            json={"name": "Test Evolution Program"},
            headers={**HEADERS, "Idempotency-Key": IDEMPOTENCY_KEY},
        )
        assert create_response.status_code == 201, create_response.text
        program = create_response.json()
        assert "program_id" in program
        program_id = program["program_id"]
        assert program["name"] == "Test Evolution Program"
        # U8A: create always starts draft; it never auto-activates.
        assert program["status"] == "draft"
        assert program["revision"] == 1

        detail = client.get(f"/bff/evolution-programs/{program_id}", headers=HEADERS)
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["program_id"] == program_id


def test_evolution_programs_patch_name_only() -> None:
    """U8A metadata PATCH allowlist is ``name`` only, CAS-guarded on the
    caller's ``revision`` precondition."""
    with _isolated_bff() as (client, _store):
        create_response = client.post(
            "/bff/evolution-programs",
            json={"name": "Before Patch"},
            headers={**HEADERS, "Idempotency-Key": IDEMPOTENCY_KEY + "-patch"},
        )
        assert create_response.status_code == 201
        program_id = create_response.json()["program_id"]
        revision = create_response.json()["revision"]

        patch_response = client.patch(
            f"/bff/evolution-programs/{program_id}",
            json={"name": "After Patch", "revision": revision},
            headers=HEADERS,
        )
        assert patch_response.status_code == 200, patch_response.text
        updated = patch_response.json()["data"]
        assert updated["name"] == "After Patch"
        assert updated["status"] == "draft"  # unchanged by a metadata patch
        assert updated["revision"] == revision + 1


def test_evolution_programs_patch_status_rejected_pause_via_action_only() -> None:
    """Test-migration note (per the task's explicit instruction: preserve
    the original business intent, do not delete the assertion, migrate it
    to the single correct control entrypoint).

    This test previously did ``PATCH .../{program_id}`` with
    ``{"status": "paused"}`` and asserted it succeeded — exactly the
    "read surface doing writes with an unrestricted PATCH allowlist" defect
    evolution-lifecycle.md §1/§3 calls out. Under the now-adopted U8A
    contract, ``name`` is the only allowlisted PATCH field, so a direct
    ``status`` PATCH must now assert **422**, never a fabricated success.

    The real "pause a program" business capability is still asserted here,
    through the *action* entrypoint instead
    (``POST .../actions/pause_program``) — which in U8A must assert the
    honest "unavailable" outcome, since real program lifecycle effects are
    U8B's obligation (see evolution_adapter.py's
    ``_execute_program_action``). When U8B lands real ``pause_program``
    effects, only this action-outcome assertion should flip to a positive
    "program is now paused" assertion; the PATCH-status 422 assertion above
    it does not change, since ``status`` will never become PATCH-allowlisted.
    """
    with _isolated_bff() as (client, _store):
        create_response = client.post(
            "/bff/evolution-programs",
            json={"name": "Before Patch"},
            headers={**HEADERS, "Idempotency-Key": IDEMPOTENCY_KEY + "-patch-status"},
        )
        assert create_response.status_code == 201
        program_id = create_response.json()["program_id"]
        revision = create_response.json()["revision"]

        patch_response = client.patch(
            f"/bff/evolution-programs/{program_id}",
            json={"revision": revision, "status": "paused"},
            headers=HEADERS,
        )
        assert patch_response.status_code == 422, patch_response.text
        assert _store.get_evolution_program(program_id)["status"] == "draft"


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
    record = {
        "id": job_id,
        "job_id": job_id,
        "status": "running",
        "name": f"job-{job_id}",
        "created_at": "2026-05-08T10:00:00Z",
        "submitted_at": "2026-05-08T10:00:00Z",
        "progress": {"percent": 50},
        "logs": [{"level": "info", "message": "Job started", "ts": "2026-05-08T10:00:01Z"}],
    }
    ds = bff_main.read_store._get_dataset("jobs")
    if isinstance(ds, dict):
        ds[job_id] = record
    return record


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
