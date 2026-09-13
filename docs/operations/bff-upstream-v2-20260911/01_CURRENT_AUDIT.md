# 01 現況、11個卡點與前版錯誤修正

版本：2026-09-11 交付版（V2）  
狀態：**依據批准規畫與 exact-tree 查證展開之正式分析報告**。

---

## 1. 查證基準與環境邊界

本報告的查證時間戳記為 **2026-09-11 00:56 UTC**（初次盤點）至 **2026-09-11 02:34 UTC**（派工後核對）。

| 基準項目 | 查證事實與精確識別碼 | 說明與邊界限制 |
|---|---|---|
| BE repository / 最新 dev | `ajoe734/pantheon@e3ef0a4b6c8500f19019196e67fcbd5df6df67dc` | 本次 fresh fetch 之遠端 dev 分支頂端。 |
| BE dev-root 實際 checkout | `ba6c9e99ec4a0b09ca85b30ab18bb862a3e42e58` | 本機 dev-root 工作樹，不把未切換的工作樹當成最新 dev。 |
| Live command runtime | `ba6c9e99ec4a0b09ca85b30ab18bb862a3e42e58` | Supervisor 配置、watchdog 與 health 綁定之 runtime SHA；未進行 promotion。 |
| FE repository / 最新 dev | `ajoe734/execute-plans@d0f2186c12355cb4aa7179f14069c7e627b8e840` | 以 `git rev-parse origin/dev` 與 `git show` 讀取精確版本；不使用落後之本機工作樹。 |
| Canonical task authority | `runtime/task-state/task-state-events-v2.jsonl` | 依 live config 指定之 V2 事件日誌，透過正式 `scripts/ai-status.sh show` 讀取。 |
| Canonical checkpoint | event count 9499（初次盤點）/ 9650（派工完成） | 207 個原節點，無循環，所有依賴均可追溯至 active/terminal/validated archive。 |
| Shared checkout 隔離 | `task/DEV-FE-HOSTED-JOURNEYS` | 其他 worker 工作樹存在本機未提交修改；本任務使用專屬獨立 worktree，絕不污染共用環境。 |

### 1.1 16 個核心 Production 檔案 Blob 一致性驗證

針對 `ba6c9e99...`（本機 runtime）與 `e3ef0a4b...`（最新 dev）之間的 18 個差異檔進行分析，確認差異全數為 supervisor/promotion 修復及 B09 測試/證據檔案。

本題引用之 BFF 產品模組、runtime_auth、Evolution、Research 等 16 個關鍵 production 檔案，經 git blob hash 交叉比對，**兩版本完全二進位相同（SHA 相等且 diff exit 0）**：

| Repository 相對路徑 | 查證 Git Blob SHA-1（base / current_dev 相同） |
|---|---|
| `services/control-plane/bff/agora/identity/router.py` | `92e1e0b6c8268d057c6a91c00f98730b2852eaec` |
| `services/control-plane/bff/agora/personalization/router.py` | `bd7c9ce3585e83f0b3ebe1c62b54921f143bdfe0` |
| `services/control-plane/bff/agora/service.py` | `22805a0a4d261e307674753a40fb474c14237e16` |
| `services/control-plane/bff/command_adapters/router.py` | `a73b4713e6b0bc524ddbe6dd8e229c94913ac1c4` |
| `services/control-plane/bff/command_adapters/service.py` | `302c579356f0b3e0aa4f1b1b5e74a9eaeb83e735` |
| `services/control-plane/bff/command_queue.py` | `37b3010d040dd1c0eaac13c3e0ee9b63fc893308` |
| `services/control-plane/bff/deployment/router.py` | `e7c7049cbf0de883319ca78249f31910ec09291c` |
| `services/control-plane/bff/governance/router.py` | `7f2bb816fdac9fe7b9e047359ca149aa67ab4a7b` |
| `services/control-plane/bff/governance/service.py` | `99f9ec4af440e7048fe376affc70777dbf20376d` |
| `services/control-plane/bff/incidents/router.py` | `363f02a2617523ab6d6f39f50a1b79c5783cb4d7` |
| `services/control-plane/bff/main.py` | `6150525cd279be2f9a6c435b7b36ed2192a39dd4` |
| `services/control-plane/bff/personas/routes/lifecycle.py` | `58dd37192408fcdee9e652eae83d0044e4ee7ce3` |
| `services/control-plane/bff/personas/routes/ranking.py` | `8f1434262a88f9444add40fcabc14be4dccfc3bd` |
| `services/control-plane/bff/personas/service.py` | `7b8ae46bdee13ec16dba3aa9e84fe4f63aa1ae07` |
| `services/control-plane/bff/tools_integrations/router.py` | `a5aa8fb15b11042cf41e2ab46a005818c809ec5a` |
| `services/control-plane/bff/tools_integrations/service.py` | `c3edb309940234a1b98b373b675d136dcd5b8d83` |

---

## 2. 查證時 11 個 Blocked 任務精確狀態

以下為初次查證時透過 canonical command 讀回之真實狀態（非猜測文字）：

| 任務 ID | Generation | Owner / Reviewer | 真正阻塞原因與處置 |
|---|---:|---|---|
| `BFF-TEST-MIGRATION-B02-ASK-ASSISTANT-WORKSHOP-001` | 2 | Antigravity2 / Codex | 真實 source collector 仍為 main 所擁有；上游需抽離 collector，同步確認資料來源；不 fake 待測業務。 |
| `BFF-TEST-MIGRATION-B03-AUTH-SESSION-JWKS-001` | 2 | Antigravity2 / Codex | 缺 HTTP/auth composition、JWKS prewarm、confirm lifecycle；已完成之 auth/session/harness 予以保留不重建。 |
| `BFF-TEST-MIGRATION-B04-SECURITY-ERROR-IDEMPOTENCY-001` | 2 | Antigravity2 / Codex | middleware/error、共用 command、NL admission、program concurrency 等 source scope 不在原 test grant 內。 |
| `BFF-TEST-MIGRATION-B05-GOVERNANCE-APPROVALS-001` | 4 | Codex / Antigravity | journal context、visibility、persona projection 及 command source 缺口；phantom-fixture 舊卡點已過時。 |
| `BFF-TEST-MIGRATION-B06-GOVERNANCE-AUDIT-COMMITTEE-001` | 5 | Codex / Antigravity2 | 受理 → durable foundation → receipt → audit 讀取鏈不一致；不能改測另一個 audit route。 |
| `BFF-TEST-MIGRATION-B10-EVOLUTION-PROGRAMS-001` | 3 | Antigravity / Codex | mutation-review 權限/evidence 與 journal surfaces 缺口；另查得 program/experiment/jobs 缺實際 owner 效果。 |
| `BFF-TEST-MIGRATION-B14-LOOPS-PAPER-V5-001` | 3 | Codex / Antigravity | main/PersonaService 重複 projection/cache；真實 health builder 與 intervention admission 需要上游修復。 |
| `BFF-TEST-MIGRATION-B16-COMMAND-WRITE-WORKFLOW-001` | 5 | Antigravity2 / Codex | command/confirmation/replay/executor 路徑分裂；不能只改外層 router。 |
| `BFF-TEST-FULL-MIGRATION-CORRECTIVE-001` | 26 | Antigravity2 / Codex | 需全部現行子批次/successors 完成；只有 evidence artifact，不是可以承接全部 source edits 的任務。 |
| `PPL-ALLOC-007` | 2 | Codex2 / Claude | 已有 done archive g1 與 active g2 之間的 authenticated import/role/generation provenance 未證明；不重做已交付 FE。 |
| `FE-EXACT-PAIR-PROTOCOL-001` | 10 | Codex / Claude | FE PR #745 仍部分 source 交付；parent authenticated delegation、同一 parent lease epoch、真 gate artifact、image qualification/whole-pair restore 未完成；不是 child 再 acquire 另一把 lease。 |

> **界限宣告**：11 個為當時阻塞任務數，非 11 個獨立程式缺陷。其他處於 `todo` 狀態的任務包含下游等待、Human/Ops acceptance 與 execution authorization，不得將「非 blocked」視為「可立即無序開工」。

---

## 3. 已完成工作與 GitHub 交付現況（查證時間點快照）

- **OPS-SUPERVISOR-PROMOTION-DRAIN-FENCE-001**：canonical done g8（2026-09-11 00:08:29Z）；PR #5736 於 00:05:52Z merge 為 `2030696b4b7e793d24824e65682bf683db0f9ed9`。
- **BFF-TEST-MIGRATION-B09-RESEARCH-KNOWLEDGE-001**：canonical done g5（00:32:02Z）；PR #5748 於 00:30:51Z merge 為 `e3ef0a4b6c8500f19019196e67fcbd5df6df67dc`。
- **歷史完成項目**：B07 done g7、B15 remainder done g11、B15 allowlist correction done g8；original B15 已 superseded。不可重開 terminal task 轉移檔案。
- **既有決策與 Seam 成果**：已存在之 auth/session seam、management session harness、B05/B06/B14 決策紀錄繼續沿用；決策完成不等於 source extraction 已交付。

### 3.1 八個測試批次 PR 狀態

查證時 8 個 batch PR 仍處於 `OPEN` 狀態：
- B02: PR #5750（head: `466c1b3f945037d048b488d30e010839f3c7ef95`）
- B03: PR #5749（head: `084e91be7f2a7db74bb9f1503ce96f7c5e2193b2`）
- B04: PR #5755（head: `ff99a19c5c2d3cb549646b9a89cfafe46ef49d01`）
- B05: PR #5746（head: `582e987c6da8636be265a6e8b4e7b82f099c1580`）
- B06: PR #5747（head: `07b5394236a2fa5db7576566085a6ad9a8264e1e`）
- B10: PR #5754（head: `1004ea1fb981f44fc548483fc11f26f23555543c`）
- B14: PR #5751（head: `5249cf6cb09bb0bc5a3b9875bb282cfb8004f128`）
- B16: PR #5757（head: `b5b7e9a8685e135508a6b2ba268df1bc21ae4f3a`）

一般 checks 成功不等於全部 gate 通過。外部讀回之 external canonical review gate 在上述 PR 為 FAILURE，FE PR #745 亦有 Component merge gate 及 Smoke acceptance 失敗。  
**Merged、Canonical done、當下 status context 是三種不同事實**；不抹消真實 done，亦不虛報所有 checks 為綠燈。

---

## 4. 同一流程多份實作：前版漏列部分與 AST 清查事實

經由 Python AST 有界靜態掃描確認：26 個具名機制定義、65 個具名呼叫者、20 個 command-persist 呼叫候選、124 個 idempotency 相關 assignment、0 parse errors。

| 位置與呼叫者 | 現況機制與缺陷 | 正確處置方向 |
|---|---|---|
| `main.py` (`_submit_final_command_admission`, `_sem_command_response`, confirm helpers) | 真實共用政策、persist、foundation 與回覆仍在主程式內。 | 搬至既有 `CommandAdapterService` 及其單一責任子模組，主程式只負責組裝。 |
| `CommandAdapterService` | 除 submit 外，尚有獨立 confirm lifecycle、role/identity/error fallback 與兩份 memory ledger。 | 不只搬移入口，徹底清除相同流程之平行實作與可選安全依賴。 |
| `action router` (`command_adapters/router.py`) | 未注入 admission 即自行 persist/dispatch/foundation。 | 刪除 local admission；舊 POST 消費端全數改接後退役該路由。 |
| `Persona service` / ranking routes | 第三份 `_sem_command_response`，另有 strategy-persona action 與重複 idempotency 全域字典。 | 納入 U3 修復，不只做 U2 讀投影；實測探針已證實 missing-global 造成 NameError。 |
| `AgoraService` / identity / personalization | action 受理有獨立持久化；Ask 亦有 command persist 候選。 | 按業務實體分類：OperatorCommand 共用 U3；conversation exchange 不強套 operator write 角色。 |
| `ToolsIntegrationService` | MCP/skills action 另有 ledger 與假成功 fallback，callback 契約不一致。 | 同步改接 typed action port、刪除 fallback，不增加第四種 callback 簽名。 |
| `GovernanceService` | governance action fallback 自行組合 accepted 結果。 | 對同類 OperatorCommand 改接唯一 admission，領域批准本身仍由 Governance owner 擁有。 |
| `Incidents alert acknowledge` | 自行 persist 時傳入錯誤 target 型別、吞錯後仍宣稱 submitted。 | 納入 U3 改接真實受理/ack owner，不能以 memory ack 掩蓋持久化失敗。 |

---

## 5. 實際隔離重現之安全與權威缺口

重用隔離 probe venv（新建 TemporaryDirectory、無外網、未 import `bff.main`）驗證下列事實：

1. **Persona Semantic Action 崩潰**：呼叫真 service 重現 `NameError: _scoped_idempotency_cache_key`。
2. **Alert Acknowledge 假成功**：直接呼叫真 async endpoint，回傳狀態 202、submitted，但底層 durable command records 為 0。
3. **Governance 缺依賴仍放行**：缺少 admission dependency 仍回傳 accepted 及 command ID。
4. **可選身份 Fallback 隱患**：Service 可選 identity fallback 接受非正式 token 格式；雖然 main 目前會注入 guard，但修復 factory 仍必須改為強制 fail-closed。
5. **CommandStore 實例快照不一致**：同檔不同 instance 之間，已 cache 的第二 instance 看不到新寫入，程序內 RLock 無法保證跨實例一致性。
6. **Journal Resolver 降級**：Journal resolver 存在 TypeError 後改讀 unscoped reader 之舊簽名 fallback；`DecisionJournal` 目前 `audience_verified: false`，不得在抽離時擅自升格權威。

---

## 6. 領域能力界線與保留義務

- **Evolution**：program POST/PATCH 向 read-store 呼叫不存在的 write 方法；正式 program owner 能力缺失，不應繼續污染 ReadSurfacePorts。現役 FE 狀態為 `draft/active/paused/under_review/completed/retired`，另有獨立 run state；撤回前版三狀態簡化方案。
- **Jobs**：實際查得至少六類 job/run 來源（Research worker jobs、Research orchestrator runs、Trainer preview jobs、Source-ingest runs/jobs、Policy-learning jobs、OpenClaw workflow jobs）。各有原本 owner，不能預設 gateway 是一切 Job 的 owner，亦不可擅刪未實作動作。
- **去重與保留**：既有下游 corrective（`BFF-ROUTER-USECASE-CORRECTIVE-001`、`DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001`、`JOURNAL-RUNTIME-CONTRACT-CORRECTIVE-001`、`BFF-READ-OWNER-WIRING-CORRECTIVE-001`、`LOOP-RECEIPT-INTEGRATION-CORRECTIVE-001`、`SIMPLIFY-BFF-RESIDUAL-001`、`DEV502-FIX-001`、`DEV502-OBSERVE-001`）均正式明列責任界線，精確保留原任務未交付之具體義務：包括 `JOURNAL-RUNTIME-CONTRACT-CORRECTIVE-001` 之真 DecisionJournal runtime durability/config/bootstrap/readback（非本輪單一 resolver 所能涵蓋）、`LOOP-RECEIPT-INTEGRATION-CORRECTIVE-001` 之真 12-loop receipt 鏈整合與 ground-truth 對照、`DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001` 之其餘 writers／配置與全域 durability 整合、`BFF-ROUTER-USECASE-CORRECTIVE-001` 之未涵蓋 route-usecase 抽離與跨 domain 整合、`BFF-READ-OWNER-WIRING-CORRECTIVE-001` 之尚缺來源與跨 owner 讀回、`SIMPLIFY-BFF-RESIDUAL-001` 之剩餘結構／體積／import 回歸、以及 `DEV502-FIX-001`／`DEV502-OBSERVE-001` 之真 trace 所需其餘 failpath／觀測與驗收。恪守單一 owner 界線與相關 shared-writer 循序約束，不准重做已由上游交付之相同修復（詳見 04 第 4 節去重與保留責任矩陣）。

---

## 7. 本文件不宣稱事項

本報告不宣稱 61 個 batch 測試檔案全數通過；不宣稱 hosted 產品、全部 provider、跨主機 HA、真實交易或資金操作已可運行。Management、Agora 與 12 個循環不能因本報告或工具健康即宣稱全數可用。未確定的產品語義列為決策前置，不以推測補完。
