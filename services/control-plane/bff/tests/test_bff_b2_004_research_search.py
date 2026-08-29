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
from ports import create_in_memory_read_surface_ports

OPERATOR_HEADERS = {"Authorization": "Bearer op-b2-004:operator"}
NO_AUTH_HEADERS: dict = {}

_IDEM_PREFIX = "b2-004-test"


class _ResearchSearchTestStore:
    def __init__(self) -> None:
        self.ports = create_in_memory_read_surface_ports()
        self._experiments: dict[str, dict[str, Any]] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self.ports, name)

    def dataset_source(self, dataset: str, **kwargs: Any) -> str:
        if dataset in ("research_experiments", "experiments"):
            return "local_snapshot"
        return self.ports.dataset_source(dataset)

    def create_experiment_bff(self, name: str, actor_id: Optional[str] = None, created_at: Optional[str] = None, params: Optional[dict] = None, status: str = "active", **kwargs: Any) -> dict[str, Any]:
        experiment_id = str(
            (params or {}).get("experiment_id")
            or (params or {}).get("id")
            or f"exp-test-{uuid.uuid4().hex[:8]}"
        )
        item = {
            "id": experiment_id,
            "experiment_id": experiment_id,
            "name": name,
            "actor_id": actor_id,
            "created_at": created_at or "2026-06-01T00:00:00Z",
            "status": status,
            "params": params or {},
        }
        self._experiments[experiment_id] = item
        return item

    def get_experiment_bff(self, experiment_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not experiment_id:
            return None
        return self._experiments.get(experiment_id)

    def list_experiments_bff(self, status: Optional[str] = None, **kwargs: Any) -> list[dict[str, Any]]:
        experiments = list(self._experiments.values())
        if status:
            experiments = [e for e in experiments if str(e.get("status") or "").lower() == status.lower()]
        return experiments

    def create_research_experiment(self, experiment_id: str, name: str, actor_id: Optional[str] = None, created_at: Optional[str] = None, params: Optional[dict] = None, status: str = "active", **kwargs: Any) -> dict[str, Any]:
        return self.create_experiment_bff(name=name, actor_id=actor_id, created_at=created_at, params={"id": experiment_id, **(params or {})}, status=status, **kwargs)

    def get_research_experiment(self, experiment_id: Optional[str]) -> Optional[dict[str, Any]]:
        return self.get_experiment_bff(experiment_id)

    def list_research_experiments(self, status: Optional[str] = None, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_experiments_bff(status=status, **kwargs)


def _fresh_client(td: str) -> TestClient:
    bff_main.read_store = _ResearchSearchTestStore()
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
            assert "detail" in body or "error" in body
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


# ---------------------------------------------------------------------------
# 5. GET /bff/search — cursor pagination regression
# ---------------------------------------------------------------------------

def test_bff_search_cursor_first_page() -> None:
    """First page with page_size=1 must return a non-null next_page_token when results remain."""
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        saved_overlay = dict(bff_main._STRATEGY_BFF_OVERLAY)
        try:
            client = _fresh_client(td)
            for i in range(3):
                bff_main._STRATEGY_BFF_OVERLAY[f"search-pag-strat-{i}"] = {
                    "name": f"search-pag-strat-{i}",
                    "state": "draft",
                    "owner": "test",
                    "updatedAt": "2026-05-23T00:00:00Z",
                }
            resp = client.get(
                "/bff/search?q=search-pag-strat&page_size=1",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert len(body.get("data") or []) == 1
            pi = body["page_info"]
            assert pi.get("returned") == 1
            assert pi.get("total") >= 3
            assert pi.get("next_page_token") is not None, (
                "next_page_token must be set when more results remain"
            )
        finally:
            bff_main.read_store = original_store
            bff_main._STRATEGY_BFF_OVERLAY.clear()
            bff_main._STRATEGY_BFF_OVERLAY.update(saved_overlay)


def test_bff_search_cursor_second_page() -> None:
    """Using next_page_token from page 1 must return a non-overlapping result set."""
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        saved_overlay = dict(bff_main._STRATEGY_BFF_OVERLAY)
        try:
            client = _fresh_client(td)
            for i in range(3):
                bff_main._STRATEGY_BFF_OVERLAY[f"search-pag-strat-{i}"] = {
                    "name": f"search-pag-strat-{i}",
                    "state": "draft",
                    "owner": "test",
                    "updatedAt": "2026-05-23T00:00:00Z",
                }
            resp1 = client.get(
                "/bff/search?q=search-pag-strat&page_size=2",
                headers=OPERATOR_HEADERS,
            )
            assert resp1.status_code == 200, resp1.text
            body1 = resp1.json()
            page1_ids = {r["id"] for r in (body1.get("data") or [])}
            npt = body1["page_info"].get("next_page_token")
            assert npt is not None, "next_page_token must be set after first page"
            resp2 = client.get(
                f"/bff/search?q=search-pag-strat&page_size=2&page_token={npt}",
                headers=OPERATOR_HEADERS,
            )
            assert resp2.status_code == 200, resp2.text
            body2 = resp2.json()
            page2_ids = {r["id"] for r in (body2.get("data") or [])}
            assert page2_ids, "Second page must contain at least one result"
            assert not (page1_ids & page2_ids), (
                f"Pages must not overlap; got page1={page1_ids} page2={page2_ids}"
            )
        finally:
            bff_main.read_store = original_store
            bff_main._STRATEGY_BFF_OVERLAY.clear()
            bff_main._STRATEGY_BFF_OVERLAY.update(saved_overlay)


# ---------------------------------------------------------------------------
# 6. GET /bff/search — backward-compat limit alias regression
# ---------------------------------------------------------------------------

def test_bff_search_limit_alias_respected() -> None:
    """?limit=N is a backward-compat alias for page_size; must cap returned items to N."""
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        saved_overlay = dict(bff_main._STRATEGY_BFF_OVERLAY)
        try:
            client = _fresh_client(td)
            for i in range(5):
                bff_main._STRATEGY_BFF_OVERLAY[f"limit-alias-strat-{i}"] = {
                    "name": f"limit-alias-strat-{i}",
                    "state": "draft",
                    "owner": "test",
                    "updatedAt": "2026-05-23T00:00:00Z",
                }
            resp = client.get(
                "/bff/search?q=limit-alias-strat&limit=2",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            returned = body.get("page_info", {}).get("returned") or len(body.get("data") or [])
            assert returned <= 2, f"limit=2 must cap results to ≤2, got {returned}"
            assert body["page_info"].get("total") >= 5
        finally:
            bff_main.read_store = original_store
            bff_main._STRATEGY_BFF_OVERLAY.clear()
            bff_main._STRATEGY_BFF_OVERLAY.update(saved_overlay)


def test_bff_search_limit_alias_matches_page_size() -> None:
    """?limit=N and ?page_size=N must return identical result counts."""
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        saved_overlay = dict(bff_main._STRATEGY_BFF_OVERLAY)
        try:
            client = _fresh_client(td)
            for i in range(5):
                bff_main._STRATEGY_BFF_OVERLAY[f"limit-eq-strat-{i}"] = {
                    "name": f"limit-eq-strat-{i}",
                    "state": "draft",
                    "owner": "test",
                    "updatedAt": "2026-05-23T00:00:00Z",
                }
            r_limit = client.get(
                "/bff/search?q=limit-eq-strat&limit=3", headers=OPERATOR_HEADERS
            )
            r_page_size = client.get(
                "/bff/search?q=limit-eq-strat&page_size=3", headers=OPERATOR_HEADERS
            )
            assert r_limit.status_code == 200 and r_page_size.status_code == 200
            b_limit = r_limit.json()
            b_ps = r_page_size.json()
            assert b_limit["page_info"]["returned"] == b_ps["page_info"]["returned"], (
                f"limit and page_size must return same count; "
                f"limit={b_limit['page_info']['returned']} page_size={b_ps['page_info']['returned']}"
            )
        finally:
            bff_main.read_store = original_store
            bff_main._STRATEGY_BFF_OVERLAY.clear()
            bff_main._STRATEGY_BFF_OVERLAY.update(saved_overlay)
