from __future__ import annotations

import unittest

from dispatch_policy import (
    DEFAULT_ACTIVE_WORKER_STATUSES,
    REASON_OWNED_FINALIZE,
    REASON_OWNED_IN_PROGRESS,
    REASON_OWNED_READY,
    REASON_REVIEW_READY,
    dispatch_reason_priority,
    is_execution_dispatch_reason,
    normalized_status_set,
    ready_dispatch_settings,
)


class DispatchPolicyTests(unittest.TestCase):
    def test_dispatch_reason_priority_matches_current_execution_order(self) -> None:
        self.assertEqual(dispatch_reason_priority(REASON_REVIEW_READY), 0)
        self.assertEqual(dispatch_reason_priority(REASON_OWNED_FINALIZE), 1)
        self.assertEqual(dispatch_reason_priority(REASON_OWNED_IN_PROGRESS), 2)
        self.assertEqual(dispatch_reason_priority(REASON_OWNED_READY), 3)
        self.assertIsNone(dispatch_reason_priority("discussion_planning_readout_dispatch"))

    def test_execution_dispatch_reason_recognizes_only_execution_reasons(self) -> None:
        self.assertTrue(is_execution_dispatch_reason(REASON_REVIEW_READY))
        self.assertTrue(is_execution_dispatch_reason(REASON_OWNED_READY))
        self.assertFalse(is_execution_dispatch_reason("discussion_planning_readout_dispatch"))
        self.assertFalse(is_execution_dispatch_reason(None))

    def test_ready_dispatch_settings_preserves_existing_defaults(self) -> None:
        settings = ready_dispatch_settings({})
        self.assertEqual(settings["review_statuses"], ["review"])
        self.assertEqual(settings["finalize_statuses"], ["review_approved"])
        self.assertEqual(settings["owned_statuses"], ["in_progress", "todo"])
        self.assertEqual(settings["dependency_done_statuses"], ["done"])
        self.assertEqual(settings["worker_terminal_statuses"], ["done", "review_approved"])
        self.assertEqual(settings["active_worker_statuses"], DEFAULT_ACTIVE_WORKER_STATUSES)
        self.assertEqual(settings["max_tasks_per_agent"], 1)
        self.assertEqual(settings["max_dispatches_per_tick"], 4)

    def test_ready_dispatch_settings_keeps_configured_values(self) -> None:
        settings = ready_dispatch_settings(
            {
                "ready_dispatcher": {
                    "review_statuses": ["needs_review"],
                    "done_statuses": ["done"],
                    "max_dispatches_per_tick": 8,
                }
            }
        )
        self.assertEqual(settings["review_statuses"], ["needs_review"])
        self.assertEqual(settings["worker_terminal_statuses"], ["done"])
        self.assertEqual(settings["max_dispatches_per_tick"], 8)

    def test_normalized_status_set_defaults_and_lowercases_values(self) -> None:
        self.assertEqual(normalized_status_set(None, ["Done"]), {"done"})
        self.assertEqual(normalized_status_set(["Review", "DONE"], ["todo"]), {"review", "done"})


if __name__ == "__main__":
    unittest.main()
