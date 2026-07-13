# Task Brief: PPL-ALLOC-014

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Fix sidebar raw i18n key + render-audit blind spot
- Status: review_approved
- Owner: Antigravity
- Reviewer: Claude
- Next: Reviewer approved: PR #296 merged with green gates, locale fix verified live on dev, render-audit regex widened as required. Handing back to owner Antigravity for finalization to done.

## Summary
瀏覽器層實走發現側欄裸 i18n key readiness.ep5Title（locale 只有 mgmt.readiness.* 巢）+ audit:render 正則盲點；直接修復 PR execute-plans #296
