# Task Brief: OPS-L12-DEP-EVIDENCE-REPLAY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Remove stale duplicate L12-DEP replay source
- Status: todo
- Owner: Claude
- Reviewer: Antigravity
- Next: Implement only the stale replay-source cleanup. Preserve docs/deployment/evidence/twelve-loop-gap/L12-DEP-001/closeout/evidence.json and its checksum exactly. Prove the global evidence-root replay has one accepted L12-DEP source and zero failing L12-DEP sources; validate the archived task review_file still resolves and passes. Open a task PR to dev with green checks and Antigravity review. Do not relabel missing hosted activation; L12-MANIFEST-001 and L12-HOSTED-001 still own runtime wiring and hosted proof.

## Summary
移除或正式歸檔 L12-DEP-001 上層的舊 schema-invalid evidence replay source，保留已通過且已綁定 archive review_file 的 closeout manifest，讓全域 closeout replay 不再被重複舊證據阻塞。

## Delivered change

`scripts/loop_done_guardrail.py --evidence-root` discovers replay sources with
`rglob("evidence.json")`, so the L12-DEP-001 packet exposed two of them. Only
`closeout/evidence.json` is the closeout manifest and the archived `review_file`;
the top-level file was the pre-PR reviewed dispatcher receipt, whose
`overall_admission` of `review_approved_for_task_pr` made it a permanently
failing duplicate replay source.

The cleanup deletes the stale source, edits nothing inside either manifest, and
touches only paths the immutable `artifact_conflict_guard` already declares:

- delete `docs/deployment/evidence/twelve-loop-gap/L12-DEP-001/evidence.json`
- delete `docs/deployment/evidence/twelve-loop-gap/L12-DEP-001/evidence.sha256`
- update `docs/deployment/evidence/twelve-loop-gap/L12-DEP-001/README.md` with a
  `Replay-source layout` section

`closeout/evidence.json` and `closeout/evidence.sha256` are untouched — they do
not appear in `git status` at any point in this task — and their digest still
verifies. No new `evidence.json` is added anywhere, so the global replay source
set only shrinks. Hosted activation labels are untouched: `L12-MANIFEST-001`
still owns runtime wiring and `L12-HOSTED-001` still owns hosted proof.

The deleted receipt stays auditable in merged `dev` history at
`22e9e319ef340b2822d7382ad49890ca09207110`
(`git show <sha>:docs/.../L12-DEP-001/evidence.json | sha256sum` reproduces
`6405c222a4ba405a11c9b1a09de9c2b006f831c94ad8495b4d0402b8a146f263`, the digest
the closeout manifest records as
`integrity.source_artifact_sha256_by_epoch.reviewed_dispatcher_receipt`; that
commit is an ancestor of `origin/dev`). The README records that the immutable
closeout manifest still cites the removed paths and must be resolved against
merged history.

### Scope correction

The first commit on this branch (`943d3d899`) renamed the two files to
`reviewed-dispatcher-receipt.json` / `.sha256` instead of deleting them. An
independent scope audit rejected that shape: those are new paths outside the
task's declared `artifact_conflict_guard.artifact_scope`. The follow-up commit
deletes them, leaving the branch's net diff against `dev` as two deletions plus
the owned README edit. Nothing in this correction touches closeout evidence, and
all validation below is re-run on the corrected shape.

## Validation

All numbers below are re-cut at the merged head, after `origin/dev`
(`51c1e9fc3`) was merged into this branch. That merge brought in a
`scripts/loop_done_guardrail.py` change and two re-cut sibling packets, so the
baseline was re-taken in a detached worktree at `origin/dev` rather than reused
from the earlier run.

```text
python3 scripts/loop_done_guardrail.py --evidence-root docs/deployment/evidence
before (origin/dev 51c1e9fc3): {'passed': 14, 'failed': 18, 'scanned': 32}
after  (task head):            {'passed': 14, 'failed': 17, 'scanned': 31}

audit result-set delta over the full record
(task_id, manifest, owner, reviewer, overall_admission, result, gap_count, gaps)
only-before: 1  docs/deployment/evidence/twelve-loop-gap/L12-DEP-001/evidence.json
              ["product evidence schema validation failed: 'deployment' is a
               required property",
               'evidence manifest review_file path mismatch: expected
                docs/.../L12-DEP-001/evidence.json, got None',
               'missing terminal readback evidence in hosted_readback',
               'missing security evidence: mfa status is not pass/not_applicable',
               'missing reviewer verdict: no approved formal reviewer verdict
                recorded in record_log']
only-after : 0
excluded_manifests: identical
```

L12-DEP-001 now resolves to exactly one replay source and it passes:

```text
[OK] L12-DEP-001 (review_approved)
  docs/deployment/evidence/twelve-loop-gap/L12-DEP-001/closeout/evidence.json
```

The 17 remaining failures are pre-existing sources owned by other tasks. Their
audit records — including the exact gap text — are identical before and after,
no passing source changed, and the excluded-manifest list is unchanged.

Archived `review_file` replay of the canonical `L12-DEP-001` snapshot
(`$PANTHEON_STATUS_ROOT/ai-task-archive/tasks/L12-DEP-001.json`) through
`loop_done_guardrail._task_from_archive_snapshot` + `check_task`:

```text
review_file: docs/deployment/evidence/twelve-loop-gap/L12-DEP-001/closeout/evidence.json
resolved:    <worktree>/docs/deployment/evidence/twelve-loop-gap/L12-DEP-001/closeout/evidence.json
gap_count:   0
classification: valid_closure
```

Checksums and focused regressions:

```text
sha256sum -c closeout/evidence.sha256   # evidence.json: OK

git show 22e9e319ef340b2822d7382ad49890ca09207110:\
docs/deployment/evidence/twelve-loop-gap/L12-DEP-001/evidence.json | sha256sum
6405c222a4ba405a11c9b1a09de9c2b006f831c94ad8495b4d0402b8a146f263  -
git merge-base --is-ancestor 22e9e319e origin/dev   # exit 0

/home/lupin/pantheon/.venv/bin/pytest -q \
  scripts/test_loop_done_guardrail.py scripts/test_ai_status.py
213 passed, 31 subtests passed in 34.00s

git diff --check HEAD                   # exit 0
```

After the correction the L12-DEP-001 packet contains exactly three files:
`README.md`, `closeout/evidence.json`, and `closeout/evidence.sha256`.

`loop_done_guardrail.requires_protected_closeout_verdict` and
`is_loop_autopilot_task` are both false for this task id, so its own `done`
transition needs no product-level `evidence.json`; adding one would have
reintroduced a replay source under the audited root.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
