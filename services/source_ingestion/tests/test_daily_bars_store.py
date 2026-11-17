"""Daily bars store of record (SD_MARKET_DATA_ONE_WRITE_PER_DAY T1)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("pyarrow")

from services.source_ingestion import daily_bars_store as dbs
from services.source_ingestion.connectors import SourceRecord
from services.source_ingestion.daily_bars_reader import DailyBarsReader
from services.source_ingestion.daily_bars_store import (
    DAILY_BARS, DailyBarsBudgetExceeded, DailyBarsStore, DatasetKeyDeclaration,
)

DAY = "2026-11-13"


def make_rows(n: int, trade_date: str = DAY, run: str = "run-1", bump: float = 0.0, warrants: int = 0):
    """Hosted-shaped rows: 4-digit shares first, then six-character warrant-like codes."""
    rows = []
    for i in range(n):
        symbol = f"{1000 + i}" if i < n - warrants else f"0{3 + i % 6}{i:04d}"[:6]
        symbol = f"{symbol}.TW" if i < n - warrants else symbol
        ohlcv = {"open": 10.0 + i % 50, "high": 11.0 + i % 50, "low": 9.0, "close": 10.5 + (i % 50) + bump,
                 "volume": float(1000 + i), "adjusted": False}
        rows.append({"market": "TW", "symbol": symbol + ("" if "." in symbol else f"-{i}"), "trade_date": trade_date,
                     **ohlcv, "source_id": f"src-{i}", "ingest_run_id": run, "lineage_ref": run,
                     "content_hash": dbs._sha(ohlcv)})
    return rows


def record(symbol: str, trade_date: str | None, close: float = 10.0, **extra):
    meta = {"symbol": symbol, "close": close, "market": "TW", "dataset": "tw_price_daily", **extra}
    if trade_date:
        meta["trade_date"] = trade_date
    return SourceRecord(source_id=f"s-{symbol}", connector_id="c", source_type="market", title="t",
                        content_ref=f"ref-{symbol}", metadata=meta)


def tree_files(root: Path):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.name != ".lock")


def test_one_row_per_key_with_manifest_and_lineage(tmp_path):
    store = DailyBarsStore(tmp_path)
    rows = make_rows(1200)
    assert store.write_day(DAY, rows, ingest_run_id="run-1", connector_id="c")
    manifest = store.read_manifest(DAY)
    assert manifest["row_count"] == 1200 and manifest["symbol_count"] == 1200
    assert manifest["key_fields"] == ["market", "symbol", "trade_date"]
    assert manifest["content_digest"] and manifest["files"]
    lineage = json.loads((tmp_path / "lineage" / f"date={DAY}.json").read_text())
    assert list(lineage["runs"]) == ["run-1"]
    got = DailyBarsReader(tmp_path).read_range(["1000.TW"], DAY, DAY)
    assert len(got["1000.TW"]) == 1


def test_batches_of_500_merge_into_one_manifest_without_duplicate_keys(tmp_path):
    store = DailyBarsStore(tmp_path)
    rows = make_rows(2300)
    for i in range(0, 2300, 500):
        store.write_day(DAY, rows[i:i + 500], ingest_run_id=f"run-{i}")
    assert store.read_manifest(DAY)["row_count"] == 2300
    assert len(list((tmp_path / "daily_bars" / "_manifest").glob("date=*.json"))) == 1
    # only the final generation's files remain
    gens = {Path(f["path"]).name.split("-")[1] for f in store.read_manifest(DAY)["files"]}
    assert len(gens) == 1
    assert len(list((tmp_path / "daily_bars").glob("market=*/date=*/*"))) == len(store.read_manifest(DAY)["files"])


def test_unchanged_rerun_writes_nothing(tmp_path):
    store = DailyBarsStore(tmp_path)
    store.write_day(DAY, make_rows(300), ingest_run_id="run-1")
    before = {p: (tmp_path / p).stat().st_mtime_ns for p in tree_files(tmp_path)}
    assert store.write_day(DAY, make_rows(300, run="run-2"), ingest_run_id="run-2") is False
    assert {p: (tmp_path / p).stat().st_mtime_ns for p in tree_files(tmp_path)} == before
    assert store.counters["days_unchanged"] == 1


def test_changed_rerun_replaces_the_day(tmp_path):
    store = DailyBarsStore(tmp_path)
    store.write_day(DAY, make_rows(300), ingest_run_id="run-1")
    assert store.write_day(DAY, make_rows(300, run="run-2", bump=1.0), ingest_run_id="run-2")
    got = DailyBarsReader(tmp_path).read_range("1000.TW", DAY, DAY)
    assert len(got) == 1 and got[0]["close"] == 11.5
    assert store.read_manifest(DAY)["row_count"] == 300 and store.read_manifest(DAY)["generation"] == 2


@pytest.mark.parametrize("step", ["after_temp_write", "after_fsync", "after_lineage", "before_manifest_rename"])
def test_writer_killed_between_steps_reader_sees_old_complete_set(tmp_path, step):
    DailyBarsStore(tmp_path).write_day(DAY, make_rows(300), ingest_run_id="run-1")
    reader = DailyBarsReader(tmp_path)
    old = reader.read_range(["1000.TW", "1299.TW"], DAY, DAY)

    def kill(name):
        if name == step:
            raise KeyboardInterrupt("writer killed")

    with pytest.raises(KeyboardInterrupt):
        DailyBarsStore(tmp_path, fault=kill).write_day(DAY, make_rows(300, run="run-2", bump=5.0), ingest_run_id="run-2")
    assert DailyBarsReader(tmp_path).read_range(["1000.TW", "1299.TW"], DAY, DAY) == old
    assert DailyBarsStore(tmp_path).read_manifest(DAY)["generation"] == 1
    # a retry completes and replaces; no leftover generation or temp files
    assert DailyBarsStore(tmp_path).write_day(DAY, make_rows(300, run="run-2", bump=5.0), ingest_run_id="run-2")
    new = DailyBarsReader(tmp_path).read_range("1000.TW", DAY, DAY)
    assert new[0]["close"] == 15.5
    names = tree_files(tmp_path / "daily_bars")
    assert not [n for n in names if ".tmp" in n]
    assert len([n for n in names if n.endswith(".parquet")]) == len(DailyBarsStore(tmp_path).read_manifest(DAY)["files"])


def test_writer_killed_on_first_write_leaves_no_visible_day(tmp_path):
    def kill(name):
        if name == "before_manifest_rename":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        DailyBarsStore(tmp_path, fault=kill).write_day(DAY, make_rows(50), ingest_run_id="r")
    assert DailyBarsReader(tmp_path).trade_dates() == []


def test_record_without_trade_date_is_rejected_and_counted_never_filed_under_ingest_day(tmp_path):
    store = DailyBarsStore(tmp_path)
    out = store.write_records(
        [record("2330.TW", None), record("2317.TW", DAY),
         record("2454.TW", None, available_time="2026-11-16T01:00:00Z")],
        ingest_run_id="run-1")
    assert out["rejected"] == {"no_trade_date": 2}
    assert store.counters["rejected_no_trade_date"] == 2
    assert DailyBarsReader(tmp_path).trade_dates() == [DAY]  # nothing under today's date
    assert store.read_manifest(DAY)["row_count"] == 1


def test_listing_filter_counts_not_listed_and_adds_no_symbol_pattern(tmp_path):
    store = DailyBarsStore(tmp_path)
    recs = [record("2330.TW", DAY), record("2317.TW", DAY), record("037999.TW", DAY)]
    out = store.write_records(recs, ingest_run_id="r", listing={"2330.TW", "037999.TW"})
    assert out["rejected"] == {"skipped_not_listed": 1}
    # without a listing nothing is filtered by symbol shape (a six-character code is kept)
    store2 = DailyBarsStore(tmp_path / "b")
    assert store2.write_records(recs, ingest_run_id="r")["rows"] == 3


def test_missing_or_contradictory_market_fails_closed(tmp_path):
    store = DailyBarsStore(tmp_path)
    bad = SourceRecord(source_id="s", connector_id="c", source_type="market", title="t", content_ref="r",
                       metadata={"symbol": "ABCD", "close": 1.0, "trade_date": DAY})
    out = store.write_records([bad], ingest_run_id="r")
    assert out["rows"] == 0 and sum(out["rejected"].values()) == 1


def test_key_declaration_is_per_dataset(tmp_path):
    decl = DatasetKeyDeclaration("tw_index_daily", ("market", "symbol", "trade_date"), "trade_date")
    store = DailyBarsStore(tmp_path, declaration=decl)
    store.write_day(DAY, make_rows(10), ingest_run_id="r")
    assert (tmp_path / "tw_index_daily" / "_manifest" / f"date={DAY}.json").is_file()
    assert store.read_manifest(DAY)["dataset"] == "tw_index_daily"
    with pytest.raises(ValueError):
        DatasetKeyDeclaration("x", ("a",), "b")
    assert DAILY_BARS.key_fields == ("market", "symbol", "trade_date")


def test_budget_bytes_on_hosted_shaped_day(tmp_path):
    """22,500 symbols incl. 20,200 listed warrants in 500-record batches: day and lineage rows of section 6."""
    store = DailyBarsStore(tmp_path)
    rows = make_rows(22_500, warrants=20_200)
    for i in range(0, len(rows), 500):
        store.write_day(DAY, rows[i:i + 500], ingest_run_id=f"run-{i // 500}", connector_id="c")
    manifest = store.read_manifest(DAY)
    assert manifest["row_count"] == 22_500
    assert manifest["bytes"] <= dbs.DAILY_BARS_DAY_TARGET_BYTES, manifest["bytes"]
    lineage_bytes = (tmp_path / "lineage" / f"date={DAY}.json").stat().st_size
    assert lineage_bytes <= dbs.LINEAGE_DAY_TARGET_BYTES, lineage_bytes
    report = store.enforce_budget()
    assert report["alarms"] == []


def test_budget_alarm_fields_and_enforcement(tmp_path, monkeypatch):
    store = DailyBarsStore(tmp_path)
    store.write_day(DAY, make_rows(2000), ingest_run_id="r")
    monkeypatch.setattr(dbs, "DAILY_BARS_DAY_ALARM_BYTES", 1000)
    monkeypatch.setattr(dbs, "LINEAGE_DAY_ALARM_BYTES", 10)
    report = store.health()
    assert set(report["alarms"]) == {"daily_bars_day_over_6mb", "lineage_day_over_1_5x"}
    with pytest.raises(DailyBarsBudgetExceeded):
        store.enforce_budget()


# --- SOURCE-MARKET-STORE-BACKFILL-AND-CLEANUP-20261118-R2: sweep, verify, symbol stats ----------------------

def test_sweep_interrupted_removes_temp_and_unreferenced_parts_only(tmp_path):
    store = DailyBarsStore(tmp_path)
    store.write_records([record("2330.TW", DAY), record("2317.TW", DAY)], ingest_run_id="r1")
    before = tree_files(tmp_path)
    killed = DailyBarsStore(tmp_path, fault=lambda step: (_ for _ in ()).throw(KeyboardInterrupt()) if step == "before_manifest_rename" else None)
    with pytest.raises(KeyboardInterrupt):
        killed.write_records([record("2330.TW", DAY, 11.0), record("2317.TW", "2026-11-14")], ingest_run_id="r2")
    assert tree_files(tmp_path) != before
    removed = DailyBarsStore(tmp_path).sweep_interrupted()
    assert removed["temp_files"] >= 1 or removed["orphan_parts"] >= 1
    assert [f for f in tree_files(tmp_path) if "2026-11-13" in f and "_manifest" not in f and "lineage" not in f] == [
        f for f in before if "2026-11-13" in f and "_manifest" not in f and "lineage" not in f]
    assert DailyBarsStore(tmp_path).verify_day(DAY)["verified"]


def test_verify_day_names_a_corrupt_or_missing_day(tmp_path):
    store = DailyBarsStore(tmp_path)
    store.write_records([record("2330.TW", DAY)], ingest_run_id="r1")
    assert store.verify_day(DAY)["verified"] is True
    assert store.verify_day("2026-01-01") == {"trade_date": "2026-01-01", "verified": False, "reason": "no_manifest"}
    part = next((tmp_path / "daily_bars").rglob("part-*.parquet"))
    part.write_bytes(b"x")
    out = store.verify_day(DAY)
    assert out["verified"] is False and "different size" in out["reason"]


def test_symbol_stats_counts_days_and_bounds_without_loading_rows(tmp_path):
    store = DailyBarsStore(tmp_path)
    store.write_records([record("2330.TW", "2026-11-12"), record("2317.TW", "2026-11-12")], ingest_run_id="r1")
    store.write_records([record("2330.TW", "2026-11-13")], ingest_run_id="r2")
    assert store.symbol_stats() == {"2330.TW": (2, "2026-11-12", "2026-11-13"), "2317.TW": (1, "2026-11-12", "2026-11-12")}
    assert store.symbol_stats("2026-11-13", "2026-11-13") == {"2330.TW": (1, "2026-11-13", "2026-11-13")}


# --- SOURCE-DAILY-BARS-VALUE-TIME-STATUS-COLUMNS-20261118: traded value, available time, trading status -------
import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

COLS = ["symbol", "trade_date", "close", "volume", "traded_value", "available_time", "trading_status"]


def read_cols(root, symbols, day=DAY):
    return DailyBarsReader(root).read_range(symbols, day, day, columns=COLS)


def test_columns_follow_the_source_fields_and_null_is_never_zero(tmp_path):
    store = DailyBarsStore(tmp_path)
    store.write_records([
        record("2330.TW", DAY, 10.0, volume=100.0, value=1234.5, available_time="2026-11-13T15:05:06.123456+08:00",
               trading_status="traded"),
        record("2317.TW", DAY, 20.0, volume=50.0, value_thousands=7.0, available_time=DAY),
        record("2454.TW", DAY, 30.0, volume=0.0, value=0.0, available_time=DAY),   # a real zero stays zero
        record("2603.TW", DAY, 40.0, volume=5.0),                                     # the source gave nothing
    ], ingest_run_id="r1")
    got = read_cols(tmp_path, ["2330.TW", "2317.TW", "2454.TW", "2603.TW"])
    a, b, c, d = (got[s][0] for s in ("2330.TW", "2317.TW", "2454.TW", "2603.TW"))
    assert a["traded_value"] == 1234.5 and a["trading_status"] == "traded"
    assert a["available_time"] == datetime(2026, 11, 13, 7, 5, 6, 123456, tzinfo=timezone.utc)
    assert b["traded_value"] == 7000.0 and b["trading_status"] is None
    assert b["available_time"] == datetime(2026, 11, 13, 6, 30, tzinfo=timezone.utc)  # date only: 14:30 +08:00
    assert c["traded_value"] == 0.0
    assert d["traded_value"] is None and d["available_time"] is None and d["trading_status"] is None
    manifest = store.read_manifest(DAY)
    assert manifest["schema_version"] == "daily_bars_manifest.v2"
    assert {"traded_value", "available_time", "trading_status"} <= set(manifest["columns"])
    assert manifest["key_fields"] == ["market", "symbol", "trade_date"]


def test_copies_follow_the_evaluator_rule_and_conflicts_are_rejected(tmp_path):
    store = DailyBarsStore(tmp_path)
    store.write_records([
        record("2330.TW", DAY, 10.0, open=9.0, high=11.0, low=8.0, volume=100.0, value=1000.0, available_time="2026-11-13T14:00:00+08:00"),
        record("2330.TW", DAY, 10.0, open=9.0, high=11.0, low=8.0, volume=300.0, value=3000.0, available_time="2026-11-13T13:00:00+08:00"),
        record("2330.TW", DAY, 10.0, open=9.0, high=11.0, low=8.0, volume=200.0, value=9999.0, available_time="2026-11-13T15:00:00+08:00"),
        record("2317.TW", DAY, 10.0, open=9.0, volume=1.0),
        record("2317.TW", DAY, 11.0, open=9.0, volume=1.0),
    ], ingest_run_id="r1")
    row = read_cols(tmp_path, ["2330.TW"])["2330.TW"][0]
    assert (row["volume"], row["traded_value"]) == (300.0, 3000.0)  # the largest volume supplies both, never mixed
    assert row["available_time"] == datetime(2026, 11, 13, 7, 0, tzinfo=timezone.utc)  # the latest of all copies
    assert read_cols(tmp_path, ["2317.TW"])["2317.TW"] == []
    assert store.last_rejections == {"conflicting_duplicate": 1}


def test_digest_covers_new_columns_so_rerun_is_idempotent_and_adding_a_value_replaces(tmp_path):
    store = DailyBarsStore(tmp_path)
    base = dict(volume=100.0, available_time="2026-11-13T15:00:00+08:00", trading_status="traded")
    assert store.write_records([record("2330.TW", DAY, 10.0, **base)], ingest_run_id="r1")["days_written"] == [DAY]
    digest = store.read_manifest(DAY)["content_digest"]
    again = store.write_records([record("2330.TW", DAY, 10.0, **base)], ingest_run_id="r2")
    assert again["days_unchanged"] == [DAY] and store.read_manifest(DAY)["content_digest"] == digest
    files = tree_files(tmp_path)
    store.write_records([record("2330.TW", DAY, 10.0, value=1000.0, **base)], ingest_run_id="r3")
    assert store.read_manifest(DAY)["content_digest"] != digest
    assert read_cols(tmp_path, ["2330.TW"])["2330.TW"][0]["traded_value"] == 1000.0
    assert tree_files(tmp_path) != files


def test_not_traded_is_stored_with_null_prices_and_price_readers_skip_it(tmp_path):
    store = DailyBarsStore(tmp_path)
    out = store.write_records([
        record("2330.TW", DAY, 10.0, volume=1.0, trading_status="traded"),
        SourceRecord(source_id="s-1101", connector_id="c", source_type="market", title="t", content_ref="r",
                                  metadata={"symbol": "1101.TW", "trade_date": DAY, "market": "TW", "trading_status": "not_traded"}),
        record("1102.TW", DAY, 0.0),  # a zero close without the status is still bad_close
    ], ingest_run_id="r1")
    assert out["rejected"] == {"bad_close": 1}
    reader = DailyBarsReader(tmp_path)
    assert [r["symbol"] for r in reader.read_range(["2330.TW", "1101.TW"], DAY, DAY, columns=["symbol", "trade_date", "close"])["2330.TW"]] == ["2330.TW"]
    assert reader.read_range(["1101.TW"], DAY, DAY)["1101.TW"] == []  # no bar
    asked = reader.read_range(["1101.TW"], DAY, DAY, columns=COLS)["1101.TW"]
    assert len(asked) == 1 and asked[0]["close"] is None and asked[0]["traded_value"] is None
    assert asked[0]["trading_status"] == "not_traded"
    assert [r["symbol"] for r in reader.read_not_traded(DAY)] == ["1101.TW"]
    assert "1101.TW" not in reader.list_symbols() and "1101.TW" not in store.symbol_stats()
    assert reader.availability("1101.TW")["1101.TW"]["count"] == 0
    assert store.verify_day(DAY)["verified"]
    contradictory = store.write_records([record("2412.TW", DAY, 9.0, trading_status="not_traded")], ingest_run_id="r2")
    assert contradictory["rejected"] == {"contradictory_status": 1}


def test_a_caller_that_does_not_ask_reads_no_new_column(tmp_path):
    store = DailyBarsStore(tmp_path)
    store.write_records([record("2330.TW", DAY, 10.0, volume=1.0, value=5.0, available_time=DAY, trading_status="traded")], ingest_run_id="r1")
    seen = []
    original = pq.ParquetFile.read_row_groups

    def spy(self, groups, columns=None, **kw):
        seen.append(list(columns or []))
        return original(self, groups, columns=columns, **kw)

    pq.ParquetFile.read_row_groups = spy
    try:
        reader = DailyBarsReader(tmp_path)
        reader.read_range(["2330.TW"], DAY, DAY)
        reader.read_range(["2330.TW"], DAY, DAY, columns=["symbol", "trade_date", "close"])
        reader.availability("2330.TW")
        reader.read_range(["2330.TW"], DAY, DAY, columns=["symbol", "trade_date", "traded_value"])
    finally:
        pq.ParquetFile.read_row_groups = original
    new = {"traded_value", "available_time", "trading_status"}
    assert all(not (set(c) & new) for c in seen[:-1]), seen
    assert set(seen[-1]) == {"symbol", "trade_date", "traded_value"}


def _write_v1_day(root, trade_date, symbols):
    """A partition as the code before this task wrote it: nine value fields, manifest v1, no new columns."""
    schema = pa.schema([
        ("market", pa.string()), ("symbol", pa.string()), ("trade_date", pa.string()),
        ("open", pa.float64()), ("high", pa.float64()), ("low", pa.float64()), ("close", pa.float64()),
        ("volume", pa.float64()), ("adjusted", pa.bool_()), ("source_id", pa.string()),
        ("ingest_run_id", pa.string()), ("lineage_ref", pa.string()), ("content_hash", pa.string())])
    rows = []
    for s in sorted(symbols):
        ohlcv = {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0, "adjusted": False}
        rows.append({"market": "TW", "symbol": s, "trade_date": trade_date, **ohlcv, "source_id": "s",
                     "ingest_run_id": "old", "lineage_ref": "old", "content_hash": dbs._sha(ohlcv)})
    part = root / "daily_bars" / "market=TW" / f"date={trade_date}" / "part-g1-0.parquet"
    part.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), part, compression="zstd")
    manifest = {"schema_version": "daily_bars_manifest.v1", "dataset": "daily_bars", "key_fields": ["market", "symbol", "trade_date"],
                "trade_date": trade_date, "generation": 1, "row_count": len(rows), "symbol_count": len(rows),
                "bytes": part.stat().st_size, "content_digest": DailyBarsStore.day_digest(rows),
                "files": [{"path": part.relative_to(root / "daily_bars").as_posix(), "market": "TW", "rows": len(rows),
                           "bytes": part.stat().st_size, "min_symbol": rows[0]["symbol"], "max_symbol": rows[-1]["symbol"]}]}
    (root / "daily_bars" / "_manifest").mkdir(parents=True, exist_ok=True)
    (root / "daily_bars" / "_manifest" / f"date={trade_date}.json").write_text(json.dumps(manifest))
    return rows


def test_v1_partition_reads_nulls_and_is_upgraded_when_the_date_is_next_written(tmp_path):
    _write_v1_day(tmp_path, DAY, ["2330.TW", "2317.TW"])
    got = read_cols(tmp_path, ["2330.TW"])["2330.TW"][0]
    assert got["close"] == 1.5 and got["traded_value"] is None and got["available_time"] is None and got["trading_status"] is None
    store = DailyBarsStore(tmp_path)
    assert store.write_records([record("2330.TW", DAY, 1.5, open=1.0, high=2.0, low=0.5, volume=10.0, value=15.0,
                                       available_time=DAY, trading_status="traded")], ingest_run_id="r1")["days_written"] == [DAY]
    manifest = store.read_manifest(DAY)
    assert manifest["schema_version"] == "daily_bars_manifest.v2" and manifest["generation"] == 2
    assert manifest["row_count"] == 2  # the other v1 row is kept
    new = read_cols(tmp_path, ["2330.TW", "2317.TW"])
    assert new["2330.TW"][0]["traded_value"] == 15.0
    assert new["2317.TW"][0]["close"] == 1.5 and new["2317.TW"][0]["traded_value"] is None
    assert store.verify_day(DAY)["verified"]


def test_v1_row_with_the_same_prices_is_replaced_because_the_hash_covers_the_new_columns(tmp_path):
    _write_v1_day(tmp_path, DAY, ["2330.TW"])
    store = DailyBarsStore(tmp_path)
    out = store.write_records([record("2330.TW", DAY, 1.5, open=1.0, high=2.0, low=0.5, volume=10.0)], ingest_run_id="r1")
    assert out["days_written"] == [DAY]  # the day digest changes once for the upgrade


def test_kill_in_the_middle_of_an_upgrade_leaves_the_previous_complete_partition(tmp_path):
    before_rows = _write_v1_day(tmp_path, DAY, ["2330.TW", "2317.TW"])
    for step in ("after_temp_write", "after_fsync", "after_lineage", "before_manifest_rename"):
        killed = DailyBarsStore(tmp_path, fault=lambda s, step=step: (_ for _ in ()).throw(KeyboardInterrupt()) if s == step else None)
        with pytest.raises(KeyboardInterrupt):
            killed.write_records([record("2330.TW", DAY, 1.5, open=1.0, high=2.0, low=0.5, volume=10.0, value=15.0, available_time=DAY)], ingest_run_id="r9")
        seen = DailyBarsReader(tmp_path).read_range(["2330.TW", "2317.TW"], DAY, DAY, columns=COLS)
        assert [seen[s][0]["close"] for s in ("2330.TW", "2317.TW")] == [1.5, 1.5]
        assert seen["2330.TW"][0]["traded_value"] is None  # still the previous complete partition
        assert DailyBarsStore(tmp_path).verify_day(DAY)["verified"]
        DailyBarsStore(tmp_path).sweep_interrupted()
    assert len(before_rows) == 2


def test_lineage_keeps_source_endpoint_venue_and_bundle_once_per_run(tmp_path):
    store = DailyBarsStore(tmp_path)
    collector = {"source_id": "collector-tw-twse-price-history", "bundle_sha256": "ab" * 32}
    store.write_records([
        record("2330", DAY, 10.0, exchange="TWSE", upstream_endpoint="MI_INDEX", trading_status="traded", collector=collector),
        SourceRecord(source_id="s-1101", connector_id="c", source_type="market", title="t", content_ref="r",
                     metadata={"symbol": "1101", "trade_date": DAY, "market": "TW", "exchange": "TWSE", "upstream_endpoint": "MI_INDEX",
                               "trading_status": "not_traded", "collector": collector}),
    ], ingest_run_id="run-a", default_market="TW")
    lineage = json.loads((tmp_path / "lineage" / f"date={DAY}.json").read_text())
    caps = lineage["runs"]["run-a"]["captures"]
    assert len(caps) == 2  # one per row source id (the join key with the row's source_id column), not per row
    assert {c["venue"] for c in caps} == {"TWSE"} and {c["upstream_endpoint"] for c in caps} == {"MI_INDEX"}
    assert {c["collector_source_id"] for c in caps} == {"collector-tw-twse-price-history"}
    assert {c["bundle_sha12"] for c in caps} == {"ab" * 6} and all(c["status_known"] for c in caps)


def test_budget_bytes_at_the_measured_listing_size_and_bytes_per_row(tmp_path):
    """About 53,000 listed symbols (2,075 shares and ETFs, 50,883 warrants) in 500 record batches, with the three
    new columns filled as the hosted shape fills them: the day and lineage limits of the amended section 6."""
    store = DailyBarsStore(tmp_path)
    n, warrants = 52_958, 50_883
    rows = make_rows(n, warrants=warrants)
    for i, row in enumerate(rows):
        row["traded_value"] = float(row["close"] * row["volume"])
        row["available_time"] = datetime(2026, 11, 13, 6, 30, tzinfo=timezone.utc)
        row["trading_status"] = "traded"
        row["content_hash"] = dbs._row_hash(row)
    for i in range(0, n, 500):
        store.write_day(DAY, rows[i:i + 500], ingest_run_id=f"run-{i // 500}", connector_id="c")
    manifest = store.read_manifest(DAY)
    assert manifest["row_count"] == n
    assert manifest["bytes"] <= dbs.DAILY_BARS_DAY_TARGET_BYTES, manifest["bytes"]
    assert manifest["bytes"] / n <= 75, manifest["bytes"] / n  # the design estimate per row
    lineage_bytes = (tmp_path / "lineage" / f"date={DAY}.json").stat().st_size
    assert lineage_bytes <= dbs.LINEAGE_DAY_TARGET_BYTES, lineage_bytes
    assert store.enforce_budget()["alarms"] == []
    for name, value in (("DAILY_BARS_DAY_TARGET_BYTES", 4_000_000), ("DAILY_BARS_DAY_ALARM_BYTES", 6_000_000),
                        ("DAILY_BARS_TOTAL_CEILING_BYTES", 2_500_000_000), ("LINEAGE_DAY_TARGET_BYTES", 400_000),
                        ("LINEAGE_DAY_ALARM_BYTES", 600_000), ("LINEAGE_TOTAL_CEILING_BYTES", 200_000_000)):
        assert getattr(dbs, name) == value


def test_a_day_over_the_limit_is_refused_by_enforce_budget(tmp_path, monkeypatch):
    store = DailyBarsStore(tmp_path)
    store.write_day(DAY, make_rows(3000), ingest_run_id="r")
    size = store.read_manifest(DAY)["bytes"]
    monkeypatch.setattr(dbs, "DAILY_BARS_DAY_ALARM_BYTES", size - 1)
    with pytest.raises(DailyBarsBudgetExceeded):
        store.enforce_budget()
