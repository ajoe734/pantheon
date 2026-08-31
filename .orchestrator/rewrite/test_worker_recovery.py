"""DTG-CLEAN-M5 characterization tests for the standalone worker-recovery
module -- not a re-test of .orchestrator/test_supervisor.py's extensive
recovery coverage (which already exercises this exact code through
supervisor.py's re-export and continues to pass unchanged), but proof
that this module is genuinely usable on its own: no circular import, the
lazy supervisor handback resolves, and the new receipt validator agrees
with the receipt shape the existing constructor produces.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import worker_recovery


class WorkerRecoveryModuleTests(unittest.TestCase):
    def test_module_imports_with_no_circular_dependency(self) -> None:
        # supervisor.py imports this module at its own top level; importing
        # supervisor here (a second, independent path into the same
        # dependency graph) must not raise, proving the graph is a DAG
        # (supervisor -> worker_recovery -> {common, dispatch_policy,
        # rewrite.task_identity}, with the reverse edge only ever taken
        # lazily, at call time, via _supervisor_module()).
        import supervisor  # noqa: F401

    def test_lazy_supervisor_handback_resolves(self) -> None:
        supervisor = worker_recovery._supervisor_module()
        self.assertTrue(hasattr(supervisor, "task_current_dispatch_responsibility"))

    def test_constructed_receipt_validates(self) -> None:
        worker = {
            "run_id": "run-1",
            "queue_event_id": "q-1",
            "agent_id": "Claude",
            "provider": "Claude",
        }
        task = {"id": "T-1", "owner": "Claude", "reviewer": "Codex", "status": "in_progress"}
        receipt = worker_recovery.build_lost_lease_receipt(
            {}, worker, task, reason_kind="worker_lease_expired", reason="lease expired"
        )
        self.assertTrue(worker_recovery.validate_lost_lease_receipt(receipt))

    def test_validator_rejects_malformed_receipts(self) -> None:
        self.assertFalse(worker_recovery.validate_lost_lease_receipt({}))
        self.assertFalse(worker_recovery.validate_lost_lease_receipt("not-a-mapping"))
        self.assertFalse(
            worker_recovery.validate_lost_lease_receipt(
                {
                    "schema_version": worker_recovery.LOST_LEASE_RECEIPT_SCHEMA_VERSION,
                    "type": "worker_lost_lease",
                    "status": "not-a-real-status",
                    "receipt_id": "r1",
                    "dedupe_key": "d1",
                    "task_id": "T-1",
                    "recovery_role": "owner",
                    "worker": {},
                    "lease": {},
                }
            )
        )
        self.assertFalse(
            worker_recovery.validate_lost_lease_receipt(
                {
                    "schema_version": worker_recovery.LOST_LEASE_RECEIPT_SCHEMA_VERSION + 1,
                    "type": "worker_lost_lease",
                    "status": "pending",
                    "receipt_id": "r1",
                    "dedupe_key": "d1",
                    "task_id": "T-1",
                    "recovery_role": "owner",
                    "worker": {},
                    "lease": {},
                }
            )
        )

    def test_pointer_predicates_agree_with_pointer_shape(self) -> None:
        pointer = worker_recovery._worker_recovery_pointer(
            {
                "receipt_id": "r1",
                "status": "pending",
                "task_generation": 3,
                "fence_generation": 3,
                "replacement": None,
            }
        )
        task = {"generation": 3, worker_recovery.WORKER_RECOVERY_TASK_KEY: pointer}
        self.assertTrue(worker_recovery.task_has_pending_worker_recovery(task))
        self.assertTrue(worker_recovery.task_has_active_worker_recovery(task))
        self.assertFalse(worker_recovery.task_has_pending_worker_recovery(None))
        self.assertFalse(worker_recovery.task_has_active_worker_recovery({}))

    def test_prune_worker_recovery_receipts_keeps_protected_and_bounded(self) -> None:
        receipts = {
            f"r{i}": {"status": "resolved", "detected_at": f"2026-01-{i:02d}T00:00:00Z"}
            for i in range(1, worker_recovery.MAX_WORKER_RECOVERY_RECEIPTS + 5)
        }
        status = {worker_recovery.WORKER_RECOVERY_RECEIPTS_KEY: receipts, "tasks": []}
        worker_recovery._prune_worker_recovery_receipts(status, current_receipt_id="r1")
        self.assertLessEqual(len(receipts), worker_recovery.MAX_WORKER_RECOVERY_RECEIPTS)
        self.assertIn("r1", receipts)

    def test_entry_points_are_exported(self) -> None:
        for name in (
            "build_lost_lease_receipt",
            "validate_lost_lease_receipt",
            "task_has_pending_worker_recovery",
            "task_has_active_worker_recovery",
            "worker_recovery_responsibility_is_obsolete",
            "count_lost_worker_recovery_outcome",
        ):
            self.assertTrue(callable(getattr(worker_recovery, name)), name)


if __name__ == "__main__":
    unittest.main()
