# Task Brief: PPL-ALLOC-007

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Binding visibility and route prune
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: execute-plans PR #285 round-4 approved and merged to dev at c62c0e8b9a49643c42f67614c542578afb233e84. Round-3 stale-href regression in e2e/25-persona-fleet-live-linked-pages.spec.ts fixed correctly (asserts new /management/performance?tab=overview destination). Gate run 29222175376 passed. Owner Codex to run closeout done.

## Summary
修 Persona Fleet / Capital 顯示不同 persona 綁定到不同 paper ledger 或 real sleeve；legacy/diagnostic 頁面不再搶主流程。

## Closeout evidence

- Delivery repository: `ajoe734/execute-plans`
- Merged PR: `#285`
- Merge target: `dev`
- Merge commit: `c62c0e8b9a49643c42f67614c542578afb233e84`
- Reviewer: Claude (round 4 approved)
- Required gate: `Pantheon FE-BFF Integration Gate` run `29222175376` passed
- Owner readback confirmed:
  - Persona Fleet distinguishes `Paper ledger` from `Real sleeve` and displays the binding id.
  - Capital links preserve persona and binding context under `/management/performance?tab=overview`.
  - `/management/promotion-allocation` no longer renders the legacy page and resolves through canonical redirects.
  - The live linked-page regression asserts the new Performance overview destination instead of the stale Rankings href.
