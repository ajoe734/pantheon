# One commit identity contract for CI and canonical closeout

Status: active operating rule
Task: OPS-COMMIT-IDENTITY-001
Plan source: `sha256:2e57e428ca20e367964f61fb152ac39b988c413bc92b7de267f70ff36aa7ff51`
(`pantheon-dev@46bbfe935df1e68740569fb5dc6233897de98b0b`, `COMMIT_IDENTITY_PLAN.md`)

## The contradiction this closes

On dev `46bbfe935df1e68740569fb5dc6233897de98b0b`, three commit-identity
consumers disagreed:

- `scripts/git/check_commit_trailers.py` (CI + the `commit-msg` hook)
  required subjects `<= 72` chars and let a description-truncating helper,
  `.orchestrator/common.py`'s `bound_commit_subject`, produce that bounded
  subject.
- `bound_commit_subject` compacts the subject's own **prefix** (not just the
  description) once the task id alone would leave no room for a
  description — so a long task id's bounded subject legitimately does not
  contain the literal, untruncated id.
- `scripts/ai_status.py`'s canonical `done` finalize gate required the full,
  untruncated task id to appear literally in the commit subject.

A 92-character generated task id
(`INTEGRATION-UNBLOCK-GOV-APPROVAL-AUTHORITY-PREREQUISITE-001-MERGE-STATE-BLOCKED-B14932FE23E9`)
cannot satisfy both: CI accepted its bounded, independently reviewed, merged
commit (PR #5639), but canonical `done` rejected the same commit because the
literal 92-char id could never appear inside a `<=72`-char subject.

## The one bounded-prefix rule

`.orchestrator/common.py` exposes two functions every identity consumer
calls to answer "what prefix can a bounded subject for this task_id carry?":

- `canonical_commit_subject_prefix(task_id, max_len=72)`: the deterministic
  compacted-if-necessary prefix. If the normalized task id leaves room for
  at least a 10-character description, this is the task id itself
  (normalized: non-alnum/hyphen characters collapsed to `-`, upper-cased).
  Otherwise it is compacted to `min(35, max(10, max_len - 15))` characters,
  trailing hyphens trimmed.
- `commit_subject_prefix_variants(task_id, max_len=72)`: returns
  `(full_prefix, canonical_commit_subject_prefix(task_id, max_len))`. A
  validator that only knows the task_id -- not the description that produced
  the subject it is checking -- must accept *either* value, because
  `bound_commit_subject` only compacts the prefix as a last resort: it first
  tries the full, uncompacted prefix, and only falls back to the compacted
  form when the literal candidate (full prefix + the *actual* description)
  still exceeds `max_len`. Whether a given task_id's prefix ends up compacted
  therefore depends on the description length, not on the task_id alone --
  e.g. a 61-character id paired with a 3-character description ("fix") keeps
  its full, uncompacted 61-character prefix (`61 + 2 + 3 = 66 <= 72`), while
  the same id paired with a longer description gets the compacted prefix
  instead. A validator comparing only against
  `canonical_commit_subject_prefix`'s single answer would (and, before this
  fix, did) wrongly reject the genuine uncompacted-prefix subject.

`bound_commit_subject` (used by `.orchestrator/watch_events.py` and
`.orchestrator/supervisor.py` to generate anchor-commit subjects) derives its
prefix from the same two lower-level helpers `canonical_commit_subject_prefix`
is built from, so a subject it generates is always one of the two values
`commit_subject_prefix_variants` returns. The full task id is never truncated
anywhere except in this subject-display convention — it always remains exact
and complete in the required `Task-ID:` trailer.

No second validator, alias map, per-task override, or exception table was
added. Existing short-id behavior (the literal id in the subject) is
unchanged; only the long/boundary-id case, which previously had no coherent
definition of "bounded" shared across consumers, now has one.

## Where it is enforced

`scripts/git/check_commit_trailers.py`'s `check_message` is the single
authoritative validator used across CI, worker preflight, and canonical status
done/reconciliation. There are no divergent parsers or secondary validators.

1. **`scripts/git/check_commit_trailers.py`** (`check_message`, used by
   `.githooks/commit-msg` for the single staged commit and by
   `.github/workflows/branch-ci.yml` for a PR's non-merge commit range):
   - unchanged: subject `<=72` chars, required trailers present and
     non-empty, no self-review. Real git merge commits are skipped in rev-list
     traversal via `--skip-merge` (parent count > 1); raw text messages starting
     with `Merge ` or `wave-merge:` without structural git parents have no
     provenance and are not exempt from trailer requirements.
   - if `prefix_required` is true, the subject's prefix (the text before the
     first `: `) must equal one of
     `commit_subject_prefix_variants(<target task id>)`. This rejects
     short-ID suffix collisions (for example `ABC-001-OTHER: repair` or
     `ABC-0010: repair` with `Task-ID: ABC-001`) as well as long-prefix suffix
     collisions, while accepting either the uncompacted or compacted form a
     genuine formatter output can carry.
   - a required trailer (for example `Task-ID`) that appears more than once
     is rejected as `duplicate trailer: ... appears N times` (or
     `conflicting trailer: ...` if the values differ).
   - supports explicit `--task-id` parameter to validate against the expected
     canonical task id.

2. **`scripts/git/worker_commit.py`** (the worker-safe commit wrapper):
   preflight delegates directly to `check_commit_trailers.check_message(..., required=("Task-ID",), prefix_required=True, expected_task_id=args.task_id, delivery_class="tooling")`.
   - unconditionally imports `common` and `check_commit_trailers` respecting
     `PANTHEON_COMMAND_ROOT` with no target-cwd override and no fail-open
     fallback (an incoherent command-runtime/candidate-source pairing is a
     hard error, not a silently disabled check).
   - subject prefix and Task-ID trailer validation mirror CI exactly, so a
     worker gets the identity-mismatch diagnostic immediately instead of
     discovering it later at push, CI, or `done` time.

3. **`scripts/ai_status.py`** (`collect_done_delivery_metadata`, the
   canonical `done` finalize gate):
   - validates the delivery commit via the shared `check_message(..., required=..., prefix_required=..., expected_task_id=task_id, delivery_class="product")`.
   - short-ID suffix collisions (`ABC-001-OTHER:`, `ABC-0010:`) and duplicate
     identical/conflicting trailers are rejected identically to CI and worker
     preflight.
   - for exempt subjects (`commit_subject_skips_trailer_check`), the task id
     must still appear in the subject, and any present `Task-ID:` trailer must
     match the task id exactly.
   - `check_commit_trailers` is imported lazily inside `_commit_trailer_checker()`,
     so synthetic test fixtures that omit `scripts/git/` can still run every
     `ai_status.py` command that does not perform commit-identity validation.

`scripts/ai_status.py`'s `validate_merged_tooling_done` (the Human/Ops
direct-tooling-delivery reconciliation path) likewise delegates directly to
the shared `checker.check_message(commit_message, required=("Task-ID",), prefix_required=True, expected_task_id=task_id, delivery_class="tooling")`,
ensuring tooling delivery reconciliation enforces the exact same identity, prefix,
and trailer constraints without drift.

## Recovering PR #5639

This task only fixes the contradiction going forward. Recovering the
original PR #5639 delivery (the merged, independently-reviewed,
required-checks-passed commit that could not reach canonical `done`) is a
separate step: the activation coordinator uses the existing
`resume_integration` / owner `done` flow against PR #5639's own original
approval and merge record. Landing this fix does not itself mark PR #5639
done, fabricate a new approval, or rewrite its accepted commit.

## Verification

See `docs/deployment/evidence/OPS-COMMIT-IDENTITY-001/evidence.json` for the
exact commands run and their results, including a reproduction of the real
92-char task id contradiction and the 61-char short-description boundary
case against the now-shared `commit_subject_prefix_variants` helper.
