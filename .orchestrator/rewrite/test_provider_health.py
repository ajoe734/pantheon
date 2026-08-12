from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import provider_health
from provider_health import DeliveryHealthState


class DeliveryHealthSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)

    def test_live_success_marks_exact_endpoint_and_capacity_account_healthy(self) -> None:
        snapshot = provider_health.apply_probe(
            provider_health.empty_delivery_health(),
            endpoint_id="codex1-2",
            account_id="codex1",
            probe={"source": "live", "ready": True, "status": "ready"},
            observed_at=self.now,
            valid_for_seconds=300,
        )

        self.assertEqual(
            provider_health.endpoint_state(snapshot, "codex1-2", now=self.now),
            DeliveryHealthState.HEALTHY,
        )
        self.assertEqual(
            provider_health.account_state(snapshot, "codex1", now=self.now),
            DeliveryHealthState.HEALTHY,
        )
        self.assertEqual(
            provider_health.delivery_health_block_reason(
                snapshot, endpoint_id="codex1-2", account_id="codex1", now=self.now
            ),
            (None, False),
        )

    def test_auth_failure_is_endpoint_local_even_when_capacity_account_is_shared(self) -> None:
        snapshot = provider_health.apply_probe(
            provider_health.empty_delivery_health(),
            endpoint_id="claude",
            account_id="claude-shared",
            probe={"source": "live", "ready": True, "status": "ready"},
            observed_at=self.now,
        )
        snapshot = provider_health.apply_probe(
            snapshot,
            endpoint_id="claude2",
            account_id="claude-shared",
            probe={"source": "live", "ready": False, "status": "auth_not_ready"},
            observed_at=self.now,
            retry_after_seconds=60,
        )

        self.assertEqual(
            provider_health.endpoint_state(snapshot, "claude", now=self.now),
            DeliveryHealthState.HEALTHY,
        )
        self.assertEqual(
            provider_health.endpoint_state(snapshot, "claude2", now=self.now),
            DeliveryHealthState.UNAVAILABLE,
        )
        self.assertEqual(
            provider_health.account_state(snapshot, "claude-shared", now=self.now),
            DeliveryHealthState.HEALTHY,
        )
        self.assertEqual(
            provider_health.delivery_health_block_reason(
                snapshot, endpoint_id="claude", account_id="claude-shared", now=self.now
            ),
            (None, False),
        )

    def test_quota_failure_blocks_account_but_preserves_endpoint_auth(self) -> None:
        reset = (self.now + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        snapshot = provider_health.apply_probe(
            provider_health.empty_delivery_health(),
            endpoint_id="codex1-2",
            account_id="codex1",
            probe={
                "source": "live",
                "ready": False,
                "status": "quota_reached",
                "quota_reset_at": reset,
            },
            observed_at=self.now,
        )

        self.assertEqual(
            provider_health.endpoint_state(snapshot, "codex1-2", now=self.now),
            DeliveryHealthState.HEALTHY,
        )
        self.assertEqual(
            provider_health.account_state(snapshot, "codex1", now=self.now),
            DeliveryHealthState.RETRY_AFTER,
        )
        self.assertEqual(
            provider_health.delivery_health_block_reason(
                snapshot, endpoint_id="codex1-2", account_id="codex1", now=self.now
            ),
            ("account_retry_after", False),
        )

    def test_expired_or_missing_evidence_demands_one_fresh_observation(self) -> None:
        snapshot = provider_health.apply_probe(
            provider_health.empty_delivery_health(),
            endpoint_id="codex1-2",
            account_id="codex1",
            probe={"source": "live", "ready": True, "status": "ready"},
            observed_at=self.now,
            valid_for_seconds=60,
        )
        later = self.now + timedelta(seconds=61)

        self.assertEqual(
            provider_health.endpoint_state(snapshot, "codex1-2", now=later),
            DeliveryHealthState.UNKNOWN,
        )
        self.assertEqual(
            provider_health.delivery_health_block_reason(
                snapshot, endpoint_id="codex1-2", account_id="codex1", now=later
            ),
            ("account_health_stale", True),
        )

    def test_cached_probe_never_becomes_delivery_evidence(self) -> None:
        snapshot = provider_health.apply_probe(
            provider_health.empty_delivery_health(),
            endpoint_id="codex1-2",
            account_id="codex1",
            probe={"source": "cached", "ready": True, "status": "ready"},
            observed_at=self.now,
        )

        self.assertEqual(
            provider_health.delivery_health_block_reason(
                snapshot, endpoint_id="codex1-2", account_id="codex1", now=self.now
            ),
            ("account_health_stale", True),
        )


if __name__ == "__main__":
    unittest.main()
