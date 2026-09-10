# Auto Worker Readiness

Supervisor Authority V2 separates configured capacity from observed account
health. No historical provider matrix in this document is runtime truth.

## Authorities

- `agents.<id>.max_parallel` is the sole logical-agent capacity. `0` is the
  configured stop for that lane.
- `providers.<id>.account` is the sole account identity.
- `ready_dispatcher.max_concurrent_per_account` is the sole account cap.
- `ready_dispatcher.max_concurrent_workers` is the fleet-wide cap.
  It is required and must be a JSON integer >= 0 (no strings, booleans,
  fractions, null, or unlimited fallback). Watchdog derives its effective
  worker ceiling from this same value: at cap 13, 13 live workers are legal
  and 14 cause worker-count pressure. A cap of 0 admits no new workers.
- `worker_slots` describes physical delivery topology; it does not create
  capacity.
- A fresh provider probe may clear a runtime auth/quota pause. Probe freshness
  does not prove runtime health, and a missing or stale probe does not prove
  health either. Neither immediately triggers task reassignment. However, when
  load balancing is enabled, a qualifying transient signal can start an optional
  guarded load-balance watch.

The retired `disabled_agents`, `max_tasks_per_agent`,
`max_tasks_per_agent_by_agent`, provider account aliases, and
`max_concurrent_per_quota_group` fields are invalid in a running V2 config.

`watchdog.max_active_workers` is retired with OPS-FLEET-CAP-CONTRACT-001;
there is no compatibility window or legacy alias. Remove the field entirely,
even if it equals the dispatcher cap. Missing the retired field is the valid
shape; missing the authoritative dispatcher cap fails closed. Supervisor
startup, watchdog settings, live-config rendering, and the drift CLI all use
the same validator. Drift validation checks both repository and live shapes,
including equally invalid values, and refuses `--fix` writes for invalid
capacity contracts. Repair through a reviewed config and runtime promotion.
The renderer validates the candidate and discards incumbent policy, so an old
live watchdog field cannot survive promotion. Fleet capacity remains 13;
account, agent, task, resource-lock, lease, and admission policies are unchanged.
After independent review and merge, promote the exact accepted runtime and
collect supervisor-health and watchdog dry-run evidence before claiming live
activation. Do not edit the incumbent config or restart leased workers to
prove a source change.

## Antigravity native invocation-log evidence

`agy --output-format stream-json` only emits opaque `step_update`/
`error_message` records on stdout when a request fails mid-turn (the CLI
keeps retrying internally), and `agy --prompt` auth/quota probes can also
authenticate and then hit quota with a clean exit and empty stdout/stderr. In
both cases no error text reaches the worker log or probe output that
`detect_worker_failure`/`_antigravity_probe_ready` scan.

The dispatch adapter (`.orchestrator/adapters/antigravity.py`) and the auth
probe (`.orchestrator/provider_permissions.py`) now bind an explicit
`--log-file <path>` to each exact invocation using the CLI's own
`--log-file` flag. If the worker's stdout stream never carries an
authoritative failure envelope, `detect_worker_failure` falls back to
scanning that bound native log (newest line first, so a later actual
failure supersedes an earlier not-logged-in startup notice) for a
provider-native quota/auth marker; the probe merges the same native log text
into its stdout/stderr classification before deciding readiness. Neither
path introduces a new classifier, cooldown store, or recovery authority: the
existing `classify_worker_failure` terminal-quota markers and the existing
rotation/cooldown logic in `_antigravity_auth_probe` consume the recovered
text unchanged. The probe deletes its own bound native log after reading it
each cycle so transient probe logs do not accumulate.

## Dispatch semantics

The planner consumes one canonical task snapshot, one runtime lease/queue
snapshot, and cached provider health. It reserves an intent only when global,
account, agent, lifecycle, assignment, dependency, and duplicate-intent gates
all pass. The delivery queue revalidates those facts immediately before the
only worker-launch call.

### Automatic recovery lanes and safeguards

The supervisor provides bounded automatic recovery across three distinct
lanes. None of these lanes launches workers directly. Automatic reassignment
actions across all three lanes commit through canonical `persist_task_reassignment`
(respecting generation fences and lease locks), after which the normal planner
evaluates dispatch on a subsequent pass. While the design includes an escalation
hold tier via `record_failure_loop_blocker` (intended to commit via a locked
`BLOCK` transition, status outbox, and sync path rather than
`persist_task_reassignment`), in the current live runtime implementation that
escalation call is a no-op that returns `None` before any status mutation or write:
while `supervisor.py:9001` is the separate missing-handoff blocker path, only
`supervisor.py:9107` is the failure-loop escalation path (`_prepare_failure_loop_blocker_locked`);
line 9107 passes the `TaskAction.BLOCK` Enum object directly into
`rewrite_task_machine.transition()`, for which `coerce_action` returns `None`
and causes `transition()` to raise `TransitionError`. The internal handler
catches `TransitionError` and returns `None`, so no escalation hold is written
pending a separate governed source repair.

1. **Durable unavailability recovery (`reconcile_unavailable_assignments`)**:
   - Master switch: `worker_reassignment.enabled` (default `false`), bounded
     per cycle by `worker_reassignment.max_reassignments_per_cycle`.
   - Triggers when an assigned actor is durably unavailable due to terminal auth,
     terminal quota, unknown agent identity, configured zero capacity
     (`agents.<id>.max_parallel == 0`), or configured no delivery endpoint
     (`configured_no_delivery_endpoint`, where the agent's delivery lane has no
     enabled auto-deliver endpoints configured).
   - Applies to reviewers on tasks in `ready_dispatch.review_statuses` (e.g.
     `review`, provided the reviewer is not an explicit human gate), and owners
     on `todo`, `in_progress`, `review_approved`, or `blocked` tasks (provided
     the task has no active worker lease, no active typed worker-recovery
     receipt, and no explicit recovery hold).
   - Reassigns to candidates configured in `worker_reassignment.reviewer_fallbacks`
     or `worker_reassignment.owner_fallbacks`.
   - Never infers unavailability from a stale or missing probe alone.

2. **Guarded load-balance recovery (`reconcile_unavailable_assignments`)**:
   - Governed by `worker_reassignment.load_balance.enabled` (off by default)
     and `worker_reassignment.load_balance.min_saturated_seconds`, within the
     master `worker_reassignment.enabled` switch.
   - Eligibility: restricted strictly to unleased, not-yet-started work in
     `todo` or `review_approved` status. It never touches tasks that have
     already started under the incumbent (`in_progress`) or tasks on an
     explicit recovery hold (`blocked`), and never touches tasks with an active
     worker lease or active worker-recovery receipt.
   - Evaluates two distinct non-durable conditions:
     - Saturated lane (`assignment_saturated_recoverable`): evaluates current
       occupancy counts against configured capacity (`agent_dispatch_capacity`).
       It triggers when the incumbent owner's current load meets or exceeds
       configured capacity (`len(agent_loads.get(owner, [])) >= capacity > 0`)
       and at least one configured candidate in `worker_reassignment.owner_fallbacks`
       has current load below configured capacity
       (`len(agent_loads.get(candidate, [])) < candidate_capacity`). It evaluates
       occupancy counts only and never inspects provider health, auth, or quota
       (it does not verify that the incumbent is healthy).
     - Transiently blocked lane (`assignment_transiently_blocked_recoverable`):
       evaluates dispatch admission readiness via `agent_can_take_task`. It
       triggers when the incumbent owner cannot take the task (due to a transient
       reason such as a stale/missing health probe, provider retry-after window,
       or unready endpoint/account in the delivery health state) while at least one
       configured fallback candidate satisfies `agent_can_take_task`. (Configured
       zero capacity routes through durable unavailability before load balancing
       is evaluated, so it must not be listed as a transient lane example.) It
       evaluates dispatch readiness/health gates, not current load or spare
       capacity.
   - Safeguards and hold duration: neither condition triggers immediate
     reassignment. Instead, the task enters a supervisor tracking state
     (`load_balance_watch`). The qualifying condition must persist continuously
     for at least `worker_reassignment.load_balance.min_saturated_seconds`. If
     the condition clears before the duration expires, the watch entry is dropped.
   - Reassignment execution: once the continuous configured hold is satisfied,
     candidate fallbacks are sorted by capacity headroom
     (`agent_dispatch_capacity - len(agent_loads)`) descending, and the
     supervisor selects the first candidate satisfying `agent_can_take_task`
     via `plan_task_assignment_pair`. Note that while sorting prioritizes
     candidates with higher headroom, `plan_task_assignment_pair` enforces agent
     validity and health-gate readiness (`agent_can_take_task`), not a guarantee
     of spare capacity at assignment time. Upon successful CAS commit via
     `persist_task_reassignment`, the watch entry is cleared and ordinary
     planner dispatch handles subsequent execution.

3. **Repeated-failure-loop recovery (`reconcile_failure_loops`)**:
   - Governed by `worker_reassignment.failure_loop` keys: `enabled` (off by
     default), `max_failures_in_window`, `window_seconds`, and
     `max_auto_reassignments`, within `worker_reassignment.enabled`.
   - An ordinary single worker failure does not trigger task reassignment.
   - Applies only to tasks in `todo` or `in_progress` without an active worker
     lease, active worker-recovery receipt, or existing explicit hold
     (`waiting_for` must be empty).
   - Tracks recent worker failures as a task-wide rolling count of `worker_failed`
     activity-log events within `window_seconds` (`recent_task_failure_counts`).
     Events are counted strictly by task ID, without owner filtering and with no
     reset when the task owner changes. When a task reaches or exceeds
     `max_failures_in_window` failures within the rolling window:
     - Bounded reassignment tier: if the task has been auto-reassigned fewer
       than `max_auto_reassignments` times (tracked in supervisor
       `failure_loop_watch` state), it is reassigned to the next configured
       fallback candidate in `worker_reassignment.owner_fallbacks` via governed
       `persist_task_reassignment`. Because the failure count is task-wide and
       does not reset on owner change, all failures within `window_seconds`
       continue to count toward the task.
     - Explicit escalation tier: once the auto-reassignment budget
       (`max_auto_reassignments`) is exhausted, automatic reassignment ceases.
       In design, the supervisor escalates on the same retained task count—without
       requiring fresh failures after reassignment—or after further failures,
       placing the task on an explicit `Human/Ops` hold via
       `record_failure_loop_blocker` (which is designed to acquire the canonical
       task state lock, execute an explicit task state machine `BLOCK`
       transition, record `waiting_for = "Human/Ops"`, append an open blocker
       with `blocker_kind = "failure_loop"`, enqueue a `task_failure_loop_blocked`
       activity event in the status outbox, write the status file, and
       synchronize the status pipeline and activity log).
       However, in current live runtime, this escalation tier does not function
       and is a complete no-op: while `supervisor.py:9001` is the separate
       missing-handoff blocker path, only `supervisor.py:9107` is the
       failure-loop escalation path (`_prepare_failure_loop_blocker_locked`).
       Line 9107 passes the `TaskAction.BLOCK` Enum object directly to
       `rewrite_task_machine.transition(...)`, while `task_machine.coerce_action`
       returns `None` for Enum instances, raising `TransitionError`.
       `_prepare_failure_loop_blocker_locked` catches `TransitionError` and
       returns `None` before mutating `task["status"]`, setting `waiting_for`,
       appending an open blocker, enqueuing an outbox event, writing the status
       file, or running `sync_status_pipeline`. `record_failure_loop_blocker`
       thus returns `None` without writing any hold or altering task state.
       Operational recovery and documentation must not claim that this escalation
       tier functions in production until a separate governed source repair
       corrects the transition invocation.

### Authority boundary

These automated recovery lanes reuse existing TaskStore mutations (canonical CAS
reassignment for durable unavailability, load-balance recovery, and bounded
failure-loop reassignment; the escalation tier's locked `BLOCK` transition
currently no-ops due to the `TaskAction.BLOCK` Enum coercion defect pending
governed source repair); they do not introduce a second authority or bypass
active leases. Human/Ops retains supreme authority and may always correct a
current owner or reviewer through canonical `ai-status assign`; repository
branch, PR, or check governance does not grant or revoke that runtime authority.

## Atomic dependency contract maintenance

After the reviewed source is merged and the existing promoter activates that
qualified command runtime, the local operator may revise existing pre-dispatch
dependency sets with:

```bash
"$PANTHEON_COMMAND_ROOT/scripts/human-ops-status.sh" dependency-contract /absolute/request.json
```

Use the provisioned canonical status root, TaskStore journal/identity and
command-runtime bindings. This operation requires explicit local Human/Ops
identity and rejects auto-worker markers. It does not extend worker authority
or the supervisor dispatch batch. The request file is bounded command input;
it is not a queue and must never be copied over canonical task data.

The exact JSON shape is:

```json
{
  "reason": "Explain the complete source ordering change",
  "tasks": [
    {
      "task_id": "EXISTING-TASK",
      "expected_sha256": "<64 lowercase hex characters>",
      "depends_on": ["EXISTING-PREDECESSOR"]
    }
  ]
}
```

Supply 1–32 distinct rows, at most 256 distinct non-self predecessors per row,
and a nonempty reason of at most 4096 characters. The request is limited to
1 MiB; duplicate JSON keys and unknown fields are rejected. `expected_sha256`
is the existing `task_mutation_cas_digest` of the **complete current task row**:
SHA256 of UTF-8 `json.dumps(row, sort_keys=True, separators=(",", ":"),
ensure_ascii=False)`. Read the row from the governed `show` result's `task`
object after pending audit projection recovery. Do not use the worktree mirror
or silently refresh an expected hash after a conflict.

Only admitted nonterminal `todo`/`blocked` ordinary tasks qualify. Every row
must actually change its edge list. The transaction preserves IDs, roles,
scope, acceptance, status, `next`, blockers/holds, delivery/review history and
original signed bridge documents/spec/hash. Retained dependency tracks remain
unchanged; removed edges lose their track; new edges use terminal completion.
Catalog/proof-policy and privileged authorization contracts are unsupported
and fail closed. No grant is derived or signed by this command.

The existing runtime-admission → canonical TaskStore → activity-outbox lock
order covers all rows. Active workers (including approval waits), queue
intents, worktree leases, active pending/reassigned worker recovery, pending
review/finalization, and off-lock phase reservations return `status: busy`,
exit 75, without clearing any authority. A valid terminal held pointer or an
obsolete recovery pointer superseded by the current assignment generation allows
governed dependency revisions while preserving task holds, waiting_for, next,
provenance, and complete recovery receipts. Malformed, unknown, or
future-generation recovery evidence fails closed. Even a phase with an
unrelated current receipt may plan another task before returning, so revision
waits for that existing phase to settle. Retry against fresh observed state; a
timeout or busy result is not a successful revision.

All prospective dependency IDs resolve against active tasks or durable terminal
facts. Validation covers the full graph, including unchanged intermediate
tasks. Unknown historical dependencies on unchanged rows are reported in
`historical_missing_dependencies`, never silently satisfied. Established
terminal ordering of overlapping source writers must survive changed
reachability, including third-party writers. Repository aliases and directory,
recursive and glob grants use the existing overlap helper; uncertain glob
intersections are conservative. Unrelated old unordered overlaps are not an
extra cleanup gate and are reported as `historical_unordered_writers`.
Reversing an overlapping writer pair requires both rows in
the batch; a one-sided deletion cannot release concurrent writers.

Success appends both row changes, their generation increments and one revision
audit payload through one TaskStore commit. Pending activity outbox entries are
carried forward without a separate recovery append. Generation increments use
the existing final launch fence and are not assignment events. The response
includes committed rows, request digest and the journal checkpoint
(`event_count`, `last_event_id`, `state_sha256`). An exact retry matching the
last revision returns `replayed` and performs zero commits; other stale
expected rows fail CAS. Pre-append interruption changes no rows; after append,
readback observes the complete batch even if view refresh was interrupted.

For OPS-DEPENDENCY-CONTRACT-001, root/operator coordination must re-read the
actual FE graph, scopes, current source and launch reservations **after runtime
activation** before considering the DEP/STRICTLIVE reversal. The original plan
keeps DEP's OSS-COVERAGE predecessor, removes its STRICTLIVE/EXACT-PAIR edges,
and adds DEP to STRICTLIVE while retaining MGMT-READ. Apply it only if still
justified. Preserve all protocol/hosted holds and later release requalification.
Record canonical readback plus actual supervisor eligibility and one genuine
owner dispatch; a prepared request or successful CLI test is not dispatch proof.

## Verification

```bash
python3 .orchestrator/doctor.py --json --no-write
python3 scripts/supervisor_runtime_health.py --require-watchdog --json
python3 scripts/check_config_drift.py --live-config /path/to/live/config.json --json
python3 scripts/explain_dispatch.py TASK-ID --json
```

Readiness requires all of the following evidence independently:

- watchdog and exact supervisor process identity are live;
- the promoted runtime reports the expected source identity;
- canonical TaskStore head and journal are valid;
- queue and worker leases reconcile without duplicate task generations;
- the target provider/account is not durably paused;
- configured global, account, and agent capacities are nonzero and available.

Dashboard or terminal sessions are observational conveniences, not liveness or
delivery authority.
