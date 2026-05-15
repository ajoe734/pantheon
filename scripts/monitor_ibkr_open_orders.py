#!/usr/bin/env python3
"""Poll IBKR open orders and archive compact readback snapshots.

This script is read-only. It does not place, modify, or cancel orders.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TERMINAL_STATES = {"cancelled", "canceled", "inactive", "filled"}


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_state(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "")


def summarize_probe(path: Path, order_id: str | None) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    open_orders = payload.get("open_orders") or []
    if order_id is not None:
        open_orders = [order for order in open_orders if str(order.get("order_id")) == str(order_id)]
    statuses = [str(order.get("status") or "") for order in open_orders]
    terminal = bool(statuses) and all(normalize_state(status) in TERMINAL_STATES for status in statuses)
    absent = order_id is not None and not open_orders
    return {
        "generated_at": payload.get("generated_at"),
        "probe_file": str(path),
        "open_order_count": len(open_orders),
        "statuses": statuses,
        "terminal": terminal,
        "absent": absent,
    }


def run_probe(args: argparse.Namespace, output_json: Path, client_id: int) -> None:
    command = [
        sys.executable,
        "scripts/probe_ibkr_open_orders.py",
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--client-id",
        str(client_id),
        "--timeout-seconds",
        str(args.timeout_seconds),
        "--output-json",
        str(output_json),
    ]
    subprocess.run(command, check=True, cwd=args.repo_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only monitor for IBKR open-order status.")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--client-id-start", type=int, required=True)
    parser.add_argument("--order-id", default=None)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []

    for index in range(args.iterations):
        client_id = args.client_id_start + index
        output_json = output_dir / f"open-orders-{index + 1:02d}.json"
        run_probe(args, output_json, client_id)
        summary = summarize_probe(output_json, args.order_id)
        summaries.append(summary)
        if summary["absent"] or summary["terminal"]:
            break
        if index + 1 < args.iterations:
            time.sleep(args.interval_seconds)

    result = {
        "status": "terminal_or_absent" if summaries and (summaries[-1]["absent"] or summaries[-1]["terminal"]) else "still_open_or_pending",
        "generated_at": iso_now(),
        "order_id": args.order_id,
        "iterations": len(summaries),
        "summaries": summaries,
        "notes": ["Read-only monitor; no order placement, modification, or cancellation was performed."],
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "terminal_or_absent" else 2


if __name__ == "__main__":
    raise SystemExit(main())
