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

sys.path.insert(0, os.path.dirname(__file__))
from read_store import ReadSurfaceStore


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


if __name__ == "__main__":
    test_detail_resolves_pools_keyed_by_internal_id()
