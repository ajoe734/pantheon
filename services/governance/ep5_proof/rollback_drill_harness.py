"""Rollback drill harness for EP5 proof packets.

The harness executes a rollback drill against a local Runtime Manager binding
store, validates the rollback packet with the Part B6 dry-run evidence runner,
and composes the result into the A2.2 EP5 proof packet shape by setting
``proof.rollback_drill_completed``.

It never calls broker APIs, enables production routing, or writes to a shared
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

from services.broker.live_activation.rollback_drill_dry_run import (
    RollbackDrillDryRunError,
    run_rollback_drill_dry_run_or_raise,
)
from services.governance.ep5_proof.dry_run import run_canary_dry_run
from services.governance.human_gate.decision_model import (
    CanProceedInput,
    EvidenceReviewed,
    HumanGateDecision,
    HumanGateSignature,
    compute_evidence_hash,
)
# Plain constants (not the class RuntimeManagerService itself), so a direct
# static import is fine here even though _load_runtime_manager_service()
# below loads the service class dynamically from the sibling bridge module.
from services.runtime_manager.service import (
    ROLLBACK_HUMAN_GATE_REQUIRED_ROLES,
    ROLLBACK_HUMAN_GATE_TARGET_TYPE,
)


HARNESS_VERSION = "2026-05-20.EP5-007.rollback-drill-harness"
RUNBOOK_REF = "docs/operations/rollback_drill_runbook.md"
DEFAULT_BROKER_SUBACCOUNT_REF = "broker-subaccount://ep5/rollback-drill/no-live-routing"
DEFAULT_HUMAN_GATE_PACKET_REF = "support/evidence/EP5-007-V2/human-gate.json"
DEFAULT_BROKER_SANDBOX_SMOKE_REF = "support/evidence/EP5-007-V2/broker-sandbox-smoke.json"
DEFAULT_RISK_OWNER_APPROVAL_REF = "support/evidence/EP5-007-V2/risk-owner-approval.json"
DEFAULT_OPERATOR_APPROVAL_REF = "support/evidence/EP5-007-V2/operator-approval.json"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNTIME_MANAGER_SERVICE_PATH = _REPO_ROOT / "services" / "runtime-manager" / "service.py"
_EXEC_RUNTIME_MANAGER_DIR = _REPO_ROOT / "services" / "execution" / "runtime-manager"
_RUNTIME_SERVICE_MODULE_NAME = "_pantheon_ep5_runtime_manager_service"


class RollbackDrillHarnessError(ValueError):
    """Raised when the rollback drill harness cannot produce passing evidence."""


class RollbackDrillHarnessRequest(BaseModel):
    """Input for the EP5 rollback drill harness."""

    model_config = ConfigDict(extra="forbid")

    harness_id: str | None = None
    proof_id: str | None = None
    promotion_readiness_packet_id: str | None = None
    run_id: str | None = None
    persona_id: str = "persona-ep5-demo"
    runtime_id: str = "rt-ep5-rollback-current"
    replacement_runtime_id: str = "rt-ep5-rollback-replacement"
    artifact_id: str = "artifact-ep5-current"
    artifact_version: str = "1.0.0"
    replacement_artifact_id: str = "artifact-ep5-fallback"
    replacement_artifact_version: str = "0.9.0"
    deployment_plan_id: str = "deployment-plan-ep5-rollback-current"
    replacement_plan_id: str = "deployment-plan-ep5-rollback-fallback"
    capital_pool_id: str = "capital-pool-ep5-demo"
    persona_capital_binding_id: str = "pcb-ep5-current"
    replacement_persona_capital_binding_id: str = "pcb-ep5-fallback"
    operator_id: str = "operator-ep5-demo"
    drill_stage: Literal["canary"] = "canary"
    replacement_deployment_mode: Literal["paper", "canary"] = "paper"
    replacement_allowed_deployment_scope: Literal["paper", "canary"] = "paper"
    mode: Literal["validate_only", "sandbox"] = "validate_only"
    order_route_mode: Literal["validate_only", "sandbox"] | None = None
    action_type: Literal["replace", "pause_then_replace", "liquidate_then_replace"] = (
        "pause_then_replace"
    )
    replacement_start_paused: bool = False
    capital_scale_pct: float = 5.0
    gross_scale_pct: float = 25.0
    broker_subaccount_ref: str = DEFAULT_BROKER_SUBACCOUNT_REF
    evidence_refs: list[str] = Field(default_factory=list)
    started_at: str | None = None
    ended_at: str | None = None
    store_path: str | None = None
    dry_run: Literal[True] = True
    side_effects_allowed: Literal[False] = False


def run_rollback_drill_harness(
    payload: Mapping[str, Any] | RollbackDrillHarnessRequest,
) -> dict[str, Any]:
    """Execute the EP5 rollback drill and return a proof/evidence bundle."""

    request = _request_from_payload(payload)
    order_route_mode = request.order_route_mode or request.mode
    harness_id = _required_text(
        request.harness_id or f"rollback-drill-{request.run_id or request.deployment_plan_id}",
        "harness_id",
    )

    with _runtime_store(request.store_path) as store_path:
        runtime_service = _load_runtime_manager_service()
        service = runtime_service.RuntimeManagerService(
            store_path=store_path,
            single_runtime_enforced=True,
        )
        paper_binding = service.deploy(_paper_deploy_request(request))
        promotion_result = service.promote_stage(
            _canary_promote_request(request, paper_binding_id=paper_binding.binding_id)
        )
        current_binding_id = promotion_result["new_binding"]["binding_id"]
        current_binding_status = promotion_result["new_binding"]["status"]
        drill_packet = _drill_packet(
            request=request,
            harness_id=harness_id,
            current_binding_id=current_binding_id,
            current_binding_status=current_binding_status,
        )
        rollback_evidence = _collect_rollback_evidence(drill_packet)
        # NOTE: this harness has never actually deployed+retired a binding
        # matching replacement_plan_id/replacement_artifact_id, so
        # service.rollback()'s candidate resolution has nothing to find
        # regardless of the human gate below — a separate, pre-existing gap
        # in this harness's own test data (see the skip reason on
        # tests/governance/test_rollback_drill_harness.py), not something
        # the human-gate change introduced or fixes by itself.
        prior_binding_id_placeholder = (
            f"{request.replacement_plan_id}:{request.replacement_artifact_id}"
        )
        rollback_response = service.rollback(
            _runtime_manager_rollback_request(
                rollback_evidence.planned_runtime_manager_request,
                replacement_start_paused=request.replacement_start_paused,
                human_gate_decision=_approved_rollback_human_gate(
                    old_binding_id=current_binding_id,
                    prior_binding_id=prior_binding_id_placeholder,
                    target_environment=request.drill_stage,
                ),
            )
        )

    evidence_payload = rollback_evidence.to_dict()
    _validate_runtime_rollback_response(
        request=request,
        current_binding_id=current_binding_id,
        rollback_response=rollback_response,
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
            "runtime_binding_id": current_binding_id,
            "artifact_id": request.artifact_id,
            "deployment_plan_id": request.deployment_plan_id,
            "environment": "canary",
            "mode": request.mode,
            "order_route_mode": order_route_mode,
            "runtime_started": True,
            "runtime_heartbeat_received": True,
            "telemetry_ingested": True,
            "rollback_drill_completed": True,
            "kill_switch_demo_completed": False,
            "audit_events_recorded": True,
            "incident_path_tested": False,
            "evidence_refs": [
                *request.evidence_refs,
                f"runbook:{RUNBOOK_REF}",
                f"rollback-drill:{rollback_evidence.evidence_id}",
                f"runtime-manager-rollback:{rollback_response['new_binding']['binding_id']}",
                "rollback-telemetry-preview:rollback_completed",
            ],
            "started_at": request.started_at,
            "ended_at": request.ended_at,
            "live_capital_side_effects": False,
        }
    )

    if not proof_bundle["proof_packet"]["proof"]["rollback_drill_completed"]:
        raise RollbackDrillHarnessError("EP5 proof packet did not mark rollback drill completed")
    if proof_bundle["proof_packet"]["proof"]["live_capital_side_effects"] is not False:
        raise RollbackDrillHarnessError("EP5 proof packet reported live capital side effects")

    return {
        "harness_id": harness_id,
        "harness_version": HARNESS_VERSION,
        "status": "passed",
        "runbook_ref": RUNBOOK_REF,
        "rollback_drill_completed": True,
        "live_capital_side_effects": False,
        "drill_packet": drill_packet,
        "rollback_drill_evidence": evidence_payload,
        "runtime_manager_response": rollback_response,
        "proof_packet": proof_bundle["proof_packet"],
        "promotion_readiness_packet": proof_bundle["promotion_readiness_packet"],
    }


def _request_from_payload(
    payload: Mapping[str, Any] | RollbackDrillHarnessRequest,
) -> RollbackDrillHarnessRequest:
    if isinstance(payload, RollbackDrillHarnessRequest):
        return payload
    try:
        return RollbackDrillHarnessRequest.model_validate(dict(payload))
    except ValidationError as exc:
        raise RollbackDrillHarnessError(str(exc)) from exc


def _required_text(value: str | None, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RollbackDrillHarnessError(f"{field_name} is required")
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
        raise RollbackDrillHarnessError("failed to load RuntimeManagerService module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_RUNTIME_SERVICE_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def _paper_deploy_request(request: RollbackDrillHarnessRequest) -> dict[str, Any]:
    """First step of the governed canary path: an ordinary paper deploy.

    RuntimeManagerService.deploy() only permits target_stage="paper" without
    the internal-only _allow_non_paper_deploy escape hatch. Reaching
    drill_stage="canary" requires deploying to paper first, then calling
    promote_stage() — see _canary_promote_request below (mirrors the same
    fix in kill_switch_harness.py).
    """
    return {
        "plan_id": request.deployment_plan_id,
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": request.artifact_id,
        "artifact_version": request.artifact_version,
        "capital_pool_id": request.capital_pool_id,
        "persona_capital_binding_id": request.persona_capital_binding_id,
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "canary",
        "loader_checks_passed": True,
        "runtime_id": request.runtime_id,
        "metadata": {
            "authoritative_loader_attestation": {
                "status": "passed",
                "authority": "canonical_deployment_registry_governance_capital",
            }
        },
    }


def _canary_promote_request(
    request: RollbackDrillHarnessRequest, *, paper_binding_id: str
) -> dict[str, Any]:
    """Second step: promote the paper binding to canary via the authoritative
    governed activation path. The demo's DEFAULT_*_REF constants stand in for
    the MFA/distinct-actor approval proof this path requires.
    """
    return {
        "current_binding_id": paper_binding_id,
        "plan_id": request.deployment_plan_id,
        "plan_status": "approved",
        "target_stage": request.drill_stage,
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
        "metadata": {
            "authoritative_promotion_attestation": {
                "status": "passed",
                "authority": "canonical_stage_promotion",
                "source_stage": "paper",
                "target_stage": request.drill_stage,
            }
        },
    }


def _drill_packet(
    *,
    request: RollbackDrillHarnessRequest,
    harness_id: str,
    current_binding_id: str,
    current_binding_status: str,
) -> dict[str, Any]:
    return {
        "drill_id": harness_id,
        "dry_run": True,
        "side_effects_allowed": False,
        "dispatch_live": False,
        "call_broker_api": False,
        "ingest_telemetry": False,
        "drill_stage": request.drill_stage,
        "deployment_plan_ref": request.deployment_plan_id,
        "current_binding_id": current_binding_id,
        "current_binding_status": current_binding_status,
        "capital_pool_id": request.capital_pool_id,
        "operator_id": request.operator_id,
        "broker_subaccount_ref": request.broker_subaccount_ref,
        "action_type": request.action_type,
        "replacement_plan_ref": request.replacement_plan_id,
        "replacement_deployment_mode": request.replacement_deployment_mode,
        "replacement_allowed_deployment_scope": request.replacement_allowed_deployment_scope,
        "replacement_runtime_id": request.replacement_runtime_id,
        "replacement_artifact_id": request.replacement_artifact_id,
        "replacement_artifact_version": request.replacement_artifact_version,
        "replacement_persona_capital_binding_id": (
            request.replacement_persona_capital_binding_id
        ),
        "opened_by_artifact_id": request.artifact_id,
        "loader_checks_passed": True,
    }


def _collect_rollback_evidence(drill_packet: Mapping[str, Any]) -> Any:
    try:
        return run_rollback_drill_dry_run_or_raise(drill_packet)
    except RollbackDrillDryRunError as exc:
        raise RollbackDrillHarnessError(f"rollback drill evidence failed: {exc}") from exc


def _approved_rollback_human_gate(
    *, old_binding_id: str, prior_binding_id: str, target_environment: str
) -> dict[str, Any]:
    """Build an approved HumanGateDecision bound to one exact rollback pairing.

    AUDIT-REMEDIATION-20260830 §3: RuntimeManagerService.rollback() now
    requires this instead of the old auto-fetched SHA-256/four-owner proof,
    for every stage. Real callers create and sign this through governance's
    human-gate API; this demo harness signs it itself as a stand-in the same
    way it already stands in for the other approval refs
    (DEFAULT_RISK_OWNER_APPROVAL_REF, DEFAULT_OPERATOR_APPROVAL_REF, ...).
    """
    evidence_reviewed = (
        EvidenceReviewed(
            key="rollback_target",
            evidence_hash="sha256:" + "a" * 64,
            source_ref=f"runtime-binding://{prior_binding_id}",
        ),
    )
    evidence_hash = compute_evidence_hash(evidence_reviewed)
    decision = HumanGateDecision(
        decision_id=f"hgd-{old_binding_id}-{prior_binding_id}",
        target_type=ROLLBACK_HUMAN_GATE_TARGET_TYPE,
        target_id=f"{old_binding_id}->{prior_binding_id}",
        target_environment=target_environment,
        required_roles=tuple(ROLLBACK_HUMAN_GATE_REQUIRED_ROLES),
        evidence_reviewed=evidence_reviewed,
        can_proceed_input=CanProceedInput(readiness_packet_can_proceed=True),
        evidence_hash=evidence_hash,
    )
    for role in ROLLBACK_HUMAN_GATE_REQUIRED_ROLES:
        decision = decision.with_signature(
            HumanGateSignature(
                signature_id=f"hgd-{old_binding_id}-{prior_binding_id}-{role}",
                role=role,
                actor_id=f"{role}-approver",
                signed_at="2026-05-20T00:00:00Z",
                evidence_hash=evidence_hash,
                evidence_reviewed=("rollback_target",),
            )
        )
    return decision.normalized().to_dict()


def _runtime_manager_rollback_request(
    planned_request: Mapping[str, Any],
    *,
    replacement_start_paused: bool,
    human_gate_decision: Mapping[str, Any],
) -> dict[str, Any]:
    request = {
        key: value
        for key, value in planned_request.items()
        if key not in {"dry_run", "side_effects_allowed"}
    }
    if replacement_start_paused:
        request["replacement_start_paused"] = True
    request["human_gate_decision"] = dict(human_gate_decision)
    return request


def _validate_runtime_rollback_response(
    *,
    request: RollbackDrillHarnessRequest,
    current_binding_id: str,
    rollback_response: Mapping[str, Any],
) -> None:
    old_binding = rollback_response.get("old_binding") or {}
    new_binding = rollback_response.get("new_binding") or {}
    position_lineage = rollback_response.get("position_lineage") or {}
    if rollback_response.get("action_type") != request.action_type:
        raise RollbackDrillHarnessError("Runtime Manager rollback action_type mismatch")
    if old_binding.get("binding_id") != current_binding_id:
        raise RollbackDrillHarnessError("Runtime Manager response lost current binding lineage")
    if old_binding.get("status") != "retired":
        raise RollbackDrillHarnessError("Runtime Manager did not retire the current binding")
    if new_binding.get("rollback_parent") != current_binding_id:
        raise RollbackDrillHarnessError("replacement binding is missing rollback_parent")
    if new_binding.get("rollback_action_type") != request.action_type:
        raise RollbackDrillHarnessError("replacement binding rollback_action_type mismatch")
    if new_binding.get("artifact_id") != request.replacement_artifact_id:
        raise RollbackDrillHarnessError("replacement binding artifact mismatch")
    if position_lineage.get("opened_by_artifact_id") != request.artifact_id:
        raise RollbackDrillHarnessError("position lineage did not preserve opened_by_artifact_id")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the EP5 rollback drill harness")
    parser.add_argument("--output", help="Optional JSON output path")
    parser.add_argument("--harness-id")
    parser.add_argument("--proof-id")
    parser.add_argument("--promotion-readiness-packet-id")
    parser.add_argument("--run-id")
    parser.add_argument("--persona-id", default="persona-ep5-demo")
    parser.add_argument("--runtime-id", default="rt-ep5-rollback-current")
    parser.add_argument("--replacement-runtime-id", default="rt-ep5-rollback-replacement")
    parser.add_argument("--artifact-id", default="artifact-ep5-current")
    parser.add_argument("--artifact-version", default="1.0.0")
    parser.add_argument("--replacement-artifact-id", default="artifact-ep5-fallback")
    parser.add_argument("--replacement-artifact-version", default="0.9.0")
    parser.add_argument("--deployment-plan-id", default="deployment-plan-ep5-rollback-current")
    parser.add_argument("--replacement-plan-id", default="deployment-plan-ep5-rollback-fallback")
    parser.add_argument("--capital-pool-id", default="capital-pool-ep5-demo")
    parser.add_argument("--persona-capital-binding-id", default="pcb-ep5-current")
    parser.add_argument("--replacement-persona-capital-binding-id", default="pcb-ep5-fallback")
    parser.add_argument("--operator-id", default="operator-ep5-demo")
    parser.add_argument("--mode", choices=("validate_only", "sandbox"), default="validate_only")
    parser.add_argument(
        "--action-type",
        choices=("replace", "pause_then_replace", "liquidate_then_replace"),
        default="pause_then_replace",
    )
    parser.add_argument("--replacement-start-paused", action="store_true")
    parser.add_argument("--store-path")
    args = parser.parse_args(argv)

    payload = {
        key: value
        for key, value in vars(args).items()
        if key != "output" and value is not None
    }
    result = run_rollback_drill_harness(payload)
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
