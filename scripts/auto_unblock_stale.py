#!/usr/bin/env python3
"""Auto-unblock stale `blocked` tasks whose formal dependencies are all satisfied.

Guards: only status==blocked; ALL depends_on in done/archived; no live worker on
it; blocked >= MIN_AGE_SECONDS; loop cap MAX_AUTO_REOPENS (then leave for human).
Reopen via ai_status.py CLI impersonating the owner.
"""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("PANTHEON_STATUS_ROOT", "/home/lupin/code/pantheon"))
ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1] / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

import sequencing_gate
from common import read_activity_audit_records

STATUS_FILE = ROOT / "ai-status.json"
LOG_FILE = ROOT / "ai-activity-log.jsonl"
ARCHIVE_DIR = ROOT / "ai-task-archive" / "tasks"
STATE_FILE = ROOT / ".orchestrator" / "auto-unblock-state.json"
AI_STATUS_CLI = ROOT / "scripts" / "ai_status.py"
DONE_STATUSES = {"done"}
MIN_AGE_SECONDS = 480
MAX_AUTO_REOPENS = 2
DRY_RUN = "--dry-run" in sys.argv


def _archived_ids() -> set[str]:
    try:
        return {f[:-5] for f in os.listdir(ARCHIVE_DIR) if f.endswith(".json")}
    except FileNotFoundError:
        return set()


def _parse_iso(ts: str) -> float:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return 0.0


def _valid_release_admission(
    data: dict,
    task: dict,
    release_audit_proof: sequencing_gate.SequencingReleaseAuditProof | None = None,
) -> bool:
    """Compatibility adapter for callers that use the legacy argument order."""

    return sequencing_gate.task_has_valid_sequencing_release_admission(
        task,
        data,
        release_audit_proof=(
            release_audit_proof
            or getattr(data, "release_audit_proof", None)
        ),
    )


def _sequencing_parked(
    data: dict,
    task: dict,
    parked_ids: set[str] | None = None,
    release_audit_proof: sequencing_gate.SequencingReleaseAuditProof | None = None,
) -> bool:
    """Compatibility adapter; shared authority derives membership from data."""

    _ = parked_ids
    return sequencing_gate.task_is_sequencing_parked(
        task,
        data,
        release_audit_proof=(
            release_audit_proof
            or getattr(data, "release_audit_proof", None)
        ),
    )


def _release_audit_proof(
    data: dict,
) -> sequencing_gate.SequencingReleaseAuditProof | None:
    if not sequencing_gate.status_declares_sequencing_release(data):
        return None
    try:
        records = read_activity_audit_records(
            LOG_FILE,
            stop_after=lambda entry: (
                entry.get("program_id") == sequencing_gate.PROGRAM_ID
                and entry.get("type") == "sequencing_gate_release"
            ),
        )
    except (OSError, RuntimeError, UnicodeError):
        return None
    return sequencing_gate.build_sequencing_release_audit_proof(data, records)


def _running_task_ids() -> set[str]:
    ids: set[str] = set()
    try:
        out = subprocess.run(["pgrep", "-f", "worker_runner.py"], capture_output=True, text=True).stdout
        for pid in out.split():
            try:
                cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
            except OSError:
                continue
            for m in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)+", cmd):
                ids.add(m.upper())
    except FileNotFoundError:
        pass
    return ids


def main() -> int:
    data = json.loads(STATUS_FILE.read_text())
    if sequencing_gate.status_has_pending_program_activity_outbox(data):
        print("auto-unblock paused: program_activity_outbox is pending")
        return 0
    tasks = data.get("tasks", [])
    release_audit_proof = _release_audit_proof(data)
    done = {t["id"] for t in tasks if t.get("status") in DONE_STATUSES} | _archived_ids()
    running = _running_task_ids()
    now = time.time()
    try:
        state = json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    reopened, skipped_loop = [], []
    for t in tasks:
        if t.get("status") != "blocked":
            continue
        # Sequencing gates are released only by the catalog-bound G2 verifier.
        # A malformed marker also fails closed for supervisor/operator repair.
        if _sequencing_parked(
            data,
            t,
            release_audit_proof=release_audit_proof,
        ):
            continue
        tid = t["id"]
        deps = t.get("depends_on") or []
        if [d for d in deps if d not in done]:
            continue
        if now - _parse_iso(t.get("last_update", "")) < MIN_AGE_SECONDS:
            continue
        if tid in running:
            continue
        rec = state.get(tid, {"reopens": 0})
        if rec.get("reopens", 0) >= MAX_AUTO_REOPENS:
            skipped_loop.append(tid)
            continue
        owner = t.get("owner") or "Codex"
        msg = (f"[auto-unblock] All formal deps satisfied ({deps or 'none'}) but still blocked; "
               f"no live worker. Re-opening (auto-reopen #{rec.get('reopens', 0) + 1}/{MAX_AUTO_REOPENS}).")
        if DRY_RUN:
            print(f"WOULD reopen {tid} owner={owner}")
            reopened.append(tid)
            continue
        env = dict(os.environ, AI_NAME=owner, PANTHEON_STATUS_ROOT=str(ROOT))
        r = subprocess.run(["python3", str(AI_STATUS_CLI), "reopen", tid, msg], capture_output=True, text=True, env=env)
        if r.returncode == 0:
            reopened.append(tid)
            rec["reopens"] = rec.get("reopens", 0) + 1
            rec["last_reopen_at"] = int(now)
            state[tid] = rec
        else:
            print(f"reopen FAILED {tid}: {r.stderr.strip()[:160]}", file=sys.stderr)
    if not DRY_RUN:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    print(f"{stamp} auto-unblock: reopened={reopened or 'none'} loop-capped={skipped_loop or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
