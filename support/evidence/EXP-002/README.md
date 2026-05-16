# EXP-002: /bff/research-experiments list/detail — Evidence

Task-ID: EXP-002
Owner: Claude2
Reviewer: Codex
Implementation commit: 7960c3c5
Review artifact: support/reviews/EXP-002-review-codex.md

## Delivered endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/bff/research-experiments` | Paginated list envelope of experiment records |
| GET | `/bff/research-experiments/{id}` | Detail view with status, analysis_links, canCancel |

## Contract test

`services/control-plane/bff/test_exp002_bff_research_experiments_contract.py` — 17 tests

Coverage:
- List: authenticated envelope shape, surface fields, seeded completed/running/failed records, field invariants, auth rejection
- Detail: authenticated envelope shape, surface fields, completed/running/failed status coverage, `analysis_links` populated, `canCancel=false` on terminal experiments, 404 for unknown id, auth rejection
- Round-trip: POST `/api/v1/experiments/launch` → BFF list/detail readback with `VITE_BFF_FALLBACK=strict`

## Reviewer approval

Codex approved 2026-05-16. No blocking findings for EXP-002 scope.
Note: `/bff/capital-pools/pool_001` returning 503 is outside the research-experiments route family and is non-blocking for this task.

## Closeout verification

```
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_exp002_bff_research_experiments_contract.py -q
17 passed, 1 warning in 33.02s
```
