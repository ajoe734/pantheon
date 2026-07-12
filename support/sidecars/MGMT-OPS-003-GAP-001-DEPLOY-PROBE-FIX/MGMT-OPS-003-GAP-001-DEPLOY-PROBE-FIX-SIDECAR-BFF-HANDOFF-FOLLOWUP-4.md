# MGMT-OPS-003 GAP-001 Deploy Probe Fix BFF Handoff Follow-up 4

Status: ready for reviewer and parent-owner absorption

Parent task: `MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX`

Sidecar task:
`MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX-SIDECAR-BFF-HANDOFF-FOLLOWUP-4`

Owned layer: support-only BFF query-gap classification, operator journey, and
frontend/deployment handoff delta

Not changing: L1 canonical truth, BFF/runtime contracts or implementation,
governance, deployment automation, `execute-plans` source, or parent lifecycle
state

## Delta Decision

This follow-up finds no new BFF contract gap beyond the earlier handoff
packets. The existing read remains:

```text
GET /bff/management/persona-fleet
```

The frontend owns `personaFocus` route state and maps it to the existing `q`
query with `page_size=100`. The focus must participate in refresh dependencies.
The plain route must keep the production view selected even when it is empty.
Neither state permits seed data, an unrelated persona, or an implicit switch
to non-production rows.

Accordingly, the parent should absorb this as a deployment/evidence handoff,
not as authority to add a BFF route, parameter, response field, or fallback.

## Operator Journey

1. Open plain `/management/persona-fleet`; production remains selected and a
   zero-row result renders as a truthful live empty state.
2. Open a governed link carrying `personaFocus`; the frontend sends the focus
   as `q` with `page_size=100` and refreshes when the focus changes.
3. If no matching row is returned, show an explicit missing/empty result. Do
   not select another identity or expose unrelated non-production rows.
4. Remove the focus; restore the unfocused production-only default.
5. If the live request fails or reports degradation, preserve that state and
   fail closed without fixtures, seeds, or synthesized rows.

## Parent Absorption Gate

Earlier task-scoped records cite `execute-plans` PR #254 at `e23aba15`, PR #256
at `30bc432f`, and deploy run `29156996097`. Those identifiers prove the
recorded delivery only; they are not proof of the currently served bundle.

Before absorbing the packet or reconciling the parent blocker, the parent
owner should confirm all of the following against one current FE/BFF pair:

- `/deployment.json` reports the exact frontend SHA under acceptance;
- the plain landing records valid fleet rows/banner and no non-production
  rows;
- focused reads visibly use `q` plus `page_size=100` and refresh on focus
  change;
- both BFF reads return `200`, with zero required-request failures, console
  errors, seed rows, or fallback substitutions;
- the linked-page Playwright journey passes against that same deployment.

If the served SHA differs from the recorded delivery, rerun the strict-live
probe. Do not use historical success to close a current deployment gap.

## Composition Boundary

- Parent owner `Codex` owns evidence absorption, frontend delivery,
  deployment acceptance, and parent lifecycle transitions.
- Pantheon BFF remains the read owner. This packet changes no authentication,
  tenant, filtering, pagination, response-shape, or source-truth semantics.
- Classify a future mismatch first as bundle identity, frontend query mapping,
  pagination, auth/CORS, or deployment drift before proposing BFF work.
- This sidecar neither approves nor completes the parent task.

## Verification Sources

- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-001-review.md`
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-001-FOCUS-PAGINATION-FIX-V2-review.md`
- `support/sidecars/MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX/MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`

Reviewer `Codex` should verify that this remains support-only, introduces no
new BFF contract claim, and keeps historical delivery evidence distinct from
current deployment proof.
