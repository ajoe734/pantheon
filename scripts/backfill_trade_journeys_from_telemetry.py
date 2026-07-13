#!/usr/bin/env python3
"""Rebuild the Trade Journey event store from paper-runtime telemetry.

Runs inside (or against) the operator BFF container, which has psycopg3 and
the store volume mounted. Idempotent: each run re-derives every
``telemetry_backfill`` event from Postgres and preserves all other sources
(e.g. the TJ-E2E-012 seed scenarios).

Usage (on the dev VM):
    docker cp scripts/backfill_trade_journeys_from_telemetry.py \
        pantheon-operator-bff-1:/tmp/tj_backfill.py
    docker cp services/trade_journey/telemetry_bridge.py \
        pantheon-operator-bff-1:/tmp/telemetry_bridge.py
    docker exec pantheon-operator-bff-1 python3 /tmp/tj_backfill.py

Defaults come from the BFF container environment:
    --dsn    PANTHEON_TRADE_JOURNEY_ACTION_LEDGER_DSN
    --store  PANTHEON_BFF_TRADE_JOURNEY_EVENTS_STORE
"""

from __future__ import annotations

import argparse
import json
import fcntl
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
for candidate in (Path(__file__).resolve().parent.parent / "services" / "trade_journey",):
    if candidate.is_dir():
        sys.path.insert(0, str(candidate))

from telemetry_bridge import (  # noqa: E402
    TELEMETRY_EVENT_TYPES,
    journey_events_from_telemetry,
    load_store_events,
    merge_with_store,
    write_store_atomic,
)

QUERY = (
    "SELECT event_type, to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), payload "
    "FROM public.telemetry_events WHERE event_type = ANY(%s) ORDER BY created_at"
)


def fetch_rows(dsn: str) -> list[tuple[str, str, dict]]:
    import psycopg  # deferred: only the runtime container needs it

    with psycopg.connect(dsn) as conn:
        rows = conn.execute(QUERY, (list(TELEMETRY_EVENT_TYPES),)).fetchall()
    return [(etype, recorded, payload if isinstance(payload, dict) else json.loads(payload))
            for etype, recorded, payload in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.getenv("PANTHEON_TRADE_JOURNEY_ACTION_LEDGER_DSN", ""))
    parser.add_argument("--store", default=os.getenv("PANTHEON_BFF_TRADE_JOURNEY_EVENTS_STORE", ""))
    parser.add_argument("--tenant", default="default")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.dsn or not args.store:
        parser.error("--dsn and --store are required (or set the corresponding env vars)")

    rows = fetch_rows(args.dsn)
    backfill = journey_events_from_telemetry(rows, tenant_id=args.tenant)
    store_path = Path(args.store)
    lock_path = store_path.with_suffix(".lock")
    
    try:
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            
            existing = load_store_events(store_path)
            merged = merge_with_store(existing, backfill)
            journeys = {event.get("journey_id") for event in backfill}
            print(f"telemetry rows: {len(rows)}; backfill events: {len(backfill)} "
                  f"({len(journeys)} journeys, tenant={args.tenant}); "
                  f"store: {len(existing)} -> {len(merged)} events")
            if args.dry_run:
                return 0
            write_store_atomic(store_path, merged)
            print(f"wrote {store_path}")
    except Exception as exc:
        print(f"Error executing backfill with lock: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
