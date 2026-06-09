# Task Brief: ASST-SKILL-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Frontend generic renderer: surfaces driven by the effective skill catalog
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Review approved by Claude2; reviewer artifact: docs/decisions/asst-skill-003-review.md. Catalog-driven renderer removes all hardcoded FE capability enumeration; BFF helper deny-by-default routing and descriptor-driven input/enable/confirm logic are correct. Returning to Codex for finalization.

## Summary
把 Management AI 工具列/命令/降級卡 action 改成遍歷 effective catalog 動態渲染（button/command/card_action 由 surface 決定，enable/confirm/輸入表單由 descriptor 決定），達到 parity 後移除寫死按鈕；FE 不得在原始碼列舉能力。

## Closeout Evidence

- Reviewed artifact: `docs/decisions/asst-skill-003-review.md`
- Merged delivery reviewed: PR #1177 / merge commit `ae0c12ed`
- Focused validation:
  - `git diff --check`
  - `python3 scripts/git/check_commit_trailers.py --range origin/dev..HEAD --skip-merge`
  - `! rg -n "ASSISTANT_SA_SD_GENERATE|ASSISTANT_SA_SD" execute-plans/src/agora/pages/AskPersonas.tsx execute-plans/src/lib/bff/managementAssistant.ts`
  - Current Pantheon task mirror does not include an `execute-plans/package.json`, so Vitest cannot be run from this worktree; reviewer approval records the frontend-specific acceptance result.
