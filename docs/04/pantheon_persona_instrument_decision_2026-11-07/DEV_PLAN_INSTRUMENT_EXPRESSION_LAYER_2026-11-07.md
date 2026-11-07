# DEV PLAN — backtest on the underlying, persona chooses the instrument in paper/live

Task: SD-INSTRUMENT-EXPRESSION-DEV-PLAN-20261107. Owner: Claude. Reviewer: Antigravity. Date: 2026-11-07.
Design it implements: `SD_PERSONA_INSTRUMENT_DECISION_2026-11-07.md` (same folder, merged in PR 7011). Earlier ledger/lifecycle material it reuses: `docs/04/pantheon_tw_paper_stock_futures_2026-11-06/SD_TW_PAPER_STOCK_FUTURES_2026-11-06.md` §2.1–2.2 (steps 1–2, 4–5), §3, §4, §5.1, §5.3–5.6, §6, §10.
Scope of this change: this document, a decision notice at the top of the design document, and `docs/deployment/evidence/SD-INSTRUMENT-EXPRESSION-DEV-PLAN-20261107/evidence.json`. No source, test, workflow or configuration change.

This document is the single specification the implementation tasks in §9 point at. Where it and the design document differ, this document wins, because it records the operator's decisions; where it is silent, the design document applies.

---

## 0. 中文摘要（操作者用）

**定調（2026-11-07）**：回測用原始商品（標的現股，1 股，與今天一樣）；實際操作（paper，之後 live）由 persona 在每一筆決策時從系統算出的候選商品中選一個（現股、股票期貨，或不做），商品不是 artifact 欄位、不是模板值、不是「方向→商品」的固定對照。

**四個決定**：

| # | 問題 | 決定 |
| --- | --- | --- |
| Q1 | 誰做商品決策 | persona 內建的**確定性表達政策**（設計文件 B 案）；參數可由演化調整；模型之後只能當輸入，不站在交易路徑上 |
| Q2 | 第一批商品 | 台股**現股**＋**上市股票期貨與 ETF 期貨**（QFF、DHF、SRF 等）＋**記錄的 no_trade**；選擇權、權證、反向 ETF、指數期貨不在第一批 |
| Q3 | 槓桿上限 | 每個 persona 一個**總名目倍數上限 2.0×**（相對於該 persona 資金池權益），寫在資金池風控政策的 `max_leverage` 與 `gross_limit`，不在 mutation surface 裡 |
| Q4 | 標的與方向是否也改交易時決策 | **之後**；表達層先上，不被它擋住 |

**架構一句話**：觀點層（標的、方向、強度）在回測與 paper 共用同一支解譯器；表達層（候選商品→政策→風控閘→保證金帳本）只在 paper／live 跑。表達政策的離線評估用「表達重播」，不是把期貨塞進回測。

**分階段**：階段 0 帳本不得賣成負數股（可立即做）；階段 1 候選商品、出口閘門、期貨帳本與風控閘；階段 2 強度、表達政策、決策事件；階段 3 持倉／歸因／前端／對帳／探針、回撤基準重訂、學習與反事實；階段 4 既有負數部位的治理切換與 hosted 驗收。任務清單見 §9；標的與方向改交易時決策、live 走 Shioaji 期貨帳戶屬下一階段，不在此文件開單。

---

## 1. Operator decisions (2026-11-07) and standing decisions

Operator statements, verbatim:

> 把我們最後討論的跑回測用原始商品，實際操作讓persona依照需求用衍伸性商品來做

> 好，按照你的建議，把所有要開發的內容完整寫成文件，並且接著寫成excution tasks 讓supervisor 跟auto worker把他們做完。

"你的建議" refers to the coordinator's recommendation of 2026-11-07, which the operator accepted in full:

| # | Question (design §0) | Decision |
| --- | --- | --- |
| Q1 | Who makes the instrument decision | **Option B**: a deterministic per-persona *expression policy* evaluated at each decision, parameterised in the strategy artifact, tuned through the existing `mutation_surface` / evolution path. No model call on the trade path. A model may later be *one input* whose output is stored under `decision_id`. |
| Q2 | First scope of instruments | **Cash shares + listed single-stock and ETF futures + the recorded no-trade.** Options, warrants, inverse ETFs and index futures are not in the first scope; each is added later as one more candidate generator (§4.2) without changing the decider contract. |
| Q3 | Leverage limit | **A per-persona gross-notional multiple, 2.0× of the persona's pool equity**, held in the pool's governed risk policy (`max_leverage = 2.0`, `gross_limit = 2.0`), never in the artifact's mutation surface. The operator can change the number per persona through the governed policy; this plan does not hardcode it in source. |
| Q4 | Underlying and direction at trade time | **Later.** The expression layer ships against today's one-symbol momentum view. The view-universe change (design §10) is a separate future plan. |

Standing decisions that are not reopened: Taiwan paper shorting stays and follows real market rules; securities margin short (融券) by whole lots is not the route; no live order, broker sandbox call, futures smoke, real capital, or model-provider call is part of any task in §9.

---

## 2. The contract between backtest and paper/live

### 2.1 Statement

* **Backtest validates the view, on the underlying, in shares.** `services/research/vectorbt/adapter/vectorbt_adapter.py::_artifact_order_sizes` already accepts only `quantity_type == "SHARES"` and evaluates the same interpreter the paper producer uses (`services.registry.strategy_artifact:evaluate_strategy_action`) over the underlying's closes. This is kept and declared as the contract: the canonical backtest answers "does the direction call have edge", not "how should it be expressed". It never prices a derivative.
* **Paper/live express the view.** The expression layer (candidates → policy → risk gate → margin ledger) runs only in the paper runtime (and later the live broker path). It takes the view as input and never changes it.
* **Offline evaluation of the expression layer is "expression replay", not backtest** (§6): recorded views and recorded candidate snapshots are replayed through a policy to see what it would have chosen and earned. It uses prices the system already holds (D−1 settlement prices from the official data path).
* **Evolution keeps grading in paper.** `services/evolution/threshold_sweep_worker.py` reads telemetry runtime summaries against `services/evolution/config/threshold_sweep_baselines.json`; the expression layer is therefore judged where it runs. The 1-share baselines must be re-derived per instrument class before any non-share decision ships (§7.2).

### 2.2 Why (reference designs)

* QuantConnect Lean's Algorithm Framework separates the Alpha model, which emits `Insight(symbol, period, type, direction, magnitude, confidence, source_model, weight)` on the underlying, from Portfolio Construction, Risk Management and Execution, which decide size and how the insight is expressed ([Alpha key concepts](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/alpha/key-concepts), [Portfolio Construction supported models](https://quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/portfolio-construction/supported-models)). Our view = Insight; our expression layer = the three downstream models.
* Freqtrade lets a strategy declare `can_short` and decide leverage *per trade* through a `leverage()` callback bounded by the exchange's `max_leverage`; backtesting calls the callback once per candle ([Freqtrade leverage docs](https://codeclimate.com/github/freqtrade/freqtrade/docs/leverage.md/source)). Our expression policy is that callback, with the instrument itself also chosen, and the bound coming from the governed risk policy.
* Nautilus Trader runs the same `Strategy`/`ExecAlgorithm` code across backtest, sandbox and live, with Cash and Margin accounts available in every environment ([overview](https://docs.nautilustrader.io/concepts/overview.html)). Our view interpreter and expression policy are likewise one code path; only the ledger behind them differs by environment.

### 2.3 Non-goals

No derivative pricing in the research backends; no automatic futures roll; no netting across instruments; no change to the artifact checksum of existing personas; no second fetch path for TAIFEX data; no new "instrument type" artifact field; no change to US or crypto paths.

---

## 3. Target architecture

```
                         backtest (research)            paper / live (execution)
                         ───────────────────            ─────────────────────────
A  View                  evaluate_strategy_action   ==  evaluate_strategy_action
   underlying,direction, (+ evaluate_strategy_view      (+ evaluate_strategy_view → strength)
   strength               → strength, not used for size)
                                  │                               │
                                  ▼                               ▼
                          1 share per signal          B  Candidates   instrument_candidates.py
                          (vectorbt, unchanged)          (ContractMaster reader, TAIFEX dataset)
                                                                  │
                                                      C  Expression policy  (artifact.expression_policy,
                                                         deterministic, mutation controls)
                                                                  │  chosen {instrument_id, side, units, reason_code}
                                                      D  Risk gate   RiskPolicyEvaluator(RUNTIME_ACTION)
                                                         max_leverage / gross_limit / net_limit /
                                                         allowed_asset_classes / initial margin
                                                                  │  accept | reject(reason) | clamp
                                                      E  Ledger     paper_runtime.py  (binding, underlying,
                                                         instrument_id); margin reserved; multiplier;
                                                         expiry settlement
                                                                  │
                                                      F  Consumers  telemetry → BFF → FE; drift; probes;
                                                         learning (counterfactual), baselines
```

| Layer | Owner module(s) today | Change | Runs in backtest? | Runs in paper/live? |
| --- | --- | --- | --- | --- |
| A View | `services/registry/strategy_artifact.py` (`evaluate_strategy_action`), `services/execution/lean_runtime/paper_signal_producer.py` (`BoundedPaperStrategy`) | add `evaluate_strategy_view` returning `{action, direction, strength, momentum}`; `evaluate_strategy_action` unchanged and still the backtest entry | yes (direction only; strength recorded, not used for size) | yes |
| B Candidates | none today; `services/data-plane/models/contract_master.py` has no reader; `services/source_ingestion/connectors/taiwan_official.py::TaifexDerivativesChipAdapter` exists | new `services/execution/lean_runtime/instrument_candidates.py`; new dataset `taifex_ssf_contracts` on connector `tw-taifex-futures-options-chip`; `ContractMaster` becomes its read model | no | yes |
| C Policy | none today | new `services/execution/lean_runtime/expression_policy.py`; artifact section `expression_policy`; controls in `mutation_surface` | no (a contract test asserts the backtest ignores it) | yes |
| D Risk gate | `services/capital/risk_policy.py` (`RiskPolicyEvaluator`, `RiskPolicyTargetType.RUNTIME_ACTION`), evaluated today only at bootstrap | call the evaluator per order inside the paper runtime before any ledger change | no | yes |
| E Ledger | `services/execution/lean_runtime/paper_runtime.py` (`SubmitTaiwanBrokerOrder`, `_commit_fill`), `services/broker/paper_simulation.py` | positions keyed by `(binding, underlying, instrument_id)`; margin, multiplier, expiry settlement; no negative share positions | no | yes |
| F Consumers | `services/telemetry/*`, `services/control-plane/bff/pm12`, `bff/agora/performance`, `bff/agora/decision_projection`, `services/reconciliation-drift`, `services/trade_journey/*`, `scripts/probe_dev_runtime_paper_lifecycle.py`, `execute-plans` PortfolioBook | instrument-aware PnL, holdings, drift identity, probe terminals; counterfactual rows | no | yes |

---

## 4. Data contracts

All numeric fields are decimal-safe floats; all identifiers are strings; every record carries `decision_id`.

### 4.1 View (layer A)

`evaluate_strategy_view(artifact, closes) -> {"action": BUY|SELL|HOLD, "direction": LONG|SHORT|FLAT, "momentum": float, "strength": float in [0,1] or None}`

* `action`/`direction` exactly as today (`evaluate_strategy_action`; `direction = SHORT if action == SELL else LONG`; HOLD → FLAT).
* `strength = min(1, |momentum| / (strength_scale_k × max(momentum_threshold, strength_floor_momentum)))`, computed with `Decimal` like the momentum itself. `strength_scale_k` (default 1.0, mutable control, range 0.25–4.0, step 0.05) and `strength_floor_momentum` (default 0.01, mutable control, range 0.002–0.05, step 0.001) live in `parameters` and are declared in `mutation_surface.controls` for artifacts that carry an `expression_policy`. The floor exists because the current template has `momentum_threshold = 0`.
* **Legacy artifacts** (no `expression_policy` section) return `strength = None`. Absent strength never enlarges a position (§4.4).
* Strength is a deterministic function of closes ≤ D−1, so backtest and paper compute the same value. The backtest records it in the run metadata and ignores it for sizing.

### 4.2 Candidate record (layer B)

Produced by `instrument_candidates.build_candidates(underlying, as_of_trading_day, persona_book, risk_budget) -> list[Candidate]` for the view's underlying before the policy runs.

```
Candidate = {
  instrument_id: "eq:2330" | "ssf:QFF:202611" | ...,   # stable; kind:code[:contract_month]
  kind: "cash_share" | "stock_future",
  underlying: "2330",                                  # normalised code (2330.TW and 2330.TWSE → 2330)
  side_capability: ["long"] | ["long","short"],
  unit: 1 | 100 | 1000 | 2000,                         # shares per unit (contract unit for futures)
  multiplier: 1 | unit,                                 # PnL per 1.00 price move per unit
  price: float, price_date: "YYYY-MM-DD", price_basis: "stock_close_prev_session" | "futures_daily_settlement_prev_session",
  initial_margin_per_unit: float, maintenance_margin_per_unit: 0 | float,   # cash_share: initial = full payment (unit × price), maintenance 0; stock_future: exchange margin per contract
  fee_per_unit_per_side: float, tax_rate_per_side: float,   # from the reviewed constants file
  expiry: null | "YYYY-MM-DD", last_trading_day: null | "YYYY-MM-DD",
  liquidity: {volume: int, open_interest: int} | null,
  max_units_by_risk: int,                               # §4.5, computed by the system
  source_refs: [...]                                    # dataset partition + Date of every input
}
```

Generation rules (kept from the earlier design §2.2 steps 1–2, 4–5): normalise the underlying; candidates are rows of `SingleStockFuturesMargining ∪ SingleStockFuturesETFMargining` with `UnderlyingSecurityCode == code` joined to `stockLists` on contract family for the unit; contract code must match `^[A-Z]{2}F$`; contract month must match `^\d{6}$` and be the nearest month whose last trading day is strictly after D; adjusted codes and spread rows are never candidates. A candidate whose price is older than D−1, whose margin row is missing, or whose regular-session volume and open interest are both 0 is **not emitted**. The cash-share candidate is always emitted for a listed underlying (long only), priced at the D−1 stock close the signal context already carries. Step 3 of the earlier design (choose the smallest unit) is **not** part of generation: the policy chooses.

Adding an instrument later = one more generator returning the same record; the policy, gate, ledger and events do not change.

### 4.3 Chosen record and events (layer C)

```
Chosen = { decision_id, instrument_id, side: "buy"|"sell", units: int ≥ 1,
           reason_code, reason_text, strength, target_notional, candidates_considered: [instrument_id...],
           policy_version, policy_parameters_digest }
NoTrade = { decision_id, reason_code: "no_feasible_instrument" | "flat_view" | "already_at_target" | "candidates_unavailable",
            detail, candidates_considered }
```

`reason_code` is a closed vocabulary: `long_cash`, `long_lever`, `short_via_future`, `close_leg`, `no_feasible_instrument`, `flat_view`, `already_at_target`, `candidates_unavailable`, `instrument_not_in_candidates` (gate rejection of a malformed decision).

**Events** (reuse first, add only what does not exist):

| Step | Event (telemetry `event_type`) | Change |
| --- | --- | --- |
| View | existing `signal_generation` | metadata gains `strength`, `momentum`, `view_direction` |
| Instrument chosen | existing `trade_decision` (already derived in `paper_runtime.py` from the signal event) | metadata gains the Chosen record |
| No trade | existing `paper_order_simulated` (noop path, `noop_count: 1`) | metadata gains the NoTrade record; `decision_id` is consumed so a restart does not retry the bar |
| Gate rejection | existing `order_rejection` | metadata gains `reason`, `required`, `available`, `limit`, `instrument_id`, `decision_id` |
| Fill | existing `paper_fill_simulated` | metadata gains `instrument_id`, `kind`, `multiplier`, `contract_month`, `fee`, `tax`, `initial_margin_reserved` |
| Expiry settlement | **new** `paper_futures_settled` | schema enum addition |
| Maintenance breach | **new** `paper_margin_call`, `paper_forced_liquidation` | schema enum additions |

The trade-journey lifecycle chain stays `signal_generation, trade_decision, [risk_evaluation, order_submitted, order_accepted, paper_fill_simulated, position_snapshot]*, reconciliation_*`; a no-trade or a rejection terminates the chain at `trade_decision` → `paper_order_simulated` or `risk_evaluation` → `order_rejection`, which `services/trade_journey/lifecycle_projector.py` already classifies as terminal. Probes must accept those terminals (§9 T1, T6).

**Restart identity.** The Chosen/NoTrade record is persisted in the existing pending signal store under `decision_id` **before** any order is placed and is reused on restart; the existing restart test (`test_producer_restart_does_not_duplicate_a_completed_decision`) is extended to the instrument decision.

### 4.4 Expression policy v1 (layer C, deterministic)

Artifact section (new, optional; absent = legacy cash-only behaviour):

```
"expression_policy": {
  "version": "1.0",
  "kind": "notional_target_v1",
  "parameters": {
    "lever_min_strength": 0.5,          # below this, target notional is capped at one cash share for LONG
    "notional_fraction_at_full_strength": 1.0,   # of the risk budget's max gross notional
    "allow_pyramiding": false,
    "close_before_reverse": true
  }
}
```

Controls added to `mutation_surface.controls` when the section is present: `strength_scale_k`, `strength_floor_momentum`, `lever_min_strength` (0.0–1.0, step 0.05), `notional_fraction_at_full_strength` (0.1–1.0, step 0.05). **Never** in the mutation surface: `max_leverage`, `gross_limit`, `net_limit`, `allowed_asset_classes`, `allow_pyramiding`, `close_before_reverse`. No parameter may encode a direction→instrument or symbol→instrument mapping; the validator rejects a parameter key matching `prefer_.*_when_.*`.

Algorithm `decide(view, candidates, book, budget, parameters) -> Chosen | NoTrade`:

1. `direction == FLAT` → `NoTrade(flat_view)`.
2. If `book` holds an open position on the underlying in the **opposite** direction and `close_before_reverse` → return the closing leg of that same instrument (`Chosen(close_leg)`, units = open units). The reversal itself happens on the next bar's decision. If the open position is in the **same** direction and `allow_pyramiding` is false → `NoTrade(already_at_target)`.
3. Feasible set `F` = candidates whose `side_capability` includes the direction and `max_units_by_risk ≥ 1`. Empty → `NoTrade(no_feasible_instrument, detail = the binding constraint of the best candidate)`.
4. Target notional `N*`:
   * `strength is None` → `N*` = the smallest `unit × price` in `F` (one unit of the cheapest feasible instrument; never an enlarged position).
   * `direction == LONG and strength < lever_min_strength` → `N*` = one cash share's notional if `eq:` is feasible, else the smallest unit notional in `F`.
   * otherwise `N* = strength × notional_fraction_at_full_strength × budget.max_gross_notional_remaining`.
5. For each `c ∈ F`: `units = clamp(round(N* / (c.unit × c.price)), 1, c.max_units_by_risk)`; `score = |units × c.unit × c.price − N*| + units × (c.fee_per_unit_per_side × 2 + c.unit × c.price × c.tax_rate_per_side × 2)`; choose the minimum score; ties → smaller `unit × price`, then lexical `instrument_id`.
6. `reason_code`: `eq:` chosen → `long_cash`; future chosen for LONG → `long_lever`; future chosen for SHORT → `short_via_future`.

Properties: pure function of `(view, candidates, book, budget, parameters)`; same inputs → same output; no price, contract, margin or quantity invented; the output `instrument_id` must be in `candidates` or the gate rejects with `instrument_not_in_candidates`.

Worked outcomes with `Date=20261106` data, equity 100,000, budget 2.0× (max gross notional 200,000), legacy template parameters otherwise:

| Case | View | F | Outcome |
| --- | --- | --- | --- |
| 2330 SHORT, strength 0.6 | `eq:2330` long-only (excluded), `ssf:QFF:202611` 2,133 × 100 = 213,300 per unit | QFF `max_units_by_risk` = 0 (213,300 > 200,000) | `NoTrade(no_feasible_instrument, gross_limit)` — the 2.0× limit binds exactly as the operator chose; a 3.0× limit would give 1 QFF |
| 0050 SHORT, strength 0.6 | `ssf:SRF:202611` 92.5 × 1,000 = 92,500 per unit, margin 8,700 | `max_units` = 2 (185,000 ≤ 200,000) | `N* = 0.6 × 200,000 = 120,000` → units = round(1.30) = 1 → `Chosen(SRF, sell, 1, short_via_future)`, margin 8,700 |
| 2317 SHORT, strength 0.3 | `ssf:DHF:202611` 225 × 2,000 = 450,000 | `max_units` = 0 | `NoTrade(no_feasible_instrument, gross_limit)` |
| 2330 LONG, strength 0.2 | `eq:2330` 2,135 × 1 | below `lever_min_strength` | `Chosen(eq:2330, buy, 1, long_cash)` |
| 0050 LONG, strength 0.9 | `eq:0050` 92.5; `ssf:SRF:202611` | `N* = 180,000`; eq `max_units_by_risk` = 1,081 (100,000 cash ÷ 92.5, full payment) so eq units = clamp(1946, 1, 1081) = 1,081 shares (≈ 100,000, diff ≈ 80,000); SRF units = 2 (185,000, diff 5,000, margin 17,400) | SRF wins on score (diff 5,000 + costs versus ≈ 80,000 + costs) — the 2.0× limit does not let cash shares exceed cash; the choice is a scored outcome, not a mapping |

The replay table of the design document §8 (E1–E3) remains the loss reference per instrument.

### 4.5 Risk gate (layer D)

`max_units_by_risk(c)` = largest integer `n` with all of: `n × c.initial_margin_per_unit ≤ equity − margin_reserved` (for a cash share this is the full purchase price, so cash shares are never bought with borrowed cash; the 2.0× multiple applies to futures notional only through the margin test and the gross/leverage tests); `gross_after(n) ≤ max_leverage × equity`; `gross_after(n) ≤ gross_limit × equity`; `|net_after(n)| ≤ net_limit × equity` where set; `c.kind`'s asset class ∈ `allowed_asset_classes` and ∉ `forbidden_asset_classes`. Asset classes: `cash_share → "equity"`, `stock_future → "future"`.

**Order-level gate**: inside `SubmitTaiwanBrokerOrder` before any ledger change, build a `RiskPolicyEvaluationContext` with `target_type = runtime_action`, `gross_exposure`, `net_exposure`, `leverage` (all as multiples of equity *after* the order, marked at the current D−1 price), `asset_classes = [class of the order]`, and call `RiskPolicyEvaluator().evaluate(policy, context)`; `REJECTED` → `order_rejection` with `reason ∈ {leverage_limit, gross_limit, net_limit, asset_class_not_allowed, insufficient_initial_margin, price_stale, instrument_not_in_candidates, sell_without_holdings}`. The policy is the persona pool's governed policy read through the existing capital reader; no limit is hardcoded. Today the paper path labels `risk_status=succeeded` after the fill (`paper_runtime.py` ~L3443); that label moves to the gate result.

For the pool policy to allow futures, the persona pool's `allowed_asset_classes` must contain `"future"` and `max_leverage`/`gross_limit` must be set (2.0 per Q3). New personas get that from the provisioning coordinator (T4a); existing personas keep `["equity"]` and therefore can only ever choose cash shares until re-provisioned (T9).

### 4.6 Ledger (layer E)

Position identity `(persona_binding_id, underlying, instrument_id)`; `2330.TW` and `2330.TWSE` normalise to `2330`. Formulas, margin definitions, maintenance breach, costs, expiry settlement and price timing are **kept verbatim from the earlier design** §3, §4, §5.1, §5.3–5.6, §6, with these substitutions: "selected contract" → "chosen `instrument_id`"; §5.2 same-direction no-op → policy outcome (`already_at_target` or pyramiding per §4.4); §5.4 pre-trade check → the gate in §4.5. No netting across instruments; exposure per underlying is reported both per instrument and as net delta in shares (`Σ q × multiplier`).

**Cash shares**: BUY buys shares at the D−1 close as today; SELL with `holdings ≤ 0` → `order_rejection(sell_without_holdings)` (no fill, no cash change, never a negative share row); SELL above holdings fills the held amount and emits `paper_order_partial_clamped` metadata on the fill. This is T1 and ships before anything else, flag-free.

**Switch**: environment flag `PANTHEON_PAPER_EXPRESSION_LAYER` (`off` default until T9) read by the paper signal producer and the paper runtime; `off` = legacy path after T1. Per persona, only artifacts carrying `expression_policy` run layer C; legacy artifacts take the cash-only path even when the flag is `on`.

---

## 5. Data path for candidates (layer B inputs)

Kept from the earlier design §10, one path only:

* Connector `tw-taifex-futures-options-chip` (`services/source_ingestion/connector_definitions.py`) gains dataset `taifex_ssf_contracts`: `SingleStockFuturesMargining`, `SingleStockFuturesETFMargining`, `DailyMarketReportFut` filtered to contracts in the margin tables, `FinalSettlementPriceSSF`, plus the `stockLists` unit table; CSV-with-BOM responses decoded `utf-8-sig`.
* `SourceUpdateRule` in `services/source_ingestion/active_universe.py` alongside `taifex_futures_chip`: `raw/taifex/ssf/date=YYYY-MM-DD/`, `normalized/taifex_ssf_contracts`, retention 2555 days, `symbol_scope` = underlying codes of active Taiwan paper personas, cadence `daily_after_taifex_publication`.
* Reader: `ContractMaster` (`services/data-plane/models/contract_master.py`) populated from `normalized/taifex_ssf_contracts` (`contract_id = ssf:<code>:<month>`, `underlying_id = <code>`, `venue = TAIFEX`, `multiplier = unit`, `margin_type = fixed|portfolio` per row, `metadata_json` carries the margin rates/amounts, settlement price and date). Extend the model if a field is missing; do not add a second model.
* Fees and tax: a reviewed constants module `services/execution/lean_runtime/taifex_fee_schedule.py` with the earlier design §6 values and a drift test against captured page text; the openapi fee row is not read at run time (its STF/STO tax columns are swapped relative to the page).
* Dev egress: `scripts/deploy_nonprod_vm.sh::validate_source_refresh_profile` and `.github/workflows/nonprod-deploy.yml` gain a second reviewed connector entry requiring exactly `openapi.taifex.com.tw,www.taifex.com.tw`; `dev-tw-market-refresh.yml` dispatches the new dataset after the TWSE refresh. No wildcard, no widening of the TWSE entry.
* Fail closed: newest `Date` older than D−1 → the futures candidates are not emitted (cash share still is), and the policy decides on what remains; the fill price for a chosen future is always the D−1 regular-session `SettlementPrice`.

---

## 6. Expression replay (offline evaluation of layer C)

`services/execution/lean_runtime/expression_replay.py` (T8): input = a sequence of `(decision_id, view, candidates, book_before, budget)` records as recorded by the paper producer (or rebuilt from stored D−1 settlement prices for a date range) plus a policy parameter set; output = the Chosen/NoTrade per decision, the resulting positions, margin use, PnL path at recorded marks, and per-decision `counterfactual_pnl` for every non-chosen candidate. It is deterministic and uses no network. It is how a mutated `expression_policy` is evaluated before evolution proposes it, and how the operator compares two parameter sets. It is **not** a backtest of the view; it never recomputes direction.

---

## 7. Learning and baselines

### 7.1 Grading

* **Direction call**: graded against the underlying's next close (sign agreement), independent of the instrument. Same metric in backtest and paper.
* **Instrument choice**: `instrument_regret = max(pnl_cf over candidates_considered) − pnl_chosen` and margin use. `pnl_cf = side × q_cf × multiplier × (mark − entry) − costs`, `q_cf` from the same policy rule applied to that candidate. Stored as `persona_instrument_counterfactual` rows (telemetry) keyed `(decision_id, instrument_id)`, computed from prices the system already holds.
* Attribution (`bff/agora/performance/attribution.py`) shows the two metrics separately so a good call with a bad instrument is not scored as a bad call.

### 7.2 Drawdown baselines

`threshold_sweep_baselines.json` holds 7 approved `expected_drawdown` values for 1-share PnL (e.g. 0.0027 for 2330). A non-share decision moves per-signal PnL by ×100 (QFF), ×1,000 (SRF), ×2,000 (DHF). **Before the flag is turned on for any persona (T9), that persona's artifact must have a baseline approved per instrument class** (T7, governed review with evidence, modelled on `PAPER-PERSONA-BASELINES-20261105`). Until then the sweep fails closed (diagnostic only) per that file's `_schema_note`.

---

## 8. Cutover and rollback

* Open stock short positions (2026-11-06 11:25Z: 2330.TWSE −6, 0050.TWSE −6, 2317.TWSE −4, two 2330.TW −1 rows) have no real equivalent and are **not rewritten**. The governed path (`_retire_persona` with authoritative archive readback, then provisioning through the same coordinator) retires those personas and provisions successors whose artifacts carry `expression_policy` and whose pool policy allows `future` with the 2.0× limits. History of the old artifact ids stays read-only in telemetry.
* Rollback = `PANTHEON_PAPER_EXPRESSION_LAYER=off` (cash-only path after T1) or provisioning a successor without `expression_policy`. No store is hand-edited.
* Fleet baseline counts change by the number of successor artifacts; the hosted acceptance task records the resulting count.

---

## 9. Implementation tasks

All tasks target `ajoe734/pantheon` unless marked `execute-plans`. Full acceptance text and artifact contracts are in the live task store; the table gives the scope and order. Owners alternate between the Claude and Antigravity lanes so each task is reviewed by the other lane. Tasks marked Human/Ops are operator acceptance tasks and are not dispatched to workers.

| # | Task id | Owner → Reviewer | Depends on | Scope | Done when |
| --- | --- | --- | --- | --- | --- |
| D | `SD-INSTRUMENT-EXPRESSION-DEV-PLAN-20261107` | Claude → Antigravity | — | this document, decision notice on the design, evidence | merged |
| T1 | `PAPER-LEDGER-NO-NEGATIVE-SHARES-20261107` | Antigravity → Claude | — | `SubmitTaiwanBrokerOrder`: SELL with no holdings → `order_rejection(sell_without_holdings)`; SELL above holdings clamps; dev and hosted lifecycle probes accept the rejection as a terminal so a release whose first natural signal is SELL still passes the deployment gate | ledger tests + probe tests; no negative share row possible |
| T2 | `TAIFEX-SSF-CONTRACT-CANDIDATES-20261107` | Claude → Antigravity | D | §5 dataset + rule, `ContractMaster` reader, `taifex_fee_schedule.py`, `instrument_candidates.py` with captured `Date=20261106` fixtures | candidate lists for 2330/2317/0050 reproduced; stale/missing/zero-liquidity rows excluded; cash-share candidate always present |
| T3 | `DEV-EGRESS-TAIFEX-CONNECTOR-GATE-20261107` | Antigravity2 → Claude | T2 | deploy script + workflows: second reviewed connector entry, refresh dispatch | exact-host validation tests; TWSE entry unchanged |
| T4a | `STRATEGY-ARTIFACT-VIEW-STRENGTH-POLICY-20261107` | Antigravity → Claude | D | registry: `evaluate_strategy_view`, `expression_policy` section + controls validation + forbidden-key rule, schema; provisioning coordinator writes the section, controls, `asset_classes ["equity","future"]` and pool policy `max_leverage/gross_limit 2.0` for new paper personas; vectorbt contract test (SHARES-only, section ignored) | existing artifact checksums unchanged; legacy artifacts validate as before |
| T4b | `PAPER-EXPRESSION-DECISION-STEP-20261107` | Claude → Antigravity | T2, T4a | `expression_policy.py` (§4.4), producer step after the view, decision envelope `instrument_id`/`units`/`reason_code`, event metadata (§4.3), restart identity, `PANTHEON_PAPER_EXPRESSION_LAYER` flag | §4.4 worked outcomes reproduced as tests; restart test extended; flag off = legacy |
| T5 | `PAPER-FUTURES-LEDGER-RISK-GATE-20261107` | Antigravity2 → Claude | T1, T2, T4b | ledger §4.6, gate §4.5 through `RiskPolicyEvaluator`, new event types, settlement across expiry, margin call, costs | earlier design §5.6 numbers and design §8 replay table reproduced; `equity − initial_cash == total_pnl` asserted; rejection events carry required/available/limit |
| T6a | `INSTRUMENT-AWARE-TELEMETRY-BFF-DRIFT-20261107` | Claude → Antigravity | T5 | telemetry fold/episodes with multiplier and instrument grouping; BFF pm12 holdings and agora performance value `q × multiplier × price`, show instrument and contract month; decision projection `subject.asset_class` from the chosen kind; drift identity + rejected/no-trade not unreconciled; hosted probes accept terminals; lean base accepts fields | real fill shapes from §4.3; no synthetic-only tests |
| T6b | `FE-HOLDINGS-INSTRUMENT-COLUMNS-20261107` (`execute-plans`) | Antigravity → Claude | T6a | PortfolioBook: instrument, contract month, multiplier, margin reserved, reason on the decision row; "—" until BFF ships | component tests + i18n parity; verified in a real browser by the acceptance task |
| T7 | `PAPER-DRAWDOWN-REBASELINE-INSTRUMENT-CLASS-20261107` | Antigravity2 → Claude | T5 | governed baselines per artifact and instrument class using expression replay on the real sequences; `policy.md` + inputs | approved `expected_drawdown` per successor artifact; sweep stays fail-closed for artifacts without one |
| T8 | `INSTRUMENT-DECISION-LEARNING-COUNTERFACTUAL-20261107` | Claude → Antigravity | T6a | `expression_replay.py` (§6), counterfactual rows, attribution split (§7.1), evolution child checks for the new controls | replay deterministic; regret shown separately from direction hit rate |
| T9 | `PAPER-PERSONA-INSTRUMENT-CUTOVER-20261107` | Human/Ops → Claude | T5, T6a, T7 | governed retire + re-provision of the personas with negative share history; flag on | operator-run; history intact; baseline count recorded |
| T10 | `INSTRUMENT-EXPRESSION-HOSTED-ACCEPTANCE-20261107` | Human/Ops → Claude | T1–T9 | one hosted run: decision → shares or contract → mark → settle across an expiry; rejection on the 2.0× limit; stale-price fail-closed; holdings and attribution in a real browser | hosted evidence; nothing earlier claims hosted behaviour |

Verified against the live task store 2026-11-07: all 13 ids exist with the owners and reviewers shown. The store carries additional serialization dependencies that this table omits and that govern dispatch order: T4a also depends on `BFF-EVO-ALPHA-PAGE-TRUTH-20261107`; T6a on `BFF-EVO-ALPHA-PAGE-TRUTH-20261107`; T6b also on `FE-STRATEGY-OPERATIONAL-VIEW-20261107`, `FE-RISK-CENTER-ARCHIVED-POOLS-20261107`, `FE-EVO-ALPHA-PAGE-LABELS-20261107`; T8 on `BFF-EVO-ALPHA-PAGE-TRUTH-20261107`; T10 lists every task above it. The store is authoritative for dependencies; the table above is the functional order.

Not opened by this plan (next stage, needs its own plan): view universe / trade-time underlying and direction (Q4), live Shioaji futures account routing, options / warrants / inverse-ETF candidate generators.

---

## 10. Risks and unverified items

1. **Deployment gate**: the dev release lifecycle probe (`scripts/probe_dev_runtime_paper_lifecycle.py` ~L1295) waits for a `paper_fill_simulated` after the stimulus; after T1 a release persona whose first natural signal is SELL produces `order_rejection` instead. T1 must make the probe accept that terminal or the next Root deploy fails. This is the first thing the T1 reviewer checks.
2. **Hosted all-12 harness** assumes `SHARES` and fills in places listed in design §12; T6a and T10 settle the list.
3. **Baselines**: shipping leverage before T7 makes every adverse day a breach; the flag stays off until T7 and T9.
4. **Shared files**: `paper_runtime.py` is touched by T1 and T5, `telemetry_event.schema.json` by T5 and T6a, `persona_provisioning_coordinator.py` by T4a only; the dependency order prevents parallel edits of the same file.
5. Unverified external items carry over from design §15 and the earlier design §14 (publication time of `DailyMarketReportFut`, holiday shift of the last trading day, 交割手續費 at cash settlement, broker commission, margin-call timing, adjusted contracts, odd-lot session rules). Each task that depends on one records it as unverified rather than guessing.
6. The §4.4 LONG/high-strength choice between shares and contracts is a score outcome; the test in T4b pins it once the constants are reviewed in T2. (Owner correction 2026-11-07: the draft let cash shares reach 2.0× of equity; cash shares are now bounded by cash through `initial_margin_per_unit`, §4.2/§4.5.)

## 11. Sources

Repository state read from `origin/dev` 63d124fcd (every path and symbol named in this document re-verified at that base) on 2026-11-07. External references: QuantConnect Lean docs (Alpha key concepts; Portfolio Construction supported models), Freqtrade leverage documentation, Nautilus Trader overview, all read 2026-11-07. Official exchange data as cited in the two design documents (TAIFEX openapi `Date=20261106`, TWSE `STOCK_DAY_ALL` `Date=1151106`).
