# SD-DISCOVERY-DEMAND-EVOLUTION-20261112 — personas ask for research, test mechanisms and evolve on schedule

| Field | Value |
| --- | --- |
| Status | Draft for operator decision (O-13 … O-20) |
| Date | 2026-11-12 |
| Extends | `SD_STRATEGY_DISCOVERY_SOURCES_2026-11-10.md` (merged #7125) §8, §10, §11, §12 |
| Repo base | `origin/dev` e28bef7e4 (all cited files identical to 32e7556fc, where they were read) |
| Hosted reads | read-only: `/data/research-orchestrator/discovery_allocator_runs.jsonl` on the dev VM |
| Scope | design only; no source, test, workflow, configuration or infrastructure change |

---

## 0. 中文摘要（操作者用）

**為什麼要寫這份：** 11/12 你問了四個問題：「persona 什麼時候知道要找新策略」、「怎麼知道要驗證哪些方向」、「演化怎麼產生」、「執行怎麼知道怎麼設定」。我對照程式查了一遍，發現 11/10 的設計只解決了「有東西可以找、可以驗、可以演化」，沒有解決「由誰、在什麼時候發起」。

**已經另外開任務修的兩個斷點（不在本文件範圍）：**
- `DISCOVERY-ALLOCATOR-RUNS-ADMITTED-STRATEGY-20261112` 修 allocator 的三個問題：
  - 它把「進入重現佇列」當成「通過驗證」；
  - 它只建資金綁定，不會啟動 runtime，所以 persona 根本沒有在跑那個策略；
  - 它會把青禾、衡岳、澄川和部署探針也當成名額。
- `DISCOVERY-GATE-PASS-APPROVED-BASELINE-20261112`：閘門通過的樣本外結果要變成已核准基準。沒有基準的話，衰退判斷永遠不會觸發，策略不會退役，名額也不會空出來。

**本文件處理的四個缺口：**
1. **找策略只看資料來源，不看 persona 缺什麼**（§2.1、§3.1）。抓取主機的查詢條件是寫死的，例如 arXiv 每天抓最新 10 篇、OpenAlex 只查一個概念。L2 每一筆都萃取，不管哪個 persona 缺什麼。persona 的 mandate（市場、持有期、策略類型、風險）從沒被拿來決定要找什麼。
2. **驗證只檢查「規則賺不賺」，不檢查「機制對不對」**（§2.3、§3.2）。一張假說卡只會編譯成一條規則，沒有附帶可以檢驗的其他預測。
3. **演化只在表現變差時發生，而且 mutate 和 revalidate 沒有人提案**（§2.4、§3.3）。執行器已經存在，但程式裡沒有任何地方會自動建立這兩種提案。通過驗證的子代也不會替換正在跑的父代。
4. **執行設定是固定的**（§2.6、§3.4）。商品選擇的反事實紀錄只寫不用；「標的與方向在交易時決定」這一項你 11/07 已決定延後。

**做法摘要：**
- **需求單（§3.1）：** persona 名額空著、找不到合格候選時，allocator 寫一張需求單，內容是 mandate 加上缺少的資料集。需求單有三個用途：
  - 決定 L2 萃取的先後順序（不增加 400 次的每日上限）；
  - 產生每週的查詢計畫，交給抓取主機；
  - 驅動因子庫（Alpha158／Alpha101）在 mandate 的範圍內直接產生候選。
- **假說卡加上預測（§3.2）：** 每張卡要寫出 2 到 4 個「如果機制對，應該還會看到什麼」，例如公布日前後的反應、景氣燈號不同時的差異、劑量反應。閘門只在樣本內檢驗這些預測，任何一個被推翻就不准入。
- **定期演化（§3.3）：** 在同一個 evolution 排程裡加兩件事：每 20 個交易日拿新資料重新驗證一次，每週在試驗預算內做一次結構變異或參數搜尋。子代除了要過原本的樣本外檢驗，還要通過准入之後新產生的資料；通過後才可以替換表現落後的父代。
- **執行學習（§3.4）：** 建議延後，等反事實紀錄累積滿 20 個交易日再設計。

**要你決定的（§6）：** 需求單在什麼情況下產生（O-13）、查詢計畫要不要人審（O-14）、機制檢驗的嚴格程度（O-15）、重新驗證的週期（O-16）、探索的預算（O-17）、子代怎麼替換父代（O-18）、執行學習要不要延後（O-19）、需求單要不要用 LLM（O-20）。

---

## 1. Operator statements (verbatim)

2026-11-11/12:

> 那什麼時候persona知道自己要找新的策略? 並且從這些資料來源裡面去找策略?
> 找完以後怎麼知道需要驗證哪些方向才知道自己的策略猜想是對的?
> 演化怎麼產生的?
> 執行怎麼知道要如何設定?

After the coordinator's answer (two broken links for tasks, the rest for a design): 「好」.

---

## 2. Current state (origin/dev, verified 2026-11-12)

### 2.1 Search is supply-driven

* Collector queries are fixed in code and policy: arXiv `search_query=cat:q-fin.*+OR+cat:stat.ML&max_results=10`
  (`deploy/collector/collector_stack/pollers.py:566`), OpenAlex one concept filter, 10 per page
  (`pollers.py:658`), Threads a configured keyword (`spiders.py:110`), categories in
  `deploy/collector/fetch-policy.json` (`collector-paper-arxiv.categories`).
* L2 extraction runs over every eligible record under the O-7 daily cap, with no input from any persona
  (`services/source_ingestion/hypothesis_card.py`); it is off by default
  (`docker-compose.yml:359`, `PANTHEON_L2_EXTRACTION_ENABLED:-0`).
* The compiler counts `needs_data:<dataset>` per dataset (`services/source_ingestion/hypothesis_compiler.py:36`,
  table `needs_data_counts`, `:109`, `:160`, `:168`). Nothing outside the compiler reads the counts.
* The persona profile used for matching already carries what a search would need: `mandate_terms`,
  `strategy_families`, `preferred_markets`, `asset_classes`, `holding_periods`, `risk_constraints`,
  `data_availability_scope` (`services/control-plane/persona/persona_strategy_discovery.py:124-140`).
  It is used only to score finished candidates (`match_persona_strategies`, `:729`).

### 2.2 When a persona "needs" a strategy today

Only two moments, both passive:

* the daily allocator finds a free discovery slot (`services/research/discovery_allocator.py:15-17`,
  cap 5, minimum score 50); if no candidate scores ≥ 50 the slot stays empty and nothing else happens;
* a decay retire frees a slot (`services/evolution/decay_monitor.py`), which needs an approved baseline
  (task `DISCOVERY-GATE-PASS-APPROVED-BASELINE-20261112`).

Hosted (read-only, 2026-11-12): `discovery_allocator_runs.jsonl` has 4 runs since 2026-11-11T20:22Z; each
saw the same 4 legacy admissions (SPY anchor, a 2330 daily-price draft, two acceptance-chain specs) and
stopped on a start-up DNS race to `persona`. No binding was ever made.

### 2.3 Validation tests one rule per card

* `hypothesis_card.v1` fields: `claim`, `mechanism`, `signal_inputs`, `rule_sketch`, `horizon`, `universe`,
  `direction`, `conditions`, `cited_spans`, `testability`, `origin`
  (`docs/contracts/hypothesis_card.v1.schema.json`). There is no field for implied, independently
  testable predictions.
* The gate judges the compiled rule's backtest: out-of-sample Sharpe after costs, deflated threshold,
  buy-and-hold and 0050 benchmarks, walk-forward folds (`services/research/replication/gate.py:775-935`).
  A rule that earns money for a reason other than the stated mechanism passes.
* Research findings are published with `sponsor_persona_id="persona-tw-equity"` hard-coded
  (`services/research/alpha_replication/revalidation_worker.py:822`), so a finding never reaches the
  persona that will trade it.

### 2.4 Evolution triggers

| Action | Executor | Who proposes it automatically |
| --- | --- | --- |
| retire | `auto_consumer.py` governed lifecycle | decay monitor (`decay_monitor.py`; `evaluate_paper_decay` `auto_consumer.py:405`, `build_retire_proposal` `:442`) — blocked by the missing baseline |
| retrain | research bounded search (`evolution_search.py:186`, budget 50 `:27`) | incident and postmortem sweeps (`services/evolution/postmortem_bridge.py:76`, `:109`; action paths `services/evolution/main.py:2766`) — fire only on decline |
| mutate | `evolution_executor.py`; research `main.py:1617`, `:2365` | **nobody** (no production code creates mutate proposals; the smoke tests only exercise execution of a `revalidate` action, not proposal creation: `services/control-plane/bff/smoke_test.py:781`, `services/runtime-manager/smoke_test.py:1273`) |
| revalidate | `evolution_executor.py` | **nobody** |

The auto consumer only approves proposals created by `evolution_controller` (`auto_consumer.py:197`), so an
operator-created mutate proposal is never auto-approved (correct) but also means mutate never runs unless the
operator acts.

### 2.5 Children

A passing child is registered as a new candidate with lineage (`evolution_executor.py` docstring). It does
not replace the running parent; it waits for a free slot. Every child is judged on the parent's original
out-of-sample window; repeated selection on that window is selection on out-of-sample data, only partly
offset by the trial-count deflation.

### 2.6 Execution settings

Rule parameters are fixed at the validated values; the 2026-11-07 expression layer picks stock / stock
future / ETF future / no-trade deterministically; the counterfactual of each choice is recorded
(`services/execution/lean_runtime/paper_runtime.py`, `expression_replay.py`) and read by the attribution page,
but no component adjusts the expression policy from it. Q4 (choose symbol and direction at trade time) was
deferred by the operator on 2026-11-07.

---

## 3. Design

### 3.1 Research demand: persona → search

**Record.** A new contract `research_demand.v1`, written by the allocator (same scheduled run, same record
file family) when:

* a discovery slot is free and no candidate reaches the minimum score (O-13 a), or
* a discovery persona was retired by decay (O-13 b; the demand inherits the retired family as "tried").

Fields: `demand_id`, `tenant_id`, `created_at`, `mandate` (the discovery mandate profile fields of §2.1),
`best_candidate_score` and its blockers, `missing_datasets` (top `needs_data` counts that intersect the
mandate's markets), `tried_families` (families with exhausted trial budget or retired in the last 90 days),
`status` (`open` / `satisfied` / `expired` after 30 days).

**Consumers (one each; no new scheduler):**

1. *L2 priority.* The extraction worker orders pending records by demand relevance before taking its daily
   budget: a record scores by controlled-vocabulary overlap between its metadata/abstract and the open
   demands' `mandate_terms` and `strategy_families`. The daily cap (O-7, 400) does not change; demand only
   changes the order. No open demand → today's order.
2. *Collector query plan* (O-14). Weekly, the open demands produce a query plan for API sources only
   (arXiv, OpenAlex, GDELT, NBER RSS filter): terms are drawn from a fixed vocabulary
   (strategy family names, dataset names, Taiwan market terms), never from model output or fetched text.
   Social sources are not expanded by demand. The plan reaches the collector as a reviewed change to
   `deploy/collector/fetch-policy.json` (recommended first) or an authenticated pull by the collector (later),
   so Pantheon keeps a single egress host and the collector keeps no Pantheon write path.
3. *Template search.* For each open demand, the existing AlphaTemplate library
   (`services/registry/alpha_template.py`; Alpha158 and Alpha101 time-series, registered at start-up by
   `ensure_builtin_alpha_templates` `:399`) is filtered to templates whose inputs exist for the mandate's
   universe; each is compiled to `factor_rule_v1` and sent through the §10 gate with its own family trial
   counter. This is the persona-initiated search that does not depend on external text.

**Read surface.** The research items page shows open demands, what each consumer did with them and which
candidate satisfied them (existing BFF research read route; no new page).

### 3.2 Mechanism tests

**Card.** `hypothesis_card.v1` gains an optional `predictions[]` (additive; v1 cards without it stay valid):

```json
{
  "statement": "Stocks with accelerating monthly revenue rise in the 5 trading days after the revenue release",
  "observable": {"dataset": "tw_monthly_revenue", "field": "yoy"},
  "test_kind": "event_window",
  "expected_sign": "+",
  "window": {"before": 0, "after": 5}
}
```

`test_kind` ∈ `event_window` (returns around the input's release date), `regime_split` (sign holds in each
NDC business-cycle signal band or 0050 above/below its 200-day mean), `dose_response` (higher signal
quantile → larger mean forward return), `lead_lag` (the input leads returns, not the reverse),
`cross_symbol_sign` (sign agrees across most symbols of the universe). The extractor prompt asks for 2–4
predictions that follow from the stated mechanism; each must cite a span like every other card field.

**Gate stage.** Before the out-of-sample backtest, the gate runs each prediction as a fixed test on the
**in-sample window only** (no peek at out-of-sample), Holm-corrected within the card, and records
`supported` / `contradicted` / `inconclusive` with the statistic. Admission (O-15 recommendation b):

* the primary rule passes §10 as today, and
* no prediction is `contradicted`;
* `mechanism_support = supported / testable` is recorded and feeds the existing `evidence_quality` factor of
  `persona_strategy_match`; a card with no testable prediction is admitted as `mechanism_untested` and
  ranked lower, not rejected.

Template-derived candidates (§3.1.3) have no mechanism text; they are always `mechanism_untested`.

### 3.3 Evolution on schedule

**Producer.** A discovery evolution scheduler inside the existing auto consumer tick
(`services/evolution/auto_consumer_runtime.py:240`, same process as the decay monitor; no second scheduler).
For each live discovery artifact it proposes, as `evolution_controller` with the discovery marker and
`risk_level=low`, so the existing auto gate approves them:

| Proposal | When | Budget |
| --- | --- | --- |
| `revalidate` | every 20 trading days (O-16), on the data that arrived since admission | 1 run |
| `mutate` (factor_rule_v1) or `retrain` (momentum / template) | weekly (O-17) | 50 variants per decision (existing default); family lifetime cap 500 trials, after which the family is `tried` |

Existing cooldowns apply (`services/evolution/cooldown_enforcement.py:44-60`: retrain and revalidate 72 h).

**Fresh-data rule for children.** A child is judged (1) on the original out-of-sample window with the
deflated threshold, as today, and (2) once at least 60 trading days have passed since the parent's
admission, on that post-admission window, which no search has seen. Only a child that passes both is
eligible to replace a parent.

**Replacement** (O-18 recommendation b). If an eligible child exists and the parent's paper Sharpe since
admission is below its approved baseline, the scheduler proposes a parent `retire` with reason
`superseded_by_child` and lineage to the child; the next allocator run binds the child. No in-place swap and no
new runtime path: retire and bind are the existing paths.

**Revalidation failure.** A parent that fails `revalidate` on post-admission data gets a decay-scoped retire
proposal with reason `revalidation_failed` (same auto gate as decay).

### 3.4 Execution learning (recommended: defer)

When 20 trading days of counterfactual records exist, a separate design can propose expression-policy
parameter changes (for example `lever_min_strength`) as evolution proposals judged by expression replay.
Until then the policy stays fixed. Q4 stays deferred as decided on 2026-11-07.

### 3.5 Finding sponsor

`research_finding_published` names the persona that will run the strategy (set when the allocator binds it)
instead of the hard-coded `persona-tw-equity`; before binding it names no persona.

---

## 4. What does not change

One intake door (collector bundle connector); one rule format and one evaluator; one admission gate
(gaining a stage); one evolution consumer (gaining a producer); allocator and provisioning paths as fixed by
`DISCOVERY-ALLOCATOR-RUNS-ADMITTED-STRATEGY-20261112`; baselines as fixed by
`DISCOVERY-GATE-PASS-APPROVED-BASELINE-20261112`. Nothing here acts beyond paper.

---

## 5. Proposed tasks (opened after the operator decides §6)

| Id (date suffix at opening) | Scope | Depends on |
| --- | --- | --- |
| `RESEARCH-DEMAND-RECORD` | §3.1 record, allocator writer, BFF read on research items | allocator task |
| `L2-DEMAND-PRIORITY` | §3.1.1 | demand record |
| `COLLECTOR-DEMAND-QUERY-PLAN` | §3.1.2 per O-14 | demand record |
| `TEMPLATE-DEMAND-SEARCH` | §3.1.3 | demand record, baseline task |
| `HYPOTHESIS-CARD-PREDICTIONS` | §3.2 card field and extractor prompt | — |
| `GATE-MECHANISM-STAGE` | §3.2 gate stage and admission rule; serialised after `INTRADAY-REPLICATION-GATE-20261111` (same files) | card predictions |
| `DISCOVERY-EVOLUTION-SCHEDULER` | §3.3 producer, fresh-data rule, replacement | baseline task |
| `FINDING-SPONSOR-PERSONA` | §3.5 | allocator task |
| `DISCOVERY-W5-HOSTED-ACCEPTANCE` | a demand satisfied by a template or card candidate; a contradicted prediction blocks admission; a scheduled revalidate and a mutate child observed on hosted; a superseded parent replaced through retire and bind | all above |

Every acceptance includes "the production path calls it" with an integration test, and "missing data fails
closed; no synthetic data in production code".

---

## 6. Open questions for the operator

| # | Question | Options | Recommendation |
| --- | --- | --- | --- |
| O-13 | When is a demand created | (a) free slot with no candidate ≥ min score; (b) also after a decay retire; (c) also on a fixed weekly schedule | (a) + (b) |
| O-14 | How demand reaches the collector | (a) weekly plan as a reviewed fetch-policy change; (b) collector pulls the plan automatically with a token | (a) for the first four weeks, then (b) with the same vocabulary |
| O-15 | Mechanism strictness | (a) report only; (b) a contradicted prediction blocks admission; (c) also require ≥ 1 supported prediction | (b) |
| O-16 | Revalidation cadence | 10 / 20 / 60 trading days | 20 |
| O-17 | Exploration budget | weekly per artifact, 50 variants per decision, family lifetime cap | weekly, 50, lifetime 500 |
| O-18 | Child replacing parent | (a) child waits for a free slot (today); (b) retire a lagging parent with `superseded_by_child` when an eligible child exists | (b) |
| O-19 | Expression-policy learning | (a) defer until 20 trading days of counterfactuals; (b) design now | (a) |
| O-20 | Model use for demand → query terms | (a) none, fixed vocabulary; (b) model-proposed terms with operator review | (a) |

---

## 7. Risks

| # | Risk | Control |
| --- | --- | --- |
| R1 | Overfitting through repeated out-of-sample judging of children | family trial deflation; fresh post-admission window (§3.3); lifetime cap |
| R2 | Demand terms steering collection toward manipulable sources | API sources only; fixed vocabulary; social sources never expanded by demand |
| R3 | Mechanism tests as a new multiple-testing surface | in-sample only; Holm within card; tests fixed by `test_kind`, not chosen per card |
| R4 | Template search flooding the gate | per-family trial counter; demand expiry after 30 days |
| R5 | Silent wiring gaps (seen in waves 1–4) | every task's acceptance requires the production caller and an integration test |
| R6 | Replacement churn | cooldowns; replacement only when the parent is below its approved baseline |

## 8. Sources read (2026-11-12, origin/dev)

`deploy/collector/collector_stack/pollers.py`, `spiders.py`, `deploy/collector/fetch-policy.json`,
`docker-compose.yml`, `services/source_ingestion/hypothesis_card.py`, `hypothesis_compiler.py`,
`docs/contracts/hypothesis_card.v1.schema.json`, `services/control-plane/persona/persona_strategy_discovery.py`,
`services/research/discovery_allocator.py`, `services/research/main.py`,
`services/research/alpha_replication/revalidation_worker.py`, `evolution_search.py`, `evolution_executor.py`,
`services/research/replication/gate.py`, `services/evolution/auto_consumer.py`, `auto_consumer_runtime.py`,
`decay_monitor.py`, `cooldown_enforcement.py`, `postmortem_bridge.py`, `main.py`,
`services/registry/alpha_template.py`, `services/execution/lean_runtime/paper_runtime.py`,
`expression_replay.py`, `services/control-plane/bff/persona_provisioning_coordinator.py`,
`services/control-plane/bff/personas/routes/collection.py`, `services/paper_fleet_reconciler/paper_fleet_reconciler.py`.
