# TW-03 Before/After Compare Review Packet

## Date

2026-04-21

## Reviewer

Codex

## Findings

None.

## Reviewed Artifacts

- Canonical contract and packet docs:
  - `docs/bff/TW-03-before-after-compare.md`
  - `docs/examples/TW-03-before-after-compare.json`
  - `docs/screens/TW-03-before-after-compare.md`
  - `docs/pantheon-handoffs/TW-03-before-after-compare/FRONTEND_CHANGE_SPEC.md`
  - `.coordination/responses/TW-03-before-after-compare-contract-ready.yaml`
  - `.coordination/responses/TW-03-before-after-compare-lovable-ui-task.yaml`
- Returned front-owned request pair:
  - `../front-ai-trading-system/.coordination/requests/TW-03-before-after-compare-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/TW-03-before-after-compare-frontend-feedback.yaml`
- Returned front feedback bundle:
  - `../front-ai-trading-system/docs/pantheon-feedback/TW-03-before-after-compare/LOVABLE_CHANGE_FEEDBACK.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/TW-03-before-after-compare/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/TW-03-before-after-compare/UI_DECISIONS.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/TW-03-before-after-compare/QA_STATUS.md`
- Reviewed front implementation at the advertised source commit:
  - `../front-ai-trading-system/src/App.tsx`
  - `../front-ai-trading-system/src/lib/bffClient.ts`
  - `../front-ai-trading-system/src/pages/trainer/TrainerBeforeAfterCompare.tsx`
  - `../front-ai-trading-system/src/pages/trainer/replayContract.ts`
  - `../front-ai-trading-system/src/pages/trainer/types.ts`
- Pantheon BFF implementation:
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/test_tw03_before_after_compare_contract.py`

## Verified

- The immutable TW-03 publication chain is now replayable from Git history:
  `origin/pkt-004-detail-fix` resolves to
  `dbc4a16dc0e9f0b8d33e1576908341ea056c660d`, and that publish commit contains
  the canonical request pair plus
  `docs/pantheon-feedback/TW-03-before-after-compare/*`. The reviewed
  implementation commit
  `ed8db5db794202659c5a377d2939df580585ccbb` contains `src/App.tsx`,
  `src/lib/bffClient.ts`,
  `src/pages/trainer/TrainerBeforeAfterCompare.tsx`,
  `src/pages/trainer/replayContract.ts`, and `src/pages/trainer/types.ts`, and
  the canonical request pair in `dbc4a16...` truthfully points back to that
  implementation commit.
- The reviewed front implementation remains contract-aligned:
  `src/App.tsx` still mounts `/trainer/sessions/:session_id/compare`;
  `src/lib/bffClient.ts` still limits TW-03 to the published GET/POST preview
  route family and posts only `{ refresh_mode: 'manual' }`; and
  `TrainerBeforeAfterCompare.tsx` still fetches and polls through
  `tw03PreviewApi.getPreview(session_id, eval_id)` only, while
  `validatePreviewResponse()` now enforces required per-item
  `metric_delta[]`, `warnings[]`, and `control_diff[]` fields before compare
  data reaches render.
- Sibling front validation now passes for the reviewed TW-03 slice:
  clean archives of both `ed8db5db794202659c5a377d2939df580585ccbb` and
  `dbc4a16dc0e9f0b8d33e1576908341ea056c660d` succeed with
  `npm ci --legacy-peer-deps && npm run build`. Targeted `npx eslint` on the
  reviewed files exits 0 with one non-blocking
  `react-hooks/exhaustive-deps` warning at
  `src/pages/trainer/TrainerBeforeAfterCompare.tsx:351`.
- The active runtime on `http://127.0.0.1:18001` remains truthful for TW-03:
  `GET /openapi.json` advertises the preview route family, operator-auth GET
  `/api/v1/trainer/sessions/trn-20260419-001/preview` returns the published
  stale complete envelope with backend-owned warning ordering and degraded
  copy, and operator-auth GET
  `/api/v1/trainer/sessions/trn-20260418-003/preview` returns the structured
  `preview_unavailable` degraded-success envelope.
- Pantheon's local TW-03 contract proof is now date-sensitive but not blocked:
  `python3 -m pytest -q services/control-plane/bff/test_tw03_before_after_compare_contract.py`
  returns `1 failed, 3 passed` because the seeded
  `deadline_at = 2026-04-20T19:50:45Z` is already in the past on
  `2026-04-21`, so `read_store.py` intentionally converts that pending lookup
  to `preview_unavailable`. This is a Pantheon fixture-maintenance caveat, not
  evidence of a missing TW-03 route or front-end gap.
- No Pantheon BFF endpoint or contract change is needed for this cycle.

## Decision

Approved. `TW-03-before-after-compare` is ready for `review_approved`.

The prior front-owned blockers are resolved: the front branch now exposes a
truthful Git-visible implementation/publish chain, the missing
`src/pages/trainer/replayContract.ts` dependency is committed, clean archive
builds pass for both the implementation and publish snapshots, and the compare
validator now enforces the required TW-03 per-item subfields before render.
The live Pantheon runtime still advertises the TW-03 preview route family and
serves structured stale plus `preview_unavailable` responses. No Pantheon API
gap remains in this loop.

## Residual Risk

- Deployed browser QA remains non-blocking follow-up for
  `/trainer/sessions/:session_id/compare`.
- Targeted `npx eslint` on the reviewed front files emits one non-blocking
  `react-hooks/exhaustive-deps` warning at
  `src/pages/trainer/TrainerBeforeAfterCompare.tsx:351`.
- Pantheon's local pending-preview proof fixture should be refreshed before the
  seeded 4/4 test is relied upon again after 2026-04-21.
