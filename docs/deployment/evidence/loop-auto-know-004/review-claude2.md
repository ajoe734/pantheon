# Review: LOOP-AUTO-KNOW-004

Reviewer: Claude2
Task: Extract Agora interaction evidence into datasets
Status: APPROVED

## Summary

Implementation is complete and satisfies all three acceptance criteria. The
surface is isolated to BFF evidence extraction only — no runtime authority,
RuntimeBinding, capital binding, or broker calls are touched.

## Acceptance Criteria Verification

### 1. Interaction evidence is routed into Observe or Learn datasets

`route_to_dataset()` in `models.py` correctly maps:
- ask / journal / note / insight → `DatasetKind.OBSERVE`
- feedback / training_example → `DatasetKind.LEARN`
- Unknown future kinds fall back to OBSERVE (safe default, documented).

Verified by `TestRouteToDataset` (7 cases) and route-layer tests covering
all 6 valid interaction kinds via `test_all_valid_interaction_kinds_are_accepted`.

### 2. Dataset extraction is idempotent

`AgoraDatasetStore.add_or_get()` returns the existing record (unchanged)
with `idempotent=True` on duplicate `evidence_id`. The `extracted_at` field
is not overwritten on duplicate calls. Thread-safety is enforced by `threading.RLock`.

Verified by:
- `test_duplicate_evidence_id_is_idempotent`
- `test_duplicate_does_not_overwrite_extracted_at`
- `test_duplicate_evidence_id_returns_201_with_idempotent_true`
- `test_concurrent_writes_are_safe` (50 threads)

### 3. Evidence never promotes artifact or mutates running runtime directly

Governance boundary is enforced at the type system level via Pydantic `Literal`
fields on every `DatasetRecord`:
- `governance_boundary: Literal["observe_or_learn_only"]`
- `no_promote_proof: Literal["agora_observe_learn_only"]`
- `no_runtime_mutation_proof: Literal["agora_evidence_extract_only"]`

These proofs are also surfaced in every API response `meta` block. Code review
confirms zero broker calls, zero RuntimeBinding writes, zero capital operations
in the `dataset_extraction` module.

Verified by:
- `test_record_carries_governance_proof`
- `test_submit_response_carries_governance_proof`
- `test_get_response_has_governance_proof_in_meta`
- `test_list_response_carries_governance_proof_in_meta`

## Test Coverage

- 49 new tests in `dataset_extraction/` (31 unit + 18 route-level)
- 80 total agora BFF tests pass with no regressions

## Quality Notes

- `AgoraInteractionEvidenceRequest` uses `extra="forbid"` — unknown fields rejected.
- Dependency injection via factory parameters enables isolated per-test stores.
- The module-level `_STORE` singleton in `router.py` is appropriate for the
  in-memory use case; tests inject their own store via `dataset_store=`.
- `_error_code()` dual-path import handles test vs production sys.path correctly
  (existing pattern in the codebase).
- The `list_by_dataset` response does not include a `has_more` cursor. This is
  acceptable for the current task scope (page_size enforced at 1–200).

## Decision

**APPROVED** — all acceptance criteria are satisfied, tests are comprehensive,
and the governance boundary is enforced structurally rather than through
convention alone.
