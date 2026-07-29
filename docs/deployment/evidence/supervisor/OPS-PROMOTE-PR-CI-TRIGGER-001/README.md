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

## Resumed Live-Proof Delivery

The supervisor returned the task to Codex as `in_progress` on
2026-07-29 with Codex2 as the canonical reviewer because the approved repair
still lacked its required live acceptance. Codex anchored that dispatch state,
then merged current `origin/dev`
`e51c1220ab3582c9f45f2689dd546ee4a660b4e1` through conflict-free merge
`d0d8e0398b41f39f6bd044d3469e573344c33b5f`.

At the composed tree, the 22-test unittest slice passed in 0.315 seconds, the
70-test focused pytest slice passed in 8.40 seconds, Python compilation,
workflow YAML and evidence JSON parsing, and `git diff --check` all passed.
The live read-only REST smoke still returned 26 open promote PRs and found PR
`#4138` at exact head
`cb90dc479214c6ff0779aff70f915593ec9196c4` with zero checks.

At the time of that pre-push observation, PR `#4262` remained at
`3ade46dceb24ca621c3801d22db8d1348ce54643`: its eight GitHub Actions checks
were successful, but it was behind `dev`. Current repository policy is
`review_before_merge`, so the historical auto-merge request was removed before
the next reviewed head. Every later exact head must reacquire CI, receive
Codex2's independent approval, and merge through the governed integrator
before a fresh promote candidate can provide the live proof.

## Exact-Head Review Reopen and Current Dev Composition

Codex2 independently reproduced the repair at exact head
`1ed3109dd787d9d0d1b51ac12268bb1bdd850f5b`. Both its push run
`30423656277` and pull-request run `30423658834` completed successfully, for
eight total check runs covering `Commit trailers`, `Runtime mirror guard`,
`Python packaging provision`, and `Smoke acceptance`. The reviewer repeated
the 22-test unittest slice, 70-test focused pytest slice, Python compilation,
workflow YAML and evidence JSON parsing, `git diff --check`, and the live
read-only REST smoke.

The implementation was sound, but the reviewer reopened delivery because
`origin/dev` had advanced beyond the reviewed head and strict branch
protection made the PR `BEHIND`. The committed manifest also still named the
prior `c89bff92` head and runs rather than reviewed head `1ed3109d`. Codex
anchored the earlier reopen context as
`13583c5ea8b54fd418453ba47a9b4bd0a5f2cb07`, composed `origin/dev`
`b1527e868654fb93765b3e5788adeea1f5e869a2` through
`d63ff5f7ad2a934b8fc9e2ed31179bf1f9fb5b1c`, then composed the next dev tip
`3eb6a6bd86093a0296fcd18871e0f014a4292e7b` through
`8d17cdbaf90d1469c36d111a5002ef95b6a3336c`, and finally composed dev
`22fb0b6ba2c1beccfd55a32b3e48bca250375192` through reviewed head
`1ed3109dd787d9d0d1b51ac12268bb1bdd850f5b`.

At that latest composed tree, Codex again passed all 22 unittest cases and all
70 focused pytest cases, compiled the helper and tests, parsed both workflow
YAML files and this evidence JSON, and passed `git diff --check`. The live
read-only REST smoke still listed 26 open promote PRs and found PR `#4138` at
`cb90dc479214c6ff0779aff70f915593ec9196c4` with zero checks. Before this
evidence refresh is pushed, the live PR remains on reviewed head `1ed3109d`,
has eight successful Branch CI checks, is `BEHIND`, has no auto-merge request,
and has the fail-closed canonical review status recorded by Codex2. The
refreshed PR head must reacquire Branch CI and Codex2 exact-head approval; the
canonical review and root-freeze contexts remain Human/Ops-controlled.

## Owner Closeout Gap Repair

Claude2 approved exact head
`77dc9e49cc105a81e213b3ff02c1b657685acf6e` but correctly withheld `done`
because the live acceptance was incomplete and exposed two additional
fail-open edges. Codex preserved the approval dispatch at
`ab31d3cebef86e4702e392cc7ea2bfecab11c29d`, then repaired those edges at
`253858835a4671e9f905b030d4ff70f108ad0077`.

The existing-PR path now reads `.github/workflows/branch-ci.yml` from the
exact promote head through REST before dispatch. A legacy head without
`workflow_dispatch`, `expected_head_sha`, and `promote_pr_number` returns
`legacy_ci_contract` without dispatching or failing the hourly promote run.
It remains open only until the evidence-based stale-retirement pass can prove
its release ancestry. The auto-merge request now fails closed on a non-zero
`gh pr merge --auto` result and immediately verifies through the pull-request
REST resource that either `auto_merge` is non-null or `merged_at` is present.

A live read-only probe exercised the new classification against PR `#4138`
head `cb90dc479214c6ff0779aff70f915593ec9196c4`. The exact workflow lacks the
dispatch contract, and `open_candidate` returned `legacy_ci_contract` with all
three required checks missing without mutating the PR. The same REST probe
confirmed current fresh release `release/v2026.07.29.5` at
`57abe669fc0b2c9c871c09920e156adf85f7e30e` contains the dispatch contract.
Read-only discovery classified that release as the single eligible candidate.
The checkout-local provisioned interpreter passed all 25
`PublishPromoteTests` and the 73-test focused workflow slice; Python
compilation, both workflow YAML parses, evidence JSON parsing, and both diff
checks also passed.

These changes still require fresh Branch CI and Antigravity review on the final
PR head. The root merge-freeze status remains Human/Ops-controlled. Actual
workflow dispatch, auto-merge observation, master reachability, and stale PR
closure remain blocked until PR `#4262` merges into `dev`.

## Finalize Dispatch Refresh

The canonical task row now assigns Codex as owner and Antigravity as reviewer.
Antigravity approved exact head
`f1b9bd3b20f1f0f3637e87fbe54bf5f1cadfe592` at
2026-07-29T09:09:06Z. Before owner closeout could merge that head, `dev`
advanced by one supervisor-only commit and strict branch protection reported
PR `#4262` as `BEHIND`.

Codex anchored the supervisor-issued owner/reviewer metadata as
`7073c77b53d27b1bfe44107888ec4000e9208a52`, then composed current `origin/dev`
`18e102a1950ab3aa9a2e9f97ad50313d1fa93d5d` through conflict-free merge
`5d40efd33a300d70ab6aa05350bfbca3cfe5e46f`. The incoming commit changes only
supervisor dispatch ordering and tests; it does not overlap promote code or
evidence. At the composed tree, all 25 `PublishPromoteTests` passed in 0.221
seconds and the 73-test focused pytest slice passed in 9.77 seconds.
`py_compile`, both workflow YAML parses, evidence JSON parsing, and both diff
checks also passed.

The resulting evidence commit and push will create a new exact PR head, so the
earlier canonical approval cannot authorize that merge. The unchanged
reviewer must bind Antigravity approval to the new head after Branch CI passes.
Human/Ops must separately supply `Pantheon root merge freeze 2026-07-27` on
the same exact head. The owner does not impersonate either authority.

## Strict-Base Refresh After Approval

Antigravity then approved exact head
`f81a9b1e96de5d5bbfd1530daabd6f3441c13893` at
2026-07-29T10:20:30Z after all Branch CI checks passed and the canonical
review gate was attached. Before the governed integrator could land that head,
`dev` advanced to `8ea01a8e3993b3dabc6cd475c7058d299eaf4a01`, and strict
branch protection correctly refused to merge the now-behind approved head.

Codex preserved the supervisor-generated approval dispatch metadata as
`2f895c5f5e95b5256025528e31809f37407fcdc7`, then composed the new `dev`
tip through conflict-free merge
`b88cf3d849fb948879ecb45a0dd15b85cacbf7b6`. The incoming commit added only
the independent `SUP-L12-REVIEW-PRIORITY-GATE-20260729` closeout brief and did
not overlap promote behavior or this task evidence.

At the composed tree, all 25 `PublishPromoteTests` passed in 0.246 seconds and
the 73-test focused pytest slice passed in 7.98 seconds. `py_compile`, both
workflow YAML parses, evidence JSON parsing, and both diff checks passed. A
live read-only REST probe again listed 26 open promote PRs, found legacy PR
`#4138` with zero checks and no dispatch contract, and confirmed that
`release/v2026.07.29.5` contains the guarded dispatch contract.

This refresh necessarily changes the PR head. PR `#4262` must reacquire Branch
CI and Antigravity approval bound to the new exact head before the governed
integrator can merge it. The owner does not reuse the superseded approval or
weaken strict branch protection.

## Codex2 Owner Finalization Refresh

Antigravity approved exact head
`c249265c8b6cde99ed20376491d3a8ff88a7aff2` at
2026-07-29T10:50:50Z after the eight Branch CI jobs and the canonical review
status succeeded. The supervisor then reassigned finalization ownership from
Codex to Codex2. Before closeout, `dev` advanced again and strict branch
protection reported PR `#4262` as `BEHIND`.

Codex2 preserved the supervisor-generated dispatch brief in anchor
`0b80fc368b6ad58e82bae73de8b1c2697a51dbd8`, then composed current
`origin/dev` `5503111f5e94d6e8be249db5ffa773b829629815` through
conflict-free merge `0463d6a9a651ecc2d67529b0b37c98b5ea19ae64`.
The incoming changes cover supervisor preferred-lane scheduling and L12
recovery documents only; they do not overlap promote implementation or this
task evidence.

At the composed tree, all 25 `PublishPromoteTests` passed in 0.204 seconds and
the 73-test focused pytest slice passed in 9.63 seconds. `py_compile`, both
workflow YAML parses, evidence JSON parsing, and both diff checks passed. A
live read-only REST probe still found 26 open promote PRs, including legacy PR
`#4138` with zero checks and no dispatch contract, and confirmed that
`release/v2026.07.29.6` contains the guarded dispatch contract.

The evidence commit and push will create another exact PR head. The prior
approval cannot authorize it. PR `#4262` must reacquire Branch CI and
Antigravity approval on that head; Human/Ops must independently supply
`Pantheon root merge freeze 2026-07-27` before governed integration.

## Codex Owner Reassignment Finalization Refresh

After repeated Codex2 worker exits, the supervisor reassigned the
`review_approved` closeout to Codex without changing Antigravity's reviewer
role or the approved repair scope. Codex preserved that task-scoped dispatch
metadata in anchor
`b374a5c05f3e4181d1ddd356fb6df3a186355959`, then composed current
`origin/dev` `24d9c547e7ce52ddcf0bda648be9d4a9bf3cefde` through conflict-free
merge `f1fafb7c4db246d7054e6c748ed5e4bac9c579a0`. The incoming changes cover
supervisor urgent-only preemption and its tests; they do not overlap promote
implementation or this task evidence.

At the composed tree, all 25 `PublishPromoteTests` passed in 0.268 seconds and
the 73-test focused pytest slice passed in 9.18 seconds. `py_compile`, both
workflow YAML parses, evidence JSON parsing, and both diff checks passed. A
live read-only REST probe again listed 26 open promote PRs, found legacy PR
`#4138` at `cb90dc479214c6ff0779aff70f915593ec9196c4` with zero checks and no
dispatch contract, and confirmed daily snapshot `release/v2026.07.29.6` at
`e7eab746afc8ad09321c6da69263dbda4d5eccce` contains the guarded dispatch
contract.

The final evidence commit changes the PR head, so Antigravity's approval of
`c249265c8b6cde99ed20376491d3a8ff88a7aff2` cannot authorize it. PR `#4262`
must reacquire Branch CI and Antigravity approval bound to the resulting exact
head, then the governed integrator must observe the independently controlled
root-freeze context before merging. No release ref, promote PR, stale PR,
branch protection, or external status was mutated during this refresh.

## Codex Strict-Base Refresh After Exact-Head Approval

Antigravity approved exact head
`6d6586da85ce3e2bb48870052d6e8c0bece0f195` at
2026-07-29T11:27:36Z. Both Branch CI runs supplied eight successful check
runs, and canonical review status `51285723012` succeeded. Before governed
integration, strict `dev` advanced to
`c1e396495d37a1c9dfeea5704e7eb73db6acde0e`, so PR `#4262` became
`BEHIND`; the separately controlled `Pantheon root merge freeze 2026-07-27`
context was also absent.

Codex preserved the supervisor-generated approved dispatch brief in anchor
`9d33e83b5785011f7ccf80904f60bb615d873d77`, then composed current
`origin/dev` through conflict-free merge
`7776a4967868b61e329cc72ec7808b4ac873ef0c`. The incoming change protects
wave-0 recovery workers in the supervisor and adds its tests. It does not
overlap promote implementation or this task evidence.

At the composed tree, all 25 `PublishPromoteTests` passed in 0.216 seconds and
the 73-test focused pytest slice passed in 9.76 seconds. `py_compile`, both
workflow YAML parses, evidence JSON parsing, and both diff checks passed.

This evidence commit will create another exact PR head. The prior approval is
therefore recorded as superseded, not reused. PR `#4262` must obtain fresh
Branch CI and Antigravity approval on the new head, then Human/Ops must
independently bind the root-freeze context before the governed integrator can
merge. Auto-merge remains off, and no release ref, promote PR, stale PR,
branch protection, or external status was mutated during this refresh.

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
