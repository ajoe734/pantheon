# LIFECYCLE-PROJ-STORE-001 verification evidence

Status: owner verification complete; independent exact-head review pending

Owner: Codex

Reviewer: Antigravity

Base: `dev@66621e3484a690921032d93e26ec2c9867708d2a`

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
task artifact paths.

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
  `17 passed in 6.77s`;
- adjacent Trade Journey regression after current-dev merge:
  `136 passed in 31.54s`;
- dedicated migration-twice, DML-only, and EXPLAIN slice:
  `3 passed, 14 deselected in 1.48s`;
- Python compilation and whitespace validation: exit 0;
- PostgreSQL server: `16.14` on x86_64 Alpine;
- least privilege: a temporary DML-only login processed a receipt and
  checkpoint, while schema bootstrap failed with `InsufficientPrivilege`;
- migration: the exact SQL file applied twice to an isolated schema; the second
  application emitted only already-exists notices;
- indexed reads: a fresh isolated schema was migrated twice, seeded with 5,000
  rows per relation, and `ANALYZE`d; all five query plans used the exact indexes
  recorded in `explain-plans.txt`, after which the temporary schema was dropped;
- independent reviewer readiness: a fresh live Antigravity auth/quota probe
  returned `ready` at `2026-08-02T09:43:03Z` using
  `gemini-3.6-flash-low`.

`ruff` was not available in the provisioned environment, so no ruff result is
claimed. Repository CI remains responsible for its configured static gates.

## Review checklist

Antigravity must independently reproduce or inspect on PR #4503's exact final
head:

1. exact duplicate input carrying mutated stage and journey rows leaves both
   rows unchanged and does not increment the revision;
2. receiptless row mutations fail closed, receiptless checkpoint claims do not
   advance, and filling sequence 4 crosses a previously durable sequence 5;
3. rollback leaves no receipt/controller and a corrected retry commits once;
4. backfill, recovery, and replay force `accepted_live=false`, retain the prior
   live timestamp, and update only their owned timestamp;
5. quarantine retry/count truth remains idempotent and controller-scoped;
6. lock IDs match across distinct `PYTHONHASHSEED` processes, same-controller
   contention fails immediately, and another controller remains ready;
7. the canonical identifier registry and first/last identity bounds are
   enforced by PostgreSQL;
8. the exact migration file applies twice, the prior controller query remains
   valid, the runtime role has DML without DDL, and all five `EXPLAIN` plans use
   the intended index names;
9. PR checks are green and the canonical approval binds `REVIEW_PR=4503` plus
   the exact 40-hex final head. The eventual protected merge SHA belongs in
   governed delivery metadata because the reviewed manifest cannot contain its
   own future merge commit.

## Rollout and rollback

Rollout is additive schema application only, with no worker or BFF reader
enabled. Rollback stops/does not start consumers and leaves the unused schema
intact. A destructive down migration is prohibited.
