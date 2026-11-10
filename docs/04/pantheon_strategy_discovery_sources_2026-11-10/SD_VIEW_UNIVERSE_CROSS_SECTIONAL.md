# SD — Trade-time universe for cross-sectional rules (design only)

Task `SD-VIEW-UNIVERSE-CROSS-SECTIONAL-20261110` · owner Claude · reviewer Antigravity · 2026-11-10
Parents: `SD_STRATEGY_DISCOVERY_SOURCES_2026-11-10.md` §9.5, `DEV_PLAN_STRATEGY_DISCOVERY_2026-11-10.md` §3.4 (plan wins where they differ), `pantheon_persona_instrument_decision_2026-11-07/SD_PERSONA_INSTRUMENT_DECISION_2026-11-07.md` §10 and `DEV_PLAN_INSTRUMENT_EXPRESSION_LAYER_2026-11-07.md` Q4.

**Status: design only. No source file is changed. Nothing here is decided for the operator; §7 lists the open questions.**

## 0. 中文摘要（操作者用）

* 問題：`cs_rank`、`cs_scale`、`industry_neutralize`（Alpha101 一部分、OSAP 類）要在「同一天一整籃股票」上比較，今天的 persona 只綁一個 provisioned 標的、`factor_rule_v1` 只算單檔時間序列。
* 提議：persona 在 provisioning 時只宣告「允許的股票池」（immutable）；每個決策日由系統從治理資料算出一份「當日股票池快照」（point-in-time、只用 D−1 以前資料）；跨股票運算子只在這個快照上算；view 輸出仍是 `{action, direction, strength}`，但多一個 `underlying`，再交給 11-07 的商品決策層（候選、expression policy、風控、ledger），下游不改。
* 以下四點屬於操作者決定，本文只列選項不決定：股票池大小與來源、是否允許 persona 在池內換標的、再平衡頻率與換手上限、產業分類來源。

## 1. What exists today (verified in the tree)

| Item | Where | Fact |
| --- | --- | --- |
| Persona symbols | `services/control-plane/bff/persona_provisioning_coordinator.py` `_paper_universe` (`:1749`) | `symbols` is a provisioning-request list; no default for non-US markets |
| Immutable set | `strategy_artifact.py` `immutable_parameters` (`:411`, `:545`, `:1027`) | `symbols` is immutable: evolution cannot change it |
| View | `strategy_artifact.py` `evaluate_strategy_view` (`:1120`) | takes one `closes` sequence, returns one-symbol `{action, direction, momentum, strength}` |
| Paper producer | `paper_signal_producer.py` (`:610-646`, `:686-702`) | one `market_input.closes` per binding; `decision_key` includes `symbol` and `bar`, so identity is already unique per underlying per bar |
| Instrument layer | `expression_policy.py`, `instrument_candidates.py` | consumes one view for one underlying and builds candidates for it |
| Grammar | dev plan §3.4 | `cs_rank`, `cs_scale`, `industry_neutralize` parsed and stored, rejected by the per-symbol evaluator until this work |

## 2. Terms

* **Permitted universe (P)**: the provisioned, immutable symbol set of a persona. Superset bound; never exceeded at trade time.
* **Trade-time universe U(D)**: the subset of P that is eligible on decision day D, computed by the system from data dated ≤ D−1.
* **Cross-sectional rule**: a `factor_rule_v1` whose expression contains a `cs_*` or `industry_neutralize` operator.

## 3. How a persona chooses its trade-time universe

1. **Declare P at provisioning.** Today's `symbols` becomes `universe.permitted` (still immutable). A one-symbol persona is the special case |P| = 1 and keeps its current behaviour unchanged (legacy artifacts carry no `universe` section).
2. **Declare a universe policy in the artifact** (a new `universe` section, versioned like `expression_policy`):
   * `source`: `provisioned_list` (P as given) or `governed_index` (a named governed membership dataset; availability is an open question, §7-Q1);
   * `eligibility filters`, all deterministic and evaluated on D−1 data: minimum bars of history, minimum median traded value over N days, not suspended, not in the limit-hit/no-trade state on D−1, listed for at least N days;
   * `min_size` (below it a cross-sectional rule returns HOLD for everyone; a rank over three stocks is noise, not a signal);
   * `rebalance`: how often U is recomputed (every bar, or every k bars; open question Q3).
3. **Mutability.** Eligibility thresholds and `rebalance` may be mutable controls on the mutation surface, with ranges. P, `source` and the operator-set `min_size` are immutable. The persona therefore *chooses within* P by its own thresholds; it cannot add a symbol the operator did not permit.
4. **Snapshot record.** For every decision day the system stores `universe_snapshot = {as_of D, members, excluded: [{symbol, reason}], filters_version, source_refs}` and hashes it into the decision key (`universe_digest`). Two decisions on the same bar with different snapshots are different decisions; the same snapshot reproduces the same decision.

## 4. Point-in-time evaluation of cross-sectional operators

* **Information cut-off**: every input to U(D) and to any `cs_*` operator is dated ≤ D−1, the same rule as the strength function in the 11-07 plan §4.1. Backtest and paper use one evaluator (dev plan §9.3).
* **Operator definitions on U(D)** (the paper's definitions; only members of U(D) take part):
  * `cs_rank(x)`: rank of x among U(D) members, scaled to (0,1]; ties get the average rank; members with missing x are excluded from that operator's set and recorded in `excluded`, never imputed.
  * `cs_scale(x)`: x divided by the sum of absolute x over U(D) members (a zero sum gives HOLD for the day).
  * `industry_neutralize(x)`: x minus the mean of x within the member's industry group on U(D); a group with fewer than 2 members falls back to no-demeaning and is flagged. The classification source and its point-in-time effective dates are open (Q4); an undated classification is look-ahead bias and is not admissible.
* **No survivorship**: history used for backtests must include symbols that were in U(D) on past days and later delisted or were suspended. If the governed daily-bar dataset only holds today's symbols, cross-sectional backtests are marked `survivorship_biased` and cannot be admitted by the validation gate (design §10.3) until the dataset says otherwise. This is checked in code, not assumed.
* **Missing data**: a member lacking D−1 data is excluded for that day, with a reason, rather than carried forward.
* **Trial counting**: every U(D) policy variant counts as a trial for the template family (design §10.3), same as a wrapper variant.
* **Parity test** (acceptance for the implementation task): for fixed artifact and data, the sequence of `universe_snapshot` and of per-day outputs from the backtest equals a paper replay, bar for bar.

## 5. Output of the view and the hand-off to the instrument decision layer

The cross-sectional view returns, for the decision day, a ranked list rather than one verdict:

```
cross_sectional_view = {
  as_of: D, universe_digest,
  picks: [ {underlying, action: BUY|SELL, direction: LONG|SHORT, strength in [0,1]|None, rank, score} ... ],
  holds: [underlying ...]            # members with no action, recorded
}
```

A **selection step** (deterministic, parameterised in the artifact, e.g. `top_k`, `bottom_k` or a quantile cut, `max_names`, `min_strength`) turns `picks` into at most `max_names` per-underlying views of exactly the existing shape `{action, direction, momentum|score, strength}` plus `underlying`. Each per-underlying view then enters the 11-07 pipeline unchanged:

1. layer A view (new: `underlying` field; legacy single-symbol views fill it from the binding);
2. layer B `build_candidates(underlying, …)` for that underlying (cash share or stock future, per the 11-07 Q2 scope);
3. layer C expression policy chooses the instrument per decision;
4. layer D risk gate: the existing per-persona leverage limit (Q3 of 11-07, 2.0× pool equity) now applies to the **sum over all picks**, not per pick. This is the main new interaction: the gate needs the gross exposure of the whole selected set. The gate is also where a cap on the number of simultaneous names and on turnover is enforced, outside the artifact's mutation surface;
5. layer E ledger: identity per underlying per bar already holds because `decision_key` has `symbol`; the additional `universe_digest` ties each row to its snapshot.

A persona that holds a name that falls out of U(D) or out of the picks gets a closing decision for it (action to flatten) from the same layer, not a silent drop; the rule for when to close (on exit from the picks, on exit from U, or only on a reverse signal) is open (Q5).

What this design does **not** change: the 11-07 candidate record, expression policy v1 formulas, the leverage-limit location, the no-model-on-trade-path rule, or any live/broker path.

## 6. Work implied (not opened by this task)

An implementation plan, after the operator answers §7, would touch: artifact schema (`universe` section, validation in `strategy_artifact.py`), a point-in-time snapshot builder over governed datasets, the cross-sectional evaluator for `cs_*`, the paper producer (multi-symbol `market_input`, decision key), the vectorbt adapter, the risk gate (aggregate exposure), and the provisioning request (`universe.permitted`). Sizing and task split belong to that plan.

## 7. Open questions for the operator (not decided here)

| # | Question | Options seen, no recommendation |
| --- | --- | --- |
| Q1 | Where does the permitted universe come from? | (a) operator lists symbols per persona (today); (b) a governed membership dataset such as the TWSE 0050/0051/0056-style index lists, if one is ingested (not verified to exist in the registry); (c) all symbols with daily bars. Larger P costs more data and more paper positions. |
| Q2 | May a persona pick *which* members to trade, or only trade P as given? | Choosing within P is this design; the alternative keeps every member of P always traded and uses the rank only for sizing. |
| Q3 | Rebalance cadence and turnover cap | every bar vs weekly vs monthly; a per-day maximum number of changes. Taiwan fees and 0.3% securities tax on sells make daily rank-flipping costly. |
| Q4 | Industry classification source | TWSE industry codes (is a point-in-time history available?), a third-party scheme, or no `industry_neutralize` until one exists. |
| Q5 | Closing rule for names that leave the picks or U | exit immediately, exit on opposite signal, or hold to a time limit. |
| Q6 | Long-only or long/short cross-sectional books | Taiwan shorts via stock futures in the 11-07 scope; bottom-k cash shorting is not available (no 融券 route, standing decision). |
| Q7 | Minimum universe size and the behaviour below it | the design says HOLD for all; the number is the operator's. |
| Q8 | Survivorship: is it acceptable to admit cross-sectional candidates before delisted-symbol history is ingested? | design says no (§4); the operator may overrule. |
| Q9 | Max simultaneous names and aggregate leverage across picks | the 2.0× limit exists per persona; whether it is the right bound for a basket is the operator's. |

## 8. Risks and unverified items

* Whether the governed Taiwan daily-bar dataset contains delisted or suspended symbols, and whether any dated industry classification exists, were **not verified** in this task (design-only); both determine Q4 and Q8.
* The aggregate-exposure change in the risk gate is the highest-risk edit; it needs its own review before any paper rollout.
* Cross-sectional factors on a small P (tens of symbols) have wide estimation error; admission thresholds in design §10.3 apply and may need a larger sample for these templates.
