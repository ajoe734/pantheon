# Storage Manifest

`docs/operations/storage-manifest.json` registers every persistent writer the code names
(RC3 WP3.2, `docs/operations/remediation-plan-20261117.md`). `scripts/check_storage_manifest.py`
discovers the same set from non-test sources and fails when the two disagree. It runs in
`branch-ci.yml` ("Storage manifest check") on every pull request.

## What is discovered

| kind | key |
|---|---|
| `jsonl` | `[A-Za-z0-9_.{}-]+\.jsonl` in `services/**/*.py` |
| `sqlite` | `...\.(sqlite3\|sqlite\|db)` in `services/**/*.py` |
| `sqlite_connect` | each `services/` file calling `sqlite3.connect` |
| `compose_path` | `/data/...(json\|jsonl\|sqlite3\|sqlite\|db)` in `docker-compose.yml` |
| `table` | `CREATE TABLE` targets (`services/**/*.py`, `*.sql`, `scripts/db_migrate.sh`); a templated target is keyed `<file>::<template>` |
| `schema_table` | `schema.table` literals on a line that mentions "table" in `services/**/*.py` |

Excluded: `test_*.py`, `tests/**`, `conftest.py`, `fixtures/**`, `reproduce_*.py`.

## Adding a writer (AGENTS.md "Runtime Budget")

When your change adds a file, table, queue or cache, the check goes red until you add an entry.
The entry must state, with numbers at hosted scale:

- `owner`, `growth_driver`, `growth_per_day`, `cap`, `cleanup`, `readers`;
- `bound_status`:
  - `bounded`: also give `bound_proof` `{file, contains}`; the check verifies the text exists
    in code. Claim it only when the code enforces the cap and the cleanup is actually invoked
    (a `prune()` that nothing calls is not a bound);
  - `unbounded`: give `register_class` and the `task` that will bound it;
  - `unreviewed`: nobody has yet judged the writer; give `register_class` and a `task`.
    This is a debt marker, not a claim.
- `measured` (optional): a size measured on hosted, with its source.

Use `name` for an exact key or `pattern` (a full-match regex) for a templated family. A
discovered name that is only a regex fragment may get a `waivers` entry with a reason. Removing
a writer from code requires removing its entry (the check fails on stale entries).

Run: `python3 scripts/check_storage_manifest.py` and
`python3 -m unittest scripts.test_check_storage_manifest`.

## Daily hosted measurement (RC3 WP3.3)

`.github/workflows/dev-storage-budget.yml` runs daily (03:17 UTC) and on demand. It measures every
manifest entry on hosted dev with `scripts/measure_hosted_storage.py`: file sizes, mtimes and
`wc -l` counts under `/data` of running containers, and `pg_total_relation_size` / `reltuples`
from `pg_class` through `docker exec psql` with `default_transaction_read_only=on`. It never reads
file contents or container environment, takes no environment lease and writes nothing on hosted.

The report (`hosted-storage-report` artifact, one per day, 30 days retention) lists, with the
manifest entry id, owner, register class and task for each finding:

| finding | severity | meaning |
|---|---|---|
| `exceeded_size` / `exceeded_growth` | red | measured bytes or change per day since the previous run is over the numeric budget in `BUDGETS` |
| `orphan_temp_files` | red | temp-named files (`*.tmp`, `*.part`, `tmp*`) older than 24 hours |
| `unbounded` | warning | an `unbounded` entry is measured but has no numeric budget yet |
| `unknown_state_file` | warning | file under `/data` older than 7 days that no manifest entry covers (grouped by directory) |

Per container memory (limit, `memory.peak`, `memory.events` max and oom_kill, restart count) is also reported with
findings `container_oom_killed` and `container_restarts_grew` (red) and `memory_limit_reached`,
`memory_peak_near_limit`, `memory_limit_missing` (warning); see docs/deployment/dev-container-memory-budget.md.

The run is red only for the red findings. The report also carries filesystem free space,
`docker system df`, and the top containers by memory. When you bound a writer, add its numeric
budget to `BUDGETS` (keyed by manifest name) in the same change.

Run: `python3 -m unittest scripts.test_measure_hosted_storage scripts.test_dev_storage_budget_workflow`.

## Canonical telemetry table retention (`public.telemetry_events`, `TELEMETRY-EVENTS-RETENTION-14D-20261125`)

`services/telemetry/heartbeat_retention.py` (`HeartbeatRetentionJob`, running every 300 s in `services/telemetry/main.py` with `<= 20` batches x `5000` rows per event class per run and a 15 s statement timeout, plus `scripts/telemetry_heartbeat_reclaim.py`) bounds `public.telemetry_events` within the 5 GB / `<= 100 MB/day` `BUDGETS` cap in `scripts/measure_hosted_storage.py`:

| Event class | Retention window | Env var (default) | Safety invariants & preserved rows |
|---|---|---|---|
| `heartbeat` | 3 days | `PANTHEON_TELEMETRY_HEARTBEAT_RAW_RETENTION_DAYS=3` | Summarized per `(runtime_id, hour_bucket)` into `public.telemetry_heartbeat_hourly_summaries` before deletion; never deletes rows above `safe_ingested_seq`. |
| `runtime_state_snapshot` | 3 days | `PANTHEON_TELEMETRY_STATE_SNAPSHOT_RETENTION_DAYS=3` | Keeps newest and oldest snapshot per runtime plus quarter-equity unknown-valuation status rows (`marks_unavailable`, `invalid_ledger`, `invalid_drawdown_series`); never deletes rows above `safe_ingested_seq`. |
| `infrastructure_health` | 7 days | `PANTHEON_TELEMETRY_INFRA_HEALTH_RETENTION_DAYS=7` | Protects any `event_id` referenced by `incident.incident_cases`, `incident.postmortems`, `incidents.json`, or BFF `downstream_health.sqlite3`; never deletes rows above `safe_ingested_seq`. |
| All other non-permanent `public.telemetry_events` rows | 14 days (2026-11-25 operator decision) | `PANTHEON_TELEMETRY_EVENTS_RETENTION_DAYS=14` | Never deletes rows above `safe_ingested_seq`; protects all incident/postmortem-referenced `event_id`s; preserves per-runtime snapshot/valuation anchors (`pnl_snapshot`, `drawdown_snapshot`, `position_snapshot`, `reconciliation_completed`/`reconciliation_failed`, distinct `quarter_equity.v1` valuation observations, binding oldest/newest/lifecycle anchors) and permanent canonical deploy/governance/fill/order/decision/episode rows (`< 15 KB/day` = `< 0.5 MB/month`). |

## Bounded in-memory runtime caches (0 persistent writers)

| cache | owner | cadence | hosted size & count | growth/day | cap & cleanup |
|---|---|---|---|---|---|
| `_MARKET_FACT_CACHE` (`services/execution/lean_runtime/instrument_candidates.py`) | execution (`RISK-POLICY-BLUEPRINT-LIMITS-ENFORCED-20261124`) | on-demand per expression / pre-order risk evaluation (cached for 300 s TTL per dataset partition mtime and symbol) | <= 256 entries per process (~64 KB max RSS across 3 trading personas; 0 disk bytes) | 0 bytes/day on disk; bounded in RAM | `FACT_CACHE_MAX_ENTRIES = 256`, `FACT_CACHE_TTL_SECONDS = 300.0`; expired entries evicted on every `_fact_cache_get` and oldest LRU entry evicted on `_fact_cache_put` |
| `_ledger_fill_history` (`services/execution/lean_runtime/paper_runtime.py`) | execution (`RISK-POLICY-BLUEPRINT-LIMITS-ENFORCED-20261124`) | on paper fill commit (`_commit_fill` / `_commit_futures_fill`) | <= 256 fill records per algorithm instance (~48 KB max RSS; 0 disk bytes, not persisted in `paper_performance_ledger.v1`) | 0 bytes/day on disk; bounded in RAM | hard cap of 256 records (`self._ledger_fill_history[-256:]` on append) |
| `_pool_risk_account_facts` & `_pool_risk_policy_facts` (`services/telemetry/pool_risk_snapshot.py` in existing `runtime_summaries.json`) | execution / telemetry (`PAPER-POOL-RISK-SNAPSHOT-IDLE-FACTS-20261125`) | carried in existing `runtime_state_snapshot` metadata on state change only (0/hr when unchanged; <= 4/hr MTM cap per runtime, ~3–75 events/day typical across 3 hosted runtimes, <= 288 events/day theoretical 24 h MTM ceiling) | ~332 B added per `runtime_state_snapshot` event (< 2 KB manifest threshold); 1 replaced dict each per runtime in `runtime_summaries.json` (~85 B account facts + ~270 B policy facts = ~355 B/runtime, ~1.07 KB across 3 runtimes; 0 new persistent writers) | 0 bytes/day unbounded growth (replaced in-place, never appended; never-filled backfill bounded by existing `RETENTION_TRADING_DAYS = 400` in `_pool_risk_snapshot_rows`) | replaced in-place on `current[_pool_risk_account_facts]` (<= 4 keys) and `current[_pool_risk_policy_facts]` (<= 9 keys), cleared on binding rollover |
