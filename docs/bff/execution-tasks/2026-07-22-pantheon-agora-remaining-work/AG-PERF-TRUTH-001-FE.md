# AG-PERF-TRUTH-001-FE — Remove simulated Strategy Performance product data

Priority: P0
Repository: `ajoe734/execute-plans`
Merge target: `dev`
Owner: Antigravity
Reviewer: Codex
Depends on: `AG-PERF-TRUTH-001-BE`

## Objective

Replace `getSimulatedDetails()` and local-only suggestion actions with the
governed BFF contracts delivered by the backend task.

## Owned scope

- `src/agora/pages/strategy-performance/**`
- Agora BFF client/generated types used by this page
- focused unit, browser, responsive, and accessibility tests

## Required work

1. Delete production use of hard-coded compliance values, securities, dates,
   P&L, warning prose, and adjustment suggestions.
2. Render loading, unavailable, stale, empty, partial, error, and ready states
   from the BFF envelope. Unknown fields remain unknown.
3. Apply/reject/return buttons call the BFF, use the returned receipt/readback,
   and never show success on request, authz, conflict, or persistence failure.
4. Show provenance/as-of information without exposing sensitive evidence.
5. Preserve keyboard, narrow/mobile, i18n, and role-aware behavior.

## Acceptance

- `getSimulatedDetails()` and equivalent product-path hard-coded details are
  absent.
- A strict test fails if the BFF is unavailable; there is no silent mock
  fallback in live mode.
- A successful action displays its receipt/reference and survives reload.
- A failed action leaves authoritative state unchanged and communicates the
  typed failure.
- Desktop/mobile hosted proof is pinned to an accepted FE/BFF pair after merge.

## Exclusions

- No Pantheon backend edits in the execute-plans repository.
- No optimistic success toast before authoritative receipt.
- No enabling real/live writes in the default frontend profile.

## Closeout record

- [Owner finalization and accepted delivery evidence](../../../04/pantheon_agora_remaining_work_2026-07-22/archive/AG-PERF-TRUTH-001-FE-closeout-2026-07-22.md)
