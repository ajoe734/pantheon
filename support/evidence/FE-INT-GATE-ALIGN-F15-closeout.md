# FE-INT-GATE-ALIGN-F15 closeout

Task: `FE-INT-GATE-ALIGN-F15`
Owner: `Codex2`
Reviewer: `Claude`
Date: 2026-05-15

## Resolution

The old blocker said hosted Lovable still rendered hybrid/seed rows under the
strict branch. That was superseded by the strict runtime hook work and the
Lovable dev refresh recorded in `OPS-GEM-REDEPLOY-001`.

## Verification

Latest hosted strict check:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_MODE=live \
VITE_BFF_FALLBACK=strict \
PANTHEON_E2E_STRICT=1 \
npx playwright test e2e/09-strict-vs-hybrid.spec.ts \
  --reporter=list \
  --output=/tmp/fe-int-f15-closeout-check
```

Result: `1 skipped`, `2 passed`.

Prior OPS-GEM redeploy evidence also recorded two hosted strict F15 reruns:

- run 1: `1 skipped`, `2 passed`
- run 2: `1 skipped`, `2 passed`

Evidence source:

- `support/evidence/OPS-GEM-REDEPLOY-001.md`

## Acceptance

- Hosted Lovable DOM/network was used.
- Strict 5xx injection fails closed without mock data.
- 4xx BFF error envelope does not fall back to mock.
- Hybrid-only test remains skipped in strict mode as intended.
- No acceptance condition was downgraded.
