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

`canonical_commit_subject_prefix(task_id, max_len=72)` in
`.orchestrator/common.py` is now the single deterministic function every
identity consumer calls to answer "what prefix does a bounded subject for
this task_id carry?":

- if the normalized task id prefix leaves room for at least a 10-character
  description, the prefix is the task id itself (normalized: non-alnum/hyphen
  characters collapsed to `-`, upper-cased) — this is the common case and
  covers essentially all hand-written task ids.
- otherwise (a generated id long enough that even a minimal description
  would not fit), the prefix is deterministically compacted to
  `min(35, max(10, max_len - 15))` characters, with any resulting trailing
  hyphens trimmed.

`bound_commit_subject` (used by `.orchestrator/watch_events.py` and
`.orchestrator/supervisor.py` to generate anchor-commit subjects) now derives
its prefix from the same two helpers `canonical_commit_subject_prefix` is
built from, so a subject it generates and a subject a validator checks can
never disagree about what "bounded" means for a given task id. The full
task id is never truncated anywhere except in this subject-display
convention — it always remains exact and complete in the required
`Task-ID:` trailer.

No second validator, alias map, per-task override, or exception table was
added. Existing short-id behavior (the literal id in the subject) is
unchanged; only the long-id case, which previously had no coherent
definition of "bounded" shared across consumers, now has one.

## Where it is enforced

1. **`scripts/git/check_commit_trailers.py`** (`check_message`, used by
   `.githooks/commit-msg` for the single staged commit and by
   `.github/workflows/branch-ci.yml` for a PR's non-merge commit range):
   - unchanged: subject `<=72` chars, required trailers present and
     non-empty, no self-review.
   - new: if a `Task-ID:` trailer is present and the bounded-subject-prefix
     rule applies (`prefix_required` true), the subject's prefix (the text
     before the first `: `) must equal
     `canonical_commit_subject_prefix(<Task-ID trailer value>)`. This
     rejects a subject that names a different or unrelated task even when
     the trailers are otherwise well-formed.
   - new: a required trailer (for example `Task-ID`) that appears more than
     once with different values is rejected as `conflicting trailer: ...`.
     `parse_trailers` still returns only the last occurrence for callers
     that want a single value (last-write-wins), but `check_message` no
     longer silently accepts a duplicated, conflicting trailer line.

2. **`scripts/git/worker_commit.py`** (the worker-safe commit wrapper):
   preflight now additionally requires, before staging or committing:
   - the subject contains either the literal `--task-id` or its
     `canonical_commit_subject_prefix` (accepts both the short-id literal
     convention and the long-id bounded convention);
   - the message body has exactly one `Task-ID:` trailer, and its value
     equals `--task-id` exactly.

   This mirrors the check the `commit-msg` hook / CI would apply, so a
   worker gets the identity-mismatch diagnostic immediately instead of
   discovering it later at push, CI, or `done` time.

3. **`scripts/ai_status.py`** (`collect_done_delivery_metadata`, the
   canonical `done` finalize gate): the subject-identity check now accepts
   either the literal task id in the subject (unchanged, and still how a
   subject that merely embeds the id — for example inside a merge commit's
   `task/<id>` branch name — passes) or `canonical_commit_subject_prefix`.
   The separate, unrelated `Task-ID:` trailer-value check (which must equal
   the task's exact id) is unchanged. `parse_commit_metadata_lines`, a
   second, looser trailer parser duplicated inside `ai_status.py`, has been
   removed in favor of importing `check_commit_trailers.parse_trailers`
   directly — there is now exactly one trailer parser.

`scripts/ai_status.py`'s `validate_merged_tooling_done` (the Human/Ops
direct-tooling-delivery reconciliation path) is a separate, narrower
consumer: it full-message-regexes the exact delivery commit already bound by
`_validated_reconcile_delivery`/`_merged_commit` for the caller-supplied task
id, with no first-parent unwrapping. It does not build or validate a bounded
subject prefix and is unchanged by this task; it retains its existing
Human/Ops + `development_tooling` authority and its reviewer-free
direct-tooling exception (no product review-proof requirement is imported
into it).

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
92-char task id contradiction against the now-shared
`canonical_commit_subject_prefix` helper.
