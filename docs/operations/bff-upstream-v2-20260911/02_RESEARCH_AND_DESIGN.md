# 02 研究規畫與SA／SD：唯一責任元件、契約與資料邊界

版本：2026-09-11 交付版（V2）  
狀態：**依據批准架構建立之系統分析與系統設計（SA/SD）規格**。

---

## 1. 研究問題、方法與交付規格

| 研究問題 | 已查證之具體事實 | 核准後實作前之輸入／輸出邊界 |
|---|---|---|
| 11 個 blocked 是否仍成立？ | 正式 show、terminal facts、207 節點現況 DAG、精確 PR 狀態。 | 最新 checkpoint / row generation / lease 與批准後 packet 綁定。 |
| 是否已有可沿用之實作？ | 對照 main、domain services、adapters、ports、stores、FE 消費端；比對新舊 dev blob。 | 每個 flow 明確一列：觸發 → 身份 → 政策 → state owner → 副作用 → receipt → readback。 |
| 是否會重做下游既有任務？ | 讀取所有 source grants 與 ancestor closure。 | 拆出最小上游 scope；原任務只保留明列之 residual obligations。 |
| 是否只是把重複邏輯搬檔？ | AST definitions / callers / persist 候選、動態負向探針、state/cache owner 查核。 | 舊 symbol → 唯一 owner → 所有 consumer → 刪除驗證；禁止 forward-only 抽離。 |
| API／資料實體是否一致？ | 精確 FE 狀態 / actions、Jobs 多來源、program / run / decision 與 tickets 分辨。 | 批准的 action / state / source 矩陣、DTO 與 authority contract。 |
| 可否完整運作？ | 已確認若干真實失敗，不把 fixture 或 metadata 當執行成功。 | 真 domain transaction、receipt / readback、UI、配對部署分層驗收。 |

---

## 2. 核心架構決策

### A. 一個責任 Owner，不等於一個巨型檔案

保留既有服務作為入口與 state owner。可將純計算、schema、response projection 拆成內聚小模組，但**不得再分配第二個 cache / ledger / executor**。嚴禁整包複製 main closure 到另一個幾萬行 service，或新增含 `globals()` / 任意字典 callback 的通用 framework。

- **分層責任**：Transport Routers → Application Use Case → Domain Policy / Ports → Authoritative Store / Domain Executor → Read Projections。
- `main.py` 僅負責建立依賴、綁定生命周期與掛載 router，不承擔上述業務決策。

### B. 同流程單一實作與不同實體的清晰邊界

| 業務責任 | 保留之唯一 Owner | 絕不混在一起的職責 |
|---|---|---|
| OperatorCommand 受理、confirm、重播與 receipt | 既有 `CommandAdapterService` ＋ `CommandStore` | 不把 NL conversation reservation、Research experiment state 塞進 command queue。 |
| 登入 / role / MFA / JWKS 驗證 | 既有 auth policy/service、`runtime_auth_inbound` cache | 不在 router/service fallback 解析另一種 token。 |
| Persona health / source truth | app-scoped `PersonaService` ＋ 單一實例 cache | Runtime/Assistant 僅為 consumer，不各自重算。 |
| 私有 Agora 資料 visibility | 既有 `AgoraService` ＋ 既有 scope policy | Context resolver 不另定第三套權限。 |
| Journal context ref | Agora interaction 內單一 resolver；原 journal / trade-episode owner 保持獨立 | 不因共用 context transport 把不同 journal store 硬合併。 |
| Command audit 讀投影 | 單一 command-audit projector 讀取既有 durable records | `/bff/audit` 與合規 ledger 可有不同 DTO，不是互相替代。 |
| Assistant 上下文組裝 | 既有 composer ＋ 單一 typed source collector | 不加入 repo 寫入或 supervisor 任務調度功能。 |
| Management NL exchange | 單一 NL use case ＋ 既有 durable NL admission store | 不共用開發工具 recovery machine；不保留 conversation/memory 雙重 replay。 |
| Evolution program membership / control | 既有 Evolution service 補齊缺失之 aggregate | 不能代替 decision、run、approval、dispatch state。 |
| Evolution 執行 | 既有 dispatch outbox / worker / terminal receipts | 不另建 program polling / retry / executor。 |
| Research experiment | 既有 `ResearchWriteOwner`，經 Research 服務提供 typed 能力 | ResearchTicket、orchestrator run、artifact 不是同一實體。 |
| Jobs 讀取 / 操作選擇 | 單一 BFF 讀 composition ＋ 既有 adapter registry 之唯一 JobAction handler | 每類 job 仍只有自己的 domain owner；不建萬用 JobStore。 |

### C. 機制呼叫端四類分類準則

對 AST 盤點出的機制與呼叫端進行嚴格分類：

1. **尚未執行的 Operator Action**：共用 U3 admission，持久化後才交給既有 executor。
2. **已由 Domain Owner 完成之動作**：驗證 owner receipt 後產生/投影稽核，**嚴禁再排入執行造成第二次副作用**（如 Agora 部分 signal-feedback/handoff）。
3. **Conversation / Ask Exchange**：維持其既有 read / conversation 角色與 reservation，不一律升級成 operator-write 權限。
4. **Domain CRUD**：留在相應 aggregate transaction；共用儲存原語不等於合併 state machine。

---

## 3. HTTP／Auth、快取與請求安全設計（U1）

- **單一 Core App Factory**：middleware、error formatting 只有一份；settings、management、core 路由不重複建構。
- **統一錯誤與安全標頭**：HTTPException、validation、unhandled exception 一律套用同一 error envelope、correlation ID 與 CORS；完整保留 preflight 204、SSE streaming 與 security headers。
- **Mandatory Auth 政策**：由真實組裝提供，嚴禁 dummy identity、optional no-op role guard 或冒名 token 解析 fallback。隔離測試使用明確外部 identity fixture，測試真實 authorization。
- **補齊 JWKS Prewarm**：重用 `runtime_auth_inbound` 現有 cache 實作 prewarm；涵蓋直連 URI、discovery、無設定 no-op、失敗有界（auth 仍 fail closed）、以及下一 request cache hit。不新造 cache 或 token validator。
- **啟用 ProviderReadinessCache 生命週期**：將既有 `ProviderReadinessCache` 與 `create_lifespan` 真正與 main 組裝、provider probe 及背景 refresh / clean shutdown 相連；外部 timeout 有界，失敗不繞過 auth。

---

## 4. Command Admission／Confirmation／Audit 設計（U3）

### 4.1 唯一處理流程

```text
Router / Typed Domain Adapter
 → Trusted Identity + Normalized Command
 → CommandAdapterService：權限檢查、Schema 驗證、Target 檢查、Policy、MFA/Approval
 → CommandStore Transaction：Scope/Hash/Replay、Active Target 鎖定、Confirm 重新驗證
 → 單次原子持久化：Durable Command / Foundation / 必要 Confirmation 紀錄
 → Receipt 回傳 + 既有 Executor 派發（僅限真正新指令）
 → Actual Domain Receipt / Readback → Command Status / Audit Projection
```

> **語義規範**：`202 Accepted` 僅代表受理成功。`executed / completed` 必須具備 owner evidence；無 owner、目標不存在、網路未知或儲存失敗，絕不得組裝成功 JSON。

### 4.2 權威安全補齊

- **Replay Identity 規則**：固定為 `trusted tenant + actor + canonical command namespace + key`；操作、target、params 納入 canonical request hash。同指令由不同 transport 進入正規化至同一 namespace；更換 target 時同 key 必須回傳 409 Conflict。
- **授權讀取政策**：Same identity/key/hash 重播相同結果；其他 tenant/actor 不得借用 replay 紀錄。Command status / audit 按 `role + tenant + record visibility` 授權，合法 reviewer/admin 可查閱被授權之他人指令，未授權者拒絕。
- **單一 Confirmation 狀態機**：Confirm create/read/redeem/revoke、two-man approval、expiry、action-target binding 共用同一份 state/policy，不另立獨立 token ledger。
- **並發與交易原子性**：同鍵並發、active-target、confirm-consumption 於同一 transaction 內重新驗證。
- **CommandStore 檔案與一致性**：CommandStore 作為唯一 state boundary。修正多實例 stale cache 優先採用單一 transaction sidecar lock ＋ fresh read ＋ fsync；若需跨主機 HA 則明確替換共享 backend，不保留雙軌 fallback。
- **Audit 與 Receipt 生成**：AuditAction 與 receipt 均由真實 durable records 投影生成，projector 不維持第二份獨立真相。

### 4.3 同步刪除範圍

清理 `main.py` 重複實作、service 簡化 fallback、action router 本機 persist、Persona `_sem_command_response` 與 strategy/persona admission、Tools/Governance 假 accepted fallback、Alert acknowledge 吞錯。所有 typed callback 參數一次修正，移除 `except TypeError` 猜簽名之不良模式。

---

## 5. Persona／Journal／Assistant／Management NL 設計

### Persona（U2）
收斂 10 組相同 AST body 與 1 組近似 helper 至 `PersonaService`；搬出真實 health builder 與必要 read helper。Runtime、Persona、Assistant 共用同一 app-scoped 實例。完整保留 TTL、monotonic clock、deep-copy、tenant scope、stale/unavailable 與 paper/live honesty 語義。

### Journal（U4）
單一 typed context resolver 讀取 bound `AgoraService` visibility 與 typed journal readers。各 DecisionEvent、DecisionJournal、trade episode 維持原 owner。缺來源明確回傳 unavailable，不保留轉 unscoped reader 之 fallback。`audience_verified: false` 維持真實狀態，不擅改為 true。

### Assistant／Management NL（U5 / U6）
- **Assistant**：typed source collector 由真實 domain ports 讀取，保留既有 composer、redaction 與 mode policy。不使用假資料替代待測業務，不引入 shell 或 supervisor 依賴。
- **Management NL**：ask 與 stream 共用單一 NL use case 及既有 `ManagementNlCommandIdempotencyStore`。嚴格遵循「身份輸入 → 高風險拒絕 → tenant 驗證 → reservation → retrieval / provider → durable terminal result」順序。徹底刪除 NL 專屬記憶體字典、雙重寫入及切換 flag；不將 NL 與 operator command queue 混為一談。

---

## 6. Evolution 產品契約與實作設計（U7 / U8A / U8B）

### 6.1 現役契約對齊（撤回簡化方案）

| 實體 | 現役契約與狀態要求 | 設計規範與職責邊界 |
|---|---|---|
| Program | FE 現役 6 狀態：`draft / active / paused / under_review / completed / retired` | 明確 review、approve、pause、resume、complete、retire 轉移規則與授權邊界。 |
| Run | 獨立狀態：`queued / running / paused / completed / failed / cancelled` | 具備獨立 worker 與 execution receipt，不以 program metadata 代替。 |
| Experiment | FE 操作：`invalidated / attached_to_review / archived / retry` | 對齊 Research owner 與真實 artifact/approval 鏈，不預設 cancel-only。 |
| Program Actions | 現役操作詞彙對齊（pause, resume, stop, freeze_generation, promotion） | 決定單一套語彙並同步改動 producer 與 consumer，不留永久 alias。 |

### 6.2 持久化與執行解耦

- **U8A（資料層）**：於既有 Evolution service 補齊 program aggregate，讀寫 port 分離。PATCH 嚴格限制可變 metadata 欄位，不得直接改 status 繞過生命週期政策。使用 `PostgresJsonOwnerStore` 交易原語，program、revision、idempotency receipt 於同 transaction 提交。
- **U8B（執行層）**：符合 approved-decision 語意之真實執行，沿用既有 decision approval → dispatch outbox → dispatch worker → terminal receipt 鏈路，不新增第二個 dispatch engine。補齊 FE resume handler、真 ID 關聯，移除前端固定空值與假完成。

---

## 7. Research／Jobs 設計（U10A / U10B）

### 7.1 六類來源明確界定

1. **Research worker jobs**：由 research-worker-gateway 其 store 擁有。
2. **Research orchestrator runs**：由 ResearchOrchestratorStore 擁有，建立 run/artifact/experiment 明確關聯。
3. **Trainer preview jobs**：由 training-session owner 擁有。
4. **Source-ingest runs/jobs**：由 ingestion runtime / store / receipts 擁有。
5. **Policy-learning jobs**：由 policy-learning owner 擁有。
6. **OpenClaw workflow jobs**：由 upstream workflow owner 擁有，BFF 僅作唯讀適配，不混入開發 supervisor tasks。

BFF 採單一 Jobs 讀取 composition ＋ 單一 JobAction 路由入口，按合格來源調度至唯一 domain owner。

### 7.2 寫入與真實收尾

- **取消**：嚴格區分 requested → owner accepted → worker stopped；late completion 不能覆蓋取消 fence。
- **重試**：僅允許合格狀態，保留原 attempt 與 evidence，同 key 產生單一新 attempt。
- **封存**：定義 visibility 與保留期間，不推論 physical delete。
- **Promote**：嚴格驗證 artifact、approval、target stage 與 registry readback，不因 UI 按鈕啟用資金操作。

---

## 8. API 退役策略（U9）

- **保留核心**：保留 `POST /bff/v1/commands` 為唯一通用指令入口；保留唯一 `GET /api/v1/operator/commands/{id}` 為狀態讀取端；保留 `GET /bff/actions` catalog 查詢。
- **退役重複 Generic Write**：同步退役 `POST /bff/actions/{...}` 與 `POST /api/v1/operator/commands`，前提為 FE（`runPersonaAction`, `PersonaDetail`, `writes.ts`, `paths.ts`）與 BE 所有呼叫端全數改接完成。
- **配對部署**：跨 repo 採用候選 commit 配對，通過 exact-pair gate 後方可進行 hosted switch，不靠相容 fallback 長期過渡。

---

## 9. 決策前置任務（D-Tasks）分工

為避免產品語義未定阻塞主體結構抽離，設立 3 個契約決策任務：
1. `BFF-COMMAND-STORE-CONTRACT-DECISION-001`：確定 CommandStore 單機 lock/fsync 或共享 DB 方案。
2. `BFF-EVOLUTION-LIFECYCLE-CONTRACT-DECISION-001`：定案 Evolution 各操作正式語義與轉移規則。
3. `BFF-RESEARCH-JOBS-CONTRACT-DECISION-001`：確定 Jobs 納入之具體來源與操作權限。
