# BFF-CONSOL-007 Seed Taxonomy

Task: `BFF-CONSOL-007`
Source inspected: `../execute-plans/src/lib/bff-v1/seed.ts`
Seed data source: `../execute-plans/src/mocks/seed.ts`

The task brief names `execute-plans/src/lib/bff/seed.ts`, but the pantheon
checkout has no `execute-plans/` directory and the sibling frontend checkout has
no `src/lib/bff/seed.ts`. The public seed accessor currently lives at
`src/lib/bff-v1/seed.ts`; `src/lib/bff/client.ts` and `src/lib/bff-v1/legacy.ts`
bridge it into the live/mock migration surface.

## Category Rules

| Category | Meaning |
| --- | --- |
| `live_required` | Operator-facing data, session, command, or detail surface that must use a live BFF route in live/strict mode. Seed is allowed only in mock mode or visible hybrid fallback. |
| `mock_only_dev` | Development-only helper. In live mode it should be hidden, disabled, or shown as an explicit mock/empty state. |
| `deprecated` | Legacy public accessor that should be replaced by the typed live surface or command client, then removed from `seed.ts`. |
| `deferred` | No safe direct live replacement in this task. The listed follow-up must wire a route, fold it into an existing detail DTO, or remove the UI surface. |

## Summary

| Category | Count | Main downstream consumer |
| --- | ---: | --- |
| `live_required` | 52 | `BFF-CONSOL-016`, `017`, `018`, `025` |
| `mock_only_dev` | 4 | `BFF-CONSOL-015`, `025` |
| `deprecated` | 2 | `BFF-CONSOL-019`, `020`, `021`, `024`, `025` |
| `deferred` | 25 | `BFF-CONSOL-028` follow-up after `BFF-CONSOL-025` gating |

JSON source of truth for scripts: `docs/bff/seed-taxonomy.json`.

## Immediate Elimination Order

| Priority | Helpers | Reason |
| --- | --- | --- |
| P0 | `bff.mutations`, `bff.commands.requestConfirmToken`, `bff.me.*`, core Management list/detail helpers, `bff.search`, `bff.mcpSecrets.forServer` | Command/session/security and global navigation surfaces must not silently seed. |
| P1 | Agora/v5 namespaces, route-policy/memory/evolution-run/decision-journal/deployment-stage/rebalance-workflow helpers | Existing BFF routes or parent DTOs can replace the seed path; detail smokes should prove it. |
| P2 | Governance/evolution/capital adjunct helpers with no direct route | `BFF-CONSOL-028` must either add route contracts, fold into detail DTOs, or remove/hide strict-live UI. |
| P3 | `getAcceptLanguage`, watcher chips | Local instrumentation/collaboration affordances; hide in live mode if not backed by real truth. |

## Helper Matrix

| Helper | Category | Live route / replacement | Follow-up |
| --- | --- | --- | --- |
| `bff.getAcceptLanguage` | `mock_only_dev` | Request headers from `bffFetch`; keep only as Settings/test instrumentation. | `015` |
| `bff.mutations` | `deprecated` | Replace direct callers with `bffWrites` / `/bff/v1/commands`; old `/bff/actions/*` remains adapter-backed during soak. | `019`, `020`, `021`, `024` |
| `bff.agora` | `live_required` | `GET /bff/agora/daily`, `/signals`, `/signals/{id}`, `/inbox`, `/journal`, `/ask/sessions`; keep `bffAgora` strict-live adapter. | `017`, `025` |
| `bff.v5` | `live_required` | `/bff/v5/control-room`, `/loop-runs`, `/sentinel/findings`, `/interventions`, `/execution/*-health`; keep strict-live adapter. | `017`, `025` |
| `bff.me.get` | `live_required` | `GET /bff/me`; mockMe only in mock mode. | `013` |
| `bff.me.refresh` | `live_required` | `POST /bff/auth/refresh`. | `013` |
| `bff.me.logout` | `live_required` | `POST /bff/logout`. | `013` |
| `bff.me.invalidate` | `live_required` | Local cache invalidation for live `/bff/me`; not seed data. | `013` |
| `bff.commands.requestConfirmToken` | `deprecated` | Use `bffWrites.requestConfirmToken` / command client. | `020`, `021`, `024` |
| `bff.strategies.list` | `live_required` | `GET /bff/strategies`; `managementClient.strategies.list`. | `016`, `025` |
| `bff.strategies.get` | `live_required` | `GET /bff/strategies/{id}`; includes specs/experiments/artifacts/lineage/audit journey. | `016`, `025` |
| `bff.personas.list` | `live_required` | `GET /bff/personas`; `managementClient.personas.list`. | `016`, `025` |
| `bff.personas.get` | `live_required` | `GET /bff/personas/{id}`; route-policy/activity/evaluations/memory tabs must be live. | `016`, `025` |
| `bff.capitalPools.list` | `live_required` | `GET /bff/capital-pools`. | `008`, `025` |
| `bff.capitalPools.get` | `live_required` | `GET /bff/capital-pools/{id}`. | `008`, `025` |
| `bff.rankingFormulas.list` | `live_required` | `GET /bff/ranking-formulas` alias backed by ranking routes. | `025` |
| `bff.rankingFormulas.get` | `live_required` | `GET /bff/ranking-formulas/{id}`. | `025` |
| `bff.rebalances.list` | `live_required` | `GET /bff/rebalances`. | `018`, `025` |
| `bff.rebalances.get` | `live_required` | `GET /bff/rebalances/{id}`. | `018`, `025` |
| `bff.deployments.list` | `live_required` | `GET /bff/deployments`. | `016`, `025` |
| `bff.deployments.get` | `live_required` | `GET /bff/deployments/{id}`; stages/approval pointer should come from live DTO. | `016`, `025` |
| `bff.evolution.list` | `live_required` | `GET /bff/evolution-programs`. | `017`, `025` |
| `bff.evolution.get` | `live_required` | `GET /bff/evolution-programs/{id}`. | `017`, `025` |
| `bff.research.list` | `live_required` | `GET /bff/research-experiments`. | `017`, `025` |
| `bff.research.get` | `live_required` | `GET /bff/research-experiments/{id}`. | `017`, `025` |
| `bff.artifacts.list` | `live_required` | `GET /bff/artifacts`. | `017`, `025` |
| `bff.artifacts.get` | `live_required` | `GET /bff/artifacts/{id}`. | `017`, `025` |
| `bff.jobs.list` | `live_required` | `GET /bff/jobs`; `jobs.get` gap is outside `seed.ts` and belongs to detail smoke C. | `018`, `025` |
| `bff.runtimes.list` | `live_required` | `GET /bff/runtimes`. | `016`, `025` |
| `bff.runtimes.get` | `live_required` | `GET /bff/runtimes/{id}`. | `016`, `025` |
| `bff.alerts.list` | `live_required` | `GET /bff/alerts`. | `010`, `025` |
| `bff.alerts.get` | `live_required` | `GET /bff/alerts/{id}`. | `010`, `025` |
| `bff.incidents.list` | `live_required` | `GET /bff/incidents`. | `018`, `025` |
| `bff.incidents.get` | `live_required` | `GET /bff/incidents/{id}`. | `018`, `025` |
| `bff.approvals.list` | `live_required` | `GET /bff/approvals`. | `018`, `025` |
| `bff.approvals.get` | `live_required` | `GET /bff/approvals/{id}`. | `018`, `025` |
| `bff.audit.list` | `live_required` | `GET /bff/audit`; audit remains list-only and detail drawer should be disabled. | `018`, `025` |
| `bff.tools.list` | `live_required` | `GET /bff/tools`. | `010`, `025` |
| `bff.tools.get` | `live_required` | `GET /bff/tools/{id}`. | `010`, `025` |
| `bff.mcpServers.list` | `live_required` | `GET /bff/mcp-servers`. | `010`, `025` |
| `bff.mcpServers.get` | `live_required` | `GET /bff/mcp-servers/{id}`. | `010`, `025` |
| `bff.mcpTools.list` | `live_required` | `GET /bff/mcp-tools`. | `010`, `025` |
| `bff.mcpTools.get` | `live_required` | `GET /bff/mcp-tools/{id}`. | `010`, `025` |
| `bff.skills.list` | `live_required` | `GET /bff/skills`. | `010`, `025` |
| `bff.skills.get` | `live_required` | `GET /bff/skills/{id}`. | `010`, `025` |
| `bff.channels.list` | `live_required` | `GET /bff/channels`. | `010`, `025` |
| `bff.channels.get` | `live_required` | `GET /bff/channels/{id}`. | `010`, `025` |
| `bff.routePolicies.list` | `deferred` | No standalone route; use persona-scoped route or add a route-policy list route. | `028` |
| `bff.routePolicies.get` | `deferred` | No policy-id route; fold into persona route-policy detail or add canonical route. | `028` |
| `bff.routePolicies.forPersona` | `live_required` | `GET /bff/personas/{id}/route-policy`. | `016`, `025` |
| `bff.policyVersions.list` | `deferred` | Add route-policy version DTO/route or remove strict-live display. | `028` |
| `bff.permissionMatrix.get` | `deferred` | Add permissions/capabilities route or hide matrix in strict live. | `028` |
| `bff.permissionMatrices.list` | `deferred` | Add permissions/capabilities route or hide matrix list in strict live. | `028` |
| `bff.memoryUpdates.list` | `deferred` | No global memory-updates route; use persona-scoped memory where possible. | `028` |
| `bff.memoryUpdates.forPersona` | `live_required` | `GET /bff/personas/{id}/memory`. | `016`, `025` |
| `bff.consultRules.list` | `deferred` | API v1 consultation routes exist, but no matching `/bff` consultRules route. | `028` |
| `bff.consultRules.get` | `deferred` | Add `/bff` consult-policy detail or remove strict-live seed display. | `028` |
| `bff.evolutionRuns.list` | `deferred` | No global route; backend exposes program-scoped runs. | `028` |
| `bff.evolutionRuns.forProgram` | `live_required` | `GET /bff/evolution-programs/{id}/runs`. | `017`, `025` |
| `bff.evolutionCandidates.forRun` | `deferred` | Backend route is program-scoped candidates; helper contract is run-scoped. | `028` |
| `bff.fitnessFormulas.list` | `deferred` | No route; add evolution config route or hide studio in strict live. | `028` |
| `bff.fitnessFormulas.get` | `deferred` | No route; add detail route or remove seed fallback. | `028` |
| `bff.mutationRules.list` | `deferred` | No route; add mutation-rule route or hide studio in strict live. | `028` |
| `bff.allocationSimulations.forRebalance` | `mock_only_dev` | Mock simulator only; hide or show explicit mock state in live. | `015`, `025` |
| `bff.policyViolations.list` | `deferred` | Add policy-violation route or fold into detail DTOs. | `028` |
| `bff.policyViolations.forSubject` | `deferred` | Add subject route or fold into detail DTOs. | `028` |
| `bff.evaluationRuns.list` | `deferred` | No generic route; persona-scoped evaluations exist. | `028` |
| `bff.evaluationRuns.forSubject` | `deferred` | Split persona subject to `/bff/personas/{id}/evaluations`; disable unsupported subject kinds. | `028` |
| `bff.objectVersions.forSubject` | `deferred` | No generic version route; fold strategy/persona versions into detail tabs. | `028` |
| `bff.featureSets.forStrategy` | `deferred` | No route; add feature-set route or fold into strategy specs/detail. | `028` |
| `bff.performanceSeries.forStrategy` | `deferred` | Add strategy performance route or map to canonical telemetry route. | `028` |
| `bff.watchers.forSubject` | `mock_only_dev` | Hide watcher chips or add real collaboration/subscription route later. | `015`, `025` |
| `bff.decisionJournal.list` | `live_required` | Delegates to `GET /bff/agora/journal`. | `017`, `025` |
| `bff.decisionJournal.forSubject` | `live_required` | Delegates to live journal list, then filters by subject. | `017`, `025` |
| `bff.allocationLimits.forPool` | `deferred` | Fold into capital-pool detail or add pool-scoped route. | `028` |
| `bff.poolFreezes.forPool` | `deferred` | Fold into capital-pool detail or add pool-scoped route. | `028` |
| `bff.deploymentStages.forDeployment` | `live_required` | Read stages from `GET /bff/deployments/{id}` detail DTO. | `016`, `025` |
| `bff.mcpSecrets.forServer` | `mock_only_dev` | Hide in live unless a masked metadata route is added; never expose real secret values. | `015`, `025` |
| `bff.promotions.forProgram` | `deferred` | Add promotion-history route or fold into evolution program detail. | `028` |
| `bff.metricFreezes.forRebalance` | `deferred` | Fold into rebalance detail or add rebalance-scoped route. | `028` |
| `bff.rebalanceOverrides.forRebalance` | `deferred` | Fold into rebalance detail or add rebalance-scoped route. | `028` |
| `bff.rebalanceWorkflow.forRebalance` | `live_required` | Read workflow/status from `GET /bff/rebalances/{id}` or a subresource. | `018`, `025` |
| `bff.search` | `live_required` | `GET /bff/search`; strict live must not return `seed.searchableObjects()`. | `025` |

## Reviewer Notes

- Backend route availability was cross-checked against
  `services/control-plane/bff/contract_snapshots/backend_routes_manifest.json`
  and the BFF route diff baseline.
- Core Management Console list helpers already have live list adapters through
  `src/lib/bff-v1/lists.ts`; most remaining risk is legacy direct `bff.*`
  call sites and related-tab helpers that still read `@/mocks/seed`.
- `jobs.get` is not present in `seed.ts`; the live detail gap is still called
  out because `src/lib/bff/client.ts` currently supplies an `undefined` mock
  placeholder and `BFF-CONSOL-018` explicitly owns that behavior.
