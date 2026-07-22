# MGMT-GAP-006 Closeout - 2026-07-01

Task: `MGMT-GAP-006`
Owner: `Claude`
Reviewer: `Codex`

## Delivery

- Implementation PR (frontend-checkout / `ajoe734/execute-plans`):
  https://github.com/ajoe734/execute-plans/pull/140
- Implementation merge commit: `49bab98` on `origin/dev` of
  `ajoe734/execute-plans`.
- Evidence archived in this repo: `archive/management-hosted-acceptance-2026-07-01.json`,
  `archive/management-hosted-acceptance-2026-07-01.md`.

## Approved Scope

Built `scripts/accept-management-hosted-production.mjs` in
`ajoe734/execute-plans` (`frontend-checkout:scripts`), a hosted (not
localhost) production-acceptance harness that reproduces and extends the
93-route/510-button `route-control-reaudit-2026-07-01` crawl against the
live dev FE/BFF pair:

- discovers the live management nav from the hosted DOM (63 links found
  2026-07-01) and merges it with the frozen 93-route baseline
  (`scripts/lib/management-routes.mjs`), so nav additions/removals since the
  2026-07-01 crawl are still caught, not just the routes already known;
- asserts every known hidden alias (`control-room`/`one-ring`/`overview`/
  `command-center` -> `cockpit`, `risk-center` -> `risk`, `capital-pools` ->
  `capital`, `ranking-formulas` -> `ranking/formulas`, `rebalances` ->
  `rebalance`, `research` -> `experiments`, `deployment(/:id)` ->
  `deployments(/:id)`) redirects to its canonical final path instead of
  direct-rendering, including the `:id`-parameterized alias forms the
  2026-07-01 crawl showed still direct-rendering;
- resolves a real live id per entity from its BFF list endpoint and crawls
  that live-id detail route in addition to the 2026-07-01 fixture-id route,
  so detail-honesty checks (raw `undefined`/`NaN`/`Invalid Date`) cover both
  a genuine live case and the known fixture/seed id (whose honest
  not-found state is expected, not a failure — no seed-id leakage);
- captures per-route BFF endpoint calls, classifies console errors
  (cors/network/render_crash/benign), and flags seed-fallback-armed /
  mock-success text claims;
- records button/disabled-button counts with each disabled button's
  title/aria-label reason;
- runs a session/RBAC check against the real hosted BFF (`/bff/me` plus a
  privileged management read under a bogus session, requiring 401/403
  fail-closed);
- reads `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-load-gate-2026-07-01.json`
  and requires `result.pass === true`, per the `MGMT-LOAD-006`/`MGMT-LOAD-007`
  handoff notes in the task brief;
- cross-checks write-CTA mock-success risk via a source scan for
  `toast.success(` call sites lacking a nearby governed/receipt signal
  (`runActionSafe`/`bffWrites`/`NonProductionActionButton`/`*commandId`/
  `*receiptId`/`*auditRef`), soft-gated (`warn`) by default since it is a
  heuristic and the task's non-scope note does not require real writes.

Wired the harness into `scripts/aggregate-release-gate.mjs` (execute-plans)
as Gate 8 ("Management Production Acceptance"), reading the harness's own
JSON evidence the same way Gate 3 reads `management-live-deep-validation`
evidence, so a hard failure in any of the checks above blocks Gate 7's
"all critical gates pass" release decision.

## Real Bug Found And Fixed While Building The Harness

The hosted dev BFF's stub-auth only allows tenant `pantheon-dev` (see the
live `/bff/me` response's `tenant.allowed_ids`). `execute-plans`'
`e2e/helpers/auth.ts` `DEFAULT_FE_TENANT_ID` (`tenant-dev`) is a
fixture-mock-only value used by the CI-safe Playwright specs; sending it to
the *real* hosted BFF returns `403 FORBIDDEN` (`tenant_scope` precondition
failure), and because the BFF's error response for that path did not carry
CORS headers, every hosted browser probe reusing that default silently
reported the failure as a **CORS error** on every route rather than an
auth/tenant error. The harness now defaults to `pantheon-dev` and documents
this distinction inline.

## Verification

Hosted rerun evidence (2026-07-01T19:13Z), FE/BFF commit `2129b56cbf86`
(same commit currently deployed, confirmed via `/deployment.json`):

```bash
cd execute-plans
PANTHEON_LOAD_GATE_MANIFEST=<pantheon>/docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-load-gate-2026-07-01.json \
node scripts/accept-management-hosted-production.mjs
```

Result: 103 routes crawled (93 baseline + 7 newly-discovered live nav + 3
resolved live-id detail routes), `overall: warn`, `result.pass: true`.
9 of 10 gate checks `pass`:

- no route crash/blank/navfail (0/103);
- no alias direct-render failure (0);
- no detail-honesty violation across fixture-id and live-id routes (0);
- no seed-fallback-armed claim (0);
- no mock/demo success claim presented as production truth (0);
- no CORS console errors on the hosted origin (0) — confirmed clean once the
  tenant-id bug above was fixed;
- no render-crash console errors (0);
- session/RBAC: authenticated `/bff/me` succeeds, invalid-session `/bff/me`
  and a privileged management read both fail closed (401/403);
- `release-load-gate-2026-07-01.json` reports `result.pass: true`.

1 soft `warn` (does not affect `result.pass`): the `toast.success(`
source-scan cross-check flags 22 of 34 call sites without an obvious nearby
governed/receipt signal within a 25-line window. This is the same residual
area the 2026-07-01 route-control-reaudit's "Source Scan Cross-Check"
section already named (governance, operations, incident, persona, strategy,
artifact rollback, rebalance workflow, freeze/unfreeze, promotion,
allocation limits, overrides, evolution freeze, MCP secrets, metric freeze
flows) — it is a heuristic line-window check, not a click-based live-write
test (the task's non-scope note does not require real writes), so it is
reported for follow-up rather than hard-failing the gate.

`node scripts/aggregate-release-gate.mjs` (execute-plans) confirmed Gate 8
renders correctly from the harness's evidence JSON. `npx eslint` clean on
`scripts/accept-management-hosted-production.mjs`,
`scripts/lib/management-routes.mjs`, and the modified
`scripts/aggregate-release-gate.mjs`.

## Residual Follow-Up

- The write-CTA source-scan `warn` (22 ungoverned `toast.success(` sites) is
  informational; a future pass could either tighten each flagged call site's
  governed-command wiring or narrow the heuristic. Does not block this
  task's `result.pass`.
- 7 live nav links were discovered that are not in the 2026-07-01 baseline
  (`/management/personas/<id>` detail links surfaced directly from the
  persona list); they crawled cleanly (no honesty/alias/crash failures) and
  are recorded in the evidence JSON's `routeCounts.liveNavNewlyFound`.
- `MGMT-GAP-007` (final closeout) can now cite this task's `result.pass:
  true` hosted evidence as the production acceptance proof named in its own
  scope.
