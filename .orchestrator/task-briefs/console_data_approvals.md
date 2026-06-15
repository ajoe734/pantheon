# Task Brief: CONSOLE-DATA-APPROVALS

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Populate /bff/approvals via promotion approvals
- Status: in_progress
- Owner: Claude2
- Reviewer: Codex
- Next: Review changes requested: projection script defaults to http://promotion-svc:8089, but docker-compose service URL is http://promotion:8089. Running the script with defaults exits 0 and writes an empty approval_decisions.json; because PANTHEON_BFF_APPROVAL_DECISION_STORE is now explicit and existing files win over HTTP, that empty file can shadow PANTHEON_PROMOTION_API_URL and keep /bff/approvals count=0. Fix default/docs and add a projection-script regression test or fail-safe so a failed fetch cannot poison the BFF store. Codex validation passed: pytest -q services/control-plane/bff/tests/test_bff_approvals_surface_contract.py; pytest -q services/control-plane/bff/test_read_store_service_clients.py services/control-plane/bff/test_read_store_deployment.py; pytest -q services/promotion/test_service.py.

## Summary
promotion svc POST /api/v1/approvals 產真 approval;接 PANTHEON_GOVERNANCE_APPROVAL_API_URL 讀路徑。用該 domain 的真實 producer 產生真資料(禁止捏造);再重接 BFF 讀路徑(設 PANTHEON_BFF_*_STORE / 指向 live service / 加投影,如 scripts/project_research_to_bff_surfaces.py);驗收:live curl(Bearer op-dev:admin:mfa)該 /bff 面回 count>0 且 surface status=ok;在 services/control-plane/bff/tests 加/更新 contract test;stub dispatch 為 dev 安全姿態。範式見 docs/05/system-verification-rounds/console-population-research-slice.md。
