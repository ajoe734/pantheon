"""Read-only post-deploy receipt probe; run inside the source-ingest container.

python services/source_ingestion/tests/collector_intake_acceptance.py --since <switch-UTC>
Only reads a bounded journal tail; no network, writes, scheduler calls or cursor changes.
Exit 0 requires a positive ingest after the switch and a current cursor for both sources.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

SOURCES = ("collector-tw-threads", "collector-tw-ptt-stock")


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def probe(data_root, since, *, now=None, tail_bytes=24_000_000):
    now = now or datetime.now(timezone.utc)
    path = Path(data_root) / "ingest_schedule.jsonl"
    latest, positive = {}, {}
    with path.open("rb") as handle:
        # Fix the upper boundary so a concurrently appended journal stays bounded.
        end = path.stat().st_size
        start = max(0, end - tail_bytes)
        handle.seek(start)
        data = handle.read(end - start)
    if start:
        data = data.partition(b"\n")[2]
    for line in data.splitlines():
        if not any(source.encode() in line for source in SOURCES):
            continue
        row = json.loads(line)
        payload = row.get("payload", {})
        source = payload.get("connector_id")
        if row.get("record_type") != "ingest_receipt" or source not in SOURCES:
            continue
        if utc(payload["started_at"]) < since:
            continue
        receipt = {k: payload.get(k) for k in (
            "ingest_run_id", "started_at", "finished_at", "status", "raw_count", "watermark")}
        latest[source] = receipt
        if receipt["status"] == "completed" and (receipt["raw_count"] or 0) > 0:
            positive[source] = receipt
    sources = {}
    for source in SOURCES:
        receipt = latest.get(source, {})
        cursor = receipt.get("watermark")
        lag = (now - utc(cursor.split("#", 1)[0])).total_seconds() / 86400 if cursor else None
        sources[source] = {
            "latest_receipt": receipt, "positive_ingest": positive.get(source), "cursor_lag_days": lag,
            "passed": bool(positive.get(source) and receipt.get("status") == "completed"
                           and lag is not None and 0 <= lag < 1),
        }
    return {"captured_at": now.isoformat(), "since": since.isoformat(),
            "journal_bytes_examined": len(data), "sources": sources,
            "passed": all(row["passed"] for row in sources.values())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="/data/source-ingest")
    parser.add_argument("--since", required=True, type=utc)
    args = parser.parse_args()
    report = probe(args.data_root, args.since)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
