# OPS-DEPLOY-WORKFLOW-GUARD-001 follow-up evidence

Captured: 2026-07-16T12:39:34Z

## What changed since the 04:40Z packet

The shared deploy workflow guard is no longer only a repo-local helper. The
live operator crontab now runs it every minute from the synced dev-root:

```cron
* * * * * [ -f /home/lupin/pantheon-ci-deploy/dev-root/scripts/check_shared_deploy_workflow_disabled.py ] && python3 /home/lupin/pantheon-ci-deploy/dev-root/scripts/check_shared_deploy_workflow_disabled.py --enable >> /home/lupin/code/pantheon/.orchestrator/logs/shared-deploy-workflow-guard-cron.log 2>&1 # pantheon-shared-deploy-workflow-guard
```

The cron log shows the guard actively correcting the incident instead of
silently reporting it. Between 2026-07-16T11:44:03Z and
2026-07-16T11:58:03Z it re-enabled one or both watched workflows on every
tick where a worker had left them in `disabled_manually`.

At 2026-07-16T12:39Z both watched workflows were active:

| repo | workflow id | state |
| --- | --- | --- |
| `ajoe734/pantheon` | `269991390` | `active` |
| `ajoe734/execute-plans` | `292028803` | `active` |

`python3 scripts/check_shared_deploy_workflow_disabled.py` returned 0 with no
findings.

## Validation

```bash
python3 -m pytest -q \
  scripts/test_dev_environment_lease.py \
  scripts/test_dev_environment_lease_deploy_contract.py \
  scripts/test_dev_environment_lease_guard.py \
  scripts/test_reap_hung_workers.py \
  scripts/test_check_shared_deploy_workflow_disabled.py
# 53 passed, 13 subtests passed in 23.86s
```

The previously merged task PR remains merged:

- PR: `ajoe734/pantheon#3735`
- task commit: `8b1524e7a41998a505cbdef92bf6382bc3963fe8`
- merge commit: `b7fe5d9220de5ad2e57584eb5f3dc6e109823c0c`
- branch checks: all required checks succeeded before merge.

## Remaining live proof blocker

The clean live proof requested by the task brief is still not reproducible
without touching unowned runs. `ajoe734/pantheon` workflow run
`29469158508` is an unowned `task/EVOCHAIN-011` nonprod deploy dispatch that
has been queued since 2026-07-16T03:31:02Z. It is not this task's run, so this
task did not cancel or force-cancel it.

Recent manual nonprod deploy attempts also show deploy-path failures outside
this task's scope:

| run | branch | result | notable failing step |
| --- | --- | --- | --- |
| `29494634993` | `dev` | `failure` | `Start identity-bound lease heartbeat` |
| `29498360289` | `master` | `failure` | `Deploy requested VM stack` |

Therefore this packet proves the fleet-freeze guard is live and the shared
workflows are active again, but it does not claim the final concurrent deploy
proof. That proof needs the stale unowned queued run and unrelated deploy-path
failures resolved first.
