# Management Console Complete Re-Audit - 2026-07-02

| Field | Value |
|---|---|
| Status | Complete re-audit after the 2026-07-01 addendum and the first six list-contract remediation slices |
| Re-audit basis | Clean worktree `/tmp/pantheon-mgmt-reaudit-20260702`, branch `task/mgmt-complete-reaudit-20260702` |
| Base commit | `e2eb5ba90483ba2eeaf22c97f1465a7ee244eafa` (`origin/dev`, PR `#2762`) |
| Scope | Current repo frontend entrypoints, Management BFF routes, list-contract audit, authenticated route smoke, focused validation |
| Important correction | The earlier audit blended hosted historical FE route inventory, BFF API inventory, and the current repo's mounted management UI. This pass keeps those layers separate. |

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

- `services/control-plane/bff/main.py` defines 57 `/bff/management*` route
  decorators, including duplicate route declarations.
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

### Management BFF Route Inventory

Static route extraction found 57 `/bff/management*` route decorators.

Important structural issues:

- `/bff/management/persona-league` is declared twice:
  - `bff_management_persona_league` near the PM12 ranking family;
  - `bff_persona_league` near the persona fleet family.
- Several families still return the same list under multiple names.
- Several endpoints are list-shaped but still include detail-grade rows,
  raw source records, or casing duplicates.

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

## Validation

Frontend validation attempted in the clean worktree:

| Command | Result |
|---|---|
| `npm run build:management` | Blocked: `execute-plans/node_modules` missing, `vite: not found` |
| `npm run test -- --run src/management/components src/lib/bff/__tests__/client.test.ts` | Blocked: `execute-plans/node_modules` missing, `vitest: not found` |

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
69 passed, 1 failed
```

Failure:

- `test_governance_ledger_unifies_approval_intervention_and_override_sources`
  expected the current governance-ledger page to include at least one
  `source_type == "intervention"` row.
- The route returned 200, but the sampled page did not include an intervention
  row.

Interpretation:

- The test mixes source-family coverage with current-page sampling.
- Governance Ledger needs either a deterministic fixture/page ordering contract
  or a test that checks the complete source set without relying on one page.

## What Should Be Adjusted

| Area | Adjustment |
|---|---|
| BFF list contract | Continue the `MGMT-LIST-CONTRACT-*` burn-down until all Management list routes use only `data.items`, `data.summary`, `page_info`, and `meta`. |
| Frontend shell | Decide whether the current three-panel management entry is the intended shell. If not, build a real route shell intentionally instead of letting API inventory masquerade as UI pages. |
| Adapter layer | Keep compatibility adapters temporarily, but new UI should consume one canonical envelope and one wire casing. |
| IA | Group endpoints into operator workflows: Evidence/Truth, Decision Inbox, Performance Review, Readiness Gates, Persona Ranking, AI Ops. Do not expose one first-level page per endpoint. |
| Human Inbox | Slim list DTOs, remove raw source/detail payloads from list rows, and move expansion to detail routes. |
| Governance Ledger | Normalize envelope, remove source-record leakage, and make source coverage/page ordering deterministic. |
| AI Audit | Add real paging/limit behavior and canonical list envelope. |
| Readiness routes | Keep as release-gate surfaces, but normalize envelope and avoid treating checks/items/evidence refs as multiple list roots. |
| Persona League/Quarterly Ranking | Keep as domain views, but split list rows, ranking detail, formula, and recommendation detail contracts. |
| Performance/Cost Attribution | The top-level aliases are now removed by `MGMT-LIST-CONTRACT-006`; continue with filter/page-before-projection and casing cleanup. |

## What Should Be Deleted, Hidden, Or Not Built

| Surface | Recommendation |
|---|---|
| Duplicate `/bff/management/persona-league` list declaration | Delete or rename the legacy duplicate route. Keep one canonical list route and one detail route. |
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
| Test contract cleanup | Fix Governance Ledger source/page determinism and keep list-contract guardrails in CI. |

## Priority Order

1. Normalize Persona League and Quarterly Ranking family.
2. Slim Human Inbox, HiQ Backlog, Intervention Stream, and Governance Ledger.
3. Add paging and envelope cleanup for Management AI audit/conversation
   surfaces.
4. Continue casing cleanup and filter/page-before-projection work for the PM12
   analytics helpers after `MGMT-LIST-CONTRACT-006`.
5. Decide the frontend product shape: keep the current three-panel shell, or
   deliberately build a smaller workflow-based Management router.
6. Add payload-size and route-smoke acceptance evidence before exposing more
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
