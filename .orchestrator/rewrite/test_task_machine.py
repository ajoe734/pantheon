from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import task_machine
from task_machine import DispatchReason, TaskAction, TaskState, TransitionError


class DispatchReasonTests(unittest.TestCase):
    def test_review_by_reviewer(self) -> None:
        self.assertEqual(
            task_machine.dispatch_reason("review", is_owner=False, is_reviewer=True, deps_satisfied=True),
            DispatchReason.REVIEW_READY,
        )

    def test_review_by_non_reviewer_is_none(self) -> None:
        self.assertIsNone(
            task_machine.dispatch_reason("review", is_owner=True, is_reviewer=False, deps_satisfied=True)
        )

    def test_review_approved_by_owner_finalize(self) -> None:
        self.assertEqual(
            task_machine.dispatch_reason("review_approved", is_owner=True, is_reviewer=False, deps_satisfied=True),
            DispatchReason.OWNED_FINALIZE,
        )

    def test_in_progress_owner_deps_ok(self) -> None:
        self.assertEqual(
            task_machine.dispatch_reason("in_progress", is_owner=True, is_reviewer=False, deps_satisfied=True),
            DispatchReason.OWNED_IN_PROGRESS,
        )

    def test_in_progress_owner_deps_unmet_is_none(self) -> None:
        self.assertIsNone(
            task_machine.dispatch_reason("in_progress", is_owner=True, is_reviewer=False, deps_satisfied=False)
        )

    def test_todo_owner_deps_ok(self) -> None:
        self.assertEqual(
            task_machine.dispatch_reason("todo", is_owner=True, is_reviewer=False, deps_satisfied=True),
            DispatchReason.OWNED_READY,
        )

    def test_todo_non_owner_is_none(self) -> None:
        self.assertIsNone(
            task_machine.dispatch_reason("todo", is_owner=False, is_reviewer=True, deps_satisfied=True)
        )

    def test_blocked_is_none(self) -> None:
        self.assertIsNone(
            task_machine.dispatch_reason("blocked", is_owner=True, is_reviewer=True, deps_satisfied=True)
        )

    def test_case_insensitive_status(self) -> None:
        self.assertEqual(
            task_machine.dispatch_reason("REVIEW", is_owner=False, is_reviewer=True, deps_satisfied=True),
            DispatchReason.REVIEW_READY,
        )


class DeliveryBindingTests(unittest.TestCase):
    def test_pull_request_binding_requires_exact_identity(self) -> None:
        task = {
            "id": "TASK-1",
            "generation": 2,
            "delivery_binding": {
                "kind": "pull_request",
                "pr": 42,
                "head_sha": "a" * 40,
                "head_branch": "task/TASK-1",
                "base": "dev",
            },
        }
        self.assertTrue(task_machine.delivery_binding_is_current(task))
        self.assertEqual(
            task_machine.delivery_binding_digest(task),
            task_machine.delivery_binding_digest(task),
        )
        task["delivery_binding"]["head_sha"] = "short"
        self.assertFalse(task_machine.delivery_binding_is_current(task))
        self.assertIsNone(task_machine.delivery_binding_digest(task))

    def test_artifact_binding_is_invalidated_by_contract_change(self) -> None:
        task = {
            "id": "TASK-2",
            "generation": 3,
            "artifacts": ["src/a.py"],
            "acceptance": ["test passes"],
        }
        contract = task_machine.delivery_contract_payload(task)
        task["delivery_binding"] = {
            "kind": "artifact_contract",
            **contract,
            "contract_sha256": task_machine._canonical_json_sha256(contract),
        }
        self.assertTrue(task_machine.delivery_binding_is_current(task))
        task["artifacts"] = ["src/b.py"]
        self.assertFalse(task_machine.delivery_binding_is_current(task))


class StateTableTests(unittest.TestCase):
    def test_coerce_known(self) -> None:
        self.assertEqual(task_machine.coerce_state("in_progress"), TaskState.IN_PROGRESS)
        self.assertEqual(task_machine.coerce_state("REVIEW"), TaskState.REVIEW)

    def test_coerce_unknown_is_none(self) -> None:
        self.assertIsNone(task_machine.coerce_state("bogus"))

    def test_core_transitions_present(self) -> None:
        self.assertEqual(task_machine.TRANSITIONS[(TaskState.TODO, "dispatch")], TaskState.IN_PROGRESS)
        self.assertEqual(task_machine.TRANSITIONS[(TaskState.REVIEW, "approve")], TaskState.REVIEW_APPROVED)
        self.assertEqual(task_machine.TRANSITIONS[(TaskState.REVIEW, "reject")], TaskState.IN_PROGRESS)
        self.assertEqual(task_machine.TRANSITIONS[(TaskState.REVIEW_APPROVED, "finalize")], TaskState.DONE)

    def test_canonical_command_lifecycle(self) -> None:
        self.assertEqual(task_machine.transition("todo", "start"), TaskState.IN_PROGRESS)
        self.assertEqual(task_machine.transition("in_progress", "progress"), TaskState.IN_PROGRESS)
        self.assertEqual(task_machine.transition("in_progress", "handoff"), TaskState.REVIEW)
        self.assertEqual(task_machine.transition("review", "approve"), TaskState.REVIEW_APPROVED)
        self.assertEqual(task_machine.transition("review", "reopen"), TaskState.IN_PROGRESS)
        self.assertEqual(task_machine.transition("review_approved", "done"), TaskState.DONE)

    def test_blocked_may_only_resume_through_reopen(self) -> None:
        self.assertEqual(task_machine.transition("blocked", "reopen"), TaskState.IN_PROGRESS)

    def test_illegal_direct_overwrites_fail_closed(self) -> None:
        illegal = [
            ("review", TaskAction.START.value),
            ("todo", TaskAction.PROGRESS.value),
            ("review_approved", TaskAction.PROGRESS.value),
            ("todo", TaskAction.HANDOFF.value),
            ("in_progress", TaskAction.APPROVE.value),
            ("review", TaskAction.DONE.value),
            ("done", TaskAction.REOPEN.value),
        ]
        for status, action in illegal:
            with self.subTest(status=status, action=action), self.assertRaises(TransitionError):
                task_machine.transition(status, action)

    def test_unknown_state_and_action_fail_closed(self) -> None:
        with self.assertRaisesRegex(TransitionError, "unknown task state"):
            task_machine.transition("ready", "start")
        with self.assertRaisesRegex(TransitionError, "unknown task action"):
            task_machine.transition("todo", "restore_approved")


class AssignmentTransitionTests(unittest.TestCase):
    def test_human_ops_can_replace_catalog_runtime_assignment(self) -> None:
        change = task_machine.assignment_transition(
            "Antigravity",
            "Claude2",
            "Codex2",
            "Antigravity",
            actor="Human/Ops",
            reason="Operator recalled an unavailable lane",
            expected_owner="Antigravity",
            expected_reviewer="Claude2",
        )
        self.assertEqual(change.new_owner, "Codex2")
        self.assertEqual(change.actor, "Human/Ops")

    def test_assignment_cas_and_actor_fail_closed(self) -> None:
        with self.assertRaisesRegex(TransitionError, "compare-and-swap"):
            task_machine.assignment_transition(
                "Codex",
                "Claude2",
                "Codex2",
                "Claude2",
                actor="Human/Ops",
                reason="reassign",
                expected_owner="Antigravity",
            )
        with self.assertRaisesRegex(TransitionError, "not authorized"):
            task_machine.assignment_transition(
                "Codex",
                "Claude2",
                "Codex2",
                "Claude2",
                actor="Codex",
                reason="reassign",
            )

    def test_orchestrator_assignment_event_is_generation_bound(self) -> None:
        assignment = task_machine.assignment_transition(
            "Codex",
            "Claude",
            "Codex2",
            "Claude",
            actor="Orchestrator",
            reason="terminal quota reassignment",
        )
        event = task_machine.assignment_activity_event(
            task_id="TASK-42",
            timestamp="2026-08-14T12:00:00Z",
            assignment=assignment,
            old_generation=4,
            new_generation=5,
            message="terminal quota reassignment",
        )

        self.assertTrue(event["event_id"].startswith("supervisor-task-reassigned-"))
        self.assertEqual(event["old_generation"], 4)
        self.assertEqual(event["generation"], 5)
        with self.assertRaisesRegex(TransitionError, "generation-bound"):
            task_machine.assignment_activity_event(
                task_id="TASK-42",
                timestamp="2026-08-14T12:00:00Z",
                assignment=assignment,
                old_generation=4,
                new_generation=6,
                message="terminal quota reassignment",
            )


if __name__ == "__main__":
    unittest.main()
