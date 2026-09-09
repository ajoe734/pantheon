"""Behavior tests for JsonGovernanceRecordStore multi-instance and process isolation.

Closes F09: verifies that multiple independent store instances and processes
coordinate around POSIX file locking, fresh read-modify-write, atomic replacement,
CAS version conflicts, and error recovery without lost updates or corrupted state.
Also verifies the four actual mounted consumers: freeze orders, rollbacks,
human gate decisions, and consultation handoffs.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from services.governance import main
from services.governance.human_gate.decision_model import HumanGateDecision
from services.governance.human_gate_store import GovernanceHumanGateDecisionStore
from services.governance.promotion_readiness.signoff_api import SignoffAPI, SignoffApiError
from services.governance.record_store import JsonGovernanceRecordStore


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
