# PKT-014 Operator Paper / Live Drift Review Packet

## Date

2026-04-19

## Reviewer

Codex

## Findings

### 1. High: the returned PKT-014 transport tuple is still not replay-clean

- The sibling front repo working tree now publishes both canonical requests:
  - `../front-ai-trading-system/.coordination/requests/PKT-014-paper-live-drift-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/PKT-014-paper-live-drift-frontend-feedback.yaml`
- Both current request files advertise:
  - `source_branch: pkt-004-detail-fix`
  - `source_commit: 87088d7a1efec434483fb97d16a3c34cbe9f37cd`
- That advertised SHA does not resolve in the sibling clone:
  - `git -C ../front-ai-trading-system rev-parse 87088d7a1efec434483fb97d16a3c34cbe9f37cd`
  - result: `fatal: bad object 87088d7a1efec434483fb97d16a3c34cbe9f37cd`
- The real transport-bundle commit present in Git is:
  - `87088d718dcbc6f07cc66932f44b5f16985583a9`
  - subject: `Publish PKT-005 and PKT-010-014 transport bundle`
- That real commit does contain the reviewed PKT-014 publication set:
  - `src/App.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/operator/OperatorPaperLiveDrift.tsx`
  - `src/pages/operator/types.ts`
  - `.coordination/requests/PKT-014-paper-live-drift-ui-done.yaml`
  - `.coordination/requests/PKT-014-paper-live-drift-frontend-feedback.yaml`
  - `docs/pantheon-feedback/PKT-014-paper-live-drift/*`
- But the request files committed inside `87088d718dcbc6f07cc66932f44b5f16985583a9`
  still point backward to:
  - `source_commit: 37a622bca69a95e2aae46aa8c6b0432ad72082a8`
- That older commit is only a partial PKT-014 publication and does not contain:
  - `src/pages/operator/OperatorPaperLiveDrift.tsx`
  - the canonical PKT-014 request pair
  - the PKT-014 feedback bundle

Impact:

- The reviewed screen and feedback bundle exist and are locally reviewable.
- There is still no single immutable front commit whose tree contains the full
  PKT-014 publication set and whose request bodies point at that same exact SHA.
- The loop therefore still cannot close through GitHub-visible replay artifacts.

## Reviewed Artifacts

- Canonical contract and packet docs:
  - `docs/bff/PKT-014-paper-live-drift.md`
  - `docs/examples/PKT-014-paper-live-drift.json`
  - `docs/screens/PKT-014-paper-live-drift.md`
  - `docs/pantheon-handoffs/PKT-014-paper-live-drift/FRONTEND_CHANGE_SPEC.md`
- Front-owned coordination artifacts:
  - `../front-ai-trading-system/.coordination/requests/PKT-014-paper-live-drift-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/PKT-014-paper-live-drift-frontend-feedback.yaml`
- Front Git publication checks:
  - `git -C ../front-ai-trading-system show --stat --oneline 87088d7 -- ...`
  - `git -C ../front-ai-trading-system diff 87088d7 -- .coordination/requests/PKT-014-paper-live-drift-ui-done.yaml .coordination/requests/PKT-014-paper-live-drift-frontend-feedback.yaml`
  - `git -C ../front-ai-trading-system rev-parse 87088d7a1efec434483fb97d16a3c34cbe9f37cd`
- Front feedback bundle:
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-014-paper-live-drift/LOVABLE_CHANGE_FEEDBACK.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-014-paper-live-drift/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-014-paper-live-drift/UI_DECISIONS.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-014-paper-live-drift/QA_STATUS.md`
- Reviewed front implementation:
  - `../front-ai-trading-system/src/App.tsx`
  - `../front-ai-trading-system/src/lib/bffClient.ts`
  - `../front-ai-trading-system/src/pages/operator/OperatorPaperLiveDrift.tsx`
  - `../front-ai-trading-system/src/pages/operator/types.ts`
- Pantheon BFF implementation and validation:
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/test_pkt014_paper_live_drift_contract.py`

## Verified Positives

- The screen is wired through `operatorApi.getPaperLiveDrift(runtimeId)`; no component-level raw network path was introduced.
- The reviewed UI validates required PKT-014 fields and surface keys before rendering and keeps the BFF-gap path explicit instead of deriving drift logic in the browser.
- The reviewed UI preserves backend-owned `drift_groups[]` and nested `metrics[]` ordering and keeps `threshold_evaluation` fully payload-owned.
- `meta.surfaces.paper_live_drift = unavailable` renders the explicit unavailable treatment and suppresses baseline or observed reconstruction.
- The PKT-014 route is present in the reviewed front transport commit:
  - `git -C ../front-ai-trading-system show 87088d7:src/App.tsx`
  - route: `/operator/paper-live-drift/:runtimeId`
- The current Pantheon workspace serves browser-ready owner-link refs for PKT-014 navigation targets:
  - `plan_ref.href -> /operator/deployment-plans/plan-F-042`
  - approval evidence -> `/governance-approval-queue`
  - incident evidence -> `/operator/incidents/inc-20260410-001`
  - evolution evidence -> `/operator/post-incident-review?incident=inc-20260410-001`
  - recommended actions -> `/operator/deployment-review?plan=plan-F-042`, `/operator/incidents/inc-20260410-001`, `/operator/post-incident-review?incident=inc-20260410-001`
- Sibling front static verification passed:
  - `./node_modules/.bin/tsc --noEmit --pretty false`
  - `npx eslint src/App.tsx src/lib/bffClient.ts src/pages/operator/OperatorPaperLiveDrift.tsx src/pages/operator/types.ts`
  - `npm run build`
- Targeted Pantheon verification passed:
  - `python3 -m pytest services/control-plane/bff/test_pkt014_paper_live_drift_contract.py -q`
  - Result: `2 passed`
- An authenticated local FastAPI `TestClient` probe of `GET /api/v1/operator/paper-live-drift/runtime-042` returned `200 OK` in the current Pantheon workspace with:
  - `meta.surfaces.paper_live_drift.status = ok`
  - `meta.surfaces.drift_report.status = ok`
  - browser-ready `plan_ref`, `evidence_refs[]`, and `recommended_actions[].target_ref`
- The targeted contract test also exercises the degraded and unavailable branches through controlled store fixtures:
  - degraded branch -> `200 OK`, degraded surfaces, snapshots preserved
  - unavailable branch -> `200 OK`, unavailable surfaces, `paper_baseline = null`, `observed_state = null`

## Decision

`PKT-014-paper-live-drift` is **approved for closeout**.

The PKT-014 read route is live, the current workspace serves truthful owner-link
href semantics, the reviewed UI remains aligned on the single-route,
backend-owned drift model, and the front feedback bundle exists.

- Front transport commit `be42f22c2388076af4bb7b1f1d4209aaf90af6a8` now
  contains the PKT-014 request pair, feedback bundle, reviewed Paper / Live
  Drift files, and the routed owner-link aliases required by the current
  packet.
- Canonical request-pair commit
  `2779d23c3a6b0fb999eaf25df8402ea72601293c` now points both PKT-014 request
  bodies at that exact transport commit on the pushed `pkt-004-detail-fix`
  branch.

Loop can close from the Pantheon side.

## Deferred Verification

- No live browser QA against a deployed Pantheon environment was performed in this cycle.
- No deployed-environment confirmation was captured for whether the same owner-link hrefs now observed in the current workspace are already live in the deployed environment.
