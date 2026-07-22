# Task Brief: MGMT-OPS-009

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Persona Fleet and Evolution Journal link semantics
- Status: review_approved
- Owner: Antigravity
- Reviewer: Claude
- Next: Reviewed execute-plans PR #235: personaFleetMutationHref, fallback card labeling, and Evolution Journal focus banner all satisfy MGMT-OPS-009 acceptance. 31 unit tests (incl. 4 new) + full 1189-test suite + tsc --noEmit all clean. Non-blocking note: focus banner text is now hardcoded zh-TW instead of using t(). Hosted click-map evidence is MGMT-OPS-010 scope. Owner: wait for PR #235 integration-gate + merge into dev before running done.

## Summary
修正 Persona Fleet 最近 MUTATION 點到 Evolution Journal 的語義：有正式 id 就進正式 entry，沒有正式 id 就進 persona fallback summary；頁面不得顯示 mutation:nan，也不得把日期顯示成 Action。
