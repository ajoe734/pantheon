# Dev release trigger: green commits start the deployer

Task: DEV-GREEN-COMMIT-DEPLOY-TRIGGER-20261117 (register RC1 / WP1.4).

## Behaviour

1. **Branch CI on dev push never cancels a started run.** `branch-ci.yml` keeps the
   concurrency group `branch-ci-<workflow>-<ref>` but sets
   `cancel-in-progress: ${{ !(github.event_name == 'push' && github.ref == 'refs/heads/dev') }}`.
   For a dev push GitHub therefore allows at most one run in progress and one
   pending run per group; a newer push replaces only the pending one. Pull requests
   and every other ref keep `cancel-in-progress: true`.
2. **A green dev push run triggers the publish cut.** `nightly-publish-cut.yml` has a
   `workflow_run` trigger on `Branch CI Gate` (`completed`, branch `dev`). Jobs `cut`
   and `dev-pair` run for that event only when the conclusion is `success` and the
   run was a `push` on `dev`.
3. **The deployer guard is unchanged.** `dev-pair` still calls
   `scripts/auto_deploy_dev_pair.py --apply`, which selects the newest green commit
   on dev, skips when hosted already serves it, and skips when a deploy is in
   progress. A triggered run for a commit that is no longer newest green, or that
   hosted already serves, ends successfully without dispatching.
4. **The schedule is a fallback**, moved from `0 * * * *` to `17 * * * *`. GitHub does
   not guarantee scheduled runs. Measured scheduled runs: 11/13 02:17 10:19 18:17;
   11/14 00:02 07:11 14:37 19:35 23:29; 11/15 03:47 11:38; then none until 11/17
   08:48, a 45 hour gap (11/15 11:38Z to 11/17 08:48Z). Every deploy on 11/17 before
   this change came from a manual dispatch.

No workflow gains a `workflow_dispatch` input. `nonprod-deploy.yml` is untouched.

## Runner bound

Dev push CI: at most 1 in progress + 1 pending, the same occupancy ceiling as before
(1 in progress); cancelled work is no longer wasted, since a started run finishes
(17 to 26 min). Publish cut: one run per completed green dev CI. At the measured
rate (6 green commits in ~6 h on 11/17, a green run needing 17 to 26 min, at most
one finishing per run slot) the ceiling is about 56 to 85 CI completions per day and
the expected number of publish cut runs is roughly 6 to 24 per day, each short
(the `cut` job and the 5 minute `dev-pair` job), versus 24 scheduled runs today.
Publish cut concurrency (`nightly-publish-cut`, no cancel) serialises them. Hosted
has no new writer: only the existing `dev-pair` deployer dispatches Nonprod Deploy.

## Tests

`scripts/test_dev_green_commit_deploy_trigger.py` pins the concurrency expression,
the `workflow_run` trigger and job conditions, the cron offset, unchanged dispatch
inputs, and the decision fixtures (served commit, superseded green commit, served
commit while tip is not green). `scripts/test_isolated_l12_provider_workflow.py` and
`scripts/test_release_gate_current_run.py` do not pin `branch-ci.yml` (they cover
`nonprod-deploy.yml` and a stage0 workflow), so they are unchanged.

## Commits that change no build input are not released

Task: DEV-DEPLOY-SKIP-NON-RUNTIME-CHANGES-20261117.

When the frontend commit is unchanged and every file changed between the served
backend commit and the candidate is outside the release build inputs, the deployer
returns `report_only_no_deploy` and dispatches nothing. The rule is
`is_non_build_input` in `scripts/auto_deploy_dev_pair.py` (one place):

- `.orchestrator/` and `ai-task-archive/` (both in `.dockerignore`);
- Markdown files in the repository root, and `*.md` under `docs/operations/` and `docs/reviews/`;
- the three closeout report paths already skipped before.

`docs/` as a whole is **not** skipped: services read `docs/contracts`,
`docs/deployment/loop-catalog.registry.json`, `docs/deployment/evidence/**`,
`docs/04` and `docs/research/notes`, and every Dockerfile copies the repository root.
Fail open: a truncated or diverged comparison, 300 or more files, any other path, or a
rename that crosses the boundary dispatches the release as before. The served commit is
the comparison base, so the next release that changes a build input carries the skipped
commits. Skipped commits stay merged on dev; they never become deployed by themselves.
The publish cut summary prints the skip with the changed file count and the served commit.
Evidence: `docs/deployment/evidence/DEV-DEPLOY-SKIP-NON-RUNTIME-CHANGES-20261117/evidence.json`.
