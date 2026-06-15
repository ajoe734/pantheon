# Task Brief: BFFGAP-WORKFLOWS-HOOKS

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: BFF GET /bff/workflows + GET /bff/hooks (P2)
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Review approved: all tests pass, canonical envelope correct, degraded state correct, scope boundary clean. Returned to Codex for closeout.

## Summary
新增兩個自動化登錄讀端點:GET /bff/workflows(workflow templates)、GET /bff/hooks(cron/hook registry)。端點回 canonical list 信封(data/items/page_info/meta);store 空時回明確 degraded 信封(status:unavailable,source:missing)非裸[];沿用既有 auth/CORS;更新 BFF_API_CONTRACT.md 與 OpenAPI;在 services/control-plane/bff/tests/ 加 contract test(仿 test_bff_management_cockpit.py)。main.py 約47k行,優先用新模組(console_gap/<feature>.py 暴露 APIRouter)+單行 include_router,降低衝突。 供 FE WorkflowTemplates / HookCronManager。

## Review / Closeout
- Reviewer artifact: `.orchestrator/task-reviews/bffgap_workflows_hooks_claude2_review.md`
- Reviewer verdict: APPROVED; no required changes.
- Delivery PR: https://github.com/ajoe734/pantheon/pull/1631
- Delivery merge commit: `a997ce4e43ea4c695046cce584e526565ab56f95`
- Current closeout base: `3f86b985c55ab8b2aba8806558def6592da6bdc1` (`origin/dev`)

Owner closeout verification on 2026-06-15:
- `PYTHONDONTWRITEBYTECODE=1 pytest -q services/control-plane/bff/tests/test_bff_workflows_hooks.py services/control-plane/bff/tests/test_bff_management_cockpit.py` => 7 passed in 8.39s.
- `python3 -m json.tool services/control-plane/bff/contract_snapshots/backend_routes_manifest.json` => valid JSON.
- `git diff --check -- .orchestrator/task-briefs/bffgap_workflows_hooks.md services/control-plane/bff/BFF_API_CONTRACT.md services/control-plane/bff/console_gap/workflows_hooks.py services/control-plane/bff/main.py services/control-plane/bff/read_store.py services/control-plane/bff/tests/test_bff_workflows_hooks.py services/control-plane/bff/contract_snapshots/backend_routes_manifest.json scripts/bff_route_manifest_backend.py` => passed.
- `python3 scripts/bff_route_manifest_backend.py --check` => currently fails only on `GET /bff/management/data-sources`, a post-merge `BFFGAP-DATASOURCES` route snapshot drift outside this task's workflows/hooks scope. PR #1631 reviewer verification had this check green for the workflows/hooks delivery.

Scope confirmed at closeout: `/bff/workflows` and `/bff/hooks` remain read-only, auth-guarded, present in OpenAPI, and return canonical list envelopes with explicit `missing` degradation instead of bare arrays.
