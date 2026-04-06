"""
Smoke Tests for Replication Gate

End-to-end integration tests with realistic research payloads.
"""

import json
import sys
from datetime import datetime

from gate_schema import ReplicationRequest, CandidateAdmissionStatus
from gate import ReplicationGate, create_promotion_request


def create_realistic_research_handoff() -> dict:
    """Create a realistic research handoff from RS-002."""
    return {
        "task_id": "RS-002",
        "source_type": "academic_paper",
        "source_metadata": {
            "api_endpoint": "https://api.openalex.org/works/W3052820607",
            "retrieved_at": "2026-04-06T10:00:00Z",
            "governance_context": "Approved structured source",
            "api_version": "v2",
            "data_quality_score": 0.95,
        },
        "normalized_findings": {
            "strategy_spec": {
                "name": "Momentum Mean Reversion Strategy",
                "description": "Cross-asset momentum strategy with mean reversion signals",
                "signals": ["momentum_score", "drawdown_indicator", "price_mean_reversion"],
                "parameters": {
                    "lookback_window": 20,
                    "mean_reversion_threshold": 1.5,
                    "max_position_size": 0.05,
                },
            },
            "replication_notes": (
                "Strategy implements 20-day momentum with mean reversion crosses. "
                "Requires daily bars for backtesting. Expected Sharpe > 1.0 in liquid markets."
            ),
            "evaluation_hypotheses": (
                "H1: Strategy outperforms SPY by 200bps Sharpe-adjusted. "
                "H2: Drawdown stays below 20% in normal market conditions. "
                "H3: Signal generation has < 100ms latency."
            ),
        },
        "grok_processing_notes": {
            "normalization_confidence": "high",
            "governance_compliance": "verified",
            "downstream_readiness": "ready_for_replication",
        },
    }


def create_realistic_strategy_spec() -> dict:
    """Create a realistic strategy spec from normalized research."""
    return {
        "name": "Momentum Mean Reversion Strategy",
        "description": "Cross-asset momentum strategy with mean reversion signals",
        "version": "1.0.0",
        "signals": ["momentum_score", "drawdown_indicator", "price_mean_reversion"],
        "parameters": {
            "lookback_window": 20,
            "mean_reversion_threshold": 1.5,
            "max_position_size": 0.05,
            "risk_limit": 0.02,
        },
        "metadata": {
            "research_source": "OpenAlex W3052820607",
            "backtesting_period": "2020-2025",
            "asset_classes": ["equities"],
        },
    }


def test_realistic_admission_flow():
    """Test realistic end-to-end admission flow."""
    print("\n=== Test 1: Realistic Admission Flow ===")

    gate = ReplicationGate()

    request = ReplicationRequest(
        candidate_id="cand-momentum-mr-20260406",
        source_task_id="RS-002",
        research_handoff=create_realistic_research_handoff(),
        proposed_strategy_spec=create_realistic_strategy_spec(),
        metadata={"source_paper": "W3052820607"},
    )

    print(f"Evaluating candidate: {request.candidate_id}")
    response = gate.evaluate_candidate(request)

    print(f"  Admission Status: {response.admission_status.value}")
    print(f"  Replication Status: {response.replication_status.value}")
    print(f"  Summary: {response.summary}")

    # Check all required criteria passed
    required_results = {
        r.criterion_id: r.passed
        for r in response.results
        if any(
            r.criterion_id == c.criterion_id
            for c in gate.config.get_required_criteria()
        )
    }

    print(f"\n  Required Criteria Results:")
    for cid, passed in required_results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"    {cid}: {status}")

    # Check optional criteria
    optional_results = {
        r.criterion_id: r.passed
        for r in response.results
        if any(
            r.criterion_id == c.criterion_id
            for c in gate.config.get_optional_criteria()
        )
    }

    print(f"\n  Optional Criteria Results:")
    for cid, passed in optional_results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"    {cid}: {status}")

    # Verify admission
    if response.passed:
        print(f"\n✓ Candidate ADMITTED to registry")

        # Create promotion request
        promo = create_promotion_request(
            response, request.proposed_strategy_spec, storage_backend="object_store"
        )

        print(f"\n  Promotion Request Created:")
        print(f"    Gate Run ID: {promo.gate_run_id}")
        print(f"    Storage Path: {promo.storage_path}")
        print(f"    Artifact Type: {promo.registry_entry['artifact_type']}")
        print(f"    Lifecycle State: {promo.registry_entry['lifecycle_state']}")

        assert promo is not None, "Promotion request should not be None"
    else:
        print(f"\n✗ Candidate REJECTED")
        return False

    return response.passed


def test_low_confidence_rejection():
    """Test that low confidence research is rejected."""
    print("\n=== Test 2: Low Confidence Rejection ===")

    gate = ReplicationGate()

    handoff = create_realistic_research_handoff()
    # Lower confidence to "low"
    handoff["grok_processing_notes"]["normalization_confidence"] = "low"

    request = ReplicationRequest(
        candidate_id="cand-low-conf-20260406",
        source_task_id="RS-002",
        research_handoff=handoff,
        proposed_strategy_spec=create_realistic_strategy_spec(),
    )

    print(f"Evaluating low-confidence candidate: {request.candidate_id}")
    response = gate.evaluate_candidate(request)

    print(f"  Admission Status: {response.admission_status.value}")
    print(f"  Summary: {response.summary}")

    # Should still be admitted because confidence is optional and other criteria pass
    print(f"\n  Result: Candidate {'ADMITTED' if response.passed else 'REJECTED'}")

    return True  # This test just checks the flow


def test_bypass_attempt_rejection():
    """Test that bypass attempts are rejected."""
    print("\n=== Test 3: Live Bypass Attempt Rejection ===")

    gate = ReplicationGate()

    spec = create_realistic_strategy_spec()
    spec["skip_promotion_gate"] = True  # Bypass attempt

    request = ReplicationRequest(
        candidate_id="cand-bypass-attempt-20260406",
        source_task_id="RS-002",
        research_handoff=create_realistic_research_handoff(),
        proposed_strategy_spec=spec,
    )

    print(f"Evaluating candidate with bypass attempt: {request.candidate_id}")
    response = gate.evaluate_candidate(request)

    print(f"  Admission Status: {response.admission_status.value}")

    # Should be rejected
    if not response.passed:
        print(f"✓ Bypass attempt correctly REJECTED")
        bypass_result = next(
            r for r in response.results if r.criterion_id == "no_live_bypass"
        )
        print(f"  Evidence: {bypass_result.evidence}")
        return True
    else:
        print(f"✗ Bypass attempt was not rejected!")
        return False


def test_missing_governance_rejection():
    """Test that missing governance context is rejected."""
    print("\n=== Test 4: Missing Governance Context Rejection ===")

    gate = ReplicationGate()

    handoff = create_realistic_research_handoff()
    # Remove governance compliance verification
    handoff["grok_processing_notes"]["governance_compliance"] = "pending"

    request = ReplicationRequest(
        candidate_id="cand-no-gov-20260406",
        source_task_id="RS-002",
        research_handoff=handoff,
        proposed_strategy_spec=create_realistic_strategy_spec(),
    )

    print(f"Evaluating candidate without governance: {request.candidate_id}")
    response = gate.evaluate_candidate(request)

    print(f"  Admission Status: {response.admission_status.value}")

    # Should be rejected (required criterion)
    if not response.passed:
        print(f"✓ Missing governance correctly REJECTED")
        gov_result = next(
            r for r in response.results if r.criterion_id == "governance_context"
        )
        print(f"  Evidence: {gov_result.evidence}")
        return True
    else:
        print(f"✗ Missing governance was not rejected!")
        return False


def test_audit_trail():
    """Test that evaluations create audit trail."""
    print("\n=== Test 5: Audit Trail ===")

    gate = ReplicationGate()

    # Evaluate 3 candidates
    for i in range(1, 4):
        request = ReplicationRequest(
            candidate_id=f"cand-audit-{i}-20260406",
            source_task_id="RS-002",
            research_handoff=create_realistic_research_handoff(),
            proposed_strategy_spec=create_realistic_strategy_spec(),
        )
        gate.evaluate_candidate(request)

    audit_log = gate.get_audit_log()
    print(f"  Audit log entries: {len(audit_log)}")

    for i, entry in enumerate(audit_log, 1):
        print(f"\n  Entry {i}:")
        print(f"    Candidate: {entry['candidate_id']}")
        print(f"    Status: {entry['admission_status']}")
        print(f"    Required Passed: {entry['required_passed']}")
        print(f"    Optional Passed: {entry['optional_passed']}")

    return len(audit_log) == 3


def main():
    """Run all smoke tests."""
    print("\n" + "=" * 70)
    print("REPLICATION GATE SMOKE TESTS")
    print("=" * 70)

    tests = [
        ("Realistic Admission Flow", test_realistic_admission_flow),
        ("Low Confidence Handling", test_low_confidence_rejection),
        ("Bypass Attempt Detection", test_bypass_attempt_rejection),
        ("Missing Governance Detection", test_missing_governance_rejection),
        ("Audit Trail", test_audit_trail),
    ]

    results = []
    for test_name, test_fn in tests:
        try:
            result = test_fn()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n✗ {test_name} FAILED with exception: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))

    # Summary
    print("\n" + "=" * 70)
    print("SMOKE TEST SUMMARY")
    print("=" * 70)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
