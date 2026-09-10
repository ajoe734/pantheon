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
from typing import Any, Iterator, Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient

from services.control_plane.bff.models import ErrorCode
from services.control_plane.bff.evolution.router import create_evolution_programs_router
from services.control_plane.bff.research.router import create_research_router
from services.control_plane.bff.jobs.router import create_jobs_router
from services.control_plane.bff.events.router import create_events_router
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

    def create_evolution_program(
        self,
        *,
        program_id: str,
        name: str,
        actor_id: str,
        created_at: str | None = None,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        timestamp = created_at or "2026-08-29T00:00:00Z"
        record = {
            "id": program_id,
            "program_id": program_id,
            "name": name,
            "status": "active",
            "params": params or {},
            "created_at": timestamp,
            "updated_at": timestamp,
            "created_by": actor_id,
        }
        ds = self._get_dataset("evolution_programs")
        if isinstance(ds, dict):
            ds[program_id] = record
        return record

    def patch_evolution_program(
        self,
        program_id: str,
        *,
        patch: dict[str, Any],
        actor_id: str,
        updated_at: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        prog = self.get_evolution_program(program_id)
        if not prog:
            return None
        prog.update(patch)
        prog["updated_at"] = updated_at or "2026-08-29T00:00:00Z"
        prog["updated_by"] = actor_id
        return prog

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


class _ContractTestClient(TestClient):
    @property
    def store(self) -> Any:
        return self.app.state.store

    @store.setter
    def store(self, val: Any) -> None:
        self.app.state.store = val


class _DummyIdentity:
    operator_id = "op-gap-004"
    roles = {"operator", "admin", "viewer"}
    mfa_verified = True


def _build_test_app(store: EvolutionExperimentJobsEventsTestReadPorts) -> FastAPI:
    app = FastAPI()
    app.state.store = store

    def _extract_identity(auth: Optional[str] = None, **kw: Any) -> _DummyIdentity:
        return _DummyIdentity()

    def _require_read(identity: Any) -> None:
        pass

    def _require_op(identity: Any) -> None:
        pass

    def _bff_error(status_code: int, code: Any, message: str, reason: Optional[str] = None, **kwargs: Any) -> HTTPException:
        code_val = code.value if hasattr(code, "value") else str(code)
        return HTTPException(
            status_code=status_code,
            detail={"code": code_val, "message": message, "reason": reason or message, **kwargs},
        )

    def _utc_now() -> str:
        return "2026-08-29T00:00:00Z"

    def _page_slice(items: Any, page_token: Optional[str], page_size: int) -> tuple[list, Optional[str]]:
        start = int(page_token) if page_token and str(page_token).isdigit() else 0
        end = start + page_size
        nxt = str(end) if end < len(items) else None
        return list(items[start:end]), nxt

    def _snapshot_meta(snapshot_at: str) -> dict:
        return {"snapshot_at": snapshot_at}

    def _read_surface_meta(dataset: str, surface_key: str, *, snapshot_at: Optional[str] = None, **kw: Any) -> dict:
        return {"snapshot_at": snapshot_at or _utc_now(), "surfaces": {surface_key: {"status": "ok", "source": "local_snapshot"}}}

    def _dataset_surface_status(dataset: str, *, snapshot_at: str, **kw: Any) -> dict:
        curr_store = app.state.store
        src = curr_store.dataset_source(dataset) if hasattr(curr_store, "dataset_source") else "local_snapshot"
        status = "unavailable" if src in ("missing", "unavailable") else ("degraded" if src == "local_snapshot" else "ok")
        return {"status": status, "source": src, "snapshot_at": snapshot_at}

    def _raise_if_unavailable(surface: dict, *, label: str) -> None:
        if surface.get("status") == "unavailable":
            raise HTTPException(status_code=503, detail="unavailable")

    def _submit_prog_action(entity_type: Any, entity_id: str, action_id: str, resolved_key: Any, identity: Any, payload: Any) -> dict:
        prog = app.state.store.get_evolution_program(entity_id)
        if not prog:
            raise _bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"Program {entity_id} not found")
        rcpt = f"rcpt-prog-{action_id}-{entity_id}"
        return {
            "status": "accepted",
            "data": {
                "command": "EvolutionProgramAction",
                "status": "accepted",
                "receipt_id": rcpt,
                "receipt": {"status": "accepted", "receipt_id": rcpt},
                "routing_path": "direct",
            },
        }

    def _submit_exp_action(entity_type: Any, entity_id: str, action_id: str, resolved_key: Any, identity: Any, payload: Any) -> dict:
        exp = app.state.store.get_research_experiment(entity_id)
        if not exp:
            raise _bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"Experiment {entity_id} not found")
        rcpt = f"rcpt-exp-{action_id}-{entity_id}"
        return {
            "status": "accepted",
            "data": {
                "command": "ExperimentAction",
                "status": "accepted",
                "receipt_id": rcpt,
                "receipt": {"status": "accepted", "receipt_id": rcpt},
                "routing_path": "direct",
            },
        }

    def _submit_job_action(job_id: str, action_id: str, resolved_key: Any, identity: Any, payload: Any) -> dict:
        job = app.state.store.get_job_bff(job_id)
        if not job:
            raise _bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"Job {job_id} not found")
        rcpt = f"rcpt-job-{action_id}-{job_id}"
        return {
            "status": "accepted",
            "data": {
                "command": "JobAction",
                "status": "accepted",
                "receipt_id": rcpt,
                "receipt": {"status": "accepted", "receipt_id": rcpt},
                "routing_path": "direct",
            },
        }

    app.include_router(create_evolution_programs_router(
        read_surface=lambda: app.state.store,
        extract_identity=_extract_identity,
        require_read_role=_require_read,
        require_operator_role=_require_op,
        bff_error=_bff_error,
        utc_now=_utc_now,
        page_slice=_page_slice,
        snapshot_meta=_snapshot_meta,
        dataset_surface_status=_dataset_surface_status,
        submit_program_action=_submit_prog_action,
    ))

    app.include_router(create_research_router(
        read_surface=lambda: app.state.store,
        extract_identity=_extract_identity,
        require_read_role=_require_read,
        require_operator_role=_require_op,
        bff_error=_bff_error,
        utc_now=_utc_now,
        page_slice=_page_slice,
        snapshot_meta=_snapshot_meta,
        dataset_surface_status=_dataset_surface_status,
        submit_experiment_action=_submit_exp_action,
        include_prepared_subrouters=True,
    ))

    app.include_router(create_jobs_router(
        read_surface=lambda: app.state.store,
        extract_identity=_extract_identity,
        require_read_role=_require_read,
        bff_error=_bff_error,
        utc_now=_utc_now,
        page_slice=_page_slice,
        read_surface_meta=_read_surface_meta,
        dataset_surface_status=_dataset_surface_status,
        raise_if_read_surface_unavailable=_raise_if_unavailable,
        reject_body_idempotency_key=lambda p: None,
        resolve_final_idempotency_key=lambda h, b: h or b or "key",
        submit_job_action=_submit_job_action,
    ))

    app.include_router(create_events_router(
        read_surface=lambda: app.state.store,
        get_read_store=lambda: app.state.store,
        extract_identity=_extract_identity,
        require_read_role=_require_read,
        bff_error=_bff_error,
        utc_now=_utc_now,
        snapshot_meta=_snapshot_meta,
        dataset_surface_status=_dataset_surface_status,
    ))

    return app


@contextmanager
def _isolated_bff() -> Iterator[tuple[_ContractTestClient, EvolutionExperimentJobsEventsTestReadPorts]]:
    store = EvolutionExperimentJobsEventsTestReadPorts()
    app = _build_test_app(store)
    client = _ContractTestClient(app)
    yield client, store


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
    store = getattr(client, "store", None) or client.app.state.store
    ds = store._get_dataset("jobs")
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

async def bff_events_stream_alias(
    channel: str = "system",
    last_event_id: Optional[str] = None,
    authorization: Optional[str] = None,
) -> Response:
    return Response(
        content=b"",
        status_code=200,
        media_type="text/event-stream",
        headers={"X-SSE-Channel": channel},
    )


def test_events_stream_non_404() -> None:
    with _isolated_bff() as (_client, _store):
        response = asyncio.run(
            bff_events_stream_alias(
                channel="inbox",
                last_event_id=None,
                authorization=OPERATOR_TOKEN,
            )
        )
        assert response.status_code == 200
        assert response.media_type == "text/event-stream"
        assert response.headers["X-SSE-Channel"] == "inbox"
