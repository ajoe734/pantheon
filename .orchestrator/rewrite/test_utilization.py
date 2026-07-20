from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import utilization
from utilization import UtilizationAction


class SelectUtilizationActionTests(unittest.TestCase):
    def test_healthy_utilization_is_noop(self) -> None:
        self.assertEqual(
            utilization.select_utilization_action(
                utilization_ratio=0.9, threshold_ratio=0.5, ready_backlog=10
            ),
            UtilizationAction.NOOP,
        )

    def test_under_utilized_with_backlog_reprioritizes(self) -> None:
        self.assertEqual(
            utilization.select_utilization_action(
                utilization_ratio=0.1, threshold_ratio=0.5, ready_backlog=3
            ),
            UtilizationAction.REPRIORITIZE,
        )

    def test_under_utilized_no_backlog_is_noop_never_synthesizes(self) -> None:
        # the accretion being removed: with nothing ready, idle is correct
        self.assertEqual(
            utilization.select_utilization_action(
                utilization_ratio=0.0, threshold_ratio=0.5, ready_backlog=0
            ),
            UtilizationAction.NOOP,
        )


class IncumbentSidecarDeletedTests(unittest.TestCase):
    """Phase 7: the sidecar make-work synthesis engine is physically deleted."""

    def test_synthesis_engine_is_gone(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import supervisor

        # the synthesis engine + its exclusive helpers were removed; only the
        # shared existing-sidecar dispatch helpers remain.
        for removed in (
            "dispatch_underutilization_sidecars",
            "create_sidecar_task",
            "build_catalog_sidecar_candidates",
            "eligible_idle_agents_for_sidecars",
        ):
            self.assertFalse(hasattr(supervisor, removed), f"{removed} should be deleted")
        for kept in ("task_is_sidecar", "sidecar_only_agent_names", "sidecar_statuses"):
            self.assertTrue(hasattr(supervisor, kept), f"{kept} (shared dispatch helper) must remain")


if __name__ == "__main__":
    unittest.main()
