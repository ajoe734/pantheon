"""SOURCE-SNAPSHOT-CURRENT-RECORD-20261117: one current record per (symbol, dataset) in SQLite.

Size, blocking and equality claims run on a hosted-shaped copy: 10,700 live keys of 60 closes each
(real-length ``content_ref``), collector batches of 500 records.  Never a three-symbol fixture.
Set ``SNAPSHOT_EVIDENCE_OUT=<file>`` to dump the measured values the evidence file quotes.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sqlite3
import statistics
import subprocess
import sys
import textwrap
import threading
import time
import types
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.source_ingestion import requirement_state as rs
from services.source_ingestion.requirement_state import (
    DAILY_SNAPSHOT_DATASET,
    LatestMarketSnapshot,
    LatestMarketSnapshotStore,
    MarketSnapshotPoint,
    MarketSnapshotStateError,
    MarketSnapshotWriteBusy,
    SNAPSHOT_FILE_BUDGET_RATIO,
    SNAPSHOT_LIVE_BUDGET_BYTES,
    SNAPSHOT_RECORD_BUDGET_BYTES,
    SNAPSHOT_RECORD_ALARM_BYTES,
    SNAPSHOT_WAL_BUDGET_BYTES,
)
from services.source_ingestion.snapshot_current_store import SqliteSnapshotBackend

REPO_ROOT = Path(__file__).resolve().parents[3]
E7E_REF = "e7ec353d6b9917b4d92cca766d4cd439a47edc2d"
E7E_FIXTURE = Path(__file__).with_name("fixtures") / "requirement_state_e7ec353.py"
E7E_SIZE = 73367
E7E_BLOB = "83070f80fc19b8131d9a489d10d20863db09c785"
E7E_SHA256 = "cb8a872aeac9476ff126701bec21fa791c0c6e1d4a3f029acb54281700fad4ff"

HOSTED_KEYS = 10_700
CLOSES = 60
BATCH = 500
MEASURED: dict[str, object] = {}


def _trading_days(count: int) -> list[date]:
    days, day = [], date(2026, 8, 3)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


DAYS = _trading_days(CLOSES + 5)


def _ref(symbol: str, day: date) -> str:
    digest = hashlib.sha256(f"{symbol}|{day}".encode()).hexdigest()
    return f"twse://openapi/STOCK_DAY_ALL/{symbol}/{day.isoformat()}/{digest}"


def _point(symbol: str, day: date, close: float, **extra) -> MarketSnapshotPoint:
    return MarketSnapshotPoint(
        event_time=f"{day.isoformat()}T05:30:00Z",
        close=close,
        source_id="twse-openapi-stock-day-all",
        connector_id="tw-twse-price-history",
        content_ref=_ref(symbol, day),
        ingest_run_id=f"ingest-run-{day.isoformat()}-0f3a9c21b7d44e56",
        market="TW",
        **extra,
    )


def _symbol(index: int) -> str:
    return f"{index:05d}.TWSE"


def _snapshot(index: int, *, last: int = CLOSES) -> LatestMarketSnapshot:
    symbol = _symbol(index)
    days = DAYS[last - CLOSES : last]
    points = tuple(
        _point(symbol, day, round(20 + index * 0.01 + n * 0.05, 2), **({"open": 19.5} if index % 7 == 0 else {}))
        for n, day in enumerate(days)
    )
    return LatestMarketSnapshot(
        symbol=symbol,
        points=points,
        observed_at=f"{days[-1].isoformat()}T10:00:00Z",
        calendar_evidence={"calendar_id": "twse-2026", "status": "open", "trade_date": days[-1].isoformat()},
    )


def _record(index: int, day_index: int):
    symbol, day = _symbol(index), DAYS[day_index]
    return SimpleNamespace(
        source_id="twse-openapi-stock-day-all",
        connector_id="tw-twse-price-history",
        content_ref=_ref(symbol, day),
        is_rejected=False,
        created_at=None,
        metadata={
            "normalized_row": {
                "symbol": symbol,
                "close": round(20 + index * 0.01 + day_index * 0.05, 2),
                "event_time": f"{day.isoformat()}T05:30:00Z",
                "market": "TW",
                "calendar_evidence": {"calendar_id": "twse-2026", "status": "open", "trade_date": day.isoformat()},
            }
        },
    )


def _new_day_batches(day_index: int, *, keys: int = HOSTED_KEYS):
    for start in range(0, keys, BATCH):
        yield [_record(i, day_index) for i in range(start, min(start + BATCH, keys))]


@pytest.fixture(scope="module")
def hosted(tmp_path_factory):
    """A journal shaped like the hosted one: HOSTED_KEYS live keys of 60 closes (v1 lines, one per key)."""
    root = tmp_path_factory.mktemp("hosted-shaped")
    journal = root / "latest_market_snapshots.jsonl"
    snapshots = {}
    started = time.monotonic()
    with journal.open("w", encoding="utf-8") as handle:
        for index in range(HOSTED_KEYS):
            snap = _snapshot(index)
            snapshots[(snap.symbol, snap.dataset)] = snap
            handle.write(rs.LatestMarketSnapshotStore._envelope_line(snap.to_dict()) + "\n")
    MEASURED["build_seconds"] = round(time.monotonic() - started, 1)
    return SimpleNamespace(root=root, journal=journal, snapshots=snapshots)


@pytest.fixture(scope="module")
def sqlite_copy(hosted, tmp_path_factory):
    """sqlite-mode store that adopted a copy of the hosted-shaped journal (S0 seeding)."""
    root = tmp_path_factory.mktemp("sqlite-copy")
    (root / hosted.journal.name).write_bytes(hosted.journal.read_bytes())
    started = time.monotonic()
    store = LatestMarketSnapshotStore(root / hosted.journal.name, store_mode="sqlite", export_min_interval_seconds=10**9)
    MEASURED["seed_seconds"] = round(time.monotonic() - started, 1)
    store._last_export_monotonic = time.monotonic()  # the old-format export is measured by its own tests
    return SimpleNamespace(root=root, store=store, path=root / hosted.journal.name)


def _dump_measured() -> None:
    out = os.environ.get("SNAPSHOT_EVIDENCE_OUT")
    if out:
        Path(out).write_text(json.dumps(MEASURED, indent=2, sort_keys=True), encoding="utf-8")


@pytest.fixture(scope="module", autouse=True)
def _write_measurements():
    yield
    _dump_measured()


# ---- 1. reproduction on the journal ------------------------------------------------------------------


def test_journal_reproduction_line_size_growth_and_compaction_block(hosted, tmp_path):
    """Acceptance 1: size of one 60-close line, file before/after one full day of replacements, lock time."""
    path = tmp_path / hosted.journal.name
    path.write_bytes(hosted.journal.read_bytes())
    store = LatestMarketSnapshotStore(path, store_mode="journal", compact_min_bytes=10**12)
    sample = next(iter(hosted.snapshots.values()))
    line_bytes = len(rs.LatestMarketSnapshotStore._envelope_line(sample.to_dict()).encode()) + 1
    record_bytes = len(rs._snapshot_row(sample)[1])
    before = path.stat().st_size
    for number, batch in enumerate(_new_day_batches(CLOSES)):
        store.append_normalized_records(batch, ingest_run_id=f"day-{number}", observed_at="2026-11-17T10:00:00Z")
    after_day = path.stat().st_size
    held = {}

    def _compact():
        began = time.monotonic()
        store.compact(force=True)
        held["compact_seconds"] = time.monotonic() - began

    _compact()
    after_compaction = path.stat().st_size
    MEASURED["journal"] = {
        "keys": HOSTED_KEYS,
        "closes_per_key": CLOSES,
        "v1_line_bytes_one_60_close_key": line_bytes,
        "compact_record_bytes_one_60_close_key": record_bytes,
        "file_bytes_before_full_day": before,
        "file_bytes_after_full_day_of_replacements": after_day,
        "file_bytes_after_compaction": after_compaction,
        "superseded_lines_after_day": HOSTED_KEYS,
        "compaction_exclusive_lock_seconds": round(held["compact_seconds"], 2),
    }
    assert after_day > before * 1.9  # one superseded copy per key sits in the file until compaction
    assert after_compaction < after_day
    assert held["compact_seconds"] > 0
    assert record_bytes < line_bytes


# ---- 2. equality with the journal on every key --------------------------------------------------------


def test_every_key_of_the_hosted_shaped_copy_equals_the_journal(hosted, sqlite_copy):
    journal_store = LatestMarketSnapshotStore(hosted.journal, store_mode="journal", compact_min_bytes=10**12)
    sql = sqlite_copy.store
    assert sql.snapshot_current_stats()["record_count"] == HOSTED_KEYS
    mismatches = 0
    for (symbol, dataset), expected in hosted.snapshots.items():
        stored = sql.get(symbol)
        jrnl = journal_store.get(symbol)
        same = (
            stored.closes == jrnl.closes == expected.closes
            and stored.points == jrnl.points
            and stored.lineage == jrnl.lineage
            and stored.calendar_evidence == jrnl.calendar_evidence
            and stored.snapshot_id == jrnl.snapshot_id == expected.snapshot_id
            and stored.to_public_dict() == jrnl.to_public_dict()
        )
        mismatches += 0 if same else 1
    MEASURED["equality"] = {"keys_compared": len(hosted.snapshots), "mismatches": mismatches}
    assert mismatches == 0
    assert len(sql.reload()) == HOSTED_KEYS


# ---- 3. size budgets (bytes, not logs) -----------------------------------------------------------------


def test_budget_row_market_snapshot_current_on_hosted_shaped_copy(sqlite_copy):
    stats = sqlite_copy.store.snapshot_current_stats()
    MEASURED["size_after_seed"] = stats
    assert stats["record_count"] == HOSTED_KEYS
    assert stats["max_record_bytes"] <= SNAPSHOT_RECORD_BUDGET_BYTES, stats
    assert stats["live_bytes"] <= SNAPSHOT_LIVE_BUDGET_BYTES, stats
    assert stats["db_bytes"] <= SNAPSHOT_FILE_BUDGET_RATIO * stats["live_bytes"], stats
    assert stats["wal_bytes"] <= SNAPSHOT_WAL_BUDGET_BYTES, stats
    assert stats["budget_alarms"] == []


def test_over_budget_record_raises_the_health_alarm(tmp_path):
    store = LatestMarketSnapshotStore(tmp_path / "s.jsonl", store_mode="sqlite")
    fat = LatestMarketSnapshot(
        symbol="2330.TWSE",
        points=(_point("2330.TWSE", DAYS[0], 1.0),),
        observed_at="2026-11-17T10:00:00Z",
        calendar_evidence={"padding": os.urandom(SNAPSHOT_RECORD_ALARM_BYTES).hex()},  # incompressible
    )
    store._push_snapshots({(fat.symbol, fat.dataset): fat})
    stats = store.snapshot_current_stats()
    assert stats["max_record_bytes"] > SNAPSHOT_RECORD_ALARM_BYTES
    assert "snapshot_record_over_16kb" in stats["budget_alarms"]
    assert "snapshot_current" in store.journal_stats()


# ---- 4. replacement in place, zero rows for an unchanged window, blocking bounds -----------------------


_READER_SCRIPT = textwrap.dedent(
    """
    import json, random, sys, time
    sys.path.insert(0, {root!r})
    from pathlib import Path
    from services.source_ingestion.requirement_state import LatestMarketSnapshotStore

    store = LatestMarketSnapshotStore({path!r}, store_mode="sqlite")
    stop, go, rng, ms = Path({stop!r}), Path({go!r}), random.Random(11), []
    print("ready", flush=True)
    while not stop.exists():
        began = time.perf_counter()
        assert store.get("%05d.TWSE" % rng.randrange({keys})) is not None
        if go.exists():  # calls before the replacement starts are warm-up (page cache, imports), not measured
            ms.append((time.perf_counter() - began) * 1000)
        time.sleep(0.001)  # a poller, not a spin loop: at most ~1,000 reads per second
    ms.sort()
    print(json.dumps({{"calls": len(ms), "max_ms": ms[-1], "p99_ms": ms[int(len(ms) * 0.99) - 1]}}))
    """
)


def test_full_day_replacement_keeps_one_row_per_key_and_never_blocks_readers(sqlite_copy, tmp_path):
    """A reader loop (a separate process, unthrottled, plus an in-process request-paced thread) runs while
    every one of the 10,700 keys is replaced in 22 transactions of 500."""
    import gc

    store, backend = sqlite_copy.store, sqlite_copy.store._backend
    symbols = [_symbol(i) for i in range(HOSTED_KEYS)]
    stop_file, go_file = tmp_path / "stop", tmp_path / "go"
    reader = subprocess.Popen(
        [sys.executable, "-c", _READER_SCRIPT.format(root=str(REPO_ROOT), path=str(sqlite_copy.path), stop=str(stop_file), go=str(go_file), keys=HOSTED_KEYS)],
        stdout=subprocess.PIPE, text=True, cwd=REPO_ROOT,
    )
    assert reader.stdout.readline().strip() == "ready"
    stop, warm = threading.Event(), threading.Event()
    read_ms: list[float] = []
    failures: list[str] = []
    # The fixture keeps ~640k point objects alive; a full interpreter GC pass over them is not store blocking.
    gc.collect()
    gc.freeze()

    def _reader():  # request-paced, as HTTP readers are; in-process so it shares the writer's GIL
        rng = random.Random(7)
        store.get(symbols[0])  # opens this thread's read connection (once per thread, not per call)
        warm.set()
        while not stop.is_set():
            began = time.perf_counter()
            try:
                assert store.get(rng.choice(symbols)) is not None
            except BaseException as exc:  # noqa: BLE001 - reported below
                failures.append(repr(exc))
                return
            read_ms.append((time.perf_counter() - began) * 1000)
            time.sleep(0.002)

    txn_ms: list[float] = []
    maintain_ms: list[float] = []
    original_txn, original_maintain = backend.transaction, backend.maintain

    def _timed():
        class _Timed:
            def __enter__(self_inner):
                self_inner.cm = original_txn()
                self_inner.began = time.perf_counter()
                return self_inner.cm.__enter__()

            def __exit__(self_inner, *exc):
                result = self_inner.cm.__exit__(*exc)  # includes COMMIT (fsync)
                txn_ms.append((time.perf_counter() - self_inner.began) * 1000)
                return result

        return _Timed()

    def _timed_maintain():
        began = time.perf_counter()
        original_maintain()
        maintain_ms.append((time.perf_counter() - began) * 1000)

    backend.transaction, backend.maintain = _timed, _timed_maintain
    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    assert warm.wait(timeout=30)
    updated = 0
    call_seconds: list[float] = []
    go_file.write_text("go")
    time.sleep(0.2)  # let the process reader record its first measured calls before the first write
    try:
        for batch in _new_day_batches(CLOSES):
            began = time.perf_counter()
            out = store.append_normalized_records(batch, ingest_run_id="day-61", observed_at="2026-11-17T10:00:00Z")
            call_seconds.append(time.perf_counter() - began)
            updated += out["updated_snapshot_count"]
    finally:
        gc.unfreeze()
        stop.set()
        thread.join(timeout=10)
        stop_file.write_text("stop")
        backend.transaction, backend.maintain = original_txn, original_maintain
    process_stats = json.loads(reader.communicate(timeout=60)[0].strip().splitlines()[-1])
    stats = store.snapshot_current_stats()
    p99_txn = sorted(txn_ms)[-(-len(txn_ms) * 99 // 100) - 1]  # nearest-rank p99 (the max for 22 batches)
    MEASURED["full_replacement"] = {
        "keys_replaced": updated,
        "batches_of_500": len(txn_ms),
        "write_txn_ms_all": [round(v) for v in txn_ms],
        "write_txn_ms_max": round(max(txn_ms), 1),
        "write_txn_ms_p50": round(statistics.median(txn_ms), 1),
        "write_txn_ms_p99": round(p99_txn, 1),
        "maintenance_step_ms_max": round(max(maintain_ms), 1),
        "append_call_seconds_max": round(max(call_seconds), 2),
        "reader_process_calls": process_stats["calls"],
        "reader_process_ms_max": round(process_stats["max_ms"], 2),
        "reader_process_ms_p99": round(process_stats["p99_ms"], 2),
        "reader_thread_calls": len(read_ms),
        "reader_thread_ms_max": round(max(read_ms), 2),
        "stats_after": stats,
    }
    assert not failures, failures
    assert updated == HOSTED_KEYS
    assert stats["record_count"] == HOSTED_KEYS  # replaced in place: no superseded version after commit
    assert process_stats["calls"] > 100 and len(read_ms) > 100
    assert process_stats["max_ms"] <= 100.0, f"reader process call blocked {process_stats['max_ms']:.1f} ms"
    assert max(read_ms) <= 100.0, f"reader thread call blocked {max(read_ms):.1f} ms"
    assert p99_txn <= 250.0, f"500-key write transaction p99 {p99_txn:.1f} ms"
    assert max(maintain_ms) <= 50.0 * 4, f"maintenance step {max(maintain_ms):.1f} ms"
    assert stats["max_record_bytes"] <= SNAPSHOT_RECORD_BUDGET_BYTES
    assert stats["live_bytes"] <= SNAPSHOT_LIVE_BUDGET_BYTES
    assert stats["db_bytes"] <= SNAPSHOT_FILE_BUDGET_RATIO * stats["live_bytes"], stats
    assert stats["wal_bytes"] <= SNAPSHOT_WAL_BUDGET_BYTES
    assert store.get(_symbol(5)).closes[-1] == round(20 + 5 * 0.01 + CLOSES * 0.05, 2)
    assert len(store.get(_symbol(5)).closes) == CLOSES  # SOURCE_INGEST_MARKET_SNAPSHOT_MAX_CLOSES still applies


def test_unchanged_window_writes_zero_rows(tmp_path):
    store = LatestMarketSnapshotStore(tmp_path / "s.jsonl", store_mode="sqlite")
    batch = [_record(i, 3) for i in range(BATCH)]
    first = store.append_normalized_records(batch, ingest_run_id="r1", observed_at="2026-11-17T10:00:00Z")
    assert first["updated_snapshot_count"] == BATCH
    rows = store.snapshot_current_stats()["record_count"]
    changes = store._backend.total_changes()
    # same window, same lineage (the run id is part of every point's lineage, as in the journal)
    again = store.append_normalized_records(batch, ingest_run_id="r1", observed_at="2026-11-17T11:00:00Z")
    assert again["updated_snapshot_count"] == 0 and again["snapshots"] == []
    assert store._backend.total_changes() == changes  # no row written
    assert store.snapshot_current_stats()["record_count"] == rows == BATCH
    # backend level: the same digest is a no-op too, a different digest replaces exactly one row
    backend = store._backend
    key = (_symbol(0), DAILY_SNAPSHOT_DATASET)
    digest, text, observed = backend.get(*key)
    assert backend.apply({key: (digest, text, observed)}) == {"written": 0, "unchanged": 1, "deleted": 0}
    assert backend.apply({key: ("0" * 64, text, observed)})["written"] == 1
    assert backend.stats()["record_count"] == BATCH


def test_second_writer_gets_a_retryable_error_after_two_seconds(tmp_path):
    store = LatestMarketSnapshotStore(tmp_path / "s.jsonl", store_mode="sqlite")
    store.append_normalized_records([_record(0, 3)], ingest_run_id="r1", observed_at="2026-11-17T10:00:00Z")
    other = sqlite3.connect(store.sqlite_path, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")  # another process holds the writer lock
    try:
        began = time.monotonic()
        with pytest.raises(MarketSnapshotWriteBusy) as raised:
            store.append_normalized_records([_record(0, 4)], ingest_run_id="r2", observed_at="2026-11-17T11:00:00Z")
        waited = time.monotonic() - began
        assert raised.value.retryable is True
        assert 1.8 <= waited <= 4.0, waited
        MEASURED["second_writer"] = {"waited_seconds": round(waited, 2), "error": type(raised.value).__name__}
        # readers are not blocked by the held writer lock
        assert store.get(_symbol(0)).closes == (round(20 + 3 * 0.05, 2),)
    finally:
        other.execute("ROLLBACK")
        other.close()
    out = store.append_normalized_records([_record(0, 4)], ingest_run_id="r2", observed_at="2026-11-17T11:00:00Z")
    assert out["updated_snapshot_count"] == 1  # the retry succeeds


def test_row_changed_by_another_writer_between_read_and_commit_is_a_retryable_conflict(tmp_path):
    store = LatestMarketSnapshotStore(tmp_path / "s.jsonl", store_mode="sqlite")
    store.append_normalized_records([_record(0, 3)], ingest_run_id="r1", observed_at="2026-11-17T10:00:00Z")
    real = store._project_symbol

    def _interfere(symbol, prior, incoming, dataset, observed):
        candidate = real(symbol, prior, incoming, dataset, observed)
        rival = LatestMarketSnapshotStore(tmp_path / "s.jsonl", store_mode="sqlite")
        rival.append_normalized_records([_record(0, 9)], ingest_run_id="rival", observed_at="2026-11-17T12:00:00Z")
        return candidate

    store._project_symbol = _interfere
    with pytest.raises(MarketSnapshotWriteBusy):
        store.append_normalized_records([_record(0, 4)], ingest_run_id="r2", observed_at="2026-11-17T11:00:00Z")
    store._project_symbol = real
    assert store.get(_symbol(0)).closes[-1] == round(20 + 9 * 0.05, 2)  # the rival's version, never a torn mix


# ---- 5. crash safety -----------------------------------------------------------------------------------

_CRASH_SCRIPT = textwrap.dedent(
    """
    import os, sys
    sys.path.insert(0, {root!r})
    from services.source_ingestion.snapshot_current_store import SnapshotTransaction
    from services.source_ingestion.tests import test_snapshot_current_record_store as t
    from services.source_ingestion.requirement_state import LatestMarketSnapshotStore

    store = LatestMarketSnapshotStore({path!r}, store_mode="sqlite")
    real_put_many = SnapshotTransaction.put_many

    def dying_put_many(self, rows):
        items = list(rows.items())
        real_put_many(self, dict(items[:250]))  # half of a 500-key transaction is written, nothing committed
        os._exit(9)

    SnapshotTransaction.put_many = dying_put_many
    store.append_normalized_records([t._record(i, 5) for i in range(500)], ingest_run_id="doomed",
                                    observed_at="2026-11-17T12:00:00Z")
    """
)


def test_kill_inside_a_500_key_transaction_leaves_the_previous_complete_state(tmp_path):
    path = tmp_path / "s.jsonl"
    store = LatestMarketSnapshotStore(path, store_mode="sqlite")
    store.append_normalized_records([_record(i, 3) for i in range(BATCH)], ingest_run_id="r1", observed_at="2026-11-17T10:00:00Z")
    before = store._backend.digests()
    done = subprocess.run(
        [sys.executable, "-c", _CRASH_SCRIPT.format(root=str(REPO_ROOT), path=str(path))],
        capture_output=True, text=True, timeout=120, cwd=REPO_ROOT,
    )
    assert done.returncode == 9, done.stderr[-2000:]
    reopened = LatestMarketSnapshotStore(path, store_mode="sqlite")
    assert reopened._backend.digests() == before  # previous complete state; not one of the 250 written rows is visible
    assert reopened.get(_symbol(0)).closes == (round(20 + 3 * 0.05, 2),)
    assert reopened.snapshot_current_stats()["record_count"] == BATCH
    # and the store keeps working: the batch the killed writer never committed applies cleanly
    out = reopened.append_normalized_records([_record(i, 5) for i in range(BATCH)], ingest_run_id="again", observed_at="2026-11-17T12:00:00Z")
    assert out["updated_snapshot_count"] == BATCH


def test_failure_inside_the_transaction_rolls_back_every_row(tmp_path):
    store = LatestMarketSnapshotStore(tmp_path / "s.jsonl", store_mode="sqlite")
    store.append_normalized_records([_record(i, 3) for i in range(20)], ingest_run_id="r1", observed_at="2026-11-17T10:00:00Z")
    before = store._backend.digests()
    from services.source_ingestion.snapshot_current_store import SnapshotTransaction

    original = SnapshotTransaction.put_many

    def failing(self, rows):
        items = list(rows.items())
        original(self, dict(items[:10]))  # ten rows replaced inside the open transaction...
        raise RuntimeError("boom")  # ...then the writer fails before commit

    SnapshotTransaction.put_many = failing
    try:
        with pytest.raises(RuntimeError):
            store.append_normalized_records([_record(i, 4) for i in range(20)], ingest_run_id="r2", observed_at="2026-11-17T11:00:00Z")
    finally:
        SnapshotTransaction.put_many = original
    assert store._backend.digests() == before


def test_withdrawals_are_one_transaction_on_the_same_row(tmp_path):
    store = LatestMarketSnapshotStore(tmp_path / "s.jsonl", store_mode="sqlite")
    minute = [
        SimpleNamespace(
            source_id="finmind:kbar", connector_id="kbar-1m", content_ref=f"intraday://{n}", is_rejected=False,
            created_at=None,
            metadata={"normalized_row": {"dataset": "tw_price_1m", "symbol": "2330.TWSE", "close": 500.0 + n,
                                          "event_time": f"2026-11-10T01:0{n}:00Z"}, "dataset": "tw_price_1m"},
        )
        for n in range(1, 4)
    ]
    store.append_normalized_records(minute, ingest_run_id="m", observed_at="2026-11-10T08:00:00Z", dataset="tw_price_1m")
    store.append_normalized_records([_record(0, 3)], ingest_run_id="d", observed_at="2026-11-17T10:00:00Z")
    transactions = []
    original = store._backend.transaction

    def counting():
        transactions.append(1)
        return original()

    store._backend.transaction = counting
    assert store.withdraw_sessions("2330.TWSE", "tw_price_1m", ["2026-11-10"], observed_at="2026-11-10T09:00:00Z") is True
    assert len(transactions) == 1
    assert store.get("2330.TWSE", "tw_price_1m") is None  # nothing left of that session: row deleted
    assert store.snapshot_current_stats()["record_count"] == 1
    transactions.clear()
    assert store.withdraw_symbols([_symbol(0)]) == [_symbol(0)]
    assert len(transactions) == 1 and store.live_symbols() == []
    with pytest.raises(MarketSnapshotStateError):
        store.withdraw_sessions("2330.TWSE", DAILY_SNAPSHOT_DATASET, ["2026-11-10"])


def test_delete_entry_point_removes_every_dataset_of_a_delisted_symbol_in_one_transaction(tmp_path):
    store = LatestMarketSnapshotStore(tmp_path / "s.jsonl", store_mode="sqlite")
    minute = [SimpleNamespace(source_id="s", connector_id="c", content_ref="r://1", is_rejected=False, created_at=None,
                              metadata={"normalized_row": {"dataset": "tw_price_1m", "symbol": _symbol(1), "close": 5.0,
                                                           "event_time": "2026-11-10T01:01:00Z"}, "dataset": "tw_price_1m"})]
    store.append_normalized_records([_record(1, 3), _record(2, 3)], ingest_run_id="d", observed_at="2026-11-17T10:00:00Z")
    store.append_normalized_records(minute, ingest_run_id="m", observed_at="2026-11-17T10:00:00Z", dataset="tw_price_1m")
    assert store.snapshot_current_stats()["record_count"] == 3
    transactions = []
    original = store._backend.transaction
    store._backend.transaction = lambda: (transactions.append(1), original())[1]
    assert store.delete_symbols([_symbol(1), "99999.TWSE"], reason="expired") == [_symbol(1)]
    assert len(transactions) == 1
    assert store.get(_symbol(1)) is None and store.get(_symbol(1), "tw_price_1m") is None
    assert store.get(_symbol(2)) is not None
    assert store.snapshot_current_stats()["record_count"] == 1
    with pytest.raises(MarketSnapshotStateError):
        store.delete_symbols([_symbol(2)], reason="because")


@pytest.mark.parametrize("mode", ["journal", "dual"])
def test_delete_entry_point_in_journal_and_dual_mode(tmp_path, mode):
    store = LatestMarketSnapshotStore(tmp_path / "s.jsonl", store_mode=mode)
    store.append_normalized_records([_record(1, 3), _record(2, 3)], ingest_run_id="d", observed_at="2026-11-17T10:00:00Z")
    assert store.delete_symbols([_symbol(1)], reason="delisted") == [_symbol(1)]
    assert store.get(_symbol(1)) is None and store.get(_symbol(2)) is not None
    assert LatestMarketSnapshotStore(tmp_path / "s.jsonl", store_mode="journal").get(_symbol(1)) is None


# ---- 6. the three rollout modes -------------------------------------------------------------------------


def _batch_days(store, days, keys=30):
    for day_index in days:
        store.append_normalized_records(
            [_record(i, day_index) for i in range(keys)], ingest_run_id=f"r{day_index}", observed_at=f"{DAYS[day_index]}T10:00:00Z"
        )


def test_default_mode_is_journal_and_behaviour_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("MARKET_SNAPSHOT_STORE", raising=False)
    store = LatestMarketSnapshotStore(tmp_path / "s.jsonl")
    assert store.store_mode == "journal"
    _batch_days(store, [3, 4])
    assert not Path(str(tmp_path / "s.jsonl") + ".current.sqlite3").exists()
    assert len((tmp_path / "s.jsonl").read_text().splitlines()) == 60  # appended journal: one line per version
    assert "snapshot_current" not in store.journal_stats()
    assert store.journal_stats()["store_mode"] == "journal"


def test_flag_selects_the_mode_and_invalid_value_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_SNAPSHOT_STORE", "sqlite")
    assert LatestMarketSnapshotStore(tmp_path / "a.jsonl").store_mode == "sqlite"
    monkeypatch.setenv("MARKET_SNAPSHOT_STORE", "Dual")
    assert LatestMarketSnapshotStore(tmp_path / "b.jsonl").store_mode == "dual"
    monkeypatch.setenv("MARKET_SNAPSHOT_STORE", "postgres")
    with pytest.raises(ValueError, match="MARKET_SNAPSHOT_STORE"):
        LatestMarketSnapshotStore(tmp_path / "c.jsonl")


def test_dual_mode_writes_both_reads_sqlite_and_reports_mismatches(tmp_path):
    path = tmp_path / "s.jsonl"
    store = LatestMarketSnapshotStore(path, store_mode="dual")
    _batch_days(store, [3, 4, 5])
    assert len(path.read_text().splitlines()) >= 30  # journal still written
    stats = store.snapshot_current_stats()
    assert stats["record_count"] == 30 and stats["mismatch_count"] == 0
    assert store.get(_symbol(1)).closes[-1] == round(20 + 0.01 + 5 * 0.05, 2)  # served from SQLite
    assert LatestMarketSnapshotStore(path, store_mode="journal").get(_symbol(1)).snapshot_id == store.get(_symbol(1)).snapshot_id
    # a divergence between the stores is counted on read and shown in the health payload
    divergent = _snapshot(1, last=CLOSES + 1)
    store._push_snapshots({(divergent.symbol, divergent.dataset): divergent})
    assert store.get(_symbol(1)) is not None
    health = store.journal_stats()
    assert health["snapshot_current"]["mismatch_count"] == 1 and health["store_mode"] == "dual"
    # a restart re-syncs the shadow with the journal and recounts: back to 0
    assert LatestMarketSnapshotStore(path, store_mode="dual").snapshot_current_stats()["mismatch_count"] == 0


def test_dual_mode_adopts_an_existing_journal_as_the_shadow(tmp_path):
    path = tmp_path / "s.jsonl"
    journal = LatestMarketSnapshotStore(path, store_mode="journal")
    _batch_days(journal, [3, 4])
    dual = LatestMarketSnapshotStore(path, store_mode="dual")
    assert dual.snapshot_current_stats()["record_count"] == 30
    assert dual.snapshot_current_stats()["mismatch_count"] == 0


def test_sqlite_mode_adopts_the_journal_then_serves_sqlite_and_rollback_is_the_flag(tmp_path):
    path = tmp_path / "s.jsonl"
    journal = LatestMarketSnapshotStore(path, store_mode="journal")
    _batch_days(journal, [3, 4])
    expected = {k: v.snapshot_id for k, v in journal.reload().items()}
    sql = LatestMarketSnapshotStore(path, store_mode="sqlite")
    assert {k: v.snapshot_id for k, v in sql.reload().items()} == expected
    _batch_days(sql, [5, 6])
    assert sql.get(_symbol(3)).closes[-1] == round(20 + 3 * 0.01 + 6 * 0.05, 2)
    # rollback: flag back to journal, same code, reads the one-line-per-key export
    back = LatestMarketSnapshotStore(path, store_mode="journal")
    assert {k: v.snapshot_id for k, v in back.reload().items()} == {k: v.snapshot_id for k, v in sql.reload().items()}
    assert len(path.read_text().splitlines()) == 30  # one line per key, no superseded copy
    _batch_days(back, [7])  # and the journal path keeps appending
    assert back.get(_symbol(3)).closes[-1] == round(20 + 3 * 0.01 + 7 * 0.05, 2)


def test_sqlite_mode_corrupt_record_fails_closed_with_a_readable_reason(tmp_path):
    store = LatestMarketSnapshotStore(tmp_path / "s.jsonl", store_mode="sqlite")
    _batch_days(store, [3], keys=2)
    raw = sqlite3.connect(store.sqlite_path, isolation_level=None)
    # damage one stored byte of key 0 only
    raw.execute("UPDATE snapshot_current SET record = CAST(substr(record, 1, 12) || X'ff' || substr(record, 14) AS BLOB) WHERE symbol=?", (_symbol(0),))
    raw.close()
    with pytest.raises(MarketSnapshotStateError, match=r"market snapshot record .* 00000\.TWSE"):
        store.get(_symbol(0))
    assert store.get(_symbol(1)) is not None  # one bad row does not take the other keys down


# ---- 7. compat export and the previous reader (hosted version e7ec353d6) -------------------------------


def _old_codec() -> types.ModuleType:
    raw = E7E_FIXTURE.read_bytes()
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if len(raw) != E7E_SIZE or blob != E7E_BLOB or hashlib.sha256(raw).hexdigest() != E7E_SHA256:
        raise AssertionError("frozen e7ec353d6 codec integrity mismatch")
    module = types.ModuleType("services.source_ingestion._frozen_" + E7E_REF)
    module.__file__ = str(E7E_FIXTURE)
    sys.modules[module.__name__] = module
    exec(compile(raw, str(E7E_FIXTURE), "exec"), module.__dict__)
    return module


def test_frozen_fixture_is_the_hosted_version_from_git():
    shown = subprocess.run(
        ["git", "show", f"{E7E_REF}:services/source_ingestion/requirement_state.py"],
        capture_output=True, cwd=REPO_ROOT,
    )
    if shown.returncode != 0:
        pytest.skip("git object e7ec353d6 not available in this checkout")
    assert shown.stdout == E7E_FIXTURE.read_bytes()


def _rich_records():
    """Daily closes with calendar evidence and opens plus a collector-relayed close."""
    lineage = {
        "collector_source_id": "collector-tw-twse-price-history",
        "exchange_endpoint": "https://www.twse.com.tw/exchangeReport/MI_INDEX",
        "bundle_sha256": "a" * 64,
        "trade_date": DAYS[7].isoformat(),
    }
    provisional = _record(0, 7)
    provisional.metadata["normalized_row"].pop("calendar_evidence")
    provisional.metadata[rs.COLLECTOR_CLOSE_KEY] = {**lineage, "provisional": True, "official": False}
    provisional.source_id = lineage["collector_source_id"]
    with_open = _record(1, 7)
    with_open.metadata["normalized_row"]["open"] = 19.5
    return [provisional, with_open]


def test_previous_reader_reads_the_sqlite_mode_export_and_returns_the_same_data(tmp_path):
    old = _old_codec()
    path = tmp_path / "s.jsonl"
    store = LatestMarketSnapshotStore(path, store_mode="sqlite")
    _batch_days(store, [3, 4, 5, 6], keys=60)
    store.append_normalized_records(_rich_records(), ingest_run_id="rich", observed_at=f"{DAYS[7]}T10:00:00Z")
    store.withdraw_symbols([_symbol(59)])  # a withdrawn key must not reappear for the old reader
    assert store.export_compat() is True
    old_store = old.LatestMarketSnapshotStore(path)
    old_view = old_store.reload()
    new_view = store.reload()
    assert set(old_view) == set(new_view) and len(new_view) == 59
    for key, snapshot in new_view.items():
        theirs = old_view[key]
        assert theirs.closes == snapshot.closes
        assert [p.to_dict() for p in theirs.points] == [p.to_dict() for p in snapshot.points]
        assert theirs.lineage == snapshot.lineage and theirs.snapshot_id == snapshot.snapshot_id
        assert theirs.to_public_dict() == snapshot.to_public_dict()
        assert old_store.get(snapshot.symbol).to_public_dict() == snapshot.to_public_dict()
    # exactly one line per key: nothing superseded is ever in the file the old reader sees
    assert len(path.read_text().splitlines()) == 59


def test_new_code_reads_a_journal_written_by_the_previous_version_in_every_mode(tmp_path):
    old = _old_codec()
    path = tmp_path / "s.jsonl"
    writer = old.LatestMarketSnapshotStore(path)
    for day_index in (3, 4, 5):
        writer.append_normalized_records(
            [_record(i, day_index) for i in range(40)], ingest_run_id=f"old{day_index}", observed_at=f"{DAYS[day_index]}T10:00:00Z"
        )
    writer.append_normalized_records(_rich_records(), ingest_run_id="rich", observed_at=f"{DAYS[7]}T10:00:00Z")
    expected = {key: snap.to_public_dict() for key, snap in writer.reload().items()}
    assert len(path.read_text().splitlines()) > len(expected)  # a genuinely appended old-format journal
    for mode in ("journal", "dual", "sqlite"):
        copy = tmp_path / mode / "s.jsonl"
        copy.parent.mkdir()
        copy.write_bytes(path.read_bytes())
        store = LatestMarketSnapshotStore(copy, store_mode=mode)
        assert {key: snap.to_public_dict() for key, snap in store.reload().items()} == expected, mode
        for (symbol, _dataset), public in expected.items():
            assert store.get(symbol).to_public_dict() == public, (mode, symbol)


def test_export_is_atomic_a_failed_rename_keeps_the_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "s.jsonl"
    store = LatestMarketSnapshotStore(path, store_mode="sqlite")
    _batch_days(store, [3], keys=5)
    good = path.read_bytes()
    _batch_days(store, [4], keys=5, )  # exports again (interval 0)
    good = path.read_bytes()

    def broken(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    assert store.export_compat() is False
    monkeypatch.undo()
    assert path.read_bytes() == good
    assert not path.with_name(path.name + ".export.tmp").exists()
    assert "export failed" in store.snapshot_current_stats()["last_export_error"]
    assert store.export_compat() is True and store.snapshot_current_stats().get("last_export_error") is None


def test_export_runs_in_the_background_at_most_once_per_interval(tmp_path):
    path = tmp_path / "s.jsonl"
    store = LatestMarketSnapshotStore(path, store_mode="sqlite", export_min_interval_seconds=3600)
    _batch_days(store, [3], keys=5)
    for _ in range(100):  # the first write exports in a thread
        if store.snapshot_current_stats()["last_export_at"]:
            break
        time.sleep(0.05)
    first = path.read_bytes()
    assert len(first.splitlines()) == 5
    _batch_days(store, [4], keys=5)  # inside the interval: SQLite is current, the old-format file waits
    assert path.read_bytes() == first
    assert store.snapshot_current_stats()["export_pending"] is True
    assert store.flush_export() is True  # rollback preparation / shutdown forces it
    assert path.read_bytes() != first and store.snapshot_current_stats()["export_pending"] is False


# ---- 8. health payload ---------------------------------------------------------------------------------


def test_health_payload_reports_count_bytes_mismatch_and_last_export(tmp_path):
    store = LatestMarketSnapshotStore(tmp_path / "s.jsonl", store_mode="sqlite")
    _batch_days(store, [3, 4], keys=10)
    stats = store.journal_stats()
    current = stats["snapshot_current"]
    for field in ("record_count", "live_bytes", "file_bytes", "db_bytes", "mismatch_count", "last_export_at", "budget_alarms"):
        assert field in current, field
    assert current["record_count"] == stats["live_snapshot_count"] == 10
    assert current["live_bytes"] > 0 and current["file_bytes"] >= current["db_bytes"] > 0
    assert current["last_export_at"] and current["mismatch_count"] == 0


def test_runtime_health_carries_the_snapshot_store_block(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(tmp_path / "svc"))
    monkeypatch.setenv("MARKET_SNAPSHOT_STORE", "sqlite")
    for name in [m for m in sys.modules if m.startswith("services.source_ingestion.main")]:
        sys.modules.pop(name)
    from services.source_ingestion import main as source_main

    body = TestClient(source_main.app).get("/health").json()
    assert body["market_snapshot_store"] == "sqlite"
    assert body["latest_market_snapshot_journal"]["snapshot_current"]["record_count"] == 0


# ---- 9. every constructor / direct reader keeps working in every mode ------------------------------------


@pytest.mark.parametrize("mode", ["journal", "dual", "sqlite"])
def test_snapshot_universe_hooks_work_in_every_mode(tmp_path, mode):
    store = LatestMarketSnapshotStore(tmp_path / "s.jsonl", store_mode=mode)
    members = {_symbol(0), _symbol(1)}
    store.admission = lambda symbol: symbol in members
    store.stats_extra = lambda: {"state": "active"}
    out = store.append_normalized_records(
        [_record(i, 3) for i in range(4)], ingest_run_id="r", observed_at="2026-11-17T10:00:00Z"
    )
    assert out["non_member_symbol_count"] == 2 and out["updated_snapshot_count"] == 2  # a non-member is not written
    assert store.live_symbols() == sorted(members)
    assert store.max_closes == 60 and store.journal_stats()["snapshot_universe"] == {"state": "active"}
    point = store._point_from_record(_record(0, 3), ingest_run_id="seed")
    assert point is not None and point[0] == _symbol(0)
    seeded = store.append_normalized_records(
        [_record(2, 3)], ingest_run_id="seed", observed_at="2026-11-17T10:00:00Z", enforce_admission=False,
        record_context=[("seed-run", "TW")],
    )
    assert seeded["updated_snapshot_count"] == 1
    assert store.withdraw_symbols([_symbol(2), _symbol(0)]) == [_symbol(0), _symbol(2)]
    assert store.live_symbols() == [_symbol(1)]
    assert LatestMarketSnapshotStore(tmp_path / "s.jsonl", store_mode=mode).live_symbols() == [_symbol(1)]


@pytest.mark.parametrize("mode", ["journal", "dual", "sqlite"])
def test_hosted_lifecycle_probe_constructor_reads_the_snapshot_in_every_mode(tmp_path, mode, monkeypatch):
    """services/trade_journey/hosted_lifecycle_probe.py builds LatestMarketSnapshotStore(path) and calls
    reload() then get(symbol); it keeps working whichever mode the Source runs in (the journal file is
    the one-line-per-key export in sqlite mode, so a probe without the flag reads the same data)."""
    path = tmp_path / "s.jsonl"
    writer = LatestMarketSnapshotStore(path, store_mode=mode)
    _batch_days(writer, [3, 4], keys=5)
    for probe_flag in (None, mode):
        if probe_flag is None:
            monkeypatch.delenv("MARKET_SNAPSHOT_STORE", raising=False)
        else:
            monkeypatch.setenv("MARKET_SNAPSHOT_STORE", probe_flag)
        probe = LatestMarketSnapshotStore(path)
        assert len(probe.reload()) == 5
        assert probe.get(_symbol(2)).closes[-1] == round(20 + 2 * 0.01 + 4 * 0.05, 2)


def test_deploy_capture_probe_parses_the_export_with_the_journal_reader(tmp_path):
    """scripts/dev_release_artifacts.py SOURCE_PROBE streams the journal file through
    _market_snapshots_from_lines; the sqlite-mode export is exactly such a file."""
    path = tmp_path / "s.jsonl"
    store = LatestMarketSnapshotStore(path, store_mode="sqlite")
    _batch_days(store, [3, 4], keys=7)
    parsed = rs._market_snapshots_from_lines(path.read_text().splitlines(), path=path)
    assert {k: v.snapshot_id for k, v in parsed.items()} == {k: v.snapshot_id for k, v in store.reload().items()}
    samples = rs.market_snapshot_writer_samples()  # witness bytes stay journal bytes whatever the flag says
    os.environ["MARKET_SNAPSHOT_STORE"] = "sqlite"
    try:
        assert rs.market_snapshot_writer_samples() == samples
    finally:
        os.environ.pop("MARKET_SNAPSHOT_STORE", None)


def test_backend_rejects_an_unsupported_schema_version(tmp_path):
    db = tmp_path / "x.db"
    SqliteSnapshotBackend(db).close()
    raw = sqlite3.connect(db, isolation_level=None)
    raw.execute("UPDATE snapshot_meta SET v='99' WHERE k='schema_version'")
    raw.close()
    from services.source_ingestion.snapshot_current_store import SnapshotStoreError

    with pytest.raises(SnapshotStoreError, match="schema '99' is not supported"):
        SqliteSnapshotBackend(db)
