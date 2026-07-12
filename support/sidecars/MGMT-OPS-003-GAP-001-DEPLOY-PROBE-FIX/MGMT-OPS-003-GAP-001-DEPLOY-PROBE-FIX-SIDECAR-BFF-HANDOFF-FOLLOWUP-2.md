# MGMT-OPS-003 GAP-001 Deploy Probe Fix BFF Handoff Follow-up 2

Status: support packet ready for independent review

Parent task: `MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX`

Sidecar task:
`MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`

Owned layer: BFF query-gap classification, operator journey, frontend handoff,
and deploy-evidence checklist

Not changing: canonical truth, BFF/runtime contracts or implementation,
governance, deployment automation, or `execute-plans` source

## Handoff Decision

The parent repair does not require a new Pantheon BFF route, parameter, or
response field. The existing read remains:

```text
GET /bff/management/persona-fleet
```

The observed gaps were frontend consumption and deployed-bundle verification:

- the unfocused Persona Fleet landing must keep the production tab selected
  even when the production result is empty;
- an explicit `personaFocus` is frontend route state that maps to the existing
  BFF `q` parameter;
- the focused request must use `page_size=100` and refresh when the focus
  changes, so a persona outside the default first page is not reported absent;
- a zero-row production result must remain a truthful empty state, never a
  reason to expose non-production rows or synthesize fallback data.

## Query And UI Boundary

| Journey state | Frontend-to-BFF behavior | Required failure behavior |
|---|---|---|
| Plain `/management/persona-fleet` | Consume the fleet read with production selected. | Render production-empty when appropriate; do not auto-switch to non-production. |
| Explicit `personaFocus` | Map focus to `q`, send `page_size=100`, and include focus in refresh dependencies. | If no matching row returns, show explicit empty/not-found state; do not substitute another persona. |
| Focus removed or plain route restored | Restore the unfocused production-default journey. | Do not persist an implicit non-production selection from the prior focused view. |
| Live response is degraded or empty | Preserve BFF source/degradation truth and only render returned rows. | Do not use seed, fixture, fallback, or locally synthesized personas as live truth. |

`personaFocus` must not be promoted into BFF contract truth. It is translated
by the frontend to the already-supported `q` query.

## Operator Journey

1. Opening the plain route shows the production view, including a truthful
   production-empty state when no production personas exist.
2. Following a governed link with `personaFocus` issues the matching `q` read
   with `page_size=100` and may show the focused persona's context when found.
3. Changing the focus causes a fresh matching request rather than reusing a
   stale collection.
4. A missing focused persona remains visibly missing; the page does not choose
   a different persona or reveal unrelated non-production rows.
5. Returning to the plain route restores the production-only default.

## Evidence The Parent May Absorb

The task-scoped review records establish the following delivery chain:

- `execute-plans` PR #254 merged the production-default repair as
  `e23aba15`;
- `execute-plans` PR #256 merged the focused-query pagination repair as
  `30bc432f`;
- deploy run `29156996097` served SHA `30bc432f` and recorded
  `persona fleet rows valid: true`, `persona fleet live banner valid: true`,
  and `persona fleet has non-production rows: false`;
- both Persona Fleet BFF reads returned `200`, with zero failed network
  requests and no console errors;
- the hosted `e2e/25-persona-fleet-live-linked-pages.spec.ts` journey passed.

These identifiers are delivery evidence, not a new canonical contract. The
parent owner must confirm that the current parent state accepts this evidence
before closing or unblocking the parent task.

## Parent Acceptance Checklist

- [ ] Plain landing remains production-only when production count is zero and
  non-production rows exist.
- [ ] Focus maps to `q`, sends `page_size=100`, and participates in request
  dependencies.
- [ ] Empty, missing, and degraded states remain explicit and fail-closed.
- [ ] No seed or fallback persona is presented as live data.
- [ ] `/deployment.json` identifies the intended merged frontend SHA before
  hosted results are accepted.
- [ ] Hosted probe records the row/banner/non-production assertions, BFF HTTP
  results, console errors, and failed requests.
- [ ] Linked-page Playwright evidence runs against the same deployed FE/BFF
  pair.

## Composition And Ownership

- Parent owner `Codex` decides whether to absorb this packet and owns frontend
  delivery, deployment, hosted verification, and parent lifecycle state.
- Pantheon BFF remains the read owner. This sidecar changes no auth, tenant,
  filtering, pagination, response-shape, or source-truth semantics.
- If a future hosted mismatch appears, classify frontend bundle identity,
  query mapping, pagination, auth/CORS, and deployment drift before proposing a
  BFF change.
- This packet does not approve or complete the parent task.

## Verification Sources

- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-001-review.md`
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-001-FOCUS-PAGINATION-FIX-V2-review.md`
- `support/sidecars/MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX/MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX-SIDECAR-BFF-HANDOFF.md`

Reviewer `Codex` should verify that this remains support-only, preserves the
production-default fail-closed boundary, and does not recast frontend route
state as a BFF contract.
