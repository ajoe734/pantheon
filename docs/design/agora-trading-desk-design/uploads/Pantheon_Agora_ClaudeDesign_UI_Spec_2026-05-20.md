# Pantheon Agora 前端 UI 完整規格書
## 給 Claude Design / 前端設計與實作團隊

版本：2026-05-20 v1
範圍：Agora 前端 UI / UX / IA / 資料模型 / BFF contract / 個人化 dashboard / Shadow mode / AI 工作流增幅器
不包含：Pathreon Management 後台、capital binding、runtime binding、broker live、治理審批後台 UI

---

## 0. 一句話定義

**Agora 是交易員的個人 AI 交易桌。**

它不是散戶聊天機器人，不是 Management 後台的簡化版，也不是一般 watchlist。

Agora 要服務的是高階交易員、股票大戶、分析師、主觀操盤者。它的核心是：

```text
交易員正在盯哪些標的
交易員正在發想哪些新交易策略 / 交易假說
交易員現在有哪些部位要監控
AI 如何幫交易員研究、篩選、監控、比較、shadow、複盤、修正與學習
```

最終體驗：

> 交易員打開 Agora，不是在找功能，而是在使用一張會學他的 AI 交易桌。

---

## 1. 產品原則

### 1.1 Agora 是個人交易增幅器

Agora 使用者看到的是：

```text
我的 AI
我的盯盤標的
我的策略想法
我的部位
我的影子結果
我的交易日誌
我的 AI 是否變得更懂我
```

不能看到：

```text
Pathreon Management
Management queue
governance
runtime binding
capital binding
artifact_state
deployment_stage
broker live
operator gate
BFF
registry
其他使用者
```

### 1.2 三個主工作區，不是一堆頁面

交易員日常只有三條主線：

```text
1. 盯盤 Radar：我正在盯哪些標的？為什麼盯？有什麼異常？
2. 策略 Lab：我正在發想哪些交易策略 / 研究假說？AI 幫我研究到哪？
3. 部位 Cockpit：我現在手上的部位怎麼樣？原 thesis 還成立嗎？
```

所有其他功能都應圍繞這三條主線：

```text
AI Command Bar
Shadow Book
Journal / Replay
Train My AI
Expert Review
Persona Progress
Settings
```

### 1.3 策略 lens 驅動 UI

同樣是盯盤，不同策略需要看的東西不同。

例如同樣盯 `8086`：

- 若策略是「分點籌碼吸貨」，要看分點、連續買超、同券商反向流、出貨風險。
- 若策略是「產業落後補漲」，要看供應鏈位置、同族群相對漲幅、催化、流動性。
- 若策略是「技術突破」，要看突破價位、量能、假突破、停損。
- 若策略是「大戶部位建立」，要看承接量、成交金額、市場衝擊、分批進場。

所以 Agora UI 的核心不是固定 watchlist，而是：

```text
Strategy Lens -> Candidate Pool -> Candidate Discussion -> Monitoring Pool -> Lens-specific Dashboard
```

### 1.4 AI 可以生成 dashboard，但不能生成任意程式碼

Agent 可以產生：

```text
WidgetSpec
ChartSpec
DashboardRecipe
Layout Proposal
Scoring Recipe
Alert Rules
```

Agent 不可以直接產生：

```text
任意 React/JS code
任意外部 API call
未授權資料源
跨使用者資料查詢
live trading command
```

### 1.5 每個交易員都有自己的個人化記憶

Agora 必須記錄：

```text
交易員偏好哪些資料
交易員常刪掉哪些候選
交易員常 pin 哪些 widget
交易員對不同策略 lens 的 dashboard 偏好
交易員常用的研究流程
交易員如何修正 AI
AI 改 dashboard 後有沒有變好
```

---

## 2. 目標使用者

### 2.1 高階主觀交易員

需求：

```text
快速研究假說
追蹤盯盤標的
檢查籌碼 / 產業 / 事件 / 技術
複盤自己的判斷
讓 AI 更懂自己的風格
```

### 2.2 股票大戶 / 資金型交易者

需求：

```text
盯多檔候選
看流動性與承接量
追蹤分點與籌碼
避免市場衝擊
監控現有部位 thesis 是否失效
```

### 2.3 分析師 / 研究型交易者

需求：

```text
產業鏈研究
找同族群落後股
比較多標的
整理研究任務
產生結構化報告
```

---

## 3. 新 Agora IA

```text
Agora
└── My AI Trading Desk
    ├── 盯盤 Radar
    ├── 策略 Lab
    └── 部位 Cockpit

橫切入口
├── AI Command Bar
├── Shadow Book
├── Journal / Replay
├── Train My AI
├── Persona Progress
├── Expert Review
└── Settings
```

### 3.1 Route 設計

```text
/agora
/agora/desk
/agora/radar
/agora/radar/:lensId
/agora/radar/:lensId/candidates/:candidateId
/agora/strategy-lab
/agora/strategy-lab/:missionId
/agora/positions
/agora/positions/:positionId
/agora/shadow-book
/agora/shadow-book/:id
/agora/journal
/agora/journal/:id
/agora/replay/:id
/agora/train
/agora/persona-progress
/agora/expert-review/:contextId
/agora/settings
```

### 3.2 不要把下列設為主導航

以下保留為子功能，不作主頁：

```text
Daily Brief
Adaptive Dashboard
Research Routine
Done For You
Notebook
Signals
Committee
Memory Review
Skill Coaching
Persona Lab
Evaluations
```

它們應被吸收進三大主工作區。

---

## 4. 全域版面設計

### 4.1 Desktop layout

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Top Bar: Agora / User / Persona / AI Command Bar                     │
├──────────────────────────────────────────────────────────────────────┤
│ Main Tabs: [盯盤 Radar] [策略 Lab] [部位 Cockpit]                    │
├──────────────────────┬──────────────────────────────┬────────────────┤
│ Left Workspace Rail  │ Main Workspace                │ AI Context     │
│ Strategy Lens /      │ Candidate table / dashboards  │ Explanation /  │
│ Missions / Positions │ charts / details              │ Evidence /     │
│                      │                               │ Actions        │
└──────────────────────┴──────────────────────────────┴────────────────┘
```

### 4.2 Mobile / narrow layout

Mobile 不強求完整交易工作桌，但至少要能：

```text
查看 AI brief
查看盯盤異常
查看部位風險
問 AI
記錄 journal
查看 shadow 結果
```

三欄 layout 改為 stacked cards + drawer。

---

## 5. 全域 AI Command Bar

### 5.1 位置

AI Command Bar 必須常駐：

```text
Top bar 中央
或頁面上方 sticky input
或右側 AI drawer trigger
```

### 5.2 Placeholder 隨 tab 改變

#### 盯盤 Radar

```text
輸入你想建立的盯盤策略，例如：找可能有大戶建立部位的股票...
```

#### 策略 Lab

```text
輸入交易假說，例如：我覺得 ABC 因為 AI server 需求可能會漲，幫我找落後補漲股...
```

#### 部位 Cockpit

```text
輸入部位問題，例如：幫我檢查 XYZ 原本 thesis 是否還成立...
```

### 5.3 快捷任務 buttons

```text
新增盯盤
找落後補漲
分析分點
建立策略任務
檢查部位
做反方論點
送 Shadow
寫 Journal
訓練 AI
```

### 5.4 AI 回答格式

AI 回答不可只是聊天，要結構化：

```text
Summary
Detected Task Type
Parsed Thesis / Intent
Suggested Mission Plan
Risks / Blind Spots
Next Actions
Evidence
```

### 5.5 Actions

每次 AI 回答都要能轉成：

```text
Create Strategy Lens
Create Candidate Pool
Create Research Mission
Add Watch Target
Send to Shadow
Add Journal Note
Ask Expert Review
Train My AI
```

---

## 6. 主頁：My AI Trading Desk

### 6.1 Route

```text
/agora
/agora/desk
```

### 6.2 目的

首頁不是一般 dashboard，而是交易員當日工作桌摘要。

要回答：

```text
我正在盯哪些策略與標的？
AI 幫我篩出了哪些候選？
我有哪些策略任務進行中？
我有哪些部位需要注意？
AI 幫我完成了哪些瑣事？
哪些 shadow 結果需要我看？
我的 AI 最近學到什麼？
```

### 6.3 頁面區塊

```text
1. AI Command Bar
2. Today Brief / AI Finished Tasks
3. Watching Radar Summary
4. Strategy Lab Summary
5. Position Cockpit Summary
6. Shadow Book Summary
7. Journal Follow-ups
8. Persona Learning Progress
```

### 6.4 Summary cards

#### Watching Radar Summary

```text
Active strategy lenses: 4
Candidate pool total: 83
Needs discussion: 17
Monitoring: 22
High attention: 5
```

Actions:

```text
Open Radar
Review Candidates
Create Lens
```

#### Strategy Lab Summary

```text
Research missions: 9
Completed awaiting review: 3
Candidates found: 26
Shadowing: 8
```

#### Position Cockpit Summary

```text
Open positions: 7
Thesis intact: 5
Needs review: 1
Risk warning: 1
```

#### Shadow Book Summary

```text
AI better: 6
Human better: 4
Inconclusive: 5
Biggest missed opportunity: +9.3%
```

---

## 7. 主頁籤一：盯盤 Radar

### 7.1 目的

盯盤 Radar 是策略 lens 驅動的候選池與監控池。

不是一般 watchlist。

核心流程：

```text
交易想法 / 策略 lens
  -> AI 產生候選池
  -> 交易員逐檔討論
  -> 加入監控池 / 送 Shadow / 剔除 / Parking
  -> AI 依策略 lens 動態生成 dashboard
```

### 7.2 Route

```text
/agora/radar
/agora/radar/:lensId
```

### 7.3 Layout

```text
┌──────────────────────────────────────────────────────────────┐
│ AI Command Bar                                                │
├──────────────────────────────────────────────────────────────┤
│ Strategy Lens Tabs                                            │
│ [籌碼大戶部位建立] [AI server 落後補漲] [技術突破] [+ New]    │
├───────────────┬──────────────────────────────┬───────────────┤
│ Lens Sidebar  │ Candidate / Monitoring Board │ AI Review     │
│               │                              │ Drawer        │
│ 候選池 38     │ Rank / Symbol / Score / Risk │ 為什麼選它     │
│ 待討論 12     │                              │ 疑慮           │
│ 監控中 9      │                              │ 下一步         │
│ Shadow 中 4   │                              │ Evidence       │
│ 已剔除 13     │                              │ Actions        │
└───────────────┴──────────────────────────────┴───────────────┘
```

### 7.4 Strategy Lens Tabs

每個 tab 是一套監控策略。

範例：

```text
籌碼大戶部位建立
AI server 落後補漲
分點吸貨
事件交易
技術突破
流動性部位建立
```

### 7.5 Lens Sidebar

顯示：

```text
Candidate Pool
Needs Discussion
Monitoring
Shadowing
Rejected
Parked
Archived
```

每個狀態顯示 count。

### 7.6 Candidate Pool Table

欄位根據 lens 動態變化，但基本欄位：

```text
Rank
Symbol
Company
AI Reason
Score
Risk
Liquidity
Status
Actions
```

#### 大戶部位建立 lens 欄位

```text
Branch Accumulation Score
Consecutive Buy Days
Net Buy Concentration
Price Still Early?
Distribution Risk
Related Branch Risk
Liquidity
```

#### 產業落後補漲 lens 欄位

```text
Similarity Score
Price Lag Score
Catalyst Relevance
Peer Return Gap
Chip Support
Liquidity
Why Not Moved Yet
```

#### 技術突破 lens 欄位

```text
Breakout Level
Volume Confirmation
ATR
False Breakout Risk
Support / Stop
Historical Setup Similarity
```

### 7.7 Candidate actions

每一檔候選都可操作：

```text
Discuss
Add to Monitoring
Need More Research
Send to Shadow
Reject
Park
Add Journal Note
```

### 7.8 Candidate Review Drawer

點一檔候選，右側 drawer 顯示：

```text
Symbol / Company
AI Why Selected
Evidence Summary
Strategy Lens Fit
Key Signals
AI Concerns
Recommended Next Action
Actions
```

#### 範例內容

```text
AI 為什麼選 8086：
- 最近 10 日分點淨買集中
- 買盤不是追高，而是在區間內吸收
- 同族群已有 3 檔先漲
- 此檔相對漲幅仍落後
- 流動性足夠

AI 疑慮：
- 同券商另一分點在同族群某檔賣超
- 今日量增但價格未有效突破
- 若明日跌破 XX 則 thesis 弱化

AI 建議：
- 加入監控池
- 設定分點買盤是否延續為主要條件
- 先觀察 2 日再進 Shadow
```

### 7.9 交易員討論紀錄

交易員可用自然語言回覆：

```text
這檔不算同族群，刪掉。
這個分點我不信，它常隔天倒貨，列高風險。
這檔留，但只看是否站上前高。
這檔加入監控，但先不要 shadow。
```

要產生 CandidateDiscussion。

---

## 8. 主頁籤二：策略 Lab

### 8.1 目的

策略 Lab 是交易員發想與研究交易假說的地方。

不是 Notebook，也不是聊天。

### 8.2 Route

```text
/agora/strategy-lab
/agora/strategy-lab/:missionId
```

### 8.3 Main view

內部 sub-tabs：

```text
進行中
已完成
Shadow 中
已轉成盯盤
已歸檔
```

### 8.4 Mission card

顯示：

```text
Mission title
Mission type
Seed symbols
Thesis
Status
AI progress
Candidates found
Needs user decision?
Last update
```

### 8.5 Mission types

```text
industry_peer_laggard
broker_branch_flow
event_research
multi_stock_comparison
chip_technical_confirmation
position_review
counter_thesis
liquidity_execution_study
sector_rotation
earnings_revision
```

### 8.6 Mission detail structure

```text
1. User Thesis
2. AI Mission Plan
3. Research Progress
4. Evidence Board
5. Candidate Results
6. Risk / Counter Thesis
7. Suggested Actions
8. Shadow Tracking
9. Journal
```

### 8.7 Industry Peer Laggard Mission

User input example:

```text
我覺得 ABC 這幾檔因為 AI server 需求轉強可能會漲，幫我找相似但還沒漲的股票。
```

AI mission plan:

```text
1. 解析 thesis
2. 建立供應鏈 / 產業位置
3. 找同類股 universe
4. 計算相似度
5. 計算漲幅落後程度
6. 檢查成交量 / 流動性
7. 檢查籌碼 / 分點支持
8. 排名候選股
9. 產出反方論點
10. 建立 watchlist / shadow candidates
```

Candidate table:

```text
Rank
Symbol
Similarity Score
Price Lag Score
Catalyst Relevance
Liquidity
Chip Support
Why Similar
Why Not Moved
Confirm Signal
Risk
Action
```

### 8.8 Broker Branch Flow Mission

User input example:

```text
幫我看 8086 最近 10 天是不是有特定分點連續買，並檢查同券商或關聯分點是否在其他地方出貨。
```

Panels:

```text
Top Net Buyers
Consecutive Buy Days
Branch Accumulation Timeline
Price vs Branch Buy
Related Branch Cross Flow
Same Broker Other Stock Flow
Sector Rotation
Potential Distribution Signals
AI Interpretation
```

Use cautious wording:

```text
疑似資金輪動
可能換手
關聯分點反向流
同券商分點行為不一致
需要人工確認
```

Do not claim confirmed manipulation.

---

## 9. 主頁籤三：部位 Cockpit

### 9.1 目的

部位 Cockpit 監控交易員現在持有的部位。

要回答：

```text
原始 thesis 還成立嗎？
風險有沒有升高？
同族群有沒有變弱？
籌碼是否轉向？
是否該減碼、續抱、等待、做 shadow 替代？
```

### 9.2 Route

```text
/agora/positions
/agora/positions/:positionId
```

### 9.3 Main view sub-tabs

```text
全部部位
需要注意
Thesis 弱化
風險升高
Shadow 替代方案
已平倉 / 歷史
```

### 9.4 Position table

欄位：

```text
Symbol
Position Size
Avg Cost
Current Price
PnL
Drawdown
Holding Days
Original Thesis
Thesis Status
AI Current Read
Risk Level
Next Action
```

### 9.5 Grouping

可依：

```text
By Strategy Lens
By Risk
By Thesis Status
By Holding Time
By Sector
```

例如：

```text
籌碼大戶部位建立
  - 8086
  - 3227
  - 1234

AI server 落後補漲
  - ABC
  - DEF
```

### 9.6 Position detail

```text
1. Original Thesis
2. What Changed Since Entry
3. PnL / Drawdown
4. Industry / Peer Movement
5. Branch / Chip Flow
6. Technical Status
7. News / Event Risk
8. Invalidation Checklist
9. AI Scenarios
10. Shadow Alternative
11. Journal / Replay
```

### 9.7 ThesisStatus

```text
intact
improved
weakening
invalidated
needs_review
```

---

## 10. Dynamic Dashboard / Widget System

### 10.1 目標

Agent 可以依照策略 lens 產生新的 dashboard widget 與圖表呈現。

但不能生成任意 React / JS code。

AI 產生的是：

```text
WidgetSpec
ChartSpec
DashboardRecipe
```

前端用受控 renderer 渲染。

### 10.2 Widget levels

#### Level 1：重組既有 widget

```text
顯示 / 隱藏 / 排序 / threshold / filter
```

#### Level 2：用既有 widget template 建新 instance

```text
ranking_table
heatmap
scatter
network_graph
timeline
comparison_table
score_card
flow_sankey
candlestick_overlay
```

#### Level 3：產生受控 ChartSpec

例如 scatter / heatmap / network grammar。

#### Level 4：提出新 plugin proposal

不可直接上線，需開發 review。

### 10.3 Required files

```text
src/agora/widgets/registry.ts
src/agora/widgets/WidgetRenderer.tsx
src/agora/widgets/ChartSpecRenderer.tsx
src/agora/widgets/validateWidgetSpec.ts
src/agora/dashboard/DashboardRecipeEngine.ts
src/agora/dashboard/DashboardRecipeRenderer.tsx
```

---

## 11. Personalization System

### 11.1 目標

每個交易員有自己的：

```text
Dashboard recipes
Widget preferences
Strategy lens preferences
Workflow memory
Personalization events
Rollback history
```

### 11.2 主要資料模型

```text
TraderPersonalizationProfile
StrategyLensPersonalization
DashboardRecipe
WidgetPreference
PersonalizationEvent
DashboardPerformanceEvaluation
```

### 11.3 UI requirements

每個 dashboard 要有：

```text
Dashboard Switcher
Dashboard Change Log
Widget Feedback Controls
Rollback to previous layout
Explain why this widget is here
```

Widget controls:

```text
Useful
Not useful
Move up
Move down
Hide
Pin
Explain why
```

### 11.4 Dashboard Change Log example

```text
AI 最近做了這些調整：

1. 把「關聯分點賣超風險」移到第一排
   原因：你最近 5 次候選股討論中，有 3 次因關聯分點賣超而剔除標的。

2. 隱藏 RSI panel
   原因：你連續 8 次未使用該 panel，且標記「不重要」。

3. 提高流動性權重
   原因：你最近拒絕 4 檔低成交量候選股。
```

Actions:

```text
Accept
Undo
Never do this again
Tell AI why
```

---

## 12. Shadow Book

### 12.1 Purpose

Shadow Book 記錄 AI 建議、人類選擇、實際結果、反事實結果。

來源可以是：

```text
WatchTarget
StrategyMission
PositionMonitor
```

### 12.2 Views

```text
All Shadow Records
By Strategy Lens
By Symbol
By Outcome
AI Better
Human Better
Inconclusive
```

### 12.3 Strategy Lens Shadow Summary

```text
Lens: 籌碼大戶部位建立

AI selected: 38
Trader approved: 9
Trader rejected: 13
Parked: 8
Shadowed: 4

After 10 days:
Approved avg return: +4.2%
Rejected avg return: +1.1%
AI top 5 avg return: +5.8%
Trader selected avg return: +4.6%
Best rejected candidate: +9.3%
Worst approved candidate: -3.1%

Learning:
AI 過度低估 liquidity risk。
交易員對關聯分點出貨判斷有效。
```

---

## 13. Journal / Replay

### 13.1 Journal sources

Journal 自動連結：

```text
watch target notes
candidate discussion
strategy mission reports
position review
shadow results
AI corrections
```

### 13.2 Replay types

```text
Replay a candidate review
Replay a strategy mission
Replay a position decision
Replay a rejected AI suggestion
Replay a losing week
```

Timeline:

```text
market context
AI suggestion
user decision
outcome
AI review
training correction
```

---

## 14. Train My AI

### 14.1 Contextual correction

使用者不應進入設定頁調 prompt。
他應該在任何 context 修正 AI：

```text
這不是我的風格。
這種分點我不信。
低流動性不要再列入候選。
下次先看出貨風險。
這檔不算同族群。
```

產生：

```text
TrainingCorrection
WidgetPreference
StrategyLensPersonalization
CandidateDiscussion
```

---

## 15. Expert Review

Expert Review 從 WatchTarget / StrategyMission / Position 觸發。

使用者看到：

```text
找風險專家挑戰
找技術面專家
找基本面專家
找籌碼專家
找交易紀律教練
```

不要顯示：

```text
committee governance
review gate
red team orchestrator
```

輸出：

```text
Bull Case
Bear Case
Risk Check
Execution Concern
Final Suggestion
Actions
```

---

## 16. BFF Contract

Phase 1 可 mock，但 contract 先定。

### Core

```text
GET  /bff/agora/me
GET  /bff/agora/desk
POST /bff/agora/command
```

### Radar / Lens / Candidate

```text
GET  /bff/agora/strategy-lenses
POST /bff/agora/strategy-lenses
GET  /bff/agora/strategy-lenses/:id
GET  /bff/agora/strategy-lenses/:id/candidates
POST /bff/agora/strategy-lenses/:id/generate-candidates
PATCH /bff/agora/candidates/:id/status
POST /bff/agora/candidates/:id/discuss
POST /bff/agora/candidates/:id/send-shadow
```

### Dashboard / Widget / Personalization

```text
GET  /bff/agora/strategy-lenses/:id/dashboard-recipe
POST /bff/agora/strategy-lenses/:id/dashboard-recipe/propose
POST /bff/agora/dashboard-recipes/:id/accept
POST /bff/agora/dashboard-recipes/:id/rollback
POST /bff/agora/widgets/:id/feedback
POST /bff/agora/widgets/validate
POST /bff/agora/widgets/propose-plugin
GET  /bff/agora/personalization/profile
GET  /bff/agora/personalization/events
```

### Strategy Lab

```text
GET  /bff/agora/missions
POST /bff/agora/missions
GET  /bff/agora/missions/:id
POST /bff/agora/missions/:id/run
POST /bff/agora/missions/:id/create-radar-lens
POST /bff/agora/missions/:id/send-shadow
```

### Positions

```text
GET  /bff/agora/positions
GET  /bff/agora/positions/:id
POST /bff/agora/positions/:id/review
POST /bff/agora/positions/:id/send-shadow
POST /bff/agora/positions/:id/journal
```

### Shadow / Journal / Training

```text
GET  /bff/agora/shadow-book
GET  /bff/agora/shadow-book/:id
POST /bff/agora/shadow-book/:id/feedback
GET  /bff/agora/journal
POST /bff/agora/journal
POST /bff/agora/training-corrections
GET  /bff/agora/persona-progress
```

### Privacy rules

All endpoints must be scoped to current user.
Do not return Management data.
Do not return other user data.
Do not return raw prompts to Management-facing pipelines.

---

## 17. Data Models Summary

Required models:

```text
StrategyLens
WatchTarget
WatchThesis
StrategyMission
PositionMonitor
CandidateDiscussion
DashboardRecipe
WidgetPreference
PersonalizationEvent
ShadowDecisionRecord
TrainingCorrection
PersonaProgress
```

---

## 18. Implementation Phases

### Phase A：三主頁籤骨架

```text
My AI Trading Desk
盯盤 Radar
策略 Lab
部位 Cockpit
AI Command Bar
```

### Phase B：Strategy Lens / Candidate Pool

```text
StrategyLens
CandidatePool
CandidateReviewDrawer
CandidateDiscussion
MonitoringPool
```

### Phase C：Dynamic Dashboard

```text
WidgetRegistry
WidgetRenderer
DashboardRecipe
ChartSpecRenderer
WidgetSpecValidator
```

### Phase D：Personalization Memory

```text
DashboardSwitcher
DashboardChangeLog
WidgetFeedback
PersonalizationProfile
Rollback
```

### Phase E：Shadow / Journal / Training

```text
ShadowBook
Journal / Replay
Train My AI
PersonaProgress
```

### Phase F：BFF live integration

```text
replace local mock with scoped BFF endpoints
strict mode no silent fallback
```

---

## 19. Acceptance Checklist

Agora UI is complete when:

```text
/agora opens My AI Trading Desk.
Main workspace has exactly three primary tabs: Radar, Strategy Lab, Position Cockpit.
Radar is strategy-lens driven, not simple watchlist.
AI can generate candidate pools for a lens.
Trader can discuss candidates one by one.
Candidate can be approved, rejected, parked, sent to shadow, or moved to monitoring.
Monitoring dashboard changes by strategy lens.
Agent can propose WidgetSpec / ChartSpec / DashboardRecipe.
Widget specs are validated before rendering.
Each trader has personalized dashboard recipes.
Dashboard changes are versioned and explainable.
Trader can undo / reject AI dashboard changes.
Strategy Lab can run industry peer laggard and broker branch flow missions.
Position Cockpit monitors thesis status and risk.
Shadow Book records AI vs human vs outcome.
Journal / Replay records decision trace.
Train My AI can be triggered from any context.
Agora UI does not expose Management vocabulary.
All data is scoped to current user.
Management receives only redacted summaries.
```

---

## 20. Do / Do Not Do

### Do

```text
Design around real trader workflow.
Use tabs, not dozens of separate pages.
Make Strategy Lens the core organizing object.
Let AI generate candidate pools.
Let AI propose dashboard widgets.
Let users discuss candidates one by one.
Track rejected candidates as useful data.
Preserve personalization history.
Make shadow mode central.
```

### Do Not

```text
Do not make Agora a generic stock chatbot.
Do not expose Pathreon Management terms.
Do not make watchlist a flat symbol list.
Do not use fixed dashboard for every strategy.
Do not allow AI to inject arbitrary code.
Do not delete rejected candidates permanently.
Do not let Agora control live trading, runtime, capital, or broker.
Do not share one trader's personalization with another trader directly.
```

---

## 21. Final Product Statement

Agora is not a feature list.
Agora is a personalized AI trading desk.

Its three operating tabs are:

```text
盯盤 Radar
策略 Lab
部位 Cockpit
```

Everything else—AI command, shadow book, journal, training, expert review, personalization—supports these three workflows.

The core product promise:

> The trader gives Agora a strategy lens. Agora finds candidate stocks, discusses them with the trader, builds a personalized monitoring dashboard, tracks outcomes in shadow mode, learns the trader's workflow, and continuously improves the trader's decision process.
