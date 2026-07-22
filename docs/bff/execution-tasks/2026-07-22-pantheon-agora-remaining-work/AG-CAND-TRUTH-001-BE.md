# AG-CAND-TRUTH-001-BE — Complete candidate evidence and provenance projection

Priority: P0
Repository: `ajoe734/pantheon`
Merge target: `dev`
Owner: Claude
Reviewer: Codex2
Depends on: `OPS-DISPATCH-LEASE-SYNC-001`

## Objective

Return a candidate DTO whose title, score, rationale, concerns, next event,
evidence, and detail fields are all either linked to the same real candidate or
explicitly unavailable.

## Owned scope

- Agora research/candidate projection and its BFF route
- additive candidate schema/OpenAPI changes
- focused provenance, privacy, tenant, pagination, and freshness tests

## Required work

1. Trace the durable candidate/research records that can legitimately supply
   every field currently rendered by Trading Room.
2. Add per-field or grouped provenance, source timestamp, freshness, and
   availability. Do not copy a different candidate's sample text.
3. Preserve private-content/no-list boundaries and redact evidence summaries
   by role.
4. Define stable null/unavailable behavior for incomplete candidates.
5. Keep score semantics explicit; a Sharpe-derived score must identify that
   transformation and must not be presented as a generic confidence score.

## Acceptance

- Every non-null rendered field is traceable to the requested candidate ID.
- Incomplete rows return typed missing fields rather than static defaults.
- Cross-tenant and viewer redaction tests pass.
- Pagination/order is stable and freshness is observable.
- OpenAPI, projection, and BFF contract tests pass and the PR merges to `dev`.

## Exclusions

- No frontend implementation.
- No generated narrative or next event unless a governed source artifact owns
  it.
- No raw private content in list responses.
