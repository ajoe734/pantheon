# MGMT-OPS-003 GAP-001 Deploy Probe Fix BFF Handoff Follow-up 3

Status: support packet ready for reviewer and parent-owner absorption

Parent task: `MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX`

Sidecar task:
`MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`

Owned layer: BFF query-gap classification, operator journey, frontend handoff,
and parent evidence reconciliation

Not changing: L1 canonical truth, BFF/runtime contracts or implementation,
governance, deployment automation, `execute-plans` source, or parent lifecycle
state

## Decision

No Pantheon BFF change is indicated. The existing read remains:

```text
GET /bff/management/persona-fleet
```

The frontend owns the two distinct view states:

- the plain Persona Fleet route keeps production selected and renders a
  truthful empty production view when no production rows exist;
- an explicit frontend `personaFocus` maps to the existing BFF `q` parameter,
  sends `page_size=100`, and participates in request refresh dependencies.

`personaFocus` is route state, not a new BFF parameter. Empty or missing results
must not trigger seed data, an unrelated persona, or an implicit switch to
non-production rows.

## Durable-State Reconciliation Handoff

At packet preparation time, the parent task's durable status still describes
the pre-pagination blocker: deploy run `29156252948` could not find a focused
persona on the default result page. Task-scoped delivery records contain newer
evidence that resolves that exact condition:

- `execute-plans` PR #254 merged the production-default fix as `e23aba15`;
- PR #256 merged focused `q` plus `page_size=100` as `30bc432f`;
- deploy run `29156996097` served `30bc432f` and passed the Persona Fleet
  row/banner/non-production assertions;
- both fleet reads returned `200`, with no failed requests or console errors;
- hosted `e2e/25-persona-fleet-live-linked-pages.spec.ts` passed.

This packet does not alter the parent status. Parent owner `Codex` should verify
that the referenced deployment is still the intended acceptance target, then
absorb the newer evidence and reconcile the stale blocker through the normal
task lifecycle. If the current deployed SHA differs, rerun the same strict-live
checks against the current FE/BFF pair rather than treating historical success
as current deployment proof.

## Operator Journey

1. Open plain `/management/persona-fleet`; production remains selected even
   when its result is empty.
2. Follow a governed link with `personaFocus`; the frontend issues the matching
   `q` request with `page_size=100`.
3. Change the focus; a fresh matching read runs instead of reusing stale rows.
4. If the identity is absent, show an explicit empty/not-found result without
   exposing unrelated or fallback personas.
5. Remove focus or return to the plain route; restore the production-only
   default.

## Parent Absorption Gate

- Confirm `/deployment.json` identifies the frontend SHA under review.
- Confirm the plain landing reports valid rows/banner and no non-production
  rows.
- Confirm focused reads map to `q`, use `page_size=100`, and refresh on focus
  changes.
- Confirm both BFF reads are `200` and strict-live mode records zero failed
  requests, console errors, seed rows, or fallback substitutions.
- Confirm the linked-page Playwright journey runs against the same deployed
  FE/BFF pair.
- Reconcile the parent blocker only after the current acceptance target and
  evidence identity match.

## Composition Boundary

- Parent owner `Codex` owns evidence absorption, deployment acceptance, and
  parent lifecycle transitions.
- Pantheon BFF remains the read owner; this sidecar changes no authentication,
  tenant, filtering, pagination, response-shape, or source-truth semantics.
- A future mismatch should be classified first as deployed-bundle identity,
  frontend query mapping, pagination, auth/CORS, or deployment drift.
- This packet neither approves nor completes the parent task.

## Verification Sources

- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-001-review.md`
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-001-FOCUS-PAGINATION-FIX-V2-review.md`
- `support/sidecars/MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX/MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX/MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`

Reviewer `Codex` should verify that the packet remains support-only, accurately
distinguishes historical deployment evidence from current deployment identity,
and does not promote frontend focus state into BFF contract truth.
