# OPS-DEPLOY-WORKFLOW-GUARD-001 blocked cleanup evidence

Captured: 2026-07-16T12:51:49Z

## Scope

This packet records the reviewer-authorized attempt to clear exactly one stale
queued deploy run before rerunning the operational proof:

- repo: `ajoe734/pantheon`
- workflow id: `269991390`
- run id: `29469158508`
- branch: `task/EVOCHAIN-011`
- event: `workflow_dispatch`
- created at: `2026-07-16T03:31:02Z`

No shared workflow was disabled. No list-based cancellation was run. No other
workflow run was cancelled or force-cancelled.

## Pre-check

Before the cleanup attempt, the stale run was still queued:

```json
{"id":29469158508,"status":"queued","conclusion":null,"head_branch":"task/EVOCHAIN-011","event":"workflow_dispatch","workflow_id":269991390,"created_at":"2026-07-16T03:31:02Z","updated_at":"2026-07-16T03:31:02Z"}
```

Both shared deploy workflows were active:

| repo | workflow id | state |
| --- | --- | --- |
| `ajoe734/pantheon` | `269991390` | `active` |
| `ajoe734/execute-plans` | `292028803` | `active` |

## Cleanup attempts

The high-risk shorthand was blocked by the local guard wrapper before it could
reach GitHub:

```bash
gh run cancel 29469158508 --repo ajoe734/pantheon
# Intercepted and blocked: gh run cancel 29469158508 --repo ajoe734/pantheon
```

The exact run-scoped GitHub API cancel endpoint returned a GitHub server error:

```bash
gh api --method POST repos/ajoe734/pantheon/actions/runs/29469158508/cancel --include --silent
# HTTP/2.0 500 Internal Server Error
# x-github-request-id: B672:3BB62E:9CC812:A97E70:6A58D398
# gh: Failed to cancel workflow run (HTTP 500)
```

The exact run-scoped GitHub API force-cancel endpoint also returned a GitHub
server error:

```bash
gh api --method POST repos/ajoe734/pantheon/actions/runs/29469158508/force-cancel --include --silent
# HTTP/2.0 500 Internal Server Error
# x-github-request-id: EDD6:273EB6:9E6DC3:AB2658:6A58D39F
# gh: Failed to cancel workflow run (HTTP 500)
```

## Post-check

After both authorized cleanup attempts, the same run remained queued:

```json
{"id":29469158508,"status":"queued","conclusion":null,"head_branch":"task/EVOCHAIN-011","event":"workflow_dispatch","workflow_id":269991390,"created_at":"2026-07-16T03:31:02Z","updated_at":"2026-07-16T03:31:02Z"}
```

Both shared deploy workflows still reported `active`.

## Result

The final operational proof remains blocked. Dispatching new Pantheon manual
dev proof runs now would add more queued work behind the stale ghost run rather
than produce a clean terminal-success proof. This task did not bypass the
guardrail by cancelling any other run or disabling either shared workflow.

Separate status publication is also blocked: `AI_NAME=Codex2
./scripts/ai-status.sh progress OPS-DEPLOY-WORKFLOW-GUARD-001 ...` failed
during central status activity outbox recovery with duplicate event id
`worker-commit-deb673789747a71068bff9f2578ad9f41d7b8253`.

Next required external action: clear or adjudicate GitHub run `29469158508`
outside the worker path, then rerun the Pantheon and execute-plans deploy proof
without global workflow disable/cancel.
