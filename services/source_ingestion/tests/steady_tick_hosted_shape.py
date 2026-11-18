"""Builder of a hosted-shaped source-ingest data root for the steady-tick tests (not production code).

Hosted facts (reported by other coordinators, 2026-11-18): 39 connectors, 32 requirements, a 41 MB receipt
journal (ingest_schedule.jsonl), about 440 pending DLQ entries, connector_config.jsonl of 4.3 MB / 4512 lines.
Workers have no hosted access, so every row here is generated from the runtime's own rows after real ticks:
finished run / receipt / frontier rows are cloned with fresh ids and older times; fetch-state rows are cloned
with higher attempt counters. The journals are appended raw (the stores catch up by stat key), exactly as
another writer would.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HOSTED_JOURNAL_BYTES = 41_000_000
HOSTED_CONNECTOR_LINES = 4512
HOSTED_PENDING_DLQ = 440


def _line(obj: dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n"


def _stamp(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def grow_receipt_journal(path: Path, target_bytes: int = HOSTED_JOURNAL_BYTES) -> int:
    """Append finished run/receipt/frontier row sets cloned from the last real ones until ``target_bytes``."""
    finals: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(raw)
        if row["record_type"] in {"ingest_run", "ingest_receipt", "crawl_frontier_item"}:
            finals[(row["record_type"], row["record_id"])] = row
    runs = [r for (t, _), r in finals.items() if t == "ingest_run" and r["payload"]["status"] == "completed"]
    assert runs, "grow_receipt_journal needs at least one completed run from a real tick"
    template = runs[-1]
    run_id = template["record_id"]
    receipt = finals[("ingest_receipt", run_id)]
    frontier = next((r for r in finals.values() if r["record_type"] == "crawl_frontier_item"
                     and r["payload"].get("ingest_run_id") == run_id), None)
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    written = count = 0
    with path.open("a", encoding="utf-8") as handle:
        start_size = path.stat().st_size
        while start_size + written < target_bytes:
            chunk: list[str] = []
            for _ in range(200):
                count += 1
                new_id = f"ingest-{uuid.uuid4().hex[:12]}"
                when = _stamp(base - timedelta(seconds=86 * count))
                text = json.dumps(template).replace(run_id, new_id)
                run_row = json.loads(text)
                for event in run_row["payload"]["events"]:
                    event["created_at"] = when
                run_row["payload"].update(started_at=when, finished_at=when)
                receipt_row = json.loads(json.dumps(receipt).replace(run_id, new_id))
                receipt_row["payload"].update(created_at=when, started_at=when, finished_at=when)
                chunk += [_line(run_row), _line(receipt_row)]
                if frontier is not None:
                    frontier_row = json.loads(json.dumps(frontier).replace(run_id, new_id))
                    fid = f"frontier-{uuid.uuid4().hex[:12]}"
                    frontier_row["payload"].update(frontier_id=fid, created_at=when, updated_at=when)
                    frontier_row["record_id"] = fid
                    chunk.append(_line(frontier_row))
            blob = "".join(chunk)
            handle.write(blob)
            handle.flush()
            written += len(blob.encode("utf-8"))
    return count


def grow_connector_journal(path: Path, total_lines: int = HOSTED_CONNECTOR_LINES) -> int:
    """Append fetch-state rows (cloned, attempts increased) until the journal has ``total_lines`` rows."""
    lines = path.read_text(encoding="utf-8").splitlines()
    states = [json.loads(x) for x in lines if '"connector_fetch_state"' in x]
    assert states, "grow_connector_journal needs at least one fetch state from a real tick"
    added = 0
    with path.open("a", encoding="utf-8") as handle:
        while len(lines) + added < total_lines:
            row = json.loads(json.dumps(states[added % len(states)]))
            row["payload"]["attempts"] = int(row["payload"].get("attempts") or 0) + added + 1
            row["payload"]["successful_attempts"] = int(row["payload"].get("successful_attempts") or 0) + added + 1
            handle.write(_line(row))
            added += 1
    return added


def add_pending_dlq(runtime: Any, connector_ids: list[str], count: int = HOSTED_PENDING_DLQ) -> None:
    from services.foundation.envelopes import TraceContext
    from services.foundation.outbox import EventEnvelope

    scheduler = runtime.scheduler
    for index in range(count):
        trace = TraceContext(trace_id=f"steady-{index}", correlation_id=f"steady-{index}",
                             environment=scheduler.environment, actor_ref=scheduler.actor_ref)
        event = EventEnvelope.new(
            event_type="source_ingestion.scheduled_run_failed", aggregate_type="source_ingest_run",
            aggregate_id=f"ingest-dlq-{index}", sequence_no=1, trace=trace, producer_service="source-ingestion",
            payload={"ingest_run_id": f"ingest-dlq-{index}", "connector_id": connector_ids[index % len(connector_ids)]},
        )
        runtime.dead_letter_queue.reject(event, reason="steady-tick fixture", tags=("steady-tick",))
