# Approval Broker Risk Classification & Lifecycle

Status: operating rule for `.orchestrator/permission_broker.py` and
`.orchestrator/approval_queue.py`.
Last updated: 2026-07-18

This file documents the deterministic permission broker's risk-class
buckets and the full lifecycle of a pending approval, so future changes
to `.orchestrator/permission_broker.py` / `.orchestrator/approval_queue.py`
don't reintroduce the 2026-07-17 `suspended_approval` incident (see
`OPS-APPROVAL-BROKER-RISK-CLASS-001`): three `claude` worker slots hung
for 5-8.7h because `TaskOutput` / `Agent` requests fell through to
`risk_class: unknown` (indefinite manual pending) and the stale-pending
prune never applied to approvals bound to a task/worker.

## Risk classes

`permission_broker.evaluate_tool_request()` returns a `risk_class` for
every tool call. Buckets, in evaluation order:

| risk_class | Tools / pattern | Decision | Notes |
|---|---|---|---|
| `safe_read` | `SAFE_TOOLS` (`Read`, `Grep`, `Glob`, `LS`, `Task`, `TodoRead`, `TodoWrite`, `ReadNotebook`, `ToolSearch`); an `Agent` request whose description/prompt is scoped to read-only exploration/review (see `_evaluate_agent_request`) | allow | Never touches the filesystem or network. |
| `harness_orchestration_read` | `HARNESS_ORCHESTRATION_READ_TOOLS` (`TaskOutput`, `TaskGet`, `TaskList`, `Monitor`, `CronList`) | allow | Harness-internal polling/query tools — background task output, task metadata, cron schedule listing, event stream monitors. No filesystem or network side effect. Kept as a distinct bucket from `safe_read` so the approval evidence trail still shows "harness polling" rather than "repo read". |
| `repo_write` | `EDIT_TOOLS` (`Edit`, `MultiEdit`, `Write`) targeting a path inside an allowed workspace root | allow | See `_allowed_workspace_roots()` / `_paths_within_workspace()`. |
| `out_of_workspace` | `EDIT_TOOLS` targeting a path outside every allowed workspace root | deny | |
| `safe_bash` / `destructive_bash` / `needs_review` | `Bash`, classified via `classify_command()` (SAFE/DEFER/DENY bash pattern lists) | allow / deny / defer | See `classify_command()` for the full pattern tables (test/build commands, docker probes, package inventory, git stage/commit/push flows, etc.). |
| `repo_finalize_git` | `git add` / `git commit` / `git push` issued during an `owned_finalize_dispatch` for a task that is `review_approved` and owned by the requesting agent | allow | See `_finalize_git_decision()`. |
| `network` | `NETWORK_TOOLS` (`WebFetch`, `WebSearch`) | defer | Always requires a human/chair decision. |
| `unknown` | Anything not matched above (mutating harness tools like `TaskCreate`/`TaskUpdate`/`TaskStop`, an `Agent` request with an unsafe marker, an unrecognized tool) | defer | Falls into the approval queue as a `pending` item. |

### Why `TaskCreate` / `TaskUpdate` / `TaskStop` are NOT in the safe bucket

They mutate orchestration state (spawn a new task, edit one, kill a
running background task) rather than only reading it. Unlike
`TaskOutput`/`TaskGet`/`TaskList`/`Monitor`/`CronList`, they are not
purely observational, so they keep going through the normal
`unknown` -> pending -> human/chair review path. Do not widen
`HARNESS_ORCHESTRATION_READ_TOOLS` to include them without a
corresponding review of what damage a wrong auto-allow could do.

### Agent subagent_type matching

`_evaluate_agent_request()` first rejects any request whose combined
`description`/`prompt` text contains an unnegated unsafe action marker
(`edit`, `write`, `commit`, `push`, `implement`, ...). Negated safety
instructions such as `do not edit`, `do not fix`, or read-only noun
contexts such as `merged change` / `relevant commit` do not count as
mutation requests. If no unsafe action marker remains, it allows the
request when either:

- `subagent_type` normalizes (lowercase, `-`/`_` collapsed to spaces)
  to something containing `explore` or `review` — e.g. `Explore`,
  `code-review`, `code-reviewer` all match; or
- the combined text contains one of `SAFE_AGENT_MARKERS` (`verify`,
  `find`, `report`, `audit`, `review`, ...).

The hyphen/underscore normalization was added because a real spawn
request with `subagent_type: "code-review"` did not match the
original exact-match `{"explore", "review"}` set during the 2026-07-17
incident.

## Approval lifecycle

Every pending approval created by `approval_queue.create_approval()`
moves through exactly one of these paths. `stale_pending_seconds`
(config key `approvals.stale_pending_seconds`, default `1800`) bounds
every path except manual resolution.

1. **Manual resolve** — `approval_queue.py allow|deny <approval_id>`
   (CLI, HTTP `/approvals/<id>/allow|deny`, or chair-review UI) moves
   the item from `pending` to `history` with `decision: allow|deny`.
2. **Orphan prune** — `prune_stale_approvals()` denies an item
   immediately (no time threshold) when its bound worker state is
   gone, OR the worker's pid is dead and it isn't a resumable Claude
   session (`session_id`/`resume_token` present on a `claude*`
   provider). See `_orphaned_worker_note()`.
3. **Stale prune** — `prune_stale_approvals()` denies ANY `pending`
   item once `now - created_at >= stale_pending_seconds`, regardless
   of whether it carries a `task_id`/`worker_run_id` or belongs to a
   live/resumable worker. This is the fix for the 2026-07-17 incident:
   previously `_is_stale_pending()` excluded any item with a
   `task_id` or `worker_run_id`, which is effectively every real
   approval — the time-based prune was dead code for the exact items
   that caused the outage. See `_is_stale_pending()`.
4. **`expires_at`** — every new approval gets a default
   `expires_at = created_at + stale_pending_seconds` (via
   `_default_expires_at()`) unless the caller supplies one. This is
   informational only (dashboards/chair-review can show when an item
   will time out); the prune loop always recomputes staleness from
   `created_at` and the *current* config value, not from the stored
   `expires_at`.

`prune_stale_approvals()` runs near the start of each supervisor loop
(`_run_once_locked()` in `.orchestrator/supervisor.py`). The promoted
immutable supervisor config owns the normal 300s watch cadence;
`scripts/run-supervisor.sh` is restricted to isolated repository-local runs.
Do not change the promoted cadence as part of approval-broker work because it
is an intentional fleet guardrail. Under the normal loop, stale approvals are therefore
bounded by `stale_pending_seconds` plus at most one poll interval.

A denied approval feeds back into `poll_workers()`: a
`waiting_approval` / `suspended_approval` worker whose latest resolved
approval is `decision: deny` is marked `failed`, and its queue event is
finalized as `failed`. This approval-deny branch does **not** record a
task failure streak by itself. The redispatch path is:

1. `prune_event_queue()` removes the failed event when the task is
   still in a redispatchable status (`todo`, `in_progress`, `review`,
   or `review_approved`, as configured).
2. `dispatch_ready_tasks()` may enqueue a new event for the same task
   once the task is eligible and either the dispatch signature changed
   or `ready_dispatcher.unchanged_task_cooldown_seconds` has elapsed
   (default 900s; tests may set it to `0` to prove immediate
   redispatch).
3. `maybe_reassign_tasks_from_failure_streaks()` only participates
   when a separate worker-failure path has recorded a failure streak;
   stale-pruned approval denial alone does not create that streak.

In other words, a stale-pruned approval does not just clear the queue
entry — it fails the suspended worker, releases the old queue event,
and lets normal ready-dispatch/cooldown policy retry or later reassign
without leaving the worker suspended forever.

## Tests

- `.orchestrator/test_provider_permissions.py`:
  `test_task_output_is_auto_allowed`,
  `test_harness_orchestration_read_tools_are_auto_allowed`,
  `test_mutating_orchestration_tools_still_require_review`,
  `test_read_only_agent_code_review_subagent_type_is_auto_allowed`,
  `test_incident_general_purpose_read_only_agent_request_is_auto_allowed`,
  `test_mutating_agent_request_with_negated_edit_still_requires_review`.
- `.orchestrator/test_approval_queue.py`:
  `test_prunes_pending_approval_after_stale_window_despite_live_worker`,
  `test_prunes_pending_approval_after_stale_window_despite_resumable_claude_session`,
  plus the `expires_at` default assertion in
  `test_create_approval_writes_request_evidence_and_sanitizes_queue_state`.
- `.orchestrator/test_supervisor.py`:
  `test_stale_pruned_suspended_approval_fails_worker_for_cooldown_bounded_redispatch`.
