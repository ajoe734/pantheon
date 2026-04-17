# PKT-005 Global Degradation Banner — Pantheon Review

**Reviewer:** Claude  
**Task:** LUV-REVIEW-011  
**Reviewed commit:** `7406990a8311ef6865491fcdb883b677a98ff6c9` (front-ai-trading-system)  
**Review date:** 2026-04-17  

## Disposition: APPROVED — loop can close

The PKT-005 Global Degradation Banner implementation is contract-correct, fully replayable from the published source commit, and satisfies all acceptance criteria without any API gap.

---

## Replayability Check

Source commit `7406990a8311ef6865491fcdb883b677a98ff6c9` (BP5-LUV-009) contains:

- `src/components/GlobalDegradationBanner.tsx` — shared banner primitive
- `src/components/GlobalDegradationBanner.test.tsx` — six test cases
- `src/lib/degradationBanner.ts` — pure decision helper
- `src/pages/operator/DeploymentReviewConsole.tsx` — PKT-001 integration
- `src/pages/operator/DeploymentPlanDetail.tsx` — PKT-001 detail panel integration
- `src/pages/operator/IncidentHome.tsx` — PKT-002 split-read integration
- `src/pages/operator/IncidentDetail.tsx` — PKT-002 detail integration
- `src/pages/operator/PostIncidentReviewConsole.tsx` — PKT-003 integration
- `.coordination/requests/PKT-005-degradation-banner-ui-done.yaml`
- `.coordination/requests/PKT-005-degradation-banner-frontend-feedback.yaml`
- Full feedback bundle (LOVABLE_CHANGE_FEEDBACK.md, API_GAP_REQUESTS.json, UI_DECISIONS.md, QA_STATUS.md)

**Verdict: fully replayable. All 14 claimed files are present in the commit.**

---

## Acceptance Criteria

| Criterion | Result |
|---|---|
| Shared GlobalDegradationBanner component driven by meta.staleness and meta.surfaces | PASS |
| No separate BFF health-check fetch | PASS |
| Banner state derived from current screen's composed view response only | PASS |
| All five banner variants implemented (none, degraded, stale, partial, critical) | PASS |
| Banner disappears automatically when all meta.surfaces return to ok | PASS |
| Wired into all three Operator Console screens (PKT-001, PKT-002, PKT-003) | PASS |
| Missing meta.surfaces keys trigger bff-gap alert (not silent mock) | PASS |

---

## Technical Verification

### Decision Tree Compliance

`deriveDegradationBannerState` in `src/lib/degradationBanner.ts` implements the canonical decision tree exactly:

```
if requestFailed or all surfaces unavailable → critical
elif any surface unavailable → partial
elif meta.staleness.served_from ∈ ["cache","reconstructed"] and any surface degraded → stale
elif any surface degraded → degraded
else → none
```

### Constraint Compliance

- No raw `fetch` or `axios` calls in component files — all BFF access via shared `operatorApi` client
- No dedicated `/api/v1/system/health` endpoint added
- No demo providers imported
- Missing surface keys surfaced via `findMissingSurfaceFields` with inline BFF-gap alerts

### Split-Read Merge (PKT-002 Incident Home)

`mergeBannerMeta` correctly:
- Pre-seeds `incident_list` and `kill_switch` surface keys as `"unavailable"` before either response arrives
- Preserves oldest `meta.staleness.last_known_at` across independent responses
- Isolates per-response surface key updates

### QA Status

- `npm run build` passed
- Targeted ESLint passed on all 6 touched files
- `npx tsx src/components/GlobalDegradationBanner.test.tsx` — all 6 test cases pass
- Decision tree cross-checked against screen spec and all 6 example payloads

### API Gap

`API_GAP_REQUESTS.json` status: `no_open_gaps`. No new BFF endpoint required. `meta.surfaces` and `meta.staleness` fields confirmed present in the PKT-005 contract-ready packet.

---

## Non-blocking Notes

- **STALE variant edge case** (non-blocking, documented): A response where all surfaces are `ok` but `served_from = "cache"` falls through to `none` — correct per decision tree; no degraded surface means no staleness signal to show.
- **Live runtime validation** (deferred, acceptable): Live browser QA against a deployed Pantheon BFF, live RBAC verification, and live SSE-triggered refresh verification are not completed in this cycle. These require a deployed BFF and are outside the current static review scope. No blocking action required before loop close.

---

## Follow-up Actions

None. No Pantheon API gap, no frontend rework required, no follow-up tasks to create.

The remaining Pantheon-owned step (publishing a normalized PKT-005 contract lock from a real Pantheon commit) was already noted in the feedback bundle as deferred/runtime-only work and does not block loop closure.

---

## Summary

PKT-005-degradation-banner is implemented correctly, the delivery is git-replayable, all acceptance criteria pass, and no API gaps exist. **Loop can close.**
