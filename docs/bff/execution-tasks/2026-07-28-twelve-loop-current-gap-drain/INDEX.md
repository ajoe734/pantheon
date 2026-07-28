# Twelve-Loop Current Gap Drain Execution Packet

Packet ID: `2026-07-28-twelve-loop-current-gap-drain`

Source audit:
`docs/04/pantheon_twelve_loop_gap_2026-07-26/archive/THREE_PASS_GAP_AUDIT_2026-07-28T1900Z.md`

Machine-readable task split:
`docs/bff/execution-tasks/2026-07-28-twelve-loop-current-gap-drain/tasks.json`

Freshness addendum: `2026-07-28T20:30:00Z`

## Goal

Drain the current twelve-loop gaps without pretending the program is already
operational.  Work must run through real supervisor/auto-worker lanes.  Codex
collaboration subagents are not part of this packet.

## Dispatch Principles

- Do not edit `.orchestrator/config.json` as a dispatch shortcut.
- Prefer healthy real supervisor workers.  As of the 20:30Z addendum, prefer
  `Antigravity` owner lanes and `Claude2` reviewer lanes when available; do not
  assign new work to aggregate `Claude` unless a fresh readiness probe proves it.
- When aggregate provider lanes fail but concrete slots are valid, dispatch to
  the concrete slot and record the routing finding.
- Do not dispatch dependency-blocked downstream tasks.  The supervisor stale
  guard is expected to reject them.
- Do not make GitHub approvals or root-freeze statuses with the PR author's
  GitHub identity.
- Do not dispatch `L12-CLOSE-001` until hosted/truth/verifier/signoff evidence
  is terminal.

## Wave 0 - Active Review And Closeout Drain

These lanes can run concurrently now:

1. `L12-FLEET-STATUS-SYNC-CLOSEOUT-DRAIN-20260728`
   - watches/records #4297 closeout blocker;
   - finishes wrapper evidence;
   - never writes status for `L12-FLEET-STATUS-SYNC-001`.
2. `L12-GAP-MERGE-QUEUE-REVIEW-DRAIN-20260728`
   - finishes #4311 review evidence;
   - records GitHub/root-freeze blocker if still blocked.
3. `OPS-L12-PROVIDER-READINESS-REVIEW-DRAIN-20260728`
   - current live lane is owner `Antigravity`, reviewer `Claude2`;
   - reviews #4312 head `c213a7a657d6cf661ec67b1d09682250fbad0247`;
   - must verify live worker-runtime truth, especially that
     `antigravity1-1-20260728T190729Z-8aeb78de` is completed, not running;
   - preserves per-slot truth: `claude2` ready, aggregate `claude` not ready,
     Antigravity ready.
4. `OPS-L12-GITHUB-ROOT-GATE-UNBLOCK-20260728`
   - Human/Ops or independent GitHub identity lane;
   - supplies or records the missing independent approval/root-freeze gate.

## Wave 1 - Provider And BFF/Root-Gate Unblock

Runs after Wave 0 clarifies whether provider/root gates are truly available:

1. `OPS-L12-OPENCLAW-GATEWAY-CONFIG-20260728`
   - configure or document absence of `OPENCLAW_GATEWAY_URL`;
   - rerun OpenClaw readiness smoke.
2. `OPS-L12-CLAUDE-CLI-READINESS-20260728`
   - restore/verify aggregate Claude CLI binary/auth or formally keep it
     paused;
   - do not conflate aggregate `Claude` with healthy `Claude2`.
3. `L12-BFF-001`
   - current status is `review_approved`, not `todo`;
   - do not run `done` until PR #4316 is exact-head root-gated/merged and the
     committed review evidence is internally consistent.

## Wave 2 - Manifest And Truth

Runs only after BFF and closeout prerequisites are terminal:

1. `L12-MANIFEST-001`
   - prove unified twelve-loop worker manifest/readiness.
2. `L12-TRUTH-001`
   - prove backend/controller/operator truth readback.
3. `L12-FE-TRUTH-001`
   - cross-repo `execute-plans` frontend truth implementation and browser
     evidence.

## Wave 3 - Parallel Product Verifiers

Run these four lanes in parallel after truth/manifest prerequisites:

1. `L12-VERIFY-KNOW-001`
2. `L12-VERIFY-LEARN-001`
3. `L12-VERIFY-RUNTIME-001`
4. `L12-VERIFY-OBS-001`

Each verifier must produce real product drill evidence, not seed-only or
unit-test-only proof.

## Wave 4 - Hosted And Final Closeout

1. `L12-HOSTED-001`
   - deploy current FE/BFF identities;
   - prove restart/recovery/no duplicate effects/auth/tenant/safety/mobile and
     desktop evidence.
2. `L12-CLOSE-001`
   - final protected closeout only after hosted/truth/signoff evidence is
     terminal.

## Current Known Blockers To Preserve

- #4297: Branch CI and Pantheon canonical review gate are green; GitHub merge is
  still blocked by independent approval / root merge-freeze authority.  The
  current task row says Antigravity must re-approve exact head
  `6b2fd109a885d7eb26a985d621ef3ef9d3e26753`.
- #4311: same root merge-freeze blocked class after canonical review evidence.
- #4312: current head `c213a7a657d6cf661ec67b1d09682250fbad0247` has green CI
  but still requires Claude2 review for live-truth consistency.
- #4316 / `L12-BFF-001`: `review_approved`, but blocked by the required
  `Pantheon root merge freeze 2026-07-27` status and merge acceptance.
- Claude family: `claude2` is currently ready; aggregate `claude` and
  `claude1-1`..`claude1-4` are auth-not-ready.
