# PPL-ALLOC-006 BFF / Frontend Handoff Follow-Up 7

Task: `PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-7`
Parent: `PPL-ALLOC-006`
Owner: Codex2
Reviewer: Claude
Kind: support-only `bff_handoff_packet`
Generated: 2026-07-11

## Boundary

This packet is a merge-readiness ledger for the parent workbench. It turns the
existing query-gap, operator-journey, adapter, and test guidance into explicit
absorption gates. It changes no canonical truth, route, schema, policy,
runtime/registry/governance implementation, or `execute-plans` source. Only
the parent and dependency owners can supply the implementation evidence named
below.

## Merge-Readiness Ledger

| Parent slice | Evidence the parent can absorb now | Evidence still required | Fail-closed behavior until supplied |
|---|---|---|---|
| Ranking spine | Resource-specific ranking decoder for `data.items`; stable persona identity; independent surface health | Parent adapter/component test showing rows survive recommendation, review, or fleet degradation | Keep the ranking row visible; mark only the affected enrichment/action unavailable |
| Recommendation submission | Server `recommendation_id`; intent-scoped idempotency key; returned review, inbox, command, and link identifiers | Replay test proving first accept and replay restore one review link | Never manufacture a review id or label the recommendation approved |
| Review state | Explicit returned `review_id` and server review/decision read or receipt | Parent mapping for review collection/detail and its degraded state | Do not select the newest review or join by display name |
| Binding display | Ledger-first paper identity and stable persona join | Adopted `PPL-ALLOC-003` read evidence for canary/live pool or sleeve, `current_weight`, and binding health | Render binding/weight unknown; never map a legacy paper pool to real capital |
| Allocation preview | Evaluation `data.lines`, snapshot identity, and `applied: false` | Complete adopted line identity plus caps, exclusions, evidence, simulation, constraints, and rollback target | Allow inspection only; disable proposal creation and name every missing input |
| Proposal creation | Dedicated rebalance decoder; intent idempotency; returned durable `rebalance_id` | Parent test proving the complete preview survives preview-to-create unchanged | Never submit a partial proposal; never treat a dry-run id as durable |
| Proposal review/apply | Successful current detail read with simulation, constraints, rollback, state, and approval data | Adopted `PPL-ALLOC-004` approval binding and error semantics, plus role/confirmation capability | List-only, stale, mismatched, or incomplete detail cannot enable apply |
| Apply completion | Apply receipt produces `apply submitted` and retains command/audit links | Named authoritative `PPL-ALLOC-003`/`004` readback proving the new allocation or binding | Preserve the old `current_weight`; never advance on toast, elapsed time, or proposal-status guess |
| Emergency containment | Risk-decreasing vocabulary, reason/evidence requirement, and no promote/increase rule | Installed governed action helper and `PPL-ALLOC-008` authorization/negative-test evidence | Show the capability unavailable; provide no direct REST fallback |

`Evidence still required` is not optional polish. If the parent cannot cite it
in its PR or test output, the corresponding write capability remains disabled.

## Operator Journey Proof Chain

The parent should preserve this evidence chain as separate records and labels:

```text
ranking/recommendation
  -> recommendation submit receipt + explicit review_id
  -> review decision receipt
  -> allocation preview (applied=false)
  -> durable proposal receipt + rebalance_id
  -> current proposal detail + bound approval
  -> apply command receipt (apply submitted)
  -> authoritative allocation/binding readback (applied confirmed)
```

No arrow may be skipped by client inference. A failed refresh retains the last
receipt and changes only the affected query-health state. A `404` retains the
originating identifier for recovery; `409` remains an unmet precondition; and
`422` remains incomplete or unsafe input.

## Parent PR Checklist

The parent implementation PR is ready for review only when it records:

- the adapter/query-key owner for ranking, recommendations, reviews, fleet,
  evaluation, rebalance list, rebalance detail, and mutation receipts;
- the stable identifiers used by every join, with unavailable joins visibly
  disabled rather than replaced by display-name, array-position, newest-row,
  or matching-weight heuristics;
- per-surface loading, stale, degraded, and error behavior;
- distinct labels and assertions for `recommended`, `review submitted`,
  `approved/rejected`, `target calculated`, `proposal created`, `apply
  submitted`, and `applied confirmed`;
- idempotent replay coverage that retains one operator intent and one durable
  server resource;
- incomplete-preview, list-ready/detail-error, live-increase `409`, and
  accepted-apply/readback-pending tests;
- the exact authoritative readback used for `applied confirmed`, or an
  explicit statement that the UI stops at `apply submitted`;
- the installed emergency governed-action helper, or a test proving that no
  fallback mutation is exposed while it is absent.

Hosted smoke and actual BFF/frontend delivery remain parent/closeout work;
this support artifact claims neither.

## Reviewer Decision Guide

Claude can approve absorption when every enabled capability cites server
evidence and every unresolved dependency produces a local, visible,
fail-closed state. Request changes if the parent:

- recomputes policy fields or defaults unknown weights/eligibility to zero or
  false;
- joins reviews, proposals, or bindings without explicit stable identifiers;
- exposes apply from rebalance list data or stale detail;
- treats recommendation approval, proposal creation, or command acceptance as
  applied capital;
- erases receipts during partial refresh failure; or
- invents an emergency mutation path when the governed helper is unavailable.

## Review And Absorption

Owned here: support-only readiness ledger, proof chain, PR checklist, and
review decision guide.
Not changing: L1/L2 truth, BFF contracts or implementation,
runtime/registry/governance behavior, frontend source/navigation, dependency
task ownership, or parent lifecycle.
Composes with: `PPL-ALLOC-003` binding reads, `PPL-ALLOC-004` allocation and
approval semantics, `PPL-ALLOC-008` emergency containment, parent
`PPL-ALLOC-006`, and the preceding PPL-ALLOC-006 handoff packets.

The parent owner decides which ready consumer rules to absorb and must keep
the unresolved ledger entries unavailable until their owning tasks provide
evidence.

## Finalization Checkpoint

Reviewer approval remains valid after closeout re-verification. The ledger and
its Claude review record are merged into `dev`; `PPL-ALLOC-003` and
`PPL-ALLOC-004` have since reached `done`, so the parent may now cite their
evidence when absorbing the corresponding gates. `PPL-ALLOC-008` remains
blocked, and emergency containment therefore remains unavailable as specified
above. This dependency progress does not change this packet's support-only
boundary or authorize any frontend, BFF, runtime, registry, governance, or
canonical-truth change.

Closeout verification confirmed both support artifacts are non-empty, commits
`3d88b399d` and `0b84e7ee8` are ancestors of `origin/dev`, and each commit
changes exactly one support Markdown file.

## Sources Reviewed

- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md`
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md`
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md`
- `support/sidecars/PPL-ALLOC-003/PPL-ALLOC-003-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/PPL-ALLOC-004/PPL-ALLOC-004-SIDECAR-BFF-HANDOFF.md`
