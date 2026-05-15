# SA-02 — 分析前提與最新校正

> **2026-05-03 Canonical correction**: `pantheon/lean` submodule backed by `ajoe734/pantheon-lean.git` is the official execution substrate. Any older `lean-platform` repo-mapping drift language in this SA note is superseded; do not treat `lean-platform` as an active gap or task target.


**文件編號**：SA-02
**文件類型**：System Analysis / Assumption & Scope Definition
**範圍**：分析邊界、repo 實際定位、校正後的系統判讀前提
**版本**：v0.1 Draft

---

## 1. 本章目的

本章用來固定 SA 報告的「分析前提」。在系統分析裡，前提錯了，後面所有 gap analysis、task packet、migration plan 都可能落到錯的 repo 或錯的 plane。

本章特別處理一個關鍵校正：

```text
實際開發修改發生在 Lean repo，而非 lean-platform。
```

這個校正會影響：

```text
- Execution Plane 的 repo ownership
- RuntimeBinding consumer 應該寫在哪裡
- Telemetry exporter 應該寫在哪裡
- DeploymentPlan handoff 應該指向哪個 binary / launcher
- Codex 後續 task packet 應該 patch 哪個 repo
- lean-platform 是否仍應保留為 product repo
```

---

## 2. 已知藍圖前提

Pantheon 藍圖定義的是一個多 plane operating system。其制度核心是：

```text
研究共享、知識共享、會診共享；
資金池與 live 執行隔離。
```

Target architecture 進一步要求：

```text
research → approval → deployment planning → runtime binding → telemetry → evolution review
```

任何 live 行為變更都不能直接由 persona、worker、runtime 或短期 market feedback 變更，而必須經過 governed research、approval、deployment planning、runtime binding、telemetry 與 evolution review。

---

## 3. 原始 repo mapping 假設

根據 Pantheon 總索引版藍圖，原始 repo 落點可以整理為：

| Repo | 原始藍圖定位 | 承接內容 |
|---|---|---|
| `front-ai-trading-system` | Pantheon Console | Operator / Persona / Research / Governance / Evolution UI |
| `pantheon` | Governance + Registry Core | Registry、lineage、artifact governance、promotion、rollback、postmortem、evolution |
| `lean-platform` | Execution Substrate | per-pool paper / canary / live runtime、orders、fills、positions、runtime health、broker events |
| `Lean` | 未明確列為 Pantheon product repo | 可視為 upstream LEAN reference / OSS engine baseline |

這個 mapping 的架構意義是：

```text
Pantheon control plane 不直接依賴上游 OSS repo；
它依賴一個 product-owned execution substrate。
```

也就是說，原本比較合理的分層是：

```text
Pantheon Governance
→ Execution Adapter / Runtime Manager
→ lean-platform product fork / runtime substrate
→ LEAN engine internals
```

---

## 4. 最新使用者校正

使用者明確指出：

```text
實際上 VS Code 裡面一直修改的是 Lean 這個 repo；lean-platform 根本都沒有動。
```

因此，不能再沿用「lean-platform 是實際 execution substrate」作為現況判斷。

---

## 5. 本報告採用的新前提

本 SA 報告採用以下新前提：

| Repo | 本報告採用定位 | 理由 |
|---|---|---|
| `front-ai-trading-system` | Console / UI Workbench | README 明確說本 repo owns pages、components、UX states、BFF client wiring，Pantheon owns BFF/API contracts |
| `pantheon` | Governance / Registry / BFF / Telemetry Core | target architecture、BFF contract、data-plane、promotion、telemetry schema 等集中於此 |
| `Lean` | 實際 execution substrate / product fork candidate | 使用者校正：實際 VS Code 修改發生於此 repo |
| `lean-platform` | 待釐清 / 幾乎未動 / 歷史分支 | 使用者校正：此 repo 幾乎沒有動 |

新邊界變成：

```text
front-ai-trading-system
→ pantheon
→ Lean
```

而不是：

```text
front-ai-trading-system
→ pantheon
→ lean-platform
```

---

## 6. 這個校正造成的主要影響

### 6.1 Execution Plane 的 owner 改變

原藍圖中的 Execution Plane 包含：

```text
Runtime Manager
Artifact Loader
Runtime Binding Store
LEAN Paper Runtime
LEAN Canary Runtime
LEAN Live Runtime
Broker / Exchange / Subaccounts
Pause / Liquidate / Replace Actions
```

如果實際修改在 Lean，這些責任必須重新映射到 Lean：

```text
Lean 必須成為 RuntimeBinding consumer
Lean 必須能消費 DeploymentPlan / artifact metadata
Lean 必須輸出 canonical TelemetryEvent
Lean 必須承接 broker account boundary
Lean 必須支援 paper / canary / live segregation
```

### 6.2 文件與實作可能出現 drift

可能存在的漂移是：

```text
文檔：lean-platform 是 execution substrate
實作：Lean 才有修改
部署：未知
CI：未知
Codex task：可能依文件 patch lean-platform
```

這是一個高風險狀態，因為 coding agent 與人類 reviewer 可能會對不同 repo 做修改。

### 6.3 `Lean` 不再只是 upstream mirror

如果 Lean 被實際修改，就不能再簡單稱它為 OSS 完整複製。它至少可能是：

```text
- upstream mirror with local patches
- product fork
- unofficial execution substrate
- transitional runtime repo
```

本報告暫時將其定義為：

```text
實際 execution substrate / product fork candidate
```

但這需要 ADR 正式化。

### 6.4 `lean-platform` 必須被重新決策

lean-platform 目前可能是：

```text
1. 原計畫中的 product fork，但未實際使用
2. 舊分支 / 歷史嘗試
3. 錯誤 repo mapping 的來源
4. 未來應該被恢復為 product fork 的候選
5. 應該 archive / merge / rename 的 repo
```

SA 報告不能直接假設它無用，但必須把它標為：

```text
pending execution-substrate decision
```

---

## 7. 需要重新驗證的技術問題

### 7.1 Lean 是否有 Pantheon-specific integration？

檢查：

```text
Pantheon namespace
RuntimeBinding
DeploymentPlan
TelemetryEvent
capital_pool_id
artifact_id
persona_capital_binding_id
Search / Data Gateway
kill-switch bridge
```

若沒有找到這些符號，不能直接說 Lean 沒改；但可以說：

```text
尚未看到 Pantheon canonical contract 被明確接進 Lean。
```

### 7.2 Lean 是否能消費 Pantheon artifact projection？

必須檢查：

```text
metadata.json
artifact checksum
deployment_stage
promotion state
rollback parent
runtime_config_ref
broker/account scope
```

### 7.3 Lean 是否能回吐 Pantheon TelemetryEvent？

必須檢查：

```text
order / fill / position / heartbeat / drawdown / pnl / rejection / broker disconnect
```

以及是否帶：

```text
binding_id
runtime_id
capital_pool_id
artifact_id
artifact_version
deployment_stage
plan_id
persona_capital_binding_id
```

### 7.4 pantheon 是否仍指向 lean-platform？

要檢查：

```text
deployment plan docs
runtime manager code
artifact loader docs
BFF labels
CI scripts
infra manifests
Codex task packets
```

### 7.5 front 是否仍顯示 lean-platform assumption？

要檢查：

```text
operator runtime pages
deployment review pages
runtime state board
bffClient route names
copy / labels / docs
```

---

## 8. 分析邊界

本批 SA 文件 01–05 的分析邊界是：

```text
01 執行摘要
02 分析前提與最新校正
03 藍圖基準
04 現行 repo 盤點方法
05 Repo 角色與責任重映射分析
```

本批文件不會逐一完成所有 plane 的詳細 gap 表；那會在後續 06–25 章展開。

不過，本批文件會先固定：

```text
- 什麼是 target blueprint
- 什麼是 current system boundary
- 什麼是 evidence hierarchy
- repo ownership drift 怎麼判斷
- Codex 後續應該如何根據 SA 報告施工
```

---

## 9. 本 SA 報告的證據準則

本報告會把證據分成五層：

| 等級 | 證據 | 解讀 |
|---|---|---|
| A | executable code + tests + e2e path | 可視為高信心 implementation |
| B | schema / service contract / event contract | 可視為正式 contract，但不等於已閉環 |
| C | BFF client / UI page / read model | 可視為 surface exists，但可能是 mock |
| D | README / design doc | 可視為意圖 / planning evidence |
| E | folder / naming | 只能視為弱線索 |

如果某個功能只有 README 或 UI page，本報告會標為：

```text
Documented-only 或 Surface-only
```

而不是 Implemented。

---

## 10. 本報告的狀態標記

後續所有 gap 項目將使用：

```text
Implemented
Partially Implemented
Contract-only
Surface-only
Documented-only
Absent
Misplaced
Conflicting
Unverified
```

### 10.1 Implemented

有 code、有 state transition、有 persistence 或 runtime effect、有 test。

### 10.2 Partially Implemented

有部分 code 或 schema，但缺 producer / consumer / writer / tests。

### 10.3 Contract-only

有 schema / contract，但尚未證明 service 落地。

### 10.4 Surface-only

有 UI / client，但後端或 state machine 不明。

### 10.5 Documented-only

只有 README / design doc。

### 10.6 Misplaced

功能存在，但在錯誤 repo / plane。

例：

```text
news connector 在 Lean toolbox，卻未進 Pantheon Source Registry / Evidence Store。
```

### 10.7 Conflicting

不同文件或 repo 指向不同 truth。

例：

```text
藍圖：lean-platform 是 execution substrate
現況：Lean 是實際修改 repo
```

### 10.8 Unverified

需要 runtime manifest、CI、deployment script 或實機才能確認。

例：

```text
production launcher 實際指向 Lean 或 lean-platform。
```

---

## 11. 必須立刻建立的 Decision Records

### 11.1 ADR-EXEC-001：Execution Substrate Repo Decision

內容：

```text
正式決定 Lean 還是 lean-platform 承接 Pantheon execution substrate。
```

必須回答：

```text
- 哪個 repo 是 product runtime repo？
- 哪個 repo 是 upstream mirror？
- Pantheon-specific patches 放哪裡？
- 如何處理 upstream sync？
- Codex 應 patch 哪個 repo？
```

### 11.2 ADR-EXEC-002：Pantheon Runtime Contract

內容：

```text
DeploymentPlan / RuntimeBinding / artifact metadata / telemetry envelope
如何在 pantheon 與 Lean 之間傳遞。
```

### 11.3 ADR-EXEC-003：lean-platform Disposition

內容：

```text
lean-platform 要 archive、merge、rename，還是重新啟用？
```

### 11.4 ADR-DATA-001：External Data Gateway Boundary

內容：

```text
news / social / alpha DB / market data / filings / macro / broker telemetry
哪些走 Pantheon Data Gateway，哪些只留在 Lean execution feed。
```

### 11.5 ADR-SEARCH-001：OpenClaw Search Gateway

內容：

```text
OpenClaw search 是否經 governed Search Gateway，如何做 ACL / source entitlement / citation pack。
```

---

## 12. 本章結論

本章固定的核心前提是：

> **本次 SA 分析不再把 lean-platform 當作已採用 execution substrate；現況以 Lean 作為實際被修改的 execution substrate 來盤點。**

這導致最重要的差異判斷：

```text
原藍圖 repo mapping 與現況 repo usage 不一致。
```

接下來所有 gap analysis 必須圍繞這個問題展開：

```text
Lean 是否正式承接 Pantheon runtime contract？
如果沒有，則現行 execution plane 仍未真正對齊藍圖。
```

---

## 附錄：本章主要依據來源

- `pantheon/Pantheon_總索引版系統分析文件.md`
- `pantheon/TARGET_ARCHITECTURE.md`
- `front-ai-trading-system/README.md`
- `Lean/readme.md`
- `Lean/Launcher/Program.cs`
- `lean-platform/readme.md`
