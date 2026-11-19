"""Off-hosted reproduction and timing driver for SOURCE-INGEST-MALLOC-ARENA-LIMIT-20261119.

Runs the thread pools source-ingest uses:
1. Collector bundle raw read windows of 16 workers (`_verify_raw_window` with
   `HISTORY_RAW_READ_CONCURRENCY = 16`, creating a 16-worker ThreadPoolExecutor per
   window and reading bounded 64 KB chunks via `read_bounded_response`).
2. The request thread pool (`ThreadPoolExecutor(max_workers=14)`), serving concurrent
   stored snapshot lookups (`SnapshotUniverse.snapshot_for_request` + `to_public_dict`
   + JSON serialization) alongside collector raw read windows.

Records `/proc/self/smaps` memory breakdown (total RSS, main heap `[heap]`,
64 MB-aligned `rw-p` glibc thread arenas, mappings above 64 MB, and small mappings)
and wall-time median and p99 for both raw read windows and stored snapshot lookups
over at least 200 rounds.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import io
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.source_ingestion.connectors.bounded_reader import read_bounded_response
from services.source_ingestion.connectors.collector_bundle import (
    HISTORY_RAW_READ_CONCURRENCY,
    BundleTransport,
    VerifiedBundle,
    _verify_raw_window,
)
from services.source_ingestion.requirement_state import (
    LatestMarketSnapshot,
    LatestMarketSnapshotStore,
    MarketSnapshotPoint,
)
from services.source_ingestion.snapshot_universe import SnapshotUniverse

ALIGN_64MB = 64 * 1024 * 1024
HEAP_MAX_SIZE_KB = 65536
SMAPS_HEADER_RE = re.compile(r"^([0-9a-f]+)-([0-9a-f]+)\s+(\S+)\s+\S+\s+\S+\s+\S+\s*(.*)$")


def _trading_days(count: int) -> list[date]:
    days, day = [], date(2026, 8, 3)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


_SNAPSHOT_DAYS = _trading_days(60)


def _snapshot(index: int) -> LatestMarketSnapshot:
    symbol = f"{index:05d}.TWSE"
    points = tuple(
        MarketSnapshotPoint(
            event_time=f"{day.isoformat()}T05:30:00Z",
            close=round(20.0 + index * 0.01 + n * 0.05, 2),
            source_id="twse-openapi-stock-day-all",
            connector_id="tw-twse-price-history",
            content_ref=(
                f"twse://openapi/STOCK_DAY_ALL/{symbol}/{day.isoformat()}/"
                f"{hashlib.sha256(f'{symbol}|{day}'.encode()).hexdigest()}"
            ),
            ingest_run_id=f"ingest-run-{day.isoformat()}-0f3a9c21b7d44e56",
            market="TW",
        )
        for n, day in enumerate(_SNAPSHOT_DAYS)
    )
    return LatestMarketSnapshot(
        symbol=symbol,
        points=points,
        observed_at=f"{_SNAPSHOT_DAYS[-1].isoformat()}T10:00:00Z",
        calendar_evidence={"calendar_id": "twse-2026", "status": "open", "trade_date": _SNAPSHOT_DAYS[-1].isoformat()},
    )


def parse_smaps_text(smaps_text: str) -> dict[str, Any]:
    """Classify `/proc/<pid>/smaps` mappings into main heap, 64 MB-aligned glibc
    thread arenas, mappings above 64 MB, and small mappings.
    """
    total_rss_kb = main_heap_rss_kb = arena_count = arena_rss_kb = above_64mb_rss_kb = small_rss_kb = 0
    header: tuple[str, str, str, str] | None = None
    size_kb = rss_kb = 0

    def flush() -> None:
        nonlocal total_rss_kb, main_heap_rss_kb, arena_count, arena_rss_kb, above_64mb_rss_kb, small_rss_kb
        if header is None:
            return
        start_hex, _end_hex, perms, name = header
        start = int(start_hex, 16)
        total_rss_kb += rss_kb
        if name == "[heap]":
            main_heap_rss_kb += rss_kb
        elif perms == "rw-p" and not name and (start % ALIGN_64MB == 0) and size_kb <= HEAP_MAX_SIZE_KB:
            arena_count += 1
            arena_rss_kb += rss_kb
        elif size_kb > HEAP_MAX_SIZE_KB:
            above_64mb_rss_kb += rss_kb
        else:
            small_rss_kb += rss_kb

    for line in smaps_text.splitlines():
        match = SMAPS_HEADER_RE.match(line)
        if match:
            flush()
            header = (match.group(1), match.group(2), match.group(3), match.group(4).strip())
            size_kb = rss_kb = 0
        elif line.startswith("Size:"):
            size_kb = int(line.split()[1])
        elif line.startswith("Rss:"):
            rss_kb = int(line.split()[1])
    flush()
    return {
        "total_rss_mb": round(total_rss_kb / 1024.0, 2),
        "main_heap_rss_mb": round(main_heap_rss_kb / 1024.0, 2),
        "aligned_64mb_arenas": arena_count,
        "aligned_64mb_arena_rss_mb": round(arena_rss_kb / 1024.0, 2),
        "above_64mb_rss_mb": round(above_64mb_rss_kb / 1024.0, 2),
        "small_mappings_rss_mb": round(small_rss_kb / 1024.0, 2),
    }


def read_proc_self_smaps() -> dict[str, Any]:
    snapshot = parse_smaps_text(Path("/proc/self/smaps").read_text(encoding="utf-8"))
    tids = sorted(int(p.name) for p in Path("/proc/self/task").iterdir() if p.name.isdigit())
    snapshot["live_threads"] = len(tids)
    snapshot["max_tid"] = tids[-1] if tids else 0
    return snapshot


class BoundedStreamBundleTransport(BundleTransport):
    """In-memory collector transport exercising `read_bounded_response` (64 KB chunks)
    inside `_verify_raw_window` worker threads.
    """

    def __init__(self, payloads: dict[str, bytes], ring_buffer: list[bytes]) -> None:
        self._payloads = payloads
        self._ring_buffer = ring_buffer
        self._lock = threading.Lock()

    def read(self, relative_path: str, *, max_bytes: int) -> bytes:
        raw = self._payloads[relative_path]
        data = read_bounded_response(io.BytesIO(raw), max_bytes=max_bytes, chunk_size=65536)
        receipt = f"{relative_path}:{len(data)}:".encode("utf-8") + (b"r" * 1536)
        with self._lock:
            self._ring_buffer.append(receipt)
            if len(self._ring_buffer) > 256:
                del self._ring_buffer[:16]
        return data


def _percentile_99(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(0.99 * (len(ordered) - 1)))))]


def run_driver(
    *,
    rounds: int = 200,
    request_pool_workers: int = 14,
    concurrent_windows_per_round: int = 1,
    lookups_per_round: int = 4,
) -> dict[str, Any]:
    if rounds < 200:
        raise ValueError(f"rounds must be at least 200 (got {rounds})")

    with tempfile.TemporaryDirectory(prefix="source-ingest-arena-driver-") as tmp:
        root = Path(tmp)
        journal = root / "latest_market_snapshots.jsonl"
        symbols: list[str] = []
        with journal.open("w", encoding="utf-8") as handle:
            for idx in range(64):
                snap = _snapshot(idx)
                symbols.append(snap.symbol)
                handle.write(LatestMarketSnapshotStore._envelope_line(snap.to_dict()) + "\n")

        prev_active = os.environ.get("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS")
        os.environ["SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS"] = ",".join(symbols)
        try:
            store = LatestMarketSnapshotStore(journal, store_mode="journal", compact_min_bytes=10**12)
            store.reload()
            universe = SnapshotUniverse(store, state_path=root / "universe.json", storage_root=root / "bars")
            universe.initialize()

            source_id, date_str, run_id = "collector-tw-tpex-price-history", "2026-11-18", "backfill-2026-11-18"
            base_prefix = f"bundles/{source_id}/{date_str}/{run_id}"
            payloads: dict[str, bytes] = {}
            items: list[dict[str, Any]] = []
            manifest_items: dict[str, dict[str, Any]] = {}

            for i in range(HISTORY_RAW_READ_CONCURRENCY):
                item_id = f"{source_id}-{date_str}-{1000 + i}"
                rows = [
                    {"symbol": f"{2300 + j}.TW", "close": 100.0 + j, "volume": 100000 + j * 10, "note": "market-row-" + ("m" * 180)}
                    for j in range(1200)
                ]
                body = json.dumps({"item_id": item_id, "digest": f"d-{i}", "rows": rows}).encode("utf-8")
                sha = hashlib.sha256(body).hexdigest()
                raw_path = f"raw/{sha}.json"
                payloads[f"{base_prefix}/{raw_path}"] = body
                items.append({"id": item_id, "symbol": f"{1000 + i}.TW"})
                manifest_items[item_id] = {"item_id": item_id, "content_sha256": sha, "bytes": len(body), "raw_path": raw_path}

            finished_at = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
            bundle = VerifiedBundle(
                source_id=source_id,
                date=date_str,
                run_id=run_id,
                finished_at=finished_at,
                manifest={"schema_version": "collector_bundle.v1", "items": list(manifest_items.values())},
                items=tuple(items),
                manifest_items=manifest_items,
                raw_bytes={},
                cursor=f"{finished_at.isoformat()}|{run_id}",
            )
            transport = BoundedStreamBundleTransport(payloads, [])

            def lookup_once(sym: str) -> float:
                t0 = time.perf_counter()
                snap = universe.snapshot_for_request(sym)
                if snap is None:
                    raise RuntimeError(f"Expected stored snapshot for {sym}")
                _ = json.dumps(snap.to_public_dict(requested_symbol=sym))
                return (time.perf_counter() - t0) * 1000.0

            def raw_window_once() -> float:
                t0 = time.perf_counter()
                verified = _verify_raw_window(
                    transport,
                    bundle,
                    item_window=(0, HISTORY_RAW_READ_CONCURRENCY),
                    deadline=None,
                    raw_read_concurrency=HISTORY_RAW_READ_CONCURRENCY,
                )
                if len(verified.raw_bytes) != HISTORY_RAW_READ_CONCURRENCY:
                    raise RuntimeError("Incomplete raw window verification")
                return (time.perf_counter() - t0) * 1000.0

            checkpoints = {0, 1, 25, 50, 75, 100, 125, 150, 175, rounds}
            series: list[dict[str, Any]] = [{"round": 0, **read_proc_self_smaps()}]
            window_times_ms: list[float] = []
            lookup_times_ms: list[float] = []

            with ThreadPoolExecutor(max_workers=request_pool_workers, thread_name_prefix="request-pool") as req_pool:
                for round_idx in range(1, rounds + 1):
                    w_futures = [req_pool.submit(raw_window_once) for _ in range(concurrent_windows_per_round)]
                    l_futures = [req_pool.submit(lookup_once, symbols[(round_idx + k) % len(symbols)]) for k in range(lookups_per_round)]
                    for wf in w_futures:
                        window_times_ms.append(wf.result())
                    for lf in l_futures:
                        lookup_times_ms.append(lf.result())
                    if round_idx in checkpoints:
                        gc.collect()
                        series.append({"round": round_idx, **read_proc_self_smaps()})
        finally:
            if prev_active is None:
                os.environ.pop("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", None)
            else:
                os.environ["SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS"] = prev_active

    by_round = {entry["round"]: entry for entry in series}
    r0, r50, r_end = by_round[0], by_round.get(50, series[min(1, len(series) - 1)]), by_round[rounds]
    return {
        "malloc_arena_max": os.environ.get("MALLOC_ARENA_MAX", "default (unset)"),
        "cpu_count": os.cpu_count(),
        "rounds": rounds,
        "raw_read_window_workers": HISTORY_RAW_READ_CONCURRENCY,
        "request_pool_workers": request_pool_workers,
        "concurrent_windows_per_round": concurrent_windows_per_round,
        "snapshot_lookups_per_round": lookups_per_round,
        "series": series,
        "summary": {
            "initial_rss_mb": r0["total_rss_mb"],
            "round_50_rss_mb": r50["total_rss_mb"],
            "final_rss_mb": r_end["total_rss_mb"],
            "peak_rss_mb": max(s["total_rss_mb"] for s in series),
            "rss_growth_round_50_to_end_mb": round(r_end["total_rss_mb"] - r50["total_rss_mb"], 2),
            "main_heap_initial_mb": r0["main_heap_rss_mb"],
            "main_heap_final_mb": r_end["main_heap_rss_mb"],
            "initial_aligned_64mb_arenas": r0["aligned_64mb_arenas"],
            "round_50_aligned_64mb_arenas": r50["aligned_64mb_arenas"],
            "final_aligned_64mb_arenas": r_end["aligned_64mb_arenas"],
            "max_aligned_64mb_arenas": max(s["aligned_64mb_arenas"] for s in series),
            "round_50_aligned_64mb_arena_rss_mb": r50["aligned_64mb_arena_rss_mb"],
            "final_aligned_64mb_arena_rss_mb": r_end["aligned_64mb_arena_rss_mb"],
            "arena_rss_growth_round_50_to_end_mb": round(r_end["aligned_64mb_arena_rss_mb"] - r50["aligned_64mb_arena_rss_mb"], 2),
            "raw_read_window_wall_ms": {
                "count": len(window_times_ms),
                "median_ms": round(statistics.median(window_times_ms), 3),
                "p99_ms": round(_percentile_99(window_times_ms), 3),
            },
            "stored_snapshot_lookup_wall_ms": {
                "count": len(lookup_times_ms),
                "median_ms": round(statistics.median(lookup_times_ms), 3),
                "p99_ms": round(_percentile_99(lookup_times_ms), 3),
            },
        },
    }


def _pct_change(before_val: float, after_val: float) -> float:
    return 0.0 if before_val <= 0 else round(((after_val - before_val) / before_val) * 100.0, 2)


def run_comparison(rounds: int) -> dict[str, Any]:
    base_env = dict(os.environ)
    base_env.pop("MALLOC_ARENA_MAX", None)
    cmd = [sys.executable, str(Path(__file__).resolve()), "--rounds", str(rounds)]
    before = json.loads(subprocess.run(cmd, env=base_env, check=True, capture_output=True, text=True).stdout)
    after_env = {**base_env, "MALLOC_ARENA_MAX": "2"}
    after = json.loads(subprocess.run(cmd, env=after_env, check=True, capture_output=True, text=True).stdout)
    b_sum, a_sum = before["summary"], after["summary"]
    w_med = _pct_change(b_sum["raw_read_window_wall_ms"]["median_ms"], a_sum["raw_read_window_wall_ms"]["median_ms"])
    w_p99 = _pct_change(b_sum["raw_read_window_wall_ms"]["p99_ms"], a_sum["raw_read_window_wall_ms"]["p99_ms"])
    l_med = _pct_change(b_sum["stored_snapshot_lookup_wall_ms"]["median_ms"], a_sum["stored_snapshot_lookup_wall_ms"]["median_ms"])
    l_p99 = _pct_change(b_sum["stored_snapshot_lookup_wall_ms"]["p99_ms"], a_sum["stored_snapshot_lookup_wall_ms"]["p99_ms"])
    return {
        "default_allocator": before,
        "malloc_arena_max_2": after,
        "comparison": {
            "max_aligned_64mb_arenas_before": b_sum["max_aligned_64mb_arenas"],
            "max_aligned_64mb_arenas_after": a_sum["max_aligned_64mb_arenas"],
            "final_aligned_64mb_arena_rss_mb_before": b_sum["final_aligned_64mb_arena_rss_mb"],
            "final_aligned_64mb_arena_rss_mb_after": a_sum["final_aligned_64mb_arena_rss_mb"],
            "final_rss_mb_before": b_sum["final_rss_mb"],
            "final_rss_mb_after": a_sum["final_rss_mb"],
            "rss_growth_round_50_to_end_mb_before": b_sum["rss_growth_round_50_to_end_mb"],
            "rss_growth_round_50_to_end_mb_after": a_sum["rss_growth_round_50_to_end_mb"],
            "raw_read_window_median_ms_change_pct": w_med,
            "raw_read_window_p99_ms_change_pct": w_p99,
            "stored_snapshot_lookup_median_ms_change_pct": l_med,
            "stored_snapshot_lookup_p99_ms_change_pct": l_p99,
            "slowdown_above_10_pct": any(pct > 10.0 for pct in (w_med, w_p99, l_med, l_p99)),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=200, help="Number of rounds (>= 200)")
    parser.add_argument("--compare", action="store_true", help="Run default allocator vs MALLOC_ARENA_MAX=2")
    args = parser.parse_args()
    print(json.dumps(run_comparison(args.rounds) if args.compare else run_driver(rounds=args.rounds), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
