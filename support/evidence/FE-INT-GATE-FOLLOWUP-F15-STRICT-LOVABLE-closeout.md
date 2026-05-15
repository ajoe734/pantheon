# FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE Closeout

Task: `FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE`
Owner: `Codex`
Reviewer: `Codex2`
Recorded: 2026-05-15T05:19:11Z

## Scope

Closeout finalizes the reviewed hosted Lovable dev strict fallback fix. The
implementation artifacts live in `/home/lupin/code/execute-plans`:

- `.lovable/audits/current-run/f15-strict-product-gap.md`
- `e2e/09-strict-vs-hybrid.spec.ts`

Those tracked files are clean in the execute-plans checkout. The scoped
implementation commit there is:

- `7dff8fa FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE wire strict runtime override`

Codex2 approved the task in:

- `support/reviews/FE-INT-GATE-FOLLOWUP-F15-STRICT-LOVABLE-codex2-review.md`

## Closeout Verification

Commands run from `/home/lupin/code/execute-plans`.

Hosted asset check:

```bash
curl -fsSL https://pantheon-dev.lovable.app/management | rg -o '/assets/index-[^"<>]+\.js' | head -5
```

Result:

```text
/assets/index-vlevju41.js
```

F15 strict run 1:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io VITE_BFF_MODE=live VITE_BFF_FALLBACK=strict PANTHEON_E2E_STRICT=1 PLAYWRIGHT_HTML_OUTPUT_DIR=/tmp/f15-closeout-playwright-report-run1 npx playwright test e2e/09-strict-vs-hybrid.spec.ts --trace=on --reporter=list --output=/tmp/f15-closeout-test-results-run1
```

Result:

```text
1 skipped, 2 passed
```

F15 strict run 2:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io VITE_BFF_MODE=live VITE_BFF_FALLBACK=strict PANTHEON_E2E_STRICT=1 PLAYWRIGHT_HTML_OUTPUT_DIR=/tmp/f15-closeout-playwright-report-run2 npx playwright test e2e/09-strict-vs-hybrid.spec.ts --trace=on --reporter=list --output=/tmp/f15-closeout-test-results-run2
```

Result:

```text
1 skipped, 2 passed
```

## Acceptance

- Hosted Lovable dev still serves `/assets/index-vlevju41.js`.
- Strict 5xx renders the strict typed error path and does not show seed rows.
- The F15 spec still skips the hybrid branch under strict mode and keeps the
  4xx non-fallback assertion.
