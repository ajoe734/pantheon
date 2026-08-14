# GitHub Approval Bus

## Purpose

GitHub is the mobile-friendly approval surface for Pantheon.

- `ai-status.json` remains the local source of truth.
- `.orchestrator/github_bus.py` mirrors review and blocked tasks into GitHub.
- GitHub Mobile is used for PR review, approvals, retries, and unblock commands.

## Phase 1

Implemented locally now:

1. review task -> bind the exact `task/<TASK-ID>` PR on the repository delivery base; only create a draft review PR when that exact branch is still published and has no PR evidence
2. blocked task -> auto open/update issue
3. GitHub Mobile approve / request changes / comment flows back through orchestrator polling
4. orchestrator writes results back into `ai-status.json` through `scripts/ai_status.py`
5. an already merged task PR -> retain its PR number, head SHA, merge commit, merge time, and delivery base as immutable review evidence instead of opening a synthetic integration PR

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

## Files

- `.orchestrator/github_bus.py`
- `.orchestrator/github_command_parser.py`
- `.orchestrator/github-bus-state.json`
- `.orchestrator/templates/github_ops_issue.md`
- `.orchestrator/templates/github_review_pr.md`
- `.github/ISSUE_TEMPLATE/pantheon-ops-bus.md`
- `.github/PULL_REQUEST_TEMPLATE/pantheon-review-bus.md`

## Config

See `.orchestrator/config.json`:

- `paths.github_bus_state`
- `github_bus.enabled`
- `github_bus.delivery_base_branches`
- `github_bus.reviewers`
- `github_bus.labels`
- `github_bus.templates`
- `branch_workflow.dev_branch`
- `branch_workflow.task_branch_prefix`

## Operational Notes

- Pantheon ReviewBus derives its primary delivery base from `branch_workflow.dev_branch`; per-repository `github_bus.delivery_base_branches` entries override that value.
- The legacy `github_bus.default_branch` value is not a ReviewBus delivery-base authority. In particular, `master` is the promotion/production branch and must not be used to review a task delivered to `dev`.
- Review scope is always the configured `task_branch_prefix` plus the exact task ID. ReviewBus never substitutes the supervisor's current branch or an owner's shared branch.
- Before creating anything, ReviewBus queries all GitHub PRs for that exact task branch. Explicit task metadata remains authoritative; otherwise the published task-branch head selects the exact PR when follow-up deliveries produced more than one merged PR. If that remote ref is gone, a unique PR head on the configured delivery base still binds the review even when an integrator advanced it past the worker's stale local ref. With no PR evidence, ReviewBus resolves the published branch head before considering draft creation.
- A merged candidate on the delivery base is recorded in `.orchestrator/github-bus-state.json` with `evidence_kind: merged_task_pr`, `base_branch`, `head_sha`, `merge_commit`, `merged_at`, PR number, and URL. No replacement PR is created.
- An open candidate on the delivery base is bound as `open_task_pr` and becomes the PR polled for review actions.
- If exact task evidence targets a legacy or otherwise unexpected base, ReviewBus records `skipped_base_mismatch` plus candidate details and refuses to create a synthetic broad PR. Explicit-head mismatches, multiple commits without an exact published/task head SHA, multiple matching PRs, closed-unmerged evidence, and incomplete or missing merge/head identities also fail closed with actionable activity-log diagnostics.
- Draft PR creation is limited to an exact, published task branch with a known head SHA and commits ahead of `origin/<delivery-base>`.
- When GitHub is offline, the bus backs off using `offline_backoff_seconds` instead of spamming failures.
- Bus actions are written to `ai-activity-log.jsonl`.
- Mobile approvals never become the source of truth directly; they are reconciled back into `ai-status.json`.
