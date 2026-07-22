# AG-CAND-TRUTH-001-FE — Stop mixing live candidates with static sample fields

Priority: P0
Repository: `ajoe734/execute-plans`
Merge target: `dev`
Owner: Codex
Reviewer: Codex2
Depends on: `AG-CAND-TRUTH-001-BE`

## Objective

Render Trading Room candidates with honest field-level truth and remove the
mapping that combines a live item with `DEFAULT_CANDIDATES` content while
setting `isSampleData=false`.

## Owned scope

- `src/agora/pages/trading-room/**` candidate/lens presentation
- candidate BFF client/generated types
- focused unit, browser, mobile, and accessibility tests

## Required work

1. Map only fields returned for the same candidate identity.
2. Render unknown/unavailable/stale field states explicitly.
3. Keep a fully isolated demo/sample mode only when the whole card/data set is
   labeled sample; never blend it into live mode.
4. Display safe provenance/freshness and preserve lens filtering.
5. Verify empty/error paths do not silently produce a believable live card.

## Acceptance

- No production live mapping reads rationale/evidence/detail from
  `DEFAULT_CANDIDATES`.
- Mixed live/sample rows are rejected by tests.
- Empty or failed BFF responses display an unavailable/error state in strict
  mode; demo data is visibly and consistently labeled.
- Desktop and 393px mobile hosted proof has no overflow, console error, or
  accessibility violation attributable to this task.
- PR merges to execute-plans `dev` and records the consumed backend contract.

## Exclusions

- No Pantheon backend edits in the frontend repository.
- No unlabeled mock fallback in `VITE_BFF_MODE=live`.
