# Management Console Adjustment Development Plan - 2026-07-02

Status: archived planning packet for post-closeout adjustment development.

This plan turns the management-console audit conclusion into development
batches. It does not reopen the completed `MGMT-GAP-*` production closeout.
The production gates remain closed; this packet is for the next layer of
operator-product quality, payload discipline, and durable command depth.

## Authoritative Inputs

- Final production closeout:
  `docs/04/pantheon_management_console_gap_2026-06-30/archive/mgmt-gap-007-final-closeout-2026-07-01.md`
- Hosted acceptance harness:
  `docs/04/pantheon_management_console_gap_2026-06-30/archive/management-hosted-acceptance-2026-07-01.md`
- Route/control re-audit:
  `docs/04/pantheon_management_console_gap_2026-06-30/archive/route-control-reaudit-2026-07-01.md`
- Supplemental hosted render re-run:
  `docs/04/pantheon_management_console_gap_2026-06-30/archive/hosted-render-rerun-2026-07-01.md`
- Load-gate production-green closeout:
  `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/MGMT-GAP-010-production-green-closeout-2026-07-01.md`
- Management list contract:
  `docs/architecture/management-list-api-contract.md`
- Management list contract audit:
  `docs/architecture/management-list-contract-audit-2026-07-02.md`

## Current Proven State

- The `MGMT-GAP-*` production closeout is complete. The hosted harness crawled
  103 routes, inspected 1303 buttons, and reported `result.pass=true` with one
  non-blocking source-scan warning.
- The release load gate is production-green: `release-load-gate-2026-07-01.json`
  reports `result.pass=true`, zero failures, and zero missing checks.
- The hosted render re-run found 69 discovered pages clean at render time.
- The remaining work is not "make production pass"; it is to reduce operator
  repetition, improve list/read contracts, deepen real command paths, and make
  repeated management surfaces feel domain-specific.
- The initial 2026-07-02 list-contract audit found 233 existing smells: 79 P0
  and 154 P1. After the `MGMT-LIST-CONTRACT-*` burn-down through
  `MGMT-LIST-CONTRACT-018`, the current guardrail is 65 existing smells:
  0 P0 and 65 P1.
- The original live hotspot was `/bff/management/persona-fleet`, which returned
  about 16.8 MB JSON for a table whose first visible rows appeared at about
  5.0 s. That route has since been slimmed; the remaining work is now casing
  consistency and page-before-projection, not P0 duplicate envelopes.
- The re-run inventory separates mounted UI, typed fetchers, BFF routes, and
  historical hosted route crawls. The current repo-mounted Management UI is a
  three-panel shell, not dozens of active pages.
- The final closeout carries one low-severity follow-up: 22 of 34
  `toast.success(` call sites lacked an obvious governed/receipt signal within
  the source-scan heuristic window.

## Planning Principles

1. Do not delete valid operator viewpoints just because their scaffolds look
   similar. Many pages are different operator jobs sharing generic table code.
2. Move list-contract work ahead of UI polish where payload shape is the real
   bottleneck. A refined table still feels bad if it downloads a full aggregate.
3. Keep old aliases as redirects only. Do not reintroduce duplicate render
   surfaces.
4. A write-looking action is production only when it has a governed command,
   receipt, audit/readback evidence, or an explicit disabled/non-production
   state.
5. Capability studios stay demoted until they have real runners, traceability,
   cost/readback, and failure semantics.
6. Every batch must preserve the existing hosted acceptance harness and release
   load gate. Product-quality work must not weaken production proof.

## Batch Plan

| Batch | Task | Primary repo | Owner | Reviewer | Purpose |
|---|---|---|---|---|---|
| 0 | `MGMT-ADJ-000` | Pantheon + execute-plans | Codex | Claude | Freeze this plan, archive it, and confirm no `MGMT-GAP-*` task is reopened. |
| 1 | `MGMT-ADJ-001` | Pantheon BFF | Claude2 | Codex | Slim management list contracts, starting with `/bff/management/persona-fleet`. |
| 1 | `MGMT-ADJ-002` | Pantheon BFF | Claude | Codex2 | Replace board-pack full child payloads with summaries, counts, hrefs, and bounded previews. |
| 2 | `MGMT-ADJ-003` | execute-plans FE | Codex2 | Claude | Differentiate registry/list pages with domain-specific columns, detail previews, and degraded states. |
| 2 | `MGMT-ADJ-004` | execute-plans FE | Gemini | Claude2 | Recluster decision and operations views so repeated inbox/sentinel/approval/governance flows behave like one workbench. |
| 3 | `MGMT-ADJ-005` | execute-plans FE + Pantheon BFF | Codex | Claude2 | Burn down write-CTA source-scan warnings to governed receipts or explicit non-production disablement. |
| 3 | `MGMT-ADJ-006` | execute-plans FE + Pantheon BFF | Gemini2 | Codex | Decide runner-vs-demotion for Formula Studio, Skill Sandbox, Tools, MCP, and Skills. |
| 4 | `MGMT-ADJ-007` | Pantheon + execute-plans | Claude | Codex | Deepen ranking, alpha-factory, lineage, workflow, hook, and knowledge flows with receipts and evidence. |
| 5 | `MGMT-ADJ-008` | Pantheon + execute-plans | Codex | Claude | Refresh hosted acceptance, list-contract audit, payload budgets, and closeout archive. |

## Batch 0 - Plan Freeze

Scope:

- Land this document and the execution packet index.
- Link the plan from the original management gap README and execution packet.
- Preserve the production-closeout truth: all `MGMT-GAP-*` rows remain done.

Acceptance:

- `git diff --check` passes.
- The plan is merged to `dev` through PR/check/merge flow.
- No runtime source file changes are included in this planning PR.

## Batch 1 - List Contract Slimming

### `MGMT-ADJ-001`: Persona Fleet And List Envelope Slimming

Problem:

- `/bff/management/persona-fleet` is the known live bottleneck.
- It duplicates row lists across top-level `items`, `data.items`, and
  `data.persona_fleet`.
- It embeds related aggregates such as persona league, capital pools, runtime
  bindings, and human inbox.

Required development:

- Produce one canonical envelope with `data.items`, `page_info`, and `meta`.
- Remove top-level list aliases and domain-list duplicates for migrated
  endpoints.
- Add server-side filters/page controls needed by visible FE tables.
- Move connector/source health, runtime bindings, capital, league, and inbox
  detail to detail or bounded section endpoints.
- Retire corresponding fingerprints from
  `management-list-contract-baseline.json` only when tests prove the smell is
  gone.

Acceptance:

- Default `/bff/management/persona-fleet` body targets <= 250 KB and must stay
  under the 1 MB hard review gate.
- Default page size is <= 50 rows.
- `scripts/audit_management_list_contract.py --baseline
  docs/architecture/management-list-contract-baseline.json --fail-on-new`
  passes.
- Focused BFF tests prove one envelope, one list field, no raw source record in
  list rows, and server filters applied before detail expansion.

### `MGMT-ADJ-002`: Board Pack And Aggregate Summary Contracts

Problem:

- Board/cockpit pack helpers can wrap full child endpoint payloads, multiplying
  the same DTO mistakes across one shell request.

Required development:

- Return section id, label, status, counts, deltas, degraded reason, and hrefs.
- Replace embedded child endpoint payloads with bounded `preview_items` where
  a preview is truly needed.
- Keep detail-grade rows behind explicit drilldown routes.

Acceptance:

- Board pack has no complete child endpoint payloads.
- Payload budget is documented and enforced by tests.
- The FE shell can render summaries without fetching full lists at startup.

## Batch 2 - UI Differentiation

### `MGMT-ADJ-003`: Registry And List Surface Differentiation

Problem:

- The route/control audit concluded most surfaces should stay, but many shared
  list-shell pages feel repetitive because they do not expose enough
  domain-specific truth.

Required development:

- Add domain-specific column sets and row summaries for strategies, personas,
  capital, ranking formulas, rebalances, evolution, experiments, artifacts,
  lineage, tools, MCP, skills, workflows, hooks, and channels.
- Add detail preview affordances that show why the row matters without
  downloading the full detail payload by default.
- Show explicit degraded, empty, source, and fixture labels.
- Remove client-side filtering over hidden full datasets for migrated endpoints;
  the FE must request server filters/page.

Acceptance:

- Each migrated list page has a page-specific column contract and empty/degraded
  state copy.
- Hosted route crawl remains clean.
- No old detail alias direct-renders.
- No new list-contract audit smell is introduced.

### `MGMT-ADJ-004`: Decision And Operations Workbench Cohesion

Problem:

- Human inbox, sentinel, interventions, approvals, governance, incidents, jobs,
  alerts, and audit routes are valid operator jobs but feel scattered.

Required development:

- Add a shared workbench frame for decision queues and operations queues.
- Keep canonical routes, but align filters, status chips, escalation labels,
  and evidence links.
- Prefer cross-links and tabs over new first-level nav expansion.

Acceptance:

- Existing bookmarked routes still resolve.
- The hosted harness sees no new route crash/blank/navfail.
- Decision and operations queues expose consistent owner, severity, status,
  evidence, and next-action columns.

## Batch 3 - Command And Capability Depth

### `MGMT-ADJ-005`: Write CTA Receipt Burn-Down

Problem:

- The hosted closeout records a soft warning: 22 `toast.success(` call sites
  lack obvious nearby governed/receipt evidence under the heuristic scan.

Required development:

- Re-scan all management write-like CTAs.
- For each flagged action, either wire `runActionSafe`/`bffWrites` with command
  id, receipt id, audit id, and readback, or convert the control to an explicit
  disabled/non-production affordance.
- Reduce heuristic false positives only after the code carries a clear receipt
  or documented whitelist.

Acceptance:

- The source-scan warning is zero, or remaining warnings are reviewed
  allow-list entries with owner and expiry.
- Hosted acceptance remains `result.pass=true`.
- No local-only success toast remains on an enabled production action.

### `MGMT-ADJ-006`: Capability Runner Or Demotion Decisions

Problem:

- Formula Studio, Skill Sandbox, Tools, MCP, and Skills are useful surfaces, but
  a visible execution affordance is only production-worthy with a governed
  runner and readback.

Required development:

- For each capability surface, pick one path: real runner or durable demotion.
- Runner path requires job id, cost/usage capture where relevant, trace link,
  status readback, failure reason, and audit evidence.
- Demotion path keeps the surface readable but removes production-looking
  execution success claims.

Acceptance:

- No capability page claims mock execution as production success.
- Runner-backed actions have receipts and readback.
- Demoted actions are visible as unavailable/non-production with reason and
  owner.

## Batch 4 - Deep Product Flows

### `MGMT-ADJ-007`: Ranking, Alpha Factory, Lineage, Workflow, Hook, Knowledge

Required development:

- Ranking: recalculate, freeze, publish, override, and compare flows must carry
  receipts and audit readback.
- Alpha Factory: idea-to-strategy, scaffold, replicate, lineage, and queue
  states need durable backend contracts or demotion.
- Lineage: root traversal, evidence packet links, graph persistence, degraded
  proof, and live-id probes.
- Workflows and hooks: run, toggle, edit, create, and delete command receipts.
- Knowledge: promote/dismiss persistence to knowledge, evidence, and audit.

Acceptance:

- Each flow has a route-level contract, command/readback contract, and hosted
  evidence artifact.
- The route/control crawl can classify every write-like control as receipt,
  disabled, or read-only.

## Batch 5 - Acceptance Refresh

### `MGMT-ADJ-008`: Post-Adjustment Closeout

Required development:

- Refresh the management route/control crawl after Batches 1-4.
- Refresh the hosted acceptance harness route set and button/control counts.
- Re-run the release load gate and list-contract audit.
- Archive before/after payload evidence for high-impact endpoints.

Acceptance:

- Hosted acceptance remains `result.pass=true`.
- Release load gate remains `result.pass=true`.
- List-contract audit has no new smells and documents retired fingerprints.
- Final closeout names PRs, merge SHAs, hosted FE/BFF evidence, residual risks,
  and any explicitly deferred runner work.

## Delete, Hide, Or Keep Rules

Keep:

- Operator viewpoints with distinct jobs: oversight, readiness, decision queues,
  operations, registries, ranking, lineage, governance, capabilities, and
  evidence.

Hide or demote:

- Fixture/demo ids that can be mistaken for production data.
- Capability execution controls without runner/readback.
- Break-glass or settings controls without governed command receipts.
- Old aliases that render a second copy of a canonical detail page.

Delete only when:

- The route is a pure duplicate and a redirect preserves bookmarks.
- The surface has no unique operator job after workbench clustering.
- The deletion is covered by hosted route tests and documented redirect behavior.

## Non-Goals

- Do not reopen `MGMT-GAP-*` production blockers.
- Do not enable real capital or broker-side effects as part of this plan.
- Do not replace the existing hosted acceptance harness; extend it where needed.
- Do not accept broad UI redesign without BFF contract and payload evidence.

## Required Validation Commands

Planning PR:

```sh
git diff --check
```

Implementation PRs, as applicable:

```sh
python3 scripts/audit_management_list_contract.py \
  --baseline docs/architecture/management-list-contract-baseline.json \
  --fail-on-new

node scripts/aggregate-release-gate.mjs
```

Frontend implementation PRs must also run the execute-plans management route,
render, and hosted acceptance commands named by the active FE integration gate.

## Archive Status

This planning document is the archive anchor for `MGMT-ADJ-*`. The companion
execution packet is:

`docs/bff/execution-tasks/2026-07-02-management-console-adjustment-development/INDEX.md`
