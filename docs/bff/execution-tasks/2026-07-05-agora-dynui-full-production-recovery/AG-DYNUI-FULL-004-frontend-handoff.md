# AG-DYNUI-FULL-004 Frontend Workshop To Trading Room Handoff

Owner: Codex
Reviewer: Codex2
Status: execute-plans PR open; integration gate pending.

## Current Evidence

- PR: `https://github.com/ajoe734/execute-plans/pull/185`
- Head commit: `4668d52bd76c973946d8466f1d65ab1f43358cc2`
- The PR changes the Strategy Workshop CTA from a default
  `/agora/trading-room` navigation to an explicit
  `/agora/trading-room/:strategyId` navigation with readiness query context.
- The PR also forwards readiness context into `TradingRoomPage`.

## Required Before Done

- FE-BFF `integration-gate` must pass.
- PR #185 must be merged into execute-plans `dev`.
- Dev FE deploy must publish the merge SHA.
- Hosted browser proof must show the CTA route and selected strategy context
  without using BFF response fixtures.
- If the BFF still cannot produce a browser-scoped ready strategy, close only
  as frontend handoff partial and keep `AG-DYNUI-FULL-005` open.

## Failure Conditions

- Default-route-only navigation.
- Local Vitest pass without hosted proof.
- Any hosted proof that uses `page.route()` to fabricate readiness or strategy
  data.
