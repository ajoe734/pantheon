# SUP-L12-HELD-CLOSE-OVERLAP-GUARD-20260731 evidence

This packet records the exact held-close overlap guard repair. The dispatcher
admits only the immutable
`L12-CONTROLLER-CATALOG-INTEGRATION-20260731` to held `L12-CLOSE-001`
registry pair. Malformed held rows, mutated release-order contracts, additional
close overlaps, and unrelated live overlaps remain fail-closed.

Local acceptance is complete and reproduces at the current task head.
Independent exact-head review, protected merge, and immutable command-root
promotion remain pending and must not be inferred from this candidate evidence
cut. No product tasks were materialized.

## Base branch regression and what this PR repairs (2026-08-06)

The stale-base squash merge of PR #4590
(`23ae23c2185d31d2aeacafaa9b051127a6d53136`, merged 2026-08-06T11:57:30Z)
changed 227 files with 1750 insertions and 47932 deletions and deleted 166
files from `dev`. It is a regression, not an intentional removal:

- The `scripts/dispatch_twelve_loop_gap_2026_07_26.py` blob it left on `dev`
  (`d5f2f9092fafcd2d474ca8ecbf1b5af8eaf32dba`, 1203 lines) is byte-identical to
  the blob at ancestor commit `780c553a0`, so four later lanes were reverted.
  The file no longer defined `_current_live_overlap_guard`.
- `scripts/test_dispatch_twelve_loop_gap_current_remediation_2026_07_31.py`, a
  declared artifact of this task, was deleted outright.
- The catalog inputs this dispatcher reads
  (`docs/bff/execution-tasks/2026-07-31-l12-current-gap-supervisor-dispatch/`
  `INDEX.md`, `tasks.json`, `guarded-remediation-tasks.json`) were deleted, so
  `--validate-only --current` failed closed with `FileNotFoundError`.
- 154 of the 166 deleted files still exist on `origin/master`.

This PR restores exactly the five files above and nothing else. Each restored
file is byte-identical to its `23ae23c21^` blob, verified with `git hash-object`
against `git rev-parse 23ae23c21^:<path>`. The PR diff against `dev` is eight
files: those five, this README, `evidence.json`, and the task brief.

Two lanes' later additions to the dispatcher (`626631be8`, `f2b480942`,
`6aec51e9c`, taking it from 2923 to 3093 lines) are **not** restored here. They
were also lost to the same squash and are outside this task's scope.

## Still blocking merge, Human/Ops scoped

`.github/workflows/canonical-review-gate.yml` remains deleted from `dev`. While
it is absent from the default branch the required "Pantheon canonical review
gate" context cannot be produced, so this PR stays unmergeable at merge time
even after an approving review. That repair is not attempted here.

## Branch history re-cut

The previous branch head `7db2114e5c18d3a8183ebae2ca92bfcc07bae52b` carried
`dfdcd07ffb244ce29182478654ffe7d6ec4178e9`, whose 90-character subject failed
the required Commit trailers check. That check re-scans the whole
`origin/dev..HEAD` range, so it could not be repaired by a follow-up commit. No
approval was ever bound to that head, so the branch was re-cut linearly on
`origin/dev` tip `ab5caf7d4` and force-pushed with lease. The pre-rewrite head
is preserved locally as tag `pre-rewrite-sup-l12-held-close`.

The machine-readable record is [evidence.json](evidence.json).
