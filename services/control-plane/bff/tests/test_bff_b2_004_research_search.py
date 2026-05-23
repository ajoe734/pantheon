"""
BFF-B2-004: Integration tests for the B2.3 Capabilities / Research / Search
facade.

Covers:
  - GET /bff/research-experiments        list + data/items + page_info + meta.surfaces
  - GET /bff/research-experiments/{id}   detail + 404 for unknown id
  - GET /bff/search                       cross-entity search + page_info + meta
  - GET /bff/capabilities                 feature-flags envelope
  - All four endpoints return HTTP 401 when unauthenticated
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

OPERATOR_HEADERS = {"Authorization": "Bearer op-b2-004:operator"}
NO_AUTH_HEADERS: dict = {}

_IDEM_PREFIX = "b2-004-test"


def _fresh_client(td: str) -> TestClient:
    bff_main.read_store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=True,
    )
    bff_main._GOV_BFF_IDEMPOTENCY.clear()
    bff_main._GOV_BFF_EXPERIMENT_OVERLAY.clear()
    return TestClient(bff_main.app)


def _create_experiment(client: TestClient, name: str = "Test Experiment") -> str:
    """Create an experiment via /bff/experiments (the compat path) and return its ID."""
    key = f"{_IDEM_PREFIX}-exp-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/bff/experiments",
        json={"name": name, "description": "b2-004 test experiment"},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": key},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return str(body.get("experiment_id") or body.get("id") or "")


# ---------------------------------------------------------------------------
# 1. GET /bff/research-experiments — list
# ---------------------------------------------------------------------------

def test_bff_research_experiments_list_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/research-experiments", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "items" in body
            assert "page_info" in body
            assert "meta" in body
            assert "surfaces" in body["meta"]
            assert "research_experiments" in body["meta"]["surfaces"]
        finally:
            bff_main.read_store = original
            bff_main._GOV_BFF_EXPERIMENT_OVERLAY.clear()


def test_bff_research_experiments_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            assert client.get("/bff/research-experiments").status_code == 401
        finally:
            bff_main.read_store = original


def test_bff_research_experiments_list_includes_created() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            exp_id = _create_experiment(client, "B2-004 Visible Experiment")
            resp = client.get("/bff/research-experiments", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            ids = [
                str(e.get("experiment_id") or e.get("id") or "")
                for e in (body.get("data") or body.get("items") or [])
            ]
            assert exp_id in ids, f"Created experiment {exp_id!r} not found in list: {ids}"
        finally:
            bff_main.read_store = original
            bff_main._GOV_BFF_EXPERIMENT_OVERLAY.clear()


def test_bff_research_experiments_list_status_filter() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            _create_experiment(client, "Queued Exp")
            resp = client.get(
                "/bff/research-experiments?status=queued", headers=OPERATOR_HEADERS
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            items = body.get("data") or body.get("items") or []
            for item in items:
                assert str(item.get("status") or "").lower() == "queued"
        finally:
            bff_main.read_store = original
            bff_main._GOV_BFF_EXPERIMENT_OVERLAY.clear()


# ---------------------------------------------------------------------------
# 2. GET /bff/research-experiments/{id} — detail
# ---------------------------------------------------------------------------

def test_bff_research_experiment_detail_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            exp_id = _create_experiment(client, "Detail Test Experiment")
            resp = client.get(
                f"/bff/research-experiments/{exp_id}", headers=OPERATOR_HEADERS
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "meta" in body
            data = body["data"]
            assert str(data.get("experiment_id") or data.get("id") or "") == exp_id
        finally:
            bff_main.read_store = original
            bff_main._GOV_BFF_EXPERIMENT_OVERLAY.clear()


def test_bff_research_experiment_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get(
                "/bff/research-experiments/nonexistent-exp-id", headers=OPERATOR_HEADERS
            )
            assert resp.status_code == 404, resp.text
            body = resp.json()
            assert "detail" in body
        finally:
            bff_main.read_store = original
            bff_main._GOV_BFF_EXPERIMENT_OVERLAY.clear()


def test_bff_research_experiment_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            assert (
                client.get("/bff/research-experiments/some-id").status_code == 401
            )
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 3. GET /bff/search — cross-entity search
# ---------------------------------------------------------------------------

def test_bff_search_returns_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/search?q=", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "items" in body
            assert "page_info" in body
            assert "meta" in body
        finally:
            bff_main.read_store = original


def test_bff_search_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            assert client.get("/bff/search?q=test").status_code == 401
        finally:
            bff_main.read_store = original


def test_bff_search_type_filter() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get(
                "/bff/search?q=&types=strategy", headers=OPERATOR_HEADERS
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            results = body.get("data") or body.get("items") or []
            for r in results:
                assert r.get("type") == "strategy", f"Unexpected type: {r.get('type')}"
        finally:
            bff_main.read_store = original


def test_bff_search_page_info_fields() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/search?q=", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            pi = resp.json().get("page_info", {})
            assert "total" in pi
            assert "returned" in pi or "next_page_token" in pi
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# 4. GET /bff/capabilities — feature-flags envelope
# ---------------------------------------------------------------------------

def test_bff_capabilities_returns_feature_flags() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/capabilities", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "feature_flags" in body["data"]
            ff = body["data"]["feature_flags"]
            assert "executePlansBff" in ff
            assert "sessionAuthMe" in ff
            assert "meta" in body
        finally:
            bff_main.read_store = original


def test_bff_capabilities_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            assert client.get("/bff/capabilities").status_code == 401
        finally:
            bff_main.read_store = original
