from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import provider_health
from provider_health import AccountHealth, FailureResponse


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
