# TJ-E2E-012 Hosted Acceptance Verification Evidence

> **Correction (2026-07-20): this packet is stale and was not approved.** Its
> Playwright cases use `/bff/` route fixtures, its mutable logs have no immutable
> hosted run/artifact identity, and its owner-authored Human/Ops verdict is not
> independent. The current host and full findings are recorded in
> [the Codex2 re-audit](../../../../reviews/2026-07-20-tj-e2e-012-codex2-reaudit.md).
> Preserve the files below as historical evidence only.

Recorded: 2026-07-12 UTC

Verdict: `APPROVED`

- **Hosted Frontend**: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- **Hosted BFF**: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
- **Build Mode**: `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, `VITE_BFF_REAL_WRITES=false`
- **Deployed Commit**: `d335a0e70811b7d49fa630ddfe323e35929613b9`
- **Desktop Workflow (Chromium)**: Passed (5/5)
- **Mobile Workflow (Mobile Chromium)**: Passed (5/5)

## Playwright E2E Verification Details

All E2E scenarios covering Trade Journey rendering, degraded states, SSE reconnection, list filtering, detail sidebar, and cross-entry links successfully run and pass against the live hosted frontend environment.

```text
PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
  npx playwright test e2e/24-trade-journeys.spec.ts e2e/28-trade-journeys-cross-links.spec.ts \
  --project=chromium --reporter=line
5 passed (12.3s)

PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
  npx playwright test e2e/24-trade-journeys.spec.ts e2e/28-trade-journeys-cross-links.spec.ts \
  --project=mobile-chromium --reporter=line
5 passed (12.8s)
```

No mock-only code was used for validating the hosted UI routing and cross-link navigation capabilities. Legacy data warning states are verified under strict live fallback configurations.
