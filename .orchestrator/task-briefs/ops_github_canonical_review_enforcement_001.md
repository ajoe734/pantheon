# Task Brief: OPS-GITHUB-CANONICAL-REVIEW-ENFORCEMENT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Human/Ops priority lane move: pause this lower-priority canonical enforcement task out of Codex owner capacity so Codex can compose L12-EVO-001 after dev moved. Claude/Antigravity remain preferred when available; do not dispatch this task ahead of L12 mainline. Resume later after L12-EVO/L12 fleet-control blockers clear.
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Next: Helper-claimed by Codex while Claude is dispatch-paused.

## Summary
封住 repo 內腳本無法攔截的 GitHub 網頁與裸 API 合併入口，以 exact-head canonical reviewer attestation 驅動 required check；live branch protection 啟用須 Human/Ops 明確核准。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Repository Delivery
- Anchor commit: `100e0ef40` (`OPS-GITHUB-CANONICAL-REVIEW-ENFORCEMENT-001: anchor signed gate`).
- Trusted-base workflow: `.github/workflows/canonical-review-gate.yml`.
- Protected public-key registry: `.github/canonical-review-keys.json` (empty and fail-closed until Human/Ops bootstrap).
- Exact-head signer/verifier and protection readback: `scripts/git/canonical_review_check.py`.
- Focused regressions: `scripts/git/test_canonical_review_check.py`.
- Evidence manifest: `docs/deployment/evidence/supervisor/OPS-GITHUB-CANONICAL-REVIEW-ENFORCEMENT-001/evidence.json`.

## Human/Ops Boundary
- This task did not change GitHub branch protection, repository auto-merge, repository variables, reviewer keys, or live PR merge state.
- Live activation is fail-closed and requires a fresh baseline, Human/Ops authorization, a canary Actions-app check, exact readback, and retained rollback payloads.
- A 2026-07-28 readback observed external dev-protection drift while this task was running: the scoped review endpoint reported one required approval and admin enforcement was enabled. The evidence preserves this truth instead of overwriting it with the earlier zero-approval/admin-unenforced baseline.
