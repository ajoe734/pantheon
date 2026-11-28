#!/usr/bin/env python3
"""Read-only post-release proof from collector and hosted intake snapshots.

Reads files only, never imports a runtime or opens the network. Export these
snapshots after the observation window; stdout is the evidence to retain.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

SOURCES = ("collector-gdelt", "collector-paper-arxiv", "collector-paper-openalex",
           "collector-tw-twse-index-breadth", "collector-youtube-finance")


def utc(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamps require a timezone")
    return result.astimezone(timezone.utc)


def rows(path):
    with Path(path).open() as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def probe(status, journal, dlq, connector_map, since, observed_at, youtube_pause_evidence=None):
    start, end = utc(since), utc(observed_at)
    if (end - start).total_seconds() < 86400:
        raise ValueError("at least 24 hours of post-switch observation required")
    if set(connector_map) != set(SOURCES) or len(set(connector_map.values())) != len(SOURCES):
        raise ValueError("map every scoped source to its distinct actual hosted connector id")
    scope = set(connector_map.values())
    runs, receipts = {}, {}
    for entry in rows(journal):
        payload = entry["payload"]
        if payload.get("connector_id") not in scope:
            continue
        target = {"ingest_run": runs, "ingest_receipt": receipts}.get(entry["record_type"])
        if target is not None:
            target[payload["ingest_run_id"]] = payload
    in_window = lambda value: start <= utc(value) <= end
    failed = [r["ingest_run_id"] for r in runs.values()
              if in_window(r["started_at"]) and r["status"] == "failed"]
    dlq_ids = set()
    for entry in rows(dlq):
        if entry.get("event", {}).get("payload", {}).get("connector_id") in scope and in_window(entry["rejected_at"]):
            dlq_ids.add(entry["entry_id"])
    errors, proofs = [], {}
    for source in SOURCES:
        host = status["sources"][source]
        cid = connector_map[source]
        succeeded = [r["ingest_run_id"] for r in receipts.values()
                     if r["connector_id"] == cid and r["status"] == "completed"
                     and in_window(r["started_at"]) and int(r["normalized_count"]) > 0
                     and r.get("storage_refs") and r["ingest_run_id"] in runs
                     and runs[r["ingest_run_id"]]["status"] == "completed"]
        proofs[source] = {"host_status": host["status"], "host_run_id": host.get("last_run_id"),
                          "host_reason": host.get("paused_reason") or host.get("reason"),
                          "ingest_run_ids": succeeded}
        pause = youtube_pause_evidence or {}
        intentional_pause = (host["status"] == "paused" and bool(host.get("paused_reason"))
                             and host.get("timer_enabled") is False and pause.get("intentional") is True
                             and bool(pause.get("actor")) and bool(pause.get("evidence_ref")))
        if source in {"collector-gdelt", "collector-paper-arxiv"} or (source == "collector-youtube-finance" and not intentional_pause):
            if not succeeded or host["status"] != "complete" or not in_window(host["last_run_at"]):
                errors.append(f"{source}: no fresh complete host run and persisted intake receipt")
        elif host["status"] in {"paused", "partial"} and not proofs[source]["host_reason"]:
            errors.append(f"{source}: unexplained producer state")
    if failed or dlq_ids:
        errors.append("scoped failed runs or dead letters grew during the window")
    return {"status": "failed" if errors else "passed", "since": since, "observed_at": observed_at,
            "proofs": proofs, "failed_run_ids": failed, "new_dlq_ids": sorted(dlq_ids),
            "dlq_growth_per_day": round(len(dlq_ids) * 86400 / (end - start).total_seconds(), 3), "errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("collector-status", "ingest-schedule", "dlq", "connector-map", "since", "observed-at"):
        parser.add_argument(f"--{arg}", required=True)
    parser.add_argument("--youtube-pause-evidence")
    args = parser.parse_args()
    try:
        result = probe(json.loads(Path(args.collector_status).read_text()), args.ingest_schedule, args.dlq,
                       json.loads(Path(args.connector_map).read_text()), args.since, args.observed_at,
                       json.loads(Path(args.youtube_pause_evidence).read_text()) if args.youtube_pause_evidence else None)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        result = {"status": "failed", "errors": [str(exc)]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
