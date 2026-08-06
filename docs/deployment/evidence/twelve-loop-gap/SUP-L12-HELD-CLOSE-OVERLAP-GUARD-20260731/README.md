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
- The regression has since reached the release branch. Re-measured at
  2026-08-06T17:00Z against a freshly fetched `origin/master` (tip
  `8ec60ff7477e6188149594a3f8ff9aec70f93e27`, the promote `v2026.08.06.2`
  merge of PR #4596), **0** of the 166 deleted files exist on `origin/master`,
  and `origin/master` carries the same reverted dispatcher blob `d5f2f9092`.
  An earlier revision of this packet recorded "154 of the 166 deleted files
  still exist on `origin/master`"; that statement is superseded. `master` is
  not a restore source for any of the 166 files, including
  `.github/workflows/canonical-review-gate.yml`. The only restore source is
  the pre-squash tree `23ae23c21^`.

This PR restores exactly the five files above and nothing else. The three catalog input files (`INDEX.md`, `tasks.json`, `guarded-remediation-tasks.json`) are byte-identical to their `23ae23c21^` blobs, verified with `git hash-object`. The restored dispatcher `scripts/dispatch_twelve_loop_gap_2026_07_26.py` (`640d5a18c8ae`) is the pre-squash file plus this task's 142-line guard change (omitting the later `626631be8` additions `_current_profile`, `_is_current_catalog`, `load_authoritative_task_snapshot`, and `authoritative_snapshot_evidence`). The restored test file `scripts/test_dispatch_twelve_loop_gap_current_remediation_2026_07_31.py` (`8d18ac893a6c`) provides 53 passing tests focusing on the guard, omitting 11 defs present at `23ae23c21^` (5 `test_` defs: `test_authority_uses_one_validated_snapshot_generation`, `test_corrected_bff_scope_avoids_nonterminal_lifecycle_overlap`, `test_current_dry_run_fails_closed_without_journal_authority`, `test_current_dry_run_fails_closed_without_provisioned_lock`, `test_previous_current_profile_remains_available_and_exact`; plus 6 helper defs) while adding 5 new test defs — a net delta of −6 defs. The 5 dropped `test_` defs all exercise `626631be8` authoritative-snapshot symbols this PR intentionally does not restore; the omission is in scope and requires no code work.

Two lanes' later additions to the dispatcher (`626631be8`, `f2b480942`,
`6aec51e9c`, taking it from 2923 to 3093 lines) are **not** restored here. They
were also lost to the same squash and are outside this task's scope.

## Cross-PR Collision Hazard (PR #4528)

Open PR #4528 (`task/SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803`, head `452f0af0f`) restores the exact pre-regression file `24015de46e27` (without this guard) along with the same two files. Since both PRs are open against `dev`, whichever merges second will overwrite the other's file unless merge-order handling or rebase/composition is performed.

## Task Actors Alignment

Ownership moved twice. It was reassigned from `Claude`/`Codex2` to `Antigravity`
on 2026-08-06, then returned to `Claude` the same day after repeated Antigravity
provider timeouts. The canonical task row and `evidence.json` now agree on the
current pair:
- **Owner**: `Claude`
- **Reviewer**: `Antigravity`

The commits authored while `Antigravity` held the task (`a31ddbf8b`,
`7ab57c517`, `02edfd5ca`) carry the trailers that were correct at the time and
are left untouched; only the manifest's live actor bindings are updated.

## Still blocking merge, Human/Ops scoped

`.github/workflows/canonical-review-gate.yml` remains deleted from `dev`, and as
of 2026-08-06T17:00Z it is also absent from `origin/master` — the promote
`v2026.08.06.2` merge (PR #4596) carried the deletion to the release branch.
While it is absent from the default branch the required "Pantheon canonical
review gate" context cannot be produced, so this PR stays unmergeable at merge
time even after an approving review. Because `master` no longer holds the file
either, the Human/Ops restore must be sourced from the pre-squash tree
`23ae23c21^` (or a `promote/*` ref cut before the deletion), not from `master`.
That repair is not attempted here.

## Required checks: nothing green has ever been produced here

No required check has produced a conclusion on this branch. The runs at
`a31ddbf8b` (31119671454, 31119674636) and at `7ab57c517` (31120873389,
31120876932) were **cancelled** when the head advanced — an earlier revision of
this packet recorded the `7ab57c517` runs as *pending*, which is corrected in
`evidence.json`. The runs at `02edfd5ca` (31121317427, 31121320470) were still
queued at the 2026-08-06T17:00Z cut. Every evidence-only revision advances the
head and cancels the in-flight runs, so this packet asserts no check result at
any head. The reviewer must read the live PR checks at the live head.

## Schema conformance

`evidence.json` declares `schema_version: loop_product_evidence.v1` and
`schema_status.status: formalized`. Until this revision it failed
`schemas/product-evidence.schema.json` with two errors (missing
`anchor_commits`; `base_branch_repair`, `delivery_commits`, and
`history_rewrite` disallowed under `implementation_delivery`). The blocks were
renamed and re-parented onto the `094a0f16d` anchor entry without dropping any
recorded fact, and the manifest now validates with zero errors:

```
python -c "import json,jsonschema; jsonschema.Draft202012Validator(
  json.load(open('schemas/product-evidence.schema.json'))).validate(
  json.load(open('docs/deployment/evidence/twelve-loop-gap/SUP-L12-HELD-CLOSE-OVERLAP-GUARD-20260731/evidence.json')))"
```

## Branch history re-cut

The previous branch head `7db2114e5c18d3a8183ebae2ca92bfcc07bae52b` carried
`dfdcd07ffb244ce29182478654ffe7d6ec4178e9`, whose 90-character subject failed
the required Commit trailers check. That check re-scans the whole
`origin/dev..HEAD` range, so it could not be repaired by a follow-up commit. No
approval was ever bound to that head, so the branch was re-cut linearly on
`origin/dev` tip `ab5caf7d4` and force-pushed with lease. The pre-rewrite head
is preserved locally as tag `pre-rewrite-sup-l12-held-close`.

The machine-readable record is [evidence.json](evidence.json).
