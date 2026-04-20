# CW-01 Consult Request — Reactivation Handoff

**Task:** LUV-REACTIVATE-CW01-001  
**Date:** 2026-04-20  
**Author:** Claude  
**Reviewer:** Codex2

---

## 1. Bundle Verification

All three handoff artifacts are present and structurally intact:

| Artifact | Path | Status |
|---|---|---|
| contract-ready | `.coordination/responses/CW-01-consult-request-contract-ready.yaml` | ✓ exists |
| lovable-ui-task | `.coordination/responses/CW-01-consult-request-lovable-ui-task.yaml` | ✓ exists |
| lovable-prompt | `.coordination/responses/CW-01-consult-request-lovable-prompt.md` | ✓ exists |

All referenced documentation files also exist:

| Doc | Path | Status |
|---|---|---|
| BFF contract | `docs/bff/CW-01-consult-request.md` | ✓ exists |
| Screen spec | `docs/screens/CW-01-consult-request.md` | ✓ exists |
| Example payload | `docs/examples/CW-01-consult-request.json` | ✓ exists |
| Frontend change spec | `docs/pantheon-handoffs/CW-01-consult-request/FRONTEND_CHANGE_SPEC.md` | ✓ exists |
| BFF gap template | `.coordination/requests/CW-01-consult-request-bff-gap.example.yaml` | ✓ exists |
| UI-done template | `.coordination/requests/CW-01-consult-request-ui-done.example.yaml` | ✓ exists |

---

## 2. Architecture Truth Check

The contract-ready bundle's endpoint list matches what is currently absent from the BFF implementation:

- `POST /api/v1/consult/requests`
- `GET /api/v1/consult/requests`
- `GET /api/v1/consult/requests/{request_id}`
- `POST /api/v1/consult/requests/{request_id}/cancel`

Cross-checked against `services/control-plane/bff/main.py`: none of the four routes have a registered handler. The BFF overview function (`_build_consultation_workbench_overview`, line 3530) explicitly lists all four as `missing_contracts` with module status `not_ready`.

The contract fields (`request_id`, `status`, `created_at`, `linked_session_id`, `request_to_session_status`, `allowedActions.canCancel`) remain architecturally correct and are not in conflict with any current L1 policy files.

**Verdict: bundle is accurate and contract truth is stable.**

---

## 3. Current Blocker

```
bff_route_live: false
```

The four CW-01 BFF routes are not implemented. The lovable-ui-task and lovable-prompt both carry this gate explicitly:

> "do not start production UI until Pantheon confirms the CW-01 request routes are live"

The Lovable front-end lane **cannot proceed** until these routes are live.

---

## 4. Next-Step Note for Lovable

**BLOCKED — do not start CW-01 UI implementation.**

Lovable must wait for a refreshed `CW-01-consult-request-contract-ready.yaml` with `bff_route_live: true` before touching production UI. The handoff bundle path and template contracts are already correct.

---

## 5. Next-Step Note for Pantheon / BFF Owner

Implement the four routes in `services/control-plane/bff/main.py`:

1. `POST /api/v1/consult/requests` — create, return `request_id`, `status="created"`, `request_to_session_status="pending_session"`, `linked_session_id=null`, `allowedActions.canCancel=true`
2. `GET /api/v1/consult/requests` — list with filters: `status`, `target_type`, `consultation_type`, `page_token`, `page_size`
3. `GET /api/v1/consult/requests/{request_id}` — detail with full lifecycle fields
4. `POST /api/v1/consult/requests/{request_id}/cancel` — cancel with `allowedActions.canCancel` guard

After routes are live and verified, update `contract-ready.yaml` → `bff_route_live: true`, then Lovable can proceed.

---

## 6. Disposition

| Criterion | Result |
|---|---|
| contract-ready bundle matches architecture truth | ✓ PASS |
| next-step note refreshed or precise blocker recorded | ✓ BLOCKED — BFF routes not implemented |
| reviewable reactivation handoff created | ✓ this file |

**Status: ready for Codex2 review.**
