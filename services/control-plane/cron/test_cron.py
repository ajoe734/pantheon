from __future__ import annotations

import unittest

from models import OpenClawRuntimePin
from openclaw_client import OpenClawCronClient
from service import CronOrchestrator, PromotionError
from workflows import WORKFLOW_CATALOG


class StagePlannerSpy:
    def __init__(self):
        self.calls = []

    def create_plan(self, **kwargs):
        self.calls.append(kwargs)
        target_stage = kwargs["target_stage"].value
        return type(
            "Plan",
            (),
            {
                "plan_id": kwargs["plan_id"],
                "plan_dict": {
                    "plan_id": kwargs["plan_id"],
                    "approval_decision_id": kwargs["approval_decision_id"],
                    "artifact_id": kwargs["registry_entry"]["registry_id"],
                    "artifact_version": kwargs["registry_entry"]["version"],
                    "artifact_type": kwargs["registry_entry"]["artifact_type"],
                    "strategy_id": kwargs["registry_entry"]["strategy_id"],
                    "capital_pool_id": kwargs["capital_pool_id"],
                    "current_stage": "none",
                    "target_stage": target_stage,
                    "transition_type": "activate",
                    "runtime_action": "deploy_new_binding",
                    "status": "approved",
                    "created_at": "2026-04-10T00:00:00Z",
                },
                "created_at": "2026-04-10T00:00:00Z",
                "to_dict": lambda self=None: self.plan_dict if self else {},
            },
        )()

    def build_execution_projection(self, plan, registry_entry):
        target_stage = plan.plan_dict["target_stage"]
        metadata = {
            "strategy_id": registry_entry["strategy_id"],
            "version": registry_entry["version"],
            "artifact_state": registry_entry["artifact_state"],
            "deployment_stage": target_stage,
            "deployment_plan_id": plan.plan_dict["plan_id"],
            "approval_decision_id": plan.plan_dict["approval_decision_id"],
        }
        if target_stage in {"paper", "live"}:
            metadata["promotion_state"] = target_stage
        return type(
            "Projection",
            (),
            {
                "metadata_key": f"openclaw/registry/{registry_entry['strategy_id']}/{registry_entry['version']}/metadata.json",
                "artifact_key": f"openclaw/registry/{registry_entry['strategy_id']}/{registry_entry['version']}/artifact.bin",
                "metadata": metadata,
            },
        )()

    def normalize_registry_entry(self, registry_entry):
        return {
            **registry_entry,
            "deployment_summary": {"current_stage": "none"},
        }


def approved_registry_entry(stage: str = "none") -> dict:
    return {
        "registry_id": "reg-strat-001-1.0.0",
        "artifact_type": "model_artifact",
        "strategy_id": "strat-001",
        "version": "1.0.0",
        "artifact_state": "approved",
        "checksum": "sha256:abc123def4567890",
        "approval_decision_id": "approval-001",
        "approved_at": "2026-04-09T12:00:00Z",
        "lineage": {"source_run_ids": ["replication-run-001"]},
        "metadata": {
            "rollback": {
                "target_registry_id": "reg-strat-001-0.9.0",
                "target_version": "0.9.0",
            }
        },
        "deployment_summary": {"current_stage": stage},
    }


def approved_decision() -> dict:
    return {
        "decision_id": "approval-001",
        "target_id": "reg-strat-001-1.0.0",
        "target_version": "1.0.0",
        "decision_state": "decided",
        "decision": "approved",
        "capital_pool_id": "pool-001",
        "persona_id": "persona-ops",
    }


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

    def test_deploy_uses_stage_planner_factory(self):
        planner_spy = StagePlannerSpy()
        orchestrator = CronOrchestrator(
            client=OpenClawCronClient(
                runtime_pin=OpenClawRuntimePin(release_tag="v0.1.0-test", commit_sha="deadbeef")
            ),
            stage_planner_factory=lambda: planner_spy,
        )

        result = orchestrator.run(
            "pantheon.deploy",
            {
                "target_stage": "paper",
                "capital_pool_id": "pool-001",
                "approval_decision": approved_decision(),
                "registry_entry": approved_registry_entry(),
            },
        )

        self.assertEqual(len(planner_spy.calls), 1)
        self.assertEqual(result.registry_entry["deployment_summary"]["current_stage"], "paper")
        self.assertEqual(result.deployment_request["deployment_contract"], "DEP-001")
        self.assertEqual(result.deployment_request["consistency_contract"], "DEP-002")
        self.assertEqual(
            result.deployment_request["deployment_saga"]["saga"]["status"],
            "awaiting_binding",
        )
        self.assertEqual(
            result.deployment_request["deployment_saga"]["outbox_event"]["event"]["sequence_no"],
            1,
        )
        self.assertEqual(
            result.deployment_request["execution_projection"]["metadata_key"],
            "openclaw/registry/strat-001/1.0.0/metadata.json",
        )

    def test_deploy_requires_approved_artifact(self):
        entry = approved_registry_entry()
        entry["artifact_state"] = "candidate"
        with self.assertRaises(PromotionError):
            self.orchestrator.run(
                "pantheon.deploy",
                {
                    "target_stage": "paper",
                    "capital_pool_id": "pool-001",
                    "approval_decision": approved_decision(),
                    "registry_entry": entry,
                },
            )

    def test_deploy_requires_matching_approval_decision(self):
        decision = approved_decision()
        decision["capital_pool_id"] = "pool-999"
        with self.assertRaises(PromotionError):
            self.orchestrator.run(
                "pantheon.deploy",
                {
                    "target_stage": "paper",
                    "capital_pool_id": "pool-001",
                    "approval_decision": decision,
                    "registry_entry": approved_registry_entry(),
                },
            )


if __name__ == "__main__":
    unittest.main()
