# L12 Current Gap Supervisor Dispatch Packet

Packet ID: `2026-07-31-l12-current-gap-supervisor-dispatch`

Generated: `2026-07-31T06:40:20Z`

Updated: `2026-07-31T12:25:00Z`

Pipeline status updated: `2026-07-31T12:46:41Z`

Supervisor redispatch updated: `2026-07-31T12:58:21Z`

Worker preemption churn updated: `2026-07-31T13:00:37Z`

Scheduler root cause updated: `2026-07-31T13:25:39Z`

Source audit:
`docs/04/pantheon_twelve_loop_gap_2026-07-26/archive/CURRENT_THREE_PASS_GAP_AUDIT_2026-07-31T0640Z.md`

Machine-readable task graph:
`docs/bff/execution-tasks/2026-07-31-l12-current-gap-supervisor-dispatch/tasks.json`

## Goal

Complete the remaining twelve-loop work through real supervisor and auto-worker
fleets, with maximum safe parallelism and no Codex conversation subagents.

## Hard Rules

- Do not edit `.orchestrator/config.json`.
- Do not use Codex chat subagents as fleets.
- Prefer Antigravity and Claude/Claude2 lanes when live readiness allows.
- Do not count a DevTaskPacket receipt unless every task materializes in
  canonical task-state / `ai-status.json`.
- Do not count stale PR heads, branch-only evidence, or merged PRs without
  governed task closeout.
- Frontend work remains in `ajoe734/execute-plans` on `dev`.
- Hosted proof must bind the Pantheon-owned FE/BFF identities, not historical
  Lovable or legacy frontend truth.

## Wave Summary

1. Wave 0 repairs/proves the supervisor DevTaskPacket materialization path,
   worker worktree source-root path, stale failure-streak cleanup, legacy
   seen-event normalization, and dispatch-eligible priority preemption.
2. Wave 0X repairs the newly discovered #4385 nonexistent evidence anchor and
   moves #4396 current-head support proof through governed PR/closeout handling.
3. Wave 0R resumes L12 fleets through the existing real supervisor controller
   after all Wave 0 blockers are proven.
4. Wave A refreshes exact-head PR review/closeout blockers in parallel.
5. Wave B retires stale PR evidence only after observability evidence is fresh.
6. Wave C finalizes already review-approved support rows.
7. Wave D starts independent product proof lanes after Wave 0/0R.
8. Wave E runs learning product proof, hosted proof, and final closeout after
   their dependencies are archived.

## 2026-07-31T11:59Z Addendum

Authoritative status-root readback shows the live supervisor is running and
caught up. A command-root runtime-health probe reads a stale command-root shadow,
so it must not be used alone to claim the supervisor is down. The current
fleet-resume gate also depends on `SUP-WORKER-WORKTREE-SOURCE-ROOT-20260730`
and `SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729`, not just the dev-bridge
materialization row.

## 2026-07-31T12:25Z Fleet Reconcile Addendum

The real supervisor drained the exact-head reconcile packet and auto-workers
processed the #4385/#4386 reconcile rows. #4385 now has concrete repair work:
its current evidence points at nonexistent anchor
`9d53a94a265c55af4c8d15c50ab3751f1440ac0f` instead of actual anchor
`9d53a94a295d71ee49aea6f4b96e47fbcfd29093`. #4386 current-head support
evidence was review-approved. At the 12:25Z observation ReviewBus PR #4396 was
still draft; the 12:46Z addendum supersedes that draft status and records that
it is now ready but still blocked from integrated proof by protected
merge/root-freeze closeout.

## 2026-07-31T12:46Z Dispatch/Closeout Architecture Addendum

The dispatch/closeout pipeline is not fully repaired. Current evidence shows
partial architecture progress, not end-to-end reliability:

- #4390, the DevTaskPacket materialization repair, is still open and
  `BLOCKED`.
- #4392, the worker source-root repair, is still open and `BLOCKED`.
- #4395 now points at exact head
  `f68827c8e17d6a1f081afe24f62ba85c116166e8` with Branch CI and Pantheon
  canonical review gate green, but auto-integrator still blocks on
  `mergeStateStatus=BLOCKED`.
- #4396 now points at exact head
  `19f71db59b94016aa0d6bf00cd3ead5bf8a9eb4f`, is no longer draft, and has
  Branch CI plus Pantheon canonical review gate green, but auto-integrator
  still blocks on `mergeStateStatus=BLOCKED` and the missing Human/Ops
  root-freeze exact-head context.
- Wave 0X fallout tasks were materialized by the real supervisor, but both are
  currently `todo` after supervisor preemption:
  `SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-20260731` and
  `SUP-L12-RUNNING-OWNER-EXACT-HEAD-PR-CLOSEOUT-20260731`.

This packet must therefore be read as an architecture gap/dispatch plan, not as
proof that supervisor/auto-worker dispatch and closeout are already fixed.

## 2026-07-31T12:58Z Supervisor Redispatch Addendum

The updated Wave 0X requeue against the original task IDs failed with
`Bridge assignment conflict`, proving already materialized task specs are
immutable-bound to their original packet/spec hashes. A V2 superseding packet
was then accepted:
`pkt-l12-wave0x-pipeline-blockers-supersede-20260731T1255Z`.

It materialized:

- `SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-V2-20260731`
- `SUP-L12-RUNNING-OWNER-EXACT-HEAD-PR-CLOSEOUT-V2-20260731`

Both V2 tasks auto-started workers, but both runs exited with `SIGTERM 15` and
the supervisor returned the rows to `todo`. This proves handoff and
materialization, but not auto-worker completion.

At `2026-07-31T13:00:37Z`, both V2 tasks had repeated SIGTERM/preemption
cycles and were still `todo`. The immediate fleet blocker is now worker
preemption/lifecycle churn, not packet creation.

## 2026-07-31T13:25Z Scheduler Root-Cause Addendum

The churn has two separate code/runtime causes:

- #4399 (`SUP-PREEMPTION-DISPATCH-ELIGIBILITY-20260731`) fixes the scheduler
  architecture so higher-priority preemption and normal dispatch share the same
  provider/cooldown/failure-loop eligibility ladder, with a five-minute
  new-worker grace. Exact head `a924a6f3c0c54982d7efe145750cc99c57bc7f2e`
  passed 486 focused tests and the actual 13:01Z state replay; Branch CI is
  green, but the PR remains open/`BLOCKED` and is not live.
- #4397 (`SUP-SEEN-EVENT-KEYS-NONNULL-20260731`) fixes legacy
  `seen_event_keys` normalization. It is green but open/`BLOCKED`; the live
  command root still logs `run_scan` and `trim_seen_events` TypeErrors.

Both repairs are Wave 0 prerequisites. Do not release Wave D/E or supersede V2
with V3 until both are merged/live-promoted and a real canary worker survives
beyond the five-minute grace window. V3 must use new IDs because the V2
task/agent pairs already carry failure-loop streaks.

## Completion Boundary

The twelve loops are operational only when `L12-CLOSE-001` is archived done and
its evidence proves every loop from current hosted FE/BFF identities with no
stale PR/task proof counted.
