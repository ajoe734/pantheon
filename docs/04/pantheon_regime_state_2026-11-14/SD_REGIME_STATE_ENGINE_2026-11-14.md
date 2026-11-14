# SD-REGIME-STATE-ENGINE-20261114 — produce RegimeState as planned: a shared market-state measurement plus per-strategy regime records

- Status: owner-verified (Claude) on base `a9284b68c`; see §10. Design only; `hosted_behavior_claimed: false`.
- Base read: `origin/dev` `a9284b68c`.
- Owned layer: design baseline. Not changing: product runtime, policy authority, hosted claims, task state.
- Composes with: `SD_MEMORY_HINDSIGHT_ADOPTION_2026-11-13.md` H4 (regime anchor "when present"), `SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md` D6 (macro-regime consult rule stays off until a governed regime signal exists).
- All former dagger references were re-read by the task owner on the delivered base; results in §10.

## 0. 中文摘要（操作者用）

**為什麼有這份：** 11/14 我沒讀規格就提了「每天用加權指數跑一個模型」，操作者指出這樣很粗糙、而且規格書早就討論過。這份是讀完規格、盤點完資料、查完開源做法之後重做的設計。

**規格定了什麼：** 盤勢（RegimeState）是決策鏈五段的第一段，屬於決策層；每筆紀錄掛在某個策略下；看一段觀察期間；使用者是選股範圍、訊號、資金配置、風控、persona 的 Orient、會診規則、事後歸因。

**規格沒定、這份補上的：**
1. 分兩層：全市場共用的「市場狀態量測」（事實），加上每個策略自己的盤勢紀錄（照規格）。
2. 分面向各自判斷：波動、趨勢、風險偏好、總經。九種分類不改，每筆紀錄的標籤由該策略在意的面向導出。
3. 每個面向每天輸出：目前狀態與機率、已持續幾天、預期還會持續多久、轉換機率。只用到當天為止的資料。
4. 方法用成熟開源零件：statsmodels 馬可夫切換（波動）、統計跳躍模型 jumpmodels（趨勢、風險偏好）、規則（總經）；盤中轉換警示放第二階段。
5. 研究層訓練與驗證模型；決策層的 regime evaluator 放在 registry 服務裡當一個模組，每天用驗證過的模型產生紀錄。
6. 不新增排程：每日循環第一次要用時計算並存下來。
7. 資料不夠就不產生紀錄，不放佔位值。

**現況：** 沒有任何程式產生盤勢；加權指數、類股指數、台指期價量、台灣 VIX 都沒進系統；融資、期貨法人部位、put/call 有程式沒排程。所以要先補資料。

**不在這份裡：** 配置的「盤勢適配度」怎麼算、選股範圍與訊號的產生者（這兩段本身還不存在），各自另開設計。

## 1. Operator statements (2026-11-14, verbatim)

Transcript: `/home/chloe_ong_dev_cctech_support_com/.claude/projects/-home-chloe-ong-dev-cctech-support-com-code-pantheon/cb4f6795-9064-4bb2-ab77-c0740122bce1.jsonl`

| Line | Time (UTC) | uuid | Statement |
|---|---|---|---|
| 3112 | 00:04:25.360Z | 67c9314d-a0ff-4764-a682-3f06dee93de1 | 另外那四個問題你要提出建議啊 |
| 3284 | 01:56:14.104Z | 729d80ba-0880-41a2-95eb-080ff3a7b879 | 盤勢產生器是什麼？ |
| 3309 | 02:21:30.774Z | 9f92cc1d-16b0-4c87-81b3-6693c9dc5f39 | 奇怪盤勢產生器只有看一天一天的嗎？我感覺你在做regime 但是又做得很粗糙 |
| 3344 | 02:25:45.067Z | 19b70b12-8708-4af2-a18d-4e7ead2d99a4 | 等一下，你到底是不是在做regime?這個規格書裡面有討論過吧？你有沒有看過還是自己亂做？ |
| 3462 | 02:58:53.888Z | fe0be987-e28e-4973-b0d6-ad3ef9b252ef | 兩個都要做啊，幹 |

Line 3462 answers the coordinator's proposal at line 3453 (02:31:43.583Z, uuid `1263bc63-0296-4ce9-9cfa-d038b7104ceb`), which ended by asking whether to write this design document and to open the data tasks first.

## 2. What the original plan defines

| Topic | Plan text | Source |
|---|---|---|
| Position | `RegimeState` must become a first-class decision object; owner plane is the Decision Plane; primary services include a "regime evaluator"; acceptance is "至少一個 strategy family 跑出完整五級決策鏈 replay" | `Pantheon_Blueprint_Gap_Review_v1.md:405-447` |
| Chain | Stage 1 of RegimeState → UniverseSelection → SignalInference → AllocationDecision → RiskAdjudication | `services/registry-core/decision-domain/regime_state.schema.json` (description); `universe_selection.schema.json:5` |
| Persona | "Orient: regime assessment、strategy ranking、risk budget…" | `Pantheon_總索引版系統分析文件.md:401` |
| Scope | `strategy_id` required; `persona_id`, `capital_pool_id`, `market_scope` optional | `regime_state.schema.json` (`:27-29`, `:204-248`) |
| Window | one `window_start`/`window_end` per record | `regime_state.schema.json` (`:75-84`) |
| Low confidence | confidence ≤ 0.3 must be `regime_transition` or `mixed` | `regime_state.schema.json:281-297` |
| Inputs | `input_refs` types dataset_version, security_master, contract_master, market_calendar_session, feature_dataset, telemetry_summary, external_macro; `feature_snapshot` volatility_regime, trend_strength, momentum_signal, macro_environment | `regime_state.schema.json` (`:93-121`, `:185-201`) |
| Taiwan features | "institutional consecutive buy/sell days", "margin and lending pressure", "TAIFEX OI and put/call regime", "macro regime features" | `docs/04/pantheon_data_strategy_source_design_2026-06-09/MARKET_DATA_COMPLETION_PLAN_2026-06-11.md:510-516` |
| Methods | `model_family`: hmm, markov_switching, random_forest, gradient_boosting, neural_classifier, rule_based, ensemble, custom | `regime_state.schema.json` (`:153-171`) |
| Research role | statsmodels output is a draft research artifact; "real-time econometric execution or online state estimation" is out of scope | `services/research/statsmodels/ACTIVATION_CRITERIA.md:160-166` |
| Module placement | `regime-evaluator` stays a module inside a service; the host service is not named | `Pantheon_GCP_GitHub_Docker_正式部署與環境設計_v2.md` (`:371-381`) |
| Universe | `regime_ref` required | `universe_selection.schema.json:12,38-48` |
| Allocation | "5.5 Regime conflict: A 判定 risk-on / B 判定 risk-off"; `effective_weight = reliability_score * regime_fit * confidence * …`; "regime fit calculation" deferred | `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md:175-209`, `:310-316` |
| Consult | "遇 macro regime shift 時需 consult macro analyst"; the rule "stays off until a governed regime signal exists" | `PERSONA_RUNTIME_MODEL.md:178`; `SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md` D6 |
| Attribution | post-mortems must separate "regime 判錯、universe 選錯、還是 alpha model 退化" | `Pantheon_Blueprint_Gap_Review_v1.md:418` |
| Lifecycle | `state`: proposed, validated, active, superseded, invalidated | `regime_state.schema.json` (`:231-280`) |
| Known defect | `regime_scores` in the example sums to 2.40; the `regime_class` enum "mixes orthogonal dimensions" (direction, behaviour, volatility) | `services/registry-core/decision-domain/review_bg003_qwen.md:74,76` |

Not specified in the plan: evaluation frequency, multiple horizons, intraday, transition probabilities and durations, who moves a record between states, the host service, how regime fit is calculated, validation requirements for the classifier.

## 3. Verified current state

**No producer and no store.**
- `services/registry-core/` contains only the decision-domain schemas, examples and `validate_schemas.py`. No migration creates `regime_states`; only `GOLDEN_REPLAY_SCENARIO_AND_RUNBOOK.md:173` expects it.
- No service writes any decision-domain object (search of `services/**/*.py` on the base).

**Fabricated references.**
- `services/control-plane/ooda/paper_loop_packet.py:519`: `regime-state://tw-paper-neutral-001`.
- `services/persona/ooda_cycle_runtime.py:480`: `regime://persona-ooda15/{persona_id}/{_research_stage(persona)}` (management batch simulation).
- `services/persona/cron_ooda_closure.py:364`: the turn reference (legacy force-run path).
- `services/optimizer-svc/portfolio_synthesis/models.py:142`: `regime_fit_score` defaults to 1.0.
- `services/knowledge/case_file.py:68`: `regime_tag=""`; none of the three constructors sets it.
- The real daily cycle (`services/persona/persona_cycle_controller.py` `consume_run`) never sets `orient.regime_state_ref`.

**Research backend.**
- `services/research/statsmodels/adapter/statsmodels_adapter.py:236-250`: the real backend fits `MarkovRegression(k_regimes=2, trend="c", switching_variance=True)` on one series and returns `smoothed_marginal_probabilities.argmax`. Smoothed probabilities use later observations, so they cannot label a decision date. Probabilities, the transition matrix and durations are discarded. The default backend returns alternating 0/1 labels.
- `regime_model` is an enum value with no routing. No gate validates a regime classifier.

**Registry service.** `services/registry` is a deployed service (`http://registry:8087` in `docker-compose.yml`) with Postgres storage (`pg_store.py`, `storage.py`), strategy specs and artifacts, an artifact registry with `model_artifact` and `feature_set` types and draft → candidate → approved → retired states (`services/registry/contract.md`), and it already reads the feature catalogue (`services/registry/sector_features.py`).

**Data (survey of the base).**

| Family | State |
|---|---|
| TAIEX level, sector indices, TPEx index | absent. The collector downloads TWSE `MI_INDEX?type=ALLBUT0999` daily but `deploy/collector/collector_stack/spiders.py:936-960` keeps only tables with 證券代號/收盤價 |
| Whole-market daily bars | code merged (`COLLECTOR-PRICE-HISTORY-BACKFILL-20261111`, `COLLECTOR-HOSTED-INTAKE-THROUGHPUT-20261113`, `COLLECTOR-STEADY-PULL-RESTORE-20261112`); hosted held 104 symbols and 60 sessions on 2026-11-11 and waits for an accepted release |
| Institutional flows | TWSE T86 through the collector; TPEx coded, not scheduled |
| Margin balances | coded, not scheduled |
| TAIFEX institutional futures positions, put/call ratio | coded, not scheduled |
| TX futures price and open interest, Taiwan VIX | absent |
| USD/TWD, NDC monitoring score and indexes | collected |
| Minute bars | coded, never run on hosted; `INTRADAY-*-20261111` tasks blocked |
| History API | none; consumers read normalized files; `feature_catalogue.read_feature_series` gives point-in-time reads over caller-supplied observations |

**Schedulers.** OpenClaw cron `pantheon.persona.daily-cycle` (`services/control-plane/cron/workflows.py:9-12`) runs after the Taiwan refresh; the source-ingest controller works on the latest closed session. There is no research scheduler.

**Consult triggers.** `services/persona/consult_triggers.py` has no macro-regime rule; the BFF already accepts `macro_regime_shift`.

## 4. Open-source building blocks and established practice

Licences and last activity re-checked by the task owner on 2026-11-14 through the GitHub repository API (`license`, `pushed_at`, commits) and the PyPI JSON API, plus the raw statsmodels source and the jumpmodels README on GitHub. Each row states its own sources.

| Block | What it gives | Licence and activity | Limits |
|---|---|---|---|
| statsmodels `MarkovRegression` | Hamilton filter and Kim smoother kept separate (`filtered_marginal_probabilities`), `switching_variance`, `expected_durations`, time-varying transition probabilities (`exog_tvtp`) | BSD-3-Clause (GitHub API `license.spdx_id`); PyPI 0.15.0 uploaded 2026-08-27; last push 2026-11-11; checked 2026-11-14 | no incremental update; fragile as regimes grow. Names `filtered_marginal_probabilities`, `expected_durations`, `exog_tvtp` confirmed in `statsmodels/tsa/regime_switching/markov_switching.py` on `main` 2026-11-14 |
| `jumpmodels` (github.com/Yizhan-Oliver-Shu/jump-models) | statistical jump model, continuous (CJM) and sparse (SJM) variants; a penalty on each state change gives persistence; `predict_online` / `predict_proba_online` use only earlier rows; states sorted by return or volatility | Apache-2.0 (GitHub API); PyPI 0.1.1 uploaded 2024-10-04; last push 2025-01-12; 173 stars; checked 2026-11-14 | one maintainer; the discrete model's online probabilities are one-hot (design inference from the model definition, not re-tested), only the continuous model gives a probability vector; refit timing is the caller's job |
| Bayesian online change-point detection (github.com/hildensia/bayesian_changepoint_detection) | posterior over time since the last change | MIT (GitHub API); last push 2026-11-06 (commit log shows release 1.2.2 on 2026-11-06); the PyPI package `bayesian-changepoint-detection` is 0.2.dev1 from 2019-08-12, so the current code is not the PyPI release; checked 2026-11-14 | hazard rate needs tuning; install from git only |
| `ruptures` | offline change points | BSD-2-Clause (GitHub API; PyPI 1.1.10 uploaded 2025-09-10); last push 2026-10-06; checked 2026-11-14 | offline only; audit use |
| `hmmlearn` | Gaussian HMM | BSD-3-Clause (GitHub API); PyPI 0.3.3 uploaded 2024-10-31; last push 2024-10-31; checked 2026-11-14 | `predict_proba` is smoothed (not re-tested here); baseline only |

Practice the design adopts (sources: the paper abstract at arxiv.org/abs/2402.05272 was read 2026-11-14 and confirms only that the jump penalty enhances regime persistence; every other figure below is **unconfirmed by the owner**, the full text could not be parsed in this environment):
- Filtered or online inference only; never smoothed probabilities for a decision date (design rule; statsmodels exposes both, see the first row).
- Persistence is modelled, not thresholded. Unconfirmed: the figure "a 2-state HMM switched 2 to 8.5 times a year against under 1 for the jump model" (Shu, Yu, Mulvey, arXiv:2402.05272).
- Several horizons through features with different lookbacks. Unconfirmed: the paper's "downside deviation with a 10-day half-life; Sortino ratios with 20- and 60-day half-lives"; Nystrup, Kolm, Lindström 2020 on intraday realised volatility (not opened).
- Separate axes estimated separately: Kritzman et al. 2012 (turbulence, inflation, growth), not opened; unconfirmed.
- Walk-forward protocol (long training window, refit every six months, penalty chosen on a validation window, signal delay): attributed to the same paper; unconfirmed.

Not found (by the earlier survey; **not re-checked by the owner**): a maintained open-source system that produces governed regime records end to end (no regime module in Qlib, QuantConnect Lean, zipline-reloaded or pyfolio-reloaded). Not verified: primary sources for breadth, put/call and skew as regime features; the Taiwan VIX download depth (reported as about three months on the TAIFEX page).

## 5. Decisions

Numeric values are coordinator proposals to be calibrated by walk-forward results.

| # | Decision |
|---|---|
| R1 | **Two layers.** (a) A market-state measurement per market and session, shared by every consumer, because the market's condition is a fact. (b) One `RegimeState` per strategy and session, as the plan requires, derived from the measurement through that strategy's regime profile. A persona's own regime assessment in Orient is its interpretation: it reads the records for its pool and may disagree with another persona, which is the plan's "Regime conflict". The interpretation lives in the cycle packet and persona memory, never in `RegimeState`. |
| R2 | **Owner and placement.** The regime evaluator is a module of the registry service (`services/registry/regime/`). Reasons: the plan keeps it a module inside a service; the registry service already owns strategy specs, the model-artifact registry and Postgres storage, and its `sector_features.py` already uses the feature catalogue (on caller-supplied observations; see §10 C1). It owns two tables, `market_state` and `regime_states`; only the evaluator writes them; consumers use GET routes. The schema stays in `services/registry-core/decision-domain/`. Rejected hosts: research (the plan forbids online estimation there), source ingestion (data plane does not own decision objects), persona (the record is needed by non-persona consumers). |
| R3 | **Axes.** Volatility (high, low); trend (up, down, mean-reverting); risk appetite (on, off); macro (expansion, slowdown, contraction). Each axis is estimated on its own. The nine-class `regime_class` enum is unchanged. **Macro is a measurement axis only: it is never the primary axis of a record**, because none of its states exists in the `regime_class` enum. It is carried in `feature_snapshot.macro_environment` and drives the consult rule (R13). |
| R4 | **Horizons.** Volatility, trend and risk appetite are each estimated at a short horizon (features with about a 10-session half-life) and a medium horizon (about 60 sessions). Macro is monthly. An intraday fast flag is R14. A strategy's holding horizon selects which horizon it reads. |
| R5 | **What each axis reports per session.** State; filtered probability vector; sessions in the current state; expected duration; transition probabilities; last switch session; model artifact version. Smoothed probabilities are never stored for a decision date. |
| R6 | **Methods.** Volatility: statsmodels `MarkovRegression` with switching variance, filtered (`model_ref.model_family: markov_switching`). Trend and risk appetite: continuous statistical jump model from `jumpmodels`, pinned and vendored (`model_family: custom`). Macro: rules over the NDC monitoring score and leading index (`model_family: rule_based`). The schema's `model_ref` has only `model_family`, `model_version` and `training_dataset_ref`, so the method name is carried in `model_version` as `<method>@<library version>/<registry artifact id>` (e.g. `jumpmodels-cjm@0.1.1/artifact-…`). When a record's primary axis uses one method but other axes use others, `model_ref` names the primary axis method; the others are listed in `feature_snapshot.axes[*].model`. No LLM produces any regime output. |
| R7 | **Inputs.** Volatility: TAIEX returns, Taiwan VIX when archived. Trend: TAIEX returns at several lookbacks, downside deviation, market breadth (advance/decline, share above moving averages). Risk appetite: institutional net flows, margin balance change, TAIFEX institutional net positions, put/call ratio, breadth. Macro: NDC series, USD/TWD. All are read point-in-time through the feature catalogue from governed official datasets and listed in `input_refs`; the values used go into `feature_snapshot`. A missing or incomplete input makes that axis `unavailable` with a recorded reason. |
| R8 | **From axes to a record.** Each strategy has a regime profile: its primary axis (volatility, trend or risk appetite only) and horizon, taken from the StrategySpec regime section or the alpha template's applicable regimes, else a default for its family. If a spec names macro as primary, the profile falls back to the family default and the macro state is still recorded as in the snapshot layout below. **Axis state to `regime_class`:** volatility high → `high_volatility`, low → `low_volatility`; trend up → `trending_up`, down → `trending_down`, mean-reverting → `mean_reverting`; risk appetite on → `risk_on`, off → `risk_off`. `confidence` is the primary state's filtered probability. `regime_class` is `regime_transition` when the primary axis switched within 5 sessions; `mixed` when no primary state reaches 0.5; the schema rule for confidence ≤ 0.3 is kept (those records use one of the two). `regime_scores` holds the primary axis probabilities keyed by the mapped class names above (not forced to sum to 1 with the unlisted classes; the evaluator checks the listed values sum to ≤ 1). If the primary axis is unavailable no record is produced. **`feature_snapshot` layout (all schema-valid):** `volatility_regime`: volatility low → `low`, high → `elevated` (`normal` and `extreme` are not emitted by the two-state model; reserved for a later three-state model); `trend_strength`: filtered P(up) − P(down) of the trend axis at the strategy's horizon, in [-1, 1]; `momentum_signal`: trend up → `positive`, down → `negative`, mean-reverting or no state ≥ 0.5 → `neutral`; `macro_environment`: expansion → `expansion`, slowdown → `slowdown`, contraction → `recession`, macro axis unavailable → `unknown` (`recovery` is not emitted by the rules). Per-axis details (probabilities, sessions in state, expected duration, transition probabilities, last switch session, model) go under the additional key `feature_snapshot.axes`; the schema sets no `additionalProperties` limit on `feature_snapshot`. An unavailable non-primary axis is recorded there with its reason and does not block the record. |
| R9 | **Model lifecycle.** Research fits and validates models and writes them to the registry as `model_artifact` (draft → candidate). A regime-classifier validation is added: walk-forward with filtered inference only; a one-session signal delay; switches per year and expected durations reported; label stability across refits; agreement with offline change points (`ruptures`) as an audit. A candidate becomes approved for paper scope when the validation passes; live scope needs an operator decision. |
| R10 | **Record lifecycle.** Records from a candidate model are stored `proposed` and not served. Records from an approved model are stored `validated` after schema and input-completeness checks; the latest per strategy is `active`; the previous one becomes `superseded` with `superseded_by`; a record whose inputs were revised is `invalidated`. |
| R11 | **History and refit.** The training window is at least 10 years of TAIEX history (proposal); refit every six months or on a drift alarm, through the existing research demand path. With less history the model stays a candidate and nothing is served. |
| R12 | **Trigger.** No new scheduler. Evaluation is idempotent per market, session and model version: the first request after the session's governed data is complete computes and stores it; later requests read it. The first requester is the daily persona cycle's observe step. The first request is a `POST` (idempotent evaluate), not a `GET`; see §10 C1-C2. |
| R13 | **Consumers, first wave.** Daily cycle: observe reads the records for the pool's strategies and sets `orient.regime_state_ref`; the case file gets `regime_tag`. Memory: the regime anchor of `SD_MEMORY_HINDSIGHT_ADOPTION` H4 and the regime mental-model question use the active record. Consult: a `macro_regime_shift` rule is added to `services/persona/consult_triggers.py` and fires when the macro axis switches. Fabricated references in §3 are replaced by a real record or left empty. |
| R14 | **Consumers, later (separate designs).** Regime fit in allocation; universe selection and signal inference (their producers do not exist); risk checks; attribution. Intraday: realised volatility from minute bars and the Taiwan VIX with online change-point detection for a fast transition flag, after the `INTRADAY-*` tasks and a VIX archive exist. |
| R15 | **Boundaries.** Paper only. Missing data fails closed with a reason; no placeholder or synthetic value. Execution never reads the measurement directly. No new privileged route. |

## 6. Target flow

```
official sources ──► collector / source ingest ──► governed datasets ──► feature catalogue (point-in-time)
                                                                                │
research: fit + walk-forward validate ──► registry model_artifact (candidate → approved)
                                                                                │
registry regime evaluator (first request per session) ──► market_state (axes × horizons)
                                                     └──► regime_states (one per strategy, schema-valid)
                                                                                │
daily cycle observe ──► orient.regime_state_ref, case file regime_tag ──► persona's own assessment
memory anchors · mental-model question · consult macro_regime_shift rule
later: regime fit · universe · signals · risk · attribution
```

## 7. Delivery

| Wave | Work |
|---|---|
| 0 | Data: index, sector indices and breadth from the page already downloaded; long TAIEX history; schedule margin, TAIFEX positions, put/call and TPEx flows; TX price and open interest; Taiwan VIX daily archive |
| 1 | Research: features, model fitting, regime-classifier validation, registry artifacts. Registry: evaluator module, tables, GET routes |
| 2 | First-wave consumers (R13) |
| 3 | Separate designs for R14 |

## 8. Risks

| # | Risk | Handling |
|---|---|---|
| K1 | `jumpmodels` has one maintainer and no release since 2024 | pin, vendor, cover with our own tests; statsmodels remains a fallback method per axis |
| K2 | History too short to validate | R11 fail-closed; Wave 0 backfill first |
| K3 | Taiwan VIX history is lost for every day not archived | archive task opened ahead of the design |
| K4 | Labels flip between refits | label stability is a validation criterion (R9); states are ordered by a fixed statistic |
| K5 | Statistical dependencies enlarge the registry image | measured in the evaluator task; a separate worker inside the same service boundary is the fallback |
| K6 | Consumers treat low confidence as a signal | `regime_transition` and `mixed` are explicit; `unavailable` produces no record |

## 9. Deviations from the plan and open items

Every addition beyond the plan:
- R1: the shared market-state measurement layer (the plan is silent; no schema change, records reference it through `input_refs` and `feature_snapshot`).
- R2: the host service (registry) and the two tables `market_state` and `regime_states`.
- R3: the per-axis decomposition, and macro as a measurement-only axis that is never a primary axis.
- R4: horizons.
- R5: persistence outputs (durations, transition probabilities, sessions in state).
- R6: method choices, and the `model_ref` naming convention (`model_family: custom` plus the method carried in `model_version`).
- R7: the input list per axis and the fail-closed `unavailable` rule.
- R8: the regime profile per strategy, the axis-to-class mapping, the `feature_snapshot` layout and the extra `feature_snapshot.axes` key.
- R9: the regime-classifier validation protocol.
- R10: the record lifecycle mapping.
- R11: training window and refit cadence.
- R12: the trigger (idempotent POST evaluate).
- R13: the daily-cycle `orient.regime_state_ref` wiring, the case-file `regime_tag`, and the new `macro_regime_shift` consult rule (the plan has the rule in text; D6 keeps it off until a governed signal exists).
- R14, R15: deferred consumers and boundaries.
- The nine-class enum is kept although the review note in §2 is correct; a per-axis schema would be a plan change and is not proposed here.
- Operator decision needed later, not now: approval of a regime model for live scope.

## 10. Owner verification record (Claude, base `a9284b68c`, 2026-11-14)

**Verified.** Every section 1 statement matches transcript lines 3112, 3284, 3309, 3344, 3462 verbatim (text, timestamp, uuid); line 3453 is the coordinator reply with the stated uuid. References in §2 and §3 resolved on the base, with these corrections made in place after review: the attribution text is `Pantheon_Blueprint_Gap_Review_v1.md:418` (not 421-424, which is owner-plane text), and `ooda_cycle_runtime.py:480` uses `{_research_stage(persona)}` (not `{stage}`). Other references: schema ranges (`regime_state.schema.json` 27, 75-84, 93-121, 151-171, 185-201, 204-248, 231-280, 281-297), Gap Review 405-447, 總索引 401, deployment doc 371-381, MARKET_DATA plan 510-516, statsmodels criteria 160-166, aggregation 175-209 and 310-316, `PERSONA_RUNTIME_MODEL.md:178`, `paper_loop_packet.py:519`, `cron_ooda_closure.py:364`, `models.py:142`, `case_file.py:68`, `statsmodels_adapter.py:236-250`, `spiders.py:936-960`, `workflows.py:9-12`, `contract.md`, `GOLDEN_REPLAY_SCENARIO_AND_RUNBOOK.md:173` (repo root), `services/control-plane/bff/ports/operations_consultation.py:181` (BFF accepts `macro_regime_shift`). No migration or producer for `regime_states` exists; `consult_triggers.py` has no macro rule. All daggers removed.

**Schema validity (AC4).** The base schema (`git show a9284b68c:services/registry-core/decision-domain/regime_state.schema.json`) was loaded with Draft7Validator and the base example was modified per R3, R6 and R8. Validated OK: one record for each of the seven primary-axis states (high_volatility, low_volatility, trending_up, trending_down, mean_reverting, risk_on, risk_off), each of the three macro states in `feature_snapshot.macro_environment` (expansion, slowdown, recession), a `regime_transition` record at confidence 0.2, a `mixed` record at 0.45. Negative control: `regime_class: contraction` fails the enum, which is why macro is never primary. No schema file is changed.

**Open source (§4).** Licences and last activity re-checked on 2026-11-14 for statsmodels, jumpmodels, bayesian_changepoint_detection, ruptures and hmmlearn (sources per row). Still unconfirmed, individually: the arXiv:2402.05272 switch-count figures, half-lives and walk-forward protocol; Nystrup/Kolm/Lindström 2020; Kritzman et al. 2012; the jumpmodels one-hot online probability claim for the discrete model; hmmlearn smoothed `predict_proba`; the Taiwan VIX download depth; the "no regime module in Qlib, Lean, zipline-reloaded, pyfolio-reloaded" survey result.

**Corrections and review findings.**
- C1 (R2/R12, placement): the registry service does not read governed datasets itself. `sector_features.py` and `candidate_pool.py:310-312` operate on caller-supplied observations, and `services/registry/requirements.txt` has no numpy/statsmodels. Placement stays workable, but the evaluator needs (a) a governed dataset reader or caller-supplied inputs and (b) the statistical dependencies (K5). The evaluator task decides this first; a separate worker inside the registry boundary is the likely outcome.
- C2 (R12, trigger): compute-on-first-read `GET` would write on a read route. Corrected to an idempotent `POST` evaluate keyed by market, session and model version, with `GET` read-only. The daily cycle observe step (`persona_cycle_controller.py:438`) already reads owners over HTTP with bearer and tenant headers, and the 10:00 cron runs after the Taiwan refresh.
- C3 (R7/R8): `regime_scores` holds normalized probabilities; the schema does not enforce this, so the evaluator validates it.
- C4 (R3/R8): macro cannot be a `regime_class`; made measurement-only with explicit snapshot mapping.

All numeric values remain coordinator proposals. No schema change is made by R1-R15.
