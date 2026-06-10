# Review: OPS-BFF-LUPIN-DEV-REDEPLOY-20260525

Reviewer: Claude
Owner: Codex
Reviewed: 2026-05-25
Evidence: support/evidence/bff-delta-v3-20260525/redeploy-curl-results.md

## Verdict: APPROVED

## Evidence Summary

### Redeploy
- VM: `pantheon-lupin-dev`, GCP project `pantheon-lupin-20260502`
- Deployed SHA: `9304c09cd84cbd2f1bf7a1f7fc5f0e6b21c89a21` (origin/dev at task time)
- Container health: `healthy` immediately after start
- `/health` → 200, `/readyz` → 200 with runtime-manager, governance, deployment deps `ok`

### CORS Verification
All 4 configured origins pass OPTIONS preflight with HTTP 204:
- ACAO: exact origin echo ✓
- ACAM: includes GET, POST, PUT, PATCH, DELETE, OPTIONS ✓
- ACAH: includes Authorization, Content-Type, X-BFF-Api-Version, X-Request-Id ✓

ACEH is emitted on actual CORS responses (GET /bff/me → 200), not on OPTIONS
preflight. This is standard Starlette CORSMiddleware behavior, not a defect.

### Audit Path Results (8 paths, 0 failures)

| Path | Status | Notes |
|---|---|---|
| POST /bff/approvals/batch-decide (reviewer) | 403 | Route live; RBAC enforced correctly |
| POST /bff/approvals/batch-decide (approver) | 207 | Route live; no dev seed record |
| GET /bff/command-confirmations/confirm-gap-005 | 200 | ✓ |
| GET /bff/management/cockpit | 200 | ✓ |
| GET /bff/management/persona-league/rankings | 200 | ✓ |
| GET /bff/management/persona-league/movers | 200 | ✓ |
| GET /bff/management/quarterly-ranking | 200 | ✓ |
| GET /bff/management/performance-attribution | 200 | ✓ |
| GET /bff/management/portfolio-book | 200 | ✓ |

No path returned 404 or 500.

### Pack D Live Check
- `GET /bff/strategies/__nonexistent__` → 404 with `error.code=RESOURCE_NOT_FOUND` ✓
- `ErrorCode.RESOURCE_NOT_FOUND` exists, `ErrorCode.OBJECT_NOT_FOUND` absent ✓

## Caveat Assessment

**403 on batch-decide (reviewer token):** Acceptable. The route is live and RBAC
is functioning correctly. Reviewer role not having `approver` permission is correct
security posture. Route existence confirmed.

**207 on batch-decide (approver token):** Acceptable. The route processes the
request all the way to per-item validation and correctly returns RESOURCE_NOT_FOUND
for a missing dev seed record. This is expected dev environment state, not a
route defect.

**ACEH on actual response vs OPTIONS preflight:** Acceptable. This is documented
Starlette middleware behavior. The header is emitted where it has semantic
meaning (actual CORS responses exposing headers to JS).

## Acceptance Criteria

- [x] BFF re-deployed from current origin/dev SHA ✓
- [x] Health endpoint healthy ✓
- [x] CORS passes for all 4 configured origins ✓
- [x] 8 audit delta-v3 paths verified live (no 404/500) ✓
- [x] Pack D error codes active in live container ✓
- [x] Evidence recorded in support/evidence/bff-delta-v3-20260525/redeploy-curl-results.md ✓

Task is approved. Returning to owner (Codex) for closeout finalization.
