from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import supervisor
from rewrite import provider_health


class WorkerProgressHealthTests(unittest.TestCase):
    NOW = datetime(2026, 9, 30, 9, 8, tzinfo=timezone.utc)

    def _state(self) -> dict:
        return {
            "delivery_health": provider_health.apply_failure(
                None, endpoint_id="antigravity", account_id="antigravity",
                failure_kind="auth", observed_at=self.NOW,
            )
        }

    def _worker(self, progress_at: datetime) -> dict:
        return {"agent_id": "antigravity", "last_work_progress_at": progress_at.strftime("%Y-%m-%dT%H:%M:%SZ")}

    def test_streaming_worker_clears_its_endpoint_auth_verdict(self) -> None:
        state = self._state()
        changed = supervisor.record_delivery_health_worker_progress({}, state, self._worker(self.NOW + timedelta(minutes=5)))
        self.assertTrue(changed)
        entry = state["delivery_health"]["endpoints"]["antigravity"]
        self.assertEqual(entry["state"], provider_health.DeliveryHealthState.HEALTHY.value)
        self.assertEqual(entry["source"], "worker_progress")

    def test_progress_before_the_verdict_changes_nothing(self) -> None:
        state = self._state()
        before = dict(state["delivery_health"])
        changed = supervisor.record_delivery_health_worker_progress({}, state, self._worker(self.NOW - timedelta(minutes=1)))
        self.assertFalse(changed)
        self.assertEqual(state["delivery_health"], before)

    def test_worker_without_progress_changes_nothing(self) -> None:
        state = self._state()
        self.assertFalse(supervisor.record_delivery_health_worker_progress({}, state, {"agent_id": "antigravity"}))


if __name__ == "__main__":
    unittest.main()
