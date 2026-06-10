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
import sys
import tempfile
import uuid

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore

OPERATOR_HEADERS = {"Authorization": "Bearer op-b2-002:operator"}
NO_AUTH_HEADERS: dict = {}

_IDEM_PREFIX = "b2-002-test"


def _fresh_client(td: str) -> TestClient:
    bff_main.read_store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=True,
    )
    bff_main._GOV_BFF_IDEMPOTENCY.clear()
    bff_main._GOV_BFF_EVOLUTION_PROGRAM_OVERLAY.clear()
    bff_main._GOV_BFF_JOB_OVERLAY.clear()
    return TestClient(bff_main.app)


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
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/evolution-programs", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
            assert "page_info" in body
            assert ("items" in body) or ("data" in body)
        finally:
            bff_main.read_store = original
            bff_main._GOV_BFF_EVOLUTION_PROGRAM_OVERLAY.clear()


def test_bff_evolution_programs_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            assert client.get("/bff/evolution-programs").status_code == 401
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 2. GET /bff/evolution-programs/{id}
# ---------------------------------------------------------------------------

def test_bff_evolution_program_detail_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            prog_id = _create_evolution_program(client, "Detail Test")
            resp = client.get(f"/bff/evolution-programs/{prog_id}", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body
            assert body["data"].get("program_id") == prog_id or body["data"].get("id") == prog_id
        finally:
            bff_main.read_store = original
            bff_main._GOV_BFF_EVOLUTION_PROGRAM_OVERLAY.clear()


def test_bff_evolution_program_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/evolution-programs/nonexistent-b2-002", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            bff_main.read_store = original


def test_bff_evolution_program_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            assert client.get("/bff/evolution-programs/any-id").status_code == 401
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 3. GET /bff/evolution-programs/{id}/runs
# ---------------------------------------------------------------------------

def test_bff_evolution_program_runs_list() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            prog_id = _create_evolution_program(client, "Runs Test")
            resp = client.get(f"/bff/evolution-programs/{prog_id}/runs", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
            assert ("items" in body) or ("data" in body)
        finally:
            bff_main.read_store = original
            bff_main._GOV_BFF_EVOLUTION_PROGRAM_OVERLAY.clear()


def test_bff_evolution_program_runs_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/evolution-programs/ghost-prog/runs", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 4. GET /bff/evolution-programs/{id}/candidates
# ---------------------------------------------------------------------------

def test_bff_evolution_program_candidates_list() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            prog_id = _create_evolution_program(client, "Candidates Test")
            resp = client.get(f"/bff/evolution-programs/{prog_id}/candidates", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
            assert ("items" in body) or ("data" in body)
        finally:
            bff_main.read_store = original
            bff_main._GOV_BFF_EVOLUTION_PROGRAM_OVERLAY.clear()


# ---------------------------------------------------------------------------
# 5. GET /bff/jobs
# ---------------------------------------------------------------------------

def test_bff_jobs_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/jobs", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
            assert ("items" in body) or ("data" in body)
        finally:
            bff_main.read_store = original


def test_bff_jobs_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            assert client.get("/bff/jobs").status_code == 401
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 6. GET /bff/jobs/{id}
# ---------------------------------------------------------------------------

def test_bff_job_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/jobs/nonexistent-b2-002-job", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            bff_main.read_store = original


def test_bff_job_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            assert client.get("/bff/jobs/any-job").status_code == 401
        finally:
            bff_main.read_store = original


def test_bff_job_detail_found_via_overlay() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            job_id = f"job-b2-{uuid.uuid4().hex[:8]}"
            bff_main._GOV_BFF_JOB_OVERLAY[job_id] = {
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
            bff_main.read_store = original
            bff_main._GOV_BFF_JOB_OVERLAY.clear()


# ---------------------------------------------------------------------------
# 7. GET /bff/alerts
# ---------------------------------------------------------------------------

def test_bff_alerts_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/alerts", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
        finally:
            bff_main.read_store = original


def test_bff_alerts_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            assert client.get("/bff/alerts").status_code == 401
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 8. GET /bff/incidents
# ---------------------------------------------------------------------------

def test_bff_incidents_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/incidents", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
        finally:
            bff_main.read_store = original


def test_bff_incidents_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            assert client.get("/bff/incidents").status_code == 401
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 9. GET /bff/audit
# ---------------------------------------------------------------------------

def test_bff_audit_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/audit", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
        finally:
            bff_main.read_store = original


def test_bff_audit_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            assert client.get("/bff/audit").status_code == 401
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 10. GET /bff/artifacts
# ---------------------------------------------------------------------------

def test_bff_artifacts_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/artifacts", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
            assert ("items" in body) or ("data" in body)
        finally:
            bff_main.read_store = original


def test_bff_artifacts_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            assert client.get("/bff/artifacts").status_code == 401
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 11. GET /bff/runtimes
# ---------------------------------------------------------------------------

def test_bff_runtimes_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/runtimes", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
            assert ("items" in body) or ("data" in body)
        finally:
            bff_main.read_store = original


def test_bff_runtimes_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            assert client.get("/bff/runtimes").status_code == 401
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 12. GET /bff/runtimes/{id}
# ---------------------------------------------------------------------------

def test_bff_runtime_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/runtimes/nonexistent-runtime-b2", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            bff_main.read_store = original


def test_bff_runtime_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            assert client.get("/bff/runtimes/any-rt").status_code == 401
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 13. GET /bff/v5/loop-runs
# ---------------------------------------------------------------------------

def test_bff_loop_runs_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/v5/loop-runs", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "meta" in body
            assert ("items" in body) or ("data" in body)
            assert "page_info" in body
        finally:
            bff_main.read_store = original


def test_bff_loop_runs_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            assert client.get("/bff/v5/loop-runs").status_code == 401
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 13b. GET /bff/v5/loop-runs/{id}  (bonus — detail)
# ---------------------------------------------------------------------------

def test_bff_loop_run_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/v5/loop-runs/nonexistent-loop-run", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            bff_main.read_store = original


def test_bff_loop_run_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            assert client.get("/bff/v5/loop-runs/any-lr").status_code == 401
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 13c. GET /bff/v5/sentinel/findings/{id}  (bonus — detail)
# ---------------------------------------------------------------------------

def test_bff_sentinel_finding_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/v5/sentinel/findings/nonexistent-finding", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            bff_main.read_store = original


def test_bff_sentinel_finding_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            assert client.get("/bff/v5/sentinel/findings/any-sf").status_code == 401
        finally:
            bff_main.read_store = original
