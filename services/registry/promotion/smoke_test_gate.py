"""
REG-002 smoke test.
Validates governed promotions and EX-001 execution projection generation.
"""
from gate import PromotionError, PromotionGate, PromotionState


def test_promotion_gate() -> None:
    gate = PromotionGate()

    draft_entry = {
        "registry_id": "reg-test-alpha-1.0.0",
        "artifact_type": "model_artifact",
        "strategy_id": "test-alpha",
        "version": "1.0.0",
        "checksum": "sha256:abc123def4567890",
        "lifecycle_state": "draft",
        "replication_success": True,
        "lineage": {"source_run_id": "run-123"},
        "storage_ref": {
            "backend": "object_store",
            "path": "object://pantheon/registry/test-alpha/1.0.0/artifact.bin",
        },
    }
    print("Testing Draft -> Candidate...")
    candidate = gate.promote(draft_entry, PromotionState.CANDIDATE)
    assert candidate["lifecycle_state"] == "candidate"
    print("Success.")

    print("Testing Candidate -> Live (Expected Failure)...")
    try:
        gate.promote(candidate, PromotionState.LIVE)
        assert False, "Should have failed due to invalid transition"
    except PromotionError as exc:
        print(f"Caught expected error: {exc}")

    print("Testing Candidate -> Paper...")
    candidate["evaluation_summary"] = {"risk_review_passed": True, "sharpe_ratio": 2.1}
    paper = gate.promote(candidate, PromotionState.PAPER)
    assert paper["lifecycle_state"] == "paper"
    print("Success.")

    print("Testing Paper -> Canary...")
    paper["approver"] = "risk-committee"
    paper["metadata"] = {
        "rollback": {
            "target_registry_id": "reg-test-alpha-0.9.0",
            "target_version": "0.9.0",
        }
    }
    canary = gate.promote(paper, PromotionState.CANARY, approver="risk-committee")
    assert canary["lifecycle_state"] == "canary"
    print("Success.")

    print("Testing Paper -> Live (Expected Failure - must go through canary)...")
    try:
        gate.promote(paper, PromotionState.LIVE, approver="human-operator")
        assert False, "Should have failed: paper -> live skip is forbidden"
    except PromotionError as exc:
        print(f"Caught expected error: {exc}")

    print("Testing Canary -> Live...")
    live = gate.promote(canary, PromotionState.LIVE, approver="human-operator")
    assert live["lifecycle_state"] == "live"
    assert live["approver"] == "human-operator"
    print("Success.")

    print("Testing Execution Projection...")
    projection = gate.build_execution_projection(live)
    assert projection.metadata_key.endswith("/metadata.json")
    assert projection.artifact_key.endswith("/artifact.bin")
    assert projection.metadata["promotion_state"] == "live"
    print("Success.")

    print("Testing Invalid Rollback Target...")
    broken_live = dict(live)
    broken_live["metadata"] = {
        "rollback": {
            "target_registry_id": live["registry_id"],
            "target_version": live["version"],
        }
    }
    try:
        gate.build_execution_projection(broken_live)
        assert False, "Should have failed due to self-referencing rollback target"
    except PromotionError as exc:
        print(f"Caught expected error: {exc}")


if __name__ == "__main__":
    test_promotion_gate()
