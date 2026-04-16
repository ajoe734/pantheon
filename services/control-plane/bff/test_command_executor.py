"""Unit tests for command_executor module."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

from models import CommandStatus, CommandType
from command_executor import (
    execute_command,
    execute_command_with_status,
    _execute_approve_deployment,
    _execute_pause_runtime,
    _execute_rollback,
    _execute_activate_kill_switch,
    _execute_approve_evolution_decision,
    _execute_evolution_action,
)


class TestApproveDeploymentExecutor(unittest.TestCase):
    @patch("command_executor._post_json")
    def test_approve_deployment_success(self, mock_post):
        mock_post.return_value = {
            "approval_decision_id": "ad-dp-001-123456",
            "target_plan_id": "dp-001",
            "state_after": "approved",
            "audit_id": "audit-ad-dp-001-123456",
            "verification_timestamp": "2026-04-11T12:00:00Z",
        }
        result = _execute_approve_deployment("cmd-001", {
            "deployment_plan_id": "dp-001",
            "approval_decision": "approve",
        })
        self.assertEqual(result["approval_decision_id"], "ad-dp-001-123456")
        self.assertEqual(result["state_after"], "approved")
        self.assertEqual(result["command_id"], "cmd-001")
        mock_post.assert_called_once()


class TestPauseRuntimeExecutor(unittest.TestCase):
    @patch("command_executor._post_json")
    def test_pause_runtime_success(self, mock_post):
        mock_post.return_value = {
            "runtime_binding_id": "rt-001",
            "pause_expires_at": "2026-04-11T13:00:00Z",
            "status": "submitted",
            "duration_seconds": 3600,
            "reason": "maintenance",
        }
        result = _execute_pause_runtime("cmd-002", {
            "binding_id": "rt-001",
            "duration_seconds": 3600,
            "reason": "maintenance",
        })
        self.assertEqual(result["runtime_binding_id"], "rt-001")
        self.assertEqual(result["command_id"], "cmd-002")


class TestRollbackExecutor(unittest.TestCase):
    @patch("command_executor._post_json")
    def test_rollback_success(self, mock_post):
        mock_post.return_value = {
            "rollback_id": "rb-dp-001-123456",
            "status": "submitted",
            "tracking_url": "/api/internal/v1/commands/cmd-rb-dp-001-123456",
        }
        result = _execute_rollback("cmd-003", {
            "rollback_target_type": "deployment",
            "target_id": "dp-001",
            "rollback_to_version": "v1.0.0",
        })
        self.assertEqual(result["rollback_id"], "rb-dp-001-123456")
        self.assertEqual(result["command_id"], "cmd-003")


class TestKillSwitchExecutor(unittest.TestCase):
    @patch("command_executor._post_json")
    def test_kill_switch_success(self, mock_post):
        mock_post.return_value = {
            "kill_switch_order_id": "ks-123456",
            "action": "activate",
            "scope": "all",
            "status": "submitted",
        }
        result = _execute_activate_kill_switch("cmd-004", {
            "scope": "all",
            "severity": "critical",
        })
        self.assertEqual(result["kill_switch_order_id"], "ks-123456")
        self.assertEqual(result["command_id"], "cmd-004")


class TestEvolutionDecisionExecutor(unittest.TestCase):
    @patch("command_executor._post_json")
    def test_approve_evolution_decision_governance_api(self, mock_post):
        mock_post.return_value = {
            "decision_id": "evo-001",
            "decision_state": "approved",
            "approval_decision_id": "approval-777",
            "risk_level": "medium",
        }
        auth_token = "op-reviewer:reviewer"
        result = _execute_approve_evolution_decision("cmd-005", {
            "evolution_decision_id": "evo-001",
            "approval_action": "approve",
            "rationale": "Looks good",
        }, auth_token=auth_token)
        self.assertEqual(result["evolution_decision_id"], "evo-001")
        self.assertEqual(result["command_id"], "cmd-005")
        self.assertEqual(result["decision_state"], "approved")
        self.assertEqual(result["approval_decision_id"], "approval-777")
        mock_post.assert_called_once_with(
            "http://localhost:5001/api/evolution/proposals/evo-001/approve",
            {
                "actor_id": "op-reviewer",
                "actor_role": "reviewer",
                "note": "Looks good",
            },
            auth_token=auth_token,
            mfa_token=None,
        )


class TestEvolutionActionExecutor(unittest.TestCase):
    @patch("command_executor._post_json")
    def test_execute_evolution_action_governance_api(self, mock_post):
        mock_post.return_value = {
            "decision_id": "evo-002",
            "action_type": "deploy",
            "decision_state": "executed",
            "execution_result": {
                "status": "submitted",
                "plane": "governance",
                "executed_at": "2026-04-11T12:05:00Z",
                "execution_ref_id": "exec-002",
            },
            "cooldown_ends_at": "2026-04-12T12:05:00Z",
        }
        auth_token = "op-admin:admin"
        result = _execute_evolution_action("cmd-006", {
            "evolution_decision_id": "evo-002",
            "action_type": "deploy",
            "has_active_runtime": True,
            "active_binding_id": "rb-002",
            "freeze_mode": "governance_only",
            "note": "Execute approved evolution action",
        }, auth_token=auth_token)
        self.assertEqual(result["evolution_decision_id"], "evo-002")
        self.assertEqual(result["action_type"], "deploy")
        self.assertEqual(result["command_id"], "cmd-006")
        self.assertEqual(result["decision_state"], "executed")
        self.assertEqual(result["execution_ref_id"], "exec-002")
        mock_post.assert_called_once_with(
            "http://localhost:5001/api/evolution/proposals/evo-002/execute",
            {
                "actor_id": "op-admin",
                "actor_role": "admin",
                "has_active_runtime": True,
                "active_binding_id": "rb-002",
                "freeze_mode": "governance_only",
                "note": "Execute approved evolution action",
            },
            auth_token=auth_token,
            mfa_token=None,
        )


class TestExecuteCommandDispatch(unittest.TestCase):
    @patch("command_executor._post_json")
    def test_dispatch_approve_deployment(self, mock_post):
        mock_post.return_value = {
            "approval_decision_id": "ad-001",
            "target_plan_id": "dp-001",
            "state_after": "approved",
            "audit_id": "audit-001",
            "verification_timestamp": "2026-04-11T12:00:00Z",
        }
        result = execute_command("cmd-001", CommandType.APPROVE_DEPLOYMENT, {
            "deployment_plan_id": "dp-001",
            "approval_decision": "approve",
        })
        self.assertEqual(result["state_after"], "approved")

    def test_dispatch_unknown_command_type(self):
        with self.assertRaises(ValueError):
            execute_command("cmd-001", "FakeCommand", {})


class TestExecuteCommandWithStatus(unittest.TestCase):
    @patch("command_executor._post_json")
    def test_success_returns_executed(self, mock_post):
        mock_post.return_value = {
            "approval_decision_id": "ad-001",
            "target_plan_id": "dp-001",
            "state_after": "approved",
            "audit_id": "audit-001",
            "verification_timestamp": "2026-04-11T12:00:00Z",
        }
        status, result, error = execute_command_with_status(
            "cmd-001", CommandType.APPROVE_DEPLOYMENT, {
                "deployment_plan_id": "dp-001",
                "approval_decision": "approve",
            }
        )
        self.assertEqual(status, CommandStatus.EXECUTED)
        self.assertIsNotNone(result)
        self.assertIsNone(error)
        self.assertIn("execution_started_at", result)
        self.assertIn("execution_completed_at", result)

    @patch("command_executor._post_json")
    def test_http_error_returns_failed(self, mock_post):
        import urllib.error
        mock_post.side_effect = urllib.error.HTTPError(
            "http://localhost:5001/api/internal/v1/deployments/dp-001/approve",
            500, "Internal Server Error", {}, None
        )
        status, result, error = execute_command_with_status(
            "cmd-002", CommandType.APPROVE_DEPLOYMENT, {
                "deployment_plan_id": "dp-001",
                "approval_decision": "approve",
            }
        )
        self.assertEqual(status, CommandStatus.FAILED)
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(error["code"], "DOWNSTREAM_ERROR")

    @patch("command_executor._post_json")
    def test_url_error_returns_failed(self, mock_post):
        import urllib.error
        mock_post.side_effect = urllib.error.URLError("Connection refused")
        status, result, error = execute_command_with_status(
            "cmd-003", CommandType.APPROVE_DEPLOYMENT, {
                "deployment_plan_id": "dp-001",
                "approval_decision": "approve",
            }
        )
        self.assertEqual(status, CommandStatus.FAILED)
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(error["code"], "DOWNSTREAM_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
