# OPS-ACTIVITY-ROTATION-PENDING-INTENT-RECOVERY-001 — live execution evidence

Date: 2026-07-17 UTC

Environment: Pantheon dev only

Outcome: recovery resolved successfully with exactly one execute invocation

This is a redacted control-plane record. Full command outputs and manifests
remain under `/tmp/oparpir-001/` on the operator host. No production system,
secret, process environment, or raw activity payload is included here.

Closeout status: this document is a factual execution record, not lifecycle
approval. It originated at PR #3788 head
`02861c351fcd6873f60f2c4340c114ad7f296256`, whose GitHub review state was
`COMMENTED` despite approval text. The durable redacted file and inventory
digests are in `post-execution-artifact-digests-20260718.json`. A new
exact-head Antigravity review and governed approval remain required after the
current-dev evidence composition.

## Reviewed identities and pre-gate

- PR: `#3786`
- Approved content head: `59c1376b02867da85691838ca061bed218fbd093`
- Head tree: `488a25063ad422580eab99260d593aabf5614ebb`, byte-identical to approved
  `06051586ef68260374ebe275a950d99a95378b54`
- Starting `origin/dev`: `e2f362846d7477e188b59c7814e93c8835a900bc`
- Exact-head Antigravity approval:
  `https://github.com/ajoe734/pantheon/pull/3786#issuecomment-4998077745`
- GitHub gate: six required checks passed, `mergeStateStatus=CLEAN`,
  `mergeable=MERGEABLE`, and `autoMergeRequest=null`
- The previously reviewed 401 implementation tests were not rerun, as directed.

The live pre-state was captured read-only before guarding. The central checkout
was already dirty and was not used for code, install, or manual data changes.

## PR #3782 composition reconciliation

- PR #3782 exact head:
  `577af8f9cd1cbf95256fb47f7aa2dca8f4738e6b`.
- Composition commit on the recovery branch:
  `6cbb6de539222a9805788b1cb76e628e7aade0c1`.
- PR #3786 approved head `59c1376b...` contains that composition and merged to
  `dev` as `b122d005...`; GitHub records PR #3782 merged at
  `2026-07-17T01:37:32Z`.
- The pre-review `README.md` maps the remaining #3782 planner findings to the
  combined recovery tests and guard contract. The recovery branch did not
  introduce a second schema-v2 rotation implementation.

## All-writer guard

The runbook's byte-exact cron transformation was used. The original crontab was
5,617 bytes with SHA-256
`1a757c543e6e74c21b0e34af01b1158bacb15225c8f7570d169960db68351ec8`.

```bash
crontab -l > /tmp/oparpir-001/guard/crontab-before.txt
crontab /tmp/oparpir-001/guard/crontab-guarded.txt
crontab -l > /tmp/oparpir-001/guard/crontab-guard-readback.txt
```

Guard assertions all passed:

- active watchdog cron lines: `1 -> 0`
- guard markers: `1`
- `<`/`>` diff rows: `2` (one intended line only)
- guarded/readback SHA-256:
  `77da22bd5edc0c4489adab750dc767d12eb2fa0f541f048c2cd7a05ad3b01c12`
- guarded readback was byte-identical to the generated guard

Supervisor PID `306942` was validated by pidfile and exact argv, then sent
`TERM` through the normal procedure at `2026-07-17T01:36:10Z`. Exact-argv
writer inventories were empty at quiescence and immediately before execute;
the activity and task locks had no writer-class holders. Broad substring hits
were operator prompt text, not writer argv, and were not killed.

The successful guard window ran from `2026-07-17T01:35:43Z` through
`2026-07-17T01:47:53Z` (730 seconds). Execute began 342 seconds after guard
activation, within both runbook limits.

## Merge and exact dev-root install

The guarded merge used manual merge mode only:

```bash
gh pr merge 3786 -R ajoe734/pantheon --merge \
  --match-head-commit 59c1376b02867da85691838ca061bed218fbd093
```

Result:

- merge SHA: `b122d005bbee3884c77ce6dbe5f225b8f3fe6c1c`
- parents, in order: `e2f362846d7477e188b59c7814e93c8835a900bc` and
  `59c1376b02867da85691838ca061bed218fbd093`
- merged at `2026-07-17T01:37:31Z`
- PR state `MERGED`; auto-merge remained off

The documented install was then run:

```bash
SYNC_REF=origin/dev \
  bash /home/lupin/pantheon-ci-deploy/dev-root/scripts/sync-dev-root.sh \
  /home/lupin/pantheon-ci-deploy/dev-root \
  /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json
```

`dev-root` moved from `e2f362846d7477e188b59c7814e93c8835a900bc` to exact
`b122d005bbee3884c77ce6dbe5f225b8f3fe6c1c`. The installed recovery tool,
`.orchestrator/common.py`, and runbook blobs matched that commit exactly. At
the install snapshot there was no tracked diff; all three pre-existing
untracked task briefs had byte-identical path lists and hashes before/after.

## Fresh pin and dry-run

Commands were run from the installed merge SHA:

```bash
python3 .orchestrator/activity_pending_intent_recovery.py inventory \
  --status-root /home/lupin/code/pantheon \
  --output /tmp/oparpir-001/guard/pin.json
python3 .orchestrator/activity_pending_intent_recovery.py dry-run \
  --status-root /home/lupin/code/pantheon \
  --inventory /tmp/oparpir-001/guard/pin.json \
  --output /tmp/oparpir-001/guard/dry-run.json
```

The fresh pin was captured at `2026-07-17T01:39:27Z` with digest
`36113cc1bda9403a609010f88e5bff384f3259060f6d1d03e58af037d9156d4c`
and 423 archive sources. Dry-run returned:

- `status=resolvable`, `mutation_performed=false`
- incident class `schema-v1-pending-intent-superseded`
- intent present, not already resolved, installed archive equal to staged
- active append since pin `0`
- missing events `0`, duplicate events `0`
- logical events `3063/3063`

Immutable incident-shape fields matched the committed reviewed manifest. Only
the allowed append-derived active and post-rotation fields differed from the
older reviewed snapshot. The dry-run's informational fresh-manifest digest
included new capture metadata; the execute gate used the explicit pinned
digest above.

## Single gated execute

After re-reading the guarded cron, exact writer argv, and both locks, exactly
one execute command was issued. There was no retry:

```bash
PANTHEON_ACTIVITY_PENDING_INTENT_RECOVERY_EXECUTE=I-UNDERSTAND-LIVE-MUTATION \
python3 .orchestrator/activity_pending_intent_recovery.py execute \
  --status-root /home/lupin/code/pantheon \
  --inventory /tmp/oparpir-001/guard/pin.json \
  --expected-inventory-sha256 36113cc1bda9403a609010f88e5bff384f3259060f6d1d03e58af037d9156d4c \
  --writer-guard-attestation "Codex fleet: supervisor stopped, respawn cron paused, worker chains drained, readback empty at 2026-07-17T01:41:25Z" \
  --output /tmp/oparpir-001/guard/execute-report.json
```

Execute ran from `2026-07-17T01:41:25Z` to `01:42:04Z` and returned
`status=resolved`, `mutation_performed=true`, sequence `1`.

- transaction:
  `activity-rotation-06bb785a56a33c4936a98df80b6aebbe2f8686ee35c052a10b59de39c7910188`
- resolution:
  `activity-intent-resolution-e5ed8528f6c7672db4a9a5b9b2e5a04a6673ad7eb011570d2ecb7eda19b43427`
- active SHA-256 remained
  `d2ef0c413ba9cdbf1ccf16c6d214f381ef5091be19b2e126063e321fb2263841`

Only the shipped tool changed central recovery metadata: it created a
hash-verified preserved transaction directory, appended one resolution row,
and removed the pending intent plus staged archive/tail after preservation.
It did not rewrite, rename, truncate, recompress, or delete the active log or
any central archive. Preserved copies matched the pinned originals.

## Post-execute conservation and reader validation

Guarded validation found the intent absent and the exact transaction
registered. The active bytes and all 423 archive path/hash entries were
unchanged from the pin. The stable reader reported 423 sources.

The central logical inventory reported:

- 1,404,007 logical entries
- logical event IDs `647/647`
- mismatch folds `0`
- payload-mismatch duplicate lines `0`
- within-source duplicates `0`
- 214 duplicate-ID physical lines, all byte-identical reviewed legacy overlap
  folded to unique logical IDs

After writers resumed, a fresh inventory at `2026-07-17T02:09:51Z` still
showed the intent absent, the same one-row resolution, and an archive listing
byte-identical to the pin. Active-log growth after resume was expected.

## Contract deviations requiring explicit acceptance

The execution followed the reviewed superseded-by-legacy implementation, but
two differences from the incident plan remain governance decisions rather
than facts this evidence can self-approve:

1. The plan and original task brief required the recovery transaction itself
   to publish ordered lineage and an active lineage-head control record. The
   executed path instead published one hash-bound resolution row and left
   lineage empty until the first later schema-v2 rotation. This avoided
   enumerating the already-superseded content archive, but it is a deliberate
   contract deviation that requires planner and reviewer acceptance or a
   returned code change.
2. The plan literally prohibited deleting the pending intent and staged
   leaves. Execute first wrote byte-verified preserved copies under the
   transaction's resolved directory, then unlinked the original pending and
   staged paths so governed writers could resume. The original bytes remain
   preserved, while the original paths do not. Planner and reviewer must
   explicitly accept or reject that preserve-then-unlink interpretation.

Neither deviation authorizes another live execute. The completed resolution
must remain single-invocation unless a separate incident plan says otherwise.

## Restore and governed smoke

```bash
crontab /tmp/oparpir-001/guard/crontab-before.txt
unset PANTHEON_ACTIVITY_ROTATION_PAUSE
env PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon \
  ./scripts/ai-status.sh show PTJ-007
```

The crontab restore was byte-identical to the original SHA-256, with one
active watchdog line and zero guard markers. The supervisor respawned
naturally as PID `405306`; a post-restore health sample at
`2026-07-17T01:49:03Z` was fully healthy with no loop error.

At execution time the recovery task ID was not retained as a governed task
snapshot, so three governed `show`-class attempts against that ID ended in two
bounded timeouts and an `Unknown task` result. The task was materialized again
on 2026-07-18; this sentence records only the 2026-07-17 smoke context. The
durable archived smoke target `PTJ-007` then
completed successfully with exit `0`, source `archive`, terminal status
`done`, and empty stderr. It ran under the repository's normal governed
outbox-recovery and locking semantics; no activity file was hand-edited.

The governed scans temporarily delayed heartbeat/watchdog freshness while
unrelated queued work held the shared audit lock; no loop error appeared and
those processes were not disturbed. After the queue drained naturally, the
final sample at `2026-07-17T02:29:15Z` was fully healthy: PID `405306` alive
and running, heartbeat age 30 seconds, watchdog age 14 seconds, no loop error,
and zero failed checks.

## Mutation ledger and safe-abort disclosure

Authorized mutations were limited to:

1. merging PR `#3786` to dev;
2. syncing dev-root to the exact merge while preserving unrelated untracked
   briefs;
3. temporarily changing one cron line, then restoring the original bytes;
4. normally terminating and naturally respawning the supervisor;
5. the shipped tool's preservation, one resolution row, and removal of the
   resolved pending/staged markers;
6. redacted Phase-B evidence under `/tmp/oparpir-001/guard/`; and
7. this separate evidence-only branch/document.

Before the successful window, one setup attempt exited during read-only state
capture and made no live/control-plane mutation (it retained `/tmp` evidence).
A later local PR-check parser attempt installed the exact guard and normally
terminated supervisor PID `276514`; its trap restored the crontab byte-for-byte
before any merge or activity mutation, and the supervisor naturally respawned.
These safe aborts did not execute recovery.

After successful restoration, unrelated live workers generated task-brief
churn in dev-root. Those files were not reset, staged, or included here. The
recovery tool/common/runbook blobs continued to match the installed merge SHA.

No central activity, archive, pending, staged, resolution, or preserved file
was hand-edited at any point.

## 2026-07-18 current-dev closeout audit

The owner recomposed the evidence onto `origin/dev` at `c9560db5...` without
changing recovery code or live state. Repo-external isolated verification on
that code passed:

- `.orchestrator/test_activity_pending_intent_recovery.py`: 37 tests;
- `.orchestrator/test_common.py`: 90 tests; and
- `scripts.test_activity_audit_logical_inventory`: 25 tests, 1 opt-in skip.

The 423-source `missing=0` / `duplicate=0` result above remains exact evidence
for the guarded recovery window, not a claim about all later appends and
rotations. A fresh central read-only diagnostic inventory at command-runtime
SHA `c5592c1068...` failed closed after scanning the later source set with:

```text
Incident lineage broken at ai-activity-log.jsonl-2026-07-17T0404Z.gz
```

The follow-up hash inventory proved that the stop occurs at an unregistered
legacy-to-legacy disjoint transition, `0404Z -> 1754Z`. There is no exact
999/1,000/1,001-line overlap between those two leaves. The first schema-v2
lineage row separately authenticates `1754Z` as its boundary predecessor and
proves the later `1754Z -> first content archive` conservation. It does not
authenticate the earlier legacy gap. Filename order, mtime, and event
timestamps cannot supply the missing authority.

Draft PR #3820's generic "validated disjoint epoch" reporting change is not a
safe resolution for this task: the shared reader can concatenate disjoint
legacy sources in filename order without proving that no byte/event span was
lost between them. No fresh product-level missing/duplicate claim is made
without a pair-specific durable boundary record or planner data-reconciliation
decision. Exact identities and a hermetic reproduction are in
`current-closeout-stop-evidence-20260718.md`.

The supervisor later refreshed the command binding: both
`PANTHEON_COMMAND_RUNTIME_SHA` and command-root HEAD matched `c9560db5...`.
A governed `progress` completed, but a subsequent read-only `show` returned a
bounded `status_recovery_pending` diagnostic for an unrelated supervisor
reassignment outbox. No retry or manual outbox edit was attempted. This proves
the command no longer hangs; it does not prove the status lane fully healthy.
