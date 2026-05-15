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
    _execute_approve_decision,
    _execute_reject_decision,
    _execute_request_approval_revision,
    _execute_pause_runtime,
    _execute_escalate_diff,
    _execute_rollback,
    _execute_approve_rollback,
    _execute_reject_rollback,
    _execute_activate_kill_switch,
    _execute_approve_evolution_decision,
    _execute_approve_mutation,
    _execute_evolution_action,
    _execute_reject_mutation,
    _execute_remediate_sentinel_intervention,
    _execute_bff_action_adapter,
)


class TestApproveDeploymentExecutor(unittest.TestCase):
    def setUp(self):
        os.environ["PANTHEON_INTERNAL_API_URL"] = "http://localhost:5001"

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
    def setUp(self):
        os.environ["PANTHEON_INTERNAL_API_URL"] = "http://localhost:5001"

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


class TestApprovalDecisionExecutors(unittest.TestCase):
    def setUp(self):
        os.environ["PANTHEON_INTERNAL_API_URL"] = "http://localhost:5001"

    @patch("command_executor._post_json")
    def test_approve_decision_success(self, mock_post):
        mock_post.return_value = {
            "decision_id": "appr-001",
            "decision_state": "approved",
            "status": "submitted",
            "audit_id": "audit-appr-001",
            "approved_at": "2026-04-18T06:00:00Z",
        }
        result = _execute_approve_decision("cmd-approve-decision", {
            "decision_id": "appr-001",
            "approval_notes": "Looks good",
        })
        self.assertEqual(result["decision_id"], "appr-001")
        self.assertEqual(result["decision_state"], "approved")
        self.assertEqual(result["command_id"], "cmd-approve-decision")

    @patch("command_executor._post_json")
    def test_reject_decision_success(self, mock_post):
        mock_post.return_value = {
            "decision_id": "appr-001",
            "decision_state": "rejected",
            "status": "submitted",
            "audit_id": "audit-appr-001",
            "rejected_at": "2026-04-18T06:05:00Z",
        }
        result = _execute_reject_decision("cmd-reject-decision", {
            "decision_id": "appr-001",
            "rejection_reason": "Risk evidence insufficient",
        })
        self.assertEqual(result["decision_id"], "appr-001")
        self.assertEqual(result["decision_state"], "rejected")
        self.assertEqual(result["command_id"], "cmd-reject-decision")

    @patch("command_executor._post_json")
    def test_request_revision_success(self, mock_post):
        mock_post.return_value = {
            "decision_id": "appr-001",
            "decision_state": "pending_revision",
            "status": "submitted",
            "audit_id": "audit-appr-001",
            "requested_at": "2026-04-18T06:10:00Z",
        }
        result = _execute_request_approval_revision("cmd-request-revision", {
            "decision_id": "appr-001",
            "revision_notes": "Need clearer evidence links",
        })
        self.assertEqual(result["decision_id"], "appr-001")
        self.assertEqual(result["decision_state"], "pending_revision")
        self.assertEqual(result["command_id"], "cmd-request-revision")


class TestDeploymentDiffExecutor(unittest.TestCase):
    def setUp(self):
        os.environ["PANTHEON_INTERNAL_API_URL"] = "http://localhost:5001"

    @patch("command_executor._post_json")
    def test_escalate_diff_success(self, mock_post):
        mock_post.return_value = {
            "plan_id": "plan-dp-001",
            "status": "submitted",
            "audit_id": "audit-plan-dp-001",
            "escalated_at": "2026-04-18T06:20:00Z",
        }
        result = _execute_escalate_diff("cmd-escalate-diff", {
            "plan_id": "plan-dp-001",
            "escalation_reason": "Binding change requires committee review",
        })
        self.assertEqual(result["plan_id"], "plan-dp-001")
        self.assertEqual(result["command_id"], "cmd-escalate-diff")


class TestRollbackExecutor(unittest.TestCase):
    def setUp(self):
        os.environ["PANTHEON_INTERNAL_API_URL"] = "http://localhost:5001"

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


class TestRollbackReviewCommandExecutors(unittest.TestCase):
    def setUp(self):
        os.environ["PANTHEON_INTERNAL_API_URL"] = "http://localhost:5001"

    @patch("command_executor._post_json")
    def test_approve_rollback_success(self, mock_post):
        mock_post.return_value = {
            "rollback_id": "rollback-rb-001",
            "decision": "approved",
            "status": "submitted",
            "audit_id": "audit-rb-001",
            "approved_at": "2026-04-17T07:00:00Z",
        }
        result = _execute_approve_rollback("cmd-003a", {
            "rollback_id": "rollback-rb-001",
            "approval_notes": "Looks safe",
        })
        self.assertEqual(result["rollback_id"], "rollback-rb-001")
        self.assertEqual(result["decision"], "approved")
        self.assertEqual(result["command_id"], "cmd-003a")

    @patch("command_executor._post_json")
    def test_reject_rollback_success(self, mock_post):
        mock_post.return_value = {
            "rollback_id": "rollback-rb-001",
            "decision": "rejected",
            "status": "submitted",
            "audit_id": "audit-rb-001",
            "rejected_at": "2026-04-17T07:05:00Z",
        }
        result = _execute_reject_rollback("cmd-003b", {
            "rollback_id": "rollback-rb-001",
            "rejection_reason": "Impact summary insufficient",
        })
        self.assertEqual(result["rollback_id"], "rollback-rb-001")
        self.assertEqual(result["decision"], "rejected")
        self.assertEqual(result["command_id"], "cmd-003b")


class TestKillSwitchExecutor(unittest.TestCase):
    def setUp(self):
        os.environ["PANTHEON_INTERNAL_API_URL"] = "http://localhost:5001"

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
    def setUp(self):
        os.environ["PANTHEON_GOVERNANCE_API_URL"] = "http://localhost:5001"

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
    def setUp(self):
        os.environ["PANTHEON_GOVERNANCE_API_URL"] = "http://localhost:5001"

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

    @patch("command_executor._post_json")
    def test_execute_revalidate_action_preserves_research_dispatch_result(self, mock_post):
        mock_post.return_value = {
            "decision_id": "evo-reval-002",
            "action_type": "revalidate",
            "decision_state": "executed",
            "execution_result": {
                "status": "submitted",
                "plane": "research",
                "executed_at": "2026-05-15T12:05:00Z",
                "execution_ref_id": "dispatch-evo-reval-002",
            },
            "cooldown_ends_at": "2026-05-18T12:05:00Z",
            "observation_window_ends_at": "2026-05-22T12:05:00Z",
        }
        auth_token = "op-admin:admin"
        result = _execute_evolution_action("cmd-reval-006", {
            "evolution_decision_id": "evo-reval-002",
            "action_type": "revalidate",
            "note": "Execute approved revalidation",
        }, auth_token=auth_token)

        self.assertEqual(result["evolution_decision_id"], "evo-reval-002")
        self.assertEqual(result["action_type"], "revalidate")
        self.assertEqual(result["decision_state"], "executed")
        self.assertEqual(result["execution_ref_id"], "dispatch-evo-reval-002")
        self.assertEqual(result["execution_result"]["plane"], "research")
        mock_post.assert_called_once_with(
            "http://localhost:5001/api/evolution/proposals/evo-reval-002/execute",
            {
                "actor_id": "op-admin",
                "actor_role": "admin",
                "note": "Execute approved revalidation",
            },
            auth_token=auth_token,
            mfa_token=None,
        )


class TestMutationReviewExecutors(unittest.TestCase):
    def setUp(self):
        os.environ["PANTHEON_GOVERNANCE_API_URL"] = "http://localhost:5001"

    @patch("command_executor._post_json")
    def test_approve_mutation_governance_api(self, mock_post):
        mock_post.return_value = {
            "decision_id": "evo-dec-88f3a2c1",
            "decision_state": "approved",
            "approval_decision_id": "approval-777",
            "risk_level": "medium",
            "decided_at": "2026-04-19T06:00:00Z",
        }
        auth_token = "op-approver:approver"
        result = _execute_approve_mutation(
            "cmd-mutation-approve",
            {
                "decision_id": "evo-dec-88f3a2c1",
                "note": "Ready for final approval",
            },
            auth_token=auth_token,
        )
        self.assertEqual(result["decision_id"], "evo-dec-88f3a2c1")
        self.assertEqual(result["new_state"], "approved")
        self.assertTrue(result["command_accepted"])
        mock_post.assert_called_once_with(
            "http://localhost:5001/api/evolution/proposals/evo-dec-88f3a2c1/approve",
            {
                "actor_id": "op-approver",
                "actor_role": "approver",
                "note": "Ready for final approval",
            },
            auth_token=auth_token,
            mfa_token=None,
        )

    @patch("command_executor._post_json")
    def test_reject_mutation_governance_api(self, mock_post):
        mock_post.return_value = {
            "decision_id": "evo-dec-88f3a2c1",
            "decision_state": "rejected",
            "approval_decision_id": "approval-777",
            "risk_level": "medium",
            "decided_at": "2026-04-19T06:05:00Z",
        }
        auth_token = "op-operator:operator"
        result = _execute_reject_mutation(
            "cmd-mutation-reject",
            {
                "decision_id": "evo-dec-88f3a2c1",
                "note": "Risk evidence still incomplete",
            },
            auth_token=auth_token,
        )
        self.assertEqual(result["decision_id"], "evo-dec-88f3a2c1")
        self.assertEqual(result["new_state"], "rejected")
        self.assertTrue(result["command_accepted"])
        mock_post.assert_called_once_with(
            "http://localhost:5001/api/evolution/proposals/evo-dec-88f3a2c1/reject",
            {
                "actor_id": "op-operator",
                "actor_role": "operator",
                "note": "Risk evidence still incomplete",
            },
            auth_token=auth_token,
            mfa_token=None,
        )


class TestExecuteCommandDispatch(unittest.TestCase):
    def setUp(self):
        os.environ["PANTHEON_INTERNAL_API_URL"] = "http://localhost:5001"

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

    @patch("command_executor._post_json")
    def test_dispatch_approve_decision(self, mock_post):
        mock_post.return_value = {
            "decision_id": "appr-001",
            "decision_state": "approved",
            "status": "submitted",
        }
        result = execute_command("cmd-approval-queue", CommandType.APPROVE_DECISION, {
            "decision_id": "appr-001",
            "approval_notes": "Proceed",
        })
        self.assertEqual(result["decision_state"], "approved")

    @patch("command_executor._post_json")
    def test_dispatch_escalate_diff(self, mock_post):
        mock_post.return_value = {
            "plan_id": "plan-dp-001",
            "status": "submitted",
        }
        result = execute_command("cmd-deployment-diff", CommandType.ESCALATE_DIFF, {
            "plan_id": "plan-dp-001",
            "escalation_reason": "Diff exceeds policy threshold",
        })
        self.assertEqual(result["plan_id"], "plan-dp-001")

    def test_dispatch_unknown_command_type(self):
        with self.assertRaises(ValueError):
            execute_command("cmd-001", "FakeCommand", {})


class TestBffActionAdapterExecutor(unittest.TestCase):
    def test_records_final_command_source_without_deprecated_receipt(self):
        result = _execute_bff_action_adapter("cmd-action-final", {
            "action_id": "promote_paper",
            "entity_type": "strategy",
            "entity_id": "stg-024",
            "audit_event": "strategy.promote_paper",
            "frontend_source_route": "/bff/v1/commands",
        })

        self.assertEqual(result["source_route"], "/bff/v1/commands")
        self.assertFalse(result["deprecated_action_receipt"])
        self.assertFalse(result["live_capital_side_effects"])

    def test_marks_legacy_adapter_source_as_deprecated_receipt(self):
        result = _execute_bff_action_adapter("cmd-action-legacy", {
            "action_id": "submit_review",
            "entity_type": "strategy",
            "entity_id": "stg-024",
            "audit_event": "strategy.submit_review",
            "adapter_source_route": "POST /bff/actions/{entityType}/{entityId}/{actionId}",
        })

        self.assertEqual(
            result["source_route"],
            "POST /bff/actions/{entityType}/{entityId}/{actionId}",
        )
        self.assertTrue(result["deprecated_action_receipt"])


class TestExecuteCommandWithStatus(unittest.TestCase):
    def setUp(self):
        os.environ["PANTHEON_INTERNAL_API_URL"] = "http://localhost:5001"

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


class TestRemediateSentinelInterventionExecutor(unittest.TestCase):
    def setUp(self):
        os.environ["PANTHEON_INTERNAL_API_URL"] = "http://localhost:5001"

    @patch("command_executor._post_json")
    def test_remediate_sentinel_success(self, mock_post):
        """Executor forwards to sentinel endpoint and returns structured result."""
        mock_post.return_value = {
            "intervention_id": "intv-exec-001",
            "status": "remediated",
            "remediated_at": "2026-05-08T10:00:00Z",
            "two_man_signature_id": "tms-exec-001",
        }
        result = _execute_remediate_sentinel_intervention("cmd-sentinel-001", {
            "intervention_id": "intv-exec-001",
            "remediation_action": "resolve",
            "two_man_signature_id": "tms-exec-001",
        })
        self.assertEqual(result["command_id"], "cmd-sentinel-001")
        self.assertEqual(result["intervention_id"], "intv-exec-001")
        self.assertEqual(result["status"], "remediated")
        self.assertNotIn("stub", result)
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        self.assertIn("intv-exec-001", call_url)
        self.assertIn("/sentinel/interventions/", call_url)

    @patch("command_executor._post_json")
    def test_remediate_sentinel_downstream_failure_propagates(self, mock_post):
        """Downstream failure must propagate — no stub result returned."""
        import urllib.error
        mock_post.side_effect = urllib.error.URLError("Connection refused")
        with self.assertRaises(urllib.error.URLError):
            _execute_remediate_sentinel_intervention("cmd-sentinel-fail-001", {
                "intervention_id": "intv-fail-001",
                "remediation_action": "resolve",
                "two_man_signature_id": "tms-fail-001",
            })

    @patch("command_executor._post_json")
    def test_remediate_sentinel_downstream_failure_returns_failed_status(self, mock_post):
        """execute_command_with_status must return FAILED (not EXECUTED with stub) on downstream error."""
        import urllib.error
        mock_post.side_effect = urllib.error.URLError("Connection refused")
        status, result, error = execute_command_with_status(
            "cmd-sentinel-fail-002",
            CommandType.REMEDIATE_SENTINEL_INTERVENTION,
            {
                "intervention_id": "intv-fail-002",
                "remediation_action": "resolve",
                "two_man_signature_id": "tms-fail-002",
            },
        )
        self.assertEqual(status, CommandStatus.FAILED)
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(error["code"], "DOWNSTREAM_UNAVAILABLE")

    def test_remediate_sentinel_missing_intervention_id_raises(self):
        """Executor raises ValueError when intervention_id is absent."""
        with self.assertRaises(ValueError):
            _execute_remediate_sentinel_intervention("cmd-sentinel-bad-001", {
                "remediation_action": "resolve",
                "two_man_signature_id": "tms-bad-001",
            })

    @patch("command_executor._post_json")
    def test_remediate_sentinel_result_has_no_stub_field(self, mock_post):
        """Successful result must not carry a stub flag."""
        mock_post.return_value = {
            "intervention_id": "intv-nostub-001",
            "status": "remediated",
            "remediated_at": "2026-05-08T10:00:00Z",
        }
        result = _execute_remediate_sentinel_intervention("cmd-nostub-001", {
            "intervention_id": "intv-nostub-001",
            "remediation_action": "resolve",
            "two_man_signature_id": "tms-nostub-001",
        })
        self.assertNotIn("stub", result)


if __name__ == "__main__":
    unittest.main()
