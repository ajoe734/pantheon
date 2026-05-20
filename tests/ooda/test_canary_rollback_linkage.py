from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from services.ooda.canary_packet_model import (
    CanaryActStage,
    CanaryAssertions,
    CanaryDecideStage,
    CanaryLearnStage,
    CanaryObserveStage,
    CanaryOodaPacket,
    CanaryOodaStages,
    CanaryOrientStage,
    CanaryPacketStatus,
)
from services.ooda.canary_rollback_drill_linkage import (
    DEFAULT_EP5_ROLLBACK_DRILL_OUTPUT_REF,
    EP5_ROLLBACK_DRILL_REF_PREFIX,
    CanaryRollbackDrillLinkageError,
    build_canary_rollback_drill_linkage,
    link_canary_packet_to_ep5_rollback_drill,
    load_ep5_rollback_drill_output,
    validate_canary_rollback_drill_linkage,
    validate_ep5_rollback_drill_output,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
EP5_ROLLBACK_OUTPUT = REPO_ROOT / DEFAULT_EP5_ROLLBACK_DRILL_OUTPUT_REF


def test_links_canary_packet_rollback_ref_to_ep5_output() -> None:
    output = _ep5_output()
    packet = _closed_packet_without_rollback(output)

    linkage = link_canary_packet_to_ep5_rollback_drill(packet, output)

    evidence_id = output["rollback_drill_evidence"]["evidence_id"]
    assert linkage.rollback_drill_ref == f"{EP5_ROLLBACK_DRILL_REF_PREFIX}{evidence_id}"
    assert linkage.source_ref == DEFAULT_EP5_ROLLBACK_DRILL_OUTPUT_REF
    assert linkage.proof_packet_id == output["proof_packet"]["packet_id"]
    assert linkage.runtime_binding_ref == (
        f"runtime-binding://{output['proof_packet']['runtime']['runtime_binding_id']}"
    )
    assert packet.act.rollback_drill_ref == linkage.rollback_drill_ref
    assert packet.assertions.rollback_drill_completed is True
    assert validate_canary_rollback_drill_linkage(packet, output) == []
    assert packet.validate() == []


def test_build_linkage_validates_ep5_output_before_generating_ref() -> None:
    output = _ep5_output()
    output["rollback_drill_completed"] = False
    output["proof_packet"]["proof"]["rollback_drill_completed"] = False

    with pytest.raises(CanaryRollbackDrillLinkageError, match="rollback_drill_completed"):
        build_canary_rollback_drill_linkage(output)


def test_validate_linkage_reports_stale_packet_ref() -> None:
    output = _ep5_output()
    packet = _closed_packet_without_rollback(output)
    packet.act.rollback_drill_ref = "rollback-drill://ep5-007-v2/stale"
    packet.assertions.rollback_drill_completed = True

    errors = validate_canary_rollback_drill_linkage(packet, output)

    expected_ref = (
        f"{EP5_ROLLBACK_DRILL_REF_PREFIX}"
        f"{output['rollback_drill_evidence']['evidence_id']}"
    )
    assert f"stages.act.rollback_drill_ref must equal {expected_ref}" in errors


def test_validate_ep5_output_requires_proof_evidence_ref() -> None:
    output = _ep5_output()
    output["proof_packet"]["result"]["evidence_refs"] = []

    errors = validate_ep5_rollback_drill_output(output)

    assert (
        "proof_packet.result.evidence_refs must include "
        f"rollback-drill:{output['rollback_drill_evidence']['evidence_id']}"
    ) in errors


def _ep5_output() -> dict[str, Any]:
    return deepcopy(load_ep5_rollback_drill_output(EP5_ROLLBACK_OUTPUT))


def _closed_packet_without_rollback(output: dict[str, Any]) -> CanaryOodaPacket:
    runtime = output["proof_packet"]["runtime"]
    return CanaryOodaPacket(
        packet_id="canary-ooda-004-rollback-linkage",
        status=CanaryPacketStatus.CLOSED,
        stages=CanaryOodaStages(
            observe=CanaryObserveStage(
                source_refs=["source://canary-ooda-004/rollback-drill"],
                telemetry_refs=["telemetry://canary-ooda-004/heartbeat"],
            ),
            orient=CanaryOrientStage(
                strategy_spec_ref="strategy-spec://canary-ooda-004@1.0.0",
                experiment_run_ref="experiment-run://canary-ooda-004/drill",
                drift_report_ref=None,
            ),
            decide=CanaryDecideStage(
                approval_decision_ref="approval://canary-ooda-004/risk-owner-operator",
                deployment_plan_ref=f"deployment-plan://{runtime['deployment_plan_id']}",
                human_gate_ref="human-gate://canary-ooda-004/risk-owner/operator",
            ),
            act=CanaryActStage(
                runtime_binding_ref=f"runtime-binding://{runtime['runtime_binding_id']}",
                canary_runtime_ref=f"runtime://{runtime['runtime_id']}",
                rollback_drill_ref="",
            ),
            learn=CanaryLearnStage(
                incident_ref="incident://canary-ooda-004/no-incident",
                postmortem_ref="postmortem://canary-ooda-004/no-incident",
                evolution_proposal_ref="evolution-proposal://canary-ooda-004/no-change",
            ),
        ),
        assertions=CanaryAssertions(
            live_capital_scope_limited=True,
            rollback_drill_completed=False,
            telemetry_ingested=True,
            human_gate_valid=True,
            validation_errors_empty=True,
        ),
    )
