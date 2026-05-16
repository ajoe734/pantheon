"""
Unit tests for paper_strategy_spec (MGMT-PAPER-001).

Verifies:
- StrategySpec and candidate registry entry validate against canonical schemas
- IDs match the downstream MGMT-PAPER-002 ApprovalDecision target
- Paper-only invariants block live side effects and deployment-stage drift
- Evidence packet write produces parseable JSON
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from paper_strategy_spec import (
    PAPER_APPROVAL_DECISION_ID,
    PAPER_DATASET_REF,
    PAPER_EVALUATOR_REF_ID,
    PAPER_STRATEGY_SPEC_ID,
    PAPER_STRATEGY_VERSION,
    build_paper_strategy_spec_packet,
    build_registry_entry,
    build_strategy_spec,
    packet_validation_errors,
    validate_paper_invariants,
    validate_registry_entry,
    validate_strategy_spec,
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


def test_strategy_spec_schema() -> None:
    print("[1] StrategySpec schema")
    strategy_spec = build_strategy_spec()
    errors = validate_strategy_spec(strategy_spec)

    check("schema has no errors", errors == [], str(errors))
    check("spec_version is 1.0", strategy_spec["spec_version"] == "1.0")
    check("execution mode is paper", strategy_spec["execution_profile"]["execution_mode_hint"] == "paper")
    check("universe has at least 50 symbols", len(strategy_spec["market_scope"]["symbols"]) >= 50)
    check("approval is required", strategy_spec["governance"]["approval_required"] is True)


def test_candidate_registry_entry() -> None:
    print("\n[2] Candidate registry entry")
    strategy_spec = build_strategy_spec()
    registry_entry = build_registry_entry(strategy_spec)
    errors = validate_registry_entry(registry_entry)

    check("registry schema has no errors", errors == [], str(errors))
    check("registry_id matches paper approval target", registry_entry["registry_id"] == PAPER_STRATEGY_SPEC_ID)
    check("version matches paper approval target", registry_entry["version"] == PAPER_STRATEGY_VERSION)
    check("artifact_state is candidate", registry_entry["artifact_state"] == "candidate")
    check("deployment_summary stage is none", registry_entry["deployment_summary"]["current_stage"] == "none")
    check("checksum is sha256 length", len(registry_entry["checksum"]) == 64)


def test_paper_invariants() -> None:
    print("\n[3] Paper invariants")
    packet = build_paper_strategy_spec_packet(generated_at="2026-05-15T15:00:00Z")
    errors = packet_validation_errors(packet)
    semantic = validate_paper_invariants(
        packet["strategy_spec"],
        packet["registry_entry"],
        packet["paper_evaluator_result"],
    )

    check("packet validation has no errors", errors == [], str(errors))
    check("semantic invariants have no errors", semantic == [], str(semantic))
    check("top-level live side effects false", packet["live_capital_side_effects"] is False)
    check(
        "evaluator live side effects false",
        packet["paper_evaluator_result"]["checks"]["live_capital_side_effects"] is False,
    )
    check("dataset ref is stable", packet["ooda_observe_orient_refs"]["dataset_ref"] == PAPER_DATASET_REF)


def test_downstream_approval_contract() -> None:
    print("\n[4] Downstream approval contract")
    packet = build_paper_strategy_spec_packet(generated_at="2026-05-15T15:00:00Z")
    approval_ref = packet["approval_target_ref"]

    check("approval decision id expected", approval_ref["approval_decision_id_expected"] == PAPER_APPROVAL_DECISION_ID)
    check("approval target type is strategy_spec", approval_ref["target_type"] == "strategy_spec")
    check("approval target id matches", approval_ref["target_id"] == PAPER_STRATEGY_SPEC_ID)
    check("approval target version matches", approval_ref["target_version"] == PAPER_STRATEGY_VERSION)
    check("evaluator ref matches MGMT-PAPER-002 default", packet["paper_evaluator_result"]["ref_id"] == PAPER_EVALUATOR_REF_ID)


def test_evidence_packet_write() -> None:
    print("\n[5] Evidence packet write")
    packet = build_paper_strategy_spec_packet(generated_at="2026-05-15T15:00:00Z")

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        tmp = Path(handle.name)

    try:
        write_evidence_packet(packet, tmp)
        raw = json.loads(tmp.read_text())
        check("task_id field present", raw.get("task_id") == "MGMT-PAPER-001")
        check("environment is paper", raw.get("environment") == "paper")
        check("strategy_spec key present", "strategy_spec" in raw)
        check("registry_entry key present", "registry_entry" in raw)
        check("paper_evaluator_result key present", "paper_evaluator_result" in raw)
        check("paper loop chain present", "paper_loop_chain" in raw)
    finally:
        os.unlink(tmp)


def main() -> int:
    global PASS, FAIL
    print("=== MGMT-PAPER-001: paper StrategySpec unit tests ===\n")

    test_strategy_spec_schema()
    test_candidate_registry_entry()
    test_paper_invariants()
    test_downstream_approval_contract()
    test_evidence_packet_write()

    print(f"\n=== Results: {PASS} PASS, {FAIL} FAIL ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
