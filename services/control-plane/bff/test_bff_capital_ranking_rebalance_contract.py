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

import main as bff_main
from read_store import ReadSurfaceStore

OPERATOR_TOKEN = "Bearer op-2:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}
IDEM_HEADERS = {**HEADERS, "Idempotency-Key": "test-key-001"}


def _fresh_client(td: str):
    bff_main.read_store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=True,
    )
    bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
    bff_main.command_store._update_commands([])
    return TestClient(bff_main.app)


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


def test_bff_capital_pools_create_returns_201() -> None:
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


def test_bff_capital_pools_create_idempotency_replay() -> None:
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
                "/bff/capital-pools/pool-main",
                json={"status": "active"},
                headers=HEADERS,
            )
            assert resp.status_code == 400, resp.text
        finally:
            bff_main.read_store = original


def test_bff_capital_pool_detail_with_seed_data() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        seed_pool = {
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


# ---------------------------------------------------------------------------
# Ranking formulas
# ---------------------------------------------------------------------------

def test_bff_ranking_formulas_list_returns_200() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/ranking/formulas", headers=HEADERS)
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
                "/bff/ranking/formulas",
                json={"name": "Momentum Formula", "description": "Ranks by momentum"},
                headers={**HEADERS, "Idempotency-Key": "rf-create-001"},
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert body["name"] == "Momentum Formula"
            assert "formula_id" in body or "id" in body
        finally:
            bff_main.read_store = original


def test_bff_ranking_formula_create_requires_name() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/ranking/formulas",
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
            resp = client.get("/bff/ranking/formulas/nonexistent-formula", headers=HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            bff_main.read_store = original


def test_bff_ranking_formula_action_accepted() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            create_resp = client.post(
                "/bff/ranking/formulas",
                json={"name": "Action Test Formula", "description": "for action test"},
                headers={**HEADERS, "Idempotency-Key": "rf-action-create-001"},
            )
            assert create_resp.status_code == 201, create_resp.text
            formula_id = create_resp.json().get("formula_id") or create_resp.json().get("id")
            action_resp = client.post(
                f"/bff/ranking/formulas/{formula_id}/actions/activate",
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


def test_bff_rebalance_create_returns_202_with_command_metadata() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/rebalances",
                json={"capital_pool_id": "pool-alpha", "reason": "quarterly rebalance"},
                headers={**HEADERS, "Idempotency-Key": "rb-create-001"},
            )
            assert resp.status_code == 202, resp.text
            body = resp.json()
            assert "rebalance_id" in body
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
        store = ReadSurfaceStore(os.path.join(td, "read_surfaces.json"))

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
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
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
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
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
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        assert store.list_rankings() == []
        assert store.get_ranking("nonexistent") is None
