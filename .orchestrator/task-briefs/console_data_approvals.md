# Task Brief: CONSOLE-DATA-APPROVALS

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Populate /bff/approvals via promotion approvals
- Status: todo
- Owner: Claude2
- Reviewer: Codex
- Next: Assignment created

## Summary
promotion svc POST /api/v1/approvals 產真 approval;接 PANTHEON_GOVERNANCE_APPROVAL_API_URL 讀路徑。用該 domain 的真實 producer 產生真資料(禁止捏造);再重接 BFF 讀路徑(設 PANTHEON_BFF_*_STORE / 指向 live service / 加投影,如 scripts/project_research_to_bff_surfaces.py);驗收:live curl(Bearer op-dev:admin:mfa)該 /bff 面回 count>0 且 surface status=ok;在 services/control-plane/bff/tests 加/更新 contract test;stub dispatch 為 dev 安全姿態。範式見 docs/05/system-verification-rounds/console-population-research-slice.md。
