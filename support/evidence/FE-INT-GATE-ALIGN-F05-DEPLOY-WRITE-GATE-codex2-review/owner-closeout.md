# FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE Owner Closeout

Task: `FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE`
Owner: `Codex`
Reviewer: `Codex2`
Date: 2026-05-15

## Result

`pantheon-dev.lovable.app` is serving the refreshed hosted bundle
`/assets/index-vlevju41.js`. The bundle still contains the dev-scoped real-write
gate markers required by the F05 acceptance path:

- `VITE_BFF_REAL_WRITES`
- `VITE_BFF_FALLBACK`
- `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`
- `pantheon-dev-browser:reviewer`

Codex2 review evidence passed, and owner closeout reran the hosted F05 spec
twice against the refreshed Lovable dev app with strict fallback and real writes
enabled. Both closeout runs passed `2/2`.

## Commands

Hosted asset and bundle gate check:

```bash
asset=$(curl -Ls https://pantheon-dev.lovable.app/management | rg -o '/assets/index-[^"[:space:]]+\.js' | head -1)
printf 'asset=%s\n' "$asset"
curl -Ls "https://pantheon-dev.lovable.app${asset}" | rg -o 'VITE_BFF_REAL_WRITES|VITE_BFF_FALLBACK|pantheon-dev-browser:reviewer|https://pantheon-lupin-dev-bff\.34\.81\.75\.241\.sslip\.io' | sort | uniq -c
```

Result:

```text
asset=/assets/index-vlevju41.js
      5 VITE_BFF_FALLBACK
      2 VITE_BFF_REAL_WRITES
      7 https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io
      7 pantheon-dev-browser:reviewer
```

Hosted F05 closeout run 1:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_MODE=live \
VITE_BFF_FALLBACK=strict \
VITE_BFF_REAL_WRITES=true \
npx playwright test e2e/04-sentinel-remediation.spec.ts --trace=on --reporter=list --output=/tmp/fe-int-gate-align-f05-deploy-write-gate-closeout-run1
```

Result: `2 passed`.

Hosted F05 closeout run 2:

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_MODE=live \
VITE_BFF_FALLBACK=strict \
VITE_BFF_REAL_WRITES=true \
npx playwright test e2e/04-sentinel-remediation.spec.ts --trace=on --reporter=list --output=/tmp/fe-int-gate-align-f05-deploy-write-gate-closeout-run2
```

Result: `2 passed`.
