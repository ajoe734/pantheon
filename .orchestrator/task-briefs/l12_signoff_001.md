# Task Brief: L12-SIGNOFF-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Install protected Human-Ops closeout verdict enforcement
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Review binding is stale: dispatch requires REVIEW_HEAD_SHA=8a56c20480f34f6f7a5b2c7f96170f1cbfceefd2, but GitHub PR #4261 current exact head is f6f9b604146680a4335d6879ffb01426be2c1752 after a post-handoff origin/dev merge. Exact-head approval cannot truthfully bind the earlier SHA. Security/acceptance review otherwise passed at 8a56c204: 294 focused tests and 39 subtests, product-evidence schema/checksum, catalog canonical digest, source-hash epoch, PR #4210 merge/check evidence, canonical lease recovery with erased workspace env, pre-access forbidden roots, revoked/consumed ledger-tail protection, exact catalog/manifest/target/FE/BFF binding, and exactly-once done consumption. Required correction: update the canonical review handoff to PR #4261 current immutable head, make no further branch mutation after handoff, and redispatch Codex2 so approval can bind REVIEW_FILE plus the actual current REVIEW_HEAD_SHA.

## Summary
在 final closeout 前安裝機器守門：受保護、可撤銷、不可重播的 Human/Ops 判決必須綁定 exact catalog、manifest、target 與部署 identity；fleet 不得自行簽發。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
