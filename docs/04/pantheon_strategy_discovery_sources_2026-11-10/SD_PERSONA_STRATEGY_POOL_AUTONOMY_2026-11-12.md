# SD-PERSONA-STRATEGY-POOL-AUTONOMY-20261112 — a persona owns a changing strategy pool and evolves on its own

| Field | Value |
| --- | --- |
| Status | Design for implementation; operator decisions of 2026-11-12 recorded in §1 |
| Date | 2026-11-12 |
| Repo base | `origin/dev` 796d55c03 |
| Replaces | the persona-per-strategy model of `SD_STRATEGY_DISCOVERY_SOURCES_2026-11-10.md` §11–§12 and of `SD_DISCOVERY_DEMAND_AND_EVOLUTION_TRIGGERS_2026-11-12.md` §3.3 "Replacement"; superseded task `DISCOVERY-ALLOCATOR-RUNS-ADMITTED-STRATEGY-20261112` |
| Keeps | collector, L2 extraction, hypothesis cards with predictions, `factor_rule_v1`, the real-backtest gate, the 2026-11-07 instrument expression layer, `SD_VIEW_UNIVERSE_CROSS_SECTIONAL.md` |
| Scope | design only; no source, test, workflow, configuration or infrastructure change |

---

## 0. 中文摘要（操作者用）

**為什麼重寫：** 之前的設計把「一個策略」和「一個 persona」綁在一起：每通過一個策略就建一個新 persona，策略變差就把 persona 退役。你 11/12 指出這是錯的。正確的模型是：persona 長期存在，它底下的策略池會變動，而且 persona 要自己進化。

**新模型：**
- **persona 是主體。** 它有自己的條件（市場、風格、風險）、資金、記憶，以及一個策略池。策略失敗只是移出池子，persona 照常運作。
- **一個帳戶、合併下單。** 池裡每個策略各自出訊號，先合併成每檔標的一個淨部位，再交給商品層決定用現股或衍生品（你選的 1a）。各策略另外記一本「虛擬帳」，用來評估它自己的成績。
- **池子大小不設固定數字。** 看兩個指標：風險額度用了多少、池裡實際有幾個互相獨立的下注（有效下注數）。額度沒用滿或策略太像，persona 就自己去找新策略；額度用滿後，新策略只能替換舊的。另留一個很寬的安全上限，只防程式出錯。
- **進池門檻不因為資金閒置而降低。** 新策略要自己通過驗證、跟池內策略不像、合併後整體變好。找不到好的，寧可讓資金閒著。
- **標的由策略選。** persona 有一份「允許交易的股票清單」。策略每天從清單裡選出候選標的，再由商品層決定怎麼交易。persona 可以自己提出擴大清單，範圍限定在有治理資料的台股。
- **每天的自主循環放在既有的 persona 排程裡。** 觀察自己、判斷缺什麼、決定找什麼或改什麼、執行、記住結果。不另外做一套排程。
- **先從青禾、衡岳、澄川開始。** 它們現在的動能策略變成各自池裡的第一個策略。
- **自主權範圍：** paper 內增減策略、調整比重、擴大標的，都不需要人核准。超出 paper 的動作都要人核准。

**分四波交付（§7）：** 先讓一個 persona 能同時跑多個策略並合併下單，再做池子的進出與資金分配，然後是自主循環，最後是 hosted 驗收。今天卡住的需求單、理由檢驗、定期演化任務會依新模型重開。

---

## 1. Operator statements and decisions (2026-11-12, verbatim)

> 我感覺不太對，應該是persona同樣，但是persona下面的策略池會變動才對
> 你這樣感覺起來就是把persona跟策略綁在一起
> 實際上persona應該要做到有自主進化的能力

Decisions on the coordinator's questions:

| # | Question | Decision |
| --- | --- | --- |
| P-1 | How pool strategies place orders | one account per persona; strategy signals are combined into one net position per underlying; each strategy is evaluated on a separate virtual book |
| P-2 | Pool size | no fixed number: 「這個問題應該要討論的是資金利用率高不高這種標準來決定策略足部足夠? 而不是一個固定數字吧」; after discussion: risk-budget utilisation and effective number of bets are the main criteria (「可以」) |
| P-3 | Which personas first | the existing 青禾, 衡岳, 澄川; their momentum strategy becomes the first pool member (「好」) |
| P-4 | Autonomy | within paper, adding/removing strategies and re-weighting need no approval; anything beyond paper needs the operator (「好」) |
| P-5 | Universe | 「當然，本來設計就有機制是要按照策略選出要交易的標的候選池阿，然後才從交易候選池思考設計用那些衍生性金融商品來交抑或是直接交易標的商品」; the persona may propose widening its permitted list within governed Taiwan data |
| P-6 | Safety ceiling | keep a wide ceiling that only guards against runaway code (「好」) |

The O-13 … O-20 decisions of the same day (「都依照你的建議處理」) stay in force where they do not assume one
persona per strategy: demand triggers, reviewed weekly query plans, a contradicted prediction blocks admission,
20-trading-day revalidation, weekly exploration with 50 variants and a 500-trial family cap, fixed query
vocabulary, deferred expression-policy learning. O-18 (child replaces parent) is restated in §5.4 as a pool
operation.

---

## 2. Why the current system binds persona to strategy (verified on origin/dev)

| Fact | Where |
| --- | --- |
| A persona's capital pool allows one runtime | `services/capital/models.py:28`, `:72` (`single_runtime_enforced: bool = True`) |
| Provisioning builds exactly one strategy artifact per persona | `services/control-plane/bff/persona_provisioning_coordinator.py` `_strategy_artifact_payload` and `_coordinate_strategy_artifact` |
| The paper producer evaluates one artifact per binding | `services/execution/lean_runtime/paper_signal_producer.py` (one `market_input` per binding) |
| Automatic evolution retires the persona, not a strategy | `services/evolution/auto_consumer.py:41` (`RETIRE_PATH = ("frozen", "retired")`, persona lifecycle) |
| The allocator treated personas as slots | `services/research/discovery_allocator.py:15` (`MAX_DISCOVERY_PERSONAS`) |
| Trade-time universe is design only | `SD_VIEW_UNIVERSE_CROSS_SECTIONAL.md`; no implementation in `services/` |
| A per-persona schedule and an Observe–Orient–Decide–Act–Learn record already exist | persona cron (registered at provisioning by `_register_persona_cron_required`, `services/control-plane/bff/personas/service.py:4380`, via `services/control-plane/cron/persona_cron_registrar.py`); `services/persona/cron_ooda_closure.py`, `services/control-plane/ooda/ooda_loop_packet.py`, read at `/bff/ooda/packets` |

## 3. What others do (read 2026-11-12)

* Multi-manager funds allocate **risk**, not slots: allocations are cut at about 5 % drawdown from the
  high-water mark and closed at 7.5–10 %; capital moves on drawdowns, correlation shifts and evidence that an
  edge still holds. No source gave a public rule for increasing an allocation.
* WorldQuant-style alpha pools are large, low-correlation (15.9 % average pairwise for the 101 formulaic
  alphas) and are combined into one traded signal; a common cut-off for admitting a new alpha is about 15 %
  correlation with existing ones (AlphaEvolve paper, attributed to hedge-fund practice).
* QuantConnect Lean feeds several alpha models into one portfolio construction model; by default only the
  latest insight per symbol is kept, so real blending needs an explicit combination rule.
* Diversification is measured by the effective number of bets (Meucci), not by a count of positions.
* More trials mean a higher probability that a backtest is overfit (Bailey, Borwein, López de Prado, Zhu), so
  a larger pool needs a stricter admission threshold.
* Standard result: a new strategy improves a portfolio when its Sharpe ratio exceeds its correlation with the
  portfolio times the portfolio's Sharpe ratio.

Sources are listed in §9.

---

## 4. Target model

```
 persona (long-lived): mandate · capital · risk budget · memory · permitted universe P · strategy pool
        │
        │  daily persona cycle (existing persona cron → OODA record)
        │    Observe  pool health: each member vs its baseline, risk-budget use, effective bets
        │    Orient   what is missing: idle risk budget, members too alike, a decayed member, missing data
        │    Decide   research demand · admit trial member · re-weight · mutate · remove · widen P
        │    Act      through shared services (below), all paper-only
        │    Learn    outcomes into persona memory; steers the next demand
        ▼
 shared services (unchanged doors): collector → L2 cards → compiler → gate (mechanism + backtest) → registry
        ▼
 one paper runtime per persona
   member views (per strategy, per underlying chosen from U(D) ⊆ P)
        → combination layer: one net view per underlying
        → instrument expression (cash share / stock future / no trade) → risk gate → ledger
   virtual book per member (its own views, same marks) → member performance vs member baseline
```

### 4.1 Strategy pool (new record, owned by the persona service)

`persona_strategy_pool.v1`, versioned; every change appends a new version.

* `members[]`: `artifact_ref` and checksum (a gate-passed registry artifact), `stage` (`trial` | `active` |
  `removed`), `risk_weight`, `baseline_ref` (the gate verdict, §5.2), `joined_at`, `parent_ref`,
  `demand_ref`.
* `pool_version` is hashed into every decision key, like the `universe_digest` specified in `SD_VIEW_UNIVERSE_CROSS_SECTIONAL.md` (design only, not yet in code), so a decision always names the
  exact pool it was made with.
* A removed member stays in history; the persona is never retired because a member is removed.

### 4.2 One runtime, combined order flow (decision P-1)

* The persona keeps one capital pool and one runtime (`single_runtime_enforced` stays true).
* The persona's runtime artifact becomes a **portfolio envelope**: immutable combination rule and risk
  limits; membership is read from the pool version of the decision day. Each member is evaluated by the
  existing single evaluator (`evaluate_strategy_view`), unchanged.
* **Combination**: for each underlying, net view = Σ member `direction × strength × risk_weight`, clipped to
  the persona risk limits; opposite views net out instead of trading against each other. The net view has
  the existing shape `{action, direction, strength}`, so the expression layer, risk gate and ledger are
  unchanged.
* **Virtual book per member**: each member's own views are marked with the same prices to give a per-member
  return series, Sharpe ratio and drawdown without placing orders. Trial members can run virtual-only before
  receiving weight.
* Backtest and paper use the same combination function (parity test).

Amendment approved by the operator on **2026-11-13**
(`PERSONA-POOL-TARGET-POSITIONS-20261113`): weights belong to strategies.
The pinned envelope fixes member artifacts, weights, combination rule and risk
limits. Every bar evaluates each member with its own position history, converts
its exit to a zero member target and combines all views before allocating
capital. `raw[s] = abs(net[s]) × notional_fraction_at_full_strength × gross_limit`;
if the sum exceeds the gross budget, multiply all targets by the same factor.
Order of symbol evaluation cannot change these targets.

Multi-member and screened envelopes use `target_position_v1`: zero net closes
held legs, a weaker net reduces them, an increased target adds only the delta,
and an opposite target closes before reversal on the next bar. The rebalance
band is the larger of one held unit's notional and
`rebalance_band_fraction × target`. The starting fraction `0.2` is the
coordinator's proposal, not an operator numerical decision. The band, member
snapshot digests, book and bar allocation inputs are recorded for replay.
A full-weight unscreened singleton and standalone strategies retain
`notional_target_v1` with identical decisions. Existing risk gates and limits
remain authoritative.

### 4.3 Universe (decision P-5)

* The persona declares a permitted universe P; each member selects its trade-time universe U(D) ⊆ P by the
  universe policy of `SD_VIEW_UNIVERSE_CROSS_SECTIONAL.md`; picks flow to the combination layer and then to
  the instrument expression layer, which chooses the underlying or a derivative.
* The persona may propose widening P inside the governed Taiwan list (symbols with governed daily bars and
  the point-in-time history the gate needs). Within paper this needs no approval; the change is recorded as
  a pool-level decision with its reason.

The **2026-11-13 operator-approved amendment** makes daily picks explicitly
unversioned: an envelope does not enumerate screened members' picks. Each
screened member evaluates the complete governed market through the same
candidate-pool snapshot path as a standalone binding. Its per-bar digest
identifies those picks; changing that snapshot never changes member weights
or requires a new pool version. The combined decision universe contains fixed
member symbols, the current screened picks and every underlying the persona
still holds. A removed pick with an open member position emits that member's
exit and lowers the combined target. Missing governed inputs or marks fail
closed; fixed provisioning lists never substitute for the governed market.

### 4.4 Pool health and sufficiency (decision P-2)

| Measure | Definition | Use |
| --- | --- | --- |
| Risk-budget utilisation | rolling average of gross notional ÷ the persona's gross limit, and drawdown ÷ the drawdown limit | low → the persona asks for strategies |
| Effective number of bets | from the member virtual-book return correlations (Meucci) | low → ask for a different kind of strategy, not more of the same |
| Idle days | share of trading days with no position | supporting signal |

Starting thresholds are the coordinator's proposals, not operator decisions (operator-tunable, recorded in policy, reviewed after 20 trading days of data); the same holds for the numeric starting values in §5.1 and §5.3:
utilisation below 60 % over 20 trading days, or effective bets below 2 with two or more members, opens a
demand. Budget full → a new member can only replace a weaker one. Safety ceiling: 20 members (P-6).

## 5. Pool operations

### 5.1 Admission (never loosened by idle capital)

A candidate joins as `trial` only when all hold:

1. it passed the gate (mechanism stage and real backtest) on symbols inside the persona's P;
2. its return correlation with every active member is below the cut-off (starting value 0.5 on backtest
   returns; stricter cut-offs are common for pure alpha pools but would starve a three-symbol universe);
3. the combined backtest of pool + candidate has a higher Sharpe ratio than the pool alone
   (equivalently: candidate Sharpe > correlation × pool Sharpe), on the untouched out-of-sample window;
4. the admission threshold is deflated by the persona's own trial count, not only the family's.

No qualifying candidate → the risk budget stays unused and the reason is recorded.

### 5.2 Baseline

The gate verdict of a member (out-of-sample Sharpe, drawdown, backtest run) is its approved baseline;
`DISCOVERY-GATE-PASS-APPROVED-BASELINE-20261112` is re-scoped to key baselines by pool member and to compare
them with the member's **virtual book**, not with the persona's combined account.

### 5.3 Weights

Risk-parity start; then tilt by realised virtual-book performance within bounds; a member's weight is halved
at 5 % drawdown from its own high-water mark and the member is removed at 10 % or on persistent decay against
its baseline (the existing decay rule, applied to the member). `trial` members hold at most 10 % of the risk
budget until 20 trading days of virtual-book history exist.

### 5.4 Evolution inside the pool

* The persona proposes `mutate` / `retrain` / `revalidate` for its own members on the adopted cadence
  (weekly; every 20 trading days) through the existing evolution proposal API; the auto gate approves
  paper-only pool actions.
* A passing child enters the same pool as `trial` and competes with its parent on virtual books; the weaker
  one is removed. This replaces "retire the parent persona".
* The automatic consumer's retire action changes target from the persona lifecycle to "remove pool member".
  The persona lifecycle is only changed by the operator.

### 5.5 Persona cycle

Runs on the existing persona cron and writes the existing OODA record with real content: observed pool
health, the orientation (which measure is short), the decision (demand, admit, re-weight, mutate, remove,
widen P, or no action with the reason), the action receipts, and what was learned. Research demands are
written by the persona (mandate, missing datasets, tried families, which measure is short), not by a
central allocator. Findings and outcomes go to the persona's own memory.

## 6. What happens to today's tasks

| Task | Outcome |
| --- | --- |
| `DISCOVERY-ALLOCATOR-RUNS-ADMITTED-STRATEGY-20261112` | superseded 2026-11-12; PR #7207 not merged |
| `DISCOVERY-GATE-PASS-APPROVED-BASELINE-20261112` | superseded and reopened under §5.2 |
| `RESEARCH-DEMAND-RECORD`, `DISCOVERY-EVOLUTION-SCHEDULER`, `FINDING-SPONSOR-PERSONA`, `TEMPLATE-DEMAND-SEARCH`, `FE-RESEARCH-DEMAND-VIEW`, `DISCOVERY-W5-HOSTED-ACCEPTANCE` (all `-20261112`) | superseded; reopened as pool tasks in §7 |
| `L2-DEMAND-PRIORITY`, `COLLECTOR-DEMAND-QUERY-PLAN`, `GATE-MECHANISM-STAGE` (`-20261112`) | model-independent; reopened with dependencies on the new demand record |
| `HYPOTHESIS-CARD-PREDICTIONS-20261112` | unaffected |
| The discovery allocator (`services/research/discovery_allocator.py`) | switched off and removed in wave 2; the persona cycle takes over its role |

## 7. Delivery waves

| Wave | Task (date suffix at opening) | Scope |
| --- | --- | --- |
| 1 | `PERSONA-STRATEGY-POOL-RECORD` | §4.1 record and read; the three personas' current momentum artifact becomes member 1 |
| 1 | `PERSONA-PORTFOLIO-COMBINATION` | §4.2 envelope, combination function, virtual books, backtest/paper parity |
| 1 | `VIEW-UNIVERSE-IMPLEMENTATION` | §4.3 with `SD_VIEW_UNIVERSE_CROSS_SECTIONAL.md`; permitted list P and widening |
| 1 | `DISCOVERY-ALLOCATOR-OFF` | disable the allocator's capital-only binding now (small, first) |
| 2 | `POOL-MEMBER-BASELINE` | §5.2 |
| 2 | `POOL-ADMISSION-AND-WEIGHTS` | §4.4, §5.1, §5.3 |
| 2 | `EVOLUTION-POOL-MEMBER-ACTIONS` | §5.4 (retire → remove member; child as trial; cadence) |
| 3 | `PERSONA-AUTONOMY-CYCLE` | §5.5 on the persona cron with OODA records and persona-written demands |
| 3 | `L2-DEMAND-PRIORITY`, `COLLECTOR-DEMAND-QUERY-PLAN`, `GATE-MECHANISM-STAGE`, `TEMPLATE-DEMAND-SEARCH` | as designed on 2026-11-12, reading persona demands |
| 3 | `BFF/FE-PERSONA-POOL-VIEWS` | persona page shows the pool, member virtual books, pool health and the cycle record |
| 4 | `PERSONA-AUTONOMY-HOSTED-ACCEPTANCE` | one of the three personas, on hosted and without operator action: opens a demand from low utilisation, admits a trial member, re-weights, removes a decayed member and keeps running |

Every acceptance keeps the rules already adopted: the production path calls the code (integration test
through the real entry point); missing data fails closed, never synthetic; recorded real responses for
external sources; hosted acceptance on natural events only.

## 8. Risks

| # | Risk | Control |
| --- | --- | --- |
| R1 | Utilisation targets pull weak strategies in | admission never depends on utilisation (§5.1); idle budget is an accepted outcome |
| R2 | Few symbols cap the effective number of bets | universe widening (§4.3) is a first-class persona action |
| R3 | Short paper history makes weights and correlations noisy | use backtest returns for admission; bounds and a 20-day minimum before tilting |
| R4 | Overfitting from many trials per persona | per-persona trial deflation, untouched out-of-sample window, fresh-data revalidation |
| R5 | Netting hides a member's losses | virtual book per member is the evaluation basis; attribution page shows both |
| R6 | Pool membership read at decision time weakens "artifact = what runs" | pool version and member checksums are hashed into each decision key; reviewer to confirm this meets registry governance, otherwise each pool version becomes a new envelope artifact |
| R7 | Baseline comparison across instrument classes | member virtual books are in underlying terms; the persona account keeps its instrument-class baselines |

## 9. Sources (read 2026-11-12)

mergersandinquisitions.com/multi-manager-hedge-funds; youngandcalculated.substack.com (senior PMs at
multi-managers); resonanzcapital.com/insights/sleeve-management-running-a-liquid-alternatives-portfolio-like-a-pod-shop;
arXiv 1601.00991 (101 Formulaic Alphas); arXiv 2103.16196 (AlphaEvolve); arXiv 1501.05381 (Combining Alphas
via Bounded Regression); quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/portfolio-construction/key-concepts;
portfoliooptimizer.io/blog/the-effective-number-of-bets-measuring-portfolio-diversification; Meucci,
"Managing Diversification"; SSRN 2326253 (The Probability of Backtest Overfitting).
