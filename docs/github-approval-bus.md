# GitHub Approval Bus

## Purpose

GitHub is the mobile-friendly approval surface for Pantheon.

- `ai-status.json` remains the local source of truth.
- `.orchestrator/github_bus.py` mirrors review and blocked tasks into GitHub.
- GitHub Mobile is used for PR review, approvals, retries, and unblock commands.

## Phase 1

Implemented locally now:

1. review task -> auto open/update PR when a non-default head branch exists
2. blocked task -> auto open/update issue
3. GitHub Mobile approve / request changes / comment flows back through orchestrator polling
4. orchestrator writes results back into `ai-status.json` through `scripts/ai_status.py`

## Phase 2

Implemented in the bus/config layer now:

1. labels and command syntax
2. reviewer auto-assign via `github_bus.reviewers`
3. notification throttling by sync hash so unchanged PRs/issues are not rewritten
4. dedicated issue and PR templates

Commands supported on ops issues:

- `/approve TASK-ID`
- `/deny TASK-ID`
- `/retry TASK-ID`
- `/status TASK-ID`
- `/resume AGENT`
- `/recheck TASK-ID`

## Phase 3

Implemented as deployable interfaces:

1. `github_webhook_server.py` receives GitHub webhook events into `.orchestrator/github-webhook-events.jsonl`
2. `github_cloud_relay.py` can push digests and pull remote commands
3. `github_bus.py` consumes webhook events and cloud relay commands through the same apply path
4. config includes GitHub App, webhook, and cloud relay settings under `github_bus.phase3`

## Files

- `.orchestrator/github_bus.py`
- `.orchestrator/github_command_parser.py`
- `.orchestrator/github_webhook_server.py`
- `.orchestrator/github_cloud_relay.py`
- `.orchestrator/github-bus-state.json`
- `.orchestrator/templates/github_ops_issue.md`
- `.orchestrator/templates/github_review_pr.md`
- `.github/ISSUE_TEMPLATE/pantheon-ops-bus.md`
- `.github/PULL_REQUEST_TEMPLATE/pantheon-review-bus.md`

## Config

See `.orchestrator/config.json`:

- `paths.github_bus_state`
- `paths.github_webhook_events`
- `paths.github_relay_state`
- `github_bus.enabled`
- `github_bus.reviewers`
- `github_bus.labels`
- `github_bus.templates`
- `github_bus.phase3.github_app`
- `github_bus.phase3.webhook`
- `github_bus.phase3.cloud_relay`

## Operational Notes

- PR sync requires a non-default local branch to exist.
- When GitHub is offline, the bus backs off using `offline_backoff_seconds` instead of spamming failures.
- Bus actions are written to `ai-activity-log.jsonl`.
- Mobile approvals never become the source of truth directly; they are reconciled back into `ai-status.json`.
