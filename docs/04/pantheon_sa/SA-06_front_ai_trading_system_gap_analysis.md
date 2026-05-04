---
project: Pantheon
document_type: System Analysis Gap Report
batch: SA-06 to SA-10
language: zh-TW
assumption: >
  本批 SA 文件採用最新校正：目前實際在 VS Code 中被修改、用於 execution substrate 判讀的是 `ajoe734/Lean`；
  `ajoe734/lean-platform` 暫列為幾乎未動、歷史分支或待決 execution repo。
---

> **2026-05-03 Canonical correction**: `pantheon/lean` submodule backed by `ajoe734/pantheon-lean.git` is the official execution substrate. Any older `lean-platform` repo-mapping drift language in this SA note is superseded; do not treat `lean-platform` as an active gap or task target.





# SA-06 — `front-ai-trading-system` 差異分析

## 1. 本章目的

本章分析 `ajoe734/front-ai-trading-system` 相對於 Pantheon 設計藍圖的落差。
本章不把前端 UI 的存在等同於系統閉環完成，而是把它視為 **Console Plane / BFF Client / UX State / Operator Workbench** 的實作證據。

本章回答：

1. `front-ai-trading-system` 目前實際承接哪些 Pantheon plane？
2. 哪些頁面與 BFF client 已經與藍圖對齊？
3. 哪些只是 UI shell、preview fallback、mock-backed surface？
4. 前端對 `pantheon` BFF 與 `Lean` runtime 的連接是否足以支撐 operating loop？
5. 後續要補哪些 UI / contract / state / realtime 能力？

---

## 2. 藍圖要求摘要

根據 Pantheon 母文件，Console Plane 應該包含：

- Operator Console
- Persona Workbench
- Research Workbench
- Knowledge Workbench
- Trainer Workbench
- Consultation Workbench
- Governance Workbench
- Evolution Workbench

BFF Plane 應該是前台唯一聚合入口，負責：

- auth / RBAC
- read model
- command facade
- realtime / notification
- view model composition

藍圖還要求：前台不是 generic chat UI，而是多個正式工作台；前台應該消費 Pantheon BFF 的 read model，不應自行建立 parallel truth source。

---

## 3. 已觀察到的 repo 證據

### 3.1 repo 自我定位

`front-ai-trading-system` README 明確表示：

- 此 repo 是 Pantheon 的 Lovable-connected UI repo。
- `pantheon` owns BFF and all `/api/*` contracts。
- 此 repo owns pages, components, UX states, and BFF client wiring。
- 若 UI blocked on missing backend fields，應寫 `bff-gap` handoff，而不是新增 fetch path 或 mock fallback。

這個定位與藍圖中的 Console Plane / BFF Plane 分工大致一致。

### 3.2 BFF client 覆蓋面

`src/lib/bffClient.ts` 顯示前端已經集中管理大量 Pantheon API surface，包括：

- research tickets / analyses / experiments / artifacts
- knowledge memory / notes / evidence / insights / strategy specs
- trainer sessions / preview / replay
- persona catalog / detail / sessions / teaching / capabilities
- capital pools / bindings / deployment plans / approval decisions
- governance review queue / approval queue / rollback review
- operator incidents / kill switch / deployment plans
- lineage graph / edges
- consultation request / transcript / committee / redteam memo
- evolution decisions / mutation review / rollback / freeze
- settings bundle

這代表 UI surface 的 coverage 很高，且命名上已經接近藍圖的多 plane 架構。

### 3.3 dev BFF hard-pin 與 preview fallback

`bffClient.ts` 同時顯示：

- `DEFAULT_BFF_BASE_URL` hard-pinned 到 dev BFF。
- `PANTHEON_ENV` hard-coded 為 `dev`。
- `PANTHEON_LIVE_BROKER_ENABLED = false`。
- `USE_MOCK_BFF = false`，但仍有 `previewMockFallback()`。
- Lovable preview host 若遇到 network error，會回到 mock data。
- 多數 BFF surface 仍保留 `mockData.mockXxx(...)` fallback factory。

這代表前端已經努力避免正式 published/dev host silent mock，但仍存在 preview fallback。對 SA 來說，這應標為 **UI surface high coverage, operational truth partial**。

---

## 4. Console Plane 對齊度分析

| Workbench | 藍圖要求 | 前端現況推定 | 對齊度 | 差異 |
|---|---|---|---|---|
| Operator Console | runtime、deployment、incident、kill switch、health | bffClient 已有 operator / incident / deployment / kill switch types | 中高 | 仍需確認是否全部由 authoritative BFF projection 支援 |
| Persona Workbench | persona registry、capability、teaching、binding | personaDrilldownApi 覆蓋 persona / sessions / teaching / capabilities / capital pool | 中高 | capability 是否來自實際 resolver 未完全驗證 |
| Research Workbench | tickets、experiments、artifacts、research search | researchBffApi 覆蓋 tickets / analyses / experiments / artifacts | 中高 | 是否有真 research orchestrator 仍取決於 pantheon |
| Knowledge Workbench | memory、notes、evidence、insight、strategy specs | knowledgeBffApi 覆蓋完整 | 中 | Evidence Store / Search Gateway 是否 authoritative 未完全驗證 |
| Trainer Workbench | session、preview、replay、patch | trainerBffApi 有 sessions / preview / replay | 中 | preview 是否接 Rapid Eval Service 未驗證 |
| Consultation Workbench | consult request、transcript、committee、redteam | bffClient 有相關 API | 中 | 後端 bounded context 是否完整仍待 pantheon 章驗證 |
| Governance Workbench | review queue、approval queue、deployment diff、rollback | governance types 與 APIs 已匯入 | 中高 | 是否能真正改變 canonical state 需檢查 command path |
| Evolution Workbench | decision、freeze、rollback、mutation | evolution decision / mutation APIs 已存在 | 中 | evolution action 是否真的 dispatch 到 runtime 未驗證 |

---

## 5. BFF Client vs BFF Contract 差異

### 5.1 BFF contract 要求

`pantheon/services/control-plane/bff/BFF_API_CONTRACT.md` 明確要求 BFF 是 **read-oriented**，且：

- BFF must never create, modify, or delete canonical state。
- BFF 不應成為 parallel truth source。
- 所有 response 欄位都應 trace back to canonical L1 object or documented derived read-model。
- v1 contract 甚至寫出 GET-only guarantee。

### 5.2 前端實作現況

`bffClient.ts` 有 `get()`、`getText()`、`postJson()`，且多個 surface 使用 `postJson()`，例如：

- legacy research analyze
- legacy research execute
- research search
- consultation create / cancel 類行為
- mutation approve / reject 類 command
- governance / incident action 類 command

### 5.3 差異判斷

這裡不能直接說前端錯。更準確是：

- **BFF contract 的 APP-001 baseline 是 read-oriented / GET-only。**
- **前端已經承接 APP-002 / command facade 類動作。**
- 因此目前文件與實作之間需要重新分層：
  - `BFF Read API`
  - `BFF Command API`
  - `Internal Control API`
  - `Secondary Control Path`

若文件仍聲稱 BFF v1 完全 read-only，但前端已經使用 BFF command path，則需要更新 contract 或拆 API namespace。

---

## 6. Mock / Preview Fallback 差異

### 6.1 已改善的部分

前端已明確將：

```text
USE_MOCK_BFF = false
PANTHEON_LIVE_BROKER_ENABLED = false
```

並且只在 Lovable preview network failure 時啟動 preview mock fallback。這比「正式環境默默吃 mock」安全很多。

### 6.2 仍存在的風險

| 風險 | 說明 |
|---|---|
| UI coverage illusion | 因為 mock factory 完整，Codex / reviewer 可能誤判後端也已完整 |
| Preview acceptance illusion | Lovable preview 可渲染，不代表 BFF / canonical store / runtime connector 存在 |
| Surface drift | mock response type 與真 BFF response 可能逐漸漂移 |
| Contract drift | 前端手寫 type 與 BFF OpenAPI / schema 不一致 |
| Runtime false readiness | runtime board 若能 mock 顯示，不代表 Lean telemetry 已接入 |

### 6.3 建議標準

前端應在每個 page / API surface 標明：

```text
source_mode:
  - authoritative_bff
  - derived_projection
  - stale_cache
  - preview_mock_only
  - unavailable
```

並且在非 preview host 禁止 mock fallback。

---

## 7. Console 與 `Lean` Execution 的連接差異

本批分析採用最新前提：目前實際修改的是 `Lean` repo，而不是 `lean-platform`。

### 7.1 需要檢查的 front-side 欄位

前端 runtime / deployment / operator surfaces 應能顯示：

```text
runtime_id
runtime_binding_id
deployment_plan_id
artifact_id
artifact_version
capital_pool_id
persona_capital_binding_id
deployment_stage
engine_repo
engine_commit
engine_runtime_kind
broker_account_ref
last_heartbeat_at
telemetry_lag_ms
```

### 7.2 目前可見差異

`bffClient.ts` 顯示前端有 runtime / deployment / incident / evolution surface，但不能從前端本身判斷：

- runtime 是否來自 `Lean`
- runtime 是否仍命名成 `lean-platform`
- runtime 是否帶 `RuntimeBinding`
- telemetry 是否從 Lean 正規送回 pantheon

因此本章結論是：**front 已有 UI / client surface，但 execution substrate identity 仍須由 pantheon BFF projection 提供，不應由 UI 猜。**

### 7.3 應補的 UI 標識

建議前端在 Runtime Detail / Deployment Review 頁面加入：

```text
Execution substrate:
  repo: Lean
  mode: product-fork
  engine_commit: ...
  upstream_base_commit: ...
  runtime_binding_id: ...
  launch_manifest_hash: ...
```

這能防止未來再發生 Lean / lean-platform 混淆。

---

## 8. 前端缺口總表

| Gap ID | 缺口 | 類型 | 嚴重度 | Owner | 建議修補 |
|---|---|---|---|---|---|
| FE-GAP-001 | UI surface 完整但 authoritative backend coverage 未逐項標記 | Behavioral / Verification | High | front + pantheon | 每個 surface 加 `source_mode` |
| FE-GAP-002 | BFF read-only contract 與 command usage 混在同一 client | Contract | High | front + pantheon | 拆 read API / command API client |
| FE-GAP-003 | preview mock fallback 可能導致 readiness illusion | Operational | Medium | front | 非 preview host 禁止 fallback，preview 顯示 banner |
| FE-GAP-004 | 缺 RuntimeBinding detail / Lean engine identity UI | Execution visibility | High | front | 新增 RuntimeBinding detail page |
| FE-GAP-005 | 缺 DeploymentPlan → Lean launch trace viewer | Runtime integration | High | front + pantheon | 新增 deployment trace timeline |
| FE-GAP-006 | 缺 TelemetryEvent drilldown | Telemetry | Medium | front | 顯示 canonical telemetry event envelope |
| FE-GAP-007 | 缺 Reconciliation diff viewer | Reconciliation | High | front + pantheon | backtest/paper/live comparison UI |
| FE-GAP-008 | 缺 Source / Search entitlement UI | Data governance | Medium | front + pantheon | settings / source health / search scope UI |
| FE-GAP-009 | 手寫 TS types 可能與 BFF schema drift | Contract | Medium | front + pantheon | OpenAPI-generated TS types |
| FE-GAP-010 | 不清楚 front 是否仍提及 lean-platform 作 runtime | Repo ownership | Medium | front | 全 repo scan / naming migration |

---

## 9. 建議 Codex Task Packets

### FE-TP-001 — Surface Source Mode Annotation

```text
Repo: front-ai-trading-system
Goal: 為所有 BFF-backed pages 加入 source_mode / degradation badge。
Acceptance:
  - authoritative_bff / derived_projection / stale_cache / preview_mock_only 顯示一致
  - published host 不可顯示 preview_mock_only
```

### FE-TP-002 — Split Read Client and Command Client

```text
Repo: front-ai-trading-system
Goal: 將 bffClient.ts 拆成 readBffClient.ts 與 commandBffClient.ts。
Acceptance:
  - GET surfaces 只在 read client
  - POST command surfaces 只在 command client
  - command client 強制要求 command_id / idempotency_key / actor context
```

### FE-TP-003 — RuntimeBinding Detail Page

```text
Repo: front-ai-trading-system
Goal: 新增 RuntimeBinding detail UI，顯示 Lean runtime identity。
Acceptance:
  - 顯示 runtime_binding_id / deployment_plan_id / artifact_id / capital_pool_id
  - 顯示 engine_repo=Lean / engine_commit / launch_manifest_hash
```

### FE-TP-004 — Deployment Trace Timeline

```text
Repo: front-ai-trading-system
Goal: 在 Deployment Review Console 中顯示 Artifact → Approval → DeploymentPlan → RuntimeBinding → Lean Runtime → Telemetry。
Acceptance:
  - 每個節點都有 id / status / timestamp / source
  - 缺節點時顯示 blocking gap
```

---

## 10. 本章結論

`front-ai-trading-system` 的狀態是：

```text
Console surface: 高成熟度
BFF client coverage: 高成熟度
Authoritative data linkage: 中等
Execution substrate visibility: 不足
Telemetry / reconciliation UI: 有殼，但需後端 projection 支撐
Mock / preview governance: 已改善，但仍需顯性標記
```

因此 SA 判斷：

> 前端不是主要架構阻塞點；它已經提供大量工作台與 BFF surface。真正風險在於 UI shell 過於完整，容易掩蓋 pantheon canonical store、Lean runtime connector、telemetry projection 尚未真正閉合的問題。下一步不是再加更多頁面，而是把每個頁面的 truth source、runtime identity、command path、mock/fallback 狀態標清楚，並與 pantheon / Lean 形成可驗證 contract。
