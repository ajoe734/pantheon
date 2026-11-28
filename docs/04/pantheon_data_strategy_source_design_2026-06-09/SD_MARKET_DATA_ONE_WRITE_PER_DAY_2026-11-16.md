# SD: Market Data Is Written Once Per Trading Day And Read As Windows

Task: SD-MARKET-DATA-ONE-WRITE-PER-DAY-20261116 (design only, no implementation code)
Date: 2026-11-16. Amended 2026-11-17 by SD-MARKET-DATA-ONE-WRITE-AMENDMENT-20261117 (operator decision F, section 0 item 10; every changed place is marked [AMENDED 2026-11-17] or [CORRECTED 2026-11-17]).
Parent design: `DATA_STRATEGY_SOURCE_SYSTEM_DESIGN.md` sections 4.4, 4.5, 14
Operator direction (2026-11-16, verbatim): 過期就刪掉，封存幹嘛? 另外你這是快照，如果需要往前看資料呢?

## 0. Decisions in one screen

Operator decisions of 2026-11-16 (A to E, binding; B corrected to option (b)) amend the reviewed head `9db7ccafe`. Principle applied everywhere: **data is not appended after an older copy of itself; the newer version replaces the older one and the same fact is stored once.**

1. **One store of record for history: `daily_bars`**, one row per (market, symbol, trade_date), partitioned by trade date, written once (section 4.1).
2. **The latest snapshot KEEPS the 60 daily closes per symbol (decision A; reverses the earlier 0.2).** What changes is the storage: exactly one current record per (symbol, dataset) in a SQLite WAL table, replaced in place by the newer version, no superseded versions kept, an unchanged window writes nothing, and the repeated lineage is stored once per record instead of once per close (section 4.2).
3. **Window readers** read the 60-close current record by key for the newest window (one key read) and go through the bounded date-partition reader (`read_close_window` / `read_range`) for anything beyond 60 closes or any date range (section 5).
4. **Receipts carry references and counts only.**
5. **Expiry is deletion, not archival** for data that is superseded or past retention. Daily bars of delisted ordinary shares are kept (survivorship). **Warrants are fetched while listed and deleted at expiry (decision B, corrected 2026-11-16: operator option (b) 抓，到期就刪, answered 14:43:25Z, later than the 14:29Z 不抓不留 that the earlier text followed)**; nothing of an expired warrant is archived; they are identified and their expiry is detected from the exchange listing, not from a symbol pattern (section 4.3).
6. **Frequencies other than daily (decision C):** this design follows `SD_INTRADAY_MINUTE_BARS_2026-11-11.md` in full; 1-minute bars are stored once, 5m to 60m are derived on read and never stored or snapshotted; measured latency and a bounded derived cache rule are in section 5.3.
7. **Every other persisted dataset gets the same rule (decision D)**, one row each in section 6A.
8. **Every store has a per-trading-day size, a total bound and an alarm** (section 6).
9. **No new heavy dependency.** `pyarrow` is already pinned (`pyarrow>=15,<30`) for the Parquet part; SQLite is the Python standard library for the current-record part.
10. **Operator decision F, 2026-11-17 (the operator answered 修, verbatim, to two amendments proposed by the market data owner).** [AMENDED 2026-11-17] (a) **Daily bars carry traded value, available time and trading status** on every row (section 4.1a), so that a reader switched to daily bars keeps the volume weighted price, the point in time timestamp and the survivorship attestation (gaps G1 and G2 of `SOURCE-MARKET-STORE-BACKFILL-AND-CLEANUP-20261118-R2`). (b) **Every budget row uses the measured listing size**: about 53,000 listed symbols on 2026-11-17, not 22,500 (section 4.3 and section 6). [CORRECTED 2026-11-17] The earlier text counted 7,053 TWSE warrants; the official page held 37,740 on 2026-11-17, so every budget row built on 22,500 symbols was about 2.4 times too low. Decisions A to E are unchanged; warrants are still fetched while listed and deleted at expiry. This amendment changes documents only; the code that must follow is listed in section 9A.

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

Note on row D (read from `requirement_state.py` on this branch): each point repeats `source_id`, `connector_id`, `market`, `event_time`, `content_ref`, `ingest_run_id`; the line also stores the derived `closes` and `lineage` lists and `snapshot_id`; `calendar_evidence` is one object per line. The hosted 71 KB per line is the coordinator's measurement; M3 splits it into those parts. Row D2: the `tw_price_1m` snapshot lines written by `intraday_snapshot_projection` (section 5.3).

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
| Latest market snapshot journal | **Departs from 4.5 in the storage mechanism only.** 4.5 says normalized data is stored by source, dataset, date. Operator decision A keeps the 60-close window in the snapshot, so the window stays as a derived current record, one per (symbol, dataset), replaced in place; the append-then-compact journal is what departs (append after an older copy). | Replace the journal with one replaced record per key (section 4.2); lineage stored once per record. |
| Ingest receipts carrying snapshot bodies | **Departs.** 4.5 puts data in raw/normalized with `raw_ref`; tw_broker_top already uses `raw_ref` and `ingest_run_id` references. A receipt body is a third copy. | Receipts carry refs and counts. |
| Per point lineage and calendar evidence (about 1.2 KB per close) | **Departs from "compact normalized table" (4.5) and invariant 7 is satisfied but wastefully.** Invariant 7 asks for evidence lineage, not a copy per point. | Lineage is stored once per (run, partition) and per (trade_date, calendar) and referenced by id from the bar row. |
| Collector two year backfill of every listed and delisted symbol | **Follows it for delisted shares; for warrants it follows 4.4 by deleting at expiry (no archive tier for warrants).** 4.4: no full-market full-depth for every dataset every day; archive_universe keeps "daily price and material events only". A one-off history backfill is allowed; rewriting it every day is not. | Delisted shares keep daily history (survivorship, see SCREEN-SURVIVORSHIP-ATTESTATION-20261115, TW-SUSPENSION-HOLIDAYS-INTAKE-20261115). Listed warrants are fetched and kept only while listed; at expiry everything of the warrant is deleted (decision B, section 4.3). History of already expired warrants is not backfilled. |
| Invariant 6 ("Hard delete is exceptional; retire is the default") | **Does not apply to market data rows.** It sits in the product-object list (invariants 1 to 5 are about data source and seed source objects, section 4.6 lifecycle). Retire/archive applies to a data source configuration, not to bars. | Operator direction; see section 4. |

## 4. Target and expiry

### 4.1 Store of record

```
market_data_store/daily_bars/market=TW/date=YYYY-MM-DD/part-<n>.parquet   # one logical write per (date, symbol)
market_data_store/daily_bars/_manifest/date=YYYY-MM-DD.json              # file list, row_count, symbol_count, content digest
market_data_store/lineage/date=YYYY-MM-DD.json                           # calendar evidence once per day; run and source refs once per run
```

Row: `market, symbol, trade_date, open, high, low, close, volume, adjusted flag, source_id, ingest_run_id, lineage_ref, content_hash` plus, since decision F, `traded_value, available_time, trading_status` (section 4.1a) (about 75 bytes columnar compressed with the three new columns, about 60 without them; design estimate). Key (market, symbol, trade_date) is unique. A re-run of the same day replaces that day's partition by atomic rename of a new part set plus manifest swap (write temp, fsync, rename manifest last), so a reader sees the old or the new complete set, never a mix. A re-run whose content digest is unchanged writes nothing.

`trade_date` is required and comes from the record's trading date; a record without one is rejected to a dead-letter count, never filed under the ingest day.

### 4.1a [AMENDED 2026-11-17] Three new columns: traded value, available time, trading status (decision F)

Why: a reader switched to daily bars (`PANTHEON_DAILY_PRICE_READER=daily_bars`) computes the volume weighted price from the close, derives the timestamp as trade date 14:30 +08:00, and loses the survivorship attestation, because the row has no traded value, no available time and no trading status (gaps G1 and G2 in the evidence of `SOURCE-MARKET-STORE-BACKFILL-AND-CLEANUP-20261118-R2`). The columns below make the daily bars read return what the run file read returns today for the same key.

| Column | Type | Nullable | Exact source in the stored record (the run file row of today) | Missing value |
|---|---|---|---|---|
| `traded_value` | float64, TWD | yes | `metadata.normalized_row.value`; if that is absent, `metadata.normalized_row.value_thousands` x 1000 (`services/source_ingestion/candidate_pool_market_feed.py` lines 96 to 99, the feed's rule). The evaluator reads the same number from `metadata.body.value` of the same line (`services/research/alpha_templates/evaluator.py` line 506). | null. **Never zero**: zero is a real value (a suspended day), null means the source gave none; the reader then computes the volume weighted price from the close exactly as `evaluator.py` line 701 does when value is None |
| `available_time` | timestamp, UTC, microsecond | yes | `metadata.body.available_time`, else `metadata.available_time` of the run file line (`evaluator.py` lines 497 to 504; the feed reads `normalized_row.available_time` else `metadata.available_time`, `candidate_pool_market_feed.py` line 83) | null only when neither field is present. The old reads then raise (`evaluator.py` line 497 raises `KeyError` that becomes `TaiwanEvaluationDataError`) or omit the bar (`candidate_pool_market_feed.py` line 85); the daily bars reader does the same and **never invents 14:30 for a null** |
| `trading_status` | string, dictionary encoded, values `traded` or `not_traded` | yes | `metadata.normalized_row.trading_status` (`candidate_pool_market_feed.py` lines 146 to 148) | null when the capture predates the field (the feed treats that as "unknown", lines 134 to 136); **never defaulted to `traded`** |

*Available time rule (it must return exactly what the run file read returns today for the same key).*

1. **A row whose available time is date only** (10 characters or fewer, for example the STOCK_DAY_ALL capture): stored as `<trade_date>T14:30:00+08:00` converted to UTC, the pin the evaluator applies at `evaluator.py` lines 498 to 501 and the daily bars feed applies at `candidate_pool_market_feed.py` line 205. The pin is applied once at write time, so the reader returns the stored instant unchanged.
2. **A row with a real available time** (a full timestamp): stored as given, converted to UTC (`evaluator.py` `_parse_timestamp_utc`); no 14:30 rule applies.
3. **A key with several copies** (several run files hold the same symbol and date, evidence of the backfill: 50,814 duplicate copies in 150 dates): the evaluator's rule is the rule of the store, because it is the one that fails closed. Copies with different open, high, low or close are a conflict (the old read raises `TaiwanEvaluationDataError`; the store rejects the key to the dead-letter count and does not choose). Copies with the same OHLC and different volume or value: the copy with the largest volume supplies `volume` and `traded_value`; `available_time` is the latest available time of all copies (`evaluator.py` lines 515 to 521). `volume` and `traded_value` always come from the same copy, never mixed.
4. **Residual finding R-F1 (not fixed here, outside scope):** the candidate pool feed resolves copies differently (the bar with the latest available time wins whole, `candidate_pool_market_feed.py` lines 157 to 158), so on a key whose latest copy has the smaller volume the two run file reads already disagree with each other today. The implementation task reports the number of such keys on the backfill copy and keeps the evaluator rule.

*Trading status and keys without a close.* The official all-market page lists securities that did not trade; the run file read keeps them only as capture evidence (`candidate_pool_market_feed.py` lines 133 to 148) and the evaluator drops an all-null quote (line 495). The survivorship attestation needs them (gap G2). So a security that the page lists as `not_traded` is stored as a row with `open, high, low, close, volume, traded_value` all null and `trading_status = not_traded`; today's writer rejects it as `bad_close` (`services/source_ingestion/daily_bars_store.py` lines 244 to 246), so that rejection gets exactly one exception for that status. The price readers (`PRICE_COLUMNS` users) skip a row whose close is null, which is what the old read does with an all-null quote; the survivorship attestation reads the status. The row count per day is still bounded by the listing (section 6).

*Lineage (what the survivorship attestation needs besides the status).* The per run **source id** (`metadata.collector.source_id`, the collector bundle that relayed the page), the **upstream endpoint** (`normalized_row.upstream_endpoint`, `MI_INDEX` for TWSE and `dailyQuotes` for TPEx, `candidate_pool_market_feed.py` lines 120 to 125), the venue and `collector.bundle_sha256` (first 12 characters) are stored **once per run in `lineage/date=YYYY-MM-DD.json`**, not once per row. The bar row keeps only `source_id` and `lineage_ref` (the run id) as today. The attestation rebuilds the capture reference `bundle://<source_id>/<dataset>/<session>@<endpoint>#<sha12>` from the lineage entry of the row's `lineage_ref`.

*Manifest and schema version effect.* `DAILY_BARS_SCHEMA_VERSION` goes from `daily_bars_manifest.v1` to `daily_bars_manifest.v2`; the v2 manifest also lists `columns`. `content_hash` is computed over the nine value fields plus the three new columns, so that an old partition (v1 hash over open, high, low, close, volume, adjusted) differs from the same day re-read with the new columns, and the store's "unchanged digest writes nothing" rule does not hide the upgrade. The day digest of an upgraded day therefore changes once.

*What the previous release reader does with a partition that carries the new columns.* `daily_bars_reader.py` reads by column name (`columns or _COLUMNS`, `_COLUMNS` has the ten old names, line 22) with `read_row_groups`, and it does not compare `schema_version`; extra Parquet columns are not selected and are ignored. The previous reader therefore returns the old fields of a v2 partition unchanged. What it must not do is write: the previous writer rebuilds merged rows from `_RAW_ROW_FIELDS` only (`daily_bars_store.py` line 367), so a previous release writer that re-runs a v2 day rewrites it without the three columns (they become absent, that is, null for the new readers). Rollback therefore keeps the new readers working (null columns fall back as in the table above) and loses no price. The implementation task proves both directions with the hosted version read from `/bff/version` (previous reader on a v2 partition; new reader on a v1 partition).

*How a partition written before the change is upgraded.* A v1 partition stays readable and is read with the three columns as null (the new reader then behaves like the run file read for a key with no value: volume weighted price from the close; for available time and status a null makes the caller keep the old run file read for that date). It is rewritten to v2 by manifest swap in one of two ways: (a) when that trade date is next written (a re-run or a late correction, changed `content_hash` as above), or (b) by the backfill script re-reading the run files, which exist until the cleanup task runs (section 5.2a keeps that cleanup blocked until the readers use daily bars). Dates whose run files are already deleted cannot be upgraded and stay v1 with null columns; the implementation task reports how many dates that is on the hosted copy.


### 4.2 The latest snapshot keeps 60 closes, stored as one replaced record (decision A)

Requirement (operator): the latest market snapshot still holds the last 60 daily closes per symbol. Exactly one current record per (symbol, dataset); a newer version replaces it; superseded versions are not kept; an unchanged window writes nothing.

**Why the current journal cannot satisfy this.** `LatestMarketSnapshotStore` (`requirement_state.py`) appends a whole new line to `latest_market_snapshots.jsonl` and leaves the older line in place until `_compact_journal_in_place` rewrites the file under an exclusive `flock`. Between appends there are always N superseded copies; compaction is a whole-file rewrite (hosted 171 MB live) and it blocks every reader and writer for its whole duration (on 2026-11-16 it held the lock long enough to fail one controller tick). That is append-after-older-copy by construction, so the mechanism changes, not only the line size.

**Mechanism: a SQLite table in WAL mode (Python standard library, no new dependency).**

```
market_snapshot_current(symbol, dataset, last_trade_date, window_digest, body BLOB, observed_at,
                        PRIMARY KEY (symbol, dataset)) WITHOUT ROWID
snapshot_dict(kind, id, value)      # source_id / connector_id / market / ingest_run_id / calendar_evidence digest, stored once
pragma journal_mode=WAL; synchronous=FULL; auto_vacuum=INCREMENTAL; busy_timeout=2000
```

- **Replace, not append.** One `INSERT ... ON CONFLICT(symbol,dataset) DO UPDATE ... WHERE window_digest <> excluded.window_digest`. Pages of the old version are reused; no superseded copy exists after commit. `window_digest` is the sha256 of the canonical window; an equal digest changes no row (an unchanged window writes nothing; the existing "prior.points == candidate.points" short-circuit in `append_normalized_records` becomes this predicate).
- **Crash safety.** One transaction per ingest batch (about 500 symbols). SQLite WAL with `synchronous=FULL` gives all-or-nothing commit: a kill before commit leaves the previous complete state, a power loss is recovered from the WAL on open. There is no multi-step in-place rewrite, so there is no intermediate state to reason about (the four-step in-place compaction is deleted). Withdrawals (`withdraw_sessions`) become a transaction on the same row.
- **Space.** `auto_vacuum=INCREMENTAL`; after commit the writer runs `incremental_vacuum(200)` in bounded steps, so the file stays within about 1.1x of live bytes. No whole-file compaction exists any more.
- **No lock held across a long operation.** Readers in WAL mode never wait for the writer (they read the last committed snapshot). The only blocking is writer versus writer, capped by `busy_timeout` 2 s then a retryable error (source-ingest has one writer process behind `process_lock.py`).

**Measured on this worker host (not hosted), 2026-11-16, synthetic records of 4.0 KB, 10,700 keys.** Load 10,700 keys in 21 batches of 500: 3.0 s, file 44 MB. Then replace all 10,700 keys with new versions (the daily full replacement) while a second thread reads random keys: write transaction of 500 keys max 194 ms (median 161 ms, includes building the blobs in Python), concurrent reader p99 1.8 ms and max 12.8 ms over 16,293 reads, file 44 MB to 47 MB (no growth from the replacement), `wal_checkpoint(TRUNCATE)` 5 ms; a batch whose digests are unchanged wrote no rows (123 ms to evaluate 500). The hosted-shaped acceptance test in T3 must repeat this with real-size records on the hosted host.

**Longest block (the bound T3 must prove).** Reader: 0 ms wait on a writer (WAL), single key read p99 <= 10 ms. Writer: transaction of one 500-record batch <= 250 ms p99 (measured max 194 ms above); a second writer waits at most 2 s and then gets a retryable error (no tick failure; the controller retries next tick). Maintenance step (incremental vacuum): <= 50 ms per step. Compare today: the exclusive lock covers the whole 171 MB rewrite (seconds to tens of seconds). T3 asserts these bounds with a test that runs a reader loop during a full 10,700-key replacement and fails if any reader call exceeds 100 ms.

**Size of one 60-close record.** Today about 71 KB (coordinator measurement; hosted line, not visible from this branch). Reading the code, the repetition per close is: each of the 60 `points` repeats `source_id`, `connector_id`, `market`, a 25-character `event_time`, a `content_ref` and an `ingest_run_id`; the line then repeats the same information again in the derived `closes` array and in the derived `lineage` lists (`content_refs`, `ingest_run_ids` hold all 60 values a second time); `snapshot_id` is also derived. `calendar_evidence` is one object per line in the code, not per close, so the "calendar evidence repeats per close" claim must be confirmed by measurement M3 before it is relied on. The 60 closes are kept; the repetition is removed like this:

| Removed repetition | How | Reader effect |
|---|---|---|
| `closes` array and `lineage` aggregate lists | not stored; they are properties computed from the points (they already are properties in `LatestMarketSnapshot`) | none |
| `source_id`, `connector_id`, `market` repeated in 60 points | stored once per record in the header (or as a dictionary id when a record mixes sources) | none |
| `ingest_run_id` per point | dictionary id per distinct run in the record | none |
| 25 character `event_time` per point | `trade_date` (10 chars) plus the fixed session time rule; the public form is rebuilt on read | none |
| `calendar_evidence` | stored once per distinct digest in `snapshot_dict`, referenced by id (same evidence object for all symbols of one day) | none |
| `content_ref` per point | phase 1: kept per close; phase 2 (after T1): replaced by the pointer (symbol, trade_date) into `daily_bars`, whose row carries the `content_hash` / `lineage_ref` | none |

`GET /api/source-ingest/snapshots/latest` returns the same JSON (`closes`, `lineage`, `points`, `snapshot_id`, `calendar_evidence`), rebuilt from the compact record; `snapshot_id` is computed on the rebuilt canonical form and must equal the journal's value for the same data (a T3 test compares both on 10,700 keys). Design estimate of the record: about 4 KB with a 32 hex `content_ref` per close (my synthetic record above); with the real `content_ref` length and lineage dictionary the target is **<= 12 KB per record in phase 1 and <= 4 KB in phase 2**, versus 71 KB today. T3 reports the measured value on a copy of the hosted journal.

**Total live size at hosted volume.** [CORRECTED 2026-11-17: the 2026-11-16 text used about 22,500 keys, 20,200 warrants and a 270 MB ceiling; the official ISIN pages listed 52,958 symbols on 2026-11-17 (section 4.3), so the ceilings below are recomputed.] Keys after the sweeps: listed warrants are kept while listed, so the live key count is bounded by the listing, not by history: 2,075 listed shares and ETFs (1,058 + 898 + 119) plus 50,883 listed warrants (37,740 + 13,143) plus delisted shares for at most 10 trading days = about 53,000 keys hard ceiling (formula: 52,958 listed + a few delisted); today about 10,700 keys are live, of which about 7,550 are warrant-like and about 6,750 have had no close for more than 30 days (hosted read-only count 2026-11-16; the stale ones are deleted by the first sweeper run, leaving about 4,000 live keys). **Size with every listed key:** phase 1 53,000 x 12 KB = 636 MB hard ceiling, phase 2 53,000 x 4 KB = 212 MB; this is a ceiling, not a forecast (expected 4,000 to 12,000 keys x about 8 KB = 32 to 96 MB, a warrant has fewer than 60 closes). **Size with the active universe only** (`default_universe_size` 200, `services/source_ingestion/active_universe.py` line 202): phase 1 200 x 12 KB = 2.4 MB, phase 2 200 x 4 KB = 0.8 MB; the listed shares and ETFs alone: phase 1 2,075 x 12 KB = 25 MB, phase 2 2,075 x 4 KB = 8.3 MB. The 60 close values are stored twice (current record and `daily_bars`), bounded at 60 x 16 bytes per key; the lineage is stored once. The snapshot record does not carry the three new columns of section 4.1a (they are bar fields, read from `daily_bars`), so its size is unchanged by decision F.

**How the previous hosted reader (e7ec353d6) keeps working during rollout and rollback.** The previous reader is the older service image reading `latest_market_snapshots.jsonl` in its own format. Rule: until the rollback window closes, a **compat export** keeps that file valid, without ever appending after an older copy:

1. Phase S0 (shadow): the writer commits to SQLite first, then keeps appending to the journal exactly as today. Both stores are current; the reader still reads the journal. Nothing changes for the old image.
2. Phase S1 (switch, flag `MARKET_SNAPSHOT_STORE=journal|dual|sqlite`, default `journal`): `dual` reads SQLite and compares with the journal on every read of the controller path, logging mismatches (target 0 over 5 trading days); `sqlite` reads SQLite only. Rolling back is setting the flag back (same image).
3. Phase S2 (journal stops being appended): once S1 shows 0 mismatches, the writer stops appending. A **compat exporter** streams the SQLite table in one read transaction to `latest_market_snapshots.jsonl.tmp` (one fat-format line per key, one version per key), fsyncs, and renames over the old path (atomic; the old reader detects the new inode through its signature check and reloads). It runs after each completed daily ingest and at most once per hour, never under a lock that blocks the writer. The old file is kept as `.prev` for one run.
4. Rollback to the old image at any point: the exported journal is a complete, valid one-line-per-key journal in the old format, at most one export interval stale (daily data changes once per trading day). Phase S2 ends after 14 trading days without rollback; the exporter and the journal file are then deleted (T3b).

**Sweeper.** Keys whose symbol is delisted (listing source) and has no close for 10 trading days, or which are warrants no longer listed (expired), are deleted from `market_snapshot_current` in one transaction at the first sweeper run after expiry; the bars of delisted shares remain in `daily_bars`, the bars of expired warrants are deleted there too (section 4.3).

### 4.3 Expiry rules (deletion, not archive) and warrants (decision B)

| Data | Rule | Enforced by |
|---|---|---|
| Daily bars of **delisted ordinary shares** | **Kept**, frozen, never rewritten (backtest and survivorship proof need them) | partition immutability; no delete |
| Daily bars of live symbols (shares, ETFs, listed warrants) | keep `retention_days` (2555 for prices) or until expiry for a warrant, whichever is first; then delete | retention sweeper (T5) |
| Current-record rows of delisted shares and of **expired warrants** | **deleted** (bars of delisted shares stay in `daily_bars`; those of expired warrants are deleted) | T3 sweeper |
| **Expired warrant**: daily bars, snapshot record, lineage | **deleted at the first sweeper run after expiry** (the listing no longer lists it); nothing is archived | T5 sweeper (warrant expiry step, uses the T8 listing) |
| Superseded snapshot versions | do not exist (replace in place); the old journal's superseded lines disappear with the journal | T3 |
| Snapshot bodies inside receipts | deleted by receipt migration; future receipts never carry them | T2 |
| Raw and normalized run files (A, B, C) after `daily_bars` verified | deleted after a verification window (default 30 days) | T4 |
| Partitions past `retention_days`; lineage of deleted partitions | deleted; lineage of kept bars kept | T5 |
| A bar referenced by a gate proof / paper session evidence | its partition is pinned in the manifest and not past retention by definition | T5 test |

**Warrants: fetched while listed, deleted at expiry (option (b) decided; this replaces the earlier option (a) text).**

Rules: (1) a listed warrant is fetched and stored like any other listed security while it is listed. (2) When the listing source no longer lists it, its daily bars, snapshot record and lineage are deleted at the first sweeper run after expiry; nothing of an expired warrant goes to an archive. (3) History of warrants that had already expired is not backfilled. (4) Daily bars of delisted ordinary shares stay kept (unchanged). (5) The authoritative identification below is used both to decide what is a warrant and to detect expiry, and the fail-closed rule applies: if the listing cannot be read, nothing is deleted as expired and price collection stops after 3 days.

*How a warrant is identified (authoritative source, not a symbol pattern).* The exchange security listing. I read the TWSE ISIN listing pages on 2026-11-16 (`https://isin.twse.com.tw/isin/C_public.jsp?strMode=2` for listed, `strMode=4` for TPEx). Each page groups rows under a category header and carries a CFI code column. **[CORRECTED 2026-11-17]** The 2026-11-16 count of the TWSE warrant category (7,053) was wrong: the official page held 37,740 TWSE warrants on 2026-11-17 (the 2026-11-16 reading under-counted by about five times). Counts below are the measured ones of 2026-11-17 from the official ISIN pages as stated by the market data owner in the amendment request; the 2026-11-17 implementation task `SOURCE-HOSTED-LISTING-STATE-AND-PIN-SOURCE-20261118` measured 57,531 listing rows (all categories, 3.2 MB state file). **Not re-counted by this task:** on 2026-11-17 at 12:25 UTC both pages answered HTTP 200 with an empty body (two attempts each, one with a browser user agent), so the numbers are the stated ones and not my own; the ETN, special share and beneficiary certificate rows are the 2026-11-16 reading.

| Page | Category header | Rows | CFI code |
|---|---|---|---|
| TWSE (listed) | 股票 | 1,058 | ESVUFR (1,057), ESVTFR (1) |
| TWSE (listed) | 上市認購(售)權證 | **37,740** (corrected from 7,053) | RWSCCA, RWSCCE, RWSCPE |
| TPEx | 上櫃認購(售)權證 | 13,143 | RWSCCA, RWSCCE, RWSCPE |
| TPEx | ETF / ETN / 股票 / 特別股 / 受益證券 | 119 / 6 / 898 / 1 / 4 | CE..., CM..., ESVUFR, EP..., DA... |

So warrants are the rows under the warrant category headers with CFI code starting `RW`; the listings of 2026-11-17 hold 50,883 warrants (37,740 + 13,143) against 2,075 shares and ETFs (1,058 + 898 + 119), that is **52,958 listed symbols, about 53,000** (the earlier text said 20,196 warrants and about 2,100 shares, ETFs and ETNs, about 22,500 symbols; **corrected**). A `listing_allowlist` (symbol, venue, category, cfi, first_seen, last_seen) is built from these two pages once per trading day, and a symbol is a warrant if and only if the listing says so. A warrant whose `last_seen` is older than the last successful listing day is expired. The symbol shape (the collector's `WARRANT_SYMBOL_RE = ^0[3-8]\d{3}[0-9A-Za-z]$` in `deploy/collector/collector_stack/spiders.py`, used by the suspension spider) is **not** an identification rule and is retired in favour of the listing lookup. If the listing cannot be fetched or parsed (header or column change), the allowlist is not replaced, the previous one is used for at most 3 trading days, and after that collection of price rows fails closed with `listing_unavailable` (nothing is guessed). **Fail closed also for deletion:** while the listing is unreadable, no symbol is treated as expired and nothing is deleted on its account.

*Where collection is filtered.* Listed warrants are collected; what is skipped is an expired warrant (not listed any more) and a symbol in no listing.

1. **Collector fetch policy** (`deploy/collector/fetch-policy.json` and spiders): `TwsePriceHistorySpider` and `TpexPriceHistorySpider` already accept `symbols_filter` (the `allowed_symbols` set); today they admit any `[0-9A-Za-z]{4,6}` code, which includes six-character warrant codes. The runner passes the current listing allowlist (every listed symbol, warrants included) as `symbols_filter`, so a six-character code that is not listed (an expired warrant) is skipped, and the fetch policy records `warrants: listed_only` and the allowlist date. The T86 spider and the suspension spider use the same filter. The two paused price-history sources are re-enabled only with the receipt fix (T2) and this expired-warrant skip active; excluding listed warrants is no longer required.
2. **Hosted connectors**: `taiwan_official.py` / `provider_adapters.py` (`tw_price_daily` from the per-date all-stock pages), the FinMind adapters (`finmind_taiwan.py`, only active-universe symbols) and `active_universe.py` apply the same allowlist at record creation; a record whose symbol is not in the current listing (expired warrant or unknown code) is not stored and is counted in the run summary as `skipped_not_listed_count` (so a reviewer sees the skip, nothing disappears silently).
3. **Pipeline guard**: `MarketDataStorageWriter.write_run` rejects a price record whose symbol is not in the current listing (defence in depth, same counter).

*One-off deletion of already expired warrants that exist today.* Warrants that are listed today are kept. The delete set is computed once and reported before deleting: every warrant-like symbol that the listing does not list today (hosted read-only count 2026-11-16: about 7,550 warrant-like keys are current in the snapshot store, about 6,750 of them with no close for more than 30 days), plus every symbol that is in no stock/ETF listing, was never in the `ordinary_share_registry` (all symbols ever listed as share, built from the daily listing snapshots; for the history before the first snapshot it is seeded from the symbols that the stored all-market official captures, the same evidence `survivorship_attestation.py` uses, show with a share-category row; `survivorship_attestation.py` itself holds no delisted list), and is not a pinned symbol. A symbol shaped like an ordinary share (four digits) is never in the automatic delete set, it goes to a review list. Stores cleaned: `market_snapshot_current` (and the old journal until it is removed), `market_data_store` raw and normalized partitions (T4 cleanup rewrites partitions without those rows; not backfilled into `daily_bars`), `daily_bars`, features, source_evidence and distillation rows, receipt journal (already bodies-free after T2), BFF/Agora projections (they read through the endpoint, so they follow), and the collector bundle store on the collector host (raw warrant rows older than `raw_retention_days` expire by the existing 90-day rule; T8 deletes the remainder). Daily bars of delisted ordinary shares stay.

*Budget effect of keeping listed warrants.* [CORRECTED 2026-11-17] About 50,900 listed warrants add at most 50,883 x 75 B = 3.8 MB of bars per trading day (only warrants that traded have a bar with prices; a `not_traded` row is also stored, section 4.1a, so the bound counts every listed key) and, because each warrant's bars are deleted at expiry, the stock of warrant bars never exceeds listed warrants x days listed (worst case 50,883 x 500 days x 75 B = 1.9 GB; typical warrant life is under a year). The per-day and total bounds are in section 6.

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

The newest 60-close window is one key read of `market_snapshot_current`; anything beyond 60 closes or any date range goes through the date-partition reader. Latency figures for the partition reader are design estimates ([CORRECTED 2026-11-17] 53,000 listed symbols; 60 partitions of about 4 MB each, 53,000 x 75 B) to be measured by the T1 hosted-shaped benchmark; the current-record figures are bounded by the measurement in section 4.2.

| Reader | Today | Target | p95 target |
|---|---|---|---|
| `services/execution/lean_runtime/paper_signal_producer.py` (`recent_closes`, pool market inputs) | closes from binding metadata / `/snapshots/latest` | endpoint answers the 60-close window from the current record for the bound symbols (pool, <= 50 symbols): 50 key reads | <= 100 ms per call, one call per pool tick |
| `services/execution/market_snapshot_admission.py` | newest close, event time, lineage validity | same current record (no window parse beyond the last point) | <= 10 ms |
| `services/registry/portfolio_envelope.py` (`closes` argument) | window supplied by caller from snapshot | caller gets the window from the endpoint; module stays pure | caller <= 100 ms |
| `services/execution/lean_runtime/performance_telemetry.py` (`MarketMark`) | newest close | current record | <= 10 ms per symbol |
| `scripts/deploy_nonprod_vm.sh` bounded refresh preflight (`closes` of one symbol) | snapshot `closes` | same endpoint | <= 100 ms |
| `scripts/run_isolated_l12_runtime_e2e.py` (2330.TW `closes`) | snapshot `closes` | same endpoint | <= 100 ms |
| Gate and pool datasets, backtests (date range, more than 60 closes) | scan journal / partitions | `read_range` over `daily_bars` with caps | 2 years x 1 symbol <= 500 ms; 500 symbols x 250 days <= 3 s |
| BFF and Agora projections | pass through source-ingest endpoints | unchanged; the endpoint is the single reader | unchanged |

Hosted-shaped acceptance (not a three symbol fixture): [CORRECTED 2026-11-17] 53,000 symbols x 500 trading days x row-groups of 4,000 rows, generated, with 6,500 dead symbols; and 10,700 current records at real size.

### 5.2a [AMENDED 2026-11-17] What each reader reads after decision F

Reader switching is `PANTHEON_DAILY_PRICE_READER` (`run_files` default | `daily_bars`), implemented by `SOURCE-DAILY-BARS-READERS-COMPLETE-20261118` (merged) and, for the columns, by `SOURCE-DAILY-BARS-VALUE-TIME-STATUS-COLUMNS-20261118`. After the change, with the setting at `daily_bars`:

| Reader | Reads from daily bars after the change | Replaces |
|---|---|---|
| Volume weighted price in `evaluator.py` `load_tw_price_daily_snapshot` (line 701) | `traded_value / volume` when `traded_value` is not null and `volume > 0`, else `close` (as the old read) | today's fallback to the close (gap G1) |
| Row timestamp (`ts`, `event_time`, `available_time`) in the evaluator and in `candidate_pool_market_feed.py` | the stored `available_time` (UTC) | today's derived trade date 14:30 +08:00 (gap G1); a null column keeps the old read's behaviour (section 4.1a) |
| Survivorship attestation (`survivorship_attestation.py`, fed by `candidate_pool_market_feed.py` lines 133 to 148 and 452) | `trading_status` per (symbol, session) for the `not_traded` set, and the lineage entry of the row's `lineage_ref` for source id and upstream endpoint | today's read of the capture rows from the normalized run files (gap G2); with no run files `includes_delisted` and `includes_suspended` fall to false |
| `traded_value` of the feed bar | the column, null when absent (the screen excludes such a bar as it does today) | omitted field |

**The cleanup of old run files stays blocked** (the cleanup step of `SOURCE-MARKET-STORE-BACKFILL-AND-CLEANUP-20261118`) **until those readers default to daily bars**: the setting's default is `daily_bars`, the three columns exist for every date the readers need, and the attestation of the feed reads status and lineage from the store (gap G2 of the R2 evidence: the cleanup must not run on a window the feed still attests while the capture evidence has no store).

### 5.3 Frequencies other than daily (decision C): follow or depart

I read `SD_INTRADAY_MINUTE_BARS_2026-11-11.md` (operator decision 2026-11-11) first. **Verdict: follow, on all four points; depart on nothing.**

| Point of the 2026-11-11 decision | Verdict | Where it is true in the code today |
|---|---|---|
| 1-minute bars stored once per (symbol, session_date, bar_start) in `tw_price_1m` | **Follows.** | `IntradayStore.put_symbol_day` (`intraday_store.py`): an identical re-fetch is a no-op, differing content for an existing key quarantines the day instead of overwriting; layout `normalized/tw_price_1m/<session_date>/<symbol>.jsonl.gz` plus a day manifest. |
| 5m / 15m / 30m / 60m derived on read, never stored as their own dataset | **Follows.** | `services/research/intraday_bars.py` `aggregate_session` / `load_bars` read reconciled `tw_price_1m` shards only. |
| Daily truth is never derived from minute bars | **Follows.** | `pipeline.persist_latest_market_snapshots` excludes minute records permanently (`is_minute_bar_record`); reconciliation reads `tw_price_daily` and never writes it. |
| Minute data never in `tw_price_daily` | **Follows.** | same exclusion. |

**Inventory: what the snapshot store holds for intraday datasets today.**

| Dataset | Written to the snapshot store today? | Rule and size bound in the target |
|---|---|---|
| `tw_price_1m` | Yes, by `intraday_snapshot_projection.project_reconciled_minute_snapshots`, the only writer (pass days only). A record holds the last `max_closes` = 60 **minute** closes of the latest reconciled session per active-universe symbol. | **Kept, bounded, one current record per (symbol, `tw_price_1m`)** in the same SQLite table, replaced when a newer reconciled session passes; it is a derived pointer of the `IntradayStore` shard (the store of record), not a second copy of history. Bound: <= 4 KB per record, <= 200 symbols (active universe) = <= 1 MB; the projection withdraws a day that is no longer a clean pass (existing `withdraw_sessions`, becomes a delete/replace of the row). |
| `tw_price_5m`, `tw_price_15m`, `tw_price_30m`, `tw_price_60m` | **Never written.** No writer exists: `requirement_state.py` only admits those names in `_FREQUENCY_SNAPSHOT_DATASETS` so a `frequency=` query validates. | **Never written, enforced:** T3 makes the writer refuse these dataset names and the endpoint answer `not_stored_derive_on_read` for them; derived bars come from `load_bars`. Size bound 0 bytes. |
| `tw_price_1m` shards (`IntradayStore`) | n/a (store of record) | Budget from the 2026-11-11 design: about 1.5 GB steady, 3 GB budget at 200 symbols and 2 years; rolling 2-year retention enforced by its manifest-driven pruner; hosted free space still measured by K2. Not changed by this design. |

**Measured read plus aggregation latency of `load_bars`, 2026-11-16, worker host (not hosted), single thread, real `IntradayStore` and `reconcile_day` on generated sessions** (271 bars per session as in the intraday design; synthetic constant prices, so gzip shards are smaller than real ones and the read cost is a floor). 60 sessions per symbol, every session reconciled; the script builds the store with `put_symbol_day` and `reconcile_day`, then calls `load_bars` per symbol:

| Symbols x sessions | Frequency | Total | Per symbol (60 sessions) | Bars returned |
|---|---|---|---|---|
| 50 x 60 | 1m | 28.2 s | 564 ms | 813,000 |
| 50 x 60 | 5m | 18.4 s | 368 ms | 162,000 |
| 50 x 60 | 15m | 14.6 s | 292 ms | 54,000 |
| 50 x 60 | 60m | 13.8 s | 276 ms | 15,000 |
| 200 x 60 | 5m | 96.9 s | 484 ms | 648,000 |
| 200 x 60 | 60m | 70.3 s | 352 ms | 60,000 |

Peak RSS 44 to 51 MB. The cost is linear in symbols and sessions and is dominated by per-file sha256 verification, gzip and JSON parsing and object construction, not by the aggregation (60m is 2x cheaper than 1m only because fewer `Bar` objects are built). Store build for 200 x 60: 291 s, 24,060 files, 43 MB.

**Bound.** `load_bars` is called per symbol with at most 60 sessions: bound **<= 1.0 s per symbol per 60 sessions** (measured 0.28 to 0.56 s) and **<= 60 s for a 200-symbol, 60-session backfill-style read** (measured 70 to 97 s: this misses the bound for 200 symbols). The named consumers: (1) the next-session paper decision (K7) reads a pool of at most 50 symbols once per day in the window between reconciliation and 08:55; 14 to 28 s meets it with a large margin; (2) intraday replay/replication (K5/K6) reads one symbol for up to 60 sessions per run: 0.3 to 0.6 s, meets it; (3) a universe-wide intraday screen over 200 symbols x 60 sessions cannot meet 60 s with on-read aggregation (70 to 97 s). For consumer (3) and for any repeated read of the same window (evolution search re-running candidates), the design specifies a **bounded derived cache, not a second store of record**: key (symbol, session_date, frequency, `shard_sha256` from the day manifest); value the aggregated bars of that one session; invalidation by key (a changed or re-admitted shard has a new `shard_sha256`, so the old entry is never hit; waived or quarantined days are never cached); size cap 512 MB LRU, in a cache directory outside the store, deleted freely, rebuilt from `tw_price_1m` at any time, never read by anything that needs a record of truth; hit-rate and size reported in health. The cache is built only if T10's benchmark on hosted shows a named consumer over its bound; otherwise it is not built.

### 5.4 Decision E: a strategy that needs more than 60 closes works in paper; one whose history is missing is refused (operator decision E, 2026-11-16)

Facts on origin/dev: `strategy_artifact_minimum_closes` (`services/registry/strategy_artifact.py`) derives the closes a strategy needs (close_to_close_momentum lookback; `factor_rule_v1` `required_history_bars`, windows up to `MAX_FACTOR_WINDOW_BARS = 504`). `services/deployment/service.py` (plan build, around line 529) copies it into `market_data_policy.minimum_closes` without comparing it with what the Source can serve. `paper_signal_producer` then raises `market_input_insufficient` ("requires N closes, got 60") at every decision, because the snapshot holds at most 60 (`SOURCE_INGEST_MARKET_SNAPSHOT_MAX_CLOSES` defaults to 60 and is unset on hosted). Backtests read date partitions, so such a strategy passes the gate, deploys, and never trades. Hosted logs of the last 24 hours show no such error: no running strategy needs more than 60 today.

**E1. Serving more than 60 closes (decision A unchanged: the record keeps exactly 60, it is not enlarged; no second store, no per-strategy history copy).**

- *How the caller asks for n.* `GET snapshots/latest` and the pool market-input call take an optional `min_closes=n` (integer, 1 to 504; default and any value <= 60 behave exactly as today and read only the current record). The producer passes `market_data_policy.minimum_closes` of the binding.
- *Where the data comes from.* For n <= 60 the 60-close current record. For n > 60 the service takes the newest 60 from the current record and reads only the part beyond it (closes 61..n) from `daily_bars` through `read_close_window(symbols, end_date = oldest date in the record minus one trading day, n - 60)`; the two parts are joined on trade date, de-duplicated and checked contiguous against the trading calendar (a gap is reported, not filled).
- *Response contract.* The same fields as today (`symbol`, `closes`, `event_time`, `snapshot_id`, lineage). `closes` has length exactly n, or the call returns no closes and a readable insufficiency reason: `reason = history_insufficient`, with `requested`, `available`, `first_available_date`, and `source = daily_bars`. It never returns a shorter list under a success status. `snapshot_id` and lineage still describe the newest point; the extension adds one field `window_source = {current_record: 60, daily_bars: n-60}`.
- *Latency bound.* 50-symbol pool at n = 504: 444 partitions beyond the record, symbol filter pushed into row groups. Bound: **p95 <= 2.0 s per call and one call per pool decision** (design estimate: about 4 ms per partition open and prune with the manifest cached, so about 1.8 s; T1 measures it on hosted-shaped data and the bound is the acceptance). Closed trading days are immutable, so an in-process LRU of already-read (symbol, date) rows, capped at 64 MB and never read as truth, may be added only if the T1 benchmark misses the bound. At n <= 60 the p95 stays <= 100 ms (section 5.2).
- *Before T1 ships.* `daily_bars` does not exist, so the service cannot serve n > 60: a call with n > 60 returns `history_insufficient` with `reason_detail = daily_bars_not_available`, and E2 below refuses such plans at deployment time so the producer is never asked. Nothing is faked from backtest partitions.

**E2. Deployment-time check (refuse instead of failing at every decision).** Where the plan derives `minimum_closes` (`services/deployment/service.py`, the `setdefault("market_data_policy", ...)` block), after the number is derived and for every bound symbol (`metadata.symbol` / `metadata.symbols`), compare it with the governed history length the Source reports for that symbol (a new small read `GET history/availability?symbols=` answered from the `daily_bars` manifest: first date, last date, count). Raise `DeploymentPlanError` with a readable reason, for example `history_insufficient: 2330.TW has 212 closes, strategy needs 504 (factor_rule_v1 window 504)`, when any symbol is short. Until E1 ships the check is simply `minimum_closes > 60 -> refuse: market snapshot serves at most 60 closes`. The check is skipped only when the plan already carries an explicit `market_data_policy` (existing behaviour) and then the producer-side refusal remains the backstop. Files: `services/deployment/service.py`, `services/deployment/test_pool_envelope_plan.py` and `test_inline_paper_execution_projection.py` (extend), `services/registry/strategy_artifact.py` (read only), `services/execution/lean_runtime/paper_signal_producer.py` and `pool_market_inputs.py` (pass `min_closes`), `services/source_ingestion/routers/ingest_operations.py` (the `min_closes` parameter and `history/availability`), `daily_bars_reader.py` (T1).

**Indicators carried forward (operator's observation), not adopted.** A 120-day average does not strictly need the previous 120 closes each day: it can be carried (previous value plus today's close minus the close leaving the window; an exponential average needs only the previous value). Paper today recomputes every rule from raw closes at each decision with the same stateless evaluator as the backtest. Carrying values is **not adopted, and not designed here**: after every restart, and after a corrected close, the carried value must be rebuilt from the full history anyway (so the history read is still needed); and one evaluator for backtest and paper is the rule that keeps their results identical, which a second incremental path would break. With E1 the cost is bounded (<= 2.0 s per pool decision at n = 504). It is revisited only if a measured decision cost misses its bound.

## 6. Growth budget

Volume: [CORRECTED 2026-11-17] about 53,000 listed symbols (measured on 2026-11-17 from the official ISIN pages: 2,075 shares and ETFs plus 50,883 warrants = 52,958; the 2026-11-16 text said about 22,500 and under-counted TWSE warrants as 7,053 against 37,740; 17,200 historical symbol keys exist today, about 10,700 live of which about 7,550 warrant-like and about 6,750 stale for more than 30 days, M5), two years (about 500 trading days), collector batches of about 500 records (53,000 / 500 = 106 batches per day). One daily bars row is about 75 B (60 B for the nine old columns plus about 15 B for `traded_value`, `available_time`, `trading_status`; design estimate, replaced by the measured value in the implementation task). Estimates are design targets, replaced with measured values in each task.

| Store | Size per trading day | Total bound | Alarm |
|---|---|---|---|
| `daily_bars` (shares, ETFs, listed warrants; delisted shares kept, expired warrants deleted; rows carry `traded_value`, `available_time`, `trading_status`) | <= 4.0 MB (per day: 53,000 x about 75 B = 3.98 MB with margin; shares and ETFs 2,086 x 75 B = 0.16 MB, listed warrants 50,883 x 75 B = 3.8 MB; before the amendment 22,500 x 60 B = 1.35 MB) | shares/ETFs/delisted <= 0.2 GB at 2 years (2,086 x 75 B x 500 = 78 MB, with margin for delisted shares); warrants <= 1.9 GB worst case (50,883 x 75 B x 500 days, deleted at expiry); total <= 2.1 GB, ceiling 2.5 GB | day > 6.0 MB (1.5 x 4.0 MB) or total > 2.5 GB |
| `lineage` | <= 0.4 MB (calendar evidence once per day about 0.3 MB; run refs with source id and upstream endpoint once per run: 106 runs x about 1 KB = 0.1 MB) | <= 200 MB (0.4 MB x 500 days) | 1.5x (0.6 MB per day) |
| `market_snapshot_current` (SQLite, 60 closes per key) | rows replaced only for keys with a newer window: <= 53,000 x 12 KB = 636 MB written per day in the worst case with every listed key (expected 4,000 to 12,000 keys x 8 KB = 32 to 96 MB; with the active universe only 200 x 12 KB = 2.4 MB); no growth of the file from replacement | live <= 640 MB phase 1 (53,000 keys x 12 KB = 636 MB; listed shares and ETFs only 2,075 x 12 KB = 25 MB; active universe only 200 x 12 KB = 2.4 MB), <= 215 MB phase 2 (53,000 x 4 KB = 212 MB; active universe only 0.8 MB); file <= 1.1x live; WAL <= 64 MB | any record > 16 KB, live bytes > ceiling, file > 1.3x live, writer transaction > 500 ms, reader call > 100 ms |
| compat journal export (phase S2 only) | one file rewrite per daily ingest, at most hourly (as built: `MARKET_SNAPSHOT_EXPORT_INTERVAL_SECONDS` default 3600) | <= 1x live in the old format: 171 MB today for 10,700 keys (about 16 KB per key); with every listed key at the old 71 KB line size the worst case is 53,000 x 71 KB = 3.8 GB (warrants have fewer than 60 closes, so far less in practice); see residual finding R-F2 in section 9 | exists 14 trading days after S2 starts |
| `ingest_schedule.jsonl` receipts | 106 batches per day (53,000 / 500) x <= 8 KB = <= 0.9 MB (was 35 batches, 0.3 MB); no line > 16 KB | <= 200 MB for retained receipts (30 days; 0.9 MB x 30 = 27 MB expected) | any line > 16 KB, or growth > 5 MB per hour |
| raw/normalized run files (transition only) | as today until T4 | removed by T4 after the verification window | still present 60 days after T4 |
| `listing_allowlist` + `ordinary_share_registry` (new, section 4.3) | <= 4 MB per day (measured 3.2 MB on 2026-11-17 for the 57,531 listing rows of the state file written by `services/source_ingestion/security_listing.py`; the pages list about 53,000 symbols; before the amendment the estimate was 2 MB for 22,500 rows), replaced by the newest listing; the registry holds shares only and only gains symbols | <= 20 MB (`MAX_STATE_BYTES = 20 * 1024 * 1024` in `security_listing.py`; the measured 3.2 MB is 16% of the cap) | listing older than 3 trading days |
| `tw_price_1m` shards (IntradayStore) | per the 2026-11-11 design (200 symbols x 271 bars) | about 1.5 GB steady, 3 GB budget | per that design |
| source_evidence, distillation | no per-close evidence row; one evidence item per run (about 106 per day: 53,000 / 500; was 35) | <= 50 MB | 2x of the M4 baseline |
| in-memory receipt replay | n/a | RSS bound from M6 (target < 1 GiB) | RSS above bound |
| other datasets | section 6A, one row each | section 6A | section 6A |

Review check for each later implementation PR: the PR evidence must include (1) the budget row for each store it touches with the measured value on hosted-shaped data, (2) a test that fails when the per-day or per-record limit is exceeded (an assertion on bytes, not a log line), (3) the alarm name and where it is emitted (source-ingest health payload field, so Ops can see it), and (4) a statement of which stores the PR does not touch. A reviewer rejects a PR that adds a store without a row. T7 turns the table into `scripts/check_market_data_budget.py`.

## 6A. Every other persisted dataset (decision D)

Same rule as `tw_price_daily`: the key is unique, a newer version replaces the older one (partition replace by manifest swap for date partitions; row replace for current records), an unchanged re-run writes nothing, and nothing is appended after an older copy of the same key. **Evidence on what exists today:** every dataset below is written by the same generic `MarketDataStorageWriter.write_run` (`services/source_ingestion/market_data_storage.py`), which appends one `<ingest_run_id>.jsonl` file per (dataset, date) with no key check; so none of them satisfies the rule at key level today, and this is the same defect as section 1, not a per-dataset one. The measurement M2 is run per dataset (files per partition, duplicate share) before T9 touches it. Keys and sizes below are read from the connector definitions and spiders and are design estimates where marked; no new store is invented: T1's keyed partition writer takes a per-dataset key declaration.

| Dataset | Unique key | Write rule | Retention | Growth budget per trading day (design estimate) | Readers |
|---|---|---|---|---|---|
| `tw_index_daily` | (index_code, trade_date) | partition replace by key | 2555 d (like prices) | <= 0.1 MB (about 40 indices) | regime evaluator (`registry/regime/evaluator.py`, `research/regime/features.py`), BFF/Agora projections |
| `tw_index_ohlc_daily` | (index_code, trade_date) | same | 2555 d | <= 0.1 MB | collector bundle reader, regime features |
| `tw_market_breadth_daily` | (market, trade_date) | same (one row per market per day) | 2555 d | <= 10 KB | regime features, `feature_catalogue.py` |
| `tw_institutional_flow` | (symbol, trade_date, investor_class) | same | 2555 d | <= 0.3 MB columnar (about 1,900 shares x 3 classes; raw JSON about 3 MB today) | `registry/factor_rule.py`, `registry/sector_features.py` |
| `tw_margin_short_balance` | (symbol, trade_date) | same | 2555 d | <= 0.2 MB | feature catalogue, factor rules |
| `tw_broker_top` | (symbol, trade_date, side, rank) | same; whole market from one FinMind SponsorPro whole-day file per session, compact rows without raw price-level rows (SOURCE-BROKER-WHOLE-MARKET-DAILY-20261127) | 750 d | <= 6 MB for the whole market (about 2,600 symbols x 20 buy + 20 sell; measured 2.15 MB gzip at 2,000 symbols) | `data-plane/taiwan_reference.py`, personas service |
| `tw_broker_branch_detail` | (symbol, trade_date, broker_id, branch_id) | same; active-universe core tier only, per-symbol-per-date requests (FinMind) | 250 d (large) | bounded by tier: <= 5 MB until measured; T9 measures first (`finmind_broker_backfill.py`) | broker features |
| `taifex_ssf_contracts` | (contract_id, trade_date) | same | 750 d | <= 0.1 MB | `data-plane/models/contract_master.py`, universe |
| `taifex_tx_daily` | (contract_id, trade_date) | same (TX and MTX, a few rows per day) | 2555 d | <= 10 KB | regime / futures features |
| `tw_macro_series` | (series_code, period) | row replace by key (a revised value replaces the old one, the old value is not kept) | no expiry (tiny) | <= 10 KB | feature catalogue, scheduler |
| `tw_community_daily` | (symbol, trade_date, platform) | derived from `collector_social_posts`; replace by key | 180 d | <= 0.2 MB | feature catalogue |
| collector news / social / paper datasets (`collector_news`, `collector_social_posts`, papers) | content hash of the item (url, published_at, body hash) | write once per hash; a re-fetch with the same hash writes nothing; changed content replaces (edit) | `raw_retention_days` = 90 in `fetch-policy.json` for raw; derived rows 365 d | bounded by `max_requests_per_run` x cadence (for example 20 + 10 + 15 per day); <= 20 MB per day raw until measured | search index (`services/search/index_pipeline.py`), distillation |
| `features/returns` and other `features/<name>` | (symbol, trade_date) | **not stored**: derived on read from `daily_bars` (today a 200 B stub per record, written only when `_feature_targets` is non-empty) | 0 | 0 (remove after T4) | feature loaders |

Datasets that already satisfy the rule: none can be claimed from code (see above); `tw_price_1m` (section 5.3) and the retention window of collector raw (90 days in policy) are the only two with a stated mechanism. Each row's task acceptance (T9) includes its budget row and a hosted-shaped test.

## 7. Migration and rollback

Principles: additive first, the previous reader keeps working, each step crash safe, deletion last and only after verification.

1. **T0 (prerequisite, immediate):** keep the collector price history sources disabled until T2 and the expired-warrant skip of T8 are deployed (operator-approved since 2026-11-16T14:06Z). Re-enable condition (corrected): receipts no longer contain `market_snapshots.snapshots` (hosted receipt line <= 16 KB for a 500-record batch and `ingest_schedule.jsonl` growth <= 5 MB per hour for one hour) and expired warrants are skipped (listing allowlist active). It no longer requires excluding listed warrants.
2. **T2 receipts** (stop the growth): new receipts carry counts and refs only. Old journal untouched. Rollback: revert; old receipts remain readable.
3. **T1 `daily_bars` dual write:** the pipeline also writes `daily_bars`. Old stores unchanged. Rollback: stop writing; delete `daily_bars`.
4. **T4 backfill** from the existing `market_data_store` (offline): normalized `date=` partitions oldest to newest, dedupe by (symbol, trade_date, content_hash) keeping the newest run, write a temp partition, verify row and symbol counts against the manifest, publish the manifest by atomic rename. Resumable per date; a crash leaves an unpublished temp dir removed by the next run. Rows of already expired warrants are not carried over; rows of listed warrants are.
5. **T3 current record store, phases S0 to S2** (section 4.2): S0 SQLite written beside the journal; S1 flag `MARKET_SNAPSHOT_STORE` with compare mode (0 mismatches over 5 trading days); S2 journal stops being appended and the compat exporter keeps the old format file valid for the previous reader (e7ec353d6) until the 14-trading-day rollback window ends. Rollback at any phase: flag back to `journal` (same image), or redeploy the previous image against the exported journal.
6. **T2b receipt journal rewrite** (offline): stream `ingest_schedule.jsonl` to a new file with `market_snapshots.snapshots` replaced by counts; temp file, verify line count and receipt ids, rename; keep the old file 7 days. Fixes the 5.9 to 6.4 GiB replay (2.2 GB to tens of MB). Rollback: rename back.
7. **T8 warrants:** allowlist built and shadowed for 3 trading days (skipped-not-listed counts reported), then enforced in the collector and connectors, then the one-off delete set of already expired warrants is reported and deleted store by store (each store's delete is its own verified step, section 4.3); afterwards the T5 sweeper deletes each warrant at the first run after it leaves the listing.
8. **Delete (T4, T5)** after 5 trading days of zero mismatch: raw/normalized run files past the verification window; partitions past `retention_days`; snapshot rows of delisted symbols.
9. **[AMENDED 2026-11-17] Daily bars value, time and status columns** (task `SOURCE-DAILY-BARS-VALUE-TIME-STATUS-COLUMNS-20261118`; additive, section 4.1a). Steps: (a) ship the reader that tolerates both v1 and v2 partitions and the previous-reader test in both directions against the hosted version read from `/bff/version`; (b) ship the writer that writes v2 (`daily_bars_manifest.v2`, three columns, new `content_hash`), so every date written from then on is v2; (c) run the backfill (dry run first, as today) to rewrite older dates from the run files by manifest swap, per date, resumable, verifying row count, symbol count and that the open, high, low, close, volume of every key equal the v1 values; (d) compare the daily_bars read with the run file read per date (volume weighted price, timestamp, attestation) with zero differences except the reported residual R-F1; (e) only then the readers default to `daily_bars`, and only after that the run file cleanup may run (section 5.2a). **Rollback:** set `PANTHEON_DAILY_PRICE_READER=run_files` (same image, immediate; the run files are still there because the cleanup has not run). If the writer must be reverted, the previous writer reads v2 days and re-writes them as v1 (the three columns are lost, nothing else, section 4.1a); a v2 partition is never deleted by rollback, and a v1 date that was upgraded can be re-created from the run files by the backfill. The cleanup of run files is not reversible, which is why it comes last.

Rollout order on hosted: nonprod VM (e7ec353d6 stack) first with the hosted-shaped benchmarks, then production; collector sources re-enabled only after T2 and the T8 expired-warrant skip are evidenced on production. Each step records a manifest digest in evidence so rollback can be checked.

## 8. In-flight tasks

| Task | Verdict | Why |
|---|---|---|
| SOURCE-INGEST-RECEIPT-SNAPSHOT-BODIES-BOUND-20261116 | **Stays and becomes T2** (counts and refs in receipts; receipt migration). Review against section 6 limits; if it only truncates a body, extend it to remove the body. | Same scope as acceptance 4. |
| SOURCE-SNAPSHOT-INCREMENTAL-READ-20261116 (**merged to dev**, seen when syncing this branch to base 6f08fdbfb) | **Stays for the journal path in S0 and S1** (it makes the previous reader and the journal backend cheap to read while both exist) **and is retired with the journal at T3b**: its incremental offset/tail-check fold exists only to avoid re-parsing an append-and-rewrite file, which the SQLite current record does not have. T3 must keep its tests green during S0 and S1 and delete them with the journal. | Decision A replaces the mechanism; the merged work is the safe bridge until then. |
| SOURCE-SNAPSHOT-JOURNAL-BOUNDED-20261115 (merged) | **Stays until T3 phase S2, then its in-place compaction is deleted** (the code path `_compact_journal_in_place` has no user once the journal is gone; the exporter writes a fresh file). Dedup behaviour carries over as the `window_digest` predicate. | Needed for S0 and S1. |
| SOURCE-SNAPSHOT-JOURNAL-COMPACTION-20261115 (PR #7414) | **Stays for S0/S1; retired at S2** for the same reason. | Same. |
| Paused collector sources `collector-tw-twse-price-history`, `collector-tw-tpex-price-history` | **Stay disabled**; re-enable per the corrected T0 condition: receipt fix (T2) plus expired warrants skipped (T8 listing); listed warrants need not be excluded. | Prevents 9 MB/min growth and collection of expired warrants. |

### 8a. [AMENDED 2026-11-17] As built: where merged tasks differ from this design

Read from the code on `origin/dev` (head `a16b4b1fc`, 2026-11-17). The design text above is the plan; these are the places where what was merged is different, so a reader does not look for the plan's version.

| Merged task | As built | Design said | Effect |
|---|---|---|---|
| `SOURCE-SNAPSHOT-CURRENT-RECORD-20261117` (T3, PR #7500) | The record is stored **compressed**: `body` is zlib level 6 of the canonical compact JSON (`requirement_state.py` lines 1086 to 1088). The table `snapshot_current` is a **rowid table** with `PRIMARY KEY (symbol, dataset)` (`snapshot_current_store.py` lines 128 to 131), not `WITHOUT ROWID`. **Page size 512** (`DEFAULT_PAGE_SIZE`, `snapshot_current_store.py` line 30). The **window digest includes the run id** (the digest is over the whole compact record without `observed_at`, so a new `ingest_run_id` with the same closes is a changed window, `requirement_state.py` lines 1079 to 1083). The compat **export interval** is `MARKET_SNAPSHOT_EXPORT_INTERVAL_SECONDS`, default 3600 (`runtime.py` line 355). | WITHOUT ROWID table; uncompressed `body BLOB`; digest of the canonical window; export after each daily ingest and at most hourly | Section 4.2 text stays the plan; sizes in section 6 are for the 12 KB per record target, to be re-measured on the compressed record. A re-run of the same closes under a new run id writes a row (the "unchanged window writes nothing" rule holds only for the same run id): residual finding R-F3. The budget checker uses page size 16384 (`SNAPSHOT_PAGE_SIZE`), the store 512 |
| `DEPLOY-PLAN-HISTORY-AVAILABILITY-20261120` (E2 phase 2, PR #7514) | The count per symbol is **derived from the symbol column** only (`daily_bars_reader.py` `availability`, lines 186 to 200); the closes present are **not proven contiguous** against the trading calendar. | E1 text: joined and "checked contiguous against the trading calendar" | A symbol with a gap in its history reports a count that includes the gap; the plan check can accept a strategy whose window has a hole. Residual finding R-F4 |
| `SOURCE-DATASETS-KEYED-WRITE-20261119` (T9, PR #7509) | The key of the news and social datasets is the **item identity** (`("item", "trade_date")` with aliases `url`, `content_ref`, `source_id`, `daily_bars_store.py` lines 624 to 626), not a content hash of (url, published_at, body hash). | Section 6A: "content hash of the item (url, published_at, body hash)" | An edited item with the same url is a replacement, not a second item; a re-fetch with changed body replaces |
| `SOURCE-RECEIPT-REFS-ONLY-20261117-R2` (T2, PR #7473) and `SOURCE-RECEIPT-SLIM-KEEPS-BACKUP-20261117` (PR #7505) | The **service rewrites the receipt journal at start** and **keeps the previous file 7 days** as a hard link `<journal>.pre-migrate.bak` (`scheduler.py` lines 218 to 226, `SOURCE_INGEST_RECEIPT_BACKUP_KEEP_DAYS` default 7; the rewrite is skipped unless the free space margin is met). | T2b: an offline rewrite by script, keep the old file 7 days | No separate offline step is needed on hosted; the script `scripts/migrate_ingest_receipts.py` uses the same backup name |

## 9. Implementation tasks (no code in this task)

Each task: acceptance includes its growth budget row and a test on hosted-shaped data ([CORRECTED 2026-11-17] about 53,000 listed symbols incl. about 50,900 listed warrants, 500 days, 6,500 dead, 10,700 current records at real size, 500-record batches), not a three-symbol fixture. Order: E2 phase 1, T2, T8 shadow, T1, T3 (S0 to S2), E1, E2 phase 2, T4, T9, T5, T10, T7; T6 optional.

**The operator confirmed on 2026-11-16 23:09 Taipei that implementation starts once the design is revised (verbatim: 按照你的建議來做，改完設計後直接實作).** This design task still opens no tasks; the coordinator reconciles the canonical store to this table.

Reconciliation with the six tasks opened at 2026-11-16T14:51Z from the previous design head (all `todo`):

| Opened task | Design id | Verdict against the amended design (A to E, B corrected) |
|---|---|---|
| SOURCE-DAILY-BARS-STORE-20261117 | T1 | **Stays as opened.** Keeps `services/source_ingestion/daily_bars_store.py` and `daily_bars_reader.py` as its file names (the table below uses them instead of `keyed_partition_store.py`). The writer takes a per-dataset key declaration so T9 only adds declarations later. Amended: rows of listed warrants are written, expired-warrant rows are not; budget row is the section 6 row (first written as 1.6 MB/day and 0.8 GB; [CORRECTED 2026-11-17] now 4.0 MB/day and 2.1 GB with ceiling 2.5 GB, section 6). |
| SOURCE-RECEIPT-REFS-ONLY-20261117 | T2 | **Stays as opened**, unchanged by the amendment. |
| SOURCE-SNAPSHOT-SLIM-20261117 | (was T3) | **Replaced** by SOURCE-SNAPSHOT-CURRENT-RECORD-20261117 (T3): slimming the snapshot contradicts decision A (the snapshot keeps 60 closes; what changes is one replaced current record per key). Close it as superseded. |
| SOURCE-MARKET-STORE-BACKFILL-AND-CLEANUP-20261118 | T4 | **Stays as opened.** Amended: expired-warrant rows are not backfilled, listed-warrant rows are; the one-off delete set of already expired warrants is reported first. |
| SOURCE-RETENTION-SWEEPER-20261118 | T5 | **Stays as opened** (its acceptance already deletes expired warrants at expiry). Amended: it uses the T8 listing for expiry detection, is fail closed when the listing is unreadable, and deletes bars, snapshot record and lineage of the warrant at the first run after expiry. |
| MARKET-DATA-BUDGET-CHECK-20261118 | T7 | **Stays as opened.** Amended: checks the recomputed section 6 rows (listed warrants included). |

New or renamed by the amended design (to open): SOURCE-SNAPSHOT-CURRENT-RECORD-20261117 (T3), T3b, SOURCE-WARRANT-LISTING-EXPIRY-20261117 (T8, renamed from exclusion), SOURCE-DATASETS-KEYED-WRITE-20261119 (T9), E1, E2, T10, optional T6.

| Task | Files | Acceptance |
|---|---|---|
| **T1 SOURCE-DAILY-BARS-STORE-20261117** keyed date-partition writer (per-dataset key declaration), `daily_bars` manifest, reader, dual write | new `services/source_ingestion/daily_bars_store.py` (keyed partition writer, per-dataset key declaration), `daily_bars_reader.py`; edit `market_data_storage.py`, `pipeline.py` (`persist_market_data_storage_refs`); tests `services/source_ingestion/tests/test_daily_bars_store.py` | unique (symbol, trade_date); same-content re-run writes nothing; missing trade_date rejected; crash between temp write and manifest rename leaves the old state; `read_close_window` p95 <= 150 ms for 50 symbols; `read_range` caps enforced; day size <= 4.0 MB with listed warrants and the three columns of section 4.1a (amended 2026-11-17 from 1.6 MB); budget alarm fields in health |
| **T2 SOURCE-RECEIPT-REFS-ONLY-20261117** (supersedes/extends SOURCE-INGEST-RECEIPT-SNAPSHOT-BODIES-BOUND-20261116) | `pipeline.py` (`persist_latest_market_snapshots`, `persist_ingest_receipt`), `scheduler.py` (`JsonlIngestScheduleStore` retention and replay), `scripts/` receipt migration; tests | 500-record receipt line <= 16 KB; journal rewrite verifies receipt ids; cold replay RSS < 1 GiB on a 2.4 GB-shaped file converted; collector re-enable evidence |
| **T3 SOURCE-SNAPSHOT-CURRENT-RECORD-20261117** SQLite current-record store, compact record, S0 to S2, compat exporter, sweeper | `services/source_ingestion/requirement_state.py` (`LatestMarketSnapshotStore` keeps its public API, new `SqliteSnapshotBackend`), `services/source_ingestion/routers/ingest_operations.py` (`snapshots/latest`, `frequency` handling), `intraday_snapshot_projection.py` (row replace), `services/execution/lean_runtime/performance_telemetry.py` (verify only); tests incl. `test_snapshot_frequency_isolation.py` extended | 10,700 keys at real size on a copy of the hosted journal: record <= 12 KB (phase 1), live <= 128 MB, file <= 1.1x live; the 60 closes of every key equal the journal's, `snapshot_id` identical; unchanged window writes 0 rows (assert); crash test (kill during a 500-key transaction leaves the previous state); reader call never > 100 ms during a full replacement, writer transaction <= 250 ms p99; compare mode 0 mismatches; old-format export read by the e7ec353d6 `LatestMarketSnapshotStore` (checked-in fixture `requirement_state_70a5bb8.py` pattern) returns the same data; `tw_price_5m..60m` writes refused |
| **T3b SOURCE-SNAPSHOT-JOURNAL-RETIRE-20261201** | `requirement_state.py`, deploy scripts | after 14 trading days in S2: journal, exporter and `_compact_journal_in_place` removed; `dev_release_artifacts.py` capture probe updated |
| **T4 SOURCE-MARKET-STORE-BACKFILL-AND-CLEANUP-20261118** | `scripts/` backfill and cleanup; `market_data_storage.py` | idempotent resumable backfill; count equality vs normalized dedupe; no rows of expired warrants carried (listed ones are); deletion only after the verification window; before/after size evidence |
| **T5 SOURCE-RETENTION-SWEEPER-20261118** | `services/source_ingestion/retention.py`; tests | deletes partitions past `retention_days`; never deletes pinned or delisted-share bars; deletes bars, snapshot record and lineage of a warrant at the first run after it leaves the listing; deletes nothing on warrant grounds while the listing is unreadable; alarm when not run in 2 days |
| **T6 (optional) LEAN-DAILY-EXPORT-20261120** | `services/execution/lean_runtime/` exporter | only if lean runtime needs LEAN folders; derived from `daily_bars` |
| **T7 MARKET-DATA-BUDGET-CHECK-20261118** | `scripts/check_market_data_budget.py`, CI | fails when a row of sections 6 or 6A is exceeded on hosted-shaped data |
| **T8 SOURCE-WARRANT-LISTING-EXPIRY-20261117** (renamed from exclusion) listing allowlist, expired-warrant skip in collector and connectors, one-off deletion of already expired warrants | new `services/source_ingestion/security_listing.py` (parse TWSE ISIN pages by category header and CFI, `listing_allowlist`, `ordinary_share_registry`); `deploy/collector/collector_stack/spiders.py` and `runner.py` (`symbols_filter`, retire `WARRANT_SYMBOL_RE`), `deploy/collector/fetch-policy.json`; `services/source_ingestion/connectors/taiwan_official.py`, `provider_adapters.py`, `active_universe.py`, `market_data_storage.py` (guard); deletion script under `scripts/`; tests with recorded listing pages | parser test on recorded pages of both venues (header rows, CFI `RW*`); fail-closed `listing_unavailable` after 3 days; a 500-record batch with listed warrant codes stores them and one with unlisted (expired) codes stores none and reports `skipped_not_listed_count`; delete set report equals count by store; four-digit codes never auto-deleted; bars of delisted shares kept (test) |
| **T9 SOURCE-DATASETS-KEYED-WRITE-20261119** section 6A datasets on the keyed partition writer | `connector_definitions.py` (per-dataset key declaration), `market_data_storage.py`, per-connector adapters touched only to declare keys; tests per dataset | for each row of 6A: unique key test, same-content re-run writes nothing, budget row measured on hosted-shaped data, readers unchanged |
| **E1 SOURCE-SNAPSHOT-MIN-CLOSES-WINDOW-20261120** (after T1 and T3) `min_closes` extension of `snapshots/latest` and pool market inputs, section 5.4 | `services/source_ingestion/routers/ingest_operations.py`, `daily_bars_reader.py`, `services/execution/lean_runtime/paper_signal_producer.py`, `pool_market_inputs.py`; tests | hosted-shaped data (53,000 listed symbols x 500 days, 10,700 current records): 50 symbols at n = 504 returns exactly 504 closes each, p95 <= 2.0 s; n <= 60 p95 <= 100 ms and reads no partition; a symbol with 212 closes returns `history_insufficient` (requested/available), never a short list; no new stored bytes (budget row unchanged) |
| **E2 DEPLOY-PLAN-HISTORY-CHECK-20261117** (independent of T1 for the first phase) | `services/deployment/service.py`, `services/deployment/test_pool_envelope_plan.py`, `test_inline_paper_execution_projection.py`; later `history/availability` in `ingest_operations.py` | phase 1: plan with `minimum_closes` 504 refused with the readable reason while the snapshot serves 60; plan with 50 accepted; phase 2 (after T1) on hosted-shaped data: refused when any of 50 bound symbols has fewer closes than needed, accepted otherwise; a strategy that previously deployed and never traded now cannot deploy; no growth (no stored data) |
| **T10 INTRADAY-LOAD-BARS-BENCH-20261119** | `services/research/intraday_bars.py` (only if a named consumer misses its bound), `scripts/` benchmark; optional `intraday_cache.py` | benchmark on the hosted host: 50 and 200 symbols x 60 sessions, 1m/5m/15m/60m, results in evidence; bound section 5.3; derived cache built only if a consumer misses the bound, keyed by `shard_sha256`, capped 512 MB, never read as truth |

### 9.1 [AMENDED 2026-11-17] Delivery state of every task of this design

State is what could be checked on `origin/dev` head `a16b4b1fc` on 2026-11-17: "merged" means a merge commit of the task branch is on `dev`. "deployed" and "accepted" on hosted are not visible from a branch; they belong to `MARKET-DATA-ONE-WRITE-HOSTED-ACCEPTANCE-20261117` (owner Human/Ops, open), so no row below claims deployed or accepted. The task state in the live store of the coordination root was not read for this table.

| Design id | Task | State on dev (PR) |
|---|---|---|
| T1 | SOURCE-DAILY-BARS-STORE-20261117 (R2) | merged (#7465) |
| T2 | SOURCE-RECEIPT-REFS-ONLY-20261117 (R2); earlier SOURCE-INGEST-RECEIPT-SNAPSHOT-BODIES-BOUND-20261116 | merged (#7473; #7442) |
| T2b follow-up | SOURCE-RECEIPT-SLIM-KEEPS-BACKUP-20261117 | merged (#7505) |
| T3 | SOURCE-SNAPSHOT-CURRENT-RECORD-20261117 | merged (#7500) |
| T3b | SOURCE-SNAPSHOT-JOURNAL-RETIRE-20261201 | not merged; to open after 14 trading days in S2 |
| T4 | SOURCE-MARKET-STORE-BACKFILL-AND-CLEANUP-20261118 (R2) | merged (#7485); the cleanup step stays blocked (section 5.2a) |
| T5 | SOURCE-RETENTION-SWEEPER-20261118 | no merge found on dev |
| T6 (optional) | LEAN-DAILY-EXPORT-20261120 | not opened |
| T7 | MARKET-DATA-BUDGET-CHECK-20261118 (R2) | merged (#7471); its constants must follow (section 9A) |
| T8 | SOURCE-WARRANT-LISTING-EXPIRY-20261117 | merged (#7472) |
| T9 | SOURCE-DATASETS-KEYED-WRITE-20261119 | merged (#7509) |
| T10 | INTRADAY-LOAD-BARS-BENCH-20261119 | no merge found on dev |
| E1 | SOURCE-SNAPSHOT-MIN-CLOSES-WINDOW-20261120 | merged (#7513) |
| E2 | DEPLOY-PLAN-HISTORY-CHECK-20261117 (phase 1); DEPLOY-PLAN-HISTORY-AVAILABILITY-20261120 (phase 2) | merged (#7461; #7514) |
| in-flight | SOURCE-SNAPSHOT-INCREMENTAL-READ-20261116 | merged (#7439) |
| hosted | MARKET-DATA-ONE-WRITE-HOSTED-ACCEPTANCE-20261117 | open (owner Human/Ops) |

**Tasks opened on 2026-11-17 (after the first revision of this design):**

| Task | Purpose | State on dev |
|---|---|---|
| SOURCE-HOSTED-LISTING-STATE-AND-PIN-SOURCE-20261118 | listing state file written by `security_listing.py` (measured 57,531 rows, 3.2 MB), pin source | merged (#7521) |
| SOURCE-DAILY-BARS-READERS-COMPLETE-20261118 | the readers switch by `PANTHEON_DAILY_PRICE_READER` | merged (#7522) |
| SOURCE-DAILY-BARS-VALUE-TIME-STATUS-COLUMNS-20261118 | the three columns of section 4.1a, schema v2, backfill upgrade, readers use them | not merged; implements this amendment |
| SOURCE-RECONCILE-TOLERATES-NEWER-STATE-20261117 | the reconcile step tolerates a newer state than it expected | merged (#7517) |

### 9A. [AMENDED 2026-11-17] Constants and code that must follow this amendment (for the implementation task; this task changes no code)

| File | Name | Now | Must become |
|---|---|---|---|
| `services/source_ingestion/daily_bars_store.py` | `DAILY_BARS_DAY_TARGET_BYTES` | 1_600_000 | 4_000_000 |
| same | `DAILY_BARS_DAY_ALARM_BYTES` | 2_500_000 | 6_000_000 |
| same | `DAILY_BARS_TOTAL_CEILING_BYTES` | 1_000_000_000 | 2_500_000_000 |
| same | `LINEAGE_DAY_TARGET_BYTES` | 300_000 | 400_000 |
| same | `LINEAGE_DAY_ALARM_BYTES` | 450_000 | 600_000 |
| same | `LINEAGE_TOTAL_CEILING_BYTES` | 150_000_000 | 200_000_000 |
| same | `DAILY_BARS_SCHEMA_VERSION` | `daily_bars_manifest.v1` | `daily_bars_manifest.v2` (manifest lists `columns`) |
| same | `_RAW_ROW_FIELDS`, `DAILY_BARS.value_fields`, the Arrow schema (around lines 151 to 158), `extract_rows` (lines 192 to 273: the three fields, the `not_traded` exception to `bad_close`, `content_hash` over twelve fields), the `write_day` merge (line 367), lineage run refs (`_write_lineage`: source id, upstream endpoint, venue, bundle sha) | nine value fields | add `traded_value`, `available_time`, `trading_status` |
| `services/source_ingestion/daily_bars_reader.py` | `_COLUMNS` (line 22); `PRICE_COLUMNS` stays; skip rows with null close | ten names | add the three names |
| `scripts/check_market_data_budget.py` | `LISTED_SYMBOLS` (line 54), `LISTED_WARRANTS` (line 55), `daily_bars_rows` (the three columns), the docstring volume (lines 14 and 16), the listing rows generator (`listing_rows`, 57,531 as measured) | 22_500; 20_200 | 53_000; 50_900. The limits are read from the section 6 table, so the table of this document is already the new limit |
| `scripts/backfill_daily_bars.py` | upgrade of v1 days to v2 from the run files, report of dates that cannot be upgraded; uses `DAILY_BARS_DAY_ALARM_BYTES` (line 289) | v1 only | v2 |
| `services/research/alpha_templates/evaluator.py` and `services/source_ingestion/candidate_pool_market_feed.py` | the daily_bars branches (evaluator lines 577 to 705, feed lines 182 to 205 and 401 to 452) read the columns | derived 14:30 and close | section 5.2a |
| `services/source_ingestion/security_listing.py` | `MAX_STATE_BYTES` | 20 MiB | unchanged (3.2 MB measured) |

**Residual findings of this amendment (outside the scope, recorded, not fixed):** R-F1 the two run file readers resolve copies of a key differently (section 4.1a item 4). R-F2 the compat export in the old line format would be 3.8 GB if every listed key held a 60-close line (section 6): the operator should decide whether listed warrants get a 60-close snapshot at all or only the daily bars row; the design has not decided it. R-F3 the window digest includes the run id, so a same-close re-run under a new run id rewrites the row. R-F4 the history availability count does not prove contiguity (section 8a).

Operator decisions: warrants (B, option (b) 抓，到期就刪) decided; snapshot window (A) decided; open points only for the operator to read: the phase-1 record ceiling (12 KB) and the 14-trading-day rollback window.

## 10. 中文摘要（給 operator）

**現在一筆收盤價被複製幾次？**
一筆台股日收盤現在至少存在 5 個地方：原始檔（raw）、整理後檔（normalized）、最新快照（每天整行重寫，連 60 天一起）、收據（ingest receipt，一整批約 15 MB 的快照內容）、證據／蒸餾資料。最大的一份在收據裡（約 30 KB／筆，是收盤價本身的 20 倍）。快照每次新增一天就「在舊的那行後面再寫一行新的」，舊的要等壓縮才刪；壓縮時會把整個檔案鎖住（今天就因此讓一次 controller tick 失敗）。

**有一點要更正：** 協調者說檔案是用「跑資料的日期」分區。程式碼是用交易日（`trade_date`／`event_time`）分區，只有缺日期才退回用今天。真正的問題是：同一天有幾十個「每次執行一個檔」的檔案、沒有按股票的索引、重跑會再多一組。需要先量（M1）確認。

**依你 2026-11-16 的裁示，這次改成：**
- **最新快照保留最近 60 天收盤價（A）。** 但存法改了：每檔每個資料集只有「一筆現行紀錄」，新版直接取代舊版，舊版不留；內容沒變就不寫。用 SQLite（Python 內建，不加新套件），一次寫一批 500 檔，要嘛全成功要嘛全不變，當機也安全；沒有整檔壓縮，也就沒有長時間鎖。讀的人不會被寫的人擋住（在本機實測：一批 500 檔寫入最久 0.19 秒，同時讀取 99% 在 1.8 毫秒內）。一筆 60 天紀錄從約 71 KB 縮成 12 KB 以下（來源／連接器／市場等重複欄位只存一次、衍生的 closes 與 lineage 不存，之後有日線庫再縮到 4 KB 以下）；總量上限約 128 MB（預期約 50 MB），現在是 171 MB 加上舊版。舊版讀取程式（e7ec353d6）在上線與回退期間照常運作：我們會定期把現行紀錄匯出成舊格式檔（原子替換、每筆只一版），14 個交易日沒有回退後才刪除。
- **權證：上市期間照抓，到期就刪（B，已更正為你選的「抓，到期就刪」）。** 用交易所公開的證券清單判斷（證交所 ISIN 清單依類別與 CFI 碼 `RW…` 區分，2026-11-17 實測上市 37,740 檔、上櫃 13,143 檔權證，對比 2,075 檔股票／ETF；原寫的 7,053 檔是少算，已更正）；不再靠代號長相猜。收集端（collector 價格歷史、T86、停牌）與主機端連接器只收清單上還在的代號，所以上市中的權證照抓、照存；清單上不再出現（到期）的權證，在下一次清理時把日線、快照紀錄、lineage 全部刪掉，不封存。已經到期的舊權證不補歷史，現有的舊權證資料（今天約 7,550 個權證代號還在快照裡，其中約 6,750 個超過 30 天沒收盤價）先出清單報告再刪；四碼代號（像普通股）不會被自動刪，只列入人工檢查清單。清單讀不到時不判定到期、不刪任何東西。已下市普通股的日線照舊保留。容量（2026-11-17 已按實測 53,000 檔重算，見最後的更新段落）：上市權證約 50,900 檔，日線每天最多約 4.0 MB，兩年總量上限 2.5 GB（權證到期即刪）；現行快照紀錄最多約 53,000 筆、第一階段上限約 640 MB。
- **其他頻率（C）：** 跟隨 2026-11-11 的分鐘線設計：1 分鐘線每個（股票、日、分鐘）只存一次；5／15／30／60 分鐘即時由 1 分鐘線算，不另存；日線真值絕不由分鐘線算出。快照只保留 1 分鐘的一筆現行紀錄（約 200 檔、不超過 1 MB），5～60 分鐘永遠不寫入快照。實測 `load_bars`（60 個交易日）：50 檔約 14～28 秒、200 檔約 70～97 秒（每檔 0.28～0.56 秒）；下一交易日 paper（≤50 檔）和單檔回放都在上限內，只有 200 檔全市場掃描會超過，所以預先規定「有上限的可丟棄快取」（以資料檔雜湊作鍵、上限 512 MB、不是第二份正本），只有主機實測超標才建。
- **其他資料集（D）：** 指數、廣度、法人、融資券、券商、期交所、總經、社群、新聞／社群／論文與 features/returns 每一個都列出唯一鍵、寫一次或取代的規則、保存期、每日成長上限與讀者（第 6A 節）；features/returns 不再存，改由日線庫即時算。目前它們共用同一個「每次執行一個檔」的寫入器，所以都需要改，沒有任何一個能先宣稱已達標。
- 收據只放數量和參考；日線庫「每檔每日一次」依交易日分區，要看任何一段歷史直接讀那幾天。

**哪些會刪掉（不是封存）：** 被取代的快照版本（不再存在）、收據裡的快照內容、下市或過期代號在快照的紀錄、到期權證的全部資料、驗證期後的舊 raw／normalized 檔、超過保存期限的分區。已下市普通股的日線保留。

**需要超過 60 天收盤價的策略（E）：** 快照照舊只留 60 天，不加大。策略需要更長歷史（例如 120 日均線、因子規則最長 504 天）時，paper 取資料的那一次呼叫帶 `min_closes=n`：前 60 天從現行紀錄讀，其餘從日線庫讀，回傳剛好 n 天，否則回「歷史不足（要 n、只有 m）」，不會回較短的清單假裝成功。50 檔、504 天，上限每次決策 2 秒（T1 在主機形狀資料上實測）。日線庫上線前，這種策略在**部署時**就被拒絕並說明原因（目前快照最多供 60 天），不會再部署後每次決策都失敗、永遠不交易。目前主機最近 24 小時沒有這種錯誤，代表現有策略都不需要超過 60 天。
**指標能不能「帶著前一天的值往前推」（例如 120 日均線＝昨天的值＋今天收盤－滑出視窗的那一天）？** 可以，但這次**不採用**。現在 paper 每次決策都用和回測同一套「無狀態」計算，從原始收盤價整個重算。不採用的原因：每次重啟、每次有收盤價被更正，帶著走的值都要用完整歷史重建，歷史還是得讀；而且回測與 paper 必須是同一套計算才能保證結果一致，再加一條增量算法會破壞這點。有了 E，重算成本有上限（≤2 秒），除非實測某個決策超過上限才重新考慮。

**[2026-11-17 更新] 兩處修正（你回答「修」）：**
1. **日線庫每一列多三欄：成交金額、資料何時可得、交易狀態（第 4.1a 節）。** 原因：讀取程式改讀日線庫後，沒有成交金額就只能用收盤價算成交量加權價，沒有「何時可得」就丟掉當時才看得到的時間點，沒有交易狀態（交易／未成交）就沒辦法證明名單包含已下市和停牌的股票（存活者偏誤證明）。三欄缺值一律存成「空」，絕不存成 0；「何時可得」只有日期的列，固定當成交易日台北 14:30（與現在讀舊檔的結果一樣）；同一天有好幾份重複資料時，用成交量最大的那份的成交金額，可得時間取最晚的。每次抓取的來源編號與上游網址放在 lineage（每次執行一筆），不是每列一份。版本從 v1 變 v2；舊版讀取程式遇到新欄位會直接忽略，不會壞；舊日期在下次寫入或補歷史時整天改寫成 v2。舊的原始檔（run files）在讀取程式都改預設讀日線庫之前，仍然不能清。
2. **容量全部改用實測的上市櫃證券數。** 2026-11-17 官方 ISIN 頁面：證交所權證 37,740、櫃買權證 13,143、證交所股票 1,058、櫃買股票 898、櫃買 ETF 119，共約 **53,000 檔**。原設計寫約 22,500 檔、證交所權證 7,053，少算約 5 倍，這裡更正。（我自己去抓這兩個頁面時回傳空內容，所以數字是採用你提供的，不是我自己數的。）重算結果：日線每天上限約 4.0 MB（53,000 × 約 75 位元組），兩年總量上限 2.5 GB（原 1.0 GB）；現行快照紀錄在每一檔都有的最壞情況約 636 MB（第一階段，只算現行活躍名單 200 檔則約 2.4 MB）；證券清單狀態檔實測 3.2 MB，上限 20 MB；每天收據約 0.9 MB。權證仍然是上市期間照抓、到期就刪，沒有改變。這次只改文件，沒有改程式。


**上線順序：** 部署時歷史檢查（E2 第一階段）→ 收據先修（停止每分鐘 9 MB 成長）→ 權證清單影子模式 → 日線庫雙寫 → 快照現行紀錄（影子→對照 5 個交易日→切換→匯出舊格式）→ 補歷史 → 其他資料集 → 最後才刪舊檔；collector 價格歷史要等收據修好、且已到期權證會被跳過才重新啟用（不再要求排除上市中的權證）。
