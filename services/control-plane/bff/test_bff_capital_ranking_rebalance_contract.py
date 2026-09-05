"""
Contract tests for BFF-LUV-GAP-003: capital pools, ranking formulas,
rebalances, and rankings BFF compatibility surfaces.
"""
from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from services.control_plane.bff import main as bff_main
import uuid
from typing import Any

from ports import ReadSurfacePorts, create_in_memory_read_surface_ports

OPERATOR_TOKEN = "Bearer op-2:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}
IDEM_HEADERS = {**HEADERS, "Idempotency-Key": "test-key-001"}


class CanonicalMock:
    def __init__(self) -> None:
        self.list_records = lambda dataset, **kwargs: (True, [])
        self.capital_pool = lambda pool_id: (True, None)
        self.bindings_for_pool = lambda pool_id: (True, [])


class CapitalRankingTestReadPorts(ReadSurfacePorts):
    def __init__(self, data: dict | None = None, *, allow_local_snapshot_fallback: bool = True) -> None:
        super().__init__()
        self._allow_fallback = allow_local_snapshot_fallback
        self._data = data if data is not None else {
            "capital_pools": {},
            "ranking_formulas": {},
            "rebalances": {},
            "rankings": {},
        }
        self._canonical = CanonicalMock()

    def dataset_source(self, dataset: str) -> str:
        if dataset == "persona_bindings":
            ok, _ = self._canonical.bindings_for_pool("probe")
            return "canonical" if ok else "missing"
        if not self._allow_fallback:
            if os.environ.get("PANTHEON_CAPITAL_API_URL") or os.environ.get("PANTHEON_CAPITAL_SERVICE_URL") or os.environ.get("PANTHEON_BFF_CAPITAL_POOL_STORE"):
                return "canonical"
            ok, records = self._canonical.list_records(dataset)
            if ok and records:
                return "canonical"
            if self._data.get(dataset):
                return "canonical"
            return "missing"
        return "canonical"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        src = self.dataset_source(dataset)
        status = "unavailable" if src == "missing" else "ok"
        return {"status": status, "source": src, "snapshot_at": snapshot_at}

    def list_capital_pools(self, **kwargs: Any) -> list[dict[str, Any]]:
        ok, records = self._canonical.list_records("capital_pools", **kwargs)
        if ok and records:
            status = kwargs.get("status")
            rp = kwargs.get("risk_policy_ref")
            res = [dict(r) for r in records]
            if status:
                res = [r for r in res if r.get("status") == status]
            if rp:
                res = [r for r in res if r.get("risk_policy_ref") == rp]
            for r in res:
                r.setdefault("id", r.get("pool_id"))
            return res
        res = [dict(r) for r in self._data.get("capital_pools", {}).values()]
        for r in res:
            r.setdefault("id", r.get("pool_id"))
        return res

    def get_capital_pool(self, pool_id: str | None) -> dict[str, Any] | None:
        ok, pool = self._canonical.capital_pool(pool_id)
        if ok and pool:
            p = dict(pool)
            p.setdefault("id", p.get("pool_id"))
            return p
        raw = self._data.get("capital_pools", {}).get(str(pool_id or ""))
        if raw:
            p = dict(raw)
            p.setdefault("id", p.get("pool_id"))
            return p
        return None

    def get_bindings_for_pool(self, pool_id: str | None) -> list[dict[str, Any]]:
        ok, bindings = self._canonical.bindings_for_pool(pool_id)
        if ok:
            return bindings
        return []

    def create_capital_pool(self, *, pool_id: str | None = None, name: str = "", actor_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        pid = pool_id or f"pool-{uuid.uuid4().hex[:8]}"
        p = {"id": pid, "pool_id": pid, "name": name, "status": kwargs.get("status", "active"), "owner_id": kwargs.get("owner_id", actor_id or "op-1"), "owner_type": kwargs.get("owner_type", "operator"), **kwargs}
        self._data.setdefault("capital_pools", {})[pid] = p
        return p

    def patch_capital_pool(self, pool_id: str, *, patch: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any] | None:
        p = self.get_capital_pool(pool_id) or self._data.get("capital_pools", {}).get(pool_id)
        if p:
            p.update(patch or kwargs)
            return p
        return None

    def list_ranking_formulas(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("ranking_formulas", {}).values())

    def get_ranking_formula(self, formula_id: str | None) -> dict[str, Any] | None:
        return self._data.get("ranking_formulas", {}).get(str(formula_id or ""))

    def create_ranking_formula(self, *, name: str = "", description: str = "", actor_id: str | None = None, formula_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        fid = formula_id or f"rf-{uuid.uuid4().hex[:8]}"
        f = {"id": fid, "formula_id": fid, "name": name, "description": description, "status": kwargs.get("status", "active"), "params": kwargs.get("params", {}), "actor_id": actor_id or "op-1", **kwargs}
        self._data.setdefault("ranking_formulas", {})[fid] = f
        return f

    def patch_ranking_formula(self, formula_id: str, *, patch: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any] | None:
        f = self.get_ranking_formula(formula_id)
        if f:
            f.update(patch or kwargs)
            return f
        return None

    def list_rebalances(self, *, pool_id: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        rbs = list(self._data.get("rebalances", {}).values())
        if pool_id:
            return [r for r in rbs if r.get("capital_pool_id") == pool_id or r.get("pool_id") == pool_id]
        return rbs

    def get_rebalance(self, rebalance_id: str | None) -> dict[str, Any] | None:
        return self._data.get("rebalances", {}).get(str(rebalance_id or ""))

    def create_rebalance(self, *, capital_pool_id: str = "", actor_id: str | None = None, reason: str = "", **kwargs: Any) -> dict[str, Any]:
        rid = f"rb-{uuid.uuid4().hex[:8]}"
        rb = {"id": rid, "rebalance_id": rid, "capital_pool_id": capital_pool_id, "pool_id": capital_pool_id, "actor_id": actor_id or "op-1", "reason": reason, **kwargs}
        self._data.setdefault("rebalances", {})[rid] = rb
        return rb

    def list_rankings(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("rankings", {}).values())

    def get_ranking(self, ranking_id: str | None) -> dict[str, Any] | None:
        return self._data.get("rankings", {}).get(str(ranking_id or ""))

    def list_capital_allocations(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []


def _error(resp):
    body = resp.json()
    if isinstance(body.get("error"), dict):
        return body["error"]
    detail = body.get("detail")
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        return detail["error"]
    raise AssertionError(f"response did not contain BFF error envelope: {body}")


def _fresh_client(td: str):
    bff_main.read_store = CapitalRankingTestReadPorts(allow_local_snapshot_fallback=True)
    bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
    bff_main.command_store._update_commands([])
    return TestClient(bff_main.app)


def _owner_pool_create(payload):
    """Contract-test owner stub; real owner integration lives in focused tests."""
    return {
        "id": payload["pool_id"],
        "pool_id": payload["pool_id"],
        "name": payload["name"],
        "owner_id": payload["owner_id"],
        "owner_type": payload["owner_type"],
        "status": payload["status"],
        "risk_policy_ref": payload.get("risk_policy_ref"),
        "idempotent_replay": False,
    }


# ---------------------------------------------------------------------------
# Capital pools
# ---------------------------------------------------------------------------

def test_bff_capital_pools_list_returns_200() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/capital-pools", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "meta" in body
            assert "page_info" in body
        finally:
            bff_main.read_store = original


def test_bff_capital_pools_list_returns_strict_items_envelope(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        store = CapitalRankingTestReadPorts(
            allow_local_snapshot_fallback=False,
        )
        seed_pool = {
            "id": "pool-alpha",
            "pool_id": "pool-alpha",
            "name": "Alpha Pool",
            "status": "active",
            "owner_id": "desk-alpha",
            "owner_type": "desk",
            "risk_policy_ref": "rp-001",
            "currency": "USD",
            "budget": 100000,
            "created_at": "2026-05-15T00:00:00Z",
        }
        other_pool = {
            "id": "pool-beta",
            "pool_id": "pool-beta",
            "name": "Beta Pool",
            "status": "suspended",
            "risk_policy_ref": "rp-002",
        }
        store._canonical.list_records = lambda dataset, **kwargs: (
            (True, [seed_pool, other_pool]) if dataset == "capital_pools" else (False, [])
        )
        monkeypatch.setattr(bff_main, "read_store", store)

        client = TestClient(bff_main.app)
        resp = client.get(
            "/bff/capital-pools?status=active&risk_policy_ref=rp-001",
            headers=HEADERS,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data"] == body["items"]
        assert body["page_info"] == {"next_page_token": None, "total": 1}
        assert body["items"][0]["id"] == "pool-alpha"
        assert body["items"][0]["pool_id"] == "pool-alpha"
        assert body["items"][0]["budget"] == 100000
        assert body["meta"]["surfaces"]["capital_pool_list"]["source"] == "canonical"


def test_bff_capital_pools_create_requires_idempotency_key() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/capital-pools",
                json={"name": "Test Pool"},
                headers=HEADERS,
            )
            assert resp.status_code == 400, resp.text
        finally:
            bff_main.read_store = original


def test_bff_capital_pools_create_returns_201(monkeypatch) -> None:
    monkeypatch.setattr(bff_main, "create_capital_pool", _owner_pool_create)
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/capital-pools",
                json={"name": "Test Pool Alpha"},
                headers={**HEADERS, "Idempotency-Key": "create-pool-001"},
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert body["name"] == "Test Pool Alpha"
            assert "pool_id" in body or "id" in body
        finally:
            bff_main.read_store = original


def test_bff_capital_pools_create_idempotency_replay(monkeypatch) -> None:
    monkeypatch.setattr(bff_main, "create_capital_pool", _owner_pool_create)
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            idem_key = "create-pool-replay-001"
            payload = {"name": "Replay Pool"}
            first = client.post(
                "/bff/capital-pools",
                json=payload,
                headers={**HEADERS, "Idempotency-Key": idem_key},
            )
            assert first.status_code == 201, first.text
            second = client.post(
                "/bff/capital-pools",
                json=payload,
                headers={**HEADERS, "Idempotency-Key": idem_key},
            )
            assert second.status_code == 201, second.text
            assert first.json()["name"] == second.json()["name"]
        finally:
            bff_main.read_store = original


def test_bff_capital_pool_detail_404_unknown() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/capital-pools/nonexistent-pool", headers=HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            bff_main.read_store = original


def test_bff_capital_pool_patch_requires_idempotency_key() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.patch(
                "/bff/capital-pools/pool-001",
                json={"status": "suspended"},
                headers=HEADERS,
            )
            assert resp.status_code == 400, resp.text
        finally:
            bff_main.read_store = original


def test_bff_capital_pool_detail_with_seed_data() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        store = CapitalRankingTestReadPorts(
            allow_local_snapshot_fallback=True,
        )
        seed_pool = {
            "id": "pool-alpha",
            "pool_id": "pool-alpha",
            "name": "Alpha Pool",
            "status": "active",
            "risk_policy_ref": "rp-001",
            "capital_allocation": 100000,
            "currency": "USD",
            "max_drawdown_pct": 15.0,
        }
        store._canonical.list_records = lambda dataset, **kwargs: (
            (True, [seed_pool]) if dataset == "capital_pools" else (False, [])
        )
        store._canonical.capital_pool = lambda pool_id: (
            (True, seed_pool) if pool_id == "pool-alpha" else (True, None)
        )
        store._canonical.bindings_for_pool = lambda pool_id: (True, [])
        bff_main.read_store = store
        bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
        try:
            client = TestClient(bff_main.app)
            resp = client.get("/bff/capital-pools/pool-alpha", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["data"]["name"] == "Alpha Pool"
            assert "bindings" in body["data"]
            assert "meta" in body
        finally:
            bff_main.read_store = original


def test_bff_capital_pool_detail_reports_binding_surface_unavailable(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        store = CapitalRankingTestReadPorts(
            allow_local_snapshot_fallback=False,
        )
        seed_pool = {
            "id": "pool-alpha",
            "pool_id": "pool-alpha",
            "name": "Alpha Pool",
            "status": "active",
            "risk_policy_ref": "rp-001",
        }
        store._canonical.list_records = lambda dataset, **kwargs: (
            (True, [seed_pool]) if dataset == "capital_pools" else (False, [])
        )
        store._canonical.capital_pool = lambda pool_id: (
            (True, seed_pool) if pool_id == "pool-alpha" else (True, None)
        )
        store._canonical.bindings_for_pool = lambda pool_id: (False, [])
        monkeypatch.setattr(bff_main, "read_store", store)

        client = TestClient(bff_main.app)
        resp = client.get("/bff/capital-pools/pool-alpha", headers=HEADERS)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data"]["id"] == "pool-alpha"
        assert body["data"]["bindings"] == []
        surfaces = body["meta"]["surfaces"]
        assert surfaces["capital_pool_detail"]["status"] == "ok"
        assert surfaces["persona_bindings"]["status"] == "unavailable"
        assert body["meta"]["degradation"]["persona_bindings_reason"] == (
            "persona bindings are currently unavailable."
        )


def test_bff_capital_pool_detail_503_when_pool_source_unavailable(monkeypatch) -> None:
    for env_name in (
        "PANTHEON_CAPITAL_API_URL",
        "PANTHEON_CAPITAL_SERVICE_URL",
        "PANTHEON_BFF_CAPITAL_POOL_STORE",
        "PANTHEON_GOVERNANCE_DATA_DIR",
    ):
        monkeypatch.setenv(env_name, "")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")

    with tempfile.TemporaryDirectory() as td:
        store = CapitalRankingTestReadPorts(
            allow_local_snapshot_fallback=False,
        )
        monkeypatch.setattr(bff_main, "read_store", store)

        client = TestClient(bff_main.app)
        resp = client.get("/bff/capital-pools/pool-missing", headers=HEADERS)

        assert resp.status_code == 503, resp.text
        assert _error(resp)["code"] == "DEPENDENCY_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Ranking formulas
# ---------------------------------------------------------------------------

def test_bff_ranking_formulas_list_returns_200() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/ranking-formulas", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "meta" in body
            assert "page_info" in body
        finally:
            bff_main.read_store = original


def test_bff_ranking_formula_create_returns_201() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/ranking-formulas",
                json={"name": "Momentum Formula", "description": "Ranks by momentum"},
                headers={**HEADERS, "Idempotency-Key": "rf-create-001"},
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()["data"]
            assert body["name"] == "Momentum Formula"
            formula_id = body.get("formula_id") or body.get("id")
            assert formula_id
            detail = client.get(f"/bff/ranking-formulas/{formula_id}", headers=HEADERS)
            assert detail.status_code == 200, detail.text
            assert detail.json()["data"]["name"] == "Momentum Formula"
        finally:
            bff_main.read_store = original


def test_bff_ranking_formula_create_idempotency_replay() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            payload = {"name": "Replay Formula", "description": "same request replay"}
            headers = {**HEADERS, "Idempotency-Key": "rf-replay-001"}
            first = client.post("/bff/ranking-formulas", json=payload, headers=headers)
            second = client.post("/bff/ranking-formulas", json=payload, headers=headers)
            assert first.status_code == 201, first.text
            assert second.status_code == 201, second.text
            assert first.json()["data"]["id"] == second.json()["data"]["id"]
            assert len(client.get("/bff/ranking-formulas", headers=HEADERS).json()["data"]) == 1
        finally:
            bff_main.read_store = original


def test_bff_ranking_formula_create_requires_name() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/ranking-formulas",
                json={"description": "Missing name"},
                headers={**HEADERS, "Idempotency-Key": "rf-no-name-001"},
            )
            assert resp.status_code == 422, resp.text
        finally:
            bff_main.read_store = original


def test_bff_ranking_formula_detail_404_unknown() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create_resp = client.post(
                "/bff/ranking-formulas",
                json={"name": "Existing Formula", "description": "establishes local store"},
                headers={**HEADERS, "Idempotency-Key": "rf-detail-seed-001"},
            )
            assert create_resp.status_code == 201, create_resp.text
            resp = client.get("/bff/ranking-formulas/nonexistent-formula", headers=HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            bff_main.read_store = original


def test_bff_ranking_formula_action_accepted() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create_resp = client.post(
                "/bff/ranking-formulas",
                json={"name": "Action Test Formula", "description": "for action test"},
                headers={**HEADERS, "Idempotency-Key": "rf-action-create-001"},
            )
            assert create_resp.status_code == 201, create_resp.text
            formula = create_resp.json()["data"]
            formula_id = formula.get("formula_id") or formula.get("id")
            action_resp = client.post(
                f"/bff/actions/ranking-formula/{formula_id}/activate",
                json={},
                headers={**HEADERS, "Idempotency-Key": "rf-action-001"},
            )
            assert action_resp.status_code == 202, action_resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# Rebalances
# ---------------------------------------------------------------------------

def test_bff_rebalances_list_returns_200() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/rebalances", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "meta" in body
        finally:
            bff_main.read_store = original


def test_bff_rebalance_create_requires_capital_pool_id() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/rebalances",
                json={"reason": "quarterly"},
                headers={**HEADERS, "Idempotency-Key": "rb-no-pool-001"},
            )
            assert resp.status_code == 422, resp.text
        finally:
            bff_main.read_store = original


def test_bff_rebalance_create_rejects_legacy_payload_without_lineage() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/rebalances",
                json={"capital_pool_id": "pool-alpha", "reason": "quarterly rebalance"},
                headers={**HEADERS, "Idempotency-Key": "rb-create-001"},
            )
            assert resp.status_code == 422, resp.text
        finally:
            bff_main.read_store = original


def test_bff_rebalance_detail_404_unknown() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/rebalances/nonexistent-rb", headers=HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            bff_main.read_store = original


def test_bff_rebalance_create_requires_idempotency_key() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/rebalances",
                json={"capital_pool_id": "pool-alpha"},
                headers=HEADERS,
            )
            assert resp.status_code == 400, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# Rankings (full-spec long tail)
# ---------------------------------------------------------------------------

def test_bff_rankings_list_returns_200() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/rankings", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "meta" in body
        finally:
            bff_main.read_store = original


def test_bff_ranking_detail_404_unknown() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/rankings/nonexistent-rk", headers=HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            bff_main.read_store = original


def test_bff_ranking_action_404_for_unknown_entity() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/rankings/nonexistent-rk/actions/refresh",
                json={},
                headers={**HEADERS, "Idempotency-Key": "rk-action-001"},
            )
            assert resp.status_code == 404, resp.text
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# Read store unit tests
# ---------------------------------------------------------------------------

def test_read_store_default_write_through_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = CapitalRankingTestReadPorts()

        pool = store.create_capital_pool(
            pool_id="pool-write-through",
            name="Write Through Pool",
            actor_id="op-1",
        )
        assert store.get_capital_pool(pool["pool_id"])["name"] == "Write Through Pool"
        assert [p["pool_id"] for p in store.list_capital_pools()] == ["pool-write-through"]
        patched_pool = store.patch_capital_pool(
            pool["pool_id"],
            patch={"status": "active"},
            actor_id="op-1",
        )
        assert patched_pool is not None
        assert store.get_capital_pool(pool["pool_id"])["status"] == "active"

        formula = store.create_ranking_formula(
            name="Write Through Formula",
            description="desc",
            actor_id="op-1",
        )
        formula_id = formula["formula_id"]
        assert store.get_ranking_formula(formula_id)["name"] == "Write Through Formula"
        assert [f["formula_id"] for f in store.list_ranking_formulas()] == [formula_id]
        patched_formula = store.patch_ranking_formula(
            formula_id,
            patch={"status": "inactive"},
            actor_id="op-1",
        )
        assert patched_formula is not None
        assert store.get_ranking_formula(formula_id)["status"] == "inactive"

        rebalance = store.create_rebalance(
            capital_pool_id=pool["pool_id"],
            actor_id="op-1",
            reason="write-through rebalance",
        )
        rebalance_id = rebalance["rebalance_id"]
        assert store.get_rebalance(rebalance_id)["reason"] == "write-through rebalance"
        assert [r["rebalance_id"] for r in store.list_rebalances(pool_id=pool["pool_id"])] == [rebalance_id]


def test_read_store_ranking_formula_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = CapitalRankingTestReadPorts(
            allow_local_snapshot_fallback=True,
        )
        assert store.list_ranking_formulas() == []
        formula = store.create_ranking_formula(
            name="Test Formula",
            description="desc",
            actor_id="op-1",
        )
        formula_id = formula["formula_id"]
        assert store.get_ranking_formula(formula_id) is not None
        formulas = store.list_ranking_formulas()
        assert len(formulas) == 1
        patched = store.patch_ranking_formula(formula_id, patch={"status": "inactive"}, actor_id="op-1")
        assert patched is not None
        assert patched["status"] == "inactive"


def test_read_store_rebalance_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = CapitalRankingTestReadPorts(
            allow_local_snapshot_fallback=False,
        )
        assert store.list_rebalances() == []
        rb = store.create_rebalance(
            capital_pool_id="pool-001",
            actor_id="op-1",
            reason="test rebalance",
        )
        rb_id = rb["rebalance_id"]
        assert store.get_rebalance(rb_id) is not None
        results = store.list_rebalances(pool_id="pool-001")
        assert len(results) == 1
        empty = store.list_rebalances(pool_id="pool-other")
        assert len(empty) == 0


def test_read_store_rankings_empty_by_default() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = CapitalRankingTestReadPorts(
            allow_local_snapshot_fallback=True,
        )
        assert store.list_rankings() == []
        assert store.get_ranking("nonexistent") is None


# ---------------------------------------------------------------------------
# Dedicated Ranking Router (ACG-01-012)
# ---------------------------------------------------------------------------

def test_ranking_router_routes_uniqueness() -> None:
    from fastapi import FastAPI
    from management_read_models.ranking_router import create_ranking_formulas_router

    router = create_ranking_formulas_router()
    app = FastAPI()
    app.include_router(router)

    routes = [(getattr(r, "methods", set()), getattr(r, "path", "")) for r in router.routes]
    list_routes = [r for r in routes if r[1] == "/bff/ranking-formulas" and "GET" in r[0]]
    detail_routes = [r for r in routes if r[1] == "/bff/ranking-formulas/{formula_id}" and "GET" in r[0]]
    create_routes = [r for r in routes if r[1] == "/bff/ranking-formulas" and "POST" in r[0]]
    patch_routes = [r for r in routes if r[1] == "/bff/ranking-formulas/{formula_id}" and "PATCH" in r[0]]

    assert len(list_routes) == 1
    assert len(detail_routes) == 1
    assert len(create_routes) == 1
    assert len(patch_routes) == 1
    assert len(router.routes) == 4


def test_ranking_router_standalone_crud_and_idempotency() -> None:
    from fastapi import FastAPI
    from management_read_models.ranking_router import create_ranking_formulas_router

    with tempfile.TemporaryDirectory() as td:
        store = CapitalRankingTestReadPorts(
            allow_local_snapshot_fallback=True,
        )
        router = create_ranking_formulas_router(get_read_store=lambda: store)
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        # 1. Create formula
        create_payload = {
            "name": "Alpha Momentum Formula",
            "description": "Momentum ranking formula",
            "params": {"window": 20, "factor": "momentum"},
        }
        create_headers = {**HEADERS, "Idempotency-Key": "rf-acg-001"}
        resp = client.post("/bff/ranking-formulas", json=create_payload, headers=create_headers)
        assert resp.status_code == 201, resp.text
        created = resp.json()["data"]
        formula_id = created["formula_id"]
        assert created["name"] == "Alpha Momentum Formula"

        # 2. Replay same request with same idempotency key
        replay_resp = client.post("/bff/ranking-formulas", json=create_payload, headers=create_headers)
        assert replay_resp.status_code == 201
        assert replay_resp.json()["data"]["formula_id"] == formula_id

        # 3. Get detail
        get_resp = client.get(f"/bff/ranking-formulas/{formula_id}", headers=HEADERS)
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["name"] == "Alpha Momentum Formula"

        # 4. List formulas
        list_resp = client.get("/bff/ranking-formulas", headers=HEADERS)
        assert list_resp.status_code == 200
        assert len(list_resp.json()["data"]) == 1

        # 5. Patch formula
        patch_resp = client.patch(
            f"/bff/ranking-formulas/{formula_id}",
            json={"status": "inactive", "description": "Updated description"},
            headers=HEADERS,
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["data"]["status"] == "inactive"
        assert patch_resp.json()["data"]["description"] == "Updated description"

        # 6. Reject body idempotency key
        body_key_resp = client.post(
            "/bff/ranking-formulas",
            json={"name": "Bad Key Formula", "idempotencyKey": "bad-key"},
            headers=HEADERS,
        )
        assert body_key_resp.status_code == 400

        # 7. Require name
        no_name_resp = client.post(
            "/bff/ranking-formulas",
            json={"description": "No name"},
            headers={**HEADERS, "Idempotency-Key": "rf-acg-noname"},
        )
        assert no_name_resp.status_code == 422

        # 8. 404 on unknown formula id
        not_found_resp = client.get("/bff/ranking-formulas/rf-unknown-999", headers=HEADERS)
        assert not_found_resp.status_code == 404

