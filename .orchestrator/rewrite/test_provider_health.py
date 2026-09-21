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

    def test_refresh_retry_is_endpoint_local_and_does_not_prove_credentials(self) -> None:
        original = provider_health.apply_probe(None, endpoint_id="claude", account_id="shared",
            probe={"source": "live", "ready": True}, observed_at=self.now)
        retry_at = (self.now + timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
        result = provider_health.apply_probe(original, endpoint_id="claude", account_id="shared",
            probe={"source": "live", "ready": False, "status": "auth_retry_after", "retry_at": retry_at}, observed_at=self.now)
        self.assertEqual(result["accounts"], original["accounts"])
        self.assertEqual(provider_health.endpoint_state(result, "claude", now=self.now), DeliveryHealthState.RETRY_AFTER)
        self.assertEqual(result["endpoints"]["claude"]["retry_at"], retry_at)
        self.assertIsNone(result["endpoints"]["claude"]["valid_until"])
        self.assertEqual(provider_health.endpoint_state(result, "claude", now=self.now + timedelta(seconds=121)), DeliveryHealthState.UNKNOWN)
        self.assertEqual(original["endpoints"]["claude"]["state"], "healthy")
        default_retry = provider_health.apply_probe(None, endpoint_id="claude", account_id="shared",
            probe={"source": "live", "ready": False, "status": "auth_retry_after"}, observed_at=self.now)
        self.assertEqual(default_retry["accounts"], {})
        self.assertEqual(default_retry["endpoints"]["claude"]["retry_at"], (self.now + timedelta(seconds=60)).isoformat().replace("+00:00", "Z"))

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

    def test_cached_probe_never_becomes_delivery_evidence(self) -> None:
        snapshot = provider_health.apply_probe(
            provider_health.empty_delivery_health(),
            endpoint_id="codex1-2",
            account_id="codex1",
            probe={"source": "cached", "ready": True, "status": "ready"},
            observed_at=self.now,
        )

        self.assertEqual(
            provider_health.endpoint_state(snapshot, "codex1-2", now=self.now),
            DeliveryHealthState.UNKNOWN,
        )
        self.assertEqual(
            provider_health.account_state(snapshot, "codex1", now=self.now),
            DeliveryHealthState.UNKNOWN,
        )

    def test_auth_only_probe_preserves_existing_quota_terminal_account_state(self) -> None:
        reset_time = self.now + timedelta(days=1)
        reset_iso = reset_time.isoformat().replace("+00:00", "Z")
        failure_detail = (
            'Claude runs at 04:15 and 06:37 UTC returned a rejected seven_day rate_limit_event '
            'and "You\'ve hit your weekly limit" with reset 2026-09-21 12:00 UTC.'
        )
        snapshot = provider_health.apply_failure(
            provider_health.empty_delivery_health(),
            endpoint_id="claude",
            account_id="claude-shared",
            failure_kind="quota_terminal",
            observed_at=self.now - timedelta(hours=2),
            retry_at=reset_time,
            detail=failure_detail,
        )
        self.assertEqual(
            provider_health.account_state(snapshot, "claude-shared", now=self.now),
            DeliveryHealthState.RETRY_AFTER,
        )
        self.assertEqual(snapshot["accounts"]["claude-shared"]["quota_reset_at"], reset_iso)

        # Successful auth probe arrives at 06:35:
        auth_probe = {
            "source": "live",
            "ready": True,
            "status": "ready",
            "method": "claude_auth_status_refresh",
            "checked_at": self.now.isoformat().replace("+00:00", "Z"),
        }
        post_probe = provider_health.apply_probe(
            snapshot,
            endpoint_id="claude",
            account_id="claude-shared",
            probe=auth_probe,
            observed_at=self.now,
        )
        # Endpoint credential is refreshed healthy:
        self.assertEqual(
            provider_health.endpoint_state(post_probe, "claude", now=self.now),
            DeliveryHealthState.HEALTHY,
        )
        # Account capacity remains quota_terminal in RETRY_AFTER:
        self.assertEqual(
            provider_health.account_state(post_probe, "claude-shared", now=self.now),
            DeliveryHealthState.RETRY_AFTER,
        )
        account_entry = post_probe["accounts"]["claude-shared"]
        self.assertEqual(account_entry["reason_kind"], "quota_terminal")
        self.assertEqual(account_entry["retry_at"], reset_iso)
        self.assertEqual(account_entry["quota_reset_at"], reset_iso)
        self.assertEqual(account_entry["detail"], failure_detail)

    def test_auth_only_probe_does_not_mark_empty_account_healthy(self) -> None:
        auth_probe = {
            "source": "live",
            "ready": True,
            "status": "ready",
            "method": "claude_auth_status_refresh",
            "checked_at": self.now.isoformat().replace("+00:00", "Z"),
        }
        snapshot = provider_health.apply_probe(
            provider_health.empty_delivery_health(),
            endpoint_id="claude",
            account_id="claude-shared",
            probe=auth_probe,
            observed_at=self.now,
        )
        self.assertEqual(
            provider_health.endpoint_state(snapshot, "claude", now=self.now),
            DeliveryHealthState.HEALTHY,
        )
        self.assertEqual(
            provider_health.account_state(snapshot, "claude-shared", now=self.now),
            DeliveryHealthState.UNKNOWN,
        )

    def test_capacity_probe_restores_quota_terminal_account(self) -> None:
        reset_time = self.now + timedelta(days=1)
        snapshot = provider_health.apply_failure(
            provider_health.empty_delivery_health(),
            endpoint_id="claude",
            account_id="claude-shared",
            failure_kind="quota_terminal",
            observed_at=self.now - timedelta(hours=2),
            retry_at=reset_time,
        )
        capacity_probe = {
            "source": "live",
            "ready": True,
            "status": "ready",
            "method": "claude_prompt",
            "checked_at": self.now.isoformat().replace("+00:00", "Z"),
        }
        post_probe = provider_health.apply_probe(
            snapshot,
            endpoint_id="claude",
            account_id="claude-shared",
            probe=capacity_probe,
            observed_at=self.now,
        )
        self.assertEqual(
            provider_health.endpoint_state(post_probe, "claude", now=self.now),
            DeliveryHealthState.HEALTHY,
        )
        self.assertEqual(
            provider_health.account_state(post_probe, "claude-shared", now=self.now),
            DeliveryHealthState.HEALTHY,
        )

    def test_apply_failure_extracts_and_preserves_quota_reset_evidence(self) -> None:
        detail = (
            'Claude runs at 04:15 and 06:37 UTC returned a rejected seven_day rate_limit_event '
            'and "You\'ve hit your weekly limit" with reset 2026-09-21 12:00 UTC.'
        )
        snapshot = provider_health.apply_failure(
            provider_health.empty_delivery_health(),
            endpoint_id="claude",
            account_id="claude-shared",
            failure_kind="quota_terminal",
            observed_at=self.now,
            detail=detail,
        )
        entry = snapshot["accounts"]["claude-shared"]
        self.assertEqual(entry["quota_reset_at"], "2026-09-21T12:00:00Z")
        self.assertEqual(entry["retry_at"], "2026-09-21T12:00:00Z")

    def test_apply_probe_quota_failure_extracts_reset_timestamp(self) -> None:
        detail = 'Usage limit reached with try again at 2026-09-22 08:30:00 UTC.'
        snapshot = provider_health.apply_probe(
            provider_health.empty_delivery_health(),
            endpoint_id="claude",
            account_id="claude-shared",
            probe={
                "source": "live",
                "ready": False,
                "status": "quota_reached",
                "error": detail,
            },
            observed_at=self.now,
        )
        self.assertEqual(
            provider_health.endpoint_state(snapshot, "claude", now=self.now),
            DeliveryHealthState.HEALTHY,
        )
        self.assertEqual(
            provider_health.account_state(snapshot, "claude-shared", now=self.now),
            DeliveryHealthState.RETRY_AFTER,
        )
        entry = snapshot["accounts"]["claude-shared"]
        self.assertEqual(entry["quota_reset_at"], "2026-09-22T08:30:00Z")
        self.assertEqual(entry["retry_at"], "2026-09-22T08:30:00Z")

    def test_relative_reset_horizon_is_recorded_from_worker_failure(self) -> None:
        snapshot = provider_health.apply_failure(
            provider_health.empty_delivery_health(),
            endpoint_id="antigravity",
            account_id="antigravity",
            failure_kind="quota_terminal",
            observed_at=self.now,
            detail="RESOURCE_EXHAUSTED: Individual quota reached. Resets in 39h.",
        )

        entry = snapshot["accounts"]["antigravity"]
        expected = (self.now + timedelta(hours=39)).isoformat().replace("+00:00", "Z")
        self.assertEqual(entry["quota_reset_at"], expected)
        self.assertEqual(entry["retry_at"], expected)

    def test_generic_capacity_probe_keeps_prior_reset_horizon(self) -> None:
        snapshot = provider_health.apply_failure(
            provider_health.empty_delivery_health(),
            endpoint_id="antigravity",
            account_id="antigravity",
            failure_kind="quota_terminal",
            observed_at=self.now,
            detail="RESOURCE_EXHAUSTED: Individual quota reached. Resets in 39h.",
        )

        post_probe = provider_health.apply_probe(
            snapshot,
            endpoint_id="antigravity2",
            account_id="antigravity",
            probe={
                "source": "live",
                "ready": False,
                "status": "rotation_models_cooling",
                "error": "Every Antigravity rotation model is cooling after quota exhaustion.",
            },
            observed_at=self.now + timedelta(minutes=5),
        )

        entry = post_probe["accounts"]["antigravity"]
        expected = (self.now + timedelta(hours=39)).isoformat().replace("+00:00", "Z")
        self.assertEqual(entry["reason_kind"], "quota_terminal")
        self.assertEqual(entry["quota_reset_at"], expected)
        self.assertEqual(entry["retry_at"], expected)

    def test_extract_reset_timestamp_preserves_timezone_offsets(self) -> None:
        self.assertEqual(
            provider_health._extract_reset_timestamp("reset 2026-09-21 12:00 +08:00"),
            "2026-09-21T04:00:00Z",
        )
        self.assertEqual(
            provider_health._extract_reset_timestamp("reset 2026-09-21T12:00+08:00"),
            "2026-09-21T04:00:00Z",
        )
        self.assertEqual(
            provider_health._extract_reset_timestamp("reset 2026-09-21 12:00 -05:00"),
            "2026-09-21T17:00:00Z",
        )
        self.assertEqual(
            provider_health._extract_reset_timestamp("reset 2026-09-21T12:00-05:00"),
            "2026-09-21T17:00:00Z",
        )
        snapshot = provider_health.apply_failure(
            provider_health.empty_delivery_health(),
            endpoint_id="claude",
            account_id="claude-shared",
            failure_kind="quota_terminal",
            observed_at=self.now,
            detail='Hit weekly limit with reset 2026-09-21 12:00 +08:00',
        )
        entry = snapshot["accounts"]["claude-shared"]
        self.assertEqual(entry["quota_reset_at"], "2026-09-21T04:00:00Z")
        self.assertEqual(entry["retry_at"], "2026-09-21T04:00:00Z")


if __name__ == "__main__":
    unittest.main()
