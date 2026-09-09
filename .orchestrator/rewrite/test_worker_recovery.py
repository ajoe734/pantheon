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
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

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


class RecoveryContinuationTests(unittest.TestCase):
    """Exercise real canonical receipt writes and replay through request building."""

    def setUp(self) -> None:
        import supervisor

        self.sup = supervisor
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "status"
        (self.root / ".orchestrator").mkdir(parents=True)
        event_log = Path(self.temp.name) / "runtime" / "tasks.jsonl"
        self.config = {
            "paths": {
                "status_file": str(self.root / "ai-status.json"),
                "activity_log": str(self.root / "ai-activity-log.jsonl"),
            },
            "task_state_store": {"mode": "authoritative", "event_log": str(event_log)},
            "agents": {
                agent: {"id": agent, "display_name": name, "provider": agent, "adapter": "codex"}
                for agent, name in (("codex", "Codex"), ("codex2", "Codex2"))
            },
            "providers": {},
        }
        task = {
            "id": "TASK-1", "generation": 1, "owner": "Codex", "reviewer": "Codex2",
            "status": "in_progress", "depends_on": [], "last_update": "2026-09-09T00:00:00Z",
        }
        task.update({
            "next": "Fix the review rejection on the existing PR.",
            "github_review_bridge": {
                "pr": 5637, "head_sha": "1" * 40,
                "head_branch": "task/TASK-1", "base": "dev",
                "repository": "ajoe734/pantheon",
                "decision": "reopen", "actor": "Codex2",
                "review_proof_ref": "DO-NOT-COPY-APPROVAL",
                "intent_nonce": "DO-NOT-COPY-NONCE",
            },
            "review_requeue_intent": {
                "task_id": "TASK-1", "task_generation": 1,
                "reopened_by": "Codex2", "reopened_at": "2026-09-09T00:00:00Z",
                "reason": "Preserve the original committed implementation and repair the manifest.",
            },
            "execution_authorization": {"grant": "DO-NOT-COPY-GRANT"},
            "review_binding": {"approval": "DO-NOT-COPY-BINDING"},
        })
        state = {"tasks": [task], "blockers": [], "handoffs": []}
        supervisor.rewrite_task_state_store.append_state_commit(event_log, state, source="test-seed")
        supervisor.write_json(Path(self.config["paths"]["status_file"]), state)
        patcher = mock.patch.object(supervisor, "sync_status_pipeline", side_effect=self._drain)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _drain(self, config):
        status = self.sup.load_status(config)
        if status.pop("status_activity_outbox", None) is not None:
            self.sup.write_status(config, status, source="test-outbox-drain")
        return True

    def _fence(self, run_id="run-1"):
        task = self.sup.load_status(self.config)["tasks"][0]
        worker = {
            "run_id": run_id, "task_id": task["id"], "queue_event_id": f"q-{run_id}",
            "agent_id": task["owner"], "provider": task["owner"],
            "request_snapshot": {"reason": self.sup.REASON_OWNED_IN_PROGRESS},
        }
        receipt = worker_recovery.build_lost_lease_receipt(
            self.config, worker, task,
            reason_kind="worker_process_missing", reason="worker disappeared",
        )
        expected = {
            "expected_owner": task["owner"], "expected_reviewer": task["reviewer"],
            "expected_status": task["status"], "expected_generation": task["generation"],
        }
        self.assertTrue(self.sup.persist_worker_recovery_receipt(self.config, receipt, **expected))
        return receipt, expected

    def _reassign(self):
        status = self.sup.load_status(self.config)
        task = status["tasks"][0]
        receipt = worker_recovery._canonical_worker_recovery_receipt(status, task)
        self.assertTrue(self.sup.persist_task_reassignment(
            self.config, task_id=task["id"],
            new_owner=task["reviewer"], new_reviewer=task["owner"],
            message="Generic operational lease recovery message",
            expected_owner=task["owner"], expected_reviewer=task["reviewer"],
            expected_status=task["status"], expected_generation=task["generation"],
            worker_recovery_receipt=receipt,
        ))
        return self.sup.load_status(self.config)

    def _request(self, status=None, **overrides):
        status = status or self.sup.load_status(self.config)
        task = status["tasks"][0]
        event = {
            "task_id": task["id"], "task_generation": task["generation"],
            "target_agent": task["owner"], "reason": self.sup.REASON_OWNED_IN_PROGRESS,
            "recovery_receipt_id": task["worker_recovery"]["receipt_id"],
            "message": "Continue this task.",
            "context_files": [".orchestrator/task-briefs/task-1.md"],
            "metadata": {"task": {"forged_context": "DO-NOT-TRUST-QUEUE"}},
        }
        event.update(overrides)
        return self.sup.build_request(self.config, event)

    def _workspace_arguments(self, task):
        return {
            "task_id": "TASK-1", "receipt_id": task["worker_recovery"]["receipt_id"],
            "expected_generation": task["generation"],
            "workspace": {
                "repository_id": "pantheon", "workspace_path": str(self.root / "worktree"),
                "branch": "task/TASK-1", "source_head": "2" * 40,
                "archive_path": str(self.root / "archive"),
                "preserved_branch_ref": "refs/pantheon/recovery/task-1/receipt-1",
            },
        }

    def _workspace_eligible(self, task, request):
        receipt_id = task["worker_recovery"]["receipt_id"]
        workspace = self._workspace_arguments(task)["workspace"]
        queue_event_id = "queue-replacement"
        runtime = {
            "workers": {},
            "queue": {"events": {queue_event_id: {
                "recovery_receipt_id": receipt_id,
                "intent": {
                    "task_id": task["id"], "task_generation": task["generation"],
                    "recovery_receipt_id": receipt_id, "target_agent": request.agent_id,
                },
            }}},
            "worker_worktrees": {"leases": {task["id"]: {
                "task_id": task["id"], "workspace_task_id": task["id"],
                "repository_id": "pantheon", "branch": workspace["branch"],
                "path": workspace["workspace_path"], "base_ref": "origin/dev",
                "source_root": str(self.root),
            }}},
        }
        return self.sup._lost_lease_replacement_may_recover_worktree(
            self.config, runtime, request, task_id=task["id"],
            repository_id="pantheon", source_root=self.root, branch=workspace["branch"],
            worktree_path=Path(workspace["workspace_path"]), base_ref="origin/dev",
            queue_event_id=queue_event_id, target_agent=request.agent_id,
        )

    def test_fence_reassignment_and_repeated_loss_preserve_context_without_authority(self):
        receipt, expected = self._fence()
        status = self._reassign()
        task = status["tasks"][0]
        self.assertEqual(task["generation"], 3)
        self.assertEqual(task["next"], "Fix the review rejection on the existing PR.")
        self.assertNotIn("review_requeue_intent", task)
        first = deepcopy(worker_recovery._canonical_worker_recovery_receipt(status, task))
        continuation = first["previous"]["continuation"]
        self.assertEqual(continuation["source"]["head_sha"], "1" * 40)
        self.assertEqual(continuation["source"]["repository_slug"], "ajoe734/pantheon")
        self.assertEqual(continuation["rejection"]["actor"], "Codex2")
        self.assertNotIn("DO-NOT-COPY", json.dumps(continuation))
        self.assertTrue(self.sup.persist_worker_recovery_receipt(self.config, receipt, **expected))
        self.assertEqual(worker_recovery._canonical_worker_recovery_receipt(
            self.sup.load_status(self.config), task), first)

        # Human/Ops adds a newer next note without revising this task's identity.
        status = self.sup.load_status(self.config)
        status["tasks"][0]["next"] = "Keep the newer operator acceptance constraint."
        status["tasks"][0].pop("github_review_bridge")
        self.sup.write_status(self.config, status, source="test-operator-note")
        self._fence("run-2")
        repeated = self._reassign()
        repeated_task = repeated["tasks"][0]
        second = worker_recovery._canonical_worker_recovery_receipt(repeated, repeated_task)
        self.assertEqual(second["previous"]["continuation"]["source"], continuation["source"])
        self.assertEqual(second["previous"]["continuation"]["rejection"], continuation["rejection"])
        self.assertNotIn("previous", second["previous"]["continuation"])
        self.assertEqual(repeated_task["next"], "Keep the newer operator acceptance constraint.")
        self.assertTrue(self.sup.rearm_worker_recovery_receipt(
            self.config, task_id="TASK-1", receipt_id=second["receipt_id"],
            task_generation=repeated_task["generation"], reason="replacement has no capacity",
        ))
        self.assertEqual(self.sup.load_status(self.config)["tasks"][0]["next"], repeated_task["next"])

    def test_canonical_projection_reaches_tracked_and_external_context_without_copying_authority(self):
        self._fence()
        status = self._reassign()
        for repository in ("pantheon", "execute_plans"):
            with self.subTest(repository=repository):
                request = self._request(metadata={"task": {"target_repo": repository}})
                self.assertEqual(request.message.count("Recovery source continuation (advisory history)"), 1)
                self.assertIn("pr=5637", request.message)
                self.assertIn("head_sha=" + "1" * 40, request.message)
                self.assertIn("repair the manifest", request.message)
                self.assertNotIn("DO-NOT-COPY", request.message)
                self.assertEqual(request.context_files, [".orchestrator/task-briefs/task-1.md"])
        self.assertEqual(self._request(status).message, self._request(status).message)

    def test_stale_or_forged_pointer_cannot_inject_continuation(self):
        self._fence()
        status = self._reassign()
        for overrides in (
            {"recovery_receipt_id": "lost-lease-forged"},
            {"task_generation": 1},
            {"target_agent": "Codex"},
            {"reason": self.sup.REASON_REVIEW_READY},
        ):
            with self.subTest(overrides=overrides):
                self.assertNotIn("Recovery source continuation", self._request(**overrides).message)
        status["tasks"][0]["worker_recovery"]["replacement_generation"] = 999
        self.sup.write_status(self.config, status, source="test-stale-pointer")
        self.assertNotIn("Recovery source continuation", self._request().message)

    def test_approved_retry_preserves_current_note_and_does_not_project_approval_proof(self):
        status = self.sup.load_status(self.config)
        task = status["tasks"][0]
        task["status"] = "review_approved"
        task["next"] = "Finish only the approved existing delivery."
        task["delivery_binding"] = {
            "kind": "pull_request", "pr": 5637, "head_sha": "4" * 40,
            "head_branch": "task/TASK-1", "base": "dev",
        }
        self.sup.write_status(self.config, status, source="test-approved-task")
        self._fence()
        fenced = self.sup.load_status(self.config)
        task = fenced["tasks"][0]
        receipt = worker_recovery._canonical_worker_recovery_receipt(fenced, task)
        self.assertTrue(self.sup.persist_approved_worker_recovery_binding(
            self.config, task_id="TASK-1", receipt=receipt,
            expected_owner=task["owner"], expected_reviewer=task["reviewer"],
            expected_generation=task["generation"],
        ))
        updated = self.sup.load_status(self.config)
        self.assertEqual(updated["tasks"][0]["next"], "Finish only the approved existing delivery.")
        self.assertEqual(updated["tasks"][0]["review_binding"], {"approval": "DO-NOT-COPY-BINDING"})
        request = self._request(reason=self.sup.REASON_OWNED_FINALIZE)
        self.assertIn("head_sha=" + "4" * 40, request.message)
        self.assertNotIn("DO-NOT-COPY", request.message)
        self.assertNotIn("Unresolved reviewer requirements", request.message)

    def test_new_nonreviewer_reopen_does_not_resurrect_resolved_rejection(self):
        self._fence()
        first = self._reassign()
        for actor in ("Human/Ops", first["tasks"][0]["owner"]):
            with self.subTest(actor=actor):
                status = deepcopy(first)
                task = status["tasks"][0]
                # A new handoff closes the old rejection, then a nonreviewer
                # reopens that later delivery for a different correction.
                task["status"] = "review"
                task["delivery_binding"] = {"pr": 5637, "head_sha": "5" * 40}
                task.pop("github_review_bridge", None)
                self.sup.write_status(self.config, status, source="test-new-handoff")
                task["status"] = "in_progress"
                task.pop("delivery_binding")
                task["next"] = "New correction after the resolved review handoff."
                task["review_requeue_intent"] = {
                    "task_id": task["id"], "task_generation": task["generation"],
                    "reopened_by": actor, "reopened_at": "2026-09-09T01:00:00Z",
                    "reason": task["next"],
                }
                self.sup.write_status(self.config, status, source="test-new-reopen")
                self._fence("run-new-reopen-" + actor)
                resumed = self._reassign()
                continuation = worker_recovery._canonical_worker_recovery_receipt(
                    resumed, resumed["tasks"][0]
                )["previous"]["continuation"]
                self.assertNotIn("rejection", continuation)
                request = self._request()
                self.assertIn("New correction after the resolved review handoff", request.message)
                self.assertNotIn("repair the manifest", request.message)
                self.assertNotIn("Unresolved reviewer requirements", request.message)

    def test_workspace_publication_eligibility_and_prompt_reject_role_drift(self):
        self._fence()
        initial = self._reassign()
        task = initial["tasks"][0]
        arguments = self._workspace_arguments(task)
        self.assertTrue(self._workspace_eligible(task, self._request()))
        for changed_status in ("review", "blocked", "done", "superseded"):
            with self.subTest(status=changed_status):
                changed = deepcopy(initial)
                changed["tasks"][0]["status"] = changed_status
                self.sup.write_status(self.config, changed, source="test-lifecycle-drift")
                self.assertFalse(self.sup.persist_worker_recovery_workspace(self.config, **arguments))
                request = self._request()
                self.assertFalse(self._workspace_eligible(changed["tasks"][0], request))
                self.assertNotIn("Recovery source continuation", request.message)
        for field, value in (("role", "reviewer"), ("agent", "Codex")):
            with self.subTest(field=field):
                changed = deepcopy(initial)
                receipt = changed["worker_recovery_receipts"][arguments["receipt_id"]]
                receipt["replacement"][field] = value
                self.sup.write_status(self.config, changed, source="test-replacement-drift")
                self.assertFalse(self.sup.persist_worker_recovery_workspace(self.config, **arguments))
                request = self._request()
                self.assertFalse(self._workspace_eligible(changed["tasks"][0], request))
                self.assertNotIn("Recovery source continuation", request.message)

        # The reverse transfer is equally stale: a reviewer replacement no
        # longer owns the work once review returns implementation to the owner.
        changed = deepcopy(initial)
        task = changed["tasks"][0]
        task["status"] = "review"
        receipt = changed["worker_recovery_receipts"][arguments["receipt_id"]]
        receipt["recovery_role"] = "reviewer"
        receipt["replacement"].update(role="reviewer", agent=task["reviewer"])
        self.sup.write_status(self.config, changed, source="test-reviewer-recovery")
        request = self._request(target_agent=task["reviewer"], reason=self.sup.REASON_REVIEW_READY)
        self.assertTrue(self._workspace_eligible(task, request))
        task["status"] = "in_progress"
        self.sup.write_status(self.config, changed, source="test-reviewer-lane-finished")
        self.assertFalse(self.sup.persist_worker_recovery_workspace(self.config, **arguments))
        request = self._request(target_agent=task["reviewer"], reason=self.sup.REASON_REVIEW_READY)
        self.assertFalse(self._workspace_eligible(task, request))
        self.assertNotIn("Recovery source continuation", request.message)

    def test_workspace_archive_fact_is_durable_idempotent_and_fenced(self):
        self._fence()
        status = self._reassign()
        task = status["tasks"][0]
        arguments = self._workspace_arguments(task)
        self.assertTrue(self.sup.persist_worker_recovery_workspace(self.config, **arguments))
        first = self.sup.load_status(self.config)
        self.assertTrue(self.sup.persist_worker_recovery_workspace(self.config, **arguments))
        self.assertEqual(self.sup.load_status(self.config), first)
        for changes in (
            {"expected_generation": 1}, {"receipt_id": "lost-lease-forged"},
            {"workspace": {**arguments["workspace"], "repository_id": "execute_plans"}},
            {"workspace": {**arguments["workspace"], "source_head": "3" * 40}},
            {"workspace": {**arguments["workspace"], "branch": "task/OTHER"}},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(self.sup.persist_worker_recovery_workspace(self.config, **{**arguments, **changes}))
        self.assertEqual(self.sup.load_status(self.config)["tasks"][0], task)
        self._fence("run-2")
        self._reassign()
        request = self._request()
        self.assertIn(str(self.root / "archive"), request.message)
        self.assertIn("refs/pantheon/recovery/task-1/receipt-1", request.message)
        self.assertIn("source_head=" + "2" * 40, request.message)


if __name__ == "__main__":
    unittest.main()
