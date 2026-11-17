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

The run is red only for the red findings. The report also carries filesystem free space,
`docker system df`, and the top containers by memory. When you bound a writer, add its numeric
budget to `BUDGETS` (keyed by manifest name) in the same change.

Run: `python3 -m unittest scripts.test_measure_hosted_storage scripts.test_dev_storage_budget_workflow`.
