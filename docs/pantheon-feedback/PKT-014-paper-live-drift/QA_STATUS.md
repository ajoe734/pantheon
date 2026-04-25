# PKT-014 Operator Paper / Live Drift — QA Status

## Status

Pantheon review complete. Outcome: `followup-required`.

## Checks Completed

- Sibling front TypeScript check passed for the reviewed PKT-014 slice:
  - `./node_modules/.bin/tsc --noEmit --pretty false`
- Sibling front ESLint passed for the reviewed PKT-014 files:
  - `src/App.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/operator/OperatorPaperLiveDrift.tsx`
  - `src/pages/operator/types.ts`
- Sibling front production build passed:
  - `npm run build`
  - Result: passed with the existing non-blocking Vite chunk-size warning
- Targeted Pantheon PKT-014 contract tests passed:
  - `python3 -m pytest services/control-plane/bff/test_pkt014_paper_live_drift_contract.py -q`
  - Result: `2 passed`
- Direct local FastAPI `TestClient` read of
  `GET /api/v1/operator/paper-live-drift/runtime-042` returned `200 OK` in the
  current Pantheon workspace with:
  - `meta.surfaces.paper_live_drift.status = ok`
  - `meta.surfaces.drift_report.status = ok`
  - `threshold_evaluation.overall_status = breached`
  - API-resource href values in `plan_ref`, `evidence_refs[]`, and
    `recommended_actions[].target_ref`
- Local BFF route probing with controlled store fixtures verified the required
  degraded and unavailable branches through the current app:
  - degraded case -> `200 OK`, `paper_live_drift = degraded`,
    `drift_report = degraded`, baseline and observed snapshots preserved
  - unavailable case -> `200 OK`, `paper_live_drift = unavailable`,
    `drift_report = unavailable`, `paper_baseline = null`,
    `observed_state = null`
- Front publication tuple review completed:
  - required `frontend-feedback` request is absent
  - `ui-done` still advertises `source_commit: HEAD`
  - front `HEAD` does not contain `OperatorPaperLiveDrift.tsx`, the PKT-014
    request pair, or the PKT-014 feedback bundle

## Open Items

- The canonical front-owned
  `.coordination/requests/PKT-014-paper-live-drift-frontend-feedback.yaml`
  request is still missing.
- The canonical `ui-done` payload is not replay-clean because it still uses
  `source_commit: HEAD` instead of a truthful immutable front commit.
- Pantheon still needs to resolve the href truth boundary for PKT-014 owner
  navigation targets.

## Not Completed In This Cycle

- No live browser QA against a deployed Pantheon environment.
- No deployed-environment confirmation that the backend-supplied evidence and
  target hrefs are the intended owner-screen destinations.
- Pantheon did not change the published PKT-014 href semantics in this review
  cycle.
