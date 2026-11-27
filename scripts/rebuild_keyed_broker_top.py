#!/usr/bin/env python3
"""Repair and rebuild ``keyed/tw_broker_top`` from ``normalized/tw_broker_top``.

SOURCE-BROKER-TOP-KEYED-SIDE-20261127 (RC3 WP3.1). Until this task ``KEYED_DATASETS["tw_broker_top"]``
declared ``("symbol", "trade_date", "rank")`` without ``"side"``, so the sell rows overwrote the buy rows
of the same rank and hosted ``keyed/tw_broker_top`` (2026-11-16..2026-11-26) held only 20 ``side=sell``
rows per symbol while ``normalized/tw_broker_top`` held all 20 buy + 20 sell rows per symbol.

* writes ``keyed/tw_broker_top`` only, through ``KeyedDatasetStore``; never modifies ``normalized/tw_broker_top``;
* deduplicates normalized run files oldest-to-newest by ``(symbol, trade_date, side, rank)``;
* dry-run by default (report only); ``--apply`` writes and verifies each partition manifest;
* idempotent: a second ``--apply`` run when ``content_digest`` and ``key_fields`` already match writes 0 files;
* ``repair_keyed_broker_top_if_needed`` runs automatically through the existing deploy startup and refresh
  keyed-store paths whenever a keyed ``tw_broker_top`` partition has stale ``key_fields`` or lacks a side
  present in ``normalized/tw_broker_top``.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import threading
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.source_ingestion.daily_bars_store import (  # noqa: E402
    KEYED_DATASETS,
    KeyedDatasetError,
    KeyedDatasetStore,
    keyed_dataset_dir,
)

REPORT_SCHEMA = "rebuild_keyed_broker_top_report.v1"
DATASET = "tw_broker_top"
_REPAIR_LOCK = threading.Lock()


def _open(path: Path):
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open("r", encoding="utf-8")


def run_files(root: Path) -> list[Path]:
    """Normalized ``tw_broker_top`` run files, oldest first: (partition write date, mtime, name)."""
    base = root / "normalized" / DATASET
    if not base.is_dir():
        return []
    files = [p for p in base.glob("date=*/*.jsonl*") if p.suffix in (".jsonl", ".gz")]
    return sorted(files, key=lambda p: (p.parent.name, p.stat().st_mtime_ns, p.name))


def _iter_rows(path: Path) -> Iterator[Mapping[str, Any]]:
    with _open(path) as handle:
        for line in handle:
            line = line.strip()
            if line:
                row = json.loads(line)
                if isinstance(row, Mapping):
                    yield row


def compact_broker_top_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one ``normalized/tw_broker_top`` line to the compact keyed record shape."""
    meta = dict(row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {})
    body = meta.get("body")
    if isinstance(body, str) and body.startswith("{"):
        if any(meta.get(k) in (None, "") for k in ("symbol", "trade_date", "date", "side", "rank")):
            try:
                parsed = json.loads(body)
            except ValueError:
                parsed = None
            if isinstance(parsed, Mapping):
                for key in ("symbol", "date", "trade_date", "side", "rank", "broker", "broker_id",
                            "buy_qty", "sell_qty", "net_qty", "source", "available_time", "source_url"):
                    if meta.get(key) in (None, "") and parsed.get(key) not in (None, ""):
                        meta[key] = parsed[key]
    nrow = meta.get("normalized_row")
    if isinstance(nrow, Mapping):
        meta = {k: v for k, v in meta.items() if k == "normalized_row" or k not in nrow or nrow[k] != v}
    return {
        "source_id": str(row.get("source_id") or ""),
        "connector_id": str(row.get("connector_id") or ""),
        "dataset": str(row.get("dataset") or DATASET),
        "metadata": meta,
    }


def _row_symbol_and_side(record: Mapping[str, Any]) -> tuple[str, str]:
    symbol = ""
    side = ""
    for src in KeyedDatasetStore._sources(record):
        if not symbol:
            for alias in ("symbol_canonical", "symbol", "stock_id", "code"):
                if src.get(alias) not in (None, ""):
                    symbol = str(src[alias]).strip()
                    break
        if not side:
            for alias in ("side", "direction"):
                if src.get(alias) not in (None, ""):
                    side = str(src[alias]).strip().lower()
                    break
        if symbol and side:
            break
    return symbol, side


def _summarize_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    buy = sell = other = 0
    per_symbol: dict[str, dict[str, int]] = {}
    for rec in records:
        symbol, side = _row_symbol_and_side(rec)
        bucket = per_symbol.setdefault(symbol or "unknown", {"buy": 0, "sell": 0, "total": 0})
        bucket["total"] += 1
        if side == "buy":
            buy += 1
            bucket["buy"] += 1
        elif side == "sell":
            sell += 1
            bucket["sell"] += 1
        else:
            other += 1
    return {
        "row_count": len(records),
        "buy_rows": buy,
        "sell_rows": sell,
        "other_rows": other,
        "symbol_count": len(per_symbol),
        "per_symbol": dict(sorted(per_symbol.items())),
    }


def _partition_sides_fast(base: Path, manifest: Mapping[str, Any]) -> set[str]:
    sides: set[str] = set()
    for entry in manifest.get("files", ()):
        path = base / str(entry.get("path") or "")
        if not path.is_file():
            return set()
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    item = json.loads(line)
                    _, side = _row_symbol_and_side(item)
                    if side in ("buy", "sell"):
                        sides.add(side)
                        if len(sides) == 2:
                            return sides
        except (OSError, ValueError):
            return set()
    return sides


def _normalized_partition_sides_fast(norm_part_dir: Path) -> set[str]:
    sides: set[str] = set()
    if not norm_part_dir.is_dir():
        return sides
    for path in sorted(norm_part_dir.glob("*.jsonl*")):
        if path.suffix not in (".jsonl", ".gz"):
            continue
        try:
            for row in _iter_rows(path):
                _, side = _row_symbol_and_side(compact_broker_top_row(row))
                if side in ("buy", "sell"):
                    sides.add(side)
                    if len(sides) == 2:
                        return sides
        except (OSError, ValueError):
            continue
    return sides


def keyed_broker_top_needs_repair(root: str | Path) -> bool:
    """Return True when keyed ``tw_broker_top`` has stale ``key_fields`` or missing sides vs normalized."""
    root_path = Path(root)
    norm_base = root_path / "normalized" / DATASET
    if not norm_base.is_dir():
        return False
    base = keyed_dataset_dir(root_path, DATASET)
    manifest_dir = base / "_manifest"
    if not manifest_dir.is_dir():
        return False
    expected_keys = list(KEYED_DATASETS[DATASET].key_fields)
    manifests = sorted(manifest_dir.glob("date=*.json"))
    if not manifests:
        return False
    for manifest_path in manifests:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return True
        if manifest.get("key_fields") != expected_keys:
            return True
        partition = manifest_path.name[5:-5]
        keyed_sides = _partition_sides_fast(base, manifest)
        if len(keyed_sides) < 2:
            norm_sides = _normalized_partition_sides_fast(norm_base / f"date={partition}")
            if not norm_sides.issubset(keyed_sides):
                return True
    return False


def rebuild_keyed_broker_top(
    root: str | Path,
    *,
    apply: bool = False,
    only_dates: Sequence[str] | None = None,
    since_date: str | None = None,
    store: KeyedDatasetStore | None = None,
) -> dict[str, Any]:
    """Rebuild ``keyed/tw_broker_top`` partitions from ``normalized/tw_broker_top``."""
    root_path = Path(root)
    decl = KEYED_DATASETS[DATASET]
    store = store or KeyedDatasetStore(root_path, decl)
    files = run_files(root_path)
    by_partition: dict[str, dict[tuple[str, ...], dict[str, Any]]] = {}
    totals = {
        "files": len(files),
        "rows_read": 0,
        "rejected_no_key": 0,
        "unique_keys": 0,
        "duplicate_rows": 0,
    }
    for path in files:
        for raw_row in _iter_rows(path):
            totals["rows_read"] += 1
            compacted = compact_broker_top_row(raw_row)
            keyed = store.key_of(compacted)
            if keyed is None:
                totals["rejected_no_key"] += 1
                continue
            key, partition = keyed
            part_map = by_partition.setdefault(partition, {})
            if key in part_map:
                totals["duplicate_rows"] += 1
            part_map[key] = compacted
    totals["unique_keys"] = sum(len(v) for v in by_partition.values())

    wanted = set(only_dates) if only_dates else None
    partitions_report: dict[str, Any] = {}
    partitions_written: list[str] = []
    partitions_unchanged: list[str] = []
    mismatches: list[str] = []

    for partition in sorted(by_partition):
        if wanted is not None and partition not in wanted:
            continue
        if since_date is not None and partition < since_date:
            continue
        before_manifest = store.read_manifest(partition)
        try:
            before_rows = store.records(partition) if before_manifest else []
        except KeyedDatasetError:
            before_rows = []
        before_summary = _summarize_records(before_rows)
        before_summary["key_fields"] = before_manifest.get("key_fields") if before_manifest else None
        before_summary["manifest_rows"] = before_manifest.get("row_count") if before_manifest else 0
        before_summary["manifest_bytes"] = before_manifest.get("bytes") if before_manifest else 0
        before_summary["generation"] = before_manifest.get("generation") if before_manifest else 0

        incoming_records = [by_partition[partition][k] for k in sorted(by_partition[partition])]
        norm_summary = _summarize_records(incoming_records)

        entry: dict[str, Any] = {
            "before": before_summary,
            "normalized": norm_summary,
            "needs_repair": (
                before_summary["key_fields"] != list(decl.key_fields)
                or before_summary["buy_rows"] < norm_summary["buy_rows"]
                or before_summary["sell_rows"] < norm_summary["sell_rows"]
            ),
        }

        if apply:
            write_res = store.write_records(incoming_records, ingest_run_id=f"rebuild-{DATASET}-{partition}")
            written = partition in write_res["partitions_written"]
            (partitions_written if written else partitions_unchanged).append(partition)
            after_manifest = store.read_manifest(partition) or {}
            verify = store.verify_partition(partition)
            after_rows = store.records(partition)
            after_summary = _summarize_records(after_rows)
            after_summary.update({
                "key_fields": after_manifest.get("key_fields"),
                "manifest_rows": after_manifest.get("row_count"),
                "manifest_bytes": after_manifest.get("bytes"),
                "generation": after_manifest.get("generation"),
                "content_digest": after_manifest.get("content_digest"),
                "verified": bool(verify["verified"]),
                "written": written,
            })
            entry["after"] = after_summary
            ok = (
                bool(verify["verified"])
                and after_summary["key_fields"] == list(decl.key_fields)
                and after_summary["buy_rows"] >= norm_summary["buy_rows"]
                and after_summary["sell_rows"] >= norm_summary["sell_rows"]
                and after_summary["row_count"] >= norm_summary["row_count"]
            )
            if not ok:
                mismatches.append(partition)
        partitions_report[partition] = entry

    if apply and mismatches:
        raise KeyedDatasetError(f"rebuilt keyed {DATASET} failed verification on: {', '.join(mismatches)}")

    return {
        "schema_version": REPORT_SCHEMA,
        "dataset": DATASET,
        "key_fields": list(decl.key_fields),
        "root": root_path.as_posix(),
        "apply": apply,
        "source": totals,
        "partition_count": len(partitions_report),
        "partitions_written": partitions_written,
        "partitions_unchanged": partitions_unchanged,
        "mismatches": mismatches,
        "partitions": partitions_report,
        "budget": store.budget_report(),
    }


def repair_keyed_broker_top_if_needed(root: str | Path) -> dict[str, Any]:
    """Idempotent deploy/refresh hook: rebuilds ``keyed/tw_broker_top`` only when a partition has stale
    ``key_fields`` or is missing a side present in ``normalized/tw_broker_top``."""
    root_path = Path(root)
    with _REPAIR_LOCK:
        if not keyed_broker_top_needs_repair(root_path):
            return {
                "schema_version": REPORT_SCHEMA,
                "dataset": DATASET,
                "root": root_path.as_posix(),
                "repaired": False,
            }
        report = rebuild_keyed_broker_top(root_path, apply=True)
        return {"repaired": True, **report}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, help="market_data_store root")
    ap.add_argument("--apply", action="store_true", help="write keyed tw_broker_top partitions (default: dry run)")
    ap.add_argument("--if-needed", action="store_true", help="apply only when keyed_broker_top_needs_repair is true")
    ap.add_argument("--report", help="write the JSON report to this file")
    ap.add_argument("--date", action="append", help="limit to this trade date (repeatable)")
    ap.add_argument("--since", help="limit to trade dates >= this ISO date")
    args = ap.parse_args(argv)
    try:
        if args.if_needed:
            report = repair_keyed_broker_top_if_needed(args.root)
        else:
            report = rebuild_keyed_broker_top(
                args.root,
                apply=args.apply,
                only_dates=args.date,
                since_date=args.since,
            )
    except KeyedDatasetError as exc:
        print(f"rebuild_keyed_broker_top stopped: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
