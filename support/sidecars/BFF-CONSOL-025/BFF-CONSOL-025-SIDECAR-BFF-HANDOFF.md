# BFF-CONSOL-025 Sidecar: BFF and Frontend Handoff Packet

Task ID: BFF-CONSOL-025-SIDECAR-BFF-HANDOFF
Parent Task: BFF-CONSOL-025 - Seed-only surface elimination
Helper Kind: bff_handoff_packet
Prepared by: Codex2
Reviewer: Codex
Date: 2026-05-13
Mutates canonical truth: false

## Purpose

This support-only packet gives the BFF consolidation owner, final acceptance
owner, and frontend owner a handoff for the BFF-CONSOL-025 seed-only surface
elimination. It summarizes the parent baseline, the BFF route and helper gap,
the operator journey in live mode, and the frontend follow-up boundaries.

This artifact does not change L1 canonical truth, the BFF runtime, command
contracts, route manifests, registry code, governance implementation, or
execute-plans source. It should be linked as supporting material only.

## Parent State Observed

| Area | Observed state |
|---|---|
| Parent lifecycle | `BFF-CONSOL-025` is archived as `done`. |
| Parent owner / reviewer | Codex / Claude. |
| Pantheon evidence commit | `f37b1099` (`BFF-CONSOL-025 record seed elimination evidence`). |
| Frontend implementation commit | sibling `../execute-plans` commit `226d7e4` (`BFF-CONSOL-025 eliminate seed-only live surfaces`). |
| Parent evidence | `docs/bff/seed-elimination-2026-05-13.md`; `support/sidecars/BFF-CONSOL-025/BFF-CONSOL-025-REVIEW.md`. |
| Taxonomy inputs | `docs/bff/seed-taxonomy.json`; sibling `src/lib/bff-v1/seed-taxonomy.json`. |
| Parent accepted result | live-required helpers strict-read BFF routes; mock-only-dev helpers return empty/null in live mode; deprecated write helpers are removed from the seed accessor; deferred helpers point at `BFF-CONSOL-028`; strict live mode has no silent seed fallback in the seed accessor. |
| Downstream dependencies | `BFF-CONSOL-028` owns unresolved adjunct helpers; `BFF-CONSOL-027` should reference the seed post-state in final acceptance; `BFF-CONSOL-022/023` strict cutover should keep `VITE_BFF_MODE=live` with strict fallback and verify no hidden seed use. |

## Context Sources Used

- `AI_COLLABORATION_GUIDE.md`
- `.orchestrator/task-briefs/bff_consol_025_sidecar_bff_handoff.md`
- `.orchestrator/skills/task-closeout-finalization.md`
- `ai-status.json`
- `ai-task-archive/tasks/BFF-CONSOL-025.json`
- `support/sidecars/BFF-CONSOL-025/BFF-CONSOL-025-REVIEW.md`
- `docs/bff/seed-elimination-2026-05-13.md`
- `docs/bff/seed-taxonomy.json`
- sibling read-only frontend files in `/home/lupin/code/execute-plans`:
  `src/lib/bff-v1/seed.ts`, `src/lib/bff-v1/seedTaxonomy.ts`,
  `src/lib/bff-v1/paths.ts`, and
  `src/lib/bff-v1/__tests__/seedTaxonomy.test.ts`

## Baseline Taxonomy Snapshot

The parent baseline remains the BFF-CONSOL-007 taxonomy as consumed by
BFF-CONSOL-025:

| Category | Count | Parent 025 live-mode handling |
|---|---:|---|
| `live_required` | 52 | Must call live BFF routes in live mode and throw typed errors on transport failure; no seed fallback. |
| `deferred` | 25 | Must not return seed as live truth; parent points them at `BFF-CONSOL-028`. |
| `mock_only_dev` | 4 | Must return empty/null in live mode and expose unavailable/badge state where the UI renders the surface. |
| `deprecated` | 2 | Removed from the public seed accessor; callers should use direct imports or command client paths. |
| Total | 83 | All helpers classified. |

Path note: the task brief names `execute-plans/src/lib/bff/seed.ts`, but the
active frontend seed accessor is `../execute-plans/src/lib/bff-v1/seed.ts`.
The Pantheon-side taxonomy records this discrepancy.

## BFF Route Coverage Handoff

### Live-required core read helpers

Parent 025 wires direct legacy helper calls to live BFF reads when
`VITE_BFF_MODE=live`.

| Helper family | Live route coverage |
|---|---|
| `strategies` | `GET /bff/strategies`; `GET /bff/strategies/{id}` |
| `personas` | `GET /bff/personas`; `GET /bff/personas/{id}` |
| `capitalPools` | `GET /bff/capital-pools`; `GET /bff/capital-pools/{id}` |
| `rankingFormulas` | `GET /bff/ranking-formulas`; `GET /bff/ranking-formulas/{id}` |
| `rebalances` | `GET /bff/rebalances`; `GET /bff/rebalances/{id}` |
| `deployments` | `GET /bff/deployments`; `GET /bff/deployments/{id}` |
| `evolution` | `GET /bff/evolution-programs`; `GET /bff/evolution-programs/{id}` |
| `research` | `GET /bff/research-experiments`; `GET /bff/research-experiments/{id}` |
| `artifacts` | `GET /bff/artifacts`; `GET /bff/artifacts/{id}` |
| `jobs` | `GET /bff/jobs` |
| `runtimes` | `GET /bff/runtimes`; `GET /bff/runtimes/{id}` |
| `alerts` | `GET /bff/alerts`; `GET /bff/alerts/{id}` |
| `incidents` | `GET /bff/incidents`; `GET /bff/incidents/{id}` |
| `approvals` | `GET /bff/approvals`; `GET /bff/approvals/{id}` |
| `audit` | `GET /bff/audit` |
| `tools`, `mcpServers`, `mcpTools`, `skills`, `channels` | Corresponding list and detail BFF routes. |
| `search` | `GET /bff/search` |

### Live-required adjunct helpers folded into existing routes

| Helper | Parent 025 route or DTO source |
|---|---|
| `bff.routePolicies.forPersona` | `GET /bff/personas/{id}/route-policy` |
| `bff.memoryUpdates.forPersona` | `GET /bff/personas/{id}/memory` |
| `bff.evolutionRuns.forProgram` | `GET /bff/evolution-programs/{id}/runs` |
| `bff.decisionJournal.list` / `forSubject` | `GET /bff/agora/journal` via the Agora client. |
| `bff.deploymentStages.forDeployment` | `GET /bff/deployments/{id}`, extracting stage arrays from the detail DTO. |
| `bff.rebalanceWorkflow.forRebalance` | `GET /bff/rebalances/{id}`, extracting workflow arrays from detail or command audit DTOs. |

These helpers are the important "no seed masquerade" surface for BFF-CONSOL-025.
If any route returns a 4xx/5xx or transport failure in live mode, the helper
must surface a typed BFF error rather than returning seeded rows.

## Remaining Query Gap Matrix

BFF-CONSOL-025 intentionally did not claim final live semantics for every
taxonomy helper. The remaining gaps are either removed, hidden, or owned by
follow-up work.

| Gap class | Helpers | Required follow-up |
|---|---|---|
| Deprecated write helpers removed | `bff.mutations`; `bff.commands.requestConfirmToken` | Keep callers on direct `mutations` imports, `runActionSafe`, `bffWrites`, `/bff/actions/*`, or `/bff/v1/commands` according to BFF-CONSOL-019/020/021/024. Do not re-export them from `bff`. |
| Mock-only-dev hidden in live mode | `bff.getAcceptLanguage`; `bff.allocationSimulations.forRebalance`; `bff.watchers.forSubject`; `bff.mcpSecrets.forServer` | Keep live mode empty/null. Do not expose seed secrets or dev-only simulation/watchers as live data. |
| Governance/policy adjuncts deferred | `bff.routePolicies.list`; `bff.routePolicies.get`; `bff.policyVersions.list`; `bff.permissionMatrix.get`; `bff.permissionMatrices.list`; `bff.consultRules.list`; `bff.consultRules.get`; `bff.policyViolations.list`; `bff.policyViolations.forSubject` | `BFF-CONSOL-028` must either add safe live BFF routes, fold each helper into a verified detail DTO, or keep the UI explicitly unavailable in strict live mode. |
| Memory/evaluation/object adjuncts deferred | `bff.memoryUpdates.list`; `bff.evaluationRuns.list`; `bff.evaluationRuns.forSubject`; `bff.objectVersions.forSubject` | Some entries name candidate routes in taxonomy, but parent 025 did not promote them to accepted live-required behavior. BFF-CONSOL-028 must verify DTO shape and tests before changing category behavior. |
| Evolution/fitness adjuncts deferred | `bff.evolutionRuns.list`; `bff.evolutionCandidates.forRun`; `bff.fitnessFormulas.list`; `bff.fitnessFormulas.get`; `bff.mutationRules.list`; `bff.promotions.forProgram` | Route or hide under BFF-CONSOL-028. Candidate routes in taxonomy are not enough without parent-owned read tests and UI empty-state behavior. |
| Capital/rebalance adjuncts deferred | `bff.featureSets.forStrategy`; `bff.performanceSeries.forStrategy`; `bff.allocationLimits.forPool`; `bff.poolFreezes.forPool`; `bff.metricFreezes.forRebalance`; `bff.rebalanceOverrides.forRebalance` | Keep empty/unavailable until live route, detail DTO fold-in, or strict-live removal is reviewed. Do not imply live capital mutability. |

## Operator Journey

### Live-required helper succeeds

```text
Operator opens a management detail or list page
  -> UI calls a legacy helper, for example bff.strategies.list()
  -> seed.ts sees VITE_BFF_MODE=live
  -> helper uses liveListOrSeed/liveDetailOrSeed/liveDerivedListOrSeed
  -> strictLiveRead calls bffFetch with mode: "live"
  -> BFF returns the route payload
  -> helper adapts data/items/detail arrays and returns live data to the UI
  -> no seed rows are read for that helper
```

### Live-required helper fails

```text
Operator opens the same page while the BFF route is down or rejects the request
  -> strictLiveRead catches the transport or BffError
  -> liveStatus records a strict transport failure
  -> helper throws the typed BFF error
  -> UI must show an error or unavailable state
  -> helper must not return seed data as live truth
```

### Deferred or mock-only-dev helper in live mode

```text
Operator opens a panel backed by a deferred helper
  -> helper checks seedHelperMustReturnEmptyInLive(helperName)
  -> seed.ts returns [] / undefined / null immediately
  -> UI can call getSeedHelperUnavailableReason(helperName)
  -> unavailable reason points to BFF-CONSOL-028 or development-only disabled behavior
  -> no seed rows are displayed as live data
```

### Removed write helper

```text
Operator triggers a governed write
  -> UI must not call bff.mutations or bff.commands.requestConfirmToken
  -> write path uses direct mutations import, runActionSafe, bffWrites, /bff/actions/*,
     or /bff/v1/commands depending on the BFF-CONSOL-019/020/021/024 stage
  -> command/receipt tests own idempotency, confirm token, and approval evidence behavior
```

## Frontend Handoff Notes

- Treat `../execute-plans/src/lib/bff-v1/seed.ts` and
  `../execute-plans/src/lib/bff-v1/seedTaxonomy.ts` as the live seed-gating
  seam.
- Strict staging is expected to use `VITE_BFF_MODE=live` plus
  `VITE_BFF_FALLBACK=strict`. The seed gate keys off `VITE_BFF_MODE=live`.
- Keep taxonomy JSON synchronized between `docs/bff/seed-taxonomy.json` and
  sibling `src/lib/bff-v1/seed-taxonomy.json` whenever category assignments
  change.
- Do not add new `bff.*` helpers without a taxonomy row and an explicit
  live-mode behavior.
- Do not reclassify a deferred helper to `live_required` just because a route
  path exists. The parent owner must prove the response shape, UI adaptation,
  typed error behavior, and strict-live test coverage.
- UI components rendering deferred/mock-only-dev surfaces should use
  `getSeedHelperUnavailableReason()` or the existing mock-data empty-state
  components instead of silently hiding failure behind seeded content.
- The final BFF consolidation packet should cite `docs/bff/seed-elimination-2026-05-13.md`
  for seed post-state and cite this packet only for handoff details.

## Current Worktree Caveat

The sibling `../execute-plans` worktree currently has dirty files including
`src/lib/bff-v1/seed.ts` and `src/lib/bff-v1/paths.ts` while `BFF-CONSOL-028`
is in progress in `ai-status.json`. This packet uses the archived parent
BFF-CONSOL-025 baseline and the reviewed parent evidence as the scope of truth.
It does not approve or reject the in-progress BFF-CONSOL-028 route experiments.

## Recommended Parent/Downstream Verification

For BFF-CONSOL-025 baseline rechecks from `../execute-plans`:

```bash
npm test -- src/lib/bff-v1/__tests__/seedTaxonomy.test.ts src/lib/bff-v1/__tests__/lists.test.ts src/lib/bff/__tests__/client.test.ts src/lib/bff-v1/__tests__/writes.test.ts src/lib/v4/h1-wiring.test.ts
npm run build
```

For Pantheon-side taxonomy/evidence checks:

```bash
jq empty docs/bff/seed-taxonomy.json
jq '[.helpers | length, (.helpers | group_by(.category) | map({category:.[0].category,count:length}))]' docs/bff/seed-taxonomy.json
```

For BFF-CONSOL-028 absorption, add focused tests for each promoted helper
family before changing taxonomy category or UI behavior.

## Reviewer Checklist for This Sidecar

- Confirm this packet only adds
  `support/sidecars/BFF-CONSOL-025/BFF-CONSOL-025-SIDECAR-BFF-HANDOFF.md`.
- Confirm it treats BFF-CONSOL-025 as archived `done` and does not reopen parent
  runtime scope.
- Confirm live-required, mock-only-dev, deprecated, and deferred behaviors match
  the parent review and seed elimination evidence.
- Confirm BFF-CONSOL-028 remains the owner for unresolved adjunct helper
  routing/hide decisions.
- Confirm no L1 canonical truth, route manifest, BFF runtime implementation,
  registry code, governance code, or execute-plans source is modified by this
  sidecar.

## Verification for This Sidecar

Performed as read-only context checks plus support artifact creation:

```bash
cat AI_COLLABORATION_GUIDE.md
cat .orchestrator/task-briefs/bff_consol_025_sidecar_bff_handoff.md
cat .orchestrator/skills/task-closeout-finalization.md
cat ai-status.json
jq '.tasks[] | select(.id=="BFF-CONSOL-025-SIDECAR-BFF-HANDOFF")' ai-status.json
jq '.' ai-task-archive/tasks/BFF-CONSOL-025.json
sed -n '1,260p' support/sidecars/BFF-CONSOL-025/BFF-CONSOL-025-REVIEW.md
sed -n '1,300p' docs/bff/seed-elimination-2026-05-13.md
jq '{source, summary, categories: (.helpers | group_by(.category) | map({category:.[0].category,count:length,names:map(.name)}))}' docs/bff/seed-taxonomy.json
sed -n '1,760p' /home/lupin/code/execute-plans/src/lib/bff-v1/seed.ts
sed -n '1,260p' /home/lupin/code/execute-plans/src/lib/bff-v1/seedTaxonomy.ts
sed -n '1,260p' /home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts
sed -n '1,260p' /home/lupin/code/execute-plans/src/lib/bff-v1/__tests__/seedTaxonomy.test.ts
git status --short  # from /home/lupin/code/pantheon
git status --short  # from /home/lupin/code/execute-plans
```

Final sidecar checks run before handoff:

```bash
git diff --no-index --check /dev/null support/sidecars/BFF-CONSOL-025/BFF-CONSOL-025-SIDECAR-BFF-HANDOFF.md
LC_ALL=C grep -nP '[^\x00-\x7F]' support/sidecars/BFF-CONSOL-025/BFF-CONSOL-025-SIDECAR-BFF-HANDOFF.md
```

Observed results:

- `git diff --no-index --check` produced no whitespace-error output.
- ASCII scan produced no matches.
- Scoped `git status --short` shows this sidecar artifact as the only
  task-owned path added by Codex2. Existing dirty Pantheon and sibling frontend
  files are outside this sidecar scope.
- The sibling frontend dirty `seed.ts` / `paths.ts` state is attributed here
  only as a BFF-CONSOL-028 caveat, not as BFF-CONSOL-025 support approval.

No runtime tests were run for this sidecar because it is a support-only handoff
packet and does not modify BFF or frontend implementation files.
