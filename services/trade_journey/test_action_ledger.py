from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from services.trade_journey.action_ledger import MemoryActionLedger, PostgresActionLedger, make_action_ledger


def test_memory_ledger_reserves_once_and_replays_completed_receipt() -> None:
    ledger = MemoryActionLedger()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: ledger.reserve("same-key", "hash-1")[0], range(8)))
    assert results.count("new") == 1
    assert results.count("pending") == 7
    ledger.complete("same-key", "hash-1", {"receipt_id": "r-1"})
    assert ledger.reserve("same-key", "hash-1") == ("replay", {"receipt_id": "r-1"})
    assert ledger.reserve("same-key", "other") == ("conflict", None)


def test_factory_requires_dsn_and_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="DSN"):
        make_action_ledger({"PANTHEON_TRADE_JOURNEY_ACTION_LEDGER_BACKEND": "postgres"})
    with pytest.raises(ValueError, match="memory or postgres"):
        make_action_ledger({"PANTHEON_TRADE_JOURNEY_ACTION_LEDGER_BACKEND": "sqlite"})


def test_postgres_ledger_survives_restart_when_database_available() -> None:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    key = f"tj-ledger-{uuid4()}"
    first = PostgresActionLedger(dsn)
    assert first.reserve(key, "hash") == ("new", None)
    first.complete(key, "hash", {"receipt_id": "durable"})
    restarted = PostgresActionLedger(dsn)
    assert restarted.reserve(key, "hash") == ("replay", {"receipt_id": "durable"})
