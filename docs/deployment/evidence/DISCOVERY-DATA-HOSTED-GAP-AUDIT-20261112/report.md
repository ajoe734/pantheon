# DISCOVERY-DATA-HOSTED-GAP-AUDIT-20261112 — why catalogued datasets and two-year history are not on hosted, and what the existing flow needs

| Field | Value |
| --- | --- |
| Named audit | `SD_STRATEGY_SCREEN_ENTRY_EXIT_2026-11-12.md` §6 (approved PR 7223 / merge `5d8ec03a3`) |
| Repo base read | `dev` `890a97f38` (contains steady core PR 7225 / `54fc`, caller PR 7227 / `04e9`) |
| Hosted evidence | read-only private Ops observations 2026-11-12 08:50Z and 09:22Z (see §1) |
| Scope | investigation only. No runtime, config, deploy, provider, credential or business write. Does **not** claim any data has arrived, and does **not** close W1 (three governed days), W5 (twenty clean minutes) or financial-statement new-fill |
| Machine-readable twin | `evidence.json` (same directory) |

## 0. 中文摘要（操作者用）

- **目錄有、執行個體沒有、資料也沒有。** 程式裡登記了 14 個 Collector 定義和 1 個官方 TWSE/TPEx 定義，但 hosted 的 Source registry 只有 8 個舊式 connector，**0 個** 綁定 Collector 定義的執行個體。Collector 的穩定拉取模式（steady pull）只會挑「綁 Collector 定義、已啟用、有排程、已到期」的 connector，所以現在即使打開旗標也沒東西可拉。
- **唯一在跑的官方 connector 只要 `tw_price_daily`，而且被截成 100 筆。** 最近一次排程（09:21Z）收了 100 筆，全部是上市（TWSE），0 筆上櫃（TPEx），符號從 `00400A` 開始（代表是 OpenAPI 回傳的前 100 列，而非全市場）。月營收、財報、估值、融資融券等在程式裡都實作了，但沒有任何 instance 去請求它們；現有的 bounded refresh 腳本還會**明確拒絕** 非 `tw_price_daily` 的官方資料集。
- **兩年日線在 Collector 主機上抓到了（依設計文件），但 Pantheon 的 normalized store 沒有。** store 只有 61 個日期標籤（2026-08-12…2026-11-11），且這些是路徑標籤，不是發布時間證明。Collector 拉取有每次上限（預設 500 筆）和每日一次的排程，不調整就追不上兩年的量。
- **產業分類、時點正確（PIT）、下市股票歷史：沒有證據，保持「未驗證」。** 月營收列裡有一個當下快照的「產業別」欄位，不是 PIT 分類。
- **下一步全部走既有流程**（原 W1 / 唯一 COMPAT publisher、既有 deployment desired-state → persona-source reconciler（COLLECTOR-HOSTED-ADMISSION-20261112）、既有 exact-pair gate；Source management 指令維持關閉、UI legacy 不動作，不建議打開旗標），本審計不新增任何工具或權限。

## 1. Evidence used (all read-only, nothing copied wholesale)

Private files under `/tmp/pantheon-root01a15322-resume-20261111/` (credential-scanned by their authors; not reproduced here — only counts, public connector/dataset names, hashes and timestamps):

| Observation | Time (UTC) | Identity |
| --- | --- | --- |
| Owner registry GET (`/api/source-ingest/registry` via existing BFF viewer login; login/logout only writes) | 2026-11-12T09:22:17Z | wire 808,246 bytes, sha256 `becb36c5b753a98c9d9ad9017a9e0c7103c44239badd570abe0f3f9dc73c644b`; BFF `bee62bc2…`, FE `acf3efae…`, identity stable during capture; fixture unchanged after |
| Normalized-store listing + service env/mounts (no provider call, no write) | 2026-11-12T08:50:18Z | `source-ingest` image `sha256:238782113a721db91a65486ad79652510e3857fa44ab4ad4375a714208b0d5ef`, 4,771 files scanned, not truncated |
| Combined runtime+registry summary | 2026-11-12T09:27:33Z | observation_at 09:22:17Z |

Code identity: only the corrected 08:50Z `/workspace` module-hash proof and the 09:22Z owner registry are authoritative. The seven hashed modules (`controller_worker.py d63473bf…`, `controller_state.py f709543c…`, `pipeline.py 97f665e4…`, `runtime.py 6fe27737…`, `connectors/finmind_taiwan.py d3def6d3…`, `hypothesis_card.py 73a7fe28…`, `hypothesis_compiler.py 1da5b1c1…`) **match last-accepted Bee**; six of seven differ from the rolled-back qualified `69a` set (only `pipeline.py` equal). The earlier 08:40Z audit used a wrong `/app` module path and is not used. These are date-specific observations, **not** post-fix exact-pair QA, publication proof, PIT proof or a clean-day claim. Deployment may have been in flight (schedule rows for the TAIFEX and FinMind connectors were updated at 09:17Z and 09:21Z).

## 2. What is actually registered and running

Observed service posture (08:50Z): the API source container (`source-ingest`) has `PANTHEON_EXTERNAL_EGRESS=allowlist` with exactly one allowed host (the collector host); the scheduler container has `SOURCE_INGEST_CONTROLLER_MODE=reconcile_only`; `SOURCE_INGEST_STEADY_COLLECTOR_PULL` is unset. Both mount the same volume at `/data/source-ingest`.

Registry (09:22Z, legacy projection) lists **8** connectors, **`collector_definition_bound_instances = 0`**. Separately, the governed owner inventory read at **2026-11-12T09:32:13.184239Z** (HTTP 200, identity stable, sanitized proof sha256 `5558904df68705a8d78061651e1be5454c59ec503312ca992be07f39a5f7312a`) returned `sources: []`, **count = 0**. Both observations are cited independently: the 09:22Z legacy registry shows no definition-bound connectors, and the 09:32Z owner inventory shows no governed managed instances. Neither is a statement about the past: the **historical cause of the empty inventory is unknown** (an empty inventory does not prove there never were instances or that no data was lost; initialization/persistence/config paths were not audited).

| Connector | Status | Definition bound | Schedule | Note |
| --- | --- | --- | --- | --- |
| `tw-twse-tpex-official-market` | enabled | no (`definition_id` null) | enabled, 86400 s | request keys `dataset`,`venues`; request dataset `tw_price_daily`; `max_records` 100 |
| `tw-taifex-futures-options-chip` | disabled | no | configured, off | normalized `taifex_*` |
| `tw-finmind-broker-daily-report` | disabled | no | configured, off | `tw_broker_top` partition 2026-11-12 exists |
| `dev-paper-us-equity-simulation` | disabled | no | configured, off | explicit simulation |
| 4 × simulation / e2e connectors | enabled | no | none | `static_records`, not market data |

The registry's `financial_data_source_catalog` self-labels `template_only_not_live_ingestion_claim` with 18 entries (`financial_source_catalog.py:1657`): **catalogued ≠ registered ≠ ingested.**

Normalized store (08:50Z; labels are partition paths, not proven publication times): `tw_price_daily` 61 date labels 2026-08-12…2026-11-11 (1,923 files); `taifex_ssf_contracts` 5 labels 2026-11-08…2026-11-12; `tw_broker_top` 1 label 2026-11-12; `market` 15 labels (2026-09-13…2026-10-09, simulation era); `internal_note` 3. **No** directory for monthly revenue, financial statements, valuation, margin, institutional flow, securities lending, day trading, holiday calendar, TDCC, macro, community, news, or industry.

## 3. Dataset → definition → adapter → instance → storage map

Definitions in `services/source_ingestion/connector_definitions.py`; storage writer routes by record metadata dataset to `normalized/<dataset>/date=<label>/<run>.jsonl` (`market_data_storage.py:34`).

| Data | Definition / adapter (source) | Registered instance? | Ingested on hosted? | Demonstrated missing link |
| --- | --- | --- | --- | --- |
| Daily bars, recent | `tw-twse-tpex-official-market` → `TaiwanOfficialMarketDatasetAdapter` OpenAPI `STOCK_DAY_ALL` / TPEx daily close | yes (legacy, no `definition_id`) | yes, 61 labels | see §4.1: 100-row cap, TWSE-only slice in last run |
| Two-year bars | `collector-tw-twse-price-history`, `collector-tw-tpex-price-history` (bundle; `connector_definitions.py:326`), collector CLI `backfill` | **no** | **no** (store has 61 labels) | no definition-bound instance; steady pull has nothing to select |
| Institutional flow TWSE | `collector-tw-twse-t86` (bundle) | no | no | same |
| Institutional flow TPEx | official adapter, `tpex_3insti_daily_trading` (`taiwan_official.py` endpoints) | no (instance requests only `tw_price_daily`) | no | no request for this dataset |
| Margin/short, securities lending, day trading, holiday calendar | official adapter endpoints, status `implemented*` | no | no | same |
| Monthly revenue, quarterly statements, valuation | official adapter via `TAIWAN_OFFICIAL_FUNDAMENTAL_ENDPOINTS` (`taiwan_official.py:3172`: `t187ap05_*`, `t187ap06/07_*_ci`, `BWIBBU_ALL`/TPEx PE) | no | no | §4.2 |
| Macro (CBC/NDC/DGBAS) | `collector-tw-macro-{cbc,ndc,dgbas}` → raw `collector_macro_series`, derived `tw_macro_series` | no | no | no instance |
| Community / news | `collector-tw-{threads,instagram-popular,cmoney-forum,ptt-stock}`, `collector-youtube-finance`, `collector-news-rss`, `collector-gdelt` → `collector_social_posts`→`tw_community_daily` | no | no | no instance |
| FinMind reference prices | `tw-finmind-datasets` (`finmind_tw_price_reference`) | no | no | reference only; **must not** be relabelled `tw_price_daily` or backdated |
| Industry / sector (PIT) | none | n/a | none | no dataset; `industry_neutralize` parsed but rejected (`factor_rule.py:281,392`) |

### 3.1 Feature storage mapping (normalized vs feature, and what is usable)

Two distinct write targets exist (`market_data_storage.py:56-104,253-285`): `normalized/<dataset>/date=<label>/<run>.jsonl` holds the records; `features/<feature_target>/date=<label>/<run>.jsonl` receives only **metadata reference rows** (`source_id`, `connector_id`, `source_dataset`, `feature_dataset`, `feature_as_of_time`) and a `feature_rows` ref in the manifest. Those feature files carry no field values, so a `features/` ref is **not** usable feature data. Values reach factor rules through the governed reader `feature_catalogue.py:431 read_feature_series` / `:480 feature_inputs_for_factor_rule` over observation rows with `dataset/field/entity/event_time/available_time`, and the catalogue (`feature_catalogue.py:95-154`) maps `tw_price_daily`, `tw_monthly_revenue` (yoy/mom/revenue, lag 10 d), `tw_quarterly_statements` -> `tw_financial_statement` (lag 45 d), `tw_margin_transactions` -> `tw_margin_short_balance`, `tw_valuation_bwibbu`, `tw_institutional_flows`/`tpex_institutional_flows` -> `tw_institutional_flow`.

| Requested dataset | Normalized path (code) | Feature target routing (code) | Hosted observation |
| --- | --- | --- | --- |
| Official daily price | `normalized/tw_price_daily` | official definition `feature_targets=("returns",)` -> `features/returns` (refs only) | normalized: 61 labels; feature: **unobserved** (08:50Z scan did not report a `features/` tree) |
| Two-year price / T86 (Collector) | `normalized/tw_price_daily` etc. | no derived feature routing (`COLLECTOR_DERIVED_FEATURE_DATASETS` covers only social and macro) | none; unobserved |
| Monthly revenue, statements, valuation | `normalized/tw_monthly_revenue`, `tw_financial_statement`, `tw_valuation_bwibbu` (definition `storage_targets`) | **no** `feature_targets` declared; catalogue reader depends on observation rows | no directory; feature coverage **unobserved** |
| Margin, institutional flow | `normalized/tw_margin_short_balance`, `tw_institutional_flow` | none declared on the definition | none; feature **unobserved** |
| Macro | raw `collector_macro_series` (raw-only) | derived `tw_macro_series` -> `normalized/tw_macro_series` + `features/tw_macro_series` (`connector_definitions.py:361-376`) | none; unobserved |
| Community / news | raw `collector_social_posts` (raw-only) | derived `tw_community_daily` -> `normalized/` + `features/` | none; unobserved |
| Industry / sector | none | none | none |

Hosted feature coverage is **unobserved** for every dataset above unless a later read-only listing evidences it; metadata refs alone would not count as feature data.

## 4. Each demonstrated missing link

### 4.1 Legacy official connector: bounded to 100 rows, no TPEx in the last run

Sources: `provider_adapters.py:103-130` (`max_records = fetch.max_records or request.max_records or 100`; final `records[:max_records]`), `provider_adapters.py:453-` (`_taiwan_official`: venues `TWSE` then `TPEx`, then truncation). Observed 09:21:26Z run `ingest-69c252b7ee0b`: `raw_count=normalized_count=100`, and all 100 evidence admission keys are venue TWSE, first `00400A`, last `2330` (priority symbols last), **zero TPEx**. That is consistent with the venue loop filling the 100-row budget on TWSE; the exact ordering inside the 100 was not independently re-derived, and the share of the whole market covered per day in the 61 historical labels is **unknown** (only counts of files were read). Health counters: 62 successes / 30 failures of 92 attempts; last typed failure 2026-11-11T19:17:57Z `ExternalEgressBlocked host_not_allowlisted www.twse.com.tw` (caller `source_ingest.taiwan_official`, a website `STOCK_DAY` URL). The root cause of that failure, and why a scheduled run succeeded at 09:21Z while steady egress allows only the collector host, are **not established** here (the bounded profile may have been active); no guess is offered.

### 4.2 Fundamentals exist in code but nothing requests them

* Definition lists them (`connector_definitions.py:444,459`) and the adapter can fetch and normalize them (`taiwan_official.py:3449 fetch_fundamental_rows`, `:1734 fundamental_records`, `_taiwan_official` accepts `request.dataset`).
* Only one registered instance exists and its request dataset is `tw_price_daily`. `_taiwan_official` fetches **one dataset per request** (`provider_adapters.py:467`); `datasets` in the config schema is not consumed there.
* Persona-requirement reconciliation cannot bind them to the official connector: `_OFFICIAL_TW_DATASETS` is built only from `TAIWAN_OFFICIAL_ENDPOINTS` (`persona_source_reconciler.py:75`) and, run locally, contains `tw_price_daily, tw_institutional_flow, tw_margin_short_balance, tw_securities_lending, tw_day_trading, tw_holiday_calendar, tdcc_shareholding_distribution, taifex_futures_chip, taifex_options_chip` — **not** `tw_monthly_revenue`, `tw_financial_statement` or `tw_valuation_bwibbu`. Also one connector id may prove only one requirement (`persona_source_reconciler.py:241`, "singleton connector").
* The existing bounded official refresh does not request them either: `scripts/deploy_nonprod_vm.sh:1364-1365` rejects any official dataset other than `tw_price_daily` ("official TWSE/TPEx refresh does not admit dataset"), and the workflow pins `SOURCE_INGEST_BOUNDED_DATASET: tw_price_daily` (`.github/workflows/dev-tw-market-refresh.yml:211`; TAIFEX `taifex_ssf_contracts`, FinMind `tw_broker_top` are the other two). So "providers unavailable" is **not** supported; "no existing flow asks for them" is. Whether `openapi.twse.com.tw/v1/opendata/t187ap05_L` etc. actually respond to the dev VM was not tested (no provider authority).
* Rows are capped by the same `max_records` (definition default 100): a fundamentals instance needs a limit at least as large as the venue payload; payload sizes were not measured by this audit.

### 4.3 Collector sources: definitions without instances, so steady pull selects nothing

* 14 reviewed definitions (`COLLECTOR_SOURCE_SPECS`, `connector_definitions.py:326`) become instances only through a definition-bound provisioning owner: the existing persona-source reconciler (§6 step 3; the `COLLECTOR-HOSTED-ADMISSION-20261112` path) or, if ever authorised, Source management `create` (`source_management_commands.py:258`). Zero exist (§2).
* `select_collector_pull_connectors` (`controller_worker.py:1312`) requires, per connector: `configured`, in the current desired-state reconcile result, `metadata.definition_id` in the Collector definitions, persisted fetch `mode=provider_owned_adapter` + Collector bundle adapter token (`:1268`), `status=enabled`, schedule enabled with interval > 0, and readback `freshness.is_due`. The legacy connectors fail the definition test, so `SOURCE_INGEST_STEADY_COLLECTOR_PULL=true` alone creates nothing to pull (confirms the task premise; core/caller are source-DONE, not served after the failed Root `69a/82ff` rollback).
* The default desired state (`default_desired_state.json`) has a single deployment requirement `persona-source-ingest-public-market` for `tw_price_daily` via the legacy connector; it registers no Collector instance.
* **14 vs 16.** The repo defines 14 Collector definitions. `deploy/collector/fetch-policy.json` has 22 source entries (19 `enabled: true`, 3 false); 8 of them have **no** Pantheon definition: `collector-macro-dbnomics`, `collector-paper-arxiv`, `collector-paper-nber-rss`, `collector-paper-openalex`, `collector-papers`, `collector-tw-ptt-mirrors`, `collector-tw-twse-openapi`, `collector-youtube-gemini-notes`; all 14 definitions are present in the policy. The "16 sources" figure comes from the host `/status.json` on 2026-11-10 (`COLLECTOR-HOST-PROVISION-20261110`), whose source names are **not** in the evidence allowed for this audit. Do not equate 16 with 14. Names must be read from the live authenticated `/status.json` and intersected with the 14 definition ids before any instance is proposed.
* Pull is capped and cursored (`collector_bundle.py:467-540`, `total_cap = min(max_records, …)`, definition default `max_records 500`, `cadence_seconds` 86400 for price/T86). Two-year backfill is written by the collector as one bundle per trading date (≈487 per source; `COLLECTOR-PRICE-HISTORY-BACKFILL-20261111`). At the default cap and a daily schedule the Source side cannot catch up; the per-bundle row count was not measured here, so the required `max_records` / interval is **unknown** and must be sized from the real bundle index.

### 4.4 Management-command traps that would otherwise create false greens

* `create` always starts `configured_disabled` with the schedule disabled (`:385-386`); `enable` requires a passed canary (`:861-…`). The canary `provider_read` and `source_normalized` stages are **fixed-value stages** (`"records_fetched": 10`, `source_management_commands.py:595`), i.e. a passed canary performs no provider/collector read. Passing canary/enable is therefore **not** data evidence; only a scheduled-run receipt with the dataset in the normalized store is.
* `_materialize_connector_runtime` (`:1490`) projects `schedule.interval_seconds` (default 86400) into the schedule store; `change_schedule` (`:1239`) requires a `cadence` string. Set `interval_seconds` explicitly.
* Management-created connectors carry no `approved`/`approval_status` metadata, while the `require_connector_approved` gate passes only for that or a built-in factory (`persona_source_reconciler.py:375-384`). A requirement that keeps that gate for a managed Collector connector will fail as `policy_gate_failed` and never enter the reconcile result. Use `require_schedule_active`, `require_source_health_ok`, `no_live_capital` (and `public-source-only` for auth-less sources) for these requirements.
* Both write surfaces are default-off and fail closed: Source `SOURCE_MANAGEMENT_COMMANDS_ENABLED=1` (`routers/management.py:38`) and BFF `PANTHEON_BFF_SOURCE_MANAGEMENT_COMMANDS_ENABLED=1` (`console_gap/datasources.py:53`). Their hosted state was not observed, and the current authorised posture is **commands off / UI legacy no-action** (§6); this audit does not recommend flipping them.

## 5. Raw coverage vs official truth vs point-in-time

| Layer | Status (this audit) |
| --- | --- |
| Raw collector bundles for two years | **Unverified.** Only the design/index claim (backfill finished 2026-11-11T12:37Z) and evidence of CLI and ledger path (`/data/state/backfill/<source>.json`). No allowed evidence reads the host bundle index. |
| Normalized official `tw_price_daily` two-year history | **Absent** (61 labels, ≤ 2026-11-11, recent OpenAPI slice). Raw bundle presence would not by itself make it normalized truth. |
| Publication / PIT availability | **Unproven.** Partition labels are paths. Collector rows are stamped `published_at = <trade_date>T06:30:00Z` (T86 `08:30Z`) by the collector; that is a collector-side convention, not proof of when Pantheon could have known it. Backdating availability is not allowed. |
| FinMind reference closes | Reference-only dataset `finmind_tw_price_reference`; never `tw_price_daily`. |
| Industry classification PIT, delisted/suspended history | **Unverified / absent.** Monthly-revenue rows carry `industry` from the filing snapshot (`taiwan_official.py:3327`), a present-day label, not a PIT classification. Belongs to `TW-INDUSTRY-CLASSIFICATION-DATASET`; no fix invented here. |

## 6. Minimal existing-flow activation / repair matrix (for original Ops owners)

No new tool, service, grant, issuer, endpoint, queue or platform. Executed only by the original W1 / sole COMPAT publisher inside the same exact-pair gate-before-switch/rollback flow. This audit performed none of it.

**Authorised path (current).** The original native Human/Ops 09:44:15Z instruction and acceptance keep Source management commands **OFF** and the UI legacy path **no-action**. This report therefore does **not** prescribe setting `SOURCE_MANAGEMENT_COMMANDS_ENABLED` or the BFF counterpart, waiving any policy gate, or registering sources by hand through compatibility routes. Source provisioning is owned by the already-admitted task `COLLECTOR-HOSTED-ADMISSION-20261112` (Claude/Codex, in_progress 09:45:58Z), which uses the one existing mechanism: deployment desired-state holder -> existing persona-source reconciler provisions from the actual `ConnectorDefinition` -> existing registry/scheduler -> steady Collector pull. No manual API registration and no second mechanism. That task is the owner of the still-pending source connection; its creation is **not** evidence that anything is implemented or deployed. The genuine O6 / open-source holds, existing flags, tenant, secret, PIT and daily truth, and the runtime uncertainty in §7 all stand.

| # | Step | Existing entry point | Required non-secret input | Check before moving on |
| --- | --- | --- | --- | --- |
| 0 | Serve the reviewed core+caller release | existing release/exact-pair flow (core `54fc`, caller `04e9`) | - | source modules on host match the release |
| 1 | Resolve real Collector names | authenticated collector `/status.json` and `/bundles/index.json` (read-only, existing bundle token path) | - | host source ids intersected with the 14 definition ids; per-source bundle count and newest/oldest date for the price/flow sources; this alone decides raw coverage |
| 2 | Declare the requirement in the deployment holder | deployment desired-state file (`SOURCE_INGEST_DESIRED_STATE_PATH`, holder `persona-source-ingest-public-market`) via `COLLECTOR-HOSTED-ADMISSION-20261112` | requirement `{dataset, market:"TW", cadence, source_class:"live_pull", connector_candidates:[...], policy_gates:[...]}`; one requirement per connector id; keep the real policy gates (do not drop `require_connector_approved` to force a pass; if a gate cannot pass for a Collector-bound connector that is an owner finding for that task, not something to waive here) | reconcile `results[].actions[].connector_id` lists the connector with a non-`skipped` status |
| 3 | Reconciler provisions from the ConnectorDefinition | existing `persona_source_reconciler.py` / registry / scheduler (no management command) | the connector, fetch mode `provider_owned_adapter` + Collector bundle adapter token and `limits` come from the definition defaults (`max_records` 500 for Collector definitions, 100 for the official definition) | registry shows `definition_id` bound and `collector_definition_bound_instances > 0` and a governed owner inventory `count > 0` |
| 4 | Limits and cadence are what the reconciler yields | `_ensure_schedule` (`persona_source_reconciler.py:561-597`) sets `interval_seconds` from the requirement cadence (`daily` = 86400) and **overwrites** any custom interval on every reconcile | cadence is set only by the requirement's `cadence`; a custom interval is not a supported lever on this path | effective schedule row equals the cadence-derived interval |
| 5 | Sizing `limits.max_records` | **not available later** through the existing commands: `change_schedule` (`source_management_commands.py:1239-1274`) preserves `limits=desired.limits`, and BFF `services/control-plane/bff/console_gap/datasources.py:1143-1161` forwards only `schedule`. Measured/sized limits belong at creation time (the definition default or the create payload), and repairing limits afterwards is an unsupported change that would need its own separately reviewed owner change | size from the step-1 bundle index (unknown today) before the first provisioning | cursor advances per run (`resume_cursor.json`); sizing remains **unknown** until measured |
| 6 | Opt in steady pull | `SOURCE_INGEST_STEADY_COLLECTOR_PULL=true` (+ `PANTHEON_COLLECTOR_HOST`), existing workflow var wired by `04e9` | deploy var | scheduler `collector_steady_pull`; egress = collector host only |
| 7 | Fundamentals | one official-connector requirement per dataset (`tw_monthly_revenue`, `tw_financial_statement`, `tw_valuation_bwibbu`) | these datasets are **not** in `_OFFICIAL_TW_DATASETS` (§4.2), so the existing reconciler cannot bind them; direct OpenAPI egress is not in the steady allowlist. Needs a separately reviewed owner decision (extend reconciler / bounded profile); not solved here | a scheduled receipt and a `normalized/<dataset>` directory |
| 8 | Legacy price slice | existing legacy instance `limits.max_records` / `priority_symbols` | cap large enough to cover both venues; required value unknown | TPEx rows in a run |
| 9 | Industry, delisted history | out of this audit's authority | - | stays unverified until `TW-INDUSTRY-CLASSIFICATION-DATASET` |

**Only if the owners later authorise the management-command surface** (it is not authorised now): each command requires a non-empty `reason` (`source_management_commands.py:204`) and an `Idempotency-Key` header (replay with a different payload gives 409, `:216-225`); `create` takes `source_instance_id`, `connector_id`, `definition_id`, `limits`, `schedule.interval_seconds`, `connector_config.public`; `change_schedule` needs `schedule.cadence` and `expected_revision`; `enable` needs a passed canary, `expected_revision` and the `enable_schedule` confirmation (`:888`). Canary is not data proof (§4.4).

## 7. Explicit unknowns (kept, not assumed)

1. Which 16 sources the host `/status.json` reports and the live raw bundle coverage per source.
2. Row counts per Collector bundle and TWSE/TPEx fundamentals payload sizes (needed for `max_records`).
3. Per-day symbol coverage inside the 61 `tw_price_daily` labels.
4. Why the legacy connector failed with `host_not_allowlisted` on 2026-11-11T19:17:57Z and how the 09:21Z scheduled success obtained egress.
5. Hosted values of the two management-command flags (authorised posture is off; not flipped by this audit). Also: the historical cause of the empty governed owner inventory.
6. Whether TWSE/TPEx fundamentals endpoints respond from the dev VM.
7. Industry classification source, PIT history and delisted-symbol history.
8. Whether any watchdog or GitHub outage contributed: no evidence read; no cause is asserted.

## 8. Non-claims

Not claimed: any hosted ingestion, W1 three governed days, W5 twenty clean minutes, financial-statement new-fill, exact-pair QA, current publication, PIT availability, or that the two-year history was imported. Pattern remains CLOSED; baseline 7228, FE transport fix, K3 source writers and the thirteen-query / four-role Persona Agora QA are untouched; the protected StageProd pair is separate. Fixed personas P and persona-per-strategy tasks are not revived: the strategy generates candidate pool C(D) from the governed boundary M(D) with separate screen / entry / exit.
