# MGMT-GAP-004 - Management Command Receipts And Write Truth

Owner: Codex
Reviewer: Claude2
Batch: 3
Fleet lane: command governance and frontend integration
Depends on: `MGMT-GAP-002`, `MGMT-GAP-003`

## Problem

Many management controls look write-capable but only mutate local state, show a
toast, use a mock overlay, or run a seed helper. This is dangerous in a live
management console because operators can believe a command was submitted.

The 2026-07-01 route/control re-audit measured the scale of the issue: 93 route
samples exposed 510 buttons, 468 enabled buttons, and 42 disabled buttons. The
highest-density routes were `/management/sentinel`, `/management/ranking`,
`/management/governance/policies/rp_quant_v2`, seed/detail strategy/capital/
rebalance pages, evolution detail, MCP detail, tool detail, skill detail,
alerts, artifacts, and deployments.

## Scope

Audit and harden write-like controls across:

- Ranking Dashboard: active formula, recalc, compare, freeze, publish, override
- Governance memory: approve, reject, merge
- Consult rules: add, edit, delete, submit for review
- Workflows: create, edit, run
- Hooks: create, toggle, edit
- Settings: break-glass and force transition
- Detail panels that currently use `toast.success` without command receipt
- High-density route/control hotspots from
  `docs/04/pantheon_management_console_gap_2026-06-30/archive/route-control-reaudit-2026-07-01.md`
- Overlay create/delete boundaries in
  `src/management/components/write/createEntity.ts`
- Write fallback boundaries in `src/lib/bff-v1/writeFallback.ts`

Each control must either:

1. call a governed BFF command endpoint and display command id, audit id, and
   status; or
2. be disabled with an explicit non-production explanation.

## Non-Scope

- Do not enable `VITE_BFF_REAL_WRITES=true` by default.
- Do not perform live capital/trading side effects as part of this task.

## Acceptance

- No in-scope write CTA can complete with only a toast/local state update.
- Enabled high-density controls from the route/control re-audit either return a
  command id, receipt, audit id, dry-run/no-side-effect proof, or become
  `NonProductionActionButton` with a clear reason.
- Disabled controls have explicit reasons and tests, especially governance
  policy submit/reset/environment/delete, consult create/submit, tools rate
  limit/risk classification, settings save, incident pause, and channel send
  test.
- `toast.success` is not accepted as write proof unless the same flow also
  renders command/audit receipt evidence.
- `writeOverlay` and write fallback paths cannot be presented as durable
  production persistence.
- Dry-run or real-writes-off probes prove no hidden side effect is created.
- High-risk actions require confirmation and return command/audit evidence.
- Tests cover command success, rejection, auth failure, and disabled state.
