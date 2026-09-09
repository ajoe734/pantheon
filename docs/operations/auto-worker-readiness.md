# Auto Worker Readiness

Supervisor Authority V2 separates configured capacity from observed account
health. No historical provider matrix in this document is runtime truth.

## Authorities

- `agents.<id>.max_parallel` is the sole logical-agent capacity. `0` is the
  configured stop for that lane.
- `providers.<id>.account` is the sole account identity.
- `ready_dispatcher.max_concurrent_per_account` is the sole account cap.
- `ready_dispatcher.max_concurrent_workers` is the fleet-wide cap.
- `worker_slots` describes physical delivery topology; it does not create
  capacity.
- A fresh provider probe may clear a runtime auth/quota pause. A missing or
  stale probe never proves health and never triggers task reassignment.

The retired `disabled_agents`, `max_tasks_per_agent`,
`max_tasks_per_agent_by_agent`, provider account aliases, and
`max_concurrent_per_quota_group` fields are invalid in a running V2 config.

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

Terminal auth, terminal quota, unknown-agent, or configured-zero-capacity
assignments may be changed by the bounded recovery reconciler. Temporary
capacity pressure, probe timeout, stale cache, and ordinary worker failure do
not change task assignment.

Human/Ops may always correct a current owner/reviewer through canonical
`ai-status assign`; repository branch/PR/check governance does not grant or
revoke that runtime authority.

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
