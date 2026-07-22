# MGMT-OPS-003 GAP-001 Deploy Probe Fix BFF Handoff Follow-up 5

Status: reviewer approved; ready for parent-owner absorption

Parent task: `MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX`

Sidecar task:
`MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX-SIDECAR-BFF-HANDOFF-FOLLOWUP-5`

Owned layer: support-only BFF query-gap classification, operator journey, and
frontend/deployment evidence handoff

Not changing: L1 canonical truth, BFF/runtime contracts or implementation,
governance, deployment automation, `execute-plans` source, or parent lifecycle
state

## Handoff Decision

No new Pantheon BFF route, parameter, or response field is required. The
existing read remains:

```text
GET /bff/management/persona-fleet
```

The frontend owns `personaFocus` route state and maps it to the existing `q`
query with `page_size=100`; focus changes must refresh the request. The plain
route must retain the production view even when production is empty. Empty,
missing, or degraded results must remain explicit and must not expose seed,
fixture, synthesized, unrelated, or implicitly selected non-production rows.

The parent should therefore absorb this packet as frontend/deployment evidence,
not as authority for BFF contract work.

## Operator Journey

1. Open plain `/management/persona-fleet`; production stays selected and a
   zero-row result renders as a truthful live empty state.
2. Open a governed link with `personaFocus`; the frontend sends that identity
   as `q` with `page_size=100` and refreshes when the focus changes.
3. If no matching row returns, show an explicit missing/empty state without
   selecting another identity or revealing unrelated non-production rows.
4. Remove focus; restore the unfocused production-only default.
5. On request failure or degradation, preserve the live source state and fail
   closed without fallback data.

## Parent-State Reconciliation

The parent durable status still cites deploy run `29156252948`, where the
focused persona was absent from the default result page. The task-scoped review
records contain later evidence for the exact repair:

- `execute-plans` PR #254 merged the production-default fix as `e23aba15`;
- PR #256 merged focused `q` plus `page_size=100` as `30bc432f`;
- deploy run `29156996097` served `30bc432f`, passed the fleet row/banner and
  no-non-production assertions, returned `200` for both fleet reads, recorded
  no failed requests or console errors, and passed the linked-page journey.

This sidecar does not change the parent status. Parent owner `Codex` should
confirm the currently accepted FE/BFF deployment identity, absorb the newer
evidence if it still matches, and reconcile the stale blocker through the
normal owner/reviewer lifecycle. If the served SHA differs, rerun the strict
live probe and linked-page journey against one current FE/BFF pair.

## Parent Absorption Gate

- Confirm `/deployment.json` reports the exact frontend SHA under acceptance.
- Confirm plain landing records valid live rows/banner and no non-production
  rows.
- Confirm focused reads use `q` plus `page_size=100` and refresh on focus
  changes.
- Confirm required BFF reads return `200`, with zero failed requests, console
  errors, seeds, or fallback substitutions.
- Confirm the linked-page Playwright journey uses the same deployed FE/BFF
  pair.
- Treat run `29156996097` as delivery evidence only unless its SHA is still the
  accepted deployment identity.

## Composition Boundary

- Parent owner `Codex` owns evidence absorption, frontend delivery, deployment
  acceptance, and parent lifecycle transitions.
- Pantheon BFF remains the read owner; this packet changes no authentication,
  tenant, filtering, pagination, response-shape, or source-truth semantics.
- Classify future mismatches first as bundle identity, frontend query mapping,
  pagination, auth/CORS, or deployment drift before proposing BFF work.
- This support packet neither approves nor completes the parent task.

## Verification Sources

- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-001-review.md`
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-001-FOCUS-PAGINATION-FIX-V2-review.md`
- `support/sidecars/MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX/MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md`

Reviewer `Codex` should verify that this remains support-only, introduces no
new BFF contract claim, and keeps historical delivery evidence distinct from
current deployment proof.

## Closeout

Reviewer `Codex` approved commit `221ec7d66` as a support-only handoff. The
approval confirms that this packet adds no BFF contract claim, retains the
production fail-closed default, maps `personaFocus` to `q` with
`page_size=100` and a refresh dependency, and separates historical run/commit
evidence from current deployment identity.

Owner finalization verification:

- `git diff --check`
- task-only diff inspection against `origin/dev`
- existence and claim inspection of every path listed under Verification
  Sources

Publication note: task PR #3401 merged the approved packet into `dev` as
`e6db345565deebc8c57dff05bfc0e090e889b7b4`.
The follow-up publication commit preserves the full task identity required by
the canonical closeout command after synchronizing the task branch with `dev`.
