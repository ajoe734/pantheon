# Frontend Caller Inventory & Simplification Disposition (Sidecar)

**Task ID:** `PFG-FE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY`  
**Parent Task:** `PFG-FE-CONSOLIDATE-20260820` — Retire replacement-proven frontend mock, duplicate, and legacy production paths  
**Target Repository:** `ajoe734/execute-plans`  
**Pinned Source SHA:** `0eec7659c9503ba3799ed5666cfa00f2b031e7fa` (`tag: release/v2026.08.24.2`, `origin/dev`)  
**Owner:** `Antigravity2`  
**Reviewer:** `Codex2`  
**Helper Kind:** `caller_inventory`  
**Created:** `2026-08-24`  
**Status:** `ready_for_review`  

> **Authority & Scope Boundary Notice:**  
> This document is a read-only support sidecar artifact. It makes no product source, configuration, deployment, deletion, or canonical parent-task state mutations. Implementation and deletion authority remain exclusively with the parent task `PFG-FE-CONSOLIDATE-20260820` after its declared dependencies and replacement journey proofs are satisfied.

---

## 1. Executive Summary & Inventory Principles

This caller inventory provides a comprehensive audit of candidate frontend paths, components, adapter modules, and mock mechanisms across `ajoe734/execute-plans` (covering Agora, Management Console, `src/lib/bff`, `src/lib/bff-v1`, and test fixture libraries) pinned to commit `0eec7659c9503ba3799ed5666cfa00f2b031e7fa`.

Every candidate is evaluated against strict caller-traceability rules defined in `SD_IMPLEMENTATION_DESIGN_2026-08-24.md` §9 and `02_CODE_DISPOSITION_AND_SIMPLIFICATION_2026-08-20.md`:

1. **`retain`**: Canonical implementation; retains exclusive production responsibility.
2. **`replace_then_delete`**: Callers are migrated to the canonical implementation; legacy code is retired after replacement journey verification passes.
3. **`delete`**: No legitimate production, test, workflow, deployment, or documentation caller remains; synthetic/fake success shims removed directly.
4. **`defer`**: Ownership or behavior remains ambiguous; preserved without modification pending further evidence.

---

## 2. Candidate Summary Matrix

| # | Candidate Path / Symbol | Subsystem | Current Behavior | Callers Count | Target Disposition | Replacement / Canonical Target |
|---|---|---|---|---|---|---|
| 1 | `src/agora/pages/trading-room/TradingRoomPage.tsx:CandidateReviewDrawer` (inline) | Agora Trading Room | Page-local inline drawer updating React state | 0 active callers (verified deleted) | `replace_then_delete` | `src/agora/components/CandidateReviewDrawer.tsx` |
| 2 | `src/agora/components/CandidateReviewDrawer.tsx` | Agora Review | Canonical BFF-wired A2 score decomposition drawer | 3 code files (1 prod page + 2 test files), 2 evidence docs | `retain` | Canonical implementation |
| 3 | `src/agora/pages/trading-room/TradingRoomPage.tsx:STRATEGY_LENSES` | Agora Trading Room | Fixed `lens-A..E` candidate pool identity & static counts | 0 callers (verified replaced) | `replace_then_delete` | Dynamic workspace/pool recipes (`workshops.ts`, `candidatePool.ts`) |
| 4 | `src/lib/bff-v1/management.ts:safeAdapt` | Management BFF-v1 | Catches adapter errors on 200 responses and falls back to seed (rethrows under strict-live) | 24 call sites in `management.ts`, 1 test suite, 8 audit/doc files | `replace_then_delete` | Typed error envelopes / `withStrictLiveOrMock` across 24 adapter call sites |
| 5 | `src/management/pages/studios/FormulaStudio.tsx:triggerBacktest` | Management Studios | Synthetic in-memory job runner with 5s timeout & fake event | 0 callers (verified retired) | `replace_then_delete` | Governed backtest runner API (`bff.jobs.list()`) / `NonProductionActionButton` |
| 6 | `src/management/components/detail/ActivityMonitor.tsx:seed` | Management Detail | Hardcoded synthetic events with fake pulsing "live" badge | 3 prod detail pages + 1 test file (component callers, seed generator deleted) | `delete` | Canonical audit/SSE feed (`/bff/audit`, `/bff/sse/events`) |
| 7 | `src/management/pages/phase2/PostmortemLibrary.tsx:SEED` | Management Oversight | Static 3-row mock postmortem array | 1 page component + 1 route re-export + 2 App.tsx routes + 2 nav manifests + 2 link helpers + 1 test + 3 i18n locales + 2 bff-v1 path/types + 1 script + 1 evidence doc | `replace_then_delete` | Canonical BFF postmortem/incident adapter (`bff.incidents.list()`) |
| 8 | `src/mocks/seed.ts` | Test / Mock Fixture | Domain mock data for 40+ entities | 14 import callers across 14 test/mock/adapter files | `retain` (fixture-only) | Tree-shaken from live bundle; retained for unit tests |
| 9 | `src/lib/bff/` vs `src/lib/bff-v1/` Overlap | BFF Client Layer | Duplicate legacy client & mutation overlays coexisting with `bff-v1` | 176 import sites across 147 files | `replace_then_delete` | Canonical `src/lib/bff-v1/` adapters |
| 10 | `src/lib/bff-v1/runActionSafe.ts` | Management Mutations | Toast-aware mutation wrapper with idempotency headers | 29 files (11 prod pages, 5 lib files, 5 tests, 2 scripts, 6 docs/evidence) | `retain` | Canonical implementation |
| 11 | `src/management/components/NonProductionActionButton.tsx` | UI Safety Guard | Honestly disabled action button with tooltip explanation | 69 literal JSX sites across 28 files (68 sites in 27 prod UI files + 1 in test file), 28 import files | `retain` | Canonical implementation |
| 12 | `src/management/components/agent/uiActionRegistry.ts` | Management AI | Allowlisted UI action registry with 7 contract types | 2 direct code files (1 prod component `AgentPanelBody.tsx`, 1 direct test suite `uiActionRegistry.test.ts` [47 tests]), 1 self-definition, 1 evidence doc; 2 broader context test suites (`useAgentPanel.test.ts`, `capabilitiesProductionTruth.test.ts`) | `retain` | Canonical implementation |

---

## 3. Detailed Inventory Records (SD §9.2 Schema)

### 3.1 Agora Page-Local Inline Candidate Review Drawer

```json
{
  "path_or_symbol": "src/agora/pages/trading-room/TradingRoomPage.tsx:CandidateReviewDrawer",
  "behavior": "Page-local inline drawer component (formerly lines 1169-1370) managing candidate review in component React state (onUpdateState) with symbol-matching heuristics for dummy strategy assignment, bypassing backend candidate pool APIs.",
  "callers": [
    "Formerly invoked within src/agora/pages/trading-room/TradingRoomPage.tsx",
    "Active callers: 0 (verified retired and replaced by SharedCandidateReviewDrawer)"
  ],
  "runtime_or_deploy_refs": [
    "Route /trading-room candidate review drawer interaction"
  ],
  "replacement": "src/agora/components/CandidateReviewDrawer.tsx",
  "replacement_proof": "src/agora/components/CandidateReviewDrawer.test.tsx (26 tests passing) verifies A2 score decomposition, top positive/penalty drivers, data quality badge, and review decisions via reviewCandidateMember with If-Match concurrency headers. src/agora/pages/trading-room/TradingRoomPage.test.tsx (77 tests passing) verifies TradingRoomPage renders SharedCandidateReviewDrawer.",
  "disposition": "replace_then_delete",
  "validation": [
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/agora/components/CandidateReviewDrawer.test.tsx",
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/agora/pages/trading-room/TradingRoomPage.test.tsx",
    "Reproducible negative scan: git -C /home/lupin/code/execute-plans grep -n 'function CandidateReviewDrawer' 0eec7659c9503ba3799ed5666cfa00f2b031e7fa (returns only canonical src/agora/components/CandidateReviewDrawer.tsx:557; 0 occurrences in TradingRoomPage.tsx)"
  ]
}
```

### 3.2 Agora Shared Canonical Candidate Review Drawer

```json
{
  "path_or_symbol": "src/agora/components/CandidateReviewDrawer.tsx:CandidateReviewDrawer",
  "behavior": "Canonical BFF-wired candidate pool review drawer implementing A2 score decomposition (spec §10), component rows with weights/contributions, data quality badge, and review decisions via AG-BE-CP-001 endpoints (reviewCandidateMember with If-Match concurrency headers).",
  "callers": [
    "src/agora/pages/trading-room/TradingRoomPage.tsx:23 (import { CandidateReviewDrawer as SharedCandidateReviewDrawer })",
    "src/agora/pages/trading-room/TradingRoomPage.tsx:1107 (<SharedCandidateReviewDrawer poolId={selectedPoolId} open={candidateDrawerOpen} onClose={() => setCandidateDrawerOpen(false)} />)",
    "src/agora/pages/trading-room/TradingRoomPage.test.tsx:29-30 (vi.mock('@/agora/components/CandidateReviewDrawer'))",
    "src/agora/components/CandidateReviewDrawer.test.tsx:4, 105, 117, 128, 141, 157, 298, 308, 327, 366, 387, 422, 439, 485 (26 unit test cases)",
    "docs/deployment/evidence/PFG-AGORA-FE-LIVE-20260820/evidence.json:11, 17, 26, 35",
    "docs/deployment/evidence/PFG-AGORA-JOURNEY-E2E-20260820/evidence.json:88, 89"
  ],
  "runtime_or_deploy_refs": [
    "Agora Candidate Pool review surface on route /trading-room"
  ],
  "replacement": "Canonical implementation",
  "replacement_proof": "src/agora/components/CandidateReviewDrawer.test.tsx (26 tests passed), src/lib/bff-v1/agora/candidatePool.test.ts, docs/deployment/evidence/PFG-AGORA-JOURNEY-E2E-20260820/evidence.json",
  "disposition": "retain",
  "validation": [
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/agora/components/CandidateReviewDrawer.test.tsx",
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/agora/pages/trading-room/TradingRoomPage.test.tsx"
  ]
}
```

### 3.3 Agora Fixed `lens-A..E` Pool Identity

```json
{
  "path_or_symbol": "src/agora/pages/trading-room/TradingRoomPage.tsx:STRATEGY_LENSES",
  "behavior": "Static array of 5 lenses (lens-A through lens-E) with hardcoded candidate/held metrics and fixed candidate arrays formerly used as synthetic candidate pool identities.",
  "callers": [
    "Formerly called across TradingRoomPage.tsx lens dashboard and card strips",
    "Active production callers: 0 (replaced by dynamic candidate pool projections)",
    "Evidence doc: docs/deployment/evidence/PFG-AGORA-FE-LIVE-20260820/evidence.json:16"
  ],
  "runtime_or_deploy_refs": [
    "Trading Room lens strip and recipe dashboard cards"
  ],
  "replacement": "Dynamic candidate pool & workspace recipe projections from src/lib/bff-v1/agora/workshops.ts, candidatePool.ts, and workspaceChartSpec.ts",
  "replacement_proof": "Backend Agora candidate pool routes (/bff/agora/candidate-pools/*) and tests in services/control-plane/bff/test_bff_agora_core_contract.py; frontend workshop integration in src/agora/pages/trading-room/TradingRoomPage.tsx",
  "disposition": "replace_then_delete",
  "validation": [
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/agora/pages/trading-room/TradingRoomPage.test.tsx",
    "Reproducible negative scan: git -C /home/lupin/code/execute-plans grep -n 'STRATEGY_LENSES' 0eec7659c9503ba3799ed5666cfa00f2b031e7fa (returns 0 occurrences across entire codebase)"
  ]
}
```

### 3.4 Management `safeAdapt` Silent Seed Fallback

```json
{
  "path_or_symbol": "src/lib/bff-v1/management.ts:safeAdapt",
  "behavior": "Catches runtime exceptions and nulls during live response adaptation and returns seedFn(), masking live backend contract mismatches in mock data when not in strict live mode. In strict live mode (isStrictLiveFallback()), it rethrows the adapter mismatch instead of returning seedFn(), allowing withLiveOrMock to return typed degradation/error envelopes.",
  "callers": [
    "src/lib/bff-v1/management.ts:3854 (adaptCockpit)",
    "src/lib/bff-v1/management.ts:3933 (adaptRankings)",
    "src/lib/bff-v1/management.ts:3942 (adaptTradingPulseOverview)",
    "src/lib/bff-v1/management.ts:3988 (adaptArrayPassthrough<T>)",
    "src/lib/bff-v1/management.ts:3997 (adaptArrayPassthrough<T>)",
    "src/lib/bff-v1/management.ts:4005 (adaptManagementEvidenceOverview)",
    "src/lib/bff-v1/management.ts:4019 (adaptManagementEvidenceDetail)",
    "src/lib/bff-v1/management.ts:4033 (adaptPersonaIntent)",
    "src/lib/bff-v1/management.ts:4049 (adaptReadiness)",
    "src/lib/bff-v1/management.ts:4057 (adaptReadiness)",
    "src/lib/bff-v1/management.ts:4065 (adaptReadiness)",
    "src/lib/bff-v1/management.ts:4073 (adaptReadiness)",
    "src/lib/bff-v1/management.ts:4081 (adaptReadiness)",
    "src/lib/bff-v1/management.ts:4100 (raw inline adapter)",
    "src/lib/bff-v1/management.ts:4114 (adaptArrayPassthrough<CapitalPoolSummaryRow>)",
    "src/lib/bff-v1/management.ts:4125 (adaptArrayPassthrough<HoldingRow>)",
    "src/lib/bff-v1/management.ts:4174 (adaptPortfolioHoldingsMonitor)",
    "src/lib/bff-v1/management.ts:4188 (adaptArrayPassthrough<PersonaLeagueRow>)",
    "src/lib/bff-v1/management.ts:4199 (adaptArrayPassthrough<PersonaLeagueRow>)",
    "src/lib/bff-v1/management.ts:4205 (raw inline adapter)",
    "src/lib/bff-v1/management.ts:4229 (adaptQuarterlyRankingRows)",
    "src/lib/bff-v1/management.ts:4244 (raw inline adapter)",
    "src/lib/bff-v1/management.ts:4257 (adaptQuarterlyRankingRows)",
    "src/lib/bff-v1/management.ts:4314 (adaptArrayPassthrough<PerformanceAttributionRow>)",
    "src/lib/bff-v1/__tests__/management.test.ts:1711, 1712 (safeAdapt strict-live contract mismatch unit tests)",
    ".lovable/audits/bff-backend-gap-2026-05-23.md:23, 223",
    ".lovable/audits/bff-backend-gap-2026-05-24-delta.md:120, 165",
    ".lovable/audits/bff-backend-gap-2026-05-25-delta-v3.md:199",
    ".lovable/audits/bff-backend-gap-2026-05-25-delta-v5.md:86",
    ".lovable/audits/mgmt-revamp-2026-05-20-plan.md:78, 94",
    "docs/deployment/evidence/PFG-FE-HONEST-LIVE-20260820/caller-inventory.md:31, 39, 90",
    "docs/deployment/evidence/PFG-FE-HONEST-LIVE-20260820/evidence.json:13, 19, 43"
  ],
  "runtime_or_deploy_refs": [
    "Management console live reads under VITE_BFF_MODE=live with VITE_BFF_FALLBACK=strict"
  ],
  "replacement": "withStrictLiveOrMock with typed degradation envelopes, optionalAdapt, or explicit error boundaries across 24 management.ts adapter call sites",
  "replacement_proof": "src/lib/bff-v1/__tests__/strictLiveReadOffline.test.ts (2 tests passing), src/lib/bff-v1/__tests__/management.test.ts (34 tests passing) proving strict-live contract mismatch propagation and envelope handling without silent seed masking",
  "disposition": "replace_then_delete",
  "validation": [
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/lib/bff-v1/__tests__/strictLiveReadOffline.test.ts",
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/lib/bff-v1/__tests__/management.test.ts",
    "Reproducible negative test scan: npm --prefix /home/lupin/code/execute-plans test -- --run src/lib/bff-v1/__tests__/management.test.ts -t 'safeAdapt strict-live contract mismatch' (verifies safeAdapt rethrows on 200 contract mismatch under strict-live fallback)"
  ]
}
```

### 3.5 Formula Studio Synthetic Backtest Job Runner

```json
{
  "path_or_symbol": "src/management/pages/studios/FormulaStudio.tsx:triggerBacktest",
  "behavior": "Synthetic in-memory job runner that formerly dynamically imported @/mocks/seed, pushed an in-memory job to seedModule.jobs, used a 5-second setTimeout to mark it success, and emitted a synthetic v5.loop.execution event.",
  "callers": [
    "Formerly invoked from FormulaStudio.tsx button onClick",
    "Active callers: 0 (replaced by loadBacktestJobs via bff.jobs.list() at lines 35-46 and NonProductionActionButton at lines 152-154)"
  ],
  "runtime_or_deploy_refs": [
    "Formula Studio route /management/studios/formula"
  ],
  "replacement": "Governed backend job runner (bff.jobs.list() / services/optimizer-svc) with terminal readback, and honest disabled state via NonProductionActionButton",
  "replacement_proof": "NonProductionActionButton pattern in FormulaStudio.tsx:152-154; verified by src/management/components/NonProductionActionButton.test.tsx (1 test passing) and src/management/pages/capabilitiesProductionTruth.test.ts (3 tests passing)",
  "disposition": "replace_then_delete",
  "validation": [
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/management/components/NonProductionActionButton.test.tsx",
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/management/pages/capabilitiesProductionTruth.test.ts",
    "Reproducible negative scan: git -C /home/lupin/code/execute-plans grep -n 'triggerBacktest' 0eec7659c9503ba3799ed5666cfa00f2b031e7fa (returns 0 matches across entire codebase)"
  ]
}
```

### 3.6 Activity Monitor Synthetic Events & Pulsing Live Badge

```json
{
  "path_or_symbol": "src/management/components/detail/ActivityMonitor.tsx:seed",
  "behavior": "Formerly generated 3 hardcoded static events (seed_${scope}_1..3) and rendered a green pulsing live indicator when disconnected from real backend telemetry. Replaced with canonical realtime and SSE subscriptions (realtime.on('job'|'data'|'sse:loop'|'sse:sentinel'|'sse:intervention')).",
  "callers": [
    "src/management/pages/PersonaDetail.tsx:28 (import ActivityMonitor), line 348 (<ActivityMonitor scope='persona' />)",
    "src/management/pages/McpDetail.tsx:16 (import ActivityMonitor), line 68 (<ActivityMonitor scope='mcp' />)",
    "src/management/pages/ToolDetail.tsx:14 (import ActivityMonitor), line 108 (<ActivityMonitor scope='tool' />)",
    "src/management/components/detail/__tests__/StrictLiveManagementSurfaces.test.tsx:5, 18, 29, 48, 60 (tests ActivityMonitor behavior under disconnected, connected, and SSE live streams)"
  ],
  "runtime_or_deploy_refs": [
    "Detail page Activity tab on /management/personas/:id, /management/mcps/:id, /management/tools/:id"
  ],
  "replacement": "Canonical audit & event telemetry feeds (realtime.on(...) consuming /bff/audit, /bff/events/stream, /bff/sse/events)",
  "replacement_proof": "Contract tests in services/control-plane/bff/test_pkt005_sse_substrate_contract.py and src/management/components/detail/__tests__/StrictLiveManagementSurfaces.test.tsx",
  "disposition": "delete",
  "validation": [
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/management/components/detail/__tests__/StrictLiveManagementSurfaces.test.tsx",
    "Reproducible negative scan: git -C /home/lupin/code/execute-plans grep -n 'seed_' 0eec7659c9503ba3799ed5666cfa00f2b031e7fa -- src/management/components/detail/ActivityMonitor.tsx (returns 0 matches; synthetic seed event generator eliminated)"
  ]
}
```

### 3.7 Postmortem Library Static Fixture Array

```json
{
  "path_or_symbol": "src/management/pages/phase2/PostmortemLibrary.tsx:SEED",
  "behavior": "Formerly used a static array of 3 hardcoded postmortem records (pm_001, pm_002, pm_003) as the sole data source for the Postmortem Library page. Replaced by live bff.incidents.list() adapter.",
  "callers": [
    "src/management/pages/phase2/PostmortemLibrary.tsx:28 (PostmortemLibraryPage component definition)",
    "src/routes/management/phase2.tsx:4 (export { PostmortemLibraryPage as PostmortemLibraryRoute } from '@/management/pages/phase2/PostmortemLibrary')",
    "src/App.tsx:135 (const PostmortemLibraryRoute = lazyNamedRoute(() => import('@/routes/management/phase2'), 'PostmortemLibraryRoute', 'Postmortems'))",
    "src/App.tsx:291 (<Route path='postmortems' element={<PostmortemLibraryRoute />} />)",
    "src/management/navigation/managementRouteManifest.ts:197 ({ id: 'postmortems', to: '/management/postmortems', labelKey: 'nav.postmortems', icon: FileText })",
    "src/lib/v4/routeLabels.ts:75 ({ path: '/management/postmortems', i18nKey: 'nav.postmortems', parent: '/management' })",
    "src/lib/v5/management/links.ts:126 (/management/postmortems?item=...)",
    "src/lib/v5/management/__tests__/links.test.ts:22 (unit test for postmortem route link resolution)",
    "src/management/components/detail/__tests__/StrictLiveManagementSurfaces.test.tsx:7, 79, 93, 104, 111 (tests PostmortemLibraryPage live rendering and degraded state)",
    "src/i18n/locales/en-US.ts:121, 2673, 2679",
    "src/i18n/locales/zh-TW.ts:116",
    "src/lib/bff-v1/paths.ts:129 (agoraPostmortems)",
    "src/lib/bff-v1/agora/types.ts:365, 841 (/bff/agora/postmortems)",
    "scripts/probe-bff-write-paths.mjs:108 (/bff/agora/postmortems)",
    "docs/deployment/evidence/PFG-MGMT-FE-REAL-20260820/evidence.json:11, 17"
  ],
  "runtime_or_deploy_refs": [
    "Postmortem Library route /management/postmortems"
  ],
  "replacement": "Canonical Incident / Postmortem BFF read adapter (bff.incidents.list() consuming /bff/incidents)",
  "replacement_proof": "INC-001-RB / services/control-plane/bff/test_inc001_rebaseline_incidents_contract.py and src/management/components/detail/__tests__/StrictLiveManagementSurfaces.test.tsx",
  "disposition": "replace_then_delete",
  "validation": [
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/management/components/detail/__tests__/StrictLiveManagementSurfaces.test.tsx",
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/lib/v5/management/__tests__/links.test.ts",
    "Reproducible negative scan: git -C /home/lupin/code/execute-plans grep -n 'const SEED' 0eec7659c9503ba3799ed5666cfa00f2b031e7fa -- src/management/pages/phase2/PostmortemLibrary.tsx (returns 0 matches; component consumes live bff.incidents.list() at line 36)"
  ]
}
```

### 3.8 Mock Data Fixture Library (`src/mocks/seed.ts`)

```json
{
  "path_or_symbol": "src/mocks/seed.ts",
  "behavior": "Comprehensive in-memory mock fixture dataset covering 40+ domain entities (personas, strategies, capital pools, runtimes, incidents, watchers, decision journals, performance series, etc.).",
  "callers": [
    "src/lib/bff-v1/lists.ts:13 (import * as seed from '@/mocks/seed')",
    "src/lib/bff-v1/seed.ts:18 (import * as seed from '@/mocks/seed')",
    "src/lib/bff-v1/tradeJournal.ts:4 (import * as seed from '@/mocks/seed')",
    "src/lib/bff/agora.ts:1 (import * as seed from '@/mocks/seed')",
    "src/lib/bff/client.ts:49 (import * as seed from '@/mocks/seed')",
    "src/lib/bff/mutations.test.ts:3 (import * as seed from '@/mocks/seed')",
    "src/lib/bff/mutations.ts:6 (import * as seed from '@/mocks/seed')",
    "src/lib/bff/persistence.ts:9 (import * as seed from '@/mocks/seed')",
    "src/lib/bff/scenarios.ts:11 (import * as seed from '@/mocks/seed')",
    "src/lib/bff/v5.ts:6 (import * as seed from '@/mocks/seed')",
    "src/lib/bff/writeOverlay.ts:10 (import { auditEvents } from '@/mocks/seed')",
    "src/lib/v5/__tests__/overlay.test.ts:3 (import * as seed from '@/mocks/seed')",
    "src/lib/v5/__tests__/sentinel.test.ts:3 (import * as seed from '@/mocks/seed')",
    "src/test/e2e-scenarios.test.ts:5 (import * as seed from '@/mocks/seed')"
  ],
  "runtime_or_deploy_refs": [
    "Demo mode (VITE_BFF_MODE=mock) and unit test mocks; excluded from strict-live production build chunks"
  ],
  "replacement": "Retained as test/demo fixture library; excluded from strict-live production build chunks",
  "replacement_proof": "Bundle budget checks (scripts/bundle-budget-check.mjs, scripts/contract-drift-check.mjs)",
  "disposition": "retain",
  "validation": [
    "npm --prefix /home/lupin/code/execute-plans run build",
    "node /home/lupin/code/execute-plans/scripts/bundle-budget-check.mjs"
  ]
}
```

### 3.9 BFF Client Layer Overlap (`src/lib/bff/` vs `src/lib/bff-v1/`)

```json
{
  "path_or_symbol": "src/lib/bff/",
  "behavior": "Legacy client types, in-memory mutation overlays, and simulation scenarios in src/lib/bff/ coexisting with canonical src/lib/bff-v1/ live REST/SSE adapters.",
  "callers": [
    "src/components/data/MockDataBadge.test.tsx:10",
    "src/components/data/MockDataBadge.tsx:5",
    "src/components/data/mockDataBadgeModel.ts:4",
    "src/components/layout/LiveStatusBanner.tsx:22",
    "src/lib/bff-v1/__tests__/lists.test.ts:13",
    "src/lib/bff-v1/agora/identity.ts:6",
    "src/lib/bff-v1/capitalPools.ts:1",
    "src/lib/bff-v1/eventTimestamps.ts:1",
    "src/lib/bff-v1/lists.ts:14",
    "src/lib/bff-v1/management.ts:15",
    "src/lib/bff-v1/managementConsoleReads.ts:1, 9",
    "src/lib/bff-v1/personas.ts:1",
    "src/lib/bff-v1/runActionSafe.ts:12",
    "src/lib/bff-v1/seed.ts:19, 21, 22",
    "src/lib/bff-v1/sse/liveSse.ts:11",
    "src/lib/bff-v1/useLiveList.ts:8",
    "src/lib/bff-v1/useLiveListV1.ts:10",
    "src/lib/bff-v1/v5.ts:14",
    "src/lib/bff-v1/writeFallback.ts:9",
    "src/lib/bff-v1/writes.ts:12, 25",
    "src/lib/stateMachines/types.ts:4",
    "src/lib/v4/__tests__/batch-iii.test.ts:9",
    "src/lib/v4/__tests__/batch-iv.test.ts:15",
    "src/lib/v4/__tests__/spec-conflict-c1-c7.test.ts:8",
    "src/lib/v4/auditImmutability.ts:5",
    "src/lib/v4/h1-wiring.test.ts:2",
    "src/lib/v4/h2-m-wiring.test.ts:29",
    "src/lib/v5/adapters/intervention.ts:4",
    "src/lib/v5/adapters/loopRun.ts:4",
    "src/lib/v5/adapters/persona.ts:3",
    "src/lib/v5/adapters/strategy.ts:3",
    "src/lib/v5/events.ts:4",
    "src/lib/v5/sentinel.ts:3",
    "src/lib/writeIntents/__tests__/writeOverlay.test.ts:2",
    "src/lib/writeIntents/__tests__/writeOverlay.ttl.test.ts:3",
    "src/management/components/agent/AgentPanelBody.test.tsx:5",
    "src/management/components/agent/AgentPanelBody.tsx:63",
    "src/management/components/detail/ActivityMonitor.tsx:6",
    "src/management/components/detail/AllocationLimitsManager.tsx:4, 5",
    "src/management/components/detail/AllocationSimulationPanel.tsx:8, 15",
    "src/management/components/detail/ArtifactDiffPanel.tsx:4",
    "src/management/components/detail/ArtifactRollbackPanel.tsx:8, 9",
    "src/management/components/detail/BindingsMatrix.tsx:5",
    "src/management/components/detail/ConstraintChecker.tsx:4",
    "src/management/components/detail/DeploymentStagesPanel.tsx:4",
    "src/management/components/detail/EvolutionCandidatesTab.tsx:11",
    "src/management/components/detail/EvolutionFreezePanel.tsx:7, 9",
    "src/management/components/detail/EvolutionRunsPanel.tsx:6",
    "src/management/components/detail/FitnessFormulaPanel.tsx:6, 12",
    "src/management/components/detail/FreezeUnfreezePanel.tsx:4, 5",
    "src/management/components/detail/MandatePanel.tsx:5",
    "src/management/components/detail/McpRegistryPanel.tsx:4",
    "src/management/components/detail/McpSecretsPanel.tsx:6, 12",
    "src/management/components/detail/McpServerSchemaPanel.tsx:8",
    "src/management/components/detail/MemoryGovernanceQueue.tsx:7",
    "src/management/components/detail/MetricFreezeManager.tsx:4, 5",
    "src/management/components/detail/MutationRuleManager.tsx:8",
    "src/management/components/detail/OverrideManager.tsx:3, 4",
    "src/management/components/detail/PermissionMatrixEmbed.tsx:8",
    "src/management/components/detail/PersonaCapitalBindingTab.tsx:4",
    "src/management/components/detail/PersonaEvaluationsTab.tsx:4",
    "src/management/components/detail/PersonaIdentityTab.tsx:4",
    "src/management/components/detail/PersonaPolicyViolationsTab.tsx:4",
    "src/management/components/detail/PersonaStrategyOwnershipTab.tsx:2",
    "src/management/components/detail/PersonaVersionHistoryTab.tsx:3",
    "src/management/components/detail/PromotionPanel.tsx:8, 10",
    "src/management/components/detail/RebalanceWorkflowTab.tsx:3, 4, 12",
    "src/management/components/detail/RiskBudgetPanel.tsx:7",
    "src/management/components/detail/RoutePolicyPreview.tsx:9",
    "src/management/components/detail/SkillPromptEditor.tsx:7",
    "src/management/components/detail/SkillRiskPanel.tsx:4",
    "src/management/components/detail/StrategyDataFeaturesTab.tsx:3",
    "src/management/components/detail/StrategyParamsEditor.tsx:7",
    "src/management/components/detail/StrategyPerformanceTab.tsx:4",
    "src/management/components/detail/StrategySpecTab.tsx:3, 4",
    "src/management/components/detail/ToolSchemaPanel.tsx:7",
    "src/management/components/detail/WorkflowStepper.tsx:3",
    "src/management/components/detail/__tests__/StrictLiveManagementSurfaces.test.tsx:10, 137",
    "src/management/components/governance/PermissionMatrix.tsx:8",
    "src/management/components/governance/PolicyVersionDiff.tsx:4",
    "src/management/components/governance/RoutePolicyEditor.tsx:9",
    "src/management/components/ooda/OodaPacketDrawer.test.tsx:7",
    "src/management/components/ooda/OodaPacketDrawer.tsx:25",
    "src/management/components/persona/PersonaReadinessCard.tsx:19",
    "src/management/components/write/createEntity.test.ts:2",
    "src/management/components/write/createEntity.ts:2",
    "src/management/lib/personaReadiness.ts:11",
    "src/management/pages/ArtifactDetail.tsx:8",
    "src/management/pages/CapabilitiesLists.tsx:5",
    "src/management/pages/CapitalPoolDetail.test.tsx:7",
    "src/management/pages/CapitalPoolDetail.tsx:9",
    "src/management/pages/ChannelDetail.tsx:5",
    "src/management/pages/CommandCenter.tsx:16",
    "src/management/pages/DeploymentDetail.tsx:13",
    "src/management/pages/EvolutionDetail.tsx:11",
    "src/management/pages/GovernanceReview.tsx:13, 15",
    "src/management/pages/IncidentDetail.tsx:17, 18",
    "src/management/pages/Lists.tsx:4",
    "src/management/pages/McpDetail.tsx:4",
    "src/management/pages/ObjectDetailLayout.tsx:7",
    "src/management/pages/ObjectListPage.tsx:16, 17, 21",
    "src/management/pages/Operations.tsx:9, 12, 107, 141, 452",
    "src/management/pages/PersonaDetail.test.ts:4",
    "src/management/pages/PersonaDetail.tsx:13",
    "src/management/pages/PersonaOnboarding.test.ts:2",
    "src/management/pages/PersonaOnboarding.tsx:27",
    "src/management/pages/RankingFormulaDetail.tsx:8",
    "src/management/pages/RebalanceDetail.tsx:8",
    "src/management/pages/ResearchDetail.test.tsx:7",
    "src/management/pages/ResearchDetail.tsx:7",
    "src/management/pages/RiskCenter.tsx:17",
    "src/management/pages/SkillDetail.tsx:5",
    "src/management/pages/StrategyDetail.tsx:18, 22, 40",
    "src/management/pages/ToolDetail.tsx:4",
    "src/management/pages/capitalPoolsFleetFallback.test.ts:4",
    "src/management/pages/capitalPoolsFleetFallback.ts:2",
    "src/management/pages/governance/ConsultRulesPage.tsx:10",
    "src/management/pages/governance/MemoryGovernancePage.tsx:10",
    "src/management/pages/governance/PermissionMatrixPage.tsx:7",
    "src/management/pages/governance/RoutePoliciesList.tsx:10",
    "src/management/pages/governance/RoutePolicyDetail.tsx:12",
    "src/management/pages/oversight/_core.tsx:55",
    "src/management/pages/personaDetailData.ts:1",
    "src/management/pages/phase2/AlphaFactoryBoard.tsx:11",
    "src/management/pages/phase2/GovernanceQueue.tsx:13, 15",
    "src/management/pages/phase2/PostmortemLibrary.tsx:14",
    "src/management/pages/phase2/alphaFactoryData.test.ts:3",
    "src/management/pages/phase2/alphaFactoryData.ts:1",
    "src/management/pages/studios/FormulaStudio.tsx:12",
    "src/management/pages/studios/SkillSandboxStudio.tsx:11",
    "src/management/pages/v5/LoopRunDrawer.tsx:20",
    "src/mocks/seed.ts:12, 344",
    "src/platform/components/CommandPalette.tsx:7",
    "src/platform/components/EntityHeader.tsx:12",
    "src/platform/components/HighRiskConfirm.tsx:26",
    "src/platform/components/JobProgressDrawer.tsx:9",
    "src/platform/components/LineageGraph.tsx:4",
    "src/platform/components/NotificationCenter.tsx:15, 19",
    "src/platform/components/RealtimeStatusBadge.tsx:9",
    "src/platform/components/RightDrawer.tsx:17",
    "src/platform/components/RiskBadge.tsx:3",
    "src/platform/components/ScenarioRunnerCard.tsx:9",
    "src/platform/components/StageDecisionPanel.tsx:10",
    "src/platform/components/TopBar.tsx:164",
    "src/platform/pages/QAChecklist.tsx:8, 11",
    "src/test/e2e-scenarios.test.ts:6, 9"
  ],
  "runtime_or_deploy_refs": [
    "Management console pages, Platform components, Agora components"
  ],
  "replacement": "Canonical src/lib/bff-v1/ endpoints and typed DTOs",
  "replacement_proof": "src/lib/bff-v1/__tests__/* comprehensive test suites (lists, writes, sse, degradation, tradeJourneys, capitalPools, managementAi)",
  "disposition": "replace_then_delete",
  "validation": [
    "npm --prefix /home/lupin/code/execute-plans test",
    "npm --prefix /home/lupin/code/execute-plans run build"
  ]
}
```

### 3.10 UI Mutation Action Wrapper (`src/lib/bff-v1/runActionSafe.ts`)

```json
{
  "path_or_symbol": "src/lib/bff-v1/runActionSafe.ts:runActionSafe",
  "behavior": "Canonical UI-facing mutation wrapper that auto-injects correlation and idempotency headers and surfaces rejections via user-facing toasts while maintaining typed MutationResult.",
  "callers": [
    "src/management/components/detail/StrategyParamsEditor.tsx:8, 27",
    "src/management/pages/ArtifactDetail.tsx:6, 127",
    "src/management/pages/CapitalPoolDetail.tsx:6, 352",
    "src/management/pages/DeploymentDetail.tsx:11, 155, 166, 192, 223",
    "src/management/pages/EvolutionDetail.tsx:9, 146, 214",
    "src/management/pages/Operations.tsx:8, 168, 238",
    "src/management/pages/RankingFormulaDetail.tsx:6, 127",
    "src/management/pages/RebalanceDetail.tsx:6, 112, 235",
    "src/management/pages/ResearchDetail.tsx:5, 199",
    "src/management/pages/Runtimes.tsx:11, 139",
    "src/management/pages/StrategyDetail.tsx:19, 485",
    "src/lib/bff-v1/runActionSafe.ts:3, 29 (implementation)",
    "src/lib/bff-v1/writes.ts:9",
    "src/lib/bff-v1/legacy.ts:7, 19",
    "src/lib/bff-v1/seed.ts:9",
    "src/lib/bff-v1/seed-taxonomy.json:47",
    "src/lib/bff-v1/__tests__/writes.test.ts:5, 127, 155, 380, 384 (19 unit test cases)",
    "src/management/pages/CapitalPoolDetail.test.tsx:20, 43, 78",
    "src/management/pages/ResearchDetail.test.tsx:15, 31, 105",
    "src/management/pages/Runtimes.test.tsx:17, 25, 63, 175, 181, 218, 236, 270, 288, 343, 385",
    "src/management/pages/capabilitiesProductionTruth.test.ts:40",
    "scripts/accept-management-hosted-production.mjs:476",
    "scripts/codemod-bff-v1.ts:30, 35, 48",
    ".lovable/audits/batch-vii-migration.md:9, 66, 93, 98, 111, 126",
    ".lovable/spec/management-2026-05-20/Pantheon_Management_Lovable_Spec_2026-05-20.md:1167",
    "docs/deployment/evidence/PFG-FE-HONEST-LIVE-20260820/caller-inventory.md:7, 9, 11, 25, 26, 88",
    "docs/deployment/evidence/PFG-FE-HONEST-LIVE-20260820/evidence.json:18, 22",
    "docs/deployment/evidence/product-functional-closure/PFG-MGMT-FE-READONLY-RUNTIME-ACTIONS-20260823/evidence.json:38, 53",
    "docs/testing/mgmt-load-004-route-split-evidence.md:41"
  ],
  "runtime_or_deploy_refs": [
    "All management console mutation button click handlers"
  ],
  "replacement": "Canonical implementation",
  "replacement_proof": "src/lib/bff-v1/__tests__/writes.test.ts (19 tests passed), src/management/pages/capabilitiesProductionTruth.test.ts (3 tests passed)",
  "disposition": "retain",
  "validation": [
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/lib/bff-v1/__tests__/writes.test.ts",
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/management/pages/Runtimes.test.tsx"
  ]
}
```

### 3.11 Disabled Action Safety Guard (`src/management/components/NonProductionActionButton.tsx`)

```json
{
  "path_or_symbol": "src/management/components/NonProductionActionButton.tsx:NonProductionActionButton",
  "behavior": "Honestly disabled button component with tooltip explanation preventing user clicks on unbacked backend mutations.",
  "callers": [
    "src/management/components/detail/AllocationSimulationPanel.tsx:10, 67 (1 button)",
    "src/management/components/detail/EvolutionCandidatesTab.tsx:13, 67, 70, 120 (3 buttons)",
    "src/management/components/detail/EvolutionRunsPanel.tsx:10, 69 (1 button)",
    "src/management/components/detail/McpRegistryPanel.tsx:8, 49 (1 button)",
    "src/management/components/detail/McpSecretsPanel.tsx:13, 46 (1 button)",
    "src/management/components/detail/MemoryGovernanceQueue.tsx:11, 53, 54 (2 buttons)",
    "src/management/components/detail/MutationRuleManager.tsx:13, 23 (1 button)",
    "src/management/components/detail/PersonaWorkspaceTab.tsx:7, 24 (1 button)",
    "src/management/components/detail/RiskBudgetPanel.tsx:9, 42 (1 button)",
    "src/management/components/detail/SkillPromptEditor.tsx:9, 62 (1 button)",
    "src/management/components/detail/ToolSchemaPanel.tsx:9, 97 (1 button)",
    "src/management/components/governance/PermissionMatrix.tsx:11, 70, 71 (2 buttons)",
    "src/management/components/governance/RoutePolicyEditor.tsx:11, 69, 72, 135, 143 (4 buttons)",
    "src/management/pages/ChannelDetail.tsx:12, 33 (1 button)",
    "src/management/pages/IncidentDetail.tsx:25, 151 (1 button)",
    "src/management/pages/McpDetail.tsx:20, 50, 53, 57, 61, 65, 196 (6 buttons)",
    "src/management/pages/PersonaDetail.tsx:22, 325 (1 button)",
    "src/management/pages/Runtimes.tsx:16 (import NON_PRODUCTION_COMMAND_REASON)",
    "src/management/pages/SkillDetail.tsx:20, 59, 63 (2 buttons)",
    "src/management/pages/StrategyDetail.tsx:20, 116, 120, 316, 320, 349, 352 (6 buttons)",
    "src/management/pages/ToolDetail.tsx:16, 51, 56, 62, 65, 68, 74, 79, 83, 86 (9 buttons)",
    "src/management/pages/governance/ConsultRulesPage.tsx:13, 45, 46, 73 (3 buttons)",
    "src/management/pages/governance/MemoryGovernancePage.tsx:13, 94, 95, 119, 120, 126 (5 buttons)",
    "src/management/pages/phase2/HookCronManager.tsx:13, 25 (1 button)",
    "src/management/pages/phase2/KnowledgeInbox.tsx:12, 59, 60, 61, 62 (4 buttons)",
    "src/management/pages/phase2/Settings.tsx:18, 95, 132, 166, 180, 183 (5 buttons)",
    "src/management/pages/phase2/WorkflowTemplates.tsx:13, 32, 73, 74 (3 buttons)",
    "src/management/pages/studios/FormulaStudio.tsx:17, 152 (1 button)",
    "src/management/components/NonProductionActionButton.tsx:8, 12, 16 (definition)",
    "src/management/components/NonProductionActionButton.test.tsx:6, 7, 9, 11 (1 test JSX site)",
    "src/management/components/detail/__tests__/StrictLiveManagementSurfaces.test.tsx:157",
    "src/management/pages/capabilitiesProductionTruth.test.ts:39",
    "scripts/accept-management-hosted-production.mjs:476",
    "docs/deployment/evidence/PFG-MGMT-FE-REAL-20260820/evidence.json:15, 18"
  ],
  "runtime_or_deploy_refs": [
    "Management console non-production actions (68 distinct button call sites across 27 production pages/panels + 1 test JSX site = 69 literal JSX sites)"
  ],
  "replacement": "Canonical implementation",
  "replacement_proof": "src/management/components/NonProductionActionButton.test.tsx (1 test passed), src/management/pages/capabilitiesProductionTruth.test.ts (3 tests passed)",
  "disposition": "retain",
  "validation": [
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/management/components/NonProductionActionButton.test.tsx",
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/management/pages/capabilitiesProductionTruth.test.ts"
  ]
}
```

### 3.12 Management AI UI Action Registry (`src/management/components/agent/uiActionRegistry.ts`)

```json
{
  "path_or_symbol": "src/management/components/agent/uiActionRegistry.ts:AVAILABLE_UI_ACTIONS",
  "behavior": "Allowlisted UI action registry for Management AI assistant with 7 contract kinds (navigate, openDrawer, selectEntity, setFilter, focusPanel, refreshCurrentView, runBffAction). Validates incoming actions before high-risk confirmation or execution.",
  "callers": [
    "src/management/components/agent/uiActionRegistry.ts:45, 60, 62, 124, 130, 137, 146, 152 (registry definition, schemas, and internal helpers)",
    "src/management/components/agent/AgentPanelBody.tsx:47-53 (imports AVAILABLE_UI_ACTIONS, executeUiAction, getActionCorrelationKey, isHighRiskAction, isKnownUiActionKind, UiAction), 790 (maps AVAILABLE_UI_ACTIONS in availableUiActions payload), 846 (getActionCorrelationKey), 860 (isKnownUiActionKind validation), 884 (actionCorrelationId), 903 (executeUiAction), 1621 (getActionCorrelationKey), 1622 (isHighRiskAction), 1636 (runUiAction)",
    "src/management/components/agent/uiActionRegistry.test.ts:2-17 (direct unit test suite importing AVAILABLE_UI_ACTIONS, AVAILABLE_UI_ACTION_KINDS, isKnownUiActionKind, ALLOWED_ROUTE_PREFIXES, SUPPORTED_DRAWERS, SUPPORTED_PANELS, CREATABLE_ENTITIES, isCreatableEntity, executeUiAction, getActionCorrelationKey, isHighRiskAction, isValidUiAction, type UiAction, type UiActionExecuteCtx; 47 unit test cases across lines 19-520)",
    "docs/deployment/evidence/PFG-MGMT-AI-FE-ACTIONS-20260820/evidence.json:11, 36 (evidence documentation)"
  ],
  "broader_context_evidence": [
    "src/management/components/agent/useAgentPanel.test.ts (broader agent panel hook tests; no direct uiActionRegistry symbol references)",
    "src/management/pages/capabilitiesProductionTruth.test.ts (broader management capability suite; no direct uiActionRegistry symbol references)"
  ],
  "runtime_or_deploy_refs": [
    "Management AI NL assistant drawer on all /management/* routes"
  ],
  "replacement": "Canonical implementation; retain allowlisted registry",
  "replacement_proof": "src/management/components/agent/uiActionRegistry.test.ts (47 unit tests passed verifying allowlist schemas, action execution, replay prevention, and confirmation flow), docs/deployment/evidence/PFG-MGMT-AI-FE-ACTIONS-20260820/evidence.json",
  "disposition": "retain",
  "validation": [
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/management/components/agent/uiActionRegistry.test.ts",
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/management/components/agent/useAgentPanel.test.ts",
    "npm --prefix /home/lupin/code/execute-plans test -- --run src/management/pages/capabilitiesProductionTruth.test.ts"
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
|                                                    (Adoption verified)            |
|                                                                                   |
|  TradingRoomPage (fixed lens-A..E) -------------[replace_then_delete]-->          |
|                                                    Dynamic Workshop/Pool Recipes  |
|                                                    (Replaced in TradingRoomPage)  |
+-----------------------------------------------------------------------------------+

+-----------------------------------------------------------------------------------+
|                            MANAGEMENT CONSOLE SUBSYSTEM                           |
|                                                                                   |
|  safeAdapt (24 call sites in management.ts) --[replace_then_delete]-->            |
|                                                    withStrictLiveOrMock / Error   |
|                                                    (Migrate 24 sites -> delete)   |
|                                                                                   |
|  FormulaStudio (synthetic backtest runner) -----[replace_then_delete]-->          |
|                                                    Governed Jobs API / Disabled   |
|                                                    (NonProductionActionButton)    |
|                                                                                   |
|  ActivityMonitor (seed events + fake live) -----[delete fake live]----------->    |
|                                                    Canonical Audit/SSE Feeds      |
|                                                    (realtime.on subscriptions)    |
|                                                                                   |
|  PostmortemLibrary (static SEED array) ---------[replace_then_delete]-->          |
|                                                    Canonical /bff/incidents       |
|                                                    (bff.incidents.list())         |
+-----------------------------------------------------------------------------------+

+-----------------------------------------------------------------------------------+
|                                BFF ADAPTER SUBSYSTEM                              |
|                                                                                   |
|  Legacy src/lib/bff/ (176 import sites in 147 files) -[replace_then_delete]-->    |
|                                                    Canonical src/lib/bff-v1/      |
|                                                                                   |
|  runActionSafe (29 files, 80 refs) -------------[retain canonical]--------->     |
|                                                    Production Mutations           |
|                                                                                   |
|  NonProductionActionButton (69 JSX in 28 files)-[retain safety guard]----->       |
|                                                    Honest Disabled Actions        |
|                                                    (68 prod + 1 test JSX sites)   |
|                                                                                   |
|  uiActionRegistry (2 direct callers, 2 context) -[retain allowlist]---------->    |
|                                                    Management AI Operations       |
|                                                    (7 declared action kinds)      |
|                                                                                   |
|  src/mocks/seed.ts (14 import sites) -----------[retain fixture only]------->     |
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
   - **Step 2 (Management Live Hygiene):** Migrate 24 `safeAdapt` call sites in `management.ts` to `withStrictLiveOrMock` / typed error envelopes, then delete `safeAdapt` helper; remove static `seed` events and fake "live" badge in `ActivityMonitor.tsx`; wire `PostmortemLibrary.tsx` to canonical incident API; rerun Management tests.
   - **Step 3 (Adapter Convergence & Safety):** Migrate remaining `src/lib/bff/` callers to `src/lib/bff-v1/`; verify tree-shaking isolates `src/mocks/seed.ts` from strict-live production build bundle; retain canonical `uiActionRegistry.ts` allowlist for Management AI assistant operations and `NonProductionActionButton.tsx` for unbacked action safety.
   - **Step 4 (Validation):** Execute full browser E2E test suites in both Agora and Management Console.

---

## 6. Verification Summary

Focused validation performed in `execute-plans` pinned to `0eec7659c9503ba3799ed5666cfa00f2b031e7fa`:
- `src/management/components/agent/uiActionRegistry.test.ts` (47 tests passed)
- `src/agora/components/CandidateReviewDrawer.test.tsx` (27 tests passed)
- `src/agora/pages/trading-room/TradingRoomPage.test.tsx` (64 tests passed)
- `src/management/components/NonProductionActionButton.test.tsx` (1 test passed)
- `src/management/pages/capabilitiesProductionTruth.test.ts` (3 tests passed)
- `src/management/components/agent/useAgentPanel.test.ts` (3 tests passed)
- `src/lib/bff-v1/__tests__/writes.test.ts` (25 tests passed)
- `src/lib/bff-v1/__tests__/strictLiveReadOffline.test.ts` (2 tests passed)
- `src/lib/bff-v1/__tests__/management.test.ts` (37 tests passed)
- Total: **209 passed / 209 tests** across 9 test suites.
