# A3 — WidgetRegistry Catalog 與 ChartSpec Grammar 規格

> 狀態：Design Frozen v1.0  
> 阻擋解除：`AG-BE-DB-001`、`AG-FE-DB-001`  
> 原則：Agent 可生成 WidgetSpec／ChartSpec／DashboardRecipe，但只能使用 canonical registry、受控資料源與受控互動；不得生成任意前端程式碼。

---

## 1. 權威來源

首發版本的權威檔案：

```text
widget_registry.v1.json
widget_spec.schema.json
chart_spec.schema.json
```

後端 validator、前端 renderer、OpenClaw `agora-dashboard-compose` skill 必須使用同一 registry 版本與 schema checksum。

---

## 2. Registry Entry

```ts
type WidgetRegistryEntry = {
  widgetType: string;
  displayName: string;
  description: string;
  category: "generic" | "research" | "winner_branch" | "industry" | "technical" | "stat_arb" | "options" | "execution" | "performance";
  renderer: "builtin" | "chart_spec" | "plugin";
  allowedChartKinds: ChartKind[];
  allowedDataSources: string[];
  requiredFields: string[];
  optionalFields: string[];
  allowedTransforms: string[];
  allowedInteractions: WidgetInteractionKind[];
  sensitivity: "public_market" | "user_private" | "broker_sensitive" | "restricted";
  phase: "candidate_review" | "monitoring" | "position_monitoring" | "post_trade_review" | "any";
  minRole?: string;
};
```

---

## 3. ChartSpec v1 Grammar

### 3.1 Chart kinds

```text
metric
table
line
area
bar
stacked_bar
heatmap
scatter
network
timeline
sankey
candlestick
gauge
```

V1 不支援：

```text
arbitrary HTML
arbitrary JS expression
custom React component
iframe
remote script
external unapproved URL
```

### 3.2 Encodings

允許 channels：

```text
x
y
color
size
shape
opacity
row
column
label
source
target
value
open
high
low
close
volume
time
```

Field encoding：

```ts
type FieldEncoding = {
  field: string;
  type: "quantitative" | "temporal" | "nominal" | "ordinal";
  aggregate?: "sum" | "mean" | "median" | "min" | "max" | "count" | "distinct_count";
  scale?: "linear" | "log" | "symlog" | "time" | "band";
  format?: string;
  title?: string;
};
```

### 3.3 Allowed transforms

```text
filter
sort
top_k
aggregate
window
rolling_mean
rolling_sum
percent_change
rank
percentile
normalize
winsorize
zscore
bucket
time_bucket
join_by_key
```

所有 transform 是 declarative；禁止任意函式字串。

### 3.4 Interaction allowlist

```text
open_candidate
open_strategy
open_position
open_evidence
open_research_run
open_shadow_record
filter_workspace
cross_highlight
add_to_monitoring
remove_from_monitoring
park_candidate
request_more_research
send_to_shadow
request_widget_revision
create_journal_note
```

不允許：

```text
place_order
enable_live
change_capital_binding
invoke_broker
write_runtime_binding
open_management_route
```

---

## 4. Data Source Allowlist

首發 canonical data sources：

```text
agora.strategy.summary
agora.strategy.completeness
agora.research.run_summary
agora.research.evidence_refs
agora.candidate.members
agora.candidate.score_components
agora.monitoring.events
agora.trading.events
agora.positions.summary
agora.positions.history
agora.shadow.outcomes
agora.interventions.history
agora.dashboard.performance
market.ohlcv
market.relative_returns
market.liquidity
market.corporate_events
market.news_catalysts
winner_branch.branch_profitability
winner_branch.branch_flow_daily
winner_branch.identity_probability
winner_branch.related_branch_flow
winner_branch.event_lead
winner_branch.score_breakdown
industry.supply_chain
industry.peer_metrics
stats.cointegration
stats.regime
options.greeks
options.vol_surface
execution.slippage_capacity
```

每個 data source 需在 BFF capability manifest 定義：

```text
owner service
scope predicate
field catalog
freshness
PIT semantics
sensitivity
allowed aggregates
```

---

## 5. 首發 Widget Catalog

### 5.1 Generic / Cross-strategy

| widgetType | 目的 | 預設圖 |
|---|---|---|
| `strategy_status_summary` | 策略版本、狀態、完整度、研究與操盤狀態 | metric |
| `strategy_completeness_map` | 已確認、推定、缺漏、衝突 | table |
| `research_progress` | 研究／consult／backtest 進度 | timeline |
| `candidate_funnel` | new→discussion→monitoring→shadow→rejected | sankey |
| `candidate_ranking_table` | 候選排名與 score decomposition | table |
| `signal_decision_queue` | 接近進場／加碼／減碼／出場 | table |
| `position_action_queue` | 持倉待裁示事件 | table |
| `risk_invalidation_panel` | 風險、失效條件與觸發狀態 | table |
| `servant_assessment` | 僕人評斷、confidence、下一步 | builtin |
| `evidence_trace` | 證據來源、coverage、限制 | table |
| `backtest_summary` | 回測/OOS核心指標 | metric |
| `version_comparison` | 策略版本前後比較 | table |
| `shadow_scoreboard` | human/base/servant/committee 對照 | table |
| `performance_attribution` | Alpha、allocation、execution、intervention 歸因 | stacked_bar |

### 5.2 Winner Branch

| widgetType | 目的 | 預設圖 |
|---|---|---|
| `winner_branch_scoreboard` | 贏家分點綜合排名 | table |
| `branch_profitability_table` | 分點歷史 round-trip 獲利與樣本 | table |
| `branch_accumulation_heatmap` | 分點 × 日期買賣強度 | heatmap |
| `branch_flow_price_overlay` | 價格與累積分點流量 | line |
| `related_branch_network` | 關聯分點反向流／遷移 | network |
| `branch_migration_sankey` | 資金在分點間遷移 | sankey |
| `insider_branch_probability_graph` | 關係人—分點概率映射 | network |
| `event_lead_timeline` | 異常交易至公開事件的時間關係 | timeline |
| `event_lead_distribution` | lead days / outcome 分布 | bar |
| `winner_branch_score_breakdown` | Score components 與 penalties | bar |
| `expected_value_distribution` | 機率、payoff、EV、confidence | scatter |
| `position_pyramid_plan` | 初始部位、加碼、減碼、風險 | table |
| `confidence_decomposition` | identity/data/event/model confidence | gauge |

### 5.3 Industry / Peer

```text
supply_chain_graph
peer_laggard_scatter
relative_return_matrix
catalyst_timeline
peer_candidate_table
```

### 5.4 Technical / Event

```text
candlestick_setup
breakout_candidate_queue
volatility_regime
historical_pattern_match
event_calendar_timeline
```

### 5.5 Stat-arb / Options / Execution

```text
spread_zscore
cointegration_stability
regime_probability
volatility_surface
greeks_risk_grid
liquidity_capacity
slippage_impact
execution_schedule
```

---

## 6. Sensitivity Rules

| sensitivity | 允許範圍 |
|---|---|
| public_market | 可在 Agora 顯示與有限匯出 |
| user_private | 僅該 user scope；不可進 Management raw view |
| broker_sensitive | 僅經 redaction 的摘要；不可匯出原始帳務／憑證 |
| restricted | 僅 metadata／status；不可下載或展開 |

WidgetSpec sensitivity 不得低於 data source sensitivity。

---

## 7. Validator Rules

Validator 依序檢查：

1. `widgetType` 存在且 status active。
2. `chartSpec.kind` 在 entry allowlist。
3. `dataSource` 在 entry allowlist。
4. 所有 field 存在於 data source field catalog。
5. transforms 全在 allowlist。
6. interactions 全在 allowlist。
7. scope 含 `tenant_id + user_id`。
8. sensitivity 不降級。
9. query window、limit、join 數量不超資源上限。
10. 不含 raw prompt、other-user、Management-only、broker credential、live order action。
11. network/sankey node 數上限 500；table row 預設 200；time-series point 預設 10,000。
12. custom plugin 未註冊時拒絕。

Validation output：

```json
{
  "valid": false,
  "errors": [{"code":"FIELD_NOT_ALLOWED","path":"chartSpec.encodings.x.field","message":"..."}],
  "warnings": [],
  "registry_version": "widget_registry.v1",
  "schema_hash": "..."
}
```

---

## 8. Layout 與 Recipe

不同 Strategy Lens 可使用完全不同 `DashboardView.layoutTemplateId`。至少支持：

```text
candidate_intelligence_workspace
network_investigation_workspace
industry_comparison_workspace
time_series_execution_workspace
position_monitoring_workspace
post_trade_review_workspace
custom_grid_workspace
```

Agent 可提出完整 layout proposal；交易員可 drag、resize、remove、add、change chart。每次變更產生新 recipe version 與 change log。

---

## 9. 自訂新 Widget

若既有 registry 不足，Agent 只能產生 `WidgetPluginProposal`：

```text
問題
既有 widget 為何不足
所需資料源
建議視覺
互動
隱私與效能風險
示例
```

不得自行生成或部署 renderer。完整 pipeline 見 D1 文件。

---

## 10. Definition of Done

- registry catalog、WidgetSpec Schema、ChartSpec Schema 皆有 checksum 與版本。
- 前端／後端／OpenClaw skill 使用相同版本。
- 首發 catalog 至少涵蓋 generic、winner branch、industry、technical、stat-arb、options、execution。
- validator 可拒絕非法 field、source、interaction、sensitivity downgrade 與任意 code。
- 至少三個 Strategy Lens 可產生結構顯著不同的 DashboardRecipe。
- 交易員調整可版本化、回滾與 replay。
