# EXEC-FRONT-KW01-001 Re-review

Review date: 2026-04-20
Reviewer: Codex
Status: follow-up required

## Findings

1. The Pantheon-owned `source_event.href` still points at an unmounted owner-screen route, so the reviewed UI cannot satisfy the KW-01 "use the BFF href exactly as provided" rule end-to-end.

- [services/control-plane/bff/test_kw01_institutional_memory_contract.py](/home/lupin/code/pantheon/services/control-plane/bff/test_kw01_institutional_memory_contract.py:214) still locks the KW-01 detail fixture to `"/operator/incidents/inc-2026-04-05-001/review"`.
- [App.tsx](/home/lupin/code/front-ai-trading-system/src/App.tsx:141) mounts `/operator/incidents/:incidentId`, [App.tsx](/home/lupin/code/front-ai-trading-system/src/App.tsx:143) mounts `/operator/incidents/:incidentId/action`, and [App.tsx](/home/lupin/code/front-ai-trading-system/src/App.tsx:144) mounts `/operator/post-incident-review`, but there is no `/operator/incidents/:incidentId/review` alias in the reviewed front route table.
- A local authenticated FastAPI TestClient probe of [main.py](/home/lupin/code/pantheon/services/control-plane/bff/main.py:6670) and [main.py](/home/lupin/code/pantheon/services/control-plane/bff/main.py:6727) returned:
  - degraded list: `route_href = /knowledge/memory/mem-...`
  - degraded detail: `source_event.href = /operator/incidents/inc-2026-04-05-001/review`
  - unavailable list: `memory_list = unavailable`, zero entries
  - unavailable detail: `entry_detail = unavailable`, `source_context = unavailable`, but the same `source_event.href`
- Result: the front implementation is contract-aligned, but Pantheon still needs a runtime or BFF follow-up to make `source_event.href` resolve to the intended owner screen in the current workspace and deployed environment.

## Verification

- `npx eslint src/App.tsx src/components/AppSidebar.tsx src/components/WorkbenchBreadcrumb.tsx src/lib/bffClient.ts src/pages/knowledge/InstitutionalMemoryTypes.ts src/pages/knowledge/InstitutionalMemoryList.tsx src/pages/knowledge/InstitutionalMemoryDetail.tsx` passed in `../front-ai-trading-system`.
- `npm run build` passed in `../front-ai-trading-system`.
- The current `ui-done` handoff now points at replayable implementation commit `ba560610044d5f11c97b2b48cfb5b7621d812e4e`, and the required feedback bundle exists at front head `2820e449dc95ab4677d9a7dc61d6eb7da4363aa4`.
