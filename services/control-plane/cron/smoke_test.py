#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

from models import OpenClawRuntimePin
from openclaw_client import OpenClawCronClient
from service import CronOrchestrator


def fake_transport(request: dict) -> dict:
    return {
        "status": "accepted",
        "workflow_id": request["workflow"]["workflow_id"],
        "request_id": request["request_id"],
        "runtime_pin": request["runtime"],
    }


def build_orchestrator() -> CronOrchestrator:
    client = OpenClawCronClient(
        runtime_pin=OpenClawRuntimePin(
            release_tag="v0.1.0-test",
            commit_sha="deadbeefcafebabe",
            image_ref="ghcr.io/openclaw/openclaw:v0.1.0-test",
        ),
        transport=fake_transport,
    )
    return CronOrchestrator(client=client)


def run_smoke_tests() -> list[tuple[str, bool]]:
    orchestrator = build_orchestrator()
    results: list[tuple[str, bool]] = []

    ingest_result = orchestrator.run(
        "pantheon.ingest",
        {
            "strategy_id": "strat-openalex-001",
            "title": "OpenAlex Momentum Intake",
            "hypothesis": "Structured research discovery can seed governed momentum ideas.",
            "objective": "Generate candidate StrategySpecs from approved sources.",
            "symbols": ["SPY", "QQQ"],
            "frequency": "daily",
            "source_refs": ["https://api.openalex.org/works/W2961191295"],
            "source_task_id": "RS-001",
            "producer_run_id": "openclaw-intake-001",
        },
        dry_run=False,
    )
    print("INGEST HANDOFF")
    print(json.dumps(ingest_result.handoff, indent=2))
    results.append(("ingest", ingest_result.handoff is not None))

    review_result = orchestrator.run(
        "pantheon.review",
        {
            "strategy_id": "strat-openalex-001",
            "spec_ref": "object://strategy-specs/strat-openalex-001.json",
            "lineage_ref": "registry:candidate:strat-openalex-001@0.1.0",
            "producer_run_id": "review-run-001",
            "source_task_id": "OC-002",
        },
        dry_run=False,
    )
    print("\nREVIEW HANDOFF")
    print(json.dumps(review_result.handoff, indent=2))
    results.append(("review", review_result.handoff is not None))

    retrain_result = orchestrator.run(
        "pantheon.retrain",
        {
            "strategy_id": "strat-openalex-001",
            "spec_ref": "object://strategy-specs/strat-openalex-001.json",
            "feedback_dataset_refs": ["feedback://approved/2026-04-06"],
            "producer_run_id": "retrain-run-001",
            "source_task_id": "LP-001",
        },
        dry_run=False,
    )
    print("\nRETRAIN HANDOFF")
    print(json.dumps(retrain_result.handoff, indent=2))
    results.append(("retrain", retrain_result.handoff is not None))

    deploy_result = orchestrator.run(
        "pantheon.deploy",
        {
            "target_stage": "live",
            "capital_pool_id": "pool-001",
            "approval_decision": {
                "decision_id": "approval-001",
                "target_id": "reg-strat-openalex-001-1.2.0",
                "target_version": "1.2.0",
                "decision_state": "decided",
                "decision": "approved",
                "capital_pool_id": "pool-001",
                "persona_id": "persona-ops",
            },
            "registry_entry": {
                "registry_id": "reg-strat-openalex-001-1.2.0",
                "artifact_type": "model_artifact",
                "strategy_id": "strat-openalex-001",
                "version": "1.2.0",
                "checksum": "sha256:abc123def4567890",
                "artifact_state": "approved",
                "approval_decision_id": "approval-001",
                "approved_at": "2026-04-09T12:00:00Z",
                "deployment_summary": {
                    "current_stage": "canary"
                },
                "evaluation_summary": {
                    "risk_review_passed": True,
                    "sharpe_ratio": 1.42,
                },
                "lineage": {
                    "source_run_ids": ["registry-paper-001"],
                },
                "metadata": {
                    "rollback": {
                        "target_registry_id": "reg-strat-openalex-001-1.1.0",
                        "target_version": "1.1.0",
                    }
                },
            },
        },
        dry_run=False,
    )
    print("\nDEPLOY RESULT")
    print(json.dumps(deploy_result.to_dict(), indent=2))
    results.append(
        (
            "deploy",
            deploy_result.registry_entry is not None
            and deploy_result.registry_entry["deployment_summary"]["current_stage"] == "live"
            and deploy_result.deployment_request["deployment_contract"] == "DEP-001"
            and deploy_result.deployment_request["consistency_contract"] == "DEP-002"
            and deploy_result.deployment_request["deployment_saga"]["outbox_event"]["event"]["sequence_no"] == 1,
        )
    )

    return results


def main() -> int:
    print("PANTHEON OC-002 SMOKE TEST")
    print("=" * 72)
    results = run_smoke_tests()
    print("\nSUMMARY")
    print("=" * 72)
    for name, passed in results:
        print(f"{name:10s} {'PASS' if passed else 'FAIL'}")
    return 0 if all(passed for _, passed in results) else 1


if __name__ == "__main__":
    sys.exit(main())
