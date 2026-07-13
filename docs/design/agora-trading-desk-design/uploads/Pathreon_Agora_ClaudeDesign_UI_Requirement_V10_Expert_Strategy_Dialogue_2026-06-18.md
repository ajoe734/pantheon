# Pathreon Agora — Claude Design UI Requirement V10
## Expert Strategy Dialogue / 高階交易策略共同建構工作區

> 版本：V10  
> 日期：2026-06-18  
> 適用對象：Claude Design、UI/UX 設計、VS Code 前端開發、Pantheon BFF / Research Plane 團隊  
> 使用者可見產品名稱：Pathreon Agora  
> 後端能力來源：Pantheon `dev` Research / Registry / Governance / Execution planes  
> 取代範圍：本文件取代 V9 中過度簡化的策略對話範例；保留「對話優先、複雜工具隱藏」原則，但將對話能力提升到專業交易員可接受的研究深度。

---

# 0. 本版要修正的錯誤

前一版把交易員描述成只會說：

```text
我想找有分點買進、股價還沒漲的股票。
```

這種範例過度簡化，不符合高階交易員、股票大戶、資深研究員的實際工作方式。

真正使用者可能一次描述完整且複雜的研究假說，例如：

```text
從每一檔股票的關係人持股開始，先找出這些關係人可能會出現的交易分點。
再計算這些分點過去進出場是否賺錢、賺多少錢、穩定性如何，建立贏家分點分數。

贏家分點可能會變換，因為股票可能由一個分點買進，再匯到另一個分點賣出。
所以不能只看單一分點，還要掃描沒有買進但突然大量賣出的其他分點，找出可能的關聯分點與資金遷移。

還要把分點交易和關係人持股變化對照，估計哪些關係人可能對應哪些分點。

如果某分點異常大量買進或賣出，要對照未來三到六個月是否出現重大訊息、併購、財報顯著變化或其他事件，判斷這個分點是否具有資訊領先特徵。

最後要建立贏家分點信賴值、上漲機率、期望值，提出部位建立、加碼、減碼與槓桿方式；再搜尋相關學術文獻與相似 Alpha，建立多個可比較策略並完整回測。
```

Agora 的交易僕人必須能理解這種密集、專業、非結構化的描述，並將它轉成可研究、可證偽、可回測、可比較、可執行監控的完整策略族群。

## 0.1 核心產品原則

```text
交易員一次可以講很多，而且可能已經講得很完整。
交易僕人不能把他當初學者，也不能重新問一堆已經回答過的低階問題。
```

交易僕人應先：

1. 完整吸收交易員原始描述。
2. 重新建構其中的因果鏈、資料關係與策略分支。
3. 清楚區分「交易員已明確定義」、「僕人推論」、「仍需定義」、「目前資料無法直接證明」。
4. 只追問真正會改變研究結論、回測設計或風險結果的高資訊量問題。
5. 在可合理推定時提出 provisional design，而不是一直要求交易員補表單。
6. 將複雜 Pantheon 工具鏈隱藏在交易語言後方。

---

# 1. 策略工坊的產品定位

策略工坊不是聊天頁，也不是 StrategySpec 表單，更不是後端研究工具控制台。

它是：

> **交易員與交易僕人共同進行策略建模、證據蒐集、策略變體設計、回測、評斷、資金配置與執行準備的專業討論工作區。**

畫面必須讓交易員感受到：

```text
我正在和一個理解市場、籌碼、資料限制、統計檢驗、風險與執行的資深研究僕人共同工作。
```

而不是：

```text
我正在回答一個 AI 問卷。
```

---

# 2. 主畫面：簡單，但足以支撐深度討論

## 2.1 Desktop layout

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ 策略名稱 / V版本 / 研究狀態 / 完整度 / 回測狀態 / 加入交易操盤室          │
├───────────────────────────────────────────────┬────────────────────────────┤
│ 對話與研究成果工作流 72%                       │ 策略結構與待裁示 28%       │
│                                               │                            │
│ 交易員原始假說                                │ 策略完整度                 │
│ 交易僕人結構化理解卡                          │ 已確認                     │
│ 假說 / 關係圖                                 │ 僕人推定                   │
│ 研究計畫卡                                    │ 尚缺                       │
│ 結果 / 回測 / 版本比較卡                      │ 薄弱 / 衝突                │
│ 交易員裁示與修正                              │ 下一個高價值決策           │
│                                               │ 策略版本 / 假設紀錄        │
├───────────────────────────────────────────────┴────────────────────────────┤
│ 交代策略、修改規則、要求研究、質疑結果、指定版本或要求重跑……              │
└────────────────────────────────────────────────────────────────────────────┘
```

## 2.2 不要增加複雜常駐介面

主畫面禁止常駐：

```text
- Qlib / vectorbt / statsmodels / QuantLib 工具列表
- 大型 18 節點工程流程
- 多個後端 job queue
- raw JSON
- 完整 StrategySpec 表單
- 全頁統計報表
```

這些只能放在 expandable details：

```text
研究來源與方法
詳細回測
進階假設
Artifact / lineage
```

---

# 3. 專業長描述進入後，交易僕人的第一個回應

交易僕人的第一個回應不能立刻問問題，也不能回一篇鬆散摘要。

必須插入一張 **Strategy Reconstruction Card**。

## 3.1 Strategy Reconstruction Card

### A. 僕人理解的策略核心

```text
目標：識別具有持續獲利能力或資訊領先特徵的券商分點／分點群組，
再以其最新異常交易作為股票候選與進出場信號。
```

### B. 推導出的研究子問題

```text
1. 關係人 ↔ 分點的可能對應關係如何估計？
2. 一個分點是否具備持續、可重複的超額獲利能力？
3. 分點買入後由另一分點賣出的資金遷移如何辨識？
4. 同券商、同群組或歷史共現分點是否應視為同一實體 cluster？
5. 異常分點交易是否領先重大訊息或財報變化？
6. 哪些訊號具有可交易性，而不是事後資料探勘？
7. 信號出現後的不同持有期報酬、風險與容量如何？
```

### C. 交易僕人已辨識出的策略元件

```text
- 關係人持股與變化資料
- 券商分點逐日買賣資料
- 分點歷史損益與勝率
- 分點／分點群組 identity graph
- 股票間產業與事件關係
- 重大訊息、併購、財報與公告事件
- Winner Branch Score
- Branch Migration / Distribution Risk
- Information Lead Proxy
- 進場、加碼、減碼、出場與風險規則
- 多策略變體與回測比較
```

### D. 僕人必須標出的不可直接斷言事項

```text
公開資料只能建立「資訊領先關聯」或「可能對應」的統計證據，
不能直接斷言某分點或關係人涉及內線、操縱或違法行為。
```

UI 顯示為醒目但不搶畫面的 **研究限制標籤**。

---

# 4. 策略缺漏掃描：不是問卷，而是高資訊量決策

交易僕人將策略拆成 12 個可追蹤區塊：

```text
1. 研究對象與市場範圍
2. 關係人與分點映射
3. 贏家分點評分
4. 分點遷移與反向流
5. 事件領先關聯
6. 信號形成
7. 進場與持有週期
8. 加碼、減碼與出場
9. 部位與槓桿
10. 成本、流動性與容量
11. 驗證、回測與反證
12. 監控與策略更新
```

右側只顯示摘要：

```text
✓ 已確認：核心假說、關係人/分點資料方向、事件窗口
△ 僕人推定：分點 cluster 以共現/同券商/買賣遷移建立
! 尚缺：實際持有週期與可接受回撤
△ 薄弱：關係人對應分點只有機率，不能視為確定身份
✕ 衝突：若使用公告後可見資料，不能再宣稱是事前訊號
```

## 4.1 追問規則

交易僕人只追問對策略結果影響最大的問題。

在此案例中，第一個高價值問題可能是：

```text
你希望「贏家分點」的主要評分目標偏向哪一種？

A. 分點買入後 20～60 日的持續超額報酬
B. 分點在多次交易中的獲利一致性
C. 分點對重大訊息的領先能力
D. 上述三項形成綜合分數，由僕人先提出權重
```

不是問：

```text
你想看幾天？
```

如果交易員沒有指定，僕人應直接提出：

```text
我先建立 5d、20d、60d、120d 四個 horizon，
並以 20d / 60d 作為主要交易與位置建立評分。
```

---

# 5. Winner Branch Strategy 的正式策略拆解

## 5.1 關係人—分點概率映射

交易僕人建立的是概率映射，不是確定身份。

### 輸入

```text
- 關係人持股揭露與持股變化
- 董監、經理人、大股東與相關法人
- 分點逐日買賣
- 大額交易／集中成交
- 公司事件與公告
- 歷史共現與時序關係
```

### 可能方法

```text
- 持股變化與分點淨買賣 lead-lag correlation
- 分點在關係人持股變動前後的異常度
- 同一股票跨分點資金遷移
- 同券商不同分點共現
- 多股票跨期間重複共現
- Bayesian / probabilistic entity matching
```

### 輸出

```ts
RelationshipBranchMapping {
  relatedPartyId
  branchId
  matchProbability
  supportingEvidence
  conflictingEvidence
  effectiveWindow
  confidenceBand
}
```

UI 不顯示「就是此人」，而顯示：

```text
可能對應程度 74%
支持證據 5 項
反向證據 2 項
資料可信度：中
```

---

## 5.2 Winner Branch Score

交易僕人應至少提出以下 score components：

```text
1. Historical Profitability
   分點買入後不同 horizon 的平均超額報酬與成本後報酬

2. Consistency
   不同股票、不同期間、不同 regime 的穩定性

3. Win Rate / Payoff Ratio
   勝率、平均獲利、平均虧損、profit factor

4. Timing Quality
   進場位置、持有期、出場後報酬衰減

5. Event Lead Score
   異常交易至重大事件的 lead time 與事件後反應

6. Relationship Alignment
   分點交易與關係人持股變化的對應程度

7. Branch Continuity
   單一分點、同券商多分點、cluster 行為的一致程度

8. Migration / Distribution Risk
   其他分點的大量賣出、疑似轉倉、反向流或高檔出貨

9. Liquidity / Capacity
   訊號是否只出現在不可交易的小量股票

10. False Discovery Penalty
    多重檢定、選股偏誤、事後挑選與資料缺漏懲罰
```

### 分數呈現

```text
Winner Branch Score: 82 / 100
可信度：68%（中高）
主要優勢：20d/60d 報酬一致、事件領先度高
主要風險：分點遷移模型不穩、低流動性曝險偏高
```

## 5.3 分數版本

交易僕人應主動提出至少三個版本：

```text
Score A — 盈利導向
重視歷史報酬、勝率、payoff ratio

Score B — 資訊領先導向
重視事件 lead、關係人持股對應、公告前異常

Score C — 可交易綜合分數
加入流動性、容量、成本、遷移風險與穩定性
```

交易員可以選一個，也可以要求 ensemble。

---

# 6. 分點遷移與出貨辨識

此策略不能只看「某分點買進」。

交易僕人必須建立 **Branch Identity / Flow Graph**：

```text
node：券商分點
edge：同股票資金遷移、同券商關係、歷史共現、反向買賣、時間接續
```

## 6.1 必須檢查

```text
- A 分點買入後，B 分點是否大量賣出
- B 分點先前是否沒有買入記錄
- A/B 是否同券商或長期共現
- 是否為同股票的轉倉或換手可能
- 是否在同族群其他股票產生反向流
- 是否在高檔出現賣出 cluster
- 是否存在多分點拆單
```

## 6.2 輸出

```text
Accumulation Confidence
Migration Probability
Distribution Risk
Cluster Confidence
Net Unified Flow
```

UI 對話結果範例：

```text
如果只看永豐金-台中分點，這次訊號為強買進。
加入同券商與歷史共現分點後，統一淨流量只剩原始值的 43%。
主要原因是另一分點在兩日後大量賣出。

因此我建議：
- 單點分數：86
- Cluster-adjusted 分數：61
- 暫不列為高信賴贏家分點
```

---

# 7. 事件與資訊領先研究

交易僕人要檢查分點異常交易後的 3～6 個月事件，但必須避免事後偏誤。

## 7.1 事件類型

```text
- 重大訊息
- 併購 / 收購 / 處分
- 財報顯著變化
- 法說 / 接單 / 展望調整
- 增資 / 減資 / 私募
- 內部人持股申報
- 處分資產或重大契約
- 產業政策或監管事件
```

## 7.2 檢驗設計

```text
- Event study
- Lead-time distribution
- Event type conditioning
- Sector / market matched control
- Random branch placebo test
- Multiple testing correction
- Rolling OOS
- Time-split validation
```

## 7.3 UI 語言

不要顯示：

```text
這是內線分點。
```

顯示：

```text
此分點在過去 18 次異常買進中，有 7 次於 90 日內出現重大基本面事件；
相對於同市場隨機分點的基準率 14%，差異具有統計意義，但樣本仍有限。

資訊領先代理分數：77 / 100
證據強度：中
```

---

# 8. 機率預測與期望值

交易僕人要把贏家分點事件轉成可討論的交易機率，不只顯示一個分數。

## 8.1 最低輸出

對每個候選股票：

```text
P(5d 正報酬)
P(20d 正報酬)
P(60d 正報酬)
P(20d 超越市場)
預期報酬
預期下行
成本後期望值
信賴區間
模型校準狀態
```

## 8.2 Expected Value

```text
EV = P(win) × Expected Gain
   - P(loss) × Expected Loss
   - Transaction Cost
   - Slippage
   - Liquidity Penalty
```

UI 卡片：

```text
候選：ABC
20d 上漲機率：64%
20d 預期報酬：+7.8%
預期下行：-4.6%
成本後 EV：+2.9%
模型信賴：中高
主要變數：贏家分點分數、Cluster-adjusted flow、事件領先度
```

---

# 9. 部位、加碼、減碼與槓桿討論

交易僕人應先提出 3 套可討論方案，而不是只問交易員要多少部位。

## 9.1 Conservative

```text
- 初始單檔 1%
- 訊號二次確認後增至 2%
- 最多 5 檔
- 總策略曝險 8%
- 不使用槓桿
```

## 9.2 Balanced

```text
- 初始單檔 1.5%
- 分點流持續 + 價格確認後加至 3%
- 最多 8 檔
- 總策略曝險 15%
- 最高槓桿 1.2×
```

## 9.3 Aggressive

```text
- 初始單檔 2%
- 分數 / 機率 / 流動性三項通過後加至 4%
- 最多 10 檔
- 總策略曝險 25%
- 最高槓桿 1.5×
```

## 9.4 僕人要提醒的相關性與容量

```text
這 12 檔候選中有 8 檔屬於同產業，實際分散度不足。
若使用 Balanced 方案，等權配置仍會產生 63% 的共同因子曝險。
建議改為 cluster-capped allocation，或最多保留每個 cluster 2 檔。
```

這與 Pantheon 現行多人格 / AllocationPolicy 路徑一致：目前 `dev` 正在補 AllocationPolicyArtifact 經 DeploymentPlan、RuntimeBinding、paper LEAN、telemetry，以及 pre-LEAN 的相關性 / 同質性 gate。前端不必暴露內部 task，但策略對話必須反映這些投組風險。

---

# 10. 文獻、外部 Alpha 與相似策略研究

交易僕人應在策略初版形成後主動提出：

```text
我會再搜尋與此假說相關的公開研究與 Alpha：
- informed trading / order-flow information
- insider ownership and trading behavior
- broker identity persistence
- lead-lag event studies
- network clustering of trading entities
- branch flow / institutional accumulation
- anomaly detection and informed probability
```

## 10.1 Pantheon 後方流程

```text
Source ingestion / allowed research sources
→ Evidence bundle
→ StrategySpecSeed / similar alpha discovery
→ related strategy candidates
→ research-only experiment artifacts
```

目前 Pantheon `dev` 已具備 data source / strategy seed split、source catalog、seed materialization、persona strategy discovery，以及 StrategySpec production distillation；策略工坊應利用這些能力，前端只呈現研究成果與來源，不顯示工程細節。

## 10.2 相似策略輸出

交易僕人至少提出：

```text
Strategy V1 — Direct Winner Branch Signal
Strategy V2 — Relationship-Confirmed Branch Signal
Strategy V3 — Event-Lead Winner Branch Signal
Strategy V4 — Cluster-Adjusted Accumulation Strategy
Strategy V5 — Ensemble Winner Branch Strategy
```

每個版本顯示：

```text
核心邏輯
相較原始想法增加了什麼
預期優點
預期缺點
所需資料
可回測程度
```

交易員可選：

```text
[全部進行初步回測]
[只測 V1 / V4 / V5]
[合併 V2 與 V4]
[先討論差異]
```

---

# 11. 回測與評斷

## 11.1 必須回測的內容

```text
- 多個持有期：5d / 20d / 60d / 120d
- 成本前 / 成本後
- 分點原始信號 / cluster-adjusted 信號
- 有無關係人對應確認
- 有無事件領先確認
- 不同市場 regime
- 不同流動性分組
- 不同產業
- Walk-forward / rolling OOS
- Placebo / random branch baseline
- Survivorship / look-ahead / announcement-time controls
- Capacity / turnover / slippage
```

## 11.2 Pantheon 工具分工

```text
vectorbt：快速 prototype、規則與組合比較
Qlib：ranking / feature / rolling OOS / model candidate
statsmodels：lead-lag、事件、regime、穩定性與 statistical diagnostics
QuantLib：若延伸至 options / hedge / derivatives risk
Ray Tune：參數與權重搜尋
FinRL / RLlib：僅限研究型 sequential allocation / execution policy
MLflow / Registry：run、artifact、版本與 lineage
LEAN：最終 paper 執行與 fills / positions / telemetry
```

但 UI 只顯示：

```text
快速規則測試
滾動樣本外驗證
統計穩定性檢查
投組與成本測試
Paper 執行準備
```

## 11.3 回測結果對話卡

```text
五個策略版本回測完成

                 V1      V2      V3      V4      V5
成本後 Sharpe    0.92    1.13    1.05    1.29    1.37
最大回撤        -18.4%  -14.2%  -16.0%  -11.8%  -10.9%
20d 命中率       56%     59%     58%     62%     64%
年均交易數       182     97      76      104     89
容量             低      中      中      高      中高
OOS 穩定度       弱      中      中      高      高

僕人評斷：
- V1 最接近你的原始想法，但容易被分點遷移誤導。
- V4 加入 cluster-adjusted flow 後，回撤與低流動性依賴明顯下降。
- V5 整體最佳，但可解釋性低於 V4。

我的建議：
1. V4 作主要候選策略
2. V5 放 Shadow 對照
3. V1 保留作監控基準，不進正式執行
```

操作：

```text
[採用 V4]
[V4 + V5 都加入交易操盤室]
[調整 V4 部位規則]
[要求更保守版本]
[查看詳細回測]
[質疑評斷]
```

---

# 12. 策略完整度與加入交易操盤室條件

策略可以先做初步回測，但要加入交易操盤室，至少必須完成：

```text
✓ 研究假說與可證偽條件
✓ Winner Branch Score 定義
✓ 分點 cluster / migration 規則
✓ 信號與 horizon
✓ 進場 / 加碼 / 減碼 / 出場
✓ 部位與相關性限制
✓ 成本 / 流動性 / capacity
✓ 至少一個 OOS 回測版本
✓ 主要風險與資料限制
✓ 交易員選定策略版本
```

完成後，交易僕人提示：

```text
V4 已達到加入交易操盤室的條件。
V5 可同時加入 Shadow 作模型對照。

加入後我會為 V4 產生：
- 贏家分點候選排名
- Cluster-adjusted flow
- 新進場候選
- 已持有標的加碼 / 減碼 / 出場提示
- 事件領先信賴值
- 機率與 EV
- 部位 / 風險概況

[將 V4 加入交易操盤室]
[將 V4 + V5 一起加入]
[先調整監控畫面]
```

---

# 13. Claude Design 必須畫的 Artboards

必畫 14 張：

1. **策略工坊空白狀態** — 專業長描述輸入，非低階 prompt 範例。
2. **交易員送出完整贏家分點假說後的第一個回應**。
3. **Strategy Reconstruction Card 展開狀態**。
4. **右側策略完整度與高價值缺漏狀態**。
5. **交易僕人提出 Winner Branch Score A/B/C 的比較卡**。
6. **Relationship ↔ Branch 概率映射研究結果卡**。
7. **Branch Identity / Migration Graph 結果卡**。
8. **事件領先統計結果卡**。
9. **機率與 EV 候選表**。
10. **Conservative / Balanced / Aggressive 部位方案討論卡**。
11. **文獻與相似 Alpha 研究結果卡**。
12. **五個策略版本回測比較卡**。
13. **交易員自然語言修改 V4，重新回測後的版本差異卡**。
14. **選擇 V4 / V5 並加入交易操盤室的完成狀態**。

## 13.1 畫面要求

- 對話仍是主畫面。
- 結構化研究結果以 message cards 插入對話。
- 不做成工程 dashboard。
- 不要求交易員自行填大表單。
- 長描述要有足夠寬度與閱讀密度。
- 結果卡要容納專業指標，但預設只顯示 decision-useful summary。
- 所有完整細節可展開，但不常駐。

---

# 14. 文案語氣

交易僕人語氣必須：

```text
尊重、精準、克制、像資深研究助理。
```

使用：

```text
我已把你的假說拆成五個可驗證分支。
目前最大的識別風險是分點遷移，不是單點買盤強度。
我先建立三種贏家分點評分方式，請你裁示主要目標。
這個結論只能視為資訊領先代理，不能視為身份或違法行為認定。
我建議 V4 作主要策略，V5 進 Shadow 對照。
```

禁止：

```text
你是不是想……？
請問你想看幾天？
AI 覺得這檔會漲。
這是內線分點。
今天要買什麼？
```

---

# 15. Design acceptance checklist

Claude Design 交付必須滿足：

```text
[ ] 可容納交易員一次輸入高密度、多段落的專業策略描述
[ ] 交易僕人先重構策略，不先問低階問題
[ ] 能顯示因果鏈、研究子問題、已知/推定/缺漏/衝突
[ ] 只追問高資訊量問題
[ ] Winner Branch Score 有多版本可比較
[ ] 關係人↔分點只顯示概率映射，不做確定身份指控
[ ] 分點遷移、關聯分點反向流、出貨風險被納入
[ ] 事件領先研究包含統計基準與限制
[ ] 候選股有機率、EV、信賴區間與成本後結果
[ ] 部位 / 加碼 / 槓桿至少有三套方案可討論
[ ] 可搜尋文獻與相似 Alpha 並產生策略變體
[ ] 可選擇一個或多個版本進行回測
[ ] 回測結果能在對話中比較並繼續修改
[ ] 至少一個主要版本 + 一個 Shadow 版本可加入交易操盤室
[ ] 介面仍簡潔，後端工具複雜度不成為主畫面
```

---

# 16. 最終產品定義

策略工坊不是把模糊想法補成幾個簡單條件。

它必須能把資深交易員的完整市場觀察與研究方法：

```text
關係人持股
→ 分點身份概率
→ 贏家分點歷史績效
→ 分點遷移與出貨
→ 事件資訊領先
→ 信號機率與 EV
→ 部位 / 加碼 / 槓桿
→ 文獻與相似 Alpha
→ 多策略版本
→ 完整回測與比較
→ 交易員裁示
→ 交易操盤室監控與 Shadow
```

轉成可以共同研究、持續討論、版本化與驗證的策略。

交易員不必面對 Pantheon 的工程複雜度；但交易僕人的研究深度必須真正使用 Pantheon 的資料、研究、Registry、治理與 LEAN 能力。
