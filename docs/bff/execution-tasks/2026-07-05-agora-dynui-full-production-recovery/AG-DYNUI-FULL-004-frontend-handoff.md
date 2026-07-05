# AG-DYNUI-FULL-004 Frontend Workshop To Trading Room Handoff

Owner: Codex
Reviewer: Codex2
Status: review-approved frontend handoff partial; closeout evidence recorded.

## Current Evidence

- PR: `https://github.com/ajoe734/execute-plans/pull/185`
- Reviewed head commit: `722cb18fd6d5e5e33b2c4e4866c72bcdd17a8571`
- Merge commit: `4cce2d10f14abcc7af5f15638e0e0efa63885944`
- PR #185 merged into execute-plans `dev` at `2026-07-05T15:17:40Z`.
- FE-BFF `integration-gate` succeeded:
  `https://github.com/ajoe734/execute-plans/actions/runs/28744994745/job/85234274266`
- Dev FE deploy succeeded:
  `https://github.com/ajoe734/execute-plans/actions/runs/28745373659`
- Hosted `deployment.json` reports commit
  `4cce2d10f14abcc7af5f15638e0e0efa63885944`, source branch `dev`, and
  `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`,
  `VITE_BFF_REAL_WRITES=false`.
- The merged frontend changes the Strategy Workshop CTA from a default
  `/agora/trading-room` navigation to an explicit
  `/agora/trading-room/:strategyId` navigation with readiness query context.
- The merged frontend also forwards readiness context into `TradingRoomPage`.

## Hosted Evidence

Evidence packet:
`docs/deployment/evidence/ag-dynui-full-004/20260705T152800Z/`

Files:

- `deployment.json` - hosted FE deployment manifest for merge
  `4cce2d10f14abcc7af5f15638e0e0efa63885944`.
- `bff-trading-room.json` - live authenticated BFF readback for
  `GET /bff/agora/trading-room`; returned `strategies: []`.
- `bff-workshops.json` - live authenticated BFF workshop list; returned one
  browser-scoped workshop:
  `ce63ec2a-c5f1-4e41-8219-e410d22037c7`.
- `bff-workshop-readiness.json` - live authenticated readiness readback for
  that workshop; `highest_ready_gate` remains absent and the Trading Room gate
  is blocked by missing Strategy Registry reference and full-validation
  readiness.
- `hosted-workshop-cta-state.json` - DOM state from hosted FE; the
  `add-to-trading-room-btn` is disabled with title
  `Trading Room gate not yet ready (highest: none)`.
- `hosted-strategy-workshop-list.png` - hosted Strategy Workshop list.
- `hosted-workshop-session-not-ready.png` - hosted workshop session showing the
  disabled Add to Trading Room CTA.
- `hosted-trading-room-default-empty.png` - hosted Trading Room default entry
  showing zero live strategies and a Strategy Workshop entry path.
- `hosted-browser-proof-summary.json` - summary of the no-fixture browser
  probe.

The hosted browser probe did not register `page.route()` and did not mock BFF
responses. It used dev-login browser storage plus live dev BFF calls only.

## Closeout Decision

Close this task as frontend handoff partial.

The frontend delivery is merged, reviewed, integration-gated, deployed, and
hosted evidence is recorded. The live browser-scoped BFF state still cannot
produce a ready `strategyId` / `strategyVersion`; the hosted CTA is therefore
correctly disabled and cannot exercise live CTA navigation without fixtures.

`AG-DYNUI-FULL-005` remains open for the live dynamic workflow and
ready-strategy materialization proof.

## Failure Conditions

- Default-route-only navigation.
- Local Vitest pass without hosted proof.
- Any hosted proof that uses `page.route()` to fabricate readiness or strategy
  data.
