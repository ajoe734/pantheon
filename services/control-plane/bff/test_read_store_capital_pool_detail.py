#!/usr/bin/env python3
"""Capital-pool list/detail invariants through the narrow capital read port."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from ports import CapitalPoolPort


def _port(records):
    return CapitalPoolPort(pools_provider=lambda: list(records), bindings_provider=lambda: [])


def test_detail_resolves_public_pool_id_when_provider_identity_differs() -> None:
    port = _port(
        [
            {
                "internal_id": "internal-key-abc",
                "pool_id": "paper-pool-persona-20260704-5d946ca4",
                "name": "Cron Scope Smoke 2 paper capital pool",
                "status": "active",
            },
            {
                "pool_id": "pool-rescue-1",
                "id": "pool-rescue-1",
                "name": "Dev Paper Pool",
                "status": "active",
            },
        ]
    )

    listed = {str(pool.get("pool_id") or pool.get("id")) for pool in port.list_capital_pools()}
    assert listed == {"paper-pool-persona-20260704-5d946ca4", "pool-rescue-1"}
    for pool_id in listed:
        detail = port.get_capital_pool(pool_id)
        assert detail is not None, f"detail missing for listed pool {pool_id}"
        assert str(detail.get("pool_id") or detail.get("id")) == pool_id


def test_detail_resolves_explicit_local_dev_record_from_typed_double() -> None:
    pool_id = "paper-pool-persona-20260704-5d946ca4"
    port = _port(
        [
            {
                "id": pool_id,
                "pool_id": pool_id,
                "name": "Cron Scope Smoke 2 paper capital pool",
                "status": "active",
                "persistenceMode": "test_double",
            }
        ]
    )

    assert port.get_capital_pool(pool_id)["id"] == pool_id


def test_every_listed_pool_has_a_detail() -> None:
    records = [
        {"id": "pool-us-equity-paper", "status": "active"},
        {"id": "pool-crypto-paper", "status": "active"},
        {"id": "pool-main", "status": "active"},
    ]
    port = _port(records)

    missing = [
        str(pool.get("pool_id") or pool.get("id"))
        for pool in port.list_capital_pools()
        if port.get_capital_pool(str(pool.get("pool_id") or pool.get("id"))) is None
    ]
    assert missing == []
