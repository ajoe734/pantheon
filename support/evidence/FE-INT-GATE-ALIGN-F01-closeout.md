# FE-INT-GATE-ALIGN-F01 closeout

Task: `FE-INT-GATE-ALIGN-F01`
Owner: `Codex`
Reviewer: `Codex2`
Date: 2026-05-15

## Resolution

The prior closeout blocker was inherited from `FE-INT-GATE-FOLLOWUP-ME-STARTUP`.
That follow-up is now done and archived. The hosted strict runtime issue was
fixed in execute-plans commit `3ddb5e6`, which installs the browser runtime
strict fallback override before F01 startup page bootstrap.

## Verification

Run 1:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_FALLBACK=strict \
npx playwright test e2e/01-startup-session.spec.ts \
  --reporter=list \
  --output=/tmp/fe-int-f01-closeout-runA
```

Result: `4 passed`.

Run 2:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_FALLBACK=strict \
npx playwright test e2e/01-startup-session.spec.ts \
  --reporter=list \
  --output=/tmp/fe-int-f01-closeout-runB
```

Result: `4 passed`.

## Acceptance

- Hosted Lovable DOM/network was used.
- The startup `/bff/me` shape check passed.
- The strict startup no-mock banner check passed.
- Browser-native SSE opened.
- Injected `/bff/me` 401 rendered auth/error state without mock current-user fallback.
- No acceptance condition was downgraded.
