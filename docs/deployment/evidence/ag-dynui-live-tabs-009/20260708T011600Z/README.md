# AG-DYNUI-LIVE-WORKSHOP-009 Hosted Proof

## Deployment

- Frontend host: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- BFF host: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
- `deployment.json` commit: `9d60297e5c200d05214df7f758ee0c20c224db02`
- `deployment.json` deployed at: `20260708T010932Z`
- Frontend PR carrying the deployed repair: `ajoe734/execute-plans#218`
- Frontend merge commit: `9d60297e5c200d05214df7f758ee0c20c224db02`
- Pantheon task PR carrying the mirrored task artifacts: `ajoe734/pantheon#3053`
- Pantheon task merge commit: `7fc912ccf7d06deef64ccad14d8f28b441023cb7`

## Hosted Verification

Command run from a clean `execute-plans` worktree at `origin/dev`
`9d60297e5c200d05214df7f758ee0c20c224db02`:

```bash
AG_DYNUI_LIVE_WORKSHOP_FE_013_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
  AG_DYNUI_LIVE_WORKSHOP_FE_013_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
  PANTHEON_AUDIT_OUT_DIR=/tmp/ag-dynui-live-tabs-013 \
  npx playwright test e2e/agora-strategy-workshop-hosted.spec.ts --reporter=line
```

Result: `4 passed`.

The hosted proof exercises the deployed Strategy Workshop tab without BFF
intercepts or synthetic workshop fixtures. It verifies:

- `/agora/strategy-workshop` renders `strategy-workshop-live-tab`,
  `strategy-workshop-page-session`, `strategy-workshop-runtime-header`,
  `workshop-conversation`, `completeness-rail`, and `servant-composer`.
- Live BFF reads were observed for list, detail, completeness, readiness,
  cards, and events.
- Selector item text does not expose raw workshop UUID/debug-list output.
- Desktop and mobile screenshots were captured.

## Readback Summary

- Desktop readback: `workshop-hosted-desktop-readback.json`
  - Cards: `2`
  - Events: `1`
  - Visible selector rows:
    - `Strategy workshop / open - 2026-07-05 16:22:58Z`
    - `Strategy workshop / open - 2026-07-05 16:16:15Z`
    - `Strategy workshop / open - 2026-07-05 14:43:35Z`
- Mobile readback: `workshop-hosted-mobile-readback.json`
  - Cards: `2`
  - Events: `1`
  - Same selector rows and no raw UUID visible text.

## CI / Deploy Gates

- `execute-plans` dev deploy run `28909983307`: success.
- `execute-plans` dev `Pantheon FE-BFF Integration Gate` run `28909983328`:
  success, head SHA `9d60297e5c200d05214df7f758ee0c20c224db02`.
