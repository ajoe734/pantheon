# OPS-PROMOTE-PR-CI-TRIGGER-001 Evidence

## Incident

Pantheon `master` requires these GitHub Actions contexts:

- `Commit trailers`
- `Runtime mirror guard`
- `Smoke acceptance`

The scheduled `Publish Promote` run `30193738289` successfully opened PR
`#4138` at exact head
`cb90dc479214c6ff0779aff70f915593ec9196c4` and enabled auto-merge. The PR
remained `BLOCKED` with an empty `statusCheckRollup`. A live inventory found
26 open `promote/*` PRs in the same state: auto-merge enabled and zero checks.

The causal boundary is the workflow token. `publish-promote.yml` creates the
branch and PR with its `GITHUB_TOKEN`, so its push and PR creation do not
recursively start `Branch CI Gate`. The ordinary `pull_request` trigger is
therefore present but never emits the required contexts for these PR heads.

## Repair

`Branch CI Gate` now exposes a guarded `workflow_dispatch` contract. The
caller must provide a full expected promote head SHA and PR number, dispatch
on a `promote/*` ref, and match `github.sha` exactly. Both independent required
jobs validate that binding before running. The runtime mirror checkout is
full-history so its dispatched diff cannot silently collapse because a parent
is absent.

`publish-promote.yml` receives only the `actions: write` permission needed to
dispatch that workflow. `publish_promote.py`:

- asks GitHub for required check rollups on open promote PRs;
- identifies only the maximal open candidate with missing contexts as
  `ci_repair`;
- validates the exact PR head before dispatch;
- dispatches `branch-ci.yml` on that immutable head;
- requests auto-merge only after dispatch succeeds; and
- leaves an existing PR alone once all three required contexts are attached.

The repair does not change release tags, publish snapshot trees, `master`
branch protection, deployment admission, or broker/capital authority.

## Validation Before Publication

- `python3 -m unittest scripts.git.test_git_workflow_helpers.PublishPromoteTests -v`
  — 18 passed.
- provisioned checkout-local interpreter, then
  `pytest -q scripts/git/test_git_workflow_helpers.py scripts/test_nightly_publish_cut.py tests/orchestrator/test_release_branch_discipline.py`
  — 66 passed.
- `python3 -m py_compile` on the helper and test module — passed.
- workflow YAML parse and `git diff --check` — passed.

## Live Proof and Stale-PR Retirement

The immutable exact-candidate proof must be recorded only after the repair is
merged into `dev`, a fresh release snapshot contains it, and that snapshot's
promote PR receives all three contexts. The manifest intentionally leaves
these fields pending during the implementation PR:

- fresh release tag and promote branch;
- exact promote PR number and head;
- workflow run and three required conclusions;
- auto-merge result and resulting `master` merge commit;
- ancestry proof covering every stale release;
- the exact stale PR numbers closed only after that proof.

No stale PR is closed merely because it is old. The owner will first prove
that the accepted fresh release makes its release tag reachable from
`master`, then close only older open promote PRs whose release tags are
ancestors of that accepted release.
