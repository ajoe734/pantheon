# Current Work

This file is generated from `ai-status.json` and `ai-activity-log.jsonl`.
Do not treat this file as the machine-readable source of truth.
Absolute times below use 台灣時間 (UTC+8).

Last updated: 2026-05-17 09:36:37

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

- `Claude`: execution, control-plane, governance-review; next: Supervisor preempted OSS-STAT-001 to free Claude for higher-priority review/finalize work; task returned to todo until a fresh run restarts it.
- `Gemini`: gcp, ci-cd, runtime-packaging, worker-ops; next: Acceptance packet drafted and ready for review in support/sidecars/OSS-STAT-001/OSS-STAT-001-SIDECAR-ACCEPTANCE.md. The packet documents the resolved shadowing issue and the final artifact shapes.
- `Codex`: integration, status-system, schema, acceptance; next: Chair reassigned review from Codex2 to Claude2: Codex2 pause blocks this review and ASK-006 is on the current consultation path.
- `Codex2`: integration, status-system, schema, acceptance; next: No active assignment
- `Copilot`: research-ingest, external-search, spec-review, critique; next: No active assignment
- `Claude2`: execution, control-plane, governance-review; next: Supervisor paused finalize on PER-003 to free Claude2 for higher-priority review work; task remains review_approved.
- `Gemini2`: gcp, ci-cd, runtime-packaging, worker-ops; next: Supervisor re-dispatched OSS-FINRL-001; task remains in progress.

## Delivery Layers

### Primary Project Work

| ID | Phase | Task | Owner | Status | Depends On | 中文說明 |
|---|---|---|---|---|---|---|
| `MGMT-BROKER-002` | Track E / EPIC-05 Shioaji Sandbox | Shioaji account readiness check | Gemini2 | blocked | - | - |
| `IMT-006` | Sprint 7 / EPIC-IMITATION-TRAINING | Imitation evaluation metrics: action-match + return-gap + KL | Codex | review | `IMT-001`, `IMT-004` | 新增 imitation evaluation metrics module：action-match accuracy, return-gap vs expert baseline, KL divergence。獨立於 bc_trainer.py。 |
| `IMT-007` | Sprint 7 / EPIC-IMITATION-TRAINING | Behavior-policy artifact validation gate | Claude | todo | `IMT-004`, `IMT-006` | behavior_policy artifact 進入 registry / governance 前的驗證閘門：metadata 完整、checksum 一致、IMT-006 eval metrics 達門檻、policy 不出現 deploy/canary/live 觸發詞。獨立 module。 |
| `IMT-008` | Sprint 7 / EPIC-IMITATION-TRAINING | TRL preference-pair dataset bridge | Claude | todo | `IMT-002`, `IMT-003` | 新增 TRL (Transformer Reinforcement Learning) preference-pair dataset bridge：把 IMT-002 PreferenceExample / CorrectionTrace 轉成 TRL 期望的 chosen/rejected 對。獨立 module。 |
| `TRN-006` | Sprint 7 / EPIC-TRAINER-ADVANCED | Rapid-eval -> vectorbt backend integration | Codex | review_approved | `TRN-003`, `VBT-001` | 把 TRN-003 rapid-eval skeleton 接到實際 backend（vectorbt VBT-001 via adapter facade）。獨立檔案，不修 TRN-003 既有 endpoint signature。 |
| `PER-003` | Sprint 7 / EPIC-TRAINER-ADVANCED | Persona registry live integration acceptance | Claude2 | review_approved | `PER-001`, `PER-002` | 把 execute-plans Persona 頁面從 fixture-backed 切換到 live persona_registry service。確認 /bff/personas 與 /bff/personas/{id} read path 走 services/control-plane/persona/persona_registry.py。獨立 acceptance。 |
| `ASK-006` | Sprint 7 / EPIC-CONSULT-ADVANCED | Consult -> Committee -> Memo -> Review e2e test | Codex | review | `ASK-001`, `ASK-002`, `ASK-003`, `ASK-004`, `ASK-005` | ASK-001..005 已落地 consult/committee flow，這個 task 寫一條 e2e integration test：ask session create -> committee invoke -> memo publish -> Management review queue 接到 handoff。獨立 test 檔。 |
| `ASK-007` | Sprint 7 / EPIC-CONSULT-ADVANCED | Consult memo evidence redaction regression | Codex | review | `ASK-004` | 驗證 consult memo publish 流程的 evidence redaction：persona-internal 機密欄位（policy_internals memory_trace internal_score）不能洩漏到 review-facing memo。獨立 test 檔。 |
| `ASK-008` | Sprint 7 / EPIC-CONSULT-ADVANCED | Committee sponsor decision -> governance action bridge | Claude | todo | `ASK-003`, `GOV-001`, `EVO-001` | committee 結出 sponsor decision 後，提供把 sponsor decision bridge 到 governance action (例如觸發 ApprovalDecision proposal 或 EvolutionDecision proposal) 的 module。獨立 module，不直接改 governance service。 |
| `OPS-REFACTOR-001` | Sprint 7 / EPIC-OPS-BACKLOG | Re-apply dispatch policy refactor on current master | Claude | todo | - | 把 archive/codex-orchestrator-dispatch-policy-cleanup-2026-04-28 tag 內的 dispatch_policy 抽取重新套用到當前 supervisor.py。原 cherry-pick 因 supervisor.py 1776 commit drift 衝突；本任務以 current master 為基準重做。獨立新增 .orchestrator/dispatch_policy.py + test。 |
| `OPS-SIDECAR-CLEANUP-001` | Sprint 7 / EPIC-OPS-BACKLOG | Sidecar packet retention and cleanup policy | Codex | review | - | support/sidecars/ 持續累積 packets 但缺退場機制。設計 retention/cleanup module：parent task done 後 N 天，sidecar packet 移至 support/sidecars/archived/，超過 M 天直接刪。獨立 module，可由 cron / chair-review 觸發。 |
| `OPS-REBASE-AUTO-001-SIDECAR-REVIEW` | Sprint 7 / EPIC-OPS-BACKLOG | [Sidecar] [Auto] [Parent OPS-REBASE-AUTO-001] Prepare OPS-REBASE-AUTO-001 review packet and evidence summary | Claude | todo | - | 平行支援 OPS-REBASE-AUTO-001，先整理 review packet、evidence summary 與 reviewer handoff，不改 canonical truth。 |
| `ASK-006-SIDECAR-REVIEW` | Sprint 7 / EPIC-CONSULT-ADVANCED | [Sidecar] [Auto] [Parent ASK-006] Prepare ASK-006 review packet and evidence summary | Claude | todo | `ASK-001`, `ASK-002`, `ASK-003`, `ASK-004`, `ASK-005` | 平行支援 ASK-006，先整理 review packet、evidence summary 與 reviewer handoff，不改 canonical truth。 |
| `ASK-007-SIDECAR-REVIEW` | Sprint 7 / EPIC-CONSULT-ADVANCED | [Sidecar] [Auto] [Parent ASK-007] Prepare ASK-007 review packet and evidence summary | Claude | todo | `ASK-004` | 平行支援 ASK-007，先整理 review packet、evidence summary 與 reviewer handoff，不改 canonical truth。 |

### External / Upstream Integration Work

| ID | Phase | Task | Owner | Status | Depends On | 中文說明 |
|---|---|---|---|---|---|---|
| `OSS-QLIB-002` | Sprint 7 / EPIC-OSS-RESEARCH | Qlib rolling-window OOS pipeline + eval | Codex | review_approved | - | 在 services/research/qlib/ 之上實作 rolling-window / OOS pipeline。建在已 archived 的 QLIB-001 dataset manifest 基礎上，產出 OOS metrics 並寫回 ExperimentRun。 |
| `OSS-STAT-001` | Sprint 7 / EPIC-OSS-RESEARCH | statsmodels cointegration adapter skeleton | Claude | todo | - | 新增 services/research/statsmodels/ adapter，落實 stat-arb 風格 cointegration / Engle-Granger 檢定，產生 signal_snapshot artifact。獨立於其他 research adapter，無共用檔案。 |
| `OSS-RLLIB-001` | Sprint 7 / EPIC-OSS-RESEARCH | RLlib PPO adapter skeleton | Claude | todo | - | 新增 services/research/rllib/ adapter skeleton，落實 Ray RLlib PPO 訓練 mini-loop，輸出 model_artifact。CPU-only smoke (no GPU)，獨立於其他 research adapter。 |
| `OSS-FINRL-001` | Sprint 7 / EPIC-OSS-RESEARCH | FinRL DQN/PPO adapter skeleton | Gemini2 | in_progress | - | 新增 services/research/finrl/ adapter skeleton，落實 FinRL DQN/PPO 在歷史 OHLCV 上 mini-training，輸出 model_artifact。CPU-only smoke。獨立於其他 research adapter。 |
| `OSS-STAT-001-SIDECAR-ACCEPTANCE` | Sprint 7 / EPIC-OSS-RESEARCH | [Sidecar] [Auto] [Parent OSS-STAT-001] Prepare OSS-STAT-001 acceptance packet and dependency map | Gemini | review | - | 平行支援 OSS-STAT-001，先整理 acceptance checklist、dependency map 與 support packet，不改 canonical truth。 |

## Recently Executed Tasks

- Archive updated: 2026-05-17 09:01:47
- Terminal tasks archived: `1165` total, `1145` completed, `20` superseded

| ID | Phase | Task | Owner | Outcome | Archived At | Snapshot |
|---|---|---|---|---|---|---|
| `TRN-007` | Sprint 7 / EPIC-TRAINER-ADVANCED | Trainer trace -> imitation dataset export | Codex | completed | 2026-05-17 09:01:47 | `ai-task-archive/tasks/TRN-007.json` |
| `OPS-REBASE-AUTO-001` | Sprint 7 / EPIC-OPS-BACKLOG | Auto-handle empty commits in worker rebase flow | Codex | completed | 2026-05-17 08:43:51 | `ai-task-archive/tasks/OPS-REBASE-AUTO-001.json` |
| `OPS-WORKER-PUSH-CRED-001` | Sprint 7 / EPIC-OPS-BACKLOG | Background worker git push credentials provisioning | Codex | completed | 2026-05-17 08:34:45 | `ai-task-archive/tasks/OPS-WORKER-PUSH-CRED-001.json` |
| `IMT-005` | Sprint 7 / EPIC-IMITATION-TRAINING | BC baseline trainer on imitation dataset | Codex | completed | 2026-05-17 08:31:05 | `ai-task-archive/tasks/IMT-005.json` |
| `OSS-QUANTLIB-001` | Sprint 7 / EPIC-OSS-RESEARCH | QuantLib option pricing adapter skeleton | Codex | completed | 2026-05-17 08:29:57 | `ai-task-archive/tasks/OSS-QUANTLIB-001.json` |
| `LEAN-ALGO-001` | Sprint 7 / EPIC-LEAN-RUNTIME | LEAN algorithm-level smoke via artifact loader | Codex | completed | 2026-05-17 08:17:26 | `ai-task-archive/tasks/LEAN-ALGO-001.json` |
| `TRN-005` | Sprint 7 / EPIC-TRAINER-ADVANCED | Trainer commit -> persona policy lineage edge | Codex | completed | 2026-05-17 07:50:17 | `ai-task-archive/tasks/TRN-005.json` |
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

## Task Board

| ID | Phase | Task | 中文說明 | Owner | Reviewer | Status | Depends On | Last Update | Next |
|---|---|---|---|---|---|---|---|---|---|
| `MGMT-BROKER-002` | Track E / EPIC-05 Shioaji Sandbox | Shioaji account readiness check | - | Gemini2 | Gemini | blocked | - | 2026-05-15 23:15:06 | Waiting for broker credentials (API_KEY/SECRET_KEY) to proceed with account readiness check. |
| `OSS-QLIB-002` | Sprint 7 / EPIC-OSS-RESEARCH | Qlib rolling-window OOS pipeline + eval | 在 services/research/qlib/ 之上實作 rolling-window / OOS pipeline。建在已 archived 的 QLIB-001 dataset manifest 基礎上，產出 OOS metrics 並寫回 ExperimentRun。 | Codex | Claude | review_approved | - | 2026-05-17 09:36:04 | Review approved: all acceptance criteria met. run() produces schema-valid ExperimentRun with producer_run_id/lineage/evaluation_summary; evaluate() returns sharpe/sortino/max_dd/ic; 2 tests pass (happy-path + fail-fast); 35 total qlib tests pass; no trailing whitespace; deployment_stage=none; no side effects. Returning to Codex for finalization. |
| `OSS-STAT-001` | Sprint 7 / EPIC-OSS-RESEARCH | statsmodels cointegration adapter skeleton | 新增 services/research/statsmodels/ adapter，落實 stat-arb 風格 cointegration / Engle-Granger 檢定，產生 signal_snapshot artifact。獨立於其他 research adapter，無共用檔案。 | Claude | Codex | todo | - | 2026-05-17 08:32:34 | Supervisor preempted OSS-STAT-001 to free Claude for higher-priority review/finalize work; task returned to todo until a fresh run restarts it. |
| `OSS-RLLIB-001` | Sprint 7 / EPIC-OSS-RESEARCH | RLlib PPO adapter skeleton | 新增 services/research/rllib/ adapter skeleton，落實 Ray RLlib PPO 訓練 mini-loop，輸出 model_artifact。CPU-only smoke (no GPU)，獨立於其他 research adapter。 | Claude | Codex | todo | - | 2026-05-17 07:27:46 | Auto-reassigned ownership from Gemini to Claude after repeated Gemini capacity/429: Capacity / rate limit failure. Task returned to todo until Claude starts a fresh run. |
| `OSS-FINRL-001` | Sprint 7 / EPIC-OSS-RESEARCH | FinRL DQN/PPO adapter skeleton | 新增 services/research/finrl/ adapter skeleton，落實 FinRL DQN/PPO 在歷史 OHLCV 上 mini-training，輸出 model_artifact。CPU-only smoke。獨立於其他 research adapter。 | Gemini2 | Codex | in_progress | - | 2026-05-17 09:34:41 | Supervisor re-dispatched OSS-FINRL-001; task remains in progress. |
| `IMT-006` | Sprint 7 / EPIC-IMITATION-TRAINING | Imitation evaluation metrics: action-match + return-gap + KL | 新增 imitation evaluation metrics module：action-match accuracy, return-gap vs expert baseline, KL divergence。獨立於 bc_trainer.py。 | Codex | Codex2 | review | `IMT-001`, `IMT-004` | 2026-05-17 07:31:55 | Implemented services/research/imitation/eval_metrics.py and test_eval_metrics.py. evaluate() returns JSON-serializable evaluation_result payload with action_match_rate, return_gap, kl_divergence for behavior_policy refs; supports deterministic, stochastic/uniform, keyed predictions, nearest-centroid policies, and counterfactual rewards. Verification: pytest -q services/research/imitation/test_eval_metrics.py; pytest -q services/research/imitation |
| `IMT-007` | Sprint 7 / EPIC-IMITATION-TRAINING | Behavior-policy artifact validation gate | behavior_policy artifact 進入 registry / governance 前的驗證閘門：metadata 完整、checksum 一致、IMT-006 eval metrics 達門檻、policy 不出現 deploy/canary/live 觸發詞。獨立 module。 | Claude | Codex2 | todo | `IMT-004`, `IMT-006` | 2026-05-17 07:22:33 | Assignment created |
| `IMT-008` | Sprint 7 / EPIC-IMITATION-TRAINING | TRL preference-pair dataset bridge | 新增 TRL (Transformer Reinforcement Learning) preference-pair dataset bridge：把 IMT-002 PreferenceExample / CorrectionTrace 轉成 TRL 期望的 chosen/rejected 對。獨立 module。 | Claude | Codex | todo | `IMT-002`, `IMT-003` | 2026-05-17 07:48:59 | Auto-reassigned ownership from Copilot to Claude after repeated Copilot quota terminal: 402 You have no quota. Task returned to todo until Claude starts a fresh run. |
| `TRN-006` | Sprint 7 / EPIC-TRAINER-ADVANCED | Rapid-eval -> vectorbt backend integration | 把 TRN-003 rapid-eval skeleton 接到實際 backend（vectorbt VBT-001 via adapter facade）。獨立檔案，不修 TRN-003 既有 endpoint signature。 | Codex | Claude2 | review_approved | `TRN-003`, `VBT-001` | 2026-05-17 09:36:13 | Review approved: rapid_eval_integration.py meets all acceptance criteria — real vectorbt backend, correct eval_summary shape, 2 isolated tests pass, combined 35+5 subtests pass, diff-check clean. Returning to owner Codex for finalization. |
| `PER-003` | Sprint 7 / EPIC-TRAINER-ADVANCED | Persona registry live integration acceptance | 把 execute-plans Persona 頁面從 fixture-backed 切換到 live persona_registry service。確認 /bff/personas 與 /bff/personas/{id} read path 走 services/control-plane/persona/persona_registry.py。獨立 acceptance。 | Claude2 | Codex | review_approved | `PER-001`, `PER-002` | 2026-05-17 09:28:33 | Supervisor paused finalize on PER-003 to free Claude2 for higher-priority review work; task remains review_approved. |
| `ASK-006` | Sprint 7 / EPIC-CONSULT-ADVANCED | Consult -> Committee -> Memo -> Review e2e test | ASK-001..005 已落地 consult/committee flow，這個 task 寫一條 e2e integration test：ask session create -> committee invoke -> memo publish -> Management review queue 接到 handoff。獨立 test 檔。 | Codex | Claude2 | review | `ASK-001`, `ASK-002`, `ASK-003`, `ASK-004`, `ASK-005` | 2026-05-17 09:27:31 | Chair reassigned review from Codex2 to Claude2: Codex2 pause blocks this review and ASK-006 is on the current consultation path. |
| `ASK-007` | Sprint 7 / EPIC-CONSULT-ADVANCED | Consult memo evidence redaction regression | 驗證 consult memo publish 流程的 evidence redaction：persona-internal 機密欄位（policy_internals memory_trace internal_score）不能洩漏到 review-facing memo。獨立 test 檔。 | Codex | Claude2 | review | `ASK-004` | 2026-05-17 09:27:18 | Chair reassigned review from Codex2 to Claude2: Codex2 pause blocks this fresh review; Claude2 can review Codex-owned BFF/consultation work after closeout. |
| `ASK-008` | Sprint 7 / EPIC-CONSULT-ADVANCED | Committee sponsor decision -> governance action bridge | committee 結出 sponsor decision 後，提供把 sponsor decision bridge 到 governance action (例如觸發 ApprovalDecision proposal 或 EvolutionDecision proposal) 的 module。獨立 module，不直接改 governance service。 | Claude | Codex2 | todo | `ASK-003`, `GOV-001`, `EVO-001` | 2026-05-17 07:25:38 | Assignment created |
| `OPS-REFACTOR-001` | Sprint 7 / EPIC-OPS-BACKLOG | Re-apply dispatch policy refactor on current master | 把 archive/codex-orchestrator-dispatch-policy-cleanup-2026-04-28 tag 內的 dispatch_policy 抽取重新套用到當前 supervisor.py。原 cherry-pick 因 supervisor.py 1776 commit drift 衝突；本任務以 current master 為基準重做。獨立新增 .orchestrator/dispatch_policy.py + test。 | Claude | Claude2 | todo | - | 2026-05-17 07:27:36 | Assignment created |
| `OPS-SIDECAR-CLEANUP-001` | Sprint 7 / EPIC-OPS-BACKLOG | Sidecar packet retention and cleanup policy | support/sidecars/ 持續累積 packets 但缺退場機制。設計 retention/cleanup module：parent task done 後 N 天，sidecar packet 移至 support/sidecars/archived/，超過 M 天直接刪。獨立 module，可由 cron / chair-review 觸發。 | Codex | Codex2 | review | - | 2026-05-17 07:46:46 | Ready for review. Added .orchestrator/sidecar_cleanup.py with scan/classify/execute retention API, CLI dry-run/apply mode, 14-day archive and 60-day delete policy; added contract doc and focused tests covering fresh, archivable, delete-eligible, dry-run no-op, real execute, and CLI exit 0. Verification: python3 -m pytest .orchestrator/test_sidecar_cleanup.py; python3 .orchestrator/test_sidecar_cleanup.py; python3 -m py_compile .orchestrator/sidecar_cleanup.py .orchestrator/test_sidecar_cleanup.py. |
| `OPS-REBASE-AUTO-001-SIDECAR-REVIEW` | Sprint 7 / EPIC-OPS-BACKLOG | [Sidecar] [Auto] [Parent OPS-REBASE-AUTO-001] Prepare OPS-REBASE-AUTO-001 review packet and evidence summary | 平行支援 OPS-REBASE-AUTO-001，先整理 review packet、evidence summary 與 reviewer handoff，不改 canonical truth。 | Claude | Codex | todo | - | 2026-05-17 08:34:36 | Auto-reassigned ownership from Gemini to Claude after repeated Gemini capacity/429: Capacity / rate limit failure. Task returned to todo until Claude starts a fresh run. |
| `OSS-STAT-001-SIDECAR-ACCEPTANCE` | Sprint 7 / EPIC-OSS-RESEARCH | [Sidecar] [Auto] [Parent OSS-STAT-001] Prepare OSS-STAT-001 acceptance packet and dependency map | 平行支援 OSS-STAT-001，先整理 acceptance checklist、dependency map 與 support packet，不改 canonical truth。 | Gemini | Claude | review | - | 2026-05-17 09:05:00 | Acceptance packet drafted and ready for review in support/sidecars/OSS-STAT-001/OSS-STAT-001-SIDECAR-ACCEPTANCE.md. The packet documents the resolved shadowing issue and the final artifact shapes. |
| `ASK-006-SIDECAR-REVIEW` | Sprint 7 / EPIC-CONSULT-ADVANCED | [Sidecar] [Auto] [Parent ASK-006] Prepare ASK-006 review packet and evidence summary | 平行支援 ASK-006，先整理 review packet、evidence summary 與 reviewer handoff，不改 canonical truth。 | Claude | Codex | todo | `ASK-001`, `ASK-002`, `ASK-003`, `ASK-004`, `ASK-005` | 2026-05-17 08:58:31 | Auto-reassigned ownership from Copilot to Claude after repeated Copilot quota terminal: 402 You have no quota. Task returned to todo until Claude starts a fresh run. |
| `ASK-007-SIDECAR-REVIEW` | Sprint 7 / EPIC-CONSULT-ADVANCED | [Sidecar] [Auto] [Parent ASK-007] Prepare ASK-007 review packet and evidence summary | 平行支援 ASK-007，先整理 review packet、evidence summary 與 reviewer handoff，不改 canonical truth。 | Claude | Codex | todo | `ASK-004` | 2026-05-17 09:28:48 | Auto-reassigned ownership from Copilot to Claude after repeated Copilot quota terminal: 402 You have no quota. Task returned to todo until Claude starts a fresh run. |

## Handoff Queue

| Task | From | To | Message | Status | Created At |
|---|---|---|---|---|---|
| `IMT-006` | Codex | Codex2 | Implemented services/research/imitation/eval_metrics.py and test_eval_metrics.py. evaluate() returns JSON-serializable evaluation_result payload with action_match_rate, return_gap, kl_divergence for behavior_policy refs; supports deterministic, stochastic/uniform, keyed predictions, nearest-centroid policies, and counterfactual rewards. Verification: pytest -q services/research/imitation/test_eval_metrics.py; pytest -q services/research/imitation | pending | 2026-05-17 07:31:55 |
| `OPS-SIDECAR-CLEANUP-001` | Codex | Codex2 | Ready for review. Added .orchestrator/sidecar_cleanup.py with scan/classify/execute retention API, CLI dry-run/apply mode, 14-day archive and 60-day delete policy; added contract doc and focused tests covering fresh, archivable, delete-eligible, dry-run no-op, real execute, and CLI exit 0. Verification: python3 -m pytest .orchestrator/test_sidecar_cleanup.py; python3 .orchestrator/test_sidecar_cleanup.py; python3 -m py_compile .orchestrator/sidecar_cleanup.py .orchestrator/test_sidecar_cleanup.py. | pending | 2026-05-17 07:46:46 |
| `PER-003` | Codex | Claude2 | Review approved by Codex. Acceptance verifies service_store persona list/detail paths, pagination, strict no-fixture fallback, and PersonaRegistry seed -> BFF read smoke. Owner Claude2 should finalize to done per closeout checklist. | pending | 2026-05-17 08:15:12 |
| `OSS-STAT-001-SIDECAR-ACCEPTANCE` | Gemini | Claude | Acceptance packet drafted and ready for review in support/sidecars/OSS-STAT-001/OSS-STAT-001-SIDECAR-ACCEPTANCE.md. The packet documents the resolved shadowing issue and the final artifact shapes. | pending | 2026-05-17 09:05:00 |
| `ASK-007` | Codex2 | Claude2 | Chair reassigned review from Codex2 to Claude2: Codex2 pause blocks this fresh review; Claude2 can review Codex-owned BFF/consultation work after closeout. | pending | 2026-05-17 09:27:18 |
| `ASK-006` | Codex2 | Claude2 | Chair reassigned review from Codex2 to Claude2: Codex2 pause blocks this review and ASK-006 is on the current consultation path. | pending | 2026-05-17 09:27:31 |
| `OSS-QLIB-002` | Claude | Codex | Review approved: all acceptance criteria met. run() produces schema-valid ExperimentRun with producer_run_id/lineage/evaluation_summary; evaluate() returns sharpe/sortino/max_dd/ic; 2 tests pass (happy-path + fail-fast); 35 total qlib tests pass; no trailing whitespace; deployment_stage=none; no side effects. Returning to Codex for finalization. | pending | 2026-05-17 09:36:04 |
| `TRN-006` | Claude2 | Codex | Review approved: rapid_eval_integration.py meets all acceptance criteria — real vectorbt backend, correct eval_summary shape, 2 isolated tests pass, combined 35+5 subtests pass, diff-check clean. Returning to owner Codex for finalization. | pending | 2026-05-17 09:36:13 |

## Blockers

| Task | Owner | Waiting For | Message | Status |
|---|---|---|---|---|
| `MGMT-BROKER-002` | Gemini2 | Gemini | Waiting for broker credentials (API_KEY/SECRET_KEY) to proceed with account readiness check. | open |

## Review Notes

| Task | Reviewer | 修正重點 | Review File |
|---|---|---|---|
| `OSS-QLIB-002` | Claude | 驗證通過。run() 回傳 schema-valid ExperimentRun，metadata 含 producer_run_id / lineage / evaluation_summary / safety_assertions。<br>evaluate() 回傳 sharpe / sortino / max_dd / ic，IC 使用觀測層 prediction vs actual_return，sharpe/sortino/max_dd 使用截面平均報酬。<br>2 個測試 (happy-path + insufficient-data fail-fast) pytest -q exit 0；全 qlib suite 35 passed。<br>diff --check 無 trailing whitespace。deployment_stage=none 有斷言，無 registry/broker/order side effect。 | - |
| `TRN-006` | Claude2 | TRN-006 review passed. run_rapid_eval 正確暴露 sharpe/sortino/max_dd eval_summary；導入路徑 services.research.vectorbt.adapter 為真實 adapter（非 mock）；2 tests pass (success path + fail-fast)；35+5 subtests combined pass；diff-check clean；TRN-003 endpoint 未被修改；governance advisory_only 欄位確認無直接 live 影響。 | - |
| `PER-003` | Codex | No blocking findings. /bff/personas list and detail routes call read_store.list_personas/get_persona, and ReadSurfaceStore resolves personas through ServiceBackedReadAdapter before local fallback.<br>Verified task artifacts: PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_persona_live_integration.py -q => 5 passed; PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=services/control-plane/bff:. python3 services/control-plane/persona/integration_smoke.py => 16/16 checks passed.<br>PER-003 artifacts are committed in 549270e3 and currently clean; unrelated dirty worktree files remain outside this review. | - |

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

- 2026-05-17 09:35:41 Orchestrator: PreToolUse: Bash
- 2026-05-17 09:35:41 Orchestrator: PostToolUse: Bash
- 2026-05-17 09:35:44 Orchestrator: PostToolUse: Bash
- 2026-05-17 09:35:44 Orchestrator: PreToolUse: Bash
- 2026-05-17 09:35:44 Orchestrator: PostToolUse: Bash
- 2026-05-17 09:35:49 Orchestrator: PreToolUse: Bash
- 2026-05-17 09:35:49 Orchestrator: PostToolUse: Bash
- 2026-05-17 09:35:51 Orchestrator: PreToolUse: Bash
- 2026-05-17 09:35:51 Orchestrator: PostToolUse: Bash
- 2026-05-17 09:36:03 Orchestrator: PreToolUse: Bash
- 2026-05-17 09:36:04 Claude: `OSS-QLIB-002` Review approved: all acceptance criteria met. run() produces schema-valid ExperimentRun with producer_run_id/lineage/evaluation_summary; evaluate() returns sharpe/sortino/max_dd/ic; 2 tests pass (happy-path + fail-fast); 35 total qlib tests pass; no trailing whitespace; deployment_stage=none; no side effects. Returning to Codex for finalization.
- 2026-05-17 09:36:12 Orchestrator: PreToolUse: Bash
- 2026-05-17 09:36:13 Claude2: `TRN-006` Review approved: rapid_eval_integration.py meets all acceptance criteria — real vectorbt backend, correct eval_summary shape, 2 isolated tests pass, combined 35+5 subtests pass, diff-check clean. Returning to owner Codex for finalization.
- 2026-05-17 09:36:25 Orchestrator: PostToolUse: Bash
- 2026-05-17 09:36:32 Orchestrator: PreToolUse: Bash
- 2026-05-17 09:36:32 Orchestrator: PostToolUse: Bash
- 2026-05-17 09:36:32 Orchestrator: PostToolUse: Bash
- 2026-05-17 09:36:36 Orchestrator: Stop: Stop
- 2026-05-17 09:36:37 Orchestrator: PreToolUse: Bash
- 2026-05-17 09:36:37 Orchestrator: SessionEnd: SessionEnd
