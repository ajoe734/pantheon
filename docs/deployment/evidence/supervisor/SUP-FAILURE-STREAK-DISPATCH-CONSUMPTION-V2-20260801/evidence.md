# SUP-FAILURE-STREAK-DISPATCH-CONSUMPTION-V2-20260801 — review pending

Task: Consume one retry generation in governed dispatch with audit

Owner: Codex · Reviewer: Human/Ops · Status: **review_pending**

## Scope

This task integrates the merged pure V2 recovery decision into the existing
supervisor queue and worker-start path. It does not change the pure decision,
failure V3 record schema, provider or reviewer policy, configuration,
canonical task state, product controllers, or live services.

## Delivered behavior

- Read the newest exact governed reviewer `reopen` from the locked activity
  stream and evaluate it against the latest failure generation, current task
  assignment/status, provider readiness and pauses, active workers, queue
  records, reservations, and active worktree leases.
- In both `run_once` and enabled worker self-claim, read that stream once
  before runtime admission when a threshold streak exists, then thread the
  same cycle-scoped snapshot through poll, reassignment, chair, failure-loop,
  dispatch, and self-claim checks. Atomic consumption deliberately takes one
  fresh exact snapshot under the runtime lock.
- Suppress automatic reassignment, failure-loop task-agent blocking, and chair
  triage only while that complete decision is allowed. Threshold tasks remain
  excluded from helper claims, so the exception cannot transfer ownership.
- Bind the dispatch key to the exact `failure-recovery:<sha256>` token. A newer
  failure plus newer reviewer progress therefore creates a distinct governed
  dispatch generation without bypassing ordinary cooldown or queue identity.
- Under `runtime_state_lock`, re-evaluate the decision, record the token in
  runtime state, append a durable redacted consumption audit, and only then
  reserve the existing governed queue event.
- Treat queue rejection, exception, or missing queue readback as fail-closed:
  the token stays consumed and is rediscovered from the durable activity audit,
  preventing an infinite retry after process restart.
- Carry only the token and exact failure/progress generation identities in the
  queue task metadata. On worker start, bind the queue event and run ID into a
  follow-up audit. If runtime state was lost in the crash window, reconstruct
  the consumption from its audit before binding the worker.
- Never clear the failure streak on consumption, queueing, worker start, or an
  arbitrary owner commit. A later exit without newer governed progress appends
  another generation and retains the threshold; a newer reviewer reopen may
  authorize a different one-shot token.
- Emit audit rows containing task, logical provider, failure-time owner and
  reviewer, prior count/kind/timestamp, exact failure/progress identities,
  qualifying event/head, decision reason, token, reservation/queue identity,
  and worker run when available. Raw failure reasons and evidence references
  are not copied into these rows.

## Deterministic acceptance coverage

The new integration suite covers the captured Antigravity/Human-Ops incident,
failure-loop/chair/reassignment gate release, concurrent double consumption,
cycle-scoped activity read counts and fresh atomic revalidation, and explicit
self-claim lock ordering. The self-claim regression proves its prefetch occurs
at lock depth zero, every non-mutating decision call reuses that exact list,
and a successful consume adds exactly one fresh nested in-lock read. Coverage
also includes next-loop replay, queue failure, process-start audit binding,
runtime-state crash recovery, a subsequent failure without progress, a newer
second progress generation, provider/occupancy denial, audit shape/redaction,
and unrelated streak preservation. The merged pure V2 suite continues to
cover every deny row for identity, status, failure kind, progress binding,
occupancy, prior consumption, and provider pause/readiness.

## Verification

- Integration + pure decision suite: 16 passed, 491 deselected, 45 subtests
  passed.
- Focused decision/dispatch/failure-loop suite: 22 passed, 485 deselected, 45
  subtests passed.
- Full supervisor regression: 507 passed, 147 subtests passed.
- Python compile check: passed.
- `git diff --check`: passed.
- Rejected PR #4437 heads `07316c73` and `77af55015`, plus rejected PR
  #4438 heads `94e3b6f3`, `d616beaa`, and `35056b71`, are absent from the
  candidate ancestry.

## Review boundary

Base: `79ba3f431127bf9718697d2ba9e9ddce97969ec3` (`origin/dev`).

Implementation anchor: `25c30277cf79b649f0762c695e10eca64e55329a`.

Self-claim lock-order correction anchor:
`369b016062fc6b54bb02b7f9571a1f6f770a6112`.

Human/Ops rejected prior exact head
`4f48e52f6997b37038fed49729e6969df3b2f40b` because enabled worker
self-claim still read the full activity stream after acquiring the exclusive
runtime lock. The correction anchor moves that prefetch before admission and
keeps only the fresh atomic-consume revalidation in-lock. This evidence and
all prior review notes remain non-approval; the new exact PR head requires a
fresh independent review.

Replacement PR: [#4491](https://github.com/ajoe734/pantheon/pull/4491).
After that replacement existed, stale PR
[#4385](https://github.com/ajoe734/pantheon/pull/4385) and rejected PR
[#4438](https://github.com/ajoe734/pantheon/pull/4438) were closed as
superseded with links to V2 PRs #4483, #4489, and #4491. Their commits were
not merged or reused.

This evidence is owner-authored and remains **review_pending**. Human/Ops must
independently review the final exact PR head and bind it through the canonical
review gate before merge. No live-runtime outcome, rollout, restart, signal, or
configuration change is claimed.

Rollout is source merge only. Rollback is revert of the activity-normalizer,
pure-decision, and this dispatch-consumption task merge commits; no config
migration is involved.
