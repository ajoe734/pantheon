# Task Brief: L12-ALPHA-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Dispatch approved Alpha replication into authoritative ExperimentRuns
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: 審查通過：初版 PR #4147 已合併 dev（merge dd9d83722b24598a623692c2b6ca8b80f159fe04）且 required checks 全綠；review fixes 03264cb0eaebd2bf9d2ddcf002096e57fa731c57 已推送，該 SHA 的 Commit trailers、Runtime mirror guard、Smoke acceptance 均 success。獨立重跑 scoped 60 passed、acceptance 15 passed，覆蓋 approved-only、canonical tenant/spec key、lease fencing/expiry、A-B-A replay restart、crash/failure/DLQ、真 FastAPI authority task/run readback與 evidence refs；py_compile、evidence SHA-256/JSON、git diff --check 皆通過。請 owner 依 closeout skill 開修正 PR、合併 dev 後再 done。

## Summary
統一 StrategySpec identifier，限制 approved review 才能進 queue，補 tenant、lease、DLQ/replay，將結果寫入真實 research authority 而非 stub/local run。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
