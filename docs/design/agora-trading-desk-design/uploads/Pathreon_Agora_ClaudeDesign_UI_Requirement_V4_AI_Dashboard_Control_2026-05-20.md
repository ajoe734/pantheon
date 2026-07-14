# Pathreon Agora — Claude Design UI Requirement V4
## 主題：讓交易員能「叫研究助理改畫面」的 AI Dashboard Control UI

版本：2026-05-20 V4  
用途：交給 Claude Design 製作 Agora 前端視覺稿與互動稿  
範圍：Agora 個人 AI 交易桌，不包含 Pathreon Management UI  
核心修正：V3 雖然定義了三主頁籤與 Strategy Lens，但畫面上看不出交易員可以命令 AI 助手動態調整 dashboard。V4 專門補足這個缺口。

---

# 0. 本版必須解決的問題

交易員要能在 Agora 裡直接用自然語言或低摩擦操作告訴 AI 助手：

```text
這個策略我想先看分點出貨風險。
把流動性放前面。
不要再顯示 RSI。
這個候選池我想用表格，不要用圖。
幫我新增一個圖，看同券商不同分點是否在同族群反向賣出。
這個部位頁先給我看 thesis 是否失效，不要先看 PnL。
```

設計目標：

> **交易員一眼就看得出：這張 dashboard 是 AI 助手替我配置的，我可以命令助手重排、增減 widget、改圖表、改監控重點，而且系統會記住我的偏好。**

---

# 1. 產品語氣修正

## 1.1 不使用「Ask AI」作主文案

這群使用者是高階交易員、大戶、主觀操盤者。UI 不能讓他感覺自己在低姿態「問 AI」。

禁用文案：

```text
Ask AI
Chat with AI
AI says
AI recommends
Ask anything
```

使用文案：

```text
交代研究助理
讓助理重排
助手已替您整理
待您裁示
納入監控
剔除
暫放
深入研究
送影子追蹤
調整這張版面
記住我的偏好
```

## 1.2 AI 的角色是「研究助理 / 交易助理」

畫面語氣：

```text
您的研究助理已根據「籌碼大戶部位建立」策略重排版面。
它把「關聯分點賣超風險」放到第一排，因為您最近多次因這項風險剔除候選股。
```

不是：

```text
AI thinks you should...
```

---

# 2. Agora 主畫面必須呈現三主頁籤 + AI 版面控制

主畫面仍維持 V3 的三大主頁籤：

```text
我的 AI 交易桌
├── 盯盤 Radar
├── 策略 Lab
└── 部位 Cockpit
```

但 V4 要在每個主頁籤上方加入一個明確的 **Dashboard Control Bar**。

---

# 3. Dashboard Control Bar 規格

## 3.1 位置

每個主頁籤頂部，位於 Strategy Lens tabs 下方、主要內容區上方。

```text
┌──────────────────────────────────────────────┐
│ Strategy Lens Tabs                            │
├──────────────────────────────────────────────┤
│ Dashboard Control Bar                         │
├──────────────────────────────────────────────┤
│ Dashboard Content                             │
└──────────────────────────────────────────────┘
```

## 3.2 內容

```text
目前版面：AI personalized v7
策略視角：籌碼大戶部位建立
最近調整：已將「關聯分點賣超風險」移到第一排
原因：您最近 5 次候選討論中，有 3 次因該風險剔除標的
效果：近 10 日 shadow hit rate +8%

[交代助理調整版面] [查看調整紀錄] [還原上一版] [固定目前版面]
```

## 3.3 必須包含的操作

```text
交代助理調整版面
查看調整紀錄
還原上一版
固定目前版面
新增 widget
檢查這張版面是否適合這個策略
```

---

# 4. 「交代助理調整版面」互動

## 4.1 觸發方式

交易員可以從三個地方觸發：

1. Dashboard Control Bar
2. 任一 widget 的右上角 menu
3. AI Command Bar

## 4.2 打開右側 Drawer

Drawer 名稱：

```text
交代研究助理調整版面
```

Drawer 結構：

```text
1. 自然語言指令輸入
2. 快速指令按鈕
3. 助手理解結果
4. 版面改動預覽
5. Apply / Edit / Reject
```

## 4.3 自然語言輸入範例

```text
這個策略先給我看出貨風險。
把分點買盤熱圖放第一排。
不要顯示 RSI。
候選股用表格，不要用散點圖。
幫我新增一個圖，看同券商分點是否在同族群反向賣出。
這個部位頁先顯示 thesis 是否失效。
```

## 4.4 快速指令按鈕

```text
先看風險
先看籌碼
先看同族群
先看流動性
隱藏技術指標
改成表格
新增比較圖
產生新版 dashboard
還原預設
```

---

# 5. AI Dashboard Proposal Drawer

當交易員交代後，AI 不能直接改畫面。必須先提出 proposal。

## 5.1 Proposal Drawer 內容

```text
助手建議以下版面調整：

1. 新增「關聯分點賣超風險」widget
   位置：第一排左側
   原因：您最近多次因關聯分點反向賣超剔除候選股
   影響：提高出貨風險可見度

2. 將「流動性 / 承接量」移到第二排
   原因：目前候選池已有流動性篩選，不需第一順位

3. 隱藏「RSI」widget
   原因：您已連續 8 次未使用，且曾標記不重要
```

操作：

```text
[套用全部]
[逐項確認]
[只預覽]
[拒絕]
[告訴助手原因]
```

## 5.2 Before / After Preview

Claude Design 必須畫出前後比較。

```text
┌──────────────────────┬──────────────────────┐
│ 目前版面              │ 助手建議版面           │
├──────────────────────┼──────────────────────┤
│ 1. 候選排名           │ 1. 關聯分點賣超風險     │
│ 2. 分點熱圖           │ 2. 候選排名             │
│ 3. RSI                │ 3. 分點熱圖             │
│ 4. 流動性             │ 4. 流動性               │
└──────────────────────┴──────────────────────┘
```

每個差異要標色：

```text
新增：綠色
移動：藍色
隱藏：灰色
高風險改動：橘色
需要確認：紅色
```

---

# 6. Widget Menu 規格

每個 widget 右上角要有 menu。

Menu items：

```text
為什麼顯示這個？
固定在這裡
往前移
往後移
隱藏
顯示更多細節
太吵，降低權重
這對我有用
這對我沒用
叫助手用別種圖表呈現
叫助手新增相關圖表
```

## 6.1 「為什麼顯示這個？」

點擊後顯示：

```text
這個 widget 被放在第一排，因為：
- 目前策略視角是「籌碼大戶部位建立」
- 您最近剔除候選股時，最常提到「出貨風險」
- 此 widget 直接監控關聯分點反向賣超
- 近 10 日此 widget 對 shadow outcome 有正向貢獻
```

## 6.2 「叫助手用別種圖表呈現」

打開 mini drawer：

```text
您希望怎麼看？
[表格] [折線圖] [Heatmap] [Network] [Scatter] [Timeline]
或輸入：用一張圖把分點買盤和價格支撐一起呈現。
```

---

# 7. AI 新增 Widget / 新圖表流程

## 7.1 使用者指令

```text
幫我新增一個圖，看同券商不同分點是否在同族群反向賣出。
```

## 7.2 AI 回應

AI 應生成 Widget Proposal，不是直接生成 code。

畫面顯示：

```text
助手可以新增一個「同券商分點反向流 Network」圖。

用途：觀察同券商不同分點是否在同族群出現一邊買、一邊賣的資金輪動。
資料：branch_flow_daily、sector_peer_map
圖表：network graph
節點：分點
連線：同券商 / 同族群 / 反向流
顏色：買超 / 賣超 / mixed
互動：點擊分點查看明細

[預覽] [加入目前 dashboard] [取消]
```

## 7.3 如果 renderer 已支援

直接加入 dashboard recipe。

## 7.4 如果 renderer 不支援

顯示：

```text
這需要新的 widget plugin。
助手已產生設計提案，需前端開發後才能使用。

[建立 Widget 提案]
```

不要讓使用者以為立刻能用。

---

# 8. Dashboard Change Log 頁 / Drawer

## 8.1 入口

Dashboard Control Bar 按鈕：

```text
查看調整紀錄
```

## 8.2 內容

```text
Dashboard Change Log

v7  2026-05-20 14:22  AI 助手調整
- 將「關聯分點賣超風險」移到第一排
- 隱藏 RSI
- 提高流動性權重
原因：您最近 5 次候選討論中，3 次因出貨風險剔除標的
效果：shadow hit rate +8%
[還原到 v6]

v6  2026-05-19 09:10  您手動調整
- 固定「分點熱圖」
- 移除新聞摘要
[查看]

v5  2026-05-18 11:42  AI 助手建議，您拒絕
- 新增 RSI divergence
拒絕原因：不看 RSI
```

## 8.3 每筆 change 必須顯示

```text
版本
誰改的：AI / 使用者 / 系統預設
改了什麼
為什麼
效果
是否可回滾
```

---

# 9. Personalization Status Bar

每個主頁籤都要顯示。

範例：

```text
個人化狀態：助手已學到您偏好先看籌碼，再看技術。此策略 dashboard 已個人化 7 次。
[查看學習紀錄] [調整偏好]
```

狀態包括：

```text
learning
personalized
stable
needs_feedback
recently_changed
locked_by_user
```

---

# 10. Dashboard Switcher

因為每個交易員有多個 strategy lens，每個 lens 有自己的 dashboard。

位置：盯盤 Radar 頁籤內上方。

範例：

```text
[籌碼大戶部位建立] [AI server 落後補漲] [事件交易] [技術突破] [+ 新增策略視角]
```

每個 dashboard card menu：

```text
重新命名
複製
歸檔
還原預設
查看調整紀錄
匯出摘要
```

---

# 11. 三主頁籤都必須支援 AI 改畫面

## 11.1 盯盤 Radar

可改：

```text
candidate pool 欄位
monitoring pool panel
分點圖表
落後補漲表
排序權重
異常警示
```

## 11.2 策略 Lab

可改：

```text
mission progress layout
candidate table columns
evidence board layout
risk/counter-thesis panel
industry map presentation
```

## 11.3 部位 Cockpit

可改：

```text
position risk panels
thesis status layout
PnL vs thesis order
invalidation checklist
shadow alternative view
```

---

# 12. 受控資料與安全規則

## 12.1 Agent 不能產生任意 code

禁止：

```text
AI 直接生成 React component
AI 直接生成 JS code
AI import 新套件
AI 打外部 API
AI 讀其他使用者資料
AI 建立 live order action
```

允許：

```text
WidgetSpec
ChartSpec
DashboardRecipe
WidgetPluginProposal
```

## 12.2 所有 WidgetSpec 必須通過 validator

Validator 檢查：

```text
widgetType 是否存在
dataSource 是否 allowlisted
fields 是否存在
interaction 是否允許
dataSensitivity 是否合規
不得包含 raw prompt
不得 cross-user
不得包含 live trading action
```

---

# 13. 必畫 Screens 給 Claude Design

Claude Design 這次至少要畫 10 張。

## Screen 1 — My AI Trading Desk / Radar Tab

必須看到：

```text
Strategy Lens tabs
Dashboard Control Bar
Candidate Pool
AI Review Drawer
Widget menu
```

## Screen 2 — AI Dashboard Proposal Drawer

必須看到：

```text
自然語言指令
AI 理解
建議改動列表
Before / After preview
Apply / Edit / Reject
```

## Screen 3 — Widget Menu Expanded

必須看到：

```text
為什麼顯示這個
固定
移動
隱藏
換圖表
新增相關圖表
有用 / 沒用
```

## Screen 4 — New Widget Proposal

必須看到：

```text
Widget title
purpose
data sources
chart type
interactions
preview
是否支援即時加入
若不支援，顯示需開發 plugin
```

## Screen 5 — Dashboard Change Log

必須看到：

```text
版本歷史
AI 調整原因
效果
還原
拒絕紀錄
```

## Screen 6 — Personalization Status

必須看到：

```text
AI 學到什麼
哪些偏好
最近調整
是否改善
```

## Screen 7 — Strategy Lab Dashboard Adjustment

必須看到：

```text
Mission detail
Evidence board
Candidate table
AI 提出改版面
```

## Screen 8 — Position Cockpit Dashboard Adjustment

必須看到：

```text
部位 thesis status
AI 提出將 thesis decay 放第一
before/after preview
```

## Screen 9 — Dashboard Switcher

必須看到：

```text
多個 strategy lens dashboard
rename / duplicate / archive / reset / history
```

## Screen 10 — Mobile / Narrow Layout

必須看到：

```text
Dashboard Control Bar collapse
Widget proposal drawer
Candidate review stacked layout
```

---

# 14. Acceptance Checklist

Claude Design 交付需符合：

```text
能看出交易員可以命令助手改畫面
能看出 AI 不是聊天，而是研究助理
能看出每個策略 lens 有不同 dashboard
能看出每個交易員有個性化記憶
能看出 AI 改版面有理由、有預覽、有回滾
能看出 widget 可以被 pin / hide / reorder / change chart
能看出 AI 可以提出新 widget / chart
能看出新 widget 不是直接生 code，而是 spec/proposal
能看出剔除候選不會真刪，會留下訓練資料
不能出現 Ask AI 作主文案
不能出現 Management / governance / runtime / capital binding 等詞
不能看起來像散戶股票聊天工具
```

---

# 15. 最終設計意圖

Agora 的 UI 必須讓人一眼看懂：

```text
這不是固定 dashboard。
這是我的交易助理依照我的策略、我的習慣、我的偏好，幫我動態配置的交易工作桌。
我可以隨時交代它重排、增加圖表、隱藏沒用資訊、改監控重點。
它會記住，而且會評估這樣改有沒有讓我做得更好。
```

這是 V4 的核心。
