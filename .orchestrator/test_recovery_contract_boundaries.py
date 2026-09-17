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


def _fenced_worker(task_id, run_id, event_id, *, alive):
    """Build a worker whose lease was fenced, over a real PID identity."""
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
    worker = fixtures.RuntimeAndFailureSemanticsTests._owner_worker(generation=1)
    worker.update(
        task_id=task_id, run_id=run_id, queue_event_id=event_id, pid=proc.pid,
        pid_start_ticks=sup.worker_pid_start_ticks(proc.pid),
        status="recovery_pending",
        lost_lease_receipt_id="promotion-drain-" + "a" * 16,
        lease_fenced_at="2026-09-17T02:02:57Z",
        last_error="Worker lease was lost.",
    )
    worker["process_generation"] = sup.worker_process_generation_id(
        task_id=task_id, worker_run_id=run_id, queue_event_id=event_id,
        pid=worker["pid"], pid_start_ticks=worker["pid_start_ticks"])
    if alive:
        return worker, proc
    proc.communicate(timeout=5)
    assert not sup.pid_is_alive(worker["pid"])
    return worker, None


def _non_terminal_canonical(config, tmp_path):
    """Canonical state whose task is mid-review: no terminal proof can exist."""
    task = task_fixture("TASK-9", status="review", owner="Codex", reviewer="Claude")
    task.update(generation=1)
    state = {"tasks": [task], "agents": [], "handoffs": [], "blockers": [],
             "terminal_facts": {}, "archive_receipts": {}}
    save_status(config, state)
    return state


@pytest.mark.parametrize("alive", [False, True])
def test_fenced_worker_queue_completion_commits_only_when_the_process_is_gone(tmp_path, alive):
    """A lost-lease fence proves a queue completion; a live attempt never does.

    Regression for the fleet-wide dispatch stall: every reconciler that retires
    a fenced attempt's queue record asserted a transition the revalidation could
    not accept, so the whole reserved maintenance phase -- delivery-health
    observations included -- was discarded on every cycle until health evidence
    expired and no lane could be dispatched at all.
    """
    root = tmp_path / "status"
    (root / ".orchestrator").mkdir(parents=True)
    config = config_fixture(root)
    config["paths"]["approval_queue"] = str(root / ".orchestrator" / "approvals.json")
    config["task_state_store"] = {"mode": "authoritative", "event_log": str(tmp_path / "tasks.jsonl")}
    _non_terminal_canonical(config, tmp_path)

    worker, proc = _fenced_worker("TASK-9", "run-9", "event-9", alive=alive)
    runtime = runtime_state.default_state()
    runtime["workers"][worker["run_id"]] = worker
    runtime["queue"]["events"][worker["queue_event_id"]] = {
        "status": "failed",
        "intent": {"event_id": worker["queue_event_id"], "task_id": worker["task_id"]},
    }
    runtime_state.save_runtime_state(config, runtime)

    thin = sup.task_index_from_status(config, sup.load_status(config))["TASK-9"]
    assert sup.canonical_worker_terminal_status(
        config, worker, thin, activity_events=[]) is None, "a fenced attempt has no exact completion event"

    def retire_queue_record_and_observe(scratch):
        # What reconcile_queue_records / reconcile_queue_intents legitimately do.
        scratch["queue"]["events"][worker["queue_event_id"]]["status"] = "completed"
        # An unrelated observation committed by the very same phase.
        scratch.setdefault("delivery_health", {}).setdefault("endpoints", {})["lane-9"] = {
            "state": "healthy", "observed_at": "2026-09-17T05:00:00Z"}
        return True

    try:
        committed = sup._run_reserved_runtime_phase(
            config, "test-fence-proof", retire_queue_record_and_observe)
    finally:
        if proc is not None:
            proc.communicate(timeout=5)

    final = runtime_state.load_runtime_state(config)
    observed = (final.get("delivery_health", {}).get("endpoints", {}).get("lane-9") or {}).get("observed_at")
    if alive:
        assert committed is False
        assert final["queue"]["events"]["event-9"]["status"] == "failed"
        assert observed is None, "a live attempt must not be completed out from under itself"
    else:
        assert committed is True
        assert final["queue"]["events"]["event-9"]["status"] == "completed"
        assert observed == "2026-09-17T05:00:00Z", (
            "the unrelated observation must survive with the accepted transition")


def test_fence_proof_requires_receipt_fence_timestamp_and_a_dead_process(tmp_path):
    """Every condition of the fence proof is load-bearing."""
    worker, _ = _fenced_worker("TASK-9", "run-9", "event-9", alive=False)
    assert sup.worker_fence_proves_queue_completion(worker) is True
    assert sup.worker_fence_proves_queue_completion({**worker, "status": "running"}) is False
    assert sup.worker_fence_proves_queue_completion({**worker, "lost_lease_receipt_id": ""}) is False
    assert sup.worker_fence_proves_queue_completion({**worker, "lease_fenced_at": ""}) is False
    assert sup.worker_fence_proves_queue_completion({**worker, "status": "superseded"}) is True
    assert sup.worker_fence_proves_queue_completion(None) is False


def test_health_observation_survives_a_rejected_transition_in_the_same_cycle(tmp_path):
    """A read-only observation must not be discarded by an unrelated write.

    Regression for the fleet-wide stall: delivery-health observations were
    committed inside the maintenance phase, so every transition that phase could
    not prove also threw away that cycle's probe results. Evidence expires after
    delivery_health.evidence_ttl_seconds, so a sustained discard rate expired
    every lane and the dispatcher then refused every lane for
    HEALTH_REFRESH_REQUIRED -- with no worker left to change the state that
    would have ended it.
    """
    root = tmp_path / "status"
    (root / ".orchestrator").mkdir(parents=True)
    config = config_fixture(root)
    config["paths"]["approval_queue"] = str(root / ".orchestrator" / "approvals.json")
    config["task_state_store"] = {"mode": "authoritative", "event_log": str(tmp_path / "tasks.jsonl")}
    _non_terminal_canonical(config, tmp_path)

    # A worker that can never prove its own queue completion: still alive, so
    # the fence proof is refused too. Any phase that completes its queue record
    # is therefore rejected.
    worker, proc = _fenced_worker("TASK-9", "run-9", "event-9", alive=True)
    worker["status"] = "running"
    runtime = runtime_state.default_state()
    runtime["workers"][worker["run_id"]] = worker
    runtime["queue"]["events"][worker["queue_event_id"]] = {
        "status": "failed",
        "intent": {"event_id": worker["queue_event_id"], "task_id": worker["task_id"]},
    }
    runtime_state.save_runtime_state(config, runtime)

    def rejected_transition(scratch):
        scratch["queue"]["events"][worker["queue_event_id"]]["status"] = "completed"
        return True

    observations = [{
        "endpoint_id": "lane-9",
        "account_id": "acct-9",
        "probe": {"provider": "lane-9", "ready": True, "status": "ready",
                  "source": "live", "checked_at": "2026-09-17T12:00:00Z"},
    }]
    try:
        # Exactly what the cycle calls, in the same order.
        health_committed = sup.commit_delivery_health_observations(config, observations)
        transition_committed = sup._run_reserved_runtime_phase(
            config, "post_dispatch_maintenance", rejected_transition)
    finally:
        if proc is not None:
            proc.communicate(timeout=5)

    final = runtime_state.load_runtime_state(config)
    assert health_committed is True, "the observation phase has no transition to reject"
    assert transition_committed is False, "the unprovable queue completion must still be refused"
    landed = final.get("delivery_health", {}).get("endpoints", {}).get("lane-9") or {}
    assert landed.get("state") == "healthy", "the observation must survive the rejection"
    assert final["queue"]["events"]["event-9"]["status"] == "failed", "the refused transition must not land"


def test_sustained_reserved_phase_discards_escalate_beyond_one_log_line(tmp_path):
    """A phase that loses its CAS every cycle is a stall, not contention."""
    phase = "phase-under-test"
    sup._PHASE_DISCARD_STREAKS.pop(phase, None)
    try:
        streaks = [
            sup.record_reserved_phase_outcome(phase, committed=False) for _ in range(4)
        ]
        assert streaks == [1, 2, 3, 4]
        assert sup.SUSTAINED_PHASE_DISCARD_THRESHOLD <= streaks[-1]
        # One commit clears it, so ordinary contention never escalates.
        assert sup.record_reserved_phase_outcome(phase, committed=True) == 0
        assert sup.record_reserved_phase_outcome(phase, committed=False) == 1
    finally:
        sup._PHASE_DISCARD_STREAKS.pop(phase, None)
