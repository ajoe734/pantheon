#!/usr/bin/env python3
"""Regression: every capital pool the list surface returns must resolve in the detail lookup.

Canonical `capital_pools.json` can be a JSON *object* keyed by an internal id that differs from a
pool's public `pool_id` (when the payload is an object, `_normalize_records` keys by the object's
own keys, not `pool_id`/`id`). `list_capital_pools()` exposes each pool as `pool_id or id`, but
`get_capital_pool()` did a direct dict-key lookup and 404'd those pools even though they appeared in
the list — which took the whole dev console offline when clicked. The adapter must resolve by the
same identity the list exposes.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from read_store import ReadSurfaceStore


@pytest.fixture(autouse=True)
def _enable_market_persona_seed(monkeypatch):
    """These detail contracts intentionally exercise the retired fixture."""

    monkeypatch.setenv("PANTHEON_BFF_MARKET_PERSONA_SEED", "1")


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_detail_resolves_pools_keyed_by_internal_id():
    tracked = {k: os.environ.get(k) for k in ("PANTHEON_GOVERNANCE_DATA_DIR",)}
    try:
        with tempfile.TemporaryDirectory() as td:
            gov = Path(td) / "governance"
            # Object payload: the first pool is keyed by an internal id != its pool_id (the shape
            # that used to 404 on detail); the second is conventionally keyed (always worked).
            _write_json(
                gov / "capital_pools.json",
                {
                    "internal-key-abc": {
                        "pool_id": "paper-pool-persona-20260704-5d946ca4",
                        "name": "Cron Scope Smoke 2 paper capital pool",
                        "status": "active",
                        "owner_id": "capital-service",
                    },
                    "pool-rescue-1": {
                        "pool_id": "pool-rescue-1",
                        "id": "pool-rescue-1",
                        "name": "Dev Paper Pool",
                        "status": "active",
                    },
                },
            )
            os.environ["PANTHEON_GOVERNANCE_DATA_DIR"] = str(gov)

            store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=False,
            )

            listed = {pool["id"] for pool in store.list_capital_pools()}
            assert "paper-pool-persona-20260704-5d946ca4" in listed
            assert "pool-rescue-1" in listed

            # Every id the list returns must resolve in detail (previously the internal-keyed pool
            # returned None → the BFF raised 404 → the console cascaded offline).
            for pool_id in listed:
                pool = store.get_capital_pool(pool_id)
                assert pool is not None, f"detail 404 for listed pool {pool_id}"
                assert pool["id"] == pool_id

            print("✅ every listed capital pool resolves via get_capital_pool")
    finally:
        for key, value in tracked.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_detail_resolves_bff_local_dev_store_pool():
    # Persona-created paper pools (POST /bff/personas) persist to the BFF local dev write store
    # (persistenceMode=bff_local_dev_store), keyed internally by an arbitrary key. They appear in
    # list_capital_pools() but get_capital_pool() previously only checked the local overlay +
    # canonical, so their detail endpoint 404'd. get_capital_pool() must resolve against the same
    # BFF-write source the list uses.
    pool_id = "paper-pool-persona-20260704-5d946ca4"
    with tempfile.TemporaryDirectory() as td:
        store_path = os.path.join(td, "read_surfaces.json")
        _write_json(
            Path(store_path),
            {
                "capital_pools": {
                    "some-internal-key": {
                        "id": pool_id,
                        "pool_id": pool_id,
                        "name": "Cron Scope Smoke 2 paper capital pool",
                        "status": "active",
                        "risk_policy_ref": "risk-policy:low:paper",
                        "persistenceMode": "bff_local_dev_store",
                        "metadata": {"created_via": "POST /bff/personas"},
                    }
                }
            },
        )
        store = ReadSurfaceStore(store_path, allow_local_snapshot_fallback=False)

        listed = {pool["id"] for pool in store.list_capital_pools()}
        assert pool_id in listed, f"{pool_id} missing from list_capital_pools"

        pool = store.get_capital_pool(pool_id)
        assert pool is not None, "detail 404 for bff_local_dev_store pool"
        assert pool["id"] == pool_id
        print("✅ bff_local_dev_store pool resolves via get_capital_pool")


def test_every_listed_pool_including_market_defaults_has_a_detail():
    # The core invariant: any pool the list surface returns MUST resolve in the detail endpoint.
    # Market-persona default pools (pool-us-equity-paper, pool-crypto-paper, …) are surfaced by
    # list_capital_pools(include_market_persona_defaults=True) but used to 404 on detail.
    with tempfile.TemporaryDirectory() as td:
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        listed = store.list_capital_pools(include_market_persona_defaults=True)
        assert listed, "expected market-persona default pools in the list"
        missing = [
            str(p.get("id") or p.get("pool_id"))
            for p in listed
            if store.get_capital_pool(str(p.get("id") or p.get("pool_id"))) is None
        ]
        assert not missing, f"listed pools with no detail: {missing}"
        print(f"✅ all {len(listed)} listed pools resolve in detail (incl. market defaults)")


if __name__ == "__main__":
    test_detail_resolves_pools_keyed_by_internal_id()
    test_detail_resolves_bff_local_dev_store_pool()
    test_every_listed_pool_including_market_defaults_has_a_detail()
