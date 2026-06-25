# Task Brief: OPS-STATUS-TREE-GIT-GUARD

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Harden status-root vs destructive git ops + recovery tool
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Supervisor resumed OPS-STATUS-TREE-GIT-GUARD for finalize after successful dispatch.

## Summary
事故根因修復:2026-06-20 canonical status-root tree 被一次 git reset --hard/clean 還原,清掉 untracked 運行檔並把 live ai-status.json 倒回舊 HEAD(連帶拖回 stale DATASTRAT-PERSONA-005 缺 phase 欄位、一度讓 sync_all 崩潰)。reset --hard/checkout-- 在 worker allowlist(provider_permissions.py:441-442)。治本:(1)讓 status-root live state 不受破壞性 git 操作影響(snapshot/commit ai-status.json,或移出 working tree,或 guard 禁 worker 對 status-root reset/clean);(2)加 state.json→ai-status.json(含 depends_on)recovery 工具;(3)sync_all 對缺欄位 task 要 fail-soft 不崩。參考 OPS-DEPLOY-DIRTY-WORKTREE。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。
