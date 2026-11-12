#!/usr/bin/env python3
"""Read-only intraday (tw_price_1m) vs official daily reconciliation report.

Evaluates every stored symbol-day with ``write=False``: nothing in the minute store,
the reconciliation sidecar or ``tw_price_daily`` is created, changed or deleted.
Reports per-outcome counts and the pass rate over the first N trading days of real
data (default 10 = two weeks). A rate below the threshold is reported to the operator;
tolerances are never widened.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.source_ingestion.intraday_reconcile import (  # noqa: E402
    OfficialDaily,
    load_config,
    pass_rate_summary,
    reconcile_day,
)
from services.source_ingestion.intraday_store import IntradayStore, NORMALIZED_DIR  # noqa: E402


def build_report(store_root: Path, official_root: Path, config: Path | None, days: int | None) -> dict:
    cfg = load_config(config)
    store = IntradayStore(store_root)
    official = OfficialDaily.from_snapshot(official_root)
    day_root = store.root / NORMALIZED_DIR
    all_days = sorted(p.name for p in day_root.iterdir() if (p / "manifest.json").exists()) if day_root.is_dir() else []
    window = all_days[: days if days is not None else cfg.pass_rate_window_days]
    results, per_day = [], {}
    for day in window:
        day_results = reconcile_day(store, official, day, cfg, write=False)
        results.extend(day_results)
        per_day[day] = pass_rate_summary(day_results, cfg)["outcome_counts"]
    failing = [r.to_dict() for r in results if r.outcome not in ("pass", "pending_official", "minute_missing", "not_applicable")]
    return {
        "read_only": True,
        "config_hash": cfg.config_hash,
        "tolerances": cfg.tolerances(),
        "window_trading_days": window,
        "days_available": len(all_days),
        "summary": pass_rate_summary(results, cfg),
        "per_day_outcomes": per_day,
        "non_pass": failing,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store-root", required=True, type=Path)
    ap.add_argument("--official-root", required=True, type=Path)
    ap.add_argument("--config", type=Path)
    ap.add_argument("--days", type=int, help="first N trading days with data (default: config window)")
    ap.add_argument("--output", type=Path, help="write report JSON here instead of stdout")
    args = ap.parse_args(argv)
    report = build_report(args.store_root, args.official_root, args.config, args.days)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
