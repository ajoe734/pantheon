#!/usr/bin/env python3
"""Auto-unblock stale `blocked` tasks whose formal dependencies are all satisfied.

Guards: only status==blocked; ALL depends_on in done/archived; no live worker on
it; blocked >= MIN_AGE_SECONDS; loop cap MAX_AUTO_REOPENS (then leave for human).
Reopen via ai_status.py CLI impersonating the owner.
"""
from __future__ import annotations
import hashlib
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

STATUS_FILE = ROOT / "ai-status.json"
ARCHIVE_DIR = ROOT / "ai-task-archive" / "tasks"
STATE_FILE = ROOT / ".orchestrator" / "auto-unblock-state.json"
AI_STATUS_CLI = ROOT / "scripts" / "ai_status.py"
DONE_STATUSES = {"done"}
MIN_AGE_SECONDS = 480
MAX_AUTO_REOPENS = 2
DRY_RUN = "--dry-run" in sys.argv
SEQUENCING_GATED_CLASSIFICATIONS = {
    "deferred strict-auth/security/governance work",
    "final verification/closeout after the appropriate gate",
}
SEQUENCING_RELEASE_PREDICATE = "g2_evidence_contract_v2_valid"
SEQUENCING_RELEASE_RECORD_FIELDS = {
    "schema_version",
    "program_id",
    "effective_catalog_sha256",
    "sequencing_overlay_sha256",
    "release_gate_id",
    "release_predicate",
    "released_at",
    "g2_issued_at",
    "closeout_at",
    "g2_evidence_sha256",
    "canonical_record_bundle_sha256",
    "hosted_probe_sha256",
    "product_manifest_sha256",
    "product_manifest_sidecar_sha256",
    "target_task_snapshot_sha256",
    "reviewer",
    "review_verdict_sha256",
    "release_admission_sha256",
    "released_task_transitions",
    "released_task_transition_set_sha256",
}
SEQUENCING_RELEASE_ADMISSION_FIELDS = {
    "g2_evidence_sha256",
    "canonical_record_bundle_sha256",
    "hosted_probe_sha256",
    "product_manifest_sha256",
    "product_manifest_sidecar_sha256",
    "target_task_snapshot_sha256",
    "reviewer",
    "review_verdict_sha256",
    "g2_issued_at",
    "closeout_at",
}
SEQUENCING_RELEASE_TRANSITION_FIELDS = {
    "task_id",
    "before_task_snapshot_sha256",
    "after_task_snapshot_sha256",
    "before_status",
    "after_status",
}
SEQUENCING_EPOCH_FIELDS = {
    "schema_version",
    "program_id",
    "source_catalog_sha256",
    "effective_catalog_sha256",
    "sequencing_overlay_sha256",
    "release_gate_id",
    "install_mode",
    "applied_at",
    "source_graph_projection_sha256",
    "effective_graph_projection_sha256",
    "task_count",
    "task_transitions",
    "task_transition_set_sha256",
}
SEQUENCING_EPOCH_TRANSITION_FIELDS = {
    "task_id",
    "before_task_snapshot_sha256",
    "after_task_snapshot_sha256",
    "before_task_contract_sha256",
    "after_task_contract_sha256",
    "before_source_ref_sha256",
    "after_source_ref_sha256",
    "before_status",
    "after_status",
    "acceptance_deferral_sha256",
    "gate_marker_sha256",
}


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


def _canonical_sha256(value: object) -> str:
    body = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _source_ref(task: dict) -> dict:
    value = task.get("source_ref")
    return value if isinstance(value, dict) else {}


def _classification(task: dict) -> str:
    return str(_source_ref(task).get("sequencing_classification") or "")


def _valid_release_admission(data: dict, task: dict) -> bool:
    source_ref = _source_ref(task)
    program_id = str(source_ref.get("program_id") or "").strip()
    task_id = str(task.get("id") or "").strip()
    task_admission = task.get("sequencing_release_admission_sha256")
    releases = data.get("program_sequencing_releases")
    record = releases.get(program_id) if isinstance(releases, dict) else None
    transitions = (
        record.get("released_task_transitions")
        if isinstance(record, dict)
        else None
    )
    if (
        not program_id
        or not task_id
        or _classification(task) not in SEQUENCING_GATED_CLASSIFICATIONS
        or not _is_sha256(task_admission)
        or not isinstance(record, dict)
        or set(record) != SEQUENCING_RELEASE_RECORD_FIELDS
        or record.get("schema_version") != 1
        or record.get("program_id") != program_id
        or record.get("effective_catalog_sha256")
        != source_ref.get("catalog_sha256")
        or record.get("sequencing_overlay_sha256")
        != source_ref.get("sequencing_overlay_sha256")
        or record.get("release_gate_id") != source_ref.get("release_gate_id")
        or record.get("release_predicate") != SEQUENCING_RELEASE_PREDICATE
        or record.get("release_admission_sha256") != task_admission
        or not isinstance(record.get("reviewer"), str)
        or not str(record.get("reviewer") or "").strip()
        or not isinstance(transitions, list)
        or not transitions
    ):
        return False
    hash_fields = {
        "effective_catalog_sha256",
        "sequencing_overlay_sha256",
        "g2_evidence_sha256",
        "canonical_record_bundle_sha256",
        "hosted_probe_sha256",
        "product_manifest_sha256",
        "product_manifest_sidecar_sha256",
        "target_task_snapshot_sha256",
        "review_verdict_sha256",
        "release_admission_sha256",
        "released_task_transition_set_sha256",
    }
    if any(not _is_sha256(record.get(field)) for field in hash_fields):
        return False
    released_at = _parse_iso(str(record.get("released_at") or ""))
    g2_issued_at = _parse_iso(str(record.get("g2_issued_at") or ""))
    closeout_at = _parse_iso(str(record.get("closeout_at") or ""))
    if (
        not released_at
        or not g2_issued_at
        or not closeout_at
        or closeout_at > g2_issued_at
        or g2_issued_at > released_at
    ):
        return False
    admission = {
        field: record.get(field)
        for field in SEQUENCING_RELEASE_ADMISSION_FIELDS
    }
    if record.get("release_admission_sha256") != _canonical_sha256(admission):
        return False
    transition_ids: list[str] = []
    for transition in transitions:
        if (
            not isinstance(transition, dict)
            or set(transition) != SEQUENCING_RELEASE_TRANSITION_FIELDS
            or transition.get("before_status") != "blocked"
            or transition.get("after_status") != "todo"
            or not _is_sha256(transition.get("before_task_snapshot_sha256"))
            or not _is_sha256(transition.get("after_task_snapshot_sha256"))
        ):
            return False
        transition_id = str(transition.get("task_id") or "").strip()
        if not transition_id:
            return False
        transition_ids.append(transition_id)

    epochs = data.get("program_sequencing_epochs")
    epoch = epochs.get(program_id) if isinstance(epochs, dict) else None
    epoch_transitions = (
        epoch.get("task_transitions") if isinstance(epoch, dict) else None
    )
    if (
        not isinstance(epoch, dict)
        or set(epoch) != SEQUENCING_EPOCH_FIELDS
        or epoch.get("schema_version") != 1
        or epoch.get("program_id") != program_id
        or epoch.get("effective_catalog_sha256")
        != record.get("effective_catalog_sha256")
        or epoch.get("sequencing_overlay_sha256")
        != record.get("sequencing_overlay_sha256")
        or epoch.get("release_gate_id") != record.get("release_gate_id")
        or epoch.get("install_mode")
        not in {"base_epoch_migration", "fresh_materialization"}
        or not _parse_iso(str(epoch.get("applied_at") or ""))
        or any(
            not _is_sha256(epoch.get(field))
            for field in (
                "source_catalog_sha256",
                "effective_catalog_sha256",
                "sequencing_overlay_sha256",
                "source_graph_projection_sha256",
                "effective_graph_projection_sha256",
                "task_transition_set_sha256",
            )
        )
        or not isinstance(epoch_transitions, list)
        or not epoch_transitions
        or epoch.get("task_count") != len(epoch_transitions)
        or epoch.get("task_transition_set_sha256")
        != _canonical_sha256(epoch_transitions)
    ):
        return False
    epoch_ids: list[str] = []
    gated_epoch_transitions: list[dict] = []
    for transition in epoch_transitions:
        if (
            not isinstance(transition, dict)
            or set(transition) != SEQUENCING_EPOCH_TRANSITION_FIELDS
            or transition.get("before_status") not in {"absent", "todo"}
            or transition.get("after_status") not in {"blocked", "todo"}
            or any(
                not _is_sha256(transition.get(field))
                for field in SEQUENCING_EPOCH_TRANSITION_FIELDS
                if field.endswith("sha256")
            )
        ):
            return False
        epoch_task_id = str(transition.get("task_id") or "").strip()
        if not epoch_task_id:
            return False
        epoch_ids.append(epoch_task_id)
        if transition.get("after_status") == "blocked":
            gated_epoch_transitions.append(transition)
    if (
        len(epoch_ids) != len(set(epoch_ids))
        or not gated_epoch_transitions
        or transition_ids
        != [str(row["task_id"]) for row in gated_epoch_transitions]
        or len(transition_ids) != len(set(transition_ids))
        or task_id not in transition_ids
        or any(
            release_transition["before_task_snapshot_sha256"]
            != epoch_transition["after_task_snapshot_sha256"]
            for release_transition, epoch_transition in zip(
                transitions, gated_epoch_transitions
            )
        )
        or record.get("released_task_transition_set_sha256")
        != _canonical_sha256(transitions)
    ):
        return False
    return True


_valid_release_admission = (
    lambda data, task: sequencing_gate.task_has_valid_sequencing_release_admission(
        task, data
    )
)


def _sequencing_parked(data: dict, task: dict, parked_ids: set[str]) -> bool:
    _ = parked_ids
    return sequencing_gate.task_is_sequencing_parked(task, data)


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
    sequencing_parked_ids: set[str] = set()
    epochs = data.get("program_sequencing_epochs") or {}
    if isinstance(epochs, dict):
        for epoch in epochs.values():
            if not isinstance(epoch, dict):
                continue
            for transition in epoch.get("task_transitions") or []:
                if (
                    isinstance(transition, dict)
                    and transition.get("after_status") == "blocked"
                    and str(transition.get("task_id") or "").strip()
                ):
                    sequencing_parked_ids.add(str(transition["task_id"]))
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
        if _sequencing_parked(data, t, sequencing_parked_ids):
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
