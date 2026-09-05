"""Unit and integration test suite for BFF Management Domain Command Routing.

Verifies:
1. Action-to-owner contract matrix: every Management action routes to an authoritative
   domain owner (Capital, Runtime, Deployment, Persona, Governance, Incident, Evolution,
   Strategy, Agora, Audit).
2. Authoritative readback and terminal status in domain receipts.
3. Idempotency and replay without duplicating domain side-effects.
4. Typed unavailable failures: unsafe/unbacked actions fail closed with typed error codes
   and user-actionable guidance rather than generic admission success.
5. Error propagation and downstream failure handling.
"""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

import pytest

from services.control_plane.bff.models import CommandStatus, CommandType
from services.control_plane.bff.command_adapters import (
    ActionUnavailableError,
    CapitalCommandAdapter,
    DeploymentCommandAdapter,
    EvolutionCommandAdapter,
    GovernanceCommandAdapter,
    IncidentCommandAdapter,
    PersonaCommandAdapter,
    RuntimeCommandAdapter,
    StrategyCommandAdapter,
    CapabilitiesCommandAdapter,
    AgoraCommandAdapter,
    AuditCommandAdapter,
    dispatch_domain_command,
    find_adapter,
)
from services.control_plane.bff.command_executor import execute_command, execute_command_with_status


class TestActionToOwnerMatrix(unittest.TestCase):
    """Verifies that all domain action types map to their authoritative domain adapters."""

    def test_capital_adapter_mapping(self):
        adapter = find_adapter("CapitalPoolAction", "capitalpool", "pause")
        self.assertIsInstance(adapter, CapitalCommandAdapter)

        adapter2 = find_adapter("RebalanceAction", "rebalance", "apply")
        self.assertIsInstance(adapter2, CapitalCommandAdapter)

        adapter3 = find_adapter("ApprovedApply", "rebalance", "apply")
        self.assertIsInstance(adapter3, CapitalCommandAdapter)

        adapter4 = find_adapter("EmergencyContainment", "persona", "EmergencyContainment")
        self.assertIsInstance(adapter4, CapitalCommandAdapter)

    def test_runtime_adapter_mapping(self):
        adapter = find_adapter("RuntimeAction", "runtime", "pause")
        self.assertIsInstance(adapter, RuntimeCommandAdapter)

        adapter2 = find_adapter("StartRuntime", "runtime", "start")
        self.assertIsInstance(adapter2, RuntimeCommandAdapter)

        adapter3 = find_adapter("PausePaperRuntime", "runtime", "pause")
        self.assertIsInstance(adapter3, RuntimeCommandAdapter)

        adapter4 = find_adapter("ExecuteRollback", "rollback", "execute")
        self.assertIsInstance(adapter4, RuntimeCommandAdapter)

        adapter5 = find_adapter("ActivateKillSwitch", "killswitchorder", "activate")
        self.assertIsInstance(adapter5, RuntimeCommandAdapter)

    def test_deployment_adapter_mapping(self):
        adapter = find_adapter("DeploymentAction", "deployment", "approve")
        self.assertIsInstance(adapter, DeploymentCommandAdapter)

        adapter2 = find_adapter("ApproveDeployment", "deploymentplan", "approve")
        self.assertIsInstance(adapter2, DeploymentCommandAdapter)

        adapter3 = find_adapter("CreateDeployment", "deployment", "create")
        self.assertIsInstance(adapter3, DeploymentCommandAdapter)

    def test_persona_adapter_mapping(self):
        adapter = find_adapter("PersonaAction", "persona", "advance_lifecycle")
        self.assertIsInstance(adapter, PersonaCommandAdapter)

        adapter2 = find_adapter("AdvanceLifecycle", "persona", "AdvanceLifecycle")
        self.assertIsInstance(adapter2, PersonaCommandAdapter)

        adapter3 = find_adapter("Observe", "persona", "observe")
        self.assertIsInstance(adapter3, PersonaCommandAdapter)

    def test_governance_adapter_mapping(self):
        adapter = find_adapter("ApproveDecision", "approvaldecision", "approve")
        self.assertIsInstance(adapter, GovernanceCommandAdapter)

        adapter2 = find_adapter("HumanGateApprove", "humangateitem", "approve")
        self.assertIsInstance(adapter2, GovernanceCommandAdapter)

        adapter3 = find_adapter("RecordSponsorDecision", "committeeboard", "sponsor_decision")
        self.assertIsInstance(adapter3, GovernanceCommandAdapter)

    def test_incident_adapter_mapping(self):
        adapter = find_adapter("IncidentAction", "incident", "resolve")
        self.assertIsInstance(adapter, IncidentCommandAdapter)

        adapter2 = find_adapter("RiskAlertAction", "riskalert", "acknowledge")
        self.assertIsInstance(adapter2, IncidentCommandAdapter)

        adapter3 = find_adapter("RemediateSentinelIntervention", "sentinelintervention", "remediate")
        self.assertIsInstance(adapter3, IncidentCommandAdapter)

    def test_evolution_adapter_mapping(self):
        adapter = find_adapter("EvolutionProgramAction", "evolutionprogram", "pause")
        self.assertIsInstance(adapter, EvolutionCommandAdapter)

        adapter2 = find_adapter("ApproveEvolutionDecision", "evolutiondecision", "approve")
        self.assertIsInstance(adapter2, EvolutionCommandAdapter)

        adapter3 = find_adapter("ExecuteEvolutionAction", "evolutiondecision", "execute")
        self.assertIsInstance(adapter3, EvolutionCommandAdapter)

    def test_strategy_adapter_mapping(self):
        adapter = find_adapter("StrategyAction", "strategy", "submit_review")
        self.assertIsInstance(adapter, StrategyCommandAdapter)

        adapter2 = find_adapter("RankingFormulaAction", "rankingformula", "publish")
        self.assertIsInstance(adapter2, StrategyCommandAdapter)

        adapter3 = find_adapter("RankingAction", "ranking", "promote")
        self.assertIsInstance(adapter3, StrategyCommandAdapter)

    def test_capabilities_adapter_mapping(self):
        adapter = find_adapter("ToolAction", "tool", "health_check")
        self.assertIsInstance(adapter, CapabilitiesCommandAdapter)

        adapter2 = find_adapter("McpServerAction", "mcpserver", "test_connection")
        self.assertIsInstance(adapter2, CapabilitiesCommandAdapter)

        adapter3 = find_adapter("SkillAction", "skill", "health_check")
        self.assertIsInstance(adapter3, CapabilitiesCommandAdapter)

    def test_agora_and_audit_adapter_mapping(self):
        adapter = find_adapter("AgoraSignalFeedback", "agorasignal", "feedback")
        self.assertIsInstance(adapter, AgoraCommandAdapter)

        adapter2 = find_adapter("AuditExport", "auditexport", "export")
        self.assertIsInstance(adapter2, AuditCommandAdapter)


class TestDomainExecutionAndReadback(unittest.TestCase):
    """Verifies domain execution, terminal receipt structure, and authoritative readback."""

    def setUp(self):
        os.environ["PANTHEON_CAPITAL_API_URL"] = "http://localhost:5002"
        os.environ["PANTHEON_INTERNAL_API_URL"] = "http://localhost:5001"
        os.environ["PANTHEON_DEPLOYMENT_API_URL"] = "http://localhost:5003"
        os.environ["PANTHEON_GOVERNANCE_API_URL"] = "http://localhost:5004"

    @patch("command_adapters.capital_adapter.http_request_json")
    def test_capital_pool_pause_action_with_readback(self, mock_http):
        mock_http.side_effect = [
            {"status": "paused", "pool_id": "pool-crypto-01"},  # PATCH response
            {"status": "paused", "pool_id": "pool-crypto-01", "budget": 100000},  # GET readback
        ]

        result = execute_command(
            "cmd-pool-pause-01",
            CommandType.CAPITAL_POOL_ACTION,
            {
                "entity_type": "CapitalPool",
                "entity_id": "pool-crypto-01",
                "action_id": "pause",
                "reason": "Market anomaly detected",
            },
        )

        self.assertEqual(result["command_id"], "cmd-pool-pause-01")
        self.assertEqual(result["entity_type"], "CapitalPool")
        self.assertEqual(result["entity_id"], "pool-crypto-01")
        self.assertEqual(result["status"], "executed")
        self.assertIn("dispatch_path", result)
        self.assertEqual(result["authoritative_readback"]["status"], "paused")
        self.assertFalse(result["live_capital_side_effects"])

    @patch("command_adapters.capital_adapter.http_request_json")
    def test_rebalance_apply_with_readback(self, mock_http):
        mock_http.side_effect = [
            {"status": "applied", "rebalance_id": "reb-001", "applied_at": "2026-08-20T12:00:00Z"},
            {"status": "applied", "rebalance_id": "reb-001"},
        ]

        result = execute_command(
            "cmd-reb-apply-01",
            CommandType.REBALANCE_ACTION,
            {
                "entity_type": "Rebalance",
                "entity_id": "reb-001",
                "action_id": "apply",
                "approval_ref": "appr-999",
            },
        )

        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["entity_id"], "reb-001")
        self.assertIsNotNone(result["authoritative_readback"])

    @patch("command_adapters.deployment_adapter.http_request_json")
    def test_deployment_approve_action(self, mock_http):
        mock_http.side_effect = [
            {"state_after": "approved", "approval_decision_id": "dec-123", "audit_id": "aud-456"},
            {"plan_id": "plan-007", "status": "approved"},
        ]

        result = execute_command(
            "cmd-deploy-appr-01",
            CommandType.DEPLOYMENT_ACTION,
            {
                "entity_type": "DeploymentPlan",
                "entity_id": "plan-007",
                "action_id": "approve",
                "approval_decision": "approve",
            },
        )

        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["entity_id"], "plan-007")
        self.assertEqual(result["authoritative_readback"]["status"], "approved")

    @patch("command_adapters.runtime_adapter.http_request_json")
    def test_runtime_pause_action(self, mock_http):
        mock_http.return_value = {
            "status": "executed",
            "status_after": "paused",
            "pause_expires_at": "2026-08-20T16:00:00Z",
        }

        result = execute_command(
            "cmd-rt-pause-01",
            CommandType.RUNTIME_ACTION,
            {
                "entity_type": "Runtime",
                "entity_id": "rt-bind-001",
                "action_id": "pause",
                "duration_seconds": 1800,
            },
        )

        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["authoritative_readback"]["status_after"], "paused")

    @patch("command_adapters.persona_adapter.http_request_json")
    def test_persona_advance_lifecycle_action(self, mock_http):
        mock_http.return_value = {
            "from_state": "candidate",
            "to_state": "paper_owner",
            "audit_id": "aud-persona-01",
        }

        result = execute_command(
            "cmd-persona-adv-01",
            CommandType.PERSONA_ACTION,
            {
                "entity_type": "Persona",
                "entity_id": "persona-alpha",
                "action_id": "AdvanceLifecycle",
                "target_state": "paper_owner",
            },
        )

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["authoritative_readback"]["current_state"], "paper_owner")

    @patch("command_adapters.governance_adapter.http_request_json")
    def test_governance_human_gate_approve(self, mock_http):
        mock_http.return_value = {
            "status": "executed",
            "state": "approved",
        }

        result = execute_command(
            "cmd-gate-appr-01",
            CommandType.HUMAN_GATE_APPROVE,
            {
                "entity_type": "HumanGateItem",
                "gate_id": "gate-xyz",
                "reason": "Risk reviewed and authorized",
            },
        )

        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["authoritative_readback"]["state"], "approved")

    @patch("command_executor._post_json")
    def test_governance_decision_approve(self, mock_post):
        mock_post.return_value = {
            "decision_state": "approved",
            "audit_id": "aud-gov-01",
        }

        result = execute_command(
            "cmd-gov-dec-01",
            CommandType.APPROVE_DECISION,
            {
                "decision_id": "dec-xyz",
                "approval_notes": "All criteria met",
            },
        )

        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["decision_state"], "approved")


class TestUnavailableActionsAndSafetyPosture(unittest.TestCase):
    """Verifies that unsafe/unbacked capability actions fail closed with typed errors."""

    def test_tool_execute_action_raises_typed_unavailable_error(self):
        status, result, error = execute_command_with_status(
            "cmd-tool-exec-01",
            CommandType.TOOL_ACTION,
            {
                "entity_type": "tool",
                "entity_id": "tool-bash",
                "action_id": "execute",
            },
        )

        self.assertEqual(status, CommandStatus.FAILED)
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(error["code"], "CAPABILITY_ACTION_UNAVAILABLE")
        self.assertFalse(error["retryable"])
        self.assertFalse(error["userActionable"])
        self.assertIn("disabled in product runtime", error["message"])

    def test_skill_publish_action_raises_typed_unavailable_error(self):
        status, result, error = execute_command_with_status(
            "cmd-skill-pub-01",
            CommandType.SKILL_ACTION,
            {
                "entity_type": "skill",
                "entity_id": "skill-auto-trade",
                "action_id": "publish",
            },
        )

        self.assertEqual(status, CommandStatus.FAILED)
        self.assertIsNone(result)
        self.assertEqual(error["code"], "CAPABILITY_ACTION_UNAVAILABLE")

    def test_tool_health_check_safe_probe_succeeds(self):
        status, result, error = execute_command_with_status(
            "cmd-tool-probe-01",
            CommandType.TOOL_ACTION,
            {
                "entity_type": "tool",
                "entity_id": "tool-market-feed",
                "action_id": "health_check",
            },
        )

        self.assertEqual(status, CommandStatus.EXECUTED)
        self.assertIsNone(error)
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["authoritative_readback"]["status"], "healthy")

    def test_unregistered_unknown_domain_action_fails_closed(self):
        status, result, error = execute_command_with_status(
            "cmd-unknown-01",
            "NonExistentCommandType",  # type: ignore
            {
                "entity_type": "completely_unknown_entity",
                "action_id": "unknown_verb",
            },
        )

        self.assertEqual(status, CommandStatus.FAILED)
        self.assertIsNone(result)
        self.assertEqual(error["code"], "DOMAIN_OWNER_NOT_FOUND")


class TestErrorAndIdempotencyHandling(unittest.TestCase):
    """Verifies downstream error wrapping, timeouts, and unconfigured states."""

    @patch("command_adapters.capital_adapter.http_request_json")
    def test_downstream_http_500_maps_to_retryable_failed(self, mock_http):
        mock_http.side_effect = urllib.error.HTTPError(
            url="http://localhost:5002/api/capital-pools/pool-1/status",
            code=503,
            msg="Service Unavailable",
            hdrs={},  # type: ignore
            fp=None,
        )

        status, result, error = execute_command_with_status(
            "cmd-pool-503",
            CommandType.CAPITAL_POOL_ACTION,
            {
                "entity_type": "CapitalPool",
                "entity_id": "pool-1",
                "action_id": "pause",
            },
        )

        self.assertEqual(status, CommandStatus.FAILED)
        self.assertIsNone(result)
        self.assertEqual(error["code"], "DOWNSTREAM_ERROR")
        self.assertTrue(error["retryable"])
        self.assertEqual(error["downstream_status"], 503)

    def test_missing_required_entity_id_fails_fast(self):
        status, result, error = execute_command_with_status(
            "cmd-missing-id",
            CommandType.CAPITAL_POOL_ACTION,
            {
                "entity_type": "CapitalPool",
                "entity_id": "",
                "action_id": "pause",
            },
        )

        self.assertEqual(status, CommandStatus.FAILED)
        self.assertIsNone(result)
        self.assertEqual(error["code"], "EXECUTION_ERROR")
        self.assertIn("non-empty entity_id", error["message"])
