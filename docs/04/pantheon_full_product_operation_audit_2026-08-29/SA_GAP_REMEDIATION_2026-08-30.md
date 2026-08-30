# Pantheon GAP 根因治理目標系統分析（SA）— 2026-08-30

## 1. 架構決策摘要

目標不是把現有大檔案切成更多檔案，而是讓每個狀態轉移只存在一條可證明路徑：

```text
Desktop UI
  -> typed domain client
  -> Operator BFF transport/read projection
  -> owning domain API
  -> canonical store + outbox
  -> read model / same-ID reload
```

任何 `UI -> overlay`、`router -> main`、`BFF -> generic internal API -> runtime-manager
-> foreign domain` 或 `test -> bypass -> safety action` 都是禁止路徑。遷移期可以在原
模組內逐步切 caller，但不建立新的 compatibility façade；最後一個 caller 移走的同一
交付單元必須刪除舊路徑。

## 2. Current architecture 的根因

```mermaid
flowchart LR
    UI[execute-plans UI] -->|typed + generic| T1[bff-v1]
    UI -->|seed / overlay| MOCK[Mock & local truth]
    T1 <--> T0[legacy bff transport]
    T1 <--> V5[v5 runtime/view modules]
    T1 --> BFF[Operator BFF main.py]
    BFF -->|domain routers import main| BFF
    BFF -->|PANTHEON_INTERNAL_API_URL| LEG[Legacy internal API]
    LEG --> RM[Runtime Manager]
    LEG -.foreign commands.-> GOV[Governance / Deployment / other owners]
    BFF --> OWNERS[Domain owner services]
    CI[Many release workflows] -.not all required.-> MASTER[master]
```

這些表面上是不同 bug，實際上都源自「過渡機制變成永久機制」：

- frontend overlay 原是 fallback，後來成為 generic CRUD 的成功路徑；
- BFF main 原是 composition root，後來成為 router 可反向 import 的 service locator；
- internal API 原是相容入口，後來由 runtime-manager 代掛所有 command domain；
- workflow 原是不同測試入口，後來 branch protection 只綁其中一小部分；
- safety harness 原是證明工具，治理閘門演進後沒有跟著正式 activation contract 演進。

## 3. Target architecture 與單一 owner

```mermaid
flowchart LR
    subgraph UI[execute-plans Desktop]
      VIEW[Views / forms]
      CLIENT[Existing bff-v1 typed domain clients]
    end

    subgraph BFF[Operator BFF]
      ROOT[Composition root]
      AUTH[Local session/RBAC]
      ROUTERS[Existing domain routers]
      PORTS[Typed owner clients/read ports]
    end

    subgraph OWNERS[Domain service plane]
      POST[Postmortem service]
      SRC[Source Ingestion]
      REG[Registry]
      DEP[Deployment]
      RM[Runtime Manager]
      GOV[Governance]
      AG[Agora stores/workers]
      PAPER[Paper execution]
    end

    subgraph DELIVERY[Delivery policy]
      POLICY[Versioned release policy manifest]
      GATE[Exact-head release gate]
      HOST[Atomic FE/BFF promotion]
    end

    VIEW --> CLIENT --> ROOT
    ROOT --> AUTH
    ROOT --> ROUTERS --> PORTS
    PORTS --> POST
    PORTS --> SRC
    PORTS --> REG
    PORTS --> DEP
    PORTS --> RM
    PORTS --> GOV
    PORTS --> AG
    SRC --> PAPER
    REG --> DEP --> RM --> PAPER
    POLICY --> GATE --> HOST
```

### 3.1 Desktop UI

- UI 擁有顯示、輸入與互動狀態，不擁有業務 entity truth。
- production network/auth/SSE/write transport 收斂在**既有** `src/lib/bff-v1`；不再新增
  `bff-v2` 或 wrapper。
- `src/lib/v5` 只保留 pure DTO、view model 與 transform；不得反向 import network/mutation。
- 沒有 durable command owner 的 control 從 production UI 移除或 disabled。
- mock/seed 只允許 test entrypoint 明示注入；production dependency graph 不可達。

### 3.2 Operator BFF

- BFF 是 operator transport、auth、DTO adaptation 與 read projection，不是 domain write owner。
- `main.py` 只建立 app、middleware、lifespan、dependency objects 並 include **既有領域
  routers**；不建立 catch-all `routers/` façade。
- domain router 只依賴 factory parameter/typed port，不 import `main`、其他 router 的私有
  symbol 或 module-global service locator。
- auth 只做本地 session/tenant/RBAC；OpenClaw/provider readiness 是非阻塞 diagnostics。
- route table 必須同時滿足 normalized path、OpenAPI operation ID 與 static-shadowing 唯一性。

### 3.3 Domain write owners

| Entity / action | 唯一 owner | BFF 責任 |
|---|---|---|
| Postmortem | existing `services/postmortems` + Incident domain store | 呼叫既有 list/detail API並投影 `/bff/management/postmortems*` |
| Source definition/receipt | `services/source_ingestion` | typed management client/read projection；不啟動第二 scheduler |
| Artifact executable projection | Registry | 轉發引用，不接受 caller 自造 metadata |
| DeploymentPlan | Deployment service | typed command client與 receipt adaptation |
| RuntimeBinding/kill/rollback | Runtime Manager | typed runtime client；不代管其他 domain command |
| Approval/governance decision | Governance | typed governance client |
| Workshop/Trading/Performance | existing Agora stores/workers | transport、SSE 與同 ID readback |
| Paper order/fill/position | Paper execution/broker ledger | read projection與 operator status |

`services/control-plane/internal/internal_api.py` 不是 owner。完成 typed-owner cutover 後，整個
legacy internal surface必須退役，不能留作 degraded fallback。

### 3.4 Source 與 Paper 執行邊界

- dev 預設 Source controller 永遠是 `reconcile_only`、external egress deny。
- 唯一 provider egress 是現有 `source-ingest-scheduler` one-shot compose/deploy profile：有限
  ticks、records、concurrency、timeout、exact host allowlist，結束後 process terminal。
- 不新增 `/manual-refresh` 第二入口。產品 UI 只顯示 command/receipt，不直接取得 egress authority。
- official snapshot 必須經 market-calendar/freshness admission，然後自然觸發 signal→paper
  order→fill→position；驗收不可直接呼叫 signal helper。

### 3.5 Safety proof 與治理邊界

- 本次為功能優先（Functional-First）之 Paper/Simulation 收斂：EP5 MFA/雙人審批治理 harness（OP-G22）與 Lineage/Sponsor 數值漂移（OP-G23）記錄為延後非功能性與測試治理觀察項，不列為本次阻塞項。
- 運行時安全著重於 RuntimeBinding 權威物理投影校驗（OP-G17）及緊急熔斷/安全模式之 fail-closed 處置，杜絕 caller 任意注入自造 metadata。

### 3.6 部署可靠度與發布邊界

- 組織級 GitHub branch-protection / organization security 管理列為延後觀察項（OP-G25），不列為本次發布阻塞項。
- 本次部署可靠度（OP-G04 / OP-G16）聚焦於：
  - 部署租約心跳指數退避與 60 秒寬限期，防止 GitHub API 瞬斷中斷部署。
  - 回滾機制採用部署前 sealed local baseline，不依賴遠端 GitHub availability。
  - 消除部署與門禁中的假綠燈：任何關鍵 step fail/skip 均使 exit code 非 0 嚴格 fail-closed。
- atomic deployment 採 gate-before-switch；所有容器探針通過後始得更新 manifest 與切換軟連結。

### 3.7 開發工具邊界

TaskStore、supervisor、worker lease與 Human/Ops projection 由
`docs/operations/development-tooling-four-gap-2026-08-30/` 擁有。產品 BFF、release gate 或 hosted
manifest不得成為第二個 task writer。本方案只消費 authoritative task outcome作為 delivery lineage。

## 4. 架構不變量

| ID | 不變量 | 機器門禁 |
|---|---|---|
| SA-I01 | 每個 mutation type 只有一個 canonical owner endpoint | command registry owner map unique |
| SA-I02 | domain/router 不 import composition root或其他 router private symbol | AST import-boundary gate |
| SA-I03 | production UI 不可達 seed/mock/overlay | Rollup module graph + source import gate |
| SA-I04 | 成功 command 可同 ID/version reload readback | contract + restart/integration test |
| SA-I05 | 部署與門禁執行 fail-closed；關鍵步驟失敗即阻斷 | deployment exit-code & step gate |
| SA-I06 | normalized route、operation ID、static shadowing皆為 0 | BFF composition test |
| SA-I07 | 部署推廣與驗收結果綁定 exact candidate SHA，缺失即阻擋 | promotion/acceptance manifest audit |
| SA-I08 | hosted manifest、FE、BFF、workers與checkpoint同屬 accepted candidate | pre/post-switch evidence validator |
| SA-I09 | dev Source預設無 provider egress；one-shot profile有限且終止 | compose contract + hosted receipt |
| SA-I10 | migration完成後舊路徑與專屬 fallback tests為 0 | retirement ledger gate |

## 5. Migration 與 deletion 原則

每個 migration unit 必須依序完成：

1. 列出 production、test、workflow、Compose、documentation callers；
2. 指定 existing canonical owner 與 typed contract；
3. 建立 parity test與 durable readback；
4. 將 caller切至 owner並觀察 legacy-path telemetry 為 0；
5. 在同一 PR/交付單元刪除舊 handler、env、export、fixture與專屬 legacy test；
6. 加入 forbidden-import/symbol/path gate，防止復活。

不接受「先留 alias 下次再刪」作為完成；若 caller 尚未歸零，該 migration unit 就仍未完成。

## 6. 非目標

- 不開啟 real capital/live broker；本次只處理 Paper/Simulation 交易循環。
- 不建立 mobile 專屬驗收。
- 不展開複雜 EP5 MFA/雙人審批治理程式開發（列為延後非功能性觀察項 OP-G22）。
- 不展開遙測 Lineage 與 Sponsor 測試數值漂移修復（列為延後測試治理待辦 OP-G23）。
- 不展開組織級 GitHub branch-protection / organization security 管理（列為延後觀察項 OP-G25）。
- 不重寫已合入且 source-level 已通過的 Source→Agora projection 邏輯，只補 current hosted proof。
- 不以文件任務重複開發已由 devtool package 擁有的 TaskStore/supervisor 修復。
- 不為了縮短行數機械拆檔；責任與依賴方向正確才是完成條件。
