# MGMT-OPS-003 Gap Reviewer Checklist

Review mode: fail closed

The reviewer must attach evidence for each item. `Not checked`, stale evidence,
or evidence from a different deployed SHA is a request-changes verdict.

## Delivery Identity

- [ ] Task scope matches the owning repository and no frontend mirror was added
  to Pantheon.
- [ ] PR number, head commit, merge commit, merge target, and required checks are
  recorded.
- [ ] Hosted frontend bundle and dev BFF were deployed after those merges.
- [ ] The tested hosted commits contain the implementation commits by ancestry.

## Contract-To-UI Difference

- [ ] Captured authenticated Portfolio Book core, holdings, positions, and
  attribution responses are attached.
- [ ] UI counts for runtimes, telemetry coverage, degraded rows, missing
  bindings, and incidents match the captured responses.
- [ ] Stage, broker, runtime, source-status, stale-telemetry, and risk-state
  filters are visible, affect requests, and survive reload.
- [ ] Paper, canary, live, and unknown capital scopes are explicit and cannot be
  confused by text, color, or grouping.
- [ ] Every degraded or missing-binding row remains visible and actionable.
- [ ] UI never labels degraded, partial, stale, unavailable, or fallback data as
  formal attribution or fully covered.
- [ ] Persona Fleet, Performance Attribution, and Human Review links preserve
  the expected entity and source context.

## Runtime Truth

- [ ] Reviewer samples raw runtime, binding, deployment, pool, and telemetry
  records rather than relying only on aggregate counters.
- [ ] Reconciliation is idempotent and does not delete or hide unresolved rows.
- [ ] Unresolved records have explicit incidents, quarantine reasons, and owner.

## Hosted Browser Evidence

- [ ] Desktop and mobile screenshots cover normal and degraded scenarios.
- [ ] Browser console exception count is recorded.
- [ ] Failed required network request count is recorded.
- [ ] Lazy route chunks load successfully after a cold navigation and reload.
- [ ] No fallback/seed data appears in strict live mode.
- [ ] No clipping, overlap, blank screen, inaccessible control, or misleading
  empty state is present.

## Verdict

The review artifact must contain exactly one verdict:

- `APPROVE`: every required item passed with current hosted evidence.
- `REQUEST_CHANGES`: one or more differences remain; list exact failures and
  assign them back to the owner.

Reviewers must not approve based only on unit tests, a green PR, a successful
deployment job, or the existence of a rendered page.
