# Management Console Hosted Render Re-Run - 2026-07-01

Status: supplemental audit evidence, not final production acceptance.

This re-run was created after the operator asked for another complete pass
because the earlier review still felt partial. It deliberately separates three
truths that were easy to conflate:

1. Hosted pages can render cleanly.
2. The management route/control inventory still contains many distinct
   operator surfaces and control affordances.
3. Production closeout still requires release gates, strict-live harnesses, and
   task lifecycle evidence beyond render success.

## Evidence Inputs

- Pantheon baseline: `origin/dev` at `93f15f80802ec49a615d0000a7b8b8a86c1f8a36`
  after PR #2703.
- Hosted FE deployment manifest:
  `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json`
- Hosted FE deployment commit:
  `d28acd7588878e82bb479f09dc6b881e393fb29c`
- Hosted FE build mode:
  `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`,
  `VITE_BFF_REAL_WRITES=false`.
- Render audit command, run from the execute-plans checkout:

```sh
env PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io npm run audit:render
```

Command result:

```text
[audit-console-render] https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io - 69 routes

all 69 pages clean
```

The script checks each discovered route for error-boundary crashes, `NaN`,
`Invalid Date`, raw `mgmt.*` i18n keys, and uncaught render errors. The run
found none after retry settling.

## Scope Caveats

This is a hosted render-regression pass. It is not a substitute for
`MGMT-GAP-006` or `MGMT-GAP-007`.

Known limitations:

- It does not click every button.
- It does not prove every write-like control has a durable command receipt.
- It does not reproduce the full 93-route / 510-button control crawl from
  `route-control-reaudit-2026-07-01.*`.
- Its route discovery is useful for render regression, but the final production
  harness must use the canonical management route/control inventory rather than
  relying only on this script's auto-discovered set.

## Cross-Checked Inventory

The earlier second-pass route/control crawl remains the richer inventory input:

- 93 route samples.
- 53 primary navigation entries.
- 40 detail, hidden, or alias routes.
- 510 buttons.
- 42 disabled controls.
- 10 mock-visible routes.

This hosted re-run improves confidence that the currently deployed dev FE no
longer has broad render regressions on the discovered route set. It does not
erase the route/control findings above.

## Current Classification

### Keep

These route families map to real and distinct operator jobs, even when their UI
shape is similar:

- Cockpit and oversight: cockpit, persona fleet, human inbox, trading pulse,
  evolution journal, evidence, persona intent.
- Operations: jobs, alerts, incidents, approvals, governance, sentinel,
  interventions, risk, deployments, runtimes.
- Registries: strategies, personas, capital, ranking formulas, rebalances,
  evolution, experiments, artifacts, lineage.
- Readiness: EP5, broker-live, capital binding, BFF HA, strict publish.
- Capability registries: tools, MCP, skills, workflows, hooks, channels, as long
  as unsupported actions fail closed.

### Adjust

- Shared list-shell pages need stronger domain-specific columns, detail
  previews, evidence links, and degraded or empty states.
- Detail aliases must remain redirect-only and must not reintroduce duplicate
  render surfaces.
- Session/RBAC and provider-auth pages must use the same tenant, role, and
  strict-live truth as `/bff/me`.
- Readiness pages must not show seed-positive readiness when live evidence is
  unavailable.
- Load and bundle warnings must be promoted into release-gate failures or
  reviewed waivers.

### Demote Or Hide Until Backed

- Settings controls without governed write receipts.
- Postmortems if not backed by canonical postmortem/evidence reads.
- Formula Studio execution paths until governed backtest runner receipts exist.
- Skill Sandbox execution paths until governed runner traces and cost/readback
  exist.
- Any remaining standalone NL/command page that is redundant with the floating
  Management AI panel and lacks production coverage.

### Deepen

- Ranking: durable recalculate, freeze, publish, override, compare receipts and
  audit readback.
- Alpha Factory: idea-to-strategy pipeline, scaffold/replicate commands,
  lineage, and queue states.
- Lineage: root traversal, evidence packet links, graph persistence, and
  degraded proof.
- Workflows and hooks: run, toggle, edit, create, and delete command receipts.
- Knowledge: promote/dismiss persistence to knowledge, evidence, and audit.
- Governance subpages: route policy, permission, memory, and consult-rule write
  parity.
- Data sources: credential state, ingestion status, provider diagnostics, and
  remediation commands.
- LLM Provider Auth: provider status, reauth, usage, orchestrator status, and
  reliable hosted degraded behavior.

## Task State From This Re-Run

At the moment this render re-run was captured, it proved one narrow thing: 69
hosted pages discovered by `audit:render` rendered cleanly. It did not by
itself prove release-load gating, strict-live all-route/control acceptance, or
final cross-task closeout.

After this supplemental pass, `origin/dev` advanced with the production
closeout chain:

- `MGMT-GAP-010`: production-green load/release gate evidence archived at
  `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/MGMT-GAP-010-production-green-closeout-2026-07-01.md`.
- `MGMT-GAP-006`: hosted acceptance harness evidence archived at
  `docs/04/pantheon_management_console_gap_2026-06-30/archive/management-hosted-acceptance-2026-07-01.md`.
- `MGMT-GAP-007`: final closeout archived at
  `docs/04/pantheon_management_console_gap_2026-06-30/archive/mgmt-gap-007-final-closeout-2026-07-01.md`.
- Pantheon PR #2731 merged the final closeout into `dev` at
  `53131e9bc19fc82aca33b80b255c4389e4295deb`.

The dispatch tracking file therefore treats this page as supplemental evidence,
not as the latest source of task state.

## How To Use This Evidence

Use this file to answer the operator's narrower concern that the previous audit
felt partial and might have missed broad render breakage. Use the final
closeout files above for the production verdict.

This re-run should not override newer production closeout evidence. It should
travel with the route/control crawl because it covers a different layer:
render-regression cleanliness on the hosted FE rather than durable command
receipts, release-load budgets, or strict-live authorization behavior.

## Bottom Line

The re-run reduces the concern that the deployed Management Console is broadly
broken at render time: the hosted render-regression pass is clean on 69
discovered routes. It confirms the opposite of a deletion-first strategy: most
surfaces should stay, but many must be differentiated, demoted, or deepened.

This re-run alone did not prove production completeness. The later
`MGMT-GAP-006`, `MGMT-GAP-010`, and `MGMT-GAP-007` closeout records now carry
that production-level proof, with only the low-severity residual source-scan
warning documented in the final closeout.
