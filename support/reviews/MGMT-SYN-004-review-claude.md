# Review: MGMT-SYN-004 — allocation synthesis method v1

**Reviewer:** Claude
**Owner:** Codex
**Review date:** 2026-05-15
**Verdict:** APPROVED

## Scope

Task-owned files reviewed:
- `services/optimizer-svc/allocation_aggregation/synthesis.py` (new)
- `services/optimizer-svc/allocation_aggregation/__init__.py` (modified — added synthesis exports)
- `services/optimizer-svc/test_allocation_synthesis_method.py` (new)

Unrelated dirty files in worktree were excluded from review scope per the task brief.

## Verification Commands

All run by reviewer (Claude):

```
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile \
  services/optimizer-svc/allocation_aggregation/synthesis.py \
  services/optimizer-svc/test_allocation_synthesis_method.py
# => OK

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  services/optimizer-svc/test_allocation_synthesis_method.py \
  services/optimizer-svc/test_portfolio_synthesis.py \
  services/optimizer-svc/test_allocation_conflict_classifier.py \
  services/optimizer-svc/test_persona_allocation_proposal_store.py -q
# => 21 passed
```

## Key Invariants Checked

### Facade contract
- `synthesis.py` is a narrow facade over `portfolio_synthesis.PortfolioSynthesizer` — arbitration engine stays untouched ✓
- `synthesize_allocation` returns `AllocationPolicyArtifact` (convenience form) ✓
- `synthesize_allocation_with_log` returns `AllocationSynthesisResult` (artifact + log in-process) ✓

### Method normalization
- `risk_first` and `weighted_committee` both alias to `SynthesisMethod.WEIGHTED_FUSION` ✓
- `single_proposal` is passed through directly ✓
- Manual/committee_override methods always raise `SynthesisError` with clear v1-scope message ✓
- Unsupported methods raise `SynthesisError` listing valid options ✓

### Committee referral rejection
- When `PortfolioSynthesizer` returns a `CommitteeReferral` instead of an artifact, `SynthesisError` is raised ✓
- v1 correctly rejects the unresolved case at the facade boundary ✓

### Store-backed proposal replay
- `PersonaAllocationProposalJsonlStore.require_proposals` is used to load proposals by id ✓
- `scope_ref` is inferred from proposals when not supplied; multi-scope inputs raise `SynthesisError` ✓

### Constraints propagation
- `risk_policy_ref`, `requested_synthesis_method`, `resolved_synthesis_method` are injected into `constraints_bundle` and appear on the artifact ✓
- `committee_override_ref` is included when provided ✓

### Safety
- No broker session opened, no capital mutation, no live order route ✓
- Implementation is purely in-memory synthesis over an append-only proposal store ✓

### Export surface
- `allocation_aggregation.__init__.py` exports `synthesize_allocation`, `synthesize_allocation_with_log`, `AllocationSynthesisResult`, `SUPPORTED_SYNTHESIS_METHODS` ✓
- `synthesis.py` `__all__` is consistent with module-level names ✓

## Minor Observation (non-blocking)

`SUPPORTED_SYNTHESIS_METHODS` includes `manual_override` and `committee_override` even though both always raise `SynthesisError` in v1. This could mislead callers enumerating valid options. Consider renaming to `KNOWN_SYNTHESIS_METHODS` in a follow-up, or filtering out unsupported methods from the tuple. Not a blocker for v1 acceptance since error messages are clear.

## Result

21/21 tests pass. Implementation is correctly scoped as a v1 risk-first + single-proposal facade. No live side effects. Approved for owner finalization.
