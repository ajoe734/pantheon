# BFF-CONSOL-017 Sidecar: BFF and Frontend Handoff Packet

Task ID: BFF-CONSOL-017-SIDECAR-BFF-HANDOFF
Parent Task: BFF-CONSOL-017 - Detail journey smoke B (evolution research v5 agora artifacts)
Helper Kind: bff_handoff_packet
Prepared by: Codex2
Reviewer: Codex
Date: 2026-05-13
Mutates canonical truth: false

## Purpose

This packet gives the BFF-CONSOL-017 parent and final BFF consolidation owners a
support-only handoff for the Pack B detail journey smoke. It translates the
completed backend evidence into frontend absorption notes, operator journey
coverage, and remaining client-side gaps for evolution, research, v5
interventions, agora sessions, and artifacts.

This document does not change L1 canonical truth, core contracts, runtime code,
registry code, governance implementation, or execute-plans implementation.

## Current State Observed

| Area | Observation | Handoff impact |
|---|---|---|
| Parent task | `BFF-CONSOL-017` is archived as `done` with commit `83c42310794d8e044f620a814186ac5de333bcfc`. | Final acceptance can treat Pack B detail smoke B as completed, not pending. |
| Dependency | `BFF-CONSOL-009` fixture pack B is archived as `done` with commit `d0efa73d270d93a86dbe4ba9f66835535b44c8b7`. | Pack B IDs and fixture relationships are the source for this packet's route list. |
| Parent evidence | `support/evidence/BFF-CONSOL-017-detail-smoke-b.json` records per-family transcripts and acceptance checks. | Use this as the primary evidence file for Pack B detail route behavior. |
| Backend regression | `services/control-plane/bff/test_bff_consol_017_detail_smoke_b.py` verifies Pack B route resolution through FastAPI `TestClient`. | Backend route behavior is locally reproducible without changing runtime code. |
| Frontend smoke artifact | `execute-plans/tests/e2e/detail-smoke-b.spec.ts` records the intended live BFF API smoke for Pack B list-detail-related journeys. | Frontend/live lanes can reuse the route list and Pack B fixture IDs for browser/API smoke. |
| Research caveat | Parent review corrected research proof away from `/api/v1/research/tickets/{id}` and `/api/v1/research/analysis/{id}` because those strict API detail routes intentionally return typed 404 for local Pack B fallback. | Frontend acceptance should use `/bff/research-experiments/{id}` plus `/bff/research-analyses/{id}` for the research linkage proof. |
| Evolution caveat | Parent fix proves `/bff/evolution-programs/{id}` resolves Pack B through BFF helper fallback to `read_store.get_evolution_program()`. | Final read-path evidence should use the BFF route, not only the raw evolution decision API. |
| Frontend checkout | In this Pantheon repo, only `execute-plans/tests` and `execute-plans/src/lib/bff/runAction.ts` are tracked. The richer frontend source inspected for absorption notes is the sibling checkout at `/home/lupin/code/execute-plans`. | Treat frontend file names below as handoff pointers for the execute-plans owner; this sidecar does not edit that checkout. |

## Evidence Sources

| Evidence | Path | Use |
|---|---|---|
| Parent archive | `ai-task-archive/tasks/BFF-CONSOL-017.json` | Delivery status, commit, review notes, and verification summary. |
| Dependency archive | `ai-task-archive/tasks/BFF-CONSOL-009.json` | Fixture pack B source and dependency delivery metadata. |
| Smoke evidence | `support/evidence/BFF-CONSOL-017-detail-smoke-b.json` | Per-family route transcript, fixture IDs, degraded-path evidence, and verification commands. |
| Backend regression | `services/control-plane/bff/test_bff_consol_017_detail_smoke_b.py` | Focused reproducible test for Pack B route behavior. |
| Fixture regression | `services/control-plane/bff/test_bff_consol_009_fixture_pack_b.py` | Confirms fixture pack B families are non-empty and linked. |
| Playwright API smoke | `execute-plans/tests/e2e/detail-smoke-b.spec.ts` | Frontend-facing API smoke route list and expected live route journey. |
| Frontend path builders | `/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts` | Current execute-plans path helper coverage. |
| Frontend read adapters | `/home/lupin/code/execute-plans/src/lib/bff-v1/seed.ts`, `/home/lupin/code/execute-plans/src/lib/bff/client.ts`, `/home/lupin/code/execute-plans/src/lib/bff/v5.ts`, `/home/lupin/code/execute-plans/src/lib/bff/agora.ts` | Current live/mock read behavior and adapter gaps. |
| Frontend pages | `/home/lupin/code/execute-plans/src/management/pages/ResearchDetail.tsx`, `/home/lupin/code/execute-plans/src/management/pages/ArtifactDetail.tsx`, `/home/lupin/code/execute-plans/src/management/pages/v5/Interventions.tsx`, `/home/lupin/code/execute-plans/src/management/pages/Lists.tsx` | UI absorption points for Pack B detail journeys. |

## BFF Query Coverage Confirmed

### Evolution family

| Route | Evidence expectation |
|---|---|
| `GET /bff/evolution-programs` | Returns a list including `evoprog-pack-b-001`. |
| `GET /bff/evolution-programs/evoprog-pack-b-001` | Resolves 200 with `program_id=evoprog-pack-b-001`, name `Pack B Momentum Carry Evolution Program`, and `status=active`. |
| `GET /api/v1/evolution-decisions/evo-dec-pack-b-001` | Resolves the linked decision with `program_id=evoprog-pack-b-001` and `target_id=artifact-pack-b-001`. |

### Research family

| Route | Evidence expectation |
|---|---|
| `GET /bff/research-experiments` | Returns a list including `exp-pack-b-001`. |
| `GET /bff/research-experiments/exp-pack-b-001` | Resolves 200 with `ticket_id=rt-pack-b-001`, `artifact_ids=["artifact-pack-b-001"]`, and `analysis_ids=["analysis-pack-b-001"]`. |
| `GET /bff/research-analyses/analysis-pack-b-001` | Resolves 200 with `ticket_id=rt-pack-b-001`, `experiment_id=exp-pack-b-001`, and `status=completed`. |
| `GET /api/v1/research/tickets/rt-pack-b-001` | Observed typed 404 by design for strict API local-fallback behavior. Do not use as the Pack B frontend proof. |
| `GET /api/v1/research/analysis/analysis-pack-b-001` | Observed typed 404 by design. Use `/bff/research-analyses/{id}` instead. |

### v5 intervention family

| Route | Evidence expectation |
|---|---|
| `GET /bff/v5/interventions` | Returns a list including `intv-pack-b-001`. |
| `GET /bff/v5/interventions/intv-pack-b-001` | Resolves 200 with `remediation_skeleton.two_man_rule_enforced=true`, `required_approvers=2`, audit trail required, approval decision required, and remediation actions. |

### Agora family

| Route | Evidence expectation |
|---|---|
| `GET /bff/agora/sessions` | Returns a list including `agora-session-pack-b-001`. |
| `GET /bff/agora/sessions/agora-session-pack-b-001` | Resolves 200 with `sessionId=agora-session-pack-b-001`, mode `committee_review`, active status, `sse_topic`, and Pack B context refs. |
| `GET /bff/agora/sessions/agora-session-pack-b-001/messages` | Resolves 200 with at least `msg-pack-b-001` as a historical message. |

### Artifact family

| Route | Evidence expectation |
|---|---|
| `GET /bff/artifacts` | Returns a list including `artifact-pack-b-001`. |
| `GET /bff/artifacts/artifact-pack-b-001` | Resolves 200 with `artifact_id=artifact-pack-b-001`, `lineage_id=lineage-pack-b-001`, status `sealed`, and Pack B artifact name. |
| `GET /api/v1/lineage/inspiration/artifact-pack-b-001` | Resolves 200 and synthesizes an inspiration edge from `lineage-pack-b-001` when no explicit inspiration graph exists. |

### Degraded path

The parent regression verifies phantom IDs for all five families return typed
`OBJECT_NOT_FOUND` 404 responses and do not leak raw 500s:

- `/bff/evolution-programs/phantom-id-does-not-exist`
- `/bff/research-experiments/phantom-id-does-not-exist`
- `/bff/v5/interventions/phantom-id-does-not-exist`
- `/bff/agora/sessions/phantom-id-does-not-exist`
- `/bff/artifacts/phantom-id-does-not-exist`

## Frontend Handoff Gap

The backend read routes are verified. The remaining absorption work is mainly in
execute-plans client path coverage, entity DTO normalization, detail-page data
loading, and typed degraded/not-found UI states.

| Gap | Current frontend observation | Recommended absorption |
|---|---|---|
| Path builders for Pack B detail routes | `src/lib/bff-v1/paths.ts` has list/detail builders for evolution programs, artifacts, and v5 interventions. It has `researchExperiments()` but no explicit `researchExperiment(id)` or `researchAnalysis(id)`. It also has only agora ask-session builders, not `/bff/agora/sessions` or `/messages`. | Add narrow builders for the exact Pack B routes, including research analysis, agora sessions/messages, evolution runs/candidates if those tabs are shown, and artifact inspiration lineage. |
| Entity DTO normalization | List and detail adapters normalize response envelopes, but management pages still expect legacy camelCase fields such as `metricValue`, `artifactId`, `sourceExperimentId`, `generation`, `population`, `bestFitness`, and `progress`. Pack B BFF payloads include snake_case and plural IDs such as `analysis_ids`, `artifact_ids`, `lineage_id`, and `program_id`. | Add family-specific adapters before rendering. Preserve raw BFF IDs for evidence links while mapping display fields with safe defaults. |
| Evolution detail journey | Lists route to `/management/evolution/:id`, but sibling-source inspection did not find a dedicated evolution detail page in the checked files used here. | If final acceptance needs UI list-to-detail coverage, add or verify an evolution detail route backed by `/bff/evolution-programs/{id}` and related `/runs` or `/candidates` tabs. |
| Research detail linkage | `ResearchDetail.tsx` calls `bff.research.get(id)` and shows a singular `x.artifactId`; it does not render `analysis_ids` or `analysis_links`. It also derives fold metrics client-side with `Math.random()`. | Add a research detail adapter that displays Pack B `analysis_links` and `artifact_ids`, links to `/management/artifacts/artifact-pack-b-001`, and avoids presenting random derived folds as live BFF evidence. |
| Artifact lineage journey | `ArtifactDetail.tsx` calls `bff.artifacts.get(id)` and shows lineage through `sourceExperimentId`; it does not call `/api/v1/lineage/inspiration/{artifact_id}` or render `lineage_id` as the Pack B proof. | Add an artifact lineage/inspiration adapter and tab backed by the verified inspiration route. Link back to `exp-pack-b-001` and expose `lineage-pack-b-001` as evidence. |
| v5 intervention detail | `src/lib/bff/v5.ts` exposes `v5.interventions.get(id)` and adapts BFF interventions, but `InterventionsPage.tsx` opens the drawer from the list item. The adapter does not carry the full `remediation_skeleton` into the drawer view model. | Fetch detail on drawer open or include detail fields in the list adapter. Render the governed remediation skeleton: two-man rule, required approvers, policy ref, audit trail, and approval requirement. |
| Agora session journey | `src/lib/bff/agora.ts` currently adapts signals, inbox, journal, and ask sessions. It does not expose `/bff/agora/sessions`, `/bff/agora/sessions/{id}`, or `/messages`. | Add an agora session client that lists Pack B sessions, opens `agora-session-pack-b-001`, and renders historical `msg-pack-b-001` plus context refs. |
| Typed 404 UI state | Detail pages commonly render a loading placeholder when the object is absent. Typed BFF errors are not consistently mapped to a not-found/degraded view. | Map `OBJECT_NOT_FOUND` to an explicit not-found state for evolution, research, intervention, agora, and artifact detail pages. Avoid infinite loading, raw backend text, or `undefined`. |
| Strict live evidence | `src/lib/bff-v1/seed.ts` now performs strict live reads for core `bff.*` helpers when live mode is configured, while list helpers can still run in auto fallback mode. | Run Pack B final UI smoke with `VITE_BFF_MODE=live` and `VITE_BFF_FALLBACK=strict` so final acceptance does not accidentally pass through mock fallback. |

## Recommended Frontend Contract Additions

Add path builders in `src/lib/bff-v1/paths.ts` or an adjacent route helper. The
lineage route is under `/api/v1`, so either add an explicit API path helper or
create a BFF alias before treating it as a `/bff` route.

```typescript
researchExperiment: (id: string) => `${BASE}/research-experiments/${enc(id)}`,
researchAnalyses: () => `${BASE}/research-analyses`,
researchAnalysis: (id: string) => `${BASE}/research-analyses/${enc(id)}`,
evolutionProgramRuns: (id: string) => `${BASE}/evolution-programs/${enc(id)}/runs`,
evolutionProgramCandidates: (id: string) => `${BASE}/evolution-programs/${enc(id)}/candidates`,
agoraSessions: () => `${BASE}/agora/sessions`,
agoraSession: (id: string) => `${BASE}/agora/sessions/${enc(id)}`,
agoraSessionMessages: (id: string) => `${BASE}/agora/sessions/${enc(id)}/messages`,
artifactInspiration: (id: string) => `/api/v1/lineage/inspiration/${enc(id)}`,
```

Recommended adapter shapes:

```typescript
export async function getResearchExperimentRelated(experimentId: string) {
  const experiment = await readDetail(paths.researchExperiment(experimentId));
  return {
    experiment,
    analysisLinks: normalizeAnalysisLinks(experiment),
    artifactIds: normalizeStringIds(experiment.artifact_ids ?? experiment.artifactIds),
  };
}

export async function getArtifactLineage(artifactId: string) {
  const artifact = await readDetail(paths.artifact(artifactId));
  const inspiration = await readDetail(paths.artifactInspiration(artifactId));
  return { artifact, inspiration };
}

export async function getAgoraSessionTranscript(sessionId: string) {
  return {
    session: await readDetail(paths.agoraSession(sessionId)),
    messages: await readList(paths.agoraSessionMessages(sessionId)),
  };
}

export async function getV5InterventionDetail(interventionId: string) {
  const detail = await v5.interventions.get(interventionId);
  return normalizeRemediationSkeleton(detail);
}
```

Use the existing `bffFetch`, `BffError`, `withLiveOrMock`,
`withStrictLiveOrMock`, and strict live seed helpers already in the
execute-plans tree. Do not invent a second response envelope; normalize
`{data}`, `{items}`, and route-specific arrays the same way `src/lib/bff-v1`
already does.

## Operator Journey

### Evolution detail journey

```text
Operator opens /management/evolution
  -> UI calls GET /bff/evolution-programs
  -> operator selects evoprog-pack-b-001
  -> UI calls GET /bff/evolution-programs/evoprog-pack-b-001
  -> detail shows Pack B program identity, status, params, and linked decision
  -> related tabs call /runs or /candidates only if those views are implemented
  -> missing program id maps to typed not-found UI
```

### Research analysis journey

```text
Operator opens /management/research
  -> UI calls GET /bff/research-experiments
  -> operator selects exp-pack-b-001
  -> UI calls GET /bff/research-experiments/exp-pack-b-001
  -> detail renders ticket rt-pack-b-001, analysis-pack-b-001, and artifact-pack-b-001
  -> analysis link calls GET /bff/research-analyses/analysis-pack-b-001
  -> UI does not depend on /api/v1/research/tickets/{id} or /api/v1/research/analysis/{id}
```

### v5 intervention journey

```text
Operator opens /management/interventions
  -> UI calls GET /bff/v5/interventions
  -> operator selects intv-pack-b-001
  -> drawer or detail calls GET /bff/v5/interventions/intv-pack-b-001
  -> UI shows remediation_skeleton with two-man rule, approver count, policy ref, and audit requirement
  -> any remediation/write control remains gated by existing write policy and does not imply live capital side effects
```

### Agora historical event journey

```text
Operator opens the agora session surface
  -> UI calls GET /bff/agora/sessions
  -> operator opens agora-session-pack-b-001
  -> UI calls GET /bff/agora/sessions/agora-session-pack-b-001
  -> messages tab calls GET /bff/agora/sessions/agora-session-pack-b-001/messages
  -> UI renders msg-pack-b-001 and context refs for research ticket, analysis, and artifact
```

### Artifact lineage journey

```text
Operator opens /management/artifacts
  -> UI calls GET /bff/artifacts
  -> operator selects artifact-pack-b-001
  -> UI calls GET /bff/artifacts/artifact-pack-b-001
  -> lineage tab calls GET /api/v1/lineage/inspiration/artifact-pack-b-001
  -> UI renders lineage-pack-b-001 and links back to spec-pack-b-v1 / exp-pack-b-001 where available
```

### Missing ID journey

```text
Operator opens a stale bookmarked detail URL
  -> BFF returns typed OBJECT_NOT_FOUND 404
  -> frontend maps the typed error to a not-found/degraded UI state
  -> UI does not show an infinite loading spinner, raw 500, or undefined text
```

## Suggested Test Matrix

Parent or frontend owners can add focused tests without changing backend truth:

| Test | Expected result |
|---|---|
| Path builders cover Pack B routes | Builders produce exactly the verified evolution, research, v5, agora, artifact, and lineage paths. |
| Research detail adapter normalizes links | `analysis_ids`, `analysis_links`, and `artifact_ids` render as stable links. |
| Research strict API caveat is preserved | UI tests do not require `/api/v1/research/tickets/{id}` or `/api/v1/research/analysis/{id}` to return 200 for Pack B proof. |
| Artifact lineage adapter | `lineage_id=lineage-pack-b-001` and inspiration edge `lineage_edge_id=lineage-pack-b-001` render in the lineage tab. |
| v5 remediation skeleton adapter | `two_man_rule_enforced`, `required_approvers`, policy ref, audit trail, and approval requirement survive normalization. |
| Agora session transcript adapter | `agora-session-pack-b-001` detail and `msg-pack-b-001` messages render with context refs. |
| Evolution detail adapter | `program_id=evoprog-pack-b-001` survives normalization and does not crash legacy list/detail columns. |
| Typed 404 state | Phantom evolution/research/intervention/agora/artifact IDs show typed not-found UI, not infinite loading. |
| Strict live mode | With `VITE_BFF_MODE=live` and `VITE_BFF_FALLBACK=strict`, Pack B pages do not substitute seed data after live transport failure. |

Suggested backend verification already proven by the parent task:

```bash
PANTHEON_BFF_AUTH_STUB=true PANTHEON_BFF_AUTH_MODE=permissive python3 -m pytest services/control-plane/bff/test_bff_consol_017_detail_smoke_b.py -q
PANTHEON_BFF_AUTH_STUB=true PANTHEON_BFF_AUTH_MODE=permissive python3 -m pytest services/control-plane/bff/test_bff_consol_017_detail_smoke_b.py services/control-plane/bff/test_bff_consol_009_fixture_pack_b.py services/control-plane/bff/test_rw03_analyze_contract.py services/control-plane/bff/test_bff_evolution_experiment_jobs_events_contract.py services/control-plane/bff/test_ew04_inspiration_graph_contract.py -q
python3 -m json.tool support/evidence/BFF-CONSOL-017-detail-smoke-b.json
```

Suggested frontend verification after absorption:

```bash
npm test -- src/lib/bff-v1/__tests__/envelope.test.ts src/lib/bff-v1/__tests__/lists.test.ts
npm test -- src/lib/v5/__tests__/bff.test.ts
npm run build
```

If a live/staging BFF is available, also run the Playwright API smoke from
`execute-plans/tests/e2e/detail-smoke-b.spec.ts` with `BFF_BASE_URL` and
`BFF_AUTH_TOKEN` set for the target environment.

## Parent Absorption Risks and Gates

- Do not reopen backend route implementation unless new evidence contradicts
  the completed BFF-CONSOL-017 regression. The parent backend scope is already
  done and reviewed.
- Do not use `/api/v1/research/tickets/{id}` or
  `/api/v1/research/analysis/{id}` as the Pack B research acceptance proof.
  Parent review explicitly moved the proof to BFF research experiment and
  research analysis routes.
- Do not let frontend entity-shape mismatches convert valid live Pack B
  payloads into blank tabs, `NaN`, crashes, or mock-looking data.
- Do not hide Pack B intervention remediation skeleton behind a list-only
  drawer. The governed detail fields are the main v5 acceptance signal.
- Do not treat agora ask sessions as equivalent to Pack B agora committee
  sessions. The verified route family is `/bff/agora/sessions`.
- Keep production live broker/capital side effects fail-closed. This packet is
  read-path evidence only.

## Handoff Checklist for Codex

- Confirm this packet changed only support artifact scope.
- Confirm it references the completed BFF-CONSOL-017 evidence and archive.
- Confirm the route list matches the parent evidence JSON and regression test.
- Confirm the frontend gaps are framed as absorption notes, not canonical truth
  or runtime implementation changes.
- Confirm parent/final acceptance can route Pack B frontend work through
  research analysis, artifact lineage, v5 remediation skeleton, and agora
  session message adapters.

## Verification for This Sidecar

Performed as read-only context checks plus artifact creation:

- Read task-scoped context: `AI_COLLABORATION_GUIDE.md`,
  `.orchestrator/task-briefs/bff_consol_017_sidecar_bff_handoff.md`,
  `.orchestrator/skills/task-closeout-finalization.md`, and `ai-status.json`.
- Reconciled sidecar state in `ai-status.json`: owner `Codex2`, reviewer
  `Codex`, status `in_progress`, artifact
  `support/sidecars/BFF-CONSOL-017/BFF-CONSOL-017-SIDECAR-BFF-HANDOFF.md`.
- Read parent archive `ai-task-archive/tasks/BFF-CONSOL-017.json`,
  dependency archive `ai-task-archive/tasks/BFF-CONSOL-009.json`, parent
  evidence JSON, backend regression, fixture regression, and Playwright smoke
  spec.
- Inspected sibling execute-plans frontend files for route/helper/page
  absorption notes without editing that checkout.
- Ran `git diff --check -- support/sidecars/BFF-CONSOL-017/BFF-CONSOL-017-SIDECAR-BFF-HANDOFF.md`.
- Ran `python3 -m json.tool support/evidence/BFF-CONSOL-017-detail-smoke-b.json >/dev/null`.
- Ran `python3 -m py_compile services/control-plane/bff/test_bff_consol_017_detail_smoke_b.py`.
- Ran `LC_ALL=C rg -n '[^[:ascii:]]' support/sidecars/BFF-CONSOL-017/BFF-CONSOL-017-SIDECAR-BFF-HANDOFF.md` and found no non-ASCII content.

No canonical truth, core contract truth, runtime implementation, registry code,
governance implementation, or execute-plans implementation was modified by this
sidecar.

## Owner Closeout Addendum

Closeout date: 2026-05-13
Owner: Codex2
Reviewer approval: Codex approved the packet for owner finalization in
`ai-status.json` at `2026-05-13T11:41:47Z`.
Scoped packet commit: `a953d629`

Owner finalization checks:

- Confirmed the approved packet remains support-only and scoped to
  `support/sidecars/BFF-CONSOL-017/BFF-CONSOL-017-SIDECAR-BFF-HANDOFF.md`.
- Confirmed `a953d629` changes only this sidecar support packet.
- Confirmed the packet still does not modify L1 canonical truth, core
  contracts, runtime code, registry code, governance implementation, or
  execute-plans implementation.
- Re-ran focused closeout verification before moving the task from
  `review_approved` to `done`.
