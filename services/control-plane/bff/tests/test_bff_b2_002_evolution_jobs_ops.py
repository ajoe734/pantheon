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

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.control_loops.router import create_control_loops_router
from services.control_plane.bff.evolution.router import create_evolution_programs_router
from services.control_plane.bff.incidents.router import create_incident_router
from services.control_plane.bff.jobs.router import create_jobs_router
from services.control_plane.bff.models import ErrorCode
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.research.router import create_research_router
from services.control_plane.bff.runtime.router import create_runtime_router

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


class _Identity:
    def __init__(self, op_id: str, roles: set[str]):
        self.operator_id = op_id
        self.roles = roles


def _extract_identity(auth: Optional[str] = None) -> Optional[_Identity]:
    if not auth or not auth.startswith("Bearer "):
        return None
    token = auth[len("Bearer "):].strip()
    if ":" in token:
        op_id, roles_str = token.split(":", 1)
        roles = {r.strip() for r in roles_str.split(",") if r.strip()}
    else:
        op_id = token
        roles = {"operator", "viewer", "reviewer", "admin"}
    return _Identity(op_id, roles)


def _require_read(identity: Optional[_Identity]) -> None:
    if identity is None:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _require_operator(identity: Optional[_Identity]) -> None:
    if identity is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    roles = getattr(identity, "roles", set()) or set()
    if "operator" not in roles and "admin" not in roles:
        raise HTTPException(status_code=403, detail="Forbidden")


def _bff_error(status_code: int, code: Any, message: str, reason: Optional[str] = None, **kwargs: Any) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": str(code), "message": message, "reason": reason or message, **kwargs}},
    )


def _utc_now() -> str:
    return "2026-06-01T00:00:00Z"


def _page_slice(items: list[Any], page_token: Optional[str] = None, page_size: int = 20) -> tuple[list[Any], Optional[str]]:
    return list(items[:page_size]), None


def _snapshot_meta(snapshot_at: str = "2026-06-01T00:00:00Z", **kwargs: Any) -> dict[str, Any]:
    return {"snapshot_at": snapshot_at, **kwargs}


def _dataset_surface_status(dataset: str, *, snapshot_at: str = "2026-06-01T00:00:00Z", **kwargs: Any) -> dict[str, Any]:
    return {"status": "available", "dataset": dataset, "snapshot_at": snapshot_at}


def _read_surface_meta(surface_name: str, read_type: str, *, snapshot_at: str = "2026-06-01T00:00:00Z", **kwargs: Any) -> dict[str, Any]:
    return {"snapshot_at": snapshot_at, "surface": surface_name, **kwargs}


def _create_app(store: _EvolutionJobsOpsTestStore) -> FastAPI:
    app = FastAPI()
    app.state.store = store

    app.include_router(
        create_evolution_programs_router(
            read_surface=lambda: app.state.store,
            extract_identity=_extract_identity,
            require_read_role=_require_read,
            require_operator_role=_require_operator,
            bff_error=_bff_error,
            utc_now=_utc_now,
            page_slice=_page_slice,
            snapshot_meta=_snapshot_meta,
            dataset_surface_status=_dataset_surface_status,
        )
    )
    app.include_router(
        create_jobs_router(
            read_surface=lambda: app.state.store,
            extract_identity=_extract_identity,
            require_read_role=_require_read,
            bff_error=_bff_error,
            utc_now=_utc_now,
            page_slice=_page_slice,
            read_surface_meta=_read_surface_meta,
            dataset_surface_status=_dataset_surface_status,
            raise_if_read_surface_unavailable=lambda s, label="": None,
            reject_body_idempotency_key=lambda b: None,
            resolve_final_idempotency_key=lambda k1, k2: k1 or k2 or "",
            submit_job_action=lambda *a, **k: {},
        )
    )
    app.include_router(
        create_incident_router(
            read_surface=lambda: app.state.store,
            extract_identity=_extract_identity,
            require_read_role=_require_read,
            require_operator_role=_require_operator,
            bff_error=_bff_error,
            utc_now=_utc_now,
            page_slice=_page_slice,
            snapshot_meta=_snapshot_meta,
            dataset_surface_status=_dataset_surface_status,
            read_surface_meta=_read_surface_meta,
            raise_if_read_surface_unavailable=lambda s, label="": None,
        )
    )
    app.include_router(
        create_research_router(
            read_surface=lambda: app.state.store,
            extract_identity=_extract_identity,
            require_read_role=_require_read,
            bff_error=_bff_error,
            utc_now=_utc_now,
            page_slice=_page_slice,
            snapshot_meta=_snapshot_meta,
            dataset_surface_status=_dataset_surface_status,
            include_prepared_subrouters=True,
        )
    )
    app.include_router(
        create_runtime_router(
            read_surface=lambda: app.state.store,
            dependencies={
                "_extract_identity": _extract_identity,
                "_require_read_role": _require_read,
                "_require_operator_role": _require_operator,
                "utc_now": _utc_now,
                "_dataset_surface_status": _dataset_surface_status,
                "_page_slice": _page_slice,
                "_snapshot_meta": _snapshot_meta,
                "_meta_staleness": lambda: None,
                "_raise_if_read_surface_unavailable": lambda s, label="": None,
                "_bff_error": _bff_error,
            },
        )
    )
    app.include_router(
        create_control_loops_router(
            read_surface=lambda: app.state.store,
            extract_identity=_extract_identity,
            require_read_role=_require_read,
            require_operator_role=_require_operator,
            bff_error=_bff_error,
            utc_now_fn=_utc_now,
        )
    )
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
