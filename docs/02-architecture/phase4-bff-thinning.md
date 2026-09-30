# 第 4 階段設計：BFF 變薄、寫入歸位、判斷交給 agent

狀態：**已核准**（2026-09-30），補入研究單一 owner、Persona 建議替換與 Agora 綜合判斷的任務範圍。原始設計依據為 origin/dev `41774a87f`；本次補充依據為 `648d0f93a`。未另註明的「現況」描述原始設計時的問題，不代表仍未修復；任務登錄、合併、部署及操作驗收分別以各自的實際紀錄為準。

## 0. 原則（已核准）

1. **BFF 只做兩件事**：整理讀取資料給前端、把指令轉交給擁有該業務的服務。BFF 不持有業務狀態、不捏造結果。
2. **寫入回到擁有者服務**：governance、persona、capital、evolution、incidents、research 各自負責自己的寫入與驗證。
3. **判斷交給 agent**：agent 只能「讀資料＋提出請求」，不能直接改狀態；請求由人核准後，才由擁有者服務執行。
4. **核准＝紀錄＋執行前核對**：沿用 governance 服務已有的核准紀錄與核對函式，不再另建任何核准系統。
5. **資金護欄是一個小的確定性核心**：放在 capital 服務，不交給 LLM。
6. **下游不存在或捏造結果的功能直接刪**。
7. **每筆任務**帶淨行數上限；驗收涵蓋同一業務動作的**所有現存入口**。
8. **移交即刪舊實作**：責任轉給 agent 或業務服務的同一筆交付，必須移除原本的判斷、寫入或派送實作；不以雙跑、相容旗標或舊規則 fallback 留下第二個 owner。

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
   - 做法比照監看 agent：排程、去重、限流；只提供建立請求的工具，不提供套用轉換、變更資金或核准自身請求的工具。
   - `PERSONA-EVALUATOR-AGENT-002` 正式接替尚未開工的 `PERSONA-EVALUATOR-AGENT-001`；先將 001 標為被 002 取代，再建立 002。只保留一個有效 evaluator 任務，不另做第二套建議來源。
   - evaluator 保存實際 provider 的建議、理由與來源識別；季度建議、排名頁及 Human Inbox 投影同一份結果，不在 GET 時重新產生建議。
   - 原本非生命週期的預算、工具或資金類建議只能保留為不可執行的報告內容；不為了保留舊標籤新增寫入能力或假造生命週期目標。
2. **核准**：人在既有的核准頁面做決定。
3. **套用**：
   - 前端或 agent 送出 AdvanceLifecycle，BFF 轉給 persona 服務的 `PATCH /lifecycle`，附上核准編號；
   - persona 服務用第 1 節的核對函式確認後才轉換狀態。
4. **刪除**：
   - Observe（它不是生命週期狀態）；
   - Promote 和 Demote 改成 AdvanceLifecycle 的目標狀態；
   - BFF 裡捏造收據的分支和呼叫不存在端點的程式。
   - 002 同時刪除 `personas/service.py` 的 `_pm12_recommendation_action_ids` 分數轉建議規則、其專用 helper，以及 `_pm12_quarterly_recommendation_item` 的模板理由生成；`_pm12_quarterly_recommendations` 改為讀取已保存的 evaluator 結果，移除不再使用的建議專用常數。

**保留與分工**：績效、風險、資格與排名的既有確定性公式仍是證據工具，不交給 LLM 重算。002 只接替建議生成與讀取投影；`PERSONA-LIFECYCLE-APPROVAL-VERIFY-001` 負責 owner 核准核對，`BFF-PERSONA-LIFECYCLE-FORWARD-001` 負責生命週期指令轉送，兩者不另建 evaluator。provisioning 狀態與其整體架構不在這次範圍。

## 3. 研究流程：agent 規劃、人核准、計算留在服務

**已接上的基礎與剩餘問題**：
- `AGORA-SERVANT-RESEARCH-PROPOSAL-001`（#6035）已合併，servant 已能透過既有 structured provider 提出研究計畫草稿；不能再把 agent gateway 描述為只做唯讀診斷。
- `AGORA-RESEARCH-APPROVAL-ROLE-001`（#6029）已合併，研究計畫核准限定工作坊擁有者或 operator。
- research 服務已有 `/api/research-orchestrator/tasks`、`/tasks/{id}/runs` 及 `/stages/{stage_type}/execute`；回測、統計與 QuantLib 等計算使用現有 adapter 與 worker gateway。
- BFF 的 Agora research service、dispatcher 仍自己管理派送與進度；策略工作坊另有建立 research task/run 的路徑。BFF 也直接建立 `services/research/write_owner.py` 的資料庫 owner，尚未真正做到研究服務唯一寫入。
- `/tasks/{id}/runs` 目前僅支援 stub 或已開啟的 offline 路徑，不能假設轉送到這個端點便等同保留現有 `/stages/{stage_type}/execute` 的真實計算能力。

**設計**：
- **servant 提研究計畫**：servant 用 `invoke_structured` 只回傳一份結構化的研究計畫草稿，**不需要新建通用的工具迴圈**。
  - BFF 用它在工作坊建立一份「提議中」的研究計畫；
  - 交易員在剛接好的研究面板上核准、派送；
  - 計算由既有 research 服務執行，agent 依真實結果提出解讀與報告。
- **研究計畫的核准限定角色**：只有工作坊的擁有者或 operator 可以核准，不再是任何有寫入權限的人。
- **單一 owner**：`BFF-RESEARCH-SINGLE-OWNER-001` 將研究計畫、run 派送／進度及研究 ticket、experiment、note 寫入交回既有 research 服務。保留的工作坊與研究入口採用同一組 owner task/run 識別，不各自維護執行狀態。
- **同次刪除**：移除 BFF `ResearchDispatcher` 的 orchestration、router 的 dispatcher 建立，以及 BFF 直接建立／匯入 `ResearchWriteOwner` 和直接寫研究資料表的路徑。必要的確定性執行與重試留在既有 owner，合併重複的 stage/backend 對照，不搬過去再留一份。
- **保留資料與能力**：沿用現有 durable store、ID、歷史、冪等與恢復／取消行為，不順帶搬資料或另建 queue。保留真實計算工具與固定公式；未設定或不支援的 backend 明確回不可用，不用 stub 結果冒充真實完成。BFF 重啟不應重跑已完成階段。
- **最小邊界**：沿用人核准的內容識別、租戶與既有 non-live 執行限制。假設、計畫提議與報告解讀使用既有 agent 能力；不新增通用 harness、工具迴圈、服務或核准系統。

### Agora 綜合判斷

`AGORA-SYNTHESIS-AGENT-001` 只替換 `agora/interaction/runner.py` 的語意綜合。現有 Persona 意見已由 provider 產生；不足之處是 `_synthesize` 仍以 conclusion 字串是否相同決定共識，再拼接理由當摘要。

- 使用既有 structured provider，根據實際意見與證據提出摘要、共識、分歧、條件與建議；同一 interaction／意見集合只保存一份綜合結果。
- 同次刪除結論字串比較、以第一份意見為基準的分歧判斷與拼接摘要；不保留這些規則作為模型不可用時的假綜合。
- 重用現有 `InteractionLifecycleStore`、工作坊結果及重試機制；原始意見始終可讀，不另建 synthesis 服務、store 或 queue。
- schema、引用的 opinion/evidence 是否存在，以及 provider 是否失敗仍由程式核對。provider 不可用或輸出無效時保留原始意見並標示綜合不可用；不授予核准、執行研究、修改 Persona 或資金的能力。
- 不改 Persona 個別意見生成、不重做 servant 草稿、不接手研究 owner，也不建立另一種前端操作模式。

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
- `internal_api` 在本機捏造的 rollback 核准紀錄；
- BFF 的研究 dispatcher 與直接研究資料庫 writer；
- Persona 分數轉建議及模板理由生成；
- Agora 以字串比較與拼接代替語意綜合的邏輯。

**本階段不處理，另外設計**：
- Persona provisioning 狀態的歸屬與整體遷移；
- `personas/service.py` 其餘無關功能的全面重寫。本次只開放第 2 節的建議替換與必要讀取投影，不以檔案很大為由順帶重構。

## 7. 任務拆分與派工順序

每筆都帶淨行數上限，驗收涵蓋所有入口。

**A 批：擁有者服務，彼此不重疊，可同時派出**

| 任務 | 服務 | 內容 | 上限 |
|---|---|---|---|
| GOV-APPROVAL-TARGETS-001 | governance | 新增 4 種核准對象型別；決定者不能是提案人 | +120 |
| EVOLUTION-EXECUTE-APPROVAL-001 | evolution | execute 核對核准；actor 改由 token 取得，不再讀 body 自報的角色 | +60 |

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

**D 批：已合併的研究基礎，不重複派工**

| 任務 | 內容 | 上限 |
|---|---|---|
| AGORA-SERVANT-RESEARCH-PROPOSAL-001 | #6035：servant 用 structured provider 產生研究計畫草稿，進入「提議中」 | +200 |
| AGORA-RESEARCH-APPROVAL-ROLE-001 | #6029：研究計畫的核准限定工作坊擁有者或 operator | +30 |

**E 批：補齊責任移交，舊實作必須隨同刪除**

| 任務 | 唯一負責範圍 | 依賴 | 淨 production 行數上限 |
|---|---|---|---|
| BFF-RESEARCH-SINGLE-OWNER-001 | 第 3 節的研究 owner、BFF 寫入／派送移除與同一組 task/run | AGORA-DEAD-SURFACES-REMOVAL-001、BFF-RECEIPT-ONLY-ROUTES-001 | 0；BFF 必須減少 |
| PERSONA-EVALUATOR-AGENT-002 | 第 2 節的單一 evaluator 與 BFF 建議替換；正式取代 001 | GOV-APPROVAL-TARGETS-001、AGORA-DEAD-SURFACES-REMOVAL-001 | +400；BFF 建議程式必須減少 |
| AGORA-SYNTHESIS-AGENT-001 | 既有 interaction 的 agent 綜合結果與舊 heuristic 刪除 | AGORA-DEAD-SURFACES-REMOVAL-001 | +100 |

Agora 清理依賴是為了先完成同區域的刪除，避免並行改寫；研究再等 receipt-only 清理完成，避免兩筆任務同時遷移 experiment/job 入口。這些是實作順序，不是新設的授權或安全 gate。E 批只補齊既有計畫的責任與驗收，不表示已經派出、實作或交付。

**E 批必做驗收**：

- 研究：mounted 測試涵蓋全部保留入口，重複派送只有一次 owner effect；驗證真實 adapter 呼叫、artifact 讀回、失敗與恢復，以及 BFF 不再建立 dispatcher／資料庫 owner。dev 用既有流程提議、人核准、派送一個支援的 non-live 計算，再於刷新及 BFF 重啟後讀回保存的 run／report。
- Persona：保留原任務的去重、限流、降級不開單及無直接寫入工具測試；新增相同保存建議出現在排名／Human Inbox 的 mounted 測試，以及「只有分數不能自行產生建議」。固定公式的回歸測試保留。dev 以真實證據與 provider 產生建議，刷新仍可讀；由人建立決定，套用轉換則由原 owner 任務負責。
- Agora：以「結論標籤相同但理由衝突」及「標籤不同但條件相容」驗證結果來自 provider；測試 provider 失敗仍可讀原意見、保存後讀回及重試不重做。dev 以兩個 Persona 的真實意見完成一次 interaction，刷新可讀同一份綜合結果。
- 三筆都以整筆差異計算上限，搬移同時計入新增與刪除；不得壓縮程式或刪有用測試湊數。分別報告 source、測試、合併、部署與真實操作結果；fixture 成功不能稱為真實 provider／計算驗收。

## 8. 已定案的決定（2026-09-30）

1. **資金資料表現在就加上租戶欄位**，含資料遷移；`capital_guard` 在服務端核對租戶。
2. **資金的雙人簽核**：由 governance 核准要求兩個不同的決定者，不另建簽核表。
3. **evolution**：這次只讓 execute 核對核准，並停止信任請求本文自報的角色；整體改成嚴格 JWT 另外開任務。
4. **進行中的 `BFF-APPROVAL-ENTRYPOINTS-TENANT-001` 照樣完成**；它的「沒有身分就拒絕」規則在改成轉送之後仍然適用。
5. **Persona 建議生成現在移交單一 evaluator**，002 正式接替 001 並同次刪除 BFF 舊判斷；`personas/service.py` 僅開放這個範圍，provisioning 仍另案處理。
6. **研究寫入與派送不再留作未規劃工作**，由 `BFF-RESEARCH-SINGLE-OWNER-001` 移交既有 research 服務並刪除 BFF 舊 owner。
7. **Agora 綜合判斷沿用現有 agent 能力**，由 `AGORA-SYNTHESIS-AGENT-001` 取代字串規則；不新增通用 agent 平台。

---
附註（主要依據）：
- governance：`services/governance/main.py:1465,1561,1573,1585`；`approval_authority.py:53`；`approval_decision.py:55-63`
- persona：`services/persona/write_owner.py:61-69,88-100,215,1633`
- capital：`services/capital/main.py:1042-1273`；`allocation_store.py:570-578`；`risk_policy.py`（未被呼叫）
- BFF：`governance/service.py:254,418-487`；`capital/router.py:310,328`；`command_adapters/persona_adapter.py:164-213`；`command_executor.py:554,1321,1351`
- agent：`openclaw-gateway-adapter/assistant_openclaw_provider.py:1208-1270`（`invoke_structured`）

本次增補依據（`648d0f93a`）：
- 研究：`services/control-plane/bff/agora/research/service.py:350`、`dispatcher.py:1062`、`router.py:69`；`agora/strategy_workshop/operations.py:227`；`services/research/main.py:797,1700`；`services/control-plane/bff/ports/research_knowledge_source.py` 的直接 owner 綁定。
- Persona 建議：`services/control-plane/bff/personas/service.py:5562,12377,13025`。
- Agora 綜合：`services/control-plane/bff/agora/interaction/runner.py:136,566`；既有 structured 呼叫可參考 `agora/servant/research_proposal.py:45`。
