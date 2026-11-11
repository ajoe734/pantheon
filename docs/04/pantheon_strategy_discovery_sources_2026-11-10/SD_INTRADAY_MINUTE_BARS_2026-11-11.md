# SD — Intraday minute-bar data and intraday strategy research path (design only)

Task `INTRADAY-MINUTE-BARS-SD-20261111` · owner Claude · reviewer Codex · 2026-11-11
Parents: `DEV_PLAN_STRATEGY_DISCOVERY_2026-11-10.md` (plan wins where they differ), `SD_STRATEGY_DISCOVERY_SOURCES_2026-11-10.md` §9–§12, `SD_VIEW_UNIVERSE_CROSS_SECTIONAL.md`.

**Status: design only. No source file is changed.** File and symbol references were read from `origin/dev` at `7b1f7f2a5`. Line numbers drift; the symbol names are the reference.

## 0. 中文摘要（操作者用）

* 決定（操作者 2026-11-11）：盤中資料的標準粒度是 FinMind `TaiwanStockKBar` 的 **1 分鐘 K 棒**；5/15/30/60 分鐘由系統在治理過的 feature dataset 內聚合；不使用 Shioaji。
* 資料權威：日線真值仍是 TWSE/TPEx 官方的 `normalized/tw_price_daily`。分鐘線寫到另一個 dataset `normalized/tw_price_1m`，**絕不**寫進 `tw_price_daily`（評估器以 `(symbol, date)` 為鍵、不看 provider，遇到衝突的重複列會 fail closed）。
* 每個交易日、每檔股票，把 1 分鐘線聚合後與官方日線對帳；超出容差就隔離（quarantine）該日，不自動修正。
* 策略宣告 `bar_frequency`；loader 依頻率選 dataset，不會「依序嘗試來源」。
* 規模：2 年回補，200 檔約 98,000 次請求（以每小時 4,500 次的自我限速約 22 小時），約 2,660 萬列，壓縮後約 0.4 GB；磁碟剩 72 GB，足夠。
* 拆成 K1–K8 + W5 共 9 個實作／驗收任務（§9），已列入 DEV_PLAN §4。
* 需要操作者確認的只有 §10 的幾個數字（宇宙大小、容差、保留年限）；預設值已寫明，不確認也能先做。

## 1. Where the runtime assumes daily bars (inventory)

Every row below was read from the tree. "Daily-only" means the code either hard-codes 1d, or would silently treat a minute bar as a day.

| Layer | Where | Fact | Intraday impact |
| --- | --- | --- | --- |
| Ingestion: official daily | `services/source_ingestion/connectors/taiwan_official.py` (`TaiwanOfficialMarketDatasetAdapter`, `SOURCE_INGEST_TW_HISTORY_TRADING_DAYS_DEFAULT = 40`); `provider_adapters.py` `_taiwan_official` (`dataset = request.get("dataset") or "tw_price_daily"`, `required_priority_records`, `per_symbol_depth`) | per-date all-stock pages, daily closes per active paper symbol | untouched; remains daily truth |
| Ingestion: FinMind | `services/source_ingestion/connectors/finmind_taiwan.py` `FINMIND_TAIWAN_DATASETS` (`TaiwanStockPrice` → `tw_price_daily`, `TaiwanStockDayTrading`), `FinMindTaiwanDatasetAdapter`, `FinMindLiveFetcher.fetch_dataset` / `_get` (Bearer token, `_FINMIND_MAX_RESPONSE_BYTES`), `FinMindQuotaError` on 402, `build_finmind_fetch_plan` (`FINMIND_ARCHIVE_DATASETS`, one request per symbol/dataset/date) | no KBar entry. `TaiwanStockPrice` maps to `tw_price_daily` here, so a second writer to that dataset already exists in code; it must stay out of the evaluator path (SD §4) | K1 adds `TaiwanStockKBar` → `tw_price_1m` only |
| Connector registry | `services/source_ingestion/connector_definitions.py` (`datasets=("tw_price_daily",)`, `storage_targets ("normalized/tw_price_daily","features/returns")`), `default_desired_state.json`, `active_universe.py` (`dataset="tw_price_daily"`, `storage_targets`, `build_active_universe_job_fanout`) | cadence 86400 s, one daily dataset per universe member | K1/K2 add a separate `tw_price_1m` connector and job class |
| Storage / reads | `services/research/alpha_templates/evaluator.py` `_snapshot_root` (`normalized/tw_price_daily`), `_read_snapshot_rows`, `load_tw_price_daily_snapshot`, `GOVERNED_SNAPSHOT_SOURCE_KIND`; duplicate rule: conflicting OHLC for `(symbol, date)` raises `TaiwanEvaluationDataError`, same OHLC keeps larger volume; bar `ts` = `T14:30:00+08:00` | key is `(symbol, date)` with no provider; availability stamped at 14:30 | **hard reason minute data must be a separate dataset**. K4 adds a frequency-aware loader beside it |
| Other readers | `scripts/project_market_data_to_bff_agora_surfaces.py` (accepts only `tw_price_daily` SourceRecords); `services/control-plane/bff/personas/service.py` (`"dataset": "tw_price_daily"`); `docker-compose.yml` `TRAINING_SESSION_SOURCE_DATASET_ID` default `tw_price_daily` | daily dataset id literal | unchanged; they never see `tw_price_1m` |
| Feature datasets | `services/registry/factor_rule.py` `PRICE_VOLUME_DATASETS` (`tw_price_volume`, `tw_price_daily`, `ohlcv`, …), `BUILT_IN_PRICE_VOLUME_FIELDS`, `MAX_FACTOR_WINDOW_BARS = 504`, `FeatureDefinition.min_availability_lag_days` | windows are in bars but the lag is in days; no frequency in a feature definition | K4 adds `tw_price_1m`/`tw_price_5m`… with a minute-granular availability lag |
| Strategy artifact | `services/registry/strategy_artifact.py` `EXECUTION_RECIPE_KIND = "close_to_close_momentum"`, `_EXECUTION_RECIPE_KEYS` (`bar_frequency`, `data_source`), `validate_execution_recipe` (only checks non-blank), contradiction check against `market_scope.frequency` (`:1381`) | frequency is a free string, validated for consistency only | K4 adds an allowed set (`1d`,`1m`,`5m`,`15m`,`30m`,`60m`) and the dataset it maps to |
| Strategy spec conversion | `services/research/strategy_spec/conversion.py` (`recipe["bar_frequency"]`, `metadata.get("frequency")`) | carries frequency through | no change beyond the allowed set |
| Backtest engine | `services/research/vectorbt/adapter/vectorbt_adapter.py` (`data_frequency = … or "daily"` at `prepare`, `MIN_BARS`, `_req_iso_date` keys records by ISO date, annualisation via `daily_rets`, default recipe `"bar_frequency": "1d"` at `:1822`, `"data_source": "source-ingest:twse/daily-bars"`) | dates are ISO days; two bars on one date collide; Sharpe/annualisation assume 1 return per day | K5 |
| Alpha template evaluation | `evaluator.py` `HORIZON_KEYS = ("1d","5d","20d")`, `turnover_1d`, IC decay `1d→20d`, `ts` = 14:30 | horizons are days | K5 adds minute horizons for intraday templates; daily results unchanged |
| Replication gate | `services/research/replication/gate.py` `ReplicationGate`, `FamilyTrialCounterStore`; `gate_config.py` `GateConfig`/`AdmissionRules`; fixtures `recorded_backtest_tw_2330*.json` carry `bar_frequency` | gate windows and minimum-sample thresholds are written in days; fixtures are daily | K6 |
| Paper execution | `services/execution/lean_runtime/paper_signal_producer.py` `_bar_identity` (daily keys on calendar date; other cadences key on exact `event_time`), the `grading` block (`closes` + `decision_id_for_bar` only when `bar_frequency ∈ {1d,d,1day,daily}`, otherwise `None` with the comment "other cadences are not graded because their bars are not one session each"), `is_trading_day` use at `:1534/:1546`; `instrument_candidates.py` `is_trading_day` | **non-daily decisions are produced but never graded**; the session calendar is day-level | K7 |
| Persona provisioning | `services/control-plane/bff/persona_provisioning_coordinator.py` `_paper_universe` (`bar_frequency = request_payload.get("bar_frequency") or "1d"`), `rebalance_cadence: "{bar_frequency} bar close, paper only"`, recipe at `:2188-2288` | default 1d; already passes frequency through | K7 validates the allowed set at provisioning |
| Research / BFF carriage | `services/control-plane/bff/agora/research/service.py:350`, `services/training-session/main.py:513` | carry `bar_frequency` in parameters | no logic change |
| Evolution search | `services/evolution/` (`seed_data.py` `window="1d"`); discovery-side search is plan task D1 | mutation surface has no frequency axis; `bar_frequency` is not a mutable control | K8: frequency stays immutable per persona; search runs inside it |
| Source-to-paper snapshot boundary | `services/source_ingestion/pipeline.py` `persist_latest_market_snapshots` (projects every completed ingest); `services/source_ingestion/requirement_state.py` `LatestMarketSnapshotStore` / `_point_from_record` (accepts `symbol`/`close`, no dataset or frequency check, stored keyed by symbol only); `services/source_ingestion/routers/ingest_operations.py` `GET /api/source-ingest/snapshots/latest` (takes `symbol` only); consumers `services/execution/lean_runtime/paper_signal_producer.py` `_load_market_input`, `services/paper_fleet_reconciler/paper_fleet_reconciler.py`, `services/control-plane/bff/agora/operational_readiness.py`, `services/trade_journey/hosted_lifecycle_probe.py`, `services/execution/lean_runtime/performance_telemetry.py` | a completed `tw_price_1m` ingest would be projected into the same per-symbol latest-close store and overwrite or contaminate the daily close that paper consumers read. Separate storage folders do not prevent this | K2 excludes `tw_price_1m` from `persist_latest_market_snapshots`; K4 adds dataset/frequency-keyed snapshot storage and a `frequency` query parameter (default `1d`, so existing consumers are unchanged); K7 selects by declared frequency |
| Calendar | `taiwan_official.py` `fetch_holiday_calendar_payload`, `TW_HOLIDAY_CALENDAR_SCHEMA_HASH`; `instrument_candidates.is_trading_day(day, holidays)` | governed TW holiday calendar exists, day-level | reused; no new calendar |

Not found in the tree: any minute-bar dataset, any `TaiwanStockKBar` reference, and any intraday session-time constant. The `FINMIND-BROKER-BRANCH-ENABLE-20261111` task lives in the task store, not in the tree; K1 depends on it by task id (shared token, quota meta, fetcher).

## 2. Decision (operator, 2026-11-11)

1. **Canonical intraday grain = FinMind `TaiwanStockKBar`, 1 minute.** Fields: `date, minute, stock_id, open, high, low, close, volume`.
2. **Coarser bars (5/15/30/60 minute) are derived by aggregation** in governed feature datasets from the stored 1-minute rows. They are never fetched separately and never ingested as their own truth.
3. **Shioaji is out of scope.** No broker-API data path.
4. **Source authority.** Daily OHLCV truth is TWSE/TPEx official in `normalized/tw_price_daily`. Minute bars live in `normalized/tw_price_1m`. No component writes minute data into `tw_price_daily`, and no component reads `tw_price_1m` as daily truth.
5. **Dataset selection is by declared frequency.** A strategy declares `bar_frequency`; the loader maps it through a fixed table (§6.1) and fails closed if the dataset is missing or unreconciled. It never tries sources in order and never substitutes daily or synthetic bars.

## 3. Ingestion contract (KBar connector)

* New dataset row in `FINMIND_TAIWAN_DATASETS`: `dataset = "TaiwanStockKBar"`, `normalized_dataset = "tw_price_1m"`, `cadence = "daily_after_close"`. FinMind documents KBar as updated at about 15:50 Asia/Taipei (https://finmind.github.io/tutor/TaiwanMarket/Technical/#taiwanstockkbar-sponsor), so **the stored feed is a post-close historical feed, not a live intraday feed** (see §6.4). A new adapter class sits beside `FinMindTaiwanBrokerDailyReportAdapter`, reusing `FinMindLiveFetcher` (token read from `FINMIND_API_TOKEN` only; never logged, never in a URL or evidence file).
* One request = one `(stock_id, date)`. **Unverified:** whether FinMind accepts a multi-day range for KBar. K1 first probes this with one request and records the answer in its evidence; if ranges work, backfill requests drop by the range length and §4 is an upper bound.
* Quota: SponsorYear = 6,000 requests/hour. The scheduler self-limits to **4,500/hour** (25 % headroom for the broker and daily jobs that share the token) and reads `_extract_quota_meta` from every response; a `FinMindQuotaError` (HTTP 402) stops the run and resumes at the next hour. Nothing falls back to another source.
* Each record keeps `source = finmind`, `dataset = TaiwanStockKBar`, request id, content hash, `fetched_at`, and the raw row; normalized row = `{symbol, session_date, bar_start, bar_end, open, high, low, close, volume, provider_published_at, fetched_at, reconciled_at, bar_availability_ts}`, where `bar_availability_ts` = max(`bar_end`, `fetched_at`, `reconciled_at`) — the time the bar was actually obtainable and reconciled, never `bar_end` for after-close data.
* **Unit check (unverified):** FinMind KBar `volume` may be shares or lots. K1 compares the sum of one day against the official daily volume for two symbols and records the factor; the reconciliation (§5) then enforces it.

## 4. Storage, retention, backfill volume

Assumptions (stated so they can be recalculated): active universe N = **200** symbols (core + candidate tiers from `active_universe.py`; operator may change, §10); ≈ 490 trading days in two years; ≤ 271 bars per symbol-day (09:00–13:30 regular session; FinMind may include one closing-auction bar).

| Quantity | Formula | Result |
| --- | --- | --- |
| Backfill requests | N × 490 | 98,000 (49,000 at N = 100; ≈ 24,500 at N = 50) |
| Backfill wall time at 4,500/h | 98,000 / 4,500 | ≈ 21.8 h, resumable per hour |
| Daily incremental requests | N | 200/day (≈ 4 % of one hour's budget) |
| Rows after 2 years | 200 × 490 × 271 | ≈ 26.6 M |
| Size as JSONL (≈ 130 B/row) | | ≈ 3.5 GB uncompressed |
| Size as gzip JSONL (≈ 8:1) | | ≈ 0.4–0.5 GB |
| Hosted disk headroom | measured on this host `df /` 242 G total, 72 G free | storing ≤ 1 GB is < 2 % of free space |

* **Layout:** `normalized/tw_price_1m/<session_date>/<shard>.jsonl.gz`, sorted by `(symbol, bar_start)`, one manifest per day with row counts, per-symbol counts and a content hash. Date-partitioned like `tw_price_daily` so the existing snapshot tooling pattern applies.
* **Idempotency:** the key is `(symbol, session_date, bar_start)`. A re-fetch with identical content is a no-op; differing content for an existing key is a conflict → the day is quarantined (§5), never overwritten.
* **Retention:** rolling **2 years** of accepted days; quarantined days are kept until resolved (§5) with a 90-day expiry after resolution. Anything older than 2 years is removed by a manifest-driven pruner that logs what it deleted.
* **Disk guard:** the backfill job refuses to start unless free space ≥ 10× the projected remaining bytes and ≥ 20 GB; it stops, reports `disk_budget_exhausted`, and does not delete other datasets.
* **Backfill order:** newest day first (recent data is what paper strategies need), then backwards; the job reports coverage per symbol so research knows the actual usable history.

## 5. Daily reconciliation against official OHLCV

For every `(symbol, session_date)` that has both a `tw_price_1m` day and an official `tw_price_daily` row:

| Field | Aggregation of 1-minute bars | Compared with official | Default tolerance |
| --- | --- | --- | --- |
| open | open of first bar of the session | official open | \|Δ\| ≤ max(1 tick, 0.1 %) |
| high | max of highs | official high | same |
| low | min of lows | official low | same |
| close | close of last regular-session bar | official close | same |
| volume | sum of volumes after the unit factor (§3) | official volume | relative Δ ≤ 2 % |
| coverage | bar count / expected bars | ≥ 95 % of expected minutes for the session; no gap > 10 consecutive minutes | — |

* One tick = the TWSE tick size at the official close price (price-band table, parameterised in config).
* **Outcome is binary per day.** `pass` → the day is eligible for loaders. `fail` or `no_official_row` or `official_row_conflict` → the day goes to `normalized/tw_price_1m_quarantine/<session_date>/` with `{symbol, field, observed, official, tolerance, reason, source_refs}`.
* **Nothing is silently fixed.** No bar is edited, interpolated, clipped, or replaced by an official value. A quarantined day is excluded from every dataset the loader can see; a strategy whose window needs it reports `insufficient_clean_history` instead of running on a partial day.
* A symbol-day can leave quarantine only by a re-fetch that then passes, or by an operator-recorded waiver with a reason (waived days carry a `waived=true` marker and are excluded from replication evidence).
* Tolerances are configuration (`config/intraday_reconciliation.json`), initial values above. K3 measures the pass rate on the first two weeks of real data and reports it; if the pass rate is below 90 % the tolerances are not widened automatically — the report goes to the operator (§10-Q2).
* The reconciliation never writes to `tw_price_daily`. A failing day produces an alert/evidence record, not a daily-data change.
* Days with a daily row but no minute data are reported as `minute_missing` (coverage), not quarantined.

## 6. Point-in-time and session rules

### 6.1 Frequency → dataset table

| `bar_frequency` | Dataset | Source |
| --- | --- | --- |
| `1d` | `tw_price_daily` | official |
| `1m` | `tw_price_1m` | KBar, reconciled days only |
| `5m`, `15m`, `30m`, `60m` | `tw_price_5m` … `tw_price_60m` (governed feature datasets) | aggregation of reconciled `tw_price_1m` |

Any other value is rejected by `validate_execution_recipe` and at provisioning.

### 6.2 Session

* Regular session **09:00–13:30 Asia/Taipei**. Bars outside it (pre-open 08:30–09:00, any after-hours or fixed-price session 13:40–14:30) are dropped at normalisation and counted in the manifest; they are not stored in `tw_price_1m`.
* **Odd-lot** trading (intraday odd-lot sessions) and after-hours are excluded. If KBar cannot separate them, K1 records that, and reconciliation (§5) is the guard: volume outside tolerance quarantines the day.
* **Holidays and half days** come from the governed TW calendar (`fetch_holiday_calendar_payload`, `is_trading_day`). Bars on a non-trading day are rejected. A trading day with an early close is handled by an `expected_bars` override from the calendar record; absent that record the day is quarantined.
* Trading halts and limit-lock minutes can produce empty minutes. A missing minute is **not** forward-filled in storage; aggregation marks the bucket `partial` and strategies see the bar only when its coverage meets the rule in §5 (`partial` buckets are skipped, never imputed).

### 6.3 No look-ahead

* A bar is stamped `bar_start` and `bar_end`; its `bar_availability_ts` = max(`bar_end`, actual `fetched_at`, actual `reconciled_at`) as defined in §3. **Backdating is prohibited:** a bar fetched after the close must not be assigned `bar_end` (or any time earlier than its real fetch/reconciliation) for live or paper admission. A decision at time t may use only bars with `bar_availability_ts ≤ t`. For aggregated bars the whole bucket must have closed: the 09:00–09:05 bar is usable from 09:05, never earlier.
* Aggregated bars align to session start (09:00 buckets), never to wall-clock hours, and the last bucket of the day is truncated at 13:30 (60-minute buckets: 09:00, 10:00, 11:00, 12:00, 13:00–13:30 as a half bucket, flagged).
* Execution at the decision bar uses the **next** bar's open (backtest and paper), the same rule as the daily path; no fill at the signal bar's own close.
* The feature `min_availability_lag_days` becomes `min_availability_lag` with a unit (`days` | `minutes`); minute features default to 0 minutes beyond bar close.
* Overnight gaps: indicators with a lookback may span sessions only if the feature definition declares `cross_session = true`; the default is within-session (window resets at 09:00), because the open gap is a different regime.
* The daily official row for day D is available only after the official refresh; an intraday strategy must not read day D's daily OHLC during day D.

### 6.4 Historical replay versus live paper capability

| Mode | Data | Availability rule | Status |
| --- | --- | --- | --- |
| Historical replay / backtest / replication | reconciled `tw_price_1m` days | replay may use `bar_end` as the simulated decision clock **only inside a replay run** and the run is labelled `replay`; it can never be presented as live evidence | supported by K5/K6 |
| Live intraday paper (decisions during 09:00–13:30) | none today: KBar arrives ≈ 15:50 and requires full-day reconciliation, so no bar of day D is available during day D | needs an authorised intraday feed whose publication time is ≤ the bar close + allowance | **`unavailable`** — K7 returns `SignalDecisionUnavailable(reason=no_live_intraday_feed)`; it does not use after-close data and does not backdate |
| Next-session paper (data of day D drives decisions on D+1) | reconciled `tw_price_1m` day D, `bar_availability_ts` ≥ D 15:50 | decision allowed only at or after `bar_availability_ts`; the first usable bar is the D+1 open | supported by K7 as the only paper mode until a live feed is authorised |

A live feed is not chosen by this SD (Shioaji is out of scope); adding one is a new operator decision and a new connector task, and until then the live-intraday row stays `unavailable`.

## 7. How intraday strategies are backtested, graded, replicated, executed

No step may fall back to daily or synthetic data. A missing or quarantined window ends the run with a named reason.

* **Backtest (K5).** The engine takes the dataset by frequency (§6.1). Bars keyed by `(symbol, bar_start)` (not ISO date); returns annualised with `bars_per_year = 252 × bars_per_session(frequency)`; costs per fill (existing cost model, applied per round trip; intraday strategies are cost-dominated so the gate shows gross and net); no overnight position unless the recipe declares `holding = overnight`; positions must be flat by 13:25 by default. Alpha-template IC horizons for intraday templates are `1, 5, 15, 30 bars`, reported next to (not instead of) the daily set.
* **Grading (K7).** Today decisions at non-daily frequency are produced but `grading` is `None` (`paper_signal_producer.py` grading block). K7 gives each intraday decision a grading target: the return from the next bar's open to the close of horizon-th bar (`decision_id_for_bar` keyed by `bar_start`), graded only after the horizon bar is available and reconciled. Daily grading is unchanged. A decision whose horizon crosses the session end is graded to 13:30 and marked `truncated`.
* **Replication gate (K6).** Windows and minimum samples are expressed in sessions and in trades (not days alone): a candidate needs ≥ 60 sessions of clean 1-minute history (≈ 3 months) and ≥ 300 trades in-sample, and ≥ 20 sessions / ≥ 100 trades out of sample, with the deflated threshold (design §10.3) counting every frequency and wrapper variant as a trial. Because only ~2 years exist, no walk-forward longer than 12 + 6 months is claimed. These numbers are proposals (§10-Q3).
* **Paper execution (K7).** The producer admits a decision only for a fully-closed, reconciled bar of the declared frequency whose `bar_availability_ts ≤` decision time (§6.4: with the KBar feed that means next-session paper only); `_bar_identity` already keys non-daily by exact `event_time` and is kept; the daily-date normalisation stays for `1d`. Intraday-window scheduling (09:00–13:30, none after 13:25) is enabled only when a live feed exists (§6.4); otherwise live-intraday is `unavailable`. No data for the bar ⇒ `SignalDecisionUnavailable` with a reason, never a reused previous bar.
* **Evolution search (K8).** `bar_frequency` is immutable per persona (like `symbols`); the evolution loop searches parameters inside the declared frequency only, and trial counting includes the frequency. Tied to D1 (`EVOLUTION-AUTO-CONSUMER-SEARCH`).

## 8. Failure modes made explicit

| Event | Behaviour |
| --- | --- |
| FinMind 402 / quota | stop; resume next hour; alert; no other source |
| Token missing | `FinMindCredentialError`, task reports blocked; no placeholder data |
| KBar day disagrees with official day | quarantine (§5) |
| Official daily row not yet available | day stays `pending_reconciliation`, invisible to loaders |
| Conflicting duplicate minute row | quarantine day; never overwrite |
| Disk guard trips | stop with `disk_budget_exhausted`; other datasets untouched |
| Live intraday decision requested, no authorised live feed | `SignalDecisionUnavailable(no_live_intraday_feed)`; no after-close data used |
| Strategy declares a frequency with no reconciled window | `insufficient_clean_history`; no synthetic bars |

## 9. Implementation tasks (all `ajoe734/pantheon`; dependencies in the task store are authoritative)

Suffix `-20261111` on every id. Owners alternate Claude/Antigravity; reviewer is the other lane.

| # | Task id | Depends on | Exact artifacts |
| --- | --- | --- | --- |
| K1 | `INTRADAY-KBAR-CONNECTOR` | `FINMIND-BROKER-BRANCH-ENABLE-20261111` | `services/source_ingestion/connectors/finmind_taiwan.py` (KBar dataset row, `FinMindTaiwanKBarAdapter`, `fetch_kbar`), `services/source_ingestion/connector_definitions.py` (connector `tw_price_1m`), `services/source_ingestion/connectors/__init__.py`, `services/source_ingestion/tests/test_finmind_kbar.py`, `services/source_ingestion/tests/test_finmind_kbar_availability.py` (provider/fetched timestamps recorded), probe evidence for the range-request and volume-unit questions in `docs/deployment/evidence/INTRADAY-KBAR-CONNECTOR-20261111/evidence.json` |
| K2 | `INTRADAY-1M-STORE-BACKFILL` | K1 | `services/source_ingestion/intraday_store.py` (partitioned gz writer, manifest, idempotency, pruner, disk guard), `services/source_ingestion/intraday_backfill.py` (4,500/h limiter, resume, newest-first), `active_universe.py` (job class for `tw_price_1m`), `default_desired_state.json`, `services/source_ingestion/pipeline.py` (`persist_latest_market_snapshots` skips `tw_price_1m`), tests `services/source_ingestion/tests/test_intraday_store.py`, `test_intraday_backfill.py`, `test_pipeline_snapshot_dataset_isolation.py` (a minute ingest leaves the daily latest close unchanged) |
| K3 | `INTRADAY-DAILY-RECONCILE` | K2 | `services/source_ingestion/intraday_reconcile.py`, `config/intraday_reconciliation.json`, quarantine writer, a read-only coverage report script `scripts/report_intraday_reconciliation.py`, `services/source_ingestion/tests/test_intraday_reconcile.py` (sets `reconciled_at` / `bar_availability_ts`), tests including a quarantined-day fixture and a "never writes tw_price_daily" assertion |
| K4 | `INTRADAY-FEATURE-AGGREGATION` | K3 | `services/registry/factor_rule.py` (frequency-aware datasets, `min_availability_lag` unit), `services/registry/strategy_artifact.py` (allowed `bar_frequency` set, dataset table §6.1), `services/research/intraday_bars.py` (aggregation, session-aligned buckets, `partial` flag, frequency-aware loader that fails closed), `services/control-plane/bff/persona_provisioning_coordinator.py` (provisioning validation), `services/source_ingestion/requirement_state.py` (`LatestMarketSnapshotStore` keyed by `(symbol, dataset/frequency)`; `_point_from_record` rejects records of another dataset), `services/source_ingestion/routers/ingest_operations.py` (`snapshots/latest` accepts `frequency`, default `1d`), tests `services/source_ingestion/tests/test_snapshot_frequency_isolation.py` (mixed daily+minute ingest: daily close never replaced by a minute close; `frequency=1m` returns only minute points), `services/research/tests/test_intraday_bars.py`, `services/registry/tests/test_strategy_artifact_bar_frequency.py` |
| K5 | `INTRADAY-BACKTEST-ENGINE` | K4 | `services/research/vectorbt/adapter/vectorbt_adapter.py` (bar-keyed records, `bars_per_year`, flat-by-close, next-bar-open fills), `services/research/alpha_templates/evaluator.py` (intraday horizons, minute availability), tests `services/research/tests/test_vectorbt_intraday.py` with a no-look-ahead assertion and a replay-label assertion (replay runs are marked `replay`, never live evidence) |
| K6 | `INTRADAY-REPLICATION-GATE` | K5, `C2` | `services/research/replication/gate.py`, `gate_config.py`, `GATE_CONTRACT.md` (sessions/trades thresholds), fixture `services/research/replication/fixtures/recorded_backtest_1m_*.json`, tests `services/research/replication/tests/test_gate_intraday.py` |
| K7 | `INTRADAY-PAPER-EXECUTION-GRADING` | K4, K5 | `services/execution/lean_runtime/paper_signal_producer.py` (`_load_market_input` selects the snapshot by declared frequency via `snapshots/latest?frequency=`; availability-gated admission; intraday `grading`; `no_live_intraday_feed` reason), `services/execution/lean_runtime/instrument_candidates.py` (session-time helper next to `is_trading_day`), tests `services/execution/lean_runtime/test_intraday_paper_grading.py` proving a non-daily decision is graded, that a missing bar yields `SignalDecisionUnavailable`, that an after-close bar is never admitted before its `bar_availability_ts`, and a mixed daily/minute snapshot regression |
| K8 | `INTRADAY-EVOLUTION-SEARCH` | K6, K7, `D1` | `services/evolution/seed_data.py` and the evolution mutation surface under `services/evolution/` (`bar_frequency` immutable), the trial counter in `services/research/replication/gate.py` (`FamilyTrialCounterStore` includes frequency), tests `services/evolution/tests/test_intraday_frequency_immutable.py` |
| W5 | `INTRADAY-HOSTED-ACCEPTANCE` | K2, K3, K6, K7 | Human/Ops → Claude: hosted run shows ≥ 20 reconciled sessions in `tw_price_1m` with real provider/fetched/reconciled timestamps, a quarantined day with its reason, one intraday persona paper-graded in next-session mode, and the live-intraday mode reporting `unavailable`; evidence under `docs/deployment/evidence/INTRADAY-HOSTED-ACCEPTANCE-20261111/` |

K7/W5 acceptance is next-session paper unless a live feed is authorised (§6.4). Critical path: K1 → K2 → K3 → K4 → K5 → K6/K7 → W5. K1/K2 can start now; K6 also waits for the replication task `C2` already in the plan.

## 10. Open questions for the operator (defaults used if unanswered)

* **Q1 Universe size.** Default 200 symbols (≈ 22 h backfill). More symbols scale requests linearly.
* **Q2 Reconciliation tolerance.** Default 0.1 %/1 tick price, 2 % volume, 95 % coverage; calibrated on real data by K3 and reported, not auto-widened.
* **Q3 Replication thresholds.** Default ≥ 60 clean sessions and ≥ 300 trades in-sample.
* **Q4 Retention.** Default 2 rolling years.
* **Q5 Odd-lot.** If KBar cannot separate odd-lot volume, accept the 2 % volume tolerance as the guard (default) or require a different source.

## 11. Unverified items

* KBar publication time (documented ≈ 15:50) must be confirmed by K1 from real `fetched_at` values.

* Whether `TaiwanStockKBar` accepts a date range (affects request count only).
* KBar volume unit and whether the closing-auction bar is present.
* Whether KBar history is available for the full two years for every active symbol (K2 reports per-symbol coverage).
* Whether the shared token's 6,000 requests/hour is enforced per token across datasets (assumed yes; hence the 25 % headroom).
* Early-close/half-day records in the governed calendar.
