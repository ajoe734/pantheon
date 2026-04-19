# PKT Consultation Workbench Backend Delivery Note

## Status

`front-followup-required`

## Summary

Pantheon re-reviewed the returned `ui-done` handoff
`.coordination/requests/PKT-consultation-workbench-ui-done.yaml` against the
current PKT-consultation-workbench contract, example payload, coordination
replay rules, the sibling front implementation, and the local Pantheon BFF
app.

The core consultation overview read surface is live in the current workspace:

- Pantheon serves `GET /api/v1/workbench/consultation`
- targeted PKT-015 contract verification passes
- a direct local `TestClient` read returns `200 OK` with the published
  overview payload shape
- the reviewed front changed-file build and targeted ESLint slice both pass

No new endpoint, write path, or client-side shadow state is authorized in this
cycle.

The loop still cannot close for one concrete reason:

1. the returned request pair is not replay-clean because both request bodies
   advertise `source_commit: 37a622bca69a95e2aae46aa8c6b0432ad72082a8`, but
   that Git-visible commit still contains the old `ComingSoonWorkbench`
   Consultation page and does not contain the consultation request pair or
   feedback bundle

## Verified UI Alignment

- `ConsultationWorkbench.tsx` reads the screen through
  `workbenchApi.getConsultationOverview()` and does not add component-level raw
  network calls.
- The page validates the required consultation overview fields before
  rendering, including the required `meta.surfaces.overview` and
  `meta.surfaces.packet_family` keys.
- The screen renders backend-owned module order via `wave_order`,
  `missing_contracts[]`, `support_refs[]`, and `next_steps[]` directly from
  the payload.
- The route is explicitly framed as an overview packet and does not introduce
  consult request forms, committee-room panels, or red-team memo UI.
- The missing-required-fields branch instructs the operator to emit the
  canonical `bff-gap` handoff instead of synthesizing local Consultation
  Workbench state.

## Delivered Findings

### 1. Pantheon consultation overview route is live and contract-shaped

Published contract:

- `GET /api/v1/workbench/consultation`

Observed runtime result in the current workspace:

- `200 OK` from local FastAPI `TestClient`
- `workbench_id = consultation-workbench`
- `overall_status = overview_ready`
- `packet_family.family_id = CW-008`
- `module_counts = { total: 4, ready: 0, not_ready: 4 }`
- `meta.surfaces.overview.status = ok`
- `meta.surfaces.packet_family.status = ok`

Observed automated verification:

- `python3 -m pytest services/control-plane/bff/test_pkt015_consultation_workbench_contract.py -q`
- Result: `1 passed`

Impact:

- the reviewed screen can load its primary data path against the current
  Pantheon runtime
- no Pantheon-side API gap or payload-shape repair is required for this cycle

### 2. The reviewed front changed-file slice is buildable and lint-clean

Observed sibling front verification:

- `npm run build`
- Result: passed with the existing non-blocking Vite chunk-size warning
- `npx eslint src/pages/workbench/ConsultationWorkbench.tsx src/pages/workbench/types.ts src/lib/bffClient.ts src/components/AppSidebar.tsx`
- Result: passed

Impact:

- the current Pantheon block is not caused by a local front build failure or a
  changed-file lint regression in the reviewed consultation slice
- the remaining blocker stays on publication replay truth only

### 3. The GitHub-visible front publication is still incomplete

Current front-repo state:

- the local sibling checkout now contains the reviewed Consultation overview
  implementation, request pair, and feedback bundle in the working tree
- both returned request bodies still advertise
  `source_commit: 37a622bca69a95e2aae46aa8c6b0432ad72082a8`
- at that advertised commit, `src/pages/workbench/ConsultationWorkbench.tsx`
  is still the older `ComingSoonWorkbench` placeholder
- the advertised commit does not contain:
  - `.coordination/requests/PKT-consultation-workbench-ui-done.yaml`
  - `.coordination/requests/PKT-consultation-workbench-frontend-feedback.yaml`
  - `docs/pantheon-feedback/PKT-consultation-workbench/`

Impact:

- replay cannot reconstruct the reviewed consultation cycle from a truthful
  front commit tuple
- supervisor-visible closeout cannot proceed until the front repo republishes
  the reviewed UI files, request pair, and feedback bundle from one immutable
  Git-visible commit

## Pantheon-Side Outcome

- Pantheon contract: unchanged in this review cycle
- Published endpoint: already live in the current workspace
- Pantheon delivery recorded:
  - mirrored the reviewed consultation frontend-feedback summary
  - recorded the consultation review packet and delivery lock
  - published the backend-delivery response for the next front-owned cycle
- Front follow-up still required:
  - publish the canonical consultation frontend-feedback request and feedback
    bundle
  - republish consultation ui-done from one truthful immutable front commit
    that actually contains the reviewed handoff set
- Pantheon follow-up still required:
  - none
- Current loop outcome: `front-followup-required`; packet loop remains open
  until the front publication tuple is Git-visible and truthful

## Verification Performed

- Reviewed the returned front-owned request artifacts:
  - `../front-ai-trading-system/.coordination/requests/PKT-consultation-workbench-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/PKT-consultation-workbench-frontend-feedback.yaml`
- Reviewed the canonical packet:
  - `docs/bff/PKT-consultation-workbench.md`
  - `docs/screens/PKT-consultation-workbench.md`
  - `docs/examples/PKT-consultation-workbench.json`
  - `docs/pantheon-handoffs/PKT-consultation-workbench/FRONTEND_CHANGE_SPEC.md`
- Re-reviewed the sibling front implementation:
  - `../front-ai-trading-system/src/pages/workbench/ConsultationWorkbench.tsx`
  - `../front-ai-trading-system/src/pages/workbench/types.ts`
  - `../front-ai-trading-system/src/lib/bffClient.ts`
  - `../front-ai-trading-system/src/components/AppSidebar.tsx`
- Ran sibling front verification:
  - `npm run build`
  - Result: passed with the existing non-blocking Vite chunk-size warning
  - `npx eslint src/pages/workbench/ConsultationWorkbench.tsx src/pages/workbench/types.ts src/lib/bffClient.ts src/components/AppSidebar.tsx`
  - Result: passed
- Re-ran targeted Pantheon verification:
  - `python3 -m pytest services/control-plane/bff/test_pkt015_consultation_workbench_contract.py -q`
  - Result: `1 passed`
- Probed the current Pantheon BFF app by loading
  `services/control-plane/bff/main.py` with FastAPI `TestClient` and confirmed
  `GET /api/v1/workbench/consultation` returns `200 OK` with the published
  Consultation overview payload shape in the current workspace
- Verified publication divergence by replaying the advertised front commit:
  - `git -C ../front-ai-trading-system show 37a622bca69a95e2aae46aa8c6b0432ad72082a8:src/pages/workbench/ConsultationWorkbench.tsx`
  - `git -C ../front-ai-trading-system show 37a622bca69a95e2aae46aa8c6b0432ad72082a8:.coordination/requests/PKT-consultation-workbench-ui-done.yaml`
  - `git -C ../front-ai-trading-system show 37a622bca69a95e2aae46aa8c6b0432ad72082a8:.coordination/requests/PKT-consultation-workbench-frontend-feedback.yaml`

## Not Completed

- No live browser QA against a deployed Pantheon environment was performed in
  this review cycle
- Pantheon did not modify the published Consultation overview contract in this
  cycle because no Pantheon API gap remains
- The front repo did not yet publish a truthful Git-visible consultation
  request pair plus feedback bundle in this cycle
