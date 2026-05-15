# SA-04 — 現行 Repo 盤點方法

> **2026-05-03 Canonical correction**: `pantheon/lean` submodule backed by `ajoe734/pantheon-lean.git` is the official execution substrate. Any older `lean-platform` repo-mapping drift language in this SA note is superseded; do not treat `lean-platform` as an active gap or task target.


**文件編號**：SA-04
**文件類型**：System Analysis / Audit Methodology
**範圍**：定義如何判斷 repo 與藍圖差異
**版本**：v0.1 Draft

---

## 1. 本章目的

本章定義 repo 盤點方法，避免後續分析落入兩種錯誤：

```text
錯誤 1：看到文件 / UI / schema 就判斷已完成。
錯誤 2：看到某 repo 沒有明顯命名，就判斷完全沒有實作。
```

Pantheon 是多 plane、多 repo、多 state machine、多 event loop 的 operating system。它的完成度不能只靠「檔案是否存在」判斷，必須看：

```text
- object 是否有 owner
- schema 是否有 implementation
- implementation 是否有 producer / consumer
- state transition 是否被 enforce
- event 是否從 runtime 進到 telemetry
- governance 是否真的能阻止非法行為
- e2e loop 是否可跑通
```

---

## 2. 盤點範圍

本輪 SA 盤點的 repo 包含：

```text
ajoe734/front-ai-trading-system
ajoe734/pantheon
ajoe734/Lean
ajoe734/lean-platform
```

在最新使用者校正下，實際產品邊界暫定為：

```text
front-ai-trading-system
pantheon
Lean
```

`lean-platform` 暫列：

```text
待釐清 / 未實際採用 / historical branch / potential retired substrate
```

---

## 3. 證據分級

### 3.1 A 級：Executable Implementation Evidence

代表有真實行為。

例：

```text
service implementation
runtime adapter
state machine implementation
repository / persistence layer
event producer
consumer / handler
integration test
e2e test
CI pipeline
```

判斷：可以支撐 Implemented 或 Partially Implemented。

### 3.2 B 級：Contract Evidence

代表規格存在，但未必落地。

例：

```text
JSON schema
OpenAPI contract
service contract
Event contract
RBAC matrix
state transition spec
```

判斷：只能支撐 Contract-only 或 Partially Implemented。

### 3.3 C 級：Surface Evidence

代表 UI / client / read surface 存在。

例：

```text
React page
BFF client method
view model type
mock data
storybook / screen spec
```

判斷：只能支撐 Surface-only，除非找到後端 implementation。

### 3.4 D 級：Documentation Evidence

代表設計意圖。

例：

```text
README
architecture doc
migration note
handoff doc
Codex readout
planning doc
```

判斷：只能支撐 Documented-only。

### 3.5 E 級：Naming / Directory Evidence

代表弱線索。

例：

```text
folder name
file name
module name
branch name
```

判斷：不能單獨用來判定已實作。

---

## 4. 狀態標記規則

### 4.1 Implemented

需同時滿足：

```text
- 有 code
- 有 canonical input/output
- 有 state transition 或 runtime effect
- 有 persistence 或 event emission
- 有測試或可執行驗證方式
```

### 4.2 Partially Implemented

符合其中部分，但缺少 producer / consumer / tests / runtime effect。

例：

```text
pantheon 有 TelemetryEvent schema，
但 Lean 是否產生該 schema 的事件尚未證明。
```

### 4.3 Contract-only

只有 schema / contract。

例：

```text
BFF contract 定義 endpoint，
但 main.py 或 actual service 未必實作。
```

### 4.4 Surface-only

只有 UI / client。

例：

```text
front 有 runtime state board，
但 BFF / telemetry / Lean event source 尚未接上。
```

### 4.5 Documented-only

只有 README / design doc。

### 4.6 Absent

找不到對應文件、schema、code、UI。

### 4.7 Misplaced

功能存在，但不在正確 plane。

例：

```text
news connector 在 Lean toolbox，卻未進 Pantheon Source Registry / Evidence Store。
```

### 4.8 Conflicting

文件 / repo / code 互相矛盾。

例：

```text
藍圖 repo mapping：lean-platform 是 Execution Substrate。
使用者現況校正：實際修改的是 Lean。
```

### 4.9 Unverified

需要部署 manifest、runtime logs、CI 或實機才能確認。

例：

```text
production launcher 實際指向 Lean 或 lean-platform。
```

---

## 5. Gap 類型分類

### 5.1 Structural Gap

plane / service / bounded context 沒有落地。

例如：

```text
沒有 Search Gateway。
沒有 Consultation backend。
沒有 Reconciliation writer。
```

### 5.2 Contract Gap

schema / API / event contract 不存在或互相不一致。

例如：

```text
DeploymentPlan 有定義，但 Lean launch manifest 沒定義。
```

### 5.3 Behavioral Gap

物件存在，但流程跑不起來。

例如：

```text
ApprovalDecision 存在，
但不會實際產生 RuntimeBinding 或啟動 Lean runtime。
```

### 5.4 Governance Gap

權限或風控邊界不完整。

例如：

```text
persona 可觸發 deploy，
但沒有 PersonaCapitalBinding / RiskPolicy 檢查。
```

### 5.5 Repo Ownership Gap

責任所在 repo 與藍圖不一致。

例如：

```text
Execution substrate 在原藍圖是 lean-platform，
但實際修改在 Lean。
```

### 5.6 Runtime Integration Gap

execution substrate 與 Pantheon control plane 沒接通。

例如：

```text
Lean 不知道 runtime_binding_id。
```

### 5.7 Data / Source Gap

外部資料源、evidence、search、point-in-time 控制未完整。

### 5.8 Testing / Verification Gap

沒有 e2e / contract / state machine tests 證明。

### 5.9 Operational Readiness Gap

能開發，但不能營運。

例如：

```text
沒有 runbook、safe mode、rollback、incident workflow、monitoring。
```

---

## 6. Repo-by-repo 盤點路徑

### 6.1 front-ai-trading-system

盤點重點：

```text
README role statement
App routes
pages inventory
bffClient.ts
mockBffData.ts
previewMockFallback
operator pages
persona pages
research pages
knowledge pages
governance pages
consultation pages
lineage pages
evolution pages
SSE / realtime client
```

判斷問題：

```text
哪些 surface 是 real BFF backed？
哪些是 preview fallback？
哪些是 pure mock？
哪些 API route 後端不存在？
哪些 UI 暗示 Lean / lean-platform runtime？
```

### 6.2 pantheon

盤點重點：

```text
TARGET_ARCHITECTURE.md
Pantheon_總索引版系統分析文件.md
BFF_API_CONTRACT.md
main.py
read_store.py
command_executor.py
data-plane models
registry / promotion
approval decision
deployment plan
capital pool
persona-capital binding
artifact loader
telemetry_event.schema.json
telemetry ingest
lineage read
incident / evolution docs and services
OpenClaw governance
```

判斷問題：

```text
哪些 canonical objects 有 owner？
哪些 schema 有 implementation？
BFF 是否只 read-oriented？
DeploymentPlan 是否能 handoff 到 Lean？
Telemetry ingest 是否接收 Lean events？
```

### 6.3 Lean

盤點重點：

```text
readme.md
Launcher/Program.cs
Engine/AlgorithmManager.cs
Engine/Setup/*
Brokerages/*
Common/Packets/*
Common/Orders/*
Engine/Results/*
config.json / config templates
job packet handling
result handler
transaction handler
runtime status handling
```

新增 Pantheon-specific 檢查：

```text
Pantheon namespace
DeploymentPlan consumer
RuntimeBinding consumer
artifact metadata loader
object-store projection reader
TelemetryEvent exporter
capital_pool_id propagation
persona_capital_binding_id propagation
kill-switch bridge
paper / canary / live segregation
```

### 6.4 lean-platform

盤點重點：

```text
是否仍有 active branch？
是否仍有 Pantheon-specific patches？
是否仍在 CI / deployment manifest？
是否與 Lean 分歧？
是否應 archive / merge / retire？
```

判斷問題：

```text
它是 product repo、歷史 repo、還是錯誤藍圖落點？
```

---

## 7. Operating loop 驗證方法

### 7.1 最小閉環驗證

必須逐步證明：

```text
StrategySpec
→ ExperimentRun
→ CandidateArtifact
→ ApprovalDecision
→ DeploymentPlan
→ RuntimeBinding
→ Lean Paper Runtime
→ TelemetryEvent
→ ReconciliationRecord
→ IncidentCase / DriftReport
→ EvolutionDecision
```

### 7.2 每一步的證據要求

| Step | 需要證據 |
|---|---|
| StrategySpec | schema + registry write path |
| ExperimentRun | task/run service + dataset_version binding |
| CandidateArtifact | artifact registration + lineage |
| ApprovalDecision | review gate + decision store |
| DeploymentPlan | planner + state transition |
| RuntimeBinding | binding store + runtime target |
| Lean Paper Runtime | Lean launch with binding metadata |
| TelemetryEvent | Lean producer + pantheon ingest |
| ReconciliationRecord | writer + expected/actual comparison |
| IncidentCase | alert trigger + incident service |
| EvolutionDecision | decision service + action dispatcher |

---

## 8. Runtime integration 驗證方法

### 8.1 Pantheon → Lean

檢查：

```text
Does pantheon produce a Lean-readable launch manifest?
Does Lean consume DeploymentPlan or RuntimeBinding?
Does artifact metadata include checksum, deployment_stage, rollback_parent?
Does Lean reject unapproved artifact?
```

### 8.2 Lean → Pantheon

檢查：

```text
Does Lean emit canonical TelemetryEvent?
Does event include runtime_binding_id?
Does event include deployment_plan_id?
Does event include capital_pool_id?
Does event include artifact_id/version?
Does pantheon ingest and persist it?
```

### 8.3 Lean runtime → Broker boundary

檢查：

```text
Who owns broker credential?
Does Lean receive credentials directly?
Does Pantheon BrokerAccount Registry gate it?
Are paper/canary/live credentials isolated?
```

---

## 9. Search / External data 驗證方法

### 9.1 Source connector 判定

每個外部來源都要回答：

```text
connector exists?
normalizer exists?
SourceRecord exists?
EvidenceBundle exists?
available_time exists?
license / entitlement exists?
searchable by ACL-aware gateway?
```

### 9.2 Execution data vs research data 分離

需要特別判斷：

```text
Lean 裡的 market data / news data connector 是否只是 execution feed？
是否已回寫 Pantheon canonical source registry？
```

若沒有，就不能把它視為 Pantheon Data Gateway 完成。

---

## 10. Codex 盤點提示規格

不要問 Codex：

```text
這個 repo 和藍圖差異大嗎？
```

要問：

```text
請檢查以下每一步是否有 producer、consumer、schema、state transition、persistence、test：
CandidateArtifact → ApprovalDecision → DeploymentPlan → RuntimeBinding → Lean runtime → TelemetryEvent → ReconciliationRecord → EvolutionDecision。
任何只有 README、mock、UI、schema 但沒有行為閉環的項目，標為 gap。
```

對 Lean 要問：

```text
請搜尋 Lean repo 是否存在 Pantheon-specific runtime integration：
DeploymentPlan, RuntimeBinding, TelemetryEvent, capital_pool_id, artifact_id, deployment_stage, persona_capital_binding_id, Pantheon namespace。
列出檔案、呼叫鏈、測試；若無，標為 runtime integration gap。
```

---

## 11. 報告輸出格式

每個 gap 應用以下格式：

```text
Gap ID:
Plane:
Repo:
Blueprint requirement:
Current evidence:
Status:
Gap type:
Severity:
Impact:
Required decision:
Required implementation:
Acceptance test:
Confidence:
```

範例：

```text
Gap ID: EXE-001
Plane: Execution
Repo: Lean / pantheon
Blueprint requirement: DeploymentPlan must create RuntimeBinding and start LEAN runtime.
Current evidence: Pantheon has DeploymentPlan-related contract; Lean launcher currently appears generic LEAN launcher.
Status: Unverified / likely Runtime Integration Gap
Severity: High
Required implementation: Lean launch manifest consumer + RuntimeBinding injection.
Acceptance test: A paper DeploymentPlan starts Lean with runtime_binding_id and emits heartbeat telemetry.
```

---

## 12. 本章結論

這個盤點方法的核心是：

```text
以行為閉環與治理邊界作為完成標準，
而不是以檔案名稱或 UI 頁面作為完成標準。
```

後續 SA 章節必須嚴格使用這套判斷規則，尤其針對目前最重要的 ambiguity：

```text
Lean vs lean-platform execution substrate ownership。
```

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
