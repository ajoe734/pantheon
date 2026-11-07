# SD-PERSONA-INSTRUMENT-DECISION-20261107 — the persona chooses the instrument per decision

Status: design for review (Reviewer: Antigravity). Owner: Claude. Date: 2026-11-07.
Scope of this change: this document, a status notice at the top of `docs/04/pantheon_tw_paper_stock_futures_2026-11-06/SD_TW_PAPER_STOCK_FUTURES_2026-11-06.md`, and `docs/deployment/evidence/SD-PERSONA-INSTRUMENT-DECISION-20261107/evidence.json`. No source, test, workflow or configuration change. No implementation task is created by this work.

> **Decision notice (2026-11-07).** The operator decided the four open questions of §0: Q1 = option B (deterministic per-persona expression policy), Q2 = cash shares + listed stock/ETF futures + recorded no-trade, Q3 = a 2.0× gross-notional multiple per persona held in the pool risk policy, Q4 = later. The implementation specification and task list are in `DEV_PLAN_INSTRUMENT_EXPRESSION_LAYER_2026-11-07.md` in this folder; where the two differ, the dev plan wins. §14 of this document is superseded by the dev plan §9.

**Nothing in this document is decided.** Every "recommendation" is a proposal for the operator. The four open questions in §0 are the operator's.

## 0. Operator statement, scope, open questions

Operator statement, 2026-11-07, verbatim:

> 不是，我的意思是交易的persona應該決定好標的買賣方向後，不一定要用現股，可以用其他衍生性商品來做，除了可以擴大部位以外，還可以突破現股的做空限制。這應該是另一個決策方式，而不是設定好的東西

Reading used here: the persona forms a view (underlying, direction); *then*, per decision, an instrument is chosen (cash shares, a futures contract, an option, …). The instrument is a decision outcome, not a field in the strategy artifact and not a provisioning template value. Enlarging the position (leverage) is a stated goal, and short selling is expressed through whichever instrument can express it.

**Standing earlier decisions** (not reopened): Taiwan paper shorting stays; it follows real market rules; securities margin short (融券) by whole lots is not the route; leverage is a goal but needs a limit.

**Out of scope, stated explicitly:** no live order, no broker sandbox call, no futures smoke, no real capital, no model-provider call, no hosted workflow. Everything below is paper simulation inside the existing paper runtime plus public HTTPS reads of exchange sites.

**Open for the operator after this document (not decided here):**

| # | Question | Where compared | This document's recommendation |
| --- | --- | --- | --- |
| Q1 | Who or what makes the instrument decision | §4 | per-persona decision policy (option B), model advice optional later |
| Q2 | First scope of instruments | §6 | cash shares + listed stock futures (+ recorded no-trade) |
| Q3 | The leverage limit | §8 | a gross-notional multiple per persona, proposed starting value 2× of capital, operator to set |
| Q4 | Whether underlying and direction also become trade-time decisions | §10 | yes eventually; instrument decision can ship first |

The earlier question "does the long side move to futures" no longer exists as a global choice: the persona chooses per decision (§11 row 9).

## 1. Current state (verified against `origin/dev` d77e03a05, 2026-11-07)

| Element | Produced where | Trade-time decision or preset? | Immutable under the artifact mutation surface? |
| --- | --- | --- | --- |
| Underlying (symbol) | `services/control-plane/bff/persona_provisioning_coordinator.py` `_strategy_artifact_payload` L2111, `symbols` from `_paper_universe` L1739 (the provisioning request); `market_scope.symbols` | **Preset at provisioning** | **Yes** (`immutable_parameters` includes `symbols`, L2182–2190) |
| Direction | `evaluate_strategy_action` (`services/registry/strategy_artifact.py`) via `paper_signal_producer.py` L255–261 / L377; `close_to_close_momentum`, `positive_action=BUY`, `non_positive_action=SELL`, `lookback_bars=2`, `momentum_threshold=0.0` (L2148–2162) | **Trade-time computation** from closes, but the *rule* is a provisioned template; L418 `direction = "SHORT" if action == "SELL" else "LONG"` | The rule's two numbers are mutable (`lookback_bars` 2–60, `momentum_threshold` 0–0.05, L2165–2180); the action names (`positive_action`, `non_positive_action`, `zero_momentum_action`) are immutable |
| Instrument | Not a field anywhere. `market_scope.asset_classes: ["equity"]` hardcoded L1859; `paper_runtime.py` L443 `"sec_type": "equity"` hardcoded; `agora/decision_projection/producer.py` L175 `"asset_class": "equity"` hardcoded | **Neither**: there is no instrument concept; everything is cash equity | n/a |
| Quantity | `order_quantity: 1`, `quantity_type: "SHARES"` (L2160–2161, `execution_profile` L1865) | **Preset at provisioning**; no strength input exists, the action carries only BUY/SELL | **Yes** (`order_quantity`, `quantity_type` immutable) |
| Fill / ledger | `paper_runtime.py` `SubmitTaiwanBrokerOrder` (~L414–580): `next_quantity = current + signed_fill`, hardcodes equity, `initial_cash` defaults 100000 (L289) | Runtime, no holdings check | n/a |

Further facts checked:

* One decision per bar: `decision_key` = binding, artifact registry id/version/checksum, symbol, bar (`paper_signal_producer.py` L422–432) hashed to `decision_id` / `signal_id`. Test `test_producer_restart_does_not_duplicate_a_completed_decision` (`test_paper_signal_producer.py` L1837). Any decider must keep this identity.
* No model call anywhere in the trade path (`grep -rIl "anthropic\|openai" services/execution/lean_runtime --include=*.py` excluding tests finds none). `services/control-plane/persona/main.py` `POST /invoke` (L381) and the `execution.signal` intent (L129) exist but are not called from `paper_signal_producer.py`.
* Risk policy is **not** evaluated per paper order. `services/capital/risk_policy.py` defines `max_leverage` (L91), `gross_limit`, `net_limit`, `allowed_asset_classes` (L99), `forbidden_asset_classes` (L100); `capital_guard`/`services/capital/main.py` use it for capital actions; the optimizer synthesizer (`portfolio_synthesis/synthesizer.py`) evaluates it for allocation proposals; `services/execution/lean_runtime/bootstrap_contract.py` L496–508 requires only evidence that a pool policy evaluation was allowed at **runtime bootstrap**. No code path found by `grep` evaluates it on an individual paper order. The order-level gate in §8 is therefore new, not a rewiring.
* `services/data-plane/models/contract_master.py` `ContractMaster` (expiry, multiplier, margin_type) has no production reader (grep finds only `generate_schemas.py` and tests).
* Brief path correction: the decision-projection producer is `services/control-plane/bff/agora/decision_projection/producer.py` (not under `agora/` at the repository root).

**Consequence.** Today the persona decides nothing about underlying, instrument or size at trade time. Only the direction is computed at trade time, by a template rule. The operator's request cannot be met by changing a default; it needs a decision step that does not exist.

## 2. Decision chain

Four separated steps. Each step has one input, one output, and one recorded event. The instrument is chosen in step 2, per decision; it is not an artifact field, a provisioning value, or a mapping from direction or symbol.

| Step | Input | Output | Recorded event |
| --- | --- | --- | --- |
| 1 View | closes through D−1 (existing market input), artifact parameters | `view = {underlying, direction ∈ {LONG, SHORT, FLAT}, strength ∈ [0,1] or absent, reason}` | `persona_view_formed` (new; today's `decision_id`/`signal_id` become its identity) |
| 2 Instrument decision | the view, the **candidate list** computed by the system (§7), current position book of the persona, risk budget | `chosen = {instrument_id, side, quantity, reason_code, reason_text}` selected **from** the candidates, or `no_trade` with a reason | `persona_instrument_chosen` / `persona_no_trade` (new) |
| 3 Risk gate | chosen instrument, quantity, persona risk budget (`max_leverage`, `gross_limit`, `net_limit`, `allowed_asset_classes`), available margin | `accept` / `reject(reason)` / `clamp(quantity)` | `paper_order_accepted` / `paper_order_rejected` (reason, required, available) |
| 4 Ledger | accepted order, fill price from the official price path (§7) | fill, position per underlying and instrument, cash, margin reserved | `paper_fill_simulated` (extended with `instrument_id`, `multiplier`, `contract_month`) |

The same `decision_id` threads all four events, so one bar still yields one decision and a restart replays to the same outcome (§4). Counter-checks that the instrument is per decision: for one persona and one underlying, step 2 may return shares on Monday and a futures contract on Tuesday; the strategy artifact bytes and checksum do not change between them. No mapping `direction → instrument` or `symbol → instrument` exists in any step.

## 3. Decision inputs

Every fact given to step 2, with source and publication time. All must be public at decision time (first runtime tick of D, ≥ 08:45 Taiwan); an input that is not available fails closed to `no_trade`.

| Fact | Source | Published | Available at decision on D? |
| --- | --- | --- | --- |
| View (underlying, direction) | step 1, closes ≤ D−1 | stock close published D−1 ~14:30; `STOCK_DAY_ALL` read 2026-11-07 returned `Date=1151106` | yes |
| Strength / conviction | step 1 (see below) | computed | yes |
| Shares ledger: holdings, cash | paper ledger | continuous | yes |
| Contract candidates, units, margin rates | `openapi.taifex.com.tw` `SingleStockFuturesMargining`, `…ETFMargining`, `stockLists` page | read 2026-11-07 with `Date=20261106` (the earlier design measured a one-session lag at 19:27 Taiwan time on 2026-11-06; by 2026-11-07 the 11-06 data is present) | yes, **if** newest `Date` ≥ D−1, else fail closed |
| Fill / mark price of a contract | `DailyMarketReportFut` `SettlementPrice`, regular session | D−1 evening | same rule |
| Option settlement prices | `DailyMarketReportOpt` | same | see §6, mostly unusable |
| Open-interest / volume (liquidity) | same reports | same | yes |
| Risk budget, allowed classes, `max_leverage` | `risk_policy.py` fields for the pool | governed config | yes |

**Strength.** The signal today carries only BUY or SELL (`paper_signal_producer.py` L418). Proposed definition: strength is computed from the view's own inputs, e.g. `min(1, |momentum| / k·momentum_threshold)` with `k` a mutable parameter (new control); this is a deterministic function of closes ≤ D−1. **When absent** (a legacy artifact, or a decider that produces none): `strength = absent` is treated as the minimum size unit of the chosen instrument (one share / one smallest contract) and never as an enlarged position. Enlarging requires a present strength.

**Forbidden for any decider** (shared rule, per-persona policy, or model alike): supplying a price, a contract code, a contract month, a margin figure, a quantity above the clamp, or an instrument that is not in the candidate list. The decider returns an `instrument_id` from the list and a quantity in units of that candidate; the system rejects anything else with `reason=instrument_not_in_candidates`. A decider that names a price is rejected, not corrected.

## 4. Deciders compared

Fleet facts: five active runtimes on 2026-11-06, each one symbol on daily bars, about **5 decisions per trading day** (brief). Cost figures below that depend on provider pricing are **not asserted**; counts and token envelopes are.

| | A. Persona LLM call per decision (existing `persona/main.py` `/invoke`) | B. Per-persona decision policy evaluated at each decision, changed via the existing evolution path | C. Shared rule |
| --- | --- | --- | --- |
| What it is | a call with the view + candidates; model picks one candidate | a small parameterised function in the persona's artifact (e.g. thresholds on strength, volatility, available margin) mapping inputs to a candidate | one rule for all personas |
| Cost/day at 5 decisions | 5 model calls/day, envelope ~3k input + 0.5k output tokens each (assumption, no provider call was made); unit price **unverified** | 0 model calls; CPU only | 0 |
| Provider down | the 2026-11-06 isolated gate failed because Anthropic credit ran out and an OpenAI token expired: this path can fail for reasons outside the trade. Must degrade to `no_trade` or to a fallback rule; trading stalls or silently becomes rule C | not affected | not affected |
| Replay / backtest | non-deterministic unless the response is stored; backtest needs the model for every historical bar (cost × history, drift between model versions) | exact: same inputs → same output | exact |
| Restart under `decision_key` | safe only if the chosen candidate is persisted under `decision_id` **before** the order and reused on restart; otherwise a restart can pick a different instrument for the same bar | safe: pure function of the identity inputs | safe |
| Per-persona differentiation | high, but unexplained | high, explicit and inspectable | none: every persona alike, contrary to the operator's wording |
| Learning | cannot be tuned with the existing evolution path (`mutation_surface`) | tuned through `mutation_surface` controls (§9) | not tunable per persona |

**Recommendation (not a decision):** B. It is the only option that is deterministic under the existing identity, free when the provider is down, replayable, and changeable through the evolution path that exists. A model may later *advise* inside B by being one input whose output is stored under `decision_id`, but must not be on the trade path first. Reuse note: `DecisionSignalProducer` / `build_decision_signals` (`services/execution/lean_runtime/signal_producer.py` L115, L217) already normalise a decision dict with `symbol`, `execution_symbol`, `signals`, `targets`, `target_weights` and the paper producer already routes decisions through it; step 2's output should travel in that existing envelope (extend with `instrument_id`) rather than a second mechanism. `PersonaAllocationProposal` (optimizer-svc) and `allocation_proposal_refs` (`ooda_loop_packet.py` L131) address portfolio weights across personas, not a per-bar instrument choice, so they do not fit step 2; they may later carry the leverage budget input of step 3.

## 5. Why not the earlier fixed mapping

The earlier design fixed short = stock future, chose the smallest unit, and wrote `instrument_type` into the artifact (§11). Under the operator statement the same persona must be able to use shares, a futures contract, or nothing at different times with the same artifact bytes. §2 gives that; §11 lists what is kept.

## 6. Candidate instruments

Official sources read 2026-11-07 unless noted. Paper truthfulness = can the paper runtime fill at a price that was actually public at decision time, with correct cost and margin.

| Instrument | Directions | Leverage | Margin / premium | Expiry | Official source | Paper simulates truthfully? |
| --- | --- | --- | --- | --- | --- | --- |
| Cash shares (whole lot 1,000) | long only (short by 融券 excluded by operator) | none | full price | none | TWSE `STOCK_DAY_ALL` (prices) | yes; a SELL with no holdings is a no-trade, never negative (§7) |
| Odd lots (1–999 shares) | long only | none | full price | none | same; **odd-lot session rules unverified** (the TWSE rules pages returned a maintenance page on 2026-11-07) | the current ledger already fills 1 share at daily close; real intraday odd-lot matching unverified |
| Listed stock futures, incl. ETF futures (e.g. QFF 100 sh, DHF 2,000, SRF 1,000) | long and short | ~7.4× at 13.5% initial; SRF fixed 8,700 | initial 13.5%, maintenance 10.35%, clearing 10.00% (tier 1) of notional; SRF 8,700 / 6,700 / 6,400 per contract; `SingleStockFuturesMargining` and `…ETFMargining`, `Date=20261106` | third Wednesday of the delivery month, cash settled (`/cht/2/sTF`, read 2026-11-06) | `openapi.taifex.com.tw/v1/*`, `stockLists` | **yes**, with the lifecycle, price timing, and costs of the earlier design (kept, §11). Liquidity checked: 11-06 volume QFF 202611 126,386, DHF 14,260, SRF 30,754 |
| Stock options (e.g. 2330 `CDO`, unit 2,000 shares; Europeans; cash settled) | long call / long put (premium paid, loss bounded); short call/put (margin) | built in via premium | premium; short side a/b/c margin ratios (`stockMargining` page, updated 2026/10/16, 2330 tier 1: a 10.00% / b 5.00% / c 0.00% clearing, ×1.035 maintenance, ×1.35 initial); **the short-side margin formula was not read → unverified** | monthly, weekly Wed; last trading day third Wed; European (`/cht/2/sSO`) | `/cht/2/sSO`, `DailyMarketReportOpt` | **no for the first scope**: on 2026-11-06 `CDO` 202611 150 put had `SettlementPrice=65`, volume 0, open interest 0; the 2200 put closed 180 vs settlement 96. A settlement/"theoretical" price with no trades is not a fillable price. Premium × unit = 65×2,000 = 130,000 (multiplier = contract unit is this author's reading, unverified) exceeds 100,000 capital |
| Index futures (臺股期貨 TX ×200, 小型臺指 MTX ×50, 微型臺指 TMF) | long and short | large | 2026/08/12 page (older than other data): TX 519,000 / 538,000 / 701,000, MTX 129,750 / 134,500 / 175,250, TMF 25,950 / 26,900 / 35,050 (clearing / maintenance / initial); `TX 202611` settlement 39,652 on 11-06, so one TX = 7,930,400 notional | third Wed (`/cht/2/tX`, `/cht/2/mTX`) | `/cht/5/indexMarging`, `DailyMarketReportFut` | **partly**: price/margin data exist. Margin 35,050 per TMF fits 100,000 but TMF's multiplier page was not found (`/cht/2/mfTX` returned no spec row): **unverified**. Index futures express a market view, not a view on 2330 or 0050, and so are a different view type, not an instrument for the same underlying |
| Warrants (權證) | long call/put-type warrants only | built in | premium; issuer-priced, with a market-maker quote | per warrant; `t187ap37_L` lists e.g. 最後交易日 1160930 | TWSE openapi `t187ap37_L` (基本資料), `t187ap42_L` (daily trades) | **no**: no TWSE rules page could be read (maintenance page), no real quote path in the repo, issuer credit and quote spreads cannot be simulated; leave out |
| Inverse ETFs (e.g. 00632R 元大台灣50反1) | short-like via a long holding | −1× daily | full price | none | `STOCK_DAY_ALL` 1151106: 00632R close 11.46 (+0.54 while 0050 −4.65) | prices are real and the instrument is a plain cash ETF, **but** it is a *daily-reset* inverse of 0050 whose multi-day return differs from a short; the TWSE rules pages could not be read, so listing rules and margin eligibility are **unverified**. Usable only as a distinct instrument whose reason is logged as "inverse, daily reset", and only for 0050 |

**Proposed first scope (not decided):** cash shares and listed stock futures, plus the recorded no-trade. Reasons: both have a real daily price path, an official margin source, an existing lifecycle design, and they cover every operator goal (leverage, shorting). Options are excluded first because settlement prices without trades are not fillable; warrants because no truthful price or rule source was readable; index futures because they change the underlying, not the instrument (§10); inverse ETFs may be a later add-on for 0050 only. Adding an instrument later changes the candidate generator only (§7).

## 7. Instrument contract

**Candidate list** (computed by the system per decision, before the decider): for the view's underlying, each entry has
`{instrument_id, kind, side_capability, unit, multiplier, price, price_date, price_basis, margin_per_unit, max_units_by_risk, fee_model_ref, expiry}`. `instrument_id` is a stable string, e.g. `eq:2330`, `ssf:QFF:202611`. A candidate whose price is stale, whose margin row is missing, or whose liquidity is zero is **not** in the list.

**Chosen instrument record:** `{decision_id, instrument_id, side, quantity, reason_code, reason_text, candidates_considered[], strength}`. `reason_code` is a closed vocabulary (e.g. `short_via_future`, `long_lever`, `long_cash`, `no_feasible_instrument`).

**Position identity:** `(persona_binding_id, underlying, instrument_id)`; one persona may hold `eq:2330 +1` and `ssf:QFF:202611 −1` at once. Normalisation of `2330.TW`/`2330.TWSE` to one underlying is kept from the earlier design.

**Netting:** no automatic netting across instruments. A BUY decided on shares while a short contract is open leaves both open; exposure is reported per underlying as the sum of `q·multiplier` for the view (net delta in shares) *and* per instrument. A decider that wants to flatten chooses a closing leg of the same instrument; the risk gate counts gross notional, so open offsetting legs consume `gross_limit`.

**Adding an instrument later:** implement a candidate generator emitting the same record. The decider contract (inputs §3, output = one `instrument_id` from the list + quantity) and the events do not change.

**Example 1 — SELL view, no shares, derivative chosen.** 2330, persona flat, view `SHORT`, strength 0.6. Candidates (data `Date=20261106`): `eq:2330` (side capability long only → cannot express SHORT), `ssf:QFF:202611` (settlement 2,133, 1 unit = 213,300 notional, initial margin 28,795.5). Decider chooses `ssf:QFF:202611` quantity −1 with `reason_code=short_via_future`. Gate: gross notional 213,300 is 2.13× of 100,000, so a 2× limit would reject it (the limit is binding) and a 3× limit accepts it; the example assumes the operator's limit is 3×. Fill at 2,133 (D−1 settlement), margin 28,795.5 reserved, cash unchanged (no sale proceeds), as in the earlier ledger.

**Example 2 — SELL view, no shares, no feasible instrument.** 2317, view `SHORT`. Candidates: `eq:2317` (long only) and `ssf:DHF:202611` (settlement 225, notional 450,000 = 4.5× capital, initial margin 60,750). With a 3× limit the only short candidate is clamped to 0 units. Outcome: event `persona_no_trade`, `reason_code=no_feasible_instrument`, `detail=gross_limit_exceeded`, `decision_id` consumed (so a restart does not retry the same bar), **no fill, no cash change, no negative share ledger**.

## 8. Sizing and risk

**Per decision** the system computes `max_units_by_risk` per candidate = the largest integer `n` such that all hold: `n·initial_margin ≤ available_equity`; `gross_notional_after ≤ max_leverage × equity` (field `max_leverage`, `risk_policy.py` L91); `net_notional_after ≤ net_limit × equity` and `gross ≤ gross_limit` where set; the candidate's asset class ∈ `allowed_asset_classes` and ∉ `forbidden_asset_classes` (L99–100; the persona's pool policy must list `future` alongside `equity`, today `asset_classes: ["equity"]` at L1859). The decider chooses `quantity ≤ max_units_by_risk`; strength maps to a fraction of it; absent strength means one unit (§3).

**Order-level gate on the paper order path (new):** inside `SubmitTaiwanBrokerOrder` before any ledger change, re-check the same constraints with the **current** price. On failure: event `paper_order_rejected` with `reason ∈ {leverage_limit, gross_limit, net_limit, asset_class_not_allowed, insufficient_initial_margin, price_stale}`, `required`, `available`, `limit`, `decision_id`; no fill. This replaces the post-fill risk label noted in the earlier design (§5.4 there). The limit value is read from the persona's governed policy; none is hardcoded.

**Replay on 100,000.** Real price sequences in fill order (stock prices as a labelled stand-in, `stand_in=stock_close`; the daily futures series is not in the repository): 2330 2370→2135; 2317 245.5→224.5; 0050 104.95→92.5. Three decider outcomes, run long and short (the sign reverses). Computed with the scratch script `replay.py` (arithmetic only; margin at each price: 13.5% of notional for QFF/DHF, fixed 8,700 for SRF; maintenance 10.35% / 6,700):

| Underlying | Decider outcome | Units | Margin at entry | PnL at last price: long / short | Worst equity (long / short) | Peak margin ÷ equity (long / short) | Maintenance breach |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2330 | E1 shares ×1 | 1 sh | 0 | −235 / +235 | 99,765 / 100,000 | 0% | no |
| 2330 | E2 one smallest contract (QFF) | 1 | 31,995 | −23,500 / +23,500 | 76,500 / 100,000 | 37.7% / 32.0% | no |
| 2330 | E3 3× notional cap | 1 QFF (2.37× notional) | 31,995 | −23,500 / +23,500 | 76,500 / 100,000 | 37.7% / 32.0% | no |
| 2317 | E1 shares ×1 | 1 sh | 0 | −21 / +21 | 99,979 / 99,994.5 | 0% | no |
| 2317 | E2 one DHF | 1 | 66,285 | −42,000 / +42,000 | 58,000 / 89,000 | **104.5%** / 76.1% | no (maintenance 10.35% is met) but initial margin exceeds equity: a new order would be rejected (§8) |
| 2317 | E3 3× notional cap | 0 (one DHF is 4.91× notional) | — | no trade recorded | — | — | — |
| 0050 | E1 shares ×1 | 1 sh | 0 | −12.4 / +12.4 | 99,988 / 100,000 | 0% | no |
| 0050 | E2 one SRF | 1 | 8,700 | −12,450 / +12,450 | 87,550 / 100,000 | 9.9% / 8.7% | no |
| 0050 | E3 3× notional cap | 2 SRF (2.10×) | 17,400 | −24,900 / +24,900 | 75,100 / 100,000 | 23.2% / 17.4% | no |

(Costs excluded here; per-fill costs are in the earlier design §6.) Reading: the same view gives −235 or −23,500 depending only on the instrument decision, so the leverage limit is what bounds the loss; a 3× cap rejects DHF but allows two SRF.

**Effect on drawdown policies.** `services/evolution/config/threshold_sweep_baselines.json` holds 8 `expected_drawdown` entries (count of the key by `grep -c`, includes the `_example`), approved for 1-share paper PnL (e.g. 0.0027 for 2330, 0.0002 for 0050, 0.0011 for 2317 with 1.25× triggers). Any outcome other than E1 moves per-signal PnL by ×100 (QFF), ×2,000 (DHF), ×1,000 (SRF): a −23,500 loss on 100,000 is 23.5% of capital against a 0.34% trigger. Baselines must be re-derived (governed review, not a config edit here) **per instrument class** before any non-share decision ships; an artifact without an approved baseline "still fails closed (diagnostic only)" per that file's `_schema_note`.

## 9. Learning

* **What is graded separately.** The *direction call* is graded against the underlying's move (close D vs D−1, sign vs `direction`), independent of the instrument. The *instrument choice* is graded as realised PnL minus the counterfactual PnL of the non-chosen candidates.
* **Counterfactual PnL of instruments not chosen.** For every decision record `candidates_considered[]` with entry price. At each mark, compute per candidate `pnl_cf = side·q_cf·multiplier·(mark − entry)` with `q_cf` from the same strength rule, minus modelled costs. Stored as `persona_instrument_counterfactual` rows keyed by `decision_id`, `instrument_id`. These are computed from prices the system already holds; no new data source.
* **Teaching evaluation.** The direction hit rate (instrument-free) is the teaching metric for the view; instrument regret `max(pnl_cf) − pnl_chosen` and margin-use are the metric for the instrument decision, so a good call with a bad instrument is not scored as a bad call.
* **Mutation surface change.** Today `mutation_surface.controls` allows only `lookback_bars` and `momentum_threshold`; `order_quantity`, `quantity_type`, `symbols` are immutable (L2182–2190). To let a persona improve the decision (option B): add controls for the policy parameters, e.g. `strength_scale_k`, `lever_min_strength` (below which shares), `prefer_future_when_short` is **not** allowed (it would be a fixed mapping); and keep every limit (`max_leverage`, `gross_limit`) **out** of the mutation surface so evolution cannot raise its own risk limit. Validation of controls is in `strategy_artifact.py` L324–348 and evolution's child checks in `services/control-plane/bff/evolution/service.py` L672, L797.

## 10. View: underlying and direction at trade time

Today underlying is immutable and direction is a template rule (§1). **Minimal change to decide underlying and direction at trade time:** replace the single provisioned `symbols` with a *watchlist universe* (still provisioned, still immutable as the permitted set) and let step 1 output `{underlying ∈ universe, direction}` per bar; the decision key already includes `symbol`, so identity stays unique per underlying per bar. The artifact's `strategy_logic.kind` keeps `close_to_close_momentum` per symbol; ranking across the universe is a new view function.

* **Can the instrument decision ship before that change?** Yes. Step 2 takes any view; with the current one-symbol-per-persona template it simply receives the same underlying every day.
* **What leverage on the current template means:** a persona fixed to 2330 with a momentum-flip rule gets 1–3 QFF instead of 1 share; with `lookback_bars=2` and a daily flip the persona would be reversing a 213,300-notional position (2.1× capital) on small noise. Leverage amplifies a rule that was tuned on 1-share PnL and has not been shown to have edge, so shipping leverage before re-baselining (§8) raises loss without a demonstrated gain.
* **Recommendation (not a decision):** ship the instrument decision first with a conservative limit and shares as the default candidate; make underlying/direction trade-time decisions a second stage.

## 11. Reuse of the earlier design (`SD_TW_PAPER_STOCK_FUTURES_2026-11-06.md`)

| # | Earlier section | Verdict | Reason |
| --- | --- | --- | --- |
| 1 | §1 decision 3 (short is done through stock futures) | **Replaced** | the instrument is chosen per decision; futures is one candidate |
| 2 | §2.1 official sources | **Kept** | re-read 2026-11-07, same endpoints (data now `Date=20261106`) |
| 2b | §2.2 steps 1–2, 4–5 (normalise, candidates, fail closed, adjusted codes) | **Kept** as the candidate generator for futures |
| 2c | §2.2 step 3 "choose smallest unit" | **Replaced** | choosing is the decider's job; the system lists all candidates and the clamp, the smallest unit is only the default when strength is absent |
| 3 | §3 contract lifecycle | **Kept** | third Wednesday, no roll, `paper_futures_settled`; the same rule was re-read today only on the index and stock-option spec pages (`/cht/2/tX`, `/cht/2/sSO`), not on `/cht/2/sTF` |
| 4 | §4 price timing | **Kept** | D−1 settlement, fail closed on stale |
| 5 | §5.1, §5.3–5.6 formulas, margin, worked numbers 30,334.5 / 63,990 / 8,700 | **Kept**; numbers are for `Date=20261105` (2026-11-06 data: QFF 2,133 → 28,795.5; DHF 225 → 60,750; SRF 8,700) | |
| 6 | §5.2 same direction no-op | **Replaced** | with strength and a size from the decider, a second same-direction signal is a decision (add up to the clamp, or no-op chosen by policy); fixed no-op was a preset |
| 7 | §6 costs | **Kept** (index-futures and option fee rows not read) | |
| 8 | §7 ledger routing of SELL by artifact (`instrument_type`) | **Replaced** | routing is by the chosen `instrument_id`, not the artifact; the "SELL with no shares never creates a negative share position" rule is **Kept** |
| 9 | §8 recipe `instrument_type`, `CONTRACTS` quantity type, provisioning template change | **Replaced** | no `instrument_type` in the artifact; no provisioning template change; `quantity` is in the chosen instrument's units |
| 10 | §9 long-side comparison as a global choice | **Replaced** | per-decision; the replay table in §8 supersedes; the per-contract loss figures stay valid |
| 11 | §10 data path | **Kept** | one connector path (`tw-taifex-futures-options-chip`), egress gate |
| 12 | §11 consumer table | **Extended** (§12 here) | |
| 13 | §12 cutover, switch keyed on `instrument_type` | **Replaced** (§13 here) | the switch is a runtime feature flag for the decision step, not an artifact field |
| 14 | §13 split | **Replaced** (§14 here) | |
| 15 | §14 unverified list | **Kept** and extended (§15) | |

## 12. Consumer table

Method: `git grep -l "paper_fill_simulated\|position_quantity\|fill_quantity"` over `*.py`, `*.ts`, `*.tsx` excluding tests and node_modules, 2026-11-07, returned 25 files (listed in `evidence.json`); no `.ts`/`.tsx` file matched. Columns: how the **instrument identity** (`instrument_id`, `multiplier`, `contract_month`) and the **decision reason** (`reason_code`, `decision_id`) reach each reader.

| Consumer | Reads | Instrument identity / reason |
| --- | --- | --- |
| `execution/lean_runtime/paper_runtime.py` | writes fills, ledger | writes both; ledger keyed `(binding, underlying, instrument_id)`; the gate (§8) |
| `execution/lean_runtime/paper_signal_producer.py`, `signal_producer.py`, `signal_consumer.py`, `executor.py` | signals, `fill_quantity`, `position_quantity` | carries `instrument_id` + `reason_code` in the decision envelope; `executor` metadata gains `multiplier` |
| `integrations/lean/pantheon_algo/base.py` | fills | must accept the extended fields without failing |
| `telemetry/runtime_summary.py`, `trade_episode_projection.py`, `feedback_adapter.py`, `capture.py`, `backpressure.py`, `telemetry_event.schema.json` | fills → average cost, realised PnL **per share** | **holdings and attribution**: PnL must multiply by `multiplier` and group by `instrument_id`; episodes carry `reason_code`; the counterfactual rows (§9) enter here |
| `control-plane/bff/pm12/service.py`, `bff/agora/performance/service.py` | quantity/avg price → holdings, value | **BFF holdings**: market value = q·multiplier·price per instrument; show instrument and contract month; reason shown on the decision row |
| `agora/decision_projection/producer.py` L175 | hardcodes `asset_class: equity` | sets `subject.asset_class` from the chosen instrument kind (schema `trading_decision_event.schema.json` already has `subject.asset_class`, `event_kind`, `origin`); the reason goes in the event payload |
| `trade_journey/lifecycle_projector.py`, `lifecycle_projector_capacity.py`, `telemetry_bridge.py`, `incremental_materializer.py`, `lifecycle_noop_order.py` | fill events | quantities are counts: unchanged; `persona_no_trade` and `paper_order_rejected` must be classified as terminal non-fill states |
| `reconciliation-drift/main.py` (L3833–3860) | `fill_quantity` vs `requested_quantity` | **reconciliation drift**: identity block gains `instrument_id`; rejected and no-trade decisions must not appear as unreconciled |
| `persona/agent_usability_validation.py`, `remaining_gap_validation.py`, `cognitive_loop_runtime.py` | fills, quantity types | expectations change (signed quantities, rejection terminal states) |
| `trade_journey/hosted_lifecycle_probe.py`, `scripts/probe_dev_runtime_paper_lifecycle.py` | counts `paper_fill_simulated` | **hosted lifecycle probes** must accept `persona_no_trade` / `paper_order_rejected` as valid terminals |
| hosted all-12 harness (`scripts/backfill_hosted_chain_persona_scope.py`, `scripts/deploy/release_manifest.py`, `.github/workflows/nonprod-deploy.yml`, `scripts/verify_e2e_evolution_loop.py`) | personas, quantities | **hosted all-12 harness**: assumptions of `SHARES` and of fills; list finalised in the hosted acceptance task |
| `services/capital/risk_policy.py` + `threshold_sweep_baselines.json` | `drawdown_pct`, `expected_drawdown` | **paper baseline**: re-baseline per instrument class (§8) |
| `registry/strategy_artifact.py` | artifact fields | **unchanged** under this design (no `instrument_type`), only the mutation surface gains controls (§9) |
| frontend | no direct reader found by `git grep` in `*.ts`/`*.tsx` | follows the BFF; **not verified in a browser** |

## 13. Cutover

* Open stock short positions (2026-11-06 11:25Z, from the brief): 2330.TWSE −6, 0050.TWSE −6, 2317.TWSE −4, and two 2330.TW −1 rows. They have no real equivalent and are not rewritten.
* **Path (existing governed persona path, no hand-edited store):** `_retire_persona` (`persona_provisioning_coordinator.py` L858, requires authoritative archive readback) for the personas that carry negative share history, then provisioning of new persona artifacts through the same coordinator, or `transition_legacy_persona_market` for a child revision. History of the old artifact id stays read-only in telemetry. The instrument decision itself needs no new artifact revision unless the mutation surface (§9) changes; if the controls are added, personas receive them through the same governed child-revision path.
* **Switch:** a runtime flag enabling step 2; off = today's behaviour for rollback, with the sell-without-holdings fix as the first, separate, switch-free correction (§14 T1).
* **Fleet baseline counts:** the file holds 8 `expected_drawdown` keys (7 baselines plus `_example`). Each *new* artifact id needs its own approved baseline; retired ones stop counting as active. The exact count after cutover depends on how many personas are re-provisioned and is not asserted.

## 14. Implementation split (ordered; no task is created by this work)

| # | Task | Artifacts | Acceptance | Depends on | Changes with decider choice / scope choice |
| --- | --- | --- | --- | --- | --- |
| T1 | Ledger no-negative-shares fix | `paper_runtime.py` | SELL with no holdings → `paper_order_rejected reason=sell_without_holdings` or no_trade; no negative share row | — | none |
| T2 | Contract master + candidate generator | connector dataset, active-universe rule, `ContractMaster` reader, fee constants | candidate lists for 2330/2317/0050 reproduced from `20261106` payloads | operator Q2 | scope: options/inverse ETF add generators later |
| T3 | Egress/deploy gate | `deploy_nonprod_vm.sh`, `nonprod-deploy.yml` | exact-host validation | T2 | none |
| T4 | Decision step and events | decision envelope extension, `persona_instrument_chosen`/`persona_no_trade`, `decision_id` reuse | restart does not re-decide (extends the existing restart test) | operator Q1 | **A**: also persistence of the response and provider-down fallback; **B**: policy evaluation + mutation controls (T8); **C**: one rule |
| T5 | Futures ledger + risk gate | `paper_runtime.py`, `paper_simulation.py` | §8 gate with rejection events; replay table reproduced | T1, T2, T4, operator Q3 | leverage limit value |
| T6 | Consumers | §12 files | instrument-aware holdings, PnL, drift, probes | T5 | scope |
| T7 | Drawdown re-baseline | governed review of `threshold_sweep_baselines.json` | approved baseline per artifact and instrument class | T5 | limit value |
| T8 | Learning | counterfactual rows, attribution, mutation controls | §9 | T4 (B only) | **B only** |
| T9 | Cutover | governed provisioning | history intact, flag on | T5–T7 | none |
| T10 | Hosted acceptance (one task) | one hosted run: decision → shares or contract → mark → settle across expiry, rejection on limit, stale-price fail-closed | hosted evidence | T1–T9 | exercises the chosen decider and scope |

If Q4 is "yes", add a view-universe task before T4 (§10). If Q2 stays at shares+futures, T2 has two generators only.

## 15. Sources and unverified items

Read 2026-11-07 (public HTTPS): `https://www.taifex.com.tw/cht/2/stockLists`, `/cht/2/tX`, `/cht/2/mTX`, `/cht/2/tXO`, `/cht/2/sSO`, `/cht/5/indexMarging` (page update date 2026/08/12), `/cht/5/stockMargining` (options table update date 2026/10/16), `/cht/4/feeSchedules`; `https://openapi.taifex.com.tw/v1/SingleStockFuturesMargining`, `SingleStockFuturesETFMargining`, `DailyMarketReportFut`, `DailyMarketReportOpt` (all `Date=20261106`); `https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL` (`Date=1151106`), `/v1/opendata/t187ap37_L`. The earlier design's sources (read 2026-11-06) are kept as cited there.

**Unverified:**

1. TWSE rules pages for warrants, inverse ETFs and odd-lot trading: `www.twse.com.tw` pages returned a 752-byte maintenance page on 2026-11-07; no rule is cited from them.
2. Stock-option short-margin formula (only the a/b/c ratios were read); option premium multiplier (read as the contract unit 2,000).
3. Micro TX (TMF) contract spec and multiplier (`/cht/2/mfTX` has no spec row); index-futures and option fee rows.
4. Index-futures margins come from a page dated 2026/08/12 and may be superseded.
5. Whether odd-lot fills at the daily close match any real odd-lot session.
6. All items 1–9 of the earlier design's unverified list (publication time, holiday shift, 交割手續費 at cash settlement, broker commission, margin-call timing, adjusted contracts, units dataset, frontend, hosted behaviour).
7. Token and cost figures for decider A (envelope only, no provider price).
8. Hosted behaviour: nothing in this document or its evidence claims hosted behaviour.
