# OPS-GEM-REDEPLOY-001 Evidence

Task: `OPS-GEM-REDEPLOY-001`
Owner: `Codex`
Reviewer: `Gemini`
Recorded: 2026-05-15T05:06:00Z

## Result

`pantheon-dev.lovable.app` is no longer serving the stale assets named in the
chair reviews (`index-BYfBkno5.js` / `index-DmMAo3dQ.js`). The current hosted
page serves:

- URL: `https://pantheon-dev.lovable.app/management`
- asset: `/assets/index-vlevju41.js`
- asset size: `2008909`
- asset sha256: `8f7acc9b187bb15630771827ebd6381934f72e99a794001fa5beca50ed7f8a81`
- execute-plans branch checked locally: `bff-luv-fe-006-dev-deploy`
- execute-plans HEAD checked locally and on origin:
  `e3452cfd43baf3aa16e0d95bb2ad3d6b8d5f79a0`

The hosted asset contains the intended dev BFF URL and dev browser bearer token:

- `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`: 7 matches
- `pantheon-dev-browser:reviewer`: 7 matches
- `VITE_BFF_FALLBACK`: 5 matches
- `VITE_BFF_REAL_WRITES`: 2 matches

Lovable MCP status for project `140c41d5-9cd8-4d6b-ba02-66d5941d0dbe`
returned `ready`.

## Preview URL

Lovable project URL:

```text
https://lovable.dev/projects/140c41d5-9cd8-4d6b-ba02-66d5941d0dbe
```

Candidate Lovable preview URL from the current Lovable project screenshot:

```text
https://id-preview-a7067bd5--140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovable.app/management
```

Anonymous worker verification of that preview URL redirects through Lovable
auth bridge:

```text
final_http=200
final_url=https://lovable.dev/auth-bridge?project_id=140c41d5-9cd8-4d6b-ba02-66d5941d0dbe&return_url=...
title=Internal Lovable project
```

Older devtools evidence also observed:

```text
https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com/management
```

That URL now also redirects through Lovable auth bridge for this worker. This
means the preview exists, but unattended Day 1 preview soak still needs either a
Lovable-authenticated browser context or a public preview URL that does not
auth-bridge.

## Dev BFF Credential

Use the documented dev-only browser bearer:

```bash
PANTHEON_BFF_SMOKE_BEARER_TOKEN='pantheon-dev-browser:reviewer'
```

This value is the non-secret dev browser bootstrap token documented for the dev
Lovable app. The authenticated smoke did not record the token value; it recorded
only `auth_source.kind=provided_bearer` and `sha256_12=008288ce7ac0`.

Authenticated read smoke:

```bash
PANTHEON_BFF_SMOKE_BEARER_TOKEN='pantheon-dev-browser:reviewer' \
  python3 scripts/probe_bff_authenticated_live.py \
  --base-url https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
  --output support/evidence/OPS-GEM-REDEPLOY-001/authenticated-live-dev-bff.json
```

Result:

```json
{"total":32,"passed":32,"failed":0,"read_probes":30,"write_probes":0,"VITE_BFF_MODE_live_allowed":true,"VITE_BFF_REAL_WRITES_true_allowed":false,"live_capital_side_effects":false}
```

## Hosted Browser Probe

Command:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
PANTHEON_AUDIT_OUT_DIR=/home/lupin/code/pantheon/support/evidence/OPS-GEM-REDEPLOY-001 \
  node scripts/probe-hosted-browser-bff.mjs
```

Evidence:

- `support/evidence/OPS-GEM-REDEPLOY-001/hosted-browser-bff-probe-2026-05-15.md`

Summary:

- intended BFF URL present: `true`
- old BFF URL present: `false`
- old BFF hit count: `0`
- required `/bff/v5/control-room` response: `200`
- BFF request count: `11`
- failed request count: `0`
- pass: `true`

## FE Hosted Rechecks

F05 hosted rerun:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_MODE=live VITE_BFF_FALLBACK=strict VITE_BFF_REAL_WRITES=true \
  npx playwright test e2e/04-sentinel-remediation.spec.ts \
  --trace=on --reporter=list \
  --output=/home/lupin/code/pantheon/support/evidence/OPS-GEM-REDEPLOY-001/f05-hosted-test-results
```

Results:

- run 1: `2 passed`
- run 2: `2 passed`
- evidence dirs:
  - `support/evidence/OPS-GEM-REDEPLOY-001/f05-hosted-test-results/`
  - `support/evidence/OPS-GEM-REDEPLOY-001/f05-hosted-test-results-run2/`

F15 hosted strict rerun:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_MODE=live VITE_BFF_FALLBACK=strict PANTHEON_E2E_STRICT=1 \
  npx playwright test e2e/09-strict-vs-hybrid.spec.ts \
  --trace=on --reporter=list \
  --output=/home/lupin/code/pantheon/support/evidence/OPS-GEM-REDEPLOY-001/f15-hosted-test-results
```

Results:

- run 1: `1 skipped, 2 passed`
- run 2: `1 skipped, 2 passed`
- evidence dirs:
  - `support/evidence/OPS-GEM-REDEPLOY-001/f15-hosted-test-results/`
  - `support/evidence/OPS-GEM-REDEPLOY-001/f15-hosted-test-results-run2/`

ME-STARTUP hosted focused rerun:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_FALLBACK=strict \
  npx playwright test e2e/01-startup-session.spec.ts \
  -g "does not fall back to mock current-user data" \
  --trace=on --reporter=list \
  --output=/home/lupin/code/pantheon/support/evidence/OPS-GEM-REDEPLOY-001/me-startup-hosted-test-results
```

Result:

- `1 failed`
- the old blocker is partially cleared: `/bff/me` is now intercepted, so hosted
  no longer has `interceptedMeRequests=0`
- remaining blocker: the injected 401 path still renders the hybrid banner text
  `HYBRID` / `live / seed fallback armed`
- evidence:
  `support/evidence/OPS-GEM-REDEPLOY-001/me-startup-hosted-test-results/`

## Acceptance Mapping

| Acceptance item | Status |
|---|---|
| Provide Lovable preview branch URL | Partial: candidate preview URL recorded, but it auth-bridges for this worker |
| Provide dev BFF JWT/bearer credential or reason unavailable | Met: dev-only bearer recorded and authenticated smoke passed |
| `pantheon-dev.lovable.app` refresh/redeploy verified with asset hash | Met: `index-vlevju41.js`, sha256 recorded, browser probe passed |
| BFF-CONSOL-022 Day 1 probe env usable | Partial: dev BFF authenticated env is usable; strict preview browser remains auth-bridged |
| FE hosted blockers can reverify or have clear remaining blocker | Met: F05/F15 pass twice; ME-STARTUP has a new clear blocker |
| Do not use archived sidecar task id | Met: all evidence is under `OPS-GEM-REDEPLOY-001` |

## Remaining Blockers

1. BFF-CONSOL-022 strict preview Day 1 cannot be started by this unattended
   worker until the Lovable preview URL is accessible in an authenticated
   browser context or a public preview URL is supplied.
2. `FE-INT-GATE-FOLLOWUP-ME-STARTUP` still fails its hosted 401 path because
   the page renders hybrid seed-fallback status even though `/bff/me` is now
   requested.

## 2026-05-15 Follow-up Blocker Recheck

`FE-INT-GATE-FOLLOWUP-ME-STARTUP` commit `3ddb5e6` fixed the false strict
runtime setup in `e2e/01-startup-session.spec.ts`. The test now installs the
same browser runtime strict override used by F15 before page bootstrap.

Hosted focused rerun:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_FALLBACK=strict \
npx playwright test e2e/01-startup-session.spec.ts \
  -g "strict startup|does not fall back" \
  --reporter=list \
  --output=/tmp/fe-int-me-startup-fix
```

Result: `2 passed`.

Updated blocker state:

- Cleared: hosted `/bff/me` 401 startup path no longer renders the hybrid
  seed-fallback banner under strict runtime override.
- Still open: BFF-CONSOL-022 strict preview Day 1 requires a public strict
  preview URL or an authenticated Lovable preview context for the soak runner.
