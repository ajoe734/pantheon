#!/usr/bin/env python3
"""One-off deletion of already expired warrants (SOURCE-WARRANT-LISTING-EXPIRY-20261117, design 4.3).

Dry run is the default and writes a report with a digest. The real run needs ``--execute`` AND
``--confirm-digest <digest of the report>``; the plan is recomputed and must hash to the same digest.

Stores handled by this script (per store counts in the report):
  raw/**/*.jsonl[.gz], normalized/**/*.jsonl  rows are rewritten without the delete-set symbols.
  daily_bars (parquet, written once)          counted read-only here; its deletion is the retention
                                              sweeper's job (SOURCE-RETENTION-SWEEPER-20261118).

Classification of a symbol found in a store:
  listed now                                   keep
  in ordinary_share_registry                   keep (delisted ordinary shares keep their daily bars)
  looks like an ordinary share / ETF           review list, NEVER automatic (four digits, 00xxx[A])
  listed warrant that expired / in no listing  delete set
Fail closed: if the listing is not fresh nothing is in the delete set.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.source_ingestion.security_listing import ListingState, bare_symbol  # noqa: E402

SHARE_SHAPED_RE = re.compile(r"^(\d{4}|00\d{2,4}[A-Z]?)$")
JSONL_STORES = ("raw", "normalized")


def _open_text(path: Path, mode: str):
    return gzip.open(path, mode + "t", encoding="utf-8") if path.suffix == ".gz" else path.open(mode, encoding="utf-8")


def _row_symbol(row: Mapping[str, Any]) -> str | None:
    meta = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    nrow = meta.get("normalized_row") if isinstance(meta.get("normalized_row"), Mapping) else {}
    for src in (meta, nrow, row):
        for key in ("symbol_canonical", "symbol", "entity_id"):
            if src.get(key):
                return bare_symbol(src[key])
    return None


def _store_files(root: Path) -> Iterable[tuple[str, Path]]:
    for store in JSONL_STORES:
        base = root / store
        if base.is_dir():
            for p in sorted(base.rglob("*.jsonl*")):
                if p.suffix in (".jsonl", ".gz"):
                    yield store, p


def classify(symbol: str, state: ListingState) -> str:
    if state.is_listed(symbol):
        return "keep_listed"
    if symbol in state.ordinary_share_registry:
        return "keep_delisted_share"
    if SHARE_SHAPED_RE.match(symbol):
        return "review"
    return "delete"


def build_report(root: Path, state: ListingState, *, today: str, pinned: Iterable[str] = ()) -> dict[str, Any]:
    pinned_set = {bare_symbol(s) for s in pinned}
    status = state.status(today)
    report: dict[str, Any] = {
        "schema_version": "delete_expired_warrants_report.v1",
        "listing_status": status,
        "listing_date": state.last_success_date,
        "stores": {},
        "delete_symbols": [],
        "review_symbols": [],
    }
    if status != "fresh":
        report["refused"] = f"listing not fresh ({status}): nothing is treated as expired and nothing is deleted"
        report["digest"] = _digest(report)
        return report
    per_store: dict[str, dict[str, Any]] = {}
    delete: set[str] = set()
    review: set[str] = set()
    for store, path in _store_files(root):
        counts = per_store.setdefault(store, {"files": 0, "rows": 0, "delete_rows": 0, "review_rows": 0})
        counts["files"] += 1
        with _open_text(path, "r") as fh:
            for line in fh:
                if not line.strip():
                    continue
                counts["rows"] += 1
                sym = _row_symbol(json.loads(line))
                if sym is None or sym in pinned_set:
                    continue
                kind = classify(sym, state)
                if kind == "delete":
                    counts["delete_rows"] += 1
                    delete.add(sym)
                elif kind == "review":
                    counts["review_rows"] += 1
                    review.add(sym)
    report["stores"] = per_store
    report["stores"]["daily_bars"] = {"note": "report only here; deleted by the retention sweeper"}
    report["delete_symbols"] = sorted(delete)
    report["review_symbols"] = sorted(review)
    report["digest"] = _digest(report)
    return report


def _digest(report: Mapping[str, Any]) -> str:
    body = {k: v for k, v in report.items() if k != "digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def execute(root: Path, report: Mapping[str, Any]) -> dict[str, int]:
    delete = set(report["delete_symbols"])
    removed = {"rows": 0, "files": 0}
    if not delete:
        return removed
    for _store, path in _store_files(root):
        kept: list[str] = []
        dropped = 0
        with _open_text(path, "r") as fh:
            for line in fh:
                if not line.strip():
                    continue
                if _row_symbol(json.loads(line)) in delete:
                    dropped += 1
                else:
                    kept.append(line if line.endswith("\n") else line + "\n")
        if not dropped:
            continue
        removed["rows"] += dropped
        if not kept:
            path.unlink()
            removed["files"] += 1
            continue
        tmp = path.with_name("rewrite-" + path.name)  # keeps the .gz/.jsonl suffix
        with _open_text(tmp, "w") as out:
            out.writelines(kept)
        os.replace(tmp, path)
    return removed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="market data store root")
    ap.add_argument("--listing-state", required=True)
    ap.add_argument("--today", required=True)
    ap.add_argument("--pinned", default="", help="comma separated pinned symbols")
    ap.add_argument("--report", required=True, help="report json path (written first, always)")
    ap.add_argument("--execute", action="store_true", help="real run; needs --confirm-digest")
    ap.add_argument("--confirm-digest")
    a = ap.parse_args(argv)
    state = ListingState.load(a.listing_state)
    root = Path(a.root)
    report = build_report(root, state, today=a.today, pinned=[s for s in a.pinned.split(",") if s])
    Path(a.report).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"report={a.report} digest={report['digest']} delete_symbols={len(report['delete_symbols'])} "
          f"review_symbols={len(report['review_symbols'])}")
    if not a.execute:
        print("dry run: nothing deleted")
        return 0
    if report.get("refused"):
        print(report["refused"], file=sys.stderr)
        return 2
    if a.confirm_digest != report["digest"]:
        print("refused: --confirm-digest does not match the recomputed report digest", file=sys.stderr)
        return 2
    print(json.dumps({"deleted": execute(root, report)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
