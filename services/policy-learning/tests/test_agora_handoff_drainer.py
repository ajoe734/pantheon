"""Tests for L12-MFC-R4-AGORA-001: Agora handoff drainer and single intake path."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add services/policy-learning to path for hyphenated folder import if needed
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agora_dataset_authority import (
    AgoraAuthorityUnavailable,
    AgoraDatasetAuthority,
)
from agora_handoff_drainer import process_drainer_cycle
from agora_handoff_drainer import DrainerRequestError


class TestAgoraDatasetAuthorityScannerToggle(unittest.TestCase):
    def test_scanner_disabled_via_env(self) -> None:
        with patch.dict(os.environ, {"AGORA_DATASET_STORE_SCANNER_ENABLED": "false", "AGORA_DATASET_STORE_BACKEND": "postgres"}):
            auth = AgoraDatasetAuthority(dsn="postgresql://user:pass@localhost:5432/db")
            self.assertEqual(auth.backend, "disabled")
            with self.assertRaises(AgoraAuthorityUnavailable) as ctx:
                auth._select(tenant_id="tenant-1", user_id="user-1", dataset_kind="learn")
            self.assertEqual(ctx.exception.reason, "agora_scanner_disabled")

    def test_scanner_enabled_default(self) -> None:
        with patch.dict(os.environ, {"AGORA_DATASET_STORE_BACKEND": "memory"}):
            auth = AgoraDatasetAuthority(records=[])
            self.assertEqual(auth.backend, "memory")


class TestAgoraHandoffDrainer(unittest.TestCase):
    @patch("agora_handoff_drainer.fetch_pending_handoffs")
    @patch("agora_handoff_drainer.post_handoff_to_policy_learning")
    @patch("agora_handoff_drainer.acknowledge_agora_handoff")
    def test_drainer_cycle_success(
        self, mock_ack: MagicMock, mock_post: MagicMock, mock_fetch: MagicMock
    ) -> None:
        mock_fetch.return_value = [
            {
                "handoff_id": "h-001",
                "dataset_version_id": "ver-001",
                "dataset_ref": {"dataset_version_id": "ver-001", "tenant_id": "tenant-test"},
                "acknowledged": False,
            }
        ]
        mock_post.return_value = {
            "status": "acknowledged",
            "candidate_id": "c-001",
            "dataset_version_id": "ver-001",
        }
        mock_ack.return_value = {"status": "acknowledged", "idempotent": False}

        res = process_drainer_cycle(
            agora_url="http://agora-bff:8000",
            policy_learning_url="http://policy-learning:8100",
            tenant_id="tenant-test",
            agora_token="agora-test-token",
            policy_learning_token="policy-test-token",
        )

        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["processed_count"], 1)
        self.assertEqual(res["acked_count"], 1)
        mock_post.assert_called_once()
        mock_ack.assert_called_once()

    @patch("agora_handoff_drainer.fetch_pending_handoffs")
    def test_fetch_failure_is_not_reported_as_empty_success(
        self,
        mock_fetch: MagicMock,
    ) -> None:
        mock_fetch.side_effect = DrainerRequestError("BFF rejected service token")

        res = process_drainer_cycle(
            agora_url="http://agora-bff:8000",
            policy_learning_url="http://policy-learning:8100",
            tenant_id="tenant-test",
            agora_token="agora-test-token",
            policy_learning_token="policy-test-token",
        )

        self.assertEqual(res["status"], "error")
        self.assertEqual(res["reason"], "agora_handoff_fetch_failed")
        self.assertEqual(res["processed_count"], 0)

    def test_configuration_refuses_cross_service_token_reuse(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AGORA_HANDOFF_SERVICE_TOKEN": "same-token",
                "POLICY_LEARNING_SERVICE_TOKEN": "same-token",
                "POLICY_LEARNING_AGORA_TENANT_ID": "tenant-test",
            },
            clear=False,
        ):
            from agora_handoff_drainer import (
                DrainerConfigurationError,
                require_drainer_configuration,
            )

            with self.assertRaises(DrainerConfigurationError):
                require_drainer_configuration()


if __name__ == "__main__":
    unittest.main()
