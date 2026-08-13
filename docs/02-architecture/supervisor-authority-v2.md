# Supervisor Authority V2

Status: authoritative target specification

Owner: Pantheon control plane

Last updated: 2026-08-11

## 1. Purpose

Supervisor Authority V2 is a simplification of the existing control plane. It
does not add a second supervisor, scheduler, task database, or recovery daemon.
It establishes one authority for each decision, removes incumbent/rewrite dual
paths, and keeps slow external I/O outside state locks and the scheduling hot
path.

The target system has seven responsibilities:

1. **TaskStore** owns task lifecycle state, current assignment events, the
   current authoritative head, and its append-only audit journal.
2. **Dispatch Planner** is a pure computation over one task snapshot, one
   runtime snapshot, dependency results, and cached account health.
3. **Delivery Queue** durably records an approved launch intent and its delivery
   attempt. It does not decide eligibility, assignment, or policy.
4. **Worker Manager** owns process identity, task worktree, execution lease,
   heartbeat, progress, and exact-generation termination.
5. **Account Health** owns normalized auth, quota, retry-at, and capacity for a
   real provider account shared by one or more configured agents.
6. **Recovery** maps an observed inconsistency to exactly one corrective action.
7. **Direct V2 Replacement** stops the outgoing supervisor and installs one
   immutable V2 candidate while retaining existing V2 worker leases.

There is no supervisor-owned chair authority. Explicit governance or review
work is either a Human/Ops action or an ordinary canonical task.

Repository development governance and task-runtime authority are separate
boundaries. Repository rules decide who may change, review, merge, and deploy
Pantheon source. They do not make a task assignment irrevocable and they do not
replace the canonical Human/Ops task-control API.

## 2. Responsibility boundaries

The following ownership rules are normative:

- TaskStore is the only component that may commit task lifecycle or assignment
  state.
- Dispatch Planner never writes state and never performs network I/O.
- Delivery Queue consumes an already approved `DispatchIntent`; it cannot
  substitute another owner, reviewer, provider account, or task generation.
- Worker Manager judges a process by its exact execution lease, PID start
  identity, and generation. It does not reconstruct assignment truth from task
  notes or queue history.
- Provider probes update Account Health. They do not directly block, reopen,
  reassign, or quarantine a task.
- Recovery is the only component allowed to repair a discrepancy between task,
  intent, execution lease, and observed process state.
- GitHub, provider, git, and filesystem discovery run outside TaskStore and
  runtime admission exclusive locks. A short compare-and-swap transaction may
  consume previously gathered evidence.
- No production feature flag may retain a V1 implementation as a fallback
  after its V2 authority is enabled.

### 2.1 Human/Ops authority

An explicitly invoked local Human/Ops actor may create a task, revoke or replace
the current owner or reviewer, reopen or supersede a task, and record an audited
note through the canonical TaskStore command API. The repository entry point is
`scripts/human-ops-status.sh`; it is independent of product BFF login, bearer
tokens, control mode, and the supervisor dispatch loop.

The transition runs inside the same TaskStore transaction as worker and
recovery mutations. Assignment changes increment the task generation and
record the actor, reason, old assignment, and new assignment. A revoke never
edits JSON directly, never kills a process by an unverified PID, and never
makes an unrelated generation terminal. If an exact-generation worker is
active, the assignment commit revokes its authority immediately; its exact
lease still blocks new delivery until Worker Manager drains or terminates that
process generation.

The explicit local CLI selection is the Human/Ops authorization boundary. It
does not grant owner/reviewer-only implementation actions such as progress,
handoff, approval, or completion. Product-originated Management AI packets may
still enter through the signed dev bridge, but that optional product ingress
does not control or block local canonical maintenance.

## 3. Task lifecycle

`ready` is not a stored lifecycle state. It is a derived predicate over task
state, dependencies, assignment, capacity, and leases.

`quarantined` is not a lifecycle state. Provider availability belongs to
Account Health; a deterministic task defect requiring intervention is stored as
`blocked` with a structured reason and required action.

The stored lifecycle is:

```text
todo
  └── execution_started ──> in_progress
        └── submit ───────> review
              ├── changes_requested ──> in_progress
              └── approve_exact_head ─> review_approved
                                             └── finalize ──> done

active status ── block ──> blocked
blocked ── reopen ──────> in_progress
active status ── cancel/supersede ──> done + terminal_outcome
```

Owner and reviewer changes are assignment transitions, not hidden lifecycle
transitions. Catalog provenance may make task scope, dependencies, acceptance
criteria, and original assignment immutable. It must not make current runtime
owner or reviewer permanently immutable. Every reassignment records actor,
reason, previous assignment, new assignment, and task generation.

Approval is bound to an exact PR and head SHA. Routing may change after
approval, but finalization is legal only while the reviewed head remains exact.

A non-remembered worker tool approval is also one-shot. It is bound to task,
task generation, tool name, and canonical tool-input digest, then atomically
consumed by the next delivery session. It never installs a temporary global
Claude allow rule and cannot authorize a second matching invocation.

## 4. Global invariants

1. Every lifecycle change passes through one transition API.
2. Every assignment change passes through one assignment transition API.
3. A task generation has at most one active execution lease.
4. A task generation has at most one nonterminal delivery intent.
5. A task cannot be reported `in_progress` solely because a queue row exists.
6. A process cannot remain authoritative solely because a task note or activity
   event resembles its assignment.
7. Account auth or quota failure never increments a task failure streak.
8. `enabled=false` means disabled; numeric capacity `0` always means no slots.
9. No external command or network request executes while a task-state or
   runtime-admission exclusive lock is held.
10. A persisted task removal is rejected unless the task was already terminal
    or an exact audited drain marker authorizes the live identity removal.
11. Runtime health reports identity, liveness, readiness, and progress
    independently. A held lock cannot substitute for exact process identity.
12. A V2 authority has one production path. Shadow comparisons are temporary
    release evidence and are removed at cutover.
13. Human/Ops may revoke or reassign current runtime ownership through an
    audited TaskStore transition; catalog provenance cannot disable it.
14. Repository source/deployment governance cannot be used as runtime task
    ownership or lease authority.
15. Retry never launches a worker. It returns the existing intent to the one
    delivery queue, where all current eligibility and capacity gates run again.
16. Private source-packet signing keys belong only to local development
    tooling; supervisor, worker, git, and product-BFF subprocesses cannot
    inherit them.
17. The source-packet replay ledger is bounded and belongs only to the local
    dev-bridge ingress; local Human/Ops maintenance needs no signing ledger.
18. A non-remembered tool approval permits exactly one matching invocation in
    the exact task generation and never mutates global provider settings.
19. A cycle refreshes `last_successful_loop_at` only if poll, plan, reserve,
    delivery, and finalization all completed; a critical phase exception stamps
    the cycle degraded and cannot satisfy promotion freshness.
20. Persistent watchdog restart does not require a development bridge verifier
    map. Local packet materialization loads its verifier only at the local
    development-tooling boundary.

## 5. Authoritative runtime call path

Each supervisor cycle has one execution path:

1. Read one canonical TaskStore head, one runtime head, the durable delivery
   queue, cached Account Health, and local process identities outside exclusive
   locks.
2. Run the pure Dispatch Planner over those immutable snapshots. It emits
   accepted intents and a rejection reason for every rejected candidate. The
   diagnostic CLI serializes this same result; it has no copy of the policy.
3. In one short runtime compare-and-swap transaction, reject stale plans and
   reserve at most one nonterminal intent per task generation.
4. Delivery Queue revalidates the current task generation, assignment,
   dependencies, account health, global/account/agent capacity, and absence of
   an active lease immediately before launch.
5. Worker Manager creates the worktree and process outside the runtime lock,
   then compare-and-swap commits the exact PID start identity, generation, and
   lease receipt. A failed receipt terminates only that exact process identity.
6. Worker polling reconciles the exact lease and process. Retry returns the
   intent to `queued` with bounded backoff; it never directly calls launch.
7. Slow GitHub, provider, bridge, archive, dashboard, and rollout observation
   runs after launch and updates evidence for the next cycle.
8. Recovery consumes durable discrepancies in a bounded pass. It does not
   create an alternate dispatcher.

There is no chair dispatch lane, discussion-planning mode, self-claim path,
helper claim, priority-preemption launch path, or retry/fallback direct launch.
Planning and governance work that needs execution is represented as an ordinary
canonical task and uses this path.

The hot-path acceptance budget is: no external I/O under either exclusive
lock; planning plus intent reservation p95 below 500 ms on the production-size
board; and a healthy queued intent reaches process launch attempt within one
cycle without waiting for GitHub or provider refresh I/O.

## 6. TaskStore V2

### 6.1 Physical files

For a configured event log `/runtime/task-state-events-v2.jsonl`, TaskStore uses:

| File | Authority |
|---|---|
| `task-state-events-v2.jsonl` | Append-only transition-delta audit journal |
| `task-state-events-v2.jsonl.head.json` | Atomic current-head snapshot used by hot reads |
| `task-state-events-v2.jsonl.lock` | Single writer/shared reader lock domain |
| `task-state-events-v2.jsonl.legacy-anchor.json` | Optional immutable V1 archive anchor |

The head is authority for the current state and is cryptographically bound to
the journal event at its recorded offset. The journal is authority for audit
history and crash-tail recovery. The legacy anchor is not consulted during a
hot read.

### 6.2 Delta event

Every journal record contains:

- format version and event type;
- monotonic sequence;
- commit timestamp and source;
- previous event digest;
- previous and resulting state digests;
- optional immutable V1 archive-anchor digest;
- deterministic delta;
- event digest and derived event ID.

The journal record does **not** contain the complete resulting board.

Top-level mappings are represented as deterministic `set` and `remove`
operations. A well-formed task list is represented by task identity:

- `upsert`: complete rows that were added or changed;
- `remove`: identities removed in this transition;
- `order`: task identity order, only when order changed.

Malformed or duplicate-identity task containers fail closed to a generic value
replacement. This is an integrity boundary, not a compatibility path, and is
not a supported steady-state representation.

The first V2 event is a genesis delta from `{}` to the current authoritative
V2 state.
It necessarily contains enough information to reconstruct that state. Every
subsequent routine task update contains only changed rows and metadata.

### 6.3 Current head

The head contains:

- the complete current state and its digest;
- the last event identity and raw event;
- journal byte offset at that event boundary;
- sequence and archive-anchor digest;
- a digest over the entire head record.

The head is written to a unique sibling temporary file, `fsync`ed, atomically
replaced, and followed by a parent-directory `fsync`.

### 6.4 Commit protocol

A writer holds the TaskStore exclusive lock for this sequence:

1. Read and validate the current head.
2. `pread` and validate only bytes after the head offset.
3. Repair the head from complete validated tail events left by a prior crash.
4. Truncate only an unterminated trailing fragment left by an interrupted
   append. A complete invalid JSON record or invalid digest is corruption and is
   never silently discarded.
5. Validate live-task removal and the caller's state transition.
6. Build and self-validate the deterministic delta event.
7. Append the event, then `fsync` the journal.
8. Atomically replace and `fsync` the new current head.

Journal-first ordering guarantees that a visible head never names an
unjournaled state. If the process dies after step 7, the next reader replays the
small tail in memory and the next writer repairs the head before appending.

An identical state digest is idempotent and creates no new event.

### 6.5 Hot-read protocol

A normal reader holds the shared store lock and performs:

1. Read and self-validate the small current head.
2. `stat` the journal.
3. If journal size equals the head offset, return the head state.
4. If the journal is longer, `pread` only the tail and validate its hash chain,
   state digests, deltas, and archive binding.

A hot read never:

- maps or hashes the historical journal prefix;
- reads the V1 archive;
- rewrites a checkpoint;
- performs GitHub, provider, or git I/O.

`refresh_checkpoint` remains temporarily as a call-signature compatibility
argument. V2 has no checkpoint. `False` means a strictly observational read
that requires an already provisioned journal, head, and lock and creates
nothing.

### 6.6 Full audit

Full V2 chain verification is explicit and offline:

```bash
python3 scripts/verify_task_state_store.py \
  --event-log /runtime/task-state-events-v2.jsonl \
  --status-file /status-root/ai-status.json \
  --full-replay --json
```

Archive-byte verification is separately explicit because the legacy journal
may be multiple gigabytes:

```bash
python3 scripts/verify_task_state_store.py \
  --event-log /runtime/task-state-events-v2.jsonl \
  --status-file /status-root/ai-status.json \
  --verify-archive --json
```

No environment variable can silently switch production hot reads back to full
replay.

## 7. V2-only deployment posture

TaskStore V2 is the only supported live store. There is no V1 migration
command, dual-store mode, fallback reader, or rollback path in the runtime.

An already-present legacy archive anchor is immutable historical audit metadata
only. It is never a launch input, a hot-read dependency, or a route back to a
prior runtime. A V2 process configured with a non-V2 journal fails closed.

## 8. Failure semantics

| Failure window | Required outcome |
|---|---|
| Before journal append | No state change |
| Partial unterminated append | Head remains authoritative; next writer truncates fragment |
| Complete append before head replace | Reader replays only tail; next writer repairs head |
| Head temporary write failure | Journal event remains recoverable; old head remains valid |
| Head digest mismatch | Fail closed; do not fall back to journal scanning in hot path |
| Complete malformed tail record | Integrity failure; do not truncate silently |
| Journal shorter than head offset | Integrity failure |
| Non-V2 journal supplied to V2 | Fail closed; correct the V2 binding rather than converting at runtime |
| Historical archive missing | Hot state remains readable; an explicit historical audit fails |
| Archive content changed | Explicit archive audit reports digest/size mismatch |
| Owner/reviewer auth terminal or quota terminal | Account Health pauses that account; bounded Recovery may CAS reassign current role; task failure count is unchanged |
| Auth state unknown or probe stale | Fail closed for new delivery; do not reassign until durable terminal evidence or Human/Ops action |
| Retry becomes due | Existing intent returns to `queued`; the sole Delivery Queue performs full revalidation |
| Process missing with active lease | Recovery closes the exact lease, preserves evidence, and requeues or blocks according to bounded retry policy |
| Stale runtime says worker is running but PID identity is absent | Recovery closes that exact stale lease; it does not infer that the task is in progress |
| Human/Ops revokes assignment with queued intent | Assignment CAS invalidates the matching intent generation before the new assignment is dispatchable |
| Human/Ops revokes assignment with active worker | Assignment CAS revokes the old actor; the exact old lease blocks new delivery until Worker Manager drains/terminates it |
| GitHub approval head differs from current PR head | Approval is invalid; task remains in review and no finalize intent is emitted |
| Worker exists at supervisor replacement | The new V2 supervisor restores the existing exact lease; it does not clear, recreate, or requeue it |

## 9. Deletion map

TaskStore V2 removes these production mechanisms rather than retaining them as
fallbacks:

- full board embedded in every journal event;
- mmap of the complete journal on current-state reads;
- complete-prefix SHA-256 on each cache generation;
- mutable checkpoint sidecar and checkpoint refresh on shared reads;
- process-local snapshot cache keyed to whole-journal stat changes;
- `PANTHEON_TASK_STATE_STORE_FULL_REPLAY` production behavior switch;
- second whole-journal readback after an append;
- V1 validation paths inside V2 hot reads.

Supervisor V2 also removes, rather than disables or shadows:

- chair settings, chair artifacts, chair mutation executors, chair queue events,
  chair workers, and chair capacity bypass;
- discussion-planning runtime mode, baton/readout dispatch, automatic planning
  materialization, and planning workers;
- manual `--claim-agent`, task release, helper-claim, self-claim, alternate
  file-inbox launch, and direct retry/fallback launch;
- priority-preemption and underutilization sidecar scheduling paths;
- task `quarantined` status, retry-quarantine state, and provider failure
  projection into task lifecycle;
- mutable-incumbent/bootstrap rollout flags, live-source execution, shadow
  authority, dual-store write modes, and permanent legacy schema aliases;
- duplicated dispatch policy in `scripts/explain_dispatch.py`;
- legacy per-agent capacity maps and inference of provider accounts from names.
- `disabled_agents` as a second enablement switch; logical-agent
  `max_parallel=0` is the sole configured stop;
- `ai-status wave`, its assign-time wave guard, chair-wave health checker, and
  one-off phase/wave dispatch scripts that created a parallel control plane;
- dated twelve-loop remediation dispatch code whose checkpoint semantics were
  incompatible with the authoritative current-head TaskStore.

The only retained recovery actions are: exact lease/process reconciliation,
queue-owned bounded retry/backoff, Account Health pause/reopen from fresh
evidence, one bounded assignment recovery transition, and one-shot exact-head
review redispatch. Each action is idempotent and compare-and-swap bound.

The following interfaces remain intentionally:

- `load_snapshot` for current-state consumers;
- `snapshot_transaction` for callers performing multiple commits under one
  writer lock;
- `append_state_commit` while callers migrate to typed transition requests;
- `load_events` and `project_latest_state` for offline diagnostics only;
- the nonterminal task disappearance guard and audited drain marker;
- projection parity reports.

`load_events` materializes derived state in returned in-memory event objects for
old offline diagnostics. Those complete states are never persisted in V2.

## 10. Acceptance criteria

TaskStore V2 is acceptable only when all of the following pass:

- a one-row task update produces a transition record materially smaller than
  the current board;
- a current-head read performs no journal-prefix read or hash;
- a crash after event `fsync` but before head replacement replays only the tail;
- a partial final append is ignored by readers and truncated by the next writer;
- tampered event, delta, head, sequence, previous digest, state digest, or
  archive binding fails closed;
- live task disappearance remains rejected unless exactly audited;
- identical state writes remain idempotent;
- the explicit full audit matches the current head and projection;
- normal verification exposes replayed tail count so crash residue cannot be
  hidden behind an overall green result.

At integration level, TaskStore performance acceptance is:

- current-head read p95 below 100 ms for the production-size board on local
  runtime storage;
- TaskStore exclusive lock p95 below 500 ms for a one-task transition, excluding
  caller work which is forbidden inside the lock;
- runtime cost independent of legacy archive size;
- no call to full audit or archive verification from supervisor scheduling,
  queue processing, worker polling, or `ai-status` mutation paths.

Integration acceptance additionally requires:

- planner and diagnostic results are derived from the same pure decision
  records;
- capacity `0`, shared-account caps, auth terminal, quota
  terminal, stale auth, dependency changes, assignment changes, and active
  leases are revalidated at delivery;
- fault injection proves no crash window can create two active leases or two
  nonterminal intents for one task generation;
- provider/auth/quota failures do not increment task failure streaks;
- Human/Ops revoke/reassign works for queued, active, review, and stale-worker
  cases with a complete audit record;
- V2 replacement refuses a mutable source, invalid V2 binding, invalid runtime
  identity, or a failed fresh-loop smoke check;
- the complete supervisor, ai-status, rollout, health, and deployment
  suites pass with no legacy expectation hidden or skipped.

## 11. Direct V2 replacement

The deployment unit is one immutable V2 command runtime and one authoritative
V2 task-state binding. Replacement is intentionally simple:

1. Stop the outgoing supervisor. Active worker records and leases remain in the
   V2 cache; they are not adopted, cleared, or requeued by replacement.
2. Install the reviewed V2 config atomically.
3. Start the persistent V2 watchdog/supervisor. Every start restores the same
   V2 cache and its leases under one atomic runtime-state update lock. A missing
   cache initializes an empty V2 state; any malformed cache fails closed. Do
   not convert old fields or touch canonical task state.
4. Require fresh successful loops, exact command-root identity, and
   delivery-health visibility before accepting the replacement.

There is no in-place V1 recovery, rollback, cache reset, or mixed-writer mode.
The watchdog may restart a failed V2 supervisor only through this same
lease-preserving V2 path; it never resets cache. An invalid V2 configuration or
runtime cache remains non-dispatching and fails closed until corrected.

## 12. Implementation traceability

This specification maps to:

- `.orchestrator/rewrite/task_state_store.py` — V2 head, delta journal, crash
  recovery, transition validation, audit, and archive-anchor verification;
- `scripts/verify_task_state_store.py` — hot parity verification and explicit
  offline audits;
- `.orchestrator/rewrite/test_task_state_store.py` — storage, integrity,
  crash-window, size, and removal invariants;
- `.orchestrator/runtime_state.py` — V2 runtime cache and durable delivery
  queue projection. Every restart preserves leases under one atomic update
  lock. It is not task authority;
- `scripts/test_verify_task_state_store.py` — operational audit contracts.

Scheduler, queue, worker, review, account-health, recovery, and rollout changes
must obey Sections 1–4 and integrate through this TaskStore contract. They must
not reintroduce lifecycle writers or a full-state journal outside this module.
