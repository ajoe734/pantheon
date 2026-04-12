"""
Smoke test for CAP-002 portfolio synthesis.

Run:
    python3 services/optimizer-svc/smoke_test_portfolio_synthesis.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from portfolio_synthesis import (  # noqa: E402
    CommitteeReferral,
    PoolRiskPolicy,
    PortfolioSynthesizer,
    SynthesisError,
    PersonaAllocationProposal,
)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def check(label: str, condition: bool) -> bool:
    status = PASS if condition else FAIL
    print(f"  [{status}] {label}")
    return condition


def make_proposal(**overrides) -> PersonaAllocationProposal:
    defaults = {
        "proposal_id": "proposal-001",
        "persona_id": "persona-alpha",
        "capital_pool_id": "pool-001",
        "scope_ref": "live",
        "directions": ["long"],
        "target_weights": {"AAPL": 0.6, "MSFT": 0.4},
        "conviction": 0.8,
        "uncertainty": 0.0,
        "reliability_score": 1.0,
        "regime_fit_score": 1.0,
        "governance_multiplier": 1.0,
        "metadata": {},
    }
    defaults.update(overrides)
    return PersonaAllocationProposal(**defaults)


def test_weighted_fusion() -> bool:
    print("\n[1] Weighted fusion produces one canonical artifact")
    ok = True
    synthesizer = PortfolioSynthesizer()
    artifact, log = synthesizer.synthesize_with_log(
        [
            make_proposal(),
            make_proposal(
                proposal_id="proposal-002",
                persona_id="persona-beta",
                target_weights={"AAPL": 0.1, "GOOG": 0.9},
                conviction=0.2,
            ),
        ],
        capital_pool_id="pool-001",
        scope_ref="live",
    )
    ok &= check("single artifact produced", artifact.capital_pool_id == "pool-001")
    ok &= check("sponsor selected from highest effective weight", artifact.sponsor_persona_id == "persona-alpha")
    ok &= check("AAPL fused weight is 0.5", math.isclose(artifact.target_weights["AAPL"], 0.5, abs_tol=1e-9))
    ok &= check("conflict log linked to artifact", artifact.conflict_resolution_log_id == log.log_id)
    return ok


def test_committee_referral() -> bool:
    print("\n[2] Long/short high-conviction conflict escalates to committee")
    ok = True
    synthesizer = PortfolioSynthesizer()
    referral, log = synthesizer.synthesize_with_log(
        [
            make_proposal(conviction=0.9, directions=["long"]),
            make_proposal(
                proposal_id="proposal-002",
                persona_id="persona-beta",
                conviction=0.95,
                directions=["short"],
            ),
        ],
        capital_pool_id="pool-001",
        scope_ref="live",
    )
    ok &= check("committee referral returned", isinstance(referral, CommitteeReferral))
    ok &= check("committee log reference recorded", log.committee_ref == referral.referral_id)
    return ok


def test_all_vetoed() -> bool:
    print("\n[3] All hard-vetoed proposals still emit a conflict log")
    ok = True
    synthesizer = PortfolioSynthesizer(
        pool_risk_policy=PoolRiskPolicy(max_single_weight=0.5),
    )
    try:
        synthesizer.synthesize(
            [
                make_proposal(target_weights={"AAPL": 0.9}),
                make_proposal(
                    proposal_id="proposal-002",
                    persona_id="persona-beta",
                    target_weights={"MSFT": 0.8},
                ),
            ],
            capital_pool_id="pool-001",
            scope_ref="live",
        )
        ok &= check("all-vetoed path should raise", False)
    except SynthesisError:
        log = synthesizer.last_conflict_resolution_log
        ok &= check("log retained after exception", log is not None)
        ok &= check(
            "rejected_reason records all vetoed",
            log is not None and log.rejected_reason == "all_proposals_vetoed",
        )
    return ok


def main() -> int:
    checks = [
        test_weighted_fusion(),
        test_committee_referral(),
        test_all_vetoed(),
    ]
    passed = sum(1 for item in checks if item)
    total = len(checks)
    print(f"\nSUMMARY: {passed}/{total} smoke groups passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
