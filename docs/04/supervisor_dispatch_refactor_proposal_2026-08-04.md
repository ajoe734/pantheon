# Supervisor Dispatch Path — Refactor Proposal (2026-08-04)

Written after a live debugging session tracing why `SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803`
sat with zero dispatch for ~4 hours despite the supervisor reporting itself healthy the whole time. Every
finding below is grounded in code actually read and functions actually executed against the live config/state
during that session — not a generic "add more tests" wishlist.

## Correction to prior assumption

An earlier memory/assumption claimed `poll_workers` was a "777-line god-loop, never decomposed." That is
**stale** — `poll_workers` (supervisor.py:10337) is already broken into discrete stage functions
(`poll_worker_orphan_stage`, `_observation_stage`, `_assignment_stage`, `_approval_stage`, `_stall_stage`,
`_failure_stage`, `_completion_stage`), each independently readable. That part of the SUPERVISOR-REWRITE work
held up. The actual hard-to-debug surface is one level up: **the decision of whether to dispatch a NEW worker
at all**, which is where all four problems below live.

## What made this so hard to debug

To answer "why isn't my task running," I had to: read ~600 lines of `dispatch_ready_tasks` (13351) end to end,
find `agent_auto_dispatch_block_reason` (6899) by grep, hand-write a Python one-liner that reconstructs
`config`/`state`/`provider_report` well enough to call it directly, and only then discover the real answer.
There is no supported way to ask the running system "why did task X not dispatch this tick" — you either
grep raw JSONL activity logs for `type` values you have to already know exist, or you reverse-engineer the
gate functions from source. That gap is the root problem; the four items below are its concrete causes.

## Problem 1 — the capability probe is flaky and directly gates dispatch, inline, on every cycle

`load_provider_report()` (952) defaults `auto_refresh_provider_capabilities: true`, so **every single dispatch
cycle re-invokes the live CLI binaries** (`agy --prompt`, `claude ...`) synchronously inside the hot path, and
whatever it gets back — including a one-off timeout under load — is written straight back to
`provider_capabilities.json` as ground truth and immediately used to gate dispatch for every agent that cycle.
Confirmed live: probing agent capability with the box under `load_1m ≈ 3–4` intermittently returned
`"Antigravity CLI (agy) is not installed"` / `"Claude CLI is not installed"` seconds apart from a probe that
returned healthy — same binaries, same PATH, no state change in between. This is exactly the failure class the
docstring of `scripts/validate_twelve_loop_gap_evidence.py` was written to prevent for *evidence* (don't let a
transient read stand in for ground truth) — the capability probe never got the equivalent treatment.

**Fix shape:**
- Decouple probing from dispatch. Run capability probes on their own timer (a background tick, not inline in
  `load_provider_report`), and have `dispatch_ready_tasks` read the last-probed result with a staleness bound,
  never trigger a live probe itself.
- Add hysteresis: require **N consecutive failed probes** (not one) before flipping `can_auto_deliver` from
  true→false. A single flake should not silently zero out dispatch for a whole agent for a whole cycle.
- When a probe result changes state (true→false or false→true), emit a distinct, greppable activity-log event
  (e.g. `provider_capability_transitioned`) instead of relying on someone diffing `provider_capabilities.json`
  by hand. Today the only trace is the file's content at whatever instant you happen to look.

## Problem 2 — no "why not dispatched" explain path

Everything needed to answer this already exists as pure functions (`agent_auto_dispatch_block_reason`,
`task_execution_dispatch_candidate`, `agent_can_take_task`, `quota_group_concurrency_limit`,
`agent_dispatch_capacity`) — they're just never composed into a single callable that takes a task_id and
returns a structured trace of which gate said no and why. I had to write this by hand, in a scratch script,
sitting outside the codebase, calling into `supervisor` as a library.

**Fix shape:** add `scripts/explain_dispatch.py <task_id>` (or a `supervisor.py --explain <task_id>` mode) that
runs the exact same gate sequence `dispatch_ready_tasks` uses for that one task against every eligible agent,
and prints each gate's verdict in order (block-reason → quota → capacity → catalog-lock → dependency →
cooldown → helper-claim-eligible), stopping at the first `no` with its exact reason string. This is the
single highest-leverage change here — it turns a 30+ minute reverse-engineering exercise into a 5-second
command, for this bug and every future one shaped like it.

## Problem 3 — a fail-closed integrity gate is also the only path for status write-back, with zero board-level signal when it trips

`ai_status.py`'s `recover_status_activity_outbox` re-validates the full activity-log lineage/archive
content-address chain (common.py `_validate_active_lineage_head_unlocked` / `activity_audit_source_paths_unlocked`)
on the same synchronous path that writes a worker's progress back to the task record. When that integrity
check throws (confirmed live: `activity content-addressed archives do not match lineage`, transient, tied to
a concurrency burst right after a supervisor restart), the **status write silently doesn't happen** — not
"retry later," not "flag as blocked" — the task record is left exactly as it was, indistinguishable from a
task that was never touched. `SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803` sat at its original
`todo` / original `last_update` for hours while a worker had actually started, run, staged a commit, and died,
because none of that could get past this gate to be recorded.

**Fix shape:** the integrity guarantee is worth keeping (P2 of the original rewrite plan already made this
exact argument for the *hot cycle* raise vs. warn — the same argument applies here one layer up). The outbox
already exists as a concept (`STATUS_ARCHIVE_OUTBOX_KEY` / `STATUS_ACTIVITY_OUTBOX_KEY`) — extend it so a
write that can't clear the integrity check is durably queued *and visibly flagged on the task itself*
(e.g. `task["status_write_pending"] = true` plus a count), rather than dropped with the task record frozen and
no distinguishing mark. A human or an auto-worker looking at the board should be able to tell "this task's
last known state may be stale, N writes are queued" apart from "this task genuinely hasn't moved."

## Problem 4 — retry exhaustion is invisible at the board level

Boot-reconciliation retried the same worker 4 times (visible only in raw activity-log `worker_retry_scheduled`
/ `worker_retried` events) before giving up with `worker_failed`. The task board carried zero trace of any of
this — no failure-streak counter, no "quarantined after N attempts" status. This is already a recognized
pattern in this codebase (see the several `SUP-L12-STALE-FAILURE-STREAK-REAPER-*` / `OPS-FAILURE-LOOP-CHAIR-TRIAGE-RECOVERY-*`
tasks already in flight) but each existing fix is a point patch for one incident, not a structural counter on
the task schema.

**Fix shape:** add a `failure_streak` field directly on the task record (owned by the same write path that
already updates `status`), incremented on `worker_failed`, reset on `worker_completed`/`review`. Past a
configured threshold, transition to an explicit `status: "quarantined"` (not silently-stuck `todo`) that
requires an operator or auto-worker to consciously clear it — visible in `ai-status.json` without grepping
gigabytes of JSONL.

## Problem 6 — a post-approval "record the approval" commit invalidates the approval it records

Added the same day, one live incident: PR #4532 (`SUP-DISPATCH-EXPLAIN-TOOL-20260804`) got a real
`review_approved` at 13:39:50 against head `4aae7942`. Six minutes later, the same task's own finalize step
committed `b84acd1e3003` -- a 2-line edit to `.orchestrator/task-briefs/sup_dispatch_explain_tool_20260804.md`
whose entire content was recording that the review had passed. That commit moved the branch head, which
invalidated the exact-head-bound approval that commit exists to record, and forced a second review pass for a
diff that changed nothing about the reviewed code.

**This is not standard code review practice and should not be treated as normal.** In a normal PR review, an
approval is metadata -- it does not, and must not, produce a new commit on the branch it approves. This
fleet's own workers have grown a habit of also writing a durable "yes, this was approved" note into the
task-brief file and committing it, on top of the real approval mechanism
(`ai_status.py approve`, which already durably records the approval in live task-state --
`.orchestrator/task-briefs/*.md`'s own header says to treat `ai-status.json` as the durable source of truth,
not itself). The closeout-record commit is redundant with a mechanism that already works, and actively
self-defeating: it is the exact "assert a fact about bytes that supersede themselves" shape
`scripts/validate_twelve_loop_gap_evidence.py`'s docstring already names as the PR #4221 defect, just
recurring in a new spot the validator doesn't cover.

**Fix shape, in order of preference:**

1. First choice -- stop producing the commit. If a governed workflow step is what emits the closeout-record
   write, remove it; the live task-state already carries this fact durably, and nothing needs a git-committed
   copy created *after* the reviewed diff.
2. If some durable git record of approval is genuinely wanted (e.g. because a recovery path like
   `reconcile_merged_done` wants evidence reachable from `origin/dev`), it must be written and committed
   *before* the commit that gets reviewed -- as part of the same diff the reviewer looks at once -- never as a
   follow-up after approval.
3. Defense in depth regardless of (1)/(2): teach the review-gate/dispatch path
   (`scripts/git/github_review_bridge.py` and whatever finalize step re-checks head bindings) to recognize a
   commit landing after `review_approved` that touches *only* paths under `.orchestrator/task-briefs/` as a
   no-op with respect to the reviewed content, and carry the existing approval forward onto the new head
   automatically instead of forcing a second review round-trip. This is a safety net, not a substitute for
   (1) -- it should rarely fire once the habit in (1) is actually removed.

Low blast radius (touches a narrow post-approval path, not the dispatch loop itself), but worth fixing
promptly since it costs a full extra review round-trip -- real reviewer time -- every time it fires, for a
diff that carries zero code content.

## Suggested sequencing

These are independent and can land as four separate small PRs, in this order (each de-risks the next):

1. **Problem 2 first** (explain-dispatch tool) — pure read-only addition, zero risk, and pays for itself
   immediately on every future "why isn't X running" question, including verifying 1/3/4 once they land.
2. **Problem 1** (probe hysteresis + decoupling) — the highest-value fix; this session's entire multi-hour
   stall traces back to it.
3. **Problem 4** (failure_streak on task schema) — small, additive schema field, no behavior change to
   existing gates.
4. **Problem 3** (outbox visibility) — touches the integrity-critical write path, so it should land last and
   go through the same shadow-verification discipline (`rewrite/shadow.py`) the original rewrite phases used,
   given it is modifying the exact mechanism a 4-hour production outage already came from once.

None of these require touching `poll_workers`, `process_queue`, or `reconcile_queue_records` — the actually
loop-shaped, highest-blast-radius code the original plan flagged as undone stays untouched. This proposal is
scoped to the decision/observability layer sitting just above them.

## Problem 5 — `blocked` is a one-way door, and every fix so far has been a point patch

Added after a second live session (same day) spent manually unsticking `SUP-DISPATCH-EXPLAIN-TOOL-20260804`,
`SUP-TASK-FAILURE-STREAK-SCHEMA-20260804`, and two of the BCD tasks from the first session — four separate
times, same shape each time: a task went `blocked` because its PR's CI failed or its remote ref was rejected;
a human (in this case me, standing in for Human/Ops) fixed the underlying git problem; and the task record
stayed frozen at its stale `blocked` state until someone ran `reopen` by hand, because nothing else would ever
look at it again.

**Root cause, confirmed by reading the actual dispatch settings, not assumed:**

```
owned_statuses    = ['in_progress', 'todo']
review_statuses   = ['review']
finalize_statuses = ['review_approved']
```

`dispatch_ready_tasks`'s entire per-tick candidate scan only ever considers these four statuses.
**`blocked` is in none of them.** The moment a task enters `blocked`, it is structurally invisible to the
dispatch loop forever, regardless of whether the condition that blocked it later resolves on its own. The
only exit is an explicit `reopen`/`assign`-class command from an actor who happens to notice.

**This is not hypothetical or rare — the codebase already has a graveyard of point patches for exactly this
shape of problem**: `SUP-L12-STALE-PR-RETIRE-20260729`, `SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729`,
`SUP-L12-STALE-REAPER-EXACT-HEAD-RECONCILE-20260731`, `SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-20260731`
(and its V2), `SUP-L12-HELD-CLOSE-OVERLAP-GUARD-20260731` — each one a one-off task aimed at some specific
flavor of "board says X, reality says Y, nobody reconciled it." Checked live on 2026-08-04: **every one of
these reaper/repair tasks is itself sitting `blocked`, waiting on Human/Ops.** The tasks meant to fix
staleness became stale themselves. `grep` confirms none of them ever landed a generic mechanism in
`supervisor.py` — "reaper" only exists in code for pruning stale worktree directories, an unrelated concept
that happens to share the name.

**Why a sixth reaper task would repeat the mistake:** each prior attempt scoped itself to one task or one
symptom (a specific stale PR, a specific failure streak) instead of the shared shape underneath: *a task's
`blocked` record asserts a fact about an external system, and nothing ever re-samples that fact.* Point
patches don't compose — the next new blocked-and-forgotten task just needs its own new reaper.

**The other structural gap: `blockers` carries no machine-checkable fact.** `command_blocker`'s record is
pure prose — `{task_id, owner, waiting_for, message, status}`. There is no field distinguishing "waiting for
a human to make a judgment call" (correctly permanent until a human acts — must never auto-clear) from
"waiting for PR #4532's CI to go green" (an objective, machine-checkable fact that anyone could re-sample).
Today both look identical to the supervisor: inert prose.

**Fix shape — one generic reconciliation pass, not a task-specific one:**

1. Extend the blocker record with an optional, opt-in `check_kind` + params, e.g.
   `{"check_kind": "github_pr_ci", "pr_number": 4532}` or `{"check_kind": "task_dependency", "task_id": "X",
   "required_status": "done"}`. Omitting `check_kind` (the default, and the only option today) behaves
   exactly as now — a pure human-judgment block that nothing auto-clears. This is purely additive: it can
   never cause an existing block to be cleared that shouldn't be, because auto-clearing only ever applies to
   blockers that explicitly opted in with a structured, checkable fact.
2. Add `reconcile_blocked_tasks(config, state)`, run on its own interval (minutes, not every ~30s tick —
   this makes external API calls and must not hammer GitHub or add latency to the hot dispatch cycle). For
   each open blocker with a `check_kind`, re-sample the real fact (PR check-run status via `gh`/GitHub API,
   the referenced task's current status, etc.) and, if resolved, drive the exact same transition `reopen`
   already performs — no new state-machine path, just an automatic trigger for the one that exists.
3. Success criterion for this fix is explicit: **it must be able to retire the existing
   `SUP-L12-STALE-*-REAPER*` task family as superseded**, not add a sixth entry next to them. If the new
   mechanism can't absorb what those were trying to do, the design is still too narrow.
4. Out of scope, deliberately: anything without a `check_kind` stays a pure human/agent judgment call,
   untouched. This fix does not try to guess intent for prose-only blockers that predate it.

This is higher blast-radius than Problems 1/2/4 (it changes a state-machine transition path used everywhere,
not just one subsystem) but lower-risk than Problem 3 (it only ever *adds* a new way to reach a transition
that a human could already trigger manually — it cannot produce a state no `reopen` could already produce).
Suggest sequencing it after Problem 4 (failure-streak schema) lands, since both touch task-record schema and
should not be designed in isolation from each other.
