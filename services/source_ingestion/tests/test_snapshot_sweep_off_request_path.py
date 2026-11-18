"""The hourly universe sweep runs on a background thread, never inside a lookup.

Events only, no elapsed-time assertions (SOURCE-SNAPSHOT-SWEEP-OFF-REQUEST-PATH-20261119).
"""
from __future__ import annotations

import datetime as dt
import inspect
import json
import threading
from pathlib import Path

from services.source_ingestion import requirement_state as m
from services.source_ingestion import snapshot_universe as su
from services.source_ingestion.tests.bench_market_snapshot_incremental_read import _line

WAIT = 30  # generous join bound; not a performance threshold


def _universe(tmp_path: Path) -> su.SnapshotUniverse:
    journal = tmp_path / "latest_market_snapshots.jsonl"
    journal.write_text("".join(_line(f"{1000 + i}.TWSE", 0) + "\n" for i in range(6)), encoding="utf-8")
    today = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)).date().isoformat()
    state = tmp_path / "latest_market_snapshots.jsonl.universe.json"
    state.write_text(json.dumps({
        "schema_version": su.STATE_SCHEMA_VERSION, "updated_at": "2026-11-18T00:00:00Z",
        "members": {"1000": {"symbol": "1000.TWSE", "last_requested_date": today, "origin": "request"}},
        "transitions": [], "transitions_total": 0}), encoding="utf-8")
    store = m.LatestMarketSnapshotStore(journal, max_closes=60)
    return su.SnapshotUniverse(store, state_path=state, storage_root=tmp_path / "storage")


def _hold_withdraw(monkeypatch, universe):
    inside, release = threading.Event(), threading.Event()
    original = universe.store.withdraw_symbols

    def held(*a, **kw):
        inside.set()
        assert release.wait(WAIT)
        return original(*a, **kw)

    monkeypatch.setattr(universe.store, "withdraw_symbols", held)
    return inside, release


def test_no_request_handler_calls_sweep():
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(su.SnapshotUniverse.snapshot_for_request)))
    called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not {"sweep", "maybe_sweep"} & called


def test_stored_lookup_returns_while_sweep_is_held(tmp_path, monkeypatch):
    universe = _universe(tmp_path)
    inside, release = _hold_withdraw(monkeypatch, universe)
    sweeper = threading.Thread(target=universe.sweep, kwargs={"force": True})
    sweeper.start()
    try:
        assert inside.wait(WAIT)  # sweep is parked inside withdraw_symbols, holding the universe lock
        snapshot = universe.snapshot_for_request("1003.TWSE")  # stored non member
        assert snapshot is not None and not release.is_set()
        assert universe.snapshot_for_request("1000.TWSE") is not None  # stored member
        assert not release.is_set()
    finally:
        release.set()
        sweeper.join(WAIT)
    assert not sweeper.is_alive()
    assert universe.store.get("1003.TWSE") is None  # sweep semantics unchanged: non member withdrawn
    assert universe.store.get("1000.TWSE") is not None


def test_seeding_lookup_waits_for_sweep_in_progress(tmp_path, monkeypatch):
    universe = _universe(tmp_path)
    inside, release = _hold_withdraw(monkeypatch, universe)
    seeded = threading.Event()
    result: list = []

    def lookup():
        result.append(universe.snapshot_for_request("9999.TWSE"))  # not stored: must seed
        seeded.set()

    sweeper = threading.Thread(target=universe.sweep, kwargs={"force": True})
    sweeper.start()
    assert inside.wait(WAIT)
    reader = threading.Thread(target=lookup)
    reader.start()
    try:
        # the seeding lookup is queued on the universe lock until the sweep releases it
        assert universe._lock.acquire(blocking=False) is False
        assert not seeded.is_set()
    finally:
        release.set()
        sweeper.join(WAIT)
        reader.join(WAIT)
    assert seeded.is_set() and result == [None]  # no stored partitions for 9999: a miss, after the sweep


def test_deferred_touch_applied_after_sweep(tmp_path, monkeypatch):
    universe = _universe(tmp_path)
    inside, release = _hold_withdraw(monkeypatch, universe)
    sweeper = threading.Thread(target=universe.sweep, kwargs={"force": True})
    sweeper.start()
    assert inside.wait(WAIT)
    universe.snapshot_for_request("1004.TWSE")
    assert "1004.TWSE" in universe._deferred_touch.values() or "1004" in universe._deferred_touch
    release.set()
    sweeper.join(WAIT)
    assert not universe._deferred_touch


def test_background_schedule_single_flight_and_stops(tmp_path):
    universe = _universe(tmp_path)
    ran = threading.Event()
    universe.maybe_sweep = ran.set  # type: ignore[method-assign]
    assert universe.start_background_sweeps(interval_seconds=0.01) is True
    assert universe.start_background_sweeps(interval_seconds=0.01) is False  # one thread per process
    assert ran.wait(WAIT)
    thread = universe._sweep_thread
    universe.stop_background_sweeps()
    assert thread is not None and not thread.is_alive()
