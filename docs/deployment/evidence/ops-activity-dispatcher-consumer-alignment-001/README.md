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

Exact commands:

```bash
PYTHONPATH=.orchestrator:scripts PYTHONDONTWRITEBYTECODE=1 \
  python3 -m pytest -q -p no:cacheprovider \
  scripts/test_dispatch_loop_product_level_remediation_2026_07_13.py::test_dispatch_accepts_shared_legacy_fold_and_remains_idempotent \
  scripts/test_dispatch_loop_product_level_remediation_2026_07_13.py::test_dispatch_rejects_payload_mismatch_after_shared_legacy_fold \
  scripts/test_dispatch_loop_product_level_remediation_2026_07_13.py::test_activity_event_index_fully_drains_shared_reader \
  scripts/test_dispatch_loop_product_level_remediation_2026_07_13.py::test_activity_event_index_normalizes_shared_reader_database_failure \
  scripts/test_dispatch_loop_product_level_remediation_2026_07_13.py::test_dispatch_rejects_duplicate_activity_json_keys_without_writes

PYTHONPATH=.orchestrator:scripts PYTHONDONTWRITEBYTECODE=1 \
  python3 -m pytest -q -p no:cacheprovider \
  scripts/test_dispatch_loop_product_level_remediation_2026_07_13.py

PYTHONDONTWRITEBYTECODE=1 python3 .orchestrator/test_common.py

env -u ORCH_RUN_ID -u PANTHEON_WORKTREE_ROOT -u ORCH_WORKSPACE_PATH \
  -u ORCH_RUNNER_STATUS_PATH -u ORCH_HEARTBEAT_PATH \
  PANTHEON_STATUS_ROOT=/tmp/pantheon-ops-activity-dispatcher-consumer-alignment-validation \
  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts.test_ai_status

PYTHONPYCACHEPREFIX=/tmp/pantheon-ops-activity-dispatcher-consumer-alignment-pycache \
  PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile \
  scripts/dispatch_loop_product_level_remediation_2026-07-13.py \
  scripts/test_dispatch_loop_product_level_remediation_2026_07_13.py

git diff --check origin/dev...HEAD

AI_NAME=Codex PYTHONDONTWRITEBYTECODE=1 \
  python3 scripts/dispatch_loop_product_level_remediation_2026-07-13.py \
  --validate-only

AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon \
  PYTHONDONTWRITEBYTECODE=1 \
  python3 scripts/dispatch_loop_product_level_remediation_2026-07-13.py \
  --dry-run
```

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

## Reviewer assignment note

The static task brief predates a supervisor reassignment and names Codex2.
The canonical central task state and the current `owned_ready_dispatch` name
Claude as reviewer, so the current anchors use `Reviewer: Claude`. Re-read the
central task entry before final handoff and closeout; if the assignment changes
again, the final task commit and review request must follow that canonical
assignment rather than this historical note.
