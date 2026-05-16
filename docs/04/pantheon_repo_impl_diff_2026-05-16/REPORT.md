# Pantheon 各 Repo 實作差異完整盤點報告

> 文件版本：v3.0 — 依最新 `pantheon@master` / `execute-plans@main` 重新盤點
> 日期：2026-05-16
> 文件用途：交付系統規劃團隊與開發團隊，作為後續排入開發工作的 GAP / Roadmap / Acceptance 依據
> 盤點範圍：`ajoe734/pantheon`、`ajoe734/execute-plans`、`ajoe734/Lean` / `ajoe734/lean-platform` 角色釐清
> 排除：舊 `front-ai-trading-system` 不再作為主判斷基礎，僅作歷史參考

---

## 0. 重要結論

這次重新盤點後，原本幾個判斷需要更新。

第一，`pantheon` 現在 default branch 已是 `master`，因此所有後端盤點改以 `pantheon@master` 為基準。

第二，`execute-plans` 是目前 Management Console / Agora Workbench 的前端主線，並不是單純 mock UI。它已具備產品級結構、BFF boundary、strict integration 模式、state-machine driven UI、高風險操作確認、audit / realtime 事件、handoff 隔離與 FE/spec current SoT。

第三，`pantheon@master` 的 Registry / Promotion 不是 blueprint。`artifact_state / deployment_stage` 分離服務已標示為 implemented，Artifact Loader 也不是純 contract，而是 contract locked、service-local loader / Object Store adapter / materialization helper / smoke path 已存在，但 LEAN algorithm-level coverage 仍 deferred。

第四，最新 `current-work.md` / `ai-status.json` 顯示多項原本列為 GAP 的項目已經完成：trainer session endpoints、teaching schema、consult / ask / committee flow、imitation dataset skeleton、EvolutionDecision service、loop-runs、sentinel findings 等。這意味原本以 2026-05-09 live probe 為主的 GAP 判斷已經過時，應改以 2026-05-16 current-work / task archive 為準。

第五，目前真正仍未完成且需要驗證的主缺口集中在：

1. Source ingest / StrategySpec distillation 的後端閉環。
2. Research backend adapters：Qlib、vectorbt、statsmodels、QuantLib、RL Lab。
3. DeploymentPlan / RuntimeBinding / Runtime Manager 的 full production path。
4. Artifact Loader metadata migration：legacy `promotion_state` → canonical `artifact_state + deployment_stage`。
5. LEAN algorithm-level smoke / runtime coverage。
6. Telemetry / audit / incident / postmortem 是否已從 schema / endpoint 進入完整 production event loop。
7. Broker / Shioaji credentials blocker 導致 Track E / M7 canary readiness 未閉合。

---

## 1. Repo 基準與角色

### 1.1 `ajoe734/pantheon@master`

#### 角色

Pantheon 後端主 repo，承接：

- Registry / Promotion
- Artifact governance
- BFF backend
- Control-plane BFF
- Training / consult / ask / evolution endpoints
- Artifact loader
- Runtime / deployment contract
- Telemetry / evolution schema 與部分 endpoint
- 系統 canonical 文件、current-work、task archive

#### 目前 repo 狀態摘要

`ai-status.json` 顯示目前 sprint 為：

```text
2026-05-16-pantheon-bff-p0-foundation
```

其 objective 直接承接 `GAP_dev_team_master_rebaseline_2026-05-15.md`，以 `pantheon@master + execute-plans@main` 為基準，並行 6 條 EPIC：

1. EPIC-BFF-P0
2. EPIC-GOV-DEPLOY
3. EPIC-RUNTIME
4. EPIC-TELEMETRY
5. EPIC-RESEARCH
6. EPIC-EVOLUTION

目前唯一 active blocker 是：

```text
MGMT-BROKER-002 — Shioaji account readiness check
原因：等待 broker credentials（API_KEY / SECRET_KEY）
```

#### 關鍵事實

- Registry / Promotion 已具 implemented service semantics。
- Artifact Loader 已 partial implemented / in-progress。
- 多個 Sprint 5 / Sprint 6 任務已完成，例如 TRN、ASK、IMT、EXP、EVO、LOOP、SENT 等。
- Track E 仍有 broker credential blocker。

### 1.2 `ajoe734/execute-plans@main`

#### 角色

Pantheon 前端主線，定位為集團內部操作介面：

1. Management Console
2. Agora Workbench

#### 目前 repo 狀態摘要

`execute-plans` 已具備：

- mock mode
- live BFF mode
- strict integration mode
- `VITE_BFF_FALLBACK=auto / strict`
- `VITE_BFF_REAL_WRITES=false` 預設保護
- BFF-only boundary
- state-machine-driven UI
- high-risk action 三重閘門
- Agora -> Management handoff
- i18n 雙語對齊
- 18 個 entity state machines
- RBAC / permission filtering
- AuditTimeline / HighRiskConfirm / JobProgressDrawer / LifecycleStepper 等共用元件

Management Console 已包含 Strategy / Persona / Capital Pool / Rebalance / Approvals / Runtimes / Risk / Incidents / Capabilities。

Agora Workbench 已包含 DailyBrief / Signals / Notebook / AskPersonas / Committee / DecisionJournal / AlertTriage / InsightInbox / Trainer / MemoryReview / SkillCoaching。

前端 / spec / state-machine / mock / strict integration surface：**高度成熟**。

### 1.3 `ajoe734/Lean` / `ajoe734/lean-platform`

Execution substrate：作為 LEAN runtime / broker / paper / live 執行基礎。本輪沒有指定它跟 Pantheon 管理系統主 repo 深接，因為目前的 GAP 主要在 `pantheon` / `execute-plans`。它們的定位仍是：

- 不承接 Pantheon governance state
- 不承接 Management UI
- 未來由 Runtime Manager / Artifact Loader 對接
- 作為 paper / canary / live runtime substrate

目前仍需完成：LEAN algorithm-level artifact-loader smoke coverage、RuntimeBinding 到 LEAN runtime 的 actual run path、Broker credentials / Shioaji readiness。

---

## 2. 目標系統與目前狀態差異總表

| 領域 | 預定目標 | 目前實作狀態 | 差異 / GAP | 成熟度 |
|---|---|---|---|---|
| Management Console | 管理策略、人員、資金池、部署、runtime、risk、incident | `execute-plans` 已有完整 FE/spec/state-machine surface | 需確認 live BFF strict path 是否已全部可走 | implemented FE / backend validating |
| Agora Workbench | 投資研究、AI persona 協作、Trainer、Committee、Journal | `execute-plans` 已有完整 workbench structure | 後端 ask/trainer/research/knowledge 是否在 live 完整連通 route 驗證 | implemented FE + partial backend |
| Registry / Promotion | artifact governance source of truth | `pantheon@master` 已 implemented split service | DeploymentPlan / RuntimeBinding 需要完整串接起 | implemented core |
| Artifact Loader | governed artifact -> execution gate | contract locked + service-local loader + Object Store adapter + smoke path | metadata migration + LEAN algorithm-level coverage 未完成 | partial implemented |
| BFF P0 | session/action/approval/entity routes | `main.py` 已有大量 BFF control logic，current-work 顯示 P0 sprint | 須以 live probe 重新驗證 endpoints，不再用 5/09 舊結果 | likely partial/advanced |
| Trainer | teaching session / patch / preview / commit | TRN-001/002/004 已完成 | rapid-eval / persona registry integration 仍需驗證 | partial implemented |
| Consultation / Committee | ask session / committee / memo / SSE | ASK-001~005 已完成 | review gate integration 仍需驗證 | partial implemented |
| Imitation | trajectory / preference / correction / dataset / behavior artifact | IMT-001~004 已完成 | training algorithm / policy evaluation 未完成 | dataset pipeline skeleton |
| Experiment | ExperimentTask / ExperimentRun | EXP-001 完成 | Qlib/vectorbt/statsmodels adapters 未完成 | schema implemented / backend partial |
| Evolution | EvolutionDecision / loop-runs / sentinel | EVO-001、LOOP-001-RB、SENT-001 完成 | closed-loop action execution 仍需 governance/runtime 串接 | partial implemented |
| Telemetry / Incident | audit / alert / incident / postmortem / reconciliation | current-work 仍把 EPIC-TELEMETRY 列為主線 | 需確認 TEL/INC/POST 是否真正完成，缺 runtime data loop | partial / uncertain |
| Runtime / Execution | DeploymentPlan -> RuntimeBinding -> LEAN | loader partial，runtime plan 仍是 EPIC | Runtime Manager、RuntimeBinding、LEAN smoke、broker readiness | pending / partial |

---

## 3. 9 個循環重新盤點

### 3.1 循環 1：研究素材接路

```text
OpenClaw cron -> OpenAlex / GitHub allowlist -> Source Registry -> normalize -> StrategySpec seed
```

目標：建立初步外部與內部研究素材入口，讓 paper、repo、教學範例、內部 memo 都能轉成可治理的 `SourceRecord` / `StrategySpecSeed`。

目前實作：Agora Workbench 已有 DailyBrief、Signals、Notebook、InsightInbox 等初步入口。`ai-status.json` 的 EPIC-RESEARCH 明確把 Source Ingest（SRC）放入 Sprint 5 主線。目前在 recently executed tasks 中沒看到 `SRC-*` 任務完成記載。

成熟度：**blueprint / planned backend**。

建議後續任務：

| 任務 | 說明 | 優先級 |
|---|---|---|
| SRC-001 | SourceRecord schema + API | P2 |
| SRC-002 | internal research ingest | P2 |
| SRC-003 | GitHub allowlist ingest | P3 |
| SRC-004 | academic source ingest adapter | P3 |
| SRC-005 | StrategySpecSeed builder | P2 |

### 3.2 循環 2：策略蒸餾接路

```text
discovered material -> scaffolded spec/data/template/baseline
```

目前實作：Registry contract 已支援 `strategy_spec` artifact type；current-work 中 Lovable Coordination 顯示 KW-05 `knowledge-strategy-spec` loop complete，但這只到 UI / handoff / workbench 層。

成熟度：**contract + UI loop complete / backend conversion pending**。

建議：STRAT-001..004（model/registry/converter/evidence binding）。

### 3.3 循環 3：Alpha 複製 / 研究接路

```text
scaffolded -> backend selection -> experiment_run -> replicated
```

目前實作：`EXP-001` 已完成 ExperimentTask / ExperimentRun schema；Registry 支援 `producer_run_id`、`evaluation_summary`、`lineage`；Artifact types 包含 `model_artifact`、`feature_set`、`signal_snapshot`、`optimizer_result`。

差異：Qlib / vectorbt / statsmodels / QuantLib / RL adapter 未完成，ExperimentRun -> CandidateArtifact 完整 production path 仍需驗證。

成熟度：**schema implemented / backend adapters pending**。

建議：EXP-002（orchestrator）、EXP-003（writeback）、VBT-001、QLIB-001、STAT-001、QLIB-002（rolling/OOS）。

### 3.4 循環 4：Persona 行為接路

目前實作：TRN-001..004 已完成 TeachingSession / TeachingEvent schema、trainer session endpoints（25 個 contract tests）、commit/discard/replay。

差異：rapid eval 與 vectorbt 整合需驗證；persona registry / route policy 與 teaching commit 整合需驗證。

成熟度：**partial implemented / strong progress**。

建議：TRN-005..007 + PER-003。

### 3.5 循環 5：Human Trader 模仿接路

目前實作：IMT-001..004 已完成 TraderTrajectory schema、PreferenceExample / CorrectionTrace、dataset builder skeleton、behavior policy artifact type registration。

差異：imitation training 本體未實作；BC / DAgger / GAIL / AIRL pipeline 未規劃完整；behavior policy artifact 目前只是 artifact type / skeleton。

成熟度：**dataset skeleton implemented / model training pending**。

建議：IMT-005..008（trainer skeleton、evaluation、validation gate、TRL bridge）。

### 3.6 循環 6：Consultation / Committee 接路

目前實作：ASK-001..005 全部完成（sessions endpoint、Schema、lifecycle、memo publish、SSE event）；execute-plans Agora 已有 AskPersonas / Committee / DecisionJournal。

成熟度：**partial-to-near implemented**。

建議：ASK-006..008（e2e test、evidence redaction、sponsor decision bridge）。

### 3.7 循環 7：Promotion / Deployment 接路

目前實作：Registry / Promotion canonical path implemented；`artifact_state` / `deployment_stage` 已正式分離；current-work 把 `GOV-001` / `DEP-001` 放在 EPIC-GOV-DEPLOY。

差異：ApprovalDecision first-class backend 仍需確認是否完成；DeploymentPlan contract/service / stage planner / projection / pool-runtime compat 仍需完成；Metadata migration 仍是阻塞。

成熟度：**registry implemented / deployment chain pending**。

建議：GOV-001、DEP-001..004。

### 3.8 循環 8：Capital Pool Execution 接路

目前實作：execute-plans 有 Capital Pool / Runtimes / Risk / Incidents 管理表面；Artifact Loader partial implemented；service-local loader、Object Store adapter、materialization helper、smoke path 已存在。

差異：RuntimeBinding schema/service、Runtime Manager skeleton 需確認；LEAN algorithm-level smoke 未完成；broker / Shioaji credentials 未提供 → M7 canary readiness 未閉合。

成熟度：**control surface + loader partial / runtime execution pending**。

建議：RT-001..003、EX-002、EX-003、BROKER-001（blocked）。

### 3.9 循環 9：Telemetry / Postmortem / Evolution 接路

目前實作：execute-plans 有 Alerts / Incidents / AuditTimeline / JobProgressDrawer / realtime / mutation flow surface；EVO-001、LOOP-001-RB、SENT-001 已完成；current-work 把 EPIC-TELEMETRY 放為 Sprint 4 主線。

差異：TelemetryEvent canonical schema 是否完成需驗證；RuntimeHeartbeat ingest 完成需驗證；AuditAction、Alerts、Incidents、Postmortem endpoints 的真正整合需確認；reconciliation / drift service 仍需補。

成熟度：**evolution seed partial implemented / telemetry backend still gap**。

建議：TEL-001、TEL-002、AUD-001、INC-001、POST-001、REC-001、EVO-002。

---

## 4. 三個大循環差異盤點

### 4.1 大循環 A：Learning / Discovery Loop

涵蓋循環 1、2、3、4、5。成熟度：中等偏弱，但比上輪盤點進步很多。

進步點：Trainer schema / endpoints / commit-discard-replay 完成；ExperimentTask / ExperimentRun schema 完成；Imitation skeleton 完成。

主要缺口：source ingest、StrategySpec distillation、Qlib/vectorbt/statsmodels/QuantLib/RL adapters、imitation model training。

### 4.2 大循環 B：Governance Loop

涵蓋循環 6、7、9 治理部分。成熟度：最高。

進步點：Registry / Promotion implemented；artifact_state / deployment_stage 分離；Consult / Ask / Committee / Memo / SSE 大致完成；FE/spec 成熟。

主要缺口：ApprovalDecision first-class 確認；DeploymentPlan / RuntimeBinding；metadata migration。

### 4.3 大循環 C：Capital / Execution Loop

涵蓋循環 7、8、9 runtime 部分。成熟度：中等，execution substrate 未閉合。

進步點：Management runtime/capital/risk surface 成熟；Artifact Loader partial implemented；Loader path 已有 Object Store adapter / materialization helper / smoke path。

主要缺口：Runtime Manager full、RuntimeBinding production state、LEAN smoke、Broker credentials blocked、telemetry/fill/position 真實回饋迴路。

---

## 5. 優先級開發計畫

### P0 重新驗證

P0-1：以最新 master 重跑 live route probe（取代 5/09 結果）。
P0-2：確認 BFF strict integration 可全跑核心 Management flow。
P0-3：重新校準 GAP 文件，把已完成的 TRN / ASK / IMT / EXP / EVO / SENT / LOOP 移出 GAP。

### P1 Governance → Deployment → Runtime

GOV-VERIFY、DEP-001、DEP-002、RT-001、RT-002、EX-002、EX-003。

### P2 Telemetry / Postmortem / Evolution

TEL-VERIFY、AUD-VERIFY、INC-001、POST-001、REC-001、EVO-BRIDGE。

### P3 Research / Learning / OSS Integration

SRC-001、STRAT-001、EXP-002、VBT-001、QLIB-001、STAT-001、IMT-005。

---

## 6. 與預定目標的差異總結

已接近或已達：前後端 Management/Agora 產品化、BFF boundary、state-machine UI、high-risk gates、Agora 不直接 deploy/動資金、Registry/Promotion canonical service、artifact_state/deployment_stage 分離、Loader contract + partial、Trainer endpoints、Ask/Committee/Memo/SSE、Imitation skeleton、Evolution seed。

尚未達成：完整 research ingest loop、formal research backend、deployment runtime chain、execution canonical metadata、LEAN smoke、telemetry OODA、autonomous evolution、broker readiness。

---

## 7. 風險與阻塞

7.1 舊 probe 與新 current-work 不一致 — 必須以新 live probe 重新驗證。
7.2 `current-work.md` 是 derived narrative — 應以 ai-status.json、task archive、actual code/tests/route probe 交叉驗證。
7.3 metadata migration 是 execution 關鍵技術債。
7.4 broker credential blocker 阻塞 M7 canary readiness。

---

## 8. 開發團隊立即執行清單

8.1 第一優先：重新驗證現況 — 重跑 live BFF probe、產出新 route matrix、把已完成 task 從 GAP 移除、把未完成 task 按 EPIC 歸類。
8.2 第二優先：對接治理到 runtime — Verify ApprovalDecision、Implement DeploymentPlan、RuntimeBinding、Runtime Manager、loader migration、LEAN paper smoke。
8.3 第三優先：對接 telemetry / evolution — Verify AuditAction、Implement TelemetryEvent / Heartbeat / Incident / Postmortem、Bridge Postmortem -> EvolutionDecision。
8.4 第四優先：對接 research / learning — Source ingest、StrategySpec distillation、Experiment orchestrator、Qlib/vectorbt adapters、Imitation baseline trainer。

---

## 9. 下一份報告建議

`Route_Implementation_Matrix_2026-05-16.md`、`Task_Completion_vs_GAP_Reconciliation_2026-05-16.md`、`Deployment_Runtime_Metadata_Migration_Plan.md`、`OODA_Closed_Loop_Acceptance_Checklist.md`、`OSS_Framework_Integration_Maturity_Matrix.md`。

---

## 10. 最終結論

Pantheon 已從 blueprint 階段進入 integration / closure 階段。前端與治理主幹成熟，下一步重點不是再設計架構，而是：

1. 重新驗證 live BFF route。
2. 對接 governance → deployment → runtime。
3. 補 telemetry/postmortem/evolution 的真實資料迴路。
4. 再補 research/source/OSS backend。

開發團隊應以此報告為依據，優先消化 P0/P1 integration blockers，再進入 P2/P3 的 research/evolution 擴展。
