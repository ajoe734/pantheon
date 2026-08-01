# LIFECYCLE-PROJ-STORE-001 verification evidence

Status: owner verification complete; independent exact-head review pending

Owner: Codex

Reviewer: Antigravity

Base: `dev@941c15a34208e54e96cdd148ba3a5bfcd339abab`

Review manifest: `evidence.json`

## Delivered boundary

The task adds only the relational projection schema, typed PostgreSQL store,
transaction invariants, real-PostgreSQL tests, and task evidence. It does not
change `telemetry_events`, the reducer, BFF routes, Compose, deployment, or
cutover. No consumer has been enabled.

The owner repair is anchored at
`e46308f432bc38ac8e3d65c8f8405a1882aea544`. Exact-head review must cover all
later evidence/test commits on the task PR.

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

- focused real-PostgreSQL suite: `17 passed in 6.08s`;
- adjacent Trade Journey regression: `136 passed in 28.53s`;
- Python compilation and whitespace validation: exit 0;
- PostgreSQL server: `16.14` on x86_64 Alpine;
- least privilege: a temporary DML-only login processed a receipt and
  checkpoint, while schema bootstrap failed with `InsufficientPrivilege`;
- migration: the exact SQL file applied twice to an isolated schema; the second
  application emitted only already-exists notices;
- indexed reads: five 5,000-row `ANALYZE`d query plans used the exact indexes
  recorded in `explain-plans.txt`.

`ruff` was not available in the provisioned environment, so no ruff result is
claimed. Repository CI remains responsible for its configured static gates.

## Review checklist

Antigravity must independently reproduce or inspect:

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
9. PR checks are green and the canonical approval binds the exact reviewed
   head. The eventual protected merge SHA belongs in governed delivery metadata
   because the reviewed manifest cannot contain its own future merge commit.

## Rollout and rollback

Rollout is additive schema application only, with no worker or BFF reader
enabled. Rollback stops/does not start consumers and leaves the unused schema
intact. A destructive down migration is prohibited.
