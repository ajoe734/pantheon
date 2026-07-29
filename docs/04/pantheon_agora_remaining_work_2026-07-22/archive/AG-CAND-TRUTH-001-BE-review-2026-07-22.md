# AG-CAND-TRUTH-001-BE — Reviewer Approval Record

- Task: AG-CAND-TRUTH-001-BE — Complete Agora candidate provenance projection
- Owner: Codex
- Reviewer: Claude (chair-reassigned from Codex2 after two consecutive reviewer
  run failures: codex-20260722T183332Z-7494e3c0, codex-20260722T194834Z-0dbcbc64)
- Review date: 2026-07-22
- Branch: `task/AG-CAND-TRUTH-001-BE`
- Reviewed HEAD: `7c0beb1df4935b13f541ba1fc26f8cc5c8e754fa`
- Decision: **APPROVED** — return to owner Codex for closeout (PR → `dev` merge → `done`)

## Scope reviewed (vs `origin/dev`)

```
scripts/test_agora_v1_12_candidate_bundle.py                          (new)
services/control-plane/bff/agora/research/router.py                   (modified)
services/control-plane/bff/tests/test_agora_candidate_truth.py        (new)
services/control-plane/openapi/agora_v1_12.openapi.yaml               (new)
services/control-plane/specs/agora/bundle_index.v1_12.json            (new)
services/control-plane/specs/agora/v13/candidate_member_truth_projection.schema.json (new)
services/control-plane/specs/agora/v13/capability_manifest_v1_12.json (new)
.orchestrator/task-briefs/ag_cand_truth_001_be.md                     (task brief)
```

`router.py` is the only modified pre-existing file; all contract artifacts are
additive new files. Published v1.10/v1.11 bundle bytes are untouched, and the
v1.12 bundle test locks `extends` → exact v1.11 `bundle_index` sha256.

## Acceptance criteria verification

1. **Every non-null rendered field traceable to the requested candidate ID** —
   `_member_truth_projection` builds per-field states (`details`, `rationale`,
   `concerns`, `next_event`, `evidence`) exclusively from the candidate's own
   pool-member record, score result, review, or monitoring record; each
   available field carries `provenance.source_type/source_ref/as_of` embedding
   the candidate `artifact_id`. Test
   `test_every_available_field_traces_to_the_requested_candidate` asserts
   `artifact_id in source_ref` per field and that two candidates' rationale
   values differ (no cross-candidate sample text).
2. **Typed missing fields, not static defaults** — `_unavailable_field` with
   typed reasons (`score_not_run`, `no_governed_source`, `not_recorded`);
   covered by `test_unscored_pool_returns_typed_unavailable_fields_not_static_defaults`
   and `test_next_event_requires_a_governed_monitoring_source`. The BFF never
   invents a next event without an active/paused governed monitoring record.
3. **Cross-tenant and viewer redaction** —
   `test_cross_tenant_and_cross_user_reads_are_denied` (403 for both);
   `test_evidence_summaries_are_redacted_by_role_and_in_lists` proves list
   responses always redact evidence summaries and score-component explanations
   (`redaction_reason=list_response`), viewer-only detail reads stay redacted
   (`redaction_reason=viewer_role`), and operator-grade detail retains the
   governed explanations. `_candidate_public_member` strips all `_`-prefixed
   private keys from every response path, including the review-response
   candidate echo and `_public_candidate_pool`.
4. **Stable pagination and observable freshness** — deterministic
   `(created_at, artifact_id)` ordering, opaque `cpm-offset-` page tokens with
   422 on malformed tokens, honest filtered `total`, and
   `meta.freshness{pool_snapshot_at,data_cutoff,last_score_run_at}`. Covered by
   `test_member_pagination_is_stable_and_freshness_observable`.
5. **Explicit score semantics** — `score_semantics.effective_score` is
   identified as `recipe_weighted_score` (recipe id/version, 0–100 scale) and
   `sharpe_summary` as `sharpe_ratio` with
   `transformation=sharpe_ratio_from_producing_research_run`; both hard-code
   `is_confidence_score: false`, schema-enforced (const) in
   `candidate_member_truth_projection.schema.json`. Covered by
   `test_sharpe_derived_score_semantics_are_explicit` plus bundle
   schema-negative tests.

Prior Codex2 review-fix items are addressed in the branch: recipe evidence
requirements no longer synthesize `evidence://` refs
(`test_generated_recipe_requirements_are_not_durable_evidence_refs`), lifecycle
review writes persist `_updated_at` and both list/detail project it as
`details.provenance.as_of`
(`test_details_provenance_uses_persisted_member_mutation_timestamp`), and the
score list route strips private component explanations.

## Exclusions respected

- No frontend changes; no generated narrative; no raw private content in list
  responses; `no_order_route_proof` retained on candidate envelopes.

## Reviewer-run verification (2026-07-22, this worktree at HEAD 7c0beb1df)

```
/home/lupin/pantheon/.venv/bin/python -m pytest -q \
  services/control-plane/bff/tests/test_agora_candidate_truth.py \
  services/control-plane/bff/tests/test_agora_candidate_pool.py \
  scripts/test_agora_v1_12_candidate_bundle.py
# → 21 passed

/home/lupin/pantheon/.venv/bin/python -m pytest -q \
  services/control-plane/bff/tests/test_agora_research_store_backend.py \
  services/control-plane/bff/tests/test_agora_router.py \
  services/control-plane/bff/tests/test_agora_research_run_projection.py
# → 26 passed, 2 skipped (AGORA_RESEARCH_TEST_POSTGRES_DSN unset)
```

## Non-blocking observations (follow-up candidates, not required for closeout)

- `_score_source_ref` indexes `score["scored_at"]` directly; safe today because
  every persisted score result records `scored_at`, but a defensive `.get`
  would remove the latent KeyError if the store contract ever loosens.
- Detail responses still return raw `reviews`/`monitoring` records to any
  caller who passes `_require_pool_access` (pre-existing behavior, in-tenant
  and same-user gated); if review rationale is later classified as
  operator-private, extend the role redaction to those records.
- Offset-based page tokens are stable for snapshot-shaped pools but are not
  cursor-safe under concurrent member mutation; acceptable for the current
  pool-snapshot model.

## Outcome

All five acceptance bullets verified against code, contract artifacts, and
passing tests. Approving and returning to owner Codex for finalization:
per-task PR to `dev`, merge, then `done` with delivery metadata.
