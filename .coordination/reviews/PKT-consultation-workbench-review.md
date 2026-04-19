# PKT Consultation Workbench Review Packet

## Date

2026-04-18

## Reviewer

Codex

## Findings

### 1. High: the returned PKT-consultation-workbench handoff is not replay-clean under the coordination loop rules

- The mirrored `ui-done` and `frontend-feedback` requests both advertise
  `source_commit: 37a622bca69a95e2aae46aa8c6b0432ad72082a8`:
  `../front-ai-trading-system/.coordination/requests/PKT-consultation-workbench-ui-done.yaml:5`
  and
  `../front-ai-trading-system/.coordination/requests/PKT-consultation-workbench-frontend-feedback.yaml:5`.
- At that advertised commit, the shipped
  `src/pages/workbench/ConsultationWorkbench.tsx` is still the old
  `ComingSoonWorkbench` placeholder rather than the overview implementation:
  `git show 37a622bca69a95e2aae46aa8c6b0432ad72082a8:src/pages/workbench/ConsultationWorkbench.tsx`.
- The advertised commit does not contain either returned coordination request:
  `git show 37a622bca69a95e2aae46aa8c6b0432ad72082a8:.coordination/requests/PKT-consultation-workbench-ui-done.yaml`
  and
  `git show 37a622bca69a95e2aae46aa8c6b0432ad72082a8:.coordination/requests/PKT-consultation-workbench-frontend-feedback.yaml`
  both fail because those files are absent from that Git-visible snapshot.

Impact:

- Pantheon can review the local sibling checkout, but it cannot honestly mark
  the Consultation Workbench cycle closed through GitHub-visible coordination
  artifacts until the UI files, request pair, and feedback bundle are
  published from one immutable front commit and both request bodies point back
  to that same commit.

## Reviewed Artifacts

- Canonical contract and packet docs:
  - `docs/bff/PKT-consultation-workbench.md`
  - `docs/examples/PKT-consultation-workbench.json`
  - `docs/screens/PKT-consultation-workbench.md`
  - `docs/pantheon-handoffs/PKT-consultation-workbench/FRONTEND_CHANGE_SPEC.md`
- Pantheon coordination state:
  - `.coordination/responses/PKT-consultation-workbench-contract-ready.yaml`
  - `.coordination/responses/PKT-consultation-workbench-lovable-ui-task.yaml`
- Returned front-owned requests:
  - `../front-ai-trading-system/.coordination/requests/PKT-consultation-workbench-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/PKT-consultation-workbench-frontend-feedback.yaml`
- Front implementation under review:
  - `../front-ai-trading-system/src/pages/workbench/ConsultationWorkbench.tsx`
  - `../front-ai-trading-system/src/pages/workbench/types.ts`
  - `../front-ai-trading-system/src/lib/bffClient.ts`
  - `../front-ai-trading-system/src/components/AppSidebar.tsx`
- Pantheon BFF implementation and tests:
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/test_pkt015_consultation_workbench_contract.py`

## Verified Positives

- The reviewed Consultation Workbench page reads only through the shared
  `workbenchApi.getConsultationOverview()` helper and does not add a
  component-level raw fetch path:
  `../front-ai-trading-system/src/pages/workbench/ConsultationWorkbench.tsx:273`
  and `../front-ai-trading-system/src/lib/bffClient.ts:511`.
- Required-field validation covers the top-level overview fields,
  `packet_family`, `module_counts`, every `modules[]` entry,
  every `support_refs[]` entry, `next_steps`, and the required
  `meta.surfaces.overview` plus `meta.surfaces.packet_family` surface keys:
  `../front-ai-trading-system/src/pages/workbench/ConsultationWorkbench.tsx:58`.
- The page preserves backend-owned module order by sorting on `wave_order`,
  renders `missing_contracts[]` and `upstream_dependencies[]` directly from the
  payload, and keeps the packet explicitly read-only:
  `../front-ai-trading-system/src/pages/workbench/ConsultationWorkbench.tsx:318`
  and `../front-ai-trading-system/src/pages/workbench/ConsultationWorkbench.tsx:473`.
- The contract-mismatch branch explicitly points operators back to the
  canonical `bff-gap` handoff instead of synthesizing Consultation Workbench
  state locally:
  `../front-ai-trading-system/src/pages/workbench/ConsultationWorkbench.tsx:348`.
- The shared BFF client exposes the published Consultation overview route and
  keeps it inside the centralized client boundary:
  `../front-ai-trading-system/src/lib/bffClient.ts:521`.
- The sidebar treats Consultation as a live overview surface rather than a
  “Soon” placeholder:
  `../front-ai-trading-system/src/components/AppSidebar.tsx:69`.
- The sibling front production build passed:
  - `npm run build`
  - Result: passed with the existing non-blocking Vite chunk-size warning.
- The targeted changed-file ESLint slice passed:
  - `npx eslint src/pages/workbench/ConsultationWorkbench.tsx src/pages/workbench/types.ts src/lib/bffClient.ts src/components/AppSidebar.tsx`
- Targeted Pantheon verification passed:
  - `python3 -m pytest services/control-plane/bff/test_pkt015_consultation_workbench_contract.py -q`
  - Result: `1 passed`
- Direct local BFF probing with FastAPI `TestClient` returned `200 OK` for
  `GET /api/v1/workbench/consultation` in the current workspace with the
  published overview shape:
  - `workbench_id = consultation-workbench`
  - `overall_status = overview_ready`
  - `packet_family.family_id = CW-008`
  - `module_counts = {total: 4, ready: 0, not_ready: 4}`
  - `meta.surfaces.overview.status = ok`
  - `meta.surfaces.packet_family.status = ok`

## Decision

`PKT-consultation-workbench` is **follow-up required**.

The Consultation Workbench overview route is live, the reviewed UI stays inside
the published single-route contract, and the page remains correctly read-only.
No new Pantheon API gap is required for this cycle.

The loop still cannot close because the returned request pair points at a
Git-visible commit that does not actually contain the overview implementation
or the returned consultation coordination artifacts.

## Required Follow-up

1. Front repo: publish one Git-visible commit that contains:
   `src/pages/workbench/ConsultationWorkbench.tsx`,
   `src/pages/workbench/types.ts`,
   `src/lib/bffClient.ts`,
   `src/components/AppSidebar.tsx`,
   `.coordination/requests/PKT-consultation-workbench-ui-done.yaml`,
   `.coordination/requests/PKT-consultation-workbench-frontend-feedback.yaml`,
   and `docs/pantheon-feedback/PKT-consultation-workbench/*`.
2. Front repo: republish both consultation request bodies from that same final
   commit and replace `source_commit: 37a622bca69a95e2aae46aa8c6b0432ad72082a8`
   with the exact immutable publication commit SHA.
3. Front repo: keep the screen read-only and continue rendering backend-owned
   module order, support refs, and next steps exactly as supplied by
   `GET /api/v1/workbench/consultation`.

## 2026-04-19 Closeout Addendum

The front-owned publication blocker is now resolved.

- The consultation overview request pair is now Git-visible at
  `c9c1e20726bfc1d35f3ddcbb4f7552859f1d8f5d`.
- Both request bodies now point `source_commit` at
  `77ab876e05dbb206f4fd4abc39051df86f6127c2`, which contains the reviewed
  overview screen and the returned feedback bundle.
- Pantheon's current route remains contract-correct:
  `python3 -m pytest services/control-plane/bff/test_pkt015_consultation_workbench_contract.py -q`
  passed in the current workspace.

## Final Decision

**APPROVED.**
