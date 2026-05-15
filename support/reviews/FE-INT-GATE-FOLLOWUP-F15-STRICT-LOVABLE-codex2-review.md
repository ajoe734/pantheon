# Review: FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE

Reviewer: Codex2
Owner: Codex
Task: FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE - Enable strict fallback selection on hosted Lovable dev build
Reviewed: 2026-05-15
Disposition: Approved

## Findings

No blocking findings.

## Scope Checked

- Task artifacts live in `/home/lupin/code/execute-plans`; the pantheon-relative artifact paths in `ai-status.json` are not present in this repo checkout.
- `execute-plans` commit `7dff8fa` is scoped to `.lovable/audits/current-run/f15-strict-product-gap.md` and `e2e/09-strict-vs-hybrid.spec.ts`.
- The F15 spec installs the supported runtime fallback override before navigation and keeps the strict acceptance unchanged: strict 5xx must render a typed error and must not show the `Momentum Quant Alpha` seed row.
- Follow-up evidence in `support/evidence/OPS-GEM-REDEPLOY-001.md` records the hosted Lovable refresh to `/assets/index-vlevju41.js`, the dev BFF browser probe, and two prior hosted F15 strict passes.
- A fresh hosted asset check confirmed `https://pantheon-dev.lovable.app/management` still serves `/assets/index-vlevju41.js`.

## Verification

Commands run from `/home/lupin/code/execute-plans`:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io VITE_BFF_MODE=live VITE_BFF_FALLBACK=strict PANTHEON_E2E_STRICT=1 PLAYWRIGHT_HTML_OUTPUT_DIR=/tmp/f15-review-playwright-report-run1 npx playwright test e2e/09-strict-vs-hybrid.spec.ts --trace=on --reporter=list --output=/tmp/f15-review-test-results-run1
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io VITE_BFF_MODE=live VITE_BFF_FALLBACK=strict PANTHEON_E2E_STRICT=1 PLAYWRIGHT_HTML_OUTPUT_DIR=/tmp/f15-review-playwright-report-run2 npx playwright test e2e/09-strict-vs-hybrid.spec.ts --trace=on --reporter=list --output=/tmp/f15-review-test-results-run2
curl -fsSL https://pantheon-dev.lovable.app/management | rg -o '/assets/index-[^"<>]+\.js' | head -5
```

Results:

- Hosted strict run 1: `1 skipped, 2 passed`.
- Hosted strict run 2: `1 skipped, 2 passed`.
- Hosted asset check: `/assets/index-vlevju41.js`.

## Decision

Approved for owner finalization. Acceptance is met: hosted Lovable dev strict 5xx fails closed with no seed row, F15 strict passed twice with `PANTHEON_E2E_STRICT=1`, and the strict branch was not weakened to accept seed fallback.
