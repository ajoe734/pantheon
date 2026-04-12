# LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md

Last updated: 2026-04-09
Status: canonical loop trigger and concurrency policy for Pantheon
Tier: L1 Platform Architecture & Policy
Scope: trigger model for all major concurrent loops, race condition resolution, immutable vs mutable version policy, and scheduling boundaries
Conflict rule: this document defines when and how loops execute; it does not override the internal logic of individual loops defined in their domain-specific policies

---

## 1. 目的

本文件定義 Pantheon 中所有主要併發迴圈的：

- 觸發模型（event-driven / cron / continuous / command-driven）
- 頻率 / 週期
- race condition 解決規則
- immutable vs mutable version policy

Pantheon 不採用「全部都是 event-driven」或「全部都是 cron」的單一模型。
正式採 **hybrid trigger model**：

- 同步 command
- 事件驅動 workflow
- cron / batch 輔助
- runtime continuous loop

目前共定義 **11 個主要迴圈**。

---

## 2. 結論摘要

### 2.1 唯一 continuous loop
11 個主要迴圈中，只有 **capital pool execution（LEAN runtime）** 是真正長駐連續迴圈。

### 2.2 其餘迴圈分類
- event-driven: strategy distillation, consultation, telemetry ingest
- cron/scheduled: source ingestion, human imitation, reconciliation, evolution daily sweep
- command-driven: promotion/deployment, persona teaching, alpha replication
- continuous + event: BFF health monitoring

### 2.3 Race condition 規則
promotion 只能消費 immutable approved artifact snapshot。
distillation 只會更新最新 mutable draft head。
deploy 只吃 immutable approved artifact snapshot。

### 2.4 最新 draft head 可以變，但 approved artifact 一旦生成就是 immutable。

---

## 3. 逐條定義

### 3.1 Source Ingestion

| 項目 | 定義 |
|---|---|
| 主要觸發 | cron / scheduled |
| 次要觸發 | manual trigger |
| 不是 | continuous crawl |
| 建議頻率 | papers: 每日 / 每小時；repo allowlist: 每日 / 每週；internal notes: 事件進場即寫 |
| 輸出 | `SourceRecord` |
| 競爭條件 | 不與其他迴圈直接競爭；輸出是 distillation 的輸入 |

### 3.2 Strategy Distillation

| 項目 | 定義 |
|---|---|
| 主要觸發 | event-driven — 新的 normalized source 進來就觸發 distillation job |
| 次要觸發 | batch catch-up / re-distill（manual 或 scheduled） |
| 輸出 | `StrategySpec` latest draft |
| 競爭條件 | 只能寫 mutable draft，不碰 approved artifact |

### 3.3 Alpha Replication

| 項目 | 定義 |
|---|---|
| 主要觸發 | human / review-driven — 新 StrategySpec 不自動全量複製 |
| 次要觸發 | scheduled revalidation |
| 輸出 | `ExperimentRun` / alpha template validation |
| 競爭條件 | 需經 researcher / persona / review 決定進 replication queue |

### 3.4 Persona Teaching

| 項目 | 定義 |
|---|---|
| 主要觸發 | user-driven — operator 或 researcher 發起 teaching session |
| 次要觸發 | preview / eval 是 async worker |
| 輸出 | `TeachingSession` / `TeachingEvent` / `ConsultMemo` |
| 競爭條件 | 不與 execution 路徑競爭；teaching 結果需經 evaluation 才能影響 persona |

### 3.5 Human Imitation

| 項目 | 定義 |
|---|---|
| 主要觸發 | batch / scheduled — 不對每個 teaching event 即時訓練 |
| 輸出 | updated imitation model weights / policy |
| 競爭條件 | 模型更新不影響 running artifact；新模型需經 experiment → approval 才能部署 |

### 3.6 Consultation

| 項目 | 定義 |
|---|---|
| 主要觸發 | on-demand event-driven |
| 次要觸發 | committee / red-team 是 async workflow |
| 輸出 | `ConsultRequest` / `ConsultMemo` / committee recommendation |
| 競爭條件 | consultation 結果是 advisory，不直接觸發 deploy |

### 3.7 Promotion / Deployment

| 項目 | 定義 |
|---|---|
| 主要觸發 | explicit command-driven — operator 或治理規則明確發出 deploy 命令 |
| 執行模型 | async — deploy execution 非同步完成 |
| 輸出 | `ApprovalDecision` → `DeploymentPlan` → `RuntimeBinding` |
| 競爭條件 | 只能消費 immutable approved artifact |

### 3.8 Capital Pool Execution

| 項目 | 定義 |
|---|---|
| 主要觸發 | **continuous runtime loop** — LEAN engine 持續運行 |
| 這是 | 11 個迴圈中唯一真正長駐連續迴圈 |
| 輸入 | 已 active 的 `RuntimeBinding` |
| 輸出 | orders, fills, positions, runtime heartbeats |
| 競爭條件 | 不與其他迴圈直接競爭；execution 路徑完全隔離 |

### 3.9 Telemetry / Reconciliation

| 項目 | 定義 |
|---|---|
| telemetry ingest | event-driven — 由 LEAN runtime / runtime-manager / operator actions 產生 |
| reconciliation | scheduled + incident-triggered |
| 輸出 | `TelemetryEvent` / `DriftReport` / `IncidentCase` |
| 競爭條件 | reconciliation 不影響 running runtime；只產生分析結果 |

### 3.10 Evolution

| 項目 | 定義 |
|---|---|
| 主要觸發 | threshold-triggered + daily sweep |
| 限制 | 受 `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY` 的 cooldown 約束 |
| 輸出 | `EvolutionDecision` |
| 競爭條件 | 同一 target 同時間只能有一個 active `EvolutionDecision` |

### 3.11 BFF Health Monitoring

| 項目 | 定義 |
|---|---|
| 主要觸發 | continuous health check — 定期 probe downstream service health |
| 次要觸發 | event-driven — downstream service error rate spike 即時觸發 |
| 輸出 | BFF health metrics → telemetry ingest → incident pipeline |
| 競爭條件 | 不與其他迴圈競爭；BFF degraded mode 不影響 active runtimes |

此迴圈定義見 `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` Section 7。

---

## 4. Race Condition 解決規則

### 4.1 核心原則

> promotion 只能消費 immutable approved artifact snapshot。
> distillation 只會更新最新 mutable draft head。
> 不允許 promotion 指向 mutable head。

### 4.2 具體場景

#### 場景 A: distillation 正在跑，promotion 同時要把同一個 strategy 送 review

誰贏？

**答：不會競爭。** 因為：

- distillation 寫入的是最新 mutable draft head
- promotion 消費的是 immutable approved artifact snapshot
- 兩者操作不同物件，不存在 race condition

#### 場景 B: 兩個 distillation job 同時跑同一個 strategy

誰贏？

**答：最後一個成功者寫入最新的 draft。** 因為 draft 本身就是 mutable working state，多個 distillation 可以視為實驗性迭代。researcher 可比較不同 draft 再決定哪個進 review。

#### 場景 C: reconciliation 正在評估 performance，evolution 同時觸發同一 artifact

誰贏？

**答：evolution 受 cooldown 約束。若同一 target 已有 active `EvolutionDecision`，新的 trigger 被合併到 existing case 或排隊等待 observation window 結束。**

#### 場景 D: deployment 正在進行中，kill switch 同時觸發

誰贏？

**答：kill switch 優先。依照 `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY`，kill switch 走 fast path，deployment saga 被中斷並進入 compensation。**

---

## 5. Immutable vs Mutable Version Policy

### 5.1 Mutable 物件

以下物件可以修改：

- `StrategySpec` latest draft / draft head — distillation 可反覆更新
- Persona route policy / consult policy（需 governance 審核）
- Teaching session events（append-only，但 session state 可變）

### 5.2 Immutable 物件

以下物件一旦寫入就不可修改：

- `ApprovalDecision` — 審批結果不可改
- `Artifact`（approved） — approved artifact 是不可變快照
- `DeploymentPlan` — 部署意圖不可改（可 abort，可新建 replacement）
- `RuntimeBinding` — 實際載入狀態不可改（可 supersede，不可原地修改）
- `TelemetryEvent` — canonical event 是 append-only
- `EvolutionDecision` — 決策歷史不可改

### 5.3 為什麼這樣分

deployment 和 execution 必須能確定性地回答「當時部署的是什麼」。
如果 approved artifact 可以原地修改，整個 governance / rollback / postmortem 鏈就崩壞。

---

## 6. 觸發模型總表

| # | 迴圈 | 觸發模型 | 是否 continuous | 輸出 | 競爭規則 |
|---|---|---|---|---|---|
| 1 | Source ingestion | cron/scheduled | 否 | SourceRecord | 無直接競爭 |
| 2 | Strategy distillation | event-driven | 否 | StrategySpec draft | 只寫 mutable draft |
| 3 | Alpha replication | review-driven + scheduled | 否 | ExperimentRun | 需 review 決定 |
| 4 | Persona teaching | user-driven | 否 | TeachingSession | 不碰 execution |
| 5 | Human imitation | batch/scheduled | 否 | imitation model | 需 experiment → approval |
| 6 | Consultation | on-demand event | 否 | ConsultMemo | advisory only |
| 7 | Promotion/deployment | command-driven | 否 | RuntimeBinding | 只吃 immutable artifact |
| 8 | Capital pool execution | continuous loop | **是** | orders/fills | 完全隔離 |
| 9 | Telemetry/reconciliation | event + scheduled | 否 | DriftReport/Incident | 不影響 running runtime |
| 10 | Evolution | threshold + sweep | 否 | EvolutionDecision | 受 cooldown 約束 |
| 11 | BFF health monitoring | continuous + event | 否 | health metrics | 不影響 active runtimes |

---

## 7. v1 決策

1. 採 hybrid trigger model，不統一為單一觸發機制
2. 只有 capital pool execution 是 continuous loop（共 11 個迴圈）
3. promotion 只消費 immutable approved artifact snapshot
4. distillation 只寫 mutable draft
5. latest draft head 可以變，但 approved artifact immutable
6. kill switch 優先於 deployment
7. evolution 受 cooldown 約束，不與其他迴圈競爭
8. BFF health monitoring 進入 telemetry pipeline，degraded mode 不影響 active runtimes

---

## 8. 後續規格拆解（non-blocking，不影響目前 L1 真相）

以下項目屬於後續 scheduler 與 protocol 細化，不是本文件目前 loop trigger truth 生效的前置條件。

- source ingestion scheduler spec
- distillation job queue design
- evolution daily sweep scheduler spec
- kill switch ↔ deployment saga interruption protocol
- reconciliation schedule matrix
