# BFF-B2-003 Owner Closeout Evidence

Task-ID: BFF-B2-003
Owner: Codex
Reviewer: Codex2
Phase: Sprint BFF-2 / EPIC-BFF-GAP-CORE
Closed: pending final owner `done` command after merged closeout PR

Note: implementation and initial closeout evidence were produced before the
orchestrator reassigned BFF-B2-003 from Claude to Codex for owner
finalization. Codex owns this final closeout and the `done` transition.

## Scope

Capabilities facade: 8 dedicated GET handlers for mcp-servers, mcp-tools,
channels, and ranking-formulas per spec §B2.3.

## Acceptance Criteria Verification

| # | Criterion | Status |
|---|---|---|
| 1 | GET /bff/mcp-servers returns data + items + page_info + meta | ✅ |
| 2 | GET /bff/mcp-servers/{id} known returns data with server record | ✅ |
| 3 | GET /bff/mcp-servers/{id} unknown returns HTTP 404 | ✅ |
| 4 | GET /bff/mcp-tools returns data + items + page_info + meta | ✅ |
| 5 | GET /bff/mcp-tools/{id} unknown returns HTTP 404 | ✅ |
| 6 | GET /bff/channels returns all SSE_CHANNEL_CATALOG entries | ✅ |
| 7 | GET /bff/channels/{id} known returns data with channel record | ✅ |
| 8 | GET /bff/channels/{id} unknown returns HTTP 404 | ✅ |
| 9 | GET /bff/ranking-formulas returns data + items + page_info + meta | ✅ |
| 10 | GET /bff/ranking-formulas/{id} known returns data with formula record | ✅ |
| 11 | GET /bff/ranking-formulas/{id} unknown returns HTTP 404 | ✅ |
| 12 | All 8 endpoints return HTTP 401 without Authorization header | ✅ |
| 13 | Dead catch-all decorators removed | ✅ |
| 14 | GET /bff/tools and GET /bff/skills still served by dedicated handlers | ✅ |
| 15 | pytest test_bff_b2_003_capabilities.py passes 23 tests | ✅ |

## Verification Commands

```
pytest services/control-plane/bff/tests/test_bff_b2_003_capabilities.py -q
# 23 passed, 3 warnings
```

Final owner verification was re-run after each `origin/dev` refresh:

```
pytest services/control-plane/bff/tests/test_bff_b2_003_capabilities.py -q
# 23 passed, 3 warnings
```

## Reviewer Approval

Review notes (Codex2):
"審查通過：8 個 B2.3 dedicated GET handlers route map 正確，catch-all dead decorators
已移除；focused pytest 23 passed，B2.1/B2.2/B2.3 suite 93 passed。"

## Delivery

- Task branch: task/BFF-B2-003
- Implementation commit: 1bf5da19 (BFF-B2-003: capabilities facade for mcp-servers, mcp-tools, channels)
- Implementation PR: #469 (merged to dev)
- Closeout evidence PR: #480 (merged to dev at de1a1701)
- Final done-gate evidence: this trailer-bearing Codex commit is the task branch HEAD for `scripts/ai-status.sh done`
- Target: dev
