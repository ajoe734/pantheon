# Management Console Complete Re-Audit - 2026-07-02

| Field | Value |
|---|---|
| Status | Complete re-audit, re-run after the user challenge that the prior read was too partial |
| Re-audit basis | Clean worktree `/tmp/pantheon-mgmt-reaudit-20260702`, branch `task/mgmt-complete-reaudit-20260702` |
| Base commit | `e2eb5ba90483ba2eeaf22c97f1465a7ee244eafa` (`origin/dev`, PR `#2762`) |
| Scope | Current repo frontend entrypoints, isolated legacy widgets, Management BFF routes, typed frontend adapters, list-contract audit, historical hosted route/control evidence, focused validation |
| Important correction | The earlier audit blended hosted historical FE route inventory, BFF API inventory, typed adapters, and the current repo's mounted management UI. This pass keeps those layers separate and does not treat every endpoint as a finished page. |

## Executive Finding

The user's concern is valid, but the root cause is more specific than "too many
UI pages."

In the current repo, the mounted Management frontend is thin:

- `execute-plans/src/entries/management-main.tsx` renders only
  `LiveEvidenceManifestPanel`, `LoopTruthPanel`, and `OodaPacketDrawer`.
- Those visible components directly call only:
  - `managementClient.evidenceExplorer.list({ page_size: 25 })`;
  - `managementClient.loopHealth.list()`;
  - `managementClient.oodaPackets.get(id)` when the drawer is opened.

The large repeated surface is mostly in the BFF/API and typed adapter layer:

- `services/control-plane/bff/main.py` currently defines 55 unique
  `/bff/management*` route decorators.
- `execute-plans/src/lib/bff-v1/management.ts` defines 30-plus Management
  response contracts and fetchers.
- `execute-plans/src/lib/bff/client.ts` exposes 29 `MANAGEMENT_FAMILIES` in a
  generic read adapter, but most are not mounted as differentiated Management
  pages in the current entrypoint.

So the correction is:

1. Do not treat every BFF management endpoint as a finished UI page.
2. Do not build one visible page per endpoint.
3. First normalize the BFF list contracts, then design a smaller set of
   operator workflows that consume them.

## Sources Checked

### Current Frontend

Checked files:

- `execute-plans/management.html`
- `execute-plans/vite.management.config.ts`
- `execute-plans/src/entries/management-main.tsx`
- `execute-plans/src/management/components/**`
- `execute-plans/src/lib/bff/client.ts`
- `execute-plans/src/lib/bff-v1/paths.ts`
- `execute-plans/src/lib/bff-v1/management.ts`
- `apps/management/src/screens/**`

Findings:

| Area | Finding | Decision |
|---|---|---|
| Mounted Management UI | Only three current components are mounted in `management-main.tsx`. | Keep, but this is not a full management console yet. |
| `apps/management` | Contains three isolated screens: Broker Go/No-Go, Capital Binding Go/No-Go, Human Gate. No current evidence that `execute-plans` imports them. | Treat as orphan/legacy until an import audit proves ownership; either migrate into the real shell or delete/archive. |
| Typed BFF management module | Many fetchers exist for cockpit, board pack, readiness, portfolio, persona league, quarterly ranking, attribution, cost, etc. | Keep as typed API only after contract cleanup; do not expose all as first-level pages. |
| Generic `managementClient` | Useful live/mock adapter but normalizes around many families that are not visible UI workflows. | Keep for now; future UI should call canonical envelope shapes only. |

### Re-Run Surface Matrix

This re-run classifies the Management system by actual ownership layer, not by
endpoint count.

| Surface group | Current evidence | Decision |
|---|---|---|
| Mounted truth shell | `management-main.tsx` mounts Live Evidence, Loop Truth, and OODA packet drawer only. | Keep and harden; this is the current repo's real visible shell. |
| Isolated readiness widgets | `apps/management` has HumanGate, Broker Go/No-Go, and Capital Binding Go/No-Go widgets but no proven import from `execute-plans`. | Migrate deliberately into the shell or archive/delete as legacy; do not leave as a second quiet app. |
| Evidence/Loop/OODA | Current mounted components directly read Evidence Explorer, Loop Health, and OODA packet detail. | Deepen as the first real operator workflow because it is already visible. |
| PM12 performance suite | Persona League, Quarterly Ranking, Performance Attribution, Cost Attribution, and Portfolio Book exist as BFF/typed contracts, not mounted pages. | Keep as one performance-review suite with drilldowns; do not create one first-level page per endpoint. |
| Decision/ops queues | Human Inbox, Sentinel, Intervention Stream, HIQ Backlog, Governance Ledger, Readiness, Incidents, Alerts, Approvals are valid operator jobs but currently spread across API/family names. | Adjust and merge into fewer queue/workbench views with canonical filters, owners, evidence, and receipts. |
| Strategy/capital/risk/loop analytics | Strategy Allocation, Capital Flow, Risk Radar, Incident Timeline, Loop Throughput still carry casing debt. | Keep as analytical slices, but finish snake_case contracts and page-before-projection before UI expansion. |
| Management AI/NL | NL ask, stream, AI audit/conversations/attachments are a separate governance/product surface. | Deep develop as an AI Ops workflow with audit, paging, provider auth, and trace semantics. |
| Capability studios and seed details | Historical route crawl found Formula Studio, Skill Sandbox, Tools/MCP/Skills seed/detail surfaces and demo evidence ids. | Hide/demote unless backed by real runners, receipts, readback, and fixture gating. |
| Compatibility aliases | Historical hosted UI had route aliases and some direct-render detail aliases. | Keep aliases only as redirects; delete duplicate render paths and duplicate payload aliases. |

### Management BFF Route Inventory

Static route extraction now finds 55 `/bff/management*` route decorators and
55 unique `(method, path)` pairs.

Important structural issues:

- The earlier duplicate `/bff/management/persona-league` declaration has been
  removed; the management route now has one registered owner.
- Several endpoint families still have typed fetchers without a differentiated
  mounted workflow in the current Management entrypoint.
- Remaining contract debt is P1-only: casing duplicates and four
  project-before-page helpers.

## Authenticated Route Smoke

Command shape:

```sh
PANTHEON_BFF_AUTH_STUB=true PANTHEON_BFF_AUTH_MODE=permissive \
python3 <route-smoke-script>
```

Header used:

```text
Authorization: Bearer op-reaudit:operator
```

Result:

| Status | Count | Meaning |
|---|---:|---|
| `200` | 45 | List/aggregate routes responded with payloads |
| `204` | 1 | CORS/preflight route responded |
| `404` | 6 | Expected for sample detail ids that do not exist |

Anonymous smoke was also run first. Every Management GET sample returned `401`
except OPTIONS, proving the route family is consistently fail-closed without
auth.

Largest authenticated payloads with `page_size=2`:

| Route | Bytes | Finding |
|---|---:|---|
| `/bff/management/human-inbox?page_size=2` | 193,113 | Too large for a two-row page; still returns aliases and likely detail-grade source/context data. |
| `/bff/management/ai/audit?page_size=2` | 180,596 | Ignores or does not honor the apparent `page_size` style; returns 85 items in smoke. |
| `/bff/management/governance-ledger?page_size=2` | 56,137 | Two rows still carry too much detail and duplicate aliases. |
| `/bff/management/readiness/ep5?page_size=2` | 13,576 | Acceptable size, but envelope still has top-level aliases. |
| `/bff/management/quarterly-ranking/recommendations?page_size=2` | 13,271 | Needs envelope/casing cleanup and workflow design. |

Routes whose smoke payload still had top-level list aliases include:

- `trading-pulse`, `trading-pulse/rankings`, `sentinel-pulse`;
- `human-inbox`, `hiq-backlog`, `intervention-stream`,
  `evolution-journal`;
- `ai/audit`, `ai/conversations`;
- `evidence`, `persona-intent`;
- all readiness routes;
- `persona-league`, `persona-league/rankings`, `movers`, `tiers`, `heatmap`;
- `quarterly-ranking`, `quarterly-ranking/formula`,
  `quarterly-ranking/recommendations`;
- `governance-ledger`, `cost-attribution`.

The already remediated families behaved correctly in this smoke:

- `portfolio-book`;
- `portfolio-book/pools`;
- `portfolio-book/exposure`;
- `portfolio-book/holdings`;
- `portfolio-book/positions`;
- `board-pack`;
- `persona-fleet`.
- `strategy-allocation`;
- `capital-flow`;
- `risk-radar`;
- `incident-timeline`;
- `loop-throughput`;
- `performance-attribution` and its by-strategy/by-persona/by-pool routes.

## Static List-Contract Audit

Command:

```sh
python3 scripts/audit_management_list_contract.py \
  --baseline docs/architecture/management-list-contract-baseline.json \
  --fail-on-new \
  --format summary
```

Result on clean `origin/dev`:

```text
source=services/control-plane/bff/main.py baseline=docs/architecture/management-list-contract-baseline.json issues=187 new=0 retired=0
```

Breakdown:

| Category | Count | Severity | Meaning |
|---|---:|---|---|
| `duplicate-envelope` | 19 | P0 | `data` plus top-level aliases such as `items`, `rows`, `rankings`, `summary` |
| `duplicate-list-alias` | 17 | P0 | Same list returned under multiple semantic names |
| `source-record-in-list-dto` | 10 | P0 | Raw source/debug records leak into list DTO helpers |
| `embedded-aggregate-payload` | 3 | P0 | List/board rows embed related aggregate payloads |
| `camel-snake-duplicate` | 130 | P1 | Both casing variants are returned in the same DTO |
| `project-before-page` | 5 | P1 | Broad projection happens before filters/page slicing |
| `heavy-row-helper` | 3 | P1 | Row helper includes detail-grade nested policy/session/memory/source data |

Top remaining clusters:

- Persona League and Quarterly Ranking family.
- Human Inbox, HiQ Backlog, Intervention Stream, Governance Ledger.
- Performance and Cost Attribution.
- Management AI audit/conversation surfaces.
- Residual casing duplicates in Strategy Allocation, Capital Flow, Risk Radar,
  Incident Timeline, Loop Throughput, and Performance Attribution row helpers.

## Mid-Audit Merge Note

During this re-audit, `MGMT-LIST-CONTRACT-006` merged as PR `#2762` at
`e2eb5ba90483ba2eeaf22c97f1465a7ee244eafa`. It retired the top-level list
aliases for:

- Strategy Allocation;
- Capital Flow;
- Risk Radar;
- Incident Timeline;
- Loop Throughput;
- Performance Attribution and its by-strategy/by-persona/by-pool routes.

That reduced the official clean-baseline audit from the earlier 200 issues to:

```text
issues=187 new=0 retired=13
```

The baseline file was updated by the merged remediation, so the current
guardrail result is `issues=187 new=0 retired=0`.

## Post-007 Remediation Note

`MGMT-LIST-CONTRACT-007` later normalized the Persona League and Quarterly
Ranking list-family envelopes, including the legacy `/bff/persona-league` list
helper. The current list-contract guardrail result after that slice is:

```text
source=services/control-plane/bff/main.py baseline=docs/architecture/management-list-contract-baseline.json issues=170 new=0 retired=0
```

`MGMT-LIST-CONTRACT-007B` also removes the shadowed legacy
`/bff/management/persona-league` decorator so the management route has exactly
one registered handler.

## Post-012 Remediation Note

Later remediation slices continued from this re-audit:

- `MGMT-LIST-CONTRACT-008` removed Cost Attribution top-level list aliases.
- `MGMT-LIST-CONTRACT-009` normalized NL/AI Management, Evolution Journal, and
  Persona Intent list envelopes.
- `MGMT-LIST-CONTRACT-010` cleared the remaining P0 list-contract cluster:
  duplicate envelopes, embedded child aggregates, and raw source records in
  Human Inbox, Evidence Explorer, HIQ Backlog, Intervention Stream, Governance
  Ledger, and Sentinel Pulse helpers.
- `MGMT-LIST-CONTRACT-011` expanded the static guard to `_build_management_*`
  builders and removed newly visible builder-level duplicate envelopes and
  casing mirrors in Trading Pulse, Sentinel Pulse, Cockpit, Anomalies, EP5
  readiness links, and Evidence Explorer.
- `MGMT-LIST-CONTRACT-012` removed the first Human/Ops P1 wire-casing cluster
  from HIQ Backlog, Intervention Stream, and Governance Ledger rows/summaries.
- `MGMT-LIST-CONTRACT-013` removed the remaining Human Inbox readiness blocker
  and summary wire-casing mirrors.
- `MGMT-LIST-CONTRACT-014` removed Evidence Explorer public item, summary,
  facet, and degraded-envelope wire-casing mirrors, including focused typed
  frontend consumers and fixtures.
- `MGMT-LIST-CONTRACT-015` removed PM12 quarterly ranking formula/window,
  governance evidence, ranking summary, formula summary, and recommendation
  summary wire-casing mirrors.
- `MGMT-LIST-CONTRACT-016` hardened frontend live transport mode/base URL and
  strict fallback handling so live-mode tests prove they call the configured
  BFF URL instead of silently returning mock data.
- `MGMT-LIST-CONTRACT-017` removed PM12 quarterly ranking row, drilldown,
  recommendation row, governance payload, and HumanGate command fixture
  wire-casing mirrors; drilldown source breakdowns now expose lightweight
  counts/summaries instead of nested capability/session/memory helper payloads.
- `MGMT-LIST-CONTRACT-018` removed PM12 performance attribution metrics, row,
  source-ref, summary, and typed-contract wire-casing mirrors; moved row DTO
  projection behind page slicing; and re-aligned PM12 persona-league DTOs and
  focused tests to snake_case-only league rows, ranking rows, movers, tiers,
  heatmap cells, and quarterly score fields without detail-grade row helper
  payloads.

The current list-contract guardrail result after `MGMT-LIST-CONTRACT-018` is:

```text
source=services/control-plane/bff/main.py baseline=docs/architecture/management-list-contract-baseline.json issues=65 new=0 retired=0
```

Current remaining categories:

| Category | Count |
|---|---:|
| `camel-snake-duplicate` | 61 |
| `project-before-page` | 4 |
| `heavy-row-helper` | 0 |

All P0 categories are now zero in the Management list-contract audit. The next
cleanup work is P1-only: remaining casing duplicates plus four
project-before-page fixes.

## Validation

Frontend validation after installing `execute-plans` dependencies in the clean
worktree:

| Command | Result |
|---|---|
| `npm ci` | Passed; npm reported existing dependency audit warnings. |
| `npm run build:management` | Passed; production bundle built successfully. |
| `npm run test -- --run src/management/components/live-evidence src/management/components/loop-truth` | Passed: 2 files, 3 tests. |
| `npm run test -- --run src/management/components src/lib/bff/__tests__/client.test.ts` | Failed: existing frontend test drift. `OodaPacketDrawer.test.tsx` cannot resolve `@/i18n`; `client.test.ts` still assumes non-empty mock seeds and live fetch behavior without consistently setting `VITE_BFF_MODE=live`. |

BFF focused validation:

```sh
python3 -m pytest \
  services/control-plane/bff/test_management_list_contract_guardrail.py \
  services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py \
  services/control-plane/bff/test_bff_management_delta_routes.py \
  -q
```

Result:

```text
70 passed
```

Additional BFF validation:

| Command | Result |
|---|---|
| `python3 -m py_compile services/control-plane/bff/main.py` | Passed |
| `python3 scripts/audit_management_list_contract.py --baseline docs/architecture/management-list-contract-baseline.json --fail-on-new --format summary` | Passed: `issues=65 new=0 retired=0` |
| `python3 -m pytest services/control-plane/bff/tests/test_bff_pm12_persona_league.py -q` | Passed: 14 tests |

## What Should Be Adjusted

| Area | Adjustment |
|---|---|
| BFF list contract | Continue the `MGMT-LIST-CONTRACT-*` burn-down until all Management list routes use only `data.items`, `data.summary`, `page_info`, and `meta`. |
| Frontend shell | Decide whether the current three-panel management entry is the intended shell. If not, build a real route shell intentionally instead of letting API inventory masquerade as UI pages. |
| Layer separation | Treat mounted UI, typed fetchers, BFF routes, and historical hosted routes as separate inventories. Do not count typed fetchers as finished pages. |
| Adapter layer | Keep compatibility adapters temporarily, but new UI should consume one canonical envelope and one wire casing. |
| IA | Group endpoints into operator workflows: Evidence/Truth, Decision Inbox, Performance Review, Readiness Gates, Persona Ranking, AI Ops. Do not expose one first-level page per endpoint. |
| Human Inbox | Fix the remaining project-before-page helper and keep list rows slim; expansion belongs to detail routes. |
| Governance Ledger | Keep the canonical envelope and make source coverage/page ordering deterministic for tests and operators. |
| AI Audit | Add real paging/limit behavior and remove the remaining AI/NL casing mirrors. |
| Readiness routes | Keep as release-gate surfaces, but present them inside a readiness workflow rather than as repeated first-level pages. |
| Persona League/Quarterly Ranking | Keep as domain views inside one PM12 performance suite; the focused contract is now snake_case-only. |
| Performance/Cost Attribution | Performance Attribution is remediated in `MGMT-LIST-CONTRACT-018`; continue Cost Attribution casing and page-before-projection cleanup. |

## What Should Be Deleted, Hidden, Or Not Built

| Surface | Recommendation |
|---|---|
| Duplicate `/bff/management/persona-league` list declaration | Done: the duplicate route is gone. Keep this as an invariant rather than a new deletion task. |
| `apps/management` isolated screens | Delete/archive if no active import owner exists; otherwise migrate deliberately into `execute-plans` with route tests. |
| One-page-per-fetcher UI expansion | Do not build every `fetchManagement*` function into a page. Collapse into fewer workflows. |
| Top-level compatibility aliases | Remove after frontend consumers migrate. Do not preserve aliases forever in payloads. |
| Seed/mock capability details | Do not surface seed ids for Tools/MCP/Skills or similar capability pages as production details. |
| Unproven write-like controls | Hide or disable until they return command id, receipt, audit ref, dry-run evidence, or explicit non-production state. |

## What Needs Deep Development

| Workstream | Deep development required |
|---|---|
| Real Management shell | If the product needs more than the current three panels, build a route shell with explicit nav, route ownership, per-workflow acceptance, and domain-specific components. |
| Decision workbench | Merge Human Inbox, Sentinel/Intervention, Approvals, and Governance Ledger into one coherent operator queue with detail drawers and receipts. |
| Performance suite | Keep Portfolio Book, Performance Attribution, Cost Attribution, Persona League, and Quarterly Ranking, but make them one suite with drilldowns instead of unrelated generic tables. |
| Command truth | Every action-like control must go through governed command/receipt/audit flow or be visibly disabled. |
| Payload budgets | Add route-level payload assertions for large list routes, especially Human Inbox, AI Audit, and Governance Ledger. |
| Hosted acceptance | Restore browser-level route/control evidence after the repo has installable frontend dependencies, including mock/unavailable/undefined/NaN detection. |
| Test contract cleanup | Keep PM12 tests on snake_case-only contracts, fix Governance Ledger source/page determinism, and keep list-contract guardrails in CI. |

## Priority Order

1. Done in `MGMT-LIST-CONTRACT-007`: normalize Persona League and Quarterly
   Ranking list envelopes.
2. Done in `MGMT-LIST-CONTRACT-008`: normalize Cost Attribution list aliases.
3. Done in `MGMT-LIST-CONTRACT-009`: normalize NL/AI Management, Evolution
   Journal, and Persona Intent list envelopes.
4. Done in `MGMT-LIST-CONTRACT-010`: clear remaining P0 list-contract findings
   in Human/Ops, Evidence, and Sentinel list helpers.
5. Done in `MGMT-LIST-CONTRACT-011`: audit `_build_management_*` builders and
   remove newly visible builder-level list-contract smells.
6. Done in `MGMT-LIST-CONTRACT-012`: remove HIQ Backlog, Intervention Stream,
   and Governance Ledger camel/snake wire-key mirrors.
7. Done in `MGMT-LIST-CONTRACT-013`: remove Human Inbox readiness/summary
   camel/snake wire-key mirrors.
8. Done in `MGMT-LIST-CONTRACT-014`: remove Evidence Explorer item, summary,
   facet, and degraded-envelope camel/snake wire-key mirrors.
9. Done in `MGMT-LIST-CONTRACT-015`: remove PM12 quarterly ranking
   formula/window, governance evidence, ranking summary, formula summary, and
   recommendation outer-summary camel/snake wire-key mirrors.
10. Done in `MGMT-LIST-CONTRACT-016`: harden frontend live transport mode/base
   URL and strict fallback handling.
11. Done in `MGMT-LIST-CONTRACT-017`: remove PM12 quarterly ranking row,
   drilldown, recommendation row, governance payload, typed contract, and
   HumanGate command fixture camel/snake wire-key mirrors; slim drilldown
   source breakdowns to summaries/counts.
12. Done in `MGMT-LIST-CONTRACT-018`: remove PM12 performance attribution
   metrics, row, source-ref, summary, and typed-contract camel/snake wire-key
   mirrors; page group entries before row DTO projection; keep PM12
   persona-league row, ranking, mover, tier, heatmap, quarterly score-field,
   typed-contract, and focused-test DTOs snake_case-only.
13. Continue casing cleanup in Management AI/NL, Strategy Allocation, Capital
   Flow, Risk Radar, Incident Timeline, Loop Throughput, and Cost Attribution.
14. Fix the four remaining project-before-page helpers: Human Inbox, Cost
   Attribution, Portfolio Exposure, and Portfolio Holdings.
15. Decide the frontend product shape: keep the current three-panel shell, or
   deliberately build a smaller workflow-based Management router.
16. Add payload-size and route-smoke acceptance evidence before exposing more
   first-level management pages.

## Bottom Line

The previous read was partial because it did not sufficiently separate:

- historical hosted FE route inventory;
- current repo-mounted Management UI;
- typed frontend BFF fetchers;
- backend Management API surfaces.

The current repo does not actually mount dozens of Management pages. It mounts a
thin shell while the BFF/API layer exposes dozens of management surfaces. The
right fix is not to delete useful operator viewpoints blindly. The right fix is
to canonicalize the list contracts, delete true duplicate/legacy declarations,
avoid building one UI page per endpoint, and then deepen a smaller set of
operator workflows with real payload budgets, command receipts, and hosted
acceptance.
