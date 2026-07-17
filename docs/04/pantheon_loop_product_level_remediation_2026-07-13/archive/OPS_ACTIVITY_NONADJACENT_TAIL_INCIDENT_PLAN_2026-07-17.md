# Activity logical-reader non-adjacent-tail incident plan — 2026-07-17

## Status and owner

This is an additive incident plan under the archived activity-rotation
follow-up plan. It does not replace that plan or authorize a live repair.
Implementation, tests, evidence, and any guarded execution remain fleet-owned.

The incident composes with the existing P0 execution task
`OPS-ACTIVITY-ROTATION-OVERLAP-PREVENTION-001`; no competing recovery task or
second rotation contract may be created for this symptom.

## Observed gap

On 2026-07-17, a read-only `scripts/ai_status.py show` probe against the
current activity history failed to return and reported:

```text
RuntimeError: Matching non-adjacent older tail detected:
  archive/logs/ai-activity-log.jsonl-2026-07-16T1609Z.gz ->
  archive/logs/ai-activity-log.jsonl-b320711ea85d1a0bfd537f39a0c934b4b865ce0805ff389df0405a3a89d5d004.gz
```

The error is distinct from the earlier duplicate-event and pending-intent
recovery incidents. The current reader reaches the error through activity
outbox recovery, and the caller can remain blocked long enough to prevent
ordinary status/show, worker closeout, and review coordination. A merged
pending-intent recovery PR is therefore not evidence that this path is safe.

This observation is diagnostic only. It does not establish which on-disk copy
is authoritative and must not be repaired by deleting, renaming, truncating,
recompressing, or manually rewriting any archive or active log.

## Required investigation

Before changing code or live state, the fleet must capture a fresh read-only,
hash-bound inventory of every legacy archive, content-addressed archive,
lineage/intent/control record, active log, inode, byte/line count, gzip payload,
and writer process. It must prove whether the reported pair is an impossible
source-order graph, a duplicate representation of one source, a missing
lineage edge, or an ambiguous/tampered input. Filename order, mtime, and event
timestamps are not ordering authority.

The inventory and all fixtures must be repo-external. No premerge command may
open or lock the central activity/status root.

## Required implementation contract

The P0 task must add a hermetic fixture for this exact class and satisfy all of
the following:

1. Source enumeration follows the approved lineage contract and never sorts or
   silently skips a non-adjacent source.
2. An ambiguous, missing, stale, forked, or content-mismatched edge fails
   closed with a bounded, structured diagnostic identifying the invariant and
   the evidence digest. It must not hang the caller or enter an unbounded
   recovery loop.
3. A uniquely provable, already-approved recovery path is idempotent and
   preserves every logical event exactly once; no inferred repair is accepted
   from the error text alone.
4. Shared-lock readers never publish a partial logical result. Exclusive
   recovery remains separately gated by exact inventory digest, all-writer
   guard, and explicit execute mode.
5. `ai_status.py`, supervisor/common consumers, and the dispatcher consume the
   same diagnostic/lineage contract without private ordering rules.

## Verification gates

- exact non-adjacent-tail fixture reproduces the prior failure and returns
  within a documented deadline;
- adjacent valid lineage succeeds, while missing edge, fork, duplicate
  sequence, content mismatch, symlink, changed inode, and append-during-read
  cases fail closed;
- the earlier 999-line boundary exception still works once and only once;
- crash/retry at inventory, lineage, active-control, and intent-resolution
  points is idempotent;
- full activity, status, runtime, supervisor, watchdog, and worker-runner
  suites pass from isolated roots;
- evidence records exact final head, test counts, digests, and no central-root
  mutation; an optional central read-only probe is clearly separated;
- no live rotation or repair occurs until the planner accepts the all-writer
  guard and the independent reviewer approves the exact head.

## Exit condition

This incident is closed only when the P0 implementation is merged and
post-merge installed, the exact fixture and bounded-show proof are archived,
the activity reader has no open P0 finding, and the original pending-intent
and rotation plans can point to one authoritative lineage contract.
