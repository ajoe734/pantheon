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


class IncumbentSidecarDisableTests(unittest.TestCase):
    """Phase 7: the live sidecar make-work engine is switchable off today."""

    def test_disabled_sidecar_is_a_clean_noop(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import supervisor

        config = {"underutilization_dispatch": {"enabled": False}, "agents": {"claude": {}}}
        state = {"workers": {}}
        changed = supervisor.dispatch_underutilization_sidecars(config, state)
        self.assertFalse(changed)
        # no tasks synthesized
        self.assertFalse(state.get("tasks"))
        # and it parked its tracking rather than accumulating a wave
        self.assertIsNone(state.get("underutilization", {}).get("below_threshold_since"))


if __name__ == "__main__":
    unittest.main()
