"""
EXP-002 contract tests: GET /bff/research-experiments (list) and
GET /bff/research-experiments/{id} (detail).

Covers:
- list shape: items, page_info, meta.surfaces.research_experiments
- list fallback snapshot data present (>= 3 seeded experiments)
- detail shape: data envelope, meta.surfaces.research_experiment_detail
- detail status-specific fields (completed, running, failed)
- analysis_links injected in detail (BFF overlay)
- 404 for unknown experiment_id
- auth gate: 401 when no credentials
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore

OPERATOR_AUTH = "Bearer exp002-op:operator"
HEADERS = {"Authorization": OPERATOR_AUTH}

# IDs from the read_store fallback snapshot (read_store.py lines ~4296-4372)
COMPLETED_EXP_ID = "exp-20260419-012"
RUNNING_EXP_ID = "exp-20260418-009"
FAILED_EXP_ID = "exp-20260417-004"
UNKNOWN_EXP_ID = "exp-does-not-exist-exp002"


@contextmanager
def _bff_client(*, fallback: bool = True) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_experiment_overlay = dict(bff_main._GOV_BFF_EXPERIMENT_OVERLAY)
        original_idempotency = dict(bff_main._GOV_BFF_IDEMPOTENCY)
        try:
            bff_main.read_store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=fallback,
            )
            bff_main._GOV_BFF_EXPERIMENT_OVERLAY.clear()
            bff_main._GOV_BFF_IDEMPOTENCY.clear()
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
            bff_main._GOV_BFF_EXPERIMENT_OVERLAY.clear()
            bff_main._GOV_BFF_EXPERIMENT_OVERLAY.update(original_experiment_overlay)
            bff_main._GOV_BFF_IDEMPOTENCY.clear()
            bff_main._GOV_BFF_IDEMPOTENCY.update(original_idempotency)


# ---------------------------------------------------------------------------
# GET /bff/research-experiments  — list
# ---------------------------------------------------------------------------

def test_exp002_list_returns_200() -> None:
    with _bff_client() as client:
        resp = client.get("/bff/research-experiments", headers=HEADERS)
        assert resp.status_code == 200, resp.text


def test_exp002_list_envelope_shape() -> None:
    with _bff_client() as client:
        payload = client.get("/bff/research-experiments", headers=HEADERS).json()
        assert "items" in payload, "list response must include 'items'"
        assert "page_info" in payload, "list response must include 'page_info'"
        assert "meta" in payload, "list response must include 'meta'"
        assert "snapshot_at" in payload["meta"]
        assert "surfaces" in payload["meta"]
        surfaces = payload["meta"]["surfaces"]
        assert "research_experiments" in surfaces, (
            "meta.surfaces must contain 'research_experiments' surface status"
        )


def test_exp002_list_surface_status_valid() -> None:
    with _bff_client() as client:
        payload = client.get("/bff/research-experiments", headers=HEADERS).json()
        surface_status = payload["meta"]["surfaces"]["research_experiments"]["status"]
        assert surface_status in {"ok", "fresh", "stale", "degraded", "unavailable"}, (
            f"unexpected surface status: {surface_status}"
        )


def test_exp002_list_seeded_experiments_present() -> None:
    with _bff_client() as client:
        payload = client.get("/bff/research-experiments", headers=HEADERS).json()
        ids = [item.get("experiment_id") for item in payload["items"]]
        assert COMPLETED_EXP_ID in ids, f"{COMPLETED_EXP_ID} missing from list"
        assert RUNNING_EXP_ID in ids, f"{RUNNING_EXP_ID} missing from list"
        assert FAILED_EXP_ID in ids, f"{FAILED_EXP_ID} missing from list"


def test_exp002_list_items_have_required_fields() -> None:
    with _bff_client() as client:
        payload = client.get("/bff/research-experiments", headers=HEADERS).json()
        for item in payload["items"]:
            assert "experiment_id" in item, "list item missing experiment_id"
            assert "ticket_id" in item, "list item missing ticket_id"
            assert "status" in item, "list item missing status"
            assert "allowedActions" in item, "list item missing allowedActions"
            assert "canCancel" in item["allowedActions"]


def test_exp002_list_can_cancel_invariant() -> None:
    """Terminal experiments must not be cancelable; active ones may be."""
    with _bff_client() as client:
        payload = client.get("/bff/research-experiments", headers=HEADERS).json()
        for item in payload["items"]:
            status = item["status"]
            can_cancel = item["allowedActions"]["canCancel"]
            if status in {"completed", "failed", "canceled"}:
                assert can_cancel is False, (
                    f"Terminal experiment {item['experiment_id']} must have canCancel=false"
                )


def test_exp002_list_page_info_shape() -> None:
    with _bff_client() as client:
        payload = client.get("/bff/research-experiments", headers=HEADERS).json()
        page_info = payload["page_info"]
        assert "next_page_token" in page_info


def test_exp002_list_requires_auth() -> None:
    with _bff_client() as client:
        resp = client.get("/bff/research-experiments")
        assert resp.status_code in {401, 403}, (
            f"unauthenticated request should be rejected, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# GET /bff/research-experiments/{id}  — detail
# ---------------------------------------------------------------------------

def test_exp002_detail_completed_returns_200() -> None:
    with _bff_client() as client:
        resp = client.get(f"/bff/research-experiments/{COMPLETED_EXP_ID}", headers=HEADERS)
        assert resp.status_code == 200, resp.text


def test_exp002_detail_envelope_shape() -> None:
    with _bff_client() as client:
        payload = client.get(
            f"/bff/research-experiments/{COMPLETED_EXP_ID}", headers=HEADERS
        ).json()
        assert "data" in payload or "experiment_id" in payload, (
            "detail response must have 'data' envelope or flat record"
        )
        assert "meta" in payload
        assert "snapshot_at" in payload["meta"]
        assert "surfaces" in payload["meta"]
        surfaces = payload["meta"]["surfaces"]
        assert "research_experiment_detail" in surfaces, (
            "meta.surfaces must contain 'research_experiment_detail'"
        )


def test_exp002_detail_surface_status_valid() -> None:
    with _bff_client() as client:
        payload = client.get(
            f"/bff/research-experiments/{COMPLETED_EXP_ID}", headers=HEADERS
        ).json()
        surface_status = payload["meta"]["surfaces"]["research_experiment_detail"]["status"]
        assert surface_status in {"ok", "fresh", "stale", "degraded", "unavailable"}


def _get_data(payload: dict) -> dict:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def test_exp002_detail_completed_fields() -> None:
    with _bff_client() as client:
        payload = client.get(
            f"/bff/research-experiments/{COMPLETED_EXP_ID}", headers=HEADERS
        ).json()
        record = _get_data(payload)
        assert record["experiment_id"] == COMPLETED_EXP_ID
        assert record["ticket_id"] == "rt-20260419-007"
        assert record["status"] == "completed"
        assert record["allowedActions"]["canCancel"] is False
        assert "analysis_links" in record, "detail must include analysis_links"
        assert "analysis_ids" in record, "detail must include analysis_ids"


def test_exp002_detail_running_can_cancel() -> None:
    with _bff_client() as client:
        payload = client.get(
            f"/bff/research-experiments/{RUNNING_EXP_ID}", headers=HEADERS
        ).json()
        record = _get_data(payload)
        assert record["status"] == "running"
        assert record["allowedActions"]["canCancel"] is True


def test_exp002_detail_failed_has_failure_fields() -> None:
    with _bff_client() as client:
        payload = client.get(
            f"/bff/research-experiments/{FAILED_EXP_ID}", headers=HEADERS
        ).json()
        record = _get_data(payload)
        assert record["status"] == "failed"
        assert record["failure"]["reason_code"] == "MISSING_DATA_PARTITION"
        assert record["failure"]["message"] is not None
        assert record["allowedActions"]["canCancel"] is False


def test_exp002_detail_404_for_unknown_id() -> None:
    with _bff_client() as client:
        resp = client.get(f"/bff/research-experiments/{UNKNOWN_EXP_ID}", headers=HEADERS)
        assert resp.status_code == 404, resp.text
        payload = resp.json()
        detail = payload.get("detail") or payload
        error = detail.get("error") if isinstance(detail, dict) else payload
        if isinstance(error, dict):
            assert error.get("code") == "OBJECT_NOT_FOUND"


def test_exp002_detail_requires_auth() -> None:
    with _bff_client() as client:
        resp = client.get(f"/bff/research-experiments/{COMPLETED_EXP_ID}")
        assert resp.status_code in {401, 403}, (
            f"unauthenticated detail request should be rejected, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Round-trip: launch via /api/v1/experiments/launch then fetch via BFF surface
# ---------------------------------------------------------------------------

def test_exp002_launch_then_bff_list_and_detail() -> None:
    """Experiment launched via the API must appear in the BFF list and detail surfaces."""
    with _bff_client(fallback=False) as client:
        launch_payload = {
            "ticket_id": "rt-exp002-test-001",
            "experiment_name": "EXP-002 BFF surface round-trip",
            "strategy_selector": {"strategy_id": "strat-exp002", "variant_id": None},
            "parameter_set": {"alpha": 0.01},
            "run_config": {
                "dataset_ref": "equities-us-2026Q1",
                "time_range": {"start_at": "2026-01-01T00:00:00Z", "end_at": "2026-03-31T23:59:59Z"},
                "execution_mode": "backtest",
                "priority": "normal",
                "requested_by": "exp002-test-agent",
            },
            "launch_context": {"analysis_refs": None},
        }
        launch_resp = client.post(
            "/api/v1/experiments/launch",
            json=launch_payload,
            headers=HEADERS,
        )
        assert launch_resp.status_code == 200, launch_resp.text
        exp_id = launch_resp.json()["experiment_id"]

        # Must appear in /bff/research-experiments list
        list_resp = client.get("/bff/research-experiments", headers=HEADERS)
        assert list_resp.status_code == 200
        ids = [item.get("experiment_id") for item in list_resp.json()["items"]]
        assert exp_id in ids, f"{exp_id} not found in /bff/research-experiments list"

        # Must be fetchable via /bff/research-experiments/{id}
        detail_resp = client.get(f"/bff/research-experiments/{exp_id}", headers=HEADERS)
        assert detail_resp.status_code == 200, detail_resp.text
        record = _get_data(detail_resp.json())
        assert record["experiment_id"] == exp_id
        assert record["status"] == "queued"
        assert record["allowedActions"]["canCancel"] is True
