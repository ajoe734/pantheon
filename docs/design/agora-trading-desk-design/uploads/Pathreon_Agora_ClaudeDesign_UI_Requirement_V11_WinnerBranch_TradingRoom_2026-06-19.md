# Pathreon Agora — Claude Design UI Requirement V11
## Winner Branch Trading Room / 贏家分點策略交易操盤室與 AI 版面生成控制

> 版本：V11
> 日期：2026-06-19
> 適用對象：Claude Design、UI/UX 設計、VS Code 前端團隊、Pathreon BFF / Pantheon Research / Execution 團隊
> 使用者可見產品名稱：Pathreon Agora
> 本版關係：本文件承接 V10「高階策略對話與策略工坊」，補齊策略完成後的「交易操盤室」完整體驗。V10 的策略工坊規格仍有效；交易操盤室部分以本文件為準。

---

# 0. 本版必須解決的問題

V10 只把「贏家分點策略如何在策略工坊中被共同建構、研究、回測與選版」寫完整，但沒有把策略加入交易操盤室後的畫面與操作寫完整。

本版必須讓 Claude Design 明確畫出以下流程：

```text
策略工坊完成策略版本
→ 交易員選擇加入交易操盤室
→ 交易僕人根據完整策略定義，自動產生整套交易操盤室 Workspace
→ 交易僕人一次產生所有必要頁面、頁籤、Widget、圖表與操作區
→ 交易員預覽整套版面
→ 交易員可拖曳、刪除、新增、縮放與替換 Widget
→ 交易員可點選任何 Widget，直接交代交易僕人修改該 Widget 的內容或呈現方式
→ 所有修改版本化、可比較、可回滾、可被交易僕人記住
→ 策略正式進入持續監控、候選產生、交易裁示、持倉管理與出場裁示
```

本版不可再交付：

```text
- 只有一張固定 Dashboard
- 不同策略只換資料或標題
- Widget 無法移動、刪除、縮放或新增
- AI 只能改整張 Dashboard，不能針對單一 Widget 修改
- 只有聊天介面，沒有完整交易工作區
- 交易員需要自己從元件庫從零搭畫面
```

---

# 1. 核心產品定義

交易操盤室是：

> **交易僕人根據一個已完成策略，主動建立的策略專屬交易工作區。**

交易員不需要先自己設計 Dashboard。交易僕人必須先交付一套可用的完整初版，包括：

```text
- 頁面 / View 結構
- 每個 View 的版面布局
- 所有必要 Widget
- Widget 所使用的資料
- 圖表形式
- 篩選條件
- 排序方式
- 監控警示
- 候選與交易裁示隊列
- 持倉加碼、減碼與出場隊列
- 策略證據與信賴值
```

交易員接著才做：

```text
- 拖曳位置
- 改變大小
- 刪除不想看的 Widget
- 新增想看的 Widget
- 切換圖表型態
- 改變欄位、篩選與時間窗口
- 直接用對話交代交易僕人改某一個 Widget
- 儲存成自己的個人化版本
```

---

# 2. 策略工坊到交易操盤室的完整交接

## 2.1 觸發點

策略工坊在以下條件滿足後顯示：

```text
[加入交易操盤室]
```

最低條件：

```text
✓ 交易假說與可證偽條件
✓ 候選／Universe 規則
✓ Alpha / Signal 定義
✓ 進場、加碼、減碼、出場與失效規則
✓ 部位、相關性、風險與槓桿規則
✓ 成本、流動性與容量假設
✓ 至少一個樣本外或 rolling OOS 驗證版本
✓ 交易員選定主要策略版本
✓ 若有 Shadow 對照版本，已指定其角色
```

## 2.2 按下後的畫面

按下「加入交易操盤室」後，不直接跳到空白 Dashboard。

顯示完整建立流程：

```text
交易僕人正在建立「贏家分點 V4」交易操盤室

✓ 讀取 Winner Branch Score 與信賴值
✓ 讀取關係人—分點概率映射
✓ 讀取分點群組、遷移與出貨模型
✓ 讀取事件領先研究
✓ 讀取候選、進場、加碼、減碼與出場規則
✓ 讀取部位、槓桿、流動性與風險限制
✓ 讀取主要回測、Shadow 版本與監控條件
● 正在產生頁面與 Widget
○ 正在安排初始布局
○ 正在建立個人化版本
```

畫面文案：

```text
我會先替您把完整操盤頁面準備好。
您不需要從空白版面開始；完成後可自行拖曳、刪除、增加、縮放，或直接交代我修改任何圖表。
```

## 2.3 建立完成後先進 Preview

產生完成後進入：

```text
Dashboard Proposal Preview
```

必須一次顯示交易僕人產生的所有 View 縮圖，而不是只顯示一張畫面。

```text
贏家分點 V4 — 操盤室提案

1. 策略總覽
2. 候選與進場
3. 贏家分點情報
4. 分點關係與資金遷移
5. 事件領先研究
6. 持倉、加碼、減碼與出場
7. 證據與監控規則
```

操作：

```text
[套用完整提案]
[逐頁預覽]
[先調整版面]
[重新產生]
[回到策略工坊]
```

---

# 3. 交易操盤室整體頁面骨架

## 3.1 Desktop layout

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Pathreon Agora / 交易操盤室                                                  │
│ 策略：贏家分點 V4  狀態：監控中  Dashboard：個人化 v3  最近更新：10:42       │
│ [切換策略] [交代僕人] [調整版面] [版本紀錄] [策略工坊]                       │
├──────────────────────────────────────────────────────────────────────────────┤
│ [策略總覽] [候選與進場] [贏家分點] [資金遷移] [事件領先] [持倉與出場] [證據] │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│                     Strategy-specific resizable grid                         │
│                                                                              │
│                     AI-generated widgets and charts                          │
│                                                                              │
├───────────────────────────────────────────────────────────────┬──────────────┤
│ 今日重要變化 / 接近交易 / 待裁示 / 持倉反轉訊號               │ 僕人面板     │
└───────────────────────────────────────────────────────────────┴──────────────┘
```

## 3.2 固定頂部控制列

固定顯示：

```text
策略名稱與版本
策略狀態：監控中 / 暫停 / Shadow / 等待資料 / 需重新驗證
Dashboard 版本
資料新鮮度
交易僕人最後一次調整
待交易員裁示數
風險警示數
```

固定操作：

```text
切換策略
交代僕人
調整版面
查看版本
開啟策略工坊
暫停策略監控
```

---

# 4. 贏家分點策略：交易僕人必須產生的完整 View Set

Claude Design 必須用「贏家分點 V4」具體畫出整套工作區，不接受只畫抽象 Widget 框。

---

## 4.1 View A — 策略總覽

目的：讓交易員在 10 秒內理解今天策略的狀況。

### 初始布局

```text
┌──────────────────────────────┬───────────────────────────────┐
│ 今日候選與交易狀態            │ 策略健康與風險                 │
│ Candidate Funnel             │ Strategy Health               │
├──────────────────────────────┼───────────────────────────────┤
│ Top 候選與 EV                 │ Winner Branch Score 分布       │
│ Candidate Ranking            │ Score Distribution            │
├──────────────────────────────┴───────────────────────────────┤
│ 接近進場 / 加碼 / 減碼 / 出場 — 待裁示隊列                   │
├──────────────────────────────┬───────────────────────────────┤
│ 事件領先警示                   │ 今日資金遷移／出貨警示          │
└──────────────────────────────┴───────────────────────────────┘
```

### 必須生成的 Widget

1. **Candidate Funnel**
   - 新候選
   - 待討論
   - 監控中
   - Shadow 中
   - 已形成部位
   - 已剔除

2. **Strategy Health**
   - 最新資料狀態
   - OOS 穩定度
   - 當前 regime
   - 今日風險
   - 策略可用狀態

3. **Top Candidate Ranking**
   - Winner Branch Score
   - 關係人映射信賴值
   - Cluster-adjusted flow
   - 20d 上漲機率
   - 成本後 EV
   - 流動性

4. **Winner Branch Score Distribution**
   - 全候選分布
   - 當日新進候選
   - 持倉標的
   - 被剔除標的

5. **Decision Queue**
   - 接近進場
   - 建議加碼
   - 建議減碼
   - 建議出場
   - 資料不足需等待

6. **Event Lead Alerts**

7. **Migration / Distribution Alerts**

---

## 4.2 View B — 候選與進場

目的：處理尚未形成部位、接近訊號或需要裁示的標的。

### 初始布局

```text
┌─────────────────────────────────────────┬────────────────────┐
│ 候選排名與篩選                           │ 接近進場隊列        │
│ Candidate Table                         │ Entry Queue        │
├─────────────────────────────────────────┼────────────────────┤
│ 機率 × EV × 信賴值 Scatter              │ 進場條件完成度      │
├─────────────────────────────────────────┴────────────────────┤
│ 候選標的訊號時間軸 / 分點變化 / 事件變化                    │
└──────────────────────────────────────────────────────────────┘
```

### 必須生成的 Widget

1. **Candidate Ranking Table**
   - Rank
   - Symbol
   - Winner Branch Score
   - Relationship Confidence
   - Unified Flow
   - Distribution Risk
   - P(20d Positive)
   - Cost-adjusted EV
   - Entry Readiness

2. **Probability / EV Scatter**
   - x：20d 上漲機率
   - y：成本後 EV
   - size：流動性 / Capacity
   - color：信賴值

3. **Entry Readiness Checklist**
   - Score threshold
   - Cluster-adjusted flow
   - Event risk
   - Liquidity
   - Position room
   - Risk cap

4. **Upcoming Entry Queue**
   - 待裁示
   - 觸發原因
   - 建議初始部位
   - 截止時間
   - 風險

5. **Candidate Signal Timeline**

### Entry Queue 操作

```text
[確認執行]
[修改部位]
[延後觀察]
[送 Shadow]
[要求補充研究]
[剔除候選]
```

「確認執行」建立受治理的 TradeIntentDecision，不等於前端直接下單。

---

## 4.3 View C — 贏家分點情報

目的：理解哪些分點值得信任、為什麼、何時失效。

### 初始布局

```text
┌─────────────────────────────────────┬────────────────────────┐
│ Winner Branch Leaderboard           │ Score Breakdown        │
├─────────────────────────────────────┼────────────────────────┤
│ 歷史績效與 Horizon 比較             │ 分數校準與可靠度        │
├─────────────────────────────────────┴────────────────────────┤
│ 分點歷史訊號、買入、賣出、事件與股票時間軸                  │
└──────────────────────────────────────────────────────────────┘
```

### 必須生成的 Widget

1. **Winner Branch Leaderboard**
2. **Score Component Breakdown**
   - Profitability
   - Consistency
   - Timing
   - Event Lead
   - Relationship Alignment
   - Migration Penalty
   - Liquidity Penalty
3. **Historical Outcome by Horizon**
   - 5d / 20d / 60d / 120d
4. **Score Calibration**
   - 預測信賴區間 vs 實際命中率
5. **Branch Signal Timeline**
6. **Branch Stability / Regime View**

---

## 4.4 View D — 關係人與資金遷移

目的：處理單點觀察不足、分點轉移、關聯分點出貨與身份概率。

### 初始布局

```text
┌──────────────────────────────────────┬───────────────────────┐
│ 關係人 ↔ 分點概率網路圖              │ 支持 / 反向證據       │
├──────────────────────────────────────┼───────────────────────┤
│ 分點 Cluster 與資金遷移 Network      │ Migration Alerts      │
├──────────────────────────────────────┴───────────────────────┤
│ 價格 vs 單點流量 vs Cluster-adjusted 統一流量               │
└──────────────────────────────────────────────────────────────┘
```

### 必須生成的 Widget

1. **Relationship Probability Graph**
   - 關係人 node
   - 分點 node
   - match probability
   - supporting / conflicting evidence

2. **Branch Cluster Network**
   - 同券商
   - 歷史共現
   - 同股資金遷移
   - 反向流

3. **Unified Flow Timeline**
   - 單點淨流
   - Cluster-adjusted 淨流
   - 價格
   - 成交量

4. **Migration Alert Table**
5. **Distribution Risk Panel**
6. **Evidence Conflict Table**

---

## 4.5 View E — 事件領先

目的：判斷分點異常是否具有事件前資訊領先特徵，以及證據可信度。

### 初始布局

```text
┌────────────────────────────────────────┬─────────────────────┐
│ 異常交易到事件的 Lead-Time Distribution │ 事件類型命中矩陣    │
├────────────────────────────────────────┼─────────────────────┤
│ Event Study / CAR                       │ Placebo Comparison  │
├────────────────────────────────────────┴─────────────────────┤
│ 異常分點交易與未來 3～6 月事件時間軸                         │
└──────────────────────────────────────────────────────────────┘
```

### 必須生成的 Widget

1. **Lead-Time Distribution**
2. **Event Type Hit Matrix**
3. **Event Study / CAR Chart**
4. **Random Branch Placebo Comparison**
5. **Upcoming / Historical Event Timeline**
6. **Information Lead Confidence Summary**

UI 必須使用「資訊領先代理」、「統計關聯」、「證據強度」等用語，不得斷言違法或內線。

---

## 4.6 View F — 持倉、加碼、減碼與出場

目的：同時呈現已持有部位與即將產生的退出／調整交易。

### 初始布局

```text
┌─────────────────────────────────────────┬────────────────────┐
│ 當前持倉與 Thesis Health                │ 加碼 / 減碼 / 出場  │
├─────────────────────────────────────────┼────────────────────┤
│ 策略曝險 / Cluster / Correlation        │ 部位風險            │
├─────────────────────────────────────────┴────────────────────┤
│ 持倉標的分點流、事件、價格與失效條件時間軸                  │
└──────────────────────────────────────────────────────────────┘
```

### 必須生成的 Widget

1. **Current Positions Table**
   - Symbol
   - Position
   - Cost
   - PnL
   - Winner Branch Score at Entry / Now
   - Thesis Health
   - Distribution Risk
   - Next Action

2. **Add / Reduce / Exit Queue**
   - 建議行動
   - 觸發條件
   - 建議調整幅度
   - 截止時間
   - 信賴值

3. **Thesis Health Monitor**
4. **Portfolio Exposure & Correlation**
5. **Cluster Concentration**
6. **Liquidity / Capacity**
7. **Invalidation Timeline**
8. **Shadow Alternative Comparison**

### 持倉行動

```text
[確認加碼]
[確認減碼]
[確認出場]
[修改幅度]
[延後觀察]
[送 Shadow 比較]
[回到策略工坊調整規則]
```

---

## 4.7 View G — 證據與監控規則

目的：讓交易員知道操盤室不是黑箱，以及目前畫面與警示依據什麼策略規則。

Widget：

```text
Strategy Version Summary
Active Monitoring Rules
Alert Thresholds
Data Freshness
Evidence References
Recent Rule Changes
Dashboard Change History
```

此 View 可低頻使用，但必須存在。

---

# 5. 交易僕人必須先產生完整畫面，而不是等待交易員自行搭建

## 5.1 初始 Dashboard Proposal

交易僕人產生的 Proposal 必須包含：

```ts
TradingRoomWorkspaceProposal {
  strategyId
  strategyVersion
  proposalId
  generatedAt
  views[]
  rationale
  dataAvailability
  warnings
  personalizationApplied
}
```

每個 View：

```ts
TradingRoomViewSpec {
  id
  title
  purpose
  order
  layoutTemplate
  widgets[]
}
```

每個 Widget：

```ts
TradingRoomWidgetSpec {
  id
  widgetType
  title
  purpose
  whyIncluded
  dataSource
  query
  chartSpec
  interactions
  placement
  minSize
  maxSize
  sensitivity
}
```

## 5.2 Proposal Preview 必須顯示

```text
- 所有 View 縮圖
- 每個 View 的 Widget 數量
- 為何需要這個 View
- 哪些資料完整
- 哪些 Widget 使用推定或暫缺資料
- 哪些呈現套用了交易員個人偏好
```

---

# 6. 版面編輯模式

## 6.1 進入方式

頂部操作：

```text
[調整版面]
```

進入後：

- Grid 顯示欄位與落點提示
- Widget 顯示拖曳 handle
- Widget 顯示 resize handle
- Widget 顯示移除／更多操作
- 未儲存變更有固定提示
- 離開時需儲存或放棄

## 6.2 交易員可以做的事

```text
- 拖曳 Widget 改位置
- 改變寬度與高度
- 移除 Widget
- 從 Widget Library 加回已移除 Widget
- 新增既有 Widget
- 複製 Widget
- 改變圖表型態
- 全螢幕查看
- 還原策略預設布局
- 儲存為新版本
- 回滾上一版本
```

## 6.3 移除不等於刪資料

移除 Widget 只會：

```text
visible=false
```

不得刪除其資料或歷史偏好。已移除 Widget 可從 Library 找回。

## 6.4 Resize 規則

每個 Widget 都有：

```text
minWidth / minHeight
preferredWidth / preferredHeight
maxWidth / maxHeight
supportedBreakpoints
```

超過適合尺寸時需切換 detail density，而不是只把空白放大。

---

# 7. Widget 操作選單

每個 Widget 右上角必須有：

```text
[⋮]
```

選單內容：

```text
交代僕人修改
拖曳位置
調整大小
換一種圖表
編輯資料範圍
新增比較基準
複製 Widget
移除 Widget
標記有用
標記無用
查看為何出現在此
查看資料與證據
```

---

# 8. 點選 Widget 後，交代交易僕人修改該 Widget

這是本版最重要的互動。

## 8.1 啟動方式

交易員可：

```text
- 點擊 Widget 後按「交代僕人修改」
- 直接從 Widget 選單開啟
- 在全域僕人輸入框輸入「修改目前這張圖」
```

右側開啟：

```text
Widget Adjustment Drawer
```

Drawer 必須知道目前 Widget context：

```text
Widget 目的
資料來源
目前欄位
目前篩選
目前時間窗口
目前圖表型態
所屬策略
所屬 View
相關證據
```

## 8.2 交易員可交代的例子

```text
把這張圖改成分點為列、日期為欄的熱圖。
```

```text
不要只看單點，把 cluster-adjusted flow 疊在同一張圖。
```

```text
只看最近 20 個交易日，並排除日成交金額低於三億元的股票。
```

```text
把事件發布前 60 日到後 20 日畫出來，重大訊息用垂直線標示。
```

```text
這張太亂，只保留前 10 名分點，再加一欄 20 日成本後報酬。
```

```text
不要網路圖，改成可排序表格，讓我直接看每個分點的信賴值、遷移風險和歷史績效。
```

```text
把此圖拆成兩張：一張看關係人概率，一張看反向出貨。
```

```text
新增一個比較：原始單點分數 vs cluster-adjusted 分數。
```

## 8.3 僕人不能直接改，先提出 Widget Revision Proposal

回應格式：

```text
我準備做以下調整：

1. 圖表由 Network 改為 Heatmap
2. y 軸改為分點 Cluster
3. 加入 cluster-adjusted net flow
4. 時間窗口改為最近 20 日
5. 排除日成交額低於三億元的標的

原因：目前畫面適合探索關係，但不適合快速比較持續性與出貨反轉。

注意：兩個分點的歷史 cluster 信賴值低於 50%，我會標記為推定資料。
```

顯示 Before / After Preview。

操作：

```text
[套用修改]
[再調整]
[保留原圖並新增一張]
[取消]
```

---

# 9. 新增 Widget

## 9.1 Add Widget 入口

版面編輯模式中顯示：

```text
[＋ 新增 Widget]
```

也可直接對僕人說：

```text
幫我新增一張圖，比較贏家分點出現後 5、20、60 日的成本後報酬。
```

## 9.2 Widget Library 分類

```text
候選與訊號
贏家分點評分
關係人概率
分點流與遷移
事件領先
交易機率與 EV
持倉與出場
部位風險
Shadow
證據與資料品質
自訂
```

## 9.3 僕人產生新 Widget 的流程

```text
交易員描述需求
→ 僕人判斷既有 Widget 是否可用
→ 若可用，建立新的 WidgetSpec instance
→ 若需要新圖表組合，產生受控 ChartSpec
→ 顯示 Preview
→ 交易員裁示
→ 加入 Workspace 並版本化
```

若需要未被前端 renderer 支援的全新元件：

```text
此呈現方式需要新增前端元件。
我可以先用現有的 Heatmap + Table 組合替代，或建立新元件需求。
```

不得直接生成任意 React / JavaScript 並注入 production。

---

# 10. 個人化、版本與回滾

每個 Workspace 必須是：

```text
per trader
per strategy
per strategy version
```

## 10.1 Dashboard Version

```ts
TradingRoomDashboardVersion {
  id
  userId
  strategyId
  strategyVersion
  dashboardVersion
  generatedBy
  previousVersionId
  changeSummary
  views
  createdAt
  status
}
```

## 10.2 版本名稱例子

```text
v1 — 交易僕人初始提案
v2 — 交易員調整 Widget 順序與大小
v3 — 新增 Cluster-adjusted Flow
v4 — 回滾事件領先布局
```

## 10.3 Change Log

顯示：

```text
什麼時間改了什麼
由交易員或交易僕人提出
為什麼改
影響哪些 View / Widget
是否已評估效果
可否回滾
```

---

# 11. 交易裁示與部位裁示

交易操盤室必須同時處理：

```text
- 尚未持有、即將形成的進場交易
- 已持有部位的加碼
- 已持有部位的減碼
- 已持有部位的出場
```

## 11.1 Trade Decision Card

```text
Symbol
Action：進場 / 加碼 / 減碼 / 出場
觸發策略規則
Winner Branch Score
關係人映射信賴值
成本後 EV
建議部位
目前部位
主要風險
有效期限
```

操作：

```text
確認執行
修改部位
延後
送 Shadow
拒絕
要求補充研究
```

## 11.2 執行狀態

交易員看到：

```text
待裁示
已確認
檢查中
待執行
已成交
部分成交
未通過安全檢查
已取消
```

不得暴露 Management、RuntimeBinding、ArtifactState 等後台詞彙。

---

# 12. 資料模型

## 12.1 TradingRoomWorkspace

```ts
type TradingRoomWorkspace = {
  id: string;
  userId: string;
  strategyId: string;
  strategyVersion: string;
  dashboardVersion: number;
  activeViewId: string;
  views: TradingRoomViewSpec[];
  status: "generating" | "preview" | "editing" | "active" | "stale" | "archived";
  generatedBy: "trading_servant" | "user_modified" | "learned_personalization";
  createdAt: string;
  updatedAt: string;
};
```

## 12.2 WidgetPlacement

```ts
type WidgetPlacement = {
  x: number;
  y: number;
  width: number;
  height: number;
  minWidth: number;
  minHeight: number;
  maxWidth?: number;
  maxHeight?: number;
};
```

## 12.3 WidgetRevisionProposal

```ts
type WidgetRevisionProposal = {
  id: string;
  workspaceId: string;
  viewId: string;
  widgetId: string;
  instruction: string;
  beforeSpec: TradingRoomWidgetSpec;
  proposedSpec: TradingRoomWidgetSpec;
  rationale: string;
  warnings: string[];
  dataAvailability: "complete" | "partial" | "unavailable";
  status: "preview" | "accepted" | "rejected" | "superseded";
};
```

---

# 13. BFF Contract Requirements

```text
POST /bff/agora/strategies/:strategyId/trading-room/proposals
GET  /bff/agora/strategies/:strategyId/trading-room/proposals/:proposalId
POST /bff/agora/strategies/:strategyId/trading-room/proposals/:proposalId/accept

GET  /bff/agora/trading-room/workspaces/:workspaceId
PATCH /bff/agora/trading-room/workspaces/:workspaceId/layout
POST /bff/agora/trading-room/workspaces/:workspaceId/views
PATCH /bff/agora/trading-room/workspaces/:workspaceId/views/:viewId

POST /bff/agora/trading-room/workspaces/:workspaceId/widgets
PATCH /bff/agora/trading-room/workspaces/:workspaceId/widgets/:widgetId
POST /bff/agora/trading-room/workspaces/:workspaceId/widgets/:widgetId/revision-proposals
POST /bff/agora/trading-room/widget-revision-proposals/:proposalId/accept

GET  /bff/agora/trading-room/workspaces/:workspaceId/versions
POST /bff/agora/trading-room/workspaces/:workspaceId/versions/:versionId/rollback

POST /bff/agora/trading-room/trade-decisions
GET  /bff/agora/trading-room/trade-decisions
```

所有資料必須限制在當前 Agora 使用者與其策略範圍。

---

# 14. Claude Design 必畫 Artboards

Claude Design 必須至少交付以下 18 張 desktop artboards：

1. 策略工坊按下「加入交易操盤室」後的建立進度。
2. 贏家分點 V4 完整 Workspace Proposal — 所有 View 縮圖。
3. 策略總覽 View。
4. 候選與進場 View。
5. 贏家分點情報 View。
6. 關係人與資金遷移 View。
7. 事件領先 View。
8. 持倉、加碼、減碼與出場 View。
9. 證據與監控規則 View。
10. Decision Queue — 同時有進場與出場交易。
11. 進入「調整版面」模式。
12. 拖曳 Widget 的進行中狀態。
13. 改變 Widget 大小的進行中狀態。
14. 刪除 Widget 後的畫面與 Widget Library。
15. 新增 Widget Drawer。
16. 點選 Widget 後的「交代僕人修改」Drawer。
17. Widget Before / After Revision Proposal。
18. Dashboard Version History / Rollback。

另外必須畫 1 張對照圖：

```text
贏家分點策略操盤室
vs
另一種策略操盤室（例如技術突破）
```

兩張必須在 View 結構、主視覺、Widget 內容與布局上顯著不同，證明系統不是固定模板換資料。

---

# 15. 視覺與互動要求

## 15.1 視覺方向

```text
專業交易桌
高資訊密度但不混亂
深色或中性工作環境
資料、圖表與裁示隊列優先
交易僕人為輔助面板，不佔據主畫面
```

## 15.2 Widget Chrome

每個 Widget 必須有：

```text
標題
最後更新時間
資料完整度
拖曳 handle
resize handle
更多操作
異常標記
進入詳細模式
```

## 15.3 異常

以下狀態需明顯標示：

```text
資料過期
信賴值下降
策略規則失效
分點遷移風險
持倉出場訊號
風險限制不足
Widget 使用推定資料
```

---

# 16. Do / Do Not Do

## Do

```text
- 先由交易僕人產生完整可用的 Workspace
- 讓交易員用拖曳與自然語言共同調整
- 每個 Widget 可單獨改呈現方式
- 所有修改先預覽再套用
- 所有版面與 Widget 版本化
- 讓交易員同時看到進場與出場裁示
- 以贏家分點策略完整畫出具體資料與圖表
```

## Do Not Do

```text
- 不要丟空白 Dashboard 給交易員自己搭
- 不要只允許調整 Widget 順序
- 不要讓 AI 直接偷偷改畫面
- 不要只做一張通用 Dashboard
- 不要把交易僕人做成聊天視窗主畫面
- 不要顯示 Pantheon / Management 工程內部詞彙
- 不要讓 Agent 注入任意前端程式碼
```

---

# 17. Acceptance Checklist

```text
[ ] 策略工坊完成後可建立完整交易操盤室提案
[ ] 交易僕人一次產生所有必要 View 與 Widget
[ ] 贏家分點策略至少有七個專屬 View
[ ] Proposal 可整體套用或逐頁預覽
[ ] 交易員可拖曳 Widget
[ ] 交易員可改變 Widget 大小
[ ] 交易員可移除 Widget 並從 Library 找回
[ ] 交易員可新增既有 Widget
[ ] 交易員可用自然語言要求新增 Widget
[ ] 交易員可點選單一 Widget 交代僕人修改
[ ] 僕人修改 Widget 前提供 Before / After Preview
[ ] 可保留原 Widget 並新增修改版
[ ] 所有 Workspace / View / Widget 修改均版本化
[ ] 可查看 Change Log 並回滾
[ ] 進場、加碼、減碼、出場均可在操盤室中裁示
[ ] 另一種策略的操盤室與贏家分點策略布局顯著不同
[ ] 交易僕人不直接注入任意前端程式碼
[ ] Agora 畫面不暴露 Management 或後端工程詞彙
```

---

# 18. 最終產品定義

交易操盤室不是固定 Dashboard，也不是空白 Widget Builder。

它是：

> **交易僕人根據完整策略規則，先替交易員準備好的策略專屬操盤工作區。**

對贏家分點策略，交易僕人要自動產生：

```text
策略總覽
候選與進場
贏家分點評分
關係人概率與分點群組
資金遷移與出貨
事件領先
持倉、加碼、減碼與出場
證據與監控規則
```

交易員接著能：

```text
拖曳
刪除
新增
改變大小
切換圖表
修改資料範圍
點選單一 Widget 交代僕人重做
比較 Before / After
儲存個人化版本
回滾
```

這樣才符合 Agora 的核心：

```text
交易僕人先把事情準備好；
交易員只裁示、調整與掌控。
```
