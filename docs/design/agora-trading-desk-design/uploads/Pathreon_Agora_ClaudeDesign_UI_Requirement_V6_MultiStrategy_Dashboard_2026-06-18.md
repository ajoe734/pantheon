# Pathreon Agora — Claude Design UI Requirement V6
## Multi-Strategy Dashboard / Trading Servant Workspace / Personalization Edition

> 文件用途：直接交付 Claude Design 產出 Agora 高保真設計稿。  
> 日期：2026-06-18  
> 範圍：只設計 Agora；不設計、提及或暴露 Pathreon Management。  
> 本版取代：V2、V3、V4、V5 Agora Claude Design requirements。  
> 不可妥協：**不同 Strategy Lens 必須是結構顯著不同的完整 Dashboard，不接受同一版型只替換資料、標題或 widget。**

---

# 0. Claude Design 的唯一任務

Claude Design 必須設計一套給高階交易員、分析師、股票大戶使用的 **Agora 個人 AI 交易桌**。

這不是散戶問答產品，不是一般 Watchlist，不是固定 Dashboard，也不是後台管理系統。

交易員的日常核心只有三個工作區：

```text
1. 交易操盤室
   多個 Strategy Lens，各自使用不同版面、不同資料、不同決策流程。

2. 策略工坊
   交易員描述策略，交易僕人在另一側即時生成策略結構、資料、候選池、回測與調整建議。

3. 策略執行與績效
   追蹤多組策略的執行歷史、績效、人工干預、偏離與優化建議。
```

所有 Shadow、Journal、Replay、訓練、專家審視、版面個人化都是橫切能力，不是主要平行頁面。

---

# 1. 產品語氣與角色關係

## 1.1 使用者角色

使用者是成熟、驕傲、有自己方法的交易員或大額資金持有人。

他不是來「問 AI」。他是來交代自己的交易僕人做事。

主文案使用：

```text
交代交易僕人
僕人已替您整理
待您裁示
納入監控
剔除候選
暫放觀察
深入研究
產生候選池
重排版面
換一種呈現
送影子追蹤
提交執行意圖
```

禁用主文案：

```text
Ask AI
Chat with AI
AI says
AI recommends
問 AI
AI 助手建議
```

可在技術說明中使用 AI，但使用者介面主語應是「交易僕人」或「研究助理」。

## 1.2 Agora 不得顯示的內容

Agora 使用者不能看見：

```text
Pathreon Management
中央治理
operator gate
risk-owner gate
runtime binding
capital binding
artifact_state
deployment_stage
BFF
registry
其他 Agora 使用者
其他交易員的人格或策略
```

若後端需要審查或治理，Agora UI 只顯示：

```text
送策略驗證
申請紙上測試
送安全檢查
等待平台審視
待確認
```

---

# 2. 全域 Shell：一張交易桌，三個主頁籤

## 2.1 主結構

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Agora / 交易員名稱 / 市場狀態 / 交易僕人狀態 / Shadow / Journal     │
├─────────────────────────────────────────────────────────────────────┤
│ 交易僕人指令列                                                     │
│ 「交代僕人：輸入策略想法、篩選條件、盯盤邏輯或執行檢查……」          │
├─────────────────────────────────────────────────────────────────────┤
│ [交易操盤室]          [策略工坊]          [策略執行與績效]           │
├─────────────────────────────────────────────────────────────────────┤
│ Current Workspace                                                  │
└─────────────────────────────────────────────────────────────────────┘
```

## 2.2 全域右側工具

所有主頁籤都可打開：

```text
交易僕人任務 Drawer
Shadow Drawer
Journal / Replay Drawer
專家審視 Drawer
版面調整 Drawer
個人化紀錄 Drawer
```

## 2.3 指令列快捷任務

```text
建立策略視角
產生候選池
分析分點
找落後補漲
比較多檔股票
檢查進場條件
檢查出場條件
重排目前畫面
新增圖表
送影子追蹤
```

---

# 3. 第一主頁籤：交易操盤室

## 3.1 核心概念

交易操盤室不是一個固定 Dashboard，也不是單一 Watchlist。

它由多個 Strategy Lens 組成：

```text
Strategy Lens
  -> Candidate Pool
  -> 逐檔討論
  -> Monitoring Pool
  -> 接近進場 / 接近出場訊號
  -> 交易員裁示
```

每個 Strategy Lens 必須有自己的：

```text
完整版面骨架
資訊階層
候選池呈現方式
主要圖表
監控資料
警示條件
決策流程
快捷操作
```

## 3.2 Strategy Lens Switcher

交易操盤室最上方必須有明顯的 Lens Switcher：

```text
[籌碼大戶部位建立]
[產業落後補漲]
[技術突破]
[事件交易]
[大額資金進出]
[+ 建立新策略視角]
```

每個 Lens tab 顯示：

```text
候選數
監控數
接近進場數
出場提醒數
待裁示數
異常數
```

切換 Lens 時，**整個 Workspace layout 必須改變**。

不可只換：

```text
表格資料
widget 標題
卡片內容
顏色
```

## 3.3 Lens 狀態漏斗

每個 Lens 都有自己的狀態流程：

```text
新候選
待討論
深入研究
納入監控
影子追蹤
接近交易
已剔除
暫放
```

剔除不是刪資料，必須保留剔除原因與後續結果，作為個人化與訓練資料。

---

# 4. 必畫 Dashboard A：籌碼大戶部位建立

## 4.1 此 Dashboard 的判斷重心

此 Lens 是從一批股票中找出疑似有大戶建立部位，但價格尚未充分反映的標的。

主視覺必須是：

```text
候選漏斗
分點熱圖
買盤累積
關聯分點流向
流動性 / 承接能力
出貨風險
```

不能以 K 線作為唯一中心。

## 4.2 完整版面骨架

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Lens Header：籌碼大戶部位建立 / 今日新候選 / 待裁示 / 異常          │
│ Dashboard Personalization：v7 / 僕人最近調整 / [查看] [還原]         │
├───────────────────┬─────────────────────────────────┬───────────────┤
│ 候選漏斗          │ 分點 × 日期 Heatmap              │ 待裁示隊列     │
│ 新候選 38         │                                 │ 加入監控       │
│ 待討論 12         │                                 │ 深入研究       │
│ 監控中 9          │                                 │ 送 Shadow      │
│ Shadow 4          │                                 │ 剔除 / 暫放    │
├───────────────────┼─────────────────────────────────┴───────────────┤
│ 候選排名表        │ 關聯分點買賣 Network                             │
│ AI Score          │ 同券商不同分點 / 同族群反向流                    │
├───────────────────┴─────────────────────────────────────────────────┤
│ 價格 vs 累積買超  │ 流動性 / 承接量 │ 出貨風險 │ 同族群輪動       │
├─────────────────────────────────────────────────────────────────────┤
│ 接近交易：即將滿足監控條件的標的 / 持有部位出場提醒                 │
└─────────────────────────────────────────────────────────────────────┘
```

## 4.3 必須顯示的 Widget

```text
Candidate Funnel
Candidate Ranking Table
Branch × Date Heatmap
Price vs Cumulative Net Buy
Same Broker Cross-Branch Network
Related Branch Sell Risk
Liquidity / Capacity Estimate
Sector Rotation Strip
Distribution Warning
Entry Near-Trigger Queue
Held Position Exit Alert Queue
```

## 4.4 候選表欄位

```text
Rank
Symbol
僕人選出理由
連續買超天數
買盤集中度
價格是否仍早
流動性
關聯分點賣超風險
異常分數
信心
狀態
```

## 4.5 候選討論 Drawer

顯示：

```text
為什麼選它
主要證據
價格位置
分點行為
同券商其他分點
同族群資金流
疑慮
反方論點
確認條件
失效條件
```

操作：

```text
納入監控
送影子追蹤
深入研究
修改監控條件
剔除
暫放
請僕人補查
```

---

# 5. 必畫 Dashboard B：產業落後補漲

## 5.1 此 Dashboard 的判斷重心

此 Lens 從 seed stocks 與交易假說出發，建立產業 / 供應鏈位置，找出相似度高但價格尚未反映的標的。

主視覺必須是：

```text
供應鏈結構
同業相似度
相對報酬差
催化事件
落後候選排名
```

版面不得沿用籌碼 Lens 的三欄熱圖骨架。

## 5.2 完整版面骨架

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Thesis Bar：交易假說 / Seed Stocks / 產業事件 / 研究進度             │
├──────────────────────────────────────────┬──────────────────────────┤
│ 供應鏈 / 產業位置圖                       │ 催化事件 Timeline          │
│ 上游 → 中游 → 下游                        │ 法說 / 接單 / 產品 / 新聞   │
├──────────────────────┬───────────────────┴──────────────────────────┤
│ 相似度 × 漲幅落後 Scatter                │ 同族群相對報酬 Comparison   │
│ size=流動性，color=籌碼支持               │ 1d / 5d / 20d / since-event │
├──────────────────────┴──────────────────────────────────────────────┤
│ 落後補漲候選 Ranking                                                │
│ 相似度 / 落後分數 / 催化 / 未漲原因 / 籌碼 / 流動性 / 風險          │
├─────────────────────────────────────────────────────────────────────┤
│ 待裁示候選 │ 加入監控 │ 深入研究 │ Shadow │ 剔除                   │
└─────────────────────────────────────────────────────────────────────┘
```

## 5.3 必須顯示的 Widget

```text
Industry Supply Chain Map
Peer Universe Map
Similarity × Price Lag Scatter
Relative Return Comparison
Catalyst Timeline
Revenue / Product Exposure Table
Laggard Candidate Ranking
Why Not Moved Yet Panel
Counter-Thesis Panel
Entry Confirmation Queue
```

## 5.4 候選表欄位

```text
Rank
Symbol
產業角色
相似度
漲幅落後分數
催化相關度
籌碼支持
流動性
為什麼相似
為什麼尚未漲
確認訊號
風險
```

---

# 6. 必畫 Dashboard C：技術突破

## 6.1 此 Dashboard 的判斷重心

此 Lens 監控接近突破、已突破、回測與假突破風險。

主視覺必須是大型時間序列圖與交易條件，不可沿用前兩套骨架。

## 6.2 完整版面骨架

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Lens Header：接近突破 / 已突破 / 假突破 / 待裁示                     │
├──────────────────────────────────────────────┬──────────────────────┤
│ 大型 K 線 Workspace                          │ 交易條件面板           │
│ Breakout Level / Volume / AVWAP / Support    │ 進場條件              │
│                                              │ 失效條件              │
│                                              │ 停損 / 目標 / 配比     │
├──────────────────────┬───────────────────────┴──────────────────────┤
│ ATR / Volatility     │ 歷史相似型態 / 成功率 / 假突破率              │
├──────────────────────┴──────────────────────────────────────────────┤
│ 即將觸發標的 │ 已突破待確認 │ 失敗型態 │ 持有部位出場訊號         │
└─────────────────────────────────────────────────────────────────────┘
```

## 6.3 必須顯示的 Widget

```text
Candlestick + Breakout Overlay
Volume Confirmation
Anchored VWAP
Support / Resistance
ATR / Volatility
Historical Pattern Similarity
Setup Success / Failure Rate
Entry / Stop / Target Panel
Near-Trigger Candidate Queue
Open Position Exit Queue
```

---

# 7. 必畫 Dashboard D：事件交易

## 7.1 此 Dashboard 的判斷重心

圍繞特定事件、時間與市場預期差，必須採 timeline-first 版面。

## 7.2 完整版面骨架

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Event Header：事件類型 / 日期 / 倒數 / 預期 / 市場共識               │
├─────────────────────────────────────────────────────────────────────┤
│ Event Timeline：事件前資訊 → 事件發布 → 盤後 / 次日反應              │
├──────────────────────────┬──────────────────────────────────────────┤
│ Historical Event Matrix  │ Expectation Gap / Scenario Tree          │
│ 類似事件反應              │ Bull / Base / Bear                       │
├──────────────────────────┼──────────────────────────────────────────┤
│ Volatility / Option View │ Candidate / Position Impact               │
├──────────────────────────┴──────────────────────────────────────────┤
│ 事件前待裁示 │ 事件後待確認 │ 已持有部位風險                         │
└─────────────────────────────────────────────────────────────────────┘
```

## 7.3 必須顯示的 Widget

```text
Event Countdown
Event Timeline
Consensus vs Actual
Historical Event Reaction Matrix
Scenario Tree
Volatility / Option Surface Summary
Affected Symbols Ranking
Current Position Impact
```

---

# 8. 必畫 Dashboard E：大額資金進出 / 流動性執行

## 8.1 此 Dashboard 的判斷重心

面向大戶的部位建立與退出可行性，必須採 capacity / impact-first 版面。

## 8.2 完整版面骨架

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Capital Intent：目標金額 / 時間窗 / 最大市場參與率 / 風險上限       │
├──────────────────────┬────────────────────────┬─────────────────────┤
│ Liquidity Profile    │ Capacity Estimate      │ Impact Risk          │
│ ADV / spread / depth │ 可承接量 / 分批天數     │ slippage / detection │
├──────────────────────┼────────────────────────┴─────────────────────┤
│ Entry Schedule       │ Alternative Symbols / Substitute Liquidity   │
├──────────────────────┴──────────────────────────────────────────────┤
│ 執行前待裁示 │ 執行中偏差 │ 需要減速 / 暫停 / 改標的                │
└─────────────────────────────────────────────────────────────────────┘
```

## 8.3 必須顯示的 Widget

```text
ADV / Spread / Depth
Capacity Estimate
Participation Rate Simulator
Slippage Curve
Market Impact Estimate
Entry Schedule
Detection Risk
Alternative Names
Execution Deviation Alerts
```

---

# 9. 策略 Lens 切換與 DashboardRecipe

## 9.1 切換規則

每個 Lens 綁定不同 `layoutTemplateId`：

```ts
type StrategyDashboardRecipe = {
  id: string;
  strategyLensId: string;
  userId: string;

  layoutTemplateId:
    | "branch_accumulation_workspace"
    | "industry_laggard_workspace"
    | "technical_breakout_workspace"
    | "event_driven_workspace"
    | "liquidity_execution_workspace"
    | "custom";

  primaryVisualization:
    | "branch_heatmap"
    | "supply_chain_map"
    | "candlestick_workspace"
    | "event_timeline"
    | "liquidity_capacity_board";

  regions: DashboardRegion[];
  widgets: WidgetSpec[];
  version: number;
  generatedBy: "default" | "trading_servant" | "trader" | "learned";
};
```

## 9.2 切換動畫與辨識

Claude Design 必須呈現：

```text
切換 Lens 後，整個 grid、主視覺、側邊欄、工具列、候選呈現方式都變化。
不是只做 tab active state。
```

每個 Dashboard 要有顯著的 `layout identity`：

```text
籌碼：heatmap / network / ranking-heavy
產業：map / scatter / comparison-heavy
技術：candlestick / trigger-heavy
事件：timeline / scenario-heavy
大額資金：capacity / impact-heavy
```

---

# 10. 交易僕人改版面：提案，不直接強改

## 10.1 Dashboard Control Bar

每個 Lens Dashboard 上方固定顯示：

```text
目前版面：個人化 v7
最近調整：出貨風險移至第一排
原因：您最近 5 次剔除，有 3 次與關聯分點賣超有關
效果：近 10 日 Shadow 命中率 +8%
[查看變更] [交代僕人改版面] [手動編輯] [還原]
```

## 10.2 交易僕人版面提案流程

```text
交易員交代：把出貨風險放前面，移除 RSI，再新增同券商反向流圖。

交易僕人產生 Proposal：
- 移動 Related Branch Sell Risk 到第一區
- 移除 RSI Widget
- 新增 Same Broker Cross-Branch Network

畫面顯示 Before / After Preview

交易員選：
[全部套用]
[逐項套用]
[拒絕]
[手動調整後套用]
```

## 10.3 必畫 Before / After

Claude Design 必須畫完整 Before / After workspace，而不是只畫一個小 modal。

---

# 11. 交易員手動編輯版面

## 11.1 Edit Mode

點「手動編輯」後：

```text
所有 widget 顯示 drag handle
可拖曳移動
可 resize
可刪除
可更換圖表類型
可新增既有 widget
可要求僕人設計新圖表
```

## 11.2 Widget Menu

每個 widget 右上角必須有：

```text
拖曳
放大 / 縮小
移到前面
移到後面
換圖表
補充資料
標記有用
標記沒用
請僕人重做
移除
```

## 11.3 移除確認

移除不是刪除歷史：

```text
從目前 Dashboard 隱藏
此 Lens 永遠不再顯示
只暫時隱藏
```

---

# 12. 交易僕人產生新 Widget / 新圖表

## 12.1 允許的生成方式

交易僕人可以產生：

```text
WidgetSpec
ChartSpec
DashboardRecipe
```

不能直接產生任意 React / JavaScript code。

## 12.2 New Widget Proposal 畫面

必須顯示：

```text
Widget 名稱
解決什麼問題
使用哪些資料
圖表類型
欄位映射
可點擊互動
為什麼現有 Widget 不足
資料敏感度
Preview
```

操作：

```text
加入目前 Dashboard
先試用
要求調整
拒絕
送開發新 Plugin
```

---

# 13. 每個交易員的個人化與版本紀錄

## 13.1 個人化層級

```text
Trader global preference
Strategy Lens preference
Workspace phase preference
Widget preference
```

Key：

```text
userId + strategyLensId + workspace + phase
```

## 13.2 Personalization Status

每個 Dashboard 顯示：

```text
僕人已學到：
- 您偏好先看籌碼再看價格
- 您常剔除低流動性候選
- 您不使用 RSI
- 您重視關聯分點出貨風險
```

## 13.3 Dashboard Change Log

必須顯示：

```text
版本
時間
變更內容
變更原因
由誰變更
使用者接受 / 拒絕
效果評估
可否回滾
```

操作：

```text
比較版本
回滾此版本
還原策略預設
固定目前版面
停止自動個人化
```

---

# 14. 第二主頁籤：策略工坊

## 14.1 版面必須是「描述 ↔ 即時生成」雙面工作區

```text
┌───────────────────────────────┬─────────────────────────────────────┐
│ 左：交易員描述與討論          │ 右：交易僕人即時生成策略結構        │
│                               │                                     │
│ 交易假說                      │ Universe / 標的範圍                 │
│ 僕人追問                      │ Alpha / 訊號                        │
│ 條件修改                      │ Portfolio / 部位配置                │
│ 流程排序                      │ Risk / 風控                          │
│ 權重討論                      │ Execution / 進出場                  │
│                               │ Candidate Pool                      │
│                               │ Flow Diagram                        │
│                               │ Backtest / Adjustment               │
└───────────────────────────────┴─────────────────────────────────────┘
```

## 14.2 左側互動

交易員可以描述：

```text
我想找有大戶建立部位、價格尚未大漲、流動性足夠的股票。
```

交易僕人追問：

```text
市場範圍？
最小成交金額？
連續買超幾天？
最大漲幅排除？
進場條件？
失效條件？
單檔配置？
總持股數？
出場規則？
```

## 14.3 右側 LEAN 對應卡片

使用使用者語言呈現，但概念對應：

```text
標的範圍 Universe
訊號條件 Alpha
部位配置 Portfolio Construction
風控規則 Risk Management
進出場 Execution
```

每張卡片：

```text
目前規則
缺少欄位
交易僕人建議
交易員可直接修改
```

## 14.4 流程與配比編輯

必須可視化：

```text
篩選 Universe
 -> 訊號判斷
 -> 候選池排名
 -> 風險過濾
 -> 部位分配
 -> 進場
 -> 監控
 -> 出場
```

交易員可以：

```text
拖曳改順序
設定條件權重
設定 AND / OR
設定必須同時成立
設定候選池上限
設定單檔配置
設定加減碼
設定進出場
```

## 14.5 回測區

必須顯示：

```text
Equity Curve
Drawdown
Sharpe
Win Rate
Turnover
Slippage
Holding Period
Capacity
Regime Breakdown
Top / Worst Trades
```

交易僕人必須提出：

```text
哪些條件過度擬合
哪些條件沒有貢獻
哪些風控應加入
哪些權重可調
哪些市場環境失效
```

## 14.6 加入執行

交易員決定採用時：

```text
[加入策略執行]
```

Agora UI 不顯示 Management、runtime、deployment 等後台語彙。

---

# 15. 第三主頁籤：策略執行與績效

## 15.1 目的

此頁管理多組策略，而不是單一部位頁。

必須回答：

```text
有哪些策略正在執行？
各策略結果如何？
哪些交易按原策略執行？
哪些被交易員干預？
干預後變好還是變差？
哪些規則需要調整？
目前有哪些部位需要出場？
```

## 15.2 Overview 版面

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Strategy Execution Overview：策略數 / 執行中 / 暫停 / 待調整       │
├───────────────────────────────┬─────────────────────────────────────┤
│ 策略績效排名                  │ 累積績效 / Drawdown / Exposure       │
├───────────────────────────────┼─────────────────────────────────────┤
│ 原策略執行 vs 人工干預比較    │ 目前部位 / 進場 / 出場提醒           │
├───────────────────────────────┴─────────────────────────────────────┤
│ 交易僕人調整建議 / 待裁示                                       │
└─────────────────────────────────────────────────────────────────────┘
```

## 15.3 策略表欄位

```text
策略名稱
Strategy Lens
狀態
候選池數
持有部位
PnL
Sharpe
Drawdown
命中率
人工干預次數
按原策略績效
干預後績效
偏離程度
調整建議
```

## 15.4 Strategy Detail

內部 tabs：

```text
Overview
Current Positions
Entry / Exit History
Original Rule vs Actual
Human Interventions
Performance
Shadow Comparison
Adjustment Suggestions
Journal / Replay
```

## 15.5 人工干預追蹤

每次干預必須顯示：

```text
原策略原本要做什麼
交易員實際做什麼
干預理由
結果
若不干預的 Shadow Outcome
干預是否改善
交易僕人學到什麼
```

---

# 16. 必畫的 Claude Design Artboards

Claude Design 必須交付以下完整高保真畫面，不接受只畫元件或局部卡片：

## A. 交易操盤室，多策略完整畫面

1. 籌碼大戶部位建立 Dashboard
2. 產業落後補漲 Dashboard
3. 技術突破 Dashboard
4. 事件交易 Dashboard
5. 大額資金進出 Dashboard
6. 五種 Dashboard 並排比較 Board
7. Lens Switcher 切換前 / 後狀態

## B. 版面個人化

8. 交易僕人 Dashboard Proposal
9. Before / After 完整版面比較
10. 手動 Edit Mode：拖曳、resize、刪除
11. Widget Menu 展開
12. New Widget / Chart Proposal
13. Dashboard Change Log
14. Personalization Status
15. Version Rollback

## C. 候選與交易裁示

16. Candidate Pool + Candidate Review Drawer
17. Monitoring Pool
18. Entry Near-Trigger Queue
19. Held Position Exit Alert Queue
20. 交易裁示確認畫面

## D. 策略工坊

21. 空白策略工坊：左側描述、右側逐步生成
22. 已生成 LEAN 對應卡片
23. 流程順序 / 權重 / AND-OR 規則編輯
24. 候選池與部位配置
25. 回測結果與交易僕人調整建議
26. 加入策略執行的確認畫面

## E. 策略執行與績效

27. 多策略執行 Overview
28. 策略績效排名
29. Strategy Detail
30. 原策略 vs 實際執行 vs 人工干預
31. 干預結果 / Shadow Outcome 比較
32. 出場提醒與部位狀態
33. 調整建議待裁示

## F. 響應式

34. 1440px desktop
35. 1920px wide research screen
36. 1024px narrow laptop

手機不作主要交易工作區，只需查看提醒、待裁示與任務進度。

---

# 17. 視覺規格

## 17.1 整體風格

```text
專業交易桌
高資訊密度
機構級研究工具
冷靜、深色或中性色
清楚區分訊號、警示、裁示與證據
不遊戲化
不散戶化
不聊天機器人化
```

## 17.2 Semantic Colors

```text
正向 / 改善：green
注意 / 待觀察：amber
高風險 / 接近失效：orange
嚴重 / 必須裁示：red
資訊 / 研究中：blue
Shadow / 模擬：purple
已剔除 / 失效：gray
```

不可用顏色單獨表達，必須同時有 icon / label / text。

## 17.3 文字層級

交易桌必須優先顯示：

```text
策略 lens
目前狀態
僕人判讀
待裁示
主要證據
下一步
```

低優先資訊放 tooltip / drawer。

---

# 18. Claude Design 禁止事項

不得：

```text
用同一三欄 Dashboard 只換內容代表多策略
只畫 Strategy Lens tabs，不畫切換後完整版面
把交易操盤室畫成一般 Watchlist
把策略工坊畫成聊天頁
把策略執行頁畫成單純持倉表
把交易僕人設計成可愛 chatbot
使用散戶問答文案
顯示 Pathreon Management 或後台治理語彙
讓交易僕人直接任意改版面而無提案 / preview / rollback
讓交易僕人直接生成任意前端 code
```

---

# 19. Design Acceptance Checklist

Claude Design 交付必須全部符合：

```text
[ ] 三主頁籤清楚：交易操盤室 / 策略工坊 / 策略執行與績效
[ ] 至少五種 Strategy Lens 各有完整、結構顯著不同的 Dashboard
[ ] 五種 Dashboard 的主視覺、grid、候選呈現與決策流程不同
[ ] Lens Switcher 不是只切資料
[ ] 交易僕人先提出版面提案
[ ] 有完整 Before / After Preview
[ ] 交易員可手動拖曳、移動、resize、刪除 Widget
[ ] 交易僕人可提出新 Widget / Chart
[ ] 有 Dashboard Change Log、版本與回滾
[ ] 個人化是 per-trader + per-lens + per-workspace + per-phase
[ ] 候選池、監控池、接近進場、出場提醒都可操作
[ ] 策略工坊是描述與即時生成的雙面畫面
[ ] 策略工坊包含 Universe / Alpha / Portfolio / Risk / Execution
[ ] 可編輯流程、權重、配比、進出場、候選池與部位配置
[ ] 有回測與調整建議
[ ] 策略執行頁可看多策略排名、績效與人工干預
[ ] 可比較原策略 / 人工干預 / Shadow Outcome
[ ] UI 語氣是交易員交代交易僕人，不是問 AI
[ ] 不出現 Management / runtime / artifact / governance 等後台詞
```

若任何一項未達成，設計稿視為未符合需求。

---

# 20. 最終設計定義

Agora 是一張會隨交易員與策略改變的 AI 交易桌。

```text
不同交易員
  -> 不同個人化記憶

不同 Strategy Lens
  -> 不同完整 Dashboard architecture

不同工作階段
  -> 不同候選、監控、部位與複盤畫面

交易僕人
  -> 提出候選、研究、版面、Widget、圖表與調整建議

交易員
  -> 裁示、移動、刪除、修改、採用、拒絕、回滾
```

核心不是「同一畫面換資料」，而是：

> **交易策略不同，整張交易桌的結構、資訊階層、視覺中心、決策流程與操作方式都不同。**
