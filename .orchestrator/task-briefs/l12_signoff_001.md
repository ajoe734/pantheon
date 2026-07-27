# Task Brief: L12-SIGNOFF-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Install protected Human-Ops closeout verdict enforcement
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Sequence-11 owner evidence revalidated after the stale-binding rejection and final pre-review dev composition: 296 focused tests and 39 subtests, product-evidence schema/checksum, catalog canonical digest, source-hash epoch, PR #4210 merge/check evidence, canonical lease recovery with erased workspace env, pre-access forbidden roots, revoked/consumed ledger-tail protection, exact catalog/manifest/target/FE/BFF binding, and exactly-once done consumption. Codex2 must review PR #4261 at the exact immutable head named in the canonical handoff, append the formal evidence verdict, and approve with REVIEW_FILE plus REVIEW_PR=4261 and that actual REVIEW_HEAD_SHA. The owner must make no further branch mutation after handoff.

## Summary
在 final closeout 前安裝機器守門：受保護、可撤銷、不可重播的 Human/Ops 判決必須綁定 exact catalog、manifest、target 與部署 identity；fleet 不得自行簽發。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
