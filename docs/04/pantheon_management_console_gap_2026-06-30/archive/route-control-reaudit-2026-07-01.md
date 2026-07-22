# Management Console Route And Control Re-Audit - 2026-07-01

| Field | Value |
|---|---|
| Status | Archived supplemental re-audit evidence |
| Re-audit type | Built local FE preview, route/control crawl, source-scan cross-check |
| FE checkout | `/home/lupin/code/execute-plans-mgmt-gap-004-receipts` |
| FE branch | `task/mgmt-gap-004-command-receipts-2` |
| Build command | `VITE_BFF_MODE=mock npm run build` |
| Preview URL | `http://127.0.0.1:4175/` |
| Raw artifact | `route-control-reaudit-2026-07-01.json` |
| Prior artifact discarded | `/tmp/pantheon-management-reaudit-fast-20260701.json` was discarded because it came from an incomplete/empty dist and blank pages |
| Related archive | `full-reaudit-addendum-2026-07-01.md` |

## 1. Why This Supplemental Audit Exists

The earlier 2026-07-01 addendum was correct, but it emphasized hosted route and
live-id detail proof. This supplemental pass answers the user's second concern:
whether the console has too many repeated management pages and controls that
only look production-grade.

This pass intentionally counted routes, controls, disabled actions, mock-visible
surfaces, duplicate aliases, and high-density detail panels. It also cross-checked
the source for write helper patterns such as `runActionSafe`, `bffWrites`,
`NonProductionActionButton`, `toast.success`, and `writeOverlay`.

## 2. Audit Caveats

This was a localhost preview crawl after a clean production build. It is valid
for render/control inventory and source-path classification. It is not a
substitute for hosted strict-live production proof.

Known caveats:

- the hosted BFF CORS policy rejects localhost origins for some assistant/LLM
  provider reads, so localhost CORS failures are evidence that the harness must
  run on the hosted FE origin, not proof that the hosted page is broken;
- pages were navigated and safe controls such as tabs/search/selects were
  inspected, but high-risk write CTAs were recorded rather than executed;
- mock mode was used for this crawl to force full route coverage after the
  command-receipt branch build; mock-visible routes are therefore a production
  classification input, not a hosted-live closeout.

## 3. Build And Bundle Evidence

`VITE_BFF_MODE=mock npm run build` passed.

Build risks carried into `MGMT-GAP-010` and `MGMT-LOAD-*`:

- CSS minify warning: generated CSS reported `Expected identifier but found "-"`
  and needs release-gate visibility.
- Realtime module is both statically and dynamically imported, reducing the
  value of route-level code splitting until corrected.
- The main management bundle remained very large:
  `assets/index-LvKjPFMP.js` was about 5.5 MB before gzip, with several chunks
  still above the 500 kB warning threshold.

## 4. Route And Control Inventory

| Signal | Count |
|---|---:|
| Route samples crawled | 93 |
| Visible nav routes | 53 |
| Detail, hidden, or alias samples | 40 |
| Routes without crawler problems | 92 |
| Problem routes | 6 |
| Mock-visible routes | 10 |
| Buttons | 510 |
| Enabled buttons | 468 |
| Disabled buttons | 42 |
| Links | 386 |
| Inputs | 47 |
| Textareas | 1 |
| Selects | 11 |

The repetition is real, but it is not one single problem. It falls into four
different categories:

1. valid operator viewpoints that share a scaffold and should stay;
2. route aliases that correctly redirect and can stay as compatibility;
3. detail aliases that still render directly and should become redirects or one
   canonical mapper;
4. capability/studio/mock surfaces that should be demoted, hidden, or deeply
   developed before being first-class operations pages.

## 5. Problem Routes

| Route | Finding | Task owner |
|---|---|---|
| `/management/cockpit` | The crawler saw an empty `main` text extraction even though headings/buttons rendered; localhost preview also hit LLM Provider Auth CORS/fetch failures. Needs hosted-origin harness proof and graceful degraded copy. | `MGMT-GAP-006`, with auth/session input to `MGMT-GAP-009` |
| `/management/persona-fleet` | Renders an honest no-live-data state, but logs `Persona Fleet requires live BFF data; demo fallback is disabled`. This is acceptable fail-honesty, but the harness should distinguish expected degraded truth from console error noise. | `MGMT-GAP-006` |
| `/management/control-room` | Correctly redirects to `/management/cockpit`; inherits cockpit LLM Provider Auth/CORS noise in localhost preview. | `MGMT-GAP-006` |
| `/management/one-ring` | Correctly redirects to `/management/cockpit`; inherits cockpit LLM Provider Auth/CORS noise. | `MGMT-GAP-006` |
| `/management/overview` | Correctly redirects to `/management/cockpit`; inherits cockpit LLM Provider Auth/CORS noise. | `MGMT-GAP-006` |
| `/management/command-center` | Correctly redirects to `/management/cockpit`; inherits cockpit LLM Provider Auth/CORS noise. | `MGMT-GAP-006` |

## 6. Aliases: Keep Redirects, Remove Duplicate Direct Renders

Compatibility redirects observed as correct:

| Alias | Final path |
|---|---|
| `/management/control-room` | `/management/cockpit` |
| `/management/one-ring` | `/management/cockpit` |
| `/management/overview` | `/management/cockpit` |
| `/management/command-center` | `/management/cockpit` |
| `/management/risk-center` | `/management/risk` |
| `/management/capital-pools` | `/management/capital` |
| `/management/ranking-formulas` | `/management/ranking/formulas` |
| `/management/rebalances` | `/management/rebalance` |
| `/management/research` | `/management/experiments` |
| `/management/deployment` | `/management/deployments` |

Detail aliases that still render directly:

| Direct-render alias | Required production behavior | Task |
|---|---|---|
| `/management/capital-pools/cp_alpha` | Redirect to `/management/capital/cp_alpha` or prove one canonical DTO mapper. | `MGMT-GAP-008` |
| `/management/ranking-formulas/rf_001` | Redirect to `/management/ranking/formulas/rf_001` or prove one canonical DTO mapper. | `MGMT-GAP-008` |
| `/management/rebalances/rb_q2_2026` | Redirect to `/management/rebalance/rb_q2_2026` or prove one canonical DTO mapper. | `MGMT-GAP-008` |
| `/management/research/rx_201` | Redirect to `/management/experiments/rx_201` or prove one canonical DTO mapper. | `MGMT-GAP-008` |

## 7. Mock-Visible And Demotion Candidates

| Route | Finding | Decision |
|---|---|---|
| `/management/evidence` | Mock-visible readiness packet remains useful as proof UX, but cannot be production evidence. | Keep, label source truth; require hosted evidence resolver closeout. |
| `/management/alpha-factory` | Explicit mock/configured mock lane. | Demote until real backend discovery/scaffold commands exist. |
| `/management/loops/execution` | Mentions `v0-mock` timeout policy. | Keep reachable only if labeled as non-production or backed by live loop data. |
| `/management/studios/formula` | Already removed from first-level nav by `MGMT-GAP-001`, but still needs runner-backed proof if kept routable. | `MGMT-GAP-005`. |
| `/management/studios/skill-sandbox` | Same as Formula Studio. | `MGMT-GAP-005`. |
| `/management/evidence/evref-demo-readiness-001` | Demo evidence detail remains routable. | Hide demo ids from production routes or keep only in explicit fixture/test mode. |
| `/management/evidence?ref_id=evref-demo-readiness-001` | Same demo evidence path through query param. | Same as above. |
| `/management/strategies/stg_001` | Seed strategy detail with many command-like controls. | Use only as mock fixture; live-id detail and command receipts required. |
| `/management/capital/cp_alpha` | Seed capital detail with many command-like controls. | Use only as mock fixture; live-id detail and alias closeout required. |
| `/management/rebalance/rb_q2_2026` | Seed rebalance detail with command-like controls. | Use only as mock fixture; live-id detail and command receipt proof required. |
| `/management/capital-pools/cp_alpha` | Seed detail alias direct-render. | Redirect or canonicalize under `MGMT-GAP-008`. |
| `/management/rebalances/rb_q2_2026` | Seed detail alias direct-render. | Redirect or canonicalize under `MGMT-GAP-008`. |

## 8. High-Density Control Hotspots

Highest button-count routes from the crawl:

| Route | Buttons | Disabled | Production interpretation |
|---|---:|---:|---|
| `/management/sentinel` | 37 | 0 | Keep as operator workbench, but command-like controls must be receipt-backed. |
| `/management/ranking` | 35 | 0 | Deep command work; formula activation/recalc/freeze/publish need receipt proof. |
| `/management/governance/policies/rp_quant_v2` | 34 | 27 | Good disabled posture, but needs reviewed command policy for enabled controls. |
| `/management/strategies/stg_001` | 25 | 0 | Seed/mock detail with many action chips; must not be production-success without receipts. |
| `/management/capital/cp_alpha` | 21 | 0 | Same, plus alias direct-render risk. |
| `/management/evolution/ev_001` | 21 | 0 | Lifecycle/freeze/promote actions need command receipts. |
| `/management/rebalance/rb_q2_2026` | 18 | 0 | Approval/cancel/report actions need receipt proof. |
| `/management/mcp/mcp_alpha` | 18 | 0 | Capability lifecycle actions need runner/registry command truth. |
| `/management/tools/tl_market_data` | 15 | 2 | Some actions disabled; remaining lifecycle controls need receipt proof. |
| `/management/skills/sk_macro_brief` | 14 | 0 | Skill publish/test/evaluate controls need runner trace or disablement. |

Disabled controls are not automatically bad. They are evidence of correct
non-production gating when paired with a clear reason. The production problem is
enabled controls that complete through local state, seed overlays, or toast-only
success.

## 9. Disabled Control Inventory

| Route | Disabled controls | Task |
|---|---|---|
| `/management/governance/policies/rp_quant_v2` | `重設`, `送審`, environment toggles, delete buttons | `MGMT-GAP-004` |
| `/management/governance/consult` | create and submit controls | `MGMT-GAP-004` |
| `/management/tools/tl_market_data` | rate-limit and risk classification controls | `MGMT-GAP-004`, `MGMT-GAP-005` |
| `/management/tools` | create | `MGMT-GAP-005` |
| `/management/mcp` | create | `MGMT-GAP-005` |
| `/management/skills` | create | `MGMT-GAP-005` |
| `/management/workflows` | new template | `MGMT-GAP-004` |
| `/management/hooks` | new rule | `MGMT-GAP-004` |
| `/management/channels` | create | `MGMT-GAP-004` |
| `/management/llm-provider-auth` | refresh provider auth | `MGMT-GAP-009`, `MGMT-GAP-006` |
| `/management/settings` | save | `MGMT-GAP-004` |
| `/management/incidents/in_021` | pause affected strategy | `MGMT-GAP-004` |
| `/management/channels/ch_slack_alerts` | send test | `MGMT-GAP-004` |

## 10. Source Scan Cross-Check

The route crawl was cross-checked against source patterns:

- `runActionSafe` appears in 16 management files, including strategy, ranking,
  rebalance, capital pool, operations, evolution, deployments, MCP, tools,
  research, and skill detail surfaces.
- `bffWrites` appears in strategy detail, governance queue, operations, and
  governance review.
- `NonProductionActionButton` is widespread and should remain the preferred
  disabled-state wrapper until a governed endpoint, command id, audit receipt,
  and dry-run/no-side-effect proof exist.
- `toast.success` still appears in governance, operations, incident, persona,
  strategy, artifact rollback, rebalance workflow, freeze/unfreeze, promotion,
  allocation limits, overrides, evolution freeze, MCP secrets, and metric freeze
  flows. Each success toast must be backed by receipt proof or converted to a
  non-production disabled state.
- `src/lib/bff-v1/writeFallback.ts` explicitly says BFF write endpoints are not
  yet verified live. That file is not production proof.
- `src/management/components/write/createEntity.ts` writes non-persona creates
  to `writeOverlay`; persona create tries BFF and falls back to overlay on not
  implemented; non-persona delete soft-deletes to overlay. This is not durable
  production persistence.

## 11. Adjust, Delete/Hide, Deep Develop

### Adjust

| Area | Required adjustment | Owner task |
|---|---|---|
| Canonical detail routes | Stop direct-rendering detail aliases; redirect or prove one mapper. | `MGMT-GAP-008` |
| Cockpit/LLM provider auth | Hosted harness must prove provider auth degraded states without localhost CORS false positives. | `MGMT-GAP-006`, `MGMT-GAP-009` |
| Registry pages | Keep the scaffold, but add domain-specific truth columns, explicit empty/degraded badges, and no seed-id leakage. | `MGMT-GAP-008`, `MGMT-GAP-005` |
| Overlay create/delete boundaries | Label overlay paths as non-production; route durable creates/deletes through governed commands only. | `MGMT-GAP-004` |
| Build/load evidence | Carry bundle warnings and route-ready proof into release gates. | `MGMT-GAP-010`, `MGMT-LOAD-*` |

### Delete, Hide, Or Demote

| Surface | Recommendation | Owner task |
|---|---|---|
| Demo evidence ids | Hide from production routes or fixture-gate them. | `MGMT-GAP-006`, `MGMT-GAP-008` |
| Alpha Factory mock lane | Demote until backend discovery/scaffold command truth exists. | `MGMT-GAP-005` |
| Formula Studio | Keep hidden/demoted until real backtest job/readback exists. | `MGMT-GAP-005` |
| Skill Sandbox | Keep hidden/demoted until real skill-runner trace exists. | `MGMT-GAP-005` |
| Settings break-glass controls | Disable/hide until governed command/audit receipt exists. | `MGMT-GAP-004` |
| Empty Tools/MCP/Skills detail seed ids | Do not surface as production capability detail pages. | `MGMT-GAP-005`, `MGMT-GAP-008` |
| Old detail aliases | Keep bookmarks as redirects only; do not duplicate component rendering. | `MGMT-GAP-008` |

### Deep Develop

| Area | Deep work required | Owner task |
|---|---|---|
| Command receipts | Burn down every write-like CTA with command id, receipt, audit id, dry-run proof, or explicit disabled state. | `MGMT-GAP-004` |
| Capability runners | Real Formula Studio backtest, Skill Sandbox runner, Tools/MCP/Skills registry command contracts, or demotion. | `MGMT-GAP-005` |
| Detail honesty | Live-id probes must fail on `undefined`, `NaN`, blank headings/owners/updates, not-found seed ids, and alias drift. | `MGMT-GAP-008` |
| Session/RBAC | `/bff/me`, tenant, roles, provider auth, and management data reads must agree under the documented token path. | `MGMT-GAP-009` |
| Production harness | Hosted 93-route-style crawl with endpoint capture, console-error classification, mock/write detection, and route-ready/load evidence. | `MGMT-GAP-006`, `MGMT-GAP-010` |
| Final closeout | Track every task to PR, merge SHA, deploy SHA, hosted probe artifact, and residual-risk owner. | `MGMT-GAP-007` |

## 12. Updated Production Judgment

The management console should not be deleted wholesale. Most pages represent
real operator viewpoints. The production-level gap is that too many of those
viewpoints share generic scaffolds, seed/mock detail ids, and enabled-looking
controls before the underlying command/readback contracts are proven.

Concrete outcome:

- keep oversight, readiness, performance, decision, registry, operations, and
  governance viewpoints;
- adjust detail aliases, degraded/session handling, empty registries, and route
  harness evidence;
- hide or demote mock studios, demo evidence ids, alpha-factory mock lanes, and
  break-glass controls until backed by real runners/commands;
- deeply develop command receipts, capability runners, detail DTO honesty,
  session/RBAC, load gates, and final hosted production acceptance.
