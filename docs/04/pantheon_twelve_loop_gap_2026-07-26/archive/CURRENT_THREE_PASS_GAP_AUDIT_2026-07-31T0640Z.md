# Current Three-Pass Twelve-Loop Gap Audit And Supervisor Dispatch Plan

Audit ID: `L12-CURRENT-GAP-SUPERVISOR-DISPATCH-20260731`

Observed: `2026-07-31T06:40:20Z`

Addendum observed: `2026-07-31T11:59:43Z`

Fleet reconcile addendum observed: `2026-07-31T12:25:00Z`

Pipeline architecture addendum observed: `2026-07-31T12:46:41Z`

Supervisor re-dispatch addendum observed: `2026-07-31T12:58:21Z`

Worker preemption churn addendum observed: `2026-07-31T13:00:37Z`

Scheduler root-cause addendum observed: `2026-07-31T13:25:39Z`

Repository base inspected: `origin/dev = 6f87a207eabf5c6121a59cae1bb8bc5bbc5cbf8e`

Status roots inspected:

- `/home/lupin/pantheon/ai-status.json`
- `/home/lupin/pantheon-ci-deploy/runtime/task-state-events.jsonl.checkpoint.json`
- `/home/lupin/pantheon/.orchestrator/state.json`

Command root for real supervisor work:
`/home/lupin/pantheon-ci-deploy/dev-root`

## Boundary

This audit is a current-state gap record, not a completion claim. The twelve
loops are not operational as a full product system at this observation time.

The operator constraint is binding: fleet work means the real supervisor and
auto-worker lanes. Codex conversation subagents, direct config edits, receipts
without canonical task-state materialization, stale PR heads, and merged PRs
without governed closeout do not count as proof.

## Evidence Readback

Authoritative task-state and projection matched at the observation point:

- `ai-status.json updated_at = 2026-07-31T06:40:20Z`
- checkpoint `updated_at = 2026-07-31T06:40:20Z`
- supervisor task-state shadow: `ok=true`, `caught_up=true`
- supervisor lifecycle: `running`
- supervisor mode status: `idle`
- active auto workers observed by live PID/status scan: `0`

Addendum readback at `2026-07-31T11:59:43Z`:

- Authoritative supervisor state is `/home/lupin/pantheon/.orchestrator/state.json`,
  not the stale command-root shadow under `/home/lupin/pantheon-ci-deploy/dev-root`.
- Live supervisor PID `1633710` is running from
  `/home/lupin/pantheon-ci-deploy/dev-root`, with fresh heartbeat
  `2026-07-31T11:57:28Z`, `lifecycle=running`, `last_loop_error=null`, and
  task-state shadow `ok=true`, `caught_up=true`.
- The command-root local health script still reports the old PID `3775971` and
  a stale `2026-07-29T10:32:51Z` heartbeat because it reads the command-root
  shadow. That is a status-root split-brain symptom, not proof that the live
  supervisor is down.
- A second Wave 0 blocker is authoritative:
  `SUP-WORKER-WORKTREE-SOURCE-ROOT-20260730` is `blocked` on PR #4392 and must
  be merged/live-promoted before worker dispatch can be trusted.
- A fleet-resume controller already exists in task-state:
  `SUP-L12-FLEET-RESUME-AFTER-WAVE0-20260731`, depending on
  `SUP-ASSISTANT-DEV-BRIDGE-MATERIALIZATION-20260730`,
  `SUP-WORKER-WORKTREE-SOURCE-ROOT-20260730`, and
  `SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729`.

Fleet reconcile readback at `2026-07-31T12:25:00Z`:

- The Wave 0 exact-head reconcile packet was drained by the real supervisor,
  and real auto-workers, not Codex conversation subagents, processed both
  reconcile rows.
- `SUP-L12-STALE-REAPER-EXACT-HEAD-RECONCILE-20260731` is now `review`,
  owner `Codex2`, reviewer `Antigravity`, with PR #4395 head
  `607a474688566b1a62c4ec24998c4d6864d62a88`. Its owner finding is that
  PR #4385 remains at `f5e70e86e01bde005dae5fed94b151c9bc07f389`, but the
  subject README and evidence manifest name nonexistent anchor
  `9d53a94a265c55af4c8d15c50ab3751f1440ac0f` instead of actual anchor
  `9d53a94a295d71ee49aea6f4b96e47fbcfd29093`. Therefore #4385 must be
  repaired/reopened; it is not a Wave 0 satisfied dependency.
- `SUP-L12-RUNNING-OWNER-EXACT-HEAD-RECONCILE-20260731` is now
  `review_approved`, owner `Codex2`, reviewer `Antigravity`, with evidence
  commit `c4346b8d53941d665acd931d32a98b3802b1e7b2` and ReviewBus PR #4396.
  At the 12:25Z observation PR #4396 was still draft; the 12:46Z addendum
  supersedes that draft status and records that the PR is now ready but still
  blocked by protected merge/root-freeze closeout.
- Auto-integrator dry-runs still block #4385 and #4386 subject PRs on
  `mergeStateStatus=BLOCKED`; row-level `review_approved` remains insufficient.

Pipeline architecture readback at `2026-07-31T12:46:41Z`:

- The dispatch/closeout pipeline is not fully repaired end-to-end. The current
  evidence proves partial progress only: the real supervisor is alive, and
  supervisor/auto-worker lanes have processed reconcile rows, but the
  architecture still fails closed at materialization, worker source-root,
  protected merge/root-freeze, and governed closeout gates.
- #4390 remains open and `BLOCKED`, so the DevTaskPacket materialization repair
  has not completed the repo flow or governed closeout. Until #4390 is merged
  and proven from canonical task-state, a DevTaskPacket receipt is still weaker
  than canonical materialization/readback.
- #4392 remains open and `BLOCKED`, so worker source-root repair has not been
  merged/live-promoted. Until that happens, fleet dispatch is still vulnerable
  to status-root/source-root split-brain and stale or read-only worktree
  assumptions.
- #4395 moved to exact head `f68827c8e17d6a1f081afe24f62ba85c116166e8`.
  Branch CI and Pantheon canonical review gate are green, and Antigravity
  reviewed that exact head at `2026-07-31T12:42:18Z`, but auto-integrator still
  blocks it on `mergeStateStatus=BLOCKED`. It remains support evidence, not an
  integrated closeout.
- #4396 moved to exact head `19f71db59b94016aa0d6bf00cd3ead5bf8a9eb4f` and is
  no longer draft. Branch CI and Pantheon canonical review gate are green, but
  auto-integrator still blocks it on `mergeStateStatus=BLOCKED`; the current
  task row records the missing Human/Ops `Pantheon root merge freeze
  2026-07-27` exact-head context.
- The real supervisor materialized Wave 0X tasks
  `SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-20260731` and
  `SUP-L12-RUNNING-OWNER-EXACT-HEAD-PR-CLOSEOUT-20260731`, but both are
  currently `todo` after supervisor preemption. Worker runtime evidence shows
  earlier Codex2 runs for both were SIGTERM/preempted. This is a fleet
  scheduling/readiness gap and must not be counted as development completed.
- The current state is therefore not "dispatch/closeout pipeline repaired";
  it is "architectural repair in progress, with exact remaining gates known and
  supervisor-visible."

Supervisor re-dispatch readback at `2026-07-31T12:58:21Z`:

- Re-queuing updated specs under the original Wave 0X task IDs failed through
  the real dev-bridge, not by manual inspection. Receipt
  `.orchestrator/assistant-dev-packets/receipts/pkt-l12-wave0x-pipeline-blockers-requeue-20260731T1252Z.json`
  reports `Bridge assignment conflict`: both original task IDs are already
  bound to packet `pkt-l12-wave0x-fleet-reconcile-fallout-20260731T1225Z` and
  their old task spec hashes. The bridge currently has no safe "update an
  already materialized task spec" path.
- A superseding V2 packet
  `pkt-l12-wave0x-pipeline-blockers-supersede-20260731T1255Z` was drained and
  admitted by the real supervisor. It materialized
  `SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-V2-20260731` and
  `SUP-L12-RUNNING-OWNER-EXACT-HEAD-PR-CLOSEOUT-V2-20260731` in canonical
  task-state with current-head acceptance criteria.
- The packet requested Antigravity owner and Claude2 reviewer, but the
  supervisor/fleet scheduler fell back through helper-claim to Codex/Codex2
  lanes. This is real fleet behavior, not Codex conversation subagents, but it
  violates the preferred Antigravity/Claude-first operating intent unless
  readiness/fallback is explicitly justified.
- Both V2 tasks auto-started real worker runs, but both worker status files
  ended with `exit_code=143`, `signal=15`. The task rows were returned to
  `todo` after supervisor preemption:
  `SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-V2-20260731` at
  `2026-07-31T12:57:23Z` and
  `SUP-L12-RUNNING-OWNER-EXACT-HEAD-PR-CLOSEOUT-V2-20260731` at
  `2026-07-31T12:57:32Z`.
- Therefore current fleet status is: dispatch and materialization are proven
  for the V2 tasks, but auto-worker execution did not complete development.
  The remaining dispatch/closeout infrastructure gap is now narrower and
  concrete: immutable task-spec updates require superseding task IDs, and
  worker preemption/retry must reliably progress these tasks to terminal
  reviewed/archived states rather than cycling todo.

Worker preemption churn readback at `2026-07-31T13:00:37Z`:

- The V2 tasks did not merely fail once. Additional worker runs
  `codex-20260731T125821Z-f615aa77` and
  `codex-20260731T125858Z-acfa9855` also exited with `exit_code=143`,
  `signal=15`.
- At that readback both V2 rows were back to `todo`, with helper-claim ownership
  churn between Codex/Codex2 and reviewers Codex2/Codex. This confirms the
  fleet can drain/materialize/start workers, but currently cannot keep these
  repairs running long enough to complete.
- This is a supervisor/auto-worker scheduling and lifecycle defect that must be
  repaired or governed-blocked before more downstream twelve-loop product work
  is dispatched.

Scheduler root-cause readback at `2026-07-31T13:25:39Z`:

- The repeated `SIGTERM 15` exits were caused by an architecture mismatch, not
  by the Wave 0X task content. `higher_priority_ready_task_exists()` considered
  raw task status while the dispatcher separately applied provider eligibility,
  unchanged-event cooldown, and failure-loop/chair-triage gates. A task that
  could not actually dispatch could therefore preempt a healthy new worker and
  leave the fleet idle.
- PR #4399, task `SUP-PREEMPTION-DISPATCH-ELIGIBILITY-20260731`, makes
  preemption and dispatch share one lifecycle-candidate decision and adds a
  five-minute new-worker stability grace. Exact head
  `a924a6f3c0c54982d7efe145750cc99c57bc7f2e` passed 486 focused supervisor
  and dispatch-policy tests plus replay of the actual 13:01Z worker/task state.
  Branch CI is green and the PR is ready, but it remains open/`BLOCKED`; the
  fix is not yet live.
- A second independent runtime defect remains: the live command root repeatedly
  logs `run_scan` and `trim_seen_events` TypeError failures when legacy
  `seen_event_keys` values are compared. PR #4397,
  `SUP-SEEN-EVENT-KEYS-NONNULL-20260731`, is green but still open/`BLOCKED` and
  not live-promoted.
- The live supervisor PID `1633710` remains alive and caught up, but is idle
  with zero active workers. Heartbeat alone is therefore insufficient proof of
  a usable fleet while scan phases fail and the preemption fix is absent.
- Do not create V3 execution pressure or release Wave D/E until #4397 and #4399
  are merged/live-promoted and a canary worker survives beyond the grace window
  without erroneous priority preemption. After that proof, use new superseding
  IDs for the V2 rows because their current task/agent pairs carry failure-loop
  streaks.

Current PR readback:

| PR | Task / purpose | Head | State | Current gap |
| --- | --- | --- | --- | --- |
| #4390 | `SUP-ASSISTANT-DEV-BRIDGE-MATERIALIZATION-20260730` | `f13748e14145f432c4d8897e945552aef899c1a7` | open, `BLOCKED` | CI and canonical review gate are green, but protected merge/root-freeze and GitHub approval are absent. |
| #4392 | `SUP-WORKER-WORKTREE-SOURCE-ROOT-20260730` | `4e2ebeeb0635bed484bc2cdf7e8e5ff1bcb0ea9f` | open, `BLOCKED` | Branch CI is green; still needs Antigravity canonical review, Human/Ops root-freeze exact-head release, live command-root promotion, worker source-root config verification, supervisor restart/watchdog verification, and first isolated worker dispatch proof. |
| #4385 | `SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729` | `f5e70e86e01bde005dae5fed94b151c9bc07f389` | open, `BLOCKED` | Canonical task row is `review_approved`, but PR is still open/blocked; fleet-resume must not treat this as done until protected merge and governed closeout complete. |
| #4382 | post-#4380 gap dispatch packet | `ca00f813f4e6a5dfcfb2cf402ebba425a034d03e` | open, `BLOCKED` | JSON/checks are green, but the canonical task row still carries a stale rejected-review blocker and no approving review. |
| #4364 | `L12-VERIFY-OBS-001` | `f3756cec99a8c44d47c075a475c25cf86a4d3171` | open, `BLOCKED` | Branch CI is green; still needs exact-head review/approval and governed closeout. |
| #4376 | `SUP-L12-LONG-FINALIZE-LEASE-20260729` | `6f63b5f54c28514a68c6a7b3599889adf98553f9` | open, `BLOCKED` | Branch CI is green; still needs exact-head review/approval and governed closeout. |
| #4386 | `SUP-L12-RUNNING-OWNER-RECONCILE-20260729` | `2d5f692e960a22eef7c4b6d63002996a68468079` | open, `BLOCKED` | Canonical task row is `review_approved`, but PR is still open/blocked; current PR head differs from the row's noted reviewed head, so exact-head/closeout must be reconciled before counted. |
| #4395 | `SUP-L12-STALE-REAPER-EXACT-HEAD-RECONCILE-20260731` | `f68827c8e17d6a1f081afe24f62ba85c116166e8` | open, `BLOCKED` | Branch CI and Pantheon canonical review gate are green; Antigravity reviewed this exact head, but auto-integrator still blocks on merge state. Owner finding identifies a nonexistent evidence anchor in #4385 and recommends reopening/repair rather than counting #4385 done. |
| #4396 | `SUP-L12-RUNNING-OWNER-EXACT-HEAD-RECONCILE-20260731` | `19f71db59b94016aa0d6bf00cd3ead5bf8a9eb4f` | open, `BLOCKED` | No longer draft; Branch CI and Pantheon canonical review gate are green, but auto-integrator still blocks on merge state and missing Human/Ops root-freeze exact-head context. |
| #4397 | `SUP-SEEN-EVENT-KEYS-NONNULL-20260731` | `fd67904e2c1adb7256d4d9d9dc618105346be424` | open, `BLOCKED` | CI and canonical review are green, but the legacy seen-event normalization fix is not merged or live-promoted; live scan phases still throw TypeError. |
| #4399 | `SUP-PREEMPTION-DISPATCH-ELIGIBILITY-20260731` | `a924a6f3c0c54982d7efe145750cc99c57bc7f2e` | open, `BLOCKED` | Branch CI is green and PR is ready; still needs governed review/merge, live promotion, restart/readback, and a worker-survival canary. |
| #4363 | `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729` | `94695276e2d174505a107ccaa4346efb1692575e` | open, `BEHIND` | Must be rebased/refreshed before review or merge can be counted. |
| #4372 | `SUP-L12-STALE-PR-RETIRE-20260729` | `07f163cb21e047a491b1b90c5422dbba69ea0563` | open, `BEHIND` | Must wait for fresh accepted #4364 evidence, then rebase/refresh. |

## Pass 1 — Specification And Product Completion Audit

The twelve-loop product requirement has four layers:

1. Accepted domain loop implementation and manifest admission.
2. Backend and frontend truth surfaces showing desired/controller/failure,
   actual, provenance, degradation, and deployment identity.
3. Real verifier drills for knowledge, learning, runtime/capital/deployment,
   and observability/BFF behavior.
4. Hosted FE/BFF exact-identity proof, restart/no-duplicate/auth/tenant/safety
   proof, then protected final closeout.

Current product verdict:

| Requirement | Current authoritative state | Gap |
| --- | --- | --- |
| Backend truth | `L12-TRUTH-001` is treated as archived done by the current task graph, and current Wave D should not redispatch it. | It remains a prerequisite, not sufficient by itself. |
| Frontend truth | `L12-FE-TRUTH-001` is `blocked`, owner `Antigravity`, reviewer `Claude2`, waiting for `Claude2`. | Needs execute-plans evidence, exact frontend commit, BFF truth contract binding, and reviewer approval. |
| Knowledge verifier | `L12-VERIFY-KNOW-001` is `todo`, owner `Claude2`, reviewer `Antigravity`. | Needs real Source/Distillation/Alpha drill evidence with readbacks; no print-pass verifier. |
| Runtime verifier | `L12-VERIFY-RUNTIME-001` is `todo`, owner `Claude2`, reviewer `Antigravity`. | Needs real deployment/capital/runtime safety drill without live-capital activation. |
| Learning verifier rebuild | `L12-VERIFY-LEARN-REAL-VERIFIER-001` is missing from canonical task-state. | Must be materialized and implemented before learning proof can pass. |
| Learning verifier product proof | `L12-VERIFY-LEARN-001` is `blocked`, owner `Antigravity`, reviewer `Claude2`. | Prior verifier was rejected as self-attesting; depends on the real-verifier rebuild. |
| Observability verifier | `L12-VERIFY-OBS-001` is `review`, owner `Antigravity`, reviewer `Claude2`; #4364 head is `f3756cec...`. | Needs exact-head review, approval, merge, and governed task closeout. |
| Hosted proof | `L12-HOSTED-001` is `todo`, owner `Antigravity`, reviewer `Claude2`. | Must wait for frontend truth and verifier lanes, then prove hosted FE/BFF exact identities and safety/restart behavior. |
| Final closeout | `L12-CLOSE-001` is `todo`, owner `Claude2`, reviewer `Antigravity`. | Must wait for hosted, truth, signoff, and all verifier archive evidence. |

Pass 1 conclusion: the product is incomplete. The remaining work is not one
bug; it is a dependency-gated product proof graph.

## Pass 2 — Development And Test/Validation Gap Audit

Missing development:

1. `SUP-ASSISTANT-DEV-BRIDGE-MATERIALIZATION-20260730`: merge the real
   supervisor/dev-bridge repair from #4390 so receipt-only dispatch cannot be
   counted when canonical task-state materialization is absent.
2. `SUP-WORKER-WORKTREE-SOURCE-ROOT-20260730`: merge/live-promote #4392 so
   worker worktrees are created from the writable command/source root, then
   prove an isolated worker dispatch no longer fails on `.git/worktrees`
   read-only state.
3. `SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729`: finish protected merge and
   governed closeout for the stale failure-streak reaper; fleet-resume depends
   on it and cannot treat review approval alone as done. The 2026-07-31
   exact-head reconcile found the current #4385 evidence names nonexistent
   anchor `9d53a94a265c55af4c8d15c50ab3751f1440ac0f` instead of actual anchor
   `9d53a94a295d71ee49aea6f4b96e47fbcfd29093`, so a concrete evidence-anchor
   repair is required before #4385 can be counted.
4. `SUP-L12-FLEET-RESUME-AFTER-WAVE0-20260731`: after the three Wave 0
   blockers are merged/live-promoted/closed out, let the real supervisor
   redispatch the L12 Pantheon/Agora task graph and record active worker
   run IDs/PIDs or exact blockers.
5. `SUP-SEEN-EVENT-KEYS-NONNULL-20260731`: merge/live-promote #4397 and prove
   `run_scan` plus `trim_seen_events` complete without legacy-null TypeError
   across repeated supervisor cycles.
6. `SUP-PREEMPTION-DISPATCH-ELIGIBILITY-20260731`: merge/live-promote #4399,
   then prove preemption uses the same dispatch eligibility gates and preserves
   a new worker for the configured five-minute stability window.
7. `L12-FE-TRUTH-001`: finish execute-plans truth UI/state acceptance against
   the Pantheon BFF truth contract.
8. `L12-VERIFY-LEARN-REAL-VERIFIER-001`: implement real cross-service learning
   verifier behavior replacing the rejected self-attesting script.
9. `L12-VERIFY-KNOW-001`: run and archive the real knowledge verifier drill.
10. `L12-VERIFY-RUNTIME-001`: run and archive the real runtime/capital/deployment
   verifier drill.
11. `L12-VERIFY-LEARN-001`: run and archive learning proof only after the real
   verifier rebuild is accepted.
12. `L12-VERIFY-OBS-001`: finish exact-head review/merge/archive of the real
   observability verifier proof.
13. `L12-HOSTED-001`: prove hosted FE/BFF exact commit identities, restart,
   no-duplicate dispatch, auth/tenant negatives, and safe-write posture.
14. `L12-CLOSE-001`: final closeout proving every loop and excluding stale
    proof.
15. Support PR cleanup: #4363 and #4372 remain stale/behind; #4376 and #4364
    still need exact-head review/approval; #4386 remains open/blocked even
    though its task row says review-approved; #4396 records current-head support
    evidence and is no longer draft, but remains blocked from protected
    merge/governed closeout.
16. Dev-bridge task-spec update gap: an updated packet cannot revise already
    materialized task IDs; it fails with `Bridge assignment conflict`. Current
    workaround is superseding V2 task IDs, but the architecture still needs an
    explicit update/supersede policy and validation.
17. Worker preemption/retry gap: V2 workers launched repeatedly and then exited
    with `SIGTERM 15`; the supervisor returned rows to `todo` instead of
    completing development. Fleet completion remains unproven until
    retry/restart produces terminal reviewed/archived evidence.

Missing validation:

- Canonical task-state readback for every queued DevTaskPacket task.
- Explicit bridge behavior for updating or superseding already materialized
  task specs, including a test that same-ID spec changes fail closed with a
  useful operator path and do not silently leave workers on stale acceptance.
- Regression that activity-log-only `assign` rows cannot produce a successful
  dispatch receipt.
- Worker source-root readback proving new auto-worker worktrees come from
  `/home/lupin/pantheon-ci-deploy/dev-root`, not a stale/read-only status-root
  `.git/worktrees` path.
- Authoritative status-root health readback that distinguishes live supervisor
  state from stale command-root shadow state.
- Exact-head PR review for #4382/#4390/#4364/#4376.
- Exact-head/closeout reconciliation for #4385/#4386/#4392.
- Evidence-anchor repair for #4385, because the current #4385 evidence points
  at a nonexistent SHA.
- Governed ready/merge/closeout handling for #4396 before its exact-head support
  evidence can be integrated.
- Worker runtime preemption/retry validation proving `SIGTERM 15` worker exits
  are reconciled to a fresh run, terminal blocker, or governed retry, not left
  as recurring todo churn.
- Shared eligibility regression proving a task blocked by provider readiness,
  unchanged-event cooldown, or failure-loop policy cannot preempt a worker.
- New-worker grace regression and live canary proving a newly started worker is
  not priority-preempted during its first five minutes.
- Repeated-cycle `run_scan`/`trim_seen_events` proof after #4397 live promotion,
  with no seen-event TypeError in supervisor logs.
- Rebase/current-dev evidence refresh for #4363/#4372.
- Real service-bound verifiers with before/after readbacks.
- Tenant/RBAC/MFA/auth negative checks where applicable.
- Restart, DLQ/retry, and no-runtime-mutation proof for learning/runtime/hosted
  lanes.
- Hosted `deployment.json` FE/BFF exact identity proof and browser-visible
  truth UI proof.
- Governed `done` or reconcile archive for each task row; merged PR alone is
  insufficient.

Pass 2 conclusion: previous rounds repaired important infrastructure, but they
did not complete the product proof or its canonical dispatch/readback path. The
dispatch/closeout pipeline remains an unfinished architecture repair, not a
validated end-to-end platform capability.

## Pass 3 — Fleet Dispatch And Parallelization Audit

Current supervisor facts:

- Supervisor is alive and caught up.
- No active auto workers were running at observation time.
- The live supervisor is healthy in the authoritative status root, but the
  command-root local runtime-health script still reads a stale command-root
  state shadow. Use authoritative status-root readback for fleet status.
- The prior 2026-07-30 `pkt-l12-actionable-gap-execution-20260730T163500Z`
  receipt recorded eight `dispatched` rows, but those task IDs are absent from
  canonical `ai-status.json`. That receipt is therefore a false-positive
  admission, not proof of real supervisor work.
- `L12-VERIFY-LEARN-REAL-VERIFIER-001` is present in the intended task graph
  but still missing from canonical task-state.
- `SUP-L12-FLEET-RESUME-AFTER-WAVE0-20260731` is the proper supervisor-owned
  resume point for L12 fleets; it is intentionally `todo` until all Wave 0
  control-plane blockers are proven.

Safe parallelization after Wave 0 is accepted:

| Wave | Parallelism | Tasks | Preferred lanes | Gate |
| --- | --- | --- | --- | --- |
| 0 | parallel where independent | `SUP-ASSISTANT-DEV-BRIDGE-MATERIALIZATION-20260730`, `SUP-WORKER-WORKTREE-SOURCE-ROOT-20260730`, `SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729` | Antigravity/Claude2 first where available, cross-lane reviewer | Must merge/prove canonical materialization, worker source-root dispatch, and stale-streak cleanup before fleet resume. |
| 0X | parallel | `SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-20260731`, `SUP-L12-RUNNING-OWNER-EXACT-HEAD-PR-CLOSEOUT-20260731` | Antigravity/Claude2 first where available | Repair #4385 nonexistent anchor and move #4396 through governed PR/closeout handling without counting no-longer-draft or row-only approval as integration. |
| 0R | serial | `SUP-L12-FLEET-RESUME-AFTER-WAVE0-20260731` | owner: Antigravity, reviewer: Codex per current row | Lets the real supervisor redispatch L12 Pantheon/Agora tasks and records worker run IDs/PIDs or exact blockers. |
| A | parallel | `L12-VERIFY-OBS-001`, `SUP-L12-LONG-FINALIZE-LEASE-20260729`, `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729`, `SUP-L12-POST-4380-GAP-REVIEW-20260729`, `SUP-L12-RUNNING-OWNER-RECONCILE-20260729` | Antigravity/Claude2 first where available | Exact current-head review and governed closeout. |
| B | dependency-gated | `SUP-L12-STALE-PR-RETIRE-20260729` | Antigravity + cross reviewer | Waits for fresh #4364 evidence. |
| C | parallel | `SUP-L12-MERGED-ROW-RECONCILE-20260729`, `L12-FLEET-STATUS-SYNC-001` | current canonical owner/reviewer | Finalize review-approved support rows through governed archive. |
| D | parallel where independent | `L12-FE-TRUTH-001`, `L12-VERIFY-KNOW-001`, `L12-VERIFY-RUNTIME-001`, `L12-VERIFY-LEARN-REAL-VERIFIER-001` | Antigravity/Claude2 first | All depend on Wave 0 materialization. |
| E | serial/dependency-gated | `L12-VERIFY-LEARN-001`, `L12-HOSTED-001`, `L12-CLOSE-001` | cross-lane owner/reviewer | Learning waits real verifier; hosted waits FE truth + verifiers; closeout waits hosted/truth/signoff. |

Pass 3 conclusion: the correct next action is not direct Codex repair or
one-off deployment. It is a supervisor/dev-bridge task packet whose success is
verified by canonical materialization, then auto-worker pickup, then governed
review/archive. The Wave 0X preemption evidence also means "task created" is
not enough; the fleet must be observed restarting, completing, and archiving
the concrete fallout tasks. The 12:58Z V2 readback proves materialization and
startup but contradicts completion because both worker runs were preempted.

## Dispatch Artifact

Machine-readable execution graph:

`docs/bff/execution-tasks/2026-07-31-l12-current-gap-supervisor-dispatch/tasks.json`

This graph is designed for supervisor/auto-worker fleets. It deliberately keeps
Wave 0 as a gate so the system cannot again claim dispatch success from receipt
text alone.
