# 第 4 階段設計：BFF 變薄、寫入歸位、判斷交給 agent

狀態：**已核准**（2026-09-30）。依據：origin/dev `41774a87f` 上各服務實際存在的 API，行號見附註。

## 0. 原則（已核准）

1. **BFF 只做兩件事**：整理讀取資料給前端、把指令轉交給擁有該業務的服務。BFF 不持有業務狀態、不捏造結果。
2. **寫入回到擁有者服務**：governance、persona、capital、evolution、incidents 各自負責自己的寫入與驗證。
3. **判斷交給 agent**：agent 只能「讀資料＋提出請求」，不能直接改狀態；請求由人核准後，才由擁有者服務執行。
4. **核准＝紀錄＋執行前核對**：沿用 governance 服務已有的核准紀錄與核對函式，不再另建任何核准系統。
5. **資金護欄是一個小的確定性核心**：放在 capital 服務，不交給 LLM。
6. **下游不存在或捏造結果的功能直接刪**。
7. **每筆任務**帶淨行數上限；驗收涵蓋同一業務動作的**所有現存入口**。

## 1. 核准：紀錄＋核對

**現況**：
- governance 服務已有持久化的核准紀錄：`POST /api/governance/approvals`、`/{id}/review`、`/{id}/decide`、`/{id}/revoke`。
- 已有執行前的共用核對函式 `ApprovalEvidence.require_valid`，deployment、registry、runtime-manager、persona 的 training-target 在用。
- 核准紀錄沒有 rebalance、persona 生命週期這類「核准對象」型別，也沒有檢查「提案人不能自己核准」。
- BFF 另有一份只存在記憶體的假核准資料（`_created_approvals`），從不呼叫 governance。

**設計**：
- **唯一的核准紀錄**：governance 的 `approval_decisions`。
  - 新增核准對象型別：rebalance 套用、資金綁定啟用、persona 生命週期轉換、evolution 執行。
  - 新增規則：**決定者不能是提案人**。
- **唯一的核對方式**：擁有者服務在執行前呼叫 `ApprovalEvidence.require_valid`，確認四件事：
  - 核准確實存在；
  - 屬於同一個租戶；
  - 核准的就是**這一次的這個動作**（對象與內容相符）；
  - 沒有過期或被撤銷。

  只傳一個字串當 `approval_ref` 的做法全面停用。
- **雙人簽核**：一律由 governance 記錄，要求兩個不同的決定者。runtime 的 canary 和 live 沿用現有的 human gate（三個不同角色簽核）。
- **BFF**：刪掉 `_created_approvals`。`/api/v1/approval-decisions` 和 `/bff/approvals` 的清單、明細、建立、decide 全部轉給 governance。
- **confirm token 留在 BFF**：它的用途是「使用者再確認一次」，防止誤按，不是核准。

## 2. Persona 生命週期：agent 提議、人核准、服務套用

**現況**：
- persona 服務有 `PATCH /api/personas/{id}/lifecycle`，每種轉換各自限定角色。
- 生命週期狀態有：draft、research_only、consultable、paper_owner、live_owner、frozen、retired。
- 以核准編號授權的路徑沒有實作，永遠回 403。
- BFF 的 AdvanceLifecycle 呼叫一個不存在的端點；Promote、Demote、Observe 直接捏造收據。

**設計**：
1. **提議**：一個「persona 評估 agent」定期讀取績效與健康證據。
   - 它只能在 governance 建立「生命週期轉換」的核准請求，並附上證據與理由。
   - 做法比照監看 agent：排程、去重、限流、沒有任何寫入工具。
2. **核准**：人在既有的核准頁面做決定。
3. **套用**：
   - 前端或 agent 送出 AdvanceLifecycle，BFF 轉給 persona 服務的 `PATCH /lifecycle`，附上核准編號；
   - persona 服務用第 1 節的核對函式確認後才轉換狀態。
4. **刪除**：
   - Observe（它不是生命週期狀態）；
   - Promote 和 Demote 改成 AdvanceLifecycle 的目標狀態；
   - BFF 裡捏造收據的分支和呼叫不存在端點的程式。

## 3. 研究流程：agent 規劃、人核准、計算留在服務

**現況**：
- 計算已經是確定性的程式：回測、統計、QuantLib 由 research orchestrator 和 worker gateway 執行。
- 人要做的判斷有兩個：核准研究計畫、審候選股。目前任何有寫入權限的使用者都能核准。
- 產品裡沒有「讓模型呼叫工具去改狀態」的執行迴圈，但已經有 `invoke_structured`：只允許一個固定的「只能回傳資料」工具，其他一律拒絕。

**設計**：
- **servant 提研究計畫**：servant 用 `invoke_structured` 只回傳一份結構化的研究計畫草稿，**不需要新建通用的工具迴圈**。
  - BFF 用它在工作坊建立一份「提議中」的研究計畫；
  - 交易員在剛接好的研究面板上核准、派送；
  - 計算照舊由 orchestrator 執行。
- **研究計畫的核准限定角色**：只有工作坊的擁有者或 operator 可以核准，不再是任何有寫入權限的人。
- **本階段不動**：研究寫入目前是 BFF 行程內直接引用的函式庫（`research/write_owner.py`），搬到獨立服務後面留待之後。

## 4. 資金護欄核心

**現況**：
- capital 服務有 pools、bindings、rebalances、containments 的路由。
- 但核准只檢查「不是空字串」，不看 kill switch，已經寫好的 `risk_policy` 評估也從沒被呼叫。
- 資料表沒有租戶欄位。
- BFF 的資金寫入呼叫的函式根本不存在，而且沒有接上任何資金服務，所以全部回「不可用」。

**設計**：在 capital 服務新增一個小模組 `capital_guard`。所有會**增加風險**的動作都必須先通過它：
- 資金池啟用或狀態變更；
- binding 啟用；
- rebalance 套用；

`capital_guard` 依序檢查：
1. **核准**：用第 1 節的核對函式；live 的增加需要雙人簽核。
2. **kill switch 與 safe mode**：生效時拒絕任何增加風險的動作。
3. **限額**：呼叫現成的 `risk_policy`。
4. **租戶**：同租戶才可以操作。

**減少風險**的 containment 不需要核准，但仍然要附上證據、留下稽核紀錄。

BFF 的資金程式改成純轉送；呼叫不存在函式的 approve 和 two-man-sign 直接刪掉。

## 5. 只回收據、從不執行的入口

有了第 1 到第 4 節，每個這類入口都有明確的去向：

| 入口 | 去向 |
|---|---|
| `/bff/approvals/{id}/decide`、batch-decide | 轉給 governance `/api/governance/approvals/{id}/decide` |
| governance 的 review 類動作 | 轉給 governance 對應路由 |
| evolution 的 program 和 proposal 動作 | 轉給 evolution 服務現有的路由；execute 必須先核對核准 |
| experiment 和 job 動作 | 走單一路徑，由現有的 experiment／orchestrator 實作執行 |
| ranking、ranking formula 動作 | 下游是捏造的，**刪除** |
| incident、Agora、Sentinel 相關 | 已由進行中的任務處理 |

原則：**不再有任何入口回 202 卻沒有人執行**。能接上的就接上，接不上的回 410，並指出應改用哪個正式指令。

## 6. 完成後的 BFF 形狀

**保留**：
- 讀取整合；
- 單一指令入口：wrapper 在入口就改寫成正式指令；
- confirm token；
- 指令執行紀錄（CommandStore）；
- 轉送到各擁有者服務的轉接層。

**刪除**：
- BFF 自己的核准資料；
- 資金的假寫入；
- 捏造結果的 adapter 分支；
- 呼叫不存在端點的程式；
- `internal_api` 在本機捏造的 rollback 核准紀錄。

**本階段不處理，另外設計**：
- `personas/service.py`（16,200 行），包含只存在 BFF 的 provisioning 狀態；
- 研究寫入搬出 BFF。

## 7. 任務拆分與派工順序

每筆都帶淨行數上限，驗收涵蓋所有入口。

**A 批：擁有者服務，彼此不重疊，可同時派出**

| 任務 | 服務 | 內容 | 上限 |
|---|---|---|---|
| GOV-APPROVAL-TARGETS-001 | governance | 新增 4 種核准對象型別；決定者不能是提案人 | +120 |
| EVOLUTION-EXECUTE-APPROVAL-001 | evolution | execute 核對核准；actor 改由 token 取得，不再讀 body 自報的角色 | +60 |
| PERSONA-EVALUATOR-AGENT-001 | 新的排程 agent | 只能建立生命週期核准請求（等 GOV 完成） | +400 |

**B 批：擁有者核對（依賴 GOV-APPROVAL-TARGETS-001）**

| 任務 | 內容 | 上限 |
|---|---|---|
| PERSONA-LIFECYCLE-APPROVAL-VERIFY-001 | persona 服務實作以核准編號授權的轉換 | +60 |
| CAPITAL-GUARD-KERNEL-001 | capital 服務的 `capital_guard`：核准、kill switch、限額、租戶 | +250 |

**C 批：BFF 轉送（依賴對應的 B 批任務與 BFF-WRAPPER-EDGE-REWRITE-001）**

| 任務 | 內容 | 上限 |
|---|---|---|
| BFF-APPROVALS-FORWARD-001 | 刪 `_created_approvals`；所有核准入口轉給 governance | 負 |
| BFF-PERSONA-LIFECYCLE-FORWARD-001 | AdvanceLifecycle 轉給 persona `PATCH /lifecycle`；刪 Observe 和捏造分支 | 負 |
| BFF-CAPITAL-FORWARD-001 | 資金入口轉給 capital；刪不存在的 approve 和 two-man-sign | 負 |
| BFF-RECEIPT-ONLY-ROUTES-001 | 第 5 節的其餘入口：接上或回 410 | 負 |

**D 批：研究（可與 A 批同時）**

| 任務 | 內容 | 上限 |
|---|---|---|
| AGORA-SERVANT-RESEARCH-PROPOSAL-001 | servant 用 `invoke_structured` 產生研究計畫草稿，進入「提議中」 | +200 |
| AGORA-RESEARCH-APPROVAL-ROLE-001 | 研究計畫的核准限定工作坊擁有者或 operator | +30 |

預估：A、D 兩批確認後就能立刻派出 5 筆，B 批接著 2 筆，C 批 4 筆會在 wrapper 改寫合併後自動開工。

## 8. 已定案的決定（2026-09-30）

1. **資金資料表現在就加上租戶欄位**，含資料遷移；`capital_guard` 在服務端核對租戶。
2. **資金的雙人簽核**：由 governance 核准要求兩個不同的決定者，不另建簽核表。
3. **evolution**：這次只讓 execute 核對核准，並停止信任請求本文自報的角色；整體改成嚴格 JWT 另外開任務。
4. **進行中的 `BFF-APPROVAL-ENTRYPOINTS-TENANT-001` 照樣完成**；它的「沒有身分就拒絕」規則在改成轉送之後仍然適用。
5. **`personas/service.py`（16,200 行）本階段不動**，之後另外設計。

---
附註（主要依據）：
- governance：`services/governance/main.py:1465,1561,1573,1585`；`approval_authority.py:53`；`approval_decision.py:55-63`
- persona：`services/persona/write_owner.py:61-69,88-100,215,1633`
- capital：`services/capital/main.py:1042-1273`；`allocation_store.py:570-578`；`risk_policy.py`（未被呼叫）
- BFF：`governance/service.py:254,418-487`；`capital/router.py:310,328`；`command_adapters/persona_adapter.py:164-213`；`command_executor.py:554,1321,1351`
- agent：`openclaw-gateway-adapter/assistant_openclaw_provider.py:1208-1270`（`invoke_structured`）
