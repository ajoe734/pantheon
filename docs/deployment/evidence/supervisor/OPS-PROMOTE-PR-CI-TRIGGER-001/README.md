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

`publish-promote.yml` receives `actions: write` to dispatch that workflow and
`checks: read` for the exact-head idempotency lookup. `publish_promote.py`:

- keeps bulk open-PR discovery lightweight and selects only the maximal open
  candidate for an exact lookup;
- asks GitHub for required check rollups only on that exact candidate;
- validates the exact PR head before dispatch;
- dispatches `branch-ci.yml` on that immutable head;
- requests auto-merge only after dispatch succeeds; and
- leaves an existing PR alone once all three required contexts are attached.

The repair does not change release tags, publish snapshot trees, `master`
branch protection, deployment admission, or broker/capital authority.

Implementation PR `#4258` merged as
`6ae436c546942df1ba0a762d7167b456dfedabc8` after both push and PR Branch CI
runs passed all three required contexts. Publish-cut run `30284714199` then
created `release/v2026.07.27.2` at that exact merge without dispatching a
deployment.

Three evaluations of the `.2` and `.3` fresh snapshots failed closed
before opening a PR because the first implementation asked GraphQL for
`statusCheckRollup` across the 1,000-row bulk lookup. Runs `30284788017` and
`30284856368` received HTTP 502 with the rollup field, and run `30285398658`
proved the runner's GraphQL path still returned 502 after that field was
removed. The final follow-up replaces promote PR, exact-head check-run, and
regression issue discovery with paginated REST calls while preserving
fail-closed errors and exact-candidate idempotency. A new release will be cut
after that follow-up merges so the live proof covers the corrected bytes.

## Validation Before Publication

- `python3 -m unittest scripts.git.test_git_workflow_helpers.PublishPromoteTests -v`
  — 22 passed.
- provisioned checkout-local interpreter, then
  `pytest -q scripts/git/test_git_workflow_helpers.py scripts/test_nightly_publish_cut.py tests/orchestrator/test_release_branch_discipline.py`
  — 70 passed.
- `python3 -m py_compile` on the helper and test module — passed.
- workflow YAML parse and `git diff --check` — passed.
- live read-only REST smoke — 26 promote PRs listed; exact PR `#4138`
  returned head and zero checks without an API error.

## Owner Rescue Revalidation

The supervisor reassigned the blocked lane from Codex2 to Codex without
changing the repair scope or reviewer. Codex anchored the task metadata as
`09af22e3c05ebea666f65ee34f57862cfc265840`, then merged current `origin/dev`
`87166a352c0b90a26a6e35c138acfaea195fa4ee` through merge commit
`8f4731aa86cbe99da6b535fa565a1dcb84474c40`. The merge was conflict-free and
composes the REST repair with the current release-controller and supervisor
mainline.

At that composed head, Codex repeated the 22-test unittest slice and the
70-test pytest slice, compiled the helper and tests, parsed both workflow YAML
files and this evidence JSON, ran `git diff --check`, and repeated the
read-only REST smoke. All local checks passed; the live lookup still returned
26 open promote PRs and exact PR `#4138` at
`cb90dc479214c6ff0779aff70f915593ec9196c4` with zero attached checks and no
API error.

## REST Follow-up Merge Gate

REST follow-up PR `#4262` is open with auto-merge enabled. At code/evidence
head `25d8f0764352369dbb6394694627e05d29087448`, both the push run
`30294569664` and pull-request run `30294571835` completed successfully:
`Commit trailers`, `Runtime mirror guard`, `Python packaging provision`, and
`Smoke acceptance` all passed.

The PR cannot merge yet because `dev` protection now requires one independent
approval with last-push approval plus the external status contexts `Pantheon
canonical review gate` and `Pantheon root merge freeze 2026-07-27`. The head
has eight successful GitHub Actions check runs but no commit statuses for
those external contexts. This is an external review/freeze gate, not a failed
repository test. The owner will not self-approve, forge a status, weaken
branch protection, or bypass the freeze. Claude/Human Ops must independently
clear the governed merge gate before the REST repair can reach `dev`.
Any owner-rescue push creates a new PR head, so the successful runs above are
historical evidence for `ee04032de9e00cde74a948b5ba1389217bcccbc4`; the
updated head must reacquire CI, last-push approval, and both external contexts.

## Independent Review

Claude independently reviewed exact head
`50c1a229f4d0bc31035a8dd67146e8dc5f28b211` and approved the REST repair for
owner closeout. The review reproduced all 22 `PublishPromoteTests`, the
70-test focused pytest slice, and live read-only REST discovery. It also
confirmed that the task head's eight successful check runs expose the three
required context names.

That approval does not satisfy the remaining live acceptance by itself.
Closeout must still observe auto-merge on an actual fresh promote candidate
after the follow-up reaches `dev`, and stale promote PRs may be retired only
after the manifest records accepted-release ancestry. A fresh owner push also
requires new CI and last-push approval; Human Ops retains ownership of the
canonical review and root merge-freeze statuses.

## Owner Closeout Preparation

After the independent review, Codex anchored the reviewed decision as
`7301f6e7a05145a1937f95d889f3af4be82b7072` and merged current `origin/dev`
`b81edf76dfc14087dd7d5e3a6599448cb9d0bb09` through conflict-free merge
`a73a639c9db350943c4b4adff0dd92523799ec81`. The composed tree passed the
same 22-test unittest slice and 70-test pytest slice, `py_compile`, workflow
YAML and evidence JSON parsing, and `git diff --check`.

The repeated live read-only smoke still listed 26 open promote PRs and found
PR `#4138` at exact head
`cb90dc479214c6ff0779aff70f915593ec9196c4` with zero checks. The previously
reviewed task head still exposed eight successful check runs whose names
include every required context. No release, promote PR, stale PR, branch
protection, or external status was mutated by this verification.

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
