# Orchestrator State Plane Redesign

Last updated: 2026-04-13
Status: phased rollout in progress; phases 1-4 landed in repo, ongoing hardening continues

## 1. Why This Redesign Exists

The current orchestrator has working building blocks, but the data model is still mixing several different concerns into the same prompt and write-back loop:

1. planning consensus
2. execution task truth
3. runtime supervisor state
4. human-readable summary
5. raw failure transcripts and tool payloads

That mixture causes a feedback loop:

1. a worker fails
2. the raw failure reason is written into runtime state, task narrative, or generated summary
3. the next worker reads those files as "shared truth"
4. the next failure contains even more inherited context
5. state and prompt size both grow until the system becomes noisy, stale, and fragile

This is not only a dashboard problem. It is a framework problem.

## 2. Core Diagnosis

The current design treats these files as part of the default shared truth:

- `ai-status.json`
- `ai-activity-log.jsonl`
- `current-work.md`
- planning docs when planning mode is active

That creates three structural failures:

1. machine state and human summary are not separated
2. planning and execution are adjacent but not truly isolated
3. raw runtime evidence can leak back into canonical task truth

In a collaborative system, these must be different planes with different contracts.

## 3. Design Goal

The orchestrator should behave like a layered collaboration system:

1. planning creates intent and slices
2. execution tracks owned work
3. runtime coordinates workers
4. evidence preserves raw history
5. dashboard summarizes without becoming a source of truth

No raw provider payload should be able to re-enter worker prompts through canonical state.

## 4. Target State Planes

### 4.1 Planning Plane

Purpose:
- architecture discussion
- cited readouts
- unresolved decisions
- proposed execution slices
- human acceptance boundary

Canonical artifacts:
- `docs/02-architecture/consensus/phaseX/planning-session.json`
- `docs/02-architecture/consensus/phaseX/starter-draft.md`
- `docs/02-architecture/consensus/phaseX/review-round-*.md`
- `docs/02-architecture/consensus/phaseX/consensus-packet.md`

Rules:
- planning state is not execution state
- planning drafts never become runtime context by default
- execution tasks are materialized outputs from planning, not live edits inside planning docs

### 4.2 Execution Plane

Purpose:
- durable task truth for delivery work
- ownership
- reviewer assignment
- lifecycle transitions
- dependencies
- artifact targets

Canonical artifact:
- `ai-status.json`

Allowed data:
- `id`, `title`, `summary_zh`, `owner`, `reviewer`
- `depends_on`, `artifacts`, `acceptance`
- lifecycle state such as `todo`, `in_progress`, `review`, `review_approved`, `done`
- short handoff/review/finalize summaries

Forbidden data:
- raw stderr
- tool transcripts
- provider quota payloads
- giant JSON blobs
- generated dashboard text

Execution truth must stay small, structured, and durable.

### 4.3 Runtime Plane

Purpose:
- queue records
- worker sessions
- approval bookkeeping
- retry state
- provider pauses
- scheduler health

Canonical artifacts:
- `.orchestrator/state.json`
- `.orchestrator/event-queue.jsonl`
- `.orchestrator/approval-queue.json`

Rules:
- runtime plane is transient control-plane state
- runtime plane is not a worker prompt source by default
- runtime plane may be noisy, but it must stay structured

### 4.4 Evidence Plane

Purpose:
- preserve raw outputs without polluting canonical truth
- support debugging, audit, and postmortem work

Canonical location:
- `.orchestrator/logs/`
- future: `.orchestrator/evidence/<run-id>.json`

Allowed data:
- provider stderr/stdout
- tool payloads
- raw quota/capacity responses
- approval broker payloads
- full transcripts or references to transcripts

Rules:
- evidence is append-only
- evidence may be large
- evidence must be referenced, not copied into execution or planning state

### 4.5 Narrative Plane

Purpose:
- concise human summary
- operator-facing snapshot
- dashboard narrative copy

Canonical artifacts:
- `current-work.md`
- `dashboard-bundle.json`
- `docs-site/*` mirrors

Rules:
- narrative plane is derived only
- narrative plane never outranks execution/runtime/planning source files
- worker prompts must not require the full narrative plane by default

## 5. New Data Contracts

### 5.1 Planning -> Execution

Planning produces:
- agreed slice definitions
- owner/reviewer suggestions
- dependency edges
- acceptance targets

Execution receives only:
- materialized task rows
- references back to the accepted planning packet

Execution must not inherit the whole planning transcript.

### 5.2 Execution -> Runtime

Execution provides:
- task metadata
- dispatch eligibility
- ownership and reviewer truth

Runtime provides:
- queue state
- worker health
- approval status
- provider backoff

Runtime does not get to mutate task meaning. It may only:
- advance lifecycle through approved scripts
- request reassignment through structured actions
- attach evidence references

### 5.3 Runtime -> Narrative

Narrative may summarize:
- currently active tasks
- paused providers
- blockers
- mismatches

But it must never embed raw payloads directly. It should summarize and link to evidence refs.

### 5.4 Runtime -> Worker Context

Runtime should not send "read all shared truth files" anymore.

Instead, supervisor should generate a task-scoped brief:

- task id
- current lifecycle state
- owner/reviewer
- direct dependencies
- direct artifacts
- relevant planning packet ref if the task came from planning
- last few task-specific events
- exact canonical docs relevant to this task

That brief becomes the worker's default prompt context.

## 6. The Biggest Current Smell

The biggest design smell is not file size by itself.

It is this loop:

1. a provider failure returns a large reason string
2. the string is written into task or pause state
3. generated summaries include that string
4. the next worker reads those summaries as context
5. the provider fails again with even more inherited context

That is a memory hierarchy failure.

## 7. Required Structural Changes

### 7.1 Remove Raw Failure Reasons From Canonical Execution Truth

Replace free-form raw messages with:

- `failure_kind`
- `failure_summary`
- `raw_ref`

For example:

Instead of:
- `Auto-reassigned review ... after repeated Qwen capacity/429: <entire payload>`

Use:
- `Auto-reassigned review after repeated Qwen quota/capacity failures.`
- `failure_kind = quota`
- `raw_ref = .orchestrator/evidence/qwen-20260413T....json`

### 7.2 Replace `dispatch_pauses.reason` With Structured Fields

Current pause state stores a giant `reason` string.

Target shape:

```json
{
  "provider": "qwen",
  "paused_at": "...",
  "blocked_until": "...",
  "task_id": "BG-001",
  "worker_run_id": "qwen-...",
  "failure_kind": "quota",
  "summary": "Daily quota exceeded",
  "raw_ref": ".orchestrator/evidence/qwen-20260413T....json"
}
```

### 7.3 Downgrade `current-work.md`

`current-work.md` should become:
- operator summary
- not worker memory
- not canonical execution truth

Worker prompts may link to it optionally, but it should not be in the mandatory first-read set.

### 7.4 Introduce Task Briefs

New generated location:
- `.orchestrator/task-briefs/<task-id>.md`

The brief should contain only relevant context for that task.

This is the main fix for prompt explosion.

### 7.5 Separate Planning Session Inputs From Execution Inputs

When planning mode is active:
- planning workers read planning canonical docs
- execution workers do not automatically read planning docs

Execution tasks should only keep a short `planning_ref`, not a full planning payload.

## 8. Revised Collaboration Model

### 8.1 Planning Mode

Planning mode should feel like a temporary design workspace:

- independent readouts
- structured disagreement resolution
- human acceptance gate
- materialization output

It should not mutate execution state until the materialization step.

### 8.2 Execution Mode

Execution mode should feel like a stable project board:

- small state
- deterministic lifecycle
- clean handoffs
- no giant historical transcripts

### 8.3 Runtime Mode

Runtime mode should feel like a scheduler and control plane:

- queue
- workers
- retries
- pauses
- approvals

Runtime history is observability, not collaboration memory.

## 9. Dashboard Implications

The dashboard should read:

- planning summary from planning plane
- task truth from execution plane
- worker/queue health from runtime plane
- narrative copy from derived summary

The dashboard should never need to parse giant embedded provider payloads.

Alert cards should render only:
- provider
- severity
- summary
- blocked_until
- task id
- evidence link or run id

## 10. Migration Plan

### Phase 1: State Hygiene

1. stop storing raw provider payloads in `task.next`, reassignment messages, and pause state
2. introduce structured pause/error summaries
3. keep raw details only in logs or evidence files

### Phase 2: Prompt Boundary Cleanup

1. remove `current-work.md` from mandatory worker-first context
2. generate task-scoped briefs
3. make workers read only the task brief plus explicitly relevant canonical docs

### Phase 3: Plane Separation

1. formally separate planning schema from execution schema
2. add references instead of cross-copying narrative between planes
3. update dashboard to consume structured plane summaries rather than overloaded text blobs

### Phase 4: Evidence Layer

1. add evidence artifacts for provider failures and approval transcripts
2. link runtime and dashboard to evidence refs
3. keep source-of-truth files concise

## 11. What "Good" Looks Like After Redesign

When the redesign is complete:

1. planning packets are rich, but isolated
2. execution board stays small and readable
3. runtime pauses and failures are structured
4. dashboard renders concise alerts without layout explosions
5. worker prompts stay task-scoped instead of repo-history-scoped
6. evidence remains available for debugging without polluting collaboration memory

## 12. Recommended Immediate Next Steps

1. update supervisor failure and pause write-back paths to emit structured summaries plus `raw_ref`
2. introduce `.orchestrator/task-briefs/` generation and switch worker dispatch to use it
3. remove `current-work.md` from the mandatory shared-truth read order
4. revise `docs/agent-orchestrator.md` and bootstrap templates so new repos start from the layered model instead of the overloaded shared-state model

This redesign should be treated as a control-plane architecture change, not as a dashboard-only cleanup.
