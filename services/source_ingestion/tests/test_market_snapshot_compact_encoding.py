"""Compact v2 storage encoding is read-only in this task: logical state and the writer stay v1."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import date, timedelta
from pathlib import Path

import pytest

from services.source_ingestion import requirement_state as m
from services.source_ingestion.connectors.base import SourceRecord
from services.source_ingestion.tests.test_latest_market_snapshot_compaction import probe_digest

TWSE = "collector-tw-twse-price-history"
ENDPOINT = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d}&type=ALLBUT0999&response=json"


def _sha(i: int) -> str:
    return hashlib.sha256(str(i).encode()).hexdigest()


def _daily_points(symbol="2330.TWSE", n=60, collector=True) -> list[m.MarketSnapshotPoint]:
    start = date(2026, 8, 20)
    points = []
    for i in range(n):
        d = start + timedelta(days=i)
        cc = None
        if collector:
            cc = {"collector_source_id": TWSE, "exchange_endpoint": ENDPOINT.format(d=d.strftime("%Y%m%d")),
                  "bundle_sha256": _sha(i), "trade_date": d.isoformat()}
        points.append(m.MarketSnapshotPoint(
            event_time=f"{d.isoformat()}T05:30:00Z", close=100.0 + i * 0.25,
            source_id=f"source-ingest:finmind-tw-daily:{symbol}:{d.isoformat()}:price-history:{_sha(i)[:24]}:x",
            connector_id="finmind-tw-daily-price-connector",
            content_ref=f"content://source-ingest/finmind-tw-daily/{symbol}/{d.isoformat()}/{_sha(i)[:32]}/record",
            ingest_run_id=f"ingest-run-{20261100 + i // 10:08d}",
            market="TW", collector_close=cc, open=(99.5 + i * 0.25) if not collector and i % 2 else None))
    return points


def _snap(points, **kw) -> m.LatestMarketSnapshot:
    return m.LatestMarketSnapshot(symbol=kw.pop("symbol", "2330.TWSE"), points=tuple(points),
                                  observed_at="2026-11-12T08:00:00Z", **kw)


def _recon() -> list[dict]:
    return [{"trade_date": "2026-10-01", "symbol": "2330.TWSE", "outcome": m.OUTCOME_AGREE if hasattr(m, "OUTCOME_AGREE") else "agree",
             "collector_close": 100.0, "official_close": 100.0}]


def _cases() -> dict[str, m.LatestMarketSnapshot]:
    cal = {"source": "twse-calendar", "holidays": ["2026-10-10"], "fetched_at": "2026-11-01T00:00:00Z", "sha256": _sha(9)}
    minute = [m.MarketSnapshotPoint(event_time=f"2026-11-12T01:{i:02d}:00Z", close=100 + i, source_id=f"m-{i}",
                                    connector_id="finmind-tw-kbar", content_ref=f"m-ref-{i}", ingest_run_id="run-m", market="TW")
              for i in range(60)]
    return {
        "daily_collector": _snap(_daily_points()),
        "daily_official_open_calendar_recon": _snap(_daily_points("2317.TWSE", collector=False), symbol="2317.TWSE", calendar_evidence=cal,
                                                    close_reconciliation=tuple(_recon())),
        "minute": _snap(minute, dataset="tw_price_1m"),
        "no_market": _snap(symbol="1101.TWSE", points=[m.MarketSnapshotPoint(event_time="2026-11-12T05:30:00Z", close=5, source_id="s", connector_id="c",
                                                  content_ref="r", ingest_run_id="i")]),
    }


def _line(state) -> str:
    return m.LatestMarketSnapshotStore._envelope_line(state)


def _read(lines, path=Path("j")):
    return m._market_snapshots_from_lines(lines, path=path)


@pytest.mark.parametrize("name", list(_cases()))
def test_round_trip_to_exact_v1_state(name):
    snap = _cases()[name]
    compact = snap.to_compact_dict()
    assert compact["schema_version"] == m.MARKET_SNAPSHOT_COMPACT_SCHEMA_VERSION
    back = m.LatestMarketSnapshot.from_dict(json.loads(json.dumps(compact)))
    assert back == snap
    assert back.to_dict() == snap.to_dict()
    assert back.snapshot_id == snap.snapshot_id
    assert back.to_public_dict() == snap.to_public_dict()


def test_compact_state_stores_nothing_derivable():
    compact = _cases()["daily_collector"].to_compact_dict()
    assert not {"closes", "lineage", "snapshot_id", "event_time"} & set(compact)
    # provenance shared by every point is stored once
    assert compact["connectors"] == ["finmind-tw-daily-price-connector"] and compact["collectors"] == [TWSE]
    text = json.dumps(compact)
    assert text.count("finmind-tw-daily-price-connector") == 1 and text.count(TWSE) == 1
    assert text.count("source_id") == 0


def test_mixed_journal_resolves_same_snapshots_whichever_encoding_wrote_them():
    cases = _cases()
    v1 = _read([_line(s.to_dict()) for s in cases.values()])
    v2 = _read([_line(s.to_compact_dict()) for s in cases.values()])
    mixed = _read([_line(s.to_dict() if i % 2 else s.to_compact_dict()) for i, s in enumerate(cases.values())])
    assert v1 == v2 == mixed and len(v1) == len(cases)
    for key in v1:
        assert v1[key].to_dict() == v2[key].to_dict() == mixed[key].to_dict()


def test_store_reads_mixed_journal_and_supersession_and_withdrawal(tmp_path):
    cases = _cases()
    older = _snap(_daily_points(n=30))
    lines = [_line(older.to_dict()), _line(cases["daily_collector"].to_compact_dict()),
             _line(cases["minute"].to_dict()),
             _line({"schema_version": m.MARKET_SNAPSHOT_SCHEMA_VERSION, "symbol": "2330.TWSE", "dataset": "tw_price_1m",
                    "observed_at": "2026-11-12T09:00:00Z", "withdrawn": True})]
    path = tmp_path / "j.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    store = m.LatestMarketSnapshotStore(path, compact_min_bytes=10**12)
    got = store.get("2330.TWSE")
    assert got.to_dict() == cases["daily_collector"].to_dict()
    assert store.get("2330.TWSE", "tw_price_1m") is None  # withdrawn after the v1 minute line


def test_logical_identity_digest_across_encodings(tmp_path):
    cases = _cases()
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text("\n".join(_line(s.to_dict()) for s in cases.values()) + "\n")
    b.write_text("\n".join(_line(s.to_compact_dict()) for s in cases.values()) + "\n")
    assert probe_digest(a) == probe_digest(b)


def test_v2_line_is_at_most_half_of_v1_for_real_shaped_daily_snapshot():
    snap = _cases()["daily_collector"]
    v1, v2 = len(_line(snap.to_dict()).encode()), len(_line(snap.to_compact_dict()).encode())
    assert v2 <= v1 * 0.5, (v1, v2)


def test_writer_and_compaction_still_emit_v1_bytes(tmp_path):
    path = tmp_path / "j.jsonl"
    store = m.LatestMarketSnapshotStore(path, compact_min_bytes=10**12)
    for i in range(8):
        store.append_normalized_records(
            [SourceRecord(source_id=f"s{i}", connector_id="c", source_type="market", title="t", content_ref=f"r{i}",
                          created_at="2026-11-12T08:00:00Z",
                          metadata={"symbol": "2330", "event_time": f"2026-11-{10 + i:02d}T05:30:00Z", "close": 100.0 + i, "market": "TW"})],
            ingest_run_id=f"run-{i}", observed_at=f"2026-11-12T08:0{i}:00Z")
    def assert_v1(p):
        lines = p.read_text().splitlines()
        assert lines
        for line in lines:
            env = json.loads(line)
            assert env["state"]["schema_version"] == m.MARKET_SNAPSHOT_SCHEMA_VERSION
            assert line == _line(env["state"])
    assert_v1(path)
    last = list(m._market_snapshots_from_lines(path.read_text().splitlines(), path=path).values())[0]
    assert path.read_text().splitlines()[-1] == _line(last.to_dict())
    m._compact_journal_in_place(os.open(path, os.O_RDWR), path=path)
    assert_v1(path)


def _tamper_cases() -> dict[str, dict]:
    base = _cases()["daily_collector"].to_compact_dict()
    def mod(fn):
        c = copy.deepcopy(base); fn(c); return c
    return {
        "unknown_field": mod(lambda c: c.update(closes=[1.0])),
        "missing_table": mod(lambda c: c.pop("runs")),
        "bad_table_type": mod(lambda c: c.update(sources=[1])),
        "index_out_of_range": mod(lambda c: c["points"][0].__setitem__(2, 99)),
        "bool_index": mod(lambda c: c["points"][0].__setitem__(3, True)),
        "short_row": mod(lambda c: c["points"].__setitem__(0, c["points"][0][:3])),
        "bad_collector": mod(lambda c: c["points"][0].__setitem__(7, [0, 0, "x"])),
        "bad_sha": mod(lambda c: c["points"][0][7].__setitem__(2, "zz")),
        "unordered": mod(lambda c: c["points"].reverse()),
        "bad_close": mod(lambda c: c["points"][0].__setitem__(1, -1)),
        "explicit_daily_dataset": mod(lambda c: c.update(dataset="tw_price_daily")),
        "wrong_schema": mod(lambda c: c.update(schema_version="source_ingest_latest_market_snapshot.v3")),
    }


@pytest.mark.parametrize("name", list(_tamper_cases()))
def test_corrupt_v2_state_fails_with_state_error_and_line_number(name):
    good = _line(_cases()["minute"].to_dict())
    bad = _line(_tamper_cases()[name])  # re-checksummed so the state itself is what is rejected
    with pytest.raises(m.MarketSnapshotStateError, match=r"market snapshot state is invalid at j:2") as v2err:
        _read([good, bad])
    # the equivalent v1 corruption class and location
    v1 = _cases()["daily_collector"].to_dict()
    v1["closes"] = [1.0]
    with pytest.raises(m.MarketSnapshotStateError, match=r"market snapshot state is invalid at j:2"):
        _read([good, _line(v1)])
    assert isinstance(v2err.value, m.MarketSnapshotStateError)


def test_tampered_envelope_checksum_and_malformed_json_match_v1():
    state = _cases()["daily_collector"].to_compact_dict()
    env = json.loads(_line(state))
    env["state"]["points"][0][1] = 999.0
    with pytest.raises(m.MarketSnapshotStateError, match="checksum mismatch"):
        _read([json.dumps(env)])
    with pytest.raises(m.MarketSnapshotStateError, match=r"malformed at j:1"):
        _read(['{"state": '])
