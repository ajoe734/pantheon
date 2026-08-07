# LIFECYCLE-PROJ-STORE-001 verification evidence

Status: reassigned to a new owner, rebuilt on current `dev`, and re-verified;
independent exact-head review required

Owner: Claude

Reviewer: Antigravity

Base: `dev@eca6b7de6313027d4c943679a1fa8fb7d93028ba`

Source branch: `task/LIFECYCLE-PROJ-STORE-001`

Delivery PR: [#4557](https://github.com/ajoe734/pantheon/pull/4557)

Review manifest: `evidence.json`

## Ownership transition

The canonical task row now records `owner: Claude` and `reviewer: Antigravity`.
The earlier `Codex` / `Codex2` and `Antigravity` / `Codex2` pairs are historical
and are retained in `evidence.json` as immutable review history. This manifest
is bound to the current pair; the prior `changes_requested` decision on
`3e525480706ee0eb1cb5aadf583cb2a6c9670d1c` is preserved but is **not** an
approval of the current head.

Antigravity has made no decision on any head of this task and must make a fresh,
fully independent one.

## Delivered boundary

The task adds only the relational projection schema, the typed PostgreSQL store,
transaction invariants, real-PostgreSQL tests, and task evidence. It does not
change `telemetry_events`, the reducer, BFF routes, Compose, deployment, or
cutover. No consumer has been enabled.

Against the recorded base the branch is strictly additive: seven added task
artifacts totalling `3013` insertions, `0` deletions, and no modified or deleted
path. The full commit adds one more file, the worker task-brief mirror, for
`3030` insertions across eight added paths.

## Branch rewrite (auditable)

The pre-rewrite head `9f6e2b8a44781bf4a7b3108daa436af2a04e968c` could never turn
PR #4557 green. The required `Commit trailers` check runs
`check_commit_trailers.py` over the whole `<merge-base>..<head>` range, and two
commits in that range carried 86-character subjects:

- `8b20e5df2e8f9d534855ee1fab5587f5a4f1d812` — subject exceeds 72 chars (86)
- `85b8c8f75bf5d107318c79216d9d30fb5f874da0` — subject exceeds 72 chars (86)

(run `30927197622`, job `92052453617`, both `FAILURE` at 2026-08-04T16:02:51Z).
No follow-up commit can clear a range check, so the branch was rebuilt.
`docs/conventions/GIT_WORKFLOW.md` §7.2 lists force push as allowed on `task/*`,
and no reviewer had approved any head, so no exact-head approval binding and no
`refs/tags/pantheon-review/approve/*` proof was orphaned by the rewrite.

The rebuild took current `dev` and restored exactly the seven declared task
artifacts from the pre-rewrite head. Every restored file is byte-identical to the
pre-rewrite head except `evidence.json`, `evidence.md`, and `checksums.txt`,
which are re-cut here. `projection_store.py`,
`001_create_trade_journey_projection_schema.sql`,
`test_projection_store.py`, and `explain-plans.txt` still match the checksums
recorded at the pre-rewrite head.

## Prior rejected-head blocker repair (retained)

The repair changes receipt handling from a pre-read followed by unchecked late
insert into an atomic global claim before any derived mutation. If two distinct
controller locks both pre-read the same absent event, PostgreSQL admits one
claim; the loser detects that it lost the claim, raises a dedicated failure, and
rolls back its controller and every derived row.

For a mixed batch, only identity, journey, stage, loop, and quarantine rows owned
by receipts newly claimed in the current transaction are applied. Already-durable
duplicate-owned rows are ignored. If a new and a duplicate receipt share an
aggregate ownership key, the transaction fails closed and requires a new-only
retry instead of guessing at provenance.

## Exact verification commands

Re-run by the current owner on the rebuilt tree, against a real PostgreSQL 16
server. The local connection values are supplied through environment variables
and are intentionally not copied into repository evidence.

```bash
TEST_DATABASE_URL="$LOCAL_TEST_DATABASE_URL" \
TEST_DATABASE_ADMIN_URL="$LOCAL_TEST_DATABASE_ADMIN_URL" \
PYTHONPATH=. python3 -m pytest -q \
  services/trade_journey/test_projection_store.py

TEST_DATABASE_URL="$LOCAL_TEST_DATABASE_URL" \
TEST_DATABASE_ADMIN_URL="$LOCAL_TEST_DATABASE_ADMIN_URL" \
PYTHONPATH=. python3 -m pytest -q services/trade_journey

TEST_DATABASE_URL="$LOCAL_TEST_DATABASE_URL" \
TEST_DATABASE_ADMIN_URL="$LOCAL_TEST_DATABASE_ADMIN_URL" \
PYTHONPATH=. python3 -m pytest -q \
  services/trade_journey/test_projection_store.py \
  -k 'runtime_role_has_dml_without_ddl or migration_applied_twice_and_prior_reader_compat or indexed_explain_paths'

python3 -m py_compile \
  services/trade_journey/projection_store.py \
  services/trade_journey/test_projection_store.py

git diff --check

sha256sum -c \
  docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-STORE-001/checksums.txt
```

Results observed on the rebuilt tree:

- focused real-PostgreSQL suite: `19 passed in 11.20s`;
- adjacent Trade Journey regression: `138 passed in 47.50s`;
- dedicated migration-twice, DML-only, and EXPLAIN slice:
  `3 passed, 16 deselected in 1.33s`;
- Python compilation and whitespace validation: exit 0;
- PostgreSQL server: `PostgreSQL 16.14 on x86_64-pc-linux-musl`;
- checksum verification of the four non-regenerated artifacts against the
  pre-rewrite manifest: `OK`;
- least privilege: a temporary DML-only login processed a receipt and
  checkpoint, while schema bootstrap failed with `InsufficientPrivilege`;
- migration: the exact SQL file applied twice to an isolated schema;
- indexed reads: the focused regression migrated an isolated schema twice,
  seeded and `ANALYZE`d 5,000 rows per relation, and verified all five intended
  indexes recorded in `explain-plans.txt`;
- mixed duplicate/new regression: the old journey, stage, quarantine, and their
  row revisions/timestamps remained unchanged while the new receipt, journey,
  stage, and controller revision committed;
- overlapping-controller regression: two real connections synchronized after the
  receipt pre-read produced one success and one concurrent-claim rollback,
  leaving one receipt, one journey, and one controller row.

The interpreter used was the shared Pantheon virtualenv
(`/home/lupin/pantheon/.venv/bin/python`, `psycopg` 3.3.4), not a
checkout-scoped `.venv-pantheon`; `scripts/dev/provision_python_distribution.py`
was not re-run for this cut and no result is claimed for it. `ruff` is not
available in this environment, so no ruff result is claimed. Repository CI
remains responsible for its configured static gates.

## Known external blocker: the canonical review gate cannot run

`dev` no longer contains `.github/workflows/canonical-review-gate.yml`,
`scripts/git/canonical_review_gate_ci.py`, or
`scripts/git/test_canonical_review_gate_workflow.py`. All three were deleted by
`23ae23c2185d31d2aeacafaa9b051127a6d53136`
("SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729: anchor owner handoff", PR
#4590), a stale-base squash that removed 166 files and 47,932 lines from `dev`,
including this task's sibling evidence directories
`LIFECYCLE-PROJ-INCIDENT-20260801` and `LIFECYCLE-PROJ-HOTFIX-REVIEW-20260801`.
The file is still present on `master`.

Consequence for this task: the required `Pantheon canonical review gate` status
context cannot be produced by a `pull_request` run on a branch based on current
`dev`, so PR #4557 stays `BLOCKED` on that context regardless of this task's
correctness. This is a fleet-wide condition, not a defect in this delivery, and
restoring those files is out of this task's declared scope. It is reported to
Human/Ops separately.

## Review checklist

Antigravity must independently reproduce or inspect on PR #4557's exact final
head:

1. a mixed batch containing a durable exact duplicate plus a new event leaves the
   old journey, stage, quarantine, and row revision/timestamp truth unchanged
   while committing only the new event's derived rows;
2. two distinct controller locks synchronized after the receipt pre-read yield
   one atomic derived commit and one concurrent-claim rollback;
3. exact duplicate input carrying mutated stage and journey rows leaves both rows
   unchanged and does not increment the revision;
4. receiptless row mutations fail closed, receiptless checkpoint claims do not
   advance, and filling sequence 4 crosses a previously durable sequence 5;
5. rollback leaves no receipt/controller and a corrected retry commits once;
6. backfill, recovery, and replay force `accepted_live=false`, retain the prior
   live timestamp, and update only their owned timestamp;
7. quarantine retry/count truth remains idempotent and controller-scoped;
8. lock IDs match across distinct `PYTHONHASHSEED` processes, same-controller
   contention fails immediately, and another controller remains ready;
9. the canonical identifier registry and first/last identity bounds are enforced
   by PostgreSQL;
10. the exact migration file applies twice, the prior controller query remains
    valid, the runtime role has DML without DDL, and all five `EXPLAIN` plans use
    the intended index names;
11. the branch rewrite restored only the seven declared artifacts and reverted no
    `dev` content — verify with
    `git diff --stat origin/dev...<head>` (additive only) and by re-running
    `sha256sum -c` against `checksums.txt`;
12. the canonical approval binds `REVIEW_PR=4557` plus the exact 40-hex final
    head. The eventual protected merge SHA belongs in governed delivery metadata
    because the reviewed manifest cannot contain its own future merge commit.

## Rollout and rollback

Rollout is additive schema application only, with no worker or BFF reader
enabled. Rollback stops/does not start consumers and leaves the unused schema
intact. A destructive down migration is prohibited.
