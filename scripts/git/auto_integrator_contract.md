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
- Draft PRs, missing PRs, failing checks, missing checks, dirty merge states, and
  rebase conflicts are not merged.
- The integrator never resolves conflicts and never bypasses branch protection.
- Blockers create an `INTEGRATION-UNBLOCK-*` task instead of leaving the parent
  stranded.

## Merge Flow

For each eligible task, capped by `max_tasks_per_run`:

1. Read the task row from `ai-status.json`.
2. Find the open PR for `task/<TASK-ID>` into `dev`.
3. Require green GitHub status rollup.
4. Fetch `origin/dev` and the task branch.
5. Create a temporary detached worktree for the task branch.
6. Rebase that worktree onto `origin/dev`.
7. Run configured smoke commands.
8. If the rebase changed the task branch and `--execute` is active, push with
   `--force-with-lease` and enable auto-merge so CI can re-run.
9. If no push was needed and the PR is still mergeable, run `gh pr merge`.
10. After merge, run `scripts/ai_status.py done` as the task owner so the normal
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

## Non-goals

- No automatic conflict resolution.
- No admin merge or branch-protection bypass.
- No broad batching; first version is intentionally serialized.
- No publish/master promotion. That remains owned by `publish_promote.py`.
