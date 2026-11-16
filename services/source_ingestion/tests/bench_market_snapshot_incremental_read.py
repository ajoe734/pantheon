"""Manual measurement (pytest does not collect bench_*.py): time per append with a second writer interleaving.

    python -m services.source_ingestion.tests.bench_market_snapshot_incremental_read OUT.json [--mb 320] [--legacy]

Builds a hosted-shaped journal (one full 60-close state line per update) of at least --mb MB with
compaction disabled, then times appends by writer A while writer B appends in between, at several
journal sizes.  ``--legacy`` forces the previous behaviour (drop incremental position => full parse).
"""
from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

from services.source_ingestion import requirement_state as m
from services.source_ingestion.connectors.base import SourceRecord


def _rec(symbol: str, day: int, close: float) -> SourceRecord:
    return SourceRecord(
        source_id=f"s-{symbol}-{day}", connector_id="finmind-tw-daily", source_type="market", title="t",
        content_ref=f"ref-{symbol}-{day}", created_at="2026-11-12T08:00:00Z",
        metadata={"symbol": symbol, "event_time": f"2026-{1 + day // 28:02d}-{1 + day % 28:02d}T05:30:00Z",
                  "close": close, "market": "TW"},
    )


def _line(symbol: str, version: int) -> str:
    """One real state line: a full 60-close snapshot exactly as the store serialises it."""
    points = tuple(
        m.MarketSnapshotPoint(
            event_time=f"2026-{1 + d // 28:02d}-{1 + d % 28:02d}T05:30:00Z", close=100.0 + d + version,
            source_id=f"s-{symbol}-{d}", connector_id="finmind-tw-daily", content_ref=f"ref-{symbol}-{d}",
            ingest_run_id=f"run-{version}", market="TW")
        for d in range(60)
    )
    snap = m.LatestMarketSnapshot(symbol=symbol, points=points, observed_at="2026-11-12T08:00:00Z", market="TW")
    return m.LatestMarketSnapshotStore._envelope_line(snap.to_dict())


def _grow(path: Path, target_bytes: int, symbols: int = 1800) -> None:
    """Append real full-state lines (compaction off, as before #7414) until the journal reaches target_bytes."""
    path.touch()
    version = path.stat().st_size // 1000  # distinct lines on every call
    with path.open("a", encoding="utf-8") as handle:
        while path.stat().st_size < target_bytes:
            for s in range(symbols):
                handle.write(_line(f"{1000 + s}.TWSE", version) + "\n")
            handle.flush()
            version += 1


def _measure(path: Path, legacy: bool, rounds: int = 12) -> dict:
    a = m.LatestMarketSnapshotStore(path, compact_min_bytes=10**15)
    b = m.LatestMarketSnapshotStore(path, compact_min_bytes=10**15)
    times = []
    for i in range(rounds):
        b.append_normalized_records([_rec("1001.TWSE", 100 + i, 1.0)], ingest_run_id="b", observed_at="2026-11-13T00:00:00Z")
        if legacy:
            a._invalidate()
        t = time.perf_counter()
        a.append_normalized_records([_rec("1002.TWSE", 100 + i, 2.0)], ingest_run_id="a", observed_at="2026-11-13T00:00:00Z")
        times.append(time.perf_counter() - t)
    return {"size_mb": round(path.stat().st_size / 2**20, 1), "median_s": statistics.median(times), "max_s": max(times)}


def main() -> None:
    out = Path(sys.argv[1])
    mb = int(sys.argv[sys.argv.index("--mb") + 1]) if "--mb" in sys.argv else 320
    results = {"incremental": [], "legacy_full_parse": []}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "latest_market_snapshots.jsonl"
        for target in (mb // 8, mb // 2, mb):
            _grow(path, target * 2**20)
            results["incremental"].append(_measure(path, legacy=False))
            results["legacy_full_parse"].append(_measure(path, legacy=True, rounds=3))
            print(json.dumps({"size_mb": results["incremental"][-1]["size_mb"],
                              "after": results["incremental"][-1]["median_s"],
                              "before": results["legacy_full_parse"][-1]["median_s"]}), flush=True)
    out.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
