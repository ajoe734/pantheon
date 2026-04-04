"""
REG-002 Smoke Test.
Tests the basic promotion flow and metadata validation.
"""
from gate import PromotionGate, PromotionState, PromotionError
import json

def test_promotion_gate():
    gate = PromotionGate()
    
    # 1. Valid Transition: Draft -> Candidate
    draft_entry = {
        "strategy_id": "test-alpha",
        "version": "1.0.0",
        "lifecycle_state": "draft",
        "replication_success": True,
        "lineage": {"source_run_id": "run-123"}
    }
    print("Testing Draft -> Candidate...")
    candidate = gate.promote(draft_entry, PromotionState.CANDIDATE)
    assert candidate["lifecycle_state"] == "candidate"
    print("Success.")

    # 2. Invalid Transition: Candidate -> Live (Should fail due to missing paper state and metadata)
    print("Testing Candidate -> Live (Expected Failure)...")
    try:
        gate.promote(candidate, PromotionState.LIVE)
        assert False, "Should have failed due to invalid transition"
    except PromotionError as e:
        print(f"Caught expected error: {e}")

    # 3. Paper Promotion with Metadata Check
    print("Testing Candidate -> Paper...")
    candidate["evaluation_summary"] = {"risk_review_passed": True, "sharpe_ratio": 2.1}
    paper = gate.promote(candidate, PromotionState.PAPER)
    assert paper["lifecycle_state"] == "paper"
    print("Success.")

    # 4. Live Promotion with Approval
    print("Testing Paper -> Live...")
    paper["rollback_target"] = "0.9.0"
    live = gate.promote(paper, PromotionState.LIVE, approver="human-operator")
    assert live["lifecycle_state"] == "live"
    assert live["approver"] == "human-operator"
    print("Success.")

    # 5. Rollback Target Validation (Self-reference should fail)
    print("Testing Invalid Rollback Target...")
    live["rollback_target"] = "1.0.0" # Same as version
    try:
        gate.check_requirements(PromotionState.LIVE, live)
        assert False, "Should have failed due to self-referencing rollback target"
    except PromotionError as e:
        print(f"Caught expected error: {e}")

if __name__ == "__main__":
    test_promotion_gate()