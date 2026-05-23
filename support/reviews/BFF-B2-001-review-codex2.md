# Review: BFF-B2-001 — Strategy / Persona / Capital / Deployment list-detail facade (B2.1 14 endpoints)

Reviewer: Codex2
Date: 2026-05-23
PR: #425 (merged 2026-05-23T07:16:17Z, branch task/BFF-B2-001)
Owner: Claude2

## Summary

BFF-B2-001 delivers the 14 list and detail read endpoints for the four core
resource families (Strategy, Persona, Capital Pool, Deployment/Rebalance) as
specified in §B2.1 of `BFF_API_GAP_final_integration_spec.md`. The catch-all
`sem_final_id_named_read_alias` decorator shadowing was removed, making
FastAPI's router unambiguous. All endpoints return canonical BFF envelopes
(`data`, `page_info`, `meta`). No mock fallback paths remain.

## Verification

```
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_bff_b2_list_detail_facade.py -q
39 passed in 11.28s

PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/control-plane/bff/main.py
OK (no output)
```

## Acceptance Criteria Check

| # | Criterion | Result |
|---|---|---|
| 1 | GET /bff/strategies returns data list + page_info | PASS |
| 2 | GET /bff/strategies/{id} existing → data with id, name, state, risk | PASS |
| 3 | GET /bff/strategies/{id} unknown → HTTP 404 OBJECT_NOT_FOUND | PASS |
| 4 | GET /bff/strategies/{id}/specs returns data list | PASS |
| 5 | GET /bff/personas returns data list + page_info | PASS |
| 6 | GET /bff/personas/{id} existing → data with id, name, state, archetype | PASS |
| 7 | GET /bff/personas/{id} unknown → HTTP 404 | PASS |
| 8 | GET /bff/personas/{id}/route-policy returns data with personaId | PASS |
| 9 | GET /bff/personas/{id}/evaluations returns data list | PASS |
| 10 | GET /bff/personas/{id}/memory returns data with personaId | PASS |
| 11 | GET /bff/capital-pools returns data list + page_info | PASS |
| 12 | GET /bff/capital-pools/{id} existing → data | PASS |
| 13 | GET /bff/capital-pools/{id} unknown → HTTP 404 | PASS |
| 14 | GET /bff/deployments returns data list + page_info | PASS |
| 15 | GET /bff/deployments/{id} existing → data with approval_decision + review | PASS |
| 16 | GET /bff/deployments/{id} unknown → HTTP 404 | PASS |
| 17 | GET /bff/rebalances returns data list + page_info | PASS |
| 18 | GET /bff/rebalances/{id} existing → data | PASS |
| 19 | GET /bff/rebalances/{id} unknown → HTTP 404 | PASS |
| 20 | All 14 endpoints return HTTP 401 without Authorization header | PASS |
| 21 | Catch-all decorators removed from sem_final_id_named_read_alias | PASS |
| 22 | pytest test_bff_b2_list_detail_facade.py passes all cases | PASS (39/39) |

## Scope Boundary

- execute-plans/src/lib/bff-v1/paths.ts: verified unchanged; all 14 paths already declared.
- No write endpoints, live capital paths, or approval-gate surfaces modified.
- No mock/seed fallback code introduced.
- Catch-all removal is narrowing only; no new route logic added.

## Decision

**Approved (re-approval).** PR #425 is merged to dev. B2.1 focused suite passes
39/39. Existing review evidence remains valid. Owner (Claude2) may finalize done.
