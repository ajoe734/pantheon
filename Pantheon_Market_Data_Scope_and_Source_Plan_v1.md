# Pantheon Market & Data Scope Plan v1

## 文件定位

本文件是提供給開發單位的 **市場範圍 / 商品範圍 / 資料來源規劃文件**。

目的不是選定唯一商業供應商，而是先把以下事情定清楚：

1. Pantheon v1 主要交易市場是哪些
2. 各市場哪些商品是 v1 必須支持
3. 各市場 / 商品需要哪些資料類別
4. Data Plane / Research Plane / Execution Plane 應該如何接這些資料
5. 哪些資料是必接、哪些可延後

> 如果這件事不先定，開發團隊無法設計 symbol master、contract master、calendar、corp action、衍生品鏈、broker adapter 與 replay pipeline。

---

## 1. v1 市場範圍（Market Scope）

Pantheon v1 正式交易市場範圍，建議定為：

1. **美股市場**
2. **台股市場**
3. **加密貨幣市場**

並且每個市場都同時考慮：

- 現貨 / 現股 / ETF
- 衍生性金融商品

---

## 2. v1 商品範圍（Instrument Scope）

## 2.1 美股市場

### v1 必接
- 美國上市普通股
- ADR
- ETF
- 美股個股選擇權
- 主要指數期貨（至少供研究 / 風控 / hedge 使用）
- 主要指數選擇權（若策略需要）

### 建議支援的決策用途
- 現股 / ETF alpha 與配置
- 個股與 ETF 的事件驅動策略
- 指數期貨作 beta overlay / hedge
- 選擇權資料作 vol / skew / hedging / sentiment proxy

### 不建議 v1 一開始就追求
- 全市場美股 options market-making 級資料
- 超高頻 level-3 / order-by-order 美股選擇權簿

---

## 2.2 台股市場

### v1 必接
- 台灣上市現股
- 上櫃現股
- ETF
- 台指類期貨
- 台指類選擇權
- 個股期貨 / 個股選擇權（若策略族需要）

### 建議支援的決策用途
- 台股現股 / ETF cross-sectional alpha
- 法人籌碼 / 事件型策略
- 台指期作 hedge / beta overlay / risk-off
- 台指選擇權作波動、事件風險與 gamma proxy

### 特別注意
台股資料與交易規則有明顯本地特性，Data Plane 必須額外處理：

- 上市 / 上櫃市場分層
- 現股與衍生品交易時段差異
- 除權息 / 減資 / 配股配息事件
- 盤後資料、零股 / 整股、價格限制等 metadata
- 本地 symbol / code / market segment mapping

---

## 2.3 加密貨幣市場

### v1 必接
- 現貨交易對
- 永續合約（perpetual futures）
- 交割合約 / dated futures
- 選擇權（若策略族需要）

### 建議支援的決策用途
- 現貨動能 / cross-sectional relative value
- perp basis / funding carry / open interest change
- dated futures basis curve / term structure
- options implied vol / skew / event risk proxy

### 特別注意
Crypto 與股票市場最大的不同，在於：

- 24/7 交易
- 沒有統一中央交易所
- 現貨 / 永續 / 期貨 / 選擇權可能來自不同 venue
- funding rate、liquidation、OI、basis 都是核心資料
- venue fragmentation 明顯，symbol mapping 與 venue policy 必須先定

---

# 3. 各市場必備資料類別

下表是 v1 開發時必須定義的資料類別，不是 optional。

| 類別 | 美股 | 台股 | 加密貨幣 |
|---|---|---|---|
| 參考主檔（security master） | 必須 | 必須 | 必須 |
| 行情 OHLCV | 必須 | 必須 | 必須 |
| Tick / intraday bars | 建議 | 建議 | 建議 |
| 交易日曆 / session | 必須 | 必須 | 必須 |
| corporate actions | 必須 | 必須 | 不適用 / 極少 |
| fundamentals | 建議 | 建議 | 不適用 |
| 事件資料 | 建議 | 建議 | 建議 |
| options chain | 建議 | 建議 | 視策略需要 |
| futures chain / contract master | 建議 | 建議 | 必須 |
| greeks / IV / surface | 視策略需要 | 視策略需要 | 視策略需要 |
| open interest | 建議 | 建議 | 必須 |
| funding rate | 不適用 | 不適用 | 必須 |
| borrow / shortability | 建議 | 視策略需要 | 不適用 |
| venue-level microstructure | 可延後 | 可延後 | 建議 |
| on-chain / crypto alt data | 不適用 | 不適用 | 建議 |

---

# 4. 每個市場應接的資料面詳細要求

## 4.1 美股：現股 / ETF / ADR

### 必接資料
1. Security master
   - ticker
   - exchange
   - currency
   - asset type
   - industry / sector
   - listing status
2. 日頻 OHLCV
3. intraday bars（至少 minute-level for execution/backtest consistency）
4. corporate actions
   - splits
   - dividends
   - symbol changes
   - delist/merge info
5. calendar / session metadata
   - holiday
   - early close
   - trading session boundaries
6. borrow / shortability（若做 long/short）
7. fundamentals（至少 basic fundamentals / financial statements / valuation fields）

### 建議資料
- earnings calendar
- macro / policy event tag
- news / filings / transcript ingestion
- insider / institutional ownership changes

### 給開發團隊的要求
Data Plane 必須能同時生成：
- raw price history
- adjusted price history
- event-aligned research dataset
- universe filterable metadata set

---

## 4.2 美股：選擇權 / 指數期貨與期權

### 必接資料（若 v1 支持衍生品研究或交易）
1. contract master
   - underlying
   - expiry
   - strike
   - call/put
   - multiplier
   - tick size
   - settlement type
2. chain snapshots
3. OHLCV / bid-ask / mid
4. open interest
5. implied vol / greeks（若策略需要）
6. futures continuous / individual contract series
7. roll metadata
8. contract calendar / expiry schedule

### 特別要求
- futures 不能只存 continuous series，必須保留 individual contract history
- options 不能只存 chain point-in-time，需能回到當時可見的 chain state
- 若不做 full options market-making，至少要支援 EOD / intraday snapshot replay

---

## 4.3 台股：現股 / ETF

### 必接資料
1. Security master
   - code
   - market (TWSE / TPEx)
   - lot metadata
   - sector / industry
2. 日頻 OHLCV
3. intraday bars
4. corporate actions
   - 除權
   - 除息
   - 減資
   - 配股 / 配息
5. calendar / session metadata
6. investor flow / 籌碼（若策略族需要）
7. fundamentals / financial statements
8. ETF composition / classification（若 ETF 策略需要）

### 建議資料
- 法說會 / 公告 / 重大訊息
- 本地新聞與政策事件分類
- 融資融券 / 借券（若做 long/short）

### 給開發團隊的要求
台股不能簡化成「只是另一個 equity market」。
必須顯式處理：
- 本地 market segmentation
- 除權息與報酬計算
- local symbol master
- 本地交易時段與盤後資料

---

## 4.4 台股：期貨 / 選擇權

### 必接資料
1. futures / options contract master
2. chain / contract series
3. OHLCV / bid-ask / mid
4. open interest
5. expiry / roll metadata
6. index/underlying mapping
7. implied vol / greeks（若策略需要）

### 給開發團隊的要求
- 期貨與選擇權資料要和現貨 underlying 明確對齊
- hedging / overlay 類策略必須能同時讀取現貨與衍生品資料
- replay 時不能只有 continuous symbol，必須知道當時實際交易的是哪個 contract

---

## 4.5 Crypto：現貨

### 必接資料
1. venue-aware symbol master
   - venue
   - base / quote
   - tick / lot size
   - price precision
   - quantity precision
2. OHLCV
3. tick / trade prints（建議）
4. bid-ask / order book snapshot（建議）
5. venue metadata
6. delisting / listing / maintenance events

### 特別要求
crypto 不是單一市場，Data Plane 必須明確選擇：
- 是做 single-venue 策略
- 還是做 cross-venue 策略

不先定這件事，symbol master 會混亂。

---

## 4.6 Crypto：perp / futures / options

### 必接資料
1. contract master
   - perpetual / dated future / option
   - base / quote
   - contract size
   - expiry
   - settlement asset
2. OHLCV / bid-ask
3. funding rate（perp）
4. open interest
5. liquidation / long-short ratio（若策略需要）
6. basis / mark / index price
7. options IV / greeks / surface（若策略需要）

### 給開發團隊的要求
若 v1 要支持 crypto 衍生品，Funding / OI / Basis 不能是 optional，必須進 canonical schema。

---

# 5. 各市場資料來源規劃方式（不是只選 vendor，而是先定 source class）

開發團隊不應先用 vendor 名稱思考，而要先用 **source class** 思考。

## 5.1 必須定義的 source class

### A. Official / venue reference source
用途：
- security master
- contract specs
- calendars
- corporate action / listing / expiry metadata

### B. Broker-aligned execution data source
用途：
- execution-synchronous bars
- live positions / fills / order states
- broker symbol mapping

### C. Research-grade market data source
用途：
- backtest / walk-forward / feature generation
- higher-quality historical coverage
- fundamentals / event enrichment

### D. Specialized derivative analytics source
用途：
- options chain
- IV / greeks / OI
- futures term structure

### E. Specialized crypto analytics source
用途：
- funding
- OI
- liquidations
- on-chain / derivatives analytics

### F. Internal canonical store
用途：
- normalized / feature-ready datasets
- replay truth
- lineage / dataset version

### 正式原則
每個市場至少都要回答：

- 參考主檔由誰提供
- 研究行情由誰提供
- execution 對帳由誰提供
- 衍生品 analytics 由誰提供
- 最後 canonical truth 存哪裡

---

# 6. Data Plane 需要的正式物件

以下物件請開發團隊正式定義。

## 6.1 SecurityMaster
```text
security_id
market
venue
symbol_native
symbol_canonical
asset_type
currency
underlying_id
listing_status
metadata_json
```

## 6.2 ContractMaster
```text
contract_id
underlying_id
market
venue
contract_type
expiry
strike
option_right
multiplier
tick_size
settlement_type
margin_type
metadata_json
```

## 6.3 MarketCalendarSession
```text
market
trade_date
session_open
session_close
early_close_flag
holiday_flag
timezone
```

## 6.4 RawDataset
```text
dataset_id
source_class
market
instrument_scope
coverage_start
coverage_end
ingest_time
storage_ref
checksum
```

## 6.5 NormalizedDataset
```text
dataset_id
parent_raw_dataset_id
normalization_version
symbol_mapping_version
corp_action_version
calendar_version
available_time_policy
storage_ref
checksum
```

## 6.6 FeatureDataset
```text
dataset_id
parent_normalized_dataset_id
feature_spec_version
label_spec_version
point_in_time_rule
storage_ref
checksum
```

## 6.7 DatasetVersion
```text
dataset_version_id
market_scope
instrument_scope
raw_dataset_refs[]
normalized_dataset_refs[]
feature_dataset_refs[]
created_at
frozen_at
```

---

# 7. 市場與時區 / 交易時段規劃

開發團隊必須先處理時區與交易時段，否則多市場會亂掉。

## 正式要求

### 美股
- 市場時區與交易日曆必須獨立管理
- regular / early close 要能辨識
- pre/post-market 若不支援，必須明寫不支援

### 台股
- TWSE / TPEx / TAIFEX session 需分開管理
- 現貨與衍生品 session 不可假設相同
- local holiday / event calendar 需獨立管理

### 加密貨幣
- 24/7，但要定義研究切片基準（UTC day? venue local day?）
- daily bar 切片規則必須固定
- funding interval、expiry、settlement windows 需明確定義

### 共同原則
所有資料都要有：
- event_time
- available_time
- ingest_time
- market timezone
- canonical UTC timestamp

---

# 8. Symbol Mapping 與 Contract Mapping 要求

這是多市場系統最容易被低估、但最容易出事故的地方。

## 8.1 現股 / ETF
必須有：
- native symbol
- canonical symbol
- venue
- corporate action linkage
- delisting / rename history

## 8.2 期貨 / 選擇權
必須有：
- underlying mapping
- contract id
- expiry
- strike / right
- roll linkage
- continuous series 與 individual contract 分離

## 8.3 Crypto
必須有：
- venue-specific symbol
- canonical pair id
- perp / future / option classification
- settlement / collateral asset

### 正式要求
開發團隊不能把 symbol mapping 視為 UI 問題。
它必須是 Data Plane 的 first-class truth model。

---

# 9. 對 Research Plane 的直接要求

資料規劃不是只為 execution。Research Plane 也必須能消費。

## 9.1 每個市場至少要能產出這四種 dataset
1. universe-ready dataset
2. factor/feature-ready dataset
3. event-aligned dataset
4. derivative-aware dataset（若含衍生品）

## 9.2 replay requirement
Research run 必須能精確引用：
- dataset_version_id
- feature_spec_version
- label_spec_version
- symbol master version
- contract master version

## 9.3 比較 requirement
開發團隊需保證：
- 美股 / 台股 / 加密可用統一 dataset version 語言
- 但不強迫不同市場共用同一 schema 細節

---

# 10. 對 Execution Plane 的直接要求

資料面規劃也必須對 execution 有意義。

## 10.1 美股 execution 需要
- broker-aligned symbol mapping
- corp action-safe position history
- options / futures contract resolution
- borrow / shortability metadata（若 long/short）

## 10.2 台股 execution 需要
- local exchange / market segment mapping
- 現貨與衍生品不同 runtime mapping
- corp action-safe historical positions

## 10.3 Crypto execution 需要
- venue-specific instrument metadata
- precision / tick / lot size
- funding / mark / index / liquidation awareness
- cross-venue if and only if explicitly in scope

## 10.4 2026-06-09 台股低成本資料源決策

台股 research-grade 資料層採 **FinMind-first**：

- `TWSE` / `TPEx` / `MOPS` / `TDCC` / `TAIFEX` 仍是 official/reference truth owner。
- `FinMind` 是低成本主要 API/cache layer，用於日行情、籌碼、集保/股權分散、新聞 metadata、active-universe 分點 top20。
- `Yahoo Taiwan` 只保留為 public fallback：主要用於 FinMind quota、entitlement 或 endpoint health 不可用時的分點 top15 與 RSS metadata。
- `TEJ` 不再是第一順位採購；它保留為歷史補洞、較舊資料、審計型研究資料或 FinMind/公開源缺欄位時的補充。
- `TWSE` / `TPEx` 全量分點歷史採購放第二順位，只有 FinMind SponsorPro + TEJ 仍補不到時才一次性購買歷史，不先做每月全量訂閱。

分點資料具體路徑：

| 需求 | 第一順位 | 第二順位 | 更新策略 |
|---|---|---|---|
| active-universe 分點 top20 | FinMind Sponsor `TaiwanStockTradingDailyReport` | Yahoo top15 fallback | 每日收盤後，只跑 core/candidate |
| 2021-06-30 至今歷史分點 | FinMind SponsorPro daily parquet | TEJ ABSR20/AMTOP1 gap-fill | 短期 backfill，raw partition 全留 |
| 2021-06-30 以前或 vendor 缺洞 | TEJ 或 TWSE/TPEx 批次歷史 | 手動研究採購 | 只補研究標的與必要回測區間 |

儲存與更新規則：

- raw：`raw/finmind/<dataset>/date=YYYY-MM-DD/`，保留原始 JSON/parquet manifest；signed URL 不落入 evidence。
- normalized：`tw_broker_top`, `tw_price_daily`, `tw_institutional_flow`, `tw_margin_short_balance`, `tw_securities_lending`, `tw_shareholding`, `tw_news_metadata`。
- features：主力連買天數、top broker concentration、外資/投信連買、融資券變化、分點反轉。
- `core_universe` 和 `candidate_universe` 抓分點/新聞/籌碼細節；`archive_universe` 只保留日行情與重大事件，不再跑高量分點與新聞細節。

---

# 11. 建議的實作波次

## Wave D0：先定市場宇宙與商品宇宙
輸出：
- Market Scope Matrix
- Instrument Scope Matrix
- Execution Market Policy

## Wave D1：SecurityMaster / ContractMaster / Calendar
輸出：
- security master
- contract master
- calendar service

## Wave D2：raw / normalized / feature-ready 三層
輸出：
- raw dataset store
- normalization pipeline
- feature dataset pipeline
- dataset version registry

## Wave D3：美股 / 台股現貨與 ETF
輸出：
- equities + ETF 正式資料鏈
- corporate action-adjusted datasets
- event-aligned datasets

## Wave D4：Crypto spot + perp
輸出：
- venue-aware crypto master
- funding / OI / basis pipeline
- 24/7 replay rule

## Wave D5：衍生品 layer
輸出：
- options/futures contract chains
- IV / greeks / OI integration
- roll metadata / replay rules

## Wave D6：alt / event / fundamentals enrichment
輸出：
- news / transcript / filing / 籌碼 / on-chain / macro enrichment

---

# 12. 開發團隊必須回覆的問題

請開發團隊對以下問題逐條回覆：

1. 美股 / 台股 / crypto 是否正式列為 v1 primary market？
2. 各市場哪些現貨商品是必接？
3. 各市場哪些衍生品商品是必接？
4. 哪些只供 research，不供 execution？
5. 哪些資料源屬於 official / broker-aligned / research-grade / specialized analytics？
6. SymbolMaster / ContractMaster 誰是 truth owner？
7. DatasetVersion 是否已存在？若不存在，何時補齊？
8. replay 時是否能重建當時 options chain / futures contract state？
9. multi-market timezone / calendar discipline 是否已 formalize？
10. 哪些市場先進 paper，哪些市場可進 canary/live？

---

# 13. 驗收清單

若開發團隊要宣稱 Data Plane 市場規劃已完成，至少必須提供：

- `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md`
- `DATA_SOURCE_SCOPE_MATRIX.md`
- `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md`
- `DATASET_VERSION_AND_REPLAY_POLICY.md`
- 一份 market master schema
- 一份 contract master schema
- 一條美股資料鏈、一條台股資料鏈、一條 crypto 資料鏈的實際示例
- 至少一個 equities + one derivatives replay case

---

# 14. 結論

目前 Pantheon 的平台骨架、治理、執行、feedback 閉環已經非常成熟。
但從 Data Plane 視角，市場範圍與資料來源邊界若不先正式定義，開發團隊就無法知道應該接哪些資料、支持哪些商品、如何規劃 replay、contract master、symbol master、calendar 與衍生品鏈。

因此，對開發團隊最重要的要求不是「去找更多資料 API」，而是：

> **先把市場宇宙、商品宇宙、資料類別、source class、truth model 與 replay contract 定清楚。**

只有這樣，後續的 Research / Decision / Execution 才能真正對齊完整藍圖。
