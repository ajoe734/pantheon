# MGMT-OPS-003 GAP-003 Flow Focus Fix Evidence

Task: `MGMT-OPS-003-GAP-003-FLOW-FOCUS-FIX`
Owner: Codex2
Recorded: `20260711T235934Z`

## Delivered Frontend

- Repository: `ajoe734/execute-plans`
- PR: `https://github.com/ajoe734/execute-plans/pull/263`
- Task commit: `a05e3b3257210e0b2371b299c82fd2118215d0d3`
- Merge commit on `dev`: `a74e58696c900112557b0c748c3f8c69629da106`
- Dev deployment: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json`
- Deployed commit reported by `/deployment.json`: `a74e58696c900112557b0c748c3f8c69629da106`
- Deployed at: `20260711T234143Z`

## Scope Verified

- Portfolio workflow navigation now uses canonical Performance Center routes:
  `/management/performance?tab=overview` and
  `/management/performance?tab=attribution`.
- Legacy Portfolio Book and Performance Attribution redirects preserve live
  snake_case workflow context including `persona_id`, `runtime_id`,
  `deployment_stage`, source status, stale telemetry, and risk state.
- Persona Fleet accepts the BFF-authored `persona_id` focus URL and issues the
  focused live request through the existing `q` query path.
- Human Inbox preserves Portfolio target context (`target_id`, `target_type`,
  `persona_id`, and `runtime_id`) and renders an explicit unresolved context
  state when the live list does not contain the exact target.
- New hosted desktop/mobile workflow E2E covers Portfolio Book -> Persona Fleet
  -> Attribution -> Human Inbox against the live BFF.

## BFF Contract Check

Command:

```bash
curl -fsS https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/openapi.json |
  jq -r '.paths["/bff/management/persona-fleet"].get.parameters[]? |
  select(.name=="q" or .name=="page_size" or .name=="pageSize") |
  [.name, .in, (.schema.type // ""), (.required // false)] | @tsv'
```

Result:

```text
q	query		false
page_size	query	integer	false
```

## Validation

Local focused unit suite:

```bash
npm run test -- src/management/navigation/managementRouteManifest.test.ts \
  src/management/pages/oversight/PersonaFleetPage.test.tsx \
  src/management/pages/oversight/HumanInboxPage.test.tsx \
  src/management/pages/oversight/PortfolioBook.test.tsx \
  src/management/pages/oversight/PerformanceAttribution.test.tsx
```

Result: 5 files passed, 50 tests passed.

Build:

```bash
npm run build
```

Result: passed. Existing warnings remained for Browserslist age, Rollup circular
chunking around `runActionSafe`, CSS minification syntax, and chunk size.

Pre-deploy browser workflow probe:

```bash
PANTHEON_HOSTED_E2E=1 \
PANTHEON_FE_BASE_URL=http://127.0.0.1:4177 \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
VITE_BFF_MODE=live \
VITE_BFF_FALLBACK=strict \
npx playwright test e2e/21-portfolio-workflow-hosted.spec.ts --project=chromium
```

Result: desktop and mobile passed.

GitHub PR gate:

- Workflow: `Pantheon FE-BFF Integration Gate`
- Run: `https://github.com/ajoe734/execute-plans/actions/runs/29172001643`
- Result: success, `17m38s`

GitHub dev gate after merge:

- Workflow: `Pantheon FE-BFF Integration Gate`
- Run: `https://github.com/ajoe734/execute-plans/actions/runs/29172478139`
- Result: success, `18m26s`

Dev FE deployment:

- Workflow: `Pantheon Dev FE Deploy`
- Run: `https://github.com/ajoe734/execute-plans/actions/runs/29172478132`
- Result: success

Hosted post-deploy workflow probe:

```bash
PANTHEON_HOSTED_E2E=1 \
PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
VITE_BFF_MODE=live \
VITE_BFF_FALLBACK=strict \
npx playwright test e2e/21-portfolio-workflow-hosted.spec.ts --project=chromium
```

Result: desktop and mobile passed, 2 tests passed in `16.2s`.

## Deployment JSON

```json
{
  "app": "execute-plans",
  "environment": "pantheon-dev-fe",
  "deployedAt": "20260711T234143Z",
  "commit": "a74e58696c900112557b0c748c3f8c69629da106",
  "sourceRef": "a74e58696c900112557b0c748c3f8c69629da106",
  "sourceBranch": "dev",
  "feHost": "https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io",
  "bffHost": "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io",
  "buildMode": {
    "VITE_BFF_MODE": "live",
    "VITE_BFF_FALLBACK": "strict",
    "VITE_BFF_REAL_WRITES": "false"
  }
}
```
