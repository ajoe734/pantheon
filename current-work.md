# Current Work

This file is generated from `ai-status.json` and `ai-activity-log.jsonl`.
Do not treat this file as the machine-readable source of truth.
Absolute times below use 台灣時間 (UTC+8).

Last updated: 2026-05-18 03:51:14

## Objective

跨進開發團隊 GAP master rebaseline (docs/04/pantheon_sa_supplemental_2026-05-15/GAP_dev_team_master_rebaseline_2026-05-15.md)，以 pantheon@master + execute-plans@main 為基準。並行 6 條 EPIC，按 P0→P3 階梯推進：(I) EPIC-BFF-P0 (P0 10 task / Sprint 1) — session trio (/bff/me, auth/refresh, logout) + /openapi.json + canonical action endpoint + approval decide + registry reads (strategies/personas/capital-pools/audit)，讓 execute-plans@main 在 VITE_BFF_FALLBACK=strict 下可 bootstrap 核心 Management flow 不再 fallback mock；(II) EPIC-GOV-DEPLOY (P1 5 task / Sprint 2) — ApprovalDecision first-class + DeploymentPlan contract/service + stage planner + deployment projection + pool/runtime compatibility 檢查；(III) EPIC-RUNTIME (P1 6 task / Sprint 3) — RuntimeBinding schema + Runtime Manager skeleton + /bff/runtimes + deploy/pause/replace/rollback actions + loader metadata migration (promotion_state → artifact_state + deployment_stage) + LEAN algorithm-level smoke；(IV) EPIC-TELEMETRY (P2 7 task / Sprint 4) — TelemetryEvent canonical schema + RuntimeHeartbeat ingest + AuditAction backend + /bff/alerts + /bff/incidents + reconciliation record + Postmortem schema/endpoint；(V) EPIC-RESEARCH (P3 28 task / Sprint 5) — Source Ingest (SRC) + StrategySpec (STRAT) + Experiment orchestrator (EXP) + Qlib/vectorbt adapters + Persona/Trainer (PER/TRN) + Imitation dataset (IMT) + Consult/Committee (ASK)；(VI) EPIC-EVOLUTION (P3 3 task / Sprint 6) — EvolutionDecision service + /bff/v5/loop-runs + /bff/v5/sentinel/findings。GAP § 10 最大阻塞：BFF live endpoints 不足 → EPIC-BFF-P0 必須最先收斂；Registry/Promotion canonical 已 implemented，DeploymentPlan/RuntimeBinding 是 governance→execution 缺口；Artifact Loader 仍寫 legacy promotion_state，EX-002 metadata migration 是 execution-side 技術債。fail-closed 鐵律延續：broker production live、capital binding live 仍禁止；canary 需 risk-owner + operator 雙閘；evidence 走 support/evidence/<epic>-<task>/。Track E 收尾備註：46 個 MGMT-* task 中 45 個 done+archive，僅 MGMT-BROKER-002 仍 blocked 等 Shioaji credentials (commit 22e5ca3b 已備 sidecar acceptance packet)；M7 canary readiness 因此未閉合；Track E objective 不在本 sprint 推進範圍，僅 carry-over 記錄。

## Current Sprint

- Sprint: `2026-05-16-pantheon-bff-p0-foundation`
- Canonical files: `AI_COLLABORATION_GUIDE.md`, `ai-status.json`, `ai-activity-log.jsonl`, `current-work.md`, `TARGET_ARCHITECTURE.md`, `OPENCLAW_RUNTIME_CONTRACT.md`, `PERSONA_RUNTIME_MODEL.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `PAPER_CANARY_LIVE_POLICY.md`, `ROLLBACK_AND_POSITION_SEMANTICS.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`, `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`, `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md`, `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md`, `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`, `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`, `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`, `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`, `CANONICAL_DOCUMENT_MAP.md`, `DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md`, `ROADMAP.md`, `DEVELOPMENT_WORKBREAKDOWN.md`, `OSS_INTEGRATION_CHECKLIST.md`, `WORKBENCH_DELIVERY_BACKLOG.md`, `DELIVERY_CLOSURE_AND_LOOP_STATES.md`, `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`, `CANONICAL_CONTRACT_MIGRATION_DECISION.md`, `WORK_REBASELINE.md`, `Pantheon_總索引版系統分析文件.md`, `Pantheon_資料表_Schema_設計版.md`, `Pantheon_API_Service_Contract_設計版.md`
- Canonical tiers: `L0 Collaboration & State`, `L0.5 Derived Narrative`, `L1 Platform Architecture & Policy`, `L2 Planning & Execution`, `L3 Supporting Design & Migration`
- Canonical map: `CANONICAL_DOCUMENT_MAP.md`
- Document boundary: `DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md`
- Full backlog: `DEVELOPMENT_WORKBREAKDOWN.md`
- Workbench backlog: `WORKBENCH_DELIVERY_BACKLOG.md`
- Loop closure: `DELIVERY_CLOSURE_AND_LOOP_STATES.md`
- Execution proof: `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`
- Dashboard: `docs-site/index.html`

## Active Slices

- `Claude`: execution, control-plane, governance-review; next: Assignment created
- `Gemini`: gcp, ci-cd, runtime-packaging, worker-ops; next: Assignment created
- `Codex`: integration, status-system, schema, acceptance; next: Assignment created
- `Codex2`: integration, status-system, schema, acceptance; next: Assignment created
- `Copilot`: research-ingest, external-search, spec-review, critique; next: Assignment created
- `Claude2`: execution, control-plane, governance-review; next: Review approved by Codex. Focused verification passed: PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/e2e/test_deployment_plan_to_paper_run.py services/execution/lean_runtime/test_algorithm_smoke.py -q -x => 8 passed in 2.87s. See support/reviews/OODA-E2E-005-review-codex.md; owner Claude2 should finalize and mark done.
- `Gemini2`: gcp, ci-cd, runtime-packaging, worker-ops; next: Assignment created

## Delivery Layers

### Primary Project Work

| ID | Phase | Task | Owner | Status | Depends On | 中文說明 |
|---|---|---|---|---|---|---|
| `DEP-004` | Sprint 7 / EPIC-GOV-DEPLOY | Pool x runtime compatibility check before deployment advance | Codex | todo | `DEP-001`, `DEP-002`, `CAP-001`, `RT-001` | GAP P1 列為 DEP-004 但 sprint 7 沒派；grep 確認 services 與 governance 樹下沒有 pool/runtime compat check 實作。本任務在 DeploymentPlan 進入 RuntimeBinding 前增加 capital_pool 能力 × runtime 要求的相容性檢查，不通過則阻擋 advance。獨立 module，不修 DEP-001..003 公開 API。 |
| `M7-CANARY-CLOSEOUT` | Track E / EPIC-05 M7 Canary Readiness | M7 canary readiness packet final closure | Claude | todo | `MGMT-BROKER-002`, `MGMT-BROKER-006` | Track E EPIC-05 全部子任務已完成；MGMT-BROKER-002 Shioaji simulation SDK smoke 也通過。本任務組裝完整 M7 PromotionReadinessPacket：含 broker_sandbox_smoke / shioaji_sandbox_evidence_packet / canary_activation_gate_refs 三項證據引用，加上 risk-owner + operator 雙閘 approval 預留欄位（未實際開啟 live），最終產出 packet JSON 與簽核表。獨立檔案，不修 broker live flag。 |
| `POST-EVO-BRIDGE` | Sprint 7 / EPIC-EVOLUTION-FOLLOWUP | Postmortem -> EvolutionDecisionProposal auto-trigger bridge | Claude2 | todo | `POST-001`, `EVO-001` | POST-001 + EVO-001 已落地為 schema/service，但 incident/postmortem publish → EvolutionDecisionProposal 自動觸發的 bridge 還沒實際 wire。本任務新增 postmortem_bridge module：訂閱 postmortem published 事件，按 severity 與 corrective_action_required 判斷是否產出 EvolutionDecisionProposal payload（不直接寫 governance store，僅 emit proposal）。獨立 module，不改 POST-001 / EVO-001 公開 API。 |
| `LOVABLE-STRICT-PUBLISH` | Sprint 7 / EPIC-LOVABLE-INFRA | Lovable build-time strict env publish audit script | Gemini | todo | - | SA § 2.2 列為 non-blocking follow-up：execute-plans@main build-time 應使用 strict env (VITE_BFF_MODE=live VITE_BFF_FALLBACK=strict VITE_BFF_REAL_WRITES=false) 重新發佈一次，並驗證發佈後的 bundle 不再含 seed fallback assets。本任務不直接動 execute-plans repo，而是寫一個 pantheon 端的 audit script + evidence packet，記錄 publish 條件、build env、bundle hash、verification probe 結果。 |
| `OODA-E2E-001` | Sprint 8 / EPIC-OODA-E2E | OODA E2E #1: source → StrategySpec transition test | Codex | todo | `SRC-001`, `STRAT-001`, `STRAT-003` | OODA Observe 階段第一步：實作整合測試證明「真實 SourceRecord → StrategySpec」這個 transition 可端到端走完。使用 SRC-* 與 STRAT-* 既有 service code，不重做。獨立 test 檔。 |
| `OODA-E2E-003` | Sprint 8 / EPIC-OODA-E2E | OODA E2E #3: ExperimentRun → CandidateArtifact admission test | Claude | todo | `EXP-005`, `REG-002` | OODA Orient→Decide 階段：證明「ExperimentRun → CandidateArtifact → Registry admission」transition 可端到端走完。使用 EXP-005 writeback + Registry promotion service。獨立 test 檔。 |
| `OODA-E2E-004` | Sprint 8 / EPIC-OODA-E2E | OODA E2E #4: Admission → ApprovalDecision → DeploymentPlan(paper) test | Claude | todo | `GOV-001`, `DEP-001`, `DEP-002`, `DEP-004` | OODA Decide 階段：證明「CandidateArtifact → ApprovalDecision → DeploymentPlan(paper)」transition 可端到端走完。使用 GOV-001 ApprovalDecision + DEP-001 DeploymentPlan service。獨立 test 檔。 |
| `OODA-E2E-005` | Sprint 8 / EPIC-OODA-E2E | OODA E2E #5: DeploymentPlan(paper) → RuntimeBinding → paper run test | Claude2 | review_approved | `DEP-001`, `RT-001`, `RT-002`, `EX-002-RB`, `LEAN-ALGO-001` | OODA Act 階段：證明「DeploymentPlan(paper) → RuntimeBinding → ArtifactLoader → paper algorithm」transition 可端到端走完。使用 RT-001..002 + EX-002-RB loader + LEAN-ALGO-001 algorithm smoke。獨立 test 檔，5 trading days deterministic 數據，無 broker。 |
| `OODA-E2E-006` | Sprint 8 / EPIC-OODA-E2E | OODA E2E #6: telemetry → Incident → Postmortem → EvolutionDecisionProposal test | Claude | todo | `TEL-001`, `INC-001-RB`, `POST-001`, `POST-EVO-BRIDGE` | OODA Learn 階段：證明「paper run telemetry → IncidentCase → Postmortem → EvolutionDecisionProposal」transition 可端到端走完。注入 1 條合成 incident-trigger telemetry，跑 POST-EVO-BRIDGE。獨立 test 檔，無 live mutation。 |
| `OODA-E2E-007` | Sprint 8 / EPIC-OODA-E2E | OODA E2E #7: full OodaLoopPacket closure + evidence chain | Codex | todo | `OODA-E2E-001`, `OODA-E2E-002`, `OODA-E2E-003`, `OODA-E2E-004`, `OODA-E2E-005`, `OODA-E2E-006` | OODA 全環收尾：把上述 6 個 transition test 串成單一 OodaLoopPacket 並驗證所有欄位齊全（observe/orient/decide/act/learn refs）。產出 evidence packet 與 closeout summary。獨立 test 檔。 |
| `STRAT-V2-001` | Sprint 8 / EPIC-STRAT-EXP-DEEP | Strategy spec distillation production smoke (real research note) | Copilot | todo | `STRAT-003`, `STRAT-004`, `SRC-001` | 把 STRAT-003 source converter 升級到 production：吃真實 internal research note (docs/research/notes/*.md 或 fixture)，產出可進 registry 的 StrategySpec，含 evidence_refs + code_refs 完整 binding。獨立 module，不改 STRAT-001..004 公開 API。 |
| `STRAT-V2-002` | Sprint 8 / EPIC-STRAT-EXP-DEEP | Strategy lineage tree backend read API | Claude2 | todo | `LIN-001`, `STRAT-001`, `EXP-001` | 新增 lineage backend API：給定 strategy_spec_id 回傳完整 lineage tree（source_record → strategy_spec → experiment_runs → candidate_artifacts → deployment_plans → runtime_bindings）。獨立 module，不改 LIN-001 既有 read-model。 |
| `EXP-V2-001` | Sprint 8 / EPIC-STRAT-EXP-DEEP | Experiment orchestrator parallel multi-backend dispatch | Codex | todo | `EXP-001`, `EXP-002`, `VBT-001`, `OSS-QLIB-002`, `OSS-STAT-001` | 升級 experiment orchestrator 支援平行多 backend：同一個 ExperimentTask 可以同時派給 vectorbt + Qlib + statsmodels 跑，回傳 N 個獨立 ExperimentRun 加上比較摘要。獨立 module，不改 EXP-001 公開 schema。 |
| `EXP-V2-002` | Sprint 8 / EPIC-STRAT-EXP-DEEP | ExperimentRun multi-artifact lineage tree | Codex2 | todo | `EXP-005`, `LIN-001` | 新增多 artifact-type lineage tree：ExperimentRun 可同時產 model_artifact + feature_set + signal_snapshot + optimizer_result，本任務確保 lineage edges 正確連接 N 個 artifact 而非單一。獨立 module。 |
| `SPRINT-8-CLOSEOUT` | Sprint 8 / EPIC-CLOSEOUT | Sprint 8 retrospective + closeout + Sprint 9 candidate topics | Claude | todo | `OSS-QLIB-V2-001`, `OSS-STAT-V2-001`, `OSS-QUANTLIB-V2-001`, `OSS-RLLIB-V2-001`, `OSS-FINRL-V2-001`, `OODA-E2E-007`, `STRAT-V2-001`, `STRAT-V2-002`, `EXP-V2-001`, `EXP-V2-002` | Sprint 8 收尾：彙整 16 條子任務 evidence、產 sprint retrospective + 統計報告（哪些 EPIC 過、哪些 EPIC 殘留缺口）、產 sprint 9 候選議題 raw list（供下一輪 planning 用）。獨立 evidence packet。 |

### External / Upstream Integration Work

| ID | Phase | Task | Owner | Status | Depends On | 中文說明 |
|---|---|---|---|---|---|---|
| `OSS-QLIB-V2-001` | Sprint 8 / EPIC-OSS-V2 | Qlib production-scale rolling + registry admission | Codex | todo | `OSS-QLIB-002`, `MGMT-QLIB-001` | 把 OSS-QLIB-002 的 rolling pipeline 升級到 production scale：使用 MGMT-QLIB-001 已建好的 TWSE OHLCV dataset（≥50 instruments × ≥2 years），跑完整 rolling-window 訓練，產 model_artifact 並提交 registry admission packet。獨立檔案路徑。 |
| `OSS-STAT-V2-001` | Sprint 8 / EPIC-OSS-V2 | statsmodels production cointegration on TWSE pairs | Copilot | todo | `OSS-STAT-001`, `MGMT-QLIB-001` | 把 OSS-STAT-001 cointegration adapter 升級到 production：對 10 個 TWSE 大型股配對跑 2-year rolling Engle-Granger 檢定，輸出 signal_snapshot artifact 含 top-N cointegrated pairs，提交 registry admission packet。獨立檔案。 |
| `OSS-QUANTLIB-V2-001` | Sprint 8 / EPIC-OSS-V2 | QuantLib production option chain pricer + greeks | Copilot | todo | `OSS-QUANTLIB-001` | 把 OSS-QUANTLIB-001 option pricer 升級為 production：對台指選擇權(TXO)鏈跨多檔履約價與多個到期日定價，輸出含 greeks 的 pricing_snapshot artifact，提交 registry admission packet。獨立檔案。 |
| `OSS-RLLIB-V2-001` | Sprint 8 / EPIC-OSS-V2 | RLlib production PPO on TWSE trading env | Claude | todo | `OSS-RLLIB-001`, `MGMT-QLIB-001` | 把 OSS-RLLIB-001 PPO skeleton 升級到 production：用 TWSE OHLCV 作為環境的 observation/action space，跑 ≥100 iter PPO，輸出 model_artifact 含 trained_policy 與 evaluation_summary，提交 registry admission packet。CPU-only。獨立檔案。 |
| `OSS-FINRL-V2-001` | Sprint 8 / EPIC-OSS-V2 | FinRL production DRL on TWSE stock env | Gemini2 | todo | `OSS-FINRL-001`, `MGMT-QLIB-001` | 把 OSS-FINRL-001 DRL skeleton 升級到 production：用 TWSE OHLCV 作為 FinRL StockTradingEnv，跑 ≥1000 steps DDPG 或 PPO，輸出 model_artifact 含 evaluation_summary（sharpe annual_return max_drawdown），提交 registry admission packet。CPU-only。 |
| `OODA-E2E-002` | Sprint 8 / EPIC-OODA-E2E | OODA E2E #2: StrategySpec → ExperimentRun transition test | Codex2 | todo | `STRAT-001`, `EXP-001`, `EXP-002`, `VBT-001` | OODA Observe→Orient 階段：證明「StrategySpec → ExperimentRun」transition 可端到端走完。使用 EXP-001..002 service + 一個 OSS adapter (vectorbt VBT-001) 跑 backtest。獨立 test 檔。 |

## Recently Executed Tasks

- Archive updated: 2026-05-17 13:51:55
- Terminal tasks archived: `1185` total, `1165` completed, `20` superseded

| ID | Phase | Task | Owner | Outcome | Archived At | Snapshot |
|---|---|---|---|---|---|---|
| `MGMT-BROKER-002` | Track E / EPIC-05 Shioaji Sandbox | Shioaji account readiness check | Gemini2 | completed | 2026-05-17 13:51:55 | `ai-task-archive/tasks/MGMT-BROKER-002.json` |
| `OSS-FINRL-001-SIDECAR-ACCEPTANCE` | Sprint 7 / EPIC-OSS-RESEARCH | Prepare OSS-FINRL-001 acceptance packet and dependency map | Codex | completed | 2026-05-17 11:44:36 | `ai-task-archive/tasks/OSS-FINRL-001-SIDECAR-ACCEPTANCE.json` |
| `OSS-FINRL-001` | Sprint 7 / EPIC-OSS-RESEARCH | FinRL DQN/PPO adapter skeleton | Codex | completed | 2026-05-17 11:35:33 | `ai-task-archive/tasks/OSS-FINRL-001.json` |
| `IMT-007` | Sprint 7 / EPIC-IMITATION-TRAINING | Behavior-policy artifact validation gate | Claude | completed | 2026-05-17 11:33:13 | `ai-task-archive/tasks/IMT-007.json` |
| `OPS-SIDECAR-CLEANUP-001` | Sprint 7 / EPIC-OPS-BACKLOG | Sidecar packet retention and cleanup policy | Codex | completed | 2026-05-17 11:14:11 | `ai-task-archive/tasks/OPS-SIDECAR-CLEANUP-001.json` |
| `IMT-006` | Sprint 7 / EPIC-IMITATION-TRAINING | Imitation evaluation metrics: action-match + return-gap + KL | Codex | completed | 2026-05-17 11:12:35 | `ai-task-archive/tasks/IMT-006.json` |
| `IMT-006-SIDECAR-REVIEW` | Sprint 7 / EPIC-IMITATION-TRAINING | Prepare IMT-006 review packet and evidence summary | Claude | completed | 2026-05-17 11:10:23 | `ai-task-archive/tasks/IMT-006-SIDECAR-REVIEW.json` |
| `ASK-007-SIDECAR-REVIEW` | Sprint 7 / EPIC-CONSULT-ADVANCED | Prepare ASK-007 review packet and evidence summary | Codex | completed | 2026-05-17 11:01:54 | `ai-task-archive/tasks/ASK-007-SIDECAR-REVIEW.json` |
| `OPS-REBASE-AUTO-001-SIDECAR-REVIEW` | Sprint 7 / EPIC-OPS-BACKLOG | Prepare OPS-REBASE-AUTO-001 review packet and evidence summary | Claude | completed | 2026-05-17 10:58:42 | `ai-task-archive/tasks/OPS-REBASE-AUTO-001-SIDECAR-REVIEW.json` |
| `ASK-006-SIDECAR-REVIEW` | Sprint 7 / EPIC-CONSULT-ADVANCED | Prepare ASK-006 review packet and evidence summary | Claude | completed | 2026-05-17 10:57:18 | `ai-task-archive/tasks/ASK-006-SIDECAR-REVIEW.json` |
| `OPS-REFACTOR-001` | Sprint 7 / EPIC-OPS-BACKLOG | Re-apply dispatch policy refactor on current master | Codex | completed | 2026-05-17 10:46:36 | `ai-task-archive/tasks/OPS-REFACTOR-001.json` |
| `ASK-008` | Sprint 7 / EPIC-CONSULT-ADVANCED | Committee sponsor decision -> governance action bridge | Codex | completed | 2026-05-17 10:41:04 | `ai-task-archive/tasks/ASK-008.json` |
| `IMT-008` | Sprint 7 / EPIC-IMITATION-TRAINING | TRL preference-pair dataset bridge | Codex | completed | 2026-05-17 10:24:08 | `ai-task-archive/tasks/IMT-008.json` |
| `OSS-RLLIB-001` | Sprint 7 / EPIC-OSS-RESEARCH | RLlib PPO adapter skeleton | Codex | completed | 2026-05-17 10:19:40 | `ai-task-archive/tasks/OSS-RLLIB-001.json` |
| `OSS-STAT-001` | Sprint 7 / EPIC-OSS-RESEARCH | statsmodels cointegration adapter skeleton | Codex | completed | 2026-05-17 10:17:33 | `ai-task-archive/tasks/OSS-STAT-001.json` |
| `ASK-006` | Sprint 7 / EPIC-CONSULT-ADVANCED | Consult -> Committee -> Memo -> Review e2e test | Codex | completed | 2026-05-17 09:53:14 | `ai-task-archive/tasks/ASK-006.json` |
| `ASK-007` | Sprint 7 / EPIC-CONSULT-ADVANCED | Consult memo evidence redaction regression | Codex | completed | 2026-05-17 09:52:54 | `ai-task-archive/tasks/ASK-007.json` |
| `TRN-006` | Sprint 7 / EPIC-TRAINER-ADVANCED | Rapid-eval -> vectorbt backend integration | Codex | completed | 2026-05-17 09:44:24 | `ai-task-archive/tasks/TRN-006.json` |
| `OSS-QLIB-002` | Sprint 7 / EPIC-OSS-RESEARCH | Qlib rolling-window OOS pipeline + eval | Codex | completed | 2026-05-17 09:42:51 | `ai-task-archive/tasks/OSS-QLIB-002.json` |
| `PER-003` | Sprint 7 / EPIC-TRAINER-ADVANCED | Persona registry live integration acceptance | Claude2 | completed | 2026-05-17 09:39:19 | `ai-task-archive/tasks/PER-003.json` |

## Task Board

| ID | Phase | Task | 中文說明 | Owner | Reviewer | Status | Depends On | Last Update | Next |
|---|---|---|---|---|---|---|---|---|---|
| `OSS-STAT-001-SIDECAR-ACCEPTANCE` | Sprint 7 / EPIC-OSS-RESEARCH | [Sidecar] [Auto] [Parent OSS-STAT-001] Prepare OSS-STAT-001 acceptance packet and dependency map | 平行支援 OSS-STAT-001，先整理 acceptance checklist、dependency map 與 support packet，不改 canonical truth。 | Gemini | Claude | done | - | 2026-05-17 11:45:00 | Owner finalized task and closed it. Sidecar acceptance packet is durable in support/sidecars/OSS-STAT-001/. |
| `DEP-004` | Sprint 7 / EPIC-GOV-DEPLOY | Pool x runtime compatibility check before deployment advance | GAP P1 列為 DEP-004 但 sprint 7 沒派；grep 確認 services 與 governance 樹下沒有 pool/runtime compat check 實作。本任務在 DeploymentPlan 進入 RuntimeBinding 前增加 capital_pool 能力 × runtime 要求的相容性檢查，不通過則阻擋 advance。獨立 module，不修 DEP-001..003 公開 API。 | Codex | Codex2 | todo | `DEP-001`, `DEP-002`, `CAP-001`, `RT-001` | 2026-05-17 18:43:57 | Assignment created |
| `M7-CANARY-CLOSEOUT` | Track E / EPIC-05 M7 Canary Readiness | M7 canary readiness packet final closure | Track E EPIC-05 全部子任務已完成；MGMT-BROKER-002 Shioaji simulation SDK smoke 也通過。本任務組裝完整 M7 PromotionReadinessPacket：含 broker_sandbox_smoke / shioaji_sandbox_evidence_packet / canary_activation_gate_refs 三項證據引用，加上 risk-owner + operator 雙閘 approval 預留欄位（未實際開啟 live），最終產出 packet JSON 與簽核表。獨立檔案，不修 broker live flag。 | Claude | Codex | todo | `MGMT-BROKER-002`, `MGMT-BROKER-006` | 2026-05-17 18:44:16 | Assignment created |
| `POST-EVO-BRIDGE` | Sprint 7 / EPIC-EVOLUTION-FOLLOWUP | Postmortem -> EvolutionDecisionProposal auto-trigger bridge | POST-001 + EVO-001 已落地為 schema/service，但 incident/postmortem publish → EvolutionDecisionProposal 自動觸發的 bridge 還沒實際 wire。本任務新增 postmortem_bridge module：訂閱 postmortem published 事件，按 severity 與 corrective_action_required 判斷是否產出 EvolutionDecisionProposal payload（不直接寫 governance store，僅 emit proposal）。獨立 module，不改 POST-001 / EVO-001 公開 API。 | Claude2 | Codex2 | todo | `POST-001`, `EVO-001` | 2026-05-17 18:44:31 | Assignment created |
| `LOVABLE-STRICT-PUBLISH` | Sprint 7 / EPIC-LOVABLE-INFRA | Lovable build-time strict env publish audit script | SA § 2.2 列為 non-blocking follow-up：execute-plans@main build-time 應使用 strict env (VITE_BFF_MODE=live VITE_BFF_FALLBACK=strict VITE_BFF_REAL_WRITES=false) 重新發佈一次，並驗證發佈後的 bundle 不再含 seed fallback assets。本任務不直接動 execute-plans repo，而是寫一個 pantheon 端的 audit script + evidence packet，記錄 publish 條件、build env、bundle hash、verification probe 結果。 | Gemini | Gemini2 | todo | - | 2026-05-17 18:44:50 | Assignment created |
| `OSS-QLIB-V2-001` | Sprint 8 / EPIC-OSS-V2 | Qlib production-scale rolling + registry admission | 把 OSS-QLIB-002 的 rolling pipeline 升級到 production scale：使用 MGMT-QLIB-001 已建好的 TWSE OHLCV dataset（≥50 instruments × ≥2 years），跑完整 rolling-window 訓練，產 model_artifact 並提交 registry admission packet。獨立檔案路徑。 | Codex | Codex2 | todo | `OSS-QLIB-002`, `MGMT-QLIB-001` | 2026-05-17 19:01:23 | Assignment created |
| `OSS-STAT-V2-001` | Sprint 8 / EPIC-OSS-V2 | statsmodels production cointegration on TWSE pairs | 把 OSS-STAT-001 cointegration adapter 升級到 production：對 10 個 TWSE 大型股配對跑 2-year rolling Engle-Granger 檢定，輸出 signal_snapshot artifact 含 top-N cointegrated pairs，提交 registry admission packet。獨立檔案。 | Copilot | Codex | todo | `OSS-STAT-001`, `MGMT-QLIB-001` | 2026-05-17 19:01:29 | Assignment created |
| `OSS-QUANTLIB-V2-001` | Sprint 8 / EPIC-OSS-V2 | QuantLib production option chain pricer + greeks | 把 OSS-QUANTLIB-001 option pricer 升級為 production：對台指選擇權(TXO)鏈跨多檔履約價與多個到期日定價，輸出含 greeks 的 pricing_snapshot artifact，提交 registry admission packet。獨立檔案。 | Copilot | Codex2 | todo | `OSS-QUANTLIB-001` | 2026-05-17 19:01:36 | Assignment created |
| `OSS-RLLIB-V2-001` | Sprint 8 / EPIC-OSS-V2 | RLlib production PPO on TWSE trading env | 把 OSS-RLLIB-001 PPO skeleton 升級到 production：用 TWSE OHLCV 作為環境的 observation/action space，跑 ≥100 iter PPO，輸出 model_artifact 含 trained_policy 與 evaluation_summary，提交 registry admission packet。CPU-only。獨立檔案。 | Claude | Codex | todo | `OSS-RLLIB-001`, `MGMT-QLIB-001` | 2026-05-17 19:01:43 | Assignment created |
| `OSS-FINRL-V2-001` | Sprint 8 / EPIC-OSS-V2 | FinRL production DRL on TWSE stock env | 把 OSS-FINRL-001 DRL skeleton 升級到 production：用 TWSE OHLCV 作為 FinRL StockTradingEnv，跑 ≥1000 steps DDPG 或 PPO，輸出 model_artifact 含 evaluation_summary（sharpe annual_return max_drawdown），提交 registry admission packet。CPU-only。 | Gemini2 | Codex2 | todo | `OSS-FINRL-001`, `MGMT-QLIB-001` | 2026-05-17 19:01:50 | Assignment created |
| `OODA-E2E-001` | Sprint 8 / EPIC-OODA-E2E | OODA E2E #1: source → StrategySpec transition test | OODA Observe 階段第一步：實作整合測試證明「真實 SourceRecord → StrategySpec」這個 transition 可端到端走完。使用 SRC-* 與 STRAT-* 既有 service code，不重做。獨立 test 檔。 | Codex | Codex2 | todo | `SRC-001`, `STRAT-001`, `STRAT-003` | 2026-05-17 19:02:55 | Assignment created |
| `OODA-E2E-002` | Sprint 8 / EPIC-OODA-E2E | OODA E2E #2: StrategySpec → ExperimentRun transition test | OODA Observe→Orient 階段：證明「StrategySpec → ExperimentRun」transition 可端到端走完。使用 EXP-001..002 service + 一個 OSS adapter (vectorbt VBT-001) 跑 backtest。獨立 test 檔。 | Codex2 | Codex | todo | `STRAT-001`, `EXP-001`, `EXP-002`, `VBT-001` | 2026-05-17 19:03:09 | Assignment created |
| `OODA-E2E-003` | Sprint 8 / EPIC-OODA-E2E | OODA E2E #3: ExperimentRun → CandidateArtifact admission test | OODA Orient→Decide 階段：證明「ExperimentRun → CandidateArtifact → Registry admission」transition 可端到端走完。使用 EXP-005 writeback + Registry promotion service。獨立 test 檔。 | Claude | Codex | todo | `EXP-005`, `REG-002` | 2026-05-17 19:03:27 | Assignment created |
| `OODA-E2E-004` | Sprint 8 / EPIC-OODA-E2E | OODA E2E #4: Admission → ApprovalDecision → DeploymentPlan(paper) test | OODA Decide 階段：證明「CandidateArtifact → ApprovalDecision → DeploymentPlan(paper)」transition 可端到端走完。使用 GOV-001 ApprovalDecision + DEP-001 DeploymentPlan service。獨立 test 檔。 | Claude | Codex2 | todo | `GOV-001`, `DEP-001`, `DEP-002`, `DEP-004` | 2026-05-17 19:03:39 | Assignment created |
| `OODA-E2E-005` | Sprint 8 / EPIC-OODA-E2E | OODA E2E #5: DeploymentPlan(paper) → RuntimeBinding → paper run test | OODA Act 階段：證明「DeploymentPlan(paper) → RuntimeBinding → ArtifactLoader → paper algorithm」transition 可端到端走完。使用 RT-001..002 + EX-002-RB loader + LEAN-ALGO-001 algorithm smoke。獨立 test 檔，5 trading days deterministic 數據，無 broker。 | Claude2 | Codex | review_approved | `DEP-001`, `RT-001`, `RT-002`, `EX-002-RB`, `LEAN-ALGO-001` | 2026-05-18 03:51:14 | Review approved by Codex. Focused verification passed: PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/e2e/test_deployment_plan_to_paper_run.py services/execution/lean_runtime/test_algorithm_smoke.py -q -x => 8 passed in 2.87s. See support/reviews/OODA-E2E-005-review-codex.md; owner Claude2 should finalize and mark done. |
| `OODA-E2E-006` | Sprint 8 / EPIC-OODA-E2E | OODA E2E #6: telemetry → Incident → Postmortem → EvolutionDecisionProposal test | OODA Learn 階段：證明「paper run telemetry → IncidentCase → Postmortem → EvolutionDecisionProposal」transition 可端到端走完。注入 1 條合成 incident-trigger telemetry，跑 POST-EVO-BRIDGE。獨立 test 檔，無 live mutation。 | Claude | Claude2 | todo | `TEL-001`, `INC-001-RB`, `POST-001`, `POST-EVO-BRIDGE` | 2026-05-17 19:04:00 | Assignment created |
| `OODA-E2E-007` | Sprint 8 / EPIC-OODA-E2E | OODA E2E #7: full OodaLoopPacket closure + evidence chain | OODA 全環收尾：把上述 6 個 transition test 串成單一 OodaLoopPacket 並驗證所有欄位齊全（observe/orient/decide/act/learn refs）。產出 evidence packet 與 closeout summary。獨立 test 檔。 | Codex | Claude | todo | `OODA-E2E-001`, `OODA-E2E-002`, `OODA-E2E-003`, `OODA-E2E-004`, `OODA-E2E-005`, `OODA-E2E-006` | 2026-05-17 19:04:11 | Assignment created |
| `STRAT-V2-001` | Sprint 8 / EPIC-STRAT-EXP-DEEP | Strategy spec distillation production smoke (real research note) | 把 STRAT-003 source converter 升級到 production：吃真實 internal research note (docs/research/notes/*.md 或 fixture)，產出可進 registry 的 StrategySpec，含 evidence_refs + code_refs 完整 binding。獨立 module，不改 STRAT-001..004 公開 API。 | Copilot | Codex2 | todo | `STRAT-003`, `STRAT-004`, `SRC-001` | 2026-05-17 19:05:16 | Assignment created |
| `STRAT-V2-002` | Sprint 8 / EPIC-STRAT-EXP-DEEP | Strategy lineage tree backend read API | 新增 lineage backend API：給定 strategy_spec_id 回傳完整 lineage tree（source_record → strategy_spec → experiment_runs → candidate_artifacts → deployment_plans → runtime_bindings）。獨立 module，不改 LIN-001 既有 read-model。 | Claude2 | Codex | todo | `LIN-001`, `STRAT-001`, `EXP-001` | 2026-05-17 19:05:29 | Assignment created |
| `EXP-V2-001` | Sprint 8 / EPIC-STRAT-EXP-DEEP | Experiment orchestrator parallel multi-backend dispatch | 升級 experiment orchestrator 支援平行多 backend：同一個 ExperimentTask 可以同時派給 vectorbt + Qlib + statsmodels 跑，回傳 N 個獨立 ExperimentRun 加上比較摘要。獨立 module，不改 EXP-001 公開 schema。 | Codex | Codex2 | todo | `EXP-001`, `EXP-002`, `VBT-001`, `OSS-QLIB-002`, `OSS-STAT-001` | 2026-05-17 19:05:38 | Assignment created |
| `EXP-V2-002` | Sprint 8 / EPIC-STRAT-EXP-DEEP | ExperimentRun multi-artifact lineage tree | 新增多 artifact-type lineage tree：ExperimentRun 可同時產 model_artifact + feature_set + signal_snapshot + optimizer_result，本任務確保 lineage edges 正確連接 N 個 artifact 而非單一。獨立 module。 | Codex2 | Copilot | todo | `EXP-005`, `LIN-001` | 2026-05-17 19:05:48 | Assignment created |
| `SPRINT-8-CLOSEOUT` | Sprint 8 / EPIC-CLOSEOUT | Sprint 8 retrospective + closeout + Sprint 9 candidate topics | Sprint 8 收尾：彙整 16 條子任務 evidence、產 sprint retrospective + 統計報告（哪些 EPIC 過、哪些 EPIC 殘留缺口）、產 sprint 9 候選議題 raw list（供下一輪 planning 用）。獨立 evidence packet。 | Claude | Codex | todo | `OSS-QLIB-V2-001`, `OSS-STAT-V2-001`, `OSS-QUANTLIB-V2-001`, `OSS-RLLIB-V2-001`, `OSS-FINRL-V2-001`, `OODA-E2E-007`, `STRAT-V2-001`, `STRAT-V2-002`, `EXP-V2-001`, `EXP-V2-002` | 2026-05-17 19:06:02 | Assignment created |

## Handoff Queue

| Task | From | To | Message | Status | Created At |
|---|---|---|---|---|---|
| `OODA-E2E-005` | Codex | Claude2 | Review approved by Codex. Focused verification passed: PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/e2e/test_deployment_plan_to_paper_run.py services/execution/lean_runtime/test_algorithm_smoke.py -q -x => 8 passed in 2.87s. See support/reviews/OODA-E2E-005-review-codex.md; owner Claude2 should finalize and mark done. | pending | 2026-05-18 03:51:14 |

## Blockers

| Task | Owner | Waiting For | Message | Status |
|---|---|---|---|---|
| _(none)_ | - | - | - | - |

## Review Notes

| Task | Reviewer | 修正重點 | Review File |
|---|---|---|---|
| `OSS-STAT-001-SIDECAR-ACCEPTANCE` | Claude | 審查通過：sidecar acceptance packet 文件完整，正確記錄 shadowing 問題解決與最終 artifact 形狀 | support/sidecars/OSS-STAT-001/OSS-STAT-001-SIDECAR-ACCEPTANCE.md |
| `OODA-E2E-005` | Codex | 審查通過：focused pytest 8 passed；DeploymentPlan fixture -> RuntimeBinding -> ArtifactLoader -> LEAN paper smoke identity 與 no-live-broker assertions 均已覆蓋。 | support/reviews/OODA-E2E-005-review-codex.md |

## Lovable Coordination

- Last coordination scan: -
- Tracked features: `0`
- Lovable-ready packets: `0`
- Waiting for Lovable/front-end: `0`
- UI-done returned: `0`
- Frontend feedback returned: `0`
- Open BFF gaps: `0`
- Backend route live: `0`
- Pantheon handoff published: `0`
- Mirrored to front default branch: `0`
- Dispatch recorded in coordinator state: `0`
- Receiver-visible payload on front default branch: `0`
- Lovable consumed packet: `0`
- UI activated: `0`
- Runtime verified: `0`

| Feature | Screen | Stage | Lovable Ready | Mirrored | UI Done | Feedback | Next Action |
|---|---|---|---|---|---|---|---|
| _(none)_ | - | - | - | - | - | - | - |

Tracked-feature note: the table above only lists modules that currently have coordination feature records.
Archive-done route-live activation publication lanes that remain outside explicit feature rows: `CW-02`, `KW-04`, `KW-05`, `RW-02`, `RW-04`, `RW-05`, `KW-02`, `KW-03`, `TW-01`, `TW-02`, `TW-04`.
Do not read those omitted modules as open Pantheon backlog purely because they are absent from the coordination feature table.

## Latest Checkpoints

- 2026-05-16 01:52:57 Orchestrator: PostToolUse: Bash
- 2026-05-16 01:53:02 Orchestrator: PreToolUse: Bash
- 2026-05-16 01:53:02 Orchestrator: PostToolUse: Bash
- 2026-05-16 01:53:08 Orchestrator: PreToolUse: Bash
- 2026-05-16 01:53:08 Orchestrator: PostToolUse: Bash
- 2026-05-16 01:53:13 Orchestrator: PreToolUse: Bash
- 2026-05-18 01:47:25 Claude2: `OODA-E2E-005` Implementation already committed: DeploymentPlan(paper) -> RuntimeBinding -> LEAN smoke e2e tests complete with 6 passing tests
- 2026-05-18 01:48:34 Claude2: `OODA-E2E-005` Handoff to Codex: OODA-E2E-005 implementation complete. All 6 tests pass (pytest -q -x exit 0). Artifacts: tests/e2e/test_deployment_plan_to_paper_run.py (6 tests) + tests/e2e/fixtures/deployment_plan_for_runtime.json. Tests cover: fixture validation, RuntimeManager paper binding, 5-day LEAN smoke run with >=1 fill, broker live flag stays false, artifact identity check, and full e2e fixture-binding identity assertion. Branch: task/OODA-E2E-005 (commits 49833039 and 418d286c). No live broker access, BROKER_PRODUCTION_LIVE_ENABLED stays false throughout.
- 2026-05-18 01:51:28 Claude2: `OODA-E2E-005` Re-verification after owned_ready_dispatch wakeup: all 6 pytest tests still pass (pytest -q -x exit 0). Task remains in review awaiting Codex. No new changes needed.
- 2026-05-18 01:55:51 unknown: `OODA-E2E-005` -
- 2026-05-18 02:25:11 Codex2: `OODA-E2E-005` Codex2 wakeup stopped before task work: ai-status assigns OODA-E2E-005 to owner Claude2 and reviewer Codex, status review; current worker Codex2 cannot owner-progress, approve, or close out. Worktree also has pre-existing generated dirty state outside task artifacts: ai-activity-log.jsonl, dashboard-bundle.json, docs-site/ai-status.json, docs-site/current-work.md, docs-site/dashboard-bundle.json. Requires correct Codex reviewer dispatch or task reassignment plus clean/owned state handling.
- 2026-05-18 03:27:31 Claude2: `OODA-E2E-005` Re-verification pass 2 (owned_ready_dispatch wakeup): all 6 pytest tests still pass (pytest -q -x exit 0 in 3.40s). No implementation changes needed. Task remains in review awaiting Codex approval.
- 2026-05-18 03:28:15 Claude2: `OODA-E2E-005` Re-verification pass 2 (owned_ready_dispatch wakeup): all 6 pytest tests still pass (pytest -q -x exit 0 in 3.40s). No implementation changes needed. Task remains in review awaiting Codex approval.
- 2026-05-18 03:29:36 unknown: `OODA-E2E-005` -
- 2026-05-18 03:34:26 Claude2: `OODA-E2E-005` Re-verification pass 3 (owned_in_progress_dispatch): all 6 pytest tests pass in 1.56s (pytest -q -x exit 0). No implementation changes. Task remains in review awaiting Codex approval. Artifacts: tests/e2e/test_deployment_plan_to_paper_run.py + tests/e2e/fixtures/deployment_plan_for_runtime.json. Branch: task/OODA-E2E-005.
- 2026-05-18 03:34:41 Claude2: `OODA-E2E-005` Re-verification pass 3 (owned_in_progress_dispatch): all 6 pytest tests pass in 1.56s (pytest -q -x exit 0). No implementation changes. Task remains in review awaiting Codex approval.
- 2026-05-18 03:34:57 Claude2: `OODA-E2E-005` Re-verification pass 3 (owned_in_progress_dispatch): all 6 pytest tests pass in 1.56s. No implementation changes. Task remains in review awaiting Codex approval.
- 2026-05-18 03:35:20 Claude2: `OODA-E2E-005` Re-verification pass 3 (owned_in_progress_dispatch): all 6 pytest tests pass in 1.56s. Task remains in review awaiting Codex approval.
- 2026-05-18 03:36:20 unknown: `OODA-E2E-005` -
- 2026-05-18 03:51:14 Codex: `OODA-E2E-005` Review approved by Codex. Focused verification passed: PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/e2e/test_deployment_plan_to_paper_run.py services/execution/lean_runtime/test_algorithm_smoke.py -q -x => 8 passed in 2.87s. See support/reviews/OODA-E2E-005-review-codex.md; owner Claude2 should finalize and mark done.
