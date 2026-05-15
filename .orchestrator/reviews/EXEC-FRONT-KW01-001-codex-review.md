# Review: EXEC-FRONT-KW01-001 - KW-01 Institutional Memory Frontend Flow

Reviewer: Codex
Date: 2026-04-20
Status: approved

## Findings

No blocking findings remain.

## Verified

- `.coordination/responses/KW-01-institutional-memory-contract-ready.yaml`, `.coordination/responses/KW-01-institutional-memory-lovable-ui-task.yaml`, and `docs/pantheon-handoffs/KW-01-institutional-memory/FRONTEND_CHANGE_SPEC.md` align on the published KW-01 list/detail contract, read-only constraints, and degradation behavior.
- `../front-ai-trading-system/.coordination/requests/KW-01-institutional-memory-ui-done.yaml` points at UI commit `ba560610044d5f11c97b2b48cfb5b7621d812e4e`, and the required feedback bundle exists under `../front-ai-trading-system/docs/pantheon-feedback/KW-01-institutional-memory/`.
- `../front-ai-trading-system/src/lib/bffClient.ts` exposes only the published BFF wrappers for `GET /api/v1/knowledge/memory` and `GET /api/v1/knowledge/memory/{entry_id}`.
- `../front-ai-trading-system/src/pages/knowledge/InstitutionalMemoryList.tsx` passes the Pantheon-owned filter vocabulary through query params, uses `route_href` for navigation, preserves superseded rows, and handles degraded/unavailable/error/empty states without client-side ranking or mutation affordances.
- `../front-ai-trading-system/src/pages/knowledge/InstitutionalMemoryDetail.tsx` renders lifecycle status, replacement-entry linking, structured payloads, source links via `source_event.href`, contributing persona fallback copy, and per-surface degraded/unavailable states in line with the published contract.
- `../front-ai-trading-system/src/App.tsx`, `../front-ai-trading-system/src/components/AppSidebar.tsx`, and `../front-ai-trading-system/src/components/WorkbenchBreadcrumb.tsx` wire the Knowledge Workbench route, navigation entry, and breadcrumb labels for the new screens.
- `npx eslint src/App.tsx src/components/AppSidebar.tsx src/components/WorkbenchBreadcrumb.tsx src/lib/bffClient.ts src/pages/knowledge/InstitutionalMemoryTypes.ts src/pages/knowledge/InstitutionalMemoryList.tsx src/pages/knowledge/InstitutionalMemoryDetail.tsx` passed in `../front-ai-trading-system`.
- `npm run build` passed in `../front-ai-trading-system`.
- `pytest services/control-plane/bff/test_kw01_institutional_memory_contract.py` passed in `pantheon`, confirming the reviewed BFF routes still satisfy the published KW-01 contract.

## Residual Notes

- No live `VITE_BFF_BASE_URL` or `VITE_PANTHEON_BFF_BASE_URL` was configured in this workspace, so reviewer validation remained static plus local contract-test based. Deployed-environment verification of `route_href` and `source_event.href` targets remains a runtime-only follow-up.
- The handoff summary references a feedback-bundle commit `2820e4439a7f7e2c1f83b99d4af5904eb36551dc` that is not present in the current local `pantheon` or `front-ai-trading-system` histories. Approval is based on the published feedback artifacts and the verified UI commit instead of that missing hash.
