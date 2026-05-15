"""
paper_runtime_binding - MGMT-PAPER-004

Factory and evidence writer for a paper-mode RuntimeBinding packet used in
the Management Paper Loop Proof (Track E / EPIC-02).

Scope
-----
Creates a concrete RuntimeBinding from the approved paper DeploymentPlan
packet produced by MGMT-PAPER-003, then materializes the downstream
RuntimeBootstrapRequest and PantheonRuntimeContext projections needed by
paper telemetry and OODA packet tasks.

The packet is paper-only: it carries no broker credentials, keeps live broker
and capital-binding side effects disabled, and targets the current
pantheon/lean bridge rather than lean-platform.

Usage
-----
Run as a standalone script to generate the evidence artifact:

    python3 services/control-plane/governance/paper_runtime_binding.py

Or import the factory from another module:

    from paper_runtime_binding import build_evidence_packet
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from paper_deployment_plan import (
        ENGINE_BRIDGE_COMMIT,
        ENGINE_BRIDGE_PATH,
        ENGINE_BRIDGE_REPO,
        PAPER_RUNTIME_PROFILE,
        PAPER_RUNTIME_ROLE,
        build_evidence_packet as build_deployment_plan_packet,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from paper_deployment_plan import (  # type: ignore
        ENGINE_BRIDGE_COMMIT,
        ENGINE_BRIDGE_PATH,
        ENGINE_BRIDGE_REPO,
        PAPER_RUNTIME_PROFILE,
        PAPER_RUNTIME_ROLE,
        build_evidence_packet as build_deployment_plan_packet,
    )

from services.execution.lean_runtime.bootstrap_contract import (
    PANTHEON_LEAN_REMOTE,
    PANTHEON_LEAN_SOURCE_PATH,
    BootstrapContractError,
    materialize_runtime_bootstrap_request,
)
from services.execution.lean_runtime.runtime_context import (
    PantheonRuntimeContext,
    RuntimeContextError,
    RuntimeContextSource,
)


PAPER_ENVIRONMENT = "paper"
TASK_ID = "MGMT-PAPER-004"
RUNTIME_BINDING_ID = "8d7e6dbb-6f5c-4e5b-89f1-66df62f9d004"
PAPER_RUNTIME_ID = "lean-paper-runtime-mgmt-001"
RUNTIME_BINDING_EFFECTIVE_AT = "2026-05-15T16:44:00Z"
BOOTSTRAP_REQUEST_ID = "runtime-bootstrap-paper-mgmt-004"
TRACE_ID = "03829d25-2c9f-44f0-981e-56a94d8ff004"

_RUNTIME_BINDING_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "execution"
    / "runtime-manager"
    / "runtime_binding.py"
)
_EVIDENCE_PATH = (
    _ROOT
    / "support"
    / "evidence"
    / "MGMT-PAPER-004-paper-runtime-binding.json"
)
_DEPLOYMENT_PLAN_EVIDENCE_PATH = (
    _ROOT
    / "support"
    / "evidence"
    / "MGMT-PAPER-003-paper-deployment-plan.json"
)


def _load_runtime_binding_module():
    spec = importlib.util.spec_from_file_location(
        "pantheon_runtime_manager_runtime_binding",
        _RUNTIME_BINDING_MODULE_PATH,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load RuntimeBinding module at {_RUNTIME_BINDING_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("pantheon_runtime_manager_runtime_binding", module)
    spec.loader.exec_module(module)
    return module


_runtime_binding_module = _load_runtime_binding_module()
RuntimeBinding = _runtime_binding_module.RuntimeBinding
RuntimeBindingStatus = _runtime_binding_module.RuntimeBindingStatus
RuntimeBindingError = _runtime_binding_module.RuntimeBindingError
validate_binding = _runtime_binding_module.validate_binding


@dataclass(frozen=True)
class PaperRuntimeBindingContext:
    """Stable identifiers for the paper-loop RuntimeBinding packet."""

    binding_id: str = RUNTIME_BINDING_ID
    runtime_id: str = PAPER_RUNTIME_ID
    effective_at: str = RUNTIME_BINDING_EFFECTIVE_AT
    request_id: str = BOOTSTRAP_REQUEST_ID
    trace_id: str = TRACE_ID
    runtime_adapter_version: str = "0.1.0"
    context_source: str = RuntimeContextSource.LAUNCH_MANIFEST.value


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_context() -> PaperRuntimeBindingContext:
    return PaperRuntimeBindingContext()


def _stable_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _deployment_packet_payload(
    deployment_packet: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if deployment_packet is not None:
        return deployment_packet
    if _DEPLOYMENT_PLAN_EVIDENCE_PATH.exists():
        payload = json.loads(_DEPLOYMENT_PLAN_EVIDENCE_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"{_DEPLOYMENT_PLAN_EVIDENCE_PATH} must contain a JSON object")
        return payload
    return build_deployment_plan_packet(generated_at="2026-05-15T16:43:00Z")


def _deployment_plan_with_checksum(deployment_packet: Mapping[str, Any]) -> Dict[str, Any]:
    plan = dict(_required_mapping(deployment_packet, "deployment_plan"))
    registry_entry = _required_mapping(deployment_packet, "approved_registry_entry")
    checksum = str(registry_entry.get("checksum") or "").strip()
    if not checksum:
        projection = _required_mapping(deployment_packet, "deployment_execution_projection")
        projection_metadata = _required_mapping(projection, "metadata")
        checksum = str(projection_metadata.get("checksum") or "").strip()
    if not checksum:
        raise ValueError("MGMT-PAPER-003 packet must provide approved artifact checksum")

    plan["artifact_checksum"] = checksum
    metadata = dict(plan.get("metadata") or {})
    metadata.setdefault("artifact_checksum", checksum)
    metadata.setdefault("strategy_id", plan.get("strategy_id"))
    plan["metadata"] = metadata
    return plan


def build_paper_runtime_binding(
    ctx: PaperRuntimeBindingContext | None = None,
    *,
    deployment_packet: Mapping[str, Any] | None = None,
    launch_manifest_hash: str | None = None,
) -> Any:
    """Create the RuntimeBinding for the paper DeploymentPlan evidence packet."""
    ctx = ctx or default_context()
    packet = _deployment_packet_payload(deployment_packet)
    plan = _deployment_plan_with_checksum(packet)
    plan_metadata = dict(plan.get("metadata") or {})
    persona_capital_binding_id = str(plan_metadata.get("persona_capital_binding_id") or "").strip()
    if not persona_capital_binding_id:
        input_ref = _required_mapping(packet, "runtime_binding_input_ref")
        persona_capital_binding_id = str(input_ref.get("persona_capital_binding_id") or "").strip()
    if not persona_capital_binding_id:
        raise ValueError("persona_capital_binding_id is required to create paper RuntimeBinding")

    metadata: Dict[str, Any] = {
        "environment": PAPER_ENVIRONMENT,
        "runtime_role": PAPER_RUNTIME_ROLE,
        "runtime_profile": PAPER_RUNTIME_PROFILE,
        "runtime_adapter_version": ctx.runtime_adapter_version,
        "runtime_config_ref": plan.get("runtime_config_ref"),
        "strategy_id": plan.get("strategy_id"),
        "artifact_checksum": plan["artifact_checksum"],
        "approval_decision_id": plan.get("approval_decision_id"),
        "engine_bridge_repo": ENGINE_BRIDGE_REPO,
        "engine_bridge_path": ENGINE_BRIDGE_PATH,
        "engine_bridge_commit": ENGINE_BRIDGE_COMMIT,
        "context_source": ctx.context_source,
        "write_owner": "runtime-manager",
        "runtime_action": plan.get("runtime_action"),
        "live_broker_enabled": False,
        "live_capital_binding_enabled": False,
        "live_capital_side_effects": False,
    }
    if launch_manifest_hash:
        metadata["launch_manifest_hash"] = launch_manifest_hash

    return RuntimeBinding(
        binding_id=ctx.binding_id,
        runtime_id=ctx.runtime_id,
        capital_pool_id=str(plan["capital_pool_id"]),
        artifact_id=str(plan["artifact_id"]),
        artifact_version=str(plan["artifact_version"]),
        deployment_mode=PAPER_ENVIRONMENT,
        effective_at=ctx.effective_at,
        status=RuntimeBindingStatus.ACTIVE.value,
        plan_id=str(plan["plan_id"]),
        persona_capital_binding_id=persona_capital_binding_id,
        metadata=metadata,
    )


def build_runtime_bootstrap_request(
    binding: Any,
    deployment_packet: Mapping[str, Any],
    ctx: PaperRuntimeBindingContext | None = None,
):
    """Materialize RuntimeBootstrapRequest from DeploymentPlan + RuntimeBinding."""
    ctx = ctx or default_context()
    plan = _deployment_plan_with_checksum(deployment_packet)
    return materialize_runtime_bootstrap_request(
        deployment_plan=plan,
        runtime_binding=binding,
        request_id=ctx.request_id,
        trace_id=ctx.trace_id,
    )


def build_pantheon_runtime_context(bootstrap_request: Any) -> PantheonRuntimeContext:
    """Validate the launch-manifest projection as PantheonRuntimeContext."""
    return PantheonRuntimeContext.from_mapping(
        bootstrap_request.to_dict(),
        source=RuntimeContextSource.LAUNCH_MANIFEST,
        expected_stage=PAPER_ENVIRONMENT,
        managed_runtime=True,
    )


def validate_paper_runtime_binding_packet(packet: Mapping[str, Any]) -> List[str]:
    """Validate packet-level paper RuntimeBinding invariants."""
    errors: List[str] = []
    if packet.get("task_id") != TASK_ID:
        errors.append(f"task_id must be {TASK_ID}")
    if packet.get("environment") != PAPER_ENVIRONMENT:
        errors.append("environment must be paper")
    if packet.get("live_capital_side_effects") is not False:
        errors.append("live_capital_side_effects must be false")

    binding_raw = packet.get("runtime_binding")
    if not isinstance(binding_raw, Mapping):
        errors.append("runtime_binding must be present")
        return errors
    try:
        binding = RuntimeBinding.from_dict(dict(binding_raw))
    except Exception as exc:
        errors.append(f"runtime_binding restore failed: {exc}")
        return errors

    errors.extend(f"runtime_binding: {error}" for error in validate_binding(binding))
    if binding.deployment_mode != PAPER_ENVIRONMENT:
        errors.append("runtime_binding deployment_mode must be paper")
    if binding.status != RuntimeBindingStatus.ACTIVE.value:
        errors.append("runtime_binding status must be active")

    plan_ref = packet.get("deployment_plan_ref")
    if isinstance(plan_ref, Mapping):
        if plan_ref.get("deployment_plan_id") != binding.plan_id:
            errors.append("deployment_plan_ref deployment_plan_id mismatch")
        if plan_ref.get("target_stage") != binding.deployment_mode:
            errors.append("deployment_plan_ref target_stage mismatch")
        if plan_ref.get("artifact_id") != binding.artifact_id:
            errors.append("deployment_plan_ref artifact_id mismatch")
        if plan_ref.get("artifact_version") != binding.artifact_version:
            errors.append("deployment_plan_ref artifact_version mismatch")
        if plan_ref.get("runtime_action") != "deploy_new_binding":
            errors.append("deployment_plan_ref runtime_action must be deploy_new_binding")
    else:
        errors.append("deployment_plan_ref must be present")

    bootstrap = packet.get("runtime_bootstrap_request")
    if isinstance(bootstrap, Mapping):
        if bootstrap.get("runtime_binding_id") != binding.binding_id:
            errors.append("runtime_bootstrap_request runtime_binding_id mismatch")
        if bootstrap.get("deployment_plan_id") != binding.plan_id:
            errors.append("runtime_bootstrap_request deployment_plan_id mismatch")
        if bootstrap.get("deployment_stage") != PAPER_ENVIRONMENT:
            errors.append("runtime_bootstrap_request deployment_stage must be paper")
        bridge = bootstrap.get("bridge") if isinstance(bootstrap.get("bridge"), Mapping) else {}
        if bridge.get("remote") != PANTHEON_LEAN_REMOTE:
            errors.append("runtime_bootstrap_request bridge.remote must be pantheon-lean")
        if bridge.get("source_path") != PANTHEON_LEAN_SOURCE_PATH:
            errors.append("runtime_bootstrap_request bridge.source_path must be pantheon/lean")
    else:
        errors.append("runtime_bootstrap_request must be present")

    runtime_context = packet.get("pantheon_runtime_context")
    if isinstance(runtime_context, Mapping):
        try:
            restored_context = PantheonRuntimeContext.from_mapping(
                runtime_context,
                source=RuntimeContextSource.LAUNCH_MANIFEST,
                expected_stage=PAPER_ENVIRONMENT,
                managed_runtime=True,
            )
            if restored_context.runtime_binding_id != binding.binding_id:
                errors.append("pantheon_runtime_context runtime_binding_id mismatch")
            if restored_context.deployment_plan_id != binding.plan_id:
                errors.append("pantheon_runtime_context deployment_plan_id mismatch")
        except RuntimeContextError as exc:
            errors.append(f"pantheon_runtime_context invalid: {exc}")
    else:
        errors.append("pantheon_runtime_context must be present")

    env_preview = packet.get("runtime_env_preview")
    if isinstance(env_preview, Mapping):
        if env_preview.get("PANTHEON_RUNTIME_BINDING_ID") != binding.binding_id:
            errors.append("runtime_env_preview runtime binding mismatch")
        if env_preview.get("PANTHEON_DEPLOYMENT_STAGE") != PAPER_ENVIRONMENT:
            errors.append("runtime_env_preview deployment stage must be paper")
        if any(_raw_secret_env_key(str(key)) for key in env_preview):
            errors.append("runtime_env_preview must not expose raw credential env keys")
    else:
        errors.append("runtime_env_preview must be present")

    safety = packet.get("safety_assertions")
    if isinstance(safety, Mapping):
        for key, value in safety.items():
            if value is not True:
                errors.append(f"safety_assertion failed: {key}")
    else:
        errors.append("safety_assertions must be present")

    if _contains_lean_platform(packet):
        errors.append("packet must not reference lean-platform")

    return errors


def build_evidence_packet(
    ctx: PaperRuntimeBindingContext | None = None,
    *,
    deployment_packet: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
) -> Dict[str, Any]:
    """Build the MGMT-PAPER-004 RuntimeBinding evidence packet."""
    ctx = ctx or default_context()
    deployment_packet = _deployment_packet_payload(deployment_packet)
    plan = _deployment_plan_with_checksum(deployment_packet)

    provisional_binding = build_paper_runtime_binding(ctx, deployment_packet=deployment_packet)
    provisional_request = build_runtime_bootstrap_request(provisional_binding, deployment_packet, ctx)
    launch_manifest_hash = _stable_hash(provisional_request.to_dict())

    binding = build_paper_runtime_binding(
        ctx,
        deployment_packet=deployment_packet,
        launch_manifest_hash=launch_manifest_hash,
    )
    bootstrap_request = build_runtime_bootstrap_request(binding, deployment_packet, ctx)
    runtime_context = build_pantheon_runtime_context(bootstrap_request)
    runtime_env_preview = bootstrap_request.to_runtime_env()

    packet: Dict[str, Any] = {
        "task_id": TASK_ID,
        "epic": "EPIC-02 Management Paper Loop Proof",
        "environment": PAPER_ENVIRONMENT,
        "generated_at": generated_at or utc_now(),
        "live_capital_side_effects": False,
        "source_artifacts": {
            "strategy_spec": "support/evidence/MGMT-PAPER-001-paper-strategy-spec.json",
            "approval_decision": "support/evidence/MGMT-PAPER-002-paper-approval-decision.json",
            "deployment_plan": "support/evidence/MGMT-PAPER-003-paper-deployment-plan.json",
        },
        "deployment_plan_ref": {
            "deployment_plan_id": plan["plan_id"],
            "approval_decision_id": plan["approval_decision_id"],
            "artifact_id": plan["artifact_id"],
            "artifact_version": plan["artifact_version"],
            "artifact_checksum": plan["artifact_checksum"],
            "strategy_id": plan["strategy_id"],
            "capital_pool_id": plan["capital_pool_id"],
            "persona_capital_binding_id": binding.persona_capital_binding_id,
            "target_stage": plan["target_stage"],
            "runtime_action": plan["runtime_action"],
            "status": plan["status"],
        },
        "runtime_binding": binding.to_dict(),
        "runtime_binding_projection": {
            "runtime_binding_id": binding.binding_id,
            "runtime_id": binding.runtime_id,
            "deployment_plan_id": binding.plan_id,
            "deployment_stage": binding.deployment_mode,
            "runtime_role": PAPER_RUNTIME_ROLE,
            "runtime_state": binding.status,
            "artifact_id": binding.artifact_id,
            "artifact_version": binding.artifact_version,
            "artifact_checksum": binding.metadata["artifact_checksum"],
            "strategy_id": binding.metadata["strategy_id"],
            "capital_pool_id": binding.capital_pool_id,
            "persona_capital_binding_id": binding.persona_capital_binding_id,
            "bridge": {
                "repo": ENGINE_BRIDGE_REPO,
                "path": ENGINE_BRIDGE_PATH,
                "commit": ENGINE_BRIDGE_COMMIT,
                "runtime_adapter_version": binding.metadata["runtime_adapter_version"],
            },
            "launch_manifest_hash": launch_manifest_hash,
            "context_source": ctx.context_source,
        },
        "runtime_bootstrap_request": bootstrap_request.to_dict(),
        "pantheon_runtime_context": runtime_context.to_dict(),
        "runtime_env_preview": runtime_env_preview,
        "runtime_events": [
            {
                "event_type": "RuntimeBindingCreated",
                "runtime_binding_id": binding.binding_id,
                "runtime_id": binding.runtime_id,
                "deployment_plan_id": binding.plan_id,
                "deployment_stage": binding.deployment_mode,
                "engine_bridge_commit": ENGINE_BRIDGE_COMMIT,
                "event_time": binding.effective_at,
            },
            {
                "event_type": "RuntimeContextMaterialized",
                "runtime_binding_id": binding.binding_id,
                "runtime_id": binding.runtime_id,
                "deployment_plan_id": binding.plan_id,
                "deployment_stage": binding.deployment_mode,
                "context_source": ctx.context_source,
                "launch_manifest_hash": launch_manifest_hash,
                "event_time": binding.effective_at,
            },
        ],
        "ooda_act_ref": {
            "approval_decision_id": plan["approval_decision_id"],
            "deployment_plan_id": binding.plan_id,
            "runtime_binding_id": binding.binding_id,
            "runtime_id": binding.runtime_id,
        },
        "telemetry_context_ref": {
            "runtime_binding_id": binding.binding_id,
            "runtime_id": binding.runtime_id,
            "deployment_plan_id": binding.plan_id,
            "deployment_stage": binding.deployment_mode,
            "artifact_id": binding.artifact_id,
            "capital_pool_id": binding.capital_pool_id,
        },
        "safety_assertions": {
            "paper_environment": binding.deployment_mode == PAPER_ENVIRONMENT,
            "deployment_plan_backing_present": bool(binding.plan_id),
            "plan_target_stage_matches_binding": plan["target_stage"] == binding.deployment_mode,
            "runtime_binding_active": binding.status == RuntimeBindingStatus.ACTIVE.value,
            "persona_capital_binding_present": bool(binding.persona_capital_binding_id),
            "bootstrap_references_runtime_binding": (
                bootstrap_request.runtime_binding_id == binding.binding_id
            ),
            "runtime_context_materialized": (
                runtime_context.runtime_binding_id == binding.binding_id
            ),
            "bridge_points_to_pantheon_lean": (
                runtime_context.bridge.repo == PANTHEON_LEAN_REMOTE
                and runtime_context.bridge.path == PANTHEON_LEAN_SOURCE_PATH
            ),
            "no_lean_platform_target": not _contains_lean_platform(
                {
                    "binding": binding.to_dict(),
                    "bootstrap": bootstrap_request.to_dict(),
                    "runtime_context": runtime_context.to_dict(),
                }
            ),
            "live_broker_disabled": bootstrap_request.runtime_config.live_broker_enabled is False,
            "live_capital_binding_disabled": binding.metadata["live_capital_binding_enabled"] is False,
            "sensitive_material_absent": not _runtime_env_has_raw_credential_key(runtime_env_preview),
        },
        "paper_loop_chain": [
            "MGMT-PAPER-001: candidate StrategySpec",
            "MGMT-PAPER-002: ApprovalDecision packet",
            "MGMT-PAPER-003: DeploymentPlan packet",
            "MGMT-PAPER-004: paper RuntimeBinding packet <- this artifact",
            "MGMT-PAPER-005: telemetry packet",
            "MGMT-PAPER-006: EvolutionDecision review packet",
            "MGMT-PAPER-007: complete OODA packet",
        ],
        "validation_errors": [],
    }
    packet["validation_errors"] = validate_paper_runtime_binding_packet(packet)
    return packet


def write_evidence_packet(packet: Mapping[str, Any], out_path: Path) -> Dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(packet)
    out_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return data


def _required_mapping(mapping: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = mapping.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be present")
    return value


def _contains_lean_platform(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_lean_platform(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_lean_platform(child) for child in value)
    return isinstance(value, str) and "lean-platform" in value.lower()


def _raw_secret_env_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in ("api_key", "password", "private_key", "secret", "token"))


def _runtime_env_has_raw_credential_key(env: Mapping[str, str]) -> bool:
    return any(_raw_secret_env_key(str(key)) for key in env)


def main() -> int:
    print("=== MGMT-PAPER-004: paper RuntimeBinding packet ===\n")
    try:
        packet = build_evidence_packet()
    except (BootstrapContractError, RuntimeBindingError, RuntimeContextError, ValueError) as exc:
        print(f"FAIL: runtime binding packet error: {exc}")
        return 1

    errors = packet["validation_errors"]
    if errors:
        print(f"FAIL: validation errors: {errors}")
        return 1

    write_evidence_packet(packet, _EVIDENCE_PATH)
    binding = packet["runtime_binding"]
    bootstrap = packet["runtime_bootstrap_request"]

    print(f"  runtime_binding_id: {binding['binding_id']}")
    print(f"  runtime_id        : {binding['runtime_id']}")
    print(f"  deployment_plan   : {binding['plan_id']}")
    print(f"  artifact          : {binding['artifact_id']}@{binding['artifact_version']}")
    print(f"  stage/status      : {binding['deployment_mode']} / {binding['status']}")
    print(f"  bootstrap_request : {bootstrap['request_id']}")
    print(f"  validation        : {'PASS (no errors)' if not errors else 'FAIL'}")
    print(f"\n  evidence packet written to: {_EVIDENCE_PATH}")
    print("\n=== PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
