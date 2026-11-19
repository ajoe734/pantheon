"""/livez must not wait for the hourly snapshot sweep (SOURCE-INGEST-HOURLY-SWEEP-PROCESS-STALL-20261119).

Cause: /livez -> health_payload -> metrics -> journal_stats -> SnapshotUniverse.stats ran ON THE EVENT LOOP and took
the universe RLock that the sweep holds while seeding static members.  Structural property proved here with events and
a lock-acquisition counter (no wall clock threshold): stats() never acquires the universe lock.
"""
from __future__ import annotations

import threading
from pathlib import Path

from services.source_ingestion import requirement_state as m
from services.source_ingestion import snapshot_universe as su


class _CountingLock:
    def __init__(self) -> None:
        self._inner = threading.RLock()
        self.acquires = 0

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        self.acquires += 1
        return self._inner.acquire(blocking, timeout)

    def release(self) -> None:
        self._inner.release()

    __enter__ = lambda self: (self.acquire(), self)[1]  # noqa: E731

    def __exit__(self, *exc) -> None:
        self.release()


def _universe(tmp_path: Path) -> su.SnapshotUniverse:
    store = m.LatestMarketSnapshotStore(tmp_path / "latest_market_snapshots.jsonl")
    return su.SnapshotUniverse(store, state_path=tmp_path / "universe.json", storage_root=tmp_path)


def test_stats_does_not_take_the_universe_lock(tmp_path):
    universe = _universe(tmp_path)
    universe._lock = counting = _CountingLock()
    universe.stats()
    universe.store.journal_stats()
    assert counting.acquires == 0


def test_stats_returns_while_the_sweep_holds_the_lock(tmp_path, monkeypatch):
    universe = _universe(tmp_path)
    monkeypatch.setenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "2317.TWSE")
    in_seed, release = threading.Event(), threading.Event()

    def blocked_seed(symbol):
        in_seed.set()
        release.wait(30)  # safety bound only; the assertions below are event ordered
        return None

    monkeypatch.setattr(universe, "seed", blocked_seed)
    sweeper = threading.Thread(target=lambda: universe._write_state() or universe.sweep(force=True))
    sweeper.start()
    try:
        assert in_seed.wait(30)
        result = {}
        reader = threading.Thread(target=lambda: result.update(universe.store.journal_stats()))
        reader.start()
        reader.join(10)
        finished_during_hold = not reader.is_alive() and not release.is_set()
    finally:
        release.set()
        sweeper.join(30)
    assert finished_during_hold
    assert "snapshot_universe" in result
