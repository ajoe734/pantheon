# CAP-002 Review — Qwen

**Reviewer:** Qwen (auto-reassigned from Copilot)
**Owner:** Codex
**Date:** 2026-04-10
**Status:** ✅ APPROVED

---

## Scope

Review of CAP-002: multi-persona synthesis module inside `optimizer-svc`, implementing the arbitration pipeline defined in `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md`.

## Artifacts Reviewed

- `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md` — L1 policy document
- `services/optimizer-svc/portfolio_synthesis/models.py`
- `services/optimizer-svc/portfolio_synthesis/synthesizer.py`
- `services/optimizer-svc/portfolio_synthesis/__init__.py`
- `services/optimizer-svc/test_portfolio_synthesis.py` (7 unit tests)
- `services/optimizer-svc/smoke_test_portfolio_synthesis.py` (3 smoke groups)

## Acceptance Criteria Verification

| Criterion | Status | Notes |
|---|---|---|
| Weighted fusion implemented in optimizer-svc | ✅ | `PortfolioSynthesizer.synthesize()` computes `effective_weight` per §6.2 formula, normalizes, and produces fused `target_weights` via weighted average. |
| One canonical synthesis artifact per scope | ✅ | Returns single `AllocationPolicyArtifact` with `capital_pool_id` + `scope_ref` as uniqueness key. |
| `conflict_resolution_log` generated | ✅ | `ConflictResolutionLog` produced on every path: success, committee escalation, and all-vetoed (even when `SynthesisError` is raised). Accessible via `synthesize_with_log()` tuple and `last_conflict_resolution_log` property. |

## Policy Alignment Checks

### §6.1 Hard Veto
- `PoolRiskPolicy.veto_reason()` checks `forbidden_asset_classes` via `metadata["asset_classes"]`, `max_single_weight` against `target_weights`, `forbidden_strategy_families` via `metadata["strategy_family"]`, and supports `custom_veto_fn`. ✅

### §6.2 Weighted Fusion
- Formula matches canonical: `reliability_score * regime_fit_score * conviction * (1 - uncertainty) * governance_multiplier`. ✅
- Zero-weight fallback to equal shares handled. ✅
- Fused weights renormalized if sum > 1.0. ✅

### §6.3 Committee Override
- long vs short high-conviction conflict (threshold 0.7). ✅
- Risk posture conflict (`wants_leverage` vs `wants_de_risk`). ✅
- High-risk first deployment in canary/live. ✅
- Sponsor ambiguity (top two within 5%). ✅
- High-importance pool flag. ✅

### §6.4 Sponsor Rule
- Sponsor selected as persona with highest normalised fusion share. ✅

### §9 Priority Order
- Pipeline order: validate → veto → committee check → fusion → sponsor → artifact. ✅
- `pool_risk_policy > governance > committee > aggregation > persona` respected. ✅

### §10 conflict_resolution_log
- All required fields present: `proposal_ids`, `vetoed_proposals`, `weighting_inputs`, `weighting_outputs`, `committee_ref`, `sponsor_persona_id`, `rejected_reason`, `timestamp`. ✅

### §7 Single Pool Single Runtime
- No parallel artifact loading — single artifact returned per call. ✅

## Code Quality

- **Dataclass design:** `frozen=True` throughout — immutability is correct for governance objects.
- **Validation:** `__post_init__` enforces range constraints on all score fields and target_weights sum ≤ 1.0.
- **Enum usage:** `SynthesisMethod` and `VetoReason` are proper StrEnums — serializable and typed.
- **Separation of concerns:** `PoolRiskPolicy` is injectable, `committee_escalation_fn` is optional callback — testable and extensible.
- **Error handling:** `SynthesisError` extends `ValueError` — clear sentinel for "cannot synthesize" path.

## Tests

- `py_compile`: PASS
- `unittest`: 7/7 PASS
  - weighted fusion produces correct artifact and log
  - single surviving proposal with vetoed inputs
  - all-vetoed raises and retains log
  - committee escalation returns referral and log
  - high-risk first canary deployment escalates
  - zero effective weight falls back to equal shares
  - pool/scope mismatch rejected before synthesis
- `smoke_test`: 3/3 PASS

## Minor Notes (non-blocking for v1)

1. `committee_ref` field in `ConflictResolutionLog` is typed as `Optional[str]` (the referral_id). The canonical spec §10 says `committee_ref (if any)` — this is sufficient for v1. Future versions could embed the full `CommitteeReferral` object if needed.
2. `_needs_committee_escalation` uses hardcoded `high_conviction_threshold = 0.7`. This is fine for v1; should be configurable via `PoolRiskPolicy` or constructor param in follow-up.
3. The `metadata` dict on `PersonaAllocationProposal` carries `asset_classes`, `strategy_family`, `wants_leverage`, `wants_de_risk`, `strategy_family_risk`, `first_deployment_in_scope`. These are not schema-validated at the model level — upstream proposal normalization is responsible. Acceptable for v1.

## Conclusion

All three acceptance criteria are met. The implementation faithfully follows the canonical L1 policy document. The code is clean, well-structured, and thoroughly tested. **Approved for v1 lock.**
