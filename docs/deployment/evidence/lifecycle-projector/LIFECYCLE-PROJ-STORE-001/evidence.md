# LIFECYCLE-PROJ-STORE-001 verification evidence

Status: rejected-head blockers repaired; independent exact-head re-review required

Owner: Codex

Reviewer: Codex2

Base: `dev@0404ca01ebb6803df6a4b927bacada5739f61de1`

Branch: `task/LIFECYCLE-PROJ-STORE-001-V2`

Superseding PR: [#4503](https://github.com/ajoe734/pantheon/pull/4503)

Review manifest: `evidence.json`

## Delivered boundary

The task adds only the relational projection schema, typed PostgreSQL store,
transaction invariants, real-PostgreSQL tests, and task evidence. It does not
change `telemetry_events`, the reducer, BFF routes, Compose, deployment, or
cutover. No consumer has been enabled.

The current-dev compose is anchored at
`090e0a1cd115728236ad1d74633630ce1b9b3f30`. It supersedes PR #4476 without
rewriting its published history: that PR remains based on `941c15a` and contains
an 86-character commit subject that fails the Commit trailers gate. The new
branch restores only the four declared task artifact scopes onto current dev.
Exact-head review must cover the final evidence commit on PR #4503.
The branch then merged current `dev@66621e34` in
`7e94409aa7e239042c300ef448991502d57f8aa3` without changing the seven declared
task artifact paths, and refreshed again to `dev@c92e60ce` in
`9f47ae6be4b544d5c5169d0d0aeb3d09a135a91a`, also without task-path overlap.
After Codex2 rejected exact head `3e525480706ee0eb1cb5aadf583cb2a6c9670d1c`,
the owner anchored the task brief at `1f2444bb`, repaired receipt ownership and
race atomicity at `6b4e6f6b`, and merged `dev@0404ca01` in `d96bdd29`; the
latest refresh also had no task-path overlap.

## Rejected-head blocker repair

The repair changes receipt handling from a pre-read followed by unchecked late
insert into an atomic global claim before any derived mutation. If two distinct
controller locks both pre-read the same absent event, PostgreSQL admits one
claim; the loser detects that it lost the claim, raises a dedicated failure,
and rolls back its controller and every derived row.

For a mixed batch, only identity, journey, stage, loop, and quarantine rows
owned by receipts newly claimed in the current transaction are applied.
Already-durable duplicate-owned rows are ignored. If a new and duplicate
receipt share an aggregate ownership key, the transaction fails closed and
requires a new-only retry instead of guessing at provenance.

Codex2's prior independent decision on `3e525480` was `changes_requested` for
these two defects. That decision is retained in `evidence.json`; Codex2 must
make a fresh independent decision on the final post-evidence PR head.

## Exact verification commands

The local connection values were supplied through secret-bearing environment
variables and are intentionally not copied into repository evidence.

```bash
python3 scripts/dev/provision_python_distribution.py

TEST_DATABASE_URL="$LOCAL_TEST_DATABASE_URL" \
TEST_DATABASE_ADMIN_URL="$LOCAL_TEST_DATABASE_ADMIN_URL" \
.venv-pantheon/bin/python3 -m pytest -q \
  services/trade_journey/test_projection_store.py

TEST_DATABASE_URL="$LOCAL_TEST_DATABASE_URL" \
TEST_DATABASE_ADMIN_URL="$LOCAL_TEST_DATABASE_ADMIN_URL" \
.venv-pantheon/bin/python3 -m pytest -q services/trade_journey

python3 -m py_compile \
  services/trade_journey/projection_store.py \
  services/trade_journey/test_projection_store.py

git diff --check
```

Results:

- focused real-PostgreSQL suite after current-dev merge:
  `19 passed in 14.93s`;
- adjacent Trade Journey regression after current-dev merge:
  `138 passed in 60.24s`;
- dedicated migration-twice, DML-only, and EXPLAIN slice:
  `3 passed, 16 deselected in 2.55s`;
- Python compilation and whitespace validation: exit 0;
- PostgreSQL server: `16.14` on x86_64 Alpine;
- least privilege: a temporary DML-only login processed a receipt and
  checkpoint, while schema bootstrap failed with `InsufficientPrivilege`;
- migration: the exact SQL file applied twice to an isolated schema; the second
  application emitted only already-exists notices;
- indexed reads: a fresh isolated schema was migrated twice, seeded with 5,000
  rows per relation, and `ANALYZE`d; all five query plans used the exact indexes
  recorded in `explain-plans.txt`, after which the temporary schema was dropped;
- mixed duplicate/new regression: the old journey, stage, quarantine, and their
  row revisions/timestamps remained unchanged while the new receipt, journey,
  stage, and controller revision committed;
- overlapping-controller regression: two real connections synchronized after
  the receipt pre-read produced one success and one concurrent-claim rollback,
  leaving one receipt, one journey, and one controller row.

`ruff` was not available in the provisioned environment, so no ruff result is
claimed. Repository CI remains responsible for its configured static gates.

## Review checklist

Codex2 must independently reproduce or inspect on PR #4503's exact final
head:

1. a mixed batch containing a durable exact duplicate plus a new event leaves
   the old journey, stage, quarantine, and row revision/timestamp truth
   unchanged while committing only the new event's derived rows;
2. two distinct controller locks synchronized after the receipt pre-read yield
   one atomic derived commit and one concurrent-claim rollback;
3. exact duplicate input carrying mutated stage and journey rows leaves both
   rows unchanged and does not increment the revision;
4. receiptless row mutations fail closed, receiptless checkpoint claims do not
   advance, and filling sequence 4 crosses a previously durable sequence 5;
5. rollback leaves no receipt/controller and a corrected retry commits once;
6. backfill, recovery, and replay force `accepted_live=false`, retain the prior
   live timestamp, and update only their owned timestamp;
7. quarantine retry/count truth remains idempotent and controller-scoped;
8. lock IDs match across distinct `PYTHONHASHSEED` processes, same-controller
   contention fails immediately, and another controller remains ready;
9. the canonical identifier registry and first/last identity bounds are
   enforced by PostgreSQL;
10. the exact migration file applies twice, the prior controller query remains
   valid, the runtime role has DML without DDL, and all five `EXPLAIN` plans use
   the intended index names;
11. PR checks are green and the canonical approval binds `REVIEW_PR=4503` plus
   the exact 40-hex final head. The eventual protected merge SHA belongs in
   governed delivery metadata because the reviewed manifest cannot contain its
   own future merge commit.

## Rollout and rollback

Rollout is additive schema application only, with no worker or BFF reader
enabled. Rollback stops/does not start consumers and leaves the unused schema
intact. A destructive down migration is prohibited.
