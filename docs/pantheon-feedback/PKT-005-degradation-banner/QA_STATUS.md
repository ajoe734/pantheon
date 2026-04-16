# PKT-005 Global Degradation Banner — QA Status

## Status

Static verification complete.

## Checks completed

- `npm run build` passed in `front-ai-trading-system` against the reviewed UI commit `7406990a8311ef6865491fcdb883b677a98ff6c9`.
- Targeted ESLint passed for all PKT-005 changed files:
  - `src/components/GlobalDegradationBanner.tsx`
  - `src/lib/degradationBanner.ts`
  - `src/pages/operator/DeploymentReviewConsole.tsx`
  - `src/pages/operator/IncidentHome.tsx`
  - `src/pages/operator/IncidentDetail.tsx`
  - `src/pages/operator/PostIncidentReviewConsole.tsx`
- All five banner variants were cross-checked against the PKT-005 screen spec decision tree (`docs/screens/PKT-005-degradation-banner.md`) and all six example payloads in `docs/examples/PKT-005-degradation-banner.json`.
- The split-read merge logic for PKT-002 Incident Home was verified against the BFF contract merge rules (`docs/bff/PKT-005-degradation-banner.md`): pre-seeded unavailable keys, oldest staleness selection, and per-response surface-key isolation.
- `npx --yes tsx src/components/GlobalDegradationBanner.test.tsx` passed, exercising all five variants plus the split-read merge case with oldest-staleness preservation through the `node:test` assertions in the checked-in banner test file.
- `findMissingSurfaceFields` gap detection was verified for each required surface key set defined in `docs/bff/PKT-005-degradation-banner.md`.
- No raw `fetch` or `axios` calls were introduced in component files. All network access uses the shared `operatorApi` BFF client.
- No demo providers are imported.

## Not completed in this cycle

- Live browser QA against a running Pantheon BFF.
- Live RBAC verification for viewer-token rejection and operator-role success.
- Live SSE-triggered refresh verification confirming that the banner updates only after a full BFF refetch, not from the SSE event payload directly.
- Full repo-wide ESLint conformance. Only the touched PKT-005 files were linted in this cycle.

## Risk note

The remaining risk is runtime-only validation against the live Pantheon BFF — particularly confirming that all four Operator Console composed view endpoints (`deployment-review`, `incident-response`, `post-incident-review`, `incidents`/`kill-switch/status`) return the `meta.surfaces` and `meta.staleness` fields with the correct shapes. The static contract-ready packet confirms the shape specification; live verification requires a deployed BFF.
