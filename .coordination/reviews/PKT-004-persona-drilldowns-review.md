# Review: PKT-004-persona-drilldowns Lovable Closeout

**Reviewer:** Codex
**Task:** LUV-REVIEW-009
**Date:** 2026-04-17
**Source commit reviewed (round 1):** `2398427980ea98a66c5fe23547f021d8efa3a53f` — non-replay-clean
**Source commit reviewed (round 2):** `6c6b4e884c8eb6537b6bf46b59971aaee852ed7d` — replay-clean

---

## Disposition: READY FOR REVIEW — transport gap resolved

The feedback bundle is complete and the persona drilldown implementation is contract-aligned. The previously blocking replay-clean issue has been resolved. PKT-004-persona-drilldowns is ready for formal Codex review.

---

## Round 1 Finding (resolved)

### Published `source_commit` 2398427 did not build

`src/App.tsx` at `2398427` imported `BindingList.tsx`, `CapitalPoolDetail.tsx`, and `CapitalPoolList.tsx` which were untracked in the front repo. Other previously-missing files (`ApprovalDecisionDetail`, `ApprovalDecisionList`, `BindingDetail`, `DeploymentPlanDetail`, `DeploymentPlanList`, `PersonaManagement`) were subsequently published in later front-repo commits.

---

## Round 2 Fix

1. Committed the 3 remaining untracked files at front-ai-trading-system `6c6b4e8`:
   - `src/pages/persona/BindingList.tsx`
   - `src/pages/persona/CapitalPoolDetail.tsx`
   - `src/pages/persona/CapitalPoolList.tsx`
2. Updated `PKT-004-persona-drilldowns-ui-done.yaml` source_commit → `6c6b4e884c8eb6537b6bf46b59971aaee852ed7d` (front commit `205da13`)
3. Updated `PKT-004-capital-binding-drilldowns-ui-done.yaml` source_commit → same (those files also belong to capital-binding)
4. Mirrored updated ui-done payload into Pantheon `.coordination/requests/`

---

## Implementation Verification (at 6c6b4e8)

- All six persona drilldown surfaces (PS-01 to PS-06) implemented
- `personaDrilldownApi` routes all six read endpoints through the shared BFF client — no raw `fetch()` or demo-provider calls
- Authorization Bearer token propagation present in `src/lib/bffClient.ts`
- `detail.error.*` Pantheon error envelope parsing present
- Catalog `lifecycle_state` and session-list `status` forwarded as query params, not filtered client-side
- Each surface implements: loading, empty, error, permission-required, contract-mismatch, and staleness states
- `meta.staleness` renders as non-dismissable notice without hiding content
- `API_GAP_REQUESTS.json`: no open gaps — `status: no_open_gaps`
- ESLint: PASS on all touched PKT-004 files
- Build: PASS at `6c6b4e8` (all App.tsx route imports satisfied)
- Narrow non-PKT fix: duplicate `rollbackArtifactId` in `IncidentActionDrawer.tsx` removed to unblock build

## Non-blocking Notes

- Live BFF authorization QA (viewer vs operator/approver/admin/reviewer roles) not completed in this cycle — runtime-only risk
- Vite 500 kB chunk warning present but build succeeds
- Wave 2 standalone Persona Workbench IA shell deferred per PKT-004 bff_caveats

## Follow-up Tracking

| Item | Status |
|---|---|
| PKT-004-capital-binding-drilldowns closeout | Separate LUV-REVIEW task needed; ui-done source_commit corrected here |
| Live BFF role-gated QA | Non-blocking; deferred to runtime integration testing |
| Persona Workbench IA shell | Wave 2 deferred |
