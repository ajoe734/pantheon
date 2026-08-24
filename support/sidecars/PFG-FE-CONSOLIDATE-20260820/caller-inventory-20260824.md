# Frontend Caller Inventory & Simplification Disposition (Sidecar)

**Task ID:** `PFG-FE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY`  
**Parent Task:** `PFG-FE-CONSOLIDATE-20260820` — Retire replacement-proven frontend mock, duplicate, and legacy production paths  
**Target Repository:** `ajoe734/execute-plans`  
**Owner:** `Antigravity2`  
**Reviewer:** `Codex2`  
**Helper Kind:** `caller_inventory`  
**Created:** `2026-08-24`  
**Status:** `ready_for_review`  

> **Authority & Scope Boundary Notice:**  
> This document is a read-only support sidecar artifact. It makes no product source, configuration, deployment, deletion, or canonical parent-task state mutations. Implementation and deletion authority remain exclusively with the parent task `PFG-FE-CONSOLIDATE-20260820` after its declared dependencies and replacement journey proofs are satisfied.

---

## 1. Executive Summary & Inventory Principles

This caller inventory provides a comprehensive audit of candidate frontend paths, components, adapter modules, and mock mechanisms across `execute-plans` (covering Agora, Management Console, `lib/bff`, `lib/bff-v1`, and test fixture libraries).

Every candidate is evaluated against strict caller-traceability rules defined in `SD_IMPLEMENTATION_DESIGN_2026-08-24.md` §9 and `02_CODE_DISPOSITION_AND_SIMPLIFICATION_2026-08-20.md`:

1. **`retain`**: Canonical implementation; retains exclusive production responsibility.
2. **`replace_then_delete`**: Callers are migrated to the canonical implementation; legacy code is retired after replacement journey verification passes.
3. **`delete`**: No legitimate production, test, workflow, deployment, or documentation caller remains; synthetic/fake success shims removed directly.
4. **`defer`**: Ownership or behavior remains ambiguous; preserved without modification pending further evidence.

---

## 2. Candidate Summary Matrix

| # | Candidate Path / Symbol | Subsystem | Current Behavior | Callers Count | Target Disposition | Replacement / Canonical Target |
|---|---|---|---|---|---|---|
| 1 | `src/agora/pages/trading-room/TradingRoomPage.tsx:CandidateReviewDrawer` | Agora Trading Room | Page-local inline drawer updating React state | 1 (page inline) | `replace_then_delete` | `src/agora/components/CandidateReviewDrawer.tsx` |
| 2 | `src/agora/components/CandidateReviewDrawer.tsx` | Agora Review | Canonical BFF-wired A2 score decomposition drawer | 2 (unit test + target page) | `retain` | Canonical implementation |
| 3 | `src/agora/pages/trading-room/TradingRoomPage.tsx:STRATEGY_LENSES` | Agora Trading Room | Fixed `lens-A..E` candidate pool identity & static counts | 6 internal call sites | `replace_then_delete` | Dynamic workspace/pool recipes (`workshops.ts`, `candidatePool.ts`) |
| 4 | `src/lib/bff-v1/management.ts:safeAdapt` | Management BFF-v1 | Catches adapter errors on 200 responses and falls back to seed | 24 call sites in `management.ts` | `delete` | Typed error envelopes / `withStrictLiveOrMock` |
| 5 | `src/management/pages/studios/FormulaStudio.tsx:triggerBacktest` | Management Studios | Synthetic in-memory job runner with 5s timeout & fake event | 1 button handler | `replace_then_delete` | Governed backtest runner API / `NonProductionActionButton` |
| 6 | `src/management/components/detail/ActivityMonitor.tsx:seed` | Management Detail | Hardcoded synthetic events with fake pulsing "live" badge | 3 detail pages | `delete` | Canonical audit/SSE feed (`/bff/audit`, `/bff/sse/events`) |
| 7 | `src/management/pages/phase2/PostmortemLibrary.tsx:SEED` | Management Oversight | Static 3-row mock postmortem array | 1 page route | `replace_then_delete` | Canonical BFF postmortem/incident adapter (`/bff/incidents`) |
| 8 | `src/mocks/seed.ts` | Test / Mock Fixture | Domain mock data for 40+ entities | 14 test/mock callers | `retain` (fixture-only) | Tree-shaken from live bundle; retained for unit tests |
| 9 | `src/lib/bff/` vs `src/lib/bff-v1/` Overlap | BFF Client Layer | Duplicate legacy client & mutation overlays coexisting with `bff-v1` | 50+ legacy imports | `replace_then_delete` | Canonical `src/lib/bff-v1/` adapters |
| 10 | `src/lib/bff-v1/runActionSafe.ts` | Management Mutations | Toast-aware mutation wrapper with idempotency headers | 18 production call sites | `retain` | Canonical implementation |
| 11 | `src/management/components/NonProductionActionButton.tsx` | UI Safety Guard | Honestly disabled action button with tooltip explanation | 61 production call sites | `retain` | Canonical implementation |
| 12 | `src/management/components/agent/uiActionRegistry.ts` | Management AI | Allowlisted UI action registry with 7 contract types | Assistant NL drawer | `retain` | Canonical implementation |

---

## 3. Detailed Inventory Records (SD §9.2 Schema)

### 3.1 Agora Page-Local Inline Candidate Review Drawer

```json
{
  "path_or_symbol": "src/agora/pages/trading-room/TradingRoomPage.tsx:CandidateReviewDrawer",
  "behavior": "Inline drawer component (lines 1169-1370) managing candidate review in component React state (onUpdateState) with symbol-matching heuristics for dummy strategy assignment, bypassing backend candidate pool APIs.",
  "callers": [
    "src/agora/pages/trading-room/TradingRoomPage.tsx:2390",
    "src/agora/pages/trading-room/TradingRoomPage.test.tsx"
  ],
  "runtime_or_deploy_refs": [
    "Route /trading-room candidate review drawer interaction"
  ],
  "replacement": "src/agora/components/CandidateReviewDrawer.tsx",
  "replacement_proof": "src/agora/components/CandidateReviewDrawer.test.tsx (26 tests passing) verifies A2 score decomposition, top positive/penalty drivers, data quality badge, and review mutations via reviewCandidateMember with If-Match concurrency headers.",
  "disposition": "replace_then_delete",
  "validation": [
    "npm test src/agora/components/CandidateReviewDrawer.test.tsx",
    "npm test src/agora/pages/trading-room/TradingRoomPage.test.tsx",
    "Agora browser journey E2E rerun"
  ]
}
```

### 3.2 Agora Shared Canonical Candidate Review Drawer

```json
{
  "path_or_symbol": "src/agora/components/CandidateReviewDrawer.tsx:CandidateReviewDrawer",
  "behavior": "Canonical BFF-wired candidate pool review drawer implementing A2 score decomposition (spec §10), component rows with weights/contributions, and review decisions via AG-BE-CP-001 endpoints.",
  "callers": [
    "src/agora/components/CandidateReviewDrawer.test.tsx",
    "Adoption target: src/agora/pages/trading-room/TradingRoomPage.tsx"
  ],
  "runtime_or_deploy_refs": [
    "Agora Candidate Pool review surface"
  ],
  "replacement": "Canonical implementation",
  "replacement_proof": "src/agora/components/CandidateReviewDrawer.test.tsx (26 tests passed), src/lib/bff-v1/agora/candidatePool.test.ts",
  "disposition": "retain",
  "validation": [
    "npm test src/agora/components/CandidateReviewDrawer.test.tsx"
  ]
}
```

### 3.3 Agora Fixed `lens-A..E` Pool Identity

```json
{
  "path_or_symbol": "src/agora/pages/trading-room/TradingRoomPage.tsx:STRATEGY_LENSES",
  "behavior": "Static array of 5 lenses (lens-A through lens-E) with hardcoded candidate/held metrics and fixed candidate arrays used as synthetic candidate pool identities.",
  "callers": [
    "src/agora/pages/trading-room/TradingRoomPage.tsx:122-208,695-915,2052-2056,2155-2225,2265-2300,2810",
    "src/agora/pages/trading-room/TradingRoomPage.test.tsx"
  ],
  "runtime_or_deploy_refs": [
    "Trading Room lens strip and recipe dashboard cards"
  ],
  "replacement": "Dynamic candidate pool & workspace recipe projections from src/lib/bff-v1/agora/workshops.ts, candidatePool.ts, and workspaceChartSpec.ts",
  "replacement_proof": "Backend Agora candidate pool routes (/bff/agora/candidate-pools/*) and tests in services/control-plane/bff/test_bff_agora_core_contract.py",
  "disposition": "replace_then_delete",
  "validation": [
    "npm test src/agora/pages/trading-room/TradingRoomPage.test.tsx",
    "Agora browser journey E2E rerun"
  ]
}
```

### 3.4 Management `safeAdapt` Silent Seed Fallback

```json
{
  "path_or_symbol": "src/lib/bff-v1/management.ts:safeAdapt",
  "behavior": "Catches runtime exceptions and nulls during live response adaptation and returns seedFn(), masking live backend contract mismatches in mock data.",
  "callers": [
    "src/lib/bff-v1/management.ts:941-965 (wrapping adaptCockpit, adaptRankings, adaptTradingPulseOverview, adaptManagementEvidenceOverview, adaptManagementEvidenceDetail, adaptPersonaIntent, adaptReadiness, etc.)"
  ],
  "runtime_or_deploy_refs": [
    "Management console live reads under VITE_BFF_MODE=live"
  ],
  "replacement": "withStrictLiveOrMock with typed degradation envelopes, optionalAdapt, or explicit error boundaries",
  "replacement_proof": "src/lib/bff-v1/__tests__/strictLiveReadOffline.test.ts, src/lib/bff-v1/__tests__/management.test.ts",
  "disposition": "delete",
  "validation": [
    "npm test src/lib/bff-v1/__tests__/strictLiveReadOffline.test.ts",
    "npm test src/lib/bff-v1/__tests__/management.test.ts"
  ]
}
```

### 3.5 Formula Studio Synthetic Backtest Job Runner

```json
{
  "path_or_symbol": "src/management/pages/studios/FormulaStudio.tsx:triggerBacktest",
  "behavior": "Dynamically imports @/mocks/seed, pushes an in-memory job to seedModule.jobs, uses a 5-second setTimeout to mark it success, and emits a synthetic v5.loop.execution event.",
  "callers": [
    "src/management/pages/studios/FormulaStudio.tsx:47"
  ],
  "runtime_or_deploy_refs": [
    "Formula Studio route /management/studios/formula"
  ],
  "replacement": "Governed backend job runner (POST /bff/jobs / services/optimizer-svc) with terminal readback, or honest disabled state via NonProductionActionButton",
  "replacement_proof": "NonProductionActionButton pattern across 61 management buttons; backend job runner contracts in services/control-plane/bff/",
  "disposition": "replace_then_delete",
  "validation": [
    "Formula Studio component tests",
    "Management E2E browser journey"
  ]
}
```

### 3.6 Activity Monitor Synthetic Events & Pulsing Live Badge

```json
{
  "path_or_symbol": "src/management/components/detail/ActivityMonitor.tsx:seed",
  "behavior": "Generates 3 hardcoded static events (seed_${scope}_1..3) and renders a green pulsing live indicator when disconnected from real backend telemetry.",
  "callers": [
    "src/management/pages/PersonaDetail.tsx",
    "src/management/pages/McpDetail.tsx",
    "src/management/pages/ToolDetail.tsx"
  ],
  "runtime_or_deploy_refs": [
    "Detail page Activity tab"
  ],
  "replacement": "Canonical audit & event telemetry feeds (GET /bff/audit, /bff/events/stream, or SSE /bff/sse/events)",
  "replacement_proof": "Contract tests in services/control-plane/bff/test_pkt005_sse_substrate_contract.py",
  "disposition": "delete",
  "validation": [
    "PersonaDetail, McpDetail, ToolDetail component tests",
    "SSE stream integration tests"
  ]
}
```

### 3.7 Postmortem Library Static Fixture Array

```json
{
  "path_or_symbol": "src/management/pages/phase2/PostmortemLibrary.tsx:SEED",
  "behavior": "Static array of 3 hardcoded postmortem records (pm_001, pm_002, pm_003) used as the sole data source for the Postmortem Library page.",
  "callers": [
    "src/management/pages/phase2/PostmortemLibrary.tsx:26-30",
    "Route /management/postmortems in App.tsx and managementRouteManifest.ts"
  ],
  "runtime_or_deploy_refs": [
    "Postmortem Library route /management/postmortems"
  ],
  "replacement": "Canonical Incident / Postmortem BFF read adapter (GET /bff/incidents or bff-v1 postmortem adapter)",
  "replacement_proof": "INC-001-RB / services/control-plane/bff/test_inc001_rebaseline_incidents_contract.py",
  "disposition": "replace_then_delete",
  "validation": [
    "PostmortemLibrary and IncidentDetail tests"
  ]
}
```

### 3.8 Mock Data Fixture Library (`src/mocks/seed.ts`)

```json
{
  "path_or_symbol": "src/mocks/seed.ts",
  "behavior": "Comprehensive in-memory mock fixture dataset covering 40+ domain entities (personas, strategies, capital pools, runtimes, incidents, watchers, decision journals, performance series, etc.).",
  "callers": [
    "src/test/e2e-scenarios.test.ts",
    "src/lib/v5/__tests__/*",
    "src/lib/bff/mutations.test.ts",
    "src/lib/bff-v1/seed.ts (mock mode only)",
    "src/lib/bff/client.ts"
  ],
  "runtime_or_deploy_refs": [
    "Demo mode (VITE_BFF_MODE=mock) and unit test mocks"
  ],
  "replacement": "Retained as test/demo fixture library; excluded from strict-live production build chunks",
  "replacement_proof": "Bundle budget checks (scripts/bundle-budget-check.mjs, contract-drift-check.mjs)",
  "disposition": "retain",
  "validation": [
    "npm run build",
    "Bundle tree-shaking check proving seed is absent from strict-live production bundle"
  ]
}
```

### 3.9 BFF Client Layer Overlap (`src/lib/bff/` vs `src/lib/bff-v1/`)

```json
{
  "path_or_symbol": "src/lib/bff/",
  "behavior": "Legacy client, in-memory mutation overlays, and simulation scenarios coexisting with canonical src/lib/bff-v1/ live REST/SSE adapters.",
  "callers": [
    "50+ management components importing types/mutations from src/lib/bff/"
  ],
  "runtime_or_deploy_refs": [
    "Management console pages, Platform components, Agora components"
  ],
  "replacement": "src/lib/bff-v1/ canonical endpoints and typed DTOs",
  "replacement_proof": "src/lib/bff-v1/__tests__/* comprehensive test suites (lists, writes, sse, degradation, tradeJourneys, capitalPools, managementAi)",
  "disposition": "replace_then_delete",
  "validation": [
    "npm test",
    "npm run build"
  ]
}
```

### 3.10 UI Mutation Action Wrapper (`src/lib/bff-v1/runActionSafe.ts`)

```json
{
  "path_or_symbol": "src/lib/bff-v1/runActionSafe.ts:runActionSafe",
  "behavior": "Canonical UI-facing mutation wrapper that auto-injects correlation and idempotency headers and surfaces rejections via user-facing toasts while maintaining typed MutationResult.",
  "callers": [
    "src/management/pages/CapitalPoolDetail.tsx",
    "src/management/pages/EvolutionDetail.tsx",
    "src/management/pages/RankingFormulaDetail.tsx",
    "src/management/pages/Runtimes.tsx",
    "src/management/pages/DeploymentDetail.tsx",
    "src/management/pages/ResearchDetail.tsx",
    "src/management/components/detail/StrategyParamsEditor.tsx",
    "src/management/pages/ArtifactDetail.tsx",
    "src/management/pages/Operations.tsx",
    "src/management/pages/StrategyDetail.tsx",
    "src/management/pages/RebalanceDetail.tsx"
  ],
  "runtime_or_deploy_refs": [
    "All management console mutation button click handlers"
  ],
  "replacement": "Canonical implementation",
  "replacement_proof": "src/lib/bff-v1/__tests__/writes.test.ts, src/management/pages/capabilitiesProductionTruth.test.ts",
  "disposition": "retain",
  "validation": [
    "npm test src/lib/bff-v1/__tests__/writes.test.ts"
  ]
}
```

### 3.11 Disabled Action Safety Guard (`src/management/components/NonProductionActionButton.tsx`)

```json
{
  "path_or_symbol": "src/management/components/NonProductionActionButton.tsx:NonProductionActionButton",
  "behavior": "Honestly disabled button component with tooltip explanation preventing user clicks on unbacked backend mutations.",
  "callers": [
    "61+ call sites across ChannelDetail, PersonaDetail, McpDetail, IncidentDetail, HookCronManager, KnowledgeInbox, Settings, WorkflowTemplates, MemoryGovernancePage, ConsultRulesPage, SkillDetail, ToolDetail, MutationRuleManager, FormulaStudio, etc."
  ],
  "runtime_or_deploy_refs": [
    "Management console non-production actions"
  ],
  "replacement": "Canonical implementation",
  "replacement_proof": "src/management/components/NonProductionActionButton.test.tsx, src/management/pages/capabilitiesProductionTruth.test.ts",
  "disposition": "retain",
  "validation": [
    "npm test src/management/components/NonProductionActionButton.test.tsx"
  ]
}
```

### 3.12 Management AI UI Action Registry (`src/management/components/agent/uiActionRegistry.ts`)

```json
{
  "path_or_symbol": "src/management/components/agent/uiActionRegistry.ts:AVAILABLE_UI_ACTIONS",
  "behavior": "Allowlisted UI action registry for Management AI assistant with 7 contract kinds (navigate, openDrawer, selectEntity, setFilter, focusPanel, refreshCurrentView, runBffAction). 4 actions are actively wired; 3 return explicit safe refusal messages.",
  "callers": [
    "src/management/components/nl/NlAssistantDrawer.tsx",
    "Management AI NL chat handlers"
  ],
  "runtime_or_deploy_refs": [
    "Management AI NL assistant drawer (/management/*)"
  ],
  "replacement": "Canonical implementation; retain and wire remaining actions during feature completion",
  "replacement_proof": "src/lib/bff-v1/__tests__/managementAi.test.ts",
  "disposition": "retain",
  "validation": [
    "Management AI journey tests"
  ]
}
```

---

## 4. Subsystem Caller Graphs & Dependency Flow

```text
+-----------------------------------------------------------------------------------+
|                                  AGORA SUBSYSTEM                                  |
|                                                                                   |
|  TradingRoomPage (inline CandidateReviewDrawer) --[replace_then_delete]-->       |
|                                                    src/agora/components/          |
|                                                    CandidateReviewDrawer.tsx      |
|                                                                                   |
|  TradingRoomPage (fixed lens-A..E) -------------[replace_then_delete]-->       |
|                                                    Dynamic Workshop/Pool Recipes  |
+-----------------------------------------------------------------------------------+

+-----------------------------------------------------------------------------------+
|                            MANAGEMENT CONSOLE SUBSYSTEM                           |
|                                                                                   |
|  safeAdapt (silent seed fallback) --------------[delete from live]---------->    |
|                                                    withStrictLiveOrMock / Error   |
|                                                                                   |
|  FormulaStudio (synthetic backtest runner) -----[replace_then_delete]-->       |
|                                                    Governed Jobs API / Disabled   |
|                                                                                   |
|  ActivityMonitor (seed events + fake live) -----[delete fake live]----------->    |
|                                                    Canonical Audit/SSE Feeds      |
|                                                                                   |
|  PostmortemLibrary (static SEED array) ---------[replace_then_delete]-->       |
|                                                    Canonical /bff/incidents       |
+-----------------------------------------------------------------------------------+

+-----------------------------------------------------------------------------------+
|                                BFF ADAPTER SUBSYSTEM                              |
|                                                                                   |
|  Legacy src/lib/bff/ ---------------------------[replace_then_delete]-->       |
|                                                    Canonical src/lib/bff-v1/      |
|                                                                                   |
|  runActionSafe ---------------------------------[retain canonical]--------->     |
|                                                    Production Mutations           |
|                                                                                   |
|  NonProductionActionButton ---------------------[retain safety guard]----->     |
|                                                    Honest Disabled Actions        |
|                                                                                   |
|  uiActionRegistry ------------------------------[retain allowlist]---------->     |
|                                                    Management AI Operations       |
|                                                                                   |
|  src/mocks/seed.ts -----------------------------[retain fixture only]------->     |
|                                                    Unit / Mock Test Suites        |
+-----------------------------------------------------------------------------------+
```

---

## 5. Handoff to Parent Task `PFG-FE-CONSOLIDATE-20260820`

1. **Preconditions for Parent Task Execution:**
   - Parent task `PFG-FE-CONSOLIDATE-20260820` starts after declared upstream dependencies (`PFG-AGORA-JOURNEY-E2E-20260820`, `PFG-MGMT-JOURNEY-E2E-20260820`) pass.
   - Parent task consumes this reviewed inventory directly.

2. **Parent Task Execution Sequence:**
   - **Step 1 (Agora):** Adopt `src/agora/components/CandidateReviewDrawer.tsx` in `TradingRoomPage.tsx`; remove inline drawer; replace hardcoded lens candidate pools with dynamic workshop recipes; rerun Agora tests.
   - **Step 2 (Management Live Hygiene):** Remove `safeAdapt` silent seed fallback in strict-live; remove static `seed` events and fake "live" badge in `ActivityMonitor.tsx`; wire `PostmortemLibrary.tsx` to canonical incident API; rerun Management tests.
   - **Step 3 (Adapter Convergence):** Migrate remaining `src/lib/bff/` callers to `src/lib/bff-v1/`; verify tree-shaking isolates `src/mocks/seed.ts` from strict-live production build bundle.
   - **Step 4 (Validation):** Execute full browser E2E test suites in both Agora and Management Console.

---

## 6. Verification Summary

Focused validation performed in `execute-plans`:
- `src/agora/components/CandidateReviewDrawer.test.tsx` (26 tests passed)
- `src/agora/pages/trading-room/TradingRoomPage.test.tsx` (77 tests passed)
- `src/management/components/NonProductionActionButton.test.tsx` (1 test passed)
- `src/lib/bff-v1/__tests__/writes.test.ts` (19 tests passed)
- `src/lib/bff-v1/__tests__/strictLiveReadOffline.test.ts` (2 tests passed)
- Total: **125 passed / 125 tests** across 5 test suites.
