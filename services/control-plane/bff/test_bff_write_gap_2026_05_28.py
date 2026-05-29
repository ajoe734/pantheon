"""Contract tests for BFF write-gap closure — 2026-05-28.

Task:     BFF-WRITE-P0-LIFECYCLE-003
Card:     P0-3 — POST /bff/runtimes/{id}/actions/StartRuntime
Spec:     docs/04/pantheon_bff_write_gap_2026-05-28/BFF_WRITE_GAP_SPEC.md §Card P0-3

Acceptance criteria:
  1. CommandType.START_RUNTIME is defined and maps to "StartRuntime".
  2. StartRuntime catalog entry exists with correct governance metadata:
     - entity_type="Runtime", risk_level=high, requires_confirm_token=True,
       requires_two_man=True, "runtime_operator" in required_roles.
  3. _execute_start_runtime dispatches POST to
     /api/internal/v1/runtimes/{runtime_id}/start and returns the Pack D
     202 envelope fields (commandId, runtime_id, state="starting").
  4. Missing confirm_token raises ValueError (VALIDATION_FAILED gate).
  5. Missing runtime_id raises ValueError.
  6. two_man_token is forwarded when present; absent when not provided.
  7. execute_command dispatches CommandType.START_RUNTIME without raising
     NoExecutorError.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

from models import CommandType, RiskLevel
from action_catalog import get_catalog_entry, catalog_action_ids
from command_executor import _execute_start_runtime, execute_command


class TestStartRuntimeCommandType(unittest.TestCase):
    """CommandType enum registration."""

    def test_start_runtime_enum_value(self) -> None:
        self.assertEqual(CommandType.START_RUNTIME.value, "StartRuntime")

    def test_start_runtime_in_enum(self) -> None:
        values = {ct.value for ct in CommandType}
        self.assertIn("StartRuntime", values)


class TestStartRuntimeCatalogEntry(unittest.TestCase):
    """Action catalog registration and governance metadata."""

    def setUp(self) -> None:
        self.entry = get_catalog_entry("StartRuntime")

    def test_entry_exists(self) -> None:
        self.assertIsNotNone(self.entry, "StartRuntime must be in action catalog")

    def test_entity_type(self) -> None:
        self.assertEqual(self.entry.entity_type, "Runtime")

    def test_risk_level_high(self) -> None:
        self.assertEqual(self.entry.risk_level, RiskLevel.HIGH)

    def test_requires_confirm_token(self) -> None:
        self.assertTrue(self.entry.requires_confirm_token)

    def test_requires_two_man(self) -> None:
        # Two-man required for live runtimes; catalog marks it True (BFF
        # precondition layer enforces conditionally on runtime_kind).
        self.assertTrue(self.entry.requires_two_man)

    def test_runtime_operator_in_required_roles(self) -> None:
        self.assertIn("runtime_operator", self.entry.required_roles)

    def test_live_owner_approver_in_required_roles(self) -> None:
        self.assertIn("live_owner_approver", self.entry.required_roles)

    def test_idempotency_required(self) -> None:
        self.assertTrue(self.entry.idempotency_required)

    def test_cooldown_nonzero(self) -> None:
        # Card P0-3 cooldown: 60s
        self.assertGreater(self.entry.cooldown_seconds, 0)

    def test_endpoint_references_runtimes_and_start_runtime(self) -> None:
        self.assertIn("runtimes", self.entry.endpoint)
        self.assertIn("StartRuntime", self.entry.endpoint)

    def test_catalog_action_ids_includes_start_runtime(self) -> None:
        self.assertIn("StartRuntime", catalog_action_ids())


class TestStartRuntimeCommandTypeFullCoverage(unittest.TestCase):
    """Every CommandType must have a catalog entry (existing contract)."""

    def test_start_runtime_catalogued(self) -> None:
        catalogued = catalog_action_ids()
        self.assertIn(CommandType.START_RUNTIME.value, catalogued)


class TestExecuteStartRuntime(unittest.TestCase):
    """_execute_start_runtime unit tests."""

    def setUp(self) -> None:
        os.environ["PANTHEON_INTERNAL_API_URL"] = "http://localhost:5001"

    @patch("command_executor._post_json")
    def test_success_returns_202_envelope(self, mock_post) -> None:
        mock_post.return_value = {
            "runtime_id": "rt-abc-001",
            "status": "accepted",
            "state": "starting",
            "audit_id": "audit-rt-abc-001",
            "started_at": "2026-05-28T00:00:00Z",
        }
        result = _execute_start_runtime(
            "cmd-rt-001",
            {"runtime_id": "rt-abc-001", "confirm_token": "tok-dev-001"},
        )
        self.assertEqual(result["command_id"], "cmd-rt-001")
        self.assertEqual(result["runtime_id"], "rt-abc-001")
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["state"], "starting")
        self.assertEqual(result["audit_id"], "audit-rt-abc-001")
        mock_post.assert_called_once()

    @patch("command_executor._post_json")
    def test_correct_url_called(self, mock_post) -> None:
        mock_post.return_value = {
            "runtime_id": "rt-xyz-002",
            "status": "accepted",
            "state": "starting",
        }
        _execute_start_runtime(
            "cmd-rt-002",
            {"runtime_id": "rt-xyz-002", "confirm_token": "tok-dev-002"},
        )
        called_url = mock_post.call_args[0][0]
        self.assertIn("/api/internal/v1/runtimes/rt-xyz-002/start", called_url)

    @patch("command_executor._post_json")
    def test_two_man_token_forwarded_when_present(self, mock_post) -> None:
        mock_post.return_value = {
            "runtime_id": "rt-live-003",
            "status": "accepted",
            "state": "starting",
        }
        result = _execute_start_runtime(
            "cmd-rt-003",
            {
                "runtime_id": "rt-live-003",
                "confirm_token": "tok-live-003",
                "two_man_token": "2man-sig-abc",
            },
        )
        self.assertEqual(result["two_man_token"], "2man-sig-abc")
        payload_sent = mock_post.call_args[0][1]
        self.assertEqual(payload_sent["two_man_token"], "2man-sig-abc")

    @patch("command_executor._post_json")
    def test_two_man_token_absent_when_not_provided(self, mock_post) -> None:
        mock_post.return_value = {
            "runtime_id": "rt-paper-004",
            "status": "accepted",
            "state": "starting",
        }
        result = _execute_start_runtime(
            "cmd-rt-004",
            {"runtime_id": "rt-paper-004", "confirm_token": "tok-paper-004"},
        )
        self.assertIsNone(result["two_man_token"])
        payload_sent = mock_post.call_args[0][1]
        self.assertNotIn("two_man_token", payload_sent)

    def test_missing_runtime_id_raises_value_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _execute_start_runtime(
                "cmd-rt-missing",
                {"confirm_token": "tok-dev"},
            )
        self.assertIn("runtime_id", str(ctx.exception))

    def test_missing_confirm_token_raises_value_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _execute_start_runtime(
                "cmd-rt-no-token",
                {"runtime_id": "rt-001"},
            )
        self.assertIn("confirm_token", str(ctx.exception))

    @patch("command_executor._post_json")
    def test_default_state_is_starting_when_backend_omits_field(self, mock_post) -> None:
        mock_post.return_value = {"runtime_id": "rt-005", "status": "accepted"}
        result = _execute_start_runtime(
            "cmd-rt-005",
            {"runtime_id": "rt-005", "confirm_token": "tok-005"},
        )
        self.assertEqual(result["state"], "starting")


class TestExecuteCommandDispatchesStartRuntime(unittest.TestCase):
    """execute_command routes CommandType.START_RUNTIME to _execute_start_runtime."""

    def setUp(self) -> None:
        os.environ["PANTHEON_INTERNAL_API_URL"] = "http://localhost:5001"

    @patch("command_executor._post_json")
    def test_execute_command_start_runtime(self, mock_post) -> None:
        mock_post.return_value = {
            "runtime_id": "rt-dispatch-001",
            "status": "accepted",
            "state": "starting",
        }
        result = execute_command(
            "cmd-dispatch-001",
            CommandType.START_RUNTIME,
            {"runtime_id": "rt-dispatch-001", "confirm_token": "tok-dispatch-001"},
        )
        self.assertEqual(result["command_id"], "cmd-dispatch-001")
        self.assertEqual(result["state"], "starting")

    def test_no_executor_error_for_start_runtime(self) -> None:
        from command_executor import _EXECUTORS
        self.assertIn(CommandType.START_RUNTIME, _EXECUTORS)


if __name__ == "__main__":
    unittest.main()
