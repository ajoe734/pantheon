"""Behavior tests for JsonGovernanceRecordStore multi-instance and process isolation.

Closes F09: verifies that multiple independent store instances and processes
coordinate around POSIX file locking, fresh read-modify-write, atomic replacement,
CAS version conflicts, and error recovery without lost updates or corrupted state.
Also verifies the four actual mounted consumers: freeze orders, rollbacks,
human gate decisions, and consultation handoffs.
"""
from __future__ import annotations

import fcntl
import json
import multiprocessing as mp
import os
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.governance import main
from services.governance.human_gate.decision_model import HumanGateDecision
from services.governance.human_gate_store import GovernanceHumanGateDecisionStore
from services.governance.promotion_readiness.signoff_api import SignoffAPI, SignoffApiError
from services.governance.record_store import JsonGovernanceRecordStore
from services.runtime_auth_inbound import encode_jwt_hs256


# ---------------------------------------------------------------------------
# Multiprocessing Worker Functions (must be top-level for pickling)
# ---------------------------------------------------------------------------

def _mp_writer_worker(
    path_str: str,
    worker_id: int,
    record_count: int,
    barrier: Any,
    error_queue: Any,
) -> None:
    try:
        store = JsonGovernanceRecordStore(path_str, id_fields=("id",))
        barrier.wait(timeout=10)
        for i in range(record_count):
            rec_id = f"worker-{worker_id}-rec-{i}"
            store.put({"id": rec_id, "worker": worker_id, "index": i, "payload": f"data-{worker_id}-{i}"})
    except Exception as exc:
        error_queue.put(f"Worker {worker_id} error: {exc}")


def _mp_insert_if_absent_worker(
    path_str: str,
    worker_id: int,
    shared_id: str,
    barrier: Any,
    result_queue: Any,
) -> None:
    try:
        store = JsonGovernanceRecordStore(path_str, id_fields=("id",))
        barrier.wait(timeout=10)
        inserted, canonical = store.insert_if_absent(
            {"id": shared_id, "winner": worker_id, "worker_tag": f"worker_{worker_id}"}
        )
        result_queue.put({"worker_id": worker_id, "inserted": inserted, "canonical": canonical})
    except Exception as exc:
        result_queue.put({"worker_id": worker_id, "error": str(exc)})


def _mp_consumer_freeze_worker(
    path_str: str,
    worker_id: int,
    barrier: Any,
    error_queue: Any,
) -> None:
    try:
        store = JsonGovernanceRecordStore(path_str, id_fields=("freeze_order_id", "id"))
        barrier.wait(timeout=10)
        order_id = f"freeze-mp-{worker_id}"
        store.put(
            {
                "freeze_order_id": order_id,
                "scope": "persona",
                "target_id": f"persona-{worker_id}",
                "status": "active",
                "actor": "admin",
                "source_command_id": f"cmd-freeze-{worker_id}",
            }
        )
    except Exception as exc:
        error_queue.put(f"Freeze worker {worker_id} error: {exc}")


def _mp_consumer_rollback_worker(
    path_str: str,
    worker_id: int,
    barrier: Any,
    error_queue: Any,
) -> None:
    try:
        store = JsonGovernanceRecordStore(path_str, id_fields=("rollback_id", "id"))
        barrier.wait(timeout=10)
        rb_id = f"rollback-mp-{worker_id}"
        store.put(
            {
                "rollback_id": rb_id,
                "runtime_id": f"runtime-{worker_id}",
                "action_type": "replace",
                "status": "initiated",
                "actor": "operator",
                "source_command_id": f"cmd-rb-{worker_id}",
            }
        )
    except Exception as exc:
        error_queue.put(f"Rollback worker {worker_id} error: {exc}")


def _jwt_headers(actor_id: str, role: str, *, mfa: bool = False) -> str:
    claims = {"sub": actor_id, "roles": [role]}
    if mfa:
        claims["amr"] = ["pwd", "mfa"]
    token = encode_jwt_hs256(claims, secret="isolation-test-secret")
    return f"Bearer {token}"


def _mp_mounted_freeze_worker(
    path_str: str,
    worker_id: int,
    shared_freeze_id: str,
    barrier: Any,
    step_barrier: Any,
    post_read_event: Any,
    verify_done_event: Any,
    error_queue: Any,
) -> None:
    try:
        os.environ["PANTHEON_GOVERNANCE_AUTH_MODE"] = "strict"
        os.environ["PANTHEON_GOVERNANCE_JWT_SECRET"] = "isolation-test-secret"
        store = JsonGovernanceRecordStore(path_str, id_fields=("freeze_order_id", "id"))
        main.freeze_order_store = store

        # Phase 1: Independent distinct freeze orders
        barrier.wait(timeout=10)
        own_id = f"freeze-mounted-{worker_id}"
        resp = main.record_freeze_order(
            body={
                "freeze_order_id": own_id,
                "scope": "persona",
                "target_id": f"persona-mounted-{worker_id}",
                "status": "requested",
                "actor": "operator",
                "source_command_id": f"cmd-mount-req-{worker_id}",
            },
            authorization=_jwt_headers(f"op-{worker_id}", "operator"),
            x_mfa_token=None,
        )
        assert resp["status"] == "requested"

        # Phase 2: Controlled transitions on shared freeze order with command-wide lock verification
        barrier.wait(timeout=10)
        if worker_id == 0:
            res_init = main.record_freeze_order(
                body={
                    "freeze_order_id": shared_freeze_id,
                    "scope": "portfolio",
                    "target_id": "portfolio-main",
                    "status": "requested",
                    "actor": "operator",
                    "source_command_id": "cmd-shared-init",
                },
                authorization=_jwt_headers("op-0", "operator"),
                x_mfa_token=None,
            )
            assert res_init["status"] == "requested"

        barrier.wait(timeout=10)
        if worker_id == 1:
            orig_get = store.get

            def post_read_hook(record_id: str) -> Any:
                rec = orig_get(record_id)
                if record_id == shared_freeze_id:
                    post_read_event.set()
                    if not verify_done_event.wait(timeout=5):
                        error_queue.put("Worker 1 timed out waiting for verify_done_event")
                return rec

            store.get = post_read_hook
            res_act = main.record_freeze_order(
                body={
                    "freeze_order_id": shared_freeze_id,
                    "status": "active",
                    "actor": "governance_reviewer",
                    "source_command_id": "cmd-shared-act",
                    "transition_actor": "governance_reviewer",
                    "transition_source_command_id": "cmd-shared-act",
                },
                authorization=_jwt_headers("rev-1", "governance_reviewer"),
                x_mfa_token=None,
            )
            assert res_act["status"] == "active"
            step_barrier.wait(timeout=10)
        elif worker_id == 2:
            if not post_read_event.wait(timeout=5):
                error_queue.put("Worker 2 timed out waiting for post_read_event")
            else:
                flock_path = Path(path_str).with_name(f".{Path(path_str).name}.flock")
                fd = os.open(str(flock_path), os.O_CREAT | os.O_RDWR, 0o666)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    error_queue.put("Worker 2: command-wide lock was not held by worker 1 at post-read/pre-write boundary")
                except (BlockingIOError, OSError):
                    pass
                finally:
                    os.close(fd)
                verify_done_event.set()

            step_barrier.wait(timeout=10)
            res_rel = main.record_freeze_order(
                body={
                    "freeze_order_id": shared_freeze_id,
                    "status": "released",
                    "actor": "admin",
                    "source_command_id": "cmd-shared-rel",
                    "transition_actor": "admin",
                    "transition_source_command_id": "cmd-shared-rel",
                },
                authorization=_jwt_headers("admin-2", "admin"),
                x_mfa_token=None,
            )
            assert res_rel["status"] == "released"
        else:
            step_barrier.wait(timeout=10)

        barrier.wait(timeout=10)
        # All workers verify that transition from terminal state raises 400
        try:
            main.record_freeze_order(
                body={
                    "freeze_order_id": shared_freeze_id,
                    "status": "active",
                    "actor": "operator",
                    "source_command_id": f"cmd-terminal-reactivate-{worker_id}",
                    "transition_actor": "operator",
                    "transition_source_command_id": f"cmd-terminal-reactivate-{worker_id}",
                },
                authorization=_jwt_headers(f"op-{worker_id}", "operator"),
                x_mfa_token=None,
            )
            error_queue.put(f"Worker {worker_id} expected terminal transition to fail")
        except HTTPException as exc:
            assert exc.status_code == 400
            assert "Cannot transition from terminal freeze order status" in str(exc.detail)
    except Exception as exc:
        error_queue.put(f"Mounted freeze worker {worker_id} error: {exc}")


def _mp_mounted_rollback_worker(
    path_str: str,
    worker_id: int,
    shared_rb_id: str,
    barrier: Any,
    step_barrier: Any,
    post_read_event: Any,
    verify_done_event: Any,
    error_queue: Any,
) -> None:
    try:
        os.environ["PANTHEON_GOVERNANCE_AUTH_MODE"] = "strict"
        os.environ["PANTHEON_GOVERNANCE_JWT_SECRET"] = "isolation-test-secret"
        store = JsonGovernanceRecordStore(path_str, id_fields=("rollback_id", "id"))
        main.rollback_store = store

        # Phase 1: Independent distinct rollback records
        barrier.wait(timeout=10)
        own_id = f"rollback-mounted-{worker_id}"
        resp = main.record_rollback(
            body={
                "rollback_id": own_id,
                "runtime_id": f"runtime-mounted-{worker_id}",
                "action_type": "replace",
                "status": "initiated",
                "actor": "operator",
                "source_command_id": f"cmd-rb-mount-init-{worker_id}",
            },
            authorization=_jwt_headers(f"op-{worker_id}", "operator"),
            x_mfa_token=None,
        )
        assert resp["status"] == "initiated"

        # Phase 2: Controlled transitions on shared rollback record with command-wide lock verification
        barrier.wait(timeout=10)
        if worker_id == 0:
            res_init = main.record_rollback(
                body={
                    "rollback_id": shared_rb_id,
                    "runtime_id": "runtime-shared-rt",
                    "action_type": "replace",
                    "status": "initiated",
                    "actor": "operator",
                    "source_command_id": "cmd-shared-rb-init",
                },
                authorization=_jwt_headers("op-0", "operator"),
                x_mfa_token=None,
            )
            assert res_init["status"] == "initiated"

        barrier.wait(timeout=10)
        if worker_id == 1:
            orig_get = store.get

            def post_read_hook(record_id: str) -> Any:
                rec = orig_get(record_id)
                if record_id == shared_rb_id:
                    post_read_event.set()
                    if not verify_done_event.wait(timeout=5):
                        error_queue.put("Worker 1 timed out waiting for verify_done_event")
                return rec

            store.get = post_read_hook
            res_appr = main.record_rollback(
                body={
                    "rollback_id": shared_rb_id,
                    "status": "approved",
                    "actor": "approver",
                    "source_command_id": "cmd-shared-rb-appr",
                    "transition_actor": "approver",
                    "transition_source_command_id": "cmd-shared-rb-appr",
                },
                authorization=_jwt_headers("appr-1", "approver"),
                x_mfa_token=None,
            )
            assert res_appr["status"] == "approved"
            step_barrier.wait(timeout=10)
        elif worker_id == 2:
            if not post_read_event.wait(timeout=5):
                error_queue.put("Worker 2 timed out waiting for post_read_event")
            else:
                flock_path = Path(path_str).with_name(f".{Path(path_str).name}.flock")
                fd = os.open(str(flock_path), os.O_CREAT | os.O_RDWR, 0o666)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    error_queue.put("Worker 2: command-wide lock was not held by worker 1 at post-read/pre-write boundary")
                except (BlockingIOError, OSError):
                    pass
                finally:
                    os.close(fd)
                verify_done_event.set()

            step_barrier.wait(timeout=10)
            res_comp = main.record_rollback(
                body={
                    "rollback_id": shared_rb_id,
                    "status": "completed",
                    "actor": "operator",
                    "source_command_id": "cmd-shared-rb-comp",
                    "transition_actor": "operator",
                    "transition_source_command_id": "cmd-shared-rb-comp",
                },
                authorization=_jwt_headers("op-2", "operator"),
                x_mfa_token=None,
            )
            assert res_comp["status"] == "completed"
        else:
            step_barrier.wait(timeout=10)

        barrier.wait(timeout=10)
        # All workers verify that transition from terminal status 'completed' raises 400
        try:
            main.record_rollback(
                body={
                    "rollback_id": shared_rb_id,
                    "status": "approved",
                    "actor": "operator",
                    "source_command_id": f"cmd-terminal-rb-mod-{worker_id}",
                    "transition_actor": "operator",
                    "transition_source_command_id": f"cmd-terminal-rb-mod-{worker_id}",
                },
                authorization=_jwt_headers(f"op-{worker_id}", "operator"),
                x_mfa_token=None,
            )
            error_queue.put(f"Worker {worker_id} expected terminal rollback transition to fail")
        except HTTPException as exc:
            assert exc.status_code == 400
            assert "Cannot transition from terminal rollback status" in str(exc.detail)
    except Exception as exc:
        error_queue.put(f"Mounted rollback worker {worker_id} error: {exc}")


def _mp_mounted_human_gate_worker(
    path_str: str,
    worker_id: int,
    shared_decision_id: str,
    barrier: Any,
    shared_snapshot_ready: Any,
    worker_0_committed: Any,
    result_queue: Any,
) -> None:
    try:
        os.environ["PANTHEON_GOVERNANCE_AUTH_MODE"] = "strict"
        os.environ["PANTHEON_GOVERNANCE_JWT_SECRET"] = "isolation-test-secret"
        store = JsonGovernanceRecordStore(path_str, id_fields=("decision_id",))
        api = SignoffAPI(store=GovernanceHumanGateDecisionStore(store))
        main.human_gate_record_store = store
        main.human_gate_api = api

        evidence_keys = sorted(main._PROMOTION_HUMAN_GATE_EVIDENCE["canary"])
        payload = {
            "decision_id": shared_decision_id,
            "target_type": "runtime_binding_promotion",
            "target_id": "plan-canary-mp",
            "target_environment": "dev",
            "required_roles": ["approver", "operator", "risk_owner"],
            "evidence_reviewed": [
                {
                    "key": k,
                    "evidence_hash": "sha256:" + f"{idx:064x}",
                    "source_ref": f"evidence://{k}",
                    "status": "passed",
                }
                for idx, k in enumerate(evidence_keys, start=1)
            ],
            "can_proceed_input": {
                "readiness_packet_ref": "packet://mp-canary",
                "readiness_packet_can_proceed": True,
                "required_evidence": evidence_keys,
                "missing_evidence": [],
                "blocking_reasons": [],
                "unsafe_true_flags": [],
                "gate_results_blocking": [],
            },
            "metadata": {"target_stage": "canary", "source_binding_id": "rb-mp-001"},
        }

        # Step 1: Duplicate creation under barrier
        barrier.wait(timeout=10)
        create_status = None
        try:
            main.create_human_gate(
                body=payload,
                authorization=_jwt_headers(f"creator-{worker_id}", "approver"),
                x_mfa_token=None,
            )
            create_status = 201
        except HTTPException as exc:
            create_status = exc.status_code

        # Step 2: Concurrent signatures with CAS conflict handling
        # Force independent mounted signing handlers to share the expected snapshot
        barrier.wait(timeout=10)
        cas_conflict_observed = False
        sign_success = False

        if worker_id == 1:
            orig_require = api.store.require
            stale_active = [True]
            stale_snapshot = None

            def require_shared(dec_id: str) -> Any:
                nonlocal stale_snapshot
                if stale_active[0]:
                    if stale_snapshot is None:
                        stale_snapshot = orig_require(dec_id)
                        shared_snapshot_ready.set()
                        if not worker_0_committed.wait(timeout=10):
                            raise RuntimeError("Worker 1 timed out waiting for worker 0 to commit")
                    return stale_snapshot
                return orig_require(dec_id)

            api.store.require = require_shared

            try:
                main.sign_human_gate(
                    decision_id=shared_decision_id,
                    body={"role": "operator"},
                    authorization=_jwt_headers("signer-1", "operator", mfa=True),
                    x_mfa_token=None,
                )
            except HTTPException as exc:
                if exc.status_code == 409 and "concurrently" in str(exc.detail):
                    cas_conflict_observed = True
                else:
                    raise
            finally:
                stale_active[0] = False

            if cas_conflict_observed:
                # Explicit retry from fresh state without sleep synchronization
                main.sign_human_gate(
                    decision_id=shared_decision_id,
                    body={"role": "operator"},
                    authorization=_jwt_headers("signer-1", "operator", mfa=True),
                    x_mfa_token=None,
                )
                sign_success = True
        elif worker_id == 0:
            if not shared_snapshot_ready.wait(timeout=10):
                raise RuntimeError("Worker 0 timed out waiting for shared snapshot capture")
            main.sign_human_gate(
                decision_id=shared_decision_id,
                body={"role": "approver"},
                authorization=_jwt_headers("signer-0", "approver", mfa=True),
                x_mfa_token=None,
            )
            sign_success = True
            worker_0_committed.set()

        # Step 3: Duplicate signature by same actor -> 409
        barrier.wait(timeout=10)
        dup_rejected = False
        if worker_id == 0:
            try:
                main.sign_human_gate(
                    decision_id=shared_decision_id,
                    body={"role": "approver"},
                    authorization=_jwt_headers("signer-0", "approver", mfa=True),
                    x_mfa_token=None,
                )
            except HTTPException as exc:
                if exc.status_code == 409 and "one authenticated actor" in str(exc.detail):
                    dup_rejected = True

        result_queue.put({
            "worker_id": worker_id,
            "create_status": create_status,
            "cas_conflict_observed": cas_conflict_observed,
            "sign_success": sign_success,
            "dup_rejected": dup_rejected,
        })
    except Exception as exc:
        result_queue.put({"worker_id": worker_id, "error": str(exc)})


# ---------------------------------------------------------------------------
# Core Isolation Tests
# ---------------------------------------------------------------------------

def test_two_instance_lost_update_reproduced_and_prevented(tmp_path: Path) -> None:
    """Original F09 repro: two instances writing distinct records must both survive.

    In the legacy store, store_b's save would overwrite store_a's write because
    store_b only loaded once at __init__ and used an in-process lock.
    """
    store_file = tmp_path / "shared_records.json"

    # Instantiate two separate instances before either writes
    store_a = JsonGovernanceRecordStore(store_file, id_fields=("id",))
    store_b = JsonGovernanceRecordStore(store_file, id_fields=("id",))

    # Store A writes record A
    store_a.put({"id": "rec-A", "data": "alpha", "seq": 1})

    # Store B writes record B without re-instantiation
    store_b.put({"id": "rec-B", "data": "beta", "seq": 2})

    # A fresh third instance reads the durable state from disk
    store_c = JsonGovernanceRecordStore(store_file, id_fields=("id",))
    all_records = store_c.list_all()

    record_map = {r["id"]: r for r in all_records}
    assert "rec-A" in record_map, "rec-A was lost to store_b's write (F09 regression!)"
    assert "rec-B" in record_map, "rec-B was not persisted"
    assert record_map["rec-A"]["data"] == "alpha"
    assert record_map["rec-B"]["data"] == "beta"

    # Interleaved writes between existing instances
    store_a.put({"id": "rec-C", "data": "gamma", "seq": 3})
    store_b.put({"id": "rec-D", "data": "delta", "seq": 4})

    fresh_map = {r["id"]: r for r in store_c.list_all()}
    assert set(fresh_map.keys()) == {"rec-A", "rec-B", "rec-C", "rec-D"}


def test_independent_processes_writing_distinct_records(tmp_path: Path) -> None:
    """Multiple independent OS processes writing distinct records concurrently."""
    store_file = tmp_path / "process_concurrent.json"
    process_count = 4
    records_per_process = 8
    total_expected = process_count * records_per_process

    barrier = mp.Barrier(process_count)
    error_queue = mp.Queue()

    processes = []
    for wid in range(process_count):
        p = mp.Process(
            target=_mp_writer_worker,
            args=(str(store_file), wid, records_per_process, barrier, error_queue),
        )
        processes.append(p)
        p.start()

    for p in processes:
        p.join(timeout=15)
        assert p.exitcode == 0, f"Process {p} failed with exitcode {p.exitcode}"

    errors = []
    while not error_queue.empty():
        errors.append(error_queue.get())
    assert not errors, f"Subprocess errors encountered: {errors}"

    verifier = JsonGovernanceRecordStore(store_file, id_fields=("id",))
    all_records = verifier.list_all()
    assert len(all_records) == total_expected, (
        f"Expected {total_expected} records, found {len(all_records)}; "
        f"lost updates detected across processes"
    )

    ids = {r["id"] for r in all_records}
    for wid in range(process_count):
        for i in range(records_per_process):
            expected_id = f"worker-{wid}-rec-{i}"
            assert expected_id in ids, f"Missing record {expected_id}"


def test_multiprocess_insert_if_absent_duplicate_conflict(tmp_path: Path) -> None:
    """Multiple processes racing insert_if_absent on the same record ID."""
    store_file = tmp_path / "insert_if_absent_race.json"
    shared_id = "shared-race-key"
    process_count = 4

    barrier = mp.Barrier(process_count)
    result_queue = mp.Queue()

    processes = []
    for wid in range(process_count):
        p = mp.Process(
            target=_mp_insert_if_absent_worker,
            args=(str(store_file), wid, shared_id, barrier, result_queue),
        )
        processes.append(p)
        p.start()

    for p in processes:
        p.join(timeout=15)
        assert p.exitcode == 0

    results = []
    while not result_queue.empty():
        results.append(result_queue.get())

    assert len(results) == process_count
    for r in results:
        assert "error" not in r, f"Subprocess error: {r.get('error')}"

    inserted_results = [r for r in results if r["inserted"] is True]
    rejected_results = [r for r in results if r["inserted"] is False]

    assert len(inserted_results) == 1, (
        f"Exactly one process must successfully insert; got {len(inserted_results)}"
    )
    assert len(rejected_results) == process_count - 1

    winning_record = inserted_results[0]["canonical"]
    assert winning_record["id"] == shared_id

    # Every losing process must have received the winner's exact canonical record
    for rejected in rejected_results:
        assert rejected["canonical"] == winning_record


def test_compare_and_set_version_conflict_concurrency(tmp_path: Path) -> None:
    """CAS accurately detects version conflicts between independent store instances."""
    store_file = tmp_path / "cas_conflict.json"
    store_a = JsonGovernanceRecordStore(store_file, id_fields=("id",))
    store_b = JsonGovernanceRecordStore(store_file, id_fields=("id",))

    initial = {"id": "target-doc", "version": 1, "payload": "initial"}
    store_a.put(initial)

    # Both instances observe version 1
    doc_a = store_a.get("target-doc")
    doc_b = store_b.get("target-doc")
    assert doc_a == initial
    assert doc_b == initial

    # Store A succeeds in CAS transition 1 -> 2
    update_a = {"id": "target-doc", "version": 2, "payload": "from-a"}
    success_a, result_a = store_a.compare_and_set(initial, update_a)
    assert success_a is True
    assert result_a == update_a

    # Store B attempts CAS transition 1 -> 2 with stale expected snapshot (fails!)
    update_b = {"id": "target-doc", "version": 2, "payload": "from-b"}
    success_b, result_b = store_b.compare_and_set(initial, update_b)
    assert success_b is False, "CAS should have rejected stale expected snapshot"
    assert result_b == update_a, "CAS failure must return the actual current record"

    # Store B refreshes and now does CAS transition 2 -> 3 with fresh expected snapshot
    update_b_v3 = {"id": "target-doc", "version": 3, "payload": "from-b"}
    success_b2, result_b2 = store_b.compare_and_set(result_b, update_b_v3)
    assert success_b2 is True
    assert result_b2 == update_b_v3

    # Store A immediately observes Store B's update on next get
    assert store_a.get("target-doc")["version"] == 3


def test_fresh_reads_across_independent_instances(tmp_path: Path) -> None:
    """Writes to one instance are immediately visible to another without re-init."""
    store_file = tmp_path / "fresh_reads.json"
    writer = JsonGovernanceRecordStore(store_file, id_fields=("id",))
    reader = JsonGovernanceRecordStore(store_file, id_fields=("id",))

    assert reader.get("dyn-1") is None
    assert reader.list_all() == []

    writer.put({"id": "dyn-1", "val": "first"})
    assert reader.get("dyn-1") == {"id": "dyn-1", "val": "first"}
    assert len(reader.list_all()) == 1

    writer.put({"id": "dyn-1", "val": "second"})
    assert reader.get("dyn-1") == {"id": "dyn-1", "val": "second"}

    writer.put({"id": "dyn-2", "val": "other"})
    all_recs = reader.list_all()
    assert len(all_recs) == 2
    assert {r["id"] for r in all_recs} == {"dyn-1", "dyn-2"}


def test_failed_write_does_not_corrupt_store_or_acknowledge_uncommitted(
    tmp_path: Path,
) -> None:
    """Errors during write rollback in-memory state and leave on-disk file untouched."""
    store_file = tmp_path / "failed_write.json"
    store = JsonGovernanceRecordStore(store_file, id_fields=("id",))

    store.put({"id": "safe-1", "val": "committed-1"})
    assert store.get("safe-1")["val"] == "committed-1"

    # 1. Un-serializable object payload
    class Unserializable:
        pass

    with pytest.raises(TypeError):
        store.put({"id": "bad-payload", "val": Unserializable()})

    assert store.get("bad-payload") is None
    assert store.get("safe-1")["val"] == "committed-1"

    # 2. Simulated disk failure during save
    with patch("os.replace", side_effect=OSError("Disk full or simulated failure")):
        with pytest.raises(OSError):
            store.put({"id": "safe-1", "val": "uncommitted-update"})

    # Memory and disk must retain the prior committed value
    assert store.get("safe-1")["val"] == "committed-1"

    restarted_store = JsonGovernanceRecordStore(store_file, id_fields=("id",))
    assert restarted_store.get("safe-1")["val"] == "committed-1"
    assert restarted_store.get("bad-payload") is None


def test_atomic_save_fsyncs_file_and_directory(tmp_path: Path) -> None:
    """Acknowledged writes fsync both the temporary file and parent directory."""
    import stat

    store_file = tmp_path / "fsync_test.json"
    store = JsonGovernanceRecordStore(store_file, id_fields=("id",))

    fsync_targets = []
    real_fsync = os.fsync

    def capture_fsync(fd: int) -> None:
        fsync_targets.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_fsync(fd)

    os.fsync = capture_fsync
    try:
        store.put({"id": "f-1", "val": "durability-checked"})
    finally:
        os.fsync = real_fsync

    assert fsync_targets == ["file", "directory"]
    assert store.get("f-1") == {"id": "f-1", "val": "durability-checked"}


def test_coordinating_journal_store_subclass_does_not_deadlock(tmp_path: Path) -> None:
    """Pass-through subclasses of CoordinatingJsonGovernanceRecordStore do not deadlock."""
    from services.governance.decision_journal import CoordinatingJsonGovernanceRecordStore

    class DerivedJournalStore(CoordinatingJsonGovernanceRecordStore):
        pass

    journal_path = tmp_path / "journal.json"
    store = DerivedJournalStore(journal_path, id_fields=("id",))
    store.put({"id": "d-1", "decision": "approved"})

    assert store.get("d-1") == {"id": "d-1", "decision": "approved"}
    assert len(store.list_all()) == 1
    inserted, canonical = store.insert_if_absent({"id": "d-1", "decision": "conflict"})
    assert not inserted
    assert canonical["decision"] == "approved"


def test_reentrant_nested_instances_preserve_inner_writes(tmp_path: Path) -> None:
    """Closes P2: re-entrant operations through independent same-file instances preserve inner writes."""
    path = tmp_path / "records.json"
    a = JsonGovernanceRecordStore(path, id_fields=("id",))
    b = JsonGovernanceRecordStore(path, id_fields=("id",))
    a.put({"id": "seed"})

    with a.lock():
        b.put({"id": "inner-committed"})
        assert b.get("inner-committed") == {"id": "inner-committed"}
        a.put({"id": "outer-committed"})

    final = JsonGovernanceRecordStore(path, id_fields=("id",)).list_all()
    record_ids = {row["id"] for row in final}
    assert "inner-committed" in record_ids
    assert "outer-committed" in record_ids
    assert "seed" in record_ids
    assert len(final) == 3


# ---------------------------------------------------------------------------
# Mounted Consumer Isolation Tests
# ---------------------------------------------------------------------------

def test_mounted_consumer_freeze_orders_multiprocess(tmp_path: Path) -> None:
    """Mounted freeze_orders consumer coordinates multiple writing processes."""
    store_file = tmp_path / "freeze_orders.json"
    process_count = 3
    barrier = mp.Barrier(process_count)
    error_queue = mp.Queue()

    processes = []
    for wid in range(process_count):
        p = mp.Process(
            target=_mp_consumer_freeze_worker,
            args=(str(store_file), wid, barrier, error_queue),
        )
        processes.append(p)
        p.start()

    for p in processes:
        p.join(timeout=15)
        assert p.exitcode == 0

    errors = []
    while not error_queue.empty():
        errors.append(error_queue.get())
    assert not errors

    reader = JsonGovernanceRecordStore(
        store_file, id_fields=("freeze_order_id", "id")
    )
    records = reader.list_all()
    assert len(records) == process_count
    order_ids = {r["freeze_order_id"] for r in records}
    assert order_ids == {f"freeze-mp-{wid}" for wid in range(process_count)}


def test_mounted_consumer_rollbacks_multiprocess(tmp_path: Path) -> None:
    """Mounted rollbacks consumer coordinates multiple writing processes."""
    store_file = tmp_path / "rollbacks.json"
    process_count = 3
    barrier = mp.Barrier(process_count)
    error_queue = mp.Queue()

    processes = []
    for wid in range(process_count):
        p = mp.Process(
            target=_mp_consumer_rollback_worker,
            args=(str(store_file), wid, barrier, error_queue),
        )
        processes.append(p)
        p.start()

    for p in processes:
        p.join(timeout=15)
        assert p.exitcode == 0

    errors = []
    while not error_queue.empty():
        errors.append(error_queue.get())
    assert not errors

    reader = JsonGovernanceRecordStore(
        store_file, id_fields=("rollback_id", "id")
    )
    records = reader.list_all()
    assert len(records) == process_count
    rb_ids = {r["rollback_id"] for r in records}
    assert rb_ids == {f"rollback-mp-{wid}" for wid in range(process_count)}


def test_mounted_consumer_human_gates_multi_instance(tmp_path: Path) -> None:
    """Mounted human_gate consumer coordinates create and CAS signatures across instances."""
    store_file = tmp_path / "human_gates.json"
    records_1 = JsonGovernanceRecordStore(store_file, id_fields=("decision_id",))
    records_2 = JsonGovernanceRecordStore(store_file, id_fields=("decision_id",))

    api_1 = SignoffAPI(store=GovernanceHumanGateDecisionStore(records_1))
    api_2 = SignoffAPI(store=GovernanceHumanGateDecisionStore(records_2))

    payload = {
        "decision_id": "gate-multi-001",
        "target_type": "runtime_binding_promotion",
        "target_id": "plan-test",
        "target_environment": "dev",
        "required_roles": ["approver", "operator"],
        "evidence_reviewed": [
            {
                "key": "eval_metrics",
                "evidence_hash": "sha256:" + "0" * 64,
                "source_ref": "evidence://eval_metrics",
                "status": "passed",
            }
        ],
        "can_proceed_input": {"can_proceed": True},
        "metadata": {"target_stage": "canary"},
    }

    # Instance 1 creates decision
    api_1.create_decision(payload)

    # Instance 2 creates duplicate -> SignoffApiError
    with pytest.raises(SignoffApiError) as exc_info:
        api_2.create_decision(payload)
    assert "already exists" in str(exc_info.value)

    # Instance 2 immediately reads fresh record
    fresh_2 = api_2.read_decision("gate-multi-001")
    assert fresh_2.decision_id == "gate-multi-001"

    # Instance 1 signs
    signed_1 = api_1.append_signature("gate-multi-001", {"role": "approver", "actor_id": "actor-1"})
    assert len(signed_1.signatures) == 1

    # Instance 2 directly calling adapter_2.put_if_matches with stale fresh_2 detects race and fails closed
    with pytest.raises(SignoffApiError) as cas_exc:
        api_2.store.put_if_matches(
            fresh_2,
            fresh_2.with_signature(
                signed_1.signatures[0]
            ),
        )
    assert "human gate changed concurrently" in str(cas_exc.value)

    # Instance 2 reading latest sees instance 1's signature and can append its own signature
    signed_2 = api_2.append_signature("gate-multi-001", {"role": "operator", "actor_id": "actor-2"})
    assert len(signed_2.signatures) == 2


def test_mounted_consumer_consultation_handoff_multi_instance(
    tmp_path: Path, monkeypatch
) -> None:
    """Mounted consultation_handoff consumer preserves duplicate idempotency across instances."""
    store_file = tmp_path / "handoffs.json"
    store_1 = JsonGovernanceRecordStore(store_file, id_fields=("handoff_id",))
    store_2 = JsonGovernanceRecordStore(store_file, id_fields=("handoff_id",))

    monkeypatch.setattr(main, "consultation_handoff_store", store_1)
    monkeypatch.setenv("CONSULTATION_HANDOFF_TOKEN", "test-token")
    monkeypatch.setenv(
        "CONSULTATION_HANDOFF_ALLOWED_SERVICE_ACTOR",
        "consultation-workflow-executor",
    )
    monkeypatch.setenv("CONSULTATION_HANDOFF_ALLOWED_TENANTS", "tenant-alpha")

    client = TestClient(main.app)
    headers = {
        "Idempotency-Key": "consultation-handoff:tenant-alpha:h-100",
        "X-Pantheon-Service-Actor": "consultation-workflow-executor",
        "X-Pantheon-Tenant-Id": "tenant-alpha",
        "Authorization": "Bearer test-token",
    }
    payload = {
        "tenant_id": "tenant-alpha",
        "request_id": "req-100",
        "handoff": {
            "handoff_id": "h-100",
            "request_id": "req-100",
            "target_gate": "consultation.committee.risk.reviewed",
            "memo_ids": ["memo-1"],
            "evidence_refs": ["ev-1"],
            "audit_refs": ["aud-1"],
            "trace_id": "tr-1",
        },
    }

    # First request creates via store_1
    res1 = client.post("/api/governance/consultation-handoffs", json=payload, headers=headers)
    assert res1.status_code == 201
    assert res1.json()["acknowledged"] is True
    assert res1.json()["idempotent"] is False

    # Switch mounted store to store_2 (simulating independent process instance)
    monkeypatch.setattr(main, "consultation_handoff_store", store_2)

    # Replay request receives idempotent 200 via store_2
    res2 = client.post("/api/governance/consultation-handoffs", json=payload, headers=headers)
    assert res2.status_code == 200
    assert res2.json()["acknowledged"] is True
    assert res2.json()["idempotent"] is True


def test_mounted_freeze_orders_command_isolation_multiprocess(tmp_path: Path) -> None:
    """Mounted freeze_orders endpoint exercises command locks, legal transitions, terminal rejection, and audit preservation across processes."""
    store_file = tmp_path / "mounted_freeze_orders.json"
    process_count = 3
    barrier = mp.Barrier(process_count)
    step_barrier = mp.Barrier(process_count)
    post_read_event = mp.Event()
    verify_done_event = mp.Event()
    error_queue = mp.Queue()
    shared_freeze_id = "freeze-mounted-shared-001"

    processes = []
    for wid in range(process_count):
        p = mp.Process(
            target=_mp_mounted_freeze_worker,
            args=(
                str(store_file),
                wid,
                shared_freeze_id,
                barrier,
                step_barrier,
                post_read_event,
                verify_done_event,
                error_queue,
            ),
        )
        processes.append(p)
        p.start()

    for p in processes:
        p.join(timeout=20)
        assert p.exitcode == 0

    errors = []
    while not error_queue.empty():
        errors.append(error_queue.get())
    assert not errors, f"Mounted freeze workers had errors: {errors}"

    reader = JsonGovernanceRecordStore(
        store_file, id_fields=("freeze_order_id", "id")
    )
    records = reader.list_all()
    # 3 distinct orders + 1 shared order
    assert len(records) == process_count + 1

    # Verify shared order terminal status and retained audit fields
    shared = reader.get(shared_freeze_id)
    assert shared is not None
    assert shared["status"] == "released"
    assert shared["scope"] == "portfolio"
    assert shared["target_id"] == "portfolio-main"
    assert shared["actor"] == "operator"
    assert shared["source_command_id"] == "cmd-shared-init"
    assert shared["transition_actor"] == "admin"
    assert shared["transition_source_command_id"] == "cmd-shared-rel"


def test_mounted_rollbacks_command_isolation_multiprocess(tmp_path: Path) -> None:
    """Mounted rollbacks endpoint exercises command locks, legal transitions, terminal rejection, and audit preservation across processes."""
    store_file = tmp_path / "mounted_rollbacks.json"
    process_count = 3
    barrier = mp.Barrier(process_count)
    step_barrier = mp.Barrier(process_count)
    post_read_event = mp.Event()
    verify_done_event = mp.Event()
    error_queue = mp.Queue()
    shared_rb_id = "rollback-mounted-shared-001"

    processes = []
    for wid in range(process_count):
        p = mp.Process(
            target=_mp_mounted_rollback_worker,
            args=(
                str(store_file),
                wid,
                shared_rb_id,
                barrier,
                step_barrier,
                post_read_event,
                verify_done_event,
                error_queue,
            ),
        )
        processes.append(p)
        p.start()

    for p in processes:
        p.join(timeout=20)
        assert p.exitcode == 0

    errors = []
    while not error_queue.empty():
        errors.append(error_queue.get())
    assert not errors, f"Mounted rollback workers had errors: {errors}"

    reader = JsonGovernanceRecordStore(
        store_file, id_fields=("rollback_id", "id")
    )
    records = reader.list_all()
    # 3 distinct rollbacks + 1 shared rollback
    assert len(records) == process_count + 1

    shared = reader.get(shared_rb_id)
    assert shared is not None
    assert shared["status"] == "completed"
    assert shared["runtime_id"] == "runtime-shared-rt"
    assert shared["action_type"] == "replace"
    assert shared["actor"] == "operator"
    assert shared["source_command_id"] == "cmd-shared-rb-init"
    assert shared["transition_actor"] == "operator"
    assert shared["transition_source_command_id"] == "cmd-shared-rb-comp"


def test_mounted_human_gates_command_isolation_multiprocess(tmp_path: Path) -> None:
    """Mounted human_gate endpoints prove duplicate conflict, concurrent CAS signatures, and duplicate actor rejection across processes."""
    store_file = tmp_path / "mounted_human_gates.json"
    barrier = mp.Barrier(2)
    shared_snapshot_ready = mp.Event()
    worker_0_committed = mp.Event()
    result_queue = mp.Queue()
    shared_decision_id = "hgd-mounted-mp-001"

    p0 = mp.Process(
        target=_mp_mounted_human_gate_worker,
        args=(
            str(store_file),
            0,
            shared_decision_id,
            barrier,
            shared_snapshot_ready,
            worker_0_committed,
            result_queue,
        ),
    )
    p1 = mp.Process(
        target=_mp_mounted_human_gate_worker,
        args=(
            str(store_file),
            1,
            shared_decision_id,
            barrier,
            shared_snapshot_ready,
            worker_0_committed,
            result_queue,
        ),
    )
    p0.start()
    p1.start()
    p0.join(timeout=20)
    p1.join(timeout=20)
    assert p0.exitcode == 0
    assert p1.exitcode == 0

    results = {}
    while not result_queue.empty():
        res = result_queue.get()
        assert "error" not in res, f"Worker error: {res}"
        results[res["worker_id"]] = res

    # Exactly one worker succeeded creation (201), the other received duplicate rejection (409)
    create_statuses = {results[0]["create_status"], results[1]["create_status"]}
    assert create_statuses == {201, 409}

    # Worker 1 observed 409 concurrent conflict on shared snapshot
    assert results[1]["cas_conflict_observed"] is True

    # Both workers succeeded signing (worker 1 after explicit retry from fresh state)
    assert results[0]["sign_success"] is True
    assert results[1]["sign_success"] is True

    # Duplicate signature by same actor was rejected with 409
    assert results[0]["dup_rejected"] is True

    # Durable reread from independent reader confirms both signatures survived
    reader = JsonGovernanceRecordStore(store_file, id_fields=("decision_id",))
    record = reader.get(shared_decision_id)
    assert record is not None
    signatures = record.get("signatures", [])
    assert len(signatures) == 2
    roles_signed = {s["role"] for s in signatures}
    assert roles_signed == {"approver", "operator"}
