# MGMT-OPS-003 Hosted Gap

Date: 2026-07-11

Status: open

Source requirements:

- `docs/04/pantheon_management_console_operations_workflow_2026-07-07/MANAGEMENT_CONSOLE_OPERATIONS_WORKFLOW_PLAN.md`
- `docs/bff/execution-tasks/2026-07-07-management-console-operations-workflow/MGMT-OPS-003-portfolio-risk-monitor.md`

## Verified Deployment Baseline

The BFF-only dev deployment completed successfully in GitHub Actions run
`29136741018`.

- Deployed Pantheon commit: `b178a2e389b13eda8c781056267b90380b096290`
- Included MGMT-OPS-003 contract commit:
  `49ccc6bec6f882d430619c6fe025d6fec093896b`
- Public health and CORS smoke: passed
- Authenticated probes for `/bff/me`, Portfolio Book core, holdings, filtered
  holdings, positions, and performance attribution: six of six returned 200
- Hosted browser route: `/management/portfolio-book` rendered with the dev
  identity `pantheon-dev-browser`

The deployment proves that the Pantheon-owned BFF contract is live. It does
not prove the original MGMT-OPS-003 operator experience is complete.

## Observed Live Data

At hosted verification time the BFF reported:

| Signal | Observed value |
|---|---:|
| Capital pools | 19 |
| Runtimes | 6 |
| Telemetry runtimes | 2 |
| Holdings | 14 |
| Degraded holdings | 14 |
| Missing-binding holdings | 10 |
| Row-level incidents | 14 |
| Live runtimes | 0 |

All observed holdings were paper-stage. The live response included normalized
identity, capital scope, source status, source issues, risk state, incidents,
and links to Persona Fleet, Performance Attribution, and Human Review.

## Plan-To-Live Difference Matrix

| Requirement | BFF | Hosted UI | Gap verdict |
|---|---|---|---|
| Capital, owner persona/runtime, and telemetry reliability are visible | Contract fields present | Capital and owner rows render, but degraded reliability and incidents are not operator-visible | Fail |
| Missing or degraded holdings appear as incidents | 14 incidents returned | No incident list, count, severity, or review action is visible | Fail |
| Filters cover stage, broker, runtime, source status, stale telemetry, and risk state | Query contract present and returns 200 | Required filter controls and URL round-trip are absent | Fail |
| Paper, canary, and live exposure cannot be confused | `capital_scope` and `deployment_stage` are present | No explicit stage segmentation suitable for mixed-stage data; only paper data was available, so canary/live visual behavior is unproven | Fail |
| Degraded coverage cannot appear as formal attribution | `data_confidence` exists | Page shows `formal attribution` and `covered` language while all 14 holdings are degraded and only 2 of 6 runtimes have telemetry | Fail |
| Runtime and binding data is trustworthy | Diagnostics are exposed | Ten holdings lack persona bindings; broker and paper-ledger identity remain unavailable in observed rows | Fail |
| Portfolio Book links into the governed operator workflow | Links are returned | Performance links render, but incident-to-Human-Review and context-preserving round trip are not proven | Fail |

## Root Causes

1. The original frontend changes were developed against a Pantheon-embedded
   `execute-plans` mirror. That mirror was correctly removed by the repository
   boundary change, but the equivalent change was not delivered through the
   separate `ajoe734/execute-plans` repository.
2. The existing frontend consumes older Portfolio Book summaries and does not
   render the new row-level incident, source-confidence, filter, or capital
   scope fields.
3. Dev runtime truth contains missing persona bindings and incomplete telemetry.
   UI work alone cannot make these records production-level.
4. Previous review accepted tests and a BFF merge without performing a
   plan-to-hosted difference audit. The final reviewer contract was too weak.

## Required Closure

The gap closes only when all four execution tasks are done:

1. `MGMT-OPS-003-GAP-001` delivers the frontend monitor contract in the real
   `execute-plans` repository.
2. `MGMT-OPS-003-GAP-002` repairs or explicitly quarantines missing runtime,
   persona, broker, ledger, and telemetry bindings in Pantheon dev truth.
3. `MGMT-OPS-003-GAP-003` proves the cross-page workflow on hosted dev at
   desktop and mobile widths.
4. `MGMT-OPS-003-GAP-004` performs independent, fail-closed reviewer closeout
   against every row in this matrix.

`MGMT-PERF-IA-003` may consume the completed work while consolidating the
Performance Center. It must not hide, supersede, or bypass these gaps.

## Completion Definition

Completion requires merged PRs in the owning repositories, successful dev
deployments, authenticated API evidence, hosted browser screenshots, console
and network failure checks, desktop/mobile coverage, and an explicit reviewer
verdict for every matrix row. A green unit test, a merged PR, or a page that
merely renders is insufficient by itself.
