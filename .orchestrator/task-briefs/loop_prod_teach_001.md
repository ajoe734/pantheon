# Task Brief: LOOP-PROD-TEACH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Fail-closed Persona Teaching on authoritative data
- Status: review_approved
- Owner: Codex2
- Reviewer: Claude
- Next: owner closeout; PR #3708 is green and must merge before `AI_NAME=Codex2 ./scripts/ai-status.sh done LOOP-PROD-TEACH-001`

## Summary
移除 STUB1/STUB2、stub-ref 與 unconditional passed proof；evaluation 讀 canonical versioned dataset、freshness、threshold policy，資料不足即 fail closed，preview worker 預設啟動。

## Closeout Readback
- Status source: `AI_NAME=Codex2 ./scripts/ai-status.sh show LOOP-PROD-TEACH-001`
- Review gate: status root reports `review_approved` for owner `Codex2` and reviewer `Claude`.
- Review file: `docs/deployment/evidence/loop-product-level/LOOP-PROD-TEACH-001/evidence.json`
- PR gate: `https://github.com/ajoe734/pantheon/pull/3708` must be merged to `dev` before final `done`.
