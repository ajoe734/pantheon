"""Tests for Source / Search Operator Ops BFF Surface (SVC-SOURCE-SEARCH-OPS-BFF).

Coverage:
- read_store: get_source_ops_snapshot / get_search_ops_snapshot
  - normal path (service_client)
  - degraded source (service unavailable)
  - degraded search (stale index)
- BFF endpoints: GET /api/v1/operator/source/ops, GET /api/v1/operator/search/ops
- BFF command endpoints: POST .../dlq/replay, POST .../frontier/{id}/replay,
  POST .../search/index/refresh, POST .../search/index/materialize
  - auth guard (403 without operator role)
  - missing idempotency key (400)
  - service client error (502)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from unittest import mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
sys.path.insert(0, os.path.dirname(__file__))


# --------------------------------------------------------------------------- #
# read_store unit tests
# --------------------------------------------------------------------------- #


def test_get_source_ops_snapshot_normal_path() -> None:
    from read_store import ReadSurfaceStore

    def fake_get(base_url, path, *, headers=None):
        if path.startswith("/api/source-ingest/registry"):
            return True, {
                "schema_version": "source_connector_registry.v1",
                "connectors": [
                    {"connector_id": "conn-a", "status": "enabled", "provider": "TestProvider"},
                ],
                "provider_examples": [],
            }
        if path.startswith("/api/source-ingest/jobs"):
            return True, {
                "runs": [
                    {"ingest_run_id": "run-001", "status": "completed", "connector_id": "conn-a"},
                    {"ingest_run_id": "run-002", "status": "failed", "connector_id": "conn-a"},
                ],
            }
        if path.startswith("/api/source-ingest/dlq"):
            return True, {
                "entries": [
                    {"entry_id": "dlq-001", "status": "pending", "tag": "retry_exhausted"},
                ],
            }
        if path.startswith("/api/source-ingest/frontier"):
            return True, {
                "frontier": [
                    {"frontier_id": "fr-001", "status": "failed", "connector_id": "conn-a"},
                ],
            }
        if path.startswith("/api/source-ingest/audit"):
            return True, {
                "actions": [
                    {"action_id": "aud-001", "action_type": "ingest_completed"},
                ],
            }
        return False, None

    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(os.environ, {"PANTHEON_SOURCE_INGEST_API_URL": "http://source:8097"}):
            with mock.patch("read_store._http_json_get", side_effect=fake_get):
                store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=False,
                )
                snap = store.get_source_ops_snapshot()

    assert snap["source"] == "service_client"
    assert len(snap["connector_health"]) == 1
    assert snap["connector_health"][0]["connector_id"] == "conn-a"
    assert len(snap["crawl_runs"]) == 2
    assert len(snap["dlq"]) == 1
    assert snap["dlq"][0]["entry_id"] == "dlq-001"
    assert len(snap["frontier"]) == 1
    assert len(snap["audit"]) == 1
    assert snap["summary"]["connector_count"] == 1
    assert snap["summary"]["dlq_count"] == 1
    assert snap["summary"]["frontier_count"] == 1


def test_get_source_ops_snapshot_degraded_source() -> None:
    """When source ingest service is unavailable, returns degraded/missing snapshot."""
    from read_store import ReadSurfaceStore

    with tempfile.TemporaryDirectory() as td:
        # No PANTHEON_SOURCE_INGEST_API_URL → missing
        env = {k: "" for k in ["PANTHEON_SOURCE_INGEST_API_URL", "PANTHEON_SOURCE_INGEST_URL", "SOURCE_INGEST_URL"]}
        with mock.patch.dict(os.environ, env):
            store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=False,
            )
            snap = store.get_source_ops_snapshot()

    assert snap["source"] == "missing"
    assert snap["connector_health"] == []
    assert snap["dlq"] == []
    assert snap["summary"]["connector_count"] == 0


def test_get_source_ops_snapshot_service_unavailable() -> None:
    """When source ingest returns errors for all calls, source becomes unavailable."""
    from read_store import ReadSurfaceStore

    def fake_get(base_url, path, *, headers=None):
        return False, None

    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(os.environ, {"PANTHEON_SOURCE_INGEST_API_URL": "http://source:8097"}):
            with mock.patch("read_store._http_json_get", side_effect=fake_get):
                store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=False,
                )
                snap = store.get_source_ops_snapshot()

    assert snap["source"] == "unavailable"
    assert snap["connector_health"] == []
    assert snap["dlq"] == []


def test_get_search_ops_snapshot_normal_path() -> None:
    from read_store import ReadSurfaceStore

    def fake_get(base_url, path, *, headers=None):
        if "/api/search/index/freshness" in path:
            return True, {
                "within_sla": True,
                "sla_seconds": 3600,
                "seconds_since_last_run": 120,
                "last_run_at": "2026-04-30T07:00:00Z",
            }
        if "/api/search/index/pipeline-runs" in path:
            return True, {
                "runs": [
                    {"run_id": "pipe-001", "status": "completed", "indexed_count": 5},
                ],
                "total": 1,
            }
        if "/api/search/index/materialize" in path:
            return True, {
                "materialized_at": "2026-04-30T07:00:00Z",
                "indexed_object_count": 42,
            }
        return False, None

    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(os.environ, {"PANTHEON_SEARCH_API_URL": "http://search:8098"}):
            with mock.patch("read_store._http_json_get", side_effect=fake_get):
                store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=False,
                )
                snap = store.get_search_ops_snapshot()

    assert snap["source"] == "service_client"
    assert snap["index_freshness"]["within_sla"] is True
    assert snap["summary"]["freshness_ok"] is True
    assert snap["summary"]["freshness_status"] == "ok"
    assert len(snap["pipeline_runs"]) == 1
    assert snap["pipeline_run_total"] == 1
    assert snap["materialized_index"]["indexed_object_count"] == 42


def test_get_search_ops_snapshot_stale_index() -> None:
    """When index is outside SLA, freshness_status is stale."""
    from read_store import ReadSurfaceStore

    def fake_get(base_url, path, *, headers=None):
        if "/api/search/index/freshness" in path:
            return True, {
                "within_sla": False,
                "sla_seconds": 3600,
                "seconds_since_last_run": 7200,
                "last_run_at": "2026-04-30T05:00:00Z",
            }
        if "/api/search/index/pipeline-runs" in path:
            return True, {"runs": [], "total": 0}
        if "/api/search/index/materialize" in path:
            return True, None  # 404 scenario
        return False, None

    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(os.environ, {"PANTHEON_SEARCH_API_URL": "http://search:8098"}):
            with mock.patch("read_store._http_json_get", side_effect=fake_get):
                store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=False,
                )
                snap = store.get_search_ops_snapshot()

    assert snap["source"] == "service_client"
    assert snap["summary"]["freshness_ok"] is False
    assert snap["summary"]["freshness_status"] == "stale"


def test_get_search_ops_snapshot_missing_url() -> None:
    from read_store import ReadSurfaceStore

    with tempfile.TemporaryDirectory() as td:
        env = {k: "" for k in ["PANTHEON_SEARCH_API_URL", "PANTHEON_SEARCH_SERVICE_URL"]}
        with mock.patch.dict(os.environ, env):
            store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=False,
            )
            snap = store.get_search_ops_snapshot()

    assert snap["source"] == "missing"
    assert snap["index_freshness"] is None
    assert snap["pipeline_runs"] == []
    assert snap["summary"]["freshness_ok"] is False


# --------------------------------------------------------------------------- #
# BFF HTTP endpoint tests
# --------------------------------------------------------------------------- #


def _make_client(stub: bool = True, role: str = "operator"):
    """Build a FastAPI test client with auth stub enabled."""
    import importlib
    import main as bff_main
    importlib.reload(bff_main)

    from fastapi.testclient import TestClient

    env_patch = {"PANTHEON_BFF_AUTH_STUB": "true"}
    with mock.patch.dict(os.environ, env_patch):
        client = TestClient(bff_main.app, raise_server_exceptions=False)
    return client


@pytest.fixture()
def bff_client():
    from fastapi.testclient import TestClient
    with mock.patch.dict(os.environ, {"PANTHEON_BFF_AUTH_STUB": "true"}):
        import main as bff_main
        yield TestClient(bff_main.app, raise_server_exceptions=False)


def _auth_header(role: str = "operator") -> dict:
    return {"Authorization": f"Bearer op-test:{role}"}


class _FakeHttpResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class _PostRecorder:
    def __init__(self, payload: dict | None = None) -> None:
        self.calls = []
        self.payload = payload or {"status": "accepted"}

    def __call__(self, request, timeout=None):
        body = json.loads((request.data or b"{}").decode("utf-8"))
        self.calls.append((request.get_method(), request.full_url, dict(request.headers), body, timeout))
        return _FakeHttpResponse(self.payload)


def test_get_source_ops_requires_auth(bff_client) -> None:
    resp = bff_client.get("/api/v1/operator/source/ops")
    assert resp.status_code == 401


def test_get_source_ops_requires_operator_role(bff_client) -> None:
    resp = bff_client.get(
        "/api/v1/operator/source/ops",
        headers={"Authorization": "Bearer viewer-token:viewer"},
    )
    assert resp.status_code == 403


def test_get_source_ops_degraded_returns_200(bff_client) -> None:
    """When source service is not configured, BFF returns 200 with missing state."""
    env = {k: "" for k in ["PANTHEON_SOURCE_INGEST_API_URL", "PANTHEON_SOURCE_INGEST_URL", "SOURCE_INGEST_URL"]}
    with mock.patch.dict(os.environ, env):
        resp = bff_client.get(
            "/api/v1/operator/source/ops",
            headers=_auth_header("operator"),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["source"] in ("missing", "unavailable")
    assert "meta" in body
    assert "snapshot_at" in body["meta"]


def test_get_source_ops_with_service(bff_client) -> None:
    """When source service is available, ops surface includes data."""
    def fake_get(base_url, path, *, headers=None):
        if "/api/source-ingest/registry" in path:
            return True, {"connectors": [{"connector_id": "c1"}], "provider_examples": []}
        if "/api/source-ingest/jobs" in path:
            return True, {"runs": [{"ingest_run_id": "r1", "status": "completed"}]}
        if "/api/source-ingest/dlq" in path:
            return True, {"entries": []}
        if "/api/source-ingest/frontier" in path:
            return True, {"frontier": []}
        if "/api/source-ingest/audit" in path:
            return True, {"actions": []}
        return False, None

    with mock.patch.dict(os.environ, {"PANTHEON_SOURCE_INGEST_API_URL": "http://source:8097"}):
        with mock.patch("read_store._http_json_get", side_effect=fake_get):
            resp = bff_client.get(
                "/api/v1/operator/source/ops",
                headers=_auth_header("operator"),
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["source"] == "service_client"
    assert body["data"]["summary"]["connector_count"] == 1
    assert body["meta"]["surfaces"]["source_ops"]["status"] == "ok"


def test_get_search_ops_requires_auth(bff_client) -> None:
    resp = bff_client.get("/api/v1/operator/search/ops")
    assert resp.status_code == 401


def test_get_search_ops_degraded_missing_url(bff_client) -> None:
    """When search service URL is absent, returns 200 with missing state."""
    env = {k: "" for k in ["PANTHEON_SEARCH_API_URL", "PANTHEON_SEARCH_SERVICE_URL"]}
    with mock.patch.dict(os.environ, env):
        resp = bff_client.get(
            "/api/v1/operator/search/ops",
            headers=_auth_header("operator"),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["source"] == "missing"
    assert body["data"]["summary"]["freshness_ok"] is False


def test_get_search_ops_stale_surface_status(bff_client) -> None:
    """When search is available but index is stale, surface status is stale."""
    def fake_get(base_url, path, *, headers=None):
        if "/api/search/index/freshness" in path:
            return True, {"within_sla": False, "sla_seconds": 3600, "seconds_since_last_run": 7200}
        if "/api/search/index/pipeline-runs" in path:
            return True, {"runs": [], "total": 0}
        if "/api/search/index/materialize" in path:
            return True, None
        return False, None

    with mock.patch.dict(os.environ, {"PANTHEON_SEARCH_API_URL": "http://search:8098"}):
        with mock.patch("read_store._http_json_get", side_effect=fake_get):
            resp = bff_client.get(
                "/api/v1/operator/search/ops",
                headers=_auth_header("operator"),
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["surfaces"]["search_ops"]["status"] == "stale"


# --------------------------------------------------------------------------- #
# Command endpoint tests
# --------------------------------------------------------------------------- #


def test_replay_dlq_requires_auth(bff_client) -> None:
    resp = bff_client.post("/api/v1/operator/source/dlq/replay", json={})
    assert resp.status_code == 401


def test_replay_dlq_requires_operator_role(bff_client) -> None:
    resp = bff_client.post(
        "/api/v1/operator/source/dlq/replay",
        json={},
        headers={"Authorization": "Bearer viewer:viewer"},
    )
    assert resp.status_code == 403


def test_replay_dlq_requires_idempotency_key(bff_client) -> None:
    resp = bff_client.post(
        "/api/v1/operator/source/dlq/replay",
        json={},
        headers=_auth_header("operator"),
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"]["error"]["code"] == "INVALID_PARAMS"


def test_replay_dlq_service_unavailable(bff_client) -> None:
    """When source-ingest service is not configured, returns 503 wrapped as BFF error."""
    env = {k: "" for k in ["PANTHEON_SOURCE_INGEST_API_URL", "PANTHEON_SOURCE_INGEST_URL", "SOURCE_INGEST_URL"]}
    with mock.patch.dict(os.environ, env):
        resp = bff_client.post(
            "/api/v1/operator/source/dlq/replay",
            json={},
            headers={**_auth_header("operator"), "X-Idempotency-Key": "key-001"},
        )
    assert resp.status_code in (503, 502)


def test_replay_dlq_forwards_operator_and_idempotency_key(bff_client) -> None:
    recorder = _PostRecorder({"replayed_count": 1})
    with mock.patch.dict(os.environ, {"PANTHEON_SOURCE_INGEST_API_URL": "http://source:8097"}):
        with mock.patch("source_search_ops_client.urllib.request.urlopen", recorder):
            resp = bff_client.post(
                "/api/v1/operator/source/dlq/replay",
                json={"entry_ids": ["dlq-001"], "reason": "retry after fix"},
                headers={**_auth_header("operator"), "X-Idempotency-Key": "idmp-source-dlq-1"},
            )

    assert resp.status_code == 202, resp.text
    method, url, headers, body, _timeout = recorder.calls[0]
    assert method == "POST"
    assert url == "http://source:8097/api/source-ingest/dlq/replay"
    assert headers["X-idempotency-key"] == "idmp-source-dlq-1"
    assert body["actor_id"] == "op-test"
    assert body["entry_ids"] == ["dlq-001"]


def test_replay_frontier_requires_idempotency_key(bff_client) -> None:
    resp = bff_client.post(
        "/api/v1/operator/source/frontier/fr-001/replay",
        json={},
        headers=_auth_header("operator"),
    )
    assert resp.status_code == 400


def test_search_index_refresh_requires_operator(bff_client) -> None:
    resp = bff_client.post(
        "/api/v1/operator/search/index/refresh",
        json={},
        headers={"Authorization": "Bearer v:viewer", "X-Idempotency-Key": "k1"},
    )
    assert resp.status_code == 403


def test_search_index_refresh_service_unavailable(bff_client) -> None:
    env = {k: "" for k in ["PANTHEON_SEARCH_API_URL", "PANTHEON_SEARCH_SERVICE_URL"]}
    with mock.patch.dict(os.environ, env):
        resp = bff_client.post(
            "/api/v1/operator/search/index/refresh",
            json={},
            headers={**_auth_header("operator"), "X-Idempotency-Key": "key-002"},
        )
    assert resp.status_code in (503, 502)


def test_search_index_refresh_forwards_idempotency_key(bff_client) -> None:
    recorder = _PostRecorder({"run_id": "pipe-001", "status": "completed"})
    with mock.patch.dict(os.environ, {"PANTHEON_SEARCH_API_URL": "http://search:8098"}):
        with mock.patch("source_search_ops_client.urllib.request.urlopen", recorder):
            resp = bff_client.post(
                "/api/v1/operator/search/index/refresh",
                json={"force_full": True, "trigger_ref": "ops-refresh-1"},
                headers={**_auth_header("operator"), "X-Idempotency-Key": "idmp-search-refresh-1"},
            )

    assert resp.status_code == 202, resp.text
    method, url, headers, body, _timeout = recorder.calls[0]
    assert method == "POST"
    assert url == "http://search:8098/api/search/index/refresh"
    assert headers["X-idempotency-key"] == "idmp-search-refresh-1"
    assert body["force_full"] is True
    assert body["trigger_ref"] == "ops-refresh-1"


def test_search_index_materialize_requires_idempotency_key(bff_client) -> None:
    resp = bff_client.post(
        "/api/v1/operator/search/index/materialize",
        headers=_auth_header("operator"),
    )
    assert resp.status_code == 400
