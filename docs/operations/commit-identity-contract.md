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

1. **`scripts/git/check_commit_trailers.py`** (`check_message`, used by
   `.githooks/commit-msg` for the single staged commit and by
   `.github/workflows/branch-ci.yml` for a PR's non-merge commit range):
   - unchanged: subject `<=72` chars, required trailers present and
     non-empty, no self-review.
   - if a `Task-ID:` trailer is present and the bounded-subject-prefix rule
     applies (`prefix_required` true), the subject's prefix (the text before
     the first `: `) must equal one of
     `commit_subject_prefix_variants(<Task-ID trailer value>)`. This rejects
     a subject that names a different or unrelated task (for example
     `XYZ-001: mentions ABC-001` with `Task-ID: ABC-001`) even when the
     trailers are otherwise well-formed, while still accepting either the
     uncompacted or compacted form a genuine formatter output can carry.
   - a required trailer (for example `Task-ID`) that appears more than once
     with different values is rejected as `conflicting trailer: ...`.
     `parse_trailers` still returns only the last occurrence for callers
     that want a single value (last-write-wins), but `check_message` no
     longer silently accepts a duplicated, conflicting trailer line.

2. **`scripts/git/worker_commit.py`** (the worker-safe commit wrapper):
   preflight requires, before staging or committing, and unconditionally
   (there is no fail-open fallback if `.orchestrator/common.py` is missing
   the identity helpers -- an incoherent command-runtime/candidate-source
   pairing is a hard error, not a silently disabled check):
   - the subject's own prefix (the text before its first `:`) equals the
     literal `--task-id` or one of `commit_subject_prefix_variants`, checked
     as an exact match against that prefix rather than a substring anywhere
     in the subject (the same `XYZ-001: mentions ABC-001` case CI rejects);
   - the message body has exactly one `Task-ID:` trailer, and its value
     equals `--task-id` exactly (missing, duplicated/conflicting, or
     mismatched trailers are all rejected).

   This mirrors the check the `commit-msg` hook / CI would apply, so a
   worker gets the identity-mismatch diagnostic immediately instead of
   discovering it later at push, CI, or `done` time.

3. **`scripts/ai_status.py`** (`collect_done_delivery_metadata`, the
   canonical `done` finalize gate):
   - for a subject that is exempt from the trailer-presence requirement
     (`commit_subject_skips_trailer_check`: `Merge `, `Revert `, `promote:`,
     `hotfix:`, `publish:`, or the `OPS-{GIT-WORKFLOW,GIT-REDESIGN,DOC,REBASE}-`
     housekeeping styles), the task id may still appear anywhere in the
     subject (unchanged, and still how a subject that merely embeds the id —
     for example inside a merge commit's `task/<id>` branch name — passes);
   - for every other subject, its own prefix must exactly equal the literal
     task id or one of `commit_subject_prefix_variants(task_id)` -- the same
     exact-prefix rule CI and `worker_commit.py` apply, replacing a looser
     "task id or its bounded prefix appears anywhere as a substring" check
     that could not tell `ABC-001: repair` apart from `XYZ-001: mentions
     ABC-001`;
   - a required trailer (including `Task-ID`) that appears more than once
     with conflicting values is rejected the same way CI rejects it --
     previously this path only used `parse_trailers`' last-write-wins value,
     so a forged/duplicated `Task-ID:` line could bind silently to whichever
     value happened to appear last;
   - the trailer-presence exemption above means trailers *may be absent*, not
     that a *present* `Task-ID:` trailer may lie: even for an exempt subject,
     if a `Task-ID:` trailer is present it must equal the task's id, or the
     transition is rejected.
   - `check_commit_trailers.parse_trailers` and `check_commit_trailers.
     duplicate_trailer_problems` are imported lazily, inside this function,
     rather than at module load. `scripts/ai_status.py` otherwise has no
     dependency on `scripts/git/`, and a command-runtime copy that omits
     that directory (for example a synthetic fixture that only copies
     `scripts/*.py`) must still be able to run every `ai_status.py` command
     that does not reach commit-identity validation.

`scripts/ai_status.py`'s `validate_merged_tooling_done` (the Human/Ops
direct-tooling-delivery reconciliation path) is a narrower consumer with its
own reviewer-free `development_tooling` authority. It now also treats a
present `Task-ID:` trailer as canonical identity: if the delivery commit
carries one, it must equal the task's id exactly (rejecting both a duplicated/
conflicting trailer and a trailer naming a different task), regardless of
what the subject or rest of the message says. Only when no `Task-ID:` trailer
is present at all does it fall back to its original whole-message,
word-boundary-guarded regex match — this preserves the historical behavior
for legacy tooling commits that never carried a trailer, while closing the
gap where a commit's subject merely *mentioned* the right task id but its
trailer bound a different one.

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
