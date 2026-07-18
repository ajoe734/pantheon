from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import task_machine
from task_machine import DispatchReason, TaskState


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

    def test_priority_values_match_incumbent_ladder(self) -> None:
        self.assertEqual(task_machine.dispatch_priority("review", is_owner=False, is_reviewer=True, deps_satisfied=True), 0)
        self.assertEqual(task_machine.dispatch_priority("review_approved", is_owner=True, is_reviewer=False, deps_satisfied=True), 1)
        self.assertEqual(task_machine.dispatch_priority("in_progress", is_owner=True, is_reviewer=False, deps_satisfied=True), 2)
        self.assertEqual(task_machine.dispatch_priority("todo", is_owner=True, is_reviewer=False, deps_satisfied=True), 3)
        self.assertIsNone(task_machine.dispatch_priority("done", is_owner=True, is_reviewer=True, deps_satisfied=True))

    def test_case_insensitive_status(self) -> None:
        self.assertEqual(
            task_machine.dispatch_reason("REVIEW", is_owner=False, is_reviewer=True, deps_satisfied=True),
            DispatchReason.REVIEW_READY,
        )


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


if __name__ == "__main__":
    unittest.main()
