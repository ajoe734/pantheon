"""
Unit tests for paper_deployment_plan (MGMT-PAPER-003).

Verifies:
- Paper DeploymentPlan factory creates none -> paper activate plan
- ApprovalDecision and approved registry entry links match
- Paper fail-closed invariants are present
- Runtime bootstrap preview references DeploymentPlan and excludes secrets
- Evidence packet writes parseable JSON with required OODA refs
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from deployment_plan import (  # noqa: E402
    DeploymentPlan,
    DeploymentPlanError,
    DeploymentStage,
    RuntimeAction,
    TransitionType,
    validate_plan,
)
from paper_deployment_plan import (  # noqa: E402
    ENGINE_BRIDGE_PATH,
    PAPER_RUNTIME_PROFILE,
    PaperDeploymentContext,
    build_approval_decision_ref,
    build_approved_registry_entry,
    build_evidence_packet,
    build_paper_deployment_plan,
    build_runtime_bootstrap_preview,
    default_context,
    validate_paper_deployment_packet,
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


def _ctx(**overrides) -> PaperDeploymentContext:
    base = default_context().__dict__.copy()
    base.update(overrides)
    return PaperDeploymentContext(**base)


def test_factory_creates_paper_activate_plan() -> None:
    print("[1] Factory creates paper activate plan")
    ctx = _ctx(plan_id="deployment-plan-test-001")
    plan = build_paper_deployment_plan(ctx)

    check("plan_id matches", plan.plan_id == ctx.plan_id)
    check("current_stage is none", plan.current_stage == DeploymentStage.NONE)
    check("target_stage is paper", plan.target_stage == DeploymentStage.PAPER)
    check("transition_type is activate", plan.transition_type == TransitionType.ACTIVATE)
    check("runtime_action is deploy_new_binding", plan.runtime_action == RuntimeAction.DEPLOY_NEW_BINDING)
    check("status is approved", plan.status == "approved")
    check("capital_scale_pct is zero", plan.scale is not None and plan.scale.capital_scale_pct == 0.0)
    check("gross_scale_pct is 100", plan.scale is not None and plan.scale.gross_scale_pct == 100.0)
    check("validate_plan has no errors", validate_plan(plan) == [])


def test_approval_and_registry_links_match() -> None:
    print("\n[2] Approval and registry links")
    ctx = _ctx()
    approval = build_approval_decision_ref(ctx)
    registry_entry = build_approved_registry_entry(ctx)
    plan = build_paper_deployment_plan(ctx, approval_decision=approval, registry_entry=registry_entry)

    check("approval decision id matches", plan.approval_decision_id == approval["decision_id"])
    check("artifact id matches registry id", plan.artifact_id == registry_entry["registry_id"])
    check("artifact version matches", plan.artifact_version == registry_entry["version"])
    check("registry is approved", registry_entry["artifact_state"] == "approved")
    check("strategy id preserved", plan.strategy_id == ctx.strategy_id)

    bad_approval = dict(approval)
    bad_approval["target_id"] = "strategy-spec-other"
    try:
        build_paper_deployment_plan(ctx, approval_decision=bad_approval, registry_entry=registry_entry)
        check("mismatched approval target should fail", False, "no exception raised")
    except DeploymentPlanError:
        check("mismatched approval target fails", True)


def test_runtime_bootstrap_preview_is_paper_and_secretless() -> None:
    print("\n[3] Runtime bootstrap preview")
    ctx = _ctx()
    registry_entry = build_approved_registry_entry(ctx)
    plan = build_paper_deployment_plan(ctx, registry_entry=registry_entry)
    preview = build_runtime_bootstrap_preview(plan, registry_entry)

    check("preview references plan", preview["deployment_plan_id"] == plan.plan_id)
    check("runtime role is paper", preview["runtime_role"] == "paper")
    check("deployment stage is paper", preview["deployment_stage"] == "paper")
    check("bridge path is pantheon/lean", preview["bridge"]["path"] == ENGINE_BRIDGE_PATH)
    check("runtime profile present", preview["runtime_config"]["runtime_profile"] == PAPER_RUNTIME_PROFILE)
    check("live broker disabled", preview["runtime_config"]["live_broker_enabled"] is False)
    check("live capital binding disabled", preview["runtime_config"]["live_capital_binding_enabled"] is False)
    check("secrets excluded", preview["secrets_included"] is False)


def test_evidence_packet_validates() -> None:
    print("\n[4] Evidence packet validation")
    packet = build_evidence_packet(generated_at="2026-05-15T16:00:00Z")

    check("task_id is MGMT-PAPER-003", packet["task_id"] == "MGMT-PAPER-003")
    check("environment is paper", packet["environment"] == "paper")
    check("validation_errors empty", packet["validation_errors"] == [], str(packet["validation_errors"]))
    check("ooda decide ref has approval id", packet["ooda_decide_ref"]["approval_decision_id"] == "approval-paper-strategy-001")
    check("ooda decide ref has deployment plan id", packet["ooda_decide_ref"]["deployment_plan_id"] == "deployment-plan-paper-001")
    check("runtime binding input present", "runtime_binding_input_ref" in packet)
    check("paper loop chain present", "paper_loop_chain" in packet)

    restored = DeploymentPlan.from_dict(packet["deployment_plan"])
    check("deployment plan round-trips", restored.plan_id == packet["deployment_plan"]["plan_id"])


def test_packet_mutation_catches_failures() -> None:
    print("\n[5] Packet mutation guards")
    packet = build_evidence_packet(generated_at="2026-05-15T16:00:00Z")

    stage_mutation = json.loads(json.dumps(packet))
    stage_mutation["deployment_plan"]["target_stage"] = "live"
    errors = validate_paper_deployment_packet(stage_mutation)
    check("live target_stage is rejected", any("target_stage" in error for error in errors), str(errors))

    secret_mutation = json.loads(json.dumps(packet))
    secret_mutation["runtime_bootstrap_request_preview"]["secrets_included"] = True
    errors = validate_paper_deployment_packet(secret_mutation)
    check("secrets_included true is rejected", any("secrets" in error for error in errors), str(errors))


def test_evidence_packet_write() -> None:
    print("\n[6] Evidence packet write")
    packet = build_evidence_packet(generated_at="2026-05-15T16:00:00Z")
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = Path(f.name)

    try:
        write_evidence_packet(packet, tmp)
        raw = json.loads(tmp.read_text(encoding="utf-8"))
        check("written task_id matches", raw["task_id"] == "MGMT-PAPER-003")
        check("written validation is empty", raw["validation_errors"] == [])
        check("deployment plan key present", "deployment_plan" in raw)
        check("bootstrap preview key present", "runtime_bootstrap_request_preview" in raw)
    finally:
        os.unlink(tmp)


def main() -> int:
    global PASS, FAIL
    print("=== MGMT-PAPER-003: paper DeploymentPlan unit tests ===\n")

    test_factory_creates_paper_activate_plan()
    test_approval_and_registry_links_match()
    test_runtime_bootstrap_preview_is_paper_and_secretless()
    test_evidence_packet_validates()
    test_packet_mutation_catches_failures()
    test_evidence_packet_write()

    print(f"\n=== Results: {PASS} PASS, {FAIL} FAIL ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
