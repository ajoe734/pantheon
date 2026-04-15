# Pantheon Blueprint Gap Review v1

## 文件定位

本文件不是用來否定目前 `pantheon` repo 已完成的成果。

依目前 repo 的 canonical collaboration/state 與 current work 來看，Pantheon 已經完成了大量平台級工作：L1 policy 層已正式收斂，Discussion Planning 已 accepted，Current Work 顯示 Phase 0 到 Phase 6 的大量任務已完成，而 `ai-status.json` 目前也顯示大部分 agent 無 active / blocker，主工作流任務大多處於 `done`。因此，本文件的目的不是回答「有沒有做」，而是回答：

> 相對於我們之前定義的完整藍圖，還缺哪些 plane-level 能力、哪些跨 plane 證據、哪些深度整合仍不足。

也就是說，這是一份：

- Blueprint Gap Review
- 不是 MVP backlog
- 不是 UI bug list
- 不是零散技術吐槽
- 而是對照完整藍圖的差距盤點

---

## 評估基準

本文件的唯一評估基準，是先前確認過的 Pantheon 完整藍圖。

該藍圖明確要求這是一套「可演化的 AI 多人格量化交易系統」，並拆成至少 8 個 Plane：

1. Data Plane  
2. Research Plane  
3. Persona Plane  
4. Memory & Knowledge Plane  
5. Decision Plane  
6. Execution Plane  
7. Governance Plane  
8. Feedback Plane  

同時要求研究/訓練層、治理層、執行層、監控回饋層分離，而且先把完整框架定清楚，不先收斂成 MVP。

因此，本文件不會以「某功能頁存在」就判定完整，而是會問：

- 這個 Plane 是否形成正式 object / service / truth model
- 是否有跨 plane 可追溯鏈路
- 是否已具備 replay / lineage / governance / rollback / feedback
- 是否有 evidence 證明這不是只存在於 contract，而是已能運作

---

## 目前已高度完成、因此不列為主要缺口的部分

先講清楚，不然開發團隊會誤以為我們在否定全部成果。

根據目前 repo 的 canonical 狀態，以下區域已經屬於高完成，不是本文件主攻點：

### 1. Governance Plane

`REG-004`、`GOV-001`、`DEP-001`、`DEP-002` 等已完成，代表 artifact_state / deployment_stage split、ApprovalDecision、DeploymentPlan、deployment saga、cross-service consistency 等治理主幹已正式落地。

### 2. Execution Plane

`CAP-001`、`RUN-001`、`EX-002`、`EVO-005` 等已完成，表示 capital pool / PersonaCapitalBinding、RuntimeBinding / runtime-manager authority、rollback execution semantics、kill-switch / safe-mode fast path 已經進入正式平台語義。

### 3. Persona / Operator / BFF surfaces

`PER-001`、`APP-001`、`APP-002`，以及 `APP-002-W0 ~ W5` 的 deployment review、incident response、CLI fallback、post-incident/evolution review、persona management、remaining catalog、SSE、Lovable cutover 等實作波次都已完成，代表 operator-facing 與 persona-facing surfaces 已有很高完成度。

### 4. Feedback / Telemetry / Incident / Evolution 主幹

`TEL-001`、`TEL-002`、`LIN-001`、`LIN-002`、`INC-001`、`EVO-003`、`EVO-004`、`EVO-005` 已完成，表示 telemetry、lineage、incident/postmortem、EvolutionDecision、kill-switch 與 cooldown/convergence 主幹已正式化。

因此，本文件不會重複要求團隊「再把 governance 或 execution 從零做一遍」。
本文件要抓的是：相對完整藍圖仍不足的那一層深度。

---

## 缺口判定原則

下列情況才列為 gap：

### A. 相對完整藍圖少了一整層能力
例如完整藍圖要求 Data Plane 有 raw / normalized / feature-ready 三層，但 repo 只看到 ingestion 與 telemetry schema，沒有看到完整 data factory。

### B. 已有 contract，但沒有足夠 evidence 證明深接完成
例如某 framework 已 activation-ready，但沒有證據顯示它成為正式主工作流。

### C. 單一 plane 自己完成度高，但跨 plane 閉環證據不足
例如 lineage / telemetry / deploy 各自完成，但缺少標準化 replay scenario。

### D. 已有功能，但尚未形成 production acceptance language
例如 UI / BFF / CLI 已存在，但沒有明確 authoritative/degraded/fallback acceptance matrix。

---

# 主要缺口盤點

## GAP-00：市場範圍與資料來源範圍尚未被正式定義成 Data Plane 輸入真相

### 對應 Plane
- Data Plane
- Research Plane
- Execution Plane

### 問題本質

開發團隊如果沒有被正式告知：

- 交易會在哪些市場發生
- 哪些資產類別是 v1 必須支持
- 哪些資料來源是必接、哪些只是 optional
- 哪些市場要支持現貨、哪些要支持衍生品

那麼 Data Plane 就無法正確定義：

- symbol master
- market calendar
- corp action normalization
- derivative chain / contract spec ingestion
- broker / venue adapter priority
- replay / backtest dataset scope

這不是單純「少接一個 API」的問題，而是整個平台缺少**市場宇宙（Market Universe）**與**資料輸入範圍（Data Scope）**的正式邊界。

### 正式建議：v1 交易市場範圍

請開發團隊以以下三個主市場作為 v1 的正式市場宇宙：

1. **美股（US Equities & Listed Derivatives）**
2. **台股（TW Equities & Listed Derivatives）**
3. **加密貨幣（Crypto Spot & Derivatives）**

### 正式建議：v1 資產類別範圍

#### 美股
- 現股（common stocks / ADR）
- ETF
- 美股個股選擇權
- 美股指數相關期貨 / 選擇權（至少作為研究與風控輸入）

#### 台股
- 上市 / 上櫃現股
- ETF
- 台指 / 相關指數期貨與選擇權
- 個股期貨 / 選擇權（若策略族群使用）

#### 加密貨幣
- 現貨交易對
- 永續合約（perpetuals）
- 交割合約 / dated futures
- 選擇權（若研究與 execution 範圍涵蓋）

### 風險 / 影響

如果這個市場宇宙不先定，團隊會在以下地方反覆返工：

- Data schema
- symbol mapping
- contract master
- market calendar
- venue adapters
- funding / margin / greeks / OI / borrow 等衍生資料結構

### 要求開發團隊補齊的內容

請建立：

1. `MARKET_SCOPE_AND_INSTRUMENT_POLICY.md`
2. `DATA_SOURCE_SCOPE_MATRIX.md`
3. `SYMBOL_MASTER_AND_CONTRACT_MASTER_POLICY.md`

並明確定義：

- v1 primary markets
- v1 supported instruments
- per-market required data classes
- per-market broker / venue execution target
- 哪些市場先 paper、哪些可進 canary/live

### 驗收證據

- 一份正式 market scope matrix
- 一份 per-market required data class matrix
- 一份 symbol master / contract master schema
- 一份 market calendar / timezone / session policy

### 生產驗收影響
**最高。**
這是所有 Data / Research / Execution 工作的上游基礎。

---

## GAP-01：Data Plane 尚未達到完整藍圖要求的三層資料工廠

### 對應 Plane
Data Plane

### 藍圖要求
完整藍圖要求 Data Plane 至少具備：

- raw / normalized / feature-ready 三層分離
- `event_time / available_time / ingest_time`
- 多來源資料統一版本化
- 可回放、可追溯、可重建的資料流

這不只是 telemetry ingest，而是整套 research-grade data factory。

### 目前已完成的部分
目前 repo 已完成不少資料治理相關元素：

- research ingestion (`RS-001`)
- StrategySpec normalization (`RS-002`)
- replication gate (`RS-003`)
- telemetry truth with deployment stage / runtime binding refs (`TEL-001`)
- telemetry ingest shock absorption (`TEL-002`)
- lineage edges 與 read-model (`LIN-001`, `LIN-002`)
- feedback / trajectory / preference schema (`FB-001`, `FB-002`, `FB-003`)

### 缺口定義
目前仍缺少明確證據顯示下列東西已正式存在：

1. **raw data layer**
   - 原始市場 / 基本面 / 事件 / 替代 / 交易內部 / 人工回饋資料的統一保存規格
2. **normalized data layer**
   - symbol mapping、corporate action、time normalization、availability discipline
3. **feature-ready layer**
   - research worker 可以直接消費的版本化 dataset 對象
4. **dataset version object**
   - 研究與回放時能引用的正式 dataset version
5. **availability-time discipline**
   - 不只是 event_time，而是資料何時真正對模型/策略可見
6. **dataset replay contract**
   - 能重建當時研究輸入的正式 replay path

### 風險 / 影響
如果這一層不補齊，會直接影響：

- 研究可重現性
- look-ahead leakage 防治
- incident / postmortem 對資料層的歸因
- evolution / retrain 的可信度
- cross-plane replay 的完整性

### 建議 Owner
- Owner Plane：Data Plane
- Source of Truth：`registry-core` 下的 data-domain schema 或專門 data-catalog service
- Primary Services：
  - source-ingest / normalize pipeline
  - dataset registry
  - feature materialization service

### 要求開發單位補齊的內容
1. 列出 **Data Plane 物件清單**
   - `RawDataset`
   - `NormalizedDataset`
   - `FeatureDataset`
   - `DatasetVersion`
2. 列出 **資料來源分類**
   - 市場
   - 基本面
   - 事件
   - 替代
   - execution internal
   - human feedback
3. 列出 **availability discipline**
   - event_time
   - available_time
   - ingest_time
4. 補一份 **dataset replay flow**
5. 說明目前哪些 dataset 已正式成為 research input truth

### 驗收證據
- schema / object definitions
- 至少一條 raw→normalized→feature 的實際 pipeline
- 一個可查詢的 dataset version object
- 一次完整 research run 可 pin 到 exact dataset version

### 生產驗收影響
**高。**
這是完整藍圖缺口，不補齊會限制整個 Research / Decision / Feedback 的可信度。

---

## GAP-02：Research Plane 雖有大量整合，但正式主工作流深度仍不均衡

### 對應 Plane
Research Plane

### 藍圖要求
完整藍圖要求 Research Plane 至少包含：

- idea / hypothesis factory
- feature / factor lab
- strategy lab
- backtest / simulation lab
- evaluation board

且應能承接：

- Qlib formal workflow
- vectorbt prototype
- statsmodels econometrics/regime
- QuantLib pricing/risk
- FinRL / RLlib / Tune sandbox 等不同 research backend

### 目前已完成的部分
repo 顯示以下已完成：

- `RS-001` 研究素材 ingestion
- `RS-002` StrategySpec normalization
- `RS-003` first-pass replication gate
- `LP-001` DSPy
- `LP-002` imitation
- `LP-003` MLflow-first registry adapter
- `LP-004` TRL preference workflow
- `LP-005` RL path definition
- `OSS-001` OpenClaw source pin
- `OSS-002` DSPy / imitation / MLflow regrade
- `OSS-003` deferred frameworks activation criteria

### 缺口定義
現在缺的不是「有沒有 research」，而是：

1. **哪些 backend 已是 production research path，哪些只是 activation-ready**
2. **Qlib / vectorbt / statsmodels / QuantLib / RL stack 的成熟度矩陣**
3. **是否每類研究問題都已有對應主 backend**
4. **研究→artifact→registry→promotion 的跨 backend 一致性**
5. **formal experiment orchestration 是否對所有 research family 一致存在**

### 風險 / 影響
如果不補這一層，會導致：

- 研究 backend 的成熟度被高估
- 不同策略家族沒有明確主工作流
- 團隊誤以為「定了 activation criteria = 已深接完成」
- 後續 production hardening 難以排優先級

### 建議 Owner
- Owner Plane：Research Plane
- Source of Truth：research orchestrator / experiment registry
- Primary Services：
  - experiment orchestrator
  - backend adapter registry
  - experiment lineage / artifact handoff

### 要求開發單位補齊的內容
請提交一份 **Research Backend Maturity Matrix**，至少列出：

- framework
- role
- status（not integrated / activation-ready / smoke-tested / production research path）
- current owner
- current example strategy family
- missing proof

重點要覆蓋：

- OpenClaw
- Qlib
- vectorbt
- statsmodels
- QuantLib
- FinRL / RLlib / Tune
- DSPy
- imitation
- TRL
- MLflow / W&B

### 驗收證據
- 每個 backend 至少一條可執行 smoke/replay path
- 明確標出 production research path vs non-production path
- 至少 3 種策略族群的正式 backend chain 跑通

### 生產驗收影響
**中高。**
不一定阻塞平台運作，但會直接限制完整藍圖中的研究深度與可擴展性。

---

## GAP-03：Decision Plane 後段強、前段弱，完整五級決策鏈仍未完整 formalize

### 對應 Plane
Decision Plane

### 藍圖要求
完整藍圖把 Decision Plane 拆成五級：

1. Market Regime Inference
2. Universe Selection
3. Signal / Alpha Inference
4. Portfolio Construction
5. Risk Adjudication

### 目前已完成的部分
repo 目前比較強的是後段：

- `CAP-002` multi-persona synthesis module
- optimizer / registry handoff
- risk / governance / deploy adjudication
- binding / runtime / rollback semantics

這些都已進正式任務並完成。

### 缺口定義
現在最主要的缺口是前 3 級尚未看到同等成熟度的 formalization：

1. **Regime inference object**
   - 是否有 `RegimeState` / `RegimeDecision`
2. **Universe selection object**
   - 是否有可版本化 `UniverseSelection`
3. **Signal / alpha inference object**
   - 是否有正式 `SignalInference` / `AlphaDecision`

目前看起來：
- portfolio construction / risk adjudication 已成熟
- regime / universe / signal inference 仍偏 research artifact 或 implicit logic

### 風險 / 影響
這會導致：

- 前段決策 provenance 不完整
- telemetry / postmortem 很難說清楚「到底是 regime 判錯、universe 選錯、還是 alpha model 退化」
- Persona / Research / Execution 之間的中間決策層仍不夠可審核

### 建議 Owner
- Owner Plane：Decision Plane
- Source of Truth：decision-registry 或 registry-core 下 decision schema
- Primary Services：
  - regime evaluator
  - universe selector
  - signal inference service
  - portfolio synthesis / optimizer

### 要求開發單位補齊的內容
請補一份 **Decision Layer Object Map**，至少正式定義：

- `RegimeState`
- `UniverseSelection`
- `SignalInference`
- `AllocationDecision`
- `RiskAdjudication`

並說明：
- 哪些已存在
- 哪些只是隱含在 research / runtime / optimizer 內
- 哪些需升格為 first-class object

### 驗收證據
- decision objects schema
- 端到端 provenance chain 可追到這些物件
- 至少一個 strategy family 跑出完整五級決策鏈 replay

### 生產驗收影響
**中高。**
這不是平台生存問題，但它是完整藍圖能否自洽的核心缺口。

---

## GAP-04：Memory & Knowledge Plane 目前偏 registry / lineage，persona memory 與 institutional memory 不夠完整

### 對應 Plane
Memory & Knowledge Plane

### 藍圖要求
完整藍圖要求至少四種記憶：

- episodic memory
- semantic memory
- persona memory
- shared institutional memory

### 目前已完成的部分
repo 在知識平面上已經很強：

- canonical document layers
- registry
- lineage
- incident / postmortem
- evolution decision
- many L1 policy files as truth source

### 缺口定義
但目前仍看不到清楚證據顯示：

1. **persona-specific memory**
   - persona 是否有獨立 memory object / retrieval contract
2. **institutional memory**
   - postmortem / review / research lessons 是否沉澱成全系統知識層
3. **semantic knowledge retrieval**
   - 是否有正式 retrieval path，而不是只靠文件散落
4. **memory write-back pipeline**
   - incident / evolution / consultation / trainer 是否正式寫回 memory plane

### 風險 / 影響
不補這塊，會出現：

- persona 只是 lifecycle object，不是真正有記憶的 agent
- incident lessons 難以轉成 institutional memory
- research / consultation 容易重複犯同樣錯
- blueprint 裡的「人格演化」會更像參數更新，而不是知識演化

### 建議 Owner
- Owner Plane：Memory & Knowledge Plane
- Source of Truth：memory service / registry extension / knowledge store
- Primary Services：
  - persona-memory-svc
  - institutional-memory index
  - retrieval facade

### 要求開發單位補齊的內容
請補一份 **Memory Layer Design Note**，說明：

- persona memory object 是什麼
- institutional memory object 是什麼
- 誰能寫、誰能讀
- 哪些事件會寫回 memory
- operator / research / consultation / evolution 如何讀取 memory

### 驗收證據
- 至少 2 種 memory object schema
- 一條 write-back pipeline
- 一條 retrieval query path
- 一個 postmortem → institutional memory → later research reuse 的例子

### 生產驗收影響
**中。**
不一定阻塞 v1 運作，但會明顯限制「完整藍圖中的可演化人格系統」成熟度。

---

## GAP-05：Data → Research → Decision → Execution → Feedback 的跨 Plane replay 證據仍不足

### 對應 Plane
跨 Plane gap，不屬單一 Plane

### 藍圖要求
完整藍圖的核心不是單點功能，而是閉環：

Data Plane  
→ Research Plane  
→ Persona / Decision Plane  
→ Execution Plane  
→ Feedback Plane  
→ 回寫 Knowledge / Evolution / Governance

### 目前已完成的部分
單一 Plane 很多東西都已 done。
尤其 governance、execution、feedback 這條線非常強。

### 缺口定義
目前缺的不是局部功能，而是：

> 一條可以作為標準驗收證據的 cross-plane replay scenario

也就是要能證明：

- 用哪版資料
- 形成哪個 StrategySpec
- 跑出哪個 ExperimentRun / CandidateArtifact
- 經過哪個 ApprovalDecision / DeploymentPlan
- 形成哪個 RuntimeBinding
- 實際收到哪些 Telemetry / Incident / Postmortem / EvolutionDecision

目前 repo 任務顯示各域都已經很多 done，但還沒有看到「標準化 replay scenario」被當成正式 closure artifact。

### 風險 / 影響
如果沒有這條證據：

- 系統看起來每塊都完成，但整體不能證明可重播
- 很難做真正的最終驗收
- production sign-off 會變成相信 task board，而不是相信端到端證據

### 建議 Owner
- Owner Plane：跨 plane integration
- Source of Truth：integration acceptance suite
- Primary Services：
  - registry-core
  - runtime-manager
  - telemetry / lineage
  - decision / research orchestrator

### 要求開發單位補齊的內容
請建立一個 **Golden Replay Scenario**，至少有：

1. 固定 dataset version
2. 固定 StrategySpec / artifact
3. 固定 ApprovalDecision / DeploymentPlan
4. 固定 RuntimeBinding
5. telemetry / incident / evolution output
6. 可 script 化重播

### 驗收證據
- replay script / runbook
- golden dataset ref
- golden artifact ref
- replay log
- expected output manifest

### 生產驗收影響
**高。**
這是從「任務完成」走向「系統驗收完成」的關鍵缺口。

---

## GAP-06：Operator / App Surfaces 雖然很強，但還缺 production acceptance language

### 對應 Plane
Persona Plane + Operator / Application Surface cross-gap

### 藍圖要求
完整藍圖要求 operator 不只是看 dashboard，而是操作 canonical objects，並能在 degraded / fallback 條件下仍安全運作。

### 目前已完成的部分
repo 內 APP-002 波次已極完整：

- deployment read/command
- incident read/control
- CLI fallback
- post-incident/evolution view
- persona management
- SSE live
- lovable/front cutover

### 缺口定義
雖然 surfaces 已多數落地，但仍應再明文化：

1. 哪些 surface 是 **authoritative**
2. 哪些是 **composed convenience**
3. 哪些 degraded mode 已正式可操作
4. 哪些 fallback path 是 operator runbook required
5. 哪些仍保留 scaffold / sidecar / support-only 性質

### 風險 / 影響
如果這層不補：

- 會出現「頁面很多，但最終驗收標準不清楚」
- operator 不知道哪條路是 canonical
- UI / CLI / fallback / internal API 的真實權限關係容易誤解

### 建議 Owner
- Owner Plane：Application / BFF / Operator Surface
- Source of Truth：BFF contract + operator acceptance spec
- Primary Services：
  - BFF
  - internal API
  - pantheon-admin
  - frontend / front repo integration

### 要求開發單位補齊的內容
請建立一份 **Operator Acceptance Matrix**，欄位至少包含：

- surface name
- canonical object
- authoritative / composed / fallback / support-only
- degraded behavior
- required permissions
- test status
- operator drill status

### 驗收證據
- operator acceptance script
- degraded mode drill
- CLI fallback drill
- BFF down scenario drill
- lovable/front repo cutover confirmation

### 生產驗收影響
**中高。**
功能本身可能已經完成，但如果沒有這份 acceptance language，最後驗收會缺少統一語言。

---

## GAP-07：Research / Data / Decision 的產品側語言還沒有完全統一成 operator 可理解的驗收格式

### 對應 Plane
跨 plane productization gap

### 問題本質
目前 repo 的 canonical 文件、tasks、review packet 很強，但很多語言仍偏工程導向：

- schema
- contract
- lineage edge
- binding
- deployment stage
- smoke test
- review notes

這些對研發團隊很好，但對最終驗收者來說，還需要一層：

> 產品級 acceptance language

也就是能回答：

- 這個 persona 現在可做什麼？
- 這個 strategy 現在在哪個 stage？
- 這個 artifact 可不可以進 canary？
- 這個 drift report 觸發的是 revalidate 還是 freeze？
- 這個 operator action 到底會動到哪個 canonical object？

### 建議 Owner
- Owner Plane：Product / Operator Acceptance
- Source of Truth：operator acceptance docs + BFF/API read models

### 驗收證據
- operator manual
- action→object mapping
- glossary / acceptance language table

### 生產驗收影響
**中。**
不一定阻塞內部開發，但會阻塞最終交付與跨角色協作。

---

# 5. 缺口優先級排序

## P0（應在最終驗收前補齊）
1. GAP-00：市場範圍與資料來源範圍正式定義  
2. GAP-05：跨 Plane replay 證據  
3. GAP-01：Data Plane 三層資料工廠與 dataset version / replay  
4. GAP-03：Decision Plane 前段 formalization  

## P1（應在 production sign-off 前補齊）
5. GAP-02：Research backend maturity matrix  
6. GAP-06：Operator Acceptance Matrix  

## P2（應在完整藍圖收斂期補齊）
7. GAP-04：persona / institutional memory formalization  
8. GAP-07：product-level acceptance language  

---

# 6. 要求開發單位的回覆方式

請開發單位**不要只回「已完成」或「待補」**。  
對每個 gap，必須以以下格式回覆：

```md
## Gap-ID
### Current Status
### Existing Evidence
### Why It Is / Is Not a Real Gap
### Proposed Owner
### Source of Truth
### Planned Closure Work
### Acceptance Evidence
### Target Wave / Date
### Production Sign-off Impact
```

並且要附：
- 對應 repo path
- 對應 canonical file
- 對應 task id（若已有）
- 若尚無 task id，需先補 task materialization

---

# 7. 這份文件的流程定位

這份 Blueprint Gap Review 在流程上屬於：

## Post-Implementation Blueprint Convergence Review

它不是：
- sprint bug triage
- daily engineering issue list
- MVP backlog

它是：
- 完整藍圖收斂盤點
- v1 backlog 完成後的藍圖對齊審核
- production sign-off 前的架構層差距確認

流程順序應該是：

1. 開發單位先承認目前已完成的 backbone  
2. 再逐項回覆 Blueprint gaps  
3. 補 task / owner / evidence  
4. 用 replay + acceptance matrix 做最後驗收  
5. 再決定是否能宣告「完整藍圖 v1 已閉環」

---

# 8. 結論

目前 Pantheon 並不是「還在概念期」。  
從 repo 的 canonical 狀態看，平台骨架、治理、執行、回饋、operator surfaces 都已高度成熟。

但如果以完整藍圖為準，而不是以任務板為準，仍有幾個核心差距：

- 市場範圍與資料來源範圍尚未正式定義成 Data Plane 輸入真相
- Data Plane 尚未完全形成三層資料工廠
- Research Plane 深度整合仍不均勻
- Decision Plane 前段 formalization 不足
- Memory / institutional memory 仍不夠完整
- 缺少標準化 cross-plane replay 證據
- 缺少 operator-level acceptance matrix 與產品化驗收語言

因此，對開發單位的正確描述不是：

> 你們還沒做完。

而是：

> 你們已經把 Pantheon 的平台 backbone 做到很深，但相對完整藍圖，還有幾個高價值 gap 需要補齊，才能從「大部分 v1 backlog 已完成」提升到「完整藍圖收斂完成」。
