# Task Brief: OPS-GITHUB-CANONICAL-REVIEW-ENFORCEMENT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Human/Ops priority lane move: pause this lower-priority canonical enforcement task out of Codex owner capacity so Codex can compose L12-EVO-001 after dev moved. Claude/Antigravity remain preferred when available; do not dispatch this task ahead of L12 mainline. Resume later after L12-EVO/L12 fleet-control blockers clear.
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: PR #4303 exact head 293cb1d4780653c9753ee19d9567f917511b7b70 rejected. The prior baseline/plan-aware readback fix is correct and 131 focused tests pass, but AC2-AC4 remain unsatisfied: app_id 15368 is the generic github-actions App, not an issuer unique to the trusted verifier. Read-only exact-head metadata shows Commit trailers, Runtime mirror guard, Smoke acceptance, and every normal Actions check use app_id 15368; repo Actions allow all. A task-branch push workflow/job named 'Pantheon canonical reviewer attestation' (or checks:write API call) can therefore emit the same exact-head name/app success without any signed reviewer envelope. Replace the required-check trust source with provenance unavailable to candidate workflows/shared owner credentials (for example a dedicated external GitHub App or an enforceable immutable required-workflow mechanism), and add a negative forgery regression/evidence. Independently, canonical-review-gate.yml only filters dev/master for schedule: issue_comment/workflow_dispatch/non-schedule PRs accept any .base.ref, then lines 125-142 checkout and execute that base's checker under a checks:write token. Fail before checkout unless base_ref is exactly dev or master, pin checkout to the captured base SHA, and test a PR targeting an attacker-controlled task branch. Refresh activation-plan/evidence trust claims/app identity, update from current dev, and re-handoff a new exact head. No live protection/settings mutation.

## Summary
封住 repo 內腳本無法攔截的 GitHub 網頁與裸 API 合併入口，以 exact-head canonical reviewer attestation 驅動 required check；live branch protection 啟用須 Human/Ops 明確核准。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
