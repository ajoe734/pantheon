# Review: BFF-CONSOL-018 Detail Journey Smoke C

**Reviewer:** Claude
**Task:** BFF-CONSOL-018 — Detail journey smoke C (incident / approval / rebalance / job / audit)
**Commit reviewed:** 1ffa8323
**Date:** 2026-05-13
**Outcome:** APPROVED

---

## Files Reviewed

1. `execute-plans/tests/e2e/detail-smoke-c.spec.ts` — Playwright API smoke spec
2. `services/control-plane/bff/read_store.py` — `_project_canonical_approval_decision` fix
3. `services/control-plane/bff/test_bff_consol_010_fixture_pack_c.py` — approval projection assertions added
4. `support/evidence/BFF-CONSOL-018-detail-smoke-c.json` — per-family transcripts

---

## Verification

```
python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/read_store.py → PASS
python3 -m pytest test_bff_consol_010_fixture_pack_c.py test_bff_consol_008_fixture_pack_a.py -q → 10 passed
```

All target routes confirmed registered in main.py:
- `/api/v1/operator/incident-response/{incident_id}` — line 12655
- `/bff/approvals/{id}` — line 23294 (via sem_final_id_named_read_alias)
- `/bff/rebalances/{rebalance_id}` — line 16493
- `/bff/jobs/{job_id}` — line 19295
- `/bff/audit/entities/{entity_type}/{entity_id}` — line 21204

---

## Acceptance Criteria Assessment

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | 5 family detail routes return 2xx or typed 404 | **PASS** | `expect2xxOrTyped404` helper rejects raw 5xx and untyped 404; all 5 routes registered and tested via FastAPI TestClient |
| 2 | job detail not returning undefined | **PASS** | Spec asserts `String(job.name).toLowerCase() !== "undefined"`; phantom job confirmed to return typed `OBJECT_NOT_FOUND` 404 (not "undefined") |
| 3 | audit detail drawer disabled, list-only explanation | **PASS** | `AUDIT_ROW_DETAIL_POLICY.detailDrawerDisabled=true` defined and asserted; `disabledRowActionLabel` and `reason` verified; entity trail remains accessible |
| 4 | incident detail exposes runtime context and rollback slot | **PASS** | Spec checks `runtime_id\|runtimeId\|binding_id` and `allowedActions.canHardRollback` on the composed incident-response route |
| 5 | approval detail shows deployment link | **PASS** | `_project_canonical_approval_decision` now preserves `target_type`, `target_id`, and builds `deployment_ref`; TestClient assertion in pack C test confirms `target_type=DeploymentPlan`, `target_id=plan-pack-c-paper-001` |
| 6 | evidence JSON includes all family transcripts | **PASS** | `transcripts` section covers all 5 families with `routes_verified` arrays |

---

## Code Quality Notes

- `_project_canonical_approval_decision` fix is narrowly scoped — only adds fields needed for deployment link assertion; no regressions to existing approval fields.
- The phantom-ID test case (5th test in spec) cleanly validates all 5 families' degraded paths in one pass.
- Pack C fixture test additions explicitly assert the approval projection shape rather than relying on evidence prose.
- No undefined-leaking fallbacks remain in the job detail path per TestClient probe.

---

## Decision

All 6 acceptance criteria satisfied. Implementation is clean and verified. **Approved — returning to Codex for finalization.**
