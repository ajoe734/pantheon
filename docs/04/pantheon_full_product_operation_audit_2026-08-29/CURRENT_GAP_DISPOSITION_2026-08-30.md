# Current Operational GAP Disposition (2026-08-30)

## Overview

All 20 identified operational gaps (**OP-G01** through **OP-G20**) from the full product operation audit (`FULL_OPERATION_AUDIT_2026-08-29.md`) are mapped to definitive states based on current `origin/dev` code evidence, prior delivery proofs, and the target execution DAG. All audit observations, product plane statuses, three-pass verification findings, observed-vs-planned comparisons, evidence ownership, and exit criteria are preserved in this exhaustive canonical record.

---

## 1. Product Planes Status Overview (P-01 to P-17)

| ID | Product Plane | Core Verified Journey | Status | Disposition Rationale & Current Boundary |
|---|---|---|---|---|
| **P-01** | Product shell / navigation | Login, shell layout, main routes, degraded state presentation | `PARTIAL` | Shell renders in desktop mode; mock fallback wiring requires bundle clean up under `OPGAP-FE-BUNDLE-CLEANUP-20260830`. |
| **P-02** | Source Ingestion | Catalog/config, manual bounded pull, record persistence, freshness, return to reconcile-only | `PARTIAL` | Frontier recovery (`f227360`), snapshot alias (`9e9ab33`), and Taiwan market session freshness (`394eb05`) verified; hosted effect proof scheduled in `OPGAP-HOSTED-BACKEND-ACCEPTANCE-20260830`. |
| **P-03** | Loop 2–4 (Distillation, Alpha, Teaching) | SourceRecord -> distillation -> strategy/alpha -> teaching readback | `PARTIAL` | Code contracts merged; verified end-to-end in 12-loop backend proof under `OPGAP-HOSTED-BACKEND-ACCEPTANCE-20260830`. |
| **P-04** | Agora / Loop 5 | Workshop -> research -> candidates -> trading room -> decision/performance | `FAIL` | Research default adapter generates fake real truth (`OP-G01`), suggestion producer lacks callers (`OP-G02`), and deploy projection binding failed (`OP-G19`); remediated in `OPGAP-BE-AGORA-ROUTER-20260830`. |
| **P-05** | Loop 6–7 (Imitation, Consultation) | Imitation/consultation -> policy/governance receipt | `PARTIAL` | Domain engines present; live journal receipts verified in `OPGAP-HOSTED-BACKEND-ACCEPTANCE-20260830`. |
| **P-06** | Deployment / Loop 8 | Approved artifact -> executable binding -> Runtime Manager readback | `FAIL` | Registry loader/market projection not emitted (`OP-G17`); remediated in `OPGAP-BE-RUNTIME-BINDING-20260830`. |
| **P-07** | Paper / Capital / Loop 9 | Snapshot -> signal -> order/fill/position/heartbeat (paper-only) | `FAIL` | Paper signal producer blocked by projection gate in run 33280168821 (`OP-G20`); remediated in `OPGAP-HOSTED-DEV-PROMOTION-20260830`. |
| **P-08** | Reconciliation / Loop 10 | Fill/heartbeat -> drift/incident readback | `PARTIAL` | Outbox/incident models present; live fill drift readback scheduled in `OPGAP-HOSTED-BACKEND-ACCEPTANCE-20260830`. |
| **P-09** | Evolution / Loop 11 | Incident/postmortem -> evolution decision/receipt | `PARTIAL` | Evolution engine present; canonical Postmortem read owner established under `OPGAP-BE-POSTMORTEM-ROUTER-20260830` and `OPGAP-BE-MANAGEMENT-ROUTER-20260830`. |
| **P-10** | Loop truth / Loop 12 | Canonical 12-loop state and Management same-ID display | `PARTIAL` | Single `loop_truth.py` join model merged; hosted same-ID UI display verified under `OPGAP-HOSTED-MGMT-ACCEPTANCE-20260830`. |
| **P-11** | Management reads | Cockpit, fleet, journeys, performance, rankings, risk, registries, ops | `PARTIAL` | Canonical read endpoints wired; desktop UI verified under `OPGAP-HOSTED-MGMT-ACCEPTANCE-20260830`. |
| **P-12** | Management commands | Visible mutations -> terminal receipt -> durable readback | `PARTIAL` | Generic non-Persona CRUD unbacked (`OP-G06`); wired to durable endpoints or removed in `OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830`. |
| **P-13** | Management AI | Conversation, NL query, UI actions, domain action handoff | `PARTIAL` | Drawer actions wired; hosted live query verified under `OPGAP-HOSTED-MGMT-ACCEPTANCE-20260830`. |
| **P-14** | Agora UI | Trading room, workshop, performance routes and detail views | `PARTIAL` | Active authentic demo underway in `AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829`; workshop card provenance fixed in `OPGAP-FE-AGORA-WORKSHOP-20260830`. |
| **P-15** | Management UI | Mounted nav, canonical redirects, detail/empty/degraded views | `PARTIAL` | Navigation converged; authenticated desktop journey matrix verified under `OPGAP-HOSTED-MGMT-ACCEPTANCE-20260830`. |
| **P-16** | Delivery/runtime truth | Exact pair, health, dependency readiness, rollback-safe served identity | `FAIL` | Release workflow summary wraps failures (`OP-G04`), lease and rollback share fragile remote API (`OP-G16`); remediated in `OPGAP-DEPLOY-RELIABILITY-20260830`. |
| **P-17** | Architecture Simplification | Single owner, zero route collision, zero production mock reachability | `FAIL` | Monolithic `main.py` 68,313 lines (`OP-G08`), cross-router imports (`OP-G09`), dead generic adapters (`OP-G10`); remediated across Batch B, C, and D. |

---

## 2. Three-Pass Audit Methodology & Detailed Findings

### Pass 1: Requirements, SA, and SD -> Current Code Truth
1. **Resolved Gaps**:
   - Lifecycle JSON as live authority: Removed; Postgres writer/reader active.
   - Interaction HTTP 202 sync execution: Fixed; asynchronous queued requests with dedicated `agora-interaction-worker`.
   - Bearer session EventSource: Fixed; `fetchSse` used for bearer auth with base URL detection.
   - Persona List/Fleet identity drift: Fixed; `PersonaDirectorySnapshot` strictly admits valid identities.
   - Trading Room fixed lenses: Fixed; dynamic candidate pools driven by BFF `candidatePoolId`.
   - ReadSurfaceStore God class: Permanently deleted.
2. **Current Code Gaps**:
   - Monolithic `main.py`: 68,313 lines, 2,272 top-level AST body nodes, 453 `@app` decorators across 441 HTTP route decorators and 421 unique route handlers (`OP-G08`).
   - Cross-router private imports in Agora (`OP-G09`).
   - Dead generic action adapter legacy code (`OP-G10`).
   - Auth readiness synchronous dependency on OpenClaw provider network latency (`OP-G05`).
   - Async ASGI test suite deadlocks on AnyIO event loops (`OP-G13`).
   - Agora research default adapter fake real truth (`OP-G01`) and `PerformanceSuggestionProducer` lacking production caller (`OP-G02`).
   - Registry loader / market policy not emitted on deployment transition (`OP-G17`).
   - Source-to-Agora read projection deploy gate sync mechanism in `scripts/project_market_data_to_bff_agora_surfaces.py` (`OP-G19`).

### Pass 2: Current Code -> Deployed Runtime & Promotion Truth
1. **Promotion History & Blockers**:
   - Run 33260583008: Environment lease GitHub API timeout; compensation lease timeout resulting in split pair.
   - Run 33262293025: Failed at `Deploy dev VM stack under lease` (bare 64-hex image ID rejected, paper-signal-producer unhealthy).
   - Run 33272385942: Failed on flat 24h market freshness on Saturday (TWSE close on Friday was 44h old). Fixed by Taiwan market session freshness policy in PR #5416 (`394eb05`).
   - Run 33280168821: Failed at `scripts/deploy_nonprod_vm.sh:1218` during Agora read projection binding check on bounded manual source refresh (`OP-G19`). Compensated back to safe baseline.
2. **Current Baseline State**:
   - Hosted Pair: Pair ID `9de4cd001a8b7aaf18a1094fb1699ece19f0efd86d3d24994cd9f3562fe33727`, Release Candidate ID `9783e78bd8e28608f2c335d566fd798db5b995c50da129876401170b45852e9a`, accepted at `2026-08-30T16:17:23Z`, Backend `2bcb4465399af83190c5027073f3b2296e377256`, Frontend `7d30e78476be61222af63a089e7ab141aa43b809`, Controller Run `33319323262`, Gate Run `33320810888`, Execute-Plans Deploy Run `33321494484`, Status `accepted`.
   - Hosted identity baseline: Served `/deployment.json` and live `/bff/version` both report backend source commit `2bcb4465399af83190c5027073f3b2296e377256` (reconciling previous manifest/runtime drift via controller run 33319323262 / gate 33320810888 / deploy run 33321494484); `OP-G03` remains planned for target candidate promotion under `OPGAP-HOSTED-DEV-PROMOTION-20260830` to deploy the unified post-remediation release candidate and verify matching runtime SHA-256 identities.

### Pass 3: Deployed Runtime -> Hosted Desktop UI Acceptance
- Held in unverified state pending complete execution of Batch B/C/D implementation and promotion.
- Split into two independent acceptance lanes:
  - Agora authentic hosted demo: Assigned to `AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829` (in `execute-plans`, canonical status `blocked` on BFF servant ensure path; PR #699 merged on `dev` at `bb438d1c7`).
  - Management desktop authenticated UI: Materialized under `OPGAP-HOSTED-MGMT-ACCEPTANCE-20260830`.

---

## 3. 原始盤點觀察 vs 規劃真實比對 (Audit Observed vs Planned Truth)

| 原始稽核觀察項目 | 判定 | 規劃真實與必要限縮 |
|---|---|---|
| 48 個後端 service 目錄；兩個 `NotImplementedError` 是抽象 Port | 正確（稽核基線） | 程式碼目錄存在不代表 production 運作；抽象方法本身不是未完成功能。 |
| BFF 在 `services/control-plane/bff`，有大量 routes/tests | 正確 | 本基線 AST 包含 68,313 行、2,272 AST nodes、441 個 HTTP decorators、421 unique handlers，所有 inline 路由需解耦至 18 domain routers。 |
| Agora write matrix、Postgres persistence、33/33 測試 | 正確（該批次） | 尚不能證明 suggestion 有自然 production caller、hosted receipt 與 same-ID durable readback。 |
| Management 在 execute-plans，不是本 repo legacy app；0 mock import | 方向正確 | import grep 不能證明 production bundle 不可達 seed/overlay/fallback，需 bundle depgraph gate。 |
| 核心系統是真的做出來 | 正確 | 證明非空殼，但唯一 write authority、failure semantics、hosted effect、安全治理與 clean retirement 仍需證明。 |
| 646 pass / 20 fail / 16 skip | 正確（快照） | 歷史快照 197 passed；最新 focused verifier 144 passed / 2 failed / 1 skipped；run 33280168821 在 checkpoint `7,637,654` 補償回滾。數字不能作為當前 HEAD 的永久結論。 |
| BFF 多副本 `from main import X` 是 failover 測試根因 (F21) | 正確 | 根因是 domain router 反向依賴 composition root；已正式併入 OP-G08 徹底解耦。 |
| EP5 gate 卡住 kill/rollback fixture (F22) | 正確且未解 | 不能以 test bypass 讓測試變綠。F22 作為 unresolved exclusion 留待安全治理處置。 |
| Evolution tenant-prefix assertions 落後 (F23) | 正確 | 測試債問題；確認 canonical contract 後更新測試，不得為舊 literal 逆改產品。 |
| Generic action adapter 退役與 command authority (F24) | 正確 | 已正式併入 OP-G10；保留 `command_executor.py` 作為中央命令權威，刪除 dead generic action adapters。 |
| Promote-to-master 六項 workflow 0-job (F25) | 正確 | 代表 merge-enforcement 證據缺失；F25 作為 unresolved exclusion 留待 branch security 治理處置。 |

---

## 4. Task-Board-vs-Git Drift & Full Test/CI Limitations

1. **Task-Board-vs-Git Drift & Reconciled Board IDs**:
   - `ai-status.json` and canonical task stores may show historical task assignments or stale in-progress statuses that have diverged from Git `origin/dev` commits.
   - **`AGORA-PERSONA-DURABLE-LIST-READBACK-V2-20260830`**: Recorded as merged on `origin/dev` (`d2bca5bc70bfae897e1ef3ca736ad3680a587679` via PR #5427) and is terminal `done`, unblocking Batch D assembly.
   - **`AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829`**: Canonical status `blocked` (waiting_for `Human/Ops`, PR #699 merged on execute-plans dev at `bb438d1c7`, blocked on servant ensure path; distinct from `OPGAP-HOSTED-MGMT-ACCEPTANCE-20260830`).
   - **`PPL-ALLOC-007`**: Historical board-drift task; binding visibility route pruning verified in canonical codebase.
   - **`PPL-ALLOC-009`**: Historical board-drift task; sidecar BFF handoff closed in merged PRs.
   - **`TJ-E2E-012`**: Historical Trade Journey E2E hosted acceptance task; canonical predecessor truth verified.
   - In contrast, verification gaps (`OP-G11`, `OP-G12`, `OP-G14`, `OP-G19`, `OP-G20`) must not be claimed as verified before implementation execution; they remain planned and open until the respective execution tasks produce live hosted receipts.

2. **Full Test & CI Limitations**:
   - Passing local unit tests in an isolated worktree does not prove system-wide operational readiness when suites mock out network boundaries, Redis, or PostgreSQL.
   - Incomplete integration environments that skip tests, encounter missing services, or timeout must be treated as `NOT_EXECUTED` or `UNVERIFIED` and cannot be presented as passing verification evidence; assertion failures are classified as `FAIL`.
   - True operational proof requires multi-process live test execution, gate-before-switch release validation, and durable readback on `pantheon-dev`.
   - Current time/SHA-bound test snapshot: image identity false-negative, frontier recovery, min closes 與 Taiwan session freshness 已在 current source 完成修復（歷史 197 passed；最新 focused verifier 144 passed / 2 failed / 1 skipped），但在 run `33280168821` 中 Agora projection 綁定驗證受阻（OP-G19）並觸發補償回滾；paper-signal-producer 仍待 live atomic switch 閉環（OP-G20）。

---

## 5. Comprehensive 20-GAP Disposition Matrix

| GAP ID | Severity | State | Primary Owner Task | Observed Evidence | Planned Resolution | Evidence Owner Task | Exit Evidence |
|---|---:|---|---|---|---|---|---|
| `OP-G01` | P0 | `planned` | `OPGAP-BE-AGORA-ROUTER-20260830` | `DefaultAllowlistedAdapter` in BFF builds candidate artifact with `provenance=real` without executing real backend models. | Decouple Agora router, require real backend execution for real candidate truth; return `simulation` or `unavailable` when backend is unexecuted. | `OPGAP-BE-AGORA-ROUTER-20260830` | Automated unit/contract tests prove default adapter emits `provenance=simulation/unavailable` and non-stub candidates require authentic backend receipt. |
| `OP-G02` | P0 | `planned` | `OPGAP-BE-AGORA-ROUTER-20260830` | `PerformanceSuggestionProducer(...)` exists only in tests; production codebase has 0 callers. | Connect paper telemetry and evaluation pipelines to naturally trigger suggestion producer; provide durable Postgres readback. | `OPGAP-BE-AGORA-ROUTER-20260830` | Production paper telemetry consumer triggers `PerformanceSuggestionProducer`, persisting durable suggestion records read back with same ID. |
| `OP-G03` | P0 | `planned` | `OPGAP-HOSTED-DEV-PROMOTION-20260830` | Current accepted pair 9de4cd00... aligned /deployment.json and live /bff/version on 2bcb4465399af83190c5027073f3b2296e377256; full product operation remediation requires final unified candidate promotion and post-switch identity verification. | Deploy verified atomic pair from current dev lines post-remediation and verify byte-identical SHA-256 in /deployment.json and /bff/version. | `OPGAP-HOSTED-DEV-PROMOTION-20260830` | Deployed /deployment.json and /bff/version match exact target pair SHAs and return identical runtime identities on pantheon-dev. |
| `OP-G04` | P0 | `planned` | `OPGAP-DEPLOY-RELIABILITY-20260830` | Workflow summary wraps step failures or skipped critical checks as green success (e.g. run 33256001457, run 33146133499). | Enforce strict fail-closed release gates in deploy workflows; fail build if any required step is skipped or returns non-zero. | `OPGAP-DEPLOY-RELIABILITY-20260830` | Release workflow fails closed on any skipped or errored step; produces explicit per-step proof artifact. |
| `OP-G05` | P1 | `planned` | `OPGAP-BE-BFF-CORE-20260830` | Auth readiness route blocks synchronously on `_safe_provider_readiness()` calling external OpenClaw provider probes. | Decouple local session/tenant authentication from upstream provider probes; read provider readiness asynchronously from degraded cache. | `OPGAP-BE-BFF-CORE-20260830` | Auth route returns HTTP 200 within 50ms regardless of OpenClaw latency; provider degradation reflected in async status. |
| `OP-G06` | P0 | `planned` | `OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830` | `createEntity.ts` routes non-Persona CRUD to `writeOverlay` in mock mode or throws in strict live mode; lacks durable mutation. | Wire all visible entity CRUD actions to canonical durable BFF endpoints, or disable unbacked actions in strict live. | `OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830` | Hosted browser CRUD journey produces genuine BFF command receipt and durable same-ID readback without `writeOverlay`. |
| `OP-G07` | P1 | `planned` | `OPGAP-FE-BUNDLE-CLEANUP-20260830` | Production bundle graph can reach `writeOverlay.ts` and legacy mock/seed files through unguarded barrel imports. | Eliminate residual mock files (`delete_after_zero_reachability`), clean live files (`retain_and_clean`), and enforce `check_bundle_mock_reachability.mjs` gate. | `OPGAP-FE-BUNDLE-CLEANUP-20260830` | Automated bundle analyzer proves 0 reachability from `src/main.tsx` to `writeOverlay` or seed data. |
| `OP-G08` | P1 | `planned` | `OPGAP-BFF-MAIN-ASSEMBLY-20260830` | Monolithic `main.py` contains 68,313 lines and 441 route decorators, causing reverse-main import sprawl and multi-replica collisions. [Merged with F21]. | Extract all 441 decorators and 421 handlers into 18 domain routers; reduce `main.py` to a pure composition root with zero reverse-main imports. | `OPGAP-BFF-MAIN-ASSEMBLY-20260830` | `main.py` contains <= 1,000 lines, 0 inline route handlers, pure composition root nodes, and 0 reverse-main imports. |
| `OP-G09` | P1 | `planned` | `OPGAP-BE-AGORA-ROUTER-20260830` | Agora domain routers cross-import private stores and unexported helpers across domain boundaries (e.g. `_build_readiness_assessment`). | Inject shared store contracts and helpers from composition root; eliminate all private cross-router imports. | `OPGAP-BE-AGORA-ROUTER-20260830` | Architecture linter proves 0 private cross-router imports across all Agora modules. |
| `OP-G10` | P2 | `planned` | `OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830` | Generic legacy action adapter `_execute_bff_action_adapter` and dead command plane artifacts remain. [Merged with F24]. | Delete dead generic action adapter and legacy test scripts while retaining `command_executor.py` as central command authority without reverse-main imports. | `OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830` | Dead generic action adapter deleted; `command_executor.py` operates as pure dispatcher importing from `ports/`. |
| `OP-G11` | P0 | `planned` | `OPGAP-HOSTED-BACKEND-ACCEPTANCE-20260830` | 12-loop cross-loop deployed proof is opt-in via environment variables, skipping verification by default. | Automate default execution of 12-loop cross-plane proof during backend acceptance on `pantheon-dev`. | `OPGAP-HOSTED-BACKEND-ACCEPTANCE-20260830` | Automated 12-loop test suite runs by default and produces 12 distinct stage receipts with durable same-ID readback. |
| `OP-G12` | P1 | `planned` | `OPGAP-HOSTED-BACKEND-ACCEPTANCE-20260830` | Source Management lacks hosted effect proof (add-disabled -> validate -> canary -> readback -> reconcile-only). | Execute hosted canary journey and verify automatic return to `reconcile_only` mode with single-stimulus receipt reuse (`source_proof_receipt_id`). | `OPGAP-HOSTED-BACKEND-ACCEPTANCE-20260830` | Hosted canary run completes with bounded 1-tick/100-records, persists `source_proof_receipt_id`, and restores `reconcile_only`. |
| `OP-G13` | P1 | `planned` | `OPGAP-BE-BFF-CORE-20260830` | Synchronous FastAPI `TestClient` verification suites deadlock against AnyIO async event loops. | Pin compatible async ASGI dependencies and migrate test suites to async transport (`httpx.AsyncClient`) with hard timeouts. | `OPGAP-BE-BFF-CORE-20260830` | All ASGI test suites execute asynchronously via `httpx.AsyncClient` without AnyIO deadlocks or timeouts. |
| `OP-G14` | P1 | `planned` | `OPGAP-HOSTED-MGMT-ACCEPTANCE-20260830` | Split into two non-overlapping lanes: Agora demo in execute-plans (`AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829`); Management desktop UI in pantheon (`OPGAP-HOSTED-MGMT-ACCEPTANCE-20260830`). | Reuses active `AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829` for Agora; materializes `OPGAP-HOSTED-MGMT-ACCEPTANCE-20260830` for Management desktop UI. | `OPGAP-HOSTED-MGMT-ACCEPTANCE-20260830` | Hosted browser journeys prove Management console routes and desktop UI render with live data. |
| `OP-G15` | P1 | `planned` | `OPGAP-FE-AGORA-WORKSHOP-20260830` | Research adapters default to stub/deferred in Compose while UI cards expect real candidate truth. | Display explicit stub/deferred/real provenance tags on UI cards and restrict candidate promotion to non-stub outputs. | `OPGAP-FE-AGORA-WORKSHOP-20260830` | UI workshop cards render explicit provenance badges; stub outputs cannot be promoted to verified Alpha candidates. |
| `OP-G16` | P0 | `planned` | `OPGAP-DEPLOY-RELIABILITY-20260830` | Deployment lease and rollback share fragile remote GitHub API dependency, causing cascading lease timeouts. | Implement bounded heartbeat retry/grace period in lease management and allow rollbacks from local sealed disk authority. | `OPGAP-DEPLOY-RELIABILITY-20260830` | Rollback executes successfully from local sealed authority even during simulated GitHub API network disconnect. |
| `OP-G17` | P0 | `planned` | `OPGAP-BE-RUNTIME-BINDING-20260830` | Registry -> Deployment -> RuntimeBinding executable loader/market projection is not naturally produced. | Emit immutable loader projection and market policy from canonical Registry for Runtime Manager verification. | `OPGAP-BE-RUNTIME-BINDING-20260830` | Registry artifact transition produces immutable loader projection verified and bound by Runtime Manager. |
| `OP-G18` | P1 | `planned` | `OPGAP-BE-POSTMORTEM-ROUTER-20260830` | Management Postmortem lacks canonical read owner (derived client-side from incident timeline strings). | Provide structured postmortem read model, list/detail API contracts in BFF, and durable Postgres persistence. | `OPGAP-BE-POSTMORTEM-ROUTER-20260830` | `GET /bff/management/postmortems` returns structured postmortem records with durable ID readback across reloads. |
| `OP-G19` | P0 | `planned` | `OPGAP-BE-AGORA-ROUTER-20260830` | Source-to-Agora Read Projection deploy gate failed in run 33280168821 on receipt/run/source binding check. | Fix `scripts/project_market_data_to_bff_agora_surfaces.py` and verify Agora read projection correctly binds `source_proof_receipt_id`. | `OPGAP-BE-AGORA-ROUTER-20260830` | Execution of `scripts/test_project_market_data_to_bff_agora_surfaces.py` and nonprod deploy gate proves valid projection binding without second egress. |
| `OP-G20` | P0 | `planned` | `OPGAP-HOSTED-DEV-PROMOTION-20260830` | Paper signal producer runtime health and full signal->order/fill/heartbeat lifecycle not closed in live promotion. | Execute nonprod deployment with latest candidate, prove producer enters healthy, and complete signal->order/fill readback. | `OPGAP-HOSTED-DEV-PROMOTION-20260830` | Live VM container status shows `paper-signal-producer` healthy and telemetry captures end-to-end signal-to-fill lifecycle. |

---

## 6. Finding Integrations & Scope Exclusions

### 1. Merged Findings
- **Finding F21** (Monolithic BFF composition root / multi-replica bare `from main import` / route sprawl): Formally merged into **OP-G08** (`OPGAP-BFF-MAIN-ASSEMBLY-20260830`). Remediated by decomposing `main.py` into 18 domain routers, extracting shared contracts to `services/control-plane/bff/ports/`, and enforcing unique OpenAPI operation IDs.
- **Finding F24** (Generic action adapter retirement / dead command plane compatibility code / central command authority): Formally merged into **OP-G10** (`OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830`). Remediated by deleting `_execute_bff_action_adapter` and unreferenced legacy files while retaining `command_executor.py` as central production command authority without reverse-main imports.

### 2. Unresolved Scope Exclusions
- **Finding F22** (EP5 governed activation proof): Unresolved scope exclusion; EP5 governed evolution activation and observation window verification are platform governance and safe mode execution policy exclusions.
- **Finding F23** (Tenant-prefix assertion drift): Unresolved scope exclusion; tenant-prefix assertion and multi-tenant isolation drift are excluded as test suite normalization items.
- **Finding F25** (Zero-job merge-workflow governance risk): Unresolved scope exclusion; six promote/master workflows starting with 0 evaluated jobs represent merge-enforcement branch protection governance evidence gaps; excluded as branch security and merge policies are distinct governance responsibilities.
