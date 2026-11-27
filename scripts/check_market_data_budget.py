#!/usr/bin/env python3
"""Fail when a market data store exceeds its growth budget (design sections 6 and 6A).

Design of record: docs/04/pantheon_data_strategy_source_design_2026-06-09/
SD_MARKET_DATA_ONE_WRITE_PER_DAY_2026-11-16.md.

The limits are READ AS DATA from the section 6 and 6A tables of that file at the checked-out
head; nothing numeric is copied into this script except the hosted-shaped volume (rows per
dataset, trading days) that the design states in prose. Each table row must have exactly one
of: a measuring checker, or a named waiver with a reason (a store that has no implementation
yet). A row with neither, or a checker whose row was removed from the table, fails closed.

Measurements:
  * ``daily_bars`` / ``lineage``: the real ``DailyBarsStore`` writing 52,958 symbols (50,883
    listed warrants, rows with traded_value, available_time, trading_status) in 500-record batches, bytes read from the manifest and lineage file.
  * ``market_snapshot_current``: the SQLite table of design 4.2 (stdlib) loaded with 22,500
    keys of a 60-close record in 500-key transactions, then fully replaced under a reader.
  * ``listing_allowlist``: generated rows measured as zlib-compressed canonical JSON.
  * every 6A dataset with a ``KEYED_DATASETS`` declaration (SOURCE-DATASETS-KEYED-WRITE-20261119): hosted-shaped
    records written through the real ``KeyedDatasetStore`` in 500-record batches, bytes read from its manifest; the
    limit is the design table row, and the declaration's own ``day_budget_bytes`` must not exceed it.
    A 6A dataset without a declaration falls back to the zlib proxy.
``--live-stats FILE`` additionally checks measured values from a live read (JSON object
``{store: {metric: value}}``, metrics named as in the report) against the same limits.

Exit code 0 only when no check failed. Output is JSON; no credential is read or written.
"""
from __future__ import annotations

import functools
import argparse
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import zlib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
DESIGN_PATH = (
    REPO_ROOT / "docs" / "04" / "pantheon_data_strategy_source_design_2026-06-09"
    / "SD_MARKET_DATA_ONE_WRITE_PER_DAY_2026-11-16.md"
)
SCHEMA_VERSION = "market_data_budget_check.v1"

# Hosted-shaped volume of design section 6 (prose) -------------------------------------------
LISTED_SYMBOLS = 22_500
LISTED_WARRANTS = 20_200
TRADING_DAYS = 500
# daily_bars and lineage follow section 6 as amended 2026-11-17: 52,958 listed symbols (2,075 shares and ETFs
# plus 50,883 warrants), about 53,000. The other stores keep their own volume until their tasks amend it.
DAILY_BARS_SYMBOLS = 52_958
DAILY_BARS_WARRANTS = 50_883
BATCH = 500
SNAPSHOT_CLOSES = 60
SNAPSHOT_PAGE_SIZE = 16_384

UNITS = {"B": 1, "KB": 1_000, "MB": 1_000_000, "GB": 1_000_000_000}
_QTY = r"([\d][\d,]*(?:\.\d+)?)\s*(KB|MB|GB)"


class BudgetError(RuntimeError):
    """Readable reason; the check fails closed on it."""


# -- design table reader -----------------------------------------------------------------------
def _section(text: str, heading_prefix: str) -> str:
    match = re.search(rf"^## {re.escape(heading_prefix)}.*?$", text, re.M)
    if not match:
        raise BudgetError(f"design section {heading_prefix!r} not found")
    rest = text[match.end():]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def parse_table(section: str) -> list[list[str]]:
    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        rows.append(cells)
    if len(rows) < 2:
        raise BudgetError("no table rows found in design section")
    return rows[1:]  # drop header


def row_key(cells: list[str]) -> str:
    ticks = re.findall(r"`([^`]+)`", cells[0])
    return ticks[0] if ticks else cells[0]


def load_design_rows(path: Path = DESIGN_PATH) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        raise BudgetError(f"design file missing: {path}")
    text = path.read_text(encoding="utf-8")
    rows: dict[str, dict[str, Any]] = {}
    for sec, prefix in (("6", "6. Growth budget"), ("6A", "6A.")):
        for cells in parse_table(_section(text, prefix)):
            key = row_key(cells)
            if key in rows:
                raise BudgetError(f"duplicate table row {key!r}")
            rows[key] = {"section": sec, "cells": cells}
    return rows


def limit(cells: list[str], col: int, pattern: str, label: str, *, unit_scale: bool = True) -> float:
    """Extract one numeric limit from a table cell; fail closed when it cannot be read."""
    if col >= len(cells):
        raise BudgetError(f"{label}: table row has no column {col}")
    match = re.search(pattern, cells[col])
    if not match:
        raise BudgetError(f"{label}: cannot read limit /{pattern}/ in column {col}: {cells[col][:80]!r}")
    value = float(match.group(1).replace(",", ""))
    if unit_scale and len(match.groups()) >= 2 and match.group(2) in UNITS:
        value *= UNITS[match.group(2)]
    return value


def _le(prefix: str = "") -> str:
    return prefix + r"<=\s*" + _QTY


# -- generated data ----------------------------------------------------------------------------
def _zbytes(rows: list[Any]) -> int:
    return len(zlib.compress(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode(), 6))


def _hash(i: int) -> str:
    return f"{(i * 2654435761) & 0xFFFFFFFFFFFFFFFF:016x}{(i * 40503) & 0xFFFFFFFFFFFFFFFF:016x}"


def daily_bars_rows(trade_date: str) -> list[dict[str, Any]]:
    from services.source_ingestion import daily_bars_store as dbs

    rows = []
    available = datetime.fromisoformat(f"{trade_date}T14:30:00+08:00").astimezone(timezone.utc)
    shares = DAILY_BARS_SYMBOLS - DAILY_BARS_WARRANTS
    for i in range(DAILY_BARS_SYMBOLS):
        symbol = f"{1000 + i}.TW" if i < shares else f"0{3 + i % 6}{i:05d}"[:7]
        ohlcv = {"open": 10.0 + i % 50, "high": 11.0 + i % 50, "low": 9.0 + (i % 7) / 10,
                 "close": 10.5 + i % 50 + (i % 13) / 100, "volume": float(1000 + 37 * i), "adjusted": False}
        run = f"run-{i // BATCH}"
        rows.append({"market": "TW", "symbol": symbol, "trade_date": trade_date, **ohlcv,
                     "traded_value": round((10.5 + i % 50) * (1000 + 37 * i), 2),
                     "available_time": available, "trading_status": "traded",
                     "source_id": f"src-{symbol}", "ingest_run_id": run, "lineage_ref": run,
                     "content_hash": dbs._sha(ohlcv)})
    return rows


@functools.lru_cache(maxsize=1)  # one 53k-row measurement per process; the design text does not change it
def measure_daily_bars() -> dict[str, float]:
    try:
        from services.source_ingestion.daily_bars_store import DailyBarsStore
    except ImportError as exc:  # pyarrow missing is a failure, never a pass
        raise BudgetError(f"daily_bars store not importable (pyarrow installed?): {exc}") from exc
    tmp = Path(tempfile.mkdtemp(prefix="budget-daily-bars-"))
    try:
        store = DailyBarsStore(tmp)
        day = "2026-11-13"
        rows = daily_bars_rows(day)
        for i in range(0, len(rows), BATCH):
            store.write_day(day, rows[i:i + BATCH], ingest_run_id=f"run-{i // BATCH}", connector_id="budget")
        manifest = store.read_manifest(day)
        if not manifest or manifest["row_count"] != DAILY_BARS_SYMBOLS:
            raise BudgetError(f"daily_bars wrote {manifest and manifest['row_count']} rows, expected {DAILY_BARS_SYMBOLS}")
        lineage = (tmp / "lineage" / f"date={day}.json").stat().st_size
        return {"rows": float(manifest["row_count"]), "day_bytes": float(manifest["bytes"]),
                "bytes_per_row": float(manifest["bytes"]) / manifest["row_count"],
                "lineage_day_bytes": float(lineage)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def snapshot_body(i: int, version: int) -> bytes:
    """Design 4.2 compact record: header once, 60 points of (trade_date, close, content_ref, run id)."""
    points = [[f"2026-{8 + (d // 28):02d}-{1 + d % 28:02d}", round(10 + (i % 90) + d / 100 + version, 2),
               _hash(i * 100 + d), d % 4] for d in range(SNAPSHOT_CLOSES)]
    return json.dumps({"s": f"src-{i}", "c": "tw-daily", "m": "TW", "cal": 1, "runs": [f"r{k}" for k in range(4)],
                       "points": points}, separators=(",", ":")).encode()


def measure_snapshot_sqlite() -> dict[str, float]:
    tmp = Path(tempfile.mkdtemp(prefix="budget-snapshot-"))
    path = tmp / "snapshot.sqlite"
    try:
        con = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        # page_size is not in the design's pragma list. Measured on this record shape (22,500 keys, full
        # replacement): file/live = 1.32 at 4096 (over the 1.3 alarm), 2.63 at 8192, 1.15 at 16384.
        con.execute(f"pragma page_size={SNAPSHOT_PAGE_SIZE}")
        con.execute("pragma auto_vacuum=INCREMENTAL")
        con.execute("pragma journal_mode=WAL")
        con.execute("pragma synchronous=FULL")
        con.execute("pragma busy_timeout=2000")
        con.execute("create table market_snapshot_current(symbol text, dataset text, last_trade_date text,"
                    " window_digest text, body blob, observed_at text, primary key(symbol, dataset)) without rowid")
        upsert = ("insert into market_snapshot_current values(?,?,?,?,?,?) on conflict(symbol,dataset) do update set"
                  " last_trade_date=excluded.last_trade_date, window_digest=excluded.window_digest,"
                  " body=excluded.body, observed_at=excluded.observed_at"
                  " where window_digest <> excluded.window_digest")
        max_record = 0
        txn_ms: list[float] = []

        def batches(version: int):
            for start in range(0, LISTED_SYMBOLS, BATCH):
                batch = []
                for i in range(start, min(start + BATCH, LISTED_SYMBOLS)):
                    body = snapshot_body(i, version)
                    batch.append((f"S{i:05d}", "tw_price_daily", f"2026-11-{13 + version:02d}",
                                  f"d{version}-{i}", body, "2026-11-13T10:00:00Z"))
                yield batch

        def write(version: int) -> None:
            nonlocal max_record
            for batch in batches(version):
                max_record = max(max_record, max(len(r[4]) for r in batch))
                t0 = time.perf_counter()
                con.execute("begin immediate")
                con.executemany(upsert, batch)
                con.execute("commit")
                txn_ms.append((time.perf_counter() - t0) * 1000)

        write(0)
        read_ms: list[float] = []
        stop = threading.Event()

        def reader() -> None:
            rcon = sqlite3.connect(path, check_same_thread=False)
            i = 0
            while not stop.is_set():
                i = (i * 7919 + 13) % LISTED_SYMBOLS
                t0 = time.perf_counter()
                rcon.execute("select body from market_snapshot_current where symbol=? and dataset=?",
                             (f"S{i:05d}", "tw_price_daily")).fetchone()
                read_ms.append((time.perf_counter() - t0) * 1000)
            rcon.close()

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        txn_ms.clear()
        write(1)  # the daily full replacement
        stop.set()
        thread.join(timeout=5)
        con.execute("pragma incremental_vacuum(200)")
        con.execute("pragma wal_checkpoint(TRUNCATE)")
        live = con.execute("select coalesce(sum(length(body)),0) from market_snapshot_current").fetchone()[0]
        count = con.execute("select count(*) from market_snapshot_current").fetchone()[0]
        con.close()
        if count != LISTED_SYMBOLS:
            raise BudgetError(f"snapshot table holds {count} keys after replacement, expected {LISTED_SYMBOLS}")
        wal = path.with_name(path.name + "-wal")
        return {"keys": float(count), "max_record_bytes": float(max_record), "live_bytes": float(live),
                "file_bytes": float(path.stat().st_size), "wal_bytes": float(wal.stat().st_size if wal.exists() else 0),
                "writer_txn_ms_max": max(txn_ms), "reader_call_ms_max": max(read_ms) if read_ms else 0.0}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def listing_rows() -> list[Any]:
    return [[f"{i:06d}", "TW", "warrant" if i >= LISTED_SYMBOLS - LISTED_WARRANTS else "share",
             "2026-11-13", _hash(i)[:16]] for i in range(LISTED_SYMBOLS)]


def _rows(n: int, make: Callable[[int], Any]) -> list[Any]:
    return [make(i) for i in range(n)]


# Row counts per 6A dataset are the design's own figures (growth column prose).
DATASET_ROWS: dict[str, Callable[[], list[Any]]] = {
    "tw_index_daily": lambda: _rows(40, lambda i: [f"IDX{i:02d}", "2026-11-13", 100.0 + i, 0.5, 1000 + i]),
    "tw_index_ohlc_daily": lambda: _rows(40, lambda i: [f"IDX{i:02d}", "2026-11-13", 99.0, 101.0, 98.5, 100.2, _hash(i)[:8]]),
    "tw_market_breadth_daily": lambda: _rows(2, lambda i: [["TWSE", "TPEX"][i], "2026-11-13", 800, 600, 120, 1.3]),
    "tw_institutional_flow": lambda: _rows(1_900 * 3, lambda i: [f"{1000 + i // 3}", "2026-11-13", ("foreign", "trust", "dealer")[i % 3], 1000 + i % 997, 900 + i % 89, (i % 211) - 100]),
    "tw_margin_short_balance": lambda: _rows(1_900, lambda i: [f"{1000 + i}", "2026-11-13", 5000 + i % 977, 300 + i % 97, 4800 + i % 911]),
    "tw_broker_top": lambda: _rows(200 * 2 * 15, lambda i: [f"{1000 + i // 30}", "2026-11-13", ("buy", "sell")[i % 2], (i // 2) % 15 + 1, f"B{(i * 31) % 997:03d}", 100 + i % 313, 90 + i % 307]),
    "tw_broker_branch_detail": lambda: _rows(200 * 15 * 3, lambda i: [f"{1000 + i // 45}", "2026-11-13", f"B{(i // 3) % 997:03d}", f"BR{i % 3}", 10 + i % 53, 9 + i % 47]),
    "taifex_ssf_contracts": lambda: _rows(600, lambda i: [f"SSF{i:04d}", "2026-11-13", 10.0 + i % 90, 100 + i % 700, 50 + i % 300]),
    "taifex_tx_daily": lambda: _rows(4, lambda i: [["TX", "MTX", "TX2", "MTX2"][i], "2026-11-13", 21000.0 + i, 21100.0, 20900.0, 21050.0, 90000]),
    "tw_macro_series": lambda: _rows(100, lambda i: [f"M{i:03d}", "2026-10", 100.0 + i / 10]),
    "tw_community_daily": lambda: _rows(2_000 * 2, lambda i: [f"{1000 + i // 2}", "2026-11-13", ("ptt", "dcard")[i % 2], i % 37, i % 11]),
    "collector_news": lambda: _rows(45, lambda i: [f"https://example.test/n/{i}", "2026-11-13T10:00:00Z", _hash(i), "x" * 4_000]),
}


def _keyed_record(name: str, i: int, n: int):
    from services.source_ingestion.connectors import SourceRecord
    from services.source_ingestion.market_data_storage import _compact_keyed_record

    day = "2026-11-13"
    row: dict[str, Any] = {"dataset": name, "date": day, "event_time": f"{day}T00:00:00Z", "value": float(i % 997) + 0.5,
                           "available_time": f"{day}T10:00:00Z", "ingest_time": f"t{i}",
                           "a": i % 977, "b": 300 + i % 97, "c": (i % 211) - 100}
    sym = f"{1000 + i}"
    row.update({
        "tw_index_daily": {"index_code": f"IDX{i:02d}"}, "tw_index_ohlc_daily": {"index_code": f"IDX{i:02d}"},
        "tw_market_breadth_daily": {"market": f"M{i}"},
        "tw_institutional_flow": {"symbol": f"{1000 + i // 3}", "investor_class": ("foreign", "trust", "dealer")[i % 3]},
        "tw_margin_short_balance": {"symbol": sym}, "tw_broker_top": {"symbol": f"{1000 + i // 30}", "side": ("buy", "sell")[i % 2], "rank": (i // 2) % 15 + 1},
        "tw_broker_branch_detail": {"symbol": f"{1000 + i // 45}", "broker_id": f"B{i // 3}", "branch_id": f"BR{i % 3}"},
        "taifex_ssf_contracts": {"contract_id": f"ssf:C{i}:202611"}, "taifex_tx_daily": {"contract_id": f"TX{i}"},
        "tw_macro_series": {"series_code": f"M{i:03d}", "date": "2026-10"},
        "tw_community_daily": {"symbol": f"{1000 + i // 2}", "platform": ("ptt", "dcard")[i % 2]},
        "collector_news": {"url": f"https://example.test/n/{i}", "body": f"{i:04d} " + "x" * 3_500},
        "collector_social_posts": {"url": f"https://example.test/p/{i}", "body": f"{i:04d} " + "x" * 3_500},
    }[name])
    rec = SourceRecord(source_id=f"s{i}", connector_id="budget", source_type="market", title="t", content_ref=f"r{i}",
                       metadata={"dataset": name, "normalized_row": row, "event_time": row["event_time"],
                                 "available_time": row["available_time"]})
    return _compact_keyed_record(rec, name)


KEYED_ROW_COUNTS = {"collector_news": 45, "collector_social_posts": 30}


def measure_dataset(name: str) -> dict[str, float]:
    try:
        from services.source_ingestion.daily_bars_store import KEYED_DATASETS, KeyedDatasetStore
    except ImportError as exc:
        raise BudgetError(f"keyed store not importable: {exc}") from exc
    decl = KEYED_DATASETS.get(name)
    if decl is None:  # no keyed writer yet: zlib proxy
        rows = DATASET_ROWS[name]()
        return {"rows": float(len(rows)), "day_bytes": float(_zbytes(rows))}
    n = KEYED_ROW_COUNTS.get(name) or len(DATASET_ROWS[name]())
    tmp = Path(tempfile.mkdtemp(prefix=f"budget-{name}-"))
    try:
        store = KeyedDatasetStore(tmp, replace(decl, day_budget_bytes=None))  # measure, do not let the writer refuse
        records = [_keyed_record(name, i, n) for i in range(n)]
        for i in range(0, n, BATCH):
            store.write_records(records[i:i + BATCH], ingest_run_id="budget")
        report = store.budget_report()
        return {"rows": float(n), "day_bytes": float(report["max_day_bytes"]),
                "declared_budget_bytes": float(decl.day_budget_bytes or 0), "keyed": 1.0}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def measure_listing() -> dict[str, float]:
    rows = listing_rows()
    return {"rows": float(len(rows)), "day_bytes": float(_zbytes(rows))}


# -- checks ------------------------------------------------------------------------------------
def _result(store: str, metric: str, measured: float, bound: float, op: str, source: str, status: str | None = None,
            reason: str | None = None) -> dict[str, Any]:
    ok = measured <= bound
    return {"store": store, "metric": metric, "measured": round(measured, 3), "limit": bound, "op": op,
            "source": source, "status": status or ("pass" if ok else "fail"),
            "reason": reason or (None if ok else f"{store} {metric} {measured:,.0f} exceeds {bound:,.0f} ({op})")}


def check_daily_bars(cells: list[str], m: dict[str, float]) -> list[dict[str, Any]]:
    day = limit(cells, 1, _le(), "daily_bars day target")
    ceiling = limit(cells, 2, r"ceiling\s+" + _QTY, "daily_bars total ceiling")
    alarm = limit(cells, 3, r"day\s*>\s*" + _QTY, "daily_bars day alarm")
    total = m["day_bytes"] * TRADING_DAYS
    return [
        _result("daily_bars", "day_bytes", m["day_bytes"], day, "<=", "sec6 size per day"),
        _result("daily_bars", "day_bytes_alarm", m["day_bytes"], alarm, "<=", "sec6 alarm"),
        _result("daily_bars", f"total_bytes_{TRADING_DAYS}_days", total, ceiling, "<=", "sec6 total ceiling"),
    ]


def check_lineage(cells: list[str], m: dict[str, float]) -> list[dict[str, Any]]:
    day = limit(cells, 1, _le(), "lineage day")
    total = limit(cells, 2, _le(), "lineage total")
    return [
        _result("lineage", "day_bytes", m["lineage_day_bytes"], day, "<=", "sec6 size per day"),
        _result("lineage", f"total_bytes_{TRADING_DAYS}_days", m["lineage_day_bytes"] * TRADING_DAYS, total, "<=",
                "sec6 total bound"),
    ]


def check_snapshot(cells: list[str], m: dict[str, float]) -> list[dict[str, Any]]:
    record = limit(cells, 3, r"record\s*>\s*" + _QTY, "snapshot record")
    live = limit(cells, 2, r"live\s*<=\s*" + _QTY, "snapshot live ceiling")
    wal = limit(cells, 2, r"WAL\s*<=\s*" + _QTY, "snapshot WAL")
    file_alarm = limit(cells, 3, r"file\s*>\s*([\d.]+)x\s*live", "snapshot file alarm factor", unit_scale=False)
    txn = limit(cells, 3, r"transaction\s*>\s*([\d.]+)\s*ms", "snapshot writer txn", unit_scale=False)
    reader = limit(cells, 3, r"reader call\s*>\s*([\d.]+)\s*ms", "snapshot reader call", unit_scale=False)
    name = "market_snapshot_current"
    return [
        _result(name, "max_record_bytes", m["max_record_bytes"], record, "<=", "sec6 alarm"),
        _result(name, f"live_bytes_{int(m['keys'])}_keys", m["live_bytes"], live, "<=", "sec6 total bound"),
        _result(name, "file_over_live_ratio", m["file_bytes"] / m["live_bytes"], file_alarm, "<=", "sec6 alarm"),
        _result(name, "wal_bytes", m["wal_bytes"], wal, "<=", "sec6 total bound"),
        _result(name, "writer_txn_ms_max", m["writer_txn_ms_max"], txn, "<=", "sec6 alarm"),
        _result(name, "reader_call_ms_max", m["reader_call_ms_max"], reader, "<=", "sec6 alarm"),
    ]


def check_listing(cells: list[str], m: dict[str, float]) -> list[dict[str, Any]]:
    day = limit(cells, 1, _le(), "listing day")
    total = limit(cells, 2, _le(), "listing total")
    return [
        _result("listing_allowlist", "day_bytes", m["day_bytes"], day, "<=", "sec6 size per day"),
        _result("listing_allowlist", "total_bytes", m["day_bytes"], total, "<=", "sec6 total bound (replaced daily)"),
    ]


def check_dataset(name: str) -> Callable[[list[str], dict[str, float]], list[dict[str, Any]]]:
    def run(cells: list[str], m: dict[str, float]) -> list[dict[str, Any]]:
        day = limit(cells, 4, r"<=\s*" + _QTY, f"{name} growth budget")
        keyed = bool(m.get("keyed"))
        out = [_result(name, "day_bytes", m["day_bytes"], day, "<=",
                       "sec6A growth budget (KeyedDatasetStore bytes)" if keyed else "sec6A growth budget (zlib JSON proxy)")]
        if keyed:  # the declaration's own enforced limit must not be looser than the design row
            out.append(_result(name, "declared_budget_bytes", m["declared_budget_bytes"], day, "<=",
                               "sec6A growth budget (declaration vs design row)"))
        return out
    return run


CHECKERS: dict[str, tuple[Callable[[], dict[str, float]], Callable]] = {
    "daily_bars": (measure_daily_bars, check_daily_bars),
    "market_snapshot_current": (measure_snapshot_sqlite, check_snapshot),
    "listing_allowlist": (measure_listing, check_listing),
}
# daily_bars measurement also feeds the lineage row (one write, two rows).
SHARED_MEASURE = {"lineage": "daily_bars"}
LINEAGE = ("lineage", check_lineage)
for _name in DATASET_ROWS:
    CHECKERS[_name] = ((lambda n=_name: measure_dataset(n)), check_dataset(_name))

WAIVERS: dict[str, str] = {
    "compat journal export (phase S2 only)": "no exporter exists before phase S2 (T3b); nothing to measure",
    "ingest_schedule.jsonl": "receipt writer has no hosted-shaped generator here; its row is owned by the T2 receipts task",
    "raw/normalized run files (transition only)": "legacy files, removed by T4; time based alarm, not a byte bound",
    "tw_price_1m": "owned by the 2026-11-11 IntradayStore design and its own tests",
    "source_evidence, distillation": "no per-close evidence store exists to measure; owned by the M4 baseline task",
    "in-memory receipt replay": "RSS bound from measurement M6; not a persisted store",
    "other datasets": "pointer row to section 6A, every 6A row is checked individually",
    "features/returns": "limit is 0 (not stored); removal is task T4, there is no writer to measure after it",
}


def evaluate(rows: dict[str, dict[str, Any]], *, only: set[str] | None = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    known = set(CHECKERS) | set(WAIVERS) | {"lineage"}
    for key in sorted(known - set(rows)):
        results.append(_result(key, "design_row_present", 1, 0, "==", "design tables", "fail",
                               f"{key}: registered in the checker but the design table has no such row"))
    cache: dict[str, dict[str, float]] = {}

    def measured(name: str) -> dict[str, float]:
        if name not in cache:
            cache[name] = CHECKERS[name][0]()
        return cache[name]

    for key, row in rows.items():
        if only and key not in only:
            continue
        cells = row["cells"]
        try:
            if key in WAIVERS:
                results.append(_result(key, "waived", 0, 0, "==", f"sec{row['section']}", "waived", WAIVERS[key]))
            elif key == "lineage":
                results.extend(LINEAGE[1](cells, measured("daily_bars")))
            elif key in CHECKERS:
                results.extend(CHECKERS[key][1](cells, measured(key)))
            else:
                results.append(_result(key, "has_checker_or_waiver", 0, 0, "==", f"sec{row['section']}", "fail",
                                       f"{key}: design row has neither a checker nor a waiver; add one before growing a store"))
        except BudgetError as exc:
            results.append(_result(key, "check", 0, 0, "==", f"sec{row['section']}", "fail", str(exc)))
    return results


def check_live(rows: dict[str, dict[str, Any]], live: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    """Compare a live stats read to the same table limits (the limits are the generated checks' limits)."""
    results = []
    probes: dict[str, Callable[[list[str], dict[str, float]], list[dict[str, Any]]]] = {
        "daily_bars": check_daily_bars, "lineage": check_lineage, "listing_allowlist": check_listing,
    }
    for name in DATASET_ROWS:
        probes[name] = check_dataset(name)
    for store, stats in live.items():
        row = rows.get(store)
        if row is None:
            results.append(_result(store, "live_row_present", 1, 0, "==", "live stats", "fail",
                                   f"{store}: live stats given for a store with no design table row"))
            continue
        try:
            if store == "market_snapshot_current":
                missing = {"max_record_bytes", "live_bytes", "file_bytes", "wal_bytes"} - set(stats)
                if missing:
                    raise BudgetError(f"live stats for {store} lack {sorted(missing)}")
                full = {"keys": stats.get("keys", 0), "writer_txn_ms_max": 0.0, "reader_call_ms_max": 0.0, **stats}
                results.extend(check_snapshot(row["cells"], full))
            elif store in probes:
                results.extend(probes[store](row["cells"], stats))
            else:
                raise BudgetError(f"{store}: no live probe defined")
        except (BudgetError, KeyError) as exc:
            results.append(_result(store, "live_check", 0, 0, "==", "live stats", "fail", f"live {store}: {exc}"))
        for r in results:
            if r["store"] == store and not r["source"].startswith("live"):
                r["source"] = "live stats; " + r["source"]
    return results


def build_report(only: set[str] | None = None, live: dict[str, dict[str, float]] | None = None,
                 design_path: Path = DESIGN_PATH) -> dict[str, Any]:
    t0 = time.perf_counter()
    rows = load_design_rows(design_path)
    results = evaluate(rows, only=only)
    if live:
        results.extend(check_live(rows, live))
    failed = [r for r in results if r["status"] == "fail"]
    return {
        "schema_version": SCHEMA_VERSION, "design": str(design_path.relative_to(REPO_ROOT)) if design_path.is_relative_to(REPO_ROOT) else str(design_path),
        "volume": {"listed_symbols": LISTED_SYMBOLS, "listed_warrants": LISTED_WARRANTS, "daily_bars_symbols": DAILY_BARS_SYMBOLS, "trading_days": TRADING_DAYS,
                   "batch": BATCH, "snapshot_closes": SNAPSHOT_CLOSES},
        "rows_in_design": len(rows), "checks": len(results), "passed": sum(r["status"] == "pass" for r in results),
        "waived": sum(r["status"] == "waived" for r in results), "failed": len(failed),
        "elapsed_seconds": round(time.perf_counter() - t0, 1), "results": results,
        "failures": [r["reason"] for r in failed], "ok": not failed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--design", type=Path, default=DESIGN_PATH)
    parser.add_argument("--live-stats", type=Path, help="JSON {store: {metric: value}} from a live read")
    parser.add_argument("--only", action="append", help="restrict to a design row key (repeatable)")
    parser.add_argument("--output", type=Path, help="write the JSON report here")
    args = parser.parse_args(argv)
    live = None
    try:
        if args.live_stats:
            live = json.loads(args.live_stats.read_text(encoding="utf-8"))
            if not isinstance(live, dict):
                raise BudgetError("--live-stats must hold a JSON object")
        report = build_report(set(args.only) if args.only else None, live, args.design)
    except (BudgetError, OSError, ValueError) as exc:
        report = {"schema_version": SCHEMA_VERSION, "ok": False, "failures": [f"budget check could not run: {exc}"],
                  "results": []}
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    for reason in report["failures"]:
        print(f"BUDGET FAIL: {reason}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
