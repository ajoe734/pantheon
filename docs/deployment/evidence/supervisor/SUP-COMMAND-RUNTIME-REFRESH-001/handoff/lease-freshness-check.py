#!/usr/bin/env python3
"""Read-only freshness check for the live worker leases (lease renewal inputs)."""
import json
import pathlib
from datetime import datetime, timezone

STATE = pathlib.Path("/home/lupin/pantheon/.orchestrator/state.json")
ACTIVE = {"running", "started", "waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled", "fallback"}
KEYS = (
    "status",
    "pid",
    "lease_acquired_at",
    "lease_expires_at",
    "last_heartbeat_at",
    "last_event_at",
    "last_work_progress_at",
    "last_process_activity_at",
)


def parse(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def main():
    now = datetime.now(timezone.utc)
    print("now", now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    state = json.loads(STATE.read_text())
    for run_id, worker in sorted((state.get("workers") or {}).items()):
        if worker.get("status") not in ACTIVE:
            continue
        print(f"\n{run_id}  task={worker.get('task_id')}")
        for key in KEYS:
            value = worker.get(key)
            parsed = parse(value)
            age = f"  age={int((now - parsed).total_seconds())}s" if parsed else ""
            print(f"  {key:26} {value}{age}")
        heartbeat = pathlib.Path(str(worker.get("heartbeat_path") or ""))
        if heartbeat.exists():
            payload = json.loads(heartbeat.read_text() or "{}")
            beat = parse(payload.get("heartbeat_at") or payload.get("updated_at") or payload.get("ts"))
            print(f"  heartbeat_file_age          {int((now - beat).total_seconds())}s" if beat else f"  heartbeat_file            {payload}")


if __name__ == "__main__":
    main()
