# Auto Integrator Contract

Status: conservative first implementation for `OPS-AUTO-INTEGRATOR-001`.

`scripts/git/auto_integrator.py` is a serialized lane for the final task-PR
integration step:

```text
review_approved task -> clean task PR into dev -> local rebase smoke -> merge
or unblock task
```

## Safety Model

- Default mode is dry-run. `--execute` is required for git/GitHub/task-state
  mutations.
- A lock at `.orchestrator/auto-integrator.lock` permits one integration pass at
  a time.
- Only active `ai-status.json` tasks with `status=review_approved` are eligible.
- The PR head must be `task/<TASK-ID>` and the base must be `dev`.
- Draft PRs, truly missing PRs, failing checks, missing checks, dirty merge
  states, and rebase conflicts are not merged.
- Merge authority is delegated to `scripts/git/task_review_merge_gate.py`.
  For a task whose canonical contract requires independent review the
  integrator merges only the exact reviewer-approved head, never enables
  GitHub auto-merge, never force-pushes a rebase over the reviewed head, and
  revokes an auto-merge request it finds on a gated PR — including on the
  approved path, since a standing request would outlive the merge and arm the
  next push (the PR #4227 shape).
- Two open PRs claiming the same task branch fail closed instead of resolving
  to the first row.
- If the open PR is already gone because GitHub merged it before the status
  row moved to `done`, the integrator may verify the merged PR's merge commit
  is already in `origin/dev` and run the normal owner `done` reconciliation.
- The integrator never resolves conflicts and never bypasses branch protection.
- Blockers create an `INTEGRATION-UNBLOCK-*` task instead of leaving the parent
  stranded.

## Merge Flow

For each eligible task, capped by `max_tasks_per_run`:

1. Read the task row from `ai-status.json`.
2. Find the open PR for `task/<TASK-ID>` into `dev`.
3. If no open PR exists, check for a merged PR from the same head/base whose
   merge commit is already in `origin/dev`; if found, reconcile the task to
   `done` and stop.
4. If no open or already-merged PR exists, create a missing-PR unblock task.
5. Evaluate the review-before-merge gate against this exact PR head. Any
   pending auto-merge request on a gated PR is revoked here, before the CI and
   merge-state probes, whatever the gate decided - a PR that ends up `waiting`
   because it is `BEHIND` must not keep one. A gated PR that is not approved is
   then blocked at this step.
6. Read `autoMergeRequest` back after every revocation attempt. The command
   exit status is diagnostic only: zero can leave the grant armed, while
   nonzero can race with another actor that already turned it off. If the
   readback is unavailable or still armed, block before any merge call is
   emitted - approval of this head does not make it safe to merge alongside a
   standing grant.
7. Require green GitHub status rollup.
8. Fetch `origin/dev` and the task branch.
9. Create a temporary detached worktree for the task branch.
10. Rebase that worktree onto `origin/dev`.
11. Run configured smoke commands.
12. Merge-then-review tasks only: if the rebase changed the task branch and
    `--execute` is active, push with `--force-with-lease` and enable
    auto-merge so CI can re-run. A gated task branch is never pushed; if it
    needs a refreshed head the result is `waiting` and the owner must rebase
    and obtain a new approval for the new head.
13. If no push was needed and the PR is still mergeable, run `gh pr merge`,
    with `--match-head-commit <approved-oid>` for a gated task so a concurrent
    finalize cannot slip a different head into the merge.
14. After merge, run `scripts/ai_status.py done` as the task owner so the normal
    delivery gate archives the task.

## Configuration

Optional settings live under `.orchestrator/config.json`:

```json
{
  "branch_workflow": {
    "auto_integrator": {
      "max_tasks_per_run": 1,
      "lock_file": ".orchestrator/auto-integrator.lock",
      "merge_method": "merge",
      "smoke_commands": [
        "python3 scripts/git/check_commit_trailers.py --range origin/dev..HEAD"
      ],
      "unblock_owner": "Codex",
      "unblock_reviewer": "Claude"
    }
  }
}
```

If no smoke commands are configured, the integrator still performs the rebase
probe and PR status checks. Operators can pass `--smoke-command` on the CLI to
override the config for one run.

## CLI

Dry-run one task:

```bash
python3 scripts/git/auto_integrator.py --task-id TASK-123 --json
```

Execute one serialized integration pass:

```bash
python3 scripts/git/auto_integrator.py --execute --max-tasks 1
```

Run without opening unblock tasks:

```bash
python3 scripts/git/auto_integrator.py --execute --no-open-unblock
```

## Scheduled Runner

`scripts/run-auto-integrator.sh` is the cron-friendly wrapper. It defaults to
`--execute --max-tasks 1` and passes the canonical status/config paths into the
Python integrator:

```bash
PANTHEON_STATUS_ROOT=/home/lupin/pantheon \
  bash scripts/run-auto-integrator.sh
```

Use `AUTO_INTEGRATOR_DRY_RUN=1` for a non-mutating scheduled smoke and
`AUTO_INTEGRATOR_MAX_TASKS=<n>` to override the default one-task limit.

Install the cron runner with:

```bash
python3 scripts/auto_integrator_install.py \
  --repo /home/lupin/pantheon-ci-deploy/dev-root \
  --status-root /home/lupin/pantheon
```

The installed line is tagged `# pantheon-auto-integrator`, runs every five
minutes by default, and writes logs to
`$PANTHEON_STATUS_ROOT/.orchestrator/logs/auto-integrator-cron.log`.

## Non-goals

- No automatic conflict resolution.
- No admin merge or branch-protection bypass.
- No broad batching; first version is intentionally serialized.
- No publish/master promotion. That remains owned by `publish_promote.py`.
