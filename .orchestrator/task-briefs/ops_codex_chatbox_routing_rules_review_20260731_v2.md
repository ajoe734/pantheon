# Task Brief: OPS-CODEX-CHATBOX-ROUTING-RULES-REVIEW-20260731-V2

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Validate and hand off chatbox routing rules for independent review
- Status: review_approved
- Owner: Codex
- Reviewer: Antigravity
- Next: Preserve the reviewed evidence, publish the owner reassignment, and obtain Antigravity exact-head re-approval for PR #4405 before integration and `done`.

## Summary
Governed review handoff for the already published routing-policy PR. Codex validates and hands off; Antigravity independently reviews the exact head. No parallel implementation is authorized.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Owner Validation Anchor (2026-07-31)
- Inspected PR #4401 at exact base `cf6a8fed7baeb102330bf32af3f59cc347d1e92a` and exact head `1cc28e07ecaee0b03c4d26c76e05dcea31952d79`; the range changes only root `AGENTS.md` with 74 additions and no deletions.
- `git diff --check cf6a8fed7baeb102330bf32af3f59cc347d1e92a 1cc28e07ecaee0b03c4d26c76e05dcea31952d79` passed.
- Twenty-one focused policy-text assertions passed for the bounded direct repair lane, dashboard example, read-only/deduplicated integration planning, governed task packets, supervisor receipt and materialization, monitoring without takeover, read-only extension subagents, sole supervisor dispatch authority, Live Repair preservation, and non-independent Codex/Codex2 identities.
- Cross-checked the exact-head `AGENTS.md` against its existing Live Repair Rule, Management AI dev bridge routes, repair-worktree boundary, and supervisor handoff path; no conflicting or bypassing rule was found.
- GitHub reported PR #4401 as `OPEN`, `MERGEABLE`, and `BEHIND`; all nine visible check runs were successful. The task does not authorize updating the PR, so independent review remains bound to head `1cc28e07ecaee0b03c4d26c76e05dcea31952d79`.
- Governed `progress` and `handoff` initially failed closed because the inherited command runtime binding was stale; a fresh supervisor dispatch bound runtime `894eb813c7cb5609ae517103a727d93ba8cbd1ed` and completed the Antigravity handoff.

## Independent Review And Closeout Evidence
- Antigravity approved the exact head against base `894eb813c7cb5609ae517103a727d93ba8cbd1ed` at audit event `ai-status-event-ea9bf7bad17a40cdb24732470506a717c097a5ca0c48dd68e20270b320e990b5` after `git diff --check`, 22 focused policy-text assertions, and all nine GitHub Branch CI checks passed.
- The reviewer-bound evidence path is `docs/reviews/ops-codex-chatbox-routing-rules-review-20260731-v2.md`.
- This task closes exact-head validation and independent review only; it does not modify PR #4401 or claim that the routing-policy change is merged into `dev`.

## Owner Closeout Gate (2026-07-31)
- Governed task state binds Antigravity's approval of PR #4405 to head `01a4f15d62fdfd49606a79364d2386a0402110b0`.
- PR #4405 later advanced to `d0ffa07ee91ae87beb8995ae33aef8b7cda50ce7` through an owner-metadata anchor and a non-force `dev` refresh; all visible checks passed, but green CI does not replace exact-head review.
- `python3 scripts/git/auto_integrator.py --task-id OPS-CODEX-CHATBOX-ROUTING-RULES-REVIEW-20260731-V2 --status-file "$PANTHEON_STATUS_ROOT/ai-status.json" --json` failed closed with `approval_head_mismatch`.
- Keep the assigned owner and reviewer unchanged. Antigravity must inspect and approve the final PR #4405 head before the integrator may merge it and the owner may run governed `done`.
