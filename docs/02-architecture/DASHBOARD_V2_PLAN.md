# Dashboard V2 Plan

## Why V1 Feels Wrong

目前 dashboard 最大的問題不是「資訊不夠多」，而是把不同層級的真相混在一起：

- `planning truth`
  - 來自 `planning-session.json` / `.orchestrator/planning-state.json`
  - 代表共識流程、artifact、readout、switch gate
- `execution truth`
  - 來自 `ai-status.json`
  - 代表 task lifecycle、handoff、blocker、review 狀態
- `runtime truth`
  - 來自 `.orchestrator/state.json`
  - 代表 supervisor heartbeat、queue、worker run、retry、approval、fallback

V1 把這三層塞在同一個頁面，而且沒有明確告訴使用者「這一格到底是在看哪一種真相」，所以會出現這些典型錯覺：

- planning 已 accepted，但 readout 看起來像還沒結束
- live worker 明明在跑，但 task board 看起來像只有 0 或 1 個 `in_progress`
- `Current Work` 看起來像 execution board，卻又混進 planning artifact 的語言
- 要確認「現在真的在跑什麼」時，視線必須在多個 panel 來回切

## V2 Product Goal

V2 的目標不是「更炫」，而是讓使用者在 10 秒內回答下面 4 個問題：

1. 現在 supervisor 有沒有活著？
2. 現在到底有哪些 worker 在跑？
3. 這些 worker 對應到哪個 task？task board 是否跟 runtime 一致？
4. planning 現在是進行中、待人工 gate，還是已經封存？

## New Information Architecture

### 1. Top Strip: Runtime First

頁面最上面只放 6 個高優先 metric：

- Supervisor heartbeat
- Queue depth
- Running workers
- Waiting approvals
- Runtime/task mismatch count
- Blocking incidents

這一排只吃 runtime + execution truth，不混 planning。

### 2. Primary Panel: Live Operations

第一大區塊改成 `Live Operations`，預設永遠展開。

它只回答「現在正在發生什麼」：

- Running workers
- Pending / approval / retry workers
- Queue events waiting to launch
- Runtime-to-task mismatch table

每個 worker card 固定顯示：

- worker run id
- agent / provider
- task id
- queue reason
- runtime status
- mapped task status
- last event at

如果 runtime status 和 task status 不一致，要直接標紅，而不是靠使用者自己比對。

### 3. Secondary Panel: Execution Board

第二大區塊才是 `Execution Board`。

它只看 `ai-status.json`：

- Ready now
- In progress
- In review
- Blocked
- Done recently

task card 必須顯示：

- owner / reviewer
- dependency readiness
- sidecar / helper-claim badge
- latest runtime attachment

也就是 task 不只是靜態卡片，而要能看到「有沒有 live worker 掛在它上面」。

### 4. Tertiary Panel: Planning

Planning 不再跟 execution 平起平坐。

規則改成：

- planning `active` / `human_required`
  - 以完整 panel 顯示
- planning `accepted`
  - 預設收合成 `Planning Archive Summary`
- planning `inactive`
  - 隱藏，只保留入口

planning panel 裡也要明確拆成兩類：

- `Artifacts`
  - planning workspace 的文件狀態
- `Readout Resolution`
  - lane readout 是否 `submitted / accepted / waived`

這裡不能再讓人誤會成 live worker 執行狀態。

### 5. Dedicated Mismatch Panel

V2 新增一個明確的 `Truth Mismatches` 區塊，集中列出：

- runtime worker exists but task still `todo`
- task `in_progress` but no live worker / no recent heartbeat
- queue event started but task owner mismatch
- planning accepted but artifacts still draft-like

這塊會變成整個 dashboard 最重要的自我診斷層。

## Data Contract Changes

### A. Derived Dashboard Bundle

不要再讓前端每次自己在 browser 端重算一堆對齊邏輯。

新增一份衍生 bundle，建議由 supervisor / sync pipeline 生成：

- `docs-site/dashboard-bundle.json`

它應該包含：

- `runtime_summary`
- `execution_summary`
- `planning_summary`
- `worker_task_links`
- `mismatches`
- `focus_mode`

這樣 dashboard renderer 只負責 render，不負責臨時拼 truth model。

### B. Runtime-to-Task Link Model

V2 固定要有一層 machine-readable 關聯：

- `task_id`
- `worker_run_id`
- `queue_event_id`
- `dispatch_reason`
- `runtime_status`
- `task_status`
- `linked_at`
- `mismatch_flags`

這樣前端可以直接 render 一致性，不必再靠 heuristics。

### C. Planning Summary Contract

planning bundle 固定輸出：

- `artifacts`
- `readouts`
- `readouts_resolved`
- `rounds_total`
- `actionable_open_items`
- `switch_gate`
- `archive_state`

`waived` 必須被當成 terminal resolved state，不然畫面永遠像半成品。

## UX Direction

### Default View

預設 landing view 應該是：

- 上方 `Runtime First`
- 中間 `Live Operations`
- 下方 `Execution Board`
- 最後才是 `Planning`

因為對日常使用來說，最重要的是「現在有沒有卡住」、「誰正在做事」、「哪個 task 真的在動」。

### Tone

畫面文案要更直接，不要過度抽象：

- 不要只寫 `Switch Gate`
- 要寫 `Planning Gate`
- 不要只寫 `Open 2`
- 要寫 `2 actionable planning issues`
- 不要只寫 `started`
- 要寫 `Worker started, task synced`

### Visual Priority

顏色優先序固定：

- red: mismatch / blocked / failed
- amber: approval / waiting / retry
- blue: active execution
- green: accepted / synced / ready
- neutral: archived / historical

V1 現在太多相同權重的小卡片，使用者要自己判讀優先順序。V2 要把 priority 直接做進版面。

## Delivery Plan

### Phase A: Truth Cleanup

- supervisor 在 dispatch 成功後自動同步 task status
- planning derived state 補齊 resolved readout semantics
- dashboard 明確標示 planning artifacts 不是 runtime status
- 新增 mismatch detection helpers

### Phase B: Bundle Layer

- 生成 `dashboard-bundle.json`
- 把 runtime/execution/planning 的對齊邏輯移出 browser
- renderer 改吃 bundle，不再各自重算

### Phase C: Layout Rewrite

- 重做 `index.html` 區塊順序
- 新增 `Live Operations`
- 新增 `Truth Mismatches`
- execution board 改成 readiness + live attachment 視圖
- planning 改成 archive-capable panel

### Phase D: History and Drilldown

- 加入 worker timeline
- task-to-run drilldown
- mismatch resolution hints
- planning archive compare view

## Definition of Done

V2 完成的標準不是「畫面變漂亮」，而是：

- user 不需要自己推理 planning / execution / runtime 的資料來源
- live running workers 與 task board 的落差可以被明確看見
- planning accepted 後，不再看起來像一堆未完成草稿
- 每個 task 都能看出目前有沒有 live runtime attached
- 畫面預設焦點是現在正在發生的事，而不是歷史文件狀態
