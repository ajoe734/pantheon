from __future__ import annotations

import unittest

from models import OpenClawRuntimePin
from openclaw_client import OpenClawCronClient
from service import CronOrchestrator, PromotionError
from workflows import WORKFLOW_CATALOG


class PromotionGateSpy:
    def __init__(self):
        self.calls = []

    def promote(self, entry, target_state, approver=None):
        self.calls.append((entry.copy(), target_state, approver))
        updated = entry.copy()
        if approver:
            updated["approver"] = approver
        updated["lifecycle_state"] = target_state.value
        return updated


class TestOpenClawCronClient(unittest.TestCase):
    def setUp(self):
        self.client = OpenClawCronClient(
            runtime_pin=OpenClawRuntimePin(
                release_tag="v0.1.0-test",
                commit_sha="deadbeef",
                image_ref="ghcr.io/openclaw/openclaw:v0.1.0-test",
            )
        )

    def test_prepare_dispatch_includes_runtime_governance_and_schedule(self):
        workflow = WORKFLOW_CATALOG["pantheon.ingest"]
        request = self.client.prepare_dispatch(
            workflow,
            {
                "strategy_id": "strat-001",
                "title": "Title",
                "hypothesis": "Hypothesis",
                "objective": "Objective",
                "symbols": ["SPY"],
                "frequency": "daily",
                "source_refs": ["https://api.openalex.org/works/W1"],
            },
        )

        self.assertEqual(request["runtime"]["repository_url"], "https://github.com/openclaw/openclaw")
        self.assertEqual(request["runtime"]["release_tag"], "v0.1.0-test")
        self.assertEqual(request["workflow"]["schedule"], "0 */6 * * *")
        self.assertEqual(request["governance"]["policy_id"], "oc002.cron.ingest")
        self.assertIn("research", request["governance"]["allowed_tool_classes"])

    def test_prepare_dispatch_requires_payload_keys(self):
        workflow = WORKFLOW_CATALOG["pantheon.ingest"]
        with self.assertRaises(ValueError):
            self.client.prepare_dispatch(workflow, {"strategy_id": "missing-fields"})


class TestCronOrchestrator(unittest.TestCase):
    def setUp(self):
        client = OpenClawCronClient(
            runtime_pin=OpenClawRuntimePin(
                release_tag="v0.1.0-test",
                commit_sha="deadbeef",
                image_ref="ghcr.io/openclaw/openclaw:v0.1.0-test",
            )
        )
        self.orchestrator = CronOrchestrator(client=client)

    def test_catalog_contains_required_workflows(self):
        self.assertEqual(set(WORKFLOW_CATALOG), {"pantheon.ingest", "pantheon.review", "pantheon.retrain", "pantheon.deploy"})

    def test_ingest_produces_valid_handoff(self):
        result = self.orchestrator.run(
            "pantheon.ingest",
            {
                "strategy_id": "strat-001",
                "title": "Structured Intake",
                "hypothesis": "Approved research can be normalized safely.",
                "objective": "Emit a governed handoff.",
                "symbols": ["SPY", "TLT"],
                "frequency": "daily",
                "source_refs": ["https://api.openalex.org/works/W1"],
                "producer_run_id": "ingest-run-001",
            },
        )

        self.assertEqual(result.handoff["handoff_type"], "research_package")
        self.assertEqual(result.handoff["governance_context"]["execution_context"], "research")
        self.assertEqual(result.handoff["strategy_spec"]["strategy_id"], "strat-001")

    def test_review_emits_approval_request(self):
        result = self.orchestrator.run(
            "pantheon.review",
            {
                "strategy_id": "strat-001",
                "spec_ref": "object://strategy-specs/strat-001.json",
                "lineage_ref": "registry:candidate:strat-001@0.1.0",
            },
        )

        self.assertEqual(result.handoff["handoff_type"], "approval_request")
        self.assertTrue(result.handoff["governance_context"]["approval_required"])
        self.assertEqual(result.handoff["strategy_spec"]["spec_ref"], "object://strategy-specs/strat-001.json")

    def test_retrain_emits_registry_submission(self):
        result = self.orchestrator.run(
            "pantheon.retrain",
            {
                "strategy_id": "strat-001",
                "spec_ref": "object://strategy-specs/strat-001.json",
                "feedback_dataset_refs": ["feedback://approved/2026-04-06"],
                "producer_run_id": "retrain-001",
            },
        )

        self.assertEqual(result.handoff["handoff_type"], "registry_submission")
        self.assertEqual(result.handoff["registry_hints"]["artifact_type"], "model_artifact")
        self.assertEqual(result.handoff["registry_hints"]["source_dataset_refs"], ["feedback://approved/2026-04-06"])

    def test_deploy_uses_promotion_gate_factory(self):
        gate_spy = PromotionGateSpy()
        orchestrator = CronOrchestrator(
            client=OpenClawCronClient(
                runtime_pin=OpenClawRuntimePin(release_tag="v0.1.0-test", commit_sha="deadbeef")
            ),
            promotion_gate_factory=lambda: gate_spy,
        )

        result = orchestrator.run(
            "pantheon.deploy",
            {
                "target_state": "paper",
                "registry_entry": {
                    "strategy_id": "strat-001",
                    "version": "1.0.0",
                    "lifecycle_state": "candidate",
                    "replication_success": True,
                    "lineage": {"source_run_id": "replication-run-001"},
                    "evaluation_summary": {
                        "risk_review_passed": True,
                        "sharpe_ratio": 1.1,
                    },
                    "rollback_target": "0.9.0",
                },
            },
        )

        self.assertEqual(len(gate_spy.calls), 1)
        self.assertEqual(result.registry_entry["lifecycle_state"], "paper")
        self.assertEqual(result.deployment_request["promotion_gate"], "REG-002")

    def test_deploy_live_rejects_without_approver(self):
        with self.assertRaises(PromotionError):
            self.orchestrator.run(
                "pantheon.deploy",
                {
                    "target_state": "live",
                    "registry_entry": {
                        "strategy_id": "strat-001",
                        "version": "1.0.0",
                        "lifecycle_state": "paper",
                        "replication_success": True,
                        "lineage": {"source_run_id": "replication-run-001"},
                        "evaluation_summary": {
                            "risk_review_passed": True,
                            "sharpe_ratio": 1.1,
                        },
                        "rollback_target": "0.9.0",
                    },
                },
            )


if __name__ == "__main__":
    unittest.main()
