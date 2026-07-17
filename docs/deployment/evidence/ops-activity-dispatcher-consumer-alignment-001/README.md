# OPS-ACTIVITY-DISPATCHER-CONSUMER-ALIGNMENT-001 evidence

Status: blocked validation snapshot
Date: 2026-07-17
Branch: `task/OPS-ACTIVITY-DISPATCHER-CONSUMER-ALIGNMENT-001`
Composed dev commit: `a06bc0c7a30ee743577209c6e74562c86d8efd3f`
Composition commit: `3a2791627`

## Scope proved

The dispatcher consumes `stream_logical_activity()` instead of maintaining a
private disjoint-source activity index. Isolated fixtures prove that a valid
1,000-row legacy fold is accepted and idempotent, while a same-ID payload
mismatch remains fail closed. The dispatcher continues to validate the
content binding of every `loop-product-event-*` row exposed by the shared
reader.

No central task state or activity source was modified during this validation.
The shared reader, activity rotation, capability manifest, and writer registry
remain owned by their prerequisite tasks.

## Validation at the composed head

- Focused dispatcher contract matrix: 4 passed; 2 failed. The two failures are
  the active and gzip duplicate-object-key regressions described below.
- Full dispatcher suite: 195 passed, 2 failed in 710.86 seconds. No failure
  other than the same two duplicate-object-key regressions was observed.
- Shared logical-reader suite: 64 passed.
- Shared status suite: 73 passed, 1 latest-`dev` baseline failure in
  `CanonicalTaskStateAndActivityRecoveryTests.test_existing_archive_conflict_or_legacy_shape_preserves_active_task`.
  The task branch and `origin/dev` versions of `.orchestrator/common.py`,
  `scripts/ai_status.py`, and `scripts/test_ai_status.py` were identical for
  this run.
- `python3 -m py_compile` for the dispatcher and its direct test: passed.
- `git diff --check origin/dev...HEAD`: passed.

The focused matrix covered:

- legitimate synthetic legacy fold acceptance and idempotency;
- payload mismatch after the same fold;
- validation-complete draining when the shared generator fails after a yield;
- normalization of a shared-reader SQLite failure;
- duplicate JSON object keys in active and gzip sources.

## Open dependency failures

The two duplicate-key regressions intentionally remain red because the shared
reader at `origin/dev` still parses activity rows with permissive
`json.loads`. Both fixtures returned success and would write state. Recreating
strict parsing inside the dispatcher would restore the private, divergent
consumer contract this task removes. PR #3800
(`OPS-ACTIVITY-READER-HARDENING-001`) owns the shared strict parser, but it is
still a conflicting draft and has not merged.

The canonical task prerequisite is also not satisfied. PR #3797
(`OPS-ACTIVITY-ROTATION-OVERLAP-PREVENTION-001`) remains open, behind `dev`,
and under explicit requested changes. Its head is not an ancestor of
`origin/dev`.

The optional central-history dry-run was attempted with `AI_NAME=Codex` and
the central status root. It exited 2 before opening the logical activity
history because the installed runtime-lock capability rejected the current
`.orchestrator/adapters/file_inbox.py` writer digest. The command was
read-only and made zero writes. A reviewed capability re-freeze/sign/install
is therefore required before the changed dispatcher can become an
authoritative writer and before the optional central-history proof can run.

## Required composition order

1. Resolve, approve, and merge PR #3797 into `dev`.
2. Compose and approve the shared reader hardening in PR #3800, including
   recursive duplicate-key rejection and capability/writer evidence.
3. Merge the resulting `dev` into this branch and rerun the full dispatcher,
   shared-reader, status, syntax, range, and optional read-only integration
   checks.
4. Obtain independent exact-head review before making this PR ready or
   merging it.
