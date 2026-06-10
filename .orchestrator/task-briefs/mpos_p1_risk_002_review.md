# Review: MPOS-P1-RISK-002 — Add homogeneity and correlation review to allocation gate

Reviewer: Claude
Date: 2026-06-10
Status: APPROVED

## Scope Verified

All six artifact files reviewed:
- `services/optimizer-svc/portfolio_synthesis/conflict_classifier.py`
- `services/capital/risk_policy.py`
- `services/optimizer-svc/test_allocation_conflict_classifier.py`
- `services/optimizer-svc/test_portfolio_synthesis.py`
- `services/capital/test_risk_policy.py`

## Acceptance Criteria Check

1. **First-class homogeneity/correlation taxonomy** ✅  
   `AllocationConflictType.HOMOGENEITY` and `AllocationConflictType.CORRELATION` added as first-class enum values in `conflict_classifier.py`.

2. **Detection: strategy family concentration, target overlap, correlation bucket, numeric correlation** ✅  
   `_classify_homogeneity_correlation_conflicts()` correctly gates on same-family proposals, computes:
   - `strategy_family_concentration` via effective weight ratio
   - `max_target_overlap` per pairwise Jaccard-style metric (excluding cash/stablecoins)
   - `max_signal_correlation` from mapped `signal_correlations` dict or scalar fallback
   - `high_correlation_bucket` from bucket labels

3. **Escalate or reject by RiskPolicy evaluator precedence; risk veto outranks committee** ✅  
   `RiskPolicy` carries `max_strategy_family_concentration`, `max_target_overlap`, `max_signal_correlation` as hard limit fields.  
   The evaluator checks these via `_check_exposure_map` / `_check_scalar_limit` — all produce REJECTED status.  
   `test_risk_veto_precedes_committee_escalation` and `test_risk_policy_correlation_veto_precedes_homogeneity_committee` confirm the precedence chain.

4. **Tests: low correlation → pass, high correlation → committee escalation, hard veto** ✅  
   - `test_low_correlation_duplicate_family_passes_without_committee` (warning only, no committee)
   - `test_high_correlation_bucket_and_target_overlap_require_committee` (committee escalation)
   - `test_homogeneity_and_correlation_limits_are_hard_vetoes` in `test_risk_policy.py` (hard veto)
   - `test_risk_policy_correlation_veto_precedes_homogeneity_committee` (veto before committee)

## Verification

```
python3 -m pytest services/optimizer-svc/test_allocation_conflict_classifier.py \
  services/optimizer-svc/test_portfolio_synthesis.py \
  services/capital/test_risk_policy.py -v
21 passed in 1.79s
```

## Notes

- `abs()` in `_check_scalar_limit` handles negative correlation values correctly.
- Committee trigger for `HOMOGENEITY` conflict is only forwarded when `high_overlap=True`; the `CORRELATION` sibling conflict carries the trigger when only bucket/numeric correlation fires — the overall `committee_triggers` list is still populated correctly in all cases.
- No canonical architecture docs need updating; this is an implementation-level addition within the approved scope.
