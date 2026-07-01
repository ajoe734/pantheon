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

## Scope

Audit and harden write-like controls across:

- Ranking Dashboard: active formula, recalc, compare, freeze, publish, override
- Governance memory: approve, reject, merge
- Consult rules: add, edit, delete, submit for review
- Workflows: create, edit, run
- Hooks: create, toggle, edit
- Settings: break-glass and force transition
- Detail panels that currently use `toast.success` without command receipt

Each control must either:

1. call a governed BFF command endpoint and display command id, audit id, and
   status; or
2. be disabled with an explicit non-production explanation.

## Non-Scope

- Do not enable `VITE_BFF_REAL_WRITES=true` by default.
- Do not perform live capital/trading side effects as part of this task.

## Acceptance

- No in-scope write CTA can complete with only a toast/local state update.
- Dry-run or real-writes-off probes prove no hidden side effect is created.
- High-risk actions require confirmation and return command/audit evidence.
- Tests cover command success, rejection, auth failure, and disabled state.
