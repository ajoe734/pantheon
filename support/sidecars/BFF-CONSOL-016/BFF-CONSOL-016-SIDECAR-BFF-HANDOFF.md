# BFF-CONSOL-016 Sidecar: BFF and Frontend Handoff Packet

Task ID: BFF-CONSOL-016-SIDECAR-BFF-HANDOFF
Parent Task: BFF-CONSOL-016 - Detail journey smoke A (strategy persona deployment runtime)
Helper Kind: bff_handoff_packet
Prepared by: Codex
Reviewer: Codex2
Date: 2026-05-13
Mutates canonical truth: false

## Purpose

This packet gives the BFF-CONSOL-016 parent and final BFF consolidation owners a
support-only handoff for the Pack A detail journey smoke. It translates the
completed backend evidence into frontend absorption notes, operator journey
coverage, and remaining client-side gaps.

This document does not change L1 canonical truth, core contracts, runtime code,
registry code, or governance implementation.

## Current State Observed

| Area | Observation | Handoff impact |
|---|---|---|
| Parent task | `BFF-CONSOL-016` is archived as `done` with commit `6b59cbd25fc133300c87b6bab482d7dbf9f58330`. | Final acceptance can treat Pack A detail smoke A as completed, not pending. |
| Parent evidence | `support/evidence/BFF-CONSOL-016-detail-smoke-a.json` records strategy, persona, deployment, and runtime transcripts. | Use this as the primary read-path evidence for these four families. |
| Backend regression | `services/control-plane/bff/test_bff_consol_016_detail_smoke_a.py` verifies Pack A routes through FastAPI `TestClient`. | Backend route behavior is locally reproducible without changing runtime code. |
| Playwright smoke artifact | `execute-plans/tests/e2e/detail-smoke-a.spec.ts` records the intended live BFF API smoke against `BFF_BASE_URL`. | Frontend/live lanes can reuse the route list and Pack A fixture IDs for browser/API smoke. |
| Fixture dependency | BFF-CONSOL-008 Pack A supplies strategies, personas, capital pools, rebalances, and deployments. | These IDs must remain stable for downstream read-smoke and final acceptance references. |
| Route manifest snapshot | `services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json` is dated 2026-05-08 and still marks several Pack A routes as `missing`. | Treat that snapshot as stale for these rows; rerun the route diff/manifest before final acceptance. |

## Evidence Sources

| Evidence | Path | Use |
|---|---|---|
| Parent archive | `ai-task-archive/tasks/BFF-CONSOL-016.json` | Delivery status, commit, review notes, and verification summary. |
| Smoke evidence | `support/evidence/BFF-CONSOL-016-detail-smoke-a.json` | Per-family route transcript, fixture IDs, degraded-path evidence. |
| Backend regression | `services/control-plane/bff/test_bff_consol_016_detail_smoke_a.py` | Focused reproducible test for Pack A route behavior. |
| Pack A fixture regression | `services/control-plane/bff/test_bff_consol_008_fixture_pack_a.py` | Confirms fixture families are non-empty and linkable. |
| Playwright API smoke | `execute-plans/tests/e2e/detail-smoke-a.spec.ts` | Frontend-facing API smoke script and expected live route journey. |
| Backend route implementations | `services/control-plane/bff/main.py` | Actual route handlers for `/bff/strategies`, `/bff/personas`, `/bff/deployments`, and `/bff/runtimes`. |
| Frontend BFF client touchpoints | `/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts`, `/home/lupin/code/execute-plans/src/lib/bff-v1/seed.ts`, `/home/lupin/code/execute-plans/src/lib/bff/liveRead.ts` | Client path builders, strict live seed replacement behavior, and management read adapters. |

## BFF Query Coverage Confirmed

### Strategy detail family

| Route | Evidence expectation |
|---|---|
| `GET /bff/strategies` | Returns a non-empty list including `strategy-pack-a-momentum`. |
| `GET /bff/strategies/strategy-pack-a-momentum` | Returns detail with `id=strategy-pack-a-momentum` and `personaIds` including `persona-pack-a-momentum`. |
| `GET /bff/strategies/strategy-pack-a-momentum/specs` | Returns at least `spec-pack-a-momentum-v1`. |
| `GET /bff/strategies/strategy-pack-a-momentum/experiments` | Returns `exp-pack-a-momentum-001` linked to `artifact-pack-a-momentum-v1`. |
| `GET /bff/strategies/strategy-pack-a-momentum/artifacts` | Returns `artifact-pack-a-momentum-v1` with lineage fields. |
| `GET /bff/strategies/strategy-pack-a-momentum/lineage` | Returns a lineage payload with `lineage-pack-a-strategy-artifact`. |
| `GET /bff/strategies/strategy-pack-a-momentum/audit` | Returns `audit-pack-a-strategy-approved`. |

### Persona detail family

| Route | Evidence expectation |
|---|---|
| `GET /bff/personas` | Returns a non-empty list including `persona-pack-a-momentum`. |
| `GET /bff/personas/persona-pack-a-momentum` | Returns detail with routed strategy count greater than zero. |
| `GET /bff/personas/persona-pack-a-momentum/route-policy` | Returns rules and links the Pack A strategy. |
| `GET /bff/personas/persona-pack-a-momentum/activity` | Returns typed `sessions` and `consultations` arrays. |
| `GET /bff/personas/persona-pack-a-momentum/evaluations` | Returns at least `eval-pack-a-momentum-001`. |

### Deployment and runtime detail family

| Route | Evidence expectation |
|---|---|
| `GET /bff/deployments` | Returns a non-empty list including `plan-pack-a-paper-001`. |
| `GET /bff/deployments/plan-pack-a-paper-001` | Returns approval, capital pool, stage history, and runtime binding fields. |
| `GET /bff/runtimes` | Returns a non-empty list including `runtime-pack-a-paper-001`. |
| `GET /bff/runtimes/runtime-pack-a-paper-001` | Returns the linked deployment plan, paper stage, capital pool, and artifact. |

### Degraded path

The parent regression verifies phantom IDs for all four families return typed
`OBJECT_NOT_FOUND` 404 responses and do not leak raw 500s or `undefined` text:

- `/bff/strategies/phantom-id-does-not-exist`
- `/bff/personas/phantom-id-does-not-exist`
- `/bff/deployments/phantom-id-does-not-exist`
- `/bff/runtimes/phantom-id-does-not-exist`

## Frontend Handoff Gap

The backend read routes are verified. The remaining absorption work is mainly in
the execute-plans client and page wiring.

| Gap | Current frontend observation | Recommended absorption |
|---|---|---|
| Related route path builders | `src/lib/bff-v1/paths.ts` has builders for list/detail routes but not the Pack A related subroutes. | Add builders for strategy `specs`, `experiments`, `artifacts`, `lineage`, `audit`; persona `route-policy`, `activity`, `evaluations`, `memory`, `audit`; and `runtime(id)`. |
| Strategy tabs | `StrategyDetail.tsx` currently gathers broad `jobs`, `audit`, `artifacts`, `research`, and `evolution` lists, then filters or slices locally. | Route the tested tabs to the specific BFF subroutes so strict live mode does not rely on broad seed-shaped lists for detail tabs. |
| Persona tabs | `PersonaDetail.tsx` uses live persona detail but several tab components still call seed-style helpers such as `bff.evaluationRuns.forSubject(...)` or in-memory activity views. | Wire `RoutePolicyPreview`, `ActivityMonitor`, and `PersonaEvaluationsTab` to `/route-policy`, `/activity`, and `/evaluations` where live mode is active. |
| Deployment related runtime | `DeploymentDetail.tsx` filters `bff.runtimes.list()` by `env` and `kind`, not the verified `runtime_binding_id` from deployment detail. | Prefer the deployment detail's `runtime_binding_id` and fetch or highlight the exact runtime binding. |
| Runtime detail journey | `Runtimes.tsx` is currently a list/action surface with no dedicated runtime detail route. | If final acceptance needs UI list-to-detail coverage, add a row-click detail path or a drawer backed by `GET /bff/runtimes/{id}`. |
| DTO normalization | Backend Pack A detail payloads include snake_case fields such as `plan_id`, `approval_decision_id`, `capital_pool_id`, `runtime_binding_id`, and `deployment_stage`. Frontend types are mostly camelCase. | Add narrow adapter functions rather than making pages read mixed naming directly. Preserve raw IDs where they are evidence links. |
| Typed 404 UI state | Persona detail maps 404 to `undefined`; Strategy and Deployment detail pages can remain on loading if a detail returns no object. | Add explicit not-found/error states that surface typed BFF errors without raw backend text. |
| Strict live fallback | BFF-CONSOL-025 makes live-required seed helpers strict in live mode; mock fallback must not masquerade as live truth. | Avoid `.catch(() => [])` on critical related tabs unless the UI visibly marks that panel as degraded/unavailable. |
| Stale route diff evidence | The 2026-05-08 route snapshot predates BFF-CONSOL-016 route closure. | Before BFF-CONSOL-027 final acceptance, rerun the BFF route manifest/diff and replace stale "missing" evidence for Pack A routes. |

## Recommended Frontend Contract Additions

Add path builders in `src/lib/bff-v1/paths.ts` or an adjacent route helper:

```typescript
strategySpecs: (id: string) => `${BASE}/strategies/${enc(id)}/specs`,
strategyExperiments: (id: string) => `${BASE}/strategies/${enc(id)}/experiments`,
strategyArtifacts: (id: string) => `${BASE}/strategies/${enc(id)}/artifacts`,
strategyLineage: (id: string) => `${BASE}/strategies/${enc(id)}/lineage`,
strategyAudit: (id: string) => `${BASE}/strategies/${enc(id)}/audit`,
personaRoutePolicy: (id: string) => `${BASE}/personas/${enc(id)}/route-policy`,
personaActivity: (id: string) => `${BASE}/personas/${enc(id)}/activity`,
personaEvaluations: (id: string) => `${BASE}/personas/${enc(id)}/evaluations`,
personaMemory: (id: string) => `${BASE}/personas/${enc(id)}/memory`,
personaAudit: (id: string) => `${BASE}/personas/${enc(id)}/audit`,
runtime: (id: string) => `${BASE}/runtimes/${enc(id)}`,
```

Recommended adapter shape:

```typescript
export async function getStrategyRelated(strategyId: string) {
  return {
    specs: await readList(paths.strategySpecs(strategyId)),
    experiments: await readList(paths.strategyExperiments(strategyId)),
    artifacts: await readList(paths.strategyArtifacts(strategyId)),
    lineage: await readDetail(paths.strategyLineage(strategyId)),
    audit: await readList(paths.strategyAudit(strategyId)),
  };
}

export async function getPersonaRelated(personaId: string) {
  return {
    routePolicy: await readDetail(paths.personaRoutePolicy(personaId)),
    activity: await readDetail(paths.personaActivity(personaId)),
    evaluations: await readList(paths.personaEvaluations(personaId)),
  };
}
```

Use existing `bffFetch`, `BffError`, `withLiveOrMock`, or the strict live seed
helpers already in the execute-plans tree. Do not invent a second response
envelope; normalize `{data}`, `{items}`, and `page_info` the same way
`src/lib/bff-v1/lists.ts` does.

## Operator Journey

### Strategy detail journey

```text
Operator opens /management/strategies
  -> UI calls GET /bff/strategies
  -> operator selects strategy-pack-a-momentum
  -> UI calls GET /bff/strategies/strategy-pack-a-momentum
  -> detail page loads related tabs:
       specs       -> GET /bff/strategies/{id}/specs
       experiments -> GET /bff/strategies/{id}/experiments
       artifacts   -> GET /bff/strategies/{id}/artifacts
       lineage     -> GET /bff/strategies/{id}/lineage
       audit       -> GET /bff/strategies/{id}/audit
  -> every tab renders at least one Pack A-backed entry or a typed degraded state
```

### Persona detail journey

```text
Operator opens /management/personas
  -> UI calls GET /bff/personas
  -> operator selects persona-pack-a-momentum
  -> UI calls GET /bff/personas/persona-pack-a-momentum
  -> route policy tab calls GET /bff/personas/{id}/route-policy
  -> activity tab calls GET /bff/personas/{id}/activity
  -> evaluations tab calls GET /bff/personas/{id}/evaluations
  -> UI links back to strategy-pack-a-momentum instead of deriving an empty route list
```

### Deployment and runtime journey

```text
Operator opens /management/deployments
  -> UI calls GET /bff/deployments
  -> operator selects plan-pack-a-paper-001
  -> UI calls GET /bff/deployments/plan-pack-a-paper-001
  -> detail shows approval-pack-a-deploy, pool-pack-a-ops, stages, and runtime-pack-a-paper-001
  -> runtime tab or runtime detail calls GET /bff/runtimes/runtime-pack-a-paper-001
  -> UI links back to plan-pack-a-paper-001 and artifact-pack-a-momentum-v1
```

### Missing ID journey

```text
Operator opens a stale bookmarked detail URL
  -> BFF returns typed OBJECT_NOT_FOUND 404
  -> frontend maps the typed error to not-found/degraded UI
  -> UI does not show an infinite loading spinner, raw 500, or undefined text
```

## Suggested Test Matrix

Parent or frontend owners can add focused tests without changing backend truth:

| Test | Expected result |
|---|---|
| Path builders cover Pack A related routes | Builders produce exactly the verified `/bff/...` paths. |
| Strategy related adapter normalizes lists | `{data: [...]}`, `{items: [...]}`, and `{page_info}` become stable tab data. |
| Strategy lineage adapter normalizes detail | `{data: {edges, node_ids}}` becomes a graph/detail view model. |
| Persona route-policy adapter | `rules[0].route` links to `strategy-pack-a-momentum`. |
| Persona activity adapter | `sessions` and `consultations` are arrays even when one side is empty. |
| Persona evaluations adapter | `eval-pack-a-momentum-001` renders in the evaluations tab. |
| Deployment detail adapter | `plan_id`, `approval_decision_id`, `capital_pool_id`, and `runtime_binding_id` survive normalization. |
| Runtime detail adapter | `runtime_id`, `plan_id`, `deployment_stage`, `capital_pool_id`, and `artifact_id` survive normalization. |
| Typed 404 state | Phantom strategy/persona/deployment/runtime IDs show typed not-found UI, not infinite loading. |
| Strict live mode | With `VITE_BFF_MODE=live` and `VITE_BFF_FALLBACK=strict`, no Pack A page silently substitutes seed data. |

Suggested backend verification already proven by the parent task:

```bash
python3 -m py_compile services/control-plane/bff/test_bff_consol_016_detail_smoke_a.py
python3 -m pytest services/control-plane/bff/test_bff_consol_016_detail_smoke_a.py services/control-plane/bff/test_bff_consol_008_fixture_pack_a.py -q
python3 -m json.tool support/evidence/BFF-CONSOL-016-detail-smoke-a.json
```

Suggested frontend verification after absorption:

```bash
npm test -- src/lib/bff-v1/__tests__/envelope.test.ts src/lib/bff-v1/__tests__/lists.test.ts
npm test -- src/management/pages/PersonaDetail.test.ts
npm run build
```

If a live/staging BFF is available, also run the Playwright API smoke from
`execute-plans/tests/e2e/detail-smoke-a.spec.ts` with `BFF_BASE_URL` and
`BFF_AUTH_TOKEN` set for the target environment.

## Parent Absorption Risks and Gates

- Do not reopen backend route implementation unless new evidence contradicts
  the completed BFF-CONSOL-016 regression. The parent backend scope is already
  done and reviewed.
- Do not treat broad list slices in `StrategyDetail.tsx` as equivalent to the
  verified related subroutes. Final read-path acceptance should prove the
  route-specific tabs.
- Do not let strict live UI convert backend errors into empty rows without a
  degraded/unavailable state. Empty rows are acceptable only when the BFF
  returns an authoritative empty list.
- Do not use the 2026-05-08 route snapshot as negative evidence for Pack A
  routes. Refresh the route manifest before BFF-CONSOL-027 final acceptance.
- Keep production live broker/capital side effects fail-closed. This packet is
  read-path evidence only.

## Handoff Checklist for Codex2

- Confirm this packet changed only support artifact scope.
- Confirm it references the completed BFF-CONSOL-016 evidence and archive.
- Confirm the route list matches the parent evidence JSON and regression test.
- Confirm the frontend gaps are framed as absorption notes, not canonical truth
  or runtime implementation changes.
- If accepted, parent/final owners can use this packet to update BFF-CONSOL-027
  acceptance sections for Pack A detail smoke A.

## Scope Confirmation

Changed by this sidecar:

- `support/sidecars/BFF-CONSOL-016/BFF-CONSOL-016-SIDECAR-BFF-HANDOFF.md`

Not changed by this sidecar:

- L1 canonical architecture or policy documents.
- `services/control-plane/bff` runtime implementation.
- execute-plans frontend implementation.
- core contract truth, route registry, governance logic, or BFF command logic.
