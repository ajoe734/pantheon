# Twelve-loop fleet recovery execution packet — 2026-07-27

Status: dispatchable recovery packet

Source audit:
`docs/04/pantheon_twelve_loop_gap_fleet_recovery_2026-07-27/TWELVE_LOOP_GAP_FLEET_RECOVERY_AUDIT.md`

Archive snapshot:
`docs/04/pantheon_twelve_loop_gap_fleet_recovery_2026-07-27/archive/20260727T203949Z-current-state-snapshot.md`

Machine task catalog: `tasks.json`

Existing-task binding map: `CANONICAL_EXISTING_TASK_BINDINGS.md`

## Dispatch intent

This packet is for supervisor-managed auto-workers. It must not be implemented
by ad hoc conversation subagents. The task plan intentionally separates:

1. newly missing fleet-control repairs that unblock reliable dispatch and
   review;
2. PR/review closeout work that must continue through existing canonical tasks
   because the supervisor artifact-conflict guard rejects duplicate active
   owners;
3. loop-family verification tasks that wait only for the specific delivery
   lanes they need;
4. hosted and final closeout tasks that must remain last.

Claude and Antigravity are preferred only when the supervisor reports them
healthy and unpaused. If those lanes are quota-paused, disabled, or auth-down,
Codex/Codex2 may proceed as real supervisor-admitted auto-workers, with the
reason recorded in task notes. The goal is healthy fleets, not symbolic labels.

## Parallelization map

| Wave | Can run in parallel? | Tasks | Why |
|---|---|---|---|
| 0 | yes | `L12-FLEET-STATUS-SYNC-001`, `L12-FLEET-WORKER-OUTCOME-001`, `L12-GITHUB-REVIEW-BRIDGE-001` | control-plane/fleet repairs touch different subsystems |
| 1 | yes, via existing tasks | `OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001`, `L12-BFF-001`, `L12-CURRENT-GAP-FLEET-AUDIT-20260727`, `L12-EVO-001`, `L12-DIST-001` | separate PRs/branches; do not create duplicate active tasks for overlapping artifacts |
| 2 | yes after prerequisites, via existing tasks | `L12-MANIFEST-001`, `L12-VERIFY-KNOW-001`, `L12-VERIFY-LEARN-001`, `L12-VERIFY-RUNTIME-001`, `L12-VERIFY-OBS-001` | loop-family verification lanes are already represented on the canonical board |
| 3 | mostly serial, via existing tasks | `L12-HOSTED-001`, `L12-CLOSE-001` | hosted evidence consumes merged/backend/frontend truth; final closeout consumes everything |

## Immediate dispatch set

The following new tasks should be assigned now because they unblock fleet
health and review correctness:

- `L12-FLEET-STATUS-SYNC-001`
- `L12-FLEET-WORKER-OUTCOME-001`
- `L12-GITHUB-REVIEW-BRIDGE-001`

The PR, verification, hosted, and final closeout work must be attached to the
existing canonical tasks listed in `CANONICAL_EXISTING_TASK_BINDINGS.md`. Do not
assign duplicate standalone tasks for those workstreams; the supervisor
artifact-conflict guard is authoritative.

## Non-negotiable gates

- Do not mark any task done without branch, commit, push, PR, exact-head CI,
  independent review, merge when applicable, and archived evidence.
- Do not claim loop maturity from registry edits alone.
- Do not use local snapshot or fixture truth as live proof.
- Do not perform live broker/capital writes; capital proof must remain governed
  paper/safe-write unless Human/Ops explicitly opens a separate live gate.
- Do not modify `.orchestrator/config.json` as part of these tasks unless a
  specific task explicitly owns a config change and ships it through PR review.
- Do not use Codex conversation subagents as fleets.
- Use clean task worktrees; the shared `/home/lupin/pantheon` checkout is dirty
  and live.

## Task catalog summary

| Task | Owner | Reviewer | Primary artifacts |
|---|---|---|---|
| `L12-FLEET-STATUS-SYNC-001` | Codex | Codex2 | `.orchestrator/supervisor.py`, `scripts/ai_status.py`, tests |
| `L12-FLEET-WORKER-OUTCOME-001` | Codex2 | Codex | `.orchestrator/supervisor.py`, worker runtime tests |
| `L12-GITHUB-REVIEW-BRIDGE-001` | Codex | Codex2 | GitHub review bridge / review bus tests |

All other workstreams are bound to existing task IDs in
`CANONICAL_EXISTING_TASK_BINDINGS.md`.

## Live dispatch update — 2026-07-27T21:30Z

The first fleet run from this packet was executed through real
supervisor-managed workers. It changed the execution state:

- PR #4269 / `L12-CURRENT-GAP-FLEET-AUDIT-20260727` is now merged and archived
  `done` at merge commit `58f7ee46a95b55fc7a88bd399cd40e55350fbf73`.
- PR #4273 / `OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001` was independently
  approved, recomposed onto latest `dev`, merged as
  `db658d8dc88dfc1e9abd6cec55e9c7e86b9a269a`, and archived `done`.
- PR #4267 / `L12-EVO-001` was reopened with concrete acceptance failures in
  direct failed-receipt compensation and default compose tenant authority.
- PR #4193 / `L12-DIST-001` was reopened with a concrete Registry
  idempotency/lineage failure.
- `L12-GITHUB-REVIEW-BRIDGE-001` landed core bridge PR #4280 as
  `16296c35fd2e604f3ecf2d06dec80da0040ee8e0`; follow-up PR #4281 remains open
  for exact-head reopen binding.
- `L12-FLEET-WORKER-OUTCOME-001` remains an active control-plane repair.

This update does not change the machine-readable task catalog. It clarifies the
current dispatch frontier: do not create duplicate tasks for these reopened
streams; continue through their canonical task IDs.
