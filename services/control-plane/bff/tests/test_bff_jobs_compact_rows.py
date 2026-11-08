"""Regression tests for BFF-JOBS-LIST-COMPACT-ROWS-20261106."""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.jobs.router import create_jobs_router


def _jobs(n: int = 25):
    return [
        {
            "job_id": f"j{i:03d}", "id": f"j{i:03d}", "source": "source_ingestion",
            "job_type": "source_ingestion", "status": "running",
            "created_at": f"2026-11-06T00:{i:02d}:00Z", "progress": 0.5,
            "detail": {"blob": "x" * 17000, "created_at": f"2026-11-06T00:{i:02d}:00Z"},
            "allowedActions": {},
        }
        for i in range(n)
    ]


class _Store:
    def __init__(self):
        self.jobs = _jobs()

    def list_jobs_bff(self, status=None, job_type=None, diagnostics=None):
        if diagnostics is not None:
            diagnostics["healthy_sources"] = ["source_ingestion"]
        return list(self.jobs)

    def get_job_bff(self, job_id):
        return next((j for j in self.jobs if j["job_id"] == job_id), None)


def _client():
    store = _Store()

    def page_slice(items, token, size):
        start = int(token or 0)
        end = start + size
        return items[start:end], (str(end) if end < len(items) else None)

    router = create_jobs_router(
        read_surface=store,
        extract_identity=lambda a: None,
        require_read_role=lambda i: None,
        bff_error=lambda *a, **k: RuntimeError(a),
        utc_now=lambda: "2026-11-06T00:00:00Z",
        page_slice=page_slice,
        read_surface_meta=lambda *a, **k: {},
        dataset_surface_status=lambda *a, **k: {},
        raise_if_read_surface_unavailable=lambda *a, **k: None,
        reject_body_idempotency_key=lambda b: None,
        resolve_final_idempotency_key=lambda a: "k",
        submit_job_action=lambda *a: {},
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), store


def test_list_rows_have_no_detail_and_are_small():
    client, _ = _client()
    resp = client.get("/bff/jobs", params={"page_size": 20})
    body = resp.json()
    assert len(body["items"]) == 20
    assert all("detail" not in r for r in body["items"])
    assert len(resp.content) <= 100 * 1024
    assert body["items"] == body["data"]
    assert body["page_info"]["total"] == 25
    assert [r["job_id"] for r in body["items"]][:2] == ["j024", "j023"]
    for key in ("job_id", "id", "source", "job_type", "status", "created_at", "progress", "allowedActions"):
        assert key in body["items"][0]


def test_detail_endpoint_still_returns_detail():
    client, store = _client()
    data = client.get("/bff/jobs/j003").json()["data"]
    assert data["detail"] == store.jobs[3]["detail"]


def test_paging_walk_unchanged():
    client, _ = _client()
    seen, token = [], None
    while True:
        params = {"page_size": 10, **({"page_token": token} if token else {})}
        b = client.get("/bff/jobs", params=params).json()
        seen += [r["job_id"] for r in b["items"]]
        token = b["page_info"]["next_page_token"]
        if not token:
            break
    assert len(seen) == len(set(seen)) == 25
