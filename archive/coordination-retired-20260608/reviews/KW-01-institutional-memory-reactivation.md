# KW-01 Institutional Memory — Reactivation Handoff

**Task:** LUV-REACTIVATE-KW01-001  
**Date:** 2026-04-20  
**Author:** Codex  
**Reviewer:** Claude

---

## 1. Bundle Verification

All three handoff artifacts are present and refreshed:

| Artifact | Path | Status |
|---|---|---|
| contract-ready | `.coordination/responses/KW-01-institutional-memory-contract-ready.yaml` | ✓ exists; refreshed for live routes |
| lovable-ui-task | `.coordination/responses/KW-01-institutional-memory-lovable-ui-task.yaml` | ✓ exists; acceptance no longer says to stay blocked |
| lovable-prompt | `.coordination/responses/KW-01-institutional-memory-lovable-prompt.md` | ✓ exists; prompt now tells Lovable to proceed against live BFF |

All referenced documentation files also exist:

| Doc | Path | Status |
|---|---|---|
| BFF contract | `docs/bff/KW-01-institutional-memory.md` | ✓ exists |
| Screen spec | `docs/screens/KW-01-institutional-memory.md` | ✓ exists; readiness text refreshed |
| Example payload | `docs/examples/KW-01-institutional-memory.json` | ✓ exists |
| Frontend change spec | `docs/pantheon-handoffs/KW-01-institutional-memory/FRONTEND_CHANGE_SPEC.md` | ✓ exists; readiness text refreshed |
| BFF gap template | `.coordination/requests/KW-01-institutional-memory-bff-gap.example.yaml` | ✓ exists |
| UI-done template | `.coordination/requests/KW-01-institutional-memory-ui-done.example.yaml` | ✓ exists |

---

## 2. Architecture Truth Check

The current Pantheon BFF implementation now registers both KW-01 browse routes in `services/control-plane/bff/main.py`:

- `GET /api/v1/knowledge/memory`
- `GET /api/v1/knowledge/memory/{entry_id}`

Direct local verification with FastAPI `TestClient` returned `200 OK` for both routes and confirmed the live payload still matches the published contract shape:

- list payload keys: `entries`, `pagination`, `meta`
- list row keys include `entry_id`, `knowledge_type`, `headline`, `scope`, `scope_filter`, `written_at`, `write_authority`, `tags`, `reuse_count`, `is_superseded`, `route_href`
- detail payload keys include `content`, `source_event`, `contributing_persona_ids`, `scope`, `lifecycle`, `usage`, `meta`
- `meta.surfaces` currently returns `memory_list=ok`, `entry_detail=ok`, `source_context=ok`

The Knowledge Workbench overview builder also now marks `KW-01` as `ready` with no missing contracts and an explicit next gate saying Lovable may proceed with production UI.

**Verdict: the old blocked reactivation note was stale; the bundle is now accurate after refresh and the architecture truth is stable.**

---

## 3. Reactivation Outcome

```text
bff_route_list_live: true
bff_route_detail_live: true
```

KW-01 is no longer blocked on Pantheon BFF route availability. The frontend lane should resume implementation against the published contract and live routes.

---

## 4. Next-Step Note for Lovable

**READY — resume KW-01 UI implementation.**

Lovable should build the Institutional Memory list/detail flow against the live BFF routes using the existing handoff bundle. If the runtime payload is missing a required field or diverges from the synced contract, stop immediately and emit `.coordination/requests/KW-01-institutional-memory-bff-gap.yaml` instead of mocking.

---

## 5. Next-Step Note for Pantheon / Reviewer

Review the refreshed handoff bundle and verify the new contract test:

- `services/control-plane/bff/test_kw01_institutional_memory_contract.py`

If approved, the task can move to `review_approved`; no further Pantheon BFF unblock step remains for KW-01 reactivation.

---

## 6. Disposition

| Criterion | Result |
|---|---|
| contract-ready bundle matches architecture truth | ✓ PASS |
| next-step note refreshed or precise blocker recorded | ✓ READY — BFF routes are live |
| reviewable reactivation handoff created | ✓ this file |

**Status: ready for Claude review.**
