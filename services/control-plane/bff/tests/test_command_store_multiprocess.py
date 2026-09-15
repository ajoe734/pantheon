"""Multiprocess, multi-instance, and concurrency tests for CommandStore.

Validates:
1. Re-entrant nested transactions do not deadlock.
2. Two CommandStore instances pointing to the same file see fresh data immediately (no stale cache).
3. Concurrent writes across separate OS processes are serialized by sidecar lock without corruption.
4. Concurrent submissions with same target/type detect active in-flight commands.
5. Idempotency key queries enforce operator and tenant isolation.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import tempfile
import time
from typing import Any, Dict

import pytest

from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import (
    CommandStatus,
    CommandType,
    ObjectType,
    TargetObject,
)


def _worker_submit_commands(file_path: str, worker_id: int, count: int) -> None:
    store = CommandStore(file_path=file_path)
    for i in range(count):
        cmd_id = f"cmd-proc-{worker_id}-{i}"
        target = TargetObject(type=ObjectType.STRATEGY, id=f"strat-{worker_id}-{i}")
        store.submit_command(
            command_id=cmd_id,
            command_type=CommandType.STRATEGY_ACTION,
            target=target,
            submitted_at="2026-09-14T12:00:00Z",
            params={"action": "test", "worker": worker_id, "index": i},
            audit_context={"actor": f"worker-{worker_id}", "tenant_id": f"tenant-{worker_id}"},
            foundation_context={
                "idempotency_record": {
                    "idempotency_key": f"key-{worker_id}-{i}",
                    "request_hash": f"hash-{worker_id}-{i}",
                }
            },
        )


def test_command_store_nested_transaction_reentrancy() -> None:
    """Verify nested serialized_transaction blocks do not deadlock."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = os.path.join(tmpdir, "test_commands.jsonl")
        store = CommandStore(file_path=file_path)

        with store.serialized_transaction():
            with store.serialized_transaction():
                with store.serialized_transaction():
                    store.submit_command(
                        command_id="cmd-nested-1",
                        command_type=CommandType.STRATEGY_ACTION,
                        target=TargetObject(type=ObjectType.STRATEGY, id="strat-1"),
                        submitted_at="2026-09-14T12:00:00Z",
                        params={},
                        audit_context={"actor": "tester"},
                    )

        cmd = store.get_command("cmd-nested-1")
        assert cmd is not None
        assert cmd["command_id"] == "cmd-nested-1"


def test_command_store_multi_instance_cache_coherence() -> None:
    """Verify instance B immediately sees writes made by instance A."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = os.path.join(tmpdir, "test_commands.jsonl")
        store_a = CommandStore(file_path=file_path)
        store_b = CommandStore(file_path=file_path)

        # Instance B reads empty list initially
        assert len(store_b._get_all_commands()) == 0

        # Instance A writes
        store_a.submit_command(
            command_id="cmd-a-1",
            command_type=CommandType.STRATEGY_ACTION,
            target=TargetObject(type=ObjectType.STRATEGY, id="strat-a"),
            submitted_at="2026-09-14T12:00:00Z",
            params={},
            audit_context={"actor": "tester"},
        )

        # Instance B immediately sees instance A's write without stale cache
        commands_b = store_b._get_all_commands()
        assert len(commands_b) == 1
        assert commands_b[0]["command_id"] == "cmd-a-1"

        # Instance B updates status
        store_b.update_status("cmd-a-1", CommandStatus.EXECUTED, result={"done": True})

        # Instance A immediately sees instance B's update
        cmd_a = store_a.get_command("cmd-a-1")
        assert cmd_a is not None
        assert cmd_a["status"] == CommandStatus.EXECUTED.value
        assert cmd_a["result"] == {"done": True}


def test_command_store_multiprocess_concurrency() -> None:
    """Verify concurrent writes across multiple distinct OS processes are serialized cleanly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = os.path.join(tmpdir, "test_commands.jsonl")
        store = CommandStore(file_path=file_path)

        num_workers = 4
        items_per_worker = 25
        total_expected = num_workers * items_per_worker

        ctx = mp.get_context("spawn")
        processes = []
        for w in range(num_workers):
            p = ctx.Process(
                target=_worker_submit_commands,
                args=(file_path, w, items_per_worker),
            )
            processes.append(p)
            p.start()

        for p in processes:
            p.join(timeout=30)
            assert p.exitcode == 0, f"Worker process failed with exit code {p.exitcode}"

        all_cmds = store._get_all_commands()
        assert len(all_cmds) == total_expected, f"Expected {total_expected} commands, got {len(all_cmds)}"

        # Verify all command IDs are unique and present
        cmd_ids = {c["command_id"] for c in all_cmds}
        assert len(cmd_ids) == total_expected


def test_command_store_active_target_concurrency() -> None:
    """Verify submit_command_if_no_active_target rejects concurrent conflicting targets."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = os.path.join(tmpdir, "test_commands.jsonl")
        store = CommandStore(file_path=file_path)
        target = TargetObject(type=ObjectType.CAPITAL_POOL, id="pool-1")

        rec1, active1 = store.submit_command_if_no_active_target(
            command_id="cmd-active-1",
            command_type=CommandType.CAPITAL_POOL_ACTION,
            target=target,
            submitted_at="2026-09-14T12:00:00Z",
            params={},
            audit_context={"actor": "tester"},
        )
        assert rec1 is not None
        assert active1 is None

        # Second attempt for same target while first is SUBMITTED must conflict
        rec2, active2 = store.submit_command_if_no_active_target(
            command_id="cmd-active-2",
            command_type=CommandType.CAPITAL_POOL_ACTION,
            target=target,
            submitted_at="2026-09-14T12:00:01Z",
            params={},
            audit_context={"actor": "tester"},
        )
        assert rec2 is None
        assert active2 is not None
        assert active2["command_id"] == "cmd-active-1"

        # Complete first command
        store.update_status("cmd-active-1", CommandStatus.EXECUTED)

        # Now third command should succeed
        rec3, active3 = store.submit_command_if_no_active_target(
            command_id="cmd-active-3",
            command_type=CommandType.CAPITAL_POOL_ACTION,
            target=target,
            submitted_at="2026-09-14T12:00:02Z",
            params={},
            audit_context={"actor": "tester"},
        )
        assert rec3 is not None
        assert active3 is None


def test_command_store_idempotency_operator_and_tenant_isolation() -> None:
    """Verify idempotency key lookup enforces operator and tenant matching."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = os.path.join(tmpdir, "test_commands.jsonl")
        store = CommandStore(file_path=file_path)

        store.submit_command(
            command_id="cmd-tenant-1",
            command_type=CommandType.STRATEGY_ACTION,
            target=TargetObject(type=ObjectType.STRATEGY, id="s-1"),
            submitted_at="2026-09-14T12:00:00Z",
            params={},
            audit_context={"actor": "op-alice", "tenant_id": "tenant-alpha"},
            foundation_context={
                "idempotency_record": {
                    "idempotency_key": "idem-key-100",
                    "request_hash": "hash-100",
                },
                "trace_context": {
                    "tenant_ref": {"tenant_id": "tenant-alpha"},
                },
            },
        )

        # Matching operator and matching tenant -> found
        found = store.get_command_by_idempotency_key(
            "idem-key-100", operator_id="op-alice", tenant_id="tenant-alpha"
        )
        assert found is not None
        assert found["command_id"] == "cmd-tenant-1"

        # Different operator -> None
        found_diff_op = store.get_command_by_idempotency_key(
            "idem-key-100", operator_id="op-bob", tenant_id="tenant-alpha"
        )
        assert found_diff_op is None

        # Different tenant -> None
        found_diff_tenant = store.get_command_by_idempotency_key(
            "idem-key-100", operator_id="op-alice", tenant_id="tenant-beta"
        )
        assert found_diff_tenant is None
