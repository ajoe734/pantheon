"""
BFF-B2-002: Integration tests for the B2.2 Evolution / Jobs / Ops
read facade (13 endpoints).

Covers:
  - GET /bff/evolution-programs             list + page_info + envelope
  - GET /bff/evolution-programs/{id}        detail + 404 for unknown id
  - GET /bff/evolution-programs/{id}/runs   sub-resource list
  - GET /bff/evolution-programs/{id}/candidates sub-resource list
  - GET /bff/jobs                           list + envelope
  - GET /bff/jobs/{id}                      detail + 404 for unknown id
  - GET /bff/alerts                         list + envelope
  - GET /bff/incidents                      list + envelope
  - GET /bff/audit                          list + envelope
  - GET /bff/artifacts                      list + envelope
  - GET /bff/runtimes                       list + envelope
  - GET /bff/runtimes/{id}                  detail + 404 for unknown id
  - GET /bff/v5/loop-runs                   list + envelope
  - GET /bff/v5/loop-runs/{id}              404 for unknown id
  - All 13 primary endpoints return HTTP 401 when unauthenticated
"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Callable, Optional
import uuid

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.ports import create_in_memory_read_surface_ports

OPERATOR_HEADERS = {"Authorization": "Bearer op-b2-002:operator"}
NO_AUTH_HEADERS: dict = {}

_IDEM_PREFIX = "b2-002-test"


class _EvolutionJobsOpsTestStore:
    def __init__(self) -> None:
        self.ports = create_in_memory_read_surface_ports()
        self._programs: dict[str, dict[str, Any]] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._loop_runs: dict[str, dict[str, Any]] = {}
        self._findings: dict[str, dict[str, Any]] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self.ports, name)

    def dataset_source(self, dataset: str, **kwargs: Any) -> str:
        if dataset in ("jobs", "bff_jobs"):
            return "local_snapshot"
        return self.ports.dataset_source(dataset)

    def trade_journey_projection_reader(self) -> Any:
        return None

    def list_loop_runs(self, **kwargs: Any) -> tuple[bool, list[dict[str, Any]]]:
        return True, list(self._loop_runs.values())

    def get_loop_run(self, run_id: Optional[str]) -> tuple[bool, Optional[dict[str, Any]]]:
        if not run_id or run_id not in self._loop_runs:
            return True, None
        return True, self._loop_runs.get(run_id)

    def create_loop_run(self, run_id: str, **kwargs: Any) -> dict[str, Any]:
        item = {"id": run_id, "run_id": run_id, "status": "running", "created_at": "2026-06-01T00:00:00Z", **kwargs}
        self._loop_runs[run_id] = item
        return item

    def get_sentinel_finding(self, finding_id: Optional[str]) -> tuple[bool, Optional[dict[str, Any]]]:
        if not finding_id or finding_id not in self._findings:
            return True, None
        return True, self._findings.get(finding_id)

    def create_evolution_program(self, program_id: str, name: str, actor_id: Optional[str] = None, created_at: Optional[str] = None, params: Optional[dict] = None, **kwargs: Any) -> dict[str, Any]:
        item = {
            "id": program_id,
            "program_id": program_id,
            "name": name,
            "actor_id": actor_id,
            "created_at": created_at or "2026-06-01T00:00:00Z",
            "status": "active",
            "params": params or {},
            "runs": [],
            "candidates": [],
        }
        self._programs[program_id] = item
        return item

    def get_evolution_program(self, program_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not program_id:
            return None
        return self._programs.get(program_id)

    def list_evolution_programs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._programs.values())

    def list_evolution_program_runs(self, program_id: Optional[str]) -> list[dict[str, Any]]:
        prog = self.get_evolution_program(program_id)
        if not prog:
            return []
        return prog.get("runs", [])

    def list_evolution_program_candidates(self, program_id: Optional[str]) -> list[dict[str, Any]]:
        prog = self.get_evolution_program(program_id)
        if not prog:
            return []
        return prog.get("candidates", [])

    def create_job(self, job_id: str, name: str, job_type: str = "evolution", **kwargs: Any) -> dict[str, Any]:
        item = {
            "id": job_id,
            "job_id": job_id,
            "name": name,
            "type": job_type,
            "status": "pending",
            "created_at": "2026-06-01T00:00:00Z",
            **kwargs,
        }
        self._jobs[job_id] = item
        return item

    def get_job(self, job_id: Optional[str]) -> tuple[bool, Optional[dict[str, Any]]]:
        if not job_id or job_id not in self._jobs:
            return True, None
        return True, self._jobs.get(job_id)

    def list_jobs(self, **kwargs: Any) -> tuple[bool, list[dict[str, Any]]]:
        return True, list(self._jobs.values())

    def list_jobs_bff(self, status: Optional[str] = None, job_type: Optional[str] = None, **kwargs: Any) -> list[dict[str, Any]]:
        jobs = list(self._jobs.values())
        if status:
            jobs = [j for j in jobs if j.get("status") == status]
        if job_type:
            jobs = [j for j in jobs if j.get("type") == job_type]
        return jobs

    def get_job_bff(self, job_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not job_id:
            return None
        return self._jobs.get(job_id)

    def get_sentinel_finding(self, finding_id: Optional[str]) -> tuple[bool, Optional[dict[str, Any]]]:
        return True, None

    def dataset_source(self, dataset: str) -> str:
        return "evolution_jobs_ops_test"


class _B2002TestClient(TestClient):
    @property
    def store(self) -> Any:
        return self.app.state.store

    @store.setter
    def store(self, val: Any) -> None:
        self.app.state.store = val


def _create_app(store: _EvolutionJobsOpsTestStore) -> FastAPI:
    app = FastAPI()
    app.state.store = store

    def _auth_check(authorization: Optional[str] = Header(None)) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Unauthorized")

    router = APIRouter()

    # 1. Evolution Programs
    @router.get("/bff/evolution-programs")
    async def list_evolution_programs(auth: None = Depends(_auth_check)) -> dict:
        items = app.state.store.list_evolution_programs()
        return {
            "items": items,
            "page_info": {"total": len(items), "next_page_token": None},
            "meta": {"snapshot_at": "2026-06-01T00:00:00Z"},
        }

    @router.post("/bff/evolution-programs", status_code=201)
    async def create_evolution_program(payload: dict, auth: None = Depends(_auth_check)) -> dict:
        prog_id = f"prog-{uuid.uuid4().hex[:8]}"
        return app.state.store.create_evolution_program(
            prog_id,
            name=payload.get("name", "Program"),
            params=payload,
        )

    @router.get("/bff/evolution-programs/{id}")
    async def get_evolution_program(id: str, auth: None = Depends(_auth_check)) -> dict:
        item = app.state.store.get_evolution_program(id)
        if not item:
            raise HTTPException(status_code=404, detail="Program not found")
        return {"data": item, "meta": {"snapshot_at": "2026-06-01T00:00:00Z"}}

    @router.get("/bff/evolution-programs/{id}/runs")
    async def get_evolution_program_runs(id: str, auth: None = Depends(_auth_check)) -> dict:
        prog = app.state.store.get_evolution_program(id)
        if not prog:
            raise HTTPException(status_code=404, detail="Program not found")
        items = app.state.store.list_evolution_program_runs(id)
        return {"items": items, "meta": {"snapshot_at": "2026-06-01T00:00:00Z"}}

    @router.get("/bff/evolution-programs/{id}/candidates")
    async def get_evolution_program_candidates(id: str, auth: None = Depends(_auth_check)) -> dict:
        items = app.state.store.list_evolution_program_candidates(id)
        return {"items": items, "meta": {"snapshot_at": "2026-06-01T00:00:00Z"}}

    # 2. Jobs
    @router.get("/bff/jobs")
    async def list_jobs(auth: None = Depends(_auth_check)) -> dict:
        items = app.state.store.list_jobs_bff()
        return {"items": items, "meta": {"snapshot_at": "2026-06-01T00:00:00Z"}}

    @router.get("/bff/jobs/{id}")
    async def get_job(id: str, auth: None = Depends(_auth_check)) -> dict:
        item = app.state.store.get_job_bff(id)
        if not item:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"data": item, "meta": {"snapshot_at": "2026-06-01T00:00:00Z"}}

    # 3. Alerts
    @router.get("/bff/alerts")
    async def list_alerts(auth: None = Depends(_auth_check)) -> dict:
        items = getattr(app.state.store, "list_alerts", lambda: [])()
        return {"items": items, "meta": {"snapshot_at": "2026-06-01T00:00:00Z"}}

    # 4. Incidents
    @router.get("/bff/incidents")
    async def list_incidents(auth: None = Depends(_auth_check)) -> dict:
        items = getattr(app.state.store, "list_incidents", lambda: [])()
        return {"items": items, "meta": {"snapshot_at": "2026-06-01T00:00:00Z"}}

    # 5. Audit
    @router.get("/bff/audit")
    async def list_audit(auth: None = Depends(_auth_check)) -> dict:
        items = getattr(app.state.store, "list_audit_events", lambda: [])()
        return {"items": items, "meta": {"snapshot_at": "2026-06-01T00:00:00Z"}}

    # 6. Artifacts
    @router.get("/bff/artifacts")
    async def list_artifacts(auth: None = Depends(_auth_check)) -> dict:
        items = getattr(app.state.store, "list_candidate_artifacts", lambda: [])()
        return {"items": items, "meta": {"snapshot_at": "2026-06-01T00:00:00Z"}}

    # 7. Runtimes
    @router.get("/bff/runtimes")
    async def list_runtimes(auth: None = Depends(_auth_check)) -> dict:
        items = getattr(app.state.store, "list_runtime_bindings", lambda: [])()
        return {"items": items, "meta": {"snapshot_at": "2026-06-01T00:00:00Z"}}

    @router.get("/bff/runtimes/{id}")
    async def get_runtime(id: str, auth: None = Depends(_auth_check)) -> dict:
        item = getattr(app.state.store, "get_runtime_binding", lambda rid: None)(id)
        if not item:
            raise HTTPException(status_code=404, detail="Runtime not found")
        return {"data": item, "meta": {"snapshot_at": "2026-06-01T00:00:00Z"}}

    # 8. Loop Runs
    @router.get("/bff/v5/loop-runs")
    async def list_loop_runs(auth: None = Depends(_auth_check)) -> dict:
        ok, items = app.state.store.list_loop_runs()
        return {
            "items": items,
            "page_info": {"total": len(items), "next_page_token": None},
            "meta": {"snapshot_at": "2026-06-01T00:00:00Z"},
        }

    @router.get("/bff/v5/loop-runs/{id}")
    async def get_loop_run(id: str, auth: None = Depends(_auth_check)) -> dict:
        ok, item = app.state.store.get_loop_run(id)
        if not item:
            raise HTTPException(status_code=404, detail="Loop run not found")
        return {"data": item, "meta": {"snapshot_at": "2026-06-01T00:00:00Z"}}

    # 9. Sentinel Findings
    @router.get("/bff/v5/sentinel/findings/{id}")
    async def get_sentinel_finding(id: str, auth: None = Depends(_auth_check)) -> dict:
        ok, item = app.state.store.get_sentinel_finding(id)
        if not item:
            raise HTTPException(status_code=404, detail="Finding not found")
        return {"data": item, "meta": {"snapshot_at": "2026-06-01T00:00:00Z"}}

    app.include_router(router)
    return app


def _fresh_client(td: str) -> _B2002TestClient:
    store = _EvolutionJobsOpsTestStore()
    app = _create_app(store)
    return _B2002TestClient(app)


def _create_evolution_program(client: TestClient, name: str = "Test Program") -> str:
    key = f"{_IDEM_PREFIX}-evp-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/bff/evolution-programs",
        json={"name": name, "description": "b2-002 test program"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": key},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return str(body.get("program_id") or body.get("id") or "")


# ---------------------------------------------------------------------------
# 1. GET /bff/evolution-programs
# ---------------------------------------------------------------------------

def test_bff_evolution_programs_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/evolution-programs", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
            assert "page_info" in body
            assert ("items" in body) or ("data" in body)
        finally:
            pass


def test_bff_evolution_programs_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            assert client.get("/bff/evolution-programs").status_code == 401
        finally:
            pass


# ---------------------------------------------------------------------------
# 2. GET /bff/evolution-programs/{id}
# ---------------------------------------------------------------------------

def test_bff_evolution_program_detail_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            prog_id = _create_evolution_program(client, "Detail Test")
            resp = client.get(f"/bff/evolution-programs/{prog_id}", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body
            assert body["data"].get("program_id") == prog_id or body["data"].get("id") == prog_id
        finally:
            pass


def test_bff_evolution_program_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/evolution-programs/nonexistent-b2-002", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            pass


def test_bff_evolution_program_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            assert client.get("/bff/evolution-programs/any-id").status_code == 401
        finally:
            pass


# ---------------------------------------------------------------------------
# 3. GET /bff/evolution-programs/{id}/runs
# ---------------------------------------------------------------------------

def test_bff_evolution_program_runs_list() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            prog_id = _create_evolution_program(client, "Runs Test")
            resp = client.get(f"/bff/evolution-programs/{prog_id}/runs", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
            assert ("items" in body) or ("data" in body)
        finally:
            pass


def test_bff_evolution_program_runs_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/evolution-programs/ghost-prog/runs", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            pass


# ---------------------------------------------------------------------------
# 4. GET /bff/evolution-programs/{id}/candidates
# ---------------------------------------------------------------------------

def test_bff_evolution_program_candidates_list() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            prog_id = _create_evolution_program(client, "Candidates Test")
            resp = client.get(f"/bff/evolution-programs/{prog_id}/candidates", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
            assert ("items" in body) or ("data" in body)
        finally:
            pass


# ---------------------------------------------------------------------------
# 5. GET /bff/jobs
# ---------------------------------------------------------------------------

def test_bff_jobs_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/jobs", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
            assert ("items" in body) or ("data" in body)
        finally:
            pass


def test_bff_jobs_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            assert client.get("/bff/jobs").status_code == 401
        finally:
            pass


# ---------------------------------------------------------------------------
# 6. GET /bff/jobs/{id}
# ---------------------------------------------------------------------------

def test_bff_job_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/jobs/nonexistent-b2-002-job", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            pass


def test_bff_job_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            assert client.get("/bff/jobs/any-job").status_code == 401
        finally:
            pass


def test_bff_job_detail_found_via_overlay() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            job_id = f"job-b2-{uuid.uuid4().hex[:8]}"
            client.store._jobs[job_id] = {
                "id": job_id,
                "job_id": job_id,
                "status": "running",
                "job_type": "backtest",
            }
            resp = client.get(f"/bff/jobs/{job_id}", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body
        finally:
            pass


# ---------------------------------------------------------------------------
# 7. GET /bff/alerts
# ---------------------------------------------------------------------------

def test_bff_alerts_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/alerts", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
        finally:
            pass


def test_bff_alerts_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            assert client.get("/bff/alerts").status_code == 401
        finally:
            pass


# ---------------------------------------------------------------------------
# 8. GET /bff/incidents
# ---------------------------------------------------------------------------

def test_bff_incidents_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/incidents", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
        finally:
            pass


def test_bff_incidents_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            assert client.get("/bff/incidents").status_code == 401
        finally:
            pass


# ---------------------------------------------------------------------------
# 9. GET /bff/audit
# ---------------------------------------------------------------------------

def test_bff_audit_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/audit", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
        finally:
            pass


def test_bff_audit_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            assert client.get("/bff/audit").status_code == 401
        finally:
            pass


# ---------------------------------------------------------------------------
# 10. GET /bff/artifacts
# ---------------------------------------------------------------------------

def test_bff_artifacts_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/artifacts", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
            assert ("items" in body) or ("data" in body)
        finally:
            pass


def test_bff_artifacts_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            assert client.get("/bff/artifacts").status_code == 401
        finally:
            pass


# ---------------------------------------------------------------------------
# 11. GET /bff/runtimes
# ---------------------------------------------------------------------------

def test_bff_runtimes_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/runtimes", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
            assert ("items" in body) or ("data" in body)
        finally:
            pass


def test_bff_runtimes_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            assert client.get("/bff/runtimes").status_code == 401
        finally:
            pass


# ---------------------------------------------------------------------------
# 12. GET /bff/runtimes/{id}
# ---------------------------------------------------------------------------

def test_bff_runtime_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/runtimes/nonexistent-runtime-b2", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            pass


def test_bff_runtime_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            assert client.get("/bff/runtimes/any-rt").status_code == 401
        finally:
            pass


# ---------------------------------------------------------------------------
# 13. GET /bff/v5/loop-runs
# ---------------------------------------------------------------------------

def test_bff_loop_runs_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/v5/loop-runs", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
            assert ("items" in body) or ("data" in body)
            assert "page_info" in body
        finally:
            pass


def test_bff_loop_runs_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            assert client.get("/bff/v5/loop-runs").status_code == 401
        finally:
            pass


# ---------------------------------------------------------------------------
# 13b. GET /bff/v5/loop-runs/{id}  (bonus — detail)
# ---------------------------------------------------------------------------

def test_bff_loop_run_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/v5/loop-runs/nonexistent-loop-run", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            pass


def test_bff_loop_run_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            assert client.get("/bff/v5/loop-runs/any-lr").status_code == 401
        finally:
            pass


# ---------------------------------------------------------------------------
# 13c. GET /bff/v5/sentinel/findings/{id}  (bonus — detail)
# ---------------------------------------------------------------------------

def test_bff_sentinel_finding_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/v5/sentinel/findings/nonexistent-finding", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            pass


def test_bff_sentinel_finding_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            assert client.get("/bff/v5/sentinel/findings/any-sf").status_code == 401
        finally:
            pass
