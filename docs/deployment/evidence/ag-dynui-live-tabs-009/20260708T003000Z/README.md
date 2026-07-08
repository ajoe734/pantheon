# AG-DYNUI-LIVE-WORKSHOP-009 Evidence

## Local Verification

- Frontend focused tests:
  `npm test -- --run src/lib/bff-v1/agora/workshops.test.ts src/agora/pages/strategy-workshop/StrategyWorkshopPage.test.tsx`
  passed with 16 tests.
- Agora production build:
  `npm run build:agora` passed with the existing large bundle warning.
- Hosted proof harness compile/skip:
  `npx playwright test e2e/13-agora.spec.ts --grep AG-DYNUI-LIVE-WORKSHOP-009`
  passed as 2 skipped without live URL env.
- BFF workshop contract suite:
  `python3 -m pytest services/control-plane/bff/tests/test_agora_strategy_workshop.py -q`
  passed with 67 tests.

## Hosted Pre-Deploy Probe

Target:
`https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`

Command:

```bash
AGORA_LIVE_TABS_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
  npx playwright test e2e/13-agora.spec.ts --grep AG-DYNUI-LIVE-WORKSHOP-009
```

Result:

- Desktop and mobile failed before branch deployment because the hosted tab
  shell did not render the Strategy Workshop session runtime after tab click.
- Playwright snapshots showed the hosted shell navigation with
  `Trading Room`, `Strategy Workshop`, and `Performance`, then an empty main
  area for the Strategy Workshop tab.
- This confirms the live-hosted gap. Final acceptance still requires rerunning
  the same proof after this branch is merged and deployed.
