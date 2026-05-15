# 2026-04-20 Current Implementation vs Blueprint Gap Audit

## 目的

這份文件回答四件事：

1. 目前 Pantheon 距離「完整系統開發藍圖」還差什麼。
2. 盤點時同時核對 repo 內的真實實作與 blueprint / backlog / packet / SA 文件。
3. 把「真的還沒做」和「其實做了但文件還沒更新」分開。
4. 整理出接下來需要補的實作項目，以及仍屬 system design / blueprint 不清楚的地方。

---

## 方法

這次盤點同時交叉檢查了：

- blueprint / planning truth：
  - `WORKBENCH_DELIVERY_BACKLOG.md`
  - `docs/lovable/PANTHEON_FRONTEND_SA.md`
  - `MODULE_READINESS_RATIFICATION_2026-04-20.md`
  - `docs/reviews/2026-04-20-system-design-open-questions-for-architecture-team.md`
  - relevant packet family docs and overview examples
- backend implementation truth：
  - `services/control-plane/bff/main.py`
  - route-level live decorators and composed payload wiring
- frontend implementation truth：
  - `/home/lupin/code/front-ai-trading-system/src/App.tsx`
  - workbench pages and sidebar IA
- execution / delivery truth：
  - `current-work.md`
  - coordination / handoff file existence

---

## 先講結論

Pantheon 目前離完整藍圖的差距，不是「主藍圖還沒展開」，而是以下四類工作還沒閉環：

1. **frontend canonical route coverage 還明顯不足**
   - 後端已有多個 route-live 模組，但前端主應用仍停留在舊 IA、placeholder 或 demo page。
2. **部分 backend 模組真的還沒落地**
   - 主要是 `RW-05`、`KW-02` 到 `KW-05`、`CW-02`、`CW-04`、`TW-02`。
3. **文件 truth drift 很嚴重**
   - backlog、frontend SA、packet family、overview example JSON 之間，對同一模組的 ready / blocked / route-live 判斷互相衝突。
4. **仍有少數 blueprint 問題沒有完全拍板**
   - 主要是全域 conventions、lineage ownership、persona boundary、router enforcement ownership，以及少數 blocked 模組的 contract 還沒 fully locked。

一句話總結：

> Pantheon 的主差距已經從「畫藍圖」轉成「把 live backend 真相接進正確前端 IA、把剩餘未實作模組補完、並把文件全面 rebaseline」。

---

## 1. 真實開發進度盤點

### 1.1 已有明確 backend / route implementation 的模組

以下模組可直接從 `services/control-plane/bff/main.py` 確認 live route：

- `CW-01`
  - `POST /api/v1/consult/requests`
  - `GET /api/v1/consult/requests`
  - `GET /api/v1/consult/requests/{request_id}`
  - `POST /api/v1/consult/requests/{request_id}/cancel`
  - evidence: `services/control-plane/bff/main.py:5793-5964`
- `CW-03`
  - `GET /api/v1/committees`
  - `GET /api/v1/committees/{committee_id}`
  - evidence: `services/control-plane/bff/main.py:5967-6037`
- `RW-01`
  - create/list/detail/patch routes live
  - evidence: `services/control-plane/bff/main.py:6049-6192`
- `RW-02`
  - search route live
  - evidence: `services/control-plane/bff/main.py:6195-6277`
- `RW-03`
  - analysis list/detail routes live
  - evidence: `services/control-plane/bff/main.py:6280-6377`
- `RW-04`
  - launch/list/detail/cancel routes live
  - evidence: `services/control-plane/bff/main.py:6465-6647`
- `KW-01`
  - institutional memory list/detail routes live
  - evidence: `services/control-plane/bff/main.py:6670-6760`
- `TW-01`
  - trainer session create/list/detail/message routes live
  - evidence: `services/control-plane/bff/main.py:4688-4879`
- `TW-03`
  - trainer preview read/refresh routes live
  - evidence: `services/control-plane/bff/main.py:4882-4983`
- `TW-04`
  - trainer replay list/detail/commit/discard routes live
  - evidence: `services/control-plane/bff/main.py:4985-5145`
- `EW-05`
  - mutation review read route live
  - evidence: `services/control-plane/bff/main.py:7749-7812`
- `EW-04`
  - inspiration graph route live
  - evidence: `services/control-plane/bff/main.py:7904-7920`

### 1.2 前端真實進度

前端主應用目前已掛上的 canonical / semi-canonical routes 很有限：

- 已存在的較新 Pantheon workbench routes：
  - `/evolution/mutation-review/:decision_id`
  - `/evolution/inspiration/:artifact_id`
  - `/knowledge/memory`
  - `/knowledge/memory/:entry_id`
  - `/consultation/requests`
  - `/consultation/requests/:request_id`
  - evidence: `/home/lupin/code/front-ai-trading-system/src/App.tsx:119-155`

但更大的真相是：

- Research 仍只有單一 `/research` 舊頁面，沒有 canonical 的
  - `/research/tickets`
  - `/research/search`
  - `/research/analyze`
  - `/research/experiments`
- Trainer 仍只有單一 `/trainer` 舊頁面，沒有 canonical 的
  - `/trainer/sessions`
  - `/trainer/sessions/:session_id`
  - `/trainer/replay`
- Consultation 沒有
  - `/consultation/committees`
  - `/consultation/memos`
- Knowledge 沒有
  - `/knowledge/notes`
  - `/knowledge/evidence`
  - `/knowledge/insights`
  - `/knowledge/strategy-specs`

evidence: `/home/lupin/code/front-ai-trading-system/src/App.tsx:121-159`

另外，sidebar IA 也還是舊的：

- Research 只有 `/research` 和 `/memory`
- Trainer 只有 `/trainer`
- Inspiration 還被標成 `comingSoon`
- evidence: `/home/lupin/code/front-ai-trading-system/src/components/AppSidebar.tsx:56-85`

### 1.3 前端頁面品質真相

有兩個特別重要的實作證據：

- `InspirationGraph` 仍是明確 placeholder，直接寫著 route 未 live、仍 blocked-shell
  - 但 backend route 其實已 live
  - evidence:
    - placeholder page: `/home/lupin/code/front-ai-trading-system/src/pages/inspiration/Graph.tsx:17-25`, `42-50`, `75-80`
    - live route: `services/control-plane/bff/main.py:7904-7920`
- `Research` 舊頁面仍走 legacy monolith flow，不是 blueprint 中拆分後的 RW-01/02/03/04 canonical screens
  - top comment 還在寫 `GET /api/research/search`, `POST /api/research/analyze`, `POST /api/execute`
  - evidence: `/home/lupin/code/front-ai-trading-system/src/pages/research/Research.tsx:1-8`
- `Trainer` 舊頁面仍直接接 demo provider，而不是 canonical BFF trainer workflow
  - evidence: `/home/lupin/code/front-ai-trading-system/src/pages/trainer/Trainer.tsx:16-22`

### 1.4 Execution truth

`current-work.md` 顯示目前 execution 真相是：

- `EW-05` 前端實作已回到 review
- `KW-01` 前端實作已回到 review
- `PKT-003 inspiration graph`、`RW-01`、`RW-02`、`TW-01` 仍在 todo / waiting lane
- `RW-04`、`TW-04` handoff rebaseline 仍在進行
- Lovable coordination 仍有 `6` 個 waiting-for-lovable features
- evidence:
  - active execution tasks: `current-work.md:53-72`
  - task board latest state: `current-work.md:86-105`
  - coordination counts: `current-work.md:136-142`

---

## 2. 與完整系統藍圖的主要落差

這裡分成三種差距：

1. 真的還沒做
2. 做了但前端沒接
3. 做了但文件還寫錯

### 2.1 真的還沒做的 backend / product slices

這些是實作上真正還缺的部分：

- `RW-05 Artifact Compare`
  - blueprint ratification 已視為 `contract_ready`
  - 但實際 BFF route 仍不存在：沒有 `/api/v1/artifacts*` live route
  - evidence:
    - ratification: `MODULE_READINESS_RATIFICATION_2026-04-20.md:21`
    - backlog still lists it as missing: `WORKBENCH_DELIVERY_BACKLOG.md:67`
- `KW-02 Research Notes`
  - ratified `contract_ready`
  - 但 BFF live route 未出現在 `main.py`
  - evidence:
    - ratification: `MODULE_READINESS_RATIFICATION_2026-04-20.md:25`
    - route only appears as missing/stale references, not live decorator
- `KW-03 Evidence Refs`
  - ratified `contract_ready`
  - BFF live route still missing
  - evidence: `MODULE_READINESS_RATIFICATION_2026-04-20.md:26`
- `KW-04 Insight Cards`
  - ratified `contract_ready`
  - BFF live route still missing
  - evidence: `MODULE_READINESS_RATIFICATION_2026-04-20.md:27`
- `KW-05 Strategy Spec`
  - 仍是 `blocked`
  - blueprint 本身尚未 fully ratify
  - evidence: `MODULE_READINESS_RATIFICATION_2026-04-20.md:28`
- `CW-02 Debate Transcript`
  - transcript route and append-only event truth still missing
  - evidence:
    - ratification blocked: `MODULE_READINESS_RATIFICATION_2026-04-20.md:22`
    - packet family still lists transcript route missing: `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md:102-120`
- `CW-04 Red-team Memo`
  - memo routes and governance handoff signal still missing
  - evidence:
    - ratification blocked: `MODULE_READINESS_RATIFICATION_2026-04-20.md:23`
    - packet family missing routes: `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md:173-194`
- `TW-02 Parameter Controls`
  - 仍 blocked，contract 沒 fully locked
  - evidence: `MODULE_READINESS_RATIFICATION_2026-04-20.md:24`

### 2.2 後端已做，但前端 canonical UI 還沒接上的模組

這是目前最大的 productization gap：

- `EW-04 Inspiration Graph`
  - backend live
  - front page still placeholder and still說 route 未 live
- `RW-01 Research Ticket`
  - backend live
  - front app 還沒有 `/research/tickets` route
- `RW-02 Search`
  - backend live
  - front app 還沒有 `/research/search` route
- `RW-03 Analyze`
  - backend live
  - front app 還沒有 `/research/analyze` route
- `RW-04 Experiment Launch`
  - backend live
  - front app 還沒有 `/research/experiments` route
  - handoff bundle / templates 也仍在補
  - evidence: `current-work.md:94`
- `KW-01 Institutional Memory`
  - front implementation已回 review，但還沒 fully close
  - evidence: `current-work.md:87`, `149`
- `TW-01 Teaching Dialog`
  - backend live
  - front app 還沒有 `/trainer/sessions` routes
- `TW-03 Before/After Compare`
  - backend live
  - front canonical screen不存在，handoff bundle也未齊
  - evidence: `docs/pantheon-handoffs/TW-03-before-after-compare/FRONTEND_CHANGE_SPEC.md` 不存在
- `TW-04 Teaching Replay`
  - backend live
  - front canonical screen不存在，handoff bundle缺漏
  - evidence:
    - routes live: `services/control-plane/bff/main.py:4985-5145`
    - handoff missing review: `current-work.md:96`
- `CW-03 Committee Board`
  - backend list/detail live
  - front app 沒有 `/consultation/committees*`
  - 同時還需要遵守 partial activation rule

### 2.3 已做但文件還寫錯的地方

這一類不是 implementation 缺口，但會直接造成錯誤派工與錯誤判讀。

#### Backlog drift

`WORKBENCH_DELIVERY_BACKLOG.md` 仍把下列項目寫錯：

- `RW-05` 還寫成 `not ready`
  - 但 ratification 已是 `contract_ready`
  - evidence:
    - backlog: `WORKBENCH_DELIVERY_BACKLOG.md:67`
    - ratification: `MODULE_READINESS_RATIFICATION_2026-04-20.md:21`
- `KW-02` 到 `KW-05` 還寫成 `module not ready`
  - 但 `KW-02` 到 `KW-04` 已 ratify 成 `contract_ready`
  - evidence:
    - backlog: `WORKBENCH_DELIVERY_BACKLOG.md:76-79`
    - ratification: `MODULE_READINESS_RATIFICATION_2026-04-20.md:25-28`
- `CW-03` 還寫成必須完全等 `CW-01` 與 `CW-02` live 才能前進
  - 但 ratification 已允許 partial activation
  - evidence:
    - backlog: `WORKBENCH_DELIVERY_BACKLOG.md:89`
    - ratified gate rule: `MODULE_READINESS_RATIFICATION_2026-04-20.md:32-40`
- `TW-03` / `TW-04` 還寫成 pending BFF implementation
  - 但實際 route 已 live
  - evidence:
    - backlog: `WORKBENCH_DELIVERY_BACKLOG.md:98-99`
    - live routes: `services/control-plane/bff/main.py:4882-5127`

#### Frontend SA drift

`docs/lovable/PANTHEON_FRONTEND_SA.md` 仍把多條已拍板或已 live 的模組寫成 blocked / shell-only：

- workbench role summary 把 `RW-05`、`KW-02~05`、`CW-03` 寫成 blocked / module-gated old truth
  - evidence: `docs/lovable/PANTHEON_FRONTEND_SA.md:109-112`
- navigation 仍把 Research / Knowledge / Consultation / Trainer 想成 blocked shell hierarchy
  - evidence: `docs/lovable/PANTHEON_FRONTEND_SA.md:137-140`
- route map 仍把 `/research/experiments` 寫成 pending BFF placeholder
  - evidence: `docs/lovable/PANTHEON_FRONTEND_SA.md:293`
- route map 仍把 `/knowledge/notes`、`/knowledge/evidence`、`/knowledge/insights` 全寫成 blocked shell only
  - evidence: `docs/lovable/PANTHEON_FRONTEND_SA.md:295-303`
- route map 仍把 `CW-03` 視為 full gate
  - evidence: `docs/lovable/PANTHEON_FRONTEND_SA.md:305-306`

#### Packet family drift

- `KW-006` packet family 明顯 overclaim
  - header直接寫「all ready」
  - `KW-02` 到 `KW-05` 都寫成 `ready / implemented / resolved`
  - 但實際上 `KW-02~04` 只是 `contract_ready`, `KW-05` 仍 blocked
  - evidence:
    - header/inventory: `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md:8`, `44-48`
    - overclaim in sections: `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md:96-107`, `125-136`, `154-165`, `183-195`, `208-223`
- `CW-008` packet family 對 `CW-03` 也落後於實作
  - header / inventory 還寫 `contract-published; pending-bff`
  - backend gaps table 還寫 `GET /api/v1/committees*` missing
  - 但實際 live route 已存在
  - evidence:
    - packet family: `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md:8`, `41-44`, `136-157`, `213-217`
    - live routes: `services/control-plane/bff/main.py:5967-6037`

#### Overview example JSON drift

- `docs/examples/PKT-consultation-workbench.json` 仍把 `CW-01` 說成 not ready、把 `CW-03` 說成 missing
  - evidence: `docs/examples/PKT-consultation-workbench.json:11-33`, `54-66`
- `docs/examples/PKT-knowledge-workbench.json` 仍把 `KW-02~05` 一律視為 blocked / not_ready
  - evidence: `docs/examples/PKT-knowledge-workbench.json:11-18`, `37-101`

---

## 3. 現在距離完整藍圖還差哪些實作項目

下面只列 implementation work，不把 doc-only rebaseline 混進來。

### 3.1 Frontend canonical route and page completion

需要補的前端實作：

- `EW-04` production inspiration graph page，替換現有 blocked placeholder
- `RW-01` research ticket list/detail/create/lifecycle UI
- `RW-02` search UI
- `RW-03` analyze UI
- `RW-04` experiment launch/history/detail/cancel UI
- `KW-01` review/finalize and merge current returned UI
- `CW-03` committee board partial activation UI
- `TW-01` trainer session list/detail/message UI
- `TW-03` before/after compare UI
- `TW-04` teaching replay list/detail/commit/discard UI

### 3.2 Missing backend implementation

需要補的 BFF / backend：

- `RW-05`
  - artifact registry list
  - artifact detail
  - artifact compare
  - versioning / ancestry wiring
- `KW-02`
  - notes create/list/detail
- `KW-03`
  - evidence list/detail
- `KW-04`
  - insight aggregation/detail
- `CW-02`
  - transcript route
  - append-only `TranscriptEvent`
  - actor labeling
  - inline evidence link resolution
- `CW-04`
  - memo list/detail
  - session-to-memo mapping
  - governance handoff authority

### 3.3 Handoff and coordination completion

這些雖不是 route implementation，但不補就無法把 backend 真相交給前端：

- `RW-04`
  - 缺 example templates referenced by lovable task
  - evidence: `current-work.md:94`
- `TW-03`
  - `FRONTEND_CHANGE_SPEC.md` 不存在
  - lovable task 也未齊
- `TW-04`
  - `FRONTEND_CHANGE_SPEC.md` 不存在
  - screen_id / prompt / ui-task 仍不同步
  - evidence: `current-work.md:96`
- `EW-04`
  - live route truth 尚未 fully rebaseline 進 coordination bundle
  - evidence: `current-work.md:93`

### 3.4 Closeout and record sync

這是把「系統真的完成」和「board 看起來完成」對齊的必要工作：

- 批次 finalize `frontend_feedback_reviewed` loops
- finalize `ui_done_reviewed` loops
- absorb currently returned `EW-05` / `KW-01` review
- finish `EXEC-CLOSEOUT-FRONTEND-001`

### 3.5 OSS next-wave

完整藍圖還差的成熟化工作：

- RL activation decision -> execution slice
- W&B parity decision -> execution slice
- vectorbt next-wave readiness
- statsmodels next-wave readiness
- QuantLib next-wave readiness

evidence: `current-work.md:64-69`, `98-102`

---

## 4. 開發藍圖仍不清楚的地方

截至目前，仍屬 blueprint / system-design 未 fully closed 的問題有兩層。

### 4.1 仍屬 architecture bucket 的問題

根據整合後的 open-questions 文件，真正還在 architecture bucket 的只剩：

- 全域 canonical conventions pack
- `LIN-002` lineage ownership
- `control-plane/persona` boundary
- router / gateway / governance enforcement ownership

evidence: `docs/reviews/2026-04-20-system-design-open-questions-for-architecture-team.md:28-34`

### 4.2 已有方向，但 repo 還沒完全收斂的規則

這些問題不是完全沒答案，而是 blueprint 已有 working answer，卻尚未全面 propagated：

- readiness ladder vocabulary 還沒全 repo 收斂
  - evidence: `docs/reviews/2026-04-20-system-design-open-questions-for-architecture-team.md:44-56`
- degradation dictionary 仍缺全域 crosswalk
  - evidence: `docs/reviews/2026-04-20-system-design-open-questions-for-architecture-team.md:203-211` 之後延伸段落
- shared response envelope 仍缺 consistent adoption
  - `allowedActions` object-shape 已有結論，但 overview example / packet / SA 還未同步
  - evidence: `docs/reviews/2026-04-20-system-design-open-questions-for-architecture-team.md:203-211`
- `CW-03` partial activation promotion rule 已 ratify，但 backlog / SA / packet / examples 都未同步
  - evidence:
    - ratification: `MODULE_READINESS_RATIFICATION_2026-04-20.md:32-40`
    - stale docs: `WORKBENCH_DELIVERY_BACKLOG.md:89`, `docs/lovable/PANTHEON_FRONTEND_SA.md:305-306`, `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md:41-44`

### 4.3 仍未 fully locked 的 blocked module contracts

下列模組 blueprint 仍未 fully locked，因此不應被當成一般 implementation task 硬推：

- `CW-02`
- `CW-04`
- `TW-02`
- `KW-05`

evidence: `MODULE_READINESS_RATIFICATION_2026-04-20.md:22-28`

---

## 5. 最後判讀

如果只問「現在距離完整系統開發藍圖有哪些差距」，我會把答案收斂成下面 8 點：

1. `Research` canonical frontend 還沒從單一舊頁面拆成 `RW-01` 到 `RW-04` 真正的 workbench routes。
2. `Trainer` canonical frontend 還沒從 demo trainer page 遷移到 `TW-01/TW-03/TW-04` 的 BFF-backed IA。
3. `EW-04` 雖已 route-live，但前端仍是 placeholder。
4. `CW-03` 雖已 backend live，但前端尚未 partial-activate，也沒有 canonical committee routes。
5. `RW-05`, `KW-02~04`, `CW-02`, `CW-04` 仍有真實 backend implementation gap。
6. `RW-04`, `TW-03`, `TW-04`, `EW-04` 的 handoff / coordination bundle 尚未完全跟上真實實作。
7. backlog / SA / packet family / overview examples 仍大面積 truth drift，已足以誤導下一輪派工。
8. 剩下的 blueprint 不清楚處，已縮到 conventions / ownership / blocked-module contract 這一層，不再是整體產品藍圖重畫問題。

---

## 6. 建議下一輪工作順序

若目標是最快縮小「真實系統」與「完整藍圖」的距離，建議順序如下：

1. 完成 doc rebaseline：
   - backlog
   - frontend SA
   - packet family
   - overview examples
2. 完成已 live backend 的前端接線：
   - `EW-04`
   - `RW-01`
   - `RW-02`
   - `RW-04`
   - `TW-01`
   - `CW-03` partial activation
3. 補 handoff bundle：
   - `RW-04`
   - `TW-03`
   - `TW-04`
4. 補真正缺的 backend：
   - `RW-05`
   - `KW-02`
   - `KW-03`
   - `KW-04`
   - `CW-02`
   - `CW-04`
5. 把 blocked blueprint 問題送回 architecture lane：
   - `TW-02`
   - `KW-05`
   - residual conventions / ownership issues
