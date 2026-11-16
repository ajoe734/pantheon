# SD: Market Data Is Written Once Per Trading Day And Read As Windows

Task: SD-MARKET-DATA-ONE-WRITE-PER-DAY-20261116 (design only, no implementation code)
Date: 2026-11-16
Parent design: `DATA_STRATEGY_SOURCE_SYSTEM_DESIGN.md` sections 4.4, 4.5, 14
Operator direction (2026-11-16, verbatim): 過期就刪掉，封存幹嘛? 另外你這是快照，如果需要往前看資料呢?

## 0. Decisions in one screen

1. **One store of record: `daily_bars`**, one row per (market, symbol, trade_date), partitioned by trade date, written once. Everything else is a pointer or a derived, bounded view.
2. **The snapshot stops being a window store.** `latest_market_snapshots` keeps only the newest close per (symbol, dataset) plus a pointer (`window_ref`: dataset, symbol, last trade_date). It is a few hundred bytes per key, not 1.5 to 71 KB.
3. **Every window reader goes through one bounded reader**, `read_close_window(symbol, end_date, n)` / `read_range(symbols, start, end)`, over the date partitions. No reader parses the journal for history.
4. **Receipts carry references and counts only.** No snapshot body, no per-record list. (Receipt must stay a complete record of what ran; it need not contain the data.)
5. **Expiry is deletion, not archival.** Invariant 6 covers data source configuration, not market data rows (see section 4). Exactly one copy of what a backtest or a gate proof needs is kept; everything superseded or past retention is deleted.
6. **Every store has a per-trading-day size and a total bound plus an alarm** (section 6).
7. **No new heavy dependency.** `pyarrow` is already pinned in `services/source_ingestion/requirements.txt` (`pyarrow>=15,<30`). Parquet files read by pyarrow is the adopted part; DuckDB is an optional reader, not required (section 5).

## 1. Correction to the coordinator finding (read before implementing)

The task note says `MarketDataStorageWriter.write_run` partitions by the ingest run finish date. The code on this branch does not do that for records that carry a date:

- `market_data_storage.py:_date_for_record` takes `trade_date`, `as_of_date`, `date`, `event_time`, `available_time` from record metadata (first non-empty, first 10 chars), and only falls back to `utc_now_iso()[:10]` when none exists. The `run_date` variable inside `write_run` is that value, not `pipeline.run_date(run)`.
- `pipeline.run_date(run)` (run finish date) is used for usage accounting (`record_ingest_usage`), not for storage partitions.
- `_persist_collector_market_storage` also keys the replay check by `event_time[:10]`.

So a two year backfill lands in `date=<trade date>` when the collector sets `event_time` / `trade_date` on each record. The real problems with the layout are different and still sufficient to need this design:

- **Each partition holds one file per `ingest_run_id`** (`<run_id>.jsonl[.gz]`). A trade date for 17200 symbols at about 500 records per batch is about 35 files, and every re-run adds another full set. Nothing says which file is the current one for a symbol, so "one write per day" is not true even when the partition is right.
- **No symbol index.** To get 60 closes of one symbol, a reader must open every file of 60 date partitions.
- **Rows are whole `SourceRecord` dumps** (raw), and a normalized line copies `metadata` including lineage, so a close is about 1.2 to 1.5 KB.
- **Fallback to wall-clock date** exists for any record lacking a date; such rows land under the ingest day. Needs measurement (section 2.3, M1) because I cannot read the hosted volume from this branch.

If M1 shows a material share in the fallback partition, the implementation task T1 must fail closed on a missing trade date instead of falling back.

## 2. Inventory: where one Taiwan daily close is persisted

Sizes use the coordinator's hosted measurements of 2026-11-16 (stack e7ec353d6) unless marked "design estimate".

### 2.1 Places and copies

| # | Store | Form | Size per close | Rewritten on new day? |
|---|---|---|---|---|
| A | `market_data_store/raw/<source>/<dataset>/date=D/<run>.jsonl[.gz]` | full `SourceRecord.to_dict()` | about 1.5 KB uncompressed (gzip when policy says) | no, but one more copy per re-run |
| B | `market_data_store/normalized/<dataset>/date=D/<run>.jsonl` | source_id, connector, content_ref, full `metadata` | about 1.2 to 1.5 KB | no, one more copy per re-run |
| C | `market_data_store/features/<feature>/date=D/<run>.jsonl` | per record feature stub | small (about 200 B), written only when `_feature_targets` is non-empty | no |
| D | `latest_market_snapshots.jsonl` (`LatestMarketSnapshotStore`) | one line per (symbol, dataset) with last 60 closes plus about 1.2 KB lineage and calendar evidence per close | line is 1.5 KB at 1 close, about 71 KB at 60 | **yes, whole line every new day**; compacts in place above 3x live size |
| E | `ingest_schedule.jsonl` ingest_receipt lines | `evidence_refs["market_snapshots"]` = whole `persist_latest_market_snapshots` batch | one batch (496 records) = 15.2 MB, so about 30 KB per record, about 20x a close | no, append only; replayed whole into memory at start |
| F | source_evidence, search and distillation rows (`persist_source_evidence_refs`, `distillation_job_queue`) | per record evidence item and distillation job | not measured | no |
| G | `source_usage_daily`, health | counters | negligible | upsert per day |
| H | consumers' own copies: `binding_metadata.recent_closes`, `pool_market_inputs`, portfolio_envelope `closes` | copies of the window inside bindings and artifacts | 60 floats (about 0.5 KB) | per binding |

(F and H sizes are "not measured"; they are listed so the migration does not miss them.)

### 2.2 Copy count of one close

- **Two year backfill (one close, one run):** A + B + (C) + D (rewritten once per batch that touches that symbol, so up to the number of batches that carry that symbol; a symbol history split over N batches rewrites its line N times) + E (about 30 KB, bigger than all other copies together) + F. That is **5 persisted copies, 6 with features**, and the one in E is the largest. Re-running the backfill adds another A and B.
- **Steady daily operation:** same set, plus D rewritten once per trading day with the last 60 closes, so the day's new close is physically re-stored 60 times over its life in D (each of the next 59 days rewrites it again), and a re-run of the same day adds A, B, E again.

Hosted facts that follow: D is 171 MB live but 6411 to 6500 of about 17200 live keys have no close after 2024-12; E is 2.2 to 2.4 GB at about 9 MB per minute; the market_data_store is 2.4 GB and no code in `services/source_ingestion` deletes `retention_days` expired partitions.

### 2.3 Further read-only measurements the coordinator must take

- **M1** Count normalized rows whose `event_time[:10]` differs from the partition `date=`; list partitions created after 2026-11-10 with trade dates older than 5 days. Tells whether the fallback date is in use.
- **M2** Files per (dataset, date) partition and total partition count for `tw_price_daily`; share of rows duplicated across run files (same symbol, trade_date, content_hash).
- **M3** Share of D bytes by lineage / calendar evidence versus prices (confirms 1.2 KB of 1.5 KB).
- **M4** Size of F (source_evidence, search, distillation tables) per close and total.
- **M5** Count of live D keys by symbol class (listed stock, ETF, warrant, delisted) with last close date, to size the delete list.
- **M6** Wall time and RSS of a cold `JsonlIngestScheduleStore` replay (task note: 5.9 to 6.4 GiB source-ingest).
- **M7** Does any strategy, gate or persona dataset read warrants (feeds the operator decision in section 4.3).

## 3. Follow or depart from sections 4.4, 4.5, 14

| Later addition | Verdict | Reason |
|---|---|---|
| Latest market snapshot journal | **Departs from 4.5 in the window part; follows it for the newest-close part.** 4.5 says normalized data is stored by source, dataset, date. A 60 day window inside a mutable per-symbol line is a second store of normalized rows. | Replace by pointer plus newest close. |
| Ingest receipts carrying snapshot bodies | **Departs.** 4.5 puts data in raw/normalized with `raw_ref`; tw_broker_top already uses `raw_ref` and `ingest_run_id` references. A receipt body is a third copy. | Receipts carry refs and counts. |
| Per point lineage and calendar evidence (about 1.2 KB per close) | **Departs from "compact normalized table" (4.5) and invariant 7 is satisfied but wastefully.** Invariant 7 asks for evidence lineage, not a copy per point. | Lineage is stored once per (run, partition) and per (trade_date, calendar) and referenced by id from the bar row. |
| Collector two year backfill of every listed and delisted symbol | **Departs from 4.4 for the live/warrant part; follows it for delisted shares.** 4.4: no full-market full-depth for every dataset every day; archive_universe keeps "daily price and material events only". A one-off history backfill is allowed; rewriting it every day is not. | Delisted shares keep daily history (survivorship, see SCREEN-SURVIVORSHIP-ATTESTATION-20261115, TW-SUSPENSION-HOLIDAYS-INTAKE-20261115). Warrants are an operator decision. |
| Invariant 6 ("Hard delete is exceptional; retire is the default") | **Does not apply to market data rows.** It sits in the product-object list (invariants 1 to 5 are about data source and seed source objects, section 4.6 lifecycle). Retire/archive applies to a data source configuration, not to bars. | Operator direction; see section 4. |

## 4. Target and expiry

### 4.1 Store of record

```
market_data_store/daily_bars/market=TW/date=YYYY-MM-DD/part-<n>.parquet   # one logical write per (date, symbol)
market_data_store/daily_bars/_manifest/date=YYYY-MM-DD.json              # file list, row_count, symbol_count, content digest
market_data_store/lineage/date=YYYY-MM-DD.json                           # calendar evidence once per day; run and source refs once per run
```

Row: `market, symbol, trade_date, open, high, low, close, volume, adjusted flag, source_id, ingest_run_id, lineage_ref, content_hash` (about 60 bytes columnar compressed; design estimate). Key (market, symbol, trade_date) is unique. A re-run of the same day replaces that day's partition by atomic rename of a new part set plus manifest swap (write temp, fsync, rename manifest last), so a reader sees the old or the new complete set, never a mix. A re-run whose content digest is unchanged writes nothing.

`trade_date` is required and comes from the record's trading date; a record without one is rejected to a dead-letter count, never filed under the ingest day.

### 4.2 Snapshot after the change

`latest_market_snapshots` line per (symbol, dataset): `symbol, dataset, last_trade_date, close, event_time, source/lineage summary for that one close, window_ref`. About 0.5 KB, rewritten only when a newer trade date arrives. The endpoint `GET /api/source-ingest/snapshots/latest?symbol=` keeps its response shape; `closes` is filled by the bounded reader for the requested `n` (default 60) so existing consumers keep working while they are migrated (rollout rule in section 7).

### 4.3 Expiry rules (deletion, not archive)

| Data | Rule | Enforced by |
|---|---|---|
| Daily bars of **delisted shares** | **Kept** (the only copy a backtest and the survivorship gate proof need); frozen, never rewritten | partition immutability; no delete |
| Daily bars of live symbols | keep `retention_days` (2555 for prices) then delete the partition | retention sweeper (T5) |
| Snapshot lines of delisted or expired symbols | **deleted** (the bars remain in daily_bars) | T3 sweeper, at the first run after the symbol has no close for 10 trading days and is marked delisted by the calendar/listing source |
| Superseded snapshot versions in the journal | deleted by compaction (already exists; keep) | `LatestMarketSnapshotStore.compact` |
| Snapshot bodies inside receipts | deleted (rewritten out during receipt migration); future receipts never contain them | T2 |
| Raw and normalized run files (A, B, C) after daily_bars verified | deleted after a verification window (default 30 days) | T4 |
| Partitions past `retention_days` | deleted | T5 |
| Lineage records of deleted partitions | deleted with them; lineage of kept bars is kept | T5 |
| Gate proof or lineage references | A bar referenced by a gate proof / paper session evidence is not past retention by definition: its `lineage_ref` and partition are pinned (`pin` list in manifest) | T5 test |

"Expired warrants" (TPEx warrants, about 6400 live-symbol keys without a close after 2024-12 are mostly these plus delisted shares): **operator decision.** None of the three trading personas uses warrants today. Options and cost (design estimate, M5/M7 to confirm): (a) do not fetch or keep warrants: saves about 4 to 5 million of the 8.6 million two-year closes (roughly half the store) and the collector time for them; (b) fetch, keep only while listed, delete at expiry: bounded, small steady cost; (c) keep forever: about +350 MB per 2 years columnar and un-bounded growth. Recommendation: (a) until a strategy or gate declares a warrant dependency, and delete existing warrant keys from D and the market store once the operator approves.

## 5. Readers and the one bounded reader

Compared ways to store and serve daily bars (sources read 2026-11-16):

| Option | What it is | Fit |
|---|---|---|
| Hive-partitioned Parquet with an embedded engine (DuckDB). DuckDB docs: a `key=value` folder layout read with `hive_partitioning` exposes folder values as columns, and a filter like `year = 2022 AND month = 11` reads only the matching folders ([DuckDB Hive partitioning](https://duckdb.org/docs/data/partitioning/hive_partitioning)) | partition pruning by date, columnar | **Adopt the layout and pruning idea.** Pantheon already pins pyarrow, which reads and writes the same Hive-style layout with partition filters; DuckDB is optional and not added. |
| LEAN data folders. LEAN daily equity bars are `equity/usa/daily/<ticker>.zip` with one `<ticker>.csv` per symbol, whereas tick/second/minute are per date files ([QuantConnect LEAN data format](https://www.quantconnect.com/docs/v2/lean-engine/data-format/us-equity)); prices stored scaled by 10,000 | one file per symbol, whole history | **Partly adopt, only as an export.** Per-symbol whole-history files are exactly the "rewrite everything for one new day" shape we are removing; they suit LEAN's reader, so a LEAN-folder exporter for the lean_runtime (T6, optional) can be derived from daily_bars. |
| Zipline bundles. `ingest` loads data and passes it to writers; a bcolz daily bar writer produces the format read by `BcolzDailyBarReader`; a sid may appear several times if dates strictly increase; metadata in SQLite ([zipline bundles docs](https://zipline-trader.readthedocs.io/en/stable/bundles.html)); one fork replaced bcolz with Parquet via PyArrow | whole-bundle ingest, calendar-aligned arrays | **Adopt one idea: calendar-aligned sessions and the strict-increasing-date check.** Not adopted: bcolz (unmaintained, new dependency) and whole-bundle re-ingest. |

Decision: Hive-style date partitions of Parquet, read through a small `daily_bars_reader` module on pyarrow (dataset with partition filter on `date` and `symbol` filters pushed into row groups; rows sorted by symbol within a part so row-group statistics prune). Reason: matches 4.5 (store by source/dataset/date), needs no new dependency, makes "read any past range by trading date" a partition-pruned read.

### 5.1 Reader contract

`read_close_window(symbols, end_date, n)` -> per symbol the last `n` trade-date bars with `date <= end_date`. Implementation: resolve the last `n` trading days from the calendar (one small manifest read), open only those `n` partitions, push down `symbol IN (...)`, return rows. Cost is O(n partitions x row groups touched), independent of how many years are stored. `read_range(symbols, start, end)` is the same with an explicit range and a hard cap (default 800 trading days, 3 years) and a symbol cap (default 500 per call) so a caller cannot trigger a full scan.

### 5.2 Per reader

Read latency estimates at hosted volume (17200 symbols; 60 partitions of about 0.7 MB; design estimates, to be measured by the T1/T4 hosted-shaped benchmark; target p95 shown).

| Reader | Today | Target | p95 target |
|---|---|---|---|
| `services/execution/lean_runtime/paper_signal_producer.py` (`recent_closes`, pool market inputs) | closes from binding metadata / `/snapshots/latest` | endpoint answers from the reader for the bound symbols (pool, <= 50 symbols): 60 partitions, symbol filter | <= 150 ms per call, one call per pool tick |
| `services/execution/market_snapshot_admission.py` | needs newest close, event time, lineage validity | reads the snapshot newest-close line only (0.5 KB), no window | <= 5 ms |
| `services/registry/portfolio_envelope.py` (`closes` argument) | window supplied by caller from snapshot | caller gets it from the reader; module stays pure | n/a (pure); caller <= 150 ms |
| `services/execution/lean_runtime/performance_telemetry.py` (`MarketMark` from `snapshots/latest`) | newest close | newest close from snapshot line | <= 5 ms per symbol |
| `scripts/deploy_nonprod_vm.sh` bounded refresh preflight (reads `closes` of one symbol) | snapshot `closes` | same endpoint, reader behind it; preflight asks `n=60` for one symbol | <= 100 ms |
| `scripts/run_isolated_l12_runtime_e2e.py` (2330.TW `closes`) | snapshot `closes` | same endpoint | <= 100 ms |
| Gate and pool datasets, backtests (date range) | scan journal / partitions | `read_range` with caps | 2 years x 1 symbol <= 500 ms; 500 symbols x 250 days <= 3 s |
| BFF and Agora projections | pass through source-ingest endpoints (BFF does not read volumes, section 4.4 note) | unchanged; endpoint is the single reader | unchanged |

Hosted-shaped acceptance (not a three symbol fixture): 17200 symbols x 500 trading days x row-groups of 4000 rows, generated, with 6500 dead symbols.

## 6. Growth budget

Volume: 17200 symbols (about 10700 live), two years (about 500 trading days), collector batches of about 500 records. Estimates are design targets, to be replaced with measured values in T1.

| Store | Size per trading day | Total bound | Alarm |
|---|---|---|---|
| `daily_bars` (live + delisted) | <= 1.2 MB (17200 x about 60 B compressed, with margin) | <= 1.0 GB at 2 years; delisted history grows only with delistings | alarm if a day exceeds 2 MB or total exceeds 1.5 GB |
| `lineage` | <= 0.3 MB (calendar evidence once per day, run refs once per run) | <= 150 MB | alarm at 1.5x |
| `latest_market_snapshots` | rewrites only keys with a newer date: <= 10700 x 0.5 KB = 5.4 MB per day written | live <= 10 MB (about 17200 x 0.5 KB = 8.6 MB); file <= 3x live (existing compaction) = 30 MB | alarm if live bytes > 15 MB or any line > 4 KB |
| `ingest_schedule.jsonl` receipts | about 35 batches per day x <= 8 KB = <= 0.3 MB; no line > 16 KB | <= 200 MB for retained receipts (retention by count of days, default 30) | alarm if any receipt line > 16 KB, or file growth > 5 MB per hour |
| raw/normalized run files (transition only) | same as today until T4 | removed by T4 after verification window | alarm if still present 60 days after T4 |
| source_evidence, distillation | target: no per-close evidence row; one evidence item per run (about 35 per day) | <= 50 MB | alarm at 2x of M4 baseline |
| in-memory receipt replay | n/a | source-ingest RSS bound set from M6 (target < 1 GiB for receipt store) | alarm on RSS above bound |

Review check for each later implementation PR: the PR evidence must include (1) the budget table row for each store it touches with the measured value on hosted-shaped data, (2) a test that fails when the per-day or per-line limit is exceeded (budget test is an assertion on bytes, not a log line), (3) the alarm name and where it is emitted (source-ingest health payload field, so Ops can see it), and (4) a statement of which stores the PR does not touch. A reviewer rejects a PR that adds a store without a row.

## 7. Migration and rollback

Principles: additive first, old reader keeps working, each step crash safe, deletion last and only after verification.

1. **T0 (prerequisite, immediate):** keep the collector price history sources disabled until T2 is deployed (operator-approved since 2026-11-16T14:06Z). Re-enable condition: receipts no longer contain `market_snapshots.snapshots`; verified by a hosted receipt line size <= 16 KB for a 500 record batch and `ingest_schedule.jsonl` growth <= 5 MB per hour for one hour.
2. **T2 receipts** (stop the bleeding): new receipts carry counts and refs only. Old journal untouched. Rollback: revert; old receipts still readable.
3. **T1 daily_bars writer, dual write:** the pipeline writes daily_bars in addition to raw/normalized. Old stores unchanged. Rollback: stop writing; delete `daily_bars`.
4. **Backfill daily_bars from the existing market_data_store** (offline job): read normalized `date=` partitions oldest to newest, dedupe by (symbol, trade_date, content_hash) keeping the newest run, write one temp partition, verify row and symbol counts against the manifest, atomically publish the manifest. Resumable by date (each date partition is independent and idempotent; a crash leaves an unpublished temp dir that the next run removes).
5. **Reader switch behind a flag:** `/snapshots/latest` fills `closes` from daily_bars when `MARKET_WINDOW_SOURCE=daily_bars`; default stays on the journal. Compare mode runs both and logs mismatches (target 0 over 5 trading days). Consumers do not change (same JSON).
6. **Snapshot slimming (T3):** write new slim lines; the store keeps reading the old fat lines (`closes` present) and new lines (`window_ref`). Compaction rewrites to slim form. Rollback: flag back to journal; fat lines remain until compaction, and compaction to slim form only starts after step 5 shows 0 mismatches.
7. **Receipt journal rewrite (T2b):** offline: stream `ingest_schedule.jsonl` to a new file replacing `market_snapshots.snapshots` with counts, write temp, verify line count and receipt ids, rename; keep the old file for 7 days. This also fixes the 5.9 to 6.4 GiB replay: the file drops from 2.2 GB to about tens of MB. Rollback: rename back.
8. **Delete (T4, T5)** after 5 trading days of zero mismatch: raw/normalized run files older than the verification window; partitions past `retention_days`; snapshot lines of delisted symbols; warrant keys only after the operator decision.

Rollout order on hosted: nonprod VM (e7ec353d6 stack) first with the hosted-shaped benchmark, then production; collector sources re-enabled only after T2 evidence on production. Each step records a manifest digest in evidence so rollback can be checked.

## 8. In-flight tasks

| Task | Verdict | Why |
|---|---|---|
| SOURCE-INGEST-RECEIPT-SNAPSHOT-BODIES-BOUND-20261116 | **Stays and becomes T2** (counts and refs in receipts; receipt migration). Review it against section 6 limits; if it only truncates a body, extend it to remove the body. | Same scope as target item 4. |
| SOURCE-SNAPSHOT-INCREMENTAL-READ-20261116 | **Replaced** by T1+T3 for the window part. It only makes the same rewritten-line store cheaper to read. Keep only if it is already merged-ready and limited to newest-close read; otherwise cancel. | Reader over date partitions makes it unnecessary. |
| SOURCE-SNAPSHOT-JOURNAL-BOUNDED-20261115 (merged) | **Stays.** Dedup and the compaction (`_compact_journal_in_place`) remain the journal mechanism for the slim snapshot. | Does not conflict. |
| SOURCE-SNAPSHOT-JOURNAL-COMPACTION-20261115 (PR #7414) | **Stays** (single compaction implementation per Human/Ops 2026-11-15). | Same. |
| Paused collector sources `collector-tw-twse-price-history`, `collector-tw-tpex-price-history` | **Stay disabled**; re-enable per T0 condition above. TPEx warrant records excluded until the operator decision. | Prevents 9 MB/min growth. |

## 9. Implementation tasks (no code in this task)

Each task: acceptance includes its growth budget row and a test on hosted-shaped data (17200 symbols, 500 days, 6500 dead, 500-record batches), not a three symbol fixture.

| Task | Files | Acceptance |
|---|---|---|
| **T1 SOURCE-DAILY-BARS-STORE-20261117** daily_bars writer, manifest, reader, dual write | new `services/source_ingestion/daily_bars_store.py`, `daily_bars_reader.py`; edit `services/source_ingestion/market_data_storage.py`, `pipeline.py` (`persist_market_data_storage_refs`); tests `services/source_ingestion/tests/test_daily_bars_store.py` | unique (symbol, trade_date); re-run of same content writes nothing; missing trade_date rejected; crash between temp write and manifest rename leaves old state; `read_close_window` p95 <= 150 ms for 50 symbols and `read_range` limits enforced on hosted-shaped data; day size <= 1.2 MB; budget alarm fields in health |
| **T2 SOURCE-RECEIPT-REFS-ONLY-20261117** (supersedes/extends SOURCE-INGEST-RECEIPT-SNAPSHOT-BODIES-BOUND-20261116) | `services/source_ingestion/pipeline.py` (`persist_latest_market_snapshots`, `persist_ingest_receipt`), `scheduler.py` (`JsonlIngestScheduleStore` retention and replay), `scripts/` receipt migration script; tests | 500-record batch receipt line <= 16 KB; receipt journal rewrite verifies receipt ids; cold replay RSS < 1 GiB on a 2.4 GB-shaped file converted; collector re-enable evidence |
| **T3 SOURCE-SNAPSHOT-SLIM-20261117** | `services/source_ingestion/requirement_state.py` (`LatestMarketSnapshotStore`), `services/source_ingestion/api` snapshot endpoint, `services/execution/lean_runtime/performance_telemetry.py` (verify only) | line <= 4 KB; live <= 15 MB at 17200 keys; flag `MARKET_WINDOW_SOURCE`; compare mode 0 mismatches on hosted-shaped data; delisted keys swept |
| **T4 SOURCE-MARKET-STORE-BACKFILL-AND-CLEANUP-20261118** | `scripts/` backfill and cleanup job; `services/source_ingestion/market_data_storage.py` | idempotent resumable backfill; count equality vs normalized dedupe; deletion only after verification window; evidence of size before/after |
| **T5 SOURCE-RETENTION-SWEEPER-20261118** | `services/source_ingestion/retention.py`; tests | deletes partitions past `retention_days`; never deletes pinned or delisted-share bars; alarm when not run in 2 days |
| **T6 (optional) LEAN-DAILY-EXPORT-20261120** | `services/execution/lean_runtime/` exporter | only if lean runtime needs LEAN data folders; derived from daily_bars |
| **T7 MARKET-DATA-BUDGET-CHECK-20261118** | `scripts/check_market_data_budget.py`, CI | fails when a store row in section 6 is exceeded on hosted-shaped data |

Operator decision open: warrants (section 4.3).

## 10. 中文摘要（給 operator）

**現在一筆收盤價被複製幾次？**
一筆台股日收盤現在至少存在 5 個地方：原始檔（raw）、整理後檔（normalized）、最新快照（每天整行重寫，連 60 天一起）、收據（ingest receipt，一整批約 15 MB 的快照內容）、證據／蒸餾資料。其中最大一份在收據裡（約 30 KB／筆，是收盤價本身的 20 倍）。快照每天整行重寫，所以同一筆收盤價在它的生命週期裡被重寫約 60 次。每次重跑再多一份 raw 和 normalized。

**有一點要更正：** 協調者說檔案是用「跑資料的日期」分區。程式碼看起來是用交易日（`trade_date`／`event_time`）分區，只有缺日期時才退回用今天。真正的問題是：同一天有幾十個「每次執行一個檔」的檔案，沒有按股票的索引，重跑會再多一組。這需要先量（M1）確認。

**要改成什麼：**
- 每個交易日每檔股票只寫一次，放在「按交易日分區」的日線庫；要看過去任何一段日期，直接讀那幾天，不用掃全部。
- 快照只留最新一天的收盤價加一個指標，不再放 60 天。
- 收據只放數量和參考，不放資料本身。
- 需要看 60 天的程式（paper 訊號、準入檢查、投組、部署前檢查等）都透過同一個有上限的讀取函式。
- 每個儲存空間都有每日大小與總量上限，超過會警報。

**哪些會刪掉（不是封存）：**
- 下市股票的日線**保留**（回測和存活者偏差檢查需要）。
- 下市或過期代號在快照裡的那一行、被取代的快照版本、收據裡的快照內容、超過保存天數的分區：**全部刪除**。
- 過期權證：目前三個交易 persona 都沒用到，建議先不抓、不留；這是要你決定的事（省下約一半的歷史筆數，但如果日後有策略要用權證就得重抓）。

**上線順序：** 先修收據（停止每分鐘 9 MB 的成長）→ 再開日線庫雙寫 → 補歷史 → 讀取切換並對照 5 個交易日 → 最後才刪舊檔。collector 的價格歷史來源要等收據修好才重新啟用。
