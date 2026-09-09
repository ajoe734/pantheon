"""Shared task-authority helpers stay identical at both public entrypoints."""
from __future__ import annotations

import hashlib
import sys
import unittest
from copy import deepcopy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / ".orchestrator"))

import ai_status
import supervisor
from rewrite import task_state_store, worker_recovery


class TaskAuthorityHelperIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = {
            "id": "任務-測試",
            "generation": 3,
            "status": "review",
            "metadata": {
                "nested": {"z": 2, "a": 1},
                "labels": ["β", {"nested": "雪"}],
            },
        }
        self.digests = (
            task_state_store.review_decision_task_digest,
            ai_status.review_decision_task_digest,
            supervisor.review_intent_recovery_task_digest,
        )

    def test_cli_uses_taskstore_digest_function(self) -> None:
        self.assertIs(
            ai_status.review_decision_task_digest,
            task_state_store.review_decision_task_digest,
        )

    def test_supervisor_uses_taskstore_digest_function(self) -> None:
        self.assertIs(
            supervisor.review_intent_recovery_task_digest,
            task_state_store.review_decision_task_digest,
        )

    def test_entrypoints_use_one_worker_recovery_predicate(self) -> None:
        self.assertIs(
            ai_status.task_has_active_worker_recovery,
            worker_recovery.task_has_active_worker_recovery,
        )
        self.assertIs(
            supervisor.task_has_active_worker_recovery,
            worker_recovery.task_has_active_worker_recovery,
        )

    def test_unicode_nested_digest_keeps_canonical_bytes_and_input(self) -> None:
        expected_bytes = (
            '{"generation":3,"id":"任務-測試","metadata":'
            '{"labels":["β",{"nested":"雪"}],"nested":{"a":1,"z":2}},'
            '"status":"review"}'
        ).encode("utf-8")
        expected_digest = hashlib.sha256(expected_bytes).hexdigest()
        original = deepcopy(self.task)
        for digest in self.digests:
            with self.subTest(entrypoint=digest.__module__):
                self.assertEqual(digest(self.task), expected_digest)
                self.assertEqual(self.task, original)

    def test_each_review_or_projection_marker_is_excluded_without_mutation(self) -> None:
        excluded = {
            "review_decision_intent": {"nonce": "reservation", "nested": ["一"]},
            "review_decision_intent_recovery": {"receipt_id": "receipt-1"},
            "status_write_pending": True,
            "status_write_pending_count": 2,
        }
        self.assertEqual(
            task_state_store.REVIEW_DECISION_DIGEST_EXCLUDED_KEYS,
            frozenset(excluded),
        )
        for fields in [*({key: value} for key, value in excluded.items()), excluded]:
            task = {**deepcopy(self.task), **deepcopy(fields)}
            original = deepcopy(task)
            for digest in self.digests:
                with self.subTest(fields=list(fields), entrypoint=digest.__module__):
                    self.assertEqual(digest(task), digest(self.task))
                    self.assertEqual(task, original)

    def test_business_and_worker_recovery_mutations_change_digest(self) -> None:
        mutations = [
            {"status": "in_progress"},
            {"generation": 4},
            {
                "metadata": {
                    "nested": {"z": 2, "a": 9},
                    "labels": ["β", {"nested": "雪"}],
                }
            },
            {"worker_recovery": {"receipt_id": "worker-1", "status": "pending"}},
        ]
        for mutation in mutations:
            task = {**deepcopy(self.task), **deepcopy(mutation)}
            for digest in self.digests:
                with self.subTest(mutation=mutation, entrypoint=digest.__module__):
                    self.assertNotEqual(digest(task), digest(self.task))


if __name__ == "__main__":
    unittest.main()
