# AG-DYNUI-PROD-003 - Trading Room Default Dynamic Entry

Owner: Claude2
Reviewer: Codex
Depends on: `AG-DYNUI-PROD-001`

## Problem

The default `/agora/trading-room` path can render an empty aggregate view:
`All Strategies`, `No strategies in the Trading Room`, empty queue, and empty
position actions. The dynamic proposal workflow is only reached when a strategy
id and strategy version are present.

## Scope

- Define and implement the default Trading Room entry state from live BFF data.
- If no strategy is ready, route the operator into the Strategy Workshop or a
  design-pack dynamic readiness flow instead of a dead empty shell.
- If a ready strategy exists, enter the workspace proposal preview path without
  requiring manual URL surgery.
- Keep the state honest: no hardcoded fake strategies and no static mock
  dashboard.

## Acceptance

- Hosted `/agora/trading-room` never lands on an inert empty table shell without
  a meaningful dynamic next action.
- Strategy selection, readiness, proposal generation, and back-to-workshop
  behavior are tested.
- Empty, loading, degraded, and no-ready-strategy states are driven by BFF data.
- Live screenshot evidence covers no-strategy and ready-strategy cases.
