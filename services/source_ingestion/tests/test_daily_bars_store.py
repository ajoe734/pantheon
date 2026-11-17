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
    assert set(report["alarms"]) == {"daily_bars_day_over_2_5mb", "lineage_day_over_1_5x"}
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
