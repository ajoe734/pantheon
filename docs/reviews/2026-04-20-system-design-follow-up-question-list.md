# 2026-04-20 System Design Follow-up Question Package For Architecture Team

## 目的

這份文件不是要重畫 Pantheon 高階藍圖。

這份文件的目的，是把目前仍會阻塞 implementation、BFF、frontend handoff、
readiness promotion、或 canonical truth 對齊的 system design 問題，整理成一份
可直接由系統設計團隊逐題回覆的正式問題包。

原則：

- 只列仍會改變 canonical contract、ownership、authority、degradation semantics、
  readiness semantics 的問題。
- 不重複已 ratify 且已可進 implementation 的項目。
- 把「現有 repo truth」與「系統設計回覆方向」有衝突的地方明確標出，避免工程端
  誤把未拍板內容偷渡成程式碼真相。

---

## 目前狀態總結

截至 `2026-04-20`，Pantheon 目前的整體判讀如下：

- 高階系統藍圖本身已大致成形，不需要再做一次全域重設計。
- 多數 workbench module 已離開 architecture bucket，主要工作已轉成 implementation、
  truth-hardening、UI activation、handoff activation、或 doc rebaseline。
- 真正仍屬 system design 未 fully closed 的，主要只剩：
  - 全域 canonical conventions 的最後收斂
  - 少數 cross-service ownership / authority boundary
  - 4 個 blocked modules 的 canonical contract closure
  - `CW-03` partial activation 與 readiness promotion 規則

---

## 已拍板事項，不需要再重問

以下結論已在現行 working blueprint 中視為已整合真相；除非系統設計團隊要正式推翻，
否則不應再回到 open question 狀態：

- `RW-05` 為 `contract_ready`
- `KW-02` / `KW-03` / `KW-04` 為 `contract_ready`
- `KW-05` 仍為 `blocked`
- `CW-03` 可在 `CW-02` 未 fully live 前做 partial activation
- `CW-03` full production handoff 仍需 `CW-02` transcript truth
- `EW-04`、`EW-05`、`RW-01`、`RW-02`、`RW-03`、`RW-04`、`CW-01`、`KW-01`、
  `TW-01`、`TW-03`、`TW-04` 不應再送回 architecture bucket

參考來源：

- [2026-04-20-system-design-open-questions-for-architecture-team.md](/home/lupin/code/pantheon/docs/reviews/2026-04-20-system-design-open-questions-for-architecture-team.md:1)
- [Pantheon_Response_to_Architecture_Team_Design_Input_List.md](/home/lupin/code/pantheon/docs/reviews/Pantheon_Response_to_Architecture_Team_Design_Input_List.md:1)
- [MODULE_READINESS_RATIFICATION_2026-04-20.md](/home/lupin/code/pantheon/MODULE_READINESS_RATIFICATION_2026-04-20.md:1)

---

## 需要系統設計團隊正式回覆的問題

以下問題仍需要 architecture / system design team 給出明確 decision wording。

每一題都包含：

- `為什麼現在仍未閉合`
- `需要拍板的決策`
- `若不拍板會卡住什麼`
- `建議最少交付`

---

## A. Global Canonical Conventions

### 1. Readiness ladder 與現有 repo vocabulary 的正式對映

為什麼現在仍未閉合：

- repo 現況仍混用 `contract-published`、`pending-bff`、`route-live`、`ready`、
  `shell-only`、`blocked`
- architecture 回覆又提出新的 readiness ladder 概念
- `CW-03` 還新增了 partial activation promotion rule

需要拍板的決策：

- Pantheon 正式 readiness ladder 是哪一組 enum
- 現有 repo vocabulary 要如何一對一對映到新 ladder
- `route-live` 是否只代表 runtime state，而非 module readiness
- `contract_ready`、`handoff_ready`、`implementation_ready` 是否為獨立層級

若不拍板會卡住什麼：

- backlog、packet family、frontend SA 會繼續各自定義 readiness
- supervisor / execution board 難以區分哪些模組可切 execution
- `CW-03` 這類 partial activation 模組會持續分類漂移

建議最少交付：

- 一份正式 mapping table
- 明文列出 `CW-03` 的特殊 promotion rule

---

### 2. `meta.surfaces.*` 的 degradation dictionary 是否保留 `partial`

為什麼現在仍未閉合：

- repo 目前同時出現 `ok | degraded | unavailable`
- 某些地方又出現 `fresh | stale | degraded | unavailable`
- 部分模組使用 `partial`
- 現有工作藍圖只確認「需要全域 dictionary」，但 dictionary 本身未 fully locked

需要拍板的決策：

- `partial` 是否為正式保留的 surface enum
- 哪些 surface 可使用 `partial`
- 哪些 surface 只能使用 `ok / degraded / unavailable`
- `fresh / stale` 是否也是 surface enum，或只應透過 staleness field 表達

若不拍板會卡住什麼：

- 前端會無法一致呈現 degraded badge、skeleton、fallback CTA
- BFF contract example 與 screen spec 會繼續各自寫不同 vocabulary
- rebaseline 會反覆出現「做了但文件仍互相矛盾」

建議最少交付：

- `DEGRADATION_DICTIONARY` 正式枚舉
- 一份 `surface_key -> allowed status set` 或通用規則

---

### 3. `stale` 應屬於 surface enum，還是 `meta.staleness` 衍生語義

為什麼現在仍未閉合：

- 有些契約把 `stale` 當 surface 狀態
- 有些契約把 `stale` 視為資料時間性訊號，與 surface availability 分開
- 這會直接影響前端是否把 `stale` 當 degraded 狀態渲染

需要拍板的決策：

- `stale` 是否允許直接出現在 `meta.surfaces.*`
- 若不允許，`meta.staleness` 的最小欄位集是什麼
- `stale` 與 `degraded` 同時存在時的優先語義是什麼

若不拍板會卡住什麼：

- BFF、frontend types、example payload 無法穩定
- 健康但非最新資料的頁面會在 badge / CTA / fallback 行為上持續分裂

建議最少交付：

- 一句明確原則
- 一個 detail payload 範例
- 一個 list payload 範例

---

### 4. Pagination canonical naming 是否維持 `page_info.next_page_token`

為什麼現在仍未閉合：

- architecture 回覆提出 `next_cursor`
- 現有 repo truth、tests、frontend types、example payload 大量使用
  `page_info.next_page_token`
- 若直接改 naming，會牽涉 migration 與 alias policy

需要拍板的決策：

- 目前 canonical naming 是否繼續使用 `page_info.next_page_token`
- 若未來要遷移到 `next_cursor`，遷移階段是否允許 alias
- `sort_by`、`sort_order`、`filters` 的 naming 是否需要一併鎖定

若不拍板會卡住什麼：

- 工程端會繼續在新舊 naming 之間搖擺
- conventions 文件與實作契約會持續不一致

建議最少交付：

- 正式 naming 決策
- 若改名，請附 migration policy

---

### 5. Shared response envelope 是否只規範最小公共外殼

為什麼現在仍未閉合：

- architecture 回覆範例使用 generic `id` / `title`
- 但現有模組 primary identity 實際上常為 `artifact_id`、`decision_id`、
  `request_id`、`session_id`
- 若強行統一成 generic detail shape，會扭曲 domain read model

需要拍板的決策：

- detail response 是否只要求最小公共外殼
- 是否允許 domain-specific primary identity 取代 generic `id`
- `title` 是否為 optional convenience field，而非 mandatory canonical field

若不拍板會卡住什麼：

- 後續新模組會不知道該遵守 domain truth 還是 generic envelope
- 文件可能為了追隨 convention 而寫出不真實的 example payload

建議最少交付：

- detail envelope 的 mandatory / optional field table
- 至少兩個對照 example：
  - 一個使用 domain-specific primary key
  - 一個同時帶 convenience summary field

---

### 6. `allowedActions` 是否正式維持 object-shaped flags

為什麼現在仍未閉合：

- architecture 回覆中的 shared example 曾使用 array
- 現有 repo truth、BFF contract、frontend types 幾乎全面採 object-shaped flags
- 這不只是格式問題，還會影響 CTA typing、feature gating、partial activation 呈現

需要拍板的決策：

- `allowedActions` 是否正式為 object-shaped flags
- flag naming 是否採 `canXxx` 規則
- 是否允許陣列型 action list 僅作輔助欄位，而非 canonical truth

若不拍板會卡住什麼：

- frontend types 很難穩定
- CTA render 與 permission gate 容易在不同模組出現不同寫法

建議最少交付：

- 一句正式原則
- 一個 object-shaped canonical example

---

## B. Ownership And Authority Boundaries

### 7. `LIN-002` 的 migration boundary 需再明文化到哪個層級

為什麼現在仍未閉合：

- working blueprint 已傾向指定 `services/lineage-read/` 為 UI-facing canonical read owner
- 但 migration boundary 仍未清楚寫到哪些既有 path 必須退出 UI truth
- 也尚未明文說明哪些 telemetry 路徑仍可作 internal substrate

需要拍板的決策：

- 哪些 UI-facing surfaces 僅能吃 `lineage-read`
- 哪些現有 telemetry lineage path 不可再被 BFF 直接消費
- `lineage-read` 是否允許包裝 telemetry substrate，但仍保持唯一外部 façade

若不拍板會卡住什麼：

- `EW-04` / lineage family 會持續存在第二條 truth path 風險
- 工程端可能在效能或方便性理由下繞過 canonical façade

建議最少交付：

- 一份 `producer / façade / consumer` boundary table
- 明列禁止的 direct-consume pattern

---

### 8. Persona boundary 的 canonical owner 要精確到什麼層級

為什麼現在仍未閉合：

- working blueprint 已經傾向 persona service 擁有 canonical persona object
- 但仍缺精確 object boundary 與 BFF aggregation boundary 的正式 wording

需要拍板的決策：

- 哪些 object 必須由 persona service 擁有
- 哪些欄位可只存在於 BFF composed read model
- `PersonaSession` metadata 與 policy refs 是否明確屬於 upstream locked truth

若不拍板會卡住什麼：

- persona service 與 BFF 之間會持續出現「誰才是 canonical owner」的灰區
- 新增 persona-facing screens 時容易把 convenience rollup 誤當 authority truth

建議最少交付：

- 一張 `canonical object / composed read model` 切分表
- 一份 minimal object inventory

---

### 9. Router / gateway / governance enforcement ownership 是否需要 command matrix

為什麼現在仍未閉合：

- 目前雖已有方向性結論：gateway 管 ingress、router 管 routing、governance 管 approval
- 但 transport TTL、domain TTL、approval authority、rate limit、fallback classifier
  的 owner 邊界還不夠操作化

需要拍板的決策：

- transport TTL 與 domain TTL 各由誰負責
- rate limit 與 command validity 的 owner 分離方式
- approval authority 與 surrogate / fallback 行為的邊界
- local intent classifier 何時只屬 degraded fallback，何時完全不可作 canonical truth

若不拍板會卡住什麼：

- implementation 端會在 router、gateway、governance 之間各自補判斷
- incident / approval / routing 類模組會持續有 authority drift

建議最少交付：

- 一張 command / approval / TTL / throttle matrix
- 明列「誰不能做什麼」

---

## C. Blocked Module Contract Closure

### 10. `CW-02 Debate Transcript`

為什麼現在仍未閉合：

- append-only `TranscriptEvent` schema 未 fully ratify
- ordering semantics 仍不夠明確
- actor labeling 與 inline evidence-link semantics 尚未鎖
- `partial transcript` 是否是正式 degraded mode 也尚未拍板

需要拍板的決策：

- `TranscriptEvent` 最小欄位集
- ordering / stable cursor / append-only rule
- actor labeling 是否完全由 BFF resolve
- evidence-link 的 canonical embedding rule
- `partial transcript` 的 surface semantics

若不拍板會卡住什麼：

- `CW-02` 本身不能進 implementation
- `CW-03` 也只能維持 partial activation，不能做 full production handoff

建議最少交付：

- 一份 `docs/bff/CW-02-*.md`
- 一個 detail response example
- 一個 list / stream / cursor example

---

### 11. `CW-04 Red-team Memo`

為什麼現在仍未閉合：

- `session_to_memo_mapping` 尚未定義 canonical object shape
- `allowedActions.canInitiateGovernanceReview` gating rule 未鎖
- `ConsultMemo` lifecycle 是否嚴格只保留 `draft -> published` 未明文定案

需要拍板的決策：

- canonical memo read model 最小欄位集
- `session_to_memo_mapping` 是否為一等欄位，以及其結構
- governance review initiation 的 gate rule
- memo lifecycle 與 state naming

若不拍板會卡住什麼：

- `CW-04` implementation 會被迫自己發明 governance handoff semantics
- 前端無法誠實渲染 CTA 與 publish / escalation boundary

建議最少交付：

- 一份 `CW-04` canonical contract
- 一個 governance handoff example payload

---

### 12. `TW-02 Parameter Controls`

為什麼現在仍未閉合：

- patch semantics 仍不清楚是 partial patch 還是 replace-style
- invalid / rejected patch response shape 未鎖
- diff payload 是否固定為 `updated_controls[]` 仍未決

需要拍板的決策：

- canonical patch semantics
- validation failure / reject / noop 的回應語義
- diff payload 與 preview payload 的最小 shape
- mutation authority 與 revert / discard boundary

若不拍板會卡住什麼：

- `TW-02` implementation lane 不能開始
- `TW-03` compare 與 `TW-04` replay/commit/discard 的邊界容易被錯誤前提污染

建議最少交付：

- 一份 read contract
- 一份 write / patch contract
- 至少三個 example：
  - valid patch
  - invalid patch
  - rejected patch

---

### 13. `KW-05 Strategy Spec`

為什麼現在仍未閉合：

- version identity、ancestry、lifecycle、compare semantics 仍屬 architecture-sensitive
- 這不是單頁 contract，而是整個 versioned strategy spec truth 的核心語義

需要拍板的決策：

- canonical version identifier
- parent / ancestor / superseded 關係表示法
- lifecycle state
- compare semantics 與 diff granularity
- 哪些 write path 可建立新 version，哪些只能 mutate draft

若不拍板會卡住什麼：

- `KW-05` implementation 若先行，後續可能整包重寫
- `KW-02` / `KW-03` / `KW-04` 後續也可能因 spec truth 漂移而返工

建議最少交付：

- 一份 version model decision
- 一份 `KW-05` contract skeleton

---

## D. Promotion Rules And Readiness Transitions

### 14. `CW-03` partial activation 的正式 promotion ladder

為什麼現在仍未閉合：

- working blueprint 已接受 `CW-03` 可 partial activate
- 但尚未有正式文件明確列出哪些 surfaces 屬 partial-safe
- 也尚未明文定義何時才可從 partial-ready 升到 full module-ready

需要拍板的決策：

- `CW-03` 哪些 surfaces 在缺 `CW-02` transcript truth 時仍可上線
- 哪些 surfaces 必須等 `CW-02` live
- `CW-03 route-live != full module-ready` 是否要升格成正式 conventions wording

若不拍板會卡住什麼：

- `CW-03` handoff、frontend activation、dashboard readiness 都會持續飄移

建議最少交付：

- 一個 promotion table
- 一個 packet / SA / backlog 的統一 wording

---

### 15. `contract_ready` 與 `implementation_ready` 是否必須分離

為什麼現在仍未閉合：

- 現有 repo 與討論中，常把 contract published、pending BFF、route live、ready 混在一起
- 但實際 dispatch execution task 時，需要更清楚的切割條件

需要拍板的決策：

- module 何時算 `contract_ready`
- module 何時算 `implementation_ready`
- `screen_ready`、`handoff_ready` 是否需要保留
- supervisor / execution 切 task 時，最低 readiness 門檻是什麼

若不拍板會卡住什麼：

- execution board 仍可能把 blocked module 誤切成 implementation task
- 或把明明可以開工的模組繼續錯放在 architecture bucket

建議最少交付：

- readiness ladder 定義表
- `may materialize execution task? yes/no` 的對應規則

---

## 建議系統設計團隊回覆格式

建議逐題回覆以下欄位：

- `decision`
- `canonical wording`
- `affected modules`
- `affected files or doc families`
- `implementation impact`
- `migration impact`
- `whether a new decision doc is required`

若有題目需要拆成兩步，也請直接標註：

- `interim rule`
- `final rule`
- `what work may proceed before final rule`

---

## 建議回覆優先順序

若需要分批回覆，建議按以下順序：

1. Global conventions：`1` 到 `6`
2. Ownership / authority：`7` 到 `9`
3. Blocked modules：`10` 到 `13`
4. Promotion rules：`14` 到 `15`

原因：

- `1` 到 `6` 會影響所有後續 canonical contract wording
- `7` 到 `9` 會影響 BFF 與 service boundary
- `10` 到 `13` 直接決定 blocked modules 能否解鎖
- `14` 到 `15` 直接決定 readiness truth 與 execution dispatch 規則

---

## 附註

這份 follow-up question package 的目的，是讓 architecture team 回答最後仍未 fully
closed 的 system design 問題；不是要求 architecture team 接手 implementation。

一旦以上問題完成回覆，Pantheon 目前剩餘的大部分差距，就可更明確地下放到：

- BFF implementation
- frontend implementation
- handoff activation
- rebaseline / closeout

而不是繼續停留在藍圖層反覆討論。

---

## 附錄：2026-04-21 實作進度 vs 完整實作藍圖快照

這個附錄不是新增 architecture open question。

它的目的，是把「目前實作進度」和「完整藍圖剩餘差距」分開，避免把 delivery、
runtime refresh、doc drift 誤判成 system design blocker。

本附錄的判讀優先順序：

- 高階藍圖 truth：`ROADMAP.md`、`DEVELOPMENT_WORKBREAKDOWN.md`
- 模組級 productization truth：`WORKBENCH_DELIVERY_BACKLOG.md`
- 最新 execution truth：`current-work.md`
- repo implementation truth：
  - `services/control-plane/bff/main.py`
  - `/home/lupin/code/front-ai-trading-system/src/App.tsx`
  - `/home/lupin/code/front-ai-trading-system/src/components/AppSidebar.tsx`

若本附錄與 `2026-04-20-current-implementation-vs-blueprint-gap-audit.md` 有衝突，
應優先採 `2026-04-21` 的 repo / execution truth。

### A. 一句話結論

- 若以 `ROADMAP.md` + `DEVELOPMENT_WORKBREAKDOWN.md` 為完整藍圖主幹，
  Pantheon 現在的差距已不是 phase backbone 未 materialize，而是 product surface
  closeout、runtime refresh、handoff activation、與 doc rebaseline。
- 若以 `WORKBENCH_DELIVERY_BACKLOG.md` 的模組級藍圖為準，剩餘差距已集中到：
  - 少數 genuinely unimplemented / blocked modules
  - 已 live route 的 frontend activation / review closeout
  - readiness / backlog / packet / overview truth drift

### B. 相較 2026-04-20 盤點，實作已往前推進的地方

- `RW-05` 已不只是 `contract_ready`；artifact list / detail / compare route 已 live。
- `KW-02` 已不只是 `contract_ready`；notes create / list / detail route 已 live。
- `KW-03` 已不只是 `contract_ready`；evidence list / detail route 已 live。
- front main app 已掛上 `RW-01` 到 `RW-04`、`TW-01`、`TW-04`、`CW-03`、`EW-04`
  等 canonical route。
- `InspirationGraph` 已是實際頁面實作，不再是舊 placeholder shell。

這代表部分舊文件裡的 `pending BFF implementation` 或 `shell only` 判讀，
已落後於 repo 真相。

### C. 模組級差距矩陣

| Family | Module | Current implementation truth | Difference from full blueprint | Gap type |
|---|---|---|---|---|
| Evolution | `EW-04` | BFF live，front route/page 已存在，handoff rebaseline 進入 review | 最後的 handoff / record sync 尚未 fully finalized | delivery / doc sync |
| Evolution | `EW-05` | BFF/live contract 與 frontend loop 已基本收斂 | 無 major blueprint gap；主要剩 closeout truth | closeout |
| Research | `RW-01` | BFF live，front route/page 已存在 | 目前卡在 runtime refresh；live probe 與 route availability 未完全對齊 | runtime refresh |
| Research | `RW-02` | BFF live，front route/page 已存在，task 已 `review_approved` | 剩 finalize disposition 與 closeout | review closeout |
| Research | `RW-03` | BFF live，front route/page 已存在 | frontend review 尚未關閉 | review |
| Research | `RW-04` | BFF live，front route/page 已存在 | review / follow-up 與 runtime refresh 尚未 fully close | review + runtime |
| Research | `RW-05` | BFF live | 前端 surface / packetization 尚未真正打開；部分文件仍描述成 pending BFF | frontend activation + doc drift |
| Knowledge | `KW-01` | BFF live，front route/page 已存在，coordination loop 已 `loop_complete` | 主要剩 truth-hardening / closeout record sync | closeout |
| Knowledge | `KW-02` | BFF live | 尚無前端 surface；相關 readiness / overview 文件仍落後 | frontend activation + doc drift |
| Knowledge | `KW-03` | BFF live | 尚無前端 surface；相關 readiness / overview 文件仍落後 | frontend activation + doc drift |
| Knowledge | `KW-04` | 尚未 live；active work 仍為 `todo` | 仍屬 genuine backend implementation gap | implementation |
| Knowledge | `KW-05` | `blocked` | versioned strategy spec contract 仍屬 architecture-sensitive gap | architecture blocker |
| Consultation | `CW-01` | BFF live，front route/page 已存在 | returned UI follow-up 仍待收斂 | review / republish |
| Consultation | `CW-02` | 尚未 live | transcript route / append-only schema / actor labeling 仍缺 | architecture + implementation |
| Consultation | `CW-03` | partial-live，front route/page 已存在 | full production handoff 仍受 `CW-02` transcript truth gate | partial activation gate |
| Consultation | `CW-04` | 尚未 live | memo lifecycle / governance handoff contract 仍缺 | architecture + implementation |
| Trainer | `TW-01` | BFF live，front route/page 已存在 | runtime refresh 與 front publication follow-up 仍未 fully close | delivery |
| Trainer | `TW-02` | 尚未 live | controls contract / patch semantics / route family 仍缺 | architecture + implementation |
| Trainer | `TW-03` | BFF live，handoff 已 rebaseline，當前 `waiting_for_lovable` | UI lane 尚未完成 | frontend activation |
| Trainer | `TW-04` | BFF live，front route/page 已存在 | runtime / route-topology follow-up 仍待關閉 | delivery |

### D. 最新差距分類

把「Pantheon 與完整藍圖的差距」重新分類後，較準確的分桶如下：

1. 主藍圖骨架差距：已非主要問題
   - canonical roadmap / work breakdown row coverage 可視為基本完成
   - 現在不是缺 phase backbone，而是缺 module closure

2. 真正未實作或仍 blocked 的模組差距
   - `KW-04`
   - `KW-05`
   - `CW-02`
   - `CW-04`
   - `TW-02`

3. 已有 live backend，但仍缺 frontend activation 的模組差距
   - `RW-05`
   - `KW-02`
   - `KW-03`
   - `TW-03`

4. 已有 live backend 與 frontend route，但仍未 fully close 的 delivery 差距
   - `RW-01`
   - `RW-02`
   - `RW-03`
   - `RW-04`
   - `CW-01`
   - `TW-01`
   - `TW-04`
   - `EW-04`

5. 文件 truth drift 差距
   - readiness ratification、workbench backlog、packet family、overview examples、
     frontend SA 之間，仍有部分 wording 落後於 repo 真相
   - 這類差距不一定代表 code 未做，但已足以誤導下一輪 execution dispatch

### E. 目前仍屬 system design 的差距

截至 `2026-04-21`，真正仍應留在 architecture / system design lane 的，
仍是原 question package 這一層：

- global readiness / degradation / envelope conventions
- ownership / authority boundary wording
- blocked modules 的 contract closure：
  - `CW-02`
  - `CW-04`
  - `TW-02`
  - `KW-05`
- `CW-03` 的正式 promotion / readiness wording

換句話說：

- `RW-05`、`KW-02`、`KW-03` 這些已不應再被視為「還在 architecture bucket」
- `RW-01` 到 `RW-04`、`TW-01`、`TW-04` 的主要差距也已不是 architecture，
  而是 runtime refresh、frontend review、handoff activation、或 closeout
- 真正還沒 fully locked 的 blueprint 問題，已縮到 conventions / ownership /
  blocked-module contract 這一層

### F. 對「現在實作進度與完整實作藍圖差異」的最新判讀

若直接回答這個問題，較準確的版本應是：

1. Pantheon 與完整藍圖的主差距，已不是「還缺高階架構」，而是 workbench module
   的最後一哩 productization。
2. 真正仍未實作的 backend / contract gap，已縮到 `KW-04`、`CW-02`、`CW-04`、
   `TW-02`，以及 blocked 的 `KW-05`。
3. `RW-05`、`KW-02`、`KW-03` 的差距，已從「BFF 未做」轉成「前端未開 +
   文件未更新」。
4. `RW-01` 到 `RW-04`、`TW-01`、`TW-04` 的差距，已主要是 runtime refresh、
   frontend review、handoff activation、或 closeout，不應再被誤判成
   architecture materialization 未完成。
5. 文件 truth drift 本身已成為實際 delivery risk；凡仍把 live module 寫成
   `pending-bff` 或 `shell-only` 的文件，都應視為需 rebaseline 的真實工作項。
