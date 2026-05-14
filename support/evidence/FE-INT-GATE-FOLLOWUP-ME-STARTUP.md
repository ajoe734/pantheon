# FE-INT-GATE-FOLLOWUP-ME-STARTUP Evidence

Task: `FE-INT-GATE-FOLLOWUP-ME-STARTUP`
Owner: `Codex2`
Reviewer: `Claude2`
Date: 2026-05-14

## Source State

Repository: `/home/lupin/code/execute-plans`
Branch: `bff-luv-fe-006-dev-deploy`

Pushed commits:

- `b09d22e` - `FE-INT-GATE-FOLLOWUP-ME-STARTUP: close local role fallback`
- `df73c3d` - `FE-INT-GATE-FOLLOWUP-ME-STARTUP: surface me startup gap first`

Changes:

- `src/platform/components/TopBar.tsx`
  - Removes the local platform role dropdown fallback when `/bff/me` has no usable session.
  - Keeps the startup session UI sourced from `useMe()`.
  - Shows the Auth/Lock state for `/bff/me` error or missing session instead of local `admin`.
- `e2e/01-startup-session.spec.ts`
  - Keeps the `/bff/me` 401 startup case strict.
  - Attaches startup BFF network evidence before assertions.
  - Requires `interceptedMeRequests > 0` before checking Auth/no-mock text.
  - Allows strict fail-closed `seed fallback blocked` text while still rejecting an armed seed/mock fallback banner.

## Verification

Local source verification passed:

```bash
PANTHEON_FE_BASE_URL=http://127.0.0.1:5174 \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_FALLBACK=strict \
npx playwright test e2e/01-startup-session.spec.ts \
  -g "does not fall back to mock current-user data" \
  --trace=on --reporter=list \
  --output=/tmp/fe-int-gate-followup-me-startup-local-run4
```

Result: `1 passed`.

Build verification passed after the TopBar source change:

```bash
npm run build
```

Result: Vite build completed successfully.

Hosted verification still fails:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_FALLBACK=strict \
npx playwright test e2e/01-startup-session.spec.ts \
  -g "does not fall back to mock current-user data" \
  --trace=on --reporter=list \
  --output=/tmp/fe-int-gate-followup-me-startup-hosted-run3
```

Result: `1 failed`; `interceptedMeRequests=0`.

Hosted bundle check after both commits were pushed:

- `origin/bff-luv-fe-006-dev-deploy` resolves to `df73c3d`.
- `https://pantheon-dev.lovable.app` still serves `/assets/index-DmMAo3dQ.js`.
- The hosted bundle does not contain `Session unavailable`.
- The hosted bundle still contains the local role dropdown path.

## Current Blocker

The source fix is committed and pushed, but the hosted Lovable deployment at
`https://pantheon-dev.lovable.app` is not serving the latest branch head. The
task should remain blocked until Lovable/runtime deployment refreshes the hosted
bundle to include `df73c3d` or provides the correct preview URL that tracks this
branch.
