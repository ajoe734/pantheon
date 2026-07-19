# Orchestrator Supervisor — Rewrite Plan

Status: proposal · Date: 2026-07-18 · Author: Human/Ops session (Claude)

This plan is grounded in a full function-level audit of the live control plane
(`supervisor.py` 11,167 lines / 334 functions / 1 class, `common.py` 4,748,
`ai_status.py` 5,478, `planning_state.py` 2,069, `sequencing_gate.py` 1,040).
It was written after a live incident in which a **single missing activity-log
archive file crash-looped the whole supervisor for ~4 hours with zero dispatch,
finalize, or archive** — a direct product of the anti-patterns below.

---

## 0. Diagnosis

The supervisor is not merely "big" — it has **no architecture**. It is a flat
procedural module where every production incident was fixed by adding one more
`if`, one more status string, or one more config flag, and nothing was ever
consolidated. Evidence:

- `poll_workers` is a single **751-line** function doing ~9 unrelated jobs.
- `dispatch_ready_tasks` (373 lines) reads state, **mutates canonical task
  ownership mid-loop**, and reloads status three times in one pass.
- The task lifecycle `todo→in_progress→review→review_approved→done` is
  **re-derived by hand in 5 separate places**; there is no state machine.
- Owner/reviewer/status is mutated from **3 independent subsystems**
  (helper-claim, chair-review, mainline-normalize) → tasks thrash between
  `review` and `in_progress` and never close.
- Concurrency is expressed **4 overlapping ways** (`worker_slots`,
  `max_tasks_per_agent`, `max_concurrent_per_quota_group`, `account_group`) that
  reduce to 2 concepts.
- The one grouping concept ("which agents share an OAuth account") has **3
  config aliases + a capability fallback + a self-fallback** and **3 identity
  fan-out functions**.
- Worker `status` has **16+ stringly-typed values**, not a declared enum, spelled
  three different ways in three functions.
- The activity log — an append-only file written by **every worker action and
  every permission hook** — carries a **content-addressed cryptographic lineage
  that is fully re-validated every supervisor cycle**, on the hot control path,
  with hard `raise` on any mismatch.

---

## 1. The systemic anti-patterns (with evidence)

### A. No failure isolation — one flat `try` runs the whole cycle
`_run_once_locked` (supervisor.py:10887–11009) executes ~30 independent phases
sequentially inside a **single** `try`. Any phase that raises short-circuits
every phase after it. The activity scan (`run_scan`, 10928) sits near the top; a
`RuntimeError` there kills dispatch (10956), finalize/chair-review (10959),
`process_queue` (10962), GitHub-bus sync, worktree GC, and auto-commit. The outer
handler (`run_supervisor_cycle`, 11020–11027) only logs one line and re-enters
the same failing cycle. **This is exactly how one missing file caused a 4-hour
total outage.** Some phases are already fail-soft (`maybe_auto_commit_archive`
8288) — proving the pattern is achievable but was applied ad hoc.

### B. Cryptographic integrity validation of a live-mutating log, on the hot path
`ai-activity-log.jsonl` is rotated into gzip archives with a content-addressed
lineage (`resolutions.jsonl`) recording byte/line conservation + multiple SHA256
digests, re-validated **every cycle** (common.py:~1682 and neighbours). Three
built-in failure modes: (a) a missing archive → hard `raise` → total outage;
(b) `activity log changed during rotation recovery` = a **race** (the log is
written while recovery reads it — guaranteed under an active fleet);
(c) `content-addressed archives do not match lineage` = digest drift. Validating
cryptographic conservation over a file that is appended to many times per second,
inside the control loop, with `raise` on failure, is over-engineering in the
wrong place.

### C. No task state machine — the lifecycle is re-derived 5×
The ladder `todo→in_progress→review→review_approved→done` exists only as string
comparisons duplicated in `dispatch_ready_tasks` (10279), `higher_priority_
ready_task_exists` (9886), `dispatch_priority_for_task` (9673), `current_
dispatch_event_key` (9641), and `worker_matches_current_assignment` (10000).
Transitions are not performed centrally — workers write status files out-of-band
and the supervisor only re-derives eligibility. Meanwhile owner/reviewer/status
is rewritten from 3 places (helper-claim in dispatch, chair-review reassignment,
mainline-normalize), so a task goes `review` → reassign flips it to `todo` →
re-dispatch → `review` again, forever. Nothing counts the round-trips except an
after-the-fact `failure_loop` detector.

### D. Four overlapping concurrency mechanisms = two concepts
`agent_dispatch_capacity` (1162) collapses `worker_slots` and `max_tasks_per_agent`
via `max(cap, len(slots))` — same axis, two config surfaces. `provider_dispatch_
group_id` (982) resolves the account group from `account_group` → alias →
capability → `quota_group` → `dispatch_group` → self — **six ways to name one
key**. Three fan-out functions (`provider_dispatch_identity_ids` 997,
`_provider_auth_identity_ids` 5476, `agent_quota_identity_ids` 1034) expand one
provider to many keys because the storage key and lookup keys are asymmetric.
The real gates are only three: global cap → account cap → per-agent cap.

### E. Worker lifecycle is a 751-line god-function with 16 statuses
`poll_workers` (6984–7732) is one `for` loop with ~18 order-dependent
`if…continue` branches spanning orphan GC, sidecar-file ingestion, log parsing,
`/proc` process-tree probing, lease management, HITL approval flow, supersession/
preemption, a 140-line failure-classify-rotate-pause-reassign branch, and
role-specific completion. Statuses (`waiting_approval` vs `suspended_approval`,
`stalled` vs `fallback` vs `retry_backoff`, `superseded` vs `reassigned`, …)
multiplied because each new production failure got a new state instead of a
uniform model. **Heartbeat ≠ progress**: lease renewal keys off heartbeat
freshness (7058), and expiry requires heartbeat staleness (4842), so a
live-but-hung runner never expires — the exact "hangs but keeps heartbeating"
bug. `terminate_worker_pid` is SIGTERM-and-assume-dead (no confirm/SIGKILL), so
workers get marked `failed` while still running.

### F. "Underutilization sidecars" manufacture make-work
`dispatch_underutilization_sidecars` (10608, 259 lines, 23 supporting functions)
**invents new tasks when the fleet looks idle** instead of fixing why it is idle.
Five stacked throttles (ratio, debounce, dwell, cooldown, chair-approval) are
scars from repeated spawning incidents. It mutates canonical state and re-enters
dispatch, then a `+10` sidecar priority penalty fights those same tasks back
down. This is a primary engine of "the board keeps growing but nothing closes."

### G. Provider/auth: reactive log-scraping, timer-based false-liveness
Auth death is detected **after** a task burns on a dead account, by scraping the
worker log tail (`classify_worker_failure` 4586, 7 kinds × marker lists).
Recovery relies on a hand-maintained `STICKY_AUTH_FAILURE_MARKERS` list (97) and a
`9999-12-31` sentinel; a revoked token whose error wording is not in that list
gets a 900 s timed pause and then the supervisor **resumes dispatching into a
still-dead account**. There is no owner-side pre-dispatch auth probe as a
first-class state.

### H. Event-queue = a redundant third indirection with a third status vocabulary
Dispatch decisions become `evt-<ts>-<hash>` events that `process_queue` (2336)
later turns into worker launches. Queue records carry their own status set
(`manual_pending`/`started`/`queued`/`pending`/…) that must be kept in sync with
worker status AND task status — three parallel vocabularies. `reconcile_queue_
records` (8330) exists solely to repair the drift this indirection creates.

### I. Chair-review = meta-controller buried under path/artifact plumbing
39 functions, of which ~9 are `chair_review_*_path`/`*_dir` helpers plus artifact
sync. The core is a periodic chairman worker emitting a JSON decision that
`apply_chair_review_decision` executes — and it is the **third** owner/reviewer/
status mutator, with a 130-line hand-rolled JSON validator
(`normalize_chair_review_decision` 3317) bundling five unrelated concerns.

### J. Discussion-planning = a parallel copy of dispatch machinery
24 functions duplicating state-load, active/needs checks, dirs/artifacts/paths,
message builder, queue event, and dispatch — a whole second pipeline for one
special task kind instead of a task attribute on the shared machinery.

### K. Live state lives inside the git tree
`ai-status.json` is **both git-tracked and live-mutated**, so a worker's
`git reset --hard`/`git clean` wipes live fleet state (a recurring disaster). The
`next` field is overwritten by any event (no durable history). A 5,478-line CLI
(`ai_status.py`) mutates state per-command with ad-hoc logic and `planning_state.py`
overlaps it.

### L. sequencing_gate.py (1,040 lines, newest) wraps the core load path
The most recent accretion injects a "sequencing release audit proof" into
`load_status` — the function every dispatch decision calls each tick — with two
un-try-guarded calls that can throw on malformed input, i.e. another way to crash
the hot path. It is a governance concern welded onto the state-load path.

---

## 2. Target architecture

Decompose the 25k-line ball of mud into bounded, independently testable, and
independently failing subsystems.

```
orchestrator/
├── core/
│   ├── cycle.py          # declarative phase list; PER-PHASE isolation
│   ├── task_machine.py   # ★ the ONLY task state machine (transition table)
│   └── clock.py          # single time source (kills the 8h clock-skew mess)
├── state/
│   ├── store.py          # ★ single mutation API (single writer)
│   ├── events.py         # append-only event log = source of truth
│   └── projection.py     # read-only board snapshot projected from events
├── dispatch/
│   ├── policy.py         # pure fn: select_dispatches(state) -> [Intent]
│   ├── concurrency.py    # ★ ONE model: 3 integers + 1 account key
│   └── launcher.py       # apply Intents -> start workers (NO event-queue layer)
├── workers/
│   ├── lifecycle.py      # ★ worker state machine; driver < 80 lines
│   ├── progress.py       # ★ OBSERVED progress (new commit) not heartbeat
│   ├── approval.py / lease.py / failure.py / completion.py
│   └── worktree.py       # worktree lifecycle, OFF the hot path
├── providers/
│   ├── registry.py       # ★ one normalized identity + one Account per provider
│   └── health.py         # ★ pre-dispatch auth probe: healthy/degraded/revoked
├── activity/
│   ├── log.py            # append only; NEVER validated on the hot path
│   └── rotate.py         # size-based rotation; integrity = offline tool
├── review/
│   └── policy.py         # chair-review as a thin periodic policy, not 39 fns
└── admin_cli.py          # thin CLI -> state/store.py (replaces 5,478-line file)
```

### Core design principles
1. **Every phase fails in isolation** — one broken subsystem degrades itself, not
   the fleet. Housekeeping (heartbeat, save, dashboard) runs in `finally`.
2. **One task state machine** — `transition(task, event, actor)` is the only way
   owner/reviewer/status changes; helper/chair/normalize become its callers. A
   `bounce_count` forces `BLOCKED`+escalation after N review↔in_progress round
   trips, ending thrash structurally.
3. **No integrity/audit on the hot path** — activity log only appends; rotation is
   size-based with atomic rename; cryptographic verification is a standalone cron
   tool that alerts, never `raise`s in a cycle.
4. **One concurrency model** — `agent.max_parallel`, `agent.account`,
   `account.max_parallel`, global `max_concurrent_workers`. No slots-list, no
   alias resolver, no identity fan-out.
5. **Providers: identity resolved once, health probed explicitly** — one canonical
   id + one `Account`; auth health is a probed `healthy/degraded(retry_at)/revoked`
   state, not log-scraped after failure.
6. **Observed progress, not heartbeat** — lease renewal requires a task-level
   progress signal (new commit / completed tool-calls); fixes "hangs but
   heartbeats." `terminate` is confirm-kill (SIGTERM→wait→SIGKILL→verify).
7. **Live state out of the git tree** — event-log + projection; `git clean` can
   never wipe it; the `next`-overwrite problem disappears (full history in events).
8. **Delete accretions** — sidecar make-work, the event-queue indirection, the
   discussion-planning parallel pipeline (make it a task `kind`), and the
   sequencing_gate-on-load-path (move governance off the hot path).

---

## 3. Per-subsystem rewrite

### 3.1 Control loop (anti-pattern A)
```python
for name, phase in PHASES:
    try: changed |= phase(ctx)
    except Exception as e: record_phase_error(name, e)   # degrade one, not all
finally: stamp_heartbeat(); save_state(); refresh_dashboard()
```
A missing archive → one warning on the `activity_scan` phase; dispatch/finalize/
archive keep running.

### 3.2 Activity log (B) — the biggest over-engineering to delete
Append-only writes never validated on the hot path. Size-based rotation via
atomic rename, no content-addressed lineage, no conservation math. If tamper-
evidence is genuinely required, a standalone `verify_activity_integrity.py` cron
checks it offline and alerts — never inside a cycle.

### 3.3 Task state machine (C, K)
Single transition table; all 5 duplicated ladders become queries against it.
`transition()` is the sole owner/reviewer/status mutator; `bounce_count` kills
thrash. Backed by the event-log store (3.7).

### 3.4 Concurrency (D)
Collapse to 3 integers + 1 `account` key. Delete `worker_slots` list, the
6-way group resolver (982–1018), and all three identity fan-outs. Add a startup
check that OAuth-sharing providers resolve to the same `account`.

### 3.5 Worker lifecycle (E)
`WorkerState` enum + transition table; driver `< 80` lines
(`hydrate → build_context(once) → classify_event → TABLE → apply`). Fold
`waiting/suspended_approval` → one state + `pid_alive`; `stalled/fallback/
retry_backoff` → one `RETRYING` + `reason`. `ProgressMonitor` separates liveness
/ heartbeat / **work-progress**; lease renewal binds to work-progress.
Confirm-kill. Worktree GC off the poll tick.

### 3.6 Providers/auth (G)
One-time id normalization; one `Account` object with probed
`healthy/degraded/revoked`. One `decide_failure_response(account, failure) ->
Rotate|Pause|Retry|Reassign` replaces the ladder duplicated at 2557/7464/8494.
Delete `STICKY_AUTH_FAILURE_MARKERS` + the `9999` sentinel.

### 3.7 State persistence (K)
Move live state out of git (`/var/lib/pantheon/` or SQLite). Source of truth =
append-only event log; board = projection. Mutation API in `state/store.py`; the
CLI becomes a thin wrapper. Solves git-wipe, `next`-overwrite, and parallel-writer
safety at once.

### 3.8 Delete: sidecar (F), event-queue indirection (H), discussion pipeline (J)
- Sidecar: gone. Utilization = reprioritize real backlog, never synthesize tasks.
- Event-queue: dispatch launches workers directly; delete the third status
  vocabulary and `reconcile_queue_records`.
- Discussion-planning: a task `kind` on the shared machinery, not a parallel copy.

### 3.9 Chair-review (I) & sequencing gate (L)
Chair-review becomes a thin periodic policy that emits transitions through the
state machine (not a 3rd mutator); path/artifact helpers collapse to one
workspace helper. The sequencing gate moves off `load_status` into an explicit
dispatch-policy predicate (a governance concern, not a state-load concern).

---

## 4. Migration path (NOT big-bang)

A one-shot rewrite of 25k lines swapped into a live fleet is the single highest-
risk action available; it would trade tonight's recoverable outage for an
opaque total one. Instead, ship the new architecture incrementally, each phase
independently deployable and reversible, validated against real state before it
touches the hot path.

| Phase | Change | Risk | Payoff |
|---|---|---|---|
| **0** | Per-phase isolation (3.1) + activity integrity `raise`→`warn` (3.2) | Very low (adds try/except) | **Ends the class of total-outage-from-one-error immediately** |
| 1 | Extract `concurrency.py`, collapse to 3 integers (compat shim for old config) | Low | Removes the alias/fan-out maze |
| 2 | Size-based rotation + offline integrity tool; delete lineage validation | Low | Kills the activity-log fragility epicentre |
| 3 | Introduce task state machine; route the 5 ladders + 3 mutators through it | Medium | Ends thrash; single source of truth |
| 4 | Decompose `poll_workers`; add `ProgressMonitor`; confirm-kill | Medium | Fixes hung-but-heartbeating; testable |
| 5 | Providers: normalize identity + probed Account health | Medium | Ends revoked-account false-liveness |
| 6 | Move live state to event-log + projection, out of git | Medium-High | Ends git-wipe + `next`-overwrite |
| 7 | Delete sidecar; drop event-queue indirection; discussion→task kind | Low-Medium | Stops make-work growth |

**Cutover status (2026-07-19):**
- **Phase 0 — done.** `_safe_phase` per-phase isolation landed (`14a41cfb`); a
  raising phase (e.g. activity-scan on a missing archive) now degrades only
  itself. (The `raise`→`warn` softening of §3.2 is folded into Phase 2.)
- **Phase 1 — done (1a shadow + 1b cutover).** `rewrite/concurrency.py` covers
  both concurrency gates, shadow-proven equal for every live agent:
  `max_parallel` (per-agent cap) ← `agent_dispatch_capacity`, and `account_limit`
  (account cap) ← `quota_group_concurrency_limit`. Both live functions route
  through the module by default, legacy one flag away
  (`ready_dispatcher.use_rewrite_concurrency=false`). The 6-way account-group
  *resolver* (anti-pattern D) still stands; its collapse to a single `account`
  key is the remaining config-migration step, tracked as Phase 1 follow-up.
- **Phase 3 — done (3a shadow + 3b cutover).** `rewrite/task_machine.py`
  (`dispatch_reason`/`dispatch_priority`) is shadow-proven equal to
  `dispatch_priority_for_task` on the live board; the incumbent now routes
  through it by default (configured status sets translated to canonical lifecycle
  states first, so parity holds for custom sets too), legacy one flag away
  (`ready_dispatcher.use_rewrite_dispatch_reason=false`).
  Behaviour preservation is pinned by `rewrite/test_cutover.py` (rewrite-vs-legacy
  parity across a config matrix).
- **Phase 2 — done (integrity off the hot path).** Two parts, both landed:
  (a) `rewrite/verify_activity_integrity.py` — the standalone offline verifier
  §3.2 calls for: reuses the incumbent validator (`common.stream_logical_activity`,
  faithful by construction), runs outside any cycle, **alerts via exit code
  (0 ok / 2 integrity / 3 operational) instead of `raise`ing** (validated on the
  live log, 37,270 rows). (b) `common.write_activity_log` no longer lets an
  integrity fault abort the cycle: a genuine **lineage-integrity drift** fault
  (missing archive, digest/conservation mismatch — the exact 4h-outage class)
  degrades to a stderr warning + a guaranteed forced append, so
  dispatch/finalize/archive keep running; the offline verifier owns the alert.
  Security faults (symlink / non-regular leaf) and correctness guards
  ("recovery is pending", mid-rotation intent) keep their fail-closed contract
  via a tight drift allow-list. Escape hatch: `PANTHEON_ACTIVITY_LOG_STRICT=1` or
  `config.activity_log_strict_hot_path=true` restores incumbent fail-closed
  writes. Pinned by `rewrite/test_activity_resilient_write.py`; full
  `test_common.py` audit suite still green.
  Follow-up (optional hardening, not blocking): fully size-based atomic-rename
  rotation to retire the lineage-build cost on healthy writes too.
- **Phase 5 — done (decision cut over; probed-health model landed).**
  `rewrite/provider_health.py` owns the account failure-response decision:
  `decide_failure_response(kind, rotation_outcome) -> Rotate|Pause|Retry|Reassign`
  and `classify_health(kind) -> healthy|degraded|revoked` (auth ⇒ revoked, a
  first-class state, not a timed-out guess). `should_pause_dispatch_for_failure_kind`
  now routes through `provider_health.should_pause`, shadow-proven equal across
  the entire failure-kind vocabulary (11 kinds, 0 mismatch). Legacy ladder one
  flag away via `PANTHEON_LEGACY_FAILURE_RESPONSE=1`. Follow-up: route the two
  remaining copies of the pause/rotate/reassign branch (in poll_workers) through
  the same decision, and add the owner-side pre-dispatch auth probe that promotes
  `classify_health` from reactive to proactive.
- **Phase 4 — partial (two correctness fixes cut over; full decomposition pending).**
  `rewrite/worker_lifecycle.py`: (a) `confirm_kill` (SIGTERM → wait → SIGKILL →
  verify) now backs `terminate_worker_pid`, ending SIGTERM-and-assume-dead
  (a worker was marked `failed` while still alive and mutating state); legacy one
  flag away via `PANTHEON_LEGACY_TERMINATE=1`. (b) `has_work_progress` (new
  commit / more completed tool-calls — NOT heartbeat) is the observed-progress
  primitive lease renewal should bind to, fixing "hangs but heartbeats". Pending:
  decompose the 751-line `poll_workers` into the enum+table driver, and rebind
  lease renewal to `has_work_progress`.
- **Phase 6 — model built in isolation (storage cutover pending).**
  `rewrite/state_projection.py` implements the plan's core §3.7 idea: an
  append-only event vocabulary + a pure `project_board(events)` that folds it into
  the board, with task transitions validated against the ONE task state machine
  (`task_machine.TRANSITIONS`), and `next` *appended* (history retained) instead
  of overwritten. Deterministic/replayable (prefix ⇒ point-in-time board). This is
  the parallel-package build the discipline requires; the live cutover (state out
  of the git tree into the event log + projection) is the remaining step and needs
  the fleet to validate — it is the plan's Medium-High-risk item.
- **Phase 7 — sidecar OFF by default (dormant); event-queue/discussion pending.**
  `rewrite/utilization.py` encodes the §3.8 principle (`select_utilization_action`
  ⇒ reprioritize real backlog, never synthesize). The sidecar make-work engine is
  now **off by default** — `underutilization_settings` defaults `enabled=False` and
  `config.json` sets it false — so the live cycle no longer manufactures tasks; the
  code path is retained (set `underutilization_dispatch.enabled=true` to restore)
  pending physical deletion once confirmed dormant on the fleet. Still pending
  (both large, fleet-gated, test-contract-bound removals): drop the event-queue
  indirection (`process_queue`/`reconcile_queue_records` — core dispatch plumbing)
  and fold discussion-planning into a task `kind`.

**Build discipline:** the new modules land in a parallel package and are
exercised in shadow/dry-run against real state (read the live board, compute
intents, diff against what the old supervisor did) **before** any phase is cut
over. Cut over one phase at a time; keep the old path one flag away.

Each phase has an explicit acceptance test. Phase 0's: inject a missing archive,
prove only the `activity_scan` phase degrades while dispatch/finalize/archive
continue.

---

## 5. One-line summary

The supervisor's failures are not "complexity" — they are the absence of three
things: **(1) per-phase failure isolation, (2) a single task state machine that
owns all transitions, and (3) integrity/audit kept off the hot control path.**
Everything else (collapse concurrency to 3 integers, delete the sidecar and
event-queue accretions, move live state out of git, observe real progress
instead of heartbeats) follows from those three principles.
