# Task Brief: OPS-PROMOTE-PR-CI-TRIGGER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promote PR CI trigger and auto-merge path
- Status: review_approved
- Owner: Codex
- Reviewer: Antigravity
- Next: 獨立審查通過 Task OPS-PROMOTE-PR-CI-TRIGGER-001 exact head a72f80ddad120452b0d0c8cd4549a55fe771a942：
1. Branch CI exact-head dispatch 契約修復與 auto-merge 路徑完全落實於 publish_promote.py / branch-ci.yml / publish-promote.yml。
2. 實證 proof 完成：Fresh promote PR #4378 (release/v2026.07.29.8) 觸發 Branch CI 驗收通過並成功自動合併至 master (2c9388e07b9a99ac2938d58a0edf6e4d34002dd5)。
3. 27 個歷史舊 promote PRs (含 PR #4138 等) 依據 release 提交祖先關係與 master 可達性證明，完成合規退役關閉。
4. 本地 27 項 PublishPromoteTests unittest、75 項 pytest 測試全數 100% 綠過，證據文件 docs/deployment/evidence/supervisor/OPS-PROMOTE-PR-CI-TRIGGER-001/evidence.json 與 README.md 均完整且一致。

## Summary
修復 promote/* PR 沒有 required checks 導致 auto-merge 永遠卡住的 CI/dispatch 治理缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
