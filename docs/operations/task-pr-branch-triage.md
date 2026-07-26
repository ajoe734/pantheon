# Task PR and task-branch triage contract

Status: active operational contract

This contract governs `scripts/git/task_pr_triage.py`. It exists to make stale
task work visible without treating age, a closed task row, or historical
archive delivery metadata as deletion authority.

## Evidence sources

Each report joins these sources at a named `origin/dev` SHA:

1. live GitHub pull-request state and PR history for `dev`;
2. refreshed `origin/task/*` heads and commit timestamps;
3. git ancestry from each branch head to the recorded `origin/dev` SHA;
4. canonical nonterminal state from `PANTHEON_STATUS_ROOT/ai-status.json`;
5. terminal snapshots under `PANTHEON_STATUS_ROOT/ai-task-archive/tasks/`;
6. task ownership trailers from branch commits when no canonical task row is
   available.

When `--refresh` is requested, the fetch must finish before the tool resolves
the full `base_sha`. Branch collection, PR reachability, trailer lookup, report
validation, and deletion proof are then bound to that immutable commit rather
than the movable `origin/dev` ref. The tool rejects a snapshot if an open
GitHub PR head is absent from the refreshed remote refs or the two head SHAs
differ. The operator must refresh and rerun instead of publishing a mixed-time
report.

Archive `ahead`, `push_status`, or branch-name fields are context only. They do
not prove current GitHub reachability and never authorize branch retirement by
themselves.

The report summary keeps the fixed overdue cohort separate from the live
repository-wide open-PR inventory: `cohort_open_pr_count` and
`cohort_resolved_pr_count` always partition `cohort_pr_count`, while
`global_open_task_pr_count` may also include recent task PRs outside the
cohort. The captured `as_of` time is normalized to the published second before
age and retention calculations, so rerunning against the same `base_sha`,
`as_of`, GitHub state, and task state reproduces identical report and dry-run
JSON.

## Dispositions

| Disposition | Meaning | Automatic mutation |
|---|---|---|
| `active-repair` | Open or canonically active work needs refresh/rebase or an explicit decision. | none |
| `conflict-needs-owner` | GitHub reports `DIRTY`; an identified owner must resolve or replace it. | none |
| `merged-reachable` | The PR is merged or the exact branch head is an ancestor of the recorded `dev`. | branch dry run only when all retention guards pass |
| `superseded` | Durable task state or a different merged PR proves replacement. | an open PR may be commented and closed through an explicit allowlist |
| `abandoned-unproven` | Old work is ahead or closed without recoverable retirement proof. | none; retain |
| `protected-retain` | Draft or recent work is inside a protection rule. | none; retain |

Every overdue PR must have one disposition and one owner. A commit
`LLM-Agent` trailer is used when the canonical task and archive no longer
exist; `Human/Ops` is the explicit fallback rather than an empty owner.

## PR closure rule

An open PR is eligible only when one of these is true:

- its durable terminal archive says `terminal_outcome=superseded`; or
- its completed archive cites a different Pantheon PR that GitHub currently
  records as merged.

The closure subcommand re-reads the live PR and fails if it is not open, no
longer targets `dev`, or its head changed after report generation. Apply mode
requires every PR number to be named with `--only`; there is no close-all
switch. The comment records the task archive, merged replacement evidence,
and the fact that the branch is retained.

Age, `BEHIND`, `DIRTY`, or an archive's stale `ahead` value is never sufficient
closure evidence.

## Branch retention and deletion dry run

The retention window is 30 days. A branch enters the deletion dry-run manifest
only if all of these are true at the same snapshot:

- it has no open PR;
- it has no nonterminal canonical task;
- its head commit is at least 30 days old;
- the exact head is an ancestor of the recorded `origin/dev` SHA; and
- its disposition is `merged-reachable`.

Recent, draft/open-PR, active, ahead, uncertain, or unrecoverable branches are
excluded with machine-readable reasons. Explicitly superseded but
non-reachable branches remain retained because removing their only ref would
reduce recoverability.

The tool emits `mode: dry-run-only` and implements no branch deletion command.
A future deletion task would need separate Human/Ops authorization, a fresh
snapshot, recovery tags where policy requires them, and its own reviewed PR.

## Reproduce the 2026-07-22 audit cohort

The original cohort had 29 overdue task PRs. Lease repair later closed #3936
as superseded and merged #3948, so they are included explicitly while the tool
discovers the still-open overdue members:

```bash
python3 scripts/git/task_pr_triage.py generate \
  --refresh \
  --status-root "$PANTHEON_STATUS_ROOT" \
  --as-of <UTC-ISO-TIMESTAMP> \
  --include-pr 3058 \
  --include-pr 3317 \
  --include-pr 3334 \
  --include-pr 3372 \
  --include-pr 3936 \
  --include-pr 3948 \
  --expected-cohort-count 29 \
  --output docs/deployment/evidence/ops-task-pr-triage-001/triage-report.json \
  --markdown docs/deployment/evidence/ops-task-pr-triage-001/README.md \
  --deletion-manifest docs/deployment/evidence/ops-task-pr-triage-001/branch-deletion-dry-run.json

python3 scripts/git/task_pr_triage.py validate \
  --report docs/deployment/evidence/ops-task-pr-triage-001/triage-report.json \
  --deletion-manifest docs/deployment/evidence/ops-task-pr-triage-001/branch-deletion-dry-run.json \
  --expected-cohort-count 29
```

Preview authorized closures without mutation:

```bash
python3 scripts/git/task_pr_triage.py close-superseded \
  --report docs/deployment/evidence/ops-task-pr-triage-001/triage-report.json
```

Apply mode must enumerate the reviewed PRs explicitly:

```bash
python3 scripts/git/task_pr_triage.py close-superseded \
  --report docs/deployment/evidence/ops-task-pr-triage-001/triage-report.json \
  --only <PR-NUMBER> \
  --apply
```
