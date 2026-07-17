# OPS-ACTIVITY-ROTATION-PENDING-INTENT-RECOVERY-001

## Objective

Safely resolve the stranded schema-v1 activity rotation transaction observed
on 2026-07-16, preserving all evidence and proving zero missing or duplicated
events before restoring the supervisor/status lane.

Owner: `Claude`. Reviewer: `Antigravity`. Priority: `P0`.
Target: `pantheon/dev`. Auto-merge: off.

## Required context

- Read `OPS_ACTIVITY_ROTATION_PENDING_INTENT_INCIDENT_PLAN_2026-07-16.md`
  completely before acting.
- Also read the archived activity rotation follow-up plan, PR #3782 and all
  unresolved exact-head review findings, and the current rotation/recovery
  implementation.
- The plan's incident baseline is informational only. Capture and hash-bind a
  fresh read-only inventory before implementation and immediately before any
  approved live execution.
- This task is a new incident recovery, not permission to repeat the earlier
  boundary migration or silently accept a schema-v1 intent in schema-v2 code.

## Scope

- narrow activity rotation recovery and compatibility code required for this
  incident class;
- directly corresponding tests and fixture builders;
- a dry-run/execute interface with exact inventory pinning;
- redacted incident, test, review, and post-recovery evidence; and
- the minimum PR #3782 composition needed to keep one authoritative recovery
  contract.

Do not modify product behavior, BFF/frontend, trading state, unrelated runtime
policy, legacy archive bytes, or central status files during development and
premerge testing.

## Required work

1. Capture a read-only inventory of the pending intent, staged files, installed
   content archive, every relevant legacy archive, active log, lineage state,
   locks, and all processes capable of appending or rotating the activity log.
2. Prove the exact byte relationships required by the incident plan. Include
   compressed and decompressed hashes, byte/line counts, file identities, and
   logical event accounting. Fail closed if any relationship is ambiguous.
3. Implement a narrow, idempotent recovery transaction for a valid stranded
   schema-v1 intent. It must produce the approved ordered lineage and active
   lineage-head control record without discarding post-intent appends.
4. Preserve the original intent and all incident artifacts. Do not use hand
   deletion, truncation, rename, recompression, or an unrecorded one-off shell
   rewrite as recovery.
5. Provide a read-only dry-run and a separately gated execute mode. Execute
   requires the exclusive activity lock, exact inventory digest, stable-input
   recheck, and an attestation that the reviewed all-writer guard is active.
6. Make every publish step crash-safe and retry-idempotent. A reader never
   recovers under a shared lock and never exposes a partial logical history.
7. Reconcile PR #3782 with this recovery path. Keep its strict schema-v2
   checks; add only explicit, tested handling needed for this incident and
   close all of the planner's remaining findings.
8. Add the full verification matrix from the incident plan, including exact
   fixture, relation variants, concurrent append, crash/retry, tamper,
   conservation, and central-lock isolation tests.

## Evidence requirements

Publish redacted evidence containing:

- exact source commit and final PR head;
- before/dry-run/after inventory manifests and their digests;
- artifact relationships, counts, hashes, logical event totals, missing count
  zero, and duplicate count zero;
- every fault/tamper test row with command and result;
- proof that all test status roots and locks were repo-external and that no
  central activity lock was opened;
- complete writer/process inventory plus the guard's stop, verify, timeout,
  abort, restore, and readback commands;
- independent exact-head review; and
- exact merge/install SHA and post-recovery supervisor/status readback.

Do not publish tokens, raw activity payloads, personal data, or unrestricted
host process environments.

## Delivery gates

- Work from a clean task worktree and compose current `origin/dev` before
  final review.
- Commit only scoped implementation, tests, and evidence with trailers:
  `LLM-Agent: Claude`, this task ID, and `Reviewer: Antigravity`.
- Run full activity, status, supervisor, watchdog, worker-runner, and runtime
  validation from repo-external isolated roots.
- Antigravity independently reviews and reruns the exact final head.
- Keep auto-merge off. Owner must not self-approve, merge, install, or mutate
  the live incident state.
- Planner acceptance is required before merge and again before live execute.
- A code merge alone is not incident completion.

## Stop conditions

Stop without live mutation if:

- any planned input changes after inventory pinning;
- any byte or event relationship is ambiguous;
- the later legacy archive cannot be accounted for exactly;
- a writer cannot be covered by the guard;
- a test touches or locks the central status root;
- the independent reviewer has an open P0 finding; or
- PR #3782 and this implementation expose two competing recovery contracts.

## Done

Done requires reviewed and merged implementation, exact-SHA dev installation,
one approved guarded live recovery, preserved incident artifacts, zero missing
and duplicate events, healthy resumed writers and supervisor/status commands,
PR #3782 reconciled with the accepted contract, and complete independent
post-recovery evidence with no open P0 finding.
