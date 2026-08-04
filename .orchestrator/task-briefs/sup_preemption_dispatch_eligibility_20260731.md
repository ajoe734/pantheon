# Task Brief: SUP-PREEMPTION-DISPATCH-ELIGIBILITY-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Govern exact-head scheduler eligibility fix through live canary
- Status: review_approved
- Owner: Codex
- Reviewer: Antigravity
- Next: Closeout evidence is ready. PR #4399 merged as `894eb813c7cb5609ae517103a727d93ba8cbd1ed`; the exact merge runs from immutable live root `/home/lupin/pantheon-ci-deploy/dev-root-894eb813c7cb-live` under supervisor PID `669007`. The real canary `codex-20260731T151146Z-323146a8` on lane `codex2-1` retained PID chain `638723 -> 638790 -> 638797` for more than fifteen minutes and across the supervisor restart, including a nine-sample 361-second heartbeat window.

## Summary
Review and close out the existing #4399 architecture fix. It makes preemption and dispatch share the same eligibility ladder and protects new workers for five minutes; live acceptance waits for #4397 and a real canary.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Delivered Cut
- Dependency PR #4397 merged first as `cf6a8fed7baeb102330bf32af3f59cc347d1e92a` and is an ancestor of the #4399 merge.
- Antigravity approved exact PR head `6f391cfd4cde8fcee0a7f913bfe2937aba955d15`; Human/Ops supplied the exact-head root-freeze status before protected merge.
- Exact merged tests: `486 passed, 4 subtests passed`; focused provider/capacity/cooldown/failure-loop/grace tests: `5 passed`.
- Live supervisor source tree: `d1d55a322bee18df6d114403ece627bfb946f235`; live config hash remained `99809368a6c040fba01700f3423e86561e261f928b9764b366d28c7fcaa87bc6`.
- `.orchestrator/config.json` was not edited and Wave D/E was not released.

## Evidence
- `docs/deployment/evidence/twelve-loop-gap/SUP-PREEMPTION-DISPATCH-ELIGIBILITY-20260731/README.md`
- `docs/deployment/evidence/twelve-loop-gap/SUP-PREEMPTION-DISPATCH-ELIGIBILITY-20260731/evidence.json`
