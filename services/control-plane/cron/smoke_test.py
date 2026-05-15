#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from models import OpenClawRuntimePin
from openclaw_client import OpenClawCronClient
from service import CronOrchestrator

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from integrations.openclaw.adapter import (
    OpenClawCronGatewayTransport,
    OpenClawDockerGatewayRuntime,
    OpenClawGatewayConfig,
)


def fake_transport(request: dict) -> dict:
    return {
        "status": "accepted",
        "workflow_id": request["workflow"]["workflow_id"],
        "request_id": request["request_id"],
        "runtime_pin": request["runtime"],
    }


def build_fake_orchestrator() -> CronOrchestrator:
    client = OpenClawCronClient(
        runtime_pin=OpenClawRuntimePin(
            release_tag="v0.1.0-test",
            commit_sha="deadbeefcafebabe",
            image_ref="ghcr.io/openclaw/openclaw:v0.1.0-test",
        ),
        transport=fake_transport,
    )
    return CronOrchestrator(client=client)


def build_live_orchestrator(args: argparse.Namespace) -> tuple[CronOrchestrator, OpenClawDockerGatewayRuntime]:
    config = OpenClawGatewayConfig(
        container_name=args.container_name,
        host_port=args.host_port,
        gateway_token=args.gateway_token,
        state_dir=args.state_dir,
        startup_pull=args.pull,
        poll_interval_seconds=args.poll_interval,
        health_timeout_seconds=args.health_timeout,
    )
    runtime = OpenClawDockerGatewayRuntime(config=config)
    client = OpenClawCronClient(
        runtime_pin=OpenClawRuntimePin(
            repository_url=config.repository_url,
            release_tag=config.release_tag,
            commit_sha=config.commit_sha,
            image_ref=config.image_ref,
        ),
        base_url=config.ws_url,
        transport=OpenClawCronGatewayTransport(
            runtime,
            poll_timeout_seconds=args.poll_timeout,
            poll_interval_seconds=args.poll_interval,
        ),
    )
    return CronOrchestrator(client=client), runtime


def run_smoke_tests(
    orchestrator: CronOrchestrator,
    *,
    label: str,
    work_dir: Path | None = None,
    runtime_probe: dict | None = None,
) -> list[tuple[str, bool]]:
    results: list[tuple[str, bool]] = []
    artifact_payload: dict[str, object] = {"mode": label, "checks": {}}
    if runtime_probe is not None:
        artifact_payload["runtime_probe"] = runtime_probe

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
    artifact_payload["checks"]["ingest"] = ingest_result.to_dict()

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
    artifact_payload["checks"]["review"] = review_result.to_dict()

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
    artifact_payload["checks"]["retrain"] = retrain_result.to_dict()

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
    artifact_payload["checks"]["deploy"] = deploy_result.to_dict()

    if work_dir is not None:
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "smoke-results.json").write_text(json.dumps(artifact_payload, indent=2), encoding="utf-8")

    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pantheon OpenClaw cron smoke test")
    parser.add_argument("--mode", choices=("fake", "live"), default="fake")
    parser.add_argument("--manage-runtime", action="store_true", help="Start and stop the gateway container for live mode")
    parser.add_argument("--pull", action="store_true", help="Pull the pinned OpenClaw image before starting live mode")
    parser.add_argument("--container-name", default="pantheon-openclaw-gateway")
    parser.add_argument("--host-port", type=int, default=18789)
    parser.add_argument("--gateway-token", default="pantheon-local-token")
    parser.add_argument("--state-dir")
    parser.add_argument("--poll-timeout", type=float, default=30.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--health-timeout", type=float, default=30.0)
    parser.add_argument("--work-dir")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print("PANTHEON OC-002 SMOKE TEST")
    print("=" * 72)
    work_dir = Path(args.work_dir).resolve() if args.work_dir else None
    if args.mode == "fake":
        results = run_smoke_tests(build_fake_orchestrator(), label="fake", work_dir=work_dir)
    else:
        orchestrator, runtime = build_live_orchestrator(args)
        runtime_probe = None
        if args.manage_runtime:
            started = runtime.start()
            runtime_probe = {
                "managed_runtime": started,
                "heartbeat_disabled": runtime.disable_heartbeat(),
            }
        try:
            runtime_probe = {
                **(runtime_probe or {}),
                **runtime.probe(),
            }
            results = run_smoke_tests(
                orchestrator,
                label="live",
                work_dir=work_dir,
                runtime_probe=runtime_probe,
            )
        finally:
            if args.manage_runtime:
                runtime.stop()
    print("\nSUMMARY")
    print("=" * 72)
    for name, passed in results:
        print(f"{name:10s} {'PASS' if passed else 'FAIL'}")
    return 0 if all(passed for _, passed in results) else 1


if __name__ == "__main__":
    sys.exit(main())
