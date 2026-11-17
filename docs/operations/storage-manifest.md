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
