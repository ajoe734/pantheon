# SA-01 — 執行摘要：Pantheon Blueprint-to-Implementation Gap Assessment

> **2026-05-03 Canonical correction**: `pantheon/lean` submodule backed by `ajoe734/pantheon-lean.git` is the official execution substrate. Any older `lean-platform` repo-mapping drift language in this SA note is superseded; do not treat `lean-platform` as an active gap or task target.


**文件編號**：SA-01
**文件類型**：System Analysis / Gap Assessment
**範圍**：Pantheon 設計藍圖 vs 現行 repository 實作狀態
**版本**：v0.1 Draft
**狀態**：供技術審查、repo ownership 決策、Codex task packet 拆分使用

---

## 1. 本章目的

本章不是功能清單，也不是一般 code review 摘要，而是用 Pantheon 原始藍圖作為 target system，對目前 repo 實作狀態做 SA 層級判斷。它要回答：

1. 現在系統整體接近藍圖到什麼程度？
2. 哪些是真實 implementation，哪些只是 UI / contract / schema / README？
3. 最大差異點是功能缺少，還是系統邊界 / repo ownership 偏移？
4. 為什麼 Codex 可能判斷「差異不大」，但 operating loop 仍不能視為閉合？
5. 下一輪開發應先補什麼，以免繼續往錯誤 repo 或錯誤 plane 施工？

---

## 2. 最新結論總覽

目前最重要的結論：

> **Pantheon 的 UI shell、BFF contract、部分 registry / telemetry / governance schema 已成形；但真正的 operating system 閉環尚未完整落地，且 Execution Plane 的實際修改位置從原藍圖的 `lean-platform` 偏移到 `Lean`，形成 repo ownership drift。**

更精準地說：

```text
藍圖語意、前端表面、BFF/registry/telemetry 文件與部分 schema 已接近；
但 promotion → runtime → telemetry → reconciliation → evolution 的跨 repo 行為閉環，
仍需要正式 runtime contract、Lean adapter、canonical telemetry exporter、reconciliation writer 與 e2e tests 來證明。
```

此處的重點不是 Lean 能不能跑 LEAN engine，而是 Lean 是否已被正式接成 Pantheon-governed execution substrate。

---

## 3. 最新 repo 判斷

### 3.1 先前理解

依原藍圖 repo 落點，可推定：

```text
front-ai-trading-system  → Pantheon Console
pantheon                 → Governance + Registry Core
lean-platform            → Execution Substrate
Lean                     → OSS upstream / reference
```

### 3.2 最新校正

使用者校正：

```text
實際上 VS Code 裡面一直修改的是 Lean repo；lean-platform 幾乎沒有動。
```

因此本 SA 報告採用新的現況映射：

| Repo | 現況判斷 | SA 解讀 |
|---|---|---|
| `front-ai-trading-system` | 有明確 Pantheon UI / BFF client 角色 | Console / Workbench repo，非真相來源 |
| `pantheon` | 有 target architecture、BFF contract、data / registry / telemetry / governance 文件與部分 service | Governance / Registry / BFF / Telemetry core |
| `Lean` | 實際被修改與使用的 LEAN execution repo | 目前事實上的 execution substrate / product fork |
| `lean-platform` | 原藍圖指定 execution substrate，但使用者表示幾乎未動 | 待釐清 / 歷史分支 / 未實際採用 execution repo |

這改變分析重心：

```text
不能再問：lean-platform 是否接上 Pantheon？
而要問：Lean 是否已經正式承接 Pantheon execution contract？
```

---

## 4. 成熟度總評

以下是以「能否支撐 operating system 閉環」為標準的成熟度判斷。

| 系統面向 | 成熟度 | 判斷摘要 | 主要問題 |
|---|---:|---|---|
| Console / Workbench | 中高 | 前端工作台與 typed BFF client 很完整 | preview fallback / mock risk 可能遮蔽後端缺口 |
| BFF Contract | 中高 | BFF contract 已定義 read-oriented surfaces、RBAC、staleness、SSE | 實作與 contract 是否完全一致需逐 endpoint 驗證 |
| Registry / Lineage | 中 | 核心物件與 registry 方向明確 | authoritative store、跨 plane write path、lineage edge 完整性需補強 |
| Research Factory | 中低 | ingest / adapter / research surfaces 有基礎 | Experiment Orchestrator、backend selection、artifact packager 尚未證明完整 |
| Governance / Promotion | 中 | Approval / DeploymentPlan / promotion 概念已明確 | artifact_state / deployment_stage 是否完全分離仍是關鍵 |
| Capital Pool Governance | 中低 | 藍圖要求明確 | 實作是否能強制 persona-capital-runtime 邊界需驗證 |
| Execution Substrate | 中低 | `pantheon/lean` / `pantheon-lean` 已確認為正式 execution bridge | RuntimeBinding / DeploymentPlan consumer 與 production launcher maturity 尚未證明 |
| Telemetry | 中 | TelemetryEvent schema 很明確 | Lean 是否產出該 canonical event，pantheon 是否完成 projection writer，仍需補 |
| Reconciliation / Drift | 低 | 藍圖要求明確 | 需要正式 reconciliation writer / drift detector / e2e tests |
| Incident / Postmortem / Evolution | 低到中 | 文件與 schema 方向明確 | action executor 與 upstream/downstream linkage 仍不足 |
| External Data / Search | 中低 | 藍圖已預留 source / evidence / search | news/social/alpha DB/search gateway 尚未形成統一 governed plane |

---

## 5. 最大差異點總表

| 差異類型 | 差異點 | 影響 | 嚴重度 |
|---|---|---|---:|
| Execution bridge canonicalized | `pantheon/lean` / `pantheon-lean` 已作為正式 bridge；`lean-platform` 不再列為 active gap | 後續工作不再盤點 repo mapping，而是追 runtime maturity | 低 |
| Runtime contract gap | 尚未完整證明 `pantheon-lean` 消費 `DeploymentPlan` / `RuntimeBinding` / artifact metadata | promotion 到 execution 無法被視為閉環 | 高 |
| Telemetry producer gap | 尚未完整證明 `pantheon-lean` 產生 Pantheon canonical `TelemetryEvent` | telemetry / reconciliation / incident / evolution 無法歸因 | 高 |
| artifact/deployment semantic gap | 藍圖要求 artifact_state 與 deployment_stage 分離 | 若混用，rollback / lineage / promotion 會混亂 | 高 |
| BFF contract drift | BFF contract 是 read-oriented，但前端與實作可能有 command-oriented paths | BFF 可能被誤用為 control truth source | 中高 |
| UI shell illusion | 前端頁面完整，但不代表後端 state machine / runtime 已接 | Codex 容易判斷差異不大 | 中高 |
| Capital pool boundary gap | 需要 capital pool、risk policy、broker account、persona-capital binding | live 隔離與風控否決權無法保證 | 高 |
| Search / Evidence gap | OpenClaw 需要 governed search，不應直接任意外連 | LLM 可能越權讀資料或產生無 evidence 的策略 | 中高 |
| Reconciliation gap | backtest / paper / canary / live 對帳尚未證明 | operating system 的學習與演化核心無法成立 | 高 |
| Evolution action gap | evolution decision 若不能驅動 freeze / rollback / retrain | evolution 只是記錄，不是閉環 | 中高 |

---

## 6. 為什麼 Codex 會覺得差異不大

Codex 或類似 coding agent 可能判斷「差異不大」，主要是因為它通常更擅長靜態結構對齊，而不是跨 plane 行為驗證。

它可能看見：

```text
- repo 名稱與藍圖類似
- front 有很多 workbench page
- pantheon 有 TARGET_ARCHITECTURE.md
- pantheon 有 BFF contract
- pantheon 有 telemetry schema
- pantheon 有 promotion / incident / lineage 文件
- Lean / lean-platform 都是 LEAN engine fork
```

於是得出：「藍圖元素大多存在」。

但 SA 層級要檢查的是：

```text
CandidateArtifact 是否真的生成 ApprovalDecision？
ApprovalDecision 是否真的生成 DeploymentPlan？
DeploymentPlan 是否真的生成 RuntimeBinding？
RuntimeBinding 是否真的啟動 Lean runtime？
Lean runtime 是否真的帶著 binding_id / artifact_id / capital_pool_id 送回 telemetry？
Telemetry 是否真的進 ReconciliationRecord / IncidentCase / EvolutionDecision？
EvolutionDecision 是否真的能 freeze / rollback / retrain / retire？
```

> **Codex 可能在看「檔案存在」；SA 必須看「閉環是否可執行、可回放、可治理」。**

---

## 7. 最關鍵 SA 判斷

### 7.1 `Lean` 必須被正式定義

目前最大風險不是 Lean 能不能跑交易，而是：

```text
Lean 到底是 OSS mirror、product fork，還是 Pantheon 官方 execution substrate？
```

如果實際開發已經全部在 Lean，應正式更新 repo mapping：

```text
Lean → Execution Substrate / LEAN Product Fork
lean-platform → archived / deprecated / historical fork / pending merge
```

否則會造成：

```text
- Codex patch 錯 repo
- 文件與實作不一致
- CI 測錯 repo
- deployment manifest 指錯 runtime
- telemetry adapter 寫在錯的地方
- broker integration 無法治理
```

### 7.2 Pantheon ↔ Lean contract 是 P0

下一步最重要的不是再補更多 UI，也不是先接更多外部資料源，而是建立：

```text
Pantheon DeploymentPlan / RuntimeBinding / Artifact Metadata
→ Lean runtime bootstrap
→ Lean TelemetryEvent exporter
→ Pantheon telemetry ingest / reconciliation
```

這是第一條真正的 deployable operating loop。

### 7.3 External Data / Search 是 P1，但需設計進 canonical plane

新聞、社群、alpha DB、LLM search 都要補；但不應直接塞進 Lean 或 OpenClaw。

它們應走：

```text
Source Ingestion / Data Gateway
→ Source Registry
→ Evidence Store
→ Search Gateway
→ StrategySpec Seed Builder / Review Gate / Postmortem
```

OpenClaw 應只透過 governed search tool 查 evidence，不應直接持有 vendor credentials 或任意搜尋權限。

---

## 8. 建議 P0 工作

### P0-A：建立 Execution Substrate ADR

```text
ADR-EXEC-001: Official Execution Substrate Decision
```

必須回答：

```text
1. Lean 是否正式成為 Pantheon execution substrate？
2. lean-platform 是否 archive / merge / retire？
3. Pantheon-specific patch 放在 Lean 哪個 namespace？
4. 如何追 upstream QuantConnect Lean？
5. CI / deployment / Codex task packet 以哪個 repo 為唯一 target？
```

### P0-B：Pantheon → Lean Deployment Contract

產物：

```text
DeploymentPlan schema hardening
RuntimeBinding schema
Lean launch manifest
artifact metadata format
object-store projection contract
```

### P0-C：Lean → Pantheon Telemetry Contract

產物：

```text
Lean TelemetryEvent exporter
runtime heartbeat exporter
order / fill / position exporter
broker disconnect exporter
kill-switch event exporter
```

每個 event 至少應帶：

```text
runtime_binding_id
runtime_id
deployment_plan_id
artifact_id
capital_pool_id
persona_capital_binding_id
strategy_id
trace_id
```

### P0-D：Paper-only Minimum Operating Loop

先打通：

```text
CandidateArtifact
→ ApprovalDecision
→ DeploymentPlan
→ RuntimeBinding
→ Lean Paper Runtime
→ TelemetryEvent
→ Runtime State Projection
→ Basic Reconciliation
```

### P0-E：Front / BFF 去 mock-risk 化

前端可保留 preview fallback，但必須標示：

```text
real BFF backed
preview fallback
mock-only
contract-only
not implemented
```

---

## 9. 建議 P1 工作

P1 再處理完整 external source / search expansion：

```text
SourceRecord schema
EvidenceBundle schema
Search Gateway
ACL-aware vector retrieval
News connector
Filings connector
Macro connector
Market data connector
Social connector
External alpha DB connector
OpenClaw governed search tool
```

這些都重要，但前提是 P0 的 registry / runtime / telemetry 骨架先固定。

---

## 10. 本章結論

1. **現在最大的偏差不是某個功能少做，而是 execution repo ownership 和 operating loop proof 尚未收斂。**
2. **如果 Lean 確實是實際修改 repo，就不要再把 lean-platform 當成主 execution target；應立刻做 ADR，把 Lean 正式化為 product execution substrate，或把 Lean 的修改遷回 lean-platform。**
3. **短期目標應是 paper-only minimum operating loop，而不是一次補滿 live / canary / social / alpha DB / evolution。**
4. **Codex 後續任務必須以 runtime contract、state transition、event producer/consumer、acceptance test 為準，而不是只看目錄名稱。**

---

## 附錄：本章主要依據來源

- `pantheon/Pantheon_總索引版系統分析文件.md`
- `pantheon/TARGET_ARCHITECTURE.md`
- `pantheon/services/control-plane/bff/BFF_API_CONTRACT.md`
- `pantheon/services/telemetry/telemetry_event.schema.json`
- `front-ai-trading-system/README.md`
- `front-ai-trading-system/src/lib/bffClient.ts`
- `Lean/readme.md`
- `Lean/Launcher/Program.cs`
- `lean-platform/readme.md`
