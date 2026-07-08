# AG-DYNUI-LIVE-TABS-GATE-011 Evidence

Date: 2026-07-08

## Scope

This evidence index closes the Agora live tabs production gate by linking the
hosted proof for all three tabs in the routed Agora shell.

## Hosted Deployment

- Frontend: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
- Hosted `/deployment.json` commit:
  `9d60297e5c200d05214df7f758ee0c20c224db02`
- Source branch: `dev`
- Build mode: `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`,
  `VITE_BFF_REAL_WRITES=false`

## Acceptance Matrix

| Tab | Evidence | Readback proof | Screenshot proof |
| --- | --- | --- | --- |
| Trading Room | `docs/deployment/evidence/ag-dynui-live-readback-008/readback/hosted-browser-bff-probe-2026-07-07.md` | `docs/deployment/evidence/ag-dynui-live-readback-008/bff/direct-bff-readback.json` shows 200 responses and `strategies: 2` for `/bff/agora/trading-room` | `docs/deployment/evidence/ag-dynui-live-readback-008/winner-branch/ag-dynui-full-006-09-live-rollback-applied-desktop.png`; `docs/deployment/evidence/ag-dynui-live-readback-008/mobile/agora-trading-room-mobile.png` |
| Strategy Workshop | `docs/deployment/evidence/ag-dynui-live-tabs-013/README.md` | Desktop and mobile JSON readbacks show 200 responses for workshop list, detail, completeness, readiness, cards, and events | `docs/deployment/evidence/ag-dynui-live-tabs-013/ag-dynui-live-workshop-fe-013-desktop.png`; `docs/deployment/evidence/ag-dynui-live-tabs-013/ag-dynui-live-workshop-fe-013-mobile.png` |
| Performance | `docs/deployment/evidence/ag-dynui-live-tabs-010/20260708T003924Z/README.md` | `docs/deployment/evidence/ag-dynui-live-tabs-010/20260708T003924Z/performance-hosted-smoke.json` shows required BFF reads returned 200 on desktop and mobile | `docs/deployment/evidence/ag-dynui-live-tabs-010/20260708T003924Z/performance-desktop.png`; `docs/deployment/evidence/ag-dynui-live-tabs-010/20260708T003924Z/performance-mobile.png` |

## Result

Pass.

- Trading Room is backed by live BFF readback and no longer presents the old
  generic failure/debug state in hosted proof.
- Strategy Workshop renders the live workshop selector and session runtime;
  selector labels are operator-readable and not raw UUID output.
- Performance renders `Strategy Performance` from live BFF reads and does not
  show the previous placeholder.

## Closeout Verification

Closeout verification run from the Pantheon task worktree:

```bash
curl -fsS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
git diff --check HEAD~1 HEAD
```

The deployment manifest readback returned the expected execute-plans SHA and
safe live-dev build mode. `git diff --check HEAD~1 HEAD` passed for this
evidence packet and the matching execution task record.
