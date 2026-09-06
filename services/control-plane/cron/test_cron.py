from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from services.control_plane.cron.models import OpenClawRuntimePin
from services.control_plane.cron.openclaw_client import OpenClawCronClient
from services.control_plane.cron.service import CronOrchestrator, PromotionError
from services.control_plane.cron.workflows import PERSONA_FIRST_EVALUATION_WORKFLOW_ID, WORKFLOW_CATALOG

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from integrations.openclaw.adapter import OpenClawCronGatewayTransport, build_system_event_text


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

    def test_prepare_dispatch_includes_adapter_boundary_refs(self):
        workflow = WORKFLOW_CATALOG["pantheon.ingest"]
        payload = {
            "strategy_id": "strat-001",
            "title": "Title",
            "hypothesis": "Hypothesis",
            "objective": "Objective",
            "symbols": ["SPY"],
            "frequency": "daily",
            "source_refs": ["https://api.openalex.org/works/W1"],
        }
        env = {
            "PANTHEON_WORKSPACE_REF": "workspace-ops",
            "PANTHEON_AUTH_PROFILE_REF": "auth-ops",
            "PANTHEON_PERSONA_ID": "persona-ops",
            "PANTHEON_SESSION_ID": "session-ops",
            "PANTHEON_TRACE_ID": "trace-ops",
            "PANTHEON_REQUEST_ID": "request-ops",
        }
        with patch.dict(os.environ, env, clear=False):
            client = OpenClawCronClient(runtime_pin=self.client.runtime_pin)
            request = client.prepare_dispatch(workflow, payload)

        self.assertEqual(
            request["pantheon_adapter"]["integration_boundary"],
            "pantheon-openclaw-gateway-adapter",
        )
        self.assertEqual(request["pantheon_adapter"]["workspace_ref"], "workspace-ops")
        self.assertEqual(request["pantheon_adapter"]["auth_profile_ref"], "auth-ops")
        self.assertEqual(request["pantheon_adapter"]["credential_sharing"], "disallowed")


class GatewayRuntimeSpy:
    def __init__(self):
        self.config = type(
            "Config",
            (),
            {
                "container_name": "pantheon-openclaw-gateway",
                "http_base_url": "http://127.0.0.1:18789",
                "ws_url": "ws://127.0.0.1:18789",
            },
        )()
        self.calls: list[tuple[str, dict | None]] = []

    def probe(self) -> dict:
        return {"http_health": {"ok": True}, "gateway_status": {"rpc": {"ok": True}}}

    def gateway_call(self, method: str, params: dict | None = None) -> dict:
        self.calls.append((method, params))
        if method == "cron.add":
            return {"id": "job-001"}
        if method == "cron.run":
            return {"ok": True, "enqueued": True, "runId": "run-001"}
        if method == "cron.runs":
            return {
                "entries": [
                    {
                        "runId": "run-001",
                        "status": "ok",
                        "summary": params["id"],
                    }
                ]
            }
        raise AssertionError(f"Unexpected method: {method}")


class TestOpenClawGatewayTransport(unittest.TestCase):
    def test_build_system_event_text_is_deterministic(self):
        request = {
            "prepared_at": "2026-04-16T19:00:00Z",
            "request_id": "pantheon.ingest-1234",
            "workflow": {
                "workflow_id": "pantheon.ingest",
                "upstream_entrypoint": "research.ingest",
            },
            "governance": {"policy_id": "oc002.cron.ingest"},
        }

        self.assertEqual(
            build_system_event_text(request),
            (
                '{"kind":"pantheon.workflow.dispatch","policy_id":"oc002.cron.ingest",'
                '"prepared_at":"2026-04-16T19:00:00Z","request_id":"pantheon.ingest-1234",'
                '"upstream_entrypoint":"research.ingest","workflow_id":"pantheon.ingest"}'
            ),
        )

    def test_transport_maps_dispatch_into_cron_rpc_calls(self):
        runtime = GatewayRuntimeSpy()
        transport = OpenClawCronGatewayTransport(runtime, poll_timeout_seconds=0.1, poll_interval_seconds=0.01)
        response = transport(
            {
                "prepared_at": "2026-04-16T19:00:00Z",
                "request_id": "pantheon.ingest-1234",
                "runtime": {"release_tag": "v2026.7.1"},
                "workflow": {
                    "workflow_id": "pantheon.ingest",
                    "upstream_entrypoint": "research.ingest",
                },
                "governance": {"policy_id": "oc002.cron.ingest"},
            }
        )

        self.assertEqual(response["status"], "accepted")
        self.assertEqual([method for method, _ in runtime.calls], ["cron.add", "cron.run", "cron.runs"])
        cron_add = runtime.calls[0][1]
        self.assertEqual(cron_add["payload"]["kind"], "systemEvent")
        self.assertEqual(cron_add["delivery"]["mode"], "none")
        self.assertEqual(cron_add["wakeMode"], "next-heartbeat")
        self.assertEqual(response["job"]["id"], "job-001")
        self.assertEqual(response["run"]["status"], "ok")


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
        self.assertEqual(
            set(WORKFLOW_CATALOG),
            {
                "pantheon.ingest",
                "pantheon.review",
                "pantheon.retrain",
                "pantheon.deploy",
                PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
            },
        )

    def test_persona_first_evaluation_is_runnable_from_catalog(self):
        result = self.orchestrator.run(
            PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
            {"persona_id": "persona-eval-001"},
        )

        self.assertEqual(result.workflow_id, PERSONA_FIRST_EVALUATION_WORKFLOW_ID)
        self.assertIsNone(result.handoff)
        self.assertEqual(
            result.dispatch_request["workflow"]["workflow_id"],
            PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
        )
        self.assertEqual(result.dispatch_request["payload"]["persona_id"], "persona-eval-001")
        self.assertEqual(result.upstream_response["status"], "prepared")

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

    def test_deploy_refuses_failed_pool_runtime_compatibility(self):
        with self.assertRaisesRegex(PromotionError, "pool_admissibility_status_not_active"):
            self.orchestrator.run(
                "pantheon.deploy",
                {
                    "target_stage": "paper",
                    "capital_pool_id": "pool-001",
                    "approval_decision": approved_decision(),
                    "registry_entry": approved_registry_entry(),
                    "metadata": {
                        "target_size": 0,
                        "persona_capital_binding_id": "pcb-001",
                    },
                    "pool_runtime_compat": {
                        "capital_pool": {
                            "pool_id": "pool-001",
                            "status": "suspended",
                            "risk_budget": 100_000,
                            "metadata": {"jurisdiction": "US"},
                        },
                        "runtime_requirements": {
                            "runtime_mode": "paper",
                            "broker_jurisdiction": "US",
                            "persona_capital_binding_id": "pcb-001",
                        },
                        "persona_capital_binding": {
                            "binding_id": "pcb-001",
                            "persona_id": "persona-ops",
                            "capital_pool_id": "pool-001",
                            "status": "active",
                        },
                    },
                },
            )

    def test_deploy_records_passed_pool_runtime_compatibility(self):
        result = self.orchestrator.run(
            "pantheon.deploy",
            {
                "target_stage": "paper",
                "capital_pool_id": "pool-001",
                "approval_decision": approved_decision(),
                "registry_entry": approved_registry_entry(),
                "metadata": {
                    "target_size": 0,
                    "persona_capital_binding_id": "pcb-001",
                },
                "pool_runtime_compat": {
                    "capital_pool": {
                        "pool_id": "pool-001",
                        "status": "active",
                        "risk_budget": 100_000,
                        "metadata": {"jurisdiction": "US"},
                    },
                    "runtime_requirements": {
                        "runtime_mode": "paper",
                        "broker_jurisdiction": "US",
                        "persona_capital_binding_id": "pcb-001",
                    },
                    "persona_capital_binding": {
                        "binding_id": "pcb-001",
                        "persona_id": "persona-ops",
                        "capital_pool_id": "pool-001",
                        "status": "active",
                    },
                },
            },
        )

        compatibility = result.deployment_request["pool_runtime_compatibility"]
        self.assertTrue(compatibility["passed"])
        self.assertEqual(compatibility["rejection_reasons"], [])


if __name__ == "__main__":
    unittest.main()
