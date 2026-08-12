from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import provider_health
from provider_health import AccountHealth, DeliveryHealthState, FailureResponse


class ShouldPauseTests(unittest.TestCase):
    def test_pause_kinds(self) -> None:
        for kind in ("quota_terminal", "capacity", "capacity_retryable", "auth"):
            self.assertTrue(provider_health.should_pause(kind), kind)

    def test_non_pause_kinds(self) -> None:
        for kind in ("terminal", "transient", "unknown_critical", "tool_auth", "", "bogus", None):
            self.assertFalse(provider_health.should_pause(kind), kind)


class DecideFailureResponseTests(unittest.TestCase):
    def test_rotation_short_circuits_to_rotate(self) -> None:
        # even a pause kind rotates when rotation absorbed it
        self.assertEqual(
            provider_health.decide_failure_response("quota_terminal", rotation_outcome="rotated"),
            FailureResponse.ROTATE,
        )

    def test_pause_kinds_pause_without_rotation(self) -> None:
        for kind in ("quota_terminal", "capacity_retryable", "auth"):
            self.assertEqual(provider_health.decide_failure_response(kind), FailureResponse.PAUSE, kind)

    def test_exhausted_rotation_still_pauses(self) -> None:
        self.assertEqual(
            provider_health.decide_failure_response("quota_terminal", rotation_outcome="exhausted"),
            FailureResponse.PAUSE,
        )

    def test_transient_retries(self) -> None:
        self.assertEqual(provider_health.decide_failure_response("transient"), FailureResponse.RETRY)

    def test_terminal_reassigns(self) -> None:
        for kind in ("terminal", "unknown_critical", "tool_auth", "bogus"):
            self.assertEqual(provider_health.decide_failure_response(kind), FailureResponse.REASSIGN, kind)


class ClassifyHealthTests(unittest.TestCase):
    def test_auth_is_revoked(self) -> None:
        self.assertEqual(provider_health.classify_health("auth"), AccountHealth.REVOKED)
        self.assertEqual(provider_health.classify_health("tool_auth"), AccountHealth.REVOKED)

    def test_quota_capacity_degraded(self) -> None:
        for kind in ("quota_terminal", "capacity", "capacity_retryable"):
            self.assertEqual(provider_health.classify_health(kind), AccountHealth.DEGRADED, kind)

    def test_others_healthy(self) -> None:
        for kind in ("terminal", "transient", "unknown_critical", "", None):
            self.assertEqual(provider_health.classify_health(kind), AccountHealth.HEALTHY, kind)

    def test_authoritative_probe_promotes_health_before_dispatch(self) -> None:
        self.assertEqual(provider_health.classify_probe(True), AccountHealth.HEALTHY)
        self.assertEqual(provider_health.classify_probe(False), AccountHealth.REVOKED)
        self.assertEqual(
            provider_health.classify_probe(False, status="quota_reached"),
            AccountHealth.DEGRADED,
        )
        self.assertEqual(
            provider_health.classify_probe(False, status="probe_timeout"),
            AccountHealth.DEGRADED,
        )
        self.assertIsNone(provider_health.classify_probe(None))

    def test_quota_probe_status_has_a_quota_failure_kind(self) -> None:
        self.assertEqual(
            provider_health.classify_probe_failure_kind(False, status="quota_reached"),
            "quota_terminal",
        )
        self.assertEqual(
            provider_health.classify_probe_failure_kind(False, status="rotation_models_cooling"),
            "capacity_retryable",
        )
        self.assertEqual(
            provider_health.classify_probe_failure_kind(False, status="not_logged_in"),
            "auth",
        )
        self.assertIsNone(provider_health.classify_probe_failure_kind(True, status="ready"))


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


class SupervisorIntegrationTests(unittest.TestCase):
    def test_full_decision_routes_through_single_authority(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import supervisor

        cases = (
            ("quota_terminal", "rotated", FailureResponse.ROTATE),
            ("quota_terminal", "exhausted", FailureResponse.PAUSE),
            ("auth", "ineligible", FailureResponse.PAUSE),
            ("transient", "ineligible", FailureResponse.RETRY),
            ("terminal", "ineligible", FailureResponse.REASSIGN),
        )
        for kind, rotation_outcome, expected in cases:
            self.assertEqual(
                supervisor.decide_provider_failure_response(
                    kind,
                    rotation_outcome=rotation_outcome,
                ).value,
                expected.value,
            )


if __name__ == "__main__":
    unittest.main()
