"""Kill-switch demo harness for EP5 proof packets.

The harness executes the Runtime Manager kill-switch fast path against a local
ephemeral binding store, packages the Runtime Manager response as Part B5
kill-switch demo evidence, and composes that evidence into the A2.2 EP5 proof
packet shape by setting ``proof.kill_switch_demo_completed``.

It never calls a broker, enables production routing, or writes to a shared
runtime store unless the caller explicitly supplies a local store path.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from services.broker.live_activation.kill_switch_evidence import (
    collect_kill_switch_demo_evidence,
)
from services.governance.ep5_proof.dry_run import run_canary_dry_run


HARNESS_VERSION = "2026-05-20.EP5-008.kill-switch-harness"
DEFAULT_BROKER_SUBACCOUNT_REF = "broker-subaccount://ep5/kill-switch-demo/no-live-routing"
DEFAULT_HUMAN_GATE_PACKET_REF = "support/evidence/EP5-008-V2/human-gate-demo.json"
DEFAULT_BROKER_SANDBOX_SMOKE_REF = "support/evidence/EP5-008-V2/broker-sandbox-smoke.json"
DEFAULT_RISK_OWNER_APPROVAL_REF = "support/evidence/EP5-008-V2/risk-owner-demo-approval.json"
DEFAULT_OPERATOR_APPROVAL_REF = "support/evidence/EP5-008-V2/operator-demo-approval.json"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNTIME_MANAGER_SERVICE_PATH = _REPO_ROOT / "services" / "runtime-manager" / "service.py"
_EXEC_RUNTIME_MANAGER_DIR = _REPO_ROOT / "services" / "execution" / "runtime-manager"
_RUNTIME_SERVICE_MODULE_NAME = "_pantheon_ep5_runtime_manager_service"


class KillSwitchHarnessError(ValueError):
    """Raised when the kill-switch demo harness cannot produce passing evidence."""


class KillSwitchDemoHarnessRequest(BaseModel):
    """Input for the EP5 kill-switch demo harness."""

    model_config = ConfigDict(extra="forbid")

    harness_id: str | None = None
    proof_id: str | None = None
    promotion_readiness_packet_id: str | None = None
    run_id: str | None = None
    persona_id: str = "persona-ep5-demo"
    runtime_id: str = "rt-ep5-kill-switch-demo"
    artifact_id: str = "artifact-ep5-demo"
    artifact_version: str = "1.0.0"
    deployment_plan_id: str = "deployment-plan-ep5-kill-switch-demo"
    capital_pool_id: str = "capital-pool-ep5-demo"
    persona_capital_binding_id: str = "pcb-ep5-demo"
    operator_id: str = "operator-ep5-demo"
    demo_stage: Literal["canary"] = "canary"
    mode: Literal["validate_only", "sandbox"] = "validate_only"
    order_route_mode: Literal["validate_only", "sandbox"] | None = None
    action_type: Literal["pause", "risk_off"] = "pause"
    reason: str = "operator_emergency_stop"
    capital_scale_pct: float = 5.0
    gross_scale_pct: float = 25.0
    broker_subaccount_ref: str = DEFAULT_BROKER_SUBACCOUNT_REF
    evidence_refs: list[str] = Field(default_factory=list)
    started_at: str | None = None
    ended_at: str | None = None
    store_path: str | None = None


def run_kill_switch_demo_harness(
    payload: Mapping[str, Any] | KillSwitchDemoHarnessRequest,
) -> dict[str, Any]:
    """Execute the EP5 kill-switch demo and return a proof/evidence bundle."""

    request = _request_from_payload(payload)
    order_route_mode = request.order_route_mode or request.mode
    harness_id = _required_text(
        request.harness_id or f"ks-demo-{request.run_id or request.deployment_plan_id}",
        "harness_id",
    )

    with _runtime_store(request.store_path) as store_path:
        runtime_service = _load_runtime_manager_service()
        service = runtime_service.RuntimeManagerService(
            store_path=store_path,
            single_runtime_enforced=True,
        )
        binding = service.deploy(_deploy_request(request))
        dispatch_response = service.execute_kill_switch(
            _kill_switch_request(request, binding_id=binding.binding_id)
        )

    demo_packet = _demo_packet(
        request=request,
        harness_id=harness_id,
        runtime_binding_id=binding.binding_id,
        dispatch_response=dispatch_response,
    )
    evidence = collect_kill_switch_demo_evidence(demo_packet)
    evidence_payload = evidence.to_dict()

    if not evidence.passed:
        raise KillSwitchHarnessError(
            "kill-switch demo evidence failed: "
            + "; ".join(evidence.blocking_reasons or ("unknown evidence failure",))
        )

    proof_bundle = run_canary_dry_run(
        {
            "proof_id": request.proof_id or f"ep5-proof-{harness_id}",
            "promotion_readiness_packet_id": (
                request.promotion_readiness_packet_id or f"prp-{harness_id}"
            ),
            "run_id": request.run_id or f"canary-run-{harness_id}",
            "persona_id": request.persona_id,
            "runtime_id": request.runtime_id,
            "runtime_binding_id": binding.binding_id,
            "artifact_id": request.artifact_id,
            "deployment_plan_id": request.deployment_plan_id,
            "environment": "canary",
            "mode": request.mode,
            "order_route_mode": order_route_mode,
            "runtime_started": True,
            "runtime_heartbeat_received": True,
            "telemetry_ingested": True,
            "rollback_drill_completed": False,
            "kill_switch_demo_completed": evidence.passed,
            "audit_events_recorded": bool(dispatch_response.get("audit_entry")),
            "incident_path_tested": False,
            "evidence_refs": [
                *request.evidence_refs,
                f"kill-switch-demo:{evidence.evidence_id}",
                f"runtime-manager-command:{dispatch_response['command']['command_id']}",
                f"runtime-manager-audit:{dispatch_response['audit_entry']['audit_id']}",
            ],
            "started_at": request.started_at,
            "ended_at": request.ended_at,
            "live_capital_side_effects": False,
        }
    )

    return {
        "harness_id": harness_id,
        "harness_version": HARNESS_VERSION,
        "status": "passed",
        "kill_switch_demo_completed": True,
        "live_capital_side_effects": False,
        "demo_packet": demo_packet,
        "kill_switch_demo_evidence": evidence_payload,
        "proof_packet": proof_bundle["proof_packet"],
        "promotion_readiness_packet": proof_bundle["promotion_readiness_packet"],
        "runtime_manager_response": dispatch_response,
    }


def _request_from_payload(
    payload: Mapping[str, Any] | KillSwitchDemoHarnessRequest,
) -> KillSwitchDemoHarnessRequest:
    if isinstance(payload, KillSwitchDemoHarnessRequest):
        return payload
    try:
        return KillSwitchDemoHarnessRequest.model_validate(dict(payload))
    except ValidationError as exc:
        raise KillSwitchHarnessError(str(exc)) from exc


def _required_text(value: str | None, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise KillSwitchHarnessError(f"{field_name} is required")
    return text


class _runtime_store:
    def __init__(self, requested_path: str | None) -> None:
        self._requested_path = requested_path
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None
        self.path: Path | None = None

    def __enter__(self) -> Path:
        if self._requested_path:
            self.path = Path(self._requested_path)
            return self.path
        self._tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tempdir.name) / "runtime-bindings.json"
        return self.path

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._tempdir is not None:
            self._tempdir.cleanup()


def _load_runtime_manager_service() -> Any:
    if str(_EXEC_RUNTIME_MANAGER_DIR) not in sys.path:
        sys.path.insert(0, str(_EXEC_RUNTIME_MANAGER_DIR))
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

    module = sys.modules.get(_RUNTIME_SERVICE_MODULE_NAME)
    if module is not None:
        return module

    spec = importlib.util.spec_from_file_location(
        _RUNTIME_SERVICE_MODULE_NAME,
        _RUNTIME_MANAGER_SERVICE_PATH,
    )
    if spec is None or spec.loader is None:
        raise KillSwitchHarnessError("failed to load RuntimeManagerService module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_RUNTIME_SERVICE_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def _deploy_request(request: KillSwitchDemoHarnessRequest) -> dict[str, Any]:
    return {
        "plan_id": request.deployment_plan_id,
        "plan_status": "approved",
        "target_stage": request.demo_stage,
        "artifact_id": request.artifact_id,
        "artifact_version": request.artifact_version,
        "capital_pool_id": request.capital_pool_id,
        "persona_capital_binding_id": request.persona_capital_binding_id,
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "canary",
        "loader_checks_passed": True,
        "runtime_id": request.runtime_id,
        "promotion_gate_decision_id": f"promotion-gate-{request.deployment_plan_id}",
        "human_gate_packet_ref": DEFAULT_HUMAN_GATE_PACKET_REF,
        "broker_sandbox_smoke_ref": DEFAULT_BROKER_SANDBOX_SMOKE_REF,
        "risk_owner_approval_ref": DEFAULT_RISK_OWNER_APPROVAL_REF,
        "operator_approval_ref": DEFAULT_OPERATOR_APPROVAL_REF,
        "capital_scale_pct": request.capital_scale_pct,
        "gross_scale_pct": request.gross_scale_pct,
    }


def _kill_switch_request(
    request: KillSwitchDemoHarnessRequest,
    *,
    binding_id: str,
) -> dict[str, Any]:
    return {
        "reason": request.reason,
        "capital_pool_id": request.capital_pool_id,
        "actor_id": request.operator_id,
        "binding_id": binding_id,
        "action_override": request.action_type,
        "idempotency_key": f"{request.deployment_plan_id}:{binding_id}:{request.action_type}",
        "context": {
            "environment": "canary",
            "target_stage": request.demo_stage,
            "demo_harness": "EP5-008-V2",
            "broker_subaccount_ref": request.broker_subaccount_ref,
            "no_live_broker_dispatch": True,
        },
    }


def _demo_packet(
    *,
    request: KillSwitchDemoHarnessRequest,
    harness_id: str,
    runtime_binding_id: str,
    dispatch_response: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "demo_id": harness_id,
        "demo_stage": request.demo_stage,
        "deployment_plan_ref": request.deployment_plan_id,
        "runtime_binding_id": runtime_binding_id,
        "capital_pool_id": request.capital_pool_id,
        "operator_id": request.operator_id,
        "broker_subaccount_ref": request.broker_subaccount_ref,
        "drills": [
            {
                "label": f"{request.action_type}-drill",
                "dispatch_response": dict(dispatch_response),
            }
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the EP5 kill-switch demo harness")
    parser.add_argument("--output", help="Optional JSON output path")
    parser.add_argument("--harness-id")
    parser.add_argument("--proof-id")
    parser.add_argument("--promotion-readiness-packet-id")
    parser.add_argument("--run-id")
    parser.add_argument("--persona-id", default="persona-ep5-demo")
    parser.add_argument("--runtime-id", default="rt-ep5-kill-switch-demo")
    parser.add_argument("--artifact-id", default="artifact-ep5-demo")
    parser.add_argument("--artifact-version", default="1.0.0")
    parser.add_argument("--deployment-plan-id", default="deployment-plan-ep5-kill-switch-demo")
    parser.add_argument("--capital-pool-id", default="capital-pool-ep5-demo")
    parser.add_argument("--persona-capital-binding-id", default="pcb-ep5-demo")
    parser.add_argument("--operator-id", default="operator-ep5-demo")
    parser.add_argument("--mode", choices=("validate_only", "sandbox"), default="validate_only")
    parser.add_argument("--action-type", choices=("pause", "risk_off"), default="pause")
    parser.add_argument("--store-path")
    args = parser.parse_args(argv)

    payload = {
        key: value
        for key, value in vars(args).items()
        if key != "output" and value is not None
    }
    result = run_kill_switch_demo_harness(payload)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
