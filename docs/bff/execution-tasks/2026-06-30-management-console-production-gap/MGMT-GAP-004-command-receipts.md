# MGMT-GAP-004 - Management Command Receipts And Write Truth

Owner: Codex
Reviewer: Claude2
Batch: 3
Fleet lane: command governance and frontend integration
Depends on: `MGMT-GAP-002`, `MGMT-GAP-003`
Status: done

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

## Closeout Evidence

Closed by `ajoe734/execute-plans` PR #132:
`https://github.com/ajoe734/execute-plans/pull/132`.

| Item | Evidence |
|---|---|
| Branch head | `60151a1c8924a4708a2aac0f2cc5ff2da250b16a` |
| Merge commit | `8ad6e034e9f831a11f143496b0320beba7a41dc2` |
| PR integration gate | `https://github.com/ajoe734/execute-plans/actions/runs/28500266955` |
| Dev integration gate | `https://github.com/ajoe734/execute-plans/actions/runs/28500441725` |
| Passing dev gate job | `https://github.com/ajoe734/execute-plans/actions/runs/28500441725/job/84480698924` |
| Dev FE deploy | `https://github.com/ajoe734/execute-plans/actions/runs/28500441733` |
| Hosted deployment | `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` |
| BFF health | `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/healthz` |
| Archive | `docs/04/pantheon_management_console_gap_2026-06-30/archive/mgmt-gap-004-closeout-2026-07-01.md` |

Task-level command receipt/write truth is done. Full management-console
production closeout still waits for `MGMT-GAP-005`, `MGMT-GAP-008`,
`MGMT-GAP-009`, `MGMT-GAP-010`, and then the hosted all-route harness in
`MGMT-GAP-006`.
