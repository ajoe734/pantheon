# AG-DYNUI-PROD-006 - Hosted E2E Publish Gate

Owner: Codex
Reviewer: Claude2
Depends on: `AG-DYNUI-PROD-001`, `AG-DYNUI-PROD-002`, `AG-DYNUI-PROD-003`, `AG-DYNUI-PROD-004`, `AG-DYNUI-PROD-005`

## Problem

The previous closure treated partial route/BFF recovery as production-level.
The final gate must instead prove the design-pack dynamic UI on the hosted
route end to end.

## Scope

- Write hosted E2E for the Winner Branch flow:
  Strategy Workshop input, reconstruction card, readiness, join Trading Room,
  workspace proposal preview, accept, grid edit, widget revision, before/after,
  keep original and add modified copy, version history, and rollback.
- Capture desktop and mobile screenshots.
- Confirm no direct order, capital binding, broker, RuntimeBinding, or
  Management leakage.
- Confirm CI, deploy, and live probes pass after merge.

## Acceptance

- E2E passes against the hosted dev FE and live BFF.
- Screenshot artifacts match the design-pack layout and do not show the old
  empty Trading Desk shell.
- Publish checklist includes PR numbers, merge commits, deploy run IDs, and
  live probe artifacts.
- The task is not closed until the PRs are merged and hosted validation passes.
