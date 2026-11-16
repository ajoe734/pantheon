"""Journal growth, in-memory append state and in-place compaction of the latest market snapshot store."""

from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from services.source_ingestion import requirement_state as m
from services.source_ingestion.connectors.base import SourceRecord

DEPLOYED_READER_COMMIT = "32fc7da61"  # shares the reader with 94b73806a


def _record(symbol: str, day: int, close: float) -> SourceRecord:
    return SourceRecord(
        source_id=f"src-{symbol}-{day}", connector_id="conn", source_type="market",
        title="t", content_ref=f"ref-{symbol}-{day}",
        metadata={"symbol": symbol, "event_time": f"2026-{1 + day // 28:02d}-{1 + day % 28:02d}T05:30:00Z",
                  "close": close, "market": "TW"},
        created_at="2026-11-12T08:00:00Z",
    )


def _feed(store: m.LatestMarketSnapshotStore, symbols: int, days: int) -> None:
    for day in range(days):
        for s in range(symbols):
            store.append_normalized_records(
                [_record(f"{1000 + s}.TWSE", day, 100.0 + day)], ingest_run_id=f"run-{day}",
                observed_at=f"2026-11-12T08:{day % 60:02d}:00Z")


def probe_digest(path: Path, module=m) -> str:
    """Projection digest computed exactly as SOURCE_PROBE does."""
    lines = path.read_bytes().decode("utf-8").splitlines()
    values = module._market_snapshots_from_lines(lines, path=Path("journal"))
    rows = sorted((s.to_dict() for s in values.values()),
                  key=lambda s: (s["symbol"], s.get("dataset", "tw_price_daily")))
    return hashlib.sha256(module._canonical_json({"snapshots": rows}).encode()).hexdigest()


def _no_compact(path: Path) -> m.LatestMarketSnapshotStore:
    return m.LatestMarketSnapshotStore(path, compact_min_bytes=10**12)


def test_append_uses_memory_and_reparses_only_when_file_changed(tmp_path, monkeypatch):
    path = tmp_path / "j.jsonl"
    store = _no_compact(path)
    _feed(store, 3, 2)
    calls = []
    real = m._market_snapshots_from_lines
    monkeypatch.setattr(m, "_market_snapshots_from_lines", lambda *a, **k: calls.append(1) or real(*a, **k))
    store.append_normalized_records([_record("1000.TWSE", 5, 1.0)], ingest_run_id="r", observed_at="2026-11-12T09:00:00Z")
    store.append_normalized_records([_record("1001.TWSE", 5, 1.0)], ingest_run_id="r", observed_at="2026-11-12T09:00:00Z")
    assert calls == []
    other = _no_compact(path)  # second writer changes the file
    other.append_normalized_records([_record("1002.TWSE", 9, 2.0)], ingest_run_id="r2", observed_at="2026-11-12T09:01:00Z")
    calls.clear()
    out = store.append_normalized_records([_record("1000.TWSE", 6, 3.0)], ingest_run_id="r", observed_at="2026-11-12T09:02:00Z")
    assert calls == [1]
    assert out["updated_snapshot_count"] == 1
    assert store.get("1002.TWSE") is not None  # other writer's state was picked up
    assert m.LatestMarketSnapshotStore(path).reload().keys() == store._latest_by_symbol.keys()


def test_memory_path_writes_same_lines_as_reparse_path(tmp_path):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    sa = _no_compact(a)
    _feed(sa, 4, 6)
    for day in range(6):
        for s in range(4):  # a fresh store per call forces the re-parse path
            _no_compact(b).append_normalized_records(
                [_record(f"{1000 + s}.TWSE", day, 100.0 + day)], ingest_run_id=f"run-{day}",
                observed_at=f"2026-11-12T08:{day % 60:02d}:00Z")
    assert a.read_bytes() == b.read_bytes()


def test_compaction_keeps_projection_and_bounds_growth(tmp_path):
    path = tmp_path / "j.jsonl"
    grow = _no_compact(path)
    _feed(grow, 20, 40)
    before_bytes, before_lines = path.stat().st_size, len(path.read_text().splitlines())
    digest = probe_digest(path)
    ino = path.stat().st_ino
    kept = m._compact_journal_in_place(os.open(path, os.O_RDWR), path=path)
    assert len(kept) == 20
    assert path.stat().st_ino == ino
    lines = path.read_text().splitlines()
    assert len(lines) == 20 and path.stat().st_size < before_bytes / 10
    assert probe_digest(path) == digest
    assert before_lines == 800
    # order: one line per key in original last-line order
    syms = [json.loads(l)["state"]["symbol"] for l in lines]
    assert syms == [f"{1000 + s}.TWSE" for s in range(20)]


def test_withdrawn_keys_dropped_and_readd_kept(tmp_path):
    path = tmp_path / "j.jsonl"
    store = _no_compact(path)
    ds = "tw_price_1m" if "tw_price_1m" in m._INTRADAY_SNAPSHOT_DATASETS else sorted(m._INTRADAY_SNAPSHOT_DATASETS)[0]
    for sym in ("2330.TWSE", "2317.TWSE"):
        store.append_normalized_records(
            [SourceRecord(source_id="s", connector_id="c", source_type="market", title="t",
                          content_ref="r", created_at="2026-11-12T08:00:00Z",
                          metadata={"symbol": sym, "event_time": "2026-11-12T05:30:00Z", "close": 1.0,
                                    "market": "TW", "dataset": ds})],
            ingest_run_id="r", observed_at="2026-11-12T08:00:00Z", dataset=ds)
    assert store.withdraw_sessions("2330.TWSE", ds, ["2026-11-12"], observed_at="2026-11-12T09:00:00Z")
    digest = probe_digest(path)
    m._compact_journal_in_place(os.open(path, os.O_RDWR), path=path)
    lines = path.read_text().splitlines()
    assert len(lines) == 1 and "2317" in lines[0]
    assert probe_digest(path) == digest


def test_store_compacts_automatically_and_stays_bounded(tmp_path):
    path = tmp_path / "j.jsonl"
    store = m.LatestMarketSnapshotStore(path, compact_min_bytes=50_000)
    _feed(store, 10, 60)
    live = sum(store._line_bytes.values())
    assert path.stat().st_size <= store.compact_factor * live + 20_000
    assert len(path.read_text().splitlines()) < 600
    assert probe_digest(path) == probe_digest_of_store(store)
    # in-memory state still correct for next append and equal to a fresh reader
    store.append_normalized_records([_record("1000.TWSE", 70, 5.0)], ingest_run_id="r", observed_at="2026-12-12T00:00:00Z")
    assert m.LatestMarketSnapshotStore(path).reload().keys() == store._latest_by_symbol.keys()


def probe_digest_of_store(store) -> str:
    rows = sorted((s.to_dict() for s in store._latest_by_symbol.values()), key=lambda s: (s["symbol"], s.get("dataset", "tw_price_daily")))
    return hashlib.sha256(m._canonical_json({"snapshots": rows}).encode()).hexdigest()


def test_interrupted_rewrite_leaves_acceptable_journal(tmp_path, monkeypatch):
    base = tmp_path / "base.jsonl"
    _feed(_no_compact(base), 6, 10)
    digest = probe_digest(base)
    monkeypatch.setattr(m, "_COMPACT_CHUNK_BYTES", 8192)
    fsyncs = []
    real_fsync = os.fsync
    # Fail at every possible fsync step in turn: each state must be accepted with the same projection.
    total = None
    for fail_at in range(1, 400):
        path = tmp_path / f"t{fail_at}.jsonl"
        path.write_bytes(base.read_bytes())
        fd = os.open(path, os.O_RDWR)
        count = {"n": 0}

        def boom(f, _c=count, _at=fail_at):
            _c["n"] += 1
            if _c["n"] == _at:
                raise OSError("interrupted")
            return real_fsync(f)

        monkeypatch.setattr(os, "fsync", boom)
        try:
            m._compact_journal_in_place(fd, path=path)
            finished = True
        except OSError:
            finished = False
        finally:
            monkeypatch.setattr(os, "fsync", real_fsync)
            os.close(fd)
        assert probe_digest(path) == digest, fail_at
        if finished:
            total = fail_at
            break
    assert total is not None and total > 3
    assert len(path.read_text().splitlines()) == 6


def test_rewrite_blocks_waiting_writer_on_same_inode(tmp_path):
    path = tmp_path / "j.jsonl"
    store = m.LatestMarketSnapshotStore(path, compact_min_bytes=20_000)
    _feed(store, 5, 5)
    ino = path.stat().st_ino
    _feed(store, 5, 40)
    assert path.stat().st_ino == ino
    holder = os.open(path, os.O_RDWR)
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        other = os.open(path, os.O_RDWR)
        with pytest.raises(BlockingIOError):
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.close(other)
    finally:
        os.close(holder)


def _deployed_reader(tmp_path):
    try:
        src = subprocess.run(["git", "show", f"{DEPLOYED_READER_COMMIT}:services/source_ingestion/requirement_state.py"],
                             check=True, capture_output=True, text=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("deployed reader commit not available")
    target = Path(m.__file__).with_name("_deployed_requirement_state_probe.py")
    target.write_text(src, encoding="utf-8")
    try:
        spec = importlib.util.spec_from_file_location("services.source_ingestion._deployed_requirement_state_probe", target)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    finally:
        target.unlink()
    mod.SOURCE_TEXT = src
    return mod


def test_deployed_reader_reads_compacted_journal_identically(tmp_path):
    old = _deployed_reader(tmp_path)
    path = tmp_path / "j.jsonl"
    _feed(_no_compact(path), 15, 35)
    full = probe_digest(path, old)
    assert full == probe_digest(path, m)
    m._compact_journal_in_place(os.open(path, os.O_RDWR), path=path)
    assert probe_digest(path, old) == full
    # the shipped reader function is byte-identical to the deployed one
    import ast
    node = next(n for n in ast.parse(old.SOURCE_TEXT).body
                if isinstance(n, ast.FunctionDef) and n.name == "_market_snapshots_from_lines")
    assert ast.get_source_segment(old.SOURCE_TEXT, node) == inspect.getsource(m._market_snapshots_from_lines).rstrip()


def test_release_probe_bound_unchanged():
    text = (Path(__file__).parents[3] / "scripts" / "dev_release_artifacts.py").read_text()
    # R2: bound raised to a documented >= 2 GiB constant, enforced in probe, reader and proof validation.
    assert "SOURCE_JOURNAL_MAX_BYTES = 4 * 1024 * 1024 * 1024" in text
    assert "if before.st_size > __MAX_BYTES__:" in text
    assert "<= SOURCE_JOURNAL_MAX_BYTES" in text
    assert "2048m" in text


@pytest.mark.skipif(
    os.environ.get("PANTHEON_RUN_COMPACTION_BENCHMARK") != "1",
    reason="~10 minute journal growth repro benchmark; opt in with PANTHEON_RUN_COMPACTION_BENCHMARK=1",
)
def test_repro_unbounded_growth_and_append_latency(tmp_path):
    """Evidence harness (hosted-shaped: full state line per update, up to 60 closes per symbol).

    Records lines/bytes at 10k and 50k lines with compaction off and on, plus the append cost
    of the previous behaviour (full re-parse under the lock, forced by dropping the signature)
    versus the in-memory path. Journals are deleted as soon as they are measured.
    """
    out = {}
    symbols = 200
    for label, kwargs in (("no_compaction", {"compact_min_bytes": 10**12}), ("compaction", {})):
        path = tmp_path / f"{label}.jsonl"
        store = m.LatestMarketSnapshotStore(path, **kwargs)
        try:
            n = 0
            for target in (10_000, 50_000):  # one growing journal, measured at both sizes
                while n < target:
                    for s in range(symbols):
                        store.append_normalized_records(
                            [_record(f"{1000 + s}.TWSE", n // symbols % 250, float(n))],
                            ingest_run_id="r", observed_at=f"2026-11-12T08:00:{n % 60:02d}Z")
                        n += 1
                row = {"lines": len(path.read_text().splitlines()), "bytes": path.stat().st_size}
                if label == "no_compaction":
                    def timed(reparse):
                        t = time.perf_counter()
                        if reparse:
                            store._journal_signature = None  # previous behaviour: parse the whole file
                        store.append_normalized_records([_record("1000.TWSE", 249, 1.5)], ingest_run_id="r",
                                                        observed_at="2026-11-12T09:00:00Z")
                        return round((time.perf_counter() - t) * 1000, 2)
                    row["append_ms_reparse_previous_behaviour"] = timed(True)
                    row["append_ms_in_memory"] = min(timed(False) for _ in range(3))
                out[f"{label}_{target}"] = row
        finally:
            path.unlink(missing_ok=True)
    print("REPRO " + json.dumps(out, sort_keys=True))
    assert out["compaction_50000"]["bytes"] < out["no_compaction_50000"]["bytes"] / 3
