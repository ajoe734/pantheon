"""Recovery boundary regressions with real archive, journal and runtime CAS.

All identities below are isolated test fixtures. No live state is read or
mutated. Terminal event proof and archive hashes use the production readers.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import supervisor as sup
import runtime_state
from test_supervisor import config_fixture, task_fixture
import test_supervisor as fixtures


def stamp(event):
    event.pop("event_id", None)
    event["event_id"] = "ai-status-event-" + hashlib.sha256(
        json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return event


def fixture(tmp_path, terminal_outcome="completed"):
    root = tmp_path / "status"
    (root / ".orchestrator").mkdir(parents=True)
    config = config_fixture(root)
    config["paths"]["approval_queue"] = str(root / ".orchestrator" / "approvals.json")
    config["task_state_store"] = {"mode": "authoritative", "event_log": str(tmp_path / "tasks.jsonl")}
    full = task_fixture("TASK-1", status="done", owner="Codex", reviewer="Claude")
    full.update(generation=1, terminal_outcome=terminal_outcome)
    archive = {"task_id": full["id"], "task": copy.deepcopy(full)}
    path = root / "ai-task-archive" / "tasks" / "TASK-1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(archive))
    fact = {key: full[key] for key in ("status", "generation", "terminal_outcome")}
    fact["recorded_at"] = "2026-09-08T00:00:00Z"
    state = {"tasks": [], "agents": [], "handoffs": [], "blockers": [],
             "terminal_facts": {"TASK-1": fact}, "archive_receipts": {"TASK-1": {
                 "schema_version": 1, "archive_root": str(path.parent.parent),
                 "snapshot_sha256": sup.task_archive._canonical_json_sha256(archive),
                 "index_sha256": "0" * 64, "recorded_at": fact["recorded_at"],
             }}}
    return config, state, path


def save_status(config, state):
    sup.rewrite_task_state_store.append_state_commit(
        config["task_state_store"]["event_log"], state, source="isolated-boundary-fixture")


@pytest.mark.parametrize("damage", [None, "hash", "root", "generation", "outcome", "missing"])
def test_archive_resolution_is_proven_detached_and_fail_closed(tmp_path, damage):
    config, state, path = fixture(tmp_path)
    thin = sup.task_index_from_status(config, state)["TASK-1"]
    if damage == "hash":
        state["archive_receipts"]["TASK-1"]["snapshot_sha256"] = "f" * 64
    elif damage == "root":
        state["archive_receipts"]["TASK-1"]["archive_root"] = "/foreign/archive"
    elif damage == "generation":
        state["terminal_facts"]["TASK-1"]["generation"] = 2
    elif damage == "outcome":
        state["terminal_facts"]["TASK-1"]["terminal_outcome"] = "superseded"
    elif damage == "missing":
        path.unlink()
    before = copy.deepcopy(state)
    resolved = sup.canonical_task_with_archive_proof(config, thin, state=state)
    assert bool(resolved.get("owner")) == (damage is None)
    assert state == before and not state["tasks"]


@pytest.mark.parametrize("mode", ["poll", "boot"])
@pytest.mark.parametrize("bad_proof", [False, True])
@pytest.mark.parametrize("proof_drift", [False, True])
@pytest.mark.parametrize("terminal_outcome", ["completed", "superseded"])
def test_real_exited_workers_archive_and_unrelated_cleanup_commit(tmp_path, mode, bad_proof, proof_drift, terminal_outcome):
    config, canonical, path = fixture(tmp_path, terminal_outcome)
    if bad_proof:
        canonical["archive_receipts"]["TASK-1"]["snapshot_sha256"] = "f" * 64
    second = task_fixture("TASK-2", status="done", owner="Codex", reviewer="Claude")
    second.update(generation=1, terminal_outcome="completed")
    canonical["tasks"].append(second)
    save_status(config, canonical)
    runtime = runtime_state.default_state()
    events = []
    for number in (1, 2):
        # Capture a genuine PID identity, then observe that exact process exit.
        proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
        try:
            worker = fixtures.RuntimeAndFailureSemanticsTests._owner_worker(generation=1)
            worker.update(task_id=f"TASK-{number}", run_id=f"run-{number}",
                          queue_event_id=f"event-{number}", pid=proc.pid,
                          pid_start_ticks=sup.worker_pid_start_ticks(proc.pid))
            worker["process_generation"] = sup.worker_process_generation_id(
                task_id=worker["task_id"], worker_run_id=worker["run_id"],
                queue_event_id=worker["queue_event_id"], pid=worker["pid"],
                pid_start_ticks=worker["pid_start_ticks"])
            event_type = "superseded" if number == 1 and terminal_outcome == "superseded" else "done"
            # Independent cancellation requires archive-proven role authority;
            # it is not this worker's exact normal-done responsibility event.
            actor = "Claude" if event_type == "superseded" else "Codex"
            event = fixtures.RuntimeAndFailureSemanticsTests._exact_lifecycle_event(worker, event_type=event_type, agent=actor)
            event["task_id"] = worker["task_id"]
            events.append(stamp(event))
            runtime["workers"][worker["run_id"]] = worker
            runtime["queue"]["events"][worker["queue_event_id"]] = {
                "status": "processing", "intent": {"event_id": worker["queue_event_id"], "task_id": worker["task_id"]}}
        finally:
            proc.communicate(timeout=5)
        assert not sup.pid_is_alive(worker["pid"])
    for event in events:
        sup.write_activity_log(config, event)
    runtime_state.save_runtime_state(config, runtime)
    journal_before = Path(config["task_state_store"]["event_log"]).read_bytes()
    # Disable only unrelated maintenance/metrics, not observation, terminal
    # predicates, TaskStore reads, archive reads, locks, final CAS or save.
    with (mock.patch.object(sup, "retry_due_workers", return_value=False),
          mock.patch.object(sup, "reconcile_pending_worker_recoveries", return_value=False),
          mock.patch.object(sup, "reconcile_review_decision_intent_lease_recovery", return_value=False),
          mock.patch.object(sup, "record_worker_runtime_measurement"),
          mock.patch.object(sup, "persist_worker_recovery_receipt") as retry_write):
        operation = sup.poll_workers if mode == "poll" else sup.reconcile_runtime_on_boot
        def run(scratch):
            changed = operation(config, scratch)
            if proof_drift:
                path.write_text("{}")
            return changed
        # A proof that changes after classification must veto the entire CAS;
        # a proof already unavailable at observation simply preserves its worker.
        expect_commit = bad_proof or not proof_drift
        assert sup._run_reserved_runtime_phase(config, "test-boundary", run) == expect_commit
        retry_write.assert_not_called()
    final = runtime_state.load_runtime_state(config)
    ended_status = "superseded" if terminal_outcome == "superseded" else "completed"
    assert final["workers"].get("run-1", {}).get("status", ended_status) == ("running" if bad_proof or not expect_commit else ended_status)
    assert final["workers"].get("run-2", {}).get("status", "completed") == ("completed" if expect_commit else "running")
    assert final["queue"]["events"]["event-1"]["status"] == ("processing" if bad_proof or not expect_commit else "completed")
    assert final["queue"]["events"]["event-2"]["status"] == ("completed" if expect_commit else "processing")
    assert Path(config["task_state_store"]["event_log"]).read_bytes() == journal_before
    assert not any(t["id"] == "TASK-1" for t in sup.load_status(config)["tasks"])


def test_exact_event_identity_is_still_required_after_archive_resolution(tmp_path):
    config, canonical, _ = fixture(tmp_path)
    worker = fixtures.RuntimeAndFailureSemanticsTests._owner_worker(generation=1)
    event = fixtures.RuntimeAndFailureSemanticsTests._exact_lifecycle_event(worker, event_type="done")
    thin = sup.task_index_from_status(config, canonical)["TASK-1"]
    assert sup.canonical_worker_terminal_status(config, worker, thin, activity_events=[event], state=canonical) == "done"
    for key, value in (("agent", "Claude"), ("event_id", "forged"), ("task_id", "OTHER")):
        altered = {**event, key: value}
        assert sup.canonical_worker_terminal_status(config, worker, thin, activity_events=[altered], state=canonical) is None
    stale = {**worker, "task_generation": 2}
    assert sup.canonical_worker_terminal_status(config, stale, thin, activity_events=[event], state=canonical) is None
