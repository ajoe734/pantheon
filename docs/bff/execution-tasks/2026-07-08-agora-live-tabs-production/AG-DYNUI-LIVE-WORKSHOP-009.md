# AG-DYNUI-LIVE-WORKSHOP-009

## Scope

Fix the live `/agora/trading-room` Strategy Workshop tab so it renders an
operable workshop runtime instead of stopping at an id/list shell.

## Implementation

- `StrategyWorkshopPage` now auto-selects the newest active workshop from
  `GET /bff/agora/workshops` when the tab has no URL workshop id.
- The selected tab view renders the existing session runtime, which loads
  workshop detail, cards, readiness, events, stream updates, and message writes.
- Workshop list labels no longer fall back to rendering a raw UUID as visible
  operator text when BFF list rows lack a title.
- `workshops.ts` now uses the shared BFF auth header builder for workshop
  reads and message writes, preserving cookie auth while adding bearer/tenant
  headers where available.
- A hosted Playwright proof block was added to `e2e/13-agora.spec.ts`; it is
  skipped unless `AGORA_LIVE_TABS_BASE_URL` or `PANTHEON_FE_BASE_URL` is set.

## Validation

- `npm test -- --run src/lib/bff-v1/agora/workshops.test.ts src/agora/pages/strategy-workshop/StrategyWorkshopPage.test.tsx`
  - 2 files passed, 16 tests passed.
- `npm run build:agora`
  - Passed; Vite reported the existing large bundle warning.
- `npx playwright test e2e/13-agora.spec.ts --grep AG-DYNUI-LIVE-WORKSHOP-009`
  - Passed as 2 skipped when no hosted URL env was provided.
- `python3 -m pytest services/control-plane/bff/tests/test_agora_strategy_workshop.py -q`
  - 67 passed, 124 FastAPI deprecation warnings.

## Hosted Pre-Deploy Probe

Command:

```bash
AGORA_LIVE_TABS_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
  npx playwright test e2e/13-agora.spec.ts --grep AG-DYNUI-LIVE-WORKSHOP-009
```

Result before this branch was merged/deployed:

- Failed on desktop and mobile after clicking Strategy Workshop.
- The hosted page showed the shell navigation, but the main area did not expose
  `strategy-workshop-page-session`, `strategy-workshop-runtime-header`,
  `workshop-conversation`, `completeness-rail`, or `servant-composer`.
- This is pre-deploy evidence of the live gap this task fixes, not final hosted
  proof of the branch.

See:

- `docs/deployment/evidence/ag-dynui-live-tabs-009/20260708T003000Z/README.md`

## Closeout Requirement

After the PR merges and the dev frontend is redeployed from the merged commit,
rerun the hosted Playwright command above and keep desktop/mobile screenshots in
`docs/deployment/evidence/ag-dynui-live-tabs-009/`.
