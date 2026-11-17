"""Snapshot reads never wait for the writer lock (SOURCE-SNAPSHOT-READ-LOCK-FREE-20261117).

Hosted shape: ~17,500 live keys, ~530 MB journal.  A reader thread calls get / journal_stats /
live_symbols every 10 ms while the writer appends a full market batch and while the journal is
compacted in place; the longest single call must stay under 50 ms.
"""

from __future__ import annotations

import gc
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.source_ingestion import requirement_state as rs
from services.source_ingestion.requirement_state import LatestMarketSnapshotStore
from services.source_ingestion.tests import test_snapshot_current_record_store as base

HOSTED_KEYS = int(os.getenv("SNAPSHOT_LOCK_FREE_KEYS", "17500"))
MAX_WAIT_SECONDS = 0.05
MIN_CALLS = 2000
MEASURED: dict[str, object] = {}


def _hosted_journal(root: Path) -> Path:
    journal = root / "latest_market_snapshots.jsonl"
    with journal.open("w", encoding="utf-8") as handle:
        for index in range(HOSTED_KEYS):
            handle.write(LatestMarketSnapshotStore._envelope_line(base._snapshot(index).to_dict()) + "\n")
    return journal


@pytest.fixture(scope="module")
def hosted(tmp_path_factory):
    root = tmp_path_factory.mktemp("lock-free-hosted")
    started = time.monotonic()
    journal = _hosted_journal(root)
    MEASURED["build_seconds"] = round(time.monotonic() - started, 1)
    MEASURED["keys"] = HOSTED_KEYS
    MEASURED["journal_bytes"] = journal.stat().st_size
    return SimpleNamespace(root=root, journal=journal)


def _probe(call, stop: threading.Event, out: dict, *, interval: float = 0.01, min_calls: int = MIN_CALLS):
    """Call ``call`` every ``interval`` until ``stop`` is set and ``min_calls`` were made."""
    longest, calls, errors = 0.0, 0, []
    while not (stop.is_set() and calls >= min_calls):
        began = time.perf_counter()
        try:
            call()
        except Exception as exc:  # pragma: no cover - reported by the assertion
            errors.append(repr(exc))
        longest = max(longest, time.perf_counter() - began)
        calls += 1
        time.sleep(interval)
    out.update(longest=longest, calls=calls, errors=errors)


def _run_with_readers(store, writer, probes):
    stop = threading.Event()
    results = {name: {} for name in probes}
    threads = [
        threading.Thread(target=_probe, args=(call, stop, results[name]), daemon=True)
        for name, call in probes.items()
    ]
    # The fixture keeps ~1M point objects alive; a full interpreter GC pass over them is a
    # process-wide pause, not store blocking (same treatment as test_snapshot_current_record_store).
    for call in probes.values():  # steady state: the health endpoint has already been polled once
        call()
    gc.collect()
    gc.freeze()
    for thread in threads:
        thread.start()
    began = time.monotonic()
    try:
        writer()
    finally:
        stop.set()
        gc.unfreeze()
    writer_seconds = time.monotonic() - began
    for thread in threads:
        thread.join(timeout=120)
    return results, writer_seconds


def _probes(store):
    return {
        "get": lambda: store.get(base._symbol(7)),
        "journal_stats": store.journal_stats,
        "live_symbols": store.live_symbols,
    }


def _assert_bounded(results, label):
    for name, result in results.items():
        assert not result["errors"], (label, name, result["errors"][:3])
        assert result["calls"] >= MIN_CALLS, (label, name, result["calls"])
        assert result["longest"] <= MAX_WAIT_SECONDS, (label, name, result)
    MEASURED[label] = {
        name: {"calls": r["calls"], "longest_call_ms": round(r["longest"] * 1000, 2)} for name, r in results.items()
    }
    MEASURED[label]["host_load_avg"] = os.getloadavg()


def test_reader_does_not_wait_during_full_market_append(hosted, tmp_path):
    path = tmp_path / hosted.journal.name
    shutil.copyfile(hosted.journal, path)
    store = LatestMarketSnapshotStore(path, store_mode="journal", compact_min_bytes=10**12)
    store.reload()
    before = path.stat().st_size
    batch = [base._record(i, base.CLOSES) for i in range(HOSTED_KEYS)]

    def writer():
        store.append_normalized_records(batch, ingest_run_id="day-lock-free", observed_at="2026-11-17T10:00:00Z")

    results, seconds = _run_with_readers(store, writer, _probes(store))
    MEASURED["append_writer_seconds"] = round(seconds, 2)
    MEASURED["append_bytes_written"] = path.stat().st_size - before
    assert path.stat().st_size > before
    _assert_bounded(results, "append")


def test_reader_does_not_wait_during_in_place_compaction_of_300mb(hosted, tmp_path):
    path = tmp_path / hosted.journal.name
    shutil.copyfile(hosted.journal, path)
    store = LatestMarketSnapshotStore(path, store_mode="journal", compact_min_bytes=10**12)
    store.append_normalized_records(
        [base._record(i, base.CLOSES) for i in range(HOSTED_KEYS)],
        ingest_run_id="day-compact",
        observed_at="2026-11-17T10:00:00Z",
    )
    size_before = path.stat().st_size
    assert size_before >= 300 * 1024 * 1024 or HOSTED_KEYS < 17_500
    outcome = {}

    def writer():
        outcome["compacted"] = store.compact(force=True)

    results, seconds = _run_with_readers(store, writer, _probes(store))
    MEASURED["compaction_writer_seconds"] = round(seconds, 2)
    MEASURED["compaction_bytes"] = {"before": size_before, "after": path.stat().st_size}
    assert outcome["compacted"] is True
    _assert_bounded(results, "compaction")


def test_reader_never_sees_a_partial_batch(tmp_path):
    """Every get during a >=1000 key append is the before or the after snapshot, each self-consistent."""
    keys = 1500
    path = tmp_path / "snap.jsonl"
    store = LatestMarketSnapshotStore(path, store_mode="journal", compact_min_bytes=10**12)
    store.append_normalized_records(
        [base._record(i, base.CLOSES - 1) for i in range(keys)],
        ingest_run_id="day-before",
        observed_at="2026-11-16T10:00:00Z",
    )
    symbols = [base._symbol(i) for i in range(keys)]
    before = {s: store.get(s).to_dict() for s in symbols}
    stop = threading.Event()
    seen: list[tuple[str, dict]] = []
    failures: list[str] = []

    def reader():
        while not stop.is_set():
            for symbol in symbols[::7]:
                snap = store.get(symbol)
                if snap is None:
                    failures.append(f"{symbol}: missing")
                    continue
                state = snap.to_dict()
                if state["snapshot_id"] != snap.snapshot_id or snap.points[-1].close != state["closes"][-1] or list(snap.closes) != state["closes"]:
                    failures.append(f"{symbol}: inconsistent")
                seen.append((symbol, state))

    thread = threading.Thread(target=reader)
    thread.start()
    store.append_normalized_records(
        [base._record(i, base.CLOSES) for i in range(keys)],
        ingest_run_id="day-after",
        observed_at="2026-11-17T10:00:00Z",
    )
    stop.set()
    thread.join(timeout=60)
    after = {s: store.get(s).to_dict() for s in symbols}
    assert not failures, failures[:3]
    assert seen
    third = [(s, st["snapshot_id"]) for s, st in seen if st != before[s] and st != after[s]]
    assert not third, f"third state observed: {third[:3]}"
    assert all(before[s] != after[s] for s in symbols)
    MEASURED["partial_batch"] = {"keys": keys, "reads": len(seen), "third_states": len(third)}


def test_published_maps_are_never_mutated_in_place(tmp_path):
    """A map a reader may hold is frozen: every writer path leaves the old map exactly as it was."""
    path = tmp_path / "snap.jsonl"
    store = LatestMarketSnapshotStore(path, store_mode="journal", compact_min_bytes=0, compact_factor=2)
    held: list[tuple[dict, dict]] = []

    def hold():
        published = store._latest_by_symbol
        held.append((published, dict(published)))

    for day in range(3):
        hold()
        store.append_normalized_records(
            [base._record(i, base.CLOSES + day - 2) for i in range(40)],
            ingest_run_id=f"day-{day}",
            observed_at=f"2026-11-1{day}T10:00:00Z",
        )
    hold()
    store.withdraw_symbols([base._symbol(0)])
    hold()
    store.compact(force=True)
    hold()
    store.reload()
    hold()
    for published, copy in held:
        assert published == copy and list(published) == list(copy)
        assert all(published[k] is copy[k] for k in copy)
    assert len({id(p) for p, _ in held}) > 1  # replaced by assignment, not edited


def test_source_has_no_in_place_mutation_of_the_published_map():
    source = Path(rs.__file__).read_text(encoding="utf-8")
    pattern = re.compile(
        r"self\._latest_by_symbol(\[[^\]]+\]\s*(=|\+=)|\.(pop|popitem|update|clear|setdefault)\()|"
        r"del\s+self\._latest_by_symbol"
    )
    assert pattern.findall(source) == []


def test_get_does_not_take_the_writer_lock(tmp_path):
    store = LatestMarketSnapshotStore(tmp_path / "snap.jsonl", store_mode="journal")
    store.append_normalized_records(
        [base._record(i, base.CLOSES) for i in range(5)], ingest_run_id="r", observed_at="2026-11-17T10:00:00Z"
    )
    done = {}

    def read():
        done["get"] = store.get(base._symbol(1))
        done["live"] = store.live_symbols()
        done["stats"] = store.journal_stats()

    with store._lock:  # held by another thread: the reader thread must still finish
        holder = threading.Thread(target=read)
        # RLock is re-entrant for the holder, so prove it from a different thread
        holder.start()
        holder.join(timeout=5)
        assert not holder.is_alive()
    assert done["get"] is not None and base._symbol(1) in done["live"]
    assert done["stats"]["live_snapshot_count"] == 5 and done["stats"]["journal_lines"] == 5


def test_journal_bytes_and_results_match_independent_encoding(tmp_path):
    """Nothing on disk changes: appended lines equal the canonical envelope of each stored snapshot."""
    path = tmp_path / "snap.jsonl"
    store = LatestMarketSnapshotStore(path, store_mode="journal", compact_min_bytes=10**12)
    store.append_normalized_records(
        [base._record(i, base.CLOSES) for i in range(25)], ingest_run_id="r", observed_at="2026-11-17T10:00:00Z"
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 25
    reread = LatestMarketSnapshotStore(path, store_mode="journal").reload()
    assert {k: v.to_dict() for k, v in reread.items()} == {
        (base._symbol(i), "tw_price_daily"): store.get(base._symbol(i)).to_dict() for i in range(25)
    }
    expected = b"".join(
        (LatestMarketSnapshotStore._envelope_line(store.get(base._symbol(i)).to_dict()) + "\n").encode()
        for i in range(25)
    )
    assert path.read_bytes() == expected
    MEASURED["bytes_sha256_25_keys"] = __import__("hashlib").sha256(expected).hexdigest()


def test_error_path_publishes_empty_map_and_reader_gets_none_not_an_exception(tmp_path):
    """After a failed re-read the store publishes {}: get returns None (as before), live_symbols []."""
    path = tmp_path / "snap.jsonl"
    store = LatestMarketSnapshotStore(path, store_mode="journal", compact_min_bytes=10**12)
    store.append_normalized_records(
        [base._record(i, base.CLOSES) for i in range(10)], ingest_run_id="r", observed_at="2026-11-17T10:00:00Z"
    )
    assert store.get(base._symbol(1)) is not None
    path.write_bytes(b'{"not": "a journal line"}\n' + path.read_bytes())
    stop = threading.Event()
    errors: list[str] = []

    def reader():
        while not stop.is_set():
            try:
                store.get(base._symbol(1))
                store.live_symbols()
                store.journal_stats()
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

    thread = threading.Thread(target=reader)
    thread.start()
    with pytest.raises(rs.MarketSnapshotStateError):
        store.reload()
    stop.set()
    thread.join(timeout=10)
    assert not errors, errors[:3]
    assert store.get(base._symbol(1)) is None
    assert store.live_symbols() == []


def test_zz_write_measurements():
    out = os.getenv("SNAPSHOT_LOCK_FREE_MEASURED")
    if out:
        Path(out).write_text(json.dumps(MEASURED, indent=2, default=str), encoding="utf-8")
