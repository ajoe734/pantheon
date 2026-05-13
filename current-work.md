# Current Work

This file is generated from `ai-status.json` and `ai-activity-log.jsonl`.
Do not treat this file as the machine-readable source of truth.
Absolute times below use 台灣時間 (UTC+8).

Last updated: 2026-05-13 11:22:59

## Objective

並行 4 條 track：(A) Shioaji TW broker sandbox smoke — services/broker/shioaji/ adapter, place/cancel/readback/reconcile, 餵進 scripts/run_ep5_canary_readiness.py human-gate packet；(B) Qlib LightGBM alpha activation — 寫 RS-003 baseline StrategySpec, 從 TWSE/TPEx 抓 ≥50 instruments × ≥2 years OHLCV, 跑 production_activation_smoke.py --backend real, submit registry admission packet；(C) services/ namespace normalization — control_plane→control-plane/internal, registry-core/decision-domain→registry/decision_domain；(D) BFF Consolidation — 補完 BFF execute-plans live wiring 的剩餘 20–30% production gap (route manifest contract diff, command envelope unification, non-empty fixture & detail journey, SSE real stream replay, strict env cutover, seed-only surface elimination)。Track D 27 tasks (BFF-CONSOL-001..027) 分 4 wave，Wave 1–2 與 Track A/B/C 並行不衝突；Wave 3 的 command adapter rollout (019/020/021) gated on EP5 paper-canary closeout (Day 12)；strict cutover 走 isolated Lovable preview branch；receipt dual-write soak 1 週後 deprecate 舊 receipt。broker production live 與 capital binding 仍 fail-closed；canary 仍需 risk-owner + operator approval gate。Track A/B 共用 TW market dataset 不重做兩次。

## Current Sprint

- Sprint: `2026-05-13-ep5-qlib-bff-consolidation`
- Canonical files: `AI_COLLABORATION_GUIDE.md`, `ai-status.json`, `ai-activity-log.jsonl`, `current-work.md`, `TARGET_ARCHITECTURE.md`, `OPENCLAW_RUNTIME_CONTRACT.md`, `PERSONA_RUNTIME_MODEL.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `PAPER_CANARY_LIVE_POLICY.md`, `ROLLBACK_AND_POSITION_SEMANTICS.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`, `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`, `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md`, `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md`, `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`, `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`, `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`, `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`, `CANONICAL_DOCUMENT_MAP.md`, `DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md`, `ROADMAP.md`, `DEVELOPMENT_WORKBREAKDOWN.md`, `OSS_INTEGRATION_CHECKLIST.md`, `WORKBENCH_DELIVERY_BACKLOG.md`, `DELIVERY_CLOSURE_AND_LOOP_STATES.md`, `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`, `CANONICAL_CONTRACT_MIGRATION_DECISION.md`, `WORK_REBASELINE.md`, `Pantheon_總索引版系統分析文件.md`, `Pantheon_資料表_Schema_設計版.md`, `Pantheon_API_Service_Contract_設計版.md`
- Canonical tiers: `L0 Collaboration & State`, `L0.5 Derived Narrative`, `L1 Platform Architecture & Policy`, `L2 Planning & Execution`, `L3 Supporting Design & Migration`
- Planning mode: `docs/02-architecture/consensus/sessions/phase6-2026-05-01-pantheon-p0-paper-loop/README.md`
- Canonical map: `CANONICAL_DOCUMENT_MAP.md`
- Document boundary: `DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md`
- Full backlog: `DEVELOPMENT_WORKBREAKDOWN.md`
- Workbench backlog: `WORKBENCH_DELIVERY_BACKLOG.md`
- Loop closure: `DELIVERY_CLOSURE_AND_LOOP_STATES.md`
- Execution proof: `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`
- Dashboard: `docs-site/index.html`

## Discussion Planning

- Session: `phase6-2026-05-01-pantheon-p0-paper-loop`
- Status: `accepted`
- Baton owner: `Codex`
- Current round: `0`
- Consensus: `accepted`
- Human gate: `approved`
- Ready for human: `True`
- Ready to materialize execution: `True`

## Active Slices

- `Claude`: execution, control-plane, governance-review; next: Supervisor re-dispatched BFF-CONSOL-006; task remains in progress.
- `Gemini`: gcp, ci-cd, runtime-packaging, worker-ops; next: Assignment created
- `Codex`: integration, status-system, schema, acceptance; next: Located execute-plans workspace and found partial banner/transport artifacts; tightening hybrid seed-warning and strict typed-error display before verification.
- `Codex2`: integration, status-system, schema, acceptance; next: Codex2 picked up fixture pack A implementation; inspecting BFF read store dataset loading and live list endpoints.
- `Copilot`: research-ingest, external-search, spec-review, critique; next: Assignment created
- `Claude2`: execution, control-plane, governance-review; next: Assignment created
- `Gemini2`: gcp, ci-cd, runtime-packaging, worker-ops; next: Chair reassigned review from Codex2 to Codex: Codex2 is occupied (working on EP5-BROKER-TW-002-RERUN-REAL, itself blocked on human-gate) and cannot promptly review. Codex is idle and not in any paused lane — moving the review to Codex unblocks the sidecar queue without disrupting ownership.

## Delivery Layers

### Primary Project Work

| ID | Phase | Task | Owner | Status | Depends On | 中文說明 |
|---|---|---|---|---|---|---|
| `EP5-BROKER-TW-002-RERUN-REAL-SIDECAR-ACCEPTANCE` | EP5 Broker TW Real Sandbox Smoke 2026-05-13 | [Sidecar] [Auto] [Parent EP5-BROKER-TW-002-RERUN-REAL] Prepare EP5-BROKER-TW-002-RERUN-REAL acceptance packet and dependency map | Claude | review | - | 平行支援 EP5-BROKER-TW-002-RERUN-REAL，先整理 acceptance checklist、dependency map 與 support packet，不改 canonical truth。 |
| `SVC-RENAME-003-SIDECAR-ACCEPTANCE` | services Namespace Normalization 2026-05-10 | [Sidecar] [Auto] [Parent SVC-RENAME-003] Prepare SVC-RENAME-003 acceptance packet and dependency map | Gemini2 | review | - | 平行支援 SVC-RENAME-003，先整理 acceptance checklist、dependency map 與 support packet，不改 canonical truth。 |
| `BFF-CONSOL-005` | BFF Consolidation 2026-05-13 | Live status banner UI (real/hybrid/mock) | Codex | in_progress | - | 在 execute-plans Management Console / Agora / v5 page header 加即時 live status banner 呼叫 getLiveStatusSnapshot() 顯示 real\|hybrid\|mock-fallback 三種狀態。hybrid/mock 顯示「資料來源：seed」警告。auto fallback 模式下 operator 不會誤判 seed 為 live。 |
| `BFF-CONSOL-006` | BFF Consolidation 2026-05-13 | Role vocabulary mapping doc | Claude | in_progress | - | 寫一份 doc 對齊 backend role vocabulary (viewer/operator/approver/admin) vs frontend mock (portfolio_manager/ops) vs MeResponse.roles 對應。輸出 docs/bff/role-vocabulary-mapping-2026-05-13.md。供 BFF-CONSOL-013 cookie write gate 與後續 capability map 使用。 |
| `BFF-CONSOL-007` | BFF Consolidation 2026-05-13 | Seed taxonomy spreadsheet | Claude | todo | - | 盤點 execute-plans src/lib/bff/seed.ts 全 helper 分四類 live_required\|mock_only_dev\|deprecated\|deferred。輸出 docs/bff/seed-taxonomy-2026-05-13.md 與 seed-taxonomy.json 供 015 mock-only badge 與 025 seed elimination 使用。 |
| `BFF-CONSOL-008` | BFF Consolidation 2026-05-13 | Canonical fixture pack A (strategies personas capital-pools rebalances deployments) | Codex2 | in_progress | `BFF-CONSOL-001` | 在 pantheon control-plane truth source 建立 canonical fixture pack A (strategies/personas/capital-pools/rebalances/deployments) 各 ≥1 筆 non-empty entry。Strategy 含 specs/experiments/artifacts/lineage/audit linkage。確保 /bff/strategies /bff/personas /bff/capital-pools /bff/rebalances /bff/deployments live list 都回 data_count≥1。 |
| `BFF-CONSOL-009` | BFF Consolidation 2026-05-13 | Canonical fixture pack B (evolution research artifacts v5 agora runtimes) | Claude | todo | `BFF-CONSOL-002` | canonical fixture pack B (evolution/research/artifacts/v5 interventions/agora/runtimes) 各 ≥1 筆 non-empty。v5 intervention 對應 governed remediation flow;agora 至少有一個 active topic;research 含 ticket 與 analysis sample。 |
| `BFF-CONSOL-010` | BFF Consolidation 2026-05-13 | Canonical fixture pack C (alerts incidents approvals audit jobs channels skills tools mcp) | Codex | todo | `BFF-CONSOL-008` | canonical fixture pack C (alerts/incidents/approvals/audit/jobs/channels/skills/tools/mcp) 各 ≥1 筆 non-empty。alert 對 incident;approval 連 deployment;audit 包含至少一個 immutable record。 |
| `BFF-CONSOL-011` | BFF Consolidation 2026-05-13 | SSE real stream replay test | Codex | todo | - | 真測 /bff/events/stream。Browser cookie-session 用 native EventSource(... {withCredentials:true}) Bearer 用 polyfill 帶 Authorization。驗證 open event/first event id/type/timestamp/Last-Event-ID replay/409 SSE_REPLAY_UNAVAILABLE/X-SSE-Resync-Routes。Evidence: support/evidence/BFF-CONSOL-011-sse-replay-smoke.json。 |
| `BFF-CONSOL-012` | BFF Consolidation 2026-05-13 | SSE backpressure & unbounded buffer test | Gemini | todo | `BFF-CONSOL-011` | client disconnect 後 backend 不該 unbounded buffer。對齊 EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md 的 ordering 規定。Test: simulate slow consumer + disconnect assert backend buffer bound + replay window policy + drop strategy。 |
| `BFF-CONSOL-013` | BFF Consolidation 2026-05-13 | Cookie-session write gate (/bff/me driven) | Claude | todo | `BFF-CONSOL-004`, `BFF-CONSOL-006` | 修改 execute-plans liveWriteGated() 從 /bff/me response 判斷 authenticated session (cookie or bearer) 不再只看 sessionStorage bearer token。cookie-only session 不應被誤判為 unauthenticated。後端 /bff/me 需回明確 session_kind: cookie\|bearer\|stub。 |
| `BFF-CONSOL-014` | BFF Consolidation 2026-05-13 | Lovable CORS allowlist + JWKS strict test infra | Codex2 | todo | - | pantheon BFF main.py 加 PANTHEON_BFF_CORS_ORIGINS 預設 include Lovable preview/dev/prod hostnames。JWKS strict mode 在 CI 跑 issuer/audience/key-rotation test。CORS dev override 在 production strict 模式下不可啟用。 |
| `BFF-CONSOL-015` | BFF Consolidation 2026-05-13 | Mock-only badge implementation (live mode) | Claude2 | todo | `BFF-CONSOL-005`, `BFF-CONSOL-007` | live mode 下若頁面 fetch 失敗回 seed-only helper UI 自動掛 mock data badge;mock_only_dev helper 在 live mode 直接禁用 (raise 或 return null + 顯示空狀態)。讀 BFF-CONSOL-007 taxonomy 作為分類來源。 |
| `BFF-CONSOL-016` | BFF Consolidation 2026-05-13 | Detail journey smoke A (strategy persona deployment runtime) | Claude | todo | `BFF-CONSOL-008` | detail journey smoke A: strategy/persona/deployment/runtime 各跑 list→detail→related tabs。每個 family ≥1 non-empty fixture (來自 008) detail drawer 渲染 tabs 切換 degraded path 都跑過。Evidence support/evidence/BFF-CONSOL-016-detail-smoke-a.json。 |
| `BFF-CONSOL-017` | BFF Consolidation 2026-05-13 | Detail journey smoke B (evolution research v5 agora artifacts) | Claude2 | todo | `BFF-CONSOL-009` | detail journey smoke B: evolution/research/v5/agora/artifacts list→detail→related tabs。每個 family 走過 live detail 而非 mock。 |
| `BFF-CONSOL-018` | BFF Consolidation 2026-05-13 | Detail journey smoke C (incident approval rebalance job audit) | Claude | todo | `BFF-CONSOL-010` | detail journey smoke C: incident/approval/rebalance/job/audit。job mock fallback undefined 改成 typed 404;audit list-only 特判 detail drawer disabled。 |
| `BFF-CONSOL-019` | BFF Consolidation 2026-05-13 | Command envelope adapter backend impl (gated on EP5 closeout) | Codex | todo | `BFF-CONSOL-004` | 後端 /bff/actions/* 在 BFF 內轉成 /bff/v1/commands admission：actor from auth/Idempotency-Key/trace_id+correlation_id/policy decision/audit action/target typed reference。**不可在 EP5 paper-canary closeout 之前 merge runtime change**;PR 準備好但 hold 在 review 直到 EP5 closeout signal。Reviewer Claude 在 EP5 closeout 後才會 approve。 |
| `BFF-CONSOL-020` | BFF Consolidation 2026-05-13 | runAction.ts migration to /bff/v1/commands | Codex2 | todo | `BFF-CONSOL-019` | execute-plans runAction.ts 新 caller 優先發 /bff/v1/commands;舊 caller 維持 /bff/actions/* 但 BFF adapter 轉發;兩條都回同一 CommandResponse shape。confirmToken/approval evidence/typed error 行為對齊 BFF_COMMAND_API_CONTRACT。 |
| `BFF-CONSOL-021` | BFF Consolidation 2026-05-13 | Receipt dual-write + replay/conflict/idempotency tests | Codex | todo | `BFF-CONSOL-019`, `BFF-CONSOL-020` | 舊 action receipt + 新 command receipt 並存。Test cases: same idempotency + same body→replay;same idempotency + diff body→409;missing confirm token→CONFIRM_TOKEN_REQUIRED;missing approval evidence→APPROVAL_REQUIRED。Soak 1 週後啟動 024。 |
| `BFF-CONSOL-022` | BFF Consolidation 2026-05-13 | Lovable staging strict cutover (isolated preview branch) | Gemini2 | todo | `BFF-CONSOL-008`, `BFF-CONSOL-009`, `BFF-CONSOL-010`, `BFF-CONSOL-015` | 開 Lovable preview branch 設 VITE_BFF_MODE=live + VITE_BFF_FALLBACK=strict + VITE_BFF_REAL_WRITES=false。現有 staging 維持 auto fallback 不切。Soak ≥7 day 紀錄 strict mode 下 read/SSE/detail journey 沒 regression。 |
| `BFF-CONSOL-023` | BFF Consolidation 2026-05-13 | Lovable prod strict cutover (staging soak gate) | Gemini2 | todo | `BFF-CONSOL-022` | 等 022 staging soak 1 週 0 regression prod 切 VITE_BFF_FALLBACK=strict (REAL_WRITES 仍 false 直到 operator onboard)。Prod soak ≥7 day 才算 cutover 完成。 |
| `BFF-CONSOL-024` | BFF Consolidation 2026-05-13 | Deprecate old action receipt (after 1-week soak) | Codex | todo | `BFF-CONSOL-021` | 021 dual-write soak 1 週後標 deprecated 保留 /bff/actions/* 路徑但 receipt schema 加 deprecated flag。前端 runAction.ts 預設改打 /bff/v1/commands。 |
| `BFF-CONSOL-025` | BFF Consolidation 2026-05-13 | Seed-only surface elimination | Claude2 | todo | `BFF-CONSOL-015`, `BFF-CONSOL-016`, `BFF-CONSOL-017`, `BFF-CONSOL-018` | 用 007 taxonomy 與 016/017/018 detail evidence 把 live_required helper 全部接上 BFF route mock_only_dev 在 live mode 隱藏 deprecated 移除 deferred 寫進 follow-up task。strict live mode 下沒有頁面會暗中用 seed 當 live 資料。 |
| `BFF-CONSOL-026` | BFF Consolidation 2026-05-13 | CI route diff fail-hard mode | Codex2 | todo | `BFF-CONSOL-001`, `BFF-CONSOL-002`, `BFF-CONSOL-003` | 把 003 的 fail-but-warn 模式切 fail-hard。任何 unmatched route 阻擋 PR merge。CI 必須在 backend manifest 加新 route 後 frontend 也加才 pass;反向亦然 (除非標 mock_only)。 |
| `BFF-CONSOL-027` | BFF Consolidation 2026-05-13 | Final BFF consolidation acceptance packet | Copilot | todo | `BFF-CONSOL-001`, `BFF-CONSOL-002`, `BFF-CONSOL-003`, `BFF-CONSOL-004`, `BFF-CONSOL-005`, `BFF-CONSOL-006`, `BFF-CONSOL-007`, `BFF-CONSOL-008`, `BFF-CONSOL-009`, `BFF-CONSOL-010`, `BFF-CONSOL-011`, `BFF-CONSOL-012`, `BFF-CONSOL-013`, `BFF-CONSOL-014`, `BFF-CONSOL-015`, `BFF-CONSOL-016`, `BFF-CONSOL-017`, `BFF-CONSOL-018`, `BFF-CONSOL-019`, `BFF-CONSOL-020`, `BFF-CONSOL-021`, `BFF-CONSOL-022`, `BFF-CONSOL-023`, `BFF-CONSOL-024`, `BFF-CONSOL-025`, `BFF-CONSOL-026` | 集合 001..026 evidence 輸出 support/sidecars/BFF-CONSOL-FINAL/ACCEPTANCE.md。內容含 contract diff baseline/live smoke (read+write)/SSE evidence/command receipt sample/staging+prod cutover log/soak metric/seed.ts post-state。Copilot 統整 Claude 最終簽核。 |

### External / Upstream Integration Work

| ID | Phase | Task | Owner | Status | Depends On | 中文說明 |
|---|---|---|---|---|---|---|
| _(none)_ | - | - | - | - | - | - |

## Recently Executed Tasks

- Archive updated: 2026-05-13 11:19:09
- Terminal tasks archived: `976` total, `959` completed, `17` superseded

| ID | Phase | Task | Owner | Outcome | Archived At | Snapshot |
|---|---|---|---|---|---|---|
| `BFF-CONSOL-004` | BFF Consolidation 2026-05-13 | Command envelope mapping spec doc | Codex | completed | 2026-05-13 11:19:09 | `ai-task-archive/tasks/BFF-CONSOL-004.json` |
| `BFF-CONSOL-003` | BFF Consolidation 2026-05-13 | CI route diff job (fail-but-warn baseline) | Codex2 | completed | 2026-05-13 11:10:35 | `ai-task-archive/tasks/BFF-CONSOL-003.json` |
| `SVC-RENAME-003` | services Namespace Normalization 2026-05-10 | Pair A — control_plane snake/kebab importlib shim + import site rewrites | Codex2 | completed | 2026-05-13 10:50:33 | `ai-task-archive/tasks/SVC-RENAME-003.json` |
| `BFF-CONSOL-001` | BFF Consolidation 2026-05-13 | Backend FastAPI route manifest extractor | Claude | completed | 2026-05-13 10:48:17 | `ai-task-archive/tasks/BFF-CONSOL-001.json` |
| `EP5-BROKER-TW-002-RERUN-REAL-FIX` | EP5 Broker TW Real Sandbox Smoke 2026-05-13 | Fix RERUN-REAL: remove signed precheck in simulation mode, run stock-only smoke (no futures, no production signed verify) | Codex2 | completed | 2026-05-13 10:37:39 | `ai-task-archive/tasks/EP5-BROKER-TW-002-RERUN-REAL-FIX.json` |
| `BFF-CONSOL-002` | BFF Consolidation 2026-05-13 | Frontend route manifest extractor (execute-plans) | Codex2 | completed | 2026-05-13 10:33:15 | `ai-task-archive/tasks/BFF-CONSOL-002.json` |
| `EP5-BROKER-TW-002-RERUN-REAL` | EP5 Broker TW Real Sandbox Smoke 2026-05-13 | Re-run EP5-BROKER-TW-002 with real Shioaji SDK (no mock) for stock+futures sandbox smoke | Codex2 | superseded | 2026-05-13 09:15:13 | `ai-task-archive/tasks/EP5-BROKER-TW-002-RERUN-REAL.json` |
| `SVC-RENAME-001-SIDECAR-REVIEW` | services Namespace Normalization 2026-05-10 | Prepare SVC-RENAME-001 review packet and evidence summary | Claude | completed | 2026-05-13 01:54:41 | `ai-task-archive/tasks/SVC-RENAME-001-SIDECAR-REVIEW.json` |
| `QLIB-ACT-001-SIDECAR-REVIEW` | Qlib Production Activation 2026-05-10 | Prepare QLIB-ACT-001 review packet and evidence summary | Codex | completed | 2026-05-13 01:53:51 | `ai-task-archive/tasks/QLIB-ACT-001-SIDECAR-REVIEW.json` |
| `BFF-LUV-GAP-007` | BFF Execute-Plans Contract Gap 2026-05-08 | Reconcile extended Agora and FULL-spec routes | Codex | completed | 2026-05-10 19:09:55 | `ai-task-archive/tasks/BFF-LUV-GAP-007.json` |
| `BFF-LUV-GAP-006-SIDECAR-BFF-HANDOFF` | BFF Execute-Plans Contract Gap 2026-05-08 | Prepare BFF-LUV-GAP-006 BFF and frontend handoff packet | Codex2 | completed | 2026-05-10 19:09:55 | `ai-task-archive/tasks/BFF-LUV-GAP-006-SIDECAR-BFF-HANDOFF.json` |
| `BFF-LUV-GAP-006` | BFF Execute-Plans Contract Gap 2026-05-08 | Implement Agora core BFF compatibility | Codex | completed | 2026-05-10 19:09:55 | `ai-task-archive/tasks/BFF-LUV-GAP-006.json` |
| `BFF-LUV-GAP-004-SIDECAR-BFF-HANDOFF` | BFF Execute-Plans Contract Gap 2026-05-08 | Prepare BFF-LUV-GAP-004 BFF and frontend handoff packet | Gemini2 | completed | 2026-05-10 19:09:55 | `ai-task-archive/tasks/BFF-LUV-GAP-004-SIDECAR-BFF-HANDOFF.json` |
| `BFF-LUV-GAP-004` | BFF Execute-Plans Contract Gap 2026-05-08 | Implement evolution experiment jobs and events BFF compatibility | Codex | completed | 2026-05-10 19:09:55 | `ai-task-archive/tasks/BFF-LUV-GAP-004.json` |
| `BFF-LUV-FE-006` | BFF Execute-Plans Frontend Live Completion 2026-05-09 | Deploy execute-plans dev and run frontend BFF E2E closure | Claude | completed | 2026-05-10 14:04:11 | `ai-task-archive/tasks/BFF-LUV-FE-006.json` |
| `BFF-LUV-FE-005` | BFF Execute-Plans Frontend Live Completion 2026-05-09 | Run final execute-plans Lovable live cutover smoke | Claude | completed | 2026-05-10 11:05:52 | `ai-task-archive/tasks/BFF-LUV-FE-005.json` |
| `BFF-LUV-AUTHED-LIVE-001` | BFF Execute-Plans Authenticated Live Completion 2026-05-09 | Run authenticated lupin dev BFF DTO/write smoke | Codex | completed | 2026-05-10 10:56:59 | `ai-task-archive/tasks/BFF-LUV-AUTHED-LIVE-001.json` |
| `BFF-LUV-FE-004` | BFF Execute-Plans Frontend Live Completion 2026-05-09 | Wire execute-plans safe real write flows | Claude2 | completed | 2026-05-10 02:30:16 | `ai-task-archive/tasks/BFF-LUV-FE-004.json` |
| `BFF-LUV-FE-005-SIDECAR-BFF-HANDOFF` | BFF Execute-Plans Frontend Live Completion 2026-05-09 | Prepare BFF-LUV-FE-005 BFF and frontend handoff packet | Claude | completed | 2026-05-10 02:22:27 | `ai-task-archive/tasks/BFF-LUV-FE-005-SIDECAR-BFF-HANDOFF.json` |
| `BFF-LUV-FE-006-SIDECAR-BFF-HANDOFF` | BFF Execute-Plans Frontend Live Completion 2026-05-09 | Prepare BFF-LUV-FE-006 BFF and frontend handoff packet | Codex2 | completed | 2026-05-10 02:20:32 | `ai-task-archive/tasks/BFF-LUV-FE-006-SIDECAR-BFF-HANDOFF.json` |

## Task Board

| ID | Phase | Task | 中文說明 | Owner | Reviewer | Status | Depends On | Last Update | Next |
|---|---|---|---|---|---|---|---|---|---|
| `EP5-BROKER-TW-002-RERUN-REAL-SIDECAR-ACCEPTANCE` | EP5 Broker TW Real Sandbox Smoke 2026-05-13 | [Sidecar] [Auto] [Parent EP5-BROKER-TW-002-RERUN-REAL] Prepare EP5-BROKER-TW-002-RERUN-REAL acceptance packet and dependency map | 平行支援 EP5-BROKER-TW-002-RERUN-REAL，先整理 acceptance checklist、dependency map 與 support packet，不改 canonical truth。 | Claude | Codex | review | - | 2026-05-13 08:53:23 | Acceptance packet prepared at support/sidecars/EP5-BROKER-TW-002-RERUN-REAL/EP5-BROKER-TW-002-RERUN-REAL-SIDECAR-ACCEPTANCE.md. Parent task block identified: stock-smoke=SHIOAJI_ACCOUNT_UNSIGNED, futures-smoke=SHIOAJI_ACCOUNT_MISSING, signed-status.json absent. Code is correct; block is external Sinopac sandbox account activation (human-gate). Remediation guide included. Ready for Codex review. |
| `SVC-RENAME-003-SIDECAR-ACCEPTANCE` | services Namespace Normalization 2026-05-10 | [Sidecar] [Auto] [Parent SVC-RENAME-003] Prepare SVC-RENAME-003 acceptance packet and dependency map | 平行支援 SVC-RENAME-003，先整理 acceptance checklist、dependency map 與 support packet，不改 canonical truth。 | Gemini2 | Codex | review | - | 2026-05-13 08:57:10 | Chair reassigned review from Codex2 to Codex: Codex2 is occupied (working on EP5-BROKER-TW-002-RERUN-REAL, itself blocked on human-gate) and cannot promptly review. Codex is idle and not in any paused lane — moving the review to Codex unblocks the sidecar queue without disrupting ownership. |
| `BFF-CONSOL-005` | BFF Consolidation 2026-05-13 | Live status banner UI (real/hybrid/mock) | 在 execute-plans Management Console / Agora / v5 page header 加即時 live status banner 呼叫 getLiveStatusSnapshot() 顯示 real\|hybrid\|mock-fallback 三種狀態。hybrid/mock 顯示「資料來源：seed」警告。auto fallback 模式下 operator 不會誤判 seed 為 live。 | Codex | Claude | in_progress | - | 2026-05-13 11:22:59 | Located execute-plans workspace and found partial banner/transport artifacts; tightening hybrid seed-warning and strict typed-error display before verification. |
| `BFF-CONSOL-006` | BFF Consolidation 2026-05-13 | Role vocabulary mapping doc | 寫一份 doc 對齊 backend role vocabulary (viewer/operator/approver/admin) vs frontend mock (portfolio_manager/ops) vs MeResponse.roles 對應。輸出 docs/bff/role-vocabulary-mapping-2026-05-13.md。供 BFF-CONSOL-013 cookie write gate 與後續 capability map 使用。 | Claude | Codex | in_progress | - | 2026-05-13 11:14:17 | Supervisor re-dispatched BFF-CONSOL-006; task remains in progress. |
| `BFF-CONSOL-007` | BFF Consolidation 2026-05-13 | Seed taxonomy spreadsheet | 盤點 execute-plans src/lib/bff/seed.ts 全 helper 分四類 live_required\|mock_only_dev\|deprecated\|deferred。輸出 docs/bff/seed-taxonomy-2026-05-13.md 與 seed-taxonomy.json 供 015 mock-only badge 與 025 seed elimination 使用。 | Claude | Codex2 | todo | - | 2026-05-13 11:10:47 | Supervisor preempted BFF-CONSOL-007 to free Claude for higher-priority review/finalize work; task returned to todo until a fresh run restarts it. |
| `BFF-CONSOL-008` | BFF Consolidation 2026-05-13 | Canonical fixture pack A (strategies personas capital-pools rebalances deployments) | 在 pantheon control-plane truth source 建立 canonical fixture pack A (strategies/personas/capital-pools/rebalances/deployments) 各 ≥1 筆 non-empty entry。Strategy 含 specs/experiments/artifacts/lineage/audit linkage。確保 /bff/strategies /bff/personas /bff/capital-pools /bff/rebalances /bff/deployments live list 都回 data_count≥1。 | Codex2 | Codex | in_progress | `BFF-CONSOL-001` | 2026-05-13 11:13:06 | Codex2 picked up fixture pack A implementation; inspecting BFF read store dataset loading and live list endpoints. |
| `BFF-CONSOL-009` | BFF Consolidation 2026-05-13 | Canonical fixture pack B (evolution research artifacts v5 agora runtimes) | canonical fixture pack B (evolution/research/artifacts/v5 interventions/agora/runtimes) 各 ≥1 筆 non-empty。v5 intervention 對應 governed remediation flow;agora 至少有一個 active topic;research 含 ticket 與 analysis sample。 | Claude | Codex2 | todo | `BFF-CONSOL-002` | 2026-05-13 11:10:28 | Supervisor preempted BFF-CONSOL-009 to free Claude for higher-priority review/finalize work; task returned to todo until a fresh run restarts it. |
| `BFF-CONSOL-010` | BFF Consolidation 2026-05-13 | Canonical fixture pack C (alerts incidents approvals audit jobs channels skills tools mcp) | canonical fixture pack C (alerts/incidents/approvals/audit/jobs/channels/skills/tools/mcp) 各 ≥1 筆 non-empty。alert 對 incident;approval 連 deployment;audit 包含至少一個 immutable record。 | Codex | Claude2 | todo | `BFF-CONSOL-008` | 2026-05-13 10:02:51 | Assignment created |
| `BFF-CONSOL-011` | BFF Consolidation 2026-05-13 | SSE real stream replay test | 真測 /bff/events/stream。Browser cookie-session 用 native EventSource(... {withCredentials:true}) Bearer 用 polyfill 帶 Authorization。驗證 open event/first event id/type/timestamp/Last-Event-ID replay/409 SSE_REPLAY_UNAVAILABLE/X-SSE-Resync-Routes。Evidence: support/evidence/BFF-CONSOL-011-sse-replay-smoke.json。 | Codex | Codex2 | todo | - | 2026-05-13 11:10:57 | Supervisor preempted BFF-CONSOL-011 to free Codex for higher-priority review/finalize work; task returned to todo until a fresh run restarts it. |
| `BFF-CONSOL-012` | BFF Consolidation 2026-05-13 | SSE backpressure & unbounded buffer test | client disconnect 後 backend 不該 unbounded buffer。對齊 EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md 的 ordering 規定。Test: simulate slow consumer + disconnect assert backend buffer bound + replay window policy + drop strategy。 | Gemini | Codex2 | todo | `BFF-CONSOL-011` | 2026-05-13 10:03:10 | Assignment created |
| `BFF-CONSOL-013` | BFF Consolidation 2026-05-13 | Cookie-session write gate (/bff/me driven) | 修改 execute-plans liveWriteGated() 從 /bff/me response 判斷 authenticated session (cookie or bearer) 不再只看 sessionStorage bearer token。cookie-only session 不應被誤判為 unauthenticated。後端 /bff/me 需回明確 session_kind: cookie\|bearer\|stub。 | Claude | Codex | todo | `BFF-CONSOL-004`, `BFF-CONSOL-006` | 2026-05-13 10:03:17 | Assignment created |
| `BFF-CONSOL-014` | BFF Consolidation 2026-05-13 | Lovable CORS allowlist + JWKS strict test infra | pantheon BFF main.py 加 PANTHEON_BFF_CORS_ORIGINS 預設 include Lovable preview/dev/prod hostnames。JWKS strict mode 在 CI 跑 issuer/audience/key-rotation test。CORS dev override 在 production strict 模式下不可啟用。 | Codex2 | Codex | todo | - | 2026-05-13 10:10:09 | Supervisor preempted BFF-CONSOL-014 to free Codex2 for higher-priority review/finalize work; task returned to todo until a fresh run restarts it. |
| `BFF-CONSOL-015` | BFF Consolidation 2026-05-13 | Mock-only badge implementation (live mode) | live mode 下若頁面 fetch 失敗回 seed-only helper UI 自動掛 mock data badge;mock_only_dev helper 在 live mode 直接禁用 (raise 或 return null + 顯示空狀態)。讀 BFF-CONSOL-007 taxonomy 作為分類來源。 | Claude2 | Copilot | todo | `BFF-CONSOL-005`, `BFF-CONSOL-007` | 2026-05-13 10:03:36 | Assignment created |
| `BFF-CONSOL-016` | BFF Consolidation 2026-05-13 | Detail journey smoke A (strategy persona deployment runtime) | detail journey smoke A: strategy/persona/deployment/runtime 各跑 list→detail→related tabs。每個 family ≥1 non-empty fixture (來自 008) detail drawer 渲染 tabs 切換 degraded path 都跑過。Evidence support/evidence/BFF-CONSOL-016-detail-smoke-a.json。 | Claude | Codex | todo | `BFF-CONSOL-008` | 2026-05-13 10:03:45 | Assignment created |
| `BFF-CONSOL-017` | BFF Consolidation 2026-05-13 | Detail journey smoke B (evolution research v5 agora artifacts) | detail journey smoke B: evolution/research/v5/agora/artifacts list→detail→related tabs。每個 family 走過 live detail 而非 mock。 | Claude2 | Codex2 | todo | `BFF-CONSOL-009` | 2026-05-13 10:03:55 | Assignment created |
| `BFF-CONSOL-018` | BFF Consolidation 2026-05-13 | Detail journey smoke C (incident approval rebalance job audit) | detail journey smoke C: incident/approval/rebalance/job/audit。job mock fallback undefined 改成 typed 404;audit list-only 特判 detail drawer disabled。 | Claude | Codex2 | todo | `BFF-CONSOL-010` | 2026-05-13 10:04:04 | Assignment created |
| `BFF-CONSOL-019` | BFF Consolidation 2026-05-13 | Command envelope adapter backend impl (gated on EP5 closeout) | 後端 /bff/actions/* 在 BFF 內轉成 /bff/v1/commands admission：actor from auth/Idempotency-Key/trace_id+correlation_id/policy decision/audit action/target typed reference。**不可在 EP5 paper-canary closeout 之前 merge runtime change**;PR 準備好但 hold 在 review 直到 EP5 closeout signal。Reviewer Claude 在 EP5 closeout 後才會 approve。 | Codex | Claude | todo | `BFF-CONSOL-004` | 2026-05-13 10:04:12 | Assignment created |
| `BFF-CONSOL-020` | BFF Consolidation 2026-05-13 | runAction.ts migration to /bff/v1/commands | execute-plans runAction.ts 新 caller 優先發 /bff/v1/commands;舊 caller 維持 /bff/actions/* 但 BFF adapter 轉發;兩條都回同一 CommandResponse shape。confirmToken/approval evidence/typed error 行為對齊 BFF_COMMAND_API_CONTRACT。 | Codex2 | Claude2 | todo | `BFF-CONSOL-019` | 2026-05-13 10:04:21 | Assignment created |
| `BFF-CONSOL-021` | BFF Consolidation 2026-05-13 | Receipt dual-write + replay/conflict/idempotency tests | 舊 action receipt + 新 command receipt 並存。Test cases: same idempotency + same body→replay;same idempotency + diff body→409;missing confirm token→CONFIRM_TOKEN_REQUIRED;missing approval evidence→APPROVAL_REQUIRED。Soak 1 週後啟動 024。 | Codex | Claude | todo | `BFF-CONSOL-019`, `BFF-CONSOL-020` | 2026-05-13 10:04:29 | Assignment created |
| `BFF-CONSOL-022` | BFF Consolidation 2026-05-13 | Lovable staging strict cutover (isolated preview branch) | 開 Lovable preview branch 設 VITE_BFF_MODE=live + VITE_BFF_FALLBACK=strict + VITE_BFF_REAL_WRITES=false。現有 staging 維持 auto fallback 不切。Soak ≥7 day 紀錄 strict mode 下 read/SSE/detail journey 沒 regression。 | Gemini2 | Gemini | todo | `BFF-CONSOL-008`, `BFF-CONSOL-009`, `BFF-CONSOL-010`, `BFF-CONSOL-015` | 2026-05-13 10:04:36 | Assignment created |
| `BFF-CONSOL-023` | BFF Consolidation 2026-05-13 | Lovable prod strict cutover (staging soak gate) | 等 022 staging soak 1 週 0 regression prod 切 VITE_BFF_FALLBACK=strict (REAL_WRITES 仍 false 直到 operator onboard)。Prod soak ≥7 day 才算 cutover 完成。 | Gemini2 | Gemini | todo | `BFF-CONSOL-022` | 2026-05-13 10:04:44 | Assignment created |
| `BFF-CONSOL-024` | BFF Consolidation 2026-05-13 | Deprecate old action receipt (after 1-week soak) | 021 dual-write soak 1 週後標 deprecated 保留 /bff/actions/* 路徑但 receipt schema 加 deprecated flag。前端 runAction.ts 預設改打 /bff/v1/commands。 | Codex | Claude | todo | `BFF-CONSOL-021` | 2026-05-13 10:04:50 | Assignment created |
| `BFF-CONSOL-025` | BFF Consolidation 2026-05-13 | Seed-only surface elimination | 用 007 taxonomy 與 016/017/018 detail evidence 把 live_required helper 全部接上 BFF route mock_only_dev 在 live mode 隱藏 deprecated 移除 deferred 寫進 follow-up task。strict live mode 下沒有頁面會暗中用 seed 當 live 資料。 | Claude2 | Copilot | todo | `BFF-CONSOL-015`, `BFF-CONSOL-016`, `BFF-CONSOL-017`, `BFF-CONSOL-018` | 2026-05-13 10:04:59 | Assignment created |
| `BFF-CONSOL-026` | BFF Consolidation 2026-05-13 | CI route diff fail-hard mode | 把 003 的 fail-but-warn 模式切 fail-hard。任何 unmatched route 阻擋 PR merge。CI 必須在 backend manifest 加新 route 後 frontend 也加才 pass;反向亦然 (除非標 mock_only)。 | Codex2 | Codex | todo | `BFF-CONSOL-001`, `BFF-CONSOL-002`, `BFF-CONSOL-003` | 2026-05-13 11:12:22 | Auto-reassigned ownership from Gemini to Codex2 after repeated Gemini capacity/429: Capacity / rate limit failure. Task returned to todo until Codex2 starts a fresh run. |
| `BFF-CONSOL-027` | BFF Consolidation 2026-05-13 | Final BFF consolidation acceptance packet | 集合 001..026 evidence 輸出 support/sidecars/BFF-CONSOL-FINAL/ACCEPTANCE.md。內容含 contract diff baseline/live smoke (read+write)/SSE evidence/command receipt sample/staging+prod cutover log/soak metric/seed.ts post-state。Copilot 統整 Claude 最終簽核。 | Copilot | Claude | todo | `BFF-CONSOL-001`, `BFF-CONSOL-002`, `BFF-CONSOL-003`, `BFF-CONSOL-004`, `BFF-CONSOL-005`, `BFF-CONSOL-006`, `BFF-CONSOL-007`, `BFF-CONSOL-008`, `BFF-CONSOL-009`, `BFF-CONSOL-010`, `BFF-CONSOL-011`, `BFF-CONSOL-012`, `BFF-CONSOL-013`, `BFF-CONSOL-014`, `BFF-CONSOL-015`, `BFF-CONSOL-016`, `BFF-CONSOL-017`, `BFF-CONSOL-018`, `BFF-CONSOL-019`, `BFF-CONSOL-020`, `BFF-CONSOL-021`, `BFF-CONSOL-022`, `BFF-CONSOL-023`, `BFF-CONSOL-024`, `BFF-CONSOL-025`, `BFF-CONSOL-026` | 2026-05-13 10:05:17 | Assignment created |

## Handoff Queue

| Task | From | To | Message | Status | Created At |
|---|---|---|---|---|---|
| `EP5-BROKER-TW-002-SIDECAR-ACCEPTANCE` | Gemini | Codex2 | Acceptance packet and dependency map for EP5-BROKER-TW-002 prepared at support/sidecars/EP5-BROKER-TW-002/EP5-BROKER-TW-002-SIDECAR-ACCEPTANCE.md. Ready for review and incorporation into parent closeout. | pending | 2026-05-12 22:50:00 |
| `EP5-BROKER-TW-002-RERUN-REAL-SIDECAR-ACCEPTANCE` | Claude | Codex | Acceptance packet prepared at support/sidecars/EP5-BROKER-TW-002-RERUN-REAL/EP5-BROKER-TW-002-RERUN-REAL-SIDECAR-ACCEPTANCE.md. Parent task block identified: stock-smoke=SHIOAJI_ACCOUNT_UNSIGNED, futures-smoke=SHIOAJI_ACCOUNT_MISSING, signed-status.json absent. Code is correct; block is external Sinopac sandbox account activation (human-gate). Remediation guide included. Ready for Codex review. | pending | 2026-05-13 08:53:23 |
| `SVC-RENAME-003-SIDECAR-ACCEPTANCE` | Codex2 | Codex | Chair reassigned review from Codex2 to Codex: Codex2 is occupied (working on EP5-BROKER-TW-002-RERUN-REAL, itself blocked on human-gate) and cannot promptly review. Codex is idle and not in any paused lane — moving the review to Codex unblocks the sidecar queue without disrupting ownership. | pending | 2026-05-13 08:57:10 |

## Blockers

| Task | Owner | Waiting For | Message | Status |
|---|---|---|---|---|
| _(none)_ | - | - | - | - |

## Review Notes

| Task | Reviewer | 修正重點 | Review File |
|---|---|---|---|
| _(none)_ | - | - | - |

## Lovable Coordination

- Last coordination scan: 2026-05-03 18:57:30
- Tracked features: `46`
- Lovable-ready packets: `45`
- Waiting for Lovable/front-end: `0`
- UI-done returned: `46`
- Frontend feedback returned: `46`
- Open BFF gaps: `0`
- Backend route live: `45`
- Pantheon handoff published: `45`
- Mirrored to front default branch: `45`
- Dispatch recorded in coordinator state: `46`
- Receiver-visible payload on front default branch: `45`
- Lovable consumed packet: `46`
- UI activated: `46`
- Runtime verified: `46`

| Feature | Screen | Stage | Lovable Ready | Mirrored | UI Done | Feedback | Next Action |
|---|---|---|---|---|---|---|---|
| `CW-01-consult-request` | consult-request | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `CW-02-debate-transcript` | consultation-debate-transcript | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `CW-03-committee-board` | consultation-committee-board | `loop_complete` | no | no | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `CW-04-redteam-memo` | redteam-memo | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `EW-05-mutation-review` | mutation-review | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `F-042` | promotion-review | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `KW-01-institutional-memory` | institutional-memory | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `KW-02-research-notes` | knowledge-research-notes | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `KW-03-evidence-refs` | knowledge-evidence-refs | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `KW-04-insight-cards` | knowledge-insight-cards | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `KW-05-strategy-spec` | knowledge-strategy-spec | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-001-deployment-review` | deployment-review-console | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-001-governance-review-queue` | governance-review-queue | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-002-incident-action-drawer` | incident-action-drawer | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-002-incident-detail` | incident-detail | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-002-incident-home` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-003-evolution-center` | evolution-center | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-003-inspiration-graph` | inspiration-graph | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-003-lineage-view` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-003-post-incident-review` | post-incident-review-console | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-004-capital-binding-drilldowns` | capital-binding-drilldowns | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-004-deployment-approval-drilldowns` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-004-persona-drilldowns` | persona-drilldowns | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-004-persona-management` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-005-degradation-banner` | global-degradation-banner | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-005-sse-substrate` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-006-approval-queue` | governance-approval-queue | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-007-deployment-diff` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-008-rollback-review` | governance-rollback-review | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-009-governance-audit-rail` | governance-audit-rail | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-010-runtime-state-board` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-011-health-status-board` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-012-alerts-rail` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-013-operator-home` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-014-paper-live-drift` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-consultation-workbench` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `PKT-knowledge-workbench` | - | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `RW-01-research-ticket` | research-ticket | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `RW-02-search` | research-search | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `RW-03-analyze` | research-analyze | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `RW-04-experiment-launch` | experiment-launch | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `RW-05-artifact-compare` | artifact-compare | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `TW-01-teaching-dialog` | teaching-dialog | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `TW-02-parameter-controls` | parameter-controls | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `TW-03-before-after-compare` | before-after-compare | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |
| `TW-04-teaching-replay` | teaching-replay | `loop_complete` | yes | yes | yes | yes | Pantheon closeout record marks the current packet loop complete. |

## Latest Checkpoints

- 2026-05-13 11:19:35 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:19:54 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:19:54 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:20:15 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:20:16 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:20:37 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:20:38 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:20:59 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:20:59 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:21:20 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:21:20 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:21:43 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:21:43 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:22:04 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:22:05 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:22:26 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:22:26 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:22:54 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:22:54 Orchestrator: `BFF-CONSOL-006` Worker suspended for approval apr-20260513T031600Z-2e917e07
- 2026-05-13 11:22:59 Codex: `BFF-CONSOL-005` Located execute-plans workspace and found partial banner/transport artifacts; tightening hybrid seed-warning and strict typed-error display before verification.
