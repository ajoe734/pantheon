# AG-DYNUI-LIVE-WORKSHOP-FE-013 Evidence

Date: 2026-07-08

## Scope

Repair the active `ajoe734/execute-plans` Strategy Workshop frontend so the
hosted dev runtime renders the live workshop selector and session runtime
instead of a raw workshop UUID/debug list.

## Execute-Plans Delivery

- PR: https://github.com/ajoe734/execute-plans/pull/218
- PR head: `3c74683b0466fade952f6afb44ba243a3b90ed98`
- Dev merge commit: `9d60297e5c200d05214df7f758ee0c20c224db02`
- Deploy workflow: https://github.com/ajoe734/execute-plans/actions/runs/28909983307
- Deploy result: success
- Dev FE: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- Dev BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`

Hosted `/deployment.json` readback after deploy:

```json
{
  "app": "execute-plans",
  "environment": "pantheon-dev-fe",
  "deployedAt": "20260708T010932Z",
  "commit": "9d60297e5c200d05214df7f758ee0c20c224db02",
  "sourceRef": "9d60297e5c200d05214df7f758ee0c20c224db02",
  "sourceBranch": "dev",
  "buildMode": {
    "VITE_BFF_MODE": "live",
    "VITE_BFF_FALLBACK": "strict",
    "VITE_BFF_REAL_WRITES": "false"
  }
}
```

## Hosted Readback

Command:

```sh
AG_DYNUI_LIVE_WORKSHOP_FE_013_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
AG_DYNUI_LIVE_WORKSHOP_FE_013_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
PANTHEON_AUDIT_OUT_DIR=/tmp/pantheon-worker-worktrees/pantheon/ag-dynui-live-workshop-fe-013/docs/deployment/evidence/ag-dynui-live-tabs-013 \
npx playwright test e2e/agora-strategy-workshop-hosted.spec.ts --project=chromium
```

Result: 2 passed.

Generated readbacks:

- `ag-dynui-live-workshop-fe-013-desktop.json`
- `ag-dynui-live-workshop-fe-013-desktop.png`
- `ag-dynui-live-workshop-fe-013-mobile.json`
- `ag-dynui-live-workshop-fe-013-mobile.png`

Selector text observed on both desktop and mobile:

```text
Strategy workshop
open - 2026-07-05 16:22:58Z

Strategy workshop
open - 2026-07-05 16:16:15Z

Strategy workshop
open - 2026-07-05 14:43:35Z
```

The hosted test asserts these selector texts do not match a raw UUID pattern.
The readback also observed live BFF calls for:

- `GET /bff/agora/workshops`
- `GET /bff/agora/workshops/{id}`
- `GET /bff/agora/workshops/{id}/completeness`
- `GET /bff/agora/workshops/{id}/readiness`
- `GET /bff/agora/workshops/{id}/cards`
- `GET /bff/agora/workshops/{id}/events`

All observed non-stream Agora BFF requests returned 200 and carried
Authorization.
