# Current Work

This file is generated from `ai-status.json` and `ai-activity-log.jsonl`.
Do not treat this file as the machine-readable source of truth.
Absolute times below use 台灣時間 (UTC+8).

Last updated: 2026-05-17 07:29:09

## Objective

跨進開發團隊 GAP master rebaseline (docs/04/pantheon_sa_supplemental_2026-05-15/GAP_dev_team_master_rebaseline_2026-05-15.md)，以 pantheon@master + execute-plans@main 為基準。並行 6 條 EPIC，按 P0→P3 階梯推進：(I) EPIC-BFF-P0 (P0 10 task / Sprint 1) — session trio (/bff/me, auth/refresh, logout) + /openapi.json + canonical action endpoint + approval decide + registry reads (strategies/personas/capital-pools/audit)，讓 execute-plans@main 在 VITE_BFF_FALLBACK=strict 下可 bootstrap 核心 Management flow 不再 fallback mock；(II) EPIC-GOV-DEPLOY (P1 5 task / Sprint 2) — ApprovalDecision first-class + DeploymentPlan contract/service + stage planner + deployment projection + pool/runtime compatibility 檢查；(III) EPIC-RUNTIME (P1 6 task / Sprint 3) — RuntimeBinding schema + Runtime Manager skeleton + /bff/runtimes + deploy/pause/replace/rollback actions + loader metadata migration (promotion_state → artifact_state + deployment_stage) + LEAN algorithm-level smoke；(IV) EPIC-TELEMETRY (P2 7 task / Sprint 4) — TelemetryEvent canonical schema + RuntimeHeartbeat ingest + AuditAction backend + /bff/alerts + /bff/incidents + reconciliation record + Postmortem schema/endpoint；(V) EPIC-RESEARCH (P3 28 task / Sprint 5) — Source Ingest (SRC) + StrategySpec (STRAT) + Experiment orchestrator (EXP) + Qlib/vectorbt adapters + Persona/Trainer (PER/TRN) + Imitation dataset (IMT) + Consult/Committee (ASK)；(VI) EPIC-EVOLUTION (P3 3 task / Sprint 6) — EvolutionDecision service + /bff/v5/loop-runs + /bff/v5/sentinel/findings。GAP § 10 最大阻塞：BFF live endpoints 不足 → EPIC-BFF-P0 必須最先收斂；Registry/Promotion canonical 已 implemented，DeploymentPlan/RuntimeBinding 是 governance→execution 缺口；Artifact Loader 仍寫 legacy promotion_state，EX-002 metadata migration 是 execution-side 技術債。fail-closed 鐵律延續：broker production live、capital binding live 仍禁止；canary 需 risk-owner + operator 雙閘；evidence 走 support/evidence/<epic>-<task>/。Track E 收尾備註：46 個 MGMT-* task 中 45 個 done+archive，僅 MGMT-BROKER-002 仍 blocked 等 Shioaji credentials (commit 22e5ca3b 已備 sidecar acceptance packet)；M7 canary readiness 因此未閉合；Track E objective 不在本 sprint 推進範圍，僅 carry-over 記錄。

## Current Sprint

- Sprint: `2026-05-16-pantheon-bff-p0-foundation`
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

- `Claude`: execution, control-plane, governance-review; next: Auto-reassigned ownership from Copilot to Claude after repeated Copilot quota terminal: 402 You have no quota. Task returned to todo until Claude starts a fresh run.
- `Gemini`: gcp, ci-cd, runtime-packaging, worker-ops; next: Assignment created
- `Codex`: integration, status-system, schema, acceptance; next: Implementing qlib rolling-window OOS ExperimentRun pipeline, OOS evaluator, tests, and contract artifacts.
- `Codex2`: integration, status-system, schema, acceptance; next: Assignment created
- `Copilot`: research-ingest, external-search, spec-review, critique; next: Assignment created
- `Claude2`: execution, control-plane, governance-review; next: Supervisor auto-started PER-003 after successful dispatch.
- `Gemini2`: gcp, ci-cd, runtime-packaging, worker-ops; next: Implementation done. Ready for review.

## Delivery Layers

### Primary Project Work

| ID | Phase | Task | Owner | Status | Depends On | 中文說明 |
|---|---|---|---|---|---|---|
| `MGMT-BROKER-002` | Track E / EPIC-05 Shioaji Sandbox | Shioaji account readiness check | Gemini2 | blocked | - | - |
| `IMT-005` | Sprint 7 / EPIC-IMITATION-TRAINING | BC baseline trainer on imitation dataset | Codex | in_progress | `IMT-003`, `IMT-004` | 新增 BC (Behavior Cloning) baseline trainer，吃 IMT-003 dataset builder 產出的 dataset，輸出 behavior_policy artifact (IMT-004 type)。獨立檔案，無共用 module。 |
| `IMT-006` | Sprint 7 / EPIC-IMITATION-TRAINING | Imitation evaluation metrics: action-match + return-gap + KL | Codex | in_progress | `IMT-001`, `IMT-004` | 新增 imitation evaluation metrics module：action-match accuracy, return-gap vs expert baseline, KL divergence。獨立於 bc_trainer.py。 |
| `IMT-007` | Sprint 7 / EPIC-IMITATION-TRAINING | Behavior-policy artifact validation gate | Claude | todo | `IMT-004`, `IMT-006` | behavior_policy artifact 進入 registry / governance 前的驗證閘門：metadata 完整、checksum 一致、IMT-006 eval metrics 達門檻、policy 不出現 deploy/canary/live 觸發詞。獨立 module。 |
| `IMT-008` | Sprint 7 / EPIC-IMITATION-TRAINING | TRL preference-pair dataset bridge | Copilot | todo | `IMT-002`, `IMT-003` | 新增 TRL (Transformer Reinforcement Learning) preference-pair dataset bridge：把 IMT-002 PreferenceExample / CorrectionTrace 轉成 TRL 期望的 chosen/rejected 對。獨立 module。 |
| `TRN-005` | Sprint 7 / EPIC-TRAINER-ADVANCED | Trainer commit -> persona policy lineage edge | Codex | in_progress | `TRN-004` | 把 TRN-004 commit 動作的副作用接到 persona policy 的 lineage：每次 commit 在 persona policy artifact 上產生 lineage edge 指向 trainer session id 與 teaching event ids。獨立 module。 |
| `TRN-006` | Sprint 7 / EPIC-TRAINER-ADVANCED | Rapid-eval -> vectorbt backend integration | Codex2 | todo | `TRN-003`, `VBT-001` | 把 TRN-003 rapid-eval skeleton 接到實際 backend（vectorbt VBT-001 via adapter facade）。獨立檔案，不修 TRN-003 既有 endpoint signature。 |
| `TRN-007` | Sprint 7 / EPIC-TRAINER-ADVANCED | Trainer trace -> imitation dataset export | Copilot | todo | `TRN-001`, `IMT-002` | 把 trainer teaching_event stream 匯出成 imitation dataset 可消化的格式。獨立 module，不修 TRN-001 schema。 |
| `PER-003` | Sprint 7 / EPIC-TRAINER-ADVANCED | Persona registry live integration acceptance | Claude2 | in_progress | `PER-001`, `PER-002` | 把 execute-plans Persona 頁面從 fixture-backed 切換到 live persona_registry service。確認 /bff/personas 與 /bff/personas/{id} read path 走 services/control-plane/persona/persona_registry.py。獨立 acceptance。 |
| `ASK-006` | Sprint 7 / EPIC-CONSULT-ADVANCED | Consult -> Committee -> Memo -> Review e2e test | Codex | todo | `ASK-001`, `ASK-002`, `ASK-003`, `ASK-004`, `ASK-005` | ASK-001..005 已落地 consult/committee flow，這個 task 寫一條 e2e integration test：ask session create -> committee invoke -> memo publish -> Management review queue 接到 handoff。獨立 test 檔。 |
| `ASK-007` | Sprint 7 / EPIC-CONSULT-ADVANCED | Consult memo evidence redaction regression | Codex2 | todo | `ASK-004` | 驗證 consult memo publish 流程的 evidence redaction：persona-internal 機密欄位（policy_internals memory_trace internal_score）不能洩漏到 review-facing memo。獨立 test 檔。 |
| `ASK-008` | Sprint 7 / EPIC-CONSULT-ADVANCED | Committee sponsor decision -> governance action bridge | Claude | todo | `ASK-003`, `GOV-001`, `EVO-001` | committee 結出 sponsor decision 後，提供把 sponsor decision bridge 到 governance action (例如觸發 ApprovalDecision proposal 或 EvolutionDecision proposal) 的 module。獨立 module，不直接改 governance service。 |
| `LEAN-ALGO-001` | Sprint 7 / EPIC-LEAN-RUNTIME | LEAN algorithm-level smoke via artifact loader | Gemini | todo | `EX-002-RB`, `EX-003`, `RT-002` | EX-003 已完成 smoke path 但 LEAN algorithm-level coverage 還 deferred。這個 task 寫一個最小 LEAN Python algorithm，從 artifact loader 載入 approved artifact 跑一段 paper backtest，驗證 RuntimeBinding 串到 LEAN runtime 的 actual run path。CPU-only smoke。 |
| `OPS-REFACTOR-001` | Sprint 7 / EPIC-OPS-BACKLOG | Re-apply dispatch policy refactor on current master | Claude | todo | - | 把 archive/codex-orchestrator-dispatch-policy-cleanup-2026-04-28 tag 內的 dispatch_policy 抽取重新套用到當前 supervisor.py。原 cherry-pick 因 supervisor.py 1776 commit drift 衝突；本任務以 current master 為基準重做。獨立新增 .orchestrator/dispatch_policy.py + test。 |
| `OPS-WORKER-PUSH-CRED-001` | Sprint 7 / EPIC-OPS-BACKLOG | Background worker git push credentials provisioning | Gemini | todo | - | 解決 background worker 跑 git push 必失敗的根因。設計選項：SSH key per worker 或 GitHub PAT via env。產出 setup 腳本與 .orchestrator/ runtime 環境讀取邏輯，不直接 commit credential 本身。獨立檔案。 |
| `OPS-REBASE-AUTO-001` | Sprint 7 / EPIC-OPS-BACKLOG | Auto-handle empty commits in worker rebase flow | Claude2 | todo | - | 修正 worker 跑 git pull --rebase 遇到 # empty pick 會卡 approval queue 的問題。設計 rebase_helper module 自動帶 --allow-empty 或 --skip 策略，supervisor.py 改用 helper 1 行替換。獨立 helper module 與 OPS-REFACTOR-001 不衝突。 |
| `OPS-SIDECAR-CLEANUP-001` | Sprint 7 / EPIC-OPS-BACKLOG | Sidecar packet retention and cleanup policy | Codex | todo | - | support/sidecars/ 持續累積 packets 但缺退場機制。設計 retention/cleanup module：parent task done 後 N 天，sidecar packet 移至 support/sidecars/archived/，超過 M 天直接刪。獨立 module，可由 cron / chair-review 觸發。 |

### External / Upstream Integration Work

| ID | Phase | Task | Owner | Status | Depends On | 中文說明 |
|---|---|---|---|---|---|---|
| `OSS-QLIB-002` | Sprint 7 / EPIC-OSS-RESEARCH | Qlib rolling-window OOS pipeline + eval | Codex | in_progress | - | 在 services/research/qlib/ 之上實作 rolling-window / OOS pipeline。建在已 archived 的 QLIB-001 dataset manifest 基礎上，產出 OOS metrics 並寫回 ExperimentRun。 |
| `OSS-STAT-001` | Sprint 7 / EPIC-OSS-RESEARCH | statsmodels cointegration adapter skeleton | Claude | todo | - | 新增 services/research/statsmodels/ adapter，落實 stat-arb 風格 cointegration / Engle-Granger 檢定，產生 signal_snapshot artifact。獨立於其他 research adapter，無共用檔案。 |
| `OSS-QUANTLIB-001` | Sprint 7 / EPIC-OSS-RESEARCH | QuantLib option pricing adapter skeleton | Copilot | todo | - | 新增 services/research/quantlib/ adapter，落實 vanilla European/American option Black-Scholes 與 Binomial 定價，產生 pricing_snapshot artifact。獨立於其他 research adapter。 |
| `OSS-RLLIB-001` | Sprint 7 / EPIC-OSS-RESEARCH | RLlib PPO adapter skeleton | Claude | todo | - | 新增 services/research/rllib/ adapter skeleton，落實 Ray RLlib PPO 訓練 mini-loop，輸出 model_artifact。CPU-only smoke (no GPU)，獨立於其他 research adapter。 |
| `OSS-FINRL-001` | Sprint 7 / EPIC-OSS-RESEARCH | FinRL DQN/PPO adapter skeleton | Gemini2 | review | - | 新增 services/research/finrl/ adapter skeleton，落實 FinRL DQN/PPO 在歷史 OHLCV 上 mini-training，輸出 model_artifact。CPU-only smoke。獨立於其他 research adapter。 |

## Recently Executed Tasks

- Archive updated: 2026-05-16 23:21:18
- Terminal tasks archived: `1158` total, `1138` completed, `20` superseded

| ID | Phase | Task | Owner | Outcome | Archived At | Snapshot |
|---|---|---|---|---|---|---|
| `EP5-BROKER-TW-002-SIDECAR-ACCEPTANCE` | Sidecar / EP5 broker TW acceptance review | Review EP5-BROKER-TW-002 sidecar acceptance packet | Codex | completed | 2026-05-16 23:21:18 | `ai-task-archive/tasks/EP5-BROKER-TW-002-SIDECAR-ACCEPTANCE.json` |
| `ASK-005` | Sprint 5 / EPIC-RESEARCH | approval / ask SSE event publishing | Claude | completed | 2026-05-16 22:25:26 | `ai-task-archive/tasks/ASK-005.json` |
| `ASK-005-SIDECAR-REVIEW` | Sprint 5 / EPIC-RESEARCH | Prepare ASK-005 review packet and evidence summary | Codex | completed | 2026-05-16 22:13:36 | `ai-task-archive/tasks/ASK-005-SIDECAR-REVIEW.json` |
| `EVO-001` | Sprint 6 / EPIC-EVOLUTION | EvolutionDecision service | Claude | completed | 2026-05-16 20:05:30 | `ai-task-archive/tasks/EVO-001.json` |
| `EVO-001-SIDECAR-REVIEW` | Sprint 6 / EPIC-EVOLUTION | Prepare EVO-001 review packet and evidence summary | Codex | superseded | 2026-05-16 20:00:55 | `ai-task-archive/tasks/EVO-001-SIDECAR-REVIEW.json` |
| `SENT-001` | Sprint 6 / EPIC-EVOLUTION | /bff/v5/sentinel/findings endpoint | Claude2 | completed | 2026-05-16 19:08:06 | `ai-task-archive/tasks/SENT-001.json` |
| `ASK-004` | Sprint 5 / EPIC-RESEARCH | memo publish to registry / review | Codex | completed | 2026-05-16 19:07:24 | `ai-task-archive/tasks/ASK-004.json` |
| `ASK-002` | Sprint 5 / EPIC-RESEARCH | ConsultRequest / ConsultMemo schema | Codex | completed | 2026-05-16 18:53:03 | `ai-task-archive/tasks/ASK-002.json` |
| `IMT-004` | Sprint 5 / EPIC-RESEARCH | behavior policy artifact type registration | Codex | completed | 2026-05-16 18:48:08 | `ai-task-archive/tasks/IMT-004.json` |
| `ASK-002-SIDECAR-REVIEW` | Sprint 5 / EPIC-RESEARCH | Prepare ASK-002 review packet and evidence summary | Claude | superseded | 2026-05-16 18:47:58 | `ai-task-archive/tasks/ASK-002-SIDECAR-REVIEW.json` |
| `TRN-004` | Sprint 5 / EPIC-RESEARCH | trainer commit / discard / replay | Codex | completed | 2026-05-16 18:47:33 | `ai-task-archive/tasks/TRN-004.json` |
| `LOOP-001-RB` | Sprint 6 / EPIC-EVOLUTION | /bff/v5/loop-runs endpoint (rebaseline) | Claude2 | completed | 2026-05-16 18:46:09 | `ai-task-archive/tasks/LOOP-001-RB.json` |
| `IMT-001` | Sprint 5 / EPIC-RESEARCH | TraderTrajectory schema | Codex | completed | 2026-05-16 18:40:26 | `ai-task-archive/tasks/IMT-001.json` |
| `TRN-002` | Sprint 5 / EPIC-RESEARCH | trainer session endpoints | Codex | completed | 2026-05-16 18:33:35 | `ai-task-archive/tasks/TRN-002.json` |
| `ASK-003` | Sprint 5 / EPIC-RESEARCH | ask / committee session lifecycle | Claude2 | completed | 2026-05-16 18:19:53 | `ai-task-archive/tasks/ASK-003.json` |
| `IMT-002` | Sprint 5 / EPIC-RESEARCH | PreferenceExample / CorrectionTrace schema | Codex | completed | 2026-05-16 18:07:48 | `ai-task-archive/tasks/IMT-002.json` |
| `ASK-001` | Sprint 5 / EPIC-RESEARCH | /bff/agora/ask/sessions | Codex | completed | 2026-05-16 18:03:43 | `ai-task-archive/tasks/ASK-001.json` |
| `TRN-001` | Sprint 5 / EPIC-RESEARCH | TeachingSession / TeachingEvent schema | Codex | completed | 2026-05-16 17:57:42 | `ai-task-archive/tasks/TRN-001.json` |
| `EXP-001` | Sprint 5 / EPIC-RESEARCH | ExperimentTask / ExperimentRun schema | Codex | completed | 2026-05-16 17:53:55 | `ai-task-archive/tasks/EXP-001.json` |
| `IMT-003` | Sprint 5 / EPIC-RESEARCH | imitation dataset builder skeleton | Claude2 | completed | 2026-05-16 17:40:02 | `ai-task-archive/tasks/IMT-003.json` |

## Task Board

| ID | Phase | Task | 中文說明 | Owner | Reviewer | Status | Depends On | Last Update | Next |
|---|---|---|---|---|---|---|---|---|---|
| `MGMT-BROKER-002` | Track E / EPIC-05 Shioaji Sandbox | Shioaji account readiness check | - | Gemini2 | Gemini | blocked | - | 2026-05-15 23:15:06 | Waiting for broker credentials (API_KEY/SECRET_KEY) to proceed with account readiness check. |
| `OSS-QLIB-002` | Sprint 7 / EPIC-OSS-RESEARCH | Qlib rolling-window OOS pipeline + eval | 在 services/research/qlib/ 之上實作 rolling-window / OOS pipeline。建在已 archived 的 QLIB-001 dataset manifest 基礎上，產出 OOS metrics 並寫回 ExperimentRun。 | Codex | Codex2 | in_progress | - | 2026-05-17 07:29:09 | Implementing qlib rolling-window OOS ExperimentRun pipeline, OOS evaluator, tests, and contract artifacts. |
| `OSS-STAT-001` | Sprint 7 / EPIC-OSS-RESEARCH | statsmodels cointegration adapter skeleton | 新增 services/research/statsmodels/ adapter，落實 stat-arb 風格 cointegration / Engle-Granger 檢定，產生 signal_snapshot artifact。獨立於其他 research adapter，無共用檔案。 | Claude | Codex | todo | - | 2026-05-17 07:28:05 | Auto-reassigned ownership from Copilot to Claude after repeated Copilot quota terminal: 402 You have no quota. Task returned to todo until Claude starts a fresh run. |
| `OSS-QUANTLIB-001` | Sprint 7 / EPIC-OSS-RESEARCH | QuantLib option pricing adapter skeleton | 新增 services/research/quantlib/ adapter，落實 vanilla European/American option Black-Scholes 與 Binomial 定價，產生 pricing_snapshot artifact。獨立於其他 research adapter。 | Copilot | Codex2 | todo | - | 2026-05-17 07:20:58 | Assignment created |
| `OSS-RLLIB-001` | Sprint 7 / EPIC-OSS-RESEARCH | RLlib PPO adapter skeleton | 新增 services/research/rllib/ adapter skeleton，落實 Ray RLlib PPO 訓練 mini-loop，輸出 model_artifact。CPU-only smoke (no GPU)，獨立於其他 research adapter。 | Claude | Codex | todo | - | 2026-05-17 07:27:46 | Auto-reassigned ownership from Gemini to Claude after repeated Gemini capacity/429: Capacity / rate limit failure. Task returned to todo until Claude starts a fresh run. |
| `OSS-FINRL-001` | Sprint 7 / EPIC-OSS-RESEARCH | FinRL DQN/PPO adapter skeleton | 新增 services/research/finrl/ adapter skeleton，落實 FinRL DQN/PPO 在歷史 OHLCV 上 mini-training，輸出 model_artifact。CPU-only smoke。獨立於其他 research adapter。 | Gemini2 | Codex2 | review | - | 2026-05-17 07:29:09 | Implementation done. Ready for review. |
| `IMT-005` | Sprint 7 / EPIC-IMITATION-TRAINING | BC baseline trainer on imitation dataset | 新增 BC (Behavior Cloning) baseline trainer，吃 IMT-003 dataset builder 產出的 dataset，輸出 behavior_policy artifact (IMT-004 type)。獨立檔案，無共用 module。 | Codex | Codex2 | in_progress | `IMT-003`, `IMT-004` | 2026-05-17 07:27:02 | Supervisor auto-started IMT-005 after successful dispatch. |
| `IMT-006` | Sprint 7 / EPIC-IMITATION-TRAINING | Imitation evaluation metrics: action-match + return-gap + KL | 新增 imitation evaluation metrics module：action-match accuracy, return-gap vs expert baseline, KL divergence。獨立於 bc_trainer.py。 | Codex | Codex2 | in_progress | `IMT-001`, `IMT-004` | 2026-05-17 07:25:28 | Supervisor auto-started IMT-006 after successful dispatch. |
| `IMT-007` | Sprint 7 / EPIC-IMITATION-TRAINING | Behavior-policy artifact validation gate | behavior_policy artifact 進入 registry / governance 前的驗證閘門：metadata 完整、checksum 一致、IMT-006 eval metrics 達門檻、policy 不出現 deploy/canary/live 觸發詞。獨立 module。 | Claude | Codex2 | todo | `IMT-004`, `IMT-006` | 2026-05-17 07:22:33 | Assignment created |
| `IMT-008` | Sprint 7 / EPIC-IMITATION-TRAINING | TRL preference-pair dataset bridge | 新增 TRL (Transformer Reinforcement Learning) preference-pair dataset bridge：把 IMT-002 PreferenceExample / CorrectionTrace 轉成 TRL 期望的 chosen/rejected 對。獨立 module。 | Copilot | Codex | todo | `IMT-002`, `IMT-003` | 2026-05-17 07:22:46 | Assignment created |
| `TRN-005` | Sprint 7 / EPIC-TRAINER-ADVANCED | Trainer commit -> persona policy lineage edge | 把 TRN-004 commit 動作的副作用接到 persona policy 的 lineage：每次 commit 在 persona policy artifact 上產生 lineage edge 指向 trainer session id 與 teaching event ids。獨立 module。 | Codex | Claude | in_progress | `TRN-004` | 2026-05-17 07:28:23 | Implementing independent trainer commit persona-policy lineage edge module and focused tests. |
| `TRN-006` | Sprint 7 / EPIC-TRAINER-ADVANCED | Rapid-eval -> vectorbt backend integration | 把 TRN-003 rapid-eval skeleton 接到實際 backend（vectorbt VBT-001 via adapter facade）。獨立檔案，不修 TRN-003 既有 endpoint signature。 | Codex2 | Codex | todo | `TRN-003`, `VBT-001` | 2026-05-17 07:23:50 | Assignment created |
| `TRN-007` | Sprint 7 / EPIC-TRAINER-ADVANCED | Trainer trace -> imitation dataset export | 把 trainer teaching_event stream 匯出成 imitation dataset 可消化的格式。獨立 module，不修 TRN-001 schema。 | Copilot | Codex2 | todo | `TRN-001`, `IMT-002` | 2026-05-17 07:24:02 | Assignment created |
| `PER-003` | Sprint 7 / EPIC-TRAINER-ADVANCED | Persona registry live integration acceptance | 把 execute-plans Persona 頁面從 fixture-backed 切換到 live persona_registry service。確認 /bff/personas 與 /bff/personas/{id} read path 走 services/control-plane/persona/persona_registry.py。獨立 acceptance。 | Claude2 | Codex2 | in_progress | `PER-001`, `PER-002` | 2026-05-17 07:25:10 | Supervisor auto-started PER-003 after successful dispatch. |
| `ASK-006` | Sprint 7 / EPIC-CONSULT-ADVANCED | Consult -> Committee -> Memo -> Review e2e test | ASK-001..005 已落地 consult/committee flow，這個 task 寫一條 e2e integration test：ask session create -> committee invoke -> memo publish -> Management review queue 接到 handoff。獨立 test 檔。 | Codex | Codex2 | todo | `ASK-001`, `ASK-002`, `ASK-003`, `ASK-004`, `ASK-005` | 2026-05-17 07:24:59 | Assignment created |
| `ASK-007` | Sprint 7 / EPIC-CONSULT-ADVANCED | Consult memo evidence redaction regression | 驗證 consult memo publish 流程的 evidence redaction：persona-internal 機密欄位（policy_internals memory_trace internal_score）不能洩漏到 review-facing memo。獨立 test 檔。 | Codex2 | Codex | todo | `ASK-004` | 2026-05-17 07:25:17 | Assignment created |
| `ASK-008` | Sprint 7 / EPIC-CONSULT-ADVANCED | Committee sponsor decision -> governance action bridge | committee 結出 sponsor decision 後，提供把 sponsor decision bridge 到 governance action (例如觸發 ApprovalDecision proposal 或 EvolutionDecision proposal) 的 module。獨立 module，不直接改 governance service。 | Claude | Codex2 | todo | `ASK-003`, `GOV-001`, `EVO-001` | 2026-05-17 07:25:38 | Assignment created |
| `LEAN-ALGO-001` | Sprint 7 / EPIC-LEAN-RUNTIME | LEAN algorithm-level smoke via artifact loader | EX-003 已完成 smoke path 但 LEAN algorithm-level coverage 還 deferred。這個 task 寫一個最小 LEAN Python algorithm，從 artifact loader 載入 approved artifact 跑一段 paper backtest，驗證 RuntimeBinding 串到 LEAN runtime 的 actual run path。CPU-only smoke。 | Gemini | Gemini2 | todo | `EX-002-RB`, `EX-003`, `RT-002` | 2026-05-17 07:26:26 | Assignment created |
| `OPS-REFACTOR-001` | Sprint 7 / EPIC-OPS-BACKLOG | Re-apply dispatch policy refactor on current master | 把 archive/codex-orchestrator-dispatch-policy-cleanup-2026-04-28 tag 內的 dispatch_policy 抽取重新套用到當前 supervisor.py。原 cherry-pick 因 supervisor.py 1776 commit drift 衝突；本任務以 current master 為基準重做。獨立新增 .orchestrator/dispatch_policy.py + test。 | Claude | Claude2 | todo | - | 2026-05-17 07:27:36 | Assignment created |
| `OPS-WORKER-PUSH-CRED-001` | Sprint 7 / EPIC-OPS-BACKLOG | Background worker git push credentials provisioning | 解決 background worker 跑 git push 必失敗的根因。設計選項：SSH key per worker 或 GitHub PAT via env。產出 setup 腳本與 .orchestrator/ runtime 環境讀取邏輯，不直接 commit credential 本身。獨立檔案。 | Gemini | Gemini2 | todo | - | 2026-05-17 07:28:01 | Assignment created |
| `OPS-REBASE-AUTO-001` | Sprint 7 / EPIC-OPS-BACKLOG | Auto-handle empty commits in worker rebase flow | 修正 worker 跑 git pull --rebase 遇到 # empty pick 會卡 approval queue 的問題。設計 rebase_helper module 自動帶 --allow-empty 或 --skip 策略，supervisor.py 改用 helper 1 行替換。獨立 helper module 與 OPS-REFACTOR-001 不衝突。 | Claude2 | Claude | todo | - | 2026-05-17 07:28:23 | Assignment created |
| `OPS-SIDECAR-CLEANUP-001` | Sprint 7 / EPIC-OPS-BACKLOG | Sidecar packet retention and cleanup policy | support/sidecars/ 持續累積 packets 但缺退場機制。設計 retention/cleanup module：parent task done 後 N 天，sidecar packet 移至 support/sidecars/archived/，超過 M 天直接刪。獨立 module，可由 cron / chair-review 觸發。 | Codex | Codex2 | todo | - | 2026-05-17 07:28:52 | Assignment created |

## Handoff Queue

| Task | From | To | Message | Status | Created At |
|---|---|---|---|---|---|
| `OSS-FINRL-001` | Gemini2 | Codex2 | Implementation done. Ready for review. | pending | 2026-05-17 07:29:09 |

## Blockers

| Task | Owner | Waiting For | Message | Status |
|---|---|---|---|---|
| `MGMT-BROKER-002` | Gemini2 | Gemini | Waiting for broker credentials (API_KEY/SECRET_KEY) to proceed with account readiness check. | open |

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

- 2026-05-17 07:27:44 Orchestrator: PreToolUse: Grep
- 2026-05-17 07:27:45 Orchestrator: PostToolUse: Grep
- 2026-05-17 07:27:46 Orchestrator: `OSS-STAT-001` Supervisor auto-started OSS-STAT-001 after successful dispatch.
- 2026-05-17 07:27:46 Orchestrator: `OSS-RLLIB-001` Paused new dispatches for gemini until 2026-05-17 07:42:46 after terminal quota failure: Capacity / rate limit failure
- 2026-05-17 07:27:54 Orchestrator: PreToolUse: Read
- 2026-05-17 07:27:55 Orchestrator: PostToolUse: Read
- 2026-05-17 07:28:01 Codex: `OPS-WORKER-PUSH-CRED-001` Assigned OPS-WORKER-PUSH-CRED-001 to Gemini with reviewer Gemini2
- 2026-05-17 07:28:05 Orchestrator: `OSS-RLLIB-001` Auto-reassigned ownership from Gemini to Claude after repeated Gemini capacity/429: Capacity / rate limit failure. Task returned to todo until Claude starts a fresh run.
- 2026-05-17 07:28:05 Orchestrator: `OSS-STAT-001` Paused new dispatches for copilot until 2026-05-17 07:43:05 after terminal quota failure: 402 You have no quota
- 2026-05-17 07:28:22 Orchestrator: PreToolUse: Grep
- 2026-05-17 07:28:22 Orchestrator: PostToolUse: Grep
- 2026-05-17 07:28:23 Codex: `OPS-REBASE-AUTO-001` Assigned OPS-REBASE-AUTO-001 to Claude2 with reviewer Claude
- 2026-05-17 07:28:23 Codex: `TRN-005` Implementing independent trainer commit persona-policy lineage edge module and focused tests.
- 2026-05-17 07:28:31 Orchestrator: `OSS-STAT-001` Auto-reassigned ownership from Copilot to Claude after repeated Copilot quota terminal: 402 You have no quota. Task returned to todo until Claude starts a fresh run.
- 2026-05-17 07:28:45 Orchestrator: PreToolUse: Grep
- 2026-05-17 07:28:46 Orchestrator: PostToolUse: Grep
- 2026-05-17 07:28:52 Codex: `OPS-SIDECAR-CLEANUP-001` Assigned OPS-SIDECAR-CLEANUP-001 to Codex with reviewer Codex2
- 2026-05-17 07:29:06 Orchestrator: PostToolUse: Bash
- 2026-05-17 07:29:09 Codex: `OSS-QLIB-002` Implementing qlib rolling-window OOS ExperimentRun pipeline, OOS evaluator, tests, and contract artifacts.
- 2026-05-17 07:29:09 Gemini2: `OSS-FINRL-001` Handoff to Codex2: Implementation done. Ready for review.
