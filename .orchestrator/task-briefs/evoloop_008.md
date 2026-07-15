# Task Brief: EVOLOOP-008

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Full-cycle live verifier
- Status: review_approved
- Owner: Antigravity
- Reviewer: Claude
- Next: APPROVED: 11-step full-cycle verifier (breach->incident->postmortem->proposal->approve->dispatch retrain->artifact v2 mutation/approve->redeploy followthrough->governance->deployment plan/dispatch->new binding->journal) covers all 4 acceptance criteria; artifact -v2 naming logic in verify_e2e_evolution_loop.py matches real retrain logic in services/research/main.py:893-897; per-step RuntimeErrors give distinct failure attribution; idempotency handled via registry pre-check + active-target collision avoidance + run_seed-unique ids. Minor non-blocking: line 349 log f-string missing braces around pm_draft.get('status') (cosmetic only, doesn't affect exit code).

## Summary
寫全圈 live 驗證:breach(真實或經 producer 正式入口注入)→ incident → sweep proposal → approve → dispatch worker 自動 execute → research work item → artifact v2 → promote → binding v2 上線 → 交易 → 演化日誌記錄完整一圈(每段有 linked id)。每段失敗要能分辨;冪等可重跑;納入 scripts/run_e2e_verifiers.sh。注入指引見 .orchestrator/task-briefs/evochain_001_upstream_decision.md。與 EVOCHAIN-010(觀測半圈 verifier)分工不重疊。
