"""The latest-market-snapshot journal must not grow with history (SOURCE-SNAPSHOT-JOURNAL-BOUNDED-20261115)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import types
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.source_ingestion import requirement_state as current
from services.source_ingestion.connectors.base import SourceRecord
from services.source_ingestion.pipeline import persist_latest_market_snapshots
from services.source_ingestion.requirement_state import LatestMarketSnapshotStore

REPO = Path(__file__).resolve().parents[3]
BASE = date(2026, 10, 1)


def _result(records, run_id, finished="2026-11-10T09:00:00Z"):
    run = SimpleNamespace(
        status=SimpleNamespace(value="completed"), ingest_run_id=run_id,
        to_dict=lambda: {"finished_at": finished},
    )
    return SimpleNamespace(run=run, records=tuple(records))


def _daily(symbol, day, close=100.0, connector="tw-twse-tpex-official-market"):
    return SourceRecord(
        source_id=f"{connector}-{day}", connector_id=connector, source_type="market",
        title=f"{symbol} daily", content_ref=f"ref://{symbol}/{day}",
        metadata={"dataset": "tw_price_daily", "symbol": symbol, "close": close,
                  "event_time": f"{day}T00:00:00Z", "market": "TW"},
    )


def _ingest(store, records, run_id, finished="2026-11-10T09:00:00Z"):
    return persist_latest_market_snapshots(store, _result(records, run_id, finished))


def _journal_lines(path: Path) -> int:
    return len([line for line in path.read_text().splitlines() if line.strip()])


def _view(store, symbols=("2330", "2317")):
    return {s: (None if store.get(s) is None else store.get(s).to_public_dict()) for s in symbols}


def test_historical_session_older_than_window_does_not_append(tmp_path):
    """Reproduction: before the fix a historical run appended a line with only observed_at changed."""
    path = tmp_path / "snap.jsonl"
    store = LatestMarketSnapshotStore(path, max_closes=3)
    days = [(BASE + timedelta(days=i)).isoformat() for i in range(10)]
    _ingest(store, [_daily("2330", d) for d in days[-3:]], "run-live")
    assert _journal_lines(path) == 1
    before_bytes, before_view = path.read_bytes(), _view(store)
    for i, d in enumerate(days[:-3]):
        out = _ingest(store, [_daily("2330", d)], f"run-hist-{i}", finished=f"2026-11-11T09:0{i}:00Z")
        assert out["updated_snapshot_count"] == 0
    assert path.read_bytes() == before_bytes
    assert _view(store) == before_view
    assert _view(LatestMarketSnapshotStore(path, max_closes=3)) == before_view


def test_unchanged_reingest_appends_nothing_but_new_session_does(tmp_path):
    path = tmp_path / "snap.jsonl"
    store = LatestMarketSnapshotStore(path, max_closes=3)
    _ingest(store, [_daily("2330", "2026-10-01")], "r1")
    _ingest(store, [_daily("2330", "2026-10-01")], "r1", finished="2026-11-12T09:00:00Z")
    assert _journal_lines(path) == 1
    _ingest(store, [_daily("2330", "2026-10-02")], "r3")
    assert _journal_lines(path) == 2


def _write_history(path, *, tombstone=False):
    store = LatestMarketSnapshotStore(path, max_closes=60, compact_threshold_bytes=10**12)
    for i in range(12):
        _ingest(store, [_daily("2330", (BASE + timedelta(days=i)).isoformat(), 100 + i),
                        _daily("2317", (BASE + timedelta(days=i)).isoformat(), 50 + i)], f"run-{i}")
    return store


def test_compaction_is_lossless_and_keeps_original_bytes(tmp_path):
    path = tmp_path / "snap.jsonl"
    store = _write_history(path)
    lines = path.read_text().splitlines()
    assert len(lines) == 24  # two symbols, one envelope per run each
    view = _view(store)
    digest = current._projection_digest(store.reload())
    result = store.compact()
    assert result["compacted"] and result["lines_before"] == 24 and result["lines_after"] == 2
    assert result["projection_sha256"] == digest
    assert path.read_text() == "".join(line + "\n" for line in lines[-2:])  # original envelope bytes
    assert current._projection_digest(LatestMarketSnapshotStore(path).reload()) == digest
    assert _view(store) == view and _view(LatestMarketSnapshotStore(path)) == view
    assert store.compact()["compacted"] is False  # idempotent
    assert not list(tmp_path.glob("*.tmp"))
    assert store.journal_stats()["last_compaction_at"]


def test_compaction_keeps_tombstones_in_force(tmp_path):
    path = tmp_path / "snap.jsonl"
    store = LatestMarketSnapshotStore(path, compact_threshold_bytes=10**12)
    minute = "tw_price_1m"
    rec = lambda day: SourceRecord(
        source_id="m", connector_id="c", source_type="market", title="t", content_ref=f"ref://m/{day}",
        metadata={"dataset": minute, "symbol": "2330", "close": 1.0, "event_time": f"{day}T02:00:00Z", "market": "TW"})
    store.append_normalized_records([rec("2026-11-10")], ingest_run_id="a", dataset=minute)
    store.append_normalized_records([rec("2026-11-11")], ingest_run_id="b", dataset=minute)
    _ingest(store, [_daily("2330", "2026-11-10")], "d")
    assert store.withdraw_sessions("2330", minute, ["2026-11-10", "2026-11-11"])
    before = _view(store)
    assert store.get("2330", minute) is None
    store.compact()
    assert _journal_lines(path) == 2  # daily envelope + the tombstone in force
    reread = LatestMarketSnapshotStore(path)
    assert reread.get("2330", minute) is None and _view(reread) == before


def test_startup_compacts_and_threshold_triggers_compaction(tmp_path):
    path = tmp_path / "snap.jsonl"
    _write_history(path)
    size = path.stat().st_size
    store = LatestMarketSnapshotStore(path)  # startup compaction
    assert _journal_lines(path) == 2 and path.stat().st_size < size
    # threshold path: tiny threshold keeps the journal bounded while sessions keep arriving
    path2 = tmp_path / "snap2.jsonl"
    bounded = LatestMarketSnapshotStore(path2, max_closes=60, compact_threshold_bytes=4000)
    for i in range(40):
        _ingest(bounded, [_daily("2330", (BASE + timedelta(days=i)).isoformat())], f"r{i}")
        assert _journal_lines(path2) <= 4
    assert bounded.journal_stats()["last_compaction_at"] is not None
    assert store.get("2330") is not None


def test_concurrent_writers_and_compaction_lose_nothing(tmp_path):
    path = tmp_path / "snap.jsonl"
    a = LatestMarketSnapshotStore(path, compact_threshold_bytes=0)
    b = LatestMarketSnapshotStore(path, compact_threshold_bytes=0)
    errors: list[BaseException] = []

    def run(store, symbol, offset):
        try:
            for i in range(25):
                _ingest(store, [_daily(symbol, (BASE + timedelta(days=i + offset)).isoformat())], f"{symbol}-{i}")
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(a, "2330", 0)), threading.Thread(target=run, args=(b, "2317", 100))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    final = LatestMarketSnapshotStore(path)
    assert final.get("2330").points[-1].event_time.startswith((BASE + timedelta(days=24)).isoformat())
    assert final.get("2317").points[-1].event_time.startswith((BASE + timedelta(days=124)).isoformat())


def test_health_stats_report_bytes_lines_compaction_and_warn(tmp_path):
    path = tmp_path / "snap.jsonl"
    store = LatestMarketSnapshotStore(path, compact_threshold_bytes=10**12, warn_bytes=1)
    _ingest(store, [_daily("2330", "2026-10-01")], "r1")
    stats = store.journal_stats()
    assert stats["journal_bytes"] == path.stat().st_size and stats["journal_lines"] == 1
    assert stats["last_compaction_at"] is None and stats["warning"] == "journal_near_capture_bound"
    assert stats["capture_bound_bytes"] == 256 * 1024 * 1024
    ok = LatestMarketSnapshotStore(tmp_path / "other.jsonl")
    assert ok.journal_stats()["warning"] is None


def test_runtime_health_metrics_carry_journal_stats(tmp_path, monkeypatch):
    from services.source_ingestion.runtime import SourceIngestionRuntime

    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(tmp_path))
    metrics = SourceIngestionRuntime()._source_runtime_metrics()
    assert metrics["latest_market_snapshot_journal"]["journal_lines"] == 0


def test_thirty_day_backfill_two_sources_bound_is_independent_of_sessions(tmp_path):
    def run(days):
        path = tmp_path / f"snap-{days}.jsonl"
        store = LatestMarketSnapshotStore(path, max_closes=5, compact_threshold_bytes=3000)
        today = BASE + timedelta(days=days)
        for sym, connector in (("2330", "official-a"), ("2317", "official-b")):
            _ingest(store, [_daily(sym, (today - timedelta(days=k)).isoformat(), connector=connector) for k in range(5)], f"live-{sym}")
        for k in range(5, days):
            for sym, connector in (("2330", "official-a"), ("2317", "official-b")):
                _ingest(store, [_daily(sym, (today - timedelta(days=k)).isoformat(), connector=connector)], f"hist-{sym}-{k}")
        return path.stat().st_size, _journal_lines(path), _view(store)

    size30, lines30, view30 = run(30)
    size90, lines90, view90 = run(90)
    bound = 3000 + 2 * 1200  # threshold plus the envelopes appended since the last compaction
    assert lines30 <= 6 and lines90 <= 6 and size30 < bound and size90 < bound
    assert view30.keys() == view90.keys()


PROBE_NS = {}


def _probe(path: Path) -> dict:
    """Run the real deployment capture probe (scripts/dev_release_artifacts.py SOURCE_PROBE) on a journal."""
    sys.path.insert(0, str(REPO))
    try:
        from scripts.dev_release_artifacts import SOURCE_PROBE
    finally:
        sys.path.pop(0)
    out = subprocess.run([sys.executable, "-c", SOURCE_PROBE, "journal", str(path)],
                         cwd=REPO, capture_output=True, text=True, env={"PYTHONPATH": str(REPO)}, check=False)
    return json.loads(out.stdout.strip().splitlines()[-1])


def _previous_reader(tmp_path):
    # The previously deployed reader is the codec on the integration branch (dev), not this change.
    base = subprocess.run(["git", "merge-base", "HEAD", "dev"], cwd=REPO, capture_output=True, text=True)
    if base.returncode != 0:
        pytest.skip("integration branch dev is not available to read the previous codec")
    src = subprocess.run(["git", "show", base.stdout.strip() + ":services/source_ingestion/requirement_state.py"],
                         cwd=REPO, capture_output=True, text=True, check=True).stdout
    name = "services.source_ingestion._previous_requirement_state"
    module = types.ModuleType(name)
    module.__package__ = "services.source_ingestion"
    sys.modules[name] = module  # dataclasses resolve their module through sys.modules
    exec(compile(src, "previous_requirement_state.py", "exec"), module.__dict__)
    return module


def test_compacted_journal_passes_capture_probe_and_previous_reader(tmp_path):
    path = tmp_path / "snap.jsonl"
    store = _write_history(path)
    pre = _probe(path)
    assert pre["ok"] is True
    store.compact()
    post = _probe(path)
    assert post["ok"] is True and post["projection_sha256"] == pre["projection_sha256"]
    assert post["journal_size"] == path.stat().st_size < pre["journal_size"]
    previous = _previous_reader(tmp_path)
    old = previous._market_snapshots_from_lines(path.read_text().splitlines(), path=Path("journal"))
    new = LatestMarketSnapshotStore(path).reload()
    assert {k: v.to_dict() for k, v in old.items()} == {k: v.to_dict() for k, v in new.items()}
    # recorded writer samples stay readable by the real probe
    samples = current.market_snapshot_writer_samples()
    assert set(samples) >= {"official_daily", "collector_close"}


def test_memory_holds_only_latest_projection(tmp_path):
    """A journal far larger than the live projection leaves only one snapshot per key in memory."""
    path = tmp_path / "snap.jsonl"
    _write_history(path)
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines * 20) + "\n")
    store = LatestMarketSnapshotStore(path)  # startup compaction
    assert len(store._latest_by_symbol) == 2
    assert _journal_lines(path) == 2
    assert hashlib.sha256(path.read_bytes()).hexdigest()
