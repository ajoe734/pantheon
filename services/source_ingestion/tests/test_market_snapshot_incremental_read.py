"""Incremental (append-only) reads of the latest market snapshot journal; every test runs in seconds."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from services.source_ingestion import requirement_state as m
from services.source_ingestion.connectors.base import SourceRecord
from services.source_ingestion.tests.test_latest_market_snapshot_compaction import (
    _deployed_reader,
    probe_digest,
)

INTRADAY = "tw_price_1m" if "tw_price_1m" in m._INTRADAY_SNAPSHOT_DATASETS else sorted(m._INTRADAY_SNAPSHOT_DATASETS)[0]


def _daily(symbol: str, day: int, close: float) -> SourceRecord:
    return SourceRecord(
        source_id=f"src-{symbol}-{day}", connector_id="finmind-tw-daily", source_type="market",
        title=f"{symbol} close", content_ref=f"ref-{symbol}-{day}", created_at="2026-11-12T08:00:00Z",
        metadata={"symbol": symbol, "event_time": f"2026-{1 + day // 28:02d}-{1 + day % 28:02d}T05:30:00Z",
                  "close": close, "market": "TW"},
    )


def _minute(symbol: str, minute: int, close: float) -> SourceRecord:
    return SourceRecord(
        source_id=f"m-{symbol}-{minute}", connector_id="finmind-tw-kbar", source_type="market",
        title="kbar", content_ref=f"m-{symbol}-{minute}", created_at="2026-11-12T08:00:00Z",
        metadata={"symbol": symbol, "event_time": f"2026-11-12T01:{minute % 60:02d}:00Z", "close": close,
                  "market": "TW", "dataset": INTRADAY},
    )


def _store(path: Path, **kw) -> m.LatestMarketSnapshotStore:
    return m.LatestMarketSnapshotStore(path, compact_min_bytes=kw.pop("compact_min_bytes", 10**12), **kw)


def _put(store, symbol, day, close=1.0, minute=False):
    rec = _minute(symbol, day, close) if minute else _daily(symbol, day, close)
    store.append_normalized_records(
        [rec], ingest_run_id=f"r{day}", observed_at=f"2026-11-12T09:{day % 60:02d}:00Z",
        **({"dataset": INTRADAY} if minute else {}),
    )


def _state(store):
    return {k: v.to_dict() for k, v in store._latest_by_symbol.items()}


def _fresh(path):
    return _state(m.LatestMarketSnapshotStore(path))


class _Spy:
    """Counts journal lines and bytes handed to the parser."""

    def __init__(self, monkeypatch):
        self.lines = 0
        self.bytes = 0
        self.full = 0
        fold, full = m._fold_market_snapshot_lines, m._market_snapshots_from_lines

        def counted(lines, **kw):
            def gen():
                for line in lines:
                    self.lines += 1
                    self.bytes += len(line) + 1
                    yield line
            if kw["first_line_number"] == 1:
                self.full += 1
            return fold(gen(), **kw)

        monkeypatch.setattr(m, "_fold_market_snapshot_lines", counted)
        monkeypatch.setattr(m, "_market_snapshots_from_lines", lambda *a, **k: pytest.fail("whole-file parse"))

    def reset(self):
        self.lines = self.bytes = self.full = 0


def _seed(path, symbols=40, days=30):
    a = _store(path)
    for day in range(days):
        for s in range(symbols):
            _put(a, f"{1000 + s}.TWSE", day, 100.0 + day)
    return a


def test_other_writer_append_parses_only_new_lines(tmp_path, monkeypatch):
    path = tmp_path / "j.jsonl"
    a = _seed(path)
    b = _store(path)  # second writer, e.g. the scheduler container
    total = path.stat().st_size
    spy = _Spy(monkeypatch)
    _put(b, "1001.TWSE", 40, 9.0)
    _put(b, "2330.TWSE", 41, 8.0)
    spy.reset()
    _put(a, "1002.TWSE", 42, 7.0)  # a must catch up on b's two lines, then write its own
    assert spy.lines == 2 and spy.full == 0
    assert spy.bytes < total / 50
    spy.reset()
    assert a.reload().keys() == b.reload().keys()  # reload after own write: nothing new except b's reload
    assert spy.lines <= 1 and spy.full == 0
    spy.reset()
    a.reload()
    assert spy.lines == 0
    assert _state(a) == _fresh(path)


def test_own_writes_are_not_reparsed(tmp_path, monkeypatch):
    path = tmp_path / "j.jsonl"
    a = _seed(path, 5, 3)
    spy = _Spy(monkeypatch)
    for day in range(10, 16):
        _put(a, "1000.TWSE", day)
    a.reload()
    assert spy.lines == 0
    assert _state(a) == _fresh(path)


def test_partial_trailing_line_is_consumed_once_when_complete(tmp_path, monkeypatch):
    path = tmp_path / "j.jsonl"
    a = _seed(path, 3, 3)
    donor = tmp_path / "donor.jsonl"
    _put(_store(donor), "9999.TWSE", 1, 5.0)
    line = donor.read_bytes()
    assert line.endswith(b"\n")
    cut = len(line) // 2
    with path.open("ab") as handle:
        handle.write(line[:cut])
    spy = _Spy(monkeypatch)
    a.reload()
    assert spy.lines == 0 and a.get("9999.TWSE") is None
    with path.open("ab") as handle:
        handle.write(line[cut:])
    a.reload()
    assert spy.lines == 1 and a.get("9999.TWSE") is not None
    a.reload()
    assert spy.lines == 1
    assert _state(a) == _fresh(path)


@pytest.mark.parametrize("mode", ["replaced", "shrank", "same_size_rewrite"])
def test_rewrite_falls_back_to_one_full_read(tmp_path, monkeypatch, mode):
    path = tmp_path / "j.jsonl"
    a = _seed(path, 8, 6)
    raw = path.read_bytes()
    if mode == "replaced":
        other = tmp_path / "other.jsonl"
        _seed(other, 3, 9)
        os.replace(other, path)
    elif mode == "shrank":
        lines = raw.splitlines(keepends=True)
        path.write_bytes(b"".join(lines[: len(lines) // 2]))
    else:
        lines = raw.splitlines(keepends=True)
        swapped = lines[:]
        swapped[0], swapped[1] = lines[1], lines[0]
        assert swapped[0] != lines[0] and len(b"".join(swapped)) == len(raw)
        path.write_bytes(b"".join(swapped))
    spy = _Spy(monkeypatch)
    a.reload()
    assert spy.full == 1
    assert _state(a) == _fresh(path)


@pytest.mark.parametrize("grow", [0, 1, 400])
def test_in_place_compaction_then_growth_matches_fresh_store(tmp_path, monkeypatch, grow):
    path = tmp_path / "j.jsonl"
    reader = _seed(path, 12, 20)
    writer = _store(path)
    ino = path.stat().st_ino
    old_size = path.stat().st_size
    fd = os.open(path, os.O_RDWR)
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
        m._compact_journal_in_place(fd, path=path)
    finally:
        os.close(fd)
    assert path.stat().st_ino == ino and path.stat().st_size < old_size
    for i in range(grow):  # writer grows the compacted file past the reader's old offset
        _put(writer, f"{1000 + i % 12}.TWSE", 25 + i % 3, 50.0 + i)
    if grow:
        assert path.stat().st_size > old_size or grow < 400
    spy = _Spy(monkeypatch)
    reader.reload()
    full_reads = spy.full
    assert _state(reader) == _fresh(path)
    if grow == 400:
        assert path.stat().st_size > old_size  # the dangerous case: offset is inside the new file
        assert full_reads == 1


def test_own_compaction_keeps_incremental_position(tmp_path, monkeypatch):
    path = tmp_path / "j.jsonl"
    a = _store(path, compact_min_bytes=20_000)
    for day in range(30):
        for s in range(6):
            _put(a, f"{1000 + s}.TWSE", day)
    b = _store(path, compact_min_bytes=10**12)
    _put(b, "3000.TWSE", 3)
    spy = _Spy(monkeypatch)
    _put(a, "1000.TWSE", 31)
    assert spy.full == 0 and spy.lines == 1
    assert _state(a) == _fresh(path)


def test_interleaved_sequence_equals_fresh_read_with_withdrawals(tmp_path):
    path = tmp_path / "j.jsonl"
    a, b = _store(path), _store(path)
    for i in range(60):
        w = a if i % 2 else b
        _put(w, f"{2000 + i % 7}.TWSE", i % 25, float(i))
        _put(w, f"{3000 + i % 4}.TWSE", i % 50, float(i), minute=True)
        if i % 9 == 8:
            w.withdraw_sessions(f"{3000 + i % 4}.TWSE", INTRADAY, ["2026-11-12"], observed_at="2026-11-12T10:00:00Z")
        if i % 5 == 0:
            a.reload()
            b.reload()
            assert _state(a) == _state(b) == _fresh(path)
    a.reload()
    b.reload()
    assert _state(a) == _state(b) == _fresh(path)


def test_checksum_failure_matches_fresh_read(tmp_path):
    path = tmp_path / "j.jsonl"
    a = _seed(path, 3, 3)
    good = _state(a)
    with path.open("ab") as handle:
        handle.write(path.read_bytes().splitlines()[-1].replace(b'"checksum":"', b'"checksum":"0') [:-3] + b'00"}\n')
    with pytest.raises(m.MarketSnapshotStateError) as fresh_err:
        m.LatestMarketSnapshotStore(path)
    with pytest.raises(m.MarketSnapshotStateError) as inc_err:
        a.reload()
    assert str(inc_err.value) == str(fresh_err.value)
    assert "checksum" in str(inc_err.value) and good
    assert a._latest_by_symbol == {}  # fail closed, like a fresh reload


def test_deployed_reader_reads_journal_written_by_incremental_store(tmp_path):
    old = _deployed_reader(tmp_path)
    path = tmp_path / "j.jsonl"
    a, b = _store(path), _store(path)
    for i in range(40):
        _put(a if i % 2 else b, f"{4000 + i % 9}.TWSE", i % 30, float(i))
        _put(b if i % 2 else a, f"{4100 + i % 3}.TWSE", i % 50, float(i), minute=True)
    assert probe_digest(path, old) == probe_digest(path, m)
    import json
    for line in path.read_text().splitlines():  # line format unchanged: canonical envelope
        assert m._canonical_json(json.loads(line)) == line
