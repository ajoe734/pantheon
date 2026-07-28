# Twelve-Loop Current Gap Drain Execution Packet

Packet ID: `2026-07-28-twelve-loop-current-gap-drain`

Source audit:
`docs/04/pantheon_twelve_loop_gap_2026-07-26/archive/THREE_PASS_GAP_AUDIT_2026-07-28T1900Z.md`

Machine-readable task split:
`docs/bff/execution-tasks/2026-07-28-twelve-loop-current-gap-drain/tasks.json`

## Goal

Drain the current twelve-loop gaps without pretending the program is already
operational.  Work must run through real supervisor/auto-worker lanes.  Codex
collaboration subagents are not part of this packet.

## Dispatch Principles

- Do not edit `.orchestrator/config.json` as a dispatch shortcut.
- Prefer healthy real supervisor workers.  Use Claude/Antigravity only when
  their readiness is proven by current runtime evidence.
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
   - reviews #4312;
   - repairs trailer/rebase if returned to owner;
   - preserves the negative readiness verdict.
4. `OPS-L12-GITHUB-ROOT-GATE-UNBLOCK-20260728`
   - Human/Ops or independent GitHub identity lane;
   - supplies or records the missing independent approval/root-freeze gate.

## Wave 1 - Provider And BFF Unblock

Runs after Wave 0 clarifies whether Claude/OpenClaw are truly available:

1. `OPS-L12-OPENCLAW-GATEWAY-CONFIG-20260728`
   - configure or document absence of `OPENCLAW_GATEWAY_URL`;
   - rerun OpenClaw readiness smoke.
2. `OPS-L12-CLAUDE-CLI-READINESS-20260728`
   - restore/verify Claude CLI binary/auth or formally keep Claude paused.
3. `L12-BFF-001`
   - restore BFF closeout on a healthy lane or governed reassignment;
   - produce formal closeout/archive evidence.

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
  still blocked because the current identity cannot approve its own PR.
- #4311: same blocked class after canonical review gate success.
- #4312: behind and Commit trailers failing.
- OpenClaw: `OPENCLAW_GATEWAY_URL` missing.
- Claude: current readiness evidence reports missing/unavailable CLI/auth.

