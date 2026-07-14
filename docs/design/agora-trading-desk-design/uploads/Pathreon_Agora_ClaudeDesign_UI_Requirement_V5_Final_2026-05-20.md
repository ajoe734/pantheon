# Pathreon Agora — Claude Design UI Requirement V5 Final

> 文件目的：交付 Claude Design 產出 Agora 前端 UI 設計稿。
> 版本：V5 Final / Trading Servant Desk + Strategy Lens + Execution Performance 版
> 日期：2026-05-20
> 範圍：只設計 Agora，不設計 Pathreon Management。
> 重要修正：Agora 不是散戶聊天工具，不是後台，不是一般 watchlist。Agora 是給高階交易員、股票大戶、分析師使用的 **個人 AI 交易工作桌**。
> 核心語氣：交易員是主人，AI 是交易研究助理 / 交易僕人。UI 不使用「問 AI」作為主概念，而是「交代助理做事」。

---

## 0. 最終產品定位

Agora 是交易員的 **個人 AI 交易工作桌**。

它讓交易員能夠：

```text
1. 交代研究助理依據自己的交易想法，先找出一批候選股票。
2. 一檔一檔與助理討論，保留、剔除、暫放、深入研究、送入影子追蹤。
3. 依據不同策略視角，動態產生完全不同的監控畫面。
4. 設計與調整多組交易策略，包含候選池、進出場、資金配比、風控、回測。
5. 觀察多組策略執行狀況，追蹤績效、干預、偏離、改善建議。
6. 讓助理記住自己的偏好、版面、策略邏輯、刪選原因與交易習慣。
```

Agora 的核心不是聊天，而是：

```text
交易員下達任務
  -> 助理拆解任務
  -> 助理產生候選池 / 研究材料 / 監控 dashboard
  -> 交易員裁示
  -> 系統納入監控 / 影子追蹤 / 策略執行
  -> 助理持續整理與提出調整
  -> 交易員績效與策略執行可被追蹤
```

---

## 1. 禁止方向

Claude Design 不要做成以下樣子：

```text
散戶股票聊天工具
一般 AI 問答頁
一堆單獨功能頁
單一 watchlist
固定 dashboard
單檔股票詳情為主
可愛聊天機器人
投資小白教育產品
Management 後台簡化版
```

不要使用以下主文案：

```text
Ask AI
Chat with AI
AI says
AI recommends
問 AI
與 AI 聊天
```

應使用以下語氣：

```text
交代研究任務
請助理整理
助理已替您篩出
待您裁示
納入監控
剔除候選
暫放觀察
深入研究
送入影子追蹤
請助理重排版面
套用助理建議版面
```

Agora 使用者不應看到：

```text
Pathreon Management
governance
runtime binding
capital binding
artifact_state
deployment_stage
operator gate
BFF
registry
broker live
human gate
```

如果需要送審或後台治理，在 Agora 表面上只能顯示為：

```text
送安全檢查
送策略驗證
申請紙上測試
請平台審核
待審查
```

---

## 2. 最終資訊架構

Agora 是一張交易桌，主頁只有三個主要頁籤。

```text
Agora / My AI Trading Desk
├── 交易操盤室
├── 策略工坊
└── 策略執行與績效
```

橫切功能不是主頁，而是 drawer / panel / side utility：

```text
交易助理指令列
候選討論 Drawer
版面調整 Drawer
Widget Proposal Drawer
影子追蹤 Drawer
Journal / Replay Drawer
訓練助理 Drawer
個人化紀錄 Drawer
```

### 2.1 三個主頁籤的定義

| 主頁籤 | 用途 | 交易員在這裡做什麼 |
|---|---|---|
| 交易操盤室 | 盯盤 + 監控 + 交易訊號確認 | 看不同策略 lens 下的候選池、監控池、即將觸發的進場/出場、持有部位提醒 |
| 策略工坊 | 發想、設計、研究、回測、調整策略 | 用自然語言描述策略，助理依 LEAN 模組整理 universe/alpha/portfolio/risk/execution，產生候選池、回測、配比、建議 |
| 策略執行與績效 | 追蹤多組策略實際執行結果 | 看哪些策略按原設定執行、哪些被交易員干預、干預結果如何、助理建議如何調整 |

### 2.2 重要修正

第三頁不是「部位 Cockpit」。
部位是策略執行結果的一部分，應放在「策略執行與績效」中呈現。

「交易操盤室」中會呈現：

```text
可能快要進場的標的
可能需要出場的持有部位
策略 lens 下正在觸發的候選股
需要交易員確認的交易訊號
```

---

## 3. Shell Layout

所有 Agora 頁面共用同一個 Trading Desk Shell。

```text
┌──────────────────────────────────────────────────────────────┐
│ Top Bar                                                      │
│  使用者名稱 / 交易助理狀態 / 今日任務數 / 影子追蹤 / Journal │
├──────────────────────────────────────────────────────────────┤
│ Trading Servant Command Bar                                  │
│  「交代助理：輸入策略想法、盯盤任務、部位問題...」             │
├──────────────────────────────────────────────────────────────┤
│ Main Tabs                                                    │
│  [交易操盤室] [策略工坊] [策略執行與績效]                    │
├──────────────────────────────────────────────────────────────┤
│ Main Workspace                                               │
│  依目前頁籤顯示三欄或多欄專業交易工作台                     │
└──────────────────────────────────────────────────────────────┘
```

### 3.1 Command Bar 文案

Placeholder：

```text
交代助理：輸入策略想法、盯盤任務、分點追蹤或部位檢查...
```

快速任務按鈕：

```text
建立策略視角
篩候選股
分析分點
找落後補漲
檢查部位
重排版面
送影子追蹤
產生回測
```

---

## 4. 第一主頁籤：交易操盤室

### 4.1 頁面目標

交易操盤室不是單一 watchlist。
它是交易員依據多種策略 lens 監控一批股票與部位的地方。

交易操盤室要回答：

```text
目前我有哪些策略視角正在運作？
每個策略視角下有哪些候選股？
哪些標的已納入監控池？
哪些標的快接近進場？
哪些持有部位快接近出場？
哪些標的需要我裁示？
助理建議我現在先處理哪一個？
```

### 4.2 Layout

```text
┌──────────────────────────────────────────────────────────────┐
│ 交易操盤室                                                     │
├──────────────────────────────────────────────────────────────┤
│ Strategy Lens Switcher                                        │
│ [籌碼大戶部位建立] [AI Server 落後補漲] [技術突破] [+新增]     │
├──────────────┬─────────────────────────────┬────────────────┤
│ Lens Sidebar │ Lens Dashboard              │ 助理裁示區       │
│              │                             │                │
│ 候選池 38    │ 依策略 lens 產生不同 widget │ 為什麼選它       │
│ 待討論 12    │ Candidate / Monitoring Board│ 疑慮             │
│ 監控中 9     │ Entry Signals               │ 下一步           │
│ 影子追蹤 4   │ Exit Signals                │ Evidence         │
│ 已剔除 13    │ Position Alerts             │ Actions          │
└──────────────┴─────────────────────────────┴────────────────┘
```

### 4.3 Strategy Lens Switcher

每個 lens 都是一套不同的監控邏輯與 dashboard layout。

示例：

```text
籌碼大戶部位建立
AI Server 供應鏈落後補漲
技術突破
事件交易
大額資金可承接量
```

每個 lens tab 顯示：

```text
lens name
候選數
監控數
待裁示數
異常數
```

### 4.4 Lens Sidebar

每個 lens 下有狀態分組：

```text
候選池
待討論
監控中
影子追蹤
已剔除
暫放
```

### 4.5 Candidate / Monitoring Board

中間主區依 lens 顯示不同 dashboard。

#### Lens: 籌碼大戶部位建立

必畫 widget：

```text
候選排名表
分點連續買超 Ranking
分點 × 日期 Heatmap
價格 vs 分點累積買超
關聯分點反向流 Network
流動性 / 承接量
出貨風險警示
同族群資金輪動
即將觸發進場條件
持有部位出場警示
```

#### Lens: 產業落後補漲

必畫 widget：

```text
產業 / 供應鏈位置圖
同族群漲幅比較
相似度 × 落後幅度 Scatter
候選排名表
催化事件 Timeline
籌碼支持分數
風險 / 反方論點
即將觸發進場條件
```

#### Lens: 技術突破

必畫 widget：

```text
突破價位表
成交量確認
ATR / 波動
壓力區 / 支撐區
假突破風險
回測相似案例
進場 / 停損條件
```

### 4.6 Candidate Row Actions

每一列候選股都要有：

```text
討論
納入監控
深入研究
送影子追蹤
剔除
暫放
```

注意：剔除不是真的刪資料。剔除要保留為 negative preference / training data。

### 4.7 Candidate Review Drawer

點擊候選股後右側打開 Drawer。

必須顯示：

```text
股票代號 / 名稱
助理為什麼選它
證據摘要
主要分數
策略 lens 符合程度
疑慮
反方論點
下一步建議
```

Actions：

```text
納入監控池
送影子追蹤
深入研究
剔除
暫放
讓助理提出反方論點
寫入我的偏好
```

### 4.8 交易確認 / 出場提醒

交易操盤室也必須呈現：

```text
可能快要進場的交易
已持有部位可能需要出場的提醒
```

用獨立區塊：

```text
交易候選 / 待確認
持有部位 / 出場提醒
```

每個提醒不可直接下單，只能：

```text
確認已處理
送影子追蹤
產生交易計畫
加入 Journal
忽略並記錄原因
```

---

## 5. 第二主頁籤：策略工坊

### 5.1 頁面目標

策略工坊是交易員描述策略想法、讓助理拆解、研究、回測、調整的地方。

交易員在這裡不是「問問題」，而是：

```text
交代一個策略研究任務
助理即時拆解成策略流程
依 LEAN 模組整理 Universe / Alpha / Portfolio / Risk / Execution
產生候選池
討論配比與進出場
執行回測
提出調整建議
如果交易員要採用，就加入執行頁簽
```

### 5.2 Layout

```text
┌──────────────────────────────────────────────────────────────┐
│ 策略工坊                                                       │
├──────────────────────────────┬───────────────────────────────┤
│ 左側：策略描述 / 對話式建構    │ 右側：助理即時產生研究材料       │
│                              │                               │
│ 交易員描述策略               │ Universe                       │
│ 助理追問缺項                 │ Alpha / Signal                 │
│ 交易員調整條件               │ Portfolio / 配比               │
│                              │ Risk / 風控                    │
│                              │ Execution / 進出場             │
│                              │ Candidate Pool                 │
│                              │ Backtest                       │
└──────────────────────────────┴───────────────────────────────┘
```

### 5.3 左側：策略描述區

Placeholder：

```text
交代助理：描述您的交易策略想法，例如「我想找有大戶建立部位、價格還沒大漲、流動性足夠的股票」...
```

助理應該能追問：

```text
時間週期？
市場範圍？
流動性下限？
進場條件？
出場條件？
是否要排除已漲多？
資金配比？
是否要分批？
停損 / invalidation？
```

### 5.4 右側：LEAN Strategy Builder

右側必須以 LEAN 概念組織，但不能叫得太工程化。可顯示成：

```text
標的範圍 Universe
訊號條件 Alpha / Signal
資金配比 Portfolio
風控規則 Risk
執行規則 Execution
```

每個模組是可編輯卡片。

#### Universe 卡片

```text
市場：TWSE / OTC
流動性條件
產業條件
價格區間
成交金額
排除條件
```

#### Alpha / Signal 卡片

```text
分點連續買超
價格尚未大漲
同族群強勢
量能確認
新聞催化
```

#### Portfolio / 配比卡片

```text
候選池上限
單檔最大權重
分批規則
等權 / 分數加權
流動性調整
```

#### Risk 卡片

```text
停損
最大回撤
流動性不足排除
出貨風險排除
同族群集中度
```

#### Execution / 進出場卡片

```text
進場觸發
出場觸發
加碼條件
減碼條件
觀察期
```

### 5.5 Candidate Pool

策略工坊也會產生候選池。

欄位：

```text
Rank
Symbol
符合策略原因
訊號分數
風險分數
流動性
建議配比
AI Comment
Actions
```

Actions：

```text
納入策略
剔除
調整權重
送影子追蹤
加入操盤室監控
```

### 5.6 Backtest Panel

必須顯示：

```text
回測期間
年化報酬
最大回撤
勝率
Sharpe
交易次數
平均持有日
最大連虧
和 benchmark 比較
```

並顯示：

```text
助理調整建議
```

例如：

```text
流動性門檻提高後，回撤下降但報酬下降。
排除已漲 20% 以上候選後，勝率提升。
分點連續買超由 3 日改成 5 日後，交易次數下降但品質提升。
```

### 5.7 Strategy Flow Diagram

策略工坊必須畫出流程：

```text
Universe -> Signal -> Candidate Pool -> Portfolio Weight -> Entry -> Monitor -> Exit -> Journal / Shadow
```

每個節點可以點擊調整。

### 5.8 加入執行頁簽

如果交易員覺得策略可以進行，就按：

```text
加入策略執行與追蹤
```

這不是 live 下單，而是加入 Agora 的策略執行/追蹤頁，開始 shadow/paper-like tracking 或使用者手動執行追蹤。

---

## 6. 第三主頁籤：策略執行與績效

### 6.1 頁面目標

這頁不是單純看持倉。
它要看：

```text
有哪些策略被設定
每個策略執行歷史如何
哪些交易是按照原策略執行
哪些交易是交易員干預
干預後結果如何
策略是否需要調整
助理建議怎麼改
```

### 6.2 Layout

```text
┌──────────────────────────────────────────────────────────────┐
│ 策略執行與績效                                                 │
├──────────────┬──────────────────────────────┬────────────────┤
│ Strategy List│ Execution / Performance Board │ Assistant Notes│
│              │                              │                │
│ 策略 A        │ PnL / Drawdown / Win Rate    │ 調整建議         │
│ 策略 B        │ 按策略執行 vs 人工干預       │ 異常             │
│ 策略 C        │ 持有部位 / 已出場            │ 下一步           │
└──────────────┴──────────────────────────────┴────────────────┘
```

### 6.3 Strategy List

每個策略 card 顯示：

```text
策略名稱
目前狀態：追蹤中 / Shadow 中 / 使用中 / 暫停 / 歸檔
候選池數
持有部位數
累積交易數
績效
AI 評價
是否需要調整
```

### 6.4 Performance Board

必須顯示：

```text
總報酬
最大回撤
勝率
平均盈虧
Sharpe
交易次數
平均持有天數
策略遵循率
人工干預次數
人工干預後績效
AI 建議被採納後績效
AI 建議被拒絕後反事實結果
```

### 6.5 Execution History

每筆交易紀錄：

```text
日期
股票
策略原始建議
交易員實際行動
是否偏離策略
偏離原因
結果
AI 事後評估
是否形成訓練資料
```

### 6.6 Intervention Tracking

這是這頁最重要的功能。

要顯示：

```text
哪些交易按原策略做
哪些被交易員改了
改了之後變好還是變差
交易員常在哪些地方干預
AI 是否應調整策略
```

範例：

```text
策略：籌碼大戶部位建立
過去 20 筆候選：
- 按原策略執行 11 筆，平均 +3.2%
- 交易員手動剔除 5 筆，後續平均 +1.1%
- 交易員手動加入 4 筆，後續平均 -0.8%

助理建議：
交易員手動加入標的表現較差，建議提高候選池外標的進入門檻。
```

### 6.7 Adjustment Suggestions

助理要提出：

```text
調整 Universe
調整 signal threshold
調整候選排序權重
調整進場條件
調整出場條件
調整部位配比
暫停某策略
增加某個監控 panel
```

每個建議有：

```text
原因
依據
預期效果
風險
套用 / 暫不套用 / 送回策略工坊修改
```

---

## 7. 版面個人化與 AI 改畫面

### 7.1 必須明確顯示助理可以改版面

每個主頁籤上方都有：

```text
版面：助理個人化 v7
最近調整：將「出貨風險」移到第一排
原因：您最近常因關聯分點賣超剔除候選
效果：影子追蹤命中率 +8%
[查看調整] [請助理重排] [還原]
```

### 7.2 交代助理改版面

按「請助理重排」後打開 drawer。

交易員可說：

```text
這個策略先給我看出貨風險。
不要再顯示 RSI。
新增一個同券商分點反向流圖。
把流動性放到前面。
```

### 7.3 助理提出版面提案

助理不得直接改 production view，必須提出提案：

```text
助理建議版面調整：
1. 將出貨風險移到第一列
2. 隱藏 RSI
3. 新增同券商分點反向流 Network

理由：您最近 5 次候選討論中，有 3 次因出貨風險剔除。
```

按鈕：

```text
套用
逐項調整
拒絕
不要再提出這類調整
```

---

## 8. Widget / Chart Generation

### 8.1 允許

助理可以產生：

```text
WidgetSpec
ChartSpec
DashboardRecipe
```

不可以產生任意 React/JS code。

### 8.2 每個 widget 都有 menu

```text
釘選
隱藏
上移
下移
換圖表
新增相關圖表
標記有用
標記沒用
請助理解釋為什麼放這裡
```

### 8.3 新 widget 提案

如果現有 widget 不夠，助理提出：

```text
New Widget Proposal
```

顯示：

```text
新圖表名稱
解決什麼問題
用哪些資料
長什麼樣
為什麼現有 widget 不夠
是否先用 prototype 顯示
```

---

## 9. Personalization Memory

Agora 必須記住每個交易員的呈現方式。

資料層：

```text
TraderPersonalizationProfile
StrategyLensPersonalization
DashboardRecipe
WidgetPreference
PersonalizationEvent
DashboardPerformanceEvaluation
```

UI 必須有：

```text
Dashboard Change Log
Personalization Status Bar
Dashboard Switcher
Widget Feedback Controls
Rollback
```

---

## 10. Shadow / Journal / Training

這些是橫切工具。

### 10.1 Shadow Book

每個 watch candidate、strategy mission、strategy execution、position decision 都可以送入 Shadow。

Shadow 顯示：

```text
AI 建議
交易員裁示
實際結果
影子結果
誰比較好
學到了什麼
```

### 10.2 Journal / Replay

自動產生：

```text
候選討論紀錄
策略建立紀錄
策略干預紀錄
持有部位檢查紀錄
影子結果
AI 事後評估
```

### 10.3 Train My AI

在任何地方都能修正助理：

```text
這不是我的風格
這個分點我不信
下次先看流動性
不要把已漲多的放太前面
```

---

## 11. 必畫畫面清單

Claude Design 請至少畫以下 12 張：

1. **My AI Trading Desk — 交易操盤室 tab**
   包含 strategy lens switcher、候選池、監控池、交易/出場提醒、助理裁示區。

2. **交易操盤室 — 籌碼大戶部位建立 lens**
   顯示分點 heatmap、買盤 ranking、關聯分點反向流、出貨風險。

3. **交易操盤室 — 產業落後補漲 lens**
   顯示供應鏈圖、同族群漲幅、相似度 × 落後幅度 scatter、候選排名。

4. **Candidate Review Drawer**
   顯示助理為什麼選、疑慮、證據、納入監控/剔除/送影子。

5. **策略工坊 — 策略描述與即時研究材料**
   左側交易員描述，右側助理生成 Universe / Alpha / Portfolio / Risk / Execution。

6. **策略工坊 — 回測與調整建議**
   顯示 backtest metrics、候選池、助理調整建議。

7. **策略工坊 — 策略流程圖**
   Universe -> Signal -> Candidate -> Portfolio -> Entry -> Monitor -> Exit。

8. **策略執行與績效 — 多策略總覽**
   顯示多組策略狀態、績效、持有部位、是否需調整。

9. **策略執行與績效 — 干預追蹤**
   顯示按策略執行 vs 交易員干預，結果比較。

10. **Dashboard Layout Proposal Drawer**
    顯示助理建議改版面、before/after、套用/拒絕。

11. **Widget Menu + New Widget Proposal**
    顯示每個 widget 可以 pin/hide/move/change chart，與新增圖表提案。

12. **Dashboard Change Log / Personalization Status**
    顯示助理如何記住交易員偏好與版面調整效果。

---

## 12. 視覺風格要求

```text
專業交易桌
高密度但清楚
沉穩、權威、快速
像交易員助理整理好的工作台
不是散戶 app
不是聊天 app
不是遊戲化介面
```

設計元素：

```text
多欄工作台
高資訊密度表格
可視化 heatmap / network / scatter / flow
右側裁示 drawer
頂部助理指令列
策略 lens tab
dashboard version / personalization 狀態條
```

---

## 13. 最終驗收標準

Claude Design 交付稿必須讓人一眼看出：

```text
這是一張交易員 AI 交易桌。
主頁籤是交易操盤室、策略工坊、策略執行與績效。
不同策略 lens 會有不同 dashboard。
交易員可以叫助理改版面。
助理會提出版面提案，不是自己亂改。
候選股是一批，不是單檔。
交易員可以逐檔裁示。
策略工坊能描述、拆解、回測、配比、調整策略。
策略執行頁能追蹤多策略績效與交易員干預結果。
Shadow / Journal / Train AI 是橫切工具。
整體不是散戶 AI 問答工具。
```

