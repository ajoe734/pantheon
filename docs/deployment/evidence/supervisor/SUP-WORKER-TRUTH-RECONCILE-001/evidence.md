# SUP-WORKER-TRUTH-RECONCILE-001 — Supervisor worker-truth reconciliation

Task: Reconcile supervisor worker truth without config mutation
Owner: Claude · Reviewer: Codex2 · Phase: Supervisor Runtime Repair

Scope rule honoured throughout: **no `.orchestrator/config.json` edit and no
hand-edited task board.** Every behaviour below is decided inside
`.orchestrator/supervisor.py` and every task-state write goes through the
existing locked canonical transaction (`write_status` → authoritative
task-state journal → `sync_status_pipeline`).

## 1. Observed failures and where they came from

### 1.1 A nonthrottling `rate_limit_event` was read as a worker failure

Live worker log
`/home/lupin/pantheon-ci-deploy/dev-root-bdbd0a99bf68/.orchestrator/logs/20260726T185418034797Z-claude1-2-claude1_2-08dcb8.log`
contains exactly one rate-limit record:

```json
{"type":"rate_limit_event","rate_limit_info":{"status":"allowed_warning","resetsAt":1785153600,"rateLimitType":"seven_day","utilization":0.83,"isUsingOverage":false,"surpassedThreshold":0.75},"uuid":"466e8308-da86-4dbd-a188-985b8558a428","session_id":"30c27323-d5f9-41ec-8d84-6ea882f1ba15"}
```

`status` is `allowed_warning` — the request was **served**; only `rejected`
means throttled. `is_allowed_rate_limit_event` accepted the literal string
`"allowed"` only, so the line fell through to
`WORKER_FAILURE_PATTERNS` (`"type"\s*:\s*"rate_limit_event"`),
`detect_worker_failure` returned it as the failure reason, and
`classify_worker_failure` matched no quota/capacity marker and returned
`kind="terminal"` → `FailureResponse.REASSIGN`. A healthy worker was recorded
as failed and its task reassigned away from an owner that never failed.

### 1.2 A fresh not-ready auth probe did not keep the lane unavailable

`refresh_provider_auth_before_dispatch` force-probes the selected provider at
launch time but only mutated the **in-cycle** capability report. The persisted
`provider_capabilities.json` still said `auth_ready: true`, so the next tick's
`build_provider_capabilities` saw `previous.ready is True`, judged the probe not
due (`probe_interval_seconds`, not `failed_probe_interval_seconds`) and reused
the cached ready record. The lane was dispatchable again with no account having
recovered. Separately, an auth dispatch pause that was not marked *sticky*
expired purely on `auth_pause_seconds` wall-clock in
`expire_provider_dispatch_pauses` / `current_provider_dispatch_pause`, and
`reconcile_provider_auth_recovery` accepted a **cached** `auth_ready: true` as
recovery for any non-sticky auth pause.

### 1.3 Ownerless `in_progress` rows were redispatched to their owner forever

At 2026-07-26T19:39Z the live board carried 10 `in_progress` tasks while
`.orchestrator/state.json` held 2 running workers — seven ownerless
`in_progress` rows plus the two live lanes and this task. When an owner worker
merges its delivery and exits, nothing moves the task row off `in_progress`:
`poll_worker_completion_stage` only records a generic-exit failure streak, and
`dispatch_ready_tasks` keeps reading the row as owned work and re-waking the
same owner with `owned_in_progress_dispatch` every cycle, even though there is
nothing left for it to implement.

### 1.4 Queue and lease truth had to stay consistent with that decision

Any supervisor-side reconciliation of an ownerless task must also settle the
worker's queue record and release its lease, or the queue keeps a `started`
record with a `lease_owner` pointing at a dead run id.

## 2. Delivered behaviour

| # | Change | Location |
|---|---|---|
| 1 | `rate_limit_info_payload` / `is_allowed_rate_limit_event` accept both `allowed` and `allowed_warning`, and read the envelope nested under `message` too | `supervisor.py` |
| 2 | `is_allowed_rate_limit_line` gives the same verdict textually, so a truncated stream record cannot be re-read as a failure | `supervisor.py` |
| 3 | `classify_worker_failure` returns a `transient`, never-pausing kind for a nonthrottling notice reached through a stored `last_error` | `supervisor.py` |
| 4 | `auth_pause_requires_live_probe` — an auth pause raised from a fresh live not-ready probe holds the lane until a later **live** successful probe; wall-clock expiry no longer reopens it | `supervisor.py` |
| 5 | `mark_provider_auth_probe_not_ready` records that hold in supervisor state, and the flipped capability report is persisted so the next scan re-probes on the failed-probe interval | `supervisor.py` |
| 6 | `reconcile_provider_auth_recovery` requires a live probe success for every live-probe-gated auth pause (previously sticky-only) | `supervisor.py` |
| 7 | `reconcile_ownerless_in_progress_tasks` — new cycle phase between `prune_event_queue` and `refresh_chair_review_state` | `supervisor.py` |
| 8 | `merged_delivery_commits` / `task_branch_has_unmerged_commits` — durable git evidence via the `Task-ID:` commit trailer on the integration base (task branches are auto-deleted on merge, so the branch ref is exactly what is absent in the merged case) | `supervisor.py` |
| 9 | `_prepare_ownerless_review_handoff_locked` — locked canonical transaction that sets `review`, appends the pending owner→reviewer handoff, emits a `task_ownerless_review_handoff` outbox event, and journals through `write_status` before `sync_status_pipeline` | `supervisor.py` |
| 10 | The same reconciliation calls `finalize_queue_event_record`, so the queue record and lease settle with the task decision | `supervisor.py` |

### Non-interference guarantees

The reconciliation returns without touching a task when **any** of these hold:

- a worker for the task is in an active status, or its pid is still alive;
- a queue record for the task is `queued` / `pending` / `started` / `stalled` /
  `retry_backoff` (a dispatch is in flight);
- no terminal owner-dispatch worker record exists;
- the terminal outcome is not a clean `completed` runner exit;
- no `Task-ID:` trailer commit is reachable from an integration base;
- the task branch still carries commits the base has not absorbed;
- owner or reviewer is missing, or reviewer equals owner;
- the locked re-read no longer shows `in_progress` with the same owner/reviewer.

It is also capped at `max_transitions_per_tick` (default 4) and marks the worker
record so the same task is never handed off twice.

## 3. Verification

Commands run from `.orchestrator/` in the task worktree
`/tmp/pantheon-worker-worktrees/pantheon/sup-worker-truth-reconcile-001`.

### 3.1 Failures reproduced against the pre-fix supervisor

The new tests were run against `HEAD~1:.orchestrator/supervisor.py` (the
supervisor before this task's changes) with the rest of `.orchestrator`
unchanged:

```bash
git show HEAD~1:.orchestrator/supervisor.py > <scratch>/orch/supervisor.py
python3 -m unittest test_supervisor.AllowedRateLimitNoticeTests \
  test_supervisor.FreshAuthProbeLaneHoldTests \
  test_supervisor.OwnerlessInProgressReconciliationTests \
  test_supervisor.MergedDeliveryEvidenceTests
```

Result: `Ran 23 tests` → `FAILED (failures=4, errors=15)` — 19 of 23 fail.
The four behavioural failures on pre-existing API are:

- `test_allowed_warning_event_is_not_detected_as_worker_failure`
  → returned the live `allowed_warning` line as a failure reason;
- `test_truncated_allowed_warning_line_is_not_a_worker_failure`
  → same for a truncated record;
- `test_allowed_warning_reason_classifies_nonterminal_and_never_pauses`
  → `'terminal' != 'transient'`;
- `test_probe_gated_auth_pause_survives_its_wall_clock_window`
  → the auth pause expired on its timer and the lane reopened.

The remaining 15 errors are the new reconciliation and evidence surfaces that
did not exist before this task.

### 3.2 Focused suite after the fix

```bash
python3 -m unittest test_supervisor.AllowedRateLimitNoticeTests \
  test_supervisor.FreshAuthProbeLaneHoldTests \
  test_supervisor.OwnerlessInProgressReconciliationTests \
  test_supervisor.MergedDeliveryEvidenceTests
# Ran 23 tests — OK
```

### 3.3 Full supervisor suite

```bash
python3 -m unittest test_supervisor
# Ran 357 tests — OK   (334 before this task, +23 new)
```

### 3.4 Rewrite package suite

```bash
python3 -m unittest discover -s rewrite -p "test_*.py" -t .
# Ran 97 tests — 95 pass; 2 pre-existing collection errors
# (rewrite/test_task_state_runtime_env.py and rewrite/test_task_state_store.py
#  import pytest, which is not installed in this environment — unrelated to this task)
```

## 4. Acceptance mapping

| Acceptance item | Covered by |
|---|---|
| Seven ownerless `in_progress` fixtures reconcile from terminal outcome and durable worker evidence without resetting any live worker | `OwnerlessInProgressReconciliationTests` — seven per-fixture tests plus `test_seven_ownerless_fixtures_reconcile_in_one_pass` |
| Allowed `rate_limit` warning remains nonterminal and redispatchable | `AllowedRateLimitNoticeTests` (6 tests, live payload) |
| Fresh provider `auth_ready` false keeps the lane unavailable until a later fresh successful probe without any config edit | `FreshAuthProbeLaneHoldTests` — asserts the config dict is byte-identical after the whole cycle |
| Merged owner delivery with no implementation remaining transitions through governed review handoff instead of repeated owner redispatch | `test_merged_owner_delivery_moves_to_governed_review_handoff`, `MergedDeliveryEvidenceTests` |
| Authoritative event log projection and queue worker lease state remain consistent under concurrent outcomes | `write_status` journals before the projection write; the locked re-read rejects a raced row; `finalize_queue_event_record` settles the queue record and lease (asserted in fixture 1) |
| Focused supervisor tests reproduce all observed failures and pass | §3.1 / §3.2 |
| Branch, commit, push, PR checks, independent Codex2 review, merge, evidence archive | this task PR |
