# TJ-E2E-004 - Journey Materializer And Reverse Index

Owner: Antigravity  
Reviewer: Claude  
Wave: 1  
Repository: `ajoe734/pantheon`  
Dependencies: `TJ-E2E-002`, `TJ-E2E-003`

## Goal

Build the deterministic read-side materializer for snapshot, timeline, graph,
identifier reverse lookup, completeness and data-quality diagnostics.

## Required work and acceptance

- Handle duplicate, out-of-order, late and correction events idempotently.
- Index all approved identifiers with tenant/environment/RBAC scope.
- Detect orphan IDs, missing stages and conflicting terminal states.
- Support deterministic rebuild without writing to execution sources of truth.
- Pass replace-chain, partial-fill, cancel, mismatch and failure-injection tests.

## Owner implementation evidence (2026-07-12)

Implemented in `services/trade_journey/materializer.py` as a source-agnostic
read-side component. It never invokes or writes an execution producer. Its
projection is rebuilt solely from immutable versioned input events.

- deterministic ordering: `occurred_at`, producer `sequence`, then `event_id`;
- idempotent duplicate handling with fail-closed conflicting-payload detection;
- late/out-of-order full projection recalculation and per-stage revisions;
- tenant + environment + identifier-type reverse keys, with caller-provided
  journey authorization filtering and ambiguity-preserving result lists;
- snapshot, timeline, replace/lineage graph, source watermarks and rebuild state;
- missing-stage, orphan-identifier, identifier-conflict and mutually-exclusive
  terminal-state diagnostics;
- roll-ups for risk/broker reject, partial fill, cancel, reconciliation variance
  and explicit correction.

Focused verification:

```text
python3 -m pytest -q services/trade_journey/test_materializer.py
8 passed
python3 -m py_compile services/trade_journey/materializer.py
git diff --check
```

Independent round-2 review also re-derived the mixed whole/fractional-second
ordering failure, verified the fixed-width UTC normalization and regression
coverage, and approved the implementation for owner closeout. See
`docs/reviews/2026-07-12-tj-e2e-004-claude-review.md`.

The downstream `TJ-E2E-005` BFF should expose this read model and apply its
authoritative RBAC policy before passing `allowed_journey_ids` to `resolve`.
