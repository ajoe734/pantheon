# SD：紙上池風控整體設計（量測 → 執行 → 回饋 → 配置與升級）

- 日期：2026-11-24
- 狀態：**設計，待操作者審閱。** 操作者核准前，不開本文件 §5 列出的實作任務。
- 問題分類：RC2「Done means merged, not working」（`docs/operations/problem-register.md`）
- 撰寫：Claude（協調對話）。依據是三份平行盤點：規劃文件、程式現況（dev `adaa418de`，加上 #7781、#7783）、業界與開源做法。
- 行號：除另有標註外，引用的是 dev `6f1375b6c`（含 #7783）的行號；程式行號以 `adaa418de` 為準。

## 0. 已定的操作者決定（本文件不再討論）

| 日期 | 決定 | 出處 |
|---|---|---|
| 2026-11-07 | 每位 persona 的槓桿與總名目曝險上限為池淨值的 2.0 倍（`max_leverage = 2.0`、`gross_limit = 2.0`）；可逐位經治理政策調整；演化不能改 | `docs/04/pantheon_persona_instrument_decision_2026-11-07/DEV_PLAN_INSTRUMENT_EXPRESSION_LAYER_2026-11-07.md` 決定表 Q3 |
| 2026-11-12 | P-1 每個標的合併成一個淨部位，每個策略各有一本虛擬帳；P-2 池大小看風險額度利用率和有效下注數；P-4 paper 內的動作不需核准，超出 paper 才要操作者；P-6 保留寬的安全上限 | `docs/04/pantheon_strategy_discovery_sources_2026-11-10/SD_PERSONA_STRATEGY_POOL_AUTONOMY_2026-11-12.md` §決定表 |
| 2026-11-15 | paper 自動核准 | 同上，11/15 修訂 |
| 2026-11-23 | 風控政策的內容由系統的風控模型（`persona-risk-officer`）草擬，不由工程或操作者填數字；低風險外框：單檔 ≤ 20%、現金 ≥ 10% | `docs/deployment/evidence/PAPER-RISK-OFFICER-DRAFTED-POOL-POLICY-20261123/evidence.json` |
| 2026-11-24 | 低風險外框改用已定數字：槓桿、總曝險 ≤ 2.0，單檔 ≤ 0.20，現金 ≥ 0.10（任務 `PAPER-RISK-ENVELOPE-OPERATOR-DECISIONS-20261124`） | 本對話 |
| 2026-11-24 | **池層級回撤分級**：從高點回撤 5% 時，可用曝險減半；回撤 10% 時，只准減倉。風控長可以在這個範圍內訂得更嚴 | 本對話 |
| 2026-11-24 | 先寫設計文件給操作者看過，再開任務；外框任務先行 | 本對話 |

## 1. 原則

1. **風控有否決權**（藍圖公理 3，`Pantheon_總索引版系統分析文件.md:91-92`）。風險層是部署與執行的否決層，不是附屬模組。
2. **LLM 在外框內草擬，程式負責執行。** 風控長只產生參數。下單前的檢查、分級降風險、閘門核准都是確定性程式。LLM 無法把任何限制放寬到外框之外。業界做法相同：多經理人基金由中央風控強制執行限額；開源的 LLM 交易框架（TradingAgents、FinCon）讓風控 agent 只給建議，而 Pantheon 給它否決權。
3. **自動路徑只能收緊。** 演化目錄裡沒有任何放寬風險的動作（`EVOLUTION_REVIEW_AND_THRESHOLDS.md:98-116`），緊急狀況只能降、不能升。放寬只能在外框內：paper 經自動閘門，canary／live 經 Risk Owner。
4. **每一次調整都要有量測依據。** 沒有量測資料，就不做調整，只記錄「資料不足」。

## 2. 規劃要求與現況對照

標記：✅ 有做也接上；⚠️ 有做但沒接上或只做一半；❌ 沒做。

| 層 | 規劃要求 | 現況 | 證據 |
|---|---|---|---|
| 下單前 | 藍圖七項限制（`Pantheon_總索引版系統分析文件.md:1184-1191`），加上 SD-06 的 `drawdown_actions{warn, risk_off, liquidate}`（`docs/03/SD-06_capital_pool_governance.md:126-129`） | ✅ 總曝險、淨曝險、槓桿、單檔、現金、資產類別、保證金；❌ 回撤、緊急停止、週轉、產業、流動性：欄位存在，但檢查時沒有傳入對應資料 | `services/execution/lean_runtime/paper_runtime.py:2089-2111` |
| 自動降風險 | 池可以由系統觸發 `risk_off`（SD-06:219、331） | ❌ 緊急停止只能由操作者下指令，或在部署補償時觸發 | `services/control-plane/bff/command_adapters/runtime_adapter.py:510` |
| 池內 | 一開始按風險平均分權重，再依績效調整；策略回撤 5% 權重減半、10% 移出；試用策略在 20 天內最多用 10% 的風險額度（POOL SD:309-312） | ✅ 這是唯一在跑的自動風險迴圈 | `services/control-plane/persona/pool_policy.py:230-261` |
| 池健康 | 利用率 = 總名目 ÷ 總曝險上限，以及回撤 ÷ 回撤上限；20 天內利用率 < 60%，或有效下注數 < 2，就開研究需求（POOL SD:217-222） | ⚠️ 有計算的程式，但沒有任何東西寫入 `pool_observations`，所以健康度永遠是「未知」 | `services/persona/persona_cycle_controller.py:1586` |
| 配置 | 每季排名 × `risk_budget_factor` × `capacity_factor`（O-21） | ⚠️ 兩個係數恆為 1.0；季配置只認共用池 `pool-paper-default`，但每位 persona 各有自己的池 | `docker-compose.yml:3615`；`services/persona-evaluator-agent/persona_evaluator_agent.py:622-645` |
| 排名 | 風險分數 < 55 就減資金 | ⚠️ 風險分數有一半來自自己宣告的等級（宣告「低」就有 90 分的底分），不是量到的風險 | `services/control-plane/bff/personas/service.py:7242-7262` |
| 風控長 | 上 paper 前要先會診；試用准入、envelope 發布、harness 修改時必須參與，而且有否決權 | ✅ 否決權已接上；⚠️ 草擬只在建池時跑一次；它的輸入看不到績效、部位、回撤、盤勢（`recent_paper_evidence` 永遠是空的，`risk_evidence_reader` 永遠是 None） | `services/control-plane/bff/persona_provisioning_coordinator.py:350, 2029-2031` |
| 演化 | `tighten_risk_policy`、`reduce_budget` 是中風險：冷卻 7 天、觀察 7 天；`force_risk_off` 是高風險：14 天／14 天（`EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md:138-140`） | ❌ 會發出指令，但沒有任何程式執行它 | `services/control-plane/governance/evolution_controller.py:258, 447-456` |
| 盤勢 | 盤勢納入風控檢查，留給後續的使用者 | ❌ 盤勢只會觸發 MT-5 會議 | `services/persona/meeting_triggers.py:16-17` |
| 升級 | paper → canary 要 20 個交易日、回撤 ≤ 預期的 1.2 倍、風控違規 0 次；canary 的總曝險只能用 live 限額的 25%（`PAPER_CANARY_LIVE_POLICY.md:153-181`） | ⚠️ 沒有回撤的「預期值」，所以無法判斷 | — |
| 跨 persona | 池的風險邊界不跨 persona 共用（藍圖:77） | ❌ 沒有檢查多位 persona 是否擠在同一個標的 | — |
| 外框 | — | ⚠️ 只有 `low` 有外框，`medium`、`high` 會被閘門拒絕 | `services/capital/risk_policy.py` `DEFAULT_PROFILE_OUTER_BOUNDS` |

## 3. 設計

### 3.1 量測：每池每日風險快照（最優先，其他層都依賴它）

每個紙上池在每個交易日收盤結算後寫一筆 `PoolRiskSnapshot`：

| 欄位 | 定義 |
|---|---|
| `as_of`, `pool_id`, `persona_id`, `policy_ref` | 識別 |
| `equity`, `peak_equity`, `drawdown_pct` | 從高點回撤。分級動作觸發後重設高點（業界做法：處理後重設基準） |
| `realized_vol_20d` | 20 個交易日日報酬的年化標準差，資料不足 20 天時為 `null` |
| `gross_exposure`, `net_exposure`, `leverage` | 收盤部位的名目值 ÷ 淨值 |
| `gross_utilisation` | `gross_exposure ÷ gross_limit`（POOL SD:217） |
| `drawdown_utilisation` | `drawdown_pct ÷ risk_off 門檻`（POOL SD:217） |
| `effective_bets` | 沿用 `pool_policy.py:139-147` 的參與比 |
| `turnover_20d` | 20 天成交名目 ÷ 平均淨值 |
| `top_name_weight`, `cash_reserve_pct` | 最大單一標的權重、現金比例 |
| `cvar_95` | 20 筆以上日損益時，取最差 5% 的平均；不足時為 `null` |
| `ladder_state` | `normal`、`halved` 或 `reduce_only` |
| `breaches` | 當天下單前檢查拒絕的次數，依限制項分類 |

用途：
- 寫入 `pool_observations`，池健康度（利用率、有效下注數）因此可以計算。
- 計算 O-21 的 `risk_budget_factor`。
- 作為風控長重擬時的輸入。
- 計算排名的風險分數。
- 作為升級條件的回撤預期值。

**執行期預算：**
- 頻率：每池每交易日 1 筆，約 1 KB。
- 規模：3 個池時每年約 750 筆、不到 1 MB；20 個池時每年約 5 MB。
- 上限與清理：保留 400 個交易日，超過的每天清掉。
- 要登記到 `docs/operations/storage-manifest.json`。

### 3.2 執行：下單前檢查與自動分級降風險

**池層級回撤分級**（操作者 2026-11-24）。對應到 SD-06 的 `drawdown_actions`：

| 狀態 | 觸發條件 | 效果 | 解除條件 |
|---|---|---|---|
| `halved` | 從高點回撤 ≥ 5%（風控長可訂得更低） | 總曝險和槓桿的可用額度 × 0.5；已持有的部位不強制平倉 | 淨值回到觸發前高點的 97.5% 以上（**待決 Q1**） |
| `reduce_only`（SD-06 的 `risk_off`） | 從高點回撤 ≥ 10%（風控長可訂得更低） | 只准減倉或平倉的單，拒絕所有開倉或加倉的單 | 風控長重擬政策並經過 paper 自動閘門（SD-06:287「recovery approval」） |

- 分級由下單前檢查依 `PoolRiskSnapshot` 和當天盤中的權益判定，**不靠 LLM**。
- 狀態變更要寫入事件，並觸發風控長重擬（見 §3.3）。
- `liquidate` 在 paper 不設；canary／live 由 Risk Owner 訂（**待決 Q5**）。

**另外三項限制**（藍圖 §11.5）：
- 產業：用已經在抓的證交所產業分類。
- 週轉：用虛擬帳本或總帳計算。
- 流動性：下單量 ÷ 20 日平均成交量。

做法是把這三項資料接進現有的 `RiskPolicyEvaluator`，不另寫一套規則引擎。數值由風控長草擬，外框是否設定見**待決 Q2**。原本的任務 `RISK-POLICY-BLUEPRINT-LIMITS-ENFORCED-20261124` 依本節修改。

### 3.3 回饋：怎麼調整

| 迴圈 | 頻率 | 執行者 | 做什麼 |
|---|---|---|---|
| 每日 | 每個交易日 | 程式 | 回撤分級（§3.2）；池內策略權重調整（既有）；選用：波動目標化，把可用總曝險乘上 `min(1, 目標波動 ÷ realized_vol_20d)`，結果仍不能超過硬上限（**待決 Q3**） |
| 重擬 | 每週 1 次，另外在事件發生時觸發：分級狀態改變、MT-5 盤勢會議結論、MT-4 歸因會議結論、操作者修改風險等級、演化發出 `tighten_risk_policy` 或 `reduce_budget` | 風控長 | 輸入：近 20 和 60 天的 `PoolRiskSnapshot`、目前部位、歸因、盤勢狀態、會議紀要、現行政策。輸出：新政策和理由 |
| 每季 | 季排名 | 程式＋操作者 | 見 §3.4 |

重擬的結果依 `compare_risk_policies`（#7783）判斷方向：
- `reduce` 或 `neutral`（收緊或不變）：paper 直接生效，並記錄決定。
- `increase` 且在外框內：paper 經自動閘門。距離上一次放寬未滿 7 天的話，延到冷卻期結束（沿用 `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md:139` 中風險的 7 天）。
- 超出外框：拒絕，並寫明超出哪一項。
- canary／live：一律交給 Risk Owner。

演化發出的 `tighten_risk_policy` 或 `reduce_budget`，由這條重擬路徑執行，並限定只能收緊。

### 3.4 配置與升級

1. **排名的風險分數改用量到的數字。** 改成用回撤 ÷ 回撤上限、實際波動、違規次數計算，拿掉「宣告等級的底分」。
2. **季配置改成配到各 persona 自己的池。** 修正 `pool-paper-default` 只認共用池的錯位。`risk_budget_factor` 由 `gross_utilisation` 計算（O-21，**待決 Q4**）。
3. **升級 canary 的條件。**
   - 沿用 `PAPER_CANARY_LIVE_POLICY.md:153-172`；其中「預期回撤」改用快照的歷史值。
   - 加上最短績效長度（MinTRL），以及考慮試過幾個變體的 Deflated Sharpe，確認紙上成績不是運氣。前提是研究端記錄了每次嘗試的變體數。
4. **跨 persona 集中度。** 每日彙總所有池在同一個標的上的名目曝險。paper 階段只發警示；canary／live 設上限，由 Risk Owner 訂。

## 4. 業界參考（摘要）

- **多經理人基金：**
  - 每個經理人有自己的風險額度和回撤分級（常見報導是虧 5% 資金減半、7.5% 出場，屬媒體報導，不是官方數字）。
  - 處理後重設基準；中央會看多個經理人是否擠在同一檔。
  - Man 的研究提醒，固定停損也會砍掉好的經理人，所以分級門檻最好之後改用各池自己的波動當單位（**待決 Q3**）。
- **波動目標化：** 用最近波動的倒數縮放曝險（Moreira–Muir）。這是最簡單、隨盤勢變動的限制。
- **風險貢獻與有效下注數：** 用 Euler 分解算各策略佔池風險的比例。有效下注數建議用 Minimum-Torsion 版本，比 Meucci 最初的 PCA 版本穩定。
- **QuantConnect Lean：** 風控模型是一串確定性的限制器，每個時間步都跑。預設值：個股回撤 5%、組合回撤 5%、產業上限 20%。
- **台股：**
  - 期貨的限制要看名目金額和波動，不能看保證金：微台每口名目約 40 萬元，保證金約 9%，等於約 11 倍槓桿。
  - 權證要依流動性提供者的報價量決定下單量；紙上成交用流動性提供者的買價或賣價，不要用中間價。

## 5. 分階段任務（操作者核准後才開，第 0 階段除外）

| 階段 | 任務 | 主要驗收 |
|---|---|---|
| 0（進行中） | `PAPER-RISK-ENVELOPE-OPERATOR-DECISIONS-20261124` | 外框為 2.0／2.0／0.20／0.10，資金端與閘門都檢查，**11/25 10:00Z 前上線** |
| 1 | `PAPER-POOL-RISK-SNAPSHOT` | §3.1 的快照每天都有寫入；`pool_observations` 有資料；hosted 三個池的健康度不再是「未知」；寫入量登記到 storage manifest |
| 2 | `PAPER-POOL-DRAWDOWN-LADDER`，以及修改後的 `RISK-POLICY-BLUEPRINT-LIMITS-ENFORCED` | 下單前檢查依分級拒絕或縮量，並寫明原因；用真實部位重播證明減倉一定放行；三項限制都有資料傳入 |
| 3 | `PAPER-RISK-OFFICER-PERIODIC-REDRAFT`，以及演化指令的執行者 | 重擬時看得到快照資料；收緊直接生效；放寬受外框和 7 天冷卻限制；演化發出的收緊指令會被執行 |
| 4 | `PERSONA-RISK-SCORE-MEASURED`、`QUARTERLY-ALLOCATION-PERSONA-POOLS`、`CANARY-GRADUATION-STATS`、`CROSS-PERSONA-CONCENTRATION` | §3.4 |

每個任務都要附唯讀的驗收探針，並寫明執行期預算。

## 6. 待操作者決定

| # | 問題 | 建議 |
|---|---|---|
| Q1 | `halved` 什麼時候解除 | 淨值回到觸發前高點的 97.5% 以上。`reduce_only` 一律要重擬並經過閘門 |
| Q2 | 產業、週轉、流動性要不要設外框數字 | 先由風控長草擬，資金端只檢查有沒有填、是不是合理的數字；等快照累積 20 天後，再根據實際分布決定外框。參考值：Lean 產業上限 20%，業界常見下單量 ≤ 平均成交量的 10% |
| Q3 | 要不要做波動目標化；回撤分級要不要改用波動當單位 | 第 3 階段再做，因為要先有 20 天以上的快照；目標波動由風控長草擬，並設外框 |
| Q4 | O-21：池健康度要不要接進 `risk_budget_factor` | 接。利用率低就減少額度，但不能因此降低進池門檻（POOL SD R1） |
| Q5 | Risk Owner 是誰（canary／live 用） | 由操作者指定；在指定之前，canary／live 的風控變更一律交給操作者 |
| Q6 | 用語釐清：`SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md:43, 51` 寫「風險與資金上限都要你核准」，P-4 則寫 paper 內不需核准 | 兩者不衝突：把「上限」解讀為外框，外框由操作者訂；外框內的政策內容由風控長草擬，paper 由自動閘門處理。CYC SD 補一句說明 |
