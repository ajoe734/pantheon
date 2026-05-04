# SA-05 — Repo 角色與責任重映射分析

> **2026-05-03 Canonical correction**: `pantheon/lean` submodule backed by `ajoe734/pantheon-lean.git` is the official execution substrate. Any older `lean-platform` repo-mapping drift language in this SA note is superseded; do not treat `lean-platform` as an active gap or task target.


**文件編號**：SA-05
**文件類型**：System Analysis / Repo Ownership & Responsibility Remapping
**範圍**：front-ai-trading-system、pantheon、Lean、lean-platform 的角色重判定
**版本**：v0.1 Draft

---

## 1. 本章目的

本章處理一個會影響所有後續開發的架構問題：

```text
Execution Plane 到底應該由哪個 repo 承接？
```

原始藍圖把 `lean-platform` 定位為 Execution Substrate，但使用者最新校正指出，實際在 VS Code 中修改的是 `Lean`，`lean-platform` 幾乎沒有動。

因此，本章要做的是：

```text
1. 重新映射 repo responsibility。
2. 判斷這種偏移造成哪些架構差異。
3. 提出正式化選項。
4. 定義後續 Codex / engineering task 應以哪個 repo 為 target。
```

---

## 2. 原藍圖 repo 角色

Pantheon 總索引版藍圖中，repo 落點為：

```text
front-ai-trading-system → Pantheon Console
pantheon → Governance + Registry Core
lean-platform → Execution Substrate
```

原始設計的隱含分層是：

```text
[Console]
front-ai-trading-system

[Control / Governance / Registry]
pantheon

[Execution Product Substrate]
lean-platform

[Upstream LEAN Engine]
Lean / QuantConnect Lean baseline
```

這種分層的好處是：

```text
- Pantheon-specific runtime code 不直接污染 upstream mirror。
- Codex task target 清楚。
- execution adapter / telemetry exporter / broker boundary 有 product-owned repo。
- upstream LEAN 更新可以被 product fork 吸收，而不是和 Pantheon governance code 混在一起。
```

---

## 3. 現況 repo 角色

根據最新校正，現況更可能是：

```text
[Console]
front-ai-trading-system

[Control / Governance / Registry]
pantheon

[Actual Execution Product Fork]
Lean

[Inactive / Historical / Ambiguous Execution Candidate]
lean-platform
```

這意味著原來的 repo mapping 發生了偏移：

```text
Execution Substrate: lean-platform → Lean
```

這不是小命名問題，而是 **repo ownership drift**。

---

## 4. Repo-by-repo 新角色判斷

## 4.1 front-ai-trading-system

### 4.1.1 目前角色

`front-ai-trading-system` 的 README 明確指出：

```text
Pantheon owns the BFF and all /api/* contracts.
This repo owns pages, components, UX states, and the BFF client wiring.
```

因此它的正確定位是：

```text
Console / Workbench / UI composition
```

不是：

```text
source of truth
registry store
execution controller
broker connector
```

### 4.1.2 正確責任

應承接：

```text
Operator Console
Persona Workbench
Research Workbench
Knowledge Workbench
Trainer Workbench
Consultation Workbench
Governance Workbench
Evolution Workbench
BFF client
SSE client
view model rendering
operator command UI shell
```

### 4.1.3 不應承接

```text
canonical registry writes
broker credentials
runtime launch
capital pool truth
telemetry truth
LLM search truth
```

### 4.1.4 當前風險

`bffClient.ts` 顯示前端有 dev BFF pin、preview mock fallback、typed surfaces。這很適合開發 UI，但也可能產生：

```text
- UI 看起來完整，後端實際未接。
- Codex 看到 surface 以為功能完成。
- preview fallback 掩蓋 BFF / runtime 缺口。
```

### 4.1.5 需要補的明確標籤

每個 UI surface 建議標示：

```text
real-backed
preview-fallback
mock-only
contract-only
not-implemented
```

---

## 4.2 pantheon

### 4.2.1 目前角色

`pantheon` 是目前最接近 target control core 的 repo。

它應被正式定位為：

```text
Governance / Registry / BFF / Data Plane / Telemetry Core
```

### 4.2.2 正確責任

應承接：

```text
StrategySpec registry
Experiment registry
Artifact registry
ApprovalDecision
DeploymentPlan
RuntimeBinding canonical store
CapitalPool
RiskPolicy
PersonaCapitalBinding
TelemetryEvent ingest
ReconciliationRecord
IncidentCase
Postmortem
EvolutionDecision
OpenClaw governance adapter
Search Gateway / Evidence Store
```

### 4.2.3 不應承接

```text
actual broker order execution
LEAN engine internals
runtime process lifecycle internals
broker credentials inside BFF
front-end local truth
```

### 4.2.4 pantheon 對 Lean 的必要 contract

若 Lean 是實際 execution substrate，pantheon 必須產出 Lean 可消費的：

```text
DeploymentPlan
RuntimeBinding
artifact metadata
runtime config
broker account reference
risk policy projection
rollback parent
kill-switch instruction
```

並接收 Lean 送回的：

```text
TelemetryEvent
RuntimeHeartbeat
OrderEvent
FillEvent
PositionSnapshot
BrokerHealthEvent
RuntimeStatusChange
KillSwitchAck
```

### 4.2.5 當前主要風險

```text
- BFF contract 與 BFF implementation 邊界可能不一致。
- artifact_state / deployment_stage 是否完全分離仍需驗證。
- runtime handoff target 可能仍未正式指向 Lean。
- telemetry schema 有，但 Lean producer 未證明。
- evolution decision 有，但 action executor 是否能驅動 Lean 未證明。
```

---

## 4.3 Lean

### 4.3.1 目前角色重判定

在最新校正後，`Lean` 不應再被簡單視為 upstream mirror。它應暫時被定義為：

```text
Actual modified execution substrate / product fork candidate
```

這個定位必須透過 ADR 正式化。

### 4.3.2 如果 Lean 是正式 execution substrate，必須承接

```text
Runtime Manager adapter
DeploymentPlan consumer
RuntimeBinding injection
Artifact metadata loader
object-store projection reader
Paper runtime
Canary runtime
Live runtime
Broker / exchange integration
Subaccount mapping
Pause / liquidate / replace actions
Telemetry exporter
Heartbeat exporter
Kill-switch bridge
```

### 4.3.3 Lean 目前可確認的 generic engine 特性

Lean README 顯示它是 modular algorithmic trading engine，具備 backtesting / live trading / plugin architecture / brokerage / datafeed 相關能力。`Launcher/Program.cs` 顯示它透過 job queue / config 啟動 engine，建立 AlgorithmManager，並進入 Engine.Run。

這代表 Lean 具有 execution substrate 的 generic 基礎，但不等於它已接上 Pantheon canonical contract。

### 4.3.4 需要查證的 Pantheon-specific features

必須搜尋與驗證：

```text
Pantheon
DeploymentPlan
RuntimeBinding
TelemetryEvent
capital_pool_id
artifact_id
artifact_version
deployment_stage
persona_capital_binding_id
plan_id
rollback_parent
kill_switch
```

若沒有，則 Lean 雖然能跑 LEAN engine，但還不是 Pantheon-governed execution substrate。

### 4.3.5 Lean product fork 風險

若直接在 Lean repo 上改 Pantheon-specific code，風險包括：

```text
- upstream QuantConnect Lean sync 難度上升
- Pantheon-specific runtime governance 混入 generic engine
- patch review 範圍過大
- Codex 難區分 upstream code 與 product code
- runtime telemetry / broker secret / capital pool 邊界可能與 engine lifecycle 交纏
- 若日後要切回 lean-platform 或其他 runtime，migration 成本升高
```

### 4.3.6 建議 namespace / adapter 策略

如果決定 Lean 是正式 product fork，建議不要把 Pantheon-specific code 散落到 engine 各處，而應集中：

```text
Pantheon/
  Bootstrap/
  RuntimeBinding/
  ArtifactLoading/
  Telemetry/
  Governance/
  KillSwitch/
  BrokerEntitlement/
```

並透過 adapter 接入 LEAN internals。

---

## 4.4 lean-platform

### 4.4.1 原角色

原藍圖將 `lean-platform` 定位為：

```text
Execution Substrate
```

承接：

```text
per-pool paper / canary / live runtime
orders / fills / positions / runtime health / broker events
```

### 4.4.2 現況重判定

根據使用者校正：

```text
lean-platform 幾乎沒有動。
```

因此它目前不能被視為實際 product execution substrate。

它應被暫列為：

```text
inactive / historical / pending disposition
```

### 4.4.3 可能處置

有四個選項：

| 選項 | 說明 | 優點 | 風險 |
|---|---|---|---|
| Retire / Archive | 不再作為產品 repo | 消除混淆 | 若裡面有有用 patch，會遺失 |
| Merge into Lean | 把有用內容合併到 Lean | 統一 execution substrate | merge 成本與衝突 |
| Re-activate as product fork | 把 Lean 修改遷回 lean-platform | 符合原藍圖 | migration 成本高 |
| Rename / consolidate | 改名成唯一 execution-platform | 長期清楚 | 需要 repo / CI / docs 全面更新 |

### 4.4.4 必須回答的問題

```text
lean-platform 是否在任何 deployment manifest 中？
lean-platform 是否在 CI 中？
lean-platform 是否有 production secrets？
lean-platform 是否有比 Lean 新的 broker / data vendor code？
lean-platform 是否只是舊 fork？
```

---

## 5. Responsibility remapping table

| Responsibility | 原藍圖 repo | 現況推定 repo | 狀態 | 風險 |
|---|---|---|---|---|
| Console / Workbench | front-ai-trading-system | front-ai-trading-system | 對齊 | 低 |
| BFF API Contract | pantheon | pantheon | 對齊 | 中：read/write 邊界需驗證 |
| Registry Core | pantheon | pantheon | 對齊 | 中：authoritative persistence 需驗證 |
| Governance / Promotion | pantheon | pantheon | 對齊 | 中：runtime handoff 需驗證 |
| Capital Pool Governance | pantheon | pantheon | 對齊 | 中高：execution linkage 需補 |
| Execution Runtime | lean-platform | Lean | 偏移 | 高 |
| Artifact Loader into LEAN | lean-platform / execution plane | Lean + pantheon | 偏移 / 未證明 | 高 |
| RuntimeBinding Store | pantheon / execution plane | pantheon + Lean consumer | 未證明 | 高 |
| Telemetry Producer | lean-platform | Lean | 偏移 / 未證明 | 高 |
| Telemetry Ingest | pantheon | pantheon | 對齊 | 中：producer linkage 需補 |
| Reconciliation | pantheon | pantheon | 未完整 | 高 |
| Evolution Action Executor | pantheon + execution repo | pantheon + Lean | 未證明 | 高 |
| OpenClaw Governance | pantheon | pantheon | 對齊 | 中：Search Gateway 需補 |
| External Data Gateway | pantheon / source plane | pantheon + maybe Lean toolboxes | 未完整 | 中高 |

---

## 6. 結構性偏移分析

### 6.1 偏移一：Execution substrate 從 lean-platform 轉移到 Lean

這是目前最高優先級的架構差異。

如果不處理，會導致：

```text
- Codex 按藍圖改 lean-platform，但實際 runtime 在 Lean。
- 人類工程師在 Lean 改 runtime，但文件仍要求 lean-platform。
- pantheon DeploymentPlan target 不明。
- telemetry exporter 不知道該寫在哪。
- CI / tests 可能覆蓋錯 repo。
```

### 6.2 偏移二：Lean upstream 與 product fork 角色混淆

Lean README 仍呈現 upstream LEAN engine 屬性。若它同時是 product fork，需要補：

```text
Product Fork README
Pantheon integration README
upstream sync policy
patch inventory
namespace boundary
```

### 6.3 偏移三：Execution feed 與 canonical data gateway 混淆

LEAN / Lean 內可能存在 market data / brokerage / news / data queue handler。但這不等於 Pantheon Source Ingestion Plane 完成。

必須分清楚：

```text
Lean execution feed = runtime 消費資料
Pantheon Data Gateway = canonical research / evidence / PIT truth
```

### 6.4 偏移四：runtime event 與 telemetry evidence contract 尚未證明一致

Pantheon telemetry schema 要求 event 必須帶 runtime binding 與 deployment stage evidence。Lean 的 generic result/order events 若沒有這些欄位，則不能直接視為 Pantheon telemetry。

---

## 7. 決策選項分析

## 7.1 Option A：正式把 Lean 定為 product execution substrate

### 做法

```text
更新藍圖 repo mapping。
將 Lean 標記為 Pantheon LEAN product fork。
建立 Pantheon namespace / adapters。
lean-platform archive 或 deprecated。
```

### 優點

```text
符合現實開發路徑。
不用遷移已修改內容。
Codex target 明確。
```

### 缺點

```text
upstream sync 風險較高。
需要嚴格隔離 Pantheon-specific code。
需要重寫原藍圖 repo mapping。
```

### 必做

```text
ADR-EXEC-001
Pantheon runtime adapter namespace
DeploymentPlan consumer
Telemetry exporter
upstream diff CI
```

## 7.2 Option B：恢復 lean-platform 為 product execution substrate

### 做法

```text
把 Lean 中已修改內容遷移到 lean-platform。
Lean 回到 upstream mirror。
```

### 優點

```text
符合原藍圖。
upstream mirror 與 product fork 分離清楚。
```

### 缺點

```text
migration 成本高。
可能需要重新整理 VS Code workspace / CI / deployment。
若 Lean 已累積大量修改，風險大。
```

## 7.3 Option C：合併 / rename 成唯一 execution repo

### 做法

```text
選定唯一 repo 名，例如 execution-platform 或 pantheon-lean-runtime。
合併 Lean / lean-platform 有用內容。
更新所有 docs / CI / Codex prompts。
```

### 優點

```text
長期最清楚。
消除雙 repo ambiguity。
```

### 缺點

```text
短期治理與 migration 成本最高。
```

## 7.4 Option D：Lean 作 engine fork，Pantheon adapter 抽 sidecar

### 做法

```text
Lean 盡量不塞 Pantheon governance code。
新增 runtime sidecar / launcher adapter 消費 DeploymentPlan、產 TelemetryEvent。
```

### 優點

```text
降低 upstream pollution。
維持 Lean 可升級性。
Pantheon-specific code 集中。
```

### 缺點

```text
sidecar complexity 增加。
runtime lifecycle 整合要設計好。
```

---

## 8. 建議決策

若現實是「已經在 Lean 改很多」，最務實的短期建議是：

```text
採 Option A + D 的混合：
正式承認 Lean 是目前 execution substrate，
但把 Pantheon-specific integration 盡量集中在 Lean 內的 Pantheon adapter namespace 或外部 sidecar，
不要散落改 LEAN engine internals。
```

同時立刻做：

```text
1. ADR-EXEC-001: Lean is current product execution substrate.
2. ADR-EXEC-002: lean-platform disposition.
3. ADR-EXEC-003: Pantheon runtime contract.
4. ADR-EXEC-004: Upstream sync and patch isolation.
```

---

## 9. 近期 Codex task target 重排

### 9.1 不應再發給 lean-platform 的 P0 task

除非 ADR 決定重新啟用 lean-platform，否則以下 task 不應發給 lean-platform：

```text
RuntimeBinding consumer
DeploymentPlan consumer
Telemetry exporter
Artifact metadata loader
Kill-switch bridge
Paper/canary/live segregation
```

### 9.2 應發給 Lean 的 P0 task

```text
TP-LEAN-001 Pantheon launch manifest consumer
TP-LEAN-002 RuntimeBinding injection into LEAN job context
TP-LEAN-003 Artifact metadata validator
TP-LEAN-004 Pantheon TelemetryEvent exporter
TP-LEAN-005 Broker credential / capital pool boundary adapter
TP-LEAN-006 Paper / canary / live environment segregation
TP-LEAN-007 Kill-switch bridge
TP-LEAN-008 Upstream diff inventory and CI guard
```

### 9.3 應發給 pantheon 的 P0 task

```text
TP-PAN-001 DeploymentPlan → runtime manifest materializer
TP-PAN-002 RuntimeBinding canonical store
TP-PAN-003 Telemetry ingest validates Lean event envelope
TP-PAN-004 Reconciliation projection writer
TP-PAN-005 Incident trigger from runtime telemetry
TP-PAN-006 Evolution action dispatcher to runtime boundary
```

### 9.4 應發給 front-ai-trading-system 的 P0 task

```text
TP-FE-001 show runtime substrate and binding trace
TP-FE-002 remove silent mock ambiguity from operator surfaces
TP-FE-003 DeploymentPlan → RuntimeBinding → Lean runtime drilldown
TP-FE-004 telemetry / reconciliation UI with source-of-truth badges
```

---

## 10. Required ADR template

建議新增：

```markdown
# ADR-EXEC-001 — Official Execution Substrate Repo

## Status
Proposed / Accepted / Superseded

## Context
Pantheon blueprint originally mapped Execution Substrate to lean-platform.
Actual VS Code development has occurred in Lean.

## Decision
Choose one:
- Lean is official product execution substrate.
- lean-platform is official product execution substrate.
- Merge / rename into new execution repo.

## Consequences
- Codex task target
- CI target
- deployment manifest target
- upstream sync policy
- runtime telemetry ownership

## Required follow-up
- Update blueprint repo mapping
- Update BFF / deployment docs
- Update runtime adapter tasks
- Archive / migrate unused repo
```

---

## 11. 本章結論

本章的核心判斷是：

> **目前 repo 角色最需要先收斂的是 Execution Substrate ownership。**

如果 `Lean` 是事實上的 execution substrate，就必須正式化；如果原藍圖仍要 `lean-platform`，就必須把 Lean 修改遷回去。

在決策前，所有 execution-related task 都有 patch 錯 repo 的風險。

因此 P0 不是新增功能，而是：

```text
1. 決定 Lean vs lean-platform。
2. 更新 repo mapping。
3. 建立 Pantheon ↔ Lean runtime contract。
4. 用 paper-only minimum loop 驗證。
```

---

## 附錄：本章主要依據來源

- `pantheon/Pantheon_總索引版系統分析文件.md`
- `pantheon/TARGET_ARCHITECTURE.md`
- `front-ai-trading-system/README.md`
- `front-ai-trading-system/src/lib/bffClient.ts`
- `Lean/readme.md`
- `Lean/Launcher/Program.cs`
- `lean-platform/readme.md`
