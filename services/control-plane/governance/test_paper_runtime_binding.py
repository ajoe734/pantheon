"""
Unit tests for paper_runtime_binding (MGMT-PAPER-004).

Verifies:
- Paper RuntimeBinding factory creates an active paper binding from DeploymentPlan
- RuntimeBinding, RuntimeBootstrapRequest, and PantheonRuntimeContext links match
- Paper fail-closed invariants are present
- Runtime env preview excludes raw credential keys
- Evidence packet writes parseable JSON with required OODA/telemetry refs
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from paper_runtime_binding import (  # noqa: E402
    ENGINE_BRIDGE_PATH,
    PAPER_ENVIRONMENT,
    RUNTIME_BINDING_ID,
    RuntimeBinding,
    RuntimeBindingStatus,
    build_evidence_packet,
    build_pantheon_runtime_context,
    build_paper_runtime_binding,
    validate_binding,
    validate_paper_runtime_binding_packet,
    write_evidence_packet,
)

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name}" + (f" - {detail}" if detail else ""))


def test_factory_creates_active_paper_binding() -> None:
    print("[1] Factory creates active paper RuntimeBinding")
    binding = build_paper_runtime_binding()

    check("binding_id matches", binding.binding_id == RUNTIME_BINDING_ID)
    check("deployment_mode is paper", binding.deployment_mode == PAPER_ENVIRONMENT)
    check("status is active", binding.status == RuntimeBindingStatus.ACTIVE.value)
    check("plan_id is deployment-plan-paper-001", binding.plan_id == "deployment-plan-paper-001")
    check("artifact id matches paper spec", binding.artifact_id == "strategy-spec-paper-qlib-lgbm-001")
    check("capital pool matches paper pool", binding.capital_pool_id == "capital-pool-paper-001")
    check("persona capital binding present", binding.persona_capital_binding_id == "pcb-paper-quant-001")
    check("validation has no errors", validate_binding(binding) == [])
    check("bridge path in metadata", binding.metadata["engine_bridge_path"] == ENGINE_BRIDGE_PATH)
    check("live broker disabled", binding.metadata["live_broker_enabled"] is False)


def test_bootstrap_request_and_runtime_context_match_binding() -> None:
    print("\n[2] Bootstrap request and runtime context")
    packet = build_evidence_packet(generated_at="2026-05-15T16:45:00Z")
    binding = RuntimeBinding.from_dict(packet["runtime_binding"])

    check("packet validation empty", packet["validation_errors"] == [], str(packet["validation_errors"]))
    bootstrap = packet["runtime_bootstrap_request"]
    runtime_context = packet["pantheon_runtime_context"]

    check("bootstrap references binding", bootstrap["runtime_binding_id"] == binding.binding_id)
    check("bootstrap references plan", bootstrap["deployment_plan_id"] == binding.plan_id)
    check("bootstrap deployment stage is paper", bootstrap["deployment_stage"] == "paper")
    check("bootstrap live broker disabled", bootstrap["runtime_config"]["live_broker_enabled"] is False)
    check("runtime context references binding", runtime_context["runtime_binding_id"] == binding.binding_id)
    check("runtime context references plan", runtime_context["deployment_plan_id"] == binding.plan_id)
    check("runtime context bridge path is pantheon/lean", runtime_context["bridge"]["path"] == ENGINE_BRIDGE_PATH)
    check("runtime context source is launch_manifest", runtime_context["context_source"] == "launch_manifest")

    class RequestView:
        def to_dict(self):
            return bootstrap

    restored_context = build_pantheon_runtime_context(RequestView())
    check("restored context validates", restored_context.runtime_binding_id == binding.binding_id)


def test_evidence_packet_links_and_safety() -> None:
    print("\n[3] Evidence packet links and safety")
    packet = build_evidence_packet(generated_at="2026-05-15T16:45:00Z")

    check("task_id is MGMT-PAPER-004", packet["task_id"] == "MGMT-PAPER-004")
    check("environment is paper", packet["environment"] == "paper")
    check("validation_errors empty", packet["validation_errors"] == [], str(packet["validation_errors"]))
    check("ooda act ref has runtime binding", packet["ooda_act_ref"]["runtime_binding_id"] == RUNTIME_BINDING_ID)
    check("telemetry ref has runtime binding", packet["telemetry_context_ref"]["runtime_binding_id"] == RUNTIME_BINDING_ID)
    check("paper loop chain present", "paper_loop_chain" in packet)

    safety = packet["safety_assertions"]
    check("all safety assertions true", all(value is True for value in safety.values()), str(safety))
    check("live capital side effects false", packet["live_capital_side_effects"] is False)
    check("launch manifest hash recorded", packet["runtime_binding"]["metadata"]["launch_manifest_hash"].startswith("sha256:"))


def test_packet_mutation_catches_failures() -> None:
    print("\n[4] Packet mutation guards")
    packet = build_evidence_packet(generated_at="2026-05-15T16:45:00Z")

    stage_mutation = json.loads(json.dumps(packet))
    stage_mutation["runtime_binding"]["deployment_mode"] = "live"
    errors = validate_paper_runtime_binding_packet(stage_mutation)
    check("live deployment_mode is rejected", any("deployment_mode" in error for error in errors), str(errors))

    bootstrap_mutation = json.loads(json.dumps(packet))
    bootstrap_mutation["runtime_bootstrap_request"]["runtime_binding_id"] = "different-binding"
    errors = validate_paper_runtime_binding_packet(bootstrap_mutation)
    check("bootstrap binding mismatch is rejected", any("runtime_binding_id mismatch" in error for error in errors), str(errors))

    env_mutation = json.loads(json.dumps(packet))
    env_mutation["runtime_env_preview"]["PANTHEON_BROKER_SECRET"] = "plain-secret"
    errors = validate_paper_runtime_binding_packet(env_mutation)
    check("raw credential env key is rejected", any("credential env keys" in error for error in errors), str(errors))

    bridge_mutation = json.loads(json.dumps(packet))
    bridge_mutation["runtime_binding"]["metadata"]["engine_bridge_path"] = "lean-platform"
    errors = validate_paper_runtime_binding_packet(bridge_mutation)
    check("lean-platform target is rejected", any("lean-platform" in error for error in errors), str(errors))


def test_evidence_packet_write() -> None:
    print("\n[5] Evidence packet write")
    packet = build_evidence_packet(generated_at="2026-05-15T16:45:00Z")
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = Path(f.name)

    try:
        write_evidence_packet(packet, tmp)
        raw = json.loads(tmp.read_text(encoding="utf-8"))
        check("written task_id matches", raw["task_id"] == "MGMT-PAPER-004")
        check("written validation is empty", raw["validation_errors"] == [])
        check("runtime binding key present", "runtime_binding" in raw)
        check("runtime context key present", "pantheon_runtime_context" in raw)
    finally:
        os.unlink(tmp)


def main() -> int:
    global PASS, FAIL
    print("=== MGMT-PAPER-004: paper RuntimeBinding unit tests ===\n")

    test_factory_creates_active_paper_binding()
    test_bootstrap_request_and_runtime_context_match_binding()
    test_evidence_packet_links_and_safety()
    test_packet_mutation_catches_failures()
    test_evidence_packet_write()

    print(f"\n=== Results: {PASS} PASS, {FAIL} FAIL ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
